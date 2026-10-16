import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from mod3 import ModWorkspace, Mod3Error
from session import EditorSession
from renderer import SoftwareRenderer
from lod_groups import suggest_lod_groups, visible_lod, lod_label

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Skimpy Master Garon Armor-2039-1-1-1579667215.zip"


class LodTests(unittest.TestCase):
    def synthetic(self, specs):
        meshes = []
        for i, (lod, lo, hi, material, gid) in enumerate(specs):
            lo, hi = np.array(lo, np.float32), np.array(hi, np.float32)
            meshes.append(SimpleNamespace(index=i, lod=lod, material_id=material, visible_condition=gid,
                                          vertices=np.array([lo, hi, [lo[0], hi[1], hi[2]]], np.float32),
                                          faces=np.array([[0, 1, 2]], np.int32)))
        d = SimpleNamespace(name="wst/example.mod3", equipment_part="wst", outfit_id="example", meshes=meshes,
                            material_info=lambda mid: SimpleNamespace(name="same"))
        w = ModWorkspace()
        w.documents = [d]
        return w, {(d.name, m.index) for m in meshes}

    def test_unique_alternatives_merged_versions_and_boundaries(self):
        w, keys = self.synthetic([
            (1, [-10, 0, 0], [-1, 10, 3], 0, 0), (1, [1, 0, 0], [10, 10, 3], 0, 0),
            (2, [-10, 0, 0], [10, 10, 3], 0, 0), (4, [-10, 0, 0], [10, 10, 3], 0, 0),
            (65535, [-10, 0, 0], [10, 10, 3], 0, 0),
            (8, [-10, 0, 0], [10, 10, 3], 0, 60),
            (8, [-10, 0, 0], [10, 10, 3], 1, 0),
            (1, [50, 0, 0], [60, 10, 3], 0, 0), (2, [50, 0, 0], [60, 10, 3], 0, 0),
        ])
        groups = suggest_lod_groups(w, keys)
        self.assertEqual([{0, 1, 2, 3}, {7, 8}], [set(i for _, i in g["members"]) for g in groups])
        self.assertTrue(groups[0]["merged_lod"])
        self.assertFalse(groups[1]["merged_lod"])
        other = SimpleNamespace(**vars(w.documents[0]))
        other.name = "wst/other.mod3"
        w.documents.append(other)
        keys |= {(other.name, m.index) for m in other.meshes}
        self.assertEqual(4, len(suggest_lod_groups(w, keys)))
        self.assertTrue(all(len({n for n, i in g["members"]}) == 1 for g in suggest_lod_groups(w, keys)))

    def test_ambiguous_and_simultaneous_masks_do_not_bind(self):
        w, keys = self.synthetic([(1, [0, 0, 0], [10, 10, 3], 0, 0),
                                  (2, [0, 0, 0], [10, 10, 3], 0, 0),
                                  (2, [0, 0, 0], [10, 10, 3], 0, 0)])
        self.assertEqual([], suggest_lod_groups(w, keys))
        w.documents[0].meshes[2].lod = 65535
        w.documents[0].meshes[1].lod = 3
        self.assertEqual([], suggest_lod_groups(w, keys))
        self.assertTrue(visible_lod(SimpleNamespace(lod=-1), 1))
        self.assertFalse(visible_lod(SimpleNamespace(lod=65504), 1))
        self.assertTrue(visible_lod(SimpleNamespace(lod=65504), 32))
        self.assertEqual("通用", lod_label(SimpleNamespace(lod=-1)))

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_garon_single_selection_expands_family_safe_patch_undo_export(self):
        session = EditorSession(ModWorkspace.open(SOURCE))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        before = {d.name: d.bytes() for d in session.workspace.documents}
        source_hash = hashlib.sha256(SOURCE.read_bytes()).digest()
        plan = session.prepare_spatial_groups([(d.name, 0)])
        self.assertEqual(0, plan["split_count"])
        self.assertEqual(1, plan["lod_families"])
        self.assertEqual(8, plan["lod_added"])
        family = plan["groups"][0]
        self.assertEqual(set(range(9)), {i for _, i in family["members"]})
        self.assertTrue(family["merged_lod"])
        self.assertEqual(before, {doc.name: doc.bytes() for doc in session.workspace.documents})
        self.assertEqual(1, session.apply_spatial_groups(plan))
        session.apply_group(family["members"], 60)
        session.validate_patch()
        self.assertTrue(all(m.visible_condition == 60 for m in d.meshes))
        expected = bytearray(before[d.name])
        for mesh in d.meshes:
            expected[mesh.visible_condition_offset:mesh.visible_condition_offset + 2] = b'\x3c\x00'
        self.assertEqual(bytes(expected), d.bytes())
        renderer = SoftwareRenderer()
        renderer.isolate_selected = True
        renderer.selected_keys = set(family["members"])
        self.assertEqual({0, 1, 2, 3}, {m.index for _, _, m in renderer.visible_entries(session.workspace)})
        renderer.lod_bit = 2
        self.assertEqual({4}, {m.index for _, _, m in renderer.visible_entries(session.workspace)})
        renderer.health = 80
        self.assertEqual([], renderer.visible_entries(session.workspace))
        renderer.health = 81
        self.assertEqual(1, len(renderer.visible_entries(session.workspace)))
        with tempfile.TemporaryDirectory() as temp:
            archive = session.export(Path(temp) / "copy.zip")
            restored = EditorSession(ModWorkspace.open(archive))
            self.assertEqual(session.named_groups, restored.named_groups)
            self.assertEqual(d.bytes(), restored.document(d.name).bytes())
            project = Path(temp) / "layers.json"
            session.save(project, {"lod_view": "精度 2"})
            restored, view = EditorSession.open(project)
            self.assertEqual(session.named_groups, restored.named_groups)
            self.assertEqual(d.bytes(), restored.document(d.name).bytes())
            self.assertEqual("精度 2", view["lod_view"])
        session.undo()  # IDs
        session.undo()  # collection
        self.assertFalse(session.named_groups)
        self.assertEqual(before, {doc.name: doc.bytes() for doc in session.workspace.documents})
        session.redo()
        session.redo()
        self.assertTrue(all(m.visible_condition == 60 for m in d.meshes))
        self.assertEqual(source_hash, hashlib.sha256(SOURCE.read_bytes()).digest())

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_manual_groups_cancel_and_non_group_byte_protection(self):
        session = EditorSession(ModWorkspace.open(SOURCE))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        session.edit_named_group("create", "手工", [(d.name, 4)])
        plan = session.prepare_spatial_groups([(d.name, 0)])
        self.assertEqual(0, plan["lod_families"])
        self.assertEqual(0, plan["split_count"])
        self.assertTrue(plan["warnings"])
        self.assertFalse(any(g["accepted"] for g in plan["groups"]))
        plan = session.prepare_spatial_groups([(d.name, 0)], include_grouped=True)
        self.assertEqual(1, plan["lod_families"])
        self.assertEqual(1, session.apply_spatial_groups(plan))
        self.assertNotIn("手工", session.named_groups)
        with self.assertRaisesRegex(Mod3Error, "取消"):
            session.prepare_spatial_groups([(d.name, 0)], include_grouped=True, cancelled=lambda: True)
        d._data[d.meshes[0].header_offset + 8] ^= 1
        with self.assertRaisesRegex(Mod3Error, "Group 字段之外"):
            session.validate_patch()


if __name__ == "__main__":
    unittest.main()
