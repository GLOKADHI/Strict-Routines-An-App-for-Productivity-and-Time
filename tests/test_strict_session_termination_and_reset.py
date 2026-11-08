"""
STRICTRECORDS – AUTOMATED TEST SUITE: STRICT SESSION TERMINATION, RESET & RECOVERY
Verifies Section 6 requirements (Tests A through L):
- Test A: Ending an active healthy session restores normal mode cleanly.
- Test B: Resetting a session stuck on WebRTC NEGOTIATING cleans up peer connections and returns to normal.
- Test C: Resetting during an iOS camera interruption clears the alert and watchdog timers.
- Test D: Ending while Android is disconnected records a pending stop command that applies on reconnect.
- Test E: Ending while Cloudflare Tunnel is down still restores Windows locally.
- Test F: Resetting while Windows timer is paused clears the pause and restores normal mode.
- Test G: Ending during active proof submission window preserves verified evidence and points.
- Test H: Resetting while Android reports pending proof does not create duplicate transactions.
- Test I: Restarting session after reset generates a new session ID and does not inherit stale camera state.
- Test J: Calling End Session multiple times is idempotent and safe.
- Test K: Windows application restart after End Session does not resume the ended session.
- Test L: Historical points, audit logs, and approved evidence remain intact after Reset.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.database import StrictRecordsDB
from core.models import StrictAuditAction
from core.strict_session_lifecycle import StrictLifecycleState
from core.time_window_engine import now_ist
from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor, IPadCameraState
from windows.strict_session_manager import (
    StrictSessionManager,
    StrictSessionPhase,
    StrictSessionState,
)
from windows.strict_study_workflow import StrictStudyWorkflow


class TestStrictSessionTerminationAndReset(unittest.TestCase):
    def setUp(self):
        IPadCameraStreamMonitor.reset_instance()
        # Mock OS-level system actions to protect development environment
        for name in (
            '_apply_registry_policies',
            '_restore_registry_policies',
            '_install_keyboard_hook',
            '_uninstall_keyboard_hook',
            '_hide_taskbar',
            '_show_taskbar',
            '_start_supervisor',
        ):
            p = patch.object(StrictSessionManager, name, return_value=None)
            p.start()
            self.addCleanup(p.stop)

        for target, value in [
            ('windows.network_policy_manager.HAS_WINREG', False),
            (
                'windows.strict_session_manager.StrictStudyHardware',
                lambda: SimpleNamespace(
                    start=lambda **_: {'success': True},
                    stop=lambda: None,
                    view=lambda: dict(monitoring_ok=True, camera='ACTIVE', bluetooth_connected=True),
                ),
            ),
            ('core.youtube_reward_cloud.client', lambda _: SimpleNamespace(configuration=lambda: None)),
            (
                'windows.browser_policy_manager.BrowserPolicyManager.launch_approved_browser',
                lambda *_, **__: {'success': True},
            ),
        ]:
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

        self.temp_dir = tempfile.mkdtemp(prefix="strict_term_test_")
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)

        now = now_ist()
        start = now + timedelta(minutes=35)
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status,pairing_state)
                VALUES('android_lok_phone','user_lok','Fixture','ANDROID','test',?,'CONNECTED','PAIRED')""",
                (now.isoformat(),),
            )
            conn.execute(
                """INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,
                window_end,notification_time,status) VALUES('unit-hourly','user_lok','HOURLY',?,?,?,?,?,'PENDING')""",
                (
                    start.date().isoformat(),
                    start.isoformat(),
                    start.isoformat(),
                    (start + timedelta(minutes=20)).isoformat(),
                    start.isoformat(),
                ),
            )

        self.mgr = StrictSessionManager(db=self.db, data_dir=self.temp_dir)
        self.mgr.user_id = "user_lok"
        self.mgr.android_device_id = "android_lok_phone"

    def tearDown(self):
        IPadCameraStreamMonitor.reset_instance()
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _start_mock_active_session(self, session_id="test_sess_001"):
        self.mgr.state = StrictSessionState.ACTIVE
        self.mgr.session_id = session_id
        self.mgr.set_lifecycle_state(StrictLifecycleState.SESSION_ACTIVE, session_id=session_id)
        # Setup active study workflow
        wf = StrictStudyWorkflow(self.db)
        wf.s = {
            "session_id": session_id,
            "account_id": "user_lok",
            "state": "ACTIVE_FOCUS",
            "focus_target_seconds": 1800.0,
            "initial_focus_target_seconds": 1800.0,
            "elapsed_seconds": 300.0,
            "monitoring_resumed": True,
            "sync_verified": False,
            "ios_required": True,
            "flags": [],
            "window": {
                "activity_id": "unit-hourly",
                "schedule_version": 1,
                "window_start": "2026-11-06T12:00:00",
                "window_end": "2026-11-06T13:00:00",
            },
        }
        wf._ipad_status = lambda: {"connected": True, "reason": "Connected"}
        self.mgr.study = wf
        return wf

    # =========================================================================
    # Test A: Ending an active healthy session restores normal mode cleanly
    # =========================================================================
    def test_a_end_active_healthy_session(self):
        self._start_mock_active_session("sess_healthy_001")
        self.assertEqual(self.mgr.state, StrictSessionState.ACTIVE)
        self.assertEqual(self.mgr.lifecycle_state, StrictLifecycleState.SESSION_ACTIVE)

        result = self.mgr.user_end_session(reason="User finished session early")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)
        self.assertEqual(self.mgr.lifecycle_state, StrictLifecycleState.IDLE)
        self.assertIsNone(self.mgr.session_id)
        self.assertFalse(self.mgr.lock_screen_active)

    # =========================================================================
    # Test B: Resetting a session stuck on WebRTC NEGOTIATING cleans up peer connections and returns to normal
    # =========================================================================
    def test_b_reset_session_stuck_negotiating(self):
        self._start_mock_active_session("sess_stuck_negotiating_002")
        cam_mon = IPadCameraStreamMonitor.get_instance(self.db)
        cam_mon.start_session_monitoring("sess_stuck_negotiating_002")
        cam_mon.record_detailed_stage("7_sdp_offer_generation", "sess_stuck_negotiating_002", success=True)
        cam_mon.record_connection_status(device_connected=True, https_verified=True, webrtc_state="CONNECTING")

        result = self.mgr.user_reset_session(reason="Stuck on WebRTC negotiation")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)
        self.assertEqual(self.mgr.lifecycle_state, StrictLifecycleState.IDLE)
        self.assertIsNone(self.mgr.session_id)
        # Cam monitor state cleared
        self.assertFalse(cam_mon.session_id)

    # =========================================================================
    # Test C: Resetting during an iOS camera interruption clears alert and watchdog
    # =========================================================================
    def test_c_reset_during_camera_interruption(self):
        self._start_mock_active_session("sess_interrupted_003")
        cam_mon = IPadCameraStreamMonitor.get_instance(self.db)
        start_mono = 1000.0
        cam_mon.session_started_mono = start_mono
        cam_mon.start_session_monitoring("sess_interrupted_003")
        cam_mon.session_started_mono = start_mono

        # Evaluate at 20 seconds without frames (> 15s interruption threshold)
        diag_before = cam_mon.evaluate_health(now_mono=start_mono + 20.0)
        self.assertTrue(diag_before["is_interrupted"])
        self.assertTrue(diag_before["is_timer_paused"])

        result = self.mgr.user_reset_session(reason="Camera failed and unrecoverable")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)

        diag_after = cam_mon.evaluate_health(now_mono=start_mono + 25.0)
        self.assertFalse(diag_after["is_interrupted"])
        self.assertFalse(diag_after["is_timer_paused"])
        self.assertFalse(diag_after["is_session_flagged"])
        self.assertIsNone(diag_after["active_incident"])

    # =========================================================================
    # Test D: Ending while Android is disconnected records pending stop command
    # =========================================================================
    def test_d_end_session_android_disconnected_records_pending_stop(self):
        self._start_mock_active_session("sess_android_offline_004")
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE devices SET sync_status='OFFLINE' WHERE device_id='android_lok_phone'"
            )
            # Create a stale START_STRICT_SESSION command
            conn.execute(
                """INSERT INTO device_commands(command_id,device_id,command_type,status,payload_json,nonce,admin_user,created_at,expires_at)
                VALUES('cmd_stale_start','android_lok_phone','START_STRICT_SESSION','PENDING','{}','n1','admin',datetime('now'),datetime('now','+1 hour'))"""
            )

        result = self.mgr.user_end_session(reason="Android offline exit")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)

        # Verify stale START_STRICT_SESSION is cancelled
        with self.db._connect() as conn:
            stale = conn.execute(
                "SELECT status FROM device_commands WHERE command_id='cmd_stale_start'"
            ).fetchone()
            self.assertEqual(stale["status"], "CANCELLED")

            # Verify pending STOP_STRICT_SESSION exists for Android
            pending_stop = conn.execute(
                "SELECT * FROM device_commands WHERE device_id='android_lok_phone' AND command_type='STOP_STRICT_SESSION'"
            ).fetchall()
            self.assertGreater(len(pending_stop), 0)

    # =========================================================================
    # Test E: Ending while Cloudflare Tunnel is down still restores Windows locally
    # =========================================================================
    def test_e_end_session_cloudflare_down_restores_windows(self):
        self._start_mock_active_session("sess_cf_down_005")
        # Simulate broken Cloudflare tunnel
        self.mgr.ios_camera_gateway = SimpleNamespace(stop=MagicMock(side_effect=RuntimeError("Cloudflare unreachable")))

        result = self.mgr.user_end_session(reason="Cloudflare tunnel down")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)
        self.assertIsNone(self.mgr.session_id)

    # =========================================================================
    # Test F: Resetting while Windows timer is paused clears pause & restores normal
    # =========================================================================
    def test_f_reset_while_timer_paused(self):
        wf = self._start_mock_active_session("sess_paused_006")
        wf.s["ipad_camera_paused"] = True
        wf.s["mobile_integrity_paused"] = True

        result = self.mgr.user_reset_session(reason="Timer paused session reset")
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)
        self.assertEqual(self.mgr.lifecycle_state, StrictLifecycleState.IDLE)
        self.assertIsNone(self.mgr.study.s)

    # =========================================================================
    # Test G: Ending during active proof submission window preserves verified evidence
    # =========================================================================
    def test_g_end_during_proof_submission_preserves_evidence(self):
        wf = self._start_mock_active_session("sess_proof_007")
        wf.s["state"] = "END_SESSION_ENABLED"
        wf.s["sync_verified"] = True
        wf.s["monitoring_resumed"] = True
        wf.s["submission_id"] = "sub_verified_777"
        wf.s["ios_required"] = False
        wf.s["elapsed_seconds"] = 1800.0
        wf._ipad_status = lambda: {"connected": True, "reason": "Connected"}

        # Record verified review in DB
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO proof_reviews(review_id,evidence_id,user_id,task_code,date,hour_slot,status,submitted_at)
                VALUES('rev_777','ev_777','user_lok','unit-hourly','2026-11-06','12:00','APPROVED',datetime('now'))"""
            )

        result = self.mgr.user_end_session(reason="Normal manual end with proof")
        self.assertTrue(result["success"])
        self.assertTrue(result.get("completed", False))

        # Check review still exists in DB
        with self.db._connect() as conn:
            row = conn.execute("SELECT status FROM proof_reviews WHERE review_id='rev_777'").fetchone()
            self.assertEqual(row["status"], "APPROVED")

    # =========================================================================
    # Test H: Resetting while Android reports pending proof does not duplicate transactions
    # =========================================================================
    def test_h_reset_with_pending_proof_no_duplicates(self):
        wf = self._start_mock_active_session("sess_pending_proof_008")
        wf.s["state"] = "AWAITING_ANDROID_SUBMISSION"
        wf.s["sync_verified"] = False

        initial_count = 0
        with self.db._connect() as conn:
            initial_count = conn.execute("SELECT COUNT(*) FROM score_events").fetchone()[0]

        result = self.mgr.user_reset_session(reason="Reset before proof arrived")
        self.assertTrue(result["success"])

        with self.db._connect() as conn:
            final_count = conn.execute("SELECT COUNT(*) FROM score_events").fetchone()[0]
            self.assertEqual(initial_count, final_count)

    # =========================================================================
    # Test I: Restarting session after reset generates a new session ID
    # =========================================================================
    def test_i_restart_session_generates_new_id_and_clean_state(self):
        self._start_mock_active_session("sess_old_009")
        cam_mon = IPadCameraStreamMonitor.get_instance(self.db)
        cam_mon.start_session_monitoring("sess_old_009")
        cam_mon.last_valid_frame_mono = 12345.6
        cam_mon.elapsed_without_frames = 18.0

        restart_result = self.mgr.user_restart_session(reason="Restart test")
        self.assertTrue(restart_result["success"])
        self.assertEqual(restart_result["lifecycle_state"], StrictLifecycleState.PREREQUISITE_CHECK.value)
        self.assertTrue(restart_result["preparation_id"].startswith("prep_"))
        self.assertNotEqual(restart_result["preparation_id"], "sess_old_009")

        # Stale camera timestamps should be cleared
        self.assertIsNone(cam_mon.last_valid_frame_mono)
        self.assertEqual(cam_mon.elapsed_without_frames, 0.0)

    # =========================================================================
    # Test J: Calling End Session multiple times is idempotent and safe
    # =========================================================================
    def test_j_end_session_idempotent(self):
        self._start_mock_active_session("sess_idempotent_010")

        res1 = self.mgr.user_end_session(reason="First call")
        self.assertTrue(res1["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)

        # Call again
        res2 = self.mgr.user_end_session(reason="Second call")
        self.assertTrue(res2["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)

        # Call third time
        res3 = self.mgr.user_end_session(reason="Third call")
        self.assertTrue(res3["success"])
        self.assertEqual(self.mgr.state, StrictSessionState.NORMAL)

    # =========================================================================
    # Test K: App restart after End Session does not resume ended session
    # =========================================================================
    def test_k_app_restart_after_end_session_no_auto_resume(self):
        self._start_mock_active_session("sess_to_end_011")
        self.mgr.user_end_session(reason="Ended cleanly")

        # Create a fresh manager instance reading the same DB
        new_mgr = StrictSessionManager(db=self.db, data_dir=self.temp_dir)
        self.assertEqual(new_mgr.state, StrictSessionState.NORMAL)
        self.assertIsNone(new_mgr.session_id)
        report = getattr(new_mgr, "last_recovery_report", {})
        self.assertFalse(report.get("recovery_needed", False))

    # =========================================================================
    # Test L: Historical points, audit logs, and approved evidence remain intact after Reset
    # =========================================================================
    def test_l_history_points_audit_evidence_intact_after_reset(self):
        now_ts = datetime.now().isoformat()
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO admin_audit_logs(audit_id,admin_user,action_type,field_changed,timestamp)
                VALUES('audit_1','admin','TEST_ACTION','field_1',?)""",
                (now_ts,),
            )
            conn.execute(
                """INSERT INTO score_events(event_id,date,timestamp,event_type,device_id,description,points)
                VALUES('score_1','2026-11-06',?,'BONUS','android_lok_phone','Daily on-time',10)""",
                (now_ts,),
            )
            conn.execute(
                """INSERT INTO proof_reviews(review_id,evidence_id,user_id,task_code,date,hour_slot,status,submitted_at)
                VALUES('rev_historical','ev_hist','user_lok','unit-hourly','2026-11-06','10:00','APPROVED',?)""",
                (now_ts,),
            )

        self._start_mock_active_session("sess_reset_012")
        result = self.mgr.user_reset_session(reason="Reset current only")
        self.assertTrue(result["success"])

        # Verify all historical tables are intact
        with self.db._connect() as conn:
            audit = conn.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action_type='TEST_ACTION'").fetchone()[0]
            self.assertEqual(audit, 1)

            points = conn.execute("SELECT COUNT(*) FROM score_events WHERE event_id='score_1'").fetchone()[0]
            self.assertEqual(points, 1)

            proof = conn.execute("SELECT COUNT(*) FROM proof_reviews WHERE review_id='rev_historical'").fetchone()[0]
            self.assertEqual(proof, 1)


if __name__ == '__main__':
    unittest.main()
