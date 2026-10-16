#include "../charge_burst.hpp"

#include <cstdio>
#include <limits>

static int failures = 0;
static int checks = 0;

static void Check(const char* name, bool pass)
{
    ++checks;
    if (!pass) ++failures;
    std::printf("[%s] %s\n", pass ? "PASS" : "FAIL", name);
}

int main()
{
    // Short explicit window keeps boundary cases separate from the default.
    vce::ChargeBurstConfig shortWindow;
    shortWindow.durationMs = 3000;
    for (int level = 1; level <= 3; ++level) {
        vce::ChargeBurst burst(shortWindow);
        Check("ordinary HP before impact", burst.VisualPercent(90.0f, 100) == 90.0f);
        Check("accept verified level", burst.OnVerifiedImpact(1, level, 1000));
        Check("5/10/15 percentage points", burst.VisualPercent(90.0f, 1000) == 90.0f - level * 5.0f);
        Check("active until deadline", burst.VisualPercent(90.0f, 3999) == 90.0f - level * 5.0f);
        Check("restore at exact deadline", burst.VisualPercent(70.0f, 4000) == 70.0f);
    }

    vce::ChargeBurst burst(shortWindow);
    burst.OnVerifiedImpact(10, 3, 1000);
    Check("follow current HP loss", burst.VisualPercent(50.0f, 1200) == 35.0f);
    Check("follow current healing", burst.VisualPercent(100.0f, 1400) == 85.0f);
    Check("clamp at zero", burst.VisualPercent(15.0f, 1500) == 0.0f);
    Check("duplicate contact rejected", !burst.OnVerifiedImpact(10, 3, 3500));
    Check("old attack rejected", !burst.OnVerifiedImpact(9, 3, 3500));
    Check("duplicates do not extend timer", burst.VisualPercent(80.0f, 4000) == 80.0f);
    Check("weak follow-up after expiry", burst.OnVerifiedImpact(11, 1, 4100));
    Check("expired strength not retained", burst.VisualPercent(100.0f, 4100) == 95.0f);
    burst.OnVerifiedImpact(12, 3, 4200);
    Check("strong follow-up adds reduction", burst.VisualPercent(100.0f, 4200) == 80.0f);
    burst.OnVerifiedImpact(13, 1, 5000);
    Check("weak follow-up adds reduction", burst.VisualPercent(100.0f, 7300) == 75.0f);
    Check("follow-up refreshes deadline", burst.VisualPercent(100.0f, 8000) == 100.0f);

    burst.OnVerifiedImpact(14, 2, 9000);
    Check("zero HP stays zero", burst.VisualPercent(0.0f, 9000) == 0.0f);
    Check("unknown HP stays unknown", burst.VisualPercent(-1.0f, 9000) == -1.0f);
    Check("NaN HP is unknown", burst.VisualPercent(std::numeric_limits<float>::quiet_NaN(), 9000) == -1.0f);
    Check("infinite HP is unknown", burst.VisualPercent(std::numeric_limits<float>::infinity(), 9000) == -1.0f);
    burst.Reset();
    Check("lifecycle reset clears effect", burst.VisualPercent(65.0f, 9001) == 65.0f);
    Check("new lifecycle permits new attack IDs", burst.OnVerifiedImpact(1, 1, 9100));
    Check("zero ID rejected", !burst.OnVerifiedImpact(0, 2, 9200));
    Check("invalid level rejected", !burst.OnVerifiedImpact(2, 4, 9200));
    Check("invalid event does not consume ID", burst.OnVerifiedImpact(2, 2, 9300));
    Check("reads do not extend effect", burst.VisualPercent(100.0f, 12300) == 100.0f);

    vce::ChargeBurstConfig custom;
    custom.durationMs = 5000;
    vce::ChargeBurst longBurst(custom);
    longBurst.OnVerifiedImpact(1, 3, 100);
    Check("duration configurable", longBurst.VisualPercent(100.0f, 5099) == 85.0f);
    Check("custom duration expires", longBurst.VisualPercent(80.0f, 5100) == 80.0f);
    custom.durationMs = 0;
    vce::ChargeBurst disabled(custom);
    Check("zero duration rejects trigger", !disabled.OnVerifiedImpact(1, 1, 100));
    custom.durationMs = 3000;
    custom.reduction[1] = std::numeric_limits<float>::quiet_NaN();
    vce::ChargeBurst invalid(custom);
    Check("invalid config rejects trigger", !invalid.OnVerifiedImpact(1, 1, 100));

    vce::ChargeBurst combo(shortWindow);
    combo.OnVerifiedImpact(1, 1, 1000);
    combo.OnVerifiedImpact(2, 2, 2000);
    combo.OnVerifiedImpact(3, 3, 3000);
    Check("1+2+3 charge combo reduces by 30", combo.VisualPercent(100.0f, 3000) == 70.0f);
    Check("combo follows real HP loss", combo.VisualPercent(80.0f, 3500) == 50.0f);
    Check("combo follows real healing", combo.VisualPercent(90.0f, 3600) == 60.0f);
    combo.OnVerifiedImpact(4, 3, 4000);
    combo.OnVerifiedImpact(5, 3, 5000);
    Check("five attacks total 60 points", combo.VisualPercent(100.0f, 5000) == 40.0f);
    for (std::uint64_t id = 6; id < 100; ++id)
        combo.OnVerifiedImpact(id, 3, 5000 + id);
    Check("long combo remains bounded", combo.VisualPercent(100.0f, 6000) == 0.0f);
    Check("combo duplicate rejected", !combo.OnVerifiedImpact(99, 3, 8000));
    Check("duplicates do not keep combo alive", combo.VisualPercent(75.0f, 8099) == 75.0f);
    combo.OnVerifiedImpact(100, 1, 8099);
    Check("hit at deadline starts fresh", combo.VisualPercent(100.0f, 8099) == 95.0f);
    combo.OnVerifiedImpact(101, 2, 9000);
    combo.Reset();
    Check("reset clears full accumulated reduction", combo.VisualPercent(90.0f, 9000) == 90.0f);

    vce::ChargeBurst defaultWindow;
    defaultWindow.OnVerifiedImpact(1, 1, 1000);
    Check("default survives a seven-second attack gap", defaultWindow.VisualPercent(100.0f, 8000) == 95.0f);
    defaultWindow.OnVerifiedImpact(2, 2, 8000);
    defaultWindow.OnVerifiedImpact(3, 3, 15000);
    Check("slow charge combo stacks all three levels", defaultWindow.VisualPercent(100.0f, 15000) == 70.0f);
    Check("ten seconds measured from latest impact", defaultWindow.VisualPercent(100.0f, 24999) == 70.0f);
    Check("default recovers using current HP", defaultWindow.VisualPercent(75.0f, 25000) == 75.0f);

    vce::ChargeBurst trueCharge;
    Check("TCS first strike accepted", trueCharge.OnVerifiedTrueChargeImpact(1, 1, 1000));
    Check("TCS first strike reduces by 15", trueCharge.VisualPercent(100.0f, 1000) == 85.0f);
    Check("TCS first strike multi-target rejected", !trueCharge.OnVerifiedTrueChargeImpact(1, 1, 1500));
    Check("TCS cannot also consume normal charge reduction", !trueCharge.OnVerifiedImpact(1, 3, 1600));
    Check("TCS second strike accepted under SAME attack ID", trueCharge.OnVerifiedTrueChargeImpact(1, 2, 2000));
    Check("TCS two strikes reduce by exactly 30", trueCharge.VisualPercent(100.0f, 2000) == 70.0f);
    Check("TCS second strike duplicate rejected", !trueCharge.OnVerifiedTrueChargeImpact(1, 2, 11000));
    Check("TCS first strike duplicate still rejected", !trueCharge.OnVerifiedTrueChargeImpact(1, 1, 11500));
    Check("TCS second strike refreshes ten second recovery", trueCharge.VisualPercent(100.0f, 11999) == 70.0f);
    Check("TCS duplicates cannot postpone recovery", trueCharge.VisualPercent(80.0f, 12000) == 80.0f);
    Check("TCS zero attack rejected", !trueCharge.OnVerifiedTrueChargeImpact(0, 2, 12001));
    Check("TCS invalid segment rejected", !trueCharge.OnVerifiedTrueChargeImpact(2, 3, 13000));
    Check("TCS invalid segment does not consume attack ID", trueCharge.OnVerifiedTrueChargeImpact(2, 2, 13000));
    Check("TCS second strike alone reduces by 15", trueCharge.VisualPercent(100.0f, 13000) == 85.0f);
    Check("TCS subsequent attack first strike stacks", trueCharge.OnVerifiedTrueChargeImpact(3, 1, 14000));
    Check("TCS separate attacks accumulate", trueCharge.VisualPercent(100.0f, 14000) == 70.0f);
    Check("TCS late previous strike rejected", !trueCharge.OnVerifiedTrueChargeImpact(2, 1, 15000));
    trueCharge.Reset();
    trueCharge.OnVerifiedImpact(1, 3, 16000);
    Check("normal charge cannot be recounted as TCS", !trueCharge.OnVerifiedTrueChargeImpact(1, 2, 16100));
    trueCharge.OnVerifiedTrueChargeImpact(2, 1, 17000);
    trueCharge.OnVerifiedTrueChargeImpact(2, 2, 18000);
    Check("normal and TCS reductions combine", trueCharge.VisualPercent(100.0f, 18000) == 55.0f);
    Check("combined reduction follows current HP", trueCharge.VisualPercent(70.0f, 19000) == 25.0f);
    trueCharge.Reset();
    Check("reset clears TCS segment dedupe", trueCharge.OnVerifiedTrueChargeImpact(1, 2, 20000));

    vce::ChargeBurst firstOnly;
    firstOnly.OnVerifiedTrueChargeImpact(1, 1, 1000);
    Check("missed second strike adds no reduction", firstOnly.VisualPercent(100.0f, 5000) == 85.0f);
    Check("first strike alone expires from its own time", firstOnly.VisualPercent(100.0f, 11000) == 100.0f);

    vce::ChargeBurstConfig invalidTcsConfig;
    invalidTcsConfig.trueChargeReduction[0] = std::numeric_limits<float>::quiet_NaN();
    vce::ChargeBurst invalidTcs(invalidTcsConfig);
    Check("invalid TCS config rejects event", !invalidTcs.OnVerifiedTrueChargeImpact(1, 1, 1000));

    vce::ChargeBurst fullChargeCombo;
    fullChargeCombo.OnVerifiedImpact(1, 3, 1000);
    Check("full normal charge reduces by 15", fullChargeCombo.VisualPercent(100.0f, 1000) == 85.0f);
    fullChargeCombo.OnVerifiedImpact(2, 3, 5000);
    Check("full strong charge brings total to 30", fullChargeCombo.VisualPercent(100.0f, 5000) == 70.0f);
    fullChargeCombo.OnVerifiedTrueChargeImpact(3, 1, 9000);
    Check("TCS first strike brings full combo to 45", fullChargeCombo.VisualPercent(100.0f, 9000) == 55.0f);
    fullChargeCombo.OnVerifiedTrueChargeImpact(3, 2, 10400);
    Check("four full combo contacts total 60", fullChargeCombo.VisualPercent(100.0f, 10400) == 40.0f);
    Check("full combo still active before ten seconds", fullChargeCombo.VisualPercent(100.0f, 20399) == 40.0f);
    Check("full combo restores CURRENT health at expiry", fullChargeCombo.VisualPercent(80.0f, 20400) == 80.0f);

    vce::ChargeBurstConfig fade;
    fade.durationMs = 5000;
    fade.recoveryPerSecond = 5;
    fade.trueChargeReduction[0] = fade.trueChargeReduction[1] = 7.5f;
    vce::ChargeBurst gradual(fade);
    gradual.OnVerifiedImpact(1, 3, 1000);
    gradual.OnVerifiedImpact(2, 3, 2000);
    gradual.OnVerifiedTrueChargeImpact(3, 1, 3000);
    gradual.OnVerifiedTrueChargeImpact(3, 2, 4000);
    Check("new full combo totals 45 points", gradual.VisualPercent(100, 4000) == 55);
    Check("holds for five seconds", gradual.VisualPercent(100, 8999) == 55);
    Check("no jump at recovery start", gradual.VisualPercent(100, 9000) == 55);
    Check("continuous recovery at half second", gradual.VisualPercent(100, 9500) == 57.5f);
    Check("recovers five points per second", gradual.VisualPercent(100, 10000) == 60);
    Check("recovery subtracts from CURRENT health", gradual.VisualPercent(80, 10000) == 40);
    Check("recovery still follows healing", gradual.VisualPercent(90, 10000) == 50);
    Check("duplicate during recovery cannot restart delay", !gradual.OnVerifiedTrueChargeImpact(3, 2, 11000));
    Check("duplicate does not stall recovery", gradual.VisualPercent(100, 11000) == 65);
    Check("new hit during recovery accepted", gradual.OnVerifiedImpact(4, 1, 11000));
    Check("new hit adds to remainder only", gradual.VisualPercent(100, 11000) == 60);
    Check("new hit pauses recovery five seconds", gradual.VisualPercent(100, 16000) == 60);
    Check("recovery resumes after new delay", gradual.VisualPercent(100, 16500) == 62.5f);
    Check("fully restores CURRENT HP", gradual.VisualPercent(75, 24000) == 75);
    gradual.OnVerifiedTrueChargeImpact(5, 1, 25000);
    Check("no old remainder after complete recovery", gradual.VisualPercent(100, 25000) == 92.5f);
    gradual.Reset();
    Check("reset during fade clears remainder", gradual.VisualPercent(100, 26000) == 100);
    for (unsigned id = 1; id <= 10; ++id) gradual.OnVerifiedImpact(id, 3, 27000);
    Check("fade accumulation still caps at 100", gradual.VisualPercent(100, 27000) == 0);
    Check("capped reduction also recovers smoothly", gradual.VisualPercent(100, 33000) == 5);
    fade.recoveryPerSecond = -1;
    Check("negative recovery rate rejected", !vce::ChargeBurst(fade).OnVerifiedImpact(1, 1, 1));
    fade.recoveryPerSecond = std::numeric_limits<float>::quiet_NaN();
    Check("NaN recovery rate rejected", !vce::ChargeBurst(fade).OnVerifiedImpact(1, 1, 1));
    vce::ChargeBurstConfig combat;
    combat.reduction[0] = combat.reduction[1] = 0;
    combat.reduction[2] = 5;
    combat.trueChargeReduction[0] = combat.trueChargeReduction[1] = 5;
    combat.durationMs = 5000;
    combat.recoveryPerSecond = 5;
    vce::ChargeBurst limited(combat);
    Check("level one disabled", !limited.OnVerifiedImpact(1, 1, 1000));
    Check("level two disabled", !limited.OnVerifiedImpact(2, 2, 2000));
    Check("disabled levels leave clothes unchanged", limited.VisualPercent(100, 2000) == 100);
    Check("level three triggers five points", limited.OnVerifiedImpact(3, 3, 3000) && limited.VisualPercent(100, 3000) == 95);
    Check("weak contact does not count", !limited.OnVerifiedImpact(4, 1, 7900));
    Check("weak contact does not postpone recovery", limited.VisualPercent(100, 8500) == 97.5f);
    Check("TCS first adds five to recovering remainder", limited.OnVerifiedTrueChargeImpact(5, 1, 8500) && limited.VisualPercent(100, 8500) == 92.5f);
    Check("TCS second also adds five", limited.OnVerifiedTrueChargeImpact(5, 2, 9000) && limited.VisualPercent(100, 9000) == 87.5f);
    limited.Reset();
    limited.OnVerifiedImpact(1, 3, 10000);
    limited.OnVerifiedImpact(2, 3, 11000);
    limited.OnVerifiedTrueChargeImpact(3, 1, 12000);
    limited.OnVerifiedTrueChargeImpact(3, 2, 13000);
    Check("new full combo totals twenty points", limited.VisualPercent(100, 13000) == 80);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
