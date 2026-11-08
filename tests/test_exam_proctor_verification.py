"""
STRICTRECORDS & EXAM PROCTOR — Comprehensive 21-Item Verification Test Suite
Validates Sections 1 to 44 of the Final PC Application Specification:
- Desktop Binary packaging
- Window chrome controls
- Local engine & server health
- Windows startup registry configuration
- YouTube restriction state machine & browser closure
- Full-Screen Point Lock Screen & SVG AI Proctor Eye
- Admin override verification
- Access restoration animation & state flow
- Installer package integrity
"""

import json
import os
import sys
import unittest
import urllib.parse
import urllib.request
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import create_server
from core.ui_dashboard import DASHBOARD_HTML
from windows.agent import StrictRecordsWindowsAgent
from windows.restriction_manager import YouTubeRestrictionManager, RestrictionState


class TestExamProctorVerification(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = StrictRecordsDB()
        cls.engine = ScoringEngine(db=cls.db)
        cls.restriction_mgr = YouTubeRestrictionManager(db=cls.db, engine=cls.engine)
        cls.server = create_server(host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.server_url = f"http://127.0.0.1:{cls.port}"
        import threading
        import time
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    # 1. Desktop EXE entry point & packaging structure
    def test_01_desktop_app_module_and_api(self):
        import desktop_app
        self.assertTrue(hasattr(desktop_app, "run_desktop_app"))
        self.assertTrue(hasattr(desktop_app, "DesktopApi"))
        api = desktop_app.DesktopApi()
        self.assertIsNotNone(api)
        # Test API exposed methods
        self.assertTrue(callable(api.minimize))
        self.assertTrue(callable(api.maximize))
        self.assertTrue(callable(api.close))
        self.assertTrue(callable(api.get_health))
        self.assertTrue(callable(api.set_startup))

    # 2. Window Chrome Controls in Dashboard HTML
    def test_02_window_controls_in_html(self):
        self.assertIn('class="window-controls-group"', DASHBOARD_HTML)
        self.assertIn('onclick="windowControl(\'minimize\')"', DASHBOARD_HTML)
        self.assertIn('onclick="windowControl(\'maximize\')"', DASHBOARD_HTML)
        self.assertIn('onclick="windowControl(\'close\')"', DASHBOARD_HTML)

    # 3. Full-Screen Point Lock Screen HTML element
    def test_03_youtube_lock_screen_overlay_present(self):
        self.assertIn('id="youtubeLockScreenOverlay"', DASHBOARD_HTML)
        self.assertIn('class="lock-screen-overlay"', DASHBOARD_HTML)
        self.assertIn('class="lock-card"', DASHBOARD_HTML)

    # 4. Animated SVG AI Proctor Eye looking directly at user
    def test_04_svg_ai_proctor_eye(self):
        self.assertIn('class="proctor-eye-svg"', DASHBOARD_HTML)
        self.assertIn('class="pupil-pulse"', DASHBOARD_HTML)
        self.assertIn('class="eye-orbit-spin"', DASHBOARD_HTML)
        self.assertIn('class="eye-scan-line"', DASHBOARD_HTML)

    # 5. Lock screen typography and headings
    def test_05_lock_screen_headings(self):
        self.assertIn('ACCESS BLOCKED', DASHBOARD_HTML)
        self.assertIn('YouTube is currently not allowed', DASHBOARD_HTML)
        self.assertIn('id="lockCurrentPoints"', DASHBOARD_HTML)
        self.assertIn('id="lockRequiredPoints"', DASHBOARD_HTML)
        self.assertIn('id="lockProgressBarFill"', DASHBOARD_HTML)
        self.assertIn('id="lockDeficitBadge"', DASHBOARD_HTML)

    # 6. Action buttons: View Progress and Admin Override
    def test_06_lock_screen_buttons(self):
        self.assertIn('VIEW PROGRESS', DASHBOARD_HTML)
        self.assertIn('ADMIN OVERRIDE', DASHBOARD_HTML)
        self.assertIn('id="adminOverrideModal"', DASHBOARD_HTML)

    # 7. Restoration animation banner
    def test_07_access_restored_banner(self):
        self.assertIn('id="lockReleaseBanner"', DASHBOARD_HTML)
        self.assertIn('ACCESS RESTORED', DASHBOARD_HTML)

    # 8. Internal Services Health Panel in Dashboard
    def test_08_internal_services_health_panel(self):
        self.assertIn('INTERNAL SERVICES HEALTH', DASHBOARD_HTML)
        self.assertIn('id="healthAppBadge"', DASHBOARD_HTML)
        self.assertIn('id="healthEngineBadge"', DASHBOARD_HTML)
        self.assertIn('id="healthProctorBadge"', DASHBOARD_HTML)
        self.assertIn('id="healthDbBadge"', DASHBOARD_HTML)
        self.assertIn('id="healthBrowserBadge"', DASHBOARD_HTML)
        self.assertIn('id="healthRestrictionBadge"', DASHBOARD_HTML)

    # 9. Startup switch in Settings
    def test_09_startup_switch_in_settings(self):
        self.assertIn('id="chkStartWithWindows"', DASHBOARD_HTML)
        self.assertIn('toggleStartupSetting', DASHBOARD_HTML)
        self.assertIn('id="startupRegistryStatus"', DASHBOARD_HTML)

    # 10. System Health REST endpoint
    def test_10_system_health_endpoint(self):
        url = f"{self.server_url}/api/system/health"
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data.get("Application"), "READY")
        self.assertEqual(data.get("Local Engine"), "RUNNING")
        self.assertEqual(data.get("Database"), "CONNECTED")
        self.assertEqual(data.get("Browser Control"), "ACTIVE")
        self.assertIn("Restriction", data)

    # 11. Windows Startup Registry endpoint
    def test_11_startup_registry_endpoint(self):
        url = f"{self.server_url}/api/system/startup"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertIn("enabled", data)
        self.assertIn("key_name", data)

    # 12. Restriction Status endpoint
    def test_12_restriction_status_endpoint(self):
        url = f"{self.server_url}/api/restriction/status"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertIn("state", data)
        self.assertIn("allowed", data)
        self.assertIn("current_score", data)
        self.assertIn("required_score", data)

    # 13. Restriction State Machine: NORMAL state
    def test_13_restriction_state_machine_normal(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        self.assertEqual(mgr.state, RestrictionState.NORMAL)
        status = mgr.get_status()
        self.assertEqual(status["state"], "NORMAL")
        self.assertFalse(status["lock_screen_visible"])

    # 14. Restriction State Machine: Simulation of Block (64/80 pts)
    def test_14_restriction_simulation_block(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        res = mgr.simulate_trigger(score=64, required=80)
        self.assertEqual(res["state"], "LOCK_SCREEN_ACTIVE")
        self.assertTrue(res["lock_screen_visible"])
        self.assertEqual(res["current_score"], 64)
        self.assertEqual(res["required_score"], 80)
        self.assertEqual(res["remaining_points"], 16)
        self.assertFalse(res["allowed"])

    # 15. Restriction State Machine: Simulation of Score Restoration (85/80 pts)
    def test_15_restriction_simulation_restore(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        mgr.simulate_trigger(score=64, required=80)
        res = mgr.simulate_restore(score=85)
        self.assertEqual(res["state"], "RELEASED")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["current_score"], 85)

    # 16. Restriction State Machine: Reset to NORMAL
    def test_16_restriction_reset(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        mgr.simulate_trigger(score=64, required=80)
        res = mgr.reset_to_normal()
        self.assertEqual(res["state"], "NORMAL")
        self.assertFalse(res["lock_screen_visible"])

    # 17. Admin Release PIN verification
    def test_17_admin_release_pin(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        mgr.simulate_trigger(score=64, required=80)
        
        # Wrong pin
        bad_res = mgr.admin_release(pin="0000")
        self.assertFalse(bad_res["success"])

        # Correct pin (default 1984)
        good_res = mgr.admin_release(pin="1984")
        self.assertTrue(good_res["success"])
        self.assertEqual(mgr.state, RestrictionState.RELEASED)
        self.assertTrue(mgr.manual_admin_release)

    # 18. Browser Closure Win32 safety
    def test_18_browser_closure_safe_processes(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        # Should refuse to close explorer.exe or taskmgr.exe
        self.assertFalse(mgr.close_browser_window(hwnd=0, pid=999, proc_name="explorer.exe"))
        self.assertFalse(mgr.close_browser_window(hwnd=0, pid=999, proc_name="taskmgr.exe"))

    # 19. Event logging history
    def test_19_immutable_event_logging(self):
        mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        initial_count = len(mgr.events_history)
        mgr.log_event(
            event_type="YOUTUBE_ACCESS_ATTEMPT",
            reason="Test attempt",
            current_points=50,
            required_points=80,
        )
        self.assertEqual(len(mgr.events_history), initial_count + 1)
        self.assertEqual(mgr.last_event, "YOUTUBE_ACCESS_ATTEMPT")

    # 20. Endpoints for test triggers
    def test_20_restriction_test_endpoints(self):
        # POST simulate_block
        data = json.dumps({"score": 64, "required": 80}).encode("utf-8")
        req = urllib.request.Request(f"{self.server_url}/api/restriction/simulate_block", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(res.get("state"), "LOCK_SCREEN_ACTIVE")

        # POST admin_release
        data = json.dumps({"pin": "1984"}).encode("utf-8")
        req = urllib.request.Request(f"{self.server_url}/api/restriction/admin_release", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(res.get("success"))

    # 21. Installer module exists and has required structure
    def test_21_installer_module_structure(self):
        import installer
        self.assertTrue(hasattr(installer, "install"))
        self.assertTrue(hasattr(installer, "create_shortcut"))
        self.assertTrue(hasattr(installer, "register_uninstaller"))
        self.assertEqual(installer.APP_NAME, "Exam Proctor")


if __name__ == "__main__":
    unittest.main()
