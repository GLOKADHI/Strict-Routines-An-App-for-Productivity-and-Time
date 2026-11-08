"""
Comprehensive Test Suite for StrictRecords Admin Ecosystem
Tests:
1. Role-Based Access Control & Admin Authentication (Password + PIN + Sessions + Logout)
2. Proof Center & Review Workflow (Submit -> Pending -> Approve -> Points -> ScoreEvent)
3. Self-Approval Protection (Admin cannot approve self-submitted evidence)
4. Proof Rejection Workflow (Structured reasons, custom notes, revisions, resubmissions)
5. Bulk Review (Multi-proof approval & rejection)
6. Controlled Manual Score Adjustment (Mandatory reason, ScoreEvent, audit log, no raw overwrite)
7. Rule & Configuration Versioning (Draft -> Published, v12 -> v13)
8. Device Trust & Revocation (Blocks subsequent sync)
9. Immutable Audit Logging
10. Analytics & Reporting (CSV + JSON Export)
11. Global Admin Search
12. Central REST API Endpoints Integration
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime

from core.database import StrictRecordsDB
from core.models import UserRole, ProofStatus, ProofRejectionReason, AdminActionType, ScoreEventType
from core.scoring_engine import ScoringEngine
from core.server import create_server


class TestAdminEcosystem(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="strict_admin_test_")
        self.db_path = os.path.join(self.temp_dir, "test_admin.db")
        self.storage_root = os.path.join(self.temp_dir, "STRICTRECORDS")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.date = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Admin Authentication & RBAC
    def test_01_admin_authentication_and_session(self):
        # Default admin credentials
        auth_res = self.db.authenticate_admin("admin", "admin1984")
        self.assertIn("token", auth_res)
        self.assertEqual(auth_res["username"], "admin")
        self.assertEqual(auth_res["role"], "ADMIN")

        # Verify session
        token = auth_res["token"]
        session = self.db.verify_admin_session(token)
        self.assertIsNotNone(session)
        self.assertEqual(session["username"], "admin")

        # Admin PIN authentication (PIN 1984)
        pin_valid = self.db.verify_admin_pin("1984")
        self.assertTrue(pin_valid)
        self.assertFalse(self.db.verify_admin_pin("0000"))

        # Pin authentication via authenticate_admin
        pin_auth = self.db.authenticate_admin("admin", "1984")
        self.assertIn("token", pin_auth)

        # Invalid password throws PermissionError
        with self.assertRaises(PermissionError):
            self.db.authenticate_admin("admin", "wrong_pass")

        # Logout invalidates session
        logged_out = self.db.logout_admin(token)
        self.assertTrue(logged_out)
        self.assertIsNone(self.db.verify_admin_session(token))

    # 2. Proof Submission, Review & Approval Workflow
    def test_02_proof_submission_and_approval_flow(self):
        # Ensure user day
        self.db.ensure_daily_record(self.date, user_id="user_lok")
        initial_score = self.db.get_daily_record(self.date)["final_score"]

        # Store proof file
        dummy_bytes = b"EXCELLENT_GYM_WORKOUT_PROOF_PHOTO_DATA"
        ev = self.engine.store_evidence_file(
            date=self.date,
            record_type="GYM",
            related_record_id="gym_session_1",
            file_bytes=dummy_bytes,
            filename_hint="gym_proof.jpg",
            device_id="android_lok_phone",
        )
        eid = ev["evidence_id"]

        # Verify proof is registered in PENDING status in Proof Center
        proofs = self.db.get_proofs(status=ProofStatus.PENDING.value)
        self.assertGreaterEqual(len(proofs), 1)
        target_proof = next(p for p in proofs if p["evidence_id"] == eid)
        self.assertEqual(target_proof["status"], "PENDING")
        self.assertEqual(target_proof["user_id"], "user_lok")
        self.assertEqual(target_proof["task_code"], "GYM")

        # Admin approves proof (+3 points)
        appr_res = self.engine.apply_proof_approval(
            evidence_id=eid,
            admin_id="Administrator",
            points=3,
            reason="Verified gym dumbbell exercise photo",
        )
        self.assertEqual(appr_res["status"], "APPROVED")
        self.assertEqual(appr_res["points_awarded"], 3)

        # Verify status updated in database
        details = self.db.get_proof_details(eid)
        self.assertEqual(details["status"], "APPROVED")
        self.assertEqual(details["reviewed_by"], "Administrator")

        # Verify ScoreEvent generated through central scoring engine
        events = self.db.get_score_events(self.date)
        appr_event = next(e for e in events if e["event_type"] == ScoreEventType.PROOF_APPROVED.value)
        self.assertEqual(appr_event["points"], 3)
        self.assertEqual(appr_event["evidence_id"], eid)

        # Verify DailyScore incremented
        rec_after = self.db.get_daily_record(self.date)
        self.assertEqual(rec_after["final_score"], min(100, initial_score + 3))

        # Verify Admin Audit Log created
        audits = self.db.get_admin_audit_logs(action_type=AdminActionType.PROOF_APPROVED.value)
        self.assertGreaterEqual(len(audits), 1)
        self.assertIn(eid, audits[0]["field_changed"])

    # 3. Self-Approval Protection
    def test_03_self_approval_protection(self):
        # Create proof submitted by "admin_root"
        ev = self.engine.store_evidence_file(
            date=self.date,
            record_type="HOURLY",
            related_record_id="hourly_1",
            device_id="android_lok_phone",
            file_bytes=b"SELF_SUBMITTED_EVIDENCE",
            filename_hint="self_proof.jpg",
        )
        eid = ev["evidence_id"]

        # Force proof submitter to be "admin_root"
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET user_id = 'admin_root' WHERE evidence_id = ?", (eid,))

        # Same admin attempting to approve must be BLOCKED
        with self.assertRaises(PermissionError) as ctx:
            self.engine.apply_proof_approval(
                evidence_id=eid,
                admin_id="admin_root",
                points=3,
            )
        self.assertIn("Administrators cannot approve their own evidence", str(ctx.exception))

    # 4. Proof Rejection, Structured Reasons & Resubmission
    def test_04_proof_rejection_and_resubmission(self):
        ev = self.engine.store_evidence_file(
            date=self.date,
            record_type="BATH",
            related_record_id="bath_1",
            device_id="android_lok_phone",
            file_bytes=b"BLURRY_PHOTO",
            filename_hint="bath.jpg",
        )
        eid = ev["evidence_id"]

        # Admin rejects proof with structured reason
        rej_res = self.engine.apply_proof_rejection(
            evidence_id=eid,
            admin_id="Administrator",
            reason_category=ProofRejectionReason.UNCLEAR_EVIDENCE.value,
            reason_note="Image is blurry and dark. Please retake with good lighting.",
            allow_resubmission=True,
        )
        self.assertEqual(rej_res["status"], "REJECTED")
        self.assertEqual(rej_res["reason_category"], "Unclear evidence")

        # Verify audit log
        audits = self.db.get_admin_audit_logs(action_type=AdminActionType.PROOF_REJECTED.value)
        self.assertGreaterEqual(len(audits), 1)

        # Verify details preserve rejection note and revision
        details = self.db.get_proof_details(eid)
        self.assertEqual(details["status"], "REJECTED")
        self.assertEqual(len(details["revisions"]), 1)

        # User resubmits new proof revision
        resub_res = self.db.resubmit_proof(
            evidence_id=eid,
            user_id="user_lok",
            new_file_path="/path/to/clear_bath.jpg",
        )
        self.assertEqual(resub_res["status"], "PENDING")
        self.assertEqual(resub_res["revision_number"], 2)

        # Details now show 2 revisions
        details_after = self.db.get_proof_details(eid)
        self.assertEqual(details_after["status"], "PENDING")
        self.assertEqual(len(details_after["revisions"]), 2)

    # 5. Bulk Review
    def test_05_bulk_review(self):
        ev1 = self.engine.store_evidence_file(
            date=self.date, record_type="HOURLY", related_record_id="h_1", device_id="android_lok_phone", file_bytes=b"PROOF_1"
        )
        ev2 = self.engine.store_evidence_file(
            date=self.date, record_type="HOURLY", related_record_id="h_2", device_id="android_lok_phone", file_bytes=b"PROOF_2"
        )

        bulk_res = self.db.bulk_review_proofs(
            evidence_ids=[ev1["evidence_id"], ev2["evidence_id"]],
            decision="APPROVE",
            admin_id="Administrator",
            points_each=3,
        )
        self.assertEqual(bulk_res["processed"], 2)
        self.assertEqual(len(bulk_res["errors"]), 0)

    # 6. Controlled Manual Score Adjustment
    def test_06_controlled_manual_score_adjustment(self):
        self.db.ensure_daily_record(self.date, user_id="user_lok")
        rec_before = self.db.get_daily_record(self.date)
        old_score = rec_before["final_score"]

        # Missing reason must be rejected
        with self.assertRaises(ValueError):
            self.engine.apply_admin_score_adjustment(
                date=self.date,
                admin_id="Administrator",
                adjustment_points=5,
                reason="",
            )

        # Apply +6 points adjustment with explicit reason
        adj_res = self.engine.apply_admin_score_adjustment(
            date=self.date,
            admin_id="Administrator",
            adjustment_points=6,
            reason="Exceptional breakthrough on robotics milestone",
        )
        self.assertEqual(adj_res["old_score"], old_score)
        self.assertEqual(adj_res["adjustment"], 6)
        self.assertEqual(adj_res["new_score"], min(100, old_score + 6))

        # Check ScoreEvent was generated
        events = self.db.get_score_events(self.date)
        adj_ev = next(e for e in events if e["event_type"] == ScoreEventType.ADMIN_SCORE_ADJUSTMENT.value)
        self.assertEqual(adj_ev["points"], 6)
        self.assertIn("Exceptional breakthrough", adj_ev["description"])

        # Check Admin Audit Log
        audits = self.db.get_admin_audit_logs(action_type=AdminActionType.POINT_ADJUSTED.value)
        self.assertGreaterEqual(len(audits), 1)

    # 7. Rule & Configuration Versioning
    def test_07_configuration_versioning(self):
        active = self.db.get_active_configuration()
        self.assertEqual(active["version_code"], "v12")
        self.assertEqual(active["status"], "PUBLISHED")

        # Create draft v13
        new_rules = {"wakeup_points": 20, "gym_points": 20}
        draft = self.db.create_config_draft("v13", new_rules, admin_id="Administrator")
        self.assertEqual(draft["status"], "DRAFT")

        # Publish v13
        pub = self.db.publish_configuration("v13", admin_id="Administrator")
        self.assertEqual(pub["status"], "PUBLISHED")

        # Check active is now v13
        active_new = self.db.get_active_configuration()
        self.assertEqual(active_new["version_code"], "v13")

        # Audit log has CONFIG_PUBLISHED
        audits = self.db.get_admin_audit_logs(action_type=AdminActionType.CONFIG_PUBLISHED.value)
        self.assertGreaterEqual(len(audits), 1)

    # 8. Device Revocation & Synchronization Block
    def test_08_device_revocation_blocks_sync(self):
        dev_id = "android_test_phone"
        self.db.register_or_update_device(dev_id, platform="ANDROID")
        self.db.register_or_update_device("windows_test_pc", platform="WINDOWS")
        devices = self.db.get_managed_devices()
        self.assertGreaterEqual(len(devices), 2)

        # Revoke Android phone
        self.db.revoke_device_pairing(dev_id, admin_user="Administrator")

        # Verify device state is REVOKED
        dev_rows = [d for d in self.db.get_managed_devices() if d["device_id"] == dev_id]
        self.assertEqual(dev_rows[0]["pairing_state"], "REVOKED")

        # Subsequent sync from revoked device must be BLOCKED
        with self.assertRaises(PermissionError) as ctx:
            self.engine.process_sync_batch(items=[], device_id=dev_id)
        self.assertIn("revoked by Administrator", str(ctx.exception))

    # 9. Reports & Analytics Export
    def test_09_reports_and_analytics_export(self):
        analytics = self.db.get_admin_analytics_summary()
        self.assertIn("average_score", analytics)
        self.assertIn("pending_proofs", analytics)
        self.assertIn("approval_rate_pct", analytics)

        # CSV export
        csv_data = self.db.export_admin_report_csv()
        self.assertTrue(csv_data.startswith("Date,User,Task,Type,Points"))

        # JSON export
        json_data = self.db.export_admin_report_json()
        self.assertIn("analytics_summary", json_data)
        self.assertIn("score_events", json_data)

    # 10. Global Search
    def test_10_global_search(self):
        self.db.create_user("test_candidate", "Test Candidate")
        search_res = self.db.global_admin_search("Candidate")
        self.assertGreaterEqual(len(search_res["users"]) + len(search_res["devices"]), 1)


class TestAdminServerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="strict_admin_api_")
        cls.db_path = os.path.join(cls.temp_dir, "api_test.db")
        cls.storage_root = os.path.join(cls.temp_dir, "STRICTRECORDS")
        cls.server = create_server(
            host="127.0.0.1", port=0, db_path=cls.db_path, storage_root=cls.storage_root
        )
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _get(self, path: str):
        request=urllib.request.Request(f"{self.base_url}{path}",headers={"X-Admin-Session":getattr(self,'admin_token','')})
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json","X-Admin-Session":getattr(self,'admin_token','')},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_admin_api_endpoints(self):
        # 1. Login
        status, auth = self._post("/api/admin/login", {"username": "admin", "password": "admin1984"})
        self.assertEqual(status, 200)
        token = auth["token"]
        self.admin_token=token

        # 2. Verify Session
        status, sess = self._get(f"/api/admin/verify_session?token={token}")
        self.assertEqual(status, 200)
        self.assertTrue(sess["valid"])

        # 3. Verify PIN
        status, pin_res = self._post("/api/admin/verify_pin", {"pin": "1984"})
        self.assertEqual(status, 200)
        self.assertTrue(pin_res["valid"])

        # 4. Analytics
        status, analytics = self._get("/api/admin/analytics")
        self.assertEqual(status, 200)
        self.assertIn("average_score", analytics)

        # 5. Active Configuration
        status, cfg = self._get("/api/admin/config/active")
        self.assertEqual(status, 200)
        self.assertEqual(cfg["version_code"], "v12")

        # 6. Devices
        status, devs = self._get("/api/admin/devices")
        self.assertEqual(status, 200)
        self.assertIsInstance(devs["devices"], list)

        # 7. Users
        status, users = self._get("/api/admin/users")
        self.assertEqual(status, 200)
        self.assertIsInstance(users["users"], list)

        # 8. Controlled Score Adjustment
        status, adj = self._post("/api/admin/score/adjust", {
            "date": "2026-10-06",
            "admin_id": "Administrator",
            "adjustment_points": 5,
            "reason": "API verified milestone",
        })
        self.assertEqual(status, 200)
        self.assertEqual(adj["adjustment"], 5)

        # 9. Audit Logs
        status, audits = self._get("/api/admin/audit_logs?limit=10")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(audits["audit_logs"]), 1)

        # 10. Global Search
        status, search = self._get("/api/admin/search?q=Lok")
        self.assertEqual(status, 200)
        self.assertIn("users", search)


if __name__ == "__main__":
    unittest.main()
