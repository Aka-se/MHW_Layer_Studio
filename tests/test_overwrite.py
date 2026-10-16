import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from mod3 import ModWorkspace, Mod3Error, EDITOR_ENTRY
from session import EditorSession
from test_core import minimal_mod3


def folder_fixture(root):
    root.mkdir()
    (root / "body.mod3").write_bytes(minimal_mod3())
    (root / "wst.mod3").write_bytes(minimal_mod3())
    (root / "texture.tex").write_bytes(b"original texture bytes")
    return EditorSession(ModWorkspace.open(root))


class OverwriteTests(unittest.TestCase):
    def test_folder_updates_changed_models_only_and_rebases_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "nativePC"
            session = folder_fixture(root)
            originals = {p.name: p.read_bytes() for p in root.iterdir()}
            untouched_stamp = (root / "wst.mod3").stat().st_mtime_ns
            session.apply_group([("body.mod3", 0)], 60)
            session.edit_named_group("create", "外套", [("body.mod3", 0)])
            session.edit_annotations([("body.mod3", 0)], name="外套", note="直接编辑")
            session.set_health_thresholds({60: 85, 61: 70, 62: 55})
            old_workspace = session.workspace
            state = old_workspace.overwrite_state(root)
            self.assertEqual({"body.mod3", EDITOR_ENTRY}, set(state))
            session.export(root, overwrite=True, expected_state=state)
            backup = old_workspace.last_backup
            self.assertFalse(backup.is_relative_to(root))
            self.assertEqual(originals["body.mod3"], (backup / "files/body.mod3").read_bytes())
            self.assertFalse((backup / "files/wst.mod3").exists())
            self.assertEqual(originals["wst.mod3"], (root / "wst.mod3").read_bytes())
            self.assertEqual(untouched_stamp, (root / "wst.mod3").stat().st_mtime_ns)
            self.assertEqual(originals["texture.tex"], (root / "texture.tex").read_bytes())
            self.assertFalse(session.can_undo)
            self.assertEqual(60, session.workspace.documents[0].meshes[0].visible_condition)
            self.assertEqual(0, session.workspace.documents[0].original_group(0))
            session.apply_group([("body.mod3", 0)], 61)
            project = Path(temp) / "after-save.json"
            session.save(project)
            restored, _ = EditorSession.open(project)
            self.assertEqual(61, restored.workspace.documents[0].meshes[0].visible_condition)
            self.assertEqual(session.named_groups, restored.named_groups)
            self.assertEqual(session.annotations, restored.annotations)
            self.assertEqual({60: 85, 61: 70, 62: 55}, restored.health_thresholds)
            old_workspace = session.workspace
            session.export(root, overwrite=True)
            self.assertNotEqual(backup, old_workspace.last_backup)
            self.assertEqual(60, ModWorkspace.open(old_workspace.last_backup / "files").documents[0].meshes[0].visible_condition)

    def test_zip_overwrite_backs_up_archive_and_preserves_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "mod.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("body.mod3", minimal_mod3())
                archive.writestr("extra.txt", b"retain me")
            original = source.read_bytes()
            session = EditorSession(ModWorkspace.open(source))
            session.apply_group([("body.mod3", 0)], 62)
            old = session.workspace
            session.export(source, overwrite=True)
            self.assertEqual(original, (old.last_backup / "files/mod.zip").read_bytes())
            with zipfile.ZipFile(source) as archive:
                self.assertEqual(b"retain me", archive.read("extra.txt"))
            self.assertEqual(62, session.workspace.documents[0].meshes[0].visible_condition)

    def test_mod3_pair_rollback_and_backup_on_success(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            sidecar = Path(str(source) + ".mhwlayer.json")
            session = EditorSession(ModWorkspace.open(source))
            session.edit_annotations([("body.mod3", 0)], name="外套")
            session.export(source, overwrite=True)
            session.apply_group([("body.mod3", 0)], 60)
            before = {p: p.read_bytes() for p in (source, sidecar)}
            replace = os.replace
            def fail_metadata(a, b):
                if Path(b) == sidecar:
                    raise PermissionError("simulated locked sidecar")
                return replace(a, b)
            with patch("safe_export.os.replace", side_effect=fail_metadata):
                with self.assertRaisesRegex(ValueError, "已恢复"):
                    session.export(source, overwrite=True)
            for p, data in before.items():
                self.assertEqual(data, p.read_bytes())
            self.assertTrue(session.can_undo)
            old = session.workspace
            session.export(source, overwrite=True)
            self.assertEqual(before[sidecar], (old.last_backup / "files" / sidecar.name).read_bytes())
            self.assertEqual(60, session.workspace.documents[0].meshes[0].visible_condition)

    def test_folder_partial_failure_restores_models_and_keeps_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "nativePC"
            session = folder_fixture(root)
            session.apply_group([("body.mod3", 0), ("wst.mod3", 0)], 60)
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            replace = os.replace
            def fail_second(a, b):
                if Path(b).name == "wst.mod3":
                    raise PermissionError("simulated locked model")
                return replace(a, b)
            with patch("safe_export.os.replace", side_effect=fail_second):
                with self.assertRaisesRegex(ValueError, "已恢复"):
                    session.export(root, overwrite=True)
            self.assertEqual(original, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_cancel_state_external_edit_and_invalid_staging_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "nativePC"
            session = folder_fixture(root)
            session.apply_group([("body.mod3", 0)], 60)
            state = session.workspace.overwrite_state(root)
            metadata = root / EDITOR_ENTRY
            metadata.parent.mkdir()
            metadata.write_bytes(b"external metadata change")
            with self.assertRaisesRegex(ValueError, "确认后发生变化"):
                session.export(root, overwrite=True, expected_state=state)
            self.assertEqual(b"external metadata change", metadata.read_bytes())
            self.assertEqual(minimal_mod3(), (root / "body.mod3").read_bytes())
            modified = bytearray(minimal_mod3())
            modified[320] ^= 1
            (root / "body.mod3").write_bytes(modified)
            with self.assertRaisesRegex(Mod3Error, "其他程序修改"):
                session.export(root, overwrite=True)
            self.assertEqual(modified, (root / "body.mod3").read_bytes())
            (root / "body.mod3").write_bytes(minimal_mod3())
            session.workspace.documents[0]._data[:4] = b"BAD!"
            with self.assertRaises(Mod3Error):
                session.workspace.export(root, overwrite=True)
            self.assertEqual(minimal_mod3(), (root / "body.mod3").read_bytes())

    def test_default_export_still_requires_new_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "nativePC"
            session = folder_fixture(root)
            with self.assertRaises(Mod3Error):
                session.export(root)
            with self.assertRaises(Mod3Error):
                session.export(root / "nested", overwrite=True)
