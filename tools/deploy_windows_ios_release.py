"""Install the verified Windows+iOS build only after the canonical EXE stops.

Does not stop/restart the EXE, development backend, or other tools. Never copies
databases, APKs, pairing data or browser profiles. Existing runtime files are kept.
"""
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime
import os
import psutil

ROOT=Path(__file__).resolve().parents[1]
RELEASE=ROOT/'releases/StrictRecords-MobileIntegrity-20261102'
SOURCE=RELEASE/'Windows/StrictRecords/StrictRecords.exe'
TARGET=ROOT/'.dist/StrictRecords.exe'
REPORT=ROOT/'reports/strict-prerequisite-20261102'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_stopped():
    for process in psutil.process_iter(['exe']):
        try:
            if process.info['exe'] and Path(process.info['exe']).resolve()==TARGET.resolve():
                raise RuntimeError('Close the canonical StrictRecords EXE before applying this build.')
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass


def main():
    assert TARGET.resolve().is_relative_to(ROOT.resolve())
    verification=json.loads((REPORT/'mobile-frozen-verification.json').read_text())
    assert verification['success'] and verification['strict_windows_ios_only']
    assert verification['strict_focus_bridge_present'] and verification['brave_chatgpt_import_present']
    assert '67 passed' in (REPORT/'windows-ios-affected-tests.log').read_text()
    expected=(RELEASE/'sha256.txt').read_text().split()[0]
    assert digest(SOURCE)==expected
    hashes={name.replace('\\','/'):value for name,value in json.loads((RELEASE/'source-sha256.json').read_text()).items()}
    owned=('core/strict_participants.py','core/strict_session_views.py','core/strict_companion.py',
           'core/strict_companion_client.py','core/strict_mobile_integrity.py','windows/strict_session_preparation.py',
           'windows/strict_display_monitor.py','windows/strict_display_warnings.py',
           'windows/embedded_strict_focus.py','windows/brave_study_session.py','desktop_app.py')
    assert all(hashes[name]==digest(ROOT/name) for name in owned),'Owned source changed after verification'
    assert_stopped()
    backup=ROOT/'.dist/backups'/('before-windows-ios-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(TARGET,backup/'StrictRecords.exe')
    temporary=TARGET.with_suffix('.exe.windows-ios-new')
    shutil.copy2(SOURCE,temporary)
    assert digest(temporary)==expected
    assert_stopped()
    os.replace(temporary,TARGET)
    result=dict(executable=str(TARGET),sha256=expected,backup=str(backup),
                apk_modified=False,user_data_modified=False,backend_restarted=False)
    (REPORT/'windows-ios-deployment.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
