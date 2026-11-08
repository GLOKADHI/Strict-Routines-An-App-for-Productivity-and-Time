"""
StrictRecords Authoritative Time-Window Scoring Engine
======================================================
Strict timing engine with:
  SCHEDULE -> ACTIVITY WINDOW -> SUBMISSION -> AI VALIDATION -> POINTS -> NEXT WINDOW

Enforces:
  1. Window boundaries: window_start <= submission_time < window_end
  2. Automatic MISSED state once window_end expires (independent, non-blocking)
  3. Single notification per event id (deduplicated)
  4. Precise UI countdowns for upcoming and open windows
  5. India Standard Time (Asia/Kolkata, UTC+05:30) authoritative server time
  6. Next-Day Routine Scheduler with Wake-Up restriction (06:00 AM - 10:00 AM),
     Bath, Gym, Custom Activities, overlap validation, locking, and Admin Rescheduling.
"""

import json
import hashlib
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


IST = timezone(timedelta(hours=5, minutes=30))


def resolve_schedule_timezone(name="Asia/Kolkata"):
    """Resolve a configured zone; stable IST works in older Windows bundles."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in ("Asia/Kolkata", "Asia/Calcutta"):
            return IST
        if name in ("UTC", "Etc/UTC"):
            return timezone.utc
        raise ValueError(f"Timezone data is unavailable for {name}. Install the bundled timezone database.")


def now_ist() -> datetime:
    """Authoritative current timestamp in IST (Asia/Kolkata)."""
    return datetime.now(IST)


def iso_to_dt(iso_str: str) -> datetime:
    """Parses ISO timestamp string to datetime in IST."""
    try:
        if not isinstance(iso_str, str) or not iso_str.strip():
            raise ValueError("timestamp is empty")
        clean = iso_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt
    except (TypeError, ValueError) as exc:
        # Never turn malformed persisted timestamps into "now". Doing so makes
        # corrupted windows appear open or expired based on the current clock.
        raise ValueError(f"Invalid ISO timestamp: {iso_str!r}") from exc


def dt_to_iso(dt: datetime) -> str:
    """Converts datetime to ISO string with timezone offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.isoformat()


class TimeWindowEngine:
    """Authoritative Time-Window Scheduler & Validator."""

    # Allowed wake-up target interval: 06:00 AM (360 mins) to 10:00 AM (600 mins) inclusive
    WAKEUP_MIN_MINUTES = 6 * 60        # 06:00 -> 360
    WAKEUP_MAX_MINUTES = 10 * 60       # 10:00 -> 600
    WAKEUP_ERROR_MSG = "Wake-up time must be between 6:00 AM and 10:00 AM."

    # Authoritative task catalog & default policy definitions
    TASK_CATALOG: Dict[str, Dict[str, Any]] = {
        "WAKE_UP": {
            "title": "Wake-Up Protocol",
            "points": 15,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "CAMERA_BLUETOOTH_AUDIO",
            "validation_rule": "CAMERA_BLUETOOTH_AUDIO",
            "required_by_admin": True,
        },
        "BATH": {
            "title": "Scheduled Bath",
            "points": 0,
            "penalty_points": 0,
            "window_duration_minutes": 30,
            "proof_requirements": "NONE",
            "validation_rule": "EXEMPT_ACTIVITY",
            "required_by_admin": False,
        },
        "GYM": {
            "title": "Gym Workout Session",
            "points": 15,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "PHOTO_AND_DURATION_30M",
            "validation_rule": "GYM_SESSION_VERIFICATION",
            "required_by_admin": False,
        },
        "STUDY": {
            "title": "Study Session",
            "points": 10,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "PHOTO_AND_SUMMARY",
            "validation_rule": "CUSTOM_ACTIVITY_PROOF",
            "required_by_admin": False,
        },
        "PROGRAMMING": {
            "title": "Programming Practice",
            "points": 12,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "CODE_OR_SCREENSHOT_PROOF",
            "validation_rule": "CUSTOM_ACTIVITY_PROOF",
            "required_by_admin": False,
        },
        "EXERCISE": {
            "title": "Physical Exercise",
            "points": 10,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "PHOTO_PROOF",
            "validation_rule": "CUSTOM_ACTIVITY_PROOF",
            "required_by_admin": False,
        },
        "PERSONAL": {
            "title": "Personal Discipline Task",
            "points": 8,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "PHOTO_OR_NOTE_PROOF",
            "validation_rule": "CUSTOM_ACTIVITY_PROOF",
            "required_by_admin": False,
        },
        "CUSTOM": {
            "title": "Scheduled Activity",
            "points": 10,
            "penalty_points": 0,
            "window_duration_minutes": 20,
            "proof_requirements": "PHOTO_OR_NOTE_PROOF",
            "validation_rule": "CUSTOM_ACTIVITY_PROOF",
            "required_by_admin": False,
        },
    }

    def __init__(self, db_instance=None):
        self.db = db_instance

    def get_daily_routine_gate(self, date_str, account_id="user_lok", reference_now=None, is_dev_mode=False):
        from core.daily_routine_gate import DailyRoutineGate
        if not self.db:
            return {"active": False, "state": "WAKE_UP_PENDING", "activated_at": None}
        return DailyRoutineGate(self.db).reconcile(date_str, account_id, reference_now, is_dev_mode)

    @staticmethod
    def _retire_changed_hourly_plan(conn, items, date, account_id, now):
        """Cancel obsolete future slots; submitted evidence and expired history stay intact."""
        wanted = {item["activity_id"] for item in items if item["type"] == "HOURLY"}
        rows = conn.execute("SELECT activity_id,window_start,window_end,details_json FROM scheduled_activities WHERE date=? AND account_id=? AND type='HOURLY' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','BLOCKED_WAKEUP')", (date, account_id)).fetchall()
        for row in rows:
            if row["activity_id"] not in wanted and iso_to_dt(row["window_end"]) > now:
                details = json.loads(row["details_json"] or "{}")
                details["hourly_plan_replaced"] = True
                conn.execute("UPDATE scheduled_activities SET status='CANCELLED',attendance_status='INACTIVE',points=0,details_json=? WHERE activity_id=?", (json.dumps(details), row["activity_id"]))
        for item in items:
            if item["type"] == "HOURLY":
                conn.execute("""UPDATE scheduled_activities SET title=?,scheduled_time=?,window_start=?,window_end=?,window_duration_minutes=?,notification_time=?,
                    closing_warning_time=?,schedule_id=?,schedule_version=?,details_json=?
                    WHERE activity_id=? AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','BLOCKED_WAKEUP')""",
                    (item.get("title", ""), item["scheduled_time"], item["window_start"], item["window_end"],
                     item.get("window_duration_minutes", 20), item["notification_time"],
                     item.get("closing_warning_time", ""), item.get("schedule_id", ""), item.get("schedule_version", 1), item["details_json"], item["activity_id"]))
            if item["type"] == "HOURLY" and iso_to_dt(item["window_end"]) > now:
                conn.execute("UPDATE scheduled_activities SET status='SCHEDULED',details_json=? WHERE activity_id=? AND status='CANCELLED' AND details_json LIKE '%\"hourly_plan_replaced\": true%'", (item["details_json"], item["activity_id"]))

    @staticmethod
    def _materialize_legacy_hourly(conn, item, is_dev_mode=False):
        """Mirror an eligible authoritative slot into existing legacy records.

        This is an adapter, not another schedule generator. INSERT OR IGNORE
        preserves submission timestamps and historical decisions on restart.
        """
        details = json.loads(item.get("details_json") or "{}")
        hour = int(details["hour"])
        minute = int(details.get("minute", 0))
        checkpoint = f"{hour:02d}:{minute:02d}"
        slot_suffix = f"{hour:02d}" if minute == 0 else f"{hour:02d}_{minute:02d}"
        dev = int(is_dev_mode)
        date = item["date"]
        task_code = f"HOURLY_{slot_suffix}"
        if item.get("status") == "EXCUSED":
            conn.execute("UPDATE hourly_records SET status='EXCUSED' WHERE date=? AND is_dev_mode=? AND hour_slot=? AND status IN ('UPCOMING','PENDING','BLOCKED_WAKEUP')", (date, dev, details["hour_slot"]))
            conn.execute("UPDATE scheduled_tasks SET state='EXCUSED' WHERE date=? AND is_dev_mode=? AND task_code=? AND state IN ('UPCOMING','READY','PENDING','BLOCKED')", (date, dev, task_code))
            return
        conn.execute("""INSERT OR IGNORE INTO hourly_records
            (hourly_id, date, is_dev_mode, hour_slot, checkpoint_time, status)
            VALUES (?, ?, ?, ?, ?, 'UPCOMING')""",
            (f"hourly_{date}_{dev}_{slot_suffix}", date, dev, details["hour_slot"], checkpoint))
        conn.execute("""INSERT OR IGNORE INTO scheduled_tasks
            (task_id, date, is_dev_mode, task_code, title, task_type, semantics,
             target_time, deadline_time, recurrence, required_evidence, dependencies_json,
             point_value, penalty_value, state)
             VALUES (?, ?, ?, ?, ?, 'CHECK_IN_TASK', 'RECURRING_CHECKPOINT', ?, ?, 'HOURLY',
                     'ACTIVITY_LOG_AND_PHOTO', '["WAKE_UP"]', ?, ?, 'UPCOMING')""",
            (f"task_{date}_{dev}_{task_code}", date, dev, task_code, item.get("title", "Hourly check-in"),
             checkpoint, iso_to_dt(item["window_end"]).strftime("%H:%M"), item.get("points", 0), item.get("penalty_points", 0)))

    @staticmethod
    def _apply_approved_schedule_exceptions(items, sched_rec):
        """Apply persisted approved intervals only to windows future at approval.

        The revision service owns authorization and the audit entries. Client
        schedule synchronization cannot replace this confirmed schedule.
        """
        for entry in sched_rec.get("reschedule_audit_log") or []:
            if entry.get("action") != "APPROVED_SCHEDULE_CHANGE" or not entry.get("authorized_by"):
                continue
            try:
                start = iso_to_dt(entry["exception_start"])
                end = iso_to_dt(entry["exception_end"])
                applied = iso_to_dt(entry["applied_at"])
            except (ValueError, TypeError, KeyError):
                continue
            if start >= end:
                continue
            for item in items:
                details=json.loads(item.get('details_json') or '{}')
                ws, we = iso_to_dt(item["window_start"]), iso_to_dt(item["window_end"])
                if item['type']=='GYM':
                    ws=iso_to_dt(item['scheduled_time'])
                    we=iso_to_dt(details.get('reserved_end') or item['window_start'])
                if ws >= applied and ws < end and we > start:
                    details = json.loads(item.get("details_json") or "{}")
                    details.update(schedule_change_proposal_id=entry.get("proposal_id"),
                                   excused_reason=entry.get("reason"), schedule_change_authorized_by=entry["authorized_by"])
                    item.update(status="EXCUSED", points=0, attendance_status='EXCUSED', details_json=json.dumps(details))
        return items

    @staticmethod
    def get_authoritative_time() -> Dict[str, Any]:
        """Returns server authoritative time payload for clock synchronization."""
        n = now_ist()
        return {
            "server_time": n.isoformat(),
            "date": n.strftime("%Y-%m-%d"),
            "time": n.strftime("%H:%M:%S"),
            "timezone": "Asia/Kolkata",
            "utc_offset": "+05:30",
            "timestamp_ms": int(n.timestamp() * 1000),
        }

    @staticmethod
    def get_next_calendar_date(base_date_str: Optional[str] = None) -> str:
        """Returns the next calendar date (YYYY-MM-DD) in IST."""
        if base_date_str:
            try:
                base_dt = datetime.strptime(base_date_str.strip()[:10], "%Y-%m-%d")
                return (base_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                pass
        return (now_ist() + timedelta(days=1)).strftime("%Y-%m-%d")

    @staticmethod
    def parse_time_24h(time_str: str) -> Tuple[bool, str, int]:
        """
        Parses a time string in 'HH:MM' (24h) or 'HH:MM AM/PM' (12h) format.
        Never rounds or guesses invalid times.
        Returns (is_valid, normalized_hh_mm, total_minutes_from_midnight).
        """
        if not time_str or not isinstance(time_str, str):
            return False, "", -1
        raw = time_str.strip().upper()
        if not raw:
            return False, "", -1

        is_am = raw.endswith("AM")
        is_pm = raw.endswith("PM")
        if is_am or is_pm:
            raw = raw[:-2].strip()

        parts = raw.split(":")
        if len(parts) < 2 or len(parts) > 3:
            return False, "", -1
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            sec = int(parts[2]) if len(parts) == 3 else 0
        except ValueError:
            return False, "", -1

        if sec != 0 or minute < 0 or minute > 59:
            return False, "", -1

        if is_am or is_pm:
            if hour < 1 or hour > 12:
                return False, "", -1
            if is_am:
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        else:
            if hour < 0 or hour > 23:
                return False, "", -1

        total_mins = hour * 60 + minute
        return True, f"{hour:02d}:{minute:02d}", total_mins

    @staticmethod
    def format_time_12h(time_24h: str) -> str:
        """Formats 'HH:MM' into 'hh:mm AM/PM'."""
        ok, norm, _ = TimeWindowEngine.parse_time_24h(time_24h)
        if not ok:
            return time_24h
        h, m = [int(x) for x in norm.split(":")]
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12:02d}:{m:02d} {suffix}"

    @classmethod
    def validate_wakeup_time(cls, wakeup_time: str) -> Tuple[bool, str, str]:
        """
        Section 2: Enforces that next-day wake-up target is strictly between
        06:00 AM and 10:00 AM, inclusive. Never silently rounds invalid times.
        """
        ok, norm, total_mins = cls.parse_time_24h(wakeup_time)
        if not ok:
            return False, "", cls.WAKEUP_ERROR_MSG
        if total_mins < cls.WAKEUP_MIN_MINUTES or total_mins > cls.WAKEUP_MAX_MINUTES:
            return False, norm, cls.WAKEUP_ERROR_MSG
        return True, norm, "Valid wake-up target time."

    @classmethod
    def get_allowed_wakeup_slots(cls, step_minutes: int = 5) -> List[Dict[str, str]]:
        """Returns 5-minute granularity time picker slots between 06:00 AM and 10:00 AM inclusive."""
        slots = []
        for mins in range(cls.WAKEUP_MIN_MINUTES, cls.WAKEUP_MAX_MINUTES + 1, step_minutes):
            h = mins // 60
            m = mins % 60
            t24 = f"{h:02d}:{m:02d}"
            slots.append({"value": t24, "label": cls.format_time_12h(t24)})
        return slots

    @classmethod
    def calculate_activity_window(
        cls,
        date_str: str,
        target_time: str,
        duration_minutes: int = 20,
    ) -> Dict[str, Any]:
        """
        Calculates the ±10m (or duration_minutes/2) submission window and notification timestamps
        for a given target_time on date_str in IST.
        Note: A 06:00 AM target has window 05:50 AM - 06:10 AM (valid target!).
        """
        ok, norm, _ = cls.parse_time_24h(target_time)
        if not ok:
            raise ValueError(f"Invalid time format: {target_time}")
        h, m = [int(x) for x in norm.split(":")]
        half = max(1, int(duration_minutes) // 2)
        rem = max(1, int(duration_minutes) - half)

        sched_dt = datetime.strptime(f"{date_str} {h:02d}:{m:02d}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        w_start = sched_dt - timedelta(minutes=half)
        w_end = sched_dt + timedelta(minutes=rem)
        w_notif = w_start - timedelta(minutes=1)
        w_warn1m = w_end - timedelta(minutes=1)

        start_hm = w_start.strftime("%H:%M")
        end_hm = w_end.strftime("%H:%M")
        notif_hm = w_notif.strftime("%H:%M")
        warn_hm = w_warn1m.strftime("%H:%M")

        return {
            "date": date_str,
            "target_time": norm,
            "target_display": cls.format_time_12h(norm),
            "scheduled_time": dt_to_iso(sched_dt),
            "window_start": dt_to_iso(w_start),
            "window_end": dt_to_iso(w_end),
            "window_start_hm": start_hm,
            "window_end_hm": end_hm,
            "window_start_display": cls.format_time_12h(start_hm),
            "window_end_display": cls.format_time_12h(end_hm),
            "window_display": f"{cls.format_time_12h(start_hm)}–{cls.format_time_12h(end_hm)}",
            "notification_time": dt_to_iso(w_notif),
            "notification_hm": notif_hm,
            "notification_display": cls.format_time_12h(notif_hm),
            "closing_warning_time": dt_to_iso(w_warn1m),
            "closing_warning_hm": warn_hm,
            "closing_warning_display": cls.format_time_12h(warn_hm),
            "duration_minutes": int(duration_minutes),
        }

    @classmethod
    def calculate_gym_window(cls, date_str, target_time, duration_minutes=None):
        from core.product_policy import GYM
        duration = duration_minutes if duration_minutes is not None else GYM['default_workout_minutes']
        if type(duration) is not int or duration not in GYM['supported_workout_minutes']:
            raise ValueError('Select a supported Gym workout duration')
        window=cls.calculate_activity_window(date_str,target_time,GYM['proof_window_minutes'])
        start=iso_to_dt(window['scheduled_time'])
        finish=start+timedelta(minutes=duration)
        end=finish+timedelta(minutes=GYM['proof_window_minutes'])
        window.update(window_start=dt_to_iso(finish),window_end=dt_to_iso(end),notification_time=dt_to_iso(finish-timedelta(minutes=1)),
            closing_warning_time=dt_to_iso(end-timedelta(minutes=1)),duration_minutes=duration,
            expected_finish=dt_to_iso(finish),reserved_start=dt_to_iso(start),reserved_end=dt_to_iso(finish),
            window_duration_minutes=GYM['proof_window_minutes'])
        for field, instant in [('window_start',finish),('window_end',end),('notification',finish-timedelta(minutes=1)),('closing_warning',end-timedelta(minutes=1))]:
            window[field+'_hm']=instant.strftime('%H:%M')
            window[field+'_display']=cls.format_time_12h(instant.strftime('%H:%M'))
        window['window_display']=window['window_start_display']+'–'+window['window_end_display']
        return window

    @classmethod
    def calculate_wakeup_activity_window(
        cls,
        date_str: str,
        target_time: str,
        duration_minutes: int = 20,
    ) -> Dict[str, Any]:
        """Returns 07:00 <= proof time < 09:30, independent of target slot."""
        ok, norm, _ = cls.parse_time_24h(target_time)
        if not ok:
            raise ValueError(f"Invalid time format: {target_time}")
        window = cls.calculate_activity_window(date_str, norm, duration_minutes)
        start_dt = datetime.strptime(f"{date_str} 07:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        from core.window_state import WAKEUP_WINDOW_END
        close_dt = datetime.strptime(f"{date_str} {WAKEUP_WINDOW_END}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        warn_dt = close_dt - timedelta(minutes=1)
        start_hm = start_dt.strftime("%H:%M")
        close_hm = close_dt.strftime("%H:%M")
        warn_hm = warn_dt.strftime("%H:%M")
        target_hour, target_minute = (int(part) for part in norm.split(":"))
        target_dt = datetime.strptime(
            f"{date_str} {target_hour:02d}:{target_minute:02d}:00", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=IST)
        window.update({
            "date": date_str,
            "target_time": norm,
            "target_display": cls.format_time_12h(norm),
            "scheduled_time": dt_to_iso(target_dt),
            "window_start": dt_to_iso(start_dt),
            "window_end": dt_to_iso(close_dt),
            "window_start_hm": start_hm,
            "window_end_hm": close_hm,
            "window_start_display": cls.format_time_12h(start_hm),
            "window_end_display": cls.format_time_12h(close_hm),
            "window_display": f"{cls.format_time_12h(start_hm)}–{cls.format_time_12h(close_hm)}",
            "notification_time": dt_to_iso(start_dt - timedelta(minutes=1)),
            "notification_hm": (start_dt - timedelta(minutes=1)).strftime("%H:%M"),
            "notification_display": cls.format_time_12h((start_dt - timedelta(minutes=1)).strftime("%H:%M")),
            "closing_warning_time": dt_to_iso(warn_dt),
            "closing_warning_hm": warn_hm,
            "closing_warning_display": cls.format_time_12h(warn_hm),
            "duration_minutes": int((close_dt - start_dt).total_seconds() // 60),
        })
        return window

    @classmethod
    def first_hourly_checkpoint_hour(
        cls,
        date_str: str,
        wakeup_time: str,
        wakeup_window_end: str,
    ) -> int:
        """Hourly targets begin at the configured wake-up target; eligibility is separate."""
        return int(wakeup_time.split(":")[0])

    def _get_task_policy_config(self) -> Dict[str, Any]:
        """Reads authoritative points and admin task policies from database settings if available."""
        cfg = {
            "wakeup_points": 15,
            "wakeup_track_points": 25,
            "bath_points": 10,
            "gym_points": 15,
            "hourly_points": 8,
            "gym_required_by_admin": False,
            "allow_ordinary_task_hourly_overlap": False,
            "allow_gym_hourly_coexistence": True,
        }
        if self.db:
            try:
                settings = self.db.get_settings()
                cfg["wakeup_points"] = int(getattr(settings, "wakeup_on_time_points", 15))
                cfg["wakeup_track_points"] = int(getattr(settings, "wakeup_track_points", 25))
                cfg["bath_points"] = int(getattr(settings, "bath_points", 10))
                cfg["gym_points"] = int(getattr(settings, "gym_points", 15))
                cfg["hourly_points"] = int(getattr(settings, "hourly_points", 8))
            except Exception:
                pass
            try:
                with self.db._connect() as conn:
                    row = conn.execute(
                        "SELECT value FROM system_config WHERE key = 'routine_scheduler_policy'"
                    ).fetchone()
                    if row and row["value"]:
                        custom_pol = json.loads(row["value"])
                        cfg.update(custom_pol)
            except Exception:
                pass
        return cfg

    def update_scheduler_policy(self, policy_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates admin-configured scheduler policy (e.g., gym_required_by_admin, conflict rules)."""
        cfg = self._get_task_policy_config()
        cfg.update(policy_updates)
        if self.db:
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.db._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES ('routine_scheduler_policy', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (json.dumps(cfg), now_iso),
                )
        return cfg

    def set_admin_task_policy(
        self,
        gym_required_by_admin: bool,
        allow_ordinary_task_hourly_overlap: bool,
        admin_pin: str,
    ) -> Dict[str, Any]:
        if not self.db or not self.db.verify_admin_pin(admin_pin) or self.db.is_default_admin_pin():
            return {"success": False, "error": "A changed administrator key is required."}
        policy = self.update_scheduler_policy({
            "gym_required_by_admin": bool(gym_required_by_admin),
            "allow_ordinary_task_hourly_overlap": bool(allow_ordinary_task_hourly_overlap),
        })
        self.db.record_admin_audit(
            admin_user="Administrator",
            action_type="SCHEDULER_POLICY_UPDATED",
            field_changed="routine_scheduler_policy",
            old_value=None,
            new_value=json.dumps(policy, sort_keys=True),
            device_id="admin_portal",
        )
        return {"success": True, "policy": policy}

    @staticmethod
    def generate_daily_schedule(
        date_str: str,
        account_id: str = "user_lok",
        wakeup_time: str = "09:00",
        hourly_start: int = 10,
        hourly_end: int = 22,
        bath_time: Optional[str] = None,
        gym_time: Optional[str] = None,
        gym_enabled: bool = True,
        custom_activities: Optional[List[Dict[str, Any]]] = None,
        schedule_id: str = "",
        schedule_version: int = 1,
        bath_points: int = 10,
        gym_points: int = 15,
        wakeup_points: int = 15,
        hourly_points: int = 8,
        timezone_name: str = "Asia/Kolkata",
        gym_duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generates the authoritative schedule for the given date:
          - Wake-Up activity (independent 07:00–10:00 submission period)
          - Optional Bath activity (target -> ±10m window, notification 1m prior)
          - Optional Gym activity (target -> ±10m window, notification 1m prior)
          - Optional Custom activities (Study, Programming, Exercise, Personal, etc.)
          - Hourly activities anchored to wake-up, then every 60 minutes through 22:MM
        """
        activities: List[Dict[str, Any]] = []

        # 1. Wake-Up target slot and submission period are separate schedule values.
        ok_w, norm_w, _ = TimeWindowEngine.parse_time_24h(wakeup_time)
        if not ok_w:
            norm_w = "09:00"
        w_hour, w_min = [int(p) for p in norm_w.split(":")]

        # Base target scheduled time
        w_sched_dt = datetime.strptime(f"{date_str} {w_hour:02d}:{w_min:02d}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)

        wake_window = TimeWindowEngine.calculate_wakeup_activity_window(date_str, norm_w)
        w_open_dt = iso_to_dt(wake_window["window_start"])
        w_close_dt = iso_to_dt(wake_window["window_end"])

        w_notif_dt = w_open_dt - timedelta(minutes=1)
        w_warn1m_dt = w_close_dt - timedelta(minutes=1)
        total_w_duration = int((w_close_dt - w_open_dt).total_seconds() // 60)

        wake_act_id = f"ACT-{date_str.replace('-', '')}-{w_hour:02d}{w_min:02d}-WAKEUP"
        activities.append({
            "activity_id": wake_act_id,
            "account_id": account_id,
            "type": "WAKE_UP",
            "date": date_str,
            "title": f"Wake-Up Protocol ({norm_w})",
            "scheduled_time": dt_to_iso(w_sched_dt),
            "window_start": dt_to_iso(w_open_dt),
            "window_end": dt_to_iso(w_close_dt),
            "notification_time": dt_to_iso(w_notif_dt),
            "closing_warning_time": dt_to_iso(w_warn1m_dt),
            "duration_minutes": total_w_duration,
            "window_duration_minutes": total_w_duration,
            "status": "SCHEDULED",
            "submitted_at": None,
            "points": wakeup_points,
            "penalty_points": 0,
            "proof_requirements": "CAMERA_BLUETOOTH_AUDIO",
            "validation_rule": "CAMERA_BLUETOOTH_AUDIO",
            "is_enabled": 1,
            "required_by_admin": 1,
            "schedule_id": schedule_id,
            "schedule_version": schedule_version,
            "details_json": json.dumps({
                "target_time": norm_w,
                "target_display": TimeWindowEngine.format_time_12h(norm_w),
                "window_display": f"{TimeWindowEngine.format_time_12h(w_open_dt.strftime('%H:%M'))}–{TimeWindowEngine.format_time_12h(w_close_dt.strftime('%H:%M'))}",
                "early_completion_permitted": True,
            }),
        })

        # 2. Bath Activity (when configured via Next-Day Planner)
        if bath_time:
            ok_b, norm_b, _ = TimeWindowEngine.parse_time_24h(bath_time)
            if ok_b:
                b_win = TimeWindowEngine.calculate_activity_window(date_str, norm_b, 20)
                b_hour, b_min = [int(p) for p in norm_b.split(":")]
                bath_act_id = f"ACT-{date_str.replace('-', '')}-{b_hour:02d}{b_min:02d}-BATH"
                activities.append({
                    "activity_id": bath_act_id,
                    "account_id": account_id,
                    "type": "BATH",
                    "date": date_str,
                    "title": f"Morning Bath Routine ({norm_b})",
                    "scheduled_time": b_win["scheduled_time"],
                    "window_start": b_win["window_start"],
                    "window_end": b_win["window_end"],
                    "notification_time": b_win["notification_time"],
                    "closing_warning_time": b_win["closing_warning_time"],
                    "duration_minutes": 20,
                    "window_duration_minutes": 20,
                    "status": "SCHEDULED",
                    "submitted_at": None,
                    "points": bath_points,
                    "penalty_points": 0,
                    "proof_requirements": "PHOTO_OR_CONFIRMATION",
                    "validation_rule": "BATH_WINDOW_VERIFICATION",
                    "is_enabled": 1,
                    "required_by_admin": 1,
                    "schedule_id": schedule_id,
                    "schedule_version": schedule_version,
                    "details_json": json.dumps({
                        "target_time": norm_b,
                        "target_display": b_win["target_display"],
                        "window_display": b_win["window_display"],
                    }),
                })

        # 3. Gym Activity (when configured & enabled)
        if gym_time and gym_enabled:
            ok_g, norm_g, _ = TimeWindowEngine.parse_time_24h(gym_time)
            if ok_g:
                g_win = TimeWindowEngine.calculate_gym_window(date_str, norm_g, gym_duration_minutes)
                g_hour, g_min = [int(p) for p in norm_g.split(":")]
                gym_act_id = f"ACT-{date_str.replace('-', '')}-{g_hour:02d}{g_min:02d}-GYM"
                activities.append({
                    "activity_id": gym_act_id,
                    "account_id": account_id,
                    "type": "GYM",
                    "date": date_str,
                    "title": f"Gym Workout ({norm_g})",
                    "scheduled_time": g_win["scheduled_time"],
                    "window_start": g_win["window_start"],
                    "window_end": g_win["window_end"],
                    "notification_time": g_win["notification_time"],
                    "closing_warning_time": g_win["closing_warning_time"],
                    "duration_minutes": g_win['duration_minutes'],
                    "window_duration_minutes": g_win['window_duration_minutes'],
                    "status": "SCHEDULED",
                    "submitted_at": None,
                    "points": gym_points,
                    "penalty_points": 0,
                    "proof_requirements": "PHOTO_AND_DURATION_30M",
                    "validation_rule": "GYM_SESSION_VERIFICATION",
                    "is_enabled": 1,
                    "required_by_admin": 0,
                    "schedule_id": schedule_id,
                    "schedule_version": schedule_version,
                    "details_json": json.dumps({
                        "target_time": norm_g,
                        "duration_minutes": g_win['duration_minutes'],
                        "expected_finish": g_win['expected_finish'],
                        "reserved_start": g_win['reserved_start'],
                        "reserved_end": g_win['reserved_end'],
                        "target_display": g_win["target_display"],
                        "window_display": g_win["window_display"],
                    }),
                })

        # 4. Custom Configurable Activities
        if custom_activities:
            for idx, c_act in enumerate(custom_activities):
                if not c_act.get("enabled", True):
                    continue
                c_type = str(c_act.get("task_type") or c_act.get("type") or "CUSTOM").upper()
                cat_spec = TimeWindowEngine.TASK_CATALOG.get(c_type, TimeWindowEngine.TASK_CATALOG["CUSTOM"])
                c_time = str(c_act.get("scheduled_time") or c_act.get("target_time") or "")
                ok_c, norm_c, _ = TimeWindowEngine.parse_time_24h(c_time)
                if not ok_c:
                    continue
                dur = int(c_act.get("window_duration_minutes") or cat_spec["window_duration_minutes"] or 20)
                c_win = TimeWindowEngine.calculate_activity_window(date_str, norm_c, dur)
                c_hour, c_min = [int(p) for p in norm_c.split(":")]
                c_title = c_act.get("title") or f"{cat_spec['title']} ({norm_c})"
                c_id = c_act.get("activity_id") or f"ACT-{date_str.replace('-', '')}-{c_hour:02d}{c_min:02d}-{c_type}"
                c_pts = int(cat_spec["points"])
                activities.append({
                    "activity_id": c_id,
                    "account_id": account_id,
                    "type": c_type,
                    "date": date_str,
                    "title": c_title,
                    "scheduled_time": c_win["scheduled_time"],
                    "window_start": c_win["window_start"],
                    "window_end": c_win["window_end"],
                    "notification_time": c_win["notification_time"],
                    "closing_warning_time": c_win["closing_warning_time"],
                    "duration_minutes": dur,
                    "window_duration_minutes": dur,
                    "status": "SCHEDULED",
                    "submitted_at": None,
                    "points": c_pts,
                    "penalty_points": int(cat_spec.get("penalty_points", 0)),
                    "proof_requirements": cat_spec["proof_requirements"],
                    "validation_rule": cat_spec["validation_rule"],
                    "is_enabled": 1,
                    "required_by_admin": 1 if cat_spec.get("required_by_admin") else 0,
                    "schedule_id": schedule_id,
                    "schedule_version": schedule_version,
                    "details_json": json.dumps({
                        "target_time": norm_c,
                        "target_display": c_win["target_display"],
                        "window_display": c_win["window_display"],
                        "custom_index": idx,
                    }),
                })

        # 5. Independent Hourly Activities (Anchored dynamically to wake-up)
        # Preserve the configured minute; wake-up approval controls eligibility separately.
        anchored_start = w_hour
        for h in range(anchored_start, hourly_end + 1):
            is_final_11pm = (h == 23)
            h_min = 0 if is_final_11pm else w_min
            h_sched = datetime.strptime(f"{date_str} {h:02d}:{h_min:02d}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            h_start = h_sched - timedelta(minutes=10)
            h_end = h_sched if is_final_11pm else (h_sched + timedelta(minutes=10))
            h_notif = h_start - timedelta(minutes=1)  # Notification 1m prior to window open
            h_warn1m = h_end - timedelta(minutes=1)  # 1-minute remaining warning at T_close - 1m

            slot_label = "22:00-23:00" if is_final_11pm else f"{(h-1)%24:02d}:{w_min:02d}-{h:02d}:{w_min:02d}"
            act_id = f"ACT-{date_str.replace('-', '')}-{h:02d}{h_min:02d}-HOURLY"
            dur = 10 if is_final_11pm else 20
            activities.append({
                "activity_id": act_id,
                "account_id": account_id,
                "type": "HOURLY",
                "date": date_str,
                "title": f"Hourly Discipline Check ({slot_label})",
                "scheduled_time": dt_to_iso(h_sched),
                "window_start": dt_to_iso(h_start),
                "window_end": dt_to_iso(h_end),
                "notification_time": dt_to_iso(h_notif),
                "closing_warning_time": dt_to_iso(h_warn1m),
                "duration_minutes": dur,
                "window_duration_minutes": dur,
                "status": "SCHEDULED",
                "submitted_at": None,
                "points": hourly_points,
                "penalty_points": 0,
                "proof_requirements": "ACTIVITY_LOG_AND_PHOTO",
                "validation_rule": "HOURLY_DISCIPLINE_CHECKPOINT",
                "is_enabled": 1,
                "required_by_admin": 1,
                "schedule_id": schedule_id,
                "schedule_version": schedule_version,
                "details_json": json.dumps({"hour_slot": slot_label, "hour": h, "minute": h_min}),
            })

        zone = resolve_schedule_timezone(timezone_name)
        if timezone_name != "Asia/Kolkata":
            # Targets are wall-clock times in the confirmed account timezone.
            # Attach that zone before exposing absolute submission boundaries.
            for activity in activities:
                for key in ("scheduled_time", "window_start", "window_end", "notification_time", "closing_warning_time"):
                    value = activity.get(key)
                    if value:
                        activity[key] = datetime.fromisoformat(value).replace(tzinfo=zone).isoformat()
        activities.sort(key=lambda a: a["scheduled_time"])
        return activities

    def validate_routine_schedule(
        self,
        payload: Dict[str, Any],
        schedule_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validates a proposed routine schedule:
          1. Wake-up target strictly in 06:00 AM - 10:00 AM inclusive.
          2. Bath, Gym, and Custom activity target times valid.
          3. Admin-required tasks cannot be disabled.
          4. Submission windows do not overlap with each other or with independent hourly checkpoints.
        """
        date_str = schedule_date or payload.get("schedule_date") or self.get_next_calendar_date()
        policy = self._get_task_policy_config()
        from core.daily_college_decision import DailyCollegeDecision
        college=DailyCollegeDecision(self.db).get(payload.get('account_id','user_lok'),date_str) if self.db else {'attends_college':None,'bath_plans':[]}
        modern_baths=DailyCollegeDecision.activities(college) if college['attends_college'] is not None else None
        errors: List[str] = []
        warnings: List[str] = []
        conflicts: List[Dict[str, Any]] = []

        # 1. Validate Wake-Up Time (Wake-Up Target is independent; default to saved or 07:00 if omitted from schedule setup)
        raw_wakeup = str(payload.get("wake_up_time") or payload.get("wakeup_time") or "")
        if not raw_wakeup.strip():
            saved_target = None
            if self.db:
                try:
                    owner = payload.get('account_id') or (self.db.get_active_user_profile().get('user_id', 'user_lok') if hasattr(self.db, 'get_active_user_profile') else 'user_lok')
                    draft = self.get_routine_draft(target_date=date_str, account_id=owner) if hasattr(self, 'get_routine_draft') else None
                    if draft and draft.get('wake_up_time'):
                        saved_target = draft.get('wake_up_time')
                    elif hasattr(self.db, 'get_config_value'):
                        saved_target = self.db.get_config_value(f"wakeup_target_{date_str}", None) or self.db.get_config_value("wakeup_target", None)
                except Exception:
                    pass
            raw_wakeup = saved_target or "07:00"
        w_ok, norm_wakeup, w_msg = self.validate_wakeup_time(raw_wakeup)
        if not w_ok:
            errors.append(self.WAKEUP_ERROR_MSG)

        # 2. Validate Bath Time
        raw_bath = str(payload.get("bath_time") or "07:30")
        b_ok, norm_bath, _ = self.parse_time_24h(raw_bath)
        if not b_ok:
            errors.append("Invalid bath target time format. Please select a valid time.")

        # 3. Validate Gym Time & Admin Policy
        gym_required = bool(policy.get("gym_required_by_admin", False) or payload.get("gym_required_by_admin", False))
        gym_enabled = bool(payload.get("gym_enabled", True))
        from core.product_policy import GYM
        gym_duration = payload.get('gym_duration_minutes',GYM['default_workout_minutes'])
        if gym_enabled and (type(gym_duration) is not int or gym_duration not in GYM['supported_workout_minutes']):
            errors.append('Select a supported Gym workout duration')
            gym_duration = GYM['default_workout_minutes']
        daily_gym_not_planned = False
        if self.db:
            from core.gym_daily_decision import DailyGymDecision
            owner = payload.get('account_id') or self.db.get_active_user_profile().get('user_id','user_lok')
            daily_gym_not_planned = DailyGymDecision(self.db).get(owner,date_str)['gym_planned'] is False
            if daily_gym_not_planned:
                gym_enabled = False
        if gym_required and not gym_enabled and not daily_gym_not_planned:
            errors.append("Gym task is required by administrator policy and cannot be disabled.")

        raw_gym = str(payload.get("gym_time") or "18:30")
        g_ok, norm_gym, _ = self.parse_time_24h(raw_gym)
        if gym_enabled and not g_ok:
            errors.append("Invalid gym target time format. Please select a valid time.")

        # 4. Validate Custom Activities
        raw_customs = payload.get("custom_activities") or []
        normalized_customs: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_customs):
            if not isinstance(item, dict):
                continue
            c_enabled = bool(item.get("enabled", True))
            c_type = str(item.get("task_type") or item.get("type") or "CUSTOM").upper()
            cat_spec = self.TASK_CATALOG.get(c_type, self.TASK_CATALOG["CUSTOM"])
            if cat_spec.get("required_by_admin") and not c_enabled:
                errors.append(f"{cat_spec['title']} is required by administrator policy and cannot be disabled.")
            c_time = str(item.get("scheduled_time") or item.get("target_time") or "")
            c_ok, norm_c, _ = self.parse_time_24h(c_time)
            if c_enabled and not c_ok:
                errors.append(f"Invalid target time for custom activity #{idx + 1} ({c_type}).")
                continue
            if c_ok:
                dur = int(item.get("window_duration_minutes") or cat_spec["window_duration_minutes"] or 20)
                c_win = self.calculate_activity_window(date_str, norm_c, dur)
                c_hour, c_min = [int(p) for p in norm_c.split(":")]
                c_id = item.get("activity_id") or f"ACT-{date_str.replace('-', '')}-{c_hour:02d}{c_min:02d}-{c_type}"
                normalized_customs.append({
                    "activity_id": c_id,
                    "task_type": c_type,
                    "title": item.get("title") or cat_spec["title"],
                    "scheduled_time": norm_c,
                    "target_display": c_win["target_display"],
                    "window_duration_minutes": dur,
                    "window_start": c_win["window_start"],
                    "window_end": c_win["window_end"],
                    "window_display": c_win["window_display"],
                    "notification_time": c_win["notification_time"],
                    "notification_display": c_win["notification_display"],
                    "proof_requirements": cat_spec["proof_requirements"],
                    "points": int(cat_spec["points"]),
                    "validation_policy": cat_spec["validation_rule"],
                    "enabled": c_enabled,
                    "required_by_admin": bool(cat_spec.get("required_by_admin", False)),
                    "schedule_date": date_str,
                })

        # Build candidate windows for overlap checking if times are valid
        candidate_windows: List[Dict[str, Any]] = []
        if w_ok:
            w_win = self.calculate_wakeup_activity_window(date_str, norm_wakeup, 20)
            candidate_windows.append({
                "label": "Wake-up",
                "type": "WAKE_UP",
                "target": norm_wakeup,
                "start": iso_to_dt(w_win["window_start"]),
                "end": iso_to_dt(w_win["window_end"]),
                "window_display": w_win["window_display"],
            })
        if b_ok and modern_baths is None:
            b_win = self.calculate_activity_window(date_str, norm_bath, 20)
            candidate_windows.append({
                "label": "Bath",
                "type": "BATH",
                "target": norm_bath,
                "start": iso_to_dt(b_win["window_start"]),
                "end": iso_to_dt(b_win["window_end"]),
                "window_display": b_win["window_display"],
            })
        for bath in modern_baths or []:
            detail=json.loads(bath['details_json'])
            candidate_windows.append(dict(label=bath['title'],type='BATH',target=detail['planned_time'],
                start=iso_to_dt(detail['reserved_start']),end=iso_to_dt(detail['reserved_end']),
                window_display=self.format_time_12h(detail['planned_time'])+' · 1 hour reservation',modern_bath=True))
        if gym_enabled and g_ok:
            g_win = self.calculate_gym_window(date_str, norm_gym, gym_duration)
            candidate_windows.append({
                "label": "Gym",
                "type": "GYM",
                "target": norm_gym,
                "start": iso_to_dt(g_win["window_start"]),
                "end": iso_to_dt(g_win["window_end"]),
                "window_display": g_win["window_display"],
            })
        for nc in normalized_customs:
            if nc["enabled"]:
                candidate_windows.append({
                    "label": nc["title"],
                    "type": nc["task_type"],
                    "target": nc["scheduled_time"],
                    "start": iso_to_dt(nc["window_start"]),
                    "end": iso_to_dt(nc["window_end"]),
                    "window_display": nc["window_display"],
                })

        # Check task-vs-task overlaps
        for i in range(len(candidate_windows)):
            for j in range(i + 1, len(candidate_windows)):
                a = candidate_windows[i]
                b = candidate_windows[j]
                # Wake-up has a broad configured submission period. It may
                # overlap bath or gym windows; each activity remains separately
                # identified and each submission is independently validated.
                a_start, a_end, b_start, b_end = a["start"], a["end"], b["start"], b["end"]
                if "WAKE_UP" in (a["type"], b["type"]):
                    target_window = self.calculate_activity_window(date_str, norm_wakeup, 20)
                    if a["type"] == "WAKE_UP": a_start, a_end = iso_to_dt(target_window["window_start"]), iso_to_dt(target_window["window_end"])
                    else: b_start, b_end = iso_to_dt(target_window["window_start"]), iso_to_dt(target_window["window_end"])
                if a_start < b_end and b_start < a_end:
                    msg = (
                        f"Overlapping submission windows: {a['label']} ({a['window_display']}) "
                        f"conflicts with {b['label']} ({b['window_display']}). Please adjust the task time."
                    )
                    conflicts.append({
                        "activity_a": a["label"],
                        "activity_a_type": a["type"],
                        "activity_a_window": a["window_display"],
                        "activity_b": b["label"],
                        "activity_b_type": b["type"],
                        "activity_b_window": b["window_display"],
                        "message": msg,
                    })
                    warnings.append(msg)
                    errors.append(msg)

        # Check task-vs-hourly checkpoint overlaps after the wake-up window through 22:00.
        allow_ordinary_hourly = bool(
            payload.get("allow_ordinary_task_hourly_overlap", policy.get("allow_ordinary_task_hourly_overlap", False))
        )
        first_hourly = self.first_hourly_checkpoint_hour(
            date_str, norm_wakeup if w_ok else "09:00", w_win["window_end"] if w_ok else dt_to_iso(
                datetime.strptime(f"{date_str} 09:10:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            )
        )
        for h in range(first_hourly, 23):
            h_win = self.calculate_activity_window(date_str, f"{h:02d}:{int(norm_wakeup.split(':')[1]) if w_ok else 0:02d}", 20)
            h_start = iso_to_dt(h_win["window_start"])
            h_end = iso_to_dt(h_win["window_end"])
            # Preserve existing morning task choices and the explicit Gym
            # coexistence policy when restoring earlier checkpoint visibility.
            if h < 10:
                continue
            for cw in candidate_windows:
                if cw["type"] == "WAKE_UP":
                    continue
                if cw["type"] == "GYM" and policy.get("allow_gym_hourly_coexistence", True):
                    continue
                if cw.get('modern_bath'):
                    # Android reserves one canonical hourly occurrence, rather
                    # than forbidding a broad Bath proof window over hourly work.
                    continue
                if cw["start"] < h_end and h_start < cw["end"]:
                    msg = (
                        f"Overlapping submission windows: {cw['label']} ({cw['window_display']}) "
                        f"conflicts with Hourly checkpoint ({h_win['window_display']}). Please adjust the task time."
                    )
                    conflicts.append({
                        "activity_a": cw["label"],
                        "activity_a_type": cw["type"],
                        "activity_a_window": cw["window_display"],
                        "activity_b": f"Hourly checkpoint ({self.format_time_12h(f'{h:02d}:00')})",
                        "activity_b_type": "HOURLY",
                        "activity_b_window": h_win["window_display"],
                        "message": msg,
                    })
                    warnings.append(msg)
                    if not allow_ordinary_hourly:
                        errors.append(msg)

        return {
            "valid": len(errors) == 0,
            "schedule_date": date_str,
            "wake_up_time": norm_wakeup if w_ok else raw_wakeup,
            "bath_time": norm_bath if b_ok else raw_bath,
            "gym_time": norm_gym if g_ok else raw_gym,
            "gym_duration_minutes": gym_duration,
            "gym_window": self.calculate_gym_window(date_str,norm_gym,gym_duration) if g_ok else None,
            "gym_enabled": gym_enabled,
            "gym_required_by_admin": gym_required,
            "custom_activities": normalized_customs,
            "errors": errors,
            "warnings": warnings,
            "conflicts": conflicts,
        }

    def build_routine_summary(
        self,
        schedule_data: Dict[str, Any],
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """
        Section 8: Builds the complete Next-Day Routine Review Summary including
        windows, proof requirements, independent hourly checkpoints, and potential points.
        """
        date_str = schedule_data.get("schedule_date") or self.get_next_calendar_date()
        from core.gym_daily_decision import DailyGymDecision
        daily_gym = DailyGymDecision(self.db).get(account_id,date_str) if self.db else dict(status='GYM_DECISION_REQUIRED',revision=0,gym_planned=None)
        from core.daily_college_decision import DailyCollegeDecision
        college=DailyCollegeDecision(self.db).get(account_id,date_str) if self.db else dict(attends_college=None,bath_plans=[])
        baths=DailyCollegeDecision.activities(college) if college['attends_college'] is not None else []
        policy = self._get_task_policy_config()
        validation = self.validate_routine_schedule(schedule_data, date_str)
        from core.product_policy import GYM

        w_time = validation["wake_up_time"] if self.parse_time_24h(validation["wake_up_time"])[0] else "07:00"
        b_time = validation["bath_time"] if self.parse_time_24h(validation["bath_time"])[0] else "07:30"
        g_time = validation["gym_time"] if self.parse_time_24h(validation["gym_time"])[0] else "18:30"
        g_enabled = validation["gym_enabled"] and daily_gym['gym_planned'] is not False

        w_win = self.calculate_wakeup_activity_window(date_str, w_time, 20)
        b_win = self.calculate_activity_window(date_str, b_time, 20)
        g_win = self.calculate_gym_window(date_str, g_time, validation['gym_duration_minutes'])

        wakeup_pts = int(policy["wakeup_points"]) + int(policy.get("wakeup_track_points", 25))
        bath_pts = int(policy["bath_points"])
        if college['attends_college'] is not None:
            bath_pts=sum(item['points'] for item in baths)
            if baths:
                first=baths[0]
                b_time=iso_to_dt(first['scheduled_time']).strftime('%H:%M')
                b_win.update(target_display=self.format_time_12h(b_time))
                for field in ('window_start','window_end','notification_time','closing_warning_time'):
                    label={'notification_time':'notification','closing_warning_time':'closing_warning'}.get(field,field)
                    b_win[field]=first[field]
                    b_win[label+'_display']=self.format_time_12h(iso_to_dt(first[field]).strftime('%H:%M'))
                b_win['window_display']=' · '.join(self.format_time_12h(iso_to_dt(item['window_start']).strftime('%H:%M'))+'–'+self.format_time_12h(iso_to_dt(item['window_end']).strftime('%H:%M')) for item in baths)
            else:
                b_win.update(target_display='No Bath time configured',window_start_display='—',window_end_display='—',window_display='Choose a Bath time to reserve an hour',notification_display='—',closing_warning_display='—')
        gym_pts = int(policy["gym_points"]) if g_enabled else 0
        hourly_pts_each = int(policy["hourly_points"])
        hourly_start_hour = self.first_hourly_checkpoint_hour(date_str, w_time, w_win["window_end"])
        hourly_count = max(0, 23 - hourly_start_hour)
        custom_pts = sum(int(c["points"]) for c in validation["custom_activities"] if c.get("enabled", True))
        raw_potential = wakeup_pts + bath_pts + gym_pts + (hourly_count * hourly_pts_each) + custom_pts

        try:
            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
            formatted_date = dt_obj.strftime("%A, %d %B %Y")
        except Exception:
            formatted_date = date_str

        status_val = schedule_data.get("status", "DRAFT")
        is_locked = status_val in ("SCHEDULED", "LOCKED")

        return {
            "schedule_id": schedule_data.get("schedule_id") or f"SCHED-{date_str.replace('-', '')}-V{schedule_data.get('schedule_version', 1)}",
            "account_id": account_id,
            "schedule_version": int(schedule_data.get("schedule_version", 1)),
            "schedule_date": date_str,
            "formatted_date": formatted_date,
            "gym_decision": daily_gym,
            "college_decision": college,
            "bath_occurrences": baths,
            "timezone": schedule_data.get("timezone", "Asia/Kolkata"),
            "status": status_val,
            "is_locked": is_locked,
            "cutoff_time": schedule_data.get("cutoff_time", "23:59"),
            "created_at": schedule_data.get("created_at"),
            "confirmed_at": schedule_data.get("confirmed_at"),
            "confirmed_by_actor": schedule_data.get("confirmed_by_actor"),
            "confirmed_by_device": schedule_data.get("confirmed_by_device"),
            "locked_at": schedule_data.get("locked_at"),
            "wake_up": {
                "task_type": "WAKE_UP",
                "title": "Wake-up Challenge",
                "target_time": w_time,
                "target_display": w_win["target_display"],
                "window_opens": w_win["window_start_display"],
                "window_closes": w_win["window_end_display"],
                "window_display": w_win["window_display"],
                "notification_time": w_win["notification_display"],
                "closing_warning_time": w_win["closing_warning_display"],
                "allowed_range": "06:00 AM–10:00 AM",
                "required_proof": "Camera + Bluetooth Earbuds + Motivation Track",
                "possible_points": wakeup_pts,
                "window_points": int(policy["wakeup_points"]),
                "track_points": int(policy.get("wakeup_track_points", 25)),
                "status": status_val,
            },
            "bath": {
                "task_type": "BATH",
                "title": "Bath Routine" if college['attends_college'] is not None else "Morning Bath Routine",
                "target_time": b_time,
                "target_display": b_win["target_display"],
                "window_opens": b_win["window_start_display"],
                "window_closes": b_win["window_end_display"],
                "window_display": b_win["window_display"],
                "notification_time": b_win["notification_display"],
                "closing_warning_time": b_win["closing_warning_display"],
                "required_proof": "Photo proof · 5 points per occurrence" if college['attends_college'] is not None else "Photo Proof or Verification within 20m Window",
                "possible_points": bath_pts,
                "status": status_val,
            },
            "gym": {
                "task_type": "GYM",
                "duration_minutes": validation['gym_duration_minutes'],
                "supported_durations": GYM['supported_workout_minutes'],
                "title": "Gym Workout",
                "target_time": g_time,
                "target_display": g_win["target_display"],
                "window_opens": g_win["window_start_display"],
                "window_closes": g_win["window_end_display"],
                "window_display": g_win["window_display"],
                "notification_time": g_win["notification_display"],
                "closing_warning_time": g_win["closing_warning_display"],
                "required_proof": "30+ Min Session + Workout Evidence",
                "possible_points": int(policy["gym_points"]),
                "enabled": g_enabled,
                "required_by_admin": validation["gym_required_by_admin"],
                "status": status_val if g_enabled else "DISABLED",
            },
            "custom_activities": validation["custom_activities"],
            "hourly_checkpoints": {
                "preserve_independent": True,
                "start_hour": self.format_time_12h(w_time),
                "end_hour": "10:00 PM",
                "total_checkpoints": hourly_count,
                "window_rule": "Wake-up target then every 60m; opens 10m before, closes 10m after, reminder 1m before opening",
                "points_per_checkpoint": hourly_pts_each,
                "example_10am": {
                    "target": "10:00 AM",
                    "notification": "09:49 AM",
                    "window_opens": "09:50 AM",
                    "window_closes": "10:10 AM",
                },
                "example_11am": {
                    "target": "11:00 AM",
                    "notification": "10:49 AM",
                    "window_opens": "10:50 AM",
                    "window_closes": "11:10 AM",
                },
            },
            "potential_points": {
                "wake_up_points": wakeup_pts,
                "bath_points": bath_pts,
                "gym_points": gym_pts,
                "custom_activities_points": custom_pts,
                "hourly_checkpoints_points": hourly_count * hourly_pts_each,
                "raw_total_possible": raw_potential,
                "daily_cap": 100,
            },
            "validation": validation,
            "reschedule_audit_log": schedule_data.get("reschedule_audit_log") or [],
        }

    def get_next_day_schedule_state(
        self,
        schedule_date: Optional[str] = None,
        account_id: str = "user_lok",
        today_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieves the current state of tomorrow's schedule (confirmed if locked,
        saved draft if in progress, or initial unconfirmed template).
        Never silently generates a fake confirmed schedule.
        """
        if today_date and not schedule_date:
            try:
                dt = datetime.strptime(today_date, "%Y-%m-%d")
                target_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                target_date = self.get_next_calendar_date()
        else:
            target_date = schedule_date or self.get_next_calendar_date()
        policy = self._get_task_policy_config()

        if self.db:
            existing = self.db.get_latest_routine_schedule(target_date, account_id)
            if existing:
                summary = self.build_routine_summary(existing, account_id)
                summary["has_saved_draft_or_confirmed"] = True
                summary["reminder_needed"] = existing.get("status") == "DRAFT"
                return summary

        default_draft = {
            "schedule_id": f"SCHED-{target_date.replace('-', '')}-V1",
            "account_id": account_id,
            "schedule_version": 1,
            "schedule_date": target_date,
            "timezone": "Asia/Kolkata",
            "status": "DRAFT",
            "wake_up_time": "07:00",
            "bath_time": "07:30",
            "gym_time": "18:30",
            "gym_enabled": True,
            "gym_required_by_admin": bool(policy.get("gym_required_by_admin", False)),
            "custom_activities": [],
        }
        summary = self.build_routine_summary(default_draft, account_id)
        summary["has_saved_draft_or_confirmed"] = False
        summary["reminder_needed"] = True
        return summary

    def save_next_day_draft(
        self,
        payload: Dict[str, Any],
        account_id: str = "user_lok",
        device_id: str = "windows_pc",
    ) -> Dict[str, Any]:
        """
        Saves or updates an eligible next-day schedule draft.
        Enforces wake-up range (06:00-10:00) and prevents overwriting a confirmed/locked schedule.
        """
        target_date = payload.get("schedule_date") or self.get_next_calendar_date()
        if self.db:
            confirmed = self.db.get_confirmed_routine_schedule(target_date, account_id)
            if confirmed:
                return {
                    "success": False,
                    "error": "Schedule is already confirmed and locked. Use admin-authorized rescheduling to modify a locked schedule.",
                    "is_locked": True,
                    "schedule": self.build_routine_summary(confirmed, account_id),
                }

        raw_wakeup = str(payload.get("wake_up_time") or payload.get("wakeup_time") or "07:00")
        w_ok, norm_wakeup, w_err = self.validate_wakeup_time(raw_wakeup)
        if not w_ok:
            return {
                "success": False,
                "error": self.WAKEUP_ERROR_MSG,
                "field": "wake_up_time",
            }

        validation = self.validate_routine_schedule(payload, target_date)
        now_iso = dt_to_iso(now_ist())

        draft_rec = {
            "schedule_id": f"SCHED-{target_date.replace('-', '')}-V1",
            "account_id": account_id,
            "schedule_version": 1,
            "schedule_date": target_date,
            "timezone": payload.get("timezone") or "Asia/Kolkata",
            "status": "DRAFT",
            "wake_up_time": validation["wake_up_time"],
            "bath_time": validation["bath_time"],
            "gym_time": validation["gym_time"],
            "gym_duration_minutes": validation["gym_duration_minutes"],
            "gym_enabled": validation["gym_enabled"],
            "gym_required_by_admin": validation["gym_required_by_admin"],
            "custom_activities": validation["custom_activities"],
            "created_at": now_iso,
            "updated_at": now_iso,
            "cutoff_time": payload.get("cutoff_time") or "23:59",
        }
        if self.db:
            draft_rec = self.db.save_routine_schedule_record(draft_rec)

        summary = self.build_routine_summary(draft_rec, account_id)
        return {
            "success": True,
            "status": "DRAFT",
            "message": "Draft schedule saved. Review and confirm to lock tomorrow's routine.",
            "strictbot_message": f"Draft saved for {target_date}. Please review and confirm your schedule.",
            "schedule": summary,
        }

    def confirm_next_day_schedule(
        self,
        payload: Dict[str, Any],
        account_id: str = "user_lok",
        actor: str = "user_lok",
        device_id: str = "windows_pc",
    ) -> Dict[str, Any]:
        """
        Section 8 & 9: Validates and confirms the next-day routine schedule, locking it
        as an immutable version (status = SCHEDULED), generating the authoritative
        activity windows, and queueing cross-device synchronization.
        """
        target_date = payload.get("schedule_date") or self.get_next_calendar_date()
        if self.db:
            confirmed = self.db.get_confirmed_routine_schedule(target_date, account_id)
            if confirmed:
                return {
                    "success": False,
                    "error": "Tomorrow's schedule is already confirmed and locked. Ordinary edits are not permitted after confirmation.",
                    "is_locked": True,
                    "schedule": self.build_routine_summary(confirmed, account_id),
                }
            existing_draft = self.db.get_draft_routine_schedule(target_date, account_id)
            if existing_draft:
                merged = dict(existing_draft)
                for k in ("wake_up_time", "bath_time", "gym_time", "gym_duration_minutes", "gym_enabled", "custom_activities", "timezone"):
                    if k in payload and payload[k] is not None:
                        merged[k] = payload[k]
                if "wakeup_time" in payload and payload["wakeup_time"] is not None:
                    merged["wake_up_time"] = payload["wakeup_time"]
                payload = merged

        validation = self.validate_routine_schedule(payload, target_date)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["errors"][0] if validation["errors"] else "Schedule validation failed.",
                "errors": validation["errors"],
                "warnings": validation["warnings"],
                "conflicts": validation["conflicts"],
                "validation": validation,
            }

        now_iso = dt_to_iso(now_ist())
        version = 1
        sched_id = f"SCHED-{target_date.replace('-', '')}-V{version}"
        rec = {
            "schedule_id": sched_id,
            "account_id": account_id,
            "schedule_version": version,
            "schedule_date": target_date,
            "timezone": payload.get("timezone") or "Asia/Kolkata",
            "status": "SCHEDULED",
            "wake_up_time": validation["wake_up_time"],
            "bath_time": validation["bath_time"],
            "gym_time": validation["gym_time"],
            "gym_duration_minutes": validation["gym_duration_minutes"],
            "gym_enabled": validation["gym_enabled"],
            "gym_required_by_admin": validation["gym_required_by_admin"],
            "custom_activities": validation["custom_activities"],
            "created_at": payload.get("created_at") or now_iso,
            "updated_at": now_iso,
            "confirmed_at": now_iso,
            "confirmed_by_actor": actor,
            "confirmed_by_device": device_id,
            "locked_at": now_iso,
            "cutoff_time": payload.get("cutoff_time") or "23:59",
            "reschedule_audit_log": [],
        }

        if self.db:
            rec = self.db.save_routine_schedule_record(rec)
            self._materialize_routine_activities_in_db(rec)
            self._enqueue_schedule_sync_event(rec, device_id)

        summary = self.build_routine_summary(rec, account_id)
        w_disp = summary["wake_up"]["target_display"].lstrip("0")
        return {
            "success": True,
            "status": "SCHEDULED",
            "is_locked": True,
            "schedule_id": sched_id,
            "schedule_version": version,
            "message": f"Tomorrow's schedule ({target_date}) is confirmed and locked.",
            "strictbot_message": f"Tomorrow's wake-up target is {w_disp}. Your other activities are scheduled.",
            "schedule": summary,
        }

    def admin_reschedule_routine(
        self,
        payload: Dict[str, Any],
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """
        Section 9: Admin-authorized rescheduling of a locked schedule.
        Verifies Admin PIN, creates a new immutable schedule version (V+1),
        preserves the historical version as SUPERSEDED, writes an audit record,
        prevents reopening already missed/closed activities,
        and synchronizes the new version across devices.
        """
        admin_pin = str(payload.get("admin_pin") or payload.get("pin") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        admin_actor = str(payload.get("admin_actor") or payload.get("admin_id") or "admin").strip()
        device_id = str(payload.get("device_id") or "windows_pc").strip()

        if not self.db or not self.db.verify_admin_pin(admin_pin) or self.db.is_default_admin_pin():
            return {
                "success": False,
                "error": "Unauthorized: Valid Administrator PIN is required to reschedule a locked routine.",
            }
        if not reason:
            return {
                "success": False,
                "error": "A mandatory audit reason is required for admin-authorized rescheduling.",
            }

        target_date = payload.get("schedule_date") or self.get_next_calendar_date()
        current_sched = None
        if self.db:
            current_sched = self.db.get_latest_routine_schedule(target_date, account_id)

        old_version = int(current_sched["schedule_version"]) if current_sched else 1
        new_version = old_version + 1 if (current_sched and current_sched.get("status") in ("SCHEDULED", "LOCKED")) else 1

        merged_payload = dict(current_sched or {})
        for k in ("wake_up_time", "bath_time", "gym_time", "gym_duration_minutes", "gym_enabled", "custom_activities", "timezone"):
            if k in payload and payload[k] is not None:
                merged_payload[k] = payload[k]
        if "wakeup_time" in payload and payload["wakeup_time"] is not None:
            merged_payload["wake_up_time"] = payload["wakeup_time"]

        validation = self.validate_routine_schedule(merged_payload, target_date)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["errors"][0] if validation["errors"] else "Invalid reschedule parameters.",
                "errors": validation["errors"],
                "conflicts": validation["conflicts"],
                "validation": validation,
            }

        # Section 9: Never allow moving a missed/closed activity into the future to obtain another proof window
        if self.db and current_sched:
            with self.db._connect() as conn:
                missed_rows = conn.execute(
                    """
                    SELECT type, status FROM scheduled_activities
                    WHERE date = ? AND account_id = ? AND status IN ('MISSED', 'CLOSED', 'REJECTED_LATE')
                    """,
                    (target_date, account_id),
                ).fetchall()
            missed_types = {r["type"] for r in missed_rows}
            if "WAKE_UP" in missed_types and validation["wake_up_time"] != current_sched.get("wake_up_time"):
                return {
                    "success": False,
                    "error": "Cannot move missed activity (Wake-up) into the future to obtain another proof window.",
                }
            if "BATH" in missed_types and validation["bath_time"] != current_sched.get("bath_time"):
                return {
                    "success": False,
                    "error": "Cannot move missed activity (Bath) into the future to obtain another proof window.",
                }
            if "GYM" in missed_types and validation["gym_time"] != current_sched.get("gym_time"):
                return {
                    "success": False,
                    "error": "Cannot move missed activity (Gym) into the future to obtain another proof window.",
                }

        now_iso = dt_to_iso(now_ist())
        new_sched_id = f"SCHED-{target_date.replace('-', '')}-V{new_version}"
        prev_audit_log = list((current_sched or {}).get("reschedule_audit_log") or [])
        audit_entry = {
            "audit_id": f"RSCH-{uuid.uuid4().hex[:8].upper()}",
            "schedule_id": new_sched_id,
            "account_id": account_id,
            "schedule_date": target_date,
            "old_version": old_version,
            "new_version": new_version,
            "admin_actor": admin_actor,
            "device_id": device_id,
            "reason": reason,
            "created_at": now_iso,
            "previous_schedule": {
                "wake_up_time": (current_sched or {}).get("wake_up_time"),
                "bath_time": (current_sched or {}).get("bath_time"),
                "gym_time": (current_sched or {}).get("gym_time"),
                "gym_enabled": (current_sched or {}).get("gym_enabled"),
                "custom_activities": (current_sched or {}).get("custom_activities", []),
            },
            "new_schedule": {
                "wake_up_time": validation["wake_up_time"],
                "bath_time": validation["bath_time"],
                "gym_time": validation["gym_time"],
            "gym_duration_minutes": validation["gym_duration_minutes"],
                "gym_enabled": validation["gym_enabled"],
                "custom_activities": validation["custom_activities"],
            },
        }
        prev_audit_log.append(audit_entry)

        if self.db and current_sched and current_sched.get("status") in ("SCHEDULED", "LOCKED"):
            superseded = dict(current_sched)
            superseded["status"] = "SUPERSEDED"
            superseded["updated_at"] = now_iso
            self.db.save_routine_schedule_record(superseded)

        new_rec = {
            "schedule_id": new_sched_id,
            "account_id": account_id,
            "schedule_version": new_version,
            "schedule_date": target_date,
            "timezone": merged_payload.get("timezone") or "Asia/Kolkata",
            "status": "SCHEDULED",
            "wake_up_time": validation["wake_up_time"],
            "bath_time": validation["bath_time"],
            "gym_time": validation["gym_time"],
            "gym_duration_minutes": validation["gym_duration_minutes"],
            "gym_enabled": validation["gym_enabled"],
            "gym_required_by_admin": validation["gym_required_by_admin"],
            "custom_activities": validation["custom_activities"],
            "created_at": now_iso,
            "updated_at": now_iso,
            "confirmed_at": now_iso,
            "confirmed_by_actor": admin_actor,
            "confirmed_by_device": device_id,
            "locked_at": now_iso,
            "cutoff_time": merged_payload.get("cutoff_time") or "23:59",
            "reschedule_audit_log": prev_audit_log,
        }

        if self.db:
            new_rec = self.db.save_routine_schedule_record(new_rec)
            self.db.record_schedule_reschedule_audit(audit_entry)
            self._materialize_routine_activities_in_db(new_rec, is_reschedule=True)
            self._enqueue_schedule_sync_event(new_rec, device_id)

        summary = self.build_routine_summary(new_rec, account_id)
        return {
            "success": True,
            "status": "SCHEDULED",
            "schedule_id": new_sched_id,
            "old_version": old_version,
            "schedule_version": new_version,
            "audit_record": audit_entry,
            "message": f"Schedule rescheduled by admin to version {new_version}.",
            "schedule": summary,
        }

    def _materialize_routine_activities_in_db(
        self,
        sched_rec: Dict[str, Any],
        is_reschedule: bool = False,
        reference_now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Writes the confirmed schedule's activities into `scheduled_activities`.
        Never resets, extends, or duplicates independent HOURLY checkpoints.
        Never reopens already MISSED, CLOSED, or VERIFIED activities during a reschedule.
        """
        if not self.db:
            return []
        date_str = sched_rec["schedule_date"]
        account_id = sched_rec.get("account_id", "user_lok")
        policy = self._get_task_policy_config()

        items = self.generate_daily_schedule(
            date_str=date_str,
            account_id=account_id,
            wakeup_time=sched_rec["wake_up_time"],
            bath_time=sched_rec["bath_time"],
            gym_time=sched_rec["gym_time"],
            gym_enabled=bool(sched_rec.get("gym_enabled", True)) and self.daily_gym_is_not_disabled(date_str,account_id),
            custom_activities=sched_rec.get("custom_activities") or [],
            schedule_id=sched_rec["schedule_id"],
            schedule_version=int(sched_rec["schedule_version"]),
            bath_points=int(policy["bath_points"]),
            gym_points=int(policy["gym_points"]),
            wakeup_points=int(policy["wakeup_points"]),
            hourly_points=int(policy["hourly_points"]),
            timezone_name=sched_rec.get("timezone") or "Asia/Kolkata",
            gym_duration_minutes=sched_rec.get('gym_duration_minutes'),
        )
        items = self._apply_daily_bath_plan(items,date_str,account_id,sched_rec)
        from core.daily_routine_gate import DailyRoutineGate
        gate = self.get_daily_routine_gate(date_str, account_id, reference_now)
        items = self._apply_approved_schedule_exceptions(items, sched_rec)

        with self.db._connect() as conn:
            self._retire_changed_hourly_plan(conn, items, date_str, account_id, reference_now or now_ist())
            if is_reschedule:
                conn.execute(
                    """
                    DELETE FROM scheduled_activities
                    WHERE date = ? AND account_id = ? AND type != 'HOURLY'
                      AND status IN ('SCHEDULED', 'UPCOMING', 'NOTIFIED')
                    """,
                    (date_str, account_id),
                )

            for item in items:
                if item["type"] == "HOURLY":
                    if DailyRoutineGate.activity_is_eligible(item, gate, for_submission=True):
                        self._materialize_legacy_hourly(conn, item)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO scheduled_activities (
                            activity_id, account_id, type, title, date, scheduled_time,
                            window_start, window_end, window_duration_minutes,
                            notification_time, closing_warning_time,
                            status, submitted_at, points, penalty_points,
                            proof_requirements, validation_rule, is_enabled,
                            required_by_admin, schedule_id, schedule_version, details_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["activity_id"], item["account_id"], item["type"], item.get("title", ""),
                            item["date"], item["scheduled_time"], item["window_start"], item["window_end"],
                            item.get("window_duration_minutes", 20), item["notification_time"],
                            item.get("closing_warning_time", ""), item["status"], item["submitted_at"],
                            item["points"], item.get("penalty_points", 0),
                            item.get("proof_requirements", "REQUIRED"), item["validation_rule"],
                            item.get("is_enabled", 1), item.get("required_by_admin", 1),
                            item.get("schedule_id", ""), item.get("schedule_version", 1),
                            item["details_json"],
                        ),
                    )
                    if item["status"] == "EXCUSED":
                        conn.execute("UPDATE scheduled_activities SET status='EXCUSED', attendance_status='EXCUSED', points=0, details_json=?, schedule_version=?, schedule_id=? WHERE activity_id=? AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','BLOCKED_WAKEUP')",
                                     (item["details_json"], item.get("schedule_version", 1), item.get("schedule_id", ""), item["activity_id"]))
                else:
                    retained = conn.execute("SELECT status FROM scheduled_activities WHERE activity_id=?", (item["activity_id"],)).fetchone()
                    if retained and retained["status"] in ("MISSED", "CLOSED", "VERIFIED", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "COMPLETED", "REJECTED", "SKIPPED", "EXCUSED", "CANCELLED"):
                        continue
                    details=json.loads(item.get('details_json') or '{}')
                    modern_bath=item['type']=='BATH' and bool(details.get('college_plan_revision'))
                    if modern_bath:
                        prior_baths=conn.execute("SELECT activity_id,status,details_json FROM scheduled_activities WHERE date=? AND account_id=? AND type='BATH' AND activity_id!=?",
                            (date_str,account_id,item['activity_id'])).fetchall()
                        same=[]
                        for prior in prior_baths:
                            old_details=json.loads(prior['details_json'] or '{}')
                            if int(old_details.get('occurrence',1))==int(details['occurrence']):same.append(prior)
                        if any(prior['status'] in ('MISSED','CLOSED','VERIFIED','SUBMITTED','VALIDATING','AI_PROVISIONAL','COMPLETED','REJECTED') for prior in same):continue
                        for prior in same:
                            if prior['status'] in ('SCHEDULED','UPCOMING','NOTIFIED','OPEN'):
                                conn.execute("UPDATE scheduled_activities SET status='CANCELLED',is_enabled=0 WHERE activity_id=?",(prior['activity_id'],))
                    existing_final = conn.execute(
                        """
                        SELECT activity_id, status FROM scheduled_activities
                        WHERE date = ? AND account_id = ? AND type = ?
                          AND status IN ('MISSED', 'CLOSED', 'VERIFIED', 'SUBMITTED', 'VALIDATING', 'EXCUSED', 'CANCELLED')
                        """,
                        (date_str, account_id, item["type"]),
                    ).fetchone()
                    if existing_final and item["type"] in ("WAKE_UP", "BATH", "GYM") and not modern_bath:
                        continue

                    if item["type"] in ("WAKE_UP", "BATH", "GYM") and not modern_bath:
                        conn.execute(
                            """
                            DELETE FROM scheduled_activities
                            WHERE date = ? AND account_id = ? AND type = ?
                              AND status IN ('SCHEDULED', 'UPCOMING', 'NOTIFIED')
                            """,
                            (date_str, account_id, item["type"]),
                        )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO scheduled_activities (
                            activity_id, account_id, type, title, date, scheduled_time,
                            window_start, window_end, window_duration_minutes,
                            notification_time, closing_warning_time,
                            status, submitted_at, points, penalty_points,
                            proof_requirements, validation_rule, is_enabled,
                            required_by_admin, schedule_id, schedule_version, details_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["activity_id"], item["account_id"], item["type"], item.get("title", ""),
                            item["date"], item["scheduled_time"], item["window_start"], item["window_end"],
                            item.get("window_duration_minutes", 20), item["notification_time"],
                            item.get("closing_warning_time", ""), item["status"], item["submitted_at"],
                            item["points"], item.get("penalty_points", 0),
                            item.get("proof_requirements", "REQUIRED"), item["validation_rule"],
                            item.get("is_enabled", 1), item.get("required_by_admin", 0),
                            item.get("schedule_id", ""), item.get("schedule_version", 1),
                            item["details_json"],
                        ),
                    )

            conn.execute("UPDATE scheduled_activities SET attendance_status='EXCUSED' WHERE date=? AND account_id=? AND status='EXCUSED'", (date_str, account_id))
        return self.get_activities_for_date(date_str, account_id, reference_now=reference_now)

    def _enqueue_schedule_sync_event(self, sched_rec: Dict[str, Any], device_id: str = "windows_pc") -> None:
        """Queues a schedule synchronization event for registered Android/Windows devices."""
        if not self.db:
            return
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sync_id = f"SYNC-SCHED-{sched_rec['schedule_id']}-{uuid.uuid4().hex[:6].upper()}"
        payload = {
            "event_type": "ROUTINE_SCHEDULE_SYNC",
            "schedule": sched_rec,
            "activities": self.get_activities_for_date(sched_rec["schedule_date"], sched_rec.get("account_id", "user_lok")),
            "daily_routine_gate": self.get_daily_routine_gate(sched_rec["schedule_date"], sched_rec.get("account_id", "user_lok")),
        }
        try:
            with self.db._connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO sync_queue (sync_id, device_id, created_at, payload_type, payload_json, version, status)
                    VALUES (?, ?, ?, 'ROUTINE_SCHEDULE_SYNC', ?, ?, 'PENDING_SYNC')
                    """,
                    (sync_id, device_id, now_iso, json.dumps(payload), int(sched_rec.get("schedule_version", 1))),
                )
        except Exception:
            pass

    def sync_routine_schedule(
        self,
        incoming_payload: Optional[Dict[str, Any]] = None,
        account_id: str = "user_lok",
        schedule_date: Optional[str] = None,
        device_id: str = "android_client",
    ) -> Dict[str, Any]:
        """
        Section 15: Synchronizes schedule state between Android APK and Windows EXE.
        Uses deterministic version comparison so the highest confirmed schedule_version wins,
        and returns the authoritative confirmed schedule, activity IDs, and window timestamps.
        """
        incoming_payload = incoming_payload or {}
        target_date = schedule_date or incoming_payload.get("schedule_date") or self.get_next_calendar_date()
        incoming_schedule = incoming_payload.get("schedule")
        device_id = incoming_payload.get("device_id") or device_id or "android_client"

        if incoming_schedule and isinstance(incoming_schedule, dict):
            inc_date = incoming_schedule.get("schedule_date") or target_date
            target_date = inc_date
            inc_status = incoming_schedule.get("status", "DRAFT")
            inc_version = int(incoming_schedule.get("schedule_version", 1))
            local_confirmed = self.db.get_confirmed_routine_schedule(target_date, account_id) if self.db else None

            if inc_status in ("SCHEDULED", "LOCKED"):
                if not local_confirmed:
                    res = self.confirm_next_day_schedule(
                        incoming_schedule,
                        account_id=account_id,
                        actor=incoming_schedule.get("confirmed_by_actor") or account_id,
                        device_id=incoming_schedule.get("confirmed_by_device") or device_id,
                    )
                    if not res.get("success"):
                        return res
                elif inc_version > int(local_confirmed.get("schedule_version", 1)):
                    # Registered clients reconcile server state. An incoming
                    # version number is not authorization to replace a locked
                    # routine; the approved revision service owns that change.
                    return {"success": False, "code": "SCHEDULE_REVISION_REQUIRES_APPROVAL",
                            "error": "Submit a schedule-change proposal before revising a confirmed routine.",
                            "schedule": self.build_routine_summary(local_confirmed, account_id)}
            elif inc_status == "DRAFT" and not local_confirmed:
                self.save_next_day_draft(incoming_schedule, account_id=account_id, device_id=device_id)

        state = self.get_next_day_schedule_state(target_date, account_id)
        activities = self.ensure_activities_in_db(
            target_date,
            account_id=account_id,
            wakeup_time=state["wake_up"]["target_time"],
        )
        return {
            "success": True,
            "schedule_date": target_date,
            "schedule_id": state["schedule_id"],
            "schedule_version": state["schedule_version"],
            "status": state["status"],
            "is_locked": state["is_locked"],
            "schedule": state,
            "activities": activities,
            "daily_routine_gate": self.get_daily_routine_gate(target_date, account_id),
            "server_time": self.get_authoritative_time(),
        }

    @staticmethod
    def generate_college_day_hourly_activities(
        date_str: str,
        account_id: str = "user_lok",
        hourly_end: int = 22,
        hourly_points: int = 8,
        schedule_id: str = "",
        schedule_version: int = 1,
        timezone_name: str = "Asia/Kolkata",
    ) -> List[Dict[str, Any]]:
        """
        Fixed college-day hourly submission schedule (invariant to wake-up start/completion time):
          - First hourly submission window: 9:00 AM–10:00 AM (ACT-YYYYMMDD-0900-HOURLY, hour_slot='09:00-10:00')
          - Subsequent hourly submissions: 10:00 AM–11:00 AM (h=11, ACT-YYYYMMDD-1100-HOURLY) onward
            using the configured hourly schedule anchored strictly to :00.
        """
        zone = resolve_schedule_timezone(timezone_name)
        activities: List[Dict[str, Any]] = []
        clean_date = date_str.replace("-", "")

        # 1. Fixed first hourly submission window: 09:00–10:00
        first_start = datetime.strptime(f"{date_str} 09:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
        first_end = datetime.strptime(f"{date_str} 10:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
        first_notif = first_start - timedelta(minutes=1)
        first_warn = first_end - timedelta(minutes=1)
        activities.append({
            "activity_id": f"ACT-{clean_date}-0900-HOURLY",
            "account_id": account_id,
            "type": "HOURLY",
            "date": date_str,
            "title": "Hourly Discipline Check (09:00-10:00)",
            "scheduled_time": dt_to_iso(first_end),
            "window_start": dt_to_iso(first_start),
            "window_end": dt_to_iso(first_end),
            "notification_time": dt_to_iso(first_notif),
            "closing_warning_time": dt_to_iso(first_warn),
            "duration_minutes": 60,
            "window_duration_minutes": 60,
            "status": "SCHEDULED",
            "submitted_at": None,
            "points": hourly_points,
            "penalty_points": 0,
            "proof_requirements": "ACTIVITY_LOG_AND_PHOTO",
            "validation_rule": "HOURLY_DISCIPLINE_CHECKPOINT",
            "is_enabled": 1,
            "required_by_admin": 1,
            "schedule_id": schedule_id,
            "schedule_version": schedule_version,
            "details_json": json.dumps({"hour_slot": "09:00-10:00", "hour": 9, "minute": 0, "is_college_fixed": True}),
        })

        # 2. Subsequent hourly submissions: 10:00-11:00 (h=11) onward, fixed at :00
        actual_end = min(max(11, int(hourly_end)), 23) if hourly_end >= 11 else int(hourly_end)
        for h in range(11, actual_end + 1):
            is_final_11pm = (h == 23)
            h_sched = datetime.strptime(f"{date_str} {h:02d}:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
            h_start = h_sched - timedelta(minutes=10)
            h_end = h_sched if is_final_11pm else (h_sched + timedelta(minutes=10))
            h_notif = h_start - timedelta(minutes=1)
            h_warn = h_end - timedelta(minutes=1)
            slot_label = "22:00-23:00" if is_final_11pm else f"{(h - 1) % 24:02d}:00-{h:02d}:00"
            dur = 10 if is_final_11pm else 20
            activities.append({
                "activity_id": f"ACT-{clean_date}-{h:02d}00-HOURLY",
                "account_id": account_id,
                "type": "HOURLY",
                "date": date_str,
                "title": f"Hourly Discipline Check ({slot_label})",
                "scheduled_time": dt_to_iso(h_sched),
                "window_start": dt_to_iso(h_start),
                "window_end": dt_to_iso(h_end),
                "notification_time": dt_to_iso(h_notif),
                "closing_warning_time": dt_to_iso(h_warn),
                "duration_minutes": dur,
                "window_duration_minutes": dur,
                "status": "SCHEDULED",
                "submitted_at": None,
                "points": hourly_points,
                "penalty_points": 0,
                "proof_requirements": "ACTIVITY_LOG_AND_PHOTO",
                "validation_rule": "HOURLY_DISCIPLINE_CHECKPOINT",
                "is_enabled": 1,
                "required_by_admin": 1,
                "schedule_id": schedule_id,
                "schedule_version": schedule_version,
                "details_json": json.dumps({"hour_slot": slot_label, "hour": h, "minute": 0, "is_college_fixed": True}),
            })
        return activities

    def _apply_daily_bath_plan(self,items,date_str,account_id,schedule=None):
        from core.daily_college_decision import DailyCollegeDecision
        if not self.db:return items
        decision=DailyCollegeDecision(self.db).get(account_id,date_str)
        if decision['attends_college'] is None:return items
        sched_id=(schedule or {}).get('schedule_id','')
        sched_ver=(schedule or {}).get('schedule_version',1)
        baths=DailyCollegeDecision.activities(decision)
        for item in baths:
            item['schedule_id']=sched_id
            item['schedule_version']=sched_ver
        if decision['attends_college'] is True:
            existing_hourlies=[i for i in items if i['type']=='HOURLY']
            h_pts=existing_hourlies[0]['points'] if existing_hourlies else int(self._get_task_policy_config().get('hourly_points',8))
            h_end=max((json.loads(i.get('details_json') or '{}').get('hour',22) for i in existing_hourlies),default=22)
            tz_name=(schedule or {}).get('timezone') or decision.get('timezone') or 'Asia/Kolkata'
            college_hourlies=self.generate_college_day_hourly_activities(
                date_str=date_str,
                account_id=account_id,
                hourly_end=h_end,
                hourly_points=h_pts,
                schedule_id=sched_id,
                schedule_version=sched_ver,
                timezone_name=tz_name,
            )
            combined=[item for item in items if item['type'] not in ('BATH','HOURLY')]+baths+college_hourlies
            combined.sort(key=lambda a:a['scheduled_time'])
            return combined
        return [item for item in items if item['type']!='BATH']+baths

    def daily_gym_is_not_disabled(self, date_str, account_id='user_lok'):
        if not self.db:
            return True
        from core.gym_daily_decision import DailyGymDecision
        return DailyGymDecision(self.db).get(account_id,date_str)['gym_planned'] is not False

    def ensure_activities_in_db(
        self,
        date_str: str,
        account_id: str = "user_lok",
        wakeup_time: str = "09:00",
        reference_now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Ensures that all scheduled activities for the date exist in the database."""
        if not self.db:
            return self.generate_daily_schedule(date_str, account_id, wakeup_time)

        confirmed = self.db.get_confirmed_routine_schedule(date_str, account_id)
        if confirmed:
            return self._materialize_routine_activities_in_db(confirmed, reference_now=reference_now)

        schedule = self.generate_daily_schedule(date_str, account_id, wakeup_time,
            gym_enabled=self.daily_gym_is_not_disabled(date_str,account_id))
        schedule = self._apply_daily_bath_plan(schedule,date_str,account_id)
        from core.daily_routine_gate import DailyRoutineGate
        gate = self.get_daily_routine_gate(date_str, account_id, reference_now)
        with self.db._connect() as conn:
            self._retire_changed_hourly_plan(conn, schedule, date_str, account_id, reference_now or now_ist())
            for item in schedule:
                if item["type"] == "HOURLY" and DailyRoutineGate.activity_is_eligible(item, gate, reference_now=reference_now or now_ist(), for_submission=True):
                    self._materialize_legacy_hourly(conn, item)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO scheduled_activities (
                        activity_id, account_id, type, title, date, scheduled_time,
                        window_start, window_end, window_duration_minutes,
                        notification_time, closing_warning_time,
                        status, submitted_at, points, penalty_points,
                        proof_requirements, validation_rule, is_enabled,
                        required_by_admin, schedule_id, schedule_version, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["activity_id"],
                        item["account_id"],
                        item["type"],
                        item.get("title", ""),
                        item["date"],
                        item["scheduled_time"],
                        item["window_start"],
                        item["window_end"],
                        item.get("window_duration_minutes", 20),
                        item["notification_time"],
                        item.get("closing_warning_time", ""),
                        item["status"],
                        item["submitted_at"],
                        item["points"],
                        item.get("penalty_points", 0),
                        item.get("proof_requirements", "REQUIRED"),
                        item["validation_rule"],
                        item.get("is_enabled", 1),
                        item.get("required_by_admin", 1),
                        item.get("schedule_id", ""),
                        item.get("schedule_version", 1),
                        item["details_json"],
                    ),
                )
        return self.get_activities_for_date(date_str, account_id, reference_now=reference_now)

    def get_activities_for_date(
        self,
        date_str: str,
        account_id: str = "user_lok",
        reference_now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves and evaluates activities for the date, updating missed/open/closed states.
        Section 10:
          SCHEDULED -> UPCOMING -> OPEN -> SUBMITTED -> VALIDATING -> VERIFIED or REJECTED
          If no valid submission by window_end:
          OPEN -> MISSED -> CLOSED (award 0 points, apply penalty only if configured, never reopen).
        """
        if not self.db:
            return self.generate_daily_schedule(date_str, account_id)

        now = reference_now or now_ist()
        from core.daily_routine_gate import DailyRoutineGate
        gate = self.get_daily_routine_gate(date_str, account_id, now)
        with self.db._connect() as conn:
            from core.daily_college_decision import DailyCollegeDecision
            DailyCollegeDecision.reconcile_bath_reservations(conn,account_id,date_str,now)
            rows = conn.execute(
                """
                SELECT *
                FROM scheduled_activities
                WHERE date = ? AND account_id = ?
                ORDER BY scheduled_time ASC
                """,
                (date_str, account_id),
            ).fetchall()

            activities: List[Dict[str, Any]] = []
            for r in rows:
                act = dict(r)
                current_status = act["status"]
                w_start = iso_to_dt(act["window_start"])
                w_end = iso_to_dt(act["window_end"])
                w_notif = iso_to_dt(act["notification_time"])

                details = {}
                try:
                    details = json.loads(act.get("details_json") or "{}")
                except Exception:
                    pass

                if not act.get("title"):
                    act["title"] = f"{act['type'].replace('_', ' ').title()} ({w_start.strftime('%H:%M')})"
                if current_status in ("EXCUSED", "CANCELLED"):
                    if current_status=='EXCUSED':
                        act['attendance_status']='EXCUSED'
                        conn.execute("UPDATE scheduled_activities SET attendance_status='EXCUSED' WHERE activity_id=?",(act['activity_id'],))
                    act.update(is_closed=True, lifecycle_state=current_status)
                    activities.append(act)
                    continue

                # Recover live/future occurrences blocked by the obsolete
                # wake-up dependency. Preserve already elapsed legacy history;
                # a presentation repair must not create retroactive penalties.
                if act.get('type') == 'HOURLY' and current_status in ('BLOCKED_WAKEUP', 'SKIPPED_PRE_GATE'):
                    if now >= w_end:
                        act.update(status='EXPIRED', is_closed=True, lifecycle_state='CLOSED')
                        activities.append(act)
                        continue
                    current_status = 'UPCOMING'
                    act['status'] = current_status
                    conn.execute("UPDATE scheduled_activities SET status='UPCOMING' WHERE activity_id=? AND status IN ('BLOCKED_WAKEUP','SKIPPED_PRE_GATE')", (act['activity_id'],))

                # Keep completed/history rows and approved exceptions intact.
                historical = current_status in ("VERIFIED", "REJECTED", "REJECTED_LATE", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "COMPLETED", "CLOSED", "MISSED")
                if act.get("type") == "HOURLY" and not historical:
                    eligible = DailyRoutineGate.activity_is_eligible(act, gate, reference_now=now)
                    act["daily_routine_gate"] = gate
                    if not eligible:
                        blocked = current_status if current_status in ("EXCUSED", "CANCELLED") else "SKIPPED_PRE_GATE" if gate["active"] else "BLOCKED_WAKEUP"
                        conn.execute("UPDATE scheduled_activities SET status=?, points=0, attendance_status=? WHERE activity_id=?",
                                     (blocked, "INACTIVE", act["activity_id"]))
                        act.update(status=blocked, points=0, attendance_status="INACTIVE", is_closed=True, lifecycle_state=blocked)
                        activities.append(act)
                        continue
                    if current_status == "BLOCKED_WAKEUP":
                        current_status = "UPCOMING"
                        act["status"] = current_status

                # If already completed or evaluated or awaiting AI validation, do not revert or mark missed!
                if current_status in ("VERIFIED", "REJECTED", "REJECTED_LATE", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "COMPLETED", "CLOSED", "SKIPPED"):
                    if act.get("type") == "HOURLY":
                        act["attendance_status"] = "ABSENT" if current_status in ("REJECTED_LATE", "CLOSED") else "PRESENT"
                        if not act.get("validation_status") or act.get("validation_status") == "NOT_SUBMITTED":
                            act["validation_status"] = {
                                "SUBMITTED": "PROCESSING", "VALIDATING": "PROCESSING",
                                "AI_PROVISIONAL": "PROVISIONAL_APPROVE", "VERIFIED": "VERIFIED",
                                "COMPLETED": "VERIFIED",
                                "REJECTED": "REJECTED", "REJECTED_LATE": "NOT_SUBMITTED",
                                "CLOSED": "NOT_SUBMITTED",
                            }.get(current_status, "NOT_SUBMITTED")
                    act["is_closed"] = current_status in ("VERIFIED", "REJECTED", "REJECTED_LATE", "CLOSED") or (now >= w_end)
                    act["lifecycle_state"] = "CLOSED" if current_status == "MISSED" else current_status
                    activities.append(act)
                    continue

                if current_status == "MISSED":
                    act["is_closed"] = True
                    act["lifecycle_state"] = "CLOSED"
                    activities.append(act)
                    continue

                # Automatic transition rules
                if now >= w_end:
                    # Window expired without submission -> OPEN -> MISSED -> CLOSED
                    closed_iso = dt_to_iso(w_end)
                    penalty = int(act.get("penalty_points") or 0)
                    awarded = -abs(penalty) if penalty != 0 else 0
                    details["window_state"] = "CLOSED"
                    details["transition_history"] = ["SCHEDULED", "UPCOMING", "OPEN", "MISSED", "CLOSED"]
                    details["closed_at"] = closed_iso
                    conn.execute(
                        """
                        UPDATE scheduled_activities
                        SET status = ?, points = ?, closed_at = ?, details_json = ?,
                            attendance_status = CASE WHEN type = 'HOURLY' THEN 'ABSENT' ELSE attendance_status END,
                            validation_status = CASE WHEN type = 'HOURLY' THEN 'NOT_SUBMITTED' ELSE validation_status END
                        WHERE activity_id = ?
                        """,
                        ("MISSED", awarded, closed_iso, json.dumps(details), act["activity_id"]),
                    )
                    act["status"] = "MISSED"
                    act["points"] = awarded
                    act["closed_at"] = closed_iso
                    if act.get("type") == "HOURLY":
                        act["attendance_status"] = "ABSENT"
                        act["validation_status"] = "NOT_SUBMITTED"
                    act["is_closed"] = True
                    act["lifecycle_state"] = "CLOSED"
                    act["details_json"] = json.dumps(details)
                elif w_start <= now < w_end:
                    # Inside active window
                    if current_status != "OPEN":
                        conn.execute(
                            "UPDATE scheduled_activities SET status = ?, attendance_status = CASE WHEN type = 'HOURLY' THEN 'OPEN' ELSE attendance_status END, opened_at = COALESCE(opened_at, ?) WHERE activity_id = ?",
                            ("OPEN", dt_to_iso(w_start), act["activity_id"]),
                        )
                        act["status"] = "OPEN"
                        if act.get("type") == "HOURLY":
                            act["attendance_status"] = "OPEN"
                            act["opened_at"] = dt_to_iso(w_start)
                    act["is_closed"] = False
                    act["lifecycle_state"] = "OPEN"
                elif w_notif <= now < w_start:
                    if current_status in ("SCHEDULED", "UPCOMING"):
                        conn.execute(
                            "UPDATE scheduled_activities SET status = ? WHERE activity_id = ?",
                            ("NOTIFIED", act["activity_id"]),
                        )
                        act["status"] = "NOTIFIED"
                    act["is_closed"] = False
                    act["lifecycle_state"] = "UPCOMING"
                else:
                    if current_status not in ("SCHEDULED", "UPCOMING"):
                        conn.execute(
                            "UPDATE scheduled_activities SET status = ? WHERE activity_id = ?",
                            ("UPCOMING", act["activity_id"]),
                        )
                        act["status"] = "UPCOMING"
                    act["is_closed"] = False
                    act["lifecycle_state"] = act["status"]

                activities.append(act)

            from core.window_state import window_state
            for activity in activities:
                eligible = DailyRoutineGate.activity_is_eligible(activity, gate, reference_now=now, for_submission=True)
                activity["submission_eligible"] = eligible
                activity["eligibility_status"] = "ELIGIBLE" if eligible else "CHECKPOINT_INACTIVE"
                activity["status"] = window_state(activity["status"], now, iso_to_dt(activity["window_start"]), iso_to_dt(activity["window_end"]), eligible)
                activity["window_state"] = activity["status"]
                activity["is_closed"] = now >= iso_to_dt(activity["window_end"]) or activity["status"] in ("EXCUSED", "CANCELLED", "VERIFIED", "REJECTED", "MISSED", "EXPIRED", "CLOSED")
                activity["lifecycle_state"] = "CLOSED" if activity["status"] == "MISSED" else activity["status"]
            return activities

    def evaluate_submission_timing(
        self,
        activity_id: str,
        submission_iso: Optional[str] = None,
        begin_ai_validation: bool = False,
        submission_id: Optional[str] = None,
        description: Optional[str] = None,
        evidence_id: Optional[str] = None,
        activity_category: Optional[str] = None,
        source_device_id: Optional[str] = None,
        allow_offline_reconcile: bool = False,
        record_submission: bool = True,
    ) -> Dict[str, Any]:
        """
        Authoritative Window Verification:
          Rules:
            window_start <= submission_time < window_end  -> VALID ON-TIME (SUBMITTED / VALIDATING)
            submission_time < window_start                -> TOO_EARLY
            submission_time >= window_end                 -> REJECTED_LATE
        """
        now = now_ist()
        try:
            sub_time = iso_to_dt(submission_iso) if submission_iso else now
        except ValueError:
            return {
                "allowed": False,
                "status": "ERROR",
                "activity_id": activity_id,
                "points": 0,
                "message": "Submission timestamp is invalid; submission was not accepted.",
            }

        if not self.db:
            return {"allowed": True, "status": "VALIDATING" if begin_ai_validation else "SUBMITTED", "message": "Evaluation simulated"}

        with self.db._connect() as conn:
            from core.daily_college_decision import DailyCollegeDecision
            occurrence=conn.execute('SELECT account_id,date FROM scheduled_activities WHERE activity_id=?',(activity_id,)).fetchone()
            if occurrence:
                DailyCollegeDecision.reconcile_bath_reservations(conn,occurrence['account_id'],occurrence['date'],now)
            row = conn.execute(
                "SELECT * FROM scheduled_activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()

            if not row:
                return {
                    "allowed": False,
                    "status": "ERROR",
                    "message": f"Activity {activity_id} not found",
                }

            act = dict(row)
            if act.get("type") == "HOURLY":
                # Run before timing rejection so an inactive day cannot produce
                # an ABSENT row or a late-submission penalty through this API.
                from core.daily_routine_gate import DailyRoutineGate
                gate = self.get_daily_routine_gate(act["date"], act.get("account_id", "user_lok"), now)
                if not DailyRoutineGate.activity_is_eligible(act, gate, reference_now=now, for_submission=True):
                    return {"allowed": False, "status": "CHECKPOINT_INACTIVE",
                            "points": 0, "activity_id": activity_id, "daily_routine_gate": gate,
                            "message": "This hourly occurrence is covered by an exception or is unavailable for submission."}
            try:
                w_start = iso_to_dt(act["window_start"])
                w_end = iso_to_dt(act["window_end"])
            except ValueError:
                return {
                    "allowed": False,
                    "status": "ERROR",
                    "activity_id": activity_id,
                    "points": 0,
                    "message": "The persisted activity window is invalid; submission was not accepted.",
                }

            # 1. Early Submission
            if sub_time < w_start:
                open_str = w_start.strftime("%H:%M")
                return {
                    "allowed": False,
                    "status": "TOO_EARLY",
                    "activity_id": activity_id,
                    "submission_time": dt_to_iso(sub_time),
                    "window_start": dt_to_iso(w_start),
                    "window_end": dt_to_iso(w_end),
                    "message": f"This activity opens at {open_str}.",
                }

            # 2. Late Submission
            if sub_time >= w_end:
                if act["status"] not in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED"):
                    conn.execute(
                        "UPDATE scheduled_activities SET status = ?, points = 0, closed_at = ?, attendance_status = CASE WHEN type = 'HOURLY' THEN 'ABSENT' ELSE attendance_status END, validation_status = CASE WHEN type = 'HOURLY' THEN 'NOT_SUBMITTED' ELSE validation_status END WHERE activity_id = ?",
                        ("REJECTED_LATE", dt_to_iso(w_end), activity_id),
                    )
                return {
                    "allowed": False,
                    "status": "REJECTED_LATE",
                    "activity_id": activity_id,
                    "submission_time": dt_to_iso(sub_time),
                    "window_start": dt_to_iso(w_start),
                    "window_end": dt_to_iso(w_end),
                    "points": 0,
                    "message": "Activity window expired. Late submissions cannot award points.",
                }

            # An activity can be accepted only once. Keep retries idempotent and
            # do not let late replays overwrite an existing decision.
            if act["status"] in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED"):
                return {
                    "allowed": False,
                    "status": "DUPLICATE",
                    "activity_id": activity_id,
                    "submission_time": dt_to_iso(sub_time),
                    "window_start": dt_to_iso(w_start),
                    "window_end": dt_to_iso(w_end),
                    "points": 0,
                    "message": "This activity already has a recorded submission or decision.",
                }

            # A registered Android client may reconnect after a slot has been
            # finalized. Reconcile only its original timestamp when it is still
            # inside this exact window and the slot has no prior submission.
            # The deadline remains fixed; this never makes the window OPEN again.
            offline_reconcile = bool(
                allow_offline_reconcile
                and act.get("type") == "HOURLY"
                and act["status"] in ("MISSED", "CLOSED", "REJECTED_LATE")
                and act.get("attendance_status") == "ABSENT"
                and not act.get("submitted_at")
                and not act.get("submission_id")
                and w_start <= sub_time < w_end
                and sub_time <= now + timedelta(seconds=60)
            )
            if act["status"] in ("MISSED", "CLOSED", "REJECTED_LATE") and not offline_reconcile:
                return {
                    "allowed": False,
                    "status": "REJECTED_LATE",
                    "activity_id": activity_id,
                    "submission_time": dt_to_iso(sub_time),
                    "window_start": dt_to_iso(w_start),
                    "window_end": dt_to_iso(w_end),
                    "points": 0,
                    "message": "Activity window has already closed and cannot be reopened.",
                }

            if not record_submission:
                return {"allowed": True, "status": "OPEN", "activity_id": activity_id, "points": 0}

            # 3. Valid On-Time Submission (Preserve original submission timestamp even if AI finishes later)
            next_status = "VALIDATING" if begin_ai_validation else "SUBMITTED"
            sub_iso_str = dt_to_iso(sub_time)
            details = {}
            try:
                details = json.loads(act.get("details_json") or "{}")
            except Exception:
                pass
            details["on_time_submission"] = True
            details["preserved_submitted_at"] = sub_iso_str
            details["ai_validation_state"] = "PENDING" if begin_ai_validation else "SUBMITTED"
            if offline_reconcile:
                details["offline_reconciled"] = True
                details["offline_reconciled_at"] = dt_to_iso(now)
                details["offline_reconciled_from_status"] = act["status"]
            if activity_category:
                details["activity_category"] = activity_category
            if source_device_id:
                details["source_device_id"] = source_device_id
            evidence_ids = [evidence_id] if evidence_id else []

            update = conn.execute(
                """
                UPDATE scheduled_activities
                SET status = ?, submitted_at = ?, points = 0, details_json = ?,
                    attendance_status = CASE WHEN type = 'HOURLY' THEN 'PRESENT' ELSE attendance_status END,
                    validation_status = CASE WHEN type = 'HOURLY' THEN ? ELSE validation_status END,
                    submission_id = COALESCE(?, submission_id),
                    description = COALESCE(?, description),
                    evidence_ids_json = CASE WHEN ? IS NOT NULL THEN ? ELSE evidence_ids_json END
                WHERE activity_id = ? AND (
                    status NOT IN
                        ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED', 'REJECTED', 'MISSED', 'CLOSED', 'REJECTED_LATE')
                    OR (? = 1 AND type = 'HOURLY'
                        AND status IN ('MISSED', 'CLOSED', 'REJECTED_LATE')
                        AND attendance_status = 'ABSENT'
                        AND submitted_at IS NULL AND submission_id IS NULL)
                )
                """,
                (
                    next_status, sub_iso_str, json.dumps(details),
                    "PROCESSING" if begin_ai_validation else "REVIEW_REQUIRED",
                    submission_id, description, evidence_id,
                    json.dumps(evidence_ids), activity_id, 1 if offline_reconcile else 0,
                ),
            )
            if update.rowcount != 1:
                return {
                    "allowed": False,
                    "status": "DUPLICATE",
                    "activity_id": activity_id,
                    "submission_time": sub_iso_str,
                    "window_start": dt_to_iso(w_start),
                    "window_end": dt_to_iso(w_end),
                    "points": 0,
                    "message": "This activity was already submitted or closed.",
                }
            return {
                "allowed": True,
                "status": next_status,
                "on_time": True,
                "attendance_status": "PRESENT" if act["type"] == "HOURLY" else None,
                "validation_status": "PROCESSING" if begin_ai_validation else "REVIEW_REQUIRED",
                "activity_id": activity_id,
                "submission_time": sub_iso_str,
                "window_start": dt_to_iso(w_start),
                "window_end": dt_to_iso(w_end),
                "points": 0,
                "reconciled_offline": offline_reconcile,
                "prompt_next_day_schedule": act["type"] == "WAKE_UP",
                "message": "Submission received inside the valid window. Points remain at 0 until proof is reviewed.",
            }

    def record_activity_submission(
        self,
        activity_id: str,
        sub_time: Optional[datetime] = None,
        submission_iso: Optional[str] = None,
        begin_ai_validation: bool = True,
        submission_id: Optional[str] = None,
        description: Optional[str] = None,
        evidence_id: Optional[str] = None,
        activity_category: Optional[str] = None,
        source_device_id: Optional[str] = None,
        allow_offline_reconcile: bool = False,
    ) -> Dict[str, Any]:
        """Records an activity submission with timing verification."""
        iso_val = submission_iso or (dt_to_iso(sub_time) if sub_time else None)
        return self.evaluate_submission_timing(
            activity_id=activity_id,
            submission_iso=iso_val,
            begin_ai_validation=begin_ai_validation,
            submission_id=submission_id,
            description=description,
            evidence_id=evidence_id,
            activity_category=activity_category,
            source_device_id=source_device_id,
            allow_offline_reconcile=allow_offline_reconcile,
        )

    def record_activity_validation_status(
        self,
        activity_id: str,
        validation_status: str,
        review_details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Updates evidence review independently while preserving timely attendance."""
        allowed = {
            "PROCESSING", "PROVISIONAL_APPROVE", "NEEDS_REVIEW",
            "PROVISIONAL_REJECT", "VERIFIED", "REJECTED", "ERROR",
            "REVIEW_REQUIRED", "NOT_SUBMITTED",
        }
        normalized = str(validation_status or "ERROR").upper()
        if normalized not in allowed:
            normalized = "ERROR"
        if not self.db:
            return {"activity_id": activity_id, "attendance_status": "PRESENT", "validation_status": normalized}

        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT type, attendance_status, details_json FROM scheduled_activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
            if not row:
                return {"success": False, "activity_id": activity_id, "error": "Activity not found"}
            details = {}
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                pass
            details["validation_status"] = normalized
            details["ai_validation_state"] = normalized
            if review_details:
                details["ai_review"] = review_details
            conn.execute(
                """
                UPDATE scheduled_activities
                SET validation_status = ?, details_json = ?,
                    status = CASE
                        WHEN type = 'HOURLY' AND status IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL')
                             AND ? IN ('VERIFIED', 'PROVISIONAL_APPROVE')
                            THEN CASE WHEN ? = 'VERIFIED' THEN 'VERIFIED' ELSE 'AI_PROVISIONAL' END
                        WHEN type = 'HOURLY' AND status IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL')
                             AND ? IN ('REJECTED', 'PROVISIONAL_REJECT')
                            THEN 'REJECTED'
                        ELSE status
                    END
                WHERE activity_id = ?
                """,
                (normalized, json.dumps(details), normalized, normalized, normalized, activity_id),
            )
            attendance = row["attendance_status"] or ("PRESENT" if row["type"] == "HOURLY" else "UPCOMING")
        return {"success": True, "activity_id": activity_id, "attendance_status": attendance, "validation_status": normalized}

    def attach_activity_evidence(self, activity_id: str, evidence_id: str) -> None:
        """Adds an evidence reference to an already accepted submission."""
        if not self.db or not evidence_id:
            return
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT evidence_ids_json, details_json FROM scheduled_activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
            if not row:
                return
            try:
                evidence_ids = json.loads(row["evidence_ids_json"] or "[]")
            except Exception:
                evidence_ids = []
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                details = {}
            details["evidence_id"] = evidence_id
            conn.execute(
                "UPDATE scheduled_activities SET evidence_ids_json = ?, details_json = ? WHERE activity_id = ?",
                (json.dumps(evidence_ids), json.dumps(details), activity_id),
            )

    def get_hourly_attendance_records(
        self,
        date_str: str,
        account_id: str = "user_lok",
        reference_now: Optional[datetime] = None,
        is_dev_mode: bool = False,
    ) -> List[Dict[str, Any]]:
        """Returns one timeline row per persisted hourly schedule slot."""
        activities = self.get_activities_for_date(date_str, account_id, reference_now=reference_now)
        if not self.db:
            return []
        with self.db._connect() as conn:
            record_rows = conn.execute(
                "SELECT hour_slot, description, evidence_id, submitted_at, device_id FROM hourly_records WHERE date = ? AND is_dev_mode = ?",
                (date_str, 1 if is_dev_mode else 0),
            ).fetchall()
        by_slot = {str(r["hour_slot"] or ""): dict(r) for r in record_rows}
        result: List[Dict[str, Any]] = []
        for act in activities:
            if act.get("type") != "HOURLY":
                continue
            details = {}
            try:
                details = json.loads(act.get("details_json") or "{}")
            except Exception:
                pass
            slot = details.get("hour_slot") or act.get("title", "Hourly checkpoint")
            legacy = by_slot.get(slot, {})
            evidence_ids = []
            try:
                evidence_ids = json.loads(act.get("evidence_ids_json") or "[]")
            except Exception:
                pass
            if not evidence_ids and legacy.get("evidence_id"):
                evidence_ids = [legacy["evidence_id"]]
            attendance = act.get("attendance_status") or (
                "PRESENT" if act.get("status") in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED")
                else "ABSENT" if act.get("status") in ("MISSED", "CLOSED", "REJECTED_LATE")
                else "OPEN" if act.get("status") == "OPEN" else "UPCOMING"
            )
            validation = act.get("validation_status") or "NOT_SUBMITTED"
            result.append({
                "activity_id": act["activity_id"],
                "account_id": act.get("account_id", account_id),
                "schedule_id": act.get("schedule_id", ""),
                "schedule_version": int(act.get("schedule_version") or 1),
                "date": act.get("date", date_str),
                "slot": slot,
                "checkpoint_at": act.get("scheduled_time"),
                "window_start": act.get("window_start"),
                "window_end": act.get("window_end"),
                "activity": details.get("activity_description") or act.get("title", "Hourly checkpoint"),
                "description": act.get("description") or legacy.get("description") or "",
                "submitted_at": act.get("submitted_at") or legacy.get("submitted_at"),
                "evidence_ids": evidence_ids,
                "attendance_status": attendance,
                "validation_status": validation,
                "admin_decision": act.get("admin_decision") or "",
                "points": int(act.get("points") or 0) if validation == "VERIFIED" else 0,
                "configured_points": int(self._get_task_policy_config().get("hourly_points", 8)),
                "submission_id": act.get("submission_id"),
                "lifecycle_status": act.get("status", "SCHEDULED"),
                "audit": {
                    "opened_at": act.get("opened_at"),
                    "closed_at": act.get("closed_at"),
                    "source_device": details.get("source_device_id") or legacy.get("device_id") or "",
                    "schedule_version": int(act.get("schedule_version") or 1),
                },
            })
        return result

    def record_activity_verification(
        self,
        activity_id: str,
        is_verified: bool,
        awarded_points: Optional[int] = None,
        verification_completed_iso: Optional[str] = None,
        evidence_id: Optional[str] = None,
        provisional: bool = False,
    ) -> Dict[str, Any]:
        """
        Finalizes activity state to VERIFIED or REJECTED based on AI validation.
        Section 3: If proof was submitted within the window (submitted_at < window_end)
        but AI processing finishes afterward, retain the original on-time submission status
        and award points!
        """
        if not self.db:
            return {"status": "VERIFIED" if is_verified else "REJECTED"}

        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
            if not row:
                return {
                    "activity_id": activity_id,
                    "status": "ERROR",
                    "points": 0,
                    "verified": False,
                    "message": "Activity not found.",
                }

            act_type = str(row["type"] or "").upper()
            details = {}
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                details = {}
            evidence_ids = []
            try:
                evidence_ids = json.loads(row["evidence_ids_json"] or "[]")
            except Exception:
                evidence_ids = []

            resolved_evidence_id = evidence_id or details.get("evidence_id") or (evidence_ids[0] if evidence_ids else None)

            proof_approved = False
            if is_verified:
                if resolved_evidence_id:
                    proof = conn.execute(
                        """
                        SELECT pr.status, ef.file_path, ef.is_deleted
                        FROM proof_reviews pr
                        LEFT JOIN evidence_files ef ON ef.evidence_id = pr.evidence_id
                        WHERE pr.evidence_id = ?
                        """,
                        (resolved_evidence_id,),
                    ).fetchone()
                    proof_approved = bool(
                        proof
                        and proof["status"] == "APPROVED"
                        and (proof["is_deleted"] is None or proof["is_deleted"] == 0)
                        and (
                            (proof["file_path"] and os.path.isfile(proof["file_path"]) and os.path.getsize(proof["file_path"]) > 0)
                            or (act_type == "HOURLY")
                        )
                    )
                    accepted_verification = proof_approved
                else:
                    if act_type == "HOURLY":
                        proof_approved = True
                        accepted_verification = True
                    else:
                        proof_approved = False
                        accepted_verification = False
            else:
                proof_approved = False
                accepted_verification = False

            preserved_sub = row["submitted_at"]
            default_pts = int(row["points"] or 0)
            valid_submission = False
            try:
                valid_submission = bool(
                    proof_approved
                    or (
                        preserved_sub
                        and iso_to_dt(row["window_start"]) <= iso_to_dt(preserved_sub) <= (iso_to_dt(row["window_end"]) + timedelta(minutes=15))
                    )
                    or (act_type == "HOURLY" and (row["status"] in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "AI_ANALYZING", "SCHEDULED", "PENDING_REVIEW", "UNDER_REVIEW", "AI_QUEUED") or bool(preserved_sub)))
                )
            except (ValueError, TypeError):
                valid_submission = bool(proof_approved or (act_type == "HOURLY" and bool(preserved_sub)))

            accepted_verification = bool(accepted_verification and valid_submission)
            if is_verified and not valid_submission:
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "No valid on-time submission exists; activity state was not changed.",
                }
            if not is_verified and row["status"] not in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "AI_ANALYZING", "SCHEDULED", "PENDING_REVIEW", "UNDER_REVIEW", "AI_QUEUED"):
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "Activity is not awaiting review; state was not changed.",
                }

            status = (
                "VERIFIED" if accepted_verification else
                "AI_PROVISIONAL" if is_verified and provisional else
                "SUBMITTED" if is_verified else
                "REJECTED"
            )
            completed_iso = verification_completed_iso or dt_to_iso(now_ist())
            pts = awarded_points if (accepted_verification and awarded_points is not None) else (default_pts if accepted_verification else 0)
            details["ai_validation_completed_at"] = completed_iso
            details["ai_verified"] = bool(is_verified)
            details["evidence_id"] = resolved_evidence_id or details.get("evidence_id")
            if resolved_evidence_id and resolved_evidence_id not in evidence_ids:
                evidence_ids.append(resolved_evidence_id)
            details["points_require_approved_proof"] = True
            if provisional and is_verified:
                details["ai_recommendation_only"] = True
            if preserved_sub:
                details["preserved_submitted_at"] = preserved_sub
                details["on_time_retained"] = True

            conn.execute(
                """
                UPDATE scheduled_activities
                SET status = ?, points = ?, details_json = ?,
                    attendance_status = CASE WHEN type = 'HOURLY' AND ? = 1 THEN 'PRESENT' ELSE attendance_status END,
                    validation_status = CASE WHEN type = 'HOURLY' THEN ? ELSE validation_status END,
                    admin_decision = CASE WHEN ? = 1 THEN 'APPROVED' WHEN ? = 1 THEN 'REJECTED' ELSE admin_decision END,
                    evidence_ids_json = ?
                WHERE activity_id = ?
                """,
                (
                    status, pts, json.dumps(details),
                    1 if valid_submission else 0,
                    "VERIFIED" if accepted_verification else "PROVISIONAL_APPROVE" if provisional and is_verified else "REVIEW_REQUIRED" if is_verified else "REJECTED",
                    1 if accepted_verification else 0,
                    1 if (not is_verified and not provisional) else 0,
                    json.dumps(evidence_ids), activity_id,
                ),
            )
        return {
            "activity_id": activity_id,
            "status": status,
            "points": pts,
            "evidence_approved": proof_approved,
            "verified": accepted_verification,
            "submitted_at": preserved_sub,
            "verification_completed_at": completed_iso,
            "on_time_retained": bool(preserved_sub),
        }

    def get_current_countdown_state(
        self,
        date_str: str,
        account_id: str = "user_lok",
        reference_now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calculates the live countdown for the currently open or next upcoming activity.
        Used by the UI countdown component and survives app/device restarts.
        """
        now = reference_now or now_ist()
        activities = self.get_activities_for_date(date_str, account_id, reference_now=now)

        open_act = None
        upcoming_act = None
        blocked_act = None

        for act in activities:
            w_start = iso_to_dt(act["window_start"])
            w_end = iso_to_dt(act["window_end"])

            if act["status"] == "OPEN" and w_start <= now < w_end:
                open_act = act
                break
            elif act["status"] == "BLOCKED_WAKEUP" and w_start <= now < w_end:
                blocked_act = act
            elif w_start > now and act["status"] in ("SCHEDULED", "UPCOMING", "NOTIFIED", "BLOCKED_WAKEUP"):
                if upcoming_act is None:
                    upcoming_act = act

        if open_act:
            w_end = iso_to_dt(open_act["window_end"])
            rem_sec = max(0, int((w_end - now).total_seconds()))
            is_warn = rem_sec <= 120  # Under 2 minutes
            return {
                "has_active_window": True,
                "activity_id": open_act["activity_id"],
                "type": open_act["type"],
                "title": open_act.get("title", "Active Activity"),
                "state": "OPEN",
                "submission_eligible": open_act.get("submission_eligible", True),
                "seconds_remaining": rem_sec,
                "is_warning": is_warn,
                "window_start": open_act["window_start"],
                "window_end": open_act["window_end"],
                "points": open_act["points"],
            }

        if blocked_act:
            return {"has_active_window": False, "activity_id": blocked_act["activity_id"], "type": "HOURLY",
                    "title": blocked_act.get("title"), "state": "BLOCKED_WAKEUP",
                    "seconds_remaining": max(0, int((iso_to_dt(blocked_act["window_end"]) - now).total_seconds())),
                    "seconds_until_open": 0, "window_start": blocked_act["window_start"], "window_end": blocked_act["window_end"],
                    "points": 0, "is_warning": False}

        if upcoming_act:
            w_start = iso_to_dt(upcoming_act["window_start"])
            until_sec = max(0, int((w_start - now).total_seconds()))
            return {
                "has_active_window": False,
                "activity_id": upcoming_act["activity_id"],
                "type": upcoming_act["type"],
                "title": upcoming_act.get("title", "Upcoming Activity"),
                "state": upcoming_act["status"],
                "seconds_until_open": until_sec,
                "is_warning": False,
                "window_start": upcoming_act["window_start"],
                "window_end": upcoming_act["window_end"],
                "points": upcoming_act["points"],
            }

        return {
            "has_active_window": False,
            "activity_id": None,
            "state": "ALL_CLOSED",
            "seconds_remaining": 0,
            "is_warning": False,
        }

    def get_pending_notifications(
        self,
        date_str: str,
        account_id: str = "user_lok",
        reference_now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Section 13: Generates deduplicated notifications from the saved schedule:
          - T_open - 1m: Reminder that the window opens in one minute.
          - T_open: Window opens.
          - T_close - 1m: Reminder that one minute remains.
          - T_close: Window closes.
        Each notification is sent once per scheduled event (tracked in notification_history).
        """
        if not self.db:
            return []

        now = reference_now or now_ist()
        activities = self.ensure_activities_in_db(date_str, account_id, reference_now=now)
        # Reuse this evaluated batch for the Windows shared reminder presenter.
        self.last_notification_activities = activities
        pending: List[Dict[str, Any]] = []

        hourly_to_open: List[Dict[str, Any]] = []
        hourly_reminders: List[Dict[str, Any]] = []
        hourly_to_close: List[Dict[str, Any]] = []
        hourly_to_expire: List[str] = []

        with self.db._connect() as conn:
            for act in activities:
                if not act.get("submission_eligible", True) or act.get("status") in ("BLOCKED_WAKEUP", "SKIPPED_PRE_GATE", "EXCUSED", "CANCELLED", "EXPIRED"):
                    continue
                aid = act["activity_id"]
                title = act.get("title") or act["type"]
                w_start = iso_to_dt(act["window_start"])
                w_end = iso_to_dt(act["window_end"])
                w_notif = iso_to_dt(act["notification_time"])
                w_warn1m = w_end - timedelta(minutes=1)

                atype = act.get("type", "")
                details = {}
                try:
                    details = json.loads(act.get("details_json") or "{}")
                except Exception:
                    pass

                # A duration-only reschedule retains the activity ID but has a
                # new proof window. Deduplicate by the window, not just the ID.
                reminder_aid=aid
                if atype=='BATH' and details.get('attendance_reservation_policy')=='BATH_ACTIVITY_WINDOW_OVERLAP_V2':
                    token=hashlib.sha256((act['window_start']+'|'+act['window_end']).encode()).hexdigest()[:12]
                    reminder_aid=f'{aid}-{token}'

                notif_title = (
                    "STRICTRECORDS · WAKE-UP VERIFICATION" if atype == "WAKE_UP"
                    else ("STRICTRECORDS · HOURLY CHECK-IN" if atype == "HOURLY" else "StrictRecords")
                )
                explicit_task_type = (
                    "WAKE_UP" if atype == "WAKE_UP"
                    else ("HOURLY_CHECK_IN" if atype == "HOURLY" else atype)
                )

                # 1. 1-minute pre-opening notification
                if w_notif <= now < w_start:
                    key = f"{reminder_aid}-OPENING"
                    if not conn.execute("SELECT 1 FROM notification_history WHERE notification_key = ?", (key,)).fetchone():
                        if atype == "HOURLY":
                            msg = f"Your hourly proof window opens in one minute. ({title})"
                        elif atype == "WAKE_UP":
                            msg = "Wake-up verification window opens in one minute."
                        else:
                            msg = f"Your next activity opens in one minute. ({title})"
                        notification = {
                            "notification_key": key,
                            "event_id": key,
                            "activity_id": aid,
                            "task_type": explicit_task_type,
                            "event_type": "PRE_OPENING",
                            "category": "TASKS",
                            "scheduled_for": dt_to_iso(w_notif),
                            "message": msg,
                            "title": notif_title,
                            "schedule_version": int(act.get("schedule_version") or 1),
                            "window_start": dt_to_iso(w_start),
                            "window_end": dt_to_iso(w_end),
                            "checkpoint_at": act.get("scheduled_time"),
                            "slot": details.get("hour_slot") or title,
                        }
                        pending.append(notification)
                        if atype == "HOURLY":
                            hourly_reminders.append(notification)

                # 2. Opening notification (Section 1C & 14)
                if w_start <= now < w_end and act["status"] == "OPEN":
                    key = f"{reminder_aid}-OPENED"
                    slot = details.get("hour_slot") or title
                    if atype == "HOURLY":
                        hourly_to_open.append({
                            "activity_id": aid,
                            "task_type": "HOURLY_CHECK_IN",
                            "schedule_version": int(act.get("schedule_version") or 1),
                            "event_id": key,
                            "slot": slot,
                            "title": title,
                            "window_start": dt_to_iso(w_start),
                            "window_end": dt_to_iso(w_end),
                            "details": dict(act),
                            "attendance_status": act.get("attendance_status", "OPEN"),
                            "validation_status": act.get("validation_status", "NOT_SUBMITTED"),
                        })
                    if not conn.execute("SELECT 1 FROM notification_history WHERE notification_key = ?", (key,)).fetchone():
                        if atype == "HOURLY":
                            w_end_hm = TimeWindowEngine.format_time_12h(w_end.strftime("%H:%M"))
                            msg = f"Your hourly proof window is open. Submit before {w_end_hm}."
                        elif atype == "WAKE_UP":
                            msg = "Wake-up verification window is open (7:00 AM–9:30 AM). Complete camera liveness, earbuds check, and music track."
                        else:
                            msg = f"Your activity window is now open. ({title})"
                        pending.append({
                            "notification_key": key,
                            "event_id": key,
                            "activity_id": aid,
                            "task_type": explicit_task_type,
                            "event_type": "OPENED",
                            "category": "TASKS",
                            "scheduled_for": dt_to_iso(w_start),
                            "message": msg,
                            "title": notif_title,
                            "schedule_version": int(act.get("schedule_version") or 1),
                            "window_start": dt_to_iso(w_start),
                            "window_end": dt_to_iso(w_end),
                            "checkpoint_at": act.get("scheduled_time"),
                            "slot": slot,
                        })

                # 3. 1 minute remaining warning (Section 14)
                if w_warn1m <= now < w_end and act["status"] == "OPEN":
                    key = f"{reminder_aid}-WARN1M"
                    if not conn.execute("SELECT 1 FROM notification_history WHERE notification_key = ?", (key,)).fetchone():
                        if atype == "HOURLY":
                            slot = details.get("hour_slot") or title
                            msg = f"1 minute remaining to submit hourly proof for {slot}."
                        elif atype == "WAKE_UP":
                            msg = "1 minute remaining in wake-up verification window. Closes at 09:30 AM."
                        else:
                            msg = f"1 minute remaining in activity window. ({title})"
                        notification = {
                            "notification_key": key,
                            "event_id": key,
                            "activity_id": aid,
                            "task_type": explicit_task_type,
                            "event_type": "WARNING_1M",
                            "category": "TASKS",
                            "scheduled_for": dt_to_iso(w_warn1m),
                            "message": msg,
                            "title": notif_title,
                            "schedule_version": int(act.get("schedule_version") or 1),
                            "window_start": dt_to_iso(w_start),
                            "window_end": dt_to_iso(w_end),
                            "checkpoint_at": act.get("scheduled_time"),
                            "slot": details.get("hour_slot") or title,
                        }
                        pending.append(notification)
                        if atype == "HOURLY":
                            hourly_reminders.append(notification)

                # 4. Activity closed notification
                hourly_status = str(act.get("status", ""))
                hourly_closed = hourly_status in (
                    "MISSED", "CLOSED", "REJECTED_LATE", "SUBMITTED", "VALIDATING",
                    "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED",
                )
                if now >= w_end and (
                    hourly_closed if atype == "HOURLY" else hourly_status in ("MISSED", "CLOSED", "REJECTED_LATE")
                ):
                    key = f"{reminder_aid}-CLOSED"
                    if not conn.execute("SELECT 1 FROM notification_history WHERE notification_key = ?", (key,)).fetchone():
                        if atype == "HOURLY":
                            attendance = act.get("attendance_status") or (
                                "PRESENT" if hourly_status in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED")
                                else "ABSENT"
                            )
                            validation = act.get("validation_status") or ("PROCESSING" if attendance == "PRESENT" else "NOT_SUBMITTED")
                            submitted_label = act.get("submitted_at") or ""
                            if attendance == "PRESENT":
                                submitted_text = TimeWindowEngine.format_time_12h(iso_to_dt(submitted_label).strftime("%H:%M")) if submitted_label else "On time"
                                msg = f"Hourly check-in recorded. PRESENT · Submitted {submitted_text} · AI review {validation.replace('_', ' ').title()}."
                            else:
                                msg = f"Hourly check-in closed. ABSENT · Window {TimeWindowEngine.format_time_12h(w_start.strftime('%H:%M'))}–{TimeWindowEngine.format_time_12h(w_end.strftime('%H:%M'))}. No accepted submission was received before the deadline."
                            hourly_to_close.append({
                                "activity_id": aid,
                                "task_type": "HOURLY_CHECK_IN",
                                "event_id": key,
                                "schedule_version": int(act.get("schedule_version") or 1),
                                "slot": details.get("hour_slot") or title,
                                "title": title,
                                "window_start": dt_to_iso(w_start),
                                "window_end": dt_to_iso(w_end),
                                "attendance_status": attendance,
                                "validation_status": validation,
                                "submitted_at": submitted_label,
                                "details": dict(act),
                            })
                            if attendance == "ABSENT":
                                hourly_to_expire.append(aid)
                        elif atype == "WAKE_UP":
                            msg = "Wake-up window closed at 09:30 AM. Activity marked missed."
                        else:
                            msg = f"The activity window has closed. Moving to next scheduled task. ({title})"
                        pending.append({
                            "notification_key": key,
                            "event_id": key,
                            "activity_id": aid,
                            "task_type": explicit_task_type,
                            "event_type": "CLOSED",
                            "category": "TASKS",
                            "scheduled_for": dt_to_iso(w_end),
                            "message": msg,
                            "title": notif_title,
                            "schedule_version": int(act.get("schedule_version") or 1),
                            "window_start": dt_to_iso(w_start),
                            "window_end": dt_to_iso(w_end),
                            "checkpoint_at": act.get("scheduled_time"),
                            "slot": details.get("hour_slot") or title,
                            "attendance_status": act.get("attendance_status", "ABSENT"),
                            "validation_status": act.get("validation_status", "NOT_SUBMITTED"),
                            "submitted_at": act.get("submitted_at"),
                        })

            # Atomically claim each logical event. Concurrent polls can return it
            # to at most one delivery worker.
            delivered: List[Dict[str, Any]] = []
            for p in pending:
                claimed = conn.execute(
                    "INSERT OR IGNORE INTO notification_history (notification_key, activity_id, event_type, sent_at) VALUES (?, ?, ?, ?)",
                    (p["notification_key"], p["activity_id"], p["event_type"], dt_to_iso(now)),
                )
                if claimed.rowcount != 1:
                    continue
                delivered.append(p)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_center (
                        notification_key, title, message, category, activity_id, event_type,
                        source, timestamp, is_read, is_dismissed
                    ) VALUES (?, ?, ?, ?, ?, ?, 'STRICTRECORDS', ?, 0, 0)
                    """,
                    (p["notification_key"], "StrictRecords", p["message"], p.get("category", "TASKS"), p["activity_id"], p["event_type"], dt_to_iso(now)),
                )
            pending = delivered

        # Trigger SubmissionWindowManager outside DB lock
        if hourly_to_open or hourly_to_expire or hourly_to_close or hourly_reminders:
            try:
                from windows.submission_window_manager import SubmissionWindowManager
                from core.event_bus import EventBus
                bus = EventBus.get_instance()
                swm = SubmissionWindowManager.get_instance(self.db, bus)
                for reminder in hourly_reminders:
                    if reminder.get("event_type") == "WARNING_1M":
                        if swm.state.value == "OPEN" and swm.active_activity_id == reminder.get("activity_id"):
                            swm.restore_window(force_focus=False)
                    bus.publish(
                        "HOURLY_REMINDER",
                        {
                            **reminder,
                            "event_type": reminder.get("event_type"),
                            "countdown_seconds": max(0, int((iso_to_dt(reminder["window_end"]) - now).total_seconds())),
                            "action": "OPEN_SUBMISSION",
                        },
                        event_id=reminder.get("event_id"),
                    )
                for item in hourly_to_open:
                    force_f = bool(item.get("force_focus", False))
                    if force_f or not swm.has_opened_activity(item["activity_id"], item.get("schedule_version", 1)):
                        swm.open_submission_window(
                            activity_id=item["activity_id"],
                            slot=item["slot"],
                            title=item["title"],
                            window_start=item["window_start"],
                            window_end=item["window_end"],
                            details=item["details"],
                            force_focus=force_f,
                            schedule_version=item.get("schedule_version", 1),
                            event_id=item.get("event_id"),
                            reference_now=now,
                        )
                for item in hourly_to_close:
                    if item["attendance_status"] == "ABSENT":
                        close_result = swm.expire_submission_window(
                            item["activity_id"],
                            schedule_version=item["schedule_version"],
                            event_id=item["event_id"],
                            attendance_status=item["attendance_status"],
                            validation_status=item["validation_status"],
                            slot=item["slot"],
                            window_start=item["window_start"],
                            window_end=item["window_end"],
                            submitted_at=item["submitted_at"],
                        )
                        if close_result.get("action") != "EXPIRED":
                            bus.publish(
                                "HOURLY_WINDOW_CLOSED",
                                {
                                    "activity_id": item["activity_id"],
                                    "schedule_version": item["schedule_version"],
                                    "event_id": item["event_id"],
                                    "slot": item["slot"],
                                    "title": item["title"],
                                    "window_start": item["window_start"],
                                    "window_end": item["window_end"],
                                    "attendance_status": item["attendance_status"],
                                    "validation_status": item["validation_status"],
                                    "submitted_at": item["submitted_at"],
                                    "status": item["attendance_status"],
                                },
                                event_id=item["event_id"],
                            )
                    else:
                        bus.publish(
                            "HOURLY_WINDOW_CLOSED",
                            {
                                "activity_id": item["activity_id"],
                                "schedule_version": item["schedule_version"],
                                "event_id": item["event_id"],
                                "slot": item["slot"],
                                "title": item["title"],
                                "window_start": item["window_start"],
                                "window_end": item["window_end"],
                                "attendance_status": item["attendance_status"],
                                "validation_status": item["validation_status"],
                                "submitted_at": item["submitted_at"],
                                "status": item["attendance_status"],
                            },
                            event_id=item["event_id"],
                        )
                for exp_aid in hourly_to_expire:
                    if not any(item.get("activity_id") == exp_aid for item in hourly_to_close):
                        swm.expire_submission_window(exp_aid, event_id=f"{exp_aid}-CLOSED")
            except Exception:
                pass

        return pending
