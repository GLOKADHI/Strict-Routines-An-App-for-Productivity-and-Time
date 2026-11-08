# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Bundle only runtime assets. The SQLite database is user data and must stay at its
# existing workspace path rather than being copied into a distributable build.
datas = [
    ('data/motivation_track.wav', 'data'),
    ('app_icon.ico', '.'),
    ('app_logo.png', '.'),
    ('StrictRecordsBrowserBridge.dll', '.'),
    ('StrictRecords-connect.html', '.'),
]
# The dashboard serves these files from ROOT_DIR/assets at runtime. PyInstaller
# does not include them unless they are listed as data files explicitly.
datas += [
    (os.path.join('assets', filename), 'assets')
    for filename in os.listdir('assets')
    if filename.lower().endswith('.png')
]
datas += [('shared', 'shared')]
datas += [('browser_extension', 'browser_extension')]
datas += [('browser_extension_monitor', 'browser_extension_monitor')]
binaries = []
hiddenimports = [
    'webview.platforms.winforms',
    'pythonnet',
    'clr',
    'win32gui',
    'win32con',
    'win32process',
    'win32crypt',
    'win32api',
    'pywintypes',
]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
datas += collect_data_files('tzdata')
hiddenimports += ['core.youtube_reward_cloud','cloud_points.youtube_rewards','cloud_points.demo_access','tzdata','core.client_account','core.client_parity','core.paired_demo_service']


a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['core.google_auth','core.google_drive','core.cloud_sync_manager','windows.google_connection_check'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StrictRecords',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='StrictRecords',
)
