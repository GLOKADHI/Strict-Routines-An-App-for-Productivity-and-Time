"""
Integration & Regression Tests for Reliable Proof Submission with Asynchronous Evaluation.
Verifies:
1. Stage A: Immediate acceptance of Hourly Proof with 0 preliminary point impact.
2. Stage B: Asynchronous evaluation completes, awarding authoritative points.
3. Idempotent Retry: Re-delivering the same sync packet does not double-award points.
4. Gym Proof Submission: Stage A marks status SUBMITTED, Stage B evaluates proof.
5. Sleep Bonus Sync: Authoritative sleep bonus sync event is processed idempotently.
"""

import os
import sys
import json
import base64
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.daily_routine_gate import DailyRoutineGate
from core.time_window_engine import TimeWindowEngine, IST, now_ist
from core.ai_robot_admin import StrictBotAIReviewer


class TestReliableProofSubmissionAsync(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="strictrecords_async_sub_")
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.test_dir)
        self.twe = TimeWindowEngine(self.db)
        self.today = datetime.now(IST).strftime("%Y-%m-%d")
        DailyRoutineGate.seed_approved_wakeup(self.db, self.today)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _get_confirmed_points(self) -> int:
        score_info = self.engine.recalculate_daily_score(self.today)
        return score_info.get("final_score", 0)

    def test_hourly_stage_a_acceptance_zero_score_impact(self):
        """
        Stage A: When an hourly submission is accepted and synced from Android,
        the activity status is marked SUBMITTED, but confirmed points must NOT increase
        until evaluation is completed.
        """
        initial_score = self._get_confirmed_points()

        activities = self.twe.ensure_activities_in_db(self.today)
        act = [a for a in activities if a["type"] == "HOURLY"][0]
        act_id = act["activity_id"]

        sub_id = "sub_and_test_async_01"
        sync_payload = {
            "submission_id": sub_id,
            "evidence_id": "ev_test_async_01",
            "activity_id": act_id,
            "task_type": "HOURLY",
            "date": self.today,
            "is_dev_mode": False,
            "schedule_version": 1,
            "hour_slot": "10:00-11:00",
            "primary_activity": "STUDY",
            "description": "Reviewed system architecture and async patterns",
            "points": 8
        }
        event = {
            "event_id": "evt_and_sub_01",
            "event_type": "HOURLY_PROOF_SUBMITTED",
            "timestamp": datetime.now().isoformat(),
            "payload": sync_payload
        }

        # Stage A sync processing
        res = self.db.process_sync_events_batch(device_id="and_dev_test", events=[event])
        self.assertTrue(res["success"])

        # Scheduled activity is SUBMITTED
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT status, submitted_at FROM scheduled_activities WHERE activity_id = ?",
                (act_id,)
            ).fetchone()
            self.assertEqual(row["status"], "SUBMITTED")
            self.assertIsNotNone(row["submitted_at"])

        # Confirmed points remain completely unchanged in Stage A
        score_after_stage_a = self._get_confirmed_points()
        self.assertEqual(score_after_stage_a, initial_score)

    def test_hourly_stage_b_evaluation_and_idempotency(self):
        """
        Stage B: Evaluation completes and awards points. Re-submitting the same
        sync packet must NOT create duplicate score entries or re-award points.
        """
        initial_score = self._get_confirmed_points()
        activities = self.twe.ensure_activities_in_db(self.today)
        act = [a for a in activities if a["type"] == "HOURLY"][0]
        act_id = act["activity_id"]
        sub_id = "sub_and_idempotent_02"

        event = {
            "event_id": "evt_idempotent_01",
            "event_type": "HOURLY_PROOF_SUBMITTED",
            "timestamp": datetime.now().isoformat(),
            "payload": {
                "submission_id": sub_id,
                "evidence_id": "ev_idempotent_02",
                "activity_id": act_id,
                "task_type": "HOURLY",
                "date": self.today,
                "hour_slot": "11:00-12:00",
                "primary_activity": "CODING",
                "description": "Implemented background thread isolation"
            }
        }

        # First delivery
        self.db.process_sync_events_batch(device_id="and_dev_test", events=[event])

        # Stage B evaluation approval (+8 pts)
        score_event = {
            "event_id": f"se_approved_ev_idempotent_02",
            "event_type": "POINTS_AWARDED",
            "timestamp": datetime.now().isoformat(),
            "payload": {
                "event_id": f"se_approved_ev_idempotent_02",
                "submission_id": sub_id,
                "activity_id": act_id,
                "date": self.today,
                "points": 8,
                "description": "Proof approved after async analysis"
            }
        }
        self.db.process_sync_events_batch(device_id="and_dev_test", events=[score_event])
        score_after_eval = self._get_confirmed_points()
        self.assertEqual(score_after_eval, initial_score + 8)

        # Re-delivery of the exact same event
        retry_res = self.db.process_sync_events_batch(device_id="and_dev_test", events=[event, score_event])
        self.assertTrue(retry_res["success"])

        # Score must remain unchanged (idempotent, no double count)
        score_after_retry = self._get_confirmed_points()
        self.assertEqual(score_after_retry, initial_score + 8)

    def test_gym_submission_lifecycle(self):
        """
        Gym proof submission:
        - Stage A acceptance marks scheduled_activities and daily_state SUBMITTED.
        - Points are only confirmed after evaluation.
        """
        initial_score = self._get_confirmed_points()
        gym_act_id = f"ACT-{self.today.replace('-', '')}-1800-GYM"

        # Ensure gym activity exists
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scheduled_activities(activity_id, account_id, type, date, scheduled_time, window_start, window_end, notification_time, status) "
                "VALUES (?,'user_lok','GYM',?,?,?,?,?,'UPCOMING')",
                (gym_act_id, self.today, f"{self.today}T18:00:00+05:30", f"{self.today}T18:00:00+05:30", f"{self.today}T20:00:00+05:30", f"{self.today}T17:59:00+05:30")
            )

        event = {
            "event_id": "evt_gym_sub_01",
            "event_type": "HOURLY_PROOF_SUBMITTED",
            "timestamp": datetime.now().isoformat(),
            "payload": {
                "submission_id": "sub_gym_01",
                "evidence_id": "ev_gym_01",
                "activity_id": gym_act_id,
                "task_type": "GYM",
                "date": self.today,
                "hour_slot": "19:40-20:40",
                "category": "GYM",
                "description": "Gym workout photo proof"
            }
        }

        # Stage A sync
        res = self.db.process_sync_events_batch(device_id="and_dev_test", events=[event])
        self.assertTrue(res["success"])

        # No premature points in Stage A
        self.assertEqual(self._get_confirmed_points(), initial_score)

        # Stage B evaluation approval (+20 pts)
        score_event = {
            "event_id": "se_approved_ev_gym_01",
            "event_type": "POINTS_AWARDED",
            "timestamp": datetime.now().isoformat(),
            "payload": {
                "event_id": "se_approved_ev_gym_01",
                "submission_id": "sub_gym_01",
                "activity_id": gym_act_id,
                "date": self.today,
                "points": 20,
                "description": "Gym workout verified"
            }
        }
        self.db.process_sync_events_batch(device_id="and_dev_test", events=[score_event])
        self.assertEqual(self._get_confirmed_points(), initial_score + 20)

    def test_sleep_bonus_sync_and_idempotency(self):
        """
        Sleep submission:
        - When sleep bonus is finalized in Android, it syncs a SCORE_EVENT with points.
        - Re-delivering or retrying the sleep bonus sync event is strictly idempotent.
        """
        initial_score = self._get_confirmed_points()
        bonus_event_id = f"se_sleep_bonus_{self.today}"

        sleep_event = {
            "event_id": bonus_event_id,
            "event_type": "SCORE_EVENT",
            "timestamp": datetime.now().isoformat(),
            "payload": {
                "event_id": bonus_event_id,
                "date": self.today,
                "points": 5,
                "event_type": "SCORE_EVENT",
                "description": "Mi Fitness sleep onset bonus (<=00:00 onset, wake <=09:30 AM)",
                "audit": {
                    "source": "ANDROID",
                    "approval_source": "ANDROID_ADMIN",
                    "synced_from_android": True
                }
            }
        }

        # Process sleep bonus sync
        res = self.db.process_sync_events_batch(device_id="and_dev_test", events=[sleep_event])
        self.assertTrue(res["success"])
        self.assertEqual(self._get_confirmed_points(), initial_score + 5)

        # Retry/re-delivery of the exact same event
        res_retry = self.db.process_sync_events_batch(device_id="and_dev_test", events=[sleep_event])
        self.assertTrue(res_retry["success"])
        # Score must remain exactly 5 pts higher, no double credit
        self.assertEqual(self._get_confirmed_points(), initial_score + 5)


if __name__ == "__main__":
    unittest.main()
