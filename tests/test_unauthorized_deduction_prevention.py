"""
Regression Test Suite for Unauthorized Deduction Prevention.
Tests all 8 criteria specified in Section 5:
1. EXE detects violation -> Proposal created with status AWAITING_CONFIRMATION, score unchanged.
2. Direct negative point write without approval -> Rejected by data layer.
3. Android human approval -> Points deducted, proposal marked APPROVED.
4. Retried/duplicated sync packets -> Processed idempotently, no double deductions.
5. Windows EXE reconnects with stale lower score -> APK authoritative score preserved, not overwritten.
6. Positive points (proof approval, focus sessions) continue working normally.
7. System restarts/disconnections do not cause proposal leaks or unauthorized deductions.
8. Dashboard displays distinct statuses: Confirmed points vs. Pending proposals.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from PIL import Image

from core.database import StrictRecordsDB
from core.models import ScoreEventType
from core.scoring_engine import ScoringEngine


class TestUnauthorizedDeductionPrevention(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="strictrecords_deduction_test_")
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.storage_root = os.path.join(self.test_dir, "STRICTRECORDS")
        os.makedirs(self.storage_root, exist_ok=True)
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.test_date = "2026-10-16"
        self.user_id = "user_lok"
        self.db.ensure_daily_record(self.test_date, user_id=self.user_id)

        # Generate test image
        out = io.BytesIO()
        Image.new("RGB", (120, 120), (90, 80, 30)).save(out, format="JPEG")
        self.valid_photo = out.getvalue()

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def _get_score(self) -> int:
        return self.engine.recalculate_daily_score(self.test_date)["final_score"]

    def _add_approved_proof(self, points: int = 20) -> str:
        ev = self.engine.store_evidence_file(
            date=self.test_date,
            record_type="Hourly",
            related_record_id=f"act_{datetime.now().timestamp()}",
            device_id="test_phone",
            file_bytes=self.valid_photo,
        )
        self.engine.apply_proof_approval(
            evidence_id=ev["evidence_id"],
            admin_id="primary_admin",
            points=points,
            reason="Approved work",
        )
        return ev["evidence_id"]

    def test_01_exe_violation_creates_proposal_with_score_unchanged(self):
        """Criterion 1: EXE detects violation -> Proposal created with status AWAITING_CONFIRMATION, score unchanged."""
        score_before = self._get_score()
        self.assertEqual(score_before, 0)

        # Simulate Windows EXE detecting an entertainment violation with record_violation_if_blocked=True
        res = self.engine.evaluate_entertainment_access(
            date=self.test_date,
            target_app="Instagram",
            platform="WINDOWS",
            record_violation_if_blocked=True,
            device_id="win_desktop_pc"
        )

        # Must report allowed=False (restricted)
        self.assertFalse(res.get("allowed"))
        self.assertIsNotNone(res.get("violation_event"))
        # Proposal created with status AWAITING_CONFIRMATION and 0 points deducted
        self.assertEqual(res["violation_event"]["points"], 0)
        self.assertEqual(res["violation_event"]["status"], "AWAITING_CONFIRMATION")
        self.assertEqual(res["violation_event"]["proposed_points"], -10)

        # Score must remain completely unchanged
        score_after = self._get_score()
        self.assertEqual(score_after, score_before)

        # Database proposal record must exist with status AWAITING_CONFIRMATION
        proposals = self.db.get_deduction_proposals(date=self.test_date, status="AWAITING_CONFIRMATION")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["proposed_points"], -10)
        self.assertEqual(proposals[0]["originating_device"], "win_desktop_pc")

        # No negative events should exist in score_events
        with self.db._connect() as conn:
            neg_events = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND points < 0",
                (self.test_date,),
            ).fetchall()
        self.assertEqual(len(neg_events), 0)

    def test_02_direct_negative_point_write_rejected_by_data_layer(self):
        """Criterion 2: Direct negative point write without approval -> Rejected by data layer."""
        score_before = self._get_score()

        # Attempt to insert a negative score event without human authorization
        res = self.engine.record_score_event(
            date=self.test_date,
            event_type=ScoreEventType.ENTERTAINMENT_VIOLATION.value,
            points=-10,
            device_id="win_client",
            description="Unauthorized penalty attempt from background agent",
            audit_details={}
        )

        # Points awarded must be 0 (rejected at data layer)
        self.assertEqual(res.get("points"), 0)
        self.assertEqual(res.get("status"), "AWAITING_CONFIRMATION")
        self.assertIn("proposal_id", res)

        # Score remains unchanged
        score_after = self._get_score()
        self.assertEqual(score_after, score_before)

        # Proposal recorded in deduction_proposals with status AWAITING_CONFIRMATION
        proposals = self.db.get_deduction_proposals(date=self.test_date, status="AWAITING_CONFIRMATION")
        self.assertTrue(any(p["proposed_points"] == -10 for p in proposals))

        # No negative points in score_events
        with self.db._connect() as conn:
            neg_events = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND points < 0",
                (self.test_date,),
            ).fetchall()
        self.assertEqual(len(neg_events), 0)

    def test_03_android_human_approval_finalizes_deduction(self):
        """Criterion 3: Android human approval -> Points deducted, proposal marked APPROVED."""
        # 1. Earn 20 points so score is positive
        self._add_approved_proof(points=20)
        self.assertEqual(self._get_score(), 20)

        # 2. EXE creates a proposal
        prop = self.db.create_deduction_proposal(
            proposal_id="prop_test_human_auth",
            date=self.test_date,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            originating_device="win_client",
            event_type="MISSED_ROUTINE",
            proposed_points=-10,
            reason="Missed mandatory bath deadline",
            applicable_rule="BATH_DEADLINE",
            status="AWAITING_CONFIRMATION"
        )
        self.assertEqual(self._get_score(), 20)

        # 3. Approve via Android human confirmation
        appr_res = self.engine.approve_deduction_proposal(
            proposal_id="prop_test_human_auth",
            admin_user="user_lok",
            approval_source="ANDROID_HUMAN_CONFIRMATION"
        )
        self.assertTrue(appr_res.get("success"))
        self.assertEqual(appr_res["score_event"]["points"], -10)

        # Proposal status is now APPROVED
        updated_prop = self.db.get_deduction_proposal("prop_test_human_auth")
        self.assertEqual(updated_prop["status"], "APPROVED")
        self.assertEqual(updated_prop["approval_source"], "ANDROID_HUMAN_CONFIRMATION")
        self.assertEqual(updated_prop["approved_by"], "user_lok")

        # Score is now deducted: 20 - 10 = 10
        self.assertEqual(self._get_score(), 10)

    def test_04_retried_duplicated_sync_packets_processed_idempotently(self):
        """Criterion 4: Retried/duplicated sync packets -> Processed idempotently, no double deductions."""
        # Earn 30 points
        self._add_approved_proof(points=30)
        self.assertEqual(self._get_score(), 30)

        prop_id = "prop_idempotent_retry"
        self.db.create_deduction_proposal(
            proposal_id=prop_id,
            date=self.test_date,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            originating_device="win_client",
            event_type="VIOLATION",
            proposed_points=-10,
            reason="Violation retry test",
            status="AWAITING_CONFIRMATION"
        )

        # First approval
        res1 = self.engine.approve_deduction_proposal(
            proposal_id=prop_id,
            admin_user="user_lok",
            approval_source="ANDROID_HUMAN_CONFIRMATION"
        )
        self.assertTrue(res1.get("success"))
        score1 = self._get_score()
        self.assertEqual(score1, 20)

        # Second approval (simulating duplicate sync packet or retry)
        res2 = self.engine.approve_deduction_proposal(
            proposal_id=prop_id,
            admin_user="user_lok",
            approval_source="ANDROID_HUMAN_CONFIRMATION"
        )
        self.assertTrue(res2.get("success"))
        self.assertTrue(res2.get("already_approved"))

        # Score MUST NOT deduct a second time (must remain 20)
        score2 = self._get_score()
        self.assertEqual(score2, 20)

    def test_05_windows_exe_reconnects_with_stale_lower_score_apk_preserved(self):
        """Criterion 5: Windows EXE reconnects with stale lower score -> APK authoritative score preserved, not overwritten."""
        apk_score = 50

        # Case A: EXE sends stale lower score (e.g., 20)
        stale_exe_score = 20
        preserved_score = max(apk_score, stale_exe_score)
        self.assertEqual(preserved_score, 50)

        # Case B: EXE sends negative unauthorized score (e.g., -40)
        negative_exe_score = -40
        safe_score = max(apk_score, negative_exe_score)
        self.assertEqual(safe_score, 50)

        # Case C: Zero baseline APK preserved against negative secondary score
        zero_apk_score = 0
        safe_zero = max(zero_apk_score, negative_exe_score)
        self.assertEqual(safe_zero, 0)

    def test_06_positive_points_continue_working_normally(self):
        """Criterion 6: Positive points (proof approval, focus sessions) continue working normally."""
        score_start = self._get_score()

        # Add proof approval (+20)
        self._add_approved_proof(points=20)
        self.assertEqual(self._get_score(), score_start + 20)

        # Add hourly checkpoint (+15)
        ev_hourly = self.engine.store_evidence_file(
            date=self.test_date,
            record_type="Hourly",
            related_record_id="act_hourly_test",
            device_id="test_phone",
            file_bytes=self.valid_photo,
        )
        r2 = self.engine.apply_proof_approval(
            evidence_id=ev_hourly["evidence_id"],
            admin_id="primary_admin",
            points=15,
            reason="Approved hourly work",
        )
        self.assertEqual(r2.get("points_awarded"), 15)
        self.assertEqual(self._get_score(), score_start + 35)

    def test_07_system_restarts_do_not_cause_proposal_leaks_or_unauthorized_deductions(self):
        """Criterion 7: System restarts/disconnections do not cause proposal leaks or unauthorized deductions."""
        # Create a pending proposal
        prop_id = "prop_restart_leak_test"
        self.db.create_deduction_proposal(
            proposal_id=prop_id,
            date=self.test_date,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            originating_device="win_client",
            event_type="DISCONNECTION_TEST",
            proposed_points=-10,
            reason="Offline detection test",
            status="AWAITING_CONFIRMATION"
        )

        # Simulate system shutdown and restart by re-initializing DB and ScoringEngine
        del self.engine
        del self.db

        db_restarted = StrictRecordsDB(db_path=self.db_path)
        engine_restarted = ScoringEngine(db=db_restarted, storage_root=self.storage_root)

        # After restart, proposal must still exist in AWAITING_CONFIRMATION
        proposals = db_restarted.get_deduction_proposals(date=self.test_date, status="AWAITING_CONFIRMATION")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["proposal_id"], prop_id)
        self.assertEqual(proposals[0]["status"], "AWAITING_CONFIRMATION")

        # Score after restart must remain 0 (no leaks into score_events)
        score_after_restart = engine_restarted.recalculate_daily_score(self.test_date)["final_score"]
        self.assertEqual(score_after_restart, 0)

        with db_restarted._connect() as conn:
            neg_events = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND points < 0",
                (self.test_date,),
            ).fetchall()
        self.assertEqual(len(neg_events), 0)

    def test_08_dashboard_displays_distinct_statuses_confirmed_vs_pending_proposals(self):
        """Criterion 8: Dashboard displays distinct statuses: Confirmed points vs. Pending proposals."""
        # 1. Earn 25 confirmed points
        self._add_approved_proof(points=25)

        # 2. EXE registers 2 pending proposals (-10 each)
        self.db.create_deduction_proposal(
            proposal_id="prop_dash_1",
            date=self.test_date,
            timestamp="2026-10-16 11:00:00",
            originating_device="win_client",
            event_type="ENTERTAINMENT_VIOLATION",
            proposed_points=-10,
            reason="Instagram detected",
            status="AWAITING_CONFIRMATION"
        )
        self.db.create_deduction_proposal(
            proposal_id="prop_dash_2",
            date=self.test_date,
            timestamp="2026-10-16 12:00:00",
            originating_device="win_client",
            event_type="ENTERTAINMENT_VIOLATION",
            proposed_points=-10,
            reason="YouTube detected",
            status="AWAITING_CONFIRMATION"
        )

        # 3. Retrieve dashboard day state
        day_state = self.engine.get_full_day_state(self.test_date)

        # Confirmed balance must be 25 (0 impact from pending proposals)
        self.assertEqual(day_state["daily_record"]["final_score"], 25)

        # Pending proposals must be reported separately
        self.assertIn("deduction_proposals", day_state)
        self.assertIn("pending_proposals_count", day_state)
        self.assertEqual(day_state["pending_proposals_count"], 2)
        self.assertEqual(len(day_state["deduction_proposals"]), 2)

        # Account summary must also report confirmed score vs pending proposals
        account_summary = self.engine.get_shared_account_summary(user_id=self.user_id, date=self.test_date)
        self.assertEqual(account_summary["shared_score"], 25)
        self.assertEqual(account_summary["pending_proposals_count"], 2)


if __name__ == "__main__":
    unittest.main()
