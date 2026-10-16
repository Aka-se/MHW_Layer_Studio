"""Exercise grouping and save shortcuts using owned copies, never installed mods."""
import hashlib
import json
from pathlib import Path
import tempfile
import traceback
import zipfile
import numpy as np
from unittest.mock import patch
from tkinter import filedialog, messagebox


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        app.geometry("1280x800")
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        d = next(d for d in app.workspace.documents if d.equipment_part == "wst")
        app.session.split_mesh(d.name, 1)
        d = app.session.document(d.name)
        members = {(d.name, 1), (d.name, 2)}
        target = {(d.name, 0)}
        app.session.apply_group(target, 61)
        app.session.edit_named_group("create", "待整理", members)
        app.session.edit_named_group("create", "目标组", target)
        app.list_mode.set("按部件组")
        app._refresh_groups()
        app.named_group.set("待整理")
        app.select_named_group()
        assert app.selection_summary.get() == "整组：待整理 · 2 个成员"
        key = next(iter(members))
        app._select_by_key(key, select_group=True)
        app._select_by_key(key, select_group=True)
        assert app._selection_keys == {key}
        assert app.selection_summary.get().startswith("单个成员：")
        assert "所属组：待整理" in app.selection_summary.get()
        assert "单个成员：" in app._preview_caption()
        # A locating dropdown is not evidence of actual membership.
        app.named_group.set("目标组")
        app._update_selection_summary()
        assert "所属组：待整理" in app.selection_summary.get()
        app.named_group.set("待整理")
        app.select_named_group()
        source_row = app.tree.selection()[0]
        target_row = next(iid for iid, name in app._group_iids.items() if name == "目标组")
        app.tree.selection_set((source_row, target_row))
        app._tree_selected()
        assert app.selection_summary.get() == "多选：2 个组 · 共 3 个部件"
        app.named_group.set("待整理")
        app.select_named_group()
        before = d.bytes()
        row = next(iid for iid, name in app._group_iids.items() if name == "目标组")
        app.tree.item(row, open=False)
        assert not app.join_group_button.instate(["disabled"])
        app._populate_join_group_menu()
        assert app._selection_keys == members
        def menu_entries():
            return {i: app.join_group_menu.entrycget(i, "label") for i in range(app.join_group_menu.index("end") + 1)
                    if app.join_group_menu.type(i) == "command"}
        index = next(i for i, label in menu_entries().items() if label.startswith("目标组 ·"))
        current = next(i for i, label in menu_entries().items() if label.startswith("待整理 ·"))
        assert "当前组" in menu_entries()[current]
        assert app.join_group_menu.entrycget(current, "state") == "disabled"
        app.join_group_menu.invoke(index)
        app.update()
        assert app.session.named_groups["目标组"] == members | target
        assert d.bytes() == before
        row = next(iid for iid, name in app._group_iids.items() if name == "目标组")
        assert not app.tree.item(row, "open")
        app._populate_join_group_menu()
        assert app.join_group_menu.entrycget(0, "label").startswith("最近 · 目标组 ·")
        assert app.join_group_menu.entrycget(0, "state") == "disabled"
        app.undo()
        assert app.session.named_groups["待整理"] == members
        assert app.session.named_groups["目标组"] == target
        app.named_group.set("待整理")
        app.select_named_group()
        app._populate_join_group_menu()
        assert app.join_group_menu.entrycget(0, "state") == "normal"
        assert app.join_group_menu.entrycget(0, "label").startswith("最近 · 目标组 ·")
        app.redo()
        assert app.session.named_groups["目标组"] == members | target

        def shortcut(sequence="<Control-KeyPress-s>"):
            app.preview.focus_force()
            app.update()
            app.preview.event_generate(sequence)
            app.update()
            assert app._export_dialog.winfo_exists()
            return app._export_dialog

        def proceed(dialog):
            text = next(w for w in dialog.winfo_children() if w.winfo_class() == "Text")
            text.focus_force()
            app.update()
            text.event_generate("<Return>")
            app.update()
            assert not dialog.winfo_exists()

        # All compressed inputs dispatch to export; project saving is explicit.
        kind = app.workspace.kind
        for archive_kind in ("zip", "rar", "7z", "mod3"):
            app.workspace.kind = archive_kind
            with patch.object(app, "export_mod") as export, patch.object(app, "overwrite_mod") as overwrite, patch.object(app, "save_project") as project:
                app.preview.focus_force()
                app.update()
                app.preview.event_generate("<Control-KeyPress-s>")
                app.update()
                export.assert_called_once_with()
                overwrite.assert_not_called()
                project.assert_not_called()
        app.workspace.kind = kind
        with tempfile.TemporaryDirectory(dir=output, prefix="owned-save-") as temp:
            work = Path(temp)
            assert work.resolve().is_relative_to(output.resolve())
            saved = work / "saved.zip"
            dialog = shortcut()
            assert dialog.title() == "导出前核对修改"
            # Repeated save while reviewing does not stack duplicate windows.
            app._save_mod_shortcut()
            assert app._export_dialog is dialog
            with patch.object(filedialog, "asksaveasfilename", return_value=str(saved)), patch.object(messagebox, "showinfo") as info, patch.object(messagebox, "showerror", side_effect=lambda *args: errors.append(str(args))):
                proceed(dialog)
                info.assert_not_called()
            assert "导出成功" in app.status.get()
            assert str(saved) in app.status.get()
            assert saved.is_file()
            reopened = EditorSession(ModWorkspace.open(saved))
            assert reopened.named_groups == app.session.named_groups
            assert reopened.document(d.name).bytes() == d.bytes()
            assert digest == hashlib.sha256(Path(source).read_bytes()).hexdigest()

            # Successful destinations survive session reload. Cancel/failure do not replace them.
            app._adopt_session(EditorSession(ModWorkspace.open(source)))
            with patch.object(filedialog, "asksaveasfilename", return_value="") as choose, patch.object(app, "_save_to_target") as save:
                app._do_export_mod()
                assert Path(choose.call_args.kwargs["initialdir"]).resolve() == saved.parent.resolve()
                assert choose.call_args.kwargs["initialfile"] == saved.name
                save.assert_not_called()
            with patch.object(app.session, "export", side_effect=OSError("owned failure")), patch.object(messagebox, "showerror") as failed, patch.object(messagebox, "showinfo") as info:
                app._save_to_target(work / "failed.zip")
                failed.assert_called_once()
                info.assert_not_called()
            assert not (work / "failed.zip").exists()
            with patch.object(filedialog, "asksaveasfilename", return_value=str(saved)), patch.object(messagebox, "askyesno", return_value=False) as confirm:
                original_export = saved.read_bytes()
                app._do_export_mod()
                confirm.assert_called_once()
                assert saved.read_bytes() == original_export
            with patch.object(filedialog, "asksaveasfilename", return_value="") as choose:
                app._do_export_mod()
                assert choose.call_args.kwargs["initialfile"] == saved.name

            folder = work / "folder"
            folder.mkdir()
            with zipfile.ZipFile(source) as archive:
                assert all(not Path(i.filename).is_absolute() and ".." not in Path(i.filename).parts for i in archive.infolist())
                archive.extractall(folder)
            untouched = folder / "untouched.resource"
            untouched.write_bytes(b"keep unrelated files")
            app._adopt_session(EditorSession(ModWorkspace.open(folder)))
            d = app.workspace.documents[0]
            model_path = folder / d.name
            original = model_path.read_bytes()
            key = (d.name, 0)
            app.session.apply_group({key}, 62)
            app._refresh_groups()
            app._export_target = saved  # Folder saving must still target its source.
            with patch.object(messagebox, "askyesno", return_value=False) as confirm, patch.object(messagebox, "showinfo"):
                dialog = shortcut()
                assert dialog.title() == "覆盖前核对修改"
                proceed(dialog)
                confirm.assert_called_once()
            assert model_path.read_bytes() == original
            assert not (work / ".mhw-layer-studio-backups").exists()
            app._select_by_key(key)
            app.renderer.pan_x = 12.5  # Cursor zoom can move beyond the former save clamp.
            navigation_before = app.renderer.camera_state()
            app._toggle_isolate_button()
            assert app._isolation_view is not None
            old = app.workspace
            with patch.object(messagebox, "askyesno", return_value=True) as confirm, patch.object(messagebox, "showinfo") as info, patch.object(messagebox, "showerror", side_effect=lambda *args: errors.append(str(args))):
                dialog = shortcut()
                proceed(dialog)
                confirm.assert_called_once()
                assert str(folder.resolve()) in confirm.call_args.args[1]
                assert confirm.call_args.kwargs["default"] == "no"
                info.assert_not_called()
            backup = old.last_backup
            assert "覆盖保存成功" in app.status.get()
            assert str(backup) in app.status.get()
            assert app.isolate.get() and app._isolation_view is not None
            app._toggle_isolate_button()
            for field, value in navigation_before.items():
                np.testing.assert_allclose(getattr(app.renderer, field), value)
            assert (backup / "files" / d.name).read_bytes() == original
            assert app.workspace.source.resolve() == folder.resolve()
            assert ModWorkspace.open(folder).documents[0].meshes[0].visible_condition == 62
            assert untouched.read_bytes() == b"keep unrelated files"
            # Explicit copy export from a folder stays separate from Ctrl+S.
            dialog = shortcut("<Control-Shift-KeyPress-S>")
            assert dialog.title() == "导出前核对修改"
            dialog.destroy()
            assert not errors, errors
            app._adopt_session(EditorSession(ModWorkspace.open(source)))
        assert digest == hashlib.sha256(Path(source).read_bytes()).hexdigest()
        report = {"ok": True, "version": app.title(), "source_unchanged": True,
                  "checks": ["visible join target menu preserves selection", "joining preserves Group IDs and collapsed target",
                             "group undo redo", "Ctrl+S exports ZIP/RAR/7Z/MOD3", "project saving not invoked", "ZIP export reopen",
                             "folder Ctrl+S cancellation", "folder backup before save", "unrelated files preserved",
                             "Ctrl+Shift+S exports a copy", "duplicate save review prevented",
                             "success uses status without modal", "export location and name survive session reload",
                             "cancel and failure preserve last successful destination", "remembered destination still confirms overwrite",
                             "backup location included in status", "group and member selection explicitly distinguished",
                             "membership ignores stale dropdown", "multiple group selection counted correctly",
                             "current group disabled in target menu", "recent join targets ordered first after undo",
                             "folder overwrite preserves isolation return camera"]}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc(), "callbacks": errors}
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
