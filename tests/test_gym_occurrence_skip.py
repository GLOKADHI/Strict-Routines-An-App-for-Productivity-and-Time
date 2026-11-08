import os
import tempfile
import unittest
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine


class GymOccurrenceSkipTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(os.path.join(self.folder.name, 'db.sqlite'))
        self.engine = ScoringEngine(self.db, storage_root=os.path.join(self.folder.name, 'proofs'))
        self.db.update_settings({'gym_missed_penalty': -13})
        self.date = '2026-10-13'
        self.seed('gym-a', self.date, '18:30')
    def tearDown(self):
        self.folder.cleanup()
    def seed(self, identity, date, hm):
        # Materialize the day before adding this explicit fixture occurrence.
        # Otherwise initial daily materialization evaluates a historical fixture
        # against the real current clock and correctly marks it MISSED.
        self.db.ensure_daily_record(date)
        with self.db._connect() as conn:
            conn.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status) VALUES (?,'user_lok','GYM',?,?,?,?,?,'UPCOMING')", (identity,date,f'{date}T{hm}:00+05:30',f'{date}T18:00:00+05:30',f'{date}T20:00:00+05:30',f'{date}T17:59:00+05:30'))
    def skip(self, identity='gym-a', date=None):
        return self.engine.skip_gym_session(date or self.date, activity_id=identity, timestamp=f'{date or self.date} 18:05:00')
    def test_retry_and_restart_return_same_transaction(self):
        first = self.skip()
        self.assertTrue(first['accepted'])
        self.assertEqual(first['points'], -13)
        for _ in range(3): self.assertEqual(self.skip()['score_event_id'], first['score_event_id'])
        restarted = ScoringEngine(self.db, storage_root=self.engine.storage_root)
        self.assertEqual(restarted.skip_gym_session(self.date, activity_id='gym-a')['score_event_id'], first['score_event_id'])
        with self.db._connect() as conn: self.assertEqual(conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='gym-a'").fetchone()[0], 'SKIPPED')
        self.assertEqual(len([e for e in self.db.get_score_events(self.date) if e['event_type']=='GYM_SKIPPED']), 1)
    def test_separate_occurrence_and_next_day_are_distinct(self):
        self.skip()
        self.seed('gym-b', self.date, '20:30')
        self.assertNotEqual(self.skip('gym-b')['score_event_id'], self.skip()['score_event_id'])
        self.seed('gym-next', '2026-10-14', '18:30')
        self.assertTrue(self.skip('gym-next','2026-10-14')['accepted'])
    def test_timeout_after_commit_recovers_existing_result(self):
        from unittest.mock import patch
        with patch.object(self.engine, 'recalculate_daily_score', side_effect=TimeoutError('response lost')):
            with self.assertRaises(TimeoutError): self.skip()
        result = self.skip()
        self.assertTrue(result['deduplicated'])
        self.assertEqual(result['score'], -13)
        self.assertEqual(len(self.db.get_score_events(self.date)), 1)
    def test_timeout_after_commit_recovers_existing_result(self):
        from unittest.mock import patch
        with patch.object(self.engine, 'recalculate_daily_score', side_effect=TimeoutError('response lost')):
            with self.assertRaises(TimeoutError): self.skip()
        result = self.skip()
        self.assertTrue(result['deduplicated'])
        self.assertEqual(result['score'], -13)
        self.assertEqual(len(self.db.get_score_events(self.date)), 1)
    def test_timeout_after_commit_recovers_existing_result(self):
        from unittest.mock import patch
        with patch.object(self.engine, 'recalculate_daily_score', side_effect=TimeoutError('response lost')):
            with self.assertRaises(TimeoutError): self.skip()
        result = self.skip()
        self.assertTrue(result['deduplicated'])
        self.assertEqual(result['score'], -13)
        self.assertEqual(len(self.db.get_score_events(self.date)), 1)
    def test_invalid_occurrence_never_changes_ledger(self):
        self.assertFalse(self.skip('missing')['accepted'])
        self.assertFalse(self.engine.skip_gym_session(self.date, activity_id='gym-a', account_id='another')['accepted'])
        self.assertEqual(self.db.get_score_events(self.date), [])
    def test_undo_cannot_erase_confirmed_transaction(self):
        self.skip()
        self.assertFalse(self.engine.withdraw_gym_skip(self.date)['success'])
        self.assertEqual(len(self.db.get_score_events(self.date)), 1)
