"""Stage a variable number of health rules before applying them to the editor."""
import tkinter as tk
from tkinter import ttk
from health_rules import DEFAULT_THRESHOLDS, validate_thresholds
from scroll_panel import ScrollPanel
from dialog_geometry import center_dialog


class HealthThresholdDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.rows = app, []
        self.title("自定义隐藏阈值 · 增加或移除层级")
        self.geometry("600x460")
        self.minsize(560, 400)
        self.transient(app)
        self.grab_set()
        ttk.Label(self, text="血量 ≤ 阈值时隐藏，高于阈值时恢复。\n增加层级后，将部件分配到对应 Group；Group 0 保留为基础层。",
                  padding=12, wraplength=540).pack(fill="x")
        ttk.Label(self, text="层级              Group ID               隐藏阈值（%）", padding=(12, 0)).pack(anchor="w")
        self.body = ScrollPanel(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.error = ttk.Label(self, foreground="#b42b32", wraplength=530, padding=(12, 4))
        self.error.pack(fill="x")
        footer = ttk.Frame(self, padding=12)
        footer.pack(fill="x")
        self.add_button = ttk.Button(footer, text="增加层级", command=self.add_layer)
        self.add_button.pack(side="left")
        self.defaults_button = ttk.Button(footer, text="默认 80 / 60 / 40 / 20", command=self.defaults)
        self.defaults_button.pack(side="left", padx=6)
        self.apply_button = ttk.Button(footer, text="应用", command=self.apply)
        self.apply_button.pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=6)
        values = app.session.health_thresholds
        for group in sorted(values, key=lambda g: (-values[g], g)):
            self.add_row(group, values[group])
        self.bind("<Return>", lambda _e: self.apply())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._initial_placement = center_dialog(self, app)

    def add_row(self, group, hp):
        row = {"group": tk.StringVar(self, value=str(group)), "hp": tk.StringVar(self, value=str(hp))}
        frame = row["frame"] = ttk.Frame(self.body.content)
        frame.pack(fill="x", pady=4)
        row["label"] = ttk.Label(frame, text=f"第 {len(self.rows) + 1} 层", width=8)
        row["label"].pack(side="left")
        ttk.Entry(frame, textvariable=row["group"], width=9).pack(side="left", padx=(0, 22))
        ttk.Spinbox(frame, from_=0, to=100, textvariable=row["hp"], width=9).pack(side="left", padx=(0, 15))
        ttk.Button(frame, text="移除", width=6, command=lambda: self.remove(row)).pack(side="right")
        self.rows.append(row)
        self.body.bind_wheel()

    def add_layer(self):
        if len(self.rows) >= 32:
            self.error.configure(text="最多保留 32 个血量层级。")
            return
        groups, thresholds = [], []
        for row in self.rows:
            try:
                groups.append(int(row["group"].get()))
                thresholds.append(int(row["hp"].get()))
            except ValueError:
                pass
        group = max([62, *groups]) + 1
        if group > 65535:
            group = next(g for g in range(63, 65536) if g not in groups)
        self.add_row(group, max(0, min(thresholds, default=100) - 20))
        self.error.configure(text="")

    def remove(self, row):
        if len(self.rows) == 1:
            self.error.configure(text="请至少保留一个血量层级。")
            return
        row["frame"].destroy()
        self.rows.remove(row)
        for i, item in enumerate(self.rows):
            item["label"].configure(text=f"第 {i + 1} 层")

    def defaults(self):
        for row in self.rows:
            row["frame"].destroy()
        self.rows.clear()
        for group, hp in DEFAULT_THRESHOLDS.items():
            self.add_row(group, hp)
        self.error.configure(text="")

    def apply(self):
        try:
            values = {}
            for row in self.rows:
                group, hp = int(row["group"].get()), int(row["hp"].get())
                if group in values:
                    raise ValueError("每个层级的 Group ID 必须不同")
                values[group] = hp
            self.app.session.set_health_thresholds(validate_thresholds(values))
        except ValueError as exc:
            self.error.configure(text=str(exc) if "Group" in str(exc) or "层级" in str(exc) or "百分比" in str(exc) else "Group 和阈值请输入整数。")
            return
        self.destroy()
        self.app._refresh_groups()
        self.app.schedule_redraw()
        self.app.status.set("隐藏规则已更新 · 分配按钮、预览和规则导出同步 · 请更新游戏插件 INI")
