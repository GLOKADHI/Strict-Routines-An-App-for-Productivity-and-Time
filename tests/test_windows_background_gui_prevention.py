"""
Unit and integration tests verifying that StrictRecords Windows EXE remains quietly
in the background/system tray without unsolicited GUI popups or repeated spam.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from core.time_window_engine import IST, TimeWindowEngine, dt_to_iso
from windows.submission_window_manager import SubmissionWindowManager, SubmissionState
from windows.notification_service import StrictNotificationService, NotificationCenter
from windows.single_instance_manager import enforce_single_instance_or_exit

DAY = datetime(2026, 10, 20, tzinfo=IST)


def hourly(hour, **extra):
    anchor = DAY.replace(hour=hour)
    row = dict(
        activity_id=f"hourly-bg-{hour}",
        type="HOURLY",
        title=f"Hourly Check-In {hour:02d}:00",
        date=DAY.date().isoformat(),
        status="OPEN",
        schedule_version=1,
        submission_eligible=True,
        scheduled_time=dt_to_iso(anchor),
        window_start=dt_to_iso(anchor - timedelta(minutes=10)),
        window_end=dt_to_iso(anchor + timedelta(minutes=10)),
        notification_time=dt_to_iso(anchor - timedelta(minutes=11)),
        details_json=json.dumps({"hour": hour, "hour_slot": f"{hour-1:02d}:00-{hour:02d}:00"}),
    )
    row.update(extra)
    return row


class IsolatedStore:
    def __init__(self, path):
        self.path = path
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE scheduled_activities(
                    activity_id TEXT PRIMARY KEY,
                    account_id TEXT DEFAULT 'user_lok',
                    date TEXT,
                    type TEXT,
                    title TEXT,
                    scheduled_time TEXT,
                    window_start TEXT,
                    window_end TEXT,
                    notification_time TEXT,
                    status TEXT,
                    schedule_version INTEGER DEFAULT 1,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS wake_up_sessions(
                    session_id TEXT PRIMARY KEY,
                    active_audio_output_verified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS score_sentinel(value INTEGER);
                CREATE TABLE IF NOT EXISTS notification_history(
                    notification_key TEXT PRIMARY KEY,
                    activity_id TEXT,
                    event_type TEXT,
                    sent_at TEXT
                );
                CREATE TABLE IF NOT EXISTS notification_center(
                    notification_key TEXT PRIMARY KEY,
                    title TEXT,
                    message TEXT,
                    category TEXT,
                    activity_id TEXT,
                    event_type TEXT,
                    source TEXT,
                    timestamp TEXT,
                    is_read INTEGER DEFAULT 0,
                    is_dismissed INTEGER DEFAULT 0
                );
                """
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get_settings(self, account_id="user_lok"):
        return {"account_id": account_id, "wake_up_time": "07:00"}

    def get_confirmed_routine_schedule(self, date_str, account_id="user_lok"):
        return None

    def save_activity(self, row):
        with self._connect() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, date, type, title, scheduled_time,
                    window_start, window_end, notification_time, status, schedule_version, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["activity_id"],
                    row.get("account_id", "user_lok"),
                    row.get("date", DAY.date().isoformat()),
                    row.get("type", "HOURLY"),
                    row.get("title", "Hourly"),
                    row.get("scheduled_time"),
                    row.get("window_start"),
                    row.get("window_end"),
                    row.get("notification_time"),
                    row.get("status", "OPEN"),
                    int(row.get("schedule_version", 1)),
                    row.get("details_json", "{}"),
                ),
            )


class FakeHiddenWindow:
    def __init__(self):
        loaded = threading.Event()
        loaded.set()
        self.events = SimpleNamespace(loaded=loaded)
        self.native = SimpleNamespace(Visible=False, WindowState="Minimized")
        self.calls = []

    def evaluate_js(self, code):
        self.calls.append(code)
        return {"rendered": False, "visible": False}


@pytest.fixture
def bg_setup(tmp_path):
    db = IsolatedStore(str(tmp_path / "bg_test.db"))
    win = FakeHiddenWindow()
    manager = SubmissionWindowManager(db=db)
    show_dash_mock = Mock(return_value={"success": True})
    manager.desktop_api = SimpleNamespace(_window=win, show_dashboard=show_dash_mock, is_exiting=False)
    with patch.object(manager, "_task_delivery_fallback") as fallback_mock, \
         patch.object(manager, "_gate_allows_activity", return_value=True):
        yield db, win, manager, show_dash_mock, fallback_mock


def test_background_window_reconcile_does_not_open_gui(bg_setup):
    """A routine task boundary reconciliation while minimized to tray MUST NOT open the GUI."""
    db, win, mgr, show_dash, fallback = bg_setup
    row = hourly(10)
    db.save_activity(row)

    at = DAY.replace(hour=9, minute=50)
    mgr.reconcile_task_reminders([row], reference_now=at)

    # Window MUST NOT be brought to foreground
    show_dash.assert_not_called()
    assert not win.native.Visible
    fallback.assert_called_once()


def test_open_submission_window_routine_does_not_pop_up(bg_setup):
    """When an hourly submission window opens at :00, it must update state without popping up the window."""
    db, win, mgr, show_dash, _ = bg_setup
    row = hourly(11)
    db.save_activity(row)

    res = mgr.open_submission_window(
        activity_id=row["activity_id"],
        slot="10:00 - 11:00",
        title="Hourly check-in",
        window_start=row["window_start"],
        window_end=row["window_end"],
        details={},
        force_focus=False,  # Routine background open
        schedule_version=1,
    )

    assert res["action"] == "OPENED"
    assert mgr.state == SubmissionState.OPEN
    show_dash.assert_not_called()
    assert not win.native.Visible


def test_warning_1m_does_not_pop_up_gui(bg_setup):
    """At 1 minute before window close, WARNING_1M must NOT bring the window to the foreground."""
    db, win, mgr, show_dash, _ = bg_setup
    row = hourly(11)
    db.save_activity(row)

    # Set window open
    mgr.open_submission_window(
        activity_id=row["activity_id"],
        slot="10:00 - 11:00",
        title="Hourly check-in",
        window_start=row["window_start"],
        window_end=row["window_end"],
        force_focus=False,
    )

    # 1-minute warning reminder arrives
    warn_at = DAY.replace(hour=11, minute=9)
    twe = TimeWindowEngine(db)

    with patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=mgr), \
         patch.object(twe, "ensure_activities_in_db", return_value=[row]):
        twe.get_pending_notifications(DAY.date().isoformat(), reference_now=warn_at)

    # show_dashboard must NOT be called by the 1-minute warning tick!
    show_dash.assert_not_called()
    assert not win.native.Visible


def test_clicking_toast_callback_explicitly_restores_gui(bg_setup):
    """Clicking the toast notification action DOES restore the GUI as expected."""
    _, _, mgr, show_dash, _ = bg_setup

    # User clicks notification action
    mgr.bring_window_to_foreground(open_hourly_overlay=True)
    show_dash.assert_called_once_with(open_hourly_if_active=True)


def test_dismissed_notification_is_not_redisplayed_on_next_tick(tmp_path):
    """A dismissed notification in notification_center must NOT be re-toasted on subsequent polls."""
    db = IsolatedStore(str(tmp_path / "dismiss_test.db"))

    # Seed an already dismissed notification in DB
    with db._connect() as c:
        c.execute(
            """
            INSERT INTO notification_center (
                notification_key, title, message, category, is_dismissed, is_read
            ) VALUES ('test-key-1', 'Test', 'Dismissed message', 'TASKS', 1, 1)
            """
        )

    service = StrictNotificationService(db=db)
    toasted = []

    with patch("windows.notification_service._try_toast_notification", side_effect=lambda **kw: toasted.append(kw)):
        # Try ingesting the same notification key
        delivered = service.ingest_pending_notifications([
            {
                "notification_key": "test-key-1",
                "event_type": "HOURLY",
                "message": "Dismissed message",
                "activity_id": "act-1",
            }
        ])

    assert delivered == 0
    assert len(toasted) == 0


def test_single_instance_background_launch_does_not_focus_primary():
    """Launching desktop_app.py --background when an instance is already running exits silently."""
    with patch("sys.platform", "win32"), \
         patch("ctypes.WinDLL") as mock_windll, \
         patch("windows.single_instance_manager.bring_primary_instance_to_front") as mock_bring_front, \
         patch("windows.single_instance_manager.notify_user_duplicate_blocked") as mock_notify:

        kernel32_mock = Mock()
        kernel32_mock.GetLastError.return_value = 183
        mock_windll.return_value = kernel32_mock

        with patch("ctypes.get_last_error", return_value=183):
            with pytest.raises(SystemExit):
                enforce_single_instance_or_exit(background=True)

        mock_bring_front.assert_not_called()
        mock_notify.assert_not_called()
