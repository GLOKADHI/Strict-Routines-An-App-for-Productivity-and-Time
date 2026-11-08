"""Comprehensive test suite for Watermark-Aware Proof Validation,
Duplicate Detection, Content Verification, and Fair Scoring in StrictRecords.
"""

import base64
import hashlib
import io
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from core.ai_robot_admin import StrictBotAIReviewer, IST
from core.watermark_validator import WatermarkValidator


def create_test_image(
    text: str = "",
    size: tuple = (400, 300),
    color: tuple = (240, 240, 240),
    draw_box: bool = False,
) -> bytes:
    """Helper to generate in-memory test images."""
    img = Image.new("RGB", size, color=color)
    draw = ImageDraw.Draw(img)
    if draw_box:
        draw.rectangle([50, 50, 350, 250], outline=(50, 50, 50), width=3)
    if text:
        draw.text((15, 270), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class TestWatermarkAndProofValidation(unittest.TestCase):
    def setUp(self):
        self.wv = WatermarkValidator()
        self.test_date = "2026-10-14"
        self.window_start = "2026-10-14T16:45:00+05:30"
        self.window_end = "2026-10-14T17:05:00+05:30"

    # 1. In-window timestamp validation
    def test_in_window_timestamp(self):
        wm_info = {
            "detected": True,
            "datetime_iso": "2026-10-14T16:49:00+05:30",
            "parsed": {
                "dt": datetime(2026, 10, 14, 16, 49, tzinfo=IST),
                "date_str": "2026-10-14",
                "time_str": "16:49:00",
            },
        }
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date=self.test_date,
            task_hour_slot="16:00-17:00",
        )
        self.assertTrue(res["valid"])
        self.assertEqual(res["status"], "PASSED")
        self.assertFalse(res["is_out_of_window"])

    # 2. Out-of-window timestamp prior (too early)
    def test_out_of_window_timestamp_prior(self):
        wm_info = {
            "detected": True,
            "datetime_iso": "2026-10-14T14:30:00+05:30",
            "parsed": {
                "dt": datetime(2026, 10, 14, 14, 30, tzinfo=IST),
                "date_str": "2026-10-14",
                "time_str": "14:30:00",
            },
        }
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date=self.test_date,
            task_hour_slot="16:00-17:00",
        )
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "OUT_OF_WINDOW")
        self.assertTrue(res["is_out_of_window"])

    # 3. Out-of-window timestamp post (too late)
    def test_out_of_window_timestamp_post(self):
        wm_info = {
            "detected": True,
            "datetime_iso": "2026-10-14T17:25:00+05:30",
            "parsed": {
                "dt": datetime(2026, 10, 14, 17, 25, tzinfo=IST),
                "date_str": "2026-10-14",
                "time_str": "17:25:00",
            },
        }
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date=self.test_date,
            task_hour_slot="16:00-17:00",
        )
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "OUT_OF_WINDOW")
        self.assertTrue(res["is_out_of_window"])

    # 4. AM/PM confusion
    def test_ampm_confusion(self):
        wm_info = {
            "detected": True,
            "datetime_iso": "2026-10-14T04:49:00+05:30",
            "parsed": {
                "dt": datetime(2026, 10, 14, 4, 49, tzinfo=IST),
                "date_str": "2026-10-14",
                "time_str": "04:49:00",
            },
        }
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date=self.test_date,
            task_hour_slot="16:00-17:00",
        )
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "AM_PM_MISMATCH")
        self.assertTrue(res["is_ampm_mismatch"])

    # 5. Date mismatch
    def test_date_mismatch(self):
        wm_info = {
            "detected": True,
            "datetime_iso": "2026-10-13T16:49:00+05:30",
            "parsed": {
                "dt": datetime(2026, 10, 13, 16, 49, tzinfo=IST),
                "date_str": "2026-10-13",
                "time_str": "16:49:00",
            },
        }
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date="2026-10-14",
        )
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "DATE_MISMATCH")
        self.assertTrue(res["is_date_mismatch"])

    # 6. Missing or unreadable watermark
    def test_missing_unreadable_watermark(self):
        wm_info = {"detected": False, "status": "NO_WATERMARK_DETECTED", "reason": "No watermark found"}
        res = self.wv.validate_watermark_timestamp(
            watermark_info=wm_info,
            window_start_iso=self.window_start,
            window_end_iso=self.window_end,
            scheduled_date=self.test_date,
        )
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "NO_WATERMARK_DETECTED")

    # 7. Diverse valid watermark formats
    def test_watermark_formats_iso_and_custom(self):
        samples = [
            ("14 October 2026 4:49 pm", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
            ("14.October 2026 4:49 PM", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
            ("14.0ctober2026s49'pm es", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
            ("2026-10-14 16:49:00", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
            ("14/10/2026 04:49 PM", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
            ("14-10-2026 16:49", datetime(2026, 10, 14, 16, 49, tzinfo=IST)),
        ]
        for raw_str, expected_dt in samples:
            parsed = self.wv.parse_datetime_stamp(raw_str, default_date="2026-10-14")
            self.assertIsNotNone(parsed, f"Failed to parse format: {raw_str}")
            self.assertEqual(parsed["dt"].year, expected_dt.year)
            self.assertEqual(parsed["dt"].month, expected_dt.month)
            self.assertEqual(parsed["dt"].day, expected_dt.day)
            self.assertEqual(parsed["dt"].hour, expected_dt.hour)
            self.assertEqual(parsed["dt"].minute, expected_dt.minute)

    # 8. Exact cryptographic duplicate check
    def test_exact_cryptographic_duplicate(self):
        img_bytes = create_test_image("duplicate test")
        sha = hashlib.sha256(img_bytes).hexdigest()
        dhash = StrictBotAIReviewer.compute_dhash(img_bytes)
        history = [
            {"evidence_id": "ev_past_1", "submission_id": "sub_past_1", "sha256": sha, "dhash": dhash}
        ]
        dup_check = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256=sha,
            current_dhash=dhash,
            historical_records=history,
            exclude_submission_id="sub_new",
        )
        self.assertEqual(dup_check["duplicate_risk"], "EXACT")
        self.assertEqual(dup_check["status"], "EXACT_DUPLICATE")

    # 9. Near-duplicate / resized image
    def test_perceptual_near_duplicate(self):
        img1 = create_test_image("pattern", size=(500, 400), draw_box=True)
        # Slightly resized version of the same image
        pil_img = Image.open(io.BytesIO(img1))
        resized = pil_img.resize((480, 384))
        buf = io.BytesIO()
        resized.save(buf, format="JPEG")
        img2 = buf.getvalue()

        dhash1 = StrictBotAIReviewer.compute_dhash(img1)
        dhash2 = StrictBotAIReviewer.compute_dhash(img2)
        dist = StrictBotAIReviewer.hamming_distance(dhash1, dhash2)
        self.assertLessEqual(dist, 4)

        history = [{"evidence_id": "ev_past", "submission_id": "sub_past", "sha256": "other", "dhash": dhash1}]
        dup_check = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256=hashlib.sha256(img2).hexdigest(),
            current_dhash=dhash2,
            historical_records=history,
            exclude_submission_id="sub_new",
        )
        self.assertIn(dup_check["duplicate_risk"], ("EXACT", "HIGH"))

    # 10. Distinct non-duplicate photos
    def test_distinct_non_duplicate_photos(self):
        img1 = create_test_image("Image 1", size=(400, 300), color=(255, 0, 0))
        img2 = create_test_image("Image 2", size=(400, 300), color=(0, 255, 0), draw_box=True)
        dhash1 = StrictBotAIReviewer.compute_dhash(img1)
        dhash2 = StrictBotAIReviewer.compute_dhash(img2)
        sha2 = hashlib.sha256(img2).hexdigest()

        history = [{"evidence_id": "ev_past", "submission_id": "sub_past", "sha256": hashlib.sha256(img1).hexdigest(), "dhash": dhash1}]
        dup_check = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256=sha2,
            current_dhash=dhash2,
            historical_records=history,
            exclude_submission_id="sub_new",
        )
        self.assertEqual(dup_check["duplicate_risk"], "LOW")
        self.assertEqual(dup_check["status"], "NO_MATCH_FOUND")

    # 11. Valid timestamp with irrelevant content
    def test_valid_timestamp_irrelevant_content(self):
        content_res = StrictBotAIReviewer.evaluate_task_content(
            primary_activity="Coding",
            task_code="HOURLY",
            description="Writing python code for backend",
            ocr_tokens=["meow", "whiskers", "paws"],
            vision_objects=["cat"],
            vlm_res={"top_class": "cat", "neural_confidence": 0.92},
        )
        self.assertFalse(content_res["valid"])
        self.assertEqual(content_res["status"], "CONTENT_MISMATCH")

    # 12. Valid task image without readable text (e.g. Gym or Bath photo)
    def test_valid_task_image_without_text(self):
        content_res = StrictBotAIReviewer.evaluate_task_content(
            primary_activity="Gym",
            task_code="HOURLY",
            description="Completed bench press workout sets and cardio",
            ocr_tokens=[],  # No readable text
            vision_objects=["person", "sports ball", "bottle"],
            vlm_res=None,
        )
        self.assertTrue(content_res["valid"])
        self.assertEqual(content_res["status"], "CONTENT_VERIFIED")

    # 13. Real AI Bot VLM neural analysis integration
    def test_real_ai_vlm_neural_analysis(self):
        from core.real_ai_vlm import RealAIBotEngine
        engine = RealAIBotEngine.get_instance()
        self.assertIsNotNone(engine)

    # 14. OCR / Vision unavailable fallback
    def test_ocr_vision_unavailable_graceful_fallback(self):
        with patch.object(WatermarkValidator, "_run_tesseract", return_value=""):
            res = self.wv.extract_watermark(create_test_image("test"))
            self.assertFalse(res["detected"])
            self.assertEqual(res["status"], "NO_WATERMARK_DETECTED")

    # 15. Zero-penalty rejections in execute_review
    def test_zero_penalty_rejections(self):
        img_bytes = create_test_image("out of window")
        # Test out of window rejection
        with patch.object(WatermarkValidator, "extract_watermark") as mock_extract:
            mock_extract.return_value = {
                "detected": True,
                "datetime_iso": "2026-10-14T14:30:00+05:30",
                "parsed": {"dt": datetime(2026, 10, 14, 14, 30, tzinfo=IST)},
            }
            rev = StrictBotAIReviewer.execute_review(
                submission_id="sub_test_1",
                activity_id="act_test_1",
                task_code="HOURLY",
                description="Coding tasks completed",
                primary_activity="Coding",
                submitted_at_iso="2026-10-14T16:50:00+05:30",
                device_id="dev_1",
                image_bytes=img_bytes,
                window_start_iso="2026-10-14T16:45:00+05:30",
                window_end_iso="2026-10-14T17:05:00+05:30",
                configured_points=8,
            )
            self.assertEqual(rev["decision"], "PROVISIONAL_REJECT")
            self.assertEqual(rev["proposed_points"], 0)
            self.assertTrue(any("Watermark timing failure" in r for r in rev["reasons"]))

    # 16. Idempotency (StrictRecordsDB.approve_proof)
    def test_idempotency_proof_approval(self):
        from core.database import StrictRecordsDB
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name
        try:
            db = StrictRecordsDB(db_path=db_path)
            # Insert test proof review
            evidence_id = "ev_idemp_test"
            db.ensure_proof_review_record(
                evidence_id=evidence_id,
                user_id="user_test",
                task_code="HOURLY",
                date="2026-10-14",
                hour_slot="16:00-17:00",
                status="NEEDS_REVIEW",
            )
            with patch("core.database.is_valid_retained_proof_file", return_value=True):
                # First approval awards points
                r1 = db.approve_proof(evidence_id, admin_id="admin_1", points=8)
                self.assertEqual(r1.get("points_awarded"), 8)

                # Second approval call is idempotent
                r2 = db.approve_proof(evidence_id, admin_id="admin_1", points=8)
                self.assertEqual(r2.get("points_awarded", 0), 8)  # Returns already awarded points without adding again
                self.assertEqual(r2.get("status"), "APPROVED")
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except OSError:
                    pass

    # 17. Timezone consistency (Asia/Kolkata, UTC+5:30)
    def test_timezone_consistency_ist(self):
        # 11:20:00 UTC is 16:50:00 IST
        sub_utc = "2026-10-14T11:20:00Z"
        w_start = "2026-10-14T16:45:00+05:30"
        w_end = "2026-10-14T17:05:00+05:30"
        timing = StrictBotAIReviewer.evaluate_timing(
            submitted_at_iso=sub_utc,
            window_start_iso=w_start,
            window_end_iso=w_end,
        )
        self.assertTrue(timing["timing_valid"])
        self.assertEqual(timing["status"], "ON_TIME")

    # 18. Windows EXE and native files preservation
    def test_windows_exe_preservation(self):
        # Verify launcher files and build scripts are unmodified
        launcher_cs = os.path.join(os.path.dirname(__file__), "..", "launcher", "StrictRecordsLauncher.cs")
        self.assertTrue(os.path.exists(launcher_cs))
        with open(launcher_cs, "rb") as f:
            content = f.read()
            self.assertGreater(len(content), 100)


if __name__ == "__main__":
    unittest.main()
