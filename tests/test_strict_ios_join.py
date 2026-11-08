"""Combined QR uses real preparation enrollment and camera session authority."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs

import pytest
from core.database import StrictRecordsDB
from core.strict_ios_camera import StrictIOSCamera
from core.strict_ios_join import create_join_invitation
from windows.strict_session_preparation import StrictSessionPreparation
from windows.strict_ios_camera_gateway import IOSCameraGateway
from windows.strict_session_manager import StrictSessionManager
from core.models import StrictSessionState


def rig(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'join.sqlite'));active=[False]
    preparation=StrictSessionPreparation(db,lambda _:dict(ready=True))
    pid=preparation.current('user_lok')['preparation_id']
    manager=object.__new__(StrictSessionManager)
    manager.db=db;manager.preparation=preparation;manager.session_id=None
    manager.user_id='user_lok';manager._active_preparation=None
    manager.study=SimpleNamespace(s={});manager.state=StrictSessionState.NORMAL
    camera=StrictIOSCamera(db,session_validator=manager._ios_camera_session_valid)
    camera.configure('https://camera.example.com',enabled=True,tls_verified=True)
    manager.ios_camera=camera
    gateway=IOSCameraGateway(manager,dict(origin='https://camera.example.com'))
    gateway.ready=True;manager.ios_camera_gateway=gateway
    return manager,gateway,pid,active


class Request:
    content_type='application/json'
    def __init__(self,body):self.body=body
    async def json(self):return self.body


def test_one_qr_can_test_camera_before_study_then_preserve_it_across_activation(tmp_path):
    manager,gateway,pid,active=rig(tmp_path)
    with manager.db._connect() as c:
        history=[tuple(r) for r in c.execute('SELECT * FROM score_events')]
    created=create_join_invitation(manager,'user_lok',pid)
    url=urlsplit(created['invitation']['url'])
    assert url.scheme=='https' and url.path in ('/strict-session/companion', '/ios-camera/join')
    assert parse_qs(url.query)['preparation_id']==[pid]
    assert created['invitation']['qr'].startswith('data:image/png;base64,')
    with manager.db._connect() as c:
        row=c.execute('SELECT expires FROM strict_join_invitations').fetchone()
        import time
        assert 175 < row['expires']-time.time() <= 180
    # A QR enrolls the same preparation; it never creates/starts another session.
    binding=manager.preparation.join(url.fragment,'iPhone',client_nonce='n'*43)
    manager.preparation.heartbeat(pid,'IOS',binding['credential'],dict(timer_ready=True,visible=True,online=True))
    request=Request(dict(preparation_id=pid,companion_credential=binding['credential']))
    response=asyncio.run(gateway._grant(request));token=json.loads(response.body)['token']
    grant=manager.ios_camera.redeem(token,pid,binding['credential'])
    assert grant['session_id']=='prep_'+pid
    assert not manager.ios_camera.status('user_lok',pid)['streaming']
    # Testing capture never starts study. Activation retains the same scoped feed.
    assert manager.session_id is None
    manager.session_id='real_session';manager._active_preparation=pid
    manager.state=StrictSessionState.ACTIVE
    manager.study.s=dict(account_id='user_lok',session_id='real_session',state='ACTIVE_FOCUS')
    manager.preparation.activated(pid)
    assert manager.ios_camera.authenticate(grant['credential'])['session_id']=='prep_'+pid
    with pytest.raises(PermissionError):manager.ios_camera.redeem(token,pid,binding['credential'])
    with manager.db._connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM score_events')]==history
        assert c.execute('SELECT count(*) FROM strict_preparations').fetchone()[0]==1
    manager.preparation.finish(pid)
    with pytest.raises(PermissionError):manager.ios_camera.authenticate(grant['credential'])


def test_grant_rejects_wrong_registration_and_qr_needs_verified_gateway(tmp_path):
    manager,gateway,pid,active=rig(tmp_path);active[0]=True
    with pytest.raises(PermissionError):
        asyncio.run(gateway._grant(Request(dict(preparation_id=pid,companion_credential='x'*43))))
    with pytest.raises(PermissionError):create_join_invitation(manager,'user_lok','old_preparation')
    gateway.ready=False
    with pytest.raises(ValueError):create_join_invitation(manager,'user_lok',pid)
