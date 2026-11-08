"""Read-only presentation plan using the scheduling engine's evaluated occurrences."""

from datetime import timedelta
import hashlib
from core.time_window_engine import iso_to_dt


EXCLUDED = {"EXCUSED", "CANCELLED", "EXPIRED", "SKIPPED_PRE_GATE", "BLOCKED_WAKEUP"}
PENDING = {"SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "PENDING_SYNC", "RECEIVED", "UNDER_REVIEW"}
FINAL = {"VERIFIED", "APPROVED", "COMPLETED", "MISSED", "CLOSED", "REJECTED_LATE", "SKIPPED"}


def build_reminder_plan(activities, now):
    """No schedule, proof, score or ledger writes. Returns rows and next absolute boundary."""
    rows, boundaries = [], []
    for activity in activities:
        task = dict(activity)
        status = str(task.get("status", "")).upper()
        blocked_hourly = task.get('type') == 'HOURLY' and task.get('eligibility_status') == 'BLOCKED_WAKEUP'
        if (status in EXCLUDED and not blocked_hourly) or (not task.get("submission_eligible", True) and not blocked_hourly):
            continue
        try:
            start, end = iso_to_dt(task["window_start"]), iso_to_dt(task["window_end"])
            if end <= start:
                continue
            reminder = iso_to_dt(task.get("notification_time") or task["window_start"])
        except (KeyError, TypeError, ValueError):
            continue
        # Reminder lead is presentation only; eligibility still uses start/end.
        if task.get("type") in {"GYM", "TRAVEL"}:
            reminder = start - timedelta(minutes=5)
        elif task.get("type") == "HOURLY":
            reminder = start - timedelta(minutes=1)
        for at in (reminder, start, end - timedelta(minutes=1), end):
            if at > now:
                boundaries.append(at)
        if now < reminder or (now >= end and status not in PENDING):
            continue
        phase = "REVIEW_PENDING" if status in PENDING else (
            status if status in FINAL else "REJECTED" if status == "REJECTED" else "OPEN" if start <= now < end else "UPCOMING")
        if blocked_hourly and phase == 'OPEN':
            phase = 'BLOCKED_WAKEUP'
            task['eligibility_message'] = 'Complete the required wake-up checks and proof approval before submitting hourly attendance. After the deadline, request administrator-authorized recovery.'
        material = f"{task['activity_id']}|{task.get('schedule_version') or 1}|{task['window_start']}|{task['window_end']}"
        task["reminder_identity"] = hashlib.sha256(material.encode()).hexdigest()[:24]
        task["reminder_phase"] = phase
        task["can_submit"] = phase == "OPEN" and start <= now < end
        rows.append(task)
    rows.sort(key=lambda task: (not task["can_submit"], task["window_end"], task["activity_id"]))
    return rows, min(boundaries) if boundaries else None
