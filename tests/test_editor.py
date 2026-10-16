import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from test_core import minimal_mod3, SAMPLE
from mod3 import Mod3Document, ModWorkspace, Mod3Error
from materials import material_hash, read_mrl3
from renderer import SoftwareRenderer, LAYER_COLORS
from session import EditorSession


def workspace_fixture():
    workspace = ModWorkspace()
    workspace.documents = [Mod3Document("body/body.mod3", minimal_mod3()),
                           Mod3Document("wst/wst.mod3", minimal_mod3())]
    return workspace


def mrl_fixture():
    data = bytearray(384)
    struct.pack_into("<I12sIIQQ", data, 0, 0x004C524D, b"\0" * 12, 1, 1, 40, 312)
    path = b"pl/test/body_BM"
    data[56:56 + len(path)] = path
    struct.pack_into("<I", data, 316, material_hash("body_skin"))
    struct.pack_into("<I", data, 328, 16)
    data[334] = 2
    struct.pack_into("<I", data, 360, 368)
    data[368] = 2
    struct.pack_into("<I", data, 376, 1)
    return data


class EditorTests(unittest.TestCase):
    def test_batch_undo_redo_preserves_unrelated_mesh_and_redo_branch(self):
        workspace = workspace_fixture()
        extra = Mod3Document("helm/helm.mod3", minimal_mod3())
        workspace.documents.append(extra)
        session = EditorSession(workspace)
        keys = {(document.name, 0) for document in workspace.documents[:2]}
        self.assertEqual(2, session.apply_group(keys, 60))
        self.assertEqual(0, extra.meshes[0].visible_condition)
        self.assertEqual(2, len(session.changes()))
        session.validate_patch()
        self.assertTrue(session.undo())
        self.assertTrue(all(document.meshes[0].visible_condition == 0 for document in workspace.documents))
        self.assertTrue(session.redo())
        session.undo()
        session.apply_group(keys, 61)
        self.assertFalse(session.can_redo)
        before = [document.bytes() for document in workspace.documents]
        with self.assertRaises(ValueError):
            session.apply_values([(workspace.documents[0].name, 0, 62), (extra.name, 99, 62)])
        self.assertEqual(before, [document.bytes() for document in workspace.documents])

    def test_project_round_trip_and_changed_source_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            session = EditorSession(ModWorkspace.open(source))
            session.apply_group({("body.mod3", 0)}, 60)
            project = Path(temp) / "layers.mhwlayer.json"
            view = {"selected": [["body.mod3", 0]], "isolate": True, "yaw": 0.7}
            session.save(project, view)
            self.assertFalse(session.project_dirty)
            reopened, restored_view = EditorSession.open(project)
            self.assertEqual(60, reopened.workspace.documents[0].meshes[0].visible_condition)
            self.assertEqual(0, reopened.workspace.documents[0].original_group(0))
            self.assertEqual(view, restored_view)
            self.assertFalse(reopened.project_dirty)
            reopened.apply_group({("body.mod3", 0)}, 61)
            self.assertTrue(reopened.project_dirty)
            damaged = bytearray(source.read_bytes())
            damaged[319] = 1
            source.write_bytes(damaged)
            with self.assertRaises(Mod3Error):
                EditorSession.open(project)

    def test_patch_guard_rejects_non_group_changes(self):
        workspace = workspace_fixture()
        session = EditorSession(workspace)
        workspace.documents[0]._data[319] = 1
        with self.assertRaises(Mod3Error):
            session.validate_patch()

    def test_mrl_binding_is_one_based_and_rejects_invalid_indices(self):
        data = mrl_fixture()
        self.assertEqual(("pl/test/body_BM",), read_mrl3(data)[material_hash("body_skin")])
        struct.pack_into("<I", data, 376, 2)
        with self.assertRaises(ValueError):
            read_mrl3(data)
        with self.assertRaises(ValueError):
            read_mrl3(b"MRL")

    @unittest.skipUnless(SAMPLE.exists(), "sample not available")
    def test_sample_names_match_mrl_hash_and_resources(self):
        workspace = ModWorkspace.open(SAMPLE)
        self.assertFalse(workspace.errors)
        body = next(document for document in workspace.documents if document.equipment_part == "body")
        info = body.material_info(body.material_names.index("Shinano_body"))
        self.assertEqual("已匹配 MRL3", info.status)
        self.assertEqual(0xE24E153B, info.name_hash)
        self.assertTrue(info.textures)
        self.assertTrue(all("/" in name or "\\" in name for name in info.textures))

    def test_isolation_hidden_parts_health_and_focus_are_independent(self):
        workspace = workspace_fixture()
        first, second = workspace.documents
        first.meshes[0].vertices *= .1
        first.meshes[0].vertices[:, 0] += 10
        first.set_visible_condition(0, 60)
        renderer = SoftwareRenderer()
        renderer.selected_keys = {(first.name, 0), (second.name, 0)}
        renderer.isolate_selected = True
        renderer.health_enabled = False
        renderer.health = 0
        self.assertEqual(2, len(renderer.visible_entries(workspace)))
        renderer.fit(workspace, {(first.name, 0)})
        np.testing.assert_allclose(renderer.camera_center, [10, 0, 0], atol=1e-6)
        self.assertAlmostEqual(.2, renderer.camera_span, places=5)
        renderer.health_enabled = True
        self.assertEqual(1, len(renderer.visible_entries(workspace)))
        renderer.health_enabled = False
        renderer.hidden_keys = {(second.name, 0)}
        self.assertEqual(1, len(renderer.visible_entries(workspace)))
        renderer.enabled_parts = {"wst"}
        renderer.render(workspace, (300, 300))
        self.assertIsNone(renderer.pick(150, 150))
        self.assertEqual(60, first.meshes[0].visible_condition)

    def test_layer_colors_silhouette_and_ghost_picking(self):
        workspace = workspace_fixture()
        first, second = workspace.documents
        second.meshes[0].vertices[:, 2] = 1
        renderer = SoftwareRenderer()
        for group in (0, 60, 61, 62):
            first.set_visible_condition(0, group)
            self.assertEqual(LAYER_COLORS[group], renderer.mesh_color(first, first.meshes[0], 0))
        first.set_visible_condition(0, 777)
        self.assertEqual((230, 86, 88), renderer.mesh_color(first, first.meshes[0], 0))
        renderer.selected_keys = {(first.name, 0)}
        renderer.ghost_others = True
        image = renderer.render(workspace, (300, 300))
        self.assertEqual((second.name, 0), renderer.pick(150, 150))
        pixels = np.array(image)
        self.assertTrue(np.any(np.all(pixels == [255, 224, 60], axis=2)))
        self.assertEqual({(first.name, 0)}, renderer.selected_keys)


if __name__ == "__main__":
    unittest.main()
