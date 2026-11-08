"""
STRICTROUTINES — Native Windows Desktop Application
Standalone Edge WebView2 Desktop Shell.
Zero CMD / PowerShell window requirement for end users.
Complies with Windows Desktop Packaging & Restriction Specifications.
"""

import argparse
from datetime import datetime
import logging
import os
import sys
import threading
import time
import urllib.request
import urllib.parse
import json

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUILD_VERIFICATION_ONLY = any(flag in sys.argv for flag in ('--verify-build-report', '--verify-overlay-ui'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

if __name__=='__main__' and '--notification-watchdog' in sys.argv:
    from windows.strict_notification_filter import watch
    watch()
    raise SystemExit(0)

# Native diagnostic mode exits before registry, database, server or monitoring startup.
if __name__ == "__main__" and any(flag in sys.argv for flag in ("--strictbot-preview", "--strictbot-settings")):
    from windows.strictbot_preview import main as strictbot_preview_main
    strictbot_preview_main()
    raise SystemExit(0)

# Set Windows AppUserModelID immediately so the Windows Taskbar associates the window
# with StrictRoutines and displays the custom StrictRoutines logo instead of the Python icon.
APP_USER_MODEL_ID = "StrictRecords.StrictRoutines.Desktop"

if sys.platform == "win32" and not _BUILD_VERIFICATION_ONLY:
    try:
        import ctypes
        # Set AppUserModelID immediately before any GUI or shell objects are initialized
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass

        # Also register AppUserModelID in HKCU if not already registered
        try:
            import winreg
            key_path = r"Software\Classes\AppUserModelId\StrictRecords.StrictRoutines.Desktop"
            _ico = os.path.join(ROOT_DIR, "app_icon.ico")
            if os.path.exists(_ico):
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as _k:
                    winreg.SetValueEx(_k, "DisplayName", 0, winreg.REG_SZ, "StrictRoutines")
                    winreg.SetValueEx(_k, "IconUri", 0, winreg.REG_SZ, os.path.abspath(_ico))
        except Exception:
            pass

        # Enable Per-Monitor v2 DPI Awareness for flexible, sharp UI scaling
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        # Attach thread to interactive user desktop (WinSta0\Default) immediately
        u32 = ctypes.windll.user32
        DESKTOP_ALL = 0x01FF
        h_def = u32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
        if not h_def:
            h_def = u32.OpenInputDesktop(0, False, DESKTOP_ALL)
        if h_def:
            u32.SetThreadDesktop(h_def)
    except Exception as e:
        pass

# =========================================================================
# ABSOLUTE SINGLE INSTANCE ENFORCEMENT VIA SYSTEM-WIDE NAMED MUTEX
# =========================================================================
_SINGLE_INSTANCE_MUTEX = None
_WIN32_ICON_CACHE = {}

def bounded_cleanup(action, timeout=2.0):
    """A stalled native UI destroy must not block deliberate application exit."""
    finished = threading.Event()
    def run():
        try: action()
        except Exception: pass
        finally: finished.set()
    threading.Thread(target=run, daemon=True, name="BoundedNativeCleanup").start()
    return finished.wait(timeout)

def _enforce_single_instance_early():
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return
    try:
        from windows.single_instance_manager import enforce_single_instance_or_exit
        enforce_single_instance_or_exit(host="127.0.0.1", port=8765, background=False)
    except Exception as e:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            ERROR_ALREADY_EXISTS = 183
            mutex = kernel32.CreateMutexW(None, True, "Local\\StrictRecords_App_Mutex_v2")
            last_err = kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                # Notify user
                try:
                    from windows.notification_service import _try_toast_notification
                    _try_toast_notification(
                        "StrictRecords — Already Running",
                        "StrictRecords is already active. Duplicate instance blocked.",
                    )
                except Exception:
                    pass
                sys.exit(0)
            _SINGLE_INSTANCE_MUTEX = mutex
        except Exception:
            pass

# _enforce_single_instance_early is called inside run_desktop_app and __main__ to avoid triggering on import

# Initialize .NET CLR & WinForms references early so Action delegates and WinForms invocations work seamlessly
HAS_WINFORMS = False
WinForms = None
Action = None
if sys.platform == "win32":
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms
        from System import Action
        HAS_WINFORMS = True
    except Exception:
        pass

# Configure logging to disk so headless / noconsole operation is completely traceable
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ROOT_DIR), "StrictRecords")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "strictrecords.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
)
logger = logging.getLogger("StrictRoutinesApp")

_STARTUP_SPLASH_LOCK = threading.Lock()
_STARTUP_SPLASH_CLOSED = False
_SPLASH_MMAP = None


def _get_splash_mmap():
    global _SPLASH_MMAP
    if sys.platform != "win32":
        return None
    if _SPLASH_MMAP is None:
        try:
            import mmap
            _SPLASH_MMAP = mmap.mmap(-1, 2048, tagname="Local\\StrictRecords_Splash_Status")
        except Exception:
            _SPLASH_MMAP = None
    return _SPLASH_MMAP


def _update_startup_splash(message: str):
    """Update the startup splash while the app starts (supports native C# launcher via MMF & PyInstaller)."""
    with _STARTUP_SPLASH_LOCK:
        if _STARTUP_SPLASH_CLOSED:
            return
        # 1. Update native C# launcher via MemoryMappedFile
        if sys.platform == "win32":
            try:
                m = _get_splash_mmap()
                if m is not None:
                    m.seek(0)
                    msg_bytes = message.encode("utf-8")[:1000]
                    m.write(msg_bytes + b"\x00")
            except Exception as exc:
                logger.debug(f"Launcher splash status update skipped: {exc}")

        # 2. Update optional PyInstaller splash if alive
        try:
            import pyi_splash
            if pyi_splash.is_alive():
                pyi_splash.update_text(message)
        except Exception as exc:
            logger.debug(f"Startup splash update skipped: {exc}")


def _close_startup_splash():
    """Close the boot splash once the dashboard is ready (or startup is ending)."""
    global _STARTUP_SPLASH_CLOSED, _SPLASH_MMAP
    with _STARTUP_SPLASH_LOCK:
        if _STARTUP_SPLASH_CLOSED:
            return
        _STARTUP_SPLASH_CLOSED = True
        if sys.platform == "win32":
            try:
                import ctypes
                EVENT_MODIFY_STATE = 0x0002
                h_event = ctypes.windll.kernel32.OpenEventW(EVENT_MODIFY_STATE, False, "Local\\StrictRecords_Splash_Close_Event")
                if h_event:
                    ctypes.windll.kernel32.SetEvent(h_event)
                    ctypes.windll.kernel32.CloseHandle(h_event)
            except Exception as exc:
                logger.debug(f"Launcher splash close event signal skipped: {exc}")

            if _SPLASH_MMAP is not None:
                try:
                    _SPLASH_MMAP.close()
                except Exception:
                    pass
                _SPLASH_MMAP = None

        try:
            import pyi_splash
            if pyi_splash.is_alive():
                pyi_splash.close()
        except Exception as exc:
            logger.debug(f"Startup splash close skipped: {exc}")

import webview
try:
    import webview.platforms.winforms as _wf
    if hasattr(_wf, "BrowserView") and hasattr(_wf.BrowserView, "BrowserForm"):
        if HAS_WINFORMS and hasattr(_wf.BrowserView.BrowserForm, "on_closing"):
            _original_form_closing = _wf.BrowserView.BrowserForm.on_closing

            def _safe_form_closing(self, sender, args):
                # Install before any native form exists. OS/session termination
                # must bypass close-to-tray even while WebView2 is still loading.
                if args.CloseReason in (WinForms.CloseReason.WindowsShutDown, WinForms.CloseReason.TaskManagerClosing):
                    args.Cancel = False
                    return
                return _original_form_closing(self, sender, args)

            _wf.BrowserView.BrowserForm.on_closing = _safe_form_closing

        def _safe_move(self, x, y):
            try:
                scale = getattr(self, '_scale', 1.0)
                x_phys = int(x * scale) if scale != 1 else int(x)
                y_phys = int(y * scale) if scale != 1 else int(y)
                from ctypes import windll
                handle = int(self.Handle.ToInt64() if hasattr(self.Handle, "ToInt64") else self.Handle.ToInt32())
                windll.user32.SetWindowPos(
                    handle,
                    0,
                    x_phys,
                    y_phys,
                    0,
                    0,
                    0x0001 | 0x0004 | 0x0040  # SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW
                )
            except Exception as _e:
                logger.debug(f"_safe_move exception: {_e}")
        _wf.BrowserView.BrowserForm.move = _safe_move
except Exception as _patch_err:
    pass
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import create_server
from windows.agent import StrictRecordsWindowsAgent
from windows.restriction_manager import YouTubeRestrictionManager


def is_server_running(host: str, port: int) -> bool:
    try:
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
        url = f"http://{probe_host}:{port}/api/system/health"
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_for_server(host: str, port: int, timeout: float = 6.0) -> bool:
    start_t = time.time()
    while time.time() - start_t < timeout:
        if is_server_running(host, port):
            return True
        time.sleep(0.15)
    return False


def start_windows_agent_daemon(agent: StrictRecordsWindowsAgent, interval: int = 8, stop_event: threading.Event = None):
    logger.info("Windows activity agent daemon loop started (Low-CPU background mode).")
    while not (stop_event and stop_event.is_set()):
        try:
            agent.sample_current_activity(
                sample_duration_seconds=float(interval),
                enforce_restrictions=True,
                show_gui_popup=True,
            )
        except Exception as e:
            logger.error(f"Error sampling Windows activity: {e}")
            if stop_event:
                stop_event.wait(1.0)
            else:
                time.sleep(1)
        if stop_event:
            stop_event.wait(max(2, interval))
        else:
            time.sleep(max(2, interval))
    logger.info("Windows activity agent daemon loop terminated.")


# Configure unhandled exception hooks for logging
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught exception:", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

if hasattr(threading, "excepthook"):
    threading.excepthook = lambda args: logger.error(
        f"Uncaught thread exception in {args.thread.name}: {args.exc_value}",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _win32_force_foreground(hwnd: int, maximize: bool = False) -> bool:
    """Restore this existing window and request foreground once, respecting Windows policy."""
    if not hwnd or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        if not user32.IsWindow(hwnd):
            return False
        user32.ShowWindowAsync(hwnd, 3 if maximize else 9)
        # HWND_TOP, preserving position/size; no permanent topmost state or desktop switch.
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040 | 0x4000)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception as error:
        logger.debug("Native restore request failed: %s", error)
        return False

def _apply_win32_icon(hwnd: int, icon_path: str) -> bool:
    if sys.platform != "win32" or not hwnd or not icon_path or not os.path.exists(icon_path):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        GCLP_HICON = -14
        GCLP_HICONSM = -34

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        key = os.path.abspath(icon_path)
        if key not in _WIN32_ICON_CACHE:
            _WIN32_ICON_CACHE[key] = (
                user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE),
                user32.LoadImageW(None, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE))
        h_icon_big, h_icon_sm = _WIN32_ICON_CACHE[key]

        if h_icon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_icon_big)
        if h_icon_sm:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_icon_sm)

        SetClassLongPtr = getattr(user32, "SetClassLongPtrW", getattr(user32, "SetClassLongW", None))
        if SetClassLongPtr:
            SetClassLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            SetClassLongPtr.restype = ctypes.c_ssize_t
            if h_icon_big:
                SetClassLongPtr(hwnd, GCLP_HICON, h_icon_big)
            if h_icon_sm:
                SetClassLongPtr(hwnd, GCLP_HICONSM, h_icon_sm)
        return True
    except Exception as ex:
        logger.debug(f"Could not apply Win32 taskbar icon: {ex}")
        return False


def create_native_settings_menu(api):
    """Native EXE menu. The dashboard HTML and HTTP routes are unchanged."""
    from webview.menu import Menu, MenuAction
    return [Menu("Settings", [
        MenuAction("StrictBot PC Settings", api.show_strictbot_settings),
        MenuAction("Application Settings", lambda: api.show_dashboard(tab="settings")),
        MenuAction("Import Brave ChatGPT session", api.import_brave_study_session_from_menu),
    ])]


class DesktopApi:
    """Exposed JS-accessible API bridge for Desktop Window Controls and System Operations."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, icon_path: str = None, stop_event: threading.Event = None):
        self._window = None
        self._is_maximized = False
        self._is_visible = True
        self.is_exiting = False
        self._hwnd = None
        self._first_close_notified = False
        self._host = host
        self._port = port
        self._icon_path = icon_path
        self._stop_event = stop_event
        self._server = None
        self._exit_request_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._shutdown_monitor = None
        self._db = None
        self._exit_challenge = None
        self._exit_challenge_init_lock = threading.Lock()
        self._strictbot_settings_controller = None
        self._strictbot_settings_lock = threading.Lock()
        self._overlay_controller_bound = None
        self._study_cookie_import_lock = threading.Lock()

    def _native_strictbot_controller(self):
        from core.server import StrictRecordsRequestHandler
        manager = getattr(StrictRecordsRequestHandler, "restriction_mgr", None)
        with self._strictbot_settings_lock:
            if manager is not None:
                if self._strictbot_settings_controller is not None:
                    self._strictbot_settings_controller.stop()
                    self._strictbot_settings_controller = None
                controller = manager._popup_controller
            else:
                # A client connected to an already running server still has local
                # presentation settings. This controller makes no access decisions.
                if self._strictbot_settings_controller is None:
                    from windows.restriction_popup import RestrictionPopupController
                    self._strictbot_settings_controller = RestrictionPopupController(lambda session, data: False)
                controller = self._strictbot_settings_controller
            return controller

    def get_desktop_restriction_status(self):
        """Native read-only snapshot; never impersonate a browser heartbeat."""
        from core.server import StrictRecordsRequestHandler
        manager = getattr(StrictRecordsRequestHandler, "restriction_mgr", None)
        if manager is not None:
            return manager.get_desktop_access_status()
        # A standalone background server can own the access engine. Read its
        # dedicated snapshot instead of creating another engine or browser visit.
        probe_host = "127.0.0.1" if self._host in ("0.0.0.0", "") else self._host
        url = f"http://{probe_host}:{self._port}/api/restrictions/desktop_access"
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return {"success": False, "error": "The live Windows access engine is unavailable."}

    def show_strictbot_settings(self):
        """Open the native character preferences from the EXE menu or tray."""
        self._native_strictbot_controller().show_settings()
        return {"success": True, "view": "native_strictbot_settings"}

    def open_media_privacy_settings(self, device):
        """User-invoked help for OS privacy denial; never changes a permission."""
        pages = {'camera': 'ms-settings:privacy-webcam', 'microphone': 'ms-settings:privacy-microphone'}
        if device not in pages:
            return {'success': False, 'error': 'Unknown media device'}
        try:
            os.startfile(pages[device])
            return {'success': True}
        except Exception:
            return {'success': False, 'error': 'Open Windows Settings → Privacy & security → Camera or Microphone.'}

    def resolve_strict_camera(self, label):
        """Match the tested browser input to the existing native camera backend."""
        from windows.strict_media_devices import match_camera_label
        try:
            camera = match_camera_label(str(label)[:512])
            return {'success': True, 'native_id': camera['native_id'], 'name': camera['name']}
        except Exception as error:
            return {'success': False, 'error': str(error)}

    def import_brave_study_session(self):
        """Explicit local action. Only import counts/status cross the JS bridge."""
        from windows.brave_study_session import import_into_desktop
        native = getattr(self._window, 'native', None) if self._window else None
        if native is None:
            return {'success': False, 'error': 'Wait for the desktop dashboard to load.'}
        if not self._study_cookie_import_lock.acquire(blocking=False):
            return {'success': False, 'error': 'A Brave session import is already in progress.'}
        try:
            return import_into_desktop(native)
        finally:
            self._study_cookie_import_lock.release()

    def import_brave_study_session_from_menu(self):
        # Native menu callbacks run in a worker; CookieManager stays on the UI
        # thread. The remote ChatGPT view has no DesktopApi/pywebview bridge.
        result = self.import_brave_study_session()
        message = (f"Imported {result['imported']} scoped cookies from Brave. "
                   "Open ChatGPT in Strict Session to confirm sign-in."
                   if result.get('success') else result.get('error', 'Import unavailable.'))
        try:
            from System import Action
            from System.Windows.Forms import MessageBox
            self._window.native.BeginInvoke(Action(lambda: MessageBox.Show(
                self._window.native, message, 'StrictRecords — Brave session')))
        except Exception:
            pass
        return result

    def _desktop_overlay_controller(self):
        controller = self._native_strictbot_controller()
        controller.on_open_submission = self._open_coordinated_hourly
        if self._overlay_controller_bound is not controller:
            self._overlay_controller_bound = controller
            native = getattr(self._window, 'native', None) if self._window else None
            visible = bool(self._is_visible and native is not None and native.Visible
                           and str(native.WindowState) not in {'Minimized', '1'})
            controller.overlays.main_changed(visible)
        return controller

    def _open_coordinated_hourly(self, row):
        self.show_dashboard(open_hourly_if_active=True)

    def coordinate_hourly_tasks(self, snapshot):
        # Only the existing scheduler's current OPEN hourly occurrence is pending.
        row = next((r for r in snapshot if r.get('type') == 'HOURLY'
                    and r.get('reminder_phase') in {'OPEN', 'BLOCKED_WAKEUP'}), None)
        return self._desktop_overlay_controller().overlays.reconcile(row)

    def request_hourly_overlay(self, activity_id, schedule_version=1, user_initiated=False):
        controller = self._desktop_overlay_controller()
        granted = controller.overlays.request_panel((str(activity_id), int(schedule_version or 1)), bool(user_initiated))
        if granted and not controller.synchronize_overlays():
            controller.overlays.panel_changed((str(activity_id), int(schedule_version or 1)), False)
            return {'granted': False, 'retry': True}
        return {'granted': granted}

    def hourly_overlay_visibility(self, activity_id, schedule_version, visible):
        self._desktop_overlay_controller().overlays.panel_changed((str(activity_id), int(schedule_version or 1)), bool(visible))
        return {'success': True}

    def hourly_overlay_submitted(self, activity_id, schedule_version):
        self._desktop_overlay_controller().overlays.submitted(activity_id, schedule_version)

    def _overlay_main_visibility(self, visible):
        self._desktop_overlay_controller().overlays.main_changed(visible)

    def get_strictbot_preferences(self):
        from windows.strictbot_preferences import Preferences, DEFAULTS, appearance
        return {"success": True, "preferences": appearance(Preferences().load()), "defaults": appearance(DEFAULTS)}

    def save_strictbot_preferences(self, values):
        from windows.strictbot_preferences import Preferences, merge_appearance, appearance
        try:
            store=Preferences()
            saved=store.save(merge_appearance(store.load(),values))
        except (OSError, ValueError, TypeError):
            logger.exception("Could not save StrictBot preferences from Controls")
            return {"success":False,"message":"Could not save StrictBot settings. Please try again."}
        self._native_strictbot_controller().refresh_preferences()
        return {"success":True,"preferences":appearance(saved)}

    def preview_strictbot_preferences(self, values):
        from windows.strictbot_preferences import Preferences, merge_appearance
        try:
            preview=merge_appearance(Preferences().load(),values)
        except (ValueError,TypeError):
            return {"success":False,"message":"Check the StrictBot settings and try again."}
        controller=self._native_strictbot_controller()
        if not controller.preview_settings(preview):
            return {"success":False,"message":"Finish the active restriction before opening a preview."}
        return {"success":True}

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def set_window(self, win):
        self._window = win
        self._enforce_form_icon()
        from core.server import StrictRecordsRequestHandler
        if StrictRecordsRequestHandler.strict_session_manager:
            from windows.embedded_strict_focus import EmbeddedStrictFocus
            StrictRecordsRequestHandler.strict_session_manager.embedded_focus=EmbeddedStrictFocus(self)

    def show_strict_focus(self):
        from core.server import StrictRecordsRequestHandler
        manager=StrictRecordsRequestHandler.strict_session_manager
        if manager and manager.embedded_focus:return bool(manager.embedded_focus.show())
        local=getattr(self,'_remote_strict_focus',None)
        return bool(local and local.show())

    def get_strict_setup_preview(self):
        """Read an already-consented setup frame through the local EXE bridge.

        This method never opens a camera or exposes a public HTTP preview route.
        """
        from core.server import StrictRecordsRequestHandler
        manager=StrictRecordsRequestHandler.strict_session_manager
        if manager is not None:
            if not manager.session_id and manager._setup_monitor.get('active'):
                return manager.study_hardware.preview()
            return dict(current=False,jpeg=None)
        cache=getattr(self,'_strict_setup_preview',None)
        return cache.read() if cache else dict(current=False,jpeg=None)

    def _enforce_form_icon(self):
        if not self._window or not self._icon_path or not os.path.exists(self._icon_path):
            return
        try:
            native = getattr(self._window, "native", None)
            if native is not None:
                def _apply_icon():
                    try:
                        import clr
                        clr.AddReference("System.Drawing")
                        from System.Drawing import Icon
                        native.Icon = Icon(self._icon_path)
                        native.ShowInTaskbar = True
                    except Exception as e:
                        logger.debug(f"Could not set native.Icon: {e}")
                if Action:
                    native.BeginInvoke(Action(_apply_icon))
                else:
                    native.BeginInvoke(_apply_icon)
        except Exception as e:
            logger.debug(f"_enforce_form_icon error: {e}")

    def set_server(self, srv):
        self._server = srv

    def _resolve_hwnd(self) -> int:
        if sys.platform != "win32":
            return 0
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32

            IGNORED_CLASSES = {"MSCTFIME UI", "IME", "OleMainThreadWndClass", "GDI+ Window", "Default IME"}

            def is_valid_window(h):
                if not h or not u32.IsWindow(h):
                    return False
                buf = ctypes.create_unicode_buffer(256)
                u32.GetClassNameW(h, buf, 256)
                cls = buf.value
                if cls in IGNORED_CLASSES or cls.startswith("StrictRecordsTrayWnd") or cls.startswith("ConsoleWindowClass"):
                    return False
                return True

            # 1. Pywebview native WinForms handle check (highest authority)
            if self._window:
                native = getattr(self._window, "native", None)
                if native is not None and hasattr(native, "Handle"):
                    try:
                        h = int(native.Handle.ToInt64() if hasattr(native.Handle, "ToInt64") else int(native.Handle))
                        if is_valid_window(h):
                            self._hwnd = h
                            return h
                    except Exception:
                        pass

            # 2. Cached handle check
            if self._hwnd and is_valid_window(self._hwnd):
                return self._hwnd
            else:
                self._hwnd = None

            # 3. PID-based window enumeration with strict class filtering
            my_pid = os.getpid()
            found_hwnd = 0

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_proc(h, lParam):
                nonlocal found_hwnd
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == my_pid:
                    class_buf = ctypes.create_unicode_buffer(256)
                    u32.GetClassNameW(h, class_buf, 256)
                    cls_name = class_buf.value
                    if cls_name in IGNORED_CLASSES or cls_name.startswith("StrictRecordsTrayWnd") or cls_name.startswith("ConsoleWindowClass"):
                        return True
                    
                    title_buf = ctypes.create_unicode_buffer(256)
                    u32.GetWindowTextW(h, title_buf, 256)
                    title = title_buf.value

                    if cls_name.startswith("WindowsForms10.Window") or "StrictRecords" in title or "StrictRoutines" in title:
                        found_hwnd = h
                        return False
                return True

            cb = WNDENUMPROC(enum_proc)
            u32.EnumWindows(cb, 0)
            if found_hwnd and is_valid_window(found_hwnd):
                self._hwnd = int(found_hwnd)
                return self._hwnd

            # 4. Fallback search by title
            for title in ("StrictRecords", "StrictRecords • Desktop Application", "StrictRoutines", "StrictRoutines • Desktop Application"):
                h = u32.FindWindowW(None, title)
                if h and is_valid_window(h):
                    self._hwnd = int(h)
                    return self._hwnd
        except Exception as e:
            logger.debug(f"_resolve_hwnd error: {e}")
        return 0

    def minimize(self):
        if self._window:
            try:
                self._window.minimize()
                return {"success": True}
            except Exception as e:
                logger.error(f"Failed to minimize window: {e}")
        return {"success": False}

    def drag_window(self):
        """Allows dragging the frameless desktop window smoothly via native Win32."""
        if sys.platform == "win32":
            try:
                import ctypes
                u32 = ctypes.windll.user32
                hwnd = self._resolve_hwnd()
                if hwnd:
                    u32.ReleaseCapture()
                    WM_NCLBUTTONDOWN = 0x00A1
                    HTCAPTION = 0x0002
                    u32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
                    return {"success": True}
            except Exception as e:
                logger.debug(f"drag_window error: {e}")
        return {"success": False}

    def maximize(self):
        if self._window:
            try:
                native = getattr(self._window, "native", None)
                if native is not None and hasattr(native, "WindowState"):
                    # WinForms FormWindowState: 0=Normal, 1=Minimized, 2=Maximized
                    is_currently_max = (int(native.WindowState) == 2)
                else:
                    is_currently_max = self._is_maximized

                if is_currently_max:
                    self._window.restore()
                    self._is_maximized = False
                else:
                    self._window.maximize()
                    self._is_maximized = True
                return {"success": True, "maximized": self._is_maximized}
            except Exception as e:
                logger.error(f"Failed to maximize/restore window: {e}")
        return {"success": False}

    def hide_to_tray(self, show_first_close_tip: bool = False):
        """Hides the main dashboard window to the Windows system tray without stopping background services."""
        self._is_visible = False
        self._overlay_main_visibility(False)

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
            except Exception:
                pass

        def _async_hide():
            # 1. Direct WinForms native Hide if available
            try:
                native = getattr(self._window, "native", None) if self._window else None
                if native is not None:
                    def _do_native_hide():
                        try:
                            native.Hide()
                        except Exception:
                            pass
                    try:
                        if Action:
                            native.BeginInvoke(Action(_do_native_hide))
                        else:
                            native.BeginInvoke(_do_native_hide)
                    except Exception:
                        try:
                            native.Hide()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"native.Hide() error: {e}")

            # 2. Window.hide() call
            try:
                if self._window:
                    self._window.hide()
            except Exception as e:
                logger.debug(f"window.hide() error: {e}")

            # 3. Direct Win32 SW_HIDE
            if sys.platform == "win32":
                try:
                    import ctypes
                    u32 = ctypes.windll.user32
                    hwnd = self._resolve_hwnd()
                    if hwnd:
                        SW_HIDE = 0
                        u32.ShowWindow(hwnd, SW_HIDE)
                except Exception as e:
                    logger.debug(f"Win32 SW_HIDE error: {e}")

            # Native Hide does not update document.hidden in every WebView2 runtime.
            if self._window:
                try: self._window.evaluate_js("window._strictRecordsHidden = true;")
                except Exception: pass
                try:
                    native = getattr(self._window, "native", None)
                    from windows.webview_lifecycle import post_memory_target
                    post_memory_target(native, "Low", Action)
                except Exception:
                    pass

            if show_first_close_tip and not self._first_close_notified:
                self._first_close_notified = True
                try:
                    from windows.tray_manager import StrictRecordsTrayManager
                    tray = StrictRecordsTrayManager.get_existing_instance()
                    if tray:
                        tray.show_balloon(
                            "StrictRoutines — Running in Background",
                            "Dashboard hidden to the system tray. Scheduled checks and device sync remain active.",
                        )
                except Exception:
                    pass

        threading.Thread(target=_async_hide, daemon=True, name="SR_AsyncHide").start()
        logger.info("Dashboard window hidden to system tray. Background scheduler, sync, and monitoring remain active.")
        return {"success": True, "window_visible": False}

    def show_dashboard(self, tab: str = None, open_hourly_if_active: bool = False):
        """Restores and brings the main dashboard window to the foreground immediately."""
        coordinated_hourly = None
        if open_hourly_if_active:
            controller = self._desktop_overlay_controller()
            coordinated_hourly = controller.overlays.pending
            if coordinated_hourly:
                result = self.request_hourly_overlay(coordinated_hourly['activity_id'], coordinated_hourly.get('schedule_version') or 1, True)
                if not result['granted']:
                    return {'success': False, 'message': 'The submission window is still being prepared.'}
        self._overlay_main_visibility(True)
        now = time.time()
        native = getattr(self._window, "native", None) if self._window else None
        already_visible = self._is_visible and native is not None and bool(native.Visible) and str(native.WindowState) not in {"Minimized", "1"}
        if not open_hourly_if_active and hasattr(self, "_last_show_ts") and (now - self._last_show_ts < 1.0) and already_visible:
            return {"success": True, "debounced": True}
        self._last_show_ts = now

        self._is_visible = True

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00000020)
            except Exception:
                pass

        # Unblock pywebview @_shown_call wait(20) immediately so show()/restore() never hangs
        if self._window and hasattr(self._window, "events") and hasattr(self._window.events, "shown"):
            try:
                if not self._window.events.shown.is_set():
                    self._window.events.shown.set()
            except Exception:
                pass

        # 1. Immediate synchronous Win32 restoration (< 0.5ms) if already resolved
        hwnd = self._resolve_hwnd()
        logger.info(f"show_dashboard: initial resolved_hwnd={hwnd}, native={getattr(self._window, 'native', None)}")
        if hwnd:
            if not open_hourly_if_active:
                _win32_force_foreground(hwnd, maximize=False)
            if self._icon_path and os.path.exists(self._icon_path):
                _apply_win32_icon(hwnd, self._icon_path)

        def _async_show():
            # Wait briefly if native is still initializing in WebView2 thread (up to 3.5s)
            native = None
            for _ in range(25):
                native = getattr(self._window, "native", None) if self._window else None
                if native is not None:
                    break
                time.sleep(0.12)

            cur_hwnd = self._resolve_hwnd()
            logger.info(f"show_dashboard _async_show: cur_hwnd={cur_hwnd}, native={native}")
            if cur_hwnd:
                if not open_hourly_if_active:
                    _win32_force_foreground(cur_hwnd, maximize=False)
                if self._icon_path and os.path.exists(self._icon_path):
                    _apply_win32_icon(cur_hwnd, self._icon_path)

            # 2. Native WinForms restore
            try:
                if native is not None:
                    def _form_restore():
                        try:
                            logger.info(f"_form_restore executing on WinForms UI thread: native={native}")
                            # Prevent white screen flash by setting dark background color
                            try:
                                import clr
                                clr.AddReference("System.Drawing")
                                import System.Drawing as SD
                                native.BackColor = SD.Color.FromArgb(11, 15, 25)
                            except Exception:
                                pass

                            # Ensure native Form icon and taskbar appearance
                            if self._icon_path and os.path.exists(self._icon_path):
                                try:
                                    import clr
                                    clr.AddReference("System.Drawing")
                                    from System.Drawing import Icon
                                    native.Icon = Icon(self._icon_path)
                                except Exception:
                                    pass
                            try:
                                native.ShowInTaskbar = True
                            except Exception:
                                pass

                            if hasattr(native, "SuspendLayout"):
                                native.SuspendLayout()

                            native.Opacity = 1.0
                            native.Visible = True
                            if WinForms:
                                native.WindowState = WinForms.FormWindowState.Maximized if open_hourly_if_active else WinForms.FormWindowState.Normal
                            else:
                                native.WindowState = 2 if open_hourly_if_active else 0
                            # Recover a window saved outside the currently attached monitors.
                            try:
                                if WinForms and not any(native.Bounds.IntersectsWith(screen.WorkingArea) for screen in WinForms.Screen.AllScreens):
                                    area = WinForms.Screen.PrimaryScreen.WorkingArea
                                    native.Location = SD.Point(area.Left + max(0, (area.Width - native.Width) // 2), area.Top + max(0, (area.Height - native.Height) // 2))
                            except Exception:
                                pass
                            native.Show()
                            native.Activate()
                            native.BringToFront()
                            native.Focus()

                            # Force WinForms Form and WebView2 child control layout & repaint
                            wv = None
                            if hasattr(native, "browser") and getattr(native.browser, "webview", None):
                                wv = native.browser.webview
                            elif hasattr(native, "Controls"):
                                for i in range(native.Controls.Count):
                                    c = native.Controls[i]
                                    if "WebView2" in c.GetType().FullName:
                                        wv = c
                                        break

                            if wv is not None:
                                wv.Visible = True
                                wv.Dock = WinForms.DockStyle.Fill
                                wv.BringToFront()
                                if native.ClientSize.Width > 0 and native.ClientSize.Height > 0:
                                    wv.Size = native.ClientSize

                                core_wv = getattr(wv, "CoreWebView2", None)
                                if core_wv is not None:
                                    try:
                                        if getattr(core_wv, "IsSuspended", False):
                                            logger.info("Resuming suspended CoreWebView2...")
                                            core_wv.Resume()
                                    except Exception as ex:
                                        logger.debug(f"CoreWebView2.Resume error: {ex}")
                                    try:
                                        import clr
                                        clr.AddReference("Microsoft.Web.WebView2.Core")
                                        from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
                                        core_wv.MemoryUsageTargetLevel = CoreWebView2MemoryUsageTargetLevel.Normal
                                    except Exception:
                                        pass
                                    try:
                                        from windows.webview_lifecycle import ensure_webview2_permissions
                                        ensure_webview2_permissions(core_wv, native, f'http://{self._host}:{self._port}')
                                    except Exception:
                                        pass

                                wv.Invalidate()
                                wv.Update()
                                wv.Refresh()

                            if hasattr(native, "ResumeLayout"):
                                native.ResumeLayout(True)
                            native.PerformLayout()
                            native.Invalidate(True)
                            native.Update()

                            logger.info(f"_form_restore completed: Visible={native.Visible}")
                        except Exception as ex:
                            logger.error(f"_form_restore inner error: {ex}")
                    try:
                        if Action:
                            native.BeginInvoke(Action(_form_restore))
                        else:
                            native.BeginInvoke(_form_restore)
                    except Exception:
                        try:
                            _form_restore()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"WinForms native restore error: {e}")

            # 3. Pywebview window show / restore
            try:
                if self._window and not (open_hourly_if_active and native is not None):
                    try:
                        self._window.show()
                    except Exception:
                        pass
                    try:
                        if open_hourly_if_active:
                            self._window.maximize()
                            self._is_maximized = True
                        else:
                            self._window.restore()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"window.show()/restore() error: {e}")

            # 4. Secondary Win32 foreground enforcement
            if hwnd and not open_hourly_if_active:
                time.sleep(0.04)
                _win32_force_foreground(hwnd, maximize=open_hourly_if_active)

            # 5. Navigate tab or open hourly if requested & verify WebView2 health
            if self._window:
                app_target = f"http://{self._host}:{self._port}"
                try:
                    js_statements = []
                    # Health check: verify document body is rendered; reload if blank/white
                    js_statements.append(
                        "(function() { "
                        "  var b = document.body; "
                        "  if (!b || !b.children || b.children.length === 0) { "
                        "    window.location.reload(); "
                        "    return 'reloading'; "
                        "  } "
                        "  if (typeof loadState === 'function') loadState(); "
                        "  return 'ok'; "
                        "})()"
                    )
                    if tab:
                        safe_tab = str(tab).replace("'", "")
                        if safe_tab == "restrictions":
                            safe_tab = "privileges"
                        js_statements.append(f"if (typeof navAppPage === 'function') navAppPage('{safe_tab}');")
                    js_statements.append("window._strictRecordsHidden = false; window.dispatchEvent(new Event('strictrecords-visibility')); if (typeof pollHourlyStatus === 'function') pollHourlyStatus();")
                    if open_hourly_if_active:
                        if coordinated_hourly:
                            js_statements.append("window.openCoordinatedHourly(" + json.dumps(coordinated_hourly) + ");")
                        else: js_statements.append(
                            "if (typeof openActiveTaskOrHourlyOverlay === 'function') { openActiveTaskOrHourlyOverlay(); } "
                            "else if (typeof showHourlySubmissionOverlay === 'function') { showHourlySubmissionOverlay(false); }"
                        )
                    res = self._window.evaluate_js("; ".join(js_statements))
                    logger.info(f"show_dashboard evaluate_js result: {res}")
                except Exception as e:
                    logger.warning(f"evaluate_js on show_dashboard failed ({e}); forcing load_url to {app_target}")
                    try:
                        self._window.load_url(app_target)
                    except Exception as load_err:
                        logger.error(f"load_url fallback failed: {load_err}")

        threading.Thread(target=_async_show, daemon=True, name="SR_AsyncShow").start()
        logger.info(f"Dashboard window restored to foreground (tab={tab}, open_hourly={open_hourly_if_active}).")
        return {"success": True, "window_visible": True, "tab": tab}

    def show_from_tray(self, tab: str = None, open_hourly_if_active: bool = False):
        """Direct alias for show_dashboard for tray managers."""
        return self.show_dashboard(tab=tab, open_hourly_if_active=open_hourly_if_active)

    def close(self):
        """Close the dashboard to the system tray while background services continue."""
        return self.hide_to_tray(show_first_close_tip=True)

    def exit_app(self, key=None):
        """PIN starts the challenge; it never authorizes process exit by itself."""
        if self.is_exiting:
            return {"success": True, "status": "already_exiting"}
        if not self._exit_request_lock.acquire(blocking=False):
            return {"success": False, "status": "authentication_in_progress"}
        try:
            from windows.exit_authentication import prompt_exit_key
            try:
                pin = key if key is not None else prompt_exit_key(
                    getattr(self._window, "native", None) if self._window else None)
            except Exception:
                logger.warning("Exit authentication dialog unavailable; application continues running.")
                pin = None
            result = self.start_exit_challenge(pin)
            if not result.get('success'):
                return result
            self.show_dashboard()
            if self._window:
                import json
                try:
                    self._window.evaluate_js("window.openExitChallengeModal && window.openExitChallengeModal(" + json.dumps(result['challenge']) + ");")
                except Exception:
                    return {**result, 'status': 'challenge_saved', 'error': 'Open the Exit check in the dashboard to resume.'}
            return {**result, 'status': 'challenge_required'}
        finally:
            self._exit_request_lock.release()

    def _get_exit_challenge(self):
        with self._exit_challenge_init_lock:
            if self._exit_challenge is None:
                if self._db is None:
                    raise ValueError('Exit check storage is unavailable. StrictRoutines remains running.')
                from core.exit_challenge import ExitChallenge
                self._exit_challenge = ExitChallenge(self._db)
        return self._exit_challenge

    def start_exit_challenge(self, pin=None):
        from windows.exit_authentication import verify_exit_key
        if not verify_exit_key(pin):
            return {'success': False, 'error': 'Incorrect exit PIN or exit cancelled.'}
        try:
            return {'success': True, 'challenge': self._get_exit_challenge().start(pin)}
        except Exception as error:
            return {'success': False, 'error': str(error)}

    def answer_exit_challenge(self, challenge_id, question_id, answer):
        try:
            return {'success': True, 'challenge': self._get_exit_challenge().answer(challenge_id, question_id, answer)}
        except (ValueError, KeyError) as error:
            return {'success': False, 'error': str(error)}

    def complete_exit_challenge(self, challenge_id):
        try:
            return {'success': True, 'result': self._get_exit_challenge().complete(challenge_id)}
        except (ValueError, KeyError) as error:
            return {'success': False, 'error': str(error)}

    def exit_via_mcq(self, challenge_id=None):
        """Consume a current PIN-authenticated, server-graded, single-use exit grant."""
        if self.is_exiting:
            return {"success": True, "status": "already_exiting"}
        try:
            if not self._get_exit_challenge().consume(challenge_id):
                return {'success': False, 'error': 'Answer all 25 questions and score at least 24/25 before the deadline. Exit is not authorized.'}
        except (ValueError, KeyError):
            return {'success': False, 'error': 'No valid exit challenge. StrictRoutines remains running.'}
        if self._db:
            self._db.record_audit_log(
                category="APPLICATION",
                action="MCQ_EXIT",
                details=f"User authorized exit via Aptitude MCQ Challenge (challenge={challenge_id}).",
                device_id="windows_lok_pc",
            )
        self._exit_type = "MCQ_EXIT"
        return self._begin_shutdown()

    def exit_for_scheduled_event(self, event_id=None, snapshot_path=None):
        """Scheduled events also use the explicit exit check."""
        return self.exit_app()

    def _handle_windows_session_end(self, payload=None):
        """Called only by native OS session-end handlers; never exposed as a JS exit bypass."""
        self._exit_type = "WINDOWS_SHUTDOWN"
        return self._begin_shutdown()

    def _begin_shutdown(self):
        with self._shutdown_lock:
            if self.is_exiting:
                return {"success": True, "status": "already_exiting"}
            self.is_exiting = True
        logger.info("Explicit Exit StrictRoutines requested — shutting down services and removing tray icon.")
        self._is_visible = False

        if self._stop_event:
            self._stop_event.set()

        # 1. Remove system tray icon immediately
        try:
            from windows.tray_manager import StrictRecordsTrayManager
            tray = StrictRecordsTrayManager.get_existing_instance()
            if tray:
                tray.stop()
        except Exception as e:
            logger.error(f"Error stopping tray manager: {e}")

        if self._strictbot_settings_controller is not None:
            bounded_cleanup(self._strictbot_settings_controller.stop)
            self._strictbot_settings_controller = None

        # 2. Stop background services cleanly
        try:
            from core.server import StrictRecordsRequestHandler
            ns = getattr(StrictRecordsRequestHandler, "notification_service", None)
            if ns and hasattr(ns, "stop"):
                ns.stop()
            mgr = getattr(StrictRecordsRequestHandler, "strict_session_manager", None)
            if mgr:
                if hasattr(mgr, "shutdown"):
                    mgr.shutdown()
                elif hasattr(mgr, "stop"):
                    mgr.stop()
            if getattr(StrictRecordsRequestHandler, "restriction_mgr", None):
                StrictRecordsRequestHandler.restriction_mgr.stop_watcher()
        except Exception as e:
            logger.error(f"Error stopping background services on exit: {e}")

        # 3. Destroy window and shut down HTTP server asynchronously so caller gets clean response
        def _finalize_shutdown():
            time.sleep(0.15)
            if self._server:
                def stop_server():
                    self._server.shutdown()
                    self._server.server_close()
                bounded_cleanup(stop_server)
            if self._shutdown_monitor:
                self._shutdown_monitor.stop()
            if self._db:
                if getattr(self, "process_session_id", None):
                    try:
                        exit_type = getattr(self, "_exit_type", "PIN_EXIT")
                        self._db.record_process_session_clean_exit(self.process_session_id, exit_type=exit_type)
                    except Exception as e:
                        logger.warning(f"Could not record clean exit: {e}")
                try:
                    with self._db._connect() as connection:
                        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    logger.warning("Checkpoint deferred; committed local transactions remain recoverable.")
            if self._window:
                bounded_cleanup(self._window.destroy)
            # The UI loop's final cleanup releases ownership after every worker
            # is stopped; do not allow a new scheduler while this shell is alive.
            logger.info("StrictRoutines explicit exit complete.")

        threading.Thread(target=_finalize_shutdown, daemon=True, name="ExplicitExitThread").start()
        return {"success": True, "status": "exiting"}

    def get_health(self):
        try:
            url = f"http://{self._host}:{self._port}/api/system/health"
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    def get_task_reminders(self):
        from windows.submission_window_manager import SubmissionWindowManager
        manager = SubmissionWindowManager.get_instance()
        return {"tasks": list(manager._task_snapshot), "alert": bool(manager._task_snapshot), "hidden": not self._is_visible}

    def set_startup(self, enable: bool):
        try:
            return StrictRecordsWindowsAgent.configure_windows_startup(enable=enable)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def admin_release(self, pin: str, reason: str = "Desktop console release"):
        try:
            data = json.dumps({"pin": pin, "reason": reason}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{self._host}:{self._port}/api/restriction/admin_release",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "error": str(e)}


def _bring_existing_window_to_front(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """Signals running instance to self-restore from tray and brings Win32 window to foreground."""
    # Ask backend server to activate/unhide its managed window
    try:
        url = f"http://{host}:{port}/api/system/activate_window"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("window_restored") or data.get("success") or data.get("status") == "ok":
                return True
    except Exception:
        pass
    return False


_GLOBAL_MUTEX_HOLDER = None


def run_desktop_app():
    global _GLOBAL_MUTEX_HOLDER
    parser = argparse.ArgumentParser(description="StrictRecords Desktop Application")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start hidden in system tray without popping up window")
    args, _ = parser.parse_known_args()

    app_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host

    _update_startup_splash("Starting StrictRoutines...")

    # ── Strict Single-Instance Enforcement ─────
    try:
        from windows.single_instance_manager import enforce_single_instance_or_exit
        enforce_single_instance_or_exit(host=app_host, port=args.port, background=bool(args.background))
    except Exception:
        logger.exception("Could not establish single-instance ownership; startup stopped.")
        raise

    _GLOBAL_MUTEX_HOLDER = _SINGLE_INSTANCE_MUTEX

    app_url = f"http://{app_host}:{args.port}"
    logger.info(f"Starting StrictRoutines desktop app targeting {app_url} (background={args.background})...")
    _update_startup_splash("Loading configuration...")

    server = None
    stop_event = threading.Event()

    icon_path = os.path.join(ROOT_DIR, "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(ROOT_DIR, "windows", "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = r"C:\Users\lokad\AppData\Local\Programs\StrictRecords\app_icon.ico"
    if not os.path.exists(icon_path):
        icon_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "StrictRecords", "app_icon.ico")

    # Initialize Desktop JS API Bridge before server so server & tray can reference it immediately
    api = DesktopApi(host=app_host, port=args.port, icon_path=icon_path, stop_event=stop_event)

    # Start embedded backend server
    db = StrictRecordsDB()
    api._db = db
    if not is_server_running(args.host, args.port):
        _update_startup_splash("Initializing local storage...")
        logger.info("Initializing embedded backend server, database, and telemetry agents...")
        session_id = db.record_process_session_start(os.getpid())
        api.process_session_id = session_id
        StrictRecordsDB.active_process_session_id = session_id

        import atexit
        def _on_normal_process_exit():
            try:
                exit_t = getattr(api, "_exit_type", "NORMAL")
                db.record_process_session_clean_exit(session_id, exit_type=exit_t)
            except Exception:
                pass
        atexit.register(_on_normal_process_exit)

        from core.server import StrictRecordsRequestHandler
        StrictRecordsRequestHandler.desktop_api = api
        StrictRecordsRequestHandler.process_session_id = session_id
        if StrictRecordsRequestHandler.strict_session_manager:
            from windows.embedded_strict_focus import EmbeddedStrictFocus
            StrictRecordsRequestHandler.strict_session_manager.embedded_focus=EmbeddedStrictFocus(api)
        # Register once, preserving a later deliberate disable in Startup Settings.
        with db._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            registered = connection.execute("SELECT value FROM system_config WHERE key='windows_startup_initialized'").fetchone()
        if not registered:
            try:
                startup = StrictRecordsWindowsAgent.configure_windows_startup(enable=True)
                if startup.get("enabled"):
                    with db._connect() as connection:
                        connection.execute("INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES ('windows_startup_initialized','1',?)", (datetime.now().isoformat(),))
            except OSError:
                logger.warning("Windows startup registration unavailable; application remains usable.")
        engine = ScoringEngine(db=db)
        win_agent = StrictRecordsWindowsAgent(db=db, engine=engine)

        # Start telemetry daemon
        agent_thread = threading.Thread(
            target=start_windows_agent_daemon,
            args=(win_agent, 8, stop_event),
            daemon=True,
            name="WinActivityDaemon",
        )

        # Start HTTP server on all interfaces (0.0.0.0) for Android Wi-Fi pairing & sync
        _update_startup_splash("Connecting to required services...")
        server = create_server(host="0.0.0.0", port=args.port, db=db, engine=engine, win_agent=win_agent)
        server.process_session_id = session_id
        api.set_server(server)
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="ServerDaemon",
        )
        server_thread.start()
        agent_thread.start()

        if not wait_for_server(args.host, args.port):
            logger.error("Failed to start embedded server within timeout.")
    else:
        logger.info("Reusing existing active backend server.")

    # The atomic named mutex enforces ownership without process scans or kills.
    if sys.platform == "win32":
        try:
            import ctypes
            # Windows Error Reporting/Restart Manager owns crash/restart recovery;
            # no second watchdog application or always-running helper is installed.
            ctypes.windll.kernel32.RegisterApplicationRestart("--background", 0)
        except Exception:
            logger.debug("Windows application restart registration unavailable.")

    try:
        from core.server import StrictRecordsRequestHandler
        StrictRecordsRequestHandler.desktop_api = api
        StrictRecordsRequestHandler.process_session_id = getattr(api, "process_session_id", None)
        if hasattr(StrictRecordsRequestHandler, "restriction_mgr") and StrictRecordsRequestHandler.restriction_mgr:
            StrictRecordsRequestHandler.restriction_mgr.desktop_api = api
    except Exception:
        pass

    _update_startup_splash("Initializing the scheduler...")
    try:
        from windows.submission_window_manager import SubmissionWindowManager
        from core.event_bus import EventBus
        swm = SubmissionWindowManager.get_instance(db=StrictRecordsDB(), event_bus=EventBus.get_instance())
        swm.set_desktop_api(api)
        swm.reconcile_on_startup()
    except Exception:
        pass

    # Start Native Windows System Tray Icon Manager
    try:
        _update_startup_splash("Preparing the workspace...")
        from windows.tray_manager import StrictRecordsTrayManager
        tray_mgr = StrictRecordsTrayManager.get_instance(
            icon_path=icon_path,
            on_open_dashboard=api.show_dashboard,
            on_open_settings=lambda: api.show_dashboard(tab="settings"),
            on_strictbot_settings=api.show_strictbot_settings,
            on_exit_app=api.exit_app,
            server_port=args.port,
            desktop_api=api,
        )
        tray_mgr.start()
    except Exception as e:
        logger.error(f"Failed to start system tray manager: {e}")

    # Create native WebView2 desktop window (frameless to eliminate duplicate OS titlebar)
    window = webview.create_window(
        title="StrictRecords",
        url=app_url,
        js_api=api,
        width=1280,
        height=800,
        min_size=(640, 480),
        resizable=True,
        frameless=True,
        easy_drag=False,
        confirm_close=False,
        background_color="#0b0f19",
        hidden=bool(args.background),
    )
    api.set_window(window)
    api._is_visible = not bool(args.background)

    def _attach_webview2_resilience(win, target_url: str):
        # CoreWebView2 getters and event subscriptions must also use its UI thread.
        from windows.webview_lifecycle import post_recovery_handlers
        def attach_when_created():
            for _ in range(80):
                if stop_event and stop_event.wait(0.25):
                    return
                native = getattr(win, "native", None)
                if native is not None:
                    post_recovery_handlers(native, target_url, Action)
                    return
        threading.Thread(target=attach_when_created, daemon=True, name="SR_WV2Resilience").start()

    _attach_webview2_resilience(window, app_url)

    def on_window_closing():
        """Intercept the window X so the dashboard hides while the app stays in the tray."""
        if api.is_exiting:
            return True
        logger.info("Window close (X) intercepted — hiding dashboard to the system tray.")
        threading.Thread(
            target=api.hide_to_tray,
            kwargs={"show_first_close_tip": True},
            daemon=True,
        ).start()
        return False

    def on_window_shown():
        native = getattr(window, "native", None)
        if native is not None and not native.Visible:
            return  # The startup-ready signal for a hidden window is not a user restore.
        set_presentation_visibility(True)
        api._resolve_hwnd()
        _apply_win32_icon(api._hwnd, icon_path)
        logger.info("Window shown event fired — dashboard window is now visible.")

    def set_presentation_visibility(visible):
        api._is_visible = visible
        api._overlay_main_visibility(visible)
        def update():
            try:
                window.evaluate_js("window._strictRecordsHidden = " + ("false" if visible else "true") + "; window.dispatchEvent(new Event('strictrecords-visibility'));")
            except Exception:
                pass
        threading.Thread(target=update, daemon=True, name="PresentationVisibility").start()

    def on_window_loaded():
        logger.info("Dashboard content loaded — closing startup splash.")
        _update_startup_splash("Ready.")
        api._overlay_main_visibility(api._is_visible)
        try:
            if not args.background:
                api.show_dashboard()
        except Exception:
            pass
        _close_startup_splash()
        # Desktop-owned observation also works with a separately running backend.
        # It only reads technical device state; scores/timers remain authoritative.
        try:
            if not getattr(api,'_strict_display_monitor',None):
                from windows.strict_display_monitor import StrictDisplayMonitor
                from core.database import _default_database_path
                api._strict_display_monitor=StrictDisplayMonitor(api,_default_database_path())
                api._strict_display_monitor.start()
            if not getattr(api,'_strict_focus_bridge',None):
                from windows.embedded_strict_focus import EmbeddedStrictFocus
                from windows.strict_focus_bridge import DesktopFocusBridge
                manager=StrictRecordsRequestHandler.strict_session_manager
                api._remote_strict_focus=(manager.embedded_focus if manager and manager.embedded_focus else EmbeddedStrictFocus(api))
                api._strict_focus_bridge=DesktopFocusBridge(api,api._remote_strict_focus)
                api._strict_focus_bridge.start()
                import atexit
                atexit.register(api._strict_focus_bridge.stop)
        except Exception:
            logger.exception('Could not initialize Strict Session display warnings')
        swm.reset_task_presentation()
        try:
            StrictRecordsRequestHandler.notification_service.request_reconcile()
        except Exception:
            pass
        # Never convert an OS shutdown/Task Manager close into a hide-to-tray veto.
        native = getattr(window, "native", None)
        if native is not None and HAS_WINFORMS:
            def native_closing(sender, event):
                if event.CloseReason in (WinForms.CloseReason.WindowsShutDown, WinForms.CloseReason.TaskManagerClosing):
                    api.is_exiting = True
                    stop_event.set()
                    event.Cancel = False
            native.FormClosing += native_closing
        try:
            swm.present_task_reminders(alert=False)
        except Exception:
            pass

    try:
        window.events.closing += on_window_closing
        window.events.shown += on_window_shown
        window.events.loaded += on_window_loaded
        window.events.minimized += lambda: set_presentation_visibility(False)
        window.events.restored += lambda: set_presentation_visibility(True)
        window.events.minimized += lambda: api._overlay_main_visibility(False)
        window.events.restored += lambda: api._overlay_main_visibility(True)
    except Exception as e:
        logger.error(f"Failed attaching window lifecycle events: {e}")

    # Proactively resolve HWND and unlock window.events.shown if started with --background
    def _bg_ready_watcher():
        for _ in range(60):
            time.sleep(0.2)
            native = getattr(window, "native", None)
            h = api._resolve_hwnd()
            if native is not None or h:
                # If hidden=True, pywebview never fires shown. Unblock events.shown so window.show() works!
                try:
                    if not window.events.shown.is_set():
                        window.events.shown.set()
                except Exception:
                    pass
                if args.background:
                    try:
                        from windows.tray_manager import StrictRecordsTrayManager
                        tray = StrictRecordsTrayManager.get_existing_instance()
                        if tray and tray.icon_registered:
                            tray.show_balloon("StrictRoutines", "StrictRoutines is active in background (system tray).")
                    except Exception:
                        pass
                break

    threading.Thread(target=_bg_ready_watcher, daemon=True, name="SR_BgReadyWatcher").start()

    # Win32 taskbar & window icon enforcer
    def _enforce_taskbar_icon():
        if sys.platform != "win32" or not os.path.exists(icon_path):
            return

        # Phase 1: Wait up to 60s for the window to appear and enforce icon
        for _ in range(120):
            if stop_event and stop_event.is_set():
                return
            time.sleep(0.5)
            hwnd = api._resolve_hwnd()
            if hwnd:
                _apply_win32_icon(hwnd, icon_path)
                api._enforce_form_icon()
                break

        # Icon ownership stays with the window. Explorer restarts are handled by
        # the tray's TaskbarCreated message; no recurring LoadImage/GDI allocation.

    threading.Thread(target=_enforce_taskbar_icon, daemon=True, name="IconEnforcer").start()

    shutdown_monitor = None
    try:
        from windows.shutdown_monitor import WindowsShutdownMonitor
        shutdown_monitor = WindowsShutdownMonitor(
            db=db,
            server_url=f"http://127.0.0.1:{args.port}",
            device_id="windows_lok_pc",
            account_id="user_lok",
            on_shutdown_callback=api._handle_windows_session_end,
        )
        api._shutdown_monitor = shutdown_monitor
        shutdown_monitor.start()
        logger.info("Windows PC Shutdown Monitor active.")
    except Exception as ex:
        logger.warning(f"Could not initialize WindowsShutdownMonitor: {ex}")

    logger.info("Launching WebView2 GUI shell...")
    if args.background:
        _update_startup_splash("Starting in the system tray...")
        _close_startup_splash()
    else:
        _update_startup_splash("Opening your workspace...")

        def _splash_startup_fallback():
            time.sleep(30)
            _close_startup_splash()

        threading.Thread(
            target=_splash_startup_fallback,
            daemon=True,
            name="StartupSplashFallback",
        ).start()
    # Explicit persistent storage directory in LocalAppData for Edge WebView2 runtime
    storage_path = os.path.join(os.environ.get("LOCALAPPDATA", ROOT_DIR), "StrictRecords", "WebView2")
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = storage_path
    try:
        os.makedirs(storage_path, exist_ok=True)
    except Exception:
        pass

    try:
        native_settings_menu = create_native_settings_menu(api)
        webview.start(
            debug=args.debug,
            icon=icon_path if os.path.exists(icon_path) else None,
            gui="edgechromium",
            storage_path=storage_path,
            private_mode=False,
            menu=native_settings_menu,
        )
    except Exception as e:
        logger.error(f"WebView runtime error: {e}")
    finally:
        _close_startup_splash()
        logger.info("Cleaning up desktop application...")
        if shutdown_monitor:
            try:
                shutdown_monitor.stop()
            except Exception:
                pass
        stop_event.set()
        try:
            from windows.tray_manager import StrictRecordsTrayManager
            tray = StrictRecordsTrayManager.get_instance()
            if tray:
                tray.stop()
        except Exception:
            pass
        if server:
            try:
                from core.server import StrictRecordsRequestHandler
                ns = getattr(StrictRecordsRequestHandler, "notification_service", None)
                if ns and hasattr(ns, "stop"):
                    ns.stop()
                mgr = getattr(StrictRecordsRequestHandler, "strict_session_manager", None)
                if mgr:
                    if hasattr(mgr, "shutdown"):
                        mgr.shutdown()
                    elif hasattr(mgr, "stop"):
                        mgr.stop()
                if getattr(StrictRecordsRequestHandler, "restriction_mgr", None):
                    StrictRecordsRequestHandler.restriction_mgr.stop_watcher()
            except Exception as e:
                logger.error(f"Error terminating background services: {e}")

            try:
                server.shutdown()
                server.server_close()
            except Exception as e:
                logger.error(f"Error shutting down server: {e}")
        if getattr(api, "process_session_id", None):
            try:
                exit_t = getattr(api, "_exit_type", "NORMAL")
                db.record_process_session_clean_exit(api.process_session_id, exit_type=exit_t)
            except Exception as e:
                logger.warning(f"Could not record clean exit in finally: {e}")
        from windows.single_instance_manager import release_single_instance
        release_single_instance()
        logger.info("StrictRoutines desktop terminated cleanly.")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.DestroyIcon.argtypes = [ctypes.c_void_p]
            for icons in _WIN32_ICON_CACHE.values():
                for handle in icons:
                    if handle: ctypes.windll.user32.DestroyIcon(handle)
            _WIN32_ICON_CACHE.clear()
        sys.exit(0)


def write_build_verification_report(path):
    """Exercise the frozen runtime/resources without opening user data or services."""
    import base64
    import hashlib
    from pathlib import Path
    from core.product_policy import POLICY
    from core.database import StrictRecordsDB
    original_constructor=StrictRecordsDB.__init__
    def reject_database_open(*args,**kwargs):
        raise RuntimeError('Build verification must not open any application database')
    StrictRecordsDB.__init__=reject_database_open
    try:
        from core.server import StrictRecordsRequestHandler
        from core.ui_dashboard import DASHBOARD_HTML
    finally:
        StrictRecordsDB.__init__=original_constructor
    manifest=json.loads((Path(ROOT_DIR)/'browser_extension/manifest.json').read_text(encoding='utf-8'))
    digest=hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension_id=''.join(chr(ord('a')+int(char,16)) for char in digest)
    assert extension_id=='iincmekgilffpjmmmcgloliadppioajc'
    assert 'schedGymDecision' in DASHBOARD_HTML and 'youtubeRewardCard' in DASHBOARD_HTML
    assert 'sharedClientPanel' in DASHBOARD_HTML
    assert 'identity' in manifest['permissions']
    assert (Path(ROOT_DIR)/'shared/client/client.html').is_file()
    assert 'pointsCloudStatus' in DASHBOARD_HTML
    import importlib.util
    google_modules_available = all(importlib.util.find_spec(name) is not None for name in
        ['core.google_auth', 'core.google_drive', 'core.drive_backup', 'core.client_parity'])
    if getattr(sys, 'frozen', False):
        assert google_modules_available, 'Shared account runtime must be bundled'
    report=dict(success=True,frozen=bool(getattr(sys,'frozen',False)),policy_version=POLICY['version'],
        extension_version=manifest['version'],extension_id=extension_id,server_imported=True,
        planning_ui_present=True,services_started=False,database_opened=False,
        shared_client_present=True,google_modules_available=google_modules_available,
        extension_google_setup_required='oauth2' not in manifest,points_cloud_status_present=True)
    from windows.strictbot_native import NativePresentation
    StrictRecordsDB.__init__=reject_database_open
    native=None
    try:
        native=NativePresentation(lambda event:None)
        report['night_native_countdown_available']=hasattr(native.host,'NightCountdown')
        report['strict_study_native_hud_available']=hasattr(native.host,'StudyStatus')
        report['strictbot_native_runtime']=native.metrics()
        report['strictbot_native_library_loaded']=True
    finally:
        if native is not None:native.stop()
        StrictRecordsDB.__init__=original_constructor
    library=Path(ROOT_DIR)/'assets/StrictBotNative.dll'
    report['strictbot_native_sha256']=hashlib.sha256(library.read_bytes()).hexdigest()
    report['strictbot_settings_integrated']=callable(DesktopApi.show_strictbot_settings)
    from windows.night_shutdown import NightStore,NightCoordinator
    from core.windows_app_catalog import CATALOG
    report['night_stage_integrated']=callable(NightCoordinator.tick)
    report['night_restart_fallback_integrated']='fallback_capture' in NightCoordinator.__init__.__code__.co_varnames
    report['night_history_assets_present']=all((Path(ROOT_DIR)/'browser_extension'/name).is_file() for name in ('night_shutdown.js','night_playback.js','night_history.js'))
    report['night_native_bridge_present']=(Path(ROOT_DIR)/'windows/StrictRecordsNativeBridge.exe').is_file()
    report['production_application_categories']=sorted(CATALOG)
    report['strictbot_settings_entries']=['Controls > StrictBot PC appearance and animation','Settings > StrictBot PC Settings','Tray > StrictBot PC Settings']
    assert 'strictbotPcSettingsCard' in DASHBOARD_HTML
    report['strictbot_controls_integrated']=all(callable(getattr(DesktopApi,name,None)) for name in
        ('get_strictbot_preferences','save_strictbot_preferences','preview_strictbot_preferences'))
    from windows.desktop_overlay_coordinator import DesktopOverlayCoordinator
    report['desktop_overlay_coordinator_integrated']=all(callable(getattr(DesktopApi,name,None)) for name in
        ('request_hourly_overlay','hourly_overlay_visibility','coordinate_hourly_tasks'))
    report['desktop_overlay_states']=sorted(DesktopOverlayCoordinator(lambda *args:None).snapshot())
    report['windows_brand_image_present']=(Path(ROOT_DIR)/'app_logo.png').is_file() and '/app_logo.png?v=windows-20261102' in DASHBOARD_HTML
    from windows.strict_study_workflow import StrictStudyWorkflow
    from windows.strict_study_hardware import StrictStudyHardware
    try:
        from windows.strict_study_hardware import face_model_path
    except ImportError:
        from windows.strict_guard_proctor import face_model_path
    import cv2
    model=face_model_path()
    import numpy as np
    detector=cv2.FaceDetectorYN.create(str(model),'',(320,240))
    detector.detect(np.zeros((240,320,3),dtype=np.uint8))
    report['strict_study_camera_runtime_present']=model.is_file() and detector is not None
    report['strict_study_ui_present']='strictStudyComplete' in DASHBOARD_HTML and 'strictStudyConsent' in DASHBOARD_HTML
    from windows.embedded_strict_focus import EmbeddedStrictFocus,allowed_url
    report['strict_prerequisite_views_present']=all(name in DASHBOARD_HTML for name in ('ssPrecheckView','ssJoinView','ssReadyView','ssFocusView'))
    report['strict_embedded_focus_present']=callable(EmbeddedStrictFocus.open) and allowed_url('https://chatgpt.com/') and not allowed_url('https://youtube.com/')
    from windows.strict_media_devices import resolve_camera_id
    from core.strict_companion import HTML as companion_html
    report['strict_input_selector_present']=all(name in DASHBOARD_HTML for name in ('ssCameraInput','deviceId:{exact:')) and callable(DesktopApi.resolve_strict_camera) and callable(resolve_camera_id)
    report['strict_microphone_selector_present']='ssMicrophoneInput' in DASHBOARD_HTML
    report['strict_timer_companion_present']='role="timer"' in companion_html and 'remaining_seconds' in companion_html and 'getUserMedia' not in companion_html
    report['strict_companion_reconnect_present']=all(name in companion_html for name in ('restoreConnection','client_nonce','Resume fullscreen timer'))
    from windows.strict_display_monitor import StrictDisplayMonitor
    from windows.strict_focus_bridge import DesktopFocusBridge,RemoteEmbeddedStrictFocus
    import pycaw.pycaw
    import comtypes
    report['strict_audio_dependencies_importable']=True
    report['strict_display_monitor_present']=callable(StrictDisplayMonitor.start) and 'resumeVisibleTimer' in companion_html
    report['strict_focus_bridge_present']=callable(DesktopFocusBridge.start) and callable(RemoteEmbeddedStrictFocus.open)
    from windows.brave_study_session import read_brave_chatgpt_cookies, apply_to_cookie_manager
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import win32crypt
    report['brave_chatgpt_import_present']=(callable(read_brave_chatgpt_cookies)
        and callable(apply_to_cookie_manager) and callable(DesktopApi.import_brave_study_session)
        and callable(AESGCM) and callable(win32crypt.CryptUnprotectData))
    assert report['brave_chatgpt_import_present']
    from core.strict_participants import REQUIRED_ROLES, COMPANION_ROLES
    report['strict_windows_ios_only']=(REQUIRED_ROLES==('WINDOWS','IOS') and COMPANION_ROLES==('IOS',)
        and 'Android (Companion)' not in DASHBOARD_HTML and 'nativeAndroid' not in companion_html
        and 'ssReadinessBlockers' in DASHBOARD_HTML)
    assert report['strict_windows_ios_only']
    from windows.strict_guard_proctor import StrictGuardProctor
    from windows.embedded_strict_focus import TAB_URLS,tab_for_url
    from windows.strict_notification_filter import StrictNotificationFilter,watch
    from core.strict_ipad_connection import GRACE_SECONDS,ipad_connection
    report['strict_two_fixed_tabs']=(set(TAB_URLS)=={'chatgpt','leetcode'}
        and tab_for_url('https://leetcode.com/problems/')=='leetcode'
        and tab_for_url('https://leetcode.com.evil.test/') is None)
    from windows.strict_focus_layout import StudyPaneLayout
    report['strict_split_workspace_present']=(callable(StudyPaneLayout.drop) and callable(StudyPaneLayout.resize)
        and StudyPaneLayout().geometry(1200,700)['divider'] is not None
        and StudyPaneLayout().geometry(600,700)['divider'] is None)
    assert report['strict_split_workspace_present']
    from windows.strict_preflight import hardware_checks
    from windows.strict_demo_workflow import DemoStudyWorkflow
    from windows.strict_session_manager import StrictSessionManager
    report['strict_live_preflight_present']=callable(StrictSessionManager.start_setup_hardware) and not hardware_checks({})['all_passed']
    demo=DemoStudyWorkflow(None);demo.begin('strict_sess_build_demo','offline_build_check')
    report['strict_ten_minute_demo_present']=demo.s['focus_target_seconds']==600 and demo.s['reward_status']=='NOT_APPLICABLE'
    assert report['strict_live_preflight_present'] and report['strict_ten_minute_demo_present']
    from core.strict_check_settings import HTML as check_html, JS as check_js
    report['strict_check_settings_present']=('id="ssCheckDialog"' in check_html
        and check_html in DASHBOARD_HTML and check_js in DASHBOARD_HTML)
    assert report['strict_check_settings_present']
    from core.strict_camera_preview import HTML as camera_html
    report['strict_setup_camera_preview_present']=camera_html in DASHBOARD_HTML and callable(DesktopApi.get_strict_setup_preview)
    assert report['strict_setup_camera_preview_present']
    report['strict_titlebar_single_drag_handler']=('pywebview-drag-region' not in DASHBOARD_HTML
        and 'onmousedown="handleTopbarDrag(event)"' in DASHBOARD_HTML)
    assert report['strict_titlebar_single_drag_handler']
    report['strict_left_alert_sixty_seconds']=StrictGuardProctor.MIN_SUSTAINED_SECONDS==60.0
    report['strict_private_camera_thumbnail']=callable(StrictGuardProctor.get_preview)
    report['strict_ipad_pause_guard']=GRACE_SECONDS==45.0 and callable(ipad_connection)
    report['strict_notification_restore_guard']=callable(StrictNotificationFilter.restore) and callable(watch)
    assert all(report[name] for name in ('strict_two_fixed_tabs','strict_left_alert_sixty_seconds',
        'strict_private_camera_thumbnail','strict_ipad_pause_guard','strict_notification_restore_guard'))
    assert report['strict_display_monitor_present'] and report['strict_focus_bridge_present']
    assert report['strict_companion_reconnect_present']
    assert report['strict_input_selector_present'] and report['strict_timer_companion_present']
    assert report['strict_prerequisite_views_present'] and report['strict_embedded_focus_present']
    assert report['strict_study_camera_runtime_present'] and report['strict_study_native_hud_available'] and report['strict_study_ui_present']
    destination=Path(path).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__ == "__main__":
    if '--verify-overlay-ui' in sys.argv:
        from windows.desktop_overlay_validation import run
        run(DesktopApi, sys.argv[sys.argv.index('--verify-overlay-ui')+1])
        sys.exit(0)
    if _BUILD_VERIFICATION_ONLY:
        destination=sys.argv[sys.argv.index('--verify-build-report')+1]
        try:
            write_build_verification_report(destination)
        except Exception as error:
            import traceback
            from pathlib import Path
            target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps({'success':False,'error':str(error),'traceback':traceback.format_exc()},indent=2),encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    try:
        run_desktop_app()
    except Exception as exc:
        logger.exception(f"Unhandled startup exception in run_desktop_app: {exc}")
        if sys.platform == "win32":
            try:
                _update_startup_splash(f"ERROR: {exc}")
                time.sleep(0.6)
            except Exception:
                pass
        _close_startup_splash()
        raise
