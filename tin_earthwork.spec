# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（macOS 生成 .app，Windows/Linux 生成程序目录）。

本地与 CI 共用这一份配置：pyinstaller --noconfirm --clean tin_earthwork.spec
"""
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "TIN土方自动算量系统"

hiddenimports = (
    collect_submodules("matplotlib.backends")
    + [
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk",
        "PIL._tkinter_finder",
        "scipy.spatial._qhull",
        "scipy.spatial._ckdtree",
        "openpyxl.cell._writer",
    ]
)
datas = collect_data_files("matplotlib")

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
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        analysis.binaries,
        analysis.datas,
        [],
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="com.tin-earthwork.calculator",
    )
else:
    coll = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
