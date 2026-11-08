import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from PIL import Image

from core.bluetooth_transfer import BluetoothTransferManager
from tests.bluetooth_fakes import FakeBackend, PEER
from core.database import StrictRecordsDB
from core.hourly_submission_service import HourlySubmissionService
from core.scoring_engine import ScoringEngine
from tests.test_daily_routine_gate import seed_approved_wakeup


class TestBluetoothAndSyncRepair(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="sr_bt_sync_test_")
        self.db_path = os.path.join(self.temp_dir, "test_db.sqlite")
        self.db = StrictRecordsDB(self.db_path)
        self.engine = ScoringEngine(self.db)
        self.service = HourlySubmissionService(self.db, self.engine)
        self.bt_manager = BluetoothTransferManager(self.db, self.engine, backend_factory=FakeBackend)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        self.bt_manager.cancel_session()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sync_events_batch_score_event_and_proof_approved(self):
        """Authoritative points from Android must be committed to score_events and reflected in daily score."""
        self.db.ensure_daily_record(self.today, user_id="user_lok")
        initial_score = int(self.db.get_daily_record(self.today, user_id="user_lok", is_dev_mode=False).get("final_score", 0))

        events = [
            {
                "event_id": "evt_score_sync_001",
                "event_type": "SCORE_EVENT",
                "timestamp": f"{self.today} 10:15:00",
                "payload": {
                    "event_id": "se_android_001",
                    "date": self.today,
                    "event_type": "PROOF_APPROVED",
                    "points": 8,
                    "description": "Admin approved proof for Hourly Checkpoint (+8)",
                    "audit": {
                        "evidence_id": "ev_phone_001",
                        "activity_id": f"act_{self.today}_10",
                        "approval_source": "ANDROID_ADMIN",
                        "task_code": "HOURLY",
                    },
                },
            }
        ]

        res = self.db.process_sync_events_batch(device_id="android_phone_1", events=events, account_id="user_lok")
        self.assertTrue(res["success"])
        self.assertIn("evt_score_sync_001", res["processed_event_ids"])

        # Check score events table
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM score_events WHERE event_id = 'se_android_001'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["points"], 8)
            self.assertEqual(row["event_type"], "PROOF_APPROVED")

        # Check recalculated score
        updated_rec = self.db.get_daily_record(self.today, user_id="user_lok", is_dev_mode=False)
        new_score = int(updated_rec.get("final_score", 0))
        self.assertEqual(new_score, initial_score + 8)

        # Re-processing same event should be idempotent
        res2 = self.db.process_sync_events_batch(device_id="android_phone_1", events=events, account_id="user_lok")
        self.assertTrue(res2["success"])
        updated_rec2 = self.db.get_daily_record(self.today, user_id="user_lok", is_dev_mode=False)
        self.assertEqual(int(updated_rec2.get("final_score", 0)), new_score)

    def test_unauthorized_negative_deduction_rejected(self):
        """Unauthorized negative deductions from secondary sources must NOT modify the ledger."""
        self.db.ensure_daily_record(self.today, user_id="user_lok")
        events = [
            {
                "event_id": "evt_unauth_deduction_001",
                "event_type": "SCORE_EVENT",
                "timestamp": f"{self.today} 11:00:00",
                "payload": {
                    "event_id": "se_unauth_001",
                    "date": self.today,
                    "event_type": "PENALTY",
                    "points": -15,
                    "description": "Unauthorized client penalty",
                    "audit": {"approval_source": "UNAUTHORIZED_SOURCE"},
                },
            }
        ]
        self.db.process_sync_events_batch(device_id="android_phone_1", events=events, account_id="user_lok")
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM score_events WHERE event_id = 'se_unauth_001'").fetchone()
            self.assertIsNone(row, "Unauthorized negative points must not be added to score_events")

    def test_bluetooth_session_lifecycle_and_import(self):
        """Explicit peer, v2 manifest, checksum and existing ledger; fake radio only."""
        from tests.test_bluetooth_transfer_v2 import package, DEVICE
        session = self.bt_manager.start_session(timeout_seconds=180, peer_address=PEER)
        self.assertTrue(session["success"])
        code = session["session_code"]
        self.assertEqual(len(code), 6)
        payload = package()
        raw = json.dumps(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        def receive(pin, checksum):
            return self.bt_manager.import_payload(pin,payload,checksum,DEVICE,raw,peer_address=PEER)
        self.assertEqual(receive("000000",digest)["code"],"AUTHENTICATION_FAILED")
        self.assertEqual(receive(code,"corrupt")["code"],"INTEGRITY_CHECK_FAILED")
        result = receive(code,digest)
        self.assertTrue(result["success"])
        self.assertEqual(result["transferred_count"],1)
        self.assertEqual(self.bt_manager.get_status()["state"],"COMMITTED")
        # The stream handler closes after sending the committed receipt.
        self.bt_manager.cancel_session()
        self.assertFalse(self.bt_manager.get_status()["active_session"])

    def test_hourly_resubmission_during_open_window(self):
        """Allow submitting another proof during an open window without DUPLICATE_SUBMISSION error."""
        now = datetime.now()
        act_id = f"act_{self.today}_test_open"
        w_start = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        w_end = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")

        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, date, scheduled_time,
                    window_start, window_end, notification_time, type, status, points, details_json
                ) VALUES (?, 'user_lok', ?, ?, ?, ?, ?, 'HOURLY', 'UPCOMING', 0, '{"hour_slot":"10:00-11:00"}')
                """,
                (act_id, self.today, w_start, w_start, w_end, w_start),
            )

        # Ensure wake-up gate is active
        seed_approved_wakeup(self.db, self.engine, self.today)

        buf = io.BytesIO()
        Image.new("RGB", (120, 120), (50, 100, 150)).save(buf, format="JPEG")
        sample_photo = buf.getvalue()

        # First submission
        res1 = self.service.accept(
            payload={
                "activity_id": act_id,
                "category": "CODING",
                "description": "First proof submission",
                "submission_id": "sub_test_open_1",
            },
            file_bytes=sample_photo,
            filename="proof1.jpg",
            account_id="user_lok",
            device_id="test_device",
            platform="WINDOWS",
            received_at=now,
        )
        self.assertTrue(res1["accepted"])

        # Second submission within the same open window with allow_resubmission=True
        res2 = self.service.accept(
            payload={
                "activity_id": act_id,
                "category": "CODING",
                "description": "Second updated proof submission",
                "submission_id": "sub_test_open_2",
                "allow_resubmission": True,
            },
            file_bytes=sample_photo,
            filename="proof2.jpg",
            account_id="user_lok",
            device_id="test_device",
            platform="WINDOWS",
            received_at=now,
        )
        self.assertTrue(res2["accepted"], "Second submission in open window must be accepted")


if __name__ == "__main__":
    unittest.main()
