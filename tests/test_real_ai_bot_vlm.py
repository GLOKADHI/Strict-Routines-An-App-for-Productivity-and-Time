"""
StrictRecords — Real AI Bot VLM Neural Verification Test Suite
=============================================================
Tests:
  1. RealAIBotEngine initialization and checkpoint loading (realistic_vlm_phase4.pt).
  2. Neural inference on synthetic and image evidence.
  3. StrictBotAIReviewer pipeline integration with VLM neural analysis.
  4. AIProofAnalyzer integration with Real AI Bot visual evaluation.
  5. StrictBot.evaluate_state inclusion of Real AI Bot coaching advice.
  6. Graceful fallback resilience when inputs are missing or irregular.
"""

import base64
import io
import os
import sys
import unittest
from datetime import datetime
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.real_ai_vlm import RealAIBotEngine, REALISTIC_CLASSES
from core.ai_robot_admin import StrictBotAIReviewer
from core.ai_analyzer import AIProofAnalyzer, AIDailyAnalyzer
from core.strictbot import StrictBot


def create_test_image_bytes(color=(70, 130, 180), size=(128, 128)) -> bytes:
    img = Image.new("RGB", size, color=color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 100, 100], fill=(220, 220, 240), outline=(30, 30, 30))
    draw.text((30, 50), "CODE", fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestRealAIBotVLM(unittest.TestCase):

    def setUp(self):
        self.engine = RealAIBotEngine.get_instance()

    def test_01_model_loading_and_status(self):
        status = self.engine.get_model_status()
        self.assertTrue(status["available"], "Real AI Bot VLM should be available")
        self.assertTrue(status["loaded"], "Real AI Bot VLM model should be loaded")
        self.assertIn("realistic_vlm_phase4", status["checkpoint"])
        self.assertEqual(status["architecture"], "cifar-residual-gru-v2")
        self.assertEqual(status["backend"], "PyTorch Local Neural Inference")
        self.assertEqual(len(status["classes"]), 10)
        self.assertFalse(status["is_fallback"])

    def test_02_neural_inference_on_image(self):
        img_bytes = create_test_image_bytes()
        res = self.engine.analyze_evidence(
            image_input=img_bytes,
            task_code="HOURLY",
            description="Working on neural VLM integration tests",
            user_context={"score": 75},
        )
        self.assertTrue(res["is_neural"])
        self.assertIn(res["top_class"], REALISTIC_CLASSES)
        self.assertGreaterEqual(res["neural_confidence"], 0.0)
        self.assertLessEqual(res["neural_confidence"], 1.0)
        self.assertIn("Image classifier label", res["ai_bot_advice"])
        self.assertEqual(len(res["top_predictions"]), 3)
        self.assertTrue(len(res["actionable_next_step"]) > 5)

    def test_03_neural_inference_with_base64_and_pil(self):
        img_bytes = create_test_image_bytes(color=(50, 160, 90))
        b64_str = "data:image/png;base64," + base64.b64encode(img_bytes).decode("ascii")

        # Test base64
        res_b64 = self.engine.analyze_evidence(
            image_input=b64_str,
            task_code="WAKE_UP",
            description="Morning wake-up camera proof",
        )
        self.assertTrue(res_b64["is_neural"])
        self.assertEqual(res_b64["discipline_verdict"], "PENDING_TASK_VALIDATION")
        self.assertIn("does not establish task completion", res_b64["ai_bot_advice"])

        # Test PIL image
        pil_img = Image.new("RGB", (64, 64), color=(180, 50, 50))
        res_pil = self.engine.analyze_evidence(
            image_input=pil_img,
            task_code="GYM",
            description="Evening workout session",
        )
        self.assertTrue(res_pil["is_neural"])
        self.assertEqual(res_pil["discipline_verdict"], "PENDING_TASK_VALIDATION")
        self.assertIn("does not establish task completion", res_pil["ai_bot_advice"])

    def test_04_real_ai_coaching_synthesis(self):
        # Elite day (score 85)
        c_elite = self.engine.generate_real_ai_coaching(score=85, current_streak=5)
        self.assertIn("Elite Performance", c_elite["headline"])
        self.assertTrue(c_elite["is_neural"])

        # Approaching unlock (score 65)
        c_app = self.engine.generate_real_ai_coaching(score=65, current_streak=2)
        self.assertIn("Approaching Unlock", c_app["headline"])
        self.assertIn("15 points away", c_app["coaching_advice"])

        # Morning recovery with snoozes
        c_snooze = self.engine.generate_real_ai_coaching(daily_record={"snooze_count": 2}, score=30)
        self.assertIn("Morning Recovery", c_snooze["headline"])

    def test_05_strictbot_ai_reviewer_integration(self):
        img_bytes = create_test_image_bytes()
        now = datetime.now()
        w_start = (now - datetime.resolution).isoformat()
        w_end = (now + datetime.resolution).isoformat()

        rev = StrictBotAIReviewer.execute_review(
            submission_id="sub_vlm_test_01",
            activity_id="ACT-VLM-HOURLY",
            task_code="HOURLY",
            description="Implemented Real AI Bot VLM neural inference adapter",
            primary_activity="CODING",
            submitted_at_iso=now.isoformat(),
            device_id="win_workstation",
            platform="WINDOWS",
            image_bytes=img_bytes,
            configured_points=8,
        )

        self.assertIn("neural_analysis", rev)
        self.assertIsNotNone(rev["neural_analysis"])
        self.assertTrue(rev["checks"].get("vlm_neural_verified"))
        self.assertIn("ai_bot_advice", rev)
        self.assertIn("Image classifier label", rev["ai_bot_advice"])

    def test_06_ai_proof_analyzer_integration(self):
        img_bytes = create_test_image_bytes()
        b64_str = base64.b64encode(img_bytes).decode("ascii")

        proof_res = AIProofAnalyzer.analyze_proof(
            evidence_id="ev_vlm_proof_01",
            task_code="HOURLY",
            task_title="Deep Work Coding Session",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            device_id="android_phone",
            platform="ANDROID",
            image_base64=b64_str,
        )

        self.assertEqual(proof_res["verdict"], "NEEDS_REVIEW")
        self.assertEqual(proof_res["source"], "LOCAL_DETERMINISTIC_ANALYZER")
        self.assertFalse(proof_res["auto_approved"])
        self.assertEqual(proof_res["suggested_points"], 0)
        self.assertIn("neural_analysis", proof_res)
        self.assertIsNotNone(proof_res["neural_analysis"])
        self.assertIn("Image classifier label", proof_res["neural_analysis"]["ai_bot_advice"])

    def test_07_strictbot_evaluate_state_integration(self):
        daily = {"final_score": 70, "hourly_missed_count": 0, "snooze_count": 0}
        settings = {"reward_threshold": 80, "controlled_threshold": 60}

        st = StrictBot.evaluate_state(daily, settings, current_streak=4)
        self.assertIn("real_ai_coaching", st)
        self.assertIn("real_ai_advice", st)
        self.assertIn("real_ai_model", st)
        self.assertIn("StrictBot Neural VLM", st["real_ai_model"])
        self.assertIn("Approaching Unlock", st["real_ai_headline"])

    def test_08_resilient_fallback_on_corrupt_image(self):
        corrupt_res = self.engine.analyze_evidence(
            image_input=b"corrupt_non_image_bytes_here",
            task_code="HOURLY",
            description="Test fallback",
        )
        self.assertFalse(corrupt_res["is_neural"])
        self.assertEqual(corrupt_res["discipline_verdict"], "PENDING_TASK_VALIDATION")
        self.assertEqual(corrupt_res["neural_confidence"], 0)
        self.assertIn("fallback_reason", corrupt_res)


if __name__ == "__main__":
    unittest.main()
