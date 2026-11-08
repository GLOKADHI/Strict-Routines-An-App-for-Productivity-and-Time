"""Rebuild current project modules using the previously validated dependency graph."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import struct
import queue
import threading
import sys

ROOT=Path(__file__).resolve().parents[1]
SEALED=ROOT/'.codex-work/overlay-sealed-20261102'
RELEASE=ROOT/'releases/StrictRecords-Overlay-20261102'

def main():
    for folder in ('core','windows','cloud_points','strictfocus'):
        for path in (ROOT/folder).rglob('*.py'):
            if '__pycache__' in path.parts:continue
            target=SEALED/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for path in ROOT.glob('*.py'):
        target=SEALED/path.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for folder in ('assets','browser_extension','browser_extension_monitor','shared','taufa_extension'):
        shutil.copytree(ROOT/folder,SEALED/folder,dirs_exist_ok=True)
    for path in (ROOT/'windows/native_host_runtime').rglob('*'):
        if path.is_file():
            target=SEALED/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    shutil.copy2(ROOT/'windows/StrictRecordsNativeBridge.exe',SEALED/'windows/StrictRecordsNativeBridge.exe')
    hashes={str(p.relative_to(SEALED)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('core','windows','cloud_points','browser_extension','assets') for p in (SEALED/folder).rglob('*') if p.is_file() and (p.suffix in ('.py','.js','.json','.html','.css') or p.name=='StrictBotNative.dll')}
    RELEASE.mkdir(parents=True,exist_ok=True)
    (RELEASE/'source-sha256.json').write_text(json.dumps(hashes,indent=2))
    spec=ROOT/'Overlay-sealed-20261102.spec'
    spec.write_text('''import ast
from pathlib import Path
import sys
from PyInstaller.building.build_main import Analysis
root=Path(SPECPATH);sealed=root/'.codex-work/overlay-sealed-20261102'
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
''',encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(RELEASE/'Windows'),'--workpath',str(ROOT/'.codex-work/overlay-build-20261102'),str(spec)],cwd=ROOT,check=True)
    executable=RELEASE/'Windows/StrictRecords/StrictRecords.exe'
    report=ROOT/'reports/desktop-overlay-20261102/frozen-verification.json'
    result=subprocess.run([str(executable),'--verify-build-report',str(report)],cwd=ROOT,timeout=90)
    if result.returncode or not report.is_file():raise RuntimeError('Frozen runtime verification failed')
    verify=json.loads(report.read_text());assert verify['night_history_assets_present'] and verify['night_native_bridge_present']
    assert verify['night_native_countdown_available'] and verify['night_restart_fallback_integrated']
    bridge=executable.parent/'_internal/windows/StrictRecordsNativeBridge.exe'
    payload=json.dumps({'action':'ping'}).encode()
    process=subprocess.Popen([str(bridge),'chrome-extension://iincmekgilffpjmmmcgloliadppioajc/'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        process.stdin.write(struct.pack('<I',len(payload))+payload);process.stdin.flush()
        replies=queue.Queue()
        def read_reply():
            size=process.stdout.read(4)
            replies.put(json.loads(process.stdout.read(struct.unpack('<I',size)[0])) if len(size)==4 else None)
        threading.Thread(target=read_reply,daemon=True).start()
        assert replies.get(timeout=12)['success']
        process.stdin.close();assert process.wait(5)==0
    finally:
        if process.poll() is None:process.terminate();process.wait(5)
    verify['frozen_native_reply_before_stdin_eof']=True
    report.write_text(json.dumps(verify,indent=2),encoding='utf-8')
    (RELEASE/'sha256.txt').write_text(hashlib.sha256(executable.read_bytes()).hexdigest()+'  Windows/StrictRecords/StrictRecords.exe\n')
    print(executable)

if __name__=='__main__':main()
