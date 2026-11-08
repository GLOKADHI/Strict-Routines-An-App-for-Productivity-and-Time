import json
from unittest.mock import patch
from core.database import StrictRecordsDB
from core.youtube_reward_cloud import YouTubeRewardCloud
from windows.restriction_manager import YouTubeRestrictionManager
from tests.test_planning_api_parity import api

def active():return dict(reward_id='yt-fixture',account_id='user_lok',state='ACTIVE',activation_ms=1000,expiry_ms=1201000,remaining_seconds=1199,remaining_ms=1199000,active_client_id='pc')

def test_windows_uses_reward_only_for_youtube_and_retains_independent_rules(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'pc.db'));manager=YouTubeRestrictionManager(db)
    manager.get_points_status=lambda **_:dict(current_score=0,required_score=80,score_available=True)
    db.is_wakeup_completed_today=lambda:True
    db.get_active_strict_session=lambda:None
    manager.get_service_policy=lambda _:dict(manuallyRestricted=False)
    class Projection:
        def youtube_active(self):return active()
    with patch('core.youtube_reward_cloud.client',return_value=Projection()):
        result=manager.get_browser_tab_restriction('youtube',actual_url='https://www.youtube.com/')
        assert result['allowed'] and result['currentScore']==0
        assert result['temporaryException']=='SINGLE_YOUTUBE_REWARD'
        assert manager.get_browser_tab_restriction('instagram')['restricted']
        assert manager.get_browser_tab_restriction('spotify')['restricted']
        assert not manager.get_browser_tab_restriction('youtube',actual_url='https://bank.example/')['restricted']
        manager.get_service_policy=lambda _:dict(manuallyRestricted=True)
        assert manager.get_browser_tab_restriction('youtube')['restricted']
        manager.get_service_policy=lambda _:dict(manuallyRestricted=False)
        db.get_active_strict_session=lambda:{'state':'ACTIVE'}
        assert manager.get_browser_tab_restriction('youtube')['restricted']
    class Expired:
        def youtube_active(self):return None
    db.get_active_strict_session=lambda:None
    with patch('core.youtube_reward_cloud.client',return_value=Expired()):
        assert manager.get_browser_tab_restriction('youtube')['restricted']

def test_projection_uses_monotonic_deadline_and_rechecks_identity(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'pc.db'))
    config=dict(endpoint='https://reward.example',account_id='user_lok',device_id='pc',device_token='fixture-token')
    class Response:
        status_code=200
        content=b'fixture'
        def json(self):return dict(success=True,account_id='user_lok',server_now_ms=1000,active_session=active(),available_rewards=[])
        def close(self):pass
    class Transport:
        def get(self,*a,**k):return Response()
    with patch('core.youtube_reward_cloud.time.monotonic',return_value=100):
        client=YouTubeRewardCloud(db,configuration=lambda:config,transport=Transport());client.request()
        assert client.view()['active_session']['remaining_seconds']==1200
    with patch('core.youtube_reward_cloud.time.monotonic',return_value=131):assert client.view()['active_session'] is None
    config['account_id']='other'
    assert client.view()['active_session'] is None

def test_pc_and_extension_routes_share_one_authoritative_reward(api,monkeypatch):
    db,call=api
    class Cloud:
        def binding(self):return dict(account_id='user_lok',device_id='pc'),'scope'
        def request(self,action,rid):
            return dict(success=True,account_id='user_lok',server_now_ms=1000,active_session=active(),available_rewards=[])
    monkeypatch.setattr('core.youtube_reward_cloud.client',lambda _:Cloud())
    for client in ['pc','android','extension']:
        code,result=call('/api/rewards/youtube/status',client=client)
        assert code==200
        assert result['active_session']['reward']['expiry_ms']==1201000
    assert call('/api/rewards/youtube/status?account_id=other')[0]==403
    assert call('/api/rewards/youtube/status',origin='https://unrelated.example')[0]==403

def test_unconfigured_authority_never_falls_back_to_an_independent_local_reward(api,monkeypatch):
    db,call=api
    class Unconfigured:
        def binding(self):raise ValueError('Shared reward service not configured')
    monkeypatch.setattr('core.youtube_reward_cloud.client',lambda _:Unconfigured())
    code,result=call('/api/rewards/youtube/start',{'reward_id':'legacy-local'})
    assert code==503 and not result['success']
    with db._connect() as conn:assert conn.execute('SELECT count(*) FROM youtube_video_rewards').fetchone()[0]==0

def test_pc_review_exports_the_original_submission_deadline_and_acceptance(tmp_path):
    from types import SimpleNamespace
    from core.hourly_submission_service import HourlySubmissionService
    db=StrictRecordsDB(str(tmp_path/'reviews.db'))
    original=dict(event_id='event-submitted',submission_id='original-sub',activity_id='hourly-slot',account_id='user_lok',
        task_type='HOURLY',evidence_id='proof-1',accepted_at='2026-10-30T09:49:00+05:30',window_end='2026-10-30T10:00:00+05:30')
    validation=dict(score=7,category_match=True,description_match=True,evidence_match=True,duplicate_result={'duplicate_detected':False})
    db.ensure_proof_review_record('proof-1','user_lok','HOURLY','2026-10-30',signals={'taufa_ai':validation},status='APPROVED')
    with db._connect() as conn:
        conn.execute("INSERT INTO proof_submission_events(event_id,account_id,submission_id,activity_id,schedule_version,source_device_id,accepted_at,status,payload_json) VALUES(?,?,?,?,1,'pc',?,'SUBMITTED',?)",
            ('event-submitted','user_lok','original-sub','hourly-slot',original['accepted_at'],json.dumps(original)))
    service=HourlySubmissionService(db,SimpleNamespace(storage_root=str(tmp_path)))
    event=service.publish_review_event('user_lok','hourly-slot','proof-1','APPROVE','APPROVED',7,'Admin')
    with db._connect() as conn:
        payload=json.loads(conn.execute('SELECT payload_json FROM proof_submission_events WHERE event_id=?',(event,)).fetchone()[0])
    reward=payload['youtube_reward_submission']
    assert reward['accepted_at']==original['accepted_at']!=payload['accepted_at']
    assert reward['submission_deadline']==original['window_end']
    assert reward['submission_id']=='original-sub' and reward['validation_status']=='VALID'
    assert reward['approved'] and reward['validated_score']==7

def test_shared_reward_does_not_override_wakeup_or_missed_hourly_policy(tmp_path):
    from core.privilege_engine import PrivilegeEngine
    db=StrictRecordsDB(str(tmp_path/'policies.db'));engine=PrivilegeEngine(db)
    base=dict(app_key='youtube',current_score=0,temporary_exception='SINGLE_YOUTUBE_REWARD')
    assert not engine.evaluate_entertainment_privilege(**base,wakeup_completed=False)['allowed']
    assert not engine.evaluate_entertainment_privilege(**base,hourly_missed_count_today=1)['allowed']
    assert not engine.evaluate_entertainment_privilege(**base,manually_restricted=True)['allowed']
    assert engine.evaluate_entertainment_privilege(**base)['allowed']
    other=dict(base,app_key='instagram')
    assert not engine.evaluate_entertainment_privilege(**other)['allowed']

def test_reward_active_on_android_is_visible_but_cannot_unlock_windows(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'owner.db'))
    config=dict(endpoint='https://reward.example',account_id='user_lok',device_id='pc',device_token='fixture-token')
    class Response:
        status_code=200;content=b'fixture'
        def json(self):return dict(success=True,account_id='user_lok',server_now_ms=1000,active_session=dict(active(),active_client_id='phone'),available_rewards=[])
        def close(self):pass
    class Transport:
        def get(self,*a,**k):return Response()
    with patch('core.youtube_reward_cloud.time.monotonic',return_value=100):
        client=YouTubeRewardCloud(db,configuration=lambda:config,transport=Transport())
        client.refresh_async=lambda:None
        client.request()
        assert client.view()['active_session']['active_client_id']=='phone'
        assert client.youtube_active() is None
