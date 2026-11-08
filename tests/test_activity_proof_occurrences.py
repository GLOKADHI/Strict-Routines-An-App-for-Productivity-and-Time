import io
import os
import tempfile
import unittest
from datetime import datetime
from PIL import Image
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.hourly_submission_service import HourlySubmissionService, HourlySubmissionError
from core.time_window_engine import IST


class ActivityProofOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = StrictRecordsDB(os.path.join(self.temp.name,'db.sqlite'))
        self.engine = ScoringEngine(self.db, storage_root=os.path.join(self.temp.name,'proofs'))
        self.service = HourlySubmissionService(self.db,self.engine)
        self.date = '2026-10-14'
        self.now = datetime(2026,10,14,9,0,tzinfo=IST)
        with self.db._connect() as conn:
            for kind in ('BATH','GYM','HOURLY'):
                conn.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status) VALUES (?,'user_lok',?,?,?,?,?,?,'UPCOMING')", (kind,kind,self.date,f'{self.date}T09:00:00+05:30',f'{self.date}T08:50:00+05:30',f'{self.date}T09:10:00+05:30',f'{self.date}T08:49:00+05:30'))
        out=io.BytesIO();Image.new('RGB',(120,120),(90,80,30)).save(out,format='JPEG');self.photo=out.getvalue()
    def tearDown(self): self.temp.cleanup()
    def accept(self, kind, **changes):
        fields={'activity_id':kind,'category':'OTHER','description':'Evidence for the scheduled activity','submission_id':f'typed_{kind}'}
        fields.update(changes)
        return self.service.accept(fields,file_bytes=self.photo,filename='proof.jpg',account_id='user_lok',device_id='test',platform='ANDROID',received_at=self.now,expected_type=kind)
    def test_bath_and_gym_remain_independent_of_hourly_gate_and_each_other(self):
        bath=self.accept('BATH');gym=self.accept('GYM')
        self.assertTrue(bath['accepted']);self.assertTrue(gym['accepted'])
        with self.db._connect() as conn:
            self.assertNotIn(conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='HOURLY'").fetchone()[0], ('SUBMITTED','VALIDATING','VERIFIED'))
            self.assertEqual(conn.execute('SELECT count(*) FROM hourly_records WHERE date=?',(self.date,)).fetchone()[0],0)
            self.assertEqual({r[0] for r in conn.execute('SELECT task_code FROM proof_reviews WHERE evidence_id IN (?,?)',(bath['evidence_id'],gym['evidence_id']))},{'BATH','GYM'})
            self.assertEqual(conn.execute('SELECT activity_id FROM proof_reviews WHERE evidence_id=?',(gym['evidence_id'],)).fetchone()[0],'GYM')
        self.assertEqual(self.db.get_score_events(self.date),[])
    def test_typed_mismatch_and_wrong_account_are_rejected(self):
        with self.assertRaises(HourlySubmissionError): self.accept('BATH',activity_id='GYM')
        with self.assertRaises(HourlySubmissionError): self.service.accept({'activity_id':'BATH','category':'OTHER','description':'proof'},file_bytes=self.photo,filename='proof.jpg',account_id='other',device_id='test',platform='ANDROID',received_at=self.now,expected_type='BATH')
    def test_retry_after_timeout_keeps_one_submission(self):
        first=self.accept('BATH');second=self.accept('BATH')
        self.assertEqual(first['evidence_id'],second['evidence_id']);self.assertTrue(second['deduplicated'])
        with self.db._connect() as conn: self.assertEqual(conn.execute('SELECT count(*) FROM proof_submission_events').fetchone()[0],1)
    def test_boundary_and_required_fields(self):
        self.now=datetime(2026,10,14,9,10,tzinfo=IST)
        with self.assertRaises(HourlySubmissionError) as caught: self.accept('BATH')
        self.assertEqual(caught.exception.code,'WINDOW_CLOSED')
        with self.assertRaises(HourlySubmissionError): self.accept('GYM',description='')
