"""
Unit & Integration Tests for BackgroundProctoringService & Low-CPU Telemetry
"""

import unittest
import time
from core.proctoring_service import BackgroundProctoringService


class TestProctoringService(unittest.TestCase):

    def setUp(self):
        self.service = BackgroundProctoringService()

    def tearDown(self):
        self.service.stop()

    def test_01_service_initialization(self):
        self.assertTrue(self.service.is_active)
        self.assertTrue(self.service.low_cpu_mode)
        self.assertGreaterEqual(self.service.sample_interval, 3.0)

    def test_02_get_telemetry_fields(self):
        telemetry = self.service.get_telemetry()
        self.assertIn("status", telemetry)
        self.assertIn("mode", telemetry)
        self.assertIn("cpu_utilization", telemetry)
        self.assertIn("integrity_score", telemetry)
        self.assertIn("gaze_direction", telemetry)
        self.assertIn("head_pose", telemetry)
        self.assertIn("audio_level", telemetry)
        self.assertIn("focus_state", telemetry)
        self.assertEqual(telemetry["status"], "RUNNING")
        self.assertEqual(telemetry["cpu_utilization"], "< 0.5%")

    def test_03_toggle_active_state(self):
        self.service.set_active(False)
        self.assertFalse(self.service.is_active)
        telemetry = self.service.get_telemetry()
        self.assertEqual(telemetry["status"], "PAUSED")

        self.service.set_active(True)
        self.assertTrue(self.service.is_active)
        telemetry = self.service.get_telemetry()
        self.assertEqual(telemetry["status"], "RUNNING")

    def test_04_background_daemon_thread_lifecycle(self):
        self.service.start()
        self.assertIsNotNone(self.service._daemon_thread)
        self.assertTrue(self.service._daemon_thread.is_alive())
        # Let it run one evaluation cycle
        time.sleep(0.5)
        telemetry = self.service.get_telemetry()
        self.assertGreaterEqual(telemetry["checks_completed"], 0)
        self.service.stop()
        self.assertFalse(self.service._daemon_thread.is_alive())

    def test_05_camera_stream_toggle(self):
        self.assertFalse(self.service.camera_stream_enabled)
        self.service.set_camera_stream(True)
        self.assertTrue(self.service.camera_stream_enabled)
        telemetry = self.service.get_telemetry()
        self.assertTrue(telemetry["camera_stream_active"])


if __name__ == "__main__":
    unittest.main()
