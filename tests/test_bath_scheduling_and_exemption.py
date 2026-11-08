"""
Unit tests A through E verifying Section 7:
- Test A: Planned Bath 3:30–4:00 PM, proof window 3:50–4:10 PM -> Excused.
- Test B: Planned Bath 3:30–4:00 PM, proof window 4:50–5:10 PM -> Proof required.
- Test C: Planned Bath 3:30–4:30 PM, proof window 3:50–4:10 PM -> Excused.
- Test D: Planned Bath 3:30–4:00 PM, proof window 3:20–3:40 PM -> Excused.
- Test E: Cancel Bath before start -> Overlapping proof windows restored to normal.
"""
from datetime import datetime, timedelta
import json
import uuid
import pytest
from zoneinfo import ZoneInfo

from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_bath_section7.sqlite"
    return StrictRecordsDB(str(db_file))


def seed_hourly_slot(conn, account, date, zone, target_hhmm, slot_display=None):
    slot_target = datetime.fromisoformat(f"{date}T{target_hhmm}:00").replace(tzinfo=zone)
    w_start = slot_target - timedelta(minutes=10)
    w_end = slot_target + timedelta(minutes=10)
    notif = w_start - timedelta(minutes=1)
    close_warn = w_end - timedelta(minutes=1)
    disp = slot_display or f"{slot_target.hour-1:02d}:00-{slot_target.hour:02d}:00"
    act_id = f"ACT-{date.replace('-', '')}-{slot_target.strftime('%H%M')}-HOURLY"
    details = {
        "hour_slot": disp,
        "schedule_source": "ANDROID_CACHED_PLAN",
        "attendance_status": "UPCOMING"
    }
    conn.execute(
        """INSERT INTO scheduled_activities (
            activity_id, account_id, type, title, date, scheduled_time,
            window_start, window_end, notification_time, closing_warning_time,
            window_duration_minutes, status, attendance_status, points,
            penalty_points, is_enabled, proof_requirements, validation_rule, details_json
        ) VALUES (?, ?, 'HOURLY', ?, ?, ?, ?, ?, ?, ?, 20, 'SCHEDULED', 'UPCOMING', 8, 0, 1, 'NONE', 'HOURLY_DISCIPLINE_CHECKPOINT', ?)""",
        (
            act_id, account, f"Hourly Discipline Check ({disp})", date,
            slot_target.isoformat(), w_start.isoformat(), w_end.isoformat(),
            notif.isoformat(), close_warn.isoformat(), json.dumps(details)
        )
    )
    return act_id


def test_A_planned_bath_330_to_400_excuses_proof_window_350_to_410(test_db):
    """
    Test A: Planned Bath: 3:30 PM (15:30), Duration: 30 minutes (3:30–4:00 PM).
    Proof window: 3:50–4:10 PM (Target: 4:00 PM).
    Overlap: 10 minutes (3:50 PM to 4:00 PM).
    Result: EXCUSED automatically (EXCUSED_BY_BATH).
    """
    account = "user_lok"
    date = "2026-11-15"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        act_id = seed_hourly_slot(conn, account, date, zone, "16:00", "15:00-16:00")

    dcd = DailyCollegeDecision(test_db)
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "15:30",
            "window_start": "13:00",
            "window_end": "18:00",
            "activity_duration_minutes": 30
        }
    ]
    dcd.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="test", device_id="test"
    )

    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row is not None
        assert row["status"] == "EXCUSED"
        assert row["attendance_status"] == "EXCUSED"
        assert row["points"] == 0
        details = json.loads(row["details_json"])
        assert details.get("suppressed_by_bath") is True
        assert details.get("reconciliation_status") == "EXCUSED_BY_BATH"
        assert details.get("attendance_status") == "EXCUSED_BY_BATH"
        assert "overlaps your scheduled Bath activity" in details.get("suppression_reason", "")


def test_B_planned_bath_330_to_400_does_not_excuse_proof_window_450_to_510(test_db):
    """
    Test B: Planned Bath: 3:30–4:00 PM.
    Proof window: 4:50–5:10 PM (Target: 5:00 PM).
    Overlap: None (Bath ends at 4:00 PM, proof begins at 4:50 PM).
    Result: Normal proof required (status remains SCHEDULED / UPCOMING, 8 points).
    """
    account = "user_lok"
    date = "2026-11-15"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        act_id = seed_hourly_slot(conn, account, date, zone, "17:00", "16:00-17:00")

    dcd = DailyCollegeDecision(test_db)
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "15:30",
            "window_start": "13:00",
            "window_end": "18:00",
            "activity_duration_minutes": 30
        }
    ]
    dcd.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="test", device_id="test"
    )

    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row is not None
        assert row["status"] == "SCHEDULED"
        assert row["points"] == 8
        details = json.loads(row["details_json"])
        assert not details.get("suppressed_by_bath")


def test_C_planned_bath_330_to_430_excuses_proof_window_350_to_410(test_db):
    """
    Test C: Planned Bath: 3:30–4:30 PM (Duration: 60 minutes).
    Proof window: 3:50–4:10 PM (Target: 4:00 PM).
    Overlap: Full 20-minute overlap (3:50–4:10 PM is entirely inside 3:30–4:30 PM).
    Result: EXCUSED automatically.
    """
    account = "user_lok"
    date = "2026-11-15"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        act_id = seed_hourly_slot(conn, account, date, zone, "16:00", "15:00-16:00")

    dcd = DailyCollegeDecision(test_db)
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "15:30",
            "window_start": "13:00",
            "window_end": "18:00",
            "activity_duration_minutes": 60
        }
    ]
    dcd.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="test", device_id="test"
    )

    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row is not None
        assert row["status"] == "EXCUSED"
        assert row["attendance_status"] == "EXCUSED"
        assert row["points"] == 0
        details = json.loads(row["details_json"])
        assert details.get("suppressed_by_bath") is True
        assert details.get("reconciliation_status") == "EXCUSED_BY_BATH"
        assert details.get("bath_overlap_kind") == "FULL"


def test_D_planned_bath_330_to_400_excuses_proof_window_320_to_340(test_db):
    """
    Test D: Planned Bath: 3:30–4:00 PM.
    Proof window: 3:20–3:40 PM (Target: 3:30 PM, anchored from 08:30 wake-up).
    Overlap: 10 minutes (3:30 PM to 3:40 PM).
    Result: EXCUSED automatically.
    """
    account = "user_lok"
    date = "2026-11-15"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        # Save a locked routine schedule with 08:30 wake-up time
        conn.execute(
            """INSERT INTO routine_schedules (
                schedule_id, account_id, schedule_date, wake_up_time, status, schedule_version, created_at, updated_at
            ) VALUES ('sched-0830', ?, ?, '08:30', 'LOCKED', 1, ?, ?)""",
            (account, date, current.isoformat(), current.isoformat())
        )
        # Window [15:20, 15:40] target 15:30
        slot_target = datetime.fromisoformat(f"{date}T15:30:00").replace(tzinfo=zone)
        w_start = slot_target - timedelta(minutes=10) # 15:20
        w_end = slot_target + timedelta(minutes=10)   # 15:40
        act_id = f"ACT-{date.replace('-', '')}-1530-HOURLY"
        details = {"hour_slot": "14:30-15:30", "attendance_status": "UPCOMING"}
        conn.execute(
            """INSERT INTO scheduled_activities (
                activity_id, account_id, type, title, date, scheduled_time,
                window_start, window_end, notification_time, closing_warning_time,
                window_duration_minutes, status, attendance_status, points,
                penalty_points, is_enabled, proof_requirements, validation_rule, details_json
            ) VALUES (?, ?, 'HOURLY', 'Hourly Discipline Check (14:30-15:30)', ?, ?, ?, ?, ?, ?, 20, 'SCHEDULED', 'UPCOMING', 8, 0, 1, 'NONE', 'HOURLY_DISCIPLINE_CHECKPOINT', ?)""",
            (
                act_id, account, date, slot_target.isoformat(), w_start.isoformat(), w_end.isoformat(),
                w_start.isoformat(), w_end.isoformat(), json.dumps(details)
            )
        )

    dcd = DailyCollegeDecision(test_db)
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "15:30",
            "window_start": "13:00",
            "window_end": "18:00",
            "activity_duration_minutes": 30
        }
    ]
    dcd.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="test", device_id="test"
    )

    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row is not None
        assert row["status"] == "EXCUSED"
        assert row["attendance_status"] == "EXCUSED"
        assert row["points"] == 0
        details = json.loads(row["details_json"])
        assert details.get("suppressed_by_bath") is True
        assert details.get("reconciliation_status") == "EXCUSED_BY_BATH"
        assert details.get("bath_overlap_kind") == "PARTIAL"


def test_E_cancel_bath_before_start_restores_future_proof_windows(test_db):
    """
    Test E: Cancel Bath before start -> Restore future proof windows to normal status.
    """
    account = "user_lok"
    date = "2026-11-15"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        act_id = seed_hourly_slot(conn, account, date, zone, "16:00", "15:00-16:00")

    dcd = DailyCollegeDecision(test_db)
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "15:30",
            "window_start": "13:00",
            "window_end": "18:00",
            "activity_duration_minutes": 30
        }
    ]
    dcd.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="test", device_id="test"
    )

    # 1. Verify excused initially
    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row["status"] == "EXCUSED"

    # 2. Cancel Bath before start
    cancel_res = dcd.cancel_bath(account, date)
    assert cancel_res["success"] is True

    # 3. Verify restored to normal status and points restored
    with test_db._connect() as conn:
        row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (act_id,)).fetchone()
        assert row["status"] == "SCHEDULED"
        assert row["points"] == 8
        details = json.loads(row["details_json"])
        assert not details.get("suppressed_by_bath")
        assert not details.get("reconciliation_status")
