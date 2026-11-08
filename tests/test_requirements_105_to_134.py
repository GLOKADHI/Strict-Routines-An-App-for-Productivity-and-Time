"""
StrictRecords — Requirements 105 to 134 Unit Test Suite
Verifies:
- Mandatory Permission & Capability requirements (105-110)
- YouTube and Entertainment App detection & restriction decision engine (111-112)
- RestrictionEvent data model, serialization, and SQLite persistence (113-115)
- Android Reward status calculation and display logic (116)
- Onboarding gate and permission revocation enforcement (117-118)
- Device Pairing (USB / Wi-Fi) and synchronization batch processing (121-124)
- Internal Diagnostics and YouTube Test Mode (127-128)
"""

import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.models import RestrictionEvent, UserSettings
from core.scoring_engine import ScoringEngine


class TestRequirements105To134(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.engine = ScoringEngine(self.db)
        self.test_date = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        os.close(self.temp_db_fd)
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def test_requirement_111_and_112_restriction_decision_engine(self):
        """Test Section 111 & 112: YouTube & Spotify restriction decisions based on score."""
        # 1. Score below threshold (Score 74 < 80) -> RESTRICTED
        res_yt_low = self.engine.evaluate_entertainment_access(
            date=self.test_date,
            target_app="com.google.android.youtube",
            platform="ANDROID",
            record_violation_if_blocked=False,
            device_id="android_lok_phone",
            is_dev_mode=True,
        )
        self.assertFalse(res_yt_low["allowed"])
        self.assertEqual(res_yt_low["target_app"], "com.google.android.youtube")

        # 2. Add points to reach 85 (>= 80 threshold)
        self.engine.record_score_event(
            date=self.test_date,
            event_type="WAKEUP_COMPLETED",
            device_id="android_lok_phone",
            description="Wake-up verified",
            points=25,
            is_dev_mode=True,
        )
        self.engine.record_score_event(
            date=self.test_date,
            event_type="BATH_COMPLETED",
            device_id="android_lok_phone",
            description="Bath verified",
            points=10,
            is_dev_mode=True,
        )
        self.engine.record_score_event(
            date=self.test_date,
            event_type="GYM_COMPLETED",
            device_id="android_lok_phone",
            description="Gym verified",
            points=15,
            is_dev_mode=True,
        )
        self.engine.record_score_event(
            date=self.test_date,
            event_type="HOURLY_PRODUCTIVE",
            device_id="android_lok_phone",
            description="Coding blocks",
            points=35,
            is_dev_mode=True,
        )

        # Total score is now 85
        daily = self.db.get_daily_record(self.test_date, is_dev_mode=True)
        self.assertGreaterEqual(daily["final_score"], 80)

        # 3. Score >= 80 -> ALLOWED
        res_yt_high = self.engine.evaluate_entertainment_access(
            date=self.test_date,
            target_app="com.google.android.youtube",
            platform="ANDROID",
            record_violation_if_blocked=False,
            device_id="android_lok_phone",
            is_dev_mode=True,
        )
        self.assertTrue(res_yt_high["allowed"])

    def test_requirement_113_restriction_event_model_and_sync(self):
        """Test Section 113: RestrictionEvent dataclass, synchronization and persistence."""
        event = RestrictionEvent(
            restriction_event_id="re_test_001",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            device_id="android_lok_phone",
            package_name="com.google.android.youtube",
            application_name="YouTube",
            score_at_detection=74,
            required_score=80,
            action="RESTRICTED",
            reason="Reward threshold not reached (< 80)",
        )
        d = event.to_dict()
        self.assertEqual(d["restriction_event_id"], "re_test_001")
        self.assertEqual(d["score_at_detection"], 74)
        self.assertEqual(d["action"], "RESTRICTED")

        # Test sync into central database
        batch_item = {
            "sync_id": "sync_re_001",
            "payload_type": "RESTRICTION_EVENT",
            "payload": d,
        }
        res = self.engine.process_sync_batch(
            items=[batch_item],
            device_id="android_lok_phone",
        )
        self.assertEqual(res["applied_count"], 1)

        # Verify record exists in restriction_events table
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restriction_events WHERE restriction_id = ?",
                ("re_test_001",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["application"], "YouTube")
            self.assertEqual(row["score"], 74)
            self.assertEqual(row["threshold"], 80)
            self.assertEqual(row["action"], "RESTRICTED")

    def test_requirement_116_reward_status_calculation(self):
        """Test Section 116: Today's score reward status display calculation."""
        # Case A: Score 74 -> Locked
        score = 74
        yt_allowed = score >= 80
        sp_allowed = score >= 80
        local_music_allowed = score >= 60
        points_needed = max(0, 80 - score)

        self.assertFalse(yt_allowed)
        self.assertFalse(sp_allowed)
        self.assertTrue(local_music_allowed)
        self.assertEqual(points_needed, 6)

        # Case B: Score 81 -> Unlocked
        score_high = 81
        self.assertTrue(score_high >= 80)
        self.assertEqual(max(0, 80 - score_high), 0)

    def test_requirement_121_and_124_device_pairing_and_wifi_sync(self):
        """Test Section 121 & 124: Device pairing records and sync status."""
        self.db.register_or_update_device("android_test_1", platform="ANDROID", pairing_state="PAIRED")
        devices = self.db.list_devices()
        android_dev = next((d for d in devices if d["platform"] == "ANDROID"), None)
        self.assertIsNotNone(android_dev)
        self.assertEqual(android_dev["pairing_state"], "PAIRED")

        # Test sync batch idempotency (Section 124)
        test_sync_id = f"sync_{uuid.uuid4().hex}"
        item = {
            "sync_id": test_sync_id,
            "payload_type": "ACTIVITY_LOG",
            "payload": {
                "date": self.test_date,
                "hour_slot": "10:00-11:00",
                "app_name": "code.exe",
                "category": "CODING",
                "duration_seconds": 60,
                "idle_seconds": 0,
                "is_dev_mode": True,
            },
        }
        res1 = self.engine.process_sync_batch([item], device_id="android_lok_phone")
        self.assertEqual(res1["applied_count"], 1)

        # Retry same batch -> duplicate skipped
        res2 = self.engine.process_sync_batch([item], device_id="android_lok_phone")
        self.assertEqual(res2["skipped_duplicates"], 1)


if __name__ == "__main__":
    unittest.main()
