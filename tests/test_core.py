import hashlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mod3 import Mod3Document, ModWorkspace, generate_health_rules
from renderer import SoftwareRenderer, point_in_triangle


SAMPLE = Path(__file__).resolve().parents[1] / "暴雨魔女.zip"


def minimal_mod3():
    mesh_offset, vertex_offset, face_offset, trail_offset = 320, 400, 436, 442
    data = bytearray(trail_offset)
    struct.pack_into(
        "<IBBHHHiiiiiQQQQQQQQQQ",
        data,
        0,
        0x00444F4D, 237, 0, 0, 1, 0,
        3, 3, 0, 36, 0,
        0, 0, 0, 320, 320, mesh_offset, vertex_offset, face_offset, trail_offset, 0,
    )
    struct.pack_into("<HHHHiHBB", data, mesh_offset, 19, 3, 0, 0, 65535, 0, 12, 0)
    struct.pack_into(
        "<IIIIII", data, mesh_offset + 16,
        0, 0, 0xA756F2F9, 0, 3, 0,
    )
    struct.pack_into("<fff", data, vertex_offset, -1.0, -1.0, 0.0)
    struct.pack_into("<fff", data, vertex_offset + 12, 1.0, -1.0, 0.0)
    struct.pack_into("<fff", data, vertex_offset + 24, 0.0, 1.0, 0.0)
    struct.pack_into("<HHH", data, face_offset, 0, 1, 2)
    return bytes(data)


class CoreTests(unittest.TestCase):
    def test_export_refuses_original_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "original.mod3"
            source.write_bytes(minimal_mod3())
            workspace = ModWorkspace.open(source)
            workspace.documents[0].set_visible_condition(0, 60)
            with self.assertRaises(ValueError):
                workspace.export(source)
            existing = Path(temp) / "existing.mod3"
            existing.write_bytes(b"keep")
            with self.assertRaises(ValueError):
                workspace.export(existing)
            self.assertEqual(b"keep", existing.read_bytes())
            self.assertEqual(minimal_mod3(), source.read_bytes())

    def test_folder_export_keeps_companions_and_tracks_pending_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "body.mod3").write_bytes(minimal_mod3())
            (source / "body.tex").write_bytes(b"texture")
            workspace = ModWorkspace.open(source)
            workspace.documents[0].set_visible_condition(0, 60)
            with self.assertRaises(ValueError):
                workspace.export(source / "nested")
            self.assertFalse((source / "nested").exists())
            self.assertTrue(workspace.has_unexported_changes)
            output = workspace.export(Path(temp) / "copy")
            self.assertEqual(b"texture", (output / "body.tex").read_bytes())
            self.assertFalse(workspace.has_unexported_changes)
            workspace.documents[0].reset()
            self.assertTrue(workspace.has_unexported_changes)

    def test_preview_keeps_frame_color_and_recovers_after_healing(self):
        workspace = ModWorkspace()
        decoration = Mod3Document("decoration.mod3", minimal_mod3())
        base = Mod3Document("base.mod3", minimal_mod3())
        decoration.set_visible_condition(0, 60)
        decoration.meshes[0].vertices *= 2
        base.meshes[0].vertices[:, 2] = 1
        workspace.documents = [decoration, base]
        renderer = SoftwareRenderer()
        full = renderer.render(workspace, (240, 180))
        base_full = next(face[3] for face in renderer._pick_faces if face[1] == "base.mod3")
        color_full = full.getpixel((120, 90))
        renderer.health = 80
        low = renderer.render(workspace, (240, 180))
        self.assertEqual((240, 180), low.size)
        self.assertTrue(all(face[1] == "base.mod3" for face in renderer._pick_faces))
        np.testing.assert_array_equal(base_full, renderer._pick_faces[0][3])
        self.assertEqual(color_full, low.getpixel((120, 90)))
        renderer.health = 81
        renderer.render(workspace, (240, 180))
        self.assertTrue(any(face[1] == "decoration.mod3" for face in renderer._pick_faces))
        for group, threshold in ((60, 80), (61, 60), (62, 40)):
            decoration.set_visible_condition(0, group)
            renderer.health = threshold
            self.assertFalse(renderer.visible_for_health(decoration.meshes[0]))
            renderer.health = threshold + 1
            self.assertTrue(renderer.visible_for_health(decoration.meshes[0]))
        self.assertFalse(point_in_triangle(50, 50, np.array([[0, 0], [0, 0], [0, 0]])))

    def test_minimal_document_round_trip(self):
        document = Mod3Document("fixture.mod3", minimal_mod3())
        self.assertEqual(1, len(document.meshes))
        self.assertEqual(1, document.meshes[0].triangle_count)
        document.set_visible_condition(0, 61)
        reopened = Mod3Document("fixture.mod3", document.bytes())
        self.assertEqual(61, reopened.meshes[0].visible_condition)

    @unittest.skipUnless(SAMPLE.exists(), "sample archive is not available")
    def test_sample_inventory(self):
        workspace = ModWorkspace.open(SAMPLE)
        self.assertEqual(5, len(workspace.documents))
        self.assertEqual(36, workspace.mesh_count)
        counts = {Path(document.name).stem: len(document.meshes) for document in workspace.documents}
        self.assertEqual(17, counts["f_body105_0000"])
        self.assertEqual(11, counts["f_arm105_0000"])

    @unittest.skipUnless(SAMPLE.exists(), "sample archive is not available")
    def test_patch_changes_only_condition_field(self):
        workspace = ModWorkspace.open(SAMPLE)
        document = next(d for d in workspace.documents if "/body/" in d.name)
        before = document.bytes()
        offset = document.meshes[0].visible_condition_offset
        document.set_visible_condition(0, 60)
        after = document.bytes()
        differences = [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
        self.assertEqual([offset], differences)
        reopened = Mod3Document(document.name, after)
        self.assertEqual(60, reopened.meshes[0].visible_condition)
        self.assertEqual(17, len(reopened.meshes))

    @unittest.skipUnless(SAMPLE.exists(), "sample archive is not available")
    def test_zip_export_preserves_other_entries(self):
        workspace = ModWorkspace.open(SAMPLE)
        target_document = next(d for d in workspace.documents if "/body/" in d.name)
        target_document.set_visible_condition(0, 60)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output.zip"
            workspace.export(output)
            reopened = ModWorkspace.open(output)
            changed = next(d for d in reopened.documents if "/body/" in d.name)
            self.assertEqual(60, changed.meshes[0].visible_condition)
            with zipfile.ZipFile(SAMPLE) as source, zipfile.ZipFile(output) as result:
                unchanged_name = next(n for n in source.namelist() if n.endswith(".tex"))
                self.assertEqual(
                    hashlib.sha256(source.read(unchanged_name)).digest(),
                    hashlib.sha256(result.read(unchanged_name)).digest(),
                )

    def test_generated_rules_are_reversible(self):
        rules = generate_health_rules()
        self.assertIn("Id=60", rules)
        self.assertIn("HealthPercentAbove=80", rules)
        self.assertNotIn("LatchUntilReset=1", rules)


if __name__ == "__main__":
    unittest.main()
