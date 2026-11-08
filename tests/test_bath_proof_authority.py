from datetime import datetime
import io,uuid
from PIL import Image,ImageDraw
from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST

def image_bytes(alternate=False):
    im=Image.new('RGB',(128,128),(20,50,90));draw=ImageDraw.Draw(im)
    for i in range(8):draw.rectangle((i*16,0,i*16+7,127) if alternate else (0,i*16,127,i*16+7),fill=(180,210,230))
    out=io.BytesIO();im.save(out,'JPEG');return out.getvalue()

def test_bath_needs_real_retained_review_and_each_canonical_occurrence_awards_five_once(tmp_path,monkeypatch):
    current=[datetime(2026,10,20,8,0,tzinfo=IST)]
    monkeypatch.setattr('core.time_window_engine.now_ist',lambda:current[0])
    db=StrictRecordsDB(str(tmp_path/'bath.sqlite'));engine=ScoringEngine(db,storage_root=str(tmp_path/'proofs'))
    DailyCollegeDecision(db).save('user_lok','2026-10-20',True,0,str(uuid.uuid4()),actor='fixture',device_id='fixture',current=current[0])
    # College Bath 1 genuinely requires an approved morning verification. Keep
    # its synthetic evidence inside this temporary test's evidence store.
    from core.daily_routine_gate import DailyRoutineGate
    with monkeypatch.context() as setup:
        setup.setattr('core.scoring_engine.ScoringEngine', lambda db: engine)
        DailyRoutineGate.seed_approved_wakeup(db,'2026-10-20',completed_hm='07:30')
    missing=engine.complete_bath('2026-10-20')
    assert missing['status']=='BATH_EVIDENCE_REQUIRED' and missing['points_awarded']==0
    first=engine.complete_bath('2026-10-20',image_bytes=image_bytes())
    assert first['status']=='BATH_PROOF_PENDING' and first['points_awarded']==0
    assert db.get_score_events('2026-10-20')==[]
    for _ in range(2):
        approved=engine.apply_proof_approval(first['evidence_id'],'independent-fixture-admin',points=999)
        assert approved['points_awarded']==5
    current[0]=current[0].replace(hour=17,minute=35)
    second=engine.complete_bath('2026-10-20',image_bytes=image_bytes(True))
    assert second['activity_id']!=first['activity_id'] and second['points_awarded']==0
    assert engine.apply_proof_approval(second['evidence_id'],'independent-fixture-admin',points=999)['points_awarded']==5
    events=[e for e in db.get_score_events('2026-10-20') if e['points']>0]
    assert len(events)==2 and sum(e['points'] for e in events)==10
    assert db.get_daily_record('2026-10-20')['bath_status']=='COMPLETED'

def test_home_day_without_planned_bath_cannot_self_award(tmp_path,monkeypatch):
    now=datetime(2026,10,20,14,0,tzinfo=IST);monkeypatch.setattr('core.time_window_engine.now_ist',lambda:now)
    db=StrictRecordsDB(str(tmp_path/'home.sqlite'));engine=ScoringEngine(db,storage_root=str(tmp_path/'proofs'))
    DailyCollegeDecision(db).save('user_lok','2026-10-20',False,0,str(uuid.uuid4()),actor='fixture',device_id='fixture',current=now)
    result=engine.complete_bath('2026-10-20',image_bytes=image_bytes())
    assert result['status']=='BATH_WINDOW_UNAVAILABLE' and result['points_awarded']==0
    assert db.get_score_events('2026-10-20')==[]
