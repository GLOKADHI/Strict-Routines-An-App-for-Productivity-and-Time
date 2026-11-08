"""
STRICTRECORDS — Native Windows System Tray Manager
==================================================
Implements a genuine Win32 Shell_NotifyIconW notification area icon and context menu:
- Uses official StrictRecords icon (app_icon.ico) at proper tray DPI sizes
- Tooltip: "StrictRoutines — Running"
- Handles Explorer.exe restarts via Registered "TaskbarCreated" message
- Watchdog timer recreates tray icon if Windows removes it
- Single-instance per process (never duplicates tray icons)
- Zero terminal / console windows, near-zero CPU overhead (blocking Win32 GetMessageW loop)
- Dynamic right-click menu populated with real live application state:
    StrictRecords ● Running
    ────────────────────────
    Open Dashboard
    Current Score
    Next Hourly Check
    Submission Status
    Device Sync Status
    YouTube / Instagram Status
    ────────────────────────
    Pause Notifications
    Settings
    ────────────────────────
    Exit StrictRecords
"""

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("StrictTrayManager")

# Win32 Constants
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 200

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
NIN_BALLOONUSERCLICK = WM_USER + 5

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_GRAYED = 0x00000001
MF_DISABLED = 0x00000002
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000
MF_DEFAULT = 0x00001000

TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

MB_YESNO = 0x00000004
MB_ICONWARNING = 0x00000030
MB_ICONQUESTION = 0x00000020
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
IDYES = 6

TIMER_WATCHDOG_ID = 9001
WATCHDOG_INTERVAL_MS = 25000  # 25 seconds lightweight check

# Menu Command IDs
IDM_STATUS_HEADER = 1000
IDM_OPEN_DASHBOARD = 1001
IDM_CURRENT_SCORE = 1002
IDM_NEXT_HOURLY = 1003
IDM_SUBMISSION_STATUS = 1004
IDM_DEVICE_SYNC = 1005
IDM_YOUTUBE_STATUS = 1006
IDM_PAUSE_NOTIFICATIONS = 1007
IDM_SETTINGS = 1008
IDM_EXIT = 1009
IDM_STRICTBOT_SETTINGS = 1010


if sys.platform == "win32":
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]


class StrictRecordsTrayManager:
    """
    Authoritative Windows System Tray Icon & Context Menu Controller for StrictRecords.
    """
    _instance: Optional["StrictRecordsTrayManager"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        icon_path: Optional[str] = None,
        db: Any = None,
        on_open_dashboard: Optional[Callable[..., Any]] = None,
        on_open_settings: Optional[Callable[..., Any]] = None,
        on_exit_app: Optional[Callable[..., Any]] = None,
        server_port: int = 8765,
        desktop_api: Any = None,
        server_url: Optional[str] = None,
        on_strictbot_settings: Optional[Callable[..., Any]] = None,
    ):
        self.root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if icon_path and os.path.exists(icon_path):
            self.icon_path = icon_path
        else:
            candidates = [
                os.path.join(self.root_dir, "app_icon.ico"),
                os.path.join(self.root_dir, "windows", "app_icon.ico"),
                r"C:\Users\lokad\AppData\Local\Programs\StrictRecords\app_icon.ico",
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "StrictRecords", "app_icon.ico"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "StrictRecords", "app_icon.ico"),
            ]
            self.icon_path = next((c for c in candidates if os.path.exists(c)), candidates[0])

        self.db = db
        self.desktop_api = desktop_api
        self.server_url = server_url
        self.on_open_dashboard = on_open_dashboard or (desktop_api.show_from_tray if desktop_api and hasattr(desktop_api, "show_from_tray") else None)
        self.on_open_settings = on_open_settings
        self.on_strictbot_settings = on_strictbot_settings
        self.on_exit_app = on_exit_app or (desktop_api.exit_app if desktop_api and hasattr(desktop_api, "exit_app") else None)
        self.server_port = server_port

        self.tooltip = "StrictRoutines — Running"
        self.is_running = False
        self.icon_registered = False
        self.recreate_count = 0
        self.last_action: Optional[str] = None
        self.last_action_time: Optional[str] = None

        self._hwnd: Optional[int] = None
        self._hicon: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._wnd_proc_ref = None
        self._wm_taskbar_created: int = 0
        self._class_name = f"StrictRecordsTrayWnd_{os.getpid()}"

    @classmethod
    def get_instance(cls, **kwargs) -> "StrictRecordsTrayManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            else:
                for k, v in kwargs.items():
                    if v is not None:
                        setattr(cls._instance, k, v)
            return cls._instance

    @classmethod
    def get_existing_instance(cls) -> Optional["StrictRecordsTrayManager"]:
        with cls._lock:
            return cls._instance

    def start(self, wait_ready: float = 3.0) -> bool:
        """Starts the dedicated Win32 tray icon message loop thread."""
        if sys.platform != "win32":
            return False
        if self.is_running and self._thread and self._thread.is_alive():
            return self.icon_registered

        self._stop_event.clear()
        self._ready_event.clear()
        self.is_running = True
        self._thread = threading.Thread(
            target=self._win32_message_loop,
            daemon=True,
            name="SR_SystemTrayThread",
        )
        self._thread.start()
        self._ready_event.wait(timeout=wait_ready)
        return self.icon_registered

    @staticmethod
    def _ensure_interactive_desktop() -> None:
        if sys.platform != "win32":
            return
        try:
            u32 = ctypes.windll.user32
            DESKTOP_ALL = 0x01FF
            h_desk = u32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
            if not h_desk:
                h_desk = u32.OpenInputDesktop(0, False, DESKTOP_ALL)
            if h_desk:
                u32.SetThreadDesktop(h_desk)
        except Exception:
            pass

    def stop(self) -> None:
        """Removes the system tray icon cleanly and stops the Win32 message loop."""
        self.is_running = False
        self._stop_event.set()
        if sys.platform == "win32" and self._hwnd:
            self._ensure_interactive_desktop()
            try:
                self._remove_tray_icon()
            except Exception:
                pass
            try:
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # =========================================================================
    # LIVE STATE & MENU SNAPSHOT
    # =========================================================================

    def get_live_state_summary(self) -> Dict[str, Any]:
        """
        Reads real application state for the tray menu without heavy queries.
        Returns actual score, next hourly check, submission status, device sync status,
        YouTube/Instagram restriction status, and notification pause status.
        """
        score_text = "0 / 100"
        next_hourly_text = "Checking schedule..."
        submission_status_text = "No active window"
        device_sync_text = "No Android Device Paired"
        youtube_status_text = "Restricted (Locked)"
        notifications_paused = False
        is_window_open = False
        active_focus_or_strict = False

        try:
            from core.server import StrictRecordsRequestHandler
            db = self.db or getattr(StrictRecordsRequestHandler, "db", None)
            notif_svc = getattr(StrictRecordsRequestHandler, "notification_service", None)
            rest_mgr = getattr(StrictRecordsRequestHandler, "restriction_mgr", None)
            strict_mgr = getattr(StrictRecordsRequestHandler, "strict_session_manager", None)

            if notif_svc is not None:
                notifications_paused = bool(getattr(notif_svc, "notifications_paused", False))

            if strict_mgr is not None:
                st_val = getattr(strict_mgr.state, "value", str(strict_mgr.state))
                if st_val in ("ACTIVE", "RESTRICTED", "PRIVILEGED"):
                    active_focus_or_strict = True

            if db is not None:
                today = datetime.now().strftime("%Y-%m-%d")

                # 1. Current Score
                try:
                    dr = db.get_daily_record(today, is_dev_mode=False) or {}
                    cur_score = int(dr.get("final_score", 0) if isinstance(dr, dict) else getattr(dr, "final_score", 0))
                    score_text = f"{cur_score} / 100 (Target: 80)"
                except Exception:
                    pass

                # 2. Next Hourly Check & Submission Status from authoritative TimeWindowEngine
                try:
                    from core.time_window_engine import TimeWindowEngine, now_ist, iso_to_dt
                    twe = TimeWindowEngine(db)
                    activities = twe.ensure_activities_in_db(today)
                    now = now_ist()
                    hourly_acts = [a for a in activities if a.get("type") == "HOURLY"]
                    hourly_acts.sort(key=lambda a: a.get("window_start", ""))

                    open_act = None
                    upcoming_act = None
                    last_closed_act = None

                    for a in hourly_acts:
                        w_start = iso_to_dt(a.get("window_start", ""))
                        w_end = iso_to_dt(a.get("window_end", ""))
                        st = a.get("status", "SCHEDULED")
                        if (w_start <= now < w_end and st in ("OPEN", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED")) or st == "OPEN":
                            open_act = a
                            break
                        elif now < w_start and st not in ("CLOSED", "VERIFIED", "MISSED", "REJECTED_LATE"):
                            if upcoming_act is None:
                                upcoming_act = a
                        elif now >= w_end and st in ("MISSED", "CLOSED", "REJECTED_LATE", "VERIFIED", "SUBMITTED", "VALIDATING"):
                            last_closed_act = a

                    def _extract_target_and_window(act_obj):
                        det = {}
                        try:
                            det = json.loads(act_obj.get("details_json") or "{}")
                        except Exception:
                            pass
                        h_val = det.get("hour")
                        target_disp = TimeWindowEngine.format_time_12h(f"{h_val:02d}:00") if h_val else act_obj.get("title", "Hourly Check")
                        ws_disp = TimeWindowEngine.format_time_12h(iso_to_dt(act_obj["window_start"]).strftime("%H:%M"))
                        we_disp = TimeWindowEngine.format_time_12h(iso_to_dt(act_obj["window_end"]).strftime("%H:%M"))
                        return target_disp, ws_disp, we_disp

                    if open_act:
                        t_disp, ws_disp, we_disp = _extract_target_and_window(open_act)
                        op_st = open_act.get("status", "OPEN")
                        if op_st == "OPEN":
                            is_window_open = True
                            next_hourly_text = f"{t_disp} (Window OPEN: {ws_disp}–{we_disp})"
                            submission_status_text = f"OPEN — Submit by {we_disp}"
                        elif op_st in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL"):
                            if upcoming_act:
                                ut_disp, uws_disp, _ = _extract_target_and_window(upcoming_act)
                                next_hourly_text = f"{ut_disp} (Opens {uws_disp})"
                            else:
                                next_hourly_text = f"{t_disp} (Completed)"
                            submission_status_text = f"Submitted ✓ (AI Review Processing)"
                        elif op_st == "VERIFIED":
                            if upcoming_act:
                                ut_disp, uws_disp, _ = _extract_target_and_window(upcoming_act)
                                next_hourly_text = f"{ut_disp} (Opens {uws_disp})"
                            else:
                                next_hourly_text = f"{t_disp} (Completed)"
                            submission_status_text = f"Verified ✓ (+{open_act.get('points', 8)} pts)"
                    elif upcoming_act:
                        ut_disp, uws_disp, uwe_disp = _extract_target_and_window(upcoming_act)
                        next_hourly_text = f"{ut_disp} (Opens {uws_disp})"
                        if last_closed_act:
                            lc_st = last_closed_act.get("status", "")
                            lc_t, _, _ = _extract_target_and_window(last_closed_act)
                            if lc_st == "VERIFIED":
                                submission_status_text = f"Last ({lc_t}): Verified ✓"
                            elif lc_st in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL"):
                                submission_status_text = f"Last ({lc_t}): Submitted ✓"
                            elif lc_st == "MISSED":
                                submission_status_text = f"Last ({lc_t}): Missed · Next at {uws_disp}"
                            else:
                                submission_status_text = f"Waiting for {uws_disp} window"
                        else:
                            submission_status_text = f"Waiting (Opens at {uws_disp})"
                    elif last_closed_act:
                        lc_t, _, _ = _extract_target_and_window(last_closed_act)
                        next_hourly_text = "All Hourly Checks Closed for Today"
                        submission_status_text = f"Last ({lc_t}): {last_closed_act.get('status', 'CLOSED').title()}"
                    else:
                        next_hourly_text = "No Remaining Checks Today"
                        submission_status_text = "Idle"
                except Exception as e:
                    logger.debug(f"Tray schedule summary error: {e}")

                # 3. Device Sync Status
                try:
                    devs = db.get_registered_devices_list()
                    android_devs = [d for d in devs if (d.get("platform") or d.get("device_type") or "").upper() == "ANDROID" and d.get("pairing_state") != "REVOKED"]
                    if android_devs:
                        primary_dev = android_devs[0]
                        d_name = primary_dev.get("device_name") or "Android Device"
                        sync_st = (primary_dev.get("sync_status") or primary_dev.get("status") or "ONLINE").upper()
                        if sync_st in ("ONLINE", "SYNCED", "CONNECTED", "ACTIVE"):
                            device_sync_text = f"Connected ({d_name} · Online)"
                        else:
                            device_sync_text = f"{sync_st.title()} ({d_name})"
                    else:
                        device_sync_text = "No Android Device Paired"
                except Exception:
                    pass

                # 4. YouTube / Instagram Restriction Status
                try:
                    sb = db.get_sidebar_restrictions_status()
                    yt_locked = sb.get("youtube", {}).get("locked", True)
                    ig_locked = sb.get("instagram", {}).get("locked", True)
                    if rest_mgr is not None:
                        r_stat = rest_mgr.get_status()
                        if r_stat and not r_stat.get("allowed", False):
                            yt_locked = True
                    yt_label = "Locked" if yt_locked else "Unlocked"
                    ig_label = "Locked" if ig_locked else "Unlocked"
                    if yt_locked and ig_locked:
                        youtube_status_text = "Restricted (YouTube & Instagram Locked)"
                    elif not yt_locked and not ig_locked:
                        youtube_status_text = "Allowed (YouTube & Instagram Unlocked)"
                    else:
                        youtube_status_text = f"YT: {yt_label} · IG: {ig_label}"
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Error building tray live state: {e}")

        return {
            "header": "StrictRoutines ● Running",
            "tooltip": self.tooltip,
            "current_score": score_text,
            "next_hourly_check": next_hourly_text,
            "submission_status": submission_status_text,
            "device_sync_status": device_sync_text,
            "youtube_instagram_status": youtube_status_text,
            "notifications_paused": notifications_paused,
            "is_window_open": is_window_open,
            "active_focus_or_strict": active_focus_or_strict,
        }

    def get_menu_snapshot(self) -> Dict[str, Any]:
        """Returns the structured tray menu items and live status for inspection and tests."""
        state = self.get_live_state_summary()
        module = sys.modules.get("core.server")
        service = getattr(getattr(module, "StrictRecordsRequestHandler", None), "notification_service", None)
        scheduler_active = bool(service and service._running and service._thread and service._thread.is_alive())
        items = [
            {"id": IDM_STATUS_HEADER, "label": "StrictRoutines ● Running · Scheduler " + ("active" if scheduler_active else "unavailable"), "enabled": False, "type": "header"},
            {"type": "separator"},
            {"id": IDM_OPEN_DASHBOARD, "label": "Open Dashboard", "enabled": True, "type": "action"},
            {"id": IDM_CURRENT_SCORE, "label": f"Current Score: {state['current_score']}", "enabled": True, "type": "info_action"},
            {"id": IDM_NEXT_HOURLY, "label": f"Next Hourly Check: {state['next_hourly_check']}", "enabled": True, "type": "info_action"},
            {"id": IDM_SUBMISSION_STATUS, "label": f"Submission Status: {state['submission_status']}", "enabled": True, "type": "info_action"},
            {"id": IDM_DEVICE_SYNC, "label": f"Device Sync Status: {state['device_sync_status']}", "enabled": True, "type": "info_action"},
            {"id": IDM_YOUTUBE_STATUS, "label": f"YouTube / Instagram Status: {state['youtube_instagram_status']}", "enabled": True, "type": "info_action"},
            {"type": "separator"},
            {
                "id": IDM_PAUSE_NOTIFICATIONS,
                "label": "Pause Notifications",
                "checked": state["notifications_paused"],
                "enabled": True,
                "type": "toggle",
            },
            {"id": IDM_SETTINGS, "label": "Settings", "enabled": True, "type": "action"},
            {"id": IDM_STRICTBOT_SETTINGS, "label": "StrictBot PC Settings", "enabled": bool(self.on_strictbot_settings), "type": "action"},
            {"type": "separator"},
            {"id": IDM_EXIT, "label": "Exit StrictRoutines", "enabled": True, "type": "exit"},
        ]
        return {
            "icon_registered": self.icon_registered,
            "is_running": self.is_running,
            "scheduler_active": scheduler_active,
            "hwnd": self._hwnd,
            "recreate_count": self.recreate_count,
            "tooltip": self.tooltip,
            "last_action": self.last_action,
            "last_action_time": self.last_action_time,
            "state": state,
            "menu_items": items,
        }

    # =========================================================================
    # WIN32 TRAY ICON REGISTRATION & BALLOONS
    # =========================================================================

    def _load_icon_handle(self) -> int:
        user32 = ctypes.windll.user32
        hicon = 0
        if self.icon_path and os.path.exists(self.icon_path):
            # Load at system small icon size for crisp notification area rendering
            sm_cx = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON
            sm_cy = user32.GetSystemMetrics(50) or 16  # SM_CYSMICON
            hicon = user32.LoadImageW(
                None,
                self.icon_path,
                IMAGE_ICON,
                sm_cx,
                sm_cy,
                LR_LOADFROMFILE,
            )
            if not hicon:
                hicon = user32.LoadImageW(
                    None,
                    self.icon_path,
                    IMAGE_ICON,
                    0,
                    0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
        if not hicon:
            hicon = user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR))
        return hicon

    def _build_nid(self, flags: int = NIF_MESSAGE | NIF_ICON | NIF_TIP) -> "NOTIFYICONDATAW":
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = self.tooltip[:127]
        return nid

    def _register_tray_icon(self, is_recreate: bool = False) -> bool:
        if sys.platform != "win32" or not self._hwnd:
            return False
        shell32 = ctypes.windll.shell32
        if not self._hicon:
            self._hicon = self._load_icon_handle()

        nid = self._build_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        # Try modify first if already registered; otherwise add
        res = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        if not res:
            res = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        if res:
            # Upgrade to NOTIFYICON_VERSION_4 for modern Windows 10/11 toasts and Action Center support
            try:
                nid_v = self._build_nid(0)
                nid_v.uTimeoutOrVersion = 4  # NOTIFYICON_VERSION_4
                shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid_v))
            except Exception as e:
                logger.debug(f"Failed setting NOTIFYICON_VERSION_4: {e}")

        self.icon_registered = bool(res)
        if is_recreate and self.icon_registered:
            self.recreate_count += 1
            logger.info(f"System tray icon recreated successfully (count={self.recreate_count}).")
        elif self.icon_registered:
            logger.info("StrictRoutines system tray icon registered in Windows notification area.")
        return self.icon_registered

    def _verify_or_recreate_tray_icon(self) -> bool:
        """Verifies the tray icon is still registered in Explorer; recreates it if missing."""
        if sys.platform != "win32" or not self._hwnd or not self.is_running:
            return False
        shell32 = ctypes.windll.shell32
        nid = self._build_nid(NIF_TIP | NIF_ICON | NIF_MESSAGE)
        ok = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        if not ok:
            logger.info("Tray icon missing from Explorer; recreating tray icon...")
            return self._register_tray_icon(is_recreate=True)
        self.icon_registered = True
        return True

    def _remove_tray_icon(self) -> None:
        if sys.platform != "win32" or not self._hwnd:
            return
        try:
            nid = self._build_nid(0)
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.icon_registered = False
            logger.info("StrictRoutines system tray icon removed cleanly.")
        except Exception as e:
            logger.debug(f"Error removing tray icon: {e}")

    def _do_show_balloon_on_tray_thread(self) -> bool:
        if sys.platform != "win32" or not self._hwnd:
            return False
        payload = getattr(self, "_pending_balloon", None)
        if not payload:
            return False
        try:
            title, message, warning = payload
            nid = self._build_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO)
            nid.szInfoTitle = (title or "StrictRoutines")[:63]
            nid.szInfo = (message or "")[:255]
            nid.uTimeoutOrVersion = 4500
            nid.dwInfoFlags = (NIIF_WARNING if warning else NIIF_INFO) | 0x00000020
            res = ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            if not res:
                if self._register_tray_icon(is_recreate=True):
                    res = ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            logger.info(f"_do_show_balloon_on_tray_thread Shell_NotifyIconW returned: {res}")
            return bool(res)
        except Exception as e:
            logger.info(f"Tray balloon error: {e}")
            return False

    _last_balloon_ts: float = 0.0
    _last_restore_ts: float = 0.0

    def show_balloon(self, title: str, message: str, warning: bool = False) -> bool:
        """Displays a modern styled toast notification directly on the desktop."""
        now = time.time()
        if now - self._last_balloon_ts < 2.0:
            return False
        self._last_balloon_ts = now

        # 1. Prefer modern sleek floating toast
        try:
            from windows.modern_toast import show_modern_toast
            if show_modern_toast(title=title, message=message, warning=warning):
                return True
        except Exception as e:
            logger.debug(f"Modern toast notification error: {e}")

        # 2. Fallback to native tray balloon
        if sys.platform != "win32" or not self._hwnd or not self.icon_registered:
            logger.info(f"show_balloon skipped: win32={sys.platform == 'win32'}, hwnd={self._hwnd}, icon_registered={self.icon_registered}")
            return False

        try:
            self._ensure_interactive_desktop()
            self._pending_balloon = (title, message, warning)
            res = ctypes.windll.user32.SendMessageW(self._hwnd, WM_TRAYICON + 1, 0, 0)
            logger.info(f"show_balloon SendMessageW returned: {res}")
            return bool(res)
        except Exception as e:
            logger.info(f"Tray balloon dispatch error: {e}")
            return False

    # =========================================================================
    # MENU EXECUTION & POPUP DISPLAY
    # =========================================================================

    def restore_app_window(self, tab: Optional[str] = None, open_hourly: bool = False) -> None:
        """Immediately restores and foregrounds the main StrictRoutines application window."""
        now = time.time()
        # Debounce: max 1 restore invocation per 1.5 seconds
        if now - self._last_restore_ts < 1.5:
            return
        self._last_restore_ts = now

        # 1. Direct Win32 foreground activation (< 0.5ms)
        if sys.platform == "win32":
            try:
                my_pid = os.getpid()
                u32 = ctypes.windll.user32
                k32 = ctypes.windll.kernel32
                found_hwnd = 0

                try:
                    h_desk = u32.OpenInputDesktop(0, False, 0x01FF)
                    if h_desk:
                        u32.SetThreadDesktop(h_desk)
                except Exception:
                    pass

                # If DesktopApi has already resolved a valid window handle, prefer it
                if self.desktop_api and hasattr(self.desktop_api, "_resolve_hwnd"):
                    try:
                        api_h = self.desktop_api._resolve_hwnd()
                        if api_h and u32.IsWindow(api_h):
                            found_hwnd = int(api_h)
                    except Exception:
                        pass

                if not found_hwnd:
                    IGNORED_CLASSES = {"MSCTFIME UI", "IME", "OleMainThreadWndClass", "GDI+ Window", "Default IME"}
                    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

                    def enum_proc(h, lParam):
                        nonlocal found_hwnd
                        pid = wintypes.DWORD()
                        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                        if pid.value == my_pid:
                            class_buf = ctypes.create_unicode_buffer(256)
                            u32.GetClassNameW(h, class_buf, 256)
                            cls = class_buf.value
                            if cls in IGNORED_CLASSES or cls.startswith("StrictRecordsTrayWnd") or cls.startswith("ConsoleWindowClass"):
                                return True
                            
                            title_buf = ctypes.create_unicode_buffer(256)
                            u32.GetWindowTextW(h, title_buf, 256)
                            title = title_buf.value

                            if cls.startswith("WindowsForms10.Window") or "StrictRecords" in title or "StrictRoutines" in title:
                                found_hwnd = h
                                return False
                        return True

                    cb = WNDENUMPROC(enum_proc)
                    u32.EnumWindows(cb, 0)

                if not found_hwnd:
                    for title in ("StrictRecords", "StrictRecords • Desktop Application", "StrictRoutines", "StrictRoutines • Desktop Application"):
                        h = u32.FindWindowW(None, title)
                        if h:
                            found_hwnd = int(h)
                            break

                if found_hwnd:
                    u32.ShowWindowAsync(found_hwnd, 9)  # SW_RESTORE
                    u32.ShowWindow(found_hwnd, 5)       # SW_SHOW
                    u32.ShowWindow(found_hwnd, 9)       # SW_RESTORE

                    fg_hwnd = u32.GetForegroundWindow()
                    fg_tid = u32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
                    cur_tid = k32.GetCurrentThreadId()
                    if fg_tid and cur_tid and fg_tid != cur_tid:
                        u32.AttachThreadInput(cur_tid, fg_tid, True)

                    u32.SetWindowPos(found_hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                    u32.SetWindowPos(found_hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                    u32.BringWindowToTop(found_hwnd)
                    u32.SetForegroundWindow(found_hwnd)

                    if fg_tid and cur_tid and fg_tid != cur_tid:
                        u32.AttachThreadInput(cur_tid, fg_tid, False)

                    try:
                        u32.SwitchToThisWindow(found_hwnd, True)
                    except Exception:
                        pass
            except Exception as ex:
                logger.debug(f"Direct tray Win32 restore error: {ex}")

        # 2. Invoke application callback
        if self.on_open_dashboard:
            try:
                threading.Thread(
                    target=self.on_open_dashboard,
                    kwargs={"tab": tab, "open_hourly_if_active": open_hourly},
                    daemon=True,
                    name="SR_TrayOpenDashboard",
                ).start()
            except Exception as ex:
                logger.debug(f"on_open_dashboard invocation error: {ex}")
        elif self.desktop_api and hasattr(self.desktop_api, "show_dashboard"):
            try:
                threading.Thread(
                    target=self.desktop_api.show_dashboard,
                    kwargs={"tab": tab, "open_hourly_if_active": open_hourly},
                    daemon=True,
                    name="SR_TrayDesktopApiShow",
                ).start()
            except Exception as ex:
                logger.debug(f"desktop_api.show_dashboard invocation error: {ex}")
        else:
            # 3. HTTP Server activation fallback (only when no direct in-process callbacks exist)
            def _http_activate():
                try:
                    port = self.server_port or 8765
                    import urllib.request
                    import json
                    url = f"http://127.0.0.1:{port}/api/system/activate_window"
                    payload = json.dumps({"tab": tab, "open_hourly_if_active": open_hourly}).encode("utf-8")
                    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=1.5)
                except Exception:
                    pass

            threading.Thread(target=_http_activate, daemon=True, name="SR_HttpActivate").start()

    def execute_menu_command(self, cmd_id: int, confirm_exit: bool = True) -> Dict[str, Any]:
        """Executes a tray menu command by ID (callable from native menu or API/tests)."""
        self.last_action_time = datetime.now().isoformat()

        if cmd_id in (IDM_STATUS_HEADER, IDM_OPEN_DASHBOARD, IDM_CURRENT_SCORE):
            self.last_action = "OPEN_DASHBOARD"
            tab = "dashboard" if cmd_id == IDM_CURRENT_SCORE else None
            self.restore_app_window(tab=tab)
            return {"success": True, "action": self.last_action, "tab": tab}

        if cmd_id in (IDM_NEXT_HOURLY, IDM_SUBMISSION_STATUS):
            self.last_action = "OPEN_HOURLY_STATUS"
            self.restore_app_window(open_hourly=True)
            return {"success": True, "action": self.last_action}

        if cmd_id == IDM_DEVICE_SYNC:
            self.last_action = "OPEN_DASHBOARD"
            tab = "devices"
            self.restore_app_window(tab=tab)
            return {"success": True, "action": self.last_action, "tab": tab}

        if cmd_id == IDM_YOUTUBE_STATUS:
            self.last_action = "OPEN_DASHBOARD"
            tab = "restrictions"
            self.restore_app_window(tab=tab)
            return {"success": True, "action": self.last_action, "tab": tab}

        if cmd_id == IDM_PAUSE_NOTIFICATIONS:
            self.last_action = "TOGGLE_PAUSE_NOTIFICATIONS"
            new_paused = False
            try:
                from core.server import StrictRecordsRequestHandler
                notif_svc = getattr(StrictRecordsRequestHandler, "notification_service", None)
                db = self.db or getattr(StrictRecordsRequestHandler, "db", None)
                if notif_svc is not None:
                    new_paused = notif_svc.set_paused(not notif_svc.notifications_paused)
                if db is not None:
                    try:
                        db.save_focus_settings({"block_notifications": new_paused})
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Error toggling pause notifications: {e}")
            # Immediately restore window to settings page so user sees notification state
            self.restore_app_window(tab="settings")
            return {"success": True, "action": self.last_action, "notifications_paused": new_paused}

        if cmd_id == IDM_STRICTBOT_SETTINGS:
            if self.on_strictbot_settings:
                self.on_strictbot_settings()
                return {"success": True, "action": "OPEN_STRICTBOT_PC_SETTINGS"}
            return {"success": False, "error": "Native StrictBot controller unavailable"}

        if cmd_id == IDM_SETTINGS:
            self.last_action = "OPEN_SETTINGS"
            self.restore_app_window(tab="settings")
            return {"success": True, "action": self.last_action, "tab": "settings"}

        if cmd_id == IDM_EXIT:
            self.last_action = "EXIT_STRICTRECORDS"
            threading.Thread(
                target=self._handle_exit_request,
                kwargs={"confirm": confirm_exit},
                daemon=True,
                name="SR_ExitHandler",
            ).start()
            return {"success": True, "action": self.last_action}

        # Any other option clicked: restore app immediately
        self.last_action = "OPEN_DASHBOARD"
        self.restore_app_window()
        return {"success": True, "action": self.last_action}

    def _handle_exit_request(self, confirm: bool = True) -> None:
        """The one central key dialog supplies explicit exit confirmation."""
        # The central exit workflow owns authentication and cleanup. A cancelled
        # or incorrect key must leave this tray icon and every worker intact.
        logger.info("Explicit Exit StrictRoutines requested.")
        if self.on_exit_app:
            self.on_exit_app()

    def _show_context_menu(self) -> None:
        """Creates and displays the native Win32 right-click tray menu at the cursor."""
        if sys.platform != "win32" or not self._hwnd:
            return
        user32 = ctypes.windll.user32
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return

        try:
            state = self.get_live_state_summary()

            # 1. Header: StrictRecords ● Running
            summary = self.get_menu_snapshot()
            user32.AppendMenuW(hmenu, MF_STRING, IDM_STATUS_HEADER, summary["menu_items"][0]["label"])
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            # 2. Main Status & Navigation Entries
            user32.AppendMenuW(hmenu, MF_STRING, IDM_OPEN_DASHBOARD, "Open Dashboard")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_CURRENT_SCORE, f"Current Score: {state['current_score']}")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_NEXT_HOURLY, f"Next Hourly Check: {state['next_hourly_check']}")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_SUBMISSION_STATUS, f"Submission Status: {state['submission_status']}")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_DEVICE_SYNC, f"Device Sync Status: {state['device_sync_status']}")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_YOUTUBE_STATUS, f"YouTube / Instagram Status: {state['youtube_instagram_status']}")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            # 3. Preferences & Settings
            pause_flags = MF_STRING | (MF_CHECKED if state["notifications_paused"] else MF_UNCHECKED)
            user32.AppendMenuW(hmenu, pause_flags, IDM_PAUSE_NOTIFICATIONS, "Pause Notifications")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_SETTINGS, "Settings")
            user32.AppendMenuW(hmenu, MF_STRING if self.on_strictbot_settings else MF_GRAYED, IDM_STRICTBOT_SETTINGS, "StrictBot PC Settings")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            # 4. Explicit Exit
            user32.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, "Exit StrictRoutines")

            # Highlight "Open Dashboard" as default item
            user32.SetMenuDefaultItem(hmenu, IDM_OPEN_DASHBOARD, False)

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            # Required by Win32 TrackPopupMenu so clicking outside dismisses the menu cleanly
            user32.SetForegroundWindow(self._hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x,
                pt.y,
                0,
                self._hwnd,
                None,
            )
            user32.PostMessageW(self._hwnd, 0, 0, 0)  # WM_NULL per Microsoft KB135788

            if cmd:
                self.execute_menu_command(int(cmd), confirm_exit=True)
        except Exception as e:
            logger.error(f"Error displaying tray context menu: {e}")
        finally:
            user32.DestroyMenu(hmenu)

    # =========================================================================
    # WIN32 MESSAGE LOOP THREAD
    # =========================================================================

    def _win32_message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        # Ensure thread is attached to the interactive user desktop (WinSta0\Default)
        try:
            DESKTOP_ALL = 0x01FF
            h_desk = user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
            if not h_desk:
                h_desk = user32.OpenInputDesktop(0, False, DESKTOP_ALL)
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception as e:
            logger.debug(f"OpenDesktop/SetThreadDesktop note: {e}")

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        self._wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                event_code = lparam & 0xFFFF
                # ONLY trigger on explicit user clicks / selections:
                # WM_LBUTTONUP (0x0202), WM_LBUTTONDBLCLK (0x0203), NIN_SELECT (0x0400), NIN_KEYSELECT (0x0401), NIN_BALLOONUSERCLICK (0x0405)
                # CRITICAL: NEVER include 0x0402 (NIN_BALLOONSHOW) as it triggers on balloon display!
                if event_code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, 0x0400, 0x0401, 0x0405):
                    self.execute_menu_command(IDM_OPEN_DASHBOARD, confirm_exit=False)
                    return 0
                elif event_code in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_context_menu()
                    return 0
            elif msg == WM_TRAYICON + 1:
                return 1 if self._do_show_balloon_on_tray_thread() else 0
            elif self._wm_taskbar_created and msg == self._wm_taskbar_created:
                logger.info("Explorer.exe TaskbarCreated message received — re-registering StrictRecords tray icon.")
                self._register_tray_icon(is_recreate=True)
                return 0
            elif msg == WM_TIMER and wparam == TIMER_WATCHDOG_ID:
                self._verify_or_recreate_tray_icon()
                return 0
            elif msg == WM_CLOSE:
                self._remove_tray_icon()
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == WM_DESTROY:
                self._remove_tray_icon()
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(_wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = self._class_name
        wc.hIconSm = 0

        user32.RegisterClassExW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            "SR_TrayMsgHost",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinst,
            None,
        )

        if not self._hwnd:
            logger.error("Failed to create Win32 hidden tray host window.")
            self.is_running = False
            self._ready_event.set()
            return

        self._register_tray_icon(is_recreate=False)
        user32.SetTimer(self._hwnd, TIMER_WATCHDOG_ID, WATCHDOG_INTERVAL_MS, None)
        self._ready_event.set()

        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.KillTimer(self._hwnd, TIMER_WATCHDOG_ID)
        except Exception:
            pass
        self._remove_tray_icon()
        self.is_running = False
