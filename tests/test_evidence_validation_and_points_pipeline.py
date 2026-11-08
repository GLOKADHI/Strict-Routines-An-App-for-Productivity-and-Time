"""
StrictRecords — Comprehensive Evidence Validation, Taufa AI Fallback,
Points Ledger & Submission Synchronization Test Suite (Part 8: Scenarios 1 to 20)
================================================================================
Verifies all 20 required scenarios:
 1. Genuine proof passes Taufa AI validation.
 2. OCR similarity is high but the evidence is genuinely different.
 3. Two legitimate photographs show the same notebook.
 4. Same submission is uploaded again through a network retry.
 5. Taufa AI approves but points posting fails.
 6. Taufa AI approves and points post successfully.
 7. Points transaction is delivered twice.
 8. Android temporarily goes offline.
 9. Windows EXE temporarily goes offline.
10. Both devices reconnect.
11. Android displays the correct approved status.
12. Windows displays the correct approved status.
13. Points history contains exactly one award.
14. Current points balance contains the awarded amount exactly once.
15. Taufa AI times out.
16. Official OpenAI API fallback returns an approval.
17. Both validation providers are unavailable.
18. Delayed Taufa AI response arrives after fallback processing.
19. Existing historical approved submissions have missing points.
20. Historical records already containing valid points are not rewarded again.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from PIL import Image

from core.database import StrictRecordsDB
from core.hourly_admin_service import HourlyAdminService
from core.hourly_submission_service import HourlySubmissionService
from core.models import ScoreEventType
from core.openai_fallback_validator import OpenAiFallbackValidator, OpenAiValidationResult
from core.reconcile_submissions import audit_historical_submissions, reconcile_historical_submissions
from core.scoring_engine import ScoringEngine
from core.time_window_engine import iso_to_dt
from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaDuplicateResult,
    TaufaValidationResult,
)


def _make_test_image(pattern: int = 1, size: tuple = (100, 100)) -> bytes:
    """Creates distinct non-empty JPEG image bytes."""
    img = Image.new("RGB", size, (200 + pattern % 55, 210, 220 + pattern % 35))
    for x in range(0, size[0], 4):
        for y in range(0, size[1], 4):
            img.putpixel((x, y), ((x * pattern * 3) % 256, (y * pattern * 7) % 256, (x + y + pattern) % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class TestEvidenceValidationAndPointsPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        self.scoring_engine = ScoringEngine(self.db)
        self.hourly_service = HourlySubmissionService(self.db, self.scoring_engine, storage_root=self.temp_dir)
        self.admin_service = HourlyAdminService(self.db, self.scoring_engine, storage_root=self.temp_dir)
        self.today = "2026-11-04"
        self.account_id = "user_lok"

        # Register user and initialize daily record
        self.db.ensure_daily_record(self.today, user_id=self.account_id)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_hourly_activity(self, activity_id="act_hour_10", hour=10):
        w_start = f"{self.today}T{hour:02d}:00:00"
        w_end = f"{self.today}T{hour:02d}:15:00"
        notif = f"{self.today}T{hour:02d}:00:00"
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, type, title, date,
                    scheduled_time, window_start, window_end, notification_time, status, points
                ) VALUES (?, ?, 'HOURLY', 'Study Hour', ?, ?, ?, ?, ?, 'SCHEDULED', 0)
                """,
                (activity_id, self.account_id, self.today, w_start, w_start, w_end, notif),
            )

    # -------------------------------------------------------------------------
    # Scenario 1: Genuine proof passes Taufa AI validation -> points posted, balance updated
    # -------------------------------------------------------------------------
    def test_01_genuine_proof_taufa_validation_and_points_update(self):
        self._seed_hourly_activity("act_01", 10)
        img = _make_test_image(1)
        sub_id = "sub_genuine_01"

        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_01",
                "category": "STUDY",
                "description": "Solved dynamic programming problem 0/1 knapsack with memoization table.",
                "submission_id": sub_id,
            },
            file_bytes=img,
            filename="knapsack.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T10:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # Atomic award from genuine Taufa AI approval (e.g. 7 points)
        res = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_01",
            submission_id=sub_id,
            points=7,
            admin_id="Taufa_AI",
            reason="Exemplary dynamic programming study notes verified.",
            date=self.today,
            account_id=self.account_id,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["points_awarded"], 7)
        self.assertEqual(res["status"], "APPROVED")

        # Verify daily score and activity updated
        balance = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(balance, 7)

        with self.db._connect() as conn:
            act = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id = 'act_01'").fetchone()
            self.assertEqual(act["status"], "VERIFIED")
            self.assertEqual(act["points"], 7)

    # -------------------------------------------------------------------------
    # Scenario 2: OCR similarity is high but the evidence is genuinely different -> not rejected
    # -------------------------------------------------------------------------
    def test_02_ocr_similarity_high_not_rejected(self):
        from core.ai_robot_admin import StrictBotAIReviewer

        past_tokens = ["def", "knapsack", "weights", "values", "capacity", "memo", "return"]
        current_tokens = ["def", "knapsack", "weights", "values", "capacity", "tabulation", "return"]

        # Jaccard similarity is high (5/7 ~ 0.71+)
        dup_res = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256="curr_sha256_unique",
            current_dhash="1111222233334444",
            historical_records=[
                {"evidence_id": "ev_past_01", "ocr_tokens": past_tokens, "sha256": "past_sha", "dhash": "5555666677778888"}
            ],
            current_ocr_tokens=current_tokens,
            exclude_evidence_id="ev_new_02",
        )

        # High OCR token similarity must NOT cause duplicate_risk = HIGH or reject the submission
        self.assertEqual(dup_res["duplicate_risk"], "LOW")
        self.assertEqual(dup_res["status"], "TOPICAL_OVERLAP")

    # -------------------------------------------------------------------------
    # Scenario 3: Two legitimate photographs show the same notebook -> not rejected
    # -------------------------------------------------------------------------
    def test_03_two_photos_same_notebook_not_rejected(self):
        validator = TaufaAiValidator()
        photo_1 = _make_test_image(10, size=(120, 120))
        photo_2 = _make_test_image(11, size=(120, 120))

        dup_res = validator.check_duplicate_evidence(
            submission_id="sub_notebook_batch",
            category="STUDY",
            description="Studying discrete math recurrence relations and generating functions.",
            images=[photo_1, photo_2],
            historical_records=[],
            task_type="HOURLY",
        )

        self.assertFalse(dup_res.duplicate_detected)
        self.assertNotEqual(dup_res.validator_status, "REJECTED")

    # -------------------------------------------------------------------------
    # Scenario 4: Same submission uploaded again through a network retry -> idempotent return
    # -------------------------------------------------------------------------
    def test_04_network_retry_same_submission_id_idempotent(self):
        self._seed_hourly_activity("act_04", 11)
        img = _make_test_image(4)
        sub_id = "sub_retry_test_04"

        # First upload
        res_1 = self.hourly_service.accept(
            {
                "activity_id": "act_04",
                "category": "STUDY",
                "description": "Reviewed system design consistent hashing and distributed caches.",
                "submission_id": sub_id,
            },
            file_bytes=img,
            filename="design.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T11:05:00"),
        )

        # Second upload retry with same submission ID and file
        res_2 = self.hourly_service.accept(
            {
                "activity_id": "act_04",
                "category": "STUDY",
                "description": "Reviewed system design consistent hashing and distributed caches.",
                "submission_id": sub_id,
            },
            file_bytes=img,
            filename="design.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T11:05:00"),
        )

        self.assertTrue(res_2.get("deduplicated"))
        self.assertEqual(res_1["submission_id"], res_2["submission_id"])
        self.assertEqual(res_1["evidence_id"], res_2["evidence_id"])

    # -------------------------------------------------------------------------
    # Scenario 5: Taufa AI approves but points posting fails -> transaction rollback
    # -------------------------------------------------------------------------
    def test_05_points_posting_failure_atomic_rollback(self):
        self._seed_hourly_activity("act_05", 12)
        img = _make_test_image(5)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_05",
                "category": "CODING",
                "description": "Implemented red-black tree rebalancing in C++.",
                "submission_id": "sub_fail_05",
            },
            file_bytes=img,
            filename="rbtree.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T12:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # Intentionally cause a database error during points transaction to verify rollback
        import contextlib
        real_connect = self.db._connect

        class ConnectionProxy:
            def __init__(self, target):
                self._target = target
            def execute(self, sql, *a, **kw):
                if "INSERT OR IGNORE INTO score_events" in str(sql):
                    raise sqlite3.OperationalError("Simulated disk error during points transaction")
                return self._target.execute(sql, *a, **kw)
            def __getattr__(self, name):
                return getattr(self._target, name)
            def __enter__(self):
                self._target.__enter__()
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._target.__exit__(exc_type, exc_val, exc_tb)

        @contextlib.contextmanager
        def connect_wrapper(*args, **kwargs):
            with real_connect(*args, **kwargs) as conn:
                yield ConnectionProxy(conn)

        with patch.object(self.db, "_connect", side_effect=connect_wrapper):
            with self.assertRaises(sqlite3.OperationalError):
                self.scoring_engine.award_evidence_points_atomically(
                    evidence_id=ev_id,
                    activity_id="act_05",
                    submission_id="sub_fail_05",
                    points=8,
                    admin_id="Taufa_AI",
                    reason="Approved",
                )

        # Verify no points transaction was committed
        with self.db._connect() as conn:
            se = conn.execute("SELECT * FROM score_events WHERE evidence_id = ?", (ev_id,)).fetchall()
            self.assertEqual(len(se), 0)

    # -------------------------------------------------------------------------
    # Scenario 6: Taufa AI approves and points post successfully -> balance updated atomically
    # -------------------------------------------------------------------------
    def test_06_taufa_approves_and_points_post_successfully(self):
        self._seed_hourly_activity("act_06", 13)
        img = _make_test_image(6)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_06",
                "category": "STUDY",
                "description": "Read operating systems virtual memory and page replacement algorithms.",
                "submission_id": "sub_atom_06",
            },
            file_bytes=img,
            filename="os.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T13:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        res = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_06",
            submission_id="sub_atom_06",
            points=6,
            admin_id="Taufa_AI",
            reason="Clear operating systems study notes.",
            date=self.today,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["points_awarded"], 6)

        balance = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(balance, 6)

    # -------------------------------------------------------------------------
    # Scenario 7: Points transaction is delivered twice -> deduplicated, no double award
    # -------------------------------------------------------------------------
    def test_07_points_transaction_delivered_twice_deduplicated(self):
        self._seed_hourly_activity("act_07", 14)
        img = _make_test_image(7)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_07",
                "category": "RESEARCH",
                "description": "Researched Raft consensus log replication safety proofs.",
                "submission_id": "sub_double_07",
            },
            file_bytes=img,
            filename="raft.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T14:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # First award
        res_1 = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_07",
            submission_id="sub_double_07",
            points=8,
            admin_id="Taufa_AI",
        )
        self.assertTrue(res_1["success"])
        self.assertFalse(res_1.get("already_rewarded", False))

        # Second delivery of same award
        res_2 = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_07",
            submission_id="sub_double_07",
            points=8,
            admin_id="Taufa_AI",
        )
        self.assertTrue(res_2["success"])
        self.assertTrue(res_2.get("already_rewarded"))

        # Verify only 8 points in total (not 16)
        balance = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(balance, 8)

    # -------------------------------------------------------------------------
    # Scenario 8: Android temporarily goes offline -> events queued in outbox
    # -------------------------------------------------------------------------
    def test_08_android_temporarily_offline_queues_outbox(self):
        self._seed_hourly_activity("act_08", 15)
        img = _make_test_image(8)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_08",
                "category": "STUDY",
                "description": "Offline queuing test submission on computer graphics shaders.",
                "submission_id": "sub_offline_08",
            },
            file_bytes=img,
            filename="shader.jpg",
            account_id=self.account_id,
            device_id="windows_pc_01",
            platform="WINDOWS",
            received_at=iso_to_dt(f"{self.today}T15:05:00"),
        )

        # Outbox should have pending event for Android device
        pending = self.hourly_service.get_pending_events(self.account_id, "android_phone_01")
        self.assertTrue(any(e.get("submission_id") == "sub_offline_08" for e in pending))

    # -------------------------------------------------------------------------
    # Scenario 9: Windows EXE temporarily goes offline -> queued events persist
    # -------------------------------------------------------------------------
    def test_09_windows_exe_offline_events_persist(self):
        self._seed_hourly_activity("act_09", 16)
        img = _make_test_image(9)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_09",
                "category": "STUDY",
                "description": "Android-generated proof while Windows was closed.",
                "submission_id": "sub_win_offline_09",
            },
            file_bytes=img,
            filename="study.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T16:05:00"),
        )

        # Pending event for Windows persists in outbox
        pending_win = self.hourly_service.get_pending_events(self.account_id, "windows_pc_01")
        self.assertTrue(any(e.get("submission_id") == "sub_win_offline_09" for e in pending_win))

    # -------------------------------------------------------------------------
    # Scenario 10: Both devices reconnect -> outbox events delivered and acknowledged
    # -------------------------------------------------------------------------
    def test_10_both_devices_reconnect_sync_and_ack(self):
        self._seed_hourly_activity("act_10", 17)
        img = _make_test_image(10)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_10",
                "category": "STUDY",
                "description": "Studying compiler design lexing and LL(1) parsing tables.",
                "submission_id": "sub_sync_10",
            },
            file_bytes=img,
            filename="compiler.jpg",
            account_id=self.account_id,
            device_id="windows_pc_01",
            platform="WINDOWS",
            received_at=iso_to_dt(f"{self.today}T17:05:00"),
        )

        # Android reconnects and pulls events
        pending = self.hourly_service.get_pending_events(self.account_id, "android_phone_01")
        target_evt = next((e for e in pending if e.get("submission_id") == "sub_sync_10"), None)
        self.assertIsNotNone(target_evt)

        # Acknowledge event
        acked = self.hourly_service.acknowledge_events(self.account_id, "android_phone_01", [target_evt["event_id"]])
        self.assertEqual(acked, 1)

        # Subsequent fetch shows empty
        remaining = self.hourly_service.get_pending_events(self.account_id, "android_phone_01")
        self.assertFalse(any(e.get("submission_id") == "sub_sync_10" for e in remaining))

    # -------------------------------------------------------------------------
    # Scenario 11: Android displays the correct approved status
    # -------------------------------------------------------------------------
    def test_11_android_displays_correct_approved_status(self):
        self._seed_hourly_activity("act_11", 18)
        img = _make_test_image(11)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_11",
                "category": "STUDY",
                "description": "Reviewed linear algebra eigenvalues and eigenvectors.",
                "submission_id": "sub_stat_11",
            },
            file_bytes=img,
            filename="linalg.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T18:05:00"),
            
        )

        self.scoring_engine.award_evidence_points_atomically(
            evidence_id=accepted["evidence_id"],
            activity_id="act_11",
            submission_id="sub_stat_11",
            points=6,
            admin_id="Taufa_AI",
        )

        detail = self.admin_service.get_hourly_submission_detail("act_11", account_id=self.account_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["review_status"], "APPROVED")
        self.assertEqual(detail["points_awarded"], 6)
        self.assertEqual(detail["validation_status"], "APPROVED")
        self.assertEqual(detail["points_status"], "POINTS_POSTED")

    # -------------------------------------------------------------------------
    # Scenario 12: Windows displays the correct approved status
    # -------------------------------------------------------------------------
    def test_12_windows_displays_correct_approved_status(self):
        self._seed_hourly_activity("act_12", 19)
        img = _make_test_image(12)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_12",
                "category": "STUDY",
                "description": "Analyzed asymptotic bounds of quickselect and median of medians.",
                "submission_id": "sub_win_12",
            },
            file_bytes=img,
            filename="select.jpg",
            account_id=self.account_id,
            device_id="windows_pc_01",
            platform="WINDOWS",
            received_at=iso_to_dt(f"{self.today}T19:05:00"),
            
        )

        self.scoring_engine.award_evidence_points_atomically(
            evidence_id=accepted["evidence_id"],
            activity_id="act_12",
            submission_id="sub_win_12",
            points=7,
            admin_id="Taufa_AI",
        )

        submissions = self.admin_service.list_hourly_submissions(target_date=self.today, occurrence_id="act_12", account_id=self.account_id)
        self.assertEqual(len(submissions), 1)
        sub = submissions[0]
        self.assertEqual(sub["review_status"], "APPROVED")
        self.assertEqual(sub["points_awarded"], 7)
        self.assertEqual(sub["validation_status"], "APPROVED")
        self.assertEqual(sub["points_status"], "POINTS_POSTED")

    # -------------------------------------------------------------------------
    # Scenario 13: Points history contains exactly one award
    # -------------------------------------------------------------------------
    def test_13_points_history_contains_exactly_one_award(self):
        self._seed_hourly_activity("act_13", 20)
        img = _make_test_image(13)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_13",
                "category": "STUDY",
                "description": "Database query optimization and B-Tree indexing.",
                "submission_id": "sub_hist_13",
            },
            file_bytes=img,
            filename="db.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T20:05:00"),
            
        )

        self.scoring_engine.award_evidence_points_atomically(
            evidence_id=accepted["evidence_id"],
            activity_id="act_13",
            submission_id="sub_hist_13",
            points=7,
            admin_id="Taufa_AI",
            date=self.today,
        )

        history = self.scoring_engine.get_unified_transaction_history(date=self.today)
        matching = [tx for tx in history["transactions"] if "sub_hist_13" in str(tx.get("transaction_id")) or "sub_hist_13" in str(tx.get("reference_id")) or "sub_hist_13" in str(tx.get("details"))]
        self.assertEqual(len(matching), 1)
        tx = matching[0]
        self.assertEqual(tx["amount"], 7)
        self.assertTrue(tx["confirmed"])

    # -------------------------------------------------------------------------
    # Scenario 14: Current points balance contains the awarded amount exactly once
    # -------------------------------------------------------------------------
    def test_14_points_balance_contains_awarded_amount_once(self):
        self._seed_hourly_activity("act_14", 21)
        img = _make_test_image(14)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_14",
                "category": "CODING",
                "description": "Constructed asynchronous worker pool in Go.",
                "submission_id": "sub_bal_14",
            },
            file_bytes=img,
            filename="worker.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T21:05:00"),
            
        )

        self.scoring_engine.award_evidence_points_atomically(
            evidence_id=accepted["evidence_id"],
            activity_id="act_14",
            submission_id="sub_bal_14",
            points=8,
            admin_id="Taufa_AI",
            date=self.today,
        )

        bal = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal, 8)

    # -------------------------------------------------------------------------
    # Scenario 15: Taufa AI times out -> fallback triggered
    # -------------------------------------------------------------------------
    def test_15_taufa_ai_timeout_triggers_fallback(self):
        validator = TaufaAiValidator()
        img = _make_test_image(15)

        # Mock urllib.request.urlopen to simulate Taufa AI TimeoutError
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out after 45s")):
            with patch.object(OpenAiFallbackValidator, "is_configured", return_value=True):
                with patch.object(OpenAiFallbackValidator, "validate_evidence") as mock_fb:
                    mock_fb.return_value = OpenAiValidationResult(
                        submission_id="sub_to_15",
                        approved=True,
                        validation_status="APPROVED",
                        awarded_points=6,
                        activity_relevance="HIGH",
                        evidence_quality="STRONG",
                        explanation="Approved by OpenAI fallback after Taufa timeout.",
                    )
                    res = validator.validate_hourly_submission(
                        submission_id="sub_to_15",
                        category="STUDY",
                        description="Solved 3 leetcode dynamic programming problems.",
                        images=[img],
                        task_type="HOURLY",
                        check_duplicate=False,
                    )

        self.assertTrue(res.approved)
        self.assertEqual(res.score, 6)
        self.assertEqual(res.validator_id, "OPENAI_FALLBACK")

    # -------------------------------------------------------------------------
    # Scenario 16: Official OpenAI API fallback returns an approval -> points awarded
    # -------------------------------------------------------------------------
    def test_16_official_openai_fallback_approval_awards_points(self):
        self._seed_hourly_activity("act_16", 10)
        img = _make_test_image(16)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_16",
                "category": "STUDY",
                "description": "Reviewed cryptographic protocols: Diffie-Hellman and RSA.",
                "submission_id": "sub_fb_16",
            },
            file_bytes=img,
            filename="crypto.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T10:05:00"),
            
        )

        # OpenAI fallback approved with 6 points
        res = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=accepted["evidence_id"],
            activity_id="act_16",
            submission_id="sub_fb_16",
            points=6,
            admin_id="OPENAI_FALLBACK",
            reason="Verified by official OpenAI fallback assistant.",
            date=self.today,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["points_awarded"], 6)
        self.assertEqual(res["reviewed_by"], "OPENAI_FALLBACK")

        bal = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal, 6)

    # -------------------------------------------------------------------------
    # Scenario 17: Both validation providers are unavailable -> retains VALIDATION_PENDING, not rejected
    # -------------------------------------------------------------------------
    def test_17_both_providers_unavailable_retains_pending(self):
        validator = TaufaAiValidator()
        img = _make_test_image(17)

        # Taufa throws network error, and OpenAI Fallback is unconfigured
        with patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            with patch.object(OpenAiFallbackValidator, "is_configured", return_value=False):
                res = validator.validate_hourly_submission(
                    submission_id="sub_unavail_17",
                    category="STUDY",
                    description="Studying compiler code generation and register allocation.",
                    images=[img],
                    task_type="HOURLY",
                    check_duplicate=False,
                )

        self.assertFalse(res.approved)
        self.assertTrue(res.is_technical_failure)
        self.assertIn("VALIDATION_PENDING", res.warnings)
        self.assertNotEqual(res.validator_status, "REJECTED")
        self.assertNotEqual(res.review_status, "REJECTED")

    # -------------------------------------------------------------------------
    # Scenario 18: Delayed Taufa AI response arrives after fallback processing -> no double award
    # -------------------------------------------------------------------------
    def test_18_delayed_taufa_response_priority_reconciliation(self):
        self._seed_hourly_activity("act_18", 11)
        img = _make_test_image(18)
        sub_id = "sub_delayed_18"
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_18",
                "category": "STUDY",
                "description": "Reviewed memory hierarchy, L1/L2 cache coherency protocols.",
                "submission_id": sub_id,
            },
            file_bytes=img,
            filename="cache.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T11:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # 1. Fallback validator finished first and posted points (6 points)
        fb_award = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_18",
            submission_id=sub_id,
            points=6,
            admin_id="OPENAI_FALLBACK",
            date=self.today,
        )
        self.assertTrue(fb_award["success"])

        # 2. Delayed Taufa AI response arrives with 7 points
        delayed_award = self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_18",
            submission_id=sub_id,
            points=7,
            admin_id="Taufa_AI",
            date=self.today,
        )

        # Idempotency prevents double award
        self.assertTrue(delayed_award.get("already_rewarded"))
        self.assertEqual(delayed_award["points_awarded"], 6)

        # Balance remains 6 (not 6+7=13)
        bal = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal, 6)

    # -------------------------------------------------------------------------
    # Scenario 19: Existing historical approved submissions have missing points -> reconciled safely
    # -------------------------------------------------------------------------
    def test_19_historical_missing_points_reconciled(self):
        self._seed_hourly_activity("act_19", 12)
        img = _make_test_image(19)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_19",
                "category": "STUDY",
                "description": "Study of relational database normalization forms 1NF to BCNF.",
                "submission_id": "sub_reconcile_19",
            },
            file_bytes=img,
            filename="norm.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T12:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # Simulate historical state: proof_reviews marked APPROVED with points_awarded = 7,
        # but score_events was never inserted (points not posted).
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE proof_reviews SET status = 'APPROVED', decision = 'APPROVED', points_awarded = 7 WHERE evidence_id = ?",
                (ev_id,),
            )
            # Ensure score_events has NO entry
            conn.execute("DELETE FROM score_events WHERE evidence_id = ?", (ev_id,))

        # Run audit: should find 1 discrepancy
        audit = audit_historical_submissions(self.db, date=self.today, account_id=self.account_id)
        self.assertEqual(audit["discrepancies_count"], 1)
        self.assertEqual(audit["discrepancies"][0]["issue"], "APPROVED_PROOF_MISSING_SCORE_EVENT")

        # Run safe reconciliation
        rec = reconcile_historical_submissions(self.db, self.scoring_engine, date=self.today, dry_run=False, account_id=self.account_id)
        self.assertEqual(rec["applied_count"], 1)

        # Balance now correctly contains the 7 points
        bal = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal, 7)

    # -------------------------------------------------------------------------
    # Scenario 20: Historical records already containing valid points are not rewarded again
    # -------------------------------------------------------------------------
    def test_20_historical_records_with_valid_points_not_rewarded_again(self):
        self._seed_hourly_activity("act_20", 13)
        img = _make_test_image(20)
        accepted = self.hourly_service.accept(
            {
                "activity_id": "act_20",
                "category": "STUDY",
                "description": "Implemented Bellman-Ford shortest path algorithm.",
                "submission_id": "sub_valid_20",
            },
            file_bytes=img,
            filename="bellman.jpg",
            account_id=self.account_id,
            device_id="android_phone_01",
            platform="ANDROID",
            received_at=iso_to_dt(f"{self.today}T13:05:00"),
            
        )
        ev_id = accepted["evidence_id"]

        # Award points normally
        self.scoring_engine.award_evidence_points_atomically(
            evidence_id=ev_id,
            activity_id="act_20",
            submission_id="sub_valid_20",
            points=8,
            admin_id="Taufa_AI",
            date=self.today,
        )

        bal_before = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal_before, 8)

        # Run reconciliation on this date
        rec = reconcile_historical_submissions(self.db, self.scoring_engine, date=self.today, dry_run=False, account_id=self.account_id)
        self.assertEqual(rec["applied_count"], 0)

        # Balance remains exactly 8 (no extra points added)
        bal_after = self.scoring_engine.calculate_verified_score(self.today, user_id=self.account_id)
        self.assertEqual(bal_after, 8)


if __name__ == "__main__":
    unittest.main()
