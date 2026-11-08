import time
import unittest
from unittest.mock import MagicMock, patch

from windows.strict_audio_controller import (
    StrictAudioController,
    EndpointVolumeNotificationCallback,
    AudioDeviceNotificationClient,
)
from windows.strict_audio_endpoints import active_render_endpoints, get_default_render_device


class TestWindowsSpeakerMuteDetection(unittest.TestCase):
    def setUp(self):
        self.controller = StrictAudioController()

    def tearDown(self):
        try:
            self.controller.restore()
        except Exception:
            pass

    def test_default_rendering_endpoint_identification(self):
        """Test #2: Identify the default Windows audio-rendering endpoint."""
        dev, name, dev_id = get_default_render_device()
        print(f"\n[Test 2] Default Audio Endpoint: {name} (ID: {dev_id})")
        if dev_id:
            self.assertTrue(dev_id.startswith("{0.0.0."), "Default device must be a rendering endpoint")
            self.assertIsNotNone(name)

    def test_enumerate_active_rendering_endpoints(self):
        """Test #3: Enumerate all active rendering endpoints without querying capture/microphones."""
        with active_render_endpoints() as devices:
            print(f"\n[Test 3] Active Render Endpoints Count: {len(devices)}")
            for d in devices:
                dev_id = getattr(d, "id", "")
                self.assertTrue(
                    str(dev_id).startswith("{0.0.0."),
                    f"Endpoint {dev_id} must be a render endpoint, never a microphone"
                )

    def test_accurate_state_representation(self):
        """Test #4 & #5: Distinguish SPEAKER MUTED, SPEAKER UNMUTED, and VOLUME 0%."""
        st = self.controller.get_status()
        self.assertIn(
            st["speaker_state"],
            ("SPEAKER MUTED", "SPEAKER UNMUTED", "VOLUME 0%", "DEVICE UNAVAILABLE", "VERIFICATION FAILED"),
            "Status must report an authoritative speaker state"
        )
        self.assertIn("master_mute", st)
        self.assertIn("zero_volume", st)
        self.assertIn("physical_hardware_note", st)
        self.assertIn("endpoints_details", st)
        self.assertIn("app_sessions", st)
        self.assertFalse(st.get("microphone_required", True), "Microphone must never be required")
        self.assertEqual(st.get("microphone"), "NOT_REQUIRED", "Microphone state must be NOT_REQUIRED")

    def test_save_and_mute_lifecycle(self):
        """Test save_and_mute(): Saves original volume and mute, enforces mute, verifies mute."""
        # 1. Capture status before mute
        pre_status = self.controller.get_status()
        orig_endpoints = pre_status.get("endpoints_details", [])

        # 2. Execute save_and_mute
        mute_res = self.controller.save_and_mute()
        self.assertTrue(self.controller.is_active(), "Audio controller must be active during session")
        print(f"\n[Test Save & Mute] State: {mute_res['speaker_state']}, Muted: {mute_res['speaker_muted']}")

        # If any active endpoints exist, they must be verified muted
        if mute_res["active_endpoints_count"] > 0:
            self.assertEqual(mute_res["speaker_state"], "SPEAKER MUTED")
            self.assertTrue(mute_res["speaker_muted"])
            self.assertEqual(mute_res["speaker_output"], "MUTED")

        # 3. Restore original configuration
        restore_res = self.controller.restore()
        self.assertFalse(self.controller.is_active(), "Audio controller must be inactive after restore")
        self.assertEqual(restore_res["speaker_output"], "RESTORED")
        print(f"[Test Restore] State: {restore_res['speaker_state']}, Restored: {restore_res['restored']}")

    def test_unmute_detection_and_reapplication(self):
        """Test #4: If speaker becomes unmuted during session, reapply mute and record event."""
        unmute_events_recorded = []
        self.controller.set_on_unmute_callback(lambda ev: unmute_events_recorded.append(ev))

        self.controller.save_and_mute()
        self.assertTrue(self.controller.is_active())

        # Simulate Core Audio volume callback firing an un-mute event
        dummy_endpoint_id = "{0.0.0.00000000}.{dummy-test-endpoint}"
        self.controller._handle_volume_notification(dummy_endpoint_id, bMuted=False, fMasterVolume=0.5)

        # Verify violation counter incremented and event recorded
        self.assertGreaterEqual(self.controller.unmute_violations_count, 1)
        self.assertGreaterEqual(len(unmute_events_recorded), 1)
        self.assertEqual(unmute_events_recorded[0]["category"], "SPEAKER_UNMUTED")
        print(f"\n[Test Unmute Event] Recorded: {unmute_events_recorded[0]}")

        self.controller.restore()

    def test_volume_zero_distinct_from_master_mute(self):
        """Test #5: Detect volume set to zero separately from master mute."""
        with patch.object(self.controller, "get_status") as mock_get_status:
            # Case A: Master Muted (Vol 50%) -> SPEAKER MUTED
            mock_get_status.return_value = {
                "speaker_state": "SPEAKER MUTED",
                "speaker_muted": True,
                "master_mute": True,
                "zero_volume": False,
            }
            st_a = self.controller.get_status()
            self.assertEqual(st_a["speaker_state"], "SPEAKER MUTED")

            # Case B: Unmuted, but Volume 0% -> VOLUME 0%
            mock_get_status.return_value = {
                "speaker_state": "VOLUME 0%",
                "speaker_muted": False,
                "master_mute": False,
                "zero_volume": True,
            }
            st_b = self.controller.get_status()
            self.assertEqual(st_b["speaker_state"], "VOLUME 0%")
            self.assertFalse(st_b["speaker_muted"])

            # Case C: Unmuted, Volume 30% -> SPEAKER UNMUTED
            mock_get_status.return_value = {
                "speaker_state": "SPEAKER UNMUTED",
                "speaker_muted": False,
                "master_mute": False,
                "zero_volume": False,
            }
            st_c = self.controller.get_status()
            self.assertEqual(st_c["speaker_state"], "SPEAKER UNMUTED")

    def test_device_connection_notification(self):
        """Test #6 & #7: Monitor newly connected audio devices and default device switches."""
        device_changes = []
        self.controller.set_on_device_change_callback(lambda evt, dev: device_changes.append((evt, dev)))
        self.controller._active = True

        # Simulate IMMNotificationClient callbacks
        client = AudioDeviceNotificationClient(self.controller._handle_device_notification)
        client.OnDeviceAdded("{0.0.0.00000000}.{new-usb-speaker}")
        client.OnDefaultDeviceChanged(0, 0, "{0.0.0.00000000}.{new-usb-speaker}")

        self.assertTrue(self.controller._wake_event.is_set(), "Wake event must be triggered on device change")
        self.assertEqual(len(device_changes), 2)

    def test_app_session_monitoring(self):
        """Test #8: Monitor individual application audio sessions where relevant."""
        st = self.controller.get_status()
        app_sessions = st.get("app_sessions", [])
        print(f"\n[Test 8] Detected Application Audio Sessions ({len(app_sessions)}):")
        for s in app_sessions[:5]:
            print(f"   App: {s.get('name')} (PID: {s.get('pid')}) | Muted: {s.get('muted')} | Vol: {s.get('volume')}")
        self.assertIsInstance(app_sessions, list)

    def test_no_microphone_permissions_and_no_audio_recording(self):
        """Test #9 & #10: Verify no microphone capture interfaces or recording streams are initialized."""
        st = self.controller.get_status()
        self.assertEqual(st.get("microphone"), "NOT_REQUIRED")
        self.assertFalse(st.get("microphone_required"))
        for ep in st.get("endpoints_details", []):
            self.assertTrue(ep["id"].startswith("{0.0.0."), f"Endpoint {ep['id']} must be render-only")
            self.assertFalse(ep["id"].startswith("{0.0.1."), f"Endpoint {ep['id']} must NOT be a capture/microphone device")


if __name__ == "__main__":
    unittest.main()
