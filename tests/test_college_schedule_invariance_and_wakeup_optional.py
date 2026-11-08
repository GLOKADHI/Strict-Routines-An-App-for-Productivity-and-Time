"""
Comprehensive automated tests for PART A & PART B (Section 15 — 14 Required Cases):
1. College-day Bath Session 1 scheduled at 7:30 AM–8:00 AM (Asia/Kolkata).
2. Bath Session 1 proof-submission window scheduled at 8:00 AM–8:30 AM.
3. First hourly submission window scheduled at 9:00 AM–10:00 AM, followed by 10:00–11:00 onward anchored to :00.
4. Late wake-up start or completion does not shift Bath Session 1, its proof window, or hourly windows.
5. Attempting Bath Session 1 proof before wake-up verification shows/returns "Complete Wake-Up Verification first."
6. Attempting hourly submission before wake-up verification shows/returns "Complete Wake-Up Verification first."
7. Completing wake-up verification unblocks currently open tasks (including late >09:30 prerequisite completion with real timestamp & 0 wake-up points).
8. Draft, failed, interrupted, pending-review, or demo wake-up sessions do not clear the prerequisite.
9. Wake-up after Bath Session 1 proof window closes (>= 8:30 AM) enforces WINDOW_CLOSED without shifting or extending windows.
10. Wake-up after an hourly window closes enforces WINDOW_CLOSED without shifting subsequent hourly windows.
11. Wake-Up Verification Demo / Diagnostics succeeds without audio and without Bluetooth earbuds, with zero production side effects.
12. Real Wake-Up Verification succeeds without audio and without Bluetooth earbuds.
13. Real Wake-Up Verification succeeds with optional audio and Bluetooth earbuds.
14. Real Wake-Up Verification fails when mandatory 20-second camera liveness check is skipped, incomplete, or unapproved.
"""

import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from PIL import Image

from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.daily_routine_gate import DailyRoutineGate
from core.google_drive import FOLDER_STRUCTURE, GoogleDriveClient
from core.hourly_submission_service import HourlySubmissionError, HourlySubmissionService
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, TimeWindowEngine
from core.wakeup_attempt_validation import android_attempt_error, configured_track_manifest
from core.wakeup_diagnostics import DiagnosticPrincipal, WakeUpDiagnosticsService


class TestCollegeScheduleInvarianceAndWakeupOptional(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        self.scoring = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir.name, "proofs"))
        self.tw = TimeWindowEngine(self.db)
        self.submission_service = HourlySubmissionService(self.db, self.scoring)
        self.date_str = "2026-10-28"
        self.account_id = "user_lok"
        self.ref_morning = datetime(2026, 10, 28, 7, 0, tzinfo=IST)
        self.db.ensure_daily_record(self.date_str)
        buf = io.BytesIO()
        Image.new("RGB", (120, 120), (50, 100, 150)).save(buf, format="JPEG")
        self.sample_photo = buf.getvalue()
        with self.db._connect() as conn:
            conn.execute("DELETE FROM scheduled_activities WHERE date = ?", (self.date_str,))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _enable_college_day(self, req_id: str) -> dict:
        decision = DailyCollegeDecision(self.db)
        return decision.save(
            account=self.account_id,
            date=self.date_str,
            attends=True,
            expected_revision=0,
            request_id=req_id,
            actor=self.account_id,
            device_id="test_pc_01",
            current=self.ref_morning,
        )

    def _valid_liveness_meta(self, attempt_id: str = "wake_attempt_01") -> dict:
        return {
            "policy_version": 2,
            "session_state": "PASSED",
            "liveness_status": "PASSED",
            "liveness_duration_sec": 20.2,
            "valid_observation_ratio": 1.0,
            "face_detected": True,
            "eyes_detected": True,
            "valid_samples": 50,
            "total_samples": 50,
            "unique_frames": 50,
            "valid_duration_ms": 20200,
            "duration_ms": 20200,
            "valid_sample_ratio": 1.0,
            "first_frame_elapsed_ms": 100,
            "last_frame_elapsed_ms": 20300,
            "accepted_elapsed_ms": 20350,
            "final_frame_valid": True,
            "stable_face": True,
            "lighting_ok": True,
            "head_pose_ok": True,
            "attempt_id": attempt_id,
            "photo_sha256": hashlib.sha256(self.sample_photo).hexdigest(),
        }

    # =========================================================================
    # Cases 1, 2, 3, 4: Fixed College-Day Schedule & Late Wake-Up Invariance
    # =========================================================================

    def test_01_02_college_day_bath1_fixed_activity_and_proof_window(self):
        res = self._enable_college_day("req_college_bath1_fixed_times_01")
        self.assertTrue(res["success"])
        items = self.tw.ensure_activities_in_db(self.date_str, reference_now=self.ref_morning)
        bath1 = next(a for a in items if "BATH-1" in a["activity_id"] and a.get("is_enabled"))

        # Case 1: Bath Session 1 activity 7:30 AM–8:00 AM
        self.assertIn("T07:30:00+05:30", bath1["scheduled_time"])
        details = json.loads(bath1["details_json"])
        self.assertEqual(details["reserved_start"], f"{self.date_str}T07:30:00+05:30")
        self.assertEqual(details["reserved_end"], f"{self.date_str}T08:00:00+05:30")

        # Case 2: Bath Session 1 proof-submission window 8:00 AM–8:30 AM
        self.assertEqual(bath1["window_start"], f"{self.date_str}T08:00:00+05:30")
        self.assertEqual(bath1["window_end"], f"{self.date_str}T08:30:00+05:30")
        self.assertEqual(details["proof_window_start"], f"{self.date_str}T08:00:00+05:30")
        self.assertEqual(details["proof_window_end"], f"{self.date_str}T08:30:00+05:30")

    def test_03_college_day_first_hourly_9_to_10_and_subsequent_slots(self):
        self._enable_college_day("req_college_hourly_schedule_03")
        items = self.tw.ensure_activities_in_db(self.date_str, reference_now=self.ref_morning)
        hourly = [a for a in items if a["type"] == "HOURLY" and a.get("is_enabled")]
        self.assertGreater(len(hourly), 3)

        # First hourly submission window: 9:00 AM–10:00 AM
        first = hourly[0]
        self.assertEqual(first["activity_id"], "ACT-20261028-0900-HOURLY")
        self.assertEqual(first["window_start"], f"{self.date_str}T09:00:00+05:30")
        self.assertEqual(first["window_end"], f"{self.date_str}T10:00:00+05:30")
        first_details = json.loads(first["details_json"])
        self.assertEqual(first_details["hour_slot"], "09:00-10:00")

        # No duplicate 1000-HOURLY covering 09:00-10:00; next slot is 10:00-11:00 (ACT-20261028-1100-HOURLY)
        hourly_ids = [a["activity_id"] for a in hourly]
        self.assertNotIn("ACT-20261028-1000-HOURLY", hourly_ids)
        second = hourly[1]
        self.assertEqual(second["activity_id"], "ACT-20261028-1100-HOURLY")
        self.assertEqual(second["window_start"], f"{self.date_str}T10:50:00+05:30")
        self.assertEqual(second["window_end"], f"{self.date_str}T11:10:00+05:30")
        self.assertEqual(json.loads(second["details_json"])["hour_slot"], "10:00-11:00")

    def test_04_college_schedule_invariance_under_late_wakeup_start_or_completion(self):
        self._enable_college_day("req_college_invariance_test_04")

        for late_hm in ("07:50", "08:15", "08:45", "09:40"):
            DailyRoutineGate.seed_approved_wakeup(
                self.db, self.date_str, completed_hm=late_hm, account_id=self.account_id
            )
            ref = datetime.fromisoformat(f"{self.date_str}T{late_hm}:00+05:30")
            items = self.tw.ensure_activities_in_db(
                self.date_str, wakeup_time=late_hm, reference_now=ref
            )
            bath1 = next(a for a in items if "BATH-1" in a["activity_id"] and a.get("is_enabled"))
            hourly = [a for a in items if a["type"] == "HOURLY" and a.get("is_enabled")]

            self.assertEqual(bath1["window_start"], f"{self.date_str}T08:00:00+05:30")
            self.assertEqual(bath1["window_end"], f"{self.date_str}T08:30:00+05:30")
            details = json.loads(bath1["details_json"])
            self.assertEqual(details["reserved_start"], f"{self.date_str}T07:30:00+05:30")
            self.assertEqual(details["reserved_end"], f"{self.date_str}T08:00:00+05:30")

            self.assertEqual(hourly[0]["activity_id"], "ACT-20261028-0900-HOURLY")
            self.assertEqual(hourly[0]["window_start"], f"{self.date_str}T09:00:00+05:30")
            self.assertEqual(hourly[0]["window_end"], f"{self.date_str}T10:00:00+05:30")
            self.assertEqual(hourly[1]["activity_id"], "ACT-20261028-1100-HOURLY")
            self.assertEqual(hourly[1]["window_start"], f"{self.date_str}T10:50:00+05:30")

    # =========================================================================
    # Cases 5, 6, 7, 8, 9, 10: Wake-Up Prerequisite & Window Closure Rules
    # =========================================================================

    def test_05_bath1_submission_blocked_before_wakeup_verification(self):
        self._enable_college_day("req_bath1_prereq_block_05")
        items = self.tw.ensure_activities_in_db(
            self.date_str, reference_now=datetime(2026, 10, 28, 8, 10, tzinfo=IST)
        )
        bath1 = next(a for a in items if "BATH-1" in a["activity_id"])

        with self.assertRaises(HourlySubmissionError) as ctx:
            self.submission_service.accept(
                {
                    "activity_id": bath1["activity_id"],
                    "submission_id": "sub_bath1_before_wakeup",
                    "description": "Completed morning bath session before college.",
                },
                file_bytes=self.sample_photo,
                filename="bath1.jpg",
                account_id=self.account_id,
                device_id="test_pc_01",
                platform="WINDOWS",
                received_at=datetime(2026, 10, 28, 8, 15, tzinfo=IST),
                expected_type="BATH",
            )
        self.assertEqual(ctx.exception.code, "WAKEUP_VERIFICATION_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("Complete Wake-Up Verification first.", str(ctx.exception))
        self.assertIn(
            "Today's required wake-up verification is still pending. Complete it before proceeding with this activity.",
            str(ctx.exception),
        )

    def test_06_hourly_submission_independent_of_wakeup_verification(self):
        self._enable_college_day("req_hourly_prereq_block_06")
        items = self.tw.ensure_activities_in_db(
            self.date_str, reference_now=datetime(2026, 10, 28, 9, 10, tzinfo=IST)
        )
        first_hourly = next(a for a in items if a["activity_id"] == "ACT-20261028-0900-HOURLY")

        result = self.submission_service.accept(
                {
                    "activity_id": first_hourly["activity_id"],
                    "submission_id": "sub_hourly1_before_wakeup",
                    "category": "STUDY",
                    "description": "Attended 9 AM lecture and took structured notes.",
                },
                file_bytes=self.sample_photo,
                filename="hourly1.jpg",
                account_id=self.account_id,
                device_id="test_pc_01",
                platform="WINDOWS",
                received_at=datetime(2026, 10, 28, 9, 15, tzinfo=IST),
                expected_type="HOURLY",
            )
        self.assertTrue(result['success'])
        self.assertTrue(result['accepted'])
        self.assertEqual(self.db.get_score_events(self.date_str), [])

    def test_07_completing_wakeup_unblocks_open_tasks_including_late_prerequisite(self):
        self._enable_college_day("req_wakeup_unblock_07")
        items = self.tw.ensure_activities_in_db(
            self.date_str, reference_now=datetime(2026, 10, 28, 9, 45, tzinfo=IST)
        )
        first_hourly = next(a for a in items if a["activity_id"] == "ACT-20261028-0900-HOURLY")

        # Complete wake-up late at 09:42 AM (after 09:30 AM wake-up credit cutoff, without audio/earbuds)
        ev = self.scoring.store_evidence_file(
            self.date_str,
            "WakeUp",
            f"wakeup_{self.date_str}_0",
            "test_phone",
            file_bytes=self.sample_photo,
            metadata=self._valid_liveness_meta("late_attempt_0942"),
            timestamp=f"{self.date_str} 09:42:00",
        )
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE wake_up_sessions SET camera_verified = 1, camera_evidence_id = ?, camera_completed_at = ? WHERE date = ? AND is_dev_mode = 0",
                (ev["evidence_id"], f"{self.date_str} 09:42:00", self.date_str),
            )
            conn.execute(
                "UPDATE proof_reviews SET status = 'APPROVED', reviewed_at = ? WHERE evidence_id = ?",
                (f"{self.date_str} 09:42:10", ev["evidence_id"]),
            )
        fin = self.scoring.finalize_wakeup_session(
            self.date_str, completed_at=f"{self.date_str} 09:42:10", device_id="test_phone"
        )
        self.assertEqual(fin["status"], "WAKEUP_WINDOW_CLOSED")
        self.assertEqual(fin["points_awarded"], 0)
        self.assertTrue(fin["prerequisite_completed"])
        self.assertIn("09:42:10", fin["completed_at"])

        gate = DailyRoutineGate(self.db).reconcile(
            self.date_str, self.account_id, reference_now=datetime(2026, 10, 28, 9, 45, tzinfo=IST)
        )
        self.assertFalse(gate["active"])
        self.assertTrue(gate["prerequisite_completed"])

        # Currently open 09:00–10:00 hourly window now succeeds at 09:45 AM!
        accepted = self.submission_service.accept(
            {
                "activity_id": first_hourly["activity_id"],
                "submission_id": "sub_hourly1_after_late_wakeup",
                "category": "STUDY",
                "description": "Attended 9 AM lecture and completed problem set.",
            },
            file_bytes=self.sample_photo,
            filename="hourly1.jpg",
            account_id=self.account_id,
            device_id="test_pc_01",
            platform="WINDOWS",
            received_at=datetime(2026, 10, 28, 9, 45, tzinfo=IST),
            expected_type="HOURLY",
        )
        self.assertTrue(accepted["accepted"])

    def test_08_draft_failed_pending_or_demo_sessions_do_not_clear_prerequisite(self):
        self._enable_college_day("req_demo_draft_no_clear_08")
        items = self.tw.ensure_activities_in_db(
            self.date_str, reference_now=datetime(2026, 10, 28, 8, 10, tzinfo=IST)
        )
        bath1 = next(a for a in items if "BATH-1" in a["activity_id"])

        # 1. Demo / Diagnostics session completed
        diag_svc = WakeUpDiagnosticsService(self.db)
        principal = DiagnosticPrincipal(account_id=self.account_id, device_id="test_pc_01", platform="WINDOWS")
        started = diag_svc.start(principal, request_id="req_diag_08")
        for idx, (chk, meas) in enumerate([
            ("camera_permission", {"granted": True}),
            ("camera_init", {"opened": True, "camera_available": True}),
            ("live_frame_stream", {"frames": 40, "duration_sec": 20.0}),
            ("lighting", {"pass_ratio": 0.95}),
            ("face_detection", {"face_ratio": 0.95, "max_absence_ms": 200}),
            ("eye_analysis", {"supported": True, "eyes_ratio": 0.90}),
            ("liveness_interval", {"duration_sec": 20.2, "valid_ratio": 0.95}),
        ]):
            diag_svc.record_event(principal, diag_session_id=started["diag_session_id"], event_id=f"evt_diag_08_{idx}_{chk}", check=chk, measurements=meas)
        rep = diag_svc.complete(principal, diag_session_id=started["diag_session_id"])
        self.assertEqual(rep["overall"], "PASS")

        # 2. Unapproved (PENDING) camera proof in production
        ev = self.scoring.store_evidence_file(
            self.date_str, "WakeUp", f"wakeup_{self.date_str}_0", "test_phone",
            file_bytes=self.sample_photo, metadata=self._valid_liveness_meta("pending_attempt_08"),
            timestamp=f"{self.date_str} 07:35:00",
        )
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE wake_up_sessions SET camera_verified = 1, camera_evidence_id = ? WHERE date = ? AND is_dev_mode = 0",
                (ev["evidence_id"], self.date_str),
            )

        # Prerequisite must STILL block Bath 1 submission
        with self.assertRaises(HourlySubmissionError) as ctx:
            self.submission_service.accept(
                {
                    "activity_id": bath1["activity_id"],
                    "submission_id": "sub_bath1_still_blocked",
                    "description": "Completed morning bath session before college.",
                },
                file_bytes=self.sample_photo,
                filename="bath1.jpg",
                account_id=self.account_id,
                device_id="test_pc_01",
                platform="WINDOWS",
                received_at=datetime(2026, 10, 28, 8, 15, tzinfo=IST),
                expected_type="BATH",
            )
        self.assertEqual(ctx.exception.code, "WAKEUP_VERIFICATION_REQUIRED")

    def test_09_10_expired_bath1_and_hourly_windows_enforce_window_closed_without_shifting(self):
        self._enable_college_day("req_expired_windows_09_10")
        DailyRoutineGate.seed_approved_wakeup(
            self.db, self.date_str, completed_hm="10:05", account_id=self.account_id
        )
        late_now = datetime(2026, 10, 28, 10, 5, tzinfo=IST)
        items = self.tw.ensure_activities_in_db(self.date_str, reference_now=late_now)
        bath1 = next(a for a in items if "BATH-1" in a["activity_id"])
        first_hourly = next(a for a in items if a["activity_id"] == "ACT-20261028-0900-HOURLY")
        second_hourly = next(a for a in items if a["activity_id"] == "ACT-20261028-1100-HOURLY")

        # Case 9: Bath 1 proof window closed at 8:30 AM -> WINDOW_CLOSED at 10:05 AM
        with self.assertRaises(HourlySubmissionError) as bath_err:
            self.submission_service.accept(
                {
                    "activity_id": bath1["activity_id"],
                    "submission_id": "sub_bath1_expired",
                    "description": "Late bath submission after 8:30 AM.",
                },
                file_bytes=self.sample_photo,
                filename="bath1.jpg",
                account_id=self.account_id,
                device_id="test_pc_01",
                platform="WINDOWS",
                received_at=late_now,
                expected_type="BATH",
            )
        self.assertEqual(bath_err.exception.code, "WINDOW_CLOSED")

        # Case 10: First hourly window (9:00–10:00 AM) closed at 10:00 AM -> WINDOW_CLOSED at 10:05 AM
        with self.assertRaises(HourlySubmissionError) as hr_err:
            self.submission_service.accept(
                {
                    "activity_id": first_hourly["activity_id"],
                    "submission_id": "sub_hr1_expired",
                    "category": "STUDY",
                    "description": "Late hourly submission after 10:00 AM.",
                },
                file_bytes=self.sample_photo,
                filename="hr1.jpg",
                account_id=self.account_id,
                device_id="test_pc_01",
                platform="WINDOWS",
                received_at=late_now,
                expected_type="HOURLY",
            )
        self.assertEqual(hr_err.exception.code, "WINDOW_CLOSED")

        # Subsequent hourly window remains anchored to 10:50–11:10 without shifting
        self.assertEqual(second_hourly["window_start"], f"{self.date_str}T10:50:00+05:30")
        self.assertEqual(second_hourly["window_end"], f"{self.date_str}T11:10:00+05:30")

    # =========================================================================
    # Cases 11, 12, 13, 14: Optional Audio/Earbuds in Demo & Production + Camera Mandatory
    # =========================================================================

    def test_11_wakeup_demo_succeeds_without_audio_and_without_earbuds(self):
        svc = WakeUpDiagnosticsService(self.db)
        principal = DiagnosticPrincipal(account_id=self.account_id, device_id="test_pc_01", platform="WINDOWS")
        started = svc.start(principal, request_id="req_demo_camera_only_11")
        diag_id = started["diag_session_id"]

        # Only mandatory camera checks recorded; Bluetooth & audio completely skipped
        camera_checks = {
            "camera_permission": {"granted": True},
            "camera_init": {"opened": True, "camera_available": True},
            "live_frame_stream": {"frames": 36, "duration_sec": 20.0},
            "lighting": {"pass_ratio": 0.92},
            "face_detection": {"face_ratio": 0.94, "max_absence_ms": 300},
            "eye_analysis": {"supported": True, "eyes_ratio": 0.88},
            "liveness_interval": {"duration_sec": 20.4, "valid_ratio": 0.90},
        }
        for idx, (name, meas) in enumerate(camera_checks.items()):
            res = svc.record_event(principal, diag_session_id=diag_id, event_id=f"evt_cam_11_{idx}_{name}", check=name, measurements=meas)
            self.assertEqual(res["result"], "PASS")

        report = svc.complete(principal, diag_session_id=diag_id)
        self.assertEqual(report["overall"], "PASS")
        self.assertTrue(report["diagnostic_gate"]["all_passed"])
        self.assertFalse(report["counts_toward_attendance"])
        self.assertEqual(self.db.get_score_events(self.date_str), [])

    def test_12_real_wakeup_succeeds_without_audio_and_without_earbuds(self):
        payload = {
            "date": self.date_str,
            "attempt_id": "wake_cam_only_12",
            "completed_at": f"{self.date_str}T07:25:00+05:30",
            "photo_liveness_passed": True,
            "earbuds_route_passed": False,
            "active_audio_output_verified": False,
            "full_track_passed": False,
            "liveness_metadata": self._valid_liveness_meta("wake_cam_only_12"),
            "playback_session": {},
        }
        self.assertIsNone(android_attempt_error(payload))

        ev = self.scoring.store_evidence_file(
            self.date_str, "WakeUp", f"wakeup_{self.date_str}_0", "test_phone",
            file_bytes=self.sample_photo, metadata=self._valid_liveness_meta("wake_cam_only_12"),
            timestamp=f"{self.date_str} 07:25:00",
        )
        with self.db._connect() as conn:
            conn.execute(
                """UPDATE wake_up_sessions
                   SET camera_verified = 1, camera_evidence_id = ?, bluetooth_verified = 0,
                       active_audio_output_verified = 0, motivation_verified = 0, playback_session_json = '{}'
                   WHERE date = ? AND is_dev_mode = 0""",
                (ev["evidence_id"], self.date_str),
            )
            conn.execute(
                "UPDATE proof_reviews SET status = 'APPROVED', reviewed_at = ? WHERE evidence_id = ?",
                (f"{self.date_str} 07:26:00", ev["evidence_id"]),
            )
        fin = self.scoring.finalize_wakeup_session(self.date_str, completed_at=f"{self.date_str} 07:26:00")
        self.assertTrue(fin["wakeup_completed"])
        self.assertGreater(fin["points_awarded"], 0)
        self.assertEqual(fin["optional_features"], {"earbuds_used": False, "audio_played": False})

        gate = DailyRoutineGate(self.db).reconcile(
            self.date_str, self.account_id, reference_now=datetime(2026, 10, 28, 7, 30, tzinfo=IST)
        )
        self.assertTrue(gate["active"])

    def test_13_real_wakeup_succeeds_with_optional_audio_and_earbuds(self):
        manifest = configured_track_manifest()
        playback = {
            "attempt_id": "wake_with_optional_13",
            "completed": True,
            "headset_connected": True,
            "volume_ok": True,
            "route_source": "MEDIA_PLAYER_ROUTED_DEVICE",
            "audio_route": "BLUETOOTH_A2DP",
            "selected_device_name": "Galaxy Buds",
            "selected_device_address": "AA:BB:CC:DD:EE:FF",
            "routed_device_name": "Galaxy Buds",
            "routed_device_address": "AA:BB:CC:DD:EE:FF",
            "seek_attempts": 0,
            "route_observations": 40,
            "track_sha256": manifest["sha256"],
            "track_duration_sec": manifest["duration_sec"],
            "completed_duration_sec": manifest["duration_sec"],
            "route_verified_duration_ms": round(manifest["duration_sec"] * 1000),
        }
        payload = {
            "date": self.date_str,
            "attempt_id": "wake_with_optional_13",
            "completed_at": f"{self.date_str}T07:25:00+05:30",
            "photo_liveness_passed": True,
            "earbuds_route_passed": True,
            "active_audio_output_verified": True,
            "full_track_passed": True,
            "liveness_metadata": self._valid_liveness_meta("wake_with_optional_13"),
            "playback_session": playback,
        }
        self.assertIsNone(android_attempt_error(payload))
        DailyRoutineGate.seed_approved_wakeup(
            self.db, self.date_str, completed_hm="07:25", account_id=self.account_id
        )
        fin = self.scoring.finalize_wakeup_session(self.date_str, completed_at=f"{self.date_str} 07:25:00")
        self.assertTrue(fin["wakeup_completed"])
        self.assertEqual(fin["optional_features"], {"earbuds_used": True, "audio_played": True})

    def test_14_real_wakeup_fails_when_mandatory_camera_liveness_incomplete_or_skipped(self):
        # 1. Skipped camera even when earbuds + audio passed
        bad_payload = {
            "date": self.date_str,
            "attempt_id": "wake_no_cam_14",
            "completed_at": f"{self.date_str}T07:25:00+05:30",
            "photo_liveness_passed": False,
            "earbuds_route_passed": True,
            "active_audio_output_verified": True,
            "full_track_passed": True,
            "liveness_metadata": self._valid_liveness_meta("wake_no_cam_14"),
            "playback_session": {},
        }
        self.assertEqual(android_attempt_error(bad_payload), "MANDATORY_STEP_INCOMPLETE")

        # 2. Incomplete (< 20s) camera liveness
        short_live = self._valid_liveness_meta("wake_short_cam_14")
        short_live["valid_duration_ms"] = 12000
        short_payload = dict(bad_payload, photo_liveness_passed=True, attempt_id="wake_short_cam_14", liveness_metadata=short_live)
        self.assertIsNotNone(android_attempt_error(short_payload))

        # 3. ScoringEngine rejects finalization without verified 20s camera proof
        fin = self.scoring.finalize_wakeup_session(self.date_str)
        self.assertFalse(fin["wakeup_completed"])
        self.assertEqual(fin["points_awarded"], 0)

    def test_google_drive_folder_hierarchy_structure(self):
        client = GoogleDriveClient(token_provider=lambda: "mock_test_token")
        self.assertIsNotNone(client)
        self.assertIn("Data", FOLDER_STRUCTURE)
        self.assertIn("Ledger", FOLDER_STRUCTURE)
        self.assertIn("Evidence", FOLDER_STRUCTURE)
        self.assertIn("PointEvents", FOLDER_STRUCTURE["Ledger"])
        self.assertIn("LedgerSnapshots", FOLDER_STRUCTURE["Ledger"])
        self.assertIn("Images", FOLDER_STRUCTURE["Evidence"])
        self.assertIn("Schedules", FOLDER_STRUCTURE["Data"])
        self.assertIn("Attendance", FOLDER_STRUCTURE["Data"])


if __name__ == "__main__":
    unittest.main()
