"""Choose a model mod from a remembered library, without changing Box state."""
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from mod_library import scan_library, filter_mods, save_library_root, library_snapshot
from ui_tables import ResizableTreeview
from dialog_geometry import center_dialog


class ModLibraryDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.closed = False
        self.records = []
        self.rows = {}
        self._items = {}
        self._next_item = 0
        self._record_root = None
        if not hasattr(app, "_mod_library_cache"):
            app._mod_library_cache = {}
        self.cache = app._mod_library_cache
        self.future = None
        self.progress_events = queue.SimpleQueue()
        self.scan_progress = {}
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mod-library")
        self.poll_job = None
        self.title("模组库 · 选择要编辑的模组")
        self.geometry("1000x630")
        self.minsize(760, 480)
        self.transient(app)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        row = ttk.Frame(self, padding=10)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="模组库路径").grid(row=0, column=0, padx=(0, 8))
        self.directory = tk.StringVar(value=app.mod_library_directory)
        self.path_entry = ttk.Entry(row, textvariable=self.directory)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<Return>", lambda e: self.refresh())
        ttk.Button(row, text="浏览…", command=self.browse).grid(row=0, column=2, padx=5)
        self.refresh_button = ttk.Button(row, text="读取 / 刷新", command=self.refresh)
        self.refresh_button.grid(row=0, column=3)
        ttk.Style(self).configure("LibraryScan.TLabel", background="#e5eef8", foreground="#164b76",
                                  font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        scan_row = ttk.Frame(self, padding=(10, 0))
        scan_row.grid(row=1, column=0, sticky="ew")
        scan_row.columnconfigure(0, weight=1)
        self.scan_text = tk.StringVar(value="首次读取会建立索引；以后只重新读取新增或变化的模组。")
        self.scan_label = ttk.Label(scan_row, textvariable=self.scan_text, style="LibraryScan.TLabel")
        self.scan_label.grid(row=0, column=0, sticky="ew")
        self.scan_bar = ttk.Progressbar(scan_row, length=150, mode="determinate")
        self.scan_bar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        scan_row.bind("<Configure>", lambda e: self.scan_label.configure(wraplength=max(200, e.width - 180)))
        search = ttk.Frame(self, padding=10)
        search.grid(row=2, column=0, sticky="ew")
        self.query = tk.StringVar()
        ttk.Label(search, text="搜索名称 / 路径").pack(side="left")
        self.search_entry = ttk.Entry(search, textvariable=self.query)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.query.trace_add("write", lambda *_: self.populate())
        ttk.Button(search, text="清除", command=lambda: self.query.set("")).pack(side="right")
        frame = ttk.Frame(self, padding=(10, 0))
        frame.grid(row=3, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ResizableTreeview(frame, columns=("kind", "count", "state", "path"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="模组名称")
        self.tree.column("#0", width=340, minwidth=160, stretch=False)
        for key, title, width in (("kind", "类型", 70), ("count", "MOD3 数", 85), ("state", "盒子状态", 100), ("path", "库内位置", 300)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=55, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Return>", self.open_selected)
        self.tree.bind("<Double-1>", self.open_selected)
        self.detail = tk.StringVar(value="选择一个模组，双击或点击“打开所选”。")
        self.summary = tk.StringVar(value="请设置模组库路径。")
        footer = ttk.Frame(self, padding=10)
        footer.grid(row=4, column=0, sticky="ew")
        ttk.Label(footer, textvariable=self.detail, wraplength=940, style="Hint.TLabel").pack(anchor="w")
        actions = ttk.Frame(footer)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Label(actions, textvariable=self.summary).pack(side="left")
        self.open_button = ttk.Button(actions, text="打开所选", command=self.open_selected, state="disabled")
        self.open_button.pack(side="right")
        ttk.Button(actions, text="关闭", command=self.close).pack(side="right", padx=8)
        self._initial_placement = center_dialog(self, app)
        self.poll_job = self.after(80, self.poll)
        if self.directory.get():
            self.after_idle(self.refresh)

    def browse(self):
        path = filedialog.askdirectory(parent=self, title="选择 MHW Box 或自定义模组库文件夹", initialdir=self.directory.get() or None)
        if path:
            self.directory.set(path)
            self.refresh()

    def refresh(self):
        if self.closed:
            return
        path = self.directory.get().strip().strip('"')
        if not path:
            self.summary.set("请先填写或浏览选择模组库路径。")
            return
        self.stop.set()
        if self.future:
            self.future.cancel()
        stop = self.stop = threading.Event()
        self.progress_events = queue.SimpleQueue()
        self.scan_progress = {}
        self.scan_text.set("正在检查目录索引 · 首次读取需扫描文件，请稍候…")
        self.scan_label.configure(foreground="#164b76")
        self.scan_bar.configure(mode="indeterminate")
        self.scan_bar.start(20)
        self.future = self.executor.submit(scan_library, path, stop.is_set, self.progress_events.put)
        key = self._cache_key(path)
        cached = self.cache.get(key)
        if cached is None:
            cached = library_snapshot(path)
        if cached is not None:
            self.records = list(cached)
        elif key != self._record_root:
            self.records = []
        self._record_root = key
        self.populate()
        if self.records:
            self.scan_text.set("已显示上次索引 · 正在后台检查新增或变化的模组…")
        else:
            self.summary.set("正在读取模组名称与文件列表…")

    @staticmethod
    def _cache_key(path):
        return str(Path(path).expanduser().absolute()).casefold()

    def poll(self):
        if self.closed:
            return
        latest = None
        while True:
            try:
                latest = self.progress_events.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.scan_progress = latest
            self.scan_bar.stop()
            self.scan_bar.configure(mode="determinate", maximum=max(1, latest["total"]), value=latest["done"])
            self.scan_text.set(f"正在检查变化 {latest['done']} / {latest['total']} · 复用 {latest['reused']} · 重新读取 {latest['scanned']} · {latest['name']}")
        if self.future and self.future.done():
            future, self.future = self.future, None
            self.scan_bar.stop()
            try:
                root, records, errors = future.result()
                # Only persist a valid readable location after scanning succeeds.
                save_library_root(root)
                self.app.mod_library_directory = str(root)
                self.directory.set(str(root))
                self.records = records
                key = self._cache_key(root)
                self.cache[key] = tuple(records)
                if len(self.cache) > 4:
                    del self.cache[next(iter(self.cache))]
                self._record_root = key
                self.populate()
                self.scan_bar.configure(mode="determinate", maximum=1, value=1)
                self.scan_text.set(f"读取完成 · {len(records)} 个模组 · 复用 {self.scan_progress.get('reused', 0)} 项 · 新增或变化 {self.scan_progress.get('scanned', 0)} 项")
                self.scan_label.configure(foreground="#236331")
                if errors:
                    self.summary.set(self.summary.get() + f" · {len(errors)} 项未读取")
                    self.detail.set("；".join(errors[:2]))
            except (OSError, ValueError) as exc:
                self.scan_text.set("读取失败 · 请检查路径或下方提示后重试")
                self.scan_label.configure(foreground="#9b2c2c")
                self.scan_bar.configure(mode="determinate", maximum=1, value=0)
                self.summary.set("模组库未读取；可修改路径后重试。")
                self.detail.set(str(exc))
        self.poll_job = self.after(80, self.poll)

    def populate(self):
        if self.closed:
            return
        selected = self.selected()
        paths = {record.path for record in self.records}
        for path in list(self._items):
            if path not in paths:
                iid, _ = self._items.pop(path)
                self.tree.delete(iid)
        records = filter_mods(self.records, self.query.get())
        rows = {}
        selected_row = None
        for record in records:
            existing = self._items.get(record.path)
            if existing is None or existing[1] != record:
                values = (record.kind, record.model_count if record.model_count is not None else "打开时检查", record.box_state, record.relative)
                if existing is None:
                    iid = f"lib:{self._next_item}"
                    self._next_item += 1
                    self.tree.insert("", "end", iid=iid, text=record.name, values=values)
                else:
                    iid = existing[0]
                    self.tree.item(iid, text=record.name, values=values)
                self._items[record.path] = (iid, record)
            else:
                iid = existing[0]
            rows[iid] = record
            if selected and selected.path == record.path:
                selected_row = iid
        # Detach filtered rows in one Tk call; reuse them on the next query.
        self.tree.set_children("", *rows)
        self.rows = rows
        if selected_row:
            self.tree.selection_set(selected_row)
        else:
            self.tree.selection_remove(*self.tree.selection())
        self.open_button.configure(state="normal" if self.selected() else "disabled")
        refreshing = " · 正在后台更新…" if self.future is not None else ""
        self.summary.set(f"{len(records)} / {len(self.records)} 个模组 · 文件夹与 ZIP 仅列出含 MOD3 的项" + refreshing)
        if not self.selected():
            self.detail.set("选择一个模组，双击或点击“打开所选”。")

    def selected(self):
        return next((self.rows[iid] for iid in self.tree.selection() if iid in self.rows), None)

    def select(self, *_):
        record = self.selected()
        self.open_button.configure(state="normal" if record else "disabled")
        self.detail.set(str(record.path) if record else "选择一个模组，双击或点击“打开所选”。")

    def open_selected(self, event=None):
        if event and event.type == tk.EventType.ButtonPress and self.tree.identify_region(event.x, event.y) not in ("tree", "cell"):
            return
        record = self.selected()
        if not record or not self.app._confirm_discard():
            return
        previous = self.app.session
        self.app._load(str(record.path))
        if self.app.session is not previous:
            self.app.title(self.app.title() + " · " + record.name)
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        self.scan_bar.stop()
        if self.poll_job:
            self.after_cancel(self.poll_job)
        if self.future:
            self.future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
