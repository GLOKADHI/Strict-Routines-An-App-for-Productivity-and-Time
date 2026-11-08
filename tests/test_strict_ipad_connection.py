"""Virtual-clock iPad connection gates; all rows belong to isolated test DBs."""
import json
import pytest
from test_strict_study_workflow import rig,window,advance
from windows.strict_session_preparation import StrictSessionPreparation


@pytest.fixture
def connected(rig):
    db,clock,workflow,_=rig
    prep=StrictSessionPreparation(db,lambda _:dict(ready=True),clock=lambda:clock.now.timestamp())
    token=prep.invite('user_lok','IOS','http://fixture.local')['invitation']['url'].split('#')[1]
    binding=prep.join(token,'iPad')
    def beat():return prep.heartbeat(binding['preparation_id'],'IOS',binding['credential'],dict(timer_ready=True,online=True,visible=True))
    beat();window(rig,40);workflow.begin('ipad_bound_fixture','user_lok','a55',activate=False)
    workflow.bind_ipad(binding['preparation_id']);prep.activated(binding['preparation_id']);workflow.activate()
    return rig,prep,binding,beat


def test_ipad_gap_pauses_credit_and_reconnection_preserves_elapsed(connected):
    (db,clock,w,_),prep,binding,beat=connected
    advance((db,clock,w,None),42);earned=w.s['elapsed_seconds']
    for _ in range(15):advance((db,clock,w,None),1)
    assert w.s['ipad_connection_paused'] and w.s['elapsed_seconds']==earned+2
    paused=w.s['elapsed_seconds'];beat();advance((db,clock,w,None),5)
    assert not w.s['ipad_connection_paused'] and w.s['elapsed_seconds']==paused+5
    assert 'ios_binding_hash' not in w.view()
    with db._connect() as c:assert c.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]==0


def test_completion_requires_current_same_ipad_even_after_proof_sync(connected,monkeypatch):
    (db,clock,w,_),prep,binding,beat=connected
    # Receipt validity is covered by the actual proof-pipeline tests. Isolate
    # the additional connection gate without fabricating production proof.
    w.s.update(state='END_SESSION_ENABLED',focus_completed_at=clock.now.isoformat(),sync_verified=True)
    monkeypatch.setattr(w,'_scan_proof',lambda now:None)
    clock.advance(46)
    assert not w.view()['end_enabled']
    with pytest.raises(ValueError,match='iPad'):w.complete(w.s['session_id'])
    assert not w.s['manually_completed']
    beat();assert w.view()['end_enabled']
    w.complete(w.s['session_id']);prep.finish(binding['preparation_id'])
    assert w.s['state']=='SESSION_FINALIZED' and w.view()['ios_connected']


def test_replacing_the_companion_cannot_silently_rebind_an_active_session(connected):
    (db,clock,w,_),prep,binding,beat=connected
    token=prep.invite('user_lok','IOS','http://fixture.local')['invitation']['url'].split('#')[1]
    other=prep.join(token,'iPhone')
    prep.heartbeat(other['preparation_id'],'IOS',other['credential'],dict(timer_ready=True,online=True,visible=True))
    assert w._ipad_status()['connected'] is False
    assert not w.view()['end_enabled']


def test_locked_or_hidden_report_is_not_a_violation_if_connection_stays_current(connected):
    (db,clock,w,_),prep,binding,beat=connected
    prep.heartbeat(binding['preparation_id'],'IOS',binding['credential'],dict(timer_ready=True,online=True,visible=False))
    advance((db,clock,w,None),5)
    assert w.view()['ios_connected'] and not w.s['ipad_connection_paused']
    assert not w.s.get('mobile_integrity_paused')
