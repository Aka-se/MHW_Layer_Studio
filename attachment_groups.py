"""Read-only accessory proposals around explicitly chosen, fixed seed meshes."""
import math
import numpy as np
from mod3 import Mod3Error
from lod_groups import lod_mask
from mesh_features import feature_profile, compare_features
from mesh_regions import bounds, outfit_frames
from surface_distance import SurfaceDistances


def propose_attachments(workspace, seeds, keys, *, percent=1.5, other_materials=False,
                        use_features=True, cancelled=lambda: False):
    seeds, keys = set(seeds), set(keys)
    lookup = {(d.name, m.index): (d, m) for d, m in workspace.all_meshes()}
    if not seeds or not seeds <= keys or not keys <= lookup.keys():
        raise Mod3Error("请先选择有效的主体部件")
    if not math.isfinite(percent) or not .1 <= percent <= 5:
        raise Mod3Error("关联范围请输入 0.1–5 之间的模型高度百分比")
    if len({(n, lookup[(n, i)][1].visible_condition, lod_mask(lookup[(n, i)][1])) for n, i in seeds}) != 1:
        raise Mod3Error("主体请选择同一文件、同一 Group 和同一精度的部件")
    first_document, first = lookup[next(iter(seeds))]
    if any(not len(lookup[key][1].faces) for key in seeds):
        raise Mod3Error("主体没有可分析的三角面")
    frame = outfit_frames(workspace)[first_document.outfit_id]
    height = max(float(frame[1][1] - frame[0][1]), .001)
    limit = height * percent / 100
    seed_materials = {lookup[k][1].material_id for k in seeds}
    seed_triangles = max(len(lookup[k][1].faces) for k in seeds)
    seed_extent = max(float(np.max(bounds(lookup[k][1])[1] - bounds(lookup[k][1])[0])) for k in seeds)
    distances = SurfaceDistances(cancelled)
    profiles = {}
    def features(key):
        if key not in profiles:
            profiles[key] = feature_profile(lookup[key][1]) if use_features else {"bones": None, "uv": None}
        return profiles[key]
    def broad_gap(a, b):
        lo, hi = bounds(a)
        blo, bhi = bounds(b)
        return float(np.linalg.norm(np.maximum(0, np.maximum(lo - bhi, blo - hi))))
    pool = [key for key in keys - seeds
            if key[0] == first_document.name and len(lookup[key][1].faces)
            and lookup[key][1].visible_condition == first.visible_condition
            and lod_mask(lookup[key][1]) == lod_mask(first)
            and (other_materials or lookup[key][1].material_id in seed_materials)]
    anchors = [k for k in pool if len(lookup[k][1].faces) >= max(32, seed_triangles * .6)
               and np.max(bounds(lookup[k][1])[1] - bounds(lookup[k][1])[0]) >= height * .04]
    proposals = []
    for key in sorted(pool):
        if cancelled():
            raise Mod3Error("已取消分析")
        mesh = lookup[key][1]
        close = [(distances.distance(mesh, lookup[seed][1], limit), seed) for seed in sorted(seeds)
                 if broad_gap(mesh, lookup[seed][1]) <= limit]
        gap, closest = min(close, default=(math.inf, None))
        if not math.isfinite(gap):
            continue
        bone, uv = compare_features(features(key), features(closest))
        small = (len(mesh.faces) <= seed_triangles * .5
                 or float(np.max(bounds(mesh)[1] - bounds(mesh)[0])) <= seed_extent * .5)
        competing = False
        if small:
            competition_limit = min(limit, gap + height * .002)
            for anchor in anchors:
                if anchor != key and broad_gap(mesh, lookup[anchor][1]) <= competition_limit:
                    if distances.distance(mesh, lookup[anchor][1], competition_limit) <= competition_limit:
                        competing = True
                        break
        same_material = mesh.material_id in seed_materials
        recommended = small and same_material and bone is not None and bone >= .6 and not competing
        notes = [f"表面距离 {gap:.3g} / 范围 {limit:.3g}",
                 f"骨骼重合 {bone:.0%}" if bone is not None else "骨骼依据不足"]
        if uv is not None:
            notes.append(f"UV 间距 {uv:.3g}（仅辅助）")
        if not same_material:
            notes.append("跨材质，需确认")
        if not small:
            notes.append("体量较大，需确认是否为附件")
        if competing:
            notes.append("也靠近其他主体，需确认")
        elif bone is not None and bone < .45:
            notes.append("骨骼影响差异较大，需确认")
        proposals.append({"key": key, "distance": gap, "limit": limit, "recommended": recommended,
                          "material_id": mesh.material_id, "triangles": len(mesh.faces),
                          "reason": "；".join(notes)})
    return {"seeds": seeds, "limit": limit, "percent": percent,
            "candidates": sorted(proposals, key=lambda p: (not p["recommended"], p["distance"], p["key"]))}
