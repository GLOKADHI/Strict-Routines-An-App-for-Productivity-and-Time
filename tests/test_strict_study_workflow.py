"""Virtual-clock, isolated-database tests. Never enable live Windows controls."""
import io
import json
from datetime import datetime, timedelta

import pytest
from PIL import Image

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST
from windows.strict_study_workflow import StrictStudyWorkflow, reward_hold


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 15, 10, tzinfo=IST)
        self.mono = 0
    def wall(self): return self.now
    def monotonic(self): return self.mono
    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture
def rig(tmp_path):
    db = StrictRecordsDB(str(tmp_path/'study.db'))
    clock = Clock()
    with db._connect() as c:
        c.execute("""INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status)
            VALUES('a55','user_lok','Galaxy A55','ANDROID','test',?,'CONNECTED')""", (clock.now.isoformat(),))
    workflow = StrictStudyWorkflow(db, clock=clock.wall, monotonic=clock.monotonic)
    return db, clock, workflow, tmp_path


def window(rig, minutes=20, ident='hourly'):
    db, clock, _, _ = rig
    start = clock.now + timedelta(minutes=minutes)
    end = start + timedelta(minutes=20)
    with db._connect() as c:
        c.execute("""INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,
            window_start,window_end,notification_time,status) VALUES(?,'user_lok','HOURLY',?,?,?,?,?,'PENDING')""",
            (ident, start.date().isoformat(), (start+timedelta(minutes=10)).isoformat(), start.isoformat(), end.isoformat(), start.isoformat()))
    return start


def advance(rig, seconds, monitored=True):
    # Each short step represents a real observation; long unobserved gaps must
    # not be credited by the production timer.
    _, clock, workflow, _ = rig
    while seconds:
        step = min(seconds, 10)
        clock.advance(step)
        workflow.tick(monitoring_ok=monitored)
        seconds -= step


def accept(rig, **overrides):
    db, clock, workflow, path = rig
    raw = io.BytesIO(); Image.new('RGB', (120,120), (50,95,150)).save(raw, format='JPEG')
    proof = ScoringEngine(db=db, storage_root=str(path)).store_evidence_file(
        date=clock.now.date().isoformat(), record_type='Hourly', related_record_id='hourly',
        device_id='a55', file_bytes=raw.getvalue(), filename_hint='proof.jpg')
    p = dict(submission_id='sub-1', activity_id='hourly', account_id='user_lok',schedule_version=1,
             source_device_id='a55', source_platform='ANDROID', task_type='HOURLY',status='SUBMITTED',
             accepted_at=clock.now.isoformat(), category='STUDY',description='Studied database transactions',
             has_evidence=True,evidence_id=proof['evidence_id'],evidence_sha256=proof['sha256_hash'])
    p.update(overrides)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET status='VALIDATING',submission_id=? WHERE activity_id='hourly'", (p['submission_id'],))
        c.execute('INSERT INTO proof_submission_events VALUES(?,?,?,?,?,?,?,?,?)',
                  ('accepted-'+p['submission_id'],p['account_id'],p['submission_id'],p['activity_id'],p['schedule_version'],p['source_device_id'],p['accepted_at'],'SUBMITTED',json.dumps(p)))
    return p


@pytest.mark.parametrize('minutes,target,completed', [(10,5,5),(15,10,10),(20,15,15),(25,20,20),(30,25,25),(34,29,29),
    (35,30,30),(37,32,32),(38,33,33),(39,34,34),(40,35,35),(50,35,40)])
def test_exact_timing_examples(rig, minutes, target, completed):
    start=window(rig,minutes); db,clock,w,_=rig
    original=start.isoformat()
    w.begin('session','user_lok','a55')
    assert w.view()['focus_target_seconds']==target*60
    advance(rig,completed*60-1)
    assert not w.s['focus_completed_at']
    advance(rig,1)
    assert w.s['focus_completed_at']==clock.now.isoformat()
    assert w.s['state'] == ('AWAITING_PROOF_WINDOW' if minutes>completed else 'AWAITING_ANDROID_SUBMISSION')
    assert not w.view()['end_enabled'] and w.s['reward_status']=='NOT_REQUESTED'
    with db._connect() as c:
        assert c.execute("SELECT window_start FROM scheduled_activities").fetchone()[0]==original


def test_310_example_wait_and_ordered_transitions(rig):
    window(rig); db,clock,w,_=rig; w.begin('session','user_lok','a55')
    advance(rig,900)
    assert clock.now.hour==15 and clock.now.minute==25
    assert w.view()['window_remaining_seconds']==300
    advance(rig,300)
    assert w.s['state']=='AWAITING_ANDROID_SUBMISSION'
    with pytest.raises(ValueError): w.complete('session')
    accept(rig); w.tick(refresh=True)
    assert w.view()['end_enabled']
    assert w.s['state']=='END_SESSION_ENABLED' and not w.s['manually_completed']
    w.complete('session')
    assert w.s['state']=='SESSION_FINALIZED'
    with db._connect() as c:
        states=[r[0] for r in c.execute('SELECT state FROM strict_study_transitions ORDER BY sequence')]
    expected=['PREPARING','ACTIVE_FOCUS','MINIMUM_DURATION_COMPLETED','FOCUS_COMPLETED','AWAITING_PROOF_WINDOW',
              'PROOF_WINDOW_OPEN','AWAITING_ANDROID_SUBMISSION','ANDROID_PROOF_SUBMITTED','SYNCING_PROOF',
              'PROOF_SYNCHRONIZED','END_SESSION_ENABLED','SESSION_FINALIZED']
    assert states==expected


@pytest.mark.parametrize('minutes',[-1,0,4,5])
def test_imminent_window_selects_following_actual_window(rig,minutes):
    window(rig,minutes); window(rig,60,'following'); _,_,w,_=rig
    w.begin('session','user_lok','a55')
    assert w.s['window']['activity_id']=='following'
    assert w.s['focus_target_seconds']==2100 and 'skipped' in w.s['message']


def test_no_positive_timer_available_is_explained(rig):
    window(rig,5)
    with pytest.raises(ValueError,match='more than five minutes'): rig[2].begin('session','user_lok','a55')


def test_schedule_changes_preserve_elapsed_and_buffer(rig):
    window(rig,50); db,clock,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,600)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?,schedule_version=2", ((clock.now+timedelta(minutes=10)).isoformat(),))
    w.tick(refresh=True)
    assert w.s['elapsed_seconds']==600 and w.s['focus_target_seconds']==900
    advance(rig,300)
    assert w.s['focus_completed_at'] and w.view()['window_remaining_seconds']==300


def test_past_recalculated_deadline_finishes_safely(rig):
    window(rig,50); db,clock,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,600)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?,schedule_version=2", (clock.now.isoformat(),))
    w.tick(refresh=True)
    assert w.s['focus_completed_at'] and w.s['elapsed_seconds']==600


@pytest.mark.parametrize('changes',[{'source_platform':'WINDOWS'},{'source_device_id':'other'},
    {'account_id':'other'},{'schedule_version':2},{'strict_session_id':'stale'},
    {'status':'FAILED'},{'has_evidence':False},{'category':''},{'description':''},
    {'accepted_at':'2026-11-02T15:29:59+05:30'}])
def test_unrelated_or_invalid_receipts_never_enable_end(rig, changes):
    window(rig); _,_,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,1200)
    accept(rig,**changes); w.tick(refresh=True)
    assert not w.view()['end_enabled']
    with pytest.raises(ValueError): w.complete('session')


def test_delayed_receipt_duplicate_and_restart(rig):
    window(rig); db,clock,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,1200)
    assert not w.view()['end_enabled']
    accept(rig); advance(rig,60); w.tick(refresh=True)
    assert w.view()['end_enabled']
    with db._connect() as c: count=c.execute('SELECT count(*) FROM strict_study_transitions').fetchone()[0]
    w.tick(refresh=True); w.tick(refresh=True)
    with db._connect() as c: assert count==c.execute('SELECT count(*) FROM strict_study_transitions').fetchone()[0]
    recovered=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic)
    recovered.recover('user_lok')
    assert recovered.s['sync_verified'] and not recovered.view()['end_enabled']
    with pytest.raises(ValueError): recovered.complete('session')
    recovered.s['monitoring_resumed']=True
    recovered.complete('session')
    assert recovered.s['manually_completed']


def test_corrupt_image_revokes_previous_sync_and_restart_never_invents_receipt(rig):
    window(rig); db,clock,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,1200)
    recovered=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic); recovered.recover('user_lok')
    assert not recovered.s['sync_verified']
    p=accept(rig); w.tick(refresh=True); assert w.view()['end_enabled']
    with db._connect() as c: c.execute('UPDATE evidence_files SET is_deleted=1 WHERE evidence_id=?',(p['evidence_id'],))
    w.tick(refresh=True); assert not w.view()['end_enabled']


def test_restart_during_focus_and_unobserved_time_never_earn_completion(rig):
    window(rig); db,clock,w,_=rig; w.begin('session','user_lok','a55'); advance(rig,60)
    clock.advance(900); w.tick()
    assert w.s['elapsed_seconds']==60
    recovered=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic); recovered.recover('user_lok')
    assert recovered.s['state']=='ABANDONED' and not recovered.view()['end_enabled']


def test_bluetooth_requires_debounced_edges_and_reconnection(rig):
    window(rig); _,_,w,_=rig; w.begin('session','user_lok','a55')
    w.bluetooth_sample(True)  # Legacy edge API needs a previously connected sample.
    assert not w.bluetooth_sample(False)
    assert not w.bluetooth_sample(None)
    assert not w.bluetooth_sample(False)
    assert not w.bluetooth_sample(False)
    for _ in range(10): assert not w.bluetooth_sample(False)
    assert w.s['bluetooth_violations']==1
    w.bluetooth_sample(True); w.bluetooth_sample(False)
    assert w.bluetooth_sample(False)
    assert w.s['state']=='FAILED' and w.s['reward_status']=='INELIGIBLE'


def test_timer_and_sync_do_not_touch_points_or_summary(rig):
    window(rig); db,_,w,_=rig
    tables=['score_events','daily_scores','point_ledger']
    def ledger():
        with db._connect() as c:
            names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in tables if t in names}
    before=ledger()
    w.begin('session','user_lok','a55'); advance(rig,1200); accept(rig); w.tick(refresh=True)
    assert reward_hold(db,'user_lok',activity_id='hourly')=='session'
    assert ledger()==before
    w.complete('session')
    assert reward_hold(db,'user_lok',activity_id='hourly') is None and ledger()==before


def manager_fixture(rig, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from windows.strict_session_manager import StrictSessionManager
    db,_,w,path=rig
    monkeypatch.setattr(StrictSessionManager,'_refresh_authoritative_score',lambda _:True)
    monkeypatch.setattr('windows.strict_study_hardware.StrictStudyHardware.view',lambda _:dict(camera='NOT_STARTED',monitoring_ok=False))
    manager=StrictSessionManager(db=db,data_dir=str(path))
    # This control/score fixture already simulates Windows hardware. Supply
    # explicitly synthetic decoded-iOS-frame evidence for the new camera gate.
    # Missing/stale/forged receiver evidence is covered by test_strict_ios_readiness.
    def received_ios_frames(*_):
        now=__import__('time').time()
        return dict(status=dict(state='IOS_CAMERA_STREAMING',streaming=True,frame_sequence=1,last_frame_at=now),
                    preview=dict(source='iPhone camera',observed_at=now,jpeg='synthetic-test-frame'))
    manager.ios_camera_gateway=SimpleNamespace(ready=True,preview=received_ios_frames)
    manager.study=w
    preparation=manager.preparation
    preparation.clock=lambda:w.clock().timestamp()
    preparation.environment=lambda _:dict(ready=True)
    pid=preparation.current('user_lok')['preparation_id']
    preparation.host_report('user_lok',pid,dict(camera=True,camera_native_id='a'*64,visible=True,secure_context=True,online=True))
    token=preparation.invite('user_lok','IOS','http://fixture.local')['invitation']['url'].split('#')[1]
    binding=preparation.join(token,'iPhone')
    def ipad_heartbeat():
        preparation.heartbeat(pid,'IOS',binding['credential'],dict(timer_ready=True,visible=True,online=True))
    ipad_heartbeat()
    manager._test_ipad_heartbeat=ipad_heartbeat
    authorize=preparation.authorize_start
    preparation.authorize_start=lambda account,requested:authorize(account,requested or pid)
    manager.notification_filter=SimpleNamespace(start=lambda:dict(active=True),status=lambda:dict(active=True),restore=Mock())
    manager.embedded_focus=SimpleNamespace(open=Mock(return_value=dict(success=True)),close=Mock(),update=Mock())
    hardware=dict(monitoring_ok=True,camera='ACTIVE',bluetooth_connected=False,bluetooth_revision=0,prohibited_audio_detected=False,
        face_tracking='VERIFIED',face_framing='VISIBLE',lighting='CLEAR',proctor_calibrated=True,attention_monitoring='ACTIVE',
        audio_detection_known=True,bluetooth_headphone_connected=False,wired_headphone_connected=False,speaker_muted=True,
        speaker_output='MUTED',wired_state_known=False,jack_detection='NOT_VERIFIED')
    manager.study_hardware=SimpleNamespace(start=lambda **_:dict(success=True),stop=Mock(),view=lambda:dict(hardware),enable_session_integrity=Mock())
    monkeypatch.setattr(manager.app_terminator, 'scan_prohibited_applications', lambda: {'prohibited_apps': []})
    manager.network_policy_manager=SimpleNamespace(apply_policy=Mock(return_value=True),update_privilege=Mock(),
        restore_network=Mock(),is_active=False,entertainment_domains=['youtube.com','instagram.com','spotify.com'])
    manager.browser_policy_manager=SimpleNamespace(configure=Mock(),launch_approved_browser=Mock(return_value=dict(success=True)),
        close_browser=Mock(),entertainment_allowed=False,is_active=False)
    for name in ('_apply_registry_policies','_restore_registry_policies','_install_keyboard_hook','_uninstall_keyboard_hook',
                 '_hide_taskbar','_show_taskbar','_start_supervisor','_update_study_hud'):
        monkeypatch.setattr(manager,name,Mock())
    class Cloud:
        configuration=lambda _:None
        def request(self,*_):raise ValueError('Test authority unconfigured')
    monkeypatch.setattr('core.youtube_reward_cloud.client',lambda _:Cloud())
    return manager,hardware


def test_manager_consent_and_actual_start_retain_education_and_block_entertainment(rig,monkeypatch):
    window(rig); manager,_=manager_fixture(rig,monkeypatch)
    assert not manager.start_session()['success']
    manager._install_keyboard_hook.assert_not_called()
    assert manager.start_session(monitoring_consent=True)['success']
    assert manager.study.view()['focus_target_seconds']==900
    domains=manager._effective_study_domains()
    assert 'chatgpt.com' in domains and 'youtube.com' not in domains
    assert not manager.evaluate_privilege()['allowed']
    manager.embedded_focus.open.assert_called_once()
    manager.browser_policy_manager.launch_approved_browser.assert_not_called()


def test_feed_lost_after_ready_blocks_activation_before_timer_or_windows_lock(rig,monkeypatch):
    from time import time
    window(rig);manager,_=manager_fixture(rig,monkeypatch)
    original=manager.ios_camera_gateway.preview;calls=[0]
    def evidence(*args):
        calls[0]+=1;value=original(*args)
        if calls[0]>1:value['preview']['observed_at']=time()-10
        return value
    manager.ios_camera_gateway.preview=evidence
    result=manager.start_session(monitoring_consent=True)
    assert not result['success'] and 'iOS live video required' in result['error']
    assert manager.session_id is None and manager.study.s is None
    manager._install_keyboard_hook.assert_not_called()


def test_live_session_monitoring_stops_accruing_after_ios_feed_goes_stale(rig,monkeypatch):
    from time import time
    window(rig);manager,_=manager_fixture(rig,monkeypatch)
    assert manager.start_session(monitoring_consent=True)['success']
    manager._test_ipad_heartbeat();manager._tick_study()
    elapsed=manager.study.s['elapsed_seconds']
    original=manager.ios_camera_gateway.preview
    def stale(*args):
        value=original(*args);value['status']['last_frame_at']=time()-10
        return value
    manager.ios_camera_gateway.preview=stale
    rig[1].advance(20)
    manager._test_ipad_heartbeat();manager._tick_study()
    assert manager.study.s['elapsed_seconds']==elapsed
    assert not manager.study.s['monitoring_ok']


@pytest.mark.parametrize('platform',['Android 16',' android '])
def test_paired_android_is_selected_automatically_without_changing_pairing_data(rig,monkeypatch,platform):
    window(rig);manager,_=manager_fixture(rig,monkeypatch);db,_,workflow,_=rig
    with db._connect() as c:c.execute("UPDATE devices SET platform=? WHERE device_id='a55'",(platform,))
    assert manager.start_session(monitoring_consent=True)['success']
    assert workflow.s['android_device_id']=='a55'
    with db._connect() as c:
        assert c.execute("SELECT platform FROM devices WHERE device_id='a55'").fetchone()[0]==platform
        assert c.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]==0


def test_receipt_from_paired_versioned_android_is_verified(rig):
    window(rig);db,_,workflow,_=rig
    with db._connect() as c:c.execute("UPDATE devices SET platform='Android 16' WHERE device_id='a55'")
    workflow.begin('session','user_lok','a55');advance(rig,1200);accept(rig)
    assert workflow.tick(refresh=True)['sync_verified']


def test_second_headphone_connection_proposes_exactly_two_points_once_without_direct_ledger_write(rig,monkeypatch):
    window(rig); manager,hardware=manager_fixture(rig,monkeypatch)
    manager.start_session(monitoring_consent=True)
    for count in (1,1,1,2,2):
        hardware.update(headphone_violations=count,last_prohibited_device='Synthetic headset')
        manager._tick_study()
    assert manager.study.s['state']=='FAILED' and manager.session_id is None
    manager._ensure_headphone_penalty();manager._ensure_headphone_penalty()
    with rig[0]._connect() as c:
        rows=c.execute('SELECT * FROM deduction_proposals WHERE proposal_id LIKE ?',('strict_hp_%',)).fetchall()
        assert len(rows)==1 and rows[0]['proposed_points']==-2 and rows[0]['status']=='AWAITING_CONFIRMATION'
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_manual_end_activates_only_existing_eligible_reward_and_never_credits_points(rig,monkeypatch):
    from core.entertainment_reward_engine import EntertainmentRewardEngine
    from types import SimpleNamespace
    window(rig); manager,_=manager_fixture(rig,monkeypatch);db,clock,w,_=rig
    manager.start_session(monitoring_consent=True)
    for _ in range(120):
        manager._test_ipad_heartbeat();advance(rig,10)
    manager._test_ipad_heartbeat()
    p=accept(rig);w.tick(refresh=True)
    engine=EntertainmentRewardEngine(db)
    grant=engine.grant_early_proof_reward('user_lok','a55',p['evidence_id'],p['accepted_at'],w.s['window']['window_end'],7,'APPROVED')
    rid=grant['reward']['entitlement_id']
    assert not engine.activate_reward(rid,'windows_lok_pc','user_lok')['success']
    monkeypatch.setattr('windows.strict_session_manager.threading.Thread',lambda **_:SimpleNamespace(start=lambda:None))
    assert manager.complete_study(w.s['session_id'])['success']
    manager._try_study_reward()
    assert w.s['state']=='REWARD_ACTIVE'
    reward=db.get_entertainment_entitlement(rid)
    assert (datetime.fromisoformat(reward['expiry_time'])-datetime.fromisoformat(reward['session_start_time'])).total_seconds()==1200
    old=reward['expiry_time']
    assert not manager.complete_study('wrong-session')['success']
    assert db.get_entertainment_entitlement(rid)['expiry_time']==old
    with db._connect() as c: assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


@pytest.mark.parametrize('failed_control',['notification','hook','network'])
def test_resume_control_failure_restores_windows_and_keeps_proof_pending(rig,monkeypatch,failed_control):
    from windows.strict_session_manager import StrictSessionState
    window(rig);manager,_=manager_fixture(rig,monkeypatch);_,clock,workflow,_=rig
    workflow.begin('strict_sess_resume','user_lok','a55')
    workflow.s.update(state='AWAITING_PROOF_WINDOW',focus_completed_at=clock.now.isoformat(),monitoring_resumed=False)
    monkeypatch.setattr(manager,'run_precheck',lambda:dict(all_passed=True))
    if failed_control=='notification':manager.notification_filter.start=lambda:dict(active=False)
    elif failed_control=='hook':manager._install_keyboard_hook.return_value=False
    else:manager.network_policy_manager.apply_policy.return_value=False
    assert not manager.resume_study_monitoring(consent=True)['success']
    assert manager.state==StrictSessionState.NORMAL and manager.session_id is None
    assert not workflow.s['monitoring_resumed'] and workflow.s['state']=='AWAITING_PROOF_WINDOW'
    manager.notification_filter.restore.assert_called_once()
    manager.study_hardware.stop.assert_called()


def test_existing_hourly_acceptance_pipeline_satisfies_receipt_gate_and_missing_image_does_not(rig,monkeypatch):
    from core.hourly_submission_service import HourlySubmissionService,HourlySubmissionError
    db,clock,w,path=rig
    # The scoring constructor reconciles current windows. It must use the same
    # virtual clock as this fixture, regardless of the real time of the test run.
    monkeypatch.setattr('core.time_window_engine.now_ist',clock.wall)
    window(rig);w.begin('session','user_lok','a55');advance(rig,1200)
    service=HourlySubmissionService(db,ScoringEngine(db=db,storage_root=str(path/'pipeline')))
    payload=dict(activity_id='hourly',submission_id='actual-service-receipt',schedule_version=1,
                 category='STUDY',description='Engineering fixture: studied database transactions; not a real attendance claim.')
    with pytest.raises(HourlySubmissionError):
        service.accept(payload,file_bytes=None,filename='',account_id='user_lok',device_id='a55',platform='ANDROID',received_at=clock.now)
    assert not w.view()['end_enabled']
    raw=io.BytesIO();Image.new('RGB',(160,120),(20,90,75)).save(raw,format='PNG')
    for platform,device in [('WINDOWS','pc'),('ANDROID','different-phone')]:
        with pytest.raises(HourlySubmissionError) as error:
            service.accept(payload,file_bytes=raw.getvalue(),filename='engineering-fixture.png',account_id='user_lok',device_id=device,platform=platform,received_at=clock.now)
        assert error.value.code=='STRICT_SESSION_ANDROID_REQUIRED'
    accepted=service.accept(payload,file_bytes=raw.getvalue(),filename='engineering-fixture.png',account_id='user_lok',device_id='a55',platform='ANDROID',received_at=clock.now)
    assert accepted['accepted']
    assert accepted['strict_session_id']=='session'
    w.tick(refresh=True)
    assert w.view()['end_enabled'] and w.s['submission_id']=='actual-service-receipt'
    retry=service.accept(payload,file_bytes=raw.getvalue(),filename='engineering-fixture.png',account_id='user_lok',device_id='a55',platform='ANDROID',received_at=clock.now)
    assert retry['deduplicated']
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM strict_study_receipts').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_preparation_does_not_credit_focus_and_status_uses_same_monotonic_timer(rig,monkeypatch):
    window(rig);db,clock,w,_=rig
    w.begin('preparing','user_lok','a55',activate=False)
    advance(rig,20)
    assert w.s['state']=='PREPARING' and w.s['elapsed_seconds']==0
    w.activate()
    assert w.s['focus_target_seconds']==880
    advance(rig,60)
    assert w.s['elapsed_seconds']==60
    # Main-window and HUD reads share this instance; a read never creates a timer.
    first=w.view();second=w.view()
    assert first['focus_remaining_seconds']==second['focus_remaining_seconds']==820


def test_failed_real_windows_control_restores_environment_and_never_starts_timer(rig,monkeypatch):
    window(rig);manager,_=manager_fixture(rig,monkeypatch)
    monkeypatch.setattr(manager,'_install_keyboard_hook',lambda:False)
    result=manager.start_session(monitoring_consent=True)
    assert not result['success']
    assert manager.study.s['state']=='FAILED' and manager.study.s['elapsed_seconds']==0
    manager.network_policy_manager.restore_network.assert_called_once()
    manager._start_supervisor.assert_not_called()


def test_restart_during_preparation_abandons_without_proof_or_elapsed_credit(rig):
    window(rig);db,clock,w,_=rig
    w.begin('preparing','user_lok','a55',activate=False)
    recovered=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic)
    recovered.recover('user_lok')
    assert recovered.s['state']=='ABANDONED' and recovered.s['elapsed_seconds']==0


def test_other_controller_cannot_start_a_second_pending_timer(rig):
    window(rig);db,clock,w,_=rig;w.begin('first','user_lok','a55')
    other=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic)
    with pytest.raises(ValueError,match='pending Strict Session'):other.begin('second','user_lok','a55')


def test_removed_android_companion_cannot_pause_existing_study_credit(rig):
    from windows.strict_session_preparation import StrictSessionPreparation
    from core.strict_mobile_integrity import MobileSessionIntegrity, SCHEMA
    db,clock,workflow,_=rig
    preparation=StrictSessionPreparation(db,lambda _:dict(ready=True),clock=lambda:clock.now.timestamp())
    pid=preparation.current('user_lok')['preparation_id'];preparation.activated(pid)
    window(rig,minutes=40);workflow.begin('mobile-integrity-session','user_lok','a55');advance(rig,100)
    with db._connect() as connection:
        connection.execute(SCHEMA)
        connection.execute('INSERT INTO strict_mobile_integrity VALUES(?,?,?,?,?,?,?,?,?,?)',
            (pid,'ANDROID','mobile-integrity-session',1,'APP_SWITCH_CONFIRMED',1,'ANDROID_NATIVE',clock.now.timestamp(),clock.now.timestamp()+43200,''))
    elapsed=workflow.s['elapsed_seconds'];advance(rig,100)
    assert workflow.s['elapsed_seconds']==elapsed+100
    with db._connect() as connection:assert connection.execute('SELECT count(*) FROM score_events').fetchone()[0]==0
