"""
Unit tests for TaufaClient and server Taufa endpoints.
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from core.taufa_client import TaufaClient
from core.taufa_ai_validator import TaufaAiValidator


def _create_png_bytes() -> bytes:
    img = Image.new("RGB", (100, 100), color=(200, 220, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestTaufaClient(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.session_file = os.path.join(self.tmp_dir.name, "taufa_session.json")
        self.client = TaufaClient(session_file=self.session_file)
        self.session_check = patch.object(self.client, "_verify_live_session", return_value=None)
        self.session_check.start()

    def tearDown(self):
        self.session_check.stop()
        self.tmp_dir.cleanup()

    def test_save_and_load_session_string(self):
        cookies_str = "authjs.session-token=mock_jwt_12345; cf_clearance=mock_cf_67890"
        status = self.client.save_session(cookies_str, user_info={"email": "student@strictrecords.org", "name": "Discipline User"})
        self.assertTrue(status["has_cookies"])
        self.assertEqual(status["cookie_count"], 2)
        self.assertEqual(self.client.get_cookie_header(), cookies_str)
        self.assertTrue(self.client.has_active_session())

        # Create another client instance from same file
        client2 = TaufaClient(session_file=self.session_file)
        self.assertTrue(client2.has_active_session())
        self.assertEqual(client2.get_cookie_header(), cookies_str)

    def test_save_session_list(self):
        cookies_list = [
            {"name": "__Secure-authjs.session-token", "value": "secure_token_abc"},
            {"name": "__Host-authjs.csrf-token", "value": "csrf_token_xyz"},
        ]
        status = self.client.save_session(cookies_list)
        self.assertTrue(status["has_cookies"])
        self.assertEqual(status["cookie_count"], 2)
        header = self.client.get_cookie_header()
        self.assertIn("__Secure-authjs.session-token=secure_token_abc", header)
        self.assertIn("__Host-authjs.csrf-token=csrf_token_xyz", header)

    def test_clear_session(self):
        self.client.save_session("authjs.session-token=abc")
        self.assertTrue(self.client.has_active_session())
        self.client.clear_session()
        self.assertFalse(self.client.has_active_session())
        self.assertFalse(os.path.exists(self.session_file))

    def test_clean_ai_stream(self):
        stream_data = '0:"{\\n"\n0:"  \\"score\\": 7,\\n"\n0:"  \\"category_match\\": true\\n"\n0:"}"'
        cleaned = self.client._clean_ai_stream(stream_data)
        self.assertIn('"score": 7', cleaned)
        self.assertIn('"category_match": true', cleaned)

    def test_clean_ai_stream_sse_delta(self):
        sse_data = (
            'data: {"type":"text-delta","id":"txt-0","delta":"{\\"score\\": 8, "}\n'
            'data: {"type":"text-delta","id":"txt-0","delta":"\\"category_match\\": true}"}\n'
            'data: {"type":"finish"}\n'
            'data: [DONE]\n'
        )
        cleaned = self.client._clean_ai_stream(sse_data)
        self.assertEqual(cleaned, '{"score": 8, "category_match": true}')

    def test_ask_score_payload_structure(self):
        self.client.save_session("authjs.session-token=mock_jwt_12345")
        dummy_img = _create_png_bytes()

        captured_req = {}

        class MockResp:
            status = 200
            def read(self, *args):
                return b'{"score": 8, "category_match": true, "description_match": true, "evidence_match": true, "evidence_quality": "HIGH", "reason": "Valid", "warnings": []}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def mock_urlopen(req, *args, **kwargs):
            captured_req["url"] = req.full_url
            captured_req["headers"] = dict(req.headers)
            captured_req["payload"] = json.loads(req.data.decode("utf-8"))
            return MockResp()

        upload = {"id":"fixture-upload","filename":"proof.png","url":"https://taufa.ai/files/fixture.png"}
        with patch.object(self.client,"_upload_image",return_value=upload), patch("core.taufa_client._open_taufa_request", side_effect=mock_urlopen):
            res = self.client.ask_score(
                category="STUDY",
                description="Reviewed algorithmic complexity proofs",
                images=[dummy_img],
                submission_id="sub_test_payload",
            )
            self.assertEqual(res["score"], 8)
            payload = captured_req["payload"]
            self.assertIn("conversationId", payload)
            self.assertIn("id", payload)
            self.assertEqual(payload["conversationId"], payload["id"])
            self.assertEqual(len(payload["messages"]), 1)
            msg = payload["messages"][0]
            self.assertIn("id", msg)
            self.assertEqual(msg["role"], "user")
            self.assertIn("parts", msg)
            parts = msg["parts"]
            self.assertGreaterEqual(len(parts), 2)
            self.assertEqual(parts[0]["type"], "text")
            self.assertEqual(parts[1]["type"], "file")
            self.assertEqual(parts[1]["url"], "https://taufa.ai/files/fixture.png")

    def test_single_conversation_reused_across_calls(self):
        self.client.save_session("authjs.session-token=mock_jwt_12345")
        captured_conv_ids = []

        class MockResp:
            status = 200
            def read(self, *args):
                return b'{"score": 8, "category_match": true, "description_match": true, "evidence_match": true, "evidence_quality": "HIGH", "reason": "Valid", "warnings": []}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def mock_urlopen(req, *args, **kwargs):
            if "/api/conversations" in req.full_url:
                class MockConvResp:
                    status = 200
                    def read(self):
                        return json.dumps({
                            "conversations": [{"id": "single-chat-conv-12345", "title": "StrictRecords Attendance Evaluator"}]
                        }).encode("utf-8")
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return MockConvResp()
            payload = json.loads(req.data.decode("utf-8"))
            captured_conv_ids.append(payload["conversationId"])
            return MockResp()

        upload = {"id":"fixture-upload","filename":"proof.png","url":"https://taufa.ai/files/fixture.png"}
        with patch.object(self.client,"_upload_image",return_value=upload), patch("core.taufa_client._open_taufa_request", side_effect=mock_urlopen):
            res1 = self.client.ask_score(category="STUDY", description="Task 1", images=[_create_png_bytes()])
            res2 = self.client.ask_score(category="STUDY", description="Task 2", images=[_create_png_bytes()])
            res3 = self.client.ask_score(category="CODING", description="Task 3", images=[_create_png_bytes()])

            self.assertEqual(len(captured_conv_ids), 3)
            self.assertEqual(captured_conv_ids[0], "891a3c1e-eed2-460e-bc05-6b8ac94bec5b")
            self.assertEqual(captured_conv_ids[1], "891a3c1e-eed2-460e-bc05-6b8ac94bec5b")
            self.assertEqual(captured_conv_ids[2], "891a3c1e-eed2-460e-bc05-6b8ac94bec5b")
            self.assertEqual(len(set(captured_conv_ids)), 1)

    def test_extract_score_from_json_block(self):
        ai_reply = """Here is my evaluation for your study session:

```json
{
  "score": 7,
  "category_match": true,
  "description_match": true,
  "evidence_match": true,
  "evidence_quality": "HIGH",
  "reason": "Clear notebook evidence showing tree traversal algorithms matching claimed description.",
  "warnings": []
}
```
Good job on your study progress!"""
        result = self.client._extract_score_from_response(ai_reply, submission_id="sub_test_01", category="STUDY")
        self.assertEqual(result["score"], 7)
        self.assertTrue(result["category_match"])
        self.assertTrue(result["description_match"])
        self.assertTrue(result["evidence_match"])
        self.assertEqual(result["evidence_quality"], "HIGH")
        self.assertEqual(result["validator_status"], "SUCCESS")
        self.assertEqual(result["review_status"], "APPROVED")

    def test_freeform_score_cannot_authorize_points(self):
        ai_reply = "I have reviewed your coding proof. Score: 6/8. The implementation matches your description."
        result = self.client._extract_score_from_response(ai_reply, submission_id="sub_test_02", category="CODING")
        self.assertIsNone(result["score"])
        self.assertEqual(result["validator_status"], "FAILED_RETRYABLE")
        self.assertEqual(result["review_status"], "UNREVIEWED")

    def test_extract_zero_score_for_rejection(self):
        ai_reply = """```json
{
  "score": 0,
  "category_match": false,
  "description_match": false,
  "evidence_match": false,
  "evidence_quality": "INSUFFICIENT",
  "reason": "Submitted image is a personal selfie and does not demonstrate studying.",
  "warnings": ["SELFIE_NOT_STUDY_EVIDENCE"]
}
```"""
        result = self.client._extract_score_from_response(ai_reply, submission_id="sub_test_03", category="STUDY")
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["category_match"])
        self.assertEqual(result["validator_status"], "REJECTED")
        self.assertEqual(result["review_status"], "REJECTED")

    def test_taufa_validator_integration_with_client(self):
        validator = TaufaAiValidator()
        # Without session
        with patch.object(TaufaClient, "has_active_session", return_value=False):
            self.assertFalse(validator.is_configured())

        # With mock session in client
        with patch.object(TaufaClient, "has_active_session", return_value=True):
            self.assertTrue(validator.is_configured())

            # Mock ask_score
            mock_score_res = {
                "submission_id": "sub_int_01",
                "score": 8,
                "max_score": 8,
                "category_match": True,
                "description_match": True,
                "evidence_match": True,
                "evidence_quality": "HIGH",
                "validator_status": "SUCCESS",
                "review_status": "APPROVED",
                "reason": "Excellent math proof photos.",
                "warnings": [],
            }
            with patch.object(TaufaClient, "ask_score", return_value=mock_score_res):
                val_res = validator.validate_hourly_submission(
                    submission_id="sub_int_01",
                    category="STUDY",
                    description="Solving differential equations chapter 4 problem set",
                    images=[_create_png_bytes()],
                )
                self.assertTrue(val_res.is_valid)
                self.assertEqual(val_res.score, 8)
                self.assertTrue(val_res.qualifies_for_points)


    def test_taufa_prompt_includes_proof_scoring_rubric(self):
        from core.taufa_client import TAUFA_PROOF_SCORING_RUBRIC, TAUFA_SCORING_PROMPT
        from core.taufa_ai_validator import TAUFA_VALIDATION_SYSTEM_INSTRUCTION

        # Check rubric header and entries 0 to 8
        self.assertIn("Score\tWhat it looks like", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("3\tRight subject, but weak proof: recycled or identical evidence reused from another slot", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("6\tAuthentic and clearly matching, timestamped, with one real gap", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("7\tStrong, specific, timestamped evidence matching category and description closely", TAUFA_PROOF_SCORING_RUBRIC)
        self.assertIn("8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point", TAUFA_PROOF_SCORING_RUBRIC)

        # Check prompt embeds the rubric
        self.assertIn("SCORING SYSTEM FOR PROOF:", TAUFA_SCORING_PROMPT)
        self.assertIn("Score\tWhat it looks like", TAUFA_SCORING_PROMPT)

        # Check system instructions embeds the rubric
        self.assertIn("SCORING SYSTEM FOR PROOF:", TAUFA_VALIDATION_SYSTEM_INSTRUCTION)
        self.assertIn("Score\tWhat it looks like", TAUFA_VALIDATION_SYSTEM_INSTRUCTION)

        # Verify formatted prompt in ask_score contains rubric
        self.client.save_session("authjs.session-token=mock_jwt_12345")
        captured_messages = []
        def mock_urlopen(req, *args, **kwargs):
            payload = json.loads(req.data.decode("utf-8"))
            captured_messages.extend(payload.get("messages", []))
            class Resp:
                status = 200
                def read(self, *a): return b'{"score": 7}'
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return Resp()

        with patch.object(self.client, "_upload_image", return_value={"id": "f1", "url": "http://f1"}), \
             patch("core.taufa_client._open_taufa_request", side_effect=mock_urlopen):
            self.client.ask_score("STUDY", "Calculus limits", [_create_png_bytes()])

        self.assertTrue(len(captured_messages) > 0)
        sent_content = captured_messages[0].get("content", "")
        self.assertIn("SCORING SYSTEM FOR PROOF:", sent_content)
        self.assertIn("Score\tWhat it looks like", sent_content)
        self.assertIn("8\tExemplary: multiple timestamped pages of worked material", sent_content)


if __name__ == "__main__":

    unittest.main()
