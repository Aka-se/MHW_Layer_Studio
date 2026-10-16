"""Exercise seed accessory review on owned staged copies in the real Tk editor."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback
from unittest.mock import patch
from tkinter import messagebox


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    from spatial_dialog import SpatialGroupDialog
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    dialog = None
    try:
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        original = {d.name: d.bytes() for d in app.workspace.documents}
        d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
        app._select_by_key((d.name, 1))
        app.update()
        dialog = SpatialGroupDialog(app)
        def wait_until(predicate):
            deadline = time.monotonic() + 60
            while not predicate() and time.monotonic() < deadline:
                app.update()
                time.sleep(.01)
            assert predicate(), "Accessory analysis timed out"
        wait_until(lambda: dialog.plan is not None and dialog.analysis is None)
        seed = (d.name, 2)
        seed_group = next(g for g in dialog.plan["groups"] if seed in g["members"])
        gi = dialog.plan["groups"].index(seed_group)
        mi = seed_group["members"].index(seed)
        def open_attachment():
            dialog.tree.selection_set(f"m:{gi}:{mi}")
            dialog.select()
            dialog.attach_button.invoke()
            child = dialog._attachment_dialog
            wait_until(lambda: child.result is not None)
            return child
        before_groups = copy.deepcopy(dialog.plan["groups"])
        child = open_attachment()
        assert child.candidates
        from dialog_geometry import window_rect, work_area
        parent_frame, child_frame, area = window_rect(dialog), window_rect(child), work_area(dialog)
        assert abs((parent_frame[0] + parent_frame[2]) - (child_frame[0] + child_frame[2])) <= 2
        assert abs((parent_frame[1] + parent_frame[3]) - (child_frame[1] + child_frame[3])) <= 2
        assert area[0] <= child_frame[0] < child_frame[2] <= area[2] and area[1] <= child_frame[1] < child_frame[3] <= area[3]
        child.tree.selection_set("0")
        child.preview()
        assert seed in dialog.renderer.selected_keys
        assert child.candidates[0]["key"] in dialog.renderer.selected_keys
        child.close()
        assert dialog.plan["groups"] == before_groups
        assert original == {d.name: d.bytes() for d in app.workspace.documents}
        child = open_attachment()
        count = len(child.candidates)
        child.percent.set("0.1")
        assert child.result is None and child.confirm.instate(["disabled"])
        child.generate()
        wait_until(lambda: child.result is not None)
        assert len(child.candidates) < count
        child.percent.set("bad")
        child.generate()
        assert child.result is None and child.confirm.instate(["disabled"])
        child.percent.set("1.5")
        child.generate()
        wait_until(lambda: child.result is not None)
        child.check_all()
        selected = child.checked.copy()
        assert selected
        old_name = child.name.get()
        duplicate = next(g["name"] for g in dialog.plan["groups"] if g is not child.target_group)
        child.name.set(duplicate)
        with patch.object(messagebox, "showerror") as invalid:
            child.commit()
            invalid.assert_called_once()
        assert dialog.plan["groups"] == before_groups
        child.name.set(old_name)
        dialog.stop.set()
        with patch.object(messagebox, "showerror") as stale:
            child.commit()
            stale.assert_called_once()
        assert dialog.plan["groups"] == before_groups
        dialog.stop.clear()
        row = f"g:{dialog.plan['groups'].index(seed_group)}"
        dialog.tree.item(row, open=False)
        child.commit()
        assert seed in seed_group["members"] and selected <= set(seed_group["members"])
        row = f"g:{dialog.plan['groups'].index(seed_group)}"
        assert not dialog.tree.item(row, "open")
        assert original == {d.name: d.bytes() for d in app.workspace.documents}
        group_name = seed_group["name"]
        final_groups = copy.deepcopy(dialog.plan["groups"])
        dialog.commit()
        assert app.session.named_groups[group_name] == set(seed_group["members"])
        after = {d.name: d.bytes() for d in app.workspace.documents}
        app.undo()
        assert original == {d.name: d.bytes() for d in app.workspace.documents}
        app.redo()
        assert after == {d.name: d.bytes() for d in app.workspace.documents}
        for d in app.workspace.documents:
            assert all(m.visible_condition == 0 for m in d.meshes)
        with tempfile.TemporaryDirectory(dir=output, prefix="owned-attachment-") as temp:
            assert Path(temp).resolve().is_relative_to(output)
            saved = app.session.export(Path(temp) / "split.zip")
            reopened = EditorSession(ModWorkspace.open(saved))
            assert reopened.named_groups == app.session.named_groups
            assert after == {d.name: d.bytes() for d in reopened.workspace.documents}
        # Existing manual groups can enter directly without another split/regroup.
        kept = copy.deepcopy(app.session.named_groups)
        app._select_by_key(seed)
        index = next(i for i in range(app.group_menu.index("end") + 1)
                     if app.group_menu.type(i) == "command" and app.group_menu.entrycget(i, "label") == "关联附近附件…")
        app.group_menu.invoke(index)
        dialog = next(w for w in app.winfo_children() if isinstance(w, SpatialGroupDialog) and not w.closed)
        wait_until(lambda: getattr(dialog, "_attachment_dialog", None) is not None
                   and dialog._attachment_dialog.result is not None)
        assert dialog.attachment_only and dialog.plan["split_count"] == 0
        assert not any(g["accepted"] for g in dialog.plan["groups"])
        staged_groups = {g["name"]: set(g["members"]) for g in dialog.plan["groups"]}
        assert all(staged_groups[name] == members for name, members in kept.items())
        dialog._attachment_dialog.close()
        dialog.close()
        assert app.session.named_groups == kept
        assert after == {d.name: d.bytes() for d in app.workspace.documents}
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == digest
        assert not errors, errors
        report = {"ok": True, "version": app.title(), "source_unchanged": True,
                  "candidates": count, "confirmed_accessories": len(selected), "groups_after": len(final_groups),
                  "checks": ["seed preview", "candidate click preview", "cancel leaves staging unchanged",
                             "radius invalidates old proposals", "smaller radius narrows candidates", "invalid radius blocked",
                             "duplicate target rejected atomically", "stale plan rejected", "confirm changes only staged groups",
                             "target remains collapsed", "one-step apply undo redo", "Group IDs unchanged", "ZIP reopen",
                             "direct group menu entry preserves manual groups without splitting"]}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc(), "callbacks": errors}
    finally:
        if dialog is not None and not dialog.closed:
            dialog.close()
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
