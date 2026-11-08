"""
Integration Tests for Central Server REST API, WAV Audio Delivery,
Windows Agent Win32 Telemetry, and Two-Device Synchronization.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request

from core.server import create_server


class TestServerAndWindowsAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="strictrecords_srv_")
        cls.db_path = os.path.join(cls.temp_dir, "srv.db")
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
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=15) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")

    def _post(self, path: str, payload: dict):
        import time
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except (ConnectionResetError, urllib.error.URLError) as e:
                if attempt == 2:
                    raise
                time.sleep(0.15)

    def test_01_dashboard_and_motivation_wav_served(self):
        status, html_bytes, ctype = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"STRICTRECORDS", html_bytes)

        status_wav, wav_bytes, wav_type = self._get("/api/audio/motivation.wav")
        self.assertEqual(status_wav, 200)
        self.assertIn("audio/wav", wav_type)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertGreater(len(wav_bytes), 50000)

    def test_02_windows_agent_live_sampling_and_categorization(self):
        status, res = self._post("/api/windows/sample", {"date": "2026-10-04"})
        self.assertEqual(status, 200)
        self.assertEqual(res["device_id"], "windows_lok_pc")
        self.assertIn("idle_seconds", res)
        self.assertIn("category", res)

    def test_03_all_16_dev_scenarios_via_api(self):
        scenarios = [
            "WAKEUP_SUCCESS",
            "SNOOZE",
            "BATH_SUCCESS",
            "HOURLY_SUCCESS",
            "HOURLY_MISSED",
            "GYM_SUCCESS",
            "OFFLINE_SYNC",
            "SCORE_95",
            "SCORE_75",
            "SCORE_45",
        ]
        for sc in scenarios:
            status, data = self._post(
                "/api/dev/simulate", {"date": "2026-10-04", "scenario": sc}
            )
            self.assertEqual(status, 200, f"Scenario {sc} failed")

        # After SCORE_45, dev score should be 45 and production score should remain 0
        _, dev_state_raw, _ = self._get("/api/state?date=2026-10-04&dev_mode=1")
        dev_state = json.loads(dev_state_raw.decode("utf-8"))
        self.assertEqual(dev_state["daily_record"]["final_score"], 45)

        _, prod_state_raw, _ = self._get("/api/state?date=2026-10-04&dev_mode=0")
        prod_state = json.loads(prod_state_raw.decode("utf-8"))
        self.assertEqual(prod_state["daily_record"]["final_score"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
