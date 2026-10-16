"""Stage coarse LOD island partitions and complete control groups."""
from __future__ import annotations

from pathlib import Path
import numpy as np
from mod3 import Mod3Error
from splitting import components
from lod_groups import suggest_lod_groups, lod_mask, lod_label
from mesh_features import feature_profile
from lod_matching import match_island


def prepare(session, keys, *, include_grouped=False, use_features=True, cancelled=lambda: False):
    session.validate_patch()
    keys = session._check_keys(keys)
    files = {name for name, index in keys}
    if len(files) != 1:
        raise Mod3Error("请先选择一个 MOD3 文件或其中的 Mesh；跨 LOD 同步拆分逐个文件处理")
    name = next(iter(files))
    document = session.document(name)
    keys = {(name, m.index) for m in document.meshes}
    before = {d.name: session._snapshot(d) for d in session.workspace.documents}
    tags = session._groups()
    grouped = set().union(*session.named_groups.values()) if session.named_groups else set()
    families = suggest_lod_groups(session.workspace, keys, cancelled=cancelled)
    staged = session._staged_copy()
    partitions, slots, proposals, all_reference_indices = {}, {}, [], set()
    skipped = 0
    for family in families:
        members = family["members"]
        masks = [lod_mask(document.meshes[i]) for n, i in members]
        finest = min(masks, key=lambda mask: mask & -mask)
        high = [document.meshes[i] for n, i in members if lod_mask(document.meshes[i]) == finest]
        if len(high) < 2:
            continue
        if any(lod_mask(document.meshes[i]) & finest for n, i in members if lod_mask(document.meshes[i]) != finest):
            continue
        if not include_grouped and set(members) & grouped:
            skipped += len(members)
            continue
        references = [{"key": (name, m.index), "triangles": np.ascontiguousarray(m.vertices[m.faces], dtype=np.float64),
                       "uv_triangles": np.empty((0, 3, 2), np.float64) if m.uvs is None else np.ascontiguousarray(m.uvs[m.faces], dtype=np.float64),
                       "features": feature_profile(m)} for m in high]
        points = [document.meshes[i].vertices for n, i in members]
        low, upper = np.minimum.reduce([p.min(0) for p in points]), np.maximum.reduce([p.max(0) for p in points])
        span = max(float(np.max(upper - low)), .001)
        controls = {}
        for m in high:
            key = (name, m.index)
            alias = session.annotations.get(key, {}).get("name")
            label = f"{document.equipment_part} · {alias or 'Mesh ' + str(m.index)} · 全精度"
            group = {"name": label, "document": name, "material_id": m.material_id,
                     "members": [(m.index, 0)], "evidence": {}, "reason": "以现有高精度 Mesh 为基准；低精度按完整几何块对应",
                     "reference": key, "review": False, "accepted": True}
            proposals.append(group)
            controls[key] = group
            all_reference_indices.add(m.index)
        for n, index in members:
            mesh = document.meshes[index]
            if lod_mask(mesh) == finest:
                continue
            if cancelled():
                raise Mod3Error("已取消分析")
            bins = {reference["key"]: [] for reference in references}
            pending = []
            for island in components(mesh):
                if cancelled():
                    raise Mod3Error("已取消分析")
                match, evidence, scores = match_island(mesh, island, references, span, use_features=use_features)
                if match is None:
                    pending.append((island, evidence, scores[0]["reference"]))
                else:
                    bins[match].append((island, evidence))
            parts = []
            for reference, assigned in bins.items():
                if not assigned:
                    continue
                slot = len(parts)
                parts.append(np.sort(np.concatenate([part for part, evidence in assigned])))
                controls[reference]["members"].append((index, slot))
                controls[reference]["evidence"][(index, slot)] = (
                    f"原 Mesh {index} · {lod_label(mesh)} · {len(assigned)} 个完整几何块；" + assigned[0][1])
            for island, evidence, candidate in pending:
                slot = len(parts)
                parts.append(island)
                proposals.append({"name": f"待确认 · Mesh {index} / 块 {slot + 1}",
                                  "document": name, "material_id": mesh.material_id,
                                  "members": [(index, slot)], "evidence": {(index, slot): evidence},
                                  "reason": "对应不唯一或完整范围不匹配；移入对应高精度组后再应用",
                                  "candidate_reference": candidate,
                                  "review": True, "accepted": False})
            if len(parts) > 1:
                partitions[index] = parts
            slots[index] = len(parts)
    if not proposals:
        detail = "；已有组请勾选“重新整理已有组”" if skipped else "；可先拆分高精度 Mesh，再选择文件重试"
        raise Mod3Error("此文件没有可分开同步的多个高精度基准" + detail)
    if cancelled():
        raise Mod3Error("已取消分析")
    layout = staged._partition_meshes(name, partitions) if partitions else {m.index: [(name, m.index)] for m in document.meshes}
    origins = {(d.name, m.index): (d.name, m.index) for d, m in session.workspace.all_meshes() if d.name != name}
    for original, children in layout.items():
        for child in children:
            origins[child] = (name, original)
    for group in proposals:
        if group.get("reference"):
            group["reference_key"] = layout[group["reference"][1]][0]
        group["members"] = [layout[index][slot] for index, slot in group["members"]]
        group["evidence"] = {layout[index][slot]: evidence for (index, slot), evidence in group["evidence"].items()}
    occupied = set(session.all_groups())
    for group in proposals:
        label = group["name"][:80]
        label, suffix = label, 2
        candidate = label
        while candidate in occupied:
            ending = f" ({suffix})"
            candidate = label[:80 - len(ending)] + ending
            suffix += 1
        group["name"] = candidate
        occupied.add(candidate)
    eligible = {key for group in proposals for key in group["members"]}
    staged.validate_patch()
    return {"staged": staged, "groups": proposals, "before": before, "tags": tags, "origins": origins,
            "split_count": len(partitions), "skipped": skipped, "lod_families": len([g for g in proposals if g.get('reference')]),
            "lod_added": 0, "use_features": use_features, "warnings": [], "keys": keys,
            "eligible": eligible, "sync_lod": True,
            "reference_keys": {layout[i][0] for i in all_reference_indices}}
