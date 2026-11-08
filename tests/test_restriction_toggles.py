"""
Unit and Integration Tests for StrictRecords YouTube & Instagram Restriction Toggles.
Verifies:
1. Strict separation between Restriction Setting (ON/OFF) and Effective Access (ALLOWED/BLOCKED).
2. Smooth visual state transitions and persistence across restarts and dashboard navigation.
3. Score < 80: Toggling OFF sets restriction to OFF, while access remains BLOCKED with score explanation.
4. Score >= 80: Toggling OFF sets restriction to OFF, and access transitions to ALLOWED.
5. Strict Session lock prevents toggling unless authorized by admin pin.
6. HTML/CSS specification conformance (.restriction-toggle 44x24px, 180ms transitions, data-state).
"""

import os
import sys
import unittest
import tempfile
import json
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.server import EventBus


class SuccessfulTestPolicyController:
    def apply_service_policy(self, service, effective_restriction_enabled, effective_access_allowed, reason=None):
        return {
            "controller": "TestPolicyController",
            "service": service,
            "enforcementStatus": "APPLIED",
            "effectiveRestrictionEnabled": effective_restriction_enabled,
            "effectiveAccessAllowed": effective_access_allowed,
            "ruleActive": effective_restriction_enabled,
            "urlBlocked": effective_restriction_enabled,
            "verified": True,
            "error": None,
        }

    def clear_caches(self):
        return None

    def check_and_enforce(self):
        return {}


class TestRestrictionToggles(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.db.restriction_mgr = SuccessfulTestPolicyController()
        self.db._notify_android_app_locks_adb = lambda *_args, **_kwargs: {
            "status": "PENDING_CONFIRMATION", "confirmed": False
        }
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(self.today)

    def tearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_sidebar_restrictions_status_initial_state(self):
        """Checks schema and default values of get_sidebar_restrictions_status."""
        stat = self.db.get_sidebar_restrictions_status()
        self.assertTrue(stat["success"])
        self.assertIn("youtube", stat["status"])
        self.assertIn("instagram", stat["status"])

        yt = stat["status"]["youtube"]
        self.assertEqual(yt["target"], "youtube")
        self.assertEqual(yt["app_name"], "YouTube")
        self.assertEqual(yt["restriction"], "ON")
        self.assertEqual(yt["access"], "UNKNOWN")
        self.assertIsNone(yt["effectiveAccessAllowed"])
        self.assertEqual(yt["enforcementStatus"], "PENDING")
        self.assertTrue(yt["can_toggle"])
        self.assertEqual(yt["disabled_reason"], "")

        ig = stat["status"]["instagram"]
        self.assertEqual(ig["target"], "instagram")
        self.assertEqual(ig["app_name"], "Instagram")
        self.assertEqual(ig["restriction"], "ON")
        self.assertEqual(ig["access"], "UNKNOWN")
        self.assertIsNone(ig["effectiveAccessAllowed"])

    def test_toggle_off_when_score_low_sets_off_with_blocked_access(self):
        """
        When score is 0 or < 80 and mandatory privilege gate is active:
        Switching YouTube or Instagram OFF must:
        - Return success: True
        - Set restriction to 'OFF'
        - Keep effective access 'BLOCKED'
        - Explain why access is blocked in reason
        - Persist preference in focus_settings
        - Prevent unauthorized daily unlock
        """
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 0, wakeup_status = 'PENDING' WHERE date = ?",
                (self.today,),
            )

        # Toggle YouTube OFF under mandatory privilege gate
        res_yt = self.db.toggle_sidebar_restriction(
            "youtube", enabled=False, requested_by="user", enforce_privilege_gate=True
        )
        self.assertTrue(res_yt["success"])
        self.assertEqual(res_yt["restriction"], "OFF")
        self.assertEqual(res_yt["access"], "BLOCKED")
        self.assertIn("below", res_yt["reason"].lower())

        # Verify daily unlock was NOT granted
        self.assertFalse(self.db.is_youtube_unlocked_for_today())

        # Verify focus settings persisted user preference as OFF
        fcfg = self.db.get_focus_settings()
        prefs = fcfg.get("user_restriction_preferences", {})
        self.assertFalse(prefs.get("youtube"))

        # Subsequent status query must NOT snap back to ON
        stat2 = self.db.get_sidebar_restrictions_status()
        self.assertEqual(stat2["status"]["youtube"]["restriction"], "OFF")
        self.assertEqual(stat2["status"]["youtube"]["access"], "BLOCKED")

        # Toggle Instagram OFF under mandatory privilege gate
        res_ig = self.db.toggle_sidebar_restriction(
            "instagram", enabled=False, requested_by="user", enforce_privilege_gate=True
        )
        self.assertTrue(res_ig["success"])
        self.assertEqual(res_ig["restriction"], "OFF")
        self.assertEqual(res_ig["access"], "BLOCKED")

        stat3 = self.db.get_sidebar_restrictions_status()
        self.assertEqual(stat3["status"]["instagram"]["restriction"], "OFF")
        self.assertEqual(stat3["status"]["instagram"]["access"], "BLOCKED")

    def test_toggle_back_on_re_enables_restriction(self):
        """Turning the switch back ON updates restriction to ON and keeps access BLOCKED."""
        # First turn OFF
        self.db.toggle_sidebar_restriction("youtube", enabled=False)

        # Now turn ON
        res_on = self.db.toggle_sidebar_restriction("youtube", enabled=True, requested_by="user")
        self.assertTrue(res_on["success"])
        self.assertEqual(res_on["restriction"], "ON")
        self.assertEqual(res_on["access"], "BLOCKED")

        fcfg = self.db.get_focus_settings()
        self.assertTrue(fcfg.get("user_restriction_preferences", {}).get("youtube"))

        stat = self.db.get_sidebar_restrictions_status()
        self.assertEqual(stat["status"]["youtube"]["restriction"], "ON")

    def test_toggle_off_when_score_high_grants_allowed_access(self):
        """
        When score >= 80 and wakeup verified:
        Toggling OFF grants effective access ALLOWED.
        """
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 88, wakeup_status = 'VERIFIED' WHERE date = ?",
                (self.today,),
            )
            conn.execute(
                "UPDATE scheduled_activities SET status = 'VERIFIED' WHERE date = ? AND type = 'HOURLY'",
                (self.today,),
            )

        res = self.db.toggle_sidebar_restriction("youtube", enabled=False, requested_by="user")
        self.assertTrue(res["success"])
        self.assertEqual(res["restriction"], "OFF")
        self.assertEqual(res["access"], "ALLOWED")
        self.assertIn("Discipline verified", res["reason"])

        # Also unlocks YouTube for today
        self.assertTrue(self.db.is_youtube_unlocked_for_today())

        stat = self.db.get_sidebar_restrictions_status()
        self.assertEqual(stat["status"]["youtube"]["restriction"], "OFF")
        self.assertEqual(stat["status"]["youtube"]["access"], "ALLOWED")

    def test_strict_session_prevents_toggle_without_admin_pin(self):
        """Active strict session disables toggles and rejects user toggle requests."""
        now_iso = datetime.now().isoformat()
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO strict_sessions (session_id, user_id, start_time, state) VALUES (?, ?, ?, ?)",
                ("strict_sess_test", "user_lok", now_iso, "ACTIVE"),
            )

        # Status should show can_toggle is False with explanatory reason
        stat = self.db.get_sidebar_restrictions_status()
        self.assertFalse(stat["status"]["youtube"]["can_toggle"])
        self.assertIn("active Strict Session", stat["status"]["youtube"]["disabled_reason"])

        # Attempting to toggle without admin pin must fail
        res = self.db.toggle_sidebar_restriction("youtube", enabled=False, requested_by="user")
        self.assertFalse(res["success"])
        self.assertIn("Cannot change restriction during an active Strict Session", res["error"])

        # A configured administrator PIN does not override an active Strict Session.
        self.assertTrue(self.db.change_admin_pin("1234", "2468")["success"])
        res_admin = self.db.toggle_sidebar_restriction("youtube", enabled=False, requested_by="admin", admin_pin="2468")
        self.assertFalse(res_admin["success"])
        self.assertIn("active Strict Session", res_admin["error"])
        self.assertTrue(self.db.get_sidebar_restrictions_status()["status"]["youtube"]["requestedRestrictionEnabled"])

    def test_dashboard_ui_markup_and_css_specs(self):
        """Verifies ui_dashboard.py contains the required CSS rules, data-states, and keyboard handlers."""
        dash_path = os.path.join(ROOT_DIR, "core", "ui_dashboard.py")
        with open(dash_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Dedicated CSS rules
        self.assertIn(".restriction-toggle", content)
        self.assertIn(".restriction-toggle[data-state=\"on\"]", content)
        self.assertIn(".restriction-toggle[data-state=\"off\"]", content)
        self.assertIn(".restriction-toggle__thumb", content)
        self.assertIn("width: 44px;", content)
        self.assertIn("height: 24px;", content)
        self.assertIn("background: #292a31;", content)
        self.assertIn("background: #8b5cf6;", content)
        self.assertIn("translateX(20px)", content)
        self.assertIn("180ms ease", content)

        # 2. HTML elements & ARIA
        self.assertIn('id="sidebarYtToggleLabel"', content)
        self.assertIn('id="sidebarIgToggleLabel"', content)
        self.assertIn('role="switch"', content)
        self.assertIn('aria-checked="true"', content)
        self.assertIn('handleRestrictionKeydown(event, \'youtube\')', content)
        self.assertIn('handleRestrictionKeydown(event, \'instagram\')', content)

        # 3. JS Controller functions
        self.assertIn("function updateSidebarRestrictionsUI", content)
        self.assertIn("async function toggleSidebarRestriction", content)
        self.assertIn("isTogglingRestriction", content)
        self.assertIn("restrictionUnlockPinModal", content)
        self.assertIn("promptRestrictionUnlock", content)

    def test_toggle_off_with_admin_pin_1234_immediately_unlocks(self):
        """
        When score is 0 and mandatory privilege lock is active:
        Toggling OFF with Admin PIN '1234' must immediately override the restriction:
        - Return success: True
        - restriction: 'OFF'
        - access: 'ALLOWED'
        - policyDecision: 'ALLOW'
        - is_youtube_unlocked_for_today(): True
        - is_instagram_unlocked_for_today(): True
        - Toggling ON immediately re-blocks access.
        """
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 0, wakeup_status = 'PENDING' WHERE date = ?",
                (self.today,),
            )

        # 1. YouTube toggle OFF with Admin PIN 1234
        res_yt = self.db.toggle_sidebar_restriction(
            "youtube",
            enabled=False,
            requested_by="admin_override",
            admin_pin="1234",
            enforce_privilege_gate=True,
        )
        self.assertTrue(res_yt["success"])
        self.assertEqual(res_yt["restriction"], "OFF")
        self.assertEqual(res_yt["access"], "ALLOWED")
        self.assertEqual(res_yt["status"]["youtube"]["policyDecision"], "ALLOW")
        self.assertTrue(self.db.is_youtube_unlocked_for_today())

        # 2. Instagram toggle OFF with Admin PIN 1234
        res_ig = self.db.toggle_sidebar_restriction(
            "instagram",
            enabled=False,
            requested_by="admin_override",
            admin_pin="1234",
            enforce_privilege_gate=True,
        )
        self.assertTrue(res_ig["success"])
        self.assertEqual(res_ig["restriction"], "OFF")
        self.assertEqual(res_ig["access"], "ALLOWED")
        self.assertEqual(res_ig["status"]["instagram"]["policyDecision"], "ALLOW")
        self.assertTrue(self.db.is_instagram_unlocked_for_today())

        # 3. Toggle back ON -> immediately blocks in real time
        res_yt_on = self.db.toggle_sidebar_restriction("youtube", enabled=True, requested_by="user")
        self.assertTrue(res_yt_on["success"])
        self.assertEqual(res_yt_on["restriction"], "ON")
        self.assertEqual(res_yt_on["access"], "BLOCKED")
        self.assertFalse(self.db.is_youtube_unlocked_for_today())

        res_ig_on = self.db.toggle_sidebar_restriction("instagram", enabled=True, requested_by="user")
        self.assertTrue(res_ig_on["success"])
        self.assertEqual(res_ig_on["restriction"], "ON")
        self.assertEqual(res_ig_on["access"], "BLOCKED")
        self.assertFalse(self.db.is_instagram_unlocked_for_today())


if __name__ == "__main__":
    unittest.main()

