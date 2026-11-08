from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import urllib.error
import pytest
from windows.strict_focus_bridge import DesktopFocusBridge,RemoteEmbeddedStrictFocus,request_json


@pytest.fixture
def bridge(tmp_path):
    controller=NS(available=lambda:True,open=Mock(return_value=dict(success=True)),show=lambda:True,close=Mock(),update=Mock())
    owner=DesktopFocusBridge(NS(_host='127.0.0.1',_port=8765),controller,tmp_path/'desktop.json')
    owner.start()
    yield owner,controller
    owner.stop()


def test_bridge_presents_in_desktop_without_taking_backend_authority(bridge):
    owner,controller=bridge;remote=RemoteEmbeddedStrictFocus(owner.path)
    assert remote.available()
    assert remote.open('strict_sess_isolated',lambda _:None)['success']
    controller.open.assert_called_once_with('strict_sess_isolated',owner.complete)
    remote.update(dict(state='ACTIVE_FOCUS'),dict(camera='ACTIVE'))
    controller.update.assert_called_once_with(dict(state='ACTIVE_FOCUS'),dict(camera='ACTIVE'))
    assert remote.show()
    remote.close();controller.close.assert_called_once()


def test_unauthenticated_and_invalid_commands_cannot_touch_focus_control(bridge):
    owner,controller=bridge;url=f'http://127.0.0.1:{owner.server.server_port}'
    with pytest.raises(urllib.error.HTTPError) as error:request_json(url+'/focus/open',dict(session_id='strict_sess_test'),'wrong')
    assert error.value.code==403
    for action,body in [('open',dict(session_id='arbitrary')),('eval',dict(code='untrusted')),('update',dict(status='invalid'))]:
        with pytest.raises(urllib.error.HTTPError) as error:request_json(url+'/focus/'+action,body,owner.token)
        assert error.value.code==400
    controller.open.assert_not_called();controller.update.assert_not_called()


def test_unavailable_or_changed_desktop_never_claims_ready(bridge,tmp_path):
    owner,_=bridge
    assert not RemoteEmbeddedStrictFocus(tmp_path/'missing.json').available()
    value=json.loads(owner.path.read_text());value['instance']='changed';owner.path.write_text(json.dumps(value))
    assert not RemoteEmbeddedStrictFocus(owner.path).available()


def test_remote_open_failure_leaves_start_blocked(tmp_path):
    remote=RemoteEmbeddedStrictFocus(tmp_path/'missing.json')
    assert not remote.open('strict_sess_isolated',None)['success']
