import json
from datetime import datetime
import pytest
from core.database import StrictRecordsDB
from core.time_window_engine import IST
from core.gym_mode_engine import GymModeEngine

@pytest.fixture
def gym_projection(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'projection.sqlite'))
    day='2026-10-20'
    with db._connect() as c:
        c.execute("DELETE FROM scheduled_activities")
        for identity,kind,time,status in [('gym','GYM','18:00','VERIFIED'),('h18','HOURLY','18:00','MISSED'),('h19','HOURLY','19:00','MISSED'),('h20','HOURLY','20:00','SUBMITTED'),('h21','HOURLY','21:00','MISSED')]:
            c.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status,details_json) VALUES(?,'user_lok',?,?,?,?,?,?,?,?)",
                (identity,kind,day,day+'T'+time+':00+05:30',day+'T'+time+':00+05:30',day+'T'+time+':30+05:30',day+'T'+time+':00+05:30',status,json.dumps({'duration_minutes':90} if identity=='gym' else {})))
        c.execute("INSERT INTO evidence_files(evidence_id,date,record_type,related_record_id,file_path,timestamp,device_id) VALUES('gym-ev',?,'GYM','gym','fixture.jpg',?,'fixture')",(day,day))
        c.execute("INSERT INTO proof_reviews(review_id,evidence_id,user_id,date,task_code,activity_id,status,submitted_at,reviewed_by) VALUES('gym-pr','gym-ev','user_lok',?,'GYM','gym','APPROVED',?,'fixture-admin')",(day,day))
        for slot in ('h18','h19','h21'):
            c.execute("INSERT INTO score_events(event_id,date,timestamp,event_type,device_id,description,points,related_task_id) VALUES(?,?,?,'HOURLY_MISSED','fixture','Exact committed penalty',-2,?)",('penalty-'+slot,day,day,slot))
    return db,GymModeEngine(db),datetime(2026,10,20,22,tzinfo=IST)

def test_accepted_interval_excuses_only_overlap_preserves_submission_and_reverses_exact_penalty_once(gym_projection):
    db,e,now=gym_projection
    for _ in range(2):e.state(dt=now)
    with db._connect() as c:
        states={r['activity_id']:r['status'] for r in c.execute('SELECT * FROM scheduled_activities')}
        assert states['h18']=='MISSED' and states['h19']=='EXCUSED' and states['h20']=='SUBMITTED' and states['h21']=='MISSED'
        corrections=c.execute("SELECT * FROM score_events WHERE event_type='SCORE_CORRECTION'").fetchall()
        assert len(corrections)==1 and corrections[0]['points']==2 and corrections[0]['parent_event_id']=='penalty-h19'

@pytest.mark.parametrize('status,reviewer',[('PENDING',''),('APPROVED','Taufa AI')])
def test_pending_or_hourly_ai_never_excuses_gym(gym_projection,status,reviewer):
    db,e,now=gym_projection
    with db._connect() as c:c.execute('UPDATE proof_reviews SET status=?,reviewed_by=?',(status,reviewer))
    e.state(dt=now)
    with db._connect() as c:
        assert c.execute("SELECT status FROM scheduled_activities WHERE activity_id='h19'").fetchone()[0]=='MISSED'
        assert c.execute("SELECT count(*) FROM score_events WHERE points>0").fetchone()[0]==0
