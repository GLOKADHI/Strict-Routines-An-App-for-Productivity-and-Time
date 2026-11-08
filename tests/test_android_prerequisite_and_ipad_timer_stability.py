import time
import sqlite3
import pytest
from unittest.mock import MagicMock
from core.strict_ipad_connection import ipad_connection
from windows.strict_session_manager import StrictSessionManager, StrictSessionState, StrictLifecycleState
from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor, IPadCameraState


class _InMemoryDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def _connect(self):
        return self.conn


@pytest.fixture
def test_db():
    db = _InMemoryDB()
    with db._connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                user_id TEXT,
                device_name TEXT,
                platform TEXT,
                pairing_state TEXT,
                last_seen REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strict_preparations (
                preparation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                created REAL NOT NULL,
                status TEXT NOT NULL,
                host_json TEXT NOT NULL DEFAULT '{}',
                host_seen REAL NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strict_join_participants (
                preparation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                credential_hash TEXT NOT NULL,
                seen REAL NOT NULL,
                report_json TEXT NOT NULL,
                PRIMARY KEY(preparation_id, role)
            )
        """)
    return db


def test_android_8_checklist_items_passed(test_db):
    with test_db._connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_id, user_id, device_name, platform, pairing_state, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            ("android_test_123", "user_1", "Samsung Galaxy A55", "ANDROID", "PAIRED", time.time())
        )

    mgr = StrictSessionManager.__new__(StrictSessionManager)
    mgr._lock = MagicMock()
    mgr._lock.__enter__ = MagicMock(return_value=None)
    mgr._lock.__exit__ = MagicMock(return_value=None)
    mgr.user_id = "user_1"
    mgr.db = test_db
    mgr._active_preparation = "prep_abc"
    mgr._android_prepare_ack = None
    mgr._android_prepare_req_mono = time.monotonic()
    mgr._android_prepare_state = "ANDROID_AUTHENTICATING"

    ack_payload = {
        "event_type": "ANDROID_STRICT_READY_ACK",
        "device_id": "android_test_123",
        "preparation_id": "prep_abc",
        "apk_version": "2.5.0",
        "authenticated": True,
        "backend_connected": True,
        "transport_connected": True,
        "usage_access_granted": True,
        "usage_monitor_available": True,
        "monitoring_service_startable": True,
        "permissions_required": [],
        "readiness_status": "READY",
        "acknowledgement_timestamp": "2026-11-07T20:00:00Z",
        "error_details": "",
    }

    result = mgr.ack_android_prepare(ack_payload)
    assert result["success"] is True
    assert result["state"] == "ANDROID_READY_ACKNOWLEDGED"

    status = mgr.get_android_prerequisite_status(preparation_id="prep_abc")
    assert status["ready"] is True
    assert status["registered"] is True
    assert status["connected"] is True
    assert "checks" in status

    checks = status["checks"]
    assert len(checks) == 8
    assert checks["registered"]["state"] == "PASSED"
    assert checks["authenticated"]["state"] == "PASSED"
    assert checks["backend_connected"]["state"] == "PASSED"
    assert checks["transport_connected"]["state"] == "PASSED"
    assert checks["usage_access"]["state"] == "PASSED"
    assert checks["monitoring_service"]["state"] == "PASSED"
    assert checks["apk_compatibility"]["state"] == "PASSED"
    assert checks["readiness_ack"]["state"] == "PASSED"


def test_android_usage_access_missing_shows_permission_required(test_db):
    with test_db._connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_id, user_id, device_name, platform, pairing_state, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            ("android_test_456", "user_1", "Pixel 8", "ANDROID", "PAIRED", time.time())
        )

    mgr = StrictSessionManager.__new__(StrictSessionManager)
    mgr._lock = MagicMock()
    mgr._lock.__enter__ = MagicMock(return_value=None)
    mgr._lock.__exit__ = MagicMock(return_value=None)
    mgr.user_id = "user_1"
    mgr.db = test_db
    mgr._active_preparation = "prep_usage"
    mgr._android_prepare_ack = None
    mgr._android_prepare_req_mono = time.monotonic()
    mgr._android_prepare_state = "ANDROID_AUTHENTICATING"

    ack_payload = {
        "event_type": "ANDROID_STRICT_READY_ACK",
        "device_id": "android_test_456",
        "preparation_id": "prep_usage",
        "apk_version": "2.5.0",
        "authenticated": True,
        "backend_connected": True,
        "transport_connected": True,
        "usage_access_granted": False,
        "usage_monitor_available": True,
        "monitoring_service_startable": True,
        "permissions_required": ["PACKAGE_USAGE_STATS"],
        "readiness_status": "PERMISSION_REQUIRED",
        "acknowledgement_timestamp": "2026-11-07T20:00:00Z",
        "error_details": "Usage Access (PACKAGE_USAGE_STATS) permission required.",
    }

    result = mgr.ack_android_prepare(ack_payload)
    assert result["success"] is False
    assert result["state"] == "ANDROID_PERMISSION_REQUIRED"

    status = mgr.get_android_prerequisite_status(preparation_id="prep_usage")
    assert status["ready"] is False
    assert status["connected"] is True
    assert status["status_label"] == "ANDROID CONNECTED – USAGE ACCESS REQUIRED"

    checks = status["checks"]
    assert checks["usage_access"]["state"] == "PERMISSION_REQUIRED"
    assert checks["usage_access"]["ok"] is False
    assert checks["registered"]["state"] == "PASSED"
    assert checks["authenticated"]["state"] == "PASSED"
    assert checks["backend_connected"]["state"] == "PASSED"
    assert checks["transport_connected"]["state"] == "PASSED"


def test_ipad_connection_continuity_across_status_change(test_db):
    import json
    now = time.time()
    with test_db._connect() as conn:
        conn.execute(
            "INSERT INTO strict_preparations (preparation_id, account_id, created, status, expires_at) VALUES (?, ?, ?, ?, ?)",
            ("prep_done", "user_1", now - 100, "COMPLETED", now + 3600)
        )
        conn.execute(
            "INSERT INTO strict_join_participants (preparation_id, role, credential_hash, seen, report_json) VALUES (?, ?, ?, ?, ?)",
            ("prep_done", "IOS", "cred_hash_123", now - 5, json.dumps({"timer_ready": True, "online": True, "screen_sharing": True}))
        )

    conn_info = ipad_connection(test_db, "prep_done", "user_1", "cred_hash_123", now)
    assert conn_info["connected"] is True
    assert conn_info["age_seconds"] <= 10.0
    assert conn_info["screen_sharing"] is True


def test_camera_arriving_at_10_5_fps_not_interrupted():
    IPadCameraStreamMonitor.reset_instance()
    mon = IPadCameraStreamMonitor.get_instance()
    mon.received_fps = 10.5
    mon.processing_fps = 0.0

    diag = mon.evaluate_health()
    assert diag["health_state"] != IPadCameraState.CAMERA_INTERRUPTED
    assert mon.health_state != IPadCameraState.CAMERA_INTERRUPTED


def test_paired_android_auto_resolves_when_ack_pending(test_db):
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    with test_db._connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_id, user_id, device_name, platform, pairing_state, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            ("android_paired_live", "user_1", "Samsung Galaxy A55", "ANDROID", "PAIRED", now_iso)
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS device_credentials (credential_id TEXT, device_id TEXT, account_id TEXT, revoked_at TEXT)"
        )
        conn.execute(
            "INSERT INTO device_credentials (credential_id, device_id, account_id, revoked_at) VALUES (?, ?, ?, NULL)",
            ("cred_live_1", "android_paired_live", "user_1")
        )

    mgr = StrictSessionManager.__new__(StrictSessionManager)
    mgr._lock = MagicMock()
    mgr._lock.__enter__ = MagicMock(return_value=None)
    mgr._lock.__exit__ = MagicMock(return_value=None)
    mgr.user_id = "user_1"
    mgr.db = test_db
    mgr._active_preparation = "prep_live_resilience"
    mgr._android_prepare_ack = None
    mgr._android_prepare_req_mono = 0.0
    mgr._android_prepare_state = "ANDROID_AUTHENTICATING"
    mgr.request_android_prepare_check = MagicMock(return_value={})

    status = mgr.get_android_prerequisite_status(preparation_id="prep_live_resilience")
    assert status["ready"] is True
    assert status["state"] == "ANDROID_READY_ACKNOWLEDGED"
    assert status["checks"]["registered"]["state"] == "PASSED"
    assert status["checks"]["authenticated"]["state"] == "PASSED"
    assert status["checks"]["backend_connected"]["state"] == "PASSED"
    assert status["checks"]["transport_connected"]["state"] == "PASSED"
    assert status["checks"]["usage_access"]["state"] == "PASSED"
    assert status["checks"]["monitoring_service"]["state"] == "PASSED"
    assert status["checks"]["apk_compatibility"]["state"] == "PASSED"
    assert status["checks"]["readiness_ack"]["state"] == "PASSED"
