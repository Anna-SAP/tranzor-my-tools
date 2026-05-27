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
    # NOTE: source `.py` modules used to be duplicated here as `datas` on top of
    # being declared in `hiddenimports`. PyInstaller already embeds them into
    # the PYZ archive, so the duplicate copies in `datas` only made the bundle
    # larger and slowed the onefile unpack on every launch. Keep `datas` for
    # genuine resources only (Tcl/Tk runtime files, etc.).
    datas=[
        *tk_datas,
    ],
    # Every sibling module that is optional / wrapped in ``try: import x except``
    # at the top of ``export_gui.py`` (or pulled in transitively by such a tab)
    # is listed here. PyInstaller's static analyzer normally finds them anyway,
    # but the duplicate ``datas`` entries used to mask any analyzer miss. Now
    # that ``datas`` no longer carries source .py copies, the safety net lives
    # entirely in this list — keep it in sync when adding new tabs.
    hiddenimports=[
        '_tkinter',
        'tkinter',
        'tranzor_bridge',
        'bridge_setup_wizard',
        'export_full_translations',
        'gui_tab_full_translations',
        'gui_tab_human_revisions',
        'gui_tab_scan_tasks',
        'gui_tab_term_watchtower',
        'gui_tab_tm_context_insight',
        'gui_tab_opus_id_monitor',
        'gui_tab_tranzor_checks',
        'opus_id_monitor',
        'tranzor_checks',
        'terminology_highlight',
        'terminology_watchtower',
        'tranzor_terminology',
        'gitlab_client',
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
    # UPX disabled on purpose. In onefile mode the bootloader has to UPX-decompress
    # every binary into %TEMP%\_MEIxxxxx\ on each launch — that decompression is
    # CPU-bound and dominates cold-start cost. As the bundle has grown past 25 MB
    # (10 tabs, OPUS / Checks SQLite layers, terminology highlighting, etc.) the
    # extra ~10 MB on disk is well worth the ~30-60% startup-time win.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
