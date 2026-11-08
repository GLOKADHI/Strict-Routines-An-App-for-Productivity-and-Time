"""Disposable SQLite + HTTP contract tests. Provider responses are explicit fixtures,
not evidence that the real semantic service is configured or available.
"""
import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw
from core.database import StrictRecordsDB
from core.hourly_submission_service import HourlySubmissionService, HourlySubmissionError
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler
from core.taufa_ai_validator import TaufaAiValidator, TaufaValidationResult
from core.time_window_engine import TimeWindowEngine, IST
from tests.test_daily_routine_gate import seed_approved_wakeup


def photo(seed=0):
    image = Image.new("RGB", (160, 120), (24 + seed, 70, 110))
    ImageDraw.Draw(image).text((10, 20 + seed), f"Test fixture {seed}", fill="white")
    stream = io.BytesIO(); image.save(stream, "PNG")
    return stream.getvalue()


def genuine_contract(submission_id, **changes):
    return {"submission_id": submission_id, "score": 8, "category_match": True,
        "description_match": True, "evidence_match": True, "evidence_quality": "HIGH",
        "validator_status": "SUCCESS", "reason": "Fixture provider assessment", **changes}


class ContractTests(unittest.TestCase):
    def test_legacy_hourly_alias_uses_the_same_canonical_handler(self):
        calls = []
        class Handler(StrictRecordsRequestHandler):
            def log_message(self, *_): pass
            def _handle_hourly_submission_request(self, raw_body, content_type, **kwargs):
                calls.append((raw_body, content_type))
                self._send_json({"success": True, "canonical": True})
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            for path in ("/api/hourly/submit", "/api/hourly/submit_proof"):
                request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=b"{}", headers={"Content-Type":"application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=10) as response:
                    self.assertTrue(json.load(response)["canonical"])
            self.assertEqual(calls, [(b"{}", "application/json")] * 2)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)

    def test_real_http_adapter_transmits_all_images_and_stable_retry_identity(self):
        requests = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((body, self.headers.get("Idempotency-Key")))
                data = json.dumps(genuine_contract(body["submission_id"], confidence=0.82, model="test-provider")).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            validator = TaufaAiValidator(f"http://127.0.0.1:{server.server_port}/validate", "unit-fixture-key")
            images = [photo(i) for i in range(10)]
            for _ in range(2):
                result = validator.validate_hourly_submission("stable-sub", "STUDY", "Read and annotated operating system scheduling notes.", images)
                self.assertTrue(result.qualifies_for_points)
                self.assertEqual(result.confidence, 0.82)
                self.assertEqual(len(result.evidence_analysis), 10)
                self.assertTrue(all(x["ocr"]["status"] in {"OCR_COMPLETED", "NO_TEXT_DETECTED", "OCR_FAILED", "OCR_UNAVAILABLE"} for x in result.evidence_analysis))
            self.assertEqual(requests[0][1], requests[1][1]); self.assertTrue(requests[0][1])
            self.assertEqual(requests[0][0]["request_id"], requests[1][0]["request_id"])
            self.assertEqual(images, [base64.b64decode(x) for x in requests[0][0]["images"]])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)

    def test_incomplete_and_unknown_results_cannot_approve_or_invent_confidence(self):
        for changes in ({"evidence_match": None}, {"evidence_quality": None},
                        {"validator_status": "SURPRISE"}, {"validator_status": "FAILED_RETRYABLE"}):
            with self.subTest(changes=changes):
                r = TaufaAiValidator.parse_and_validate_response("sub", genuine_contract("sub", **changes))
                self.assertFalse(r.qualifies_for_points); self.assertIsNone(r.confidence)
                self.assertNotEqual("PROVISIONAL_APPROVE", r.as_review("a", "ev")["decision"])

    def test_network_timeout_and_malformed_json_are_processing_failures(self):
        validator = TaufaAiValidator("http://127.0.0.1:8765/validate", "fixture")
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            r = validator.validate_hourly_submission("sub", "STUDY", "Reviewed algorithms and wrote annotated graph traversal notes.", [photo()])
            self.assertEqual(r.validator_status, "FAILED_RETRYABLE")
            self.assertEqual(r.as_review("a", "ev")["decision"], "PROCESSING_ERROR")
            self.assertIsNone(r.score)
        response = MagicMock(); response.__enter__.return_value = response
        response.getcode.return_value = 200; response.read.return_value = b"not JSON"
        with patch("urllib.request.urlopen", return_value=response):
            r = validator.validate_hourly_submission("sub", "STUDY", "Reviewed algorithms and wrote annotated graph traversal notes.", [photo()])
            self.assertEqual(r.validator_status, "FAILED_RETRYABLE"); self.assertIn("MALFORMED_JSON_RESPONSE", r.warnings)

    def test_corrupt_photo_and_invalid_category_never_reach_provider(self):
        validator = TaufaAiValidator("http://127.0.0.1:8765/validate", "fixture")
        with patch("urllib.request.urlopen") as network:
            for category, image in (("BANKING", photo()), ("STUDY", b"not a photo")):
                r = validator.validate_hourly_submission("sub", category, "Read and annotated operating system scheduling notes.", [image])
                self.assertFalse(r.is_valid); self.assertIsNone(r.score)
            network.assert_not_called()


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.date = "2030-06-03"; self.now = datetime(2030, 6, 3, 10, 0, tzinfo=IST)
        self.db = StrictRecordsDB(os.path.join(self.folder.name, "fixture.db"))
        self.engine = ScoringEngine(self.db, storage_root=os.path.join(self.folder.name, "proofs"))
        seed_approved_wakeup(self.db, self.engine, self.date)
        self.clock = patch("core.time_window_engine.now_ist", return_value=self.now); self.clock.start(); self.addCleanup(self.clock.stop)
        activities = TimeWindowEngine(self.db).ensure_activities_in_db(self.date, reference_now=self.now)
        self.activity = next(a for a in activities if a["type"] == "HOURLY" and "-1000-" in a["activity_id"])
        self.service = HourlySubmissionService(self.db, self.engine)
        self.images = [photo(i) for i in range(3)]
        self.payload = {"activity_id": self.activity["activity_id"], "submission_id": "stable-live-sub",
            "category": "STUDY", "description": "Read and annotated operating system scheduling notes.", "image_count": 3,
            "attachments": [{"filename": f"photo{i}.png", "image_base64": base64.b64encode(raw).decode()} for i, raw in enumerate(self.images)]}

    def accept(self, payload=None):
        return self.service.accept(payload or self.payload, file_bytes=self.images[0], filename="photo0.png",
            account_id="user_lok", device_id="test-device", platform="ANDROID", received_at=self.now)

    def score_rows(self):
        with self.db._connect() as conn:
            return [tuple(row) for row in conn.execute("SELECT * FROM score_events ORDER BY event_id")]

    def run_review(self, accepted, result):
        handler = object.__new__(StrictRecordsRequestHandler); handler.db = self.db; handler.engine = self.engine
        class ImmediateThread:
            def __init__(self, target, **_): self.target = target
            def start(self): self.target()
        with patch("core.server.threading.Thread", ImmediateThread), \
             patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=MagicMock()), \
             patch.object(TaufaAiValidator, "validate_hourly_submission", return_value=result) as validation:
            handler._start_hourly_ai_review(accepted, self.images[0])
            return validation

    def test_all_originals_persist_atomically_and_retry_is_idempotent(self):
        first = self.accept(); retry = self.accept()
        self.assertTrue(retry["deduplicated"]); self.assertEqual(first["evidence_ids"], retry["evidence_ids"])
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM evidence_files WHERE related_record_id=?", ("stable-live-sub",)).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual({r["sha256_hash"] for r in rows}, {hashlib.sha256(p).hexdigest() for p in self.images})
            self.assertEqual(conn.execute("SELECT count(*) FROM proof_submission_events WHERE submission_id=?", ("stable-live-sub",)).fetchone()[0], 1)

    def test_retry_with_changed_secondary_photo_is_rejected(self):
        self.accept(); payload = json.loads(json.dumps(self.payload))
        payload["attachments"][1]["image_base64"] = base64.b64encode(photo(9)).decode()
        with self.assertRaises(HourlySubmissionError) as error: self.accept(payload)
        self.assertEqual(error.exception.code, "SUBMISSION_ID_CONFLICT")

    def test_invalid_secondary_photo_leaves_no_partial_acceptance(self):
        payload = json.loads(json.dumps(self.payload)); payload["attachments"][2]["image_base64"] = base64.b64encode(b"corrupt").decode()
        with self.assertRaises(HourlySubmissionError): self.accept(payload)
        with self.db._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM proof_submission_events").fetchone()[0], 0)

    def test_provider_failure_persists_retryable_state_without_score_or_rejection(self):
        accepted = self.accept(); before = self.score_rows()
        result = TaufaValidationResult("stable-live-sub", validator_status="UNCONFIGURED", reason="Configure the existing endpoint.")
        validation = self.run_review(accepted, result)
        validation.assert_called_once(); self.assertEqual(validation.call_args.kwargs["images"], self.images)
        self.assertEqual(before, self.score_rows())
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id=?", (accepted["evidence_id"],)).fetchone()
            self.assertEqual(row["status"], "RETRYABLE_ERROR"); self.assertEqual(row["points_awarded"], 0)
            self.assertEqual(json.loads(row["signals_json"])["taufa_ai"]["validator_status"], "UNCONFIGURED")
            activity = conn.execute("SELECT status FROM scheduled_activities WHERE activity_id=?", (self.activity["activity_id"],)).fetchone()
            self.assertNotEqual(activity["status"], "REJECTED")

    def test_genuine_qualified_result_commits_once_through_score_authority(self):
        accepted = self.accept()
        result = TaufaAiValidator.parse_and_validate_response("stable-live-sub", genuine_contract("stable-live-sub"))
        self.run_review(accepted, result)
        after = self.score_rows(); self.run_review(accepted, result)
        self.assertEqual(after, self.score_rows())
        with self.db._connect() as conn:
            review = conn.execute("SELECT status,points_awarded FROM proof_reviews WHERE evidence_id=?", (accepted["evidence_id"],)).fetchone()
            self.assertEqual(review["status"], "APPROVED"); self.assertEqual(review["points_awarded"], 8)
            awards = conn.execute("SELECT points FROM score_events WHERE evidence_id=? AND points>0", (accepted["evidence_id"],)).fetchall()
            self.assertEqual([r[0] for r in awards], [8])

    def test_demo_leaves_database_and_production_status_identical(self):
        accepted = self.accept()
        with self.db._connect() as conn: before = list(conn.iterdump())
        validator = TaufaAiValidator()
        with patch.object(validator, "is_configured", return_value=False):
            result = validator.run_demo_validation("STUDY", self.payload["description"], [self.images[0]], demo_request_id="repeat-demo")
            retry = validator.run_demo_validation("STUDY", self.payload["description"], [self.images[0]], demo_request_id="repeat-demo")
        self.assertFalse(result["success"]); self.assertIsNone(result["score"])
        self.assertEqual(result["submission_id"], retry["submission_id"])
        # A genuine-shaped positive provider fixture must remain just as isolated.
        # This asserts score isolation, not live semantic model availability.
        qualified = TaufaAiValidator.parse_and_validate_response("demo-fixture", genuine_contract("demo-fixture"))
        with patch.object(validator, "_validate_remote_submission", return_value=qualified):
            positive = validator.run_demo_validation("STUDY", self.payload["description"], [self.images[0]])
        self.assertTrue(positive["success"]); self.assertEqual(positive["score"], 8)
        self.assertEqual(positive["simulated_outcome"]["real_points_awarded"], 0)
        self.assertFalse(positive["simulated_outcome"]["production_record_created"])
        with self.db._connect() as conn: self.assertEqual(before, list(conn.iterdump()))

    def test_interrupted_approval_recovers_from_saved_result_without_new_inference(self):
        accepted = self.accept()
        result = TaufaAiValidator.parse_and_validate_response("stable-live-sub", genuine_contract("stable-live-sub"))
        def interrupted(**kwargs):
            self.db.approve_proof(**{key: value for key,value in kwargs.items() if key in {"evidence_id","admin_id","points","reason"}})
            raise RuntimeError("Fixture interruption before ledger commit")
        with patch.object(self.engine, "apply_proof_approval", side_effect=interrupted):
            self.run_review(accepted, result)
        validation = self.run_review(accepted, TaufaValidationResult("stable-live-sub", validator_status="UNCONFIGURED"))
        validation.assert_not_called()
        with self.db._connect() as conn:
            awards = conn.execute("SELECT points FROM score_events WHERE evidence_id=? AND points>0", (accepted["evidence_id"],)).fetchall()
            self.assertEqual([row[0] for row in awards], [8])
            self.assertEqual(conn.execute("SELECT validation_status FROM scheduled_activities WHERE activity_id=?", (accepted["activity_id"],)).fetchone()[0], "VERIFIED")


if __name__ == "__main__": unittest.main()
