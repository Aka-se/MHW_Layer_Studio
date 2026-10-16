"""Exercise the continuous health/select/assign loop using the real Tk controls."""
import hashlib
import json
import time
import numpy as np
from pathlib import Path


def run(app, source, output, capture=False):
    from mod3 import ModWorkspace
    from session import EditorSession
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    app._adopt_session(EditorSession(ModWorkspace.open(source)))
    # Empty selection keeps readable native buttons; activation never edits data.
    from PIL import ImageTk
    for button in app.layer_buttons.values():
        assert not button.instate(["disabled"])
        pixels = np.asarray(ImageTk.getimage(button._photo).convert("RGBA"))
        assert (pixels[:, :, 3] > 0).sum() > 100
        assert ((pixels[:, :, :3].mean(2) < 130) & (pixels[:, :, 3] > 0)).sum() > 100
    unchanged = [d.bytes() for d in app.workspace.documents]
    app.layer_buttons[60].invoke()
    assert "请先" in app.status.get()
    assert unchanged == [d.bytes() for d in app.workspace.documents]
    document = next(d for d in app.workspace.documents if d.equipment_part == "wst")
    di = app.workspace.documents.index(document)
    # Exercise structural refresh first, then assign a complete group of children.
    app.session.split_mesh(document.name, 1)
    document = app.session.document(document.name)
    keys = {(document.name, i) for i in (1, 2, 3)}
    app.session.edit_named_group("create", "验证部件组", keys)
    app.list_mode.set("按部件组")
    app._refresh_groups()
    app.named_group.set("验证部件组")
    app.select_named_group()
    group_iid = next(iid for iid, name in app._group_iids.items() if name == "验证部件组")
    app.tree.item(group_iid, open=True)
    assert app._selection_keys == keys
    assert app.simulate_health.get()
    for mode, button in app.color_buttons.items():
        button.invoke()
        app._sync_preview_controls()
        assert app.color_mode.get() == mode
        assert app.renderer.color_mode == {"分层": "layer", "独立部件": "mesh", "材质": "material", "贴图": "texture"}[mode]
    app.color_buttons["分层"].invoke()
    # Joining a chosen group from the context menu does not first replace selection.
    from types import SimpleNamespace
    from unittest.mock import patch
    app.session.edit_named_group("create", "验证目标", {(document.name, 4)})
    app._refresh_groups()
    app.update()
    app.tree.see(group_iid)
    app.update()
    row = app.tree.bbox(group_iid)
    event = SimpleNamespace(x=row[0] + 40, y=row[1] + row[3] // 2,
                            x_root=app.tree.winfo_rootx() + row[0] + 40,
                            y_root=app.tree.winfo_rooty() + row[1] + row[3] // 2)
    with patch("tkinter.Menu.tk_popup"):
        app._tree_context_menu(event)
    menu = app._context_menu
    cascade = next(i for i in range(menu.index("end") + 1)
                   if menu.type(i) == "cascade" and menu.entrycget(i, "label") == "加入部件组")
    targets = app.nametowidget(menu.entrycget(cascade, "menu"))
    target = next(i for i in range(targets.index("end") + 1)
                  if targets.type(i) == "command" and targets.entrycget(i, "label").startswith("验证目标 ·"))
    targets.invoke(target)
    assert app.session.named_groups["验证目标"] == keys | {(document.name, 4)}
    assert app._selection_keys == keys | {(document.name, 4)}
    app.undo()
    app.undo()
    app.named_group.set("验证部件组")
    app.select_named_group()
    group_iid = next(iid for iid, name in app._group_iids.items() if name == "验证部件组")
    app.tree.item(group_iid, open=True)
    assert app._selection_keys == keys
    app.health_input.set("70")
    app._health_input_commit()
    app.renderer.yaw, app.renderer.zoom = .3, 1.15
    pose = (app.renderer.yaw, app.renderer.zoom)
    app.layer_buttons[60].invoke()
    app._sync_preview_controls()
    assert app.health.get() == 70 and app._selection_keys == keys
    assert (app.renderer.yaw, app.renderer.zoom) == pose
    assert all(document.meshes[i].visible_condition == 60 for _, i in keys)
    assert all(not app.renderer.visible_for_health(document.meshes[i]) for _, i in keys)
    assert app.tree.set(group_iid, "visibility") == "因血量隐藏"
    assert "分配后隐藏" in app.layer_buttons[60].cget("text")
    assert "分配后显示" in app.layer_buttons[61].cget("text")
    # Hidden meshes remain selected and editable without raising health.
    app.layer_buttons[61].invoke()
    app._sync_preview_controls()
    assert all(app.renderer.visible_for_health(document.meshes[i]) for _, i in keys)
    assert app.tree.set(group_iid, "visibility") == "显示"
    app.undo_button.invoke()
    assert all(document.meshes[i].visible_condition == 60 for _, i in keys)
    assert app.tree.set(group_iid, "visibility") == "因血量隐藏"
    app.redo_button.invoke()
    assert all(document.meshes[i].visible_condition == 61 for _, i in keys)
    app.hp_scale.set(60)
    assert app.health.get() == 60
    assert app.tree.set(group_iid, "visibility") == "因血量隐藏"
    app.health_input.set("61")
    app._health_input_commit()
    assert app.tree.set(group_iid, "visibility") == "显示"
    app.visibility_button.invoke()
    assert app.visibility_button.cget("text") == "显示所选"
    app.hp_scale.set(100)
    assert app.tree.set(group_iid, "visibility") == "手动隐藏"
    app.visibility_button.invoke()
    assert app.visibility_button.cget("text") == "隐藏所选"
    assert app.tree.set(group_iid, "visibility") == "显示"
    assert app._selection_keys == keys
    app.visibility_button.invoke()
    app.show_all()
    assert app.tree.set(group_iid, "visibility") == "显示"
    assert app.health.get() == 100
    # Configured thresholds determine both rendering and the assignment labels.
    app.session.set_health_thresholds({60: 85, 61: 70, 62: 55})
    app._refresh_groups()
    # Check both displayed controls after undo/redo and after saving/reopening a project.
    def assert_threshold_labels(values):
        for group, threshold in values.items():
            assert f"≤{threshold}%" in app.layer_buttons[group].cget("text")
            assert app.session.health_thresholds[group] == threshold
    assert_threshold_labels({60: 85, 61: 70, 62: 55})
    app.undo()
    assert_threshold_labels({60: 80, 61: 60, 62: 40})
    app.redo()
    assert_threshold_labels({60: 85, 61: 70, 62: 55})
    project = output / "thresholds-check.mhwlayer.json"
    app.session.save(project, app._view_state())
    restored, saved_view = EditorSession.open(project)
    app._adopt_session(restored)
    app._restore_view(saved_view)
    document = app.session.document(document.name)
    group_iid = next(iid for iid, name in app._group_iids.items() if name == "验证部件组")
    assert_threshold_labels({60: 85, 61: 70, 62: 55})
    app.hp_scale.set(70)
    assert app.health.get() == 70
    assert "分配后隐藏" in app.layer_buttons[61].cget("text")
    app.simulate_health.set(False)
    app._health_simulation_changed()
    assert app.tree.set(group_iid, "visibility") == "显示"
    assert "模拟关闭" in app.layer_buttons[61].cget("text")
    app.simulate_health.set(True)
    app._health_simulation_changed()
    assert app.tree.set(group_iid, "visibility") == "因血量隐藏"
    app.health_input.set("bad")
    app._health_input_commit()
    assert app.health_input.get() == "70"
    app.health_input.set("150")
    app._health_input_commit()
    assert app.health.get() == 100
    # Configurable columns and saved view restore cannot change group selection.
    app.column_vars["material"].set(True)
    app._columns_changed()
    view = app._view_state()
    app._compact_columns()
    app._restore_view(view)
    assert "material" in app.tree.cget("displaycolumns")
    assert app._selection_keys == keys
    app._compact_columns()
    app.health_input.set("70")
    app._health_input_commit()
    app.layer_buttons[60].invoke()
    app._toggle_details()
    app._toggle_details()
    sizes = []
    for width, height in ((1120, 740), (1280, 800), (1500, 900)):
        app.geometry(f"{width}x{height}+0+0")
        app.update()
        assert app.preview.winfo_width() >= 380, app.preview.winfo_width()
        assert app.preview.winfo_height() >= 350, app.preview.winfo_height()
        controls = [app.hp_scale, app.health_entry, app.details_toggle, *app.layer_buttons.values(),
                    *getattr(app, 'damage_mode_buttons', []),
                    *app.color_buttons.values(), app.named_group_combo, app.tree, app.export_button, app.overwrite_button, app.split_toggle,
                    app.visibility_button, app.isolate_button, app.focus_button, app.show_all_button, app.view_button, app.threshold_button]
        for widget in controls:
            assert widget.winfo_ismapped()
            x = widget.winfo_rootx() - app.winfo_rootx()
            y = widget.winfo_rooty() - app.winfo_rooty()
            assert x >= 0 and y >= 0 and x + widget.winfo_width() <= app.winfo_width(), (widget, x)
            assert y + widget.winfo_height() <= app.winfo_height(), (widget, y)
        sizes.append({"window": [width, height], "preview": [app.preview.winfo_width(), app.preview.winfo_height()]})
        bar = (app.isolate_button, app.focus_button, app.visibility_button, app.show_all_button, app.view_button)
        assert max(w.winfo_height() for w in bar) == min(w.winfo_height() for w in bar)
        assert max(w.winfo_width() for w in bar) - min(w.winfo_width() for w in bar) <= 1
        assert app.isolate_button.winfo_rootx() < app.focus_button.winfo_rootx() < app.visibility_button.winfo_rootx()
        # Pointer clicks are absolute, aligned with the visual reference ticks.
        for hp in (0, 25, 50, 75, 100, 63):
            x = int(round(app.hp_scale.coordinates(hp)))
            app.hp_scale.event_generate("<ButtonPress-1>", x=x, y=14)
            app.hp_scale.event_generate("<ButtonRelease-1>", x=x, y=14)
            assert app.health.get() == hp, (hp, app.health.get())
            assert app.health_input.get() == str(hp)
        for hp in app.session.health_thresholds.values():
            assert app.hp_scale.itemcget(app.hp_scale.find_withtag(f"tick-{hp}")[0], "text") == str(hp)
    app.hp_scale.event_generate("<B1-Motion>", x=int(round(app.hp_scale.coordinates(41))), y=14)
    assert app.health.get() == 41
    app.hp_scale.event_generate("<KeyPress-Right>")
    assert app.health.get() == 42
    app.hp_scale.event_generate("<Home>")
    assert app.health.get() == 0
    # Click the secondary label itself: both text lines activate the same layer action.
    target = app.layer_buttons[62]
    target.event_generate("<ButtonPress-1>", x=15, y=target.winfo_height() - 12)
    target.event_generate("<ButtonRelease-1>", x=15, y=target.winfo_height() - 12)
    assert all(document.meshes[i].visible_condition == 62 for _, i in keys)
    app.layer_buttons[60].focus_set()
    app.update()
    assert app.focus_get() is app.layer_buttons[60]
    app.layer_buttons[60].event_generate("<Return>")
    assert all(document.meshes[i].visible_condition == 60 for _, i in keys)
    app.isolate_button.invoke()
    app.update()
    assert app.isolate.get() and "✓" in app.isolate_button.cget("text")
    assert app.isolate_button.instate(["pressed"])
    app.isolate_button.invoke()
    app.update()
    assert not app.isolate.get() and not app.isolate_button.instate(["pressed"])
    # Four default stages are available immediately; custom stages remain editable.
    app.threshold_button.invoke()
    dialog = app._threshold_dialog
    dialog.defaults_button.invoke()
    assert len(dialog.rows) == 4
    assert (dialog.rows[-1]["group"].get(), dialog.rows[-1]["hp"].get()) == ("63", "20")
    dialog.add_button.invoke()
    assert len(dialog.rows) == 5 and dialog.rows[-1]["group"].get() == "64"
    dialog.remove(dialog.rows[-1])
    dialog.apply_button.invoke()
    app.update()
    assert app.session.health_thresholds == {60: 80, 61: 60, 62: 40, 63: 20}
    assert 63 in app.layer_buttons and 20 in app.hp_scale.ticks
    assert all(document.meshes[i].visible_condition == 60 for _, i in keys)
    app.layer_buttons[63].invoke()
    app.hp_scale.set(20)
    assert app.tree.set(group_iid, "visibility") == "因血量隐藏"
    app.hp_scale.set(21)
    assert app.tree.set(group_iid, "visibility") == "显示"
    from mod3 import generate_health_rules
    rules = generate_health_rules(thresholds=app.session.health_thresholds)
    assert "Id=63\nHealthPercentAbove=20\nReturn=1" in rules
    four_project = output / "four-stages-check.mhwlayer.json"
    app.session.save(four_project, app._view_state())
    saved_session, saved_view = EditorSession.open(four_project)
    assert saved_session.health_thresholds == app.session.health_thresholds
    app._adopt_session(saved_session)
    app._restore_view(saved_view)
    document = app.session.document(document.name)
    assert 63 in app.layer_buttons
    # Removing a rule restores unmapped preview behavior, keeping the assigned Group.
    app.threshold_button.invoke()
    dialog = app._threshold_dialog
    dialog.remove(dialog.rows[-1])
    dialog.apply_button.invoke()
    assert 63 not in app.layer_buttons
    three_project = output / "intentional-three.mhwlayer.json"
    app.session.save(three_project)
    assert 63 not in EditorSession.open(three_project)[0].health_thresholds
    assert all(document.meshes[i].visible_condition == 63 for _, i in keys)
    app.hp_scale.set(0)
    assert all(app._mesh_visibility(document, document.meshes[i]) == "显示" for _, i in keys)
    app.undo()
    assert 63 in app.layer_buttons
    app.layer_buttons[60].invoke()
    # Both expandable sections remain reachable via the right panel's scrollbar.
    app.geometry("1120x740")
    app.split_toggle.invoke()
    app.details_toggle.invoke()
    app.update()
    assert app._split_open and app._details_open
    assert all(button.winfo_ismapped() for button in app.split_buttons.values())
    assert app.right_panel.scroll.winfo_ismapped()
    app.right_panel.canvas.yview_moveto(1)
    app.update()
    assert app.details_toggle.winfo_rooty() < app.winfo_rooty() + app.winfo_height()
    app.details_toggle.invoke()
    app.split_toggle.invoke()
    app.update()
    app.hp_scale.set(60)
    app.geometry("1280x800+30+30")
    app.update()
    app._redraw(synchronous=True)
    hidden_pixels = app.renderer._pixel_ids.copy()
    from PIL import ImageTk
    ImageTk.getimage(app.preview_photo).save(output / "health-60.png")
    if capture:
        from PIL import ImageGrab
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(output / "editor-health-60.png")
    app.hp_scale.set(100)
    app._sync_preview_controls()
    app._redraw(synchronous=True)
    assert (hidden_pixels != app.renderer._pixel_ids).any(), "Health assignment did not change rendered geometry"
    ImageTk.getimage(app.preview_photo).save(output / "health-100.png")
    if capture:
        from PIL import ImageGrab
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(output / "editor-window.png")
        app.geometry("1500x900+20+20")
        app.split_toggle.invoke()
        app.update()
        app.right_panel.canvas.yview_moveto(0)
        app._redraw(synchronous=True)
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(output / "editor-tools-expanded.png")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert not errors, errors
    report = {"ok": True, "version": app.title().split(" · ")[0], "source_unchanged": True,
              "health_select_assign": True, "hidden_remains_editable": True, "undo_redo": True,
              "custom_thresholds": True, "threshold_undo_reopen_sync": True,
              "visible_coloring_controls": True, "saved_columns": True, "context_join_group": True,
              "absolute_track_click": True, "rule_ticks": True, "drag_keyboard": True,
              "split_expansion_scroll": True, "selected_hide_restore": True,
              "uniform_preview_toolbar": True, "layer_label_mouse_keyboard": True,
              "four_stages_add_remove_restore": True, "native_isolate_toggle": True, "sizes": sizes}
    report["readable_buttons_without_selection"] = True
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
