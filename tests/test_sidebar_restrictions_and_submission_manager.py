"""
Unit and Integration Tests for:
1. Left Sidebar YouTube and Instagram Toggles & Authoritative Policy Enforcement.
2. Single-Instance Submission Window Manager (Reuse, No Stacking, Instant Android Sync Dismissal).
3. Win32 Single-Instance Mutex & Process Protection.
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
from core.privilege_engine import PrivilegeEngine
from windows.submission_window_manager import SubmissionWindowManager, SubmissionState


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


class TestSidebarRestrictionsAndSubmissionManager(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = StrictRecordsDB(db_path=self.temp_db_path)
        self.db.restriction_mgr = SuccessfulTestPolicyController()
        self.db._notify_android_app_locks_adb = lambda *_args, **_kwargs: {
            "status": "PENDING_CONFIRMATION", "confirmed": False
        }
        self.event_bus = EventBus.get_instance()
        today = datetime.now().strftime("%Y-%m-%d")
        self.db.ensure_daily_record(today)

    def tearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_sidebar_restrictions_status_schema(self):
        """Verifies GET /api/restrictions/status returns authoritative data for YouTube and Instagram."""
        status = self.db.get_sidebar_restrictions_status()
        self.assertTrue(status["success"])
        self.assertIn("youtube", status["status"])
        self.assertIn("instagram", status["status"])

        yt = status["status"]["youtube"]
        self.assertEqual(yt["target"], "youtube")
        self.assertIn(yt["restriction"], ("ON", "OFF"))
        self.assertEqual(yt["access"], "UNKNOWN")
        self.assertIsNone(yt["effectiveAccessAllowed"])
        self.assertEqual(yt["admin_policy"], "UNKNOWN")
        self.assertTrue(len(yt["reason"]) > 0)

        ig = status["status"]["instagram"]
        self.assertEqual(ig["target"], "instagram")
        self.assertIn(ig["restriction"], ("ON", "OFF"))
        self.assertEqual(ig["access"], "UNKNOWN")
        self.assertIsNone(ig["effectiveAccessAllowed"])
        self.assertEqual(ig["admin_policy"], "UNKNOWN")

    def test_client_toggle_cannot_override_authoritative_restriction_when_score_low(self):
        """
        When score is below 80 and mandatory privilege gate is active, switching the toggle OFF
        updates restriction to 'OFF', while effective access remains authoritative 'BLOCKED'
        with score explanation. The switch itself MUST stay OFF and not be forced back to ON.
        """
        # Ensure fresh day with 45 points (< 80)
        today = datetime.now().strftime("%Y-%m-%d")
        with self.db._connect() as conn:
            conn.execute("UPDATE daily_records SET final_score = 45, wakeup_status = 'PENDING' WHERE date = ?", (today,))

        # 1. Turn OFF Instagram restriction under mandatory privilege gate
        res_ig = self.db.toggle_sidebar_restriction(
            "instagram", enabled=False, requested_by="user", enforce_privilege_gate=True
        )
        self.assertTrue(res_ig["success"])
        self.assertEqual(res_ig["restriction"], "OFF")
        self.assertEqual(res_ig["access"], "BLOCKED")
        self.assertIn("below", res_ig["reason"].lower())

        # Verify audit log recorded gated access
        logs = self.db.get_admin_audit_logs(limit=5)
        actions = [l["action_type"] for l in logs]
        self.assertIn("RESTRICTION_DISABLED_ACCESS_GATED", actions)

        # Subsequent status query MUST maintain restriction OFF and access BLOCKED
        status = self.db.get_sidebar_restrictions_status()
        self.assertEqual(status["status"]["instagram"]["restriction"], "OFF")
        self.assertEqual(status["status"]["instagram"]["access"], "BLOCKED")

        # 2. Turning ON sets restriction to ON and access BLOCKED
        res_enable = self.db.toggle_sidebar_restriction("instagram", enabled=True, requested_by="user")
        self.assertTrue(res_enable["success"])
        self.assertEqual(res_enable["restriction"], "ON")
        self.assertEqual(res_enable["access"], "BLOCKED")

        logs_after = self.db.get_admin_audit_logs(limit=5)
        actions_after = [l["action_type"] for l in logs_after]
        self.assertIn("RESTRICTION_ENABLED", actions_after)

    def test_restriction_disabled_legitimately_under_verified_discipline(self):
        """
        When score >= 80, wake-up is verified, and proofs are valid,
        the user can legitimately turn the restriction OFF (Access: ALLOWED).
        """
        today = datetime.now().strftime("%Y-%m-%d")
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 88, wakeup_status = 'VERIFIED' WHERE date = ?",
                (today,),
            )
            conn.execute(
                "UPDATE scheduled_activities SET status = 'VERIFIED' WHERE date = ? AND type = 'HOURLY'",
                (today,),
            )

        res = self.db.toggle_sidebar_restriction("instagram", enabled=False, requested_by="user")
        self.assertTrue(res["success"])
        self.assertEqual(res["restriction"], "OFF")
        self.assertEqual(res["access"], "ALLOWED")
        self.assertIn("Discipline verified", res["reason"])

        # Also test Admin Override via PIN 1984 works unconditionally
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE daily_records SET final_score = 20, wakeup_status = 'PENDING' WHERE date = ?",
                (today,),
            )
        res_admin = self.db.toggle_sidebar_restriction("youtube", enabled=False, requested_by="admin", admin_pin="1984")
        self.assertTrue(res_admin["success"])
        self.assertEqual(res_admin["restriction"], "OFF")
        self.assertEqual(res_admin["access"], "ALLOWED")

    def test_single_instance_submission_window_manager_workflow(self):
        """
        Tests SubmissionWindowManager:
        - Exactly one active submission experience.
        - Multiple notifications for same window reuse/focus existing window.
        - Android submission dismisses matching Windows form and transitions to RECEIVED.
        - Background AI analysis continues uninterrupted.
        """
        mgr = SubmissionWindowManager(db=self.db, event_bus=self.event_bus)

        # 1. Hourly window opens
        act_id = "ACT-TEST-SWM-WINDOW"
        op1 = mgr.open_submission_window(
            activity_id=act_id,
            slot="10:00-11:00",
            title="Hourly Discipline Check (10:00-11:00)",
            window_start="10:50",
            window_end="11:10",
        )
        self.assertEqual(op1["action"], "OPENED")
        self.assertEqual(op1["state"], SubmissionState.OPEN.value)

        # 2. Duplicate notification / timer trigger arrives for same activity
        op2 = mgr.open_submission_window(
            activity_id=act_id,
            slot="10:00-11:00",
            title="Hourly Discipline Check (10:00-11:00)",
        )
        self.assertEqual(op2["action"], "FOCUSED_EXISTING")
        self.assertTrue(op2["reused"])
        self.assertEqual(mgr.state, SubmissionState.OPEN)

        # 3. Android phone submits proof for this activity
        dismiss_res = mgr.dismiss_submission_form(
            activity_id=act_id,
            source_device_name="Lenovo Android Tablet",
            submitted_at="10:56 AM",
            has_evidence=True,
            description="Completed test suite for single instance",
            primary_activity="CODING",
        )
        self.assertEqual(dismiss_res["action"], "DISMISSED")
        self.assertEqual(dismiss_res["state"], SubmissionState.RECEIVED.value)
        self.assertTrue(dismiss_res["ai_review_active"])
        self.assertTrue(mgr.review_in_progress)

        # 4. Robotic AI completes multimodal review in background
        review_result = {
            "decision": "PROVISIONAL_APPROVE",
            "proposed_points": 8,
            "confidence": 0.95,
            "confidence_label": "HIGH",
            "signals": {"clarity": 120.5, "activity_alignment": "HIGH"},
        }
        mgr.record_ai_review_completed(activity_id=act_id, review_result=review_result)
        self.assertEqual(mgr.state, SubmissionState.REVIEWED)
        self.assertFalse(mgr.review_in_progress)
        self.assertIsNotNone(mgr.last_review_data)
        self.assertEqual(mgr.last_review_data["decision"], "PROVISIONAL_APPROVE")

    def test_single_instance_mutex_concept(self):
        """Verifies Named Mutex pattern works as intended on Windows."""
        if sys.platform != "win32":
            return
        import ctypes
        MUTEX_NAME = "Local\\StrictRecords_Test_Mutex"
        h1 = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.assertNotEqual(h1, 0)
        
        # Second call should indicate already exists (183)
        h2 = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        self.assertEqual(err, 183)

        ctypes.windll.kernel32.CloseHandle(h1)
        if h2:
            ctypes.windll.kernel32.CloseHandle(h2)


if __name__ == "__main__":
    unittest.main()
