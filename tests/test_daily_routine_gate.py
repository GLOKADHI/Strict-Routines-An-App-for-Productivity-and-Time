import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
from queue import Empty
from datetime import datetime

from PIL import Image

from core.wakeup_attempt_validation import configured_track_manifest
TRACK_SECONDS = configured_track_manifest()["duration_sec"]

from core.database import StrictRecordsDB
from core.daily_routine_gate import DailyRoutineGate
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, TimeWindowEngine
from windows.submission_window_manager import SubmissionWindowManager
from core.event_bus import EventBus


def seed_approved_wakeup(db, scoring, date, completed_hm="07:30"):
    """A real retained image and explicit saved hardware facts for timing tests."""
    db.ensure_daily_record(date)
    buffer = io.BytesIO()
    Image.new("RGB", (120, 120), (100, 140, 170)).save(buffer, format="JPEG")
    evidence = scoring.store_evidence_file(date, "WakeUp", f"wakeup_{date}_0", "fixture-phone", file_bytes=buffer.getvalue(),
        metadata={"liveness_status": "PASSED", "liveness_duration_sec": 20, "valid_observation_ratio": 0.9, "eyes_detected": True})
    playback = {"completed": True, "headset_connected": True, "volume_ok": True, "seek_attempts": 0,
                "audio_route": "BLUETOOTH_A2DP", "track_duration_sec": TRACK_SECONDS, "completed_duration_sec": TRACK_SECONDS}
    timestamp = f"{date} {completed_hm}:00"
    with db._connect() as conn:
        conn.execute("""UPDATE wake_up_sessions SET camera_verified=1, camera_evidence_id=?, bluetooth_verified=1,
            active_audio_output_verified=1, bluetooth_device_name='Bluetooth earbuds', motivation_verified=1,
            track_duration_sec=?, listened_duration_sec=?, completion_pct=100, completed_at=?, playback_session_json=?
            WHERE date=? AND is_dev_mode=0""", (evidence["evidence_id"], TRACK_SECONDS, TRACK_SECONDS, timestamp, json.dumps(playback), date))
        conn.execute("UPDATE proof_reviews SET status='APPROVED', reviewed_at=? WHERE evidence_id=?", (timestamp, evidence["evidence_id"]))
    return evidence


class DailyRoutineGateTests(unittest.TestCase):
    date = "2026-10-12"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(os.path.join(self.folder.name, "gate.db"))
        self.scoring = ScoringEngine(self.db, storage_root=os.path.join(self.folder.name, "proofs"))
        self.tw = TimeWindowEngine(self.db)
        self.db.ensure_daily_record(self.date)
        self.gate = DailyRoutineGate(self.db)
        self.morning = datetime(2026, 10, 12, 9, 0, tzinfo=IST)
        buffer = io.BytesIO()
        Image.new("RGB", (120, 120), (100, 140, 170)).save(buffer, format="JPEG")
        self.photo = buffer.getvalue()
        self.live = {"liveness_status": "PASSED", "liveness_duration_sec": 20,
                     "valid_observation_ratio": 0.9, "eyes_detected": True, "face_detected": True}
        self.playback = {"completed": True, "headset_connected": True, "volume_ok": True,
                         "seek_attempts": 0, "audio_route": "BLUETOOTH_A2DP",
                         "track_duration_sec": TRACK_SECONDS, "completed_duration_sec": TRACK_SECONDS}

    def tearDown(self):
        self.folder.cleanup()

    def save_checks(self, approved=True, **changes):
        evidence = self.scoring.store_evidence_file(self.date, "WakeUp", f"wakeup_{self.date}_0", "phone",
            file_bytes=self.photo, metadata=self.live, timestamp=f"{self.date} 08:55:00")
        values = {"camera_verified": 1, "bluetooth_verified": 1, "active_audio_output_verified": 1,
                  "bluetooth_device_name": "Bluetooth earbuds", "motivation_verified": 1,
                  "track_duration_sec": TRACK_SECONDS, "listened_duration_sec": TRACK_SECONDS, "completion_pct": 100,
                  "completed_at": f"{self.date} 09:00:00", "camera_evidence_id": evidence["evidence_id"],
                  "playback_session_json": json.dumps(self.playback)}
        values.update(changes)
        with self.db._connect() as conn:
            query = ",".join(f"{key}=?" for key in values)
            conn.execute(f"UPDATE wake_up_sessions SET {query} WHERE date=? AND is_dev_mode=0", (*values.values(), self.date))
            conn.execute("UPDATE proof_reviews SET status=?, reviewed_at=? WHERE evidence_id=?",
                         ("APPROVED" if approved else "PENDING", f"{self.date} 09:00:00" if approved else None, evidence["evidence_id"]))
        return evidence

    def test_incomplete_wakeup_shows_plan_without_attendance_or_penalty(self):
        later = datetime(2026, 10, 12, 16, 0, tzinfo=IST)
        activities = self.tw.ensure_activities_in_db(self.date, reference_now=later)
        hourly = [a for a in activities if a["type"] == "HOURLY"]
        self.assertTrue(hourly)
        self.assertTrue(all(a['status']!='BLOCKED_WAKEUP' for a in hourly))
        with self.db._connect() as conn:
            self.assertGreater(conn.execute("SELECT count(*) FROM hourly_records WHERE date=?", (self.date,)).fetchone()[0], 0)
        result = self.scoring.mark_hourly_missed(self.date, "14:00-15:00", timestamp=later.isoformat())
        self.assertEqual(result["points"], 0)
        self.assertEqual(self.db.get_score_events(self.date), [])
        self.assertFalse(self.gate.reconcile(self.date,reference_now=later)['active'])

    def test_each_mandatory_component_blocks_gate(self):
        self.save_checks(camera_verified=0)
        blocked = self.gate.reconcile(self.date, reference_now=self.morning)
        self.assertFalse(blocked["active"])
        self.assertNotEqual(blocked["state"], "DAILY_ROUTINE_ACTIVE")

        for optional_col in ("bluetooth_verified", "active_audio_output_verified", "motivation_verified"):
            with self.subTest(optional_component=optional_col):
                self.save_checks(**{optional_col: 0})
                result = self.gate.reconcile(self.date, reference_now=self.morning)
                self.assertTrue(result["active"])
                self.assertEqual(result["state"], "DAILY_ROUTINE_ACTIVE")

    def test_claimed_complete_track_does_not_replace_saved_playback(self):
        self.save_checks(playback_session_json="{}")
        self.assertFalse(self.gate.inspect_checks(self.date)["checks"]["full_track"])
        self.assertTrue(self.gate.reconcile(self.date, reference_now=self.morning)["active"])
        self.save_checks(listened_duration_sec=221, completion_pct=99.55)
        self.assertFalse(self.gate.inspect_checks(self.date)["checks"]["full_track"])
        self.assertTrue(self.gate.reconcile(self.date, reference_now=self.morning)["active"])
        self.save_checks(playback_session_json=json.dumps(dict(self.playback, audio_route="SPEAKER")))
        self.assertFalse(self.gate.inspect_checks(self.date)["checks"]["bluetooth_active_output"])
        self.assertTrue(self.gate.reconcile(self.date, reference_now=self.morning)["active"])

    def test_pending_review_and_missing_photo_cannot_start_attendance(self):
        evidence = self.save_checks(approved=False)
        self.assertEqual(self.gate.reconcile(self.date, reference_now=self.morning)["state"], "WAKE_UP_VERIFICATION")
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET status='APPROVED' WHERE evidence_id=?", (evidence["evidence_id"],))
        os.remove(evidence["file_path"])
        self.assertFalse(self.gate.reconcile(self.date, reference_now=self.morning)["active"])

    def test_restart_and_schedule_revision_are_idempotent(self):
        self.save_checks()
        bus = EventBus.get_instance()
        subscriber = bus.subscribe()
        self.addCleanup(lambda: bus.unsubscribe(subscriber))
        first = self.gate.reconcile(self.date, reference_now=self.morning)
        activities = self.tw.ensure_activities_in_db(self.date, reference_now=self.morning)
        second = DailyRoutineGate(self.db).reconcile(self.date, reference_now=self.morning)
        self.assertTrue(first["active"])
        self.assertEqual(first["activated_at"], second["activated_at"])
        hourly = [a for a in activities if a["type"] == "HOURLY"]
        self.assertTrue(hourly)
        with self.db._connect() as conn:
            count = conn.execute("SELECT count(*) FROM sync_queue WHERE payload_type='DAILY_ROUTINE_ACTIVE'").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(subscriber.get_nowait()["event_id"], first["event_id"])
        with self.assertRaises(Empty):
            subscriber.get_nowait()
        self.db.save_routine_schedule_record({"schedule_id": "gate-v2", "schedule_date": self.date,
                                            "schedule_version": 2, "status": "SCHEDULED", "is_locked": True})
        revised = self.gate.reconcile(self.date, reference_now=self.morning)
        self.assertEqual(revised["schedule_version"], 2)
        self.assertEqual(revised["activated_at"], first["activated_at"])
        self.gate.reconcile(self.date, reference_now=self.morning)
        with self.db._connect() as conn:
            count = conn.execute("SELECT count(*) FROM sync_queue WHERE payload_type='DAILY_ROUTINE_ACTIVE'").fetchone()[0]
        self.assertEqual(count, 2)

    def test_approval_after_earlier_checkpoints_skips_them_without_absence(self):
        evidence = self.save_checks()
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET reviewed_at=? WHERE evidence_id=?", (f"{self.date} 12:00:00", evidence["evidence_id"]))
        noon = datetime(2026, 10, 12, 12, 0, tzinfo=IST)
        rows = self.tw.ensure_activities_in_db(self.date, reference_now=noon)
        hourly = [a for a in rows if a["type"] == "HOURLY" and a["submission_eligible"]]
        self.assertTrue(hourly)
        self.assertTrue(any(a['window_start']<noon.isoformat() for a in hourly))
        self.assertTrue(any(a['status']=='MISSED' for a in hourly))
        self.assertEqual(self.scoring.mark_hourly_missed(self.date, "10:00-11:00", timestamp=noon.isoformat())["points"], 0)

    def test_late_hardware_completion_requires_explicit_recovery(self):
        self.save_checks(completed_at=f"{self.date} 10:01:00")
        result = self.gate.reconcile(self.date, reference_now=datetime(2026, 10, 12, 10, 2, tzinfo=IST))
        self.assertFalse(result["active"])
        self.assertTrue(result["recovery_required"])
        with self.assertRaises(PermissionError):
            self.gate.authorize_recovery(self.date, "frontend-admin-flag", "travel recovery", reference_now=self.morning)

    def test_completion_at_0955_requires_recovery_after_0930_cutoff(self):
        self.save_checks(completed_at=f"{self.date} 09:55:00")
        reference = datetime(2026, 10, 12, 9, 55, tzinfo=IST)
        activities = self.tw.ensure_activities_in_db(self.date, reference_now=reference)
        hourly = [a for a in activities if a["type"] == "HOURLY"]
        self.assertTrue(hourly)
        self.assertFalse(self.gate.reconcile(self.date,reference_now=reference)['active'])
        self.assertTrue(any(a['submission_eligible'] for a in hourly))

    def test_wakeup_window_closes_exactly_at_0930(self):
        self.save_checks(completed_at=f"{self.date} 10:00:00")
        reference = datetime(2026, 10, 12, 10, 0, tzinfo=IST)
        self.assertFalse(self.gate.reconcile(self.date, reference_now=reference)["active"])
        self.assertTrue(self.tw.calculate_wakeup_activity_window(self.date, "07:30")["window_end"].startswith(f"{self.date}T09:30:00"))

    def test_approved_exception_survives_first_materialization_and_restart(self):
        self.save_checks()
        schedule = {"schedule_id": "gate-travel-v2", "schedule_date": self.date, "schedule_version": 2,
                    "status": "SCHEDULED", "gym_enabled": True,
                    "reschedule_audit_log": [{"action": "APPROVED_SCHEDULE_CHANGE", "authorized_by": "fixture-admin",
                        "proposal_id": "travel-proposal", "reason": "Travel", "exception_start": f"{self.date}T17:00:00+05:30",
                        "exception_end": f"{self.date}T19:00:00+05:30", "applied_at": f"{self.date}T10:00:00+05:30"}]}
        self.db.save_routine_schedule_record(schedule)
        reference = datetime(2026, 10, 12, 13, 0, tzinfo=IST)
        first = self.tw.ensure_activities_in_db(self.date, reference_now=reference)
        excused = [a for a in first if a["status"] == "EXCUSED"]
        self.assertTrue(any(a["type"] == "HOURLY" for a in excused))
        self.assertTrue(any(a["type"] == "GYM" for a in excused))
        self.assertTrue(all(a["attendance_status"] == "EXCUSED" for a in excused))
        restarted = TimeWindowEngine(self.db).ensure_activities_in_db(self.date, reference_now=reference)
        self.assertEqual({a["activity_id"] for a in excused}, {a["activity_id"] for a in restarted if a["status"] == "EXCUSED"})

    def test_confirmed_timezone_controls_absolute_windows_and_wakeup_gate(self):
        self.save_checks(completed_at=f"{self.date}T09:00:00-04:00")
        self.db.save_routine_schedule_record({"schedule_id": "ny-v1", "schedule_date": self.date, "schedule_version": 1,
                                            "status": "SCHEDULED", "timezone": "America/New_York"})
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET reviewed_at=? WHERE date=?", (f"{self.date}T09:00:00-04:00", self.date))
        reference = datetime.fromisoformat(f"{self.date}T09:00:00-04:00")
        activities = self.tw.ensure_activities_in_db(self.date, reference_now=reference)
        self.assertTrue(self.gate.reconcile(self.date, reference_now=reference)["active"])
        hourly = next(a for a in activities if a["type"] == "HOURLY" and a["submission_eligible"])
        self.assertEqual(hourly["window_start"], f"{self.date}T06:50:00-04:00")

    def test_windows_popup_checks_authoritative_gate(self):
        item = next(a for a in self.tw.generate_daily_schedule(self.date) if a["type"] == "HOURLY")
        with self.db._connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO scheduled_activities(activity_id, account_id, type, date, scheduled_time,
                window_start, window_end, notification_time, status, validation_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)""",
                tuple(item[k] for k in ("activity_id", "account_id", "type", "date", "scheduled_time", "window_start", "window_end", "notification_time", "validation_rule")))
        manager = SubmissionWindowManager(self.db)
        response = manager.open_submission_window(item["activity_id"], reference_now=self.morning)
        self.assertEqual(response["action"], "OPENED")
        self.assertEqual(manager.active_activity_id, item['activity_id'])

    def test_sync_claim_without_saved_photo_or_measurements_cannot_complete_wakeup(self):
        result = self.db.process_sync_events_batch("unregistered-phone", [{"event_id": "empty-wake", "event_type": "WAKEUP_VERIFIED", "payload": {"date": self.date}}])
        self.assertFalse(result["daily_routine_gate"]["active"])
        with self.db._connect() as conn:
            session = conn.execute("SELECT camera_verified, bluetooth_verified, motivation_verified FROM wake_up_sessions WHERE date=?", (self.date,)).fetchone()
        self.assertEqual(tuple(session), (0, 0, 0))

    def test_fabricated_photo_path_is_rejected(self):
        response = self.scoring.verify_wakeup_step1_camera(self.date, image_path="nonexistent-camera.jpg",
            face_detected=True, eyes_detected=True, liveness_duration_sec=20, valid_observation_ratio=0.9, liveness_status="PASSED")
        self.assertFalse(response["camera_verified"])
        self.assertEqual(response["status"], "EVIDENCE_REQUIRED")

    def test_real_android_payload_retains_photo_and_waits_for_server_review(self):
        session = self.db.create_pairing_session(server_endpoint="http://192.168.1.2:8765")
        paired = self.db.handshake_pairing(session_key=session["session_key"], device_id="gate-a55",
                                         device_name="SM-A556E", app_version="1.15.0")
        self.assertTrue(paired["success"])
        device_id = paired["device_id"]
        from core.wakeup_attempt_validation import configured_track_manifest
        manifest = configured_track_manifest()
        live = dict(self.live, attempt_id="fixture-attempt", policy_version=2, session_state="PASSED",
                    photo_sha256=hashlib.sha256(self.photo).hexdigest(),
                    valid_samples=40, total_samples=40, valid_duration_ms=20000, duration_ms=20000,
                    valid_sample_ratio=1.0, unique_frames=40, first_frame_elapsed_ms=1000,
                    last_frame_elapsed_ms=21000, accepted_elapsed_ms=21100, final_frame_valid=True,
                    stable_face=True, lighting_ok=True, head_pose_ok=True)
        playback = dict(self.playback, attempt_id="fixture-attempt", route_source="MEDIA_PLAYER_ROUTED_DEVICE",
                        selected_device_name="Bluetooth earbuds", selected_device_address="AA:BB:CC:DD:EE:FF",
                        routed_device_name="Bluetooth earbuds", routed_device_address="AA:BB:CC:DD:EE:FF",
                        track_duration_sec=manifest["duration_sec"], completed_duration_sec=manifest["duration_sec"],
                        track_sha256=manifest["sha256"], route_verified_duration_ms=round(manifest["duration_sec"] * 1000),
                        route_observations=10)
        payload = {"date": self.date, "completed_at": f"{self.date}T09:00:00+05:30",
                   "attempt_id": "fixture-attempt",
                   "photo_liveness_passed": True, "earbuds_route_passed": True, "active_audio_output_verified": True,
                   "full_track_passed": True, "bt_device": "Bluetooth earbuds", "track_name": "Configured track",
                   "playback_session": playback, "liveness_metadata": live, "evidence_id": "real-android-photo",
                   "image_base64": base64.b64encode(self.photo).decode()}
        with self.db._connect() as conn:
            ingested = self.gate.ingest_android_wakeup(conn, payload, device_id, "user_lok", f"{self.date}T09:01:00+05:30")
        self.assertEqual(ingested, self.date)
        checks = self.gate.inspect_checks(self.date)
        self.assertTrue(checks["hardware_passed"])
        self.assertFalse(checks["proof_approved"])
        self.assertFalse(self.gate.reconcile(self.date, reference_now=self.morning)["active"])
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET status='APPROVED', reviewed_at=? WHERE evidence_id='real-android-photo'", (f"{self.date} 09:00:00",))
        self.assertTrue(self.gate.reconcile(self.date, reference_now=self.morning)["active"])


if __name__ == "__main__":
    unittest.main()
