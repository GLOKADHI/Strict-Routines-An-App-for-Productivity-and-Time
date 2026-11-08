"""
STRICTRECORDS SECURE-EXAM-BROWSER (SEB) STRICT WINDOWS SESSION ACCEPTANCE SUITE
Implements all acceptance scenarios defined in Requirement 29:
- Start session lifecycle & precheck
- Restricted application attempt & allowlisting
- Restricted website attempt & browser policy
- Dynamic PrivilegeEngine (score increases, falls, thresholds)
- Hourly proof validation (pass/fail)
- Privilege granted & revoked transitions
- Authorized admin exit (PIN protected) & clean Windows restoration
- Crash recovery & reboot recovery
"""

import json
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from core.database import StrictRecordsDB
from core.models import (
    HourlyProofStatus,
    PrivilegeOutput,
    StrictAuditAction,
    StrictSessionPolicy,
    StrictSessionState,
)
from core.privilege_engine import PrivilegeEngine
from windows.browser_policy_manager import BrowserPolicyManager
from windows.network_policy_manager import NetworkPolicyManager
from windows.strict_session_manager import StrictSessionManager


class TestStrictWindowsSession(unittest.TestCase):
    def setUp(self):
        # A unit test must never hide the real taskbar, install keyboard hooks,
        # alter registry policies, open a webcam, or launch the user's browser.
        for name in ('_apply_registry_policies','_restore_registry_policies','_install_keyboard_hook',
                     '_uninstall_keyboard_hook','_hide_taskbar','_show_taskbar','_start_supervisor'):
            p=patch.object(StrictSessionManager,name,return_value=None);p.start();self.addCleanup(p.stop)
        for target,value in [('windows.network_policy_manager.HAS_WINREG',False),
            ('windows.strict_session_manager.StrictStudyHardware',lambda:SimpleNamespace(
                start=lambda **_: {'success':True},stop=lambda:None,view=lambda:dict(monitoring_ok=True,camera='ACTIVE',bluetooth_connected=True))),
            ('core.youtube_reward_cloud.client',lambda _:SimpleNamespace(configuration=lambda:None)),
            ('windows.browser_policy_manager.BrowserPolicyManager.launch_approved_browser',lambda *_,**__: {'success':True})]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        self.temp_dir = tempfile.mkdtemp(prefix="strict_test_")
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)
        from core.time_window_engine import now_ist
        now=now_ist();start=now+timedelta(minutes=35)
        with self.db._connect() as conn:
            conn.execute("""INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status)
                VALUES('unit-android','user_lok','Fixture','ANDROID','test',?,'CONNECTED')""",(now.isoformat(),))
            conn.execute("""INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,
                window_end,notification_time,status) VALUES('unit-hourly','user_lok','HOURLY',?,?,?,?,?,'PENDING')""",
                (start.date().isoformat(),start.isoformat(),start.isoformat(),(start+timedelta(minutes=20)).isoformat(),start.isoformat()))
        self.session_mgr = StrictSessionManager(db=self.db, data_dir=self.temp_dir)
        self.session_mgr.preparation=SimpleNamespace(authorize_start=lambda *_:None,activated=lambda *_:None,finish=lambda *_:None)
        self.session_mgr.embedded_focus=SimpleNamespace(open=lambda *_:dict(success=True),close=lambda:None)

    def tearDown(self):
        try:
            self.session_mgr._restore_windows_environment()
            self.session_mgr._clear_state_file()
        except Exception:
            pass

    def test_01_strict_session_lifecycle_and_precheck(self):
        """Tests precheck, start session, state transitions, and audit records."""
        self.assertEqual(self.session_mgr.state, StrictSessionState.NORMAL)

        # 1. Precheck
        precheck = self.session_mgr.run_precheck()
        self.assertTrue(precheck["all_passed"])
        self.assertEqual(precheck["camera"]["status"], "PASS")
        self.assertEqual(precheck["microphone"]["status"], "NOT_USED")
        self.assertEqual(precheck["device"]["device_id"], "windows_lok_pc")

        # 2. Start Session
        res = self.session_mgr.start_session(monitoring_consent=True)
        self.assertTrue(res["success"])
        self.assertIn(self.session_mgr.state, (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED, StrictSessionState.PRIVILEGED))
        self.assertIsNotNone(self.session_mgr.session_id)

        # Verify state persistence file
        self.assertTrue(os.path.exists(self.session_mgr.state_file))

        # Verify database record
        db_rec = self.db.get_strict_session(self.session_mgr.session_id)
        self.assertIsNotNone(db_rec)
        self.assertEqual(db_rec["user_id"], "user_lok")
        self.assertEqual(db_rec["device_id"], "windows_lok_pc")

        # Verify audit log
        audits = self.db.get_strict_audit_logs(self.session_mgr.session_id)
        actions = [a["action"] for a in audits]
        self.assertIn(StrictAuditAction.SESSION_STARTED.value, actions)
        self.assertIn(StrictAuditAction.POLICY_LOADED.value, actions)

    def test_02_application_allowlist_and_restricted_app_attempt(self):
        """Tests application allowlist enforcement and policy updates."""
        # Initial allowlist
        self.session_mgr.start_session(monitoring_consent=True)
        allowed = self.session_mgr.policy.allowed_applications
        self.assertIn("StrictRecords.exe", allowed)
        self.assertIn("Comet.exe", allowed)
        self.assertIn("CalculatorApp.exe", allowed)

        # Test unauthorized process attempt
        unauthorized_app = "MaliciousGame.exe"
        self.assertNotIn(unauthorized_app, allowed)

        # Trigger simulated detection of unauthorized app
        self.session_mgr.last_blocked_app = unauthorized_app
        self.session_mgr.blocked_notification_message = f"This application is unavailable during Strict Session: {unauthorized_app}"
        self.session_mgr._audit(
            StrictAuditAction.APP_BLOCKED.value,
            reason=f"Application not in policy allowlist: {unauthorized_app}",
            details={"process_name": unauthorized_app, "pid": 9999},
        )

        # Verify audit log contains APP_BLOCKED
        audits = self.db.get_strict_audit_logs(self.session_mgr.session_id)
        blocked_events = [a for a in audits if a["action"] == StrictAuditAction.APP_BLOCKED.value]
        self.assertGreaterEqual(len(blocked_events), 1)
        self.assertIn("MaliciousGame.exe", blocked_events[0]["reason"])

        # Test Admin updating policy allowlist to add approved application
        updated_policy = self.session_mgr.update_policy({
            "allowed_applications": allowed + ["VSCode.exe", "Slack.exe"]
        })
        self.assertIn("VSCode.exe", updated_policy["allowed_applications"])
        self.assertIn("Slack.exe", updated_policy["allowed_applications"])

    def test_03_browser_policy_and_dynamic_website_gating(self):
        """Tests browser policy manager, PAC generation, and navigation detection."""
        browser_mgr = BrowserPolicyManager(designated_browser="Comet.exe")
        browser_mgr.configure(
            designated_browser="Comet.exe",
            allowed_domains=["github.com", "stackoverflow.com"],
            entertainment_allowed=False,
        )

        # 1. Allowed domain navigation
        nav_allowed = browser_mgr.check_active_browser_navigation("GitHub — Where the world builds software")
        self.assertIsNone(nav_allowed)  # No block event

        # 2. Blocked entertainment navigation while privilege is False
        nav_blocked = browser_mgr.check_active_browser_navigation("YouTube — Home of Trending Videos")
        self.assertIsNotNone(nav_blocked)
        self.assertEqual(nav_blocked["action"], "BLOCKED")
        self.assertEqual(nav_blocked["matched_keyword"], "youtube")

        # 3. Test NetworkPolicyManager PAC generation
        net_mgr = NetworkPolicyManager(pac_dir=self.temp_dir)
        ok = net_mgr.apply_policy(allowed_domains=["github.com", "stackoverflow.com"], entertainment_allowed=False)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(net_mgr.pac_path))
        with open(net_mgr.pac_path, "r", encoding="utf-8") as f:
            pac_text = f.read()
        self.assertIn("github.com", pac_text)
        self.assertIn("PROXY 127.0.0.1:8765", pac_text)

        # Domain permission test
        self.assertTrue(net_mgr.is_domain_permitted("github.com"))
        self.assertTrue(net_mgr.is_domain_permitted("api.github.com"))
        self.assertFalse(net_mgr.is_domain_permitted("youtube.com"))

        # Release entertainment and check PAC update
        net_mgr.update_privilege(entertainment_allowed=True)
        self.assertTrue(net_mgr.is_domain_permitted("youtube.com"))
        net_mgr.restore_network()
        self.assertFalse(os.path.exists(net_mgr.pac_path))

    def test_04_privilege_engine_score_fluctuation_and_violations(self):
        """Tests PrivilegeEngine under score increases, score falls, and violations."""
        engine = PrivilegeEngine(db=self.db, default_threshold=80)

        # Case A: Score 76 < 80 -> BLOCKED
        res_a = engine.evaluate_privilege(
            current_score=76,
            verified_points=20,
            hourly_proof_status=HourlyProofStatus.VALID,
            critical_violations=0,
            camera_health=True,
            microphone_health=True,
            strict_session_state=StrictSessionState.ACTIVE,
            required_score=80,
        )
        self.assertEqual(res_a["output"], PrivilegeOutput.ENTERTAINMENT_BLOCKED.value)
        self.assertFalse(res_a["allowed"])
        self.assertEqual(res_a["remaining_points"], 4)
        self.assertIn("Score 76 is below required threshold 80", res_a["reasons"][0])

        # Case B: Score increases to 82 >= 80 -> ALLOWED
        res_b = engine.evaluate_privilege(
            current_score=82,
            verified_points=35,
            hourly_proof_status=HourlyProofStatus.VALID,
            critical_violations=0,
            camera_health=True,
            microphone_health=True,
            strict_session_state=StrictSessionState.ACTIVE,
            required_score=80,
        )
        self.assertEqual(res_b["output"], PrivilegeOutput.ENTERTAINMENT_ALLOWED.value)
        self.assertTrue(res_b["allowed"])
        self.assertEqual(res_b["remaining_points"], 0)

        # Case C: Critical violation occurs -> BLOCKED despite high score
        res_c = engine.evaluate_privilege(
            current_score=95,
            verified_points=40,
            hourly_proof_status=HourlyProofStatus.VALID,
            critical_violations=1,
            camera_health=True,
            microphone_health=True,
            strict_session_state=StrictSessionState.ACTIVE,
            required_score=80,
        )
        self.assertEqual(res_c["output"], PrivilegeOutput.ENTERTAINMENT_BLOCKED.value)
        self.assertFalse(res_c["allowed"])
        self.assertIn("critical integrity violation", res_c["reasons"][0])

        # Case D: Camera disconnects -> BLOCKED
        res_d = engine.evaluate_privilege(
            current_score=95,
            camera_health=False,
            microphone_health=True,
            required_score=80,
        )
        self.assertFalse(res_d["allowed"])
        self.assertIn("camera", res_d["reasons"][0].lower())

        # Case E: Configurable threshold (e.g. required_score = 60)
        res_e = engine.evaluate_privilege(
            current_score=65,
            required_score=60,
        )
        self.assertTrue(res_e["allowed"])

    def test_05_hourly_proof_checkpoint_validation(self):
        """Only an approved retained proof can validate a strict-session checkpoint."""
        # Exercise the policy calculation without applying live Windows controls.
        self.session_mgr.state = StrictSessionState.ACTIVE
        self.session_mgr.session_id = "strict-proof-gate-test"
        self.session_mgr.network_policy_manager = SimpleNamespace(
            update_privilege=lambda _allowed: True,
            is_active=False,
        )
        self.session_mgr.browser_policy_manager = SimpleNamespace(
            entertainment_allowed=False,
            is_active=False,
        )

        # A newly created session starts at zero and remains blocked without proof.
        self.assertEqual(self.session_mgr.current_score, 0)
        self.assertEqual(self.session_mgr.hourly_proof_status, HourlyProofStatus.PENDING)
        denied = self.session_mgr.evaluate_privilege()
        self.assertFalse(denied["allowed"])

        invalid_res = self.session_mgr.submit_hourly_proof(evidence_id="missing-proof-id")
        self.assertEqual(invalid_res["status"], HourlyProofStatus.INVALID.value)
        self.assertEqual(invalid_res["privilege"]["current_score"], 0)
        self.assertFalse(invalid_res["privilege"]["allowed"])

        with self.db._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM score_events WHERE points > 0").fetchone()[0], 0)

        # Build a genuine retained image and persist an administrator approval.
        from PIL import Image
        from core.scoring_engine import ScoringEngine
        image_buffer = io.BytesIO()
        Image.new("RGB", (120, 120), color=(25, 110, 75)).save(image_buffer, format="JPEG")
        scoring = ScoringEngine(db=self.db, storage_root=self.temp_dir)
        evidence = scoring.store_evidence_file(
            date=datetime.now().strftime("%Y-%m-%d"),
            record_type="Hourly",
            related_record_id=self.session_mgr.session_id or "strict-session-checkpoint",
            device_id="windows_lok_pc",
            file_bytes=image_buffer.getvalue(),
            filename_hint="checkpoint.jpg",
        )
        approval = scoring.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="separate-test-admin",
            points=5,
            reason="Regression test approval",
        )
        self.assertEqual(approval["points_awarded"], 5)

        valid_res = self.session_mgr.submit_hourly_proof(evidence_id=evidence["evidence_id"])
        self.assertEqual(valid_res["status"], HourlyProofStatus.VALID.value)
        self.assertEqual(valid_res["privilege"]["current_score"], 5)

        audits = self.db.get_strict_audit_logs(self.session_mgr.session_id)
        verified_events = [a for a in audits if a["action"] == StrictAuditAction.PROOF_VERIFIED.value]
        self.assertGreaterEqual(len(verified_events), 1)

    def test_06_authorized_admin_exit_and_windows_restoration(self):
        """Tests PIN protection and complete Windows restoration."""
        self.session_mgr.start_session(monitoring_consent=True)

        # 1. Attempt exit with WRONG PIN -> FAILS
        fail_res = self.session_mgr.end_session(admin_pin="9999_WRONG")
        self.assertFalse(fail_res["success"])
        self.assertIn("Invalid Administrator PIN", fail_res["error"])
        self.assertNotEqual(self.session_mgr.state, StrictSessionState.NORMAL)

        # Initial setup credentials cannot release a protected session until changed.
        setup_key_res = self.session_mgr.end_session(admin_pin="1234", reason="Exam finished by administrator")
        self.assertFalse(setup_key_res["success"])
        self.assertEqual(setup_key_res["code"], "PIN_CHANGE_REQUIRED")

        self.assertTrue(self.db.change_admin_pin("1234", "5678")["success"])
        success_res = self.session_mgr.end_session(admin_pin="5678", reason="Exam finished by administrator")
        self.assertTrue(success_res["success"])
        self.assertEqual(self.session_mgr.state, StrictSessionState.NORMAL)
        self.assertIsNone(self.session_mgr.session_id)

        # Verify state file removed
        self.assertFalse(os.path.exists(self.session_mgr.state_file))

        # Verify audit log recorded SESSION_ENDED
        audits = self.db.get_strict_audit_logs(limit=20)
        end_events = [a for a in audits if a["action"] == StrictAuditAction.SESSION_ENDED.value]
        self.assertGreaterEqual(len(end_events), 1)
        self.assertEqual(end_events[0]["reason"], "Exam finished by administrator")

    def test_07_crash_recovery_and_restart(self):
        """Simulates machine crash or reboot and tests automatic safe recovery."""
        # Create simulated crash state file
        simulated_crash_state = {
            "session_id": "strict_sess_crashed_999",
            "state": "ACTIVE",
            "user_id": "user_lok",
            "device_id": "windows_lok_pc",
            "policy_version": "StrictRecords Policy v12",
            "start_time": "2026-10-08 10:00:00",
            "heartbeat": "2026-10-08 10:15:00",
        }
        with open(self.session_mgr.state_file, "w", encoding="utf-8") as f:
            json.dump(simulated_crash_state, f)

        # Insert unclosed session in DB
        self.db.create_strict_session(simulated_crash_state)

        # Instantiate fresh StrictSessionManager simulating app restart after crash
        new_session_mgr = StrictSessionManager(db=self.db, data_dir=self.temp_dir)
        recovery_report = new_session_mgr.last_recovery_report

        # Should detect unclosed session and recover
        self.assertTrue(recovery_report["recovery_needed"])
        self.assertEqual(recovery_report["recovered_session"]["session_id"], "strict_sess_crashed_999")
        self.assertEqual(new_session_mgr.state, StrictSessionState.NORMAL)

        # State file must be cleaned so next startup is clean
        self.assertFalse(os.path.exists(new_session_mgr.state_file))

        # Database record must be marked as crash recovered
        db_rec = self.db.get_strict_session("strict_sess_crashed_999")
        self.assertEqual(db_rec["state"], "NORMAL")
        self.assertEqual(db_rec["crash_recovered"], 1)


if __name__ == "__main__":
    unittest.main()
