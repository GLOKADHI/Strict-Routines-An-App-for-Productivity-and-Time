"""Live restriction reconciliation tests using a fake browser policy controller."""

import os
import tempfile
import unittest
from datetime import datetime

from core.database import StrictRecordsDB
from windows.restriction_manager import YouTubeRestrictionManager


class FakeBrowserPolicyController:
    def __init__(self):
        self.access_allowed = {"youtube": False, "instagram": False}
        self.fail_next = False

    def apply_service_policy(self, service, restriction_enabled):
        if self.fail_next:
            self.fail_next = False
            return {
                "controller": "FakeBrowserPolicyController",
                "service": service,
                "enforcementStatus": "FAILED",
                "verified": False,
                "error": "simulated policy write failure",
            }
        allowed = not bool(restriction_enabled)
        self.access_allowed[service] = allowed
        return {
            "controller": "FakeBrowserPolicyController",
            "service": service,
            "enforcementStatus": "APPLIED",
            "effectiveRestrictionEnabled": not allowed,
            "effectiveAccessAllowed": allowed,
            "verified": True,
        }


class RestrictionLivePolicyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="restriction_reconcile_")
        self.db = StrictRecordsDB(os.path.join(self.temp_dir.name, "test.db"))
        self.db._notify_android_app_locks_adb = lambda *_args, **_kwargs: {
            "status": "PENDING_CONFIRMATION",
            "confirmed": False,
        }
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)
        self.policy_controller = FakeBrowserPolicyController()
        self.manager = YouTubeRestrictionManager(db=self.db)
        self.manager.browser_policy_controller = self.policy_controller
        self._check_and_enforce = self.manager.check_and_enforce
        # Restriction toggle normally refreshes the active-window watcher. Keep this
        # regression test on the injected policy adapter and away from OS window APIs.
        self.manager.check_and_enforce = lambda: {}

    def tearDown(self):
        self.temp_dir.cleanup()

    def _grant_score_gated_access(self):
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 88, wakeup_status = 'VERIFIED' WHERE date = ?",
                (self.today,),
            )
            conn.execute(
                "UPDATE scheduled_activities SET status = 'VERIFIED' WHERE date = ? AND type = 'HOURLY'",
                (self.today,),
            )
        focus = self.db.get_focus_settings()
        focus["mandatory_privilege_lock_youtube"] = True
        self.db.save_focus_settings(focus)
        result = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="user",
            enforce_privilege_gate=True,
            restriction_controller=self.manager,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["effectiveAccessAllowed"])
        self.assertTrue(self.policy_controller.access_allowed["youtube"])

    def test_score_drop_reapplies_block_during_the_running_session(self):
        self._grant_score_gated_access()
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 0, wakeup_status = 'PENDING' WHERE date = ?",
                (self.today,),
            )

        # Exercise the real watcher cycle while keeping window enumeration isolated.
        self.manager.find_restricted_windows = lambda: []
        self.manager.check_and_enforce = self._check_and_enforce
        self.manager.check_and_enforce()
        self.assertFalse(self.policy_controller.access_allowed["youtube"])
        status = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(status["enforcementStatus"], "APPLIED")
        self.assertFalse(status["effectiveAccessAllowed"])
        self.assertFalse(status["desiredEffectiveAccessAllowed"])

    def test_failed_controller_still_uses_denied_authorization_in_window_guard(self):
        self._grant_score_gated_access()
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 0, wakeup_status = 'PENDING' WHERE date = ?",
                (self.today,),
            )
        self.policy_controller.fail_next = True

        result = self.manager.reconcile_browser_policies_if_needed()
        self.assertEqual(result["services"]["youtube"]["enforcementStatus"], "FAILED")
        self.assertTrue(self.policy_controller.access_allowed["youtube"])
        status = self.db.get_sidebar_restrictions_status()["status"]["youtube"]
        self.assertEqual(status["enforcementStatus"], "FAILED")
        self.assertFalse(status["desiredEffectiveAccessAllowed"])

        decision = self.manager._evaluate_window_restriction(
            "YouTube - Google Chrome",
            "chrome.exe",
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "CLOSE_TAB")


if __name__ == "__main__":
    unittest.main()
