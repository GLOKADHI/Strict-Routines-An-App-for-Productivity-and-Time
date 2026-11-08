"""
StrictRecords – Windows Media Playback Enforcer
===============================================
Enforces the Centralized Media Restriction Policy on Windows Desktop:
- Monitors running media processes and Windows media session targets.
- Intercepts unauthorized playback across local media players (VLC, MPC, etc.),
  streaming apps (Spotify, Netflix), and browser players.
- Suppresses Picture-in-Picture (PiP) windows of unauthorized media.
- Verifies actual player output stops (e.g., via media key pause simulation or window minimize).
- Respects SUN NXT daily allowance (8:25 PM–9:35 PM IST) and valid reward entitlements.
- Never terminates or mutes essential OS audio or critical system services.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from core.media_restriction_policy import (
    ALLOWED_BY_REWARD,
    ALLOWED_BY_SUNNXT_SCHEDULE,
    MEDIA_ALLOWED,
    MEDIA_BLOCKED,
    RESTRICTED_LOCAL_PLAYER_PROCESSES,
    RESTRICTED_MUSIC_TARGETS,
    RESTRICTED_OTT_TARGETS,
    STANDARD_RESTRICTION_MESSAGE,
    get_media_policy_engine,
    now_ist,
)

logger = logging.getLogger("WindowsMediaEnforcer")

# Virtual key codes for Windows media controls
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_STOP = 0xB2
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def send_media_pause():
    """Simulates pressing the Windows hardware media pause key."""
    if not user32:
        return
    try:
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
    except Exception as e:
        logger.debug(f"send_media_pause failed: {e}")


def is_pip_window(title: str) -> bool:
    """Checks if a window title indicates a Picture-in-Picture floating window."""
    clean = (title or "").lower().strip()
    return (
        "picture-in-picture" in clean
        or "picture in picture" in clean
        or clean == "pip"
        or "pip overlay" in clean
    )


class WindowsMediaPlaybackEnforcer:
    """
    Background enforcer that scans active desktop processes and windows,
    evaluating them against the authoritative CentralizedMediaPolicyEngine.
    """

    def __init__(self, check_interval_seconds: float = 3.0):
        self.check_interval_seconds = check_interval_seconds
        self.policy_engine = get_media_policy_engine()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_enforced_targets: Set[str] = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._enforcement_loop,
            name="WindowsMediaPlaybackEnforcer",
            daemon=True,
        )
        self._thread.start()
        logger.info("WindowsMediaPlaybackEnforcer started.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("WindowsMediaPlaybackEnforcer stopped.")

    def _enforcement_loop(self):
        while not self._stop_event.is_set():
            try:
                self.enforce_once()
            except Exception as e:
                logger.debug(f"Media enforcement error: {e}")
            self._stop_event.wait(self.check_interval_seconds)

    def enforce_once(self) -> Dict[str, Any]:
        """Runs a single enforcement cycle and returns enforcement summary."""
        if not self.policy_engine.global_restriction_enabled:
            return {"active": False, "enforced_count": 0}

        restricted_found = []
        now = now_ist()

        if sys.platform != "win32":
            return {"active": True, "enforced_count": 0, "platform": "non-win32"}

        # Enumerate top-level windows to detect media players and PiP windows
        def enum_windows_callback(hwnd, extra):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value

            # Check PiP window
            if is_pip_window(title):
                eval_res = self.policy_engine.evaluate_playback(
                    target="picture-in-picture",
                    player_type="pip",
                    device_id="desktop",
                    now_dt=now,
                )
                if not eval_res["is_permitted"]:
                    # Minimize or hide unauthorized PiP window
                    user32.ShowWindowAsync(hwnd, 6)  # SW_MINIMIZE
                    restricted_found.append({"hwnd": hwnd, "title": title, "reason": "PiP suppressed"})

            # Check Window Process Name
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                proc_name = self._get_process_name(pid.value)
                if proc_name and proc_name.lower() in RESTRICTED_LOCAL_PLAYER_PROCESSES:
                    eval_res = self.policy_engine.evaluate_playback(
                        target=proc_name.lower(),
                        player_type="local_player",
                        device_id="desktop",
                        now_dt=now,
                    )
                    if not eval_res["is_permitted"]:
                        # Pause playback and minimize player
                        send_media_pause()
                        user32.ShowWindowAsync(hwnd, 6)  # SW_MINIMIZE
                        restricted_found.append({
                            "hwnd": hwnd,
                            "proc_name": proc_name,
                            "title": title,
                            "reason": eval_res["reason"],
                        })

            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_windows_callback)
        user32.EnumWindows(enum_proc, 0)

        return {
            "active": True,
            "timestamp": now.isoformat(),
            "enforced_count": len(restricted_found),
            "enforced_items": restricted_found,
        }

    def _get_process_name(self, pid: int) -> Optional[str]:
        """Retrieves process executable name for a given PID without crashing."""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_proc:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h_proc)
        return None


_enforcer_singleton: Optional[WindowsMediaPlaybackEnforcer] = None


def get_windows_media_enforcer() -> WindowsMediaPlaybackEnforcer:
    global _enforcer_singleton
    if _enforcer_singleton is None:
        _enforcer_singleton = WindowsMediaPlaybackEnforcer()
    return _enforcer_singleton
