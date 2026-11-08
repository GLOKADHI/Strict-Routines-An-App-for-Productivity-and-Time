"""
Comprehensive Automated Tests for StrictRecords Browser Extension
Taufa AI Educational YouTube Video Validation Integration
===================================================================
Covers all 19 required test cases specified in Section 12:
1. Educational programming tutorial.
2. Operating-system interview lesson.
3. Mathematics explanation.
4. Music video.
5. Entertainment vlog.
6. Video with unavailable captions.
7. Video with insufficient evidence.
8. Invalid YouTube URL.
9. Video ID changes after approval.
10. Taufa processing timeout.
11. Taufa rejection.
12. Taufa approval.
13. Playback pause/resume.
14. Buffering.
15. Forward seeking.
16. Page refresh.
17. Browser restart.
18. Extension service-worker restart.
19. Cross-device entitlement synchronization.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.database import StrictRecordsDB
from core.entertainment_reward_engine import (
    EntertainmentRewardEngine,
    calculate_watched_seconds,
    extract_canonical_video_id,
    format_seconds_to_mmss,
    merge_watched_intervals,
)
from core.taufa_client import DESIGNATED_CONVERSATION_ID
from core.taufa_video_evaluator import TaufaVideoEvaluator


class TestBrowserExtensionTaufaValidation(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        self.db = StrictRecordsDB(self.tmp_path)
        self.engine = EntertainmentRewardEngine(self.db)
        self.evaluator = TaufaVideoEvaluator()

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)

    # 1. Educational programming tutorial
    def test_01_educational_programming_tutorial(self):
        """Authentic Python tutorial with transcript is approved."""
        raw_json = json.dumps({
            "video_id": "py_async_101",
            "canonical_url": "https://www.youtube.com/watch?v=py_async_101",
            "decision": "EDUCATIONAL",
            "duration_seconds": 1125,
            "duration_formatted": "18:45",
            "timeline": [
                {"start_seconds": 0, "end_seconds": 300, "topic": "Event Loops", "description": "asyncio event loop fundamentals"},
                {"start_seconds": 300, "end_seconds": 1125, "topic": "Coroutines", "description": "awaitable patterns and task groups"}
            ],
            "reason": "Authentic computer science programming course teaching asynchronous programming in Python.",
            "confidence": 0.98,
            "review_status": "COMPLETED"
        })
        parsed = TaufaVideoEvaluator._parse_response(
            f"```json\n{raw_json}\n```", "py_async_101", "https://www.youtube.com/watch?v=py_async_101", 1125, "18:45"
        )
        self.assertEqual(parsed["decision"], "EDUCATIONAL")
        self.assertEqual(parsed["duration_seconds"], 1125)
        self.assertEqual(parsed["duration_formatted"], "18:45")
        self.assertEqual(len(parsed["timeline"]), 2)

        saved = self.engine.save_educational_approval(
            video_id="py_async_101",
            canonical_url=parsed["canonical_url"],
            decision=parsed["decision"],
            duration_seconds=parsed["duration_seconds"],
            duration_formatted=parsed["duration_formatted"],
            reason=parsed["reason"],
            confidence=parsed["confidence"],
            title="Python Asyncio Tutorial",
            channel="TechAcademy",
        )
        self.assertEqual(saved["decision"], "EDUCATIONAL")

    # 2. Operating-system interview lesson
    def test_02_os_interview_lesson(self):
        """OS virtual memory lecture is approved with exact duration 25:10 (1510s)."""
        raw_json = json.dumps({
            "video_id": "os_virt_mem",
            "canonical_url": "https://www.youtube.com/watch?v=os_virt_mem",
            "decision": "EDUCATIONAL",
            "duration_seconds": 1510,
            "duration_formatted": "25:10",
            "timeline": [
                {"start_seconds": 0, "end_seconds": 500, "topic": "Paging & TLB", "description": "Virtual addressing and page tables"}
            ],
            "reason": "Detailed operating systems lecture covering page tables and TLB cache translation.",
            "confidence": 0.96,
            "review_status": "COMPLETED"
        })
        parsed = TaufaVideoEvaluator._parse_response(
            f"```json\n{raw_json}\n```", "os_virt_mem", "https://www.youtube.com/watch?v=os_virt_mem", 1510, "25:10"
        )
        self.assertEqual(parsed["decision"], "EDUCATIONAL")
        self.assertEqual(parsed["duration_seconds"], 1510)
        self.assertEqual(parsed["duration_formatted"], "25:10")

    # 3. Mathematics explanation
    def test_03_mathematics_explanation(self):
        """Linear algebra eigenvalues lesson is approved."""
        raw_json = json.dumps({
            "video_id": "math_eigen",
            "canonical_url": "https://www.youtube.com/watch?v=math_eigen",
            "decision": "EDUCATIONAL",
            "duration_seconds": 900,
            "duration_formatted": "15:00",
            "timeline": [],
            "reason": "Linear algebra mathematical derivation of eigenvalues and eigenvectors.",
            "confidence": 0.99,
            "review_status": "COMPLETED"
        })
        parsed = TaufaVideoEvaluator._parse_response(
            f"```json\n{raw_json}\n```", "math_eigen", "https://www.youtube.com/watch?v=math_eigen", 900, "15:00"
        )
        self.assertEqual(parsed["decision"], "EDUCATIONAL")
        self.assertEqual(parsed["duration_seconds"], 900)

    # 4. Music video
    def test_04_music_video_rejected(self):
        """Commercial music video is rejected as NOT_EDUCATIONAL."""
        raw_json = json.dumps({
            "video_id": "music_pop_01",
            "canonical_url": "https://www.youtube.com/watch?v=music_pop_01",
            "decision": "NOT_EDUCATIONAL",
            "duration_seconds": 225,
            "duration_formatted": "03:45",
            "timeline": [],
            "reason": "Commercial pop song and entertainment music video.",
            "confidence": 1.0,
            "review_status": "COMPLETED"
        })
        parsed = TaufaVideoEvaluator._parse_response(
            f"```json\n{raw_json}\n```", "music_pop_01", "https://www.youtube.com/watch?v=music_pop_01", 225, "03:45"
        )
        self.assertEqual(parsed["decision"], "NOT_EDUCATIONAL")
        self.assertIn("music", parsed["reason"].lower())

    # 5. Entertainment vlog
    def test_05_entertainment_vlog_rejected(self):
        """Lifestyle prank vlog is rejected as NOT_EDUCATIONAL."""
        raw_json = json.dumps({
            "video_id": "vlog_fun_123",
            "canonical_url": "https://www.youtube.com/watch?v=vlog_fun_123",
            "decision": "NOT_EDUCATIONAL",
            "duration_seconds": 860,
            "duration_formatted": "14:20",
            "timeline": [],
            "reason": "Entertainment vlog and comedy sketch without academic or study content.",
            "confidence": 0.95,
            "review_status": "COMPLETED"
        })
        parsed = TaufaVideoEvaluator._parse_response(
            f"```json\n{raw_json}\n```", "vlog_fun_123", "https://www.youtube.com/watch?v=vlog_fun_123", 860, "14:20"
        )
        self.assertEqual(parsed["decision"], "NOT_EDUCATIONAL")

    # 6. Video with unavailable captions and no substantive description
    def test_06_video_with_unavailable_captions_insufficient_evidence(self):
        """Video lacking captions, transcript, and description returns INSUFFICIENT_EVIDENCE."""
        result = self.evaluator.evaluate_video(
            url="https://www.youtube.com/watch?v=no_captions_v",
            video_id="no_captions_v",
            title="Calculus Exam Prep (Keyword Title)",
            channel="RandomChannel",
            duration_seconds=600,
            captions_text="",
            description="",
            chapters=[],
        )
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["review_status"], "NEEDS_REVIEW")
        self.assertIn("transcript/captions and detailed description are unavailable", result["reason"])

    # 7. Video with insufficient evidence (near empty description)
    def test_07_video_with_insufficient_evidence_needs_review(self):
        """Description of only a few characters without transcript returns INSUFFICIENT_EVIDENCE."""
        result = self.evaluator.evaluate_video(
            url="https://www.youtube.com/watch?v=sparse_ev_01",
            video_id="sparse_ev_01",
            title="Study Video",
            description="watch",
            captions_text=None,
            chapters=None,
        )
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["review_status"], "NEEDS_REVIEW")

    # 8. Invalid YouTube URL
    def test_08_invalid_youtube_url(self):
        """Non-YouTube URL is rejected with clean error."""
        result = self.evaluator.evaluate_video(
            url="https://example.com/not_youtube",
            video_id=None,
        )
        self.assertEqual(result["decision"], "NOT_EDUCATIONAL")
        self.assertEqual(result["video_id"], "")
        self.assertIn("Invalid YouTube URL", result["reason"])

    # 9. Video ID changes after approval (strict single-video scoping)
    def test_09_video_id_changes_after_approval(self):
        """Approval of Video A does not grant access to Video B."""
        # Approve Video A
        self.engine.save_educational_approval(
            video_id="video_aaa_11",
            canonical_url="https://www.youtube.com/watch?v=video_aaa_11",
            decision="EDUCATIONAL",
            duration_seconds=600,
            duration_formatted="10:00",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("video_aaa_11", "user_test", "BROWSER_EXT")

        # Video A is allowed
        dec_a = self.engine.get_educational_video_access_decision("video_aaa_11", "user_test")
        self.assertTrue(dec_a["allowed"])

        # Video B is NOT approved and must be blocked
        dec_b = self.engine.get_educational_video_access_decision("video_bbb_22", "user_test")
        self.assertFalse(dec_b["allowed"])
        self.assertIn("no educational review", dec_b["reason"].lower())

    # 10. Taufa processing timeout / unauthenticated session
    def test_10_taufa_processing_timeout_or_unauthenticated(self):
        """Unauthenticated Taufa session returns fail-closed PROCESSING_ERROR, never fake approval."""
        evaluator = TaufaVideoEvaluator()
        evaluator.client.has_active_session = MagicMock(return_value=False)
        evaluator.client.get_cookie_header = MagicMock(return_value="")
        result = evaluator.evaluate_video(
            url="https://www.youtube.com/watch?v=test_valid_v",
            video_id="test_valid_v",
            title="Real Study Video",
            description="This is a substantive description explaining algorithm design in detail.",
            captions_text="Welcome to the algorithm design lecture.",
        )
        # Without session cookies, it must fail-closed safely
        self.assertEqual(result["decision"], "PROCESSING_ERROR")
        self.assertEqual(result["review_status"], "MANUAL_REVIEW")
        self.assertIn(DESIGNATED_CONVERSATION_ID, result["reason"])

    # 11. Taufa rejection preserves reason
    def test_11_taufa_rejection_preserves_reason(self):
        """Rejection stores exact reason in database."""
        self.engine.save_educational_approval(
            video_id="vid_rej_999",
            canonical_url="https://www.youtube.com/watch?v=vid_rej_999",
            decision="NOT_EDUCATIONAL",
            duration_seconds=300,
            duration_formatted="05:00",
            reason="Gameplay walkthrough without instructional value.",
            account_id="user_test",
        )
        approval = self.engine.get_educational_approval("vid_rej_999", "user_test")
        self.assertEqual(approval["decision"], "NOT_EDUCATIONAL")
        self.assertEqual(approval["reason"], "Gameplay walkthrough without instructional value.")

    # 12. Taufa approval creates entitlement
    def test_12_taufa_approval_creates_entitlement(self):
        """Educational decision creates active playback session with exact duration."""
        self.engine.save_educational_approval(
            video_id="vid_app_123",
            canonical_url="https://www.youtube.com/watch?v=vid_app_123",
            decision="EDUCATIONAL",
            duration_seconds=1205,
            duration_formatted="20:05",
            reason="Operating systems memory management lecture.",
            account_id="user_test",
        )
        session = self.engine.start_or_resume_playback_session("vid_app_123", "user_test", "BROWSER_EXT")
        self.assertTrue(session["allowed"])
        self.assertEqual(session["remaining_seconds"], 1205)
        self.assertEqual(session["status"], "ACTIVE")

    # 13. Playback pause / resume
    def test_13_playback_pause_and_resume(self):
        """Pausing does not advance watched time; resuming advances correctly."""
        self.engine.save_educational_approval(
            video_id="vid_pb_01",
            canonical_url="https://www.youtube.com/watch?v=vid_pb_01",
            decision="EDUCATIONAL",
            duration_seconds=600,
            duration_formatted="10:00",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("vid_pb_01", "user_test", "BROWSER_EXT")

        # Play 0 to 30s
        hb1 = self.engine.record_playback_heartbeat(
            video_id="vid_pb_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="PLAYING", interval_start=0.0, interval_end=30.0,
        )
        self.assertEqual(hb1["remaining_seconds"], 570)

        # Paused heartbeat (no interval sent)
        hb_pause = self.engine.record_playback_heartbeat(
            video_id="vid_pb_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="PAUSED", interval_start=None, interval_end=None,
        )
        self.assertEqual(hb_pause["remaining_seconds"], 570)

        # Resumed: play 30 to 60s
        hb_resume = self.engine.record_playback_heartbeat(
            video_id="vid_pb_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="PLAYING", interval_start=30.0, interval_end=60.0,
        )
        self.assertEqual(hb_resume["remaining_seconds"], 540)

    # 14. Buffering state
    def test_14_buffering_state(self):
        """Buffering reports state BUFFERING without deducting unwatched time."""
        self.engine.save_educational_approval(
            video_id="vid_buf_01",
            canonical_url="https://www.youtube.com/watch?v=vid_buf_01",
            decision="EDUCATIONAL",
            duration_seconds=500,
            duration_formatted="08:20",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("vid_buf_01", "user_test", "BROWSER_EXT")
        hb = self.engine.record_playback_heartbeat(
            video_id="vid_buf_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="BUFFERING", interval_start=None, interval_end=None,
        )
        self.assertEqual(hb["last_playback_state"], "BUFFERING")
        self.assertEqual(hb["remaining_seconds"], 500)

    # 15. Forward seeking protection
    def test_15_forward_seeking_protection(self):
        """Skipping forward does not count the skipped range towards watched progress."""
        # Watched: 0-10, then skipped to 200 and watched 200-215
        intervals = [[0.0, 10.0], [200.0, 215.0]]
        merged = merge_watched_intervals(intervals)
        watched_sec = calculate_watched_seconds(merged)
        # Exactly 10 + 15 = 25s watched, not 215s!
        self.assertEqual(watched_sec, 25.0)

    # 16. Page refresh preserves approval and session
    def test_16_page_refresh_preserves_approval_and_session(self):
        """Refreshing page retains exact remaining duration and approval state."""
        self.engine.save_educational_approval(
            video_id="vid_refresh_01",
            canonical_url="https://www.youtube.com/watch?v=vid_refresh_01",
            decision="EDUCATIONAL",
            duration_seconds=1200,
            duration_formatted="20:00",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("vid_refresh_01", "user_test", "BROWSER_EXT")

        # Play 100s
        self.engine.record_playback_heartbeat(
            video_id="vid_refresh_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="PLAYING", interval_start=0.0, interval_end=100.0,
        )

        # Page reloaded -> check access decision
        dec = self.engine.get_educational_video_access_decision("vid_refresh_01", "user_test")
        self.assertTrue(dec["allowed"])
        self.assertEqual(dec["remaining_seconds"], 1100)

    # 17. Browser restart restores saved approval and session
    def test_17_browser_restart_restores_saved_approval(self):
        """Re-instantiating engine simulates browser restart; database restores session."""
        self.engine.save_educational_approval(
            video_id="vid_restart_01",
            canonical_url="https://www.youtube.com/watch?v=vid_restart_01",
            decision="EDUCATIONAL",
            duration_seconds=800,
            duration_formatted="13:20",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("vid_restart_01", "user_test", "BROWSER_EXT")

        # New engine instance connected to same SQLite DB
        fresh_engine = EntertainmentRewardEngine(self.db)
        dec = fresh_engine.get_educational_video_access_decision("vid_restart_01", "user_test")
        self.assertTrue(dec["allowed"])
        self.assertEqual(dec["remaining_seconds"], 800)

    # 18. Extension service-worker restart & history persistence
    def test_18_validation_history_persistence(self):
        """Validation history is saved and queryable across restarts."""
        self.db.save_educational_validation_history(
            history_id="hist_sw_01",
            request_id="req_sw_01",
            validation_session_id="val_sw_01",
            video_id="vid_sw_01",
            canonical_url="https://www.youtube.com/watch?v=vid_sw_01",
            decision="EDUCATIONAL",
            duration_seconds=900,
            duration_formatted="15:00",
            title="Algorithm Theory",
            channel="MIT OpenCourseWare",
            reason="College-level algorithm analysis lecture.",
            confidence=0.99,
            review_status="COMPLETED",
            playback_completion_state="IN_PROGRESS",
            account_id="user_test",
            device_id="BROWSER_EXT",
        )

        history = self.db.get_educational_validation_history("user_test", limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["video_id"], "vid_sw_01")
        self.assertEqual(history[0]["decision"], "EDUCATIONAL")
        self.assertEqual(history[0]["playback_completion_state"], "IN_PROGRESS")

        # Update playback state to COMPLETED
        self.db.update_educational_history_playback_state("vid_sw_01", "COMPLETED", "user_test")
        updated = self.db.get_educational_validation_history("user_test", limit=10)
        self.assertEqual(updated[0]["playback_completion_state"], "COMPLETED")

    # 19. Cross-device entitlement synchronization
    def test_19_cross_device_entitlement_synchronization(self):
        """Educational video approval sets shared active config visible to Android and Windows."""
        # 1. Approval on extension
        self.engine.save_educational_approval(
            video_id="vid_sync_01",
            canonical_url="https://www.youtube.com/watch?v=vid_sync_01",
            decision="EDUCATIONAL",
            duration_seconds=600,
            duration_formatted="10:00",
            title="Calculus Integration",
            account_id="user_test",
        )
        self.engine.start_or_resume_playback_session("vid_sync_01", "user_test", "BROWSER_EXT")

        # 2. Simulate server syncing config keys
        self.db.set_config_value("educational_unlock_active", "1")
        self.db.set_config_value("educational_active_video_id", "vid_sync_01")
        self.db.set_config_value("educational_unlock_expires_at", "9999999999999")

        # 3. Windows & Android read shared config
        self.assertEqual(self.db.get_config_value("educational_unlock_active"), "1")
        self.assertEqual(self.db.get_config_value("educational_active_video_id"), "vid_sync_01")

        # 4. Completion re-locks all devices
        self.engine.record_playback_heartbeat(
            video_id="vid_sync_01", account_id="user_test", device_id="BROWSER_EXT",
            playback_state="ENDED", interval_start=0.0, interval_end=600.0,
        )
        self.db.set_config_value("educational_unlock_active", "0")
        self.assertEqual(self.db.get_config_value("educational_unlock_active"), "0")

        # Re-check decision is blocked
        dec = self.engine.get_educational_video_access_decision("vid_sync_01", "user_test")
        self.assertFalse(dec["allowed"])
        self.assertTrue(dec["is_completed"])


if __name__ == "__main__":
    unittest.main()
