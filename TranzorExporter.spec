# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path


PYTHON_PREFIX = Path(sys.base_prefix)
DLL_DIR = PYTHON_PREFIX / "DLLs"
TCL_DIR = PYTHON_PREFIX / "tcl"


def _collect_tk_assets():
    binaries = []
    datas = []

    for dll_name in ("tcl86t.dll", "tk86t.dll"):
        dll_path = DLL_DIR / dll_name
        if dll_path.exists():
            binaries.append((str(dll_path), "."))

    tk_dirs = (
        (TCL_DIR / "tcl8.6", "_tcl_data"),
        (TCL_DIR / "tk8.6", "_tk_data"),
        (TCL_DIR / "tcl8", "tcl8"),
    )
    for src_path, dest_name in tk_dirs:
        if src_path.exists():
            datas.append((str(src_path), dest_name))

    return binaries, datas


tk_binaries, tk_datas = _collect_tk_assets()


a = Analysis(
    ['export_gui.py'],
    pathex=[],
    binaries=tk_binaries,
    datas=[
        ('export_changes.py', '.'),
        ('export_translations.py', '.'),
        ('export_mr_pipeline.py', '.'),
        ('quality_overview.py', '.'),
        ('gui_tabs.py', '.'),
        ('export_full_translations.py', '.'),
        ('gui_tab_full_translations.py', '.'),
        ('pyi_rth_tkinter_fix.py', '.'),
        *tk_datas,
    ],
    hiddenimports=[
        '_tkinter',
        'tkinter',
        'export_full_translations',
        'gui_tab_full_translations',
    ],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=['pyi_rth_tkinter_fix.py'],
    excludes=[
        '81d243bd2c585b0f4821__mypyc',
        'charset_normalizer.md',
        'charset_normalizer.cd',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TranzorExporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
