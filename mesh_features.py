"""Read-only bone/primary-UV evidence for conservative editor suggestions.

Vertex layouts follow AsteriskAmpersand/Mod3-MHW-Importer Mod3VertexBuffers.
Never encode weights back into MOD3; unknown or unusual layouts stay unavailable.
"""
from __future__ import annotations

import numpy as np

# UV count, weight slots, optional RGBA after joints.
SKIN_LAYOUTS = {
    0xF637401C: (1, 4, False), 0xF471FE45: (2, 4, False),
    0x3C730760: (1, 4, True), 0xB2FC0083: (2, 4, True),
    0x81F58067: (1, 8, False), 0x83B33E3E: (2, 8, False),
    0x366995A7: (1, 8, True), 0xB8E69244: (2, 8, True),
}


def read_bones(data, start, count, stride, block_type):
    layout = SKIN_LAYOUTS.get(block_type)
    if layout is None:
        return None, None, "无可识别骨骼权重"
    uv_count, slots, color = layout
    weight_bytes = 4 if slots == 4 else 8
    offset = 20 + uv_count * 4
    size = offset + weight_bytes + slots + (4 if color else 0)
    if stride < size or start < 0 or start + count * stride > len(data):
        return None, None, "顶点布局与权重格式不匹配"
    packed = np.ndarray((count,), dtype="<u4", buffer=data, offset=start + offset,
                        strides=(stride,)).copy()
    weights = np.column_stack([((packed >> (10 * i)) & 1023) / 1023.0 for i in range(3)])
    if slots == 8:
        extra = np.ndarray((count, 4), dtype="u1", buffer=data, offset=start + offset + 4,
                           strides=(stride, 1)).astype(np.float64) / 255.0
        weights = np.column_stack((weights, extra))
    residual = 1.0 - weights.sum(1)
    # Quantization can produce a small negative complement. Larger signed
    # residuals require a format-specific interpretation; don't guess at identity.
    if np.any(residual < -.02) or np.any(packed >> 30):
        return None, None, "带特殊或负余数权重，未用于成组"
    weights = np.column_stack((weights, np.maximum(residual, 0)))
    ids = np.ndarray((count, slots), dtype="u1", buffer=data, offset=start + offset + weight_bytes,
                     strides=(stride, 1)).copy()
    # Combine repeated IDs before normalization. Slot order is not bone identity.
    combined = np.zeros_like(weights)
    for i in range(slots):
        first = np.ones(count, bool) if i == 0 else ~(ids[:, :i] == ids[:, i:i + 1]).any(1)
        combined[:, i] = np.where(first, np.minimum((weights * (ids == ids[:, i:i + 1])).sum(1), 1), 0)
    totals = combined.sum(1)
    if np.any(totals <= 0) or not np.isfinite(combined).all():
        return None, None, "权重无有效影响，未用于成组"
    combined /= totals[:, None]
    return ids, combined.astype(np.float32), f"可读 {slots} 骨骼权重（仅分析）"


def feature_profile(mesh):
    used = np.unique(mesh.faces) if len(mesh.faces) else np.arange(len(mesh.vertices))
    ids, weights = getattr(mesh, "bone_ids", None), getattr(mesh, "bone_weights", None)
    bones = None
    if ids is not None and weights is not None and len(used):
        if ids.shape == weights.shape and len(ids) == len(mesh.vertices):
            bones = np.bincount(ids[used].ravel(), weights=weights[used].ravel(), minlength=256).astype(np.float64)
            total = bones.sum()
            bones = bones / total if total > 0 else None
    uv = getattr(mesh, "uvs", None)
    uv_bounds = None
    if uv is not None and len(used) and len(uv) == len(mesh.vertices):
        points = uv[used]
        if np.isfinite(points).all() and points.min() >= -.01 and points.max() <= 1.01:
            uv_bounds = (points.min(0).astype(np.float64), points.max(0).astype(np.float64))
    return {"bones": bones, "uv": uv_bounds}


def compare_features(a, b):
    bones = None if a["bones"] is None or b["bones"] is None else float(np.minimum(a["bones"], b["bones"]).sum())
    uv_gap = None
    if a["uv"] is not None and b["uv"] is not None:
        lo_a, hi_a = a["uv"]
        lo_b, hi_b = b["uv"]
        uv_gap = float(np.linalg.norm(np.maximum(0, np.maximum(lo_a - hi_b, lo_b - hi_a))))
    return bones, uv_gap


def bone_summary(profile, limit=5):
    bones = profile["bones"]
    if bones is None:
        return "未知 / 未参与判断"
    indices = np.argsort(-bones)
    return "、".join(f"{int(i)}（{bones[i]:.0%}）" for i in indices[:limit] if bones[i] >= .02)
