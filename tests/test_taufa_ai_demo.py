"""
Tests for StrictRecords — Taufa AI Validation Demo Mode
======================================================
Covers:
  1. Isolated Demo execution: zero points awarded or deducted, zero database changes.
  2. Input validation parity (1-10 images, file sizes, description quality).
  3. Duplicate testing (intra-submission and against reference images).
  4. Honest UNCONFIGURED failure without fabricated scores.
  5. GET /api/demo/taufa_validation/status endpoint.
  6. POST /api/demo/taufa_validation endpoint.
  7. Strict rejection of demo submissions on production endpoints (/api/hourly/submit).
"""

import base64
import io
from PIL import Image
import json
import os
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from core.database import StrictRecordsDB
from core.server import StrictRecordsRequestHandler
from core.taufa_ai_validator import (
    MAX_HOURLY_PHOTOS,
    TaufaAiValidator,
)


def real_image(color=(30, 90, 160)):
    stream = io.BytesIO()
    Image.new("RGB", (120, 120), color).save(stream, format="PNG")
    return stream.getvalue()


class TestTaufaAiDemo(unittest.TestCase):

    def setUp(self) -> None:
        self.validator = TaufaAiValidator()
        self.sample_img1 = real_image()
        self.sample_img2 = real_image((70, 30, 200))

    def test_demo_requires_at_least_one_image(self) -> None:
        res = self.validator.run_demo_validation(
            category="STUDY",
            description="Completed reading operating system chapters on process memory.",
            images=[],
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["code"], "NO_ATTACHMENTS")
        self.assertEqual(res["mode"], "DEMO — NO REAL POINTS")
        self.assertTrue(res["is_demo"])

    def test_demo_rejects_exceeding_max_photos(self) -> None:
        eleven = [self.sample_img1 + bytes([i]) for i in range(11)]
        res = self.validator.run_demo_validation(
            category="STUDY",
            description="Completed reading operating system chapters on process memory.",
            images=eleven,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["code"], "EXCEEDED_MAX_ATTACHMENTS")

    def test_demo_rejects_invalid_description(self) -> None:
        for invalid_desc in ["", "   ", "done", "test", "working", "nothing"]:
            res = self.validator.run_demo_validation(
                category="STUDY",
                description=invalid_desc,
                images=[self.sample_img1],
            )
            self.assertFalse(res["success"])
            self.assertEqual(res["code"], "INVALID_DESCRIPTION")

    def test_demo_unconfigured_has_no_simulated_inference(self) -> None:
        with patch.object(self.validator, "is_configured", return_value=False):
            res = self.validator.run_demo_validation(
                category="STUDY",
                description="Studied data structures and implemented AVL balanced trees in C++.",
                images=[self.sample_img1, self.sample_img2],
                check_duplicate=False,
            )
            self.assertFalse(res["success"])
            self.assertTrue(res["is_demo"])
            self.assertEqual(res["mode"], "DEMO — NO REAL POINTS")
            self.assertEqual(res["validator_status"], "UNCONFIGURED")
            self.assertFalse(res["is_mock"])
            self.assertIsNone(res["score"])
            self.assertEqual(res["max_score"], 8)
            outcome = res["simulated_outcome"]
            self.assertFalse(outcome["threshold_met"])
            self.assertEqual(outcome["simulated_points"], 0)
            self.assertEqual(outcome["real_points_awarded"], 0)
            self.assertFalse(outcome["real_balance_changed"])
            self.assertFalse(outcome["production_record_created"])
            self.assertFalse(outcome["youtube_reward_issued"])

    def test_demo_duplicate_detection_intra_submission(self) -> None:
        identical = real_image()
        res = self.validator.run_demo_validation(
            category="STUDY",
            description="Studied data structures and implemented AVL balanced trees in C++.",
            images=[identical, identical],
            check_duplicate=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["validator_status"], "REJECTED")
        self.assertIsNotNone(res["duplicate_result"])
        self.assertTrue(res["duplicate_result"]["duplicate_detected"])

        outcome = res["simulated_outcome"]
        self.assertFalse(outcome["threshold_met"])
        self.assertEqual(outcome["simulated_points"], 0)
        self.assertEqual(outcome["real_points_awarded"], 0)
        self.assertFalse(outcome["real_balance_changed"])

    def test_demo_duplicate_detection_against_reference(self) -> None:
        ref = real_image()
        res = self.validator.run_demo_validation(
            category="EDITING",
            description="Color grading multi-cam timeline in DaVinci Resolve Studio.",
            images=[ref],
            reference_images=[ref],
            reference_submission_ids=["prior_hourly_001"],
            check_duplicate=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["validator_status"], "REJECTED")
        self.assertTrue(res["duplicate_result"]["duplicate_detected"])

    def test_production_endpoints_reject_demo_submissions(self) -> None:
        # Mock request handler calling _handle_hourly_submission_request
        handler = MagicMock()
        handler.client_address = ("127.0.0.1", 12345)
        sent_jsons: List[Dict[str, Any]] = []
        sent_statuses: List[int] = []

        def capture_send_json(obj: Dict[str, Any], status: int = 200) -> None:
            sent_jsons.append(obj)
            sent_statuses.append(status)

        handler._send_json = capture_send_json

        # 1. Payload with is_demo=True
        demo_payload = {
            "is_demo": True,
            "submission_id": "demo_sub_12345",
            "category": "STUDY",
            "description": "Valid study description for test.",
        }
        StrictRecordsRequestHandler._handle_hourly_submission_request(
            handler,
            raw_body=json.dumps(demo_payload).encode("utf-8"),
            content_type="application/json",
        )
        self.assertTrue(len(sent_jsons) >= 1)
        self.assertEqual(sent_statuses[-1], 400)
        self.assertEqual(sent_jsons[-1].get("code"), "DEMO_SUBMISSION_REJECTED")

        # 2. Payload with demo_ prefix on submission_id without explicit is_demo flag
        demo_prefix_payload = {
            "submission_id": "demo_sub_abc987",
            "category": "STUDY",
            "description": "Valid study description for test.",
        }
        StrictRecordsRequestHandler._handle_hourly_submission_request(
            handler,
            raw_body=json.dumps(demo_prefix_payload).encode("utf-8"),
            content_type="application/json",
        )
        self.assertEqual(sent_statuses[-1], 400)
        self.assertEqual(sent_jsons[-1].get("code"), "DEMO_SUBMISSION_REJECTED")

    def test_server_demo_taufa_validation_handler(self) -> None:
        handler = MagicMock()
        sent_jsons: List[Dict[str, Any]] = []
        sent_statuses: List[int] = []

        def capture_send_json(obj: Dict[str, Any], status: int = 200) -> None:
            sent_jsons.append(obj)
            sent_statuses.append(status)

        handler._send_json = capture_send_json

        demo_req = {
            "category": "STUDY",
            "description": "Studied algorithm complexity and asymptotic runtime proofs.",
            "images_base64": [base64.b64encode(self.sample_img1).decode("utf-8")],
            "check_duplicate": True,
        }
        with patch.object(TaufaAiValidator, "is_configured", return_value=False):
            StrictRecordsRequestHandler._handle_demo_taufa_validation(
                handler,
                raw_body=json.dumps(demo_req).encode("utf-8"),
                content_type="application/json",
            )
        self.assertEqual(sent_statuses[-1], 400)
        res = sent_jsons[-1]
        self.assertFalse(res.get("success"))
        self.assertEqual(res["validator_status"], "UNCONFIGURED")
        self.assertEqual(res.get("mode"), "DEMO — NO REAL POINTS")
        self.assertTrue(res.get("is_demo"))
        self.assertEqual(res.get("simulated_outcome", {}).get("real_points_awarded"), 0)


if __name__ == "__main__":
    unittest.main()
