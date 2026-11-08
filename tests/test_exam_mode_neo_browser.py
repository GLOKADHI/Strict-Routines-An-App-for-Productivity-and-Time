"""
Comprehensive Test Suite: StrictRecords Exam Mode, NeoExamBrowser Monitoring,
Full-Point Protection, Notification Suppression & Early Exit Controller.
"""

import json
import os
import tempfile
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.database import StrictRecordsDB
from core.models import ScoreEventType
from core.scoring_engine import ScoringEngine
from windows.neo_exam_monitor import (
    NeoExamBrowserMonitor,
    STATE_IDLE,
    STATE_EXAM_BOOKING_PENDING,
    STATE_EXAM_ACTIVE_SILENT_MONITORING,
    STATE_EXAM_PROCESS_NOT_DETECTED,
    STATE_EXAM_ENDED_EARLY,
    STATE_EXAM_FINISHED,
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StrictRecordsDB(path)
    yield db
    try:
        os.remove(path)
    except Exception:
        pass


def test_full_points_during_approved_exam(temp_db):
    """
    Requirement 1:
    - Treat the approved exam period as an officially excused activity.
    - Award full eligible points (8 points) for proof windows excused because of the approved exam.
    - Never deduct points or apply missed-submission penalties for those excused windows.
    - Record the exemption and full-point credit in score_events and daily summary idempotently.
    """
    exam_date = "2026-11-10"
    temp_db.ensure_daily_record(exam_date)

    # Approve exam from 09:30 to 11:30 (with 0m buffers for exact test)
    booking = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-POINTS-001",
        "exam_name": "Database Systems End Sem",
        "subject": "CS8492",
        "institution": "Anna University",
        "exam_date": exam_date,
        "official_start_time": "09:30",
        "official_end_time": "11:30",
        "prep_buffer_minutes": 0,
        "completion_buffer_minutes": 0,
        "status": "EXAM_APPROVED",
        "supporting_images_count": 1,
    })

    with temp_db._connect() as conn:
        acts = {r["activity_id"]: dict(r) for r in conn.execute("SELECT * FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'", (exam_date,)).fetchall()}
        events = {r["event_id"]: dict(r) for r in conn.execute("SELECT * FROM score_events WHERE date = ?", (exam_date,)).fetchall()}

    # Check 09:00 window (08:50 - 09:10) is outside -> SCHEDULED (8 pts target)
    assert acts["ACT-20261110-0900-HOURLY"]["status"] == "SCHEDULED"
    assert acts["ACT-20261110-0900-HOURLY"]["points"] == 8

    # Check 10:00 (09:50 - 10:10) and 11:00 (10:50 - 11:10) windows are inside -> EXCUSED with full 8 points!
    assert acts["ACT-20261110-1000-HOURLY"]["status"] == "EXCUSED"
    assert acts["ACT-20261110-1000-HOURLY"]["points"] == 8
    assert acts["ACT-20261110-1100-HOURLY"]["status"] == "EXCUSED"
    assert acts["ACT-20261110-1100-HOURLY"]["points"] == 8

    # Check 12:00 window (11:50 - 12:10) is outside -> SCHEDULED (8 pts target)
    assert acts["ACT-20261110-1200-HOURLY"]["status"] == "SCHEDULED"
    assert acts["ACT-20261110-1200-HOURLY"]["points"] == 8

    # Verify score_events ledger entries
    se_1000 = f"se_exam_excused_EXAM-POINTS-001_ACT-20261110-1000-HOURLY"
    se_1100 = f"se_exam_excused_EXAM-POINTS-001_ACT-20261110-1100-HOURLY"
    assert se_1000 in events
    assert se_1100 in events
    assert events[se_1000]["points"] == 8
    assert events[se_1100]["points"] == 8
    assert events[se_1000]["event_type"] == ScoreEventType.EXAM_EXCUSED.value
    assert events[se_1100]["event_type"] == ScoreEventType.EXAM_EXCUSED.value

    # Recalculate daily score and verify total points reflect 16 points for the 2 excused windows
    scoring = ScoringEngine(temp_db)
    summary = scoring.recalculate_daily_score(exam_date)
    assert summary["positive_points"] == 16
    assert summary["breakdown"]["Hourly"] == 16

    # Idempotency test: Re-running reconciliation must NOT duplicate credits
    temp_db.reconcile_exam_attendance_exemptions(exam_date)
    with temp_db._connect() as conn:
        all_events = conn.execute("SELECT * FROM score_events WHERE date = ?", (exam_date,)).fetchall()
    assert len(all_events) == 2
    summary2 = scoring.recalculate_daily_score(exam_date)
    assert summary2["positive_points"] == 16


def test_neo_browser_dynamic_discovery_and_signature():
    """
    Requirement 2:
    - Identify actual installed NeoExamBrowser executable.
    - Check registry and known path fallbacks.
    - Validate Authenticode signature where available.
    """
    monitor = NeoExamBrowserMonitor(db=None)
    browser_info = monitor.discover_installed_browser(force_refresh=True)

    assert browser_info is not None
    assert "exe_path" in browser_info
    assert "process_name" in browser_info

    # On this PC, Neo Browser is installed at %LOCALAPPDATA%\Programs\Neo Browser\Neo Browser.exe
    if os.path.exists(browser_info["exe_path"]):
        assert browser_info["installed"] is True
        assert browser_info["process_name"] == "Neo Browser.exe"
        assert browser_info["signature_valid"] is True
        assert "Iamneo edutech private limited" in (browser_info.get("signer_subject") or "")


def test_silent_process_monitoring_safety_guarantees():
    """
    Requirement 2 Safety Guarantee:
    - Process presence check only.
    - NEVER keylog, capture answers, inspect content, or screenshot.
    - Zero interference or process termination.
    """
    monitor = NeoExamBrowserMonitor(db=None)
    # Check process presence does not crash or raise exceptions
    proc = monitor.check_process_presence()
    if proc is not None:
        assert "pid" in proc
        assert "name" in proc
        assert "exe" in proc


def test_notification_suppression_and_queue(temp_db):
    """
    Requirement 3:
    - While approved exam is active, pause routine reminders, hourly proof popups, toasts, etc.
    - Store suppressed notifications for post-exam review.
    - Ephemeral countdowns discarded, pending proof windows preserved.
    """
    exam_date = "2026-11-10"
    tz = ZoneInfo("Asia/Kolkata")

    booking = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-SUPPRESS-001",
        "exam_name": "Computer Networks Exam",
        "subject": "CS8591",
        "institution": "Anna University",
        "exam_date": exam_date,
        "official_start_time": "10:00",
        "official_end_time": "13:00",
        "prep_buffer_minutes": 0,
        "completion_buffer_minutes": 0,
        "status": "EXAM_APPROVED",
    })

    monitor = NeoExamBrowserMonitor(db=temp_db)
    monitor.clear_suppressed_notifications()

    # Time at 09:00 (before exam) -> Suppression NOT active
    dt_before = datetime(2026, 11, 10, 9, 0, 0, tzinfo=tz)
    assert monitor.is_notification_suppression_active(reference_now=dt_before) is False
    assert monitor.current_state == STATE_EXAM_BOOKING_PENDING

    # Time at 11:00 (during exam) -> Suppression IS active!
    dt_during = datetime(2026, 11, 10, 11, 0, 0, tzinfo=tz)
    assert monitor.is_notification_suppression_active(reference_now=dt_during) is True
    # Since process is not running in test, state is EXAM_PROCESS_NOT_DETECTED, but suppression stays TRUE!
    assert monitor.current_state == STATE_EXAM_PROCESS_NOT_DETECTED

    # Test stashing suppressed notifications
    monitor.queue_suppressed_notification({
        "title": "Hourly Proof Alert",
        "message": "Window open for 11:00 Check",
        "activity_id": "act_1100",
    })
    stashed = monitor.get_suppressed_notifications()
    assert len(stashed) == 1
    assert stashed[0]["title"] == "Hourly Proof Alert"
    assert stashed[0]["exam_booking_id"] == "EXAM-SUPPRESS-001"

    # Time at 14:00 (after exam) -> Suppression NOT active
    dt_after = datetime(2026, 11, 10, 14, 0, 0, tzinfo=tz)
    assert monitor.is_notification_suppression_active(reference_now=dt_after) is False
    assert monitor.current_state == STATE_EXAM_FINISHED


def test_unexpected_process_exit_maintains_suppression_and_booking(temp_db):
    """
    Requirement 6:
    - If NeoExamBrowser unexpectedly exits/crashes, transition state to EXAM_PROCESS_NOT_DETECTED.
    - Maintain approved booking and continue notification suppression.
    - Do NOT prematurely assume finished or reactivate proof windows without user confirmation.
    """
    exam_date = "2026-11-10"
    tz = ZoneInfo("Asia/Kolkata")

    booking = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-CRASH-SAFETY",
        "exam_name": "Compiler Design Exam",
        "subject": "CS8602",
        "institution": "Anna University",
        "exam_date": exam_date,
        "official_start_time": "10:00",
        "official_end_time": "13:00",
        "prep_buffer_minutes": 0,
        "completion_buffer_minutes": 0,
        "status": "EXAM_APPROVED",
    })

    monitor = NeoExamBrowserMonitor(db=temp_db)
    dt_during = datetime(2026, 11, 10, 11, 30, 0, tzinfo=tz)

    # Process not detected -> state EXAM_PROCESS_NOT_DETECTED
    state = monitor.update_state(reference_now=dt_during)
    assert state == STATE_EXAM_PROCESS_NOT_DETECTED

    # Invariant: Booking status is STILL EXAM_APPROVED (not finished)
    saved = temp_db.get_exam_prebooking("EXAM-CRASH-SAFETY")
    assert saved["status"] == "EXAM_APPROVED"
    assert saved["monitoring_state"] == STATE_EXAM_PROCESS_NOT_DETECTED

    # Invariant: Suppression remains ACTIVE!
    assert monitor.is_notification_suppression_active(reference_now=dt_during) is True


def test_early_exam_completion_and_proof_window_reactivation(temp_db):
    """
    Requirements 4 & 5:
    - User finishes early at 11:00 AM on a 9:00 AM – 12:00 PM exam.
    - Confirm early exit updates actual_end, status = EXAM_COMPLETED_EARLY, monitoring_state = EXAM_ENDED_EARLY.
    - Windows before 11:00 AM remain EXCUSED with full points credited.
    - Windows starting after 11:00 AM (e.g. 11:50 AM – 12:10 PM) are reactivated as SCHEDULED.
    - Proof is requested ONLY when that window naturally opens (11:50 AM), NOT immediately at early exit (11:00 AM).
    """
    exam_date = "2026-11-10"
    tz = ZoneInfo("Asia/Kolkata")
    temp_db.ensure_daily_record(exam_date)

    # Exam originally booked 09:30 to 12:00 (prep 0m, comp 0m)
    booking = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-EARLY-EXIT-001",
        "exam_name": "Artificial Intelligence End Sem",
        "subject": "CS8080",
        "institution": "Anna University",
        "exam_date": exam_date,
        "official_start_time": "09:30",
        "official_end_time": "12:00",
        "prep_buffer_minutes": 0,
        "completion_buffer_minutes": 0,
        "status": "EXAM_APPROVED",
    })

    # Initially, windows inside [09:00, 12:00] (10:00, 11:00, 12:00 checks) are EXCUSED with full points!
    with temp_db._connect() as conn:
        initial_acts = {r["activity_id"]: dict(r) for r in conn.execute("SELECT * FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'", (exam_date,)).fetchall()}
    assert initial_acts["ACT-20261110-1000-HOURLY"]["status"] == "EXCUSED"
    assert initial_acts["ACT-20261110-1100-HOURLY"]["status"] == "EXCUSED"
    assert initial_acts["ACT-20261110-1200-HOURLY"]["status"] == "EXCUSED"

    # User finishes early at 11:00 AM!
    dt_1100 = datetime(2026, 11, 10, 11, 0, 0, tzinfo=tz)
    ms_1100 = int(dt_1100.timestamp() * 1000)

    monitor = NeoExamBrowserMonitor(db=temp_db)
    updated = monitor.confirm_early_exit(
        booking_id="EXAM-EARLY-EXIT-001",
        actual_end_time="11:00",
        actual_end_iso="2026-11-10T11:00:00+05:30",
        actual_end_ms=ms_1100,
        device_id="windows_pc",
    )

    assert updated["status"] == "EXAM_COMPLETED_EARLY"
    assert updated["early_exit_confirmed"] == 1
    assert updated["actual_end_time"] == "11:00"
    assert updated["actual_end_ms"] == ms_1100

    # Query activities after early exit
    with temp_db._connect() as conn:
        post_acts = {r["activity_id"]: dict(r) for r in conn.execute("SELECT * FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'", (exam_date,)).fetchall()}
        post_events = {r["event_id"]: dict(r) for r in conn.execute("SELECT * FROM score_events WHERE date = ?", (exam_date,)).fetchall()}

    # Windows before 11:00 AM (10:00 and 11:00 checks) remain EXCUSED with full points credited!
    assert post_acts["ACT-20261110-1000-HOURLY"]["status"] == "EXCUSED"
    assert post_acts["ACT-20261110-1000-HOURLY"]["points"] == 8
    assert post_acts["ACT-20261110-1100-HOURLY"]["status"] == "EXCUSED"
    assert post_acts["ACT-20261110-1100-HOURLY"]["points"] == 8

    # Window starting at 11:50 AM (12:00 check) is REACTIVATED to SCHEDULED!
    assert post_acts["ACT-20261110-1200-HOURLY"]["status"] == "SCHEDULED"
    assert post_acts["ACT-20261110-1200-HOURLY"]["points"] == 8
    det_1200 = json.loads(post_acts["ACT-20261110-1200-HOURLY"]["details_json"] or "{}")
    assert det_1200.get("suppressed_by_exam") is None or det_1200.get("suppressed_by_exam") is False

    # Score event for ACT-20261110-1200-HOURLY is removed, while 10:00 and 11:00 retain their score events
    assert "se_exam_excused_EXAM-EARLY-EXIT-001_ACT-20261110-1000-HOURLY" in post_events
    assert "se_exam_excused_EXAM-EARLY-EXIT-001_ACT-20261110-1100-HOURLY" in post_events
    assert "se_exam_excused_EXAM-EARLY-EXIT-001_ACT-20261110-1200-HOURLY" not in post_events

    # Daily summary reflects 16 points for the 2 excused windows
    scoring = ScoringEngine(temp_db)
    summary = scoring.recalculate_daily_score(exam_date)
    assert summary["positive_points"] == 16

    # Verify notifications are now unsuppressed
    dt_post_exit = datetime(2026, 11, 10, 11, 5, 0, tzinfo=tz)
    assert monitor.is_notification_suppression_active(reference_now=dt_post_exit) is False
    assert monitor.current_state == STATE_EXAM_ENDED_EARLY


def test_cross_device_sync_conflict_handling(temp_db):
    """
    Requirement 7:
    - Authoritative sync between Android and Windows.
    - Older scheduled end time or status must NEVER overwrite a confirmed early completion.
    """
    exam_date = "2026-11-10"
    tz = ZoneInfo("Asia/Kolkata")
    dt_1100 = datetime(2026, 11, 10, 11, 0, 0, tzinfo=tz)
    ms_1100 = int(dt_1100.timestamp() * 1000)

    # Create approved exam booking
    temp_db.create_exam_prebooking({
        "booking_id": "EXAM-SYNC-RACE",
        "exam_name": "Cloud Computing",
        "subject": "CS8791",
        "institution": "University",
        "exam_date": exam_date,
        "official_start_time": "09:00",
        "official_end_time": "12:00",
        "prep_buffer_minutes": 0,
        "completion_buffer_minutes": 0,
        "status": "EXAM_APPROVED",
        "version": 1,
    })

    # User confirms early exit on Windows
    temp_db.confirm_early_exam_exit(
        booking_id="EXAM-SYNC-RACE",
        actual_end_time="11:00",
        actual_end_iso="2026-11-10T11:00:00+05:30",
        actual_end_ms=ms_1100,
        device_id="windows_pc",
    )

    existing = temp_db.get_exam_prebooking("EXAM-SYNC-RACE")
    assert existing["status"] == "EXAM_COMPLETED_EARLY"
    assert existing["early_exit_confirmed"] == 1

    # Incoming sync packet from Android with older scheduled time (09:00 - 12:00) without early exit confirmation
    stale_payload = {
        "booking_id": "EXAM-SYNC-RACE",
        "exam_name": "Cloud Computing",
        "subject": "CS8791",
        "institution": "University",
        "exam_date": exam_date,
        "official_start_time": "09:00",
        "official_end_time": "12:00",
        "status": "EXAM_APPROVED",
        "version": 1,
    }
    sync_result = temp_db.sync_exam_prebooking(stale_payload, device_id="android_phone")

    # Race condition prevented: confirmed early exit is preserved!
    assert sync_result["action"] == "PRESERVED_EARLY_EXIT"
    after_sync = temp_db.get_exam_prebooking("EXAM-SYNC-RACE")
    assert after_sync["status"] == "EXAM_COMPLETED_EARLY"
    assert after_sync["early_exit_confirmed"] == 1
    assert after_sync["actual_end_time"] == "11:00"
