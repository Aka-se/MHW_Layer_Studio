import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from mod3 import ModWorkspace
from session import EditorSession
from mesh_features import read_bones, feature_profile, compare_features
from spatial_groups import suggest_groups
from test_split import triangle_records

ROOT = Path(__file__).resolve().parents[1]


class FeatureTests(unittest.TestCase):
    def test_four_weights_complement_duplicate_ids_and_nonzero_start(self):
        data = bytearray(7 + 36 * 2)
        for row in range(2):
            start = 7 + row * 36
            struct.pack_into("<I", data, start + 24, 512 | (256 << 10))
            data[start + 28:start + 32] = bytes([3, 3, 5, 7])
            data[start + 32:start + 36] = b'\xff\xff\xff\xff'
        before = bytes(data)
        ids, weights, status = read_bones(data, 7, 2, 36, 0x3C730760)
        self.assertEqual([[3, 3, 5, 7]] * 2, ids.tolist())
        np.testing.assert_allclose(weights[0], [768 / 1023, 0, 0, 255 / 1023], atol=1e-6)
        np.testing.assert_allclose(weights.sum(1), 1)
        self.assertIn("4", status)
        self.assertEqual(before, bytes(data))

    def test_eight_weights_two_uvs_color_and_unknown_layout_fallback(self):
        data = bytearray(48)
        struct.pack_into("<I", data, 28, 256 | (256 << 10))
        data[32:36] = bytes([64, 0, 0, 0])
        data[36:44] = bytes([3, 3, 4, 7, 0, 0, 0, 8])
        data[44:48] = b'\xff\x00\xff\xff'
        ids, weights, status = read_bones(data, 0, 1, 48, 0xB8E69244)
        self.assertEqual([3, 3, 4, 7, 0, 0, 0, 8], ids[0].tolist())
        np.testing.assert_allclose(weights[0, [0, 3, 7]], [512 / 1023, 64 / 255, 1 - 512 / 1023 - 64 / 255], atol=1e-6)
        self.assertEqual(0, weights[0, 1])
        self.assertIn("8", status)
        self.assertIsNone(read_bones(data, 0, 1, 32, 0xB8E69244)[0])
        self.assertIsNone(read_bones(data, 0, 1, 48, 0x12345678)[0])
        struct.pack_into("<I", data, 28, (1023 | (1023 << 10) | (1023 << 20)))
        self.assertIsNone(read_bones(data, 0, 1, 48, 0xB8E69244)[0])
        struct.pack_into("<I", data, 28, 1 << 30)
        self.assertIsNone(read_bones(data, 0, 1, 48, 0xB8E69244)[0])

    def scene(self):
        bounds = [([-10, 50, 0], [-1, 51, 1]), ([1, 50, 0], [10, 51, 1]),
                  ([-.1, 50.4, .4], [.1, 50.6, .6]), ([0, 0, 0], [0, 100, 0])]
        meshes = []
        for i, (lo, hi) in enumerate(bounds):
            vertices = np.array([lo, hi, [lo[0], hi[1], hi[2]]], np.float32)
            ids = np.array([[5, 6, 0, 0]] * 3, np.uint8)
            weights = np.array([[.8, .2, 0, 0]] * 3, np.float32)
            meshes.append(SimpleNamespace(index=i, vertices=vertices, faces=np.array([[0, 1, 2]], np.int32),
                                          lod=65535, material_id=2, visible_condition=0, bone_ids=ids,
                                          bone_weights=weights, uvs=np.array([[.1, .1], [.2, .2], [.1, .2]], np.float32)))
        meshes[1].bone_weights[:, :2] = [.2, .8]
        meshes[2].bone_weights[:, :2] = [.75, .25]
        d = SimpleNamespace(name="wst/test.mod3", outfit_id="test", equipment_part="wst", meshes=meshes,
                            material_info=lambda mid: SimpleNamespace(name="same"))
        w = ModWorkspace()
        w.documents = [d]
        return w, {(d.name, i) for i in range(3)}

    def test_bones_resolve_spatial_tie_without_changing_geometry(self):
        w, keys = self.scene()
        fragment = (w.documents[0].name, 2)
        before = [(m.vertices.copy(), m.faces.copy()) for m in w.documents[0].meshes]
        old = next(g for g in suggest_groups(w, keys, distance="常规", pair=False, use_features=False) if fragment in g["members"])
        self.assertFalse(old["accepted"])
        group = next(g for g in suggest_groups(w, keys, distance="常规", pair=False) if fragment in g["members"])
        self.assertEqual({0, 2}, {i for n, i in group["members"]})
        self.assertTrue(group["accepted"])
        self.assertIn("骨骼影响区分", group["reason"])
        for m, (vertices, faces) in zip(w.documents[0].meshes, before):
            np.testing.assert_array_equal(m.vertices, vertices)
            np.testing.assert_array_equal(m.faces, faces)

    def test_uv_only_never_resolves_tie_or_bone_conflict(self):
        w, keys = self.scene()
        d = w.documents[0]
        for m in d.meshes:
            m.bone_weights[:, :2] = [.8, .2]
        d.meshes[1].uvs += .6
        fragment = (d.name, 2)
        group = next(g for g in suggest_groups(w, keys, distance="常规", pair=False) if fragment in g["members"])
        self.assertEqual([fragment], group["members"])
        self.assertFalse(group["accepted"])
        self.assertIn("UV 更接近", group["reason"])
        d.meshes[2].bone_ids[:] = 99
        group = next(g for g in suggest_groups(w, keys, distance="常规", pair=False) if fragment in g["members"])
        self.assertFalse(group["accepted"])
        self.assertIn("骨骼影响不一致", group["reason"])

    def test_bone_similarity_does_not_expand_distance_and_repeating_uv_is_unknown(self):
        w, keys = self.scene()
        d = w.documents[0]
        d.meshes[2].vertices[:, 1] = 80
        fragment = (d.name, 2)
        group = next(g for g in suggest_groups(w, keys, distance="常规", pair=False) if fragment in g["members"])
        self.assertFalse(group["accepted"])
        d.meshes[2].uvs += 2
        self.assertIsNone(feature_profile(d.meshes[2])["uv"])
        d.meshes[2].bone_weights = None
        self.assertEqual((None, None), compare_features(feature_profile(d.meshes[2]), feature_profile(d.meshes[0])))

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
    def test_ex2_complete_leg_assemblies_and_original_records_preserved(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        before = d.bytes()
        triangles = triangle_records(d)
        key = (d.name, 1)
        old = session.prepare_spatial_groups([key], use_features=False)
        new = session.prepare_spatial_groups([key])
        self.assertEqual(6, sum(len(g["members"]) for g in old["groups"] if not g["accepted"]))
        self.assertEqual(0, sum(len(g["members"]) for g in new["groups"] if not g["accepted"]))
        cuff = next(g for g in new["groups"] if (d.name, 9) in g["members"])
        cuff_pair = [g for g in new["groups"] if g.get("parent") == cuff["parent"]]
        self.assertEqual({3, 4, 9, 10}, {i for g in cuff_pair for n, i in g["members"]})
        legs = {11, 12, 13, 14, 15, 16, 18, 19, 25, 26, 27, 28}
        group = next(g for g in new["groups"] if (d.name, 12) in g["members"])
        pair = [g for g in new["groups"] if g.get("parent") == group["parent"]]
        self.assertEqual([6, 6], [len(g["members"]) for g in pair])
        self.assertEqual(legs, {i for g in pair for n, i in g["members"]})
        self.assertTrue(group["accepted"])
        self.assertIn("完整局部组件", group["reason"])
        unpaired = session.prepare_spatial_groups([key], pair=False)
        for member, expected in ((12, {11, 12, 13, 18, 25, 27}), (15, {14, 15, 16, 19, 26, 28})):
            group = next(g for g in unpaired["groups"] if (d.name, member) in g["members"])
            self.assertEqual(expected, {i for n, i in group["members"]})
            self.assertTrue(group["accepted"])
        self.assertEqual(before, d.bytes())
        self.assertEqual(triangles, triangle_records(new["staged"].document(d.name)))

    def test_rigid_assembly_requires_known_joint_local_connectivity_and_bounded_span(self):
        from spatial_groups import _bone_assemblies, _bounds
        from surface_distance import SurfaceDistances
        # A chain of tiny, identically skinned parts must not grow indefinitely.
        w, _ = self.scene()
        template = w.documents[0].meshes[0]
        parts = []
        for i in range(25):
            from copy import deepcopy
            mesh = deepcopy(template)
            mesh.vertices = np.array([[i * 2, 50, 0], [i * 2 + 1, 50, 0], [i * 2, 51, 0]], np.float32)
            mesh.bone_weights[:, :2] = [1, 0]
            lo, hi = _bounds(mesh)
            parts.append({"key": ("example", i), "features": feature_profile(mesh), "region": "中部",
                          "bounds": (lo, hi), "mesh": mesh, "triangles": 1})
        groups = _bone_assemblies(parts, 100, 1.5, SurfaceDistances(), lambda: False)
        self.assertGreater(len(groups), 1)
        for group in groups:
            lo = np.minimum.reduce([p["bounds"][0] for p in group["members"]])
            hi = np.maximum.reduce([p["bounds"][1] for p in group["members"]])
            self.assertLessEqual(np.max(hi - lo), 18)
        for p in parts:
            p["features"]["bones"] = None
        self.assertFalse(_bone_assemblies(parts, 100, 1.5, SurfaceDistances(), lambda: False))


if __name__ == "__main__":
    unittest.main()
