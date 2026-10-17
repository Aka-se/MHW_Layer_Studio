"""Read-only Iceborne CMP/PKG texture lookup, on-demand decode and local caches."""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath

import numpy as np

BLOCK = 0x40000
KEYS = tuple(bytes.fromhex(s) for s in (
    "ac76cb97ec7500133a81038e7a82c80a", "6bb5c956e44d00bc305233cfbfaafa25",
    "8f021dccb0f2787206fbdee2390bbb5c", "da5c1e531d8359157875bd63bfaafa25",
    "1f6d31c883dd716d7e8f598ce23f1929", "4bb0de04e4e0856980ccb2942f9ce9f9",
    "8bce54dc4c11139a7875bd63bfaafa25", "ec13345966ce7312440089a2ceddcee9",
    "e4662c709c753a039a2c0f5ae23f1929", "a492fc9033949c15a033ac223735cca7",
    "25099c1f911a26e5ce9172f07a82c80a", "d1d29d7446d4fdf1a033ac223735cca7",
    "7eb268373b5d361ed6d313e2933c4dcb", "a1c7d2ea661895ac7875bd63bfaafa25",
    "82a43b2108797c6a440089a2ceddcee9", "41d055b3dd6015167e8f598ce23f1929"))


class ResourceError(ValueError):
    pass


def assets_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"


def user_data_directory():
    override = os.environ.get("MHW_LAYER_STUDIO_DATA_DIR")
    if override:
        return Path(override).resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
    return base / "MHW Layer Studio"


def cache_directory():
    return user_data_directory() / "game-textures"


def saved_game_directory():
    value = read_settings().get("game_directory")
    return value if isinstance(value, str) and len(value) < 4096 else None


def read_settings():
    try:
        value = json.loads((user_data_directory() / "settings.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def update_settings(**changes):
    settings = read_settings()
    settings.update(changes)
    _atomic_bytes(user_data_directory() / "settings.json", json.dumps(settings, ensure_ascii=False).encode("utf-8"))


def save_game_directory(directory):
    update_settings(game_directory=str(directory))


def normalize_reference(name):
    return _normalize_reference(name, ".tex")


def _normalize_reference(name, extension):
    name = name.replace("\\", "/").lstrip("/")
    if name.lower().startswith("nativepc/"):
        name = name[9:]
    if not name or any(p in ("", ".", "..") or ":" in p for p in name.split("/")) or any(ord(c) < 32 for c in name):
        raise ResourceError("游戏贴图路径无效")
    return name.lower() if name.lower().endswith(extension) else name.lower() + extension


def chunk_paths(directory):
    root = Path(directory).resolve()
    if root.name.lower() == "chunk":
        root = root.parent
    files = [p for p in (root / "chunk").glob("chunkG*.bin") if re.fullmatch(r"chunkG\d+\.bin", p.name, re.I)]
    if not files:
        raise ResourceError("此目录没有找到冰原 chunkG*.bin；请选择游戏安装目录")
    return root, sorted(files, key=lambda p: int(re.search(r"\d+", p.name)[0]), reverse=True)


def _atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        staged = Path(temp.name)
        temp.write(data)
    try:
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


class OozWorker:
    def __init__(self):
        self.process = None

    @staticmethod
    def _read(stream, size):
        out = bytearray()
        while len(out) < size:
            chunk = stream.read(size - len(out))
            if not chunk:
                raise ResourceError("游戏资源解压失败，组件已停止；请检查资源包完整性")
            out.extend(chunk)
        return bytes(out)

    def decode(self, data):
        if self.process is None:
            exe = assets_dir() / "ooz/OOZWorker.exe"
            command = [str(exe)] if getattr(sys, "frozen", False) else [sys.executable, "-u", str(assets_dir() / "ooz/worker.py")]
            if not Path(command[-1]).is_file():
                raise ResourceError("缺少游戏资源读取组件，请重新安装完整版本")
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        process = self.process
        timer = threading.Timer(30, process.kill)
        timer.daemon = True
        timer.start()
        try:
            process.stdin.write(struct.pack("<II", len(data), BLOCK) + data)
            process.stdin.flush()
            size = struct.unpack("<I", self._read(process.stdout, 4))[0]
            if size != BLOCK:
                raise ResourceError("此资源块暂无法解压，请检查游戏文件版本或完整性")
            return self._read(process.stdout, size)
        except (OSError, ResourceError):
            self.close()
            raise ResourceError("游戏资源解压失败，请检查游戏文件版本或完整性")
        finally:
            timer.cancel()

    def close(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdin.close()
            process.stdout.close()

    def __del__(self):
        self.close()


class ChunkReader:
    def __init__(self, path, worker, pattern, cache):
        self.path, self.worker, self.pattern = Path(path), worker, pattern
        self.signature = [str(self.path.resolve()), self.path.stat().st_size, self.path.stat().st_mtime_ns]
        self.cache_id = hashlib.sha256(json.dumps([self.signature, hashlib.sha256(pattern).hexdigest(), 1]).encode()).hexdigest()
        self.cache = Path(cache) / self.cache_id
        self.blocks = OrderedDict()
        self.records = None
        with self.path.open("rb") as f:
            header = f.read(8)
            if len(header) != 8 or header[:4] != b"CMP\0":
                raise ResourceError(f"不支持此资源包格式：{self.path.name}")
            count = struct.unpack_from("<I", header, 4)[0]
            if not 1 <= count <= len(pattern) or 8 + count * 8 > self.signature[1]:
                raise ResourceError("资源包索引超出范围")
            self.table = np.frombuffer(f.read(count * 8), dtype="<u8")
        offsets = self.table >> np.uint64(24)
        sizes = (self.table & np.uint64(0xffffff)) >> np.uint64(4)
        if np.any(offsets < 8 + count * 8) or np.any(offsets + sizes > self.signature[1]):
            raise ResourceError("资源包数据位置无效")

    def block(self, index):
        if index in self.blocks:
            self.blocks.move_to_end(index)
            return self.blocks[index]
        if not 0 <= index < len(self.table):
            raise ResourceError("资源块索引超出范围")
        record = int(self.table[index])
        size, offset = (record & 0xffffff) >> 4, record >> 24
        with self.path.open("rb") as f:
            f.seek(offset)
            encoded = f.read(size or BLOCK)
        data = self.worker.decode(encoded) if size else encoded
        key_index = self.pattern[index]
        if key_index >= len(KEYS) or len(data) != BLOCK:
            raise ResourceError("资源包密钥或资源块长度不支持")
        key = np.frombuffer(KEYS[key_index], np.uint8)
        decoded = np.bitwise_xor(np.frombuffer(data, np.uint8).reshape(-1, 16), key).tobytes()
        self.blocks[index] = decoded
        while len(self.blocks) > 4:
            self.blocks.popitem(last=False)
        return decoded

    def read(self, offset, size):
        if offset < 0 or not 0 <= size <= 256 * 1024 ** 2 or offset + size > len(self.table) * BLOCK:
            raise ResourceError("游戏资源大小或位置异常")
        result = bytearray()
        while size:
            index, start = divmod(offset, BLOCK)
            part = self.block(index)[start:start + min(size, BLOCK - start)]
            result.extend(part)
            size -= len(part)
            offset += len(part)
        return bytes(result)

    def index(self):
        if self.records is not None:
            return self.records
        index_path = self.cache / "index.json"
        try:
            if index_path.stat().st_size > 64 * 1024 ** 2:
                raise ValueError()
            data = json.loads(index_path.read_text(encoding="utf-8"))
            # Version 1 indexed TEX only. Rebuild it once to include companion
            # materials, otherwise a valid old cache silently hides every MRL3.
            if data.get("version") == 2 and data.get("signature") == self.signature and isinstance(data.get("resources"), dict):
                records = data["resources"]
                for name, (offset, size) in records.items():
                    extension = Path(name).suffix
                    if extension not in (".tex", ".mrl3") or _normalize_reference(name, extension) != name or type(offset) is not int or type(size) is not int or not 0 < size <= 256 * 1024 ** 2 or not 0 <= offset < offset + size <= len(self.table) * BLOCK:
                        raise ValueError()
                self.records = records
                return records
        except (OSError, ValueError, TypeError, KeyError):
            pass
        header = self.read(0, 0x100)
        if header[:4] != b"PKG ":
            raise ResourceError("仅支持冰原资源包，游戏资源索引标识不正确")
        parents, children = struct.unpack_from("<II", header, 12)
        if parents > 500000 or children > 2000000:
            raise ResourceError("游戏资源文件清单过大")
        data = self.read(0x100, parents * 84 + children * 184)
        cursor, seen, records = 0, 0, {}
        for _ in range(parents):
            if cursor + 84 > len(data):
                raise ResourceError("资源目录表不完整")
            count = struct.unpack_from("<I", data, cursor + 80)[0]
            cursor += 84
            if seen + count > children:
                raise ResourceError("资源文件数与目录表不一致")
            for _ in range(count):
                row = data[cursor:cursor + 184]
                if len(row) != 184:
                    raise ResourceError("游戏资源文件表不完整")
                cursor += 184
                size, offset, kind = struct.unpack_from("<QQI", row, 160)
                if kind not in (0, 2):
                    continue
                name = row[:80 if kind == 2 else 160].split(b"\0", 1)[0].decode("ascii", errors="strict").replace("\\", "/").lstrip("/")
                extension = Path(name).suffix.lower()
                if extension in (".tex", ".mrl3"):
                    name = _normalize_reference(name, extension)
                    if not 0 < size <= 256 * 1024 ** 2 or offset + size > len(self.table) * BLOCK:
                        raise ResourceError("游戏贴图索引无效")
                    records[name] = (offset, size)
            seen += count
        if seen != children:
            raise ResourceError("游戏资源清单未完整读取")
        self.records = records
        try:
            _atomic_bytes(index_path, json.dumps({"version": 2, "signature": self.signature, "resources": records}, separators=(",", ":")).encode())
        except OSError:
            pass  # A full cache disk must not prevent read-only preview.
        return records

    def texture(self, name):
        return self.resource(name)

    def resource(self, name):
        offset, size = self.index()[name]
        key = hashlib.sha256(name.encode()).hexdigest()
        extension = Path(name).suffix
        magic = {".tex": b"TEX\0", ".mrl3": b"MRL\0"}[extension]
        path = self.cache / (key + extension)
        try:
            if path.stat().st_size != size:
                raise OSError("invalid cached length")
            data = path.read_bytes()
            checksum = path.with_suffix(".sha256").read_text()
            if len(data) == size and data[:4] == magic and hashlib.sha256(data).hexdigest() == checksum:
                return data, True
        except OSError:
            pass
        data = self.read(offset, size)
        if data[:4] != magic:
            raise ResourceError(f"索引对应的资源不是 {extension[1:].upper()} 文件")
        try:
            _atomic_bytes(path, data)
            _atomic_bytes(path.with_suffix(".sha256"), hashlib.sha256(data).hexdigest().encode())
        except OSError:
            pass
        return data, False


class GameTextures:
    def __init__(self, directory, cache=None):
        self.root, self.paths = chunk_paths(directory)
        self.cache = Path(cache) if cache is not None else cache_directory()
        if self.cache.resolve().is_relative_to(self.root):
            raise ResourceError("贴图缓存目录不能放在游戏目录中；请更换编辑器缓存位置")
        raw = (assets_dir() / "mhwchunk/chunk.key").read_bytes()
        start, end = struct.unpack_from("<II", raw)
        if start != 1 or len(raw) != 8 + end or max(raw[8:]) >= 16:
            raise ResourceError("游戏资源读取表不完整，请重新安装编辑器")
        self.pattern = raw[8:]
        self.worker = OozWorker()
        self.readers = {}
        self.lock = threading.RLock()
        self.progress = "游戏资源待读取"
        self.closed = False

    def get(self, reference):
        return self._get(reference, ".tex", "贴图")

    def get_material(self, reference):
        return self._get(reference, ".mrl3", "材质")

    def _get(self, reference, extension, label):
        name = _normalize_reference(reference, extension)
        with self.lock:
            self.root, self.paths = chunk_paths(self.root)
            for i, path in enumerate(self.paths):
                if self.closed:
                    raise ResourceError("游戏资源读取已取消")
                self.progress = f"正在查找游戏{label} · {i + 1}/{len(self.paths)} · {path.name}"
                current = [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns]
                if path not in self.readers or self.readers[path].signature != current:
                    self.readers[path] = ChunkReader(path, self.worker, self.pattern, self.cache)
                reader = self.readers[path]
                if name in reader.index():
                    data, cached = reader.resource(name)
                    self.progress = f"游戏{label}已加载"
                    return data, f"游戏资源 · {path.name} · {name}" + (" · 缓存" if cached else "")
            self.progress = f"游戏资源中未找到此{label}"
            raise ResourceError(f"游戏资源中没有此{label}：{reference}")

    def close(self):
        self.closed = True
        self.worker.close()

    def __del__(self):
        if hasattr(self, "worker"):
            self.worker.close()
