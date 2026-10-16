"""Opt-in real-window LOD checks; writes only independent test artifacts."""
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

from mod3 import ModWorkspace
from session import EditorSession
from spatial_dialog import SpatialGroupDialog
from lod_groups import suggest_lod_groups, visible_lod


def run(app, source, output, capture=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = Path(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    app._adopt_session(EditorSession(ModWorkspace.open(source)))
    before = {d.name: d.bytes() for d in app.workspace.documents}
    errors = []
    app.report_callback_exception = lambda *e: errors.append(str(e))
    def pump(predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("LOD preview timed out")
            app.update()
            time.sleep(.005)
        app.update()
    def main_ready():
        return app._render_future is None and app._redraw_job is None and not app._render_pending
    def shot(name):
        if capture:
            from PIL import ImageGrab
            app.attributes("-topmost", True)
            app.lift()
            app.update()
            time.sleep(.2)
            x, y = app.winfo_rootx(), app.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(output / name)
            app.attributes("-topmost", False)
    keys = {(d.name, m.index) for d, m in app.workspace.all_meshes()}
    families = suggest_lod_groups(app.workspace, keys)
    assert len(families) == 16
    suites = []
    for outfit in app.workspace.outfits:
        app.outfit_choice.set(next(label for label, value in app._outfit_choices.items() if value == outfit))
        app._outfit_changed()
        pump(main_ready)
        assert all(d.outfit_id == outfit and visible_lod(m, 1) for _, d, m in app.renderer.visible_entries(app.workspace))
        local = [g for g in families if app.session.document(g["document"]).outfit_id == outfit]
        assert all(len({app.session.document(n).outfit_id for n, i in g["members"]}) == 1 for g in local)
        suites.append({"outfit": outfit, "families": len(local), "members": sum(len(g["members"]) for g in local)})
    d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
    app.outfit_choice.set(next(label for label, value in app._outfit_choices.items() if value == d.outfit_id))
    app._outfit_changed()
    di = app.workspace.documents.index(d)
    app.tree.selection_set(f"m:{di}:0")
    app._tree_selected()
    cancel = SpatialGroupDialog(app, lod_only=True)
    app.update()
    cancel.close()
    pump(main_ready)
    assert before == {d.name: d.bytes() for d in app.workspace.documents}
    dialog = SpatialGroupDialog(app, lod_only=True)
    pump(lambda: dialog.plan is not None and hasattr(dialog, "last_image") and dialog.frame is None and not dialog.pending)
    assert dialog.plan["split_count"] == 0 and dialog.plan["lod_families"] == 1
    assert dialog.plan["lod_added"] == 8
    dialog.tree.selection_set("g:0")
    dialog.tree.item("g:0", open=True)
    dialog.select()
    dialog.isolate.set(True)
    dialog.focus()
    pump(lambda: dialog.frame is None and not dialog.pending)
    dialog.last_image.save(output / "lod-high-preview.png")
    dialog.lod_view.set("远距离")
    dialog.request_frame()
    pump(lambda: dialog.frame is None and not dialog.pending)
    dialog.last_image.save(output / "lod-far-preview.png")
    dialog.geometry("900x680")
    app.update()
    assert dialog.apply.winfo_rooty() + dialog.apply.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    with patch("spatial_dialog.simpledialog.askstring", return_value="腰部 · 全精度"):
        dialog.rename()
    with patch("spatial_dialog.messagebox.showerror") as failure:
        dialog.commit()
        failure.assert_not_called()
    members = {(d.name, i) for i in range(9)}
    assert app.session.named_groups["腰部 · 全精度"] == members
    assert app._selection_keys == members
    assert before == {d.name: d.bytes() for d in app.workspace.documents}
    app.isolate.set(True)
    app.focus_selected()
    app.color_mode.set("独立部件")
    for label, expected in (("高精度", {0, 1, 2, 3}), ("精度 2", {4}), ("远距离", {8}), ("全部版本", set(range(9)))):
        app.lod_view.set(label)
        app._lod_changed()
        pump(main_ready)
        assert {i for n, i in app.renderer._face_keys} == expected
        app.renderer.render(app.workspace, (700, 700)).save(output / f"view-{label}.png")
        if label in ("高精度", "远距离"):
            shot(f"editor-{label}.png")
    app.lod_view.set("高精度")
    app._lod_changed()
    app.tree.selection_set(f"m:{di}:4")
    app._tree_selected()
    assert app.lod_view.get() == "精度 2"
    assert app.tree.set(f"m:{di}:4", "lod") == "精度 2"
    app.named_group.set("腰部 · 全精度")
    app.select_named_group()
    app.apply_group(60)
    assert all(m.visible_condition == 60 for m in d.meshes)
    app.session.validate_patch()
    archive = app.session.export(output / "Garon_LOD-group-demo.zip", app._view_state())
    restored = EditorSession(ModWorkspace.open(archive))
    assert restored.named_groups == app.session.named_groups
    assert restored.document(d.name).bytes() == d.bytes()
    app.undo()
    app.undo()
    assert not app.session.named_groups
    assert before == {d.name: d.bytes() for d in app.workspace.documents}
    app.redo()
    app.redo()
    assert all(m.visible_condition == 60 for m in d.meshes)
    app.geometry("1120x740")
    app.update()
    assert app.view_button.winfo_ismapped()
    assert app.view_button.winfo_rootx() + app.view_button.winfo_width() <= app.preview.master.winfo_rootx() + app.preview.master.winfo_width()
    assert not errors, errors
    assert source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    report = {"source": str(source), "outfits": suites, "families": len(families), "waist_members": 9,
              "checks": ["cancel read-only", "unique LOD family", "merged low-detail hint", "LOD switch/render", "automatic selection precision", "minimum window", "batch Group/undo/redo", "ZIP metadata reload", "source unchanged"],
              "export": str(archive), "ok": True}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
