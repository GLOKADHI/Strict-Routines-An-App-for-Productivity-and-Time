import os
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from PIL import Image

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, TimeWindowEngine, iso_to_dt, dt_to_iso
from core.hourly_submission_service import HourlySubmissionService, HourlySubmissionError
from windows.notification_service import StrictNotificationService
from windows.submission_window_manager import SubmissionWindowManager
from tests.test_daily_routine_gate import seed_approved_wakeup


class TestHourlySubmissionWindowTiming(unittest.TestCase):
    """
    Comprehensive regression test suite for hourly submission window timing,
    boundary rules (window_start <= t < window_end), notifications, and backend validation.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        self.scoring = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        self.tw = TimeWindowEngine(self.db)
        self.submission_service = HourlySubmissionService(self.db, self.scoring)
        self.notif_service = StrictNotificationService()
        self.swm = SubmissionWindowManager(self.db)

        self.date_str = "2026-10-14"
        self.account_id = "user_lok"
        self.db.ensure_daily_record(self.date_str)

        # Generate 120x120 test image bytes (meets 100x100 minimum)
        buf = io.BytesIO()
        Image.new("RGB", (120, 120), (50, 100, 150)).save(buf, format="JPEG")
        self.sample_photo = buf.getvalue()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_dt(self, hour: int, minute: int, second: int = 0) -> datetime:
        return datetime(2026, 10, 14, hour, minute, second, tzinfo=IST)

    def _configure_routine_and_seed_activities(self, wakeup_time: str = "09:00"):
        """Configures next day routine and seeds activities into SQLite."""
        with self.db._connect() as conn:
            conn.execute("DELETE FROM scheduled_activities WHERE date = ?", (self.date_str,))
        sched_id = f"SCHED-{self.date_str.replace('-', '')}-V1"
        self.db.save_routine_schedule_record({
            "schedule_id": sched_id,
            "schedule_date": self.date_str,
            "schedule_version": 1,
            "account_id": self.account_id,
            "status": "SCHEDULED",
            "wake_up_time": wakeup_time,
            "bath_time": None,
            "gym_time": None,
            "gym_enabled": 0,
            "custom_activities_json": "[]",
            "timezone": "Asia/Kolkata",
        })

    def test_schedule_generation_exact_hourly_timeline(self):
        """
        Verify hourly window generation for configured 9:00 AM wake-up:
        - Window 1 (k=0): Reminder 8:49 AM, Start 8:50 AM, End 9:10 AM
        - Window 2 (k=1): Reminder 9:49 AM, Start 9:50 AM, End 10:10 AM
        - Window 3 (k=2): Reminder 10:49 AM, Start 10:50 AM, End 11:10 AM
        """
        self._configure_routine_and_seed_activities("09:00")
        ref_morning = self._make_dt(8, 0)
        activities = self.tw.ensure_activities_in_db(self.date_str, self.account_id, reference_now=ref_morning)

        hourly_acts = [a for a in activities if a["type"] == "HOURLY"]
        self.assertGreaterEqual(len(hourly_acts), 3)

        # Window 1: ACT-20261014-0900-HOURLY
        act1 = hourly_acts[0]
        self.assertEqual(act1["activity_id"], "ACT-20261014-0900-HOURLY")
        self.assertTrue(act1["notification_time"].startswith(f"{self.date_str}T08:49:00"))
        self.assertTrue(act1["window_start"].startswith(f"{self.date_str}T08:50:00"))
        self.assertTrue(act1["window_end"].startswith(f"{self.date_str}T09:10:00"))

        # Window 2: ACT-20261014-1000-HOURLY
        act2 = hourly_acts[1]
        self.assertEqual(act2["activity_id"], "ACT-20261014-1000-HOURLY")
        self.assertTrue(act2["notification_time"].startswith(f"{self.date_str}T09:49:00"))
        self.assertTrue(act2["window_start"].startswith(f"{self.date_str}T09:50:00"))
        self.assertTrue(act2["window_end"].startswith(f"{self.date_str}T10:10:00"))

        # Window 3: ACT-20261014-1100-HOURLY
        act3 = hourly_acts[2]
        self.assertEqual(act3["activity_id"], "ACT-20261014-1100-HOURLY")
        self.assertTrue(act3["notification_time"].startswith(f"{self.date_str}T10:49:00"))
        self.assertTrue(act3["window_start"].startswith(f"{self.date_str}T10:50:00"))
        self.assertTrue(act3["window_end"].startswith(f"{self.date_str}T11:10:00"))

    def test_dynamic_wake_up_configuration_not_hardcoded(self):
        """
        Verify that wake-up time is NOT hard-coded to 9:00 AM.
        Test with configured wake-up time of 07:30 AM:
        - Window 1 (k=0): Reminder 7:19 AM, Start 7:20 AM, End 7:40 AM
        """
        self._configure_routine_and_seed_activities("07:30")
        ref_morning = self._make_dt(7, 0)
        activities = self.tw.ensure_activities_in_db(self.date_str, self.account_id, reference_now=ref_morning)

        hourly_acts = [a for a in activities if a["type"] == "HOURLY"]
        self.assertGreaterEqual(len(hourly_acts), 1)

        act1 = hourly_acts[0]
        self.assertEqual(act1["activity_id"], "ACT-20261014-0730-HOURLY")
        self.assertTrue(act1["notification_time"].startswith(f"{self.date_str}T07:19:00"))
        self.assertTrue(act1["window_start"].startswith(f"{self.date_str}T07:20:00"))
        self.assertTrue(act1["window_end"].startswith(f"{self.date_str}T07:40:00"))

    def test_timeline_states_notifications_and_submission_boundaries(self):
        """
        Deterministic walkthrough of the full 9:00 AM timeline:
        - 8:48 AM: before reminder. No notifications. Window not open; submission rejected.
        - 8:49 AM: reminder scheduled/fired (PRE_OPENING). Window not yet open; submission rejected.
        - 8:50 AM: window opens (OPENED); submission allowed.
        - 9:00 AM: within window; submission allowed.
        - 9:09 AM: within window (WARNING_1M); submission allowed.
        - 9:10 AM: window closed (CLOSED); submission rejected.
        - 9:49 AM: next reminder (PRE_OPENING for 10:00 slot).
        - 9:50 AM: next window opens (OPENED for 10:00 slot).
        - 10:10 AM: next window closes (CLOSED for 10:00 slot).
        """
        self._configure_routine_and_seed_activities("09:00")
        # Pre-seed approved wake-up at 8:40 AM so daily routine gate is activated before 8:48 AM
        seed_approved_wakeup(self.db, self.scoring, self.date_str, completed_hm="08:40")

        # -------------------------------------------------------------
        # 1. 8:48 AM: Before reminder
        # -------------------------------------------------------------
        t_848 = self._make_dt(8, 48)
        notifs_848 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_848)
        hourly_notifs_848 = [n for n in notifs_848 if "HOURLY" in n.get("activity_id", "")]
        self.assertEqual(len(hourly_notifs_848), 0, "No hourly notification should fire at 8:48 AM")

        # Submission attempt at 8:48 AM must be rejected (WINDOW_NOT_OPEN)
        with self.assertRaises(HourlySubmissionError) as ctx_848:
            self.submission_service.accept(
                payload={
                    "activity_id": "ACT-20261014-0900-HOURLY",
                    "primary_activity": "STUDY",
                    "description": "Early attempt",
                },
                file_bytes=self.sample_photo,
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device",
                platform="windows",
                received_at=t_848,
            )
        self.assertEqual(ctx_848.exception.code, "WINDOW_NOT_OPEN")

        # -------------------------------------------------------------
        # 2. 8:49 AM: Reminder scheduled / fired
        # -------------------------------------------------------------
        t_849 = self._make_dt(8, 49)
        notifs_849 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_849)
        hourly_notifs_849 = [n for n in notifs_849 if n.get("activity_id") == "ACT-20261014-0900-HOURLY"]
        self.assertEqual(len(hourly_notifs_849), 1)
        self.assertEqual(hourly_notifs_849[0]["event_type"], "PRE_OPENING")
        self.assertIn("opens in one minute", hourly_notifs_849[0]["message"])

        # Delivery via NotificationService
        delivered = self.notif_service.ingest_pending_notifications(notifs_849)
        self.assertGreaterEqual(delivered, 1)

        # Verify no duplicate notification on repeated poll at 8:49 AM
        notifs_849_repeat = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_849)
        hourly_repeat = [n for n in notifs_849_repeat if n.get("activity_id") == "ACT-20261014-0900-HOURLY"]
        self.assertEqual(len(hourly_repeat), 0, "Should not return duplicate PRE_OPENING notification")

        # Submission attempt at 8:49 AM must still be rejected (WINDOW_NOT_OPEN)
        with self.assertRaises(HourlySubmissionError) as ctx_849:
            self.submission_service.accept(
                payload={
                    "activity_id": "ACT-20261014-0900-HOURLY",
                    "primary_activity": "STUDY",
                    "description": "Reminder fired but window still not open",
                },
                file_bytes=self.sample_photo,
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device",
                platform="windows",
                received_at=t_849,
            )
        self.assertEqual(ctx_849.exception.code, "WINDOW_NOT_OPEN")

        # -------------------------------------------------------------
        # 3. 8:50 AM: Window opens (Inclusive open boundary)
        # -------------------------------------------------------------
        t_850 = self._make_dt(8, 50)
        notifs_850 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_850)
        hourly_notifs_850 = [n for n in notifs_850 if n.get("activity_id") == "ACT-20261014-0900-HOURLY"]
        self.assertEqual(len(hourly_notifs_850), 1)
        self.assertEqual(hourly_notifs_850[0]["event_type"], "OPENED")

        # Submission at 8:50:00 AM MUST be accepted
        res_850 = self.submission_service.accept(
            payload={
                "activity_id": "ACT-20261014-0900-HOURLY",
                "primary_activity": "CODING",
                "description": "Submitted right at 8:50 opening",
                "submission_id": "sub_test_850",
            },
            file_bytes=self.sample_photo,
            filename="proof.jpg",
            account_id=self.account_id,
            device_id="test_device",
            platform="windows",
            received_at=t_850,
        )
        self.assertTrue(res_850["accepted"])
        self.assertEqual(res_850["attendance_status"], "PRESENT")

        # Duplicate submission with different submission_id for same slot must be rejected
        with self.assertRaises(HourlySubmissionError) as ctx_dup:
            self.submission_service.accept(
                payload={
                    "activity_id": "ACT-20261014-0900-HOURLY",
                    "primary_activity": "CODING",
                    "description": "Duplicate submission attempt",
                    "submission_id": "sub_test_dup",
                },
                file_bytes=self.sample_photo,
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device",
                platform="windows",
                received_at=self._make_dt(8, 55),
            )
        self.assertEqual(ctx_dup.exception.code, "DUPLICATE_SUBMISSION")

        # Idempotent retry with the SAME submission_id must return accepted without error
        res_idem = self.submission_service.accept(
            payload={
                "activity_id": "ACT-20261014-0900-HOURLY",
                "primary_activity": "CODING",
                "description": "Submitted right at 8:50 opening",
                "submission_id": "sub_test_850",
            },
            file_bytes=self.sample_photo,
            filename="proof.jpg",
            account_id=self.account_id,
            device_id="test_device",
            platform="windows",
            received_at=t_850,
        )
        self.assertTrue(res_idem["accepted"])
        self.assertTrue(res_idem.get("deduplicated", False))

        # -------------------------------------------------------------
        # 4. Check Window 2 (10:00 AM slot) at 9:00 AM
        #    (Before 9:50 AM opening, Window 2 is upcoming)
        # -------------------------------------------------------------
        t_900 = self._make_dt(9, 0)
        with self.assertRaises(HourlySubmissionError) as ctx_900:
            self.submission_service.accept(
                payload={
                    "activity_id": "ACT-20261014-1000-HOURLY",
                    "primary_activity": "CODING",
                    "description": "Too early for 10:00 window",
                },
                file_bytes=self.sample_photo,
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device",
                platform="windows",
                received_at=t_900,
            )
        self.assertEqual(ctx_900.exception.code, "WINDOW_NOT_OPEN")

        # -------------------------------------------------------------
        # 5. 9:10 AM: Window 1 closes (Exclusive close boundary)
        # -------------------------------------------------------------
        t_910 = self._make_dt(9, 10)
        # Activity 1 was submitted, so at 9:10 it gets CLOSED notification with attendance PRESENT
        notifs_910 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_910)
        act1_notifs_910 = [n for n in notifs_910 if n.get("activity_id") == "ACT-20261014-0900-HOURLY"]
        self.assertEqual(len(act1_notifs_910), 1)
        self.assertEqual(act1_notifs_910[0]["event_type"], "CLOSED")

        # -------------------------------------------------------------
        # 6. Test Window 2:
        #    9:49 AM: Reminder 2
        #    9:50 AM: Window 2 opens
        #    10:00 AM: Within window
        #    10:09 AM: Within window (1m warning)
        #    10:10 AM: Window 2 closes
        # -------------------------------------------------------------
        t_949 = self._make_dt(9, 49)
        notifs_949 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_949)
        act2_notifs_949 = [n for n in notifs_949 if n.get("activity_id") == "ACT-20261014-1000-HOURLY"]
        self.assertEqual(len(act2_notifs_949), 1)
        self.assertEqual(act2_notifs_949[0]["event_type"], "PRE_OPENING")

        t_950 = self._make_dt(9, 50)
        notifs_950 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_950)
        act2_notifs_950 = [n for n in notifs_950 if n.get("activity_id") == "ACT-20261014-1000-HOURLY"]
        self.assertEqual(len(act2_notifs_950), 1)
        self.assertEqual(act2_notifs_950[0]["event_type"], "OPENED")

        # At 10:00 AM (middle of window) - window state is dynamically OPEN
        t_1000 = self._make_dt(10, 0)
        acts_1000 = self.tw.get_activities_for_date(self.date_str, self.account_id, reference_now=t_1000)
        act2_1000 = next(a for a in acts_1000 if a["activity_id"] == "ACT-20261014-1000-HOURLY")
        self.assertEqual(act2_1000["status"], "OPEN")
        self.assertFalse(act2_1000["is_closed"])

        # At 10:09 AM (last minute of window) - WARNING_1M notification fires for open window
        t_1009 = self._make_dt(10, 9)
        notifs_1009 = self.tw.get_pending_notifications(self.date_str, self.account_id, reference_now=t_1009)
        act2_notifs_1009 = [n for n in notifs_1009 if n.get("activity_id") == "ACT-20261014-1000-HOURLY"]
        self.assertEqual(len(act2_notifs_1009), 1)
        self.assertEqual(act2_notifs_1009[0]["event_type"], "WARNING_1M")

        # At 10:10 AM - Window 2 is closed! Late submission rejected
        t_1010 = self._make_dt(10, 10)
        with self.assertRaises(HourlySubmissionError) as ctx_1010:
            self.submission_service.accept(
                payload={
                    "activity_id": "ACT-20261014-1000-HOURLY",
                    "primary_activity": "EXERCISE",
                    "description": "Submitted at close boundary",
                },
                file_bytes=self.sample_photo,
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device",
                platform="windows",
                received_at=t_1010,
            )
        self.assertEqual(ctx_1010.exception.code, "WINDOW_CLOSED")

    def test_strict_boundary_half_open_interval(self):
        """
        Verify precise boundary rule:
        windowOpen <= currentTime < windowClose
        At window_start: ALLOWED
        At window_end - 1s: ALLOWED
        At window_end: REJECTED
        """
        self._configure_routine_and_seed_activities("09:00")
        seed_approved_wakeup(self.db, self.scoring, self.date_str, completed_hm="08:40")
        self.tw.ensure_activities_in_db(self.date_str, self.account_id, reference_now=self._make_dt(8, 50))

        # ACT-20261014-0900-HOURLY has window: 08:50:00 to 09:10:00
        w_start = self._make_dt(8, 50, 0)
        w_last_sec = self._make_dt(9, 9, 59)
        w_end = self._make_dt(9, 10, 0)

        # 1. 08:49:59 (1 second before start) -> REJECTED (TOO_EARLY)
        t_before = w_start - timedelta(seconds=1)
        with patch("core.time_window_engine.now_ist", return_value=t_before):
            res_before = self.tw.evaluate_submission_timing("ACT-20261014-0900-HOURLY", dt_to_iso(t_before))
        self.assertFalse(res_before["allowed"])
        self.assertEqual(res_before["status"], "TOO_EARLY")

        # 2. 08:50:00 (exact start) -> ALLOWED
        with patch("core.time_window_engine.now_ist", return_value=w_start):
            res_start = self.tw.evaluate_submission_timing("ACT-20261014-0900-HOURLY", dt_to_iso(w_start))
        self.assertTrue(res_start["allowed"])

        # Reset status so scenario 3 tests the boundary without being blocked as duplicate
        with self.db._connect() as conn:
            conn.execute("UPDATE scheduled_activities SET status='OPEN' WHERE activity_id='ACT-20261014-0900-HOURLY'")

        # 3. 09:09:59 (1 second before end) -> ALLOWED
        with patch("core.time_window_engine.now_ist", return_value=w_last_sec):
            res_last = self.tw.evaluate_submission_timing("ACT-20261014-0900-HOURLY", dt_to_iso(w_last_sec))
        self.assertTrue(res_last["allowed"])

        # Reset status so scenario 4 tests the close boundary
        with self.db._connect() as conn:
            conn.execute("UPDATE scheduled_activities SET status='OPEN' WHERE activity_id='ACT-20261014-0900-HOURLY'")

        # 4. 09:10:00 (exact end) -> REJECTED (REJECTED_LATE)
        with patch("core.time_window_engine.now_ist", return_value=w_end):
            res_end = self.tw.evaluate_submission_timing("ACT-20261014-0900-HOURLY", dt_to_iso(w_end))
        self.assertFalse(res_end["allowed"])
        self.assertEqual(res_end["status"], "REJECTED_LATE")

    def test_utc_and_local_timezone_consistency(self):
        """
        Verify that client submitting in UTC equivalent to 08:55 AM IST is correctly validated.
        08:55 AM IST = 03:25 AM UTC (since IST is UTC+5:30).
        """
        self._configure_routine_and_seed_activities("09:00")
        seed_approved_wakeup(self.db, self.scoring, self.date_str, completed_hm="08:40")
        self.tw.ensure_activities_in_db(self.date_str, self.account_id, reference_now=self._make_dt(8, 50))

        # 03:25 AM UTC = 08:55 AM IST (inside 08:50 - 09:10 window)
        utc_dt = datetime.fromisoformat("2026-10-14T03:25:00+00:00")

        res = self.submission_service.accept(
            payload={
                "activity_id": "ACT-20261014-0900-HOURLY",
                "primary_activity": "CODING",
                "description": "UTC timestamp submission",
                "submission_id": "sub_utc_test",
            },
            file_bytes=self.sample_photo,
            filename="proof.jpg",
            account_id=self.account_id,
            device_id="test_device",
            platform="windows",
            received_at=utc_dt,
        )
        self.assertTrue(res["accepted"])
        self.assertEqual(res["attendance_status"], "PRESENT")


if __name__ == "__main__":
    unittest.main()
