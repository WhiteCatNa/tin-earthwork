# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the macOS TIN earthwork application."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("matplotlib.backends")
    + collect_submodules("tkcalendar")
    + [
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk",
        "PIL._tkinter_finder",
        "scipy.spatial._qhull",
        "openpyxl.cell._writer",
    ]
)
datas = collect_data_files("matplotlib") + collect_data_files("tkcalendar")

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TIN土方自动算量系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
app = BUNDLE(
    exe,
    analysis.binaries,
    analysis.datas,
    [],
    name="TIN土方自动算量系统.app",
    icon=None,
    bundle_identifier="com.tin-earthwork.calculator",
)
