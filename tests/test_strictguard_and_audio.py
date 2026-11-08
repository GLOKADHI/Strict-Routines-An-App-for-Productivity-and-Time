import os
import sys
import unittest
from unittest.mock import MagicMock,patch
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath("."))

from windows.strict_guard_proctor import StrictGuardProctor
from windows.strict_headphone_detector import StrictHeadphoneDetector
from windows.strict_audio_controller import StrictAudioController
from windows.strict_study_hardware import StrictStudyHardware
from windows.strict_study_workflow import StrictStudyWorkflow

class TestStrictGuardAndAudio(unittest.TestCase):

    def test_strictguard_violation_progression(self):
        proctor = StrictGuardProctor()
        proctor.is_calibrated = True
        proctor.neutral_ratio = 1.0
        
        # Test warning messages at different counts
        msg1 = proctor.get_warning_message(1)
        self.assertIn("First attention warning", msg1)
        
        msg5 = proctor.get_warning_message(5)
        self.assertIn("Repeated attention detected", msg5)
        
        msg10 = proctor.get_warning_message(10)
        self.assertIn("Attention limit approaching", msg10)
        
        msg15 = proctor.get_warning_message(15)
        self.assertIn("Final permitted warning", msg15)

        # Simulate reaching 15 violations
        proctor.violations_count = 15
        self.assertFalse(proctor.session_failed)
        
        # Trigger 16th violation
        proctor.trigger_violation()
        self.assertEqual(proctor.violations_count, 16)
        self.assertTrue(proctor.session_failed)
        self.assertIn("Maximum attention violations exceeded", proctor.failure_reason)

    def test_headphone_detector_classification(self):
        detector = StrictHeadphoneDetector()
        
        # Non-audio devices should not be flagged as prohibited
        self.assertFalse(StrictHeadphoneDetector.is_prohibited_audio_name("Logitech MX Master 3S Mouse"))
        self.assertFalse(StrictHeadphoneDetector.is_prohibited_audio_name("Keychron K2 Keyboard"))
        self.assertFalse(StrictHeadphoneDetector.is_prohibited_audio_name("Xbox Wireless Controller"))

        # Audio devices must be flagged
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("AirPods Pro"))
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("CMF Buds Pro"))
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("Sony WH-1000XM5"))
        self.assertTrue(StrictHeadphoneDetector.is_prohibited_audio_name("Galaxy Buds2"))

        # Verify jack detection status returns a valid enum
        jack_status = detector.get_jack_detection_status()
        self.assertIn(jack_status, ("SUPPORTED", "UNSUPPORTED", "NOT_VERIFIED"))

    def test_audio_controller_mute_and_restore(self):
        # Never change the developer's real speakers from a unit test.
        try:
            from tests.test_strict_proctoring_controls import Volume
        except ImportError:
            from test_strict_proctoring_controls import Volume
        volume=Volume(False)
        @contextmanager
        def endpoints():
            yield [SimpleNamespace(id='{0.0.0.fixture}',state=1,EndpointVolume=volume)]
        with patch('windows.strict_audio_controller.audio_devices',endpoints):
            controller=StrictAudioController()
            try:
                self.assertTrue(controller.save_and_mute()['speaker_muted'])
                self.assertTrue(controller.is_muted())
            finally:res_restore=controller.restore_volume_and_unmute()
            self.assertTrue(res_restore['restored'])
            self.assertFalse(controller.is_muted())

    def test_workflow_leftward_and_headphone_violations(self):
        mock_db = MagicMock()
        mock_db.create_deduction_proposal = MagicMock(return_value=True)
        
        wf = StrictStudyWorkflow(db=mock_db)
        # Mock active session state
        wf.s = {
            "session_id": "sess_test_123",
            "account_id": "user_lok",
            "android_device_id": "dev_and_123",
            "state": "ACTIVE_FOCUS",
            "started_at": "2026-11-02T10:00:00+05:30",
            "leftward_violations": 0,
            "max_leftward_violations": 15,
            "headphone_violations": 0,
            "last_leftward_message": "",
        }
        wf._save = MagicMock()
        
        # Record leftward violation
        wf.record_leftward_violation(1, "First attention warning")
        self.assertEqual(wf.s['leftward_violations'], 1)
        self.assertIn("First attention warning", wf.s['last_leftward_message'])
        
        # 16th violation must fail session
        res = wf.record_leftward_violation(16, "Exceeded permitted violations")
        self.assertEqual(wf.s['state'], 'FAILED')
        self.assertIn("leftward", wf.s['failed_reason'].lower())
        
        # Reset and test headphone 2-strike policy
        wf.s['state'] = 'ACTIVE_FOCUS'
        wf.s['headphone_violations'] = 0
        
        # Strike 1: Warning, remains active
        strike1 = wf.record_headphone_violation("CMF Buds", 1)
        self.assertFalse(strike1['failed'])
        self.assertEqual(wf.s['headphone_violations'], 1)
        self.assertEqual(wf.s['state'], 'ACTIVE_FOCUS')
        
        # Strike 2: Fails session
        strike2 = wf.record_headphone_violation("CMF Buds", 2)
        self.assertTrue(strike2['failed'])
        self.assertEqual(wf.s['state'], 'FAILED')
        self.assertIn("headphone", wf.s['failed_reason'].lower())

if __name__ == "__main__":
    unittest.main()
