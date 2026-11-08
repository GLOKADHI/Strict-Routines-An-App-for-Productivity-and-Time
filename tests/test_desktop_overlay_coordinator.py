"""Presentation races with no production DB, score changes, or process actions."""
from concurrent.futures import ThreadPoolExecutor
from windows.desktop_overlay_coordinator import DesktopOverlayCoordinator


def setup():
    effects = []
    coordinator = DesktopOverlayCoordinator(lambda state, row: effects.append((state, row)))
    row = dict(activity_id='isolated-hourly', schedule_version=2, type='HOURLY', reminder_phase='OPEN')
    return coordinator, effects, row


def test_walk_and_speech_conflict_waits_for_explicit_handoff():
    for stage in ('walking', 'speak'):
        c, effects, row = setup()
        c.robot_started(7)
        c.robot_stage_changed(7, stage)
        c.reconcile(row)
        state = c.snapshot()
        assert state['ROBOT_VISIBLE'] and state['HOURLY_ALERT_VISIBLE']
        assert not c.request_panel(('isolated-hourly', 2))
        assert c.open_pending()
        assert c.snapshot()['ROBOT_MINIMIZED']
        assert c.snapshot()['ROBOT_RESTRICTION_ACTIVE']
        assert not c.snapshot()['HOURLY_ALERT_VISIBLE']
        assert effects[-1][1] == row
        c.panel_changed(('isolated-hourly', 2), True)
        assert c.snapshot()['HOURLY_PANEL_VISIBLE']


def test_new_robot_during_hourly_never_claims_foreground():
    c, _, row = setup()
    c.reconcile(row)
    assert c.request_panel(('isolated-hourly', 2))
    c.panel_changed(('isolated-hourly', 2), True)
    c.robot_started(1)
    assert c.snapshot()['ROBOT_MINIMIZED'] and not c.snapshot()['ROBOT_VISIBLE']
    c.robot_started(2)
    assert c.snapshot()['HOURLY_PANEL_VISIBLE'] and c.snapshot()['ROBOT_MINIMIZED']
    c.robot_resolved(1)  # Old verified-close result cannot remove a newer warning.
    assert c.snapshot()['ROBOT_RESTRICTION_ACTIVE']


def test_repeats_are_single_instance_and_open_only_once():
    c, effects, row = setup()
    c.robot_started(1)
    c.reconcile(row)
    before = len(effects)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: c.reconcile(row), range(80)))
        opened = list(pool.map(lambda _: c.open_pending(), range(80)))
    assert sum(opened) == 1
    assert len(effects) == before + 1


def test_home_preserves_pending_and_restores_matching_panel():
    c, effects, row = setup()
    c.robot_started(1)
    c.reconcile(row)
    c.home_requested()
    assert c.snapshot()['ROBOT_RESTRICTION_ACTIVE']
    c.home_finished()
    assert c.snapshot()['HOURLY_WINDOW_PENDING'] and effects[-1][1] == row
    assert c.snapshot()['ROBOT_MINIMIZED']


def test_completion_clears_only_matching_occurrence_and_retains_restriction():
    c, _, row = setup()
    c.robot_started(1)
    c.reconcile(row)
    c.open_pending()
    before=c.snapshot()
    c.submitted('another', 2)
    assert c.snapshot()==before
    assert c.snapshot()['HOURLY_WINDOW_PENDING']
    c.submitted('isolated-hourly', 1)
    assert c.snapshot()['HOURLY_WINDOW_PENDING']
    c.submitted('isolated-hourly', 2)
    state = c.snapshot()
    assert state['HOURLY_SUBMISSION_COMPLETED'] and not state['HOURLY_ALERT_VISIBLE']
    assert not state['HOURLY_WINDOW_PENDING'] and state['ROBOT_VISIBLE']
    c.reconcile(row)  # A scheduler delivery already in flight cannot recreate it.
    assert not c.snapshot()['HOURLY_WINDOW_PENDING']
    assert not c.request_panel(('isolated-hourly',2))
    c.robot_resolved(1)
    assert not c.snapshot()['ROBOT_VISIBLE']


def test_cancel_expire_and_main_window_visibility_preserve_independent_state():
    c, _, row = setup()
    c.robot_started(1)
    c.reconcile(row)
    c.open_pending()
    c.panel_changed(('isolated-hourly', 2), True)
    c.main_changed(False)
    assert c.snapshot()['ROBOT_VISIBLE'] and c.snapshot()['HOURLY_ALERT_VISIBLE']
    c.main_changed(True)
    assert c.snapshot()['ROBOT_MINIMIZED'] and c.snapshot()['HOURLY_PANEL_VISIBLE']
    c.panel_changed(('isolated-hourly', 2), False)
    assert c.snapshot()['HOURLY_WINDOW_PENDING'] and c.snapshot()['ROBOT_VISIBLE']
    c.reconcile(None)
    assert not c.snapshot()['HOURLY_WINDOW_PENDING'] and not c.open_pending()


def test_resolved_robot_opens_deferred_hourly_without_resurrecting_robot():
    c, effects, row = setup()
    c.robot_started(1)
    c.reconcile(row)
    c.robot_resolved(1)
    assert effects[-1][1] == row and not c.snapshot()['ROBOT_RESTRICTION_ACTIVE']
    c.submitted('isolated-hourly', 2)
    assert not c.snapshot()['ROBOT_VISIBLE']


def test_old_dom_callback_does_not_displace_new_panel():
    c, _, row = setup()
    c.reconcile(row)
    c.open_pending()
    c.reconcile(dict(row, schedule_version=3))
    c.open_pending()
    assert not c.request_panel(('isolated-hourly',2))
    c.panel_changed(('isolated-hourly', 2), False)
    c.panel_changed(('isolated-hourly', 2), True)
    assert c.panel_identity == ('isolated-hourly', 3)
    assert c.snapshot()['HOURLY_WINDOW_PENDING']


def test_hidden_background_webview_cannot_suppress_a_real_restriction():
    c, _, row=setup()
    c.main_changed(False)
    c.reconcile(row)
    assert not c.request_panel(('isolated-hourly',2))
    c.robot_started(1)
    assert c.snapshot()['ROBOT_VISIBLE'] and c.snapshot()['HOURLY_ALERT_VISIBLE']
    assert c.open_pending()  # Explicit notice action is allowed to restore the main UI.
    assert c.request_panel(('isolated-hourly',2))
    assert c.snapshot()['ROBOT_MINIMIZED']


def test_desktop_bridge_initializes_from_actual_native_visibility():
    from types import SimpleNamespace
    from desktop_app import DesktopApi
    c, _, row=setup()
    controller=SimpleNamespace(overlays=c,synchronize_overlays=lambda:True)
    api=object.__new__(DesktopApi)
    api._native_strictbot_controller=lambda:controller
    api._overlay_controller_bound=None
    api._is_visible=False
    api._window=SimpleNamespace(native=SimpleNamespace(Visible=False,WindowState='Normal'))
    api.coordinate_hourly_tasks([row])
    assert not api.request_hourly_overlay(row['activity_id'],2)['granted']
    c.robot_started(1)
    assert c.snapshot()['ROBOT_VISIBLE']


def test_scheduler_deferral_is_not_recorded_as_a_visible_or_completed_submission(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from tests.test_hourly_popup_delivery_recovery import Store, Window, hourly, DAY
    from windows.submission_window_manager import SubmissionWindowManager
    db=Store(str(tmp_path/'isolated.db'))
    win=Window()
    c, _, _=setup()
    def coordinate(snapshot):
        return c.reconcile(next((r for r in snapshot if r['type']=='HOURLY' and r['reminder_phase']=='OPEN'),None))
    api=SimpleNamespace(_window=win,coordinate_hourly_tasks=coordinate,
                        hourly_overlay_submitted=c.submitted)
    mgr=SubmissionWindowManager(db=db)
    mgr.desktop_api=api
    row=hourly(11)
    db.save(row)
    c.robot_started(1)
    with patch.object(mgr,'_task_delivery_fallback') as fallback:
        for _ in range(10):mgr.reconcile_task_reminders([row],reference_now=DAY.replace(hour=10,minute=55))
        assert not win.calls and db.visible()==0 and not fallback.called
        assert c.snapshot()['HOURLY_ALERT_VISIBLE']
        c.open_pending()
        mgr.reconcile_task_reminders([row],reference_now=DAY.replace(hour=10,minute=55))
        assert mgr._task_delivery_done.wait(4)
        assert db.visible()==1 and len(win.calls)==1
        mgr.dismiss_submission_form(activity_id=row['activity_id'],schedule_version=1)
        assert not c.snapshot()['HOURLY_WINDOW_PENDING']
    with db._connect() as conn:
        assert conn.execute('SELECT value FROM score_sentinel').fetchone()[0]==17


def test_restoring_hourly_cannot_resurrect_a_verified_exited_native_target():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from windows.restriction_manager import YouTubeRestrictionManager
    from windows.restriction_popup import EntrySession
    manager=object.__new__(YouTubeRestrictionManager)
    manager._active_native_target={'action':'CLOSE_APP','process_identity':{'root':{'pid':123}}}
    manager._is_front_popup_active=True
    manager.lock_screen_visible=True
    manager._popup_controller=Mock()
    manager._entry_session=EntrySession()
    manager._entry_session.observe(('vlc',123))
    with patch('windows.restriction_manager.verify_application_exit',return_value=False):
        assert not manager._reconcile_native_warning_exit()
        assert manager._is_front_popup_active
    with patch('windows.restriction_manager.verify_application_exit',return_value=True):
        assert manager._reconcile_native_warning_exit()
        assert manager._entry_session.identity is None
        assert not manager._is_front_popup_active and not manager.lock_screen_visible
        manager._popup_controller.close.assert_called_once()
