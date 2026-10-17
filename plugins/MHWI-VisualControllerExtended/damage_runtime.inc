// Included after plugin's INI reader and cached-state definitions.
namespace damage_runtime {
struct Part { int id = -1, unit = -2, threshold = -2, forceHidden = 0; std::string pool; };
struct Model {
    int version = 0, step = 0;
    bool random = false, hasMode = false, valid = true;
    std::map<int, Part> parts;
    std::map<std::string, vce::RandomDamage> pools;
};
SRWLOCK gLock = SRWLOCK_INIT;
std::vector<Model> gModels;
// Static storage: never put this 64KiB table on a game/loader thread's stack.
signed char gStates[65536];
bool gLoaded = false;
std::uintptr_t gActor = 0;
int gMap = -1;
bool gDead = false;

bool Integer(const std::string& value, int& out)
{
    const auto result = std::from_chars(value.data(), value.data() + value.size(), out);
    return result.ec == std::errc{} && result.ptr == value.data() + value.size();
}

void Value(const std::string& section, const std::string& key, const std::string& value, void* context)
{
    auto& model = *static_cast<Model*>(context);
    if (section == "DamageModel") {
        if (key == "Version") model.valid &= Integer(value, model.version);
        else if (key == "Step") model.valid &= Integer(value, model.step);
        else if (key == "Mode") {
            model.hasMode = value == "random" || value == "manual";
            model.random = value == "random";
        }
        return;
    }
    if (section.compare(0, 10, "DamagePart") != 0) return;
    int index = -1;
    if (!Integer(section.substr(10), index) || index < 0 || index >= 8192) { model.valid = false; return; }
    auto& part = model.parts[index];
    if (key == "Id") model.valid &= Integer(value, part.id);
    else if (key == "Unit") model.valid &= Integer(value, part.unit);
    else if (key == "Threshold") model.valid &= Integer(value, part.threshold);
    else if (key == "ForceHidden") model.valid &= Integer(value, part.forceHidden);
    else if (key == "Pool") part.pool = value;
}

bool Validate(Model& model, std::uint64_t seed)
{
    if (!model.valid || (model.version != 1 && model.version != 2) || model.step != 5 || !model.hasMode || model.parts.empty()) return false;
    std::set<int> ids;
    std::map<std::string, std::set<int>> units;
    for (const auto& entry : model.parts) {
        const auto& part = entry.second;
        if (part.id < 20000 || part.id > 64999 || !ids.insert(part.id).second ||
            part.unit < -1 || part.unit >= 2048 || part.threshold < -1 || part.threshold > 100 ||
            (part.forceHidden != 0 && part.forceHidden != 1) ||
            (part.forceHidden && (model.version != 2 || part.unit != -1)) ||
            part.pool.size() != 16 || part.pool.find_first_not_of("0123456789abcdef") != std::string::npos) return false;
        if (part.unit >= 0) units[part.pool].insert(part.unit);
    }
    unsigned total = 0;
    for (const auto& entry : units) {
        if (*entry.second.rbegin() != static_cast<int>(entry.second.size()) - 1) return false;
        total += static_cast<unsigned>(entry.second.size());
        model.pools.emplace(entry.first, vce::RandomDamage(static_cast<unsigned>(entry.second.size()), ++seed));
    }
    return total <= 4096;
}

void Load()
{
    std::vector<Model> models;
    std::set<int> claimed, conflicts;
    if (!plugin::gModuleDir.empty()) {
        WIN32_FIND_DATAW found{};
        HANDLE search = ::FindFirstFileW((plugin::gModuleDir + L"MHWLayerStudio-*.ini").c_str(), &found);
        if (search != INVALID_HANDLE_VALUE) {
            unsigned files = 0;
            do {
                if ((found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || ++files > 128) continue;
                std::string text;
                Model model;
                if (plugin::ReadFileUtf8(plugin::gModuleDir + found.cFileName, text)) {
                    plugin::WalkIni(text, &Value, &model);
                    if (Validate(model, ::GetTickCount64() ^ (static_cast<std::uint64_t>(models.size()) << 32))) {
                        for (const auto& entry : model.parts)
                            if (!claimed.insert(entry.second.id).second) conflicts.insert(entry.second.id);
                        models.push_back(std::move(model));
                        continue;
                    }
                }
                plugin::Log("damage manifest rejected: %ls", found.cFileName);
            } while (::FindNextFileW(search, &found));
            ::FindClose(search);
        }
    }
    std::set<int> legacyIds;
    const LONG active = ::InterlockedCompareExchange(&gActiveRuleBuffer, 0, 0);
    for (int i = 0; i < gRuleCounts[active]; ++i) {
        const int id = gRuleBuffers[active][i].id;
        if (claimed.count(id)) { conflicts.insert(id); legacyIds.insert(id); }
    }
    // Colliding IDs cannot be attributed to a model. Keep them visible and
    // remove whole conflicting models from random budgets rather than guessing.
    models.erase(std::remove_if(models.begin(), models.end(), [&](const Model& model) {
        for (const auto& entry : model.parts) if (conflicts.count(entry.second.id)) return true;
        return false;
    }), models.end());
    ::AcquireSRWLockExclusive(&gLock);
    gModels = std::move(models);
    std::fill(std::begin(gStates), std::end(gStates), static_cast<signed char>(-1));
    for (int id : claimed) if (!legacyIds.count(id)) gStates[id] = 1;
    for (const auto& model : gModels)
        for (const auto& entry : model.parts)
            if (entry.second.forceHidden) gStates[entry.second.id] = 0;
    gLoaded = true; gActor = 0; gMap = -1; gDead = false;
    const auto count = gModels.size();
    ::ReleaseSRWLockExclusive(&gLock);
    plugin::Log("damage manifests: models=%zu claimed_ids=%zu conflicting_ids=%zu (conflicting models kept visible)",
                count, claimed.size(), conflicts.size());
}

void Refresh(const player::StateSnapshot& state, float visual, bool enabled)
{
    ::AcquireSRWLockExclusive(&gLock);
    const bool ready = enabled && state.entityAddress && state.currentMapId >= 0 &&
        std::isfinite(visual) && visual >= 0;
    const bool reset = !ready || gActor != state.entityAddress || gMap != state.currentMapId ||
        gDead != (state.healthPercent <= 0);
    gActor = state.entityAddress; gMap = state.currentMapId; gDead = state.healthPercent <= 0;
    for (auto& model : gModels) {
        for (auto& pool : model.pools) {
            if (reset || !model.random) pool.second.Reset();
            if (ready && model.random) pool.second.Update(visual);
        }
        for (const auto& entry : model.parts) {
            const auto& part = entry.second;
            bool show = !part.forceHidden;
            if (ready && !part.forceHidden) {
                if (model.random) show = part.unit < 0 || !model.pools.at(part.pool).Hidden(static_cast<unsigned>(part.unit));
                else show = part.threshold < 0 || visual > part.threshold;
            }
            gStates[part.id] = static_cast<signed char>(show);
        }
    }
    ::ReleaseSRWLockExclusive(&gLock);
}

int Query(unsigned short id)
{
    ::AcquireSRWLockShared(&gLock);
    const int result = gLoaded ? gStates[id] : -1;
    ::ReleaseSRWLockShared(&gLock);
    return result;
}
} // namespace damage_runtime
