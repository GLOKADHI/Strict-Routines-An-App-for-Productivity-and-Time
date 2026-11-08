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
datas += [('assets/StrictBotNative.dll', 'assets')]
datas += [('taufa_extension', 'taufa_extension')]
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
hiddenimports += ['tzdata','core.google_auth','core.google_drive','core.drive_backup','core.cloud_sync_manager','core.client_account','core.client_parity','core.paired_demo_service','windows.google_connection_check']


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # WebView2 is the Windows UI; Taufa is the designated AI destination.
    # Unconfigured experimental local-model fallbacks must remain unavailable,
    # rather than adding training/notebook/Qt runtimes to the desktop client.
    excludes=['torch','torchvision','torchaudio','transformers','tensorflow',
              'PySide6','PyQt6','PyQt5','IPython','jupyter','notebook',
              'new_model.checkpoints','new_model.train','new_model.cifar'],
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
    name='StrictRecords',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=['app_icon.ico'],
)
