from datetime import datetime
import io
from PIL import Image
import pytest
from core.scoring_engine import ScoringEngine
from tests.test_daily_gym_decision import gym,choose,NOW,DAY,ACT

def photo():
    b=io.BytesIO();Image.new('RGB',(128,128),(50,100,150)).save(b,format='JPEG');return b.getvalue()

def test_gym_submission_never_self_approves_and_duplicate_photo_reuses_review(gym,monkeypatch,tmp_path):
    db,d,e=gym;choose(d,True)
    monkeypatch.setattr('core.time_window_engine.now_ist',lambda:NOW)
    engine=ScoringEngine(db,storage_root=str(tmp_path/'proofs'))
    first=engine.complete_gym_session(DAY,duration_minutes=90,image_bytes=photo())
    second=engine.complete_gym_session(DAY,duration_minutes=90,image_bytes=photo())
    assert first['submitted'] and first['points_awarded']==0 and first['points_pending_review']
    assert first['evidence_id']==second['evidence_id']
    with db._connect() as c:
        review=c.execute('SELECT * FROM proof_reviews WHERE evidence_id=?',(first['evidence_id'],)).fetchone()
        assert review['status']=='PENDING' and review['activity_id']
        assert c.execute("SELECT count(*) FROM score_events WHERE points>0").fetchone()[0]==0

def test_no_does_not_create_gym_proof_obligation_or_points(gym,monkeypatch,tmp_path):
    db,d,e=gym;choose(d,False)
    monkeypatch.setattr('core.time_window_engine.now_ist',lambda:NOW)
    result=ScoringEngine(db,storage_root=str(tmp_path/'proofs')).complete_gym_session(DAY,duration_minutes=90,image_bytes=photo())
    assert result['status']=='GYM_NOT_PLANNED' and result['points_awarded']==0
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM proof_reviews').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM gym_mode_sessions').fetchone()[0]==0
