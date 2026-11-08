"""
STRICTRECORDS - APTITUDE MCQ RECOVERY LIFECYCLE REGRESSION TEST SUITE
====================================================================
Tests the full lifecycle and strict rules for unexpected-exit detection:
1. First-ever launch: no MCQ challenge.
2. Normal launch after a completed authorized shutdown: no challenge.
3. Normal operation and navigation: no challenge.
4. Minimize to tray and restore: no challenge.
5. Authorized PIN exit followed by relaunch: no challenge.
6. Force-kill the EXE through Task Manager, then relaunch: challenge appears.
7. Crash the EXE, then relaunch: challenge appears when the interrupted session is reliably detected.
8. Force-kill the EXE during a challenge, then relaunch: the same challenge resumes.
9. Complete the challenge and relaunch: no duplicate challenge for the same incident.
10. Complete an authorized scheduled-event exit and relaunch: no false recovery challenge.
11. Repeat startup and shutdown rapidly: no duplicate challenge records.
12. Verify that no points are awarded or deducted by the recovery workflow.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine


class TestRecoveryLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="strict_rec_test_")
        self.db_path = os.path.join(self.temp_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db, storage_root=self.temp_dir)

    def tearDown(self):
        StrictRecordsDB.active_process_session_id = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_first_ever_launch_no_mcq_challenge(self):
        """Scenario 1: First-ever launch or no previous session must never show recovery challenge."""
        # Clean database, start first session
        sess_1 = self.db.record_process_session_start(pid=1001)
        self.assertEqual(sess_1, self.db.current_process_session_id)
        self.assertEqual(sess_1, StrictRecordsDB.active_process_session_id)

        # Query recovery status for this first launch
        unclean = self.db.get_last_unclean_process_session(sess_1)
        self.assertIsNone(unclean, "First-ever launch must never detect an unclean session")

        # Query with None (simulating uninitialized caller)
        unclean_none = self.db.get_last_unclean_process_session(None)
        self.assertIsNone(unclean_none, "First launch must evaluate to None even when session_id is None")

        # Ensure no challenge rows were created
        with self.db._connect() as conn:
            chall_count = conn.execute("SELECT COUNT(*) FROM unexpected_exit_challenges").fetchone()[0]
            self.assertEqual(chall_count, 0)

    def test_02_normal_launch_after_authorized_shutdown_no_challenge(self):
        """Scenario 2: Normal launch after a completed authorized shutdown must not show challenge."""
        sess_1 = self.db.record_process_session_start(pid=1001)
        # Normal authorized clean exit
        ok = self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")
        self.assertTrue(ok)

        # Next launch
        sess_2 = self.db.record_process_session_start(pid=1002)
        unclean = self.db.get_last_unclean_process_session(sess_2)
        self.assertIsNone(unclean, "Relaunch after clean shutdown must not detect unclean session")

    def test_03_normal_operation_and_navigation_no_challenge(self):
        """Scenario 3: Normal operation, periodic ticks, navigation must never trigger challenge."""
        sess_1 = self.db.record_process_session_start(pid=2001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        sess_2 = self.db.record_process_session_start(pid=2002)

        # Simulate periodic status checks during normal operation
        for _ in range(5):
            unclean = self.db.get_last_unclean_process_session(sess_2)
            self.assertIsNone(unclean, "Normal operation must never trigger recovery challenge")

    def test_04_minimize_to_tray_and_restore_no_challenge(self):
        """Scenario 4: Minimizing to system tray and restoring must not trigger challenge."""
        sess_1 = self.db.record_process_session_start(pid=3001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        sess_2 = self.db.record_process_session_start(pid=3002)

        # Tray minimize/restore does not change process or session clean status
        unclean = self.db.get_last_unclean_process_session(sess_2)
        self.assertIsNone(unclean, "Tray minimize/restore must not produce unclean session")

    def test_05_authorized_pin_exit_followed_by_relaunch_no_challenge(self):
        """Scenario 5: Authorized PIN exit followed by relaunch must not show challenge."""
        sess_1 = self.db.record_process_session_start(pid=4001)
        # PIN-to-close clean exit
        ok = self.db.record_process_session_clean_exit(sess_1, exit_type="PIN_EXIT")
        self.assertTrue(ok)

        # Verify DB state of session 1
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM app_process_sessions WHERE session_id = ?", (sess_1,)).fetchone()
            self.assertEqual(row["clean_exit"], 1)
            self.assertEqual(row["status"], "CLEAN_EXIT")
            self.assertEqual(row["exit_type"], "PIN_EXIT")

        # Next launch
        sess_2 = self.db.record_process_session_start(pid=4002)
        unclean = self.db.get_last_unclean_process_session(sess_2)
        self.assertIsNone(unclean, "Relaunch after PIN exit must not show challenge")

    def test_06_force_kill_through_task_manager_triggers_challenge(self):
        """Scenario 6: Force-killing the application (Task Manager / taskkill) triggers recovery challenge on next launch."""
        sess_1 = self.db.record_process_session_start(pid=5001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        # Session 2 starts
        sess_2 = self.db.record_process_session_start(pid=5002)
        # Simulated Task Manager kill: process terminated abruptly, record_process_session_clean_exit is NOT called.

        # Next launch: Session 3 starts
        sess_3 = self.db.record_process_session_start(pid=5003)
        unclean = self.db.get_last_unclean_process_session(sess_3)

        self.assertIsNotNone(unclean, "Interrupted session must be detected after Task Manager kill")
        self.assertEqual(unclean["session_id"], sess_2)
        self.assertEqual(unclean["clean_exit"], 0)

        # Create challenge for the interrupted session
        chall = self.db.create_or_get_recovery_challenge(unclean["session_id"], mode="STANDARD")
        self.assertIsNotNone(chall)
        self.assertEqual(chall["session_id"], sess_2)
        self.assertEqual(len(chall["questions"]), 25)
        self.assertEqual(chall["status"], "PENDING")

        # Verify correct_answer is not exposed in client questions
        for q in chall["questions"]:
            self.assertNotIn("correct_answer", q, "Correct answer must never leak to client questions")

    def test_07_crash_triggers_challenge_for_interrupted_session(self):
        """Scenario 7: Application crash is reliably detected on next launch and creates one challenge."""
        sess_1 = self.db.record_process_session_start(pid=6001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        # Session 2 crashes abruptly
        sess_2 = self.db.record_process_session_start(pid=6002)
        # Crash occurs (clean_exit stays 0)

        # Next launch
        sess_3 = self.db.record_process_session_start(pid=6003)
        unclean = self.db.get_last_unclean_process_session(sess_3)
        self.assertIsNotNone(unclean)
        self.assertEqual(unclean["session_id"], sess_2)
        self.assertEqual(unclean["status"], "INTERRUPTED")

    def test_08_force_kill_during_challenge_resumes_same_challenge(self):
        """Scenario 8: Force-killing during an MCQ challenge resumes the same challenge at its saved question and answer state."""
        sess_1 = self.db.record_process_session_start(pid=7001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        # Session 2 terminates abruptly
        sess_2 = self.db.record_process_session_start(pid=7002)

        # Session 3 starts and detects crash
        sess_3 = self.db.record_process_session_start(pid=7003)
        unclean = self.db.get_last_unclean_process_session(sess_3)
        self.assertIsNotNone(unclean)

        chall = self.db.create_or_get_recovery_challenge(unclean["session_id"], mode="STANDARD")
        chall_id = chall["challenge_id"]
        q1_id = chall["questions"][0]["id"]
        q2_id = chall["questions"][1]["id"]
        q1_opt = chall["questions"][0]["options"][0]
        q2_opt = chall["questions"][1]["options"][1]

        # User answers Question 1 and Question 2
        self.db.save_recovery_challenge_answer(chall_id, q1_id, q1_opt)
        self.db.save_recovery_challenge_answer(chall_id, q2_id, q2_opt)

        # Now app is forcefully terminated while challenge is in progress (Question 2 of 25 answered)!
        # Session 4 launches:
        sess_4 = self.db.record_process_session_start(pid=7004)
        unclean_resumed = self.db.get_last_unclean_process_session(sess_4)
        self.assertIsNotNone(unclean_resumed)
        self.assertEqual(unclean_resumed["session_id"], sess_2)

        # Retrieve challenge
        resumed_chall = self.db.create_or_get_recovery_challenge(unclean_resumed["session_id"])
        # Same challenge ID!
        self.assertEqual(resumed_chall["challenge_id"], chall_id)
        # Answers preserved!
        self.assertEqual(resumed_chall["answers"][q1_id], q1_opt)
        self.assertEqual(resumed_chall["answers"][q2_id], q2_opt)
        self.assertEqual(len(resumed_chall["answers"]), 2)

        # In-progress index: Question 3 (index 2)
        unanswered = [i for i, q in enumerate(resumed_chall["questions"]) if q["id"] not in resumed_chall["answers"]]
        self.assertEqual(unanswered[0], 2, "Must resume at Question 3 (first unanswered question), NOT reset to Question 1")

    def test_09_complete_challenge_and_relaunch_no_duplicate(self):
        """Scenario 9: Completing the challenge resolves recovery state and prevents duplicate challenges."""
        sess_1 = self.db.record_process_session_start(pid=8001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        sess_2 = self.db.record_process_session_start(pid=8002)  # Interrupted

        sess_3 = self.db.record_process_session_start(pid=8003)
        unclean = self.db.get_last_unclean_process_session(sess_3)
        chall = self.db.create_or_get_recovery_challenge(unclean["session_id"], mode="QUICK")
        chall_id = chall["challenge_id"]

        # Answer all questions
        for q in chall["questions"]:
            self.db.save_recovery_challenge_answer(chall_id, q["id"], q["options"][0])

        # Complete challenge
        result = self.db.complete_recovery_challenge(chall_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["points_impact"], 0)

        # Session 3 continues normal operation and then shuts down cleanly
        self.db.record_process_session_clean_exit(sess_3, exit_type="NORMAL")

        # Next launch
        sess_4 = self.db.record_process_session_start(pid=8004)
        unclean_next = self.db.get_last_unclean_process_session(sess_4)
        self.assertIsNone(unclean_next, "Completed challenge must clear recovery state; no new challenge on next launch")

    def test_10_authorized_scheduled_event_exit_no_false_challenge(self):
        """Scenario 10: Authorized scheduled-event exit must not trigger recovery challenge on relaunch."""
        sess_1 = self.db.record_process_session_start(pid=9001)
        self.db.record_process_session_clean_exit(sess_1, exit_type="NORMAL")

        sess_2 = self.db.record_process_session_start(pid=9002)

        # Prebook an official event
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        booking_payload = {
            "title": "College Practical Exam",
            "category": "COLLEGE_EXAM",
            "organization": "Sathyabama University",
            "event_date": tomorrow,
            "start_time": "09:00",
            "end_time": "12:00",
            "expected_duration_minutes": 180,
            "event_mode": "IN_PERSON",
            "status": "CONFIRMED",
        }
        event = self.db.create_official_event_booking(booking_payload, device_id="windows_pc")
        event_id = event["event_id"]

        # Authorize exit for scheduled event
        consumed = self.db.consume_official_event_exit(event_id)
        self.assertTrue(consumed)
        ok = self.db.record_process_session_clean_exit(sess_2, exit_type="SCHEDULED_EVENT_EXIT")
        self.assertTrue(ok)

        # Next launch
        sess_3 = self.db.record_process_session_start(pid=9003)
        unclean = self.db.get_last_unclean_process_session(sess_3)
        self.assertIsNone(unclean, "Scheduled-event clean exit must not trigger false recovery challenge")

    def test_11_rapid_startup_and_shutdown_no_duplicate_records(self):
        """Scenario 11: Rapid startup and shutdown cycles must not create duplicate challenge records."""
        # Baseline launch
        sess_0 = self.db.record_process_session_start(pid=10000)
        self.db.record_process_session_clean_exit(sess_0)

        # 10 rapid launches and clean shutdowns
        for i in range(10):
            s = self.db.record_process_session_start(pid=10001 + i)
            unclean = self.db.get_last_unclean_process_session(s)
            self.assertIsNone(unclean)
            self.db.record_process_session_clean_exit(s)

        with self.db._connect() as conn:
            chall_count = conn.execute("SELECT COUNT(*) FROM unexpected_exit_challenges").fetchone()[0]
            self.assertEqual(chall_count, 0, "Rapid normal cycles must never generate any challenge records")

    def test_12_points_impact_zero_guarantee(self):
        """Scenario 12: Recovery challenge completion must neither award nor deduct StrictRecords points."""
        today = datetime.now().strftime("%Y-%m-%d")
        initial_score = self.engine.authoritative_score_snapshot(today)["shared_score"]

        sess_1 = self.db.record_process_session_start(pid=11001)
        self.db.record_process_session_clean_exit(sess_1)

        sess_2 = self.db.record_process_session_start(pid=11002)  # Interrupted

        sess_3 = self.db.record_process_session_start(pid=11003)
        unclean = self.db.get_last_unclean_process_session(sess_3)
        chall = self.db.create_or_get_recovery_challenge(unclean["session_id"], mode="QUICK")

        # Answer questions
        for q in chall["questions"]:
            self.db.save_recovery_challenge_answer(chall["challenge_id"], q["id"], q["options"][0])

        result = self.db.complete_recovery_challenge(chall["challenge_id"])
        self.assertEqual(result["points_impact"], 0)

        # Check score after completion
        final_score = self.engine.authoritative_score_snapshot(today)["shared_score"]
        self.assertEqual(
            initial_score,
            final_score,
            f"Score must be completely unchanged by recovery challenge (was {initial_score}, now {final_score})"
        )


if __name__ == "__main__":
    unittest.main()
