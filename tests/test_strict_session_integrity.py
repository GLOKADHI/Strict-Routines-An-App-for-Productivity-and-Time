from types import SimpleNamespace
from unittest.mock import MagicMock
import numpy as np
import pytest
from windows.strict_integrity_events import IntegrityEpisodes
from windows.strict_guard_proctor import StrictGuardProctor
from windows.strict_screen_monitor import StrictScreenMonitor
from windows.strict_study_workflow import StrictStudyWorkflow
from windows.strict_demo_workflow import DemoStudyWorkflow


def test_absence_is_debounced_one_per_episode_and_unknown_does_not_rearm():
    e=IntegrityEpisodes()
    for now in range(5):assert e.observe('FACE_MISSING',True,now) is None
    assert e.observe('FACE_MISSING',True,5)
    for now in range(6,15):assert e.observe('FACE_MISSING',True,now) is None
    e.observe('FACE_MISSING',None,15)
    for now in range(16,30):assert e.observe('FACE_MISSING',True,now) is None
    e.observe('FACE_MISSING',False,30);e.observe('FACE_MISSING',False,31)
    for now in range(32,37):assert e.observe('FACE_MISSING',True,now) is None
    assert e.observe('FACE_MISSING',True,37)
    assert len(e.snapshot())==2


def test_capture_gaps_do_not_count_as_observed_missing_time():
    e=IntegrityEpisodes()
    for now in range(4):e.observe('FACE_MISSING',True,now)
    assert e.observe('FACE_MISSING',True,20) is None
    assert not e.snapshot()


def test_preflight_never_records_violations():
    e=IntegrityEpisodes(enabled=False)
    for now in range(100):e.observe('FACE_MISSING',True,now)
    assert e.snapshot()==[]


@pytest.mark.parametrize('healthy',[True,False])
def test_actual_missing_face_path_records_only_clear_frames(healthy):
    p=StrictGuardProctor();p.is_calibrated=True;p.enable_session_integrity(True)
    frame=np.random.default_rng(7).integers(30,200,(240,320,3),dtype=np.uint8) if healthy else np.zeros((240,320,3),np.uint8)
    for now in range(12):p._process_detection(None,np.eye(3),np.zeros(4),now,frame)
    assert len(p.integrity.snapshot())==(1 if healthy else 0)
    assert p.violations_count==0 # the existing leftward threshold is unchanged


def test_screen_capture_is_memory_only_closes_frames_and_expires():
    clock=[10.];closed=[]
    image=SimpleNamespace(size=(800,600),width=800,height=600,close=lambda:closed.append(True))
    monitor=StrictScreenMonitor(capture=lambda:image,clock=lambda:clock[0])
    monitor.thread=SimpleNamespace(is_alive=lambda:True)
    assert monitor.sample() and closed==[True] and monitor.status()['screen_capture_active']
    clock[0]=14.
    assert not monitor.status()['screen_capture_active']
    monitor.capture=lambda:None
    assert not monitor.sample() and not monitor.status()['screen_capture_active']


@pytest.mark.parametrize('workflow_class',[StrictStudyWorkflow,DemoStudyWorkflow])
def test_integrity_events_are_idempotent_and_never_touch_points(workflow_class):
    db=MagicMock();wf=workflow_class(db)
    wf.s=dict(session_id='test',account_id='user',state='ACTIVE_FOCUS')
    wf._save=MagicMock()
    event=IntegrityEpisodes().record('PHONE_VISIBLE','Phone visible.',source='CAMERA')
    assert wf.record_integrity_violation(event)
    assert not wf.record_integrity_violation(event)
    assert wf.s['integrity_counts']=={'PHONE_VISIBLE':1}
    assert not db.create_deduction_proposal.called
    assert not db.add_score_event.called
    assert wf.s['state']=='ACTIVE_FOCUS'


def test_phone_model_is_local_and_does_not_claim_earbud_support():
    from windows.strict_visual_objects import PhoneDetector,VisualObjectMonitor
    detector=PhoneDetector()
    assert detector.detect(np.zeros((480,640,3),np.uint8))==[]
    state=VisualObjectMonitor(IntegrityEpisodes()).status()
    assert state['visual_earbud_detection']=='UNAVAILABLE'
