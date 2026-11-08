"""Device online state is based on recent persisted heartbeat timestamps."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from core.database import StrictRecordsDB


class TestDevicePresenceFreshness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="device_presence_")
        self.db = StrictRecordsDB(os.path.join(self.temp_dir.name, "test.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stale_windows_and_android_heartbeats_are_offline(self):
        self.db.register_or_heartbeat_device("windows-old", "Old PC", "WINDOWS")
        self.db.register_or_heartbeat_device("android-old", "Old Phone", "ANDROID")
        stale_time = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
        with self.db._connect() as conn:
            conn.execute("UPDATE devices SET last_seen = ?, sync_status = 'ONLINE' WHERE device_id IN (?, ?)",
                         (stale_time, "windows-old", "android-old"))

        devices = {item["device_id"]: item for item in self.db.get_registered_devices_list()}
        self.assertEqual(devices["windows-old"]["presence_status"], "OFFLINE")
        self.assertEqual(devices["android-old"]["presence_status"], "OFFLINE")
        self.assertEqual(devices["windows-old"]["sync_status"], "OFFLINE")

    def test_recent_authenticated_registration_timestamp_is_online(self):
        self.db.register_or_heartbeat_device("windows-fresh", "Current PC", "WINDOWS")
        devices = {item["device_id"]: item for item in self.db.get_registered_devices_list()}
        self.assertEqual(devices["windows-fresh"]["presence_status"], "ONLINE")

    def test_future_timestamp_does_not_claim_presence(self):
        self.db.register_or_heartbeat_device("android-future", "Future Phone", "ANDROID")
        future_time = (datetime.now().astimezone() + timedelta(minutes=5)).isoformat(timespec="seconds")
        with self.db._connect() as conn:
            conn.execute("UPDATE devices SET last_seen = ?, sync_status = 'ONLINE' WHERE device_id = ?",
                         (future_time, "android-future"))
        device = next(item for item in self.db.get_registered_devices_list() if item["device_id"] == "android-future")
        self.assertEqual(device["presence_status"], "OFFLINE")


if __name__ == "__main__":
    unittest.main()
