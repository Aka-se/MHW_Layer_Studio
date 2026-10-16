"""Exercise owned Tk controls and real MOD3 exports; never sends game input."""
import hashlib
import json
from pathlib import Path
import traceback


def check(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    from damage_model import units, export_plan
    from damage_dialog import DamageSettingsDialog
    from dialog_geometry import window_rect
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        source = Path(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        s = EditorSession(ModWorkspace.open(source))
        d = next(d for d in s.workspace.documents if d.equipment_part == 'wst')
        key = (d.name, min(1, len(d.meshes)-1))
        s.apply_group([key], 60)
        app._adopt_session(s)
        app.update()
        before = {d.name: d.bytes() for d in s.workspace.documents}
        app.damage_mode.set('random'); app.change_damage_mode(); app.update()
        assert s.damage_model['mode'] == 'random'
        assert before == {d.name: d.bytes() for d in s.workspace.documents}
        dialog = DamageSettingsDialog(app)
        dialog.update()
        # Mark every unit eligible, then explicitly protect the first body unit.
        dialog.mark_others()
        body = next(i for i, r in enumerate(dialog.records) if any('/body/' in k[0] for k in r['keys']))
        dialog.tree.selection_set(str(body)); dialog.mark('base')
        dialog.apply(); app.update()
        app.health.set(50); app._health_changed('50')
        app._redraw(synchronous=True); app.update()
        eligible = [r for r in units(s) if not r['protected']]
        # Outfit budgets are checked independently.
        for pool in {r['pool'] for r in eligible}:
            records = [r for r in eligible if r['pool'] == pool]
            hidden = sum(all(k in app._damage_hidden_keys for k in r['keys']) for r in records)
            assert hidden == len(records) // 2
        protected = next(r for r in units(s) if r['protected'])
        assert all(k not in app._damage_hidden_keys for k in protected['keys'])
        app.renderer.render(s.workspace, (640, 680)).save(output / 'random-preview.png')
        random_zip = s.export(output / 'Kuroki-random-demo.zip')
        reopened = EditorSession(ModWorkspace.open(random_zip))
        assert not reopened.workspace.errors
        assert reopened.damage_model['mode'] == 'random'
        assert reopened.named_groups == s.named_groups
        app.damage_mode.set('manual'); app.change_damage_mode(); app.update()
        app._sync_preview_controls()
        assert app.renderer.health_hidden_keys is None
        assert before == {d.name: d.bytes() for d in s.workspace.documents}
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        assert not errors, errors
        report = {'version': app.title(), 'frozen': bool(getattr(__import__('sys'), 'frozen', False)),
                  'source_unchanged': True, 'manual_mesh_bytes_unchanged': True,
                  'eligible_units': len(eligible), 'protected_units': sum(r['protected'] for r in units(s)),
                  'dual_export': str(random_zip), 'random_and_manual_controls': 'passed',
                  'errors': errors}
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    except Exception:
        (output / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
