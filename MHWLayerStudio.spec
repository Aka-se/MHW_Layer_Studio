# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=copy_metadata("numba") + copy_metadata("llvmlite") + copy_metadata("rarfile") + [("assets/unrar", "assets/unrar"), ("assets/7zip", "assets/7zip"), ("assets/mhwchunk", "assets/mhwchunk"), ("assets/ooz", "assets/ooz")],
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["ooz"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MHWLayerStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="MHWLayerStudio",
)
