"""
Comprehensive End-to-End Tests for YouTube & Instagram Restriction Toggles,
Browser Enforcement Controller, Synchronization Version Control, and Diagnostics.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.server import EventBus
from windows.restriction_manager import YouTubeRestrictionManager


class SuccessfulTestPolicyController:
    def apply_service_policy(self, service, restriction_enabled):
        return {
            "enforcementStatus": "APPLIED",
            "effectiveRestrictionEnabled": restriction_enabled,
            "effectiveAccessAllowed": not restriction_enabled,
            "ruleActive": restriction_enabled,
            "urlBlocked": restriction_enabled,
            "verified": True,
            "error": None,
        }


class TestYouTubeInstagramRestrictions(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.event_bus = EventBus.get_instance()
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)
        self.yt_mgr = YouTubeRestrictionManager(db=self.db)
        self.yt_mgr.browser_policy_controller = SuccessfulTestPolicyController()
        self.db._notify_android_app_locks_adb = lambda *_args, **_kwargs: {
            "status": "PENDING_CONFIRMATION", "confirmed": False
        }
        self.yt_mgr.reconcile_browser_policies()

    def tearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_01_explicit_restriction_states_schema(self):
        """Verifies all required explicit state fields exist on YouTube and Instagram."""
        status = self.db.get_sidebar_restrictions_status()["status"]
        for service in ("youtube", "instagram"):
            item = status[service]
            self.assertEqual(item["service"], service)
            self.assertIsInstance(item["requestedRestrictionEnabled"], bool)
            self.assertIsInstance(item["effectiveRestrictionEnabled"], bool)
            self.assertIsInstance(item["effectiveAccessAllowed"], bool)
            self.assertIn(item["policyDecision"], ("ALLOW", "BLOCK"))
            self.assertIsInstance(item["policyReason"], str)
            self.assertTrue(len(item["policyReason"]) > 0)
            self.assertIn(item["enforcementStatus"], ("APPLIED", "FAILED", "PENDING"))
            self.assertIsInstance(item["lastConfirmedAt"], str)
            self.assertGreaterEqual(item["policyVersion"], 1)

    def test_02_youtube_off_unblocks_browser_and_persists(self):
        """
        Scenario:
        1. Start with YouTube restriction ON -> browser controller blocks YouTube.
        2. Click YouTube toggle OFF -> state updates to OFF / ALLOWED / APPLIED.
        3. Browser controller genuinely allows YouTube homepage & videos.
        4. Reload DB & controller -> remains OFF / ALLOWED.
        """
        # 1. Initial state is ON / BLOCKED
        init_yt = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertTrue(init_yt["requestedRestrictionEnabled"])
        self.assertTrue(init_yt["effectiveRestrictionEnabled"])
        self.assertFalse(init_yt["effectiveAccessAllowed"])

        # Browser enforcement blocks YouTube when ON
        block_eval = self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        self.assertIsNotNone(block_eval)
        self.assertEqual(block_eval["action"], "CLOSE_TAB")
        self.assertEqual(block_eval["target_app"], "YouTube")

        # 2. Click YouTube toggle OFF
        res_off = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            event_bus=self.event_bus,
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(res_off["success"])
        self.assertFalse(res_off["requestedRestrictionEnabled"])
        self.assertFalse(res_off["effectiveRestrictionEnabled"])
        self.assertTrue(res_off["effectiveAccessAllowed"])
        self.assertEqual(res_off["policyDecision"], "ALLOW")
        self.assertEqual(res_off["enforcementStatus"], "APPLIED")
        self.assertEqual(res_off["restriction"], "OFF")
        self.assertEqual(res_off["access"], "ALLOWED")

        # 3. Verify browser controller genuinely permits YouTube homepage, search, and videos
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        )
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("Music Video - YouTube - Microsoft Edge", "msedge.exe")
        )
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("YouTube Shorts - Brave", "brave.exe")
        )

        # 4. Reload DB & RestrictionManager (simulating app restart)
        reloaded_db = StrictRecordsDB(db_path=self.temp_db_path)
        reloaded_mgr = YouTubeRestrictionManager(db=reloaded_db)
        reloaded_yt = reloaded_db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertFalse(reloaded_yt["requestedRestrictionEnabled"])
        self.assertFalse(reloaded_yt["effectiveRestrictionEnabled"])
        self.assertTrue(reloaded_yt["effectiveAccessAllowed"])
        self.assertEqual(reloaded_yt["restriction"], "OFF")
        self.assertEqual(reloaded_yt["access"], "ALLOWED")
        self.assertIsNone(
            reloaded_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        )

    def test_03_youtube_on_blocks_browser_and_never_reverts_to_off(self):
        """
        Scenario:
        1. Start with YouTube restriction OFF (and youtube_unlocked_date set for today).
        2. Click YouTube toggle ON.
        3. Verify toggle stays ON (purple) on subsequent status polls and never reverts to OFF (black).
        4. Verify browser controller immediately blocks YouTube again.
        """
        # Turn OFF first (sets youtube_unlocked_date = today)
        self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(self.db.is_youtube_unlocked_for_today())

        # Now turn ON
        res_on = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=True,
            requested_by="user",
            event_bus=self.event_bus,
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(res_on["success"])
        self.assertTrue(res_on["requestedRestrictionEnabled"])
        self.assertTrue(res_on["effectiveRestrictionEnabled"])
        self.assertFalse(res_on["effectiveAccessAllowed"])
        self.assertEqual(res_on["policyDecision"], "BLOCK")
        self.assertEqual(res_on["enforcementStatus"], "APPLIED")
        self.assertEqual(res_on["restriction"], "ON")
        self.assertEqual(res_on["access"], "BLOCKED")

        # Crucial regression check: youtube_unlocked_date must be cleared so polling never reverts ON -> OFF
        self.assertFalse(self.db.is_youtube_unlocked_for_today())

        # Multiple subsequent status polls must stay ON / BLOCKED
        for _ in range(3):
            polled = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
            self.assertTrue(polled["requestedRestrictionEnabled"])
            self.assertTrue(polled["effectiveRestrictionEnabled"])
            self.assertFalse(polled["effectiveAccessAllowed"])
            self.assertEqual(polled["restriction"], "ON")
            self.assertEqual(polled["access"], "BLOCKED")

        # Browser controller must block YouTube
        block_eval = self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        self.assertIsNotNone(block_eval)
        self.assertEqual(block_eval["action"], "CLOSE_TAB")

    def test_04_instagram_toggle_off_and_on_independently_of_youtube(self):
        """
        Verifies Instagram toggle OFF and ON updates Instagram browser enforcement
        without disturbing YouTube's restriction state.
        """
        # Ensure YouTube is ON
        self.db.toggle_sidebar_restriction("youtube", enabled=True, restriction_controller=self.yt_mgr)

        # Toggle Instagram OFF
        res_ig_off = self.db.toggle_sidebar_restriction(
            "instagram",
            enabled=False,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(res_ig_off["success"])
        self.assertFalse(res_ig_off["requestedRestrictionEnabled"])
        self.assertTrue(res_ig_off["effectiveAccessAllowed"])
        self.assertEqual(res_ig_off["restriction"], "OFF")
        self.assertEqual(res_ig_off["access"], "ALLOWED")

        # Instagram browser tab & desktop app are allowed
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("Instagram - Google Chrome", "chrome.exe")
        )
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("Instagram", "instagram.exe")
        )

        # YouTube remains ON / BLOCKED
        yt_state = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(yt_state["restriction"], "ON")
        self.assertEqual(yt_state["access"], "BLOCKED")
        self.assertIsNotNone(
            self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        )

        # Toggle Instagram back ON
        res_ig_on = self.db.toggle_sidebar_restriction(
            "instagram",
            enabled=True,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(res_ig_on["success"])
        self.assertTrue(res_ig_on["requestedRestrictionEnabled"])
        self.assertFalse(res_ig_on["effectiveAccessAllowed"])
        self.assertEqual(res_ig_on["restriction"], "ON")
        self.assertEqual(res_ig_on["access"], "BLOCKED")

        # Instagram browser tab is blocked again
        ig_eval = self.yt_mgr._evaluate_window_restriction("Instagram - Google Chrome", "chrome.exe")
        self.assertIsNotNone(ig_eval)
        self.assertEqual(ig_eval["action"], "CLOSE_TAB")
        self.assertEqual(ig_eval["target_app"], "Instagram")

    def test_05_failure_scenario_enforcement_controller_unavailable(self):
        """
        When the browser enforcement controller fails to apply the rule,
        enforcementStatus must be 'FAILED' and diagnostics must record the failure reason.
        Retrying when controller recovers transitions enforcementStatus to 'APPLIED'.
        """
        self.yt_mgr.simulated_unavailable = True
        res_fail = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        self.assertEqual(res_fail["enforcementStatus"], "FAILED")
        self.assertIn("browser restriction controller", res_fail["controllerResponse"].get("error", "").lower())

        diag = self.db.get_restriction_diagnostics(limit=1)[0]
        self.assertEqual(diag["service"], "youtube")
        self.assertTrue(len(diag["failureReason"]) > 0)

        # Recover controller and retry
        self.yt_mgr.simulated_unavailable = False
        res_retry = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        self.assertEqual(res_retry["enforcementStatus"], "APPLIED")
        self.assertTrue(res_retry["effectiveAccessAllowed"])

    def test_06_failure_scenario_outdated_sync_event_rejected(self):
        """
        Outdated Android/Windows synchronization events with older policyVersion
        must be rejected and must NOT overwrite a newer local toggle state.
        """
        # Toggle YouTube OFF (advances policyVersion)
        res_off = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            restriction_controller=self.yt_mgr,
        )
        current_version = res_off["policyVersion"]
        self.assertEqual(res_off["restriction"], "OFF")
        self.assertEqual(res_off["access"], "ALLOWED")

        # Simulate stale sync payload arriving with older version trying to re-enable restriction
        stale_sync = self.db.reconcile_synced_restriction_policy(
            service="youtube",
            requested_restriction_enabled=True,
            incoming_version=current_version - 1,
            incoming_timestamp="2020-01-01T00:00:00",
            source="android_sync",
            restriction_controller=self.yt_mgr,
        )
        self.assertFalse(stale_sync["applied"])
        self.assertTrue(stale_sync["stale_rejected"])

        # YouTube must still be OFF / ALLOWED
        yt_after_stale = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(yt_after_stale["restriction"], "OFF")
        self.assertEqual(yt_after_stale["access"], "ALLOWED")

        # Now simulate a genuinely newer sync event with version = current_version + 1
        fresh_sync = self.db.reconcile_synced_restriction_policy(
            service="youtube",
            requested_restriction_enabled=True,
            incoming_version=current_version + 1,
            incoming_timestamp=datetime.now().isoformat(),
            source="android_sync",
            restriction_controller=self.yt_mgr,
        )
        self.assertTrue(fresh_sync["applied"])
        self.assertFalse(fresh_sync["stale_rejected"])
        self.assertEqual(fresh_sync["state"]["restriction"], "ON")
        self.assertEqual(fresh_sync["state"]["access"], "BLOCKED")

    def test_07_device_app_controls_sync_with_sidebar_toggles(self):
        """
        Toggling via Device App Controls (toggle_device_app_lock) and via Sidebar Toggles
        must stay 100% consistent without divergence.
        """
        # Unlock YouTube via device app controls
        self.db.toggle_device_app_lock("youtube", locked=False)
        yt_stat = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(yt_stat["restriction"], "OFF")
        self.assertEqual(yt_stat["access"], "ALLOWED")
        self.assertIsNone(
            self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        )

        # Lock YouTube via device app controls
        self.db.toggle_device_app_lock("youtube", locked=True)
        yt_stat2 = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(yt_stat2["restriction"], "ON")
        self.assertEqual(yt_stat2["access"], "BLOCKED")
        self.assertIsNotNone(
            self.yt_mgr._evaluate_window_restriction("YouTube - Google Chrome", "chrome.exe")
        )


if __name__ == "__main__":
    unittest.main()
