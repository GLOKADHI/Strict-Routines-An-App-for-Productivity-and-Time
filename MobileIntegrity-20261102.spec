import json
from pathlib import Path
root=Path(SPECPATH);release=root/'releases/StrictRecords-MobileIntegrity-20261102';sealed=root/'.codex-work/mobile-integrity-sealed-20261102'
pure=json.loads((release/'pure.json').read_text());pyz=PYZ(pure)
exe=EXE(pyz,json.loads((release/'scripts.json').read_text()),[],exclude_binaries=True,name='StrictRecords',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False,icon=[str(root/'app_icon.ico')])
