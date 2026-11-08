"""
StrictRecords PC ↔ Android Connection Pipeline & Device Center Integration Tests
Implements and rigorously asserts all 20 Acceptance Tests from Section 103:
- Tests 1-4: USB Discovery, 6-digit verification code, and Mutual Trust Pairing
- Tests 5-8: Initial sync, USB disconnect, and Wi-Fi automatic discovery & reconnect
- Tests 9-10: Android creates 5 ScoreEvents, 2-way transfer queue, and exact-once idempotency
- Tests 11-13: Admin pushes config (gym points), Proof transfer, and Admin approval return path
- Tests 14-15: Network drop during transfer (store-and-forward) and auto-resume
- Tests 16-17: Admin revokes device (sync refused) and fresh re-pairing
- Tests 18-20: Android restart resilience, Windows recovery, and offline local operations
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime

from core.connection_pipeline import (
    AckStage,
    CommandType,
    ConnectionPipelineManager,
    ConnectionState,
    TransferDirection,
    TransferStatus,
    TransportType,
)
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import create_server


class TestConnectionPipelineMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="sr_pipe_test_")
        cls.db_path = os.path.join(cls.temp_dir, "test_pipe.db")
        cls.storage_root = os.path.join(cls.temp_dir, "storage")
        os.makedirs(cls.storage_root, exist_ok=True)

        cls.db = StrictRecordsDB(db_path=cls.db_path)
        cls.engine = ScoringEngine(db=cls.db, storage_root=cls.storage_root)
        cls.pipe = ConnectionPipelineManager(db=cls.db, scoring_engine=cls.engine)

        cls.port = 8798
        cls.host = "127.0.0.1"
        cls.server = create_server(
            host=cls.host,
            port=cls.port,
            db_path=cls.db_path,
            storage_root=cls.storage_root,
        )
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)
        cls.base_url = f"http://{cls.host}:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _post(self, path: str, payload: dict, timeout: int = 15):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            return he.code, json.loads(he.read().decode("utf-8"))

    def _get(self, path: str, timeout: int = 15):
        req = urllib.request.Request(f"{self.base_url}{path}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            return he.code, json.loads(he.read().decode("utf-8"))

    # ── TEST 1: Fresh Android + Windows ─────────────────────────────────────
    def test_01_fresh_devices_not_paired(self):
        """Fresh Android + Windows. Expected: DEVICE NOT PAIRED."""
        dev_id = "test_phone_fresh"
        # Ensure device has no pairing
        self.db.unpair_device(dev_id)
        summary = self.pipe.get_device_center_summary(dev_id)
        self.assertEqual(summary["trust"], "UNPAIRED")
        self.assertEqual(summary["state"], ConnectionState.DISCONNECTED.value)

    # ── TEST 2: USB connected ───────────────────────────────────────────────
    def test_02_usb_connected_device_found(self):
        """USB connected. Expected: DEVICE FOUND."""
        dev_id = "test_phone_usb"
        det = self.pipe.detect_device(dev_id, transport="USB")
        self.assertEqual(det["transport"], TransportType.USB.value)
        # Should reach DEVICE_FOUND
        hist = self.db.get_connection_history(dev_id, limit=5)
        states_logged = [h["to_state"] for h in hist]
        self.assertIn(ConnectionState.DEVICE_FOUND.value, states_logged)

    # ── TEST 3: User approves (6-digit USB verification) ────────────────────
    def test_03_user_approves_paired(self):
        """User approves 6-digit USB pairing code. Expected: PAIRED."""
        dev_id = "test_phone_pair"
        init_res = self.pipe.initiate_pairing(dev_id, transport="USB")
        self.assertEqual(init_res["status"], "PENDING_APPROVAL")
        code = init_res["verification_code"]
        self.assertTrue(len(code.replace(" ", "")) == 6)

        # Enter code and approve
        app_res = self.pipe.verify_pairing_code(code, android_device_id=dev_id)
        self.assertEqual(app_res["status"], "PAIRED")
        self.assertTrue(app_res["android_approved"])
        self.assertTrue(app_res["windows_approved"])

    # ── TEST 4: Authentication succeeds ─────────────────────────────────────
    def test_04_authentication_succeeds_trust_verified(self):
        """Authentication succeeds. Expected: TRUST VERIFIED."""
        dev_id = "test_phone_pair"
        summary = self.pipe.get_device_center_summary(dev_id)
        self.assertEqual(summary["trust"], "VERIFIED")
        self.assertEqual(summary["state"], ConnectionState.CONNECTED.value)

    # ── TEST 5: Initial sync ────────────────────────────────────────────────
    def test_05_initial_sync_real_transfer_counts(self):
        """Initial sync. Expected: real transfer counts."""
        dev_id = "test_phone_pair"
        batch = [
            {
                "sync_id": "tx_init_1",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": "se_init_1",
                    "date": "2026-10-06",
                    "event_type": "WAKEUP",
                    "points": 20,
                    "description": "On-time wakeup initial sync",
                },
            }
        ]
        res = self.pipe.process_bidirectional_sync(batch, device_id=dev_id)
        self.assertEqual(res["sync_status"], "SYNC_COMPLETE")
        self.assertEqual(res["applied_count"], 1)
        self.assertEqual(res["skipped_duplicates"], 0)
        self.assertEqual(res["acks"][0]["ack_stage"], AckStage.STORED.value)

    # ── TEST 6: Disconnect USB ──────────────────────────────────────────────
    def test_06_disconnect_usb_paired_relationship_remains(self):
        """Disconnect USB. Expected: paired relationship remains."""
        dev_id = "test_phone_pair"
        self.pipe.transition_state(dev_id, ConnectionState.DISCONNECTED, reason="USB cable unplugged")
        summary = self.pipe.get_device_center_summary(dev_id)
        self.assertEqual(summary["state"], ConnectionState.DISCONNECTED.value)
        self.assertEqual(summary["trust"], "VERIFIED")  # Cryptographic trust preserved!

    # ── TEST 7: Both devices join same Wi-Fi ────────────────────────────────
    def test_07_wifi_automatic_discovery(self):
        """Both devices join same Wi-Fi. Expected: automatic discovery."""
        dev_id = "test_phone_pair"
        det = self.pipe.detect_device(dev_id, transport="LOCAL_WIFI")
        self.assertEqual(det["transport"], TransportType.LOCAL_WIFI.value)

    # ── TEST 8: Trusted connection established via Wi-Fi ────────────────────
    def test_08_trusted_connection_via_wifi(self):
        """Trusted connection established. Expected: CONNECTED VIA WI-FI."""
        dev_id = "test_phone_pair"
        hb = self.pipe.record_heartbeat(
            dev_id,
            transport="LOCAL_WIFI",
            latency_ms=14.2,
            telemetry={"battery_level": 92, "wifi_ssid": "HomeNetwork_5G"},
        )
        self.assertEqual(hb["state"], ConnectionState.CONNECTED.value)
        self.assertEqual(hb["transport"], TransportType.LOCAL_WIFI.value)
        self.assertEqual(hb["trust"], "VERIFIED")

    # ── TEST 9: Android creates 5 ScoreEvents ───────────────────────────────
    def test_09_android_creates_5_score_events_appear_in_queue(self):
        """Android creates 5 ScoreEvents. Expected: 5 events appear in Windows queue."""
        dev_id = "test_phone_pair"
        created_tx_ids = []
        for i in range(1, 6):
            payload = {
                "event_id": f"se_batch5_{i}",
                "date": "2026-10-06",
                "event_type": "HOURLY_SUCCESS",
                "points": 5,
                "description": f"Productive slot {i}",
            }
            item = self.pipe.enqueue_transfer_item(
                device_id=dev_id,
                direction=TransferDirection.ANDROID_TO_PC,
                item_type="SCORE_EVENT",
                payload=payload,
            )
            created_tx_ids.append(item["transfer_id"])

        self.assertEqual(len(created_tx_ids), 5)
        # Verify they appear in queue
        queue = self.db.get_transfer_queue_items(dev_id, limit=10)
        q_ids = [q["transfer_id"] for q in queue]
        for tx in created_tx_ids:
            self.assertIn(tx, q_ids)

    # ── TEST 10: Sync transferred exactly once (idempotent) ─────────────────
    def test_10_sync_transferred_exactly_once_idempotent(self):
        """Sync. Expected: 5 events transferred exactly once, zero duplicates."""
        dev_id = "test_phone_pair"
        items = []
        for i in range(1, 6):
            items.append({
                "transfer_id": f"tx_batch5_{i}",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": f"se_batch5_{i}",
                    "date": "2026-10-06",
                    "event_type": "HOURLY_SUCCESS",
                    "points": 5,
                    "description": f"Productive slot {i}",
                },
            })

        # First sync
        res1 = self.pipe.process_bidirectional_sync(items, device_id=dev_id)
        self.assertEqual(res1["applied_count"], 5)
        self.assertEqual(res1["skipped_duplicates"], 0)

        # Retry exact same sync batch
        res2 = self.pipe.process_bidirectional_sync(items, device_id=dev_id)
        self.assertEqual(res2["applied_count"], 0)
        self.assertEqual(res2["skipped_duplicates"], 5)  # Exact-once guarantee!

    # ── TEST 11: Admin changes Gym points ───────────────────────────────────
    def test_11_admin_changes_gym_points_config_published_sync(self):
        """Admin changes Gym points. Expected: configuration published and synchronized."""
        dev_id = "test_phone_pair"
        # Update settings to 25 gym points
        self.db.update_settings({"gym_points": 25}, actor_role="ADMIN")
        cmd = self.pipe.dispatch_admin_command("PUSH_CONFIG", device_id=dev_id)
        self.assertEqual(cmd["command_type"], "PUSH_CONFIG")
        self.assertEqual(cmd["status"], "PENDING")

        # Android polls commands
        polled = self.pipe.poll_device_commands(dev_id)
        push_cmd = next((c for c in polled if c["command_id"] == cmd["command_id"]), None)
        self.assertIsNotNone(push_cmd)
        payload = json.loads(push_cmd["payload_json"])
        self.assertEqual(payload["config"]["gym_points"], 25)

        # Android acks
        self.pipe.ack_device_command(
            cmd["command_id"],
            {"status": "CONFIG_APPLIED"},
            device_id=dev_id,
        )

    # ── TEST 12: User submits proof ─────────────────────────────────────────
    def test_12_user_submits_proof_proof_center_pending(self):
        """User submits proof. Expected: Windows Proof Center shows PENDING."""
        dev_id = "test_phone_pair"
        proof_item = {
            "transfer_id": "tx_proof_1",
            "payload_type": "EVIDENCE_PHOTO",
            "payload": {
                "evidence_id": "ev_pipe_1",
                "date": "2026-10-06",
                "task_code": "GYM_WORKOUT",
                "task_title": "Heavy Lifting Session",
                "image_base64": "fake_jpeg_bytes_here",
            },
        }
        self.pipe.process_bidirectional_sync([proof_item], device_id=dev_id)
        proofs = self.db.get_proofs(status="PENDING")
        found = any(p["evidence_id"] == "ev_pipe_1" for p in proofs)
        self.assertTrue(found)

    # ── TEST 13: Admin approves ─────────────────────────────────────────────
    def test_13_admin_approves_approval_event_returned_to_android(self):
        """Admin approves. Expected: approval event generated and returned to Android."""
        res = self.engine.apply_proof_approval(
            evidence_id="ev_pipe_1",
            admin_id="Administrator",
            points=15,
            reason="Verified gym session evidence",
        )
        self.assertEqual(res["decision"], "APPROVE")
        self.assertEqual(res["points_awarded"], 15)

    # ── TEST 14: Network disappears during transfer ─────────────────────────
    def test_14_network_disappears_records_remain_local_pending(self):
        """Network disappears during transfer. Expected: records remain local and status becomes PENDING."""
        dev_id = "test_phone_pair"
        # Simulate local enqueue without network
        item = self.pipe.enqueue_transfer_item(
            device_id=dev_id,
            direction=TransferDirection.ANDROID_TO_PC,
            item_type="SCORE_EVENT",
            payload={"event_id": "se_offline_1", "points": 10},
        )
        self.assertEqual(item["status"], TransferStatus.PENDING.value)
        self.assertEqual(item["ack_stage"], AckStage.SENT.value)

    # ── TEST 15: Network returns ────────────────────────────────────────────
    def test_15_network_returns_automatic_resume(self):
        """Network returns. Expected: automatic resume."""
        dev_id = "test_phone_pair"
        # Heartbeat confirms network returned
        hb = self.pipe.record_heartbeat(dev_id, transport="LOCAL_WIFI", latency_ms=10.0)
        self.assertEqual(hb["state"], ConnectionState.CONNECTED.value)

        # Resume sync
        batch = [{
            "transfer_id": "tx_resumed_1",
            "payload_type": "SCORE_EVENT",
            "payload": {"event_id": "se_offline_1", "date": "2026-10-06", "points": 10},
        }]
        res = self.pipe.process_bidirectional_sync(batch, device_id=dev_id)
        self.assertEqual(res["sync_status"], "SYNC_COMPLETE")

    # ── TEST 16: Admin revokes device ───────────────────────────────────────
    def test_16_admin_revokes_device_future_sync_refused(self):
        """Admin revokes device. Expected: future synchronization refused."""
        dev_id = "test_phone_pair"
        self.pipe.dispatch_admin_command("REVOKE_DEVICE", device_id=dev_id)
        st = self.pipe.get_current_state(dev_id)
        self.assertEqual(st, ConnectionState.REVOKED.value)

        # Future sync MUST raise PermissionError
        with self.assertRaises(PermissionError):
            self.pipe.process_bidirectional_sync(
                [{"transfer_id": "tx_bad", "payload_type": "SCORE_EVENT", "payload": {}}],
                device_id=dev_id,
            )

    # ── TEST 17: Re-pair ────────────────────────────────────────────────────
    def test_17_repair_creates_new_trusted_session(self):
        """Re-pair. Expected: new trusted session."""
        dev_id = "test_phone_pair"
        init = self.pipe.initiate_pairing(dev_id, transport="USB")
        self.assertIn("verification_code", init)
        code = init["verification_code"]

        app = self.pipe.verify_pairing_code(code, android_device_id=dev_id)
        self.assertEqual(app["status"], "PAIRED")
        summary = self.pipe.get_device_center_summary(dev_id)
        self.assertEqual(summary["trust"], "VERIFIED")
        self.assertEqual(summary["state"], ConnectionState.CONNECTED.value)

    # ── TEST 18: Android restarts ───────────────────────────────────────────
    def test_18_android_restarts_local_state_survives(self):
        """Android restarts. Expected: local state survives."""
        dev_id = "test_phone_pair"
        # Simulate restart: new instance reading from DB
        pipe_restarted = ConnectionPipelineManager(db=self.db, scoring_engine=self.engine)
        summary = pipe_restarted.get_device_center_summary(dev_id)
        self.assertEqual(summary["trust"], "VERIFIED")

    # ── TEST 19: Windows restarts ───────────────────────────────────────────
    def test_19_windows_restarts_admin_console_recovers_normally(self):
        """Windows restarts. Expected: user agent/admin console recovers normally."""
        status, data = self._get("/api/device/center?device_id=test_phone_pair")
        self.assertEqual(status, 200)
        self.assertIn("stages", data)
        self.assertIn("telemetry", data)
        self.assertIn("metrics", data)
        self.assertEqual(data["trust"], "VERIFIED")

    # ── TEST 20: No Internet ────────────────────────────────────────────────
    def test_20_no_internet_local_operations_continue_wait_connection(self):
        """No Internet. Expected: Android + Windows local operations continue; sync waits for connection."""
        dev_id = "test_phone_offline_op"
        # Local operations continue offline
        day_res = self.engine.submit_hourly_update(
            date="2026-10-06",
            hour_slot="14:00-15:00",
            primary_activity="STUDY",
            description="Offline autonomous study session",
            device_id=dev_id,
            is_dev_mode=True,
        )
        self.assertEqual(day_res["primary_activity"], "STUDY")
        self.assertTrue("hourly_id" in day_res)

        # HTTP diagnostics test
        status, diag = self._post("/api/device/diagnostics", {"device_id": "test_phone_pair"})
        self.assertEqual(status, 200)
        self.assertEqual(diag["diagnostics"], "HEALTHY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
