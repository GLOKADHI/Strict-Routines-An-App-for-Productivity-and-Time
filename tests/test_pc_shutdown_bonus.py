"""
Comprehensive test suite for PART 6:
PC Shutdown Bonus (< 11:45 PM), Phone Attendance, Photo Proof, and Next-Day Points Association.

Tests all 16 scenarios:
1. Qualifying shutdown before 11:45 PM (e.g. 23:44:59).
2. Strict cutoff boundary: 23:45:00 is disqualified; 23:44:59 is qualifying.
3. Shutdown after cutoff (e.g. 23:50:00 is disqualified).
4. Sleep / hibernation not treated as shutdown.
5. Network loss / offline disconnected PC not treated as shutdown.
6. Missing phone attendance blocks bonus.
7. Missing workstation shutdown photo blocks bonus.
8. Rejected photo blocks bonus.
9. Bonus credited to following day (target_reward_date = date + 1 day), NOT current day.
10. Midnight crossing: points survive midnight reset and are present on next day's recalculation.
11. Idempotency: duplicate evaluations/submissions never award duplicate points.
12. Offline reconnection reconciliation.
13. PC restart / agent recovery persists shutdown event in local database.
14. Negative starting balance on next day: bonus adds mathematically without clearing penalties.
15. 60/80 pt app-unlock threshold: bonus contributes to unlocking privileges on next day.
16. Admin review approval & rejection audit trail.
"""

import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from PIL import Image

from core.database import StrictRecordsDB
from core.models import UserSettings, ScoreEventType
from core.scoring_engine import ScoringEngine
from windows.shutdown_monitor import WindowsShutdownMonitor


def create_test_photo(width=200, height=200, color="black") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestPcShutdownBonusEcosystem(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(self.db_path)
        self.engine = ScoringEngine(self.db)
        self.account_id = "user_lok"
        self.date_str = "2026-10-12"
        self.next_day_str = "2026-10-13"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_qualifying_shutdown_before_cutoff(self):
        """Scenario 1: Genuine Windows shutdown at 23:30:00 (< 23:45) is qualifying."""
        res = self.db.record_pc_shutdown_event(
            event_id="sd_001",
            shutdown_timestamp="2026-10-12T23:30:00+05:30",
            evidence_source="WIN32_API",
            account_id=self.account_id,
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["is_qualifying"])
        self.assertEqual(res["target_reward_date"], self.next_day_str)
        self.assertEqual(res["bonus_status"], "AWAITING_PHONE_ATTENDANCE")

    def test_02_strict_cutoff_boundary(self):
        """
        Scenario 2: Strict cutoff boundary at 23:45:
        - 23:44:59 is qualifying (< 23:45:00).
        - 23:45:00 is disqualified (>= 23:45:00).
        """
        res_eligible = self.db.record_pc_shutdown_event(
            event_id="sd_002_a",
            shutdown_timestamp="2026-10-12T23:44:59+05:30",
            account_id=self.account_id,
        )
        self.assertTrue(res_eligible["is_qualifying"])

        res_boundary = self.db.record_pc_shutdown_event(
            event_id="sd_002_b",
            shutdown_timestamp="2026-10-12T23:45:00+05:30",
            account_id=self.account_id,
        )
        self.assertFalse(res_boundary["is_qualifying"])
        self.assertEqual(res_boundary["bonus_status"], "INELIGIBLE")

    def test_03_shutdown_after_cutoff_ineligible(self):
        """Scenario 3: Shutdown at 23:50:00 (after 23:45) is disqualified."""
        res = self.db.record_pc_shutdown_event(
            event_id="sd_003",
            shutdown_timestamp="2026-10-12T23:50:00+05:30",
            account_id=self.account_id,
        )
        self.assertFalse(res["is_qualifying"])
        self.assertEqual(res["bonus_status"], "INELIGIBLE")
        self.assertIn("at or after cutoff", res["disqualification_reason"])

    def test_04_sleep_and_hibernate_not_shutdown(self):
        """Scenario 4: Sleep / Hibernate / Suspend events are not recognized as shutdowns."""
        self.assertFalse(WindowsShutdownMonitor.is_genuine_shutdown("SUSPEND"))
        self.assertFalse(WindowsShutdownMonitor.is_genuine_shutdown("HIBERNATE"))
        self.assertFalse(WindowsShutdownMonitor.is_genuine_shutdown("CRASH"))
        self.assertFalse(WindowsShutdownMonitor.is_genuine_shutdown("NETWORK_DISCONNECT"))
        self.assertTrue(WindowsShutdownMonitor.is_genuine_shutdown("CTRL_SHUTDOWN_EVENT"))
        self.assertTrue(WindowsShutdownMonitor.is_genuine_shutdown("WM_ENDSESSION"))
        self.assertTrue(WindowsShutdownMonitor.is_genuine_shutdown("WIN32_API"))

    def test_05_network_loss_not_treated_as_shutdown(self):
        """Scenario 5: A disappearing PC or lost network heartbeat alone does not create a shutdown event."""
        # Ensure that querying status when no shutdown event was recorded returns shutdown_recorded = False
        st = self.db.get_pc_shutdown_status(self.date_str, account_id=self.account_id)
        self.assertFalse(st["shutdown_recorded"])
        self.assertEqual(st["bonus_status"], "AWAITING_WINDOWS_SHUTDOWN")
        self.assertFalse(st["eligible_for_bonus"])

    def test_06_missing_phone_attendance_blocks_bonus(self):
        """Scenario 6: Qualifying shutdown event alone does not award points without phone attendance."""
        self.db.record_pc_shutdown_event(
            event_id="sd_006",
            shutdown_timestamp="2026-10-12T23:20:00+05:30",
            account_id=self.account_id,
        )
        eval_res = self.db.evaluate_and_award_pc_shutdown_bonus("sd_006", account_id=self.account_id)
        self.assertFalse(eval_res["success"])
        self.assertEqual(eval_res["code"], "MISSING_PHONE_ATTENDANCE")

    def test_07_missing_shutdown_photo_blocks_bonus(self):
        """Scenario 7: Qualifying shutdown + phone attendance confirmed, but missing photo blocks bonus."""
        self.db.record_pc_shutdown_event(
            event_id="sd_007",
            shutdown_timestamp="2026-10-12T23:25:00+05:30",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_phone_attendance(
            event_id="sd_007",
            attendance_id="att_007",
            device_id="android_device_1",
            account_id=self.account_id,
        )
        eval_res = self.db.evaluate_and_award_pc_shutdown_bonus("sd_007", account_id=self.account_id)
        self.assertFalse(eval_res["success"])
        self.assertEqual(eval_res["code"], "MISSING_SHUTDOWN_PHOTO")

    def test_08_rejected_photo_blocks_bonus(self):
        """Scenario 8: Admin rejection of shutdown submission marks it REJECTED with no points awarded."""
        self.db.record_pc_shutdown_event(
            event_id="sd_008",
            shutdown_timestamp="2026-10-12T23:20:00+05:30",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_phone_attendance(
            event_id="sd_008",
            attendance_id="att_008",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_photo_evidence(
            event_id="sd_008",
            evidence_id="ev_008",
            account_id=self.account_id,
        )
        eval_res = self.db.evaluate_and_award_pc_shutdown_bonus(
            "sd_008",
            admin_id="ADMIN_1",
            decision="REJECT",
            reason="Display was still illuminated; photo invalid",
            account_id=self.account_id,
        )
        self.assertTrue(eval_res["success"])
        self.assertEqual(eval_res["decision"], "REJECTED")
        self.assertEqual(eval_res["bonus_status"], "REJECTED")

        # Verify score on next day is still 0
        score = self.engine.calculate_verified_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(score, 0)

    def test_09_bonus_credited_to_following_day_not_current_day(self):
        """
        Scenario 9: Bonus points are credited to the FOLLOWING DAY's balance (target_reward_date).
        They are NOT credited to current day's balance.
        """
        self.db.record_pc_shutdown_event(
            event_id="sd_009",
            shutdown_timestamp="2026-10-12T23:35:00+05:30",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_phone_attendance(
            event_id="sd_009",
            attendance_id="att_009",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_photo_evidence(
            event_id="sd_009",
            evidence_id="ev_009",
            account_id=self.account_id,
        )

        eval_res = self.db.evaluate_and_award_pc_shutdown_bonus(
            "sd_009",
            admin_id="ADMIN_1",
            account_id=self.account_id,
        )
        self.assertTrue(eval_res["success"])
        self.assertEqual(eval_res["bonus_status"], "BONUS_CREDITED")
        self.assertEqual(eval_res["target_reward_date"], self.next_day_str)
        self.assertEqual(eval_res["points_awarded"], 15)

        # Check Oct 12 score: MUST BE 0 (no shutdown bonus on Oct 12)
        score_oct12 = self.engine.calculate_verified_score(self.date_str, user_id=self.account_id)
        self.assertEqual(score_oct12, 0)

        # Check Oct 13 score: MUST BE 15 (credited to Oct 13)
        score_oct13 = self.engine.calculate_verified_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(score_oct13, 15)

    def test_10_midnight_crossing_points_survival(self):
        """
        Scenario 10: Midnight reset on Oct 12 seals Oct 12.
        Oct 13 begins with the +15 shutdown bonus in its score calculation.
        """
        # Award bonus for Oct 12 shutdown -> target Oct 13
        self.db.record_pc_shutdown_event(
            event_id="sd_010",
            shutdown_timestamp="2026-10-12T23:15:00+05:30",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_phone_attendance(event_id="sd_010", attendance_id="att_010", account_id=self.account_id)
        self.db.record_pc_shutdown_photo_evidence(event_id="sd_010", evidence_id="ev_010", account_id=self.account_id)
        self.db.evaluate_and_award_pc_shutdown_bonus("sd_010", account_id=self.account_id)

        # Recalculate daily score for Oct 13
        recalculated = self.engine.recalculate_daily_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(recalculated["score"], 15)
        self.assertIn("Shutdown Bonus", recalculated["breakdown"])
        self.assertEqual(recalculated["breakdown"]["Shutdown Bonus"], 15)

    def test_11_idempotency_duplicate_submission_no_duplicate_points(self):
        """Scenario 11: Repeated evaluation calls or duplicate events never award duplicate bonus points."""
        self.db.record_pc_shutdown_event(
            event_id="sd_011",
            shutdown_timestamp="2026-10-12T23:10:00+05:30",
            account_id=self.account_id,
        )
        self.db.record_pc_shutdown_phone_attendance(event_id="sd_011", attendance_id="att_011", account_id=self.account_id)
        self.db.record_pc_shutdown_photo_evidence(event_id="sd_011", evidence_id="ev_011", account_id=self.account_id)

        first_eval = self.db.evaluate_and_award_pc_shutdown_bonus("sd_011", account_id=self.account_id)
        self.assertTrue(first_eval["success"])
        self.assertEqual(first_eval["points_awarded"], 15)

        # Second evaluation of the same event
        second_eval = self.db.evaluate_and_award_pc_shutdown_bonus("sd_011", account_id=self.account_id)
        self.assertTrue(second_eval["success"])
        self.assertTrue(second_eval["already_credited"])

        # Score on target reward date must still be exactly 15, not 30
        score = self.engine.calculate_verified_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(score, 15)

    def test_12_offline_reconnection_reconciliation(self):
        """Scenario 12: Offline shutdown event recorded locally is reconciled via sync handler."""
        event_payload = {
            "event_id": "sd_012_offline",
            "shutdown_timestamp": "2026-10-12T23:22:00+05:30",
            "date": self.date_str,
            "cutoff_time": "23:45",
            "timezone": "Asia/Kolkata",
            "evidence_source": "WIN32_API",
        }
        batch = [
            {
                "event_id": "sync_ev_012",
                "event_type": "PC_SHUTDOWN_EVENT",
                "payload": event_payload,
            }
        ]
        sync_res = self.db.process_sync_events_batch(device_id="windows_lok_pc", events=batch, account_id=self.account_id)
        self.assertIn("sync_ev_012", sync_res["processed_event_ids"])

        # Check event exists in database
        ev = self.db.get_pc_shutdown_event(self.date_str, account_id=self.account_id)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_id"], "sd_012_offline")
        self.assertTrue(ev["is_qualifying"])

    def test_13_pc_restart_and_agent_recovery(self):
        """Scenario 13: Local SQLite stores shutdown event and persists across agent restarts."""
        monitor = WindowsShutdownMonitor(
            db=self.db,
            server_url="http://127.0.0.1:9999",  # dummy offline URL
            account_id=self.account_id,
        )
        event_id = monitor.record_local_shutdown(
            event_type="WM_QUERYENDSESSION",
            shutdown_time=datetime(2026, 10, 12, 23, 20, 0),
        )
        self.assertIsNotNone(event_id)

        # Read back directly from database
        ev = self.db.get_pc_shutdown_event(self.date_str, account_id=self.account_id)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["time_hhmmss"], "23:20:00")
        self.assertTrue(ev["is_qualifying"])

    def test_14_negative_starting_balance_on_next_day(self):
        """
        Scenario 14: If next day has negative penalty (-20), shutdown bonus (+15)
        adds mathematically (-20 + 15 = -5) without resetting or wiping the penalty.
        """
        # Insert a negative penalty for morning missed task on Oct 13
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type, device_id,
                    description, points, rule_version, audit_json
                ) VALUES ('se_pen_014', ?, 0, ?, 'MISSED_CHECKIN', 'android', 'Missed early checkpoint', -20, 1, '{}')
                """,
                (self.next_day_str, f"{self.next_day_str}T06:00:00+05:30")
            )

        # Award shutdown bonus to Oct 13
        self.db.record_pc_shutdown_event("sd_014", "2026-10-12T23:05:00+05:30", account_id=self.account_id)
        self.db.record_pc_shutdown_phone_attendance("sd_014", "att_014", account_id=self.account_id)
        self.db.record_pc_shutdown_photo_evidence("sd_014", "ev_014", account_id=self.account_id)
        self.db.evaluate_and_award_pc_shutdown_bonus("sd_014", account_id=self.account_id)

        recalc = self.engine.recalculate_daily_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(recalc["score"], -5)
        self.assertEqual(recalc["breakdown"]["Shutdown Bonus"], 15)

    def test_15_privilege_thresholds_60_80_pts(self):
        """
        Scenario 15: Bonus points (+15) help push score across 60 pts and 80 pts thresholds on next day.
        """
        # User has 48 points on Oct 13 (Controlled Mode requires 60)
        ev_file = self.engine.store_evidence_file(
            date=self.next_day_str,
            record_type="Hourly",
            related_record_id="hourly-015",
            device_id="test-device",
            file_bytes=create_test_photo(width=200, height=200),
            filename_hint="proof.jpg",
        )
        self.engine.apply_proof_approval(
            evidence_id=ev_file["evidence_id"],
            admin_id="ADMIN_1",
            points=48,
            reason="Approved hourly work",
        )
        score_before = self.engine.calculate_verified_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(score_before, 48)
        self.assertLess(score_before, 60)

        # Award shutdown bonus (+15)
        self.db.record_pc_shutdown_event("sd_015", "2026-10-12T23:00:00+05:30", account_id=self.account_id)
        self.db.record_pc_shutdown_phone_attendance("sd_015", "att_015", account_id=self.account_id)
        self.db.record_pc_shutdown_photo_evidence("sd_015", "ev_015", account_id=self.account_id)
        self.db.evaluate_and_award_pc_shutdown_bonus("sd_015", account_id=self.account_id)

        score_after = self.engine.calculate_verified_score(self.next_day_str, user_id=self.account_id)
        self.assertEqual(score_after, 63)
        self.assertGreaterEqual(score_after, 60)

    def test_16_admin_review_approval_and_rejection_audit(self):
        """Scenario 16: Admin review list and approval/rejection audit trail."""
        self.db.record_pc_shutdown_event("sd_016", "2026-10-12T23:10:00+05:30", account_id=self.account_id)
        self.db.record_pc_shutdown_phone_attendance("sd_016", "att_016", account_id=self.account_id)
        self.db.record_pc_shutdown_photo_evidence("sd_016", "ev_016", account_id=self.account_id)

        reviews = self.db.get_all_pc_shutdown_reviews(account_id=self.account_id)
        self.assertTrue(len(reviews) >= 1)
        target_rev = next(r for r in reviews if r["event_id"] == "sd_016")
        self.assertEqual(target_rev["bonus_status"], "AWAITING_REVIEW")

        res = self.db.evaluate_and_award_pc_shutdown_bonus(
            "sd_016",
            admin_id="ADMIN_LEO",
            decision="APPROVE",
            reason="Verified dark workstation display",
            account_id=self.account_id,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["decision"], "APPROVED")

        # Verify audit records in event
        ev = self.db.get_pc_shutdown_event(self.date_str, account_id=self.account_id)
        self.assertEqual(ev["reviewed_by"], "ADMIN_LEO")
        self.assertIsNotNone(ev["reviewed_at"])
        self.assertEqual(ev["bonus_status"], "BONUS_CREDITED")


if __name__ == "__main__":
    unittest.main()
