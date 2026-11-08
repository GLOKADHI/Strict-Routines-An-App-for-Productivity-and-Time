"""
StrictRecords — NeoExamBrowser Process Monitor, Notification Suppression & Early Exit Controller.
==================================================================================================

Authoritative controller for:
1. Dynamic discovery and Authenticode signature verification of installed NeoExamBrowser.
2. Silent process-presence monitoring during approved exam bookings.
   STRICT REQUIREMENT: Process-presence monitoring ONLY.
   - NO keylogging.
   - NO capturing exam answers.
   - NO inspecting exam content.
   - NO taking screenshots of the exam browser.
   - NEVER terminate, interfere with, or inject into NeoExamBrowser.
3. StrictRoutines notification suppression:
   - While exam is active, pauses routine reminders, hourly proof popups, toasts, sounds,
     countdown dialogs, productivity prompts and intrusive overlays.
   - Queues suppressed notifications for post-exam review.
   - Never deletes pending proof windows.
4. Early exam completion controller:
   - "End Exam Early" confirmation handling.
   - Truncates approved exemption window to actual_end.
   - Windows overlapping before actual_end retain full eligible points.
   - Windows starting after actual_end (e.g. 11:50 AM) are reactivated as SCHEDULED without prompt.
5. Crash / Unexpected exit resilience:
   - If NeoExamBrowser unexpectedly exits/crashes, transitions to EXAM_PROCESS_NOT_DETECTED.
   - Maintains approved booking and continues notification suppression.
   - Does NOT prematurely reactivate proof windows without user confirmation.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("NeoExamMonitor")

DEFAULT_TIMEZONE = "Asia/Kolkata"

# State Machine Constants
STATE_IDLE = "IDLE"
STATE_EXAM_BOOKING_PENDING = "EXAM_BOOKING_PENDING"
STATE_EXAM_ACTIVE_SILENT_MONITORING = "EXAM_ACTIVE_SILENT_MONITORING"
STATE_EXAM_PROCESS_NOT_DETECTED = "EXAM_PROCESS_NOT_DETECTED"
STATE_EXAM_ENDED_EARLY = "EXAM_ENDED_EARLY"
STATE_EXAM_FINISHED = "EXAM_FINISHED"

KNOWN_PROCESS_NAMES = [
    "Neo Browser.exe",
    "NeoBrowser.exe",
    "NeoExamBrowser.exe",
    "Neo Exam Browser.exe",
]

KNOWN_INSTALL_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Neo Browser\Neo Browser.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Neo Browser\Neo Browser.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Neo Browser\Neo Browser.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\NeoExamBrowser\NeoExamBrowser.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\NeoExamBrowser\NeoExamBrowser.exe"),
]


class NeoExamBrowserMonitor:
    """Authoritative monitor and controller for NeoExamBrowser exam sessions."""

    _instance: Optional["NeoExamBrowserMonitor"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, db=None) -> "NeoExamBrowserMonitor":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db=db)
            elif db is not None and cls._instance.db is None:
                cls._instance.db = db
            return cls._instance

    def __init__(self, db=None):
        self.db = db
        self.current_state: str = STATE_IDLE
        self.active_booking: Optional[Dict[str, Any]] = None
        self.detected_process_info: Optional[Dict[str, Any]] = None
        self.discovered_browser_info: Optional[Dict[str, Any]] = None
        self._suppressed_notifications: List[Dict[str, Any]] = []
        self._suppressed_lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False

        # Run initial discovery
        self.discover_installed_browser()

    # ── Discovery & Executable Verification ───────────────────────

    def discover_installed_browser(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Dynamically locates the installed NeoExamBrowser executable.
        Checks:
        1. Configurable environment variables (NEO_EXAM_BROWSER_PATH).
        2. Known default installation paths.
        3. Windows Registry Uninstall keys.
        """
        if self.discovered_browser_info and not force_refresh:
            return self.discovered_browser_info

        # 1. Environment variable override
        env_path = os.environ.get("NEO_EXAM_BROWSER_PATH")
        if env_path and os.path.isfile(env_path):
            info = {
                "installed": True,
                "exe_path": env_path,
                "process_name": os.path.basename(env_path),
                "discovery_method": "ENVIRONMENT_VARIABLE",
                "display_name": "Neo Browser",
                "publisher": "iamneo.ai",
            }
            info.update(self._verify_signature(env_path))
            self.discovered_browser_info = info
            return info

        # 2. Known install paths
        for path in KNOWN_INSTALL_PATHS:
            if os.path.isfile(path):
                info = {
                    "installed": True,
                    "exe_path": path,
                    "process_name": os.path.basename(path),
                    "discovery_method": "KNOWN_PATH",
                    "display_name": "Neo Browser",
                    "publisher": "iamneo.ai",
                }
                info.update(self._verify_signature(path))
                self.discovered_browser_info = info
                return info

        # 3. Windows Registry Uninstall Keys
        if sys.platform == "win32":
            try:
                import winreg

                roots = [
                    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                ]
                for hkey, subkey in roots:
                    try:
                        with winreg.OpenKey(hkey, subkey) as root_key:
                            num_subkeys = winreg.QueryInfoKey(root_key)[0]
                            for i in range(num_subkeys):
                                try:
                                    sk_name = winreg.EnumKey(root_key, i)
                                    with winreg.OpenKey(root_key, sk_name) as app_key:
                                        values = {}
                                        num_values = winreg.QueryInfoKey(app_key)[1]
                                        for j in range(num_values):
                                            v_name, v_data, _ = winreg.EnumValue(app_key, j)
                                            values[v_name] = v_data

                                        disp_name = str(values.get("DisplayName", "")).strip()
                                        publisher = str(values.get("Publisher", "")).strip()
                                        install_loc = str(values.get("InstallLocation", "")).strip()
                                        display_icon = str(values.get("DisplayIcon", "")).strip()

                                        if any(k in disp_name.lower() for k in ("neo browser", "neo exam", "neoexam")) or "iamneo" in publisher.lower():
                                            exe_candidates = []
                                            if display_icon and os.path.isfile(display_icon.split(",")[0].strip('"')):
                                                exe_candidates.append(display_icon.split(",")[0].strip('"'))
                                            if install_loc:
                                                for p_name in KNOWN_PROCESS_NAMES:
                                                    c_path = os.path.join(install_loc, p_name)
                                                    if os.path.isfile(c_path):
                                                        exe_candidates.append(c_path)

                                            for cand in exe_candidates:
                                                if os.path.isfile(cand):
                                                    info = {
                                                        "installed": True,
                                                        "exe_path": cand,
                                                        "process_name": os.path.basename(cand),
                                                        "discovery_method": "REGISTRY",
                                                        "display_name": disp_name or "Neo Browser",
                                                        "publisher": publisher or "iamneo.ai",
                                                    }
                                                    info.update(self._verify_signature(cand))
                                                    self.discovered_browser_info = info
                                                    return info
                                except Exception:
                                    pass
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Registry search encountered error: {e}")

        # Default fallback if not yet installed
        default_info = {
            "installed": False,
            "exe_path": KNOWN_INSTALL_PATHS[0],
            "process_name": "Neo Browser.exe",
            "discovery_method": "FALLBACK",
            "display_name": "Neo Browser",
            "publisher": "iamneo.ai",
            "signature_valid": False,
            "signer_subject": None,
            "thumbprint": None,
        }
        self.discovered_browser_info = default_info
        return default_info

    def _verify_signature(self, exe_path: str) -> Dict[str, Any]:
        """
        Validates Authenticode signature using non-intrusive PowerShell query.
        Returns signature status, subject, and thumbprint.
        """
        if sys.platform != "win32" or not os.path.isfile(exe_path):
            return {"signature_valid": False, "signer_subject": None, "thumbprint": None}

        try:
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-Command",
                f'$sig = Get-AuthenticodeSignature -FilePath "{exe_path}"; '
                f'[PSCustomObject]@{{ '
                f'  Status = [string]$sig.Status; '
                f'  Subject = [string]$sig.SignerCertificate.Subject; '
                f'  Thumbprint = [string]$sig.SignerCertificate.Thumbprint '
                f'}} | ConvertTo-Json'
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                status = str(data.get("Status", "")).strip()
                subject = str(data.get("Subject", "")).strip()
                thumbprint = str(data.get("Thumbprint", "")).strip()
                is_valid = status == "Valid" or "iamneo" in subject.lower()
                return {
                    "signature_valid": is_valid,
                    "signature_status": status,
                    "signer_subject": subject or None,
                    "thumbprint": thumbprint or None,
                }
        except Exception as e:
            logger.debug(f"Signature verification error: {e}")

        return {"signature_valid": False, "signer_subject": None, "thumbprint": None}

    # ── Silent Process Presence Monitoring ────────────────────────

    def check_process_presence(self) -> Optional[Dict[str, Any]]:
        """
        Checks whether NeoExamBrowser is currently running.
        STRICT GUARANTEE: Presence check ONLY.
        NO content capture, NO keylogging, NO screenshots, NO process injection, NO termination.
        """
        try:
            import psutil
        except ImportError:
            return None

        configured_name = os.environ.get("NEO_EXAM_BROWSER_PROCESS_NAME")
        target_names = set([p.lower() for p in KNOWN_PROCESS_NAMES])
        if configured_name:
            target_names.add(configured_name.lower())
        if self.discovered_browser_info and self.discovered_browser_info.get("process_name"):
            target_names.add(self.discovered_browser_info["process_name"].lower())

        target_exe_path = ""
        if self.discovered_browser_info and self.discovered_browser_info.get("exe_path"):
            target_exe_path = os.path.normcase(self.discovered_browser_info["exe_path"])

        for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = os.path.normcase(proc.info.get("exe") or "")

                if name in target_names or (target_exe_path and exe == target_exe_path):
                    return {
                        "running": True,
                        "pid": proc.info["pid"],
                        "name": proc.info.get("name"),
                        "exe": proc.info.get("exe"),
                        "create_time": proc.info.get("create_time"),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return None

    # ── State Machine & Notification Suppression ──────────────────

    def update_state(self, reference_now: Optional[datetime] = None) -> str:
        """
        Re-evaluates the active examination booking and transitions the state machine.
        States:
        - EXAM_BOOKING_PENDING: Exam scheduled/approved for today, but either not yet reached start time or process not started yet.
        - EXAM_ACTIVE_SILENT_MONITORING: Time is within exam period and NeoExamBrowser is running.
        - EXAM_PROCESS_NOT_DETECTED: Time is within exam period, but browser process is not detected.
          (Maintains suppression and approved booking, does not assume completion without user confirmation).
        - EXAM_ENDED_EARLY: Early exit confirmed by user.
        - EXAM_FINISHED: Normal exam scheduled interval elapsed.
        """
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now_dt = reference_now or datetime.now(tz)
        today_str = now_dt.strftime("%Y-%m-%d")
        now_ms = int(now_dt.timestamp() * 1000)

        if not self.db:
            return self.current_state

        active_exams = self.db.list_exam_prebookings(date=today_str, include_past=True)
        # Find active or approved exams
        valid_exam = None
        for ex in active_exams:
            if ex.get("status") in ("EXAM_APPROVED", "EXAM_SCHEDULED", "EXAM_ACTIVE", "EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"):
                valid_exam = ex
                break

        if not valid_exam:
            self.active_booking = None
            self.detected_process_info = None
            self.current_state = STATE_IDLE
            return STATE_IDLE

        self.active_booking = valid_exam
        booking_id = valid_exam["booking_id"]
        start_ms = int(valid_exam.get("allocation_start_ms") or 0)
        end_ms = int(valid_exam.get("actual_end_ms") or valid_exam.get("allocation_end_ms") or 0)
        is_early_exit = (valid_exam.get("early_exit_confirmed") == 1) or (valid_exam.get("status") in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"))

        if is_early_exit:
            self.current_state = STATE_EXAM_ENDED_EARLY
            self.db.update_exam_monitoring_state(booking_id, STATE_EXAM_ENDED_EARLY, neo_process_detected=0)
            return STATE_EXAM_ENDED_EARLY

        if now_ms < start_ms:
            self.current_state = STATE_EXAM_BOOKING_PENDING
            self.db.update_exam_monitoring_state(booking_id, STATE_EXAM_BOOKING_PENDING, neo_process_detected=0)
            return STATE_EXAM_BOOKING_PENDING

        if now_ms >= end_ms:
            self.current_state = STATE_EXAM_FINISHED
            self.db.update_exam_monitoring_state(booking_id, STATE_EXAM_FINISHED, neo_process_detected=0)
            return STATE_EXAM_FINISHED

        # Current time is within the exam window [start_ms, end_ms]!
        proc = self.check_process_presence()
        self.detected_process_info = proc

        if proc is not None:
            # Browser process detected and running!
            self.current_state = STATE_ACTIVE_STATE = STATE_EXAM_ACTIVE_SILENT_MONITORING
            self.db.update_exam_monitoring_state(
                booking_id,
                STATE_EXAM_ACTIVE_SILENT_MONITORING,
                neo_process_detected=1,
                neo_process_pid=proc.get("pid", 0),
                neo_exe_path=proc.get("exe") or "",
            )
            return STATE_EXAM_ACTIVE_SILENT_MONITORING
        else:
            # Process not detected!
            # CRITICAL: Do NOT assume exam is finished! Maintain suppression and approved booking!
            self.current_state = STATE_EXAM_PROCESS_NOT_DETECTED
            self.db.update_exam_monitoring_state(
                booking_id,
                STATE_EXAM_PROCESS_NOT_DETECTED,
                neo_process_detected=0,
                neo_process_pid=0,
            )
            return STATE_EXAM_PROCESS_NOT_DETECTED

    def is_notification_suppression_active(self, reference_now: Optional[datetime] = None) -> bool:
        """
        Determines whether notifications, toasts, sounds, popups, and overlays must be suppressed.
        Returns True whenever an approved exam is active (within its approved time window and not ended early).
        Suppression remains ACTIVE even if the browser process crashes or exits unexpectedly (STATE_EXAM_PROCESS_NOT_DETECTED).
        """
        state = self.update_state(reference_now=reference_now)
        return state in (STATE_EXAM_ACTIVE_SILENT_MONITORING, STATE_EXAM_PROCESS_NOT_DETECTED)

    def queue_suppressed_notification(self, notification: Dict[str, Any]) -> None:
        """Stores a suppressed notification for post-exam review."""
        with self._suppressed_lock:
            stashed = dict(notification)
            stashed["suppressed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stashed["exam_booking_id"] = self.active_booking.get("booking_id") if self.active_booking else ""
            self._suppressed_notifications.append(stashed)
            if len(self._suppressed_notifications) > 200:
                self._suppressed_notifications = self._suppressed_notifications[-200:]

    def get_suppressed_notifications(self) -> List[Dict[str, Any]]:
        """Returns all queued notifications suppressed during the active exam session."""
        with self._suppressed_lock:
            return list(self._suppressed_notifications)

    def clear_suppressed_notifications(self) -> None:
        """Clears the suppressed notification queue."""
        with self._suppressed_lock:
            self._suppressed_notifications.clear()

    # ── Early Exit Actions ────────────────────────────────────────

    def confirm_early_exit(
        self,
        booking_id: Optional[str] = None,
        actual_end_time: Optional[str] = None,
        actual_end_iso: Optional[str] = None,
        actual_end_ms: Optional[int] = None,
        device_id: str = "windows_pc",
    ) -> Dict[str, Any]:
        """
        Confirms early exam exit with authoritative state truncation and proof window reactivation.
        """
        bid = booking_id or (self.active_booking.get("booking_id") if self.active_booking else None)
        if not bid and self.db:
            # Fallback to today's active exam
            today = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d")
            active = self.db.list_exam_prebookings(date=today)
            for ex in active:
                if ex.get("status") in ("EXAM_APPROVED", "EXAM_SCHEDULED", "EXAM_ACTIVE"):
                    bid = ex["booking_id"]
                    break

        if not bid:
            raise ValueError("No active exam booking found to end early.")

        if not self.db:
            raise ValueError("Database handle not configured on NeoExamBrowserMonitor.")

        updated = self.db.confirm_early_exam_exit(
            booking_id=bid,
            actual_end_time=actual_end_time,
            actual_end_iso=actual_end_iso,
            actual_end_ms=actual_end_ms,
            device_id=device_id,
        )

        self.current_state = STATE_EXAM_ENDED_EARLY
        self.active_booking = updated
        logger.info(f"Early exit confirmed for exam {bid} at {updated.get('actual_end_time')}")
        return updated

    def get_monitoring_status(self) -> Dict[str, Any]:
        """Returns comprehensive monitoring status for API consumers and frontend dashboards."""
        self.update_state()
        return {
            "state": self.current_state,
            "suppression_active": self.is_notification_suppression_active(),
            "active_booking": self.active_booking,
            "browser_info": self.discovered_browser_info or self.discover_installed_browser(),
            "detected_process": self.detected_process_info,
            "suppressed_notification_count": len(self._suppressed_notifications),
        }
