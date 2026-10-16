"""Review staged splits and spatial collections in a separate, cancellable view."""
import copy
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

from PIL import ImageTk
from renderer import SoftwareRenderer
from ui_tables import ResizableTreeview, child_group_label
from lod_groups import LOD_VIEWS, alternative, lod_label, visible_lod
from mesh_features import feature_profile, bone_summary
from mesh_regions import group_order, member_order, outfit_frames, position_hint
from dialog_geometry import place_large_dialog
from pointer_controls import alt_pressed


class SpatialGroupDialog(tk.Toplevel):
    def __init__(self, app, lod_only=False, sync_lod=False, attachment_only=False):
        super().__init__(app)
        self.app, self.session = app, app.session
        self.sync_lod = sync_lod
        self.attachment_only = attachment_only
        self.attachment_seeds = app._selection_keys.copy() if attachment_only else set()
        self.lod_only = lod_only = lod_only or sync_lod
        self.plan = None
        self.closed = False
        self.stop = threading.Event()
        self.analysis = self.frame = None
        self.poll_job = self.draw_job = None
        self.pending = False
        self.context = 0
        self.interaction_until = 0
        self.temporary_mesh_colors = False
        self.renderer = SoftwareRenderer()
        self.renderer.health_enabled = False
        self.renderer.color_mode = "mesh"
        self.renderer.ghost_others = True
        self.renderer.texture_library = app.renderer.texture_library
        self.title("跨 LOD 同步拆分 · 先核对各精度" if sync_lod else "LOD 自动成组 · 先预览再应用" if lod_only else "拆分与空间成组 · 先预览再应用")
        if attachment_only:
            self.title("主体关联附件 · 保留现有部件与组")
        self.transient(app)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        options = ttk.Frame(self, padding=10)
        options.grid(row=0, column=0, sticky="ew")
        has_selection = bool(app._selected_meshes())
        has_file = any(iid in app._document_iids for iid in app.tree.selection())
        self.scope = tk.StringVar(value="所选 Mesh 所在文件" if sync_lod or attachment_only else "所选 Mesh" if has_selection else "所选 Mesh 所在文件" if has_file else "当前套装全部 Mesh")
        self.split = tk.BooleanVar(value=not attachment_only and (sync_lod or not lod_only))
        self.associate_lod = tk.BooleanVar(value=not attachment_only)
        self.use_features = app.spatial_features
        self.pair = tk.BooleanVar(value=not sync_lod and not attachment_only)
        self.replace = tk.BooleanVar(value=False)
        self.distance = tk.StringVar(value="常规")
        ttk.Label(options, text="范围").grid(row=0, column=0)
        scope = ttk.Combobox(options, state="readonly", textvariable=self.scope, width=23,
                             values=("所选 Mesh", "所选 Mesh 所在文件", "当前套装全部 Mesh"))
        scope.grid(row=0, column=1, padx=8)
        if sync_lod or attachment_only:
            scope.configure(state="disabled")
        ttk.Label(options, text="基准" if sync_lod else "聚合程度").grid(row=0, column=2)
        distance = ttk.Combobox(options, state="readonly", textvariable=self.distance, width=7, values=("保守", "常规", "宽松"))
        distance.grid(row=0, column=3, padx=8)
        if sync_lod:
            self.distance.set("最高精度")
            distance.configure(state="disabled")
        if attachment_only:
            self.distance.set("主体附近")
            distance.configure(state="disabled")
        self.generate = ttk.Button(options, text="生成建议", command=self.analyze)
        self.generate.grid(row=0, column=4)
        for i, (label, variable) in enumerate(((("低精度按完整几何块重组" if sync_lod else "先按独立几何拆分"), self.split), ("建立左右父组", self.pair), ("重新整理已有组", self.replace))):
            check = ttk.Checkbutton(options, text=label, variable=variable, command=self.options_changed)
            check.grid(row=1, column=i * 2, columnspan=2, sticky="w", pady=7)
            if attachment_only or (lod_only and variable in (self.split, self.pair)):
                check.configure(state="disabled")
        lod_check = ttk.Checkbutton(options, text="保留原三角面、权重及 LOD" if sync_lod else "关联对应 LOD；保留各精度整体", variable=self.associate_lod, command=self.options_changed)
        lod_check.grid(row=2, column=0, columnspan=3, sticky="w")
        features_check = ttk.Checkbutton(options, text="骨骼 / UV 候选核对" if sync_lod else "骨骼 / UV 辅助空间成组", variable=self.use_features, command=self.options_changed)
        features_check.grid(row=2, column=3, columnspan=3, sticky="w")
        if lod_only or attachment_only:
            lod_check.configure(state="disabled")
            if lod_only and not sync_lod:
                features_check.configure(state="disabled")
        for combo in (scope, distance):
            combo.bind("<<ComboboxSelected>>", self.options_changed)
        description = ("以现有高精度 Mesh 为基准，将低精度完整几何块对应重组；不切开三角面。逐个切换精度核对后应用。\n待确认块需移入对应高精度组；可点击“同时看候选”对照。高精度仍过粗时，可先安全拆分，再同步。" if sync_lod else
                       "同文件、同材质、同 Group 内提出建议。选中一个 LOD 时会纳入其对应版本；未找到唯一对应时待确认。\n低精度合并多个高精度部件时需一起控制。通用 Mesh 仍按几何处理；LOD 不切碎，确认前不修改。")
        if not lod_only and not attachment_only:
            description += "\n骨骼一致且表面相连的小件先组成单侧组件；建立左右父组后，可分别选择子组或统一选择父组。"
        if attachment_only:
            description = "保留已有独立部件和手工组，围绕主界面所选主体寻找附件，不重新拆分或自动改组。\n先预览并确认附件，再应用当前建议。跨文件、不同 Group 或不同精度不会自动加入。"
        ttk.Label(options, text=description,
                  wraplength=1000).grid(row=3, column=0, columnspan=6, sticky="w")
        self.summary = ttk.Label(self, text="准备分析…", padding=(10, 0, 10, 7), wraplength=1000)
        self.summary.grid(row=1, column=0, sticky="ew")
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew", padx=10)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        self.after_idle(lambda: panes.sashpos(0, round(panes.winfo_width() * .57)) if not self.closed else None)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.tree = ResizableTreeview(left, columns=("file", "count", "reason"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="勾选建议组 / 成员（双击勾选）")
        self.tree.column("#0", width=260, minwidth=130, stretch=False)
        for key, label, width in (("file", "所属文件 / 材质", 210), ("count", "Mesh 数", 65), ("reason", "分组依据", 310)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=50, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Double-1>", self.toggle, add="+")
        edit = ttk.Frame(left, padding=(0, 7))
        edit.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(edit, text="勾选 / 取消所选组", command=self.toggle).pack(side="left")
        ttk.Button(edit, text="改组名", command=self.rename).pack(side="left", padx=4)
        ttk.Button(edit, text="所选成员另建组", command=self.new_group).pack(side="left")
        self.attach_button = ttk.Button(edit, text="关联附件…", command=self.attach_accessories,
                                        state="disabled" if lod_only else "normal")
        self.attach_button.pack(side="left", padx=4)
        move = ttk.Frame(left, padding=(0, 0, 0, 5))
        move.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.target = ttk.Combobox(move, state="readonly", width=25)
        self.target.pack(side="left", fill="x", expand=True)
        ttk.Button(move, text="移入该组", command=self.move).pack(side="left", padx=4)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        view = ttk.Frame(right)
        view.grid(row=0, column=0, sticky="ew")
        self.isolate = tk.BooleanVar(value=False)
        self.compare_candidate = tk.BooleanVar(value=False)
        self.texture = tk.BooleanVar(value=False)
        ttk.Checkbutton(view, text="只看所选", variable=self.isolate, command=self.request_frame).pack(side="left")
        ttk.Checkbutton(view, text="贴图", variable=self.texture, command=self.request_frame).pack(side="left")
        self.lod_view = tk.StringVar(value="高精度")
        lod_controls = ttk.Frame(right)
        lod_controls.grid(row=2, column=0, sticky="ew")
        ttk.Label(lod_controls, text="预览精度").pack(side="left")
        lod_combo = ttk.Combobox(lod_controls, textvariable=self.lod_view, state="readonly", values=tuple(LOD_VIEWS), width=10)
        lod_combo.pack(side="left", padx=3)
        lod_combo.bind("<<ComboboxSelected>>", self.request_frame)
        if sync_lod:
            ttk.Checkbutton(lod_controls, text="同时看候选", variable=self.compare_candidate, command=self.select).pack(side="left", padx=2)
        ttk.Button(view, text="聚焦", command=self.focus).pack(side="right")
        self.canvas = tk.Canvas(right, background="#1c1f26", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", pady=5)
        self.canvas.bind("<Configure>", self.request_frame)
        self.canvas.bind("<ButtonPress-1>", self.mouse_down)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.mouse_up)
        self.canvas.bind("<ButtonPress-2>", self.pan_down)
        self.canvas.bind("<B2-Motion>", self.pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self.pan_up)
        self.canvas.bind("<ButtonPress-3>", self.rotate_down)
        self.canvas.bind("<B3-Motion>", self.rotate_drag)
        self.canvas.bind("<ButtonRelease-3>", self.rotate_up)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Double-Button-2>", self.pivot_at_pointer)
        self.canvas.bind("<KeyPress-space>", self.temporary_color_press)
        self.canvas.bind("<KeyRelease-space>", self.temporary_color_release)
        self.canvas.bind("<FocusOut>", self.temporary_color_release)
        self.feature_text = tk.StringVar(value="左键选择 · 中键平移 · 右键旋转 · 滚轮缩放\n选择成员查看骨骼 / UV 依据；UV 共用不代表同一衣物。")
        ttk.Label(right, textvariable=self.feature_text, wraplength=320).grid(row=3, column=0, sticky="w")
        footer = ttk.Frame(self, padding=10)
        footer.grid(row=3, column=0, sticky="ew")
        self.apply = ttk.Button(footer, text="应用勾选建议", state="disabled", command=self.commit)
        self.apply.pack(side="right")
        ttk.Button(footer, text="取消", command=self.close).pack(side="right", padx=8)
        self._initial_placement = place_large_dialog(self, app)
        self.after_idle(self.analyze)
        self.poll_job = self.after(10, self.poll)

    def options_changed(self, *_):
        self.stop.set()
        self.apply.configure(state="disabled")
        self.summary.configure(text="选项已改变，请点“生成建议”；已显示的建议尚未应用。")

    def analyze(self):
        self.stop.set()
        if self.analysis:
            self.analysis.cancel()
        self.stop = threading.Event()
        self.plan = None
        self.context += 1
        self.pending = False
        self.tree.delete(*self.tree.get_children())
        self.canvas.delete("all")
        self.apply.configure(state="disabled")
        self.summary.configure(text="正在副本中分析几何与空间位置，窗口仍可操作；取消不会修改当前模型…")
        keys = self.app._material_group_scope(self.scope.get())
        if self.attachment_only:
            self.analysis = self.app._executor.submit(self.session.prepare_attachment_review, keys,
                                                     use_features=self.use_features.get(), cancelled=self.stop.is_set)
            return
        if self.sync_lod:
            self.analysis = self.app._executor.submit(self.session.prepare_lod_sync, keys, include_grouped=self.replace.get(),
                                                     use_features=self.use_features.get(), cancelled=self.stop.is_set)
            return
        if self.lod_only:
            keys = {key for key in keys if alternative(self.session.document(key[0]).meshes[key[1]])}
        stop = self.stop
        self.analysis = self.app._executor.submit(self.session.prepare_spatial_groups, keys, split=self.split.get(),
                                                pair=self.pair.get(), distance=self.distance.get(),
                                                include_grouped=self.replace.get(), associate_lod=self.associate_lod.get(), use_features=self.use_features.get(), cancelled=stop.is_set)

    def populate(self):
        old_rows = getattr(self, "_row_records", {})
        selected_names = {old_rows[iid] for iid in self.tree.selection() if iid in old_rows}
        expanded_names = {token for iid, token in old_rows.items() if self.tree.exists(iid) and self.tree.item(iid, "open")}
        self._row_records, self.parent_rows = {}, {}
        self.tree.delete(*self.tree.get_children())
        staged = self.plan["staged"]
        frames = outfit_frames(staged.workspace)
        families = {}
        for index, group in enumerate(self.plan["groups"]):
            if group.get("parent"):
                families.setdefault(group["parent"], []).append(index)
        families = {name: indices for name, indices in families.items() if len(indices) >= 2}
        ordered = group_order(staged.workspace, [(str(i), g["members"]) for i, g in enumerate(self.plan["groups"])])
        display_order, seen = [], set()
        for index_text, _ in ordered:
            index = int(index_text)
            family = families.get(self.plan["groups"][index].get("parent"), [index])
            for member in sorted(family, key=lambda i: self.plan["groups"][i].get("side", 0)):
                if member not in seen:
                    display_order.append((str(member), None))
                    seen.add(member)
        for index_text, _ in display_order:
            index = int(index_text)
            group = self.plan["groups"][index]
            iid = f"g:{index}"
            parent = ""
            if group.get("parent") in families:
                label = group["parent"]
                indices = families[label]
                parent = f"p:{indices[0]}"
                if not self.tree.exists(parent):
                    self.parent_rows[parent] = indices
                    accepted = [self.plan["groups"][i]["accepted"] for i in indices]
                    mark = "☑" if all(accepted) else "☐" if not any(accepted) else "▣"
                    self.tree.insert("", "end", iid=parent, text=f"{mark} {label}", open=("parent", label) in expanded_names,
                                     values=(f"{len(indices)} 个子组", sum(len(self.plan["groups"][i]["members"]) for i in indices), "父组 · 子组可独立选择和分配"))
                    self._row_records[parent] = ("parent", label)
            mark = "☑" if group["accepted"] else "☐"
            documents = {key[0] for key in group["members"]}
            materials = {self.plan["staged"].document(n).meshes[i].material_id for n, i in group["members"]}
            files = Path(next(iter(documents))).name if len(documents) == 1 else f"{len(documents)} 个文件"
            label = child_group_label(group["name"], group["parent"], [self.plan["groups"][i]["name"] for i in families[group["parent"]]]) if parent else group["name"]
            self.tree.insert(parent, "end", iid=iid, text=f"{mark} {label}", open=("group", group["name"]) in expanded_names,
                             values=(f"{files} · 材质 {' / '.join(map(str, sorted(materials)))}", len(group["members"]),
                                     ("待确认 · " if group["review"] else "建议 · ") + group["reason"]))
            self._row_records[iid] = ("group", group["name"])
            for key in member_order(staged.workspace, group["members"]):
                j = group["members"].index(key)
                d = self.plan["staged"].document(key[0])
                original = self.plan["origins"][key][1]
                annotation = self.plan["staged"].annotations.get(key, {})
                evidence = group.get("evidence", {}).get(key, "")
                profile = feature_profile(d.meshes[key[1]])
                detail = f"原 Mesh {original} · {lod_label(d.meshes[key[1]])} · Group {d.meshes[key[1]].visible_condition} · 骨骼 {bone_summary(profile, limit=3)}"
                if evidence:
                    detail += " · " + evidence
                hint = position_hint(d, d.meshes[key[1]], frames[d.outfit_id])
                self.tree.insert(iid, "end", iid=f"m:{index}:{j}", text=f"Mesh {key[1]} · {annotation.get('name') or hint}",
                                 values=(Path(key[0]).name, 1, detail))
        self.target.configure(values=[g["name"] for g in self.plan["groups"]])
        if self.plan["groups"]:
            self.target.current(0)
        selected_rows = [iid for iid, token in self._row_records.items() if token in selected_names]
        if selected_rows:
            self.tree.selection_set(selected_rows)
        accepted = sum(g["accepted"] for g in self.plan["groups"])
        pending = sum(len(g["members"]) for g in self.plan["groups"] if not g["accepted"])
        profiles = [feature_profile(self.plan["staged"].document(n).meshes[i]) for n, i in self.plan["eligible"]]
        feature_summary = f" · 辅助开启：骨骼可读 {sum(p['bones'] is not None for p in profiles)} / UV 范围可读 {sum(p['uv'] is not None for p in profiles)}" if self.plan["use_features"] and not self.lod_only else ""
        self.summary.configure(text=f"拟拆分 {self.plan['split_count']} 个原 Mesh · 分析 {len(self.plan['eligible'])} 个部件 · 勾选 {accepted} 个独立组 · {len(families)} 个父组 · 未勾选 {pending} 个部件 · 跳过已有组 {self.plan['skipped']} 个"
                                    + f" · LOD 关联 {self.plan['lod_families']} 组（纳入 {self.plan['lod_added']} 个对应版本）"
                                    + feature_summary
                                    + ("\n" + "；".join(self.plan["warnings"][:2]) if self.plan["warnings"] else "")
                                    + "\n应用后未勾选部件仍独立保留；不修改 Group ID。Ctrl+Z 一次撤销拆分与成组。")
        if self.sync_lod:
            self.summary.configure(text=f"拟重组 {self.plan['split_count']} 个低精度 Mesh · {self.plan['lod_families']} 个高精度控制组 · 待确认 {pending} 块"
                                   + "\n请选择待确认块，按候选预览核对并移入高精度组；全组确认后应用。Group ID 保持，Ctrl+Z 整批撤销。")
        if self.attachment_only:
            self.summary.configure(text=f"保留 {len(self.plan['eligible'])} 个已有部件 · 勾选 {accepted} 个组。未确认的原组保持。"
                                   "\n请选择主体后点“关联附件”；确认后再应用。Ctrl+Z 可撤销。")
        self.apply.configure(state="normal" if accepted and not self.stop.is_set() else "disabled")

    def selected_keys(self):
        if not self.plan:
            return set()
        keys = set()
        for iid in self.tree.selection():
            if iid in self.parent_rows:
                keys.update(key for index in self.parent_rows[iid] for key in self.plan["groups"][index]["members"])
                continue
            fields = iid.split(":")
            group = self.plan["groups"][int(fields[1])]
            keys.update(group["members"] if fields[0] == "g" else [group["members"][int(fields[2])]])
        return keys

    def select(self, *_):
        self.renderer.selected_keys = self.selected_keys()
        self.renderer.comparison_keys = set()
        keys = self.renderer.selected_keys
        if self.sync_lod and self.plan:
            selected_groups = [self.plan["groups"][int(iid.split(":")[1])] for iid in self.tree.selection()]
            candidate = next((g.get("candidate_reference") for g in selected_groups if g.get("candidate_reference")), None)
            target = next((g for g in self.plan["groups"] if g.get("reference") == candidate), None)
            if target:
                self.target.set(target["name"])
            if self.compare_candidate.get() and target:
                self.renderer.comparison_keys = {target["reference_key"]}
        details = []
        for n, i in sorted(keys)[:2]:
            profile = feature_profile(self.plan["staged"].document(n).meshes[i])
            details.append(f"Mesh {i} · 骨骼 {bone_summary(profile, limit=3)}")
            if profile["uv"] is not None and len(keys) == 1:
                lo, hi = profile["uv"]
                details.append(f"UV：U {lo[0]:.3f}–{hi[0]:.3f} / V {lo[1]:.3f}–{hi[1]:.3f}")
        self.feature_text.set("\n".join(details) if details else "选择成员查看骨骼 / UV 依据；UV 共用不代表同一衣物。")
        if keys and not any(visible_lod(self.plan["staged"].document(n).meshes[i], LOD_VIEWS[self.lod_view.get()]) for n, i in keys):
            self.lod_view.set(next((label for label, bit in LOD_VIEWS.items() if bit is not None
                                  and any(visible_lod(self.plan["staged"].document(n).meshes[i], bit) for n, i in keys)), "全部版本"))
        self.request_frame()

    def toggle(self, event=None):
        if not self.plan or event and self.tree.identify_region(event.x, event.y) in ("heading", "separator"):
            return
        parent_indices = {i for iid in self.tree.selection() for i in self.parent_rows.get(iid, [])}
        indices = {int(iid.split(":")[1]) for iid in self.tree.selection() if iid.startswith("g:")} - parent_indices
        for i in indices:
            self.plan["groups"][i]["accepted"] = not self.plan["groups"][i]["accepted"]
        if parent_indices:
            value = not all(self.plan["groups"][i]["accepted"] for i in parent_indices)
            for i in parent_indices:
                self.plan["groups"][i]["accepted"] = value
        if indices or parent_indices:
            self.populate()

    def rename(self):
        if not self.plan or not self.tree.selection():
            return
        row = self.tree.selection()[0]
        if row in self.parent_rows:
            indices = self.parent_rows[row]
            label = self.plan["groups"][indices[0]]["parent"]
            value = simpledialog.askstring("父组名称", "给父组命名（保留子组）：", initialvalue=label, parent=self)
            if value is None:
                return
            value = value.strip()
            occupied = {g["name"] for g in self.plan["groups"]} | {g.get("parent") for g in self.plan["groups"] if g.get("parent") != label}
            if not value or len(value) > 80 or any(ord(c) < 32 for c in value) or value in occupied or value in self.session.all_groups():
                messagebox.showerror("名称无效", "请输入不重复的 1–80 个可显示字符。", parent=self)
                return
            for i in indices:
                self.plan["groups"][i]["parent"] = value
            self.populate()
            self.tree.selection_set(row)
            return
        index = int(row.split(":")[1])
        value = simpledialog.askstring("建议组名称", "给所选建议组命名：", initialvalue=self.plan["groups"][index]["name"], parent=self)
        if value is not None:
            value = value.strip()
            if not value or len(value) > 80 or any(ord(c) < 32 for c in value) or value in {g.get("parent") for g in self.plan["groups"]} or any(i != index and g["name"] == value for i, g in enumerate(self.plan["groups"])):
                messagebox.showerror("名称无效", "请输入不重复的 1–80 个可显示字符。", parent=self)
                return
            self.plan["groups"][index]["name"] = value
            self.populate()
            self.tree.selection_set(f"g:{index}")

    def transfer(self, keys, target):
        opened = {token for iid, token in self._row_records.items() if self.tree.item(iid, "open")}
        for group in self.plan["groups"]:
            if keys.intersection(group["members"]):
                group["reason"] = "已手动调整成员"
            group["members"] = [key for key in group["members"] if key not in keys]
        target["reason"] = "已手动调整成员"
        target["members"].extend(sorted(keys))
        self.plan["groups"] = [g for g in self.plan["groups"] if g["members"]]
        self.populate()
        self.tree.selection_set(f"g:{self.plan['groups'].index(target)}")
        self.tree.see(f"g:{self.plan['groups'].index(target)}")
        for iid, token in self._row_records.items():
            self.tree.item(iid, open=token in opened)
        self.target.set(target["name"])
        self.select()

    def move(self):
        keys = self.selected_keys()
        if keys and self.target.current() >= 0:
            self.transfer(keys, self.plan["groups"][self.target.current()])

    def new_group(self):
        keys = self.selected_keys()
        if not keys:
            return
        value = simpledialog.askstring("另建建议组", "将所选成员单独成组，组名：", parent=self)
        if value is None:
            return
        value = value.strip()
        if not value or len(value) > 80 or any(ord(c) < 32 for c in value) or value in self.session.all_groups() or any(g["name"] == value or g.get("parent") == value for g in self.plan["groups"]):
            messagebox.showerror("名称无效", "请输入不重复的 1–80 个可显示字符。", parent=self)
            return
        first = next(iter(keys))
        d = self.plan["staged"].document(first[0])
        group = {"name": value, "document": first[0], "material_id": d.meshes[first[1]].material_id,
                 "members": [], "reason": "手动调整成员", "review": False, "accepted": True}
        self.plan["groups"].append(group)
        self.transfer(keys, group)

    def attach_accessories(self):
        if not self.plan or self.lod_only or self.stop.is_set():
            return
        if not self.selected_keys():
            messagebox.showinfo("请选择主体", "先选择一个主体部件或同组成员，再建议关联附件。", parent=self)
            return
        from attachment_dialog import AttachmentDialog
        current = getattr(self, "_attachment_dialog", None)
        if current is not None and current.winfo_exists():
            current.lift()
            return
        self._attachment_dialog = AttachmentDialog(self)

    def focus(self):
        if self.plan:
            self.renderer.fit(self.plan["staged"].workspace, (self.selected_keys() | self.renderer.comparison_keys) or self.plan["eligible"])
            self.request_frame()

    def request_frame(self, event=None, interactive=False):
        if self.closed or not self.plan:
            return
        if not interactive:
            self.context += 1
        self.renderer.isolate_selected = self.isolate.get() or bool(self.renderer.comparison_keys)
        self.renderer.lod_bit = None if self.renderer.comparison_keys else LOD_VIEWS[self.lod_view.get()]
        self.renderer.color_mode = "texture" if self.texture.get() and not self.temporary_mesh_colors else "mesh"
        self.pending = True

    def mouse_down(self, event):
        self.canvas.focus_set()
        self.drag_start = (event.x, event.y)
        self.dragged = False

    def drag(self, event):
        start = getattr(self, "drag_start", None)
        if start and max(abs(event.x - start[0]), abs(event.y - start[1])) > 4:
            self.dragged = True

    def mouse_up(self, event):
        start = getattr(self, "drag_start", None)
        self.drag_start = None
        if (not start or self.dragged or not self.plan
                or max(abs(event.x - start[0]), abs(event.y - start[1])) > 4):
            return
        if alt_pressed(event.state):
            key = self.renderer.pick_through(event.x, event.y)
        else:
            self.renderer._through_pick = None
            key = self.renderer.pick(event.x, event.y)
        if key is None:
            return
        rows = {member: f"m:{gi}:{mi}" for gi, group in enumerate(self.plan["groups"])
                for mi, member in enumerate(group["members"])}
        if key not in rows:
            return
        selected = self.selected_keys() if event.state & (0x4 | 0x1) else set()
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        self.tree.selection_set([rows[k] for k in selected if k in rows])
        self.tree.see(rows[key])
        self.select()

    def rotate_down(self, event):
        self.canvas.focus_set()
        self.drag_start = None
        self.rotate_start = (event.x, event.y)

    def rotate_drag(self, event):
        start = getattr(self, "rotate_start", None)
        if start:
            self.renderer.yaw += (event.x - start[0]) * .012
            self.renderer.pitch = max(-1.45, min(1.45, self.renderer.pitch + (event.y - start[1]) * .012))
            self.rotate_start = (event.x, event.y)
            self.interaction_until = time.monotonic() + .16
            self.request_frame(interactive=True)

    def rotate_up(self, _event):
        self.rotate_start = None
        self.interaction_until = 0
        self.request_frame()

    def pan_down(self, event):
        self.canvas.focus_set()
        self.drag_start = None
        self.pan_start = (event.x, event.y)

    def pan_drag(self, event):
        start = getattr(self, "pan_start", None)
        if start:
            self.renderer.pan_x += (event.x - start[0]) / max(1, self.canvas.winfo_width())
            self.renderer.pan_y += (event.y - start[1]) / max(1, self.canvas.winfo_height())
            self.pan_start = (event.x, event.y)
            self.interaction_until = time.monotonic() + .16
            self.request_frame(interactive=True)

    def pan_up(self, _event):
        self.pan_start = None
        self.interaction_until = 0
        self.request_frame()

    def wheel(self, event):
        if not event.delta:
            return
        self.canvas.focus_set()
        self.renderer.zoom_at(1.1 if event.delta > 0 else .9,
                              (self.canvas.winfo_width(), self.canvas.winfo_height()), (event.x, event.y))
        self.interaction_until = time.monotonic() + .16
        self.request_frame(interactive=True)

    def pivot_at_pointer(self, event):
        self.canvas.focus_set()
        self.pan_start = None
        key = self.renderer.pick(event.x, event.y)
        if key and self.plan:
            keys = self.selected_keys() if key in self.selected_keys() else {key}
            if self.renderer.pivot_on(self.plan["staged"].workspace, keys,
                                       (self.canvas.winfo_width(), self.canvas.winfo_height())):
                self.request_frame()
        return "break"

    def temporary_color_press(self, event):
        if event.state & 0x4 or alt_pressed(event.state) or not self.plan:
            return
        if not self.temporary_mesh_colors:
            self.temporary_mesh_colors = True
            self.request_frame()
        return "break"

    def temporary_color_release(self, _event=None):
        if self.temporary_mesh_colors:
            self.temporary_mesh_colors = False
            self.request_frame()

    def poll(self):
        self.poll_job = None
        if self.closed:
            return
        if self.analysis and self.analysis.done():
            future, self.analysis = self.analysis, None
            try:
                result = future.result()
                if not self.stop.is_set():
                    self.plan = result
                    self.renderer._normals = {}
                    self.populate()
                    self.renderer.hidden_keys = {(d.name, m.index) for d, m in result["staged"].workspace.all_meshes()} - result["eligible"]
                    self.renderer.selected_keys = set()
                    self.focus()
                    if self.tree.get_children():
                        self.tree.selection_set(self.tree.get_children()[0])
                    if self.attachment_only:
                        rows = [f"m:{gi}:{mi}" for gi, group in enumerate(result["groups"])
                                for mi, key in enumerate(group["members"]) if key in self.attachment_seeds]
                        self.tree.selection_set(rows)
                        self.select()
                        self.after_idle(self.attach_accessories)
                    if result["warnings"]:
                        messagebox.showwarning("部分 Mesh 保持整体", "\n".join(result["warnings"][:15]), parent=self)
            except Exception as exc:
                if not self.stop.is_set():
                    self.summary.configure(text=f"无法分析：{exc}")
        if self.frame and self.frame[0].done():
            future, context, renderer = self.frame
            self.frame = None
            try:
                image = future.result()
                if context == self.context and self.plan:
                    for name in ("_pixel_ids", "_pick_scale", "_polygons", "_face_keys", "_face_ids", "_pick_order", "_depths",
                                 "_triangle_depths", "_pick_alpha", "selection_occluded"):
                        setattr(self.renderer, name, getattr(renderer, name))
                    self.renderer._pick_cache = None
                    self.last_image = image
                    self.photo = ImageTk.PhotoImage(image)
                    self.canvas.delete("all")
                    self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            except Exception as exc:
                self.summary.configure(text=f"预览失败：{exc}")
        if self.interaction_until and time.monotonic() > self.interaction_until:
            self.interaction_until = 0
            self.request_frame()
        if self.pending and self.plan and self.frame is None:
            self.pending = False
            renderer = copy.copy(self.renderer)
            future = self.app._executor.submit(renderer.render_frame, self.plan["staged"].workspace,
                                              (self.canvas.winfo_width(), self.canvas.winfo_height()), bool(self.interaction_until))
            self.frame = (future, self.context, renderer)
        self.poll_job = self.after(10, self.poll)

    def commit(self):
        if self.plan is None or self.stop.is_set():
            return
        try:
            count = self.session.apply_spatial_groups(self.plan)
            old_hidden = self.app.renderer.hidden_keys
            hidden_after = {key for key, original in self.plan["origins"].items() if original in old_hidden}
            if count:
                entry = self.session._undo[-1]
                entry["preview_hidden_before"] = old_hidden.copy()
                entry["preview_hidden_after"] = hidden_after.copy()
            names = [g["name"] for g in self.plan["groups"] if g["accepted"]]
            self.close()
            self.app.list_mode.set("按部件组")
            self.app.group_search.set("")
            self.app.named_group.set(names[0])
            self.app._refresh_groups(hidden_keys=hidden_after)
            self.app.select_named_group()
            kind = "主体与附件" if self.attachment_only else "LOD 同步" if self.sync_lod else "空间建议"
            self.app.status.set(f"已应用 {count} 个{kind}组 · Group ID 保持 · Ctrl+Z 整批撤销")
        except Exception as exc:
            messagebox.showerror("无法应用建议", str(exc), parent=self)

    def close(self):
        self.closed = True
        attachment = getattr(self, "_attachment_dialog", None)
        if attachment is not None and attachment.winfo_exists():
            attachment.close()
        self.stop.set()
        for job in (self.poll_job, self.draw_job):
            if job:
                self.after_cancel(job)
        if self.analysis:
            self.analysis.cancel()
        if self.frame:
            self.frame[0].cancel()
        self.destroy()
