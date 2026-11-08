"""iOS-only session observations; legacy Android metadata cannot interfere."""
import hashlib
import sqlite3
import pytest
from core.strict_mobile_integrity import MobileSessionIntegrity, session_interrupted, SCHEMA


@pytest.fixture
def rig(tmp_path):
    class DB:
        def _connect(self):
            connection=sqlite3.connect(tmp_path/'integrity.sqlite');connection.row_factory=sqlite3.Row;return connection
    db=DB();now=[1000]
    with db._connect() as c:
        c.executescript('''CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY,account_id TEXT,status TEXT);
        CREATE TABLE strict_join_participants(preparation_id TEXT,role TEXT,credential_hash TEXT,client_kind TEXT);
        CREATE TABLE strict_study_sessions(session_id TEXT,account_id TEXT,state TEXT);
        CREATE TABLE score_events(id TEXT,value INTEGER); INSERT INTO score_events VALUES('untouched',81);
        INSERT INTO strict_preparations VALUES('prep','user','ACTIVE');
        INSERT INTO strict_study_sessions VALUES('session','user','ACTIVE_FOCUS');''')
        c.execute('INSERT INTO strict_join_participants VALUES(?,?,?,?)',('prep','ANDROID',hashlib.sha256(b'old').hexdigest(),'ANDROID_NATIVE'))
        c.execute('INSERT INTO strict_join_participants VALUES(?,?,?,?)',('prep','IOS',hashlib.sha256(b'ios').hexdigest(),'BROWSER'))
    return db,MobileSessionIntegrity(db,lambda:now[0]),now


def report(state,sequence=1,**changes):
    value=dict(state=state,sequence=sequence,session_id='session');value.update(changes)
    return dict(visible=state=='FOREGROUND',integrity=value)


@pytest.mark.parametrize('state',['SCREEN_OFF_ALLOWED','SCREEN_LOCKED','BACKGROUND_REASON_UNKNOWN','RECONNECTING','SESSION_CLOSED_PENDING','FOREGROUND','APP_SWITCH_CONFIRMED'])
def test_browser_observations_never_manufacture_app_switch_or_points(rig,state):
    db,service,_=rig
    before=[tuple(row) for row in db._connect().execute('SELECT * FROM score_events')]
    observation=service.observe('prep','IOS','ios',report(state))
    assert not observation['confirmed'] and not session_interrupted(db,'user','session')
    assert [tuple(row) for row in db._connect().execute('SELECT * FROM score_events')]==before


def test_removed_android_cannot_submit_new_observations(rig):
    _,service,_=rig
    with pytest.raises(PermissionError):service.observe('prep','ANDROID','old',report('APP_SWITCH_CONFIRMED'))
    assert service.states('prep')=={}


def test_legacy_android_pause_is_ignored_without_deleting_history(rig):
    db,service,_=rig
    with db._connect() as c:
        c.execute(SCHEMA)
        c.execute('INSERT INTO strict_mobile_integrity VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('prep','ANDROID','session',1,'APP_SWITCH_CONFIRMED',1,'ANDROID_NATIVE',1000,44000,''))
    assert not session_interrupted(db,'user','session') and service.states('prep')=={}
    reopened=MobileSessionIntegrity(db)
    assert not session_interrupted(db,'user','session') and reopened.states('prep')=={}
    with db._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strict_mobile_integrity').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]==1


def test_credentials_session_replay_and_duplicate_events_remain_bound(rig):
    _,service,_=rig
    with pytest.raises(PermissionError):service.observe('prep','IOS','wrong',report('FOREGROUND'))
    with pytest.raises(ValueError):service.observe('prep','IOS','ios',report('FOREGROUND',session_id='wrong'))
    first=service.observe('prep','IOS','ios',report('FOREGROUND',5))
    assert service.observe('prep','IOS','ios',report('BACKGROUND_REASON_UNKNOWN',5))==first
    with pytest.raises(ValueError):service.observe('prep','IOS','ios',report('FOREGROUND',4))


def test_browser_page_exit_retains_an_honest_unconfirmed_warning(rig):
    _,service,_=rig
    observed=service.observe('prep','IOS','ios',report('BACKGROUND_REASON_UNKNOWN',reason='PAGE_EXIT_OBSERVED'))
    assert observed['reason']=='PAGE_EXIT_OBSERVED' and not observed['confirmed']
