"""Synthetic frames/endpoints only; never opens a camera or changes host audio."""
from contextlib import contextmanager
from types import SimpleNamespace
import numpy as np
import pytest
from windows.strict_guard_proctor import StrictGuardProctor
from windows.strict_audio_controller import StrictAudioController
from windows.strict_audio_endpoints import is_active_render
from windows.strict_headphone_detector import StrictHeadphoneDetector
from windows.embedded_strict_focus import blocked_accelerator,tab_for_url,TAB_URLS


def test_left_alert_requires_more_than_sixty_continuous_seconds_and_rearms_after_neutral():
    alerts=[];p=StrictGuardProctor(on_violation=lambda *event:alerts.append(event))
    for now in range(61):p._track_direction(now,True)
    assert not alerts
    p._track_direction(60.01,True);assert len(alerts)==1
    for now in range(61,100):p._track_direction(now,True)
    assert len(alerts)==1
    p._track_direction(100,False);p._track_direction(101.1,False)
    for now in range(102,164):p._track_direction(now,True)
    assert len(alerts)==2 and p.violations_count==2


def test_capture_gap_and_uncertainty_are_not_leftward_time():
    p=StrictGuardProctor()
    for now in range(40):p._track_direction(now,True)
    p._uncertain_tracking('Synthetic missing face')
    for now in range(41,90):p._track_direction(now,True)
    assert p.violations_count==0
    p._track_direction(200,True)
    assert p.violations_count==0


@pytest.mark.parametrize('pitch',[30,-30])
def test_up_down_head_pose_does_not_raise_left_alert(monkeypatch,pitch):
    p=StrictGuardProctor();p.is_calibrated=True
    face=np.array([[70,30,180,180,100,80,200,80,180,130,110,170,190,170,.99]],dtype=float)
    monkeypatch.setattr('windows.strict_guard_proctor.cv2.solvePnP',lambda *_args,**_kw:(True,np.array([np.radians(pitch),0,0]),np.zeros(3)))
    for now in range(80):p._process_detection(face,np.eye(3),np.zeros(4),now)
    assert p.violations_count==0 and p.attention_state=='NORMAL'


def test_dark_or_cropped_face_is_uncertain_not_a_violation():
    p=StrictGuardProctor();p.is_calibrated=True
    face=np.array([[70,30,180,180,100,80,200,80,180,130,110,170,190,170,.99]],dtype=float)
    p._process_detection(face,np.eye(3),np.zeros(4),0,np.zeros((240,320,3),np.uint8))
    assert p.lighting=='LOW' and p.face_presence=='UNVERIFIED' and not p.violations_count
    face[0,0]=-2
    p._process_detection(face,np.eye(3),np.zeros(4),1,np.ones((240,320,3),np.uint8)*120)
    assert p.face_framing=='MOVE_BACK' and not p.violations_count


@pytest.mark.parametrize('name',['Sony WF1000XM5','WF-1000XM5','CMFBuds2a','CMF Buds 2a'])
def test_model_names_with_different_spacing_are_detected(name):assert StrictHeadphoneDetector.is_prohibited_audio_name(name)


@pytest.mark.parametrize('name',['Logitech MX Master Mouse','Keychron keyboard','Xbox controller'])
def test_non_audio_bluetooth_is_ignored(name):assert not StrictHeadphoneDetector.is_prohibited_audio_name(name)


def test_active_render_handles_numeric_states_without_flagging_unplugged_or_capture():
    assert is_active_render(SimpleNamespace(id='{0.0.0.test}',state=1))
    assert not is_active_render(SimpleNamespace(id='{0.0.0.test}',state=8))
    assert not is_active_render(SimpleNamespace(id='{0.0.1.test}',state=1))


def observation(connected,known=True):
    return dict(detection_known=known,prohibited_connected=connected,detected_device='Synthetic headset',detected_type='WIRED')


def test_headphone_connection_episodes_do_not_double_count_or_clear_on_unknown():
    d=StrictHeadphoneDetector()
    assert d._apply_observation(observation(True))['violations_count']==1
    assert d._apply_observation(observation(True))['violations_count']==1
    assert d._apply_observation(observation(False,False))['is_paused']
    assert d._apply_observation(observation(False))['is_paused']
    assert not d._apply_observation(observation(False))['is_paused']
    assert d._apply_observation(observation(True))['violations_count']==2


class Volume:
    def __init__(self,muted=False,working=True):self.muted=muted;self.level=.65;self.working=working
    def GetMute(self):return int(self.muted)
    def SetMute(self,value,_):
        if self.working:self.muted=bool(value)
    def GetMasterVolumeLevelScalar(self):return self.level
    def SetMasterVolumeLevelScalar(self,value,_):self.level=value


@pytest.mark.parametrize('initial',[False,True])
def test_verified_mute_and_restore_preserve_original_state(monkeypatch,initial):
    volume=Volume(initial);device=SimpleNamespace(id='{0.0.0.fixture}',state=1,EndpointVolume=volume)
    @contextmanager
    def endpoints():yield [device]
    monkeypatch.setattr('windows.strict_audio_controller.audio_devices',endpoints)
    c=StrictAudioController()
    try:
        assert c.save_and_mute()['speaker_muted'] and volume.muted
        volume.level=.2
    finally:result=c.restore()
    assert result['restored'] and volume.muted==initial and volume.level==.65


def test_failed_mute_is_never_reported_as_muted(monkeypatch):
    volume=Volume(working=False);device=SimpleNamespace(id='{0.0.0.fixture}',state=1,EndpointVolume=volume)
    @contextmanager
    def endpoints():yield [device]
    monkeypatch.setattr('windows.strict_audio_controller.audio_devices',endpoints)
    c=StrictAudioController()
    try:assert not c.save_and_mute()['speaker_muted']
    finally:c.restore()


def test_tab_shortcuts_are_blocked_but_chat_text_editing_remains_available():
    for key in (9,0x54,0x4E,0x57,0x4C,0x73):assert blocked_accelerator(key,ctrl=True)
    for key in (0x43,0x56,0x41,0x5A):assert not blocked_accelerator(key,ctrl=True)
    assert not blocked_accelerator(13) and not blocked_accelerator(9)


def test_exactly_two_fixed_study_tabs_with_host_boundary_checks():
    assert set(TAB_URLS)=={'chatgpt','leetcode'}
    assert tab_for_url('https://chatgpt.com/c/example')=='chatgpt'
    assert tab_for_url('https://auth.openai.com/')=='chatgpt'
    assert tab_for_url('https://leetcode.com/problems/two-sum/')=='leetcode'
    for url in ('http://leetcode.com/','https://leetcode.com.evil.test/','https://github.com/',
                'https://user:pass@chatgpt.com/','https://chatgpt.com:999/','https://youtube.com/'):
        assert tab_for_url(url) is None


@pytest.mark.parametrize('connected,reliable,expected',[(True,True,True),(False,True,False),(True,False,False)])
def test_physical_jack_requires_reliable_presence_evidence(monkeypatch,tmp_path,connected,reliable,expected):
    import json,hashlib
    endpoint=SimpleNamespace(id='{0.0.0.fixture}',state=1,FriendlyName='Headphone (Realtek)',properties={})
    @contextmanager
    def endpoints():yield [endpoint]
    monkeypatch.setattr('windows.strict_headphone_detector.audio_devices',endpoints)
    monkeypatch.setattr('windows.strict_headphone_detector.form_factor',lambda _:3)
    monkeypatch.setattr('windows.strict_headphone_detector.jack_descriptions',lambda _:[
        dict(connected=connected,presence_supported=True,connection_type=1,port=0)])
    monkeypatch.setattr('core.database._default_database_path',lambda:str(tmp_path/'test.db'))
    if not reliable:
        (tmp_path/'strict_audio_driver_validation.json').write_text(json.dumps(dict(
            unreliable_jack_ids=[hashlib.sha256(endpoint.id.encode()).hexdigest()])))
    detector=StrictHeadphoneDetector()
    assert bool(detector._detect_wired_headphones())==expected
    assert detector._wired_state_known==reliable


def test_hotplug_mute_restores_both_endpoints_and_verification_expires(monkeypatch):
    import time
    first=SimpleNamespace(id='{0.0.0.first}',state=1,EndpointVolume=Volume(False))
    second=SimpleNamespace(id='{0.0.0.second}',state=1,EndpointVolume=Volume(True))
    devices=[first]
    @contextmanager
    def endpoints():yield list(devices)
    monkeypatch.setattr('windows.strict_audio_controller.audio_devices',endpoints)
    controller=StrictAudioController()
    try:
        controller.save_and_mute();devices.append(second)
        with controller._lock:
            controller._apply_mute_all()
            controller._checked=time.monotonic()-4
            controller._verification=(controller._checked,True,2,0)
            assert not controller.get_status()['speaker_muted']
    finally:restored=controller.restore()
    assert restored['restored'] and not first.EndpointVolume.muted and second.EndpointVolume.muted


def test_status_read_does_not_wait_for_endpoint_enumeration_lock():
    import time,threading
    controller=StrictAudioController();controller._active=True
    controller._verification=(time.monotonic(),True,1,0)
    held=threading.Event();release=threading.Event()
    def blocked_scan():
        with controller._lock:held.set();release.wait(2)
    worker=threading.Thread(target=blocked_scan);worker.start();held.wait(1)
    try:
        start=time.monotonic();assert controller.get_status()['speaker_muted']
        assert time.monotonic()-start<.2
    finally:release.set();worker.join(2)
