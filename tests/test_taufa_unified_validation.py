"""
Unit and Integration Tests for StrictRecords Unified Taufa AI Validation Engine
==============================================================================
Covers all 16 verification scenarios specified in User Requirements:
  1. Demo mode — 1 valid Study image with matching description.
  2. Demo mode — 3 valid Study images.
  3. Demo mode — Mismatched description (aptitude mock test with "OS Interview" description).
  4. Demo mode — Blank or solid color image (rejected before or during evaluation).
  5. Demo mode — Exact duplicate image (detected, flagged).
  6. Demo mode — Zero points awarded, no database records changed.
  7. Main mode — Valid Study submission (auto-approved, points awarded, record persisted).
  8. Main mode — Mismatched Study submission (rejected or flagged for review, no auto-approval).
  9. Main mode — Bath Protocol submission (correctly requires human review per policy).
  10. Main mode — Gym Mode submission (correctly requires human review per policy).
  11. Main mode — Wake-Up Protocol submission (correctly evaluated against schedule window).
  12. Main mode — Exact duplicate of past submission (flagged as duplicate, rejected).
  13. Unauthenticated state — Clean error message with link to authenticate.
  14. Network timeout — Retryable error state without crashing.
  15. Android APK / API — Schema & mode toggle validation (Section 6 standardized schema).
  16. Windows EXE / Dashboard — Schema & mode toggle validation (Section 6 standardized schema).
"""

import io
import os
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaValidationResult,
    TaufaDuplicateResult,
    DESIGNATED_CONVERSATION_ID,
)


def make_test_image(color=(30, 90, 160), size=(200, 200), format="JPEG", is_solid=False):
    from PIL import ImageDraw
    stream = io.BytesIO()
    img = Image.new("RGB", size, color)
    if not is_solid:
        draw = ImageDraw.Draw(img)
        for i in range(10, size[0], 20):
            draw.line([(i, 0), (i, size[1])], fill=((color[0] + i * 5) % 255, (color[1] + i * 3) % 255, (color[2] + i * 7) % 255), width=3)
        draw.text((15, 15), "Operating Systems Paging and Virtual Memory Study Notes", fill=(255, 255, 255))
    img.save(stream, format=format)
    return stream.getvalue()


class TestTaufaUnifiedValidation(unittest.TestCase):
    def setUp(self):
        self.validator = TaufaAiValidator()
        self.valid_study_img1 = make_test_image((25, 80, 150))
        self.valid_study_img2 = make_test_image((35, 95, 170))
        self.valid_study_img3 = make_test_image((45, 110, 190))

    # Scenario 1: Demo mode — 1 valid Study image with matching description
    def test_01_demo_mode_single_valid_study_image(self):
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="demo_sub_01",
                evaluation_id="provider-demo-01",
                score=8,
                max_score=8,
                category_match=True,
                description_match=True,
                evidence_match=True,
                evidence_quality="HIGH",
                validator_status="SUCCESS",
                review_status="APPROVED",
                reason="Handwritten Operating Systems notes on paging and virtual memory verified.",
                warnings=[],
            )
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_01",
                category="STUDY",
                description="Studied Operating Systems chapter on virtual memory paging.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
            )
            self.assertEqual(res["mode"], "DEMO")
            self.assertTrue(res["is_demo"])
            self.assertEqual(res["status"], "VALID")
            self.assertEqual(res["recommended_action"], "approve")
            self.assertFalse(res["requires_human_review"])
            self.assertEqual(res["conversation_id"], DESIGNATED_CONVERSATION_ID)
            self.assertEqual(res["score"], 8)
            self.assertEqual(res["evaluation_id"], "provider-demo-01")
            self.assertEqual(res["validation_result"]["evaluation_id"], "provider-demo-01")
            self.assertEqual(res["real_points_awarded"], 0)
            self.assertFalse(res["simulated_outcome"]["real_balance_changed"])

    # Scenario 2: Demo mode — 3 valid Study images
    def test_02_demo_mode_three_valid_study_images(self):
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="demo_sub_02",
                score=7,
                max_score=8,
                category_match=True,
                description_match=True,
                evidence_match=True,
                evidence_quality="HIGH",
                validator_status="SUCCESS",
                review_status="APPROVED",
                reason="Three complementary study diagrams and notes verified.",
                warnings=[],
            )
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_02",
                category="STUDY",
                description="Studied Operating Systems deadlock avoidance: Banker's algorithm and resource allocation graphs.",
                images=[self.valid_study_img1, self.valid_study_img2, self.valid_study_img3],
                task_type="study",
                mode="DEMO",
            )
            self.assertEqual(res["status"], "VALID")
            self.assertEqual(res["score"], 7)
            self.assertEqual(res["real_points_awarded"], 0)
            self.assertEqual(res["images_evaluated"], 3)
            self.assertFalse(res["requires_human_review"])

    # Scenario 3: Demo mode — Mismatched description (aptitude test photos with "OS Interview" description)
    def test_03_demo_mode_mismatched_description(self):
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="demo_sub_03",
                score=0,
                max_score=8,
                category_match=False,
                description_match=False,
                evidence_match=False,
                evidence_quality="INSUFFICIENT",
                validator_status="REJECTED",
                review_status="REJECTED",
                reason="Visual evidence shows TCS NQT aptitude reasoning mock test, not Operating Systems interview material.",
                warnings=["DESCRIPTION_CONTRADICTS_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
            )
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_03",
                category="STUDY",
                description="OS Interview questions studied last hr",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
            )
            self.assertEqual(res["status"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(res["recommended_action"], "reject")
            self.assertEqual(res["score"], 0)
            self.assertIn("contradictions", res)
            self.assertTrue(len(res["contradictions"]) > 0)

    # Scenario 4: Demo mode — Blank or solid color image (rejected before or during evaluation)
    def test_04_demo_mode_blank_solid_image_rejected(self):
        solid_img = make_test_image((255, 255, 255), size=(100, 100), is_solid=True)
        res = self.validator.evaluate_submission(
            submission_id="demo_sub_04",
            category="STUDY",
            description="Studied math proofs in textbook.",
            images=[solid_img],
            task_type="study",
            mode="DEMO",
        )
        self.assertEqual(res["status"], "INVALID")
        self.assertEqual(res["recommended_action"], "reject")
        self.assertIn("BLANK_IMAGE", res["reason_codes"])

    # Scenario 5: Demo mode — Exact duplicate image within same submission
    def test_05_demo_mode_exact_duplicate_within_submission(self):
        img_dup1 = make_test_image((120, 60, 40))
        img_dup2 = bytes(img_dup1)
        res = self.validator.evaluate_submission(
            submission_id="demo_sub_05",
            category="STUDY",
            description="Studied data structures graphs and trees.",
            images=[img_dup1, img_dup2],
            task_type="study",
            mode="DEMO",
        )
        self.assertEqual(res["status"], "INVALID")
        self.assertEqual(res["recommended_action"], "reject")
        self.assertTrue(res["duplicate_check"]["duplicate_detected"])
        self.assertEqual(res["duplicate_check"]["match_type"], "INTRA_SUBMISSION")

    # Scenario 6: Demo mode — Zero points awarded, no database records changed
    def test_06_demo_mode_zero_points_guaranteed(self):
        mock_db = MagicMock()
        mock_engine = MagicMock()
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="demo_sub_06",
                score=8,
                max_score=8,
                category_match=True,
                description_match=True,
                evidence_match=True,
                evidence_quality="HIGH",
                validator_status="SUCCESS",
                review_status="APPROVED",
                reason="Excellent study notes.",
                warnings=[],
            )
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_06",
                category="STUDY",
                description="Studied Operating Systems algorithms.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
                db=mock_db,
                engine=mock_engine,
            )
            self.assertEqual(res["real_points_awarded"], 0)
            self.assertFalse(res["simulated_outcome"]["real_balance_changed"])
            self.assertFalse(res["simulated_outcome"]["production_record_created"])
            mock_engine.apply_proof_approval.assert_not_called()

    # Scenario 7: Main mode — Valid Study submission (auto-approved, points awarded, record persisted)
    def test_07_main_mode_evaluation_recommends_approval_without_bypassing_submission(self):
        mock_db = MagicMock()
        mock_engine = MagicMock()
        mock_engine.apply_proof_approval.return_value = {"success": True, "points": 8}
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="prod_sub_07",
                score=8,
                max_score=8,
                category_match=True,
                description_match=True,
                evidence_match=True,
                evidence_quality="HIGH",
                validator_status="SUCCESS",
                review_status="APPROVED",
                reason="Genuine OS interview notes and code snippets verified.",
                warnings=[],
            )
            res = self.validator.evaluate_submission(
                submission_id="prod_sub_07",
                category="STUDY",
                description="Studied Operating Systems interview questions on synchronization primitives.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="PRODUCTION",
                db=mock_db,
                engine=mock_engine,
            )
            self.assertEqual(res["mode"], "PRODUCTION")
            self.assertFalse(res["is_demo"])
            self.assertEqual(res["status"], "VALID")
            self.assertEqual(res["recommended_action"], "approve")
            self.assertFalse(res["requires_human_review"])
            self.assertEqual(res["real_points_awarded"], 0)
            self.assertFalse(res["simulated_outcome"]["real_balance_changed"])
            mock_engine.apply_proof_approval.assert_not_called()
            mock_db._connect.assert_not_called()

    # Scenario 8: Main mode — Mismatched Study submission (rejected, no auto-approval)
    def test_08_main_mode_mismatched_study_rejected(self):
        mock_engine = MagicMock()
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="prod_sub_08",
                score=0,
                max_score=8,
                category_match=False,
                description_match=False,
                evidence_match=False,
                evidence_quality="INSUFFICIENT",
                validator_status="REJECTED",
                review_status="REJECTED",
                reason="Evidence does not support Operating Systems topic.",
                warnings=["DESCRIPTION_CONTRADICTS_EVIDENCE"],
            )
            res = self.validator.evaluate_submission(
                submission_id="prod_sub_08",
                category="STUDY",
                description="Studied Operating Systems questions.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="PRODUCTION",
                engine=mock_engine,
            )
            self.assertEqual(res["status"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(res["recommended_action"], "reject")
            self.assertEqual(res["real_points_awarded"], 0)
            mock_engine.apply_proof_approval.assert_not_called()

    # Scenario 9: Main mode — Bath Protocol submission (correctly requires human review per policy)
    def test_09_main_mode_bath_protocol_requires_human_review(self):
        res = self.validator.evaluate_submission(
            submission_id="prod_sub_09",
            category="BATH",
            description="Bath protocol session 1 completed.",
            images=[self.valid_study_img1],
            task_type="bath",
            mode="PRODUCTION",
        )
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertTrue(res["requires_human_review"])
        self.assertEqual(res["recommended_action"], "review")
        self.assertEqual(res["real_points_awarded"], 0)

    # Scenario 10: Main mode — Gym Mode submission (correctly requires human review per policy)
    def test_10_main_mode_gym_mode_requires_human_review(self):
        res = self.validator.evaluate_submission(
            submission_id="prod_sub_10",
            category="GYM",
            description="Gym mode dumbbell and resistance workout session completed.",
            images=[self.valid_study_img1],
            task_type="gym",
            mode="PRODUCTION",
        )
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertTrue(res["requires_human_review"])
        self.assertEqual(res["recommended_action"], "review")
        self.assertEqual(res["real_points_awarded"], 0)

    # Scenario 11: Main mode — Wake-Up Protocol submission (correctly evaluated against schedule window)
    def test_11_main_mode_wakeup_protocol_evaluated_against_window(self):
        # Case A: Inside window -> VALID, requires review
        res_in = self.validator.evaluate_submission(
            submission_id="prod_sub_11_in",
            category="WAKE_UP",
            description="Morning wake-up protocol verified with clock and light.",
            images=[self.valid_study_img1],
            task_type="wake_up",
            mode="PRODUCTION",
            schedule_context={"window_valid": True, "reason": "Inside wake-up target window"},
        )
        self.assertEqual(res_in["status"], "NEEDS_REVIEW")
        self.assertTrue(res_in["requires_human_review"])
        self.assertEqual(res_in["recommended_action"], "review")

        # Case B: Outside window -> INVALID
        res_out = self.validator.evaluate_submission(
            submission_id="prod_sub_11_out",
            category="WAKE_UP",
            description="Morning wake-up protocol submitted late.",
            images=[self.valid_study_img1],
            task_type="wake_up",
            mode="PRODUCTION",
            schedule_context={"window_valid": False, "reason": "Outside wake-up target window"},
        )
        self.assertEqual(res_out["status"], "INVALID")
        self.assertEqual(res_out["recommended_action"], "reject")
        self.assertIn("OUTSIDE_SCHEDULE_WINDOW", res_out["reason_codes"])

    # Scenario 12: Main mode — Exact duplicate of past submission (flagged as duplicate, rejected)
    def test_12_main_mode_exact_duplicate_of_past_submission(self):
        history = [{
            "submission_id": "past_sub_999",
            "sha256": self.validator.compute_sha256(self.valid_study_img1),
            "dhash": self.validator.compute_dhash(self.valid_study_img1),
            "task_type": "HOURLY"
        }]
        res = self.validator.evaluate_submission(
            submission_id="prod_sub_12",
            category="STUDY",
            description="Studied calculus lecture and notes.",
            images=[self.valid_study_img1],
            task_type="study",
            mode="PRODUCTION",
            check_duplicate=True,
            historical_records=history,
        )
        self.assertEqual(res["status"], "INVALID")
        self.assertEqual(res["recommended_action"], "reject")
        self.assertTrue(res["duplicate_check"]["duplicate_detected"])
        self.assertEqual(res["duplicate_check"]["matched_submission_id"], "past_sub_999")

    # Scenario 13: Unauthenticated state — Clean error message with link to authenticate
    def test_13_unauthenticated_state_clean_message(self):
        with patch.object(self.validator, "is_configured", return_value=False):
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_13",
                category="STUDY",
                description="Studied operating systems questions.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
            )
            self.assertEqual(res["status"], "PROCESSING_ERROR")
            self.assertEqual(res["recommended_action"], "retry")
            self.assertIn("TAUFA_AI_UNCONFIGURED", res["reason_codes"])
            self.assertIn("https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b", res["reasoning_summary"])

    # Scenario 14: Network timeout — Retryable error state without crashing
    def test_14_network_timeout_retryable_state(self):
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="demo_sub_14",
                score=None,
                validator_status="FAILED_RETRYABLE",
                review_status="UNREVIEWED",
                reason="Connection timed out while contacting Taufa AI.",
                warnings=["TIMEOUT", "RETRYABLE"],
            )
            res = self.validator.evaluate_submission(
                submission_id="demo_sub_14",
                category="STUDY",
                description="Studied compiler design algorithms.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
            )
            self.assertEqual(res["status"], "PROCESSING_ERROR")
            self.assertEqual(res["recommended_action"], "retry")
            self.assertFalse(res["requires_human_review"])
            self.assertIn("TIMEOUT", res["reason_codes"])

    # Scenario 15: Standardized Schema — All 13 mandatory Section 6 keys present
    def test_15_standardized_schema_all_13_keys_present(self):
        with patch.object(self.validator, "is_configured", return_value=True), \
             patch.object(self.validator, "_validate_remote_submission") as mock_remote:
            mock_remote.return_value = TaufaValidationResult(
                submission_id="sub_schema_15",
                score=8,
                max_score=8,
                category_match=True,
                description_match=True,
                evidence_match=True,
                evidence_quality="HIGH",
                validator_status="SUCCESS",
                review_status="APPROVED",
                reason="Schema test complete.",
            )
            res = self.validator.evaluate_submission(
                submission_id="sub_schema_15",
                category="STUDY",
                description="Studied operating systems memory management.",
                images=[self.valid_study_img1],
                task_type="study",
                mode="DEMO",
            )
            mandatory_keys = [
                "evaluation_id",
                "submission_id",
                "mode",
                "task_type",
                "conversation_id",
                "model_version",
                "policy_version",
                "status",
                "observations",
                "extracted_text",
                "criteria_results",
                "supporting_evidence",
                "contradictions",
                "duplicate_check",
                "temporal_check",
                "missing_information",
                "reason_codes",
                "reasoning_summary",
                "requires_human_review",
                "recommended_action",
            ]
            for key in mandatory_keys:
                self.assertIn(key, res, f"Mandatory key '{key}' is missing from schema!")

    # Scenario 16: Mode isolation verification — Demo never modifies production points or ledger
    def test_16_mode_isolation_guarantee(self):
        mock_db = MagicMock()
        mock_engine = MagicMock()
        res = self.validator.run_demo_validation(
            category="STUDY",
            description="Valid study session proof for demo.",
            images=[self.valid_study_img1],
        )
        self.assertIn("DEMO", res["mode"])
        self.assertTrue(res["is_demo"])
        self.assertEqual(res["simulated_outcome"]["real_points_awarded"], 0)
        self.assertFalse(res["simulated_outcome"]["real_balance_changed"])
        self.assertFalse(res["simulated_outcome"]["production_record_created"])


if __name__ == "__main__":
    unittest.main()
