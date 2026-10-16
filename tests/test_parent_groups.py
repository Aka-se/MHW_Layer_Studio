import json
import tempfile
import unittest
from pathlib import Path

from mod3 import ModWorkspace, Mod3Error
from session import EditorSession

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
class ParentGroupTests(unittest.TestCase):
    def setUp(self):
        self.s = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        self.d = next(d for d in self.s.workspace.documents if d.equipment_part == "wst")
        self.s.split_mesh(self.d.name, 1)
        self.left = {(self.d.name, i) for i in (11, 12, 13, 18, 25, 27)}
        self.right = {(self.d.name, i) for i in (14, 15, 16, 19, 26, 28)}
        self.s.edit_named_group("create", "左侧", self.left)
        self.s.edit_named_group("create", "右侧", self.right)
        self.s.edit_parent_group("create", "腿部装饰", ["左侧", "右侧"])

    def test_parent_union_and_independent_layers_without_duplicate_ownership(self):
        s = self.s
        original = self.d.bytes()
        self.assertEqual(self.left | self.right, s.group_members("腿部装饰"))
        self.assertEqual(12, sum(len(k) for k in s.named_groups.values()))
        s.apply_group(s.group_members("腿部装饰"), 60)
        s.apply_group(s.group_members("左侧"), 62)
        self.assertEqual({62}, {self.d.meshes[i].visible_condition for n, i in self.left})
        self.assertEqual({60}, {self.d.meshes[i].visible_condition for n, i in self.right})
        s.undo()
        self.assertEqual({60}, {self.d.meshes[i].visible_condition for n, i in s.group_members("腿部装饰")})
        s.undo()
        self.assertEqual(original, self.d.bytes())
        s.redo()
        s.redo()
        s.validate_patch()

    def test_rename_dissolve_and_member_moves_keep_references_with_undo(self):
        s = self.s
        s.edit_named_group("rename", "左侧", new_name="左腿")
        self.assertEqual(["左腿", "右侧"], s.parent_groups["腿部装饰"])
        s.undo()
        self.assertEqual(["左侧", "右侧"], s.parent_groups["腿部装饰"])
        s.redo()
        key = next(iter(self.left))
        s.edit_named_group("add", "右侧", {key})
        self.assertEqual(self.left | self.right, s.group_members("腿部装饰"))
        self.assertNotIn(key, s.named_groups["左腿"])
        s.edit_named_group("rename", "腿部装饰", new_name="完整腿饰")
        s.edit_named_group("delete", "完整腿饰")
        self.assertFalse(s.parent_groups)
        self.assertEqual({"左腿", "右侧"}, set(s.named_groups))
        s.undo()
        self.assertEqual(["左腿", "右侧"], s.parent_groups["完整腿饰"])
        s.edit_named_group("delete", "左腿")
        self.assertEqual(["右侧"], s.parent_groups["完整腿饰"])
        s.undo()
        self.assertEqual(["左腿", "右侧"], s.parent_groups["完整腿饰"])

    def test_invalid_hierarchy_is_atomic_and_parent_cannot_take_raw_members(self):
        s = self.s
        state, parents, history = s._groups(), s._parents(), len(s._undo)
        for data in ({"父组": ["不存在"]}, {"左侧": ["右侧"]},
                     {"A": ["左侧"], "B": ["左侧"]}, {"A": ["B"], "B": ["A"]}):
            with self.assertRaises(Mod3Error):
                s._restore_tags({"named_groups": [{"name": n, "members": [list(k) for k in keys]}
                                                 for n, keys in s.named_groups.items()], "parent_groups": data})
            self.assertEqual(state, s._groups())
        for action in ("add", "remove"):
            with self.assertRaises(Mod3Error):
                s.edit_named_group(action, "腿部装饰", self.left)
        with self.assertRaises(Mod3Error):
            s.edit_parent_group("create", "另一个父组", ["左侧", "右侧"])
        self.assertEqual(parents, s.parent_groups)
        self.assertEqual(history, len(s._undo))

    def test_project_zip_folder_and_legacy_flat_projects(self):
        s = self.s
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project = tmp / "parent.json"
            s.save(project)
            self.assertFalse(s.project_metadata_dirty)
            opened, _ = EditorSession.open(project)
            self.assertEqual(s.parent_groups, opened.parent_groups)
            self.assertEqual(s.named_groups, opened.named_groups)
            for destination in (tmp / "parent.zip", tmp / "folder"):
                s.export(destination)
                restored = EditorSession(ModWorkspace.open(destination))
                self.assertEqual(s.parent_groups, restored.parent_groups)
                self.assertEqual(s.named_groups, restored.named_groups)
                self.assertEqual(self.d.bytes(), restored.document(self.d.name).bytes())
            data = json.loads(project.read_text(encoding="utf-8"))
            data.pop("parent_groups")
            data["version"] = 9
            project.write_text(json.dumps(data), encoding="utf-8")
            flat, _ = EditorSession.open(project)
            self.assertFalse(flat.parent_groups)
            self.assertEqual(s.named_groups, flat.named_groups)
            s.edit_parent_group("rename", "腿部装饰", new_name="新父组")
            self.assertTrue(s.named_groups_dirty)
            s.undo()
            self.assertFalse(s.named_groups_dirty)

    def test_reanalysis_preserves_hierarchy_until_apply_and_undo_restores_it(self):
        s = self.s
        before, parents, data = s._groups(), s._parents(), self.d.bytes()
        plan = s.prepare_attachment_review(self.left | self.right)
        self.assertEqual(parents, plan["staged"].parent_groups)
        self.assertEqual(before, s._groups())

        group = next(g for g in plan["groups"] if g["name"] == "左侧")
        group["accepted"] = True
        s.apply_spatial_groups(plan)
        self.assertEqual(parents, s.parent_groups)
        new = s.prepare_spatial_groups(self.left | self.right, split=False, include_grouped=True, distance="常规")
        s.apply_spatial_groups(new)
        self.assertEqual(1, len(s.parent_groups))
        self.assertEqual(self.left | self.right, next(iter(s.all_groups()[n] for n in s.parent_groups)))
        self.assertEqual(data, self.d.bytes())
        s.undo()
        self.assertEqual(parents, s.parent_groups)
        self.assertEqual(before, s._groups())

    def test_single_mod3_sidecar_remaps_members_and_preserves_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "waist.mod3"
            source.write_bytes(self.d.bytes())
            single = EditorSession(ModWorkspace.open(source))
            name = single.workspace.documents[0].name
            single.edit_named_group("create", "左", {(name, 12)})
            single.edit_named_group("create", "右", {(name, 15)})
            single.edit_parent_group("create", "腿饰", ["左", "右"])
            single.export(tmp / "renamed.mod3")
            restored = EditorSession(ModWorkspace.open(tmp / "renamed.mod3"))
            self.assertEqual(single.parent_groups, restored.parent_groups)
            self.assertEqual({("renamed.mod3", 12), ("renamed.mod3", 15)}, restored.group_members("腿饰"))


if __name__ == "__main__":
    unittest.main()
