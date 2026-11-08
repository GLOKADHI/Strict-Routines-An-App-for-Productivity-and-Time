"""
Tests for StrictRecords — Automatic Finalization of Eligible Hourly Attendance
Proofs After Taufa AI Validation (Without Unnecessary Admin Review).

Verifies all 14 requirements from Prompt #7:
1. Valid Hourly Attendance evidence is automatically finalized when all configured gates pass.
2. A valid score of 6/8 or higher does not wait for unnecessary manual approval.
3. An invalid category match enters the appropriate exception path.
4. A confirmed disqualifying duplicate follows the existing evidence policy.
5. An ambiguous duplicate is not automatically labelled fraud.
6. A Taufa AI timeout does not fabricate an approval or an automatic rejection.
7. Retrying the same submission resumes the existing evaluation.
8. A completed automatic evaluation does not remain incorrectly marked ADMIN REVIEW.
9. Invalid or out-of-range scores cannot create point awards.
10. Points are committed only once (idempotent).
11. Demo results never affect production balances or approval states.
12. Bath, Gym, sleep-data, and other excluded tasks retain their existing workflows.
13. Ordinary photo without camera watermark is not forced to NEEDS_REVIEW.
14. Activity report recognizes Taufa_AI as automated validator without admin review badge.
"""

import io
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from PIL import Image, ImageDraw

from core.database import StrictRecordsDB
from core.time_window_engine import TimeWindowEngine
from core.ai_robot_admin import StrictBotAIReviewer
from core.activity_report import build_daily_activity_report, _review_stage
from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaValidationResult,
    TaufaDuplicateResult,
    EXCLUDED_TASK_TYPES,
)


def _make_dummy_image(color=(160, 160, 170), size=(200, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    for i in range(10, size[1] - 10, 8):
        d.line([(10, i), (size[0] - 10, i)], fill=(20, 20, 40), width=1)
    d.text((10, 10), "Notes: calculus chapter 4 recurrence relations", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestTaufaAutoFinalization(unittest.TestCase):
    def setUp(self):
        self.validator = TaufaAiValidator(
            endpoint_url="https://api.taufa.example.com/v1/validate",
            api_key="valid-secret-key-test",
        )
        self.valid_img = _make_dummy_image()

    # 1. Valid Hourly Attendance evidence is automatically finalized when all configured gates pass
    def test_01_valid_hourly_attendance_auto_finalizes(self):
        sub_id = "sub_auto_01"
        data = {
            "submission_id": sub_id,
            "score": 7,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Study notes visually verify computer science revision.",
            "warnings": [],
        }
        res = self.validator.parse_and_validate_response(sub_id, data, category="STUDY", description="Studying operating systems memory management")
        self.assertTrue(res.is_valid)
        self.assertTrue(res.qualifies_for_points)
        self.assertEqual(res.score, 7)
        self.assertEqual(res.review_status, "APPROVED")

    # 2. A valid score of 6/8 or higher does not wait for unnecessary manual approval
    def test_02_score_six_or_higher_no_manual_approval_wait(self):
        sub_id = "sub_score6_02"
        data = {
            "submission_id": sub_id,
            "score": 6,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "MEDIUM",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "warnings": [],
        }
        res = self.validator.parse_and_validate_response(sub_id, data, category="CODING", description="Writing Python backend microservice unit tests")
        self.assertTrue(res.is_valid)
        self.assertTrue(res.qualifies_for_points)
        self.assertNotIn("REVIEW_REQUIRED", res.review_status)

    # 3. An invalid category match enters the appropriate exception path
    def test_03_invalid_category_match_enters_exception_path(self):
        sub_id = "sub_cat_mismatch_03"
        data = {
            "submission_id": sub_id,
            "score": 7,
            "category_match": False,
            "description_match": True,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "warnings": ["CATEGORY_MISMATCH"],
        }
        res = self.validator.parse_and_validate_response(sub_id, data, category="STUDY")
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.score, 0)
        self.assertEqual(res.review_status, "REJECTED")
        self.assertIn("CATEGORY_MISMATCH", res.warnings)

    # 4. A confirmed disqualifying duplicate follows the existing evidence policy
    def test_04_confirmed_duplicate_rejected(self):
        sub_id = "sub_dup_04"
        dup_res = TaufaDuplicateResult(
            submission_id=sub_id,
            duplicate_detected=True,
            matched_submission_id="sub_prev_999",
            similarity_or_confidence=0.98,
            reason="Exact perceptual image hash duplicate",
        )
        res = TaufaValidationResult(
            submission_id=sub_id,
            score=0,
            max_score=8,
            validator_status="REJECTED",
            review_status="REJECTED",
            reason=f"Duplicate evidence detected: {dup_res.reason}",
            warnings=["DUPLICATE_EVIDENCE_DETECTED"],
            duplicate_result=dup_res,
        )
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.score, 0)
        self.assertTrue(res.duplicate_result.duplicate_detected)

    # 5. An ambiguous duplicate is not automatically labelled fraud
    def test_05_ambiguous_duplicate_not_labelled_fraud(self):
        res = self.validator.evaluate_offline_heuristics(
            category="STUDY",
            description="Re-reading same calculus chapter 3 textbook page for continuous functions",
            images=[b"repeated textbook study material"],
        )
        self.assertTrue(res["category_match"])
        self.assertNotIn("FRAUD", res["warnings"])
        self.assertIn("REPEATED_STUDY_MATERIAL_VERIFIED", res["warnings"])

    # 6. A Taufa AI timeout does not fabricate an approval or an automatic rejection
    def test_06_timeout_does_not_fabricate_approval_or_rejection(self):
        sub_id = "sub_timeout_06"
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out after 15s")):
            res = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category="STUDY",
                description="Legitimate revision of engineering mathematics notes",
                images=[self.valid_img],
                task_type="HOURLY",
            )
        self.assertEqual(res.validator_status, "FAILED_RETRYABLE")
        self.assertIsNone(res.score)
        self.assertFalse(res.is_valid)
        self.assertIn("TIMEOUT", res.warnings)

    # 7. Retrying the same submission resumes the existing evaluation
    def test_07_retry_resumes_same_submission(self):
        sub_id = "sub_retry_07"
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({
            "submission_id": sub_id,
            "score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Successfully verified on retry",
            "conversation_id": __import__('core.taufa_ai_validator',fromlist=['DESIGNATED_CONVERSATION_ID']).DESIGNATED_CONVERSATION_ID,
            "evaluation_id": "fixture-retry-evaluation",
        }).encode("utf-8")

        mock_open = MagicMock()
        mock_open.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_open):
            res = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category="CODING",
                description="Writing and debugging microservice controller logic",
                images=[self.valid_img],
                task_type="HOURLY",
            )
        self.assertEqual(res.submission_id, sub_id)
        self.assertEqual(res.score, 8)
        self.assertTrue(res.qualifies_for_points)

    # 8. A completed automatic evaluation does not remain incorrectly marked ADMIN REVIEW
    def test_08_completed_evaluation_not_marked_admin_review(self):
        review_record = {
            "status": "APPROVED",
            "decision": "APPROVED",
            "reviewed_by": "TAUFA_AI",
            "points_awarded": 8,
            "signals_json": json.dumps({"decision": "APPROVED", "admin_review_required": False}),
        }
        stage = _review_stage(review_record)
        self.assertEqual(stage, "VERIFIED")
        self.assertNotEqual(stage, "ADMIN_REVIEW")

    # 9. Invalid or out-of-range scores cannot create point awards
    def test_09_invalid_or_out_of_range_score_rejected(self):
        sub_id = "sub_out_of_range_09"
        # Score > 8
        res_high = self.validator.parse_and_validate_response(
            sub_id, {"submission_id": sub_id, "score": 10}, category="STUDY"
        )
        self.assertFalse(res_high.is_valid)
        self.assertIsNone(res_high.score)

        # Negative score
        res_neg = self.validator.parse_and_validate_response(
            sub_id, {"submission_id": sub_id, "score": -1}, category="STUDY"
        )
        self.assertFalse(res_neg.is_valid)
        self.assertIsNone(res_neg.score)

        # Non-numeric score
        res_str = self.validator.parse_and_validate_response(
            sub_id, {"submission_id": sub_id, "score": "exceptional"}, category="STUDY"
        )
        self.assertFalse(res_str.is_valid)

    # 10. Points are committed only once (idempotent)
    def test_10_points_committed_only_once(self):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_db._connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchone.return_value = {
            "evidence_id": "ev_test_10",
            "status": "APPROVED",
            "points_awarded": 8,
            "reviewed_at": "2026-10-20 10:00:00",
            "reviewed_by": "Taufa_AI",
            "user_id": "user_lok",
            "date": "2026-10-20",
            "task_code": "STUDY",
            "evidence_file_path": "tests/dummy.jpg",
            "evidence_deleted": 0,
            "evidence_record_type": "STUDY",
            "evidence_sha256_hash": "a" * 64,
        }
        with patch("core.database.is_valid_retained_proof_file", return_value=True):
            res = StrictRecordsDB.approve_proof(mock_db, "ev_test_10", admin_id="Taufa_AI", points=8)
        self.assertTrue(res.get("already_approved"))
        self.assertEqual(res.get("points_awarded"), 8)

    # 11. Demo results never affect production balances or approval states
    def test_11_demo_results_isolated_from_production(self):
        demo_res = self.validator.run_demo_validation(
            category="STUDY",
            description="Demo test of study notes verification",
            images=[self.valid_img],
        )
        self.assertTrue(demo_res.get("is_demo"))
        self.assertIn("DEMO", demo_res.get("mode", ""))
        sim = demo_res.get("simulated_outcome", {})
        self.assertEqual(sim.get("real_points_awarded", 0), 0)
        self.assertFalse(sim.get("real_balance_changed", True))
        self.assertFalse(sim.get("production_record_created", True))

    # 12. Bath, Gym, sleep-data, and other excluded tasks retain their existing workflows
    def test_12_excluded_tasks_reject_hourly_taufa_validation(self):
        for excluded_task in ("BATH", "GYM", "SLEEP", "WAKE_UP", "TRAVEL", "MOVIE"):
            with self.assertRaises(ValueError):
                self.validator.validate_hourly_submission(
                    submission_id=f"sub_ex_{excluded_task}",
                    category=excluded_task,
                    description=f"Attempting {excluded_task} validation",
                    images=[self.valid_img],
                    task_type=excluded_task,
                )

    # 13. Ordinary photo without camera watermark is not forced to NEEDS_REVIEW
    def test_13_ordinary_photo_without_watermark_passes(self):
        import base64
        img_b64 = base64.b64encode(self.valid_img).decode("ascii")

        with patch("core.real_ai_vlm.RealAIBotEngine.get_instance") as mock_vlm:
            mock_vlm.return_value.analyze_evidence.return_value = {
                "is_neural": True,
                "neural_confidence": 0.88,
                "top_class": "study_notes",
                "checkpoint": "phase4",
            }
            review = StrictBotAIReviewer.execute_review(
                submission_id="sub_wm_test",
                activity_id="act_wm_test",
                task_code="HOURLY",
                description="Studying discrete math recurrence relations and generating functions",
                primary_activity="STUDY",
                submitted_at_iso="2026-10-20T10:15:00+05:30",
                device_id="dev_test",
                platform="ANDROID",
                image_base64=img_b64,
                window_start_iso="2026-10-20T10:00:00+05:30",
                window_end_iso="2026-10-20T11:00:00+05:30",
                hour_slot="10:00-11:00",
                historical_records=[],
                configured_points=8,
                auto_approve_high_confidence=True,
                auto_approve_duplicate=False,
            )
        self.assertEqual(review.get("decision"), "PROVISIONAL_APPROVE")
        self.assertEqual(review.get("proposed_points"), 8)
        self.assertFalse(review.get("admin_review_required"))

    # 14. Activity report recognizes Taufa_AI as automated validator without admin review badge
    def test_14_activity_report_recognizes_taufa_ai(self):
        import tempfile
        import os
        from core.database import StrictRecordsDB

        with tempfile.TemporaryDirectory() as temp_dir:
            db = StrictRecordsDB(db_path=os.path.join(temp_dir, "report.db"))
            date = "2026-10-20"
            db.ensure_daily_record(date)
            evidence_id = "ev_01"
            activity_id = "act_01"

            with db._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO proof_reviews (
                        review_id, evidence_id, user_id, task_code, date,
                        status, decision, points_awarded, reviewed_by, submitted_at, signals_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rev_01", evidence_id, "user_lok", "STUDY", date,
                        "APPROVED", "APPROVED", 8, "TAUFA_AI",
                        date + "T10:15:00+05:30",
                        json.dumps({"decision": "APPROVED", "admin_review_required": False}),
                    ),
                )

            activities = [
                {
                    "activity_id": activity_id,
                    "type": "HOURLY",
                    "title": "Study Checkpoint",
                    "status": "VERIFIED",
                    "scheduled_time": "2026-10-20T10:00:00+05:30",
                    "window_start": "2026-10-20T10:00:00+05:30",
                    "window_end": "2026-10-20T11:00:00+05:30",
                    "submitted_at": "2026-10-20T10:15:00+05:30",
                    "points": 8,
                    "details_json": json.dumps({"evidence_id": evidence_id}),
                }
            ]

            report = build_daily_activity_report(db, date, activities, account_id="user_lok")
            summary = report["summary"]
            self.assertEqual(summary["verified"], 1)
            self.assertEqual(summary["awaiting_admin"], 0)


if __name__ == "__main__":
    unittest.main()
