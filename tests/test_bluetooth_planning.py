"""Registered automatic Bluetooth uses the same planning CAS and atomic ledger commit."""
import hashlib
import json
import uuid
import pytest
from core.gym_daily_decision import DailyGymDecision
from core.daily_college_decision import DailyCollegeDecision
from core.time_window_engine import now_ist,TimeWindowEngine
from core.bluetooth_auto_sync import automatic_key,automatic_receipt_text
from core import bluetooth_protocol as wire
from tests.test_bluetooth_auto_sync import automatic,enable,exchange,TOKEN_HASH,DEVICE
from tests.test_bluetooth_transfer_v2 import package

def choice(kind='GYM_DECISION',revision=0,answer=False):
    day=TimeWindowEngine.get_next_calendar_date(now_ist().strftime('%Y-%m-%d'))
    body=dict(account_id='user_lok',date=day,expected_revision=revision,request_id=str(uuid.uuid4()),timezone='Asia/Kolkata')
    body['gym_planned' if kind=='GYM_DECISION' else 'attends_college']=answer
    return dict(kind=kind,payload=body)

def test_automatic_planning_and_ledger_commit_together_with_signed_snapshot(automatic):
    db,m,_=automatic;enable(m)
    requests=[choice(),choice('COLLEGE_DECISION',answer=True)]
    value=package();value['planning_requests']=requests
    receipt,_=exchange(m,value)
    assert receipt['success'] and receipt['transferred_count']==3 and receipt['confirmed_score']==4
    planning=json.loads(receipt['planning_payload'])
    assert all(r['accepted'] for r in planning['results'])
    day=requests[0]['payload']['date']
    assert DailyGymDecision(db).get('user_lok',day)['gym_planned'] is False
    assert len(DailyCollegeDecision(db).get('user_lok',day)['bath_plans'])==2
    assert receipt['planning_sha256']==hashlib.sha256(receipt['planning_payload'].encode()).hexdigest()
    tag=receipt['automatic_receipt_mac'];receipt['planning_sha256']='0'*64
    assert tag!=wire.mac(automatic_key(TOKEN_HASH,DEVICE,'user_lok'),automatic_receipt_text(receipt))

def test_conflicting_choice_is_retained_pending_and_cannot_replace_newer_no(automatic):
    db,m,_=automatic;enable(m)
    entry=choice(answer=True);body=entry['payload']
    DailyGymDecision(db).save('user_lok',body['date'],False,0,str(uuid.uuid4()),actor='fixture',device_id=DEVICE)
    value=package();value['planning_requests']=[entry]
    receipt,_=exchange(m,value)
    assert receipt['success'] and receipt['confirmed_score']==4 and receipt['pending_metadata_count']==1
    outcome=json.loads(receipt['planning_payload'])['results'][0]
    assert not outcome['accepted'] and outcome['code']=='REVISION_CONFLICT'
    assert DailyGymDecision(db).get('user_lok',body['date'])['gym_planned'] is False
    with db._connect() as c:
        assert json.loads(c.execute('SELECT request_json FROM bluetooth_planning_receipts').fetchone()[0])==entry

@pytest.mark.parametrize('bad', ['account','schema'])
def test_malformed_planning_rolls_back_ledger_and_prior_choices(automatic,bad):
    db,m,_=automatic;enable(m)
    first=choice();second=choice('COLLEGE_DECISION')
    if bad=='account':second['payload']['account_id']='other'
    else:second['kind']='APPROVE_PROOF'
    value=package();value['planning_requests']=[first,second]
    receipt,_=exchange(m,value)
    assert not receipt['success']
    assert DailyGymDecision(db).get('user_lok',first['payload']['date'])['revision']==0
    with db._connect() as c:
        assert c.execute("SELECT count(*) FROM score_events WHERE event_id='sleep-4'").fetchone()[0]==0

def test_http_then_bluetooth_repeat_is_idempotent(automatic):
    db,m,_=automatic;enable(m)
    entry=choice();body=entry['payload']
    DailyGymDecision(db).save('user_lok',body['date'],False,0,body['request_id'],actor='fixture',device_id=DEVICE)
    value=package();value['planning_requests']=[entry]
    receipt,_=exchange(m,value)
    assert receipt['success'] and receipt['already_present_count']==1 and receipt['transferred_count']==1
    assert DailyGymDecision(db).get('user_lok',body['date'])['revision']==1
