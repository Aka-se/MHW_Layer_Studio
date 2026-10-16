"""Read-only MRL3 resource metadata. No shaders or texture payloads are decoded."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialInfo:
    name: str
    name_hash: int | None = None
    textures: tuple[str, ...] = ()
    status: str = "未找到配套 MRL3"
    color_texture: str | None = None


@dataclass(frozen=True)
class MaterialBindings:
    textures: tuple[str, ...]
    color_texture: str | None = None


def read_mrl3(data: bytes) -> dict[int, tuple[str, ...]]:
    return {hashed: binding.textures for hashed, binding in read_mrl3_bindings(data).items()}


def read_mrl3_bindings(data: bytes) -> dict[int, MaterialBindings]:
    if len(data) < 40:
        raise ValueError("MRL3 文件过短")
    magic, _, count, texture_count, texture_offset, material_offset = struct.unpack_from("<I12sIIQQ", data)
    if magic != 0x004C524D:
        raise ValueError("MRL3 标识无效")
    if texture_offset + texture_count * 272 > len(data) or material_offset + count * 56 > len(data):
        raise ValueError("MRL3 表超出文件范围")
    textures = [data[texture_offset + i * 272 + 16:texture_offset + (i + 1) * 272]
                .split(b"\0", 1)[0].decode("utf-8", errors="replace") for i in range(texture_count)]
    result = {}
    for i in range(count):
        offset = material_offset + i * 56
        name_hash = struct.unpack_from("<I", data, offset + 4)[0]
        resource_size = data[offset + 22] * 8
        address = struct.unpack_from("<I", data, offset + 48)[0]
        material_size = struct.unpack_from("<I", data, offset + 16)[0]
        if resource_size % 16 or resource_size > material_size or address + material_size > len(data):
            raise ValueError("MRL3 资源绑定超出文件范围")
        paths, color = [], None
        color_rank = 99
        for binding in range(address, address + resource_size, 16):
            if data[binding] & 0xF == 2:
                index = struct.unpack_from("<I", data, binding + 8)[0]
                if index and not 1 <= index <= len(textures):
                    raise ValueError("MRL3 贴图索引无效")
                if index and textures[index - 1] not in paths:
                    paths.append(textures[index - 1])
                # Known color slots from Mod3-MHW-Importer/mrl3/maptype.py.
                # Some VFX models store their entire painted color in emissive.
                # Prefer albedo when present; preview emissive as an ordinary image.
                descriptor = struct.unpack_from("<I", data, binding + 4)[0] >> 12
                rank = {0x52AD9: 0, 0x76826: 1}.get(descriptor, 99)
                if index and rank < color_rank:
                    color = textures[index - 1]
                    color_rank = rank
        if name_hash in result:
            raise ValueError("MRL3 材质哈希重复，无法唯一关联")
        result[name_hash] = MaterialBindings(tuple(paths), color)
    return result


def material_hash(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) ^ 0xFFFFFFFF
