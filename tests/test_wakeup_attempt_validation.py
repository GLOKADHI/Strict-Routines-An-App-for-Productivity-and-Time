import unittest

from core.wakeup_attempt_validation import android_attempt_error, camera_policy_error, configured_track_manifest, playback_policy_error
from tests import test_daily_routine_gate as gate_fixtures


def measured_attempt():
    manifest = configured_track_manifest()
    live = dict(policy_version=2, session_state="PASSED", attempt_id="attempt-test",
                valid_samples=96, total_samples=100, unique_frames=100,
                valid_duration_ms=20191, duration_ms=20191, valid_sample_ratio=.96,
                first_frame_elapsed_ms=1000, last_frame_elapsed_ms=22108, accepted_elapsed_ms=22193,
                final_frame_valid=True, stable_face=True, eyes_detected=True, lighting_ok=True, head_pose_ok=True)
    playback = dict(attempt_id="attempt-test", completed=True, headset_connected=True, volume_ok=True,
                    route_source="MEDIA_PLAYER_ROUTED_DEVICE", audio_route="BLUETOOTH_A2DP",
                    selected_device_name="Selected buds", selected_device_address="AA:BB:CC:DD:EE:FF",
                    routed_device_name="Selected buds", routed_device_address="AA:BB:CC:DD:EE:FF",
                    seek_attempts=0, route_observations=70, track_sha256=manifest["sha256"],
                    track_duration_sec=manifest["duration_sec"], completed_duration_sec=manifest["duration_sec"],
                    route_verified_duration_ms=round(manifest["duration_sec"] * 1000))
    return dict(date="2026-10-12", completed_at="2026-10-12T09:00:00+05:30", attempt_id="attempt-test",
                photo_liveness_passed=True, earbuds_route_passed=True, active_audio_output_verified=True,
                full_track_passed=True, liveness_metadata=live, playback_session=playback)


class MeasuredAttemptTests(unittest.TestCase):
    def test_complete_measured_attempt(self):
        self.assertIsNone(android_attempt_error(measured_attempt()))

    def test_each_step_and_same_attempt_are_mandatory(self):
        payload = measured_attempt(); payload["photo_liveness_passed"] = False
        self.assertEqual(android_attempt_error(payload), "MANDATORY_STEP_INCOMPLETE")

        skipped_optional = measured_attempt()
        skipped_optional.update(
            earbuds_route_passed=False,
            active_audio_output_verified=False,
            full_track_passed=False,
            playback_session={},
        )
        self.assertIsNone(android_attempt_error(skipped_optional))

        for field in ("liveness_metadata", "playback_session"):
            payload = measured_attempt(); payload[field]["attempt_id"] = "other"
            self.assertEqual(android_attempt_error(payload), "ATTEMPT_MISMATCH")

    def test_incomplete_stale_inconsistent_camera_is_rejected(self):
        for changes in ({"valid_duration_ms": 19999}, {"valid_samples": 39}, {"unique_frames": 99},
                        {"accepted_elapsed_ms": 23000}, {"valid_sample_ratio": float("nan")},
                        {"eyes_detected": False}, {"stable_face": False}):
            with self.subTest(changes=changes):
                live = measured_attempt()["liveness_metadata"]; live.update(changes)
                self.assertIsNotNone(camera_policy_error(live))

    def test_paired_speaker_other_route_never_passes(self):
        for changes in ({"route_source": "AVAILABLE_DEVICES"}, {"audio_route": "SPEAKER"},
                        {"routed_device_address": ""}, {"routed_device_address": "11:22:33:44:55:66"},
                        {"headset_connected": False}):
            payload = measured_attempt(); payload["playback_session"].update(changes)
            self.assertIsNotNone(android_attempt_error(payload))

    def test_full_configured_asset_and_measured_duration_required(self):
        for changes in ({"completed_duration_sec": 7.9}, {"route_verified_duration_ms": 7999},
                        {"completed": False}, {"seek_attempts": 1}, {"route_observations": 0},
                        {"track_sha256": "different"}, {"track_duration_sec": 5},
                        {"track_duration_sec": float("nan")}, {"route_verified_duration_ms": float("inf")}):
            payload = measured_attempt(); payload["playback_session"].update(changes)
            self.assertIsNotNone(android_attempt_error(payload))

    def test_submission_boundaries_demo_and_timezone(self):
        # The current restoration request explicitly reinstates the 09:30 cutoff.
        for when, accepted in (("06:59:59", False), ("07:00:00", True), ("09:29:59", True), ("09:30:00", False), ("10:00:00", False)):
            payload = measured_attempt(); payload["completed_at"] = f"2026-10-12T{when}+05:30"
            self.assertEqual(android_attempt_error(payload) is None, accepted)
        payload = measured_attempt(); payload["completed_at"] = "2026-10-12T03:30:00Z"
        self.assertIsNone(android_attempt_error(payload))
        payload["date"] = "2026-10-11"
        self.assertEqual(android_attempt_error(payload), "WAKEUP_WINDOW_CLOSED")
        for flag in ("demo", "is_dev_mode"):
            payload = measured_attempt(); payload[flag] = True
            self.assertEqual(android_attempt_error(payload), "DEMO_CANNOT_SUBMIT")


class WakeUpCreditTests(unittest.TestCase):
    date = gate_fixtures.DailyRoutineGateTests.date
    setUp = gate_fixtures.DailyRoutineGateTests.setUp
    tearDown = gate_fixtures.DailyRoutineGateTests.tearDown
    save_checks = gate_fixtures.DailyRoutineGateTests.save_checks

    def positive_points(self):
        return sum(max(0, e["points"]) for e in self.db.get_score_events(self.date))

    def test_camera_approval_alone_never_awards(self):
        # Mandatory camera missing -> proof approval must NEVER award points
        missing_cam = self.save_checks(approved=False, camera_verified=0)
        blocked_res = self.scoring.apply_proof_approval(missing_cam["evidence_id"], "independent-admin", points=12)
        self.assertEqual(blocked_res["points_awarded"], 0)
        self.assertEqual(self.positive_points(), 0)
        self.assertFalse(blocked_res["wake_up_result"].get("wakeup_completed", False))

        # Mandatory camera verified with optional earbuds & audio skipped -> awards normally once approved
        evidence = self.save_checks(approved=False, bluetooth_verified=0, active_audio_output_verified=0, motivation_verified=0, playback_session_json="{}")
        result = self.scoring.apply_proof_approval(evidence["evidence_id"], "independent-admin", points=12)
        self.assertEqual(result["points_awarded"], 12)
        self.assertEqual(self.positive_points(), 12)
        self.assertTrue(result["wake_up_result"]["wakeup_completed"])

    def test_pending_review_never_marks_attendance_or_points(self):
        self.save_checks(approved=False)
        result = self.scoring.finalize_wakeup_session(self.date)
        self.assertEqual(result["status"], "PENDING_REVIEW")
        self.assertEqual(self.positive_points(), 0)
        self.assertFalse(self.gate.reconcile(self.date, reference_now=self.morning)["active"])

    def test_all_three_approved_points_exactly_once_after_restart(self):
        evidence = self.save_checks(approved=False)
        first = self.scoring.apply_proof_approval(evidence["evidence_id"], "independent-admin", points=12)
        self.assertTrue(first["wake_up_result"]["wakeup_completed"])
        self.assertEqual(first["points_awarded"], 12)
        from core.scoring_engine import ScoringEngine
        restarted = ScoringEngine(self.db, storage_root=self.scoring.storage_root)
        second = restarted.apply_proof_approval(evidence["evidence_id"], "independent-admin", points=12)
        self.assertEqual(second["points_awarded"], 0)
        self.assertEqual(self.positive_points(), 12)

    def test_expired_and_demo_never_award(self):
        self.save_checks(completed_at=f"{self.date} 10:00:00")
        self.assertFalse(self.scoring.finalize_wakeup_session(self.date)["wakeup_completed"])
        self.assertEqual(self.scoring.finalize_wakeup_session(self.date, is_dev_mode=True)["status"], "DEMO_NO_CREDIT")
        self.assertEqual(self.positive_points(), 0)

    def test_invalid_sync_does_not_complete_any_step(self):
        self.db.process_sync_events_batch("unregistered", [{"event_id": "invalid-test", "event_type": "WAKEUP_VERIFIED", "payload": {"date": self.date}}])
        with self.db._connect() as conn:
            row = conn.execute("SELECT camera_verified, bluetooth_verified, motivation_verified FROM wake_up_sessions WHERE date=? AND is_dev_mode=0", (self.date,)).fetchone()
        self.assertEqual(tuple(row), (0, 0, 0))
        self.assertEqual(self.positive_points(), 0)

    def test_measured_android_attempt_retention_approval_and_replay(self):
        import base64
        import hashlib
        payload = measured_attempt()
        payload.update(bt_device="Selected buds", track_name="Configured track", evidence_id="measured-evidence",
                       image_base64=base64.b64encode(self.photo).decode())
        payload["liveness_metadata"]["photo_sha256"] = hashlib.sha256(self.photo).hexdigest()
        paired = self.db.create_pairing_session(server_endpoint="http://192.168.1.2:8765")
        device = self.db.handshake_pairing(session_key=paired["session_key"], device_id="measured-device", device_name="Test phone", app_version="1.15.12")
        with self.db._connect() as conn:
            self.assertEqual(self.gate.ingest_android_wakeup(conn, payload, device["device_id"], "user_lok", f"{self.date}T09:01:00+05:30"), self.date)
        self.assertEqual(self.scoring.finalize_wakeup_session(self.date)["status"], "PENDING_REVIEW")
        self.assertEqual(self.positive_points(), 0)
        first = self.scoring.apply_proof_approval("measured-evidence", "independent-admin", points=12)
        self.assertTrue(first["wake_up_result"]["wakeup_completed"])
        self.assertEqual(first["points_awarded"], 12)
        with self.db._connect() as conn:
            self.gate.ingest_android_wakeup(conn, payload, device["device_id"], "user_lok", f"{self.date}T09:02:00+05:30")
        self.assertEqual(self.scoring.finalize_wakeup_session(self.date)["points_awarded"], 0)
        self.assertEqual(self.positive_points(), 12)
