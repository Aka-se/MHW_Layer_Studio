import unittest
import numpy as np

from mod3 import Mod3Document, ModWorkspace
from renderer import SoftwareRenderer
from test_core import minimal_mod3


def nested_model():
    workspace = ModWorkspace()
    inner = Mod3Document("inner.mod3", minimal_mod3())
    outer = Mod3Document("outer.mod3", minimal_mod3())
    inner.meshes[0].vertices[:, 2] = -1
    outer.meshes[0].vertices *= 2
    outer.meshes[0].vertices[:, 2] = 1
    workspace.documents = [inner, outer]
    return workspace, (inner.name, 0), (outer.name, 0)


class SelectionFeedbackTests(unittest.TestCase):
    def test_ghost_selection_does_not_capture_clicks_from_the_outer_surface(self):
        workspace, inner, outer = nested_model()
        for mode in ("layer", "texture"):
            for interactive in (False, True):
                for selected in ({inner}, {outer}, {inner, outer}):
                    r = SoftwareRenderer()
                    r.color_mode = mode
                    r.ghost_others = True
                    r.selected_keys = selected
                    r.render_frame(workspace, (640, 480), interactive)
                    self.assertEqual(outer, r.pick(320, 240), (mode, interactive, selected))
                    self.assertEqual([outer, inner], r.pick_stack(320, 240))
                    self.assertEqual(inner, r.pick_through(320, 240))
                    self.assertEqual(outer, r.pick_through(320, 240))

    def test_fully_occluded_selected_mesh_still_has_feedback(self):
        workspace, inner, outer = nested_model()
        for mode in ("layer", "texture"):
            r = SoftwareRenderer()
            r.color_mode = mode
            plain = np.asarray(r.render(workspace, (320, 240)))
            self.assertEqual(outer, r.pick(160, 120))
            r.selected_keys = {inner}
            selected = np.asarray(r.render(workspace, (320, 240)))
            self.assertGreater(np.count_nonzero(np.any(plain != selected, axis=2)), 100,
                               "Selection feedback must survive complete occlusion")
            self.assertTrue(r.selection_occluded)
            self.assertEqual(outer, r.pick(160, 120))

    def test_pick_uses_depth_at_cursor_instead_of_triangle_average(self):
        workspace, sloping, flat = nested_model()
        a, b = [d.meshes[0] for d in workspace.documents]
        a.vertices = np.array([[-1, -1, -10], [1, -1, -10], [0, 1, 20]], np.float32)
        b.vertices = np.array([[-1, -1, 1], [1, -1, 1], [0, 1, 1]], np.float32)
        for mode in ("layer", "texture"):
            r = SoftwareRenderer()
            r.color_mode = mode
            r.camera_center, r.camera_span = np.zeros(3), 2
            r.render(workspace, (320, 240))
            self.assertEqual(sloping, r.pick(160, 70))
            self.assertEqual(flat, r.pick(160, 180))

    def test_nested_members_keep_individual_contours_and_cycle_without_duplicates(self):
        workspace, inner, outer = nested_model()
        for mode in ("layer", "texture"):
            for interactive in (False, True):
                r = SoftwareRenderer()
                r.color_mode = mode
                r.selected_keys = {outer}
                outside = np.asarray(r.render_frame(workspace, (640, 480), interactive))
                r.selected_keys = {inner, outer}
                both = np.asarray(r.render_frame(workspace, (640, 480), interactive))
                self.assertGreater(np.count_nonzero(np.any(outside != both, axis=2)), 100)
                self.assertEqual([outer, inner], r.pick_stack(320, 240))
                self.assertEqual(inner, r.pick_through(320, 240))
                self.assertEqual(outer, r.pick_through(320, 240))
                self.assertEqual(inner, r.pick_through(320, 240))
                self.assertEqual(outer, r.pick(320, 240))
                self.assertEqual([], r.pick_stack(-1, 240))

    def test_hidden_by_health_or_manual_control_is_not_xrayed_or_picked(self):
        workspace, inner, outer = nested_model()
        document = workspace.documents[0]
        document.set_visible_condition(0, 60)
        for mode in ("layer", "texture"):
            r = SoftwareRenderer()
            r.color_mode = mode
            r.health = 50
            baseline = np.asarray(r.render(workspace, (320, 240)))
            r.selected_keys = {inner}
            selected = np.asarray(r.render(workspace, (320, 240)))
            np.testing.assert_array_equal(baseline, selected)
            self.assertFalse(r.selection_occluded)
            self.assertEqual([outer], r.pick_stack(160, 120))
            r.health = 100
            r.hidden_keys = {inner}
            np.testing.assert_array_equal(baseline, np.asarray(r.render(workspace, (320, 240))))
            self.assertEqual([outer], r.pick_stack(160, 120))

    def test_transparent_material_does_not_create_a_false_hidden_selection(self):
        from types import SimpleNamespace
        workspace, inner, outer = nested_model()
        r = SoftwareRenderer()
        r.color_mode = "texture"
        r.texture_alpha = True
        r.selected_keys = {outer}
        for d in workspace.documents:
            d.meshes[0].uvs = np.zeros((3, 2), np.float32)
        invisible = np.array([[[200, 100, 100, 0]]], np.uint8)
        opaque = np.array([[[100, 100, 200, 255]]], np.uint8)
        r.texture_library = SimpleNamespace(get=lambda d, _: invisible if d.name == outer[0] else opaque)
        selected = np.asarray(r.render(workspace, (320, 240)))
        self.assertEqual([inner], r.pick_stack(160, 120))
        self.assertFalse(r.selection_occluded)
        r.selected_keys.clear()
        np.testing.assert_array_equal(selected, np.asarray(r.render(workspace, (320, 240))))


if __name__ == "__main__":
    unittest.main()
