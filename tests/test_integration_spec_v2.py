"""
Test Suite for StrictRecords Integration Update:
Cross-Device Notifications + Dynamic Wake-Up + Hourly Proof + Shared YouTube Privileges
(Specifications 1 through 18)
"""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from http.server import HTTPServer

from core.database import StrictRecordsDB as Database
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler
from core.time_window_engine import IST, TimeWindowEngine
from core.wakeup_state_machine import WakeUpStateMachine, WakeUpPlaybackSession, WakeUpState
from core.privilege_engine import PrivilegeEngine
from core.daily_routine_gate import DailyRoutineGate
from windows.notification_service import (
    StrictNotificationService,
    NotificationCenter,
    CATEGORY_TASKS,
    CATEGORY_PROOF,
    CATEGORY_POINTS,
    CATEGORY_PRIVILEGES,
    CATEGORY_FOCUS,
    CATEGORY_ANDROID,
    CATEGORY_SYNC,
    SENSITIVE_APP_PACKAGES,
)


class TestIntegrationSpecV2(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_v2.db")
        self.db = Database(self.db_path)
        self.engine = ScoringEngine(self.db)
        self.tw = self.engine.tw_engine
        self.today = "2026-10-04"

    def tearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    # ── 1. Dynamic Wake-up Window & Early Submission ────────────────────────
    def test_01_dynamic_wakeup_window_boundaries(self):
        """Wake-up window is open 07:00 AM - 10:00 AM IST, allowing early completion."""
        ref_0730 = datetime.strptime(f"{self.today} 07:30:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        acts = self.tw.ensure_activities_in_db(self.today, wakeup_time="08:30", reference_now=ref_0730)
        wake_act = next(a for a in acts if "WAKEUP" in a["activity_id"])
        
        # The restored morning proof period closes at 09:30.
        self.assertEqual(wake_act["window_start"], f"{self.today}T07:00:00+05:30")
        self.assertEqual(wake_act["window_end"], f"{self.today}T09:30:00+05:30")
        details = json.loads(wake_act.get("details_json") or "{}")
        self.assertTrue(details.get("early_completion_permitted"))

        # Early submission at 07:30 AM (when target was 08:30) is ALLOWED
        res_early = self.tw.evaluate_submission_timing(
            wake_act["activity_id"],
            submission_iso=f"{self.today}T07:30:00+05:30"
        )
        self.assertTrue(res_early["allowed"])

        # Submission before 07:00 AM (06:50 AM) is REJECTED (TOO_EARLY)
        res_before = self.tw.evaluate_submission_timing(
            wake_act["activity_id"],
            submission_iso=f"{self.today}T06:50:00+05:30"
        )
        self.assertFalse(res_before["allowed"])
        self.assertEqual(res_before["status"], "TOO_EARLY")

        # Submission after 10:00 AM (10:01 AM) is REJECTED (REJECTED_LATE)
        res_after = self.tw.evaluate_submission_timing(
            wake_act["activity_id"],
            submission_iso=f"{self.today}T10:01:00+05:30"
        )
        self.assertFalse(res_after["allowed"])
        self.assertEqual(res_after["status"], "REJECTED_LATE")

    # ── 2. Wake-Up Checklist State Machine & Playback Session ───────────────
    def test_02_wakeup_state_machine_pipeline_and_checklist(self):
        """Verifies state transitions and checklist reporting in WakeUpStateMachine."""
        self.engine.trigger_wakeup_alarm(self.today)

        sm = WakeUpStateMachine(db=self.db, date_str=self.today)
        chk = sm.get_checklist_state()
        self.assertEqual(chk["date"], self.today)
        self.assertEqual(len(chk["checklist"]), 7)
        self.assertFalse(chk["is_completed"])

        # Update state along the pipeline
        sm.update_state(WakeUpState.PROOF_SUBMITTED)
        self.assertEqual(sm.get_checklist_state()["state"], "PROOF_SUBMITTED")

        sm.update_state(WakeUpState.VALIDATING)
        self.assertEqual(sm.get_checklist_state()["state"], "VALIDATING")

        sm.update_state(WakeUpState.HEADSET_CHECK)
        self.assertEqual(sm.get_checklist_state()["state"], "HEADSET_CHECK")

        sm.update_state(WakeUpState.PLAYBACK_REQUIRED)
        self.assertEqual(sm.get_checklist_state()["state"], "PLAYBACK_REQUIRED")

        sm.update_state(WakeUpState.PLAYBACK_VALIDATION)
        self.assertEqual(sm.get_checklist_state()["state"], "PLAYBACK_VALIDATION")

    def test_03_wakeup_playback_anti_seek_volume_headset(self):
        """Verifies genuine playback telemetry, anti-seek bypass detection, and volume limits."""
        session = WakeUpPlaybackSession(
            session_id="test_sess_01",
            track_name="Discipline Affirmations",
            track_duration_sec=60.0,
            max_volume_pct=60.0,
        )

        # 1. Start playback with Bluetooth route at 50% volume
        session.start(
            headset_name="Sony WH-1000XM4",
            audio_route="BLUETOOTH_A2DP",
            initial_volume_pct=50.0,
        )
        self.assertTrue(session.is_playing)
        self.assertTrue(session.headset_connected)
        self.assertTrue(session.to_dict()["volume_ok"])

        # 2. Volume > 60% violation detection
        session.update_progress(5.0, volume_pct=75.0)
        stat_vol = session.to_dict()
        self.assertFalse(stat_vol["volume_ok"])
        self.assertGreater(stat_vol["volume_violations"], 0)

        # Restore volume to <= 60%
        session.update_progress(10.0, volume_pct=55.0)
        self.assertTrue(session.to_dict()["volume_ok"])

        # 3. Anti-Seek Bypass: trying to jump from 10s to 55s instantly
        session.update_progress(55.0)
        stat_seek = session.to_dict()
        self.assertGreater(stat_seek["seek_attempts"], 0)
        self.assertLess(stat_seek["completed_duration_sec"], 20.0)
        self.assertFalse(stat_seek["completed"])

        # 4. Headset Disconnection handling
        session.on_headset_disconnected()
        stat_disc = session.to_dict()
        self.assertFalse(stat_disc["headset_connected"])
        self.assertTrue(stat_disc["is_paused"])

    # ── 3. Dynamic Hourly Schedule Anchoring ─────────────────────────────────
    def test_04_hourly_schedule_anchored_to_wakeup(self):
        """Verifies hourly checkpoints start at max(8, wakeup_hour + 1) without retroactive penalties."""
        # Case A: Wake up at 07:00 -> hourly slots start at 08:00 AM
        acts_07 = self.tw.generate_daily_schedule(self.today, wakeup_time="07:00")
        hourly_07 = [a for a in acts_07 if a["type"] == "HOURLY"]
        self.assertEqual(hourly_07[0]["scheduled_time"], f"{self.today}T07:00:00+05:30")
        self.assertEqual(hourly_07[0]["window_start"], f"{self.today}T06:50:00+05:30")
        self.assertEqual(hourly_07[0]["window_end"], f"{self.today}T07:10:00+05:30")

        # Case B: Wake up at 08:30 -> hourly slots start at 09:00 AM (8 + 1)
        acts_0830 = self.tw.generate_daily_schedule(self.today, wakeup_time="08:30")
        hourly_0830 = [a for a in acts_0830 if a["type"] == "HOURLY"]
        self.assertEqual(hourly_0830[0]["scheduled_time"], f"{self.today}T08:30:00+05:30")
        # Ensure 07:00 and 08:00 slots are NOT generated (no retroactive penalties)
        self.assertFalse(any("07:00" in a["scheduled_time"] or "08:00" in a["scheduled_time"] for a in hourly_0830))

        # Case C: Wake up at 09:30 -> hourly slots start at 10:00 AM (9 + 1)
        acts_0930 = self.tw.generate_daily_schedule(self.today, wakeup_time="09:30")
        hourly_0930 = [a for a in acts_0930 if a["type"] == "HOURLY"]
        self.assertEqual(hourly_0930[0]["scheduled_time"], f"{self.today}T09:30:00+05:30")

    def test_05_hourly_notification_generation(self):
        """Hourly proof notifications are generated for PRE_OPENING and OPENED with exact text."""
        # Target: 09:00 AM -> Window: 08:50 - 09:10
        # Check PRE_OPENING at 08:49:15
        ref_pre = datetime.strptime(f"{self.today} 08:49:15", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        DailyRoutineGate.seed_approved_wakeup(self.db, self.today, completed_hm="08:00")
        self.tw.ensure_activities_in_db(self.today, wakeup_time="08:00", reference_now=ref_pre)
        notifs_pre = self.tw.get_pending_notifications(self.today, reference_now=ref_pre)
        self.assertTrue(len(notifs_pre) >= 1)
        pre_notif = next((n for n in notifs_pre if "0900" in n["activity_id"]), None)
        self.assertIsNotNone(pre_notif)
        self.assertEqual(pre_notif["event_type"], "PRE_OPENING")
        self.assertIn("opens in one minute", pre_notif["message"])

        # Check OPENED at 08:55:00
        ref_open = datetime.strptime(f"{self.today} 08:55:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        notifs_open = self.tw.get_pending_notifications(self.today, reference_now=ref_open)
        self.assertTrue(len(notifs_open) >= 1)
        open_notif = next((n for n in notifs_open if "0900" in n["activity_id"]), None)
        self.assertIsNotNone(open_notif)
        self.assertEqual(open_notif["event_type"], "OPENED")
        self.assertIn("09:10 AM", open_notif["message"])

    def test_06_auto_closing_missed_hourly_proofs(self):
        """Expired submission windows are automatically closed as MISSED."""
        # 10:15 AM -> 09:00 AM activity (window 08:50 - 09:10) is expired!
        ref_dt = datetime.strptime(f"{self.today} 10:15:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        DailyRoutineGate.seed_approved_wakeup(self.db, self.today, completed_hm="08:00")
        self.tw.ensure_activities_in_db(self.today, wakeup_time="08:00", reference_now=ref_dt)
        
        acts = self.tw.get_activities_for_date(self.today, reference_now=ref_dt)
        missed_09 = next(a for a in acts if "0900" in a["activity_id"])
        self.assertEqual(missed_09["status"], "MISSED")

    # ── 4. Shared YouTube Privilege Engine ──────────────────────────────────
    def test_07_youtube_privilege_modes(self):
        """Verifies YouTube privilege evaluation across all 3 modes."""
        pe = PrivilegeEngine(self.db)
        
        # Test Mode A: CONTINUOUS_PROOF_GATED
        # Without wakeup complete -> Locked
        stat_a_nowake = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="CONTINUOUS_PROOF_GATED",
            wakeup_completed=False,
        )
        self.assertFalse(stat_a_nowake["allowed"])
        self.assertIn("Wake-up", stat_a_nowake["reason"])

        # With wakeup complete & hourly proof verified -> Allowed
        stat_a_ok = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="CONTINUOUS_PROOF_GATED",
            wakeup_completed=True,
            last_hourly_proof_status="VERIFIED",
        )
        self.assertTrue(stat_a_ok["allowed"])

        # Test Mode B: ALTERNATING_HOURS
        # Hour 13:15 (odd) -> Reward hour -> Allowed if proof verified
        stat_b_odd = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="ALTERNATING_HOURS",
            wakeup_completed=True,
            last_hourly_proof_status="VERIFIED",
            current_time_hhmm="13:15",
        )
        self.assertTrue(stat_b_odd["allowed"])

        # Hour 14:15 (even) -> Focus hour -> Locked
        stat_b_even = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="ALTERNATING_HOURS",
            wakeup_completed=True,
            current_time_hhmm="14:15",
        )
        self.assertFalse(stat_b_even["allowed"])

        # Test Mode C: SCHEDULED_ALLOW_WINDOWS
        # Outside allow windows (15:00) -> Locked
        stat_c_out = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="SCHEDULED_ALLOW_WINDOWS",
            wakeup_completed=True,
            current_time_hhmm="15:00",
            allow_windows=[{"start": "18:00", "end": "20:00"}],
        )
        self.assertFalse(stat_c_out["allowed"])

        # Inside allow window (18:30) -> Allowed
        stat_c_in = pe.evaluate_youtube_privilege(
            current_score=85, required_score=80,
            privilege_mode="SCHEDULED_ALLOW_WINDOWS",
            wakeup_completed=True,
            current_time_hhmm="18:30",
            allow_windows=[{"start": "18:00", "end": "20:00"}],
        )
        self.assertTrue(stat_c_in["allowed"])

        # Admin Daily Unlock Override applies regardless of mode or score
        stat_admin = pe.evaluate_youtube_privilege(
            current_score=50, required_score=80,
            privilege_mode="SCHEDULED_ALLOW_WINDOWS",
            is_daily_unlock=True,
        )
        self.assertTrue(stat_admin["allowed"])

    # ── 5. Notification Center & Database Methods ────────────────────────────
    def test_08_notification_center_categories_and_deduplication(self):
        """Notification Center handles categories, deduplication, and read/dismiss."""
        nc = NotificationCenter()

        # Add PROOF notification
        added1 = nc.add(
            key="ACT-20261010-1000-OPEN",
            title="Hourly Checkpoint",
            message="Submit hourly proof before 10:10 AM.",
            category=CATEGORY_PROOF,
            activity_id="ACT-20261010-1000-HOURLY",
            event_type="OPENED",
        )
        self.assertTrue(added1)

        # Duplicate add with same key -> rejected (returns False)
        added2 = nc.add(
            key="ACT-20261010-1000-OPEN",
            title="Hourly Checkpoint",
            message="Submit hourly proof before 10:10 AM.",
            category=CATEGORY_PROOF,
        )
        self.assertFalse(added2)

        # Query by category
        events = nc.get_all(category=CATEGORY_PROOF)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["read"])

        # Mark read
        nc.mark_read("ACT-20261010-1000-OPEN")
        events_read = nc.get_all(category=CATEGORY_PROOF)
        self.assertTrue(events_read[0]["read"])

        # Dismiss
        nc.dismiss("ACT-20261010-1000-OPEN")
        events_active = nc.get_all(category=CATEGORY_PROOF, include_dismissed=False)
        self.assertEqual(len(events_active), 0)

    def test_09_database_notification_center_methods(self):
        """Database notification_center table methods work correctly."""
        # 1. Save notification event
        saved = self.db.save_notification_center_event(
            notification_key="EVT-001",
            title="Points Awarded",
            message="15 points awarded for morning workout proof.",
            category=CATEGORY_POINTS,
            event_type="POINTS_AWARDED",
            activity_id="ACT-GYM",
        )
        self.assertIsNotNone(saved)

        # 2. Retrieve events
        evts = self.db.get_notification_center_events(category=CATEGORY_POINTS)
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["title"], "Points Awarded")

        # 3. Mark read
        self.db.mark_notification_center_read(notification_key="EVT-001")
        evts_r = self.db.get_notification_center_events(category=CATEGORY_POINTS)
        self.assertEqual(evts_r[0]["is_read"], 1)

        # 4. Dismiss
        self.db.dismiss_notification_center_event(notification_key="EVT-001")
        evts_active = self.db.get_notification_center_events(category=CATEGORY_POINTS, unread_only=False)
        self.assertEqual(len(evts_active), 0)


class TestServerEndpointsV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "server_test_v2.db")
        cls.db = Database(cls.db_path)
        cls.admin_token=cls.db.authenticate_admin("admin", "admin1984")['token']
        cls.engine = ScoringEngine(cls.db)
        cls.notif_service = StrictNotificationService(server_url="http://127.0.0.1:8789")
        
        cls.port = 8789
        StrictRecordsRequestHandler.db = cls.db
        StrictRecordsRequestHandler.engine = cls.engine
        StrictRecordsRequestHandler.scoring_engine = cls.engine
        StrictRecordsRequestHandler.notification_service = cls.notif_service
        StrictRecordsRequestHandler.active_playback_sessions = {}

        cls.httpd = HTTPServer(("127.0.0.1", cls.port), StrictRecordsRequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.tmp_dir.cleanup()
        except Exception:
            pass

    def _api_get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",headers={'X-Admin-Session':self.admin_token})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _api_post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",'X-Admin-Session':self.admin_token}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_10_api_wakeup_checklist_and_playback_progress(self):
        """End-to-end API tests for wake-up checklist and playback telemetry."""
        today = datetime.now().strftime("%Y-%m-%d")
        self.engine.trigger_wakeup_alarm(today)

        # 1. Get Checklist
        res_chk = self._api_get("/api/wakeup/checklist")
        self.assertIn("state", res_chk)
        self.assertIn("checklist", res_chk)

        # 2. Step Update: change state
        res_step = self._api_post("/api/wakeup/step_update", {
            "date": today,
            "state": "PROOF_SUBMITTED",
            "volume_limit_ok": True,
        })
        self.assertEqual(res_step["state"], "PROOF_SUBMITTED")

        # 3. Playback progress API
        res_prog = self._api_post("/api/wakeup/playback/progress", {
            "date": today,
            "track_name": "Awaken Mind",
            "duration_sec": __import__('core.wakeup_attempt_validation',fromlist=['configured_track_manifest']).configured_track_manifest()['duration_sec'],
            "position_sec": 0.0,
            "volume_pct": 50.0,
            "audio_route": "BLUETOOTH_A2DP",
            "headset_name": "Bose QC45",
            "action": "START",
        })
        self.assertIn("playback", res_prog)
        self.assertTrue(res_prog["playback"]["is_playing"])
        self.assertEqual(res_prog["playback"]["max_volume_pct"], 60.0)

    def test_11_api_notification_center_and_privilege_modes(self):
        """API tests for Notification Center and Privilege Engine mode switches."""
        # 1. Push notification via Android mirror API
        push_res = self._api_post("/api/android/notification/push", {
            "package_name": "com.microsoft.teams",
            "app_name": "Teams",
            "title": "StrictRecords Standup",
            "text": "Daily standup meeting is starting now.",
            "post_time": 1728551000000
        })
        self.assertTrue(push_res["success"])

        # 2. Query Notification Center
        center_res = self._api_get("/api/notifications/center")
        self.assertIn("events", center_res)
        self.assertTrue(len(center_res["events"]) >= 1)
        event_key = center_res["events"][0]["notification_key"]

        # 3. Mark Read
        read_res = self._api_post("/api/notifications/read", {"notification_key": event_key})
        self.assertTrue(read_res["success"])

        # 4. Dismiss
        dismiss_res = self._api_post("/api/notifications/dismiss", {"notification_key": event_key})
        self.assertTrue(dismiss_res["success"])

        # 5. Switch YouTube privilege mode
        mode_res = self._api_post("/api/privilege/youtube/mode", {"mode": "ALTERNATING_HOURS"})
        self.assertTrue(mode_res["success"])
        self.assertEqual(mode_res["mode"], "ALTERNATING_HOURS")

        # 6. Check YouTube privilege status
        status_res = self._api_get("/api/privilege/youtube/status")
        self.assertEqual(status_res["mode"], "ALTERNATING_HOURS")


if __name__ == "__main__":
    unittest.main()
