"""Reversible mesh edits and portable, source-checked layer projects."""
from __future__ import annotations

import hashlib
import copy
import json
import os
import tempfile
import secrets
from pathlib import Path

from mod3 import Mod3Error, ModWorkspace
from splitting import components, rebuild_split, rebuild_partitions
from health_rules import validate_thresholds, restore_thresholds
from damage_model import settings as damage_settings, new_settings as new_damage_settings

PROJECT_FORMAT = "mhw-layer-project"


class EditorSession:
    def __init__(self, workspace: ModWorkspace):
        self.workspace = workspace
        self._undo: list[list[tuple[str, int, int, int]]] = []
        self._redo: list[list[tuple[str, int, int, int]]] = []
        self.project_path: Path | None = None
        self.named_groups: dict[str, set[tuple[str, int]]] = {}
        self.parent_groups: dict[str, list[str]] = {}
        self.annotations: dict[tuple[str, int], dict[str, str]] = {}
        self.health_thresholds = validate_thresholds()
        self.damage_model = damage_settings()
        self.embedded_view = {}
        self._source_metadata = {}
        if workspace.editor_data is not None:
            try:
                self._import_metadata(workspace.editor_data)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                workspace.errors.append(f"编辑信息与模型不匹配，未恢复分组和备注：{exc}")
        self._saved_groups = self._groups()

    @staticmethod
    def _document_metadata(document, record):
        groups, labels = record.get("original_groups"), record.get("mesh_labels")
        if (not isinstance(groups, list) or len(groups) != len(document.meshes)
                or any(type(g) is not int or not 0 <= g <= 65535 for g in groups)
                or not isinstance(labels, list) or len(labels) != len(groups)
                or any(not isinstance(label, str) or len(label) > 200 for label in labels)):
            raise Mod3Error("原始层级或拆分标签无效")
        return {"original_groups": groups.copy(), "mesh_labels": labels.copy()}

    def _import_metadata(self, data):
        if not isinstance(data, dict) or data.get("format") != "mhw-layer-metadata" or data.get("version") not in (1, 2, 3, 4, 5):
            raise Mod3Error("不支持的编辑信息格式")
        thresholds = restore_thresholds(data.get("health_thresholds"), legacy=data["version"] < 3)
        records = data["documents"]
        if not isinstance(records, list) or len(records) != len(self.workspace.documents):
            raise Mod3Error("模型清单不同")
        lookup = {record["name"]: record for record in records}
        if set(lookup) != {d.name for d in self.workspace.documents}:
            raise Mod3Error("模型文件不同")
        metadata = {}
        manual_groups = {}
        damage = damage_settings(data.get("damage_model"))
        for document in self.workspace.documents:
            record = lookup[document.name]
            if record["sha256"] != hashlib.sha256(document.bytes()).hexdigest():
                raise Mod3Error("模型已被其他工具修改")
            metadata[document.name] = self._document_metadata(document, record)
            if damage['enabled']:
                groups = record.get('manual_groups')
                if (not isinstance(groups, list) or len(groups) != len(document.meshes) or
                        any(type(g) is not int or not 0 <= g <= 65535 for g in groups)):
                    raise Mod3Error('双模式手动层级记录无效')
                manual_groups[document.name] = groups
        self._restore_tags(data)
        self.health_thresholds = thresholds
        self.damage_model = damage
        for document in self.workspace.documents:
            document._origin_groups = metadata[document.name]["original_groups"].copy()
            document._mesh_labels = metadata[document.name]["mesh_labels"].copy()
            for index, group in enumerate(manual_groups.get(document.name, [])):
                document.set_visible_condition(index, group)
        self._source_metadata = metadata
        self.embedded_view = data.get("view", {})

    def export(self, path, view=None, *, overwrite=False, expected_state=None):
        self.validate_patch()
        output_workspace = self.workspace
        if self.damage_model['enabled']:
            from damage_model import export_plan
            output_workspace = export_plan(self)
        exported = {d.name: d for d in output_workspace.documents}
        single = self.workspace.kind == "mod3"
        names = {d.name: Path(path).name if single else d.name for d in self.workspace.documents}
        # Exported geometry is already split; metadata never replays split recipes.
        payload = {"format": "mhw-layer-metadata", "version": 5,
                   "health_thresholds": self.health_thresholds,
                   "damage_model": self.damage_model.copy(),
                   "documents": [{"name": names[d.name], "sha256": hashlib.sha256(exported[d.name].bytes()).hexdigest(),
                                  "manual_groups": [m.visible_condition for m in d.meshes],
                                  "original_groups": [d.original_group(m.index) for m in d.meshes],
                                  "mesh_labels": list(getattr(d, "_mesh_labels", [""] * len(d.meshes)))}
                                 for d in self.workspace.documents],
                   "named_groups": [{"name": name, "members": [[names[n], i] for n, i in sorted(keys)]}
                                    for name, keys in self.named_groups.items()],
                   "parent_groups": self._parents(),
                   "annotations": [{"document": names[n], "mesh": i, **value}
                                   for (n, i), value in sorted(self.annotations.items())],
                   "view": view or {}}
        if single:
            payload["view"] = json.loads(json.dumps(payload["view"]))
            for field in ("selected", "hidden"):
                payload["view"][field] = [[names.get(n, n), i] for n, i in payload["view"].get(field, [])]
            payload["view"]["texture_overrides"] = [[names.get(n, n), m, p] for n, m, p in payload["view"].get("texture_overrides", [])]
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        result = output_workspace.export(path, payload, overwrite=overwrite, expected_state=expected_state)
        self.workspace._exported_state = [d.bytes() for d in self.workspace.documents]
        if hasattr(output_workspace, 'last_backup'):
            self.workspace.last_backup = output_workspace.last_backup
        self._saved_groups = self._groups()
        if overwrite and Path(path).resolve() == self.workspace.source.resolve():
            # The old baseline no longer matches the source. Reload exported
            # metadata so later projects/splits use the newly saved geometry.
            restored = type(self)(ModWorkspace.open(path))
            self.__dict__.update(restored.__dict__)
        return result

    def _groups(self):
        return (tuple((document.name, mesh.index, mesh.visible_condition)
                      for document, mesh in self.workspace.all_meshes()),
                tuple((name, tuple(sorted(keys))) for name, keys in sorted(self.named_groups.items())),
                tuple((key, value.get("name", ""), value.get("note", ""), value.get('damage_role', ''), value.get('visibility', '')) for key, value in sorted(self.annotations.items())),
                tuple(sorted(self.health_thresholds.items())),
                tuple((name, tuple(children)) for name, children in sorted(self.parent_groups.items())),
                tuple(sorted(self.damage_model.items())))

    def set_damage_mode(self, mode):
        after = new_damage_settings(mode, self.damage_model)
        if after == self.damage_model:
            return False
        self._undo.append({'kind': 'damage_model', 'before': self.damage_model.copy(), 'after': after.copy()})
        self._redo.clear()
        self.damage_model = after
        return True

    def overwrite_state(self, path):
        from damage_model import export_plan
        workspace = export_plan(self) if self.damage_model['enabled'] else self.workspace
        return workspace.overwrite_state(path)

    def set_health_thresholds(self, values):
        after = validate_thresholds(values)
        if after == self.health_thresholds:
            return False
        self._undo.append({"kind": "thresholds", "before": self.health_thresholds.copy(), "after": after.copy()})
        self._redo.clear()
        self.health_thresholds = after
        return True

    def _annotation_snapshot(self):
        return {key: value.copy() for key, value in self.annotations.items()}

    def set_always_hidden(self, keys):
        keys = self._check_keys(keys)
        if not keys:
            raise Mod3Error('请先选择部件')
        if self.workspace.kind == 'mod3':
            raise Mod3Error('始终隐藏需要随模型导出插件清单，请打开完整模组文件夹或压缩包。')
        before = self._annotation_snapshot()
        damage_before = self.damage_model.copy()
        count = self.edit_annotations(keys, visibility='hidden')
        if not count:
            return 0
        self.damage_model = damage_settings({'enabled': True, 'mode': self.damage_model['mode'],
                                            'token': self.damage_model['token'] or secrets.token_hex(8)})
        self._undo[-1] = {'kind': 'visibility', 'before': before, 'after': self._annotation_snapshot(),
                          'damage_before': damage_before, 'damage_after': self.damage_model.copy(), 'groups': []}
        return count

    @staticmethod
    def _validate_annotation(field, value):
        if field == 'visibility':
            if value not in ('', 'hidden'):
                raise Mod3Error('始终隐藏标记无效')
            return value
        if field == 'damage_role':
            if value not in ('', 'base', 'random'):
                raise Mod3Error('随机战损标记无效')
            return value
        if field not in ("name", "note") or not isinstance(value, str):
            raise Mod3Error("名称或备注格式无效")
        maximum = 80 if field == "name" else 2000
        if len(value) > maximum or any(ord(c) < 32 and (field == "name" or c not in "\n\t") for c in value):
            raise Mod3Error(f"{'名称' if field == 'name' else '备注'}最多 {maximum} 个字符，不支持异常控制字符")
        return value.strip()

    def edit_annotations(self, keys, **fields):
        keys = self._check_keys(keys)
        if not keys:
            raise Mod3Error("请先选择 Mesh")
        if not fields:
            return 0
        fields = {field: self._validate_annotation(field, value) for field, value in fields.items()}
        before = self._annotation_snapshot()
        after = self._annotation_snapshot()
        count = 0
        for key in keys:
            old = after.get(key, {"name": "", "note": ""})
            value = {**old, **fields}
            if value != old:
                count += 1
            if any(value.values()):
                after[key] = value
            else:
                after.pop(key, None)
        if count:
            self.annotations = after
            self._undo.append({"kind": "annotations", "before": before, "after": self._annotation_snapshot()})
            self._redo.clear()
        return count

    def _collections(self):
        return {name: keys.copy() for name, keys in self.named_groups.items()}

    def _parents(self):
        return {name: list(children) for name, children in self.parent_groups.items()}

    def group_members(self, name):
        if name in self.parent_groups:
            return set().union(*(self.named_groups[child] for child in self.parent_groups[name]))
        return self.named_groups.get(name, set()).copy()

    def all_groups(self):
        return {**self._collections(), **{name: self.group_members(name) for name in self.parent_groups}}

    @staticmethod
    def _validate_parents(parents, groups):
        if not isinstance(parents, dict) or len(parents) > 10000:
            raise Mod3Error("父组清单无效")
        seen, result = set(), {}
        for name, children in parents.items():
            if (not isinstance(name, str) or not name.strip() or len(name) > 80
                    or any(ord(c) < 32 for c in name) or name in groups
                    or not isinstance(children, list) or not children
                    or any(not isinstance(child, str) or child not in groups for child in children)
                    or len(set(children)) != len(children) or seen.intersection(children)):
                raise Mod3Error("父组名称或子组关系无效；子组只能属于一个父组")
            seen.update(children)
            result[name] = children.copy()
        return result

    def _prune_parents(self):
        self.parent_groups = {name: [child for child in children if child in self.named_groups]
                              for name, children in self.parent_groups.items() if name not in self.named_groups}
        self.parent_groups = {name: children for name, children in self.parent_groups.items() if children}

    def _record_collections(self, before, parents_before):
        self._prune_parents()
        self._undo.append({"kind": "collections", "before": before, "after": self._collections(),
                           "parents_before": parents_before, "parents_after": self._parents()})
        self._redo.clear()

    def edit_parent_group(self, action, name, children=(), new_name=None):
        before, parents_before = self._collections(), self._parents()
        after = self._parents()
        if action in ("create", "rename"):
            target = (new_name if action == "rename" else name).strip()
            if (not target or len(target) > 80 or any(ord(c) < 32 for c in target)
                    or target in self.named_groups or (target in after and target != name)):
                raise Mod3Error("父组名请输入不重复的 1–80 个可显示字符")
        if action == "create":
            children = list(dict.fromkeys(children))
            if name in after or len(children) < 2:
                raise Mod3Error("请至少选择两个独立子组，并使用新父组名称")
            used = {child for values in after.values() for child in values}
            if any(child not in self.named_groups or child in used for child in children):
                raise Mod3Error("请选择尚未加入父组的独立子组")
            after[target] = children
        elif name not in after:
            raise Mod3Error("请先选择父组")
        elif action == "rename":
            after[target] = after.pop(name)
        elif action == "delete":
            del after[name]  # Dissolving a parent never dissolves its children.
        else:
            raise Mod3Error("请选择具体子组调整成员；父组用于统一选择和分配")
        after = self._validate_parents(after, self.named_groups)
        if after == parents_before:
            return False
        self.parent_groups = after
        self._record_collections(before, parents_before)
        return True

    def _check_keys(self, keys):
        available = {(d.name, m.index) for d, m in self.workspace.all_meshes()}
        keys = set(keys)
        if not keys <= available:
            raise Mod3Error("部件组含无效 Mesh")
        return keys

    def edit_named_group(self, action, name, keys=(), new_name=None):
        if name in self.parent_groups and action != "create":
            return self.edit_parent_group(action, name, new_name=new_name)
        before = self._collections()
        parents_before = self._parents()
        after = self._collections()
        if action in ("create", "rename"):
            target = (new_name if action == "rename" else name).strip()
            if not target or len(target) > 80 or any(ord(c) < 32 for c in target):
                raise Mod3Error("组名请输入 1–80 个可显示字符")
            if target in self.parent_groups or (target in after and not (action == "rename" and target == name)):
                raise Mod3Error("组名已存在，请换一个名称")
        if action != "create" and name not in after:
            raise Mod3Error("请先选择一个部件组")
        if action in ("create", "add", "remove"):
            keys = self._check_keys(keys)
            if not keys:
                raise Mod3Error("请先选择需要操作的 Mesh")
        if action in ("create", "add"):
            for members in after.values():
                members.difference_update(keys)
            target = target if action == "create" else name
            after.setdefault(target, set()).update(keys)
        elif action == "remove":
            after[name].difference_update(keys)
        elif action == "rename":
            after[target] = after.pop(name)
        elif action == "delete":
            del after[name]
        else:
            raise Mod3Error("不支持的部件组操作")
        if before == after:
            return False
        self.named_groups = after
        if action == "rename":
            self.parent_groups = {p: [target if child == name else child for child in children]
                                  for p, children in self.parent_groups.items()}
        self._record_collections(before, parents_before)
        return True

    def plan_material_groups(self, keys, include_grouped=False):
        """Read-only proposal; material IDs are local to each MOD3, never global."""
        keys = self._check_keys(keys)
        grouped = set().union(*self.named_groups.values()) if self.named_groups else set()
        eligible = keys if include_grouped else keys - grouped
        removed = {name for name, members in self.named_groups.items() if members and members <= eligible}
        occupied = set(self.all_groups()) - removed
        buckets = {}
        for document in self.workspace.documents:
            for mesh in document.meshes:
                key = (document.name, mesh.index)
                if key in eligible:
                    buckets.setdefault((document.name, mesh.material_id), []).append(key)
        proposals = []
        for (document_name, material_id), members in buckets.items():
            document = self.document(document_name)
            material = document.material_info(material_id).name
            label = f"{document.equipment_part} · {material_id} {material} · {document.outfit_label}"
            label = "".join(c if ord(c) >= 32 else " " for c in label).strip()[:80]
            name, suffix = label, 2
            while name in occupied:
                ending = f" ({suffix})"
                name = label[:80 - len(ending)] + ending
                suffix += 1
            occupied.add(name)
            proposals.append({"name": name, "document": document_name, "material_id": material_id,
                              "material": material, "members": members,
                              "group_ids": sorted({document.meshes[i].visible_condition for _, i in members})})
        return {"groups": proposals, "skipped": len(keys - eligible), "moved": len(eligible & grouped),
                "removed": sorted(removed)}

    def apply_material_groups(self, keys, include_grouped=False):
        plan = self.plan_material_groups(keys, include_grouped)
        if not plan["groups"]:
            return plan
        before, after = self._collections(), self._collections()
        parents_before = self._parents()
        affected = {key for group in plan["groups"] for key in group["members"]}
        for members in after.values():
            members.difference_update(affected)
        for name in plan["removed"]:
            del after[name]
        for group in plan["groups"]:
            after[group["name"]] = set(group["members"])
        if before == after:
            return plan
        self.named_groups = after
        self._record_collections(before, parents_before)
        return plan

    @property
    def project_dirty(self):
        return self._groups() != self._saved_groups

    def prepare_spatial_groups(self, keys, *, split=True, pair=True, distance="保守", include_grouped=False, associate_lod=True, use_features=True, cancelled=lambda: False):
        """Stage geometry and tags without changing this session or its history."""
        from spatial_groups import suggest_groups
        from lod_groups import suggest_lod_groups, alternative, lod_label
        self.validate_patch()
        keys = self._check_keys(keys)
        before = {d.name: self._snapshot(d) for d in self.workspace.documents}
        tags = self._groups()
        grouped = set().union(*self.named_groups.values()) if self.named_groups else set()
        eligible = keys if include_grouped else keys - grouped
        skipped = len(keys - eligible)
        lod_groups, warnings = [], []
        if associate_lod:
            files = {name for name, index in eligible}
            candidates = {(d.name, m.index) for d, m in self.workspace.all_meshes() if d.name in files}
            for group in suggest_lod_groups(self.workspace, candidates, cancelled=cancelled):
                members = set(group["members"])
                if not members & eligible:
                    continue
                if not include_grouped and members & grouped:
                    warnings.append(f"{Path(group['document']).name}：部分 LOD 已在手工组中，未关联；可勾选重新整理已有组")
                    continue
                lod_groups.append(group)
                eligible |= members
        lod_added = len(eligible - keys)
        staged = self._staged_copy()
        workspace = staged.workspace
        origins = {(d.name, m.index): (d.name, m.index) for d, m in workspace.all_meshes()}
        split_count = 0
        if split:
            for name, index in sorted(eligible, reverse=True):
                if cancelled():
                    raise Mod3Error("已取消分析")
                document = staged.document(name)
                mesh = document.meshes[index]
                if associate_lod and alternative(mesh):
                    continue  # Preserve LOD correspondence, including merged coarse versions.
                if not len(mesh.faces) or len(components(mesh)) < 2:
                    continue
                try:
                    count = staged.split_mesh(name, index)
                except Mod3Error as exc:
                    warnings.append(f"{Path(name).name} / Mesh {index}：{exc}，保持整体")
                    continue
                def remap(key):
                    n, i = key
                    if n != name or i < index:
                        return [key]
                    return [(n, j) for j in range(index, index + count)] if i == index else [(n, i + count - 1)]
                eligible = {child for key in eligible for child in remap(key)}
                origins = {child: parent for key, parent in origins.items() for child in remap(key)}
                split_count += 1
        if associate_lod:
            for group in lod_groups:
                parents = set(group["members"])
                group["members"] = sorted(key for key, original in origins.items() if original in parents)
            covered = {key for group in lod_groups for key in group["members"]}
            remaining = eligible - covered
            spatial = {key for key in remaining if not alternative(staged.document(key[0]).meshes[key[1]])}
            groups = lod_groups + suggest_groups(workspace, spatial, distance=distance, pair=pair, use_features=use_features, cancelled=cancelled)
            for name, index in sorted(remaining - spatial):
                document = staged.document(name)
                mesh = document.meshes[index]
                groups.append({"name": f"{document.equipment_part} · LOD待确认 · Mesh {index}",
                               "document": name, "material_id": mesh.material_id,
                               "material": document.material_info(mesh.material_id).name,
                               "group_ids": [mesh.visible_condition], "members": [(name, index)],
                               "reason": f"{lod_label(mesh)}；未找到唯一对应版本，保持整体，请手工核对",
                               "review": True, "accepted": False})
        else:
            groups = suggest_groups(workspace, eligible, distance=distance, pair=pair, use_features=use_features, cancelled=cancelled)
        occupied = set(self.all_groups())
        for group in groups:
            label = group["name"][:80]
            if label in staged.named_groups and staged.named_groups[label] == set(group["members"]):
                occupied.discard(label)
            name, suffix = label, 2
            while name in occupied:
                ending = f" ({suffix})"
                name = label[:80 - len(ending)] + ending
                suffix += 1
            group["name"] = name
            occupied.add(name)
        parent_names = {}
        for label in {g.get("parent") for g in groups if g.get("parent")}:
            proposed_children = [g for g in groups if g.get("parent") == label]
            if (label in staged.parent_groups
                    and set(staged.parent_groups[label]) == {g["name"] for g in proposed_children}
                    and all(staged.named_groups.get(g["name"]) == set(g["members"]) for g in proposed_children)):
                occupied.discard(label)
        for group in groups:
            label = group.get("parent")
            if label is None:
                continue
            if label not in parent_names:
                unique, suffix = label[:80], 2
                while unique in occupied:
                    ending = f" ({suffix})"
                    unique = label[:80 - len(ending)] + ending
                    suffix += 1
                parent_names[label] = unique
                occupied.add(unique)
            group["parent"] = parent_names[label]
        return {"staged": staged, "groups": groups, "before": before, "tags": tags,
                "origins": origins, "split_count": split_count, "warnings": warnings,
                "skipped": skipped,
                "lod_families": len(lod_groups), "lod_added": lod_added,
                "use_features": use_features,
                "eligible": eligible, "keys": keys}

    def prepare_attachment_review(self, keys, *, use_features=True, cancelled=lambda: False):
        """Stage existing parts and groups intact; no automatic split or regroup."""
        if cancelled():
            raise Mod3Error("已取消分析")
        self.validate_patch()
        keys = self._check_keys(keys)
        eligible = keys.copy()
        for members in self.named_groups.values():
            if members & keys:
                eligible.update(members)  # Preserve whole cross-file manual groups.
        staged = self._staged_copy()
        groups, occupied = [], set(self.all_groups())
        covered = set()
        for name, members in self.named_groups.items():
            if cancelled():
                raise Mod3Error("已取消分析")
            if members & eligible:
                covered.update(members)
                first = next(iter(members))
                mesh = staged.document(first[0]).meshes[first[1]]
                groups.append({"name": name, "document": first[0], "material_id": mesh.material_id,
                               "members": sorted(members), "reason": "保留现有组；确认附件后才更新", "review": True, "accepted": False})
        for name, index in sorted(eligible - covered):
            if cancelled():
                raise Mod3Error("已取消分析")
            document = staged.document(name)
            label = f"未成组 · {document.equipment_part} · Mesh {index}"
            unique, suffix = label, 2
            while unique in occupied:
                unique, suffix = f"{label} ({suffix})", suffix + 1
            occupied.add(unique)
            groups.append({"name": unique, "document": name, "material_id": document.meshes[index].material_id,
                           "members": [(name, index)], "reason": "已有独立部件；未重新拆分", "review": True, "accepted": False})
        return {"staged": staged, "groups": groups, "before": {d.name: self._snapshot(d) for d in self.workspace.documents},
                "tags": self._groups(), "origins": {(d.name, m.index): (d.name, m.index) for d, m in self.workspace.all_meshes()},
                "split_count": 0, "warnings": [], "skipped": 0, "lod_families": 0, "lod_added": 0,
                "use_features": use_features, "eligible": eligible, "keys": keys}

    def _staged_copy(self):
        workspace = ModWorkspace()
        workspace.source, workspace.kind = self.workspace.source, self.workspace.kind
        # No archive handles/temp-directory ownership are copied into the proposal.
        for document in self.workspace.documents:
            clone = copy.copy(document)
            clone._data = bytearray(document._data)
            clone.meshes = []
            for mesh in document.meshes:
                part = copy.copy(mesh)
                part.vertices, part.faces = mesh.vertices.copy(), mesh.faces.copy()
                part.uvs = None if mesh.uvs is None else mesh.uvs.copy()
                part.bone_ids = None if mesh.bone_ids is None else mesh.bone_ids.copy()
                part.bone_weights = None if mesh.bone_weights is None else mesh.bone_weights.copy()
                clone.meshes.append(part)
            workspace.documents.append(clone)
        staged = type(self)(workspace)
        staged.named_groups = self._collections()
        staged.parent_groups = self._parents()
        staged.annotations = self._annotation_snapshot()
        staged.health_thresholds = self.health_thresholds.copy()
        return staged

    def prepare_lod_sync(self, keys, *, include_grouped=False, use_features=True, cancelled=lambda: False):
        from lod_sync import prepare
        return prepare(self, keys, include_grouped=include_grouped, use_features=use_features, cancelled=cancelled)

    def apply_spatial_groups(self, plan):
        """Atomically apply the reviewed proposal, with one compound undo record."""
        if self._groups() != plan["tags"] or any(self._snapshot(d) != plan["before"].get(d.name) for d in self.workspace.documents):
            raise Mod3Error("模型或分组已改变，请重新生成建议")
        staged = plan["staged"]
        available = plan["eligible"]
        accepted = [g for g in plan["groups"] if g["accepted"]]
        if not accepted:
            raise Mod3Error("请勾选至少一个建议组")
        names, used = set(), set()
        for group in accepted:
            name = group["name"].strip()
            members = set(group["members"])
            if (not name or len(name) > 80 or any(ord(c) < 32 for c in name) or name in names
                    or not members or not members <= available or members & used):
                raise Mod3Error("建议组名称或成员重复 / 无效，请调整后再应用")
            # A name can replace only an existing group fully moved by this proposal.
            if name in staged.named_groups and staged.named_groups[name] - members:
                raise Mod3Error("建议组名称与已有组冲突")
            group["name"] = name
            names.add(name)
            used.update(members)
        if plan.get("sync_lod"):
            if used != available or any(not set(g["members"]) & plan["reference_keys"] for g in accepted):
                raise Mod3Error("请将所有待确认部件移入带高精度基准的组，并勾选完整控制组；未确认时不应用同步拆分")
        groups_before, annotations_before = self._collections(), self._annotation_snapshot()
        parents_before = self._parents()
        groups_after = staged._collections()
        for name in list(groups_after):
            old = groups_after[name]
            groups_after[name] = old - used
            if old and not groups_after[name]:
                del groups_after[name]
        for group in accepted:
            groups_after[group["name"]] = set(group["members"])
        parents_after = {name: [child for child in children if child in groups_after]
                         for name, children in staged.parent_groups.items()}
        parents_after = {name: children for name, children in parents_after.items() if children}
        proposed = {}
        for group in sorted(accepted, key=lambda g: (g.get("side", 0), g["name"])):
            if group.get("parent") is not None:
                if not isinstance(group["parent"], str):
                    raise Mod3Error("建议父组名称无效")
                proposed.setdefault(group["parent"], []).append(group["name"])
        for name, children in proposed.items():
            if len(children) >= 2:
                # Reorganizing reviewed leaves may replace their old parent.
                # Unreviewed leaves and their remaining parent links stay intact.
                parents_after = {parent: [child for child in old if child not in children]
                                 for parent, old in parents_after.items()}
                parents_after = {parent: old for parent, old in parents_after.items() if old}
                if name in parents_after:
                    raise Mod3Error("建议父组名称与已有父组冲突")
                parents_after[name] = children
        parents_after = self._validate_parents(parents_after, groups_after)
        after = {d.name: staged._snapshot(d) for d in staged.workspace.documents}
        changed = {name for name in after if after[name] != plan["before"][name]}
        if not changed and groups_before == groups_after and parents_before == parents_after:
            return 0
        try:
            for name in changed:
                self._restore_structure(name, after[name])
            self.named_groups = groups_after
            self.parent_groups = parents_after
            self.annotations = staged._annotation_snapshot()
            self.validate_patch()
        except Exception:
            for name in changed:
                self._restore_structure(name, plan["before"][name])
            self.named_groups, self.annotations = groups_before, annotations_before
            self.parent_groups = parents_before
            raise
        self._undo.append({"kind": "spatial", "before": {n: plan["before"][n] for n in changed},
                           "after": {n: after[n] for n in changed}, "groups_before": groups_before,
                           "groups_after": self._collections(), "annotations_before": annotations_before,
                           "annotations_after": self._annotation_snapshot()})
        self._undo[-1].update(parents_before=parents_before, parents_after=self._parents())
        self._redo.clear()
        return len(accepted)

    @property
    def named_groups_dirty(self):
        return self._groups()[1] != self._saved_groups[1] or self._groups()[4] != self._saved_groups[4]

    @property
    def project_metadata_dirty(self):
        return self._groups()[1:] != self._saved_groups[1:]

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def document(self, name):
        return next(document for document in self.workspace.documents if document.name == name)

    def apply_group(self, keys, group):
        if type(group) is not int or not 0 <= group <= 65535:
            raise ValueError("Group ID 必须在 0–65535 之间")
        keys = self._check_keys(keys)
        before = self._annotation_snapshot()
        edits = [(n, i, self.document(n).meshes[i].visible_condition, group) for n, i in keys
                 if self.document(n).meshes[i].visible_condition != group]
        cleared = {k for k in keys if self.annotations.get(k, {}).get('visibility') == 'hidden'}
        if not cleared:
            return self.apply_values([(name, index, group) for name, index in sorted(keys)])
        for key in cleared:
            self.annotations[key].pop('visibility')
            if not any(self.annotations[key].values()):
                self.annotations.pop(key)
        for n, i, _, new in edits:
            self.document(n).set_visible_condition(i, new)
        self._undo.append({'kind': 'visibility', 'before': before, 'after': self._annotation_snapshot(),
                           'damage_before': self.damage_model.copy(), 'damage_after': self.damage_model.copy(), 'groups': edits})
        self._redo.clear()
        return len(cleared | {(n, i) for n, i, _, _ in edits})

    def apply_values(self, values):
        edits = []
        for name, index, group in values:
            document = self.document(name)
            if type(index) is not int or not 0 <= index < len(document.meshes):
                raise ValueError("Mesh 索引无效")
            if type(group) is not int or not 0 <= group <= 65535:
                raise ValueError("Group ID 无效")
            old = document.meshes[index].visible_condition
            if old != group:
                edits.append((name, index, old, group))
        if not edits:
            return 0
        for name, index, _, group in edits:
            self.document(name).set_visible_condition(index, group)
        self._undo.append(edits)
        self._redo.clear()
        return len(edits)

    def undo(self):
        if not self._undo:
            return False
        edits = self._undo.pop()
        if isinstance(edits, dict):
            if edits.get('kind') == 'visibility':
                self.annotations = copy.deepcopy(edits['before'])
                self.damage_model = edits['damage_before'].copy()
                for n, i, old, _ in edits['groups']:
                    self.document(n).set_visible_condition(i, old)
            elif edits.get('kind') == 'damage_model':
                self.damage_model = edits['before'].copy()
            elif edits.get("kind") == "thresholds":
                self.health_thresholds = edits["before"].copy()
            elif edits.get("kind") == "annotations":
                self.annotations = {key: value.copy() for key, value in edits["before"].items()}
            elif edits.get("kind") == "collections":
                self.named_groups = {name: keys.copy() for name, keys in edits["before"].items()}
            elif edits.get("kind") == "spatial":
                for name, state in edits["before"].items():
                    self._restore_structure(name, state)
                self.named_groups = {name: keys.copy() for name, keys in edits["groups_before"].items()}
                self.annotations = {key: value.copy() for key, value in edits["annotations_before"].items()}
            else:
                self._restore_structure(edits["name"], edits["before"])
                self.named_groups = {name: keys.copy() for name, keys in edits["groups_before"].items()}
                self.annotations = {key: value.copy() for key, value in edits["annotations_before"].items()}
            self._redo.append(edits)
            if "parents_before" in edits:
                self.parent_groups = copy.deepcopy(edits["parents_before"])
            return True
        for name, index, old, _ in reversed(edits):
            self.document(name).set_visible_condition(index, old)
        self._redo.append(edits)
        return True

    def redo(self):
        if not self._redo:
            return False
        edits = self._redo.pop()
        if isinstance(edits, dict):
            if edits.get('kind') == 'visibility':
                self.annotations = copy.deepcopy(edits['after'])
                self.damage_model = edits['damage_after'].copy()
                for n, i, _, new in edits['groups']:
                    self.document(n).set_visible_condition(i, new)
            elif edits.get('kind') == 'damage_model':
                self.damage_model = edits['after'].copy()
            elif edits.get("kind") == "thresholds":
                self.health_thresholds = edits["after"].copy()
            elif edits.get("kind") == "annotations":
                self.annotations = {key: value.copy() for key, value in edits["after"].items()}
            elif edits.get("kind") == "collections":
                self.named_groups = {name: keys.copy() for name, keys in edits["after"].items()}
            elif edits.get("kind") == "spatial":
                for name, state in edits["after"].items():
                    self._restore_structure(name, state)
                self.named_groups = {name: keys.copy() for name, keys in edits["groups_after"].items()}
                self.annotations = {key: value.copy() for key, value in edits["annotations_after"].items()}
            else:
                self._restore_structure(edits["name"], edits["after"])
                self.named_groups = {name: keys.copy() for name, keys in edits["groups_after"].items()}
                self.annotations = {key: value.copy() for key, value in edits["annotations_after"].items()}
            self._undo.append(edits)
            if "parents_after" in edits:
                self.parent_groups = copy.deepcopy(edits["parents_after"])
            return True
        for name, index, _, new in edits:
            self.document(name).set_visible_condition(index, new)
        self._undo.append(edits)
        return True

    def reset(self):
        return self.apply_values([(document.name, mesh.index, document.original_group(mesh.index))
                                  for document, mesh in self.workspace.all_meshes()])

    def _snapshot(self, document):
        return (document.bytes(), getattr(document, "_structural_base", None),
                list(getattr(document, "_origin_groups", [])), list(getattr(document, "_splits", [])),
                list(getattr(document, "_mesh_labels", [])))

    def _restore_structure(self, name, state):
        document = self.document(name)
        document._data = bytearray(state[0])
        document.meshes = []
        document._parse()
        for attr, value in zip(("_structural_base", "_origin_groups", "_splits", "_mesh_labels"), state[1:]):
            if value:
                setattr(document, attr, value.copy() if isinstance(value, list) else value)
            elif hasattr(document, attr):
                delattr(document, attr)

    def split_mesh(self, name, index, mode="seams-v1"):
        self.validate_patch()
        document = self.document(name)
        if type(index) is not int or not 0 <= index < len(document.meshes):
            raise Mod3Error("Mesh 索引无效")
        islands = components(document.meshes[index], mode)
        before = self._snapshot(document)
        groups_before = self._collections()
        annotations_before = self._annotation_snapshot()
        data, origins, labels = rebuild_split(document, index, islands)
        splits = list(getattr(document, "_splits", [])) + [{"mesh": index, "mode": mode}]
        self._restore_structure(name, (data, data, origins, splits, labels))
        for group_name, members in self.named_groups.items():
            updated = set()
            for doc_name, mesh_index in members:
                if doc_name != name or mesh_index < index:
                    updated.add((doc_name, mesh_index))
                elif mesh_index == index:
                    updated.update((name, i) for i in range(index, index + len(islands)))
                else:
                    updated.add((name, mesh_index + len(islands) - 1))
            self.named_groups[group_name] = updated
        updated_annotations = {}
        for (doc_name, mesh_index), annotation in self.annotations.items():
            if doc_name != name or mesh_index < index:
                updated_annotations[(doc_name, mesh_index)] = annotation.copy()
            elif mesh_index == index:
                for i in range(index, index + len(islands)):
                    updated_annotations[(name, i)] = annotation.copy()
            else:
                updated_annotations[(name, mesh_index + len(islands) - 1)] = annotation.copy()
        self.annotations = updated_annotations
        self._undo.append({"name": name, "before": before, "after": self._snapshot(document),
                           "groups_before": groups_before, "groups_after": self._collections(),
                           "annotations_before": annotations_before, "annotations_after": self._annotation_snapshot()})
        self._redo.clear()
        return len(islands)

    def _partition_meshes(self, name, partitions):
        """Replay exact face assignments, independent of future matching changes."""
        self.validate_patch()
        document = self.document(name)
        before = self._snapshot(document)
        groups_before, annotations_before = self._collections(), self._annotation_snapshot()
        data, origins, labels = rebuild_partitions(document, partitions)
        layout, cursor = {}, 0
        for mesh in document.meshes:
            count = len(partitions[mesh.index]) if mesh.index in partitions else 1
            layout[mesh.index] = [(name, i) for i in range(cursor, cursor + count)]
            cursor += count
        recipe = {"mode": "lod-islands-v1", "partitions": [
            {"mesh": i, "faces": [np_part.tolist() if hasattr(np_part, "tolist") else list(np_part) for np_part in islands]}
            for i, islands in sorted(partitions.items())]}
        splits = copy.deepcopy(getattr(document, "_splits", [])) + [recipe]
        self._restore_structure(name, (data, data, origins, splits, labels))
        self.named_groups = {group: {child for key in members for child in (layout[key[1]] if key[0] == name else [key])}
                             for group, members in self.named_groups.items()}
        self.annotations = {child: value.copy() for key, value in self.annotations.items()
                            for child in (layout[key[1]] if key[0] == name else [key])}
        self._undo.append({"name": name, "before": before, "after": self._snapshot(document),
                           "groups_before": groups_before, "groups_after": self._collections(),
                           "annotations_before": annotations_before, "annotations_after": self._annotation_snapshot()})
        self._redo.clear()
        return layout

    def changes(self):
        return [{"document": document.name, "mesh": mesh.index, "part": document.equipment_part,
                 "material": document.material_info(mesh.material_id).name,
                 "display_name": self.annotations.get((document.name, mesh.index), {}).get("name", ""),
                 "original": document.original_group(mesh.index), "group": mesh.visible_condition,
                 "always_hidden": self.annotations.get((document.name, mesh.index), {}).get('visibility') == 'hidden'}
                for document, mesh in self.workspace.all_meshes()
                if mesh.visible_condition != document.original_group(mesh.index)
                or self.annotations.get((document.name, mesh.index), {}).get('visibility') == 'hidden']

    def validate_patch(self):
        for document in self.workspace.documents:
            current = document.bytes()
            baseline = getattr(document, "_structural_base", document._original)
            if current == baseline:
                continue
            expected = bytearray(baseline)
            for mesh in document.meshes:
                offset = mesh.visible_condition_offset
                expected[offset:offset + 2] = current[offset:offset + 2]
            if len(current) != len(baseline) or current != expected:
                raise Mod3Error(f"{document.name}: 检测到 Group 字段之外的修改，禁止导出")

    def save(self, path, view=None):
        path = Path(path).resolve()
        if path.suffix.lower() != ".json":
            raise Mod3Error("分层项目请保存为 .json 文件")
        source = self.workspace.source.resolve()
        if path == source or (self.workspace.kind == "folder" and source in path.parents):
            raise Mod3Error("项目文件请保存在原模组之外")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if existing.get("format") != PROJECT_FORMAT:
                raise Mod3Error("目标不是分层项目，不能覆盖")
        self.validate_patch()
        payload = {"format": PROJECT_FORMAT, "version": 13,
                   "damage_model": self.damage_model.copy(),
                   "health_thresholds": self.health_thresholds,
                   "annotations": [{"document": key[0], "mesh": key[1], **value} for key, value in sorted(self.annotations.items())],
                   "named_groups": [{"name": name, "members": sorted(keys)} for name, keys in self.named_groups.items()],
                   "parent_groups": self._parents(),
                   "source": os.path.relpath(source, path.parent) if source.drive == path.drive else str(source),
                   "documents": [{"name": document.name,
                                  "sha256": hashlib.sha256(document._original).hexdigest(),
                                  "splits": list(getattr(document, "_splits", [])),
                                  "source_metadata": self._source_metadata.get(document.name),
                                  "groups": [mesh.visible_condition for mesh in document.meshes]}
                                 for document in self.workspace.documents], "view": view or {}}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            staged = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, indent=2)
        try:
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)
        self.project_path = path
        self._saved_groups = self._groups()

    @classmethod
    def open(cls, path):
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("format") != PROJECT_FORMAT or data.get("version") not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13):
            raise Mod3Error("不支持的分层项目格式")
        source = (path.parent / data["source"]).resolve()
        workspace = ModWorkspace.open(source)
        records = data["documents"]
        if len(records) != len(workspace.documents) or len({record["name"] for record in records}) != len(records):
            raise Mod3Error("项目模型清单与源模组不一致")
        lookup = {record["name"]: record for record in records}
        session = cls(workspace)
        if "health_thresholds" in data:
            session.health_thresholds = restore_thresholds(data["health_thresholds"], legacy=data["version"] < 11)
        for document in workspace.documents:
            record = lookup.get(document.name)
            if not record or record["sha256"] != hashlib.sha256(document._original).hexdigest():
                raise Mod3Error("原模组已变化，禁止将项目分组套用到不同模型")
            if record.get("source_metadata") is not None:
                metadata = session._document_metadata(document, record["source_metadata"])
                document._origin_groups = metadata["original_groups"].copy()
                document._mesh_labels = metadata["mesh_labels"].copy()
                session._source_metadata[document.name] = metadata
            splits = record.get("splits", [])
            if not isinstance(splits, list) or len(splits) > 1024:
                raise Mod3Error("项目拆分记录无效")
            for recipe in splits:
                if isinstance(recipe, dict) and recipe.get("mode") == "lod-islands-v1" and data["version"] in (8, 9, 10, 11, 12, 13):
                    parts = recipe.get("partitions")
                    if not isinstance(parts, list) or not parts or any(not isinstance(p, dict) or type(p.get("mesh")) is not int for p in parts):
                        raise Mod3Error("LOD 三角面分区记录无效")
                    partitions = {p["mesh"]: p.get("faces") for p in parts}
                    if len(partitions) != len(parts):
                        raise Mod3Error("LOD 三角面分区记录重复")
                    session._partition_meshes(document.name, partitions)
                    continue
                if type(recipe) is int:
                    index, mode = recipe, "exact"
                elif isinstance(recipe, dict) and data["version"] in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13):
                    index, mode = recipe.get("mesh"), recipe.get("mode")
                    if not isinstance(mode, str):
                        raise Mod3Error("项目拆分算法无效")
                else:
                    raise Mod3Error("项目拆分记录无效")
                session.split_mesh(document.name, index, mode)
            if len(record["groups"]) != len(document.meshes) or any(
                type(group) is not int or not 0 <= group <= 65535 for group in record["groups"]
            ):
                raise Mod3Error("项目中的 Mesh 或 Group 信息无效")
        for document in workspace.documents:
            for index, group in enumerate(lookup[document.name]["groups"]):
                document.set_visible_condition(index, group)
        session._undo.clear()
        session._redo.clear()
        session._restore_tags(data)
        session.damage_model = damage_settings(data.get('damage_model'))
        session._saved_groups = session._groups()
        session.project_path = path
        return session, data.get("view", {})

    def _restore_tags(self, data):
        # Validate all tags before applying any so a bad member cannot partially rename meshes.
        session = self
        workspace = self.workspace
        named_groups, annotations = {}, {}
        records = data.get("named_groups", [])
        if not isinstance(records, list) or len(records) > 10000:
            raise Mod3Error("部件组清单无效")
        seen = set()
        for record in records:
            name, members = record.get("name"), record.get("members")
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name) or name in named_groups:
                raise Mod3Error("部件组名称无效或重复")
            if not isinstance(members, list) or any(not isinstance(k, list) or len(k) != 2 or not isinstance(k[0], str) or type(k[1]) is not int for k in members):
                raise Mod3Error("部件组成员无效")
            keys = session._check_keys(tuple(k) for k in members)
            if len(keys) != len(members) or keys & seen:
                raise Mod3Error("部件组成员重复")
            seen.update(keys)
            named_groups[name] = keys
        records = data.get("annotations", [])
        if not isinstance(records, list) or len(records) > workspace.mesh_count:
            raise Mod3Error("项目备注清单无效")
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("document"), str) or type(record.get("mesh")) is not int:
                raise Mod3Error("项目备注 Mesh 信息无效")
            key = (record["document"], record["mesh"])
            session._check_keys({key})
            if key in annotations:
                raise Mod3Error("项目备注 Mesh 重复")
            annotations[key] = {field: session._validate_annotation(field, record.get(field, "")) for field in ("name", "note")}
            if record.get('damage_role'):
                annotations[key]['damage_role'] = session._validate_annotation('damage_role', record['damage_role'])
            if record.get('visibility'):
                annotations[key]['visibility'] = session._validate_annotation('visibility', record['visibility'])
        parents = self._validate_parents(data.get("parent_groups", {}), named_groups)
        self.named_groups, self.annotations, self.parent_groups = named_groups, annotations, parents
