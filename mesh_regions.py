"""Position hints and stable top-to-bottom ordering; never infer garment identity."""
import numpy as np


def bounds(mesh):
    cached = getattr(mesh, "_editor_position_bounds", None)
    if cached is None:
        used = np.unique(mesh.faces) if len(mesh.faces) else np.arange(len(mesh.vertices))
        points = mesh.vertices[used]
        points = points[np.isfinite(points).all(1)]
        cached = (points.min(0), points.max(0)) if len(points) else (np.zeros(3), np.zeros(3))
        mesh._editor_position_bounds = cached
    return cached


def outfit_frames(workspace):
    frames = {}
    for d in workspace.documents:
        for m in d.meshes:
            if not len(m.vertices):
                continue
            lo, hi = bounds(m)
            old = frames.get(d.outfit_id)
            frames[d.outfit_id] = (np.minimum(old[0], lo), np.maximum(old[1], hi)) if old else (lo, hi)
    return frames


def position_hint(document, mesh, frame):
    lo, hi = bounds(mesh)
    bottom, top = frame
    height = max(float(top[1] - bottom[1]), .001)
    center = (lo + hi) / 2
    y = float((center[1] - bottom[1]) / height)
    lateral = abs(float(center[0] - (bottom[0] + top[0]) / 2)) / height
    if (hi[1] - lo[1]) / height > .48:
        return "跨部位"
    if y >= .86:
        return "头部附近"
    if y >= .80 and lateral < .12:
        return "颈肩附近"
    if .40 <= y < .86 and lateral >= .23:
        return "手部附近"
    if .50 <= y < .86 and lateral >= .15:
        return "手臂附近"
    if y < .16:
        return "脚部附近"
    if y < .40:
        return "小腿附近"
    if y < .50:
        return "大腿附近"
    if y < .59:
        return "腰部附近"
    return "躯干附近"


def member_order(workspace, keys):
    lookup = {d.name: d for d in workspace.documents}
    def order(key):
        d = lookup[key[0]]
        lo, hi = bounds(d.meshes[key[1]])
        return (d.outfit_id, -float((lo[1] + hi[1]) / 2), key[0], key[1])
    return sorted(keys, key=order)


def group_order(workspace, records):
    lookup = {d.name: d for d in workspace.documents}
    def order(record):
        name, keys = record
        if not keys:
            return ("\uffff", float("inf"), name or "")
        # A group's height is its combined bounding box midpoint, not fragment count.
        extents = [bounds(lookup[n].meshes[i]) for n, i in keys]
        y = (min(float(lo[1]) for lo, hi in extents) + max(float(hi[1]) for lo, hi in extents)) / 2
        return (min(lookup[n].outfit_id for n, i in keys), -y, name or "")
    return sorted(records, key=order)
