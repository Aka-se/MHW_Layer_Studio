"""Check actual native dialog frames against moved owners, without committing edits."""
import hashlib
import json
from pathlib import Path
import tkinter as tk
import traceback


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    from health_dialog import HealthThresholdDialog
    from mod_library_dialog import ModLibraryDialog
    from dialog_geometry import window_rect, work_area
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        document = next(d for d in app.workspace.documents if d.equipment_part == "wst")
        app._select_by_key((document.name, 1))
        original = [d.bytes() for d in app.workspace.documents]
        app.mod_library_directory = ""
        factories = [app.edit_mesh_annotations, app.auto_group_materials, app.show_texture,
                     app.export_mod, lambda: HealthThresholdDialog(app), lambda: ModLibraryDialog(app)]
        for x, y in ((160, 90), (620, 270)):
            app.geometry(f"1120x740+{x}+{y}")
            app.update_idletasks()
            for create in factories:
                before = set(app.winfo_children())
                create()
                app.update_idletasks()
                dialog = next(w for w in app.winfo_children() if w not in before and isinstance(w, tk.Toplevel))
                parent, child, area = window_rect(app), window_rect(dialog), work_area(app)
                w, h = child[2] - child[0], child[3] - child[1]
                expected_x = max(area[0] + 12, min(round((parent[0] + parent[2] - w) / 2), area[2] - w - 12))
                expected_y = max(area[1] + 12, min(round((parent[1] + parent[3] - h) / 2), area[3] - h - 12))
                assert abs(child[0] - expected_x) <= 2 and abs(child[1] - expected_y) <= 2, (dialog.title(), child, parent)
                assert area[0] <= child[0] < child[2] <= area[2] and area[1] <= child[1] < child[3] <= area[3]
                assert str(dialog.transient()) == str(app), (dialog.title(), str(dialog.transient()), str(app))
                results.append({"title": dialog.title(), "parent": parent, "frame": child, "work_area": area})
                if isinstance(dialog, ModLibraryDialog):
                    dialog.close()
                else:
                    dialog.destroy()
        assert original == [d.bytes() for d in app.workspace.documents]
        assert digest == hashlib.sha256(Path(source).read_bytes()).hexdigest()
        assert not errors, errors
        report = {"ok": True, "version": app.title(), "dialogs": results, "source_unchanged": True}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
