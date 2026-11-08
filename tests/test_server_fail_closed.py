"""Strict-session and restriction APIs never report success without a controller."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from core.connection_pipeline import ConnectionPipelineManager
from http.server import ThreadingHTTPServer

from core.database import StrictRecordsDB
from core.server import StrictRecordsRequestHandler


class TestServerFailClosed(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="strictrecords_fail_closed_")
        db = StrictRecordsDB(os.path.join(self.temp_dir.name, "test.db"))
        self.db = db

        class IsolatedHandler(StrictRecordsRequestHandler):
            pass

        IsolatedHandler.db = db
        IsolatedHandler.engine = None
        IsolatedHandler.win_agent = None
        self.connection_manager = ConnectionPipelineManager(db)
        IsolatedHandler.connection_manager = self.connection_manager
        IsolatedHandler.restriction_mgr = None
        IsolatedHandler.strict_session_manager = None
        IsolatedHandler.ram_optimizer = None
        IsolatedHandler.notification_service = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def post(self, path, payload):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def get(self, path, headers=None):
        request = urllib.request.Request(self.url + path, headers=headers or {}, method="GET")
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _register_device(self, device_id):
        session = self.db.create_pairing_session(account_id="user_lok")
        paired = self.db.handshake_pairing(
            session["session_key"],
            device_id=device_id,
            device_name=device_id,
        )
        self.assertTrue(paired["success"])
        return paired["device_token"]

    def test_wifi_pairing_session_payload_and_handshake(self):
        session = self.db.create_pairing_session(
            account_id="user_lok",
            server_endpoint=self.url,
        )
        qr_payload = json.loads(session["qr_payload"])
        self.assertEqual(qr_payload["session_key"], session["session_key"])
        self.assertEqual(qr_payload["server_endpoint"], self.url)

        status, paired = self.post(
            "/api/device/pairing/handshake",
            {
                "session_key": session["session_key"],
                "device_id": "pairing-route-test-device",
                "device_name": "Pairing route test",
                "device_type": "ANDROID",
                "platform": "Android test",
                "app_version": "1.14.2",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(paired["success"])
        self.assertTrue(paired.get("device_token"))
        self.assertEqual(paired.get("account_id"), "user_lok")

    def test_missing_strict_session_service_fails_closed(self):
        status, payload = self.post("/api/strict_session/precheck", {})
        self.assertEqual(status, 503)
        self.assertFalse(payload.get("all_passed", False))

        status, payload = self.post("/api/strict_session/privilege/evaluate", {})
        self.assertEqual(status, 503)
        self.assertFalse(payload["allowed"])

        status, payload = self.post("/api/strict_session/hourly_proof/submit", {"evidence_id": ""})
        self.assertEqual(status, 503)
        self.assertNotEqual(payload["status"], "VALID")

    def test_missing_restriction_service_does_not_grant_unlock(self):
        status, payload = self.post(
            "/api/restriction/request_unlock",
            {"reason": "educational programming tutorial", "duration_minutes": 30},
        )
        self.assertEqual(status, 503)
        self.assertFalse(payload["success"])

    def test_strict_session_policy_requires_admin_authorization(self):
        status, payload = self.post(
            "/api/strict_session/policy",
            {"policy": {"score_threshold": 0}},
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "UNAUTHORIZED")

        status, payload = self.post("/api/releases/notify", {})
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "UNAUTHORIZED")

        for path, body in (
            ("/api/device/revoke", {"device_id": "registered-device"}),
            ("/api/device/clear_all", {}),
            ("/api/privilege/youtube/mode", {"mode": "ALTERNATING_HOURS"}),
        ):
            status, payload = self.post(path, body)
            self.assertEqual(status, 401, path)
            self.assertEqual(payload["code"], "UNAUTHORIZED", path)

    def test_initial_admin_pin_session_is_limited_to_pin_rotation(self):
        session = self.db.authenticate_admin("admin", "1234", device_id="test-admin")
        token = session["token"]

        status, payload = self.get(f"/api/admin/config/active?token={token}")
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "PIN_CHANGE_REQUIRED")

        status, payload = self.post(
            "/api/admin/change_pin",
            {"token": token, "current_pin": "1234", "new_pin": "5678"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])

        status, payload = self.get(f"/api/admin/config/active?token={token}")
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, dict)

    def test_status_and_health_do_not_claim_access_when_controller_is_missing(self):
        status, payload = self.get("/api/restriction/status")
        self.assertEqual(status, 503)
        self.assertFalse(payload["allowed"])

        status, payload = self.get("/api/privilege/youtube/status")
        self.assertEqual(status, 503)
        self.assertFalse(payload["allowed"])

        status, payload = self.get("/api/system/health")
        self.assertEqual(status, 503)
        self.assertEqual(payload["Restriction"], "UNKNOWN")

        status, payload = self.get("/api/strict_session/status")
        self.assertEqual(status, 503)
        self.assertTrue(payload["lock_screen_active"])
        self.assertFalse(payload["privilege"]["allowed"])

    def test_device_sync_queue_and_command_ack_require_credential_and_device_ownership(self):
        status, payload = self.post("/api/device/sync", {"device_id": "unknown", "items": []})
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "UNAUTHORIZED_DEVICE")

        status, payload = self.post(
            "/api/device/transfer/queue",
            {"device_id": "unknown", "payload": {"points": 999}},
        )
        self.assertEqual(status, 403)

        status, payload = self.post(
            "/api/device/command/ack",
            {"device_id": "unknown", "command_id": "private-command", "success": True},
        )
        self.assertEqual(status, 403)

        status, payload = self.get("/api/device/command/poll?device_id=unknown")
        self.assertEqual(status, 403)
        status, payload = self.get("/api/device/transfer/queue?device_id=unknown")
        self.assertEqual(status, 403)

        device_a_token = self._register_device("device-command-owner")
        device_b_token = self._register_device("device-command-other")
        status, payload = self.post(
            "/api/device/sync",
            {"device_id": "device-command-owner", "device_token": device_a_token, "items": []},
        )
        self.assertEqual(status, 200)

        command = self.db.create_device_command(
            command_id="cmd_owner_test",
            device_id="device-command-owner",
            command_type="REQUEST_STATUS",
            payload_json="{}",
            nonce="nonce_owner_test",
        )
        status, payload = self.get(
            "/api/device/command/poll?device_id=device-command-owner",
            headers={"X-Device-Token": device_a_token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["commands"][0]["command_id"], command["command_id"])

        status, payload = self.post(
            "/api/device/command/ack",
            {
                "device_id": "device-command-other",
                "device_token": device_b_token,
                "command_id": command["command_id"],
                "result": {"status": "spoofed"},
            },
        )
        self.assertEqual(status, 404)
        self.assertFalse(payload["acknowledged"])
        self.assertEqual(self.db.get_device_commands("device-command-owner")[0]["status"], "DISPATCHED")

        status, payload = self.post(
            "/api/device/command/ack",
            {
                "device_id": "device-command-owner",
                "device_token": device_a_token,
                "command_id": command["command_id"],
                "result": {"status": "applied"},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["acknowledged"])

    def test_revoked_device_credential_cannot_call_bidirectional_sync(self):
        token = self._register_device("device-revoked-test")
        self.db.revoke_registered_device("device-revoked-test")

        status, payload = self.post(
            "/api/device/sync",
            {"device_id": "device-revoked-test", "device_token": token, "items": []},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "UNAUTHORIZED_DEVICE")


if __name__ == "__main__":
    unittest.main()
