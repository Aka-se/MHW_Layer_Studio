"""Match intact coarse geometry islands to finest-detail mesh references.

Face and vertex records are never edited; ambiguous islands stay unassigned.
"""
from __future__ import annotations

import numpy as np
from numba import njit
import sys

CACHE = not getattr(sys, "frozen", False)


@njit(cache=CACHE, nogil=True)
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@njit(cache=CACHE, nogil=True)
def _closest(point, a, b, c):
    ab, ac, ap = b - a, c - a, point - a
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a, 0.0, 0.0
    bp = point - b
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b, 1.0, 0.0
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3) if d1 != d3 else 0.0
        return a + v * ab, v, 0.0
    cp = point - c
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c, 0.0, 1.0
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6) if d2 != d6 else 0.0
        return a + w * ac, 0.0, w
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        denominator = d4 - d3 + d5 - d6
        w = (d4 - d3) / denominator if denominator != 0 else 0.0
        return b + w * (c - b), 1.0 - w, w
    denominator = va + vb + vc
    if abs(denominator) < 1e-20:
        # Degenerate triangles: conservatively use the nearest vertex.
        da, db, dc = _dot(ap, ap), _dot(bp, bp), _dot(cp, cp)
        if da <= db and da <= dc:
            return a, 0.0, 0.0
        if db <= dc:
            return b, 1.0, 0.0
        return c, 0.0, 1.0
    v, w = vb / denominator, vc / denominator
    return a + ab * v + ac * w, v, w


@njit(cache=CACHE, nogil=True)
def surface_distances(points, triangles, triangle_uvs):
    distances = np.empty(len(points), np.float64)
    projected_uvs = np.empty((len(points), 2), np.float64)
    for i in range(len(points)):
        best, best_uv = 1e300, np.zeros(2, np.float64)
        p = points[i]
        for j in range(len(triangles)):
            a, b, c = triangles[j, 0], triangles[j, 1], triangles[j, 2]
            lower = 0.0
            for axis in range(3):
                lo = min(a[axis], b[axis], c[axis])
                hi = max(a[axis], b[axis], c[axis])
                delta = max(lo - p[axis], p[axis] - hi, 0.0)
                lower += delta * delta
            if lower > best:
                continue
            q, v, w = _closest(p, a, b, c)
            delta = p - q
            distance = _dot(delta, delta)
            if distance < best:
                best = distance
                if len(triangle_uvs):
                    best_uv = triangle_uvs[j, 0] * (1.0 - v - w) + triangle_uvs[j, 1] * v + triangle_uvs[j, 2] * w
        distances[i] = np.sqrt(best)
        projected_uvs[i] = best_uv
    return distances, projected_uvs


def island_samples(mesh, indices, limit=48):
    used = np.unique(mesh.faces[indices])
    if len(used) > limit:
        # Include coordinate extremes before evenly sampling vertex indices.
        vertices = mesh.vertices[used]
        extremes = used[np.concatenate((vertices.argmin(0), vertices.argmax(0)))]
        used = np.unique(np.concatenate((extremes, used[np.linspace(0, len(used) - 1, limit, dtype=int)])))
    points = mesh.vertices[used].astype(np.float64)
    uvs = None if mesh.uvs is None else mesh.uvs[used].astype(np.float64)
    return points, uvs


def match_island(mesh, indices, references, span, *, use_features=True):
    from mesh_features import feature_profile, compare_features
    from types import SimpleNamespace
    points, uvs = island_samples(mesh, indices)
    part = SimpleNamespace(vertices=mesh.vertices, faces=mesh.faces[indices], uvs=mesh.uvs,
                           bone_ids=mesh.bone_ids, bone_weights=mesh.bone_weights)
    profile = feature_profile(part)
    scores, point_distances = [], []
    for reference in references:
        distances, projected_uvs = surface_distances(points, reference["triangles"], reference["uv_triangles"])
        bones, _ = compare_features(profile, reference["features"])
        uv_error = None if uvs is None or not len(reference["uv_triangles"]) else float(np.linalg.norm(uvs - projected_uvs, axis=1).mean())
        scores.append({"reference": reference["key"], "mean": float(distances.mean()),
                       "max": float(distances.max()), "bone": bones, "uv": uv_error})
        point_distances.append(distances)
    ordered = sorted(range(len(scores)), key=lambda i: scores[i]["mean"])
    best = ordered[0]
    winner, runner = scores[best], scores[ordered[1]]
    votes = float(np.mean(point_distances[best] <= np.minimum.reduce(point_distances) + .0005 * span))
    accepted = (winner["mean"] <= .012 * span and winner["max"] <= .03 * span
                and runner["mean"] - winner["mean"] >= .002 * span and votes >= .9
                and (not use_features or winner["bone"] is None or winner["bone"] >= .2))
    # Validate every used vertex against the winner. Sampling chooses the
    # candidate; full coverage detects islands containing unrelated geometry.
    if accepted:
        used = np.unique(mesh.faces[indices])
        distances, _ = surface_distances(mesh.vertices[used].astype(np.float64), references[best]["triangles"],
                                         np.empty((0, 3, 2), np.float64))
        accepted = bool(len(distances) and distances.max() <= .03 * span)
        winner["max"] = float(distances.max())
    evidence = f"对应高精度 Mesh {winner['reference'][1]} · 表面平均间距 {winner['mean']:.3f} / 最大 {winner['max']:.3f} · 样本一致 {votes:.0%}"
    if use_features:
        evidence += " · 骨骼未知" if winner["bone"] is None else f" · 骨骼重合 {winner['bone']:.0%}"
        if winner["uv"] is not None:
            evidence += f" · 对应 UV 偏差 {winner['uv']:.3f}（参考）"
    return winner["reference"] if accepted else None, evidence, [scores[i] for i in ordered]
