import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np

from attachment_groups import propose_attachments
from mod3 import ModWorkspace, Mod3Error


def part(index, points, material=0, group=0, lod=1):
    vertices = np.asarray(points, np.float32)
    return SimpleNamespace(index=index, vertices=vertices, faces=np.array([[0, 1, 2]], np.int32),
                           material_id=material, visible_condition=group, lod=lod,
                           bone_ids=np.ones((3, 1), np.uint8), bone_weights=np.ones((3, 1), np.float32),
                           uvs=np.zeros((3, 2), np.float32))


def fixture():
    seed = part(0, [[0, 0, 0], [10, 0, 0], [0, 10, 0]])
    near = part(1, [[10.2, 0, 0], [10.3, 0, 0], [10.2, .1, 0]])
    chained = part(2, [[11.7, 0, 0], [11.8, 0, 0], [11.7, .1, 0]])
    frame = part(3, [[0, 0, 0], [0, 100, 0], [0, 0, 0]])
    document = SimpleNamespace(name="wst/example.mod3", outfit_id="outfit", equipment_part="wst",
                               meshes=[seed, near, chained, frame])
    workspace = ModWorkspace()
    workspace.documents = [document]
    return workspace, document, {(document.name, i) for i in range(3)}


class AttachmentTests(unittest.TestCase):
    def test_range_uses_fixed_seed_without_chaining_and_is_read_only(self):
        w, d, keys = fixture()
        before = [m.vertices.copy() for m in d.meshes]
        result = propose_attachments(w, {(d.name, 0)}, keys)
        self.assertEqual(1.5, result["limit"])
        self.assertEqual([(d.name, 1)], [r["key"] for r in result["candidates"]])
        self.assertTrue(result["candidates"][0]["recommended"])
        wider = propose_attachments(w, {(d.name, 0)}, keys, percent=2)
        self.assertEqual(2, len(wider["candidates"]))
        for m, original in zip(d.meshes, before):
            np.testing.assert_array_equal(original, m.vertices)
            self.assertEqual(0, m.visible_condition)

    def test_material_opt_in_is_pending_and_group_lod_file_boundaries_hold(self):
        w, d, keys = fixture()
        d.meshes[1].material_id = 2
        self.assertFalse(propose_attachments(w, {(d.name, 0)}, keys)["candidates"])
        result = propose_attachments(w, {(d.name, 0)}, keys, other_materials=True)
        self.assertEqual(1, len(result["candidates"]))
        self.assertFalse(result["candidates"][0]["recommended"])
        self.assertIn("跨材质", result["candidates"][0]["reason"])
        d.meshes[1].material_id = 0
        for field, value in (("visible_condition", 60), ("lod", 2)):
            setattr(d.meshes[1], field, value)
            self.assertFalse(propose_attachments(w, {(d.name, 0)}, keys)["candidates"])
            setattr(d.meshes[1], field, 0 if field == "visible_condition" else 1)
        other = SimpleNamespace(name="wst/other.mod3", outfit_id="outfit", equipment_part="wst", meshes=[part(0, d.meshes[1].vertices)])
        w.documents.append(other)
        result = propose_attachments(w, {(d.name, 0)}, keys | {(other.name, 0)})
        self.assertEqual([(d.name, 1)], [row["key"] for row in result["candidates"]])

    def test_unknown_or_conflicting_bones_do_not_auto_check(self):
        w, d, keys = fixture()
        d.meshes[1].bone_ids[:] = 2
        result = propose_attachments(w, {(d.name, 0)}, keys)
        self.assertFalse(result["candidates"][0]["recommended"])
        self.assertIn("差异", result["candidates"][0]["reason"])
        result = propose_attachments(w, {(d.name, 0)}, keys, use_features=False)
        self.assertFalse(result["candidates"][0]["recommended"])
        self.assertIn("不足", result["candidates"][0]["reason"])

    def test_invalid_seeds_range_and_cancel_are_rejected(self):
        w, d, keys = fixture()
        for percent in (0, 6, float("nan")):
            with self.assertRaises(Mod3Error):
                propose_attachments(w, {(d.name, 0)}, keys, percent=percent)
        d.meshes[1].visible_condition = 61
        with self.assertRaises(Mod3Error):
            propose_attachments(w, {(d.name, 0), (d.name, 1)}, keys)
        with self.assertRaisesRegex(Mod3Error, "取消"):
            propose_attachments(w, {(d.name, 0)}, keys, cancelled=lambda: True)

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "Kuroki-EX2.zip").exists(),
                         "optional local integration sample not available")
    def test_review_existing_manual_groups_does_not_regroup_until_confirmed(self):
        from pathlib import Path
        from session import EditorSession
        source = Path(__file__).resolve().parents[1] / "Kuroki-EX2.zip"
        session = EditorSession(ModWorkspace.open(source))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        session.split_mesh(d.name, 1)
        original = d.bytes()
        shoes = {(d.name, 7), (d.name, 8)}
        session.edit_named_group("create", "手工鞋组", shoes)
        session.edit_named_group("create", "保留的衣物组", {(d.name, 1)})
        before = session._collections()
        history = len(session._undo)
        plan = session.prepare_attachment_review({(d.name, m.index) for m in d.meshes})
        self.assertEqual(0, plan["split_count"])
        self.assertEqual(original, d.bytes())
        self.assertEqual(before, session.named_groups)
        self.assertEqual(history, len(session._undo))
        self.assertFalse(any(g["accepted"] for g in plan["groups"]))
        target = next(g for g in plan["groups"] if g["name"] == "手工鞋组")
        target["members"].append((d.name, 30))
        target["accepted"] = True
        self.assertEqual(1, session.apply_spatial_groups(plan))
        self.assertEqual(shoes | {(d.name, 30)}, session.named_groups["手工鞋组"])
        self.assertEqual(before["保留的衣物组"], session.named_groups["保留的衣物组"])
        self.assertEqual(original, d.bytes())
        session.undo()
        self.assertEqual(before, session.named_groups)
        session.redo()
        self.assertEqual(original, d.bytes())


if __name__ == "__main__":
    unittest.main()
