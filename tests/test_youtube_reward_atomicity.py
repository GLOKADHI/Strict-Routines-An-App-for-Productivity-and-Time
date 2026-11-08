from concurrent.futures import ThreadPoolExecutor
from core.youtube_reward_engine import YouTubeRewardEngine
from core.database import StrictRecordsDB
from tests.test_planning_api_parity import api

def reward(db,identity='reward-fixture',account='user_lok'):
    return db.create_youtube_reward(identity,account,identity+'-hour',identity+'-submission',8,True)

def test_simultaneous_rewards_cannot_create_parallel_sessions(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'rewards.sqlite'));e=YouTubeRewardEngine(db)
    for identity in ('one','two'):
        reward(db,identity);assert e.select_video(identity,'dQw4w9WgXcQ',300)['success']
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda item:YouTubeRewardEngine(db).start_session(*item),[('one','WINDOWS'),('two','ANDROID')]))
    assert sum(r['success'] for r in results)==1
    with db._connect() as c:assert c.execute("SELECT count(*) FROM youtube_video_rewards WHERE state='ACTIVE'").fetchone()[0]==1

def test_binding_deadline_and_owner_remain_immutable_across_retries(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'rewards.sqlite'));reward(db);e=YouTubeRewardEngine(db)
    first=e.select_video('reward-fixture','dQw4w9WgXcQ',120)
    assert first['allowed_seconds']==1200
    assert e.select_video('reward-fixture','dQw4w9WgXcQ',900)['allowed_seconds']==1200
    assert not e.select_video('reward-fixture','abcdefghijk',300)['success']
    active=e.start_session('reward-fixture','WINDOWS')
    assert e.start_session('reward-fixture','WINDOWS')['expiry_time']==active['expiry_time']
    assert not e.start_session('reward-fixture','ANDROID')['success']
    assert not e.transfer_session('reward-fixture','ANDROID','CHROME')['success']
    moved=e.transfer_session('reward-fixture','WINDOWS','ANDROID')
    assert moved['expiry_time']==active['expiry_time']
    assert not e.expire_session('reward-fixture','WINDOWS','USER_COMPLETED')['success']
    assert e.expire_session('reward-fixture','ANDROID','USER_COMPLETED')['success']

def test_unconfigured_reward_api_cannot_activate_a_second_local_entitlement(api,monkeypatch):
    db,call=api;reward(db)
    class Unconfigured:
        def binding(self):raise ValueError('Configure shared reward authority')
    monkeypatch.setattr('core.youtube_reward_cloud.client',lambda db:Unconfigured())
    assert call('/api/rewards/youtube/status?account_id=other')[0]==403
    assert call('/api/rewards/youtube/status',origin='https://foreign.example')[0]==403
    assert call('/api/rewards/youtube/start',{'reward_id':'reward-fixture'})[0]==503
    assert db.get_youtube_reward('reward-fixture')['state']=='AVAILABLE'
