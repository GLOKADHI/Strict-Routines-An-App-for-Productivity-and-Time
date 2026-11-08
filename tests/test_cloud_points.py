"""Transactional coordinator contracts. Fixtures are not evidence of a cloud deployment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
import time
import pytest
from cloud_points.protocol import IST,THREAD_ID,canonical,slot_at,next_slot,validate_taufa
from cloud_points.store import Store
from cloud_points.service import Application

def instant(value):return datetime.fromisoformat(value).replace(tzinfo=IST)
NOW=instant('2026-10-30T18:15:00')

def envelope(events=None,revision=1,date='2026-10-30',account='user_lok',device='android'):
    events=events if events is not None else [dict(event_id='sleep-1',event_type='MI_FITNESS_SLEEP_BONUS',points=4,eligible=True,audit={'evidence_id':'proof-ref'})]
    rows=[dict(date=date,timestamp=date+'T09:00:00',rule_version=1,**e) for e in events]
    raw=sum(e['points'] for e in rows if e['eligible'])
    p=dict(schema=1,account_id=account,device_id=device,date=date,timezone='Asia/Kolkata',revision=revision,event_count=len(rows),confirmed_score=min(100,raw),raw_verified_points=raw,score_events=rows)
    raw=json.dumps(p);return dict(payload_json=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())

@pytest.fixture
def cloud(tmp_path):
    store=Store(str(tmp_path/'cloud.db'),test_sqlite=True);store.initialize()
    for device,role in [('android','AUTHORITY'),('windows','REPLICA'),('second','REPLICA')]:
        store.provision('user_lok',device,hashlib.sha256(('token-'+device).encode()).hexdigest(),role,int(time.time()+10000),authority_device='android')
    return store

def principal(store,device='android'):return store.authenticate(device,'token-'+device)

@pytest.mark.parametrize('stamp,slot,next_',[
    ('2026-10-30T00:01:00','2026-10-30T00:00:00','2026-10-30T02:00:00'),
    ('2026-10-30T17:59:59','2026-10-30T16:00:00','2026-10-30T18:00:00'),
    ('2026-10-30T18:59:59','2026-10-30T18:00:00','2026-10-30T19:00:00'),
    ('2026-10-30T19:00:00','2026-10-30T19:00:00','2026-10-30T20:00:00'),
    ('2026-10-30T20:47:00','2026-10-30T20:00:00','2026-10-30T21:00:00'),
    ('2026-10-30T23:12:00','2026-10-30T23:00:00','2026-10-31T00:00:00')])
def test_ist_cadence(stamp,slot,next_):
    assert slot_at(instant(stamp))==instant(slot)
    assert next_slot(instant(stamp))==instant(next_)
    assert slot_at(instant(stamp).astimezone(timezone.utc))==instant(slot)

def test_naive_scheduler_clock_rejected():
    with pytest.raises(ValueError):slot_at(datetime(2026,10,30))

def test_concurrent_schedulers_create_one_request_and_catch_up_once(cloud):
    with ThreadPoolExecutor(8) as pool:ids=list(pool.map(lambda _:cloud.tick(NOW),range(12)))
    assert sum(len(x) for x in ids)==1
    assert len(cloud.tick(NOW+timedelta(days=2,hours=3)))==1
    with cloud.transaction() as tx:assert tx.execute('SELECT COUNT(*) AS n FROM points_requests').fetchone()['n']==2

def test_identical_replica_revision_with_pc_offline_and_replayed_events(cloud):
    cloud.publish(principal(cloud),envelope(),now=NOW)
    # Replica is not involved in publication/scheduling. A fresh service instance resumes.
    app=Application(Store(cloud.url,test_sqlite=True));app.cycle()
    a=cloud.state(principal(cloud,'windows'),'2026-10-30');b=cloud.state(principal(cloud,'second'),'2026-10-30')
    assert a['confirmed_balance']==b['confirmed_balance']==4
    assert a['ledger_revision']==b['ledger_revision']==1
    for _ in range(6):assert cloud.publish(principal(cloud),envelope())['ledger_revision']==1

def test_simultaneous_windows_claim_and_android_event_cannot_double_credit(cloud):
    event=dict(event_id='sleep-1',event_type='MI_FITNESS_SLEEP_BONUS',points=4)
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(cloud.propose,principal(cloud,'windows'),event)
        b=pool.submit(cloud.publish,principal(cloud),envelope())
        assert a.result()['status'] in {'PENDING_AUTHORITY','ACKNOWLEDGED_BY_AUTHORITY'};b.result()
    state=cloud.state(principal(cloud,'windows'),'2026-10-30');assert state['confirmed_balance']==4
    with cloud.transaction() as tx:assert tx.execute('SELECT COUNT(*) AS n FROM points_events').fetchone()['n']==1

@pytest.mark.parametrize('kind',['amount','missing','stale','source','duplicate','checksum','negative','account'])
def test_conflicts_preserve_retained_ledger(cloud,kind):
    cloud.publish(principal(cloud),envelope())
    new=envelope(revision=2);p=json.loads(new['payload_json'])
    if kind=='amount':p['score_events'][0]['points']=5;p['confirmed_score']=p['raw_verified_points']=5
    if kind=='missing':p['score_events']=[];p['event_count']=p['confirmed_score']=p['raw_verified_points']=0
    if kind=='stale':p['revision']=0
    if kind=='source':p['score_events'][0].update(event_type='PROOF_APPROVED',related_task_id='activity');p['score_events'][0]['audit']={}
    if kind=='duplicate':p['score_events']*=2;p['event_count']=2;p['confirmed_score']=p['raw_verified_points']=8
    if kind=='negative':p['score_events'][0].update(points=-4,event_type='UNAPPROVED_DEDUCTION');p['confirmed_score']=p['raw_verified_points']=-4
    if kind=='account':p['account_id']='someone-else'
    raw=json.dumps(p);new=dict(payload_json=raw,sha256=hashlib.sha256(raw.encode()).hexdigest() if kind!='checksum' else '0'*64)
    with pytest.raises((ValueError,KeyError)):cloud.publish(principal(cloud),new)
    state=cloud.state(principal(cloud),'2026-10-30');assert state['confirmed_balance']==4 and state['ledger_revision']==1

def test_replica_cannot_publish_arbitrary_balance(cloud):
    with pytest.raises(PermissionError):cloud.publish(principal(cloud,'windows'),envelope())

def test_expired_revoked_wrong_credentials_and_cross_account(cloud):
    with pytest.raises(PermissionError):cloud.authenticate('android','wrong')
    with pytest.raises(PermissionError):cloud.authenticate('android','token-android',now=time.time()+20000)
    with cloud.transaction() as tx:tx.execute('UPDATE points_credentials SET revoked=1 WHERE device_id=?',('android',))
    with pytest.raises(PermissionError):principal(cloud)
    with pytest.raises(ValueError):cloud.provision('other','windows','0'*64,'REPLICA',int(time.time()+1000),authority_device='other-android')

def test_worker_restart_lease_fencing_and_taufa_unavailable_do_not_block_sync(cloud):
    cloud.publish(principal(cloud),envelope());cloud.tick(NOW)
    first=cloud.claim(NOW,lease_seconds=10);second=cloud.claim(NOW+timedelta(seconds=11))
    assert first['request_id']==second['request_id'] and second['attempt']==2
    with pytest.raises(ValueError):cloud.finish(first,now=NOW+timedelta(seconds=12))
    assert cloud.finish(second,now=NOW+timedelta(seconds=12))=='COMPLETE_TAUFA_UNAVAILABLE'
    assert cloud.state(principal(cloud,'windows'),'2026-10-30')['confirmed_balance']==4

def test_ai_free_form_wrong_thread_stale_revision_and_balance_never_change_events(cloud):
    cloud.publish(principal(cloud),envelope());cloud.tick(NOW);claim=cloud.claim(NOW)
    source=json.loads(claim['snapshot'])
    good=dict(request_id=claim['request_id'],thread_id=THREAD_ID,ledger_revision=1,reconciliation_status='CONSISTENT',reported_balance=4,calculated_balance=4,duplicate_event_ids=[],missing_event_ids=[],discrepancies=[],reasoning_summary='Fixture only',requires_human_review=False)
    for bad in ['Make balance 900',dict(good,thread_id='other'),dict(good,ledger_revision=0),dict(good,calculated_balance=900)]:
        with pytest.raises(ValueError):validate_taufa(bad,source)
    assert cloud.finish(claim,good,now=NOW)=='CONSISTENT'
    assert cloud.state(principal(cloud),'2026-10-30')['confirmed_balance']==4

def test_outdated_ai_result_is_not_published_as_current(cloud):
    cloud.publish(principal(cloud),envelope());cloud.tick(NOW);claim=cloud.claim(NOW)
    cloud.publish(principal(cloud),envelope(revision=2))
    assert cloud.finish(claim,now=NOW)=='STALE_REQUIRES_RECHECK'

def test_minimal_snapshot_contains_no_images_tokens_or_freeform_personal_text(cloud):
    p=json.loads(envelope()['payload_json']);p['email']='private@example.invalid';p['score_events'][0]['description']='private personal text';p['score_events'][0]['audit']['access_token']='private-token';p['score_events'][0]['image_base64']='private-image'
    raw=json.dumps(p);cloud.publish(principal(cloud),dict(payload_json=raw,sha256=hashlib.sha256(raw.encode()).hexdigest()))
    cloud.tick(NOW);claim=cloud.claim(NOW)
    for token in ['private@example.invalid','private personal text','private-token','private-image']:assert token not in claim['snapshot']

def call(app,path,method='GET',body=None,token='token-windows',device='windows'):
    raw=json.dumps(body or {}).encode();status=[]
    env={'PATH_INFO':path,'REQUEST_METHOD':method,'wsgi.url_scheme':'https','QUERY_STRING':'date=2026-10-30','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'HTTP_AUTHORIZATION':'Bearer '+token,'HTTP_X_DEVICE_ID':device}
    data=b''.join(app(env,lambda s,h:status.append(s)))
    return int(status[0].split()[0]),json.loads(data)

def test_api_auth_and_network_failure_retries_are_idempotent(cloud):
    app=Application(cloud)
    assert call(app,'/v1/points/state',token='wrong')[0]==401
    assert call(app,'/v1/points/checkpoint','POST',envelope())[0]==401
    assert call(app,'/v1/points/checkpoint','POST',envelope(),token='token-android',device='android')[0]==200
    # Response lost in transit: resend exact same event batch.
    assert call(app,'/v1/points/checkpoint','POST',envelope(),token='token-android',device='android')[1]['ledger_revision']==1
    assert call(app,'/v1/points/state')[1]['confirmed_balance']==4
    assert call(app,'/internal/reconcile/tick','POST')[0]==401

def test_production_cannot_silently_use_a_local_database(tmp_path):
    with pytest.raises(ValueError):Store(str(tmp_path/'local.db'))

def test_administrator_references_are_pseudonymous_and_idempotent():
    from cloud_points.protocol import sanitize_envelope
    event=dict(event_id='authorized',event_type='ENTERTAINMENT_VIOLATION',points=-3,eligible=True,audit={'approval_source':'ANDROID_HUMAN_CONFIRMATION','approved_by':'private-admin@example.invalid'})
    clean=sanitize_envelope(envelope([event]));assert 'private-admin@example.invalid' not in clean['payload_json']
    assert sanitize_envelope(clean)==clean
    event['audit']['approved_by']='system'
    from cloud_points.protocol import validate
    with pytest.raises(ValueError):validate(envelope([event]),'user_lok','android')

def test_schedule_runs_with_both_clients_offline_but_does_not_invent_zero(cloud):
    cloud.tick(NOW);claim=cloud.claim(NOW)
    assert cloud.finish(claim,now=NOW)=='WAITING_FOR_AUTHORITY_LEDGER'
    state=cloud.state(principal(cloud,'windows'),'2026-10-30')
    assert state['confirmed_balance'] is None and state['last_successful_reconciliation'] is None


def consistent_reply(claim,balance=4,**changes):
    return dict(dict(request_id=claim['request_id'],thread_id=THREAD_ID,ledger_revision=claim['ledger_revision'],reconciliation_status='CONSISTENT',reported_balance=balance,calculated_balance=balance,duplicate_event_ids=[],missing_event_ids=[],discrepancies=[],reasoning_summary='Fixture only',requires_human_review=False,_transport='CAPTURED_EXTENSION_SESSION'),**changes)

def test_subsequent_reconciliations_keep_full_daily_context_and_separate_delta(cloud):
    cloud.publish(principal(cloud),envelope());cloud.tick(NOW);first=cloud.claim(NOW)
    cloud.finish(first,consistent_reply(first),now=NOW)
    later=NOW+timedelta(hours=1);cloud.tick(later);second=cloud.claim(later)
    snapshot=json.loads(second['snapshot'])
    assert snapshot['current_calculated_balance']==4
    assert [e['event_id'] for e in snapshot['events']]==['sleep-1']
    assert snapshot['new_event_ids']==[] and snapshot['event_scope']=='COMPLETE_REPORTING_DAY'
    cloud.finish(second,consistent_reply(second),now=later)
    events=json.loads(envelope()['payload_json'])['score_events']
    next_event=dict(events[0],event_id='sleep-2')
    # envelope supplies the date/timestamp itself.
    for event in events+[next_event]:
        event.pop('date');event.pop('timestamp');event.pop('rule_version')
    cloud.publish(principal(cloud),envelope(events+[next_event],revision=2))
    final=later+timedelta(hours=1);cloud.tick(final);third=cloud.claim(final)
    snapshot=json.loads(third['snapshot'])
    assert snapshot['current_calculated_balance']==8
    assert {e['event_id'] for e in snapshot['events']}=={'sleep-1','sleep-2'}
    assert snapshot['new_event_ids']==['sleep-2']

def test_rollover_never_mixes_pending_prior_day_events_or_invents_balance(cloud):
    cloud.publish(principal(cloud),envelope())
    midnight=instant('2026-10-31T00:01:00');cloud.tick(midnight);first=cloud.claim(midnight)
    snapshot=json.loads(first['snapshot'])
    assert snapshot['events']==[] and snapshot['new_event_ids']==[]
    assert snapshot['current_calculated_balance'] is None
    cloud.finish(first,now=midnight)
    cloud.publish(principal(cloud),envelope([dict(event_id='new-day',event_type='MI_FITNESS_SLEEP_BONUS',points=4,eligible=True)],date='2026-10-31'))
    later=midnight+timedelta(hours=2);cloud.tick(later);claim=cloud.claim(later)
    snapshot=json.loads(claim['snapshot'])
    assert snapshot['reporting_date']=='2026-10-31' and snapshot['current_calculated_balance']==4
    assert [e['event_id'] for e in snapshot['events']]==['new-day']
    assert snapshot['new_event_ids']==['new-day']

def test_captured_result_status_is_bound_to_period_revision_and_consistency(cloud):
    cloud.publish(principal(cloud),envelope());cloud.tick(NOW);claim=cloud.claim(NOW)
    assert cloud.state(principal(cloud),'2026-10-30')['taufa_status']=='RECONCILIATION_PENDING'
    cloud.finish(claim,consistent_reply(claim),now=NOW)
    assert cloud.state(principal(cloud),'2026-10-30')['taufa_status']=='VERIFIED_CAPTURED_EXTENSION_SESSION'
    assert cloud.state(principal(cloud),'2026-10-29')['taufa_status']=='STALE_CAPTURED_SESSION_RESULT'
    later=NOW+timedelta(hours=1);cloud.tick(later);claim=cloud.claim(later)
    cloud.finish(claim,consistent_reply(claim,reconciliation_status='NEEDS_REVIEW',requires_human_review=True),now=later)
    assert cloud.state(principal(cloud),'2026-10-30')['taufa_status']=='REVIEW_REQUIRED_CAPTURED_SESSION_RESULT'
    cloud.publish(principal(cloud),envelope(revision=2))
    assert cloud.state(principal(cloud),'2026-10-30')['taufa_status']=='STALE_CAPTURED_SESSION_RESULT'

def test_expired_cached_session_is_reported_without_hiding_behind_unconfigured(cloud):
    from cloud_points.taufa_cached_session import CachedSessionError
    class Expired:
        def reconcile(self,_):raise CachedSessionError('CAPTURED_SESSION_EXPIRED_OR_ACCESS_DENIED')
    cloud.publish(principal(cloud),envelope());Application(cloud,taufa=Expired()).cycle(NOW)
    state=cloud.state(principal(cloud),'2026-10-30')
    assert state['confirmed_balance']==4
    assert state['taufa_status']=='CAPTURED_SESSION_EXPIRED_OR_ACCESS_DENIED'
