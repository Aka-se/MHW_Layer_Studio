"""RAR/7Z input is streamed into an owned ZIP cache, without extracting paths."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
import re
import zlib
from datetime import datetime
from pathlib import Path, PurePosixPath

import rarfile


class ArchiveError(ValueError):
    pass


def unrar_path():
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets/unrar/UnRAR.exe"
    if bundled.is_file():
        return bundled
    installed = shutil.which("unrar")
    if installed:
        return Path(installed)
    raise ArchiveError("缺少 RAR 读取组件，请使用完整安装包")


def safe_member_name(name):
    name = name.replace("\\", "/")
    parts = name.rstrip("/").split("/")
    if (not name or PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") or ":" in p for p in parts)
            or any(ord(c) < 32 or c in '<>|?*"' for c in name)):
        raise ArchiveError(f"压缩包内路径无效：{name!r}")
    devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(p.rstrip(" .").split(".")[0].upper() in devices for p in parts):
        raise ArchiveError(f"压缩包内路径使用 Windows 保留名称：{name!r}")
    return name


def sevenzip_path():
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets/7zip/7za.exe"
    if bundled.is_file():
        return bundled
    installed = shutil.which("7za") or shutil.which("7z")
    if installed:
        return Path(installed)
    raise ArchiveError("缺少 7Z 读取组件，请使用完整安装包")


def _sevenzip_entries(listing):
    # 7-Zip on Windows can emit CR CR LF; normalize before parsing records.
    listing = re.sub(r"\r+\n", "\n", listing)
    entries, seen, total = [], set(), 0
    for block in listing.strip("\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if " = " not in line:
                raise ArchiveError("7Z 文件清单无法识别")
            key, value = line.split(" = ", 1)
            if key in fields:
                raise ArchiveError("7Z 文件清单包含重复字段")
            fields[key] = value
        if "Path" not in fields:
            raise ArchiveError("7Z 文件清单缺少路径")
        name = safe_member_name(fields["Path"])
        identity = name.rstrip("/").casefold()
        if identity in seen:
            raise ArchiveError(f"7Z 内有重复路径：{name}")
        seen.add(identity)
        if fields.get("Encrypted") == "+":
            raise ArchiveError("此 7Z 已加密，请先使用密码解压为文件夹，再在编辑器中打开文件夹")
        attributes = fields.get("Attributes", "")
        if (any(k in fields for k in ("Symbolic Link", "Hard Link", "Reparse Point"))
                or "l" in attributes.split(" ")[-1] or "L" in attributes):
            raise ArchiveError("7Z 含链接或文件重定向，请先复制为普通文件夹")
        directory = fields.get("Folder") == "+" or attributes.startswith("D")
        try:
            size = int(fields["Size"])
            crc = int(fields["CRC"], 16) if fields.get("CRC") else None
            if size < 0 or (directory and size) or (size and crc is None):
                raise ValueError()
            modified = fields.get("Modified", "").split(".")[0]
            stamp = datetime.strptime(modified, "%Y-%m-%d %H:%M:%S").timetuple()[:6] if modified else (1980, 1, 1, 0, 0, 0)
        except (KeyError, ValueError) as exc:
            raise ArchiveError("7Z 文件清单中的大小、日期或校验值无效") from exc
        entries.append((name, directory, size, crc, stamp))
        total += size
    if not entries or len(entries) > 100000:
        raise ArchiveError("7Z 文件清单为空或过大")
    if total > 8 * 1024 ** 3:
        raise ArchiveError("7Z 解压后的内容超过 8 GB，建议先解压为文件夹")
    return entries, total


def sevenzip_to_zip(source, destination):
    tool = sevenzip_path()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    source = str(Path(source).resolve())
    try:
        result = subprocess.run([str(tool), "l", "-t7z", "-slt", "-ba", "-sccUTF-8", "-p-", "--", source],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=300, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError("7Z 读取超时，请先解压为文件夹") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace")
        if "password" in message.lower() or "encrypted" in message.lower():
            raise ArchiveError("此 7Z 已加密，请先使用密码解压为文件夹，再在编辑器中打开文件夹")
        raise ArchiveError("7Z 无法读取，请检查压缩包是否损坏或分卷是否齐全")
    try:
        entries, total = _sevenzip_entries(result.stdout.decode("utf-8"))
    except UnicodeError as exc:
        raise ArchiveError("7Z 文件清单字符编码无法读取") from exc
    if shutil.disk_usage(Path(destination).parent).free < total + 64 * 1024 ** 2:
        raise ArchiveError("临时目录剩余空间不足，请先解压到空间足够的磁盘，再打开文件夹")
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(tool), "x", "-t7z", "-so", "-bso0", "-bse2", "-bsp0", "-y", "-p-", "--", source],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, creationflags=flags)
        timer = threading.Timer(300, process.kill)
        timer.daemon = True
        timer.start()
        try:
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as target:
                for name, directory, size, expected_crc, stamp in entries:
                    stamp = stamp if 1980 <= stamp[0] <= 2107 else (1980, 1, 1, 0, 0, 0)
                    entry = zipfile.ZipInfo(name.rstrip("/") + "/" if directory else name, stamp)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    if directory:
                        entry.external_attr = (0o40755 << 16) | 0x10
                        target.writestr(entry, b"")
                        continue
                    remaining, crc = size, 0
                    with target.open(entry, "w", force_zip64=size >= zipfile.ZIP64_LIMIT) as output:
                        while remaining:
                            chunk = process.stdout.read(min(128 * 1024, remaining))
                            if not chunk:
                                raise ArchiveError("7Z 内容不完整或解压失败，请检查压缩包是否损坏")
                            output.write(chunk)
                            crc = zlib.crc32(chunk, crc)
                            remaining -= len(chunk)
                    # Also validates that streaming order matches the listed paths.
                    if expected_crc is not None and crc != expected_crc:
                        raise ArchiveError(f"7Z 文件校验失败：{name}")
                if process.stdout.read(1):
                    raise ArchiveError("7Z 数据长度与文件清单不一致，未打开此模组")
            if process.wait(timeout=10) != 0:
                raise ArchiveError("7Z 解压校验失败，请检查压缩包或分卷完整性")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def rar_to_zip(source, destination):
    tool = unrar_path()
    try:
        with rarfile.RarFile(source, errors="strict") as archive:
            if archive.needs_password():
                raise ArchiveError("此 RAR 已加密，请先使用密码解压为文件夹，再在编辑器中打开文件夹")
            entries = list(archive.infolist())
            if not entries or len(entries) > 100000:
                raise ArchiveError("RAR 文件清单为空或过大")
            names, seen, total = [], set(), 0
            for info in entries:
                name = safe_member_name(info.filename)
                identity = name.rstrip("/").casefold()
                if identity in seen:
                    raise ArchiveError(f"RAR 内有重复路径：{name}")
                if info.is_symlink() or info.file_redir is not None:
                    raise ArchiveError("RAR 含链接或文件重定向，暂不能安全读取；请先复制为普通文件夹")
                seen.add(identity)
                names.append(name)
                total += info.file_size
            if total > 8 * 1024 ** 3:
                raise ArchiveError("RAR 解压后的内容超过 8 GB，建议先解压为文件夹")
            if shutil.disk_usage(Path(destination).parent).free < total + 64 * 1024 ** 2:
                raise ArchiveError("临时目录剩余空间不足，请先解压到空间足够的磁盘，再打开文件夹")
            # A single pass also avoids repeatedly decompressing solid RAR archives.
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen([str(tool), "p", "-inul", "-p-", "-y", "--", str(Path(source).resolve())],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, creationflags=flags)
                timer = threading.Timer(300, process.kill)
                timer.daemon = True
                timer.start()
                try:
                    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as target:
                        target.comment = (archive.comment or "").encode("utf-8")[:65535]
                        for info, name in zip(entries, names):
                            stamp = info.date_time or (1980, 1, 1, 0, 0, 0)
                            stamp = stamp if 1980 <= stamp[0] <= 2107 else (1980, 1, 1, 0, 0, 0)
                            entry = zipfile.ZipInfo(name.rstrip("/") + "/" if info.is_dir() else name, stamp)
                            entry.compress_type = zipfile.ZIP_DEFLATED
                            if info.is_dir():
                                entry.external_attr = (0o40755 << 16) | 0x10
                                target.writestr(entry, b"")
                                continue
                            remaining = info.file_size
                            with target.open(entry, "w", force_zip64=remaining >= zipfile.ZIP64_LIMIT) as output:
                                while remaining:
                                    chunk = process.stdout.read(min(128 * 1024, remaining))
                                    if not chunk:
                                        raise ArchiveError("RAR 内容不完整或无法解压，请检查分卷是否齐全、压缩包是否损坏")
                                    output.write(chunk)
                                    remaining -= len(chunk)
                        if process.stdout.read(1):
                            raise ArchiveError("RAR 数据长度与文件清单不一致，未打开此模组")
                    if process.wait(timeout=10) != 0:
                        errors.seek(0)
                        message = errors.read(4096).decode("utf-8", errors="replace").strip()
                        raise ArchiveError("RAR 解压校验失败；请检查分卷或文件完整性" + (f"：{message}" if message else ""))
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
    except rarfile.NeedFirstVolume as exc:
        raise ArchiveError("请打开 RAR 第一卷（通常为 .part1.rar），并将所有分卷放在同一目录") from exc
    except rarfile.Error as exc:
        raise ArchiveError(f"RAR 无法读取，请检查文件和分卷完整性：{exc}") from exc
