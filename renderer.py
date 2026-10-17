from __future__ import annotations

import math
import numpy as np
from PIL import Image, ImageChops, ImageFilter
from mod3 import MeshPart, ModWorkspace
from materials import material_hash
from health_rules import DEFAULT_THRESHOLDS
from lod_groups import visible_lod

PALETTE = [(71, 133, 255), (255, 145, 77), (112, 196, 127), (187, 120, 255),
           (255, 91, 121), (73, 190, 191), (224, 184, 65), (122, 142, 160)]
LAYER_COLORS = {0: (151, 161, 178), 62: (72, 146, 242), 61: (244, 147, 61), 60: (175, 106, 234)}


def point_in_triangle(px, py, triangle):
    (x1, y1), (x2, y2), (x3, y3) = triangle
    if abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)) < 1e-6:
        return False
    d1 = (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
    d2 = (px - x3) * (y2 - y3) - (x2 - x3) * (py - y3)
    d3 = (px - x1) * (y3 - y1) - (x3 - x1) * (py - y1)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


class SoftwareRenderer:
    def __init__(self):
        self.yaw = self.pitch = 0.0
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.health = 100
        self.health_enabled = True
        self.health_thresholds = DEFAULT_THRESHOLDS.copy()
        self.health_hidden_keys = None
        self.max_triangles_per_mesh = None
        self.selected_keys = set()
        self.comparison_keys = set()
        self.hidden_keys = set()
        self.permanent_hidden_keys = frozenset()
        self.enabled_parts = {"helm", "body", "arm", "wst", "leg", "other"}
        self.active_outfit = None  # None displays every outfit.
        self.lod_bit = 1  # Preview one detail version, plus common geometry.
        self.isolate_selected = False
        self.ghost_others = False
        self.color_mode = "layer"
        self.texture_library = None
        self.texture_alpha = False
        self._pixel_ids = None
        self._pick_scale = (1.0, 1.0)
        self.camera_center = None
        self.camera_span = 1.0
        self._normals = {}
        self._pick_cache = None
        self._depths = np.empty(0, dtype=np.float32)
        self._polygons = np.empty((0, 3, 2), dtype=np.float32)
        self._face_keys = []
        self._face_ids = np.empty(0, dtype=np.int32)
        self._pick_order = np.empty(0, dtype=np.int64)
        self._triangle_depths = np.empty((0, 3), dtype=np.float32)
        self._pick_alpha = {}
        self._through_pick = None
        self.selection_occluded = False

    @property
    def _pick_faces(self):
        if self._pick_cache is None:
            self._pick_cache = [(float(self._depths[index]), *self._face_keys[self._face_ids[index]], self._polygons[index])
                                for index in self._pick_order]
        return self._pick_cache

    @property
    def selected(self):
        return next(iter(sorted(self.selected_keys)), None)

    @selected.setter
    def selected(self, key):
        self.selected_keys = {key} if key else set()

    def visible_for_health(self, mesh: MeshPart):
        threshold = self.health_thresholds.get(mesh.visible_condition)
        return not self.health_enabled or threshold is None or self.health > threshold

    def visible_entries(self, workspace, include_health=True):
        return [(index, document, mesh) for index, (document, mesh) in enumerate(workspace.all_meshes())
                if len(mesh.vertices) and document.equipment_part in self.enabled_parts
                and (self.active_outfit is None or document.outfit_id == self.active_outfit)
                and visible_lod(mesh, self.lod_bit)
                and (document.name, mesh.index) not in self.hidden_keys
                and ((document.name, mesh.index) not in self.permanent_hidden_keys
                     or self.isolate_selected and (document.name, mesh.index) in self.selected_keys)
                and (not self.isolate_selected or (document.name, mesh.index) in self.selected_keys
                     or (document.name, mesh.index) in self.comparison_keys)
                and (self.isolate_selected and (document.name, mesh.index) in self.permanent_hidden_keys
                     or not include_health or not self.health_enabled or
                     ((document.name, mesh.index) not in self.health_hidden_keys if self.health_hidden_keys is not None
                      else self.visible_for_health(mesh)))]

    def fit(self, workspace, keys=None):
        if keys is not None:
            points = [mesh.vertices for document, mesh in workspace.all_meshes()
                      if (document.name, mesh.index) in keys and len(mesh.vertices)
                      and (self.active_outfit is None or document.outfit_id == self.active_outfit)]
        else:
            points = [mesh.vertices for _, _, mesh in self.visible_entries(workspace, include_health=False)]
        if not points:
            return False
        low = np.minimum.reduce([point.min(axis=0) for point in points])
        high = np.maximum.reduce([point.max(axis=0) for point in points])
        self.camera_center = (low + high) / 2
        self.camera_span = max(float(np.max(high - low)), 0.001)
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        return True

    def _rotation(self):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32) @ np.array(
            [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)

    def camera_state(self):
        return {name: (value.copy() if isinstance(value, np.ndarray) else value)
                for name in ("yaw", "pitch", "zoom", "pan_x", "pan_y", "camera_center", "camera_span")
                for value in (getattr(self, name),)}

    def restore_camera(self, state):
        for name, value in state.items():
            setattr(self, name, value.copy() if isinstance(value, np.ndarray) else value)

    def zoom_at(self, factor, size, point=None):
        width, height = (max(1, v) for v in size)
        x, y = point if point is not None else (width / 2, height / 2)
        enlarged = max(.1, min(40., self.zoom * factor))
        ratio = enlarged / self.zoom
        self.pan_x = (x - (x - width * (.5 + self.pan_x)) * ratio) / width - .5
        self.pan_y = (y - (y - height * (.5 + self.pan_y)) * ratio) / height - .5
        self.zoom = enlarged

    def pivot_on(self, workspace, keys, size):
        points = [mesh.vertices for document, mesh in workspace.all_meshes()
                  if (document.name, mesh.index) in keys and len(mesh.vertices)
                  and (self.active_outfit is None or document.outfit_id == self.active_outfit)]
        if not points or self.camera_center is None:
            return False
        center = (np.minimum.reduce([p.min(axis=0) for p in points])
                  + np.maximum.reduce([p.max(axis=0) for p in points])) / 2
        width, height = (max(1, v) for v in size)
        scale = min(width, height) * .84 / self.camera_span * self.zoom
        offset = (center - self.camera_center) @ self._rotation().T
        # Rebase the orbit without moving the current image or changing zoom.
        self.pan_x += float(offset[0]) * scale / width
        self.pan_y -= float(offset[1]) * scale / height
        self.camera_center = center
        return True

    def mesh_color(self, document, mesh, index):
        if self.color_mode == "mesh":
            return PALETTE[index % len(PALETTE)]
        if self.color_mode == "material":
            name = document.material_info(mesh.material_id).name
            return PALETTE[(material_hash(name) if name != "未知" else mesh.material_id) % len(PALETTE)]
        group = mesh.visible_condition
        if group in LAYER_COLORS:
            return LAYER_COLORS[group]
        if group in self.health_thresholds:
            return (92, 171, 128) if group == 63 else PALETTE[group % len(PALETTE)]
        return (230, 86, 88)

    def _face_normals(self, mesh):
        cached = self._normals.get(id(mesh))
        if cached is None or cached[0] is not mesh:
            triangle = mesh.vertices[mesh.faces]
            normals = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
            self._normals[id(mesh)] = (mesh, normals)
            return normals
        return cached[1]

    def render(self, workspace: ModWorkspace, size):
        return self.render_frame(workspace, size)

    def render_frame(self, workspace: ModWorkspace, size, interactive=False):
        size = (max(1, size[0]), max(1, size[1]))
        # Reduce pixels, never triangle count: connected surfaces remain complete.
        factor = min(1.0, 480 / max(size)) if interactive else 1.0
        render_size = tuple(max(1, round(value * factor)) for value in size)
        self._pick_scale = (render_size[0] / size[0], render_size[1] / size[1])
        image = self._render_textures(workspace, render_size) if self.color_mode == "texture" else self._render_colors(workspace, render_size)
        return image.resize(size, Image.Resampling.BILINEAR) if render_size != size else image

    @staticmethod
    def _expand_mask(mask):
        # Separable maximum avoids Pillow's costly general-purpose rank filter.
        horizontal = mask.copy()
        for offset in (1, 2):
            np.maximum(horizontal[:, offset:], mask[:, :-offset], out=horizontal[:, offset:])
            np.maximum(horizontal[:, :-offset], mask[:, offset:], out=horizontal[:, :-offset])
        expanded = horizontal.copy()
        for offset in (1, 2):
            np.maximum(expanded[offset:], horizontal[:-offset], out=expanded[offset:])
            np.maximum(expanded[:-offset], horizontal[offset:], out=expanded[:-offset])
        return expanded

    def _selection_overlay(self, image, pixel_ids, geometry):
        from raster import draw_selection_mask, draw_mesh
        self.selection_occluded = False
        contours = []
        width, height = image.size
        # Each mesh gets its own silhouette, including nested selected members.
        # Cropping bounds the extra work for hundreds of tiny split components.
        for number, polygons, surface in geometry:
            if not len(polygons):
                continue
            xs, ys = polygons[:, :, 0], polygons[:, :, 1]
            left, top = max(0, int(np.floor(xs.min())) - 3), max(0, int(np.floor(ys.min())) - 3)
            right, bottom = min(width, int(np.ceil(xs.max())) + 4), min(height, int(np.ceil(ys.max())) + 4)
            if left >= right or top >= bottom:
                continue
            box = (left, top, right, bottom)
            shape = (bottom - top, right - left)
            mask = np.zeros(shape, np.uint8)
            if surface is not None and self.texture_alpha:
                xy, z, faces, uvs, texture = surface
                ids = np.full(shape, -1, np.int32)
                draw_mesh(xy - [left, top], z, faces, uvs, texture,
                          np.zeros((*shape, 3), np.uint8), np.full(shape, -np.inf, np.float32), ids, number, True, False)
                mask[ids == number] = 255
            else:
                draw_selection_mask(polygons - [left, top], mask)
            visible = (pixel_ids[top:bottom, left:right] == number)
            hidden = (mask != 0) & ~visible
            self.selection_occluded |= bool(hidden.any())
            image.paste((255, 224, 60), box, Image.fromarray(visible.astype(np.uint8) * 24))
            image.paste((255, 156, 65), box, Image.fromarray(hidden.astype(np.uint8) * 25))
            vis = visible.astype(np.uint8) * 255
            vis_expanded = self._expand_mask(vis)
            solid = Image.fromarray(vis_expanded - vis)
            boundary = self._expand_mask(mask) - mask
            occluded = np.where((boundary != 0) & (vis_expanded == 0), 255, 0).astype(np.uint8)
            yy, xx = np.ogrid[top:bottom, left:right]
            occluded[((xx + 2 * yy) // 5) % 2 != 0] = 0
            contours.append((box, solid, Image.fromarray(occluded)))
        for box, solid, hidden in contours:
            image.paste((255, 224, 60), box, solid)
            image.paste((255, 156, 65), box, hidden)
        return image

    def _render_colors(self, workspace: ModWorkspace, size):
        from raster import draw_colored_faces
        self._pixel_ids = None
        width, height = max(1, size[0]), max(1, size[1])
        image = Image.new("RGB", (width, height), (28, 31, 38))
        entries = self.visible_entries(workspace)
        self._pick_cache = None
        self._depths = np.empty(0, dtype=np.float32)
        self._polygons = np.empty((0, 3, 2), dtype=np.float32)
        self._face_ids = np.empty(0, dtype=np.int32)
        self._pick_order = np.empty(0, dtype=np.int64)
        self._face_keys = []
        self._triangle_depths = np.empty((0, 3), np.float32)
        self._pick_alpha = {}
        self.selection_occluded = False
        if not entries:
            return image
        if self.camera_center is None:
            self.fit(workspace)
        scale = min(width, height) * 0.84 / self.camera_span * self.zoom
        rotation = self._rotation()
        polygons, depths, colors, ids, ghost_flags = [], [], [], [], []
        triangle_depths, selected_geometry = [], []
        light = np.array([0.3, 0.5, 0.8], dtype=np.float32)
        light /= np.linalg.norm(light)
        for number, (color_index, document, mesh) in enumerate(entries):
            transformed = (mesh.vertices - self.camera_center) @ rotation.T
            projected = np.column_stack((width * (0.5 + self.pan_x) + transformed[:, 0] * scale,
                                         height * (0.5 + self.pan_y) - transformed[:, 1] * scale))
            face_indices = np.arange(len(mesh.faces))
            if self.max_triangles_per_mesh and len(face_indices) > self.max_triangles_per_mesh:
                face_indices = np.linspace(0, len(face_indices) - 1, self.max_triangles_per_mesh, dtype=int)
            faces = mesh.faces[face_indices]
            projected_faces = projected[faces]
            # Most fitted meshes are wholly on screen. Avoid four reductions over
            # every triangle in that common path; clipped meshes use vector bounds.
            if (projected[:, 0].min() >= 0 and projected[:, 0].max() < width
                    and projected[:, 1].min() >= 0 and projected[:, 1].max() < height):
                valid = np.ones(len(faces), dtype=bool)
            else:
                a, b, c = projected_faces[:, 0], projected_faces[:, 1], projected_faces[:, 2]
                low = np.minimum(np.minimum(a, b), c)
                high = np.maximum(np.maximum(a, b), c)
                valid = (high[:, 0] >= 0) & (low[:, 0] < width) & (high[:, 1] >= 0) & (low[:, 1] < height)
            polygons.append(projected_faces[valid])
            face_depths = transformed[:, 2][faces][valid]
            triangle_depths.append(face_depths)
            depths.append(face_depths.mean(axis=1))
            brightness = 0.42 + 0.58 * np.abs(self._face_normals(mesh)[face_indices] @ (rotation.T @ light))
            color = np.array(self.mesh_color(document, mesh, color_index))
            colors.append((brightness[valid, None] * color).astype(np.uint8))
            count = int(valid.sum())
            key = (document.name, mesh.index)
            self._face_keys.append(key)
            if key in self.selected_keys:
                selected_geometry.append((number, projected_faces[valid], None))
            ids.append(np.full(count, number, dtype=np.int32))
            ghost_flags.append(np.full(count, self.ghost_others and bool(self.selected_keys)
                                       and key not in self.selected_keys, dtype=bool))
        polygon = np.concatenate(polygons)
        depth = np.concatenate(depths)
        color = np.concatenate(colors)
        face_ids = np.concatenate(ids)
        ghost = np.concatenate(ghost_flags)
        order = np.lexsort((depth, ~ghost)) if ghost.any() else np.argsort(depth, kind="stable")
        rgb = np.empty((height, width, 3), dtype=np.uint8)
        rgb[:] = (28, 31, 38)
        pixel_ids = np.full((height, width), -1, dtype=np.int32)
        draw_colored_faces(polygon, color, order, face_ids, ghost, rgb, pixel_ids)
        image = Image.fromarray(rgb)
        self._selection_overlay(image, pixel_ids, selected_geometry)
        self._polygons, self._face_ids, self._pick_order = polygon, face_ids, order
        self._depths = depth
        self._triangle_depths = np.concatenate(triangle_depths)
        self._pixel_ids = pixel_ids
        return image

    def _render_textures(self, workspace, size):
        from raster import draw_mesh
        width, height = max(1, size[0]), max(1, size[1])
        rgb = np.empty((height, width, 3), dtype=np.uint8)
        rgb[:] = (28, 31, 38)
        ids = np.full((height, width), -1, dtype=np.int32)
        zbuffer = np.full((height, width), -np.inf, dtype=np.float32)
        self._pick_cache = None
        self._face_keys = []
        self._depths = np.empty(0, np.float32)
        self._face_ids = np.empty(0, np.int32)
        self._pick_order = np.empty(0, np.int64)
        self._pick_alpha = {}
        entries = self.visible_entries(workspace)
        if self.camera_center is None and entries:
            self.fit(workspace)
        rotation = self._rotation()
        scale = min(width, height) * .84 / self.camera_span * self.zoom
        polygons, prepared, triangle_depths, face_ids, selected_geometry = [], [], [], [], []
        start = 0
        for number, (color_index, document, mesh) in enumerate(entries):
            transformed = (mesh.vertices - self.camera_center) @ rotation.T
            xy = np.ascontiguousarray(np.column_stack((width * (.5 + self.pan_x) + transformed[:, 0] * scale,
                                                      height * (.5 + self.pan_y) - transformed[:, 1] * scale)), dtype=np.float32)
            polygons.append(xy[mesh.faces])
            key = (document.name, mesh.index)
            self._face_keys.append(key)
            texture = self.texture_library.get(document, mesh.material_id) if self.texture_library else None
            if texture is None or mesh.uvs is None:
                texture = np.array([[(*self.mesh_color(document, mesh, color_index), 255)]], np.uint8)
                uvs = np.zeros((len(mesh.vertices), 2), np.float32)
            else:
                uvs = mesh.uvs
            ghost = self.ghost_others and bool(self.selected_keys) and key not in self.selected_keys
            prepared.append((xy, np.ascontiguousarray(transformed[:, 2]), mesh.faces, uvs, texture, number, ghost))
            triangle_depths.append(transformed[:, 2][mesh.faces])
            face_ids.append(np.full(len(mesh.faces), number, np.int32))
            if self.texture_alpha:
                self._pick_alpha[number] = (start, mesh.faces, uvs, texture)
            start += len(mesh.faces)
            if key in self.selected_keys:
                selected_geometry.append((number, xy[mesh.faces], (xy, transformed[:, 2], mesh.faces, uvs, texture)))
        # Ghosts provide context behind the selected geometry, matching the plain preview.
        for phase in (True, False):
            zbuffer[:] = -np.inf
            for xy, z, faces, uvs, texture, number, ghost in prepared:
                if ghost == phase:
                    draw_mesh(xy, z, faces, uvs, texture, rgb, zbuffer, ids, number, self.texture_alpha, ghost)
        image = Image.fromarray(rgb)
        self._selection_overlay(image, ids, selected_geometry)
        self._polygons = np.concatenate(polygons) if polygons else np.empty((0, 3, 2), np.float32)
        self._face_ids = np.concatenate(face_ids) if face_ids else np.empty(0, np.int32)
        self._triangle_depths = np.concatenate(triangle_depths) if triangle_depths else np.empty((0, 3), np.float32)
        self._depths = self._triangle_depths.mean(axis=1)
        self._pick_order = np.argsort(self._depths, kind="stable")
        self._pixel_ids = ids
        return image

    def pick_stack(self, x, y):
        px, py = x * self._pick_scale[0], y * self._pick_scale[1]
        if self._pixel_ids is None or not (0 <= py < self._pixel_ids.shape[0] and 0 <= px < self._pixel_ids.shape[1]):
            return []
        # Match the raster's pixel center, including low-resolution interaction.
        px, py = int(px) + .5, int(py) + .5
        polygons = self._polygons
        if not len(polygons):
            return []
        low, high = polygons.min(axis=1), polygons.max(axis=1)
        candidates = np.flatnonzero((low[:, 0] <= px) & (high[:, 0] >= px) & (low[:, 1] <= py) & (high[:, 1] >= py))
        hits = {}
        for index in candidates:
            (ax, ay), (bx, by), (cx, cy) = polygons[index]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-9:
                continue
            wa = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
            wb = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
            wc = 1 - wa - wb
            if min(wa, wb, wc) < -1e-6:
                continue
            number = int(self._face_ids[index])
            if number in self._pick_alpha:
                start, faces, uvs, texture = self._pick_alpha[number]
                uv = np.array([wa, wb, wc]) @ uvs[faces[index - start]]
                th, tw = texture.shape[:2]
                if texture[int((uv[1] % 1) * th) % th, int((uv[0] % 1) * tw) % tw, 3] < 128:
                    continue
            key = self._face_keys[number]
            depth = float(np.array([wa, wb, wc]) @ self._triangle_depths[index])
            hits[key] = max(hits.get(key, -np.inf), depth)
        # Presentation order can bring a selected inner mesh in front of ghosted
        # context. Picking must use geometric depth, independent of selection.
        rank = {key: number for number, key in enumerate(self._face_keys)}
        return sorted(hits, key=lambda key: (hits[key], rank[key]), reverse=True)

    def pick_through(self, x, y):
        stack = self.pick_stack(x, y)
        if not stack:
            self._through_pick = None
            return None
        camera = (self.yaw, self.pitch, self.zoom, self.pan_x, self.pan_y, self.camera_span,
                  tuple(self.camera_center) if self.camera_center is not None else None)
        previous = self._through_pick
        index = ((previous[3] + 1) % len(stack) if previous and previous[0] == (tuple(stack), camera)
                 and abs(previous[1] - x) <= 4 and abs(previous[2] - y) <= 4 else (1 if len(stack) > 1 else 0))
        self._through_pick = ((tuple(stack), camera), x, y, index)
        return stack[index]

    def pick(self, x, y):
        stack = self.pick_stack(x, y)
        return stack[0] if stack else None
