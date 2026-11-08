import os
import tempfile
import unittest

from core.database import StrictRecordsDB
from core.models import ScoreEventType
from core.scoring_engine import ScoringEngine


class TestPointsResetAndTransactionsLedger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        self.rules = ScoringEngine(self.db)
        self.test_date = "2026-10-16"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_initial_points_reset_idempotency(self):
        """Verify that initial reset sets balance to exactly 0 and is strictly idempotent."""
        # Seed positive points before reset
        self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.GYM_COMPLETED.value,
            points=8,
            device_id="android_phone",
            description="Gym session completed",
            audit_details={"reason": "Morning workout passed", "human_confirmed": True},
        )

        initial_score = self.rules.calculate_verified_score(self.test_date)
        self.assertEqual(initial_score, 8)

        # Execute initial reset
        res1 = self.rules.execute_initial_points_reset_safely(
            date=self.test_date,
        )
        self.assertTrue(res1["success"])
        self.assertEqual(res1["resulting_balance"], 0)
        self.assertFalse(res1.get("already_applied", False))

        # Authoritative verified balance must now be exactly 0
        new_balance = self.rules.calculate_verified_score(self.test_date)
        self.assertEqual(new_balance, 0)

        # Calling it a second time must be a no-op (idempotent)
        res2 = self.rules.execute_initial_points_reset_safely(
            date=self.test_date,
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2.get("already_applied", False))
        self.assertEqual(self.rules.calculate_verified_score(self.test_date), 0)

    def test_02_unauthorized_negative_points_become_proposals(self):
        """Unauthorized negative points from Windows or external sources must NOT deduct points."""
        self.rules.execute_initial_points_reset_safely(date=self.test_date)
        self.assertEqual(self.rules.calculate_verified_score(self.test_date), 0)

        # Attempt to record unauthorized negative deduction
        res = self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
            points=-10,
            device_id="windows_lok_pc",
            description="Unauthorized deduction attempt from Windows agent",
            audit_details={"reason": "Blocked YouTube music", "originating_device": "Windows EXE"},
        )

        # Must not be finalized; must be marked as pending proposal
        self.assertFalse(res.get("awarded", False))
        self.assertEqual(res.get("status"), "AWAITING_CONFIRMATION")
        self.assertIsNotNone(res.get("proposal_id"))

        # Confirmed balance must REMAIN 0 (no deduction occurred)
        confirmed_balance = self.rules.calculate_verified_score(self.test_date)
        self.assertEqual(confirmed_balance, 0)

        # Deduction proposal must be stored in deduction_proposals table
        proposal = self.db.get_deduction_proposal(res["proposal_id"])
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "AWAITING_CONFIRMATION")
        self.assertEqual(proposal["proposed_points"], -10)

    def test_03_deduction_proposal_approval_and_rejection_workflows(self):
        """Proposals only deduct after explicit authorization. Rejections deduct 0 points."""
        self.rules.execute_initial_points_reset_safely(date=self.test_date)

        # 1. Create a proposal via negative point event
        res1 = self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
            points=-5,
            device_id="windows_lok_pc",
            description="Suspected early shutdown",
            audit_details={"reason": "Early shutdown", "rule": "DisciplineScoringRules.rule_pc_shutdown"},
        )
        pid1 = res1["proposal_id"]

        # Reject it
        rej_res = self.rules.reject_deduction_proposal(
            proposal_id=pid1,
            admin_user="Primary Administrator",
            reason="Evidence showed legitimate shutdown",
        )
        self.assertTrue(rej_res["success"])
        self.assertEqual(self.rules.calculate_verified_score(self.test_date), 0)

        # 2. Test Approval
        res2 = self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
            points=-8,
            device_id="windows_lok_pc",
            description="Confirmed YouTube gameplay access during work window",
            audit_details={"reason": "Confirmed violation", "rule": "DisciplineScoringRules.rule_entertainment_lock"},
        )
        pid2 = res2["proposal_id"]

        app_res = self.rules.approve_deduction_proposal(
            proposal_id=pid2,
            admin_user="Primary Administrator",
            decision_note="Verified by screenshot",
        )
        self.assertTrue(app_res["success"])

        # Confirmed balance must now reflect the deduction: 0 - 8 = -8
        self.assertEqual(self.rules.calculate_verified_score(self.test_date), -8)

    def test_04_gpay_unified_transaction_history_format(self):
        """Unified transaction history must contain human-readable reasons, categories, origins, rules, and statuses."""
        self.rules.execute_initial_points_reset_safely(date=self.test_date)

        # Record a positive event
        self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.WAKEUP_COMPLETED.value,
            points=8,
            device_id="android_phone",
            description="Wake-up checks passed",
            audit_details={"reason": "All required wake-up checks passed", "human_confirmed": True},
        )

        history = self.rules.get_unified_transaction_history(date=self.test_date)
        self.assertEqual(history["confirmed_balance"], 8)
        self.assertIn("transactions", history)
        txs = history["transactions"]

        # Should have at least the initial reset and the wake-up task
        titles = [t["title"] for t in txs]
        self.assertIn("Initial balance reset", titles)
        self.assertIn("Wake-up task completed", titles)

        # Check transaction fields
        wakeup_tx = next(t for t in txs if t["title"] == "Wake-up task completed")
        self.assertEqual(wakeup_tx["formatted_amount"], "+8 pts")
        self.assertEqual(wakeup_tx["reason"], "All required wake-up checks passed")
        self.assertEqual(wakeup_tx["status"], "Approved")
        self.assertEqual(wakeup_tx["category"], "Wake-up")
        self.assertIn("rule_applied", wakeup_tx)
        self.assertIn("timestamp_formatted", wakeup_tx)
        self.assertIn("reference_id", wakeup_tx)

    def test_05_reversal_creates_compensating_ledger_entry(self):
        """Reversing a transaction must create a linked REVERSAL compensating entry."""
        self.rules.execute_initial_points_reset_safely(date=self.test_date)

        # Award points
        ev = self.rules.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.GYM_COMPLETED.value,
            points=8,
            device_id="android_phone",
            description="Gym completed",
            audit_details={"reason": "Gym check-in approved", "human_confirmed": True},
        )
        ev_id = ev["event_id"]
        self.assertEqual(self.rules.calculate_verified_score(self.test_date), 8)

        # Reverse this transaction
        rev_res = self.rules.reverse_transaction(
            event_id=ev_id,
            reason="Incorrect date on gym photo",
            admin_user="Primary Administrator",
        )
        self.assertTrue(rev_res["success"])
        self.assertEqual(rev_res["reversal_points"], -8)
        self.assertEqual(rev_res["new_balance"], 0)

        # History should show reversal entry
        history = self.rules.get_unified_transaction_history(date=self.test_date)
        rev_tx = next(t for t in history["transactions"] if t.get("details", {}).get("event_type") == "REVERSAL")
        self.assertIn(rev_tx["title"].lower(), ["transaction reversal", "transaction reversal".lower()])
        self.assertEqual(rev_tx["amount_formatted"], "-8 pts")
        self.assertEqual(rev_tx.get("details", {}).get("parent_event_id"), ev_id)


if __name__ == "__main__":
    unittest.main()
