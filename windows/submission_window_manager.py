"""
STRICTRECORDS — Single-Instance Submission Window Manager
Controls the dedicated full-screen hourly submission experience on Windows.
Guarantees:
1. Exactly one active submission experience exists at any given time per (activity_id, schedule_version).
2. Explicit State Machine:
   UPCOMING -> OPEN -> SUBMITTED -> VALIDATING -> VERIFIED / REJECTED
   or
   OPEN -> MISSED -> CLOSED
3. A window can be displayed in interactive input mode ONLY when in OPEN state.
   Once in SUBMITTED, VALIDATING, VERIFIED, REJECTED, MISSED, or CLOSED, it never shows the input form for that activity ID again.
4. All triggers (scheduler, notifications, WebSocket, resume, startup, Android sync) pass through a persisted idempotent handler.
5. When proof is submitted from Android or Windows:
   - Dismisses/closes the edit form for that activity ID immediately.
   - Transitions to compact received status ("Submission received — AI review processing").
   - Continues AI review in the background and updates to approved/rejected on completion.
"""

import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger("SubmissionWindowManager")


class SubmissionState(str, Enum):
    IDLE = "IDLE"
    UPCOMING = "UPCOMING"
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    MISSED = "MISSED"
    CLOSED = "CLOSED"
    # Legacy aliases for backward compatibility with existing callers/tests
    RECEIVED = "SUBMITTED"
    UNDER_REVIEW = "VALIDATING"
    REVIEWED = "VERIFIED"


TERMINAL_OR_SUBMITTED_STATES = {
    "SUBMITTED",
    "VALIDATING",
    "AI_PROVISIONAL",
    "VERIFIED",
    "REJECTED",
    "REJECTED_LATE",
    "MISSED",
    "CLOSED",
    "RECEIVED",
    "UNDER_REVIEW",
    "REVIEWED",
    "COMPLETED",
}


class WindowVisibilityState(str, Enum):
    HIDDEN = "HIDDEN"
    OPEN_FULLSCREEN = "OPEN_FULLSCREEN"
    MINIMIZED_PILL = "MINIMIZED_PILL"
    CLOSED = "CLOSED"


class SubmissionWindowManager:
    _instance: Optional["SubmissionWindowManager"] = None
    _lock = threading.Lock()

    def __init__(self, db=None, event_bus=None):
        self.db = db
        self.event_bus = event_bus
        self.state: SubmissionState = SubmissionState.IDLE
        self.window_visibility_state: WindowVisibilityState = WindowVisibilityState.HIDDEN
        self.active_activity_id: Optional[str] = None
        self.active_schedule_version: int = 1
        self.active_slot: str = ""
        self.active_title: str = ""
        self.active_bounds: str = ""
        self.deadline_iso: Optional[str] = None
        self.activity_data: Dict[str, Any] = {}
        self.review_in_progress: bool = False
        self.last_review_data: Optional[Dict[str, Any]] = None
        self.desktop_api = None
        self._opened_activity_ids: Set[str] = set()
        self._opened_identities: Set[Tuple[str, int]] = set()
        self._processed_event_ids: Set[str] = set()
        self._state_lock = threading.RLock()
        self._task_snapshot = []
        self._task_signature = None
        self._task_alerts = set()
        self._task_rendered_signature = None
        self._task_delivery_generation = 0
        self._task_delivery_inflight = False
        self._task_delivery_started = 0.0
        self._task_last_error = None
        self._task_delivery_done = threading.Event()
        self._task_delivery_done.set()
        self._task_retry_at = None
        self._task_retry_count = 0
        self._task_reference_now = None
        self._ensure_tables_and_hydrate()

    @classmethod
    def get_instance(cls, db=None, event_bus=None) -> "SubmissionWindowManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db=db, event_bus=event_bus)
            else:
                need_hydrate = False
                if db and cls._instance.db is None:
                    cls._instance.db = db
                    need_hydrate = True
                if event_bus and cls._instance.event_bus is None:
                    cls._instance.event_bus = event_bus
                if need_hydrate:
                    cls._instance._ensure_tables_and_hydrate()
            return cls._instance

    def _ensure_tables_and_hydrate(self) -> None:
        """Ensures persistence tables exist and loads previously opened/handled activities across restarts."""
        if not self.db:
            return
        try:
            with self.db._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS submission_window_state (
                        activity_id TEXT NOT NULL,
                        schedule_version INTEGER NOT NULL DEFAULT 1,
                        state TEXT NOT NULL DEFAULT 'UPCOMING',
                        window_start TEXT,
                        window_end TEXT,
                        opened_at TEXT,
                        dismissed_at TEXT,
                        submitted_at TEXT,
                        source_device TEXT,
                        last_event_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (activity_id, schedule_version)
                    );
                    CREATE TABLE IF NOT EXISTS idempotent_event_log (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        activity_id TEXT NOT NULL DEFAULT '',
                        schedule_version INTEGER NOT NULL DEFAULT 1,
                        processed_at TEXT NOT NULL
                    );
                    """
                )
                rows = conn.execute(
                    "SELECT activity_id, schedule_version, state, opened_at FROM submission_window_state"
                ).fetchall()
                for r in rows:
                    aid = r["activity_id"]
                    ver = int(r["schedule_version"] or 1)
                    st = r["state"]
                    if r["opened_at"] or st in TERMINAL_OR_SUBMITTED_STATES or st == "OPEN":
                        self._opened_activity_ids.add(aid)
                        self._opened_identities.add((aid, ver))
        except Exception as e:
            logger.debug(f"_ensure_tables_and_hydrate error: {e}")

    def set_desktop_api(self, api: Any) -> None:
        self.desktop_api = api

    def reconcile_task_reminders(self, activities, reference_now=None):
        """One shared presentation manager; the existing scheduler owns all task state."""
        from core.time_window_engine import now_ist
        from windows.task_reminder_plan import build_reminder_plan
        now = reference_now or now_ist()
        rows, next_boundary = build_reminder_plan(activities, now)
        # Only fields rendered by the shared panel participate in change detection.
        fields = ("activity_id", "type", "title", "scheduled_time", "window_start", "window_end", "status",
                  "schedule_version", "reminder_identity", "reminder_phase", "can_submit", "details_json",
                  "attendance_status", "validation_status", "submitted_at", "submission_eligible", "eligibility_status", "eligibility_message")
        snapshot = [{field: row.get(field) for field in fields} for row in rows]
        coordinate = getattr(self.desktop_api, 'coordinate_hourly_tasks', None)
        if coordinate:
            coordinate(snapshot)
        signature = json.dumps(snapshot, sort_keys=True)
        with self._state_lock:
            if signature != self._task_signature:
                self._task_retry_at = None  # A newly due phase must not inherit an idle retry delay.
            if self._task_reference_now and now < self._task_reference_now:
                self._task_retry_at = None  # A wall-clock correction must not postpone recovery.
            self._task_reference_now = now
            self._task_signature = signature
            self._task_snapshot = snapshot
            keys = self._task_delivery_keys(snapshot)
            pending = signature != self._task_rendered_signature or bool(keys)
            due = self._task_retry_at is None or now >= self._task_retry_at
            if not pending:
                self._task_retry_at = None
        if pending and due:
            if self._task_delivery_inflight:
                window = getattr(self.desktop_api, '_window', None)
                if keys and window and not self._task_native_visible(window):
                    self._task_delivery_fallback(keys, snapshot)
                elif time.monotonic() - self._task_delivery_started >= 5:
                    self._task_delivery_fallback(keys, snapshot)
                self._task_retry_at = now + timedelta(seconds=2 if keys else 30)
            else:
                self.present_task_reminders(alert=bool(keys))
        with self._state_lock:
            if pending:
                retry = self._task_retry_at or (now + timedelta(seconds=2))
                if retry <= now:
                    retry = now + timedelta(seconds=2)
                next_boundary = min(next_boundary, retry) if next_boundary else retry
        return next_boundary

    def present_task_reminders(self, alert=False):
        """Queue one UI delivery. Only a visible browser acknowledgement records delivery."""
        try:
            from windows.neo_exam_monitor import NeoExamBrowserMonitor
            if NeoExamBrowserMonitor.get_instance().is_notification_suppression_active():
                return False
        except Exception:
            pass
        coordinate = getattr(self.desktop_api, 'coordinate_hourly_tasks', None)
        if coordinate:
            state = coordinate(self._task_snapshot)
            if state.get('HOURLY_ALERT_VISIBLE'):
                # Native ready notice owns this handoff. Do not acknowledge a
                # hidden form, fallback-toast it, or repeatedly fight for focus.
                return False
        with self._state_lock:
            if self._task_delivery_inflight or getattr(self.desktop_api, "is_exiting", False):
                return False
            snapshot = list(self._task_snapshot)
            signature = self._task_signature
            generation = self._task_delivery_generation
            keys = self._task_delivery_keys(snapshot) if alert else {}
            window = getattr(self.desktop_api, '_window', None)
            is_visible = window is not None and self._task_native_visible(window)
            if not is_visible:
                # When window is hidden or minimized to tray, do NOT force window open!
                # Keep it in background/tray and notify via desktop toast notifications instead.
                from core.time_window_engine import now_ist
                now = self._task_reference_now or now_ist()
                if keys:
                    self._task_delivery_fallback(keys, snapshot)
                    for key, activity_id in keys.items():
                        self._task_alerts.add(key)
                        row = next((r for r in snapshot if r["activity_id"] == activity_id), None)
                        ver = int(row.get("schedule_version") or 1) if row else 1
                        self.mark_event_processed(key, "TASK_POPUP_FALLBACK", activity_id, ver)
                self._task_rendered_signature = signature
                self._task_retry_at = None
                self._task_retry_count = 0
                return False
            self._task_delivery_inflight = True
            self._task_delivery_started = time.monotonic()
            self._task_delivery_done.clear()
            from core.time_window_engine import now_ist
            now = self._task_reference_now or now_ist()
            if now:
                self._task_retry_at = now + timedelta(seconds=2)

        def deliver():
            acknowledged = set()
            rendered = False
            try:
                win = getattr(self.desktop_api, "_window", None)
                if not win:
                    raise RuntimeError("Desktop window is not available")
                if not self._task_native_visible(win):
                    if keys:
                        self._task_delivery_fallback(keys, snapshot)
                        for key, activity_id in keys.items():
                            self._task_alerts.add(key)
                            row = next((r for r in snapshot if r["activity_id"] == activity_id), None)
                            ver = int(row.get("schedule_version") or 1) if row else 1
                            self.mark_event_processed(key, "TASK_POPUP_FALLBACK", activity_id, ver)
                    return
                # A hidden WebView can finish bridge initialization only after its form is shown.
                loaded = getattr(getattr(win, "events", None), "loaded", None)
                if loaded is not None and not loaded.is_set():
                    raise RuntimeError("Dashboard is not loaded yet")
                with self._state_lock:
                    if generation != self._task_delivery_generation or signature != self._task_signature:
                        return
                for row in snapshot:
                    if row["reminder_phase"] == "OPEN" and row["activity_id"] in keys.values():
                        if self.get_persisted_activity_state(row["activity_id"], int(row.get("schedule_version") or 1)) in TERMINAL_OR_SUBMITTED_STATES:
                            raise RuntimeError("Proof state changed before popup delivery; reconciliation required")
                payload = json.dumps({"tasks": snapshot, "alert": bool(keys), "delivery_keys": keys})
                result = win.evaluate_js(
                    "(function(){if(typeof window.deliverTaskSubmissionReminders !== 'function') return null;"
                    f"return window.deliverTaskSubmissionReminders({payload});" + "})()"
                )
                rendered = isinstance(result, dict) and result.get("rendered") is True
                if rendered and result.get("visible") is True and self._task_native_visible(win):
                    acknowledged = set(result.get("displayed_keys") or []) & set(keys)
                    hwnd = getattr(self.desktop_api, "_hwnd", None)
                    if acknowledged and sys.platform == "win32" and hwnd:
                        import ctypes
                        user32 = ctypes.WinDLL("user32", use_last_error=True)
                        user32.GetForegroundWindow.restype = ctypes.c_void_p
                        if user32.GetForegroundWindow() != hwnd:
                            # The rendered surface remains available; notify once if Windows denies focus.
                            self._task_delivery_fallback(keys, snapshot)
                if keys and not acknowledged:
                    raise RuntimeError("Dashboard did not acknowledge a visible reminder")
            except Exception as error:
                self._task_last_error = str(error)
                logger.warning("Task popup delivery remains pending: %s", error)
                if keys and generation == self._task_delivery_generation:
                    self._task_delivery_fallback(keys, snapshot)
            finally:
                with self._state_lock:
                    current = generation == self._task_delivery_generation and signature == self._task_signature
                    if current:
                        if rendered:
                            self._task_rendered_signature = signature
                        for key in acknowledged:
                            row = next(row for row in snapshot if row["activity_id"] == keys[key])
                            self.mark_event_processed(key, "TASK_POPUP_VISIBLE", row["activity_id"], int(row.get("schedule_version") or 1))
                            self._task_alerts.add(key)
                            if row["type"] == "HOURLY" and row["reminder_phase"] == "OPEN":
                                self.active_activity_id = row["activity_id"]
                                self.active_schedule_version = int(row.get("schedule_version") or 1)
                                self.active_title = row.get("title") or "Hourly check-in"
                                self.active_slot = self.active_title
                                self.active_bounds = f"{row['window_start']} - {row['window_end']}"
                                self.deadline_iso = row["window_end"]
                                self.activity_data = row
                                fresh = self.get_persisted_activity_state(row["activity_id"], self.active_schedule_version)
                                if fresh not in TERMINAL_OR_SUBMITTED_STATES:
                                    self.state = SubmissionState.OPEN
                                    self.window_visibility_state = WindowVisibilityState.OPEN_FULLSCREEN
                                    self.review_in_progress = False
                                    self._opened_activity_ids.add(row["activity_id"])
                                    self._opened_identities.add((row["activity_id"], self.active_schedule_version))
                                    self._persist_window_state(row["activity_id"], self.active_schedule_version, "OPEN",
                                        window_start=row["window_start"], window_end=row["window_end"], opened_at=(self._task_reference_now or now).isoformat())
                                else:
                                    self.state = SubmissionState.VERIFIED if fresh in {"VERIFIED", "COMPLETED", "REVIEWED"} else (
                                        SubmissionState.REJECTED if fresh == "REJECTED" else SubmissionState.CLOSED if fresh in {"CLOSED", "MISSED", "REJECTED_LATE"}
                                        else SubmissionState.VALIDATING if fresh in {"VALIDATING", "AI_PROVISIONAL", "UNDER_REVIEW"} else SubmissionState.SUBMITTED)
                                    self.window_visibility_state = WindowVisibilityState.CLOSED
                                    self.review_in_progress = self.state in {SubmissionState.SUBMITTED, SubmissionState.VALIDATING}
                        success = (rendered and set(keys) <= acknowledged) or (keys and set(keys) <= self._task_alerts)
                        if success:
                            self._task_last_error = None
                        self._task_retry_count = 0 if success else min(4, self._task_retry_count + 1)
                        delay = min(30, 2 ** (self._task_retry_count + 1))
                        self._task_retry_at = None if success else (self._task_reference_now or now) + timedelta(seconds=delay)
                    self._task_delivery_inflight = False
                    self._task_delivery_done.set()
                if self.event_bus:
                    self.event_bus.publish("TASK_PRESENTATION_CHANGED", {"delivered": bool(acknowledged)})

        threading.Thread(target=deliver, daemon=True, name="SWM_TaskDelivery").start()
        return True

    def _task_delivery_keys(self, snapshot):
        # Old attempt markers cannot suppress a confirmed delivery. A fresh DOM
        # restores one active surface; a backward clock jump cannot replay its pre-reminder.
        keys = {}
        for row in snapshot:
            phase = row["reminder_phase"]
            if phase not in {"UPCOMING", "OPEN", "REJECTED", "BLOCKED_WAKEUP"}:
                continue
            prefix = f"task-visible-v2:{row['reminder_identity']}:"
            if phase == "UPCOMING" and (prefix + "OPEN" in self._task_alerts or self.is_event_processed(prefix + "OPEN")):
                continue
            key = prefix + phase
            if key not in self._task_alerts:
                keys[key] = row["activity_id"]
        return keys

    def reset_task_presentation(self):
        """A loaded/reloaded WebView has new DOM state; durable task/proof state stays intact."""
        with self._state_lock:
            self._task_delivery_generation += 1
            self._task_alerts.clear()
            self._task_rendered_signature = None
            self._task_retry_at = None
            self._task_retry_count = 0

    def _task_native_visible(self, win):
        native = getattr(win, "native", None)
        hwnd = getattr(self.desktop_api, "_hwnd", None)
        if native is not None and not hwnd:
            try:
                hwnd = int(native.Handle.ToInt64())
            except (AttributeError, TypeError):
                pass
        if sys.platform == "win32" and hwnd:
            import ctypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            for name in ("IsWindowVisible", "IsIconic"):
                getattr(user32, name).argtypes = [ctypes.c_void_p]
                getattr(user32, name).restype = ctypes.c_int
            user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            return bool(user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and user32.MonitorFromWindow(hwnd, 0))
        return bool(native is not None and getattr(native, "Visible", False)
                    and str(getattr(native, "WindowState", "Normal")) not in {"Minimized", "1"})

    def _task_delivery_fallback(self, keys, snapshot):
        if getattr(self.desktop_api, "is_exiting", False):
            return
        from windows.notification_service import _try_toast_notification
        for key, activity_id in keys.items():
            fallback_key = key + ":notification"
            if self.is_event_processed(fallback_key):
                continue
            row = next(row for row in snapshot if row["activity_id"] == activity_id)
            if _try_toast_notification("StrictRecords submission reminder", row.get("title") or row["type"], "TASKS",
                                       on_click=lambda: self.bring_window_to_foreground(open_hourly_overlay=False)):
                self.mark_event_processed(fallback_key, "TASK_POPUP_FALLBACK", activity_id, int(row.get("schedule_version") or 1))

    def _gate_allows_activity(self, activity_id, reference_now=None):
        if not self.db:
            return True
        from core.daily_routine_gate import DailyRoutineGate
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (activity_id,)).fetchone()
        if not row:
            return True
        activity = dict(row)
        gate = DailyRoutineGate(self.db).reconcile(activity["date"], activity.get("account_id", "user_lok"), reference_now)
        return DailyRoutineGate.activity_is_eligible(activity, gate, reference_now=reference_now, for_submission=True)

    def _resolve_schedule_version(self, activity_id: str, schedule_version: Optional[int] = None, details: Optional[Dict[str, Any]] = None) -> int:
        if schedule_version is not None and int(schedule_version) > 0:
            return int(schedule_version)
        if details and details.get("schedule_version"):
            try:
                return int(details["schedule_version"])
            except Exception:
                pass
        if self.db and activity_id:
            try:
                with self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT schedule_version FROM scheduled_activities WHERE activity_id = ?",
                        (activity_id,),
                    ).fetchone()
                    if row and row["schedule_version"]:
                        return int(row["schedule_version"])
            except Exception:
                pass
        return 1

    def _persist_window_state(
        self,
        activity_id: str,
        schedule_version: int,
        state: str,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        opened_at: Optional[str] = None,
        dismissed_at: Optional[str] = None,
        submitted_at: Optional[str] = None,
        source_device: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> None:
        if not self.db or not activity_id:
            return
        now_iso = datetime.now().isoformat()
        try:
            with self.db._connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM submission_window_state WHERE activity_id = ? AND schedule_version = ?",
                    (activity_id, schedule_version),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE submission_window_state SET
                            state = ?,
                            window_start = COALESCE(?, window_start),
                            window_end = COALESCE(?, window_end),
                            opened_at = COALESCE(?, opened_at),
                            dismissed_at = COALESCE(?, dismissed_at),
                            submitted_at = COALESCE(?, submitted_at),
                            source_device = COALESCE(?, source_device),
                            last_event_id = COALESCE(?, last_event_id),
                            updated_at = ?
                        WHERE activity_id = ? AND schedule_version = ?
                        """,
                        (
                            state,
                            window_start,
                            window_end,
                            opened_at,
                            dismissed_at,
                            submitted_at,
                            source_device,
                            event_id,
                            now_iso,
                            activity_id,
                            schedule_version,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO submission_window_state (
                            activity_id, schedule_version, state, window_start, window_end,
                            opened_at, dismissed_at, submitted_at, source_device, last_event_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            activity_id,
                            schedule_version,
                            state,
                            window_start,
                            window_end,
                            opened_at,
                            dismissed_at,
                            submitted_at,
                            source_device,
                            event_id,
                            now_iso,
                            now_iso,
                        ),
                    )
        except Exception as e:
            logger.debug(f"_persist_window_state error: {e}")

    def is_event_processed(self, event_id: str) -> bool:
        """Checks if an event/notification ID was already processed (in-memory + SQLite)."""
        if not event_id:
            return False
        if event_id in self._processed_event_ids:
            return True
        if self.db:
            try:
                with self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT 1 FROM idempotent_event_log WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()
                    if row:
                        self._processed_event_ids.add(event_id)
                        return True
            except Exception:
                pass
        return False

    def mark_event_processed(
        self,
        event_id: str,
        event_type: str,
        activity_id: str = "",
        schedule_version: int = 1,
    ) -> None:
        """Persists a processed event/notification ID to prevent duplicate execution across restarts."""
        if not event_id:
            return
        self._processed_event_ids.add(event_id)
        if self.db:
            try:
                with self.db._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO idempotent_event_log (
                            event_id, event_type, activity_id, schedule_version, processed_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (event_id, event_type, activity_id or "", int(schedule_version or 1), datetime.now().isoformat()),
                    )
            except Exception as e:
                logger.debug(f"mark_event_processed error: {e}")

    def has_opened_activity(self, activity_id: str, schedule_version: Optional[int] = None) -> bool:
        with self._state_lock:
            if schedule_version is not None:
                if (activity_id, int(schedule_version)) in self._opened_identities:
                    return True
            if activity_id in self._opened_activity_ids:
                return True
            if self.db and activity_id:
                try:
                    with self.db._connect() as conn:
                        if schedule_version is not None:
                            row = conn.execute(
                                "SELECT state, opened_at FROM submission_window_state WHERE activity_id = ? AND schedule_version = ?",
                                (activity_id, int(schedule_version)),
                            ).fetchone()
                        else:
                            row = conn.execute(
                                "SELECT state, opened_at FROM submission_window_state WHERE activity_id = ? ORDER BY schedule_version DESC LIMIT 1",
                                (activity_id,),
                            ).fetchone()
                        if row and (row["opened_at"] or row["state"] in TERMINAL_OR_SUBMITTED_STATES or row["state"] == "OPEN"):
                            self._opened_activity_ids.add(activity_id)
                            return True
                except Exception:
                    pass
            return False

    def get_persisted_activity_state(self, activity_id: str, schedule_version: int = 1) -> Optional[str]:
        """Checks both submission_window_state and scheduled_activities in DB for authoritative state."""
        if not self.db or not activity_id:
            return None
        try:
            with self.db._connect() as conn:
                sw_row = conn.execute(
                    "SELECT state FROM submission_window_state WHERE activity_id = ? AND schedule_version = ?",
                    (activity_id, int(schedule_version)),
                ).fetchone()
                act_row = conn.execute(
                    "SELECT status FROM scheduled_activities WHERE activity_id = ?",
                    (activity_id,),
                ).fetchone()

                act_status = act_row["status"] if act_row else None
                sw_state = sw_row["state"] if sw_row else None

                # Terminal/submitted status in either table takes precedence
                if act_status in TERMINAL_OR_SUBMITTED_STATES:
                    return act_status
                if sw_state in TERMINAL_OR_SUBMITTED_STATES:
                    return sw_state
                return sw_state or act_status
        except Exception as e:
            logger.debug(f"get_persisted_activity_state error: {e}")
            return None

    def _eval_js_safe(self, js_code: str) -> None:
        """Evaluates JS on the active WebView2 window in a non-blocking daemon thread."""
        if not self.desktop_api:
            return
        win = getattr(self.desktop_api, "_window", None)
        if not win:
            return

        def _runner():
            try:
                win.evaluate_js(js_code)
            except Exception as e:
                logger.debug(f"JS evaluation skipped: {e}")

        threading.Thread(target=_runner, daemon=True, name="SWM_JsEval").start()

    def bring_window_to_foreground(self, open_hourly_overlay: bool = False) -> bool:
        """Unhides, restores, and brings the native StrictRecords desktop window to foreground without stacking."""
        now = time.time()
        window = getattr(self.desktop_api, "_window", None)
        if hasattr(self, "_last_foreground_ts") and (now - self._last_foreground_ts < 1.5) and window and self._task_native_visible(window):
            if open_hourly_overlay:
                self._eval_js_safe(
                    "if (typeof showHourlySubmissionOverlay === 'function') showHourlySubmissionOverlay(false);"
                )
            return True
        self._last_foreground_ts = now

        restored = False
        if self.desktop_api:
            if hasattr(self.desktop_api, "show_dashboard"):
                try:
                    res = self.desktop_api.show_dashboard(open_hourly_if_active=open_hourly_overlay)
                    if isinstance(res, dict) and res.get("success"):
                        restored = True
                except Exception as e:
                    logger.debug(f"Could not call desktop_api.show_dashboard: {e}")
            elif getattr(self.desktop_api, "_window", None):
                try:
                    self.desktop_api._window.show()
                    self.desktop_api._window.restore()
                    restored = True
                except Exception as e:
                    logger.debug(f"Could not restore webview window: {e}")

        if not restored and sys.platform == "win32":
            try:
                import ctypes
                u32 = ctypes.windll.user32
                hwnd = getattr(self.desktop_api, "_hwnd", None) if self.desktop_api else None
                if not hwnd:
                    for title in ("StrictRoutines", "StrictRecords", "StrictRoutines • Desktop Application"):
                        hwnd = u32.FindWindowW(None, title)
                        if hwnd:
                            break
                if hwnd:
                    u32.ShowWindow(hwnd, 5)  # SW_SHOW
                    u32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    u32.SetForegroundWindow(hwnd)
                    u32.BringWindowToTop(hwnd)
                    restored = True
            except Exception as e:
                logger.debug(f"Win32 bring to foreground error: {e}")
        return restored

    def handle_trigger_event(
        self,
        event_type: str,
        activity_id: str,
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Unified idempotent event handler for all submission window triggers:
        - Local scheduler (PRE_OPENING, OPENED, EXPIRED)
        - Android notification / Windows notification
        - Backend event / WebSocket reconnection
        - Application resume / Startup recovery
        - Android-to-Windows submission synchronization
        """
        data = payload or {}
        ver = self._resolve_schedule_version(activity_id, schedule_version, data)

        with self._state_lock:
            if event_id and self.is_event_processed(event_id):
                logger.info(f"Deduplicated repeated event_id={event_id} ({event_type}) for {activity_id}@v{ver}")
                return {
                    "action": "DEDUPLICATED_EVENT",
                    "event_id": event_id,
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                    "reused": True,
                }

        etype = (event_type or "").upper()
        if etype in ("PRE_OPENING", "REMINDER_T_MINUS_11", "OPEN", "OPENED", "HOURLY_WINDOW_OPEN", "RESUME", "STARTUP_RECOVERY", "EXPIRED", "MISSED", "CLOSED", "HOURLY_WINDOW_CLOSED") and not self._gate_allows_activity(activity_id):
            return {"action": "BLOCKED_WAKEUP", "activity_id": activity_id, "schedule_version": ver, "state": "INACTIVE"}
        if etype in ("PRE_OPENING", "REMINDER_T_MINUS_11"):
            # Reminder at T-11 min must NOT open the full submission popup before T-10 min
            with self._state_lock:
                if event_id:
                    self.mark_event_processed(event_id, event_type, activity_id, ver)
                persisted = self.get_persisted_activity_state(activity_id, ver)
                if not persisted:
                    self._persist_window_state(
                        activity_id=activity_id,
                        schedule_version=ver,
                        state=SubmissionState.UPCOMING.value,
                        window_start=data.get("window_start"),
                        window_end=data.get("window_end"),
                        event_id=event_id,
                    )
                return {
                    "action": "REMINDER_ONLY_NO_POPUP",
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": SubmissionState.UPCOMING.value,
                    "reused": False,
                }

        if etype in ("OPEN", "OPENED", "HOURLY_WINDOW_OPEN", "RESUME", "STARTUP_RECOVERY"):
            return self.open_submission_window(
                activity_id=activity_id,
                slot=data.get("slot", "Current Hour"),
                title=data.get("title", "Hourly Discipline Check"),
                window_start=data.get("window_start", ""),
                window_end=data.get("window_end", ""),
                details=data.get("details") or data,
                force_focus=bool(data.get("force_focus", False)),
                schedule_version=ver,
                event_id=event_id,
            )

        if etype in ("SUBMITTED", "HOURLY_PROOF_SUBMITTED", "ANDROID_SUBMISSION_SYNC"):
            return self.dismiss_submission_form(
                activity_id=activity_id,
                source_device_name=data.get("source_device_name", "Android Device"),
                submitted_at=data.get("submitted_at", ""),
                has_evidence=bool(data.get("has_evidence", False)),
                description=data.get("description", ""),
                primary_activity=data.get("primary_activity", "CODING"),
                schedule_version=ver,
                event_id=event_id,
            )

        if etype in ("EXPIRED", "MISSED", "CLOSED", "HOURLY_WINDOW_CLOSED"):
            return self.expire_submission_window(
                activity_id=activity_id,
                schedule_version=ver,
                event_id=event_id,
            )

        if etype == "AI_REVIEW_COMPLETED":
            self.record_ai_review_completed(
                activity_id=activity_id,
                review_result=data.get("review") or data,
                schedule_version=ver,
                event_id=event_id,
            )
            return {
                "action": "REVIEW_RECORDED",
                "activity_id": activity_id,
                "schedule_version": ver,
                "state": self.state.value,
            }

        return {
            "action": "IGNORED_UNKNOWN_EVENT",
            "event_type": event_type,
            "activity_id": activity_id,
            "schedule_version": ver,
            "state": self.state.value,
        }

    def open_submission_window(
        self,
        activity_id: str,
        slot: str = "Current Hour",
        title: str = "Hourly Discipline Check",
        window_start: str = "",
        window_end: str = "",
        details: Optional[Dict[str, Any]] = None,
        force_focus: bool = False,
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
        reference_now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Displays or reuses the single-instance full-screen hourly submission window.
        Identity: (activity_id, schedule_version).
        - Reuses existing window if already open, never stacking multiple popups.
        - Does not open before window_start (e.g. during T-11m reminder).
        - Does not reopen if activity is already in SUBMITTED, VALIDATING, VERIFIED, REJECTED, MISSED, or CLOSED.
        - Does not repeatedly steal focus on background polls once opened for (activity_id, schedule_version).
        """
        with self._state_lock:
            ver = self._resolve_schedule_version(activity_id, schedule_version, details)
            identity = (activity_id, ver)
            if not self._gate_allows_activity(activity_id, reference_now):
                return {"action": "BLOCKED_WAKEUP", "activity_id": activity_id, "schedule_version": ver, "state": "INACTIVE"}

            if event_id and self.is_event_processed(event_id):
                return {
                    "action": "DEDUPLICATED_EVENT",
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                    "reused": True,
                }

            # 1. Check persisted & DB status to guarantee submitted/expired activities never reopen input form
            persisted_state = self.get_persisted_activity_state(activity_id, ver)
            if persisted_state in TERMINAL_OR_SUBMITTED_STATES:
                logger.info(
                    f"Skipping open_submission_window for {activity_id}@v{ver} — state is already {persisted_state}."
                )
                self._opened_activity_ids.add(activity_id)
                self._opened_identities.add(identity)
                if event_id:
                    self.mark_event_processed(event_id, "OPEN_SKIPPED", activity_id, ver)
                return {
                    "action": "SKIPPED_ALREADY_HANDLED",
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": persisted_state,
                    "reused": False,
                }

            # Also check if in-memory state for this exact activity is already submitted/reviewed/missed
            if (
                self.active_activity_id == activity_id
                and self.active_schedule_version == ver
                and self.state.value in TERMINAL_OR_SUBMITTED_STATES
            ):
                return {
                    "action": "SKIPPED_ALREADY_HANDLED",
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                    "reused": False,
                }

            # 2. Check timing boundaries if window_start/window_end are provided and reference_now is passed
            if reference_now and (window_start or window_end):
                try:
                    from core.time_window_engine import iso_to_dt
                    if window_start:
                        ws_dt = iso_to_dt(window_start)
                        if reference_now < ws_dt:
                            logger.info(
                                f"Refusing to open submission popup for {activity_id}@v{ver} before window_start ({window_start})."
                            )
                            self._persist_window_state(
                                activity_id=activity_id,
                                schedule_version=ver,
                                state=SubmissionState.UPCOMING.value,
                                window_start=window_start,
                                window_end=window_end,
                            )
                            return {
                                "action": "SKIPPED_BEFORE_WINDOW",
                                "activity_id": activity_id,
                                "schedule_version": ver,
                                "state": SubmissionState.UPCOMING.value,
                                "reused": False,
                            }
                    if window_end:
                        we_dt = iso_to_dt(window_end)
                        if reference_now >= we_dt:
                            logger.info(
                                f"Refusing to open submission popup for {activity_id}@v{ver} after window_end ({window_end})."
                            )
                            self._persist_window_state(
                                activity_id=activity_id,
                                schedule_version=ver,
                                state=SubmissionState.MISSED.value,
                                window_start=window_start,
                                window_end=window_end,
                            )
                            return {
                                "action": "SKIPPED_EXPIRED",
                                "activity_id": activity_id,
                                "schedule_version": ver,
                                "state": SubmissionState.MISSED.value,
                                "reused": False,
                            }
                except Exception as e:
                    logger.debug(f"Timing check in open_submission_window skipped: {e}")

            if event_id:
                self.mark_event_processed(event_id, "HOURLY_WINDOW_OPEN", activity_id, ver)

            already_opened_once = (identity in self._opened_identities) or (activity_id in self._opened_activity_ids)

            # 3. If already OPEN for this exact (activity_id, schedule_version), reuse existing window without creating another
            if (
                self.state == SubmissionState.OPEN
                and self.active_activity_id == activity_id
                and self.active_schedule_version == ver
            ):
                if force_focus or (not already_opened_once and self._task_native_visible(getattr(self.desktop_api, "_window", None))):
                    logger.info(f"Submission window already open for {activity_id}@v{ver}; updating overlay.")
                    self._opened_activity_ids.add(activity_id)
                    self._opened_identities.add(identity)
                    self.window_visibility_state = WindowVisibilityState.OPEN_FULLSCREEN
                    if force_focus:
                        self.bring_window_to_foreground(open_hourly_overlay=True)
                    if self._task_native_visible(getattr(self.desktop_api, "_window", None)):
                        self._eval_js_safe(
                            "if (window.pollHourlyStatus) window.pollHourlyStatus(); "
                            "if (window.showHourlySubmissionOverlay) window.showHourlySubmissionOverlay(false);"
                        )
                return {
                    "action": "FOCUSED_EXISTING",
                    "activity_id": activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                    "window_visibility_state": self.window_visibility_state.value,
                    "reused": True,
                }

            # 4. Transition to OPEN and update active activity context
            prev_act = self.active_activity_id
            self.active_activity_id = activity_id
            self.active_schedule_version = ver
            self.active_slot = slot
            self.active_title = title
            self.active_bounds = f"{window_start} – {window_end}" if window_start and window_end else ""
            self.deadline_iso = window_end
            self.activity_data = details or {}
            self.state = SubmissionState.OPEN
            self.window_visibility_state = WindowVisibilityState.OPEN_FULLSCREEN
            self.review_in_progress = False
            self._opened_activity_ids.add(activity_id)
            self._opened_identities.add(identity)

            # Retrieve or attach authoritative AI preflight record (Section 4 & 5)
            try:
                preflight_info = self.db.get_ai_preflight_record(activity_id, ver) if self.db else None
                if not preflight_info and self.db:
                    preflight_info = self.db.get_latest_ai_preflight_for_activity(activity_id)
                if preflight_info:
                    self.activity_data['preflight'] = preflight_info
                    self.activity_data['selected_validation_provider'] = preflight_info.get('selected_validation_provider')
            except Exception as pe:
                logger.debug(f'Preflight lookup in open_submission_window: {pe}')

            now_iso = datetime.now().isoformat()
            if reference_now:
                try:
                    from core.time_window_engine import dt_to_iso
                    now_iso = dt_to_iso(reference_now)
                except Exception:
                    pass
            self._persist_window_state(
                activity_id=activity_id,
                schedule_version=ver,
                state=SubmissionState.OPEN.value,
                window_start=window_start,
                window_end=window_end,
                opened_at=now_iso,
                event_id=event_id,
            )

            logger.info(
                f"Opening single-instance submission window for {activity_id}@v{ver} (Previous: {prev_act})"
            )

            # Bring existing window to front ONLY when explicitly forced by user action
            # Routine submission window opening in background/tray must NEVER steal focus or pop up the GUI!
            if force_focus:
                self.bring_window_to_foreground(open_hourly_overlay=True)

            # Broadcast event so web dashboard immediately updates and shows the full-screen submission overlay
            countdown_seconds = 0
            if window_end:
                try:
                    from core.time_window_engine import iso_to_dt, now_ist
                    countdown_seconds = max(0, int((iso_to_dt(window_end) - (reference_now or now_ist())).total_seconds()))
                except Exception:
                    countdown_seconds = 0
            if self.event_bus:
                self.event_bus.publish(
                    "HOURLY_WINDOW_OPEN",
                    {
                        "event_id": event_id or f"{activity_id}-OPENED",
                        "activity_id": activity_id,
                        "schedule_version": ver,
                        "slot": slot,
                        "title": title,
                        "window_start": window_start,
                        "window_end": window_end,
                        "attendance_status": "OPEN",
                        "validation_status": "NOT_SUBMITTED",
                        "countdown_seconds": countdown_seconds,
                        "action": "OPEN_SUBMISSION",
                        "activity": {
                            "activity_id": activity_id,
                            "schedule_version": ver,
                            "title": title,
                            "window_start": window_start,
                            "window_end": window_end,
                            "status": "OPEN",
                            "details_json": json.dumps(self.activity_data),
                        },
                    },
                    event_id=event_id or f"{activity_id}-OPENED",
                )

            if self._task_native_visible(getattr(self.desktop_api, "_window", None)):
                self._eval_js_safe(
                    "if (window.pollHourlyStatus) window.pollHourlyStatus(); "
                    "if (window.showHourlySubmissionOverlay) window.showHourlySubmissionOverlay(false);"
                )

            return {
                "action": "OPENED",
                "activity_id": activity_id,
                "schedule_version": ver,
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
                "reused": prev_act is not None,
            }

    def dismiss_submission_form(
        self,
        activity_id: str,
        source_device_name: str = "Android Device",
        submitted_at: str = "",
        has_evidence: bool = False,
        description: str = "",
        primary_activity: str = "CODING",
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
        submission_id: Optional[str] = None,
        accepted_at: Optional[str] = None,
        evidence_id: Optional[str] = None,
        evidence_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dismisses/closes the hourly submission edit form for that activity ID,
        transitioning the state machine to SUBMITTED / VALIDATING and displaying
        the compact "Submission received — AI review processing" status card while
        continuing the AI robot review in the background.
        Does NOT close unrelated windows if a different activity is currently open.
        """
        with self._state_lock:
            target_aid = activity_id or self.active_activity_id or ""
            ver = self._resolve_schedule_version(target_aid, schedule_version)

            if event_id:
                self.mark_event_processed(event_id, "HOURLY_PROOF_SUBMITTED", target_aid, ver)

            if target_aid:
                self._opened_activity_ids.add(target_aid)
                self._opened_identities.add((target_aid, ver))

            is_matching = (
                not self.active_activity_id
                or not target_aid
                or self.active_activity_id == target_aid
            )

            now_iso = accepted_at or datetime.now().isoformat()
            sub_display = submitted_at or datetime.now().strftime("%I:%M %p")

            self._persist_window_state(
                activity_id=target_aid,
                schedule_version=ver,
                state=SubmissionState.SUBMITTED.value,
                dismissed_at=now_iso,
                submitted_at=now_iso,
                source_device=source_device_name,
                event_id=event_id,
            )

            completed = getattr(self.desktop_api, 'hourly_overlay_submitted', None)
            if completed:
                completed(target_aid, ver)

            if not is_matching:
                logger.info(
                    f"Submission received for {target_aid}@v{ver}, but active popup is {self.active_activity_id}; preserving active popup."
                )
                return {
                    "action": "IGNORED_UNRELATED_ACTIVITY",
                    "activity_id": target_aid,
                    "active_activity_id": self.active_activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                    "window_visibility_state": self.window_visibility_state.value,
                }

            self.state = SubmissionState.SUBMITTED
            self.window_visibility_state = WindowVisibilityState.CLOSED
            self.review_in_progress = True
            logger.info(
                f"Dismissing submission edit form for {target_aid}@v{ver}; transitioned to SUBMITTED/VALIDATING."
            )

            # Notify EventBus so UI transitions instantly
            if self.event_bus:
                self.event_bus.publish(
                    "HOURLY_PROOF_SUBMITTED",
                    {
                        "event_id": event_id or f"submission:{target_aid}",
                        "activity_id": target_aid,
                        "schedule_version": ver,
                        "source_device_name": source_device_name,
                        "submitted_at": sub_display,
                        "has_evidence": has_evidence,
                        "attendance_status": "PRESENT",
                        "validation_status": "PROCESSING",
                        "submission_id": submission_id or "",
                        "accepted_at": now_iso,
                        "evidence_id": evidence_id,
                        "evidence_sha256": evidence_sha256,
                        "description": description,
                        "primary_activity": primary_activity,
                        "status": "SUBMITTED",
                        "state": SubmissionState.SUBMITTED.value,
                        "banner_message": "Submission received — AI review processing",
                    },
                    event_id=event_id or f"submission:{target_aid}",
                )

            safe_aid = str(target_aid).replace("'", "")
            self._eval_js_safe(
                f"if (window.closeHourlySubmissionModal) window.closeHourlySubmissionModal('{safe_aid}'); "
                "if (window.pollHourlyStatus) window.pollHourlyStatus(); "
                "if (window.loadState) window.loadState();"
            )

            return {
                "action": "DISMISSED",
                "activity_id": target_aid,
                "schedule_version": ver,
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
                "ai_review_active": True,
            }

    def expire_submission_window(
        self,
        activity_id: str,
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
        attendance_status: str = "ABSENT",
        validation_status: str = "NOT_SUBMITTED",
        slot: str = "",
        window_start: str = "",
        window_end: str = "",
        submitted_at: str = "",
    ) -> Dict[str, Any]:
        """Closes the submission window when the hourly window expires (T + 10 min) without a valid submission."""
        if not self._gate_allows_activity(activity_id):
            return {"action": "BLOCKED_WAKEUP", "activity_id": activity_id, "state": "INACTIVE"}
        with self._state_lock:
            target_aid = activity_id or self.active_activity_id or ""
            ver = self._resolve_schedule_version(target_aid, schedule_version)

            if event_id:
                self.mark_event_processed(event_id, "HOURLY_WINDOW_CLOSED", target_aid, ver)

            if target_aid:
                self._opened_activity_ids.add(target_aid)
                self._opened_identities.add((target_aid, ver))

            is_matching = (
                not self.active_activity_id
                or not target_aid
                or self.active_activity_id == target_aid
            )

            self._persist_window_state(
                activity_id=target_aid,
                schedule_version=ver,
                state=SubmissionState.CLOSED.value,
                dismissed_at=datetime.now().isoformat(),
                event_id=event_id,
            )

            if not is_matching:
                return {
                    "action": "IGNORED_UNRELATED_ACTIVITY",
                    "activity_id": target_aid,
                    "active_activity_id": self.active_activity_id,
                    "schedule_version": ver,
                    "state": self.state.value,
                }

            self.state = SubmissionState.MISSED
            self.window_visibility_state = WindowVisibilityState.CLOSED
            self.review_in_progress = False
            logger.info(f"Hourly submission window expired for {target_aid}@v{ver}; transitioned to MISSED.")

            if self.event_bus:
                self.event_bus.publish(
                    "HOURLY_WINDOW_CLOSED",
                    {
                        "event_id": event_id or f"{target_aid}-CLOSED",
                        "activity_id": target_aid,
                        "schedule_version": ver,
                        "status": attendance_status,
                        "attendance_status": attendance_status,
                        "validation_status": validation_status,
                        "slot": slot,
                        "window_start": window_start,
                        "window_end": window_end,
                        "submitted_at": submitted_at,
                    },
                    event_id=event_id or f"{target_aid}-CLOSED",
                )

            safe_aid = str(target_aid).replace("'", "")
            self._eval_js_safe(
                f"if (window.closeHourlySubmissionModal) window.closeHourlySubmissionModal('{safe_aid}'); "
                "if (window.pollHourlyStatus) window.pollHourlyStatus(); "
                "if (window.loadState) window.loadState();"
            )

            return {
                "action": "EXPIRED",
                "activity_id": target_aid,
                "schedule_version": ver,
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
            }

    def start_ai_validation(
        self,
        activity_id: str,
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly transitions the submission state from SUBMITTED to VALIDATING while AI review runs."""
        with self._state_lock:
            target_aid = activity_id or self.active_activity_id or ""
            ver = self._resolve_schedule_version(target_aid, schedule_version)

            if event_id:
                self.mark_event_processed(event_id, "AI_VALIDATION_STARTED", target_aid, ver)

            self.review_in_progress = True
            if not self.active_activity_id or self.active_activity_id == target_aid:
                self.state = SubmissionState.VALIDATING
                self.window_visibility_state = WindowVisibilityState.CLOSED

            self._persist_window_state(
                activity_id=target_aid,
                schedule_version=ver,
                state=SubmissionState.VALIDATING.value,
                event_id=event_id,
            )
            return {
                "action": "VALIDATING",
                "activity_id": target_aid,
                "schedule_version": ver,
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
            }

    def record_ai_review_completed(
        self,
        activity_id: str,
        review_result: Dict[str, Any],
        schedule_version: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        """Stores AI review result, transitions state to VERIFIED or REJECTED, and notifies subscribers without reopening input form."""
        with self._state_lock:
            target_aid = activity_id or self.active_activity_id or ""
            ver = self._resolve_schedule_version(target_aid, schedule_version)

            if event_id:
                self.mark_event_processed(event_id, "AI_REVIEW_COMPLETED", target_aid, ver)

            decision = (review_result or {}).get("decision", "PROVISIONAL_APPROVE")
            final_state = (
                SubmissionState.VERIFIED
                if decision in ("PROVISIONAL_APPROVE", "APPROVED", "VERIFIED")
                else SubmissionState.REJECTED
            )

            self.review_in_progress = False
            self.last_review_data = review_result
            if not self.active_activity_id or self.active_activity_id == target_aid:
                self.state = final_state
                self.window_visibility_state = WindowVisibilityState.CLOSED

            self._persist_window_state(
                activity_id=target_aid,
                schedule_version=ver,
                state=final_state.value,
                event_id=event_id,
            )

            if self.event_bus:
                self.event_bus.publish(
                    "AI_REVIEW_COMPLETED",
                    {
                        "activity_id": target_aid,
                        "schedule_version": ver,
                        "state": final_state.value,
                        "review": review_result,
                    },
                )

            self._eval_js_safe(
                "if (window.pollHourlyStatus) window.pollHourlyStatus(); "
                "if (window.loadState) window.loadState();"
            )

    def reconcile_on_startup(self, date_str: Optional[str] = None, reference_now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Reconciles the submission window state on application startup or resume from persisted DB state.
        - If an hourly activity is currently OPEN and not yet submitted, restores its state cleanly.
        - If already submitted or expired, ensures input popup stays closed.
        """
        if not self.db:
            return {"action": "NO_DB", "state": self.state.value}
        try:
            from core.time_window_engine import TimeWindowEngine, now_ist, iso_to_dt
            now = reference_now or now_ist()
            target_date = date_str or now.strftime("%Y-%m-%d")
            twe = TimeWindowEngine(self.db)
            activities = twe.get_activities_for_date(target_date, reference_now=now)
            for act in activities:
                if act.get("type") != "HOURLY":
                    continue
                aid = act["activity_id"]
                ver = int(act.get("schedule_version") or 1)
                w_start = iso_to_dt(act["window_start"])
                w_end = iso_to_dt(act["window_end"])
                st = act.get("status", "SCHEDULED")

                if w_start <= now < w_end:
                    if st == "OPEN":
                        details = {}
                        try:
                            details = json.loads(act.get("details_json") or "{}")
                        except Exception:
                            pass
                        slot = details.get("hour_slot") or act.get("title") or "Current Hour"
                        return self.open_submission_window(
                            activity_id=aid,
                            slot=slot,
                            title=act.get("title", "Hourly Discipline Check"),
                            window_start=act["window_start"],
                            window_end=act["window_end"],
                            details=dict(act),
                            schedule_version=ver,
                        )
                    elif st in TERMINAL_OR_SUBMITTED_STATES:
                        with self._state_lock:
                            self._opened_activity_ids.add(aid)
                            self._opened_identities.add((aid, ver))
                            self.state = SubmissionState.VERIFIED if st == "VERIFIED" else SubmissionState.SUBMITTED
                            self.window_visibility_state = WindowVisibilityState.CLOSED
                        return {
                            "action": "RECONCILED_ALREADY_SUBMITTED",
                            "activity_id": aid,
                            "schedule_version": ver,
                            "state": self.state.value,
                            "window_visibility_state": self.window_visibility_state.value,
                        }
        except Exception as e:
            logger.debug(f"reconcile_on_startup error: {e}")
        return {"action": "RECONCILED_IDLE", "state": self.state.value}

    def minimize_window(self) -> Dict[str, Any]:
        """Minimizes the UI overlay to floating pill without altering submission state."""
        with self._state_lock:
            self.window_visibility_state = WindowVisibilityState.MINIMIZED_PILL
            return {
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
                "active_activity_id": self.active_activity_id,
            }

    def restore_window(self, force_focus: bool = True) -> Dict[str, Any]:
        """Restores the UI overlay from minimized pill back to OPEN_FULLSCREEN."""
        with self._state_lock:
            if self.state == SubmissionState.OPEN:
                self.window_visibility_state = WindowVisibilityState.OPEN_FULLSCREEN
            if force_focus:
                self.bring_window_to_foreground(open_hourly_overlay=True)
            return {
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
                "active_activity_id": self.active_activity_id,
            }

    def close_window(self) -> None:
        """
        Manually closes/hides the UI submission overlay without deleting a valid submission
        or resetting the persisted activity state.
        """
        with self._state_lock:
            self.window_visibility_state = WindowVisibilityState.HIDDEN

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "state": self.state.value,
                "window_visibility_state": self.window_visibility_state.value,
                "active_activity_id": self.active_activity_id,
                "active_schedule_version": self.active_schedule_version,
                "active_slot": self.active_slot,
                "active_title": self.active_title,
                "active_bounds": self.active_bounds,
                "deadline_iso": self.deadline_iso,
                "review_in_progress": self.review_in_progress,
                "has_review": self.last_review_data is not None,
                "opened_activities_count": len(self._opened_activity_ids),
                "pending_task_popup_count": len(self._task_delivery_keys(self._task_snapshot)),
                "popup_delivery_inflight": self._task_delivery_inflight,
                "last_popup_delivery_error": self._task_last_error,
            }
