"""Exercise parent/side grouping in owned actual Tk widgets, source or frozen."""
import hashlib
import json
from pathlib import Path
import time
import traceback
from unittest.mock import patch
from tkinter import simpledialog


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    from spatial_dialog import SpatialGroupDialog
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    errors, dialog = [], None
    app.report_callback_exception = lambda *args: errors.append(str(args))
    def wait(predicate):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            app.update()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("Parent grouping UI did not finish")
    try:
        before_source = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
        before = d.bytes()
        app._select_by_key((d.name, 1))
        dialog = SpatialGroupDialog(app)
        wait(lambda: dialog.plan is not None and dialog.analysis is None)
        left = next(g for g in dialog.plan["groups"] if (d.name, 12) in g["members"])
        right = next(g for g in dialog.plan["groups"] if (d.name, 15) in g["members"])
        assert left["parent"] == right["parent"] and len(left["members"]) == len(right["members"]) == 6
        both = set(left["members"]) | set(right["members"])
        parent_row = next(row for row, indices in dialog.parent_rows.items()
                          if dialog.plan["groups"].index(left) in indices)
        left_row = f"g:{dialog.plan['groups'].index(left)}"
        dialog.tree.selection_set(parent_row)
        dialog.select()
        assert dialog.selected_keys() == both
        with patch("spatial_dialog.simpledialog.askstring", return_value="腿部装饰"):
            dialog.rename()
        assert left["parent"] == right["parent"] == "腿部装饰"
        dialog.tree.selection_set(left_row)
        dialog.toggle()
        assert not left["accepted"] and right["accepted"]
        assert dialog.tree.item(parent_row, "text").startswith("▣")
        dialog.tree.selection_set(parent_row)
        dialog.toggle()
        assert left["accepted"] and right["accepted"]
        dialog.tree.item(parent_row, open=False)
        dialog.tree.item(left_row, open=False)
        key = (d.name, 27)
        dialog.transfer({key}, right)
        dialog.transfer({key}, left)
        assert not dialog.tree.item(parent_row, "open")
        assert not dialog.tree.item(left_row, "open")
        assert before == d.bytes()
        dialog.commit()
        assert dialog.closed and app.session.group_members("腿部装饰") == both
        app.undo()
        assert before == d.bytes() and not app.session.parent_groups
        app.redo()
        assert app.session.group_members("腿部装饰") == both
        # Canvas selection drills to the side first, then the individual member.
        app._last_group_pick = None
        app._select_by_key((d.name, 12), select_group=True)
        assert app._selection_keys == set(left["members"])
        app._select_by_key((d.name, 12), select_group=True)
        assert app._selection_keys == {(d.name, 12)}
        parent_row = next(iid for iid, name in app._group_iids.items() if name == "腿部装饰")
        left_row = next(iid for iid, name in app._group_iids.items() if name == left["name"])
        assert app.tree.parent(left_row) == parent_row
        assert app.tree.item(left_row, "text").startswith("左侧")
        app.named_group.set("腿部装饰")
        app.select_named_group()
        assert app._selection_keys == both
        app.apply_group(60)
        app.named_group.set(left["name"])
        app.select_named_group()
        app.apply_group(62)
        assert {d.meshes[i].visible_condition for n, i in left["members"]} == {62}
        assert {d.meshes[i].visible_condition for n, i in right["members"]} == {60}
        assert app.tree.item(parent_row, "values")[3] == "混合"
        app.health.set(60)
        app._health_changed("60")
        assert {app._mesh_visibility(d, d.meshes[i]) for n, i in left["members"]} == {"显示"}
        assert {app._mesh_visibility(d, d.meshes[i]) for n, i in right["members"]} == {"因血量隐藏"}
        app.undo()
        app.undo()
        app.session.validate_patch()
        app.health.set(100)
        app._health_changed("100")
        app.named_group.set("腿部装饰")
        app.select_named_group()
        app.tree.item(parent_row, open=True)
        app.ghost.set(True)
        app.color_mode.set("独立部件")
        app.focus_selected()
        app._sync_preview_controls()
        view = app._view_state()
        project = output / "Kuroki-EX2-parent-groups.mhwlayer.json"
        app.session.save(project, view)
        restored, restored_view = EditorSession.open(project)
        assert restored.parent_groups == app.session.parent_groups
        archive = app.session.export(output / "parent-groups-demo.zip", view)
        reopened = EditorSession(ModWorkspace.open(archive))
        assert reopened.parent_groups == app.session.parent_groups
        assert reopened.named_groups == app.session.named_groups
        assert reopened.document(d.name).bytes() == d.bytes()
        # Dissolve only the parent, undo restores its links and no geometry moves.
        groups = app.session._collections()
        app.session.edit_named_group("delete", "腿部装饰")
        assert "腿部装饰" not in app.session.parent_groups and app.session.named_groups == groups
        app._refresh_groups()
        app.tree.selection_set([iid for iid, name in app._group_iids.items()
                                if name in (left["name"], right["name"])])
        app._tree_selected()
        with patch.object(simpledialog, "askstring", return_value="手工父组"):
            app.create_parent_group()
        assert app.session.group_members("手工父组") == both
        app.undo()
        app.undo()
        assert app.session.group_members("腿部装饰") == both
        # Search by a child still exposes the parent and both sides for clarity.
        app.group_search.set(left["name"])
        app._populate_tree()
        assert {"腿部装饰", left["name"], right["name"]} <= set(app._group_iids.values())
        app.group_search.set("")
        app._populate_tree()
        app.named_group.set("腿部装饰")
        app.select_named_group()
        app._refresh_groups()
        wait(lambda: app._render_future is None and app._redraw_job is None)
        assert not errors, errors
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before_source
        report = {"ok": True, "version": app.title(), "parents": len(app.session.parent_groups),
                  "leg_sides": [len(left["members"]), len(right["members"])], "parent_members": len(both),
                  "source_unchanged": True, "project": str(project), "checks": [
                      "staged parent with independent sides", "parent rename", "mixed check state",
                      "parent toggles children", "transfer preserves folds", "atomic apply undo redo",
                      "canvas side then member", "main tree parent child", "parent batch layer",
                      "single side layer", "mixed parent layer", "independent HP simulation",
                      "project and ZIP hierarchy roundtrip", "dissolve keeps children", "manual parent creation from selected groups",
                      "child search retains parent"]}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc(), "callbacks": errors}
    finally:
        if dialog is not None and not dialog.closed:
            dialog.close()
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
