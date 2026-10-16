"""Opt-in real-window checks shared by source and packaged validation."""
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

from mod3 import ModWorkspace
from session import EditorSession
from spatial_dialog import SpatialGroupDialog


def run(app, source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    before_source = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    app._adopt_session(EditorSession(ModWorkspace.open(source)))
    d = next(d for d in app.workspace.documents if d.equipment_part == "wst" and len(d.meshes) > 1)
    di = app.workspace.documents.index(d)
    before = d.bytes()
    errors, ticks = [], []
    app.report_callback_exception = lambda *e: errors.append(str(e))
    def pump(predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("Spatial UI check timed out")
            app.update()
            ticks.append(time.monotonic())
            time.sleep(.004)
        app.update()
    app.tree.selection_set(f"m:{di}:1")
    app._tree_selected()
    # Close during background analysis: no geometry/tag/history change.
    cancelled = SpatialGroupDialog(app)
    app.update()
    cancelled.close()
    pump(lambda: app._render_future is None and app._redraw_job is None)
    assert before == d.bytes() and not app.session.named_groups
    dialog = SpatialGroupDialog(app)
    pump(lambda: dialog.plan is not None and hasattr(dialog, "photo") and dialog.frame is None and not dialog.pending)
    assert before == d.bytes() and len(d.meshes) == 2
    assert dialog.plan["split_count"] == 1 and len(dialog.plan["eligible"]) == 31
    assert dialog.plan["use_features"]
    legs = next(g for g in dialog.plan["groups"] if (d.name, 12) in g["members"])
    assert len(legs["members"]) == 6 and legs.get("parent")
    assert legs["accepted"]
    # Compare the checkbox against spatial-only analysis; regenerate is explicit.
    dialog.use_features.set(False)
    dialog.options_changed()
    assert str(dialog.apply.cget("state")) == "disabled"
    dialog.analyze()
    pump(lambda: dialog.plan is not None and dialog.frame is None and not dialog.pending)
    assert not dialog.plan["use_features"]
    assert sum(len(g["members"]) for g in dialog.plan["groups"] if not g["accepted"]) == 6
    assert before == d.bytes() and not app.session.named_groups
    dialog.use_features.set(True)
    dialog.options_changed()
    dialog.analyze()
    pump(lambda: dialog.plan is not None and dialog.frame is None and not dialog.pending)
    assert dialog.plan["use_features"]
    cuffs = next(g for g in dialog.plan["groups"] if (d.name, 9) in g["members"])
    assert len(cuffs["members"]) == 2 and cuffs.get("parent")
    assert len(ticks) > 20
    groups = dialog.plan["groups"]
    pending = sum(len(g["members"]) for g in groups if not g["accepted"])
    foot_index = next(i for i, g in enumerate(groups) if (d.name, 7) in g["members"])
    foot = groups[foot_index]
    assert set(foot["members"]) == {(d.name, i) for i in (7, 30)}
    dialog.tree.selection_set(f"g:{foot_index}")
    dialog.tree.item(f"g:{foot_index}", open=True)
    app.update()
    with patch("spatial_dialog.simpledialog.askstring", return_value="鞋子"):
        dialog.rename()
    assert foot["name"] == "鞋子"
    # Checkbox toggling keeps group selection and membership visible.
    dialog.toggle()
    assert not foot["accepted"]
    dialog.tree.selection_set(f"g:{foot_index}")
    dialog.toggle()
    assert foot["accepted"]
    left_rows = [f"m:{foot_index}:{j}" for j, key in enumerate(foot["members"]) if key[1] == 30]
    dialog.tree.selection_set(left_rows)
    app.update()
    with patch("spatial_dialog.simpledialog.askstring", return_value="左鞋"):
        dialog.new_group()
    left = next(g for g in groups if g["name"] == "左鞋")
    assert set(left["members"]) == {(d.name, 30)}
    assert not set(left["members"]) & set(foot["members"])
    # Move the reviewed pair back and remove the emptied temporary suggestion.
    left_index = dialog.plan["groups"].index(left)
    dialog.tree.selection_set(f"g:{left_index}")
    app.update()
    dialog.target.set("鞋子")
    dialog.move()
    assert set(foot["members"]) == {(d.name, i) for i in (7, 30)}
    assert all(g["name"] != "左鞋" for g in dialog.plan["groups"])
    dialog.isolate.set(True)
    dialog.focus()
    pump(lambda: dialog.frame is None and not dialog.pending)
    assert dialog.renderer.selected_keys == set(foot["members"])
    dialog.geometry("900x680")
    app.update()
    assert dialog.apply.winfo_rooty() + dialog.apply.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    dialog.tree.column("reason", width=800)
    assert dialog.tree.column("reason", "width") == 800
    dialog.last_image.save(output / "spatial-shoes-preview.png")
    with patch("spatial_dialog.messagebox.showerror") as failure:
        dialog.commit()
        failure.assert_not_called()
    assert not dialog.winfo_exists()
    assert len(d.meshes) == 32 and app.session.named_groups["鞋子"] == set(foot["members"])
    after, collections = d.bytes(), app.session._collections()
    app.undo()
    assert before == d.bytes() and not app.session.named_groups
    app.redo()
    assert after == d.bytes() and collections == app.session.named_groups
    app.named_group.set("鞋子")
    app.select_named_group()
    assert app._selection_keys == set(foot["members"])
    app.apply_group(60)
    assert all(d.meshes[i].visible_condition == 60 for _, i in foot["members"])
    archive = app.session.export(output / "reviewed.zip", app._view_state())
    restored = EditorSession(ModWorkspace.open(archive))
    assert restored.named_groups == app.session.named_groups
    assert restored.document(d.name).bytes() == d.bytes()
    assert restored.embedded_view["spatial_features"] is True
    app.geometry("1120x740")
    app.update()
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    for button in (w for w in descendants(app) if w.winfo_class() == "TButton"):
        assert button.winfo_rooty() + button.winfo_height() <= app.winfo_rooty() + app.winfo_height(), button.cget("text")
        assert button.winfo_rootx() + button.winfo_width() <= app.winfo_rootx() + app.winfo_width(), button.cget("text")
    assert before_source == hashlib.sha256(Path(source).read_bytes()).hexdigest()
    assert not errors, errors
    report = {"ok": True, "subparts": 31, "accepted_groups": len(collections), "pending_parts": pending,
              "ui_ticks": len(ticks), "source_unchanged": True,
              "checks": ["cancel/read-only staging", "31 subparts", "bone/UV checkbox comparison and regeneration", "cuffs resolved by bones", "same-material feet apart from coat", "paired shoes", "review defaults", "rename/toggle/selection retention", "member transfer/new group", "isolation/focus", "minimum dialog/footer/column width", "one-step undo/redo", "batch layer", "export/reload and preference", "minimum main window"]}
    return report
