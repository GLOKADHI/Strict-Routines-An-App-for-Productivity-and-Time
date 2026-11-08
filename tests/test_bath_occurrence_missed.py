import os
import tempfile
import unittest
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine


class BathOccurrenceMissedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=StrictRecordsDB(os.path.join(self.temp.name,'db.sqlite'))
        self.engine=ScoringEngine(self.db,storage_root=os.path.join(self.temp.name,'proofs'))
        self.date='2026-10-14'; self.db.ensure_daily_record(self.date)
        self.db.update_settings({'bath_missed_penalty':-17})
        with self.db._connect() as c:
            for name,hm in [('bath-a','08:00'),('bath-b','13:00')]:
                c.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status) VALUES (?,'user_lok','BATH',?,?,?,?,?,'UPCOMING')",(name,self.date,f'{self.date}T{hm}:00+05:30',f'{self.date}T{hm}:00+05:30',f'{self.date}T{hm}:59+05:30',f'{self.date}T{hm}:00+05:30'))
    def tearDown(self): self.temp.cleanup()
    def miss(self,name='bath-a',timestamp=None):
        return self.engine.mark_bath_missed(self.date,activity_id=name,timestamp=timestamp or f'{self.date} 14:00:00')
    def test_repeated_worker_and_cross_device_retry_are_one_transaction(self):
        first=self.miss()
        self.assertTrue(first['accepted']);self.assertEqual(first['points'],-17)
        for _ in range(4): self.assertEqual(self.miss()['score_event_id'],first['score_event_id'])
        restarted=ScoringEngine(self.db,storage_root=self.engine.storage_root)
        self.assertTrue(restarted.mark_bath_missed(self.date,activity_id='bath-a',timestamp=f'{self.date} 14:00:00')['deduplicated'])
        self.assertEqual(len([r for r in self.db.get_score_events(self.date) if r['event_type']=='BATH_MISSED']),1)
    def test_concurrent_workers_and_atomic_failure(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as workers:
            results=list(workers.map(lambda _: self.miss(),range(4)))
        self.assertEqual(len({r['score_event_id'] for r in results}),1)
        with self.db._connect() as c:
            c.execute("CREATE TRIGGER fail_bath_state BEFORE UPDATE ON bath_records BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(Exception): self.miss('bath-b')
        self.assertEqual(len(self.db.get_score_events(self.date)),1)
    def test_two_real_occurrences_remain_distinct(self):
        self.miss();self.miss('bath-b');self.assertEqual(len(self.db.get_score_events(self.date)),2)
    def test_early_or_submitted_task_cannot_be_marked_missed(self):
        self.assertFalse(self.miss(timestamp=f'{self.date} 07:50:00')['accepted'])
        with self.db._connect() as c: c.execute("UPDATE scheduled_activities SET status='VALIDATING' WHERE activity_id='bath-a'")
        self.assertFalse(self.miss()['accepted']);self.assertEqual(self.db.get_score_events(self.date),[])
