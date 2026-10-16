import json
import tempfile
import unittest
from pathlib import Path

from mod3 import ModWorkspace, Mod3Error
from session import EditorSession
from test_editor import workspace_fixture

ROOT = Path(__file__).resolve().parents[1]


class NamedGroupTests(unittest.TestCase):
    def test_membership_transfer_history_and_group_ids_are_independent(self):
        session = EditorSession(workspace_fixture())
        a, b = [(d.name, 0) for d in session.workspace.documents]
        original = [d.bytes() for d in session.workspace.documents]
        session.edit_named_group("create", "鞋子", {a, b})
        self.assertTrue(session.named_groups_dirty)
        self.assertEqual(original, [d.bytes() for d in session.workspace.documents])
        session.edit_named_group("create", "外套", {a})
        self.assertEqual({b}, session.named_groups["鞋子"])
        self.assertEqual({a}, session.named_groups["外套"])
        session.edit_named_group("rename", "外套", new_name="上衣")
        with self.assertRaises(Mod3Error):
            session.edit_named_group("rename", "上衣", new_name="鞋子")
        session.undo()
        self.assertIn("外套", session.named_groups)
        session.redo()
        self.assertIn("上衣", session.named_groups)
        session.edit_named_group("add", "鞋子", {a})
        session.apply_group(session.named_groups["鞋子"], 61)
        session.edit_named_group("remove", "鞋子", {a})
        session.edit_named_group("delete", "鞋子")
        self.assertTrue(all(d.meshes[0].visible_condition == 61 for d in session.workspace.documents))
        session.undo()
        self.assertEqual({b}, session.named_groups["鞋子"])
        session.undo()
        self.assertEqual({a, b}, session.named_groups["鞋子"])
        session.undo()
        self.assertTrue(all(d.meshes[0].visible_condition == 0 for d in session.workspace.documents))

    @unittest.skipUnless((ROOT / "Kuroki-Defult.zip").exists(), "optional local integration sample not available")
    def test_split_remaps_members_and_project_round_trip(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-Defult.zip"))
        document = next(d for d in session.workspace.documents if d.equipment_part == "arm")
        session.edit_named_group("create", "袖子", {(document.name, 0)})
        session.edit_named_group("create", "其他", {(document.name, 1)})
        count = session.split_mesh(document.name, 0)
        self.assertEqual(2, count)
        self.assertEqual({(document.name, 0), (document.name, 1)}, session.named_groups["袖子"])
        self.assertEqual({(document.name, 2)}, session.named_groups["其他"])
        session.undo()
        self.assertEqual({(document.name, 0)}, session.named_groups["袖子"])
        self.assertEqual({(document.name, 1)}, session.named_groups["其他"])
        session.redo()
        session.apply_group(session.named_groups["袖子"], 60)
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "groups.json"
            session.save(project)
            self.assertFalse(session.project_dirty)
            reopened, _ = EditorSession.open(project)
            self.assertEqual(session.named_groups, reopened.named_groups)
            self.assertEqual(document.bytes(), reopened.document(document.name).bytes())
            self.assertFalse(reopened.project_dirty)
            data = json.loads(project.read_text(encoding="utf-8"))
            data["named_groups"][0]["members"].append([document.name, 99999])
            project.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(Mod3Error):
                EditorSession.open(project)

    def test_group_only_edit_requires_save_and_undo_clears_dirty(self):
        session = EditorSession(workspace_fixture())
        key = (session.workspace.documents[0].name, 0)
        session.edit_named_group("create", "基础", {key})
        self.assertTrue(session.project_dirty)
        self.assertFalse(session.workspace.changed)
        session.undo()
        self.assertFalse(session.project_dirty)
        session.redo()
        self.assertTrue(session.project_dirty)
