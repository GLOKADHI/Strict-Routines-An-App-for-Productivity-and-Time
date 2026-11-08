import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from windows.tray_manager import (
    StrictRecordsTrayManager,
    IDM_STATUS_HEADER,
    IDM_OPEN_DASHBOARD,
    IDM_CURRENT_SCORE,
    IDM_NEXT_HOURLY,
    IDM_SUBMISSION_STATUS,
    IDM_DEVICE_SYNC,
    IDM_YOUTUBE_STATUS,
    IDM_PAUSE_NOTIFICATIONS,
    IDM_SETTINGS,
    IDM_EXIT,
)
from desktop_app import DesktopApi, _win32_force_foreground


class TestTrayOptionsImmediateLaunch(unittest.TestCase):
    def setUp(self):
        self.dashboard_calls = []
        self.settings_calls = []
        self.exit_calls = []

        def mock_open_dashboard(**kwargs):
            self.dashboard_calls.append(kwargs)

        def mock_open_settings():
            self.settings_calls.append(True)

        def mock_exit():
            self.exit_calls.append(True)

        self.mock_open_dashboard = mock_open_dashboard
        self.mock_open_settings = mock_open_settings
        self.mock_exit = mock_exit

        # Create or update tray instance
        self.tray = StrictRecordsTrayManager(
            on_open_dashboard=self.mock_open_dashboard,
            on_open_settings=self.mock_open_settings,
            on_exit_app=self.mock_exit,
            server_port=8765,
        )

    def test_status_header_opens_app_immediately(self):
        res = self.tray.execute_menu_command(IDM_STATUS_HEADER)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")

    def test_open_dashboard_opens_app_immediately(self):
        res = self.tray.execute_menu_command(IDM_OPEN_DASHBOARD)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")

    def test_current_score_opens_app_immediately(self):
        res = self.tray.execute_menu_command(IDM_CURRENT_SCORE)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")
        self.assertEqual(res.get("tab"), "dashboard")

    def test_next_hourly_opens_app_immediately_with_overlay(self):
        res = self.tray.execute_menu_command(IDM_NEXT_HOURLY)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_HOURLY_STATUS")

    def test_submission_status_opens_app_immediately_with_overlay(self):
        res = self.tray.execute_menu_command(IDM_SUBMISSION_STATUS)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_HOURLY_STATUS")

    def test_device_sync_opens_app_immediately_on_devices_tab(self):
        res = self.tray.execute_menu_command(IDM_DEVICE_SYNC)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")
        self.assertEqual(res.get("tab"), "devices")

    def test_youtube_status_opens_app_immediately_on_restrictions_tab(self):
        res = self.tray.execute_menu_command(IDM_YOUTUBE_STATUS)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")
        self.assertEqual(res.get("tab"), "restrictions")

    def test_pause_notifications_opens_app_immediately_on_settings_tab(self):
        res = self.tray.execute_menu_command(IDM_PAUSE_NOTIFICATIONS)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "TOGGLE_PAUSE_NOTIFICATIONS")
        self.assertIn("notifications_paused", res)

    def test_settings_opens_app_immediately_on_settings_tab(self):
        res = self.tray.execute_menu_command(IDM_SETTINGS)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_SETTINGS")
        self.assertEqual(res.get("tab"), "settings")

    def test_unknown_command_fallback_opens_app_immediately(self):
        res = self.tray.execute_menu_command(99999)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "OPEN_DASHBOARD")

    def test_desktop_api_show_dashboard_unblocks_events_shown(self):
        api = DesktopApi()
        mock_win = MagicMock()
        mock_win.events = MagicMock()
        mock_win.events.shown = MagicMock()
        mock_win.events.shown.is_set.return_value = False
        api.set_window(mock_win)

        res = api.show_dashboard(tab="devices", open_hourly_if_active=False)
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("window_visible"))
        self.assertEqual(res.get("tab"), "devices")
        # Ensure shown was set so wait(20) is unblocked
        mock_win.events.shown.set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
