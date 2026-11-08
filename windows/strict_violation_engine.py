"""
STRICTRECORDS – STRICT SESSION VIOLATION ENGINE
===============================================
Individual violation tracking, deduplication, auditing, and enforcement for:
1. UNAUTHORIZED_TAB_SWITCH
2. TASK_MANAGER_OPENED
3. COMMAND_PROMPT_OPENED

Requirements Guarantee:
- Every distinct prohibited action generates exactly ONE violation (+1).
- 3 distinct actions produce 3 violations (+3 total), not 1 multiplied by 3.
- Switching between ChatGPT and LeetCode panes is allowed (0 violations).
- Reopening Task Manager or CMD after closing counts as a new distinct violation.
- Multiple detection mechanisms for the same action (e.g. shortcut + process launch) deduplicate to 1 violation.
- Full persistent ledger in SQLite with complete audit trail.
- Persists across Windows EXE restart.
- Dispatches existing StrictGuard warning notifications and updates accumulated violation count.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("StrictViolationEngine")

# ---------------------------------------------------------------------------
# VIOLATION CATEGORIES
# ---------------------------------------------------------------------------
CATEGORY_TAB_SWITCH = "UNAUTHORIZED_TAB_SWITCH"
CATEGORY_TASK_MANAGER = "TASK_MANAGER_OPENED"
CATEGORY_COMMAND_PROMPT = "COMMAND_PROMPT_OPENED"

ALL_VIOLATION_CATEGORIES: Set[str] = {
    CATEGORY_TAB_SWITCH,
    CATEGORY_TASK_MANAGER,
    CATEGORY_COMMAND_PROMPT,
}

# ---------------------------------------------------------------------------
# DETECTION SOURCES
# ---------------------------------------------------------------------------
SOURCE_KEYBOARD_HOOK = "KEYBOARD_HOOK"
SOURCE_FOREGROUND_MONITOR = "FOREGROUND_MONITOR"
SOURCE_PROCESS_SUPERVISOR = "PROCESS_SUPERVISOR"
SOURCE_BROWSER_NAVIGATION = "BROWSER_NAVIGATION"
SOURCE_BROWSER_POPUP = "BROWSER_POPUP"

# ---------------------------------------------------------------------------
# TARGET PROCESS NAMES
# ---------------------------------------------------------------------------
TASK_MANAGER_PROCESSES: Set[str] = {"taskmgr.exe"}

TERMINAL_PROCESSES: Set[str] = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "windowsterminal.exe",
    "wt.exe",
    "bash.exe",
    "wsl.exe",
    "powershell_ise.exe",
    "openconsole.exe",
    "mintty.exe",
}

EXTERNAL_BROWSERS: Set[str] = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
    "arc.exe",
    "chromium.exe",
    "yandex.exe",
    "tor.exe",
    "waterfox.exe",
    "floorp.exe",
    "librewolf.exe",
    "sidekick.exe",
    "epic.exe",
    "maxthon.exe",
    "ucbrowser.exe",
}

# Allowed hosts for study browser
ALLOWED_STUDY_HOSTS: Set[str] = {
    "chatgpt.com",
    "openai.com",
    "oaistatic.com",
    "oaiusercontent.com",
    "auth0.com",
    "challenges.cloudflare.com",
    "leetcode.com",
}


def is_allowed_study_url(url: str) -> bool:
    """Checks whether the given URL belongs to ChatGPT, LeetCode, or allowed auth domains."""
    if not url:
        return False
    u = str(url).strip().lower()
    if u in ("about:blank", "about:srcdoc"):
        return True
    try:
        from urllib.parse import urlsplit
        host = urlsplit(u).hostname or ""
        host = host.lower()
        return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_STUDY_HOSTS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# VIOLATION RECORD DATACLASS
# ---------------------------------------------------------------------------
class StrictViolationRecord:
    def __init__(
        self,
        violation_id: str,
        session_id: str,
        category: str,
        timestamp: str,
        detection_source: str,
        process_or_tab_id: str = "",
        count_before: int = 0,
        count_after: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.violation_id = violation_id
        self.session_id = session_id
        self.category = category
        self.timestamp = timestamp
        self.detection_source = detection_source
        self.process_or_tab_id = process_or_tab_id
        self.count_before = count_before
        self.count_after = count_after
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "violation_id": self.violation_id,
            "session_id": self.session_id,
            "category": self.category,
            "timestamp": self.timestamp,
            "detection_source": self.detection_source,
            "process_or_tab_id": self.process_or_tab_id,
            "count_before": self.count_before,
            "count_after": self.count_after,
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------
# STRICT VIOLATION ENGINE
# ---------------------------------------------------------------------------
class StrictViolationEngine:
    """
    Authoritative Strict Session violation engine.
    Ensures:
    1. Every distinct prohibited action produces exactly ONE violation.
    2. Multiple simultaneous detection sources deduplicate to ONE event.
    3. Reopening a terminated application produces another distinct violation.
    4. Full persistent ledger in SQLite.
    5. Feeds accumulated count to StrictGuard monitoring.
    """

    MAX_PERMITTED_VIOLATIONS = 15

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._lock = threading.RLock()

        # Session state: session_id -> state dict
        # {
        #    "total_violations": int,
        #    "category_counts": {cat: int},
        #    "ledger": [StrictViolationRecord],
        #    "active_taskmgr_pids": set(int),
        #    "active_terminal_pids": set(int),
        #    "known_taskmgr_pids": set(int),
        #    "known_terminal_pids": set(int),
        #    "last_shortcut_time": {cat: float},
        #    "last_tab_switch_time": float,
        #    "last_unauthorized_url": str,
        #    "external_browser_latched": bool,
        #    "session_failed": bool,
        #    "failure_reason": str,
        # }
        self._sessions: Dict[str, Dict[str, Any]] = {}

        # Callbacks
        self._on_violation_callbacks: List[Callable[[StrictViolationRecord], None]] = []
        self._on_failure_callbacks: List[Callable[[str, StrictViolationRecord], None]] = []

    def set_db(self, db: Any) -> None:
        with self._lock:
            self.db = db

    def add_violation_callback(self, cb: Callable[[StrictViolationRecord], None]) -> None:
        with self._lock:
            if cb not in self._on_violation_callbacks:
                self._on_violation_callbacks.append(cb)

    def add_failure_callback(self, cb: Callable[[str, StrictViolationRecord], None]) -> None:
        with self._lock:
            if cb not in self._on_failure_callbacks:
                self._on_failure_callbacks.append(cb)

    # -----------------------------------------------------------------------
    # SESSION MANAGEMENT & HISTORY RECOVERY
    # -----------------------------------------------------------------------
    def _get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        """Ensures session state exists in memory, loading from DB if needed."""
        clean_sid = str(session_id or "").strip()
        if not clean_sid:
            clean_sid = "default_session"

        if clean_sid not in self._sessions:
            s_data: Dict[str, Any] = {
                "session_id": clean_sid,
                "total_violations": 0,
                "category_counts": {cat: 0 for cat in ALL_VIOLATION_CATEGORIES},
                "ledger": [],
                "active_taskmgr_pids": set(),
                "active_terminal_pids": set(),
                "known_taskmgr_pids": set(),
                "known_terminal_pids": set(),
                "last_shortcut_time": {},
                "last_tab_switch_time": 0.0,
                "last_unauthorized_url": "",
                "external_browser_latched": False,
                "session_failed": False,
                "failure_reason": "",
            }

            # Attempt to load persistent violation ledger from DB
            if self.db and hasattr(self.db, "get_strict_violations_for_session"):
                try:
                    records = self.db.get_strict_violations_for_session(clean_sid)
                    for r in records:
                        rec = StrictViolationRecord(
                            violation_id=r["violation_id"],
                            session_id=r["session_id"],
                            category=r["category"],
                            timestamp=r["timestamp"],
                            detection_source=r["detection_source"],
                            process_or_tab_id=r.get("process_or_tab_id", ""),
                            count_before=r.get("count_before", 0),
                            count_after=r.get("count_after", 0),
                            details=r.get("details", {}),
                        )
                        s_data["ledger"].append(rec)
                        cat = rec.category
                        if cat in s_data["category_counts"]:
                            s_data["category_counts"][cat] += 1
                        else:
                            s_data["category_counts"][cat] = 1
                    s_data["total_violations"] = len(s_data["ledger"])
                    if s_data["total_violations"] > self.MAX_PERMITTED_VIOLATIONS:
                        s_data["session_failed"] = True
                        s_data["failure_reason"] = f"Maximum violations exceeded ({s_data['total_violations']}/{self.MAX_PERMITTED_VIOLATIONS})"
                    logger.info("Restored %d violation records from DB for session %s", len(s_data["ledger"]), clean_sid)
                except Exception as e:
                    logger.warning("Could not restore violation ledger for %s: %s", clean_sid, e)

            self._sessions[clean_sid] = s_data

        return self._sessions[clean_sid]

    def reset_session(self, session_id: str) -> None:
        """Resets in-memory state for a new session."""
        with self._lock:
            clean_sid = str(session_id or "").strip()
            if clean_sid in self._sessions:
                del self._sessions[clean_sid]

    def get_total_violations(self, session_id: str) -> int:
        with self._lock:
            s = self._get_or_create_session(session_id)
            return int(s.get("total_violations", 0))

    def get_category_counts(self, session_id: str) -> Dict[str, int]:
        with self._lock:
            s = self._get_or_create_session(session_id)
            return dict(s.get("category_counts", {}))

    def get_ledger(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            s = self._get_or_create_session(session_id)
            return [r.to_dict() for r in s.get("ledger", [])]

    def is_session_failed(self, session_id: str) -> Tuple[bool, str]:
        with self._lock:
            s = self._get_or_create_session(session_id)
            return bool(s.get("session_failed", False)), str(s.get("failure_reason", ""))

    # -----------------------------------------------------------------------
    # CORE RECORD VIOLATION (ATOMIC + LEDGER PERSISTENCE)
    # -----------------------------------------------------------------------
    def _record_violation_internal(
        self,
        session_id: str,
        category: str,
        detection_source: str,
        process_or_tab_id: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Creates an audit record, increments total violation count by exactly ONE (+1),
        persists to SQLite, dispatches to StrictGuard and callbacks.
        """
        s = self._get_or_create_session(session_id)

        count_before = s["total_violations"]
        count_after = count_before + 1

        v_id = f"viol_{category.lower()[:8]}_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        rec = StrictViolationRecord(
            violation_id=v_id,
            session_id=session_id,
            category=category,
            timestamp=now_iso,
            detection_source=detection_source,
            process_or_tab_id=process_or_tab_id,
            count_before=count_before,
            count_after=count_after,
            details=details or {},
        )

        s["total_violations"] = count_after
        s["category_counts"][category] = s["category_counts"].get(category, 0) + 1
        s["ledger"].append(rec)

        # Check failure threshold (16th violation fails session)
        if count_after > self.MAX_PERMITTED_VIOLATIONS and not s["session_failed"]:
            s["session_failed"] = True
            s["failure_reason"] = f"StrictGuard: Maximum permitted violations exceeded ({count_after} / {self.MAX_PERMITTED_VIOLATIONS})"

        # 1. Persist to SQLite
        if self.db and hasattr(self.db, "record_strict_violation"):
            try:
                self.db.record_strict_violation(rec.to_dict())
            except Exception as e:
                logger.warning("Failed to persist violation to database: %s", e)

        # 2. Update StrictGuard Proctor singleton
        try:
            from windows.strict_guard_proctor import get_strict_guard_proctor
            proctor = get_strict_guard_proctor()
            if proctor:
                with proctor._lock:
                    proctor.violations_count = count_after
                    proctor.last_violation_details = {
                        "count": count_after,
                        "max": self.MAX_PERMITTED_VIOLATIONS,
                        "category": category,
                        "reason": self.get_warning_text(category, count_after),
                        "timestamp": time.monotonic(),
                        "formatted_time": datetime.now().strftime("%I:%M:%S %p"),
                        "advice": f"Return focus to the study screen. {max(0, self.MAX_PERMITTED_VIOLATIONS - count_after)} warnings remaining.",
                    }
                    proctor.status_message = f"Warning: {category} ({count_after}/{self.MAX_PERMITTED_VIOLATIONS})"
                    if count_after > self.MAX_PERMITTED_VIOLATIONS:
                        proctor.session_failed = True
                        proctor.failure_reason = s["failure_reason"]
                        proctor.status_message = f"SESSION FAILED: {s['failure_reason']}"
                        if proctor.on_failure:
                            try:
                                proctor.on_failure(proctor.failure_reason)
                            except Exception:
                                pass
                    elif proctor.on_violation:
                        try:
                            proctor.on_violation(count_after, self.get_warning_text(category, count_after))
                        except Exception:
                            pass
        except Exception as e:
            logger.debug("StrictGuardProctor update note: %s", e)

        # 3. Toast / Notification
        warning_msg = self.get_warning_text(category, count_after)
        try:
            from windows.modern_toast import show_modern_toast
            show_modern_toast(
                title=f"StrictGuard Alert ({count_after}/{self.MAX_PERMITTED_VIOLATIONS})",
                message=warning_msg,
                duration_ms=4500,
            )
        except Exception:
            pass

        # 4. Notify registered engine callbacks
        for cb in self._on_violation_callbacks:
            try:
                cb(rec)
            except Exception as e:
                logger.error("Error in on_violation callback: %s", e)

        if s["session_failed"]:
            for fcb in self._on_failure_callbacks:
                try:
                    import inspect
                    sig = inspect.signature(fcb)
                    if len(sig.parameters) == 1:
                        fcb(s["failure_reason"])
                    else:
                        fcb(s["failure_reason"], rec)
                except Exception:
                    try:
                        fcb(s["failure_reason"])
                    except Exception as e:
                        logger.error("Error in on_failure callback: %s", e)

        logger.warning(
            "Recorded violation: [%s] count=%d (category=%d) src=%s id=%s",
            category,
            count_after,
            s["category_counts"][category],
            detection_source,
            process_or_tab_id,
        )
        return rec

    def get_warning_text(self, category: str, count: int) -> str:
        """Constructs appropriate warning notification text for the category and count."""
        remaining = max(0, self.MAX_PERMITTED_VIOLATIONS - count)
        if category == CATEGORY_TAB_SWITCH:
            base = "Unauthorized tab switch or navigation detected. Keep focus on ChatGPT or LeetCode."
        elif category == CATEGORY_TASK_MANAGER:
            base = "Windows Task Manager access is prohibited during Strict Session."
        elif category == CATEGORY_COMMAND_PROMPT:
            base = "Command Prompt or Terminal access is prohibited during Strict Session."
        else:
            base = "Strict Session prohibited action detected."

        if remaining == 0:
            return f"FINAL WARNING: {base} The next violation will terminate your session."
        elif remaining < 0:
            return f"SESSION FAILED: {base} Exceeded maximum permitted violations."
        return f"{base} ({count}/{self.MAX_PERMITTED_VIOLATIONS}) – {remaining} warnings remaining."

    # -----------------------------------------------------------------------
    # 1. BROWSER TAB SWITCHING DETECTION
    # -----------------------------------------------------------------------
    def record_browser_tab_switch(
        self,
        session_id: str,
        target_url_or_tab: str = "",
        detection_source: str = SOURCE_BROWSER_NAVIGATION,
        details: Optional[Dict[str, Any]] = None,
        target_url: Optional[str] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Detects navigation or tab switch away from approved study environment.
        ChatGPT and LeetCode tabs and switching between them are explicitly ALLOWED (0 violations).
        """
        target = target_url if target_url is not None else target_url_or_tab
        with self._lock:
            # 1. Whitelist check: ChatGPT and LeetCode are ALLOWED
            if is_allowed_study_url(target):
                # Permitted navigation or pane switch - 0 violations!
                return None

            s = self._get_or_create_session(session_id)
            now_mono = time.monotonic()

            # 2. Debounce rapid redirects to avoid duplicate counts on HTTP -> HTTPS or 302 jumps
            if (now_mono - s["last_tab_switch_time"] < 2.0) and (
                target == s["last_unauthorized_url"]
                or target.startswith(s["last_unauthorized_url"][:25])
            ):
                logger.debug("Debounced rapid URL redirect switch: %s", target)
                return None

            s["last_tab_switch_time"] = now_mono
            s["last_unauthorized_url"] = target

            det = details or {}
            det["target_url"] = target

            return self._record_violation_internal(
                session_id=session_id,
                category=CATEGORY_TAB_SWITCH,
                detection_source=detection_source,
                process_or_tab_id=str(target)[:256],
                details=det,
            )

    def record_external_browser_switch(
        self,
        session_id: str,
        pid: int,
        proc_name: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Detects user switching the active window to an external browser (Chrome, Edge, Firefox, etc.).
        Latched while user stays on the external browser; resets when returning to study window.
        """
        with self._lock:
            s = self._get_or_create_session(session_id)
            if s["external_browser_latched"]:
                # Already latched for this departure; do not repeat-count on every supervisor tick
                return None

            s["external_browser_latched"] = True
            det = details or {}
            det["process_name"] = proc_name
            det["pid"] = pid

            return self._record_violation_internal(
                session_id=session_id,
                category=CATEGORY_TAB_SWITCH,
                detection_source=SOURCE_FOREGROUND_MONITOR,
                process_or_tab_id=f"{proc_name}:{pid}",
                details=det,
            )

    def on_study_foreground_restored(self, session_id: str) -> None:
        """Called when focus returns to the study window, un-latching external browser switch."""
        with self._lock:
            s = self._get_or_create_session(session_id)
            s["external_browser_latched"] = False

    # -----------------------------------------------------------------------
    # 2. TASK MANAGER DETECTION & DEDUPLICATION
    # -----------------------------------------------------------------------
    def record_task_manager_shortcut(
        self,
        session_id: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Called when Ctrl+Shift+Esc is intercepted by the low-level keyboard hook.
        Debounced by 3.0 seconds against key-repeat.
        """
        with self._lock:
            s = self._get_or_create_session(session_id)
            now_mono = time.monotonic()
            last_t = s["last_shortcut_time"].get(CATEGORY_TASK_MANAGER, 0.0)

            if now_mono - last_t < 3.0:
                # Key-repeat debounce
                return None

            s["last_shortcut_time"][CATEGORY_TASK_MANAGER] = now_mono
            det = details or {}
            det["key_combination"] = "Ctrl+Shift+Esc"

            return self._record_violation_internal(
                session_id=session_id,
                category=CATEGORY_TASK_MANAGER,
                detection_source=SOURCE_KEYBOARD_HOOK,
                process_or_tab_id="shortcut_ctrl_shift_esc",
                details=det,
            )

    def record_task_manager_process(
        self,
        session_id: str,
        pid: int,
        detection_source: str = SOURCE_PROCESS_SUPERVISOR,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Called when taskmgr.exe is detected running or foregrounded.
        Deduplicates:
        1. If the exact PID was already recorded in this session -> DO NOT count again.
        2. If Ctrl+Shift+Esc was pressed within the last 3.0s -> correlate to that event, do NOT add another violation.
        3. Reopening Task Manager after closing (new PID) -> counts as a new distinct violation.
        """
        with self._lock:
            s = self._get_or_create_session(session_id)

            # 1. Exact PID deduplication
            if pid in s["known_taskmgr_pids"]:
                s["active_taskmgr_pids"].add(pid)
                return None

            s["active_taskmgr_pids"].add(pid)
            s["known_taskmgr_pids"].add(pid)

            # 2. Correlate with recent keyboard shortcut (within 3.0s)
            now_mono = time.monotonic()
            last_sc = s["last_shortcut_time"].get(CATEGORY_TASK_MANAGER, 0.0)
            if now_mono - last_sc < 3.0:
                # Correlated to the shortcut event — associate PID without adding another count
                logger.info("Correlated Task Manager PID %d with recent shortcut event; deduplicated (+0)", pid)
                return None

            det = details or {}
            det["pid"] = pid
            det["process_name"] = "taskmgr.exe"

            return self._record_violation_internal(
                session_id=session_id,
                category=CATEGORY_TASK_MANAGER,
                detection_source=detection_source,
                process_or_tab_id=str(pid),
                details=det,
            )

    # -----------------------------------------------------------------------
    # 3. COMMAND PROMPT & TERMINAL DETECTION & DEDUPLICATION
    # -----------------------------------------------------------------------
    def record_command_prompt_process(
        self,
        session_id: str,
        pid: int,
        proc_name: str,
        detection_source: str = SOURCE_PROCESS_SUPERVISOR,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrictViolationRecord]:
        """
        Called when an interactive CMD/Terminal is opened or foregrounded.
        Deduplicates:
        1. If exact PID was already recorded in this session -> DO NOT count again.
        2. Reopening CMD/Terminal after closing (new PID) -> counts as a new distinct violation.
        """
        with self._lock:
            s = self._get_or_create_session(session_id)

            # Exact PID deduplication
            if pid in s["known_terminal_pids"]:
                s["active_terminal_pids"].add(pid)
                return None

            s["active_terminal_pids"].add(pid)
            s["known_terminal_pids"].add(pid)

            det = details or {}
            det["pid"] = pid
            det["process_name"] = proc_name

            return self._record_violation_internal(
                session_id=session_id,
                category=CATEGORY_COMMAND_PROMPT,
                detection_source=detection_source,
                process_or_tab_id=f"{proc_name}:{pid}",
                details=det,
            )

    # -----------------------------------------------------------------------
    # PROCESS EXIT RECONCILIATION
    # -----------------------------------------------------------------------
    def reconcile_active_processes(
        self,
        session_id: str,
        is_process_alive_fn: Optional[Callable[[int], bool]] = None,
    ) -> None:
        """
        Checks which tracked Task Manager / Terminal PIDs have exited.
        When a process exits, removes it from active_pids so that if the user
        launches it again later (new PID), it generates a new violation.
        """
        with self._lock:
            s = self._get_or_create_session(session_id)

            def _alive(p: int) -> bool:
                if is_process_alive_fn:
                    return is_process_alive_fn(p)
                if os.name != "nt":
                    return False
                try:
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, p)
                    if not h:
                        return False
                    exit_code = ctypes.c_ulong()
                    kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                    kernel32.CloseHandle(h)
                    STILL_ACTIVE = 259
                    return exit_code.value == STILL_ACTIVE
                except Exception:
                    return False

            # Reconcile Task Manager PIDs
            for pid in list(s["active_taskmgr_pids"]):
                if not _alive(pid):
                    s["active_taskmgr_pids"].discard(pid)
                    logger.debug("Task Manager PID %d exited; eligible for new distinct violation if reopened", pid)

            # Reconcile Terminal PIDs
            for pid in list(s["active_terminal_pids"]):
                if not _alive(pid):
                    s["active_terminal_pids"].discard(pid)
                    logger.debug("Terminal PID %d exited; eligible for new distinct violation if reopened", pid)


# Global singleton instance
_engine_instance: Optional[StrictViolationEngine] = None
_engine_lock = threading.Lock()


def get_strict_violation_engine(db: Optional[Any] = None) -> StrictViolationEngine:
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            _engine_instance = StrictViolationEngine(db=db)
        elif db is not None and _engine_instance.db is None:
            _engine_instance.set_db(db)
        return _engine_instance
