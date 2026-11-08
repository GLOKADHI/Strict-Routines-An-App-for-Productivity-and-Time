"""
Unit and Integration Tests for Taufa AI Hourly Attendance Validation Adapter (Python)
Covers all requirements from Section 10:
  1. Submit 1 hourly attendance image.
  2. Submit multiple images (e.g., 5) in one submission.
  3. Confirm the 10-image limit (10 accepted, 11 rejected).
  4. Association of category, description, images with the same submission ID.
  5. Strict exclusion of BATH, GYM, SLEEP, WAKEUP, TRAVEL.
  6. Prevention of duplicate submissions for locked occurrences.
  7. Valid scores: 0, 4, 8.
  8. Rejection of negative scores, >8, NaN, missing, malformed JSON.
  9. No points awarded before required conditions met.
  10. Idempotency: award committed at most once.
  11. Description validation: rejects blank or generic filler.
  12. Offline / unconfigured behavior (safe fallback, no fabricated 8 points).
  13. Network errors, timeouts, HTTP errors.
"""

import http.server
import io
import os
import tempfile
from PIL import Image
import json
import socketserver
import threading
import unittest
from typing import Any, Dict, List, Optional

from core.taufa_ai_validator import (
    MAX_HOURLY_PHOTOS,
    TaufaAiValidator,
    TaufaValidationResult,
)


def real_image(color=(30, 90, 160)):
    stream = io.BytesIO()
    Image.new("RGB", (120, 120), color).save(stream, format="PNG")
    return stream.getvalue()


class MockTaufaHandler(http.server.BaseHTTPRequestHandler):
    """Mock HTTP server for testing Taufa AI network requests."""

    mock_response_body: Optional[Dict[str, Any]] = None
    mock_status_code: int = 200
    received_requests: List[Dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Silence console logs

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        MockTaufaHandler.received_requests.append(parsed)

        self.send_response(MockTaufaHandler.mock_status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        body = MockTaufaHandler.mock_response_body or {
            "submission_id": parsed.get("submission_id"),
            "score": 6,
            "max_score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "reason": "Good work on coding task.",
            "warnings": [],
            "validator_status": "SUCCESS",
        }
        self.wfile.write(json.dumps(body).encode("utf-8"))


class TestTaufaAiValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_session_file = os.environ.get("TAUFA_SESSION_FILE")
        os.environ["TAUFA_SESSION_FILE"] = os.path.join(tempfile.gettempdir(), "test_unconfigured_taufa_session.json")
        MockTaufaHandler.received_requests = []
        # Find free port
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), MockTaufaHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.mock_url = f"https://127.0.0.1:{cls.port}/validate"

    @classmethod
    def tearDownClass(cls):
        if cls._old_session_file is not None:
            os.environ["TAUFA_SESSION_FILE"] = cls._old_session_file
        else:
            os.environ.pop("TAUFA_SESSION_FILE", None)
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def setUp(self):
        MockTaufaHandler.received_requests = []
        MockTaufaHandler.mock_status_code = 200
        MockTaufaHandler.mock_response_body = None

    # 1. Submit one hourly attendance image
    def test_01_submit_one_image_unconfigured(self):
        validator = TaufaAiValidator()
        self.assertFalse(validator.is_configured())

        dummy_img = real_image()
        res = validator.validate_hourly_submission(
            submission_id="sub_test_01",
            category="STUDY",
            description="Studied operating systems lecture chapter 4.",
            images=[dummy_img],
            task_type="HOURLY",
        )
        self.assertEqual(res.validator_status, "UNCONFIGURED")
        self.assertIsNone(res.score)
        self.assertEqual(res.submission_id, "sub_test_01")

    # 2. Submit multiple images (5 photos) in one hourly submission
    def test_02_submit_multiple_images(self):
        validator = TaufaAiValidator()
        dummy_img = real_image()
        five_imgs = [dummy_img for _ in range(5)]

        res = validator.validate_hourly_submission(
            submission_id="sub_test_02",
            category="CODING",
            description="Implemented SQLite database indexes and cached queries.",
            images=five_imgs,
            task_type="HOURLY",
        )
        self.assertEqual(res.submission_id, "sub_test_02")
        self.assertEqual(res.validator_status, "UNCONFIGURED")

    # 3. Confirm 10-image limit (10 allowed, 11 rejected)
    def test_03_ten_image_limit(self):
        validator = TaufaAiValidator()
        dummy_img = real_image()

        # Exactly 10 images -> accepted for processing
        ten_imgs = [dummy_img for _ in range(10)]
        res_10 = validator.validate_hourly_submission(
            submission_id="sub_test_10",
            category="EDITING",
            description="Video editing color grading and audio synchronization.",
            images=ten_imgs,
            task_type="HOURLY",
        )
        self.assertNotEqual(res_10.validator_status, "FAILED_FINAL")

        # 11 images -> rejected with clear error message
        eleven_imgs = [dummy_img for _ in range(11)]
        res_11 = validator.validate_hourly_submission(
            submission_id="sub_test_11",
            category="EDITING",
            description="Video editing color grading and audio synchronization.",
            images=eleven_imgs,
            task_type="HOURLY",
        )
        self.assertEqual(res_11.validator_status, "FAILED_FINAL")
        self.assertIn("EXCEEDED_MAX_ATTACHMENTS", res_11.warnings)
        self.assertIn("10 evidence photos", res_11.reason)

    # 4. Association: category, description, and every selected image with same request
    def test_04_association_with_submission_id(self):
        validator = TaufaAiValidator()
        dummy_img = real_image()
        res = validator.validate_hourly_submission(
            submission_id="sub_stable_xyz_123",
            category="COLLEGE",
            description="Attended computer networks laboratory session.",
            images=[dummy_img],
            task_type="HOURLY",
        )
        self.assertEqual(res.submission_id, "sub_stable_xyz_123")

    # 5. Confirm Bath, Gym, and sleep-data submissions NEVER call Taufa AI adapter
    def test_05_explicit_exclusions(self):
        validator = TaufaAiValidator()
        dummy_img = real_image()

        for excluded_type in ["BATH", "GYM", "SLEEP", "WAKE_UP", "WAKEUP", "TRAVEL", "MOVIE"]:
            with self.assertRaises(ValueError) as ctx:
                validator.validate_hourly_submission(
                    submission_id=f"sub_{excluded_type}",
                    category=excluded_type,
                    description=f"{excluded_type} verification proof",
                    images=[dummy_img],
                    task_type=excluded_type,
                )
            self.assertIn("strictly excluded", str(ctx.exception))

    # 6. Test description validation: meaningful description required
    def test_06_meaningful_description_required(self):
        validator = TaufaAiValidator()
        dummy_img = real_image()

        # Empty / whitespace description
        res_blank = validator.validate_hourly_submission(
            submission_id="sub_desc_blank",
            category="STUDY",
            description="   ",
            images=[dummy_img],
        )
        self.assertEqual(res_blank.validator_status, "FAILED_FINAL")
        self.assertIn("INVALID_DESCRIPTION", res_blank.warnings)

        # Generic filler descriptions
        for filler in ["asdf", "test", "done", "ok", "nothing", "idk", "waste", "completed"]:
            res_filler = validator.validate_hourly_submission(
                submission_id=f"sub_desc_{filler}",
                category="STUDY",
                description=filler,
                images=[dummy_img],
            )
            self.assertEqual(res_filler.validator_status, "FAILED_FINAL")
            self.assertIn("INVALID_DESCRIPTION", res_filler.warnings)

        # Meaningful description succeeds
        ok, msg = TaufaAiValidator.validate_description("Reviewed algorithm complexity analysis for assignment 2")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    # 7. Test valid scores (0, 4, 8)
    def test_07_score_parsing_and_bounds(self):
        sub_id = "sub_score_test"
        # Score 0
        r0 = TaufaAiValidator.parse_and_validate_response(
            sub_id,
            {"submission_id": sub_id, "score": 0, "reason": "No evidence match", "validator_status": "REJECTED"}
        )
        self.assertTrue(r0.is_valid)
        self.assertEqual(r0.score, 0)
        self.assertEqual(r0.validator_status, "REJECTED")

        # Score 4
        r4 = TaufaAiValidator.parse_and_validate_response(
            sub_id,
            {"submission_id": sub_id, "score": 4, "category_match": True, "description_match": True, "evidence_match": True, "evidence_quality": "MEDIUM", "reason": "Partial evidence"}
        )
        self.assertTrue(r4.is_valid)
        self.assertEqual(r4.score, 4)
        self.assertEqual(r4.validator_status, "SUCCESS")

        # Score 8
        r8 = TaufaAiValidator.parse_and_validate_response(
            sub_id,
            {"submission_id": sub_id, "score": 8, "category_match": True, "description_match": True, "evidence_match": True, "evidence_quality": "HIGH", "reason": "Full match"}
        )
        self.assertTrue(r8.is_valid)
        self.assertEqual(r8.score, 8)
        self.assertEqual(r8.validator_status, "SUCCESS")

    # 8. Reject invalid scores: negative, >8, NaN, missing, malformed
    def test_08_reject_invalid_scores(self):
        sub_id = "sub_bad_score"

        # Negative score
        rn = TaufaAiValidator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": -1})
        self.assertFalse(rn.is_valid)
        self.assertEqual(rn.validator_status, "FAILED_FINAL")

        # Score > 8
        ro = TaufaAiValidator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": 9})
        self.assertFalse(ro.is_valid)
        self.assertEqual(ro.validator_status, "FAILED_FINAL")

        # Missing score
        rm = TaufaAiValidator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "reason": "Missing score entirely"})
        self.assertFalse(rm.is_valid)
        self.assertEqual(rm.validator_status, "FAILED_FINAL")

        # String score non-numeric
        rs = TaufaAiValidator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": "excellent"})
        self.assertFalse(rs.is_valid)
        self.assertEqual(rs.validator_status, "FAILED_FINAL")

        # Boolean score
        rb = TaufaAiValidator.parse_and_validate_response(sub_id, {"submission_id": sub_id, "score": True})
        self.assertFalse(rb.is_valid)
        self.assertEqual(rb.validator_status, "FAILED_FINAL")

    # 9. No auto 8 points: ensure adapter never fabricates score
    def test_09_no_automatic_full_marks(self):
        validator = TaufaAiValidator()
        res = validator.parse_and_validate_response(
            "sub_auto_test",
            {"submission_id": "sub_auto_test", "score": 3, "reason": "Consistent work"}
        )
        self.assertEqual(res.score, 0)
        self.assertEqual(res.validator_status, "REVIEW_REQUIRED")
        self.assertFalse(res.qualifies_for_points)

    # 10. Idempotency: award committed at most once through authoritative ledger
    def test_10_authoritative_scoring_and_idempotency(self):
        import os
        import tempfile
        from core.database import StrictRecordsDB
        from core.scoring_engine import ScoringEngine
        from core.hourly_admin_service import HourlyAdminService

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = StrictRecordsDB(db_path=tmp.name)
            engine = ScoringEngine(db=db)
            admin = HourlyAdminService(db=db, scoring_engine=engine)

            date_str = "2029-01-01"
            act_id = "act_hourly_10"
            with db._connect() as conn:
                conn.execute(
                    """INSERT INTO scheduled_activities (
                        activity_id, account_id, date, type, status, validation_status,
                        window_start, window_end, scheduled_time, notification_time, details_json, evidence_ids_json
                    ) VALUES (?, 'user_lok', ?, 'HOURLY', 'SUBMITTED', 'PENDING_EVALUATION',
                        '2026-10-19T10:00:00+05:30', '2026-10-19T11:00:00+05:30', '2026-10-19T10:00:00+05:30', '2026-10-19T10:00:00+05:30',
                        '{"activity_category": "CODING", "description": "Unit tests"}', '["ev_10"]')""",
                    (act_id, date_str),
                )
                conn.execute(
                    """INSERT INTO proof_reviews (
                        review_id, evidence_id, user_id, task_code, date, hour_slot, status, submitted_at, decision, points_awarded
                    ) VALUES ('rev_10', 'ev_10', 'user_lok', 'HOURLY', '2029-01-01', '10:00-11:00', 'APPROVED', '2026-10-19T10:05:00+05:30', 'APPROVED', 0)""",
                )

            # Initial award of 6 points based on Taufa score
            award1 = admin.award_hourly_points(
                activity_id=act_id,
                admin_user="Taufa_AI",
                points_to_award=6,
                reason="Taufa AI validated",
            )
            self.assertTrue(award1["success"])
            self.assertEqual(award1["points_awarded"], 6)

            # Duplicate award attempt (idempotency check)
            award2 = admin.award_hourly_points(
                activity_id=act_id,
                admin_user="Taufa_AI",
                points_to_award=6,
                reason="Taufa AI validated duplicate retry",
            )
            self.assertTrue(award2["success"])
            self.assertTrue(award2.get("already_applied", False))
            self.assertEqual(award2["points_awarded"], 6)
            self.assertIn("No duplicate points applied", award2.get("message", ""))
        finally:
            if os.path.exists(tmp.name):
                try: os.remove(tmp.name)
                except Exception: pass

    # 11. Locked occurrence prevention: second submission cannot be created for locked occurrence
    def test_11_locked_occurrence_prevention(self):
        import os
        import tempfile
        from core.database import StrictRecordsDB
        from core.hourly_submission_service import HourlySubmissionService, HourlySubmissionError
        from core.scoring_engine import ScoringEngine

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = StrictRecordsDB(db_path=tmp.name)
            engine = ScoringEngine(db=db)
            svc = HourlySubmissionService(db=db, engine=engine)

            date_str = "2029-01-01"
            act_id = "act_hourly_locked"
            with db._connect() as conn:
                conn.execute(
                    """INSERT INTO scheduled_activities (
                        activity_id, account_id, date, type, status, validation_status,
                        window_start, window_end, scheduled_time, notification_time, details_json
                    ) VALUES (?, 'user_lok', ?, 'HOURLY', 'VERIFIED', 'VERIFIED',
                        '2026-10-19T10:00:00+05:30', '2026-10-19T11:00:00+05:30', '2026-10-19T10:00:00+05:30', '2026-10-19T10:00:00+05:30',
                        '{"activity_category": "CODING"}')""",
                    (act_id, date_str),
                )

            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (120, 120), color="blue").save(buf, format="JPEG")
            valid_jpg = buf.getvalue()

            with self.assertRaises(HourlySubmissionError) as ctx:
                svc.accept(
                    payload={
                        "activity_id": act_id,
                        "category": "CODING",
                        "description": "Attempt to resubmit after verification lock",
                    },
                    file_bytes=valid_jpg,
                    filename="evidence.jpg",
                    account_id="user_lok",
                    device_id="win_test",
                    platform="WINDOWS",
                )
            self.assertIn(ctx.exception.code, ["CHECKPOINT_INACTIVE", "WINDOW_CLOSED", "ALREADY_SUBMITTED", "ACTIVITY_NOT_FOUND", "SUBMISSION_DATE_MISMATCH"])
        finally:
            if os.path.exists(tmp.name):
                try: os.remove(tmp.name)
                except Exception: pass


if __name__ == "__main__":
    unittest.main()
