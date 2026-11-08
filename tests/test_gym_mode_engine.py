"""
Unit tests for GYM Mode Engine (Windows / Server / Restriction components)
"""

from datetime import datetime
import pytest

from core.database import StrictRecordsDB
from core.gym_mode_engine import (
    GymModeEngine,
    calculate_access_expiry,
    format_lives_badge,
    get_iso_week_key,
    is_within_activation_window,
)
from core.time_window_engine import IST


def test_activation_window_boundaries():
    # 4:59 PM (16:59) -> False
    t_459 = datetime(2026, 10, 19, 16, 59, tzinfo=IST)
    assert not is_within_activation_window(t_459)

    # 5:00 PM (17:00) -> True
    t_500 = datetime(2026, 10, 19, 17, 0, tzinfo=IST)
    assert is_within_activation_window(t_500)

    # 6:00 PM (18:00) -> True
    t_600 = datetime(2026, 10, 19, 18, 0, tzinfo=IST)
    assert is_within_activation_window(t_600)

    # 8:29 PM (20:29) -> True
    t_829 = datetime(2026, 10, 19, 20, 29, tzinfo=IST)
    assert is_within_activation_window(t_829)

    # 8:30 PM (20:30) -> False
    t_830 = datetime(2026, 10, 19, 20, 30, tzinfo=IST)
    assert not is_within_activation_window(t_830)

    # 9:00 PM (21:00) -> False
    t_900 = datetime(2026, 10, 19, 21, 0, tzinfo=IST)
    assert not is_within_activation_window(t_900)


def test_duration_and_cutoff_calculation():
    # 5:00 PM -> 8:30 PM (3.5 hours / daily cutoff)
    t_500 = datetime(2026, 10, 19, 17, 0, tzinfo=IST)
    exp_500 = calculate_access_expiry(t_500)
    assert exp_500 == datetime(2026, 10, 19, 20, 30, tzinfo=IST)

    # 5:30 PM -> 8:30 PM (capped at 8:30 PM daily cutoff)
    t_530 = datetime(2026, 10, 19, 17, 30, tzinfo=IST)
    exp_530 = calculate_access_expiry(t_530)
    assert exp_530 == datetime(2026, 10, 19, 20, 30, tzinfo=IST)

    # 7:00 PM -> 8:30 PM (1h 30m, capped at 8:30 PM)
    t_700 = datetime(2026, 10, 19, 19, 0, tzinfo=IST)
    exp_700 = calculate_access_expiry(t_700)
    assert exp_700 == datetime(2026, 10, 19, 20, 30, tzinfo=IST)

    # 8:00 PM -> 8:30 PM (30m, capped at 8:30 PM)
    t_800 = datetime(2026, 10, 19, 20, 0, tzinfo=IST)
    exp_800 = calculate_access_expiry(t_800)
    assert exp_800 == datetime(2026, 10, 19, 20, 30, tzinfo=IST)

    # 8:29 PM -> 8:30 PM (1m)
    t_829 = datetime(2026, 10, 19, 20, 29, tzinfo=IST)
    exp_829 = calculate_access_expiry(t_829)
    assert exp_829 == datetime(2026, 10, 19, 20, 30, tzinfo=IST)


def test_format_lives_badge():
    assert format_lives_badge(3) == "♥ ♥ ♥ — 3/3 lives"
    assert format_lives_badge(2) == "♥ ♥ ♡ — 2/3 lives"
    assert format_lives_badge(1) == "♥ ♡ ♡ — 1/3 lives"
    assert format_lives_badge(0) == "♡ ♡ ♡ — 0/3 lives"


def test_gym_mode_database_and_engine(tmp_path):
    db_file = str(tmp_path / "test_gym_mode.db")
    db = StrictRecordsDB(db_file)
    engine = GymModeEngine(db)

    # Initial state
    st = db.get_gym_mode_state("user_lok")
    assert not st["is_active"]
    assert st["lives_balance"] == 3

    # Activation outside window
    t_early = datetime(2026, 10, 19, 15, 0, tzinfo=IST)
    res_early = engine.activate_session("ACT-20261019-GYM", dt=t_early)
    assert not res_early["success"]

    # Valid activation at 5:00 PM
    t_valid = datetime(2026, 10, 19, 17, 0, tzinfo=IST)
    from core.gym_daily_decision import DailyGymDecision
    DailyGymDecision(db).save('user_lok','2026-10-19',True,0,'fixture-gym-choice',actor='fixture',device_id='fixture',current=t_early)
    with db._connect() as conn:
        conn.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status) VALUES('ACT-20261019-GYM','user_lok','GYM','2026-10-19','2026-10-19T17:00:00+05:30','2026-10-19T18:30:00+05:30','2026-10-19T19:00:00+05:30','2026-10-19T17:00:00+05:30','SCHEDULED')")
    res_valid = engine.activate_session("ACT-20261019-GYM", dt=t_valid)
    assert res_valid["success"]
    assert "session_id" in res_valid

    # Check database active status at 5:00 PM
    valid_ms = int(t_valid.timestamp() * 1000)
    assert db.is_gym_mode_music_active("user_lok", now_ms=valid_ms)
    st_active = db.get_gym_mode_state("user_lok", now_ms=valid_ms)
    assert st_active["is_active"]
    assert st_active["lives_balance"] == 3

    # Cannot activate again while active
    res_dup = engine.activate_session("ACT-20261019-GYM-2", dt=t_valid)
    assert not res_dup["success"]

    # Deactivate early
    deact_res = engine.deactivate_early(dt=t_valid)
    assert deact_res["success"]

    # Should no longer be active
    assert not db.is_gym_mode_music_active("user_lok")


def test_gym_mode_sync_events_batch(tmp_path):
    db_file = str(tmp_path / "test_sync_gym.db")
    db = StrictRecordsDB(db_file)

    # Android syncs active session
    events = [
        {
            "event_id": "sync_1",
            "event_type": "GYM_MODE_SESSION_UPDATE",
            "timestamp": "2026-10-19T17:00:00",
            "payload": {
                "session_id": "sess_100",
                "gym_activity_id": "ACT-20261019-GYM",
                "activation_time_ms": int(datetime(2026, 10, 19, 17, 0, tzinfo=IST).timestamp() * 1000),
                "access_expiry_ms": int(datetime(2026, 10, 19, 19, 0, tzinfo=IST).timestamp() * 1000),
                "proof_deadline_ms": int(datetime(2026, 10, 19, 19, 30, tzinfo=IST).timestamp() * 1000),
                "status": "ACTIVE",
                "lives_balance": 3,
            },
        }
    ]
    db.process_sync_events_batch("android_device_1", events, "user_lok")
    st = db.get_gym_mode_state("user_lok")
    assert st["lives_balance"] == 3

    # Android syncs life deduction
    deduct_event = [
        {
            "event_id": "sync_2",
            "event_type": "GYM_MODE_LIFE_DEDUCTED",
            "timestamp": "2026-10-19T20:00:00",
            "payload": {
                "gym_activity_id": "ACT-20261019-GYM",
                "reason": "Proof deadline expired",
                "remaining_lives": 2,
            },
        }
    ]
    db.process_sync_events_batch("android_device_1", deduct_event, "user_lok")
    st_deducted = db.get_gym_mode_state("user_lok")
    # Legacy client claims cannot mutate the canonical life balance.
    assert st_deducted["lives_balance"] == 3
    assert not st_deducted['is_active']
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM sync_events WHERE sync_status='PENDING_RECONCILIATION'").fetchone()[0]==2
