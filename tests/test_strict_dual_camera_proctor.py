"""
Comprehensive unit and integration tests for Dual-Camera Real-Time Proctoring:
- Windows Webcam + iOS Camera continuous video processing.
- Session entry gate enforcement (Windows confirmation of live iOS video feed).
- Independent perspective calibration and side-angle adaptation.
- Multi-camera event fusion and time-based deduplication (~3.0s window).
- Stream interruption recovery and bounded frame queue performance.
"""

import time
import uuid
import numpy as np
import pytest

from windows.strict_dual_camera_proctor import (
    StrictDualCameraProctor,
    MultiCameraEventManager,
)
from windows.strict_guard_proctor import StrictGuardProctor


def make_synthetic_frame(w=320, h=240, color=(128, 128, 128)):
    np.random.seed(42)
    # Generate sharp textured frame with clear lighting (brightness ~ 128, high variance)
    return np.random.randint(70, 180, (h, w, 3), dtype=np.uint8)


class TestDualCameraProctoring:
    def setup_method(self):
        StrictDualCameraProctor.reset_instance()

    def teardown_method(self):
        StrictDualCameraProctor.reset_instance()

    def test_multi_camera_event_manager_deduplicates_correlated_events(self):
        """Events observed on both cameras within 3.0s are counted once but both retained."""
        violations_emitted = []
        manager = MultiCameraEventManager(
            time_correlation_window_sec=3.0,
            on_fused_violation=lambda evt: violations_emitted.append(evt),
        )

        t0 = time.time()
        # Event 1 from Windows camera
        evt_win = {
            "session_id": "test_sess_1",
            "event_id": "evt_win_001",
            "camera_source_id": "WINDOWS",
            "event_type": "SUSTAINED_LOOKING_AWAY",
            "detection_timestamp": t0,
            "duration": 60.5,
            "confidence": 0.90,
            "detection_status": "CANDIDATE",
        }
        penalize1, rec1 = manager.record_candidate(evt_win)
        assert penalize1 is True
        assert rec1["detection_status"] == "CONFIRMED"
        assert len(violations_emitted) == 1

        # Event 2 from iOS camera 1.5s later (within 3.0s window)
        evt_ios = {
            "session_id": "test_sess_1",
            "event_id": "evt_ios_002",
            "camera_source_id": "IOS",
            "event_type": "SUSTAINED_LOOKING_AWAY",
            "detection_timestamp": t0 + 1.5,
            "duration": 60.2,
            "confidence": 0.88,
            "detection_status": "CANDIDATE",
        }
        penalize2, rec2 = manager.record_candidate(evt_ios)
        # Deduplication must prevent second penalty!
        assert penalize2 is False
        assert rec2["detection_status"] == "CORRELATED"
        assert rec2["related_event_id"] == "evt_win_001"
        assert rec1["related_event_id"] == "evt_ios_002"
        # Only 1 violation penalty emitted in total
        assert len(violations_emitted) == 1

        # Event 3 occurs 10s later (outside window) -> genuinely independent violation
        evt_win_late = {
            "session_id": "test_sess_1",
            "event_id": "evt_win_003",
            "camera_source_id": "WINDOWS",
            "event_type": "SUSTAINED_LOOKING_AWAY",
            "detection_timestamp": t0 + 10.0,
            "duration": 61.0,
            "confidence": 0.92,
            "detection_status": "CANDIDATE",
        }
        penalize3, rec3 = manager.record_candidate(evt_win_late)
        assert penalize3 is True
        assert rec3["detection_status"] == "CONFIRMED"
        assert len(violations_emitted) == 2

        # Verify audit trail retains all 3 individual camera observations
        all_events = manager.get_recent_events(limit=10)
        assert len(all_events) == 3
        assert [e["event_id"] for e in all_events] == ["evt_win_001", "evt_ios_002", "evt_win_003"]

    def test_ios_proctor_process_frame_in_memory(self):
        """Dedicated iOS proctor processes in-memory frames without VideoCapture."""
        proctor = StrictGuardProctor(camera_source="IOS")
        assert proctor.camera_source == "IOS"
        assert proctor.camera_name == "iPhone / iPad Camera"

        frame = make_synthetic_frame(320, 240)
        res = proctor.process_frame(frame)
        assert isinstance(res, dict)
        assert res["camera_status"] == "ACTIVE"
        assert proctor._last_frame_mono is not None
        assert proctor.processing_fps >= 0.0

    def test_ios_side_angle_calibration_adaptation(self):
        """When iOS camera is placed at a side angle (yaw > 35 deg), eye gaze is marked unavailable without false violations."""
        proctor = StrictGuardProctor(camera_source="IOS")
        # Dummy face detection [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc, score]
        face_dummy = np.array([[50, 50, 100, 100, 70, 80, 130, 80, 100, 100, 80, 130, 120, 130, 0.95]], dtype=np.float32)
        frame = make_synthetic_frame(320, 240)
        cam_mat = np.array([[320.0, 0, 160], [0, 320.0, 120], [0, 0, 1]])
        dist = np.zeros((4, 1))

        # Calibrate neutral orientation to the side-angle perspective (e.g. 50 deg angle)
        proctor.is_calibrated = True
        proctor.neutral_yaw = -169.1  # Matches the calibrated PnP baseline for this side perspective (> 35 deg)
        proctor.neutral_pitch = 33.3
        proctor.neutral_ratio = 0.50

        now = time.monotonic()
        proctor._process_detection(face_dummy, cam_mat, dist, now, frame)

        # Gaze status must recognize side-angle geometry and NOT flag false gaze violations
        assert proctor.gaze_status == "UNAVAILABLE (ANGLE OFFSET)"
        assert proctor.attention_state == "NORMAL"
        assert proctor.violations_count == 0

    def test_composite_status_both_active_only_when_both_receive_real_frames(self):
        """'BOTH CAMERAS ACTIVE' is shown only when both cameras are actively receiving frames."""
        dual = StrictDualCameraProctor.get_instance()

        # Initially neither is receiving
        comp0 = dual.get_composite_status()
        assert comp0["state"] in ("BOTH_UNAVAILABLE", "WINDOWS_ONLY", "IOS_ONLY")
        assert comp0["label"] != "BOTH CAMERAS ACTIVE"

        # Submit iOS frame
        frame = make_synthetic_frame(320, 240)
        dual.submit_ios_frame(frame, timestamp=time.time())
        time.sleep(0.15)  # Allow worker to process

        # Simulate Windows webcam active
        dual.windows_proctor.is_running = True
        dual.windows_proctor._last_frame_mono = time.monotonic()
        dual.windows_proctor.camera_status = "ACTIVE"

        comp_both = dual.get_composite_status()
        assert comp_both["state"] == "BOTH_ACTIVE"
        assert comp_both["label"] == "BOTH CAMERAS ACTIVE"
        assert comp_both["color"] == "#4be78b"

        # Pause iOS camera (simulate 6 seconds without frames)
        dual._ios_last_frame_received_at = time.time() - 6.0
        comp_paused = dual.get_composite_status()
        assert comp_paused["state"] == "WINDOWS_ONLY"
        assert comp_paused["label"] == "WINDOWS CAMERA ACTIVE – iOS CAMERA UNAVAILABLE"
        assert comp_paused["color"] == "#f59e0b"

    def test_bounded_queue_drops_stale_frames(self):
        """Queue capacity 2 drops stale frames under high ingestion rate without memory growth."""
        dual = StrictDualCameraProctor.get_instance()
        frame = make_synthetic_frame(320, 240)

        # Submit 10 frames rapidly
        for _ in range(10):
            dual.submit_ios_frame(frame)

        # Queue size should never exceed 2
        assert dual.ios_frame_queue.qsize() <= 2
        assert dual._ios_dropped_frames >= 0
