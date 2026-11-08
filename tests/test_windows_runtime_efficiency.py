"""Isolated runtime/presentation regression checks; never launch or stop the user's app."""
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
import ntpath
import subprocess
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace
from typing import Optional, Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from core.time_window_engine import IST, dt_to_iso
from windows.exit_authentication import verify_exit_key
from windows.task_reminder_plan import build_reminder_plan
from windows.submission_window_manager import SubmissionWindowManager
from windows.notification_service import StrictNotificationService
from windows.ram_optimizer import RamAndCacheOptimizer


NOW = datetime(2026, 10, 16, 20, 25, tzinfo=IST)


def activity(kind="GYM", **changes):
    row = dict(activity_id="test-" + kind, type=kind, title=kind + " proof", status="SCHEDULED",
               schedule_version=1, submission_eligible=True,
               window_start=dt_to_iso(NOW + timedelta(minutes=5)),
               window_end=dt_to_iso(NOW + timedelta(minutes=65)),
               notification_time=dt_to_iso(NOW + timedelta(minutes=4)))
    row.update(changes)
    return row


@pytest.mark.parametrize("value,allowed", [("1234", True), ("1234 ", False), (" 1234", False),
    ("1984", False), ("", False), (1234, False), (None, False), ("1234\n", False)])
def test_exit_key_is_exact(value, allowed):
    assert verify_exit_key(value) is allowed


def desktop_api_class():
    # Load just the API class so tests cannot register startup, create GUI/CLR
    # objects, configure the user's log files, or open production records.
    source = Path("desktop_app.py").read_text(encoding="utf-8-sig")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "DesktopApi")
    namespace = {"threading": threading, "logger": logging.getLogger("isolated-exit-test")}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "desktop_app.py", "exec"), namespace)
    return namespace["DesktopApi"]


def test_wrong_key_keeps_workers_and_audit_untouched():
    api = desktop_api_class()(stop_event=threading.Event())
    api._db = MagicMock()
    with patch.object(api, "_begin_shutdown") as stop:
        assert not api.exit_app("wrong")["success"]
    stop.assert_not_called()
    assert not api.is_exiting and not api._stop_event.is_set()
    api._db.record_audit_log.assert_not_called()


def test_correct_key_starts_quiz_without_shutdown_or_exit_audit():
    api = desktop_api_class()()
    api._db = MagicMock()
    api._exit_challenge = MagicMock()
    api._exit_challenge.start.return_value = {'challenge_id': 'fixture'}
    with patch.object(api, "_begin_shutdown") as stop, patch.object(api, 'show_dashboard'):
        assert api.exit_app("1234")["status"] == 'challenge_required'
    stop.assert_not_called()
    api._exit_challenge.start.assert_called_once_with('1234')
    api._db.record_audit_log.assert_not_called()


def test_close_hides_without_prompt_or_stopping_process():
    api = desktop_api_class()(stop_event=threading.Event())
    with patch.object(api, "hide_to_tray", return_value={"success": True}) as hide:
        assert api.close()["success"]
    hide.assert_called_once_with(show_first_close_tip=True)
    assert not api._stop_event.is_set()


def test_os_session_end_does_not_request_authentication():
    api = desktop_api_class()()
    with patch.object(api, "_begin_shutdown") as stop, patch("windows.exit_authentication.prompt_exit_key") as prompt:
        api._handle_windows_session_end()
    stop.assert_called_once()
    prompt.assert_not_called()


@pytest.mark.parametrize("kind", ["GYM", "TRAVEL"])
def test_five_minute_reminders_do_not_open_submissions(kind):
    row = activity(kind)
    assert build_reminder_plan([row], NOW - timedelta(seconds=1))[0] == []
    rows, boundary = build_reminder_plan([row], NOW)
    assert rows[0]["reminder_phase"] == "UPCOMING"
    assert not rows[0]["can_submit"]
    assert boundary == NOW + timedelta(minutes=5)
    assert row == activity(kind)  # read-only planner


@pytest.mark.parametrize("kind", ["HOURLY", "WAKE_UP", "BATH", "CUSTOM"])
def test_other_categories_keep_configured_reminder_time(kind):
    row = activity(kind)
    assert build_reminder_plan([row], NOW)[0] == []
    assert len(build_reminder_plan([row], NOW + timedelta(minutes=4))[0]) == 1


@pytest.mark.parametrize("offset,can_submit", [(-1, False), (0, True), (3599, True), (3600, False)])
def test_half_open_submission_boundaries(offset, can_submit):
    row = activity(status="OPEN")
    at = NOW + timedelta(minutes=5, seconds=offset)
    rows, _ = build_reminder_plan([row], at)
    assert bool(rows and rows[0]["can_submit"]) == can_submit


@pytest.mark.parametrize("status", ["EXCUSED", "CANCELLED", "BLOCKED_WAKEUP", "EXPIRED", "SKIPPED_PRE_GATE"])
def test_ineligible_occurrences_are_not_presented(status):
    assert build_reminder_plan([activity(status=status)], NOW)[0] == []
    assert build_reminder_plan([activity(submission_eligible=False)], NOW)[0] == []


def test_review_remains_visible_after_deadline_and_never_enables_resubmission():
    rows, _ = build_reminder_plan([activity(status="VALIDATING")], NOW + timedelta(hours=2))
    assert rows[0]["reminder_phase"] == "REVIEW_PENDING"
    assert not rows[0]["can_submit"]


def test_overlapping_categories_retained_and_current_open_precedes_future():
    rows, _ = build_reminder_plan([activity("TRAVEL"), activity("GYM", status="OPEN", window_start=dt_to_iso(NOW))], NOW)
    assert len(rows) == 2
    assert rows[0]["type"] == "GYM"


class IsolatedStore:
    def __init__(self, path): self.path = path
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()


def test_reminders_deduplicate_restart_clock_replay_and_refresh_only_changed_task(tmp_path):
    import json
    db = IsolatedStore(str(tmp_path / "reminders.db"))
    class Window:
        native = SimpleNamespace(Visible=True, WindowState="Normal")
        def __init__(self): self.calls = []
        def evaluate_js(self, code):
            marker = "return window.deliverTaskSubmissionReminders("
            if marker not in code: return None
            payload = json.JSONDecoder().raw_decode(code.split(marker, 1)[1])[0]
            self.calls.append(payload)
            return {"rendered":True,"visible":True,"displayed_keys":list(payload["delivery_keys"])}
    window = Window()
    api = SimpleNamespace(_window=window, show_dashboard=lambda **kwargs: {"success":True})
    manager = SubmissionWindowManager(db=db)
    manager.desktop_api = api
    manager.reconcile_task_reminders([activity("GYM"), activity("TRAVEL")], NOW)
    assert manager._task_delivery_done.wait(3)
    assert window.calls[-1]["alert"]
    manager.reconcile_task_reminders([activity("GYM"), activity("TRAVEL")], NOW)
    assert len(window.calls) == 1
    restored = SubmissionWindowManager(db=db)
    restored.desktop_api = SimpleNamespace(_window=Window(), show_dashboard=api.show_dashboard)
    restored.reconcile_task_reminders([activity("GYM"), activity("TRAVEL")], NOW)
    assert restored._task_delivery_done.wait(3)
    # A fresh DOM restores its active reminder once; it does not create another logical event.
    assert restored.desktop_api._window.calls[-1]["alert"]
    restored.reconcile_task_reminders([activity("GYM", status="VALIDATING"), activity("TRAVEL")], NOW)
    assert restored._task_delivery_done.wait(3)
    assert len(restored._task_snapshot) == 2
    assert restored._task_snapshot[0]["activity_id"] != restored._task_snapshot[1]["activity_id"]
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM idempotent_event_log").fetchone()[0] == 2


def test_schedule_change_changes_reminder_identity():
    first = build_reminder_plan([activity()], NOW)[0][0]
    changed = build_reminder_plan([activity(schedule_version=2)], NOW)[0][0]
    assert first["reminder_identity"] != changed["reminder_identity"]


def test_automatic_ram_cleanup_keeps_hot_caches_and_resident_pages():
    db, restrictions = MagicMock(), MagicMock()
    optimizer = RamAndCacheOptimizer(db=db, restriction_mgr=restrictions)
    with patch("windows.ram_optimizer.gc.collect") as collect, patch("windows.ram_optimizer.ctypes.windll") as native:
        optimizer.optimize(force=False)
    collect.assert_called_once()
    db._connect.assert_not_called()
    restrictions.clear_caches.assert_not_called()
    native.psapi.EmptyWorkingSet.assert_not_called()


def test_locked_sleep_for_optimizer_is_interruptible_and_no_startup_trim():
    optimizer = RamAndCacheOptimizer()
    optimizer._stop_event.set()
    with patch.object(optimizer, "optimize") as optimize:
        optimizer._auto_optimize_loop(90)
    optimize.assert_not_called()


def test_native_mutex_blocks_even_when_primary_is_not_yet_responsive():
    import windows.single_instance_manager as sim
    import uuid
    if sys.platform != "win32": pytest.skip("Windows native mutex check")
    saved = sim._GLOBAL_MUTEX_HOLDER
    primary = None
    with patch.object(sim, "MUTEX_NAME", "Local\\StrictRecords_Test_" + uuid.uuid4().hex), \
         patch.object(sim, "bring_primary_instance_to_front"), patch.object(sim, "notify_user_duplicate_blocked"), \
         patch.object(sim, "is_another_instance_running", side_effect=AssertionError("No heuristic race")):
        sim._GLOBAL_MUTEX_HOLDER = None
        try:
            sim.enforce_single_instance_or_exit()
            primary = sim._GLOBAL_MUTEX_HOLDER
            assert primary
            sim._GLOBAL_MUTEX_HOLDER = None
            with pytest.raises(SystemExit): sim.enforce_single_instance_or_exit()
        finally:
            sim._GLOBAL_MUTEX_HOLDER = primary
            sim.release_single_instance()
            sim._GLOBAL_MUTEX_HOLDER = saved


@pytest.mark.parametrize("frozen", [True, False])
def test_startup_uses_installed_executable_or_pythonw_and_one_canonical_entry(frozen):
    rows = {"ExamProctor": "obsolete"}
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=2, KEY_SET_VALUE=3, REG_SZ=1,
        OpenKey=lambda *args: Key(), CreateKeyEx=lambda *args: Key(),
        DeleteValue=lambda key, name: rows.pop(name, None),
        SetValueEx=lambda key, name, reserved, kind, value: rows.__setitem__(name, value))
    def query(key, name):
        if name not in rows: raise FileNotFoundError()
        return rows[name], 1
    registry.QueryValueEx = query
    method = next(node for cls in ast.parse(Path("windows/agent.py").read_text(encoding="utf-8-sig")).body
                  if isinstance(cls, ast.ClassDef) and cls.name == "StrictRecordsWindowsAgent"
                  for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "configure_windows_startup")
    method.decorator_list = []
    fake_os = SimpleNamespace(name="nt", environ={"LOCALAPPDATA": ""}, path=SimpleNamespace(
        join=ntpath.join, dirname=ntpath.dirname,
        exists=lambda path: path.endswith(("pythonw.exe", "desktop_app.py")),
        isfile=lambda path: False,
    ))
    fake_sys = SimpleNamespace(frozen=frozen, executable=r"C:\Apps\StrictRecords\StrictRecords.exe" if frozen else r"C:\Python\python.exe")
    namespace = dict(os=fake_os, sys=fake_sys, ROOT_DIR=r"C:\Apps\StrictRecords", subprocess=subprocess,
                     Optional=Optional, Any=Any, Dict=Dict)
    exec(compile(ast.Module(body=[method], type_ignores=[]), "windows/agent.py", "exec"), namespace)
    configure = namespace["configure_windows_startup"]
    with patch.dict(sys.modules, {"winreg": registry}):
        result = configure(True)
        assert list(rows) == ["StrictRecords"]
        assert "--background" in result["command"]
        assert ("StrictRecords.exe" in result["command"]) if frozen else ("pythonw.exe" in result["command"] and "desktop_app.py" in result["command"])
        assert ".dist" not in result["command"]
        assert configure(None)["enabled"]
        configure(False)
        assert not rows


def test_tray_does_not_disappear_when_exit_authentication_is_cancelled():
    from windows.tray_manager import StrictRecordsTrayManager
    tray = StrictRecordsTrayManager(on_exit_app=MagicMock(return_value={"success":False}))
    with patch.object(tray, "stop") as stop:
        tray._handle_exit_request(confirm=False)
    stop.assert_not_called()
    tray.on_exit_app.assert_called_once()


def test_native_session_messages_allow_query_and_record_only_confirmed_end():
    if sys.platform != "win32": pytest.skip("Windows session messages")
    import ctypes
    import time
    from windows.shutdown_monitor import WindowsShutdownMonitor, WM_QUERYENDSESSION, WM_ENDSESSION
    monitor = WindowsShutdownMonitor()
    monitor._running = True
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
        ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    try:
        with patch.object(monitor, "on_shutdown_detected") as detected:
            monitor._start_hidden_window_thread()
            deadline = time.monotonic() + 5
            while not monitor._hwnd and time.monotonic() < deadline:
                threading.Event().wait(.02)
            assert monitor._hwnd, "Isolated native listener was not created"
            result = ctypes.c_size_t()
            assert user32.SendMessageTimeoutW(monitor._hwnd, WM_QUERYENDSESSION, 0, 0, 2, 1000, ctypes.byref(result))
            assert result.value == 1
            detected.assert_not_called()
            assert user32.SendMessageTimeoutW(monitor._hwnd, WM_ENDSESSION, 0, 0, 2, 1000, ctypes.byref(result))
            detected.assert_not_called()
            assert user32.SendMessageTimeoutW(monitor._hwnd, WM_ENDSESSION, 1, 0, 2, 1000, ctypes.byref(result))
            deadline = time.monotonic() + 2
            while not detected.called and time.monotonic() < deadline:
                threading.Event().wait(.02)
            detected.assert_called_once()
    finally:
        monitor.stop()
        if monitor._window_thread: monitor._window_thread.join(timeout=2)
    assert not monitor._window_thread.is_alive()


@pytest.mark.parametrize("bind_fails", [False, True])
def test_server_owns_one_shared_scheduler_and_closes_every_worker_once(bind_fails):
    import os
    class Server:
        def __init__(self, address, handler):
            if bind_fails: raise OSError("isolated duplicate bind")
        def server_close(self): pass
    create = next(node for node in ast.parse(Path("core/server.py").read_text(encoding="utf-8-sig")).body
                  if isinstance(node, ast.FunctionDef) and node.name == "create_server")
    create.returns = None
    for arg in create.args.args: arg.annotation = None
    handler = SimpleNamespace(desktop_api=None)
    db = MagicMock(); db.get_focus_settings.return_value = {}
    engine, agent, service, manager = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    db_constructor, scoring_constructor, agent_constructor = MagicMock(), MagicMock(), MagicMock()
    restriction, optimizer, strict_session = MagicMock(), MagicMock(), MagicMock()
    stop_discovery = MagicMock()
    namespace = dict(ThreadingHTTPServer=Server, StrictRecordsRequestHandler=handler,
        StrictRecordsDB=db_constructor, ScoringEngine=scoring_constructor,
        StrictRecordsWindowsAgent=agent_constructor, ConnectionPipelineManager=MagicMock(),
        StrictSessionManager=MagicMock(return_value=strict_session),
        YouTubeRestrictionManager=MagicMock(return_value=restriction),
        RamAndCacheOptimizer=MagicMock(return_value=optimizer), ensure_motivation_wav=MagicMock(),
        _start_udp_discovery_responder=MagicMock(return_value=stop_discovery),
        EventBus=SimpleNamespace(get_instance=lambda: None), ROOT_DIR="isolated", os=os,
        logger=logging.getLogger("isolated-server"))
    exec(compile(ast.Module(body=[create], type_ignores=[]), "core/server.py", "exec"), namespace)
    with patch("windows.notification_service.StrictNotificationService", return_value=service), \
         patch.object(SubmissionWindowManager, "get_instance", return_value=manager):
        if bind_fails:
            with pytest.raises(OSError): namespace["create_server"](db=db, engine=engine, win_agent=agent)
            service.start.assert_not_called()
            restriction.start_watcher.assert_not_called()
        else:
            server = namespace["create_server"](db=db, engine=engine, win_agent=agent)
            assert handler.engine is engine and handler.db is db and handler.win_agent is agent
            service.start.assert_called_once()
            server.server_close(); server.server_close()
            for stop in (service.stop, restriction.stop_watcher, optimizer.stop_auto_optimizer,
                         strict_session.shutdown, stop_discovery): stop.assert_called_once()
    db_constructor.assert_not_called()
    scoring_constructor.assert_not_called()
    agent_constructor.assert_not_called()


@pytest.mark.parametrize("growth,collections", [(0, 0), (80, 1)])
def test_automatic_cleanup_requires_actual_growth_or_memory_pressure(growth, collections):
    optimizer = RamAndCacheOptimizer()
    stop = MagicMock()
    stop.wait.return_value = False
    stop.is_set.side_effect = [False, False, True]
    optimizer._stop_event = stop
    with patch.object(optimizer, "get_memory_stats", side_effect=[{"rss_mb":300}, {"rss_mb":300 + growth}]), \
         patch.object(optimizer, "optimize") as optimize, \
         patch("psutil.virtual_memory", return_value=SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3)):
        optimizer._auto_optimize_loop(90)
    assert optimize.call_count == collections
    assert stop.wait.call_args_list[0].args == (60.0,)
    assert stop.wait.call_args_list[1].args == (300.0,)


@pytest.mark.parametrize("reason,original_called", [(3, False), (4, False), (0, True)])
def test_os_close_bypasses_tray_handler_before_page_load(reason, original_called):
    tree = ast.parse(Path("desktop_app.py").read_text(encoding="utf-8-sig"))
    wrapper = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_safe_form_closing")
    original = MagicMock()
    namespace = {"WinForms":SimpleNamespace(CloseReason=SimpleNamespace(WindowsShutDown=3, TaskManagerClosing=4)),
                 "_original_form_closing":original}
    exec(compile(ast.Module(body=[wrapper],type_ignores=[]),"desktop_app.py","exec"),namespace)
    event = SimpleNamespace(CloseReason=reason, Cancel=True)
    namespace["_safe_form_closing"](object(), object(), event)
    assert original.called is original_called
    if not original_called: assert event.Cancel is False


def test_repeated_monitoring_reads_reuse_confirmed_score_but_explicit_checks_refresh():
    import os
    clock = SimpleNamespace(value=100.0)
    tree = ast.parse(Path("windows/restriction_manager.py").read_text(encoding="utf-8-sig"))
    method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "YouTubeRestrictionManager"
                  for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "get_points_status")
    method.returns = None
    for arg in method.args.args: arg.annotation = None
    namespace = {"time":SimpleNamespace(time=lambda:clock.value), "ROOT_DIR":"isolated", "os":os}
    exec(compile(ast.Module(body=[method],type_ignores=[]),"windows/restriction_manager.py","exec"),namespace)
    db, scoring = MagicMock(), MagicMock()
    db.get_settings.return_value.reward_threshold = 80
    scoring.recalculate_daily_score.side_effect = [{"final_score":-30},{"final_score":80},{"final_score":70},{"final_score":60}]
    manager = SimpleNamespace(_points_cache=None,_points_cache_time=0,simulated_test_mode=False,db=db,engine=scoring)
    read = lambda force=False: namespace["get_points_status"](manager,force)
    assert read()["current_score"] == -30
    clock.value += 1
    assert read()["current_score"] == -30
    assert scoring.recalculate_daily_score.call_count == 1
    assert read(True)["current_score"] == 80
    clock.value += 4
    assert read()["current_score"] == 70
    clock.value = 90  # Clock change cannot pin a future-dated cache forever.
    assert read()["current_score"] == 60


def test_watcher_does_not_force_recalculation_for_unchanged_foreground():
    # Inspect the actual worker decision with no UI/native actions or ledger writes.
    tree = ast.parse(Path("windows/restriction_manager.py").read_text(encoding="utf-8-sig"))
    method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "YouTubeRestrictionManager"
                  for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "check_and_enforce")
    point_call = next(node for node in ast.walk(method) if isinstance(node,ast.Call) and getattr(node.func,"attr",None)=="get_points_status")
    assert any(keyword.arg=="force" and isinstance(keyword.value,ast.Name) and keyword.value.id=="new_entry" for keyword in point_call.keywords)


def test_ordinary_shutdown_does_not_touch_unowned_os_or_network_settings():
    tree = ast.parse(Path("windows/strict_session_manager.py").read_text(encoding="utf-8-sig"))
    method = next(node for cls in tree.body if isinstance(cls,ast.ClassDef) and cls.name=="StrictSessionManager"
                  for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=="shutdown")
    namespace = {"threading":threading, "StrictSessionState":SimpleNamespace(NORMAL="NORMAL")}
    exec(compile(ast.Module(body=[method],type_ignores=[]),"windows/strict_session_manager.py","exec"),namespace)
    network = MagicMock(is_active=False)
    manager = SimpleNamespace(_stop_event=threading.Event(),_uninstall_keyboard_hook=MagicMock(),
        _restore_registry_policies=MagicMock(),_show_taskbar=MagicMock(),session_id=None,state="NORMAL",
        network_policy_manager=network,_supervisor_thread=None)
    namespace["shutdown"](manager)
    assert manager._stop_event.is_set()
    manager._restore_registry_policies.assert_not_called()
    manager._show_taskbar.assert_not_called()
    network.restore_network.assert_not_called()
