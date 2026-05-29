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
        # PR-A..E (Review Worklist series) — Worklist tab and its
        # support modules are referenced through try/except imports so
        # PyInstaller's static analyzer might skip them; list explicitly.
        'gui_tab_review_worklist',
        'merge_watchdog',
        'unregistered_terms',
        'daily_digest',
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
    # Trim modules we know are dead weight for this app. Each entry here drops
    # one or more files from the bundle, which directly reduces the number of
    # .pyd/.dll files the PyInstaller onefile bootloader must extract into
    # %TEMP%\_MEIxxxxx\ on every launch — fewer files means less Defender
    # scanning during cold-start. Only exclude modules we are SURE the GUI does
    # not need at runtime.
    excludes=[
        '81d243bd2c585b0f4821__mypyc',
        'charset_normalizer.md',
        'charset_normalizer.cd',
        # We never run unittest from inside the GUI. Drops ~25 .pyc files.
        'unittest',
        # pydoc pulls in tkinter docs viewer; we have our own help text.
        'pydoc',
        # xmlrpc client/server — unused.
        'xmlrpc',
        # Standard-library test packages PyInstaller sometimes grabs.
        'test',
        'tests',
        # setuptools / pkg_resources are the biggest single drop on Windows —
        # they alone account for a hundred+ files we never call into.
        'setuptools',
        'pkg_resources',
        # Distutils is gone in 3.12 anyway; explicit exclude silences a warning.
        'distutils',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# onefile layout — back from a brief onedir detour
# ---------------------------------------------------------------------------
# PR #64 switched this build to onedir to dodge per-file Defender scans during
# the onefile bootloader's %TEMP% unpack. That worked, but the .zip /
# folder distribution model broke users in practice:
#
#   - 7-Zip / Windows zip viewer happily run TranzorExporter.exe straight from
#     the .zip without ever materialising _internal\, then the EXE crashes
#     with "Failed to load python312.dll". Reproduced even after wrapping the
#     bundle in a top-level TranzorExporter/ folder and adding a README.
#   - Users expect a single .exe and find the folder layout confusing.
#
# Onefile is the right shape for this tool. Cold-start speed is now tackled by
# trimming `excludes` above (fewer files to scan) plus the per-stage timing
# log added in #64 (~/.tranzor_exporter/startup.log) — so if it is still slow
# the next iteration has real data to point at, not a guessed root cause.
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
