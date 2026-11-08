"""
Comprehensive test suite for:
Real StrictBot Proof Analysis, Delayed Point Awards, Windows Admin Review,
and Bidirectional Sync between Windows Admin and Android clients.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from PIL import Image

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.hourly_submission_service import HourlySubmissionService


def create_test_image(width=200, height=200, color="green") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestRealStrictBotReviewAndDelayedAwards(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.storage_dir = os.path.join(self.temp_dir, "storage")
        os.makedirs(self.storage_dir, exist_ok=True)
        self.db = StrictRecordsDB(self.db_path)
        self.engine = ScoringEngine(self.db, storage_root=self.storage_dir)
        self.service = HourlySubmissionService(self.db, self.engine, storage_root=self.storage_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_submission_lifecycle_and_delayed_points(self):
        """
        Verify:
        1. Real image submitted -> status = AI_QUEUED.
        2. Scheduled activity points = 0 (points NOT awarded on submit).
        3. Database get_proof_counts reflects AI_QUEUED count.
        4. StrictBot review sets PENDING_ADMIN_REVIEW, points still 0.
        5. Admin approval awards points and emits ADMIN_REVIEW_COMPLETED event.
        6. Subsequent query confirms APPROVED and points awarded.
        """
        date_str = "2026-10-12"
        account_id = "test_user_1"
        activity_id = "act_hourly_10"

        # Setup routine and scheduled activity
        with self.db._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_routine_gates (date, account_id, is_dev_mode, state, activated_at, activation_kind, authorized_by, schedule_version, updated_at)
                   VALUES (?, ?, 0, 'DAILY_ROUTINE_ACTIVE', '2026-10-12T05:00:00+05:30', 'ADMIN_RECOVERY', 'admin', 1, '2026-10-12T05:00:00+05:30')""",
                (date_str, account_id),
            )
            conn.execute(
                """INSERT INTO scheduled_activities (
                    activity_id, account_id, date, schedule_version, title, type,
                    scheduled_time, window_start, window_end, notification_time, status, points
                ) VALUES (?, ?, ?, 1, 'Deep Work', 'HOURLY', '2026-10-12T10:00:00+05:30',
                          '2026-10-12T09:45:00+05:30', '2026-10-12T10:15:00+05:30', '2026-10-12T09:40:00+05:30', 'OPEN', 0)""",
                (activity_id, account_id, date_str),
            )

        # 1. Submit real image
        image_bytes = create_test_image(250, 250, "blue")
        submission_payload = {
            "activity_id": activity_id,
            "category": "CODING",
            "primary_activity": "CODING",
            "description": "Implemented database transactions and proof audit trail.",
            "is_dev_mode": False,
        }

        result = self.service.accept(
            submission_payload,
            file_bytes=image_bytes,
            filename="coding_work.jpg",
            account_id=account_id,
            device_id="android_device_tb4",
            platform="ANDROID",
            received_at=datetime.fromisoformat("2026-10-12T10:05:00+05:30"),
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["accepted"])
        evidence_id = result["evidence_id"]
        submission_id = result["submission_id"]

        # 2. Verify points in scheduled_activities are 0 (DELAYED POINT AWARDS)
        with self.db._connect() as conn:
            act = conn.execute(
                "SELECT status, points, validation_status FROM scheduled_activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
            self.assertEqual(act["status"], "VALIDATING")
            self.assertEqual(act["points"], 0, "Points must NOT be awarded upon submission!")
            self.assertEqual(act["validation_status"], "PROCESSING")

        # 3. Check proof review record status is AI_QUEUED
        proof = self.db.get_proof_details(evidence_id)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["status"], "AI_QUEUED")

        # 4. Check get_proof_counts
        counts = self.db.get_proof_counts()
        self.assertGreaterEqual(counts.get("ai_analyzing", 0), 1)

        # 5. StrictBot saves AI review -> PENDING_ADMIN_REVIEW with 0 awarded points
        ai_review_data = {
            "evidence_id": evidence_id,
            "task_code": "CODING",
            "decision": "NEEDS_REVIEW",
            "ai_score": 88,
            "ai_confidence": 0.92,
            "ai_recommendation": "APPROVE",
            "ai_reasoning": "Observed high quality coding evidence matching description.",
            "ocr_text": "class Database:",
            "points_awarded": 0,
        }
        self.db.save_strictbot_ai_review(ai_review_data)

        updated_proof = self.db.get_proof_details(evidence_id)
        self.assertEqual(updated_proof["status"], "PENDING_ADMIN_REVIEW")
        self.assertEqual(updated_proof["points_awarded"], 0)

        counts2 = self.db.get_proof_counts()
        self.assertGreaterEqual(counts2.get("pending", 0), 1)

        # 6. Admin approves proof with +5 points
        admin_action = "APPROVE"
        admin_points = 5
        admin_notes = "Verified authentic coding screenshot."

        # Update proof review
        with self.db._connect() as conn:
            conn.execute(
                """UPDATE proof_reviews
                   SET status = 'APPROVED', decision = 'APPROVED', points_awarded = ?,
                       reviewed_at = '2026-10-12T10:10:00+05:30', reviewed_by = 'windows_admin',
                       reason_note = ?
                   WHERE evidence_id = ?""",
                (admin_points, admin_notes, evidence_id),
            )
            conn.execute(
                """UPDATE scheduled_activities
                   SET status = 'VERIFIED', validation_status = 'VERIFIED', points = ?
                   WHERE activity_id = ?""",
                (admin_points, activity_id),
            )

        # Publish durable event for Android
        self.service.publish_review_event(
            account_id=account_id,
            activity_id=activity_id,
            evidence_id=evidence_id,
            action=admin_action,
            status="APPROVED",
            points=admin_points,
            admin_id="windows_admin",
            reason_note=admin_notes,
        )

        # 7. Verify updated state and event published
        final_proof = self.db.get_proof_details(evidence_id)
        self.assertEqual(final_proof["status"], "APPROVED")
        self.assertEqual(final_proof["points_awarded"], 5)

        with self.db._connect() as conn:
            event = conn.execute(
                "SELECT * FROM proof_submission_events WHERE activity_id = ? AND status = 'APPROVED'",
                (activity_id,),
            ).fetchone()
            self.assertIsNotNone(event)
            payload = json.loads(event["payload_json"])
            self.assertEqual(payload["event_type"], "ADMIN_REVIEW_COMPLETED")
            self.assertEqual(payload["status"], "APPROVED")
            self.assertEqual(payload["points"], 5)

        # 8. Verify delivery to Android device
        pending_events = self.service.get_pending_events(account_id, "android_device_tb4")
        self.assertGreaterEqual(len(pending_events), 1)
        delivered_evt = pending_events[0]
        self.assertEqual(delivered_evt["event_type"], "ADMIN_REVIEW_COMPLETED")
        self.assertEqual(delivered_evt["points"], 5)

    def test_proof_filtering_and_rejection_flow(self):
        """
        Verify:
        1. Filtering by status category (pending, ai_analyzing, approved, rejected, all).
        2. Proof rejection flow sets REJECTED status, points 0, and records reason.
        3. Secure evidence resolution by evidence_id.
        """
        date_str = "2026-10-12"
        account_id = "test_user_2"
        act_id = "act_hourly_11"

        with self.db._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_routine_gates (date, account_id, is_dev_mode, state, activated_at, activation_kind, authorized_by, schedule_version, updated_at)
                   VALUES (?, ?, 0, 'DAILY_ROUTINE_ACTIVE', '2026-10-12T05:00:00+05:30', 'ADMIN_RECOVERY', 'admin', 1, '2026-10-12T05:00:00+05:30')""",
                (date_str, account_id),
            )
            conn.execute(
                """INSERT INTO scheduled_activities (
                    activity_id, account_id, date, schedule_version, title, type,
                    scheduled_time, window_start, window_end, notification_time, status, points
                ) VALUES (?, ?, ?, 1, 'Reading', 'HOURLY', '2026-10-12T11:00:00+05:30',
                          '2026-10-12T10:45:00+05:30', '2026-10-12T11:15:00+05:30', '2026-10-12T10:40:00+05:30', 'OPEN', 0)""",
                (act_id, account_id, date_str),
            )

        # Submit proof
        img = create_test_image(300, 300, "red")
        res = self.service.accept(
            {"activity_id": act_id, "category": "READING", "description": "Reading chapter 4."},
            file_bytes=img,
            filename="reading.jpg",
            account_id=account_id,
            device_id="android_device_tb4",
            platform="ANDROID",
            received_at=datetime.fromisoformat("2026-10-12T11:05:00+05:30"),
        )
        ev_id = res["evidence_id"]

        # Initially queued for AI
        queued_proofs = self.db.get_proofs(status="ai_analyzing")
        self.assertTrue(any(p["evidence_id"] == ev_id for p in queued_proofs))

        # Transition to PENDING_ADMIN_REVIEW
        self.db.save_strictbot_ai_review({
            "evidence_id": ev_id,
            "task_code": "READING",
            "decision": "NEEDS_REVIEW",
            "ai_score": 45,
            "ai_recommendation": "REJECT",
            "ai_reasoning": "Unclear text, requires admin inspection.",
        })

        pending_proofs = self.db.get_proofs(status="pending")
        self.assertTrue(any(p["evidence_id"] == ev_id for p in pending_proofs))

        # Admin rejects
        with self.db._connect() as conn:
            conn.execute(
                """UPDATE proof_reviews
                   SET status = 'REJECTED', decision = 'REJECTED', points_awarded = 0,
                       reviewed_at = '2026-10-12T11:10:00+05:30', reviewed_by = 'windows_admin',
                       reason_note = 'Photo is unreadable'
                   WHERE evidence_id = ?""",
                (ev_id,),
            )

        rejected_proofs = self.db.get_proofs(status="rejected")
        self.assertTrue(any(p["evidence_id"] == ev_id for p in rejected_proofs))

        proof_details = self.db.get_proof_details(ev_id)
        self.assertEqual(proof_details["status"], "REJECTED")
        self.assertEqual(proof_details["points_awarded"], 0)
        self.assertEqual(proof_details["reason_note"], "Photo is unreadable")

        # Check file exists and can be resolved
        with self.db._connect() as conn:
            row = conn.execute("SELECT file_path FROM evidence_files WHERE evidence_id = ?", (ev_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(os.path.isfile(row["file_path"]))


class TestAdminApiAndEvidenceView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        import urllib.request
        from core.server import create_server

        cls.temp_dir = tempfile.mkdtemp(prefix="strict_api_test_")
        cls.db_path = os.path.join(cls.temp_dir, "api_test.db")
        cls.storage_root = os.path.join(cls.temp_dir, "STRICTRECORDS")
        os.makedirs(cls.storage_root, exist_ok=True)
        cls.server = create_server(
            host="127.0.0.1", port=0, db_path=cls.db_path, storage_root=cls.storage_root
        )
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.db = StrictRecordsDB(cls.db_path)
        cls.engine = ScoringEngine(cls.db, storage_root=cls.storage_root)
        cls.hourly_service = HourlySubmissionService(cls.db, cls.engine, storage_root=cls.storage_root)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _get(self, path: str, headers: dict = None):
        import urllib.request
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                try:
                    return resp.status, json.loads(data.decode("utf-8")), resp.headers
                except Exception:
                    return resp.status, data, resp.headers
        except urllib.error.HTTPError as exc:
            data = exc.read()
            try:
                return exc.code, json.loads(data.decode("utf-8")), exc.headers
            except Exception:
                return exc.code, data, exc.headers

    def _post(self, path: str, payload: dict, headers: dict = None):
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                try:
                    return resp.status, json.loads(data.decode("utf-8"))
                except Exception:
                    return resp.status, data
        except urllib.error.HTTPError as exc:
            data = exc.read()
            try:
                return exc.code, json.loads(data.decode("utf-8"))
            except Exception:
                return exc.code, data

    def test_admin_api_endpoints_and_evidence_view(self):
        # 1. Unauthenticated request to /api/admin/proof/review is rejected
        status, body = self._post("/api/admin/proof/review", {"action": "APPROVE"})
        self.assertEqual(status, 401)
        self.assertEqual(body.get("code"), "UNAUTHORIZED")

        # 2. Configure non-default admin PIN and create valid session
        res = self.db.change_admin_pin("1234", "5678", "admin")
        self.assertTrue(res.get("success"), res.get("error"))
        auth = self.db.authenticate_admin("admin", "5678")
        session_token = auth["token"]
        admin_headers = {"X-Admin-Session": session_token}

        # 3. Setup activity and submit real proof via HourlySubmissionService
        date_str = "2026-10-12"
        account_id = "test_user_api"
        act_id = "act_hourly_api_1"

        with self.db._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_routine_gates (date, account_id, is_dev_mode, state, activated_at, activation_kind, authorized_by, schedule_version, updated_at)
                   VALUES (?, ?, 0, 'DAILY_ROUTINE_ACTIVE', '2026-10-12T05:00:00+05:30', 'ADMIN_RECOVERY', 'admin', 1, '2026-10-12T05:00:00+05:30')""",
                (date_str, account_id),
            )
            conn.execute(
                """INSERT INTO scheduled_activities (
                    activity_id, account_id, date, schedule_version, title, type,
                    scheduled_time, window_start, window_end, notification_time, status, points
                ) VALUES (?, ?, ?, 1, 'Coding Session', 'HOURLY', '2026-10-12T12:00:00+05:30',
                          '2026-10-12T11:45:00+05:30', '2026-10-12T12:15:00+05:30', '2026-10-12T11:40:00+05:30', 'OPEN', 0)""",
                (act_id, account_id, date_str),
            )

        img_bytes = create_test_image(200, 200, "purple")
        accept_res = self.hourly_service.accept(
            {"activity_id": act_id, "category": "CODING", "description": "Writing API tests."},
            file_bytes=img_bytes,
            filename="api_test.jpg",
            account_id=account_id,
            device_id="android_test_dev",
            platform="ANDROID",
            received_at=datetime.fromisoformat("2026-10-12T12:05:00+05:30"),
        )
        ev_id = accept_res["evidence_id"]

        # 4. Fetch /api/admin/proofs with admin session
        status, proofs_resp, _ = self._get("/api/admin/proofs?status=all", headers=admin_headers)
        self.assertEqual(status, 200)
        self.assertTrue(proofs_resp.get("success"))
        self.assertIn("counts", proofs_resp)
        self.assertGreaterEqual(proofs_resp["counts"]["ai_analyzing"], 1)

        # 5. Fetch evidence image via /api/evidence/view?evidence_id=...
        status, img_data, hdrs = self._get(f"/api/evidence/view?evidence_id={ev_id}")
        self.assertEqual(status, 200)
        self.assertIn("image/jpeg", hdrs.get("Content-Type", ""))
        self.assertEqual(len(img_data), len(img_bytes))

        # 6. Verify directory traversal blocked
        status, err_data, _ = self._get("/api/evidence/view?file_path=../../../../windows/system32/cmd.exe")
        self.assertIn(status, (400, 403, 404))

        # 7. Approve proof via /api/admin/proof/review
        review_payload = {
            "evidence_id": ev_id,
            "action": "APPROVE",
            "proposed_points": 5,
            "reason_note": "API test approval verified",
        }
        status, review_resp = self._post("/api/admin/proof/review", review_payload, headers=admin_headers)
        self.assertEqual(status, 200)
        self.assertTrue(review_resp.get("success"), str(review_resp))
        self.assertEqual(review_resp.get("status"), "APPROVED")
        self.assertEqual(review_resp.get("points_awarded"), 5)

        # 8. Check scheduled_activities reflects points
        with self.db._connect() as conn:
            act_row = conn.execute("SELECT status, points FROM scheduled_activities WHERE activity_id = ?", (act_id,)).fetchone()
            self.assertEqual(act_row["status"], "VERIFIED")
            self.assertEqual(act_row["points"], 5)


if __name__ == "__main__":
    unittest.main()
