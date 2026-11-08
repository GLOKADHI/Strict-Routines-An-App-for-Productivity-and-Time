"""
Unit and integration tests for PC Shutdown endpoints in core/server.py:
- GET /api/pc/shutdown_status
- POST /api/pc/shutdown_event
- POST /api/pc/shutdown_attendance
- POST /api/pc/shutdown_photo
- GET /api/admin/pc_shutdown_reviews
- POST /api/admin/pc_shutdown_review
"""

import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from PIL import Image
import io

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler


def create_jpeg_base64() -> str:
    buf = io.BytesIO()
    img = Image.new("RGB", (200, 200), color=(10, 10, 10))
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class TestServerPcShutdownRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, "test_server.db")
        cls.db = StrictRecordsDB(cls.db_path)
        cls.engine = ScoringEngine(cls.db)

        # Configure handler class
        class CustomHandler(StrictRecordsRequestHandler):
            db = cls.db
            engine = cls.engine

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CustomHandler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _request(self, method: str, path: str, data: dict = None, headers: dict = None):
        url = f"{self.base_url}{path}"
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                resp_body = resp.read().decode("utf-8")
                return resp.status, json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8")
            return e.code, json.loads(resp_body) if resp_body else {}

    def test_01_get_shutdown_status_initial(self):
        status, data = self._request("GET", "/api/pc/shutdown_status?date=2026-10-12")
        self.assertEqual(status, 200)
        self.assertFalse(data["shutdown_recorded"])
        self.assertEqual(data["bonus_status"], "AWAITING_WINDOWS_SHUTDOWN")
        self.assertEqual(data["target_reward_date"], "2026-10-13")

    def test_02_post_shutdown_event_qualifying(self):
        payload = {
            "event_id": "srv_sd_001",
            "shutdown_timestamp": "2026-10-12T23:30:00+05:30",
            "device_id": "windows_lok_pc",
            "date": "2026-10-12",
            "evidence_source": "WIN32_API",
        }
        status, data = self._request("POST", "/api/pc/shutdown_event", data=payload)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["is_qualifying"])
        self.assertEqual(data["target_reward_date"], "2026-10-13")

    def test_03_post_shutdown_attendance(self):
        payload = {
            "date": "2026-10-12",
            "phone_device_id": "android_lok_phone",
            "shutdown_event_id": "srv_sd_001",
        }
        status, data = self._request("POST", "/api/pc/shutdown_attendance", data=payload)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["bonus_status"], "PHOTO_REQUIRED")

    def test_04_post_shutdown_photo(self):
        b64 = create_jpeg_base64()
        payload = {
            "date": "2026-10-12",
            "phone_device_id": "android_lok_phone",
            "shutdown_event_id": "srv_sd_001",
            "image_base64": b64,
        }
        status, data = self._request("POST", "/api/pc/shutdown_photo", data=payload)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["success"])
        self.assertIn("evidence_id", data)

    def test_05_admin_reviews_and_approval(self):
        # 1. Rotate initial PIN and authenticate admin session
        self.db.change_admin_pin("1234", "9876", admin_user="admin")
        auth = self.db.authenticate_admin("admin", "9876")
        token = auth["token"]
        headers = {"X-Admin-Session": token}

        # 2. Check reviews list
        status, data = self._request("GET", "/api/admin/pc_shutdown_reviews", headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        reviews = data["reviews"]
        self.assertTrue(len(reviews) >= 1)

        # 3. Approve via admin review endpoint
        payload = {
            "event_id": "srv_sd_001",
            "decision": "APPROVE",
            "reason": "Display dark; verified workstation shutdown",
            "admin_id": "Administrator",
        }
        status, res = self._request("POST", "/api/admin/pc_shutdown_review", data=payload, headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(res["success"])
        self.assertEqual(res["bonus_status"], "BONUS_CREDITED")
        self.assertEqual(res["target_reward_date"], "2026-10-13")


if __name__ == "__main__":
    unittest.main()
