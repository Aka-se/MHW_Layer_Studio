import json
import tempfile
import unittest
from pathlib import Path

from mod3 import ModWorkspace, Mod3Error
from session import EditorSession
from test_editor import workspace_fixture

ROOT = Path(__file__).resolve().parents[1]


class AnnotationTests(unittest.TestCase):
    def test_batch_names_preserve_distinct_notes_and_model_bytes(self):
        session = EditorSession(workspace_fixture())
        a, b = [(d.name, 0) for d in session.workspace.documents]
        original = [d.bytes() for d in session.workspace.documents]
        session.edit_annotations({a}, note="左侧\n绑带")
        session.edit_annotations({b}, note="右侧")
        session.edit_annotations({a, b}, name="鞋子")
        self.assertEqual("左侧\n绑带", session.annotations[a]["note"])
        self.assertEqual("右侧", session.annotations[b]["note"])
        self.assertTrue(session.project_metadata_dirty)
        self.assertFalse(session.workspace.changed)
        self.assertEqual(original, [d.bytes() for d in session.workspace.documents])
        session.undo()
        self.assertEqual("", session.annotations[a]["name"])
        session.redo()
        self.assertEqual("鞋子", session.annotations[b]["name"])
        session.edit_annotations({a}, name="")
        self.assertEqual("左侧\n绑带", session.annotations[a]["note"])
        session.edit_annotations({a}, note="")
        self.assertNotIn(a, session.annotations)

    def test_validation_is_atomic_and_metadata_only_dirty_can_be_undone(self):
        session = EditorSession(workspace_fixture())
        key = (session.workspace.documents[0].name, 0)
        for fields in ({"name": "x" * 81}, {"note": "x" * 2001}, {"name": "坏\n名称"}, {"note": 3}, {"unexpected": "x"}):
            with self.assertRaises(Mod3Error):
                session.edit_annotations({key}, **fields)
            self.assertFalse(session.annotations)
            self.assertFalse(session.can_undo)
        with self.assertRaises(Mod3Error):
            session.edit_annotations({key, (key[0], 999)}, name="外套")
        self.assertFalse(session.annotations)
        session.edit_annotations({key}, name="外套", note="保留衣领")
        session.undo()
        self.assertFalse(session.project_dirty)
        session.redo()
        self.assertTrue(session.project_metadata_dirty)

    @unittest.skipUnless((ROOT / "Kuroki-Defult.zip").exists(), "optional local integration sample not available")
    def test_split_inherits_labels_shifts_other_labels_and_restores_history(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-Defult.zip"))
        document = next(d for d in session.workspace.documents if d.equipment_part == "arm")
        parent, later = (document.name, 0), (document.name, 1)
        session.edit_annotations({parent}, name="袖子", note="左右一组")
        session.edit_annotations({later}, name="后续部件")
        before = session._annotation_snapshot()
        self.assertEqual(2, session.split_mesh(document.name, 0))
        self.assertEqual("袖子", session.annotations[(document.name, 1)]["name"])
        self.assertEqual("后续部件", session.annotations[(document.name, 2)]["name"])
        session.edit_annotations({(document.name, 1)}, name="右袖")
        self.assertEqual("袖子", session.annotations[parent]["name"])
        session.undo()
        session.undo()
        self.assertEqual(before, session.annotations)
        session.redo()
        session.redo()
        self.assertEqual("右袖", session.annotations[(document.name, 1)]["name"])

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
    def test_project_round_trip_and_export_keeps_material_bytes(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        document = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        originals = {d.name: d.bytes() for d in session.workspace.documents}
        session.edit_annotations({(document.name, 1)}, name="外套", note="衣领与袖子一起处理")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notes.json"
            session.save(path)
            reopened, _ = EditorSession.open(path)
            self.assertEqual(session.annotations, reopened.annotations)
            self.assertFalse(reopened.project_dirty)
            archive = session.workspace.export(Path(folder) / "copy.zip")
            self.assertEqual(originals, {d.name: d.bytes() for d in ModWorkspace.open(archive).documents})
            data = json.loads(path.read_text(encoding="utf-8"))
            data["annotations"][0]["mesh"] = 99999
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(Mod3Error):
                EditorSession.open(path)
