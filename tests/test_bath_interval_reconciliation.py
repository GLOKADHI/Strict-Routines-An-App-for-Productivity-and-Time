"""Actual interval scheduling, SQLite consistency, and no ledger side effects."""
from datetime import datetime,timedelta
import json
import uuid

import pytest

from core.database import StrictRecordsDB
from core.daily_college_decision import ATTENDANCE_POLICY,DailyCollegeDecision
from core.gym_daily_decision import GymDecisionError
from core.time_window_engine import IST,TimeWindowEngine


DAY='2026-11-04'
NOW=datetime(2026,11,4,13,41,tzinfo=IST)


def clock(hhmm):return datetime.fromisoformat(DAY+'T'+hhmm+':00').replace(tzinfo=IST)


@pytest.fixture
def env(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'bath_interval.sqlite'))
    with db._connect() as conn:
        for h in range(13,20):
            target=clock(f'{h:02d}:00')
            start,end=target-timedelta(minutes=10),target+timedelta(minutes=10)
            conn.execute('''INSERT INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,
                window_start,window_end,notification_time,status,points,penalty_points,details_json)
                VALUES(?,'user_lok','HOURLY','Hourly',?,?,?,?,?,'SCHEDULED',8,0,'{}')''',
                (f'ACT-20261104-{h:02d}00-HOURLY',DAY,target.isoformat(),start.isoformat(),end.isoformat(),
                 (start-timedelta(minutes=1)).isoformat()))
    return db,DailyCollegeDecision(db)


def plan(service,start='14:22',duration=60,revision=0,extra=None,current=NOW,request=None):
    plans=[dict(planned_time=start,activity_duration_minutes=duration,window_start='12:00',window_end='22:00')]
    if extra:plans.append(extra)
    return service.save('user_lok',DAY,False,revision,request or str(uuid.uuid4()),bath_plans=plans,
                        actor='test',device_id='phone',current=current)


def rows(db):
    with db._connect() as conn:
        return {r['activity_id']:dict(r) for r in conn.execute('SELECT * FROM scheduled_activities')}


def hourly(db,hour):return rows(db)[f'ACT-20261104-{hour:02d}00-HOURLY']


def ledger(db):
    with db._connect() as conn:
        return [tuple(r) for r in conn.execute('SELECT * FROM score_events ORDER BY event_id')]


def test_exact_screenshot_interval_and_preserved_proof_window(env):
    db,service=env
    before=ledger(db)
    plan(service)
    assert hourly(db,14)['status']=='SCHEDULED'
    assert hourly(db,14)['points']==8
    excuse=hourly(db,15)
    assert excuse['status']==excuse['attendance_status']=='EXCUSED' and excuse['points']==0
    d=json.loads(excuse['details_json'])
    assert d['bath_overlap_kind']=='FULL' and d['attendance_reservation_policy']==ATTENDANCE_POLICY
    bath=rows(db)['ACT-20261104-1422-BATH-1']
    detail=json.loads(bath['details_json'])
    assert detail['held_hourly_slot']=='14:00-15:00'
    assert detail['held_hourly_act_id']=='ACT-20261104-1500-HOURLY'
    assert detail['reserved_end']==clock('15:22').isoformat()
    assert bath['window_start']==clock('15:52').isoformat()
    assert bath['window_end']==clock('16:12').isoformat()
    assert bath['status']=='SCHEDULED' and bath['submitted_at'] is None
    assert ledger(db)==before


def test_stale_previous_bath_assignment_restores_normal_live_window(env):
    db,service=env
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET status='EXCUSED',attendance_status='EXCUSED',points=0,details_json=? WHERE activity_id='ACT-20261104-1400-HOURLY'",
                     (json.dumps(dict(suppressed_by_bath=True,bath_slot_target_ms=1)),))
    plan(service)
    fixed=hourly(db,14)
    assert fixed['status']=='SCHEDULED' and fixed['attendance_status']=='UPCOMING'
    assert not json.loads(fixed['details_json']).get('suppressed_by_bath')
    assert hourly(db,15)['status']=='EXCUSED'


def test_reschedule_to_three_and_shorten_and_extend_recalculate(env):
    db,service=env
    before=ledger(db)
    plan(service)
    plan(service,'15:00',60,1)
    assert hourly(db,15)['status']=='EXCUSED'  # partial overlap 15:00–15:10
    assert json.loads(hourly(db,15)['details_json'])['bath_overlap_kind']=='PARTIAL'
    assert hourly(db,16)['status']=='EXCUSED'
    assert rows(db)['ACT-20261104-1422-BATH-1']['status']=='CANCELLED'
    plan(service,'15:00',20,2)
    assert hourly(db,16)['status']=='SCHEDULED' and hourly(db,16)['points']==8
    bath=rows(db)['ACT-20261104-1500-BATH-1']
    assert bath['window_start']==clock('15:50').isoformat()
    assert bath['window_end']==clock('16:10').isoformat()
    plan(service,'15:00',120,3)
    assert [h for h in range(14,19) if hourly(db,h)['status']=='EXCUSED']==[15,16,17]
    assert ledger(db)==before


@pytest.mark.parametrize('start,duration,excused',[
    ('14:10',40,[]),  # touches 13:50–14:10 and 14:50–15:10 only at endpoints
    ('14:05',10,[14]),
    ('14:40',20,[15]),
    ('14:22',15,[]),
])
def test_positive_partial_overlap_and_exact_boundaries(env,start,duration,excused):
    db,service=env
    plan(service,start,duration)
    assert [h for h in range(14,18) if hourly(db,h)['status']=='EXCUSED']==excused


def test_two_nonoverlapping_baths_aggregate_exemptions_and_overlap_rejected_atomically(env):
    db,service=env
    plan(service,extra=dict(planned_time='17:22',activity_duration_minutes=60,window_start='12:00',window_end='22:00'))
    assert hourly(db,15)['status']==hourly(db,18)['status']=='EXCUSED'
    before=rows(db);before_ledger=ledger(db)
    with pytest.raises(GymDecisionError):
        plan(service,'15:00',120,1,extra=dict(planned_time='16:00',activity_duration_minutes=60,window_start='12:00',window_end='22:00'))
    assert rows(db)==before and ledger(db)==before_ledger


def test_finalized_evidence_other_excuses_and_elapsed_records_untouched(env):
    db,service=env
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET status='VERIFIED',submitted_at=?,submission_id='real',points=8 WHERE activity_id='ACT-20261104-1500-HOURLY'",(clock('14:55').isoformat(),))
        conn.execute("UPDATE scheduled_activities SET status='EXCUSED',details_json=? WHERE activity_id='ACT-20261104-1600-HOURLY'",(json.dumps(dict(suppressed_by_travel=True)),))
        conn.execute("UPDATE scheduled_activities SET status='MISSED',points=-3 WHERE activity_id='ACT-20261104-1300-HOURLY'")
    before=rows(db);before_ledger=ledger(db)
    plan(service,duration=120)
    for h in [13,15,16]:assert hourly(db,h)==before[f'ACT-20261104-{h:02d}00-HOURLY']
    assert ledger(db)==before_ledger


def test_reconciliation_is_idempotent_and_preserves_notification_history(env):
    db,service=env
    with db._connect() as conn:
        conn.execute("INSERT INTO notification_center(notification_key,title,message,activity_id,timestamp) VALUES('old','Hourly','submit','ACT-20261104-1500-HOURLY',?)",(NOW.isoformat(),))
        conn.execute("INSERT INTO notification_history VALUES('old','ACT-20261104-1500-HOURLY','OPENED',?)",(NOW.isoformat(),))
    request=str(uuid.uuid4())
    first=plan(service,request=request)
    snapshot=rows(db)
    with db._connect() as conn:
        for _ in range(3):DailyCollegeDecision.reconcile_bath_reservations(conn,'user_lok',DAY,NOW)
        assert conn.execute("SELECT is_dismissed FROM notification_center WHERE notification_key='old'").fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM notification_history WHERE notification_key='old'").fetchone()[0]==1
    assert rows(db)==snapshot
    assert plan(service,request=request)==first


def test_transaction_failure_rolls_back_bath_and_hourly_changes(env):
    db,service=env
    plan(service)
    before=rows(db)
    with db._connect() as conn:
        conn.execute("CREATE TRIGGER fail_bath_hourly BEFORE UPDATE ON scheduled_activities WHEN NEW.type='HOURLY' BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(Exception,match='test rollback'):
        plan(service,'16:22',60,1)
    assert rows(db)==before
    assert service.get('user_lok',DAY)['revision']==1


def test_past_date_never_rewrites_history(env):
    db,service=env
    plan(service)
    before=rows(db)
    with db._connect() as conn:
        DailyCollegeDecision.reconcile_bath_reservations(conn,'user_lok',DAY,NOW+timedelta(days=1))
    assert rows(db)==before


@pytest.mark.parametrize('duration',[0,181,True,12.5,'60'])
def test_invalid_duration_rejected_without_partial_plan(env,duration):
    db,service=env
    with pytest.raises(GymDecisionError):plan(service,duration=duration)
    assert service.get('user_lok',DAY)['revision']==0


def test_windows_projection_includes_same_assignment_and_excuse(env,monkeypatch):
    from core.client_parity import ClientParityService
    db,service=env
    plan(service)
    monkeypatch.setattr('core.client_parity.now_ist',lambda:NOW)
    response=ClientParityService(db,TimeWindowEngine(db)).state('user_lok',DAY)
    projected={r['activity_id']:r for r in response['activities']}
    assert projected['ACT-20261104-1500-HOURLY']['status']=='EXCUSED'
    assert projected['ACT-20261104-1400-HOURLY']['status']=='SCHEDULED'
    assert json.loads(projected['ACT-20261104-1422-BATH-1']['details_json'])['held_hourly_slot']=='14:00-15:00'
