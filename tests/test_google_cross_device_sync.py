"""
StrictRecords — Test Suite for Google Account Identity & Real-Time Cross-Device Sync
Tests Android-Windows shared Google identity, Credential Manager flows,
EventBus real-time presence broadcasting, account isolation / conflict detection,
stale presence expiration, and preservation of PC pairing authorization.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from core.database import StrictRecordsDB
from core.event_bus import EventBus


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def mock_db(temp_dir):
    db_path = os.path.join(temp_dir, "test_strictrecords.db")
    return StrictRecordsDB(db_path)


class TestGoogleCrossDeviceSync:

    def test_register_google_device(self, mock_db):
        """Test registering a device under a verified Google UID."""
        res = mock_db.register_google_device(
            uid="google_uid_104857",
            email="testuser@gmail.com",
            display_name="Test User",
            device_id="android_a55_unit_test",
            device_name="Samsung Galaxy A55",
            platform="Android 14",
            app_version="1.18.1",
            pairing_state="PAIRED"
        )
        assert res["success"] is True
        assert res["uid"] == "google_uid_104857"
        assert res["device_id"] == "android_a55_unit_test"

        # Verify database record
        with mock_db._connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", ("google_uid_104857",)).fetchone()
            assert user is not None
            assert user["display_name"] == "Test User"
            assert user["username"] == "testuser@gmail.com"

            dev = conn.execute("SELECT * FROM devices WHERE device_id = ?", ("android_a55_unit_test",)).fetchone()
            assert dev is not None
            assert dev["user_id"] == "google_uid_104857"
            assert dev["platform"] == "Android 14"
            assert dev["pairing_state"] == "PAIRED"
            assert dev["status"] == "REGISTERED"

    def test_stale_device_presence_expiry(self, mock_db):
        """Test that device presence expires when last_seen exceeds the freshness threshold."""
        # 1. Fresh device (last_seen = now)
        mock_db.register_google_device(
            uid="user_fresh",
            email="fresh@gmail.com",
            display_name="Fresh User",
            device_id="android_fresh_dev",
            device_name="Samsung A55",
            platform="Android",
            app_version="1.18.1"
        )

        devices = mock_db.get_registered_devices_list()
        fresh_dev = next((d for d in devices if d["device_id"] == "android_fresh_dev"), None)
        assert fresh_dev is not None
        assert fresh_dev["is_online"] is True
        assert fresh_dev["presence_status"] == "ONLINE"

        # 2. Stale device (last_seen = 5 minutes ago)
        stale_time = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
        with mock_db._connect() as conn:
            conn.execute("UPDATE devices SET last_seen = ? WHERE device_id = ?", (stale_time, "android_fresh_dev"))
            conn.commit()

        devices = mock_db.get_registered_devices_list()
        stale_dev = next((d for d in devices if d["device_id"] == "android_fresh_dev"), None)
        assert stale_dev is not None
        assert stale_dev["is_online"] is False
        assert stale_dev["presence_status"] == "OFFLINE"

    def test_device_disconnect(self, mock_db):
        """Test marking a device offline upon explicit disconnect / sign out."""
        mock_db.register_google_device(
            uid="user_disc",
            email="disc@gmail.com",
            display_name="Disc User",
            device_id="android_disc_dev",
            device_name="Samsung A55",
            platform="Android",
            app_version="1.18.1"
        )

        ok = mock_db.disconnect_google_device("android_disc_dev")
        assert ok is True

        with mock_db._connect() as conn:
            dev = conn.execute("SELECT * FROM devices WHERE device_id = ?", ("android_disc_dev",)).fetchone()
            assert dev["status"] == "OFFLINE"

    def test_event_bus_publishing(self):
        """Test that EventBus correctly notifies subscribers of presence and identity updates."""
        bus = EventBus.get_instance()
        q = bus.subscribe()
        try:
            test_evt = {
                "event_type": "DEVICE_PRESENCE_UPDATE",
                "device_id": "test_dev_bus",
                "status": "ONLINE",
                "is_online": True,
                "uid": "uid_test",
                "email": "bus@test.com",
                "timestamp": datetime.now().isoformat()
            }
            bus.publish("DEVICE_PRESENCE_UPDATE", test_evt)

            received = q.get(timeout=2.0)
            assert received["event_type"] == "DEVICE_PRESENCE_UPDATE"
            assert received["device_id"] == "test_dev_bus"
            assert received["status"] == "ONLINE"
        finally:
            bus.unsubscribe(q)

    def test_account_isolation_no_silent_merge(self, mock_db):
        """Test that two different Google accounts register separate user records and separate devices."""
        mock_db.register_google_device(
            uid="google_uid_alpha",
            email="alpha@gmail.com",
            display_name="User Alpha",
            device_id="phone_alpha",
            device_name="Alpha Phone",
            platform="Android",
            app_version="1.18.1"
        )
        mock_db.register_google_device(
            uid="google_uid_beta",
            email="beta@gmail.com",
            display_name="User Beta",
            device_id="phone_beta",
            device_name="Beta Phone",
            platform="Android",
            app_version="1.18.1"
        )

        with mock_db._connect() as conn:
            users = conn.execute("SELECT * FROM users WHERE user_id IN ('google_uid_alpha', 'google_uid_beta')").fetchall()
            assert len(users) == 2
            emails = {u["username"] for u in users}
            assert emails == {"alpha@gmail.com", "beta@gmail.com"}

            dev_alpha = conn.execute("SELECT * FROM devices WHERE device_id = 'phone_alpha'").fetchone()
            dev_beta = conn.execute("SELECT * FROM devices WHERE device_id = 'phone_beta'").fetchone()
            assert dev_alpha["user_id"] == "google_uid_alpha"
            assert dev_beta["user_id"] == "google_uid_beta"
            assert dev_alpha["user_id"] != dev_beta["user_id"]
