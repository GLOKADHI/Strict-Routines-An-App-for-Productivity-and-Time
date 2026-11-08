"""
Tests for StrictRecords Score Consistency, Idempotency, and Audit Reconciliation
Verifies all 12 regression scenarios specified in the Audit & Correction specification:
1. Four Snooze events of -5 result in -20.
2. Six Snooze events of -5 result in -30.
3. Duplicate delivery of any event produces no extra deduction.
4. Stale cached balance cannot overwrite latest authoritative balance.
5. Profile and Rewards screens display identical balances.
6. Legitimate negative balance remains negative on every screen (no clamping).
7. One-time reset brings current daily balance to 0 without deleting history.
8. Repeating the reset operation is idempotent and keeps the balance at 0.
9. Historical summaries and lifetime totals remain preserved.
10. Next snooze applies -5 normally after reset.
11. Midnight reset operates normally.
12. Android and Windows reconcile to the same balance.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta

from core.database import StrictRecordsDB
from core.models import ScoreEventType, ScoreMode, ScoreState
from core.scoring_engine import ScoringEngine


class TestScoreAuditConsistency(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="strictrecords_audit_test_")
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.storage_root = os.path.join(self.test_dir, "STRICTRECORDS")
        os.makedirs(self.storage_root, exist_ok=True)
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.test_date = "2026-10-12"
        self.user_id = "user_lok"
        self.db.ensure_daily_record(self.test_date, user_id=self.user_id)

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_four_snooze_events_result_in_minus_20(self):
        """Scenario 1: Four Snooze events of -5 each result in exactly -20."""
        for i in range(1, 5):
            res = self.engine.record_snooze(
                date=self.test_date,
                device_id="android_test_phone",
                timestamp=f"{self.test_date} 07:0{i}:00",
                event_id=f"evt_snooze_4_{i}",
            )
            self.assertEqual(res["status"], "SNOOZED")

        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -20)
        self.assertEqual(recalc["gross_score"], -20)
        self.assertEqual(recalc["negative_points"], 20)
        self.assertEqual(recalc["snooze_count"], 4)

    def test_02_six_snooze_events_result_in_minus_30(self):
        """Scenario 2: Six Snooze events of -5 each result in exactly -30."""
        for i in range(1, 7):
            res = self.engine.record_snooze(
                date=self.test_date,
                device_id="android_test_phone",
                timestamp=f"{self.test_date} 07:1{i}:00",
                event_id=f"evt_snooze_6_{i}",
            )
            self.assertEqual(res["status"], "SNOOZED")

        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -30)
        self.assertEqual(recalc["gross_score"], -30)
        self.assertEqual(recalc["negative_points"], 30)
        self.assertEqual(recalc["snooze_count"], 6)

    def test_03_duplicate_event_delivery_produces_no_extra_deduction(self):
        """Scenario 3: Replaying the same event ID or identical timestamp produces no extra deduction."""
        first = self.engine.record_snooze(
            date=self.test_date,
            device_id="android_test_phone",
            timestamp=f"{self.test_date} 07:05:00",
            event_id="evt_unique_101",
        )
        self.assertEqual(first["status"], "SNOOZED")
        self.assertEqual(first["score_after"], -5)

        # Retry 1: same event_id
        second = self.engine.record_snooze(
            date=self.test_date,
            device_id="android_test_phone",
            timestamp=f"{self.test_date} 07:05:00",
            event_id="evt_unique_101",
        )
        self.assertTrue(second.get("already_processed"))
        self.assertEqual(second["score_after"], -5)

        # Retry 2: batch sync duplicate delivery with same event_id
        batch_res = self.db.process_sync_events_batch(
            device_id="android_test_phone",
            events=[{
                "event_id": "evt_unique_101",
                "event_type": "SNOOZE",
                "timestamp": f"{self.test_date} 07:05:00",
                "payload": {"date": self.test_date, "timestamp": f"{self.test_date} 07:05:00"},
            }],
        )
        self.assertTrue(batch_res["success"])
        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -5)

    def test_04_stale_cached_balance_cannot_overwrite_latest_authoritative_balance(self):
        """Scenario 4: Authoritative server ledger cannot be regressed by stale client score claims."""
        # Record authoritative deductions on server
        self.engine.record_snooze(self.test_date, timestamp=f"{self.test_date} 07:00:00", event_id="ev_s1")
        self.engine.record_snooze(self.test_date, timestamp=f"{self.test_date} 07:05:00", event_id="ev_s2")
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], -10)

        # Client submits a sync batch claiming an outdated score of 0 or +10
        self.db.process_sync_events_batch(
            device_id="android_test_phone",
            events=[{
                "event_id": "evt_stale_claim",
                "event_type": "RESTRICTION_SYNC",
                "timestamp": f"{self.test_date} 06:50:00",
                "payload": {"score": 0, "client_verified_points": 0},
            }],
        )

        # Authoritative score remains -10
        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -10)

    def test_05_profile_and_rewards_screens_display_identical_balances(self):
        """Scenario 5: Profile read (score) and Rewards read (verified_points) are completely consistent."""
        # Add deductions
        self.engine.record_snooze(self.test_date, timestamp=f"{self.test_date} 07:00:00", event_id="ev_pr_1")
        self.engine.record_snooze(self.test_date, timestamp=f"{self.test_date} 07:05:00", event_id="ev_pr_2")
        self.engine.record_snooze(self.test_date, timestamp=f"{self.test_date} 07:10:00", event_id="ev_pr_3")

        recalc = self.engine.recalculate_daily_score(self.test_date)
        authoritative_score = int(recalc["final_score"])
        self.assertEqual(authoritative_score, -15)

        ledger_summary = self.db.get_point_ledger_summary(self.test_date)
        verified_points = ledger_summary["verified_points"]

        # Both must agree identically! Neither may show 0 while the other shows -15
        self.assertEqual(authoritative_score, verified_points)
        self.assertEqual(verified_points, -15)

    def test_06_legitimate_negative_balance_remains_negative_without_clamping(self):
        """Scenario 6: True negative balances are never clamped to 0 / 100."""
        # 3 violations of -10 each
        for i in range(3):
            self.engine.record_score_event(
                date=self.test_date,
                event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
                points=-10,
                device_id="windows_pc",
                description="YouTube access while locked",
                event_id=f"ev_violation_{i}",
                audit_details={"approval_source": "ANDROID_HUMAN_CONFIRMATION", "approved_by": "test-human-admin"},
            )

        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -30)

        ledger = self.db.get_point_ledger_summary(self.test_date)
        # CRITICAL: verified_points must NOT be clamped to 0
        self.assertEqual(ledger["verified_points"], -30)
        self.assertEqual(ledger["raw_verified_points"], -30)

    def test_07_one_time_reset_brings_balance_to_zero_preserving_history(self):
        """Scenario 7: Safe one-time reset brings balance to 0 without deleting prior deductions."""
        # Start with -30 points from 3 violations
        for i in range(3):
            self.engine.record_score_event(
                date=self.test_date,
                event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
                points=-10,
                device_id="windows_pc",
                description="YouTube access",
                event_id=f"ev_viol_pre_{i}",
                audit_details={"approval_source": "ANDROID_HUMAN_CONFIRMATION", "approved_by": "test-human-admin"},
            )

        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], -30)

        # Apply safe one-time reset
        res = self.engine.reset_current_daily_score_safely(
            date=self.test_date,
            admin_id="primary_admin",
            reason="One-time audit reconciliation to 0",
            operation_id="op_audit_test_7",
        )
        self.assertTrue(res["success"])
        self.assertFalse(res["already_applied"])
        self.assertEqual(res["previous_balance"], -30)
        self.assertEqual(res["correction_amount"], 30)
        self.assertEqual(res["resulting_balance"], 0)
        self.assertEqual(res["persisted_balance"], 0)

        # Verify history is preserved
        with self.db._connect() as conn:
            events = conn.execute(
                "SELECT * FROM score_events WHERE date = ? ORDER BY timestamp ASC",
                (self.test_date,),
            ).fetchall()
            # 3 violations + 1 correction = at least 4 events
            types = [e["event_type"] for e in events]
            self.assertIn(ScoreEventType.ENTERTAINMENT_VIOLATION.value, types)
            self.assertIn(ScoreEventType.SCORE_CORRECTION.value, types)
            self.assertEqual(types.count(ScoreEventType.ENTERTAINMENT_VIOLATION.value), 3)

        # Both score and ledger summary must be exactly 0
        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], 0)
        ledger = self.db.get_point_ledger_summary(self.test_date)
        self.assertEqual(ledger["verified_points"], 0)

    def test_08_repeating_reset_operation_is_idempotent(self):
        """Scenario 8: Retrying the reset operation is idempotent and keeps the balance at 0."""
        # Seed negative score
        self.engine.record_snooze(self.test_date, event_id="ev_snooze_idemp")
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], -5)

        op_id = "op_idemp_test_8"
        first = self.engine.reset_current_daily_score_safely(
            date=self.test_date,
            admin_id="primary_admin",
            reason="Idempotency test",
            operation_id=op_id,
        )
        self.assertTrue(first["success"])
        self.assertEqual(first["resulting_balance"], 0)

        # Call again with same op_id
        second = self.engine.reset_current_daily_score_safely(
            date=self.test_date,
            admin_id="primary_admin",
            reason="Idempotency test",
            operation_id=op_id,
        )
        self.assertTrue(second["success"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(second["correction_amount"], 0)
        self.assertEqual(second["resulting_balance"], 0)
        self.assertEqual(second["persisted_balance"], 0)

        # Final score remains 0, does not double-correct to +5
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], 0)

    def test_09_historical_summaries_and_lifetime_totals_preserved(self):
        """Scenario 9: Resetting today's score preserves prior daily summaries and lifetime totals."""
        prev_date = "2026-10-11"
        self.db.ensure_daily_record(prev_date, user_id=self.user_id)
        # Create an approved proof for yesterday
        self.engine.record_score_event(
            date=prev_date,
            event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
            points=-10,
            device_id="windows_pc",
            description="Prior day event",
            event_id="ev_yesterday_1",
            audit_details={"approval_source": "ANDROID_HUMAN_CONFIRMATION", "approved_by": "test-human-admin"},
        )
        prev_calc = self.engine.recalculate_daily_score(prev_date)
        self.assertEqual(prev_calc["final_score"], -10)

        # Reset today
        self.engine.record_snooze(self.test_date, event_id="ev_today_snooze")
        self.engine.reset_current_daily_score_safely(
            date=self.test_date,
            operation_id="op_preserve_yesterday",
        )

        # Yesterday's record is completely untouched
        yesterday_after = self.engine.recalculate_daily_score(prev_date)
        self.assertEqual(yesterday_after["final_score"], -10)

    def test_10_subsequent_snooze_applies_normally_after_reset(self):
        """Scenario 10: After resetting balance to 0, future Snooze deductions apply -5 normally."""
        # 1. Start with -20
        for i in range(4):
            self.engine.record_snooze(
                self.test_date,
                timestamp=f"{self.test_date} 07:0{i}:00",
                event_id=f"ev_snz_pre_{i}",
            )
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], -20)

        # 2. Reset to 0
        self.engine.reset_current_daily_score_safely(self.test_date, operation_id="op_before_next_snz")
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], 0)

        # 3. Next snooze occurs
        next_snooze = self.engine.record_snooze(
            self.test_date,
            timestamp=f"{self.test_date} 08:30:00",
            event_id="ev_snz_post_reset",
        )
        self.assertEqual(next_snooze["status"], "SNOOZED")
        self.assertEqual(next_snooze["points_delta"], -5)

        # 4. Resulting balance is 0 - 5 = -5
        recalc = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc["final_score"], -5)

    def test_11_midnight_reset_operates_normally(self):
        """Scenario 11: Normal automatic midnight reset establishes new day at 0 while retaining prior day."""
        # Day 1: score -15
        for i in range(3):
            self.engine.record_snooze(
                self.test_date,
                timestamp=f"{self.test_date} 07:0{i}:00",
                event_id=f"ev_day1_snz_{i}",
            )
        self.assertEqual(self.engine.recalculate_daily_score(self.test_date)["final_score"], -15)

        # Midnight transition to next day
        next_date = "2026-10-13"
        self.db.ensure_daily_record(next_date, user_id=self.user_id)
        midnight_res = self.db.execute_midnight_reset(next_date, user_id=self.user_id)
        self.assertTrue(midnight_res["success"])

        # New day starts at 0
        new_day = self.engine.recalculate_daily_score(next_date)
        self.assertEqual(new_day["final_score"], 0)

        # Prior day remains preserved at -15
        day1 = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(day1["final_score"], -15)

    def test_12_android_and_windows_reconcile_to_same_balance(self):
        """Scenario 12: Android verified scoring and Windows ScoringEngine reconcile to identical balances."""
        # Simulated mixed day: 1 Snooze (-5), 1 Violation (-10), 1 Correction (+15)
        events = [
            {"event_type": "SNOOZE", "points": -5, "evidence_id": ""},
            {"event_type": "ENTERTAINMENT_VIOLATION", "points": -10, "evidence_id": ""},
            {"event_type": "SCORE_CORRECTION", "points": 15, "evidence_id": ""},
        ]

        # Windows ScoringEngine
        for i, ev in enumerate(events):
            self.engine.record_score_event(
                date=self.test_date,
                event_type=ev["event_type"],
                points=ev["points"],
                device_id="mixed_device",
                description="Simulated event",
                event_id=f"ev_sim_{i}",
                audit_details={"approval_source": "ANDROID_HUMAN_CONFIRMATION", "approved_by": "test-human-admin"},
            )

        win_score = self.engine.recalculate_daily_score(self.test_date)["final_score"]
        win_ledger = self.db.get_point_ledger_summary(self.test_date)["verified_points"]

        # Android calculation logic:
        # positive proof allowed only for whitelisted events (including SCORE_CORRECTION)
        def android_calculate_verified_score(ev_list):
            pos = 0
            neg = 0
            for e in ev_list:
                pts = e["points"]
                et = e["event_type"]
                if pts <= 0:
                    neg += abs(pts)
                elif et in ("PROOF_APPROVED", "PC_SHUTDOWN_BONUS", "SCORE_CORRECTION", "ADMIN_SCORE_ADJUSTMENT"):
                    pos += pts
            raw = pos - neg
            return min(100, raw)

        android_score = android_calculate_verified_score(events)

        # Both must equal exactly 0
        self.assertEqual(win_score, 0)
        self.assertEqual(win_ledger, 0)
        self.assertEqual(android_score, 0)
        self.assertEqual(win_score, android_score)

    def test_13_withdraw_gym_skip_restores_balance_and_status(self):
        """Scenario 13: Withdrawing gym skip removes penalty, resets status to UPCOMING, and restores score."""
        # 1. Skip gym -> -10 points
        skip_res = self.engine.skip_gym_session(self.test_date, timestamp=f"{self.test_date} 18:00:00")
        self.assertEqual(skip_res["points"], -10)
        recalc_skipped = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc_skipped["final_score"], -10)

        # 2. Withdraw skip -> 0 points, UPCOMING status
        withdraw_res = self.engine.withdraw_gym_skip(self.test_date)
        self.assertTrue(withdraw_res["success"])
        self.assertEqual(withdraw_res["status"], "UPCOMING")
        recalc_restored = self.engine.recalculate_daily_score(self.test_date)
        self.assertEqual(recalc_restored["final_score"], 0)

        # Check gym_sessions table
        with self.db._connect() as conn:
            row = conn.execute("SELECT status FROM gym_sessions WHERE date = ?", (self.test_date,)).fetchone()
            self.assertEqual(row["status"], "UPCOMING")
            # Verify no GYM_SKIPPED in score_events
            skips = conn.execute("SELECT * FROM score_events WHERE date = ? AND event_type = 'GYM_SKIPPED'", (self.test_date,)).fetchall()
            self.assertEqual(len(skips), 0)


if __name__ == "__main__":
    unittest.main()
