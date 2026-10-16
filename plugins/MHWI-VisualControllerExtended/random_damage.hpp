#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <numeric>
#include <vector>

namespace vce {
// Per-outfit budget; queries never generate randomness. Healing restores only
// the necessary subset; later damage draws again from currently visible units.
class RandomDamage {
public:
    explicit RandomDamage(unsigned count = 0, std::uint64_t seed = 1)
        : hidden_(count, false), random_(seed ? seed : 1) {}

    void Reset() { std::fill(hidden_.begin(), hidden_.end(), false); count_ = 0; }

    void Update(float hp)
    {
        if (!std::isfinite(hp) || hp < 0) { Reset(); return; }
        hp = std::clamp(hp, 0.0f, 100.0f);
        const unsigned stage = static_cast<unsigned>(std::floor((100.0 - hp) / 5.0 + 1e-9));
        const unsigned target = static_cast<unsigned>(hidden_.size()) * stage / 20;
        if (target == count_) return;
        const bool hide = target > count_;
        std::vector<unsigned> candidates;
        for (unsigned i = 0; i < hidden_.size(); ++i)
            if (hidden_[i] != hide) candidates.push_back(i);
        while (count_ != target && !candidates.empty()) {
            const unsigned selected = Bounded(static_cast<unsigned>(candidates.size()));
            hidden_[candidates[selected]] = hide;
            candidates[selected] = candidates.back();
            candidates.pop_back();
            if (hide) ++count_; else --count_;
        }
    }

    bool Hidden(unsigned unit) const { return unit < hidden_.size() && hidden_[unit]; }
    unsigned Count() const { return count_; }

private:
    unsigned Bounded(unsigned limit)
    {
        // Rejection prevents modulo bias; state survives complete healing.
        const std::uint64_t threshold = (std::uint64_t(0) - limit) % limit;
        std::uint64_t value;
        do {
            random_ ^= random_ >> 12; random_ ^= random_ << 25; random_ ^= random_ >> 27;
            value = random_ * 2685821657736338717ULL;
        } while (value < threshold);
        return static_cast<unsigned>(value % limit);
    }
    std::vector<bool> hidden_;
    std::uint64_t random_;
    unsigned count_ = 0;
};
} // namespace vce
