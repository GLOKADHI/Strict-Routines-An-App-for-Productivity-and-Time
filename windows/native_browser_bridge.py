"""Native Windows browser metadata/control adapter, with one bounded UIA worker."""
import os
import queue
import sys
import threading
import time

from core.restriction_targets import target_for_url


class NativeBrowserBridge:
    def __init__(self, library_path=None):
        root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.library_path = library_path or os.path.join(root, "StrictRecordsBrowserBridge.dll")
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=1)
        self._thread = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._busy = False
        self._busy_since = 0.0
        self._last_alive = 0.0
        self._capabilities = {}
        self.error = "Native browser integration has not connected yet."

    def connect(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._stopped.clear()
                self._thread = threading.Thread(target=self._worker, daemon=True, name="NativeBrowserUIA")
                self._thread.start()
        self._ready.wait(1.5)
        return self.status()

    def _worker(self):
        try:
            if os.name != "nt":
                raise RuntimeError("Native browser integration is supported on Windows only.")
            import clr
            clr.AddReference(self.library_path)
            from StrictRecords.NativeBrowser import BrowserBridge
            self.error = ""
            self._last_alive = time.monotonic()
            self._ready.set()
            while not self._stopped.is_set():
                try:
                    operation, args, done, result = self._queue.get(timeout=.2)
                except queue.Empty:
                    continue
                try:
                    if operation == "inspect":
                        try:
                            if hasattr(BrowserBridge, "InspectJson"):
                                import json
                                raw = str(BrowserBridge.InspectJson(int(args[0])))
                                data = json.loads(raw)
                                result.update(
                                    supported=bool(data.get("Supported")),
                                    pid=int(data.get("ProcessId", 0)),
                                    origin=str(data.get("Url", "")),
                                    tab_id=str(data.get("TabId", "")),
                                    tab_count=int(data.get("TabCount", 0)),
                                    can_close=bool(data.get("CanClose")),
                                    reason=str(data.get("Reason", "")),
                                )
                            else:
                                value = BrowserBridge.Inspect(int(args[0]))
                                result.update(supported=bool(value.Supported), pid=int(value.ProcessId),
                                              origin=str(value.Url), tab_id=str(value.TabId),
                                              tab_count=int(value.TabCount), can_close=bool(value.CanClose), reason=str(value.Reason))
                        except BaseException as inspect_err:
                            result.update(supported=False, reason=type(inspect_err).__name__)
                    elif operation == "close":
                        result["success"] = bool(BrowserBridge.CloseVerifiedTab(*args))
                    self._last_alive = time.monotonic()
                except BaseException as exc:
                    result.update(supported=False, success=False, reason=type(exc).__name__)
                finally:
                    with self._lock:
                        self._busy = False
                    done.set()
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, RuntimeError) else "Native browser component could not load: " + type(exc).__name__
            self._ready.set()

    def _call(self, operation, args, timeout):
        if not self.connected:
            return {"supported": False, "success": False, "reason": self.error}
        with self._lock:
            if self._busy:
                return {"supported": False, "success": False, "reason": "Browser accessibility provider is busy."}
            self._busy = True
            self._busy_since = time.monotonic()
        done, result = threading.Event(), {}
        self._queue.put((operation, args, done, result))
        if not done.wait(timeout):
            # The existing worker remains responsible for completion. Never create
            # another UIA worker or repeat an unconfirmed close action.
            return {"supported": False, "success": False, "reason": "Browser accessibility provider did not respond in time."}
        return result

    @property
    def connected(self):
        return bool(not self._stopped.is_set() and self._thread and self._thread.is_alive() and self._ready.is_set() and not self.error
                    and not (self._busy and time.monotonic() - self._busy_since > 3))

    def stop(self):
        self._stopped.set()
        # Cancel work still queued; an executing UIA action retains its identity guards.
        try:
            _, _, done, result = self._queue.get_nowait()
            result.update(supported=False, success=False, reason="Native browser integration stopped.")
            done.set()
        except queue.Empty:
            pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def inspect(self, hwnd, browser):
        result = self._call("inspect", (int(hwnd),), 0.8)
        self._capabilities[browser] = {"browser": browser, "url_detection": bool(result.get("supported")),
                                       "exact_tab_close": bool(result.get("can_close")),
                                       "reason": result.get("reason", ""), "checked_at": time.time()}
        origin = result.get("origin", "")
        target = target_for_url(origin)
        if target:
            result["target"] = target
        else:
            # Non-target browser origins are discarded at the observation boundary.
            result.pop("origin", None)
        return result

    def close(self, snapshot):
        args = (int(snapshot["hwnd"]), int(snapshot["pid"]), str(snapshot["tab_id"]), str(snapshot["origin"]))
        return self._call("close", args, 2.0).get("success") is True

    def status(self):
        return {"connected": self.connected, "mechanism": "WINDOWS_UI_AUTOMATION", "extension_required": False,
                "error": self.error, "busy": self._busy, "browsers": list(self._capabilities.values())}
