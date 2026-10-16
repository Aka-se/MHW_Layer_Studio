"""Conservative geometry suggestions; material is a boundary, not an identity."""
from __future__ import annotations

import numpy as np
from mod3 import Mod3Error
from lod_groups import lod_mask
from mesh_features import feature_profile, compare_features
from mesh_regions import position_hint, group_order
from surface_distance import SurfaceDistances


def _bounds(mesh):
    used = np.unique(mesh.faces) if len(mesh.faces) else np.arange(len(mesh.vertices))
    points = mesh.vertices[used].astype(np.float64)
    if not len(points) or not np.isfinite(points).all():
        raise Mod3Error("部件为空或坐标异常，不能生成空间建议")
    low, high = points.min(0), points.max(0)
    return low, high


def _gap(a, b):
    return float(np.linalg.norm(np.maximum(0, np.maximum(a[0] - b[1], b[0] - a[1]))))


def _region(center, low, height):
    y = (center[1] - low[1]) / height
    return next((label for limit, label in ((.22, "下部"), (.5, "中下部"), (.68, "中部"), (.88, "上部")) if y < limit), "顶部")


def _bone_assemblies(parts, height, tolerance, surfaces, cancelled):
    """Join compact, surface-connected pieces rigidly following one joint.

    Unlike a generic proximity chain, every member independently needs >=90%
    influence from the same joint. A fixed overall span limits propagation.
    Unknown or mixed skinning falls through to the existing anchor review.
    """
    buckets = {}
    for part in parts:
        bones = part["features"]["bones"]
        if bones is not None and bones.max() >= .9:
            buckets.setdefault((int(bones.argmax()), part["region"]), []).append(part)
    assemblies = []
    for (bone, _), pool in sorted(buckets.items()):
        pool = sorted(pool, key=lambda p: (-p["triangles"], p["key"]))
        while pool:
            members, pool = [pool[0]], pool[1:]
            low, high = (v.copy() for v in members[0]["bounds"])
            changed = True
            while changed:
                changed = False
                for candidate in pool[:]:
                    if cancelled():
                        raise Mod3Error("已取消分析")
                    lo = np.minimum(low, candidate["bounds"][0])
                    hi = np.maximum(high, candidate["bounds"][1])
                    if np.max(hi - lo) > height * .18:
                        continue
                    if not any(_gap(candidate["bounds"], p["bounds"]) <= tolerance
                               and surfaces.distance(candidate["mesh"], p["mesh"], tolerance) <= tolerance
                               for p in members):
                        continue
                    members.append(candidate)
                    pool = [p for p in pool if p is not candidate]
                    low, high, changed = lo, hi, True
            if len(members) > 1:
                for p in members:
                    p["evidence"] = f"骨骼 {bone} 影响 {p['features']['bones'][bone]:.0%}；表面相连的局部组件"
                assemblies.append({"anchors": members.copy(), "members": members.copy(),
                                   "review": False, "assembly": True,
                                   "reason": f"同一骨骼 {bone} 影响均 ≥90%；表面相连，整体范围受限，组成完整局部组件"})
    return assemblies


def suggest_groups(workspace, keys, *, distance="保守", pair=True, use_features=True, cancelled=lambda: False):
    """Only return proposed memberships. Never change faces, vertices or IDs.

    Strong single-joint pieces form bounded local assemblies first. Other large
    anchors use complete linkage. Small parts attach to fixed anchors; a tie stays unassigned. Pairing
    checks reflection in X, dimensions and triangle counts within one bucket.
    """
    factors = {"保守": .008, "常规": .015, "宽松": .025}
    surfaces = SurfaceDistances(cancelled)
    if distance not in factors:
        raise Mod3Error("空间聚合程度无效")
    frames = {}
    for document in workspace.documents:
        points = [mesh.vertices for mesh in document.meshes if len(mesh.vertices) and np.isfinite(mesh.vertices).all()]
        if not points:
            continue
        low, high = np.minimum.reduce([p.min(0) for p in points]), np.maximum.reduce([p.max(0) for p in points])
        frame = frames.get(document.outfit_id)
        frames[document.outfit_id] = (np.minimum(frame[0], low), np.maximum(frame[1], high)) if frame else (low, high)
    buckets = {}
    for document in workspace.documents:
        for mesh in document.meshes:
            key = (document.name, mesh.index)
            if key not in keys:
                continue
            if cancelled():
                raise Mod3Error("已取消分析")
            low, high = _bounds(mesh)
            buckets.setdefault((document.name, mesh.material_id, mesh.visible_condition, lod_mask(mesh)), []).append(
                {"key": key, "bounds": (low, high), "center": (low + high) / 2,
                 "mesh": mesh,
                 "extent": high - low, "triangles": len(mesh.faces),
                 "features": feature_profile(mesh) if use_features else {"bones": None, "uv": None},
                 "evidence": ""})
    suggestions = []
    for (name, material, group_id, lod), parts in buckets.items():
        document = next(d for d in workspace.documents if d.name == name)
        frame = frames[document.outfit_id]
        height = max(float(frame[1][1] - frame[0][1]), .001)
        tolerance = factors[distance] * height
        mirror_x = float((frame[0][0] + frame[1][0]) / 2)
        for p in parts:
            p["region"] = _region(p["center"], frame[0], height)
        max_triangles = max(p["triangles"] for p in parts)
        clusters = _bone_assemblies(parts, height, tolerance, surfaces, cancelled)
        assembled = {p["key"] for c in clusters for p in c["members"]}
        anchors, small = [], []
        for p in sorted(parts, key=lambda p: (-p["triangles"], p["key"])):
            if p["key"] in assembled:
                continue
            (anchors if p["extent"].max() >= .055 * height or p["triangles"] >= max(32, max_triangles * .12) else small).append(p)
        # Ensure isolated tiny parts don't all get swallowed by arbitrary proximity.
        if not anchors:
            anchors, small = small[:1], small[1:]
        def near(a, b):
            bones, uv = compare_features(a["features"], b["features"])
            return ((bones is None or bones >= .2)
                    and a["region"] == b["region"] and _gap(a["bounds"], b["bounds"]) <= tolerance
                    and abs(a["center"][1] - b["center"][1]) <= tolerance * 2
                    and np.max(np.abs(a["extent"] - b["extent"])) <= .04 * height
                    and surfaces.distance(a["mesh"], b["mesh"], tolerance) <= tolerance)
        for p in anchors:
            if cancelled():
                raise Mod3Error("已取消分析")
            matches = [c for c in clusters if not c.get("assembly") and all(near(p, q) for q in c["anchors"])]
            if len(matches) == 1:
                matches[0]["anchors"].append(p)
                matches[0]["members"].append(p)
            else:
                clusters.append({"anchors": [p], "members": [p], "review": False, "reason": "独立主体 / 位置接近且范围相似"})
        # Use fixed original anchors, never enlarge them after adding small parts.
        for p in small:
            choices = []
            conflicts = False
            for i, c in enumerate(clusters):
                gaps = [surfaces.distance(p["mesh"], q["mesh"], tolerance) for q in c["anchors"]
                        if p["region"] == q["region"] and _gap(p["bounds"], q["bounds"]) <= tolerance]
                gaps = [gap for gap in gaps if np.isfinite(gap)]
                if gaps and min(gaps) <= tolerance:
                    evidence = [compare_features(p["features"], q["features"]) for q in c["anchors"] if p["region"] == q["region"]]
                    known = [bone for bone, uv in evidence if bone is not None]
                    bone = max(known) if known else None
                    uv_gaps = [uv for b, uv in evidence if uv is not None and (b is None or b >= .45)]
                    uv = min(uv_gaps) if uv_gaps else None
                    if bone is not None and bone < .2:
                        conflicts = True
                        continue
                    choices.append((min(gaps), i, bone, uv))
            choices.sort()
            chosen = choices[0] if choices and (len(choices) == 1 or choices[1][0] - choices[0][0] > .002 * height) else None
            bone_winner = False
            if len(choices) > 1 and all(choice[2] is not None for choice in choices):
                ranked = sorted(choices, key=lambda choice: -choice[2])
                if ranked[0][2] >= .65 and ranked[0][2] - ranked[1][2] >= .2:
                    chosen, bone_winner = ranked[0], True
            if chosen and chosen[2] is not None and chosen[2] < .45:
                chosen = None
            if chosen:
                c = clusters[chosen[1]]
                c["members"].append(p)
                p["evidence"] = f"骨骼影响重合 {chosen[2]:.0%}" if chosen[2] is not None else "骨骼未知；依据空间距离"
                if chosen[3] is not None:
                    p["evidence"] += f" · UV 间距 {chosen[3]:.3f}"
                c["reason"] = f"主体与附近小部件；表面间距 {chosen[0]:.3g}；未扩大归属范围"
                if use_features:
                    c["reason"] += "；骨骼 / UV 辅助核对" if chosen[2] is not None else "；辅助信息不足，按空间判断"
                if bone_winner:
                    c["reason"] += "；骨骼影响区分了附近主体"
            else:
                reason = "小部件归属接近多个主体" if choices else "附近主体骨骼影响不一致" if conflicts else "小部件离主体较远"
                uv_choices = sorted((choice for choice in choices if choice[2] is not None and choice[2] >= .45 and choice[3] is not None), key=lambda choice: choice[3])
                if len(uv_choices) > 1 and uv_choices[0][3] <= .02 and uv_choices[1][3] - uv_choices[0][3] >= .03:
                    winner = uv_choices[0]
                    anchor = clusters[winner[1]]["anchors"][0]["key"][1]
                    reason += f"；UV 更接近 Mesh {anchor}，骨骼重合 {winner[2]:.0%}，需确认"
                    p["evidence"] = f"候选主体 Mesh {anchor} · UV 间距 {winner[3]:.3f}；UV 共用不代表同一衣物"
                clusters.append({"anchors": [], "members": [p], "review": True,
                                 "reason": reason})
        def mirrored(a, b):
            x, y = a["center"], b["center"]
            return (a["region"] == b["region"] and (x[0] - mirror_x) * (y[0] - mirror_x) < 0
                    and abs(x[0] - mirror_x) > .015 * height
                    and abs(x[0] + y[0] - 2 * mirror_x) <= .012 * height
                    and np.max(np.abs(x[1:] - y[1:])) <= .012 * height
                    and np.max(np.abs(a["extent"] - b["extent"])) <= .012 * height
                    and min(a["triangles"], b["triangles"]) / max(1, max(a["triangles"], b["triangles"])) >= .75)
        paired = []
        while clusters:
            c = clusters.pop(0)
            if pair and c["anchors"]:
                candidates = [i for i, other in enumerate(clusters) if other["anchors"]
                              and len(other["anchors"]) == len(c["anchors"])
                              and all(any(mirrored(a, b) for b in other["anchors"]) for a in c["anchors"])
                              and all(any(mirrored(a, b) for a in c["anchors"]) for b in other["anchors"])]
                if len(candidates) == 1:
                    other = clusters.pop(candidates[0])
                    # Keep both independently controllable sides, linked by an
                    # organizational parent rather than duplicate Mesh ownership.
                    sides = sorted([c, other], key=lambda side: float(np.mean([p["center"][0] for p in side["members"]])))
                    c = {**c, "members": c["members"] + other["members"], "sides": sides}
            paired.append(c)
        for i, c in enumerate(paired, 1):
            representative = max(c["members"], key=lambda p: p["triangles"])
            region = position_hint(document, document.meshes[representative["key"][1]], frame)
            material_name = document.material_info(material).name
            label = f"{region} · {document.equipment_part} · {material} {material_name[:24]} · {i}"
            for side_index, side in enumerate(c.get("sides", [c])):
                child = {"name": label,
                                "document": name, "material_id": material,
                                "material": document.material_info(material).name,
                                "group_ids": [group_id], "members": [p["key"] for p in side["members"]],
                                "evidence": {p["key"]: p["evidence"] for p in side["members"] if p["evidence"]},
                                "reason": side["reason"], "review": side["review"], "accepted": not side["review"]}
                if "sides" in c:
                    child.update(name=label[:74] + (" · 左侧" if side_index == 0 else " · 右侧"), parent=label[:80], side=side_index)
                    child["reason"] += "；独立单侧子组，共同父组统一控制"
                suggestions.append(child)
    return [suggestions[int(index)] for index, _ in group_order(workspace, [(str(i), g["members"]) for i, g in enumerate(suggestions)])]
