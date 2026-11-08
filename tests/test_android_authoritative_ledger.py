"""Cross-client ledger, reset, filtering and authority regressions; all data is temporary."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from core.android_ledger import checkpoint, projection, reconcile, reporting_date
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine

DATE = '2026-10-17'
DEVICE = 'android_fixture'


def envelope(events=None, score=4, revision=1, date=DATE):
    if events is None:
        events = [
            dict(event_id='initial-reset', event_type='INITIAL_RESET', points=0, description='User-requested initial balance reset', audit={'action':'INITIAL_POINTS_RESET','reason':'User-requested reset'}),
            dict(event_id='daily-reset', event_type='DAILY_RESET', points=0, description='Daily reporting period started', audit={}),
            dict(event_id='sleep-four', event_type='MI_FITNESS_SLEEP_BONUS', points=4, description='Sleep onset 12:33 AM; confirmed sleep bonus', audit={'evidence_id':'sleep-proof'})]
    rows = [dict(date=date, is_dev_mode=False, timestamp=date+' 09:31:49', eligible=True, **ev) for ev in events]
    payload = dict(schema=1, account_id='user_lok', device_id=DEVICE, date=date, timezone='Asia/Kolkata',
                   revision=revision, event_count=len(rows), score_events=rows,
                   confirmed_score=score, raw_verified_points=sum(e['points'] for e in rows if e['eligible']))
    raw = json.dumps(payload, separators=(',',':'), ensure_ascii=False)
    return dict(payload_json=raw, sha256=hashlib.sha256(raw.encode()).hexdigest())


@pytest.fixture
def store(tmp_path):
    db = StrictRecordsDB(str(tmp_path/'ledger.db'))
    engine = ScoringEngine(db, str(tmp_path/'proofs'))
    return db, engine


def test_original_zero_four_fifteen_discrepancy(store):
    db, engine = store
    with db._connect() as conn:
        conn.execute('''INSERT INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,device_id,
            description,points,rule_version,audit_json) VALUES('old-pc-proof',?,0,?,'PROOF_APPROVED',?,'Old PC approval claim',15,1,'{}')''',
            (DATE,DATE+' 09:29:04',DEVICE))
    assert reconcile(db,envelope(),DEVICE)['accepted']
    assert engine.calculate_verified_score(DATE) == 4
    assert db.get_point_ledger_summary(DATE)['verified_points'] == 4
    history = engine.get_unified_transaction_history(date=DATE)
    assert history['confirmed_balance'] == 4
    assert history['balance_verified'] and history['sync_status'] == 'SYNCED'
    assert history['metrics']['points_gained'] == 4
    assert next(t for t in history['transactions'] if t['transaction_id']=='old-pc-proof')['status']=='Not confirmed by APK'
    assert next(t for t in history['transactions'] if t['transaction_id']=='sleep-four')['title']=='Mi Fitness sleep bonus'
    with db._connect() as conn:
        assert conn.execute("SELECT points FROM score_events WHERE event_id='old-pc-proof'").fetchone()[0] == 15


def test_retries_are_idempotent_and_keep_one_transaction_per_id(store):
    db,engine=store
    for _ in range(12):
        assert reconcile(db,envelope(),DEVICE)['accepted']
        assert engine.get_unified_transaction_history(DATE)['confirmed_balance']==4
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM score_events WHERE event_id='sleep-four'").fetchone()[0]==1


def test_confirmed_empty_zero_is_different_from_missing_checkpoint(store):
    db,engine=store
    before=engine.get_unified_transaction_history(DATE)
    assert not before['balance_verified'] and before['sync_status']!='SYNCED'
    assert reconcile(db,envelope([],0),DEVICE)['accepted']
    after=engine.get_unified_transaction_history(DATE)
    assert after['balance_verified'] and after['confirmed_balance']==0


def test_reset_compensation_keeps_original_sleep_history(store):
    db,engine=store
    original=json.loads(envelope()['payload_json'])['score_events']
    reset=dict(event_id='explicit-reset',event_type='INITIAL_RESET',points=-4,description='User-requested reset',audit={'action':'INITIAL_POINTS_RESET','reason':'User-requested reset'})
    events=[{k:v for k,v in e.items() if k not in {'date','timestamp','is_dev_mode','eligible'}} for e in original]+[reset]
    assert reconcile(db,envelope(events,0,2),DEVICE)['accepted']
    assert engine.calculate_verified_score(DATE)==0
    assert engine.get_unified_transaction_history(DATE)['total_count']==4
    assert not reconcile(db,envelope(),DEVICE)['accepted']
    assert engine.calculate_verified_score(DATE)==0


def test_daily_reset_preserves_prior_period(store):
    db,engine=store
    assert reconcile(db,envelope(),DEVICE)['accepted']
    assert reconcile(db,envelope([],0,date='2026-10-18'),DEVICE)['accepted']
    assert engine.calculate_verified_score('2026-10-18')==0
    assert engine.calculate_verified_score(DATE)==4
    assert engine.get_unified_transaction_history('all')['total_count']==3


@pytest.mark.parametrize('mutation',['checksum','count','balance','account','device','duplicate','scope'])
def test_invalid_or_incomplete_snapshots_never_claim_delivery(store,mutation):
    db,engine=store
    value=envelope();payload=json.loads(value['payload_json'])
    if mutation=='checksum':value['sha256']='0'*64
    else:
        if mutation=='count':payload['event_count']+=1
        if mutation=='balance':payload['confirmed_score']=15
        if mutation=='account':payload['account_id']='another-user'
        if mutation=='device':payload['device_id']='another-device'
        if mutation=='duplicate':payload['score_events'].append(payload['score_events'][-1]);payload['event_count']+=1
        if mutation=='scope':payload['score_events'][-1]['date']='2026-10-16'
        value['payload_json']=json.dumps(payload);value['sha256']=hashlib.sha256(value['payload_json'].encode()).hexdigest()
    assert not reconcile(db,value,DEVICE)['accepted']
    assert checkpoint(db,DATE) is None


def test_pending_deduction_does_not_change_confirmed_score(store):
    db,engine=store
    assert reconcile(db,envelope(),DEVICE)['accepted']
    result=engine.record_score_event(date=DATE,event_type='ENTERTAINMENT_VIOLATION',points=-7,device_id='windows',description='Proposed violation')
    assert result['points']==0
    assert engine.calculate_verified_score(DATE)==4
    assert any(t.get('is_negative_proposal') for t in engine.get_unified_transaction_history(DATE)['transactions'])


def test_only_human_authorized_negative_transactions_can_sync(store):
    db,engine=store
    event=dict(event_id='deduction',event_type='ENTERTAINMENT_VIOLATION',points=-3,description='Confirmed violation',audit={})
    assert not reconcile(db,envelope([event],-3),DEVICE)['accepted']
    event['audit']={'approval_source':'ANDROID_HUMAN_CONFIRMATION','approved_by':'human-admin'}
    assert reconcile(db,envelope([event],-3,2),DEVICE)['accepted']
    assert engine.calculate_verified_score(DATE)==-3


def test_offline_keeps_the_last_confirmed_amount_without_fresh_label(store):
    db,_=store
    reconcile(db,envelope(),DEVICE)
    future=datetime.now(timezone.utc)+timedelta(minutes=3)
    receipt=checkpoint(db,DATE,now=future)
    assert receipt['confirmed_balance']==4 and not receipt['fresh'] and receipt['sync_status']!='SYNCED'


@pytest.mark.parametrize('tz,stamp,wanted',[
    ('Asia/Kolkata','2026-10-16T19:00:00+00:00','2026-10-17'),
    ('America/Los_Angeles','2026-10-17T02:00:00+00:00','2026-10-16'),
    ('Asia/Kolkata','2026-10-16T18:29:59+00:00','2026-10-16'),
    ('Asia/Kolkata','2026-10-16T18:30:00+00:00','2026-10-17')])
def test_today_uses_reporting_timezone(tz,stamp,wanted):
    assert reporting_date('today',datetime.fromisoformat(stamp),tz)==wanted


def test_conflicting_retained_transaction_rolls_back_whole_manifest(store):
    db,_=store
    first=envelope();assert reconcile(db,first,DEVICE)['accepted']
    data=json.loads(first['payload_json']);data['revision']=2;data['score_events'][-1]['points']=15
    data['raw_verified_points']=15;data['confirmed_score']=15
    raw=json.dumps(data);changed={'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    assert not reconcile(db,changed,DEVICE)['accepted']
    assert projection(db,DATE)['verified_points']==4

def test_failed_transfer_is_visible_and_keeps_last_confirmed_balance(store):
    db,engine=store
    assert reconcile(db,envelope(),DEVICE)['accepted']
    bad=envelope();bad['sha256']='0'*64
    assert not reconcile(db,bad,DEVICE)['accepted']
    result=engine.get_unified_transaction_history(DATE)
    assert result['confirmed_balance']==4
    assert result['sync_status']=='RECONCILIATION REQUIRED'
    assert result['last_sync_error']
    assert reconcile(db,envelope(),DEVICE)['accepted']
    assert engine.get_unified_transaction_history(DATE)['sync_status']=='SYNCED'

def test_bluetooth_transfers_the_same_checkpoint_and_correct_receipt(store):
    from core.bluetooth_transfer import BluetoothTransferManager
    db,engine=store
    from tests.bluetooth_fakes import FakeBackend, PEER
    manager=BluetoothTransferManager(db,engine,backend_factory=FakeBackend)
    payload={'transfer_version':2,'account_id':'user_lok','device_id':DEVICE,'date':DATE,'ledger_snapshot':envelope(),'score_events':[]}
    raw=json.dumps(payload)
    try:
        first=manager.start_session(timeout_seconds=180,peer_address=PEER)
        receipt=manager.import_payload(first['session_code'],payload,hashlib.sha256(raw.encode()).hexdigest(),DEVICE,raw,peer_address=PEER)
        assert receipt['success'] and receipt['ledger_receipt']['accepted']
        assert receipt['confirmed_score']==receipt['final_score']==4
        assert receipt['transferred_count']==3
        second=manager.start_session(timeout_seconds=180,peer_address=PEER)
        repeat=manager.import_payload(second['session_code'],payload,hashlib.sha256(raw.encode()).hexdigest(),DEVICE,raw,peer_address=PEER)
        assert repeat['success'] and repeat['transferred_count']==0 and repeat['already_present_count']==3
        assert engine.calculate_verified_score(DATE)==4
    finally:
        manager.cancel_session()


def test_legacy_queue_cannot_finalize_an_unapproved_windows_loss(store):
    db,engine=store
    result=engine.process_sync_batch([{'sync_id':'legacy-unapproved','payload_type':'SCORE_EVENT','payload':{
        'event_id':'bad-negative','date':DATE,'event_type':'ENTERTAINMENT_VIOLATION','points':-7,
        'description':'Unconfirmed Windows loss','audit':{'approved_by':'system'}}}],device_id='windows_exe')
    assert 'legacy-unapproved' in result['processed_sync_ids']
    with db._connect() as conn:
        assert conn.execute("SELECT 1 FROM score_events WHERE event_id='bad-negative'").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM deduction_proposals").fetchone()[0]==1


def test_a_reset_correction_is_not_counted_as_a_new_positive_reward(store):
    db,engine=store
    events=[dict(event_id='loss',event_type='ENTERTAINMENT_VIOLATION',points=-4,description='Confirmed loss',audit={
        'approval_source':'ANDROID_HUMAN_CONFIRMATION','approved_by':'human'}),
        dict(event_id='reset-to-zero',event_type='INITIAL_RESET',points=4,description='Reset negative balance to zero',audit={
        'action':'INITIAL_POINTS_RESET','reason':'User-requested reset'})]
    assert reconcile(db,envelope(events,0),DEVICE)['accepted']
    history=engine.get_unified_transaction_history(DATE)
    assert history['confirmed_balance']==0 and history['metrics']['points_gained']==0
