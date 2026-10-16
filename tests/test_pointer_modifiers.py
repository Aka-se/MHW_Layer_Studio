"""Windows lock keys must never activate through-selection or bypass groups."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app import LayerStudio
from spatial_dialog import SpatialGroupDialog


@unittest.skipUnless(sys.platform == "win32", "Windows Tk modifier regression")
class WindowsPointerModifiersTests(unittest.TestCase):
    outer = ("coat.mod3", 0)
    inner = ("body.mod3", 0)

    def renderer(self):
        return SimpleNamespace(pick=Mock(return_value=self.outer),
                               pick_through=Mock(return_value=self.inner), _through_pick=None)

    def test_lock_keys_keep_normal_click_on_surface_and_select_its_group(self):
        for locks in (0, 0x8, 0x2, 0x20, 0x2a):  # Num / Caps / Scroll lock.
            for additive in (0, 0x1, 0x4):
                with self.subTest(locks=locks, additive=additive):
                    app = SimpleNamespace(drag_start=(160, 120), dragged=False, renderer=self.renderer(),
                                          _select_by_key=Mock(), status=Mock())
                    LayerStudio._mouse_up(app, SimpleNamespace(x=160, y=120, state=locks | additive))
                    app._select_by_key.assert_called_once_with(self.outer, additive=bool(additive), select_group=True)
                    app.renderer.pick_through.assert_not_called()

    def test_explicit_alt_still_selects_inside_with_num_lock_on_or_off(self):
        for locks in (0, 0x8, 0x2a):
            app = SimpleNamespace(drag_start=(160, 120), dragged=False, renderer=self.renderer(),
                                  _select_by_key=Mock(), status=Mock())
            LayerStudio._mouse_up(app, SimpleNamespace(x=160, y=120, state=locks | 0x20000))
            app._select_by_key.assert_called_once_with(self.inner, additive=False, select_group=False)
            app.renderer.pick.assert_not_called()

    def test_legacy_project_cannot_disable_group_first_selection(self):
        app = SimpleNamespace(drag_start=(160, 120), dragged=False, renderer=self.renderer(),
                              _select_by_key=Mock(), status=Mock())
        LayerStudio._mouse_up(app, SimpleNamespace(x=160, y=120, state=0))
        app._select_by_key.assert_called_once_with(self.outer, additive=False, select_group=True)

    def test_split_preview_lock_keys_do_not_pick_inside(self):
        for state, expected in ((0x8, self.outer), (0x2a, self.outer), (0x20008, self.inner)):
            dialog = SimpleNamespace(drag_start=(160, 120), dragged=False, renderer=self.renderer(),
                                     plan={"groups": [{"members": [self.outer, self.inner]}]},
                                     tree=Mock(), selected_keys=lambda: set(), select=Mock())
            SpatialGroupDialog.mouse_up(dialog, SimpleNamespace(x=160, y=120, state=state))
            row = "m:0:0" if expected == self.outer else "m:0:1"
            dialog.tree.selection_set.assert_called_once_with([row])

    def test_num_lock_does_not_disable_space_temporary_colors(self):
        app = SimpleNamespace(workspace=object(), _temporary_mesh_colors=False, schedule_redraw=Mock())
        LayerStudio._temporary_color_press(app, SimpleNamespace(state=0x8))
        self.assertTrue(app._temporary_mesh_colors)
        dialog = SimpleNamespace(plan=object(), temporary_mesh_colors=False, request_frame=Mock())
        SpatialGroupDialog.temporary_color_press(dialog, SimpleNamespace(state=0x8))
        self.assertTrue(dialog.temporary_mesh_colors)


if __name__ == "__main__":
    unittest.main()
