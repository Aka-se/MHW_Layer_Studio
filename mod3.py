from __future__ import annotations

import os
import shutil
import stat
import struct
import re
import tempfile
import zipfile
import json
from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path
from typing import Iterable

import numpy as np
from mesh_features import read_bones

from materials import MaterialInfo, material_hash, read_mrl3_bindings
from health_rules import validate_thresholds, layer_name


BASE_HEADER = struct.Struct("<IBBHHHiiiiiQQQQQQQQQQ")
MESH_HEADER_SIZE = 80
MOD3_MAGIC = 0x00444F4D
EDITOR_ENTRY = ".mhw-layer-studio/editor.json"
UV_BLOCKS = {0xA756F2F9, 0xA5104CA0, 0xC3AC03A1, 0xC9690AB8, 0x818904DC,
             0x0F06033F, 0xF637401C, 0xF471FE45, 0x3C730760, 0xB2FC0083,
             0x81F58067, 0x83B33E3E, 0x366995A7, 0xB8E69244}


class Mod3Error(ValueError):
    pass


@dataclass
class MeshPart:
    index: int
    header_offset: int
    visible_condition_offset: int
    visible_condition: int
    material_id: int
    lod: int
    vertex_count: int
    index_count: int
    block_size: int
    block_type: int
    shadow_flags: int
    vertices: np.ndarray
    faces: np.ndarray
    uvs: np.ndarray | None = None
    bone_ids: np.ndarray | None = None
    bone_weights: np.ndarray | None = None
    bone_status: str = "未读取"

    @property
    def triangle_count(self) -> int:
        return int(len(self.faces))

    @property
    def layer(self) -> str:
        return {
            0: "基础/原始",
            60: "装饰层",
            61: "护甲层",
            62: "内层",
        }.get(self.visible_condition, "自定义")


class Mod3Document:
    def __init__(self, name: str, data: bytes):
        self.name = name.replace("\\", "/")
        self._original = bytes(data)
        self._data = bytearray(data)
        self.meshes: list[MeshPart] = []
        self.material_count = 0
        self.material_names: list[str] = []
        self.materials: dict[int, MaterialInfo] = {}
        self.vertex_offset = 0
        self.face_offset = 0
        self._parse()

    @property
    def changed(self) -> bool:
        return self._data != self._original

    def _parse(self) -> None:
        if len(self._data) < BASE_HEADER.size:
            raise Mod3Error(f"{self.name}: 文件过小，无法成为 MOD3")
        values = BASE_HEADER.unpack_from(self._data, 0)
        magic = values[0]
        if magic != MOD3_MAGIC:
            raise Mod3Error(f"{self.name}: MOD3 标识错误 0x{magic:08X}")
        mesh_count = values[4]
        self.material_count = values[5]
        material_offset = values[15]
        if self.material_count and material_offset + self.material_count * 128 <= len(self._data):
            self.material_names = [bytes(self._data[material_offset + i * 128:material_offset + (i + 1) * 128])
                                   .split(b"\0", 1)[0].decode("utf-8", errors="replace")
                                   for i in range(self.material_count)]
        mesh_offset = values[16]
        self.vertex_offset = values[17]
        self.face_offset = values[18]
        if mesh_offset + mesh_count * MESH_HEADER_SIZE > len(self._data):
            raise Mod3Error(f"{self.name}: Mesh 表超出文件范围")

        for index in range(mesh_count):
            offset = mesh_offset + index * MESH_HEADER_SIZE
            shadow, vertex_count, condition, material = struct.unpack_from("<HHHH", self._data, offset)
            lod = struct.unpack_from("<i", self._data, offset + 8)[0]
            block_size = self._data[offset + 14]
            vertex_sub, vertex_local, block_type, face_local, face_count, vertex_base = struct.unpack_from(
                "<IIIIII", self._data, offset + 16
            )
            if block_size < 12:
                raise Mod3Error(f"{self.name}: Mesh {index} 顶点步长异常: {block_size}")
            face_start = self.face_offset + face_local * 2
            face_end = face_start + face_count * 2
            if face_count % 3 or face_end > len(self._data):
                raise Mod3Error(f"{self.name}: Mesh {index} 索引缓冲区异常")
            raw_faces = np.frombuffer(self._data, dtype="<u2", count=face_count, offset=face_start).astype(np.int64)
            faces = raw_faces.reshape((-1, 3))
            adjustment = max(0, int(vertex_sub - faces.min())) if len(faces) else 0
            faces = faces + adjustment - vertex_sub
            vertex_start = self.vertex_offset + vertex_local + block_size * (
                vertex_sub + vertex_base - adjustment
            )
            vertex_end = vertex_start + vertex_count * block_size
            if vertex_start < 0 or vertex_end > len(self._data):
                raise Mod3Error(f"{self.name}: Mesh {index} 顶点缓冲区异常")
            vertices = np.empty((vertex_count, 3), dtype=np.float32)
            for vertex_index in range(vertex_count):
                vertices[vertex_index] = struct.unpack_from(
                    "<fff", self._data, vertex_start + vertex_index * block_size
                )
            valid = np.all((faces >= 0) & (faces < vertex_count), axis=1)
            uvs = None
            if block_type in UV_BLOCKS and block_size >= 24:
                uvs = np.ndarray((vertex_count, 2), dtype="<f2", buffer=self._data,
                                 offset=vertex_start + 20, strides=(block_size, 2)).astype(np.float32)
                if not np.isfinite(uvs).all():
                    uvs = None
            bone_ids, bone_weights, bone_status = read_bones(self._data, vertex_start, vertex_count, block_size, block_type)
            self.meshes.append(
                MeshPart(
                    index=index,
                    header_offset=offset,
                    visible_condition_offset=offset + 4,
                    visible_condition=condition,
                    material_id=material,
                    lod=lod,
                    vertex_count=vertex_count,
                    index_count=face_count,
                    block_size=block_size,
                    block_type=block_type,
                    shadow_flags=shadow,
                    vertices=vertices,
                    faces=faces[valid].astype(np.int32),
                    uvs=uvs,
                    bone_ids=bone_ids,
                    bone_weights=bone_weights,
                    bone_status=bone_status,
                )
            )

    def set_visible_condition(self, mesh_index: int, condition: int) -> None:
        if not 0 <= condition <= 0xFFFF:
            raise ValueError("Group ID 必须在 0–65535 之间")
        mesh = self.meshes[mesh_index]
        struct.pack_into("<H", self._data, mesh.visible_condition_offset, condition)
        mesh.visible_condition = condition

    @property
    def equipment_part(self) -> str:
        parts = self.name.lower().replace("\\", "/").split("/")
        for part in ("helm", "body", "arm", "wst", "leg"):
            if part in parts or f"_{part}" in parts[-1]:
                return part
        return "other"

    @property
    def outfit_id(self) -> str:
        parts = self.name.split("/")
        for i, part in enumerate(parts[:-1]):
            if part.lower() in ("f_equip", "m_equip"):
                return "/".join(parts[:i + 2])
        match = re.match(r"([fm])_(?:helm|body|arm|wst|leg)(\d+_\d+)", parts[-1], re.I)
        if match:
            return f"{match[1].lower()}_equip/pl{match[2]}"
        # Keep parallel optional folders distinct when equipment IDs are absent.
        for i, part in enumerate(parts[:-1]):
            if part.lower() in ("helm", "body", "arm", "wst", "leg"):
                return "/".join(parts[:i]) or "未识别套装"
        return str(Path(self.name).parent) if len(parts) > 1 else "未识别套装"

    @property
    def outfit_label(self) -> str:
        identity = self.outfit_id
        marker = "nativePC/"
        if marker.lower() in identity.lower():
            at = identity.lower().index(marker.lower())
            prefix = identity[:at].strip("/")
            suffix = identity[at + len(marker):]
            if suffix.lower().startswith("pl/"):
                suffix = suffix[3:]
            return f"{prefix} / {suffix}" if prefix else suffix
        return identity

    def original_group(self, mesh_index: int) -> int:
        if hasattr(self, "_origin_groups"):
            return self._origin_groups[mesh_index]
        return struct.unpack_from("<H", self._original, self.meshes[mesh_index].visible_condition_offset)[0]

    def material_info(self, material_id: int) -> MaterialInfo:
        name = self.material_names[material_id] if 0 <= material_id < len(self.material_names) else ""
        return self.materials.get(material_id, MaterialInfo(name or "未知"))

    def attach_materials(self, data: bytes) -> None:
        bindings = read_mrl3_bindings(data)
        self.materials = {}
        for i, name in enumerate(self.material_names):
            hashed = material_hash(name)
            binding = bindings.get(hashed)
            self.materials[i] = MaterialInfo(name or "未知", hashed, binding.textures if binding else (),
                                             "已匹配 MRL3" if binding else "MRL3 中未匹配此材质",
                                             binding.color_texture if binding else None)

    def bytes(self) -> bytes:
        return bytes(self._data)

    def reset(self) -> None:
        self._data[:] = self._original
        for mesh in self.meshes:
            mesh.visible_condition = struct.unpack_from(
                "<H", self._data, mesh.visible_condition_offset
            )[0]


class ModWorkspace:
    def __init__(self):
        self.source: Path | None = None
        self.kind = ""
        self.documents: list[Mod3Document] = []
        self.errors: list[str] = []
        self._exported_state: list[bytes] | None = None
        self.editor_data = None
        self._rar_cache = None

    @property
    def archive_path(self):
        return Path(self._rar_cache.name) / "input.zip" if self._rar_cache else self.source

    def __del__(self):
        cache = getattr(self, "_rar_cache", None)
        if cache is not None:
            try:
                cache.cleanup()
            except Exception:
                pass  # Interpreter shutdown or a delayed Windows handle release.

    @classmethod
    def open(cls, path: str | Path) -> "ModWorkspace":
        workspace = cls()
        workspace.source = Path(path)
        if workspace.source.is_dir():
            workspace.kind = "folder"
            files = sorted(workspace.source.rglob("*.mod3"))
            for file in files:
                workspace._append(file.relative_to(workspace.source).as_posix(), file.read_bytes())
        elif zipfile.is_zipfile(workspace.source) or workspace.source.suffix.lower() in (".rar", ".7z"):
            workspace.kind = "zip"
            if not zipfile.is_zipfile(workspace.source):
                from archive_support import rar_to_zip, sevenzip_to_zip
                with workspace.source.open("rb") as signature:
                    magic = signature.read(8)
                workspace.kind = "7z" if magic.startswith(b"7z\xbc\xaf\x27\x1c") else "rar" if magic.startswith(b"Rar!\x1a\x07") else workspace.source.suffix.lower()[1:]
                workspace._rar_cache = tempfile.TemporaryDirectory(prefix=f"mhw-{workspace.kind}-", ignore_cleanup_errors=True)
                try:
                    converter = rar_to_zip if workspace.kind == "rar" else sevenzip_to_zip
                    converter(workspace.source, workspace.archive_path)
                except Exception:
                    workspace._rar_cache.cleanup()
                    raise
            with zipfile.ZipFile(workspace.archive_path) as archive:
                for info in archive.infolist():
                    if not info.is_dir() and info.filename.lower().endswith(".mod3"):
                        workspace._append(info.filename, archive.read(info))
        elif workspace.source.suffix.lower() == ".mod3":
            workspace.kind = "mod3"
            workspace._append(workspace.source.name, workspace.source.read_bytes())
        else:
            raise Mod3Error("请选择 MOD3、包含 MOD3 的 ZIP / RAR / 7Z，或模组文件夹")
        if not workspace.documents:
            details = "\n".join(workspace.errors[:5])
            raise Mod3Error("没有找到可读取的 MOD3 文件" + (f"\n{details}" if details else ""))
        workspace._load_materials()
        workspace._load_editor_data()
        return workspace

    def _load_editor_data(self):
        try:
            if self.kind in ("zip", "rar", "7z"):
                with zipfile.ZipFile(self.archive_path) as archive:
                    if EDITOR_ENTRY not in archive.namelist():
                        return
                    if archive.getinfo(EDITOR_ENTRY).file_size > 8 * 1024 * 1024:
                        raise ValueError("编辑信息过大")
                    payload = archive.read(EDITOR_ENTRY)
            else:
                path = (Path(str(self.source) + ".mhwlayer.json") if self.kind == "mod3"
                        else self.source / EDITOR_ENTRY)
                if not path.exists():
                    return
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("编辑信息过大")
                payload = path.read_bytes()
            self.editor_data = json.loads(payload.decode("utf-8"))
        except (ValueError, OSError, KeyError) as exc:
            self.errors.append(f"编辑信息无法读取：{exc}；仍可编辑模型")

    def _load_materials(self) -> None:
        with (zipfile.ZipFile(self.archive_path) if self.kind in ("zip", "rar", "7z") else nullcontext()) as archive:
            names = {name.replace("\\", "/").lower(): name for name in archive.namelist()} if archive else {}
            for document in self.documents:
                companion = str(Path(document.name).with_suffix(".mrl3")).replace("\\", "/")
                try:
                    if archive:
                        name = names.get(companion.lower())
                        if not name:
                            continue
                        data = archive.read(name)
                    else:
                        path = self.source.with_suffix(".mrl3") if self.kind == "mod3" else self.source / companion
                        if not path.exists():
                            continue
                        data = path.read_bytes()
                    document.attach_materials(data)
                except (ValueError, OSError, KeyError, struct.error) as exc:
                    self.errors.append(f"{companion}: 材质信息不可读 ({exc})，模型仍可编辑")

    def _append(self, name: str, data: bytes) -> None:
        try:
            self.documents.append(Mod3Document(name, data))
        except Exception as exc:
            self.errors.append(f"{name}: {exc}")

    def all_meshes(self) -> Iterable[tuple[Mod3Document, MeshPart]]:
        for document in self.documents:
            for mesh in document.meshes:
                yield document, mesh

    @property
    def mesh_count(self) -> int:
        return sum(len(document.meshes) for document in self.documents)

    @property
    def outfits(self) -> dict[str, list[Mod3Document]]:
        result = {}
        for document in self.documents:
            result.setdefault(document.outfit_id, []).append(document)
        return result

    @property
    def changed(self) -> bool:
        return any(document.changed for document in self.documents)

    @property
    def has_unexported_changes(self) -> bool:
        if self._exported_state is None:
            return self.changed
        return [document.bytes() for document in self.documents] != self._exported_state

    def _overwrite_names(self, destination, editor_data=True):
        destination = Path(destination)
        if self.kind != "folder":
            names = [destination.name]
            if self.kind == "mod3" and editor_data:
                names.append(destination.name + ".mhwlayer.json")
            return destination.parent, names
        names = [document.name for document in self.documents if document.changed]
        if destination.resolve() != self.source.resolve():
            names = [p.relative_to(self.source).as_posix() for p in self.source.rglob("*") if p.is_file()]
        names.extend(name for name in getattr(self, 'export_files', {}) if name not in names)
        if editor_data and EDITOR_ENTRY not in names:
            names.append(EDITOR_ENTRY)
        return destination, names

    def overwrite_state(self, destination, editor_data=True):
        from safe_export import target_state
        root, names = self._overwrite_names(destination, editor_data)
        return target_state(root, names)

    def export(self, destination: str | Path, editor_data=None, *, overwrite=False, expected_state=None) -> Path:
        if self.source is None:
            raise Mod3Error("尚未打开模组")
        destination = Path(destination)
        if self.kind in ("rar", "7z") and destination.suffix.lower() != ".zip":
            raise Mod3Error("RAR / 7Z 输入请另存为 .zip 模组；原始压缩包保留不变")
        source_path, target_path = self.source.resolve(), destination.resolve()
        same_source = source_path == target_path
        if destination.exists() and not same_source and os.path.samefile(self.source, destination):
            raise Mod3Error("目标与原模组是同一文件的不同链接，请选择原路径或新的普通文件")
        if same_source and not overwrite:
            raise Mod3Error("请保存为副本，不能覆盖原始模组")
        if self.kind == "folder" and not same_source and (
            source_path in target_path.parents or target_path in source_path.parents
        ):
            raise Mod3Error("导出文件夹不能位于原始模组内，也不能包含原始模组")
        if destination.exists() and not overwrite:
            raise Mod3Error("目标已存在，请选择新的文件或文件夹名称")
        sidecar = Path(str(destination) + ".mhwlayer.json")
        if self.kind == "mod3" and editor_data is not None and sidecar.exists() and not overwrite:
            raise Mod3Error("目标编辑信息已存在，请选择新的文件名称")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            from safe_export import check_path
            check_path(destination, destination.parent)
            if self.kind == "folder" and destination.exists() and not destination.is_dir():
                raise Mod3Error("覆盖目标必须是模组文件夹")
            if self.kind != "folder" and destination.exists() and not destination.is_file():
                raise Mod3Error("覆盖目标必须是模组文件")
            if same_source:
                with (zipfile.ZipFile(self.archive_path) if self.kind == "zip" else nullcontext()) as original:
                    for document in self.documents:
                        data = original.read(document.name) if original else (self.source / document.name if self.kind == "folder" else self.source).read_bytes()
                        if data != document._original:
                            raise Mod3Error("原模型在打开后已被其他程序修改，请重新打开文件夹或模组再编辑；未覆盖")
        # Stage output beside its destination so a failed export never leaves a partial mod.
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".mhw-export-") as temp:
            staged = Path(temp) / destination.name
            if overwrite and self.kind == "folder" and same_source:
                staged.mkdir()
                for document in self.documents:
                    target = staged / document.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(document.bytes())
                for name, data in getattr(self, 'export_files', {}).items():
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                if editor_data is not None:
                    target = staged / EDITOR_ENTRY
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(editor_data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                self._export_to(staged, editor_data)
            reopened = ModWorkspace.open(staged)
            expected = {document.name: document.bytes() for document in self.documents}
            actual = {document.name: document.bytes() for document in reopened.documents}
            if self.kind == "mod3":
                verified = reopened.documents[0].bytes() == self.documents[0].bytes()
            else:
                verified = actual == expected
            if not verified:
                raise Mod3Error("导出重读验证失败，未生成目标文件")
            if editor_data is not None and reopened.editor_data != editor_data:
                raise Mod3Error("编辑信息重读验证失败，未生成目标文件")
            for name, data in getattr(self, 'export_files', {}).items():
                if self.kind == 'folder':
                    actual_file = (staged / name).read_bytes()
                else:
                    with zipfile.ZipFile(staged) as z:
                        actual_file = z.read(name)
                if actual_file != data:
                    raise Mod3Error('战损配置重读验证失败，未生成目标文件')
            if overwrite:
                from safe_export import replace_with_backup
                root, names = self._overwrite_names(destination, editor_data is not None)
                self.last_backup = replace_with_backup(staged if self.kind == "folder" else staged.parent,
                                                      root, names, expected_state, destination.name[:40],
                                                      destination.parent / ".mhw-layer-studio-backups")
            else:
                staged.rename(destination)
            if not overwrite and self.kind == "mod3" and editor_data is not None:
                try:
                    Path(str(staged) + ".mhwlayer.json").rename(sidecar)
                except OSError:
                    destination.unlink()  # Only the newly created output belongs to this operation.
                    raise
        self._exported_state = [document.bytes() for document in self.documents]
        return destination

    def _export_to(self, destination: Path, editor_data=None) -> None:
        payload_json = json.dumps(editor_data, ensure_ascii=False, indent=2).encode("utf-8") if editor_data is not None else None
        if self.kind in ("zip", "rar", "7z"):
            replacements = {document.name: document.bytes() for document in self.documents}
            extra = getattr(self, 'export_files', {})
            with zipfile.ZipFile(self.archive_path, "r") as source, zipfile.ZipFile(
                destination, "w", allowZip64=True
            ) as target:
                target.comment = source.comment
                for info in source.infolist():
                    if info.filename in extra:
                        continue
                    if payload_json is not None and info.filename == EDITOR_ENTRY:
                        continue
                    payload = replacements.get(info.filename)
                    if payload is None:
                        payload = source.read(info)
                    target.writestr(info, payload)
                if payload_json is not None:
                    target.writestr(EDITOR_ENTRY, payload_json, compress_type=zipfile.ZIP_DEFLATED)
                for name, data in extra.items():
                    target.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        elif self.kind == "mod3":
            destination.write_bytes(self.documents[0].bytes())
            if payload_json is not None:
                Path(str(destination) + ".mhwlayer.json").write_bytes(payload_json)
        elif self.kind == "folder":
            if any(path.is_symlink() or (getattr(path.lstat(), "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
                   for path in self.source.rglob("*")):
                raise Mod3Error("模组文件夹含链接，请先复制为普通文件夹再导出")
            shutil.copytree(self.source, destination)
            for document in self.documents:
                target = destination / document.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(document.bytes())
            for name, data in getattr(self, 'export_files', {}).items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if payload_json is not None:
                target = destination / EDITOR_ENTRY
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload_json)


def generate_health_rules(
    decoration_id: int = 60,
    armor_id: int = 61,
    inner_id: int = 62,
    first_rule: int = 100,
    thresholds=None,
) -> str:
    thresholds = validate_thresholds(thresholds)
    mapping = {60: decoration_id, 61: armor_id, 62: inner_id}
    layers = [(mapping.get(group, group), thresholds[group], layer_name(group, thresholds))
              for group in sorted(thresholds, key=lambda g: (-thresholds[g], g))]
    if len({group for group, _, _ in layers}) != len(layers):
        raise ValueError("导出规则的 Group ID 发生冲突")
    lines = [
        "; MHW Layer Studio 生成的血量分层规则",
        "; 首次使用添加到 MHWI-VisualControllerExtended.ini；更新时替换旧分层 Rule 段落。",
        "; 不使用 LatchUntilReset，因此回血后模型会恢复。",
        "",
    ]
    rule = first_rule
    for group_id, threshold, label in layers:
        lines.extend(
            [
                f"; {label}: 血量高于 {threshold}% 时显示",
                f"[Rule{rule}]",
                f"Id={group_id}",
                f"HealthPercentAbove={threshold}",
                "Return=1",
                "",
                f"[Rule{rule + 1}]",
                f"Id={group_id}",
                "Return=0",
                "",
            ]
        )
        rule += 2
    return "\n".join(lines)
