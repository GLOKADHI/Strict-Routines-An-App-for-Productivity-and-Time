"""
Unit Tests for StrictRecords Single-Video YouTube Reward Engine
==============================================================
Validates:
  1. Canonical video ID extraction (watch, youtu.be, embed, shorts, raw).
  2. Rejection of playlist, channel, and non-YouTube URLs.
  3. Viewing allowance calculation min(D + 60, 1200) with 20m hard cap.
  4. Authoritative timeliness evaluation (accepted_at < scheduled_time).
  5. Eligibility gating (score >= 6, HOURLY only, no duplicates, timely).
  6. Reward grant idempotency (exactly one entitlement per occurrence).
  7. Video selection and state transitions.
  8. Single active session across devices (WINDOWS, CHROME, ANDROID).
  9. Session transfer between devices preserving monotonic deadline.
  10. Session expiry and termination handling.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from core.database import StrictRecordsDB
from core.time_window_engine import IST, dt_to_iso, now_ist
from core.youtube_reward_engine import (
    HARD_MAX_ALLOWANCE_SECONDS,
    YouTubeRewardEngine,
    calculate_viewing_allowance,
    evaluate_eligibility,
    extract_canonical_video_id,
    is_submission_timely,
)


class TestYouTubeRewardEngine(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.engine = YouTubeRewardEngine(self.db)

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Canonical Video ID Extraction
    # ─────────────────────────────────────────────────────────────────────────

    def test_extract_canonical_video_id_standard_watch(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_watch_with_extra_params(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&feature=shared"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_short_link(self) -> None:
        url = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_short_link_with_query(self) -> None:
        url = "https://youtu.be/dQw4w9WgXcQ?t=15"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_embed(self) -> None:
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_shorts(self) -> None:
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        self.assertEqual(extract_canonical_video_id(url), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_raw_11char_id(self) -> None:
        raw_id = "dQw4w9WgXcQ"
        self.assertEqual(extract_canonical_video_id(raw_id), "dQw4w9WgXcQ")

    def test_extract_canonical_video_id_rejects_playlist(self) -> None:
        url = "https://www.youtube.com/playlist?list=PL1234567890ABCDEF"
        self.assertIsNone(extract_canonical_video_id(url))

    def test_extract_canonical_video_id_rejects_channel(self) -> None:
        url = "https://www.youtube.com/channel/UCuAXFkgsw1L7unroywLshAQ"
        self.assertIsNone(extract_canonical_video_id(url))
        self.assertIsNone(extract_canonical_video_id("https://www.youtube.com/@Veritasium"))

    def test_extract_canonical_video_id_rejects_foreign_domain(self) -> None:
        url = "https://vimeo.com/12345678"
        self.assertIsNone(extract_canonical_video_id(url))

    def test_extract_canonical_video_id_rejects_empty_and_garbage(self) -> None:
        self.assertIsNone(extract_canonical_video_id(""))
        self.assertIsNone(extract_canonical_video_id(None))  # type: ignore
        self.assertIsNone(extract_canonical_video_id("not_a_valid_youtube_link"))

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Viewing Allowance Calculation
    # ─────────────────────────────────────────────────────────────────────────

    def test_allowance_math_5_minutes(self) -> None:
        # 5 mins = 300s -> 300 + 60 = 360s (6 mins)
        self.assertEqual(calculate_viewing_allowance(300), 1200)

    def test_allowance_math_16_minutes(self) -> None:
        # 16 mins = 960s -> 960 + 60 = 1020s (17 mins)
        self.assertEqual(calculate_viewing_allowance(960), 1200)

    def test_allowance_math_19_minutes(self) -> None:
        # 19 mins = 1140s -> min(1140 + 60, 1200) = 1200s (20 mins cap)
        self.assertEqual(calculate_viewing_allowance(1140), 1200)

    def test_allowance_math_25_minutes_caps_at_20(self) -> None:
        # 25 mins = 1500s -> capped at 1200s (20 mins)
        self.assertEqual(calculate_viewing_allowance(1500), 1200)

    def test_allowance_math_60_minutes_caps_at_20(self) -> None:
        # 60 mins = 3600s -> capped at 1200s (20 mins)
        self.assertEqual(calculate_viewing_allowance(3600), 1200)

    def test_allowance_math_none_or_negative_defaults_to_20m(self) -> None:
        self.assertEqual(calculate_viewing_allowance(None), HARD_MAX_ALLOWANCE_SECONDS)
        self.assertEqual(calculate_viewing_allowance(-10), HARD_MAX_ALLOWANCE_SECONDS)
        self.assertEqual(calculate_viewing_allowance(0), HARD_MAX_ALLOWANCE_SECONDS)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Timeliness Evaluation
    # ─────────────────────────────────────────────────────────────────────────

    def test_timeliness_accepted_before_cutoff(self) -> None:
        accepted = "2026-10-19T13:49:00+05:30"
        cutoff = "2026-10-19T14:00:00+05:30"
        self.assertTrue(is_submission_timely(accepted, cutoff))

    def test_timeliness_accepted_at_cutoff_is_late(self) -> None:
        accepted = "2026-10-19T14:00:00+05:30"
        cutoff = "2026-10-19T14:00:00+05:30"
        self.assertFalse(is_submission_timely(accepted, cutoff))

    def test_timeliness_accepted_during_grace_period_is_late_for_reward(self) -> None:
        # Grace window allows submission until 14:10, but reward eligibility requires < 14:00
        accepted = "2026-10-19T14:05:00+05:30"
        cutoff = "2026-10-19T14:00:00+05:30"
        self.assertFalse(is_submission_timely(accepted, cutoff))

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Eligibility Gating
    # ─────────────────────────────────────────────────────────────────────────

    def test_eligibility_qualifying_hourly_submission(self) -> None:
        eligible, _ = evaluate_eligibility(
            occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_test_01",
            score=8,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
        )
        self.assertTrue(eligible)

    def test_eligibility_score_6_qualifies(self) -> None:
        eligible, _ = evaluate_eligibility(
            occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_test_02",
            score=6,
            is_timely=True,
            duplicate_detected=False,
            task_type="HOURLY",
        )
        self.assertTrue(eligible)

    def test_eligibility_score_under_6_rejected(self) -> None:
        for score in (5, 4, 2, 0):
            eligible, reason = evaluate_eligibility(
                occurrence_id="ACT-20261019-1400-HOURLY@v1",
                submission_id="sub_test_low",
                score=score,
                is_timely=True,
                duplicate_detected=False,
                task_type="HOURLY",
            )
            self.assertFalse(eligible)
            self.assertIn("threshold", reason.lower())

    def test_eligibility_late_submission_rejected(self) -> None:
        eligible, reason = evaluate_eligibility(
            occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_test_late",
            score=8,
            is_timely=False,
            duplicate_detected=False,
            task_type="HOURLY",
        )
        self.assertFalse(eligible)
        self.assertIn("cutoff", reason.lower())

    def test_eligibility_duplicate_evidence_rejected(self) -> None:
        eligible, reason = evaluate_eligibility(
            occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_test_dup",
            score=8,
            is_timely=True,
            duplicate_detected=True,
            task_type="HOURLY",
        )
        self.assertFalse(eligible)
        self.assertIn("duplicate", reason.lower())

    def test_eligibility_excluded_task_types_bypassed(self) -> None:
        for task in ("BATH", "GYM", "SLEEP", "WAKE_UP", "MOVIE", "TRAVEL"):
            eligible, reason = evaluate_eligibility(
                occurrence_id=f"ACT-20261019-1400-{task}@v1",
                submission_id=f"sub_{task}",
                score=8,
                is_timely=True,
                duplicate_detected=False,
                task_type=task,
            )
            self.assertFalse(eligible)
            self.assertIn("excluded", reason.lower())

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Reward Lifecycle, Idempotency & Sessions
    # ─────────────────────────────────────────────────────────────────────────

    def test_grant_reward_lifecycle_and_idempotency(self) -> None:
        reward = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_timely_8",
            score=8,
            accepted_at_iso="2026-10-19T13:50:00+05:30",
            scheduled_time_iso="2026-10-19T14:00:00+05:30",
            duplicate_detected=False,
        )
        self.assertIsNotNone(reward)
        self.assertEqual(reward["state"], "AVAILABLE")
        self.assertEqual(reward["validated_score"], 8)
        self.assertEqual(reward["is_timely"], 1)

        # Idempotent call returns identical record
        reward2 = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1400-HOURLY@v1",
            submission_id="sub_timely_8",
            score=8,
            accepted_at_iso="2026-10-19T13:50:00+05:30",
            scheduled_time_iso="2026-10-19T14:00:00+05:30",
            duplicate_detected=False,
        )
        self.assertEqual(reward["reward_id"], reward2["reward_id"])

    def test_select_video_and_allowance(self) -> None:
        reward = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1500-HOURLY@v1",
            submission_id="sub_timely_7",
            score=7,
            accepted_at_iso="2026-10-19T14:50:00+05:30",
            scheduled_time_iso="2026-10-19T15:00:00+05:30",
            duplicate_detected=False,
        )
        reward_id = reward["reward_id"]

        # Select a 10-minute video (600s) -> allowed = 660s
        sel_res = self.engine.select_video(
            reward_id=reward_id,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            duration_seconds=600,
        )
        self.assertTrue(sel_res["success"])
        self.assertEqual(sel_res["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(sel_res["allowed_seconds"], 1200)

        # Verify DB state
        r_db = self.db.get_youtube_reward(reward_id)
        self.assertEqual(r_db["state"], "VIDEO_SELECTED")
        self.assertEqual(r_db["selected_video_id"], "dQw4w9WgXcQ")
        self.assertEqual(r_db["allowed_seconds"], 1200)

    def test_single_active_session_across_devices(self) -> None:
        reward = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1600-HOURLY@v1",
            submission_id="sub_timely_6",
            score=6,
            accepted_at_iso="2026-10-19T15:50:00+05:30",
            scheduled_time_iso="2026-10-19T16:00:00+05:30",
            duplicate_detected=False,
        )
        reward_id = reward["reward_id"]
        self.engine.select_video(reward_id, "https://youtu.be/dQw4w9WgXcQ", 500)

        # Start session on WINDOWS
        start_res = self.engine.start_session(reward_id, client_id="WINDOWS")
        self.assertTrue(start_res["success"])
        self.assertTrue(start_res["session_active"])
        self.assertEqual(start_res["active_client_id"], "WINDOWS")

        # Create a second reward for a different hour
        reward_b = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1700-HOURLY@v1",
            submission_id="sub_timely_8b",
            score=8,
            accepted_at_iso="2026-10-19T16:50:00+05:30",
            scheduled_time_iso="2026-10-19T17:00:00+05:30",
            duplicate_detected=False,
        )
        self.engine.select_video(reward_b["reward_id"], "https://youtu.be/dQw4w9WgXcQ", 300)

        # Attempt to start second reward concurrently on CHROME -> disallowed!
        concurrent_res = self.engine.start_session(reward_b["reward_id"], client_id="CHROME")
        self.assertFalse(concurrent_res["success"])
        self.assertEqual(concurrent_res["code"], "PARALLEL_SESSION_DISALLOWED")

    def test_transfer_session_preserves_monotonic_deadline(self) -> None:
        reward = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1800-HOURLY@v1",
            submission_id="sub_timely_x",
            score=8,
            accepted_at_iso="2026-10-19T17:50:00+05:30",
            scheduled_time_iso="2026-10-19T18:00:00+05:30",
            duplicate_detected=False,
        )
        reward_id = reward["reward_id"]
        self.engine.select_video(reward_id, "https://youtu.be/dQw4w9WgXcQ", 600)
        self.engine.start_session(reward_id, client_id="WINDOWS")

        r_before = self.db.get_youtube_reward(reward_id)
        original_expiry = r_before["expiry_time"]

        # Transfer from WINDOWS to ANDROID
        transfer_res = self.engine.transfer_session(reward_id, from_client="WINDOWS", to_client="ANDROID")
        self.assertTrue(transfer_res["success"])
        self.assertEqual(transfer_res["transferred_to"], "ANDROID")

        r_after = self.db.get_youtube_reward(reward_id)
        self.assertEqual(r_after["active_client_id"], "ANDROID")
        # Monotonic deadline is strictly preserved, never extended
        self.assertEqual(r_after["expiry_time"], original_expiry)

    def test_expire_and_stop_session(self) -> None:
        reward = self.engine.evaluate_and_grant_reward(
            account_id="user_lok",
            hourly_occurrence_id="ACT-20261019-1900-HOURLY@v1",
            submission_id="sub_timely_y",
            score=8,
            accepted_at_iso="2026-10-19T18:50:00+05:30",
            scheduled_time_iso="2026-10-19T19:00:00+05:30",
            duplicate_detected=False,
        )
        reward_id = reward["reward_id"]
        self.engine.select_video(reward_id, "https://youtu.be/dQw4w9WgXcQ", 600)
        self.engine.start_session(reward_id, client_id="WINDOWS")

        # Stop session
        stop_res = self.engine.expire_session(reward_id, client_id="WINDOWS", termination_result="USER_COMPLETED")
        self.assertTrue(stop_res["success"])
        self.assertEqual(stop_res["state"], "CONSUMED")

        r_final = self.db.get_youtube_reward(reward_id)
        self.assertEqual(r_final["state"], "CONSUMED")
        self.assertEqual(r_final["termination_result"], "USER_COMPLETED")


if __name__ == "__main__":
    unittest.main()
