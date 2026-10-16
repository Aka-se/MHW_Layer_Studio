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
