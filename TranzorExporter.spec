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

# ---------------------------------------------------------------------------
# onedir layout — was onefile, switched to onedir for startup speed
# ---------------------------------------------------------------------------
# Background: in onefile mode the PyInstaller bootloader has to extract every
# bundled .pyd / .dll into %TEMP%\_MEIxxxxx\ on every single launch (50+ files
# for a Tk + requests + sqlite + 10-tab GUI). Each newly-written executable
# triggers a Windows Defender real-time scan, which serialises behind every
# file write. Empirically this pushed cold-start to ~60 s on a Defender-enabled
# laptop even after upx=False and the datas cleanup.
#
# Onedir lays the same files out in dist/TranzorExporter/_internal/ at build
# time, Defender scans them once at install / unzip time, and subsequent cold
# starts are O(load DLLs into memory) instead of O(write + scan every file).
# The trade-off is that we now ship a folder (zipped by the CI artifact step)
# instead of a single .exe — small inconvenience for a 10-20x speedup.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # binaries go to COLLECT (onedir), not the EXE
    name='TranzorExporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TranzorExporter',
)
