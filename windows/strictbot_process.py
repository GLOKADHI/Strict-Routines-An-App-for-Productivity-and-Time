"""Exact process lifecycle support for the existing Windows restriction manager."""
import logging
import os
import threading
import time

log = logging.getLogger("StrictBotProcess")
PROTECTED = {"system", "registry", "idle", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
             "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "explorer.exe", "taskmgr.exe",
             "sihost.exe", "fontdrvhost.exe", "spoolsv.exe", "runtimebroker.exe", "securityhealthservice.exe",
             "msmpeng.exe", "mssense.exe", "avastsvc.exe", "avp.exe", "nortonsecurity.exe",
             "systemsettings.exe", "utilman.exe", "narrator.exe", "magnify.exe", "osk.exe",
             "nvda.exe", "jfw.exe", "voiceaccess.exe", "1password.exe", "bitwarden.exe", "keepass.exe",
             "keepassxc.exe", "dashlane.exe", "strictrecords.exe", "strictrecordsapp.exe",
             "strictroutines.exe", "strictrecordsdesktop.exe", "examproctor.exe", "python.exe", "pythonw.exe"}


def protected(name, exclusions=()):
    name = str(name or "").lower()
    return (name in PROTECTED or name in exclusions or not name.endswith(".exe")
            or any(word in name for word in ("bank", "payment", "password", "antivirus", "emergency")))


def close_exact_tree(pid, name, hwnd=0, force=False, grace=.8, exclusions=(), expected_started=None):
    """Compatibility entry point; both automatic and explicit closing use one identity verifier."""
    if protected(name,exclusions) or pid in (0,os.getpid()):return False
    try:
        from windows.strictbot_targets import capture_application,close_application
        identity=capture_application(pid,name,expected_started,exclusions)
        return close_application(identity,force,grace,exclusions)
    except Exception:
        log.exception("Exact process close unconfirmed pid=%s name=%s",pid,name)
        return False


class LaunchNotifier:
    """WinEvent and optional WMI notifications wake the one existing policy watcher."""
    def __init__(self, wake):
        self.wake=wake;self.stop_event=threading.Event();self.thread=None;self.thread_id=None

    def start(self):
        if os.name != "nt" or self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._events,daemon=True,name="StrictBotWindowEvents")
        self.thread.start()

    def _events(self):
        import ctypes as c
        from ctypes import wintypes as w
        user=c.windll.user32
        self.thread_id=c.windll.kernel32.GetCurrentThreadId()
        callback_type=c.WINFUNCTYPE(None,w.HANDLE,w.DWORD,w.HWND,w.LONG,w.LONG,w.DWORD,w.DWORD)
        last=[0.]
        def event(hook,event,hwnd,obj,child,tid,stamp):
            now=time.monotonic()
            if hwnd and obj==0 and child==0 and now-last[0]>.08:
                last[0]=now;self.wake.set()
        self.callback=callback_type(event)
        user.SetWinEventHook.restype=w.HANDLE
        user.SetWinEventHook.argtypes=[w.DWORD,w.DWORD,w.HMODULE,callback_type,w.DWORD,w.DWORD,w.DWORD]
        hooks=[user.SetWinEventHook(code,code,None,self.callback,0,0,2) for code in (3,0x8002,0x800c)]
        try:
            msg=w.MSG()
            while not self.stop_event.is_set() and user.GetMessageW(c.byref(msg),None,0,0)>0:
                user.TranslateMessage(c.byref(msg));user.DispatchMessageW(c.byref(msg))
        finally:
            for hook in hooks:
                if hook: user.UnhookWinEvent(hook)

    def stop(self):
        self.stop_event.set();self.wake.set()
        if self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x12,0,0)
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(1)
