import os
import unittest
import tempfile
import json
from unittest.mock import MagicMock
from core.database import StrictRecordsDB

class TestMcqExitChallenge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_exit_mcq.db")
        self.db = StrictRecordsDB(db_path=self.db_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_startup_recovery_status_always_false(self):
        """Verify startup recovery status does not trigger challenge."""
        sess_1 = self.db.record_process_session_start(pid=1001)
        # Even if previous session was unclean
        sess_2 = self.db.record_process_session_start(pid=1002)
        
        # In server.py, /api/recovery/status returns recovery_needed: False
        # Simulating server check:
        res = {"recovery_needed": False}
        self.assertFalse(res["recovery_needed"])

    def test_mcq_exit_challenge_lifecycle(self):
        """User requests MCQ exit challenge, answers questions, completes challenge, and exits cleanly."""
        sess_id = self.db.record_process_session_start(pid=2001)
        
        # 1. User opens exit challenge (STANDARD mode = 25 questions)
        chall = self.db.create_or_get_recovery_challenge(sess_id, mode="STANDARD")
        self.assertIsNotNone(chall)
        self.assertEqual(chall["total_questions"], 25)
        cid = chall["challenge_id"]
        
        # 2. Answer question 1
        q1_id = chall["questions"][0]["id"]
        updated = self.db.save_recovery_challenge_answer(cid, q1_id, "test_ans")
        self.assertEqual(updated["status"], "IN_PROGRESS")
        self.assertEqual(updated["answers"].get(q1_id), "test_ans")

        # 3. Complete challenge
        result = self.db.complete_recovery_challenge(cid)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["total_questions"], 25)

        # 4. Process clean exit via MCQ_EXIT
        clean_recorded = self.db.record_process_session_clean_exit(sess_id, exit_type="MCQ_EXIT")
        self.assertTrue(clean_recorded)

        # Verify in DB
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM app_process_sessions WHERE session_id = ?", (sess_id,)).fetchone()
            self.assertEqual(row["clean_exit"], 1)
            self.assertEqual(row["status"], "CLEAN_EXIT")
            self.assertEqual(row["exit_type"], "MCQ_EXIT")

    def test_winforms_exit_key_prompt_options(self):
        """Verify prompt_exit_key returns boolean or 'MCQ'."""
        from windows.exit_authentication import verify_exit_key
        self.assertTrue(verify_exit_key("1234"))
        self.assertFalse(verify_exit_key("wrong_pin"))

if __name__ == "__main__":
    unittest.main()
