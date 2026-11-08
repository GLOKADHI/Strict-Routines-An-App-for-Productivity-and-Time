import json
import os
import tempfile
import unittest
from datetime import datetime

from core.activity_report import _matches_activity, build_daily_activity_report
from core.database import StrictRecordsDB


class DailyActivityReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(db_path=os.path.join(self.temp_dir.name, "report.db"))
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_report_uses_saved_schedule_review_and_point_ledger_without_paths(self):
        activity_id = "activity-hourly-0900"
        evidence_id = "evidence-1"
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, type, title, date, scheduled_time,
                    window_start, window_end, notification_time, status, submitted_at,
                    points, schedule_id, schedule_version, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity_id, "user_lok", "HOURLY", "Hourly Check 09:00",
                    self.today, self.today + "T09:00:00+05:30",
                    self.today + "T08:50:00+05:30", self.today + "T09:10:00+05:30",
                    self.today + "T08:49:00+05:30", "SUBMITTED",
                    self.today + "T09:00:00+05:30", 0, "schedule-1", 3,
                    json.dumps({"hour_slot": "08:00-09:00", "evidence_id": evidence_id}),
                ),
            )
            conn.execute(
                """
                INSERT INTO proof_reviews (
                    review_id, evidence_id, user_id, task_code, date, hour_slot,
                    status, submitted_at, signals_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "review-1", evidence_id, "user_lok", "HOURLY", self.today,
                    "08:00-09:00", "NEEDS_REVIEW",
                    self.today + "T09:00:00+05:30",
                    json.dumps({"decision": "NEEDS_REVIEW", "explanation": "Manual comparison required."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type, device_id,
                    description, points, evidence_id, related_task_id, audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "score-provisional-1", self.today, 0, self.today + "T09:01:00+05:30",
                    "PROOF_PROVISIONAL", "android-test", "Provisional proof points",
                    10, evidence_id, activity_id,
                    json.dumps({"ledger_state": "PROVISIONAL", "activity_id": activity_id, "evidence_id": evidence_id}),
                ),
            )

        report = build_daily_activity_report(
            self.db,
            self.today,
            [{
                "activity_id": activity_id,
                "account_id": "user_lok",
                "type": "HOURLY",
                "date": self.today,
                "title": "Hourly Check 09:00",
                "scheduled_time": self.today + "T09:00:00+05:30",
                "window_start": self.today + "T08:50:00+05:30",
                "window_end": self.today + "T09:10:00+05:30",
                "notification_time": self.today + "T08:49:00+05:30",
                "status": "SUBMITTED",
                "submitted_at": self.today + "T09:00:00+05:30",
                "points": 0,
                "schedule_id": "schedule-1",
                "schedule_version": 3,
                "details_json": json.dumps({"hour_slot": "08:00-09:00", "evidence_id": evidence_id}),
            }],
        )

        item = report["activities"][0]
        self.assertEqual(item["bucket"], "awaiting_admin")
        self.assertEqual(item["evidence"]["evidence_id"], evidence_id)
        self.assertEqual(item["provisional_points"], 10)
        self.assertEqual(item["point_event_ids"], ["score-provisional-1"])
        self.assertEqual(report["summary"]["completed"], 1)
        self.assertEqual(report["summary"]["awaiting_admin"], 1)
        self.assertNotIn("file_path", json.dumps(report))
        self.assertNotIn("private/evidence", json.dumps(report))

    def test_generic_task_code_does_not_match_an_unrelated_hourly_window(self):
        activity = {
            "activity_id": "hourly-0900",
            "type": "HOURLY",
            "title": "Hourly Check 09:00",
            "window_start": self.today + "T08:50:00+05:30",
            "window_end": self.today + "T09:10:00+05:30",
        }
        review = {
            "task_code": "HOURLY",
            "task_title": "Hourly Check",
            "hour_slot": "",
            "submitted_at": self.today + "T11:00:00+05:30",
            "evidence_id": "unrelated",
        }
        self.assertFalse(_matches_activity(review, activity, {}, exact_title_allowed=False))
        review["submitted_at"] = self.today + "T09:00:00+05:30"
        self.assertTrue(_matches_activity(review, activity, {}, exact_title_allowed=False))

    def test_past_unsubmitted_window_reports_as_missed_not_upcoming(self):
        past_activity = {
            "activity_id": "past-hourly-0800",
            "account_id": "user_lok",
            "type": "HOURLY",
            "date": "2026-10-10",
            "title": "Hourly Check 08:00",
            "scheduled_time": "2026-10-10T08:00:00+05:30",
            "window_start": "2026-10-10T07:50:00+05:30",
            "window_end": "2026-10-10T08:10:00+05:30",
            "notification_time": "2026-10-10T07:49:00+05:30",
            "status": "SCHEDULED",
            "submitted_at": None,
            "points": 0,
        }
        report = build_daily_activity_report(
            self.db,
            "2026-10-10",
            [past_activity],
        )
        item = report["activities"][0]
        self.assertEqual(item["status"], "MISSED")
        self.assertEqual(item["bucket"], "missed")
        self.assertEqual(report["summary"]["missed"], 1)
        self.assertEqual(report["summary"]["upcoming"], 0)


if __name__ == "__main__":
    unittest.main()

