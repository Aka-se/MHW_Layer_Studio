"""Owned-copy checks of the Garon pilot, including the manual review flow."""
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

from mod3 import ModWorkspace
from session import EditorSession
from spatial_dialog import SpatialGroupDialog
from renderer import SoftwareRenderer


def run(app, source, output, capture=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = Path(source)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    app._adopt_session(EditorSession(ModWorkspace.open(source)))
    d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
    di = app.workspace.documents.index(d)
    before = {doc.name: doc.bytes() for doc in app.workspace.documents}
    original_renderer = SoftwareRenderer()
    original_renderer.active_outfit = d.outfit_id
    original_renderer.enabled_parts = {"wst"}
    original_renderer.fit(app.workspace)
    baseline_masks = {}
    for bit in (1, 2, 4, 8, 16, 32):
        original_renderer.lod_bit = bit
        original_renderer.render(app.workspace, (700, 700))
        baseline_masks[bit] = original_renderer._pixel_ids >= 0
    errors = []
    app.report_callback_exception = lambda *e: errors.append(str(e))
    def pump(predicate, timeout=45):
        until = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > until:
                raise AssertionError("LOD sync UI timed out")
            app.update()
            time.sleep(.004)
        app.update()
    def ready():
        return app._render_future is None and app._redraw_job is None and not app._render_pending
    def shot(window, name):
        if capture:
            from PIL import ImageGrab
            window.attributes("-topmost", True)
            window.lift()
            window.update()
            time.sleep(.2)
            x, y = window.winfo_rootx(), window.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(output / name)
            window.attributes("-topmost", False)
    app.tree.selection_set(f"m:{di}:0")
    app._tree_selected()
    app.renderer.hidden_keys = {(d.name, 4)}
    cancel = SpatialGroupDialog(app, sync_lod=True)
    app.update()
    cancel.close()
    pump(ready)
    assert before == {doc.name: doc.bytes() for doc in app.workspace.documents}
    dialog = SpatialGroupDialog(app, sync_lod=True)
    dialog.geometry("1350x860+35+35")
    def dialog_ready():
        return dialog.plan is not None and hasattr(dialog, "last_image") and dialog.frame is None and not dialog.pending
    pump(dialog_ready)
    plan = dialog.plan
    assert plan["split_count"] == 5
    assert len(d.meshes) == 9 and len(plan["staged"].document(d.name).meshes) == 27
    pending = [g for g in plan["groups"] if not g["accepted"]]
    assert len(pending) == 3
    with patch("spatial_dialog.messagebox.showerror") as blocked:
        dialog.commit()
        blocked.assert_called_once()
    assert before == {doc.name: doc.bytes() for doc in app.workspace.documents}
    assert not app.session.named_groups
    review = []
    for group in pending:
        index = dialog.plan["groups"].index(group)
        dialog.tree.selection_set(f"g:{index}")
        dialog.tree.item(f"g:{index}", open=True)
        dialog.isolate.set(True)
        dialog.compare_candidate.set(True)
        dialog.select()
        dialog.focus()
        pump(dialog_ready)
        assert dialog.renderer.comparison_keys
        assert set((doc.name, m.index) for _, doc, m in dialog.renderer.visible_entries(plan["staged"].workspace)) == dialog.selected_keys() | dialog.renderer.comparison_keys
        review.append({"members": group["members"], "candidate": group["candidate_reference"],
                       "evidence": list(group["evidence"].values()), "review": "test example accepts candidate; game validation remains required"})
        dialog.last_image.save(output / f"candidate-{len(review)}.png")
        shot(dialog, f"review-{len(review)}.png")
        target = dialog.target.get()
        dialog.compare_candidate.set(False)
        dialog.select()
        assert dialog.target.get() == target
        dialog.move()  # Explicit test-example confirmation, not an automatic rule.
    assert len(dialog.plan["groups"]) == 4
    assert all(g["accepted"] for g in dialog.plan["groups"])
    dialog.tree.selection_set("g:0")
    dialog.select()
    dialog.lod_view.set("高精度")
    dialog.focus()
    pump(dialog_ready)
    dialog.last_image.save(output / "group-high.png")
    shot(dialog, "group-high-window.png")
    dialog.lod_view.set("远距离")
    dialog.request_frame()
    pump(dialog_ready)
    dialog.last_image.save(output / "group-far.png")
    shot(dialog, "group-far-window.png")
    dialog.geometry("900x680")
    app.update()
    assert dialog.apply.winfo_rooty() + dialog.apply.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    with patch("spatial_dialog.messagebox.showerror") as fail:
        dialog.commit()
        fail.assert_not_called()
    assert len(d.meshes) == 27 and len(app.session.named_groups) == 4
    assert app.renderer.hidden_keys == {(d.name, i) for i in (4, 5, 6, 7)}
    collections = app.session._collections()
    after = d.bytes()
    app.undo()
    assert len(d.meshes) == 9 and before[d.name] == d.bytes()
    assert app.renderer.hidden_keys == {(d.name, 4)}
    app.redo()
    assert after == d.bytes() and collections == app.session.named_groups
    assert app.renderer.hidden_keys == {(d.name, i) for i in (4, 5, 6, 7)}
    # Every detail version has exactly the same coverage after partitioning.
    coverage = {}
    for bit, baseline in baseline_masks.items():
        original_renderer.lod_bit = bit
        original_renderer.render(app.workspace, (700, 700))
        changed = int(((original_renderer._pixel_ids >= 0) != baseline).sum())
        assert changed == 0, (bit, changed)
        coverage[str(bit)] = changed
    assert all(doc.bytes() == before[doc.name] for doc in app.workspace.documents if doc.name != d.name)
    app.show_all()
    app.named_group.set(next(iter(collections)))
    app.select_named_group()
    app.isolate.set(True)
    app.focus_selected()
    app.apply_group(60)
    app.simulate_health.set(True)
    for label in ("高精度", "精度 2", "精度 3", "精度 4", "精度 5", "远距离"):
        app.lod_view.set(label)
        for hp in (100, 80, 81):
            app.health.set(hp)
            app._lod_changed()
            pump(ready)
            assert bool(app.renderer.visible_entries(app.workspace)) == (hp > 80)
    app.session.validate_patch()
    archive = app.session.export(output / "Garon-wst-LOD-sync-test.zip", app._view_state())
    project = output / "Garon-wst-LOD-sync-test.mhwlayer.json"
    app.session.save(project, app._view_state())
    restored, view = EditorSession.open(project)
    assert d.bytes() == restored.document(d.name).bytes()
    assert app.session.named_groups == restored.named_groups
    restored = EditorSession(ModWorkspace.open(archive))
    assert d.bytes() == restored.document(d.name).bytes()
    assert app.session.named_groups == restored.named_groups
    app.geometry("1120x740")
    app.update()
    def widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from widgets(child)
    for button in (w for w in widgets(app) if w.winfo_class() == "TButton"):
        assert button.winfo_rooty() + button.winfo_height() <= app.winfo_rooty() + app.winfo_height(), button.cget("text")
    assert not errors, errors
    assert source_sha == hashlib.sha256(source.read_bytes()).hexdigest()
    report = {"ok": True, "version": app.title().split(" · ")[0], "file": d.name, "source_unchanged": True,
              "meshes_before": 9, "meshes_after": 27, "control_groups": 4, "manual_review_blocks": review,
              "coverage_changed_pixels_by_lod": coverage, "export": str(archive), "project": str(project),
              "game_verified": False, "checks": ["cancel/read-only", "review blocks apply until confirmed", "candidate ghost comparison", "member move", "one-step undo/redo and hidden remap", "all LOD coverage", "all LOD health/recovery", "ZIP reopen", "exact project replay", "other documents unchanged", "minimum window", "source hash"]}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
