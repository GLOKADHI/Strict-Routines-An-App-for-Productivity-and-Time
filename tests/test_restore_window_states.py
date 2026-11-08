import unittest
from datetime import datetime

from tests import test_daily_routine_gate as fixtures
from core.time_window_engine import IST


class RestoredWindowStatesTests(unittest.TestCase):
    date = fixtures.DailyRoutineGateTests.date
    setUp = fixtures.DailyRoutineGateTests.setUp
    tearDown = fixtures.DailyRoutineGateTests.tearDown

    def test_past_unapproved_slot_is_expired_not_upcoming(self):
        now = datetime(2026, 10, 12, 18, 0, tzinfo=IST)
        rows = self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        row = next(a for a in rows if a["activity_id"] == "ACT-20261012-0900-HOURLY")
        self.assertEqual(row["status"], "MISSED")
        self.assertTrue(row['is_closed'])
        self.assertEqual(self.db.get_score_events(self.date), [])

    def test_current_window_open_separate_from_approval(self):
        now = datetime(2026, 10, 12, 9, 0, tzinfo=IST)
        rows = self.tw.ensure_activities_in_db(self.date, wakeup_time="09:00", reference_now=now)
        row = next(a for a in rows if a["activity_id"] == "ACT-20261012-0900-HOURLY")
        self.assertEqual(row["status"], "OPEN")
        self.assertTrue(row["submission_eligible"])
        self.assertEqual(row["eligibility_status"], "ELIGIBLE")

    def test_morning_cutoff_is_0930(self):
        window = self.tw.calculate_wakeup_activity_window(self.date, "09:00")
        self.assertEqual(window["window_end"], f"{self.date}T09:30:00+05:30")

    def test_submitted_window_keeps_submission_after_deadline(self):
        self.tw.ensure_activities_in_db(self.date, reference_now=datetime(2026, 10, 12, 8, 0, tzinfo=IST))
        with self.db._connect() as conn:
            conn.execute("UPDATE scheduled_activities SET status='SUBMITTED',submitted_at=? WHERE activity_id='ACT-20261012-0900-HOURLY'", (f"{self.date}T09:00:00+05:30",))
        row = next(a for a in self.tw.get_activities_for_date(self.date, reference_now=datetime(2026, 10, 12, 18, 0, tzinfo=IST)) if a["activity_id"] == "ACT-20261012-0900-HOURLY")
        self.assertEqual(row["status"], "SUBMITTED")
