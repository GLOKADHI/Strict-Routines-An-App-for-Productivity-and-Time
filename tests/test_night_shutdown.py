import copy
from datetime import datetime,timezone
import json
import sqlite3
from unittest.mock import Mock,patch
import pytest
from windows.night_shutdown import NightStore,NightCoordinator,hms,video_id
from windows.night_platform import request_shutdown

@pytest.fixture
def store(tmp_path):return NightStore(tmp_path/'night.sqlite')

def record(run='test',profile='profile-123',status='CAPTURED'):
    return dict(session_id=f'{run}:{profile}:document:aqz-KE-bpKQ',profile_id=profile,tab_id=7,document_id='document',
        capture_date='2026-11-01',capture_timestamp='2026-11-01T23:21:00.123+05:30',timezone='Asia/Kolkata',
        video_id='aqz-KE-bpKQ',video_url='https://www.youtube.com/watch?v=aqz-KE-bpKQ&list=example',
        video_title='Test fixture only',channel='Fixture',playback_position_seconds=1122.789,total_duration_seconds=2536.2,
        playback_state='PAUSED',playback_speed=1,capture_status=status,browser_name='chrome')

def run(store,controlled=False):
    if controlled:return store.controlled_run('profile-123',[{'scoped':True}],1762000000000,1762000001000,1762000002000)
    with store.connect() as c:c.execute("INSERT INTO night_runs(run_id,date,monitor_ms,capture_ms,shutdown_ms,state,shutdown_status) VALUES('test','2026-11-01',1000,2000,3000,'SCHEDULED','PENDING')")
    return store.run('test')

def persist(store,r):
    item=record(r['run_id']);store.save(item);store.commit_profile(r['run_id'],'profile-123',[item['session_id']]);return item

def test_actual_time_url_validation_and_immutable_idempotence(store):
    result=store.save(record())['record'];assert result['playback_position']=='00:18:42';assert result['playback_position_seconds']==1122.789
    assert result['total_duration']=='00:42:16';assert result['canonical_url']=='https://www.youtube.com/watch?v=aqz-KE-bpKQ'
    store.save(record());assert len(store.history())==1
    altered=record();altered['playback_position_seconds']=99
    with pytest.raises(ValueError):store.save(altered)
    assert video_id('https://youtube.com.evil/watch?v=aqz-KE-bpKQ') is None
    assert video_id('https://youtu.be/aqz-KE-bpKQ')=='aqz-KE-bpKQ'
    assert hms(360012.9)=='100:00:12'

@pytest.mark.parametrize('field,value',[('playback_position_seconds',float('nan')),('total_duration_seconds',-1),('video_id','fake'),('capture_timestamp','2026-11-01T23:21:00')])
def test_rejects_fabricated_or_invalid_player_data(store,field,value):
    item=record();item[field]=value
    with pytest.raises(ValueError):store.save(item)

def test_missing_native_backup_never_closes_or_shuts_down(store):
    run(store);close=Mock();shutdown=Mock();n=NightCoordinator(store,close,shutdown)
    n.tick(2001);n.tick(3000);close.assert_not_called();shutdown.assert_not_called()
    assert store.run('test')['shutdown_status']=='SHUTDOWN_BLOCKED'

def test_all_profiles_commit_before_closure_and_request_not_confirmation(store):
    r=run(store);item=persist(store,r)
    with store.connect() as c:c.execute("INSERT INTO night_profiles VALUES('test','other-profile',0)")
    close=Mock(return_value=[dict(exited=True)]);shutdown=Mock(return_value=dict(requested=True));n=NightCoordinator(store,close,shutdown,boot_time=lambda:100)
    n.tick(2000);close.assert_not_called()
    second=record('test','other-profile');store.save(second);store.commit_profile('test','other-profile',[second['session_id']])
    n.tick(2100);assert close.called;n.tick(3000);shutdown.assert_called_once()
    assert store.history()[0]['shutdown_status']=='SHUTDOWN_REQUESTED'
    n.tick(3100);n.tick(3200);shutdown.assert_called_once()
    store.confirmed_startup(200,lambda *args:False)
    assert store.run('test')['shutdown_status']=='UNCONFIRMED_AFTER_RESTART';assert not n.lock_active()

def test_controlled_live_run_never_calls_os_shutdown_and_survives_restart(store):
    r=run(store,True);persist(store,r);n=NightCoordinator(store,lambda _:[dict(exited=True)],Mock())
    reopened=NightStore(store.path);assert reopened.run(r['run_id'])['shutdown_ms']==r['shutdown_ms']
    n.tick(r['shutdown_ms']);n.shutdown.assert_not_called();assert 'SUPPRESSED' in reopened.history()[0]['shutdown_status']

def test_confirmation_requires_new_boot_and_os_evidence(store):
    r=run(store);persist(store,r);store.update_run('test',state='SHUTDOWN_REQUESTED',shutdown_status='SHUTDOWN_REQUESTED',requested_boot=100)
    store.confirmed_startup(100,lambda *_:True);assert store.run('test')['shutdown_status']=='SHUTDOWN_REQUESTED'
    store.confirmed_startup(200,lambda *_:True);assert store.run('test')['shutdown_status']=='SHUTDOWN_CONFIRMED_ON_NEXT_STARTUP'

def test_real_shutdown_command_has_no_implicit_force():
    with patch('windows.night_platform.subprocess.run',return_value=Mock(returncode=0)) as call:
        assert request_shutdown({'controlled':0})['requested'];args=call.call_args.args[0];assert args[-3:]==['/s','/t','0'];assert '/f' not in args
    with patch('windows.night_platform.subprocess.run') as call:request_shutdown({'controlled':1});call.assert_not_called()

def test_test_mode_never_mutates_real_score_release_or_history(tmp_path):
    from core.database import StrictRecordsDB
    from windows.restriction_manager import YouTubeRestrictionManager
    db=StrictRecordsDB(str(tmp_path/'actual.sqlite'));manager=YouTubeRestrictionManager(db)
    with db._connect() as c:before=list(c.iterdump())
    state=(manager.state,manager.manual_admin_release,manager.lock_screen_visible,copy.deepcopy(manager.events_history))
    assert manager.simulate_trigger()['mode']=='TEST_MODE';assert manager.simulate_restore(999)['allowed']
    assert state==(manager.state,manager.manual_admin_release,manager.lock_screen_visible,manager.events_history)
    with db._connect() as c:assert before==list(c.iterdump())
    assert not manager.simulated_test_mode

def test_capture_store_has_no_points_or_cloud_tables(store):
    store.save(record());store.checkpoint(record());store.configure(dict(enabled=True))
    with store.connect() as c:assert all(row[0].startswith('night_') for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'"))


def test_browser_exit_fallback_preserves_last_observed_position(store):
    from windows.night_platform import finalize_when_browsers_closed
    r=run(store);store.context('profile-123',now_ms=1500);store.checkpoint(record())
    with patch('psutil.process_iter',return_value=[]):finalize_when_browsers_closed(store,r)
    item=store.history('profile-123')[0]
    assert item['capture_status']=='LAST_OBSERVED'
    assert item['playback_position_seconds']==1122.789
    assert item['observed_timestamp']=='2026-11-01T23:21:00.123+05:30'
    assert store.ready(r)

def test_live_browser_forbids_native_fallback(store):
    from windows.night_platform import finalize_when_browsers_closed
    r=run(store)
    with patch('psutil.process_iter',return_value=[Mock(info={'name':'chrome.exe'})]):finalize_when_browsers_closed(store,r)
    assert not store.history()

def test_no_browser_record_has_no_invented_video_or_time(store):
    from windows.night_platform import finalize_when_browsers_closed
    r=run(store)
    with patch('psutil.process_iter',return_value=[]):finalize_when_browsers_closed(store,r)
    item=store.history('some-profile')[0]
    assert item['capture_status']=='NO_ACTIVE_YOUTUBE_VIDEO'
    assert 'video_id' not in item and 'playback_position_seconds' not in item
    assert store.ready(r)


def test_fast_startup_new_logon_releases_old_lock_without_false_confirmation(store):
    r=run(store);persist(store,r)
    store.update_run('test',state='SHUTDOWN_REQUESTED',shutdown_status='SHUTDOWN_REQUESTED',requested_boot=100,requested_session='old-logon')
    store.confirmed_startup(100,lambda *_:False,'old-logon')
    assert store.run('test')['state']=='SHUTDOWN_REQUESTED'
    store.confirmed_startup(100,lambda *_:False,'new-logon')
    assert store.run('test')['shutdown_status']=='UNCONFIRMED_AFTER_RESTART'
    assert not NightCoordinator(store,Mock(),Mock()).lock_active()

def test_fast_startup_confirmation_still_requires_clean_os_events(store):
    r=run(store);persist(store,r)
    store.update_run('test',state='SHUTDOWN_REQUESTED',shutdown_status='SHUTDOWN_REQUESTED',requested_boot=100,requested_session='old-logon')
    store.confirmed_startup(100,lambda *_:True,'new-logon')
    assert store.run('test')['shutdown_status']=='SHUTDOWN_CONFIRMED_ON_NEXT_STARTUP'


def test_process_snapshot_survives_nested_psutil_info_refresh():
    from windows.restriction_manager import YouTubeRestrictionManager,RestrictionState
    from types import SimpleNamespace
    m=object.__new__(YouTubeRestrictionManager)
    m._ensure_input_desktop=lambda:None;m.get_app_locks=lambda:{};m._last_full_enum_time=0;m.state=RestrictionState.NORMAL
    process=SimpleNamespace(pid=7777,info={'pid':7777,'ppid':1,'name':'vlc.exe','create_time':42.5})
    def decision(*args):
        process.info={'pid':7777,'name':'vlc.exe'}
        return dict(action='CLOSE_APP',process_name='vlc.exe')
    m._configured_native_decision=decision
    with patch('win32gui.GetForegroundWindow',return_value=0),patch('win32gui.EnumWindows'),patch('psutil.process_iter',return_value=[process]):
        matches=m.find_restricted_windows()
    assert len(matches)==1 and matches[0]['started']==42.5 and matches[0]['pid']==7777


@pytest.mark.parametrize('name',['chrome.exe','msedge.exe','brave.exe','opera.exe','ccleanerbrowser.exe','comet.exe','vivaldi.exe','arc.exe','firefox.exe'])
def test_browser_apps_are_not_points_blocked_before_night(name):
    from windows.restriction_manager import YouTubeRestrictionManager
    m=object.__new__(YouTubeRestrictionManager)
    assert m._configured_native_decision(name,0,{}) is None

def test_night_closes_browsers_even_without_daytime_points_classification():
    from windows.night_platform import close_targets
    from types import SimpleNamespace
    process=SimpleNamespace(pid=9876,info={'name':'chrome.exe','create_time':12.3})
    identity={'root':{'name':'chrome.exe','pid':9876}}
    settings=SimpleNamespace(restricted_windows_apps=['vlc.exe'],local_music_windows_apps=[])
    with patch('psutil.process_iter',return_value=[process]),patch('windows.strictbot_targets.capture_application',return_value=identity),patch('windows.strictbot_targets.close_application',return_value=True):
        result=close_targets({'controlled':0},settings)
    assert result[0]['name']=='chrome.exe' and result[0]['exited'] is True
