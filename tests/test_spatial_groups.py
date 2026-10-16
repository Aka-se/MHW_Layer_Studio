import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from mod3 import ModWorkspace, Mod3Error
from session import EditorSession
from spatial_groups import suggest_groups
from mesh_regions import bounds, group_order, position_hint
from test_split import triangle_records

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
class SpatialTests(unittest.TestCase):
    def setUp(self):
        self.session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        self.document = next(d for d in self.session.workspace.documents if d.equipment_part == "wst")
        self.key = (self.document.name, 1)

    def test_staging_is_read_only_same_material_feet_separate_and_symmetry_optional(self):
        session, d = self.session, self.document
        original, triangles = d.bytes(), triangle_records(d)
        source_hash = hashlib.sha256(session.workspace.source.read_bytes()).digest()
        session.edit_annotations([self.key], name="保留别名", note="保留备注")
        history = len(session._undo)
        plan = session.prepare_spatial_groups([self.key])
        self.assertEqual(original, d.bytes())
        self.assertEqual(2, len(d.meshes))
        self.assertEqual(history, len(session._undo))
        self.assertEqual(1, plan["split_count"])
        self.assertEqual(31, len(plan["eligible"]))
        staged = plan["staged"].document(d.name)
        self.assertEqual(triangles, triangle_records(staged))
        self.assertEqual(plan["eligible"], {key for g in plan["groups"] for key in g["members"]})
        self.assertEqual(31, sum(len(g["members"]) for g in plan["groups"]))
        self.assertTrue(all("附近" in g["name"] or "跨部位" in g["name"] for g in plan["groups"]))
        expected_order = group_order(plan["staged"].workspace, [(g["name"], g["members"]) for g in plan["groups"]])
        self.assertEqual([g["name"] for g in plan["groups"]], [name for name, _ in expected_order])
        feet = next(g for g in plan["groups"] if (d.name, 7) in g["members"])
        foot_pair = [g for g in plan["groups"] if g.get("parent") == feet["parent"]]
        self.assertEqual([2, 2], sorted(len(g["members"]) for g in foot_pair))
        self.assertEqual({(d.name, i) for i in (7, 8, 30, 31)}, {k for g in foot_pair for k in g["members"]})
        self.assertNotIn((d.name, 1), feet["members"])
        legs = next(g for g in plan["groups"] if (d.name, 12) in g["members"])
        leg_pair = [g for g in plan["groups"] if g.get("parent") == legs["parent"]]
        self.assertEqual([6, 6], sorted(len(g["members"]) for g in leg_pair))
        self.assertEqual({11, 12, 13, 14, 15, 16, 18, 19, 25, 26, 27, 28}, {i for g in leg_pair for n, i in g["members"]})
        unpaired = session.prepare_spatial_groups([self.key], pair=False)
        foot_group = next(g for g in unpaired["groups"] if (d.name, 7) in g["members"])
        self.assertNotIn((d.name, 8), foot_group["members"])
        self.assertEqual(source_hash, hashlib.sha256(session.workspace.source.read_bytes()).digest())

    def test_compound_undo_redo_export_and_project_preserve_geometry_and_tags(self):
        session, d = self.session, self.document
        original, triangles = d.bytes(), triangle_records(d)
        session.edit_annotations([self.key], name="别名", note="备注")
        plan = session.prepare_spatial_groups([self.key])
        count = session.apply_spatial_groups(plan)
        self.assertGreater(count, 1)
        after = d.bytes()
        groups = session._collections()
        self.assertEqual(triangles, triangle_records(d))
        self.assertTrue(all(d.meshes[i].visible_condition == 0 for i in range(1, 32)))
        self.assertEqual(31, len(session.annotations))
        session.undo()
        self.assertEqual(original, d.bytes())
        self.assertFalse(session.named_groups)
        self.assertEqual({self.key: {"name": "别名", "note": "备注"}}, session.annotations)
        session.redo()
        self.assertEqual(after, d.bytes())
        self.assertEqual(groups, session.named_groups)
        history = len(session._undo)
        repeated = session.prepare_spatial_groups(plan["eligible"], split=False, include_grouped=True)
        self.assertEqual(0, session.apply_spatial_groups(repeated))
        self.assertEqual(groups, session.named_groups)
        self.assertEqual(history, len(session._undo))
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "layers.json"
            session.save(project)
            opened, _ = EditorSession.open(project)
            self.assertEqual(after, opened.document(d.name).bytes())
            self.assertEqual(groups, opened.named_groups)
            self.assertEqual(session.parent_groups, opened.parent_groups)
            output = session.export(Path(temp) / "layers.zip")
            reopened = EditorSession(ModWorkspace.open(output))
            self.assertEqual(after, reopened.document(d.name).bytes())
            self.assertEqual(groups, reopened.named_groups)
            self.assertEqual(session.parent_groups, reopened.parent_groups)
            self.assertEqual(session.annotations, reopened.annotations)
            session.apply_group(next(iter(groups.values())), 60)
            session.validate_patch()
            self.assertEqual(triangles, triangle_records(d))

    def test_manual_groups_protected_partial_regroup_and_stale_plan_rejected(self):
        session, d = self.session, self.document
        session.edit_named_group("create", "已有外套", [self.key])
        plan = session.prepare_spatial_groups([self.key])
        self.assertFalse(plan["groups"])
        self.assertEqual(1, plan["skipped"])
        self.assertEqual(0, plan["split_count"])
        plan = session.prepare_spatial_groups([self.key], include_grouped=True)
        session.apply_spatial_groups(plan)
        pending = set(plan["eligible"]) - set().union(*[set(g["members"]) for g in plan["groups"] if g["accepted"]])
        self.assertEqual(pending, session.named_groups.get("已有外套", set()))
        stale = session.prepare_spatial_groups(plan["eligible"], split=False, include_grouped=True)
        session.edit_annotations([(d.name, 0)], note="分析后改动")
        before, groups = d.bytes(), session._collections()
        with self.assertRaisesRegex(Mod3Error, "重新生成"):
            session.apply_spatial_groups(stale)
        self.assertEqual(before, d.bytes())
        self.assertEqual(groups, session.named_groups)

    def test_invalid_members_duplicate_names_and_cancel_are_atomic(self):
        session, d = self.session, self.document
        before, history = d.bytes(), len(session._undo)
        with self.assertRaises(Mod3Error):
            session.prepare_spatial_groups([self.key], cancelled=lambda: True)
        plan = session.prepare_spatial_groups([self.key])
        plan["groups"][1]["members"] += plan["groups"][0]["members"]
        with self.assertRaises(Mod3Error):
            session.apply_spatial_groups(plan)
        self.assertEqual(before, d.bytes())
        self.assertEqual(history, len(session._undo))
        plan = session.prepare_spatial_groups([self.key])
        for g in plan["groups"]:
            g["accepted"] = False
        with self.assertRaises(Mod3Error):
            session.apply_spatial_groups(plan)
        self.assertEqual(before, d.bytes())
        plan["groups"][0]["accepted"] = True
        with patch.object(session, "validate_patch", side_effect=Mod3Error("模拟验证失败")):
            with self.assertRaises(Mod3Error):
                session.apply_spatial_groups(plan)
        self.assertEqual(before, d.bytes())
        self.assertFalse(session.named_groups)
        self.assertEqual(history, len(session._undo))

    def test_spatial_buckets_do_not_mix_files_materials_or_layer_ids(self):
        session, d = self.session, self.document
        plan = session.prepare_spatial_groups([self.key])
        stage = plan["staged"]
        stage.document(d.name).set_visible_condition(7, 60)
        groups = suggest_groups(stage.workspace, plan["eligible"])
        foot = next(g for g in groups if (d.name, 7) in g["members"])
        self.assertNotIn((d.name, 8), foot["members"])
        self.assertEqual([60], foot["group_ids"])
        keys = {(doc.name, m.index) for doc, m in stage.workspace.all_meshes()}
        groups = suggest_groups(stage.workspace, keys)
        for group in groups:
            self.assertEqual(1, len({name for name, i in group["members"]}))
            self.assertEqual(1, len({stage.document(name).meshes[i].material_id for name, i in group["members"]}))
            self.assertEqual(1, len({stage.document(name).meshes[i].visible_condition for name, i in group["members"]}))

    def synthetic(self, bounds):
        meshes = []
        for index, (low, high) in enumerate(bounds):
            lo, hi = np.asarray(low, dtype=np.float32), np.asarray(high, dtype=np.float32)
            meshes.append(SimpleNamespace(index=index, vertices=np.array([lo, hi, [lo[0], hi[1], hi[2]]], np.float32),
                                          faces=np.array([[0, 1, 2]], np.int32), material_id=2, visible_condition=0))
        d = SimpleNamespace(name="wst/example.mod3", outfit_id="outfit", equipment_part="wst", meshes=meshes,
                            material_info=lambda mid: SimpleNamespace(name="same"))
        w = ModWorkspace()
        w.documents = [d]
        return w, {(d.name, i) for i in range(len(bounds) - 1)}

    def test_proximity_does_not_chain_across_three_large_anchors(self):
        w, keys = self.synthetic([([0, 50, 0], [8, 51, 1]), ([8.8, 50, 0], [16.8, 51, 1]),
                                  ([17.6, 50, 0], [25.6, 51, 1]), ([0, 0, 0], [0, 100, 0])])
        groups = suggest_groups(w, keys, distance="常规", pair=False)
        self.assertEqual([1, 2], sorted(len(g["members"]) for g in groups))

    def test_equally_close_small_fragment_stays_pending(self):
        w, keys = self.synthetic([([-10, 50, 0], [-1, 51, 1]), ([1, 50, 0], [10, 51, 1]),
                                  ([-.1, 50.4, .4], [.1, 50.6, .6]), ([-5.1, 50.4, .4], [-4.9, 50.6, .6]),
                                  ([0, 0, 0], [0, 100, 0])])
        groups = suggest_groups(w, keys, distance="常规", pair=False)
        ambiguous = next(g for g in groups if ("wst/example.mod3", 2) in g["members"])
        self.assertTrue(ambiguous["review"])
        self.assertFalse(ambiguous["accepted"])
        nearby = next(g for g in groups if ("wst/example.mod3", 3) in g["members"])
        self.assertIn(("wst/example.mod3", 0), nearby["members"])

    def test_position_hints_and_order_are_independent_of_input_numbering(self):
        w, keys = self.synthetic([([-1, 5, 0], [1, 8, 1]), ([27, 60, 0], [29, 62, 1]),
                                  ([-1, 90, 0], [1, 93, 1]), ([-30, 0, 0], [30, 100, 1])])
        d = w.documents[0]
        frame = (np.array([-30, 0, 0]), np.array([30, 100, 1]))
        self.assertEqual(["脚部附近", "手部附近", "头部附近"],
                         [position_hint(d, m, frame) for m in d.meshes[:3]])
        records = [(str(i), {(d.name, i)}) for i in (0, 1, 2)]
        self.assertEqual(["2", "1", "0"], [name for name, _ in group_order(w, records)])


if __name__ == "__main__":
    unittest.main()
