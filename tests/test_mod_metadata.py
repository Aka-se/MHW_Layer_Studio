import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mod3 import ModWorkspace, EDITOR_ENTRY
from session import EditorSession
from test_core import minimal_mod3

ROOT = Path(__file__).resolve().parents[1]


class ModMetadataTests(unittest.TestCase):
    def test_zip_and_folder_preserve_tags_and_authoritative_group_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for kind in ("zip", "folder"):
                source = base / ("source.zip" if kind == "zip" else "source")
                if kind == "zip":
                    with zipfile.ZipFile(source, "w") as z:
                        z.writestr("nativePC/pl/body.mod3", minimal_mod3())
                        z.writestr("nativePC/keep.tex", b"original-asset")
                else:
                    (source / "nativePC/pl").mkdir(parents=True)
                    (source / "nativePC/pl/body.mod3").write_bytes(minimal_mod3())
                    (source / "nativePC/keep.tex").write_bytes(b"original-asset")
                s = EditorSession(ModWorkspace.open(source))
                key = (s.workspace.documents[0].name, 0)
                s.edit_named_group("create", "鞋子", [key])
                s.edit_annotations([key], name="左鞋", note="内层\n成对控制")
                s.apply_group([key], 62)
                path = s.export(base / ("copy.zip" if kind == "zip" else "copy"), {"color": "贴图"})
                self.assertFalse(s.project_dirty)
                r = EditorSession(ModWorkspace.open(path))
                self.assertEqual(s.named_groups, r.named_groups)
                self.assertEqual(s.annotations, r.annotations)
                self.assertEqual(62, r.workspace.documents[0].meshes[0].visible_condition)
                self.assertEqual(0, r.workspace.documents[0].original_group(0))
                self.assertEqual({"color": "贴图"}, r.embedded_view)
                self.assertEqual(s.workspace.documents[0].bytes(), r.workspace.documents[0].bytes())
                r.export(base / ("again.zip" if kind == "zip" else "again"))
                if kind == "zip":
                    with zipfile.ZipFile(base / "again.zip") as z:
                        self.assertEqual(1, z.namelist().count(EDITOR_ENTRY))
                        self.assertEqual(b"original-asset", z.read("nativePC/keep.tex"))

    def test_single_mod3_sidecar_renames_keys_and_refuses_existing_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "body.mod3", Path(temp) / "renamed.mod3"
            source.write_bytes(minimal_mod3())
            s = EditorSession(ModWorkspace.open(source))
            s.edit_annotations([("body.mod3", 0)], name="外套")
            s.edit_named_group("create", "外套", [("body.mod3", 0)])
            sidecar = Path(str(output) + ".mhwlayer.json")
            sidecar.write_text("keep")
            with self.assertRaises(ValueError):
                s.export(output)
            self.assertFalse(output.exists())
            self.assertEqual("keep", sidecar.read_text())
            sidecar.unlink()
            s.export(output, {"selected": [["body.mod3", 0]], "texture_overrides": [["body.mod3", 0, "image.png"]]})
            r = EditorSession(ModWorkspace.open(output))
            self.assertEqual("外套", r.annotations[(output.name, 0)]["name"])
            self.assertEqual([[output.name, 0]], r.embedded_view["selected"])
            self.assertEqual(output.name, r.embedded_view["texture_overrides"][0][0])
            self.assertEqual(source.read_bytes(), output.read_bytes())

    def test_stale_or_invalid_members_reject_tags_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            s = EditorSession(ModWorkspace.open(source))
            s.edit_annotations([("body.mod3", 0)], name="外套")
            output = Path(temp) / "copy.mod3"
            s.export(output)
            data = ModWorkspace.open(output).editor_data
            for field in ("sha256", "annotations"):
                w = ModWorkspace.open(output)
                w.editor_data = copy.deepcopy(data)
                if field == "sha256":
                    w.editor_data["documents"][0]["sha256"] = "stale"
                else:
                    w.editor_data["annotations"].append({"document": output.name, "mesh": 999, "name": "bad"})
                r = EditorSession(w)
                self.assertFalse(r.annotations)
                self.assertFalse(r.named_groups)
                self.assertTrue(w.errors)

    @unittest.skipUnless((ROOT / "Kuroki-Defult.zip").exists(), "Kuroki sample not present")
    def test_split_export_reload_does_not_split_again_and_project_keeps_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            s = EditorSession(ModWorkspace.open(ROOT / "Kuroki-Defult.zip"))
            d = next(d for d in s.workspace.documents if d.equipment_part == "arm")
            s.split_mesh(d.name, 0)
            s.edit_annotations([(d.name, 1)], name="右袖")
            s.edit_named_group("create", "袖子", [(d.name, 0), (d.name, 1)])
            s.apply_group([(d.name, 0), (d.name, 1)], 60)
            count = s.workspace.mesh_count
            output = Path(temp) / "split.zip"
            s.export(output)
            r = EditorSession(ModWorkspace.open(output))
            self.assertEqual(count, r.workspace.mesh_count)
            self.assertEqual(s.named_groups, r.named_groups)
            self.assertEqual(0, r.document(d.name).original_group(1))
            self.assertFalse(getattr(r.document(d.name), "_splits", []))
            project = Path(temp) / "continue.json"
            r.save(project)
            q, _ = EditorSession.open(project)
            self.assertEqual(0, q.document(d.name).original_group(1))
            self.assertEqual(r.annotations, q.annotations)
            self.assertEqual(r.document(d.name)._mesh_labels, q.document(d.name)._mesh_labels)
