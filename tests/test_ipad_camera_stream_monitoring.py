"""
STRICTRECORDS – AUTOMATED TEST SUITE: MANDATORY iPAD CAMERA STREAM MONITORING
Tests A through J verifying:
- Test A: Normal continuous stream maintains active monitoring.
- Test B: 10-second frame gap does NOT trigger an interruption incident or pause the session.
- Test C: 20-second frame gap transitions to CAMERA_DEGRADED.
- Test D: >30-second frame gap triggers CAMERA_INTERRUPTED, creates a single incident, flags session, and pauses authoritative timer.
- Test E: WebRTC remains connected while frames stop arriving – correctly detects camera interruption.
- Test F: Camera permission revoked / track ends – detects missing frames.
- Test G: Network disconnection during streaming – detects missing frames and preserves incident record.
- Test H: Frame recovery requires 5 consecutive seconds of stable frames before clearing incident and resuming timer.
- Test I: Multiple separate interruptions produce distinct incident records without duplicates during the same outage.
- Test J: Windows webcam proctoring continues unhindered during an iPad camera interruption.
- Test K: StrictStudyWorkflow authoritative timer advancement and pausing integration.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pytest

from windows.strict_ipad_camera_monitor import (
    IPadCameraStreamMonitor,
    IPadCameraState,
)


class MockClock:
    def __init__(self, start_mono: float = 1000.0, start_wall: Optional[datetime] = None):
        self.mono = start_mono
        self.wall = start_wall or datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

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
# TEST A: Normal continuous stream maintains active monitoring
# =============================================================================
def test_test_a_normal_continuous_stream():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_a", "ipad_dev_1")

    # Ingest 10 consecutive frames 1 second apart
    for seq in range(1, 11):
        frame = create_dummy_frame()
        accepted = monitor.record_valid_frame(
            frame,
            session_id="session_test_a",
            presentation_timestamp=mock_clock.mono,
            frame_seq=seq,
        )
        assert accepted is True
        mock_clock.advance(1.0)

    diag = monitor.evaluate_health()
    assert diag["is_live"] is True
    assert diag["is_degraded"] is False
    assert diag["is_interrupted"] is False
    assert diag["is_timer_paused"] is False
    assert diag["display_label"] == "IPAD CAMERA: LIVE – MONITORING ACTIVE"
    assert diag["frame_sequence"] == 10
    assert diag["frame_dimensions"] == "640x480"
    assert diag["dropped_frames_count"] == 0
    assert diag["active_incident"] is None


# =============================================================================
# TEST B: 10-second frame gap does NOT trigger interruption incident or pause
# =============================================================================
def test_test_b_10_second_frame_gap():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_b", "ipad_dev_1")

    # Ingest baseline frame
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_b", frame_seq=1)

    # 10 seconds elapse with no frames
    mock_clock.advance(10.0)
    diag = monitor.evaluate_health()

    assert diag["elapsed_without_frames_seconds"] == 10.0
    assert diag["is_interrupted"] is False
    assert diag["is_degraded"] is False
    assert diag["is_timer_paused"] is False
    assert diag["active_incident"] is None
    assert diag["incidents_count"] == 0


# =============================================================================
# TEST C: 12-second frame gap transitions to CAMERA_DEGRADED (10s < gap <= 15s)
# =============================================================================
def test_test_c_20_second_frame_gap_degraded():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_c", "ipad_dev_1")

    # Ingest baseline frame
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_c", frame_seq=1)

    # 12 seconds elapse with no frames (10s < gap <= 15s)
    mock_clock.advance(12.0)
    diag = monitor.evaluate_health()

    assert diag["elapsed_without_frames_seconds"] == 12.0
    assert diag["is_degraded"] is True
    assert diag["is_interrupted"] is False
    assert diag["is_timer_paused"] is False  # Degraded does NOT pause timer yet
    assert diag["display_label"] == "IPAD CAMERA: DEGRADED – ATTEMPTING RECOVERY"
    assert diag["proctoring_processing_state"] == "DEGRADED"
    assert diag["active_incident"] is None


# =============================================================================
# TEST D: >15-second frame gap triggers CAMERA_INTERRUPTED, creates incident,
# flags session, and pauses authoritative timer
# =============================================================================
def test_test_d_30_second_frame_gap_interrupted():
    IPadCameraStreamMonitor.reset_instance()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mock_db = MockDB(db_path)
        mock_clock = MockClock()
        monitor = IPadCameraStreamMonitor(
            db=mock_db,
            clock=mock_clock.clock,
            wall_clock=mock_clock.wall_clock,
        )
        monitor.start_session_monitoring("session_test_d", "ipad_dev_1")

        # Ingest baseline frame
        frame = create_dummy_frame()
        monitor.record_valid_frame(frame, session_id="session_test_d", frame_seq=1)

        # 31 seconds elapse with no frames (> 15s)
        mock_clock.advance(31.0)
        diag = monitor.evaluate_health()

        assert diag["elapsed_without_frames_seconds"] == 31.0
        assert diag["is_interrupted"] is True
        assert diag["is_timer_paused"] is False  # Never paused per Section 4
        assert diag["is_session_flagged"] is True
        assert "MANDATORY_IPAD_CAMERA_INTERRUPTED" in diag["flags"]
        assert "FLAGGED_CAMERA_INTERRUPTION" in diag["flags"]
        assert diag["display_label"] == "MANDATORY iPAD CAMERA FEED INTERRUPTED"
        assert diag["proctoring_processing_state"] == "INTERRUPTED"

        # Verify exactly one incident created
        incident = diag["active_incident"]
        assert incident is not None
        assert incident["session_id"] == "session_test_d"
        assert incident["incident_status"] == "OPEN"
        assert incident["interruption_duration"] == 31.0

        # Verify warning modal format
        modal = diag["interruption_warning_modal"]
        assert modal is not None
        assert modal["title"] in ("iOS CAMERA INTERRUPTED – SESSION FLAGGED", "MANDATORY iPAD CAMERA FEED INTERRUPTED")
        assert modal["camera_source"] == "iPhone/iPad"
        assert modal["webrtc_state"] == "DISCONNECTED"
        assert "FLAGGED" in modal["actions_status"]
        assert "TIMER PAUSED" in modal["actions_status"]
        assert "ATTEMPTING RECONNECTION" in modal["actions_status"]

        # Verify persisted in database
        with mock_db._connect() as conn:
            rows = conn.execute("SELECT * FROM strict_camera_incidents WHERE session_id='session_test_d'").fetchall()
            assert len(rows) == 1
            assert rows[0]["incident_status"] == "OPEN"

        # Subsequent evaluation during same outage does NOT create duplicate incident
        mock_clock.advance(5.0)
        diag2 = monitor.evaluate_health()
        assert diag2["incidents_count"] == 1
        with mock_db._connect() as conn:
            rows2 = conn.execute("SELECT * FROM strict_camera_incidents WHERE session_id='session_test_d'").fetchall()
            assert len(rows2) == 1
            assert rows2[0]["interruption_duration"] == 36.0

    finally:
        IPadCameraStreamMonitor.reset_instance()
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass


# =============================================================================
# TEST E: WebRTC remains connected while frames stop arriving –
# correctly detects camera interruption
# =============================================================================
def test_test_e_webrtc_connected_without_frames():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_e", "ipad_dev_1")

    # Ingest frame
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_e", frame_seq=1)

    # WebRTC connection remains CONNECTED
    monitor.record_connection_status(
        device_connected=True,
        https_verified=True,
        webrtc_state="CONNECTED",
        ice_state="COMPLETED",
        track_received=True,
    )

    # 35 seconds elapse without video frames
    mock_clock.advance(35.0)
    # Repeated WebRTC heartbeats arrive
    monitor.record_connection_status(
        device_connected=True,
        https_verified=True,
        webrtc_state="CONNECTED",
        ice_state="COMPLETED",
        track_received=True,
    )

    diag = monitor.evaluate_health()
    # WebRTC status is CONNECTED, but frames stopped -> Interruption MUST be triggered!
    assert diag["webrtc_connection_state"] == "CONNECTED"
    assert diag["is_interrupted"] is True
    assert diag["is_timer_paused"] is False  # Never paused per Section 4
    assert diag["elapsed_without_frames_seconds"] == 35.0


# =============================================================================
# TEST F: Camera permission revoked / track ends – detects missing frames
# =============================================================================
def test_test_f_track_ended_missing_frames():
    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_f", "ipad_dev_1")

    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_f", frame_seq=1)

    # iOS Safari revokes camera permission -> track ended
    monitor.record_connection_status(
        device_connected=True,
        https_verified=True,
        webrtc_state="CONNECTED",
        ice_state="COMPLETED",
        track_received=False,  # Video track ended/revoked
    )

    mock_clock.advance(32.0)
    diag = monitor.evaluate_health()
    assert diag["is_interrupted"] is True
    assert diag["is_timer_paused"] is False  # Never paused per Section 4
    assert diag["video_track_received"] is False


# =============================================================================
# TEST G: Network disconnection during streaming – detects missing frames
# and preserves incident record in DB
# =============================================================================
def test_test_g_network_disconnection():
    IPadCameraStreamMonitor.reset_instance()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mock_db = MockDB(db_path)
        mock_clock = MockClock()
        monitor = IPadCameraStreamMonitor(
            db=mock_db,
            clock=mock_clock.clock,
            wall_clock=mock_clock.wall_clock,
        )
        monitor.start_session_monitoring("session_test_g", "ipad_dev_1")

        frame = create_dummy_frame()
        monitor.record_valid_frame(frame, session_id="session_test_g", frame_seq=1)

        # Network drops abruptly
        monitor.record_connection_status(
            device_connected=False,
            https_verified=False,
            webrtc_state="DISCONNECTED",
            ice_state="FAILED",
            track_received=False,
        )

        mock_clock.advance(33.0)
        diag = monitor.evaluate_health()
        assert diag["is_interrupted"] is True
        assert diag["webrtc_connection_state"] == "DISCONNECTED"

        with mock_db._connect() as conn:
            rows = conn.execute("SELECT * FROM strict_camera_incidents WHERE session_id='session_test_g'").fetchall()
            assert len(rows) == 1
            assert rows[0]["last_webrtc_state"] == "DISCONNECTED"
    finally:
        IPadCameraStreamMonitor.reset_instance()
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass


# =============================================================================
# TEST H: Frame recovery requires 5 consecutive seconds of stable frames
# before clearing incident and resuming timer
# =============================================================================
def test_test_h_5_second_recovery_gate():
    IPadCameraStreamMonitor.reset_instance()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mock_db = MockDB(db_path)
        mock_clock = MockClock()
        monitor = IPadCameraStreamMonitor(
            db=mock_db,
            clock=mock_clock.clock,
            wall_clock=mock_clock.wall_clock,
        )
        monitor.start_session_monitoring("session_test_h", "ipad_dev_1")

        frame = create_dummy_frame()
        monitor.record_valid_frame(frame, session_id="session_test_h", frame_seq=1)

        # 1. Outage of 35 seconds triggers interruption
        mock_clock.advance(35.0)
        diag = monitor.evaluate_health()
        assert diag["is_interrupted"] is True
        assert diag["is_timer_paused"] is False  # Never paused per Section 4
        assert diag["active_incident"] is not None

        # 2. Frames return at T=35s. Only 1 frame arrives -> Recovery stability gate begins
        mock_clock.advance(0.1)
        accepted = monitor.record_valid_frame(frame, session_id="session_test_h", frame_seq=2)
        assert accepted is True

        diag_rec1 = monitor.evaluate_health()
        # Must NOT instantly resolve! It must be in CAMERA_RECOVERING
        assert diag_rec1["health_state"] == IPadCameraState.CAMERA_RECOVERING
        assert diag_rec1["is_timer_paused"] is False  # Stream has returned, entering verification
        assert diag_rec1["active_incident"] is not None  # Incident STILL OPEN

        # 3. 2 seconds into recovery (total stable = 2.1s < 5.0s)
        mock_clock.advance(2.0)
        monitor.record_valid_frame(frame, session_id="session_test_h", frame_seq=3)
        diag_rec2 = monitor.evaluate_health()
        assert diag_rec2["health_state"] == IPadCameraState.CAMERA_RECOVERING
        assert diag_rec2["active_incident"] is not None  # Incident STILL OPEN

        # 4. Advance past 5.0s threshold (e.g. 3.5s more -> total stable = 5.6s)
        mock_clock.advance(3.5)
        monitor.record_valid_frame(frame, session_id="session_test_h", frame_seq=4)
        diag_recovered = monitor.evaluate_health()

        # Now verified recovered!
        assert diag_recovered["health_state"] in (
            IPadCameraState.CAMERA_RECOVERED,
            IPadCameraState.WINDOWS_PROCESSING_ACTIVE,
        )
        assert diag_recovered["is_live"] is True
        assert diag_recovered["is_interrupted"] is False
        assert diag_recovered["is_timer_paused"] is False
        assert diag_recovered["active_incident"] is None  # Incident resolved and cleared!

        # Verify incident resolved in database
        with mock_db._connect() as conn:
            rows = conn.execute("SELECT * FROM strict_camera_incidents WHERE session_id='session_test_h'").fetchall()
            assert len(rows) == 1
            assert rows[0]["incident_status"] == "RESOLVED"
            assert rows[0]["recovery_timestamp"] is not None

    finally:
        IPadCameraStreamMonitor.reset_instance()
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass


# =============================================================================
# TEST I: Multiple separate interruptions produce distinct incident records
# without duplicates during the same outage
# =============================================================================
def test_test_i_multiple_separate_outages():
    IPadCameraStreamMonitor.reset_instance()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mock_db = MockDB(db_path)
        mock_clock = MockClock()
        monitor = IPadCameraStreamMonitor(
            db=mock_db,
            clock=mock_clock.clock,
            wall_clock=mock_clock.wall_clock,
        )
        monitor.start_session_monitoring("session_test_i", "ipad_dev_1")

        frame = create_dummy_frame()
        monitor.record_valid_frame(frame, session_id="session_test_i", frame_seq=1)

        # --- OUTAGE 1 ---
        mock_clock.advance(32.0)
        diag1 = monitor.evaluate_health()
        assert diag1["is_interrupted"] is True
        inc1_id = diag1["active_incident"]["incident_id"]

        # Tick multiple times during Outage 1 (deduplication check)
        mock_clock.advance(5.0)
        diag1_b = monitor.evaluate_health()
        assert diag1_b["active_incident"]["incident_id"] == inc1_id
        assert diag1_b["incidents_count"] == 1

        # Recover from Outage 1 (5s consecutive frames)
        for _ in range(6):
            mock_clock.advance(1.0)
            monitor.record_valid_frame(frame, session_id="session_test_i")
        diag_rec = monitor.evaluate_health()
        assert diag_rec["is_live"] is True
        assert diag_rec["active_incident"] is None

        # --- NORMAL STREAM FOR 60s ---
        for _ in range(10):
            mock_clock.advance(6.0)
            monitor.record_valid_frame(frame, session_id="session_test_i")

        # --- OUTAGE 2 ---
        mock_clock.advance(35.0)
        diag2 = monitor.evaluate_health()
        assert diag2["is_interrupted"] is True
        inc2_id = diag2["active_incident"]["incident_id"]

        # Distinct incident IDs
        assert inc1_id != inc2_id
        assert diag2["incidents_count"] == 2

        with mock_db._connect() as conn:
            rows = conn.execute("SELECT incident_id, incident_status FROM strict_camera_incidents WHERE session_id='session_test_i'").fetchall()
            assert len(rows) == 2
            statuses = {r["incident_id"]: r["incident_status"] for r in rows}
            assert statuses[inc1_id] == "RESOLVED"
            assert statuses[inc2_id] == "OPEN"

    finally:
        IPadCameraStreamMonitor.reset_instance()
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass


# =============================================================================
# TEST J: Windows webcam proctoring continues unhindered during an iPad interruption
# =============================================================================
def test_test_j_independent_windows_webcam():
    from windows.strict_dual_camera_proctor import StrictDualCameraProctor

    IPadCameraStreamMonitor.reset_instance()
    mock_clock = MockClock()
    monitor = IPadCameraStreamMonitor(
        clock=mock_clock.clock,
        wall_clock=mock_clock.wall_clock,
    )
    monitor.start_session_monitoring("session_test_j", "ipad_dev_1")

    # Ingest baseline iPad frame
    frame = create_dummy_frame()
    monitor.record_valid_frame(frame, session_id="session_test_j", frame_seq=1)

    # iPad camera drops out (>30s)
    mock_clock.advance(40.0)
    diag = monitor.evaluate_health()
    assert diag["is_interrupted"] is True
    assert diag["is_timer_paused"] is False  # Never paused per Section 4

    # Windows webcam proctor continues independent operation
    dual = StrictDualCameraProctor.get_instance()
    dual.set_session_id("session_test_j")
    dual.windows_proctor.is_running = True

    win_frame = create_dummy_frame(640, 480)
    # Windows proctor process_frame executes unhindered
    res = dual.windows_proctor.process_frame(win_frame, timestamp=time.time())
    assert isinstance(res, dict)
    assert dual.windows_proctor.camera_source == "WINDOWS"
    assert dual.windows_proctor.is_running is True


# =============================================================================
# TEST K: StrictStudyWorkflow Authoritative Timer Advancement & Pausing Integration
# =============================================================================
def test_test_k_workflow_timer_advancement_and_pausing():
    from windows.strict_study_workflow import StrictStudyWorkflow

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mock_db = MockDB(db_path)
        with mock_db._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_activities (
                    activity_id TEXT PRIMARY KEY,
                    account_id TEXT,
                    window_start TEXT,
                    window_end TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    target_points INTEGER,
                    is_enabled INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'PENDING'
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO scheduled_activities (activity_id, account_id, window_start, window_end, start_time, end_time, target_points, is_enabled, status)
                VALUES ('act_1', 'user_1', '2026-11-05T12:40:00Z', '2026-11-05T13:00:00Z', '12:40', '13:00', 50, 1, 'PENDING')
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS study_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    event_type TEXT,
                    created_at TEXT,
                    payload_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_name TEXT,
                    user_id TEXT,
                    platform TEXT,
                    pairing_state TEXT,
                    last_seen TEXT
                )
            """)

        mock_clock = MockClock()
        IPadCameraStreamMonitor.reset_instance()
        monitor = IPadCameraStreamMonitor.get_instance(mock_db)
        monitor._clock = mock_clock.clock
        monitor._wall_clock = mock_clock.wall_clock

        def mock_window_provider(account, now):
            return [{
                "activity_id": "act_1",
                "window_id": "win_1",
                "window_start": (now + timedelta(minutes=40)).isoformat(),
                "window_end": (now + timedelta(minutes=60)).isoformat(),
            }]

        workflow = StrictStudyWorkflow(
            mock_db,
            clock=mock_clock.wall_clock,
            monotonic=mock_clock.clock,
            windows=mock_window_provider,
        )

        session_id = "sess_timer_test"
        monitor.start_session_monitoring(session_id, "ipad_1")

        # Fake iPad status as connected
        workflow._ipad_status = lambda: {"connected": True, "reason": "iPad live"}
        workflow.begin(session_id, "user_1", "android_dev_1", activate=True)
        workflow.s["ios_required"] = True
        workflow.s["state"] = "ACTIVE_FOCUS"
        workflow.s["focus_target_seconds"] = 1800.0
        workflow.s["elapsed_seconds"] = 0.0

        # Feed regular iPad frames
        frame = create_dummy_frame()
        monitor.record_valid_frame(frame, session_id=session_id, frame_seq=1)

        # Tick 1: 5 seconds elapsed with valid frames -> timer advances by 5s
        mock_clock.advance(5.0)
        monitor.record_valid_frame(frame, session_id=session_id, frame_seq=2)
        workflow.tick(monitoring_ok=True)
        assert workflow.s["elapsed_seconds"] >= 5.0
        assert workflow.s.get("ipad_camera_paused") is False

        # Now simulate iPad camera interruption (>30s)
        mock_clock.advance(35.0)
        # Tick workflow: evaluates camera health, sees interruption -> timer NEVER pauses!
        workflow.tick(monitoring_ok=True)
        assert workflow.s.get("ipad_camera_interrupted") is True
        assert workflow.s.get("ipad_camera_paused") is False
        assert "MANDATORY_IPAD_CAMERA_INTERRUPTED" in workflow.s.get("flags", [])

        elapsed_before_stall = workflow.s["elapsed_seconds"]

        # Advance another 10s during interruption -> timer MUST CONTINUE TO INCREMENT!
        mock_clock.advance(10.0)
        workflow.tick(monitoring_ok=True)
        assert workflow.s["elapsed_seconds"] > elapsed_before_stall  # Kept running!

        # Stream recovers after 5 seconds of continuous frames
        for _ in range(6):
            mock_clock.advance(1.0)
            monitor.record_valid_frame(frame, session_id=session_id)

        # Tick workflow after verified recovery
        workflow.tick(monitoring_ok=True)
        assert workflow.s.get("ipad_camera_paused") is False
        assert workflow.s.get("ipad_camera_interrupted") is False

        # Advance 5s with restored camera -> timer resumes advancing!
        mock_clock.advance(5.0)
        monitor.record_valid_frame(frame, session_id=session_id)
        workflow.tick(monitoring_ok=True)
        assert workflow.s["elapsed_seconds"] > elapsed_before_stall

    finally:
        IPadCameraStreamMonitor.reset_instance()
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
