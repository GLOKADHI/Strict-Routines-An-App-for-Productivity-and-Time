"""
STRICTRECORDS AGENT — Windows Activity Tracker & Entertainment Control Agent
(Sections 16, 18, 19, 27, 29, 30, 42, 43, 46)

Features:
- Real Win32 API foreground window & process tracking (user32.dll / kernel32.dll)
- Real Windows idle time detection via GetLastInputInfo()
- Real Windows Bluetooth audio endpoint enumeration
- Configurable categorization: PRODUCTIVE, STUDY, ENTERTAINMENT, LOCAL_MUSIC, IDLE, UNKNOWN
- Real Windows YouTube / Entertainment Control with safe recovery (never locks OS, Task Manager, or security screens)
- Optional HKCU Startup registration with explicit user permission
- Offline-first queue with idempotent batch synchronization
"""

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine


# Never restrict emergency, system, or developer recovery processes (Section 46)
SAFE_SYSTEM_PROCESSES = {
    "taskmgr.exe",
    "lockapp.exe",
    "logonui.exe",
    "csrss.exe",
    "winlogon.exe",
    "dwm.exe",
    "sihost.exe",
    "Settle.exe",
    "Narrator.exe",
    "Magnify.exe",
    "osk.exe",
    "werfault.exe",
}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwTime", ctypes.wintypes.DWORD),
    ]


class WindowsWin32Inspector:
    """Queries actual Windows foreground window, process executable, idle seconds, and Bluetooth audio endpoints."""

    @staticmethod
    def get_idle_duration_seconds() -> float:
        if os.name != "nt":
            return 0.0
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return max(0.0, round(millis / 1000.0, 2))
        except Exception:
            pass
        return 0.0

    @staticmethod
    def get_active_window_info() -> Dict[str, Any]:
        if os.name != "nt":
            return {"hwnd": 0, "process_name": "unknown", "window_title": "", "pid": 0}

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            found_hwnds = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

            def _enum_cb(h, _lparam):
                if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h) > 0:
                    found_hwnds.append(h)
                    return False
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            hwnd = found_hwnds[0] if found_hwnds else 0

        if not hwnd:
            try:
                import psutil
                priority_procs = {
                    "code.exe",
                    "pycharm64.exe",
                    "studio64.exe",
                    "idea64.exe",
                    "spotify.exe",
                    "chrome.exe",
                    "msedge.exe",
                    "powershell.exe",
                }
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        pname = (p.info.get("name") or "").lower()
                        if pname in priority_procs:
                            return {
                                "hwnd": 0,
                                "pid": int(p.info.get("pid") or 0),
                                "process_name": p.info.get("name") or pname,
                                "window_title": f"Active Process: {p.info.get('name')}",
                            }
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
            return {"hwnd": 0, "process_name": "idle", "window_title": "", "pid": 0}

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        window_title = buf.value.strip()

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = "unknown.exe"

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h_process:
            try:
                exe_buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, exe_buf, ctypes.byref(size)):
                    full_path = exe_buf.value
                    process_name = os.path.basename(full_path)
            finally:
                kernel32.CloseHandle(h_process)

        return {
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "process_name": process_name,
            "window_title": window_title,
        }

    @staticmethod
    def safely_minimize_window(hwnd: int) -> bool:
        """Safely minimizes a restricted entertainment window without killing processes or locking the OS."""
        if os.name != "nt" or not hwnd:
            return False
        try:
            SW_MINIMIZE = 6
            ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
            return True
        except Exception:
            return False

    @staticmethod
    def safely_close_window(hwnd: int) -> bool:
        """Graceful native-window closure. Browser tabs belong to the central controller."""
        if os.name != "nt" or not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            import psutil
            from windows.restriction_manager import MONITORED_BROWSERS
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if psutil.Process(pid.value).name().lower() in MONITORED_BROWSERS:
                return False
            WM_CLOSE = 0x0010
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def lock_workstation_if_configured(enabled: bool = False) -> bool:
        """
        Section 71: Optional Windows workstation lock via user32.LockWorkStation().
        Only executes when explicitly enabled in user/admin settings.
        """
        if not enabled or os.name != "nt":
            return False
        try:
            return bool(ctypes.windll.user32.LockWorkStation())
        except Exception:
            return False

    _bt_cache: Optional[Tuple[float, List[str]]] = None

    @classmethod
    def get_connected_bluetooth_audio_devices(cls) -> List[str]:
        """Queries Windows PnP audio endpoints for active Bluetooth headphones/earbuds (cached for 90s)."""
        if os.name != "nt":
            return []
        now = time.time()
        if cls._bt_cache and (now - cls._bt_cache[0] < 90.0):
            return cls._bt_cache[1]
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-PnpDevice -Class AudioEndpoint -Status OK -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FriendlyName",
            ]
            out = subprocess.check_output(cmd, timeout=4, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            devices = []
            for line in out.splitlines():
                name = line.strip()
                if name:
                    devices.append(name)
            cls._bt_cache = (now, devices)
            return devices
        except Exception:
            return cls._bt_cache[1] if cls._bt_cache else []


class StrictRecordsWindowsAgent:
    def __init__(
        self,
        db: Optional[StrictRecordsDB] = None,
        engine: Optional[ScoringEngine] = None,
        device_id: str = "windows_lok_pc",
    ):
        self.db = db or StrictRecordsDB()
        self.engine = engine or ScoringEngine(self.db)
        self.device_id = device_id
        self.offline_queue_path = os.path.join(ROOT_DIR, "data", "windows_offline_queue.json")
        os.makedirs(os.path.dirname(self.offline_queue_path), exist_ok=True)
        self.shutdown_monitor = None

    def start_shutdown_monitor(self, server_url: str = "http://127.0.0.1:8765"):
        try:
            from windows.shutdown_monitor import WindowsShutdownMonitor
            self.shutdown_monitor = WindowsShutdownMonitor(
                db=self.db,
                server_url=server_url,
                device_id=self.device_id,
                account_id="user_lok"
            )
            self.shutdown_monitor.start()
            return self.shutdown_monitor
        except Exception as e:
            return None

    def categorize_window(
        self, process_name: str, window_title: str, idle_seconds: float, idle_threshold: float = 180.0
    ) -> str:
        if idle_seconds >= idle_threshold:
            return "IDLE"

        settings = self.db.get_settings()
        proc_lower = (process_name or "").lower()
        title_lower = (window_title or "").lower()
        combined = f"{proc_lower} {title_lower}"

        # 0. Check daily unblock / preferences exclusions
        try:
            from windows.strictbot_preferences import Preferences
            if proc_lower in Preferences().load().get("exclusions", []):
                return "LOCAL_MUSIC"
        except Exception:
            pass

        # 1. Check restricted streaming entertainment first (e.g. YouTube in Chrome, Spotify)
        for keyword in settings.restricted_windows_apps:
            if keyword.lower() in combined:
                return "ENTERTAINMENT"

        # 2. Check local music player exception (MP3 / local player)
        for keyword in settings.local_music_windows_apps:
            if keyword.lower() in combined:
                return "LOCAL_MUSIC"

        # 3. Check browser + educational / study keywords
        is_browser = any(b in proc_lower for b in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"))
        if is_browser:
            for study_kw in settings.study_browser_keywords:
                if study_kw.lower() in title_lower:
                    return "STUDY"

        # 4. Check productive IDE / work applications (VS Code, PyCharm, Android Studio, Terminal)
        for prod_kw in settings.productive_windows_apps:
            if prod_kw.lower() in combined:
                return "PRODUCTIVE"

        if not proc_lower or proc_lower in ("idle", "unknown.exe", "explorer.exe"):
            return "UNKNOWN"
        return "UNKNOWN"

    def sample_current_activity(
        self,
        sample_duration_seconds: float = 5.0,
        is_dev_mode: bool = False,
        enforce_restrictions: bool = True,
        show_gui_popup: bool = True,
    ) -> Dict[str, Any]:
        win_info = WindowsWin32Inspector.get_active_window_info()
        idle_sec = WindowsWin32Inspector.get_idle_duration_seconds()
        proc_name = win_info["process_name"]
        win_title = win_info["window_title"]

        settings = self.db.get_settings()
        if not settings.privacy_allow_window_titles:
            win_title = "[REDACTED_BY_PRIVACY_SETTING]"

        category = self.categorize_window(proc_name, win_title, idle_sec)
        signal_confidence = (
            "LOW"
            if category == "IDLE"
            else ("HIGH" if win_info.get("hwnd", 0) > 0 and idle_sec < 60.0 else "MEDIUM")
        )
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        hour = now.hour
        hour_slot = f"{max(0, hour - 1):02d}:00-{hour:02d}:00" if hour >= 8 else "07:00-08:00"

        # Heartbeat device registration
        self.db.register_or_heartbeat_device(
            device_id=self.device_id,
            device_name="Lok PC",
            platform="WINDOWS",
            sync_status="ONLINE",
        )

        # Log telemetry
        self.engine.log_device_activity(
            date=date_str,
            hour_slot=hour_slot,
            device_id=self.device_id,
            platform="WINDOWS",
            app_name=proc_name,
            window_title=win_title,
            category=category,
            duration_seconds=sample_duration_seconds,
            idle_seconds=idle_sec,
            is_dev_mode=is_dev_mode,
        )

        restriction_action = None
        if (
            enforce_restrictions
            and proc_name.lower() not in SAFE_SYSTEM_PROCESSES
            and (category in ("ENTERTAINMENT", "LOCAL_MUSIC") or "instagram" in f"{proc_name} {win_title}".lower())
        ):
            target_label = "YouTube" if "youtube" in f"{proc_name} {win_title}".lower() else ("Instagram" if "instagram" in f"{proc_name} {win_title}".lower() else (win_title or proc_name))
            access = self.engine.evaluate_entertainment_access(
                date=date_str,
                target_app=target_label,
                platform="WINDOWS",
                record_violation_if_blocked=False,
                device_id=self.device_id,
                is_dev_mode=is_dev_mode,
            )
            if not access["allowed"]:
                restriction_action = access
                from windows.restriction_manager import MONITORED_BROWSERS
                # Keep the existing eligibility/violation evaluation. The central
                # restriction controller owns one UI session and any tab action.
                if proc_name.lower() in MONITORED_BROWSERS or proc_name.lower() == "spotify.exe":
                    return {
                        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"), "device_id": self.device_id,
                        "hwnd": win_info["hwnd"], "pid": win_info["pid"], "process_name": proc_name,
                        "window_title": win_title, "idle_seconds": idle_sec, "category": category,
                        "signal_confidence": signal_confidence, "hour_slot": hour_slot,
                        "restriction_action": restriction_action,
                    }
                manager = getattr(self.db, "restriction_mgr", None)
                if manager is not None:
                    manager._wake_event.set()

        return {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "device_id": self.device_id,
            "hwnd": win_info["hwnd"],
            "pid": win_info["pid"],
            "process_name": proc_name,
            "window_title": win_title,
            "idle_seconds": idle_sec,
            "category": category,
            "signal_confidence": signal_confidence,
            "hour_slot": hour_slot,
            "restriction_action": restriction_action,
        }

    def show_strictbot_restriction_dialog(
        self, access_info: Dict[str, Any], hwnd_to_minimize: int = 0
    ) -> None:
        """
        Displays the native Windows StrictBot restriction dialog (Section 27):
        🤖 STRICTBOT — NOT ALLOWED — Current score / Required / Remaining — [ BACK TO WORK ]
        Safely minimizes the entertainment window when [ BACK TO WORK ] is pressed.
        Never blocks emergency or OS functions.
        """
        manager = getattr(self.db, "restriction_mgr", None)
        if manager is not None:
            manager._wake_event.set()

    def configure_windows_startup(enable: Optional[bool] = True) -> Dict[str, Any]:
        """Configures or queries Windows login startup in HKCU Run key (Section 18 & 25)."""
        if os.name != "nt":
            return {"supported": False, "enabled": False, "reason": "Not Windows"}
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        value_name = "StrictRecords"
        legacy_name = "ExamProctor"

        # Frozen bundles must restart their actual installed executable, never
        # an obsolete build under .dist or a script inside PyInstaller's temp dir.
        if getattr(sys, "frozen", False):
            cmd_str = subprocess.list2cmdline([sys.executable, "--background"])
        else:
            # Prefer the named, icon-bearing PyInstaller desktop build when a
            # source checkout has one available. Keep Python as a dev fallback.
            packaged_candidates = [
                os.path.join(ROOT_DIR, "dist", "StrictRecords", "StrictRecords.exe"),
            ]
            local_app = os.environ.get("LOCALAPPDATA")
            if local_app:
                packaged_candidates.insert(
                    0,
                    os.path.join(local_app, "StrictRecords", "Runtime", "StrictRecords.exe"),
                )

            packaged_exe = next(
                (candidate for candidate in packaged_candidates if os.path.isfile(candidate)),
                None,
            )
            if packaged_exe:
                cmd_str = subprocess.list2cmdline([packaged_exe, "--background"])
            else:
                # Use pythonw.exe in source/development mode to avoid a console.
                python_exe = sys.executable
                pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
                runner_exe = pythonw_exe if os.path.exists(pythonw_exe) else python_exe
                launcher_py = os.path.join(ROOT_DIR, "desktop_app.py")
                if not os.path.exists(launcher_py):
                    launcher_py = os.path.join(ROOT_DIR, "app.py")
                cmd_str = subprocess.list2cmdline([runner_exe, launcher_py, "--background"])

        reg_key_full = f"HKCU\\{key_path}\\{value_name}"
        if enable is None:
            # Query current state
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                    for v_name in (value_name, legacy_name):
                        try:
                            val, _ = winreg.QueryValueEx(key, v_name)
                            return {"supported": True, "enabled": bool(val), "key_name": v_name, "registry_key": reg_key_full, "command": val}
                        except FileNotFoundError:
                            pass
                    return {"supported": True, "enabled": False, "key_name": value_name, "registry_key": reg_key_full}
            except Exception as e:
                return {"supported": True, "enabled": False, "error": str(e), "key_name": value_name, "registry_key": reg_key_full}

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            # Clean up legacy key if present
            try:
                winreg.DeleteValue(key, legacy_name)
            except FileNotFoundError:
                pass

            if enable:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd_str)
                return {"supported": True, "enabled": True, "key_name": value_name, "registry_key": reg_key_full, "command": cmd_str}
            else:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    pass
                return {"supported": True, "enabled": False, "key_name": value_name, "registry_key": reg_key_full}


def main() -> None:
    parser = argparse.ArgumentParser(description="StrictRecords Windows Agent")
    parser.add_argument("--once", action="store_true", help="Sample active window once and print JSON")
    parser.add_argument("--daemon", action="store_true", help="Run continuous background monitoring loop")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--enable-startup", action="store_true", help="Opt-in to start agent on Windows login")
    parser.add_argument("--disable-startup", action="store_true", help="Remove agent from Windows login startup")
    args = parser.parse_args()

    agent = StrictRecordsWindowsAgent()
    if args.enable_startup:
        print(json.dumps(agent.configure_windows_startup(True), indent=2))
        return
    if args.disable_startup:
        print(json.dumps(agent.configure_windows_startup(False), indent=2))
        return
    if args.once:
        sample = agent.sample_current_activity(sample_duration_seconds=float(args.interval))
        print(json.dumps(sample, indent=2))
        return

    print("STRICTRECORDS AGENT started on Windows. Press Ctrl+C to stop safely.")
    try:
        while True:
            agent.sample_current_activity(
                sample_duration_seconds=float(args.interval),
                enforce_restrictions=True,
                show_gui_popup=True,
            )
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print("\nSTRICTRECORDS AGENT stopped cleanly.")


if __name__ == "__main__":
    main()
