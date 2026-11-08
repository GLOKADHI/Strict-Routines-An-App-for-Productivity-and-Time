import os
import tempfile
import unittest
from datetime import datetime

from core.database import StrictRecordsDB


class DevicePolicyAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(db_path=os.path.join(self.temp_dir.name, "sync.db"))
        now = datetime.now().isoformat()
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    pairing_state, last_seen, sync_status, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "android-tablet", "user_lok", "Lenovo TB-X304L", "Android 8.1.0",
                    "1.11.0", "PAIRED", now, "ONLINE", "REGISTERED",
                ),
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_policy_receipt_is_confirmed_only_after_authenticated_version_ack(self):
        pending = self.db._get_android_policy_sync_status(
            "youtube", 8, True, True, False, "user_lok"
        )
        self.assertEqual(pending["status"], "PENDING_CONFIRMATION")
        self.assertFalse(pending["confirmed"])

        ack = self.db.record_device_restriction_policy_ack(
            "android-tablet",
            "youtube",
            {"policyVersion": 8, "requestedRestrictionEnabled": True},
        )
        self.assertTrue(ack["success"])
        confirmed = self.db._get_android_policy_sync_status(
            "youtube", 8, True, True, False, "user_lok"
        )
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["confirmedPolicyVersion"], 8)
        self.assertIsNone(confirmed["effectiveAccessAllowed"])

        stale = self.db.record_device_restriction_policy_ack(
            "android-tablet",
            "youtube",
            {"policyVersion": 7, "requestedRestrictionEnabled": False},
        )
        self.assertFalse(stale["success"])
        self.assertTrue(stale["staleRejected"])


if __name__ == "__main__":
    unittest.main()
