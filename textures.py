"""Read-only color texture lookup and TEX mip decoding for the editor preview."""
from __future__ import annotations

import io
import re
import struct
import zipfile
import os
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image
from materials import MaterialInfo, material_hash, read_mrl3_bindings


class TextureError(ValueError):
    pass


def decode_tex(data: bytes, maximum=1024):
    def preview(image):
        # Several custom models have one 4096px mip only. Keep the decoded
        # cache at the requested preview size even when no smaller mip exists.
        image.thumbnail((maximum, maximum))
        return image
    if len(data) < 192 or data[:4] != b"TEX\0" or struct.unpack_from("<I", data, 4)[0] != 16:
        raise TextureError("不支持的 TEX 版本")
    mip_count, width, height = struct.unpack_from("<III", data, 20)
    kind = struct.unpack_from("<I", data, 36)[0]
    # BC1 sRGB has identical compressed pixels; Pillow accepts its UNORM container.
    formats = {7: (28, 4), 9: (29, 4), 19: (49, 2), 22: (71, 8), 23: (71, 8),
               24: (80, 8), 26: (83, 16), 30: (98, 16), 31: (99, 16)}
    if kind not in formats:
        raise TextureError(f"暂不支持 TEX 格式 {kind}")
    if not 1 <= mip_count <= 32 or not 1 <= width <= 32768 or not 1 <= height <= 32768 or 184 + mip_count * 8 > len(data):
        raise TextureError("TEX 尺寸或 mip 表异常")
    mip = 0
    while max(width >> mip, height >> mip) > maximum and mip + 1 < mip_count:
        mip += 1
    width, height = max(1, width >> mip), max(1, height >> mip)
    if width * height > 16 * 1024 * 1024:
        raise TextureError("贴图过大且缺少可用缩略层")
    offset = struct.unpack_from("<Q", data, 184 + mip * 8)[0]
    dxgi, unit = formats[kind]
    length = width * height * unit if kind in (7, 9, 19) else ((width + 3) // 4) * ((height + 3) // 4) * unit
    if offset < 184 + mip_count * 8 or offset + length > len(data):
        raise TextureError("TEX 像素数据超出文件")
    pixels = data[offset:offset + length]
    if kind in (7, 9):
        return preview(Image.frombytes("RGBA", (width, height), pixels))
    if kind == 19:
        rg = np.frombuffer(pixels, np.uint8).reshape(height, width, 2)
        rgba = np.full((height, width, 4), 255, np.uint8)
        rgba[:, :, :2], rgba[:, :, 2] = rg, 0
        return preview(Image.fromarray(rgba))
    # DDS DX10 is a container understood by Pillow's BC1/4/5/7 decoder.
    header = struct.pack("<31I", 124, 0x21007, height, width, length, 0, 1,
                         *([0] * 11), 32, 4, int.from_bytes(b"DX10", "little"),
                         0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
    with Image.open(io.BytesIO(b"DDS " + header + struct.pack("<5I", dxgi, 3, 0, 1, 0) + pixels)) as image:
        return preview(image.convert("RGBA"))


def discover_texture_roots(source=None):
    """Locate loose nativePC assets, without scanning disks or changing the game."""
    candidates = []
    # Common Steam libraries on the source/current drive, checked directly (no recursive scan).
    anchors = {Path.cwd().anchor, Path(source).resolve().anchor if source else Path.cwd().anchor}
    candidates += [Path(anchor) / "SteamLibrary/steamapps/common/Monster Hunter World/nativePC" for anchor in anchors]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "Steam/steamapps/common/Monster Hunter World/nativePC")
    if source:
        path = Path(source).resolve()
        for parent in [path if path.is_dir() else path.parent, *path.parents]:
            if parent.name.lower() == "nativepc":
                candidates.append(parent)
            if (parent / "nativePC").is_dir():
                candidates.append(parent / "nativePC")
            if parent.name.lower() == "common":
                candidates.append(parent / "Monster Hunter World" / "nativePC")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        libraries = [steam]
        config = steam / "steamapps/libraryfolders.vdf"
        if config.exists():
            libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"\n]+)"', config.read_text(encoding="utf-8"))]
        candidates += [p / "steamapps/common/Monster Hunter World/nativePC" for p in libraries]
    except (ImportError, OSError):
        pass
    return list(dict.fromkeys(str(p.resolve()) for p in candidates if p.is_dir()))


class TextureLibrary:
    def __init__(self, workspace, roots=(), overrides=None, game=None):
        self.workspace = workspace
        self.roots = tuple(Path(root).resolve() for root in roots)
        self.overrides = dict(overrides or {})
        self.game = game
        self.cache = {}
        self.status = {}
        self._archive_names = None
        self._material_attempts = set()
        self._material_fallbacks = {}
        self._material_errors = {}

    def material_info(self, document, material_id):
        # Preview-only bindings stay with this library, not the document/session.
        # Changing game roots rebuilds the library and discards old fallbacks.
        return self._material_fallbacks.get((document.name, material_id), document.material_info(material_id))

    def _load_missing_materials(self, document):
        if document.name in self._material_attempts:
            return
        self._material_attempts.add(document.name)
        name = document.name.replace("\\", "/")
        marker = name.lower().find("nativepc/")
        reference = str(PurePosixPath(name[marker + 9:] if marker >= 0 else name).with_suffix(".mrl3"))
        try:
            data, source = self._read(document, reference, extension=".mrl3")
            bindings = read_mrl3_bindings(data)
            for mid, name in enumerate(document.material_names):
                current = document.material_info(mid)
                if current.textures or current.color_texture:
                    continue
                hashed = material_hash(name)
                binding = bindings.get(hashed)
                if binding:
                    self._material_fallbacks[(document.name, mid)] = MaterialInfo(
                        name, hashed, binding.textures, "预览补读 MRL3 · " + source, binding.color_texture)
        except (ValueError, OSError, KeyError, struct.error, NotImplementedError) as exc:
            self._material_errors[document.name] = "配套 MRL3 补读失败：" + str(exc)

    @staticmethod
    def color_reference(document, material_id, material=None):
        material = material if material is not None else document.material_info(material_id)
        if material.color_texture:
            return material.color_texture
        references = material.textures
        return next((p for p in references if re.search(r"(?:_bml|_bm|albedo|diffuse)(?:\.tex)?$", p, re.I)), None)

    @staticmethod
    def _paths(reference, document, extension=".tex"):
        normalized = reference.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(p in ("..", ".") or ":" in p for p in path.parts):
            raise TextureError("贴图路径无效")
        normalized = normalized.removeprefix("/")
        if not normalized.lower().endswith(extension):
            normalized += extension
        if normalized.lower().startswith("nativepc/"):
            normalized = normalized[9:]
        # Optional mod variants may each contain their own nativePC folder.
        marker = document.name.lower().find("nativepc/")
        prefix = document.name[:marker] if marker >= 0 else ""
        return list(dict.fromkeys([prefix + "nativePC/" + normalized, "nativePC/" + normalized, normalized]))

    def _read(self, document, reference, extension=".tex"):
        paths = self._paths(reference, document, extension)
        workspace = self.workspace
        if workspace.kind in ("zip", "rar", "7z"):
            with zipfile.ZipFile(workspace.archive_path) as archive:
                if self._archive_names is None:
                    self._archive_names = {n.replace("\\", "/").lower(): n for n in archive.namelist()}
                for name in paths:
                    entry = self._archive_names.get(name.lower())
                    if entry:
                        if archive.getinfo(entry).file_size > 256 * 1024 * 1024:
                            raise TextureError("贴图文件过大")
                        return archive.read(entry), "模组内 · " + entry
        else:
            root = workspace.source if workspace.kind == "folder" else workspace.source.parent
            for name in paths:
                candidate = root / name
                if candidate.is_file():
                    return self._read_file(candidate), str(candidate)
        for root in self.roots:
            for name in paths:
                candidate = (root / name).resolve()
                if root not in candidate.parents:
                    continue
                if candidate.is_file():
                    return self._read_file(candidate), str(candidate)
        if self.game:
            if extension == ".mrl3":
                getter = getattr(self.game, "get_material", None)
                if getter:
                    return getter(reference)
            else:
                return self.game.get(reference)
        raise TextureError("未找到配套材质；请在设置中选择游戏资源目录" if extension == ".mrl3"
                           else "未找到颜色图；可设置游戏资源目录、共享 nativePC 目录或指定颜色图")

    @staticmethod
    def _read_file(path):
        if path.stat().st_size > 256 * 1024 * 1024:
            raise TextureError("贴图文件过大")
        return path.read_bytes()

    def get(self, document, material_id):
        key = (document.name, material_id)
        if key in self.cache:
            return self.cache[key]
        try:
            override = self.overrides.get(key)
            material = self.material_info(document, material_id)
            if not override and not material.textures and not material.color_texture:
                self._load_missing_materials(document)
                material = self.material_info(document, material_id)
            reference = self.color_reference(document, material_id, material)
            if override:
                data, source = self._read_file(Path(override)), str(override)
            elif reference:
                try:
                    data, source = self._read(document, reference)
                except TextureError:
                    # Exact game constant maps can be represented for preview
                    # without copying game assets into the program or the mod.
                    constants = {"assets/default_tex/null_white": (255, 255, 255, 255),
                                 "assets/default_tex/null_black": (0, 0, 0, 255),
                                 "assets/default_tex/null_gray_lin": (128, 128, 128, 255)}
                    constant = reference.replace("\\", "/").lower().removesuffix(".tex")
                    if constant not in constants:
                        raise
                    self.cache[key] = np.array([[constants[constant]]], dtype=np.uint8)
                    self.status[key] = "预览常量颜色 · " + reference + "（未模拟 Shader 材质染色）"
                    return self.cache[key]
            else:
                raise TextureError(self._material_errors.get(document.name, "材质没有颜色图记录；可手动指定"))
            if data[:4] == b"TEX\0":
                image = decode_tex(data)
            else:
                with Image.open(io.BytesIO(data)) as opened:
                    if opened.width * opened.height > 64 * 1024 * 1024:
                        raise TextureError("图片过大")
                    image = opened.convert("RGBA")
                    image.thumbnail((1024, 1024))
            array = np.ascontiguousarray(image, dtype=np.uint8)
            self.cache[key] = array
            self.status[key] = f"颜色图 {image.width}×{image.height} · {source}"
        except (ValueError, OSError, KeyError, struct.error, NotImplementedError) as exc:
            self.cache[key] = None
            self.status[key] = str(exc)
        return self.cache[key]
