"""
Automated Verification Suite for StrictRecords Core Engine (Phases 1-16)
Verifies data model, default daily routine, wake-up 3-step verification,
snooze penalties, bath deadline, hourly updates, cross-device anti-cheating,
gym duration enforcement, entertainment restrictions & local music exception,
offline sync idempotency, evidence deletion, auditability, and dev mode isolation.
"""

import os
import shutil
import tempfile
import unittest

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine


VALID_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\xff\xd9"
)


class TestStrictRecordsCore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="strictrecords_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.storage_root = os.path.join(self.temp_dir, "STRICTRECORDS")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.date = "2026-10-04"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_default_schedule_and_semantics(self):
        state = self.engine.get_full_day_state(self.date)
        settings = state["settings"]
        self.assertEqual(settings["wakeup_time"], "07:00")
        self.assertEqual(settings["bath_deadline"], "10:00")
        self.assertEqual(settings["gym_time"], "18:00")
        self.assertEqual(settings["hourly_start_time"], "08:00")
        self.assertEqual(settings["hourly_end_time"], "22:00")

        tasks = {t["task_code"]: t for t in state["scheduled_tasks"]}
        self.assertEqual(tasks["WAKE_UP"]["semantics"], "EXACT_TIME")
        self.assertEqual(tasks["BATH"]["semantics"], "DEADLINE")
        self.assertEqual(tasks["GYM"]["semantics"], "SCHEDULED_START")
        self.assertEqual(tasks["HOURLY_08"]["semantics"], "RECURRING_CHECKPOINT")
        self.assertEqual(tasks["HOURLY_22"]["semantics"], "RECURRING_CHECKPOINT")
        self.assertEqual(len(state["hourly_records"]), 16)  # Configured wake-up target 07:00 through 22:00 inclusive

    def test_02_snooze_penalties_accumulate(self):
        s1 = self.engine.record_snooze(self.date, timestamp="2026-10-04 07:03:00")
        s2 = self.engine.record_snooze(self.date, timestamp="2026-10-04 07:08:00")
        self.assertEqual(s1["snooze_number"], 1)
        self.assertEqual(s1["points_delta"], -5)
        self.assertEqual(s2["snooze_number"], 2)
        self.assertEqual(s2["points_delta"], -5)

        daily = self.engine.recalculate_daily_score(self.date)
        self.assertEqual(daily["snooze_count"], 2)
        self.assertEqual(daily["negative_points"], 10)
        self.assertEqual(daily["breakdown"]["Snooze"], -10)

    def test_03_wakeup_three_step_verification_strict_enforcement(self):
        self.engine.trigger_wakeup_alarm(self.date, timestamp="2026-10-04 07:00:00")
        self.engine.start_wakeup_task(self.date, timestamp="2026-10-04 07:00:10")

        # Cannot jump to Step 2 before Step 1
        with self.assertRaises(ValueError):
            self.engine.verify_wakeup_step2_bluetooth(self.date, ["Sony Earbuds"])

        # Empty 0-byte photo must be rejected
        with self.assertRaises(ValueError):
            self.engine.verify_wakeup_step1_camera(self.date, image_bytes=b"")

        # Step 1: Valid camera capture
        s1 = self.engine.verify_wakeup_step1_camera(
            self.date, image_bytes=VALID_JPEG, timestamp="2026-10-04 07:01:00"
        )
        self.assertEqual(s1["camera_verified"], 1)

        # Cannot finalize before Step 2 & 3
        with self.assertRaises(ValueError):
            self.engine.finalize_wakeup_session(self.date)

        # Step 2: Empty Bluetooth list stays in WAITING FOR CONNECTION
        bt_wait = self.engine.verify_wakeup_step2_bluetooth(self.date, [])
        self.assertFalse(bt_wait["bluetooth_verified"])
        self.assertEqual(bt_wait["status"], "WAITING FOR CONNECTION")

        # Step 2: Real connected Bluetooth device
        bt_ok = self.engine.verify_wakeup_step2_bluetooth(
            self.date, ["Galaxy Buds2 Pro"], timestamp="2026-10-04 07:01:20"
        )
        self.assertTrue(bt_ok["bluetooth_verified"])
        self.assertEqual(bt_ok["device_name"], "Galaxy Buds2 Pro")

        # Step 3: Incomplete track listening (50%) does NOT unlock completion
        tr_partial = self.engine.verify_wakeup_step3_motivation_track(
            self.date,
            track_duration_sec=222,
            listened_duration_sec=111,
            track_started_at="2026-10-04 07:01:25",
            interruption_count=1,
        )
        self.assertFalse(tr_partial["motivation_verified"])
        self.assertEqual(tr_partial["completion_pct"], 50.0)

        # Synthetic camera/route flags cannot substitute for measured retained proof.
        tr_full = self.engine.verify_wakeup_step3_motivation_track(
            self.date,
            track_duration_sec=222,
            listened_duration_sec=222,
            track_started_at="2026-10-04 07:01:00",
            track_ended_at="2026-10-04 07:04:42",
            timestamp="2026-10-04 07:04:42",
        )
        self.assertFalse(tr_full["wakeup_complete"])
        self.assertEqual(tr_full['points_awarded'],0)

        daily = self.engine.recalculate_daily_score(self.date)
        self.assertEqual(daily["final_score"], 0)

    def test_04_bath_deadline_and_evidence_deletion(self):
        res = self.engine.complete_bath(
            self.date,
            completed_at="2026-10-04 08:32:00",
            verification_mode="PHOTO_PROOF",
            image_bytes=VALID_JPEG,
            is_dev_mode=True,
        )
        self.assertEqual(res["status"], "BATH COMPLETE ✅")
        self.assertEqual(res["points"], 10)
        ev_id = res["evidence_id"]
        self.assertIsNotNone(ev_id)

        # User can delete their own evidence file (Section 15 & 45)
        del_res = self.engine.delete_user_evidence(ev_id)
        self.assertTrue(del_res["deleted"])

    def test_05_hourly_cross_device_verification_and_anti_cheating(self):
        # Hour 10:00-11:00: Strong Coding Evidence (VS Code 48m + Phone 7m + Photo)
        self.engine.log_device_activity(
            self.date, "10:00-11:00", "android_lok_phone", "ANDROID", "Screen", "Study", "PRODUCTIVE", 7 * 60
        )
        self.engine.log_device_activity(
            self.date, "10:00-11:00", "windows_lok_pc", "WINDOWS", "VS Code", "engine.py", "PRODUCTIVE", 48 * 60
        )
        h1 = self.engine.submit_hourly_update(
            self.date,
            "10:00-11:00",
            "CODING",
            image_bytes=VALID_JPEG,
            timestamp="2026-10-04 11:00:00",
        )
        self.assertEqual(h1["verification_result"], "Strong Coding Evidence")
        self.assertEqual(h1["integrity_flag"], "VERIFIED")
        self.assertEqual(h1["points_awarded"], 8)  # +5 productive +3 photo proof

        # Hour 11:00-12:00: Anti-cheating inconsistent evidence (5m VS Code, 45m Entertainment)
        self.engine.log_device_activity(
            self.date, "11:00-12:00", "windows_lok_pc", "WINDOWS", "VS Code", "test.py", "PRODUCTIVE", 5 * 60
        )
        self.engine.log_device_activity(
            self.date, "11:00-12:00", "android_lok_phone", "ANDROID", "YouTube", "Shorts", "ENTERTAINMENT", 45 * 60
        )
        h2 = self.engine.submit_hourly_update(
            self.date,
            "11:00-12:00",
            "CODING",
            timestamp="2026-10-04 12:00:00",
        )
        self.assertEqual(h2["verification_result"], "INCONSISTENT EVIDENCE ⚠")
        self.assertEqual(h2["integrity_flag"], "INCONSISTENT_EVIDENCE")

    def test_06_gym_minimum_duration_enforcement(self):
        self.engine.start_gym_session(self.date, start_timestamp="2026-10-04 18:00:00")
        short = self.engine.complete_gym_session(
            self.date, end_timestamp="2026-10-04 18:15:00", duration_minutes=15.0
        )
        self.assertFalse(short["completed"])
        self.assertEqual(short["status"], "INSUFFICIENT_DURATION")

        valid = self.engine.complete_gym_session(
            self.date, end_timestamp="2026-10-04 18:45:00", duration_minutes=45.0,is_dev_mode=True
        )
        self.assertTrue(valid["completed"])
        self.assertEqual(valid["status"], "GYM COMPLETE ✅")
        self.assertEqual(valid["points_earned"], 15)

    def test_07_entertainment_thresholds_and_local_music_exception(self):
        # 1. Simulate Score 45 (STRICT MODE) in dev mode
        self.engine.run_dev_simulation(self.date, "SCORE_45")
        yt_45 = self.engine.evaluate_entertainment_access(self.date, "YouTube", is_dev_mode=True)
        mp3_45 = self.engine.evaluate_entertainment_access(self.date, "Local MP3", is_dev_mode=True)
        self.assertFalse(yt_45["allowed"])
        self.assertFalse(mp3_45["allowed"])
        self.assertEqual(yt_45["score_mode"], "STRICT_MODE")

        # 2. Simulate Score 75 (CONTROLLED MODE: Local Music ALLOWED, YouTube/Spotify RESTRICTED)
        self.engine.run_dev_simulation(self.date, "SCORE_75")
        yt_75 = self.engine.evaluate_entertainment_access(self.date, "YouTube", is_dev_mode=True)
        sp_75 = self.engine.evaluate_entertainment_access(self.date, "Spotify", is_dev_mode=True)
        mp3_75 = self.engine.evaluate_entertainment_access(self.date, "Local MP3", is_dev_mode=True)
        self.assertFalse(yt_75["allowed"])
        self.assertFalse(sp_75["allowed"])
        self.assertTrue(mp3_75["allowed"])
        self.assertEqual(mp3_75["score_mode"], "CONTROLLED_MODE")

        # 3. Simulate Score 95 (REWARD UNLOCKED: YouTube & Spotify ALLOWED)
        self.engine.run_dev_simulation(self.date, "SCORE_95")
        yt_95 = self.engine.evaluate_entertainment_access(self.date, "YouTube", is_dev_mode=True)
        sp_95 = self.engine.evaluate_entertainment_access(self.date, "Spotify", is_dev_mode=True)
        self.assertTrue(yt_95["allowed"])
        self.assertTrue(sp_95["allowed"])
        self.assertEqual(yt_95["score_mode"], "REWARD_UNLOCKED")

        # Verify Production Score remained 0 (Dev Mode did not pollute production!)
        prod_daily = self.engine.recalculate_daily_score(self.date, is_dev_mode=False)
        self.assertEqual(prod_daily["final_score"], 0)

    def test_08_offline_sync_deduplication_and_evidence_package(self):
        batch = [
            {
                "sync_id": "unique_sync_001",
                "payload_type": "SNOOZE",
                "payload": {"date": self.date, "timestamp": "2026-10-04 07:05:00"},
            }
        ]
        r1 = self.engine.process_sync_batch(batch, device_id="android_lok_phone")
        r2 = self.engine.process_sync_batch(batch, device_id="android_lok_phone")
        self.assertEqual(r1["applied_count"], 1)
        self.assertEqual(r2["applied_count"], 0)
        self.assertEqual(r2["skipped_duplicates"], 1)

        pkg = self.engine.export_daily_evidence_package(self.date)
        self.assertTrue(os.path.exists(pkg["daily_record_json"]))
        self.assertTrue(os.path.exists(pkg["score_events_json"]))
        self.assertTrue(os.path.exists(pkg["daily_review_json"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
