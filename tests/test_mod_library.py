import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import mod_library

from mod_library import scan_library, filter_mods, save_library_root, saved_library_root, normalize_root, library_snapshot
from game_resources import save_game_directory, saved_game_directory
from test_core import minimal_mod3


class ModLibraryTests(unittest.TestCase):
    def setUp(self):
        profile = tempfile.TemporaryDirectory()
        self.addCleanup(profile.cleanup)
        environment = patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": profile.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_saved_index_reuses_unchanged_entries_and_detects_nested_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a", "b"):
                payload = root / name / "nativePC/body"
                payload.mkdir(parents=True)
                (payload / "body.mod3").write_bytes(minimal_mod3())
            archive = root / "coat.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("a.mod3", minimal_mod3())
            events = []
            first = scan_library(root, progress=events.append)
            self.assertEqual(3, events[-1]["scanned"])
            with patch("mod_library_index.stamp", side_effect=AssertionError("Snapshot must not rescan directories")):
                self.assertEqual(first[1], library_snapshot(root))
            with patch.object(mod_library.os, "scandir", wraps=os.scandir) as walk, patch.object(mod_library.zipfile, "ZipFile", wraps=zipfile.ZipFile) as zip_read:
                events.clear()
                second = scan_library(root, progress=events.append)
                self.assertEqual(first, second)
                self.assertFalse(walk.called)
                self.assertFalse(zip_read.called)
                self.assertEqual(3, events[-1]["reused"])
            (root / "a/nativePC/body/new.mod3").write_bytes(minimal_mod3())
            events.clear()
            _, records, _ = scan_library(root, progress=events.append)
            self.assertEqual(1, events[-1]["scanned"])
            self.assertEqual(2, events[-1]["reused"])
            self.assertEqual(2, next(r.model_count for r in records if r.name == "a"))
            (root / "b/nativePC/body/body.mod3").unlink()
            _, records, _ = scan_library(root)
            self.assertNotIn("b", {r.name for r in records})
            payload = root / "c/nativePC"
            payload.mkdir(parents=True)
            (payload / "new.mod3").write_bytes(minimal_mod3())
            archive.unlink()
            _, records, _ = scan_library(root)
            self.assertEqual({"a", "c"}, {r.name for r in records})

    def test_index_refreshes_metadata_and_replaced_zip_and_recovers_from_corruption(self):
        from mod_library_index import LibraryIndex
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "100/files/nativePC"
            payload.mkdir(parents=True)
            (payload / "body.mod3").write_bytes(minimal_mod3())
            info = root / "100/info.xml"
            info.write_text('<ModInfo><name>Old</name><enable>false</enable></ModInfo>')
            archive = root / "other.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("a.mod3", minimal_mod3())
            scan_library(root)
            info.write_text('<ModInfo><name>New</name><enable>true</enable></ModInfo>')
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("a.mod3", minimal_mod3())
                z.writestr("b.mod3", minimal_mod3())
            _, records, _ = scan_library(root)
            self.assertEqual("盒子已启用", next(r.box_state for r in records if r.name == "New"))
            self.assertEqual(2, next(r.model_count for r in records if r.kind == "ZIP"))
            index = LibraryIndex(root)
            before = index.path.read_bytes()
            scan_library(root, cancelled=lambda: True)
            self.assertEqual(before, index.path.read_bytes())
            index.path.write_text("broken JSON")
            _, recovered, _ = scan_library(root)
            self.assertEqual(records, recovered)

    def test_scan_avoids_path_stat_for_unrelated_texture_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "outfit/nativePC"
            payload.mkdir(parents=True)
            (payload / "body.mod3").write_bytes(minimal_mod3())
            for i in range(200):
                (payload / f"texture-{i}.tex").write_bytes(b"x")
            with patch.object(mod_library, "_linked", wraps=mod_library._linked) as linked:
                _, records, errors = scan_library(root)
            self.assertFalse(errors)
            self.assertEqual(1, records[0].model_count)
            self.assertFalse(any(Path(call.args[0]).suffix == ".tex" for call in linked.call_args_list))

    def test_box_names_payloads_texture_filter_and_read_only_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            box = Path(temp) / "MHW_box"
            root = box / "Mods_582010"
            model = root / "1030/files/nativePC/body/body.mod3"
            model.parent.mkdir(parents=True)
            model.write_bytes(minimal_mod3())
            info = root / "1030/info.xml"
            info.write_text('<ModInfo><moduleName>外套 &amp; 饰品</moduleName><enable>false</enable></ModInfo>', encoding="utf-8")
            texture = root / "1031/files/nativePC/test.tex"
            texture.parent.mkdir(parents=True)
            texture.write_bytes(b"texture only")
            before = {p: hashlib.sha256(p.read_bytes()).digest() for p in root.rglob("*") if p.is_file()}
            scanned, records, errors = scan_library(box)
            self.assertEqual(root, scanned)
            self.assertFalse(errors)
            self.assertEqual(1, len(records))
            self.assertEqual("外套 & 饰品", records[0].name)
            self.assertEqual(root / "1030/files", records[0].path)
            self.assertEqual("盒子未启用", records[0].box_state)
            self.assertEqual(1, records[0].model_count)
            self.assertEqual(records, filter_mods(records, "外套 1030"))
            self.assertFalse(filter_mods(records, "不存在"))
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).digest() for p in root.rglob("*") if p.is_file()})

    def test_custom_libraries_archives_invalid_zip_and_direct_model_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "普通外观/nativePC/body/model.MOD3"
            path.parent.mkdir(parents=True)
            path.write_bytes(minimal_mod3())
            with zipfile.ZipFile(root / "模型.zip", "w") as z:
                z.writestr("variant/nativePC/body/body.mod3", minimal_mod3())
            with zipfile.ZipFile(root / "贴图.zip", "w") as z:
                z.writestr("nativePC/skin.tex", b"x")
            (root / "未展开.rar").write_bytes(b"no decompression during listing")
            (root / "未展开.7z").write_bytes(b"no decompression during listing")
            (root / "损坏.zip").write_bytes(b"not ZIP")
            (root / "单独.mod3").write_bytes(minimal_mod3())
            scanned, records, errors = scan_library(root)
            self.assertEqual({"文件夹", "ZIP", "RAR", "7Z", "MOD3"}, {r.kind for r in records})
            self.assertEqual(5, len(records))
            self.assertEqual(1, len(errors))
            self.assertNotIn("贴图", {r.name for r in records})
            self.assertTrue(all(r.model_count is None for r in records if r.kind in ("RAR", "7Z")))
            _, direct, _ = scan_library(root / "普通外观")
            self.assertEqual([root / "普通外观"], [r.path for r in direct])

    def test_settings_preserve_game_directory_and_custom_fields_both_directions(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profile"
            profile.mkdir()
            library = Path(temp) / "library"
            library.mkdir()
            with patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": str(profile)}):
                (profile / "settings.json").write_text(json.dumps({"other_preference": 123}))
                save_game_directory("E:/Game/MHW")
                save_library_root(library)
                self.assertEqual(str(library), saved_library_root())
                self.assertEqual("E:/Game/MHW", saved_game_directory())
                save_game_directory("E:/Game/Other")
                self.assertEqual(str(library), saved_library_root())
                self.assertEqual(123, json.loads((profile / "settings.json").read_text())["other_preference"])
                with self.assertRaises(ValueError):
                    save_library_root(library / "missing")
                self.assertEqual(str(library), saved_library_root())

    def test_cancel_and_bad_metadata_do_not_break_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "100/files/nativePC/a.mod3"
            path.parent.mkdir(parents=True)
            path.write_bytes(minimal_mod3())
            (root / "100/info.xml").write_text("invalid XML")
            _, cancelled, _ = scan_library(root, cancelled=lambda: True)
            self.assertEqual([], cancelled)
            _, records, _ = scan_library(root)
            self.assertEqual("100", records[0].name)
            with self.assertRaises(ValueError):
                normalize_root(root / "missing")


if __name__ == "__main__":
    unittest.main()
