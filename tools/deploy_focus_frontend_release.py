"""Apply the validated frontend EXE without restarting the separate backend."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.deploy_strict_proctoring_release import fingerprint

REPORT=ROOT/'reports/strict-focus-frontend-20261103'
TARGET=ROOT/'.dist/StrictRecords.exe'
SOURCE=ROOT/'releases/StrictRecords-FocusFrontend-20261103-r5/Windows/StrictRecords/StrictRecords.exe'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    receipt=json.loads((REPORT/'package-preservation.json').read_text())
    assert all(receipt[key] for key in ('all_non_frontend_modules_byte_identical','bootstrap_byte_identical','runtime_files_byte_identical'))
    assert digest(SOURCE)==receipt['exe_sha256']
    assert digest(TARGET)==receipt['base_sha256'],'Installed EXE changed; rebase before deployment.'
    functional=json.loads((REPORT/'test-frozen-r5-final-verified/native-functional-checks.json').read_text())
    assert functional['success'] and functional['executable_sha256']==receipt['exe_sha256'],'Run native checks on the final packaged EXE first.'
    for name,expected in receipt['source_sha256'].items():
        if name!='core/ui_dashboard.py':assert digest(ROOT/name)==expected,'Source changed after verification: '+name
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/strict_session/status',timeout=10) as response:status=json.load(response)
    assert not status.get('session_id'),'Active session detected; leave it running.'
    stop_pid=int(sys.argv[sys.argv.index('--stop-pid')+1])
    process=psutil.Process(stop_pid)
    assert Path(process.exe()).resolve()==TARGET.resolve()
    process.terminate();process.wait(8)
    assert not any(Path(p.info['exe']).resolve()==TARGET.resolve() for p in psutil.process_iter(['exe']) if p.info['exe'])
    backup=REPORT/('before-final-frontend-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False);shutil.copy2(TARGET,backup/'StrictRecords.exe')
    with sqlite3.connect((ROOT/'data/strictrecords.db').as_uri()+'?mode=ro',uri=True) as connection:
        before=fingerprint(connection)
        with sqlite3.connect(backup/'strictrecords.db') as saved:connection.backup(saved)
        staged=TARGET.with_suffix('.exe.frontend-new');shutil.copy2(SOURCE,staged)
        assert digest(staged)==receipt['exe_sha256'];os.replace(staged,TARGET)
        after=fingerprint(connection)
    assert before==after,'A background data change occurred during deployment; inspect the receipt.'
    result=dict(executable=str(TARGET),sha256=digest(TARGET),backup=str(backup),
        data_tables_unchanged=before==after,backend_process_stopped=False,
        apk_modified=False,extension_modified=False)
    (REPORT/'data-preservation-final.json').write_text(json.dumps(dict(before=before,after=after),indent=2))
    (REPORT/'deployment-final.json').write_text(json.dumps(result,indent=2))
    subprocess.run([str(TARGET),'--verify-build-report',str(REPORT/'installed-verification.json')],cwd=ROOT,timeout=90,check=True)
    assert json.loads((REPORT/'installed-verification.json').read_text())['success']
    print(json.dumps(result))


if __name__=='__main__':main()
