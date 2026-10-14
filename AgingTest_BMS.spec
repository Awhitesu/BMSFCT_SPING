# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui/tabs/checkmark.png', 'ui/tabs'),
        ('resources', 'resources'),
        ('config', 'config'),
        ('recipes', 'recipes'),
        ('故障码.txt', '.'),
        ('PCAN-Basic/x64/PCANBasic.dll', 'PCAN-Basic/x64'),
        ('力捷峰U盘/Demo/OpteeQDLLx64.dll', '力捷峰U盘/Demo'),
        ('力捷峰U盘/Demo/libcrypto-3.dll', '力捷峰U盘/Demo')
    ],
    hiddenimports=[
        'ui.dialogs.admin_auth_dialog',
        'ui.dialogs.user_account_dialog',
        'ui.dialogs.startup_dialog',
        'ui.dialogs.ai_recipe_dialog',
        'can',
        'can.interfaces.pcan',
        'can.interfaces.pcan.pcan',
        'serial',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2'],
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
    name='BMS软件注入&APP刷写系统',
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
    icon='resources/icon.ico',
)
