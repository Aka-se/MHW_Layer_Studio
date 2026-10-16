"""Read-only inventory of MHW Box and custom model mod libraries."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET
import zipfile

from game_resources import read_settings, update_settings


ARCHIVES = {".zip": "ZIP", ".rar": "RAR", ".7z": "7Z"}
SKIP = {".git", ".codex", ".agents", ".codex-remote-attachments", ".mhw-layer-studio", ".mhw-layer-studio-backups", "__pycache__"}


@dataclass(frozen=True)
class LibraryMod:
    name: str
    path: Path
    kind: str
    model_count: int | None
    box_state: str = ""
    relative: str = ""


def normalize_root(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("模组库路径不存在或不是文件夹")
    # Choosing the Box installation itself is also useful.
    box = root / "Mods_582010"
    return box if box.is_dir() else root


def saved_library_root():
    value = read_settings().get("mod_library_directory")
    return value if isinstance(value, str) and len(value) < 4096 else ""


def save_library_root(directory):
    root = normalize_root(directory)
    update_settings(mod_library_directory=str(root))
    return root


def discover_library_root(game_directory=""):
    saved = saved_library_root()
    if saved:
        return saved  # Keep unavailable drives visible so they can be changed.
    candidates = []
    cwd = Path.cwd()
    if cwd.name.lower() in ("mods_582010", "mhw_box"):
        candidates.append(cwd)
    if game_directory:
        candidates.append(Path(game_directory).parent / "MHW_box" / "Mods_582010")
    anchors = {cwd.anchor, Path(__file__).resolve().anchor}
    if game_directory:
        anchors.add(Path(game_directory).anchor)
    for anchor in sorted(anchors):
        if anchor:
            candidates.append(Path(anchor) / "SteamLibrary/steamapps/common/MHW_box/Mods_582010")
    return next((str(normalize_root(p)) for p in candidates if p.is_dir()), "")


def _linked(path):
    try:
        st = path.lstat()
        return path.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return True


def _metadata(root):
    name, state = root.name, ""
    info = root / "info.xml"
    try:
        if not info.is_file() or info.stat().st_size > 256 * 1024 or _linked(info):
            return name, state
        data = info.read_bytes()
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            return name, state
        values = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in ET.fromstring(data)}
        name = values.get("moduleName") or values.get("name") or name
        state = {"true": "盒子已启用", "false": "盒子未启用"}.get(values.get("enable", "").lower(), "")
    except (OSError, ET.ParseError, ValueError):
        pass
    return "".join(c if ord(c) >= 32 else " " for c in name)[:512], state


def scan_library(directory, cancelled=lambda: False, progress=None):
    """Read names/ZIP tables only. Never parse model payloads or install mods."""
    root = normalize_root(directory)
    from mod_library_index import LibraryIndex, stamp
    index = LibraryIndex(root)
    records, errors = [], []
    watches = {}
    def watch(path):
        relative = index.relative(path)
        if relative not in watches:
            watches[relative] = stamp(path)
    def children(path):
        try:
            return sorted(path.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            errors.append(f"{path.name}：{exc}")
            return []
    def walk_files(path):
        if _linked(path):
            return
        pending = [path]
        while pending:
            if cancelled():
                return
            current = pending.pop()
            try:
                watch(current)
                # DirEntry reuses Windows directory metadata. Path.lstat on every
                # texture/auxiliary file used to dominate library startup time.
                with os.scandir(current) as scan:
                    entries = sorted(scan, key=lambda e: e.name.casefold())
            except OSError as exc:
                errors.append(str(exc))
                continue
            directories = []
            for entry in entries:
                if cancelled():
                    return
                if entry.name.casefold() in SKIP:
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if not is_dir and Path(entry.name).suffix.lower() not in {".mod3", *ARCHIVES}:
                        continue
                    if entry.is_symlink() or (getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                        continue
                    if is_dir:
                        directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                except OSError as exc:
                    errors.append(str(exc))
            pending.extend(reversed(directories))
    def archive(path):
        watch(path)
        count = None
        if path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as z:
                    count = sum(not i.is_dir() and i.filename.lower().endswith(".mod3") for i in z.infolist())
                if not count:
                    return
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append(f"{path.name}：{exc}")
                return
        records.append(LibraryMod(path.stem, path, ARCHIVES[path.suffix.lower()], count, relative=path.relative_to(root).as_posix()))
    def folder(path, label_root=None):
        label_root = label_root or path
        watch(path)
        watch(label_root / "info.xml")
        name, state = _metadata(label_root)
        count = 0
        for p in walk_files(path):
            if cancelled():
                return
            if p.suffix.lower() == ".mod3":
                count += 1
            elif p.suffix.lower() in ARCHIVES:
                archive(p)
        if count:
            relative = path.relative_to(root).as_posix()
            records.append(LibraryMod(name, path, "文件夹", count, state, relative))
    tasks = []
    if root.name.lower() == "nativepc" or (root / "nativePC").is_dir() or ((root / "info.xml").is_file() and (root / "files").is_dir()):
        payload = root / "files" if (root / "info.xml").is_file() and (root / "files").is_dir() else root
        tasks.append(("folder", payload, root))
    else:
        direct_models = []
        for path in children(root):
            if cancelled():
                break
            if path.name.casefold() in SKIP or _linked(path):
                continue
            if path.is_dir():
                payload = path / "files" if (path / "info.xml").is_file() and (path / "files").is_dir() else path
                tasks.append(("folder", payload, path))
            elif path.suffix.lower() in ARCHIVES:
                tasks.append(("archive", path, None))
            elif path.suffix.lower() == ".mod3":
                direct_models.append(path)
        for path in direct_models:
            records.append(LibraryMod(path.stem, path, "MOD3", 1, relative=path.name))
    reused = scanned = 0
    for position, (kind, path, label) in enumerate(tasks):
        if cancelled():
            break
        if progress:
            progress({"done": position, "total": len(tasks), "reused": reused, "scanned": scanned,
                      "name": (label or path).name})
        key = kind + ":" + index.relative(path)
        cached = index.get(key)
        if cached is not None:
            cached_records, cached_errors = cached
            records.extend(cached_records)
            errors.extend(cached_errors)
            reused += 1
            continue
        watches = {}
        first_record, first_error = len(records), len(errors)
        if kind == "folder":
            folder(path, label)
        else:
            archive(path)
        if cancelled():
            break
        index.remember(key, records[first_record:], errors[first_error:], watches)
        scanned += 1
    if not cancelled():
        index.save()
        if progress:
            progress({"done": len(tasks), "total": len(tasks), "reused": reused,
                      "scanned": scanned, "name": "", "complete": True})
    return root, sorted(records, key=lambda r: (r.name.casefold(), str(r.path).casefold())), errors


def filter_mods(records, query):
    words = query.casefold().split()
    return [r for r in records if all(w in (r.name + " " + r.relative).casefold() for w in words)]


def library_snapshot(directory):
    """Show the last index immediately; background scanning checks freshness."""
    from mod_library_index import LibraryIndex
    try:
        root = normalize_root(directory)
        index = LibraryIndex(root)
        records = []
        for key in index.entries:
            cached = index.get(key, check=False)
            if cached is not None:
                records.extend(cached[0])
        return sorted(records, key=lambda r: (r.name.casefold(), str(r.path).casefold()))
    except (OSError, ValueError):
        return []
