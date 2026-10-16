"""Conservative editor collections for alternative levels of detail.

LOD geometry stays intact. Common (FFFF) meshes are never treated as alternatives.
Bounds provide suggestions, not a claim that a clothing item has been identified.
"""
from __future__ import annotations

import numpy as np
from mod3 import Mod3Error

LOD_VIEWS = {"高精度": 1, "精度 2": 2, "精度 3": 4, "精度 4": 8,
             "精度 5": 16, "远距离": 32, "全部版本": None}


def lod_mask(mesh):
    value = getattr(mesh, "lod", 65535)
    return 65535 if value == -1 else value


def alternative(mesh):
    return 0 < lod_mask(mesh) < 65535


def lod_label(mesh):
    mask = lod_mask(mesh)
    if mask == 65535:
        return "通用"
    labels = {1: "高精度", 2: "精度 2", 4: "精度 3", 8: "精度 4", 16: "精度 5", 65504: "远距离"}
    return labels.get(mask, f"掩码 0x{mask & 0xffffffff:X}")


def visible_lod(mesh, bit):
    mask = lod_mask(mesh)
    # Preserve unknown masks in the preview; they require explicit inspection.
    return bit is None or not 0 < mask < 65535 or bool(mask & bit)


def _bounds(mesh):
    if not len(mesh.faces):
        return None
    points = mesh.vertices[np.unique(mesh.faces)].astype(np.float64)
    if not len(points) or not np.isfinite(points).all():
        return None
    return points.min(0), points.max(0)


def _similar(a, b):
    extent = np.maximum(a[1] - a[0], b[1] - b[0])
    # Axis-specific tolerances avoid associating narrow, nearby duplicate parts.
    tolerance = np.maximum(extent * .18, max(float(extent.max()), .001) * .015)
    return bool(np.all(np.abs(a[0] - b[0]) <= tolerance)
                and np.all(np.abs(a[1] - b[1]) <= tolerance))


def _contains(outer, inner):
    extent = outer[1] - outer[0]
    tolerance = np.maximum(extent * .08, max(float(extent.max()), .001) * .01)
    return bool(np.all(inner[0] >= outer[0] - tolerance) and np.all(inner[1] <= outer[1] + tolerance))


def suggest_lod_groups(workspace, keys, *, cancelled=lambda: False):
    """Match adjacent disjoint LOD masks within file/material/current Group.

    Accept a unique comparable range or a uniquely contained set whose union
    matches the coarser range. Merged coarse versions connect their fine members
    into one control family. Ambiguous associations remain unassigned.
    """
    buckets = {}
    for document, mesh in workspace.all_meshes():
        if cancelled():
            raise Mod3Error("已取消分析")
        key = (document.name, mesh.index)
        if key not in keys or not alternative(mesh):
            continue
        bounds = _bounds(mesh)
        if bounds is not None:
            buckets.setdefault((document.name, mesh.material_id, mesh.visible_condition), []).append(
                {"key": key, "mask": lod_mask(mesh), "bounds": bounds})
    result = []
    for (name, material, gid), parts in buckets.items():
        tiers = {}
        for p in parts:
            tiers.setdefault(p["mask"], []).append(p)
        masks = sorted(tiers, key=lambda mask: (mask & -mask, mask))
        parent = {p["key"]: p["key"] for p in parts}
        def root(key):
            while parent[key] != key:
                key = parent[key]
            return key
        def join(a, b):
            parent[root(a)] = root(b)
        for fine_mask, coarse_mask in zip(masks, masks[1:]):
            if cancelled():
                raise Mod3Error("已取消分析")
            if fine_mask & coarse_mask:
                continue  # Simultaneously visible masks aren't alternatives.
            fine, coarse = tiers[fine_mask], tiers[coarse_mask]
            contains = {p["key"]: [q for q in coarse if _contains(q["bounds"], p["bounds"])] for p in fine}
            for q in coarse:
                matches = [p for p in fine if _similar(p["bounds"], q["bounds"])]
                if len(matches) == 1:
                    p = matches[0]
                    reverse = [other for other in coarse if _similar(p["bounds"], other["bounds"])]
                    if len(reverse) == 1:
                        # A larger fine part plus smaller contained parts can
                        # still share one coarse mesh; include them below.
                        join(p["key"], q["key"])
                contained = [p for p in fine if len(contains[p["key"]]) == 1
                             and contains[p["key"]][0]["key"] == q["key"]]
                if len(contained) > 1:
                    union = (np.minimum.reduce([p["bounds"][0] for p in contained]),
                             np.maximum.reduce([p["bounds"][1] for p in contained]))
                    if _similar(union, q["bounds"]):
                        for p in contained:
                            join(p["key"], q["key"])
        families = {}
        for p in parts:
            families.setdefault(root(p["key"]), []).append(p)
        document = next(d for d in workspace.documents if d.name == name)
        serial = 0
        for members in families.values():
            levels = {p["mask"] for p in members}
            if len(levels) < 2:
                continue
            serial += 1
            counts = [sum(p["mask"] == mask for p in members) for mask in levels]
            merged = max(counts) > 1
            reason = "不同 LOD 的范围对应；请切换精度核对"
            if merged:
                reason += "；低精度合并多个高精度部件，需一起控制"
            result.append({"name": f"{document.equipment_part} · LOD关联 · {material} · {serial}",
                           "document": name, "material_id": material,
                           "material": document.material_info(material).name,
                           "group_ids": [gid], "members": sorted(p["key"] for p in members),
                           "reason": reason, "review": False, "accepted": True, "lod_family": True,
                           "merged_lod": merged})
    return result
