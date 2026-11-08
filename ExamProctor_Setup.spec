# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/lokad/Music/Practical/StrictRecords/installer.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/lokad/Music/Practical/StrictRecords/installer_payload/ExamProctor.bin', '.'), ('C:/Users/lokad/Music/Practical/StrictRecords/installer_payload/app_icon.ico', '.')],
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
    name='ExamProctor_Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=['C:/Users/lokad/Music/Practical/StrictRecords/installer_payload/app_icon.ico'],
)
