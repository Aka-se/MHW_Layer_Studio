import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from damage_model import DamagePreview, units, step_for_health, export_plan
from mod3 import ModWorkspace
from session import EditorSession
from test_core import minimal_mod3


def create_source(root, folder=False):
    source = root / ('source' if folder else 'source.zip')
    files = {f'nativePC/pl/f_equip/pl001_0000/wst/part{i:02d}.mod3': minimal_mod3() for i in range(21)}
    files['nativePC/keep.tex'] = b'unrelated texture'
    if folder:
        for name, data in files.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    else:
        with zipfile.ZipFile(source, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
    s = EditorSession(ModWorkspace.open(source))
    keys = [(d.name, 0) for d in s.workspace.documents]
    s.apply_group(keys[:20], 60)
    return s, keys


class DamageTests(unittest.TestCase):
    def test_budget_healing_and_redamage_keep_existing_hidden_units(self):
        records = [{'pool': 'one', 'keys': ((str(i), 0),), 'protected': False} for i in range(20)]
        records += [{'pool': 'one', 'keys': (('base', 0),), 'protected': True}]
        preview = DamagePreview(7)
        a = preview.update(records, 80)
        self.assertEqual(4, len(a))
        self.assertEqual(a, preview.update(records, 79))
        records += [{'pool': 'two', 'keys': (('second outfit', 0),), 'protected': False}]
        self.assertNotIn(('second outfit', 0), preview.update(records, 5))
        self.assertIn(('second outfit', 0), preview.update(records, 0))

    def test_five_percent_boundary_and_protected_units(self):
        records = [{'pool': 'one', 'keys': ((str(i), 0),), 'protected': False} for i in range(20)]
        records += [{'pool': 'one', 'keys': (('base', 0),), 'protected': True}]
        preview = DamagePreview(8)
        self.assertEqual(set(), preview.update(records, 95.01))
        a = preview.update(records, 80)
        self.assertEqual(4, len(a))
        self.assertEqual(a, preview.update(records, 79.99))
        b = preview.update(records, 90)
        self.assertEqual(2, len(b)); self.assertLessEqual(b, a)
        c = preview.update(records, 80)
        self.assertEqual(4, len(c)); self.assertLessEqual(b, c)
        self.assertNotIn(('base', 0), preview.update(records, 0))
        self.assertEqual(20, len(preview.update(records, 0)))
        self.assertEqual(set(), preview.update(records, 100))
        outcomes = {frozenset(preview.update(records, 80))}
        for _ in range(8):
            preview.update(records, 100)
            outcomes.add(frozenset(preview.update(records, 80)))
        self.assertGreater(len(outcomes), 1)
        self.assertEqual(0, step_for_health(float('nan')))
        self.assertEqual(20, step_for_health(0))

    def test_group_is_atomic_protection_and_roles_do_not_edit_manual_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            s.edit_named_group('create', '鞋子与LOD', keys[:2])
            self.assertEqual(20, len(units(s)))
            s.edit_annotations([keys[0]], damage_role='base')
            record = next(r for r in units(s) if r['name'] == '鞋子与LOD')
            self.assertTrue(record['protected'])
            self.assertEqual(60, s.document(keys[0][0]).meshes[0].visible_condition)
            s.undo()
            self.assertFalse(next(r for r in units(s) if r['name'] == '鞋子与LOD')['protected'])
            s.set_damage_mode('random'); s.undo()
            self.assertFalse(s.damage_model['enabled'])
            s.redo(); self.assertEqual('random', s.damage_model['mode'])

    def test_dual_export_roundtrip_modes_and_geometry_only_condition_bytes_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            s, keys = create_source(root)
            s.set_damage_mode('random')
            s.edit_named_group('create', '左右袖含LOD', keys[:2])
            s.apply_group([keys[1]], 61)  # One random unit, two preserved manual thresholds.
            s.edit_annotations([keys[2]], damage_role='base')
            before = {d.name: d.bytes() for d in s.workspace.documents}
            source_sha = hashlib.sha256(s.workspace.source.read_bytes()).hexdigest()
            output = s.export(root / 'random.zip')
            self.assertEqual(before, {d.name: d.bytes() for d in s.workspace.documents})
            self.assertEqual(source_sha, hashlib.sha256(s.workspace.source.read_bytes()).hexdigest())
            with zipfile.ZipFile(output) as z:
                cfg = next(n for n in z.namelist() if '/plugins/MHWLayerStudio-' in n)
                self.assertIn('Mode=random', z.read(cfg).decode('utf-8-sig'))
                self.assertEqual(b'unrelated texture', z.read('nativePC/keep.tex'))
                for d in s.workspace.documents:
                    raw = z.read(d.name)
                    offset = d.meshes[0].visible_condition_offset
                    self.assertEqual(before[d.name][:offset], raw[:offset])
                    self.assertEqual(before[d.name][offset+2:], raw[offset+2:])
            r = EditorSession(ModWorkspace.open(output))
            self.assertFalse(r.workspace.errors)
            self.assertEqual(s.damage_model, r.damage_model)
            self.assertEqual(s.named_groups, r.named_groups)
            self.assertEqual(s.annotations, r.annotations)
            self.assertEqual(before, {d.name: d.bytes() for d in r.workspace.documents})
            r.set_damage_mode('manual')
            out2 = r.export(root / 'manual.zip')
            with zipfile.ZipFile(output) as a, zipfile.ZipFile(out2) as b:
                for d in s.workspace.documents: self.assertEqual(a.read(d.name), b.read(d.name))
                self.assertIn('Mode=manual', b.read(cfg).decode('utf-8-sig'))
                self.assertEqual(1, b.namelist().count(cfg))
            project = root / 'continue.json'
            r.save(project)
            q, _ = EditorSession.open(project)
            self.assertEqual(r.damage_model, q.damage_model)
            self.assertEqual(r.annotations, q.annotations)

    def test_folder_overwrite_backs_up_manifest_and_preserves_manual_values(self):
        with tempfile.TemporaryDirectory() as temp:
            s, _ = create_source(Path(temp), folder=True)
            s.set_damage_mode('random')
            source = s.workspace.source
            s.export(source, overwrite=True, expected_state=s.overwrite_state(source))
            self.assertEqual('random', s.damage_model['mode'])
            self.assertEqual(60, s.workspace.documents[0].meshes[0].visible_condition)
            s.set_damage_mode('manual')
            expected = s.overwrite_state(source)
            manifest = next(n for n in expected if '/plugins/' in n)
            previous = (source / manifest).read_bytes()
            old_workspace = s.workspace
            s.export(source, overwrite=True, expected_state=expected)
            self.assertEqual(previous, (old_workspace.last_backup / 'files' / manifest).read_bytes())
            self.assertIn(b'Mode=manual', (source / manifest).read_bytes())
            self.assertEqual(b'unrelated texture', (source / 'nativePC/keep.tex').read_bytes())

    def test_unsupported_custom_manual_group_is_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            s.apply_group([keys[0]], 500)
            s.set_damage_mode('random')
            with self.assertRaisesRegex(ValueError, '500'): export_plan(s)


if __name__ == '__main__': unittest.main()
