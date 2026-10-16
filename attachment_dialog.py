"""Review accessory candidates; changes stay in the staged split plan."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from attachment_groups import propose_attachments
from mod3 import Mod3Error
from ui_tables import ResizableTreeview
from dialog_geometry import center_dialog


class AttachmentDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent, self.plan = parent, parent.plan
        self.seeds = parent.selected_keys().copy()
        self.target_group = next((g for g in self.plan["groups"] if self.seeds <= set(g["members"])), None)
        self.base = set(self.target_group["members"]) if self.target_group else self.seeds.copy()
        self.candidates, self.checked, self.result = [], set(), None
        self.stop, self.future, self.job, self.closed = threading.Event(), None, None, False
        self.title("指定主体 · 预览关联附件")
        self.geometry("820x560")
        self.minsize(740, 450)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _: self.close())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        fields = ttk.Frame(self, padding=10)
        fields.grid(row=0, column=0, sticky="ew")
        ttk.Label(fields, text=f"以选中 {len(self.seeds)} 个部件为距离基准 · 主体组已有 {len(self.base)} 个成员").grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(fields, text="关联范围").grid(row=1, column=0, sticky="w", pady=6)
        self.percent = tk.StringVar(value="1.5")
        self.radius = ttk.Spinbox(fields, from_=.1, to=5, increment=.1, textvariable=self.percent, width=6)
        self.radius.grid(row=1, column=1, padx=5)
        ttk.Label(fields, text="% 模型高度").grid(row=1, column=2)
        self.materials = tk.BooleanVar(value=False)
        ttk.Checkbutton(fields, text="含其他材质（需确认）", variable=self.materials, command=self.mark_stale).grid(row=1, column=3, padx=10)
        self.refresh = ttk.Button(fields, text="更新建议", command=self.generate)
        self.refresh.grid(row=1, column=4)
        ttk.Label(fields, text="目标组名").grid(row=2, column=0, sticky="w")
        self.name = tk.StringVar(value=self.target_group["name"] if self.target_group else self._new_name())
        ttk.Entry(fields, textvariable=self.name).grid(row=2, column=1, columnspan=4, sticky="ew", padx=5)
        fields.columnconfigure(3, weight=1)
        self.status = tk.StringVar(value="准备分析…")
        ttk.Label(self, textvariable=self.status, wraplength=780, padding=(10, 0, 10, 8)).grid(row=1, column=0, sticky="ew")
        table = ttk.Frame(self, padding=(10, 0))
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ResizableTreeview(table, columns=("distance", "material", "reason"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="勾选附件（双击切换；单击在后方预览）")
        self.tree.column("#0", width=275, minwidth=150, stretch=False)
        for key, label, width in (("distance", "表面距离", 85), ("material", "材质", 65), ("reason", "建议依据", 450)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self.preview)
        self.tree.bind("<Double-1>", self.toggle)
        tools = ttk.Frame(self, padding=10)
        tools.grid(row=3, column=0, sticky="ew")
        ttk.Button(tools, text="勾选 / 取消所选", command=self.toggle).pack(side="left")
        ttk.Button(tools, text="仅勾选推荐", command=self.recommended).pack(side="left", padx=5)
        ttk.Button(tools, text="全部勾选", command=self.check_all).pack(side="left", padx=(0, 5))
        ttk.Button(tools, text="全部取消", command=self.clear).pack(side="left")
        footer = ttk.Frame(self, padding=(10, 0, 10, 10))
        footer.grid(row=4, column=0, sticky="ew")
        ttk.Label(footer, text="只调整当前拆分建议；返回后仍需点击“应用勾选建议”。").pack(side="left")
        self.confirm = ttk.Button(footer, text="确认加入建议组", command=self.commit, state="disabled")
        self.confirm.pack(side="right")
        ttk.Button(footer, text="取消", command=self.close).pack(side="right", padx=5)
        self.percent.trace_add("write", self.mark_stale)
        self._initial_placement = center_dialog(self, parent)
        self.after_idle(self.generate)

    def _new_name(self):
        occupied = {g["name"] for g in self.plan["groups"]} | {g.get("parent") for g in self.plan["groups"]} | set(self.parent.session.all_groups())
        index = 1
        while f"主体与附件 {index}" in occupied:
            index += 1
        return f"主体与附件 {index}"

    def mark_stale(self, *_):
        self.stop.set()
        self.result = None
        self.checked.clear()
        self.tree.delete(*self.tree.get_children())
        self.confirm.configure(state="disabled")
        self.status.set("范围已改变，请更新建议。原组及成员尚未改变。")
        self.preview()

    def generate(self):
        if self.closed:
            return
        self.mark_stale()
        try:
            percent = float(self.percent.get())
        except ValueError:
            self.status.set("关联范围请输入 0.1–5 之间的数字。")
            return
        if not .1 <= percent <= 5:
            self.status.set("关联范围请输入 0.1–5 之间的数字。")
            return
        if self.job:
            self.after_cancel(self.job)
        if self.future:
            self.future.cancel()
        self.stop = threading.Event()
        keys = self.plan["eligible"] - (self.base - self.seeds)
        self.status.set("正在计算原主体到附件的表面距离…不会沿附件继续扩张；可随时取消。")
        self.future = self.parent.app._executor.submit(propose_attachments, self.plan["staged"].workspace,
                        self.seeds, keys, percent=percent, other_materials=self.materials.get(),
                        use_features=self.parent.use_features.get(), cancelled=self.stop.is_set)
        self.job = self.after(40, self.poll)

    def poll(self):
        self.job = None
        if self.closed:
            return
        if not self.future.done():
            self.job = self.after(40, self.poll)
            return
        try:
            result = self.future.result()
            if self.stop.is_set():
                return
            self.result = result
            self.candidates = result["candidates"]
            self.checked = {row["key"] for row in self.candidates if row["recommended"]}
            for i, row in enumerate(self.candidates):
                key = row["key"]
                name = self.plan["staged"].annotations.get(key, {}).get("name") or f"Mesh {key[1]}"
                self.tree.insert("", "end", iid=str(i), text=name,
                                 values=(f"{row['distance']:.3g}", row["material_id"],
                                         ("推荐 · " if row["recommended"] else "待确认 · ") + row["reason"]))
            self.update_checks()
        except Exception as exc:
            if not self.stop.is_set():
                self.status.set(f"无法生成附件建议：{exc}")

    def update_checks(self):
        for i, row in enumerate(self.candidates):
            if self.tree.exists(str(i)):
                name = self.plan["staged"].annotations.get(row["key"], {}).get("name") or f"Mesh {row['key'][1]}"
                self.tree.item(str(i), text=("☑ " if row["key"] in self.checked else "☐ ") + name)
        if self.result:
            self.status.set(f"找到 {len(self.candidates)} 个候选 · 已勾选 {len(self.checked)} 个 · 范围 {self.result['limit']:.3g} 模型单位。"
                            "默认勾选有骨骼支持的小附件；其余需确认。后方黄色轮廓为主体、已勾选及当前预览候选。")
        self.confirm.configure(state="normal" if self.result and self.checked else "disabled")
        self.preview()

    def preview(self, *_):
        if self.closed or self.parent.closed:
            return
        focused = {self.candidates[int(i)]["key"] for i in self.tree.selection()}
        self.parent.renderer.selected_keys = self.seeds | self.checked | focused
        self.parent.request_frame()

    def toggle(self, *_):
        for iid in self.tree.selection():
            key = self.candidates[int(iid)]["key"]
            self.checked.symmetric_difference_update({key})
        self.update_checks()
        return "break"

    def recommended(self):
        self.checked = {row["key"] for row in self.candidates if row["recommended"]} if self.result else set()
        self.update_checks()

    def clear(self):
        self.checked.clear()
        self.update_checks()

    def check_all(self):
        self.checked = {row["key"] for row in self.candidates} if self.result else set()
        self.update_checks()

    def commit(self):
        if not self.result or not self.checked:
            return
        try:
            if self.parent.plan is not self.plan or self.parent.stop.is_set():
                raise Mod3Error("拆分建议已经变化，请重新生成附件建议")
            if (not self.checked <= {row["key"] for row in self.candidates}
                    or not (self.seeds | self.checked) <= self.plan["eligible"]):
                raise Mod3Error("候选已经变化，请重新生成附件建议")
            name = self.name.get().strip()
            occupied = {g["name"] for g in self.plan["groups"] if g is not self.target_group} | {g.get("parent") for g in self.plan["groups"]} | set(self.parent.session.parent_groups)
            if (not name or len(name) > 80 or any(ord(c) < 32 for c in name) or name in occupied
                    or (name in self.parent.session.named_groups and (not self.target_group or name != self.target_group["name"]))):
                raise Mod3Error("组名请输入不重复的 1–80 个可显示字符")
            target = self.target_group
            if target is None:
                key = next(iter(self.seeds))
                mesh = self.plan["staged"].document(key[0]).meshes[key[1]]
                target = {"name": name, "document": key[0], "material_id": mesh.material_id, "members": [], "reason": "", "review": False, "accepted": True}
                self.plan["groups"].append(target)
            target["name"], target["accepted"], target["review"] = name, True, False
            self.parent.transfer(self.seeds | self.checked, target)
            target["reason"] = "用户确认：以所选主体按表面距离关联附件；未沿附件扩张"
            target.setdefault("evidence", {}).update({row["key"]: row["reason"] for row in self.candidates if row["key"] in self.checked})
            self.parent.populate()
            self.close()
        except (Mod3Error, ValueError) as exc:
            messagebox.showerror("无法加入附件", str(exc), parent=self)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        if self.job:
            self.after_cancel(self.job)
        if self.future:
            self.future.cancel()
        self.destroy()
        if not self.parent.closed:
            self.parent.grab_set()
            self.parent.select()
