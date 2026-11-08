"""Hourly windows follow the configured schedule, independently of wake-up proof."""
from datetime import datetime,timedelta
import io
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import pytest
from core.database import StrictRecordsDB
from core.daily_routine_gate import DailyRoutineGate
from core.hourly_submission_service import HourlySubmissionService,HourlySubmissionError
from core.time_window_engine import TimeWindowEngine,IST


@pytest.fixture
def setup(tmp_path,monkeypatch):
    date='2026-10-20';now=datetime(2026,10,20,12,0,tzinfo=IST)
    monkeypatch.setattr('core.time_window_engine.now_ist',lambda:now)
    db=StrictRecordsDB(str(tmp_path/'independent.sqlite'))
    db.ensure_daily_record(date)
    tw=TimeWindowEngine(db)
    rows=tw.ensure_activities_in_db(date,wakeup_time='09:00',reference_now=now)
    row=next(a for a in rows if a['type']=='HOURLY' and a['scheduled_time'][11:16]=='12:00')
    output=io.BytesIO();Image.new('RGB',(128,128),(30,80,160)).save(output,'JPEG')
    service=HourlySubmissionService(db,SimpleNamespace(storage_root=str(tmp_path/'evidence')))
    return db,tw,service,row,now,output.getvalue()


def submit(setup,**overrides):
    db,tw,service,row,now,photo=setup
    payload={'activity_id':row['activity_id'],'schedule_version':row['schedule_version'],
             'submission_id':'isolated-independent-proof','category':'STUDY','description':'Reviewed notes during this hourly occurrence.'}
    payload.update(overrides.pop('payload',{}))
    return service.accept(payload,file_bytes=overrides.pop('photo',photo),filename='proof.jpg',
                          account_id='user_lok',device_id='isolated-windows',platform='WINDOWS',
                          received_at=overrides.pop('received_at',now),**overrides)


@pytest.mark.parametrize('gate',[{'active':False},{'active':False,'recovery_required':True},
    {'active':True,'activated_at':'2026-10-20T13:00:00+05:30'}])
def test_wakeup_completion_or_review_does_not_control_hourly_eligibility(gate):
    row={'type':'HOURLY','status':'OPEN','window_start':'2026-10-20T11:50:00+05:30'}
    assert DailyRoutineGate.activity_is_eligible(row,gate,for_submission=True)
    for status in ('EXCUSED','CANCELLED','SKIPPED'):
        assert not DailyRoutineGate.activity_is_eligible(dict(row,status=status),gate,for_submission=True)


def test_actual_open_occurrence_is_available_without_wakeup(setup):
    db,tw,_,row,now,_=setup
    assert not DailyRoutineGate(db).reconcile(now.date().isoformat(),reference_now=now)['active']
    assert row['status']=='OPEN' and row['submission_eligible'] is True
    assert row['window_start'][11:16]=='11:50' and row['window_end'][11:16]=='12:10'
    assert db.get_score_events(now.date().isoformat())==[]


@pytest.mark.parametrize('wakeup,opening,closing',[('09:00','08:50','09:10'),('09:30','09:20','09:40')])
def test_first_hourly_window_remains_anchored_to_configured_wakeup(setup,wakeup,opening,closing):
    db,tw,_,_,_,_=setup
    rows=[a for a in tw.generate_daily_schedule('2026-10-21',wakeup_time=wakeup) if a['type']=='HOURLY']
    assert rows[0]['window_start'][11:16]==opening and rows[0]['window_end'][11:16]==closing
    assert (datetime.fromisoformat(rows[1]['window_start'])-datetime.fromisoformat(rows[0]['window_start'])).total_seconds()==3600


def test_real_acceptance_without_wakeup_is_pending_review_and_has_no_award(setup):
    db,tw,service,row,now,photo=setup
    # Hourly submission succeeds even when wake-up verification is not completed
    result=submit(setup)
    assert result['success'] and result['accepted']
    with db._connect() as conn:
        saved=conn.execute('SELECT status,submission_id FROM scheduled_activities WHERE activity_id=?',(row['activity_id'],)).fetchone()
        assert saved['status']=='VALIDATING' and saved['submission_id']=='isolated-independent-proof'
    assert db.get_score_events(now.date().isoformat())==[]
    assert submit(setup)['deduplicated'] is True


def test_bath1_gated_by_wakeup_only_on_college_days(setup):
    from core.daily_college_decision import DailyCollegeDecision
    db,tw,service,_,now,photo=setup
    date=now.date().isoformat()
    bath_act_id=f"ACT-{date.replace('-','')}-0730-BATH-1"
    with db._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scheduled_activities(activity_id,account_id,date,type,status,scheduled_time,window_start,window_end,notification_time,schedule_version,details_json) "
            "VALUES(?,'user_lok',?,'BATH','OPEN',?,?,?,?,1,'{}')",
            (bath_act_id, date, f"{date}T07:30:00+05:30", f"{date}T07:00:00+05:30", f"{date}T13:00:00+05:30", f"{date}T07:00:00+05:30")
        )

    payload={'activity_id':bath_act_id,'schedule_version':1,'submission_id':'bath-1-proof-test','category':'OTHER','description':'Bath session 1 proof'}

    # 1. Before college decision is made -> DAILY_SETUP_REQUIRED
    with pytest.raises(HourlySubmissionError) as blocked:
        service.accept(payload, file_bytes=photo, filename='bath.jpg', expected_type='BATH', account_id='user_lok', device_id='isolated-windows', platform='WINDOWS', received_at=now)
    assert blocked.value.code == 'DAILY_SETUP_REQUIRED'

    # 2. On confirmed College Day -> WAKEUP_VERIFICATION_REQUIRED if wake-up is not done
    with db._connect() as conn:
        DailyCollegeDecision.ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO daily_college_decisions(account_id,date,attends_college,revision,bath_plans_json,timezone,updated_at,actor,device_id) VALUES(?,?,?,?,?,?,?,?,?)",
            ('user_lok', date, 1, 1, '[{"occurrence":1,"planned_time":"07:30"}]', 'Asia/Kolkata', now.isoformat(), 'test', 'test-device')
        )
    with pytest.raises(HourlySubmissionError) as blocked:
        service.accept(payload, file_bytes=photo, filename='bath.jpg', expected_type='BATH', account_id='user_lok', device_id='isolated-windows', platform='WINDOWS', received_at=now)
    assert blocked.value.code == 'WAKEUP_VERIFICATION_REQUIRED'
    assert 'Complete Wake-Up Verification first.' in str(blocked.value)

    # 3. On confirmed Non-College Day -> Allowed even without wake-up verification!
    with db._connect() as conn:
        conn.execute(
            "UPDATE daily_college_decisions SET attends_college=0 WHERE account_id=? AND date=?",
            ('user_lok', date)
        )
    res = service.accept(payload, file_bytes=photo, filename='bath.jpg', expected_type='BATH', account_id='user_lok', device_id='isolated-windows', platform='WINDOWS', received_at=now)
    assert res['success'] and res['accepted']


@pytest.mark.parametrize('seconds,code',[(-601,'WINDOW_NOT_OPEN'),(600,'WINDOW_CLOSED')])
def test_absolute_window_boundaries_still_enforced(setup,seconds,code):
    now=setup[4]
    with pytest.raises(HourlySubmissionError) as caught:submit(setup,received_at=now+timedelta(seconds=seconds))
    assert caught.value.code==code


def test_missing_description_and_photo_still_rejected(setup):
    with pytest.raises(HourlySubmissionError) as caught:submit(setup,payload={'description':''})
    assert caught.value.code=='DESCRIPTION_REQUIRED'
    with pytest.raises(HourlySubmissionError) as caught:submit(setup,photo=None)
    assert caught.value.code=='ATTACHMENT_REQUIRED'


@pytest.mark.parametrize('status',['EXCUSED','CANCELLED'])
def test_approved_holds_and_cancelled_occurrences_stay_unavailable(setup,status):
    db,tw,_,row,now,_=setup
    with db._connect() as c:c.execute('UPDATE scheduled_activities SET status=? WHERE activity_id=?',(status,row['activity_id']))
    with pytest.raises(HourlySubmissionError) as caught:submit(setup)
    assert caught.value.code=='CHECKPOINT_INACTIVE'


@pytest.mark.parametrize('legacy',['BLOCKED_WAKEUP','SKIPPED_PRE_GATE'])
def test_legacy_gate_recovers_only_live_or_future_windows_without_rewriting_history(setup,legacy):
    db,tw,_,row,now,_=setup
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET status=? WHERE type='HOURLY' AND date=?",(legacy,now.date().isoformat()))
    rows=tw.get_activities_for_date(now.date().isoformat(),reference_now=now)
    active=next(a for a in rows if a['activity_id']==row['activity_id'])
    assert active['status']=='OPEN' and active['submission_eligible']
    with db._connect() as c:
        old=c.execute("SELECT status FROM scheduled_activities WHERE type='HOURLY' AND scheduled_time LIKE '%T10:00:%'").fetchone()
        assert old['status']==legacy
    assert db.get_score_events(now.date().isoformat())==[]


def test_legacy_elapsed_window_cannot_generate_a_retroactive_missed_deduction(setup):
    from core.scoring_engine import ScoringEngine
    db,tw,service,row,now,_=setup
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET status='BLOCKED_WAKEUP' WHERE type='HOURLY' AND scheduled_time LIKE '%T10:00:%'")
    with patch('core.time_window_engine.now_ist',return_value=now):
        engine=ScoringEngine(db,storage_root=service.storage_root)
        before=db.get_score_events(now.date().isoformat())
        result=engine.mark_hourly_missed(now.date().isoformat(),'09:00-10:00',timestamp=now.isoformat())
    assert result['status']=='INACTIVE' and result['points']==0
    assert db.get_score_events(now.date().isoformat())==before
