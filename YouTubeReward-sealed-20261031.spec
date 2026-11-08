import ast
from pathlib import Path
import sys
from PyInstaller.building.build_main import Analysis
root=Path(SPECPATH)
sealed=root/'.codex-work/youtube-reward-windows-sealed-20261031'
saved=ast.literal_eval((root/'.codex-work/youtube-reward-build-20261031/YouTubeReward-20261031/Analysis-00.toc').read_text(encoding='utf-8'))
fields=[field[0] for field in Analysis._GUTS]
assert len(saved)==len(fields)
a=dict(zip(fields,saved));assert a['_python_version']==sys.version
pure=list(a['pure']);names={r[0] for r in pure}
extras=['core.youtube_reward_cloud','cloud_points.youtube_rewards','cloud_points.demo_access','core.google_auth','core.google_drive','core.drive_backup','core.cloud_sync_manager','core.client_account','core.client_parity','windows.google_connection_check']
for name in extras:
    path=sealed/Path(name.replace('.','/')+'.py')
    if path.is_file() and name not in names:pure.append((name,str(path),'PYMODULE'));names.add(name)
def rewrite(row):
    name,source,kind=row
    try: relative=Path(source).resolve().relative_to(root)
    except ValueError:return row
    replacement=sealed/relative
    return (name,str(replacement),kind) if replacement.is_file() else row
pure=[rewrite(r) for r in pure]
scripts=[rewrite(r) for r in a['scripts']]
datas=[rewrite(r) for r in a['datas']]
pyz=PYZ(pure)
exe=EXE(pyz,scripts,[],exclude_binaries=True,name='StrictRecords',debug=False,bootloader_ignore_signals=False,strip=False,upx=True,console=False,disable_windowed_traceback=False,argv_emulation=False,target_arch=None,codesign_identity=None,entitlements_file=None,icon=[str(root/'app_icon.ico')])
coll=COLLECT(exe,a['binaries'],datas,strip=False,upx=True,upx_exclude=[],name='StrictRecords')
