#pragma once
#include "charge_burst.hpp"

namespace vce {

// Experimental adapter for the observed 15.23.00 GS ground / contact signal.
// The counter is NOT advertised as a damage-hit detector. Only validated
// action/animation combinations are admitted, after observing their windup.
struct ChargeProbeFrame {
    std::uintptr_t actor = 0, weapon = 0, action = 0;
    int map = -1, weaponType = -1, fsm = -1, lmt = -1, level = -1;
    float health = -1;
    std::uint32_t counter = 0;
    bool ready = false;
};

struct ChargeProbeEvent {
    bool applied = false;
    int kind = 0; // 1 normal, 2 strong, 3 true charged slash
    int level = 0, segment = 0;
    std::uint64_t attack = 0;
};

class ChargeProbe {
public:
    explicit ChargeProbe(ChargeBurstConfig config = {}) : burst_(config) {}

    void Reset()
    {
        burst_.Reset();
        previous_ = {};
        baseline_ = false;
        previousTime_ = sequence_ = phaseStarted_ = 0;
        kind_ = phase_ = releaseLevel_ = 0;
    }

    float VisualPercent(float hp, std::uint64_t now) const
    {
        return burst_.VisualPercent(hp, now);
    }

    ChargeProbeEvent Update(const ChargeProbeFrame& frame, std::uint64_t now,
                            bool enabled)
    {
        if (!enabled || !frame.ready || !frame.actor || !frame.weapon || !frame.action ||
            frame.map < 0 || frame.weaponType != 0 || !std::isfinite(frame.health) ||
            frame.health <= 0 || frame.level < 0 || frame.level > 3) {
            Reset();
            return {};
        }
        if (baseline_ && (frame.actor != previous_.actor || frame.weapon != previous_.weapon ||
            frame.action != previous_.action || frame.map != previous_.map ||
            now < previousTime_ || now - previousTime_ > 250))
            Reset();

        const bool hadBaseline = baseline_;
        const unsigned delta = hadBaseline
            ? ((frame.counter & 0xffffU) - (previous_.counter & 0xffffU)) & 0xffffU : 0;
        const bool discontinuity = hadBaseline &&
            ((frame.counter >> 16) != (previous_.counter >> 16) || delta > 1);
        if (discontinuity) Reset();

        const int windup = Windup(frame.fsm);
        if (windup) {
            if (!baseline_ || Windup(previous_.fsm) != windup) {
                ++sequence_;
                kind_ = windup;
                phase_ = 0;
                releaseLevel_ = 0;
            }
        } else if (kind_ == 1 && frame.fsm == 65 && frame.level >= 1 &&
                   frame.lmt == 49303 + frame.level) {
            BeginPhase(1, frame.level, now);
        } else if (kind_ == 2 && frame.fsm == 74 && frame.level >= 1 &&
                   frame.lmt == 49306 + frame.level) {
            BeginPhase(1, frame.level, now);
        } else if (kind_ == 3 && frame.fsm == 78 &&
                   (frame.lmt == 49341 || frame.lmt == 49342) && phase_ <= 1) {
            BeginPhase(1, frame.level, now);
        } else if (kind_ == 3 && phase_ >= 1 &&
                   ((frame.fsm == 136 && frame.lmt == 49428) ||
                    (frame.fsm == 137 && frame.lmt == 49429))) {
            BeginPhase(2, frame.level, now);
        } else {
            kind_ = phase_ = releaseLevel_ = 0; // cancelled / unrelated action
        }

        ChargeProbeEvent event;
        if (hadBaseline && !discontinuity && delta == 1 && kind_ && phase_ &&
            now >= phaseStarted_ && now - phaseStarted_ <= 2200) {
            const bool accepted = kind_ == 3
                ? burst_.OnVerifiedTrueChargeImpact(sequence_, phase_, now)
                : burst_.OnVerifiedImpact(sequence_, releaseLevel_, now);
            if (accepted) event = {true, kind_, releaseLevel_, phase_, sequence_};
        }
        previous_ = frame;
        previousTime_ = now;
        baseline_ = true;
        return event;
    }

private:
    static int Windup(int fsm)
    {
        return (fsm == 84 || fsm == 85) ? 1 : fsm == 86 ? 2 : fsm == 102 ? 3 : 0;
    }

    void BeginPhase(int phase, int level, std::uint64_t now)
    {
        if (phase_ != phase) {
            phase_ = phase;
            releaseLevel_ = level;
            phaseStarted_ = now;
        }
    }

    ChargeBurst burst_;
    ChargeProbeFrame previous_;
    bool baseline_ = false;
    std::uint64_t previousTime_ = 0, sequence_ = 0, phaseStarted_ = 0;
    int kind_ = 0, phase_ = 0, releaseLevel_ = 0;
};

} // namespace vce
