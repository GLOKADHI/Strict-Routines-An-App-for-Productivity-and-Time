"""Deploy only the verified Windows EXE, with database backup/fingerprints."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET
import psutil

ROOT=Path(__file__).resolve().parents[1]
AUTO_PROOF='--auto-proof' in sys.argv
SPLIT_STUDY='--split-study' in sys.argv
PREFLIGHT_DEMO='--preflight-demo' in sys.argv
CHECK_SETTINGS='--check-settings' in sys.argv
RELEASE=ROOT/('releases/StrictRecords-CheckSettings-20261103' if CHECK_SETTINGS else 'releases/StrictRecords-PreflightDemo-20261103' if PREFLIGHT_DEMO else 'releases/StrictRecords-SplitStudy-20261103' if SPLIT_STUDY else 'releases/StrictRecords-AutoProof-20261103' if AUTO_PROOF else 'releases/StrictRecords-Proctoring-20261103')
REPORT=ROOT/('reports/strict-check-settings-20261103' if CHECK_SETTINGS else 'reports/strict-preflight-demo-20261103' if PREFLIGHT_DEMO else 'reports/strict-split-study-20261103' if SPLIT_STUDY else 'reports/strict-auto-proof-20261103' if AUTO_PROOF else 'reports/strict-proctoring-20261103')
TARGET=ROOT/'.dist/StrictRecords.exe'
TABLES=('score_events','farm_ledger_events','evidence_files','hourly_submissions','devices','deduction_proposals','strict_study_sessions','strict_session_audit','strict_sessions')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def stopped():
    for process in psutil.process_iter(['exe','cmdline']):
        try:
            if process.info['exe'] and Path(process.info['exe']).resolve()==TARGET.resolve():
                raise RuntimeError('Canonical StrictRecords EXE is still running; do not replace it.')
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass


def fingerprint(connection):
    result={}
    for table in TABLES:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            result[table]=dict(exists=False)
            continue
        rows=connection.execute('SELECT * FROM '+table).fetchall()
        serialized=sorted(json.dumps(list(row),default=lambda x:x.hex() if isinstance(x,bytes) else str(x),ensure_ascii=True) for row in rows)
        result[table]=dict(exists=True,rows=len(rows),sha256=hashlib.sha256('\n'.join(serialized).encode()).hexdigest())
    return result


def main():
    verification=json.loads((REPORT/'frozen-verification.json').read_text())
    assert all(verification.get(name) for name in ('success','frozen','strict_two_fixed_tabs',
        'strict_private_camera_thumbnail','strict_left_alert_sixty_seconds','strict_ipad_pause_guard',
        'strict_notification_restore_guard','strict_windows_ios_only','brave_chatgpt_import_present'))
    test_report=ROOT/'reports/strict-proctoring-20261103/auto-proof-tests.xml' if AUTO_PROOF else REPORT/'affected-tests.xml'
    suites=ET.parse(test_report).getroot().iter('testsuite')
    counts=[(int(s.get('tests','0')),int(s.get('failures','0')),int(s.get('errors','0'))) for s in suites]
    assert sum(row[0] for row in counts)>=(100 if PREFLIGHT_DEMO or CHECK_SETTINGS else 31 if SPLIT_STUDY else 48 if AUTO_PROOF else 199) and all(row[1]==row[2]==0 for row in counts)
    if CHECK_SETTINGS:
        assert verification['strict_check_settings_present'] and verification['strict_setup_camera_preview_present'] and verification['strict_titlebar_single_drag_handler'] and verification['strict_live_preflight_present'] and verification['strict_ten_minute_demo_present']
    elif PREFLIGHT_DEMO:
        assert verification['strict_live_preflight_present'] and verification['strict_ten_minute_demo_present']
    elif SPLIT_STUDY:
        assert verification['strict_split_workspace_present']
        assert json.loads((REPORT/'test_frozen/split-check.json').read_text())['success']
        assert json.loads((REPORT/'frozen-render-check.json').read_text())['success']
    elif not AUTO_PROOF:assert '3 passed' in (REPORT/'resume-recovery-tests.log').read_text()
    source=RELEASE/'Windows/StrictRecords/StrictRecords.exe'
    expected=(RELEASE/'sha256.txt').read_text().split()[0]
    assert digest(source)==expected
    hashes=json.loads((RELEASE/'source-sha256.json').read_text())
    owned=('desktop_app.py','core/strict_ipad_connection.py','core/ui_dashboard.py',
        'windows/strict_guard_proctor.py','windows/strict_audio_endpoints.py','windows/strict_audio_controller.py',
        'windows/strict_headphone_detector.py','windows/strict_study_hardware.py','windows/strict_study_workflow.py',
        'windows/strict_session_manager.py','windows/strict_notification_filter.py','windows/notification_service.py',
        'windows/strict_focus_bridge.py','windows/embedded_strict_focus.py','windows/single_instance_manager.py',
        'windows/desktop_overlay_validation.py')
    if SPLIT_STUDY:owned+=('windows/strict_focus_layout.py',)
    if PREFLIGHT_DEMO or CHECK_SETTINGS:owned+=('windows/strict_preflight.py','windows/strict_demo_workflow.py','windows/strict_focus_layout.py',
        'windows/strict_session_preparation.py','core/strict_session_views.py','core/strict_media_picker.py','core/server.py')
    if CHECK_SETTINGS:owned+=('core/strict_check_settings.py','core/strict_camera_preview.py','windows/strict_setup_preview.py')
    assert all(hashes[name]==digest(ROOT/name) for name in owned),'Owned source changed after sealing'
    stopped()
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    backup=REPORT/('before-proctoring-'+stamp);backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(TARGET,backup/'StrictRecords.exe')
    with sqlite3.connect((ROOT/'data/strictrecords.db').as_uri()+'?mode=ro',uri=True) as connection:
        before=fingerprint(connection)
        with sqlite3.connect(backup/'strictrecords.db') as saved:connection.backup(saved)
        temporary=TARGET.with_suffix('.exe.proctoring-new')
        shutil.copy2(source,temporary);assert digest(temporary)==expected
        stopped();os.replace(temporary,TARGET)
        after=fingerprint(connection)
    preserved={table:dict(unchanged=before[table]==after[table]) for table in TABLES}
    (REPORT/'data-preservation.json').write_text(json.dumps(preserved,indent=2))
    result=dict(executable=str(TARGET),sha256=expected,backup=str(backup),
        points_and_proof_tables_unchanged=all(value['unchanged'] for value in preserved.values()),
        apk_modified=False,extension_modified=False,services_restarted=False)
    (REPORT/'deployment.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    assert result['points_and_proof_tables_unchanged']


if __name__=='__main__':main()
