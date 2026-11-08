"""One native UI worker and one restriction card per foreground-entry session."""
import ctypes
import ctypes.wintypes
import logging
import queue
import threading

from windows.notification_style import BG, RestrictionCard, popup_geometry, style_titlebar

log = logging.getLogger("RestrictionPopup")


class EntrySession:
    def __init__(self):
        self._lock = threading.Lock()
        self.identity = None
        self.session_id = 0
        self.presented = False

    def observe(self, identity):
        with self._lock:
            if identity != self.identity:
                self.identity = identity
                self.session_id += 1
                self.presented = False
            return self.session_id

    def claim(self, session_id):
        with self._lock:
            if self.identity is None or session_id != self.session_id or self.presented:
                return False
            self.presented = True
            return True


def bounded_geometry(bounds, scale=1.0, requested_height=460):
    left, top, right, bottom = bounds
    margin = max(8, round(16 * scale))
    width = min(round(620 * scale), right - left - margin * 2)
    height = min(round(requested_height), bottom - top - margin * 2)
    return width, height, left + (right - left - width) // 2, top + (bottom - top - height) // 2


def monitor_bounds(hwnd, fallback):
    try:
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("monitor", ctypes.wintypes.RECT),
                        ("work", ctypes.wintypes.RECT), ("flags", ctypes.wintypes.DWORD)]
        user = ctypes.windll.user32
        user.MonitorFromWindow.restype = ctypes.wintypes.HANDLE
        monitor = user.MonitorFromWindow(ctypes.wintypes.HWND(hwnd), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.work
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return fallback


class RestrictionPopupController:
    def __init__(self, on_home, on_close=None):
        self.on_home = on_home
        self.on_close = on_close
        self._close_pending = set()
        self.commands = queue.Queue()
        self._start_lock = threading.Lock()
        self._worker = None
        self._home_lock = threading.Lock()
        self._home_pending = set()
        self._stopping = False
        self.visible = False
        self._preview_active = False
        from windows.desktop_overlay_coordinator import DesktopOverlayCoordinator
        self.overlays = DesktopOverlayCoordinator(self._project_overlays)
        self.on_open_submission = None

    def _project_overlays(self, state, open_submission):
        # Do not start WPF for an idle scheduler snapshot.
        if state['ROBOT_RESTRICTION_ACTIVE'] or state['HOURLY_ALERT_VISIBLE'] or self._worker:
            self._ensure_worker()
            self.commands.put(('coordinate', None, (state, open_submission)))

    def synchronize_overlays(self):
        """UI barrier: hide native topmost surfaces before granting the WebView."""
        if not self._worker and not self.overlays.snapshot()['ROBOT_RESTRICTION_ACTIVE']:
            return True
        done = threading.Event()
        self._ensure_worker()
        self.commands.put(('coordinate_barrier', None, done))
        return done.wait(5)

    def _ensure_worker(self):
        with self._start_lock:
            if self._stopping:
                return
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, daemon=True, name="RestrictionPopupUI")
                self._worker.start()

    def show(self, session_id, data):
        self._ensure_worker()
        self.commands.put(("show", session_id, dict(data)))

    def show_settings(self):
        self._ensure_worker()
        self.commands.put(("settings", None, None))

    def show_night(self,data):
        self._ensure_worker()
        self.commands.put(("night",None,dict(data)))

    def refresh_preferences(self):
        if self._worker and self._worker.is_alive():
            self.commands.put(("configure", None, None))

    def preview_settings(self, values):
        if self._stopping or (self.visible and not self._preview_active):
            return False
        self._ensure_worker()
        self.commands.put(("preview", -1, dict(values)))
        return True

    def close(self):
        self.commands.put(("close", None, None))

    def stop(self):
        self._stopping = True
        self.commands.put(("stop", None, None))
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=2.0)

    def request_home(self, session_id, data, completed):
        """Never run browser/desktop calls on Tk; one pending action per entry."""
        with self._home_lock:
            if self._stopping or session_id in self._home_pending:
                return False
            self._home_pending.add(session_id)
        def run():
            result = False
            try:
                result = bool(self.on_home(session_id, dict(data)))
            except Exception:
                log.exception("platform=Windows navigation=home_failed")
            finally:
                with self._home_lock:
                    self._home_pending.discard(session_id)
                completed(result)
        threading.Thread(target=run, daemon=True, name="RestrictionHomeAction").start()
        return True

    def _run(self):
        from windows.strictbot_native import NativePresentation
        from windows.strictbot_preferences import Preferences
        current_id=None
        current_data={}
        presentation=NativePresentation(lambda event:self.commands.put(("native_event",event.get("session"),event)))
        self.native_presentation=presentation

        def close_action(entry,data):
            with self._home_lock:
                if entry in self._close_pending or self._stopping:return
                self._close_pending.add(entry)
            def run():
                result={"verified":False,"message":"Application closure is unavailable."}
                try:
                    for attempt in range(3):
                        if self._stopping:break
                        if self.on_close is not None:result=self.on_close(entry,dict(data))
                        if result.get("verified") or result.get("released"):break
                        if attempt<2:
                            presentation.host.Progress(entry,f"Unable to close this application. Retrying... ({attempt+1}/3)")
                            threading.Event().wait(.4)
                    self.commands.put(("close_done",entry,result))
                except Exception:
                    log.exception("Native application close action failed")
                    self.commands.put(("close_done",entry,{"verified":False,"message":"Unable to close this application. Open StrictRecords for details."}))
                finally:
                    with self._home_lock:self._close_pending.discard(entry)
            threading.Thread(target=run,daemon=True,name="StrictBotVerifiedClose").start()

        try:
            while True:
                action,entry,data=self.commands.get()
                if action=="stop":break
                if action=="close":
                    # Policy reconciliation owns real warnings. A user-opened
                    # appearance preview stays until Home, shutdown or a real warning.
                    if self._preview_active:continue
                    presentation.close();self.visible=False;self._preview_active=False
                    self.overlays.robot_resolved(current_id)
                elif action=='coordinate':
                    state, open_submission = data
                    presentation.coordinate(state)
                    if open_submission and self.overlays.can_open(open_submission) and self.on_open_submission:
                        # Never wait for a WebView/native barrier on this queue.
                        threading.Thread(target=self.on_open_submission, args=(open_submission,),
                                         daemon=True, name='HourlyOverlayOpen').start()
                elif action=='coordinate_barrier':
                    presentation.coordinate(self.overlays.snapshot())
                    data.set()
                elif action=="night":
                    import json
                    presentation.host.NightCountdown(json.dumps(data))
                elif action=="settings":presentation.settings()
                elif action=="configure":presentation.configure()
                elif action=="preview":
                    if self.visible and not self._preview_active:continue
                    current_id,current_data=-1,dict(preview=True,robot_preview=True,target_app="StrictBot Preview")
                    presentation.coordinate(self.overlays.robot_started(-1))
                    presentation.preview(data);self.visible=True;self._preview_active=True
                elif action=="show":
                    current_id,current_data=entry,data
                    presentation.coordinate(self.overlays.robot_started(entry))
                    presentation.show(entry,data);self.visible=True;self._preview_active=False
                elif action=="home_done" and entry==current_id:
                    if data:
                        presentation.host.Result(entry,True,"Application exit verified.",True);self.visible=False
                        self.overlays.robot_resolved(entry)
                    else:presentation.host.Result(entry,False,"The application is still open. Use CLOSE IT to close it.",False)
                    self.overlays.home_finished()
                elif action=="close_done" and entry==current_id:
                    if data.get("released"):
                        presentation.close();self.visible=False
                        self.overlays.robot_resolved(entry)
                    else:
                        verified=bool(data.get("verified"))
                        presentation.host.Result(entry,verified,data.get("message","Closure not confirmed."),verified)
                        if verified:
                            self.visible=False
                            self.overlays.robot_resolved(entry)
                elif action=="native_event":
                    kind=data.get("action")
                    if kind=="stage":
                        log.info("session=%s native_3d_stage=%s",entry,data.get("values"))
                        self.overlays.robot_stage_changed(entry,data.get('values'))
                    elif kind=='open_submission':self.overlays.open_pending()
                    elif kind=="native_error":
                        self.visible=False;self.overlays.robot_resolved(entry)
                        log.error("Native 3D presentation error: %s",data.get("values"))
                    elif kind=="save_settings":
                        try:
                            Preferences().save(data.get("values") or {})
                            presentation.configure()
                            presentation.host.SettingsSaved(True,"Settings saved")
                        except Exception:
                            log.exception("Could not save native StrictBot settings")
                            presentation.host.SettingsSaved(False,"Could not save. Try again.")
                    elif kind=="home":
                        self.overlays.home_requested()
                        if entry<0:
                            presentation.close();self.visible=False;self._preview_active=False
                            self.overlays.robot_resolved(entry)
                            self.overlays.home_finished()
                        elif entry==current_id:
                            self.request_home(entry,current_data,lambda result,token=entry:self.commands.put(("home_done",token,result)))
                    elif kind=="close":
                        if entry<0:presentation.host.Result(entry,False,"PREVIEW — No restricted application is attached.",False)
                        elif entry==current_id:close_action(entry,current_data)
        except Exception:
            log.exception("Native StrictBot presentation stopped unexpectedly")
        finally:
            self.visible=False;presentation.stop()
