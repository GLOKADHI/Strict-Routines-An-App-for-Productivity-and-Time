import unittest
from datetime import datetime

from core.time_window_engine import IST, TimeWindowEngine, iso_to_dt
from tests import test_daily_routine_gate as fixtures


class HourlyWindowRestoreTests(unittest.TestCase):
    date = fixtures.DailyRoutineGateTests.date
    setUp = fixtures.DailyRoutineGateTests.setUp
    tearDown = fixtures.DailyRoutineGateTests.tearDown
    save_checks = fixtures.DailyRoutineGateTests.save_checks

    def slots(self, target="09:00", **kwargs):
        return [a for a in self.tw.generate_daily_schedule(self.date, wakeup_time=target, **kwargs) if a["type"] == "HOURLY"]

    def test_first_window_and_hourly_recurrence(self):
        slots = self.slots()
        self.assertEqual([a["scheduled_time"][11:16] for a in slots[:3]], ["09:00", "10:00", "11:00"])
        self.assertEqual(slots[0]["window_start"][11:16], "08:50")
        self.assertEqual(slots[0]["window_end"][11:16], "09:10")
        self.assertEqual(slots[0]["notification_time"][11:16], "08:49")
        for previous, current in zip(slots, slots[1:]):
            self.assertEqual((iso_to_dt(current["scheduled_time"]) - iso_to_dt(previous["scheduled_time"])).total_seconds(), 3600)

    def test_nonzero_minutes_and_timezone_midnight(self):
        slots = self.slots("07:30", schedule_id="confirmed")
        self.assertEqual(slots[0]["window_start"][11:16], "07:20")
        self.assertEqual(slots[1]["window_start"][11:16], "08:20")
        self.assertEqual(slots[0]["notification_time"][11:16], "07:19")
        midnight = self.slots("00:05", timezone_name="America/New_York")[0]
        self.assertTrue(midnight["window_start"].startswith("2026-10-11T23:55:00-04:00"))
        self.assertTrue(midnight["window_end"].startswith("2026-10-12T00:15:00-04:00"))

    def test_planned_windows_visible_without_unapproved_attendance(self):
        now = datetime(2026, 10, 12, 9, 0, tzinfo=IST)
        acts = self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        slots = [a for a in acts if a["type"] == "HOURLY"]
        self.assertTrue(slots)
        self.assertTrue(all(a['status']!='BLOCKED_WAKEUP' for a in slots))
        self.assertEqual(self.db.get_score_events(self.date), [])
        with self.db._connect() as conn:
            self.assertGreater(conn.execute("SELECT count(*) FROM hourly_records WHERE date=?", (self.date,)).fetchone()[0], 0)

    def test_eligible_window_countdown_boundaries_and_restart_dedup(self):
        self.save_checks(completed_at=f"{self.date} 07:30:00")
        now = datetime(2026, 10, 12, 8, 49, tzinfo=IST)
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET reviewed_at=? WHERE date=?", (f"{self.date} 07:30:00", self.date))
        self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        reminder = self.tw.get_pending_notifications(self.date, reference_now=now)
        self.assertTrue(any(a.get("task_type") == "HOURLY_CHECK_IN" and a["event_id"].endswith("-OPENING") for a in reminder))
        from core.time_window_engine import TimeWindowEngine
        restarted = TimeWindowEngine(self.db)
        restarted.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        self.assertFalse(any(a.get("task_type") == "HOURLY_CHECK_IN" for a in restarted.get_pending_notifications(self.date, reference_now=now)))
        countdown = restarted.get_current_countdown_state(self.date, reference_now=datetime(2026, 10, 12, 9, 0, tzinfo=IST))
        # Wake-up may also be open; select the hourly activity to test its exact deadline.
        slot = next(a for a in restarted.get_activities_for_date(self.date, reference_now=now) if a["type"] == "HOURLY" and a["scheduled_time"][11:16] == "09:00")
        from unittest.mock import patch
        with patch("core.time_window_engine.now_ist", return_value=iso_to_dt(slot["window_end"])):
            result = restarted.evaluate_submission_timing(slot["activity_id"], slot["window_end"])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["points"], 0)

    def test_future_plan_change_preserves_submitted_evidence(self):
        now = datetime(2026, 10, 12, 7, 0, tzinfo=IST)
        self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        with self.db._connect() as conn:
            conn.execute("UPDATE scheduled_activities SET status='SUBMITTED',submitted_at=?,details_json='{}' WHERE activity_id='ACT-20261012-0900-HOURLY'", (f"{self.date}T08:55:00+05:30",))
        self.tw.ensure_activities_in_db(self.date, wakeup_time="09:30", reference_now=now)
        with self.db._connect() as conn:
            old = conn.execute("SELECT status,submitted_at FROM scheduled_activities WHERE activity_id='ACT-20261012-0900-HOURLY'").fetchone()
            future = conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='ACT-20261012-1000-HOURLY'").fetchone()
        self.assertEqual(old["status"], "SUBMITTED")
        self.assertTrue(old["submitted_at"])
        self.assertEqual(future["status"], "CANCELLED")
        self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        with self.db._connect() as conn:
            restored = conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='ACT-20261012-1000-HOURLY'").fetchone()
        self.assertIn(restored["status"], ("BLOCKED_WAKEUP", "SCHEDULED", "UPCOMING"))
