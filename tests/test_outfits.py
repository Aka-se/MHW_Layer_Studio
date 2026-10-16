import tempfile
import unittest
from pathlib import Path

from mod3 import Mod3Document, ModWorkspace
from renderer import SoftwareRenderer
from session import EditorSession
from test_core import minimal_mod3

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "Skimpy Master Garon Armor-2039-1-1-1579667215.zip"


@unittest.skipUnless(SAMPLE.exists(), "optional local integration sample not available")
class OutfitTests(unittest.TestCase):
    def test_sample_outfits_and_preview_filter_are_independent_of_edits(self):
        workspace = ModWorkspace.open(SAMPLE)
        self.assertEqual(20, len(workspace.documents))
        self.assertEqual(143, workspace.mesh_count)
        outfits = workspace.outfits
        self.assertEqual([40, 31, 40, 32], [sum(len(d.meshes) for d in docs) for docs in outfits.values()])
        self.assertTrue(all({d.equipment_part for d in docs} == {"helm", "body", "arm", "wst", "leg"}
                            for docs in outfits.values()))
        before = [d.bytes() for d in workspace.documents]
        renderer = SoftwareRenderer()
        renderer.health_enabled = False
        ids = list(outfits)
        for identity in ids:
            renderer.active_outfit = identity
            entries = renderer.visible_entries(workspace)
            self.assertTrue(all(d.outfit_id == identity for _, d, _ in entries))
            self.assertTrue(renderer.fit(workspace))
        self.assertEqual(before, [d.bytes() for d in workspace.documents])
        renderer.active_outfit = ids[1]
        renderer.enabled_parts = {"wst"}
        self.assertEqual(1, len(renderer.visible_entries(workspace)))
        renderer.lod_bit = None
        self.assertEqual(6, len(renderer.visible_entries(workspace)))
        other_key = (outfits[ids[0]][0].name, 0)
        self.assertFalse(renderer.fit(workspace, {other_key}))
        renderer.active_outfit = None
        renderer.enabled_parts = {"helm", "body", "arm", "wst", "leg"}
        self.assertEqual(143, len(renderer.visible_entries(workspace)))
        renderer.lod_bit = 1
        self.assertEqual(63, len(renderer.visible_entries(workspace)))

    def test_outfit_paths_preserve_gender_and_optional_variants(self):
        paths = ["A/nativePC/pl/f_equip/pl029_0001/wst/mod/f_wst029_0001.mod3",
                 "B/nativePC/pl/f_equip/pl029_0001/wst/mod/f_wst029_0001.mod3",
                 "A/nativePC/pl/m_equip/pl029_0001/wst/mod/m_wst029_0001.mod3"]
        documents = [Mod3Document(path, minimal_mod3()) for path in paths]
        self.assertEqual(3, len({d.outfit_id for d in documents}))
        self.assertEqual(3, len({d.outfit_label for d in documents}))
        standalone = Mod3Document("f_wst029_0001.mod3", minimal_mod3())
        self.assertEqual("f_equip/pl029_0001", standalone.outfit_id)

    def test_export_keeps_all_outfits_with_only_target_group_changed(self):
        workspace = ModWorkspace.open(SAMPLE)
        originals = {d.name: d.bytes() for d in workspace.documents}
        first, second = list(workspace.outfits.values())[:2]
        target = next(d for d in first if d.equipment_part == "wst")
        session = EditorSession(workspace)
        session.apply_group({(target.name, 0)}, 60)
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "outfit.json"
            session.save(project, {"outfit": second[0].outfit_id})
            restored, view = EditorSession.open(project)
            self.assertEqual(second[0].outfit_id, view["outfit"])
            self.assertEqual(4, len(restored.workspace.outfits))
            archive = workspace.export(Path(folder) / "outfits.zip")
            output = ModWorkspace.open(archive)
            self.assertEqual(20, len(output.documents))
            for document in output.documents:
                if document.name == target.name:
                    expected = bytearray(originals[document.name])
                    expected[document.meshes[0].visible_condition_offset:document.meshes[0].visible_condition_offset + 2] = b"\x3c\x00"
                    self.assertEqual(bytes(expected), document.bytes())
                else:
                    self.assertEqual(originals[document.name], document.bytes())
