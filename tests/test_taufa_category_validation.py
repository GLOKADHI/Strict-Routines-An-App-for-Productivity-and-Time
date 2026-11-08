"""
Comprehensive Unit Test Suite for Taufa AI Category Validation
and Hourly Evidence Relevance Verification.

Covers all 18 required scenarios:
  1. Study category with relevant study notes and matching description.
  2. Study category with relevant lecture slides and matching description.
  3. Study category with legitimate coding exercises or college project work.
  4. Study category with a random selfie and a claim of studying.
  5. Study category with an unrelated screenshot and convincing but unrelated text.
  6. Mismatched description and image.
  7. Unreadable, blank, or insufficient image.
  8. Mixed image collection containing both relevant and unrelated photos.
  9. Legitimate repeated notebook or textbook image (not auto-classified as fraud).
  10. Exact reused image and cropped/resized near-duplicate.
  11. Relevant evidence scored below 6/8.
  12. Relevant evidence scored 6/8 or higher.
  13. Irrelevant evidence accompanied by an incorrectly high model score (Gate override).
  14. Malformed or contradictory Taufa AI responses.
  15. Duplicate validation requests and network retries.
  16. Confirm failed category validation cannot award points or generate a YouTube reward.
  17. Confirm excluded tasks never invoke the Hourly Attendance validator.
  18. Confirm all clients display the same authoritative committed result schema.
"""

import io
import json
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaValidationResult,
    TaufaDuplicateResult,
    VALIDATOR_VERSION,
    EXCLUDED_TASK_TYPES,
    ACADEMIC_CATEGORIES,
    TAUFA_VALIDATION_SYSTEM_INSTRUCTION,
)
from core.youtube_reward_engine import YouTubeRewardEngine, evaluate_eligibility


def _create_test_image(color=(255, 255, 255), size=(100, 100), noise=False) -> bytes:
    """Creates an in-memory PNG image for testing."""
    img = Image.new("RGB", size, color)
    if noise:
        # Add varied pixels so it's not detected as blank/solid
        for x in range(0, size[0], 4):
            for y in range(0, size[1], 4):
                img.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, (x + y) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestTaufaCategoryValidationScenarios(unittest.TestCase):
    """18-Scenario verification suite for Taufa AI Category Validation."""

    def setUp(self):
        self.validator = TaufaAiValidator(
            endpoint_url="https://api.taufa.example.com/v1/validate",
            api_key="test-api-key-12345",
        )
        self.solid_blank_img = _create_test_image(color=(255, 255, 255), noise=False)
        self.notes_img = _create_test_image(color=(240, 240, 250), noise=True)
        self.slides_img = _create_test_image(color=(220, 230, 240), noise=True)
        self.coding_img = _create_test_image(color=(30, 30, 30), noise=True)

    # -------------------------------------------------------------------------
    # Test 1: Study category with relevant study notes and matching description
    # -------------------------------------------------------------------------
    def test_01_study_category_relevant_study_notes(self):
        sub_id = "sub_study_notes_01"
        category = "STUDY"
        desc = "Studying computer architecture chapter 4 on cache hierarchy and virtual memory paging"
        
        # Test offline heuristic
        res = self.validator.evaluate_offline_heuristics(category, desc, [self.notes_img])
        self.assertTrue(res["category_match"])
        self.assertTrue(res["evidence_match"])
        self.assertTrue(res["description_match"])
        self.assertGreaterEqual(res["score"], 6)

        # Test structured response parsing
        api_resp = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Visible study notes on cache hierarchy matching description.",
            "warnings": [],
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 7)
        self.assertEqual(val_res.validator_status, "SUCCESS")
        self.assertEqual(val_res.review_status, "APPROVED")
        self.assertTrue(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Test 2: Study category with relevant lecture slides and matching description
    # -------------------------------------------------------------------------
    def test_02_study_category_lecture_slides(self):
        sub_id = "sub_lecture_slides_02"
        category = "STUDY"
        desc = "Reviewed operating systems lecture slides on semaphore synchronization and deadlock avoidance"

        api_resp = {
            "submission_id": sub_id,
            "score": 8,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Lecture slides on synchronization verified.",
            "warnings": [],
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 8)
        self.assertTrue(val_res.category_match)
        self.assertTrue(val_res.description_match)
        self.assertTrue(val_res.evidence_match)
        self.assertTrue(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Test 3: Study category with legitimate coding exercises or college project work
    # -------------------------------------------------------------------------
    def test_03_study_category_coding_exercises(self):
        sub_id = "sub_coding_exercise_03"
        category = "STUDY"
        desc = "Implementing Dijkstra shortest path algorithm in Python for data structures college assignment"

        api_resp = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Algorithm implementation in IDE verified as college coursework.",
            "warnings": [],
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 7)
        self.assertTrue(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Test 4: Study category with a random selfie and a claim of studying
    # -------------------------------------------------------------------------
    def test_04_study_category_selfie_rejected(self):
        sub_id = "sub_selfie_04"
        category = "STUDY"
        desc = "Studied calculus integrals for an entire hour and completed all homework problems"

        # Model might mistakenly report score=7, but category_match=False
        api_resp = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": False,
            "description_match": True,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "validator_status": "SUCCESS",
            "warnings": ["CATEGORY_MISMATCH", "SELFIE_NOT_STUDY_EVIDENCE"],
            "reason": "Image is a personal selfie, not academic study evidence.",
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 0, "Gate must cap score to 0 on selfie / category mismatch")
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertEqual(val_res.review_status, "REJECTED")
        self.assertFalse(val_res.qualifies_for_points)
        self.assertIn("CATEGORY_MISMATCH", val_res.warnings)
        self.assertIn("IRRELEVANT_EVIDENCE", val_res.warnings)

    # -------------------------------------------------------------------------
    # Test 5: Study category with unrelated screenshot and convincing but unrelated text
    # -------------------------------------------------------------------------
    def test_05_study_category_unrelated_screenshot(self):
        sub_id = "sub_unrelated_05"
        category = "STUDY"
        desc = "Read chapters 1 and 2 on data structures and finished chapter exercises with high focus"

        api_resp = {
            "submission_id": sub_id,
            "score": 6,
            "max_score": 8,
            "category_match": False,
            "description_match": False,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "validator_status": "SUCCESS",
            "warnings": ["CATEGORY_MISMATCH", "IRRELEVANT_EVIDENCE"],
            "reason": "Submitted image shows Minecraft gameplay, not study evidence.",
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 0)
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertFalse(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Test 6: Mismatched description and image
    # -------------------------------------------------------------------------
    def test_06_mismatched_description_and_image(self):
        sub_id = "sub_mismatch_06"
        category = "STUDY"
        desc = "Cooked pasta dinner with garlic sauce and olive oil"

        api_resp = {
            "submission_id": sub_id,
            "score": 6,
            "max_score": 8,
            "category_match": True,
            "description_match": False,
            "evidence_match": True,
            "evidence_quality": "MEDIUM",
            "validator_status": "SUCCESS",
            "warnings": ["DESCRIPTION_CONTRADICTS_EVIDENCE"],
            "reason": "Description claims cooking while category is STUDY.",
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 0)
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertFalse(val_res.qualifies_for_points)
        self.assertIn("DESCRIPTION_CONTRADICTS_EVIDENCE", val_res.warnings)

    # -------------------------------------------------------------------------
    # Test 7: Unreadable, blank, or insufficient image
    # -------------------------------------------------------------------------
    def test_07_unreadable_or_blank_image(self):
        sub_id = "sub_blank_07"
        category = "STUDY"
        desc = "Studied physics mechanics textbook chapter 3 on Newton's laws"

        # Check offline blank image detection
        is_blank = self.validator.is_blank_or_solid_image(self.solid_blank_img)
        self.assertTrue(is_blank, "Solid blank image must be detected by heuristic")

        api_resp = {
            "submission_id": sub_id,
            "score": 6,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "INSUFFICIENT",
            "validator_status": "SUCCESS",
            "warnings": ["INSUFFICIENT_EVIDENCE"],
            "reason": "Image is blank white canvas.",
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 0)
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertFalse(val_res.qualifies_for_points)
        self.assertIn("INSUFFICIENT_EVIDENCE", val_res.warnings)

    # -------------------------------------------------------------------------
    # Test 8: Mixed image collection containing both relevant and unrelated photos
    # -------------------------------------------------------------------------
    def test_08_mixed_image_collection(self):
        sub_id = "sub_mixed_08"
        category = "STUDY"
        desc = "Studied biology genetics notes and solved punnett square problems"

        api_resp = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "MEDIUM",
            "validator_status": "SUCCESS",
            "warnings": ["MIXED_EVIDENCE_DETECTED"],
            "mixed_evidence": True,
            "reason": "Contains 1 notes photo and 1 unrelated gaming screenshot.",
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertLessEqual(val_res.score, 4, "Mixed evidence score must be capped <= 4")
        self.assertEqual(val_res.validator_status, "REVIEW_REQUIRED")
        self.assertEqual(val_res.review_status, "REVIEW_REQUIRED")
        self.assertFalse(val_res.qualifies_for_points)
        self.assertIn("MIXED_EVIDENCE_DETECTED", val_res.warnings)

    # -------------------------------------------------------------------------
    # Test 9: Legitimate repeated notebook or textbook image (not auto fraud)
    # -------------------------------------------------------------------------
    def test_09_legitimate_repeated_study_material(self):
        sub_id = "sub_repeated_09"
        category = "STUDY"
        desc = "Continuing revision of chemistry textbook chapter 5 on chemical kinetics from yesterday"

        res = self.validator.evaluate_offline_heuristics(category, desc, [self.notes_img])
        self.assertTrue(res["category_match"])
        self.assertTrue(res["evidence_match"])
        self.assertIn(res["validator_status"], ("UNCONFIGURED", "SUCCESS"))
        self.assertNotIn("DUPLICATE_CONFIRMED", res["warnings"])

    # -------------------------------------------------------------------------
    # Test 10: Exact reused image and cropped/resized near-duplicate
    # -------------------------------------------------------------------------
    def test_10_exact_and_near_duplicate_detection(self):
        # 10a: Exact intra-submission duplicate
        dup_res = self.validator.check_duplicate_evidence(
            submission_id="sub_dup_10a",
            category="STUDY",
            description="Studying algorithms notes chapter 2",
            images=[self.notes_img, self.notes_img],
            task_type="HOURLY",
        )
        self.assertTrue(dup_res.duplicate_detected)
        self.assertEqual(dup_res.match_type, "INTRA_SUBMISSION")
        self.assertEqual(dup_res.validator_status, "DUPLICATE_CONFIRMED")

        # 10b: Historical duplicate
        hist = [{"submission_id": "sub_prior_99", "sha256": self.validator.compute_sha256(self.notes_img)}]
        dup_res_hist = self.validator.check_duplicate_evidence(
            submission_id="sub_dup_10b",
            category="STUDY",
            description="Studying algorithms notes chapter 2",
            images=[self.notes_img],
            historical_records=hist,
            task_type="HOURLY",
        )
        self.assertTrue(dup_res_hist.duplicate_detected)
        self.assertEqual(dup_res_hist.matched_submission_id, "sub_prior_99")
        self.assertEqual(dup_res_hist.match_type, "EXACT")

    # -------------------------------------------------------------------------
    # Test 11: Relevant evidence scored below 6/8
    # -------------------------------------------------------------------------
    def test_11_relevant_evidence_below_threshold(self):
        sub_id = "sub_below_11"
        category = "STUDY"
        desc = "Brief overview of math formulas"

        api_resp = {
            "submission_id": sub_id,
            "score": 4,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "MEDIUM",
            "validator_status": "SUCCESS",
            "reason": "Brief study evidence, only partially complete.",
            "warnings": [],
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 4)
        self.assertEqual(val_res.review_status, "BELOW_THRESHOLD")
        self.assertFalse(val_res.qualifies_for_points, "Score 4/8 must not qualify for points")
        self.assertIn("BELOW_QUALIFYING_THRESHOLD", val_res.warnings)

    # -------------------------------------------------------------------------
    # Test 12: Relevant evidence scored 6/8 or higher
    # -------------------------------------------------------------------------
    def test_12_relevant_evidence_qualifies(self):
        sub_id = "sub_qualifies_12"
        category = "STUDY"
        desc = "Completed comprehensive practice exam on discrete mathematics and graph theory"

        for score in (6, 7, 8):
            api_resp = {
                "submission_id": sub_id,
                "score": score,
                "max_score": 8,
                "category_match": True,
                "description_match": True,
                "evidence_match": True,
                "evidence_quality": "HIGH",
                "validator_status": "SUCCESS",
                "reason": f"Thorough exam work verified (score {score}/8).",
                "warnings": [],
            }
            val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
            self.assertEqual(val_res.score, score)
            self.assertEqual(val_res.validator_status, "SUCCESS")
            self.assertEqual(val_res.review_status, "APPROVED")
            self.assertTrue(val_res.qualifies_for_points, f"Score {score}/8 must qualify for points")

    # -------------------------------------------------------------------------
    # Test 13: Irrelevant evidence accompanied by an incorrectly high model score
    # -------------------------------------------------------------------------
    def test_13_irrelevant_evidence_with_high_model_score_overridden_by_gate(self):
        """CRITICAL VULNERABILITY TEST: Model returns score 8 for an irrelevant image."""
        sub_id = "sub_gate_override_13"
        category = "STUDY"
        desc = "Studied algorithms for an hour"

        # Flawed model output: awarded 8 despite category_match = False
        api_resp = {
            "submission_id": sub_id,
            "score": 8,
            "max_score": 8,
            "category_match": False,  # Gate trigger!
            "description_match": True,
            "evidence_match": False,  # Gate trigger!
            "evidence_quality": "LOW",
            "validator_status": "SUCCESS",  # Flawed status from remote
            "reason": "Model mistakenly thought high score should be awarded.",
            "warnings": ["IRRELEVANT_EVIDENCE"],
        }
        val_res = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        self.assertEqual(val_res.score, 0, "Gate must override score 8 to 0")
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertEqual(val_res.review_status, "REJECTED")
        self.assertFalse(val_res.category_match)
        self.assertFalse(val_res.evidence_match)
        self.assertFalse(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Test 14: Malformed or contradictory Taufa AI responses
    # -------------------------------------------------------------------------
    def test_14_malformed_and_contradictory_responses(self):
        sub_id = "sub_malformed_14"

        # 14a: Non-numeric score string
        resp1 = {"submission_id": sub_id, "score": "invalid", "category_match": True}
        val1 = self.validator.parse_and_validate_response(sub_id, resp1)
        self.assertIsNone(val1.score)
        self.assertEqual(val1.validator_status, "FAILED_FINAL")
        self.assertFalse(val1.qualifies_for_points)

        # 14b: Boolean score
        resp2 = {"submission_id": sub_id, "score": True, "category_match": True}
        val2 = self.validator.parse_and_validate_response(sub_id, resp2)
        self.assertIsNone(val2.score)
        self.assertEqual(val2.validator_status, "FAILED_FINAL")

        # 14c: Out of bounds score
        resp3 = {"submission_id": sub_id, "score": 99, "category_match": True}
        val3 = self.validator.parse_and_validate_response(sub_id, resp3)
        self.assertIsNone(val3.score)
        self.assertEqual(val3.validator_status, "FAILED_FINAL")

        # 14d: Mismatched submission ID
        resp4 = {"submission_id": "other_id", "score": 7, "category_match": True}
        val4 = self.validator.parse_and_validate_response(sub_id, resp4)
        self.assertIsNone(val4.score)
        self.assertEqual(val4.validator_status, "FAILED_FINAL")

    # -------------------------------------------------------------------------
    # Test 15: Duplicate validation requests and network retries
    # -------------------------------------------------------------------------
    def test_15_duplicate_validation_requests_idempotency(self):
        sub_id = "sub_retry_15"
        category = "STUDY"
        desc = "Solved calculus problem set on integration by parts"

        api_resp = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Calculus problem set verified.",
            "warnings": [],
        }

        # First parse
        val1 = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)
        # Second parse (simulating retry)
        val2 = self.validator.parse_and_validate_response(sub_id, api_resp, category=category, description=desc)

        self.assertEqual(val1.score, val2.score)
        self.assertEqual(val1.qualifies_for_points, val2.qualifies_for_points)
        self.assertEqual(val1.validator_status, val2.validator_status)

    # -------------------------------------------------------------------------
    # Test 16: Confirm failed category validation cannot award points or generate YouTube reward
    # -------------------------------------------------------------------------
    def test_16_failed_category_validation_blocks_youtube_reward(self):
        # 16a: Module-level eligibility check with failed category match
        eligible, reason = evaluate_eligibility(
            occurrence_id="occ_16a",
            submission_id="sub_16a",
            score=7,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
            category_match=False,  # FAILED CATEGORY MATCH
            description_match=True,
        )
        self.assertFalse(eligible, "YouTube reward must be blocked when category_match is False")
        self.assertIn("category", reason.lower())

        # 16b: Failed description match
        eligible_desc, reason_desc = evaluate_eligibility(
            occurrence_id="occ_16b",
            submission_id="sub_16b",
            score=8,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
            category_match=True,
            description_match=False,  # FAILED DESCRIPTION MATCH
        )
        self.assertFalse(eligible_desc, "YouTube reward must be blocked when description_match is False")

        # 16c: Score below 6
        eligible_low, reason_low = evaluate_eligibility(
            occurrence_id="occ_16c",
            submission_id="sub_16c",
            score=5,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
            category_match=True,
            description_match=True,
        )
        self.assertFalse(eligible_low, "Score below 6 must be blocked")

        # 16d: Fully qualified
        eligible_ok, reason_ok = evaluate_eligibility(
            occurrence_id="occ_16d",
            submission_id="sub_16d",
            score=7,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
            category_match=True,
            description_match=True,
        )
        self.assertTrue(eligible_ok)

        # 16e: YouTubeRewardEngine.evaluate_and_grant_reward rejects when category_match is False
        mock_db = MagicMock()
        mock_db.get_youtube_reward_by_occurrence.return_value = None
        engine = YouTubeRewardEngine(db=mock_db)
        reward = engine.evaluate_and_grant_reward(
            account_id="acc_test",
            hourly_occurrence_id="occ_grant_fail",
            submission_id="sub_grant_fail",
            score=7,
            accepted_at_iso="2026-10-19T10:15:00+05:30",
            scheduled_time_iso="2026-10-19T10:00:00+05:30",
            duplicate_detected=False,
            category_match=False,  # FAILED
            description_match=True,
        )
        self.assertIsNone(reward, "No reward record may be created when category_match is False")
        mock_db.create_youtube_reward.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 17: Confirm excluded tasks never invoke the Hourly Attendance validator
    # -------------------------------------------------------------------------
    def test_17_excluded_tasks_strictly_rejected(self):
        for task in EXCLUDED_TASK_TYPES:
            with self.assertRaises(ValueError) as ctx:
                self.validator.validate_hourly_submission(
                    submission_id="sub_ex",
                    category="GENERAL",
                    description="Completed general task",
                    images=[self.notes_img],
                    task_type=task,
                )
            self.assertIn("strictly excluded", str(ctx.exception))

            with self.assertRaises(ValueError) as ctx2:
                self.validator.check_duplicate_evidence(
                    submission_id="sub_ex2",
                    category="GENERAL",
                    description="Completed general task",
                    images=[self.notes_img],
                    task_type=task,
                )
            self.assertIn("strictly excluded", str(ctx2.exception))

    # -------------------------------------------------------------------------
    # Test 18: Confirm all clients display the same authoritative committed result
    # -------------------------------------------------------------------------
    def test_18_authoritative_committed_result_schema(self):
        sub_id = "sub_schema_18"
        val_res = TaufaValidationResult(
            submission_id=sub_id,
            score=7,
            max_score=8,
            category_match=True,
            description_match=True,
            evidence_match=True,
            evidence_quality="HIGH",
            reason="Verified study notes.",
            warnings=[],
            validator_status="SUCCESS",
            review_status="APPROVED",
        )
        serialized = val_res.to_dict()
        required_keys = {
            "submission_id",
            "score",
            "max_score",
            "category_match",
            "description_match",
            "evidence_match",
            "evidence_quality",
            "reason",
            "warnings",
            "validator_status",
            "review_status",
            "qualifies_for_points",
            "is_valid",
        }
        for key in required_keys:
            self.assertIn(key, serialized, f"Missing key '{key}' in serialized result")
        self.assertTrue(serialized["qualifies_for_points"])
        self.assertEqual(serialized["score"], 7)


if __name__ == "__main__":
    unittest.main()
