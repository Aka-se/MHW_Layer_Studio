"""Opt-in packaged preview check used by release validation, never during normal startup."""
import hashlib
import json
import time
import sys
import traceback
from pathlib import Path


def check_mouse_controls(app, source, output):
    from mouse_controls_validation import run
    try:
        run(app, source, output)
        return 0
    except Exception:
        Path(output).mkdir(parents=True, exist_ok=True)
        Path(output, "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()


def check_editor_layout(app, source, output):
    from editor_layout_validation import run
    try:
        run(app, source, output)
        return 0
    except Exception:
        Path(output).mkdir(parents=True, exist_ok=True)
        Path(output, "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()


def check_lod_preview(app, source, output):
    from lod_validation import run
    try:
        report = run(app, source, output)
        report["version"] = app.title().split(" · ")[0]
        Path(output, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception:
        Path(output).mkdir(parents=True, exist_ok=True)
        Path(output, "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()


def check_lod_sync(app, source, output):
    from lod_sync_validation import run
    try:
        report = run(app, source, output)
        report["frozen"] = bool(getattr(sys, "frozen", False))
        Path(output, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception:
        Path(output).mkdir(parents=True, exist_ok=True)
        Path(output, "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()


def check_spatial_preview(app, source, output):
    from spatial_validation import run
    try:
        report = run(app, source, output)
        report["frozen"] = bool(getattr(sys, "frozen", False))
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def check_preview(app, source, output):
    from mod3 import ModWorkspace
    from session import EditorSession
    output = Path(output)
    if output.exists():
        raise ValueError("Diagnostic output must be a new directory")
    output.mkdir(parents=True)
    try:
        original = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        d, mesh = next((d, m) for d, m in app.workspace.all_meshes()
                       if m.uvs is not None and app.renderer.texture_library.color_reference(d, m.material_id)
                       and app.renderer.texture_library.get(d, m.material_id) is not None)
        key = (d.name, mesh.index)
        app.session.edit_annotations([key], name="验证部件", note="打包预览测试")
        app.session.edit_named_group("create", "验证组", [key])
        app.session.apply_group([key], 60)
        app.session.set_health_thresholds({60: 85, 61: 70, 62: 55})
        app._sync_preview_controls()
        app.renderer.health_enabled = True
        app.renderer.health = 85
        assert not app.renderer.visible_for_health(mesh)
        app.renderer.health = 86
        assert app.renderer.visible_for_health(mesh)
        app.renderer.color_mode = "texture"
        app.renderer.health_enabled = False
        app.renderer.selected_keys = {key}
        app.renderer.isolate_selected = True
        app.renderer.fit(app.workspace, {key})
        started = time.perf_counter()
        image = app.renderer.render(app.workspace, (640, 800))
        first_seconds = time.perf_counter() - started
        library = app.renderer.texture_library
        assert library.get(d, mesh.material_id) is not None, library.status
        assert (app.renderer._pixel_ids >= 0).any(), "No geometry rendered"
        y, x = next(zip(*(app.renderer._pixel_ids >= 0).nonzero()))
        assert app.renderer.pick(x, y) == key
        # Exercise the new grouping code in the frozen executable too.
        group_plan = app.session.apply_material_groups({(document.name, m.index) for document, m in app.workspace.all_meshes()})
        assert group_plan["groups"]
        assert app.session.named_groups["验证组"] == {key}
        app.list_mode.set("按部件组")
        app._refresh_groups()
        app.named_group.set("验证组")
        app.select_named_group()
        assert app._selection_keys == {key}
        assert app.tree.set(next(iid for iid, name in app._group_iids.items() if name == "验证组"), "group") == "60"
        image.save(output / "preview.png")
        # Compile the color scanline kernel in the distributed application too.
        app.renderer.color_mode = "layer"
        colored = app.renderer.render_frame(app.workspace, (640, 800), interactive=True)
        assert app.renderer._pixel_ids.shape == (480, 384)
        assert (app.renderer._pixel_ids >= 0).any()
        y, x = next(zip(*(app.renderer._pixel_ids >= 0).nonzero()))
        assert app.renderer.pick((x + .5) / app.renderer._pick_scale[0], (y + .5) / app.renderer._pick_scale[1]) == key
        colored.save(output / "layer-preview.png")
        app.renderer.render(app.workspace, (640, 800))
        assert app.renderer._pixel_ids.shape == (800, 640)
        app.renderer.color_mode = "texture"
        started = time.perf_counter()
        app.renderer.render(app.workspace, (640, 800))
        steady_seconds = time.perf_counter() - started
        exported = output / "check.zip"
        app.session.export(exported, {"color": "贴图"})
        restored = EditorSession(ModWorkspace.open(exported))
        assert restored.named_groups == app.session.named_groups
        assert restored.annotations == app.session.annotations
        assert restored.health_thresholds == {60: 85, 61: 70, 62: 55}
        # Exercise direct folder saving against an owned mock installed mod.
        from mod3 import EDITOR_ENTRY
        installed = output / "installed-mod"
        installed.mkdir()
        for document in restored.workspace.documents:
            path = installed / document.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(document.bytes())
        metadata = installed / EDITOR_ENTRY
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(restored.workspace.editor_data, ensure_ascii=False), encoding="utf-8")
        asset = installed / "untouched.texture"
        asset.write_bytes(b"unchanged resource")
        direct = EditorSession(ModWorkspace.open(installed))
        direct.apply_group([key], 62)
        previous = direct.workspace
        direct.export(installed, overwrite=True, expected_state=previous.overwrite_state(installed))
        assert previous.last_backup.is_dir()
        assert (previous.last_backup / "files" / d.name).read_bytes() == restored.document(d.name).bytes()
        assert asset.read_bytes() == b"unchanged resource"
        assert direct.document(d.name).meshes[mesh.index].visible_condition == 62
        assert direct.named_groups == restored.named_groups
        project = output / "after-overwrite.mhwlayer.json"
        direct.save(project)
        project_session, _ = EditorSession.open(project)
        assert project_session.document(d.name).meshes[mesh.index].visible_condition == 62
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == original
        report = {"ok": True, "frozen": bool(getattr(sys, "frozen", False)), "archive_kind": app.workspace.kind, "source_sha256": original, "first_render_seconds": round(first_seconds, 3),
                  "steady_render_seconds": round(steady_seconds, 3), "texture_status": library.status[(d.name, mesh.material_id)],
                  "checks": ["Tk startup", "archive backend", "TEX decoding", "Numba texture/color raster", "interactive resolution/pixel picking/full-size recovery", "material grouping", "group/member browser", "85/70/55 thresholds", "metadata export/reload", "direct folder overwrite", "backup and project after overwrite", "original unchanged"]}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def check_game_preview(app, game_directory, source, output):
    from game_resources import GameTextures
    from mod3 import ModWorkspace
    from session import EditorSession
    from textures import TextureLibrary
    import zipfile
    output = Path(output)
    if output.exists():
        raise ValueError("Diagnostic output must be a new directory")
    output.mkdir(parents=True)
    resources = None
    try:
        original = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        resources = GameTextures(game_directory, cache=output / "cache")
        before = {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in resources.paths}
        app.game_resources, app.game_directory = resources, str(resources.root)
        app._adopt_session(EditorSession(ModWorkspace.open(source)))
        library = app.renderer.texture_library
        started = time.perf_counter()
        materials = []
        for document in app.workspace.documents:
            for mid in sorted({mesh.material_id for mesh in document.meshes}):
                pixels = library.get(document, mid)
                materials.append({"file": document.name, "material": document.material_info(mid).name,
                                  "id": mid, "ok": pixels is not None, "status": library.status[(document.name, mid)]})
        assert all(item["ok"] for item in materials), materials
        game_count = sum("游戏资源" in item["status"] for item in materials)
        assert game_count > 0
        app.renderer.color_mode = "texture"
        app.renderer.health_enabled = False
        app.renderer.fit(app.workspace)
        image = app.renderer.render(app.workspace, (600, 900))
        assert (app.renderer._pixel_ids >= 0).any()
        y, x = next(zip(*(app.renderer._pixel_ids >= 0).nonzero()))
        assert app.renderer.pick(x, y) is not None
        image.save(output / "preview.png")
        with GameCacheProbe(game_directory, output / "cache") as cached:
            again = TextureLibrary(app.workspace, game=cached)
            for document in app.workspace.documents:
                for mid in {mesh.material_id for mesh in document.meshes}:
                    assert again.get(document, mid) is not None
            assert cached.worker.process is None, "Cache restart unexpectedly decoded game blocks"
        view = app._view_state()
        assert view["game_directory"] == str(resources.root)
        project = output / "game-preview.mhwlayer.json"
        app.session.save(project, view)
        _, restored_view = EditorSession.open(project)
        assert restored_view["game_directory"] == str(resources.root)
        exported = output / "check.zip"
        app.session.export(exported, view)
        with zipfile.ZipFile(app.workspace.archive_path) as original_archive, zipfile.ZipFile(exported) as result:
            original_tex = {n for n in original_archive.namelist() if n.lower().endswith(".tex")}
            assert original_tex == {n for n in result.namelist() if n.lower().endswith(".tex")}, "Game cache leaked into mod export"
        after = {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in resources.paths}
        assert before == after
        assert original == hashlib.sha256(Path(source).read_bytes()).hexdigest()
        report = {"ok": True, "frozen": bool(getattr(sys, "frozen", False)), "materials": len(materials),
                  "game_materials": game_count, "mod_materials": sum("模组内" in i["status"] for i in materials),
                  "elapsed_seconds": round(time.perf_counter() - started, 3), "source_sha256": original,
                  "checks": ["standalone bundled decoder", "game chunk lookup", "all material colors", "textured preview/picking",
                             "cache restart without decompression", "game path project roundtrip", "cache excluded from export", "game/source unchanged"],
                  "material_details": materials}
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
    finally:
        app._executor.shutdown(wait=True, cancel_futures=True)
        app.destroy()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


class GameCacheProbe:
    def __init__(self, directory, cache):
        from game_resources import GameTextures
        self.resources = GameTextures(directory, cache)

    def __enter__(self):
        return self.resources

    def __exit__(self, *_):
        self.resources.close()
