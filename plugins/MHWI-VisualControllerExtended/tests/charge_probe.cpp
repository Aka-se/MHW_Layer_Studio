#include "../charge_probe.hpp"
#include <cstdio>
#include <cstdlib>

static int checks = 0, failures = 0;
static void Check(const char* name, bool ok)
{
    ++checks;
    if (!ok) ++failures;
    std::printf("[%s] %s\n", ok ? "PASS" : "FAIL", name);
}

struct Fixture {
    vce::ChargeProbe probe;
    vce::ChargeProbeFrame frame;
    std::uint64_t time = 0;
    Fixture(vce::ChargeBurstConfig config = {}) : probe(config) {
        frame.actor = 1; frame.weapon = 2; frame.action = 3; frame.map = 504;
        frame.weaponType = 0; frame.health = 100; frame.level = 0;
        frame.fsm = 0; frame.lmt = 49153; frame.ready = true;
        probe.Update(frame, time, true);
    }
    vce::ChargeProbeEvent Tick() { return probe.Update(frame, ++time, true); }
    void Wait(unsigned ms) {
        const auto end = time + ms;
        while (time < end) {
            time += end - time < 100 ? end - time : 100;
            probe.Update(frame, time, true);
        }
    }
    void State(int fsm, int lmt, int level) {
        frame.fsm = fsm; frame.lmt = lmt; frame.level = level; Tick();
    }
    vce::ChargeProbeEvent Contact(unsigned delta = 1) {
        frame.counter = (frame.counter & 0xffff0000U) |
            ((frame.counter + delta) & 0xffffU);
        return Tick();
    }
    void Normal() { State(85, 49433, 3); State(65, 49306, 3); Wait(600); }
    void TrueFirst() { State(102, 49439, 3); State(78, 49342, 3); Wait(600); }
    float Percent() { return probe.VisualPercent(frame.health, time); }
};

static void SelfTest()
{
    Fixture f;
    f.Normal();
    auto event = f.Contact();
    Check("normal action and animation qualify contact", event.applied && event.kind == 1 && event.level == 3);
    Check("level 3 reduces by 15", f.Percent() == 85);
    Check("same swing multi-contact deduplicated", !f.Contact().applied && f.Percent() == 85);
    f.TrueFirst(); event = f.Contact();
    Check("true first strike accepted", event.applied && event.segment == 1);
    const auto attack = event.attack;
    f.State(137, 49429, 3); f.Wait(780); event = f.Contact();
    Check("power true second shares attack ID", event.applied && event.segment == 2 && event.attack == attack);
    Check("normal plus true two strikes totals 45", f.Percent() == 55);
    Check("second strike duplicates rejected", !f.Contact().applied);
    f.frame.health = 70;
    Check("real health changes remain effective", f.Percent() == 25);
    f.Wait(10000);
    Check("expires while stationary", f.Percent() == 70);

    Fixture missed;
    missed.TrueFirst();
    missed.State(136, 49428, 2); missed.Wait(780);
    Check("second alone works without first contact", missed.Contact().applied && missed.Percent() == 85);

    Fixture cancelled;
    cancelled.State(85, 49277, 1);
    cancelled.State(96, 123, 1);
    cancelled.State(65, 49304, 1);
    Check("cancelled charge cannot create contact", !cancelled.Contact().applied && cancelled.Percent() == 100);
    Fixture uncharged;
    uncharged.State(85, 49277, 0); uncharged.State(65, 49303, 0);
    Check("uncharged draw excluded", !uncharged.Contact().applied);
    Fixture noWindup;
    noWindup.State(65, 49306, 3);
    Check("joining mid swing cannot invent impact", !noWindup.Contact().applied);
    Fixture wrongAnimation;
    wrongAnimation.State(85, 49433, 3); wrongAnimation.State(65, 1, 3);
    Check("unexpected animation rejected", !wrongAnimation.Contact().applied);

    Fixture wrap;
    wrap.frame.counter = 0xffffffffU; wrap.Tick();
    wrap.Normal();
    Check("16-bit counter wrap handles sentinel", wrap.Contact().applied && wrap.Percent() == 85);
    Fixture jump;
    jump.Normal();
    Check("batched counter jump fails closed", !jump.Contact(2).applied);
    Check("jump does not leave swing armed", !jump.Contact().applied);

    for (int reset = 0; reset < 7; ++reset) {
        Fixture r; r.Normal(); r.Contact();
        if (reset == 0) r.frame.actor = 7;
        if (reset == 1) r.frame.weapon = 7;
        if (reset == 2) r.frame.action = 7;
        if (reset == 3) r.frame.map = 505;
        if (reset == 4) r.frame.weaponType = 3;
        if (reset == 5) r.frame.ready = false;
        if (reset == 6) r.frame.counter ^= 0x10000;
        r.Tick();
        Check("context/read/counter reset clears burst", r.Percent() == 100);
    }
    Fixture gap; gap.Normal(); gap.Contact();
    gap.time += 400; gap.Tick();
    Check("stale sample gap clears burst", gap.Percent() == 100);
    Fixture off; off.Normal(); off.Contact();
    off.probe.Update(off.frame, ++off.time, false);
    Check("disable clears burst immediately", off.Percent() == 100);
    Fixture dead; dead.Normal(); dead.Contact(); dead.frame.health = 0; dead.Tick();
    dead.frame.health = 100; dead.Tick();
    Check("death and respawn do not retain reduction", dead.Percent() == 100);
    Fixture strong;
    strong.State(86, 49402, 1); strong.State(74, 49307, 1); strong.Wait(780);
    event = strong.Contact();
    Check("strong level 1 reduces by 5", event.applied && event.kind == 2 && strong.Percent() == 95);
    Fixture duringCharge;
    duringCharge.State(85, 49433, 3);
    Check("counter during windup is not a hit", !duringCharge.Contact().applied);
    vce::ChargeBurstConfig combat;
    combat.reduction[0] = combat.reduction[1] = 0;
    combat.reduction[2] = 5;
    combat.trueChargeReduction[0] = combat.trueChargeReduction[1] = 5;
    Fixture selective(combat);
    selective.State(85, 49277, 1); selective.State(65, 49304, 1);
    Check("adapter level one emits no event", !selective.Contact().applied);
    selective.State(86, 49402, 2); selective.State(74, 49308, 2);
    Check("adapter strong level two emits no event", !selective.Contact().applied);
    selective.Normal();
    Check("adapter full charge reduces by five", selective.Contact().applied && selective.Percent() == 95);
    selective.TrueFirst(); selective.Contact();
    selective.State(137, 49429, 3);
    Check("adapter true two strikes each five", selective.Contact().applied && selective.Percent() == 85);
}

static int Replay(const char* filename)
{
    FILE* file = std::fopen(filename, "rb");
    if (!file) return 1;
    vce::ChargeProbe probe;
    unsigned long long now = 0, actor = 0, weapon = 0, action = 0;
    int map, type, fsm, lmt, level, ready;
    unsigned counter;
    float health;
    int events = 0, first = 0, second = 0, normal = 0, strong = 0, samples = 0;
    while (std::fscanf(file, "%llu %llu %llu %llu %d %d %d %d %d %u %d %f",
        &now, &actor, &weapon, &action, &map, &type, &fsm, &lmt, &level,
        &counter, &ready, &health) == 12) {
        vce::ChargeProbeFrame frame{static_cast<std::uintptr_t>(actor), static_cast<std::uintptr_t>(weapon),
            static_cast<std::uintptr_t>(action), map, type, fsm, lmt, level, health, counter, ready != 0};
        const auto event = probe.Update(frame, now, true);
        ++samples;
        if (!event.applied) continue;
        ++events;
        normal += event.kind == 1; strong += event.kind == 2;
        first += event.kind == 3 && event.segment == 1;
        second += event.kind == 3 && event.segment == 2;
        std::printf("replay t=%llu kind=%d segment=%d level=%d visual=%.1f\n", now,
            event.kind, event.segment, event.level, probe.VisualPercent(health, now));
    }
    std::fclose(file);
    std::printf("replay: samples=%d events=%d normal=%d strong=%d true=%d+%d\n",
        samples, events, normal, strong, first, second);
    Check("four-round replay has 15 charged contacts (one uncharged excluded)",
        samples == 2860 && events == 15 && normal == 3 && strong == 4 && first == 4 && second == 4);
    return failures ? 1 : 0;
}

int main(int argc, char** argv)
{
    SelfTest();
    if (argc > 1) Replay(argv[1]);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
