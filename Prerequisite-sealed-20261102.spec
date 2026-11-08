import ast
from pathlib import Path
import sys
from PyInstaller.building.build_main import Analysis
root=Path(SPECPATH);sealed=root/'.codex-work/strict-prerequisite-sealed-20261102'
saved=ast.literal_eval((root/'.codex-work/youtube-reward-build-20261031/YouTubeReward-20261031/Analysis-00.toc').read_text(encoding='utf-8'))
a=dict(zip([field[0] for field in Analysis._GUTS],saved));assert a['_python_version']==sys.version
def seal(row):
 name,source,kind=row
 try:relative=Path(source).resolve().relative_to(root)
 except ValueError:return row
 current=root/relative;target=sealed/relative
 if not target.is_file() and current.is_file():
  import shutil
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(current,target)
 return (name,str(target),kind) if target.is_file() else row
pure=[seal(row) for row in a['pure']];names={r[0] for r in pure}
for package in ('core','windows','cloud_points','strictfocus'):
 for path in (sealed/package).rglob('*.py'):
  name='.'.join(path.relative_to(sealed).with_suffix('').parts)
  if name.endswith('.__init__'):name=name[:-9]
  if name not in names:pure.append((name,str(path),'PYMODULE'));names.add(name)
datas={r[0]:seal(r) for r in a['datas']}
for folder in ('assets','browser_extension','browser_extension_monitor','shared','taufa_extension','windows/native_host_runtime'):
 for path in (sealed/folder).rglob('*'):
  if path.is_file():
   relative=str(path.relative_to(sealed));datas[relative]=(relative,str(path),'DATA')
for filename in ('windows/StrictRecordsNativeBridge.exe',):datas[filename]=(filename,str(sealed/filename),'DATA')
assert not any(str(r[1]).endswith(('.db','.sqlite')) for r in datas.values())
pyz=PYZ(pure)
exe=EXE(pyz,[seal(r) for r in a['scripts']],[],exclude_binaries=True,name='StrictRecords',debug=False,bootloader_ignore_signals=False,strip=False,upx=True,console=False,disable_windowed_traceback=False,argv_emulation=False,target_arch=None,codesign_identity=None,entitlements_file=None,icon=[str(root/'app_icon.ico')])
coll=COLLECT(exe,a['binaries'],list(datas.values()),strip=False,upx=True,upx_exclude=[],name='StrictRecords')
