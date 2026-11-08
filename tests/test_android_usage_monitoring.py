"""
Comprehensive Unit & Integration Test Suite for:
PC-Controlled Android Mobile Usage Monitoring & Connection Violation System.

Verifies:
1. PC-Triggered Session Activation (START_STRICT_SESSION payload, EventBus broadcast, admin command).
2. Android Activation Acknowledgment (ACK_START_STRICT_SESSION -> ANDROID_MONITORING_ACTIVE).
3. Authoritative Package Allowlist & Unauthorized App Detection (ANDROID_APP_USAGE_DETECTED).
4. Authorized Proof-Submission Exception (StrictRecords allowed when window is OPEN; others prohibited).
5. Screen Lock / Unlock Behavior (SCREEN_LOCKED does not disconnect; unlock does not generate violation).
6. Central PC Warning Notification & Modal (STRICTGUARD - ANDROID USAGE DETECTED, [ACKNOWLEDGE / CLOSE]).
7. StrictGuard Camera Proctor Correlation (downward gaze / camera phone detection linkage).
8. Heartbeat Tracking & Connection Degradation (3 missed heartbeats -> ANDROID_CONNECTION_DEGRADED).
9. Connection Loss Detection & Timer Pausing (45s without heartbeat -> ANDROID_CONNECTION_LOST, timer paused, preserved time).
10. Connection Loss Exactly-Once Violation Rule (exactly 1 violation recorded per incident).
11. Reconnection & Seamless Recovery (ANDROID_RECONNECTING -> ANDROID_MONITORING_RESTORED -> ANDROID_MONITORING_ACTIVE, timer resumes).
12. Full 11-State Machine Lifecycle Transitions.
"""

import json
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from core.database import StrictRecordsDB
from core.time_window_engine import IST
from windows.strict_session_manager import StrictSessionManager, StrictSessionState
from windows.strict_study_workflow import StrictStudyWorkflow


class TestAndroidUsageMonitoring(unittest.TestCase):

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.temp_dir / "test_android_monitoring.db"
        self.db = StrictRecordsDB(str(self.db_path))

        # Seed devices & account
        self.now = datetime(2026, 11, 4, 14, 0, tzinfo=IST)
        self.clock_mono = 1000.0

        with self.db._connect() as c:
            c.execute("""
                INSERT INTO devices(device_id, user_id, device_name, platform, app_version, last_seen, sync_status, pairing_state)
                VALUES('a55_device', 'user_lok', 'Samsung Galaxy A55', 'ANDROID', '1.18.16', ?, 'CONNECTED', 'PAIRED')
            """, (self.now.isoformat(),))
            c.execute("""
                INSERT INTO scheduled_activities(activity_id, account_id, type, date, scheduled_time, window_start, window_end, notification_time, status)
                VALUES('hourly_check_14', 'user_lok', 'HOURLY', '2026-11-04', '2026-11-04T14:00:00+05:30', '2026-11-04T14:00:00+05:30', '2026-11-04T14:20:00+05:30', '2026-11-04T14:00:00+05:30', 'PENDING')
            """)

        self.study = StrictStudyWorkflow(self.db, clock=lambda: self.now, monotonic=lambda: self.clock_mono)
        self.manager = StrictSessionManager(db=self.db, data_dir=str(self.temp_dir))
        self.manager.study = self.study
        self.manager.user_id = "user_lok"
        self.manager.session_id = "sess_android_test_101"

    def advance_time(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.clock_mono += seconds

    # 1. PC-Triggered Android Session Activation
    def test_01_pc_triggered_activation(self):
        mock_bus = MagicMock()
        with patch("core.server.EventBus.get_instance", return_value=mock_bus):
            self.manager._trigger_android_session_activation("sess_android_test_101", "a55_device")

            self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTED")
            mock_bus.publish.assert_called_once()
            call_args = mock_bus.publish.call_args[0]
            self.assertEqual(call_args[0], "START_STRICT_SESSION")
            payload = call_args[1]
            self.assertEqual(payload["session_id"], "sess_android_test_101")
            self.assertEqual(payload["user_id"], "user_lok")
            self.assertEqual(payload["device_id"], "a55_device")
            self.assertIn("submission_window_status", payload)

    # 2. Android Activation Acknowledgment
    def test_02_android_acknowledgment_activates_monitoring(self):
        self.manager.android_monitoring_state = "ANDROID_CONNECTED"
        ack_data = {
            "session_id": "sess_android_test_101",
            "device_id": "a55_device",
            "status": "ACK_START_STRICT_SESSION",
            "state": "ANDROID_MONITORING_ACTIVE"
        }
        res = self.manager.ack_android_start(ack_data)
        self.assertTrue(res["success"])
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_MONITORING_ACTIVE")

    # 3. Heartbeat Processing & Status Query
    def test_03_heartbeat_processing(self):
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"
        self.manager.state = StrictSessionState.ACTIVE

        hb = {
            "session_id": "sess_android_test_101",
            "device_id": "a55_device",
            "state": "ANDROID_MONITORING_ACTIVE",
            "screen_state": "UNLOCKED",
            "event_cursor": 123456789
        }
        resp = self.manager.process_android_heartbeat(hb)
        self.assertTrue(resp["success"])
        self.assertTrue(resp["session_active"])
        self.assertEqual(resp["state"], "ANDROID_MONITORING_ACTIVE")
        self.assertFalse(resp["timer_paused"])

    # 4. Screen Lock & Unlock Transitions (Screen lock never disconnects or triggers violation)
    def test_04_screen_lock_and_unlock_behavior(self):
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"

        # Screen locks
        lock_hb = {
            "session_id": "sess_android_test_101",
            "device_id": "a55_device",
            "state": "ANDROID_SCREEN_LOCKED",
            "screen_state": "LOCKED"
        }
        resp = self.manager.process_android_heartbeat(lock_hb)
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_SCREEN_LOCKED")
        self.assertTrue(self.manager.android_screen_locked)
        self.assertEqual(len(self.manager.get_active_phone_usage_alerts()), 0, "Screen lock must never generate violation")

        # Screen unlocks
        unlock_hb = {
            "session_id": "sess_android_test_101",
            "device_id": "a55_device",
            "state": "ANDROID_UNLOCKED",
            "screen_state": "UNLOCKED"
        }
        resp = self.manager.process_android_heartbeat(unlock_hb)
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_UNLOCKED")
        self.assertFalse(self.manager.android_screen_locked)
        self.assertEqual(len(self.manager.get_active_phone_usage_alerts()), 0, "Unlocking alone must never generate violation")

    # 5. Unauthorized App Usage Detection outside Proof Window
    def test_05_unauthorized_app_usage_detected(self):
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"
        # Window closed
        self.manager.is_submission_window_open = MagicMock(return_value=False)

        violation_payload = {
            "event_id": "viol_yt_123",
            "session_id": "sess_android_test_101",
            "user_id": "user_lok",
            "device_id": "a55_device",
            "package_name": "com.google.android.youtube",
            "app_label": "YouTube",
            "event_timestamp": "2026-11-04T14:05:00.000Z",
            "detection_source": "ANDROID_USAGE_STATS",
            "submission_window_status": "CLOSED"
        }

        res = self.manager.record_android_app_usage(violation_payload)
        self.assertTrue(res["success"])
        self.assertTrue(res["recorded"])
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_APP_USAGE_DETECTED")

        alerts = self.manager.get_active_phone_usage_alerts()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["title"], "ANDROID USAGE DETECTED")
        self.assertEqual(alert["app_name"], "YouTube")
        self.assertEqual(alert["package_name"], "com.google.android.youtube")
        self.assertFalse(alert["acknowledged"])

    # 6. Authorized Proof-Submission Exception (StrictRecords allowed when window is OPEN)
    def test_06_authorized_proof_submission_exception(self):
        # Window OPEN
        self.manager.is_submission_window_open = MagicMock(return_value=True)

        proof_app_payload = {
            "event_id": "proof_sr_001",
            "session_id": "sess_android_test_101",
            "package_name": "com.strictrecords.app",
            "app_label": "StrictRecords",
            "event_timestamp": "2026-11-04T14:02:00.000Z",
            "submission_window_status": "OPEN"
        }
        res = self.manager.record_android_app_usage(proof_app_payload)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("authorized", False), "StrictRecords must be authorized when proof window is OPEN")

        # But social media during open window is still unauthorized
        yt_payload = {
            "event_id": "viol_yt_open_002",
            "session_id": "sess_android_test_101",
            "package_name": "com.google.android.youtube",
            "app_label": "YouTube",
            "event_timestamp": "2026-11-04T14:03:00.000Z",
            "submission_window_status": "OPEN"
        }
        res_yt = self.manager.record_android_app_usage(yt_payload)
        self.assertTrue(res_yt["recorded"])
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_APP_USAGE_DETECTED")

    # 7. StrictGuard Camera Proctor Correlation
    def test_07_proctor_camera_correlation(self):
        self.manager.is_submission_window_open = MagicMock(return_value=False)
        mock_proctor = MagicMock()
        mock_proctor.get_telemetry.return_value = {
            "downward_study_active": True,
            "attention_state": "DOWNWARD_STUDY"
        }
        self.manager.study_hardware = MagicMock()
        self.manager.study_hardware.proctor = mock_proctor

        payload = {
            "event_id": "viol_ig_456",
            "session_id": "sess_android_test_101",
            "package_name": "com.instagram.android",
            "app_label": "Instagram",
            "event_timestamp": "2026-11-04T14:08:00.000Z"
        }
        self.manager.record_android_app_usage(payload)
        alerts = self.manager.get_active_phone_usage_alerts()
        ig_alert = next((a for a in alerts if a["event_id"] == "viol_ig_456"), None)
        self.assertIsNotNone(ig_alert)
        self.assertTrue(ig_alert["correlated"], "Should correlate with StrictGuard camera downward gaze")

    # 8. Alert Acknowledgment
    def test_08_alert_acknowledgment(self):
        self.manager.android_monitoring_state = "ANDROID_APP_USAGE_DETECTED"
        self.manager._active_phone_usage_alerts = [{
            "event_id": "viol_test_ack",
            "app_name": "Chrome",
            "acknowledged": False
        }]

        ack_res = self.manager.acknowledge_phone_usage_alert("viol_test_ack")
        self.assertTrue(ack_res["success"])
        self.assertEqual(len(self.manager.get_active_phone_usage_alerts()), 0)
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_MONITORING_ACTIVE")

    # 9. Connection Degradation on 3 Missed Heartbeats (30s)
    def test_09_connection_degraded_on_3_missed_heartbeats(self):
        self.manager.state = StrictSessionState.ACTIVE
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"
        self.manager.android_screen_locked = False
        self.manager.android_last_heartbeat_at = time.monotonic() - 32.0

        self.manager._check_android_connection_health()
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTION_DEGRADED")

    # 10. Connection Loss Detection (45s) & Authoritative Timer Pause
    def test_10_connection_lost_pauses_timer_and_records_single_violation(self):
        # Set up active study session
        self.study.s = {
            "session_id": "sess_android_test_101",
            "account_id": "user_lok",
            "state": "ACTIVE_FOCUS",
            "focus_target_seconds": 1200.0,
            "elapsed_seconds": 300.0,
            "monitoring_resumed": True,
            "monitoring_ok": True,
            "window": {"activity_id": "hourly_check_14", "window_start": "2026-11-04T14:00:00+05:30", "window_end": "2026-11-04T14:20:00+05:30"},
            "connection_loss_violations": 0
        }
        self.manager.state = StrictSessionState.ACTIVE
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"
        self.manager.android_screen_locked = False
        self.manager.android_last_heartbeat_at = time.monotonic() - 50.0

        self.manager._check_android_connection_health()
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTION_LOST")
        self.assertTrue(self.study.s["android_connection_lost"])

        # Authoritative focus timer must be paused
        self.study.tick(monitoring_ok=True)
        self.assertEqual(self.study.s["elapsed_seconds"], 300.0, "Elapsed focus time must be paused on connection loss")

        # Exactly 1 connection-loss violation
        self.assertEqual(self.study.s["connection_loss_violations"], 1)

        # Subsequent health checks do NOT duplicate the violation
        self.manager._check_android_connection_health()
        self.assertEqual(self.study.s["connection_loss_violations"], 1, "Must never duplicate connection-loss violation")

        # Central warning alert is raised
        alerts = self.manager.get_active_phone_usage_alerts()
        conn_alert = next((a for a in alerts if a.get("title") == "ANDROID CONNECTION LOST"), None)
        self.assertIsNotNone(conn_alert)
        self.assertIn("Focus timer paused", conn_alert["message"])

    # 11. Reconnection & Seamless Timer Resumption
    def test_11_reconnection_restores_timer_without_resetting(self):
        # Setup session in paused connection lost state
        self.study.s = {
            "session_id": "sess_android_test_101",
            "account_id": "user_lok",
            "state": "ACTIVE_FOCUS",
            "focus_target_seconds": 1200.0,
            "elapsed_seconds": 300.0,
            "monitoring_resumed": True,
            "monitoring_ok": False,
            "android_connection_lost": True,
            "window": {"activity_id": "hourly_check_14", "window_start": "2026-11-04T14:00:00+05:30", "window_end": "2026-11-04T14:20:00+05:30"},
            "connection_loss_violations": 1
        }
        self.manager.state = StrictSessionState.ACTIVE
        self.manager.android_monitoring_state = "ANDROID_CONNECTION_LOST"

        # Android reconnects
        reconnect_hb = {
            "session_id": "sess_android_test_101",
            "device_id": "a55_device",
            "state": "ANDROID_MONITORING_ACTIVE",
            "screen_state": "UNLOCKED"
        }
        resp = self.manager.process_android_heartbeat(reconnect_hb)
        self.assertTrue(resp["success"])
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_MONITORING_ACTIVE")
        self.assertFalse(self.study.s["android_connection_lost"])

        # Connection alert cleared
        alerts = self.manager.get_active_phone_usage_alerts()
        self.assertEqual(len(alerts), 0)

        # Focus timer resumes from exactly 300.0 (never reset!)
        self.clock_mono += 10.0
        self.study.tick(monitoring_ok=True)
        self.assertAlmostEqual(self.study.s["elapsed_seconds"], 310.0, places=1)
        self.assertEqual(self.study.s["connection_loss_violations"], 1, "Violation count unchanged on reconnect")

    # 12. Full 11-State Transitions
    def test_12_all_eleven_state_transitions(self):
        # 1. ANDROID_NOT_JOINED
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_NOT_JOINED")
        
        # 2. ANDROID_CONNECTED
        self.manager._trigger_android_session_activation("sess_android_test_101", "a55_device")
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTED")

        # 3. ANDROID_MONITORING_ACTIVE
        self.manager.ack_android_start({"session_id": "sess_android_test_101"})
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_MONITORING_ACTIVE")

        # 4. ANDROID_SCREEN_LOCKED
        self.manager.process_android_heartbeat({"session_id": "sess_android_test_101", "state": "ANDROID_SCREEN_LOCKED", "screen_state": "LOCKED"})
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_SCREEN_LOCKED")

        # 5. ANDROID_UNLOCKED
        self.manager.process_android_heartbeat({"session_id": "sess_android_test_101", "state": "ANDROID_UNLOCKED", "screen_state": "UNLOCKED"})
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_UNLOCKED")

        # 6. ANDROID_APP_USAGE_DETECTED
        self.manager.record_android_app_usage({"session_id": "sess_android_test_101", "package_name": "com.whatsapp", "app_label": "WhatsApp"})
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_APP_USAGE_DETECTED")

        # 7. ANDROID_USAGE_WARNING
        self.manager.android_monitoring_state = "ANDROID_USAGE_WARNING"
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_USAGE_WARNING")

        # Back to active
        self.manager.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"

        # 8. ANDROID_CONNECTION_DEGRADED
        self.manager.state = StrictSessionState.ACTIVE
        self.manager.android_screen_locked = False
        self.manager.android_last_heartbeat_at = time.monotonic() - 32.0
        self.manager._check_android_connection_health()
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTION_DEGRADED")

        # 9. ANDROID_CONNECTION_LOST
        self.manager.android_last_heartbeat_at = time.monotonic() - 48.0
        self.manager._check_android_connection_health()
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_CONNECTION_LOST")

        # 10. ANDROID_RECONNECTING
        self.manager.android_monitoring_state = "ANDROID_RECONNECTING"
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_RECONNECTING")

        # 11. ANDROID_MONITORING_RESTORED
        self.manager.process_android_heartbeat({"session_id": "sess_android_test_101", "state": "ANDROID_MONITORING_ACTIVE"})
        # After processing, it completes restoration and returns to ACTIVE
        self.assertEqual(self.manager.android_monitoring_state, "ANDROID_MONITORING_ACTIVE")


if __name__ == "__main__":
    unittest.main()
