"""Remember successful file exports without changing save/backup policy."""
import os
import re
from pathlib import Path

from game_resources import read_settings, update_settings


def _source_key(source):
    return os.path.normcase(str(Path(source).resolve()))


def export_suggestion(source, extension):
    source = Path(source)
    settings = read_settings()
    history = settings.get("file_exports", {})
    previous = history.get(_source_key(source)) if isinstance(history, dict) else None
    # Only reuse a file name for this source, never another mod's name.
    if isinstance(previous, str) and previous and len(previous) < 4096:
        path = Path(previous)
        if path.suffix.lower() == extension and path.parent.is_dir():
            return path
    directory = settings.get("export_directory")
    parent = Path(directory) if isinstance(directory, str) and directory and len(directory) < 4096 else source.parent
    if not parent.is_dir():
        parent = source.parent
    stem = re.sub(r"(?:_layered)+$", "", source.stem, flags=re.IGNORECASE)
    return parent / (stem + "_layered" + extension)


def remember_export(source, destination):
    destination = Path(destination).resolve()
    settings = read_settings()
    stored = settings.get("file_exports", {})
    history = dict(stored) if isinstance(stored, dict) else {}
    key = _source_key(source)
    history.pop(key, None)
    history[key] = str(destination)
    # Bound local preferences even when processing many unrelated mods.
    history = dict(list(history.items())[-40:])
    update_settings(file_exports=history, export_directory=str(destination.parent))
