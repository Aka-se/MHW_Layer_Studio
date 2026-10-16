"""Verify real preview bindings without modifying a model or user session."""
import hashlib
import json
from pathlib import Path
import numpy as np


def exercise(canvas, renderer, selected):
    keys = selected().copy()
    pose = (renderer.yaw, renderer.pitch, renderer.pan_x, renderer.pan_y, renderer.zoom)
    canvas.event_generate("<ButtonPress-1>", x=100, y=100)
    canvas.event_generate("<B1-Motion>", x=140, y=120)
    canvas.event_generate("<B1-Motion>", x=100, y=100)
    canvas.event_generate("<ButtonRelease-1>", x=100, y=100)
    assert selected() == keys and pose == (renderer.yaw, renderer.pitch, renderer.pan_x, renderer.pan_y, renderer.zoom)
    # A distant release is cancelled even if no motion event was delivered.
    canvas.event_generate("<ButtonPress-1>", x=100, y=100)
    canvas.event_generate("<ButtonRelease-1>", x=160, y=100)
    assert selected() == keys and renderer.yaw == pose[0]
    canvas.event_generate("<ButtonPress-3>", x=100, y=100)
    canvas.event_generate("<B3-Motion>", x=130, y=115)
    canvas.event_generate("<ButtonRelease-3>", x=130, y=115)
    assert renderer.yaw != pose[0] and renderer.pitch != pose[1]
    assert (renderer.pan_x, renderer.pan_y, renderer.zoom) == pose[2:]
    assert selected() == keys
    angles = renderer.yaw, renderer.pitch
    canvas.event_generate("<ButtonPress-2>", x=100, y=100)
    canvas.event_generate("<B2-Motion>", x=130, y=115)
    canvas.event_generate("<ButtonRelease-2>", x=130, y=115)
    assert (renderer.yaw, renderer.pitch) == angles
    assert renderer.pan_x != pose[2] and renderer.pan_y != pose[3]
    assert selected() == keys
    zoom = renderer.zoom
    canvas.event_generate("<MouseWheel>", delta=120)
    assert renderer.zoom > zoom and selected() == keys
    enlarged = renderer.zoom
    canvas.event_generate("<MouseWheel>", delta=-120)
    assert renderer.zoom < enlarged and selected() == keys


def pick_points(renderer):
    points = {}
    ids = renderer._pixel_ids
    for value in np.unique(ids):
        if value < 0:
            continue
        rows, columns = np.nonzero(ids == value)
        i = len(rows) // 2
        x, y = int(columns[i] / renderer._pick_scale[0]), int(rows[i] / renderer._pick_scale[1])
        key = renderer.pick(x, y)
        if key is not None:
            points.setdefault(key, (x, y))
    return points


def selection_feedback(app):
    app._redraw(synchronous=True)
    r = app.renderer
    for x, y in pick_points(r).values():
        stack = r.pick_stack(x, y)
        if len(stack) > 1:
            break
    else:
        raise AssertionError("EX2 fixture needs an overlapping pair for through-selection")
    original = [d.bytes() for d in app.workspace.documents]
    first, second = stack[:2]
    for expected in (second, stack[2] if len(stack) > 2 else first):
        app.preview.event_generate("<ButtonPress-1>", x=x, y=y, state=0x20000)
        app.preview.event_generate("<ButtonRelease-1>", x=x, y=y, state=0x20000)
        assert app._selection_keys == {expected}
    app.preview.event_generate("<ButtonPress-1>", x=x, y=y, state=0)
    app.preview.event_generate("<ButtonRelease-1>", x=x, y=y, state=0)
    assert app._selection_keys == {first}
    # Reproduce the reported trap: a selected inner mesh must not monopolize
    # normal clicks when the surrounding outer meshes are ghosted.
    original_mode = app.color_mode.get()
    for mode in ("独立部件", "贴图"):
        app.color_mode.set(mode)
        app.ghost.set(True)
        app._select_by_key(second)
        app._redraw(synchronous=True)
        assert r.pick(x, y) == first
        assert r.pick_stack(x, y)[:2] == [first, second]
        for locks in (0, 0x8, 0x2a):
            app.preview.event_generate("<ButtonPress-1>", x=x, y=y, state=locks)
            app.preview.event_generate("<ButtonRelease-1>", x=x, y=y, state=locks)
            assert app._selection_keys == {first}, (mode, locks, app._selection_keys)
            app._redraw(synchronous=True)
            app.preview.event_generate("<ButtonPress-1>", x=x, y=y, state=locks | 0x20000)
            app.preview.event_generate("<ButtonRelease-1>", x=x, y=y, state=locks | 0x20000)
            assert app._selection_keys == {second}
    app.ghost.set(False)
    app.color_mode.set(original_mode)
    app._select_by_key(first)
    app._redraw(synchronous=True)
    assert original == [d.bytes() for d in app.workspace.documents]


def navigation(app):
    r = app.renderer
    app._redraw(synchronous=True)
    points = pick_points(r)
    key = next(iter(points))
    app._select_by_key(key)
    r.yaw, r.pitch, r.zoom = .23, -.17, 1.13
    r.pan_x, r.pan_y = .08, -.06
    before = r.camera_state()
    focused = app._focus_selection
    for _ in range(2):
        app._toggle_isolate_button()
        assert app.isolate.get()
        r.yaw, r.pitch, r.zoom = .8, .1, 2.3
        r.pan_x, r.pan_y = -.1, .2
        app._toggle_isolate_button()
        assert not app.isolate.get()
        for name, value in before.items():
            np.testing.assert_allclose(getattr(r, name), value)
        assert app._focus_selection == focused
    # Rebase at a hit through the actual double-middle-click binding.
    app._redraw(synchronous=True)
    points = pick_points(r)
    key, (x, y) = next(iter(points.items()))
    app._select_by_key(key)
    vertices = np.concatenate([m.vertices for _, d, m in r.visible_entries(app.workspace)])
    def screen():
        p = (vertices - r.camera_center) @ r._rotation().T
        w, h = app.preview.winfo_width(), app.preview.winfo_height()
        scale = min(w, h) * .84 / r.camera_span * r.zoom
        return p[:, :2] * [scale, -scale] + [w * (.5 + r.pan_x), h * (.5 + r.pan_y)]
    image_points, zoom, selected = screen(), r.zoom, app._selection_keys.copy()
    for tick in (10000, 10100):
        app.preview.event_generate("<ButtonPress-2>", x=x, y=y, time=tick)
        app.preview.event_generate("<ButtonRelease-2>", x=x, y=y, time=tick + 20)
    assert "旋转中心已设为" in app.status.get()
    np.testing.assert_allclose(screen(), image_points, atol=2e-4)
    assert r.zoom == zoom and app._selection_keys == selected
    # Space only overrides the rendered mode; it never changes saved color choice.
    for mode in ("分层", "材质", "贴图"):
        app.color_mode.set(mode)
        app.preview.focus_force()
        app.update()
        app.preview.event_generate("<KeyPress-space>")
        app.preview.event_generate("<KeyPress-space>")  # Windows key repeat.
        app._sync_preview_controls()
        assert app._temporary_mesh_colors and r.color_mode == "mesh"
        assert app._view_state()["color"] == mode
        app.preview.event_generate("<KeyRelease-space>")
        app._sync_preview_controls()
        assert not app._temporary_mesh_colors
        assert r.color_mode == {"分层": "layer", "材质": "material", "贴图": "texture"}[mode]
    app.preview.event_generate("<KeyPress-space>")
    assert app._temporary_mesh_colors
    app.group_search_entry.focus_force()
    app.update()
    assert not app._temporary_mesh_colors
    app.group_search_entry.event_generate("<KeyPress-space>")
    assert not app._temporary_mesh_colors
    app.group_search.set("")
    app.color_mode.set("分层")
    app._sync_preview_controls()
    # An off-center wheel event follows the pointer, without selecting anything.
    pan = (r.pan_x, r.pan_y)
    app.preview.event_generate("<MouseWheel>", x=31, y=47, delta=120)
    assert (r.pan_x, r.pan_y) != pan and app._selection_keys == selected
    app.show_all()
    r.yaw = r.pitch = 0
    app._redraw(synchronous=True)


def run(app, source, output):
    import time
    from mod3 import ModWorkspace
    from session import EditorSession
    from spatial_dialog import SpatialGroupDialog
    from unittest.mock import patch
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    app.geometry("1280x800+320+120")
    app._adopt_session(EditorSession(ModWorkspace.open(source)))
    app.update()
    original = [d.bytes() for d in app.workspace.documents]
    app._redraw(synchronous=True)
    points = pick_points(app.renderer)
    assert len(points) >= 2
    first, second = list(points)[:2]
    def click(key, ctrl=False, locks=0):
        x, y = points[key]
        state = locks | (4 if ctrl else 0)
        app.preview.event_generate("<ButtonPress-1>", x=x, y=y, state=state)
        app.preview.event_generate("<ButtonRelease-1>", x=x, y=y, state=state)
    click(first)
    assert app._selection_keys == {first}
    click(second, True)
    assert app._selection_keys == {first, second}
    click(second, True)
    assert app._selection_keys == {first}
    exercise(app.preview, app.renderer, lambda: app._selection_keys)
    selection_feedback(app)
    navigation(app)
    # Table context menus keep their established right-click binding.
    assert app.tree.bind("<Button-3>")
    d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
    app._select_by_key((d.name, 1))
    dialog = SpatialGroupDialog(app)
    deadline = time.monotonic() + 45
    while (not dialog.plan or dialog.frame is not None or dialog.pending or dialog.last_image is None) and time.monotonic() < deadline:
        app.update()
        time.sleep(.015)
    assert dialog.plan and dialog.last_image is not None
    from dialog_geometry import window_rect, work_area
    area, frame, parent_frame = work_area(app), window_rect(dialog), window_rect(app)
    split_geometry = {"size": [dialog.winfo_width(), dialog.winfo_height()], "frame": frame,
                      "parent_frame": parent_frame, "work_area": area}
    assert area[0] <= frame[0] < frame[2] <= area[2]
    assert area[1] <= frame[1] < frame[3] <= area[3]
    if area[2] - area[0] >= 1544 and area[3] - area[1] >= 976:
        assert dialog.winfo_width() >= 1480 and dialog.winfo_height() >= 880
    ideal_x = (parent_frame[0] + parent_frame[2] - (frame[2] - frame[0])) / 2
    ideal_y = (parent_frame[1] + parent_frame[3] - (frame[3] - frame[1])) / 2
    if area[0] + 12 <= ideal_x <= area[2] - (frame[2] - frame[0]) - 12:
        assert abs((frame[0] + frame[2]) - (parent_frame[0] + parent_frame[2])) <= 2
    if area[1] + 12 <= ideal_y <= area[3] - (frame[3] - frame[1]) - 12:
        assert abs((frame[1] + frame[3]) - (parent_frame[1] + parent_frame[3])) <= 2
    assert dialog.distance.get() == "常规"
    exercise(dialog.canvas, dialog.renderer, dialog.selected_keys)
    dialog.texture.set(True)
    dialog.canvas.focus_force()
    app.update()
    dialog.canvas.event_generate("<KeyPress-space>")
    assert dialog.temporary_mesh_colors and dialog.renderer.color_mode == "mesh"
    dialog.canvas.event_generate("<KeyRelease-space>")
    assert not dialog.temporary_mesh_colors and dialog.renderer.color_mode == "texture"
    dialog.texture.set(False)
    dialog.request_frame()
    # Wait for the new camera frame, then pick a visible child in the staged model.
    while (dialog.frame is not None or dialog.pending) and time.monotonic() < deadline:
        app.update()
        time.sleep(.015)
    assert dialog.renderer._pixel_ids is not None, (
        dialog.canvas.winfo_width(), dialog.canvas.winfo_height(), dialog.renderer.pan_x, dialog.renderer.pan_y,
        len(dialog.renderer.visible_entries(dialog.plan["staged"].workspace)), dialog.frame, dialog.pending)
    staged_points = pick_points(dialog.renderer)
    key = next(k for k in staged_points if k in dialog.plan["eligible"])
    x, y = staged_points[key]
    dialog.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
    dialog.canvas.event_generate("<ButtonRelease-1>", x=x, y=y, state=0)
    assert dialog.selected_keys() == {key}
    for px, py in staged_points.values():
        stack = dialog.renderer.pick_stack(px, py)
        if len(stack) > 1:
            outer, inner = stack[:2]
            gi = next(i for i, g in enumerate(dialog.plan["groups"]) if inner in g["members"])
            mi = dialog.plan["groups"][gi]["members"].index(inner)
            dialog.tree.selection_set(f"m:{gi}:{mi}")
            dialog.select()
            while (dialog.frame is not None or dialog.pending) and time.monotonic() < deadline:
                app.update()
                time.sleep(.015)
            assert dialog.renderer.pick(px, py) == outer
            for locks in (0, 0x8, 0x2a):
                dialog.canvas.event_generate("<ButtonPress-1>", x=px, y=py, state=locks)
                dialog.canvas.event_generate("<ButtonRelease-1>", x=px, y=py, state=locks)
                assert dialog.selected_keys() == {outer}
                dialog.canvas.event_generate("<ButtonPress-1>", x=px, y=py, state=locks | 0x20000)
                dialog.canvas.event_generate("<ButtonRelease-1>", x=px, y=py, state=locks | 0x20000)
                assert dialog.selected_keys() == {inner}
            break
    else:
        raise AssertionError("Split fixture needs overlapping parts")
    # Continue testing transfer with the previously identified single member.
    gi = next(i for i, g in enumerate(dialog.plan["groups"]) if key in g["members"])
    dialog.tree.selection_set(f"m:{gi}:{dialog.plan['groups'][gi]['members'].index(key)}")
    dialog.select()
    source_index = next(i for i, g in enumerate(dialog.plan["groups"]) if key in g["members"])
    target_index = next(i for i in range(len(dialog.plan["groups"])) if i != source_index)
    target = dialog.plan["groups"][target_index]
    target_name = target["name"]
    dialog.tree.item(f"g:{target_index}", open=False)
    dialog.target.set(target_name)
    dialog.move()
    row = f"g:{dialog.plan['groups'].index(target)}"
    assert not dialog.tree.item(row, "open") and key in target["members"]
    assert dialog.tree.selection() == (row,) and dialog.target.get() == target_name
    dialog.tree.item(row, open=True)
    dialog.transfer({key}, target)
    row = f"g:{dialog.plan['groups'].index(target)}"
    assert dialog.tree.item(row, "open")
    dialog.close()
    # Default group picks and additive selection support regrouping whole groups.
    app._redraw(synchronous=True)
    points = pick_points(app.renderer)
    first, second = list(points)[:2]
    extras = [k for k in [(d.name, m.index) for d, m in app.workspace.all_meshes()] if k not in (first, second)]
    a, b = {first, extras[0]}, {second, extras[1]}
    app.session.edit_named_group("create", "组 A", a)
    app.session.edit_named_group("create", "组 B", b)
    app._refresh_groups()
    app.pick_groups.set(True)
    for locks in (0, 0x8, 0x2a):
        row_a = next((iid for iid, name in app._group_iids.items() if name == "组 A"), None)
        was_open = bool(row_a and app.tree.item(row_a, "open"))
        app._last_group_pick = None
        click(first, locks=locks)
        assert app._selection_keys == a
        assert bool(app.tree.item(app.tree.selection()[0], "open")) == was_open
        click(first, locks=locks)
        assert app._selection_keys == {first} and app.tree.selection()[0].startswith("m:")
        click(first, locks=locks)
        assert app._selection_keys == {first}
    # Clicking elsewhere starts a new group pick rather than drilling another group.
    click(second)
    assert app._selection_keys == b
    click(first)
    assert app._selection_keys == a
    click(second, True, locks=0x8)
    assert app._selection_keys == a | b
    from tkinter import simpledialog
    with patch.object(simpledialog, "askstring", return_value="合并组"):
        app.edit_named_group("create")
    assert app.session.named_groups["合并组"] == a | b
    assert not app.tree.item(app.tree.selection()[0], "open")
    app.pick_groups.set(False)
    click(first)
    assert app._selection_keys == {first}
    app.session.edit_named_group("create", "移入目标", {extras[2]})
    app._refresh_groups()
    target_row = next(iid for iid, name in app._group_iids.items() if name == "移入目标")
    app.tree.item(target_row, open=False)
    app._select_by_key(first)
    app.named_group.set("移入目标")
    app.edit_named_group("add")
    target_row = next(iid for iid, name in app._group_iids.items() if name == "移入目标")
    assert not app.tree.item(target_row, "open")
    assert first in app.session.named_groups["移入目标"]
    app.pick_groups.set(True)
    assert app._view_state()["pick_groups"] is True
    assert original == [d.bytes() for d in app.workspace.documents]
    assert digest == hashlib.sha256(Path(source).read_bytes()).hexdigest()
    assert not errors, errors
    report = {"ok": True, "version": app.title(), "left_click_select": True, "ctrl_click_multiselect": True,
              "left_drag_cancelled": True, "right_drag_rotation": True, "middle_drag_pan": True,
              "wheel_zoom": True, "split_preview_same_controls": True, "split_preview_picking": True,
              "table_context_menu_preserved": True, "source_unchanged": True,
              "default_regular": True, "move_preserves_fold": True,
              "default_group_picking": True, "merge_selected_groups": True,
              "repeat_click_member_picking": True,
              "larger_centered_split_window": True, "split_window_within_work_area": True,
              "split_geometry": split_geometry,
              "single_component_picking": True, "main_add_preserves_fold": True,
              "cursor_zoom_and_limits": True, "isolation_restores_camera": True,
              "middle_double_click_pivot_without_jump": True, "space_temporary_colors": True,
              "space_release_and_focus_loss_restore": True, "text_entry_space_unaffected": True,
              "alt_click_cycles_overlapping_meshes": True, "normal_click_keeps_frontmost_selection": True,
              "ghosted_outer_surface_remains_pickable": True, "split_ghost_inner_does_not_capture_clicks": True,
              "num_caps_scroll_lock_keep_surface_and_group_selection": True,
              "alt_with_num_lock_still_picks_through": True,
              "num_lock_ctrl_click_keeps_group_multiselect": True}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
