import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from damage_model import DamagePreview, export_plan, permanently_hidden, units
from mod3 import ModWorkspace, Mod3Error
from renderer import SoftwareRenderer
from session import EditorSession
from test_core import minimal_mod3
from test_damage_model import create_source


class PermanentVisibilityTests(unittest.TestCase):
    def test_assignment_restore_and_atomic_history_preserve_geometry_and_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            s.edit_named_group('create', '鞋子', keys[:2])
            s.edit_annotations(keys[:2], name='鞋', note='保留备注')
            before = [d.bytes() for d in s.workspace.documents]
            self.assertEqual(2, s.set_always_hidden(keys[:2]))
            self.assertTrue(s.project_metadata_dirty)
            self.assertTrue(s.damage_model['enabled'])
            self.assertEqual(set(keys[:2]), permanently_hidden(s))
            self.assertEqual(before, [d.bytes() for d in s.workspace.documents])
            self.assertEqual(0, s.set_always_hidden(keys[:2]))
            s.undo()
            self.assertFalse(s.damage_model['enabled'])
            self.assertFalse(permanently_hidden(s))
            s.redo()
            self.assertEqual(2, s.apply_group(keys[:2], 60))  # Same group also restores visibility.
            self.assertFalse(permanently_hidden(s))
            self.assertEqual('鞋', s.annotations[keys[0]]['name'])
            s.undo()
            self.assertEqual(set(keys[:2]), permanently_hidden(s))
            s.redo()
            s.set_always_hidden(keys[:2])
            self.assertEqual(2, s.apply_group(keys[:2], 62))
            s.undo()
            self.assertEqual(60, s.document(keys[0][0]).meshes[0].visible_condition)
            self.assertEqual(set(keys[:2]), permanently_hidden(s))
            self.assertEqual(set(keys[:2]), s.named_groups['鞋子'])

    def test_preview_ignores_health_toggle_and_isolation_is_temporary(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            key = keys[-1]  # Group zero can also be permanently hidden.
            s.set_always_hidden([key])
            renderer = SoftwareRenderer()
            renderer.permanent_hidden_keys = permanently_hidden(s)
            for enabled in (False, True):
                for hp in (0, 20, 80, 100):
                    renderer.health_enabled, renderer.health = enabled, hp
                    visible = {(d.name, m.index) for _, d, m in renderer.visible_entries(s.workspace)}
                    self.assertNotIn(key, visible)
            renderer.isolate_selected = True
            renderer.selected_keys = {key}
            renderer.health_hidden_keys = {key}
            visible = {(d.name, m.index) for _, d, m in renderer.visible_entries(s.workspace)}
            self.assertEqual({key}, visible)
            renderer.isolate_selected = False
            self.assertNotIn(key, {(d.name, m.index) for _, d, m in renderer.visible_entries(s.workspace)})
            self.assertIn(key, permanently_hidden(s))

    def test_random_budget_excludes_hidden_units_and_preserves_partial_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            s.edit_named_group('create', '配对鞋', keys[:2])
            s.set_always_hidden([keys[0], keys[2]])
            records = units(s)
            preview = DamagePreview(4)
            self.assertEqual({keys[0], keys[2]}, preview.update(records, 100))
            self.assertEqual(2 + 9, len(preview.update(records, 50)))  # 18 eligible units / 2.
            self.assertNotIn(keys[-1], preview.update(records, 0))
            s.set_damage_mode('random')
            manifest = next(iter(export_plan(s).export_files.values())).decode('utf-8-sig')
            self.assertIn('Version=2', manifest)
            self.assertEqual(2, manifest.count('ForceHidden=1'))
            self.assertEqual(set(range(18)), {int(line[5:]) for line in manifest.splitlines()
                                           if line.startswith('Unit=') and line != 'Unit=-1'})

    def test_zip_project_and_folder_roundtrip_keep_flag_and_only_patch_group_bytes(self):
        for folder in (False, True):
            with self.subTest(folder=folder), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                s, keys = create_source(root, folder=folder)
                s.set_always_hidden([keys[-1]])
                manual = {d.name: d.bytes() for d in s.workspace.documents}
                for mode in ('manual', 'random', 'manual'):
                    s.set_damage_mode(mode)
                    plan = export_plan(s)
                    for doc in plan.documents:
                        base = s.document(doc.name)
                        patched = bytearray(doc.bytes())
                        for mesh in base.meshes:
                            off = mesh.visible_condition_offset
                            patched[off:off + 2] = base.bytes()[off:off + 2]
                        self.assertEqual(manual[doc.name], patched)
                    target = root / f'{mode}.zip'
                    if target.exists():
                        target = root / 'manual-return.zip'
                    s.export(target)
                    restored = EditorSession(ModWorkspace.open(target))
                    self.assertFalse(restored.workspace.errors)
                    self.assertEqual(s.annotations, restored.annotations)
                    self.assertEqual(manual, {d.name: d.bytes() for d in restored.workspace.documents})
                    if folder:
                        text = next(target.rglob('MHWLayerStudio-*.ini')).read_text(encoding='utf-8-sig')
                    else:
                        with zipfile.ZipFile(target) as archive:
                            cfg = next(n for n in archive.namelist() if '/plugins/MHWLayerStudio-' in n)
                            text = archive.read(cfg).decode('utf-8-sig')
                    self.assertIn('ForceHidden=1', text)
                    self.assertIn(f'Mode={mode}', text)
                project = root / 'continue.json'
                s.save(project)
                restored, _ = EditorSession.open(project)
                self.assertEqual(s.annotations, restored.annotations)
                if folder:
                    s.export(s.workspace.source, overwrite=True,
                             expected_state=s.overwrite_state(s.workspace.source))
                    self.assertIn(keys[-1], permanently_hidden(s))
                    self.assertFalse(s.project_metadata_dirty)

    def test_unmapped_npc_groups_stay_unchanged_in_manual_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            s, keys = create_source(Path(temp))
            s.apply_group(keys[:2], 2)
            s.set_always_hidden([keys[0]])
            plan = export_plan(s)
            self.assertEqual(2, plan.documents[1].meshes[0].visible_condition)
            text = next(iter(plan.export_files.values())).decode('utf-8-sig')
            self.assertIn('ForceHidden=1', text)
            self.assertEqual(set(range(18)), {int(line[5:]) for line in text.splitlines()
                                           if line.startswith('Unit=') and line != 'Unit=-1'})
            s.set_damage_mode('random')
            with self.assertRaisesRegex(ValueError, 'Group 2'):
                export_plan(s)

    def test_single_mod3_rejected_before_edit_and_invalid_flags_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'single.mod3'
            source.write_bytes(minimal_mod3())
            s = EditorSession(ModWorkspace.open(source))
            before = source.read_bytes()
            with self.assertRaisesRegex(Mod3Error, '完整'):
                s.set_always_hidden([(s.workspace.documents[0].name, 0)])
            self.assertFalse(s.annotations)
            self.assertFalse(s.damage_model['enabled'])
            self.assertEqual(before, source.read_bytes())
            with self.assertRaises(Mod3Error):
                s.edit_annotations([(s.workspace.documents[0].name, 0)], visibility='invisible')


if __name__ == '__main__':
    unittest.main()
