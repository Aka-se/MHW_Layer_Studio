import struct
import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from textures import decode_tex, TextureLibrary, TextureError
from mod3 import ModWorkspace, Mod3Document
from materials import MaterialInfo, material_hash, read_mrl3_bindings
from renderer import SoftwareRenderer
from session import EditorSession
from test_core import minimal_mod3
from test_editor import mrl_fixture

ROOT = Path(__file__).resolve().parents[1]


def tex(kind=7, width=2, height=2, pixels=None):
    header = bytearray(192)
    struct.pack_into("<II", header, 0, 0x00584554, 16)
    struct.pack_into("<III", header, 20, 1, width, height)
    struct.pack_into("<I", header, 36, kind)
    struct.pack_into("<Q", header, 184, 192)
    return bytes(header) + (pixels if pixels is not None else bytes([255, 0, 0, 255]) * width * height)


class TextureTests(unittest.TestCase):
    def test_albedo_binding_identifies_custom_names_and_excludes_normal_map(self):
        data = mrl_fixture()
        data[56:312] = b"\0" * 256
        data[56:56 + len(b"pl/custom/coat")] = b"pl/custom/coat"
        struct.pack_into("<I", data, 372, 0x52AD9000)
        binding = read_mrl3_bindings(data)[material_hash("body_skin")]
        self.assertEqual("pl/custom/coat", binding.color_texture)
        d = Mod3Document("body.mod3", minimal_mod3())
        d.materials[0] = MaterialInfo("coat", textures=("pl/custom/normal_BML", "pl/custom/coat"), color_texture=binding.color_texture)
        self.assertEqual("pl/custom/coat", TextureLibrary.color_reference(d, 0))
        struct.pack_into("<I", data, 372, 0xC2545000)
        self.assertIsNone(read_mrl3_bindings(data)[material_hash("body_skin")].color_texture)

    def test_exact_constant_maps_have_preview_only_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            w = ModWorkspace.open(source)
            d = w.documents[0]
            d.materials[0] = MaterialInfo("white", color_texture=r"Assets\default_tex\null_white")
            library = TextureLibrary(w)
            self.assertEqual([255, 255, 255, 255], library.get(d, 0)[0, 0].tolist())
            self.assertIn("常量", library.status[(d.name, 0)])
            self.assertEqual([source], list(Path(temp).iterdir()))
            d.materials[0] = MaterialInfo("missing", color_texture=r"pl\missing\null_white")
            self.assertIsNone(TextureLibrary(w).get(d, 0))

    def test_emissive_color_slot_fallback_and_albedo_priority(self):
        data = mrl_fixture()
        struct.pack_into("<I", data, 372, 0x76826000)
        self.assertEqual("pl/test/body_BM", read_mrl3_bindings(data)[material_hash("body_skin")].color_texture)
        # Adding a null albedo slot must not replace a valid emissive image.
        data.extend(bytes(16))
        data[334] = 4
        struct.pack_into("<I", data, 328, 32)
        data[384] = 2
        struct.pack_into("<I", data, 388, 0x52AD9000)
        self.assertEqual("pl/test/body_BM", read_mrl3_bindings(data)[material_hash("body_skin")].color_texture)
        # Prefer real albedo even when an emissive binding was read first.
        struct.pack_into("<I", data, 392, 1)
        struct.pack_into("<I", data, 376, 0)
        self.assertEqual("pl/test/body_BM", read_mrl3_bindings(data)[material_hash("body_skin")].color_texture)

    def test_single_large_mip_is_reduced_for_preview(self):
        image = decode_tex(tex(width=16, height=16), maximum=4)
        self.assertEqual((4, 4), image.size)
        self.assertEqual((255, 0, 0, 255), image.getpixel((1, 1)))

    @unittest.skipUnless(list(ROOT.glob("Milltina*.zip")), "Milltina sample not present")
    def test_milltina_emissive_painted_textures_read_without_game_files(self):
        source = next(ROOT.glob("Milltina*.zip"))
        digest = hashlib.sha256(source.read_bytes()).digest()
        workspace = ModWorkspace.open(source)
        original = [d.bytes() for d in workspace.documents]
        library = TextureLibrary(workspace)
        references = set()
        self.assertFalse(workspace.errors)
        for d in workspace.documents:
            for mid in {m.material_id for m in d.meshes}:
                info = d.material_info(mid)
                self.assertEqual("已匹配 MRL3", info.status)
                self.assertTrue(info.color_texture.startswith("Milltina_P\\"))
                references.add(info.color_texture)
                image = library.get(d, mid)
                self.assertIsNotNone(image, library.status.get((d.name, mid)))
                self.assertLessEqual(max(image.shape[:2]), 1024)
                self.assertIn("模组内", library.status[(d.name, mid)])
        self.assertEqual(8, len(references))
        self.assertEqual(original, [d.bytes() for d in workspace.documents])
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).digest())

    def test_rgba_bc1_bc7_and_truncated_payload(self):
        self.assertEqual((255, 0, 0, 255), decode_tex(tex()).getpixel((0, 0)))
        bc1 = struct.pack("<HHI", 0xF800, 0, 0)
        self.assertEqual((255, 0, 0, 255), decode_tex(tex(22, 4, 4, bc1)).getpixel((1, 1)))
        self.assertEqual((255, 0, 0, 255), decode_tex(tex(23, 4, 4, bc1)).getpixel((1, 1)))
        self.assertEqual((4, 4), decode_tex(tex(31, 4, 4, bytes(16))).size)
        with self.assertRaises(TextureError):
            decode_tex(tex()[:-1])
        with self.assertRaises(TextureError):
            decode_tex(tex(28))

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "EX2 sample not present")
    def test_mip_selection_and_uv_parser_preserve_split_uvs(self):
        # Choose the small mip and verify offset use, not bytes from the original mip.
        data = bytearray(200)
        struct.pack_into("<II", data, 0, 0x00584554, 16)
        struct.pack_into("<III", data, 20, 2, 2, 2)
        struct.pack_into("<I", data, 36, 7)
        struct.pack_into("<QQ", data, 184, 200, 216)
        data += bytes([255, 0, 0, 255]) * 4 + bytes([0, 0, 255, 255])
        self.assertEqual((0, 0, 255, 255), decode_tex(bytes(data), maximum=1).getpixel((0, 0)))
        s = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        d = next(d for d in s.workspace.documents if d.equipment_part == "wst")
        m = d.meshes[1]
        original = m.uvs[m.faces].copy()
        count = s.split_mesh(d.name, 1)
        after = np.concatenate([m.uvs[m.faces] for m in d.meshes[1:1 + count]])
        # Components reorder faces but never rewrite UV records.
        rows = lambda a: sorted(a.reshape(-1, 6).tobytes()[i:i+24] for i in range(0, a.size * 4, 24))
        self.assertEqual(rows(original), rows(after))

    def test_archive_lookup_shared_directory_override_and_missing_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "mod.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("Option/nativePC/body.mod3", minimal_mod3())
                z.writestr("Option/nativePC/Textures/Coat_BML.tex", tex())
            w = ModWorkspace.open(source)
            d = w.documents[0]
            d.materials[0] = MaterialInfo("Coat", textures=(r"Textures\Coat_BML",))
            library = TextureLibrary(w)
            self.assertEqual([255, 0, 0, 255], library.get(d, 0)[0, 0].tolist())
            d.materials[0] = MaterialInfo("Coat", textures=("Textures/Missing_BML",))
            library = TextureLibrary(w)
            self.assertIsNone(library.get(d, 0))
            (base / "Textures").mkdir()
            (base / "Textures/Missing_BML.tex").write_bytes(tex(pixels=bytes([0, 255, 0, 255]) * 4))
            library = TextureLibrary(w, [base])
            self.assertEqual([0, 255, 0, 255], library.get(d, 0)[0, 0].tolist())
            image = base / "manual.png"
            Image.new("RGBA", (2, 2), "blue").save(image)
            library = TextureLibrary(w, overrides={(d.name, 0): str(image)})
            self.assertEqual([0, 0, 255, 255], library.get(d, 0)[0, 0].tolist())
            with self.assertRaises(TextureError):
                library._paths("../secret_BML", d)

    def test_depth_alpha_picking_and_health_in_textured_preview(self):
        w = ModWorkspace()
        a, b = Mod3Document("rear.mod3", minimal_mod3()), Mod3Document("front.mod3", minimal_mod3())
        b.meshes[0].vertices[:, 2] = 1
        b.set_visible_condition(0, 60)
        for d in (a, b):
            d.meshes[0].uvs = np.zeros((3, 2), np.float32)
        w.documents = [a, b]
        class Library:
            def get(self, d, _):
                return np.array([[(255, 0, 0, 255) if d is a else (0, 0, 255, 0)]], np.uint8)
        renderer = SoftwareRenderer()
        renderer.color_mode, renderer.texture_library = "texture", Library()
        image = renderer.render(w, (160, 160))
        self.assertEqual((0, 0, 255), image.getpixel((80, 80)))
        self.assertEqual((b.name, 0), renderer.pick(80, 80))
        renderer.texture_alpha = True
        image = renderer.render(w, (160, 160))
        self.assertEqual((255, 0, 0), image.getpixel((80, 80)))
        self.assertEqual((a.name, 0), renderer.pick(80, 80))
        renderer.texture_alpha, renderer.health = False, 75
        image = renderer.render(w, (160, 160))
        self.assertEqual((255, 0, 0), image.getpixel((80, 80)))
        renderer.health = 100
        self.assertEqual((0, 0, 255), renderer.render(w, (160, 160)).getpixel((80, 80)))
