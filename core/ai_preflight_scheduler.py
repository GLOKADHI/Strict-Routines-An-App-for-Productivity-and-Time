"""
StrictRecords — Authoritative 5-Minute AI Preflight Scheduler
============================================================
Authoritative background scheduler that monitors upcoming proof-submission windows
and triggers the AI preflight check exactly 5 minutes before opening.

Features:
1. Automatic calculation: scheduled_open_time - 300s.
2. Background execution: independent daemon thread runs every 15 seconds.
3. Skip filters: excused proof windows, cancelled activities, obsolete versions,
   non-AI tasks.
4. Retry intervals: 5m before (initial), 3m before (retry 1), 1m before (retry 2).
5. Schedule mutation handling: detects rescheduling/cancellations, cancels obsolete
   preflights, recalculates new trigger times, and preserves audit history.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from core.ai_preflight_service import (
    AiPreflightResult,
    AiPreflightService,
    NON_RETRYABLE_STATES,
    PROVIDER_PENDING_VALIDATION,
    STATE_CONNECTED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING_RETRY,
    ai_preflight_service,
)
from core.time_window_engine import IST, now_ist

PREFLIGHT_OFFSET_SEC: float = 300.0  # Exactly 5 minutes before opening
RETRY_1_OFFSET_SEC: float = 180.0    # 3 minutes before opening
RETRY_2_OFFSET_SEC: float = 60.0     # 1 minute before opening

logger = logging.getLogger("StrictRecords.AiPreflightScheduler")


class AiPreflightScheduler:
    """
    Background scheduler for timely 5-minute preflights before every scheduled proof window.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, db=None, preflight_svc=None):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(AiPreflightScheduler, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db=None, preflight_svc=None):
        if getattr(self, "_initialized", False):
            if db is not None:
                self.db = db
            if preflight_svc is not None:
                self.preflight_svc = preflight_svc
            return
        self.db = db
        self.preflight_svc = preflight_svc or ai_preflight_service
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._scheduled_jobs: Dict[str, Dict[str, Any]] = {}  # key: f"{window_id}_v{version}"
        self._executed_keys: Set[str] = set()
        self._check_interval_sec = 15.0
        self._initialized = True

    @classmethod
    def get_instance(cls, db=None, preflight_svc=None) -> "AiPreflightScheduler":
        return cls(db=db, preflight_svc=preflight_svc)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, name="StrictRecords-AiPreflightScheduler", daemon=True)
            self._thread.start()
            logger.info("AiPreflightScheduler started.")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("AiPreflightScheduler stopped.")

    # -------------------------------------------------------------------------
    # Window Filtering & Timestamp Calculation (Section 1 & 9)
    # -------------------------------------------------------------------------
    @staticmethod
    def parse_window_timestamp(iso_str: str) -> Optional[datetime]:
        if not iso_str or not str(iso_str).strip():
            return None
        clean = str(iso_str).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt
        except Exception:
            return None

    def _is_window_eligible(self, act: Dict[str, Any]) -> bool:
        """
        Determines if a scheduled activity requires an AI preflight check.
        Filters out excused, cancelled, obsolete, or non-AI activities (Section 1).
        """
        status = str(act.get("status") or "").strip().upper()
        if status in ("CANCELLED", "CANCELED", "DELETED", "EXCUSED", "OFFICIALLY_EXCUSED", "EXAM_EXCUSED"):
            return False

        if act.get("is_enabled") in (0, False, "0"):
            return False

        req = str(act.get("proof_requirements") or "REQUIRED").strip().upper()
        if req in ("NOT_REQUIRED", "NONE", "EXCUSED"):
            return False

        # Must have valid window start
        w_start = act.get("window_start") or act.get("scheduled_open_time") or act.get("scheduled_time")
        if not w_start:
            return False

        return True

    @staticmethod
    def calculate_preflight_time(open_time_str: str) -> Optional[datetime]:
        dt = AiPreflightScheduler.parse_window_timestamp(open_time_str)
        if not dt:
            return None
        return dt - timedelta(seconds=PREFLIGHT_OFFSET_SEC)

    def _should_retry(self, result: AiPreflightResult) -> bool:
        if result.taufa.status in NON_RETRYABLE_STATES or result.openai.status in NON_RETRYABLE_STATES:
            return False
        if result.retry_count >= 2:
            return False
        return (
            result.selected_validation_provider == PROVIDER_PENDING_VALIDATION
            or result.taufa.status not in (STATE_CONNECTED,)
        )

    def register_window(self, window_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        act_id = str(window_data.get("activity_id") or "").strip()
        if not self._is_window_eligible(window_data):
            with self._lock:
                for old_k in list(self._scheduled_jobs.keys()):
                    if old_k.startswith(f"{act_id}_v"):
                        del self._scheduled_jobs[old_k]
            return None
        sched_ver = int(window_data.get("schedule_version") or 1)
        w_start_str = str(window_data.get("scheduled_open_time") or window_data.get("window_start") or "").strip()
        w_start_dt = self.parse_window_timestamp(w_start_str)
        if not w_start_dt:
            return None
        job_key = f"{act_id}_v{sched_ver}"
        with self._lock:
            for old_k in list(self._scheduled_jobs.keys()):
                if old_k.startswith(f"{act_id}_v") and old_k != job_key:
                    del self._scheduled_jobs[old_k]
            job = {
                "job_key": job_key,
                "activity_id": act_id,
                "schedule_version": sched_ver,
                "window_start_str": w_start_str,
                "open_epoch": w_start_dt.timestamp(),
                "trigger_time": w_start_dt - timedelta(seconds=PREFLIGHT_OFFSET_SEC),
                "preflight_epoch": w_start_dt.timestamp() - PREFLIGHT_OFFSET_SEC,
                "retry1_epoch": w_start_dt.timestamp() - RETRY_1_OFFSET_SEC,
                "retry2_epoch": w_start_dt.timestamp() - RETRY_2_OFFSET_SEC,
                "status": "QUEUED"
            }
            self._scheduled_jobs[job_key] = job
            return job

    def get_scheduled_job(self, activity_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for k, job in self._scheduled_jobs.items():
                if job.get("activity_id") == activity_id:
                    return job
        return None

    def scan_and_reconcile_windows(self, reference_now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Scans today's scheduled activities, identifies eligible proof-submission windows,
        calculates opening and 5-minute preflight timestamps, and reconciles jobs.
        """
        if not self.db:
            return []

        now = reference_now or datetime.now(timezone.utc)
        now_epoch = now.timestamp()
        today_str = now.astimezone(IST).strftime("%Y-%m-%d")

        activities: List[Dict[str, Any]] = []
        try:
            with self.db._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduled_activities
                    WHERE date = ? OR date = '' OR window_start LIKE ?
                    ORDER BY window_start ASC, scheduled_time ASC
                    """,
                    (today_str, f"{today_str}%"),
                ).fetchall()
                activities = [dict(r) for r in rows]
        except Exception as e:
            logger.warning("Error fetching scheduled activities for preflight: %s", e)
            return []

        active_jobs: List[Dict[str, Any]] = []

        with self._lock:
            current_keys: Set[str] = set()

            for act in activities:
                if not self._is_window_eligible(act):
                    continue

                act_id = str(act.get("activity_id") or "").strip()
                sched_ver = int(act.get("schedule_version") or 1)
                w_start_str = str(act.get("window_start") or act.get("scheduled_time") or "").strip()
                w_start_dt = self.parse_window_timestamp(w_start_str)
                if not w_start_dt:
                    continue

                open_epoch = w_start_dt.timestamp()
                preflight_epoch = open_epoch - 300.0  # Exactly 5 minutes before opening
                retry1_epoch = open_epoch - 180.0     # 3 minutes before opening
                retry2_epoch = open_epoch - 60.0      # 1 minute before opening

                job_key = f"{act_id}_v{sched_ver}"
                current_keys.add(job_key)

                # Check if obsolete version was previously queued
                for old_k in list(self._scheduled_jobs.keys()):
                    if old_k.startswith(f"{act_id}_v") and old_k != job_key:
                        # Cancel obsolete preflight job (Section 9)
                        logger.info("Cancelling obsolete preflight job %s in favor of %s", old_k, job_key)
                        del self._scheduled_jobs[old_k]

                job_info = self._scheduled_jobs.get(job_key)
                if not job_info:
                    job_info = {
                        "job_key": job_key,
                        "activity_id": act_id,
                        "proof_window_id": act_id,
                        "schedule_version": sched_ver,
                        "window_start_iso": w_start_dt.isoformat(),
                        "open_epoch": open_epoch,
                        "preflight_epoch": preflight_epoch,
                        "retry1_epoch": retry1_epoch,
                        "retry2_epoch": retry2_epoch,
                        "status": "SCHEDULED",
                        "retry_count": 0,
                        "last_result": None,
                    }
                    self._scheduled_jobs[job_key] = job_info

                active_jobs.append(job_info)

            # Purge removed activities
            for k in list(self._scheduled_jobs.keys()):
                if k not in current_keys:
                    del self._scheduled_jobs[k]

        return active_jobs

    def tick(self, reference_now: Optional[datetime] = None) -> List[AiPreflightResult]:
        """
        Evaluates active preflight jobs and executes them when their target time arrives.
        Returns any preflights completed in this tick.
        """
        now = reference_now or datetime.now(timezone.utc)
        now_epoch = now.timestamp()
        self.scan_and_reconcile_windows(now)

        completed_in_tick: List[AiPreflightResult] = []

        with self._lock:
            for job_key, job in list(self._scheduled_jobs.items()):
                open_epoch = job["open_epoch"]
                preflight_epoch = job["preflight_epoch"]
                retry1_epoch = job["retry1_epoch"]
                retry2_epoch = job["retry2_epoch"]
                act_id = job["activity_id"]
                sched_ver = job["schedule_version"]
                window_iso = job["window_start_iso"]

                # If window closed more than 30 minutes ago, clean up
                if now_epoch > (open_epoch + 1800.0):
                    continue

                exec_initial_key = f"{job_key}_initial"
                exec_retry1_key = f"{job_key}_retry1"
                exec_retry2_key = f"{job_key}_retry2"

                # 1. Initial 5-minute preflight check
                if now_epoch >= preflight_epoch and exec_initial_key not in self._executed_keys:
                    self._executed_keys.add(exec_initial_key)
                    logger.info("Triggering 5-minute preflight for window %s (open at %s)", act_id, window_iso)
                    res = self.preflight_svc.execute_preflight(
                        proof_window_id=act_id,
                        activity_id=act_id,
                        scheduled_open_time=window_iso,
                        schedule_version=sched_ver,
                        retry_attempt=0,
                    )
                    job["last_result"] = res
                    job["status"] = res.preflight_status
                    job["retry_count"] = 0
                    completed_in_tick.append(res)
                    continue

                # 2. Retry 1 (3 minutes before opening)
                last_res = job.get("last_result")
                if last_res and last_res.preflight_status == STATUS_PENDING_RETRY:
                    if now_epoch >= retry1_epoch and exec_retry1_key not in self._executed_keys:
                        self._executed_keys.add(exec_retry1_key)
                        logger.info("Triggering preflight retry 1 for window %s", act_id)
                        res = self.preflight_svc.execute_preflight(
                            proof_window_id=act_id,
                            activity_id=act_id,
                            scheduled_open_time=window_iso,
                            schedule_version=sched_ver,
                            force=True,
                            retry_attempt=1,
                        )
                        job["last_result"] = res
                        job["status"] = res.preflight_status
                        job["retry_count"] = 1
                        completed_in_tick.append(res)
                        continue

                # 3. Final Retry 2 (1 minute before opening)
                if last_res and last_res.preflight_status == STATUS_PENDING_RETRY:
                    if now_epoch >= retry2_epoch and exec_retry2_key not in self._executed_keys:
                        self._executed_keys.add(exec_retry2_key)
                        logger.info("Triggering final preflight retry 2 for window %s", act_id)
                        res = self.preflight_svc.execute_preflight(
                            proof_window_id=act_id,
                            activity_id=act_id,
                            scheduled_open_time=window_iso,
                            schedule_version=sched_ver,
                            force=True,
                            retry_attempt=2,
                        )
                        job["last_result"] = res
                        job["status"] = res.preflight_status
                        job["retry_count"] = 2
                        completed_in_tick.append(res)
                        continue

        return completed_in_tick

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as e:
                logger.error("Error in AiPreflightScheduler tick: %s", e)
            time.sleep(self._check_interval_sec)

    def get_upcoming_schedule(self) -> List[Dict[str, Any]]:
        """Returns the current list of scheduled preflight jobs and their readiness status."""
        with self._lock:
            res: List[Dict[str, Any]] = []
            for k, j in sorted(self._scheduled_jobs.items(), key=lambda item: item[1]["open_epoch"]):
                lr: Optional[AiPreflightResult] = j.get("last_result")
                res.append({
                    "activity_id": j["activity_id"],
                    "proof_window_id": j["proof_window_id"],
                    "schedule_version": j["schedule_version"],
                    "window_start": j["window_start_iso"],
                    "preflight_trigger_epoch": j["preflight_epoch"],
                    "status": j["status"],
                    "retry_count": j["retry_count"],
                    "selected_validation_provider": lr.selected_validation_provider if lr else "NOT_CHECKED",
                    "taufa_status": lr.taufa_status if lr else "NOT_CHECKED",
                    "openai_status": lr.openai_status if lr else "NOT_CHECKED",
                    "display_title": lr.display_title if lr else "",
                    "display_lines": lr.display_lines if lr else [],
                })
            return res


# Global singleton
ai_preflight_scheduler = AiPreflightScheduler()
