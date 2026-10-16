// Entire visibility-rule path, using owned snapshots and synthetic GS contacts.
#include "../MHWI-VisualControllerExtended.cpp"

static int failures = 0, checks = 0;
static bool originalCalled = false;
static bool __fastcall Original(std::uintptr_t, std::uintptr_t, std::uintptr_t, std::uintptr_t)
{
    originalCalled = true; return false;
}
static int Query(unsigned short id)
{
    unsigned char object[16]{};
    std::memcpy(object + 4, &id, sizeof(id));
    originalCalled = false;
    const bool result = hook::Detour(0, 0, reinterpret_cast<std::uintptr_t>(object), 0);
    return originalCalled ? -1 : result;
}
static void Check(const char* name, bool ok)
{
    ++checks; if (!ok) ++failures;
    std::printf("[%s] %s\n", ok ? "PASS" : "FAIL", name);
}

int main()
{
    plugin::RuleBuildContext context;
    plugin::WalkIni(
        "[GreatSwordBurst]\nEnabled=1\nLevel1=5\nLevel2=10\nLevel3=15\n"
        "TrueFirst=15\nTrueSecond=15\nRestoreMs=10000\n"
        "[Rule1]\nId=60\nHealthPercentAbove=80\nReturn=1\n"
        "[Rule2]\nId=60\nReturn=0\n"
        "[Rule3]\nId=61\nHealthPercentAbove=60\nReturn=1\n"
        "[Rule4]\nId=61\nReturn=0\n"
        "[Rule5]\nId=54\nHealthPercentMax=50\nLatchUntilReset=1\nReturn=0\n"
        "[Rule6]\nId=54\nReturn=1\n",
        plugin::OnIniValue, &context);
    plugin::PublishRules(context.rules);
    charge_trial::Configure(context.charge);
    Check("trial INI parsed and enabled", charge_trial::Enabled());
    Check("six existing rules retained", gRuleCounts[gActiveRuleBuffer] == 6);
    hook::gOriginalFunction = &Original;
    player::StateSnapshot state;
    state.entityAddress = 1; state.healthPercent = 100;
    player::PublishState(state);
    Check("full health initially shows both groups", Query(60) == 1 && Query(61) == 1);

    vce::ChargeProbeFrame f;
    f.actor = 1; f.weapon = 2; f.action = 3; f.map = 504; f.weaponType = 0;
    f.health = 100; f.ready = true; f.level = 3;
    auto feed = [&] { return charge_trial::Feed(f, ::GetTickCount64(), true); };
    f.fsm = 85; f.lmt = 49433; feed();
    f.fsm = 65; f.lmt = 49306; feed();
    ++f.counter; auto r = feed();
    Check("accepted impact reaches runtime", r.event.applied && r.visual == 85);
    f.fsm = 102; f.lmt = 49439; feed();
    f.fsm = 78; f.lmt = 49342; feed();
    ++f.counter; r = feed();
    Check("first true strike produces visual 70", r.event.applied && r.visual == 70);
    Check("group60 hides through actual detour", Query(60) == 0);
    Check("group61 still shows at 70", Query(61) == 1);
    const auto epoch = CurrentLatchEpoch();
    f.fsm = 137; f.lmt = 49429; feed();
    ++f.counter; r = feed();
    Check("second true strike produces visual 55", r.event.applied && r.visual == 55);
    Check("group61 hides through actual detour", Query(61) == 0);
    Check("real published health remains 100", player::GetStateSnapshot().healthPercent == 100);
    Check("ordinary HP rule ignores temporary reduction", Query(54) == 1);
    Check("temporary reduction cannot reset latch epoch", CurrentLatchEpoch() == epoch);
    Check("unconfigured group still passes through", Query(99) == -1);
    Check("unrelated IDs keep real health", charge_trial::VisualForId(54, 100, ::GetTickCount64()) == 100);

    state.healthPercent = 45; player::PublishState(state);
    Check("real HP damage still triggers old latch", Query(54) == 0);
    state.healthPercent = 100; player::PublishState(state);
    Check("old latch still persists after real healing", Query(54) == 0);
    charge_trial::Configure({});
    Check("disabled trial restores original HP behavior", !charge_trial::Enabled() && Query(60) == 1 && Query(61) == 1);
    Check("disabling trial does not reset real HP latch", Query(54) == 0);
    state.healthPercent = 0; player::PublishState(state); Query(54);
    state.healthPercent = 100; player::PublishState(state);
    Check("old real-health death reset still works", Query(54) == 1);
    charge_trial::Settings invalid;
    invalid.enabled = true; invalid.burst.reduction[0] = -1;
    charge_trial::Configure(invalid);
    Check("invalid trial config fails closed", !charge_trial::Enabled());
    plugin::RuleBuildContext gradualContext;
    plugin::WalkIni("[GreatSwordBurst]\nEnabled=1\nRestoreMs=5000\nRecoveryPerSecond=5\n"
                    "TrueFirst=7.5\nTrueSecond=7.5\n", plugin::OnIniValue, &gradualContext);
    Check("gradual INI accepts delay rate and fractional true strikes",
        gradualContext.charge.burst.durationMs == 5000 &&
        gradualContext.charge.burst.recoveryPerSecond == 5 &&
        gradualContext.charge.burst.trueChargeReduction[0] == 7.5f &&
        gradualContext.charge.burst.trueChargeReduction[1] == 7.5f);
    charge_trial::Configure(gradualContext.charge);
    const auto then = ::GetTickCount64() - 6500;
    f.counter = 0; f.fsm = 85; f.lmt = 49433;
    charge_trial::Feed(f, then, true);
    f.fsm = 65; f.lmt = 49306; ++f.counter;
    charge_trial::Feed(f, then + 1, true);
    f.fsm = 102; f.lmt = 49439;
    charge_trial::Feed(f, then + 2, true);
    f.fsm = 78; f.lmt = 49342; ++f.counter;
    r = charge_trial::Feed(f, then + 3, true);
    Check("runtime true first now reduces by 7.5", r.visual == 77.5f);
    f.fsm = 137; f.lmt = 49429; ++f.counter;
    r = charge_trial::Feed(f, then + 4, true);
    Check("runtime true second now reduces by 7.5", r.visual == 70);
    Check("runtime no jump after five second delay",
        charge_trial::VisualForId(60, 100, then + 5004) == 70);
    Check("runtime smooth five-point recovery",
        charge_trial::VisualForId(60, 100, then + 6004) == 75);
    Check("actual detour sees partial recovery below threshold", Query(60) == 0);
    Check("runtime recovery uses current damaged health",
        charge_trial::VisualForId(60, 80, then + 6004) == 55);
    gradualContext.charge.burst.recoveryPerSecond = -1;
    charge_trial::Configure(gradualContext.charge);
    Check("invalid recovery rate disables runtime trial", !charge_trial::Enabled());
    player::PublishState({});
    Check("unavailable state retains passthrough", Query(60) == -1);
    std::printf("%d integration checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
