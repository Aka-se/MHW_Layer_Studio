"""Verified replacements with persistent backups and rollback on write failure."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path


def check_path(path, root):
    path, root = Path(path), Path(root)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("目标路径超出所选目录")
    current = path
    while current != root.parent:
        if current.exists() and (current.is_symlink() or getattr(current.lstat(), "st_file_attributes", 0)
                                 & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("覆盖目标含链接或重定向，请选择普通文件或目录")
        if current == root:
            break
        current = current.parent


def fingerprint(path):
    path = Path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"目标不是普通文件：{path}")
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for block in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def target_state(root, names):
    root = Path(root)
    result = {}
    for name in names:
        path = root / name
        check_path(path, root)
        result[name] = fingerprint(path)
    return result


def replace_with_backup(staged_root, root, names, expected=None, label="mod", backup_parent=None):
    staged_root, root = Path(staged_root), Path(root)
    names = list(names)
    state = target_state(root, names)
    if expected is not None and state != expected:
        raise ValueError("目标文件在确认后发生变化，未覆盖；请重新核对后保存")
    # Backups are siblings of the output, never scanned as meshes within it.
    backup_parent = Path(backup_parent) if backup_parent else root.parent / ".mhw-layer-studio-backups"
    check_path(backup_parent, backup_parent.parent)
    backup_parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    backup = Path(tempfile.mkdtemp(prefix=f"{label}-{datetime.now():%Y%m%d-%H%M%S}-", dir=backup_parent))
    manifest = {"format": "mhw-layer-backup", "root": str(root.resolve()), "before": state}
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, digest in state.items():
        if digest is not None:
            destination = backup / "files" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, destination)
            if fingerprint(destination) != digest:
                raise ValueError("备份校验失败，未覆盖目标")
    if target_state(root, names) != state:
        raise ValueError("目标文件在备份期间发生变化，未覆盖")
    written = []
    created_directories = []
    try:
        for name in names:
            path = root / name
            check_path(path, root)
            absent = []
            parent = path.parent
            while not parent.exists():
                absent.append(parent)
                parent = parent.parent
            path.parent.mkdir(parents=True, exist_ok=True)
            created_directories.extend(reversed(absent))
            os.replace(staged_root / name, path)
            written.append(name)
    except Exception as exc:
        failures = []
        for name in reversed(written):
            try:
                if state[name] is None:
                    (root / name).unlink()
                else:
                    shutil.copy2(backup / "files" / name, root / name)
            except OSError:
                failures.append(name)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()  # Empty directories created by this operation only.
            except OSError:
                pass
        if failures:
            raise ValueError(f"覆盖失败，部分文件无法自动恢复；完整备份位于：{backup}") from exc
        raise ValueError(f"覆盖失败，已恢复原文件；备份位于：{backup}；原因：{exc}") from exc
    return backup
