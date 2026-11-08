"""
StrictRecords Windows Notification Service (Section 1)
=======================================================
Mirrors StrictRecords events to Windows notification area.
Optionally mirrors Android app notifications to PC.

Architecture:
  - Single authoritative scheduler (polling /api/notifications/pending)
  - Deduplication by stable event keys stored in notification_center DB table
  - Categories: TASKS, PROOF, POINTS, PRIVILEGES, FOCUS, ANDROID, SYNC
  - Read/dismiss state persisted
  - Pause Notifications toggle (suppresses desktop toasts without disabling scheduler or proof tracking)
  - Privacy controls for Android notification mirroring
  - Sensitive content (OTPs, banking) blocked by default
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)

# Notification categories
CATEGORY_TASKS = "TASKS"
CATEGORY_PROOF = "PROOF"
CATEGORY_POINTS = "POINTS"
CATEGORY_PRIVILEGES = "PRIVILEGES"
CATEGORY_FOCUS = "FOCUS"
CATEGORY_ANDROID = "ANDROID"
CATEGORY_SYNC = "SYNC"

# Sensitive Android app package prefixes that are blocked by default
SENSITIVE_APP_PACKAGES = {
    "com.google.android.gm",       # Gmail
    "com.whatsapp",                 # WhatsApp (message content)
    "com.android.mms",             # SMS
    "in.org.npci.upiapp",          # BHIM
    "com.phonepe.app",             # PhonePe
    "com.google.android.apps.nbu.paisa.user",  # Google Pay
    "net.one97.paytm",             # Paytm
    "com.sbi.lotusintouch",        # SBI banking
    "com.snapchat.android",        # Snapchat
}

# Event type to category mapping
EVENT_TO_CATEGORY = {
    "WAKE_UP": CATEGORY_TASKS,
    "BATH": CATEGORY_TASKS,
    "GYM": CATEGORY_TASKS,
    "HOURLY": CATEGORY_TASKS,
    "CUSTOM": CATEGORY_TASKS,
    "PRE_OPENING": CATEGORY_TASKS,
    "OPENED": CATEGORY_TASKS,
    "WARNING_1M": CATEGORY_TASKS,
    "CLOSED": CATEGORY_TASKS,
    "PROOF_SUBMITTED": CATEGORY_PROOF,
    "AI_RESULT": CATEGORY_PROOF,
    "ADMIN_APPROVED": CATEGORY_PROOF,
    "ADMIN_REJECTED": CATEGORY_PROOF,
    "POINTS_AWARDED": CATEGORY_POINTS,
    "PENALTY_APPLIED": CATEGORY_POINTS,
    "SCORE_THRESHOLD": CATEGORY_POINTS,
    "YOUTUBE_LOCKED": CATEGORY_PRIVILEGES,
    "YOUTUBE_UNLOCKED": CATEGORY_PRIVILEGES,
    "PRIVILEGE_CHANGED": CATEGORY_PRIVILEGES,
    "FOCUS_START": CATEGORY_FOCUS,
    "FOCUS_END": CATEGORY_FOCUS,
    "STRICTBOT": CATEGORY_FOCUS,
    "DEVICE_CONNECTED": CATEGORY_SYNC,
    "DEVICE_DISCONNECTED": CATEGORY_SYNC,
    "SYNC_STATUS": CATEGORY_SYNC,
    "ANDROID_MIRROR": CATEGORY_ANDROID,
}


_LAST_TOAST_TIME = 0.0
_TOAST_LOCK = threading.Lock()


def _try_toast_notification(title: str, message: str, category: str = "", on_click: Any = None) -> bool:
    """
    Send a modern styled desktop toast notification.
    First attempts native modern floating toast (zero subprocess overhead),
    falling back to tray manager and HTTP backend server.
    """
    global _LAST_TOAST_TIME

    # 0. Suppress desktop notifications during approved active examinations
    try:
        from windows.neo_exam_monitor import NeoExamBrowserMonitor
        monitor = NeoExamBrowserMonitor.get_instance()
        if monitor.is_notification_suppression_active():
            monitor.queue_suppressed_notification({
                "title": title,
                "message": message,
                "category": category,
                "suppressed_by": "NEO_EXAM_ACTIVE",
            })
            return False
    except Exception:
        pass

    if category==CATEGORY_ANDROID:
        from windows.strict_notification_filter import session_filter_active
        if session_filter_active():
            # Preserve the mirrored record/synchronization, suppress its banner.
            return False

    # 1. Native Modern Toast Window
    try:
        from windows.modern_toast import show_modern_toast
        if show_modern_toast(title=title, message=message, category=category, on_click=on_click):
            return True
    except Exception:
        pass

    # 2. Fast native delivery via active StrictRecordsTrayManager (zero subprocess)
    try:
        from windows.tray_manager import StrictRecordsTrayManager
        tray = StrictRecordsTrayManager.get_existing_instance()
        if tray and tray.is_running and tray.icon_registered:
            if tray.show_balloon(title, message):
                return True
    except Exception:
        pass

    # 2. Local HTTP backend server notification endpoint
    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/system/notify",
            data=json.dumps({"title": title, "message": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("shown") or data.get("success"):
                return True
    except Exception:
        pass

    # 3. Fallback: Rate-limited transient notification (never pile up background PowerShell processes)
    with _TOAST_LOCK:
        now = time.time()
        if now - _LAST_TOAST_TIME < 4.0:
            return False
        _LAST_TOAST_TIME = now

    try:
        safe_title = title.replace("'", "''").replace('"', '""')[:64]
        safe_msg = message.replace("'", "''").replace('"', '""')[:200]
        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.ShowBalloonTip(3000, '{safe_title}', '{safe_msg}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 600
$notify.Dispose()
"""
        creation_flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        return True
    except Exception:
        return False


class NotificationCenter:
    """
    In-process notification center. Stores all notifications for the UI
    to render. Notifications are stored in memory and optionally persisted
    to the database via the server's /api/notifications/* endpoints.
    """

    def __init__(self, server_url: str = "http://127.0.0.1:8765"):
        self.server_url = server_url
        self._notifications: List[Dict[str, Any]] = []  # in-memory store
        self._seen_keys: set = set()
        self._lock = threading.Lock()
        self._unread_count = 0

    def add(self, key: str, title: str, message: str, category: str,
            activity_id: str = "", event_type: str = "", timestamp: str = "") -> bool:
        """
        Add a notification if not already seen. Returns True if it was new.
        """
        with self._lock:
            if key in self._seen_keys:
                return False
            self._seen_keys.add(key)
            ts = timestamp or datetime.now(IST).isoformat()
            self._notifications.append({
                "key": key,
                "title": title,
                "message": message,
                "category": category,
                "activity_id": activity_id,
                "event_type": event_type,
                "timestamp": ts,
                "read": False,
                "dismissed": False,
            })
            self._unread_count += 1
            # Keep last 200 notifications
            if len(self._notifications) > 200:
                self._notifications = self._notifications[-200:]
            return True

    def get_all(self, category: Optional[str] = None, include_dismissed: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            result = [n for n in self._notifications
                      if (category is None or n["category"] == category)
                      and (include_dismissed or not n["dismissed"])]
            return list(reversed(result))  # newest first

    def mark_read(self, key: str) -> None:
        with self._lock:
            for n in self._notifications:
                if n["key"] == key and not n["read"]:
                    n["read"] = True
                    self._unread_count = max(0, self._unread_count - 1)
                    break

    def dismiss(self, key: str) -> None:
        with self._lock:
            for n in self._notifications:
                if n["key"] == key:
                    if not n["read"]:
                        self._unread_count = max(0, self._unread_count - 1)
                    n["read"] = True
                    n["dismissed"] = True
                    break

    def clear_category(self, category: str) -> int:
        with self._lock:
            before = len(self._notifications)
            self._notifications = [n for n in self._notifications
                                    if n["category"] != category or n["dismissed"]]
            cleared = before - len(self._notifications)
            self._unread_count = sum(1 for n in self._notifications if not n["read"])
            return cleared

    def get_unread_count(self) -> int:
        with self._lock:
            return self._unread_count

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            cats: Dict[str, int] = {}
            for n in self._notifications:
                if not n["dismissed"]:
                    cats[n["category"]] = cats.get(n["category"], 0) + (1 if not n["read"] else 0)
            return {
                "total_unread": self._unread_count,
                "by_category": cats,
                "total": len([n for n in self._notifications if not n["dismissed"]]),
            }


class StrictNotificationService:
    """
    Windows notification service for StrictRecords.

    Polls /api/notifications/pending on a configurable interval and dispatches
    Windows toast notifications for new events. Also supports optional
    Android app notification mirroring and Pause Notifications preference.

    Design principles:
      - Single authoritative scheduler loop (default: 10s interval)
      - Stable event keys prevent duplicate notifications on reconnect
      - Pause Notifications only suppresses toast popups without disabling scheduler or proof tracking
      - Android mirroring requires explicit opt-in and permission
      - Sensitive app content blocked by default
    """

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8765",
        poll_interval_sec: float = 10.0,
        enable_android_mirror: bool = False,
        android_excluded_packages: Optional[List[str]] = None,
        android_sensitive_mode: str = "BLOCK_SENSITIVE",  # BLOCK_SENSITIVE, BLOCK_CONTENT, ALLOW_ALL
        notifications_paused: bool = False,
        db=None,
        engine=None,
        submission_manager=None,
        clock=None,
    ):
        self.server_url = server_url
        self.poll_interval_sec = poll_interval_sec
        self.enable_android_mirror = enable_android_mirror
        self.android_excluded_packages = set(android_excluded_packages or [])
        self.android_sensitive_mode = android_sensitive_mode
        self.notifications_paused = bool(notifications_paused)
        self.center = NotificationCenter(server_url)
        self._running = False
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mirror_status = "DISABLED"  # DISABLED, ENABLED, NO_PERMISSION, DISCONNECTED
        self.last_toast_delivered: Optional[Dict[str, Any]] = None
        self.db = db
        self.engine = engine
        self.submission_manager = submission_manager
        self._next_boundary = None
        self._last_reset_date = None
        self._event_queue = None
        self._clock = clock
        self.last_scheduler_error = None
        self._last_error_log_at = 0.0
        if self.db is not None:
            self._hydrate_seen_keys()

    def _hydrate_seen_keys(self) -> None:
        try:
            with self.db._connect() as conn:
                for row in conn.execute("SELECT notification_key FROM notification_history").fetchall():
                    self.center._seen_keys.add(row["notification_key"])
                for row in conn.execute("SELECT notification_key FROM notification_center WHERE is_dismissed = 1 OR is_read = 1").fetchall():
                    self.center._seen_keys.add(row["notification_key"])
        except Exception:
            pass

    def _now(self):
        from core.time_window_engine import now_ist
        return self._clock() if self._clock else now_ist()

    def start(self) -> None:
        """Start one worker; never clear a stop token still owned by an older worker."""
        with self._lifecycle_lock:
            if self._running: return
            previous = self._thread
            if previous and previous.is_alive():
                previous.join(timeout=6.0)
                if previous.is_alive(): return
            self._stop_event = threading.Event()
            if self.db:
                from core.event_bus import EventBus
                self._event_queue = EventBus.get_instance().subscribe()
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="SR_NotificationService")
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._running = False
            self._stop_event.set()
            if self._event_queue is not None:
                try: self._event_queue.put_nowait(None)
                except Exception: pass
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(timeout=6.0)
            if self._event_queue is not None:
                from core.event_bus import EventBus
                EventBus.get_instance().unsubscribe(self._event_queue)
                self._event_queue = None

    def set_paused(self, paused: bool) -> bool:
        """Updates notification pause preference without stopping scheduler or proof tracking."""
        self.notifications_paused = bool(paused)
        return self.notifications_paused

    def request_reconcile(self):
        if self._event_queue is not None:
            try: self._event_queue.put_nowait({"event_type": "SCHEDULE_CHANGED"})
            except Exception: pass

    def _poll_loop(self) -> None:
        """Main polling loop — polls every poll_interval_sec."""
        while self._running and not self._stop_event.is_set():
            try:
                self._poll_strictrecords_notifications()
                self.last_scheduler_error = None
                if self._running and self.enable_android_mirror:
                    self._poll_android_mirror_notifications()
            except Exception as error:
                self.last_scheduler_error = str(error)
                if time.monotonic() - self._last_error_log_at >= 30:
                    logger.exception("Scheduled reminder reconciliation failed; the existing worker will retry")
                    self._last_error_log_at = time.monotonic()
            if self._event_queue is None:
                self._stop_event.wait(self.poll_interval_sec)
            else:
                import queue
                # Absolute schedule deadlines wake this same worker. Thirty seconds
                # bounds recovery from suspension, clock changes or silent DB edits.
                delay = 30.0
                if self._next_boundary:
                    delay = min(delay, max(0.1, (self._next_boundary - self._now()).total_seconds()))
                try:
                    event = self._event_queue.get(timeout=delay)
                    if event is None: break
                    # Coalesce a burst of sync/review events into one reconciliation.
                    self._stop_event.wait(0.15)
                    for _ in range(100):
                        self._event_queue.get_nowait()
                except queue.Empty:
                    pass

    def _http_get(self, path: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        import urllib.request
        try:
            with urllib.request.urlopen(f"{self.server_url}{path}", timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def ingest_pending_notifications(self, notifications: List[Dict[str, Any]]) -> int:
        """
        Ingests a list of pending notifications from TimeWindowEngine, deduplicating
        by key and dispatching desktop toasts if notifications are not paused.
        """
        delivered_count = 0
        for n in notifications:
            key = n.get("notification_key", "")
            if not key:
                continue

            if self.db is not None:
                try:
                    with self.db._connect() as conn:
                        row = conn.execute("SELECT is_dismissed FROM notification_center WHERE notification_key = ?", (key,)).fetchone()
                        if row and row["is_dismissed"]:
                            self.center._seen_keys.add(key)
                            continue
                except Exception:
                    pass

            event_type = n.get("event_type", "")
            message = n.get("message", "")
            act_id = n.get("activity_id", "")
            timestamp = n.get("scheduled_for", "")
            category = EVENT_TO_CATEGORY.get(event_type, CATEGORY_TASKS)

            title = self._get_notification_title(event_type)

            is_new = self.center.add(
                key=key,
                title=title,
                message=message,
                category=category,
                activity_id=act_id,
                event_type=event_type,
                timestamp=timestamp,
            )

            toast_title = title
            if event_type == "PRE_OPENING":
                toast_title = "⏰ Hourly Window Opening Soon" if ("HOURLY" in act_id or "HOURLY" in str(event_type)) else "⏰ Activity Opening Soon"
            elif event_type == "OPENED":
                toast_title = "🟢 Hourly Window Open" if ("HOURLY" in act_id or "HOURLY" in str(event_type)) else "🟢 Activity Window Open"

            def _make_click_callback(a_id=act_id, e_type=event_type):
                def _on_click():
                    try:
                        from windows.tray_manager import StrictRecordsTrayManager
                        tray = StrictRecordsTrayManager.get_existing_instance()
                        if tray:
                            tray.restore_app_window(tab="hourly", open_hourly=True)
                            return
                    except Exception:
                        pass
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            f"{self.server_url}/api/system/activate_window?tab=hourly&open_hourly=1",
                            data=b"",
                            headers={"Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req, timeout=1.0) as resp:
                            pass
                    except Exception:
                        pass
                return _on_click

            if is_new:
                delivered_count += 1
                self.last_toast_delivered = {
                    "key": key,
                    "title": toast_title,
                    "message": message,
                    "event_type": event_type,
                    "timestamp": datetime.now(IST).isoformat(),
                    "suppressed_by_pause": self.notifications_paused,
                }
                if not self.notifications_paused:
                    _try_toast_notification(
                        title=toast_title,
                        message=message,
                        category=category,
                        on_click=_make_click_callback(),
                    )
        return delivered_count

    def _poll_strictrecords_notifications(self) -> None:
        """Poll /api/notifications/pending and dispatch new Windows toasts."""
        now = self._now()
        today = now.strftime("%Y-%m-%d")
        if self.db is not None:
            from core.time_window_engine import TimeWindowEngine
            today = now.strftime("%Y-%m-%d")
            if self.engine and self._last_reset_date != today:
                self.engine.execute_daily_reset_if_needed()
                self._last_reset_date = today
            scheduler = TimeWindowEngine(self.db)
            notifications = scheduler.get_pending_notifications(today, reference_now=now)
            self.ingest_pending_notifications(notifications)
            if self.submission_manager:
                self._next_boundary = self.submission_manager.reconcile_task_reminders(
                    getattr(scheduler, "last_notification_activities", []), reference_now=now)
            return
        data = self._http_get(f"/api/notifications/pending?date={today}")
        if not data:
            return

        notifications = data.get("notifications", [])
        if notifications:
            self.ingest_pending_notifications(notifications)

    def _get_notification_title(self, event_type: str) -> str:
        titles = {
            "PRE_OPENING": "⏰ Activity Opening Soon",
            "OPENED": "🟢 Activity Window Open",
            "WARNING_1M": "⚠️ 1 Minute Remaining",
            "CLOSED": "🔴 Window Closed",
            "WAKE_UP": "🌅 Wake-Up",
            "PROOF_SUBMITTED": "📸 Proof Submitted",
            "AI_RESULT": "🤖 AI Verification",
            "ADMIN_APPROVED": "✅ Approved by Admin",
            "ADMIN_REJECTED": "❌ Rejected by Admin",
            "POINTS_AWARDED": "⭐ Points Awarded",
            "PENALTY_APPLIED": "📉 Penalty Applied",
            "YOUTUBE_LOCKED": "🔒 YouTube Locked",
            "YOUTUBE_UNLOCKED": "🔓 YouTube Unlocked",
            "FOCUS_START": "🎯 Focus Session Started",
            "FOCUS_END": "✅ Focus Session Complete",
            "DEVICE_CONNECTED": "📱 Device Connected",
            "DEVICE_DISCONNECTED": "⚡ Device Disconnected",
            "ANDROID_MIRROR": "📱 Android Notification",
            "STRICTBOT": "🤖 StrictBot",
        }
        return titles.get(event_type, "StrictRoutines")

    def _poll_android_mirror_notifications(self) -> None:
        """Poll /api/android/notifications for mirrored Android app notifications."""
        data = self._http_get("/api/android/notifications?unread_only=true")
        if not data:
            self._mirror_status = "DISCONNECTED"
            return

        if data.get("permission_required"):
            self._mirror_status = "NO_PERMISSION"
            return

        self._mirror_status = "ENABLED"
        notifications = data.get("notifications", [])
        for n in notifications:
            pkg = n.get("package_name", "")
            key = n.get("notification_key", "")
            if not key:
                continue

            if pkg in self.android_excluded_packages:
                continue

            is_sensitive = pkg in SENSITIVE_APP_PACKAGES
            if is_sensitive and self.android_sensitive_mode == "BLOCK_SENSITIVE":
                continue

            content = n.get("title", "")
            if self.android_sensitive_mode == "BLOCK_CONTENT":
                content = n.get("app_name", pkg)

            is_new = self.center.add(
                key=f"ANDROID-{key}",
                title=f"📱 {n.get('app_name', 'Android')}",
                message=content if content else n.get("text", ""),
                category=CATEGORY_ANDROID,
                event_type="ANDROID_MIRROR",
                timestamp=n.get("timestamp", ""),
            )

            if is_new and content and not self.notifications_paused:
                _try_toast_notification(
                    title=f"StrictRoutines · Android · {n.get('app_name', '')}",
                    message=content[:150],
                    category=CATEGORY_ANDROID,
                )

    def push_notification(
        self,
        key: str,
        title: str,
        message: str,
        event_type: str = "",
        category: str = CATEGORY_TASKS,
        activity_id: str = "",
        show_toast: bool = True,
    ) -> bool:
        """Push a notification directly (e.g., from score engine or restriction manager)."""
        is_new = self.center.add(
            key=key,
            title=title,
            message=message,
            category=category,
            activity_id=activity_id,
            event_type=event_type,
        )
        if is_new:
            self.last_toast_delivered = {
                "key": key,
                "title": f"StrictRoutines · {category.title()}",
                "message": message,
                "event_type": event_type,
                "timestamp": datetime.now(IST).isoformat(),
                "suppressed_by_pause": self.notifications_paused,
            }
            if show_toast and not self.notifications_paused:
                _try_toast_notification(
                    title=f"StrictRoutines · {category.title()}",
                    message=message,
                )
        return is_new

    def get_status(self) -> Dict[str, Any]:
        """Returns notification service status for API endpoint."""
        return {
            "service_running": self._running,
            "notifications_paused": self.notifications_paused,
            "mirror_status": self._mirror_status,
            "mirror_enabled": self.enable_android_mirror,
            "android_sensitive_mode": self.android_sensitive_mode,
            "excluded_packages": list(self.android_excluded_packages),
            "last_toast_delivered": self.last_toast_delivered,
            "center_summary": self.center.get_summary(),
            "scheduler_active": bool(self._running and self._thread and self._thread.is_alive()),
            "next_schedule_boundary": self._next_boundary.isoformat() if self._next_boundary else None,
            "last_scheduler_error": self.last_scheduler_error,
        }

    def configure_mirror(
        self,
        enabled: bool,
        excluded_packages: Optional[List[str]] = None,
        sensitive_mode: str = "BLOCK_SENSITIVE",
    ) -> None:
        """Configure Android notification mirroring settings."""
        self.enable_android_mirror = enabled
        if excluded_packages is not None:
            self.android_excluded_packages = set(excluded_packages)
        self.android_sensitive_mode = sensitive_mode
        if enabled:
            self._mirror_status = "ENABLED"
        else:
            self._mirror_status = "DISABLED"
