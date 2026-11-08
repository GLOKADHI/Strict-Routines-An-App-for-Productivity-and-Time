"""Install the validated ZIP frontend and its assets, preserving backend/data."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.deploy_strict_proctoring_release import fingerprint
from tools.build_zip_frontend_release import tree_hashes

REPORT=ROOT/'reports/strict-session-zip-20261103'
RELEASE=ROOT/'releases/StrictRecords-ZipFrontend-20261103/Windows/StrictRecords'
TARGET=ROOT/'.dist/StrictRecords.exe'


def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()


def main():
    receipt=json.loads((REPORT/'package-preservation.json').read_text())
    checks=json.loads((REPORT/'test-frozen/zip-native-functional-checks.json').read_text())
    assert checks['success'] and checks['executable_sha256']==receipt['exe_sha256']
    assert all(receipt[k] for k in ('bootstrap_byte_identical','all_other_modules_byte_identical','existing_runtime_byte_identical'))
    assert digest(TARGET)==receipt['base_sha256'],'Installed build changed; rebase first.'
    assert digest(RELEASE/'StrictRecords.exe')==receipt['exe_sha256']
    assert tree_hashes(RELEASE/'_internal/strict_session_frontend')==receipt['frontend_assets']
    for name,expected in receipt['source_sha256'].items():assert digest(ROOT/name)==expected
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/strict_session/status',timeout=10) as response:status=json.load(response)
    assert not status.get('session_id'),'An active session must finish before the app is restarted.'
    pid=int(sys.argv[sys.argv.index('--stop-pid')+1]);process=psutil.Process(pid)
    assert Path(process.exe()).resolve()==TARGET.resolve()
    process.terminate();process.wait(10)
    for candidate in psutil.process_iter(['exe']):
        if candidate.info['exe']:assert Path(candidate.info['exe']).resolve()!=TARGET.resolve()
    backend_pid=globals().get('BACKEND_STOP_PID')
    if backend_pid:
        backend=psutil.Process(backend_pid);arguments=backend.cmdline()
        assert '-m' in arguments and arguments[arguments.index('-m')+1]=='core.server'
        assert '--port' in arguments and arguments[arguments.index('--port')+1]=='8765'
        backend.terminate();backend.wait(10)
    backup=REPORT/('before-zip-frontend-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True);shutil.copy2(TARGET,backup/'StrictRecords.exe')
    for relative,expected in receipt.get('extra_assets',{}).items():
        source=RELEASE/'_internal'/relative;destination=TARGET.parent/'_internal'/relative
        assert digest(source)==expected
        if destination.exists():
            saved=backup/'extra_assets'/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,saved)
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        assert digest(destination)==expected
    assets=TARGET.parent/'_internal/strict_session_frontend'
    staged=TARGET.with_suffix('.exe.zip-new');staged_assets=assets.with_name('strict_session_frontend.zip-new')
    assert not staged_assets.exists(),'Unfinished staged assets exist; inspect before retry.'
    shutil.copy2(RELEASE/'StrictRecords.exe',staged)
    shutil.copytree(RELEASE/'_internal/strict_session_frontend',staged_assets)
    assert digest(staged)==receipt['exe_sha256'] and tree_hashes(staged_assets)==receipt['frontend_assets']
    with sqlite3.connect((ROOT/'data/strictrecords.db').as_uri()+'?mode=ro',uri=True) as db:
        before=fingerprint(db)
        with sqlite3.connect(backup/'strictrecords.db') as saved:db.backup(saved)
        if assets.exists():shutil.move(str(assets),str(backup/'strict_session_frontend'))
        try:
            os.replace(staged_assets,assets);os.replace(staged,TARGET)
        except Exception:
            shutil.copy2(backup/'StrictRecords.exe',TARGET)
            if (backup/'strict_session_frontend').exists():
                if assets.exists():
                    assert assets.resolve().is_relative_to(TARGET.parent.resolve())
                    shutil.rmtree(assets)
                shutil.copytree(backup/'strict_session_frontend',assets)
            raise
        after=fingerprint(db)
    assert before==after,'Concurrent user-data change detected; review data receipt.'
    subprocess.run([str(TARGET),'--verify-build-report',str(REPORT/'installed-verification.json')],cwd=ROOT,check=True,timeout=90)
    assert json.loads((REPORT/'installed-verification.json').read_text())['success']
    result=dict(executable=str(TARGET),sha256=digest(TARGET),frontend_assets_verified=tree_hashes(assets)==receipt['frontend_assets'],
        data_tables_unchanged=True,backup=str(backup),separate_backend_stopped=bool(backend_pid),apk_modified=False,extension_modified=False)
    (REPORT/'data-preservation.json').write_text(json.dumps(dict(before=before,after=after),indent=2))
    (REPORT/'deployment.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
