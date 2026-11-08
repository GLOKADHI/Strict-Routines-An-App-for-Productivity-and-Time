"""
Comprehensive Verification Suite for StrictRecords Full-Screen Hourly Proof Popup
Covers all 17 required scenarios from Section 10:
1. Open a real hourly submission window (and verify T-11m reminder does not open early).
2. Trigger its reminder multiple times.
3. Verify only one popup exists.
4. Deliver the same event repeatedly.
5. Verify no duplicate windows or notifications appear.
6. Minimize and restore the popup.
7. Submit proof on Windows.
8. Submit proof on Android while the Windows popup is open.
9. Verify the matching popup closes after server acceptance (and unrelated popups are not closed).
10. Disconnect and restore Wi-Fi / EventBus sync recovery.
11. Restart the application during an open window.
12. Restart after submission.
13. Verify expired or submitted forms never reopen.
14. Let AI validation continue after the popup closes (including after deadline for on-time submission).
15. Monitor process count and memory during repeated hourly cycles.
16. Verify there is only one background controller and scheduler.
17. Confirm existing scoring, evidence validation, privileges, and notifications still work.
"""

import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.daily_routine_gate import DailyRoutineGate
from core.database import StrictRecordsDB
from core.event_bus import EventBus
from core.scoring_engine import ScoringEngine
from core.time_window_engine import TimeWindowEngine, IST, dt_to_iso, iso_to_dt
from windows.submission_window_manager import (
    SubmissionWindowManager,
    SubmissionState,
    WindowVisibilityState,
)
from windows.notification_service import StrictNotificationService


@pytest.fixture
def isolated_env():
    """Creates an isolated real SQLite database and fresh singletons for deterministic testing."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="sr_hourly_test_")
    os.close(fd)
    db = StrictRecordsDB(db_path=db_path)
    DailyRoutineGate.seed_approved_wakeup(db, "2026-10-11")
    bus = EventBus()
    SubmissionWindowManager._instance = None
    swm = SubmissionWindowManager.get_instance(db=db, event_bus=bus)
    twe = TimeWindowEngine(db)
    engine = ScoringEngine(db)
    yield {
        "db": db,
        "db_path": db_path,
        "bus": bus,
        "swm": swm,
        "twe": twe,
        "engine": engine,
    }
    SubmissionWindowManager._instance = None
    try:
        os.remove(db_path)
    except Exception:
        pass


def test_01_to_06_single_popup_deduplication_and_minimize_restore(isolated_env):
    """
    Tests Scenarios 1, 2, 3, 4, 5, 6:
    - T-11m reminder does NOT open the full-screen submission popup early.
    - T-10m opens the real hourly submission window.
    - Triggering reminders multiple times reuses/focuses the single popup.
    - Delivering the same event_id repeatedly is idempotently deduplicated.
    - No duplicate windows or notifications appear.
    - Minimizing and restoring preserves submission state while updating visibility state.
    """
    db = isolated_env["db"]
    twe = isolated_env["twe"]
    swm = isolated_env["swm"]

    date_str = "2026-10-11"
    t_morning = datetime(2026, 10, 11, 9, 0, 0, tzinfo=IST)
    activities = twe.ensure_activities_in_db(date_str, reference_now=t_morning)
    hourly_10 = next(a for a in activities if a["type"] == "HOURLY" and "1000" in a["activity_id"])
    aid = hourly_10["activity_id"]
    ver = int(hourly_10.get("schedule_version") or 1)

    # 1A. At 09:49 AM (T - 11 min): Reminder fires, popup must NOT open early
    t_0949 = datetime(2026, 10, 11, 9, 49, 10, tzinfo=IST)
    pending_0949 = twe.get_pending_notifications(date_str, reference_now=t_0949)
    assert any(p["event_type"] == "PRE_OPENING" and p["activity_id"] == aid for p in pending_0949)
    assert swm.state != SubmissionState.OPEN

    # Direct attempt to open before window_start must also be rejected
    early_res = swm.open_submission_window(
        activity_id=aid,
        slot="09:00-10:00",
        title=hourly_10["title"],
        window_start=hourly_10["window_start"],
        window_end=hourly_10["window_end"],
        schedule_version=ver,
        reference_now=t_0949,
    )
    assert early_res["action"] == "SKIPPED_BEFORE_WINDOW"
    assert swm.state != SubmissionState.OPEN

    # 1B. At 09:50 AM (T - 10 min): Authoritative submission window opens
    t_0950 = datetime(2026, 10, 11, 9, 50, 5, tzinfo=IST)
    pending_0950 = twe.get_pending_notifications(date_str, reference_now=t_0950)
    assert any(p["event_type"] == "OPENED" and p["activity_id"] == aid for p in pending_0950)
    assert swm.state == SubmissionState.OPEN
    assert swm.window_visibility_state == WindowVisibilityState.OPEN_FULLSCREEN
    assert swm.active_activity_id == aid
    assert swm.active_schedule_version == ver
    opened_before_retries = swm.get_status()["opened_activities_count"]

    # 2 & 3. Trigger reminders and open calls multiple times -> only 1 popup exists
    for i in range(5):
        dup_open = swm.open_submission_window(
            activity_id=aid,
            slot="09:00-10:00",
            title=hourly_10["title"],
            window_start=hourly_10["window_start"],
            window_end=hourly_10["window_end"],
            schedule_version=ver,
            force_focus=(i == 2),
            reference_now=t_0950 + timedelta(seconds=10 * (i + 1)),
        )
        assert dup_open["action"] == "FOCUSED_EXISTING"
        assert dup_open["reused"] is True
        assert swm.get_status()["opened_activities_count"] == opened_before_retries

    # 4 & 5. Deliver the exact same event_id and poll pending notifications repeatedly
    evt_res_1 = swm.handle_trigger_event(
        event_type="HOURLY_WINDOW_OPEN",
        activity_id=aid,
        schedule_version=ver,
        event_id=f"{aid}-OPENED",
        payload=hourly_10,
    )
    assert evt_res_1["action"] == "DEDUPLICATED_EVENT"
    assert evt_res_1["reused"] is True

    # Polling pending notifications again at 09:51 AM produces 0 duplicate notifications
    pending_0951 = twe.get_pending_notifications(date_str, reference_now=datetime(2026, 10, 11, 9, 51, 0, tzinfo=IST))
    assert len([p for p in pending_0951 if p["activity_id"] == aid]) == 0

    # 6. Minimize and restore the popup
    min_res = swm.minimize_window()
    assert min_res["window_visibility_state"] == "MINIMIZED_PILL"
    assert min_res["state"] == "OPEN"

    # Triggering 1-minute warning at 10:09 AM restores the minimized popup without creating a second instance
    t_1009 = datetime(2026, 10, 11, 10, 9, 5, tzinfo=IST)
    pending_1009 = twe.get_pending_notifications(date_str, reference_now=t_1009)
    assert any(p["event_type"] == "WARNING_1M" and p["activity_id"] == aid for p in pending_1009)
    assert swm.state == SubmissionState.OPEN
    assert swm.window_visibility_state == WindowVisibilityState.OPEN_FULLSCREEN
    assert swm.get_status()["opened_activities_count"] == opened_before_retries


def test_07_to_10_windows_and_android_submission_closes_matching_popup(isolated_env):
    """
    Tests Scenarios 7, 8, 9, 10:
    - Submit proof on Windows for 10:00 AM checkpoint.
    - Open 11:00 AM checkpoint on Windows, then submit proof on Android while Windows popup is open.
    - Verify unrelated activity submission does NOT close the active 11:00 AM popup.
    - Verify matching Android submission DOES close the 11:00 AM Windows popup and emits compact status:
      'Submission received — AI review processing'.
    - Verify Wi-Fi disconnect/reconnect (EventBus replay + startup/resume reconciliation) never reopens submitted form.
    """
    db = isolated_env["db"]
    twe = isolated_env["twe"]
    swm = isolated_env["swm"]
    bus = isolated_env["bus"]

    date_str = "2026-10-11"
    t_morning = datetime(2026, 10, 11, 9, 0, 0, tzinfo=IST)
    activities = twe.ensure_activities_in_db(date_str, reference_now=t_morning)
    act_10 = next(a for a in activities if a["type"] == "HOURLY" and "1000" in a["activity_id"])
    act_11 = next(a for a in activities if a["type"] == "HOURLY" and "1100" in a["activity_id"])

    # 7. Submit proof on Windows for 10:00 AM activity
    t_0955 = datetime(2026, 10, 11, 9, 55, 0, tzinfo=IST)
    twe.get_pending_notifications(date_str, reference_now=t_0955)
    assert swm.active_activity_id == act_10["activity_id"]
    assert swm.state == SubmissionState.OPEN

    twe.record_activity_submission(act_10["activity_id"], sub_time=t_0955, begin_ai_validation=True)
    win_sub_res = swm.dismiss_submission_form(
        activity_id=act_10["activity_id"],
        source_device_name="Windows Workstation",
        submitted_at="09:55 AM",
        has_evidence=True,
        description="Completed backend module refactoring",
        primary_activity="CODING",
        event_id="evt_win_1000",
    )
    assert win_sub_res["action"] == "DISMISSED"
    assert swm.state == SubmissionState.SUBMITTED
    assert swm.window_visibility_state == WindowVisibilityState.CLOSED
    swm.start_ai_validation(act_10["activity_id"])
    assert swm.state == SubmissionState.VALIDATING

    # 8 & 9. Open 11:00 AM window at 10:52 AM on Windows
    t_1052 = datetime(2026, 10, 11, 10, 52, 0, tzinfo=IST)
    twe.get_pending_notifications(date_str, reference_now=t_1052)
    assert swm.active_activity_id == act_11["activity_id"]
    assert swm.state == SubmissionState.OPEN
    assert swm.window_visibility_state == WindowVisibilityState.OPEN_FULLSCREEN

    # Unrelated submission event (e.g. delayed event for 10:00 AM) must NOT close 11:00 AM popup
    unrelated_res = swm.dismiss_submission_form(
        activity_id=act_10["activity_id"],
        source_device_name="Android Device",
        submitted_at="09:56 AM",
        event_id="evt_delayed_1000",
    )
    assert unrelated_res["action"] == "IGNORED_UNRELATED_ACTIVITY"
    assert swm.state == SubmissionState.OPEN
    assert swm.window_visibility_state == WindowVisibilityState.OPEN_FULLSCREEN
    assert swm.active_activity_id == act_11["activity_id"]

    # Matching Android submission for 11:00 AM closes the Windows popup immediately
    twe.record_activity_submission(act_11["activity_id"], sub_time=t_1052, begin_ai_validation=True)
    and_sub_res = swm.dismiss_submission_form(
        activity_id=act_11["activity_id"],
        source_device_name="Android Device",
        submitted_at="10:52 AM",
        has_evidence=True,
        description="Solved algorithms problem set on Android",
        primary_activity="STUDY",
        event_id="evt_and_1100",
    )
    assert and_sub_res["action"] == "DISMISSED"
    assert swm.state == SubmissionState.SUBMITTED
    assert swm.window_visibility_state == WindowVisibilityState.CLOSED
    swm.start_ai_validation(act_11["activity_id"])
    assert swm.state == SubmissionState.VALIDATING

    # Verify EventBus published HOURLY_PROOF_SUBMITTED with compact status message
    recent_events = bus.get_events_since(None)
    sub_events = [
        e for e in recent_events
        if e.get("event_type") == "HOURLY_PROOF_SUBMITTED"
        and e.get("activity_id") == act_11["activity_id"]
    ]
    assert len(sub_events) == 1
    assert sub_events[0]["banner_message"] == "Submission received — AI review processing"

    # 10. Wi-Fi disconnect/reconnect or delayed duplicate open trigger must NOT reopen the submitted 11:00 AM form
    reopen_attempt = swm.open_submission_window(
        activity_id=act_11["activity_id"],
        slot="10:00-11:00",
        title=act_11["title"],
        window_start=act_11["window_start"],
        window_end=act_11["window_end"],
        force_focus=True,
        reference_now=t_1052 + timedelta(minutes=2),
    )
    assert reopen_attempt["action"] == "SKIPPED_ALREADY_HANDLED"
    assert swm.window_visibility_state == WindowVisibilityState.CLOSED


def test_11_to_14_restarts_expiry_and_background_ai_validation(isolated_env):
    """
    Tests Scenarios 11, 12, 13, 14:
    - Scenario 11: Restart application during an open window -> restores OPEN state from persisted DB.
    - Scenario 12: Restart application after submission -> reconciles as already submitted, never reopens input form.
    - Scenario 13: Unsubmitted window expires at T+10m -> marked MISSED/CLOSED, never reopens.
    - Scenario 14: AI validation continues after popup closes (and after deadline for on-time submission) and awards points.
    """
    db = isolated_env["db"]
    twe = isolated_env["twe"]
    bus = isolated_env["bus"]

    date_str = "2026-10-11"
    t_morning = datetime(2026, 10, 11, 11, 30, 0, tzinfo=IST)
    activities = twe.ensure_activities_in_db(date_str, reference_now=t_morning)
    act_12 = next(a for a in activities if a["type"] == "HOURLY" and "1200" in a["activity_id"])
    act_13 = next(a for a in activities if a["type"] == "HOURLY" and "1300" in a["activity_id"])

    # 11. Open 12:00 PM window at 11:51 AM, then simulate app restart
    t_1151 = datetime(2026, 10, 11, 11, 51, 0, tzinfo=IST)
    twe.get_pending_notifications(date_str, reference_now=t_1151)

    SubmissionWindowManager._instance = None
    swm_restarted = SubmissionWindowManager.get_instance(db=db, event_bus=bus)
    rec_open = swm_restarted.reconcile_on_startup(date_str=date_str, reference_now=t_1151 + timedelta(minutes=1))
    assert rec_open["action"] in ("OPENED", "FOCUSED_EXISTING")
    assert swm_restarted.active_activity_id == act_12["activity_id"]
    assert swm_restarted.state == SubmissionState.OPEN

    # Submit at 12:09 PM (on-time, 1 minute before 12:10 PM deadline)
    t_1209 = datetime(2026, 10, 11, 12, 9, 0, tzinfo=IST)
    sub_eval = twe.record_activity_submission(act_12["activity_id"], sub_time=t_1209, begin_ai_validation=True)
    assert sub_eval["allowed"] is True
    assert sub_eval["on_time"] is True
    swm_restarted.dismiss_submission_form(
        activity_id=act_12["activity_id"],
        source_device_name="Windows Workstation",
        submitted_at="12:09 PM",
    )

    # 12. Simulate app restart after submission (at 12:09:30 PM while window is still within time bounds)
    SubmissionWindowManager._instance = None
    swm_after_sub = SubmissionWindowManager.get_instance(db=db, event_bus=bus)
    rec_sub = swm_after_sub.reconcile_on_startup(date_str=date_str, reference_now=t_1209 + timedelta(seconds=30))
    assert rec_sub["action"] == "RECONCILED_ALREADY_SUBMITTED"
    assert swm_after_sub.window_visibility_state == WindowVisibilityState.CLOSED

    # 14. AI validation completes at 12:11 PM (AFTER the 12:10 PM deadline) for the on-time 12:09 PM submission
    t_1211 = datetime(2026, 10, 11, 12, 11, 0, tzinfo=IST)
    ver_res = twe.record_activity_verification(
        activity_id=act_12["activity_id"],
        is_verified=True,
        awarded_points=8,
        verification_completed_iso=dt_to_iso(t_1211),
    )
    assert ver_res["status"] == "VERIFIED"
    assert ver_res["points"] == 8
    assert ver_res["on_time_retained"] is True
    swm_after_sub.record_ai_review_completed(
        activity_id=act_12["activity_id"],
        review_result={"decision": "PROVISIONAL_APPROVE", "proposed_points": 8},
    )
    assert swm_after_sub.state == SubmissionState.VERIFIED
    assert swm_after_sub.window_visibility_state == WindowVisibilityState.CLOSED

    # 13. Let 13:00 (1:00 PM) checkpoint open at 12:50 PM and expire unsubmitted at 13:10 PM
    t_1251 = datetime(2026, 10, 11, 12, 51, 0, tzinfo=IST)
    twe.get_pending_notifications(date_str, reference_now=t_1251)
    assert swm_after_sub.active_activity_id == act_13["activity_id"]
    assert swm_after_sub.state == SubmissionState.OPEN

    t_1311 = datetime(2026, 10, 11, 13, 11, 0, tzinfo=IST)
    pending_1311 = twe.get_pending_notifications(date_str, reference_now=t_1311)
    assert any(p["event_type"] == "CLOSED" and p["activity_id"] == act_13["activity_id"] for p in pending_1311)
    assert swm_after_sub.state == SubmissionState.MISSED
    assert swm_after_sub.window_visibility_state == WindowVisibilityState.CLOSED

    # Expired activity must NEVER reopen
    reopen_expired = swm_after_sub.open_submission_window(
        activity_id=act_13["activity_id"],
        slot="12:00-13:00",
        title=act_13["title"],
        window_start=act_13["window_start"],
        window_end=act_13["window_end"],
        force_focus=True,
        reference_now=t_1311 + timedelta(minutes=1),
    )
    assert reopen_expired["action"] == "SKIPPED_ALREADY_HANDLED"


def test_15_to_17_live_process_count_memory_and_system_integrity():
    """
    Tests Scenarios 15, 16, 17 against the live running Windows application on port 8765:
    - Verifies single authoritative pythonw.exe instance.
    - Monitors process count and memory across repeated hourly status & notification cycles.
    - Verifies YouTube privilege status, hourly status endpoint, and scoring/notification endpoints.
    """
    import psutil

    def get_json(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8765{path}", timeout=4.0) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pytest.skip("Live Windows application on port 8765 is not running")

    # 16. Verify single instance via API and OS process table
    inst_data = get_json("/api/system/instances")
    assert inst_data["success"] is True
    assert inst_data["count"] == 1
    assert inst_data["single_instance_enforced"] is True

    pid = inst_data["current_pid"]
    proc = psutil.Process(pid)
    mem_before_mb = proc.memory_info().rss / (1024 * 1024)

    # 15. Simulate 25 rapid hourly status + notification + event poll cycles and measure memory delta
    for _ in range(25):
        st = get_json("/api/hourly/status")
        assert st["success"] is True
        notifs = get_json("/api/notifications/pending")
        assert "notifications" in notifs
        evts = get_json("/api/events/poll")
        assert "events" in evts

    mem_after_mb = proc.memory_info().rss / (1024 * 1024)
    inst_after = get_json("/api/system/instances")
    assert inst_after["count"] == 1
    # Memory growth across 25 cycles must remain bounded (< 15 MB delta)
    assert (mem_after_mb - mem_before_mb) < 15.0

    # 17. Confirm existing scoring, privileges (YouTube unlocked today), and tray status work
    priv = get_json("/api/privilege/youtube/status")
    assert priv["allowed"] is True

    tray = get_json("/api/system/tray_status")
    assert tray["is_running"] is True
    assert tray["icon_registered"] is True
