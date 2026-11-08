"""
Unit test suite for iPad screen sharing integration and multi-source proctoring.
Verifies:
1. Multi-track WebRTC offer validation (1-2 video allowed, audio/data forbidden).
2. Independent IPadScreenShareMonitor metrics, MAD change detection, and activity levels.
3. Independent screen share watchdog timer (10s threshold, recovery after 3s).
4. StrictDualCameraProctor 3-source telemetry and composite status reporting.
"""

import time
import pytest
import numpy as np

from windows.strict_ios_receiver import validate_offer
from windows.strict_ipad_screen_monitor import (
    IPadScreenShareMonitor,
    IPadScreenShareState,
)
from windows.strict_dual_camera_proctor import StrictDualCameraProctor


SAMPLE_SINGLE_VIDEO_SDP = (
    "v=0\r\n"
    "o=- 12345 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=fingerprint:sha-256 AA:BB:CC:DD\r\n"
    "a=ice-ufrag:testufrag\r\n"
    "a=ice-pwd:testpwd1234567890\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=sendonly\r\n"
)

SAMPLE_DUAL_VIDEO_SDP = (
    "v=0\r\n"
    "o=- 12345 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=fingerprint:sha-256 AA:BB:CC:DD\r\n"
    "a=ice-ufrag:testufrag\r\n"
    "a=ice-pwd:testpwd1234567890\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=sendonly\r\n"
    "m=video 10 UDP/TLS/RTP/SAVPF 97\r\n"
    "a=sendonly\r\n"
)

SAMPLE_AUDIO_VIDEO_SDP = (
    "v=0\r\n"
    "o=- 12345 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=fingerprint:sha-256 AA:BB:CC:DD\r\n"
    "a=ice-ufrag:testufrag\r\n"
    "a=ice-pwd:testpwd1234567890\r\n"
    "m=audio 8 UDP/TLS/RTP/SAVPF 111\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=sendonly\r\n"
)


class TestWebRTCMultiTrackOfferValidation:
    def test_single_video_offer_accepted(self):
        res = validate_offer(SAMPLE_SINGLE_VIDEO_SDP)
        assert "m=video 9" in res

    def test_dual_video_offer_accepted(self):
        res = validate_offer(SAMPLE_DUAL_VIDEO_SDP)
        assert "m=video 9" in res
        assert "m=video 10" in res

    def test_audio_offer_strictly_rejected(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_offer(SAMPLE_AUDIO_VIDEO_SDP)

    def test_more_than_two_videos_rejected(self):
        triple_video_sdp = SAMPLE_DUAL_VIDEO_SDP + "m=video 11 UDP/TLS/RTP/SAVPF 98\r\na=sendonly\r\n"
        with pytest.raises(ValueError, match="forbidden"):
            validate_offer(triple_video_sdp)


class TestIPadScreenShareMonitor:
    def setup_method(self):
        IPadScreenShareMonitor.reset_instance()
        self.monitor = IPadScreenShareMonitor.get_instance()

    def teardown_method(self):
        self.monitor.reset()

    def test_frame_ingestion_and_static_detection(self):
        self.monitor.start_track("track_screen_123")
        # Ingest two identical blank frames
        frame1 = np.zeros((480, 640, 3), dtype=np.uint8)
        frame2 = np.zeros((480, 640, 3), dtype=np.uint8)

        res1 = self.monitor.process_frame(frame1)
        assert res1["accepted"] is True

        res2 = self.monitor.process_frame(frame2)
        assert res2["accepted"] is True
        assert res2["activity_level"] == IPadScreenShareState.ACTIVITY_STATIC
        assert res2["mad"] < 2.0

        tel = self.monitor.get_telemetry()
        assert tel["is_streaming"] is True
        assert tel["resolution"] == "640x480"
        assert tel["activity_level"] == "STATIC"

    def test_active_and_rapid_transition_detection(self):
        self.monitor.start_track("track_screen_123")
        base = np.zeros((480, 640, 3), dtype=np.uint8)
        self.monitor.process_frame(base)

        # Frame with moderate difference (scrolling text / active)
        active_frame = np.full((480, 640, 3), 15, dtype=np.uint8)
        res_act = self.monitor.process_frame(active_frame)
        assert res_act["activity_level"] == IPadScreenShareState.ACTIVITY_ACTIVE

        # Frame with drastic difference (app switch / video)
        rapid_frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        res_rap = self.monitor.process_frame(rapid_frame)
        assert res_rap["activity_level"] == IPadScreenShareState.ACTIVITY_RAPID_TRANSITION
        assert res_rap["mad"] >= 25.0

    def test_watchdog_interruption_and_recovery(self):
        interrupted_events = []
        recovered_events = []
        self.monitor.on_interruption = lambda ev: interrupted_events.append(ev)
        self.monitor.on_recovery = lambda ev: recovered_events.append(ev)

        now_m = time.monotonic()
        self.monitor._initial_verified = True
        self.monitor.start_active_session_monitoring("session_test", now_mono=now_m)
        self.monitor.start_track("track_screen_123")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.monitor.process_frame(frame, timestamp_epoch=time.time())

        # Check watchdog right away (within 10s gap)
        status = self.monitor.check_watchdog(now_mono=now_m + 3.0)
        assert status["interrupted"] is False

        # Advance beyond 10s threshold
        status_stalled = self.monitor.check_watchdog(now_mono=now_m + 11.5)
        assert status_stalled["interrupted"] is True
        assert len(interrupted_events) == 1
        assert interrupted_events[0]["source"] in ("IOS_FULL_SCREEN_SHARE", "IOS_SCREEN_SHARE")

        # Resume streaming frames
        rec_m = now_m + 12.0
        self.monitor.process_frame(frame, timestamp_epoch=time.time(), now_mono=rec_m)
        # 3.5s later after stable frames
        self.monitor.process_frame(frame, timestamp_epoch=time.time(), now_mono=rec_m + 3.5)

        tel = self.monitor.get_telemetry()
        assert tel["is_interrupted"] is False
        assert len(recovered_events) == 1


class TestMultiSourceProctoringIntegration:
    def setup_method(self):
        IPadScreenShareMonitor.reset_instance()
        StrictDualCameraProctor.reset_instance()
        self.proctor = StrictDualCameraProctor.get_instance()
        self.screen_mon = IPadScreenShareMonitor.get_instance()

    def teardown_method(self):
        self.proctor.stop()
        self.screen_mon.reset()

    def test_dual_camera_proctor_submits_screen_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.proctor.submit_ios_screen_frame(frame)
        tel = self.proctor.get_dual_camera_telemetry()
        assert "ios_screen_share" in tel
        assert tel["ios_screen_share"]["source"] in ("IOS_FULL_SCREEN_SHARE", "IOS_SCREEN_SHARE")
        assert tel["ios_screen_share"]["frame_sequence"] == 1

    def test_composite_status_all_three_active(self):
        now = time.time()
        # Simulate active Windows camera
        self.proctor.windows_proctor.is_running = True
        self.proctor.windows_proctor._last_frame_mono = time.monotonic()

        # Simulate active iOS camera
        self.proctor._ios_last_frame_received_at = now
        self.proctor._ios_received_times.extend([now - 0.5, now])
        self.proctor._ios_proc_times.extend([now - 0.5, now])
        self.proctor._ios_track_status = "LIVE"
        self.proctor._ios_received_fps = 12.0

        # Simulate active iPad screen share
        self.screen_mon.start_track("track_1")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(5):
            self.screen_mon.process_frame(frame)

        comp = self.proctor.get_composite_status()
        assert comp["state"] == "ALL_THREE_ACTIVE"
        assert "ALL 3 SOURCES ACTIVE" in comp["label"]
        assert comp["windows_fresh"] is True
        assert comp["ios_fresh"] is True
        assert comp["screen_fresh"] is True

    def test_composite_status_dual_camera_when_screen_stopped(self):
        now = time.time()
        # Active Windows camera and iOS camera
        self.proctor.windows_proctor.is_running = True
        self.proctor.windows_proctor._last_frame_mono = time.monotonic()
        self.proctor._ios_last_frame_received_at = now
        self.proctor._ios_received_times.extend([now - 0.5, now])
        self.proctor._ios_proc_times.extend([now - 0.5, now])
        self.proctor._ios_track_status = "LIVE"
        self.proctor._ios_received_fps = 12.0

        # Screen share is stopped
        self.screen_mon.stop_track()

        comp = self.proctor.get_composite_status()
        assert comp["state"] == "BOTH_ACTIVE"
        assert comp["screen_fresh"] is False
        assert comp["windows_fresh"] is True
        assert comp["ios_fresh"] is True
