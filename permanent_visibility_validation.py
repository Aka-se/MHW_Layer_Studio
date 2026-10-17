"""Check owned editor controls and full-package export without installing a mod."""
import hashlib
import json
from pathlib import Path
import traceback


def check(app, source, output):
    from damage_model import permanently_hidden
    from mod3 import ModWorkspace
    from session import EditorSession
    from damage_dialog import DamageSettingsDialog
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        source = Path(source)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        session = EditorSession(ModWorkspace.open(source))
        doc = next(d for d in session.workspace.documents if len(d.meshes) >= 2)
        keys = {(doc.name, 0), (doc.name, 1)}
        session.apply_group(keys, 60)
        session.edit_named_group('create', '隐藏验证组', keys)
        session.edit_annotations(keys, note='检查后保留')
        before = {d.name: d.bytes() for d in session.workspace.documents}
        app._adopt_session(session)
        app.update()
        app._select_by_key((doc.name, 0), select_group=True)
        app.update()
        assert app._selection_keys == keys
        app.permanent_hide_button.invoke()
        app.update()
        assert permanently_hidden(session) == keys
        assert before == {d.name: d.bytes() for d in session.workspace.documents}
        widgets = app.layer_actions.pack_slaves()
        assert widgets.index(app.permanent_hide_button) == widgets.index(app.layer_buttons[0]) + 1
        for i in (0, 1):
            di = session.workspace.documents.index(doc)
            iid = f'm:{di}:{i}'
            assert '始终隐藏' in app.tree.item(iid, 'text')
            assert 'permanent_hidden' in app.tree.item(iid, 'tags')
        for mode in ('manual', 'random'):
            app.damage_mode.set(mode)
            app.change_damage_mode()
            for hp in (0, 50, 100):
                app.health.set(hp)
                app._sync_preview_controls()
                assert not keys & {(d.name, m.index) for _, d, m in app.renderer.visible_entries(session.workspace)}
        app.simulate_health.set(False)
        app._sync_preview_controls()
        assert not keys & {(d.name, m.index) for _, d, m in app.renderer.visible_entries(session.workspace)}
        dialog = DamageSettingsDialog(app)
        dialog.update()
        assert any('始终隐藏' in str(dialog.tree.item(iid, 'values')) for iid in dialog.tree.get_children())
        dialog.destroy()
        app.isolate.set(True)
        app._isolate_changed()
        assert keys == {(d.name, m.index) for _, d, m in app.renderer.visible_entries(session.workspace)}
        app._redraw(synchronous=True)
        app.renderer.render(session.workspace, (640, 680)).save(output / 'isolated-hidden-preview.png')
        app.isolate.set(False)
        app._isolate_changed()
        archive = session.export(output / 'permanent-hidden-demo.zip')
        reopened = EditorSession(ModWorkspace.open(archive))
        assert not reopened.workspace.errors
        assert permanently_hidden(reopened) == keys
        app._adopt_session(reopened)
        app.update()
        app._select_by_key((doc.name, 0), select_group=True)
        app.apply_group(60)
        assert not permanently_hidden(reopened)
        assert all(reopened.annotations[k]['note'] == '检查后保留' for k in keys)
        app.undo()
        assert permanently_hidden(reopened) == keys
        app.redo()
        assert not permanently_hidden(reopened)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
        assert not errors, errors
        (output / 'report.json').write_text(json.dumps(dict(ok=True, version=app.title(),
            frozen=bool(getattr(__import__('sys'), 'frozen', False)), group_assignment=True,
            both_modes=True, health_toggle=True, isolation=True, restore_undo_redo=True,
            export_reopen=True, source_unchanged=True), ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    except Exception:
        (output / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
