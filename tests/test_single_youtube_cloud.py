"""Reward authority tests against isolated fixtures, never a real entitlement."""
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest
from cloud_points.store import Store
from cloud_points.service import Application
from cloud_points.youtube_rewards import reconcile, activate, state, DURATION_MS
from tests.test_demo_access import points_bytes

NOW=1793446400000

@pytest.fixture
def store(tmp_path):
    s=Store(str(tmp_path/'rewards.db'),test_sqlite=True);s.initialize()
    for device,role in [('apk','AUTHORITY'),('exe','REPLICA'),('extension','REPLICA')]:
        s.provision('owner',device,hashlib.sha256(('token-'+device).encode()).hexdigest(),role,int(time.time())+3600,authority_device='apk')
    return s

def principal(s,device='apk'):return s.authenticate(device,'token-'+device)

def submission(minutes=10,**overrides):
    deadline=datetime.fromisoformat('2026-10-30T10:00:00+05:30')
    return dict(submission_id='sub-1',activity_id='ACT-20261030-0940-HOURLY',task_type='HOURLY',
        accepted_at=(deadline-timedelta(minutes=minutes)).isoformat(),submission_deadline=deadline.isoformat(),
        validation_status='VALID',approved=True,validator='TAUFA_AI',validated_score=7,
        category_match=True,description_match=True,evidence_match=True,duplicate_detected=False,**overrides)

def grant(s,item=None):
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        result=reconcile(s,principal(s),[item or submission()])
    return result

@pytest.mark.parametrize('minutes,count',[(10,1),(11,1),(9,0)])
def test_ten_minute_inclusive_actual_submission_deadline(store,minutes,count):
    assert len(grant(store,submission(minutes))['available_rewards'])==count

@pytest.mark.parametrize('status',['NEEDS_REVIEW','INSUFFICIENT_EVIDENCE','INVALID','PROCESSING_ERROR','PENDING'])
def test_non_valid_evidence_never_grants(store,status):
    item=submission();item['validation_status']=status
    assert not grant(store,item)['available_rewards']

@pytest.mark.parametrize('field,value',[('approved',False),('validator','LOCAL_GUESS'),('validated_score',5),('validated_score',9),('category_match',False),('description_match',False),('evidence_match',False),('duplicate_detected',True)])
def test_existing_validation_and_approval_policy_is_required(store,field,value):
    item=submission();item[field]=value
    assert not grant(store,item)['available_rewards']

def test_delayed_validation_retains_acceptance_and_no_duplicate_on_retry(store):
    item=submission();item['validation_status']='PROCESSING_ERROR'
    assert not grant(store,item)['available_rewards']
    first=grant(store);second=grant(store)
    assert first['available_rewards']==second['available_rewards']
    changed=submission();changed['submission_deadline']='2026-10-30T11:00:00+05:30'
    with pytest.raises(ValueError):grant(store,changed)
    # Another submission for the same slot also cannot mint another reward.
    repeated=submission();repeated['submission_id']='sub-repeat'
    assert len(grant(store,repeated)['available_rewards'])==1

def test_fixed_twenty_minutes_shared_by_apk_exe_extension_and_restart(store):
    rid=grant(store)['available_rewards'][0]['reward_id']
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        result=activate(store,principal(store),rid)
    active=result['active_session']
    assert active['expiry_ms']-active['activation_ms']==DURATION_MS==1200000
    reopened=Store(store.url,test_sqlite=True);reopened.initialize()
    for client in ['apk','exe','extension']:
        with patch('cloud_points.youtube_rewards._now',return_value=NOW+1000):
            r=state(reopened,principal(reopened,client))
            assert r['active_session']['expiry_ms']==active['expiry_ms']
            assert r['active_session']['remaining_ms']==1199000
            if client != 'apk':
                with pytest.raises(ValueError):activate(reopened,principal(reopened,client),rid)
            else:assert activate(reopened,principal(reopened,client),rid)['active_session']['expiry_ms']==active['expiry_ms']
    with patch('cloud_points.youtube_rewards._now',return_value=NOW+DURATION_MS):
        expired=state(reopened,principal(reopened,'extension'))
        assert expired['active_session'] is None
        assert expired['reward_history'][0]['state']=='EXPIRED'
        with pytest.raises(ValueError):activate(reopened,principal(reopened),rid)

def test_simultaneous_claims_are_one_consumption_not_two_timers(store):
    rid=grant(store)['available_rewards'][0]['reward_id']
    def claim(device):
        try:return activate(store,principal(store,device),rid)['active_session']
        except ValueError:return None
    with patch('cloud_points.youtube_rewards._now',return_value=NOW),ThreadPoolExecutor(8) as workers:
        result=list(workers.map(claim,['apk','exe','extension']*5))
    result=[r for r in result if r is not None]
    assert {r['activation_ms'] for r in result}=={NOW}
    assert {r['expiry_ms'] for r in result}=={NOW+DURATION_MS}
    assert len({r['active_client_id'] for r in result})==1

def test_two_distinct_rewards_cannot_start_parallel_periods(store):
    first=grant(store)['available_rewards'][0]['reward_id']
    item=submission();item.update(submission_id='sub-2',activity_id='ACT-20261030-1040-HOURLY')
    second=next(r['reward_id'] for r in grant(store,item)['available_rewards'] if r['reward_id']!=first)
    def claim(rid):
        try:return activate(store,principal(store,'exe'),rid)['active_session']['reward_id']
        except ValueError:return None
    with patch('cloud_points.youtube_rewards._now',return_value=NOW),ThreadPoolExecutor(2) as workers:
        result=list(workers.map(claim,[first,second]))
    assert sum(r is not None for r in result)==1
    with patch('cloud_points.youtube_rewards._now',return_value=NOW+DURATION_MS):
        waiting=state(store,principal(store))['available_rewards']
        assert len(waiting)==1
        assert activate(store,principal(store),waiting[0]['reward_id'])['active_session']['remaining_ms']==DURATION_MS

def test_reward_lifecycle_never_writes_points_summary_approval_or_cloud_points(store):
    before=points_bytes(store)
    rid=grant(store)['available_rewards'][0]['reward_id']
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        activate(store,principal(store),rid)
        activate(store,principal(store),rid,stop=True)
    assert points_bytes(store)==before

def test_replica_cannot_attest_and_account_cannot_claim_another_accounts_reward(store):
    with pytest.raises(PermissionError):reconcile(store,principal(store,'exe'),[submission()])
    rid=grant(store)['available_rewards'][0]['reward_id']
    store.provision('other','other',hashlib.sha256(b'other-token').hexdigest(),'AUTHORITY',int(time.time())+3600,authority_device='other')
    with pytest.raises(ValueError):activate(store,store.authenticate('other','other-token'),rid)

def test_authenticated_http_routes_ignore_forged_clock_account_and_score(store):
    app=Application(store)
    def call(path,body=None,device='apk',token=None):
        raw=json.dumps(body or {}).encode();statuses=[]
        out=app(dict(PATH_INFO='/v1/rewards/youtube/'+path,REQUEST_METHOD='GET' if path=='state' else 'POST',
            CONTENT_LENGTH=str(len(raw)),HTTP_X_DEVICE_ID=device,HTTP_AUTHORIZATION='Bearer '+(token or 'token-'+device),
            **{'wsgi.url_scheme':'https','wsgi.input':io.BytesIO(raw)}),lambda s,_:statuses.append(s))
        return int(statuses[0].split()[0]),json.loads(b''.join(out))
    assert call('state',token='expired')[0]==401
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        code,result=call('reconcile',{'submissions':[submission()]});assert code==200
        rid=result['available_rewards'][0]['reward_id']
        code,result=call('activate',{'reward_id':rid,'account_id':'other','score':81,'expiry_ms':NOW+999999999},device='extension')
        assert code==200 and result['account_id']=='owner'
        assert result['active_session']['expiry_ms']==NOW+DURATION_MS

def test_clock_rollback_invalidates_active_reward_instead_of_extending(store):
    rid=grant(store)['available_rewards'][0]['reward_id']
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):activate(store,principal(store),rid)
    with patch('cloud_points.youtube_rewards._now',return_value=NOW-1):assert state(store,principal(store))['active_session'] is None
