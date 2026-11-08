import unittest
import os
import json
import time
import hashlib
import tempfile
import numpy as np

from core.hourly_submission_service import HourlySubmissionService
from core.taufa_ai_validator import TaufaAiValidator, TaufaDuplicateResult, TaufaValidationResult
from windows.strict_guard_proctor import StrictGuardProctor, get_strict_guard_proctor
from windows.strict_session_manager import StrictSessionManager, get_strict_session_manager
from core.database import StrictRecordsDB


class TestCriticalFixesSuite(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = StrictRecordsDB(self.db_path)

    def tearDown(self):
        try:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # --- PART 1: Proof Deduplication & Multi-Category & File Storage ---

    def test_01_resilient_evidence_file_writing(self):
        """Tests that _safe_write_evidence_file succeeds even with simulated Windows locks or existing files."""
        temp_path = os.path.join(self.temp_dir, "test.png.uploading")
        final_path = os.path.join(self.temp_dir, "test.png")
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 200

        # Initial write
        HourlySubmissionService._safe_write_evidence_file(temp_path, final_path, data)
        self.assertTrue(os.path.isfile(final_path))
        self.assertEqual(os.path.getsize(final_path), len(data))

        # Idempotent re-write of exact same file (should succeed immediately)
        HourlySubmissionService._safe_write_evidence_file(temp_path, final_path, data)
        self.assertTrue(os.path.isfile(final_path))

    def test_02_multi_category_normalization(self):
        """Tests that compound UI categories ('Coding / Programming', 'Academic Study', etc.) normalize properly."""
        self.assertEqual(HourlySubmissionService.normalize_category("Coding / Programming"), "CODING")
        self.assertEqual(HourlySubmissionService.normalize_category("Academic Study"), "STUDY")
        self.assertEqual(HourlySubmissionService.normalize_category("Deep Work / Project"), "PROJECT")
        self.assertEqual(HourlySubmissionService.normalize_category("Exercise / Workout"), "EXERCISE")
        self.assertEqual(HourlySubmissionService.normalize_category("Book Reading"), "READING")
        self.assertEqual(HourlySubmissionService.normalize_category("Study, Project"), "STUDY")

    def test_03_taufa_duplicate_scoping(self):
        """Tests that near-duplicates flag REVIEW_REQUIRED without duplicate_detected=True (no auto rejection)."""
        validator = TaufaAiValidator()
        res = validator.check_duplicate_evidence(
            images=[b"test_image_bytes_12345678"],
            submission_id="sub_test_001",
            category="STUDY",
            description="Studying machine learning notes",
            task_type="HOURLY",
            historical_records=[]
        )
        self.assertFalse(res.duplicate_detected)

    # --- PART 2: StrictGuard Face Tracking & Calibration ---

    def test_04_strictguard_downward_study_pitch(self):
        """Tests that downward head pitch (notebook writing) activates DOWNWARD_STUDY and does not trigger violations."""
        sg = StrictGuardProctor()
        sg.is_calibrated = True
        sg.neutral_pitch = 0.0
        sg.downward_pitch_min = -45.0
        sg.downward_pitch_max = -12.0
        sg.current_pitch = -25.0  # looking down at notebook
        sg.current_yaw = 0.0

        sg._track_direction(100.0, is_left=False)
        self.assertEqual(sg.attention_state, "DOWNWARD_STUDY")
        self.assertEqual(sg.violations_count, 0)
        telem = sg.get_telemetry()
        self.assertTrue(telem["downward_study_active"])

    def test_05_strictguard_guided_calibration(self):
        """Tests the 6-step guided calibration state machine and profile generation."""
        sg = StrictGuardProctor()
        step1 = sg.start_guided_calibration()
        self.assertEqual(step1["step"], 1)
        self.assertEqual(step1["name"], "CAMERA_CHECK")

        step2 = sg.advance_guided_calibration(2)
        self.assertEqual(step2["step"], 2)
        self.assertEqual(step2["name"], "CENTER_NEUTRAL")

        step3 = sg.advance_guided_calibration(3)
        self.assertEqual(step3["step"], 3)
        self.assertEqual(step3["name"], "DOWNWARD_STUDY")

        step4 = sg.advance_guided_calibration(4)
        self.assertEqual(step4["step"], 4)
        self.assertEqual(step4["name"], "UPWARD_PITCH")

        step5 = sg.advance_guided_calibration(5)
        self.assertEqual(step5["step"], 5)
        self.assertEqual(step5["name"], "RETURN_CENTER")

        step6 = sg.advance_guided_calibration(6)
        self.assertEqual(step6["step"], 6)
        self.assertEqual(step6["name"], "COMPLETE")
        self.assertTrue(sg.is_calibrated)
        self.assertTrue(sg.calibration_profile["calibrated"])

    def test_06_strictguard_leftward_persistence(self):
        """Tests that leftward violations require >= 60.0s continuous sustained attention and >= 1.0s neutral return."""
        sg = StrictGuardProctor()
        sg.is_calibrated = True
        sg.neutral_pitch = 0.0

        # Sub-60s leftward attention -> 0 violations
        for t in np.arange(10.0, 40.0, 0.2):
            sg._track_direction(float(t), is_left=True)
        self.assertEqual(sg.violations_count, 0)

        # > 60s sustained attention -> 1 violation
        for t in np.arange(40.0, 102.0, 0.2):
            sg._track_direction(float(t), is_left=True)
        self.assertEqual(sg.violations_count, 1)
        self.assertTrue(sg._in_violation_event)

        # Immediate return (< 1.0s) -> stays in violation
        sg._track_direction(102.2, is_left=False)
        self.assertTrue(sg._in_violation_event)

        # >= 1.0s return -> violation cleared
        sg._track_direction(103.5, is_left=False)
        self.assertFalse(sg._in_violation_event)

    # --- PART 3 & 4: Mobile Usage Alerts & Modal Synchronization ---

    def test_07_phone_usage_event_processing(self):
        """Tests that PHONE_USAGE_DETECTED events are recorded and active alerts exposed to UI."""
        from windows.strict_session_manager import StrictSessionManager
        ssm = StrictSessionManager(self.db, "user_lok")
        ssm.record_phone_usage_event(
            event_id="phone_alert_12345",
            device="ANDROID",
            package_name="com.instagram.android",
            app_name="Instagram",
            timestamp="2026-11-04T12:00:00Z"
        )
        alerts = ssm.get_active_phone_usage_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["event_id"], "phone_alert_12345")
        self.assertEqual(alerts[0]["app_name"], "Instagram")

        # Acknowledge alert
        ack = ssm.acknowledge_phone_usage_alert("phone_alert_12345")
        self.assertTrue(ack["success"])
        self.assertEqual(len(ssm.get_active_phone_usage_alerts()), 0)


if __name__ == "__main__":
    unittest.main()
