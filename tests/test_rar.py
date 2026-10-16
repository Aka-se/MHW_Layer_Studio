import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from archive_support import ArchiveError, safe_member_name, unrar_path
from materials import MaterialInfo
from mod3 import ModWorkspace
from session import EditorSession
from textures import TextureLibrary

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/rar"


class ArchivePathTests(unittest.TestCase):
    def test_rar_paths_cannot_escape_or_target_devices(self):
        self.assertEqual("nativePC/衣服/body.mod3", safe_member_name(r"nativePC\衣服\body.mod3"))
        for name in ("../body.mod3", "/body.mod3", r"C:\body.mod3", r"\\server\body.mod3", "nativePC//body.mod3", "nativePC/../body.mod3", "a:stream", "bad\0.mod3", "NUL", "nativePC/CON.txt"):
            with self.assertRaises(ArchiveError):
                safe_member_name(name)


@unittest.skipUnless(os.name == "nt" and (ROOT / "assets/unrar/UnRAR.exe").exists(), "Windows UnRAR backend required")
class RarTests(unittest.TestCase):
    def test_rar4_rar5_solid_and_normal_preserve_models_materials_and_unicode_assets(self):
        for filename in ("rar4-solid.rar", "rar5-solid.rar", "rar5-normal.rar"):
            with self.subTest(filename=filename):
                source = FIXTURES / filename
                before = hashlib.sha256(source.read_bytes()).digest()
                workspace = ModWorkspace.open(source)
                self.assertEqual("rar", workspace.kind)
                self.assertEqual(1, workspace.mesh_count)
                self.assertFalse(workspace.errors)
                document = workspace.documents[0]
                document.materials[0] = MaterialInfo("coat", textures=("材质目录/coat_BML",))
                self.assertEqual([255, 0, 0, 255], TextureLibrary(workspace).get(document, 0)[0, 0].tolist())
                with zipfile.ZipFile(workspace.archive_path) as cached:
                    original = {n: cached.read(n) for n in cached.namelist() if not n.endswith("/")}
                self.assertIn("说明.txt", original)
                self.assertIn("nativePC/材质目录/coat_BML.tex", original)
                session = EditorSession(workspace)
                key = (document.name, 0)
                session.apply_group([key], 60)
                session.edit_named_group("create", "外套", [key])
                session.edit_annotations([key], name="颜色部件", note="RAR 编辑")
                session.set_health_thresholds({60: 85, 61: 70, 62: 55})
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(ValueError):
                        session.export(Path(temp) / "copy.rar")
                    output = session.export(Path(temp) / "copy.zip")
                    reopened = EditorSession(ModWorkspace.open(output))
                    self.assertEqual(session.named_groups, reopened.named_groups)
                    self.assertEqual(session.annotations, reopened.annotations)
                    self.assertEqual(session.health_thresholds, reopened.health_thresholds)
                    self.assertEqual(document.bytes(), reopened.workspace.documents[0].bytes())
                    with zipfile.ZipFile(output) as exported:
                        for name, payload in original.items():
                            if not name.endswith(".mod3"):
                                self.assertEqual(payload, exported.read(name))
                    project = Path(temp) / "project.json"
                    session.save(project)
                    project_session, _ = EditorSession.open(project)
                    self.assertEqual("rar", project_session.workspace.kind)
                    self.assertEqual(session.named_groups, project_session.named_groups)
                    self.assertEqual(session.health_thresholds, project_session.health_thresholds)
                self.assertEqual(before, hashlib.sha256(source.read_bytes()).digest())

    def test_multi_volume_complete_missing_and_wrong_first_volume(self):
        complete = ModWorkspace.open(FIXTURES / "multi.part1.rar")
        self.assertEqual(1, complete.mesh_count)
        with zipfile.ZipFile(complete.archive_path) as archive:
            self.assertEqual(2100, len(archive.read("binary.bin")))
        with self.assertRaises(ArchiveError):
            ModWorkspace.open(FIXTURES / "multi.part2.rar")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "multi.part1.rar"
            source.write_bytes((FIXTURES / source.name).read_bytes())
            with self.assertRaises(ArchiveError):
                ModWorkspace.open(source)

    def test_password_and_truncated_archives_are_explicitly_rejected(self):
        with self.assertRaisesRegex(ArchiveError, "加密"):
            ModWorkspace.open(FIXTURES / "rar5-password.rar")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "broken.rar"
            payload = (FIXTURES / "rar5-solid.rar").read_bytes()
            source.write_bytes(payload[:100])
            with self.assertRaises(ArchiveError):
                ModWorkspace.open(source)
