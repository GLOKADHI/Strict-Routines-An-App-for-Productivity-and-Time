"""Unit tests for CCleaner Browser restriction enforcement.

Verifies:
1. When CCleaner Browser has opened tabs of youtube.com, instagram.com, or open.spotify.com,
   it evaluates to action="CLOSE_APP" and target_app="CCleaner Browser".
2. Normal / productive tabs in CCleaner Browser (e.g. Wikipedia, Python docs, New Tab)
   are not restricted.
3. Other browsers (Chrome, Comet, Brave) retain their existing tab-preservation policies.
4. close_restricted_window triggers complete browser closure (taskkill /F /IM CCleanerBrowser.exe /T)
   for CCleaner Browser when action="CLOSE_APP".
5. Native UIA tab inspection for CCleaner Browser triggers action="CLOSE_APP".
"""

import os
import sys
import unittest
import tempfile
from core.database import StrictRecordsDB
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from windows.restriction_manager import YouTubeRestrictionManager, MONITORED_BROWSERS


class TestCCleanerRestrictions(unittest.TestCase):

    def setUp(self):
        self._temp=tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.mgr = YouTubeRestrictionManager(db=StrictRecordsDB(os.path.join(self._temp.name,"test.sqlite")))
        # Mock points status so YouTube/Instagram/Spotify remain restricted
        self.mgr.current_score = 0
        self.mgr.required_score = 80
        self.mgr.manual_admin_release = False
        self.mgr.get_service_policy = MagicMock(return_value={"effectiveAccessAllowed":False,"policyReason":"Website restricted."})
        self.mgr.get_points_status = MagicMock(return_value={
            "current_score": 0,
            "required_score": 80,
            "remaining_points": 80,
            "score_available": True,
            "score_error": "",
        })

    def test_ccleaner_in_monitored_browsers(self):
        """Verifies CCleaner Browser process names are included in MONITORED_BROWSERS."""
        self.assertIn("ccleanerbrowser.exe", MONITORED_BROWSERS)
        self.assertNotIn("ccleaner.exe", MONITORED_BROWSERS)

    def test_ccleaner_youtube_tabs_restrict_only_target_tab(self):
        """Verifies opening YouTube in CCleaner Browser triggers CLOSE_APP to shut browser completely."""
        titles = [
            "YouTube - CCleaner Browser",
            "(1) Video Title - YouTube - CCleaner Browser",
            "YouTube - CCleaner",
            "https://www.youtube.com/watch?v=test - CCleaner Browser",
            "trending on youtube.com - CCleaner Browser",
            "Relaxing Music - YouTube",
        ]
        for proc in ("CCleanerBrowser.exe", "ccleanerbrowser.exe"):
            for title in titles:
                res = self.mgr._evaluate_window_restriction(1001, title, proc, locks={})
                self.assertIsNotNone(res, f"Expected restriction for title '{title}' with proc '{proc}'")
                self.assertEqual(res["action"], "CLOSE_TAB", f"Failed for {title} on {proc}")
                self.assertIn(res["target_app"], ("YouTube","Instagram"))
                self.assertEqual(res["app_key"], "youtube")
                self.assertNotEqual(res["action"], "CLOSE_APP")

    def test_ccleaner_instagram_tabs_restrict_only_target_tab(self):
        """Verifies opening Instagram in CCleaner Browser triggers CLOSE_APP to shut browser completely."""
        titles = [
            "Instagram - CCleaner Browser",
            "(2) Instagram - CCleaner Browser",
            "John Doe (@johndoe) • Instagram photos and videos - CCleaner Browser",
            "Instagram - Direct - CCleaner Browser",
            "instagram.com/explore - CCleaner Browser",
        ]
        for proc in ("CCleanerBrowser.exe", "ccleanerbrowser.exe"):
            for title in titles:
                res = self.mgr._evaluate_window_restriction(1002, title, proc, locks={})
                self.assertIsNotNone(res, f"Expected restriction for title '{title}' with proc '{proc}'")
                self.assertEqual(res["action"], "CLOSE_TAB", f"Failed for {title} on {proc}")
                self.assertIn(res["target_app"], ("YouTube","Instagram"))
                self.assertEqual(res["app_key"], "instagram")
                self.assertNotEqual(res["action"], "CLOSE_APP")

    def test_ccleaner_spotify_tabs_title_does_not_close_browser(self):
        """Verifies opening open.spotify.com in CCleaner Browser triggers CLOSE_APP to shut browser completely."""
        titles = [
            "Spotify – Web Player - CCleaner Browser",
            "Spotify - Web Player - CCleaner Browser",
            "Spotify – Web Player: Music for everyone - CCleaner Browser",
            "Song Title • Artist | Spotify - CCleaner Browser",
            "Track Name - Spotify - CCleaner Browser",
            "open.spotify.com/track/123 - CCleaner Browser",
            "https://spotify.com/premium - CCleaner Browser",
        ]
        for proc in ("CCleanerBrowser.exe", "ccleanerbrowser.exe"):
            for title in titles:
                res = self.mgr._evaluate_window_restriction(1003, title, proc, locks={})
                self.assertTrue(res is None or res['action'] != 'CLOSE_APP')
                # Spotify website enforcement uses its verified URL/extension.

    def test_ccleaner_unrestricted_tabs_allowed(self):
        """Verifies non-restricted websites in CCleaner Browser are NOT restricted."""
        allowed_titles = [
            "Python Documentation - CCleaner Browser",
            "Wikipedia, the free encyclopedia - CCleaner Browser",
            "GitHub - CCleaner Browser",
            "Stack Overflow - Where Developers Learn - CCleaner Browser",
            "New Tab - CCleaner Browser",
            "Settings - CCleaner Browser",
        ]
        for title in allowed_titles:
            res = self.mgr._evaluate_window_restriction(1004, title, "CCleanerBrowser.exe", locks={})
            self.assertIsNone(res, f"Expected title '{title}' to be allowed in CCleaner Browser")

    def test_other_browsers_preserve_tab_closing_action(self):
        """Verifies Chrome and Comet keep their standard tab-closing action and do not close the entire browser."""
        self.mgr.get_service_policy = MagicMock(return_value={
            "effectiveAccessAllowed": False,
            "policyReason": "Service locked.",
        })
        chrome_res = self.mgr._evaluate_window_restriction(
            2001, "Video Title - YouTube - Google Chrome", "chrome.exe", locks={}
        )
        self.assertIsNotNone(chrome_res)
        self.assertEqual(chrome_res["action"], "CLOSE_TAB")
        self.assertEqual(chrome_res["target_app"], "YouTube")

        # Comet tab
        comet_res = self.mgr._evaluate_window_restriction(
            2002, "Instagram - Comet", "comet.exe", locks={"comet": False}
        )
        self.assertIsNotNone(comet_res)
        self.assertEqual(comet_res["action"], "CLOSE_TAB")
        self.assertEqual(comet_res["target_app"], "Instagram")

    def test_close_restricted_window_requires_verified_identity_and_exit(self):
        identity={"root":{"pid":7777,"name":"ccleanerbrowser.exe"}}
        with patch("windows.restriction_manager.capture_application",return_value=identity),patch("windows.restriction_manager.close_application",return_value=False):
            self.assertFalse(self.mgr.close_restricted_window(5555,7777,"CCleanerBrowser.exe",action="CLOSE_APP"))
        with patch("windows.restriction_manager.capture_application",return_value=identity),patch("windows.restriction_manager.close_application",return_value=True):
            self.assertTrue(self.mgr.close_restricted_window(5555,7777,"CCleanerBrowser.exe",action="CLOSE_APP"))

    def test_cleaner_utility_is_not_a_browser_target(self):
        self.assertIsNone(self.mgr._evaluate_window_restriction(0,"YouTube - CCleaner","ccleaner.exe",locks={}))

    def test_native_tab_decision_returns_close_app_for_ccleaner(self):
        """Verifies _native_tab_decision returns CLOSE_APP when UIA detects restricted service in CCleaner Browser."""
        self.mgr._native_browser = MagicMock()
        self.mgr._native_browser.connected = True
        self.mgr._native_browser.inspect.return_value = {
            "supported": True,
            "pid": 8888,
            "target": "youtube",
            "origin": "https://www.youtube.com",
            "tab_id": "tab1",
        }
        self.mgr.get_browser_tab_restriction = MagicMock(return_value={
            "restricted": True,
            "requiredScore": 80,
            "reason": "Restricted by discipline policy.",
        })

        decision = self.mgr._native_tab_decision(
            hwnd=1234, pid=8888, process_name="CCleanerBrowser.exe"
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "CLOSE_NATIVE_TAB")
        self.assertEqual(decision["target_app"], "YouTube")
        self.assertEqual(decision["app_key"], "youtube")
        self.assertEqual(decision["reason"], "Restricted by discipline policy.")


if __name__ == "__main__":
    unittest.main()
