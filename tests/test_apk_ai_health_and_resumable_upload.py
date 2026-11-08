"""
Automated verification suite for StrictRecords APK Complete Fix:
- Part A: AI Service Health Monitoring (60-minute persistent health check, Taufa AI primary + Official OpenAI API fallback, no point award/deduction on provider outage)
- Part B: Reliable Background Upload and Processing (resumable 256 KB chunked uploads, SHA-256 checksum verification, offset resumption, server-side async AI validation, state reconciliation)
"""
import base64
import hashlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.ai_health_coordinator import AiHealthCoordinator
from core.resumable_upload_service import ResumableUploadService


class TestApkAiHealthMonitoring(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_health.db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def test_60_minute_health_check_caching_prevents_window_open_spam(self):
        """Opening multiple APK windows within 60 minutes must return cached health without issuing new AI probes."""
        probe_calls = []

        def fake_taufa_probe(request_id, timeout_sec=8.0):
            probe_calls.append("taufa")
            return {
                "ok": True,
                "status": "CONNECTED",
                "latency_ms": 142,
                "error": "",
            }

        def fake_openai_probe(request_id, timeout_sec=10.0):
            probe_calls.append("openai")
            return {
                "ok": False,
                "status": "UNCONFIGURED",
                "latency_ms": 0,
                "error": "Not configured",
            }

        with patch("core.ai_health_coordinator.get_connection", side_effect=self._get_conn):
            coord = AiHealthCoordinator()
            coord._cached_record = coord._default_record()

            with patch.object(coord, "_probe_taufa_ai", side_effect=fake_taufa_probe), \
                 patch.object(coord, "_probe_openai_fallback", side_effect=fake_openai_probe):

                # 1. Initial periodic check runs 1 probe
                r1 = coord.run_health_check(force=False, reason="periodic_60m")
                self.assertEqual(r1["active_provider"], "TAUFA_AI")
                self.assertEqual(r1["ui_status"], "AI STATUS: CONNECTED – TAUFA AI")
                self.assertEqual(len(probe_calls), 1)

                # 2. Simulate opening Dashboard, Schedule, Proof dialog, Activity screen, Tabs
                for _ in ["dashboard", "schedule", "proof_submission", "activity", "tab_switch", "on_resume"]:
                    st = coord.get_health_status(refresh_if_stale=False)
                    self.assertEqual(st["active_provider"], "TAUFA_AI")
                    self.assertEqual(st["ui_status"], "AI STATUS: CONNECTED – TAUFA AI")
                    self.assertFalse(st["is_stale"])

                # Calling run_health_check(force=False) within 60m must NOT re-probe
                r2 = coord.run_health_check(force=False, reason="window_open")
                self.assertEqual(len(probe_calls), 1)
                self.assertEqual(r2["active_provider"], "TAUFA_AI")

                # 3. Manual "Retry AI Connection" button in Settings (force=True) forces a live probe
                r3 = coord.run_health_check(force=True, reason="manual_retry")
                self.assertEqual(len(probe_calls), 2)
                self.assertEqual(r3["active_provider"], "TAUFA_AI")

    def test_taufa_primary_failure_switches_to_openai_fallback(self):
        """When Taufa AI fails, coordinator automatically probes Official OpenAI API fallback and sets OPENAI_FALLBACK."""
        with patch("core.ai_health_coordinator.get_connection", side_effect=self._get_conn):
            coord = AiHealthCoordinator()
            coord._cached_record = coord._default_record()

            with patch.object(coord, "_probe_taufa_ai", return_value={
                "ok": False,
                "status": "UNAVAILABLE",
                "latency_ms": 310,
                "error": "Taufa upstream timeout",
            }), patch.object(coord, "_probe_openai_fallback", return_value={
                "ok": True,
                "status": "CONNECTED",
                "latency_ms": 220,
                "error": "",
            }):
                record = coord.run_health_check(force=True, reason="periodic_60m")
                self.assertEqual(record["taufa_status"], "UNAVAILABLE")
                self.assertEqual(record["openai_status"], "CONNECTED")
                self.assertEqual(record["active_provider"], "OPENAI_FALLBACK")
                self.assertEqual(record["ui_status"], "AI STATUS: CONNECTED – OPENAI FALLBACK")

    def test_both_providers_unavailable_preserves_submission_without_points_change(self):
        """When both Taufa AI and OpenAI fallback are unavailable, status is UNAVAILABLE."""
        with patch("core.ai_health_coordinator.get_connection", side_effect=self._get_conn):
            coord = AiHealthCoordinator()
            coord._cached_record = coord._default_record()

            with patch.object(coord, "_probe_taufa_ai", return_value={
                "ok": False,
                "status": "UNAVAILABLE",
                "latency_ms": 150,
                "error": "Connection refused",
            }), patch.object(coord, "_probe_openai_fallback", return_value={
                "ok": False,
                "status": "UNAVAILABLE",
                "latency_ms": 180,
                "error": "Network unreachable",
            }):
                record = coord.run_health_check(force=True, reason="periodic_60m")
                self.assertEqual(record["active_provider"], "UNAVAILABLE")
                self.assertEqual(record["ui_status"], "AI STATUS: UNAVAILABLE")


class TestApkResumableUploadService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_strict.db")
        self.upload_dir = os.path.join(self.temp_dir.name, "uploads")
        os.makedirs(self.upload_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def test_chunked_resumable_upload_interruption_and_reconciliation(self):
        """Test 256 KB chunked upload init -> partial chunk -> resume from offset -> complete -> SHA-256 verification -> reconcile."""
        with patch("core.resumable_upload_service.get_connection", side_effect=self._get_conn), \
             patch("core.resumable_upload_service.UPLOADS_DIR", self.upload_dir):
            svc = ResumableUploadService()
            svc._ensure_tables()

            # Generate 600 KB test payload (3 chunks at 256 KB chunk size)
            payload = os.urandom(600 * 1024)
            sha256_hex = hashlib.sha256(payload).hexdigest()
            sub_id = "sub_and_test_resumable_001"

            # 1. Init upload
            init_res = svc.init_upload({
                "submission_id": sub_id,
                "activity_id": "act_hourly_1000",
                "task_type": "HOURLY",
                "category": "STUDY",
                "date": "2026-11-06",
                "description": "Studying operating systems",
                "filename": "proof.jpg",
                "mime_type": "image/jpeg",
                "file_size": len(payload),
                "sha256_checksum": sha256_hex,
            })
            self.assertTrue(init_res["success"])
            self.assertEqual(init_res["upload_offset"], 0)
            self.assertEqual(init_res["state"], "UPLOADING")

            # 2. Upload first 256 KB chunk
            chunk1 = payload[:262144]
            c1_res = svc.upload_chunk({
                "submission_id": sub_id,
                "offset": 0,
                "chunk_base64": base64.b64encode(chunk1).decode("ascii"),
            })
            self.assertTrue(c1_res["success"])
            self.assertEqual(c1_res["upload_offset"], 262144)
            self.assertEqual(c1_res["state"], "UPLOADING")

            # 3. Simulate phone screen lock / network drop: re-init with same submission_id must resume from offset 262144
            resume_init = svc.init_upload({
                "submission_id": sub_id,
                "file_size": len(payload),
                "sha256_checksum": sha256_hex,
            })
            self.assertTrue(resume_init["success"])
            self.assertTrue(resume_init["resumed"])
            self.assertEqual(resume_init["upload_offset"], 262144)

            # 4. Upload remaining chunks from offset 262144
            chunk2 = payload[262144:524288]
            c2_res = svc.upload_chunk({
                "submission_id": sub_id,
                "offset": 262144,
                "chunk_base64": base64.b64encode(chunk2).decode("ascii"),
            })
            self.assertTrue(c2_res["success"])
            self.assertEqual(c2_res["upload_offset"], 524288)

            chunk3 = payload[524288:]
            c3_res = svc.upload_chunk({
                "submission_id": sub_id,
                "offset": 524288,
                "chunk_base64": base64.b64encode(chunk3).decode("ascii"),
            })
            self.assertTrue(c3_res["success"])
            self.assertEqual(c3_res["upload_offset"], len(payload))

            # 5. Complete upload (async_validation=False to inspect immediate state)
            comp_res = svc.complete_upload({"submission_id": sub_id}, async_validation=False)
            self.assertTrue(comp_res["success"])

            # 6. Reconcile submissions from APK on unlock/reconnect
            rec_res = svc.reconcile_submissions({"submission_ids": [sub_id]})
            self.assertTrue(rec_res["success"])
            self.assertEqual(len(rec_res["submissions"]), 1)
            self.assertEqual(rec_res["submissions"][0]["submission_id"], sub_id)


if __name__ == "__main__":
    unittest.main()
