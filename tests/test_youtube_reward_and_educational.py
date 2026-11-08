import json
import os
import tempfile
import unittest

from core.database import StrictRecordsDB
from core.entertainment_reward_engine import (
    EARLY_PROOF_POINTS_THRESHOLD,
    EntertainmentRewardEngine,
    calculate_watched_seconds,
    evaluate_early_proof_eligibility,
    extract_canonical_video_id,
    format_seconds_to_mmss,
    is_early_submission,
    merge_watched_intervals,
    parse_mmss_to_seconds,
)
from core.taufa_video_evaluator import TaufaVideoEvaluator


class TestYouTubeRewardAndEducational(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        self.db = StrictRecordsDB(self.tmp_path)
        self.engine = EntertainmentRewardEngine(self.db)

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)

    # ── Feature 1: Early Proof Submission Tests ──

    def test_01_proof_submitted_11m_before_with_7pts_eligible(self):
        """Proof submitted 11 minutes before deadline and approved with 7 points is eligible."""
        result = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_11m",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:11:00+05:30",
            points_awarded=7,
            status="APPROVED",
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["eligible"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["reward"]["state"], "AVAILABLE")
        self.assertEqual(result["reward"]["duration_seconds"], 1200)

    def test_02_proof_submitted_9m_before_rejected(self):
        """Proof submitted 9 minutes before deadline fails 10-minute requirement."""
        result = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_9m",
            submission_received_iso="2026-11-01T10:02:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:11:00+05:30",
            points_awarded=7,
            status="APPROVED",
        )
        self.assertFalse(result["eligible"])
        self.assertIn("10 minutes before", result["reason"])

    def test_03_proof_submitted_early_but_rejected_status(self):
        """Proof submitted early but rejected by reviewer fails eligibility."""
        result = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_rejected",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=7,
            status="REJECTED",
        )
        self.assertFalse(result["eligible"])
        self.assertIn("not approved", result["reason"])

    def test_04_proof_submitted_early_with_6pts_rejected(self):
        """Proof awarded exactly 6 points fails > 6 threshold."""
        result = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_6pts",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=6,
            status="APPROVED",
        )
        self.assertFalse(result["eligible"])
        self.assertIn("> 6", result["reason"])

    def test_05_proof_submitted_early_with_8pts_eligible(self):
        """Proof awarded 8 points (exemplary) passes > 6 threshold."""
        result = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_8pts",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=8,
            status="APPROVED",
        )
        self.assertTrue(result["eligible"])

    def test_06_duplicate_approval_callback_is_idempotent(self):
        """Simultaneous/duplicate callbacks return existing record with zero extra grants."""
        res1 = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_dup",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=7,
            status="APPROVED",
        )
        res2 = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_dup",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=7,
            status="APPROVED",
        )
        self.assertTrue(res2["idempotent"])
        self.assertEqual(res1["reward"]["entitlement_id"], res2["reward"]["entitlement_id"])

    def test_07_activation_on_first_use_and_wall_clock_expiry(self):
        """Reward remains AVAILABLE until first use; then runs 20m wall time."""
        grant = self.engine.grant_early_proof_reward(
            account_id="user_test",
            device_id="dev_phone",
            proof_id="proof_act",
            submission_received_iso="2026-11-01T10:00:00+05:30",
            scheduled_deadline_iso="2026-11-01T10:15:00+05:30",
            points_awarded=7,
            status="APPROVED",
        )
        ent_id = grant["reward"]["entitlement_id"]

        # Before activation, status shows AVAILABLE and not active
        st_before = self.engine.get_reward_status("user_test")
        self.assertFalse(st_before["is_active"])
        self.assertEqual(len(st_before["available_rewards"]), 1)

        # First use activates the 20-minute timer
        act = self.engine.activate_reward(ent_id, "WINDOWS_EXE", "user_test")
        self.assertTrue(act["success"])
        self.assertEqual(act["remaining_seconds"], 1200)

        # After activation, active session is visible
        st_after = self.engine.get_reward_status("user_test")
        self.assertTrue(st_after["is_active"])
        self.assertEqual(st_after["active_session"]["state"], "ACTIVE")

    # ── Feature 2: Educational Video & Duration Tests ──

    def test_08_duration_parsing_and_formatting(self):
        """Exact duration 20:05 translates to 1205s; no rounding to 20 mins."""
        self.assertEqual(format_seconds_to_mmss(1205), "20:05")
        self.assertEqual(parse_mmss_to_seconds("20:05"), 1205)
        self.assertEqual(format_seconds_to_mmss(212), "03:32")
        self.assertEqual(parse_mmss_to_seconds("03:32"), 212)
        self.assertEqual(format_seconds_to_mmss(3665), "01:01:05")
        self.assertEqual(parse_mmss_to_seconds("01:01:05"), 3665)

    def test_09_taufa_response_parsing_educational(self):
        """Structured Taufa AI response correctly extracted into normalized schema."""
        raw_ai = (
            "```json\n"
            "{\n"
            '  "video_id": "cs50_lec1",\n'
            '  "canonical_url": "https://www.youtube.com/watch?v=cs50_lec1",\n'
            '  "decision": "EDUCATIONAL",\n'
            '  "duration_seconds": 1205,\n'
            '  "duration_formatted": "20:05",\n'
            '  "timeline": [\n'
            '    {"start_seconds": 0, "end_seconds": 300, "topic": "C Basics", "description": "Syntax and compilation"}\n'
            '  ],\n'
            '  "reason": "Authentic computer science lecture explaining C programming fundamentals.",\n'
            '  "confidence": 0.98,\n'
            '  "review_status": "COMPLETED"\n'
            "}\n"
            "```"
        )
        parsed = TaufaVideoEvaluator._parse_response(raw_ai, "cs50_lec1", "https://www.youtube.com/watch?v=cs50_lec1", 0, "Unknown")
        self.assertEqual(parsed["decision"], "EDUCATIONAL")
        self.assertEqual(parsed["duration_seconds"], 1205)
        self.assertEqual(parsed["duration_formatted"], "20:05")
        self.assertEqual(parsed["timeline"][0]["topic"], "C Basics")
        self.assertEqual(parsed["confidence"], 0.98)

    def test_10_taufa_response_parsing_not_educational(self):
        """Entertainment music video classified as NOT_EDUCATIONAL."""
        raw_ai = (
            "```json\n"
            "{\n"
            '  "video_id": "rickroll",\n'
            '  "canonical_url": "https://www.youtube.com/watch?v=rickroll",\n'
            '  "decision": "NOT_EDUCATIONAL",\n'
            '  "duration_seconds": 212,\n'
            '  "duration_formatted": "03:32",\n'
            '  "timeline": [],\n'
            '  "reason": "Music video containing commercial pop song and performance.",\n'
            '  "confidence": 1.0,\n'
            '  "review_status": "COMPLETED"\n'
            "}\n"
            "```"
        )
        parsed = TaufaVideoEvaluator._parse_response(raw_ai, "rickroll", "https://www.youtube.com/watch?v=rickroll", 212, "03:32")
        self.assertEqual(parsed["decision"], "NOT_EDUCATIONAL")
        self.assertEqual(parsed["duration_seconds"], 212)

    def test_11_playback_interval_merging_and_seeking_protection(self):
        """Forward seek skipped content is omitted; backward seek replayed content is not double-counted."""
        # 1. Play 0s to 100s
        # 2. Forward seek to 300s (skipping 100s-300s) and play to 400s
        # 3. Backward seek to 50s and replay to 80s
        raw = [[0, 100], [300, 400], [50, 80]]
        merged = merge_watched_intervals(raw)
        self.assertEqual(merged, [(0.0, 100.0), (300.0, 400.0)])
        total = calculate_watched_seconds(merged)
        # Total = 100 + 100 = 200s (skipped 200s is NOT counted, replay 30s is NOT double counted)
        self.assertEqual(total, 200.0)

    def test_12_playback_session_lifecycle_and_completion(self):
        """Video session reaches COMPLETED at full duration and locks out further access."""
        # 1. Save approval for 300s video
        self.engine.save_educational_approval(
            video_id="vid_short_edu",
            canonical_url="https://www.youtube.com/watch?v=vid_short_edu",
            decision="EDUCATIONAL",
            duration_seconds=300,
            duration_formatted="05:00",
            reason="Calculus derivatives lesson",
            account_id="user_test",
        )

        # 2. Start session
        session_res = self.engine.start_or_resume_playback_session("vid_short_edu", "user_test", "WINDOWS_EXE")
        self.assertTrue(session_res["allowed"])
        self.assertEqual(session_res["remaining_seconds"], 300)

        # 3. Heartbeat 1: watched 0 to 150s
        hb1 = self.engine.record_playback_heartbeat(
            video_id="vid_short_edu",
            account_id="user_test",
            device_id="WINDOWS_EXE",
            playback_state="PLAYING",
            playback_rate=1.0,
            interval_start=0.0,
            interval_end=150.0,
        )
        self.assertFalse(hb1["completed"])
        self.assertEqual(hb1["remaining_seconds"], 150)

        # 4. Heartbeat 2: watched 150 to 300s (finished!)
        hb2 = self.engine.record_playback_heartbeat(
            video_id="vid_short_edu",
            account_id="user_test",
            device_id="WINDOWS_EXE",
            playback_state="ENDED",
            playback_rate=1.0,
            interval_start=150.0,
            interval_end=300.0,
        )
        self.assertTrue(hb2["completed"])
        self.assertEqual(hb2["status"], "COMPLETED")
        self.assertEqual(hb2["remaining_seconds"], 0)

        # 5. Access decision is now BLOCKED because it's COMPLETED!
        decision = self.engine.get_educational_video_access_decision("vid_short_edu", "user_test")
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["is_completed"])
        self.assertIn("reload the page", decision["reason"])

        # 6. Re-initiating session returns COMPLETED and allowed=False
        resume_res = self.engine.start_or_resume_playback_session("vid_short_edu", "user_test", "WINDOWS_EXE")
        self.assertFalse(resume_res["allowed"])
        self.assertTrue(resume_res["is_completed"])


if __name__ == "__main__":
    unittest.main()
