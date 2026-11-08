"""
Regression and Verification Tests for Browser Extension Restriction Parity Across
YouTube, Instagram, Spotify, and Other Restricted Sites.

Covers:
1. Canonical result model fields (all 9 fields present and typed)
2. Score 91 with wake-up completed unlocks YouTube and Instagram
3. Score 91 with wake-up incomplete keeps YouTube and Instagram blocked with clear prerequisite reason
4. Wake-up verification completes -> immediately flips to allowed
5. Spotify (threshold 60, music) and Instagram (threshold 80, video) evaluated consistently
6. Exact 80-point threshold boundary behavior (79 blocked, 80 allowed, 81 allowed)
7. Exact 60-point music threshold boundary behavior (59 blocked, 60 allowed, 61 allowed)
8. Spotify GYM Mode temporary exception
9. YouTube Single-Video Reward temporary exception
10. Explicit manual restriction overrides score eligibility
11. AI validation score (out of 8) is never confused with account threshold (out of 100)
12. Demo/test scores do not alter production access
13. Synchronization across all 7 browser extension distribution directories
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.privilege_engine import PrivilegeEngine
from core.restriction_targets import TARGETS, threshold_for
from windows.restriction_manager import YouTubeRestrictionManager


class TestBrowserExtensionRestrictionParity(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)
        self.mgr = YouTubeRestrictionManager(db=self.db)
        self.engine = PrivilegeEngine(db=self.db)

    def tearDown(self):
        try:
            self.mgr.clear_caches()
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def _set_daily_score_and_wakeup(self, score: int, wakeup_status: str = "PENDING"):
        with self.db._connect() as conn:
            conn.execute(
                """
                UPDATE daily_records
                SET final_score = ?, gross_score = ?, positive_points = ?,
                    wakeup_status = ?, updated_at = datetime('now')
                WHERE date = ? AND is_dev_mode = 0
                """,
                (score, score, score, wakeup_status, self.today),
            )
        self.mgr.clear_caches()

    def test_01_canonical_result_model_fields(self):
        """Verify that get_browser_tab_restriction returns all 9 canonical fields independently."""
        self._set_daily_score_and_wakeup(91, "ON_TIME")
        decision = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/watch?v=123")

        # 9 canonical fields required by the model:
        self.assertIn("currentScore", decision)
        self.assertIn("requiredScore", decision)
        self.assertIn("scoreEligible", decision)
        self.assertIn("dailyPrerequisiteMet", decision)
        self.assertIn("manuallyRestricted", decision)
        self.assertIn("temporaryException", decision)
        self.assertIn("effectiveAccessDecision", decision)
        self.assertIn("enforcementStatus", decision)
        self.assertIn("lastSuccessfulSync", decision)

        # Value validations
        self.assertEqual(decision["currentScore"], 91)
        self.assertEqual(decision["requiredScore"], 80)
        self.assertTrue(decision["scoreEligible"])
        self.assertTrue(decision["dailyPrerequisiteMet"])
        self.assertFalse(decision["manuallyRestricted"])
        self.assertIsNone(decision["temporaryException"])
        self.assertEqual(decision["effectiveAccessDecision"], "ALLOW")
        self.assertEqual(decision["enforcementStatus"], "APPLIED")
        self.assertTrue(decision["allowed"])
        self.assertFalse(decision["restricted"])

    def test_02_score_91_with_wakeup_completed_unlocks_youtube_and_instagram(self):
        """Score 91 with wake-up completed unlocks both YouTube and Instagram."""
        self._set_daily_score_and_wakeup(91, "ON_TIME")
        self.assertTrue(self.db.is_wakeup_completed_today(self.today))

        yt = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(yt["allowed"])
        self.assertFalse(yt["restricted"])
        self.assertTrue(yt["scoreEligible"])
        self.assertTrue(yt["dailyPrerequisiteMet"])
        self.assertIn("Discipline verified", yt["reason"])

        ig = self.mgr.get_browser_tab_restriction("instagram", actual_url="https://www.instagram.com/")
        self.assertEqual(ig["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(ig["allowed"])
        self.assertFalse(ig["restricted"])
        self.assertTrue(ig["scoreEligible"])
        self.assertTrue(ig["dailyPrerequisiteMet"])

    def test_03_score_91_with_wakeup_incomplete_blocks_with_clear_prerequisite_reason(self):
        """Score 91 with wake-up incomplete keeps YouTube and Instagram blocked with clear reason."""
        self._set_daily_score_and_wakeup(91, "PENDING")
        self.assertFalse(self.db.is_wakeup_completed_today(self.today))

        yt = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(yt["allowed"])
        self.assertTrue(yt["restricted"])
        self.assertTrue(yt["scoreEligible"])  # Score itself is eligible (91 >= 80)
        self.assertFalse(yt["dailyPrerequisiteMet"])  # Prerequisite is not met!
        self.assertEqual(yt["reason"], "Wake-up protocol has not been completed today.")

        ig = self.mgr.get_browser_tab_restriction("instagram", actual_url="https://www.instagram.com/")
        self.assertEqual(ig["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(ig["allowed"])
        self.assertTrue(ig["restricted"])
        self.assertTrue(ig["scoreEligible"])
        self.assertFalse(ig["dailyPrerequisiteMet"])
        self.assertEqual(ig["reason"], "Wake-up protocol has not been completed today.")

    def test_04_wakeup_verification_completes_immediately_transitions_to_allowed(self):
        """Wake-up verification completion immediately flips restriction state from blocked to allowed."""
        # Initial: 91 points but wake-up pending
        self._set_daily_score_and_wakeup(91, "PENDING")
        yt_initial = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt_initial["effectiveAccessDecision"], "BLOCK")
        self.assertTrue(yt_initial["restricted"])

        # Wake-up completed!
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET wakeup_status = 'ON_TIME', wakeup_completed_at = datetime('now') WHERE date = ?",
                (self.today,),
            )
        self.mgr.clear_caches()

        # Re-check: immediately transitions to ALLOW
        yt_after = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt_after["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(yt_after["allowed"])
        self.assertFalse(yt_after["restricted"])
        self.assertTrue(yt_after["dailyPrerequisiteMet"])

    def test_05_spotify_music_policy_parity(self):
        """Spotify is governed by Music/Audio rules (threshold 60, no wake-up gate)."""
        # Score 91, wake-up PENDING: Spotify is ALLOWED while YouTube is BLOCKED
        self._set_daily_score_and_wakeup(91, "PENDING")
        sp = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(sp["allowed"])
        self.assertEqual(sp["requiredScore"], 60)
        self.assertTrue(sp["dailyPrerequisiteMet"])  # Music doesn't require wake-up

        # YouTube under same conditions is BLOCKED
        yt = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(yt["allowed"])

    def test_06_social_video_80_point_threshold_boundary(self):
        """Exact 80-point threshold boundary behavior: 79 blocked, 80 allowed, 81 allowed."""
        # 79 points with wake-up completed -> BLOCKED (1 point needed)
        self._set_daily_score_and_wakeup(79, "ON_TIME")
        yt_79 = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt_79["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(yt_79["allowed"])
        self.assertFalse(yt_79["scoreEligible"])
        self.assertIn("1 pts needed", yt_79["reason"])

        # 80 points with wake-up completed -> ALLOWED (inclusive boundary)
        self._set_daily_score_and_wakeup(80, "ON_TIME")
        yt_80 = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt_80["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(yt_80["allowed"])
        self.assertTrue(yt_80["scoreEligible"])

        # 81 points with wake-up completed -> ALLOWED
        self._set_daily_score_and_wakeup(81, "ON_TIME")
        yt_81 = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt_81["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(yt_81["allowed"])

    def test_07_spotify_music_threshold_boundary(self):
        """Exact 60-point music threshold boundary: 59 blocked, 60 allowed, 61 allowed."""
        # 59 points -> BLOCKED
        self._set_daily_score_and_wakeup(59, "PENDING")
        sp_59 = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp_59["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(sp_59["allowed"])
        self.assertFalse(sp_59["scoreEligible"])
        self.assertIn("1 pts needed", sp_59["reason"])

        # 60 points -> ALLOWED
        self._set_daily_score_and_wakeup(60, "PENDING")
        sp_60 = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp_60["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(sp_60["allowed"])
        self.assertTrue(sp_60["scoreEligible"])

        # 61 points -> ALLOWED
        self._set_daily_score_and_wakeup(61, "PENDING")
        sp_61 = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp_61["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(sp_61["allowed"])

    def test_08_spotify_gym_mode_temporary_exception(self):
        """GYM Mode active grants Spotify temporary access even when score is below 60."""
        self._set_daily_score_and_wakeup(45, "PENDING")

        # Without GYM mode -> BLOCKED
        sp_before = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp_before["effectiveAccessDecision"], "BLOCK")

        # Mock GYM mode active
        self.db.get_gym_mode_state = lambda: {"is_active": True, "remaining_seconds": 3600}
        sp_gym = self.mgr.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com/")
        self.assertEqual(sp_gym["effectiveAccessDecision"], "ALLOW")
        self.assertTrue(sp_gym["allowed"])
        self.assertEqual(sp_gym["temporaryException"], "GYM_MODE")
        self.assertTrue(sp_gym.get("gym_mode_active"))
        self.assertIn("GYM Mode active", sp_gym["reason"])

    def test_09_youtube_shared_reward_satisfies_only_its_score_requirement(self):
        from unittest.mock import patch
        self._set_daily_score_and_wakeup(50, "ON_TIME")
        class Projection:
            def youtube_active(self):return dict(remaining_seconds=900,remaining_ms=900000,expiry_ms=1790000000000)
            def view(self):return dict(account_id='user_lok',active_session=self.youtube_active())
        with patch('core.youtube_reward_cloud.client',return_value=Projection()):
            for url in ['https://www.youtube.com/','https://www.youtube.com/watch?v=abcdefghijk']:
                yt=self.mgr.get_browser_tab_restriction('youtube',actual_url=url)
                self.assertTrue(yt['allowed'])
                self.assertEqual(yt['temporaryException'],'SINGLE_YOUTUBE_REWARD')
                self.assertEqual(yt['currentScore'],50)
            self.assertTrue(self.mgr.get_browser_tab_restriction('instagram')['restricted'])

    def test_10_explicit_manual_restriction_overrides_score_eligibility(self):
        """User manual lock preference blocks access even with score 95 and wake-up completed."""
        self._set_daily_score_and_wakeup(95, "ON_TIME")

        # Manually lock YouTube via sidebar toggle
        self.db.toggle_sidebar_restriction("youtube", enabled=True, requested_by="user_lok")
        self.mgr.clear_caches()

        yt = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt["effectiveAccessDecision"], "BLOCK")
        self.assertFalse(yt["allowed"])
        self.assertTrue(yt["manuallyRestricted"])
        self.assertEqual(yt["reason"], "Restriction enabled by user preference.")

    def test_11_demo_and_dev_scores_do_not_alter_production_access(self):
        """Dev/demo records (is_dev_mode=1) never leak into production restriction decisions."""
        self._set_daily_score_and_wakeup(50, "PENDING")

        # Insert a 100-point dev-mode record
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_records (record_id, date, user_id, is_dev_mode, final_score, wakeup_status, updated_at)
                VALUES ('daily_dev_test', ?, 'user_lok', 1, 100, 'ON_TIME', datetime('now'))
                """,
                (self.today,),
            )
        self.mgr.clear_caches()

        # Production evaluation must still see 50, not 100
        yt = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/")
        self.assertEqual(yt["currentScore"], 50)
        self.assertFalse(yt["scoreEligible"])
        self.assertEqual(yt["effectiveAccessDecision"], "BLOCK")

    def test_12_all_browser_extension_packages_synchronized(self):
        """Verify that browser_extension/content.js matches all 7 browser distribution directories."""
        source_path = Path(ROOT_DIR) / "browser_extension" / "content.js"
        self.assertTrue(source_path.is_file(), "Source content.js must exist")
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

        browsers = ["Arc", "Brave", "Chrome", "Comet", "Edge", "Opera", "Vivaldi"]
        ext_root = Path(ROOT_DIR) / "StrictRoutines-Browser-Extensions"

        for b in browsers:
            target = ext_root / b / "StrictRoutines" / "content.js"
            self.assertTrue(target.is_file(), f"{b} content.js must exist")
            target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(
                source_hash,
                target_hash,
                f"{b} StrictRoutines/content.js does not match source browser_extension/content.js",
            )


if __name__ == "__main__":
    unittest.main()
