import json
import os
import hashlib
import tempfile
import threading
import unittest
from tests.test_daily_routine_gate import seed_approved_wakeup
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer

from core.database import StrictRecordsDB as Database
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler as StrictRecordsHTTPHandler
from core.strictbot import StrictBot
from core.time_window_engine import IST, TimeWindowEngine


class TestNextDayRoutineScheduler(unittest.TestCase):
    """
    Comprehensive 20-scenario test suite for the Next-Day Routine Scheduler
    (Sections 1–20 of the specification).
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_routine_scheduler.db")
        self.db = Database(self.db_path)
        self.db.change_admin_pin("1234", "5678", admin_user="test_admin")
        self.engine = ScoringEngine(self.db)
        self.tw = self.engine.tw_engine
        self.today = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.tomorrow = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

    def tearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def _get_activity_dict(self, activity_id: str, date_str: str, ref_dt=None):
        acts = self.tw.get_activities_for_date(date_str, reference_now=ref_dt)
        for a in acts:
            if a["activity_id"] == activity_id:
                return a
        return None

    # ── 1. Submitting today's wake-up proof & opening next-day schedule prompt while AI verification is pending
    def test_01_wakeup_proof_pending_ai_and_next_day_prompt(self):
        dt_0701 = datetime.strptime(f"{self.today} 07:01:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        self.tw.ensure_activities_in_db(self.today, wakeup_time="07:00", reference_now=dt_0701)
        wake_act_id = f"ACT-{self.today.replace('-', '')}-0700-WAKEUP"

        # Submit wake-up proof inside the 06:50–07:10 window while AI verification is pending
        res = self.tw.evaluate_submission_timing(
            wake_act_id,
            submission_iso=f"{self.today}T07:01:15+05:30",
            begin_ai_validation=True,
        )
        self.assertTrue(res["allowed"])
        self.assertEqual(res["status"], "VALIDATING")
        self.assertTrue(res["prompt_next_day_schedule"])

        today_acts = self.tw.get_activities_for_date(self.today, reference_now=dt_0701)
        bot_msg = StrictBot.evaluate_schedule_message(
            today_activities=today_acts,
            event_trigger="WAKEUP_SUBMITTED",
        )
        self.assertEqual(
            bot_msg["message"],
            "Your wake-up proof has been submitted. Let's plan tomorrow's routine.",
        )

        # User can save a draft while proof verification is still pending (VALIDATING)
        draft_res = self.tw.save_next_day_draft({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:15",
            "bath_time": "07:45",
            "gym_time": "18:30",
        })
        self.assertTrue(draft_res["success"])
        self.assertEqual(draft_res["status"], "DRAFT")
        state = self.tw.get_next_day_schedule_state(self.tomorrow)
        self.assertTrue(state["reminder_needed"])
        # Ensure no fake confirmed schedule was silently generated
        self.assertIsNone(self.db.get_confirmed_routine_schedule(self.tomorrow))

    # ── 2. Valid wake-up time at 06:00 AM
    def test_02_valid_wakeup_0600(self):
        ok, norm_time, _ = TimeWindowEngine.validate_wakeup_time("06:00")
        self.assertTrue(ok)
        self.assertEqual(norm_time, "06:00")

        win = TimeWindowEngine.calculate_activity_window(self.tomorrow, "06:00", 20)
        self.assertIn("05:50", win["window_start"])
        self.assertIn("06:10", win["window_end"])
        self.assertIn("05:49", win["notification_time"])

        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "06:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        self.assertTrue(res["success"])
        wake_act = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-0600-WAKEUP", self.tomorrow)
        self.assertIsNotNone(wake_act)
        self.assertIn("07:00", wake_act["window_start"])
        self.assertIn("T09:30:00", wake_act["window_end"])  # restored 07:00-09:30 wake-up window
        self.assertIn("06:00", wake_act["scheduled_time"])

    # ── 3. Valid wake-up time at 10:00 AM
    def test_03_valid_wakeup_1000(self):
        ok, norm_time, _ = TimeWindowEngine.validate_wakeup_time("10:00")
        self.assertTrue(ok)
        self.assertEqual(norm_time, "10:00")

        win = TimeWindowEngine.calculate_activity_window(self.tomorrow, "10:00", 20)
        self.assertIn("09:50", win["window_start"])
        self.assertIn("10:10", win["window_end"])
        self.assertIn("09:49", win["notification_time"])

        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "10:00",
            "bath_time": "08:30",
            "gym_time": "18:30",
        })
        self.assertTrue(res["success"])
        wake_act = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-1000-WAKEUP", self.tomorrow)
        self.assertIsNotNone(wake_act)
        self.assertIn("07:00", wake_act["window_start"])
        self.assertIn("T09:30:00", wake_act["window_end"])  # restored 07:00-09:30 wake-up window
        self.assertIn("10:00", wake_act["scheduled_time"])

    # ── 4. Invalid wake-up time at 05:59 AM rejected
    def test_04_invalid_wakeup_0559_rejected(self):
        ok, _, err = TimeWindowEngine.validate_wakeup_time("05:59")
        self.assertFalse(ok)
        self.assertEqual(err, "Wake-up time must be between 6:00 AM and 10:00 AM.")

        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "05:59",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "Wake-up time must be between 6:00 AM and 10:00 AM.")

    # ── 5. Invalid wake-up time at 10:01 AM rejected
    def test_05_invalid_wakeup_1001_rejected(self):
        ok, _, err = TimeWindowEngine.validate_wakeup_time("10:01")
        self.assertFalse(ok)
        self.assertEqual(err, "Wake-up time must be between 6:00 AM and 10:00 AM.")

        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "10:01",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "Wake-up time must be between 6:00 AM and 10:00 AM.")

    # ── 6. Custom bath time and its 20-minute window
    def test_06_custom_bath_time_and_window(self):
        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "08:15",
            "gym_time": "18:30",
        })
        self.assertTrue(res["success"])
        bath_act = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-0815-BATH", self.tomorrow)
        self.assertIsNotNone(bath_act)
        self.assertIn("08:15", bath_act["scheduled_time"])
        self.assertIn("08:05", bath_act["window_start"])
        self.assertIn("08:25", bath_act["window_end"])
        self.assertIn("08:04", bath_act["notification_time"])
        self.assertEqual(bath_act["points"], 10)
        self.assertTrue(len(bath_act["proof_requirements"]) > 0)

    # ── 7. Custom gym time and its 20-minute window
    def test_07_custom_gym_time_and_window(self):
        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "17:30",
            "gym_enabled": True,
        })
        self.assertTrue(res["success"])
        gym_act = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-1730-GYM", self.tomorrow)
        self.assertIsNotNone(gym_act)
        self.assertIn("17:30", gym_act["scheduled_time"])
        self.assertIn("19:00", gym_act["window_start"])
        self.assertIn("19:30", gym_act["window_end"])
        self.assertIn("18:59", gym_act["notification_time"])
        self.assertEqual(gym_act["points"], 15)

        # Admin-required gym policy cannot be disabled by user
        val = self.tw.validate_routine_schedule({
            "schedule_date": "2026-10-11",
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:00",
            "gym_enabled": False,
            "gym_required_by_admin": True,
        })
        self.assertFalse(val["valid"])
        self.assertTrue(any("Gym task is required" in e for e in val["errors"]))

    # ── 8. Additional custom activity scheduling & point protection
    def test_08_additional_custom_activity_scheduling(self):
        # Attempt to tamper with points (e.g. passing 999 points for STUDY) must be sanitized to configured policy (10 pts)
        custom_acts = [
            {
                "task_type": "STUDY",
                "title": "Algorithms Study Session",
                "scheduled_time": "14:30",
                "points": 999,
                "enabled": True,
            },
            {
                "task_type": "PROGRAMMING",
                "title": "Systems Programming Practice",
                "scheduled_time": "16:30",
                "points": 500,
                "enabled": True,
            },
        ]
        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
            "custom_activities": custom_acts,
        })
        self.assertTrue(res["success"])
        acts = self.tw.get_activities_for_date(self.tomorrow)
        study_acts = [a for a in acts if a["type"] == "STUDY"]
        prog_acts = [a for a in acts if a["type"] == "PROGRAMMING"]
        self.assertEqual(len(study_acts), 1)
        self.assertEqual(len(prog_acts), 1)
        self.assertEqual(study_acts[0]["points"], 10)
        self.assertIn("14:20", study_acts[0]["window_start"])
        self.assertIn("14:40", study_acts[0]["window_end"])
        self.assertEqual(prog_acts[0]["points"], 12)
        self.assertIn("16:20", prog_acts[0]["window_start"])
        self.assertIn("16:40", prog_acts[0]["window_end"])

    # ── 9. Independent hourly checkpoint preservation
    def test_09_independent_hourly_checkpoint_preservation(self):
        res = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:15",
            "bath_time": "07:45",
            "gym_time": "18:30",
        })
        self.assertTrue(res["success"])
        dt_morning = datetime.strptime(f"{self.tomorrow} 08:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        planned = [a for a in self.tw.get_activities_for_date(self.tomorrow, reference_now=dt_morning) if a["type"] == "HOURLY"]
        self.assertTrue(planned)
        self.assertTrue(all(a.get('status')!='BLOCKED_WAKEUP' for a in planned))
        # Hourly windows are independent; actual window boundaries still apply.
        scoring = ScoringEngine(self.db, storage_root=os.path.join(self.tmp_dir.name, "wake_proofs"))
        seed_approved_wakeup(self.db, scoring, self.tomorrow)
        acts = self.tw.ensure_activities_in_db(self.tomorrow, reference_now=dt_morning)
        hourly = [a for a in acts if a["type"] == "HOURLY"]
        self.assertEqual(len(hourly), 16)  # 07:15 through 22:15, independent of wake-up approval

        h10 = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-1015-HOURLY", self.tomorrow, ref_dt=dt_morning)
        h11 = self._get_activity_dict(f"ACT-{self.tomorrow.replace('-', '')}-1115-HOURLY", self.tomorrow, ref_dt=dt_morning)
        self.assertIn("10:04", h10["notification_time"])
        self.assertIn("10:05", h10["window_start"])
        self.assertIn("10:25", h10["window_end"])
        self.assertIn("11:04", h11["notification_time"])
        self.assertIn("11:05", h11["window_start"])
        self.assertIn("11:25", h11["window_end"])

        # Missing 10:00 AM checkpoint must NOT prevent 11:00 AM checkpoint from opening
        dt_after_10 = datetime.strptime(f"{self.tomorrow} 10:30:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        h10_after = self._get_activity_dict(h10["activity_id"], self.tomorrow, ref_dt=dt_after_10)
        self.assertEqual(h10_after["status"], "MISSED")
        self.assertEqual(h10_after["lifecycle_state"], "CLOSED")

        dt_at_1055 = datetime.strptime(f"{self.tomorrow} 11:10:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        h11_open = self._get_activity_dict(h11["activity_id"], self.tomorrow, ref_dt=dt_at_1055)
        self.assertEqual(h11_open["status"], "OPEN")

    # ── 10. Overlapping window detection
    def test_10_overlapping_window_detection(self):
        # Bath at 10:00 AM (09:50–10:10) overlaps with Hourly Checkpoint 10:00 (09:50–10:10)
        val = self.tw.validate_routine_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "10:00",
            "gym_time": "18:00",
        })
        self.assertFalse(val["valid"])
        self.assertTrue(len(val["conflicts"]) > 0)
        self.assertIn("Overlapping submission windows", val["conflicts"][0]["message"])

        # Also test Wake-up (07:00 -> 06:50–07:10) overlapping Bath (07:05 -> 06:55–07:15)
        val2 = self.tw.validate_routine_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:05",
            "gym_time": "18:00",
        })
        self.assertFalse(val2["valid"])
        self.assertTrue(len(val2["conflicts"]) > 0)

    # ── 11. Schedule confirmation and locking
    def test_11_schedule_confirmation_and_locking(self):
        summary = self.tw.build_routine_summary({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        self.assertTrue(summary["validation"]["valid"])
        self.assertIn("wake_up", summary)
        self.assertIn("hourly_checkpoints", summary)
        self.assertIn("potential_points", summary)

        res = self.tw.confirm_next_day_schedule(
            {
                "schedule_date": self.tomorrow,
                "wake_up_time": "07:00",
                "bath_time": "07:30",
                "gym_time": "18:30",
            },
            actor="user_lok",
            device_id="PC-MAIN",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "SCHEDULED")
        self.assertTrue(res["is_locked"])
        self.assertEqual(res["schedule_version"], 1)
        self.assertEqual(
            res["strictbot_message"],
            "Tomorrow's wake-up target is 7:00 AM. Your other activities are scheduled.",
        )

    # ── 12. Attempt to edit a locked schedule without admin approval rejected
    def test_12_edit_locked_schedule_rejected(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        # Ordinary confirm attempt on locked schedule
        res2 = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "08:00",
            "bath_time": "08:30",
            "gym_time": "18:30",
        })
        self.assertFalse(res2["success"])
        self.assertTrue(res2.get("is_locked"))

        # Ordinary draft save attempt on locked schedule
        draft_res = self.tw.save_next_day_draft({
            "schedule_date": self.tomorrow,
            "wake_up_time": "08:00",
            "bath_time": "08:30",
            "gym_time": "18:30",
        })
        self.assertFalse(draft_res["success"])
        self.assertTrue(draft_res.get("is_locked"))

    # ── 13. Admin-authorized rescheduling with audit logging
    def test_13_admin_authorized_rescheduling_and_audit(self):
        res1 = self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        self.assertTrue(res1["success"])
        old_version = res1["schedule_version"]

        # Reject invalid PIN
        bad_pin = self.tw.admin_reschedule_routine({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:30",
            "bath_time": "08:00",
            "gym_time": "18:30",
            "admin_pin": "0000",
            "reason": "Medical appointment",
        })
        self.assertFalse(bad_pin["success"])

        # Reject missing reason
        no_reason = self.tw.admin_reschedule_routine({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:30",
            "bath_time": "08:00",
            "gym_time": "18:30",
            "admin_pin": "5678",
            "reason": "",
        })
        self.assertFalse(no_reason["success"])

        # Valid admin reschedule
        res2 = self.tw.admin_reschedule_routine({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:30",
            "bath_time": "08:00",
            "gym_time": "19:30",
            "admin_pin": "5678",
            "reason": "Doctor appointment at 07:00 AM",
            "admin_actor": "ADMIN",
            "device_id": "PC-ADMIN",
        })
        self.assertTrue(res2["success"])
        self.assertEqual(res2["schedule_version"], old_version + 1)
        self.assertEqual(res2["schedule"]["wake_up"]["target_time"], "07:30")

        # Verify historical version is preserved as SUPERSEDED and audit record exists
        all_versions = self.db.get_all_routine_schedule_versions(self.tomorrow)
        self.assertEqual(len(all_versions), 2)
        self.assertEqual(all_versions[0]["status"], "SUPERSEDED")
        self.assertEqual(all_versions[1]["status"], "SCHEDULED")

        audits = self.db.get_schedule_reschedule_audits(self.tomorrow)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["reason"], "Doctor appointment at 07:00 AM")
        self.assertEqual(audits[0]["old_version"], 1)
        self.assertEqual(audits[0]["new_version"], 2)

    # ── 14. Window opening on time
    def test_14_window_opening_on_time(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        wake_id = f"ACT-{self.tomorrow.replace('-', '')}-0700-WAKEUP"

        # At 06:45 AM -> SCHEDULED/UPCOMING
        dt_before = datetime.strptime(f"{self.tomorrow} 06:45:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        act_before = self._get_activity_dict(wake_id, self.tomorrow, ref_dt=dt_before)
        self.assertIn(act_before["status"], ("SCHEDULED", "UPCOMING"))

        # At 07:00 AM -> OPEN; notification is scheduled for 06:59 AM.
        dt_open = datetime.strptime(f"{self.tomorrow} 07:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        act_open = self._get_activity_dict(wake_id, self.tomorrow, ref_dt=dt_open)
        self.assertEqual(act_open["status"], "OPEN")

    # ── 15. On-time proof submission with delayed AI verification
    def test_15_ontime_submission_delayed_ai_verification(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        bath_id = f"ACT-{self.tomorrow.replace('-', '')}-0730-BATH"

        # Submit at 07:38:20 (within 07:20–07:40 window), AI verification pending
        sub_res = self.tw.evaluate_submission_timing(
            bath_id,
            submission_iso=f"{self.tomorrow}T07:38:20+05:30",
            begin_ai_validation=True,
        )
        self.assertTrue(sub_res["allowed"])
        self.assertEqual(sub_res["status"], "VALIDATING")

        # Clock passes 07:45 (past window_end 07:40) while AI is still processing
        dt_after = datetime.strptime(f"{self.tomorrow} 07:45:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        bath_still_validating = self._get_activity_dict(bath_id, self.tomorrow, ref_dt=dt_after)
        self.assertEqual(bath_still_validating["status"], "VALIDATING")

        # AI completion without an approved, retained evidence file remains pending and earns zero.
        ver_res = self.tw.record_activity_verification(
            bath_id,
            is_verified=True,
            awarded_points=10,
            verification_completed_iso=f"{self.tomorrow}T07:46:10+05:30",
        )
        self.assertEqual(ver_res["status"], "SUBMITTED")
        self.assertIn("07:38:20", ver_res["submitted_at"])
        self.assertTrue(ver_res["on_time_retained"])
        self.assertEqual(ver_res["points"], 0)
        self.assertFalse(ver_res["verified"])

    # ── 16. Late proof submission rejected
    def test_16_late_proof_submission_rejected(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        bath_id = f"ACT-{self.tomorrow.replace('-', '')}-0730-BATH"

        # Attempt submission at 07:41 (window closed at 07:40)
        sub_res = self.tw.evaluate_submission_timing(
            bath_id,
            submission_iso=f"{self.tomorrow}T07:41:00+05:30",
        )
        self.assertFalse(sub_res["allowed"])
        self.assertEqual(sub_res["status"], "REJECTED_LATE")

    # ── 17. Missed activity closing at the end of its window
    def test_17_missed_activity_closes_and_cannot_reopen(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        bath_id = f"ACT-{self.tomorrow.replace('-', '')}-0730-BATH"

        # Tick at 07:41 AM -> OPEN transitions through MISSED to CLOSED with 0 points
        dt_expired = datetime.strptime(f"{self.tomorrow} 07:41:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        bath_act = self._get_activity_dict(bath_id, self.tomorrow, ref_dt=dt_expired)
        self.assertEqual(bath_act["status"], "MISSED")
        self.assertEqual(bath_act["lifecycle_state"], "CLOSED")
        self.assertTrue(bath_act["is_closed"])
        self.assertEqual(bath_act["points"], 0)

        # Even admin reschedule cannot move a MISSED/CLOSED activity into the future to get a new proof window
        resched = self.tw.admin_reschedule_routine({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "11:30",  # trying to move missed bath from 07:30 to 11:30
            "gym_time": "18:30",
            "admin_pin": "5678",
            "reason": "Trying to move missed bath",
        })
        self.assertFalse(resched["success"])
        self.assertIn("Cannot move missed activity", resched["error"])

    # ── 18. Deduplicated reminder notifications
    def test_18_deduplicated_reminder_notifications(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        bath_id = f"ACT-{self.tomorrow.replace('-', '')}-0730-BATH"

        # 07:19 AM -> 1-min pre-opening reminder
        dt_0719 = datetime.strptime(f"{self.tomorrow} 07:19:10", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        r1 = self.tw.get_pending_notifications(self.tomorrow, reference_now=dt_0719)
        bath_alerts_1 = [a for a in r1 if a["activity_id"] == bath_id]
        self.assertEqual(len(bath_alerts_1), 1)
        self.assertEqual(bath_alerts_1[0]["event_type"], "PRE_OPENING")

        # Tick again at 07:19:40 -> must NOT duplicate notification
        dt_0719_b = datetime.strptime(f"{self.tomorrow} 07:19:40", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        r1_dup = self.tw.get_pending_notifications(self.tomorrow, reference_now=dt_0719_b)
        bath_alerts_1_dup = [a for a in r1_dup if a["activity_id"] == bath_id]
        self.assertEqual(len(bath_alerts_1_dup), 0)

        # 07:20 AM -> Window opens notification
        dt_0720 = datetime.strptime(f"{self.tomorrow} 07:20:05", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        r2 = self.tw.get_pending_notifications(self.tomorrow, reference_now=dt_0720)
        bath_alerts_2 = [a for a in r2 if a["activity_id"] == bath_id]
        self.assertEqual(len(bath_alerts_2), 1)
        self.assertEqual(bath_alerts_2[0]["event_type"], "OPENED")

        # 07:39 AM -> 1 minute remaining notification
        dt_0739 = datetime.strptime(f"{self.tomorrow} 07:39:05", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        r3 = self.tw.get_pending_notifications(self.tomorrow, reference_now=dt_0739)
        bath_alerts_3 = [a for a in r3 if a["activity_id"] == bath_id]
        self.assertEqual(len(bath_alerts_3), 1)
        self.assertEqual(bath_alerts_3[0]["event_type"], "WARNING_1M")

        # 07:40:05 AM -> Window closed notification
        dt_0740 = datetime.strptime(f"{self.tomorrow} 07:40:05", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        r4 = self.tw.get_pending_notifications(self.tomorrow, reference_now=dt_0740)
        bath_alerts_4 = [a for a in r4 if a["activity_id"] == bath_id]
        self.assertEqual(len(bath_alerts_4), 1)
        self.assertEqual(bath_alerts_4[0]["event_type"], "CLOSED")

    # ── 19. Android and Windows schedule synchronization (HTTP API + Sync Payload)
    def test_19_android_and_windows_schedule_sync(self):
        StrictRecordsHTTPHandler.db = self.db
        StrictRecordsHTTPHandler.engine = self.engine
        server = HTTPServer(("127.0.0.1", 0), StrictRecordsHTTPHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Confirm schedule via HTTP POST /api/schedule/confirm (Windows EXE)
            confirm_payload = json.dumps({
                "schedule_date": self.tomorrow,
                "wake_up_time": "06:45",
                "bath_time": "07:30",
                "gym_time": "18:30",
                "gym_enabled": True,
                "actor": "USER",
                "device_id": "WINDOWS-PC",
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/schedule/confirm",
                data=confirm_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["success"])
                win_sched = data["schedule"]

            # Android uses an authenticated paired-device sync request.
            device_id = "android_sync_test"
            device_token = "test-device-token"
            now_iso = datetime.now().isoformat()
            with self.db._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO devices (device_id, user_id, device_name, platform, app_version, status, sync_status, pairing_state, last_seen) VALUES (?, 'user_lok', 'Android test', 'ANDROID', '2.0.0', 'REGISTERED', 'ONLINE', 'PAIRED', ?)",
                    (device_id, now_iso),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO device_credentials (credential_id, device_id, account_id, token_hash, issued_at, revoked_at) VALUES (?, ?, 'user_lok', ?, ?, NULL)",
                    ("cred_sync_test", device_id, hashlib.sha256(device_token.encode()).hexdigest(), now_iso),
                )
            sync_url = f"http://127.0.0.1:{port}/api/schedule/sync?date={self.tomorrow}&device_id={device_id}&device_token={device_token}"
            with urllib.request.urlopen(sync_url, timeout=5) as resp:
                sync_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(sync_data["success"])
                android_sched = sync_data["schedule"]

            # Verify identical schedule ID, version, state, activity IDs, and window timestamps
            self.assertEqual(win_sched["schedule_id"], android_sched["schedule_id"])
            self.assertEqual(win_sched["schedule_version"], android_sched["schedule_version"])
            self.assertEqual(win_sched["status"], android_sched["status"])
            self.assertEqual(win_sched["wake_up"]["target_time"], "06:45")
            self.assertEqual(android_sched["wake_up"]["target_time"], "06:45")

            act_ids = [a["activity_id"] for a in sync_data["activities"]]
            self.assertIn(f"ACT-{self.tomorrow.replace('-', '')}-0645-WAKEUP", act_ids)
            self.assertIn(f"ACT-{self.tomorrow.replace('-', '')}-0730-BATH", act_ids)
            self.assertIn(f"ACT-{self.tomorrow.replace('-', '')}-1830-GYM", act_ids)
        finally:
            server.shutdown()
            server.server_close()

    # ── 20. Application restart recovery without duplicate activities or points
    def test_20_application_restart_recovery(self):
        self.tw.confirm_next_day_schedule({
            "schedule_date": self.tomorrow,
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
        })
        wake_id = f"ACT-{self.tomorrow.replace('-', '')}-0700-WAKEUP"
        self.tw.evaluate_submission_timing(
            wake_id,
            submission_iso=f"{self.tomorrow}T07:01:00+05:30",
            begin_ai_validation=True,
        )
        self.tw.record_activity_verification(
            wake_id,
            is_verified=True,
            awarded_points=40,
        )

        dt_midday = datetime.strptime(f"{self.tomorrow} 12:05:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        acts_before = self.tw.get_activities_for_date(self.tomorrow, reference_now=dt_midday)
        ledger_before = self.db.get_point_ledger_summary(self.tomorrow)

        # Simulate full application restart by instantiating a new Database & ScoringEngine on the same DB file
        db_restarted = Database(self.db_path)
        engine_restarted = ScoringEngine(db_restarted)
        acts_after = engine_restarted.tw_engine.get_activities_for_date(self.tomorrow, reference_now=dt_midday)
        ledger_after = db_restarted.get_point_ledger_summary(self.tomorrow)

        # Count of activities and verified points must not duplicate
        self.assertEqual(len(acts_before), len(acts_after))
        self.assertEqual(ledger_before["verified_points"], ledger_after["verified_points"])
        self.assertEqual(ledger_after["verified_points"], 0)

        wake_after = [a for a in acts_after if a["activity_id"] == wake_id][0]
        self.assertEqual(wake_after["status"], "SUBMITTED")
        self.assertEqual(wake_after["points"], 0)


if __name__ == "__main__":
    unittest.main()
