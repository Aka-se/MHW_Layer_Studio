#include "../MHWI-VisualControllerExtended.cpp"
#include <fstream>

static bool __fastcall Original(std::uintptr_t, std::uintptr_t, std::uintptr_t, std::uintptr_t) { return false; }
static int Query(unsigned short id) {
    unsigned char object[16]{}; std::memcpy(object + 4, &id, sizeof(id));
    return hook::Detour(0, 0, reinterpret_cast<std::uintptr_t>(object), 0);
}
int main(int argc, char** argv)
{
    int checks = 0, failures = 0;
    auto check = [&](const char* name, bool pass) {
        ++checks; if (!pass) ++failures;
        std::printf("[%s] %s\n", pass ? "PASS" : "FAIL", name);
    };
    plugin::gLogPath = L"damage-test.log";
    if (argc == 2) {
        const int length = ::MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, argv[1], -1, nullptr, 0);
        if (length <= 1) return 1;
        std::wstring directory(static_cast<std::size_t>(length), L'\0');
        ::MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, argv[1], -1, directory.data(), length);
        directory.resize(directory.size() - 1);
        plugin::gModuleDir = directory + L"\\";
        damage_runtime::Load();
        check("real editor export loads", !damage_runtime::gModels.empty());
        player::StateSnapshot state; state.entityAddress = 1; state.currentMapId = 504; state.healthPercent = 100;
        hook::gOriginalFunction = &Original; plugin::gEnabled = 1;
        damage_runtime::Refresh(state, 50, true);
        for (const auto& model : damage_runtime::gModels) {
            check("real export is random mode", model.random);
            for (const auto& entry : model.parts) {
                const auto& part = entry.second;
                const bool expected = part.unit < 0 || !model.pools.at(part.pool).Hidden(static_cast<unsigned>(part.unit));
                check("real exported ID reaches hook cache", Query(static_cast<unsigned short>(part.id)) == expected);
            }
        }
        std::printf("%d exported-manifest checks, %d failures\n", checks, failures);
        return failures ? 1 : 0;
    }
    std::wstring filename = L"MHWLayerStudio-owned-test.ini";
    wchar_t current[MAX_PATH]{}; ::GetCurrentDirectoryW(MAX_PATH, current);
    plugin::gModuleDir = std::wstring(current) + L"\\";
    auto write = [&](const char* mode) {
        FILE* file = nullptr; _wfopen_s(&file, filename.c_str(), L"wb");
        if (!file) return false;
        std::fprintf(file, "[DamageModel]\nVersion=1\nStep=5\nMode=%s\n", mode);
        // 20 units plus one protected part. Two IDs/LODs share unit zero.
        for (int i=0; i<22; ++i)
            std::fprintf(file, "[DamagePart%d]\nId=%d\nPool=abcdef0123456789\nUnit=%d\nThreshold=80\n",
                         i, 21000+i, i < 20 ? i : i == 20 ? 0 : -1);
        std::fclose(file); return true;
    };
    check("owned fixture written", write("random"));
    damage_runtime::Load();
    check("manifest parsed", damage_runtime::gModels.size() == 1);
    hook::gOriginalFunction = &Original;
    plugin::gEnabled = 1;
    player::StateSnapshot state;
    state.entityAddress = 1; state.currentMapId = 504; state.healthPercent = 100;
    player::PublishState(state);
    damage_runtime::Refresh(state, 80, true);
    int hidden = 0;
    for (int i=0; i<20; ++i) hidden += !Query(static_cast<unsigned short>(21000+i));
    check("actual detour hides proportional four", hidden == 4);
    check("LOD variants share exact visibility", Query(21000) == Query(21020));
    check("protected part always visible in random mode", Query(21021) == 1);
    check("unmanaged ID keeps next handler", Query(19999) == 0);
    check("temporary visual budget never writes real HP", player::GetStateSnapshot().healthPercent == 100);
    damage_runtime::Refresh(state, 90, true);
    hidden = 0;
    for (int i=0; i<20; ++i) hidden += !Query(static_cast<unsigned short>(21000+i));
    check("real hook restores two on healing", hidden == 2);
    damage_runtime::Refresh(state, 100, true);
    check("full recovery shows formerly hidden base variant", Query(21000) && Query(21020));
    check("same fixture rewritten for manual", write("manual"));
    damage_runtime::Load(); damage_runtime::Refresh(state, 80, true);
    check("manual mode restores original threshold including protected part", Query(21000) == 0 && Query(21021) == 0);
    damage_runtime::Refresh(state, 90, true);
    check("manual mode shows above saved threshold", Query(21000) == 1 && Query(21021) == 1);
    check("duplicate fixture copied", ::CopyFileW(filename.c_str(), L"MHWLayerStudio-owned-collision.ini", FALSE) != 0);
    damage_runtime::Load();
    check("colliding models excluded from random pools", damage_runtime::gModels.empty());
    check("colliding control IDs kept visible", damage_runtime::Query(21000) == 1);
    ::DeleteFileW(L"MHWLayerStudio-owned-collision.ini");
    IdRule legacy; legacy.id = 21000; legacy.result = 0;
    plugin::PublishRules({legacy}); damage_runtime::Load();
    check("legacy rule ID cannot be overridden by manifest", damage_runtime::Query(21000) == -1 && damage_runtime::gModels.empty());
    plugin::PublishRules({}); damage_runtime::Load();
    state.entityAddress = 0; damage_runtime::Refresh(state, -1, true);
    check("unavailable actor keeps managed parts visible", Query(21000) == 1);
    damage_runtime::Model bad;
    plugin::WalkIni("[DamageModel]\nVersion=1\nStep=5\nMode=random\n"
                    "[DamagePart0]\nId=21000\nPool=abcdef0123456789\nUnit=2\nThreshold=80\n", damage_runtime::Value, &bad);
    check("non-dense unit mapping rejected", !damage_runtime::Validate(bad, 1));
    ::DeleteFileW(filename.c_str()); plugin::gModuleDir.clear(); damage_runtime::Load();
    check("reload without manifests releases IDs", damage_runtime::Query(21000) == -1);
    std::printf("%d integration checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
