"""Depth-tested orthographic UV preview; intentionally no game shader simulation."""
import math
from numba import njit


@njit(nogil=True, cache=False)
def draw_colored_faces(polygons, colors, order, face_ids, ghosts, rgb, ids):
    """Painter-order scanlines, without a Python/Pillow call for every face."""
    height, width = ids.shape
    for index in order:
        triangle = polygons[index]
        ax, ay = triangle[0]
        bx, by = triangle[1]
        cx, cy = triangle[2]
        if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) < 1e-9:
            continue
        top = max(0, int(math.ceil(min(ay, by, cy) - .5)))
        bottom = min(height - 1, int(math.floor(max(ay, by, cy) - .5)))
        for y in range(top, bottom + 1):
            py = y + .5
            left, right = math.inf, -math.inf
            for edge in range(3):
                x1, y1 = triangle[edge]
                x2, y2 = triangle[(edge + 1) % 3]
                if (y1 <= py < y2) or (y2 <= py < y1):
                    intersection = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                    left, right = min(left, intersection), max(right, intersection)
            if left > right:
                continue
            start = max(0, int(math.ceil(left - .5)))
            end = min(width - 1, int(math.floor(right - .5)))
            for x in range(start, end + 1):
                for channel in range(3):
                    color = colors[index, channel]
                    rgb[y, x, channel] = (int(rgb[y, x, channel]) * 213 + int(color) * 42 + 127) // 255 if ghosts[index] else color
                ids[y, x] = face_ids[index]


@njit(nogil=True, cache=False)
def draw_selection_mask(polygons, mask):
    height, width = mask.shape
    for triangle in polygons:
        ax, ay = triangle[0]
        bx, by = triangle[1]
        cx, cy = triangle[2]
        if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) < 1e-9:
            continue
        top = max(0, int(math.ceil(min(ay, by, cy) - .5)))
        bottom = min(height - 1, int(math.floor(max(ay, by, cy) - .5)))
        for y in range(top, bottom + 1):
            py = y + .5
            left, right = math.inf, -math.inf
            for edge in range(3):
                x1, y1 = triangle[edge]
                x2, y2 = triangle[(edge + 1) % 3]
                if (y1 <= py < y2) or (y2 <= py < y1):
                    hit = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                    left, right = min(left, hit), max(right, hit)
            if left > right:
                continue
            for x in range(max(0, int(math.ceil(left - .5))), min(width - 1, int(math.floor(right - .5))) + 1):
                mask[y, x] = 255


@njit(nogil=True, cache=False)
def draw_mesh(xy, depths, faces, uvs, texture, rgb, zbuffer, ids, number, transparent, ghost):
    height, width = ids.shape
    th, tw = texture.shape[:2]
    for face in faces:
        a, b, c = face
        ax, ay = xy[a]
        bx, by = xy[b]
        cx, cy = xy[c]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        left = max(0, int(math.floor(min(ax, bx, cx))))
        right = min(width - 1, int(math.ceil(max(ax, bx, cx))))
        top = max(0, int(math.floor(min(ay, by, cy))))
        bottom = min(height - 1, int(math.ceil(max(ay, by, cy))))
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                wa = ((by - cy) * (x + .5 - cx) + (cx - bx) * (y + .5 - cy)) / den
                wb = ((cy - ay) * (x + .5 - cx) + (ax - cx) * (y + .5 - cy)) / den
                wc = 1.0 - wa - wb
                if min(wa, wb, wc) < -1e-6:
                    continue
                z = wa * depths[a] + wb * depths[b] + wc * depths[c]
                if z < zbuffer[y, x]:
                    continue
                u = wa * uvs[a, 0] + wb * uvs[b, 0] + wc * uvs[c, 0]
                v = wa * uvs[a, 1] + wb * uvs[b, 1] + wc * uvs[c, 1]
                tx, ty = int((u - math.floor(u)) * tw) % tw, int((v - math.floor(v)) * th) % th
                if transparent and texture[ty, tx, 3] < 128:
                    continue
                for channel in range(3):
                    color = texture[ty, tx, channel]
                    rgb[y, x, channel] = int(rgb[y, x, channel] * .82 + color * .18) if ghost else color
                zbuffer[y, x] = z
                ids[y, x] = number
