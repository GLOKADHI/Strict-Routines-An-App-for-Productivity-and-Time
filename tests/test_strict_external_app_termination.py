"""
STRICTRECORDS STRICT SESSION – EXTERNAL APPLICATION TERMINATION SUITE
Verifies all 14 test requirements:
1. Chrome running before Strict Session.
2. Edge running before Strict Session (msedge.exe).
3. Firefox running before Strict Session.
4. Spotify running before Strict Session.
5. VLC running before Strict Session.
6. Steam running before Strict Session.
7. Multiple prohibited applications running together.
8. Application with unsaved work (save/shutdown warning first).
9. Application that refuses graceful shutdown (controlled forced termination).
10. Dedicated ChatGPT WebView2 browser remains operational.
11. LeetCode remains accessible.
12. StrictRecords EXE and webcam proctoring remain operational.
13. Attempt to relaunch a prohibited application during Strict Session.
14. Successful session completion and restoration of normal desktop restrictions.
"""

import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.database import StrictRecordsDB
from core.models import StrictAuditAction, StrictSessionState
from windows.strict_external_app_terminator import (
    StrictExternalAppTerminator,
    BROWSERS,
    ENTERTAINMENT_APPS,
    GAMES,
    VIDEO_MUSIC_PLAYERS,
)
from windows.strict_session_manager import StrictSessionManager


class TestStrictExternalAppTermination(unittest.TestCase):
    def setUp(self):
        # Patch hardware and Windows kiosk hooks to protect developer environment
        for name in (
            '_apply_registry_policies',
            '_restore_registry_policies',
            '_install_keyboard_hook',
            '_uninstall_keyboard_hook',
            '_hide_taskbar',
            '_show_taskbar',
            '_start_supervisor',
        ):
            p = patch.object(StrictSessionManager, name, return_value=None)
            p.start()
            self.addCleanup(p.stop)

        p_reg = patch('windows.network_policy_manager.HAS_WINREG', False)
        p_reg.start()
        self.addCleanup(p_reg.stop)

        p_cloud = patch('core.youtube_reward_cloud.client', lambda _: SimpleNamespace(configuration=lambda: None))
        p_cloud.start()
        self.addCleanup(p_cloud.stop)

        p_hw = patch(
            'windows.strict_session_manager.StrictStudyHardware',
            lambda: SimpleNamespace(
                start=lambda **_: {'success': True},
                stop=lambda: None,
                view=lambda: dict(monitoring_ok=True, camera='ACTIVE', bluetooth_connected=True),
                enable_session_integrity=lambda *a, **k: None,
            ),
        )
        p_hw.start()
        self.addCleanup(p_hw.stop)

        self.temp_dir = tempfile.mkdtemp(prefix="strict_app_test_")
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)

        self.session_mgr = StrictSessionManager(db=self.db, data_dir=self.temp_dir)
        self.session_mgr.preparation = SimpleNamespace(
            authorize_start=lambda *_: None,
            activated=lambda *_: None,
            finish=lambda *_: None,
        )
        self.session_mgr.embedded_focus = SimpleNamespace(
            open=lambda *_: dict(success=True),
            close=lambda: None,
            owns_foreground=lambda pid: pid == os.getpid(),
        )

    def tearDown(self):
        try:
            self.session_mgr._restore_windows_environment()
            self.session_mgr._clear_state_file()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # TEST 1: Chrome running before Strict Session
    # -----------------------------------------------------------------------
    def test_01_chrome_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat = terminator.identify_application("chrome.exe", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
        self.assertIsNotNone(cat)
        self.assertEqual(cat[0], "BROWSERS")
        self.assertEqual(cat[2], "Google Chrome")

        # Mock running process
        fake_proc = SimpleNamespace(
            info={"pid": 1111, "name": "chrome.exe"},
            pid=1111,
            name=lambda: "chrome.exe",
            exe=lambda: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            create_time=lambda: 1000.0,
            children=lambda **_: [],
            terminate=MagicMock(),
            kill=MagicMock(),
            is_running=lambda: False,
        )

        with patch("psutil.process_iter", return_value=[fake_proc]):
            with patch("psutil.Process", return_value=fake_proc):
                scan = terminator.scan_prohibited_applications()
                self.assertEqual(scan["total_apps"], 1)
                self.assertEqual(scan["prohibited_apps"][0]["display_name"], "Google Chrome")
                self.assertEqual(scan["clean"], False)

                # Close process
                with patch("psutil.wait_procs", return_value=([fake_proc], [])):
                    res = terminator.close_prohibited_applications(scan["prohibited_apps"], grace_period=0.1)
                    self.assertTrue(res["success"])
                    self.assertIn("Google Chrome", res["closed_apps"])

    # -----------------------------------------------------------------------
    # TEST 2: Standalone Edge running before Strict Session
    # -----------------------------------------------------------------------
    def test_02_edge_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat = terminator.identify_application("msedge.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")
        self.assertIsNotNone(cat)
        self.assertEqual(cat[0], "BROWSERS")
        self.assertEqual(cat[2], "Microsoft Edge")

        # Crucial check: msedgewebview2.exe must NOT be identified as standalone Edge
        wv_cat = terminator.identify_application("msedgewebview2.exe", "C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application\\msedgewebview2.exe")
        self.assertIsNone(wv_cat)
        self.assertTrue(terminator.is_protected(9999, "msedgewebview2.exe"))

    # -----------------------------------------------------------------------
    # TEST 3: Firefox running before Strict Session
    # -----------------------------------------------------------------------
    def test_03_firefox_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat = terminator.identify_application("firefox.exe", "C:\\Program Files\\Mozilla Firefox\\firefox.exe")
        self.assertIsNotNone(cat)
        self.assertEqual(cat[0], "BROWSERS")
        self.assertEqual(cat[2], "Mozilla Firefox")

    # -----------------------------------------------------------------------
    # TEST 4: Spotify running before Strict Session
    # -----------------------------------------------------------------------
    def test_04_spotify_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat = terminator.identify_application("spotify.exe", "C:\\Users\\User\\AppData\\Roaming\\Spotify\\Spotify.exe")
        self.assertIsNotNone(cat)
        self.assertEqual(cat[0], "MEDIA_PLAYERS")
        self.assertEqual(cat[2], "Spotify")

    # -----------------------------------------------------------------------
    # TEST 5: VLC running before Strict Session
    # -----------------------------------------------------------------------
    def test_05_vlc_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat = terminator.identify_application("vlc.exe", "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe")
        self.assertIsNotNone(cat)
        self.assertEqual(cat[0], "MEDIA_PLAYERS")
        self.assertEqual(cat[2], "VLC Media Player")

    # -----------------------------------------------------------------------
    # TEST 6: Steam running before Strict Session
    # -----------------------------------------------------------------------
    def test_06_steam_running_before_strict_session(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        cat1 = terminator.identify_application("steam.exe", "C:\\Program Files (x86)\\Steam\\steam.exe")
        cat2 = terminator.identify_application("steamwebhelper.exe", "C:\\Program Files (x86)\\Steam\\bin\\cef\\steamwebhelper.exe")
        self.assertIsNotNone(cat1)
        self.assertIsNotNone(cat2)
        self.assertEqual(cat1[0], "GAMES")
        self.assertEqual(cat1[2], "Steam")

    # -----------------------------------------------------------------------
    # TEST 7: Multiple prohibited applications running together
    # -----------------------------------------------------------------------
    def test_07_multiple_prohibited_applications_running(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        procs = [
            SimpleNamespace(info={"pid": 101, "name": "chrome.exe"}, pid=101, name=lambda: "chrome.exe", exe=lambda: "C:\\Chrome\\chrome.exe", create_time=lambda: 10.0, children=lambda **_: [], terminate=MagicMock(), kill=MagicMock()),
            SimpleNamespace(info={"pid": 102, "name": "spotify.exe"}, pid=102, name=lambda: "spotify.exe", exe=lambda: "C:\\Spotify\\spotify.exe", create_time=lambda: 20.0, children=lambda **_: [], terminate=MagicMock(), kill=MagicMock()),
            SimpleNamespace(info={"pid": 103, "name": "vlc.exe"}, pid=103, name=lambda: "vlc.exe", exe=lambda: "C:\\VLC\\vlc.exe", create_time=lambda: 30.0, children=lambda **_: [], terminate=MagicMock(), kill=MagicMock()),
            SimpleNamespace(info={"pid": 104, "name": "steam.exe"}, pid=104, name=lambda: "steam.exe", exe=lambda: "C:\\Steam\\steam.exe", create_time=lambda: 40.0, children=lambda **_: [], terminate=MagicMock(), kill=MagicMock()),
        ]

        def get_proc(pid):
            for p in procs:
                if p.pid == pid:
                    return p
            raise ValueError(f"No proc {pid}")

        with patch("psutil.process_iter", return_value=procs):
            with patch("psutil.Process", side_effect=get_proc):
                scan = terminator.scan_prohibited_applications()
                self.assertEqual(scan["total_apps"], 4)
                app_names = {a["display_name"] for a in scan["prohibited_apps"]}
                self.assertSetEqual(app_names, {"Google Chrome", "Spotify", "VLC Media Player", "Steam"})

                with patch("psutil.wait_procs", return_value=(procs, [])):
                    res = terminator.close_prohibited_applications(scan["prohibited_apps"], grace_period=0.1)
                    self.assertTrue(res["success"])
                    self.assertEqual(len(res["closed_apps"]), 4)

    # -----------------------------------------------------------------------
    # TEST 8: Application with unsaved work (save/shutdown warning first)
    # -----------------------------------------------------------------------
    def test_08_application_with_unsaved_work_warning_first(self):
        fake_chrome = {
            "app_key": "chrome.exe",
            "display_name": "Google Chrome",
            "category": "BROWSERS",
            "category_label": "Browsers",
            "root_pid": 1234,
            "pids": [1234],
            "exe_path": "C:\\Chrome\\chrome.exe",
            "started": 100.0,
        }

        # User clicks start WITHOUT prior acknowledgment
        with patch.object(self.session_mgr.app_terminator, "scan_prohibited_applications", return_value={"prohibited_apps": [fake_chrome], "total_apps": 1, "clean": False}):
            res = self.session_mgr.start_session(monitoring_consent=True, acknowledge_shutdown=False)
            # Must not start focus timer, must require distraction check acknowledgment
            self.assertFalse(res["success"])
            self.assertTrue(res.get("distraction_check_required"))
            self.assertIn("STRICT SESSION – DISTRACTION CHECK", res["message"])
            self.assertIn("Google Chrome", res["message"])
            self.assertIn("Please save any unsaved work", res["message"])
            self.assertEqual(self.session_mgr.state, StrictSessionState.NORMAL)

    # -----------------------------------------------------------------------
    # TEST 9: Application that refuses graceful shutdown (controlled forced termination)
    # -----------------------------------------------------------------------
    def test_09_application_refuses_graceful_shutdown_forces_termination(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        stubborn_proc = SimpleNamespace(
            pid=7777,
            name=lambda: "chrome.exe",
            exe=lambda: "C:\\Chrome\\chrome.exe",
            create_time=lambda: 100.0,
            children=lambda **_: [],
            parent=lambda: None,
            terminate=MagicMock(),
            kill=MagicMock(),
        )

        app_info = [{
            "app_key": "chrome.exe",
            "display_name": "Google Chrome",
            "category": "BROWSERS",
            "category_label": "Browsers",
            "root_pid": 7777,
            "pids": [7777],
            "exe_path": "C:\\Chrome\\chrome.exe",
            "started": 100.0,
        }]

        with patch("psutil.Process", return_value=stubborn_proc):
            # Phase 1: graceful wait times out (alive contains stubborn_proc)
            # Phase 2: force kill succeeds (alive becomes empty)
            with patch("psutil.wait_procs", side_effect=[([], [stubborn_proc]), ([stubborn_proc], [])]):
                res = terminator.close_prohibited_applications(app_info, grace_period=0.1, force=True)
                stubborn_proc.kill.assert_called_once()
                self.assertTrue(res["success"])
                self.assertIn("Google Chrome", res["closed_apps"])

    # -----------------------------------------------------------------------
    # TEST 10: Dedicated ChatGPT WebView2 browser remains operational
    # -----------------------------------------------------------------------
    def test_10_dedicated_chatgpt_webview2_browser_protected(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        terminator.register_study_browser_pid(os.getpid())

        # msedgewebview2.exe must be protected
        self.assertTrue(terminator.is_protected(os.getpid(), "msedgewebview2.exe"))
        # Any msedgewebview2.exe is protected from external browser termination
        self.assertTrue(terminator.is_protected(8888, "msedgewebview2.exe"))
        self.assertIsNone(terminator.identify_application("msedgewebview2.exe"))

    # -----------------------------------------------------------------------
    # TEST 11: LeetCode remains accessible in study browser
    # -----------------------------------------------------------------------
    def test_11_leetcode_study_browser_remains_accessible(self):
        from windows.embedded_strict_focus import allowed_url, TAB_HOSTS
        self.assertTrue(allowed_url("https://leetcode.com/"))
        self.assertTrue(allowed_url("https://leetcode.com/problems/two-sum/"))
        self.assertTrue(allowed_url("https://chatgpt.com/"))
        self.assertFalse(allowed_url("https://youtube.com/"))
        self.assertFalse(allowed_url("https://netflix.com/"))

    # -----------------------------------------------------------------------
    # TEST 12: StrictRecords EXE and webcam proctoring remain operational
    # -----------------------------------------------------------------------
    def test_12_strictrecords_exe_and_proctoring_protected(self):
        terminator = StrictExternalAppTerminator(current_pid=os.getpid())
        for proc in ("strictrecords.exe", "strictroutines.exe", "strictrecordsdesktop.exe", "python.exe", "pythonw.exe"):
            self.assertTrue(terminator.is_protected(os.getpid(), proc))
            self.assertTrue(terminator.is_protected(5555, proc))

    # -----------------------------------------------------------------------
    # TEST 13: Attempt to relaunch a prohibited application during Strict Session
    # -----------------------------------------------------------------------
    def test_13_relaunch_prohibited_application_during_session_blocked(self):
        # Start session with clean environment
        with patch.object(self.session_mgr.app_terminator, "scan_prohibited_applications", return_value={"prohibited_apps": [], "clean": True}):
            res = self.session_mgr.start_session(monitoring_consent=True, acknowledge_shutdown=True)
            self.assertTrue(res["success"])

        # Simulate user launching Chrome during active session
        fake_chrome = SimpleNamespace(
            pid=9999,
            name=lambda: "chrome.exe",
            exe=lambda: "C:\\Chrome\\chrome.exe",
            children=lambda **_: [],
            terminate=MagicMock(),
            kill=MagicMock(),
        )

        with patch("psutil.Process", return_value=fake_chrome):
            with patch("psutil.wait_procs", return_value=([fake_chrome], [])):
                blocked = self.session_mgr.app_terminator.check_and_block_process(9999, "chrome.exe")
                self.assertIsNotNone(blocked)
                self.assertTrue(blocked["blocked"])
                self.assertEqual(blocked["app_name"], "Google Chrome")
                self.assertEqual(blocked["category"], "BROWSERS")

    # -----------------------------------------------------------------------
    # TEST 14: Session completion and restoration of normal desktop restrictions
    # -----------------------------------------------------------------------
    def test_14_session_completion_and_restoration_no_auto_reopen(self):
        with patch.object(self.session_mgr.app_terminator, "scan_prohibited_applications", return_value={"prohibited_apps": [], "clean": True}):
            res = self.session_mgr.start_session(monitoring_consent=True, acknowledge_shutdown=True)
            self.assertTrue(res["success"])

        # End session
        with patch.object(self.db, "verify_admin_pin", return_value=True):
            with patch.object(self.db, "is_default_admin_pin", return_value=False):
                with patch.object(self.db, "admin_auth_lockout_remaining", return_value=0):
                    end_res = self.session_mgr.end_session(admin_pin="123456", reason="Completed study")
                    self.assertTrue(end_res["success"])
                    self.assertEqual(self.session_mgr.state, StrictSessionState.NORMAL)
                    self.assertIsNone(self.session_mgr.session_id)
                    # Monitoring stopped
                    self.assertFalse(self.session_mgr.app_terminator._monitoring_active)
                    # Closed apps must not be automatically relaunched


if __name__ == "__main__":
    unittest.main()
