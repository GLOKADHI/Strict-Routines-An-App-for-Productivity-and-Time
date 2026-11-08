"""Shared-client contracts, account isolation, and non-authoritative Drive imports."""
import hashlib
import json
import sqlite3
import threading
from unittest.mock import MagicMock

import pytest

from cloud_points.demo_access import state as demo_state, DemoUnavailable
from core.client_account import ClientAccountConnection
from core.client_parity import ClientParityService
from core.cloud_sync_manager import CloudSyncManager
from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.paired_demo_service import LocalAccountDemoStore, PairedDemoStore
from core.scoring_engine import ScoringEngine
from core.drive_backup import DriveBackupCoordinator
from core.strictbot_assistant import AssistantPrincipal


@pytest.fixture
def db(tmp_path):
    return StrictRecordsDB(str(tmp_path/'client.sqlite'))


def test_calendar_and_summary_use_the_same_persisted_activity_set(db):
    service=ClientParityService(db,ScoringEngine(db))
    result=service.state('user_lok','2026-10-31')
    month=service.month('user_lok','2026-10')
    assert result['summary']['activity_overview']['scheduled_tasks']==len(result['activities'])
    assert {a['activity_id'] for a in result['activities']}=={a['activity_id'] for a in month['activities'] if a['date']==result['date']}
    assert not service.month('other-account','2026-10')['activities']
    assert not result['capabilities']['native_android_blocking']


def test_drive_backups_never_insert_remote_score_events(db):
    auth=MagicMock();auth.get_account_sub.return_value='google-sub'
    sync=CloudSyncManager(db,auth_manager=auth)
    sync.drive_client=MagicMock()
    sync.drive_client.list_files.return_value=[{'id':'drive-file','name':'event_1.json'}]
    raw=json.dumps({'event_id':'untrusted-event','points':9000}).encode()
    sync.drive_client.download_file_bytes.return_value=raw
    before=db.get_score_events('2026-10-31')
    assert sync._sync_point_events_from_drive()==0
    assert sync._sync_point_events_from_drive()==0
    assert db.get_score_events('2026-10-31')==before
    with db._connect() as conn:
        row=conn.execute('SELECT * FROM drive_pending_records').fetchall()
        assert len(row)==1 and row[0]['payload']==raw
        assert row[0]['sha256']==hashlib.sha256(raw).hexdigest()


def test_account_switch_retains_pending_evidence_and_queue(db):
    with db._connect() as conn:
        conn.execute("INSERT INTO cloud_sync_files(file_id,local_path,category,created_at) VALUES('file','original.jpg','Evidence','2026-10-31')")
        conn.execute("INSERT INTO cloud_sync_queue(queue_id,file_id,created_at,updated_at) VALUES('queue','file','2026-10-31','2026-10-31')")
    sync=CloudSyncManager(db,auth_manager=MagicMock())
    sync._handle_account_switch('old-sub','new-sub')
    with db._connect() as conn:
        assert conn.execute('SELECT status FROM cloud_sync_queue').fetchone()[0]=='ACCOUNT_SWITCH_PENDING'
        assert conn.execute('SELECT local_path,sync_status FROM cloud_sync_files').fetchone()[:]==('original.jpg','ACCOUNT_SWITCH_PENDING')


def test_desktop_signin_does_not_request_drive_or_start_sync(db):
    auth=MagicMock();sync=MagicMock();done=threading.Event()
    auth.start_pkce_flow.side_effect=lambda **kwargs:done.set()
    service=ClientAccountConnection(db,auth=auth,sync=sync)
    assert service.action('user_lok','sign-in',{})['pending']
    assert done.wait(2)
    auth.start_pkce_flow.assert_called_once_with(include_drive_scope=False,force_account_select=True)
    sync.sync_now.assert_not_called()
    with pytest.raises(PermissionError):service.action('user_lok','sync',{})


def test_native_and_phone_share_one_demo_allowance(db):
    with db._connect() as conn:
        conn.execute("INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status,pairing_state,status) VALUES('phone','user_lok','Phone','ANDROID','fixture','now','SYNCED','REGISTERED','ONLINE')")
        conn.execute("INSERT INTO device_credentials(credential_id,device_id,account_id,token_hash,issued_at) VALUES('credential','phone','user_lok',?,'now')",(hashlib.sha256(b'fixture-token').hexdigest(),))
    phone=PairedDemoStore(db.db_path,'phone','fixture-token')
    principal=phone.authenticate()
    before=db.get_score_events('2026-10-31')
    result=demo_state(phone,principal,activate=True)
    assert result['demo_access']['effective_access_points']==81
    desktop=LocalAccountDemoStore(db.db_path,'user_lok')
    assert demo_state(desktop,principal)['demo_access']['status']=='ACTIVE'
    with pytest.raises(DemoUnavailable):demo_state(desktop,principal,activate=True)
    assert db.get_score_events('2026-10-31')==before
    # A stored credential digest is not itself a bearer credential.
    with pytest.raises(PermissionError):PairedDemoStore(db.db_path,'phone',hashlib.sha256(b'fixture-token').hexdigest()).authenticate()


def test_failed_gym_revision_rolls_back_the_whole_daily_setup(db):
    from core.gym_daily_decision import GymDecisionError
    from core.time_window_engine import now_ist
    from datetime import timedelta
    day=(now_ist().date()+timedelta(days=1)).isoformat()
    service=ClientParityService(db,ScoringEngine(db))
    before=DailyCollegeDecision(db).get('user_lok',day)
    body=dict(date=day,request_id='routine-fixture-rollback',attends_college=True,college_revision=0,
        gym_planned=False,gym_revision=999,wake_up_time='07:00',bath_plans=[dict(planned_time='07:30',window_start='08:00',window_end='08:30')])
    with pytest.raises(GymDecisionError):
        service.confirm_entry(AssistantPrincipal('user_lok','user_lok','windows_pc'),body)
    assert DailyCollegeDecision(db).get('user_lok',day)==before
    with db._connect() as conn:
        assert not conn.execute('SELECT 1 FROM college_decision_requests WHERE request_id=?',(body['request_id']+'-college',)).fetchone()


def test_original_files_are_deduplicated_in_the_account_scoped_upload_queue(db,tmp_path):
    auth=MagicMock();auth.get_account_sub.return_value='google-sub';auth.is_authenticated.return_value=True
    auth.is_drive_connected.return_value=True
    coordinator=DriveBackupCoordinator(db,auth)
    from core.client_account import ensure_configuration
    ensure_configuration(db)
    with db._connect() as conn:
        conn.execute("INSERT INTO system_config(key,value) VALUES('google_identity_binding:user_lok','google-sub')")
    original=tmp_path/'original.jpg';original.write_bytes(b'original fixture bytes')
    coordinator._queue('user_lok','google-sub',original,'Evidence/Images')
    coordinator._queue('user_lok','google-sub',original,'Evidence/Images')
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM cloud_sync_queue').fetchone()[0]==1
        saved=conn.execute('SELECT * FROM cloud_sync_files').fetchone()
        assert saved['google_sub']=='google-sub' and saved['local_account_id']=='user_lok'
        assert saved['sha256_hash']==hashlib.sha256(original.read_bytes()).hexdigest()
    coordinator._export=MagicMock()
    coordinator.drive_client=MagicMock();coordinator.drive_client.upload_file.return_value={'id':'drive-fixture-id'}
    assert coordinator.sync_now()['success']
    coordinator._queue('user_lok','google-sub',original,'Evidence/Images')
    assert coordinator.sync_now()['success']
    coordinator.drive_client.upload_file.assert_called_once()
    assert db.get_score_events('2026-10-31')==[]


def test_demo_changes_only_effective_browser_score_and_keeps_independent_policies(db):
    from tests.test_extension_decision_repair import manager
    obj=manager(-7)
    obj.db.db_path=db.db_path
    obj.db.get_active_user_profile=lambda:{'user_id':'user_lok'}
    demo_state(LocalAccountDemoStore(db.db_path,'user_lok'),{'account_id':'user_lok'},activate=True)
    decision=obj.get_browser_tab_restriction('instagram')
    assert decision['allowed'] and decision['effectiveAccessScore']==81
    assert decision['currentScore']==-7 and decision['temporaryException']=='DEMO_ACCESS'
    obj.get_service_policy=lambda _: {'manuallyRestricted':True}
    assert obj.get_browser_tab_restriction('instagram')['restricted']
    with db._connect() as conn:
        conn.execute("UPDATE demo_access_state SET status='EXPIRED'")
    obj.get_service_policy=lambda _: {'manuallyRestricted':False}
    assert obj.get_browser_tab_restriction('instagram')['restricted']


def test_confirmed_routine_replay_is_idempotent_and_changed_request_is_rejected(db):
    from core.time_window_engine import now_ist
    from datetime import timedelta
    day=(now_ist().date()+timedelta(days=1)).isoformat()
    service=ClientParityService(db,ScoringEngine(db))
    body=dict(date=day,request_id='routine-fixture-confirm',attends_college=True,college_revision=0,
        gym_planned=False,gym_revision=0,wake_up_time='07:00',bath_plans=[dict(planned_time='07:30',window_start='08:00',window_end='08:30')])
    principal=AssistantPrincipal('user_lok','user_lok','windows_pc')
    first=service.confirm_entry(principal,body)
    assert first['entry_confirmed'] and first['gym']['gym_planned'] is False
    assert len([a for a in first['activities'] if a['type']=='BATH'])==1
    assert not [a for a in first['activities'] if a['type']=='GYM']
    again=service.confirm_entry(principal,body)
    assert {a['activity_id'] for a in first['activities']}=={a['activity_id'] for a in again['activities']}
    with pytest.raises(ValueError):service.confirm_entry(principal,dict(body,wake_up_time='08:00'))
