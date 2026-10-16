# Contributing

MHW Layer Studio is a Windows desktop editor for assigning visual-condition
group IDs to Monster Hunter: World MOD3 meshes. Contributions should preserve
the central workflow: open a mod, identify or split parts, assign groups,
preview the result, then export safely.

## Development setup

Use Python 3.12 on Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe app.py
```

The unit suite uses synthetic fixtures by default. A few integration tests are
enabled only when locally owned mod archives with the expected names are placed
at the project root. Those archives are ignored by Git and must not be attached
to issues or pull requests unless their author has explicitly allowed
redistribution.

## Pull requests

- Keep changes focused and describe the user-visible behavior.
- Add or update tests for MOD3 parsing, binary rewriting, grouping, or export
  behavior when the change can affect file integrity.
- Never commit game files, mod archives, extracted textures, save data, logs,
  absolute local paths, or generated installers.
- Preserve `LICENSE`, `THIRD_PARTY_NOTICES.md`, and the license files within
  `assets` when changing packaged dependencies.
- Treat the editor and `MHWI-VisualControllerExtended` as separate release
  units. Editor exports may contain rule fragments and per-mod manifests; the
  global `[GreatSwordBurst]` configuration belongs to the plugin package.

## Windows builds

`build_windows.bat` runs the tests, builds the bundled OOZ helper, packages the
editor with PyInstaller, and invokes Inno Setup 6 when available. Build output
is written to ignored directories and should be attached to a release rather
than committed.

The game plugin is maintained and released as a separate project because it
uses a native C++ toolchain and has its own third-party license requirements.
