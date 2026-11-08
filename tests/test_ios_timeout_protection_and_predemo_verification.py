"""
Unit & Integration tests for StrictRecords iOS Session Timeout Protection
and Mandatory Pre-Demo Media Verification Suite.
"""

import time
import pytest
import sqlite3
import numpy as np
from datetime import datetime, timezone

from windows.strict_study_workflow import StrictStudyWorkflow
from windows.strict_demo_workflow import DemoStudyWorkflow as StrictDemoWorkflow
from windows.strict_session_manager import StrictSessionManager, StrictSessionState, StrictLifecycleState
from windows.strict_ios_media_health import (
    IOSMediaHealthManager,
    IOSMediaHealthClassification,
    SOURCE_FRONT_CAMERA,
    SOURCE_SCREEN_SHARE,
)
from windows.strict_predemo_verifier import IOSPreDemoVerifier
from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor, IPadCameraState
from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor


class _MockDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _connect(self):
        return self.conn

    def _init_schema(self):
        with self.conn as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS strict_preparations (
                    preparation_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    created REAL NOT NULL,
                    status TEXT NOT NULL,
                    host_json TEXT NOT NULL DEFAULT '{}',
                    host_seen REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS strict_join_participants (
                    preparation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    client_nonce_hash TEXT NOT NULL,
                    joined_at REAL NOT NULL,
                    seen REAL NOT NULL,
                    report_json TEXT NOT NULL,
                    credential_hash TEXT NOT NULL,
                    client_kind TEXT NOT NULL DEFAULT 'BROWSER',
                    PRIMARY KEY (preparation_id, role)
                );
                CREATE TABLE IF NOT EXISTS strict_join_invitations (
                    preparation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    secret_token_hash TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    claimed_at REAL,
                    client_fingerprint TEXT,
                    claim_nonce_hash TEXT NOT NULL DEFAULT '',
                    revoked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (preparation_id, role)
                );
                CREATE TABLE IF NOT EXISTS strict_mobile_integrity_state (
                    preparation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    state TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (preparation_id, role)
                );
                CREATE TABLE IF NOT EXISTS ipad_camera_outages (
                    incident_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    event_type TEXT,
                    interruption_duration REAL,
                    last_webrtc_state TEXT,
                    last_received_fps REAL,
                    last_valid_frame_timestamp TEXT,
                    interruption_detected_timestamp TEXT,
                    interruption_start_time TEXT,
                    resumed_timestamp TEXT,
                    is_active INTEGER,
                    recovery_latency_sec REAL,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_activities (
                    activity_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    is_enabled INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'SCHEDULED',
                    schedule_version INTEGER DEFAULT 1,
                    window_start TEXT,
                    window_end TEXT
                );
                CREATE TABLE IF NOT EXISTS proof_submission_events (
                    account_id TEXT NOT NULL,
                    activity_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    email TEXT,
                    display_name TEXT
                );
            """)
            c.execute("INSERT OR REPLACE INTO user_profiles VALUES ('user_lok', 'lok@test.com', 'Lok')")
            c.execute("INSERT OR REPLACE INTO scheduled_activities VALUES ('act_1', 'user_lok', 1, 'SCHEDULED', 1, '2026-11-07T12:00:00Z', '2026-11-07T13:00:00Z')")

    def get_active_user_profile(self):
        return {"user_id": "user_lok", "email": "lok@test.com"}


@pytest.fixture
def mock_db():
    return _MockDB()


# --- TEST 1: iOS camera interrupted -> Session timer continues, session NOT paused ---
def test_ios_camera_interruption_does_not_pause_session_timer(mock_db):
    clock_mono = 1000.0
    clock_wall = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)

    demo = StrictDemoWorkflow(mock_db, clock=lambda: clock_wall, monotonic=lambda: clock_mono)
    demo.s = {
        'state': 'ACTIVE_FOCUS',
        'session_id': 'demo_session_123',
        'account_id': 'user_lok',
        'elapsed_seconds': 100.0,
        'monitoring_ok': True,
        'ios_required': True,
        'ios_preparation_id': 'prep_123',
        'ios_binding_hash': 'hash_123',
    }
    demo._last_mono = clock_mono

    # Advance time by 5 seconds while iPad connection drops
    clock_mono += 5.0
    res = demo.tick(monitoring_ok=True)

    # Timer must have advanced by 5 seconds (100 -> 105)
    assert res['elapsed_seconds'] == 105.0
    assert "session continues" in res['ipad_connection_message']
    assert res['state'] == 'ACTIVE_FOCUS'


# --- TEST 2: iOS screen share drop -> Front camera & session timer continue ---
def test_ios_screen_share_drop_does_not_halt_session(mock_db):
    clock_mono = 2000.0
    clock_wall = datetime(2026, 11, 7, 12, 5, 0, tzinfo=timezone.utc)

    study = StrictStudyWorkflow(mock_db, clock=lambda: clock_wall, monotonic=lambda: clock_mono)
    study.s = {
        'state': 'ACTIVE_FOCUS',
        'session_id': 'study_session_456',
        'account_id': 'user_lok',
        'elapsed_seconds': 500.0,
        'monitoring_ok': True,
        'monitoring_resumed': True,
        'ios_required': True,
        'timing_rule': 'WINDOW_MINUS_FIVE_MINUTES',
        'focus_target_seconds': 2400.0,
        'window': {
            'activity_id': 'act_1',
            'window_start': datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
            'window_end': datetime(2026, 11, 7, 13, 0, 0, tzinfo=timezone.utc).isoformat(),
        }
    }
    study._last_mono = clock_mono
    study._scan_mono = clock_mono

    # Advance time by 10 seconds while iPad drops
    clock_mono += 10.0
    res = study.tick(monitoring_ok=True)

    # Elapsed seconds must increase by 10s
    assert res['elapsed_seconds'] == 510.0
    assert res['ipad_connection_paused'] is False
    assert "session continues" in res['ipad_connection_message']


# --- TEST 3: WebRTC blocked while heartbeat continues -> IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING ---
def test_webrtc_blocked_classified_accurately():
    mgr = IOSMediaHealthManager()
    mgr._heartbeats.clear()
    mgr._prev_heartbeats.clear()
    mgr._receiver_stats.clear()
    mgr._explicit_stops.clear()

    now = 5000.0
    # First baseline heartbeat
    mgr.record_heartbeat("IOS_FRONT_CAMERA", {
        "user_sharing_enabled": True,
        "captured_frames": 50,
        "transmitted_frames": 50,
        "capture_session_status": "running",
        "video_track_ready_state": "live",
    }, received_at=now - 10.0)

    # Next advancing heartbeat
    mgr.record_heartbeat("IOS_FRONT_CAMERA", {
        "user_sharing_enabled": True,
        "captured_frames": 100,
        "transmitted_frames": 100,
        "capture_session_status": "running",
        "video_track_ready_state": "live",
    }, received_at=now - 1.0)

    # But Windows receiver has rx_fps = 0 and rx_frames_count = 0
    mgr.record_receiver_stats(
        source_id="IOS_FRONT_CAMERA",
        rx_fps=0.0,
        rx_frames_count=0,
        decoded_fps=0.0,
        decoded_frames_count=0,
        last_rx_epoch=None,
    )

    cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
    # Must NOT report IOS_SHARING_STOPPED_BY_USER!
    assert cls == IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING
    assert "0 frames" in reason


# --- TEST 4: Windows video decoder stopped -> WINDOWS_DECODER_STALLED ---
def test_windows_decoder_stalled_classification():
    mgr = IOSMediaHealthManager()
    mgr._heartbeats.clear()
    mgr._receiver_stats.clear()
    mgr._explicit_stops.clear()

    now = 6000.0
    mgr.record_heartbeat("IOS_FRONT_CAMERA", {
        "user_sharing_enabled": True,
        "captured_frames": 200,
        "transmitted_frames": 200,
    }, received_at=now - 1.0)

    # Windows receiving packets at 15 FPS, but decoded FPS is 0
    mgr.record_receiver_stats(
        source_id="IOS_FRONT_CAMERA",
        rx_fps=15.0,
        rx_frames_count=150,
        decoded_fps=0.0,
        decoded_frames_count=0,
        last_rx_epoch=now - 0.1,
    )

    cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
    assert cls == IOSMediaHealthClassification.WINDOWS_DECODER_STALLED
    assert "decoder stalled" in reason


# --- TEST 5: Windows inference stalled -> WINDOWS_PROCESSING_STALLED ---
def test_windows_inference_stalled_classification():
    mgr = IOSMediaHealthManager()
    mgr._heartbeats.clear()
    mgr._receiver_stats.clear()
    mgr._explicit_stops.clear()

    now = 7000.0
    mgr.record_heartbeat("IOS_FRONT_CAMERA", {
        "user_sharing_enabled": True,
        "captured_frames": 300,
        "transmitted_frames": 300,
    }, received_at=now - 1.0)

    # Windows receiving and decoding, but processing is 0
    mgr.record_receiver_stats(
        source_id="IOS_FRONT_CAMERA",
        rx_fps=15.0,
        rx_frames_count=300,
        decoded_fps=15.0,
        decoded_frames_count=300,
        processing_fps=0.0,
        last_rx_epoch=now - 0.1,
    )

    cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
    assert cls == IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED
    assert "processing stalled" in reason


# --- TEST 6: Missing heartbeat without explicit stop -> IOS_STATUS_UNKNOWN / DISCONNECTED ---
def test_missing_heartbeat_not_classified_as_user_stop():
    mgr = IOSMediaHealthManager()
    mgr._heartbeats.clear()
    mgr._receiver_stats.clear()
    mgr._explicit_stops.clear()

    now = 8000.0
    cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
    assert cls in (
        IOSMediaHealthClassification.IOS_STATUS_UNKNOWN,
        IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED,
    )
    assert cls != IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER


# --- TEST 7: Explicit user stop is correctly classified ONLY on confirmed event ---
def test_explicit_user_stop_classification():
    mgr = IOSMediaHealthManager()
    mgr._heartbeats.clear()
    mgr._receiver_stats.clear()
    mgr._explicit_stops.clear()

    now = 9000.0
    mgr.record_explicit_stop(SOURCE_FRONT_CAMERA, {
        "reason": "USER_STOPPED_CAMERA",
        "user_initiated": True,
        "timestamp": now - 1.0,
    })

    cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
    assert cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER
    assert "intentionally stopped" in reason


# --- TEST 8: Camera monitor does NOT classify as interrupted when received_fps > 0 ---
def test_camera_monitor_no_interruption_at_positive_fps(mock_db):
    IPadCameraStreamMonitor.reset_instance()
    cam = IPadCameraStreamMonitor.get_instance(mock_db)
    cam.session_id = "test_sess_active"
    cam.is_session_active = True
    cam.lifecycle_state = StrictLifecycleState.SESSION_ACTIVE
    cam.session_started_mono = 100.0
    cam.session_frame_count = 10
    cam.frame_sequence = 10
    cam.queued_frames_count = 3
    cam.session_last_processed_mono = 100.0
    cam.received_fps = 10.5
    cam.processing_fps = 0.0
    cam._frame_rx_times = [199.0, 199.5, 200.0]

    diag = cam.evaluate_health(now_mono=200.0, now_wall=datetime.now(timezone.utc))
    # Must NOT report is_interrupted = True when rx_fps is 10.5!
    assert diag["is_interrupted"] is False
    assert diag["health_state"] != IPadCameraState.CAMERA_INTERRUPTED
    assert diag["health_state"] == IPadCameraState.IOS_PROCESSING_STALLED


# --- TEST 9: Pre-Demo Verifier Report with 8 Authoritative Categories ---
def test_predemo_verifier_report_structure(mock_db):
    IOSPreDemoVerifier.reset_instance()
    verifier = IOSPreDemoVerifier.get_instance(mock_db)

    report = verifier.get_predemo_report()
    checklist = report["checklist"]

    expected_categories = [
        "IOS_FRONT_CAMERA",
        "IOS_FULL_SCREEN_SHARE",
        "SIMULTANEOUS_RECEPTION",
        "CONTROL_CHANNEL_HEARTBEAT",
        "WINDOWS_DECODING",
        "WINDOWS_PROCESSING_PIPELINE",
        "AUTOMATIC_RECOVERY",
        "SESSION_TIMEOUT_PROTECTION",
    ]
    for cat in expected_categories:
        assert cat in checklist, f"Missing category: {cat}"
        assert "passed" in checklist[cat]
        assert "status" in checklist[cat]
        assert checklist[cat]["status"] in ("PASS", "FAIL")

    assert "all_passed" in report
    assert "blockers" in report
    assert "summary_label" in report


# --- TEST 10: Lifecycle state stays ACTIVE during iOS media drop ---
def test_lifecycle_state_stays_active(mock_db):
    mgr = StrictSessionManager(mock_db)
    mgr.session_id = "sess_active_1"
    mgr.state = StrictSessionState.ACTIVE

    class MockStudy:
        def __init__(self):
            self.s = {
                'ipad_camera_paused': True,
                'ipad_connection_paused': True,
                'state': 'ACTIVE_FOCUS',
            }

    mgr.study = MockStudy()

    # Lifecycle state must stay SESSION_ACTIVE
    assert mgr.get_lifecycle_state() == StrictLifecycleState.SESSION_ACTIVE
