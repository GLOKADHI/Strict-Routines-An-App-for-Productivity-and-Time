"""
Verification test suite for StrictRecords video playback restriction policy:
1. Sun NXT exemption (sunnxt.com is always allowed, never blocked).
2. YouTube video playback restriction (requires 80 points, allows Taufa AI educational videos).
3. 3rd party social media videos (Facebook, Twitter/X, TikTok, Reddit, Twitch, Vimeo, Dailymotion, Threads).
4. Internal file videos (<= 5 minutes allowed, > 5 minutes blocked).
5. Audio streaming platforms (Spotify, Apple Music, etc.) pure <audio> not affected by video restriction.
6. Multi-browser distribution parity across all 7 supported browsers.
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
from core.restriction_targets import TARGETS, threshold_for, target_for_url
from windows.restriction_manager import YouTubeRestrictionManager


class TestVideoPlaybackRestrictionPolicy(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)
        self.mgr = YouTubeRestrictionManager(db=self.db)

    def tearDown(self):
        try:
            self.mgr.clear_caches()
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def _set_score_and_wakeup(self, score: int, wakeup_status: str = "ON_TIME"):
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

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Sun NXT Exemption Tests
    # ─────────────────────────────────────────────────────────────────────────
    def test_01_sunnxt_exemption_in_restriction_manager(self):
        """Sun NXT is always allowed, even when user score is 0 and wakeup incomplete."""
        self._set_score_and_wakeup(0, "MISSED")
        res1 = self.mgr.get_browser_tab_restriction("sunnxt", actual_url="https://sunnxt.com/movie/watch")
        self.assertTrue(res1.get("allowed"))
        self.assertFalse(res1.get("restricted"))

        res2 = self.mgr.get_browser_tab_restriction("anything", actual_url="https://www.sunnxt.com/livetv")
        self.assertTrue(res2.get("allowed"))
        self.assertFalse(res2.get("restricted"))

    def test_02_sunnxt_bypass_in_scripts(self):
        """Verify that all extension scripts explicitly check and bypass Sun NXT."""
        ext_dir = Path(ROOT_DIR) / "browser_extension"

        # 1. yt_main_world.js
        yt_main = (ext_dir / "yt_main_world.js").read_text(encoding="utf-8")
        self.assertIn("function isSunNxt", yt_main)
        self.assertIn("sunnxt.com", yt_main)

        # 2. global_video_engine.js
        gve = (ext_dir / "global_video_engine.js").read_text(encoding="utf-8")
        self.assertIn("isSunNxtHost", gve)
        self.assertIn("sunnxt.com", gve)

        # 3. content.js
        content = (ext_dir / "content.js").read_text(encoding="utf-8")
        self.assertIn("isSunNxtHost", content)
        self.assertIn("sunnxt.com", content)

        # 4. service_worker.js
        sw = (ext_dir / "service_worker.js").read_text(encoding="utf-8")
        self.assertIn("sunnxt.com", sw)
        self.assertIn("isSunNxt", sw)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. YouTube Restriction & Educational Flow Tests
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_youtube_restricted_below_threshold(self):
        """YouTube requires 80 points with completed wake-up."""
        self._set_score_and_wakeup(50, "MISSED")
        res = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/watch?v=123")
        self.assertFalse(res.get("allowed"))
        self.assertTrue(res.get("restricted"))

    def test_04_youtube_allowed_with_80_points(self):
        """YouTube is unlocked when score is 80+ and wake-up is completed."""
        self._set_score_and_wakeup(80, "ON_TIME")
        res = self.mgr.get_browser_tab_restriction("youtube", actual_url="https://www.youtube.com/watch?v=123")
        self.assertTrue(res.get("allowed"))
        self.assertFalse(res.get("restricted"))

    # ─────────────────────────────────────────────────────────────────────────
    # 3. 3rd Party Social Media Video Tests
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_social_media_video_targets_catalog(self):
        """Verify all requested social platforms are registered in TARGETS with 80-point threshold."""
        expected_social = [
            ("facebook", "facebook.com"),
            ("facebook", "fb.watch"),
            ("facebook", "fb.com"),
            ("twitter", "twitter.com"),
            ("twitter", "x.com"),
            ("tiktok", "tiktok.com"),
            ("reddit", "reddit.com"),
            ("reddit", "v.redd.it"),
            ("twitch", "twitch.tv"),
            ("vimeo", "vimeo.com"),
            ("dailymotion", "dailymotion.com"),
            ("threads", "threads.net"),
        ]

        for service_key, domain in expected_social:
            url = f"https://{domain}/video/test"
            matched_key = target_for_url(url)
            self.assertEqual(
                matched_key,
                service_key,
                f"URL {url} must match target key '{service_key}', got '{matched_key}'"
            )
            spec = TARGETS.get(service_key, {})
            self.assertEqual(spec.get("thresholdSetting"), "reward_threshold")
            self.assertEqual(spec.get("defaultThreshold"), 80)

    def test_06_social_media_enforcement_boundary(self):
        """Social video platforms are blocked under 80 points, unlocked at 80 points."""
        self._set_score_and_wakeup(79, "ON_TIME")
        fb_blocked = self.mgr.get_browser_tab_restriction("facebook", actual_url="https://facebook.com/")
        self.assertTrue(fb_blocked.get("restricted"))

        tiktok_blocked = self.mgr.get_browser_tab_restriction("tiktok", actual_url="https://tiktok.com/")
        self.assertTrue(tiktok_blocked.get("restricted"))

        self._set_score_and_wakeup(80, "ON_TIME")
        fb_allowed = self.mgr.get_browser_tab_restriction("facebook", actual_url="https://facebook.com/")
        self.assertFalse(fb_allowed.get("restricted"))
        self.assertTrue(fb_allowed.get("allowed"))

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Internal File Video Tests (5-Minute Limit)
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_internal_video_five_minute_rule_in_scripts(self):
        """Verify global_video_engine.js and yt_main_world.js implement the 5-minute (300s) rule."""
        ext_dir = Path(ROOT_DIR) / "browser_extension"
        gve = (ext_dir / "global_video_engine.js").read_text(encoding="utf-8")
        yt_main = (ext_dir / "yt_main_world.js").read_text(encoding="utf-8")

        # Both must have isInternalFileMedia and isInternalVideoBlocked checks
        for name, script in [("global_video_engine.js", gve), ("yt_main_world.js", yt_main)]:
            self.assertIn("isInternalFileMedia", script, f"{name} missing isInternalFileMedia")
            self.assertIn("isInternalVideoBlocked", script, f"{name} missing isInternalVideoBlocked")
            self.assertIn("300", script, f"{name} missing 300 second (5 min) limit")

        # Global video engine must display dedicated 5-minute limit overlay badge
        self.assertIn("FILE LIMIT (5 MIN)", gve)
        self.assertIn("Internal Video Exceeds 5 Minutes", gve)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Audio vs Video Separation Tests
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_audio_playback_not_silenced_by_video_restriction(self):
        """Verify yt_main_world.js separates <audio> element checks from video-only playback restriction."""
        yt_main = (Path(ROOT_DIR) / "browser_extension" / "yt_main_world.js").read_text(encoding="utf-8")
        self.assertIn("isPlaybackRestrictedForMedia", yt_main)
        self.assertIn("AUDIO", yt_main)
        self.assertIn("strictrecords-restricted", yt_main)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Manifest & Multi-Browser Parity Tests
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_manifest_version_and_file_match(self):
        """Verify manifest.json is at version 1.8.5 and matches file:///*."""
        manifest_path = Path(ROOT_DIR) / "browser_extension" / "manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertIn(manifest.get("version"), ["1.8.5", "1.8.6"])

        # Verify file scheme matching in content_scripts
        gve_cs = next(cs for cs in manifest["content_scripts"] if "global_video_engine.js" in cs["js"])
        self.assertIn("file:///*", gve_cs["matches"])

        yt_cs = next(cs for cs in manifest["content_scripts"] if "yt_main_world.js" in cs["js"])
        self.assertIn("file:///*", yt_cs["matches"])

    def test_10_all_7_browsers_synchronized(self):
        """Verify that all 7 browser distribution directories have matching core files."""
        src_root = Path(ROOT_DIR) / "browser_extension"
        dist_root = Path(ROOT_DIR) / "StrictRoutines-Browser-Extensions"
        browsers = ["Arc", "Brave", "Chrome", "Comet", "Edge", "Opera", "Vivaldi"]

        check_files = [
            "global_video_engine.js",
            "yt_main_world.js",
            "content.js",
            "service_worker.js",
            "targets.json",
            "manifest.json"
        ]

        for fname in check_files:
            src_hash = hashlib.sha256((src_root / fname).read_bytes()).hexdigest()
            for b in browsers:
                b_file = dist_root / b / "StrictRoutines" / fname
                self.assertTrue(b_file.is_file(), f"{b} missing {fname}")
                b_hash = hashlib.sha256(b_file.read_bytes()).hexdigest()
                self.assertEqual(
                    src_hash,
                    b_hash,
                    f"File {fname} in browser {b} differs from browser_extension source"
                )

        # Ensure setup-config.js exists in each browser
        for b in browsers:
            setup_cfg = dist_root / b / "StrictRoutines" / "setup-config.js"
            self.assertTrue(setup_cfg.is_file(), f"{b} missing setup-config.js")


if __name__ == "__main__":
    unittest.main()
