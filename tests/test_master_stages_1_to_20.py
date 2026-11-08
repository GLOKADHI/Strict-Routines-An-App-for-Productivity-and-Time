"""
Comprehensive Automated Verification Suite for StrictRecords Master Specification Stages 1–20.
Tests Design System tokens, Safe Schema Migrations, Task Dependencies, Focus & Deep Work Sessions,
Break Logging, Motivation Track Pause/Resume, Deadline Warnings, 4-Tier Score States (ELITE_DAY),
Immutable ScoreEvent Corrections, Scoring Rule Versioning, Admin Role Protection & Audit Logs,
10-State Adaptive StrictBot, Structured RestrictionEvents, USB Pairing Trust Workflow,
Deterministic SyncConflict Resolution, Clock Tamper Signals, Reboot Recovery,
Analytics/Levels/XP/Achievements/Journal, Multi-Format Export (JSON/CSV/MD), and Self-Diagnostics.
"""

import os
import shutil
import tempfile
import unittest

from core.database import StrictRecordsDB
from core.models import DESIGN_SYSTEM_TOKENS, StrictBotState, TaskState, TaskType
from core.scoring_engine import ScoringEngine
from core.strictbot import StrictBot

VALID_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\xff\xd9"
)


class TestMasterStages1To20(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="strictrecords_master_")
        self.db_path = os.path.join(self.temp_dir, "master.db")
        self.storage_root = os.path.join(self.temp_dir, "STRICTRECORDS")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.date = "2026-10-06"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stage_01_02_design_tokens_migrations_and_task_dependencies(self):
        # 1. Design tokens (Sections 97-105)
        self.assertEqual(DESIGN_SYSTEM_TOKENS["dark"]["background_primary"], "#07090D")
        self.assertEqual(DESIGN_SYSTEM_TOKENS["light"]["background_primary"], "#F8FAFC")
        self.assertEqual(
            DESIGN_SYSTEM_TOKENS["navigation"]["mobile_tabs"],
            ["HOME", "TASKS", "TIMELINE", "REWARDS", "PROFILE"],
        )
        self.assertEqual(len(DESIGN_SYSTEM_TOKENS["navigation"]["admin_portal_sections"]), 14)

        # 2. Safe migration version check (Section 140)
        with self.db._connect() as conn:
            mig = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            self.assertGreaterEqual(mig["v"], 2)

        # 3. Task dependencies (Sections 5-8)
        dep_task = self.engine.create_custom_task(
            date=self.date,
            task_code="STUDY_DEEP",
            title="Morning Study Block",
            task_type=TaskType.SEQUENTIAL_TASK.value,
            dependencies=["WAKE_UP", "BATH"],
            min_duration_minutes=25,
            point_value=12,
        )
        self.assertEqual(dep_task["state"], TaskState.BLOCKED.value)
        self.assertEqual(dep_task["unmet_dependencies"], ["WAKE_UP", "BATH"])

        # Cannot complete while BLOCKED
        with self.assertRaises(ValueError):
            self.engine.complete_custom_task(self.date, "STUDY_DEEP", duration_minutes=30)

        # Complete WAKE_UP and BATH first
        self.engine.trigger_wakeup_alarm(self.date, timestamp="2026-10-06 07:00:00")
        self.engine.start_wakeup_task(self.date, timestamp="2026-10-06 07:00:05")
        from tests.test_daily_routine_gate import seed_approved_wakeup
        from tests.proof_fixtures import seed_approved_bath
        seed_approved_wakeup(self.db,self.engine,self.date)
        self.engine.finalize_wakeup_session(self.date,completed_at=self.date+' 07:30:00')
        seed_approved_bath(self.db,self.engine,self.date)

        # Dependency evaluation must now unlock STUDY_DEEP -> READY
        tasks_after = {t["task_code"]: t for t in self.engine.evaluate_task_dependencies(self.date)}
        self.assertEqual(tasks_after["STUDY_DEEP"]["state"], TaskState.READY.value)

        # Minimum duration check still enforced
        with self.assertRaises(ValueError):
            self.engine.complete_custom_task(self.date, "STUDY_DEEP", duration_minutes=10)

        # Valid completion succeeds
        comp = self.engine.complete_custom_task(self.date, "STUDY_DEEP", duration_minutes=30)
        self.assertEqual(comp["state"], TaskState.COMPLETED.value)
        self.assertEqual(comp["points_awarded"], 12)

    def test_stage_03_07_focus_deep_work_breaks_and_deadline_warnings(self):
        # Focus session (45m)
        f1 = self.engine.start_focus_session(self.date, title="Algorithm Practice", target_minutes=45)
        self.assertEqual(f1["session_type"], "FOCUS_SESSION")
        # Insufficient duration
        incomplete = self.engine.complete_focus_session(self.date, f1["focus_id"], actual_minutes=20)
        self.assertFalse(incomplete["completed"])
        # Full duration
        done_f1 = self.engine.complete_focus_session(self.date, f1["focus_id"], actual_minutes=45)
        self.assertTrue(done_f1["completed"])
        self.assertEqual(done_f1["points_awarded"], 10)

        # Deep work session (>=90m)
        dw = self.engine.start_focus_session(self.date, title="System Architecture", target_minutes=90)
        self.assertEqual(dw["session_type"], "DEEP_WORK")
        done_dw = self.engine.complete_focus_session(
            self.date, dw["focus_id"], actual_minutes=95, image_bytes=VALID_JPEG
        )
        self.assertTrue(done_dw["completed"])
        self.assertEqual(done_dw["points_awarded"], 23)  # 20 deep work + 3 evidence

        # Break logging
        brk = self.engine.log_break_session(self.date, category="MEAL", duration_minutes=20)
        self.assertEqual(brk["category"], "MEAL")

        # Deadline warnings (09:50 AM -> 10 minutes before 10:00 AM Bath deadline)
        warnings = self.engine.get_deadline_warnings(self.date, current_time_hhmm="09:50")
        bath_w = next(w for w in warnings if w["task"] == "Bath Deadline")
        self.assertEqual(bath_w["warning_stage"], "10 MINUTES LEFT")

    def test_stage_05_06_08_09_snooze_track_pause_admin_rules_and_immutable_correction(self):
        # Snooze records score_before and score_after
        sn = self.engine.record_snooze(self.date, timestamp="2026-10-06 07:02:00")
        self.assertIn("score_before", sn)
        self.assertIn("score_after", sn)

        # Pause and resume motivation track
        paused = self.engine.pause_or_resume_motivation_track(
            self.date, action="PAUSE", listened_duration_sec=50.0, track_duration_sec=100.0
        )
        self.assertEqual(paused["playback_state"], "PAUSED")
        self.assertEqual(paused["completion_pct"], 50.0)
        resumed = self.engine.pause_or_resume_motivation_track(self.date, action="RESUME")
        self.assertEqual(resumed["playback_state"], "PLAYING")

        # Non-admin role cannot change protected scoring rules (Section 152)
        with self.assertRaises(PermissionError):
            self.db.update_settings({"reward_threshold": 50}, actor_role="USER")

        # User role CAN change appearance_mode (Light/Dark)
        updated_user = self.db.update_settings({"appearance_mode": "LIGHT"}, actor_role="USER")
        self.assertEqual(updated_user.appearance_mode, "LIGHT")

        # Admin role updating scoring rule increments rule_version and logs AdminAuditEvent (Sections 33-34)
        v_before = self.db.get_current_rule_version()
        self.db.update_settings({"bath_points": 12}, actor_role="ADMIN", admin_user="LokAdmin")
        v_after = self.db.get_current_rule_version()
        self.assertEqual(v_after, v_before + 1)
        audit_logs = self.db.get_admin_audit_logs()
        self.assertTrue(any(a["field_changed"] == "bath_points" and a["admin_user"] == "LokAdmin" for a in audit_logs))

        # Immutable ScoreEvent correction (Section 76: ORIGINAL +12 + CORRECTION -2 = NET +10)
        from tests.proof_fixtures import seed_approved_bath
        bath_res = seed_approved_bath(self.db,self.engine,self.date,points=12)
        self.assertEqual(bath_res["points_awarded"], 12)
        state_before = self.engine.get_full_day_state(self.date)
        bath_ev = next(e for e in state_before["score_events"] if e["event_type"] == "PROOF_APPROVED")

        corr = self.engine.create_score_correction_event(
            date=self.date,
            parent_event_id=bath_ev["event_id"],
            correction_points=-2,
            reason="Late towel verification adjustment",
        )
        self.assertEqual(corr["correction_points"], -2)
        self.assertEqual(corr["final_net_points"], 10)
        state_after = self.engine.get_full_day_state(self.date)
        orig_still_intact = next(e for e in state_after["score_events"] if e["event_id"] == bath_ev["event_id"])
        self.assertEqual(orig_still_intact["points"], 12)  # Original event never mutated
        self.assertEqual(state_after["daily_record"]["breakdown"]["Corrections"], -2)

    def test_stage_10_11_12_14_strictbot_restrictions_usb_pairing_and_sync_conflicts(self):
        # 10-state StrictBot
        self.assertEqual(len(list(StrictBotState)), 10)
        state = self.engine.get_full_day_state(self.date)
        sb = StrictBot.evaluate_state(
            {"wakeup_status": "ON_TIME", "bath_status": "PENDING", "final_score": 30},
            state["settings"],
            now_hhmm="09:45",
        )
        self.assertEqual(sb["period"], "BEFORE_10_BATH")

        # RestrictionEvent logged when blocked
        acc = self.engine.evaluate_entertainment_access(self.date, target_app="YouTube", platform="WINDOWS")
        self.assertFalse(acc["allowed"])
        st2 = self.engine.get_full_day_state(self.date)
        self.assertGreaterEqual(len(st2["restriction_events"]), 1)

        # USB Pairing trust workflow (Sections 82 & 153)
        pair_init = self.db.initiate_usb_pairing("android_pixel", "windows_lok_pc")
        self.assertEqual(pair_init["status"], "PENDING_APPROVAL")
        pair_ok = self.db.approve_usb_pairing(pair_init["pairing_id"], approve_android=True, approve_windows=True)
        self.assertEqual(pair_ok["status"], "PAIRED")
        self.assertTrue(self.db.verify_pairing_token(pair_init["trust_token"]))

        # Deterministic SyncConflict recording & resolution (Sections 80-81)
        self.engine.submit_hourly_update(
            date=self.date,
            hour_slot="10:00-11:00",
            primary_activity="STUDY",
            description="Manual entry on PC",
            device_id="windows_lok_pc",
        )
        sync_res = self.engine.process_sync_batch(
            items=[
                {
                    "sync_id": "sync_conflict_test_1",
                    "payload_type": "HOURLY_UPDATE",
                    "payload": {
                        "date": self.date,
                        "hour_slot": "10:00-11:00",
                        "primary_activity": "CODING",
                        "description": "Photo-verified entry from Android",
                    },
                }
            ],
            device_id="android_pixel",
        )
        self.assertEqual(sync_res["applied_count"], 1)
        self.assertEqual(sync_res["conflicts_resolved"], 1)
        st_after_sync = self.engine.get_full_day_state(self.date)
        self.assertEqual(len(st_after_sync["sync_conflicts"]), 1)
        self.assertEqual(
            st_after_sync["sync_conflicts"][0]["resolution_policy"],
            "EARLIEST_VERIFIED_WINS_WITH_MERGED_SECONDARY",
        )

    def test_stage_16_to_20_clock_tamper_reboot_analytics_journal_export_and_diagnostics(self):
        # Log an event at 12:00:00, then report a backward clock jump to 11:00:00
        self.db.log_event(self.date, "NOON_CHECK", "android_lok_phone", "SYSTEM", {}, timestamp="2026-10-06 12:00:00")
        clk = self.engine.detect_system_clock_change(self.date, reported_timestamp="2026-10-06 11:00:00")
        self.assertTrue(clk["clock_anomaly_detected"])
        self.assertEqual(clk["severity"], "NEEDS_REVIEW")

        # Reboot recovery during overnight window (23:30)
        rec = self.engine.recover_after_reboot(self.date, current_time_hhmm="23:30")
        self.assertTrue(rec["recovered"])
        self.assertEqual(rec["overnight_state"], "OVERNIGHT_LOCKED")

        # Save Daily Journal
        jrn = self.engine.save_daily_journal(
            date=self.date,
            reflection_text="Maintained strong focus across morning blocks.",
            what_went_well="Zero snoozes",
            what_was_difficult="Afternoon fatigue",
            improve_tomorrow="Prepare gym gear before 5 PM",
        )
        self.assertEqual(jrn["what_went_well"], "Zero snoozes")

        # Simulate Elite Day (95/100) and verify ELITE_DAY state + Multi-format export
        sim95 = self.engine.run_dev_simulation(self.date, "SCORE_95")
        self.assertEqual(sim95["daily_record"]["final_score"], 95)
        self.assertEqual(sim95["daily_record"]["score_state"], "ELITE_DAY")
        self.assertEqual(sim95["daily_record"]["score_mode"], "REWARD_UNLOCKED")

        exp = self.engine.export_daily_record_formats(self.date, is_dev_mode=True)
        self.assertTrue(os.path.exists(exp["daily_record_json"]))
        self.assertTrue(os.path.exists(exp["score_events_csv"]))
        self.assertTrue(os.path.exists(exp["daily_report_markdown"]))

        # Self-diagnostics (Sections 134-135)
        diag = self.engine.run_self_diagnostics()
        self.assertTrue(diag["All_Passed"])
        self.assertEqual(diag["Database"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
