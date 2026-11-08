import asyncio
import sqlite3
import time
import uuid
import numpy as np
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor, IPadCameraState, StrictLifecycleState
from windows.strict_study_workflow import StrictStudyWorkflow
from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor, IPadScreenShareState
from windows.strict_ios_receiver import IOSVideoReceiver, _Peer


class MockDB:
    def __init__(self):
        self._incidents = []
        self._profiles = [{'user_id': 'user_test'}]
        self._conn = sqlite3.connect(':memory:')

    def get_active_user_profile(self):
        return self._profiles[0]

    def _connect(self):
        class ConnContext:
            def __init__(self_ctx, conn):
                self_ctx.conn = conn
            def __enter__(self_ctx):
                return self_ctx.conn
            def __exit__(self_ctx, *args):
                pass
        return ConnContext(self._conn)


def test_initial_camera_verification_milestone():
    """Initial prerequisite requires at least 1 real frame decoded & processed, and persists across gaps."""
    db = MockDB()
    mon = IPadCameraStreamMonitor(db=db)
    mon.camera_device_id = "test_ipad_device_1"
    mon.reset_initial_check()

    # 1. Before any frames: not verified
    status_before = mon.get_initial_prerequisite_status()
    assert not status_before["initial_verified"]
    assert not status_before["initial_check_passed"]
    assert status_before["status"] == "IOS_CAMERA_INITIAL_CHECK: PENDING"
    assert status_before["initial_verification_record"] is None

    # 2. Real frame received, decoded (numpy ndarray)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    success = mon.record_valid_frame(
        frame=frame, frame_seq=1, mark_processed=True
    )
    assert success is True

    # 3. Prerequisite is marked IOS_CAMERA_INITIAL_VERIFIED with full record
    status_after = mon.get_initial_prerequisite_status()
    assert status_after["initial_verified"] is True
    assert status_after["initial_check_passed"] is True
    assert status_after["status"] == "IOS_CAMERA_INITIAL_VERIFIED"
    rec = status_after["initial_verification_record"]
    assert rec is not None
    assert rec["status"] == "IOS_CAMERA_INITIAL_VERIFIED"
    assert rec["device_id"] == "test_ipad_device_1"
    assert rec["camera_source_id"] == "IOS_FRONT_CAMERA"
    assert rec["received_frame_sequence"] == 1
    assert rec["decoded_frame_sequence"] >= 1
    assert rec["processed_frame_sequence"] >= 1

    # 4. Subsequent temporary frame gap does NOT un-verify the completed prerequisite
    mon.record_connection_status(device_connected=True, https_verified=True, webrtc_state="RECONNECTING", track_received=False)
    status_gap = mon.get_initial_prerequisite_status()
    assert status_gap["initial_verified"] is True
    assert status_gap["initial_check_passed"] is True
    assert status_gap["status"] == "IOS_CAMERA_INITIAL_VERIFIED"


def test_timer_never_pauses_during_brief_or_extended_interruption():
    """Authoritative Strict Session timer keeps running across 5s, 10s, 15s, and >15s frame gaps."""
    db = MockDB()
    mon = IPadCameraStreamMonitor.get_instance(db=db)
    mon.reset()
    mon.camera_device_id = "test_ipad_device_1"
    mon.camera_required = True

    current_mono = 100.0
    current_wall = datetime.fromisoformat("2026-11-06T10:00:00")
    mon._clock = lambda: current_mono
    mon._wall_clock = lambda: current_wall

    # Activate session
    sess_id = "session_test_resilience"
    mon.transition_lifecycle_state(StrictLifecycleState.SESSION_ACTIVE, session_id=sess_id)
    assert mon.lifecycle_state == StrictLifecycleState.SESSION_ACTIVE

    # Feed an initial session frame at t=100s
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mon.record_valid_frame(frame=frame, session_id=sess_id, frame_seq=1, mark_processed=True)

    # Mock study workflow state
    wf = StrictStudyWorkflow(db)
    wf.s = {
        'state': 'ACTIVE_FOCUS',
        'ios_required': True,
        'monitoring_resumed': True,
        'elapsed_seconds': 100.0,
        'focus_target_seconds': 2400.0,
        'flags': [],
        'account_id': 'user_test',
        'session_id': sess_id,
        'window': {'activity_id': 'w1', 'window_start': '2026-11-06T09:00:00+05:30', 'window_end': '2026-11-06T12:00:00+05:30'},
        'timing_rule': 'STANDARD'
    }
    wf._ipad_status = lambda: {'connected': True, 'reason': ''}
    wf._refresh_window = lambda now: None
    wf._scan_proof = lambda now: None

    # Tier 1: 3s gap (0–5s) -> ACTIVE / SHORT_GAP, timer unpaused
    current_mono = 103.0
    diag_3s = mon.evaluate_health(now_mono=current_mono)
    assert not diag_3s["is_timer_paused"]
    assert not diag_3s["is_interrupted"]
    assert diag_3s["health_state"] in ("CAMERA_RECOVERING", "WINDOWS_PROCESSING_ACTIVE")

    # Tier 2: 7s gap (5–10s) -> TEMPORARILY_PAUSED, timer unpaused
    current_mono = 107.0
    diag_7s = mon.evaluate_health(now_mono=current_mono)
    assert not diag_7s["is_timer_paused"]
    assert diag_7s["health_state"] == IPadCameraState.CAMERA_TEMPORARILY_PAUSED
    assert diag_7s["is_temporarily_paused"] is True

    # Tier 3: 12s gap (10–15s) -> DEGRADED, timer unpaused
    current_mono = 112.0
    diag_12s = mon.evaluate_health(now_mono=current_mono)
    assert not diag_12s["is_timer_paused"]
    assert diag_12s["health_state"] == IPadCameraState.CAMERA_DEGRADED
    assert diag_12s["is_degraded"] is True

    # Tier 4: 20s gap (>15s) -> CAMERA_INTERRUPTED, single incident, is_timer_paused MUST BE FALSE!
    current_mono = 120.0
    diag_20s = mon.evaluate_health(now_mono=current_mono)
    assert not diag_20s["is_timer_paused"], "Tier 4 must never pause the session timer!"
    assert diag_20s["is_interrupted"] is True
    assert diag_20s["is_session_flagged"] is True
    assert mon.active_incident is not None
    inc_id = mon.active_incident["incident_id"]

    # Verify that study workflow continues incrementing elapsed_seconds
    initial_elapsed = wf.s['elapsed_seconds']
    wf.monotonic = lambda: 121.0
    wf._last_mono = 120.0
    wf.clock = lambda: datetime.fromisoformat("2026-11-06T10:00:21+05:30")
    wf.tick(monitoring_ok=True)
    assert wf.s['elapsed_seconds'] > initial_elapsed, "Strict Session focus timer MUST advance during camera interruption!"
    assert wf.s.get('ipad_camera_paused') is False

    # Second evaluation at 25s: must not create a duplicate incident for the same continuous outage
    current_mono = 125.0
    diag_25s = mon.evaluate_health(now_mono=current_mono)
    assert not diag_25s["is_timer_paused"]
    assert mon.active_incident["incident_id"] == inc_id, "Must maintain single open incident without duplicates"


def test_automatic_recovery_after_three_stable_seconds():
    """Feed restored: requires 3.0 seconds of stable frames to mark recovered and resolve incident."""
    db = MockDB()
    mon = IPadCameraStreamMonitor(db=db)
    mon.reset()
    mon.camera_device_id = "test_ipad_device_1"

    current_mono = 200.0
    current_wall = datetime.fromisoformat("2026-11-06T10:00:00")
    mon._clock = lambda: current_mono
    mon._wall_clock = lambda: current_wall

    mon.transition_lifecycle_state(StrictLifecycleState.SESSION_ACTIVE, session_id="sess_recovery")

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mon.record_valid_frame(frame=frame, session_id="sess_recovery", frame_seq=1, mark_processed=True)

    # Cause interruption (>15s)
    current_mono = 218.0
    mon.evaluate_health(now_mono=current_mono)
    assert mon.active_incident is not None

    # Feed returns at t=220s (first frame after gap)
    current_mono = 220.0
    mon.record_valid_frame(frame=frame, session_id="sess_recovery", frame_seq=2, mark_processed=True)
    assert mon.health_state == IPadCameraState.CAMERA_RECOVERING
    assert mon.stable_recovery_start_mono == 220.0

    # 1.5s later: still in recovery stability window
    current_mono = 221.5
    mon.record_valid_frame(frame=frame, session_id="sess_recovery", frame_seq=3, mark_processed=True)
    assert mon.health_state == IPadCameraState.CAMERA_RECOVERING
    assert mon.active_incident is not None

    # 3.1s later: stable recovery complete!
    current_mono = 223.1
    mon.record_valid_frame(frame=frame, session_id="sess_recovery", frame_seq=4, mark_processed=True)
    assert mon.active_incident is None, "Incident must be resolved upon 3s stable recovery"
    assert mon.was_recovered is True
    assert len(mon.incidents_history) == 1
    assert mon.incidents_history[0]["incident_status"] == "RESOLVED"
    assert mon.incidents_history[0]["recovery_timestamp"] is not None


def test_independent_front_camera_and_screen_share_lifecycles():
    """Screen share start, failure, or stop never affects front camera receiver."""
    receiver = IOSVideoReceiver(store=MagicMock())
    cred = "c" * 64
    receiver._identity = MagicMock(return_value={"camera_id": "cam_independent", "session_id": "sess_ind"})

    mock_pc_cam = MagicMock()
    mock_pc_cam.iceGatheringState = "complete"
    mock_pc_cam.getTransceivers = MagicMock(return_value=[MagicMock(kind="video")])
    mock_pc_cam.localDescription = MagicMock(sdp="v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n")
    mock_pc_cam.close = AsyncMock()
    mock_pc_cam.setRemoteDescription = AsyncMock()
    mock_pc_cam.setLocalDescription = AsyncMock()
    mock_pc_cam.createAnswer = AsyncMock(return_value=mock_pc_cam.localDescription)

    receiver._runtime = (lambda **kw: mock_pc_cam, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    receiver.ice_servers = []

    async def run_test():
        sdp_offer = (
            "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\n"
            "a=fingerprint:sha-256 AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA\r\n"
            "a=setup:actpass\r\na=rtcp-mux\r\na=rtpmap:96 VP8/90000\r\n"
            "a=ice-ufrag:test\r\na=ice-pwd:testpassword1234567890\r\n"
            "a=sendonly\r\n"
        )

        ans_cam = await receiver.offer(cred, sdp_offer, source_id="IOS_FRONT_CAMERA")
        assert ans_cam["source_id"] == "IOS_FRONT_CAMERA"
        assert "cam_independent" in receiver._peers

        # 2. Screen share starts independently
        mock_pc_screen = MagicMock()
        mock_pc_screen.iceGatheringState = "complete"
        mock_pc_screen.getTransceivers = MagicMock(return_value=[MagicMock(kind="video")])
        mock_pc_screen.localDescription = MagicMock(sdp="v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n")
        mock_pc_screen.close = AsyncMock()
        mock_pc_screen.setRemoteDescription = AsyncMock()
        mock_pc_screen.setLocalDescription = AsyncMock()
        mock_pc_screen.createAnswer = AsyncMock(return_value=mock_pc_screen.localDescription)

        receiver._runtime = (lambda **kw: mock_pc_screen, MagicMock(), MagicMock(), MagicMock(), MagicMock())

        ans_screen = await receiver.offer(cred, sdp_offer, source_id="IOS_SCREEN_SHARE")
        assert ans_screen["source_id"] == "IOS_SCREEN_SHARE"
        assert "cam_independent" in receiver._screen_peers
        assert "cam_independent" in receiver._peers, "Camera peer MUST NOT be overwritten by screen share peer!"

        # 3. Stop screen share independently
        screen_peer = receiver._screen_peers["cam_independent"]
        await receiver._close_peer(screen_peer)
        assert "cam_independent" not in receiver._screen_peers
        assert "cam_independent" in receiver._peers, "Camera peer MUST remain connected after screen share stops!"

        await receiver.close()

    asyncio.run(run_test())


def test_diagnostic_root_cause_categories():
    """Specific diagnostic failure classifications."""
    db = MockDB()
    mon = IPadCameraStreamMonitor(db=db)
    mon.frame_sequence = 10

    # Case A: Rx > 0, Decoded == 0 -> VIDEO_DECODING_FAILURE
    mon.received_fps = 12.0
    mon.decoded_fps = 0.0
    mon.processing_fps = 0.0
    assert mon.classify_root_cause(elapsed_without_frames=16.0, processing_stalled=False) == "VIDEO_DECODING_FAILURE"

    # Case B: Decoded > 0, Processing == 0 -> INFERENCE_PIPELINE_STALLED
    mon.received_fps = 12.0
    mon.decoded_fps = 12.0
    mon.processing_fps = 0.0
    assert mon.classify_root_cause(elapsed_without_frames=16.0, processing_stalled=True) == "INFERENCE_PIPELINE_STALLED"

    # Case C: Rx == 0 after gap -> MEDIA_TRANSPORT_OR_CAPTURE_INTERRUPTION
    mon.received_fps = 0.0
    mon.decoded_fps = 0.0
    mon.processing_fps = 0.0
    assert mon.classify_root_cause(elapsed_without_frames=16.0, processing_stalled=False) == "MEDIA_TRANSPORT_OR_CAPTURE_INTERRUPTION"
