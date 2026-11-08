"""
Unit and Integration Test Suite for StrictRecords APK In-App Updater & Release System.
Tests:
1. Manifest API & Version Comparison (GET /api/releases/latest)
2. Release Publishing Pipeline (POST /api/releases/publish)
3. Binary Streaming & APK Download (GET /api/releases/download/<file>)
4. Active / Inactive Release Toggling (POST /api/releases/toggle_active)
5. Device Status Tracking & Synchronization (POST /api/device/update_status)
6. Heartbeat Version Detection & Update Flagging (POST /api/device/heartbeat)
7. Integrity Verification (SHA-256 calculation & size check)
"""

import os
import sys
import json
import time
import queue
import shutil
import hashlib
import unittest
import tempfile
import threading

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.server import EventBus, StrictRecordsRequestHandler
from http.server import ThreadingHTTPServer
from core.scoring_engine import ScoringEngine
from unittest.mock import patch

class TestApkUpdaterSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="strict_updater_test_")
        cls.db_path = os.path.join(cls.test_dir, "test_strictrecords.db")

        # Set up dummy APK for testing
        cls.releases_dir = os.path.join(cls.test_dir, "releases")
        os.makedirs(cls.releases_dir, exist_ok=True)
        cls.server_root_patch = patch('core.server.ROOT_DIR', cls.test_dir)
        cls.server_root_patch.start()
        cls.dummy_apk_path = os.path.join(cls.test_dir, "test_strictrecords_v12.apk")
        real_apk = os.path.join(ROOT_DIR, "app-debug.apk")
        if os.path.exists(real_apk):
            shutil.copy2(real_apk, cls.dummy_apk_path)
        else:
            with open(cls.dummy_apk_path, "wb") as f:
                f.write(b"PK\x03\x04DummyStrictRecordsApkBinaryPayloadForTesting1234567890")
        
        cls.dummy_size = os.path.getsize(cls.dummy_apk_path)
        with open(cls.dummy_apk_path, "rb") as f:
            cls.dummy_sha256 = hashlib.sha256(f.read()).hexdigest().lower()

        # Exercise the real release routes without starting enforcement against
        # the developer's browser or opening the live application database.
        cls.db = StrictRecordsDB(cls.db_path)
        class ReleaseHandler(StrictRecordsRequestHandler):
            pass
        ReleaseHandler.db = cls.db
        ReleaseHandler.engine = ScoringEngine(cls.db, storage_root=cls.test_dir)
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), ReleaseHandler)
        cls.server_port = cls.httpd.server_address[1]
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        except Exception:
            pass
        cls.server_root_patch.stop()
        try:
            shutil.rmtree(cls.test_dir)
        except Exception:
            pass

    def setUp(self):
        self.base_url = f"http://127.0.0.1:{self.server_port}"

    def _register_test_device(self, device_id: str, device_name: str, token: str):
        now_iso = "2026-10-10 12:00:00"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO devices (device_id, user_id, device_name, platform, app_version, status, sync_status, pairing_state, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, "user_lok", device_name, "ANDROID", "1.0.0", "ONLINE", "ONLINE", "PAIRED", now_iso)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO device_credentials (credential_id, device_id, account_id, token_hash, issued_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (f"cred_{device_id}", device_id, "user_lok", token_hash, now_iso)
            )

    def _http_get(self, path: str):
        import urllib.request
        req = urllib.request.Request(f"{self.base_url}{path}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                return resp.status, resp.headers, data
        except urllib.error.HTTPError as e:
            data = e.read()
            e.close()
            return e.code, e.headers, data

    def _http_post(self, path: str, payload: dict):
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return resp.status, resp.headers, json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read()
            e.close()
            return e.code, e.headers, json.loads(body.decode("utf-8")) if body else {}

    def test_01_publish_release_with_valid_and_invalid_pin(self):
        """Verify release publishing requires valid Admin PIN and publishes manifest to EventBus."""
        # 1. Invalid PIN should return 403
        status, _, body = self._http_post("/api/releases/publish", {
            "admin_pin": "wrong_pin",
            "version_name": "1.2.0",
            "version_code": 12
        })
        self.assertEqual(status, 403)
        self.assertIn("UNAUTHORIZED", body.get("code", ""))

        # 2. Capture EventBus publication
        bus = EventBus.get_instance()
        q = bus.subscribe()

        try:
            # 3. Valid PIN should publish release
            status, _, body = self._http_post("/api/releases/publish", {
                "admin_pin": "1984",
                "version_name": "1.2.0",
                "version_code": 12,
                "channel": "PRODUCTION",
                "mandatory": False,
                "release_notes": "• In-app updater support\n• Real-time sync",
                "source_apk_path": self.dummy_apk_path,
                "platform": "ANDROID"
            })

            self.assertEqual(status, 200, f"Failed with body: {body}")
            self.assertTrue(body.get("success"))
            self.assertEqual(body.get("version_code"), 12)
            self.assertEqual(body.get("sha256"), self.dummy_sha256)
            self.assertEqual(body.get("file_size_bytes"), self.dummy_size)

            # Drain queue until APK_UPDATE_AVAILABLE is found
            found_evt = None
            event_deadline = time.monotonic() + 5
            while time.monotonic() < event_deadline:
                try:
                    evt = q.get(timeout=1.0)
                    if evt.get("event_type") == "APK_UPDATE_AVAILABLE":
                        found_evt = evt
                        break
                except queue.Empty:
                    break

            self.assertIsNotNone(found_evt)
            self.assertEqual(found_evt.get("version_code"), 12)
            self.assertEqual(found_evt.get("version_name"), "1.2.0")
        finally:
            bus.unsubscribe(q)

    def test_02_get_latest_release_manifest(self):
        """Verify GET /api/releases/latest returns formatted release manifest with dynamic Host URL."""
        status, headers, data = self._http_get("/api/releases/latest?platform=ANDROID&channel=PRODUCTION")
        self.assertEqual(status, 200)
        resp = json.loads(data.decode("utf-8"))
        self.assertTrue(resp.get("success"))
        rel = resp.get("release")
        self.assertIsNotNone(rel)
        self.assertEqual(rel.get("versionCode"), 12)
        self.assertEqual(rel.get("versionName"), "1.2.0")
        self.assertEqual(rel.get("sha256"), self.dummy_sha256)
        self.assertEqual(rel.get("fileSizeBytes"), self.dummy_size)
        self.assertTrue(
            rel.get("downloadUrl").startswith(f"http://127.0.0.1:{self.server_port}/api/releases/download/") or
            "/api/releases/download/" in rel.get("downloadUrl"),
            f"downloadUrl {rel.get('downloadUrl')} did not contain expected download path"
        )

    def test_03_download_apk_binary_streaming(self):
        """Verify GET /api/releases/download/ streams binary APK with correct headers."""
        status, headers, data = self._http_get("/api/releases/download/strictrecords_v12.apk")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/vnd.android.package-archive")
        with open(self.dummy_apk_path, "rb") as f:
            self.assertEqual(data, f.read())

        # Non-existent file should 404
        status404, _, _ = self._http_get("/api/releases/download/non_existent.apk")
        self.assertEqual(status404, 404)

    def test_04_toggle_release_active_status(self):
        """Verify deactivating a release removes it from GET /api/releases/latest."""
        # Deactivate
        status, _, body = self._http_post("/api/releases/toggle_active", {
            "admin_pin": "1984",
            "release_id": "rel_android_12",
            "is_active": False
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertFalse(body.get("is_active"))

        # Now latest should return 404
        status, _, data = self._http_get("/api/releases/latest?platform=ANDROID&channel=PRODUCTION")
        self.assertEqual(status, 404)

        # Reactivate
        status, _, body = self._http_post("/api/releases/toggle_active", {
            "admin_pin": "1984",
            "release_id": "rel_android_12",
            "is_active": True
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("is_active"))

        status, _, data = self._http_get("/api/releases/latest?platform=ANDROID&channel=PRODUCTION")
        self.assertEqual(status, 200)

    def test_05_device_update_status_lifecycle(self):
        """Verify device update status transitions and tracking in admin dashboard."""
        dev_id = "test_android_phone_01"
        self._register_test_device(dev_id, "Test Phone", "dev_token_123")

        # 1. DOWNLOADING status
        status, _, body = self._http_post("/api/device/update_status", {
            "device_id": dev_id,
            "release_id": "rel_android_12",
            "installed_version_code": 1,
            "status": "DOWNLOADING",
            "progress_percent": 45
        })
        self.assertEqual(status, 200, f"Failed with: {body}")
        self.assertTrue(body.get("success"))

        rec = self.db.get_device_update_status(dev_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "DOWNLOADING")
        self.assertEqual(rec["download_progress_percent"], 45)

        # 2. INSTALLED status
        status, _, body = self._http_post("/api/device/update_status", {
            "device_id": dev_id,
            "release_id": "rel_android_12",
            "installed_version_code": 12,
            "status": "INSTALLED",
            "progress_percent": 100
        })
        self.assertEqual(status, 200, f"Failed with: {body}")

        # 3. Check /api/releases/list for admin reporting
        status, _, data = self._http_get("/api/releases/list")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertTrue(res.get("success"))
        statuses = res.get("device_statuses", [])
        phone_st = next((s for s in statuses if s["device_id"] == dev_id), None)
        self.assertIsNotNone(phone_st)
        self.assertEqual(phone_st["status"], "INSTALLED")

    def test_06_heartbeat_update_detection(self):
        """Verify device heartbeat detects newer release and flags update_available."""
        dev_id = "test_device_hb"
        self._register_test_device(dev_id, "Heartbeat Tester", "token_hb")

        # Heartbeat with older version 1 (latest is 12)
        status, _, body = self._http_post("/api/device/heartbeat", {
            "device_id": dev_id,
            "device_token": "token_hb",
            "status": "ONLINE",
            "app_version_name": "1.0.0",
            "app_version_code": 1
        })
        self.assertEqual(status, 200, f"Failed with: {body}")
        self.assertTrue(body.get("update_available"))
        self.assertIsNotNone(body.get("latest_release"))
        self.assertEqual(body["latest_release"]["versionCode"], 12)

        # Heartbeat with current version 12
        status, _, body = self._http_post("/api/device/heartbeat", {
            "device_id": dev_id,
            "device_token": "token_hb",
            "status": "ONLINE",
            "app_version_name": "1.2.0",
            "app_version_code": 12
        })
        self.assertEqual(status, 200)
        self.assertFalse(body.get("update_available"))
        self.assertIsNone(body.get("latest_release"))

    def test_07_heartbeat_update_manifest_is_available_for_registered_android(self):
        """A registered phone receives the published Wi-Fi manifest on heartbeat."""
        release = {
            "release_id": "rel_android_heartbeat_13",
            "platform": "ANDROID",
            "version_name": "1.3.0",
            "version_code": 13,
            "min_supported_version": 1,
            "package_name": "com.strictrecords.app",
            "release_notes": "Heartbeat update regression",
            "download_url": "/api/releases/download/strictrecords_v13.apk",
            "file_size_bytes": 128,
            "sha256_checksum": "a" * 64,
            "channel": "PRODUCTION",
        }
        self.db.publish_app_release(release)
        dev_id = "heartbeat_manifest_regression"
        self._register_test_device(dev_id, "Heartbeat Regression Phone", "heartbeat_token_13")

        status, _, body = self._http_post("/api/device/heartbeat", {
            "device_id": dev_id,
            "device_token": "heartbeat_token_13",
            "status": "ONLINE",
            "app_version_name": "1.0.0",
            "app_version_code": 1,
        })

        self.assertEqual(status, 200, f"Heartbeat failed: {body}")
        self.assertTrue(body["update_available"])
        self.assertEqual(body["latest_release"]["versionCode"], 13)
        self.assertIn("/api/releases/download/strictrecords_v13.apk", body["latest_release"]["downloadUrl"])

if __name__ == "__main__":
    unittest.main()
