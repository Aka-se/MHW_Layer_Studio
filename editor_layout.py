"""The editing workspace: select, assign, and inspect at the current health."""
import math
import tkinter as tk
from tkinter import ttk

from lod_groups import LOD_VIEWS
from ui_tables import ResizableTreeview
from health_slider import HealthSlider
from scroll_panel import ScrollPanel
from layer_controls import LayerButton


def menu_button(parent, text, commands):
    button = ttk.Menubutton(parent, text=text.removesuffix(" ▾"))
    menu = tk.Menu(button, tearoff=False)
    for label, command in commands:
        if label is None:
            menu.add_separator()
        else:
            menu.add_command(label=label, command=command)
    button.configure(menu=menu)
    return button, menu


def build(app):
    style = ttk.Style(app)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
    style.configure("Hint.TLabel", foreground="#606975")
    style.configure("Preview.TButton", padding=(4, 3))
    style.configure("Layer.TButton", padding=(6, 4))
    style.configure("Treeview", rowheight=28)

    toolbar = ttk.Frame(app, padding=(10, 8))
    toolbar.pack(fill="x")
    opening, app.open_menu = menu_button(toolbar, "打开 ▾", (
        ("打开模组文件…", app.open_file), ("打开模组文件夹…", app.open_folder),
        ("打开分层项目…", app.open_project), ("保存分层项目…", app.save_project)))
    opening.pack(side="left")
    app.mod_library_button = ttk.Button(toolbar, text="模组库", command=app.open_mod_library)
    app.mod_library_button.pack(side="left", padx=(6, 0))
    app.export_button = ttk.Button(toolbar, text="导出压缩包", command=app.export_mod)
    app.export_button.pack(side="left", padx=6)
    app.overwrite_button = ttk.Button(toolbar, text="覆盖保存", command=app.overwrite_mod)
    app.overwrite_button.pack(side="left")
    app.undo_button = ttk.Button(toolbar, text="撤销", command=app.undo)
    app.undo_button.pack(side="left", padx=(16, 4))
    app.redo_button = ttk.Button(toolbar, text="重做", command=app.redo)
    app.redo_button.pack(side="left")
    mode = ttk.Frame(toolbar)
    mode.pack(side='left', padx=(16, 0))
    ttk.Label(mode, text='衣装模式').pack(side='left', padx=(0, 6))
    app.damage_mode_buttons = []
    for text, value in (('手动分层', 'manual'), ('随机战损', 'random')):
        button = ttk.Radiobutton(mode, text=text, value=value, variable=app.damage_mode, command=app.change_damage_mode)
        button.pack(side='left', padx=(0, 6))
        app.damage_mode_buttons.append(button)
    button = ttk.Button(mode, text='战损设置', width=8, command=app.edit_damage_settings)
    button.pack(side='left')
    app.damage_mode_buttons.append(button)
    settings, app.settings_menu = menu_button(toolbar, "设置 ▾", (
        ("隐藏阈值…", app.edit_health_thresholds), ('随机战损设置…', app.edit_damage_settings), (None, None),
        ("模组库路径…", app.open_mod_library),
        ("导出血量规则…", app.export_rules), (None, None),
        ("游戏资源目录…", app.select_game_directory), ("共享贴图目录…", app.add_texture_root),
        (None, None), ("恢复原始层级（核对后）…", app.reset_all)))
    settings.pack(side="right")

    panes = ttk.Panedwindow(app, orient="horizontal")
    app.main_panes = panes
    panes.pack(fill="both", expand=True)
    panes.bind("<Configure>", lambda _e: app.after_idle(app._initial_pane_widths))
    panes.bind("<ButtonRelease-1>", app._remember_pane_widths)
    left = ttk.Frame(panes, padding=(10, 4, 5, 8))
    center = ttk.Frame(panes, padding=(5, 4, 5, 8))
    app.right_panel = ScrollPanel(panes, padding=(5, 4, 10, 8))
    right = app.right_panel.content
    panes.add(left, weight=3)
    panes.add(center, weight=5)
    panes.add(app.right_panel, weight=2)

    ttk.Label(left, text="选择部件", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
    outfits = ttk.Frame(left)
    outfits.pack(fill="x")
    ttk.Label(outfits, text="套装").pack(side="left", padx=(0, 6))
    app.outfit_combo = ttk.Combobox(outfits, textvariable=app.outfit_choice, state="readonly", width=10)
    app.outfit_combo.pack(side="left", fill="x", expand=True)
    app.outfit_combo.bind("<<ComboboxSelected>>", app._outfit_changed)
    parts = ttk.Frame(left)
    parts.pack(fill="x", pady=6)
    for i, (part, label) in enumerate((("helm", "头"), ("body", "胸"), ("arm", "手"),
                                       ("wst", "腰"), ("leg", "腿"), ("other", "其他"))):
        ttk.Checkbutton(parts, text=label, variable=app.part_vars[part], command=app._parts_changed).grid(
            row=0, column=i, sticky="w", padx=(0, 3))
    layout = ttk.Frame(left)
    layout.pack(fill="x", pady=(0, 6))
    app.list_mode_combo = ttk.Combobox(layout, textvariable=app.list_mode,
                                     values=("按部件组", "按文件"), state="readonly", width=9)
    app.list_mode_combo.pack(side="left")
    app.list_mode_combo.bind("<<ComboboxSelected>>", app._list_mode_changed)
    columns_button = ttk.Menubutton(layout, text="列")
    columns_button.pack(side="right")
    app.columns_menu = tk.Menu(columns_button, tearoff=False)
    columns_button.configure(menu=app.columns_menu)
    app.column_vars = {}
    columns = ("material", "note", "collection", "group", "original", "lod", "visibility")
    headings = {"material": "名称 / 材质", "note": "备注", "collection": "部件组", "group": "层级",
                "original": "原始 Group", "lod": "精度 / LOD", "visibility": "当前显隐"}
    for column in columns:
        variable = tk.BooleanVar(app, value=column in ("group", "visibility"))
        app.column_vars[column] = variable
        app.columns_menu.add_checkbutton(label=headings[column], variable=variable, command=app._columns_changed)
    app.columns_menu.add_separator()
    app.columns_menu.add_command(label="自适应可见列宽", command=app._fit_visible_columns)
    app.columns_menu.add_command(label="恢复精简列", command=app._compact_columns)
    search = ttk.Frame(left)
    search.pack(fill="x", pady=(0, 6))
    ttk.Label(search, text="查找组").pack(side="left")
    app.group_search_entry = ttk.Entry(search, textvariable=app.group_search, width=8)
    app.group_search_entry.pack(side="left", fill="x", expand=True, padx=5)
    app.group_search_entry.bind("<KeyRelease>", app._search_groups_changed)
    ttk.Button(search, text="清除", width=5, command=app._clear_group_search).pack(side="right")
    tree_frame = ttk.Frame(left)
    tree_frame.pack(fill="both", expand=True)
    app.tree = ResizableTreeview(tree_frame, columns=columns, displaycolumns=("group", "visibility"),
                                 show="tree headings", selectmode="extended")
    app.tree.heading("#0", text="部件 / 文件")
    app.tree.column("#0", width=165, minwidth=85, stretch=False)
    widths = {"material": 170, "note": 160, "collection": 170, "group": 60, "original": 75,
              "lod": 100, "visibility": 100}
    for column in columns:
        app.tree.heading(column, text=headings[column])
        app.tree.column(column, width=widths[column], minwidth=55, anchor="center", stretch=False)
    vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=app.tree.yview)
    horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=app.tree.xview)
    app.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    app.tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)
    app.tree.bind("<<TreeviewSelect>>", app._tree_selected)
    app.tree.bind("<Double-1>", app._edit_table_annotation, add="+")
    app.tree.bind("<Button-3>", app._tree_context_menu)
    ttk.Label(left, text="模型点选整组，再点同一部件选成员\nCtrl / Shift 多选 · 表头拖动调列宽", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    view = ttk.Frame(center)
    view.pack(fill="x", pady=(0, 6))
    app.isolate_button = ttk.Button(view, text="隔离", style="Preview.TButton", width=1, command=app._toggle_isolate_button)
    app.focus_button = ttk.Button(view, text="聚焦 F", style="Preview.TButton", width=1, command=app.focus_selected)
    app.visibility_button = ttk.Button(view, text="隐藏所选", style="Preview.TButton", width=1, command=app.toggle_selected_visibility)
    app.show_all_button = ttk.Button(view, text="显示全部", style="Preview.TButton", width=1, command=app.show_all)
    observing, app.view_menu = menu_button(view, "视图 ▾", (
        ("正面", lambda: app.standard_view(0)), ("背面", lambda: app.standard_view(math.pi)),
        ("左侧", lambda: app.standard_view(-math.pi / 2)), ("右侧", lambda: app.standard_view(math.pi / 2)),
        (None, None), ("自动居中", app.center_view), ("重置视角", app.reset_view),
        ("临时隐藏所选", app.hide_selected)))
    for column, widget in enumerate((app.isolate_button, app.focus_button, app.visibility_button, app.show_all_button, observing)):
        view.columnconfigure(column, weight=1, uniform="preview-actions")
        widget.grid(row=0, column=column, sticky="nsew", padx=1)
    app.view_button = observing
    app.view_menu.add_checkbutton(label="其他部件半透明", variable=app.ghost, command=app.schedule_redraw)
    precision = tk.Menu(app.view_menu, tearoff=False)
    app.view_menu.add_cascade(label="观察精度", menu=precision)
    for label in LOD_VIEWS:
        precision.add_radiobutton(label=label, value=label, variable=app.lod_view, command=app._lod_changed)
    coloring = ttk.Frame(center)
    coloring.pack(fill="x", pady=(0, 6))
    ttk.Label(coloring, text="着色").pack(side="left", padx=(0, 8))
    app.color_buttons = {}
    for label in ("分层", "独立部件", "材质", "贴图"):
        button = ttk.Radiobutton(coloring, text=label, value=label, variable=app.color_mode, command=app.schedule_redraw)
        button.pack(side="left", padx=(0, 8))
        app.color_buttons[label] = button
    app.view_menu.add_checkbutton(label="贴图透明区域", variable=app.texture_alpha, command=app.schedule_redraw)

    preview_frame = ttk.Frame(center, relief="sunken", borderwidth=1)
    preview_frame.pack(fill="both", expand=True)
    app.preview = tk.Canvas(preview_frame, background="#1c1f26", highlightthickness=0, width=1, height=1)
    app.preview.pack(fill="both", expand=True)
    bindings = {"<Configure>": lambda _e: app.schedule_redraw(), "<ButtonPress-1>": app._mouse_down,
                "<B1-Motion>": app._mouse_drag, "<ButtonRelease-1>": app._mouse_up,
                "<MouseWheel>": app._mouse_wheel, "<Button-4>": lambda _e: app._zoom(1.1),
                "<Button-5>": lambda e: app._zoom(.9, (e.x, e.y)), "<ButtonPress-2>": app._pan_down,
                "<B2-Motion>": app._pan_drag, "<ButtonRelease-2>": app._pan_up,
                "<ButtonPress-3>": app._rotate_down, "<B3-Motion>": app._rotate_drag,
                "<ButtonRelease-3>": app._rotate_up}
    for event, callback in bindings.items():
        app.preview.bind(event, callback)
    app.preview.bind("<Button-4>", lambda e: app._zoom(1.1, (e.x, e.y)))
    app.preview.bind("<Double-Button-2>", app._pivot_at_pointer)
    app.preview.bind("<KeyPress-space>", app._temporary_color_press)
    app.preview.bind("<KeyRelease-space>", app._temporary_color_release)
    app.preview.bind("<FocusOut>", app._temporary_color_release)
    ttk.Label(center, text="左键选择 · 中键平移 · 右键旋转 · 滚轮指向缩放\n中键双击设旋转中心 · 按住空格查看部件色", style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
    hp = ttk.LabelFrame(center, text="血量模拟 · 分配后立即观察", padding=8)
    hp.pack(fill="x", pady=(8, 0))
    headline = ttk.Frame(hp)
    headline.pack(fill="x")
    ttk.Checkbutton(headline, text="启用模拟", variable=app.simulate_health, command=app._health_simulation_changed).pack(side="left")
    app.threshold_button = ttk.Button(headline, text="自定义隐藏阈值", command=app.edit_health_thresholds)
    app.threshold_button.pack(side="left", padx=(8, 0))
    ttk.Label(headline, text="%").pack(side="right")
    app.health_input = tk.StringVar(app, value="100")
    app.health_entry = ttk.Entry(headline, textvariable=app.health_input, width=4)
    app.health_entry.pack(side="right", padx=(4, 2))
    app.health_entry.bind("<Return>", app._health_input_commit)
    app.health_entry.bind("<FocusOut>", app._health_input_commit)
    ttk.Label(headline, text="血量").pack(side="right")
    app.hp_scale = HealthSlider(hp, variable=app.health, command=app._health_changed)
    app.hp_scale.pack(fill="x", pady=5)

    ttk.Label(right, text="手动分层设置", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
    app.selection_summary = tk.StringVar(app, value="先从模型或列表选择部件")
    ttk.Label(right, textvariable=app.selection_summary, wraplength=225).pack(fill="x")
    app.selection_effect = tk.StringVar(app, value="")
    ttk.Label(right, textvariable=app.selection_effect, style="Hint.TLabel", wraplength=225).pack(fill="x", pady=(4, 8))
    app.layer_buttons = {}
    app.layer_actions = ttk.Frame(right)
    app.layer_actions.pack(fill="x")
    more, app.layer_menu = menu_button(right, "更多分配 ▾", (("自定义 Group ID…", app._custom_group_dialog),))
    more.pack(anchor="e", pady=(3, 6))
    ttk.Separator(right).pack(fill="x", pady=4)
    app.split_toggle = ttk.Button(right, text="拆分 / 自动成组 ▸", command=app._toggle_split)
    app.split_toggle.pack(fill="x")
    app.split_panel = ttk.Frame(right)
    app.split_buttons = {}
    actions = (("拆分并成组", app.auto_group_spatial),
               ("跨 LOD 同步拆分", lambda: app.auto_group_spatial(sync_lod=True)),
               ("材质成组", app.auto_group_materials), ("LOD 成组", lambda: app.auto_group_spatial(lod_only=True)))
    for label, command in actions:
        button = ttk.Button(app.split_panel, text=label, command=command, width=1)
        app.split_buttons[label] = button
    for column in (0, 1):
        app.split_panel.columnconfigure(column, weight=1, uniform="split")
    for label, row, column, span in (("拆分并成组", 0, 0, 2),
                                     ("跨 LOD 同步拆分", 1, 0, 2), ("材质成组", 2, 0, 1), ("LOD 成组", 2, 1, 1)):
        app.split_buttons[label].grid(row=row, column=column, columnspan=span, sticky="ew", padx=1, pady=2)
    ttk.Label(app.split_panel, text="拆分并成组：拆开独立几何，再按位置建议组\n跨 LOD：把现有分组同步到不同精度\n材质成组：同材质归组，不拆几何\nLOD 成组：关联精度版本，保留整体", style="Hint.TLabel", wraplength=225).grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
    app._split_open = False
    groups = ttk.LabelFrame(right, text="部件组", padding=4)
    groups.pack(fill="x", pady=(6, 4))
    app.named_group_combo = ttk.Combobox(groups, textvariable=app.named_group, state="readonly", width=10)
    app.named_group_combo.pack(fill="x")
    app.named_group_combo.bind("<<ComboboxSelected>>", app.select_named_group)
    app.named_group_summary = ttk.Label(groups, text="多选部件后新建组", wraplength=225, style="Hint.TLabel")
    app.named_group_summary.pack(anchor="w", pady=4)
    app.join_group_button = ttk.Menubutton(groups, text="加入组")
    app.join_group_menu = tk.Menu(app.join_group_button, tearoff=False, postcommand=app._populate_join_group_menu)
    app.join_group_button.configure(menu=app.join_group_menu)
    app.join_group_button.pack(fill="x", pady=(0, 4))
    row = ttk.Frame(groups)
    row.pack(fill="x")
    ttk.Button(row, text="新建组", command=lambda: app.edit_named_group("create")).pack(side="left")
    managing, app.group_menu = menu_button(row, "管理 ▾", (
        ("将所选组建立父组…", app.create_parent_group),
        ("关联附近附件…", app.suggest_accessories),
        ("移出所选部件", lambda: app.edit_named_group("remove")),
        ("重命名组…", lambda: app.edit_named_group("rename")),
        ("解散组", lambda: app.edit_named_group("delete"))))
    managing.pack(side="right")

    app.details_toggle = ttk.Button(right, text="部件详情 ▸", command=app._toggle_details)
    app.details_toggle.pack(fill="x", pady=(2, 4))
    app.details_panel = ttk.Frame(right)
    info = ttk.Frame(app.details_panel)
    info.pack(fill="both", expand=True)
    app.info_text = tk.Text(info, height=5, width=1, wrap="word", relief="flat", state="disabled", font="TkDefaultFont")
    scroll = ttk.Scrollbar(info, command=app.info_text.yview)
    app.info_text.configure(yscrollcommand=scroll.set)
    app.info_text.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    texture, app.texture_menu = menu_button(app.details_panel, "贴图 / 名称 ▾", (
        ("查看贴图 / UV…", app.show_texture), ("指定颜色图…", app.assign_texture),
        ("编辑名称 / 备注…", app.edit_mesh_annotations)))
    texture.pack(fill="x", pady=4)
    app._details_open = False
    app.right_panel.bind_wheel()
    ttk.Label(app, textvariable=app.status, relief="sunken", anchor="w", padding=(8, 4)).pack(fill="x", side="bottom")
    app._update_threshold_labels()
    app._update_selection_summary()
    app._update_history_buttons()
    app.after_idle(app._initial_pane_widths)
