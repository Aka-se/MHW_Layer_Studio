"""Exercise real Tk widgets on Windows; write reviewable copies beside the project."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import LayerStudio, APP_VERSION
from mod3 import ModWorkspace, generate_health_rules
from session import EditorSession


def main():
    output = ROOT / "validation-output"
    output.mkdir(exist_ok=True)
    sample = ROOT / "暴雨魔女.zip"
    before = hashlib.sha256(sample.read_bytes()).hexdigest()
    app = LayerStudio()
    callback_errors = []
    app.report_callback_exception = lambda *error: callback_errors.append(str(error))
    started = time.perf_counter()
    try:
        app.update()
        app.geometry("1120x740")
        app.update()
        assert app.preview.winfo_width() >= 250 and app.preview.winfo_height() >= 250
        def buttons(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "TButton":
                    yield child
                yield from buttons(child)
        for button in buttons(app):
            assert button.winfo_rootx() + button.winfo_width() <= app.winfo_rootx() + app.winfo_width(), (
                button.cget("text"), button.winfo_rootx(), button.winfo_width(), app.winfo_rootx(), app.winfo_width())
            assert button.winfo_rooty() + button.winfo_height() <= app.winfo_rooty() + app.winfo_height(), button.cget("text")
        app.geometry("1500x900")
        app.update_idletasks()
        app._load(str(sample))
        app.update()
        assert app.workspace.mesh_count == 36
        body = next(d for d in app.workspace.documents if "/body/" in d.name)
        waist = next(d for d in app.workspace.documents if d.equipment_part == "wst")
        app._select_by_key((body.name, 0))
        app.update()
        app.apply_group(60)
        assert body.meshes[0].visible_condition == 60
        app.simulate_health.set(True)
        app.health.set(75)
        app._health_changed("75")
        app._redraw(synchronous=True)
        assert not any(n == body.name and i == 0 for _, n, i, _ in app.renderer._pick_faces)
        app.health.set(100)
        app._health_changed("100")
        app._redraw(synchronous=True)
        assert any(n == body.name and i == 0 for _, n, i, _ in app.renderer._pick_faces)
        face = app.renderer._pick_faces[-1]
        point = face[3].mean(axis=0)
        expected_pick = app.renderer.pick(int(point[0]), int(point[1]))
        assert expected_pick is not None
        app.tree.selection_remove(*app.tree.selection())
        app.renderer.selected = None
        app.preview.event_generate("<ButtonPress-1>", x=int(point[0]), y=int(point[1]))
        app.preview.event_generate("<ButtonRelease-1>", x=int(point[0]), y=int(point[1]))
        app.update()
        assert app.renderer.selected == expected_pick
        app.preview.event_generate("<ButtonPress-1>", x=int(point[0]), y=int(point[1]), state=4)
        app.preview.event_generate("<ButtonRelease-1>", x=int(point[0]), y=int(point[1]), state=4)
        app.update()
        assert expected_pick not in app.renderer.selected_keys
        old_yaw = app.renderer.yaw
        app.preview.event_generate("<ButtonPress-3>", x=100, y=100)
        app.preview.event_generate("<B3-Motion>", x=130, y=110)
        app.preview.event_generate("<ButtonRelease-3>", x=130, y=110)
        app.update()
        assert app.renderer.yaw != old_yaw
        old_zoom = app.renderer.zoom
        app.preview.event_generate("<MouseWheel>", delta=120)
        app.update()
        assert app.renderer.zoom > old_zoom
        app._select_by_key((body.name, 0))
        app._select_by_key((waist.name, 0), additive=True)
        assert len(app._selected_meshes()) == 2
        app.apply_group(61)
        assert body.meshes[0].visible_condition == waist.meshes[0].visible_condition == 61
        app.undo()
        assert body.meshes[0].visible_condition == 60
        assert waist.meshes[0].visible_condition == waist.original_group(0)
        app.redo()
        assert body.meshes[0].visible_condition == waist.meshes[0].visible_condition == 61
        app.undo()
        before_hidden = [document.bytes() for document in app.workspace.documents]
        app.hide_selected()
        app._redraw(synchronous=True)
        assert not any((n, i) in {(body.name, 0), (waist.name, 0)} for _, n, i, _ in app.renderer._pick_faces)
        app.show_all()
        assert before_hidden == [document.bytes() for document in app.workspace.documents]
        app.part_vars["wst"].set(False)
        app._parts_changed()
        app._redraw(synchronous=True)
        assert not any("/wst/" in n for _, n, _, _ in app.renderer._pick_faces)
        app.show_all()
        app.preview.event_generate("<ButtonPress-2>", x=100, y=100)
        app.preview.event_generate("<B2-Motion>", x=140, y=120)
        app.preview.event_generate("<ButtonRelease-2>", x=140, y=120)
        assert app.renderer.pan_x > 0
        app.reset_view()
        app._select_by_key((body.name, 0))
        app.isolate.set(True)
        app._isolate_changed()
        app._redraw(synchronous=True)
        assert all(n == body.name and i == 0 for _, n, i, _ in app.renderer._pick_faces)
        center = (body.meshes[0].vertices.min(axis=0) + body.meshes[0].vertices.max(axis=0)) / 2
        assert abs(app.renderer.camera_center - center).max() < .0001
        app.renderer.render(app.workspace, (800, 800)).save(output / "body-mesh-0.png")
        app.ghost.set(True)
        app.isolate.set(False)
        app._isolate_changed()
        app._redraw(synchronous=True)
        app.renderer.render(app.workspace, (800, 800)).save(output / "ghost-selected.png")
        app.ghost.set(False)
        app._redraw(synchronous=True)
        app.renderer.render(app.workspace, (800, 800)).save(output / "full-model.png")
        render_started = time.perf_counter()
        app.renderer.render(app.workspace, (800, 800))
        render_seconds = time.perf_counter() - render_started
        project = output / f"v02-project-{time.time_ns()}.mhwlayer.json"
        app.isolate.set(True)
        app._isolate_changed()
        view = app._view_state()
        with patch("app.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        restored, restored_view = EditorSession.open(project)
        assert restored.workspace.documents[1].name == app.workspace.documents[1].name
        assert restored_view == view
        app._load_project(str(project))
        app._redraw(synchronous=True)
        assert app.isolate.get() and app._selection_keys == {(body.name, 0)}
        assert "材质" in app.info_text.get("1.0", "end")
        app.session.apply_group({(body.name, 0)}, 0)
        assert not app.workspace.has_unexported_changes and app.session.project_dirty
        with patch("app.messagebox.askyesno", return_value=False) as confirmation:
            assert not app._confirm_discard()
            confirmation.assert_called_once()
        app.session.undo()
        app._refresh_groups()
        app.export_mod()
        app.update_idletasks()
        review = next(child for child in app.winfo_children() if child.winfo_class() == "Toplevel")
        text = next(child for child in review.winfo_children() if child.winfo_class() == "Text")
        assert "0 → 60" in text.get("1.0", "end")
        review.destroy()
        # Verify the normal asynchronous preview path reaches a frame without blocking Tk.
        app.show_all()
        app.schedule_redraw()
        deadline = time.perf_counter() + 30
        while (app._redraw_job or app._render_future or app._render_pending) and time.perf_counter() < deadline:
            app.update()
            time.sleep(.01)
        assert app._render_future is None and app._redraw_job is None
        # Never overwrite output from a previous validation run.
        archive = output / f"暴雨魔女_body_mesh0_group60_{time.time_ns()}.zip"
        app.workspace.export(archive)
        reopened = ModWorkspace.open(archive)
        assert next(d for d in reopened.documents if d.name == body.name).meshes[0].visible_condition == 60
        assert not app.workspace.has_unexported_changes
        (output / "MHWLayerStudio_health_rules.ini").write_text(generate_health_rules(), encoding="utf-8-sig")
        assert before == hashlib.sha256(sample.read_bytes()).hexdigest()
        assert not callback_errors, callback_errors
        report = {
            "app_version": APP_VERSION,
            "python": sys.version,
            "mod3_count": len(app.workspace.documents),
            "mesh_count": app.workspace.mesh_count,
            "archive": str(archive),
            "project": str(project),
            "source_sha256": before,
            "render_800x800_seconds": round(render_seconds, 3),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "preview_size": [app.preview.winfo_width(), app.preview.winfo_height()],
            "tk_scaling": app.tk.call("tk", "scaling"),
            "checks": ["Tk startup", "sample load", "list selection", "Group assignment",
                       "75 percent hide", "100 percent restore", "click event", "drag event",
                       "wheel event", "Ctrl click toggle", "batch assign", "batch undo and redo",
                       "temporary hide without file changes", "equipment part toggle", "right-drag pan",
                       "isolation auto-focus", "project save and reopen", "async preview",
                       "MRL3 material information", "1120x740 layout", "project dirty prompt after restore",
                       "export change review", "export and reopen", "original SHA256 unchanged"],
            "limitations": ["Programmatic widget validation; no manual visual UI inspection",
                            "Body Mesh 0 is a test candidate, not an identified decoration",
                            "v0.1 game behavior was verified by user; v0.2 export not tested in game by this script"],
        }
        (output / "windows-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
