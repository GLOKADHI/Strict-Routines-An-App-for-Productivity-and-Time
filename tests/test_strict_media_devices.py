"""Input selection regressions without starting real camera/audio capture."""
import sys
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from windows.strict_media_devices import match_camera_label, resolve_camera_id
from windows.strict_study_hardware import StrictStudyHardware
from windows.strict_guard_proctor import StrictGuardProctor
from windows.strict_session_manager import StrictSessionManager

CAMERAS=[dict(native_id='a'*64,name='Built-in Camera',index=0),
         dict(native_id='b'*64,name='Virtual Camera',index=2)]


def test_virtual_camera_and_usb_label_resolve_to_exact_native_identity():
    assert match_camera_label('Virtual Camera',CAMERAS)==CAMERAS[1]
    assert match_camera_label('Built-in Camera (1234:ABCD)',CAMERAS)==CAMERAS[0]
    assert resolve_camera_id('b'*64,CAMERAS)['index']==2
    assert resolve_camera_id('b'*64,[dict(CAMERAS[1],index=0)])['index']==0


def test_removed_or_ambiguous_camera_never_falls_back_to_zero():
    with pytest.raises(ValueError):resolve_camera_id('missing',CAMERAS)
    with pytest.raises(ValueError):match_camera_label('Built-in Camera',[CAMERAS[0],dict(CAMERAS[0],index=1,native_id='c'*64)])
    with pytest.raises(ValueError):match_camera_label('unrecognized',CAMERAS)


def test_hardware_needs_consent_and_selected_camera(monkeypatch):
    lookup=Mock(side_effect=ValueError('Selected input is unavailable'))
    monkeypatch.setattr('windows.strict_media_devices.resolve_camera_id',lookup)
    hardware=StrictStudyHardware()
    assert not hardware.start(consent=False,camera_id='b'*64)['success']
    lookup.assert_not_called()
    assert not hardware.start(consent=True,camera_id='b'*64)['success']
    assert hardware.thread is None


def test_capture_opens_selected_camera_index_and_releases_on_failure(monkeypatch):
    monkeypatch.setattr('windows.strict_media_devices.resolve_camera_id',lambda _:CAMERAS[1])
    capture=NS(isOpened=lambda:False,release=Mock())
    factory=Mock(return_value=capture)
    monkeypatch.setattr('windows.strict_guard_proctor.cv2',NS(VideoCapture=factory,CAP_DSHOW=700,FaceDetectorYN=NS(create=lambda *_:Mock())))
    proctor=StrictGuardProctor();proctor.camera_id='b'*64;proctor._proctoring_loop()
    factory.assert_called_once_with(2,700)
    capture.release.assert_called_once()
    assert proctor.get_telemetry()['camera']=='UNAVAILABLE'


def test_reorder_during_open_cannot_process_an_unrelated_camera(monkeypatch):
    lookup=Mock(side_effect=[CAMERAS[1],dict(CAMERAS[1],index=1)])
    monkeypatch.setattr('windows.strict_media_devices.resolve_camera_id',lookup)
    capture=NS(isOpened=lambda:True,release=Mock(),read=Mock())
    monkeypatch.setattr('windows.strict_guard_proctor.cv2',NS(VideoCapture=lambda *_:capture,CAP_DSHOW=700,FaceDetectorYN=NS(create=lambda *_:Mock())))
    proctor=StrictGuardProctor();proctor.camera_id='b'*64;proctor._proctoring_loop()
    capture.read.assert_not_called();capture.release.assert_called_once()
    assert proctor.get_telemetry()['camera']=='UNAVAILABLE'


def test_companion_timer_is_scoped_readonly_and_uses_same_authoritative_time():
    view=dict(account_id='user',state='ACTIVE_FOCUS',focus_remaining_seconds=125,
              monitoring_ok=True,monitoring_resumed=True)
    manager=object.__new__(StrictSessionManager)
    manager._lock=threading.RLock();manager._active_preparation='prep'
    manager.db=NS(get_active_user_profile=lambda:dict(user_id='active-other'))
    manager.study=NS(view=lambda:dict(view))
    assert manager._companion_timer('wrong','user')['remaining_seconds'] is None
    assert manager._companion_timer('prep','other')['state']=='WAITING'
    timer=manager._companion_timer('prep','user')
    assert timer['remaining_seconds']==125 and timer['running']
    assert 'account_id' not in timer
    view.update(state='ADAPTIVE_COMPLETION',adaptive_remaining_seconds=65,monitoring_ok=False)
    timer=manager._companion_timer('prep','user')
    assert timer['remaining_seconds']==65 and not timer['running']


def test_joined_phone_sees_planned_duration_without_starting_session():
    manager=object.__new__(StrictSessionManager)
    manager._lock=threading.RLock();manager._active_preparation=None
    manager.db=NS(get_active_user_profile=lambda:dict(user_id='user'))
    manager.study=NS(view=lambda:dict(state='IDLE'))
    manager._preparation_environment=lambda _:dict(focus_seconds=2100)
    timer=manager._companion_timer('pending','user')
    assert timer['remaining_seconds']==2100 and not timer['running'] and timer['state']=='WAITING'
    assert manager._companion_timer('pending','other')['remaining_seconds'] is None
