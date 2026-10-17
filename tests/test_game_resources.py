import hashlib
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from game_resources import BLOCK, KEYS, ChunkReader, GameTextures, OozWorker, ResourceError, chunk_paths, normalize_reference, save_game_directory, saved_game_directory
from materials import MaterialInfo
from mod3 import ModWorkspace
from textures import TextureLibrary, decode_tex
from test_core import minimal_mod3
from test_textures import tex
from test_editor import mrl_fixture


def synthetic_chunk(path, reference="pl/test/armor.tex", payload=None, short_name=False):
    payload = tex() if payload is None else payload
    data = bytearray(2 * BLOCK)
    data[:4] = b"PKG "
    struct.pack_into("<II", data, 12, 1, 1)
    data[256] = 92
    struct.pack_into("<I", data, 336, 1)
    row = 340
    name = ("\\" + reference.replace("/", "\\")).encode()
    data[row:row + len(name)] = name
    offset = BLOCK - 10
    struct.pack_into("<QQII", data, row + 160, len(payload), offset, 2 if short_name else 0, 0)
    data[offset:offset + len(payload)] = payload
    encoded = b"".join(bytes(b ^ KEYS[i][j % 16] for j, b in enumerate(data[i * BLOCK:(i + 1) * BLOCK])) for i in range(2))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"CMP\0" + struct.pack("<I", 2) + struct.pack("<QQ", 24 << 24, (24 + BLOCK) << 24) + encoded)
    return payload


class GameResourceTests(unittest.TestCase):
    def test_material_lookup_rebuilds_texture_only_cache_and_uses_newest_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root, cache = Path(temp) / "game", Path(temp) / "cache"
            reference = "npc/npcbs/npcbs009/npcbs009.mrl3"
            original = bytes(mrl_fixture())
            synthetic_chunk(root / "chunk/chunkG0.bin", reference, original)
            resources = GameTextures(root, cache)
            try:
                reader = ChunkReader(resources.paths[0], resources.worker, resources.pattern, cache)
                resources.readers[resources.paths[0]] = reader
                reader.cache.mkdir(parents=True, exist_ok=True)
                (reader.cache / "index.json").write_text(json.dumps({"signature": reader.signature, "textures": {}}))
                data, origin = resources.get_material(r"nativePC\npc\npcbs\npcbs009\npcbs009")
                self.assertEqual(original, data)
                self.assertIn("chunkG0.bin", origin)
                with patch.object(reader, "read", side_effect=AssertionError("material payload should be cached")):
                    self.assertEqual(original, resources.get_material(reference)[0])
                newer = original + b"new patch"
                synthetic_chunk(root / "chunk/chunkG10.bin", reference, newer)
                self.assertEqual(newer, resources.get_material(reference)[0])
                with self.assertRaises(ResourceError):
                    resources.get_material("../secret")
                resources.close()
                with self.assertRaises(ResourceError):
                    resources.get_material(reference)
            finally:
                resources.close()

    def test_missing_npc_materials_resolve_before_textures_without_exporting_game_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "npc.zip"
            name = "Option/NativePC/npc/npcbs/npcbs009/npcbs009.mod3"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr(name, minimal_mod3())
                # A mod's texture must win even when its MRL3 comes from the game.
                z.writestr("Option/NativePC/pl/test/body_BM.tex", tex())
            root = base / "game"
            synthetic_chunk(root / "chunk/chunkG0.bin", "npc/npcbs/npcbs009/npcbs009.mrl3", bytes(mrl_fixture()))
            workspace = ModWorkspace.open(source)
            document = workspace.documents[0]
            document.material_names = ["body_skin"]
            original = document.bytes()
            resources = GameTextures(root, base / "cache")
            try:
                library = TextureLibrary(workspace, game=resources)
                self.assertEqual([255, 0, 0, 255], library.get(document, 0)[0, 0].tolist())
                self.assertIn("游戏资源", library.material_info(document, 0).status)
                self.assertIn("模组内", library.status[(name, 0)])
                self.assertEqual({}, document.materials)
                self.assertEqual(original, document.bytes())
                exported = workspace.export(base / "export.zip")
                with zipfile.ZipFile(exported) as z:
                    self.assertFalse(any(n.endswith('.mrl3') for n in z.namelist()))
                    self.assertEqual(original, z.read(name))
            finally:
                resources.close()

    def test_shared_material_fallback_keeps_custom_bindings_and_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "npc.zip"
            name = "nativePC/npc/example.mod3"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr(name, minimal_mod3())
                z.writestr("nativePC/pl/test/body_BM.tex", tex())
            shared = base / "shared/nativePC/npc"
            shared.mkdir(parents=True)
            (shared / "example.mrl3").write_bytes(mrl_fixture())
            workspace = ModWorkspace.open(source)
            document = workspace.documents[0]
            document.material_names = ["body_skin", "custom"]
            custom = MaterialInfo("custom", color_texture="pl/custom/unique_BM")
            document.materials[1] = custom
            library = TextureLibrary(workspace, roots=[base / "shared"])
            self.assertIsNotNone(library.get(document, 0))
            self.assertEqual(custom, library.material_info(document, 1))
            (shared / "example.mrl3").write_bytes(b"broken")
            library = TextureLibrary(workspace, roots=[base / "shared"])
            self.assertIsNone(library.get(document, 0))
            self.assertIn("MRL3", library.status[(name, 0)])
            self.assertIsNone(TextureLibrary(workspace).get(document, 0))

    def test_cross_block_read_short_names_cache_and_game_files_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root, cache = Path(temp) / "game", Path(temp) / "cache"
            path = root / "chunk/chunkG0.bin"
            payload = synthetic_chunk(path, short_name=True)
            original = hashlib.sha256(path.read_bytes()).digest()
            resources = GameTextures(root, cache)
            try:
                data, origin = resources.get(r"nativePC\pl\test\armor")
                self.assertEqual(payload, data)
                self.assertIn("chunkG0.bin", origin)
                reader = next(iter(resources.readers.values()))
                with patch.object(reader, "read", side_effect=AssertionError("cache should skip game decoding")):
                    data, origin = resources.get("pl/test/armor.tex")
                self.assertIn("缓存", origin)
                reader.records = None
                with patch.object(reader, "block", side_effect=AssertionError("index should be cached")):
                    self.assertIn("pl/test/armor.tex", reader.index())
            finally:
                resources.close()
            self.assertEqual(original, hashlib.sha256(path.read_bytes()).digest())
            self.assertEqual([path], list(root.rglob("*.bin")))
            self.assertFalse((root / "game-textures").exists())

    def test_numeric_patch_priority_and_new_chunk_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            synthetic_chunk(root / "chunk/chunkG2.bin")
            blue = tex(pixels=bytes([0, 0, 255, 255]) * 4)
            synthetic_chunk(root / "chunk/chunkG10.bin", payload=blue)
            resources = GameTextures(root, Path(temp) / "cache")
            try:
                data, origin = resources.get("pl/test/armor")
                self.assertEqual(blue, data)
                self.assertIn("chunkG10", origin)
                green = tex(pixels=bytes([0, 255, 0, 255]) * 4)
                synthetic_chunk(root / "chunk/chunkG11.bin", payload=green)
                self.assertEqual(green, resources.get("pl/test/armor")[0])
                red = tex()
                synthetic_chunk(root / "chunk/chunkG11.bin", payload=red)
                self.assertEqual(red, resources.get("pl/test/armor")[0])
            finally:
                resources.close()

    def test_corrupt_texture_and_index_cache_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            payload = synthetic_chunk(root / "chunk/chunkG0.bin")
            resources = GameTextures(root, Path(temp) / "cache")
            try:
                resources.get("pl/test/armor")
                reader = next(iter(resources.readers.values()))
                texture = next(reader.cache.glob("*.tex"))
                texture.write_bytes(bytes(len(payload)))
                self.assertEqual(payload, resources.get("pl/test/armor")[0])
                (reader.cache / "index.json").write_text("broken cache", encoding="utf-8")
                reader.records = None
                self.assertIn("pl/test/armor.tex", reader.index())
            finally:
                resources.close()

    def test_missing_paths_malformed_chunks_and_closed_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            with self.assertRaises(ResourceError):
                chunk_paths(root)
            path = root / "chunk/chunkG0.bin"
            synthetic_chunk(path)
            resources = GameTextures(root, Path(temp) / "cache")
            try:
                with self.assertRaisesRegex(ResourceError, "没有此贴图"):
                    resources.get("pl/test/missing")
                resources.close()
                with self.assertRaises(ResourceError):
                    resources.get("pl/test/armor")
            finally:
                resources.close()
            path.write_bytes(b"CMP\0" + struct.pack("<I", 2000000))
            with self.assertRaises(ResourceError):
                ChunkReader(path, OozWorker(), bytes(16), Path(temp) / "cache")
        for bad in ("../secret", "C:/secret", "pl/../secret", "pl//secret", "pl/test\0.tex"):
            with self.assertRaises(ResourceError):
                normalize_reference(bad)

    def test_game_path_settings_roundtrip_and_malformed_settings(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": temp}):
            self.assertIsNone(saved_game_directory())
            save_game_directory("E:/Game/Monster Hunter World")
            self.assertEqual("E:/Game/Monster Hunter World", saved_game_directory())
            (Path(temp) / "settings.json").write_text("invalid")
            self.assertIsNone(saved_game_directory())

    def test_mod_images_precede_game_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "mod.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("nativePC/body.mod3", minimal_mod3())
                z.writestr("nativePC/pl/test/armor.tex", tex())
            workspace = ModWorkspace.open(source)
            document = workspace.documents[0]
            document.materials[0] = MaterialInfo("armor", color_texture="pl/test/armor")
            class Game:
                def get(self, reference):
                    raise AssertionError("mod texture must take priority")
            lib = TextureLibrary(workspace, game=Game())
            self.assertEqual([255, 0, 0, 255], lib.get(document, 0)[0, 0].tolist())
            document.materials[0] = MaterialInfo("armor", color_texture="pl/test/missing")
            class Fallback:
                def get(self, reference):
                    return tex(pixels=bytes([0, 0, 255, 255]) * 4), "game test"
            self.assertEqual([0, 0, 255, 255], TextureLibrary(workspace, game=Fallback()).get(document, 0)[0, 0].tolist())

    def test_worker_bounded_raw_stream_and_bad_input_recovers(self):
        worker = OozWorker()
        try:
            self.assertEqual(bytes(BLOCK), worker.decode(b"\xcc\x06" + bytes(BLOCK)))
            with self.assertRaises(ResourceError):
                worker.decode(b"bad input")
            self.assertEqual(bytes(BLOCK), worker.decode(b"\xcc\x06" + bytes(BLOCK)))
        finally:
            worker.close()
