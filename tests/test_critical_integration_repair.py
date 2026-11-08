"""
Integration tests for the 6 critical repairs across Windows EXE, shared backend, and Android sync:
1. Wake-up proof vs. Hourly activity check-in task separation.
2. Wake-up camera verification: lighting, face, eyes, and 20-second live-presence check.
3. Bluetooth earbuds selection, active audio route check, 60% volume cap, full music track, and mandatory 3-step completion rule.
4. Sliding StrictBot productivity popup (no force-closing) + per-session dismissal cooldown + separated restriction states.
5. QR code + 6-digit manual PIN pairing, non-empty QR image fallback, LAN IP filtering, and idempotent handshake retry.
6. Cross-device sync of wake-up completion, earbuds config, hourly check-ins, and restrictions.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB, get_local_ip, get_all_lan_ips
from core.scoring_engine import ScoringEngine
from core.time_window_engine import TimeWindowEngine
from core.ui_dashboard import DASHBOARD_HTML as HTML_TEMPLATE
from windows.restriction_manager import YouTubeRestrictionManager


class TestCriticalIntegrationRepair(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.scoring = ScoringEngine(self.db)
        self.time_engine = TimeWindowEngine(self.db)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)
        os.makedirs("evidence", exist_ok=True)
        img_path = "evidence/wakeup_verified.jpg"
        if not os.path.exists(img_path):
            from PIL import Image
            Image.new("RGB", (120, 120), (100, 140, 170)).save(img_path, format="JPEG")

    def tearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_1_wakeup_and_hourly_task_separation_in_ui_and_schedule(self):
        """Verifies WAKE-UP VERIFICATION and HOURLY ACTIVITY CHECK-IN are distinct in UI and schedule."""
        self.assertIn("WAKE-UP VERIFICATION", HTML_TEMPLATE)
        self.assertIn("HOURLY ACTIVITY CHECK-IN", HTML_TEMPLATE)
        self.assertIn("openActiveTaskOrHourlyOverlay", HTML_TEMPLATE)
        self.assertIn("isWakeupActivityObj", HTML_TEMPLATE)
        self.assertIn("form.append('task_type', 'HOURLY_CHECK_IN')", HTML_TEMPLATE)

        # Ensure daily schedule creates distinct WAKE_UP and HOURLY activities
        activities = self.time_engine.generate_daily_schedule(self.today)
        wakeup_acts = [a for a in activities if a["type"] == "WAKE_UP"]
        hourly_acts = [a for a in activities if a["type"] == "HOURLY"]
        self.assertEqual(len(wakeup_acts), 1)
        self.assertGreaterEqual(len(hourly_acts), 1)
        self.assertIn("WAKEUP", wakeup_acts[0]["activity_id"])
        self.assertNotEqual(wakeup_acts[0]["activity_id"], hourly_acts[0]["activity_id"])

    def test_2_wakeup_camera_lighting_face_eyes_and_20s_liveness(self):
        """Verifies Step 1 rejects dark frames, missing face/eyes, or short liveness, and passes valid 20s liveness."""
        # 1. Low lighting must fail with guidance
        low_light_res = self.scoring.verify_wakeup_step1_camera(
            self.today,
            file_path="",
            lighting_score=22.0,
            face_detected=True,
            eyes_detected=True,
            liveness_duration_sec=20.0,
            valid_observation_ratio=0.90,
            liveness_status="RETRY_REQUIRED",
        )
        self.assertFalse(low_light_res["camera_verified"])
        self.assertEqual(low_light_res["liveness_status"], "RETRY_REQUIRED")
        self.assertIn("Lighting is too low", low_light_res["reason"])

        # 2. Missing face/eye detection must fail
        no_face_res = self.scoring.verify_wakeup_step1_camera(
            self.today,
            file_path="",
            lighting_score=75.0,
            face_detected=False,
            eyes_detected=False,
            liveness_duration_sec=20.0,
            valid_observation_ratio=0.20,
            liveness_status="RETRY_REQUIRED",
        )
        self.assertFalse(no_face_res["camera_verified"])
        self.assertIn("Face was not clearly visible", no_face_res["reason"])

        # 3. Short liveness (< 20 seconds) must fail
        short_res = self.scoring.verify_wakeup_step1_camera(
            self.today,
            file_path="",
            lighting_score=80.0,
            face_detected=True,
            eyes_detected=True,
            liveness_duration_sec=8.0,
            valid_observation_ratio=0.95,
            liveness_status="RETRY_REQUIRED",
        )
        self.assertFalse(short_res["camera_verified"])
        self.assertIn("20-second live-presence session incomplete", short_res["reason"])

        # 4. Valid 20-second liveness session with bright frame, face, and eyes passes
        pass_res = self.scoring.verify_wakeup_step1_camera(
            self.today,
            file_path="evidence/wakeup_verified.jpg",
            lighting_score=82.5,
            face_detected=True,
            eyes_detected=True,
            liveness_duration_sec=20.0,
            valid_observation_ratio=0.92,
            liveness_status="PASSED",
            detection_confidence=0.88,
        )
        self.assertTrue(pass_res["camera_verified"])
        self.assertEqual(pass_res["liveness_status"], "PASSED")

    def test_3_bluetooth_earbuds_full_track_and_mandatory_completion_rule(self):
        """Verifies Step 2 earbuds config/route, Step 3 full track + 60% volume cap, and mandatory 3-step rule."""
        # Configure preferred earbuds in Settings -> Wake-Up Audio -> Select Earbuds
        cfg_res = self.db.set_earbuds_config("Galaxy Buds2 Pro", "AA:BB:CC:DD:EE:FF")
        self.assertTrue(cfg_res["success"])
        loaded_cfg = self.db.get_earbuds_config()
        self.assertEqual(loaded_cfg["device_name"], "Galaxy Buds2 Pro")

        # Pass Step 1 first
        self.scoring.verify_wakeup_step1_camera(
            self.today,
            file_path="evidence/wakeup_verified.jpg",
            lighting_score=80.0,
            face_detected=True,
            eyes_detected=True,
            liveness_duration_sec=20.0,
            valid_observation_ratio=0.90,
            liveness_status="PASSED",
        )

        # Attempt to finalize before Step 2 and Step 3 -> must fail mandatory completion rule
        early_fin = self.scoring.finalize_wakeup_session(self.today)
        self.assertFalse(early_fin["wakeup_completed"])

        # Step 2: Disconnected or non-matching device must fail
        bt_fail = self.scoring.verify_wakeup_step2_bluetooth(
            self.today,
            connected_devices=["Phone Speaker"],
            preferred_device_name="Galaxy Buds2 Pro",
            active_route_verified=True,
        )
        self.assertFalse(bt_fail["bluetooth_verified"])

        # Step 2: Connected preferred Bluetooth earbuds with active route passes
        bt_pass = self.scoring.verify_wakeup_step2_bluetooth(
            self.today,
            connected_devices=["Galaxy Buds2 Pro (Bluetooth A2DP)"],
            preferred_device_name="Galaxy Buds2 Pro",
            active_route_verified=True,
        )
        self.assertTrue(bt_pass["bluetooth_verified"])

        # Step 3: Partial playback or >60% volume must fail
        track_partial = self.scoring.verify_wakeup_step3_motivation_track(
            self.today,
            played_duration_sec=3.0,
            track_duration_sec=8.0,
            volume_percent=50.0,
            audio_route="BLUETOOTH_A2DP",
        )
        self.assertFalse(track_partial["track_completed"])

        track_loud = self.scoring.verify_wakeup_step3_motivation_track(
            self.today,
            played_duration_sec=8.0,
            track_duration_sec=8.0,
            volume_percent=85.0,
            audio_route="BLUETOOTH_A2DP",
        )
        self.assertFalse(track_loud["track_completed"])

        # Step 3: 100% genuine playback at <= 60% volume over Bluetooth earbuds passes
        track_pass = self.scoring.verify_wakeup_step3_motivation_track(
            self.today,
            played_duration_sec=8.0,
            track_duration_sec=8.0,
            volume_percent=55.0,
            audio_route="BLUETOOTH_A2DP",
        )
        self.assertTrue(track_pass["track_completed"])

        # Finalize wake-up session now that all 3 mandatory checks passed
        fin = self.scoring.finalize_wakeup_session(self.today)
        self.assertTrue(fin["wakeup_completed"])
        self.assertGreater(fin["points_awarded"], 0)

    def test_4_sliding_strictbot_popup_and_session_dismissal_cooldown(self):
        """Verifies YouTubeRestrictionManager uses non-destructive popup with session cooldown and explicit state fields."""
        mgr = YouTubeRestrictionManager(self.db)
        status = mgr.get_status()
        self.assertIn("requestedRestriction", status)
        self.assertIn("effectivePrivilegeDecision", status)
        self.assertIn("actualEnforcementResult", status)
        self.assertIn("popupVisibility", status)
        self.assertIn("next_task", status)

        # Simulate dismissing the popup for YouTube
        dismiss_res = mgr.dismiss_lock_screen(app_key="youtube", duration_seconds=300)
        self.assertTrue(dismiss_res["success"])
        self.assertEqual(dismiss_res["dismissed_app"], "youtube")
        self.assertFalse(mgr.lock_screen_visible)

        # UI template must include StrictBot productivity popup elements
        self.assertIn("Time for something productive!", HTML_TEMPLATE)
        self.assertIn("openRecommendedNextTaskFromPopup()", HTML_TEMPLATE)
        self.assertIn("toggleLockWhyDetails()", HTML_TEMPLATE)

    def test_5_qr_and_manual_pin_pairing_and_lan_ip_filtering(self):
        """Verifies LAN IP filtering, QR code SVG fallback, 6-digit PIN + SR-XXXX-XXXX handshake, and idempotent retry."""
        lan_ip = get_local_ip()
        self.assertFalse(lan_ip.startswith("192.168.56."), "VirtualBox host-only IP must not be selected as primary LAN IP")
        all_ips = get_all_lan_ips()
        self.assertGreaterEqual(len(all_ips), 1)

        # Create pairing session
        sess = self.db.create_pairing_session()
        self.assertIn("session_key", sess)
        self.assertIn("manual_pin", sess)
        self.assertEqual(len(sess["manual_pin"]), 6)
        self.assertTrue(sess["manual_pin"].isdigit())
        self.assertTrue(sess["qr_code_image"].startswith("data:image/"), "QR code image must never be empty")
        self.assertTrue(len(sess.get("candidate_endpoints", [])) >= 1)

        # Handshake using 6-digit manual PIN
        hs1 = self.db.handshake_pairing(
            session_key="",
            manual_pin=sess["manual_pin"],
            device_id="samsung_a55_test_01",
            device_name="Samsung Galaxy A55",
            device_model="SM-A556E",
            os_version="Android 14",
            app_version="3.0.0",
            local_ip="192.168.1.55",
        )
        self.assertTrue(hs1["success"], f"Handshake with 6-digit PIN failed: {hs1}")
        self.assertEqual(hs1["trust_status"], "TRUSTED")

        # Idempotent retry from the same device must succeed (not fail with 'already used')
        hs_retry = self.db.handshake_pairing(
            session_key="",
            manual_pin=sess["manual_pin"],
            device_id="samsung_a55_test_01",
            device_name="Samsung Galaxy A55",
            device_model="SM-A556E",
            os_version="Android 14",
            app_version="3.0.0",
            local_ip="192.168.1.55",
        )
        self.assertTrue(hs_retry["success"], "Idempotent retry from same device must succeed")

        # Reuse by a DIFFERENT device must be rejected
        hs_other = self.db.handshake_pairing(
            session_key="",
            manual_pin=sess["manual_pin"],
            device_id="other_device_99",
            device_name="Other Phone",
        )
        self.assertFalse(hs_other["success"])

    def test_6_cross_device_sync_wakeup_and_earbuds(self):
        """Verifies Android sync events update Windows wake-up verification, earbuds config, and shared score."""
        events = [
            {
                "event_id": "evt_earbuds_sync_1",
                "event_type": "EARBUDS_CONFIG_UPDATE",
                "payload": {
                    "device_name": "Samsung Galaxy Buds FE",
                    "device_address": "11:22:33:44:55:66",
                },
            },
            {
                "event_id": "evt_wakeup_sync_1",
                "event_type": "WAKEUP_VERIFIED",
                "payload": {
                    "date": self.today,
                    "points": 15,
                    "bluetooth_device": "Samsung Galaxy Buds FE",
                },
            },
        ]
        res = self.db.process_sync_events_batch("samsung_a55_test_01", events)
        self.assertEqual(res["processed_count"], 2)

        # Verify earbuds config updated
        eb = self.db.get_earbuds_config()
        self.assertEqual(eb["device_name"], "Samsung Galaxy Buds FE")

        # A device label is not the measured three-step proof.
        chk = self.scoring.get_wakeup_checklist(self.today)
        self.assertNotEqual(chk["state"], "COMPLETED")
        with self.db._connect() as conn:
            checks=conn.execute('SELECT camera_verified,bluetooth_verified,motivation_verified FROM wake_up_sessions WHERE date=? AND is_dev_mode=0',(self.today,)).fetchone()
        self.assertEqual(tuple(checks),(0,0,0))


if __name__ == "__main__":
    unittest.main()
