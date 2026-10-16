"""Optional random damage, reversible dual-mode exports and stable preview state."""
from __future__ import annotations

import copy
import hashlib
import math
import random
import re
import secrets
from collections import defaultdict
from pathlib import Path

ID_MIN, ID_MAX = 20000, 64999


def settings(value=None):
    value = value or {}
    enabled, mode, token = value.get('enabled', False), value.get('mode', 'manual'), value.get('token', '')
    if type(enabled) is not bool or mode not in ('manual', 'random') or not isinstance(token, str):
        raise ValueError('战损模式设置无效')
    if token and not re.fullmatch('[0-9a-f]{16}', token):
        raise ValueError('战损配置标识无效')
    if enabled and not token:
        raise ValueError('缺少战损配置标识')
    if mode == 'random' and not enabled:
        raise ValueError('随机模式必须启用双模式导出')
    return {'enabled': enabled, 'mode': mode, 'token': token}


def new_settings(mode, previous):
    if mode == 'manual' and not previous['enabled']:
        return previous.copy()
    return settings({'enabled': True, 'mode': mode, 'token': previous['token'] or secrets.token_hex(8)})


def units(session):
    """Leaf named groups are atomic; outfits never share a random budget.

    A protected member protects the whole unit. Explicit eligibility overrides
    Group 0 only for that member, without altering its manual assignment.
    """
    groups = {key: name for name, members in session.named_groups.items() for key in members}
    grouped = defaultdict(list)
    for document, mesh in session.workspace.all_meshes():
        key = (document.name, mesh.index)
        label = groups.get(key, f'{Path(document.name).name} / Mesh {mesh.index}')
        identity = ('group', label) if key in groups else ('mesh', *key)
        grouped[(document.outfit_id, identity)].append(key)
    result = []
    for (pool, identity), keys in sorted(grouped.items(), key=lambda pair: str(pair[0])):
        protected = any(session.annotations.get(k, {}).get('damage_role') == 'base' or
                        (session.annotations.get(k, {}).get('damage_role', '') == '' and
                         session.document(k[0]).meshes[k[1]].visible_condition == 0) for k in keys)
        result.append({'pool': pool, 'name': identity[1] if identity[0] == 'group' else
                       f'{Path(keys[0][0]).name} / Mesh {keys[0][1]}',
                       'keys': tuple(sorted(keys)), 'protected': protected})
    return result


def step_for_health(hp):
    if not math.isfinite(hp) or hp < 0:
        return 0
    return min(20, max(0, int(math.floor((100 - min(100, hp)) / 5 + 1e-9))))


class DamagePreview:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.signature = None
        self.hidden = {}

    def update(self, records, hp):
        signature = tuple((r['pool'], r['keys'], r['protected']) for r in records)
        if signature != self.signature:
            self.signature, self.hidden = signature, {}
        pools = defaultdict(list)
        for index, record in enumerate(records):
            if not record['protected']:
                pools[record['pool']].append(index)
        step = step_for_health(hp)
        hidden_keys = set()
        for pool, indices in pools.items():
            hidden = self.hidden.setdefault(pool, set())
            target = len(indices) * step // 20
            if len(hidden) < target:
                hidden.update(self.rng.sample([i for i in indices if i not in hidden], target - len(hidden)))
            elif len(hidden) > target:
                hidden.difference_update(self.rng.sample(sorted(hidden), len(hidden) - target))
            hidden_keys.update(k for i in hidden for k in records[i]['keys'])
        return hidden_keys


def _native_prefix(name):
    parts = name.replace('\\', '/').split('/')
    index = next((i for i, part in enumerate(parts) if part.lower() == 'nativepc'), None)
    return '/'.join(parts[:index + 1]) if index is not None else 'nativePC'


def _reserved_ids(token):
    # Read other installed manifests when a game directory was configured.
    from game_resources import saved_game_directory
    root = saved_game_directory()
    ids = set()
    if root:
        directory = Path(root) / 'nativePC/plugins'
        for path in [directory / 'MHWI-VisualControllerExtended.ini', *directory.glob('MHWLayerStudio-*.ini')]:
            if token in path.name:
                continue
            try:
                ids.update(int(x) for x in re.findall(r'^[ \t]*Id[ \t]*=[ \t]*(\d+)', path.read_text(encoding='utf-8-sig'), re.M))
            except (OSError, UnicodeError, ValueError):
                continue
    return ids


def export_plan(session):
    if session.workspace.kind == 'mod3':
        raise ValueError('双模式需要同时导出插件配置，请打开模组压缩包或完整文件夹再导出。')
    config = session.damage_model
    records = units(session)
    token = config['token']
    occupied = {m.visible_condition for _, m in session.workspace.all_meshes()} | _reserved_ids(token)
    cursor = ID_MIN + int(token[:8], 16) % (ID_MAX - ID_MIN + 1)
    clones = {d.name: copy.copy(d) for d in session.workspace.documents}
    for d in clones.values():
        d._data = bytearray(d.bytes())
        d.meshes = []
        d._parse()
    files = defaultdict(list)
    pool_units = defaultdict(int)
    for record in records:
        # Ordinary Group 0 needs no runtime control. Explicitly protected manual
        # layers still receive a control ID so random mode can force them visible.
        variants = defaultdict(list)
        for key in record['keys']:
            group = session.document(key[0]).meshes[key[1]].visible_condition
            if group == 0 and record['protected']:
                continue
            if group != 0 and group not in session.health_thresholds:
                raise ValueError(f'{record["name"]} 使用自定义 Group {group}；请先为它设置隐藏阈值，才能保留手动模式。')
            variants[(group, _native_prefix(key[0]))].append(key)
        unit = -1 if record['protected'] else pool_units[record['pool']]
        if not record['protected']:
            pool_units[record['pool']] += 1
        for (group, prefix), keys in sorted(variants.items()):
            for _ in range(ID_MAX - ID_MIN + 1):
                if cursor not in occupied:
                    break
                cursor = ID_MIN if cursor == ID_MAX else cursor + 1
            else:
                raise ValueError('独立控制 ID 已用完，请减少可控制部件或检查已有模组配置。')
            control_id = cursor
            occupied.add(control_id)
            for name, index in keys:
                clones[name].set_visible_condition(index, control_id)
            # Pool names are opaque hashes, not INI-escaped asset paths.
            pool = hashlib.sha256((token + record['pool']).encode()).hexdigest()[:16]
            files[prefix].append((control_id, pool, unit, session.health_thresholds.get(group, -1)))
    if sum(pool_units.values()) > 4096 or any(count > 2048 for count in pool_units.values()):
        raise ValueError('可随机部件过多，请先成组；每套最多2048组，单包最多4096组。')
    output = {}
    for prefix, parts in files.items():
        lines = ['; MHW Layer Studio dual-mode manifest; requires VCE 1.5.0 or later.',
                 '; Keep beside MHWI-VisualControllerExtended.dll. Ctrl+F5 reloads mode changes.',
                 '[DamageModel]', 'Version=1', f'Mode={config["mode"]}', 'Step=5', '']
        for index, (control_id, pool, unit, threshold) in enumerate(parts):
            lines += [f'[DamagePart{index}]', f'Id={control_id}', f'Pool={pool}', f'Unit={unit}', f'Threshold={threshold}', '']
        output[f'{prefix}/plugins/MHWLayerStudio-{token}.ini'] = '\r\n'.join(lines).encode('utf-8-sig')
    workspace = copy.copy(session.workspace)
    workspace.documents = list(clones.values())
    workspace.export_files = output
    return workspace
