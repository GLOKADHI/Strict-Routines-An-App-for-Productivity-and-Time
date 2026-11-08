"""The camera gate trusts native receiver evidence, not phone permission flags."""
from copy import deepcopy
from types import SimpleNamespace as NS
import time

import pytest

from core.database import StrictRecordsDB
from core.models import StrictSessionState
from windows.strict_ios_readiness import camera_readiness
from windows.strict_session_preparation import StrictSessionPreparation
from windows.strict_session_manager import StrictSessionManager


def live():
    return dict(status=dict(state='IOS_CAMERA_STREAMING',streaming=True,frame_sequence=3,last_frame_at=1000.),
                preview=dict(source='iPhone camera',observed_at=1000.,jpeg='receiver-encoded-jpeg'))


@pytest.mark.parametrize('change',[
    lambda v:v['status'].update(streaming=False),
    lambda v:v['status'].update(state='IOS_VIDEO_CONNECTING'),
    lambda v:v['status'].update(frame_sequence=0),
    lambda v:v['status'].update(frame_sequence=True),
    lambda v:v['status'].update(last_frame_at=996.),
    lambda v:v['status'].update(last_frame_at=1002.),
    lambda v:v['preview'].update(observed_at=996.),
    lambda v:v['preview'].update(jpeg=None),
    lambda v:v['preview'].update(source='Windows webcam'),
])
def test_no_unknown_stale_wrong_source_or_permission_only_positive(change):
    value=live();change(value)
    assert not camera_readiness(NS(ready=True,preview=lambda *_:value),'user','prep',now=1001.)['ready']


def test_fresh_decoded_frames_required_and_disconnected_gateway_fails_closed():
    gateway=NS(ready=True,preview=lambda *_:live())
    assert camera_readiness(gateway,'user','prep',now=1001.)['ready']
    gateway.ready=False
    assert not camera_readiness(gateway,'user','prep',now=1001.)['ready']
    gateway.ready=True;gateway.preview=lambda *_:(_ for _ in ()).throw(RuntimeError('receiver unavailable'))
    assert not camera_readiness(gateway,'user','prep',now=1001.)['ready']


def test_both_ready_navigation_and_direct_start_recheck_live_video(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'gate.sqlite'));camera={'ready':False}
    preparation=StrictSessionPreparation(db,lambda _:dict(ready=True,mode='DEMO'),
        camera_observer=lambda *_:dict(camera))
    pid=preparation.current('user_lok')['preparation_id']
    preparation.host_report('user_lok',pid,dict(camera=True,camera_native_id='a'*64,visible=True,secure_context=True,online=True))
    token=preparation.invite('user_lok','IOS','https://camera.example.com')['invitation']['url'].split('#')[1]
    phone=preparation.join(token,'iPad')
    preparation.heartbeat(pid,'IOS',phone['credential'],dict(timer_ready=True,visible=True,online=True))
    assert not preparation.state('user_lok')['ready']
    with pytest.raises(ValueError,match='(?i)(fresh video|live video feed)'):preparation.authorize_start('user_lok',pid)
    camera['ready']=True
    assert preparation.authorize_start('user_lok',pid)['ready']
    camera['ready']=False
    with pytest.raises(ValueError,match='(?i)(fresh video|live video feed)'):preparation.authorize_start('user_lok',pid)
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM strict_sessions').fetchone()[0]==0


def test_preparation_camera_scope_expiry_wrong_account_and_other_session(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'scope.sqlite'))
    prep=StrictSessionPreparation(db,lambda _:dict(ready=True));pid=prep.current('user_lok')['preparation_id']
    manager=object.__new__(StrictSessionManager)
    manager.db=db;manager.user_id='user_lok';manager.session_id=None;manager._active_preparation=None
    manager.study=NS(s={});manager.state=StrictSessionState.NORMAL;manager.ios_camera=NS(clock=time.time)
    scope='prep_'+pid
    assert manager._ios_camera_session_valid('user_lok',pid,scope)
    assert not manager._ios_camera_session_valid('other',pid,scope)
    assert not manager._ios_camera_session_valid('user_lok',pid,'wrong')
    manager.session_id='another';manager._active_preparation='different'
    assert not manager._ios_camera_session_valid('user_lok',pid,scope)
    manager.session_id=None
    with db._connect() as c:c.execute('UPDATE strict_preparations SET expires_at=0 WHERE preparation_id=?',(pid,))
    assert not manager._ios_camera_session_valid('user_lok',pid,scope)


def test_missing_ios_feed_pauses_monitoring_even_when_windows_hardware_passes():
    manager=object.__new__(StrictSessionManager)
    manager.study_hardware=NS(view=lambda:dict(monitoring_ok=True))
    manager._active_preparation='prep';manager.user_id='user';manager.session_id=None
    manager.state=StrictSessionState.NORMAL;manager.notification_filter=NS(status=lambda:dict(active=True))
    manager._ios_camera_readiness=lambda *_:dict(ready=False,camera=dict(streaming=False))
    assert not manager._live_study_hardware()['monitoring_ok']
    manager._ios_camera_readiness=lambda *_:dict(ready=True,camera=dict(streaming=True))
    assert manager._live_study_hardware()['monitoring_ok']
