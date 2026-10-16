"""Opt-in real Tk inventory checks, with a private settings profile."""
import hashlib
import json
from pathlib import Path
import time
import traceback
from unittest.mock import patch
import threading


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    from mod_library import scan_library, saved_library_root
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    dialog = None
    try:
        app.geometry("1280x800")
        errors = []
        app.report_callback_exception = lambda *args: errors.append(str(args))
        root, expected, scan_errors = scan_library(source)
        assert expected
        app.mod_library_directory = str(root)
        app.mod_library_button.invoke()
        dialog = app._mod_library_dialog
        app.update()  # Start the dialog's after_idle initial scan.
        deadline = time.monotonic() + 40
        while dialog.future is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        assert dialog.future is None and len(dialog.records) == len(expected)
        dialog.geometry("760x480")
        app.update()
        assert dialog.open_button.winfo_rooty() + dialog.open_button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
        assert dialog.open_button.winfo_rootx() + dialog.open_button.winfo_width() <= dialog.winfo_rootx() + dialog.winfo_width()
        assert saved_library_root() == str(root)
        # MHW Box opens the selected files directory, not the whole mod library.
        target = min((r for r in expected if r.kind == "文件夹"), key=lambda r: (r.model_count, str(r.path)))
        files = [p for p in target.path.rglob("*") if p.is_file() and p.suffix.lower() in (".mod3", ".mrl3")]
        original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        info = target.path.parent / "info.xml"
        info_bytes = info.read_bytes() if info.exists() else None
        dialog.query.set(target.relative)
        app.update()
        row = next(iid for iid, record in dialog.rows.items() if record.path == target.path)
        dialog.tree.selection_set(row)
        dialog.select()
        assert str(dialog.open_button.cget("state")) == "normal"
        item_count = dialog._next_item
        with patch.object(dialog.tree, "insert", wraps=dialog.tree.insert) as insert, patch.object(dialog.tree, "delete", wraps=dialog.tree.delete) as delete:
            for query in ("not-a-real-mod-name", "", target.relative, "", target.name):
                dialog.query.set(query)
                app.update()
            assert not insert.called and not delete.called
        assert dialog._next_item == item_count
        dialog.query.set(target.relative)
        app.update()
        assert len(dialog.rows) == len(dialog.tree.get_children())
        # Invalid new root keeps the remembered location and active session intact.
        dialog.directory.set(str(root / "no-such-folder"))
        dialog.refresh()
        while dialog.future is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        assert saved_library_root() == str(root)
        assert app.session is None
        dialog.directory.set(str(root))
        dialog.refresh()
        while dialog.future is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        row = next(iid for iid, record in dialog.rows.items() if record.path == target.path)
        dialog.tree.selection_set(row)
        dialog.select()
        # Existing discard handling is used; cancellation preserves the window.
        with patch.object(app, "_confirm_discard", return_value=False):
            dialog.open_button.invoke()
        assert not dialog.closed and app.session is None
        with patch.object(app, "_confirm_discard", return_value=True):
            dialog.tree.event_generate("<KeyPress-Return>")
            # Focus-less synthetic key events are not delivered on all desktops.
            if not dialog.closed:
                dialog.open_button.invoke()
        assert dialog.closed and app.workspace.source.resolve() == target.path.resolve()
        assert len(app.workspace.documents) == target.model_count
        assert target.name in app.title()
        assert original == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        if info_bytes is not None:
            assert info_bytes == info.read_bytes()
        reopen_times = {}
        for cache_kind in ("memory", "saved_index"):
            if cache_kind == "saved_index":
                app._mod_library_cache.clear()
            gate = threading.Event()
            def delayed_scan(*_args):
                gate.wait(5)
                return root, expected, scan_errors
            with patch("mod_library_dialog.scan_library", side_effect=delayed_scan):
                started = time.perf_counter()
                app.mod_library_button.invoke()
                reopened = app._mod_library_dialog
                try:
                    app.update()
                    reopen_times[cache_kind] = round(time.perf_counter() - started, 4)
                    assert reopened.directory.get() == str(root)
                    assert len(reopened.rows) == len(expected)
                    assert reopened.future is not None and not reopened.future.done()
                    assert "上次索引" in reopened.scan_text.get()
                    assert reopened.scan_label.winfo_ismapped() and reopened.scan_bar.winfo_ismapped()
                    reopened.query.set(target.relative)
                    app.update()
                    assert any(r.path == target.path for r in reopened.rows.values())
                finally:
                    gate.set()
                    reopened.close()
        assert not errors, errors
        report = {"ok": True, "version": app.title(), "root": str(root), "mods": len(expected),
                  "cached_reopen_seconds": reopen_times,
                  "scan_warnings": len(scan_errors), "selected": target.name, "selected_models": target.model_count,
                  "checks": ["Box names and files payload", "custom root persistence", "search and open", "invalid root keeps settings",
                             "discard cancellation", "reopen remembered root", "minimum size actions reachable", "source models and Box info unchanged",
                             "search reuses existing Tk rows without insert/delete", "cached rows immediately searchable during background refresh"]}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    finally:
        if app._mod_library_dialog and not app._mod_library_dialog.closed:
            app._mod_library_dialog.close()
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
