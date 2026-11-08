"""
STRICTRECORDS — WINDOWS PC SHUTDOWN MONITOR
===========================================
Monitors Windows system shutdown and session-ending lifecycle events.
Detects genuine Windows shutdown/logoff events via:
1. Win32 SetConsoleCtrlHandler (CTRL_SHUTDOWN_EVENT, CTRL_LOGOFF_EVENT)
2. Win32 Message Loop (WM_QUERYENDSESSION, WM_ENDSESSION)
3. .NET Microsoft.Win32.SystemEvents.SessionEnding (when CLR is present)

Features:
- Bounded, non-blocking execution (<= 1.5s timeout) to never impede OS shutdown.
- Validates shutdown time strictly before 23:45 (cutoff).
- Distinguishes confirmed shutdown from sleep, hibernation, crash, or network loss.
- Offline-first persistence in SQLite with idempotent backend synchronization.
"""

import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("WindowsShutdownMonitor")

# IST Timezone fallback
IST = timezone(timedelta(hours=5, minutes=30))

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
ENDSESSION_LOGOFF = 0x80000000
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6


class WindowsShutdownMonitor:
    def __init__(
        self,
        db=None,
        server_url: str = "http://127.0.0.1:8765",
        device_id: str = "windows_lok_pc",
        account_id: str = "user_lok",
        on_shutdown_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.db = db
        self.server_url = server_url.rstrip("/")
        self.device_id = device_id
        self.account_id = account_id
        self.on_shutdown_callback = on_shutdown_callback
        self._shutdown_recorded = False
        self._lock = threading.Lock()
        self._ctrl_handler_ref = None
        self._window_thread = None
        self._hwnd = None
        self._running = False
        self._dotnet_session_handler = None

    def start(self) -> None:
        """Starts monitoring Windows shutdown lifecycle events."""
        with self._lock:
            if self._running:
                return
            self._running = True

        # 1. Console Ctrl Handler
        if sys.platform == "win32":
            try:
                HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)

                def _console_ctrl_handler(ctrl_type):
                    if ctrl_type in (CTRL_SHUTDOWN_EVENT, CTRL_LOGOFF_EVENT):
                        is_logoff = (ctrl_type == CTRL_LOGOFF_EVENT)
                        source = "WIN32_CTRL_LOGOFF" if is_logoff else "WIN32_CTRL_SHUTDOWN"
                        self.on_shutdown_detected(event_source=source, is_logoff=is_logoff)
                        return True
                    return False

                self._ctrl_handler_ref = HandlerRoutine(_console_ctrl_handler)
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_handler_ref, True)
                logger.info("Win32 Console Ctrl shutdown handler registered.")
            except Exception as e:
                logger.warning(f"Could not register SetConsoleCtrlHandler: {e}")

        # 2. .NET SystemEvents.SessionEnding
        try:
            from Microsoft.Win32 import SystemEvents, SessionEndReasons

            def _on_session_ending(sender, e):
                # This is a query, which Windows or another app can still cancel.
                # The native WM_ENDSESSION handler records only a confirmed end.
                e.Cancel = False

            self._dotnet_session_handler = _on_session_ending
            SystemEvents.SessionEnding += _on_session_ending
            logger.info(".NET SystemEvents.SessionEnding handler registered.")
        except Exception:
            pass

        # 3. Native Win32 message-only window for WM_QUERYENDSESSION / WM_ENDSESSION
        if sys.platform == "win32":
            self._start_hidden_window_thread()

        # 4. Startup reconciliation for unposted events
        threading.Thread(target=self.reconcile_unposted_events, daemon=True, name="ShutdownReconcileThread").start()

    def stop(self) -> None:
        """Stops monitoring."""
        with self._lock:
            self._running = False
        if self._dotnet_session_handler:
            try:
                from Microsoft.Win32 import SystemEvents
                SystemEvents.SessionEnding -= self._dotnet_session_handler
            except Exception:
                pass
            self._dotnet_session_handler = None
        if sys.platform == "win32" and self._ctrl_handler_ref:
            try:
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_handler_ref, False)
            except Exception:
                pass
        if sys.platform == "win32" and self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
                ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass

    def _start_hidden_window_thread(self) -> None:
        def _window_loop():
            try:
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                kernel32.GetModuleHandleW.restype = ctypes.c_void_p
                user32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p,
                    ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                user32.CreateWindowExW.restype = ctypes.c_void_p
                user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
                user32.DefWindowProcW.restype = ctypes.c_ssize_t
                user32.DestroyWindow.argtypes = [ctypes.c_void_p]

                WNDPROC = ctypes.WINFUNCTYPE(
                    ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong
                )

                def _wnd_proc(hwnd, msg, wparam, lparam):
                    if msg == WM_QUERYENDSESSION:
                        return 1  # TRUE to allow shutdown to proceed cleanly
                    elif msg == WM_ENDSESSION:
                        if wparam:  # If session is actually ending
                            is_logoff = bool(lparam & ENDSESSION_LOGOFF)
                            source = "WIN32_WM_ENDSESSION_LOGOFF" if is_logoff else "WIN32_WM_ENDSESSION"
                            threading.Thread(target=self.on_shutdown_detected,
                                kwargs={"event_source": source, "is_logoff": is_logoff},
                                daemon=True, name="ConfirmedWindowsSessionEnd").start()
                        return 0
                    elif msg == 0x0010:  # WM_CLOSE
                        user32.DestroyWindow(hwnd)
                        return 0
                    elif msg == 0x0002:  # WM_DESTROY
                        user32.PostQuitMessage(0)
                        return 0
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

                proc_ptr = WNDPROC(_wnd_proc)

                class WNDCLASS(ctypes.Structure):
                    _fields_ = [
                        ("style", ctypes.c_uint),
                        ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", ctypes.c_void_p),
                        ("hIcon", ctypes.c_void_p),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", ctypes.c_wchar_p),
                        ("lpszClassName", ctypes.c_wchar_p),
                    ]

                cls_name = f"StrictRecords_ShutdownListener_{os.getpid()}"
                wc = WNDCLASS()
                wc.lpfnWndProc = proc_ptr
                wc.lpszClassName = cls_name
                wc.hInstance = kernel32.GetModuleHandleW(None)

                if user32.RegisterClassW(ctypes.byref(wc)):
                    # Hidden top-level windows receive session broadcasts; a
                    # message-only window is excluded from those broadcasts.
                    self._hwnd = user32.CreateWindowExW(
                        0, cls_name, "StrictRecords Shutdown Listener",
                        0, 0, 0, 0, 0, 0, 0, wc.hInstance, 0
                    )
                    logger.info(f"Win32 hidden message window created for shutdown detection (hwnd={self._hwnd}).")

                    class MSG(ctypes.Structure):
                        _fields_ = [
                            ("hwnd", ctypes.c_void_p),
                            ("message", ctypes.c_uint),
                            ("wParam", ctypes.c_ulonglong),
                            ("lParam", ctypes.c_longlong),
                            ("time", ctypes.c_ulong),
                            ("pt_x", ctypes.c_long),
                            ("pt_y", ctypes.c_long),
                        ]

                    msg = MSG()
                    while self._running and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
            except Exception as e:
                logger.debug(f"Hidden window thread error: {e}")

        self._window_thread = threading.Thread(target=_window_loop, daemon=True, name="ShutdownWindowThread")
        self._window_thread.start()

    @staticmethod
    def is_genuine_shutdown(source: str) -> bool:
        """
        Distinguishes genuine Win32 shutdown/logoff/session-ending events
        from sleep, hibernate, crash, or network disconnects.
        """
        if not source:
            return False
        s = str(source).upper()
        if any(bad in s for bad in ("SUSPEND", "HIBERNATE", "CRASH", "NETWORK_DISCONNECT", "DISCONNECT", "SLEEP")):
            return False
        return any(good in s for good in ("SHUTDOWN", "ENDSESSION", "LOGOFF", "WIN32_API", "SYSTEM_EVENTS"))

    def record_local_shutdown(
        self,
        event_type: str = "WIN32_API",
        shutdown_time: Optional[datetime] = None,
    ) -> str:
        """Helper for registering a local shutdown event and returning its event_id."""
        res = self.on_shutdown_detected(event_source=event_type, custom_timestamp=shutdown_time)
        return res.get("event_id", "")

    def on_shutdown_detected(
        self,
        event_source: str = "WIN32_API",
        is_logoff: bool = False,
        custom_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Executes bounded, non-blocking shutdown recording.
        Records event locally, checks < 23:45 cutoff, and transmits to backend.
        """
        with self._lock:
            if self._shutdown_recorded:
                return {"success": True, "already_recorded": True}
            self._shutdown_recorded = True

        now = custom_timestamp or datetime.now()
        now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        event_id = f"pc_shut_{date_str}_{uuid.uuid4().hex[:8]}"
        cutoff = "23:45"
        try:
            if self.db:
                settings = self.db.get_settings(self.account_id)
                cutoff = settings.pc_shutdown_cutoff_time or "23:45"
        except Exception:
            pass

        cutoff_full = cutoff if len(cutoff) >= 8 else f"{cutoff}:00"
        is_qualifying = bool(time_str < cutoff_full)
        disqual_reason = None if is_qualifying else f"Shutdown occurred at {time_str}, which is at or after cutoff {cutoff}."

        try:
            d_obj = datetime.strptime(date_str, "%Y-%m-%d")
            target_date = (d_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            target_date = date_str

        payload = {
            "event_id": event_id,
            "device_id": self.device_id,
            "account_id": self.account_id,
            "shutdown_timestamp": now_iso,
            "date": date_str,
            "time_hhmmss": time_str,
            "cutoff_time": cutoff,
            "evidence_source": event_source,
            "is_qualifying": is_qualifying,
            "disqualification_reason": disqual_reason,
            "target_reward_date": target_date,
        }

        # 1. Direct local SQLite persistence (< 5ms)
        if self.db:
            try:
                self.db.record_pc_shutdown_event(
                    event_id=event_id,
                    shutdown_timestamp=now_iso,
                    device_id=self.device_id,
                    date=date_str,
                    time_hhmmss=time_str,
                    evidence_source=event_source,
                    account_id=self.account_id,
                )
                logger.info(f"PC shutdown event persisted to local DB: {event_id} (qualifying={is_qualifying})")
                sess_id = getattr(self.db, "current_process_session_id", None)
                if not sess_id:
                    from core.database import StrictRecordsDB
                    sess_id = getattr(StrictRecordsDB, "active_process_session_id", None)
                if sess_id:
                    self.db.record_process_session_clean_exit(sess_id, exit_type="OS_SHUTDOWN")
            except Exception as e:
                logger.error(f"Error persisting PC shutdown to local DB: {e}")

        # 2. Bounded HTTP transmission to backend (<= 0.5s timeout)
        try:
            req_data = json.dumps(payload).encode("utf-8")
            url = f"{self.server_url}/api/pc/shutdown_event"
            req = urllib.request.Request(
                url, data=req_data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status == 200:
                    logger.info("PC shutdown event delivered to authoritative server.")
        except Exception as e:
            logger.warning(f"Could not reach server during shutdown (event queued locally): {e}")

        if self.on_shutdown_callback:
            try:
                self.on_shutdown_callback(payload)
            except Exception:
                pass

        return payload

    def reconcile_unposted_events(self) -> None:
        """Synchronizes any pending local shutdown events to the backend."""
        if not self.db:
            return
        try:
            with self.db._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM pc_shutdown_events WHERE bonus_status = 'AWAITING_PHONE_ATTENDANCE' LIMIT 10"
                ).fetchall()
                for r in rows:
                    ev = dict(r)
                    try:
                        req_data = json.dumps(ev).encode("utf-8")
                        url = f"{self.server_url}/api/pc/shutdown_event"
                        req = urllib.request.Request(
                            url, data=req_data, headers={"Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req, timeout=2.0) as resp:
                            if resp.status == 200:
                                logger.info(f"Reconciled shutdown event {ev['event_id']} with server.")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Reconciliation check: {e}")
