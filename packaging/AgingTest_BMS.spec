# -*- mode: python ; coding: utf-8 -*-
import os

PROJECT_ROOT = os.path.dirname(SPECPATH)


def source(path):
    return os.path.join(PROJECT_ROOT, path)

folder_build = os.environ.get('BMS_BUILD_MODE') == 'onedir'

a = Analysis(
    [source('main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[(source('vendor/PCAN-Basic/x64/PCANBasic.dll'), 'vendor/PCAN-Basic/x64')],
    datas=([(os.environ['BMS_VERSION_FILE'], 'resources')] if os.environ.get('BMS_VERSION_FILE') else []) + [
        (source('resources'), 'resources'),
        (source('vendor/zlgcan(20260806)/zlgcan_x64'), 'vendor/zlgcan(20260806)/zlgcan_x64'),
        (source('vendor/zlgcan_python_260529/zlgcan.py'), 'vendor/zlgcan_python_260529'),
        (source('config/故障码.txt'), '.'),
        (source('vendor/力捷峰U盘/Demo/OpteeQDLLx64.dll'), 'vendor/力捷峰U盘/Demo'),
        (source('vendor/力捷峰U盘/Demo/libcrypto-3.dll'), 'vendor/力捷峰U盘/Demo')
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
    icon=source('resources/icon.ico'),
    version=os.environ.get('BMS_VERSION_INFO'),
)

if folder_build:
    collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BMSFCT')
