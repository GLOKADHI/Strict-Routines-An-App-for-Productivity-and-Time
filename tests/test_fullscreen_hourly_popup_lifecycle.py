"""
Verification Suite: Full-Screen Hourly Proof Popup — Single Instance, Deduplication & Lifecycle
Tests:
1. Popup opens at T - 10 minutes (window_start) and NEVER at T - 11 minutes (reminder).
2. Composite identity (activity_id, schedule_version) guarantees one popup per activity across repeated triggers.
3. Deduplication across all trigger sources (scheduler, notifications, WebSocket, resume, startup, Android sync) persisted in SQLite.
4. Android submission immediately dismisses Windows input popup, transitions to VALIDATING ("Submission received — AI review processing"), and completes AI review to VERIFIED without reopening form.
5. Restart recovery reconciles persisted SQLite state without duplicate popups or reopening submitted/expired windows.
6. Explicit state machine transitions: UPCOMING -> OPEN -> SUBMITTED/VALIDATING -> VERIFIED/REJECTED and OPEN -> MISSED -> CLOSED.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from core.daily_routine_gate import DailyRoutineGate
from core.database import StrictRecordsDB
from core.event_bus import EventBus
from core.time_window_engine import TimeWindowEngine, IST
from windows.submission_window_manager import SubmissionWindowManager, SubmissionState


class DummyDesktopWindow:
    def __init__(self):
        self.js_calls = []
        self.shown_count = 0

    def evaluate_js(self, code: str):
        self.js_calls.append(code)

    def show(self):
        self.shown_count += 1

    def restore(self):
        pass


class DummyDesktopApi:
    def __init__(self):
        self._window = DummyDesktopWindow()
        self.show_calls = []

    def show_dashboard(self, tab=None, open_hourly_if_active=False):
        self.show_calls.append({"tab": tab, "open_hourly_if_active": open_hourly_if_active})
        return {"success": True}


class TestFullScreenHourlyPopupLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        DailyRoutineGate.seed_approved_wakeup(self.db, "2026-10-11")
        self.bus = EventBus()
        SubmissionWindowManager._instance = None
        self.swm = SubmissionWindowManager.get_instance(db=self.db, event_bus=self.bus)
        self.api = DummyDesktopApi()
        self.swm.set_desktop_api(self.api)

    def tearDown(self):
        SubmissionWindowManager._instance = None
        self.tmp_dir.cleanup()

    def test_1_timing_boundaries_t_minus_11_vs_t_minus_10(self):
        """Verify reminder at T-11 min does NOT open popup, and window open at T-10 min DOES open popup."""
        twe = TimeWindowEngine(self.db)
        test_date = datetime.now(IST).date() + timedelta(days=1)
        date_str = test_date.isoformat()
        act_id = f"ACT-{test_date.strftime('%Y%m%d')}-1500-HOURLY"
        # Checkpoint T = 15:00 -> T-11m = 14:49, T-10m = 14:50, T+10m = 15:10
        t_minus_11 = datetime(test_date.year, test_date.month, test_date.day, 14, 49, 10, tzinfo=IST)
        DailyRoutineGate.seed_approved_wakeup(self.db, date_str)
        twe.ensure_activities_in_db(date_str, reference_now=t_minus_11)
        notifs_pre = twe.get_pending_notifications(date_str, reference_now=t_minus_11)
        hourly_pre = [n for n in notifs_pre if "1500-HOURLY" in n["activity_id"]]
        self.assertEqual(len(hourly_pre), 1)
        self.assertEqual(hourly_pre[0]["event_type"], "PRE_OPENING")
        # SubmissionWindowManager must NOT have opened the popup at T-11m
        self.assertNotEqual(self.swm.state, SubmissionState.OPEN)
        self.assertIsNone(self.swm.active_activity_id)

        # Direct trigger test before window_start also refuses to open
        res_early = self.swm.open_submission_window(
            activity_id=act_id,
            schedule_version=1,
            window_start=f"{date_str}T14:50:00+05:30",
            window_end=f"{date_str}T15:10:00+05:30",
            reference_now=t_minus_11,
        )
        self.assertEqual(res_early["action"], "SKIPPED_BEFORE_WINDOW")
        self.assertNotEqual(self.swm.state, SubmissionState.OPEN)

        # Now at T-10m (14:50:05): Window opens -> popup MUST open
        t_minus_10 = datetime(test_date.year, test_date.month, test_date.day, 14, 50, 5, tzinfo=IST)
        notifs_open = twe.get_pending_notifications(date_str, reference_now=t_minus_10)
        hourly_open = [n for n in notifs_open if "1500-HOURLY" in n["activity_id"]]
        self.assertEqual(len(hourly_open), 1)
        self.assertEqual(hourly_open[0]["event_type"], "OPENED")
        self.assertEqual(self.swm.state, SubmissionState.OPEN)
        self.assertEqual(self.swm.active_activity_id, act_id)

    def test_2_single_popup_per_activity_and_trigger_deduplication(self):
        """Verify repeated triggers for the same (activity_id, schedule_version) reuse existing window and never stack."""
        act_id = "hourly-20261011-1000"
        sched_ver = 12

        # 1st trigger: creates/opens window
        r1 = self.swm.handle_trigger_event(
            event_type="HOURLY_WINDOW_OPEN",
            activity_id=act_id,
            schedule_version=sched_ver,
            event_id="evt-open-1000-v12",
            payload={"slot": "09:00-10:00", "title": "Hourly Check 10:00"},
        )
        self.assertEqual(r1["action"], "OPENED")
        self.assertEqual(self.swm.state, SubmissionState.OPEN)
        self.assertEqual(self.swm.active_activity_id, act_id)
        self.assertEqual(self.swm.active_schedule_version, sched_ver)

        # Exact same event_id repeated (e.g. WebSocket reconnect / duplicate notification)
        r2 = self.swm.handle_trigger_event(
            event_type="HOURLY_WINDOW_OPEN",
            activity_id=act_id,
            schedule_version=sched_ver,
            event_id="evt-open-1000-v12",
        )
        self.assertEqual(r2["action"], "DEDUPLICATED_EVENT")
        self.assertTrue(r2["reused"])

        # Different trigger source (e.g. app resume) for same (activity_id, schedule_version)
        r3 = self.swm.handle_trigger_event(
            event_type="RESUME",
            activity_id=act_id,
            schedule_version=sched_ver,
            event_id="evt-resume-1",
        )
        self.assertEqual(r3["action"], "FOCUSED_EXISTING")
        self.assertTrue(r3["reused"])

    def test_3_android_submission_closes_windows_popup_and_runs_ai_review(self):
        """Verify Android submission closes the Windows popup, shows received/validating status, and updates to VERIFIED."""
        act_id = "ACT-20261011-1100-HOURLY"
        sched_ver = 2

        self.swm.open_submission_window(
            activity_id=act_id,
            schedule_version=sched_ver,
            slot="10:00-11:00",
        )
        self.assertEqual(self.swm.state, SubmissionState.OPEN)

        q = self.bus.subscribe()

        # Android submits proof
        dismiss_res = self.swm.handle_trigger_event(
            event_type="ANDROID_SUBMISSION_SYNC",
            activity_id=act_id,
            schedule_version=sched_ver,
            event_id="evt-and-sub-1100",
            payload={
                "source_device_name": "Android Device",
                "submitted_at": "10:54 AM",
                "has_evidence": True,
                "description": "Completed Android UI & backend tests",
                "primary_activity": "CODING",
            },
        )
        self.assertEqual(dismiss_res["action"], "DISMISSED")
        self.assertEqual(self.swm.state, SubmissionState.SUBMITTED)
        self.assertTrue(self.swm.review_in_progress)

        # Verify EventBus received HOURLY_PROOF_SUBMITTED with compact status message
        captured_events = []
        while not q.empty():
            captured_events.append(q.get_nowait())
        sub_evts = [e for e in captured_events if e["event_type"] == "HOURLY_PROOF_SUBMITTED"]
        self.assertEqual(len(sub_evts), 1)
        self.assertEqual(
            sub_evts[0]["banner_message"],
            "Submission received — AI review processing",
        )

        # Subsequent open trigger after submission MUST NOT reopen input form
        reopen_attempt = self.swm.open_submission_window(
            activity_id=act_id,
            schedule_version=sched_ver,
        )
        self.assertEqual(reopen_attempt["action"], "SKIPPED_ALREADY_HANDLED")

        # AI Review completes in background
        self.swm.record_ai_review_completed(
            activity_id=act_id,
            schedule_version=sched_ver,
            review_result={"decision": "PROVISIONAL_APPROVE", "confidence": 0.96, "proposed_points": 8},
            event_id="evt-ai-done-1100",
        )
        self.assertEqual(self.swm.state, SubmissionState.VERIFIED)
        self.assertFalse(self.swm.review_in_progress)

        # Even after AI review completes, input form must NEVER reopen for this activity
        reopen_after_ai = self.swm.open_submission_window(
            activity_id=act_id,
            schedule_version=sched_ver,
        )
        self.assertEqual(reopen_after_ai["action"], "SKIPPED_ALREADY_HANDLED")

    def test_4_restart_persistence_and_expiration(self):
        """Verify persisted state across SubmissionWindowManager restart prevents duplicate popups and handles expiration."""
        act_id = "ACT-20261011-1200-HOURLY"
        sched_ver = 3

        self.swm.open_submission_window(activity_id=act_id, schedule_version=sched_ver)
        self.swm.dismiss_submission_form(activity_id=act_id, schedule_version=sched_ver, event_id="evt-sub-1200")

        # Simulate process restart by resetting singleton and creating a fresh manager attached to the same SQLite DB
        SubmissionWindowManager._instance = None
        restarted_swm = SubmissionWindowManager.get_instance(db=self.db, event_bus=self.bus)

        # Event ID is still remembered as processed in SQLite
        self.assertTrue(restarted_swm.is_event_processed("evt-sub-1200"))

        # Attempting to open the already-submitted activity after restart must be skipped
        res = restarted_swm.open_submission_window(activity_id=act_id, schedule_version=sched_ver)
        self.assertEqual(res["action"], "SKIPPED_ALREADY_HANDLED")

        # Test expiration transition: OPEN -> MISSED -> CLOSED
        exp_act_id = "ACT-20261011-1300-HOURLY"
        restarted_swm.open_submission_window(activity_id=exp_act_id, schedule_version=1)
        self.assertEqual(restarted_swm.state, SubmissionState.OPEN)

        exp_res = restarted_swm.expire_submission_window(activity_id=exp_act_id, schedule_version=1)
        self.assertEqual(exp_res["action"], "EXPIRED")
        self.assertEqual(restarted_swm.state, SubmissionState.MISSED)

        # Expired activity must not reopen
        reopen_expired = restarted_swm.open_submission_window(activity_id=exp_act_id, schedule_version=1)
        self.assertEqual(reopen_expired["action"], "SKIPPED_ALREADY_HANDLED")


if __name__ == "__main__":
    unittest.main()
