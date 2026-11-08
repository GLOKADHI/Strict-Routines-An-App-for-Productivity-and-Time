from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from test_strict_study_workflow import rig,manager_fixture
from windows.strict_preflight import hardware_checks
from windows.strict_demo_workflow import DemoStudyWorkflow


def healthy():
    return dict(camera='ACTIVE',attention_monitoring='ACTIVE',face_tracking='VERIFIED',face_framing='VISIBLE',
        lighting='CLEAR',proctor_calibrated=True,audio_detection_known=True,bluetooth_headphone_connected=False,
        wired_headphone_connected=False,speaker_muted=True,monitoring_ok=True,wired_state_known=False,notification_filter_active=True)


def test_connected_earbuds_fail_before_ready_and_never_report_bluetooth_pass():
    hw=healthy();hw.update(bluetooth_headphone_connected=True,prohibited_audio_name='WF-1000XM5',monitoring_ok=False)
    checks=hardware_checks(hw)
    assert not checks['all_passed'] and checks['prohibited_bluetooth']['passed'] is False
    assert 'WF-1000XM5' in ' '.join(checks['blockers']) and 'leave Bluetooth itself enabled' in checks['prohibited_bluetooth']['message']
    assert checks['earbud_wearing']['status']=='NOT_VERIFIED'


@pytest.mark.parametrize('change',[dict(camera='STOPPED'),dict(face_tracking='UNVERIFIED'),dict(lighting='LOW'),
    dict(proctor_calibrated=False),dict(audio_detection_known=False),dict(speaker_muted=False)])
def test_real_hardware_problems_cannot_be_green_prerequisites(change):
    hw=healthy();hw.update(change);assert not hardware_checks(hw)['all_passed']


def test_unreliable_jack_is_disclosed_not_mistaken_for_connected_headphones():
    checks=hardware_checks(healthy())
    assert checks['all_passed'] and checks['physical_jack']['status']=='NOT_VERIFIED'
    assert checks['earbud_wearing']['passed'] is None


def test_demo_has_no_schedule_dependency_and_cannot_write_database():
    db=SimpleNamespace(_connect=Mock(side_effect=AssertionError('Demo attempted a database write')))
    now=[0.];workflow=DemoStudyWorkflow(db,monotonic=lambda:now[0])
    workflow._ipad_status=lambda:dict(connected=True,reason='')
    workflow.begin('strict_sess_demo_fixture','user');workflow.activate()
    for _ in range(60):now[0]+=10;workflow.tick()
    view=workflow.view()
    assert view['state']=='SESSION_FINALIZED' and view['elapsed_seconds']==600 and view['reward_status']=='NOT_APPLICABLE'
    db._connect.assert_not_called()


def test_demo_pauses_on_missing_ipad_and_can_always_end_early_without_points():
    now=[0.];connected=[True];workflow=DemoStudyWorkflow(None,monotonic=lambda:now[0])
    workflow._ipad_status=lambda:dict(connected=connected[0],reason='Reconnect iPad' if not connected[0] else '')
    workflow.begin('strict_sess_demo_fixture','user');workflow.activate()
    now[0]+=10;workflow.tick();connected[0]=False
    now[0]+=10;assert workflow.tick()['elapsed_seconds']==10
    assert workflow.complete('strict_sess_demo_fixture')['state']=='SESSION_FINALIZED'


def test_demo_manager_start_does_not_need_window_and_creates_no_session_or_score_records(rig,monkeypatch):
    manager,_=manager_fixture(rig,monkeypatch);db,_,_,_=rig
    manager._session_mode='DEMO'
    with db._connect() as c:
        before={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('score_events','strict_study_sessions','strict_session_audit','strict_sessions')}
    result=manager.start_session(monitoring_consent=True)
    assert result['success'],result
    assert manager.study.view()['mode']=='DEMO' and manager.study.view()['focus_target_seconds']==600
    assert manager.complete_study(manager.session_id)['success']
    with db._connect() as c:
        after={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in before}
    assert before==after


def test_blocked_setup_cannot_create_abandoned_study_or_start_kiosk(rig,monkeypatch):
    manager,hardware=manager_fixture(rig,monkeypatch)
    manager._setup_monitor.update(active=True,camera_id='a'*64)
    hardware.update(bluetooth_headphone_connected=True,prohibited_audio_name='WF-1000XM5',monitoring_ok=False)
    result=manager.start_session(monitoring_consent=True)
    assert not result['success'] and result['next_view']=='PRECHECK' and 'WF-1000XM5' in result['error']
    manager._install_keyboard_hook.assert_not_called()
    with rig[0]._connect() as c:assert c.execute('SELECT COUNT(*) FROM strict_study_sessions').fetchone()[0]==0


def test_demo_headphone_failure_proposes_no_deduction(rig,monkeypatch):
    manager,hardware=manager_fixture(rig,monkeypatch);manager._session_mode='DEMO'
    assert manager.start_session(monitoring_consent=True)['success']
    hardware.update(headphone_violations=2,last_prohibited_device='AirPods')
    manager._tick_study()
    assert manager.session_id is None and manager.study.s['state']=='FAILED'
    with rig[0]._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM deduction_proposals').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]==0


def test_demo_privilege_and_proof_endpoints_cannot_recalculate_or_award_scores(rig,monkeypatch):
    manager,_=manager_fixture(rig,monkeypatch);manager._session_mode='DEMO'
    assert manager.start_session(monitoring_consent=True)['success']
    recalc=Mock(side_effect=AssertionError('Demo recalculated real scores'))
    monkeypatch.setattr(manager,'_refresh_authoritative_score',recalc)
    assert not manager.evaluate_privilege()['allowed']
    assert not manager.submit_hourly_proof('not-a-real-proof')['success']
    recalc.assert_not_called()
