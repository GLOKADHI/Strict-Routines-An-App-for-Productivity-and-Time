from datetime import datetime
import uuid
import pytest
from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.gym_daily_decision import DailyGymDecision,GymDecisionError
from core.time_window_engine import IST,TimeWindowEngine

NOW=datetime(2026,10,20,10,0,tzinfo=IST)
DAY='2026-10-21'
@pytest.fixture
def college(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'college.sqlite'))
    return db,DailyCollegeDecision(db)
def save(service,answer,revision=0,plans=None):
    return service.save('user_lok',DAY,answer,revision,str(uuid.uuid4()),bath_plans=plans,actor='fixture',device_id='fixture',current=NOW)

def test_college_yes_has_two_independent_five_point_baths_and_no_gym_inference(college):
    db,c=college
    result=save(c,True)
    items=TimeWindowEngine(db).ensure_activities_in_db(DAY,reference_now=NOW)
    baths=[a for a in items if a['type']=='BATH' and a.get('is_enabled')]
    assert len(baths)==2 and [a['points'] for a in baths]==[5,5]
    assert '07:30' in baths[0]['scheduled_time']
    assert '08:00' in baths[0]['window_start'] and '08:30' in baths[0]['window_end']
    assert '17:30' in baths[1]['window_start'] and '17:50' in baths[1]['window_end']
    hourly=[a for a in items if a['type']=='HOURLY' and a.get('is_enabled')]
    assert hourly[0]['activity_id']=='ACT-20261021-0900-HOURLY'
    assert '09:00' in hourly[0]['window_start'] and '10:00' in hourly[0]['window_end']
    assert DailyGymDecision(db).get('user_lok',DAY)['gym_planned'] is None

def test_home_without_explicit_bath_time_creates_no_fictitious_reservation(college):
    db,c=college
    save(c,False)
    assert not [a for a in TimeWindowEngine(db).ensure_activities_in_db(DAY,reference_now=NOW) if a['type']=='BATH' and a.get('is_enabled')]

def test_home_explicit_bath_keeps_planning_range_separate_from_photo_window(college):
    db,c=college
    save(c,False,plans=[dict(planned_time='13:30',window_start='13:00',window_end='17:30')])
    baths=[a for a in TimeWindowEngine(db).ensure_activities_in_db(DAY,reference_now=NOW) if a['type']=='BATH' and a.get('is_enabled')]
    assert len(baths)==1 and baths[0]['activity_id']=='ACT-20261021-1330-BATH-1'
    assert '15:00' in baths[0]['window_start'] and '15:20' in baths[0]['window_end']

def test_stale_college_choice_is_rejected_and_does_not_replace_bath_plan(college):
    db,c=college
    save(c,True);save(c,False,revision=1)
    with pytest.raises(GymDecisionError) as error:save(c,True,revision=1)
    assert error.value.code=='REVISION_CONFLICT'
    assert c.get('user_lok',DAY)['attends_college'] is False

def test_college_plan_changes_preserve_submitted_and_verified_baths(college):
    db,c=college
    save(c,True)
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET status='VERIFIED' WHERE activity_id='ACT-20261021-1600-BATH-2'")
    save(c,False,revision=1)
    with db._connect() as conn:
        assert conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='ACT-20261021-1600-BATH-2'").fetchone()[0]=='VERIFIED'

def test_confirming_routine_preserves_both_college_bath_occurrences(college):
    db,c=college
    save(c,True)
    tw=TimeWindowEngine(db)
    result=tw.confirm_next_day_schedule(dict(schedule_date=DAY,wake_up_time='07:00',bath_time='07:30',gym_time='18:30'))
    assert result['success']
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM scheduled_activities WHERE date=? AND type='BATH' AND is_enabled=1",(DAY,)).fetchone()[0]==2
