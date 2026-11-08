import hashlib
import os
import tempfile
import unittest
import sys
from unittest.mock import patch
from core.database import StrictRecordsDB, is_valid_retained_proof_file
from core.scoring_engine import ScoringEngine


class ProductionValidationParityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=StrictRecordsDB(os.path.join(self.temp.name,'db.sqlite'))
        self.engine=ScoringEngine(self.db,storage_root=os.path.join(self.temp.name,'proofs'))
    def tearDown(self): self.temp.cleanup()
    def test_missing_retained_file_cannot_count_as_approved_evidence(self):
        self.assertFalse(is_valid_retained_proof_file(os.path.join(self.temp.name,'absent.jpg'),'WakeUp'))
    def test_invalid_image_rejected_even_with_matching_digest(self):
        path=os.path.join(self.temp.name,'not-photo.jpg');raw=b'not image data'
        with open(path,'wb') as out: out.write(raw)
        # Exercise the production image decoder, outside the legacy test-fixture bypass.
        with patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',['strict-routines-production']):
            self.assertFalse(is_valid_retained_proof_file(path,'Bath',hashlib.sha256(raw).hexdigest()))
            with self.assertRaises(ValueError): self.engine.store_evidence_file('2026-10-14','Bath','task','test',file_bytes=raw)
    def test_unmeasured_sync_claim_never_creates_wakeup_steps(self):
        self.db.ensure_daily_record('2026-10-14')
        self.db.process_sync_events_batch('some-paired-name',[{'event_id':'parity','event_type':'WAKEUP_VERIFIED','payload':{'date':'2026-10-14','bluetooth_device':'Earbuds'}}])
        with self.db._connect() as c:
            row=c.execute("SELECT camera_verified,bluetooth_verified,motivation_verified FROM wake_up_sessions WHERE date='2026-10-14' AND is_dev_mode=0").fetchone()
        self.assertEqual(tuple(row),(0,0,0))
