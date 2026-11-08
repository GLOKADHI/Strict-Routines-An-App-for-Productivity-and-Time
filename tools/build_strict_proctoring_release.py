"""Seal the Windows proctoring update; retain verified native/runtime assets."""
import ast
import filecmp
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
VARIANT='CheckSettings' if '--check-settings' in sys.argv else 'PreflightDemo' if '--preflight-demo' in sys.argv else 'SplitStudy' if '--split-study' in sys.argv else 'AutoProof' if '--auto-proof' in sys.argv else 'Proctoring'
SLUG='check-settings' if VARIANT=='CheckSettings' else 'preflight-demo' if VARIANT=='PreflightDemo' else 'split-study' if VARIANT=='SplitStudy' else 'auto-proof' if VARIANT=='AutoProof' else 'proctoring'
SEALED=ROOT/f'.codex-work/{SLUG}-sealed-20261103'
RELEASE=ROOT/f'releases/StrictRecords-{VARIANT}-20261103'
REPORT=ROOT/f'reports/strict-{SLUG}-20261103'


def main():
    reference=ROOT/'releases/StrictRecords-MobileIntegrity-20261102'
    previous=reference/'Windows/StrictRecords'
    assert hashlib.sha256((previous/'StrictRecords.exe').read_bytes()).hexdigest()==(reference/'sha256.txt').read_text().split()[0]
    saved=ast.literal_eval((ROOT/'.codex-work/youtube-reward-build-20261031/YouTubeReward-20261031/Analysis-00.toc').read_text(encoding='utf-8'))
    from PyInstaller.building.build_main import Analysis
    analysis=dict(zip([field[0] for field in Analysis._GUTS],saved))
    assert analysis['_python_version']==sys.version
    pure={row[0]:row for row in analysis['pure']}
    def snapshot(path,name):
        destination=SEALED/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
        return name,str(destination),'PYMODULE'
    for folder in ('core','windows','cloud_points','strictfocus'):
        for path in (ROOT/folder).rglob('*.py'):
            name='.'.join(path.relative_to(ROOT).with_suffix('').parts)
            if name.endswith('.__init__'):name=name[:-9]
            pure[name]=snapshot(path,name)
    for path in ROOT.glob('*.py'):snapshot(path,path.stem)
    for package in ('pycaw','comtypes'):
        base=Path(importlib.util.find_spec(package).origin).parent
        for path in base.rglob('*.py'):
            if 'test' in path.parts or 'tests' in path.parts:continue
            name='.'.join((package,*path.relative_to(base).with_suffix('').parts))
            if name.endswith('.__init__'):name=name[:-9]
            target=SEALED/'dependencies'/path.relative_to(base.parent)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
            pure[name]=(name,str(target),'PYMODULE')
    module='cryptography.hazmat.primitives.ciphers.aead'
    path=Path(importlib.util.find_spec(module).origin)
    target=SEALED/'dependencies'/Path(*module.split('.')).with_suffix('.py')
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    pure[module]=(module,str(target),'PYMODULE')
    RELEASE.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    (RELEASE/'pure.json').write_text(json.dumps(list(pure.values())))
    scripts=[(name,str(SEALED/'desktop_app.py') if name=='desktop_app' else source,kind) for name,source,kind in analysis['scripts']]
    (RELEASE/'scripts.json').write_text(json.dumps(scripts))
    hashes={p.relative_to(SEALED).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('core','windows','cloud_points','strictfocus') for p in (SEALED/folder).rglob('*.py')}
    hashes['desktop_app.py']=hashlib.sha256((SEALED/'desktop_app.py').read_bytes()).hexdigest()
    (RELEASE/'source-sha256.json').write_text(json.dumps(hashes,indent=2))
    spec=ROOT/f'{VARIANT}-20261103.spec'
    spec.write_text(f"""import json
from pathlib import Path
root=Path(SPECPATH);release=root/'releases/StrictRecords-{VARIANT}-20261103'
pure=json.loads((release/'pure.json').read_text());pyz=PYZ(pure)
exe=EXE(pyz,json.loads((release/'scripts.json').read_text()),[],exclude_binaries=True,name='StrictRecords',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False,icon=[str(root/'app_icon.ico')])
""",encoding='utf-8')
    work=ROOT/f'.codex-work/{SLUG}-build-20261103'
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(RELEASE/'build'),'--workpath',str(work),str(spec)],check=True,cwd=ROOT)
    destination=RELEASE/'Windows/StrictRecords';destination.mkdir(parents=True,exist_ok=True)
    def copy_resource(source,target):
        if Path(target).is_file() and filecmp.cmp(source,target,shallow=True):return target
        return shutil.copy2(source,target)
    shutil.copytree(previous/'_internal',destination/'_internal',dirs_exist_ok=True,copy_function=copy_resource)
    executable=destination/'StrictRecords.exe'
    shutil.copy2(work/f'{VARIANT}-20261103/StrictRecords.exe',executable)
    report=REPORT/'frozen-verification.json'
    completed=subprocess.run([str(executable),'--verify-build-report',str(report)],timeout=90,cwd=ROOT)
    verification=json.loads(report.read_text())
    required=('success','strict_windows_ios_only','strict_focus_bridge_present','brave_chatgpt_import_present',
        'strict_two_fixed_tabs','strict_left_alert_sixty_seconds','strict_private_camera_thumbnail',
        'strict_ipad_pause_guard','strict_notification_restore_guard')
    assert completed.returncode==0 and all(verification.get(key) for key in required)
    if VARIANT=='SplitStudy':assert verification['strict_split_workspace_present']
    if VARIANT in ('PreflightDemo','CheckSettings'):assert verification['strict_live_preflight_present'] and verification['strict_ten_minute_demo_present']
    if VARIANT=='CheckSettings':assert verification['strict_check_settings_present'] and verification['strict_setup_camera_preview_present'] and verification['strict_titlebar_single_drag_handler']
    (RELEASE/'sha256.txt').write_text(hashlib.sha256(executable.read_bytes()).hexdigest()+'  Windows/StrictRecords/StrictRecords.exe\n')
    print(executable)


if __name__=='__main__':main()
