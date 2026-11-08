"""
Comprehensive integration test suite for Bath Schedule, Hourly Attendance Excuse & Proof Window Alignment.
Validates the exact fix requested by the user:
- Planned Bath: 2:22 PM, duration: 60 min -> Activity: 2:22–3:22 PM.
- Assigned attendance slot: 2:00–3:00 PM (never 1:00–2:00 PM).
- 1:50–2:10 PM Hourly Discipline Check: NOT excused (ends at 2:10 PM, before Bath starts at 2:22 PM). Target 8 PTS.
- 2:50–3:10 PM Hourly Discipline Check: EXCUSED (falls within 2:22–3:22 PM). 0 PTS, reason "Excused — Scheduled Bath Activity".
- Bath proof window: 3:52–4:12 PM (30 min delay after 3:22 PM, 20 min window), Target 5 PTS.
- Dynamic rescheduling & duration changes dynamically recalculate assigned slot, excusals, un-excusals, and points.
"""
from datetime import datetime, timedelta
import json
import uuid
import pytest
from zoneinfo import ZoneInfo

from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_bath_alignment.sqlite"
    return StrictRecordsDB(str(db_file))


def seed_hourly_windows(conn, account, date, zone):
    """Seed hourly discipline windows from 07:00 to 22:00 with standard 20m window [XX:50, (XX+1):10]."""
    for h in range(7, 23):
        slot_target = datetime.fromisoformat(f"{date}T{h:02d}:00:00").replace(tzinfo=zone)
        w_start = slot_target - timedelta(minutes=10)
        w_end = slot_target + timedelta(minutes=10)
        notif = w_start - timedelta(minutes=1)
        close_warn = w_end - timedelta(minutes=1)
        prev_h = h - 1
        slot_display = f"{prev_h:02d}:00-{h:02d}:00"
        act_id = f"ACT-{date.replace('-', '')}-{h:02d}00-HOURLY"
        details = {
            "hour_slot": slot_display,
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
                act_id, account, f"Hourly Discipline Check ({slot_display})", date,
                slot_target.isoformat(), w_start.isoformat(), w_end.isoformat(),
                notif.isoformat(), close_warn.isoformat(), json.dumps(details)
            )
        )


def test_bath_222pm_assigned_slot_and_overlap_alignment(test_db):
    """
    Scenario 1: Bath at 2:22 PM (60 min).
    - Planned: 2:22 PM (14:22), End: 3:22 PM (15:22).
    - Assigned slot MUST be 2:00–3:00 PM (14:00–15:00, target 15:00).
    - 1:50–2:10 PM window ends before Bath begins (2:10 <= 2:22) -> NOT EXCUSED, target 8 PTS.
    - 2:50–3:10 PM window is within Bath activity (14:22–15:22) -> EXCUSED, 0 PTS, reason "Excused — Scheduled Bath Activity".
    - Bath proof window is 3:52–4:12 PM (30 min delay after 3:22 PM).
    """
    account = "user_lok"
    date = "2026-11-10"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        seed_hourly_windows(conn, account, date, zone)

    college_decision = DailyCollegeDecision(test_db)
    # Save a non-college day with 1 Bath at 14:22, 60 minutes duration
    bath_plans = [
        {
            "occurrence": 1,
            "planned_time": "14:22",
            "window_start": "06:00",
            "window_end": "22:00",
            "activity_duration_minutes": 60
        }
    ]
    college_decision.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plans, current=current,
        actor="fixture", device_id="fixture"
    )

    with test_db._connect() as conn:
        # 1. Verify Bath occurrence details
        bath_row = conn.execute(
            "SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND type='BATH'",
            (account, date)
        ).fetchone()
        assert bath_row is not None
        b_details = json.loads(bath_row["details_json"])
        
        # Canonical assigned slot MUST be 2:00–3:00 PM
        assert b_details["held_hourly_slot_display"] in ("2:00–3:00 PM", "2:00 PM–3:00 PM")
        assert b_details["held_hourly_slot"] == "14:00-15:00"
        assert b_details["held_hourly_act_id"] == f"ACT-{date.replace('-', '')}-1500-HOURLY"
        
        # Bath activity interval: 2:22–3:22 PM
        assert b_details["reserved_start"] == f"{date}T14:22:00+05:30"
        assert b_details["reserved_end"] == f"{date}T15:22:00+05:30"
        
        # Bath photo proof window: 3:52–4:12 PM
        assert bath_row["window_start"] == f"{date}T15:52:00+05:30"
        assert bath_row["window_end"] == f"{date}T16:12:00+05:30"
        assert bath_row["points"] == 5

        # 2. Verify Hourly 1:50–2:10 PM (Target 14:00 / 2:00 PM) -> NOT EXCUSED
        h14 = conn.execute(
            f"SELECT * FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1400-HOURLY'"
        ).fetchone()
        assert h14 is not None
        assert h14["status"] != "EXCUSED", "1:50–2:10 PM window ends at 2:10 PM, before bath at 2:22 PM; must NOT be excused!"
        h14_details = json.loads(h14["details_json"])
        assert not h14_details.get("suppressed_by_bath")

        # 3. Verify Hourly 2:50–3:10 PM (Target 15:00 / 3:00 PM) -> EXCUSED
        h15 = conn.execute(
            f"SELECT * FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1500-HOURLY'"
        ).fetchone()
        assert h15 is not None
        assert h15["status"] == "EXCUSED", "2:50–3:10 PM window falls within 2:22–3:22 PM; MUST be excused!"
        assert h15["points"] == 0, "Excused hourly check awards 0 PTS"
        h15_details = json.loads(h15["details_json"])
        assert h15_details.get("suppressed_by_bath") is True
        assert h15_details.get("suppression_reason") == "Excused — Scheduled Bath Activity"
        assert h15_details.get("bath_slot_display") in ("2:00–3:00 PM", "2:00 PM–3:00 PM")


def test_dynamic_reschedule_unexcuses_prior_and_excuses_new(test_db):
    """
    Scenario 2: Dynamic Rescheduling from 1:00 PM to 2:22 PM.
    - Initial: Bath at 1:00 PM (13:00–14:00) overlaps 1:50–2:10 PM (ends at 14:10, overlaps 13:50–14:00).
      So 1:50–2:10 PM is EXCUSED.
    - Reschedule: Bath moved to 2:22 PM (14:22–15:22).
    - 1:50–2:10 PM MUST be UN-EXCUSED (restored to SCHEDULED / UPCOMING, 8 points configured).
    - 2:50–3:10 PM MUST be EXCUSED (0 PTS).
    """
    account = "user_lok"
    date = "2026-11-11"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T12:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        seed_hourly_windows(conn, account, date, zone)

    college_decision = DailyCollegeDecision(test_db)
    
    # 1. Initially plan Bath at 1:00 PM (13:00)
    bath_1pm = [{
        "occurrence": 1,
        "planned_time": "13:00",
        "window_start": "06:00",
        "window_end": "22:00",
        "activity_duration_minutes": 60
    }]
    college_decision.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_1pm, current=current,
        actor="fixture", device_id="fixture"
    )

    with test_db._connect() as conn:
        h14 = conn.execute(
            f"SELECT status, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1400-HOURLY'"
        ).fetchone()
        assert h14["status"] == "EXCUSED"
        assert json.loads(h14["details_json"]).get("suppressed_by_bath") is True

    # 2. User reschedules Bath to 2:22 PM (14:22)
    bath_222pm = [{
        "occurrence": 1,
        "planned_time": "14:22",
        "window_start": "06:00",
        "window_end": "22:00",
        "activity_duration_minutes": 60
    }]
    college_decision.save(
        account, date, attends=False, expected_revision=1,
        request_id=str(uuid.uuid4()), bath_plans=bath_222pm, current=current,
        actor="fixture", device_id="fixture"
    )

    with test_db._connect() as conn:
        # h14 (1:50–2:10 PM) MUST be un-excused!
        h14_after = conn.execute(
            f"SELECT status, points, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1400-HOURLY'"
        ).fetchone()
        assert h14_after["status"] in ("SCHEDULED", "UPCOMING", "OPEN"), f"Expected restored status, got {h14_after['status']}"
        h14_det = json.loads(h14_after["details_json"])
        assert not h14_det.get("suppressed_by_bath")
        assert "suppression_reason" not in h14_det or not h14_det["suppression_reason"]

        # h15 (2:50–3:10 PM) MUST now be excused!
        h15_after = conn.execute(
            f"SELECT status, points, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1500-HOURLY'"
        ).fetchone()
        assert h15_after["status"] == "EXCUSED"
        assert h15_after["points"] == 0
        h15_det = json.loads(h15_after["details_json"])
        assert h15_det.get("suppressed_by_bath") is True
        assert h15_det.get("suppression_reason") == "Excused — Scheduled Bath Activity"


def test_longer_duration_excuses_both_overlapping_windows(test_db):
    """
    Scenario 3: 90-minute Bath from 2:00 PM to 3:30 PM (14:00–15:30).
    - Window 1: 1:50–2:10 PM overlaps 14:00–14:10 -> EXCUSED.
    - Window 2: 2:50–3:10 PM overlaps 14:50–15:10 -> EXCUSED.
    Both overlapping attendance checks are automatically excused.
    """
    account = "user_lok"
    date = "2026-11-12"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        seed_hourly_windows(conn, account, date, zone)

    college_decision = DailyCollegeDecision(test_db)
    bath_90m = [{
        "occurrence": 1,
        "planned_time": "14:00",
        "window_start": "06:00",
        "window_end": "22:00",
        "activity_duration_minutes": 90
    }]
    college_decision.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_90m, current=current,
        actor="fixture", device_id="fixture"
    )

    with test_db._connect() as conn:
        h14 = conn.execute(
            f"SELECT status, points, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1400-HOURLY'"
        ).fetchone()
        h15 = conn.execute(
            f"SELECT status, points, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1500-HOURLY'"
        ).fetchone()

        assert h14["status"] == "EXCUSED"
        assert h14["points"] == 0
        assert json.loads(h14["details_json"]).get("suppressed_by_bath") is True

        assert h15["status"] == "EXCUSED"
        assert h15["points"] == 0
        assert json.loads(h15["details_json"]).get("suppressed_by_bath") is True

        # Bath proof window: 90m activity ends at 15:30 -> proof window starts 30m later at 16:00 to 16:20
        bath = conn.execute(
            "SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND type='BATH'",
            (account, date)
        ).fetchone()
        assert bath["window_start"] == f"{date}T16:00:00+05:30"
        assert bath["window_end"] == f"{date}T16:20:00+05:30"


def test_edge_touching_window_is_not_excused(test_db):
    """
    Scenario 4: Window ends exactly when Bath starts:
    Window: 1:50–2:10 PM. Bath starts at 2:10 PM (14:10).
    Because [13:50, 14:10) and [14:10, 15:10) do not overlap, 1:50–2:10 PM is NOT excused.
    """
    account = "user_lok"
    date = "2026-11-13"
    zone = ZoneInfo("Asia/Kolkata")
    current = datetime.fromisoformat(f"{date}T13:00:00").replace(tzinfo=zone)

    with test_db._connect() as conn:
        seed_hourly_windows(conn, account, date, zone)

    college_decision = DailyCollegeDecision(test_db)
    bath_plan = [{
        "occurrence": 1,
        "planned_time": "14:10",
        "window_start": "06:00",
        "window_end": "22:00",
        "activity_duration_minutes": 60
    }]
    college_decision.save(
        account, date, attends=False, expected_revision=0,
        request_id=str(uuid.uuid4()), bath_plans=bath_plan, current=current,
        actor="fixture", device_id="fixture"
    )

    with test_db._connect() as conn:
        h14 = conn.execute(
            f"SELECT status, details_json FROM scheduled_activities WHERE activity_id='ACT-{date.replace('-', '')}-1400-HOURLY'"
        ).fetchone()
        assert h14["status"] != "EXCUSED", "Touching boundary at 14:10 must not excuse 1:50–2:10 PM"
        assert not json.loads(h14["details_json"]).get("suppressed_by_bath")
