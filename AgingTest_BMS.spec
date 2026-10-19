# -*- mode: python ; coding: utf-8 -*-
import os

folder_build = os.environ.get('BMS_BUILD_MODE') == 'onedir'

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('PCAN-Basic/x64/PCANBasic.dll', '.')],
    datas=([(os.environ['BMS_VERSION_FILE'], 'resources')] if os.environ.get('BMS_VERSION_FILE') else []) + [
        ('resources', 'resources'),
        ('zlgcan(20260806)/zlgcan_x64', 'zlgcan(20260806)/zlgcan_x64'),
        ('zlgcan_python_260529/zlgcan.py', 'zlgcan_python_260529'),
        ('故障码.txt', '.'),
        ('力捷峰U盘/Demo/OpteeQDLLx64.dll', '力捷峰U盘/Demo'),
        ('力捷峰U盘/Demo/libcrypto-3.dll', '力捷峰U盘/Demo')
    ],
    hiddenimports=[
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
    # PyInstaller adds __main__ during analysis. Specify it up front so its
    # cached excludes list stays identical on the next incremental build.
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'IPython', 'pytest', 'notebook',
              'jupyterlab', 'matplotlib', 'pandas', 'scipy', '__main__'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    *([] if folder_build else [a.binaries, a.datas]),
    [],
    name='BMS软件注入&APP刷写系统',
    exclude_binaries=folder_build,
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
    icon='resources/icon.ico',
    version=os.environ.get('BMS_VERSION_INFO'),
)

if folder_build:
    collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BMSFCT')
