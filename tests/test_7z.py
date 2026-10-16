import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from archive_support import ArchiveError, _sevenzip_entries
from materials import MaterialInfo
from mod3 import ModWorkspace
from session import EditorSession
from textures import TextureLibrary

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/7z"


class SevenZipListingTests(unittest.TestCase):
    def test_unsafe_duplicate_link_and_encrypted_entries_rejected(self):
        valid = "Path = body.mod3\r\r\nSize = 0\r\r\nAttributes = A\r\r\nEncrypted = -\r\r\nCRC = \r\r\n"
        self.assertEqual("body.mod3", _sevenzip_entries(valid)[0][0][0])
        for listing in (valid.replace("body.mod3", "../body.mod3"), valid + "\n\n" + valid,
                        valid.replace("Encrypted = -", "Encrypted = +"), valid + "Symbolic Link = secret\n",
                        valid.replace("Size = 0", "Size = 123")):
            with self.assertRaises(ArchiveError):
                _sevenzip_entries(listing)


@unittest.skipUnless(os.name == "nt" and (ROOT / "assets/7zip/7za.exe").exists(), "Windows 7-Zip backend required")
class SevenZipTests(unittest.TestCase):
    def test_solid_and_non_solid_assets_edit_export_and_projects(self):
        for filename in ("solid.7z", "normal.7z"):
            with self.subTest(filename=filename):
                source = FIXTURES / filename
                before = hashlib.sha256(source.read_bytes()).digest()
                workspace = ModWorkspace.open(source)
                self.assertEqual("7z", workspace.kind)
                self.assertEqual(1, workspace.mesh_count)
                self.assertFalse(workspace.errors)
                document = workspace.documents[0]
                document.materials[0] = MaterialInfo("coat", textures=("材质目录/coat_BML",))
                self.assertEqual([255, 0, 0, 255], TextureLibrary(workspace).get(document, 0)[0, 0].tolist())
                with zipfile.ZipFile(workspace.archive_path) as cached:
                    original = {n: cached.read(n) for n in cached.namelist() if not n.endswith("/")}
                session = EditorSession(workspace)
                key = (document.name, 0)
                session.apply_group([key], 62)
                session.edit_named_group("create", "内层", [key])
                session.set_health_thresholds({60: 85, 61: 70, 62: 55})
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(ValueError):
                        session.export(Path(temp) / "copy.7z")
                    output = session.export(Path(temp) / "copy.zip")
                    reopened = EditorSession(ModWorkspace.open(output))
                    self.assertEqual(session.named_groups, reopened.named_groups)
                    self.assertEqual(session.health_thresholds, reopened.health_thresholds)
                    self.assertEqual(document.bytes(), reopened.workspace.documents[0].bytes())
                    with zipfile.ZipFile(output) as exported:
                        for name, payload in original.items():
                            if not name.endswith(".mod3"):
                                self.assertEqual(payload, exported.read(name))
                    project = Path(temp) / "project.json"
                    session.save(project)
                    restored, _ = EditorSession.open(project)
                    self.assertEqual("7z", restored.workspace.kind)
                    self.assertEqual(session.health_thresholds, restored.health_thresholds)
                self.assertEqual(before, hashlib.sha256(source.read_bytes()).digest())

    def test_wrong_extension_detected_by_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "misnamed.rar"
            source.write_bytes((FIXTURES / "solid.7z").read_bytes())
            self.assertEqual("7z", ModWorkspace.open(source).kind)
            source = Path(temp) / "misnamed.7z"
            source.write_bytes((ROOT / "tests/fixtures/rar/rar5-solid.rar").read_bytes())
            self.assertEqual("rar", ModWorkspace.open(source).kind)

    def test_encrypted_truncated_and_corrupt_payloads_rejected(self):
        for filename in ("password.7z", "password-content.7z"):
            with self.assertRaisesRegex(ArchiveError, "加密"):
                ModWorkspace.open(FIXTURES / filename)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "broken.7z"
            payload = (FIXTURES / "solid.7z").read_bytes()
            source.write_bytes(payload[:80])
            with self.assertRaises(ArchiveError):
                ModWorkspace.open(source)
            data = bytearray(payload)
            data[60] ^= 127
            source.write_bytes(data)
            with self.assertRaises(ArchiveError):
                ModWorkspace.open(source)
