import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from mod3 import BASE_HEADER, Mod3Document, ModWorkspace, Mod3Error
from session import EditorSession
from splitting import components

ROOT = Path(__file__).resolve().parents[1]


def triangle_records(document):
    """Compare opaque UV/normal/weight records at each oriented triangle corner."""
    source = document.bytes()
    header = BASE_HEADER.unpack_from(source)
    result = Counter()
    for mesh in document.meshes:
        sub, local, _, offset, count, base = struct.unpack_from("<IIIIII", source, mesh.header_offset + 16)
        indices = np.frombuffer(source, dtype="<u2", count=count, offset=header[18] + offset * 2)
        adjustment = max(0, sub - int(indices.min())) if len(indices) else 0
        start = header[17] + local + mesh.block_size * (sub + base - adjustment)
        records = [source[start + i * mesh.block_size:start + (i + 1) * mesh.block_size] for i in range(mesh.vertex_count)]
        for face in mesh.faces:
            payload = struct.pack("<HI", mesh.material_id, mesh.block_type) + b"".join(records[int(i)] for i in face)
            result[hashlib.sha256(payload).digest()] += 1
    return result


class SplitTests(unittest.TestCase):
    def test_near_seams_stay_in_one_component_without_moving_vertices(self):
        # The seam straddles a spatial cell boundary, with separate UV records.
        points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                           [1.000002, 0, 0], [1, 1, 0], [0, 1.000002, 0]], dtype=np.float32)
        mesh = SimpleNamespace(vertices=points, faces=np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32))
        original = points.copy()
        self.assertEqual(1, len(components(mesh)))
        np.testing.assert_array_equal(original, mesh.vertices)
        mesh.vertices = points.copy()
        mesh.vertices[3, 0] += .001
        mesh.vertices[5, 1] += .001
        self.assertEqual(2, len(components(mesh)))

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
    def test_ex2_wst_seams_do_not_create_triangle_shards(self):
        workspace = ModWorkspace.open(ROOT / "Kuroki-EX2.zip")
        mesh = next(d for d in workspace.documents if d.equipment_part == "wst").meshes[1]
        parts = components(mesh)
        self.assertEqual(31, len(parts))
        self.assertEqual(mesh.triangle_count, sum(len(part) for part in parts))
        self.assertGreaterEqual(min(len(part) for part in parts), 28)

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
    def test_legacy_split_project_keeps_mesh_members_and_group_ids(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        document = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        self.assertEqual(68, session.split_mesh(document.name, 1, "exact"))
        keys = {(document.name, 13), (document.name, 38)}
        session.edit_named_group("create", "旧项目部件", keys)
        session.apply_group(keys, 62)
        before = document.bytes()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.json"
            session.save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["version"] = 3
            for record in data["documents"]:
                record["splits"] = [recipe["mesh"] for recipe in record["splits"]]
            path.write_text(json.dumps(data), encoding="utf-8")
            restored, _ = EditorSession.open(path)
            self.assertEqual(before, restored.document(document.name).bytes())
            self.assertEqual(session.named_groups, restored.named_groups)
            restored.save(path)
            restored_again, _ = EditorSession.open(path)
            self.assertEqual(before, restored_again.document(document.name).bytes())
            data = json.loads(path.read_text(encoding="utf-8"))
            next(r for r in data["documents"] if r["name"] == document.name)["splits"] = [{"mesh": 1, "mode": "unknown"}]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(Mod3Error):
                EditorSession.open(path)

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "optional local integration sample not available")
    def test_ex2_preserves_geometry_vertex_attributes_and_companions(self):
        sample = ROOT / "Kuroki-EX2.zip"
        before_hash = hashlib.sha256(sample.read_bytes()).digest()
        workspace = ModWorkspace.open(sample)
        session = EditorSession(workspace)
        document = next(d for d in workspace.documents if d.equipment_part == "wst")
        original = document.bytes()
        baseline = triangle_records(document)
        parts = components(document.meshes[1])
        self.assertGreater(len(parts), 1)
        count = session.split_mesh(document.name, 1)
        self.assertEqual(count, len(parts))
        self.assertEqual(baseline, triangle_records(document))
        self.assertEqual(BASE_HEADER.unpack_from(original)[7], sum(m.index_count for m in document.meshes))
        session.apply_group({(document.name, 1), (document.name, 2)}, 60)
        session.validate_patch()
        split_bytes = document.bytes()
        session.undo()
        session.undo()
        self.assertEqual(original, document.bytes())
        session.redo()
        session.redo()
        self.assertEqual(split_bytes, document.bytes())
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "split.json"
            session.save(project)
            reopened, _ = EditorSession.open(project)
            self.assertEqual(split_bytes, reopened.document(document.name).bytes())
            archive = workspace.export(Path(folder) / "split.zip")
            exported = ModWorkspace.open(archive)
            self.assertEqual(split_bytes, next(d for d in exported.documents if d.name == document.name).bytes())
            with zipfile.ZipFile(sample) as source, zipfile.ZipFile(archive) as output:
                for name in source.namelist():
                    if name != document.name:
                        self.assertEqual(source.read(name), output.read(name), name)
        self.assertEqual(before_hash, hashlib.sha256(sample.read_bytes()).digest())
        document._data[document.meshes[1].header_offset + 7] ^= 1
        with self.assertRaises(Mod3Error):
            session.validate_patch()

    @unittest.skipUnless((ROOT / "Kuroki-Defult.zip").exists(), "optional local integration sample not available")
    def test_connected_mesh_is_rejected_without_mutation(self):
        workspace = ModWorkspace.open(ROOT / "Kuroki-Defult.zip")
        session = EditorSession(workspace)
        document = next(d for d in workspace.documents if d.equipment_part == "wst")
        before = document.bytes()
        with self.assertRaises(Mod3Error):
            session.split_mesh(document.name, 0)
        self.assertEqual(before, document.bytes())
        self.assertFalse(session.can_undo)


if __name__ == "__main__":
    unittest.main()
