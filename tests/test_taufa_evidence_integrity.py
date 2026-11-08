"""
StrictRecords — Comprehensive Taufa Evidence Integrity Test Suite
==================================================================
Tests all 15 required scenarios from Section 7 of the specification:
  Case 1:  Relevant study notes and a matching study description.
  Case 2:  Relevant academic evidence with a clear description.
  Case 3:  Random unrelated imagery labelled STUDY (Gate forces score to 0).
  Case 4:  A screenshot unrelated to the selected category.
  Case 5:  A description that contradicts the image.
  Case 6:  Multiple images where some are relevant but others contradict the claim.
  Case 7:  An exact duplicate image.
  Case 8:  A resized or cropped near-duplicate.
  Case 9:  A genuine Taufa response containing a valid score.
  Case 10: Taufa returning a response with no score (Fail-closed).
  Case 11: A timeout or authentication failure (HTTP 401/403 or TimeoutError).
  Case 12: A malformed or out-of-range score (NaN, infinity, string, 9, -1).
  Case 13: A demo/mock response mistakenly presented to production code.
  Case 14: A stale response from a different submission (submission_id mismatch).
  Case 15: Retrying a request after a network interruption (idempotent, no dup attendance).
"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
from PIL import Image

from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaValidationResult,
    TaufaDuplicateResult,
    VALIDATOR_VERSION,
)
from core.database import StrictRecordsDB


def _create_test_image(color=(240, 240, 250), size=(120, 120), tag=b"") -> bytes:
    """Creates a PNG image in memory with optional embedded bytes."""
    img = Image.new("RGB", size, color)
    # Add varying pixels
    for x in range(0, size[0], 5):
        for y in range(0, size[1], 5):
            img.putpixel((x, y), ((x * 11) % 256, (y * 17) % 256, (x + y) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    val = buf.getvalue()
    if tag:
        val = val + tag
    return val


class TestTaufaEvidenceIntegrity(unittest.TestCase):
    def setUp(self):
        self.validator = TaufaAiValidator(
            endpoint_url="https://api.taufa.example.com/v1/validate",
            api_key="sec_live_taufa_key_12345",
        )
        self.study_notes_img = _create_test_image(color=(250, 250, 255), tag=b"study_notes_linear_algebra")
        self.academic_img = _create_test_image(color=(240, 245, 250), tag=b"textbook_operating_systems")
        self.random_img = _create_test_image(color=(100, 100, 100), tag=b"random_unrelated_shoe")
        self.screenshot_img = _create_test_image(color=(50, 50, 50), tag=b"gaming_screenshot_unrelated")
        self.contradict_img = _create_test_image(color=(180, 180, 180), tag=b"food_recipe_dinner")

    # -------------------------------------------------------------------------
    # Case 1: Relevant study notes and a matching study description
    # -------------------------------------------------------------------------
    def test_01_relevant_study_notes_matching_description(self):
        sub_id = "sub_case_01"
        category = "STUDY"
        desc = "Studying linear algebra chapter 3 on matrix determinants and eigenvalues"

        # Offline heuristic
        heur = self.validator.evaluate_offline_heuristics(category, desc, [self.study_notes_img])
        self.assertTrue(heur["category_match"])
        self.assertTrue(heur["evidence_match"])
        self.assertTrue(heur["description_match"])

        # Live structured response parsing
        raw_taufa = {
            "submission_id": sub_id,
            "score": 8,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Clear handwritten notes on eigenvalues matching claimed study description.",
            "warnings": [],
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category=category, description=desc)
        self.assertTrue(res.is_valid)
        self.assertTrue(res.qualifies_for_points)
        self.assertEqual(res.score, 8)
        self.assertEqual(res.validator_status, "SUCCESS")

    # -------------------------------------------------------------------------
    # Case 2: Relevant academic evidence with a clear description
    # -------------------------------------------------------------------------
    def test_02_relevant_academic_evidence_clear_description(self):
        sub_id = "sub_case_02"
        category = "COLLEGE"
        desc = "Completed computer systems assignment reading textbook section on memory hierarchy"

        raw_taufa = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Textbook diagram and academic notes support college coursework.",
            "warnings": [],
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category=category, description=desc)
        self.assertTrue(res.is_valid)
        self.assertTrue(res.qualifies_for_points)
        self.assertEqual(res.score, 7)

    # -------------------------------------------------------------------------
    # Case 3: Random unrelated imagery labelled STUDY (Gate forces score to 0)
    # -------------------------------------------------------------------------
    def test_03_random_unrelated_imagery_labelled_study(self):
        sub_id = "sub_case_03"
        category = "STUDY"
        desc = "Studied algorithms and complexity for sixty minutes"

        # Even if the external model erroneously outputs score 8, the category-consistency gate must fail closed
        raw_taufa_fake = {
            "submission_id": sub_id,
            "score": 8,
            "max_score": 8,
            "category_match": False,  # Model or validator detects mismatch
            "description_match": True,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "validator_status": "SUCCESS",
            "warnings": ["CATEGORY_MISMATCH", "IRRELEVANT_EVIDENCE"],
            "reason": "Submitted image shows an unrelated object, not academic study material.",
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa_fake, category=category, description=desc)
        self.assertEqual(res.score, 0, "Score must be overridden to 0 when category match fails")
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.validator_status, "REJECTED")
        self.assertIn("CATEGORY_MISMATCH", res.warnings)

        # Also verify offline heuristics catch unrelated image
        heur = self.validator.evaluate_offline_heuristics(category, desc, [self.random_img])
        self.assertFalse(heur["category_match"])
        self.assertEqual(heur["score"], 0)

    # -------------------------------------------------------------------------
    # Case 4: A screenshot unrelated to the selected category
    # -------------------------------------------------------------------------
    def test_04_screenshot_unrelated_to_selected_category(self):
        sub_id = "sub_case_04"
        category = "STUDY"
        desc = "Studying biology textbook pages for upcoming exam"

        raw_taufa = {
            "submission_id": sub_id,
            "score": 6,
            "max_score": 8,
            "category_match": False,
            "description_match": False,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "validator_status": "REJECTED",
            "warnings": ["CATEGORY_MISMATCH", "IRRELEVANT_EVIDENCE"],
            "reason": "Screenshot shows gaming application rather than biology textbook.",
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category=category, description=desc)
        self.assertEqual(res.score, 0)
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.validator_status, "REJECTED")

    # -------------------------------------------------------------------------
    # Case 5: A description that contradicts the image
    # -------------------------------------------------------------------------
    def test_05_description_contradicts_image(self):
        sub_id = "sub_case_05"
        category = "STUDY"
        desc = "Cooking dinner recipe and meal prep in the kitchen"

        heur = self.validator.evaluate_offline_heuristics(category, desc, [self.study_notes_img])
        self.assertFalse(heur["category_match"])
        self.assertEqual(heur["score"], 0)

        raw_taufa = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": False,
            "evidence_match": False,
            "evidence_quality": "LOW",
            "validator_status": "REJECTED",
            "warnings": ["DESCRIPTION_CONTRADICTS_EVIDENCE"],
            "reason": "Description contradicts submitted visual evidence.",
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category=category, description=desc)
        self.assertEqual(res.score, 0)
        self.assertFalse(res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Case 6: Multiple images where some are relevant but others contradict
    # -------------------------------------------------------------------------
    def test_06_multiple_images_mixed_evidence(self):
        sub_id = "sub_case_06"
        category = "STUDY"
        desc = "Lecture review on distributed operating systems"

        # Heuristic test with 1 study notes and 1 unrelated gaming screenshot
        heur = self.validator.evaluate_offline_heuristics(category, desc, [self.study_notes_img, self.screenshot_img])
        self.assertEqual(heur["validator_status"], "REVIEW_REQUIRED")
        self.assertIn("MIXED_EVIDENCE_DETECTED", heur["warnings"])
        self.assertLessEqual(heur["score"], 4)

        raw_taufa = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "mixed_evidence": True,
            "warnings": ["MIXED_EVIDENCE_DETECTED"],
            "reason": "Submission contains mixed evidence (both notes and unrelated screenshots).",
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category=category, description=desc)
        self.assertEqual(res.validator_status, "REVIEW_REQUIRED")
        self.assertFalse(res.qualifies_for_points, "Mixed evidence cannot automatically qualify for points")
        self.assertLessEqual(res.score, 4)

    # -------------------------------------------------------------------------
    # Case 7: An exact duplicate image
    # -------------------------------------------------------------------------
    def test_07_exact_duplicate_image(self):
        sub_id = "sub_case_07"
        history = [
            {
                "submission_id": "sub_prior_100",
                "sha256": self.validator.compute_sha256(self.study_notes_img),
                "dhash": self.validator.compute_dhash(self.study_notes_img),
            }
        ]
        dup_res = self.validator.check_duplicate_evidence(
            submission_id=sub_id,
            category="STUDY",
            description="Studying notes again",
            images=[self.study_notes_img],
            historical_records=history,
            task_type="HOURLY",
        )
        self.assertTrue(dup_res.duplicate_detected)
        self.assertEqual(dup_res.match_type, "EXACT")

        val_res = self.validator.validate_hourly_submission(
            submission_id=sub_id,
            category="STUDY",
            description="Studying notes again",
            images=[self.study_notes_img],
            task_type="HOURLY",
            historical_records=history,
            check_duplicate=True,
        )
        self.assertEqual(val_res.score, 0)
        self.assertEqual(val_res.validator_status, "REJECTED")
        self.assertFalse(val_res.qualifies_for_points)

    # -------------------------------------------------------------------------
    # Case 8: A resized or cropped near-duplicate
    # -------------------------------------------------------------------------
    def test_08_resized_or_cropped_near_duplicate(self):
        sub_id = "sub_case_08"
        # Resize image to create near duplicate with identical perceptual dhash
        pil_img = Image.open(io.BytesIO(self.study_notes_img)).resize((80, 80))
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        resized_bytes = buf.getvalue()

        dh = self.validator.compute_dhash(resized_bytes)
        history = [
            {
                "submission_id": "sub_prior_200",
                "sha256": "different_hash_due_to_resize",
                "dhash": dh,
            }
        ]
        dup_res = self.validator.check_duplicate_evidence(
            submission_id=sub_id,
            category="STUDY",
            description="Studying notes cropped version",
            images=[resized_bytes],
            historical_records=history,
            task_type="HOURLY",
        )
        self.assertTrue(dup_res.duplicate_detected)
        self.assertIn(dup_res.match_type, ("NEAR_DUPLICATE", "EXACT"))

    # -------------------------------------------------------------------------
    # Case 9: A genuine Taufa response containing a valid score
    # -------------------------------------------------------------------------
    def test_09_genuine_taufa_response_valid_score(self):
        sub_id = "sub_case_09"
        raw_taufa = {
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Authentic academic study notes verified.",
            "warnings": [],
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category="STUDY", description="Discrete math proofs")
        self.assertTrue(res.is_valid)
        self.assertTrue(res.qualifies_for_points)
        self.assertEqual(res.score, 7)

    # -------------------------------------------------------------------------
    # Case 10: Taufa returning a response with no score (Fail-closed)
    # -------------------------------------------------------------------------
    def test_10_taufa_response_missing_score(self):
        sub_id = "sub_case_10"
        raw_taufa = {
            "submission_id": sub_id,
            # 'score' is intentionally omitted
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "validator_status": "SUCCESS",
            "reason": "Validation performed but score field missing.",
        }
        res = self.validator.parse_and_validate_response(sub_id, raw_taufa, category="STUDY")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.score)
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.validator_status, "FAILED_FINAL")
        self.assertIn("MISSING_SCORE", res.warnings)

    # -------------------------------------------------------------------------
    # Case 11: A timeout or authentication failure (HTTP 401/403 or TimeoutError)
    # -------------------------------------------------------------------------
    def test_11_timeout_or_authentication_failure(self):
        sub_id = "sub_case_11"

        # Sub-test 11a: Timeout
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
            res = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category="STUDY",
                description="Study session notes",
                images=[self.study_notes_img],
            )
            self.assertIsNone(res.score)
            self.assertFalse(res.qualifies_for_points)
            self.assertEqual(res.validator_status, "FAILED_RETRYABLE")
            self.assertIn("TIMEOUT", res.warnings)

        # Sub-test 11b: Authentication failure (HTTP 401)
        err = urllib.error.HTTPError(
            url="https://api.taufa.example.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "Invalid API Key"}'),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            res_auth = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category="STUDY",
                description="Study session notes",
                images=[self.study_notes_img],
            )
            self.assertIsNone(res_auth.score)
            self.assertFalse(res_auth.qualifies_for_points)
            self.assertEqual(res_auth.validator_status, "FAILED_FINAL")
            self.assertIn("HTTP_401", res_auth.warnings)

    # -------------------------------------------------------------------------
    # Case 12: A malformed or out-of-range score
    # -------------------------------------------------------------------------
    def test_12_malformed_or_out_of_range_score(self):
        sub_id = "sub_case_12"

        # Sub-test 12a: String score
        res_str = self.validator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": "invalid_number"})
        self.assertIsNone(res_str.score)
        self.assertFalse(res_str.qualifies_for_points)
        self.assertIn("INVALID_NUMERIC_SCORE", res_str.warnings)

        # Sub-test 12b: Boolean score
        res_bool = self.validator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": True})
        self.assertIsNone(res_bool.score)
        self.assertFalse(res_bool.qualifies_for_points)
        self.assertIn("INVALID_NUMERIC_SCORE", res_bool.warnings)

        # Sub-test 12c: NaN score
        res_nan = self.validator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": float("nan")})
        self.assertIsNone(res_nan.score)
        self.assertFalse(res_nan.qualifies_for_points)
        self.assertIn("INVALID_NUMERIC_SCORE", res_nan.warnings)

        # Sub-test 12d: Out of bounds (score = 15 > 8)
        res_high = self.validator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": 15})
        self.assertIsNone(res_high.score)
        self.assertFalse(res_high.qualifies_for_points)
        self.assertIn("SCORE_OUT_OF_BOUNDS", res_high.warnings)

        # Sub-test 12e: Out of bounds (score = -2 < 0)
        res_neg = self.validator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": -2})
        self.assertIsNone(res_neg.score)
        self.assertFalse(res_neg.qualifies_for_points)
        self.assertIn("SCORE_OUT_OF_BOUNDS", res_neg.warnings)

    # -------------------------------------------------------------------------
    # Case 13: A demo/mock response mistakenly presented to production code
    # -------------------------------------------------------------------------
    def test_13_demo_mock_response_presented_to_production(self):
        sub_id = "sub_case_13"
        demo_payload = {
            "submission_id": sub_id,
            "score": 8,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "is_demo": True,
            "mode": "DEMO — NO REAL POINTS",
            "is_mock": True,
        }
        res = self.validator.parse_and_validate_response(sub_id, demo_payload, category="STUDY")
        self.assertIsNone(res.score, "Demo mock response in production must not yield a score")
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.validator_status, "FAILED_FINAL")
        self.assertIn("MOCK_OR_DEMO_RESPONSE_REJECTED", res.warnings)

    # -------------------------------------------------------------------------
    # Case 14: A stale response from a different submission
    # -------------------------------------------------------------------------
    def test_14_stale_response_submission_id_mismatch(self):
        expected_sub_id = "sub_case_14_current"
        stale_resp = {
            "submission_id": "sub_old_submission_99",  # Mismatched ID
            "score": 8,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "validator_status": "SUCCESS",
        }
        res = self.validator.parse_and_validate_response(expected_sub_id, stale_resp, category="STUDY")
        self.assertIsNone(res.score)
        self.assertFalse(res.qualifies_for_points)
        self.assertEqual(res.validator_status, "FAILED_FINAL")
        self.assertIn("SUBMISSION_ID_MISMATCH", res.warnings)

    # -------------------------------------------------------------------------
    # Case 15: Retrying a request after a network interruption
    # -------------------------------------------------------------------------
    def test_15_retry_after_network_interruption(self):
        sub_id = "sub_case_15_retry"
        category = "STUDY"
        desc = "Studying operating systems deadlock handling"

        # Attempt 1: Network failure
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable")):
            res_fail = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category=category,
                description=desc,
                images=[self.study_notes_img],
            )
            self.assertEqual(res_fail.validator_status, "FAILED_RETRYABLE")
            self.assertIsNone(res_fail.score)
            self.assertFalse(res_fail.qualifies_for_points)

        # Attempt 2: Successful retry with the same submission_id
        success_body = json.dumps({
            "submission_id": sub_id,
            "score": 7,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Retry successful: valid notes on deadlock handling verified.",
            "warnings": [],
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = success_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res_retry = self.validator.validate_hourly_submission(
                submission_id=sub_id,
                category=category,
                description=desc,
                images=[self.study_notes_img],
            )
            self.assertEqual(res_retry.submission_id, sub_id)
            self.assertEqual(res_retry.score, 7)
            self.assertTrue(res_retry.qualifies_for_points)
            self.assertEqual(res_retry.validator_status, "SUCCESS")

    # -------------------------------------------------------------------------
    # Authoritative Ingestion Gate: Reject unverified hourly points synced from Android
    # -------------------------------------------------------------------------
    def test_authoritative_sync_quarantines_unverified_points(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_strictrecords.db")
            db = StrictRecordsDB(db_path)
            # Push an event simulating the bug: points = 8, but audit is unverified Automated Validator
            fake_sync_payload = {
                "device_id": "test_device",
                "device_token": "token123",
                "events": [
                    {
                        "event_id": "evt_fake_bypassed_88",
                        "event_type": "SCORE_EVENT",
                        "payload": {
                            "event_id": "se_fake_bypassed_88",
                            "date": "2026-10-21",
                            "points": 8,
                            "task_code": "STUDY",
                            "evidence_id": "ev_fake_123",
                            "activity_id": "ACT-20261021-1000-HOURLY",
                            "audit": {
                                "evidence_id": "ev_fake_123",
                                "reviewed_by": "Automated Validator",
                                "approval_source": "ANDROID_ADMIN",
                                "category": "STUDY",
                                "points": 8,
                            },
                        },
                    }
                ],
            }
            db.process_sync_events_batch(
                device_id="test_device",
                events=fake_sync_payload["events"],
                account_id="user_test",
            )

            # Verify that score_events has NOT committed 8 points
            with db._connect() as conn:
                se = conn.execute("SELECT points FROM score_events WHERE event_id = 'se_fake_bypassed_88'").fetchone()
                self.assertIsNone(se, "Unverified hourly score event must be quarantined and not inserted into score_events")

                # Verify that proof_reviews is quarantined with 0 points and NEEDS_REVIEW status
                pr = conn.execute("SELECT status, points_awarded FROM proof_reviews WHERE evidence_id = 'ev_fake_123'").fetchone()
                self.assertIsNotNone(pr)
                self.assertEqual(pr[0], "NEEDS_REVIEW")
                self.assertEqual(pr[1], 0, "Quarantined proof must receive 0 points")


if __name__ == "__main__":
    unittest.main()
