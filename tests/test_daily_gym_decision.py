from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import json
import uuid

import pytest
from core.database import StrictRecordsDB
from core.gym_daily_decision import DailyGymDecision,GymDecisionError
from core.gym_mode_engine import GymModeEngine
from core.time_window_engine import IST,TimeWindowEngine

NOW=datetime(2026,10,20,17,0,tzinfo=IST)
DAY=NOW.strftime('%Y-%m-%d')
ACT='ACT-20261020-1830-GYM'

@pytest.fixture
def gym(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'gym.sqlite'))
    TimeWindowEngine(db).ensure_activities_in_db(DAY,wakeup_time='07:00',reference_now=NOW.replace(hour=6))
    with db._connect() as c:
        c.execute('''INSERT INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,window_start,window_end,
            notification_time,status,details_json) VALUES(?,?,'GYM','Fixture booked Gym',?,?,?,?,?,'SCHEDULED','{}')''',
            (ACT,'user_lok',DAY,NOW.replace(hour=18,minute=30).isoformat(),NOW.replace(hour=20,minute=50).isoformat(),
             NOW.replace(hour=21,minute=10).isoformat(),NOW.replace(hour=20,minute=50).isoformat()))
        c.execute("UPDATE scheduled_activities SET window_end=?,status='SCHEDULED',is_enabled=1 WHERE activity_id=?",
            ((NOW.replace(hour=21,minute=0)).isoformat(),ACT))
    return db,DailyGymDecision(db),GymModeEngine(db)

def choose(service,planned,date=DAY,revision=0,request=None,admin=False):
    return service.save('user_lok',date,planned,revision,request or str(uuid.uuid4()),actor='fixture',device_id='fixture_pc',
        current=NOW,admin_authorized=admin)

def test_decision_required_fails_closed_without_mutating_lives(gym):
    db,d,e=gym
    assert d.get('user_lok',DAY)['status']=='GYM_DECISION_REQUIRED'
    assert not e.activate_session(ACT,dt=NOW)['success']
    assert e.state(dt=NOW)['lives_balance']==3

def test_first_choice_cannot_forge_a_different_timezone(gym):
    db,d,e=gym
    with pytest.raises(GymDecisionError) as error:
        d.save('user_lok',DAY,True,0,'timezone-forgery',actor='fixture',device_id='fixture',timezone='America/New_York',current=NOW)
    assert error.value.code=='TIMEZONE_CONFLICT'

def test_gym_cutoff_uses_saved_schedule_timezone_across_ist_midnight(gym):
    from zoneinfo import ZoneInfo
    db,d,e=gym
    db.save_routine_schedule_record(dict(schedule_id='ny-gym',schedule_date=DAY,timezone='America/New_York',schedule_version=1,status='DRAFT'))
    local=datetime(2026,10,20,17,0,tzinfo=ZoneInfo('America/New_York'))
    d.save('user_lok',DAY,True,0,'ny-gym-choice',actor='fixture',device_id='fixture',timezone='America/New_York',current=local)
    with db._connect() as c:
        c.execute('UPDATE scheduled_activities SET scheduled_time=?,window_start=?,window_end=?,status=\'SCHEDULED\' WHERE activity_id=?',
            (local.replace(hour=18).isoformat(),local.replace(hour=20).isoformat(),local.replace(hour=22).isoformat(),ACT))
    result=e.activate_session(ACT,dt=local)
    assert result['success'] and result['access_expiry']==local.replace(hour=20,minute=30).isoformat()
    assert result['state']['timezone']=='America/New_York'

def test_no_is_dated_durable_and_creates_no_proof_or_life_penalty(gym):
    db,d,e=gym
    value=choose(d,False)['decision']
    assert value['status']=='GYM_NOT_PLANNED' and value['revision']==1
    reopened=StrictRecordsDB(db.db_path)
    for hour in (0,12,17,20,23):
        assert not GymModeEngine(reopened).activate_session(ACT,dt=NOW.replace(hour=hour))['success']
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM gym_mode_sessions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM gym_mode_violations').fetchone()[0]==0
    assert e.state(dt=NOW)['lives_balance']==3
    assert d.get('user_lok','2026-10-21')['status']=='GYM_DECISION_REQUIRED'

@pytest.mark.parametrize('time,allowed,expiry', [('16:59',False,None),('17:00',True,'20:30'),('18:00',True,'20:30'),
    ('19:00',True,'20:30'),('20:29',True,'20:30'),('20:30',False,None),('20:31',False,None)])
def test_yes_obeys_all_activation_boundaries_and_hard_cutoff(gym,time,allowed,expiry):
    db,d,e=gym
    choose(d,True)
    hour,minute=map(int,time.split(':'));current=NOW.replace(hour=hour,minute=minute)
    result=e.activate_session(ACT,dt=current,expected_revision=1)
    assert result['success']==allowed
    if allowed:
        assert datetime.fromisoformat(result['access_expiry']).strftime('%H:%M')==expiry
        assert e.state(dt=current)['lives_balance']==3

def test_arbitrary_other_day_disabled_or_missing_session_cannot_activate(gym):
    db,d,e=gym
    choose(d,True)
    assert not e.activate_session('arbitrary-id',dt=NOW)['success']
    with db._connect() as c:c.execute('UPDATE scheduled_activities SET is_enabled=0 WHERE activity_id=?',(ACT,))
    assert not e.activate_session(ACT,dt=NOW)['success']

def test_stale_yes_cannot_replace_newer_no_and_requests_are_idempotent(gym):
    db,d,e=gym
    req=str(uuid.uuid4())
    first=choose(d,True,request=req)
    assert choose(d,True,request=req)==first
    choose(d,False,revision=1,admin=True)
    with pytest.raises(GymDecisionError,match='newer Gym decision'):choose(d,True,revision=1)
    assert not e.activate_session(ACT,dt=NOW,expected_revision=1)['success']

def test_future_choice_never_disables_today_or_rewrites_prior_events(gym):
    db,d,e=gym
    choose(d,True)
    assert e.activate_session(ACT,dt=NOW)['success']
    choose(d,False,date='2026-10-21')
    assert e.state(dt=NOW)['is_active']
    with pytest.raises(GymDecisionError,match='Past Gym decisions'):choose(d,False,date='2026-10-19')

def test_no_revokes_active_music_without_erasing_consumed_proof_obligation(gym):
    db,d,e=gym
    choose(d,True);activated=e.activate_session(ACT,dt=NOW)
    choose(d,False,revision=1,admin=True)
    state=e.state(dt=NOW)
    assert not state['is_active'] and state['lives_balance']==3
    assert state['proof_status']=='GYM_PROOF_PENDING'
    with db._connect() as c:assert c.execute('SELECT count(*) FROM gym_mode_sessions').fetchone()[0]==1

def test_confirmed_schedule_changes_require_existing_admin_auth(gym):
    db,d,e=gym
    choose(d,True,date='2026-10-21')
    result=TimeWindowEngine(db).confirm_next_day_schedule(dict(schedule_date='2026-10-21',wake_up_time='07:00',bath_time='07:30',gym_time='18:30'))
    assert result['success']
    with pytest.raises(GymDecisionError) as error:choose(d,False,date='2026-10-21',revision=1)
    assert error.value.code=='ADMIN_REQUIRED'
    assert choose(d,False,date='2026-10-21',revision=1,admin=True)['success']

def test_concurrent_activations_cannot_create_two_entitlements(gym):
    db,d,e=gym
    choose(d,True)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:e.activate_session(ACT,dt=NOW),range(2)))
    assert sum(bool(r['success']) for r in results)==1
    with db._connect() as c:assert c.execute('SELECT count(*) FROM gym_mode_sessions').fetchone()[0]==1

def test_early_off_preserves_obligation_and_cannot_reactivate(gym):
    db,d,e=gym
    choose(d,True);e.activate_session(ACT,dt=NOW)
    e.deactivate_early(dt=NOW+timedelta(minutes=10))
    assert e.state(dt=NOW+timedelta(minutes=10))['proof_status']=='GYM_PROOF_PENDING'
    assert not e.activate_session(ACT,dt=NOW+timedelta(minutes=11))['success']

def add_review(db,status,reason='',reviewer='ADMIN_FIXTURE'):
    with db._connect() as c:
        c.execute('''INSERT INTO proof_reviews(review_id,evidence_id,user_id,date,task_code,activity_id,status,submitted_at,reviewed_by,reason_category)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',('review-fixture','evidence-fixture','user_lok',DAY,'GYM',ACT,status,NOW.isoformat(),reviewer,reason))

def test_expiry_pending_review_or_network_uncertainty_alone_never_deducts(gym):
    db,d,e=gym
    choose(d,True);e.activate_session(ACT,dt=NOW)
    assert e.state(dt=NOW.replace(hour=22))['lives_balance']==3
    add_review(db,'PENDING')
    assert e.state(dt=NOW.replace(hour=22))['lives_balance']==3

@pytest.mark.parametrize('reason',['CONFIRMED_MISSING_PROOF','CONFIRMED_FAKE'])
def test_confirmed_violation_deducts_at_most_once_per_session(gym,reason):
    db,d,e=gym
    choose(d,True);e.activate_session(ACT,dt=NOW)
    add_review(db,'REJECTED',reason)
    assert e.state(dt=NOW.replace(hour=22))['lives_balance']==2
    assert e.state(dt=NOW.replace(hour=22,minute=30))['lives_balance']==2
    with db._connect() as c:assert c.execute('SELECT count(*) FROM gym_mode_violations').fetchone()[0]==1

def test_accepted_gym_proof_is_fulfilled_once_without_life_loss(gym):
    db,d,e=gym
    choose(d,True);e.activate_session(ACT,dt=NOW)
    add_review(db,'APPROVED')
    with db._connect() as c:
        c.execute("INSERT INTO evidence_files(evidence_id,date,record_type,related_record_id,file_path,timestamp,device_id) VALUES('evidence-fixture',?,'Gym',?,'fixture.jpg',?,'fixture')",(DAY,ACT,NOW.isoformat()))
        c.execute("UPDATE scheduled_activities SET status='VERIFIED' WHERE activity_id=?",(ACT,))
    for _ in range(2):
        state=e.state(dt=NOW+timedelta(hours=2))
        assert state['proof_status']=='GYM_PROOF_ACCEPTED' and state['lives_balance']==3


def test_review_label_without_retained_gym_evidence_cannot_fulfill_obligation(gym):
    db,d,e=gym
    choose(d,True);e.activate_session(ACT,dt=NOW)
    add_review(db,'APPROVED')
    with db._connect() as c:c.execute("UPDATE scheduled_activities SET status='VERIFIED' WHERE activity_id=?",(ACT,))
    state=e.state(dt=NOW+timedelta(hours=2))
    assert state['proof_status']=='GYM_PROOF_PENDING' and state['lives_balance']==3

def test_weekly_reset_is_monotonic_and_restart_cannot_reset_lives(gym):
    db,d,e=gym
    e.state(dt=NOW)
    with db._connect() as c:c.execute("UPDATE gym_mode_state SET lives_balance=1 WHERE account_id='user_lok'")
    assert GymModeEngine(StrictRecordsDB(db.db_path)).state(dt=NOW)['lives_balance']==1
    assert e.state(dt=NOW-timedelta(days=1))['lives_balance']==1
    assert e.state(dt=NOW+timedelta(days=6))['lives_balance']==3

def test_legacy_sync_claims_cannot_raise_lives_or_activate_gym(gym):
    db,d,e=gym
    e.state(dt=NOW)
    with db._connect() as c:c.execute("UPDATE gym_mode_state SET lives_balance=0 WHERE account_id='user_lok'")
    events=[dict(event_id='legacy-reset',event_type='GYM_MODE_RESET',payload={'lives_balance':3}),
        dict(event_id='legacy-session',event_type='GYM_MODE_SESSION_UPDATE',payload={'status':'ACTIVE','gym_activity_id':ACT,'lives_balance':3})]
    db.process_sync_events_batch('fixture-android',events,'user_lok')
    assert e.state(dt=NOW)['lives_balance']==0 and not e.state(dt=NOW)['is_active']
    with db._connect() as c:assert c.execute("SELECT count(*) FROM sync_events WHERE sync_status='PENDING_RECONCILIATION'").fetchone()[0]==2

def test_no_decision_prevents_scheduler_from_recreating_obligation(gym):
    db,d,e=gym
    choose(d,False)
    items=TimeWindowEngine(db).ensure_activities_in_db(DAY,account_id='user_lok')
    assert all(item.get('status')=='CANCELLED' and not item.get('is_enabled') for item in items if item['type']=='GYM')

def test_routine_schedule_validation_wakeup_omitted_and_gym_conditional(gym):
    db,d,e=gym
    engine = TimeWindowEngine(db)

    # 1. Routine schedule with omitted wake_up_time (removed from Schedule Config) and gym_enabled=False
    payload_no_gym = {
        "schedule_date": DAY,
        "bath_time": "07:30",
        "gym_enabled": False,
        "account_id": "user_lok"
    }
    val_no_gym = engine.validate_routine_schedule(payload_no_gym, DAY)
    assert val_no_gym["valid"] is True, f"Errors: {val_no_gym.get('errors')}"
    assert val_no_gym["gym_enabled"] is False

    # 2. Routine schedule with omitted wake_up_time and gym_enabled=True with valid gym_time & duration
    payload_yes_gym = {
        "schedule_date": DAY,
        "bath_time": "07:30",
        "gym_time": "18:30",
        "gym_duration_minutes": 90,
        "gym_enabled": True,
        "account_id": "user_lok"
    }
    val_yes_gym = engine.validate_routine_schedule(payload_yes_gym, DAY)
    assert val_yes_gym["valid"] is True, f"Errors: {val_yes_gym.get('errors')}"
    assert val_yes_gym["gym_enabled"] is True
