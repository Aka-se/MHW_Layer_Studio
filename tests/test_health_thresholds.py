import json
import tempfile
import unittest
from pathlib import Path

from health_rules import DEFAULT_THRESHOLDS, validate_thresholds
from mod3 import ModWorkspace, generate_health_rules
from session import EditorSession
from renderer import SoftwareRenderer
from test_core import minimal_mod3
from test_editor import workspace_fixture


class ThresholdTests(unittest.TestCase):
    def test_four_stages_export_recovery_and_project_preserve_model_bytes(self):
        self.assertEqual({60: 80, 61: 60, 62: 40, 63: 20}, DEFAULT_THRESHOLDS)
        session = EditorSession(workspace_fixture())
        custom = {60: 85, 61: 70, 62: 55}
        session.set_health_thresholds(custom)
        before = [d.bytes() for d in session.workspace.documents]
        values = {60: 80, 61: 60, 62: 40, 63: 20}
        session.set_health_thresholds(values)
        self.assertEqual(before, [d.bytes() for d in session.workspace.documents])
        rules = generate_health_rules(thresholds=values)
        self.assertEqual(8, rules.count("[Rule"))
        self.assertIn("Id=63\nHealthPercentAbove=20\nReturn=1", rules)
        self.assertIn("Id=63\nReturn=0", rules)
        renderer = SoftwareRenderer()
        renderer.health_thresholds = values
        mesh = session.workspace.documents[0].meshes[0]
        mesh.visible_condition = 63
        for hp, visible in ((20, False), (21, True), (0, False), (100, True)):
            renderer.health = hp
            self.assertEqual(visible, renderer.visible_for_health(mesh))
        session.undo()
        self.assertEqual(custom, session.health_thresholds)
        session.redo()
        self.assertEqual(values, session.health_thresholds)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.mod3"
            source.write_bytes(minimal_mod3())
            session = EditorSession(ModWorkspace.open(source))
            session.set_health_thresholds(values)
            project = Path(temp) / "four.json"
            session.save(project)
            restored, _ = EditorSession.open(project)
            self.assertEqual(values, restored.health_thresholds)
            self.assertEqual(source.read_bytes(), restored.workspace.documents[0].bytes())
            archive = session.export(Path(temp) / "copy.mod3")
            reopened = EditorSession(ModWorkspace.open(archive))
            self.assertEqual(values, reopened.health_thresholds)
        with self.assertRaises(ValueError):
            generate_health_rules(decoration_id=63, thresholds=values)

    def test_custom_threshold_boundaries_recover_and_leave_unmapped_groups_alone(self):
        session = EditorSession(workspace_fixture())
        renderer = SoftwareRenderer()
        renderer.health_thresholds = {60: 85, 61: 70, 62: 55}
        mesh = session.workspace.documents[0].meshes[0]
        for group, threshold in renderer.health_thresholds.items():
            mesh.visible_condition = group
            renderer.health = threshold
            self.assertFalse(renderer.visible_for_health(mesh))
            renderer.health = threshold + .1
            self.assertTrue(renderer.visible_for_health(mesh))
            renderer.health = threshold - .1
            self.assertFalse(renderer.visible_for_health(mesh))
            renderer.health = 100
            self.assertTrue(renderer.visible_for_health(mesh))
        for group in (0, 500):
            mesh.visible_condition, renderer.health = group, 0
            self.assertTrue(renderer.visible_for_health(mesh))
        mesh.visible_condition, renderer.health_enabled = 60, False
        self.assertTrue(renderer.visible_for_health(mesh))

    def test_settings_validation_history_and_no_model_byte_changes(self):
        session = EditorSession(workspace_fixture())
        original = [d.bytes() for d in session.workspace.documents]
        for bad in ({60: 101, 61: 70, 62: 55}, {60: -1, 61: 70, 62: 55}, {60: True, 61: 70, 62: 55},
                    {60: "85", 61: 70, 62: 55}, {60: 85.5, 61: 70, 62: 55}, {}, {0: 85, 61: 70, 62: 55}, {60: 80, "60": 60}, {65536: 20}):
            with self.assertRaises(ValueError):
                session.set_health_thresholds(bad)
            self.assertEqual(DEFAULT_THRESHOLDS, session.health_thresholds)
            self.assertFalse(session.can_undo)
        session.set_health_thresholds({"60": 85, "61": 70, "62": 55})
        self.assertTrue(session.project_metadata_dirty)
        self.assertEqual(original, [d.bytes() for d in session.workspace.documents])
        session.undo()
        self.assertEqual(DEFAULT_THRESHOLDS, session.health_thresholds)
        self.assertFalse(session.project_dirty)
        session.redo()
        self.assertEqual({60: 85, 61: 70, 62: 55}, session.health_thresholds)
        count = len(session._undo)
        self.assertFalse(session.set_health_thresholds(session.health_thresholds))
        self.assertEqual(count, len(session._undo))

    def test_rule_export_uses_same_settings_with_reversible_fallbacks(self):
        rules = generate_health_rules(thresholds={60: 85, 61: 70, 62: 55})
        for group, threshold in ((60, 85), (61, 70), (62, 55)):
            self.assertIn(f"Id={group}\nHealthPercentAbove={threshold}\nReturn=1", rules)
            self.assertIn(f"Id={group}\nReturn=0", rules)
        self.assertNotIn("HealthPercentAbove=75", rules)
        self.assertNotIn("LatchUntilReset=1", rules)

    def test_project_sidecar_and_legacy_project_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            session = EditorSession(ModWorkspace.open(source))
            session.set_health_thresholds({60: 85, 61: 70, 62: 55})
            project = Path(temp) / "layers.json"
            session.save(project)
            restored, _ = EditorSession.open(project)
            self.assertEqual(session.health_thresholds, restored.health_thresholds)
            output = Path(temp) / "copy.mod3"
            session.export(output)
            self.assertEqual(source.read_bytes(), output.read_bytes())
            restored = EditorSession(ModWorkspace.open(output))
            self.assertEqual(session.health_thresholds, restored.health_thresholds)
            data = json.loads(project.read_text(encoding="utf-8"))
            data["version"] = 6
            del data["health_thresholds"]
            project.write_text(json.dumps(data), encoding="utf-8")
            restored, _ = EditorSession.open(project)
            self.assertEqual(DEFAULT_THRESHOLDS, restored.health_thresholds)

    def test_legacy_default_upgrade_and_intentionally_removed_fourth_stage(self):
        from health_rules import restore_thresholds
        old = {60: 80, 61: 60, 62: 40}
        self.assertEqual(DEFAULT_THRESHOLDS, restore_thresholds(old, legacy=True))
        self.assertEqual(old, restore_thresholds(old))
        custom = {60: 85, 61: 70, 62: 55}
        self.assertEqual(custom, restore_thresholds(custom, legacy=True))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "body.mod3"
            source.write_bytes(minimal_mod3())
            s = EditorSession(ModWorkspace.open(source))
            s.set_health_thresholds(old)  # Deliberately choose three stages now.
            project = Path(tmp) / "three.json"
            s.save(project)
            restored, _ = EditorSession.open(project)
            self.assertEqual(old, restored.health_thresholds)
            exported = s.export(Path(tmp) / "three.mod3")
            self.assertEqual(old, EditorSession(ModWorkspace.open(exported)).health_thresholds)
            data = json.loads(project.read_text(encoding="utf-8"))
            data["version"] = 10
            project.write_text(json.dumps(data), encoding="utf-8")
            upgraded, _ = EditorSession.open(project)
            self.assertEqual(DEFAULT_THRESHOLDS, upgraded.health_thresholds)
            # Read a legacy sidecar with the previous standard three stages.
            sidecar = exported.with_name(exported.name + ".mhwlayer.json")
            self.assertTrue(sidecar.exists())
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data["version"] = 2
            sidecar.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(DEFAULT_THRESHOLDS, EditorSession(ModWorkspace.open(exported)).health_thresholds)
