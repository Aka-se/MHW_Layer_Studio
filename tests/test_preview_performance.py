"""Complete surfaces, accurate picking and bounded interactive resolution."""
import unittest

import numpy as np
from mod3 import Mod3Document, ModWorkspace
from renderer import SoftwareRenderer
from raster import draw_colored_faces
from test_core import minimal_mod3


class PreviewTests(unittest.TestCase):
    def test_cursor_zoom_and_limits_keep_the_same_point_in_place(self):
        r = SoftwareRenderer()
        r.camera_center = np.array([1., 2., 3.])
        r.camera_span = 12
        r.yaw, r.pitch = .4, -.2
        r.pan_x, r.pan_y = .15, -.12
        size = (960, 600)
        point = np.array([2., 3., 4.])
        def screen():
            p = (point - r.camera_center) @ r._rotation().T
            scale = min(size) * .84 / r.camera_span * r.zoom
            return np.array([size[0] * (.5 + r.pan_x) + p[0] * scale,
                             size[1] * (.5 + r.pan_y) - p[1] * scale])
        anchor = screen()
        for factor in (1.1, .9, 10000, 2, .000001, .5):
            r.zoom_at(factor, size, anchor)
            np.testing.assert_allclose(anchor, screen(), atol=1e-8)
            self.assertTrue(.1 <= r.zoom <= 40)

    def test_pivot_rebase_does_not_move_image_and_orbits_in_place(self):
        workspace = ModWorkspace()
        d = Mod3Document("body.mod3", minimal_mod3())
        workspace.documents = [d]
        r = SoftwareRenderer()
        r.fit(workspace)
        r.camera_center = r.camera_center + [2., 3., 4.]
        r.yaw, r.pitch, r.zoom = .6, -.3, 1.7
        r.pan_x, r.pan_y = .17, -.1
        def screen(points):
            p = (points - r.camera_center) @ r._rotation().T
            scale = 600 * .84 / r.camera_span * r.zoom
            return p[:, :2] * [scale, -scale] + [960 * (.5 + r.pan_x), 600 * (.5 + r.pan_y)]
        before = screen(d.meshes[0].vertices)
        self.assertTrue(r.pivot_on(workspace, {(d.name, 0)}, (960, 600)))
        np.testing.assert_allclose(before, screen(d.meshes[0].vertices), atol=1e-5)
        pivot_pixel = screen(r.camera_center[None, :])
        state = r.camera_state()
        r.yaw += .7
        r.pitch += .2
        np.testing.assert_allclose(pivot_pixel, screen(r.camera_center[None, :]), atol=1e-6)
        r.camera_center += 10
        r.restore_camera(state)
        np.testing.assert_allclose(before, screen(d.meshes[0].vertices), atol=1e-5)
        self.assertFalse(r.pivot_on(workspace, set(), (960, 600)))

    def test_scanlines_have_no_holes_on_shared_edges_or_reversed_winding(self):
        polygons = np.array([[[0, 0], [16, 0], [16, 16]], [[0, 0], [16, 16], [0, 16]]], np.float64)
        for faces in (polygons, polygons[:, ::-1].copy()):
            rgb = np.zeros((16, 16, 3), np.uint8)
            ids = np.full((16, 16), -1, np.int32)
            draw_colored_faces(faces, np.full((2, 3), 120, np.uint8), np.arange(2), np.array([3, 3], np.int32), np.zeros(2, bool), rgb, ids)
            self.assertTrue(np.all(ids == 3))
            self.assertTrue(np.all(rgb == 120))

    def test_scanlines_clip_huge_offscreen_and_degenerate_triangles(self):
        polygons = np.array([[[-1e5, -1e5], [1e5, -1e5], [0, 1e5]], [[0, 0], [0, 0], [0, 0]], [[100, 100], [110, 100], [105, 110]]], np.float64)
        rgb = np.zeros((16, 16, 3), np.uint8)
        ids = np.full((16, 16), -1, np.int32)
        draw_colored_faces(polygons, np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], np.uint8), np.arange(3), np.arange(3, dtype=np.int32), np.zeros(3, bool), rgb, ids)
        self.assertTrue(np.all(ids == 0))
        self.assertTrue(np.all(rgb == [255, 0, 0]))

    def test_interactive_keeps_faces_and_pick_coordinates_then_restores_resolution(self):
        workspace = ModWorkspace()
        document = Mod3Document("body.mod3", minimal_mod3())
        workspace.documents = [document]
        original_vertices = document.meshes[0].vertices.copy()
        original_faces = document.meshes[0].faces.copy()
        renderer = SoftwareRenderer()
        for mode in ("layer", "texture"):
            renderer.color_mode = mode
            renderer.selected_keys = {(document.name, 0)}
            image = renderer.render_frame(workspace, (1200, 900), interactive=True)
            self.assertEqual((1200, 900), image.size)
            self.assertEqual((360, 480), renderer._pixel_ids.shape)
            self.assertEqual((document.name, 0), renderer.pick(600, 450))
            renderer._pixel_ids[:] = 0
            self.assertIsNone(renderer.pick(-1, 450))
            self.assertIsNone(renderer.pick(1200, 450))
            self.assertEqual(1, len(renderer._polygons))
            renderer.render_frame(workspace, (1200, 900))
            self.assertEqual((900, 1200), renderer._pixel_ids.shape)
            self.assertEqual((document.name, 0), renderer.pick(600, 450))
        np.testing.assert_array_equal(original_vertices, document.meshes[0].vertices)
        np.testing.assert_array_equal(original_faces, document.meshes[0].faces)

    def test_no_faces_removes_previous_interactive_pick(self):
        workspace = ModWorkspace()
        workspace.documents = [Mod3Document("body.mod3", minimal_mod3())]
        renderer = SoftwareRenderer()
        renderer.render_frame(workspace, (1200, 900), interactive=True)
        renderer.enabled_parts = set()
        image = renderer.render_frame(workspace, (1200, 900), interactive=True)
        self.assertEqual((1200, 900), image.size)
        self.assertIsNone(renderer.pick(600, 450))


if __name__ == "__main__":
    unittest.main()
