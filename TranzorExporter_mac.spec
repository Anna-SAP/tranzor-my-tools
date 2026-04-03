# -*- mode: python ; coding: utf-8 -*-
# TranzorExporter Mac OS .app 打包配置
# 用法: pyinstaller TranzorExporter_mac.spec

a = Analysis(
    ['export_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('export_changes.py', '.'),
        ('export_translations.py', '.'),
        ('export_mr_pipeline.py', '.'),
        ('quality_overview.py', '.'),
        ('gui_tabs.py', '.'),
    ],
    hiddenimports=[],
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
