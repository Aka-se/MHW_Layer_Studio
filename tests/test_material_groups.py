import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from mod3 import BASE_HEADER, Mod3Document, ModWorkspace, Mod3Error
from session import EditorSession
from test_core import minimal_mod3

ROOT = Path(__file__).resolve().parents[1]


def material_fixture(count=120):
    """Many valid mesh records sharing two local material IDs with identical names."""
    values = list(BASE_HEADER.unpack_from(minimal_mod3()))
    material_offset = 320
    mesh_offset = material_offset + 256
    vertex_offset = mesh_offset + count * 80
    face_offset = vertex_offset + count * 36
    values[4:10] = [count, 2, count * 3, count * 3, 0, count * 36]
    values[15:20] = [material_offset, mesh_offset, vertex_offset, face_offset, face_offset + count * 6]
    data = bytearray(values[19])
    BASE_HEADER.pack_into(data, 0, *values)
    for i in (0, 1):
        data[material_offset + i * 128:material_offset + i * 128 + 4] = b"EX2\0"
    for i in range(count):
        offset = mesh_offset + i * 80
        data[offset:offset + 80] = minimal_mod3()[320:400]
        struct.pack_into("<HH", data, offset + 4, 60 if i % 3 else 62, i % 2)
        struct.pack_into("<I", data, offset + 20, i * 36)
        struct.pack_into("<I", data, offset + 28, i * 3)
        data[vertex_offset + i * 36:vertex_offset + (i + 1) * 36] = minimal_mod3()[400:436]
        data[face_offset + i * 6:face_offset + (i + 1) * 6] = minimal_mod3()[436:442]
    return bytes(data)


def workspace_fixture():
    workspace = ModWorkspace()
    workspace.documents = [Mod3Document(f"nativePC/pl/f_equip/{suit}/{part}/f_{part}075_0000.mod3", material_fixture())
                           for suit, part in (("pl075_0000", "wst"), ("pl075_0000", "body"), ("pl029_0001", "wst"))]
    return workspace


class MaterialGroupTests(unittest.TestCase):
    def test_hundreds_of_parts_group_by_local_material_id_without_touching_bytes(self):
        session = EditorSession(workspace_fixture())
        keys = {(d.name, m.index) for d, m in session.workspace.all_meshes()}
        original = [d.bytes() for d in session.workspace.documents]
        annotation_key = sorted(keys)[0]
        session.edit_annotations([annotation_key], name="换过名字", note="手动备注")
        history = len(session._undo)
        plan = session.plan_material_groups(keys)
        self.assertFalse(session.named_groups)
        self.assertEqual(history, len(session._undo))
        self.assertEqual(6, len(plan["groups"]))
        self.assertTrue(all(len(g["members"]) == 60 for g in plan["groups"]))
        self.assertTrue(all(g["group_ids"] == [60, 62] for g in plan["groups"]))
        session.apply_material_groups(keys)
        self.assertEqual(6, len(session.named_groups))
        self.assertEqual(history + 1, len(session._undo))
        self.assertEqual(original, [d.bytes() for d in session.workspace.documents])
        self.assertEqual("手动备注", session.annotations[annotation_key]["note"])
        session.undo()
        self.assertFalse(session.named_groups)
        session.redo()
        self.assertEqual(6, len(session.named_groups))
        self.assertEqual("换过名字", session.annotations[annotation_key]["name"])

    def test_existing_groups_scope_collision_and_repeat_keep_manual_work(self):
        session = EditorSession(workspace_fixture())
        d = session.workspace.documents[0]
        keys = {(d.name, i) for i in range(120)}
        session.edit_named_group("create", "手工鞋子", {(d.name, 0), (d.name, 1)})
        name = session.plan_material_groups(keys)["groups"][0]["name"]
        other = (session.workspace.documents[1].name, 0)
        session.edit_named_group("create", name, [other])
        plan = session.apply_material_groups(keys)
        self.assertEqual(2, plan["skipped"])
        self.assertEqual({(d.name, 0), (d.name, 1)}, session.named_groups["手工鞋子"])
        self.assertEqual({other}, session.named_groups[name])
        self.assertNotEqual(name, plan["groups"][0]["name"])
        self.assertEqual(118, sum(len(g["members"]) for g in plan["groups"]))
        before = session._collections()
        history = len(session._undo)
        repeated = session.apply_material_groups(keys)
        self.assertFalse(repeated["groups"])
        self.assertEqual(before, session.named_groups)
        self.assertEqual(history, len(session._undo))
        with self.assertRaises(Mod3Error):
            session.apply_material_groups(keys | {(d.name, 999)})
        self.assertEqual(before, session.named_groups)

    def test_explicit_regroup_moves_only_scope_removes_empty_and_is_idempotent(self):
        session = EditorSession(workspace_fixture())
        d = session.workspace.documents[0]
        a, b, outside = (d.name, 0), (d.name, 1), (d.name, 2)
        session.edit_named_group("create", "范围内旧组", [a])
        session.edit_named_group("create", "跨范围组", [b, outside])
        before = session._collections()
        plan = session.apply_material_groups({a, b}, include_grouped=True)
        self.assertEqual(2, plan["moved"])
        self.assertNotIn("范围内旧组", session.named_groups)
        self.assertEqual({outside}, session.named_groups["跨范围组"])
        after = session._collections()
        session.undo()
        self.assertEqual(before, session.named_groups)
        session.redo()
        self.assertEqual(after, session.named_groups)
        history = len(session._undo)
        session.apply_material_groups({a, b}, include_grouped=True)
        self.assertEqual(after, session.named_groups)
        self.assertEqual(history, len(session._undo))

    def test_material_names_are_sanitized_and_never_collide(self):
        session = EditorSession(workspace_fixture())
        for d in session.workspace.documents:
            d.material_names = ["相同\n" + "x" * 200, "相同\n" + "x" * 200]
        plan = session.apply_material_groups({(d.name, m.index) for d, m in session.workspace.all_meshes()})
        self.assertEqual(6, len({g["name"] for g in plan["groups"]}))
        self.assertTrue(all(len(g["name"]) <= 80 and "\n" not in g["name"] for g in plan["groups"]))

    def test_groups_persist_through_zip_and_project_and_allow_batch_layer_assignment(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("nativePC/pl/wst/body.mod3", material_fixture())
            session = EditorSession(ModWorkspace.open(source))
            d = session.workspace.documents[0]
            before = d.bytes()
            session.apply_material_groups({(d.name, m.index) for m in d.meshes})
            output = session.export(Path(temp) / "grouped.zip")
            reopened = EditorSession(ModWorkspace.open(output))
            self.assertEqual(session.named_groups, reopened.named_groups)
            self.assertEqual(before, reopened.workspace.documents[0].bytes())
            first = next(iter(reopened.named_groups.values()))
            reopened.apply_group(first, 61)
            self.assertTrue(all(reopened.document(n).meshes[i].visible_condition == 61 for n, i in first))
            project = Path(temp) / "project.json"
            reopened.save(project)
            again, _ = EditorSession.open(project)
            self.assertEqual(reopened.named_groups, again.named_groups)
            self.assertEqual(reopened.workspace.documents[0].bytes(), again.workspace.documents[0].bytes())

    @unittest.skipUnless((ROOT / "Kuroki-EX2.zip").exists(), "EX2 sample not present")
    def test_actual_split_children_form_one_material_group_and_remap_when_undoing_split(self):
        session = EditorSession(ModWorkspace.open(ROOT / "Kuroki-EX2.zip"))
        d = next(d for d in session.workspace.documents if d.equipment_part == "wst")
        count = session.split_mesh(d.name, 1)
        self.assertEqual(31, count)
        children = {(d.name, i) for i in range(1, count + 1)}
        before = d.bytes()
        plan = session.apply_material_groups(children)
        self.assertEqual(1, len(plan["groups"]))
        self.assertEqual(children, session.named_groups[plan["groups"][0]["name"]])
        self.assertEqual(before, d.bytes())
        session.undo()
        self.assertFalse(session.named_groups)
        self.assertEqual(32, len(d.meshes))
        session.undo()
        self.assertEqual(2, len(d.meshes))
        session.redo()
        session.redo()
        self.assertEqual(children, next(iter(session.named_groups.values())))
