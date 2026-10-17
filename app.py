from __future__ import annotations

import copy
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk
import numpy as np

from mod3 import Mod3Document, Mod3Error, ModWorkspace, generate_health_rules
from renderer import SoftwareRenderer
from lod_groups import LOD_VIEWS, lod_label, visible_lod
from mesh_features import feature_profile, bone_summary
from mesh_regions import group_order, member_order, outfit_frames, position_hint
from session import EditorSession
from textures import TextureLibrary, discover_texture_roots
from ui_tables import ResizableTreeview, child_group_label
from health_rules import DEFAULT_THRESHOLDS, validate_thresholds, layer_name
from game_resources import GameTextures, ResourceError, saved_game_directory, save_game_directory
from export_preferences import export_suggestion, remember_export
from pointer_controls import alt_pressed
from dialog_geometry import center_dialog


APP_NAME = "MHW Layer Studio"
APP_VERSION = "0.6.2"


class LayerStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1500x900")
        self.minsize(1120, 740)
        self.workspace: ModWorkspace | None = None
        self.session: EditorSession | None = None
        self._export_target = None
        self.renderer = SoftwareRenderer()
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.drag_start: tuple[int, int] | None = None
        self.dragged = False
        self._rotate_start = None
        self._pan_start = None
        self._isolation_view = None
        self._temporary_mesh_colors = False
        self._redraw_job: str | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self._render_future = None
        self._render_pending = False
        self._render_generation = 0
        self._render_context = 0
        self._interaction_until = 0.0
        self._refine_job = None
        self._closed = False
        self.status = tk.StringVar(value="打开 MOD3、模组 ZIP 或文件夹开始")
        self.group_id = tk.StringVar(value="0")
        self.named_group = tk.StringVar(value="")
        self._last_group_pick = None
        self._recent_join_groups = []
        self.list_mode = tk.StringVar(value="按文件")
        self.group_search = tk.StringVar(value="")
        self._group_iids = {}
        self._document_iids = {}
        self._tree_group_signature = None
        self._tree_group_members = {}
        self._tree_inventory = None
        self._pane_layout = None
        self._group_search_job = None
        self.outfit_choice = tk.StringVar(value="")
        self._outfit_choices = {}
        self._active_outfit = None
        self.selected_text = tk.StringVar(value="未选择 Mesh")
        self.health = tk.DoubleVar(value=100)
        self.damage_mode = tk.StringVar(value='manual')
        from damage_model import DamagePreview
        self._damage_preview = DamagePreview()
        self._damage_hidden_keys = frozenset()
        self.isolate = tk.BooleanVar(value=False)
        self.ghost = tk.BooleanVar(value=False)
        self.simulate_health = tk.BooleanVar(value=True)
        self.color_mode = tk.StringVar(value="分层")
        self.lod_view = tk.StringVar(value="高精度")
        self.spatial_features = tk.BooleanVar(value=True)
        self.texture_alpha = tk.BooleanVar(value=False)
        self.texture_roots = []
        self.texture_overrides = {}
        self.game_directory = saved_game_directory() or ""
        from mod_library import discover_library_root
        self.mod_library_directory = discover_library_root(self.game_directory)
        self._mod_library_dialog = None
        self.game_resources = None
        if self.game_directory:
            try:
                self.game_resources = GameTextures(self.game_directory)
            except (ValueError, OSError):
                self.game_directory = ""
        self.part_vars = {part: tk.BooleanVar(value=True) for part in ("helm", "body", "arm", "wst", "leg", "other")}
        self._selection_keys = set()
        self._focus_selection = False
        families = set(tkfont.families(self))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font = tkfont.nametofont(name)
            if "Microsoft YaHei UI" in families:
                font.configure(family="Microsoft YaHei UI", size=10)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-z>", lambda _event: self.undo())
        self.bind("<Control-y>", lambda _event: self.redo())
        self.bind("<Control-s>", self._save_mod_shortcut)
        self.bind("<Control-Shift-S>", self._export_copy_shortcut)
        self.bind("<Control-Shift-s>", self._export_copy_shortcut)
        self.bind("<KeyPress-f>", self._focus_shortcut)

    def _build_ui(self) -> None:
        from editor_layout import build
        build(self)

    def _columns_changed(self):
        self.tree.configure(displaycolumns=tuple(c for c, var in self.column_vars.items() if var.get()))

    def _compact_columns(self):
        for column, variable in self.column_vars.items():
            variable.set(column in ("group", "visibility"))
        self._columns_changed()

    def _fit_visible_columns(self):
        for column in ("#0", *self.tree.cget("displaycolumns")):
            self.tree.fit_column(column)

    def _toggle_details(self):
        self._details_open = not self._details_open
        if self._details_open:
            self.details_panel.pack(fill="both", expand=True)
        else:
            self.details_panel.pack_forget()
        self.details_toggle.configure(text="部件详情 ▾" if self._details_open else "部件详情 ▸")

    def _toggle_split(self):
        self._split_open = not self._split_open
        if self._split_open:
            self.split_panel.pack(after=self.split_toggle, fill="x", pady=(4, 4))
        else:
            self.split_panel.pack_forget()
        self.split_toggle.configure(text="拆分 / 自动成组 ▾" if self._split_open else "拆分 / 自动成组 ▸")

    def _custom_group_dialog(self):
        value = simpledialog.askstring("自定义 Group ID", "输入 0–65535 的 Group ID（自定义 ID 需自行配置游戏规则）：",
                                       initialvalue=self.group_id.get(), parent=self)
        if value is not None:
            self.group_id.set(value)
            self.apply_custom_group()

    def _health_input_commit(self, _event=None):
        try:
            value = int(self.health_input.get())
        except ValueError:
            self.health_input.set(str(int(self.health.get())))
            return "break"
        self.health.set(max(0, min(100, value)))
        self._health_changed(str(self.health.get()))
        return "break"

    def _health_simulation_changed(self):
        self._update_threshold_labels()
        self._refresh_visibility_rows(force=True)
        self.schedule_redraw()

    def _toggle_isolate_button(self):
        self.isolate.set(not self.isolate.get())
        self._isolate_changed()
        self.after_idle(self._update_isolate_button)

    def _update_isolate_button(self):
        if not self._closed:
            self.isolate_button.configure(text="隔离 ✓" if self.isolate.get() else "隔离")
            self.isolate_button.state(["pressed"] if self.isolate.get() else ["!pressed"])

    def _mesh_visibility(self, document, mesh):
        key = (document.name, mesh.index)
        if self.session and self.session.annotations.get(key, {}).get('visibility') == 'hidden':
            return '始终隐藏 · 隔离查看' if self.isolate.get() and key in self._selection_keys else '始终隐藏'
        if key in self.renderer.hidden_keys:
            return "手动隐藏"
        if not self.part_vars.get(document.equipment_part, self.part_vars["other"]).get():
            return "部位关闭"
        if not visible_lod(mesh, LOD_VIEWS[self.lod_view.get()]):
            return "其他精度"
        if self.isolate.get() and key not in self._selection_keys:
            return "隔离排除"
        thresholds = self.session.health_thresholds if self.session else DEFAULT_THRESHOLDS
        threshold = thresholds.get(mesh.visible_condition)
        if self.session and self.session.damage_model['mode'] == 'random':
            return '随机战损隐藏' if self.simulate_health.get() and key in self._damage_hidden_keys else ('显示' if len(mesh.vertices) else '无几何')
        if self.simulate_health.get() and threshold is not None and int(self.health.get()) <= threshold:
            return "因血量隐藏"
        return "显示" if len(mesh.vertices) else "无几何"

    def _group_visibility(self, keys):
        states = {self._mesh_visibility(self.session.document(name), self.session.document(name).meshes[index])
                  for name, index in keys}
        if len(states) == 1:
            return next(iter(states))
        active = states - {"其他精度"}
        return next(iter(active)) + " / 多精度" if len(active) == 1 else "部分显示" if "显示" in states else "全部隐藏" if states else "空组"

    def _refresh_visibility_rows(self, force=False):
        if not self.workspace:
            return
        thresholds = self.session.health_thresholds
        # Slider movement within the same interval only updates four labels, not the entire tree.
        signature = (self._tree_group_signature, self._tree_inventory, self.simulate_health.get(),
                     self.session.damage_model['mode'], self._damage_hidden_keys,
                     tuple((g, t, int(self.health.get()) <= t) for g, t in thresholds.items()),
                     tuple(sorted(self.renderer.hidden_keys)), self.lod_view.get(),
                     tuple((p, v.get()) for p, v in self.part_vars.items()), self.isolate.get(),
                     frozenset(self._selection_keys) if self.isolate.get() else None)
        if not force and signature == getattr(self, "_visibility_signature", None):
            return
        self._visibility_signature = signature
        for di, document in enumerate(self.workspace.documents):
            for mesh in document.meshes:
                iid = f"m:{di}:{mesh.index}"
                if self.tree.exists(iid):
                    self.tree.set(iid, "visibility", self._mesh_visibility(document, mesh))
        for iid, keys in self._tree_group_members.items():
            self.tree.set(iid, "visibility", self._group_visibility(keys))
        self._update_selection_summary()

    def _selection_summary_text(self, selected=None):
        selected = self._selected_meshes() if selected is None else selected
        if not selected:
            return "先从模型或列表选择部件"
        keys = {(d.name, i) for d, i in selected}
        roots = [iid for iid in self.tree.selection() if self._group_iids.get(iid) is not None]
        covered = set().union(*(self._tree_group_members[iid] for iid in roots)) if roots else set()
        extra = len(keys - covered)
        if len(roots) == 1 and not extra:
            name = self._group_iids[roots[0]]
            total = len(self.session.group_members(name))
            if len(keys) < total:
                return f"组：{name}\n当前套装 {len(keys)} / 全组 {total} 个成员"
            if name in self.session.parent_groups:
                return f"父组：{name}\n{len(self.session.parent_groups[name])} 个子组 · 共 {len(keys)} 个成员"
            return f"整组：{name} · {len(keys)} 个成员"
        if roots:
            return f"多选：{len(roots)} 个组" + (f" + {extra} 个部件" if extra else "") + f" · 共 {len(keys)} 个部件"
        if len(selected) == 1:
            d, i = selected[0]
            name = self.session.annotations.get((d.name, i), {}).get("name")
            group = next((name for name, members in self.session.named_groups.items() if (d.name, i) in members), None)
            return (f"单个成员：{name or f'{d.equipment_part} / Mesh {i}'}\n所属组：{group}" if group else
                    f"单个部件：{name or f'{d.equipment_part} / Mesh {i}'} · 未成组")
        group = next((name for name, members in self.session.named_groups.items() if keys <= members), None)
        if group is not None:
            return f"组内多选：{group} · {len(keys)} / {len(self.session.named_groups[group])} 个成员"
        return f"多选：{len(keys)} 个部件"

    def _update_selection_summary(self):
        selected = self._selected_meshes()
        self.selection_summary.set(self._selection_summary_text(selected))
        states = [self._mesh_visibility(d, d.meshes[i]) for d, i in selected]
        hidden = states.count("因血量隐藏")
        visible = states.count("显示")
        other = "".join(f" · {state} {states.count(state)}" for state in sorted(set(states) - {"显示", "因血量隐藏"}))
        self.selection_effect.set(f"显示 {visible} · 血量隐藏 {hidden}" + other
                                  if selected else "可点组批量分配；隐藏后仍可从列表修改")
        for button in self.layer_buttons.values():
            # Native Windows disabled-image rendering makes the two-line label
            # unreadable. Keep the controls legible; apply_group guards edits.
            button.configure(state="normal")
        self._update_threshold_labels()
        self.visibility_button.configure(state="normal" if selected else "disabled",
                                         text="显示所选" if self._selection_keys and self._selection_keys <= self.renderer.hidden_keys else "隐藏所选")
        self.join_group_button.configure(state="normal" if selected and self.session and self.session.named_groups else "disabled")
        self._update_isolate_button()

    def _confirm_discard(self) -> bool:
        if not self.workspace:
            return True
        project_dirty = bool(self.session and ((self.session.project_path and self.session.project_dirty) or self.session.project_metadata_dirty))
        unsaved = self.workspace.has_unexported_changes and (not self.session or self.session.project_dirty)
        prompt = ("部件组、名称、备注或隐藏阈值尚未随模组导出或保存到项目。确定放弃未保存的修改吗？"
                  if self.session and self.session.project_metadata_dirty else "当前修改尚未导出或保存为项目，确定放弃吗？")
        return not (project_dirty or unsaved) or messagebox.askyesno(
            "未保存的修改", prompt
        , parent=self)

    def open_file(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开 MHW 模组",
            filetypes=(("MHW 模组", ("*.zip", "*.rar", "*.7z", "*.mod3")), ("ZIP / RAR / 7Z", ("*.zip", "*.rar", "*.7z")), ("MOD3", "*.mod3"), ("所有文件", "*.*")),
         parent=self)
        if path:
            self._load(path)

    def open_folder(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askdirectory(title="选择包含 MOD3 的模组文件夹", parent=self)
        if path:
            self._load(path)

    def open_mod_library(self):
        if self._mod_library_dialog is not None and self._mod_library_dialog.winfo_exists():
            self._mod_library_dialog.lift()
            return
        from mod_library_dialog import ModLibraryDialog
        self._mod_library_dialog = ModLibraryDialog(self)

    def _load(self, path: str) -> None:
        try:
            extension = Path(path).suffix.lower()
            self.status.set(f"正在读取 {extension[1:].upper()} 并准备预览缓存……" if extension in (".rar", ".7z") else "正在读取模型……")
            self.update_idletasks()
            workspace = ModWorkspace.open(path)
            self._adopt_session(EditorSession(workspace))
            if self.session.embedded_view:
                self._restore_view(self.session.embedded_view)
            self.status.set(
                f"已打开 {Path(path).name}：{len(self.workspace.documents)} 个 MOD3，"
                f"{self.workspace.mesh_count} 个可选择 Mesh"
            )
            self.schedule_redraw()
            if workspace.errors:
                messagebox.showwarning("部分模型未读取", "已加载可读取的模型。以下文件未读取：\n" + "\n".join(workspace.errors), parent=self)
            self.title(f"{APP_NAME} {APP_VERSION} · {Path(path).name} · {workspace.mesh_count} Mesh")
        except Exception as exc:
            messagebox.showerror("无法打开", str(exc), parent=self)
            self.status.set("打开失败，保留当前模组")

    def _populate_tree(self) -> None:
        opened_groups = {name for iid, name in self._group_iids.items() if self.tree.exists(iid) and self.tree.item(iid, "open")}
        self.tree.delete(*self.tree.get_children())
        self.tree.tag_configure('permanent_hidden', foreground='#777777')
        self._group_iids, self._document_iids, self._tree_group_members = {}, {}, {}
        self._tree_group_signature = self._group_tree_signature()
        self._tree_inventory = self._mesh_inventory()
        if not self.workspace:
            return
        self._position_frames = outfit_frames(self.workspace)
        if self.list_mode.get() == "按部件组":
            visible = {d.name: (di, d) for di, d in enumerate(self.workspace.documents)
                       if self._active_outfit is None or d.outfit_id == self._active_outfit}
            assigned = set().union(*self.session.named_groups.values()) if self.session.named_groups else set()
            unassigned = {(d.name, m.index) for _, d in visible.values() for m in d.meshes} - assigned
            groups = self.session.all_groups()
            children = {child for names in self.session.parent_groups.values() for child in names}
            records = [*group_order(self.workspace, [(name, members) for name, members in groups.items() if name not in children]), (None, unassigned)]
            group_indices = {name: i for i, name in enumerate(groups)}
            group_indices[None] = len(groups)
            query = self.group_search.get().strip().casefold()
            def insert_group(name, members, parent=""):
                gi = group_indices[name]
                local = {key for key in members if key[0] in visible}
                label = name if name is not None else "未成组"
                if (members and not local) or (name is None and not local):
                    return
                iid = f"g:{gi}"
                self._group_iids[iid] = name
                self._tree_group_members[iid] = local
                if parent:
                    parent_name = self._group_iids[parent]
                    label = child_group_label(label, parent_name, self.session.parent_groups[parent_name])
                is_parent = name in self.session.parent_groups
                text = f"{label} · {len(local)} 个 Mesh" + (f" · {len(self.session.parent_groups[name])} 个子组" if is_parent else "")
                self.tree.insert(parent, "end", iid=iid, text=text,
                                 values=self._group_node_values(iid), open=name in opened_groups or name is None)
                if is_parent:
                    for child in self.session.parent_groups[name]:
                        insert_group(child, groups[child], iid)
                    return
                for document_name in sorted({key[0] for key in local}):
                    di, document = visible[document_name]
                    parent = f"gd:{gi}:{di}"
                    self._document_iids[parent] = document_name
                    label = f"{document.equipment_part} · {Path(document.name).name}"
                    if self._active_outfit is None and len(self.workspace.outfits) > 1:
                        label = document.outfit_label + " / " + label
                    self.tree.insert(iid, "end", iid=parent, text=label, open=True)
                    for _, index in member_order(self.workspace, [k for k in local if k[0] == document_name]):
                        mesh = document.meshes[index]
                        self.tree.insert(parent, "end", iid=f"m:{di}:{index}", text=self._mesh_text(document, mesh),
                                         values=self._mesh_values(document, mesh),
                                         tags=('permanent_hidden',) if self.session.annotations.get((document.name, index), {}).get('visibility') == 'hidden' else ())
            for name, members in records:
                labels = [name or "未成组", *self.session.parent_groups.get(name, [])]
                if query and not any(query in label.casefold() for label in labels):
                    continue
                insert_group(name, members)
            return
        multiple = len(self.workspace.outfits) > 1
        outfit_nodes = {}
        for document_index, document in enumerate(self.workspace.documents):
            if self._active_outfit is not None and document.outfit_id != self._active_outfit:
                continue
            outfit_parent = ""
            if multiple:
                if document.outfit_id not in outfit_nodes:
                    outfit_parent = f"s:{len(outfit_nodes)}"
                    outfit_nodes[document.outfit_id] = outfit_parent
                    self.tree.insert("", "end", iid=outfit_parent, text=document.outfit_label, open=True)
                outfit_parent = outfit_nodes[document.outfit_id]
            parent = f"d:{document_index}"
            self._document_iids[parent] = document.name
            self.tree.insert(outfit_parent, "end", iid=parent, text=f"{document.equipment_part} · {Path(document.name).name}", open=True)
            for _, index in member_order(self.workspace, [(document.name, m.index) for m in document.meshes]):
                mesh = document.meshes[index]
                iid = f"m:{document_index}:{mesh.index}"
                self.tree.insert(
                    parent,
                    "end",
                    iid=iid,
                    text=self._mesh_text(document, mesh),
                    values=self._mesh_values(document, mesh),
                    tags=('permanent_hidden',) if self.session.annotations.get((document.name, mesh.index), {}).get('visibility') == 'hidden' else (),
                )

    def _group_node_values(self, iid):
        keys = self._tree_group_members[iid]
        ids = {self.session.document(name).meshes[index].visible_condition for name, index in keys}
        name = self._group_iids[iid]
        total = len(self.session.group_members(name)) if name is not None else len(keys)
        return (f"{len(keys)} 个部件", f"当前套装 {len(keys)} / 全组 {total}", name or "未成组",
                str(next(iter(ids))) if len(ids) == 1 else "混合" if ids else "", "", "多版本" if len({self.session.document(n).meshes[i].lod for n, i in keys}) > 1 else "", self._group_visibility(keys))

    def _group_tree_signature(self):
        return (self.list_mode.get(), self._active_outfit, self.group_search.get() if self.list_mode.get() == "按部件组" else "",
                tuple((name, tuple(sorted(keys))) for name, keys in self.session.all_groups().items()) if self.session else (),
                tuple((name, tuple(children)) for name, children in self.session.parent_groups.items()) if self.session else ())

    def _mesh_inventory(self):
        return tuple((d.name, len(d.meshes), id(d.meshes[0]) if d.meshes else None) for d in self.workspace.documents) if self.workspace else ()

    def _rebuild_tree(self, preserve_selection=True):
        keys = {(d.name, i) for d, i in self._selected_meshes()} if preserve_selection else set()
        groups = [self._group_iids[iid] for iid in self.tree.selection() if iid in self._group_iids] if preserve_selection else []
        self._populate_tree()
        group_items = [iid for iid, name in self._group_iids.items() if name in groups]
        items = group_items or [f"m:{di}:{index}" for di, d in enumerate(self.workspace.documents) for name, index in sorted(keys)
                                if name == d.name and self.tree.exists(f"m:{di}:{index}")]
        self.tree.selection_set(items)
        if items:
            self.tree.see(items[0])
        self._tree_selected()

    def _list_mode_changed(self, _event=None):
        if self.workspace:
            self._rebuild_tree()

    def _search_groups_changed(self, _event=None):
        if self._group_search_job:
            self.after_cancel(self._group_search_job)
        self.list_mode.set("按部件组")
        def refresh():
            self._group_search_job = None
            self._list_mode_changed()
        self._group_search_job = self.after(150, refresh)

    def _clear_group_search(self):
        if self._group_search_job:
            self.after_cancel(self._group_search_job)
            self._group_search_job = None
        self.group_search.set("")
        self._list_mode_changed()

    def _initial_pane_widths(self):
        width = self.main_panes.winfo_width()
        if width > 100:
            a, b = self._pane_layout or (.32, .78)
            self.main_panes.sashpos(0, int(width * a))
            self.main_panes.sashpos(1, int(width * b))

    def _remember_pane_widths(self, _event=None):
        width = max(1, self.main_panes.winfo_width())
        a, b = (self.main_panes.sashpos(i) / width for i in (0, 1))
        if .15 <= a < b - .15 and b <= .9:
            self._pane_layout = (a, b)

    def _adopt_session(self, session):
        self._last_group_pick = None
        self._isolation_view = None
        self._temporary_mesh_colors = False
        self._recent_join_groups = []
        self.session, self.workspace = session, session.workspace
        self.damage_mode.set(session.damage_model['mode'])
        from damage_model import DamagePreview
        self._damage_preview = DamagePreview()
        self._damage_hidden_keys = frozenset()
        self.export_button.configure(text="导出压缩包" if self.workspace.kind in ("zip", "rar", "7z") else "导出模组")
        self._export_target = None
        self._active_outfit = next(iter(self.workspace.outfits), None)
        self._refresh_outfits()
        self.named_group.set("")
        self.group_search.set("")
        self.list_mode.set("按部件组" if self.session.named_groups else "按文件")
        self._refresh_named_groups()
        self.renderer = SoftwareRenderer()
        self.texture_roots = discover_texture_roots(self.workspace.source)
        self.texture_overrides = {}
        self.texture_alpha.set(False)
        self._rebuild_texture_library()
        self.renderer.active_outfit = self._active_outfit
        self._selection_keys = set()
        self._focus_selection = False
        self.isolate.set(False)
        self.ghost.set(False)
        self.simulate_health.set(True)
        self.color_mode.set("分层")
        self.lod_view.set("高精度")
        self.health.set(100)
        self.health_input.set("100")
        for variable in self.part_vars.values():
            variable.set(True)
        self._populate_tree()
        self._update_info()
        self._update_history_buttons()
        self._update_threshold_labels()
        self.schedule_redraw()

    def _selected_meshes(self):
        if not self.workspace:
            return []
        keys = set()
        for iid in self.tree.selection():
            if iid.startswith("m:"):
                _, document_index, mesh_index = iid.split(":")
                keys.add((self.workspace.documents[int(document_index)].name, int(mesh_index)))
            elif iid in self._tree_group_members:
                keys.update(self._tree_group_members[iid])
        return [(document, index) for document in self.workspace.documents for name, index in sorted(keys)
                if name == document.name and index < len(document.meshes)]

    def _mesh_text(self, document, mesh):
        label = layer_name(mesh.visible_condition, self.session.health_thresholds)
        if self.session.annotations.get((document.name, mesh.index), {}).get('visibility') == 'hidden':
            label = '始终隐藏'
        name = self.session.annotations.get((document.name, mesh.index), {}).get("name")
        if not name and getattr(document, "_mesh_labels", [""] * len(document.meshes))[mesh.index]:
            frame = self._position_frames[document.outfit_id]
            name = f"{position_hint(document, mesh, frame)} · Mesh {mesh.index}"
        return f"{name or f'Mesh {mesh.index}'} · {label}"

    def _mesh_values(self, document, mesh):
        annotation = self.session.annotations.get((document.name, mesh.index), {})
        label = annotation.get("name") or document.material_info(mesh.material_id).name
        note = annotation.get("note", "").replace("\n", " · ").replace("\t", " ")
        collection = next((name for name, keys in self.session.named_groups.items() if (document.name, mesh.index) in keys), "未成组")
        return (f"{mesh.material_id} · {label}", note, collection, mesh.visible_condition, document.original_group(mesh.index), lod_label(mesh), self._mesh_visibility(document, mesh))

    def _edit_table_annotation(self, event):
        if self.tree.identify_region(event.x, event.y) == "separator":
            return
        iid = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        displayed = self.tree.cget("displaycolumns")
        column = "#0" if column == "#0" else displayed[int(column[1:]) - 1] if column else ""
        if iid.startswith("m:") and column in ("#0", "material", "note"):
            self.tree.selection_set(iid)
            self._tree_selected()
            self.edit_mesh_annotations("note" if column == "note" else "name")
            return "break"

    def edit_mesh_annotations(self, field=None):
        selected = self._selected_meshes()
        if not selected:
            messagebox.showinfo("请选择 Mesh", "请先选择一个或多个 Mesh，再编辑名称和备注。", parent=self)
            return
        keys = {(document.name, index) for document, index in selected}
        values = [self.session.annotations.get(key, {"name": "", "note": ""}) for key in keys]
        dialog = tk.Toplevel(self)
        dialog.title("编辑组件名称与备注")
        dialog.geometry("620x430")
        dialog.minsize(520, 350)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(4, weight=1)
        ttk.Label(dialog, text=f"所选 {len(keys)} 个 Mesh · 仅勾选的字段会应用到全部所选 · 留空可清除", wraplength=480, padding=10).grid(row=0, column=0, sticky="ew")
        change_name = tk.BooleanVar(dialog, value=field == "name" or (field is None and len(keys) == 1))
        change_note = tk.BooleanVar(dialog, value=field == "note" or (field is None and len(keys) == 1))
        ttk.Checkbutton(dialog, text="修改显示名称（最多 80 字）", variable=change_name).grid(row=1, column=0, sticky="w", padx=10)
        name_values = {v.get("name", "") for v in values}
        entry = ttk.Entry(dialog)
        entry.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        entry.insert(0, next(iter(name_values)) if len(name_values) == 1 else "")
        ttk.Checkbutton(dialog, text="修改备注（最多 2000 字）", variable=change_note).grid(row=3, column=0, sticky="w", padx=10)
        note = tk.Text(dialog, height=5, wrap="word", font="TkDefaultFont")
        note.grid(row=4, column=0, sticky="nsew", padx=10)
        note_values = {v.get("note", "") for v in values}
        note.insert("1.0", next(iter(note_values)) if len(note_values) == 1 else "")
        materials = sorted({document.material_info(document.meshes[index].material_id).name for document, index in selected})
        ttk.Label(dialog, text="原始材质：" + "、".join(materials[:5]) + "\n名称与备注仅用于编辑器分类，请保存项目以保留。", wraplength=480, padding=10).grid(row=5, column=0, sticky="ew")
        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=6, column=0, sticky="ew")
        def cancel(_event=None):
            dialog.destroy()
            return "break"
        def save(_event=None):
            fields = {}
            if change_name.get():
                fields["name"] = entry.get()
            if change_note.get():
                fields["note"] = note.get("1.0", "end-1c")
            if not fields:
                messagebox.showinfo("请选择字段", "请勾选要修改的显示名称或备注。", parent=dialog)
                return "break"
            try:
                count = self.session.edit_annotations(keys, **fields)
            except Mod3Error as exc:
                messagebox.showerror("无法保存标签", str(exc), parent=dialog)
                return "break"
            dialog.destroy()
            self._refresh_groups()
            self.status.set(f"已更新 {count} 个 Mesh 的名称 / 备注 · 可撤销 · 请保存项目")
            return "break"
        ttk.Button(buttons, text="取消（Esc）", command=cancel).pack(side="right")
        ttk.Button(buttons, text="保存（Ctrl+Enter）", command=save).pack(side="right", padx=8)
        dialog.bind("<Escape>", cancel)
        for widget in (dialog, entry, note):
            widget.bind("<Control-Return>", save)
        entry.bind("<Return>", save)
        center_dialog(dialog, self)
        entry.focus_set() if field != "note" else note.focus_set()

    def _refresh_outfits(self):
        self._outfit_choices = {"全部套装（叠加查看）": None}
        if self.workspace:
            for identity, documents in self.workspace.outfits.items():
                label = f"{documents[0].outfit_label} · {len(documents)} 文件 / {sum(len(d.meshes) for d in documents)} Mesh"
                self._outfit_choices[label] = identity
        self.outfit_combo.configure(values=list(self._outfit_choices))
        self.outfit_choice.set(next((label for label, identity in self._outfit_choices.items()
                                    if identity == self._active_outfit), "全部套装（叠加查看）"))

    def _outfit_changed(self, _event=None):
        if not self.workspace:
            return
        self._active_outfit = self._outfit_choices.get(self.outfit_choice.get())
        self._isolation_view = None
        self.renderer.active_outfit = self._active_outfit
        self._selection_keys = set()
        self.renderer.selected_keys = set()
        self.renderer._pick_order = np.empty(0, dtype=np.int64)
        self.preview.delete("all")
        self.isolate.set(False)
        self._focus_selection = False
        self._populate_tree()
        self._tree_selected()
        self._sync_preview_controls()
        self.renderer.fit(self.workspace)
        self.status.set(f"当前套装：{self.outfit_choice.get()} · 全部套装的分层仍保留，导出包含完整模组")
        self.schedule_redraw()

    def _selected_mesh(self) -> tuple[Mod3Document, int] | None:
        meshes = self._selected_meshes()
        return meshes[0] if meshes else None

    def _tree_selected(self, _event=None) -> None:
        selected_groups = [self._group_iids[iid] for iid in self.tree.selection() if iid in self._group_iids]
        if len(selected_groups) == 1:
            self.named_group.set(selected_groups[0] or "")
        keys = {(document.name, index) for document, index in self._selected_meshes()}
        changed = keys != self._selection_keys
        self._selection_keys = keys
        self.renderer.selected_keys = keys.copy()
        if changed and keys and not any(visible_lod(self.session.document(n).meshes[i], LOD_VIEWS[self.lod_view.get()]) for n, i in keys):
            label = next((label for label, bit in LOD_VIEWS.items() if bit is not None
                          and any(visible_lod(self.session.document(n).meshes[i], bit) for n, i in keys)), "全部版本")
            self.lod_view.set(label)
        groups = {document.meshes[index].visible_condition for document, index in self._selected_meshes()}
        self.group_id.set(str(next(iter(groups))) if len(groups) == 1 else "")
        if changed and keys and (self.isolate.get() or self._focus_selection):
            self.renderer.fit(self.workspace, keys)
        self._update_info()
        self._refresh_named_groups()
        self.schedule_redraw()

    def _refresh_named_groups(self):
        names = list(self.session.all_groups()) if self.session else []
        self.named_group_combo.configure(values=names)
        if self.named_group.get() not in names:
            self.named_group.set("")
        members = self.session.group_members(self.named_group.get()) if self.session else set()
        if not self.named_group.get():
            text = "多选 Mesh 后新建组"
        elif members:
            ids = {self.session.document(name).meshes[index].visible_condition for name, index in members}
            text = f"{len(members)} 个 Mesh · Group " + (str(next(iter(ids))) if len(ids) == 1 else "混合（可批量统一）")
            if self.named_group.get() in self.session.parent_groups:
                text += f" · {len(self.session.parent_groups[self.named_group.get()])} 个子组"
        else:
            text = "空组 · 选择 Mesh 后点击加入所选"
        self.named_group_summary.configure(text=text)

    def select_named_group(self, _event=None):
        if not self.session:
            return
        members = self.session.group_members(self.named_group.get())
        if self.list_mode.get() == "按部件组":
            if self.group_search.get() and self.group_search.get().strip().casefold() not in self.named_group.get().casefold():
                self.group_search.set("")
            if self._tree_group_signature != self._group_tree_signature():
                self._populate_tree()
            items = [iid for iid, name in self._group_iids.items() if name == self.named_group.get()]
            self.tree.selection_set(items)
            if items:
                self.tree.see(items[0])
            self._tree_selected()
            self.status.set(f"部件组 {self.named_group.get()} · 当前套装选中 {len(self._selection_keys)} / {len(members)} 个 Mesh"
                            + (" · 选中整个组请切换全部套装" if len(self._selection_keys) < len(members) else ""))
            return
        indices = {d.name: i for i, d in enumerate(self.workspace.documents)}
        items = [f"m:{indices[name]}:{index}" for name, index in sorted(members)
                 if self.tree.exists(f"m:{indices[name]}:{index}")]
        self.tree.selection_set(items)
        if items:
            self.tree.see(items[0])
        self._tree_selected()
        self._refresh_named_groups()
        self.status.set(f"部件组 {self.named_group.get()} · 当前套装选中 {len(items)} / {len(members)} 个 Mesh"
                        + (" · 选中整个组请切换全部套装" if len(items) < len(members) else ""))

    def edit_named_group(self, action):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        name = self.named_group.get()
        new_name = None
        keys = {(d.name, i) for d, i in self._selected_meshes()}
        if action == "create" and not keys:
            messagebox.showinfo("请选择 Mesh", "请先多选需要成组的 Mesh。", parent=self)
            return
        if action != "create" and not name:
            messagebox.showinfo("请选择部件组", "请先从部件组下拉框选择一个组。", parent=self)
            return
        if action in ("create", "rename"):
            entered = simpledialog.askstring("新建部件组" if action == "create" else "重命名部件组", "组名（例如：鞋子、外套）", initialvalue=name if action == "rename" else "", parent=self)
            if entered is None:
                return
            if action == "create":
                name = entered
            else:
                new_name = entered
        try:
            opened = {group: bool(self.tree.item(iid, "open")) for iid, group in self._group_iids.items()}
            self.session.edit_named_group(action, name, keys, new_name)
            if action == "add":
                self._recent_join_groups = [name] + [n for n in self._recent_join_groups if n != name][:4]
            elif action == "rename":
                self._recent_join_groups = [new_name.strip() if n == name else n for n in self._recent_join_groups]
            elif action == "delete":
                self._recent_join_groups = [n for n in self._recent_join_groups if n != name]
            self.named_group.set((new_name or name).strip() if action != "delete" else "")
            if action == "create":
                self.list_mode.set("按部件组")
                self.group_search.set("")
            self._refresh_groups()
            if action in ("create", "rename", "add"):
                self.named_group.set((new_name or name).strip())
                if action == "add":
                    self.list_mode.set("按部件组")
                    self.group_search.set("")
                self.select_named_group()
                for iid, group in self._group_iids.items():
                    if group == self.named_group.get():
                        self.tree.item(iid, open=opened.get(name, False) if action != "create" else False)
                    elif action == "add":
                        self.tree.item(iid, open=opened.get(group, False))
            self.status.set("部件组已更新 · 可撤销 · 保存项目或导出模组可保留组名和成员")
        except (Mod3Error, ValueError) as exc:
            messagebox.showerror("部件组操作失败", str(exc), parent=self)

    def _populate_join_group_menu(self):
        self._fill_join_group_menu(self.join_group_menu)

    def create_parent_group(self):
        if not self.session:
            return
        names = [self._group_iids[iid] for iid in self.tree.selection()
                 if self._group_iids.get(iid) in self.session.named_groups]
        if len(names) < 2:
            messagebox.showinfo("选择子组", "请在部件组列表中按 Ctrl 选择至少两个独立组，再建立父组。", parent=self)
            return
        name = simpledialog.askstring("建立父组", "父组名称（保留所选子组及各自层级）：", parent=self)
        if name is None:
            return
        try:
            self.session.edit_parent_group("create", name, names)
            self.list_mode.set("按部件组")
            self.group_search.set("")
            self._refresh_groups()
            self.named_group.set(name.strip())
            self.select_named_group()
            self.status.set("父组已建立 · 子组可独立分配，父组可统一分配 · 可撤销")
        except Mod3Error as exc:
            messagebox.showerror("无法建立父组", str(exc), parent=self)

    def _fill_join_group_menu(self, menu):
        menu.delete(0, "end")
        if not self.session or not self._selection_keys:
            return
        ordered = [name for name, _ in group_order(self.workspace, self.session.named_groups.items())]
        recent = [name for name in self._recent_join_groups if name in self.session.named_groups]
        remaining = [name for name in ordered if name not in recent]
        for index, name in enumerate(recent + remaining):
            if index == len(recent) and recent and remaining:
                menu.add_separator()
            members = self.session.named_groups[name]
            overlap = len(self._selection_keys & members)
            current = self._selection_keys <= members
            label = ("最近 · " if name in recent else "") + f"{name} · {len(members)} 个成员"
            if current:
                label += "（当前组）"
            elif overlap:
                label += f"（含所选 {overlap} 个）"
            menu.add_command(label=label, state="disabled" if current else "normal",
                             command=lambda target=name: self._join_selected_group(target))

    def _join_selected_group(self, name):
        # A target menu never replaces the current mesh selection, unlike the
        # locating combobox above it. Reuse the existing undo and folding logic.
        self.named_group.set(name)
        self.edit_named_group("add")

    def _show_selected_group(self):
        keys = {(d.name, i) for d, i in self._selected_meshes()}
        name = next((name for name, members in self.session.named_groups.items() if keys and keys <= members), None)
        if name is None:
            return
        self.list_mode.set("按部件组")
        self.group_search.set("")
        self._populate_tree()
        self.named_group.set(name)
        self.select_named_group()
        for iid, group_name in self._group_iids.items():
            if group_name == name:
                self.tree.item(iid, open=True)

    def _set_group_expanded(self, opened):
        for iid in self.tree.selection():
            if iid in self._group_iids:
                self.tree.item(iid, open=opened)

    def _tree_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or not self.session:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self._tree_selected()
        menu = tk.Menu(self, tearoff=False)
        keys = {(d.name, i) for d, i in self._selected_meshes()}
        named = self._group_iids.get(iid)
        if not keys and iid not in self._group_iids:
            menu.destroy()
            return
        if iid in self._group_iids:
            menu.add_command(label="展开成员", command=lambda: self._set_group_expanded(True))
            menu.add_command(label="收起成员", command=lambda: self._set_group_expanded(False))
            menu.add_command(label="将所选组建立父组…", command=self.create_parent_group)
        elif keys and any(keys <= members for members in self.session.named_groups.values()):
            menu.add_command(label="定位所属部件组", command=self._show_selected_group)
        if keys:
            def isolate():
                self.isolate.set(True)
                self._isolate_changed()
            menu.add_command(label="隔离所选", command=isolate)
            menu.add_command(label="聚焦所选", command=self.focus_selected)
            menu.add_command(label="隐藏所选", command=self.hide_selected)
            menu.add_separator()
            menu.add_command(label="将所选建为新组", command=lambda: self.edit_named_group("create"))
            menu.add_command(label="编辑名称 / 备注…", command=self.edit_mesh_annotations)
            if self.session.named_groups:
                targets = tk.Menu(menu, tearoff=False)
                menu.add_cascade(label="加入部件组", menu=targets)
                self._fill_join_group_menu(targets)
            membership = named or next((name for name, members in self.session.named_groups.items() if keys <= members), None)
            if membership and membership not in self.session.parent_groups:
                def remove_members():
                    self.named_group.set(membership)
                    self.edit_named_group("remove")
                menu.add_command(label="移出所属组", command=remove_members)
        if named:
            def rename_group():
                self.named_group.set(named)
                self.edit_named_group("rename")
            menu.add_command(label="重命名部件组", command=rename_group)
            def delete_group():
                self.named_group.set(named)
                self.edit_named_group("delete")
            menu.add_command(label="解散父组（保留子组）" if named in self.session.parent_groups else "解散部件组", command=delete_group)
        previous = getattr(self, "_context_menu", None)
        if previous is not None:
            previous.destroy()
        self._context_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _material_group_scope(self, scope):
        selected = {(d.name, i) for d, i in self._selected_meshes()}
        if scope == "所选 Mesh":
            return selected
        files = {name for name, _ in selected}
        if scope == "所选 Mesh 所在文件":
            # File headers can be selected directly without selecting every child row.
            files.update(self._document_iids[iid] for iid in self.tree.selection() if iid in self._document_iids)
        return {(d.name, m.index) for d, m in self.workspace.all_meshes()
                if (d.name in files if scope == "所选 Mesh 所在文件"
                    else self._active_outfit is None or d.outfit_id == self._active_outfit)}

    def auto_group_materials(self):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("按材质自动成组")
        dialog.geometry("860x520")
        dialog.minsize(640, 400)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)
        controls = ttk.Frame(dialog, padding=10)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="范围").grid(row=0, column=0, sticky="w")
        has_selection = bool(self._selected_meshes())
        has_files = any(iid in self._document_iids for iid in self.tree.selection())
        scope = tk.StringVar(value="所选 Mesh" if has_selection else "所选 Mesh 所在文件" if has_files else "当前套装全部 Mesh")
        choices = ttk.Combobox(controls, textvariable=scope, state="readonly", width=24,
                              values=("所选 Mesh", "所选 Mesh 所在文件", "当前套装全部 Mesh"))
        choices.grid(row=0, column=1, sticky="w", padx=8)
        include_grouped = tk.BooleanVar(value=False)
        replace = ttk.Checkbutton(controls, text="重新整理范围内已成组的 Mesh", variable=include_grouped)
        replace.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="同一文件内按 Material ID 归类；同名材质的不同文件分别成组。\n同材质可能同时含鞋子、外套等，成组后仍可移出或细分。Group ID、名称和备注保持。",
                  wraplength=610).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        summary = ttk.Label(dialog, padding=(10, 0, 10, 8), wraplength=610)
        summary.grid(row=1, column=0, sticky="ew")
        frame = ttk.Frame(dialog, padding=(10, 0))
        frame.grid(row=2, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ResizableTreeview(frame, columns=("file", "material", "count", "ids"), show="tree headings")
        tree.heading("#0", text="拟创建的部件组")
        tree.column("#0", width=330, minwidth=160, stretch=False)
        for key, label, width in (("file", "所属文件", 150), ("material", "材质 / ID", 130), ("count", "Mesh 数", 70), ("ids", "现有 Group", 100)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=width, stretch=False, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        footer = ttk.Frame(dialog, padding=10)
        footer.grid(row=3, column=0, sticky="ew")
        state = {}
        def refresh(*_):
            keys = self._material_group_scope(scope.get())
            plan = self.session.plan_material_groups(keys, include_grouped.get())
            state.update(keys=keys, plan=plan)
            tree.delete(*tree.get_children())
            for group in plan["groups"]:
                tree.insert("", "end", text=group["name"], values=(Path(group["document"]).name, f"{group['material_id']} · {group['material']}",
                            len(group["members"]), " / ".join(map(str, group["group_ids"]))))
            count = sum(len(g["members"]) for g in plan["groups"])
            label = f"{len(plan['groups'])} 个组 / {count} 个 Mesh · 跳过已成组 {plan['skipped']} 个"
            if plan["moved"]:
                label += f" · {plan['moved']} 个将移出原组"
            if plan["removed"]:
                label += f" · 替换 {len(plan['removed'])} 个被移空的原组"
            if not keys:
                label += " · 请换一个范围，或返回选择 Mesh / 文件"
            elif not count:
                label += " · 已有分组保持；需要重排时勾选上方选项"
            if self._active_outfit is None and scope.get() == "当前套装全部 Mesh":
                label += " · 当前为全部套装"
            summary.configure(text=label)
            apply.configure(state="normal" if count else "disabled")
        def commit(_event=None):
            if not state["plan"]["groups"]:
                return "break"
            try:
                plan = self.session.apply_material_groups(state["keys"], include_grouped.get())
                dialog.destroy()
                self.named_group.set(plan["groups"][0]["name"])
                self.list_mode.set("按部件组")
                self.group_search.set("")
                self._refresh_groups()
                self.select_named_group()
                count = sum(len(g["members"]) for g in plan["groups"])
                self.status.set(f"已按材质创建 {len(plan['groups'])} 个组 / {count} 个 Mesh · Group ID 保持 · Ctrl+Z 整批撤销")
            except (Mod3Error, ValueError) as exc:
                messagebox.showerror("自动成组失败", str(exc), parent=dialog)
            return "break"
        apply = ttk.Button(footer, text="应用成组", command=commit)
        apply.pack(side="right")
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side="right", padx=8)
        choices.bind("<<ComboboxSelected>>", refresh)
        replace.configure(command=refresh)
        dialog.bind("<Return>", commit)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        refresh()
        center_dialog(dialog, self)
        choices.focus_set()

    def suggest_accessories(self):
        if not self.session or not self._selection_keys:
            self.status.set("请先选择一个主体部件或同一文件内的组成员。")
            return
        if len({name for name, _ in self._selection_keys}) != 1:
            messagebox.showinfo("请选择同一文件", "主体请选择同一 MOD3 文件中的部件；原跨文件组会保留。", parent=self)
            return
        from spatial_dialog import SpatialGroupDialog
        SpatialGroupDialog(self, attachment_only=True)

    def auto_group_spatial(self, lod_only=False, sync_lod=False):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        from spatial_dialog import SpatialGroupDialog
        if sync_lod and len({key[0] for key in self._material_group_scope("所选 Mesh 所在文件")}) != 1:
            messagebox.showinfo("请选择一个文件", "请在左侧选择一个含多个 LOD 的 MOD3 文件，或其中的 Mesh，再同步拆分。", parent=self)
            return
        SpatialGroupDialog(self, lod_only=lod_only, sync_lod=sync_lod)

    def _select_by_key(self, key: tuple[str, int], additive=False, select_group=False) -> None:
        if not self.workspace:
            return
        group = next((name for name, members in self.session.named_groups.items() if key in members), None) if select_group else None
        if group is not None and not additive:
            group_row = next((iid for iid, name in self._group_iids.items() if name == group), None)
            same_selection = self.tree.selection() == (group_row,) or self._selection_keys == {key}
            if self._last_group_pick == (group, key) and same_selection:
                group = None  # Repeat on the same hit drills into the selected group.
            else:
                self._last_group_pick = (group, key)
        else:
            self._last_group_pick = None
        if group is not None:
            previous = self._selection_keys.copy() if additive else set()
            self.list_mode.set("按部件组")
            self.group_search.set("")
            if self._tree_group_signature != self._group_tree_signature():
                self._populate_tree()
            iid = next((iid for iid, name in self._group_iids.items() if name == group), None)
            if iid is not None:
                members = self._tree_group_members[iid]
                desired = (previous - members if members <= previous else previous | members) if additive else members
                items = [iid] if not additive else [row for row, keys in self._tree_group_members.items()
                         if self._group_iids[row] in self.session.named_groups and keys and keys <= desired]
                covered = set().union(*(self._tree_group_members[row] for row in items)) if items else set()
                items += [f"m:{di}:{i}" for di, d in enumerate(self.workspace.documents) for n, i in desired - covered
                          if n == d.name and self.tree.exists(f"m:{di}:{i}")]
                self.tree.selection_set(items)
                self.tree.focus(iid)
                self.tree.see(iid)
                self._tree_selected()
            return
        for document_index, document in enumerate(self.workspace.documents):
            if document.name == key[0]:
                iid = f"m:{document_index}:{key[1]}"
                if not self.tree.exists(iid):
                    return
                if additive:
                    if iid in self.tree.selection():
                        self.tree.selection_remove(iid)
                    else:
                        self.tree.selection_add(iid)
                else:
                    self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self._tree_selected()
                break

    def apply_group(self, group_id: int) -> None:
        selected = self._selected_meshes()
        if not selected:
            self.status.set("请先从模型或列表选择部件，再分配层级。")
            return
        count = self.session.apply_group([(document.name, index) for document, index in selected], group_id)
        self.group_id.set(str(group_id))
        self._refresh_groups()
        self.status.set(f"已将 {count} 个 Mesh 设置为 Group {group_id} · 可一次撤销")

    def apply_custom_group(self) -> None:
        try:
            value = int(self.group_id.get(), 0)
        except ValueError:
            messagebox.showerror("Group ID 无效", "请输入0–65535之间的十进制或0x十六进制整数。", parent=self)
            return
        try:
            self.apply_group(value)
        except ValueError as exc:
            messagebox.showerror("Group ID 无效", str(exc), parent=self)

    def apply_always_hidden(self):
        selected = self._selected_meshes()
        if not selected:
            self.status.set('请先选择组件或部件组，再设置始终隐藏。')
            return
        try:
            count = self.session.set_always_hidden([(d.name, i) for d, i in selected])
        except ValueError as exc:
            messagebox.showerror('无法设置始终隐藏', str(exc), parent=self)
            return
        self._refresh_groups()
        self.status.set(f'已设置 {count} 个 Mesh 始终隐藏 · 分配其他层级即可恢复 · 导出时附带插件清单，需要 VCE 1.5.1')

    def reset_all(self) -> None:
        if not self.workspace:
            return
        if not messagebox.askyesno("恢复原分组", "恢复所有 Mesh 的原始 Group ID 吗？已拆分的子部件会保留；拆分本身可通过撤销恢复。", parent=self):
            return
        self.session.reset()
        self._refresh_groups()
        self.status.set("已恢复原分组 · 可撤销此操作")

    def _refresh_groups(self, hidden_keys=None):
        structure_changed = self._tree_inventory != self._mesh_inventory()
        if structure_changed:
            self._selection_keys = set()
            self.renderer.selected_keys = set()
            self.renderer.hidden_keys = set()
            self.renderer._normals = {}
            self.renderer._pick_order = np.empty(0, dtype=np.int64)
            self.preview.delete("all")
            self.preview.create_text(20, 20, text="正在更新拆分预览…", anchor="nw", fill="#e3e8f2")
        if hidden_keys is not None:
            self.renderer.hidden_keys = set(hidden_keys)
        if structure_changed or self._tree_group_signature != self._group_tree_signature():
            self._rebuild_tree(preserve_selection=not structure_changed)
        for document_index, document in enumerate(self.workspace.documents):
            for mesh in document.meshes:
                if not self.tree.exists(f"m:{document_index}:{mesh.index}"):
                    continue
                self.tree.item(f"m:{document_index}:{mesh.index}",
                               text=self._mesh_text(document, mesh), values=self._mesh_values(document, mesh),
                               tags=('permanent_hidden',) if self.session.annotations.get((document.name, mesh.index), {}).get('visibility') == 'hidden' else ())
        for iid in self._group_iids:
            self.tree.item(iid, values=self._group_node_values(iid))
        self._tree_selected()
        self._update_history_buttons()
        self._refresh_named_groups()
        self._refresh_outfits()
        self._update_threshold_labels()
        self._refresh_visibility_rows(force=True)

    def split_selected(self):
        selected = self._selected_meshes()
        if len(selected) != 1:
            messagebox.showinfo("选择一个 Mesh", "请先选择一个 Mesh，再拆分为不相连的子部件。拆分后可 Ctrl/Shift 多选并批量设置 Group ID。", parent=self)
            return
        document, index = selected[0]
        try:
            count = self.session.split_mesh(document.name, index)
            self._refresh_groups()
            di = self.workspace.documents.index(document)
            self.tree.selection_set([f"m:{di}:{i}" for i in range(index, index + count)])
            self.tree.see(f"m:{di}:{index}")
            self.color_mode.set("独立部件")
            self._tree_selected()
            self.status.set(f"已按接缝容差拆为 {count} 个子部件 · Ctrl/Shift 多选后设置同一 Group · Ctrl+Z 可撤销")
        except Exception as exc:
            messagebox.showerror("无法拆分", str(exc), parent=self)

    def _update_info(self):
        self._update_selection_summary()
        selected = self._selected_meshes()
        lines = [f"已选择 {len(selected)} 个 Mesh"] if selected else ["未选择 Mesh", "点击模型或列表开始辨认部件"]
        for document, index in selected[:4]:
            mesh = document.meshes[index]
            material = document.material_info(mesh.material_id)
            annotation = self.session.annotations.get((document.name, index), {})
            lines.extend(["", f"{document.equipment_part} / {Path(document.name).name} · Mesh {index}",
                          f"套装：{document.outfit_label}",
                          f"显示名称：{annotation.get('name') or '未填写'}",
                          f"备注：{annotation.get('note') or '未填写'}",
                          f"材质 {mesh.material_id}：{material.name}",
                          f"Group {mesh.visible_condition} · 原始 {document.original_group(index)}",
                          f"精度：{lod_label(mesh)} · LOD 0x{mesh.lod & 0xffffffff:X}",
                          f"{mesh.vertex_count:,} 顶点 / {mesh.triangle_count:,} 三角面",
                          f"材质关联：{material.status}"])
            collection = next((name for name, members in self.session.named_groups.items() if (document.name, index) in members), "未成组")
            lines.append(f"部件组：{collection}")
            labels = getattr(document, "_mesh_labels", [])
            if index < len(labels) and labels[index]:
                lines.append(f"拆分来源：{labels[index]}")
            if material.name_hash is not None:
                lines.append(f"名称哈希：0x{material.name_hash:08X}")
            lines.append("可能使用的贴图：")
            lines.extend(material.textures or ("未知 / 未匹配",))
            lines.append("UV：" + ("可预览" if mesh.uvs is not None else "未知顶点格式，使用分层色"))
            profile = feature_profile(mesh)
            lines.append(f"骨骼读取：{mesh.bone_status}")
            lines.append(f"骨骼影响（文件内编号）：{bone_summary(profile)}")
            if profile["uv"] is not None:
                lo, hi = profile["uv"]
                lines.append(f"UV 范围：U {lo[0]:.3f}–{hi[0]:.3f} / V {lo[1]:.3f}–{hi[1]:.3f}")
            library = self.renderer.texture_library
            if library:
                lines.append("贴图预览：" + library.status.get((document.name, mesh.material_id), "切换“贴图”或点击“查看贴图”加载"))
            if (document.name, index) in self.renderer.hidden_keys:
                lines.append("当前临时隐藏 · Group 未因此修改")
            if not visible_lod(mesh, LOD_VIEWS[self.lod_view.get()]):
                lines.append("当前预览精度不显示此版本；可用精度下拉框切换")
        if len(selected) > 4:
            lines.append(f"\n还有 {len(selected) - 4} 个所选 Mesh；分组将应用到全部所选。")
        content = "\n".join(lines)
        self.selected_text.set(content)
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", content)
        self.info_text.configure(state="disabled")

    def _update_history_buttons(self):
        self.undo_button.configure(state="normal" if self.session and self.session.can_undo else "disabled")
        self.redo_button.configure(state="normal" if self.session and self.session.can_redo else "disabled")

    def undo(self):
        record = self.session._undo[-1] if self.session and self.session.can_undo else None
        if self.session and self.session.undo():
            hidden = record.get("preview_hidden_before") if isinstance(record, dict) else None
            self._refresh_groups(hidden_keys=hidden)
            self.status.set("已撤销上一步修改")

    def redo(self):
        record = self.session._redo[-1] if self.session and self.session.can_redo else None
        if self.session and self.session.redo():
            hidden = record.get("preview_hidden_after") if isinstance(record, dict) else None
            self._refresh_groups(hidden_keys=hidden)
            self.status.set("已重做修改")

    def _sync_preview_controls(self):
        from damage_model import permanently_hidden
        self.renderer.permanent_hidden_keys = permanently_hidden(self.session) if self.session else frozenset()
        self._update_damage_preview()
        self.renderer.health = int(float(self.health.get()))
        self.renderer.health_enabled = self.simulate_health.get()
        self.renderer.health_thresholds = self.session.health_thresholds.copy() if self.session else DEFAULT_THRESHOLDS.copy()
        self.renderer.isolate_selected = self.isolate.get()
        self.renderer.ghost_others = self.ghost.get()
        self.renderer.enabled_parts = {part for part, variable in self.part_vars.items() if variable.get()}
        self.renderer.active_outfit = self._active_outfit
        self.renderer.lod_bit = LOD_VIEWS[self.lod_view.get()]
        self.renderer.color_mode = ("mesh" if self._temporary_mesh_colors else
                                    {"分层": "layer", "独立部件": "mesh", "材质": "material", "贴图": "texture"}[self.color_mode.get()])
        self.renderer.texture_alpha = self.texture_alpha.get()
        self._refresh_visibility_rows()

    def _update_damage_preview(self):
        from damage_model import units
        mode = self.session.damage_model['mode'] if self.session else 'manual'
        self.damage_mode.set(mode)
        if mode == 'random':
            self._damage_hidden_keys = frozenset(self._damage_preview.update(units(self.session), float(self.health.get())))
        else:
            self._damage_hidden_keys = frozenset()
        self.renderer.health_hidden_keys = self._damage_hidden_keys if mode == 'random' else None

    def change_damage_mode(self):
        if not self.session:
            self.damage_mode.set('manual')
            self.status.set('请先打开模组，再选择衣装变化模式')
            return
        self.session.set_damage_mode(self.damage_mode.get())
        self._update_damage_preview()
        self._refresh_groups()
        self.schedule_redraw()
        self.status.set('衣装模式已切换 · 两套设置保留 · 导出后将配置更新至游戏插件目录，再 Ctrl+F5 重载')

    def edit_damage_settings(self):
        if not self.session:
            self.status.set('请先打开模组')
            return
        from damage_dialog import DamageSettingsDialog
        self._damage_dialog = DamageSettingsDialog(self)

    def _update_threshold_labels(self):
        values = self.session.health_thresholds if self.session else DEFAULT_THRESHOLDS
        self.hp_scale.set_ticks(values.values())
        from layer_controls import LayerButton
        wanted = [0, *sorted(values, key=lambda g: (-values[g], g))]
        if list(self.layer_buttons) != wanted:
            for group in list(self.layer_buttons):
                if group not in wanted:
                    self.layer_buttons.pop(group).destroy()
            for group in wanted:
                if group not in self.layer_buttons:
                    button = LayerButton(self.layer_actions, command=lambda g=group: self.apply_group(g))
                    button.bind("<MouseWheel>", self.right_panel._wheel, add="+")
                    self.layer_buttons[group] = button
            self.layer_buttons = {group: self.layer_buttons[group] for group in wanted}
            for button in self.layer_buttons.values():
                button.pack_forget()
                button.pack(fill="x", pady=3)
                button.configure(state="normal")
        self.layer_buttons[0].configure(text="基础 / 原始\n不随血量隐藏（0）")
        self.permanent_hide_button.pack_forget()
        self.permanent_hide_button.pack(fill='x', pady=3, after=self.layer_buttons[0])
        for group in wanted[1:]:
            label = layer_name(group, values)
            effect = "分配后隐藏" if int(self.health.get()) <= values[group] else "分配后显示"
            if not self.simulate_health.get():
                effect = "模拟关闭"
            if not self._selection_keys:
                effect = "先选择部件"
            self.layer_buttons[group].configure(text=f"{label} · ≤{values[group]}%隐藏\n{effect}（{group}）")

    def edit_health_thresholds(self):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        from health_dialog import HealthThresholdDialog
        self._threshold_dialog = HealthThresholdDialog(self)

    def _rebuild_texture_library(self):
        if self.workspace:
            roots = list(self.texture_roots)
            if self.game_directory:
                roots += [str(Path(self.game_directory) / "nativePC"), self.game_directory]
            self.renderer.texture_library = TextureLibrary(self.workspace, roots, self.texture_overrides, self.game_resources)

    def select_game_directory(self):
        path = filedialog.askdirectory(title="选择 MHW 游戏目录（含 chunk 文件夹）", initialdir=self.game_directory or None, parent=self)
        if not path:
            return
        try:
            resources = GameTextures(path)
            save_game_directory(resources.root)
            old = self.game_resources
            self.game_resources, self.game_directory = resources, str(resources.root)
            if old:
                old.close()
            self._rebuild_texture_library()
            self.color_mode.set("贴图")
            self.status.set(f"游戏目录已保存 · {len(resources.paths)} 个资源包 · 缺失贴图将在后台按需读取")
            self.schedule_redraw()
        except (OSError, ValueError) as exc:
            messagebox.showerror("游戏目录设置失败", str(exc), parent=self)

    def add_texture_root(self):
        if not self.workspace:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        path = filedialog.askdirectory(title="选择共享贴图目录（游戏目录或 nativePC）", parent=self)
        if path:
            path = str(Path(path).resolve())
            self.texture_roots = list(dict.fromkeys([*self.texture_roots, path]))
            self._rebuild_texture_library()
            self.color_mode.set("贴图")
            self.status.set("已添加共享贴图目录 · 仅用于预览")
            self.schedule_redraw()

    def assign_texture(self):
        selected = self._selected_meshes()
        if not selected:
            messagebox.showinfo("请选择 Mesh", "先选择需要辨识的 Mesh。颜色图会应用到同一文件内使用相同材质的 Mesh。", parent=self)
            return
        path = filedialog.askopenfilename(title="指定所选材质的颜色图（仅用于预览）",
            filetypes=(("颜色图", ("*.tex", "*.dds", "*.png", "*.jpg", "*.jpeg", "*.tga")), ("所有文件", "*.*")), parent=self)
        if path:
            for document, index in selected:
                self.texture_overrides[(document.name, document.meshes[index].material_id)] = str(Path(path).resolve())
            self._rebuild_texture_library()
            self.color_mode.set("贴图")
            self.schedule_redraw()

    def show_texture(self):
        selected = self._selected_meshes()
        if not selected:
            messagebox.showinfo("请选择 Mesh", "先选择需要查看贴图的 Mesh。", parent=self)
            return
        document, index = selected[0]
        mesh = document.meshes[index]
        library = self.renderer.texture_library
        dialog = tk.Toplevel(self)
        dialog.title(f"颜色图 / UV · {document.equipment_part} · Mesh {index}")
        dialog.geometry("580x650")
        dialog.transient(self)
        label = ttk.Label(dialog, text="正在读取颜色图……", wraplength=550)
        label.pack(fill="x", padx=12, pady=8)
        show_uv = tk.BooleanVar(value=True)
        canvas = tk.Canvas(dialog, background="#30343b", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=12)
        array = [None]
        def paint(*_):
            if array[0] is None or not dialog.winfo_exists():
                return
            image = Image.fromarray(array[0])
            image.thumbnail((512, 512))
            background = Image.new("RGB", image.size)
            draw = ImageDraw.Draw(background)
            for y in range(0, image.height, 16):
                for x in range(0, image.width, 16):
                    draw.rectangle((x, y, x + 15, y + 15), fill=(90, 94, 100) if (x // 16 + y // 16) % 2 else (55, 59, 65))
            background.paste(image, mask=image.getchannel("A"))
            if show_uv.get() and mesh.uvs is not None:
                draw = ImageDraw.Draw(background)
                uv = mesh.uvs * [image.width, image.height]
                # Skip wrapped/outside UVs instead of drawing misleading seam-spanning lines.
                for triangle in uv[mesh.faces]:
                    if np.all(triangle >= 0) and np.all(triangle <= [image.width, image.height]):
                        points = [tuple(p) for p in triangle]
                        draw.line(points + [points[0]], fill=(255, 224, 60), width=1)
            canvas.photo = ImageTk.PhotoImage(background)
            canvas.delete("all")
            canvas.create_image(canvas.winfo_width() / 2, canvas.winfo_height() / 2, image=canvas.photo)
        ttk.Checkbutton(dialog, text="叠加所选 Mesh 的 UV", variable=show_uv, command=paint).pack(anchor="w", padx=12, pady=8)
        future = self._executor.submit(library.get, document, mesh.material_id)
        def poll():
            if self._closed or not dialog.winfo_exists():
                return
            if not future.done():
                self.after(40, poll)
                return
            try:
                array[0] = future.result()
                label.configure(text=library.status.get((document.name, mesh.material_id), "没有颜色图"))
                if array[0] is not None:
                    paint()
                else:
                    canvas.create_text(280, 200, text="没有可用颜色图\n可添加共享贴图目录或手动指定", fill="#e3e8f2", justify="center")
                self._update_info()
            except Exception as exc:
                label.configure(text=f"贴图预览失败：{exc}")
        canvas.bind("<Configure>", paint)
        center_dialog(dialog, self)
        self.after(40, poll)

    def _lod_changed(self, _event=None):
        self._sync_preview_controls()
        self._update_info()
        self.schedule_redraw()

    def _isolate_changed(self):
        if self.isolate.get() and not self._selection_keys:
            self.isolate.set(False)
            self.status.set("请先选择部件，再隔离查看。")
            self._update_isolate_button()
            return
        if self.isolate.get() and self._isolation_view is None:
            self._isolation_view = (self.renderer.camera_state(), self._focus_selection)
        self._sync_preview_controls()
        if self.isolate.get():
            self._focus_selection = True
            if self.workspace:
                self.renderer.fit(self.workspace, self._selection_keys)
        elif self._isolation_view is not None:
            state, self._focus_selection = self._isolation_view
            self.renderer.restore_camera(state)
            self._isolation_view = None
        elif self.workspace:
            self._focus_selection = False
            self.renderer.fit(self.workspace)
        self.schedule_redraw()

    def hide_selected(self):
        if not self.workspace:
            return
        self.renderer.hidden_keys.update(self._selection_keys)
        self._refresh_groups()
        self.status.set(f"临时隐藏 {len(self._selection_keys)} 个 Mesh · 分组未改变")

    def toggle_selected_visibility(self):
        if not self._selection_keys:
            return
        if self._selection_keys <= self.renderer.hidden_keys:
            self.renderer.hidden_keys.difference_update(self._selection_keys)
            self._refresh_groups()
            self.status.set("已取消所选部件的临时隐藏 · 仍按当前血量和精度预览")
        else:
            self.hide_selected()

    def show_all(self):
        self._isolation_view = None
        self.renderer.hidden_keys.clear()
        self.isolate.set(False)
        self.ghost.set(False)
        self._focus_selection = False
        for variable in self.part_vars.values():
            variable.set(True)
        self._sync_preview_controls()
        if self.workspace:
            self.renderer.fit(self.workspace)
            self._refresh_groups()
        self.status.set("已显示当前套装全部部件 · 套装选择与血量模拟保持当前设置")
        self.schedule_redraw()

    def _parts_changed(self):
        self._sync_preview_controls()
        if self.workspace:
            self.renderer.fit(self.workspace)
        self.schedule_redraw()

    def standard_view(self, yaw):
        self.renderer.yaw, self.renderer.pitch = yaw, 0.0
        self.schedule_redraw()

    def center_view(self):
        self._sync_preview_controls()
        if self.workspace:
            self.renderer.fit(self.workspace, self._selection_keys if self._focus_selection and self._selection_keys else None)
        self.schedule_redraw()

    def focus_selected(self):
        if self.workspace and self._selection_keys:
            self._focus_selection = True
            self.renderer.fit(self.workspace, self._selection_keys)
            self.schedule_redraw()

    def _focus_shortcut(self, event):
        if not isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox)):
            self.focus_selected()

    def _pan_down(self, event):
        self.preview.focus_set()
        self.drag_start = None
        self._pan_start = (event.x, event.y)

    def _pan_drag(self, event):
        if getattr(self, "_pan_start", None):
            x, y = self._pan_start
            self.renderer.pan_x += (event.x - x) / max(1, self.preview.winfo_width())
            self.renderer.pan_y += (event.y - y) / max(1, self.preview.winfo_height())
            self._pan_start = (event.x, event.y)
            self.schedule_redraw(interactive=True)

    def _pan_up(self, _event):
        self._pan_start = None
        self.schedule_redraw()

    def _view_state(self):
        self._sync_preview_controls()
        return {"selected": [list(key) for key in sorted(self._selection_keys)],
                "outfit": self._active_outfit,
                "hidden": [list(key) for key in sorted(self.renderer.hidden_keys)],
                "parts": sorted(self.renderer.enabled_parts), "isolate": self.isolate.get(),
                "ghost": self.ghost.get(), "simulate_health": self.simulate_health.get(),
                "health": self.health.get(), "color": self.color_mode.get(), "lod_view": self.lod_view.get(),
                "spatial_features": self.spatial_features.get(),
                "list_mode": self.list_mode.get(), "group_search": self.group_search.get(),
                "selected_collections": [self._group_iids[iid] for iid in self.tree.selection() if iid in self._group_iids and self._group_iids[iid] is not None],
                "selected_ungrouped": any(iid in self._group_iids and self._group_iids[iid] is None for iid in self.tree.selection()),
                "expanded_collections": [name for iid, name in self._group_iids.items() if name is not None and self.tree.item(iid, "open")],
                "column_widths": {c: int(self.tree.column(c, "width")) for c in ("#0", *self.tree.cget("columns"))},
                "display_columns": list(self.tree.cget("displaycolumns")),
                "pane_ratios": [self.main_panes.sashpos(i) / max(1, self.main_panes.winfo_width()) for i in (0, 1)],
                "texture_alpha": self.texture_alpha.get(), "texture_roots": self.texture_roots,
                "game_directory": self.game_directory,
                "texture_overrides": [[n, m, path] for (n, m), path in sorted(self.texture_overrides.items())],
                "yaw": self.renderer.yaw, "pitch": self.renderer.pitch, "zoom": self.renderer.zoom,
                "pan_x": self.renderer.pan_x, "pan_y": self.renderer.pan_y,
                "focus_selection": self._focus_selection,
                "camera_center": self.renderer.camera_center.tolist() if self.renderer.camera_center is not None else None,
                "camera_span": self.renderer.camera_span}

    def save_project(self):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        destination = filedialog.asksaveasfilename(title="保存分层项目（保留原模组以便继续编辑）",
            defaultextension=".json", initialfile=self.session.project_path.name if self.session.project_path else "分层项目.mhwlayer.json",
            filetypes=(("分层项目", "*.json"),), parent=self)
        if destination:
            try:
                self.session.save(destination, self._view_state())
                self.status.set(f"项目已保存：{destination}")
            except Exception as exc:
                messagebox.showerror("项目保存失败", str(exc), parent=self)

    def open_project(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="打开分层项目", filetypes=(("分层项目", "*.json"),), parent=self)
        if path:
            self._load_project(path)

    def _load_project(self, path):
        try:
            session, view = EditorSession.open(path)
            self._adopt_session(session)
            self._restore_view(view)
            self.title(f"{APP_NAME} {APP_VERSION} · {Path(path).name} · {self.workspace.mesh_count} Mesh")
            legacy = any(isinstance(recipe, dict) and recipe.get("mode") == "exact"
                         for document in self.workspace.documents for recipe in getattr(document, "_splits", []))
            self.status.set("旧项目按原拆分恢复 · 应用接缝修复需打开原始 ZIP 重新拆分"
                            if legacy else "项目已恢复 · 原模组校验一致 · 可继续编辑或导出")
        except Exception as exc:
            messagebox.showerror("项目打开失败", str(exc), parent=self)

    def _restore_view(self, view):
        if not isinstance(view, dict):
            return
        if view.get("lod_view") in LOD_VIEWS:
            self.lod_view.set(view["lod_view"])
        self.spatial_features.set(view.get("spatial_features", True) is True)
        if view.get("list_mode") in ("按文件", "按部件组"):
            self.list_mode.set(view["list_mode"])
        search = view.get("group_search", "")
        self.group_search.set(search[:200] if isinstance(search, str) else "")
        widths = view.get("column_widths", {})
        displayed = view.get("display_columns")
        if isinstance(displayed, list) and len(displayed) == len(set(c for c in displayed if isinstance(c, str))) and all(c in self.column_vars for c in displayed):
            for column, variable in self.column_vars.items():
                variable.set(column in displayed)
            self._columns_changed()
        if isinstance(widths, dict):
            for column in ("#0", *self.tree.cget("columns")):
                value = widths.get(column)
                if type(value) is int:
                    self.tree.column(column, width=max(int(self.tree.column(column, "minwidth")), min(2400, value)), stretch=False)
        ratios = view.get("pane_ratios")
        if isinstance(ratios, list) and len(ratios) == 2 and all(type(v) in (int, float) and math.isfinite(v) for v in ratios):
            if .15 <= ratios[0] < ratios[1] - .15 and ratios[1] <= .9:
                self._pane_layout = tuple(ratios)
                self.after_idle(self._initial_pane_widths)
        active = view.get("outfit", self._active_outfit)
        if active is None or (isinstance(active, str) and active in self.workspace.outfits):
            self._active_outfit = active
        self._refresh_outfits()
        self.renderer.active_outfit = self._active_outfit
        self._populate_tree()
        expanded = view.get("expanded_collections", [])
        if isinstance(expanded, list):
            for iid, name in self._group_iids.items():
                if name is not None:
                    self.tree.item(iid, open=name in expanded)
        valid_keys = {(document.name, mesh.index) for document, mesh in self.workspace.all_meshes()}
        def keys(name):
            values = view.get(name, [])
            if not isinstance(values, list):
                return set()
            return {(value[0], value[1]) for value in values if isinstance(value, list) and len(value) == 2
                    and isinstance(value[0], str) and type(value[1]) is int and (value[0], value[1]) in valid_keys}
        self.renderer.hidden_keys = keys("hidden")
        self.isolate.set(view.get("isolate") is True)
        self.ghost.set(view.get("ghost") is True)
        self.simulate_health.set(view.get("simulate_health") is True)
        self._focus_selection = view.get("focus_selection") is True
        if isinstance(view.get("parts"), list):
            for part, variable in self.part_vars.items():
                variable.set(part in view["parts"])
        self.texture_alpha.set(view.get("texture_alpha") is True)
        roots = view.get("texture_roots", [])
        if isinstance(roots, list):
            self.texture_roots = list(dict.fromkeys(self.texture_roots + [r for r in roots if isinstance(r, str) and len(r) < 4096]))[:32]
        game = view.get("game_directory")
        if isinstance(game, str) and game and len(game) < 4096 and game != self.game_directory:
            try:
                resources = GameTextures(game)
                if self.game_resources:
                    self.game_resources.close()
                self.game_resources, self.game_directory = resources, str(resources.root)
            except (OSError, ValueError):
                self.status.set("项目保存的游戏目录不可用；可重新选择游戏资源目录")
        overrides = view.get("texture_overrides", [])
        if isinstance(overrides, list):
            materials = {(d.name, m.material_id) for d, m in self.workspace.all_meshes()}
            self.texture_overrides = {(r[0], r[1]): r[2] for r in overrides if isinstance(r, list) and len(r) == 3
                                      and isinstance(r[0], str) and type(r[1]) is int and isinstance(r[2], str)
                                      and len(r[2]) < 4096 and (r[0], r[1]) in materials}
        self._rebuild_texture_library()
        if view.get("color") in ("分层", "独立部件", "材质", "贴图"):
            self.color_mode.set(view["color"])
        selected = keys("selected")
        iids = [f"m:{i}:{index}" for i, document in enumerate(self.workspace.documents)
                for name, index in selected if name == document.name and self.tree.exists(f"m:{i}:{index}")]
        selected_groups = view.get("selected_collections", [])
        if isinstance(selected_groups, list):
            roots = [iid for iid, name in self._group_iids.items() if (name is not None and name in selected_groups)
                     or (name is None and view.get("selected_ungrouped") is True)]
            if roots:
                iids = roots
        self.tree.selection_set(iids)
        if iids:
            self.tree.see(iids[0])
        self._tree_selected()
        self._sync_preview_controls()
        self.renderer.fit(self.workspace, selected if self._focus_selection and selected else None)
        for name, low, high in (("yaw", -100, 100), ("pitch", -1.45, 1.45), ("zoom", .1, 40),
                                 ("pan_x", -1000, 1000), ("pan_y", -1000, 1000)):
            value = view.get(name)
            if type(value) in (int, float) and math.isfinite(value):
                setattr(self.renderer, name, max(low, min(high, value)))
        center = view.get("camera_center")
        span = view.get("camera_span")
        if (isinstance(center, list) and len(center) == 3 and all(type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e8 for v in center)
                and type(span) in (int, float) and math.isfinite(span) and .001 <= span <= 1e8):
            self.renderer.camera_center = np.array(center, dtype=np.float32)
            self.renderer.camera_span = span
        health = view.get("health", 100)
        if type(health) in (int, float) and math.isfinite(health):
            self.health.set(max(0, min(100, health)))
            self._health_changed(str(self.health.get()))
        self._refresh_groups()

    def overwrite_mod(self):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        target = self.workspace.source if self.workspace.kind == "folder" else self._export_target
        if target is None and self.workspace.kind in ("rar", "7z"):
            messagebox.showinfo("先另存 ZIP", "RAR / 7Z 暂不能原格式覆盖。请先导出 ZIP，之后可使用“覆盖保存”更新该 ZIP。", parent=self)
            self.export_mod()
            return
        self.export_mod(overwrite_target=target or self.workspace.source)

    def _save_mod_shortcut(self, _event=None):
        if not self.session:
            self.status.set("请先打开模组，再保存。")
        elif self.workspace.kind == "folder":
            self.overwrite_mod()
        else:
            self.export_mod()
        return "break"

    def _export_copy_shortcut(self, _event=None):
        if self.session:
            self.export_mod()
        else:
            self.status.set("请先打开模组，再导出副本。")
        return "break"

    def export_mod(self, overwrite_target=None):
        if not self.session:
            messagebox.showinfo("尚未打开模组", "请先打开模组。", parent=self)
            return
        current = getattr(self, "_export_dialog", None)
        if current is not None and current.winfo_exists():
            current.lift()
            return
        try:
            self.session.validate_patch()
        except Mod3Error as exc:
            messagebox.showerror("禁止导出", str(exc), parent=self)
            return
        dialog = tk.Toplevel(self)
        self._export_dialog = dialog
        dialog.title("覆盖前核对修改" if overwrite_target else "导出前核对修改")
        dialog.geometry("780x480")
        dialog.minsize(640, 360)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        changes = self.session.changes()
        splits = sum(len(getattr(d, "_splits", [])) for d in self.workspace.documents)
        detail = f"{splits} 次 Mesh 拆分 · {self.workspace.mesh_count} 个 Mesh" if splits else "仅 Group 字段变化"
        heading = ttk.Label(dialog, text=f"共 {len(changes)} 个 Mesh 分组修改 · {detail} · 导出后自动重读验证", padding=10, wraplength=600)
        heading.grid(row=0, column=0, sticky="ew")
        text = tk.Text(dialog, wrap="word", font="TkDefaultFont", height=8)
        text.grid(row=1, column=0, sticky="nsew", padx=(10, 26))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=text.yview)
        scroll.grid(row=1, column=0, sticky="nse", padx=(0, 10))
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", "\n".join(f"{change['part']} / {Path(change['document']).name} / Mesh {change['mesh']} / {change['display_name'] or change['material']}：{change['original']} → {change['group']}" + (' · 始终隐藏' if change.get('always_hidden') else '')
                                      for change in changes) or ("没有 Group 修改，将导出已拆分的模型副本。" if splits else "没有 Group 修改，将导出原模组副本。"))
        text.configure(state="disabled")
        note = ttk.Label(dialog, text="临时隐藏、部位开关和半透明不写入模组。自定义 Group 不会自动生成血量规则。", padding=10, wraplength=600)
        if self.session.damage_model['enabled']:
            note.configure(text='双模式导出：自动写入独立控制 ID 和 nativePC/plugins/MHWLayerStudio 配置；原手动层级保存在编辑信息中。需要 VCE 1.5.0 或更新版。临时隐藏和部位开关不写入模组。')
        if any(value.get('visibility') == 'hidden' for value in self.session.annotations.values()):
            note.configure(text='包含“始终隐藏”：模型与 nativePC/plugins/MHWLayerStudio 清单必须一起安装，需要 VCE 1.5.1 或更新版。原分层与几何保留。临时隐藏不导出。')
        note.grid(row=2, column=0, sticky="ew")
        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=3, column=0, sticky="ew")
        def cancel(_event=None):
            dialog.destroy()
            return "break"
        ttk.Button(buttons, text="返回编辑（Esc）", command=cancel).pack(side="right")
        def proceed(_event=None):
            if not dialog.winfo_exists():
                return "break"
            dialog.destroy()
            if overwrite_target:
                self._save_to_target(overwrite_target, overwrite=True)
            else:
                self._do_export_mod()
            return "break"
        confirm = ttk.Button(buttons, text="继续核对覆盖（Enter）" if overwrite_target else "确认并导出副本（Enter）", command=proceed, default="active")
        confirm.pack(side="right", padx=8)
        for widget in (dialog, text, confirm):
            widget.bind("<Return>", proceed)
            widget.bind("<KP_Enter>", proceed)
        dialog.bind("<Escape>", cancel)
        dialog.bind("<Configure>", lambda event: (
            heading.configure(wraplength=max(200, event.width - 40)),
            note.configure(wraplength=max(200, event.width - 40))) if event.widget == dialog else None)
        center_dialog(dialog, self)
        confirm.focus_set()

    def _do_export_mod(self) -> None:
        if not self.workspace or not self.workspace.source:
            messagebox.showinfo("尚未打开模组", "请先打开 MOD3、ZIP 或模组文件夹。", parent=self)
            return
        if self.workspace.kind in ("zip", "rar", "7z"):
            initial = export_suggestion(self.workspace.source, ".zip")
            destination = filedialog.asksaveasfilename(
                title="导出分层模组",
                defaultextension=".zip",
                initialdir=str(initial.parent),
                initialfile=initial.name,
                filetypes=(("ZIP", "*.zip"),),
                confirmoverwrite=False,
             parent=self)
        elif self.workspace.kind == "mod3":
            initial = export_suggestion(self.workspace.source, ".mod3")
            destination = filedialog.asksaveasfilename(
                title="导出 MOD3",
                defaultextension=".mod3",
                initialdir=str(initial.parent),
                initialfile=initial.name,
                filetypes=(("MOD3", "*.mod3"),),
                confirmoverwrite=False,
             parent=self)
        else:
            parent = filedialog.askdirectory(title="选择存放模组副本的位置", parent=self)
            destination = str(Path(parent) / (self.workspace.source.name + "_layered")) if parent else ""
        if not destination:
            return
        exists = Path(destination).exists() or (self.workspace.kind == "mod3" and Path(str(destination) + ".mhwlayer.json").exists())
        self._save_to_target(destination, overwrite=exists)

    def _save_to_target(self, destination, *, overwrite=False):
        try:
            self.session.validate_patch()
            destination = Path(destination)
            same_source = destination.resolve() == self.workspace.source.resolve()
            state = None
            if overwrite:
                state = self.session.overwrite_state(destination)
                count = sum(value is not None for value in state.values())
                text = f"将覆盖保存到：\n{destination.resolve()}\n\n涉及 {count} 个已有文件，覆盖前自动备份；校验失败不会写入。"
                if self.workspace.kind == "folder":
                    text += "\n目录中其他未涉及的文件保留。"
                if same_source:
                    text += "\n覆盖后重新载入，保留部件组和备注，清空撤销历史。已有项目需重新保存。"
                text += "\n\n确认覆盖吗？"
                if not messagebox.askyesno("确认覆盖保存", text, default="no", parent=self):
                    return
            old_workspace = self.workspace
            isolation_return = self._isolation_view
            view = self._view_state()
            path = self.session.export(destination, view, overwrite=overwrite, expected_state=state)
            backup = getattr(old_workspace, "last_backup", None) if overwrite else None
            if same_source:
                self._adopt_session(self.session)
                self._restore_view(view)
                self._isolation_view = isolation_return if self.isolate.get() else None
                self.schedule_redraw()
            self._export_target = Path(path)
            notice = f"{'覆盖保存' if overwrite else '导出'}成功：{path}"
            if backup:
                notice += f" · 备份：{backup}"
            if self.workspace.kind == "mod3":
                notice += " · 请一并保留旁边的 .mhwlayer.json"
            if self.session.damage_model['enabled']:
                notice += ' · 包含双模式插件配置 · 游戏内 Ctrl+F5 重载；独立控制 ID 需要新版插件'
            if old_workspace.kind != "folder":
                try:
                    remember_export(old_workspace.source, path)
                except (OSError, ValueError):
                    notice += " · 本次导出位置未能记住"
            self.status.set(notice)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def export_rules(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="导出血量规则",
            defaultextension=".ini",
            initialfile="MHWLayerStudio_health_rules.ini",
            filetypes=(("INI", "*.ini"), ("文本文件", "*.txt")),
         parent=self)
        if destination:
            try:
                with Path(destination).open("x", encoding="utf-8-sig") as output:
                    output.write(generate_health_rules(thresholds=self.session.health_thresholds if self.session else None))
                self.status.set(f"血量规则已导出：{destination}")
            except OSError as exc:
                messagebox.showerror("规则导出失败", str(exc), parent=self)

    def schedule_redraw(self, interactive=False) -> None:
        if self._closed:
            return
        self._render_generation += 1
        if interactive:
            self._interaction_until = time.monotonic() + .15
            if self._refine_job:
                self.after_cancel(self._refine_job)
            self._refine_job = self.after(160, self._refine_preview)
        else:
            # Selection, editing, another file/outfit, etc. invalidate old picking.
            self._render_context += 1
            self._interaction_until = 0.0
            if self._refine_job:
                self.after_cancel(self._refine_job)
                self._refine_job = None
        # Throttle to the next frame; continuous input must not postpone it.
        if self._redraw_job is None:
            self._redraw_job = self.after(16, self._redraw)

    def _refine_preview(self):
        self._refine_job = None
        self.schedule_redraw()

    def _redraw(self, synchronous=False) -> None:
        if self._closed:
            return
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw_job = None
        width, height = self.preview.winfo_width(), self.preview.winfo_height()
        if not self.workspace:
            self.preview.delete("all")
            self.preview.create_text(
                width / 2,
                height / 2,
                text="打开 MOD3 或模组 ZIP",
                fill="#c5ccd6",
                font=("Segoe UI", 16),
            )
            return
        self._sync_preview_controls()
        # Background rasterization keeps complete surfaces, including during rotation.
        self.renderer.max_triangles_per_mesh = None
        if synchronous:
            self._render_generation += 1
            self._render_context += 1
            image = self.renderer.render(self.workspace, (width, height))
            self._display_frame(image)
            return
        if self._render_future is not None:
            self._render_pending = True
            return
        frame_renderer = copy.copy(self.renderer)
        frame_renderer.selected_keys = self.renderer.selected_keys.copy()
        frame_renderer.hidden_keys = self.renderer.hidden_keys.copy()
        frame_renderer.enabled_parts = self.renderer.enabled_parts.copy()
        generation = self._render_generation
        future = self._executor.submit(frame_renderer.render_frame, self.workspace, (width, height), time.monotonic() < self._interaction_until)
        self._render_future = (future, generation, frame_renderer, self._render_context)
        self.after(8, self._poll_frame)

    def _poll_frame(self):
        if self._closed or self._render_future is None:
            return
        future, generation, frame_renderer, context = self._render_future
        if not future.done():
            library = frame_renderer.texture_library
            if library and library.game and frame_renderer.color_mode == "texture":
                self.status.set(library.game.progress)
            self.after(8, self._poll_frame)
            return
        self._render_future = None
        try:
            image = future.result()
            # Show intermediate poses; a different edit/file still invalidates them.
            if context == self._render_context:
                for name in ("_polygons", "_face_ids", "_face_keys", "_pick_order", "_depths", "_pixel_ids", "_pick_scale",
                             "_triangle_depths", "_pick_alpha", "selection_occluded"):
                    setattr(self.renderer, name, getattr(frame_renderer, name))
                if self.renderer.camera_center is None:
                    self.renderer.camera_center = frame_renderer.camera_center
                    self.renderer.camera_span = frame_renderer.camera_span
                self.renderer._pick_cache = None
                self._display_frame(image)
                if generation == self._render_generation and self.renderer.color_mode == "texture":
                    self._update_info()
                    library = self.renderer.texture_library
                    loaded = sum(value is not None for value in library.cache.values()) if library else 0
                    missing = len(library.cache) - loaded if library else 0
                    self.status.set(f"贴图预览已更新 · {loaded} 个材质有颜色图 · {missing} 个未找到或暂不支持")
        except Exception as exc:
            self.status.set(f"预览失败：{exc}")
        if self._render_pending or generation != self._render_generation:
            self._render_pending = False
            self._redraw()

    def _display_frame(self, image):
        self.preview.delete("all")
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview.create_image(0, 0, image=self.preview_photo, anchor="nw")
        if not len(self.renderer._polygons):
            self.preview.create_text(image.width / 2, image.height / 2,
                text="无可见 Mesh\n检查隔离、临时隐藏、部位开关和血量模拟", fill="#c5ccd6", justify="center", font="TkDefaultFont")
        self.preview.create_text(12, 12, text=self._preview_caption(), fill="#e3e8f2", anchor="nw", font="TkDefaultFont", tags="caption")

    def _preview_caption(self):
        summary = self._selection_summary_text() if self._selection_keys else "未选择部件"
        feedback = "\n黄色实线：可见所选 · 橙色虚线：遮挡所选" if self.renderer.selection_occluded else ""
        return (f"{summary}\n血量模拟 {'开启' if self.simulate_health.get() else '关闭'} · HP {int(self.health.get())}%"
                + (" · 空格临时部件色" if self._temporary_mesh_colors else "") + feedback + "\nAlt＋左键：穿透选择内部部件")

    def _health_changed(self, value: str) -> None:
        hp = int(float(value))
        old_random = self._damage_hidden_keys
        self._update_damage_preview()
        self.health_input.set(str(hp))
        self._update_threshold_labels()
        self._refresh_visibility_rows()
        thresholds = self.session.health_thresholds if self.session else DEFAULT_THRESHOLDS
        groups = {mesh.visible_condition for _, _, mesh in self.renderer.visible_entries(self.workspace, include_health=False)} if self.workspace else set()
        changed = self.simulate_health.get() and any((self.renderer.health > threshold) != (hp > threshold)
                                                   for group, threshold in thresholds.items() if group in groups)
        changed = changed or (self.simulate_health.get() and old_random != self._damage_hidden_keys)
        self.renderer.health = hp
        self.preview.itemconfigure("caption", text=self._preview_caption())
        if changed:
            self.schedule_redraw(interactive=True)

    def _mouse_down(self, event) -> None:
        self.preview.focus_set()
        self.drag_start = (event.x, event.y)
        self.dragged = False

    def _mouse_drag(self, event) -> None:
        if not self.drag_start:
            return
        dx, dy = event.x - self.drag_start[0], event.y - self.drag_start[1]
        if max(abs(dx), abs(dy)) > 4:
            self.dragged = True

    def _mouse_up(self, event) -> None:
        if (self.drag_start and not self.dragged
                and max(abs(event.x - self.drag_start[0]), abs(event.y - self.drag_start[1])) <= 4):
            through = alt_pressed(event.state)
            if through:
                picked = self.renderer.pick_through(event.x, event.y)
            else:
                self.renderer._through_pick = None
                picked = self.renderer.pick(event.x, event.y)
            if picked:
                # A normal click always starts from the named group. Repeating
                # the same hit drills into its member in _select_by_key. This
                # keeps legacy projects from silently disabling group picking.
                self._select_by_key(picked, additive=bool(event.state & (0x4 | 0x1)), select_group=not through)
                if through:
                    self.status.set(f"穿透选中 Mesh {picked[1]} · 橙色虚线显示被遮挡轮廓 · 同处 Alt＋左键继续切换")
            elif not event.state & (0x4 | 0x1):
                self._last_group_pick = None
                self.tree.selection_remove(*self.tree.selection())
                self._tree_selected()
        self.drag_start = None

    def _rotate_down(self, event):
        self.preview.focus_set()
        self.drag_start = None
        self._rotate_start = (event.x, event.y)

    def _rotate_drag(self, event):
        if self._rotate_start is None:
            return
        x, y = self._rotate_start
        self.renderer.yaw += (event.x - x) * .012
        self.renderer.pitch = max(-1.45, min(1.45, self.renderer.pitch + (event.y - y) * .012))
        self._rotate_start = (event.x, event.y)
        self.schedule_redraw(interactive=True)

    def _rotate_up(self, _event):
        self._rotate_start = None
        self.schedule_redraw()

    def _mouse_wheel(self, event) -> None:
        if event.delta:
            self.preview.focus_set()
            self._zoom(1.1 if event.delta > 0 else 0.9, (event.x, event.y))

    def _zoom(self, factor: float, point=None) -> None:
        self.renderer.zoom_at(factor, (self.preview.winfo_width(), self.preview.winfo_height()), point)
        self.schedule_redraw(interactive=True)

    def _pivot_at_pointer(self, event):
        self.preview.focus_set()
        self._pan_start = None
        key = self.renderer.pick(event.x, event.y)
        if key and self.workspace:
            keys = self._selection_keys if key in self._selection_keys else {key}
            if self.renderer.pivot_on(self.workspace, keys, (self.preview.winfo_width(), self.preview.winfo_height())):
                self.status.set("旋转中心已设为所指部件 · 缩放和观察位置保持 · 右键拖动旋转")
                self.schedule_redraw()
        return "break"

    def _temporary_color_press(self, event):
        if event.state & 0x4 or alt_pressed(event.state) or not self.workspace:
            return
        if not self._temporary_mesh_colors:
            self._temporary_mesh_colors = True
            self.schedule_redraw()
        return "break"

    def _temporary_color_release(self, _event=None):
        if self._temporary_mesh_colors:
            self._temporary_mesh_colors = False
            self.schedule_redraw()

    def reset_view(self) -> None:
        self.renderer.yaw = self.renderer.pitch = 0.0
        self.center_view()
        self.schedule_redraw()

    def destroy(self):
        self._closed = True
        dialog = getattr(self, "_mod_library_dialog", None)
        if dialog is not None and not dialog.closed:
            dialog.close()
        if self.game_resources:
            self.game_resources.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().destroy()

    def _on_close(self) -> None:
        if self._confirm_discard():
            self.destroy()


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()
    app = LayerStudio()
    if len(sys.argv) == 4 and sys.argv[1] == '--check-permanent-visibility':
        from permanent_visibility_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == '--check-damage-model':
        from damage_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-dialog-placement":
        from dialog_placement_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-parent-groups":
        from parent_groups_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-attachments":
        from attachment_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-edit-workflow":
        from edit_workflow_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-mod-library":
        from mod_library_validation import check
        sys.exit(check(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-mouse-controls":
        from diagnostics import check_mouse_controls
        sys.exit(check_mouse_controls(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-editor-layout":
        from diagnostics import check_editor_layout
        sys.exit(check_editor_layout(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-lod-sync":
        from diagnostics import check_lod_sync
        sys.exit(check_lod_sync(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-lod-preview":
        from diagnostics import check_lod_preview
        sys.exit(check_lod_preview(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-spatial-preview":
        from diagnostics import check_spatial_preview
        sys.exit(check_spatial_preview(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 5 and sys.argv[1] == "--check-game-preview":
        from diagnostics import check_game_preview
        app.withdraw()
        sys.exit(check_game_preview(app, sys.argv[2], sys.argv[3], sys.argv[4]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-preview":
        from diagnostics import check_preview
        app.withdraw()
        sys.exit(check_preview(app, sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.after(100, lambda: app._load_project(sys.argv[1]) if Path(sys.argv[1]).suffix.lower() == ".json" else app._load(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
