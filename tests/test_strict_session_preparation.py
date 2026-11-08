import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.database import StrictRecordsDB
from windows.strict_session_preparation import StrictSessionPreparation, digest
from windows.embedded_strict_focus import allowed_url


@pytest.fixture
def prep(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'preparation.db'));now=[1000.0];environment={'ready':True,'secure_browser':True}
    service=StrictSessionPreparation(db,lambda _:dict(environment),clock=lambda:now[0])
    return db,service,now,environment


def report(**changes):return dict(camera=True,microphone=True,camera_native_id='a'*64,visible=True,secure_context=True,online=True,timer_ready=True,display_mode='fullscreen',**changes)
def joined(service,role):
    invitation=service.invite('user_lok',role,'https://pc.example:8766')['invitation']
    token=invitation['url'].split('#')[1]
    return service.join(token,'Android' if role=='ANDROID' else 'iPhone')
def beat(service,binding,values=None):return service.heartbeat(binding['preparation_id'],binding['role'],binding['credential'],values or report())
def all_ready(service):
    pid=service.current('user_lok')['preparation_id'];service.host_report('user_lok',pid,report())
    android=joined(service,'ANDROID');beat(service,android)
    ios=joined(service,'IOS');beat(service,ios)
    return pid,ios


def test_reported_camera_permission_never_bypasses_real_hardware_checks(prep):
    _,service,_,environment=prep
    pid,_=all_ready(service)
    environment.update(hardware_required=True,hardware_ready=False,hardware_camera_id='a'*64)
    assert not service.state('user_lok')['ready']
    with pytest.raises(ValueError):service.authorize_start('user_lok',pid)
    environment['hardware_ready']=True
    assert service.state('user_lok')['ready']
    environment['hardware_camera_id']='b'*64
    assert not service.state('user_lok')['ready']


def test_enter_refresh_and_navigation_never_create_a_study_session_or_timer(prep):
    db,s,_,_=prep
    first=s.state('user_lok');second=s.state('user_lok',refresh=True)
    assert first['preparation_id']==second['preparation_id'] and not first['ready']
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM strict_sessions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_windows_and_ios_reports_and_environment_required(prep):
    _,s,_,env=prep;pid=s.current('user_lok')['preparation_id']
    with pytest.raises(ValueError):s.authorize_start('user_lok',pid)
    s.host_report('user_lok',pid,report())
    assert not s.state('user_lok')['ready']
    a=joined(s,'ANDROID');beat(s,a)
    assert not s.state('user_lok')['ready']
    i=joined(s,'IOS');beat(s,i);assert s.authorize_start('user_lok',pid)['ready']
    env['ready']=False
    with pytest.raises(ValueError):s.authorize_start('user_lok',pid)


@pytest.mark.parametrize('field',['timer_ready','visible','online'])
def test_denied_unknown_or_hidden_device_cannot_enable_ready(prep,field):
    _,s,_,_=prep;pid,i=all_ready(s)
    bad=report();bad[field]=False;beat(s,i,bad)
    assert not s.state('user_lok')['ready']
    with pytest.raises(ValueError):s.authorize_start('user_lok',pid)


def test_stale_disconnect_restart_and_reconnect(prep):
    db,s,now,_=prep;pid,i=all_ready(s);now[0]+=46
    assert not s.state('user_lok')['ready']
    reopened=StrictSessionPreparation(db,lambda _:dict(ready=True),clock=lambda:now[0])
    assert not reopened.state('user_lok')['ready']
    s.host_report('user_lok',pid,report());beat(s,i)
    # Refresh android participant as well
    with db._connect() as c:
        c.execute("UPDATE strict_join_participants SET seen=? WHERE role='ANDROID'", (now[0],))
    assert s.state('user_lok')['ready']


def test_invitation_temporary_single_use_platform_scoped_and_credential_bound(prep):
    db,s,now,_=prep
    invite=s.invite('user_lok','IOS','https://pc.example')['invitation'];token=invite['url'].split('#')[1]
    with pytest.raises(ValueError):s.join(token,'Windows Chrome')
    binding=s.join(token,'iPhone')
    with pytest.raises(ValueError):s.join(token,'iPhone')
    with pytest.raises(PermissionError):s.heartbeat(binding['preparation_id'],'IOS','wrong',report())
    with db._connect() as c:
        assert token not in json.dumps([dict(r) for r in c.execute('SELECT * FROM strict_join_invitations')])
    new=s.invite('user_lok','IOS','https://pc.example')['invitation'];now[0]+=601
    with pytest.raises(ValueError):s.join(new['url'].split('#')[1],'iPhone')


def test_account_scope_and_finish_revoke_ready(prep):
    _,s,_,_=prep;pid,_=all_ready(s)
    with pytest.raises(ValueError):s.authorize_start('other',pid)
    s.activated(pid);s.finish(pid)
    assert not s.state('user_lok')['ready']


def test_immediate_host_restart_invalidates_old_permission_reports(prep):
    db,s,now,_=prep;all_ready(s);assert s.state('user_lok')['ready']
    reopened=StrictSessionPreparation(db,lambda _:dict(ready=True),clock=lambda:now[0])
    assert not reopened.state('user_lok')['ready']


def test_plain_http_timer_needs_no_camera_microphone_or_https(prep):
    from core.server import StrictRecordsRequestHandler
    db,s,_,_=prep;pid,i=all_ready(s)
    handler=object.__new__(StrictRecordsRequestHandler)
    handler.db=db;handler.strict_session_manager=SimpleNamespace(preparation=s)
    handler.headers={'Host':'pc.local:8765','Origin':'http://pc.local:8765'}
    handler.connection=object();handler._is_local_request=lambda:False;handler._send_json=Mock()
    handler._strict_preparation_action('/api/strict_session/preparation/heartbeat',dict(i,report=dict(timer_ready=True,visible=True,online=True,display_mode='expanded')))
    assert s.state('user_lok')['ready']
    values=s.state('user_lok')['devices']['IOS']['report']
    assert not {'camera','microphone','secure_context','camera_native_id'} & values.keys()


@pytest.mark.parametrize('field',['camera','secure_context','camera_native_id'])
def test_windows_still_requires_tested_selected_inputs(prep,field):
    _,s,_,_=prep;pid,_=all_ready(s)
    bad=report();bad[field]=False
    s.host_report('user_lok',pid,bad)
    assert not s.state('user_lok')['ready']


def test_timer_heartbeat_returns_only_scoped_readonly_timer(prep):
    db,s,_,_=prep;pid,i=all_ready(s)
    calls=[]
    s.timer=lambda p,account:calls.append((p,account)) or dict(state='ACTIVE_FOCUS',remaining_seconds=120,running=True)
    value=beat(s,i)
    assert calls==[(pid,'user_lok')] and value['timer']['remaining_seconds']==120
    with pytest.raises(PermissionError):s.heartbeat(pid,'ANDROID','invalid',{})
    assert len(calls)==1
    with db._connect() as c:assert c.execute('SELECT count(*) FROM strict_sessions').fetchone()[0]==0


def test_active_preparation_remains_available_for_long_proof_wait(prep):
    _,s,now,_=prep;pid,i=all_ready(s);s.activated(pid);now[0]+=3700
    assert s.current('user_lok')['preparation_id']==pid
    assert beat(s,i)['success']


def test_same_browser_can_retry_a_lost_join_response_without_new_participant(prep):
    db,s,_,_=prep
    invite=s.invite('user_lok','IOS','http://pc.local:8765')['invitation'];token=invite['url'].split('#')[1]
    first=s.join(token,'iPhone','n'*64);retry=s.join(token,'iPhone','n'*64)
    assert retry['resumed'] and retry['credential']==first['credential']
    with db._connect() as c:assert c.execute('SELECT count(*) FROM strict_join_participants').fetchone()[0]==1
    with pytest.raises(ValueError):s.join(token,'iPhone','different-device'*4)
    with pytest.raises(ValueError):s.join(token,'iPhone')


def test_reconnect_survives_host_restart_and_expired_invitation(prep):
    db,s,now,env=prep
    token=s.invite('user_lok','IOS','http://pc.local:8765')['invitation']['url'].split('#')[1]
    binding=s.join(token,'iPhone','n'*64)
    reopened=StrictSessionPreparation(db,lambda _:env,clock=lambda:now[0])
    assert reopened.join(token,'iPhone','n'*64)['credential']==binding['credential']
    now[0]+=601
    with pytest.raises(ValueError):reopened.join(token,'iPhone','n'*64)
    assert beat(reopened,binding)['success']  # The authenticated connection outlives its invitation.


def test_rotated_invitation_cannot_be_redeemed_by_an_old_retry(prep):
    _,s,_,_=prep
    token=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    old=s.join(token,'iPhone','n'*64)
    fresh=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    with pytest.raises(ValueError):s.join(token,'iPhone','n'*64)
    s.join(fresh,'iPhone','m'*64)
    with pytest.raises(PermissionError):beat(s,old)


def test_invalid_join_nonce_cannot_consume_an_invitation(prep):
    _,s,_,_=prep
    token=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    for value in ['short',{},'! '*32]:
        with pytest.raises(ValueError):s.join(token,'iPhone',value)
    assert s.join(token,'iPhone','n'*64)['success']


def test_new_invitation_remains_usable_for_its_full_ten_minutes(prep):
    _,s,now,_=prep
    pid=s.current('user_lok')['preparation_id'];now[0]+=3590
    token=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    now[0]+=300
    assert s.join(token,'iPhone','n'*64)['preparation_id']==pid


def test_legacy_preparation_migration_preserves_existing_records(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'legacy.db'))
    with db._connect() as c:
        c.execute("CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY,account_id TEXT,created REAL,status TEXT,host_json TEXT DEFAULT '{}',host_seen REAL DEFAULT 0)")
        c.execute("INSERT INTO strict_preparations(preparation_id,account_id,created,status) VALUES('legacy','user_lok',900,'PENDING')")
        c.execute('CREATE TABLE strict_join_invitations(token_hash TEXT PRIMARY KEY,preparation_id TEXT,role TEXT,expires REAL,used INTEGER DEFAULT 0)')
        c.execute("INSERT INTO strict_join_invitations VALUES('retained-hash','legacy','ANDROID',1500,1)")
    service=StrictSessionPreparation(db,lambda _:dict(ready=True),clock=lambda:1000)
    assert service.current('user_lok')['preparation_id']=='legacy'
    with db._connect() as c:
        row=c.execute('SELECT * FROM strict_join_invitations').fetchone()
        assert row['token_hash']=='retained-hash' and row['used']==1 and row['revoked']==0
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_desktop_mode_ipad_is_recognized_without_accepting_a_normal_mac(prep):
    _,s,_,_=prep
    ua='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'
    token=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    for info in [None,dict(platform='MacIntel',max_touch_points=0),dict(platform='Win32',max_touch_points=5)]:
        with pytest.raises(ValueError):s.join(token,ua,'n'*64,info)
    joined=s.join(token,ua,'n'*64,dict(platform='MacIntel',max_touch_points=5))
    assert joined['role']=='IOS'
    ios=s.invite('user_lok','IOS','http://pc.local')['invitation']['url'].split('#')[1]
    with pytest.raises(ValueError):s.join(ios,'Android','n'*64)


def test_invitation_rejects_invalid_origin_or_platform(prep):
    _,s,_,_=prep
    for role,origin in [('WINDOWS','https://pc.example'),('IOS','javascript:alert(1)'),('IOS','https://user:secret@pc.example/path')]:
        with pytest.raises(ValueError):s.invite('user_lok',role,origin)


def test_three_device_session_join_windows_android_ios(prep):
    db,s,now,_=prep;pid,ios=all_ready(s)
    st = s.state('user_lok')
    assert set(st['devices'])=={'WINDOWS','IOS'}
    assert s.authorize_start('user_lok',pid)['ready']
    # Verify Android and iOS can invite and join
    inv_and = s.invite('user_lok','ANDROID','http://pc.local')
    assert inv_and['success'] and inv_and['invitation']['role']=='ANDROID'
    inv_ios = s.invite('user_lok','IOS','http://pc.local')
    assert inv_ios['success'] and inv_ios['invitation']['role']=='IOS'
    with pytest.raises(ValueError):s.invite('user_lok','UNKNOWN_DEVICE','http://pc.local')


@pytest.mark.parametrize('url', ['http://chatgpt.com','https://chatgpt.com.evil.test','https://user:password@chatgpt.com','file:///C:/secret','javascript:alert(1)','https://youtube.com'])
def test_embedded_secure_browser_rejects_outside_navigation(url):assert not allowed_url(url)
@pytest.mark.parametrize('url',['https://chatgpt.com/','https://auth.openai.com/','https://cdn.oaistatic.com/a.js'])
def test_embedded_secure_browser_allows_configured_chatgpt_origins(url):assert allowed_url(url)
