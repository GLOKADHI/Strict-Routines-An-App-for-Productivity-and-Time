"""Reversible onedir update while the canonical EXE is stopped. Never opens user DB."""
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
import psutil

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'releases/StrictRecords-StrictStudy-20261102/Windows/StrictRecords'
TARGET=ROOT/'.dist'
REPORT=ROOT/'reports/strict-study-20261102'


def digest(path):
    sha=hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def assert_stopped():
    for process in psutil.process_iter(['name','exe']):
        try:
            if process.info['exe'] and Path(process.info['exe']).resolve()==(TARGET/'StrictRecords.exe').resolve():
                raise RuntimeError('Canonical StrictRecords is still running; keep the verified build ready.')
        except (psutil.AccessDenied,psutil.NoSuchProcess):pass


def main():
    assert SOURCE.resolve().is_relative_to(ROOT.resolve()) and TARGET.resolve().is_relative_to(ROOT.resolve())
    verification=json.loads((REPORT/'frozen-verification.json').read_text())
    assert verification['success'] and verification['strict_study_camera_runtime_present'] and verification['strict_study_native_hud_available']
    assert '192 passed' in (REPORT/'verified-tests.log').read_text()
    expected=(SOURCE.parents[1]/'sha256.txt').read_text().split()[0]
    assert digest(SOURCE/'StrictRecords.exe')==expected
    sealed=json.loads((SOURCE.parents[1]/'source-sha256.json').read_text())
    drift=[name for name,value in sealed.items() if (ROOT/name).is_file() and digest(ROOT/name)!=value]
    if drift:raise RuntimeError('Source changed after sealing: '+', '.join(drift))
    assert_stopped()
    backup=TARGET/'backups'/('before-strict-study-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    changes=[]
    files=sorted((p for p in SOURCE.rglob('*') if p.is_file()),key=lambda p:p.name=='StrictRecords.exe')
    for source in files:
        relative=source.relative_to(SOURCE);destination=TARGET/relative
        assert destination.resolve().is_relative_to(TARGET.resolve())
        assert source.suffix.lower() not in {'.db','.sqlite','.sqlite3'}
        current=digest(source)
        if destination.is_file() and digest(destination)==current:continue
        if destination.is_file():
            old=backup/relative;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,old)
        destination.parent.mkdir(parents=True,exist_ok=True)
        if source.name=='StrictRecords.exe':assert_stopped()
        temporary=destination.with_name(destination.name+'.strict-study-new')
        shutil.copy2(source,temporary);os.replace(temporary,destination)
        changes.append(str(relative))
    result=dict(executable=str(TARGET/'StrictRecords.exe'),sha256=expected,backup=str(backup),
                updated_files=changes,production_app_started=False,user_database_opened=False,
                apk_built=False,android_app_modified=False)
    assert digest(TARGET/'StrictRecords.exe')==expected
    (REPORT/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='updated_files'},indent=2))


if __name__=='__main__':main()
