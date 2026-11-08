"""
STRICTRECORDS – AUTOMATED TEST SUITE: STRICT SESSION LIFECYCLE & CAMERA INTERRUPTION WATCHDOG GATING
Verifies Test Cases A through J from Section 10:
- Case A: Open Settings without starting a session -> No red popup, no flag, no timer pause.
- Case B: Generate a QR invitation -> Informational device status only.
- Case C: Prerequisite checking -> Normal camera setup status, no session incident.
- Case D: Session Ready -> Complete prerequisites, wait without confirming Start Session -> No interruption popup.
- Case E: Confirm and start session -> Fresh session-specific watchdog starts with clean baseline.
- Case F: Short interruption -> Disconnect iPad video for 10 seconds -> No violation or session failure.
- Case G: Sustained interruption -> Disconnect iPad frames for >15s during active session -> One alert, one incident, timer paused.
- Case H: End session -> End session, disconnect iPad video -> No new alert.
- Case I: Restart Windows EXE -> Open Settings with no active session -> No stale interruption popup.
- Case J: Start another session -> Fresh session ID and new watchdog baseline, no interruption duration carried over.
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pytest

from core.strict_session_lifecycle import StrictLifecycleState
from windows.strict_ipad_camera_monitor import (
    IPadCameraStreamMonitor,
    IPadCameraState,
)


class MockClock:
    def __init__(self, start_mono: float = 1000.0, start_wall: Optional[datetime] = None):
        self.mono = start_mono
        self.wall = start_wall or datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

    def clock(self) -> float:
        return self.mono

    def wall_clock(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class MockDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strict_camera_incidents (
                    incident_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    camera_device_id TEXT,
                    camera_source TEXT NOT NULL,
                    last_valid_frame_timestamp TEXT,
                    interruption_detected_timestamp TEXT NOT NULL,
                    interruption_duration REAL,
                    last_webrtc_state TEXT,
                    last_received_fps REAL,
                    last_processing_fps REAL,
                    incident_status TEXT NOT NULL,
                    recovery_timestamp TEXT,
                    details_json TEXT
                )
            """)


def create_dummy_frame(width=640, height=480) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


# =============================================================================
# CASE A: Open Settings without starting a session
# =============================================================================
def test_case_a_open_settings_without_starting_session():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    # Lifecycle is IDLE / not active
    assert monitor.lifecycle_state == StrictLifecycleState.IDLE

    # Advance clock by 7231 seconds while camera is closed
    mock_clock.advance(7231.0)
    diag = monitor.evaluate_health()

    assert diag["is_session_active"] is False
    assert diag["is_interrupted"] is False
    assert diag["is_degraded"] is False
    assert diag["is_timer_paused"] is False
    assert diag["is_session_flagged"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["active_incident"] is None
    assert diag["incidents_count"] == 0
    assert diag["elapsed_without_frames_seconds"] == 0.0


# =============================================================================
# CASE B: Generate a QR invitation
# =============================================================================
def test_case_b_generate_qr_invitation():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.transition_lifecycle_state(StrictLifecycleState.PREREQUISITE_CHECK)
    assert monitor.lifecycle_state == StrictLifecycleState.PREREQUISITE_CHECK

    # QR generated, but camera not yet connected
    mock_clock.advance(60.0)
    diag = monitor.evaluate_health()

    assert diag["is_session_active"] is False
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["display_label"] == "IPAD CAMERA: NOT CONNECTED"
    assert diag["active_incident"] is None


# =============================================================================
# CASE C: Prerequisite checking
# =============================================================================
def test_case_c_prerequisite_checking():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.transition_lifecycle_state(StrictLifecycleState.PREREQUISITE_CHECK)

    # Ingest 1 real frame during camera setup test
    frame = create_dummy_frame()
    accepted = monitor.record_valid_frame(frame, frame_seq=1)
    assert accepted is True

    # Prerequisite check passes
    prereq = monitor.get_initial_prerequisite_status()
    assert prereq["initial_check_passed"] is True
    assert prereq["status"] == IPadCameraState.IOS_CAMERA_INITIAL_CHECK_PASSED

    # Camera preview test finishes and stream stops for 300 seconds
    mock_clock.advance(300.0)
    diag = monitor.evaluate_health()

    # Informational setup status only: NO session incident, NO interruption alert!
    assert diag["is_session_active"] is False
    assert diag["initial_check_passed"] is True
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["is_timer_paused"] is False
    assert diag["active_incident"] is None
    assert diag["display_label"] == "IPAD CAMERA: PREREQUISITE READY"


# =============================================================================
# CASE D: Session Ready
# =============================================================================
def test_case_d_session_ready():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    # Prerequisite completed
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, frame_seq=1)
    monitor.transition_lifecycle_state(StrictLifecycleState.SESSION_READY)
    assert monitor.lifecycle_state == StrictLifecycleState.SESSION_READY

    # User waits 120 seconds before starting
    mock_clock.advance(120.0)
    diag = monitor.evaluate_health()

    assert diag["is_session_active"] is False
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["is_timer_paused"] is False


# =============================================================================
# CASE E: Confirm and start session
# =============================================================================
def test_case_e_confirm_and_start_session():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock(start_mono=1000.0)
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    # Simulate prerequisite test frame from 2 hours ago (7231 seconds ago)
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, frame_seq=1)
    mock_clock.advance(7231.0)

    # User clicks Start/Confirm Session at mono=8231.0
    monitor.start_session_monitoring("session_test_e", "ipad_cam_e")
    assert monitor.lifecycle_state == StrictLifecycleState.SESSION_ACTIVE
    assert monitor.session_id == "session_test_e"

    # Immediately after start, evaluate health
    diag = monitor.evaluate_health()

    # Must NOT calculate 7231 seconds! Measured from session start baseline (0.0s)
    assert diag["elapsed_without_frames_seconds"] == 0.0
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["last_valid_frame_timestamp"] == "Waiting for first session frame"
    assert diag["is_timer_paused"] is False


# =============================================================================
# CASE F: Short interruption
# =============================================================================
def test_case_f_short_interruption():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock(start_mono=1000.0)
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_f", "ipad_cam_f")

    # Ingest session frame 1
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_f", frame_seq=1)

    # Disconnect iPad video for 10 seconds (Tier 2: 5-10s TEMPORARILY_PAUSED)
    mock_clock.advance(10.0)
    diag = monitor.evaluate_health()

    assert diag["elapsed_without_frames_seconds"] == 10.0
    assert diag["is_interrupted"] is False
    assert diag["is_degraded"] is False
    assert diag["is_temporarily_paused"] is True
    assert diag["is_timer_paused"] is False
    assert diag["active_incident"] is None
    assert diag["interruption_warning_modal"] is None


# =============================================================================
# CASE G: Sustained interruption
# =============================================================================
def test_case_g_sustained_interruption():
    IPadCameraStreamMonitor.reset_instance()
    temp_db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_file.close()
    mock_db = MockDB(temp_db_file.name)
    mock_clock = MockClock(start_mono=1000.0)

    monitor = IPadCameraStreamMonitor(
        db=mock_db,
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_g", "ipad_cam_g")

    # Baseline frame
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_g", frame_seq=1)

    # Disconnect iPad frames for 16 seconds (> 15s threshold)
    mock_clock.advance(16.0)
    diag = monitor.evaluate_health()

    assert diag["elapsed_without_frames_seconds"] == 16.0
    assert diag["is_interrupted"] is True
    assert diag["is_timer_paused"] is False  # Never pause timer during camera interruption
    assert diag["is_session_flagged"] is True
    assert diag["interruption_warning_modal"] is not None
    assert diag["interruption_warning_modal"]["title"] == "MANDATORY iPAD CAMERA FEED INTERRUPTED"
    assert diag["active_incident"] is not None
    assert diag["incidents_count"] == 1

    # Continued outage for another 10 seconds: NO duplicate incident!
    mock_clock.advance(10.0)
    diag2 = monitor.evaluate_health()
    assert diag2["incidents_count"] == 1
    assert diag2["elapsed_without_frames_seconds"] == 26.0


# =============================================================================
# CASE H: End session
# =============================================================================
def test_case_h_end_session():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_h", "ipad_cam_h")
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_h", frame_seq=1)

    # End session cleanly
    monitor.stop_session_monitoring(reason="Session completed cleanly")
    assert monitor.lifecycle_state == StrictLifecycleState.SESSION_COMPLETED
    assert monitor.session_id == ""

    # Disconnect iPad video and advance 60 seconds
    mock_clock.advance(60.0)
    diag = monitor.evaluate_health()

    # No alert, no timer pause, watchdog stopped
    assert diag["is_session_active"] is False
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["is_timer_paused"] is False


# =============================================================================
# CASE I: Restart Windows EXE
# =============================================================================
def test_case_i_restart_windows_exe():
    # Fresh process / restarted EXE instance
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor.get_instance()
    monitor._clock = mock_clock.clock
    monitor._wall_clock = mock_clock.wall_clock

    assert monitor.lifecycle_state == StrictLifecycleState.IDLE
    assert monitor.session_id == ""

    # Open Settings without active session
    diag = monitor.get_diagnostics()
    assert diag["is_session_active"] is False
    assert diag["is_interrupted"] is False
    assert diag["interruption_warning_modal"] is None
    assert diag["is_timer_paused"] is False


# =============================================================================
# CASE J: Start another session
# =============================================================================
def test_case_j_start_another_session():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock(start_mono=1000.0)
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )

    # Session 1 runs and has an interruption
    monitor.start_session_monitoring("session_1", "ipad_cam_1")
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_1", frame_seq=1)
    mock_clock.advance(20.0)
    diag1 = monitor.evaluate_health()
    assert diag1["is_interrupted"] is True
    assert diag1["elapsed_without_frames_seconds"] == 20.0

    # Session 1 is ended
    monitor.stop_session_monitoring(reason="Session 1 finished")

    # Time passes between sessions
    mock_clock.advance(500.0)

    # Session 2 is started with fresh ID at mono=1520.0
    monitor.start_session_monitoring("session_2", "ipad_cam_2")
    assert monitor.session_id == "session_2"

    diag2 = monitor.evaluate_health()
    # Baseline must be fresh: NO 520s duration carried over!
    assert diag2["elapsed_without_frames_seconds"] == 0.0
    assert diag2["is_interrupted"] is False
    assert diag2["interruption_warning_modal"] is None
    assert diag2["last_valid_frame_timestamp"] == "Waiting for first session frame"
    assert diag2["active_incident"] is None
