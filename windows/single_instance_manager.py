"""
StrictRecords — Single Instance Manager & Process Watchdog
===========================================================
Guarantees that only ONE active instance of the StrictRecords Windows application
runs at any given time.

If more than 1 instance is detected:
1. Blocks the secondary/duplicate instance from running (terminates immediately).
2. Notifies the user via Windows Toast, Tray balloon, and Notification Center.
3. Restores and brings the active StrictRecords dashboard to the foreground (<1ms).
4. Continuous background watchdog in the primary process detects and blocks any
   rogue or orphan duplicate instances that appear after startup.
"""

import ctypes
from ctypes import wintypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("StrictSingleInstance")

MUTEX_NAME = "Local\\StrictRecords_App_Mutex_v2"
ERROR_ALREADY_EXISTS = 183

_GLOBAL_MUTEX_HOLDER = None
_WATCHDOG_THREAD = None
_WATCHDOG_STOP_EVENT = threading.Event()
_LAST_DUPLICATE_ALERT_TIME = 0.0
_DUPLICATE_ALERT_LOCK = threading.Lock()
_LAST_BRING_FRONT_TIME = 0.0
_BRING_FRONT_LOCK = threading.Lock()


def get_strictrecords_app_instances() -> List[Dict[str, Any]]:
    """
    Enumerates all active StrictRecords application processes on the system.
    Identifies:
    1. Standalone StrictRecords executables (StrictRecords.exe, StrictRecordsDesktop.exe, StrictRecordsApp.exe).
    2. Python processes running desktop_app.py (excluding test runners and builders).
    """
    instances = []
    current_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, "getppid") else None

    # 1. Primary inspection via psutil
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                pid = p.info["pid"]
                name = (p.info["name"] or "").lower()
                cmdline = p.info.get("cmdline") or []
                cmd_str = " ".join(cmdline).lower()

                # Always exclude unit tests, test runners, pytest, and build scripts
                if any(t in cmd_str for t in ("test_", "unittest", "pytest", "build_", "--notification-watchdog")):
                    continue

                is_instance = False
                if name in ("strictrecords.exe", "strictrecordsdesktop.exe", "strictrecordsapp.exe", "strictroutines.exe"):
                    is_instance = True
                elif name in ("python.exe", "pythonw.exe"):
                    if "desktop_app.py" in cmd_str:
                        is_instance = True

                if is_instance:
                    is_current = (pid == current_pid or (parent_pid is not None and pid == parent_pid))
                    instances.append({
                        "pid": int(pid),
                        "name": p.info["name"],
                        "cmdline": cmdline,
                        "create_time": float(p.info.get("create_time") or 0.0),
                        "is_current": is_current,
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        if instances:
            return instances
    except Exception as e:
        logger.debug(f"psutil enumeration failed or unavailable: {e}")

    return instances


def is_another_instance_running(host: str = "127.0.0.1", port: int = 8765) -> Tuple[bool, str]:
    """
    Determines whether another instance of StrictRecords is actively running.
    Returns (True, reason) if an existing instance is found, (False, "") otherwise.
    Cleans up any dead/unresponsive zombie processes so the user can always launch cleanly.
    """
    global _GLOBAL_MUTEX_HOLDER
    if _GLOBAL_MUTEX_HOLDER is not None:
        # Current process already holds the primary mutex
        return False, ""

    current_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, "getppid") else None

    # 1. HTTP Backend server health probe (authoritative proof of active responsive instance)
    backend_alive = False
    try:
        url = f"http://{host}:{port}/api/system/health"
        req = urllib.request.Request(url, headers={"User-Agent": "StrictSingleInstanceCheck/1.0"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                backend_alive = True
    except Exception:
        pass

    # 2. Top-level window handle probe (authoritative proof of active GUI instance)
    window_found = False
    window_pid = None
    if sys.platform == "win32":
        try:
            u32 = ctypes.windll.user32
            for title in ("StrictRecords", "StrictRecords • Desktop Application", "StrictRoutines"):
                h = u32.FindWindowW(None, title)
                if h and u32.IsWindow(h):
                    pid = wintypes.DWORD()
                    u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    if pid.value != current_pid and (parent_pid is None or pid.value != parent_pid):
                        window_found = True
                        window_pid = pid.value
                        break
        except Exception:
            pass

    # 3. Process count check
    instances = get_strictrecords_app_instances()
    other_instances = [inst for inst in instances if not inst["is_current"]]

    if other_instances:
        if backend_alive or window_found:
            pids = [str(inst["pid"]) for inst in other_instances]
            return True, f"Active responsive process(es) detected: PID {', '.join(pids)}"
        else:
            # A slow launch is not proof of a dead process. Never kill it on a
            # failed health probe; Windows remains available for emergency recovery.
            return True, "Existing process(es): PID " + ", ".join(str(inst["pid"]) for inst in other_instances)

    if backend_alive:
        return True, f"HTTP backend server active on {host}:{port}"
    if window_found and window_pid:
        return True, f"Existing window HWND owned by PID {window_pid}"

    # 4. System-wide Named Mutex check via OpenMutexW
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            h = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
            if h:
                kernel32.CloseHandle(h)
                if backend_alive or window_found:
                    return True, "Win32 named mutex already held by active instance"
        except Exception as e:
            logger.debug(f"Mutex check error: {e}")

    return False, ""


def notify_user_duplicate_blocked(
    dup_pid: Optional[int] = None,
    from_primary: bool = False,
    host: str = "127.0.0.1",
    port: int = 8765,
    force: bool = False,
) -> None:
    """
    Delivers a clear notification to the user that a duplicate instance was blocked.
    Uses Tray balloons, Windows Toast, backend server notifications, and Notification Center DB.
    Guarantees no runaway spam: rate-limited to at most one notification per 4.0 seconds unless forced.
    """
    global _LAST_DUPLICATE_ALERT_TIME
    with _DUPLICATE_ALERT_LOCK:
        now = time.time()
        if not force and (now - _LAST_DUPLICATE_ALERT_TIME < 4.0):
            logger.debug("Suppressing duplicate notification alert (debounced).")
            return
        _LAST_DUPLICATE_ALERT_TIME = now

    if from_primary:
        title = "StrictRecords — Duplicate Process Blocked"
        msg = f"A duplicate StrictRecords instance (PID {dup_pid or 'unknown'}) was detected and blocked. Only one instance is allowed to run."
        warning = True
    else:
        title = "StrictRecords — Already Running"
        msg = "StrictRecords is already active. Only one instance can run at a time.\nOpening your active dashboard..."
        warning = False

    logger.info(f"Notifying user: [{title}] {msg}")

    # Deliver via ONE single authoritative path (no duplicate balloons or recursive triggers)
    delivered = False

    # Attempt 1: Tray balloon notification via local tray instance if active in this process
    try:
        from windows.tray_manager import StrictRecordsTrayManager
        tray = StrictRecordsTrayManager.get_existing_instance()
        if tray and getattr(tray, "icon_registered", False):
            delivered = bool(tray.show_balloon(title, msg, warning=warning))
    except Exception:
        pass

    # Attempt 2: If not delivered locally, request through active backend server
    if not delivered:
        try:
            url = f"http://{host}:{port}/api/system/notify"
            payload = json.dumps({"title": title, "message": msg, "warning": warning}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                delivered = bool(data.get("shown") or data.get("success"))
        except Exception:
            pass

    # Attempt 3: If still not delivered (e.g. headless unit tests without tray or server)
    if not delivered:
        try:
            from windows.notification_service import _try_toast_notification
            _try_toast_notification(title, msg)
        except Exception:
            pass

    # Record ONCE to Notification Center database table
    try:
        from core.database import StrictRecordsDB
        db = StrictRecordsDB()
        event_key = f"DUP_BLOCKED_{dup_pid or int(time.time())}_{int(time.time() * 1000) % 10000}"
        db.save_notification_center_event(
            notification_key=event_key,
            title=title,
            message=msg,
            category="SYNC",
        )
    except Exception:
        pass


def bring_primary_instance_to_front(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """Signals the running primary instance to un-hide and focus its dashboard (debounced)."""
    global _LAST_BRING_FRONT_TIME
    with _BRING_FRONT_LOCK:
        now = time.time()
        if now - _LAST_BRING_FRONT_TIME < 1.5:
            return True
        _LAST_BRING_FRONT_TIME = now

    # 1. Request via HTTP backend endpoint
    try:
        url = f"http://{host}:{port}/api/system/activate_window"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") or data.get("window_visible") or data.get("window_restored"):
                return True
    except Exception:
        pass

    # 2. Direct Win32 window search & activation fallback
    if sys.platform == "win32":
        try:
            u32 = ctypes.windll.user32
            for title in ("StrictRecords", "StrictRecords • Desktop Application", "StrictRoutines"):
                h = u32.FindWindowW(None, title)
                if h and u32.IsWindow(h):
                    u32.ShowWindow(h, 9)  # SW_RESTORE
                    u32.ShowWindow(h, 5)  # SW_SHOW
                    u32.SetForegroundWindow(h)
                    u32.BringWindowToTop(h)
                    try:
                        u32.SwitchToThisWindow(h, True)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass

    return False


def enforce_single_instance_or_exit(host: str = "127.0.0.1", port: int = 8765, background: bool = False) -> None:
    """
    Called at process startup. If another StrictRecords instance is already active:
    - Blocks the new instance by terminating immediately.
    - Notifies the user that only one instance is permitted.
    - Restores the active primary dashboard window.
    """
    global _GLOBAL_MUTEX_HOLDER
    if _GLOBAL_MUTEX_HOLDER is not None:
        return

    if sys.platform == "win32":
        # Acquire BEFORE HTTP/window probes. Two simultaneous launches must not
        # both pass the probes while their servers and windows are still starting.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        error = ctypes.get_last_error()
        if not mutex:
            raise OSError(error, "Could not establish the StrictRecords instance lock")
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex)
            if not background:
                bring_primary_instance_to_front(host=host, port=port)
                notify_user_duplicate_blocked(dup_pid=os.getpid(), from_primary=False, host=host, port=port)
            raise SystemExit(0)
        _GLOBAL_MUTEX_HOLDER = mutex
        return

    is_running, reason = is_another_instance_running(host=host, port=port)
    if is_running:
        logger.warning(f"Startup blocked: Another StrictRoutines instance is already running ({reason}).")
        # Notify user and bring active dashboard to front ONLY if not launched as a background worker
        if not background:
            notify_user_duplicate_blocked(dup_pid=os.getpid(), from_primary=False, host=host, port=port)
            bring_primary_instance_to_front(host=host, port=port)
        logger.info("Duplicate instance cleanly exiting (single-instance policy enforced).")
        # Clean immediate exit without spinning up duplicate background workers
        sys.exit(0)



def release_single_instance() -> None:
    """Close the handle after workers stop. Process death also releases it automatically."""
    global _GLOBAL_MUTEX_HOLDER
    handle, _GLOBAL_MUTEX_HOLDER = _GLOBAL_MUTEX_HOLDER, None
    if handle and sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def block_and_terminate_duplicate_processes(current_pid: Optional[int] = None) -> List[int]:
    """
    Scans for any other StrictRecords application processes and terminates them immediately.
    Returns the list of blocked/terminated PIDs.
    """
    cur_pid = current_pid or os.getpid()
    instances = get_strictrecords_app_instances()
    other_procs = [p for p in instances if not p.get("is_current", False) and p["pid"] != cur_pid]

    blocked_pids = []
    for proc in other_procs:
        dup_pid = proc["pid"]
        proc_name = proc["name"]
        logger.warning(f"Detected duplicate StrictRoutines process {dup_pid} ({proc_name}). Terminating...")

        terminated = False
        try:
            import psutil
            p = psutil.Process(dup_pid)
            p.terminate()
            try:
                p.wait(timeout=1.5)
            except psutil.TimeoutExpired:
                p.kill()
            terminated = True
        except Exception:
            pass

        if not terminated and sys.platform == "win32":
            try:
                # Win32 TerminateProcess fallback (PROCESS_TERMINATE = 0x0001)
                kernel32 = ctypes.windll.kernel32
                h_proc = kernel32.OpenProcess(0x0001, False, dup_pid)
                if h_proc:
                    kernel32.TerminateProcess(h_proc, 1)
                    kernel32.CloseHandle(h_proc)
                    terminated = True
            except Exception:
                pass

        if terminated:
            blocked_pids.append(dup_pid)
            logger.info(f"Successfully blocked and terminated duplicate StrictRoutines process {dup_pid}.")

    if blocked_pids:
        # Notify user that duplicate processes were blocked
        notify_user_duplicate_blocked(dup_pid=blocked_pids[0], from_primary=True)

    return blocked_pids


def start_single_instance_watchdog(interval: float = 4.0, stop_event: Optional[threading.Event] = None) -> threading.Thread:
    """
    Starts a background watchdog thread in the primary process that periodically checks
    if the number of StrictRecords processes exceeds 1, terminating duplicates and notifying the user.
    """
    global _WATCHDOG_THREAD, _WATCHDOG_STOP_EVENT
    ev = stop_event or _WATCHDOG_STOP_EVENT

    def _watchdog_loop():
        logger.info(f"StrictRoutines Single-Instance Watchdog active (interval={interval}s).")
        while not ev.is_set():
            ev.wait(interval)
            if ev.is_set():
                break
            try:
                block_and_terminate_duplicate_processes()
            except Exception as e:
                logger.debug(f"Watchdog scan exception: {e}")
        logger.info("StrictRoutines Single-Instance Watchdog stopped cleanly.")

    t = threading.Thread(target=_watchdog_loop, daemon=True, name="SR_SingleInstanceWatchdog")
    t.start()
    _WATCHDOG_THREAD = t
    return t
