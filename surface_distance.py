"""Triangle-surface proximity with per-analysis BVHs; no mesh mutation."""
import heapq
import math
import numpy as np
from numba import njit
from mod3 import Mod3Error


@njit(nogil=True, cache=False)
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@njit(nogil=True, cache=False)
def _segment_point(p, a, b):
    v = b - a
    length = _dot(v, v)
    t = max(0., min(1., _dot(p - a, v) / length)) if length > 1e-24 else 0.
    delta = p - a - t * v
    return _dot(delta, delta)


@njit(nogil=True, cache=False)
def _point_triangle(p, t):
    a, b, c = t[0], t[1], t[2]
    ab, ac = b - a, c - a
    n = np.cross(ab, ac)
    if _dot(n, n) < 1e-24:
        return min(_segment_point(p, a, b), _segment_point(p, b, c), _segment_point(p, c, a))
    ap = p - a
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return _dot(ap, ap)
    bp = p - b
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return _dot(bp, bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        q = ap - d1 / (d1 - d3) * ab
        return _dot(q, q)
    cp = p - c
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return _dot(cp, cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        q = ap - d2 / (d2 - d6) * ac
        return _dot(q, q)
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        q = bp - (d4 - d3) / ((d4 - d3) + (d5 - d6)) * (c - b)
        return _dot(q, q)
    q = _dot(ap, n)
    return q * q / _dot(n, n)


@njit(nogil=True, cache=False)
def _segments(a, b, c, d):
    u, v, w = b - a, d - c, a - c
    aa, ee = _dot(u, u), _dot(v, v)
    if aa < 1e-24:
        return _segment_point(a, c, d)
    if ee < 1e-24:
        return _segment_point(c, a, b)
    uv, uw, vw = _dot(u, v), _dot(u, w), _dot(v, w)
    den = aa * ee - uv * uv
    s = max(0., min(1., (uv * vw - uw * ee) / den)) if den > 1e-24 else 0.
    t = (uv * s + vw) / ee
    if t < 0:
        t, s = 0., max(0., min(1., -uw / aa))
    elif t > 1:
        t, s = 1., max(0., min(1., (uv - uw) / aa))
    q = w + s * u - t * v
    return _dot(q, q)


@njit(nogil=True, cache=False)
def _pierces(a, b, triangle):
    direction = b - a
    e1, e2 = triangle[1] - triangle[0], triangle[2] - triangle[0]
    p = np.cross(direction, e2)
    det = _dot(e1, p)
    if abs(det) < 1e-12:
        return False
    q = a - triangle[0]
    u = _dot(q, p) / det
    r = np.cross(q, e1)
    v, along = _dot(direction, r) / det, _dot(e2, r) / det
    return u >= -1e-10 and v >= -1e-10 and u + v <= 1 + 1e-10 and -1e-10 <= along <= 1 + 1e-10


@njit(nogil=True, cache=False)
def _leaf_distance(a, b, best):
    for first in a:
        for second in b:
            for i in range(3):
                if _pierces(first[i], first[(i + 1) % 3], second) or _pierces(second[i], second[(i + 1) % 3], first):
                    return 0.
                best = min(best, _point_triangle(first[i], second), _point_triangle(second[i], first))
                for j in range(3):
                    best = min(best, _segments(first[i], first[(i + 1) % 3], second[j], second[(j + 1) % 3]))
            if best < 1e-24:
                return 0.
    return best


def _gap_squared(first, second):
    delta = np.maximum(0, np.maximum(first[0] - second[1], second[0] - first[1]))
    return float(np.dot(delta, delta))


class _Tree:
    def __init__(self, mesh, cancelled):
        self.triangles = np.ascontiguousarray(mesh.vertices[mesh.faces], dtype=np.float64)
        if not len(self.triangles) or not np.isfinite(self.triangles).all():
            raise Mod3Error("部件没有可分析的有效三角面")
        lows, highs = self.triangles.min(1), self.triangles.max(1)
        centers = (lows + highs) / 2
        self.nodes = []
        def build(indices):
            if cancelled():
                raise Mod3Error("已取消分析")
            index = len(self.nodes)
            low, high = lows[indices].min(0), highs[indices].max(0)
            self.nodes.append(None)
            if len(indices) <= 8:
                self.nodes[index] = (low, high, -1, -1, self.triangles[indices])
            else:
                axis = int(np.argmax(np.ptp(centers[indices], axis=0)))
                middle = len(indices) // 2
                order = indices[np.argpartition(centers[indices, axis], middle)]
                left, right = build(order[:middle]), build(order[middle:])
                self.nodes[index] = (low, high, left, right, None)
            return index
        build(np.arange(len(self.triangles)))


class SurfaceDistances:
    """Cache exact in-range distances only for this immutable analysis workspace."""
    def __init__(self, cancelled=lambda: False):
        self.cancelled, self.trees, self.results = cancelled, {}, {}

    def _tree(self, mesh):
        key = id(mesh)
        if key not in self.trees:
            self.trees[key] = _Tree(mesh, self.cancelled)
        return self.trees[key]

    def distance(self, first, second, limit):
        if self.cancelled():
            raise Mod3Error("已取消分析")
        if not math.isfinite(limit) or limit < 0:
            raise Mod3Error("表面距离范围无效")
        if first is second:
            return 0.
        key = tuple(sorted((id(first), id(second))))
        saved = self.results.get(key)
        if saved is not None:
            previous_limit, value = saved
            if math.isfinite(value) or previous_limit >= limit:
                return value if value <= limit else math.inf
        a, b = self._tree(first), self._tree(second)
        best = np.nextafter(limit * limit, math.inf)
        found = False
        queue = [(_gap_squared(a.nodes[0], b.nodes[0]), 0, 0)]
        while queue:
            if self.cancelled():
                raise Mod3Error("已取消分析")
            bound, ai, bi = heapq.heappop(queue)
            if bound >= best:
                break
            an, bn = a.nodes[ai], b.nodes[bi]
            if an[2] < 0 and bn[2] < 0:
                value = float(_leaf_distance(an[4], bn[4], best))
                if value < best:
                    found, best = True, value
                if best == 0:
                    break
                continue
            split_a = bn[2] < 0 or (an[2] >= 0 and np.max(an[1] - an[0]) >= np.max(bn[1] - bn[0]))
            for child in (an[2:4] if split_a else bn[2:4]):
                ia, ib = (child, bi) if split_a else (ai, child)
                bound = _gap_squared(a.nodes[ia], b.nodes[ib])
                if bound < best:
                    heapq.heappush(queue, (bound, ia, ib))
        value = math.sqrt(max(0., best)) if found else math.inf
        self.results[key] = (limit, value)
        return value if value <= limit + 1e-12 else math.inf
