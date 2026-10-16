import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from export_preferences import export_suggestion, remember_export
from game_resources import read_settings, update_settings


class ExportPreferencesTests(unittest.TestCase):
    def test_per_mod_names_persist_and_other_settings_survive(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": temp}):
            root = Path(temp)
            source = root / "A.rar"
            target = root / "外套分层.zip"
            update_settings(game_directory="game", mod_library_directory="mods")
            remember_export(source, target)
            self.assertEqual(target.resolve(), export_suggestion(source, ".zip"))
            self.assertEqual(root.resolve() / "B_layered.zip", export_suggestion(root / "B.7z", ".zip"))
            self.assertEqual("game", read_settings()["game_directory"])
            self.assertEqual("mods", read_settings()["mod_library_directory"])

    def test_existing_suffix_and_missing_destination_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": temp}):
            root = Path(temp)
            source = root / "A_layered_layered.ZIP"
            remember_export(source, root / "removed" / "custom.zip")
            self.assertEqual(root / "A_layered.zip", export_suggestion(source, ".zip"))
            self.assertEqual(root / "B_layered.mod3", export_suggestion(root / "B_layered.MOD3", ".mod3"))

    def test_new_mod_uses_last_directory_but_its_own_name(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MHW_LAYER_STUDIO_DATA_DIR": temp}):
            root = Path(temp)
            output = root / "exports"
            output.mkdir()
            remember_export(root / "A.zip", output / "custom.zip")
            self.assertEqual(output.resolve() / "B_layered.zip", export_suggestion(root / "B.zip", ".zip"))
            update_settings(file_exports=[], export_directory=123)
            self.assertEqual(root / "B_layered.zip", export_suggestion(root / "B.zip", ".zip"))
