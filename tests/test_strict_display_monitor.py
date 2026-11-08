import hashlib
import json
import sqlite3
import pytest
from windows.strict_display_monitor import read_display_snapshot
from windows.strict_display_warnings import derive_warnings


@pytest.fixture
def fixture(tmp_path):
    path=tmp_path/'display.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript('''CREATE TABLE strict_preparations(preparation_id TEXT,account_id TEXT,status TEXT,created REAL,expires_at REAL);
        CREATE TABLE strict_join_participants(preparation_id TEXT,role TEXT,seen REAL,report_json TEXT);
        CREATE TABLE strict_study_sessions(account_id TEXT,updated_at TEXT,data_json TEXT);
        CREATE TABLE score_events(id TEXT); INSERT INTO score_events VALUES('retained');''')
        c.execute("INSERT INTO strict_preparations VALUES('prep','user_lok','PENDING',900,2000)")
        c.execute('INSERT INTO strict_join_participants VALUES(?,?,?,?)',('prep','IOS',995,json.dumps(dict(visible=True,fullscreen=False,display_mode='expanded',timer_ready=True))))
    return path


def test_snapshot_and_warning_leave_database_byte_for_byte_unchanged(fixture):
    before=hashlib.sha256(fixture.read_bytes()).hexdigest()
    snapshot=read_display_snapshot(fixture,now=1000)
    assert not snapshot['windows_expected']
    warnings=derive_warnings(snapshot['preparation'])
    assert warnings==[]
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()==before


def test_stale_phone_never_manufactures_a_switch_violation(fixture):
    snapshot=read_display_snapshot(fixture,now=1020)
    assert derive_warnings(snapshot['preparation'])==[]


def test_snapshot_is_account_scoped_and_pending_expiry_is_respected(fixture):
    assert read_display_snapshot(fixture,now=1000,account='other')['preparation'] is None
    assert read_display_snapshot(fixture,now=2000)['preparation'] is None


def test_active_monitoring_checks_windows_and_retains_long_session(fixture):
    with sqlite3.connect(fixture) as c:
        c.execute("UPDATE strict_preparations SET status='ACTIVE'")
        c.execute('INSERT INTO strict_study_sessions VALUES(?,?,?)',('user_lok','2026-11-02',json.dumps(dict(state='ACTIVE_FOCUS',monitoring_resumed=True))))
    assert read_display_snapshot(fixture,now=3000)['windows_expected']
    with sqlite3.connect(fixture) as c:c.execute('UPDATE strict_study_sessions SET data_json=?',(json.dumps(dict(state='REWARD_ACTIVE',monitoring_resumed=True)),))
    assert not read_display_snapshot(fixture,now=3000)['windows_expected']


def test_reader_never_creates_missing_user_database(tmp_path):
    path=tmp_path/'missing.sqlite'
    with pytest.raises(sqlite3.OperationalError):read_display_snapshot(path,now=1000)
    assert not path.exists()
