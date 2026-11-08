import os
import io
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from tests.test_daily_routine_gate import seed_approved_wakeup

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, TimeWindowEngine, iso_to_dt, now_ist


class TestProofScoreGateAndSubmissionWindows(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(os.path.join(self.temp_dir.name, "strictrecords.db"))
        self.engine = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        self.tw = TimeWindowEngine(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fresh_day_and_unproved_hourly_submission_award_zero(self):
        date = (now_ist().date() + timedelta(days=1)).isoformat()
        self.db.ensure_daily_record(date)
        with self.db._connect() as conn:
            row = conn.execute("SELECT final_score FROM daily_records WHERE date = ? AND is_dev_mode = 0", (date,)).fetchone()
        self.assertEqual(row["final_score"], 0)

        result = self.engine.submit_hourly_update(
            date=date,
            hour_slot="09:00-10:00",
            primary_activity="CODING",
            description="No image was attached",
        )
        self.assertFalse(result["evidence_submitted"])
        self.assertEqual(result["points_awarded"], 0)
        self.assertIsNone(result["evidence_id"])
        self.assertEqual(self.db.get_score_events(date, is_dev_mode=False), [])

    def test_client_sync_points_are_ignored(self):
        date = now_ist().date().isoformat()
        self.db.ensure_daily_record(date)
        result = self.db.process_sync_events_batch(
            device_id="unpaired-test-device",
            events=[{
                "event_id": "evt_claim_999",
                "event_type": "HOURLY_PROOF_SUBMITTED",
                "timestamp": now_ist().isoformat(),
                "payload": {"activity_id": "missing-activity", "points": 999},
            }],
        )
        self.assertEqual(result["shared_score"], 0)
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM score_events WHERE date = ?", (date,)).fetchone()[0]
        self.assertEqual(count, 0)

    def test_only_approved_real_proof_awards_once(self):
        date = (now_ist().date() + timedelta(days=1)).isoformat()
        from PIL import Image
        image_buffer = io.BytesIO()
        Image.new("RGB", (120, 120), color=(35, 80, 130)).save(image_buffer, format="JPEG")
        evidence = self.engine.store_evidence_file(
            date=date,
            record_type="Hourly",
            related_record_id="hourly-test",
            device_id="test-device",
            file_bytes=image_buffer.getvalue(),
            filename_hint="proof.jpg",
        )

        blocked = self.engine.record_score_event(
            date=date,
            event_type="HOURLY_PRODUCTIVE",
            points=8,
            device_id="test-device",
            description="Claim without approval",
        )
        self.assertFalse(blocked["awarded"])
        self.assertEqual(blocked["points"], 0)

        first = self.engine.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="different-admin",
            points=8,
            reason="Regression test approval",
        )
        second = self.engine.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="different-admin",
            points=8,
            reason="Repeated approval request",
        )
        self.assertEqual(first["points_awarded"], 8)
        self.assertTrue(second["already_approved"])
        self.assertEqual(second["points_awarded"], 8)
        events = self.db.get_score_events(date, is_dev_mode=False)
        proof_events = [event for event in events if event["event_type"] == "PROOF_APPROVED"]
        self.assertEqual(len(proof_events), 1)
        self.assertEqual(proof_events[0]["points"], 8)
        self.assertEqual(self.engine.recalculate_daily_score(date)["final_score"], 8)

        duplicate_type = self.engine.record_score_event(
            date=date,
            event_type="HOURLY_PRODUCTIVE",
            points=8,
            device_id="test-device",
            description="Second award attempt for the same approved proof",
            evidence_id=evidence["evidence_id"],
        )
        self.assertFalse(duplicate_type["awarded"])
        self.assertEqual(duplicate_type["points"], 0)
        self.assertEqual(self.engine.recalculate_daily_score(date)["final_score"], 8)

    def test_startup_rebuilds_stale_unproved_daily_total_without_deleting_history(self):
        date = now_ist().date().isoformat()
        self.db.ensure_daily_record(date)
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type, device_id,
                    description, points, rule_version, evidence_id, related_task_id, audit_json
                ) VALUES (?, ?, 0, ?, 'PROOF_APPROVED', 'legacy-device', 'Old unproved claim', 99, 1, 'missing-proof', '', '{}')
                """,
                ("legacy_unproved_positive", date, now_ist().isoformat()),
            )
            conn.execute("UPDATE daily_records SET final_score = 99 WHERE date = ? AND is_dev_mode = 0", (date,))

        restarted_engine = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT final_score FROM daily_records WHERE date = ? AND is_dev_mode = 0", (date,)
            ).fetchone()
            retained = conn.execute(
                "SELECT COUNT(*) FROM score_events WHERE event_id = 'legacy_unproved_positive'"
            ).fetchone()[0]
        self.assertEqual(row["final_score"], 0)
        self.assertEqual(retained, 1)
        self.assertEqual(restarted_engine.get_full_day_state(date)["daily_record"]["final_score"], 0)

    def test_corrupt_retained_proof_cannot_keep_approved_points_after_restart(self):
        date = now_ist().date().isoformat()
        from PIL import Image
        image_buffer = io.BytesIO()
        Image.new("RGB", (120, 120), color=(130, 80, 35)).save(image_buffer, format="JPEG")
        evidence = self.engine.store_evidence_file(
            date=date,
            record_type="Gym",
            related_record_id="gym-corrupt-proof",
            device_id="test-device",
            file_bytes=image_buffer.getvalue(),
            filename_hint="proof.jpg",
        )
        self.engine.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="different-admin",
            points=12,
            reason="Valid at approval time",
        )
        self.assertEqual(self.engine.recalculate_daily_score(date)["final_score"], 12)

        with open(evidence["file_path"], "wb") as corrupt_file:
            corrupt_file.write(b"not an image")

        restarted_engine = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        self.assertEqual(restarted_engine.recalculate_daily_score(date)["final_score"], 0)
        self.assertEqual(self.db.get_point_ledger_summary(date_str=date)["verified_points"], 0)

    def test_replaced_valid_image_cannot_keep_approved_points(self):
        date = now_ist().date().isoformat()
        from PIL import Image
        image_buffer = io.BytesIO()
        Image.new("RGB", (120, 120), color=(30, 100, 170)).save(image_buffer, format="JPEG")
        evidence = self.engine.store_evidence_file(
            date=date,
            record_type="Hourly",
            related_record_id="hourly-replaced-proof",
            device_id="test-device",
            file_bytes=image_buffer.getvalue(),
            filename_hint="proof.jpg",
        )
        self.engine.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="separate-test-admin",
            points=12,
            reason="Valid at approval time",
        )
        self.assertEqual(self.engine.recalculate_daily_score(date)["final_score"], 12)

        replacement = io.BytesIO()
        Image.new("RGB", (120, 120), color=(190, 35, 60)).save(replacement, format="JPEG")
        with open(evidence["file_path"], "wb") as proof_file:
            proof_file.write(replacement.getvalue())

        restarted_engine = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        self.assertEqual(restarted_engine.recalculate_daily_score(date)["final_score"], 0)
        self.assertEqual(self.db.get_point_ledger_summary(date_str=date)["verified_points"], 0)

        repeated_award = restarted_engine.record_score_event(
            date=date,
            event_type="PROOF_APPROVED",
            points=12,
            device_id="test-device",
            description="Attempt to award a replaced image",
            evidence_id=evidence["evidence_id"],
        )
        self.assertFalse(repeated_award["awarded"])
        self.assertEqual(repeated_award["points"], 0)

    def test_wakeup_window_is_separate_and_hourly_anchors_to_target(self):
        date = (now_ist().date() + timedelta(days=1)).isoformat()
        wake_window = TimeWindowEngine.calculate_wakeup_activity_window(date, "07:15")
        self.assertTrue(wake_window["window_start"].startswith(f"{date}T07:00:00"))
        self.assertTrue(wake_window["window_end"].startswith(f"{date}T09:30:00"))
        self.assertTrue(wake_window["scheduled_time"].startswith(f"{date}T07:15:00"))

        validation = self.tw.validate_routine_schedule({
            "schedule_date": date,
            "wake_up_time": "07:15",
            "bath_time": "07:45",
            "gym_time": "18:30",
        }, date)
        self.assertTrue(validation["valid"], validation["errors"])
        activities = TimeWindowEngine.generate_daily_schedule(
            date,
            wakeup_time="07:15",
            bath_time="07:45",
            gym_time="18:30",
            schedule_id=f"SCHED-{date.replace('-', '')}-V1",
        )
        hourly = [activity for activity in activities if activity["type"] == "HOURLY"]
        self.assertTrue(hourly)
        self.assertTrue(hourly[0]["scheduled_time"].startswith(f"{date}T07:15:00"))

    def test_window_boundaries_are_half_open_and_duplicate_safe(self):
        base_date = now_ist().date() + timedelta(days=1)
        scenarios = [
            ("before", -1, False, "TOO_EARLY"),
            ("start", 0, True, "SUBMITTED"),
            ("last_second", 1199, True, "SUBMITTED"),
            ("close", 1200, False, "REJECTED_LATE"),
        ]
        for index, (name, seconds_from_start, allowed, expected_status) in enumerate(scenarios):
            date = (base_date + timedelta(days=index)).isoformat()
            seed_approved_wakeup(self.db, self.engine, date)
            activities = self.tw.ensure_activities_in_db(date, reference_now=iso_to_dt(f"{date}T09:00:00+05:30"))
            activity = next(
                item for item in activities
                if item["type"] == "HOURLY" and item["scheduled_time"].startswith(f"{date}T10:00:00")
            )
            start = iso_to_dt(activity["window_start"])
            submitted = start + timedelta(seconds=seconds_from_start)
            with patch("core.time_window_engine.now_ist", return_value=submitted):
                result = self.tw.evaluate_submission_timing(activity["activity_id"], submitted.isoformat())
            self.assertEqual(result["allowed"], allowed, name)
            self.assertEqual(result["status"], expected_status, name)
            self.assertEqual(result.get("points", 0), 0, name)

            if name == "start":
                with patch("core.time_window_engine.now_ist", return_value=submitted):
                    duplicate = self.tw.evaluate_submission_timing(activity["activity_id"], submitted.isoformat())
                self.assertFalse(duplicate["allowed"])
                self.assertEqual(duplicate["status"], "DUPLICATE")

    def test_time_parser_uses_explicit_offsets_and_fails_closed(self):
        utc_time = iso_to_dt("2026-10-12T04:30:00Z")
        self.assertEqual(utc_time.hour, 10)
        self.assertEqual(utc_time.minute, 0)
        self.assertEqual(utc_time.utcoffset(), IST.utcoffset(utc_time))
        with self.assertRaises(ValueError):
            iso_to_dt("not-a-timestamp")


if __name__ == "__main__":
    unittest.main()
