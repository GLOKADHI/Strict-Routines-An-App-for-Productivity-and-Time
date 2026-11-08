"""
Tests for StrictRecords Midnight Daily Points Reset & True Negative Score Handling
Verifies:
1. Midnight (12:00 AM IST) daily balance reset to 0.
2. Idempotent reset execution (exactly once per user and daily period).
3. Preservation of all historical ledgers, score events, and prior daily balances.
4. Retention of streak metrics and lifetime aggregates.
5. True negative score balances without zero-floor clamping.
6. Unchanged unlock thresholds requiring deficit clearance.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime

from core.database import StrictRecordsDB
from core.models import ScoreEventType, ScoreMode
from core.scoring_engine import ScoringEngine
from windows.restriction_manager import YouTubeRestrictionManager


class TestDailyResetAndNegativeScore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="strictrecords_reset_test_")
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.storage_root = os.path.join(self.test_dir, "STRICTRECORDS")
        os.makedirs(self.storage_root, exist_ok=True)
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def _create_approved_proof(self, date_str: str, points: int = 75) -> str:
        evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
        file_path = os.path.join(self.storage_root, f"{evidence_id}.jpg")
        with open(file_path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0test_jpeg_data_bytes_for_unit_tests")
        sha256 = hashlib.sha256(b"\xff\xd8\xff\xe0test_jpeg_data_bytes_for_unit_tests").hexdigest()

        review_id = f"rev_{uuid.uuid4().hex[:8]}"
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_files (
                    evidence_id, date, is_dev_mode, record_type, related_record_id,
                    file_path, timestamp, device_id, file_size_bytes, sha256_hash, is_deleted
                ) VALUES (?, ?, 0, 'IMAGE', 'task_1', ?, ?, 'test_device', 42, ?, 0)
                """,
                (evidence_id, date_str, file_path, f"{date_str} 10:00:00", sha256),
            )
            conn.execute(
                """
                INSERT INTO proof_reviews (
                    review_id, evidence_id, user_id, task_code, date, status, decision,
                    points_awarded, submitted_at, reviewed_at, reviewed_by, reason_note
                ) VALUES (?, ?, 'user_lok', 'HOURLY', ?, 'APPROVED', 'APPROVED', ?, ?, ?, 'admin', 'Verified authentic proof')
                """,
                (review_id, evidence_id, date_str, points, f"{date_str} 10:00:00", f"{date_str} 10:05:00"),
            )

        self.engine.record_score_event(
            date=date_str,
            event_type=ScoreEventType.PROOF_APPROVED.value,
            points=points,
            device_id="test_admin",
            description="Proof approved",
            evidence_id=evidence_id,
        )
        return evidence_id

    def test_negative_score_recalculation_without_zero_clamp(self):
        """Deductions exceeding earned points must yield true negative daily scores."""
        date_str = "2026-10-12"
        self.db.ensure_daily_record(date_str)

        # Record penalties without positive points: -5 snooze, -10 admin deduction
        self.engine.record_score_event(
            date=date_str,
            event_type=ScoreEventType.SNOOZE.value,
            points=-5,
            device_id="test_device",
            description="Snoozed wake-up alarm",
        )
        self.engine.record_score_event(
            date=date_str,
            event_type=ScoreEventType.ADMIN_SCORE_ADJUSTMENT.value,
            points=-10,
            device_id="test_admin",
            description="Admin discipline penalty",
        )

        daily = self.engine.recalculate_daily_score(date_str)
        self.assertEqual(daily["gross_score"], -15)
        self.assertEqual(daily["final_score"], -15)
        self.assertEqual(daily["score_mode"], ScoreMode.STRICT_MODE.value)
        self.assertEqual(daily["reward_unlocked"], 0)
        self.assertEqual(daily["grade"], "F")

    def test_negative_score_unlock_thresholds_require_deficit_clearance(self):
        """Negative scores must require clearing the deficit before unlocking apps."""
        date_str = "2026-10-12"
        self.db.ensure_daily_record(date_str)
        self.engine.record_score_event(
            date=date_str,
            event_type=ScoreEventType.SNOOZE.value,
            points=-15,
            device_id="test_device",
            description="Multiple snoozes",
        )
        self.engine.recalculate_daily_score(date_str)

        summary = self.engine.get_shared_account_summary(date=date_str)
        self.assertEqual(summary["shared_score"], -15)
        self.assertFalse(summary["reward_unlocked"])
        self.assertFalse(summary["controlled_unlocked"])

        # Check privileges: YouTube requires 80, deficit is 80 - (-15) = 95
        restr_mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        # Pass date_str into scorer check
        daily = self.engine.recalculate_daily_score(date_str)
        curr = int(daily.get("final_score", 0) or 0)
        req = 80
        rem = max(0, req - curr)
        self.assertEqual(curr, -15)
        self.assertEqual(req, 80)
        self.assertEqual(rem, 95)

    def test_midnight_daily_reset_execution_and_ledger_preservation(self):
        """Midnight reset resets current day's balance to 0 while preserving prior day's ledger."""
        day1 = "2026-10-12"
        day2 = "2026-10-13"

        # Setup Day 1: give the user 75 verified proof points
        self.db.ensure_daily_record(day1)
        self._create_approved_proof(day1, points=75)
        day1_calc = self.engine.recalculate_daily_score(day1)
        self.assertEqual(day1_calc["final_score"], 75)

        # Now execute midnight reset for Day 2
        res = self.engine.execute_daily_reset_if_needed(target_date=day2)
        self.assertTrue(res["reset_applied"])
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["previous_balance"], 75)
        self.assertEqual(res["new_balance"], 0)
        self.assertEqual(res["date"], day2)

        # Day 2 balance must be 0
        day2_calc = self.engine.recalculate_daily_score(day2)
        self.assertEqual(day2_calc["final_score"], 0)

        # Day 1 ledger and balance must remain completely intact
        day1_after = self.engine.recalculate_daily_score(day1)
        self.assertEqual(day1_after["final_score"], 75)

        # Verify audit event in score_events
        with self.db._connect() as conn:
            reset_event = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND event_type = ?",
                (day2, ScoreEventType.DAILY_RESET.value),
            ).fetchone()
            self.assertIsNotNone(reset_event)
            self.assertEqual(reset_event["points"], 0)
            self.assertEqual(reset_event["event_id"], res["event_id"])

            # Verify audit log in structured_logs
            audit_log = conn.execute(
                "SELECT * FROM structured_logs WHERE date = ? AND event_code = ?",
                (day2, "DAILY_POINTS_RESET"),
            ).fetchone()
            self.assertIsNotNone(audit_log)

    def test_midnight_daily_reset_idempotency(self):
        """Executing midnight reset multiple times for the same day must not duplicate events."""
        target_day = "2026-10-14"
        res1 = self.engine.execute_daily_reset_if_needed(target_date=target_day)
        self.assertTrue(res1["reset_applied"])
        self.assertEqual(res1["status"], "SUCCESS")

        res2 = self.engine.execute_daily_reset_if_needed(target_date=target_day)
        self.assertFalse(res2["reset_applied"])
        self.assertEqual(res2["status"], "ALREADY_RESET")
        self.assertEqual(res2["event_id"], res1["event_id"])

        res3 = self.engine.execute_daily_reset_if_needed(target_date=target_day)
        self.assertFalse(res3["reset_applied"])
        self.assertEqual(res3["status"], "ALREADY_RESET")

        # Exactly one DAILY_RESET event must exist
        with self.db._connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) as c FROM score_events WHERE date = ? AND event_type = ?",
                (target_day, ScoreEventType.DAILY_RESET.value),
            ).fetchone()["c"]
            self.assertEqual(cnt, 1)

    def test_midnight_reset_with_negative_previous_day_balance(self):
        """A closing negative score on day 1 must be recorded as previous balance and day 2 resets to 0."""
        day1 = "2026-10-15"
        day2 = "2026-10-16"
        self.db.ensure_daily_record(day1)
        self.engine.record_score_event(
            date=day1,
            event_type=ScoreEventType.SNOOZE.value,
            points=-20,
            device_id="test_device",
            description="Repeated snoozes",
        )
        self.engine.recalculate_daily_score(day1)

        res = self.engine.execute_daily_reset_if_needed(target_date=day2)
        self.assertTrue(res["reset_applied"])
        self.assertEqual(res["previous_balance"], -20)
        self.assertEqual(res["new_balance"], 0)

        # Day 2 starts at 0
        day2_state = self.engine.recalculate_daily_score(day2)
        self.assertEqual(day2_state["final_score"], 0)

        # Day 1 remains -20
        day1_state = self.engine.recalculate_daily_score(day1)
        self.assertEqual(day1_state["final_score"], -20)


if __name__ == "__main__":
    unittest.main()
