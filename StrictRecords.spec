# -*- mode: python ; coding: utf-8 -*-

import os

ROOT_DIR = os.path.dirname(os.path.abspath(SPEC))
ICON_PATH = os.path.join(ROOT_DIR, "app_icon.ico")
SPLASH_PATH = os.path.join(ROOT_DIR, ".dist", "StrictRecords-splash.png")

runtime_data = [
    (os.path.join(ROOT_DIR, "data", "motivation_track.wav"), "data"),
    (os.path.join(ROOT_DIR, "app_icon.ico"), "."),
    (os.path.join(ROOT_DIR, "app_logo.png"), "."),
    (os.path.join(ROOT_DIR, "assets"), "assets"),
    (os.path.join(ROOT_DIR, "shared"), "shared"),
    (os.path.join(ROOT_DIR, "browser_extension"), "browser_extension"),
    (os.path.join(ROOT_DIR, "taufa_extension"), "taufa_extension"),
    (os.path.join(ROOT_DIR, "browser_extension_monitor"), "browser_extension_monitor"),
    (os.path.join(ROOT_DIR, "StrictRecordsBrowserBridge.dll"), "."),
    (os.path.join(ROOT_DIR, "StrictRecords-connect.html"), "."),
]
runtime_data = [
    (source, destination)
    for source, destination in runtime_data
    if os.path.isfile(source) or os.path.isdir(source)
]

a = Analysis(
    [os.path.join(ROOT_DIR, "app.py")],
    pathex=[ROOT_DIR],
    binaries=[],
    datas=runtime_data,
    hiddenimports=[
        "win32crypt",
        "win32gui",
        "win32con",
        "win32process",
        "win32api",
        "pywintypes",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

splash = Splash(
    SPLASH_PATH,
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(326, 448),
    text_size=10,
    # PyInstaller inserts custom font names into Tcl without quoting spaces.
    # TkDefaultFont avoids the parse error that leaves an empty Tk root open.
    text_font="TkDefaultFont",
    text_color="#91A4B8",
    text_default="Starting StrictRecords...",
    center="active",
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="StrictRecords",
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
    icon=[ICON_PATH],
)
