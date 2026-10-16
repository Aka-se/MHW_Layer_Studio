#pragma once

#include <cmath>
#include <cstdint>
#include <limits>

namespace vce {

// Pure state only. A future verified impact adapter owns event validation,
// attack IDs, actor/map lifecycle and synchronization. No game reads or writes.
struct ChargeBurstConfig {
    float reduction[3] = {5.0f, 10.0f, 15.0f};
    float trueChargeReduction[2] = {15.0f, 15.0f};
    std::uint64_t durationMs = 10000; // Inactivity delay; legacy configs restore instantly.
    float recoveryPerSecond = 0.0f; // 0 keeps legacy instant recovery; >0 fades reduction.
};

class ChargeBurst {
public:
    explicit ChargeBurst(ChargeBurstConfig config = {}) : config_(config) {}

    // Normal/strong charged slash: one reduction per verified attack.
    // IDs increase once per attack, not per target, query or frame.
    bool OnVerifiedImpact(std::uint64_t attackId, int chargeLevel,
                          std::uint64_t nowMs)
    {
        if (chargeLevel < 1 || chargeLevel > 3) return false;
        return AcceptImpact(attackId, ImpactKind::ChargedSlash, 1,
                            config_.reduction[chargeLevel - 1], nowMs);
    }

    // True charged slash: both strikes share an attack ID. Each verified strike
    // uses its configured reduction, independent of charge level. Contacts retain
    // the same segment, including contacts with different targets.
    bool OnVerifiedTrueChargeImpact(std::uint64_t attackId, int segment,
                                    std::uint64_t nowMs)
    {
        if (segment < 1 || segment > 2) return false;
        return AcceptImpact(attackId, ImpactKind::TrueChargedSlash,
                            1U << (segment - 1),
                            config_.trueChargeReduction[segment - 1], nowMs);
    }

    // Always use CURRENT health. Never restore a saved pre-impact HP value.
    // Unknown/invalid health fails back to the caller's ordinary state handling.
    float VisualPercent(float healthPercent, std::uint64_t nowMs) const
    {
        if (!std::isfinite(healthPercent) || healthPercent < 0.0f)
            return -1.0f;
        const float hp = healthPercent > 100.0f ? 100.0f : healthPercent;
        const float value = hp - RemainingReduction(nowMs);
        return value < 0.0f ? 0.0f : value;
    }

    // Adapter must reset on death, character/map/weapon change, unavailable
    // actor state, config reload or feature disable. No cross-scene residue.
    void Reset()
    {
        lastAttackId_ = 0;
        lastKind_ = ImpactKind::ChargedSlash;
        seenSegments_ = 0;
        expiresAtMs_ = 0;
        reduction_ = 0.0f;
    }

private:
    enum class ImpactKind { ChargedSlash, TrueChargedSlash };

    bool AcceptImpact(std::uint64_t attackId, ImpactKind kind, unsigned segmentBit,
                      float incoming, std::uint64_t nowMs)
    {
        // A disabled level must not count as a contact or postpone recovery.
        if (!ValidConfig() || incoming <= 0 || attackId == 0 || attackId < lastAttackId_)
            return false;
        if (attackId == lastAttackId_ &&
            (kind != lastKind_ || (seenSegments_ & segmentBit)))
            return false;
        if (attackId != lastAttackId_) {
            lastAttackId_ = attackId;
            lastKind_ = kind;
            seenSegments_ = 0;
        }
        seenSegments_ |= segmentBit;
        // Settle elapsed recovery BEFORE stacking; never resurrect recovered points.
        const float accumulated = RemainingReduction(nowMs) + incoming;
        reduction_ = accumulated > 100.0f ? 100.0f : accumulated;
        const auto maxTime = (std::numeric_limits<std::uint64_t>::max)();
        expiresAtMs_ = nowMs > maxTime - config_.durationMs
            ? maxTime : nowMs + config_.durationMs;
        return true;
    }

    bool ValidConfig() const
    {
        if (config_.durationMs == 0 || config_.durationMs > 60000) return false;
        if (!std::isfinite(config_.recoveryPerSecond) ||
            config_.recoveryPerSecond < 0 || config_.recoveryPerSecond > 100) return false;
        for (const float value : config_.reduction)
            if (!std::isfinite(value) || value < 0.0f || value > 100.0f)
                return false;
        for (const float value : config_.trueChargeReduction)
            if (!std::isfinite(value) || value < 0.0f || value > 100.0f)
                return false;
        return true;
    }

    float RemainingReduction(std::uint64_t nowMs) const
    {
        if (nowMs < expiresAtMs_) return reduction_;
        if (config_.recoveryPerSecond <= 0) return 0.0f;
        const double recovered = static_cast<double>(nowMs - expiresAtMs_) *
            static_cast<double>(config_.recoveryPerSecond) / 1000.0;
        return recovered >= reduction_ ? 0.0f : static_cast<float>(reduction_ - recovered);
    }

    ChargeBurstConfig config_;
    std::uint64_t lastAttackId_ = 0;
    ImpactKind lastKind_ = ImpactKind::ChargedSlash;
    unsigned seenSegments_ = 0;
    std::uint64_t expiresAtMs_ = 0;
    float reduction_ = 0.0f;
};

} // namespace vce
