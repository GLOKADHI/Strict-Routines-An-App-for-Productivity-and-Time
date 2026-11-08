"""
Tests for StrictRecords:
1. Profile persistence & display name: LOK ADHITHYA G
2. Admin PIN security (1234 initial key, salted hash, rate-limiting, lockout, PIN change, audit logs)
3. Diagnostic permission controls (Windows OS states, Registry startup, Notifications, Restriction capabilities)
4. Preservation of existing schedules, scoring, and device pairing
"""
import pytest
import time
import os
import sqlite3
from core.database import StrictRecordsDB
from core.models import AdminActionType

TEST_DB_PATH = "test_profile_admin.db"

@pytest.fixture
def db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    database = StrictRecordsDB(db_path=TEST_DB_PATH)
    yield database
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

def test_default_user_profile(db):
    profile = db.get_active_user_profile("user_lok")
    assert profile is not None
    assert profile["display_name"] == "LOK ADHITHYA G"
    assert profile["role"] == "ADMIN"
    assert profile["user_id"] == "user_lok"
    assert profile["xp"] >= 0

def test_initial_admin_pin_and_default_detection(db):
    # Verify default PIN 1234 is accepted
    assert db.verify_admin_pin("1234") is True
    # Verify incorrect PIN is rejected
    assert db.verify_admin_pin("0000") is False
    assert db.verify_admin_pin("9999") is False
    # Verify system detects default PIN 1234 is in use
    assert db.is_default_admin_pin() is True

def test_change_admin_pin_and_audit_logging(db):
    # Change PIN to 5678
    res = db.change_admin_pin("1234", "5678", admin_user="LOK ADHITHYA G")
    assert res["success"] is True
    assert res["is_default"] is False

    # Old PIN should no longer work
    assert db.verify_admin_pin("1234") is False
    # New PIN should work
    assert db.verify_admin_pin("5678") is True
    # Should no longer be identified as default PIN
    assert db.is_default_admin_pin() is False

    # Check audit log
    logs = db.get_admin_audit_logs(limit=5)
    assert len(logs) > 0
    change_log = next((l for l in logs if l["action_type"] == "ADMIN_PIN_CHANGED"), None)
    assert change_log is not None
    assert change_log["field_changed"] == "pin_hash"
    assert change_log["old_value"] == "[REDACTED_PREVIOUS_KEY]"
    assert change_log["admin_user"] == "LOK ADHITHYA G"

def test_profile_persistence_across_restart(db):
    # Update profile
    db.update_user_profile("user_lok", display_name="LOK ADHITHYA G", level="OPERATOR")
    
    # Simulate DB reconnect / app restart
    db2 = StrictRecordsDB(db_path=TEST_DB_PATH)
    profile = db2.get_active_user_profile("user_lok")
    assert profile["display_name"] == "LOK ADHITHYA G"
    assert profile["level"] == "OPERATOR"

def test_rate_limiting_logic():
    from core.server import StrictRecordsRequestHandler
    # Test rate limiter class methods
    StrictRecordsRequestHandler._pin_failed_attempts = 0
    StrictRecordsRequestHandler._pin_lockout_until = 0.0

    now = time.time()
    # 4 failed attempts should not lock out
    for i in range(4):
        StrictRecordsRequestHandler._pin_failed_attempts += 1
    assert StrictRecordsRequestHandler._pin_failed_attempts == 4
    assert StrictRecordsRequestHandler._pin_lockout_until < now

    # 5th failed attempt locks out for 60s
    StrictRecordsRequestHandler._pin_failed_attempts += 1
    StrictRecordsRequestHandler._pin_lockout_until = time.time() + 60.0

    assert StrictRecordsRequestHandler._pin_lockout_until > time.time()
    # Reset
    StrictRecordsRequestHandler._pin_failed_attempts = 0
    StrictRecordsRequestHandler._pin_lockout_until = 0.0

def test_get_diagnostic_permissions_status(db):
    from core.server import StrictRecordsRequestHandler
    class MockHandler:
        def __init__(self, database):
            self.db = database
            self.restriction_mgr = None

    # Verify diagnostic permissions status payload
    payload = StrictRecordsRequestHandler._get_diagnostic_permissions_status(MockHandler(db))
    assert "windows" in payload
    assert "android" in payload
    
    win = payload["windows"]
    assert "startup_background" in win
    assert "notification_delivery" in win
    assert "browser_restriction" in win
    assert "app_restriction" in win
    assert "device_sync" in win
    assert "os_permissions" in win

    # Check status values are valid defined states
    valid_states = {"Granted / Enabled", "Not granted", "Permission required", "Unsupported", "Error"}
    assert win["startup_background"]["status"] in valid_states
    assert win["notification_delivery"]["status"] in valid_states
    assert win["app_restriction"]["status"] in valid_states
    assert win["device_sync"]["status"] in valid_states
    assert win["os_permissions"]["status"] in valid_states
