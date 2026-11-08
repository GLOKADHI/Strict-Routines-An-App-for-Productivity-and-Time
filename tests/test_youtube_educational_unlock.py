"""
STRICTRECORDS - YouTube Educational Unlock & Policy Verification Tests
Verifies:
1. Reason-based educational unlock request (PC & Android rules)
2. Blocked YouTube Shorts in Educational Mode
3. Blocked YouTube Homepage / Feed in Educational Mode
4. Search educational videos only (NO entertainment, music, gaming, comedy, serials)
5. Full API endpoint lifecycle (/api/restriction/request_unlock, cancel_unlock, status)
"""

import unittest
from windows.restriction_manager import (
    YouTubeRestrictionManager,
    validate_educational_unlock_request,
    is_youtube_homepage,
    is_youtube_shorts,
)


class TestYouTubeEducationalUnlock(unittest.TestCase):

    def setUp(self):
        self.mgr = YouTubeRestrictionManager()

    def test_01_valid_educational_reasons_approved(self):
        valid_reasons = [
            "Studying MIT 18.06 linear algebra lectures for university exam",
            "Need to watch Python data structures and algorithms tutorial",
            "Preparing for GATE computer science exam operating systems",
            "Watching Stanford CS229 machine learning lecture series",
            "Biology biochemistry crash course for medical test",
        ]
        for r in valid_reasons:
            ok, msg = validate_educational_unlock_request(r)
            self.assertTrue(ok, f"Expected '{r}' to be approved, got error: {msg}")

    def test_02_short_or_empty_reasons_rejected(self):
        invalid_reasons = ["", "   ", "hi", "study", "open it"]
        for r in invalid_reasons:
            ok, msg = validate_educational_unlock_request(r)
            self.assertFalse(ok, f"Expected '{r}' to be rejected")
            self.assertIn("at least 8 characters", msg)

    def test_03_entertainment_music_gaming_comedy_serials_rejected(self):
        rejected_reasons = [
            ("Want to listen to lofi music songs while studying", "music"),
            ("Watching GTA 5 gameplay video with friends", "gameplay"),
            ("Need to watch funny comedy jokes video", "comedy"),
            ("Catching up on Anupama daily serials episode", "episode"),
            ("Watching latest Marvel movie trailer", "movie"),
            ("Watch funny pranks video", "pranks"),
        ]
        for reason, expected_keyword in rejected_reasons:
            ok, msg = validate_educational_unlock_request(reason)
            self.assertFalse(ok, f"Expected '{reason}' to be rejected")
            self.assertIn("Requests for entertainment, music, gaming, or serials", msg)

    def test_04_homepage_detection(self):
        self.assertTrue(is_youtube_homepage("YouTube"))
        self.assertTrue(is_youtube_homepage("YouTube - Google Chrome"))
        self.assertTrue(is_youtube_homepage("YouTube - Brave"))
        self.assertTrue(is_youtube_homepage("YouTube \u2014 Mozilla Firefox"))
        self.assertTrue(is_youtube_homepage("YouTube - Microsoft\u200b Edge"))
        self.assertTrue(is_youtube_homepage("YouTube - Personal - Microsoft\u200b Edge"))
        self.assertTrue(is_youtube_homepage("https://www.youtube.com"))

        # Video and search pages are not homepages
        self.assertFalse(is_youtube_homepage("Python Tutorial - YouTube - Google Chrome"))
        self.assertFalse(is_youtube_homepage("MIT Lecture 1 - YouTube - Brave"))
        self.assertFalse(is_youtube_homepage("quantum physics - YouTube - Google Chrome"))

    def test_05_shorts_detection(self):
        self.assertTrue(is_youtube_shorts("Shorts - YouTube"))
        self.assertTrue(is_youtube_shorts("Science Experiment #shorts - YouTube"))
        self.assertTrue(is_youtube_shorts("How rockets work /shorts/ - YouTube"))
        self.assertTrue(is_youtube_shorts("shorts - google chrome"))
        self.assertTrue(is_youtube_shorts("Math trick youtube shorts - YouTube"))

        self.assertFalse(is_youtube_shorts("MIT 18.06 Linear Algebra Lecture 1 - YouTube"))
        self.assertFalse(is_youtube_shorts("Python Crash Course for Beginners - YouTube"))

    def test_06_educational_unlock_lifecycle(self):
        # Initial state: normal locked
        status = self.mgr.get_status()
        self.assertFalse(status["educational_unlock_active"])

        # Request unlock with proper reason
        res = self.mgr.request_educational_unlock(
            reason="Studying Python algorithms for exam",
            duration_minutes=45
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["mode"], "EDUCATIONAL_ONLY")
        self.assertIn("expires_at", res)

        # Status reflects active educational unlock
        status_after = self.mgr.get_status()
        self.assertTrue(status_after["educational_unlock_active"])
        self.assertEqual(status_after["educational_unlock_reason"], "Studying Python algorithms for exam")
        # Educational mode permits classified learning URLs, not blanket YouTube access.
        self.assertFalse(status_after["allowed"])

        # Cancel unlock
        cancel_res = self.mgr.cancel_educational_unlock()
        self.assertTrue(cancel_res["success"])
        self.assertFalse(self.mgr.get_status()["educational_unlock_active"])

    def test_07_educational_mode_enforces_blocked_shorts(self):
        self.mgr.request_educational_unlock("Studying physics for college exam")

        # Shorts must be blocked even when educational unlock is active
        self.assertTrue(self.mgr._should_restrict_window("Physics Experiment #shorts - YouTube - Google Chrome"))
        self.assertTrue(self.mgr._should_restrict_window("Shorts - YouTube - Brave"))
        self.assertIn("Shorts are blocked", self.mgr.last_educational_block_reason)

    def test_08_educational_mode_enforces_blocked_homepage(self):
        self.mgr.request_educational_unlock("Studying physics for college exam")

        # Homepage must be blocked
        self.assertTrue(self.mgr._should_restrict_window("YouTube - Google Chrome"))
        self.assertTrue(self.mgr._should_restrict_window("YouTube - Brave"))
        self.assertIn("Homepage is blocked", self.mgr.last_educational_block_reason)

    def test_09_educational_mode_enforces_no_entertainment_music_gaming(self):
        self.mgr.request_educational_unlock("Studying chemistry lectures")

        # Music blocked
        self.assertTrue(self.mgr._should_restrict_window("Top Pop Songs 2026 - YouTube - Google Chrome"))
        self.assertIn("Entertainment content", self.mgr.last_educational_block_reason)

        # Gaming blocked
        self.assertTrue(self.mgr._should_restrict_window("Minecraft Survival Gameplay Part 1 - YouTube"))

        # Comedy blocked
        self.assertTrue(self.mgr._should_restrict_window("Standup Comedy Special Full - YouTube"))

        # Serials blocked
        self.assertTrue(self.mgr._should_restrict_window("Anupama Full Episode 840 - YouTube"))

        # Movies blocked
        self.assertTrue(self.mgr._should_restrict_window("Official Movie Teaser Trailer 2026 - YouTube"))

    def test_10_educational_mode_allows_educational_videos(self):
        self.mgr.request_educational_unlock("Studying engineering lectures")

        # Educational lectures, tutorials, courses are allowed
        self.assertFalse(self.mgr._should_restrict_window("MIT 18.06 Linear Algebra Lecture 1 - YouTube - Google Chrome"))
        self.assertFalse(self.mgr._should_restrict_window("Python Full Course for Beginners Tutorial - YouTube"))
        self.assertFalse(self.mgr._should_restrict_window("Calculus 1 Full College Course - YouTube"))
        self.assertFalse(self.mgr._should_restrict_window("Computer Science Operating Systems Lecture - YouTube"))
        self.assertFalse(self.mgr._should_restrict_window("UPSC History Documentary Revision - YouTube"))

    def test_11_educational_mode_blocks_non_educational_content(self):
        self.mgr.request_educational_unlock("Studying mathematics")

        # Random clickbait / non-educational content without educational keywords is blocked
        self.assertTrue(self.mgr._should_restrict_window("Top 10 Most Expensive Luxury Mansions - YouTube"))
        self.assertIn("Only educational lectures", self.mgr.last_educational_block_reason)


if __name__ == "__main__":
    unittest.main()
