"""
STRICTRECORDS — Modern Floating Desktop Toast Notification System
==================================================================
Replaces legacy Windows balloon tooltips with a sleek, modern,
dark-theme glassmorphism toast popup.

Features:
- Borderless floating window with subtle glowing accent border
- Category-aware branding (TASKS, PROOF, WARNING, SYNC, AI)
- Never steals focus (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
- Positioned in bottom-right corner above Windows taskbar
- Micro-animations: smooth fade-in and fade-out
- Progress countdown line along the bottom edge
- Hover-to-pause dismiss timer
- Click on toast activates and restores the main StrictRecords dashboard
- Dedicated close button (✕)
- Thread-safe background execution queue
"""

import os
import sys
import threading
import time
from typing import Callable, Optional

logger = None
def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("ModernToast")
    return logger

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ModernToastManager:
    _instance: Optional["ModernToastManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ModernToastManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._queue = []
        self._queue_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_active = False

    def show(
        self,
        title: str,
        message: str,
        category: str = "SYSTEM",
        warning: bool = False,
        duration_sec: float = 4.2,
        on_click: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Queues and displays a modern styled toast notification."""
        if sys.platform != "win32":
            return False

        try:
            from windows.neo_exam_monitor import NeoExamBrowserMonitor
            if NeoExamBrowserMonitor.get_instance().is_notification_suppression_active():
                return False
        except Exception:
            pass

        with self._queue_lock:
            # Avoid flooding duplicate notifications
            if self._queue:
                last = self._queue[-1]
                if last.get("title") == title and last.get("message") == message:
                    return True
            self._queue.append({
                "title": title or "StrictRoutines",
                "message": message or "",
                "category": category or ("WARNING" if warning else "SYSTEM"),
                "warning": warning,
                "duration_sec": max(2.5, min(10.0, duration_sec)),
                "on_click": on_click,
            })

            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run_ui_loop,
                    daemon=True,
                    name="ModernToastThread",
                )
                self._thread.start()

        return True

    def _run_ui_loop(self):
        """Dedicated STA thread hosting the WinForms toast window."""
        try:
            import clr
            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
            import System.Windows.Forms as WF
            import System.Drawing as SD
            import System.Drawing.Drawing2D as SD2D
        except Exception as e:
            _get_logger().debug(f"WinForms not available for modern toast: {e}")
            return

        while not self._stop_event.is_set():
            item = None
            with self._queue_lock:
                if self._queue:
                    item = self._queue.pop(0)

            if not item:
                with self._queue_lock:
                    if self._queue:
                        continue
                    self._thread = None
                    return

            try:
                self._display_single_toast(WF, SD, SD2D, item)
            except Exception as ex:
                _get_logger().debug(f"Error displaying modern toast: {ex}")

    def stop(self):
        self._stop_event.set()
        with self._queue_lock:
            self._queue.clear()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _display_single_toast(self, WF, SD, SD2D, item):
        from windows.notification_toast_view import display_toast
        display_toast(WF, SD, SD2D, item, self._stop_event)


def show_modern_toast(
    title: str,
    message: str,
    category: str = "SYSTEM",
    warning: bool = False,
    duration_sec: float = 4.2,
    on_click: Optional[Callable[[], None]] = None,
) -> bool:
    """Convenience helper to show a modern desktop toast."""
    return ModernToastManager.get_instance().show(
        title=title,
        message=message,
        category=category,
        warning=warning,
        duration_sec=duration_sec,
        on_click=on_click,
    )
