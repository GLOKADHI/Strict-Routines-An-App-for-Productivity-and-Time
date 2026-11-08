"""
Regression and verification test suite for StrictGuard enhancements:
Mobile phone detection during submission window & camera-based AirPods / wireless earbuds detection.

Covers all 14 regression scenarios:
1. Normal face visibility without a phone.
2. Phone held within the webcam view during an open submission window.
3. Phone visible outside the submission window.
4. Registered Android used for authorized proof submission.
5. AirPods worn visibly.
6. AirPods merely held in the hand.
7. AirPods connected to Bluetooth but not visibly worn.
8. Earbuds visibly worn but Bluetooth disconnected.
9. Sony WF-1000XM5 connected.
10. CMF Buds connected.
11. Camera temporarily loses tracking.
12. Camera and Bluetooth detect the same headphone event.
13. Repeated notifications for one continuous event.
14. Second distinct headphone violation and existing two-point penalty.
"""

import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from windows.strict_integrity_events import IntegrityEpisodes
from windows.strict_visual_objects import EarbudWearDetector, VisualObjectMonitor
from windows.strict_headphone_detector import StrictHeadphoneDetector
from windows.strict_study_workflow import StrictStudyWorkflow


class TestStrictGuardPhoneAndEarbuds(unittest.TestCase):

    def setUp(self):
        self.session_id = "sess_regression_test_42"
        self.episodes = IntegrityEpisodes(enabled=True, session_id=self.session_id)
        # Synthetic face landmarks: [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc, score]
        self.synthetic_face = np.array([100, 60, 120, 140, 130, 100, 190, 100, 160, 130, 140, 170, 180, 170, 0.95], dtype=np.float32)
        # Synthetic neutral frame: 320x240 RGB/BGR skin tone background
        self.base_frame = np.full((240, 320, 3), (120, 150, 190), dtype=np.uint8)

    # 1. Normal face visibility without a phone
    def test_01_normal_face_visibility_without_phone(self):
        detector = EarbudWearDetector()
        worn = detector.detect_in_ear(self.base_frame, self.synthetic_face)
        self.assertEqual(len(worn), 0, "Clean face frame should have 0 worn earbuds")

        # Test in VisualObjectMonitor pipeline
        mock_phone_detector = MagicMock()
        mock_phone_detector.detect.return_value = []
        with patch("windows.strict_visual_objects.PhoneDetector", return_value=mock_phone_detector):
            clock_val = [100.0]
            monitor = VisualObjectMonitor(self.episodes, clock=lambda: clock_val[0])
            monitor.submit(self.base_frame, self.synthetic_face)
            
            # Simulate worker run cycle
            sample = monitor.pending
            frame, face, _ = sample
            found_phones = mock_phone_detector.detect(frame)
            found_earbuds = monitor.earbud_detector.detect_in_ear(frame, face)
            
            self.assertEqual(len(found_phones), 0)
            self.assertEqual(len(found_earbuds), 0)
            self.assertEqual(len(self.episodes.snapshot()), 0, "No violations should be recorded for normal face")

    # 2. Phone held within webcam view during open submission window
    def test_02_phone_during_open_submission_window(self):
        clock_val = [100.0]
        monitor = VisualObjectMonitor(self.episodes, clock=lambda: clock_val[0])
        monitor.set_window_checker(lambda: True) # Submission window OPEN
        monitor.set_session_context_provider(lambda: {"authorized_proof_submission": False})

        mock_phone = [{"category": "PHONE_VISIBLE", "confidence": 0.88, "box": [50, 50, 80, 140]}]
        
        # Debounce confirmation over 2.0s
        for t in [100.0, 101.0, 102.5]:
            self.episodes.observe("MOBILE_PHONE_DETECTED", True, t, hold=2.0, max_gap=2.0,
                                  message="Mobile phone detected during submission window.", confidence=0.88)
        
        events = self.episodes.snapshot()
        phone_events = [e for e in events if e["category"] == "MOBILE_PHONE_DETECTED"]
        self.assertEqual(len(phone_events), 1, "Should emit confirmed MOBILE_PHONE_DETECTED event")
        self.assertEqual(phone_events[0]["session_id"], self.session_id)
        self.assertEqual(phone_events[0]["confidence"], 0.88)

        # Phone removed/cleared
        self.episodes.observe("MOBILE_PHONE_DETECTED", False, 104.0)
        self.episodes.observe("MOBILE_PHONE_DETECTED", False, 105.5)
        
        cleared_events = [e for e in self.episodes.snapshot() if e["category"] == "MOBILE_PHONE_DETECTION_CLEARED"]
        self.assertEqual(len(cleared_events), 1, "Should emit MOBILE_PHONE_DETECTION_CLEARED event upon removal")
        self.assertEqual(cleared_events[0]["session_id"], self.session_id)

    # 3. Phone visible outside the submission window
    def test_03_phone_outside_submission_window(self):
        clock_val = [100.0]
        monitor = VisualObjectMonitor(self.episodes, clock=lambda: clock_val[0])
        monitor.set_window_checker(lambda: False) # Submission window CLOSED
        
        # Phone is detected in camera, but window is NOT open
        self.assertFalse(monitor.is_submission_window_open())
        # Rule specifies: Phone visible outside submission window does NOT trigger MOBILE_PHONE_DETECTED
        window_open = monitor.is_submission_window_open()
        if window_open:
            self.episodes.observe("MOBILE_PHONE_DETECTED", True, 100.0, hold=2.0)
        else:
            self.episodes.observe("MOBILE_PHONE_DETECTED", False, 100.0)
            
        events = [e for e in self.episodes.snapshot() if e["category"] == "MOBILE_PHONE_DETECTED"]
        self.assertEqual(len(events), 0, "No MOBILE_PHONE_DETECTED violation when submission window is closed")

    # 4. Registered Android used for authorized proof submission
    def test_04_registered_android_authorized_proof_submission(self):
        clock_val = [100.0]
        monitor = VisualObjectMonitor(self.episodes, clock=lambda: clock_val[0])
        monitor.set_window_checker(lambda: True) # Window is OPEN
        # Explicit authorized proof interaction context
        monitor.set_session_context_provider(lambda: {
            "authorized_proof_submission": True,
            "device_id": "registered_android_123"
        })

        self.assertTrue(monitor.is_submission_window_open())
        self.assertTrue(monitor.is_authorized_proof_submission())

        # When authorized proof submission is active, phone violation rule must NOT trigger
        if monitor.is_submission_window_open() and not monitor.is_authorized_proof_submission():
            self.episodes.observe("MOBILE_PHONE_DETECTED", True, 100.0)
        else:
            self.episodes.observe("MOBILE_PHONE_DETECTED", False, 100.0)

        events = [e for e in self.episodes.snapshot() if e["category"] == "MOBILE_PHONE_DETECTED"]
        self.assertEqual(len(events), 0, "Authorized Android proof submission must not trigger phone violation")

    # 5. AirPods worn visibly
    def test_05_airpods_worn_visibly(self):
        detector = EarbudWearDetector()
        rois = detector.get_ear_rois(self.base_frame.shape, self.synthetic_face)
        self.assertGreaterEqual(len(rois), 1)

        # Draw white AirPods in right ear ROI
        frame_with_airpods = self.base_frame.copy()
        rx, ry, rw, rh = rois[0][1]
        import cv2
        cv2.ellipse(frame_with_airpods, (rx + rw//2, ry + rh//2), (5, 8), 0, 0, 360, (250, 250, 250), -1)
        cv2.rectangle(frame_with_airpods, (rx + rw//2 - 2, ry + rh//2), (rx + rw//2 + 2, ry + rh//2 + 10), (250, 250, 250), -1)

        detected = detector.detect_in_ear(frame_with_airpods, self.synthetic_face)
        self.assertGreaterEqual(len(detected), 1, "Should detect worn AirPods")
        self.assertEqual(detected[0]["category"], "AIRPODS")
        self.assertTrue(detected[0]["worn"])

        # Temporal confirmation in episodes
        for t in [10.0, 11.0, 12.0]:
            self.episodes.observe(
                "AIRPODS_WORN_CAMERA_DETECTED", True, t, hold=1.5, max_gap=2.0,
                message="Prohibited AIRPODS detected worn in camera.",
                confidence=detected[0]["confidence"],
                device_category="AIRPODS",
                detection_source="CAMERA_VISION"
            )

        events = [e for e in self.episodes.snapshot() if e["category"] == "AIRPODS_WORN_CAMERA_DETECTED"]
        self.assertEqual(len(events), 1, "Continuous wearing must be counted only once")
        self.assertEqual(events[0]["session_id"], self.session_id)
        self.assertEqual(events[0]["device_category"], "AIRPODS")
        self.assertEqual(events[0]["detection_source"], "CAMERA_VISION")

    # 6. AirPods merely held in the hand
    def test_06_airpods_merely_held_in_hand(self):
        detector = EarbudWearDetector()
        frame_hand = self.base_frame.copy()
        import cv2
        # Draw white earbud in lower part of frame (hand zone, away from ear ROIs)
        cv2.ellipse(frame_hand, (160, 180), (6, 9), 0, 0, 360, (250, 250, 250), -1)

        in_ear = detector.detect_in_ear(frame_hand, self.synthetic_face)
        in_hand = detector.detect_in_hand_or_frame(frame_hand, self.synthetic_face)

        self.assertEqual(len(in_ear), 0, "Earbuds held in hand should NOT be classified as worn in ear")
        self.assertGreaterEqual(len(in_hand), 1, "Should detect earbud object in hand/frame")
        self.assertFalse(in_hand[0]["worn"], "Earbud in hand must have worn=False")
        self.assertTrue(in_hand[0]["held_in_hand"])

    # 7. AirPods connected to Bluetooth but not visibly worn
    def test_07_airpods_connected_bluetooth_not_worn(self):
        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        detector.update_camera_earbuds(worn=False) # Camera does NOT see them worn

        # Bluetooth reports AirPods Pro actively connected
        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[{"name": "AirPods Pro", "class": 0x0404, "connected": True}]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            res = detector.check_audio_devices()
            self.assertTrue(res["prohibited_connected"])
            self.assertTrue(res["bluetooth_headphone_connected"])
            self.assertFalse(res["camera_earbuds_worn"])

            detector._apply_observation(res)
            self.assertEqual(detector.violations_count, 1, "Strike 1 for actively connected Bluetooth AirPods")
            self.assertTrue(detector.is_paused_for_headphone)

            # Check structured events
            events = self.episodes.snapshot()
            bt_conn_ev = [e for e in events if e["category"] == "BLUETOOTH_AUDIO_DEVICE_CONNECTED"]
            self.assertEqual(len(bt_conn_ev), 1)
            self.assertEqual(bt_conn_ev[0]["device_name"], "AirPods Pro")
            self.assertEqual(bt_conn_ev[0]["session_id"], self.session_id)

    # 8. Earbuds visibly worn but Bluetooth disconnected
    def test_08_earbuds_worn_bluetooth_disconnected(self):
        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        # Camera reports earbuds worn
        detector.update_camera_earbuds(worn=True, category="AIRPODS", confidence=0.92)

        # Bluetooth reports NO connected devices
        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            res = detector.check_audio_devices()
            self.assertTrue(res["prohibited_connected"], "Visibly worn earbuds must trigger prohibited connection")
            self.assertTrue(res["camera_earbuds_worn"])
            self.assertFalse(res["bluetooth_headphone_connected"])

            detector._apply_observation(res)
            self.assertEqual(detector.violations_count, 1, "Strike 1 for visibly worn earbuds")
            self.assertTrue(detector.is_paused_for_headphone)

            events = self.episodes.snapshot()
            violation_ev = [e for e in events if e["category"] == "HEADPHONE_VIOLATION_CONFIRMED"]
            self.assertEqual(len(violation_ev), 1)
            self.assertIn("CAMERA", violation_ev[0]["evidence_sources"])

    # 9. Sony WF-1000XM5 connected
    def test_09_sony_wf_1000xm5_connected(self):
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("Sony WF-1000XM5"))
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("WF-1000XM5"))

        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[{"name": "Sony WF-1000XM5", "class": 0x0404, "connected": True}]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            res = detector.check_audio_devices()
            self.assertTrue(res["prohibited_connected"])
            detector._apply_observation(res)
            self.assertEqual(detector.violations_count, 1)

            events = [e for e in self.episodes.snapshot() if e["category"] == "BLUETOOTH_AUDIO_DEVICE_CONNECTED"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["device_name"], "Sony WF-1000XM5")

    # 10. CMF Buds connected
    def test_10_cmf_buds_connected(self):
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("CMF Buds Pro 2"))
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("CMF by Nothing Buds"))

        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[{"name": "CMF Buds Pro 2", "class": 0x0404, "connected": True}]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            res = detector.check_audio_devices()
            self.assertTrue(res["prohibited_connected"])
            detector._apply_observation(res)
            self.assertEqual(detector.violations_count, 1)

            events = [e for e in self.episodes.snapshot() if e["category"] == "BLUETOOTH_AUDIO_DEVICE_CONNECTED"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["device_name"], "CMF Buds Pro 2")

    # 11. Camera temporarily loses tracking (no fake violations)
    def test_11_camera_temporarily_loses_tracking(self):
        detector = EarbudWearDetector()
        # When face is None (camera loss or face occluded/absent)
        worn = detector.detect_in_ear(self.base_frame, None)
        self.assertEqual(len(worn), 0, "No face should yield 0 earbud detections")

        # In IntegrityEpisodes, missing sample must bridge cleanly without firing violations
        self.episodes.observe("AIRPODS_WORN_CAMERA_DETECTED", None, 100.0)
        self.episodes.observe("AIRPODS_WORN_CAMERA_DETECTED", None, 101.0)
        self.assertEqual(len(self.episodes.snapshot()), 0, "Tracking loss must not generate fake violations")

    # 12. Camera and Bluetooth detect the same headphone event (single violation count)
    def test_12_camera_and_bluetooth_same_headphone_event(self):
        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        # Both camera and Bluetooth detect AirPods simultaneously
        detector.update_camera_earbuds(worn=True, category="AIRPODS", confidence=0.95)

        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[{"name": "AirPods", "class": 0x0404, "connected": True}]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            res = detector.check_audio_devices()
            self.assertTrue(res["prohibited_connected"])
            self.assertIn("BLUETOOTH", res["evidence_sources"])
            self.assertIn("CAMERA", res["evidence_sources"])

            detector._apply_observation(res)
            # Must count as ONE event (Strike 1), NOT two!
            self.assertEqual(detector.violations_count, 1, "Simultaneous camera and bluetooth detections must count as a single event")
            self.assertTrue(detector.is_paused_for_headphone)

    # 13. Repeated notifications for one continuous event (de-duplicated)
    def test_13_repeated_notifications_de_duplicated(self):
        detector = StrictHeadphoneDetector()
        detector.set_integrity(self.episodes)
        detector.update_camera_earbuds(worn=True, category="AIRPODS", confidence=0.95)

        with patch("windows.strict_headphone_detector.enumerate_connected_bluetooth_audio", return_value=[{"name": "AirPods", "class": 0x0404, "connected": True}]), \
             patch.object(detector, "_detect_wired_headphones", return_value=[]):
            
            # Simulate 10 successive polling ticks while the headphones remain connected/worn
            for _ in range(10):
                res = detector.check_audio_devices()
                detector._apply_observation(res)

            # Violation count must remain 1 throughout the continuous event
            self.assertEqual(detector.violations_count, 1, "Continuous ongoing detection must NOT increment violations count")
            self.assertTrue(detector.is_paused_for_headphone)

            # Exactly one HEADPHONE_VIOLATION_CONFIRMED event should have been logged
            violations = [e for e in self.episodes.snapshot() if e["category"] == "HEADPHONE_VIOLATION_CONFIRMED"]
            self.assertEqual(len(violations), 1, "Only one violation confirmation event should be recorded per episode")

    # 14. Second distinct headphone violation and existing two-point penalty
    def test_14_second_distinct_violation_and_penalty(self):
        mock_db = MagicMock()
        mock_db.create_deduction_proposal = MagicMock(return_value=True)
        # Prevent recover() from failing on mock query
        mock_db._connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None

        wf = StrictStudyWorkflow(db=mock_db)
        wf.s = {
            "session_id": self.session_id,
            "account_id": "user_lok",
            "android_device_id": "dev_and_123",
            "state": "ACTIVE_FOCUS",
            "started_at": "2026-11-04T10:00:00+05:30",
            "headphone_violations": 0,
            "reward_status": "ACTIVE",
            "integrity_counts": {},
            "integrity_events": []
        }
        wf._save = MagicMock()

        # Strike 1: Warning, paused, session NOT failed
        strike1 = wf.record_headphone_violation("AirPods Pro", 1)
        self.assertFalse(strike1["failed"])
        self.assertEqual(wf.s["headphone_violations"], 1)
        self.assertTrue(wf.s.get("headphone_paused"))
        self.assertEqual(wf.s["state"], "ACTIVE_FOCUS")
        self.assertEqual(mock_db.create_deduction_proposal.call_count, 0, "No deduction on strike 1")

        # Condition clears and resumes
        wf.s["headphone_paused"] = False

        # Strike 2: Second distinct confirmed event fails session and revokes reward
        strike2 = wf.record_headphone_violation("AirPods Pro", 2)
        self.assertTrue(strike2["failed"])
        self.assertEqual(wf.s["headphone_violations"], 2)
        self.assertEqual(wf.s["state"], "FAILED")
        self.assertIn(wf.s["reward_status"], ("CANCELLED", "INELIGIBLE"), "Reward eligibility must be revoked")

        # Manager ensures two-point deduction proposal
        from windows.strict_session_manager import StrictSessionManager
        mgr = StrictSessionManager(db=mock_db)
        mgr.session_id = self.session_id
        mgr.study = wf
        mgr._ensure_headphone_penalty("AirPods Pro")

        mock_db.create_deduction_proposal.assert_called_once()
        call_kwargs = mock_db.create_deduction_proposal.call_args.kwargs
        self.assertEqual(call_kwargs["proposed_points"], -2, "Must apply existing 2-point deduction exactly once")
        self.assertEqual(call_kwargs["event_type"], "STRICT_SESSION_HEADPHONE_SECOND_DETECTION")
        self.assertEqual(call_kwargs["related_task_id"], self.session_id)


if __name__ == "__main__":
    unittest.main()
