"""Real local HTTP contract across PC, registered Android and installed-companion origins."""
from datetime import datetime
import hashlib
import json
import threading
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import pytest
from core.database import StrictRecordsDB
from core.server import StrictRecordsRequestHandler
from core.time_window_engine import IST

NOW=datetime(2026,10,20,17,0,tzinfo=IST)
ACT='ACT-20261020-1830-GYM'
ORIGIN='chrome-extension://iincmekgilffpjmmmcgloliadppioajc'
TOKEN='fixture-registered-android-token'

@pytest.fixture
def api(tmp_path,monkeypatch):
    db=StrictRecordsDB(str(tmp_path/'api.sqlite'))
    with db._connect() as conn:
        for device,platform in [('fixture_pc','WINDOWS'),('fixture_android','ANDROID')]:
            conn.execute('''INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status)
                VALUES(?,'user_lok',?,?,?,'2026-10-20','ONLINE')''',(device,device,platform,'fixture'))
        conn.execute("INSERT INTO device_credentials VALUES('credential-fixture','fixture_android','user_lok',?,'2026-10-20',NULL)",(hashlib.sha256(TOKEN.encode()).hexdigest(),))
        conn.execute('''INSERT INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,window_start,window_end,notification_time,status)
            VALUES(?,'user_lok','GYM','Fixture booked Gym','2026-10-20','2026-10-20T18:30:00+05:30','2026-10-20T20:00:00+05:30','2026-10-20T21:00:00+05:30','2026-10-20T19:59:00+05:30','SCHEDULED')''',(ACT,))
    class Handler(StrictRecordsRequestHandler):
        pass
    Handler.db=db
    monkeypatch.setattr('core.gym_mode_engine.now_ist',lambda:NOW)
    monkeypatch.setattr('core.gym_daily_decision.now_ist',lambda:NOW)
    monkeypatch.setattr('core.daily_college_decision.now_ist',lambda:NOW)
    monkeypatch.setattr('core.server.now_ist',lambda:NOW)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root='http://127.0.0.1:'+str(server.server_port)
    def request(path,body=None,client='pc',origin=None):
        headers={'Content-Type':'application/json'}
        if client=='android':headers.update({'X-Device-Id':'fixture_android','X-Device-Token':TOKEN})
        if client=='extension':headers['Origin']=ORIGIN
        if origin:headers['Origin']=origin
        data=json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(root+path,data=data,headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=4) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    yield db,request
    server.shutdown();server.server_close();thread.join(timeout=2)

def decision(planned,date='2026-10-20',revision=0):
    return dict(date=date,gym_planned=planned,expected_revision=revision,request_id=str(uuid.uuid4()))

def test_choice_written_on_pc_is_same_authoritative_revision_on_android_and_extension(api):
    db,call=api
    status,result=call('/api/gym/decision',decision(False,date='2026-10-21'))
    assert status==200 and result['decision']['revision']==1
    for client in ['pc','android','extension']:
        status,result=call('/api/gym/decision?date=2026-10-21',client=client)
        assert status==200 and result['decision']['gym_planned'] is False and result['decision']['revision']==1

def test_extension_yes_does_not_infer_college_or_grant_other_services(api):
    db,call=api
    assert call('/api/gym/decision',decision(True),client='extension')[0]==200
    status,result=call('/api/gym_mode/status',client='android')
    assert status==200 and result['state']['lives_balance']==3 and not result['state']['is_active']
    assert result['planning']['2026-10-20']['college']['attends_college'] is None

def test_stale_client_missing_revision_and_stale_yes_cannot_override_no(api):
    db,call=api
    call('/api/gym/decision',decision(False))
    assert call('/api/gym/decision',decision(True),client='android')[0]==409
    status,result=call('/api/gym_mode/activate',{'gym_activity_id':ACT,'request_id':'fixture-request'},client='android')
    assert status==409 and result['code']=='REVISION_REQUIRED'
    status,result=call('/api/gym_mode/activate',{'gym_activity_id':ACT,'expected_revision':1,'request_id':'fixture-request'},client='extension')
    assert status==409 and not result['success']

def test_activation_retries_return_same_session_without_extra_grants(api):
    db,call=api
    call('/api/gym/decision',decision(True))
    body=dict(gym_activity_id=ACT,expected_revision=1,request_id='fixture-idempotent-activation')
    status,first=call('/api/gym_mode/activate',body,client='android')
    status2,second=call('/api/gym_mode/activate',body,client='extension')
    assert status==status2==200 and first['session_id']==second['session_id']
    assert second['replayed'] and second['state']['is_active']
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM gym_mode_sessions').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0]==0

def test_account_forgery_revoked_token_and_foreign_origins_are_rejected(api):
    db,call=api
    assert call('/api/gym/decision',{**decision(True),'account_id':'other-user'},client='android')[0]==403
    assert call('/api/gym/decision',decision(True),origin='https://hostile.invalid')[0]==403
    assert call('/api/gym/decision',decision(True),origin='chrome-extension://unrelated-extension')[0]==403
    with db._connect() as conn:conn.execute("UPDATE device_credentials SET revoked_at='now'")
    assert call('/api/gym_mode/status',client='android')[0]==403

def test_college_and_gym_are_independent_but_consistent_across_clients(api):
    db,call=api
    body=dict(date='2026-10-21',attends_college=True,expected_revision=0,request_id=str(uuid.uuid4()))
    assert call('/api/planning/college',body,client='android')[0]==200
    for client in ['pc','extension']:
        status,result=call('/api/planning/college?date=2026-10-21',client=client)
        assert status==200 and len(result['college_decision']['bath_plans'])==2
        status,result=call('/api/gym/decision?date=2026-10-21',client=client)
        assert result['decision']['gym_planned'] is None

def test_request_id_reuse_for_different_activation_is_not_a_new_session(api):
    db,call=api
    call('/api/gym/decision',decision(True))
    body=dict(gym_activity_id=ACT,expected_revision=1,request_id='fixture-idempotent-activation')
    assert call('/api/gym_mode/activate',body)[0]==200
    assert call('/api/gym_mode/activate',{**body,'gym_activity_id':'different-booking'})[0]==409
