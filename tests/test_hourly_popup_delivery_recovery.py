"""Deterministic Windows popup delivery tests with isolated persistence and a controllable clock."""
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from core.time_window_engine import IST, TimeWindowEngine, dt_to_iso
from windows.submission_window_manager import SubmissionWindowManager
from windows.notification_service import StrictNotificationService
from windows.task_reminder_plan import build_reminder_plan

DAY = datetime(2026, 10, 17, tzinfo=IST)


def hourly(hour, **extra):
    anchor = DAY.replace(hour=hour)
    row = dict(activity_id=f'isolated-hourly-{hour}', type='HOURLY', title=f'Hourly ({hour-1:02d}:00-{hour:02d}:00)',
               date=DAY.date().isoformat(), status='OPEN', schedule_version=1, submission_eligible=True,
               scheduled_time=dt_to_iso(anchor), window_start=dt_to_iso(anchor-timedelta(minutes=10)),
               window_end=dt_to_iso(anchor+timedelta(minutes=10)), notification_time=dt_to_iso(anchor-timedelta(minutes=11)),
               details_json=json.dumps({'hour':hour,'hour_slot':f'{hour-1:02d}:00-{hour:02d}:00'}))
    row.update(extra)
    return row


class Store:
    def __init__(self, path):
        self.path = path
        with self._connect() as conn:
            conn.executescript('''CREATE TABLE scheduled_activities(activity_id TEXT PRIMARY KEY,status TEXT);
                CREATE TABLE score_sentinel(value INTEGER); INSERT INTO score_sentinel VALUES(17);
                CREATE TABLE proof_reviews(evidence_id TEXT, task_code TEXT, date TEXT, submitted_at TEXT, decision TEXT, signals_json TEXT);''')
    @contextmanager
    def _connect(self):
        conn=sqlite3.connect(self.path, timeout=3); conn.row_factory=sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()
    def save(self, row):
        with self._connect() as c: c.execute('INSERT OR REPLACE INTO scheduled_activities VALUES(?,?)',(row['activity_id'],row['status']))
    def visible(self):
        with self._connect() as c: return c.execute("SELECT count(*) FROM idempotent_event_log WHERE event_type='TASK_POPUP_VISIBLE'").fetchone()[0]


class Window:
    def __init__(self):
        loaded=threading.Event(); loaded.set()
        self.events=SimpleNamespace(loaded=loaded)
        self.native=SimpleNamespace(Visible=True, WindowState='Normal')
        self.calls=[]; self.fail=False; self.block=None; self.entered=threading.Event()
    def evaluate_js(self, code):
        marker='return window.deliverTaskSubmissionReminders('
        if marker not in code: return None
        payload=json.JSONDecoder().raw_decode(code.split(marker,1)[1])[0]
        self.calls.append(payload); self.entered.set()
        if self.block: assert self.block.wait(5)
        if getattr(self,'on_eval',None): self.on_eval()
        if self.fail: return None
        return dict(rendered=True,visible=self.native.Visible,displayed_keys=list(payload['delivery_keys']))


@pytest.fixture
def setup(tmp_path):
    db=Store(str(tmp_path/'isolated.db')); win=Window(); manager=SubmissionWindowManager(db=db)
    manager.desktop_api=SimpleNamespace(_window=win, show_dashboard=lambda **kw: {'success':True})
    with patch.object(manager,'_task_delivery_fallback') as fallback:
        yield db,win,manager,fallback


def deliver(manager, rows, now):
    result=manager.reconcile_task_reminders(rows, reference_now=now)
    assert manager._task_delivery_done.wait(4), 'UI delivery did not finish'
    return result


def test_blocked_wakeup_is_explained_in_popup_without_submission_or_state_write(setup):
    db,win,mgr,_=setup
    row=hourly(11,submission_eligible=False,eligibility_status='BLOCKED_WAKEUP')
    db.save(row); at=DAY.replace(hour=10,minute=50)
    deliver(mgr,[row],at)
    shown=win.calls[-1]['tasks'][0]
    assert shown['reminder_phase']=='BLOCKED_WAKEUP' and not shown['can_submit']
    assert 'wake-up' in shown['eligibility_message']
    assert win.calls[-1]['alert'] and db.visible()==1
    assert mgr.active_activity_id is None
    # Becoming eligible opens the real proof form as a distinct delivery.
    deliver(mgr,[dict(row,submission_eligible=True,eligibility_status='ELIGIBLE')],at+timedelta(seconds=1))
    assert win.calls[-1]['tasks'][0]['can_submit'] and db.visible()==2
    assert mgr.active_activity_id==row['activity_id']


def test_status_reports_blocked_wakeup_instead_of_false_open(setup):
    db,_,_,_=setup; row=hourly(11,submission_eligible=False,eligibility_status='BLOCKED_WAKEUP')
    response=invoke_status(db,[row],DAY.replace(hour=10,minute=55))
    assert response['state']=='BLOCKED_WAKEUP' and response['is_window_open'] is False


@pytest.mark.parametrize('hour',[8,9,14,20,21])
@pytest.mark.parametrize('seconds,phase',[(-61,None),(-60,'UPCOMING'),(-1,'UPCOMING'),(0,'OPEN'),(1199,'OPEN'),(1200,None)])
def test_hourly_reminder_and_exact_boundaries(hour,seconds,phase):
    row=hourly(hour); start=DAY.replace(hour=hour)-timedelta(minutes=10)
    rows,_=build_reminder_plan([row],start+timedelta(seconds=seconds))
    assert (rows[0]['reminder_phase'] if rows else None)==phase
    assert bool(rows and rows[0]['can_submit']) == (phase=='OPEN')


def test_repeated_windows_and_dismiss_do_not_cancel_next_occurrence(setup):
    db,win,mgr,_=setup; rows=[hourly(8),hourly(9),hourly(20)]
    for row in rows: db.save(row)
    for hour in (8,9,20):
        at=DAY.replace(hour=hour)-timedelta(minutes=11)
        deliver(mgr,rows,at); deliver(mgr,rows,at+timedelta(minutes=1))
        mgr.close_window()
        for _ in range(15): deliver(mgr,rows,at+timedelta(minutes=2))
    alerts=[p for p in win.calls if p['alert']]
    assert len(alerts)==6
    assert [next(t['activity_id'] for t in p['tasks'] if t['reminder_phase']=='OPEN') for p in alerts if any(t['reminder_phase']=='OPEN' for t in p['tasks'])] == [r['activity_id'] for r in rows]
    assert db.visible()==6
    with db._connect() as c: assert c.execute('SELECT value FROM score_sentinel').fetchone()[0]==17


def test_failed_loading_retries_same_phase_and_does_not_claim_delivery(setup):
    db,win,mgr,fallback=setup; row=hourly(8); db.save(row); at=DAY.replace(hour=7,minute=50)
    win.events.loaded.clear(); deliver(mgr,[row],at)
    assert db.visible()==0 and not mgr._task_alerts
    fallback.assert_called_once()
    win.events.loaded.set(); deliver(mgr,[row],at+timedelta(seconds=1))
    assert not win.calls
    deliver(mgr,[row],at+timedelta(seconds=5))
    assert db.visible()==1 and len(win.calls)==1


def test_unacknowledged_javascript_is_retried_without_losing_occurrence(setup):
    db,win,mgr,_=setup; row=hourly(9); db.save(row); at=DAY.replace(hour=8,minute=50)
    win.fail=True; deliver(mgr,[row],at)
    assert db.visible()==0 and mgr.get_status()['last_popup_delivery_error']
    win.fail=False; deliver(mgr,[row],at+timedelta(seconds=5))
    assert db.visible()==1 and len(win.calls)==2
    assert mgr.get_status()['last_popup_delivery_error'] is None


def test_hidden_window_remains_in_tray_and_delivers_fallback_toast(setup):
    db,win,mgr,fallback=setup; row=hourly(8); db.save(row); win.native.Visible=False
    show_dash = Mock(return_value={'success':True})
    mgr.desktop_api.show_dashboard=show_dash
    mgr.reconcile_task_reminders([row],DAY.replace(hour=7,minute=50))
    show_dash.assert_not_called()
    assert not win.native.Visible
    fallback.assert_called_once()


def test_old_attempt_markers_do_not_suppress_first_visible_popup(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(row); at=DAY.replace(hour=7,minute=50)
    planned=build_reminder_plan([row],at)[0][0]
    mgr.mark_event_processed(f"task-reminder:{planned['reminder_identity']}:OPEN",'TASK_SUBMISSION_REMINDER',row['activity_id'])
    deliver(mgr,[row],at)
    assert db.visible()==1


def test_restart_restores_active_surface_without_duplicate_logical_event(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(row); at=DAY.replace(hour=7,minute=50)
    deliver(mgr,[row],at); assert db.visible()==1
    other=SubmissionWindowManager(db=db); other_win=Window()
    other.desktop_api=SimpleNamespace(_window=other_win,show_dashboard=lambda **kw:{'success':True})
    with patch.object(other,'_task_delivery_fallback'):
        deliver(other,[row],at+timedelta(minutes=1)); deliver(other,[row],at+timedelta(minutes=2))
    assert len(other_win.calls)==1 and db.visible()==1


def test_reload_recovers_only_current_eligible_occurrence(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(row); at=DAY.replace(hour=7,minute=50)
    deliver(mgr,[row],at); mgr.reset_task_presentation(); deliver(mgr,[row],at+timedelta(minutes=2))
    assert len(win.calls)==2 and db.visible()==1
    mgr.reset_task_presentation(); deliver(mgr,[row],at+timedelta(minutes=21))
    assert not win.calls[-1]['tasks']


def test_delayed_callback_cannot_ack_a_stale_hour(setup):
    db,win,mgr,_=setup; first,second=hourly(8),hourly(9)
    db.save(first); db.save(second); win.block=threading.Event()
    mgr.reconcile_task_reminders([first,second],DAY.replace(hour=7,minute=50))
    assert win.entered.wait(2)
    mgr.reconcile_task_reminders([first,second],DAY.replace(hour=8,minute=50))
    assert len(win.calls)==1
    win.block.set(); assert mgr._task_delivery_done.wait(3)
    assert db.visible()==0
    win.block=None; deliver(mgr,[first,second],DAY.replace(hour=8,minute=50,second=5))
    assert db.visible()==1
    assert set(win.calls[-1]['delivery_keys'].values())=={second['activity_id']}


def test_accepted_proof_is_rechecked_before_popup_creation(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(dict(row,status='SUBMITTED'))
    deliver(mgr,[row],DAY.replace(hour=7,minute=50))
    assert not win.calls and db.visible()==0
    deliver(mgr,[dict(row,status='VALIDATING')],DAY.replace(hour=8,minute=11))
    assert win.calls[-1]['tasks'][0]['reminder_phase']=='REVIEW_PENDING'
    assert not win.calls[-1]['tasks'][0]['can_submit']


def test_overlap_pending_review_and_holds_do_not_drop_hourly(setup):
    db,win,mgr,_=setup; row=hourly(9); db.save(row)
    previous=hourly(8,status='VALIDATING'); db.save(previous)
    gym=dict(row,activity_id='gym',type='GYM'); trip=dict(row,activity_id='trip',type='TRAVEL')
    hold=dict(row,activity_id='held-hour',status='EXCUSED',submission_eligible=False)
    for item in (gym,trip,hold): db.save(item)
    deliver(mgr,[previous,gym,trip,hold,row],DAY.replace(hour=8,minute=50))
    tasks=win.calls[-1]['tasks']
    assert {t['activity_id'] for t in tasks}=={previous['activity_id'],'gym','trip',row['activity_id']}
    assert next(t for t in tasks if t['activity_id']==previous['activity_id'])['reminder_phase']=='REVIEW_PENDING'
    assert row['activity_id'] in win.calls[-1]['delivery_keys'].values()


def hourly_route():
    tree=ast.parse(Path('core/server.py').read_text(encoding='utf-8-sig'))
    branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and any(isinstance(v,ast.Constant) and v.value=='/api/hourly/status' for v in ast.walk(n.test)))
    function=ast.FunctionDef(name='route',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=x) for x in ('self','path','qs')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[branch],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    return module


def invoke_status(db,rows,at):
    class Scheduler:
        format_time_12h=staticmethod(TimeWindowEngine.format_time_12h)
        def __init__(self,store): pass
        def ensure_activities_in_db(self,date): return rows
        def get_current_countdown_state(self,*a,**kw): return {}
    namespace=dict(TimeWindowEngine=Scheduler,now_ist=lambda:at,json=json)
    exec(compile(hourly_route(),'isolated-hourly-status','exec'),namespace)
    response={}; handler=SimpleNamespace(db=db,_send_json=lambda data:response.update(data))
    namespace['route'](handler,'/api/hourly/status',{})
    return response


def test_status_does_not_inherit_previous_approval_or_stale_open_state(setup):
    db,_,_,_=setup
    with db._connect() as c: c.execute('INSERT INTO proof_reviews VALUES(?,?,?,?,?,?)',('older-proof','HOURLY',DAY.date().isoformat(),'2099-01-01','APPROVED','{}'))
    response=invoke_status(db,[hourly(8),hourly(9)],DAY.replace(hour=8,minute=50))
    assert response['hourly_activity']['activity_id']==hourly(9)['activity_id']
    assert response['state']=='OPEN' and response['review_info'] is None


def test_status_labels_preserve_configured_checkpoint_minutes(setup):
    db,_,_,_=setup; anchor=DAY.replace(hour=8,minute=30)
    row=hourly(8,scheduled_time=dt_to_iso(anchor),window_start=dt_to_iso(anchor-timedelta(minutes=10)),window_end=dt_to_iso(anchor+timedelta(minutes=10)))
    response=invoke_status(db,[row],anchor)
    assert response['checkpoint_time']=='08:30'
    assert ':30' in response['activity_slot']


def test_single_worker_uses_injected_clock_for_repeated_windows(setup):
    db,win,mgr,_=setup; rows=[hourly(8),hourly(9)]
    for row in rows: db.save(row)
    current=[DAY.replace(hour=7,minute=49)]
    class Scheduler:
        def __init__(self,store): pass
        def get_pending_notifications(self,date,reference_now):
            self.last_notification_activities=rows
            return []
    service=StrictNotificationService(db=db,submission_manager=mgr,clock=lambda:current[0])
    with patch('core.time_window_engine.TimeWindowEngine',Scheduler):
        for hour in (7,8):
            for minute in (49,50):
                current[0]=DAY.replace(hour=hour,minute=minute)
                service._poll_strictrecords_notifications()
                assert mgr._task_delivery_done.wait(3)
    assert len([call for call in win.calls if call['alert']])==4


def test_visible_hourly_recovery_updates_the_matching_submission_context(setup):
    db,win,mgr,_=setup; old,new=hourly(8,status='VALIDATING'),hourly(9)
    db.save(old); db.save(new)
    mgr.active_activity_id=old['activity_id']
    deliver(mgr,[old,new],DAY.replace(hour=8,minute=50))
    assert mgr.active_activity_id==new['activity_id']
    result=mgr.dismiss_submission_form(new['activity_id'])
    assert result['action']=='DISMISSED'


def test_hidden_view_does_not_unhide_on_due_reminder(setup):
    db,win,mgr,fallback=setup; row=hourly(8); db.save(row)
    win.native.Visible=False; win.events.loaded.clear()
    show_dash = Mock(return_value={'success':True})
    mgr.desktop_api.show_dashboard=show_dash
    deliver(mgr,[row],DAY.replace(hour=7,minute=50))
    show_dash.assert_not_called()
    assert not win.native.Visible
    fallback.assert_called_once()


def test_recent_foreground_request_does_not_debounce_a_hidden_window(setup):
    import time
    db,win,mgr,_=setup; win.native.Visible=False; mgr._last_foreground_ts=time.time()
    with patch.object(mgr.desktop_api,'show_dashboard',return_value={'success':True}) as restore:
        assert mgr.bring_window_to_foreground()
    restore.assert_called_once()


def test_proof_accepted_during_render_cannot_be_overwritten_by_open_ack(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(row)
    win.on_eval=lambda:db.save(dict(row,status='VALIDATING'))
    deliver(mgr,[row],DAY.replace(hour=7,minute=50))
    assert mgr.state.value=='VALIDATING'
    assert mgr.window_visibility_state.value=='CLOSED'
    with db._connect() as c:
        assert c.execute('SELECT status FROM scheduled_activities WHERE activity_id=?',(row['activity_id'],)).fetchone()[0]=='VALIDATING'


def test_stalled_ui_uses_fallback_without_starting_more_delivery_workers(setup):
    import time
    db,win,mgr,fallback=setup; row=hourly(8); db.save(row)
    win.block=threading.Event(); now=DAY.replace(hour=7,minute=50)
    mgr.reconcile_task_reminders([row],now); assert win.entered.wait(2)
    mgr._task_delivery_started=time.monotonic()-6
    mgr.reconcile_task_reminders([row],now+timedelta(seconds=3))
    fallback.assert_called_once(); assert len(win.calls)==1
    win.block.set(); assert mgr._task_delivery_done.wait(3)


def test_idle_hidden_dashboard_never_starts_a_javascript_delivery(setup):
    _,win,mgr,_=setup
    win.native.Visible=False
    deliver(mgr,[],DAY.replace(hour=7,minute=40))
    assert win.calls==[]
    assert not mgr._task_delivery_inflight


@pytest.mark.parametrize('visible,window_state', [
    (False, 'Normal'),
    (True, 'Minimized'),
    (True, 'Normal'),
])
def test_due_popup_keeps_tray_or_minimized_window_in_background(setup, visible, window_state):
    db, win, mgr, fallback = setup
    row = hourly(8)
    db.save(row)
    win.native.Visible = visible
    win.native.WindowState = window_state

    show_dash = Mock(return_value={'success': True})
    mgr.desktop_api.show_dashboard = show_dash
    at = DAY.replace(hour=7, minute=50)
    deliver(mgr, [row], at)
    show_dash.assert_not_called()
    if not visible or window_state == 'Minimized':
        fallback.assert_called_once()
    else:
        assert win.calls[-1]['alert'] and db.visible() == 1


def test_new_due_phase_delivers_fallback_without_unhiding_tray(setup):
    db,win,mgr,fallback=setup;row=hourly(8);db.save(row)
    at=DAY.replace(hour=7,minute=49,second=59)
    win.native.Visible=False
    deliver(mgr,[],at)
    show_dash = Mock(return_value={'success':True})
    mgr.desktop_api.show_dashboard=show_dash
    deliver(mgr,[row],at+timedelta(seconds=1))
    show_dash.assert_not_called()
    assert not win.native.Visible
    fallback.assert_called_once()


def test_due_window_keeps_tray_without_foreground_even_when_prior_js_stalled(setup):
    db,win,mgr,fallback=setup; row=hourly(8);db.save(row)
    win.block=threading.Event()
    mgr.reconcile_task_reminders([],DAY.replace(hour=7,minute=40))
    assert win.entered.wait(2)
    win.native.Visible=False
    show_dash = Mock(return_value={'success':True})
    mgr.desktop_api.show_dashboard=show_dash
    now=DAY.replace(hour=7,minute=50)
    try:
        mgr.reconcile_task_reminders([row],now)
        show_dash.assert_not_called()
        fallback.assert_called_once()
    finally:
        win.block.set()
        assert mgr._task_delivery_done.wait(3)


def test_scheduler_recovers_after_exception_without_another_worker():
    calls=[]; recovered=threading.Event()
    def reconcile():
        calls.append(1)
        if len(calls)==1: raise RuntimeError('isolated transient failure')
        recovered.set()
    service=StrictNotificationService(poll_interval_sec=.01)
    with patch.object(service,'_poll_strictrecords_notifications',side_effect=reconcile):
        try:
            service.start(); worker=service._thread; service.start()
            assert recovered.wait(2) and service._thread is worker and worker.is_alive()
        finally: service.stop()
    assert not worker.is_alive() and len(calls)>=2


def test_backward_clock_jump_does_not_replay_pre_reminder_after_open_delivery(setup):
    db,win,mgr,_=setup; row=hourly(8); db.save(row)
    deliver(mgr,[row],DAY.replace(hour=7,minute=50))
    deliver(mgr,[row],DAY.replace(hour=7,minute=49))
    deliver(mgr,[row],DAY.replace(hour=7,minute=50))
    assert len([payload for payload in win.calls if payload['alert']])==1
    assert db.visible()==1


@pytest.mark.parametrize('stamp,phase',[('2026-11-01T05:49:00+00:00','UPCOMING'),('2026-11-01T05:50:00+00:00','OPEN'),('2026-11-01T06:09:59+00:00','OPEN'),('2026-11-01T06:10:00+00:00',None)])
def test_absolute_bounds_across_a_daylight_saving_offset_change(stamp,phase):
    row=hourly(8,window_start='2026-11-01T01:50:00-04:00',window_end='2026-11-01T01:10:00-05:00',notification_time='2026-11-01T01:49:00-04:00')
    planned,_=build_reminder_plan([row],datetime.fromisoformat(stamp))
    assert (planned[0]['reminder_phase'] if planned else None)==phase
