# -*- mode: python ; coding: utf-8 -*-
# TranzorExporter Mac OS .app 打包配置
# 用法: pyinstaller TranzorExporter_mac.spec

a = Analysis(
    ['export_gui.py'],
    pathex=[],
    binaries=[],
    # NOTE: source `.py` modules used to be duplicated here as `datas` on top of
    # `hiddenimports`. PyInstaller already embeds them in the PYZ — the duplicate
    # copies only inflated the .app bundle and slowed the first launch. Keep
    # `datas` for genuine resource files only.
    datas=[],
    # Sync with TranzorExporter.spec — see the note there about why every
    # optional ``try: import x`` target needs to be listed explicitly now
    # that we no longer mirror them as ``datas`` source copies.
    hiddenimports=[
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
        'date_picker',  # 共享深色日历选择组件（日期字段 📅 选择器）
        'opus_id_monitor',
        'tranzor_checks',
        'terminology_highlight',
        'terminology_watchtower',
        'tranzor_terminology',
        'gitlab_client',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx=False,              # upx 在 Mac 上不常用
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,    # Mac 需要此选项支持双击运行
    target_arch='universal2',  # 同时支持 Intel (x86_64) 和 Apple Silicon (arm64)
    codesign_identity=None,
    entitlements_file=None,
)

# Mac .app 打包 — 这是 Windows spec 中没有的部分
app = BUNDLE(
    exe,
    name='TranzorExporter.app',
    # icon='TranzorExporter.icns',  # 如有 .icns 图标文件可取消注释
    bundle_identifier='com.tranzor.exporter',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleName': 'TranzorExporter',
        'NSHighResolutionCapable': True,
    },
)
