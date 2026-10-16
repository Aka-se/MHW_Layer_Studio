import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from mod3 import ModWorkspace, Mod3Document, Mod3Error, BASE_HEADER
from session import EditorSession
from splitting import components, rebuild_partitions
from lod_matching import surface_distances, match_island
from mesh_features import feature_profile
from renderer import SoftwareRenderer
from test_split import triangle_records

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Skimpy Master Garon Armor-2039-1-1-1579667215.zip"


def boxes(document):
    data = document.bytes()
    values = BASE_HEADER.unpack_from(data)
    start = values[16] + len(document.meshes) * 80
    count = struct.unpack_from("<I", data, start)[0]
    cursor = start + 4
    assigned = []
    for mesh in document.meshes:
        size = data[mesh.header_offset + 41] * 144
        assigned.append(data[cursor:cursor + size])
        cursor += size
    end = start + 4 + count * 144
    return assigned, data[cursor:end], data[end:values[17]]


def confirm_candidates(plan):
    for group in list(plan["groups"]):
        if not group["accepted"]:
            target = next(g for g in plan["groups"] if g.get("reference") == group["candidate_reference"])
            target["members"].extend(group["members"])
            plan["groups"].remove(group)


class SyncTests(unittest.TestCase):
    def test_surface_projection_and_uv_interpolation(self):
        triangles = np.array([[[0, 0, 0], [2, 0, 0], [0, 2, 0]]], np.float64)
        uvs = np.array([[[0, 0], [1, 0], [0, 1]]], np.float64)
        distances, projected = surface_distances(np.array([[.5, .5, 3], [3, 0, 0]], np.float64), triangles, uvs)
        np.testing.assert_allclose(distances, [3, 1], atol=1e-8)
        np.testing.assert_allclose(projected, [[.25, .25], [1, 0]], atol=1e-8)
        collapsed = np.array([[[0, 0, 0]] * 3], np.float64)
        distances, _ = surface_distances(np.array([[1, 0, 0]], np.float64), collapsed, np.empty((0, 3, 2)))
        self.assertEqual(1, distances[0])

    def test_overlapping_references_and_bridging_geometry_stay_unassigned(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
        mesh = SimpleNamespace(vertices=vertices, faces=np.array([[0, 1, 2]], np.int32), uvs=None, bone_ids=None, bone_weights=None)
        refs = [{"key": ("file", i), "triangles": vertices[mesh.faces].astype(np.float64),
                 "uv_triangles": np.empty((0, 3, 2)), "features": feature_profile(mesh)} for i in range(2)]
        found, _, _ = match_island(mesh, np.array([0]), refs, 1)
        self.assertIsNone(found)
        refs[1]["triangles"] = refs[1]["triangles"] + [10, 0, 0]
        mesh.vertices = np.array([[0, 0, 0], [11, 0, 0], [0, 1, 0]], np.float32)
        found, _, _ = match_island(mesh, np.array([0]), refs, 12)
        self.assertIsNone(found)

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_surplus_boxes_and_padding_survive_safe_split(self):
        w = ModWorkspace.open(SOURCE)
        d = next(d for d in w.documents if d.equipment_part == "body")
        original, surplus, padding = boxes(d)
        self.assertGreater(len(surplus), 0)
        islands = components(d.meshes[0])
        self.assertGreater(len(islands), 1)
        baseline = triangle_records(d)
        data, _, _ = rebuild_partitions(d, {0: islands})
        parsed = Mod3Document(d.name, data)
        assigned, tail, gap = boxes(parsed)
        self.assertEqual(surplus, tail)
        self.assertEqual(padding, gap)
        self.assertEqual([original[0]] * len(islands) + original[1:], assigned)
        self.assertEqual(baseline, triangle_records(parsed))
        # Nonzero pre-vertex padding is opaque, too.
        h = list(BASE_HEADER.unpack_from(d.bytes()))
        added = b'opaque-padding'
        raw = bytearray(d.bytes()[:h[17]] + added + d.bytes()[h[17]:])
        for i in (17, 18, 19):
            h[i] += len(added)
        BASE_HEADER.pack_into(raw, 0, *h)
        padded = Mod3Document(d.name, bytes(raw))
        data, _, _ = rebuild_partitions(padded, {0: islands})
        self.assertEqual(added, boxes(Mod3Document(d.name, data))[2])

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_invalid_partitions_do_not_drop_duplicate_or_change_faces(self):
        d = next(d for d in ModWorkspace.open(SOURCE).documents if d.equipment_part == "wst")
        before = d.bytes()
        count = d.meshes[4].triangle_count
        cases = [{4: [np.arange(count - 1)]}, {4: [np.arange(count), np.array([0])]},
                 {4: [np.arange(count, dtype=np.float64)]}, {4: [np.array([-1])]},
                 {4: [np.array([], np.int32)]}, {999: [np.array([0])]}]
        for invalid in cases:
            with self.assertRaises(Mod3Error):
                rebuild_partitions(d, invalid)
            self.assertEqual(before, d.bytes())

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_garon_review_safe_export_replay_undo_and_all_lod_health(self):
        session = EditorSession(ModWorkspace.open(SOURCE))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        before = {doc.name: doc.bytes() for doc in session.workspace.documents}
        source_sha = hashlib.sha256(SOURCE.read_bytes()).digest()
        triangles = triangle_records(d)
        plan = session.prepare_lod_sync([(d.name, 0)])
        self.assertEqual(9, len(d.meshes))
        self.assertEqual(27, len(plan["staged"].document(d.name).meshes))
        self.assertEqual(3, sum(not g["accepted"] for g in plan["groups"]))
        self.assertEqual(triangles, triangle_records(plan["staged"].document(d.name)))
        with self.assertRaisesRegex(Mod3Error, "待确认"):
            session.apply_spatial_groups(plan)
        self.assertEqual(before, {doc.name: doc.bytes() for doc in session.workspace.documents})
        self.assertFalse(session.named_groups)
        confirm_candidates(plan)
        self.assertEqual(4, session.apply_spatial_groups(plan))
        self.assertEqual(27, len(d.meshes))
        old_document = Mod3Document(d.name, plan["before"][d.name][0])
        for mesh in d.meshes:
            origin = plan["origins"][(d.name, mesh.index)][1]
            old = old_document.meshes[origin]
            self.assertEqual((old.lod, old.shadow_flags, old.material_id), (mesh.lod, mesh.shadow_flags, mesh.material_id))
        groups = session._collections()
        group = plan["groups"][0]
        session.apply_group(group["members"], 60)
        session.validate_patch()
        renderer = SoftwareRenderer()
        renderer.selected_keys = set(group["members"])
        renderer.isolate_selected = True
        for bit in (1, 2, 4, 8, 16, 32):
            renderer.lod_bit = bit
            renderer.health = 100
            self.assertTrue(renderer.visible_entries(session.workspace))
            renderer.health = 80
            self.assertFalse(renderer.visible_entries(session.workspace))
            renderer.health = 81
            self.assertTrue(renderer.visible_entries(session.workspace))
        with tempfile.TemporaryDirectory() as temp:
            archive = session.export(Path(temp) / "sync.zip")
            restored = EditorSession(ModWorkspace.open(archive))
            self.assertEqual(d.bytes(), restored.document(d.name).bytes())
            self.assertEqual(groups, restored.named_groups)
            project = Path(temp) / "layers.json"
            session.save(project)
            saved = json.loads(project.read_text(encoding="utf-8"))
            self.assertEqual(12, saved["version"])
            restored, _ = EditorSession.open(project)
            self.assertEqual(d.bytes(), restored.document(d.name).bytes())
            self.assertEqual(groups, restored.named_groups)
            # A corrupted face-assignment recipe cannot be applied to the source.
            record = next(r for r in saved["documents"] if r["name"] == d.name)
            record["splits"][-1]["partitions"][0]["faces"][0].append(0)
            project.write_text(json.dumps(saved), encoding="utf-8")
            with self.assertRaises(Mod3Error):
                EditorSession.open(project)
        session.undo()
        session.undo()
        self.assertEqual(before, {doc.name: doc.bytes() for doc in session.workspace.documents})
        self.assertFalse(session.named_groups)
        session.redo()
        session.redo()
        self.assertEqual(27, len(d.meshes))
        self.assertEqual(groups, session.named_groups)
        self.assertEqual(source_sha, hashlib.sha256(SOURCE.read_bytes()).digest())

    @unittest.skipUnless(SOURCE.exists(), "optional local integration sample not available")
    def test_cancel_existing_groups_and_stale_review_are_read_only(self):
        session = EditorSession(ModWorkspace.open(SOURCE))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        key = (d.name, 0)
        with self.assertRaisesRegex(Mod3Error, "取消"):
            session.prepare_lod_sync([key], cancelled=lambda: True)
        session.edit_named_group("create", "原组", [key])
        with self.assertRaisesRegex(Mod3Error, "已有组"):
            session.prepare_lod_sync([key])
        plan = session.prepare_lod_sync([key], include_grouped=True)
        confirm_candidates(plan)
        session.edit_annotations([key], note="分析后的修改")
        before = d.bytes()
        with self.assertRaisesRegex(Mod3Error, "重新生成"):
            session.apply_spatial_groups(plan)
        self.assertEqual(before, d.bytes())
        self.assertEqual({key}, session.named_groups["原组"])


if __name__ == "__main__":
    unittest.main()
