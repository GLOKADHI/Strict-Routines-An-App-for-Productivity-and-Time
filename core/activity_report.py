"""Build privacy-scoped daily activity reports from the persisted schedule and review ledger."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.account_scope import filter_account_score_events


_VERIFIED = {"VERIFIED", "APPROVED", "COMPLETED"}
_AI_PENDING = {"PENDING", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "AI_PROCESSING"}
_ADMIN_PENDING = {"UNDER_REVIEW", "NEEDS_REVIEW", "DUPLICATE_REVIEW", "ADMIN_REVIEW", "PROVISIONAL_REJECT"}
_REJECTED = {"REJECTED", "REJECTED_LATE", "PROVISIONAL_REJECT"}
_MISSED = {"MISSED", "CLOSED", "EXPIRED"}
_NOT_STARTED = {"SCHEDULED", "UPCOMING", "NOTIFIED", "OPEN"}


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bucket_for(activity: Dict[str, Any]) -> str:
    status = str(activity.get("effective_status") or activity.get("status") or "UNKNOWN").upper()
    if status in _VERIFIED:
        return "verified"
    if status in _ADMIN_PENDING:
        return "awaiting_admin"
    if status in {"AI_FAILED", "PROCESSING_ERROR", "RETRYABLE_ERROR", "FAILED_RETRYABLE", "UNCONFIGURED"}:
        return "ai_failed"
    if status in _AI_PENDING:
        return "awaiting_ai"
    if status in _REJECTED:
        return "rejected" if activity.get("submitted_at") or activity.get("has_submission") else "missed"
    if status in _MISSED:
        return "missed"
    if status in _NOT_STARTED:
        return "upcoming"
    return "unclassified"


def _time_bucket(value: Any) -> str:
    raw = str(value or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        hour = dt.hour
    except (ValueError, TypeError):
        match = re.search(r"(?:T|\s)(\d{1,2}):\d{2}", raw)
        hour = int(match.group(1)) if match else 12
    if 5 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 21:
        return "Evening"
    return "Night"


def _normalized_code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _matches_activity(
    review: Dict[str, Any],
    activity: Dict[str, Any],
    details: Dict[str, Any],
    exact_title_allowed: bool = True,
) -> bool:
    signals = _json_object(review.get("signals_json"))
    explicit_activity_id = (
        signals.get("activity_id")
        or signals.get("scheduled_activity_id")
        or signals.get("activityId")
    )
    if explicit_activity_id:
        return str(explicit_activity_id) == str(activity.get("activity_id"))
    evidence_ids = {
        str(details.get("evidence_id") or ""),
        str(details.get("evidenceId") or ""),
        str(details.get("review_evidence_id") or ""),
    }
    if review.get("evidence_id") and review["evidence_id"] in evidence_ids:
        return True
    task_code = _normalized_code(review.get("task_code"))
    if task_code and task_code == _normalized_code(activity.get("type")):
        hour_slot = str(review.get("hour_slot") or "").strip()
        expected_slot = str(details.get("hour_slot") or details.get("hourSlot") or "").strip()
        if hour_slot and expected_slot:
            if _normalized_code(hour_slot) == _normalized_code(expected_slot):
                return True
        submitted = str(review.get("submitted_at") or "")
        try:
            submitted_at = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
            window_start = datetime.fromisoformat(str(activity.get("window_start") or "").replace("Z", "+00:00"))
            window_end = datetime.fromisoformat(str(activity.get("window_end") or "").replace("Z", "+00:00"))
            if window_start <= submitted_at < window_end:
                return True
        except (TypeError, ValueError):
            pass
    review_title = _normalized_code(review.get("task_title"))
    activity_title = _normalized_code(activity.get("title"))
    return bool(exact_title_allowed and review_title and activity_title and review_title == activity_title)


def _review_stage(review: Dict[str, Any]) -> str:
    status = str(review.get("status") or "PENDING").upper()
    signals = _json_object(review.get("signals_json"))
    decision = str(review.get("decision") or signals.get("decision") or "").upper()
    if status in _VERIFIED or decision in {"APPROVED", "AUTO_APPROVED", "VERIFIED"}:
        return "VERIFIED"
    if status in _REJECTED or decision in {"REJECTED", "PROVISIONAL_REJECT", "FLAGGED_DUPLICATE"}:
        return "REJECTED"
    if status in _ADMIN_PENDING or signals.get("admin_review_required") is True:
        return "ADMIN_REVIEW"
    if status in {"PROCESSING_ERROR", "AI_FAILED", "FAILED", "RETRYABLE_ERROR", "FAILED_RETRYABLE", "UNCONFIGURED"}:
        return "AI_FAILED"
    if status in _AI_PENDING or decision in {"PROVISIONAL_APPROVE", "NEEDS_REVIEW", "DUPLICATE_REVIEW"}:
        return "AI_REVIEW"
    return "UNKNOWN"


def build_daily_activity_report(
    db: Any,
    date: str,
    activities: Iterable[Dict[str, Any]],
    account_id: str = "user_lok",
    is_dev_mode: bool = False,
) -> Dict[str, Any]:
    """Serialize actual activity, review, and score-event records without evidence paths."""
    activity_rows = [dict(item) for item in activities]
    dev_flag = 1 if is_dev_mode else 0
    with db._connect() as conn:
        review_rows = conn.execute(
            "SELECT * FROM proof_reviews WHERE date = ? AND user_id = ? ORDER BY submitted_at ASC",
            (date, account_id),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM score_events WHERE date = ? AND is_dev_mode = ? ORDER BY timestamp ASC",
            (date, dev_flag),
        ).fetchall()
        event_rows = filter_account_score_events(conn, event_rows, account_id)
    reviews = [dict(row) for row in review_rows]
    events = [dict(row) for row in event_rows]

    summary = {
        "scheduled": len(activity_rows),
        "completed": 0,
        "verified": 0,
        "awaiting_ai": 0,
        "awaiting_admin": 0,
        "ai_failed": 0,
        "rejected": 0,
        "missed": 0,
        "upcoming": 0,
        "unclassified": 0,
    }
    ledger = db.get_point_ledger_summary(date=date, is_dev_mode=is_dev_mode, account_id=account_id)
    event_payloads: List[Dict[str, Any]] = []
    for event in events:
        audit = _json_object(event.get("audit_json"))
        event_payloads.append({
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "timestamp": event.get("timestamp"),
            "description": event.get("description"),
            "points": int(event.get("points") or 0),
            "related_task_id": event.get("related_task_id"),
            "evidence_id": event.get("evidence_id"),
            "ledger_state": audit.get("ledger_state"),
        })

    serialized: List[Dict[str, Any]] = []
    normalized_title_counts: Dict[str, int] = {}
    for item in activity_rows:
        title_key = _normalized_code(item.get("title"))
        if title_key:
            normalized_title_counts[title_key] = normalized_title_counts.get(title_key, 0) + 1
    for original in activity_rows:
        details = _json_object(original.get("details_json"))
        title_key = _normalized_code(original.get("title"))
        matched_reviews = [
            review for review in reviews
            if _matches_activity(
                review, original, details,
                exact_title_allowed=normalized_title_counts.get(title_key, 0) == 1,
            )
        ]
        # Respect the scheduler state unless the schedule row is still pre-submission and
        # a persisted proof-review record establishes that a submission arrived.
        persisted_status = str(original.get("status") or "UNKNOWN").upper()
        review_stage = _review_stage(matched_reviews[-1]) if matched_reviews else ""
        effective_status = persisted_status
        if matched_reviews and persisted_status not in _MISSED:
            review_status_override = {
                "VERIFIED": "VERIFIED",
                "REJECTED": "REJECTED",
                "ADMIN_REVIEW": "ADMIN_REVIEW",
                "AI_REVIEW": "SUBMITTED",
                "AI_FAILED": "AI_FAILED",
            }.get(review_stage)
            if review_status_override:
                effective_status = review_status_override
        elif not original.get("submitted_at") and effective_status not in _VERIFIED:
            w_start = original.get("window_start")
            w_end = original.get("window_end")
            if w_start and w_end:
                try:
                    from core.time_window_engine import iso_to_dt, now_ist
                    from core.window_state import window_state
                    start_dt = iso_to_dt(w_start)
                    end_dt = iso_to_dt(w_end)
                    now_val = now_ist()
                    eligible = bool(original.get("submission_eligible", True)) if original.get("submission_eligible") is not None else True
                    effective_status = window_state(
                        effective_status,
                        now_val,
                        start_dt,
                        end_dt,
                        eligible=eligible,
                    )
                except Exception:
                    pass

        has_submission = bool(original.get("submitted_at") or matched_reviews or effective_status in _VERIFIED)
        shell = {**original, "effective_status": effective_status, "has_submission": has_submission}
        bucket = _bucket_for(shell)
        summary[bucket] += 1
        if has_submission:
            summary["completed"] += 1

        review = matched_reviews[-1] if matched_reviews else {}
        signals = _json_object(review.get("signals_json"))
        review_status = str(review.get("status") or "").upper()
        ai_decision = signals.get("decision") or review.get("decision") or ""
        automated_reviewers = {"STRICTBOT_AI", "TAUFA_AI", "AUTOMATED_VALIDATOR"}
        admin_decision = ""
        if review and review.get("reviewed_by") and str(review.get("reviewed_by")).upper() not in automated_reviewers:
            admin_decision = str(review.get("decision") or review_status)
        elif review and review_status in _ADMIN_PENDING:
            admin_decision = "Awaiting administrator review"

        reason = ""
        if effective_status in _REJECTED or review_stage == "REJECTED":
            reason = str(review.get("reason_note") or review.get("reason_category") or signals.get("explanation") or "Reason not recorded.")
        elif effective_status in _MISSED and not has_submission:
            reason = str(details.get("missed_reason") or details.get("close_reason") or "No submission was recorded before the window closed.")

        points = _as_int(original.get("points")) or 0
        configured_points = _as_int(details.get("configured_points") or details.get("target_points") or details.get("points_configured"))
        if configured_points is None and points > 0:
            configured_points = points
        if configured_points is None and effective_status not in _MISSED and effective_status not in _REJECTED and points >= 0:
            configured_points = points

        linked_events = [
            event for event in event_payloads
            if event.get("related_task_id") == original.get("activity_id")
            or event.get("event_id") == details.get("score_event_id")
            or (event.get("evidence_id") and event.get("evidence_id") == review.get("evidence_id"))
            or _json_object(next((raw.get("audit_json") for raw in events if raw.get("event_id") == event.get("event_id")), {})).get("activity_id") == original.get("activity_id")
        ]
        verified_points = sum(
            int(event.get("points") or 0)
            for event in linked_events
            if str(event.get("ledger_state") or "").upper() not in {"PROVISIONAL", "REJECTED_OR_REVOKED"}
            and str(event.get("event_type") or "").upper() != "PROOF_REJECTED"
        )
        provisional_points = sum(
            max(0, int(event.get("points") or 0))
            for event in linked_events
            if str(event.get("ledger_state") or "").upper() == "PROVISIONAL"
        )
        rejected_points = sum(
            abs(int(event.get("points") or 0))
            for event in linked_events
            if str(event.get("ledger_state") or "").upper() == "REJECTED_OR_REVOKED"
            or str(event.get("event_type") or "").upper() == "PROOF_REJECTED"
        )
        if not linked_events and effective_status in _VERIFIED:
            verified_points = max(0, points)
        elif not linked_events and effective_status in _AI_PENDING | _ADMIN_PENDING:
            provisional_points = max(0, points)

        review_summary = {
            "evidence_status": "RECEIVED" if matched_reviews or original.get("submitted_at") else "NOT_SUBMITTED",
            "ai_analysis_status": (
                "FAILED" if review_stage == "AI_FAILED"
                else str(signals.get("analysis_status") or signals.get("ai_status") or review_stage or "NOT_RECORDED")
            ),
            "ai_decision": str(ai_decision or ""),
            "ai_explanation": str(signals.get("explanation") or signals.get("reason") or ""),
            "admin_decision": admin_decision,
            "admin_reason": str(review.get("reason_note") or "") if admin_decision and "Awaiting" not in admin_decision else "",
            "evidence_id": str(review.get("evidence_id") or details.get("evidence_id") or ""),
            "reviewed_at": review.get("reviewed_at"),
        }
        description = str(details.get("user_description") or details.get("description") or details.get("activity_description") or "")
        serialized.append({
            "activity_id": original.get("activity_id"),
            "schedule_id": original.get("schedule_id"),
            "schedule_version": original.get("schedule_version"),
            "date": original.get("date") or date,
            "type": original.get("type"),
            "title": original.get("title") or str(original.get("type") or "Activity").replace("_", " ").title(),
            "description": description,
            "scheduled_time": original.get("scheduled_time"),
            "window_start": original.get("window_start"),
            "window_end": original.get("window_end"),
            "submitted_at": original.get("submitted_at") or (review.get("submitted_at") if review else None),
            "status": effective_status,
            "bucket": bucket,
            "configured_points": configured_points,
            "provisional_points": provisional_points,
            "verified_points": verified_points,
            "rejected_points": rejected_points,
            "penalty_points": int(original.get("penalty_points") or 0),
            "evidence": review_summary,
            "details": {
                key: details[key]
                for key in ("hour_slot", "steps", "requirements", "proof_requirements", "validation_rule", "target_time", "deadline", "sync_status")
                if key in details
            },
            "rejection_reason": reason,
            "time_of_day": _time_bucket(original.get("scheduled_time")),
            "point_event_ids": [event.get("event_id") for event in linked_events],
        })

    serialized.sort(key=lambda item: str(item.get("scheduled_time") or ""))
    proof_payload = [
        {
            "evidence_id": review.get("evidence_id"),
            "task_code": review.get("task_code"),
            "task_title": review.get("task_title"),
            "hour_slot": review.get("hour_slot"),
            "status": review.get("status"),
            "submitted_at": review.get("submitted_at"),
            "reviewed_at": review.get("reviewed_at"),
            "decision": review.get("decision"),
            "reason_category": review.get("reason_category"),
            "reason_note": review.get("reason_note"),
            "points_awarded": int(review.get("points_awarded") or 0),
            "reviewed_by": review.get("reviewed_by"),
        }
        for review in reviews
    ]

    truthful_summary = build_truthful_daily_summary(
        db=db,
        date=date,
        activities=serialized,
        reviews=reviews,
        ledger=ledger,
        account_id=account_id,
        is_dev_mode=is_dev_mode,
    )

    return {
        "date": date,
        "summary": summary,
        "ledger": ledger,
        "activities": serialized,
        "point_events": event_payloads,
        "proof_reviews": proof_payload,
        "truthful_daily_summary": truthful_summary,
        "completion_definition": "Completed means a persisted submission timestamp or proof-review record exists. Verified, pending, and rejected are review outcomes shown as separate workflow breakdowns.",
    }


def build_truthful_daily_summary(
    db: Any,
    date: str,
    activities: Optional[Iterable[Dict[str, Any]]] = None,
    reviews: Optional[List[Dict[str, Any]]] = None,
    ledger: Optional[Dict[str, Any]] = None,
    account_id: str = "user_lok",
    is_dev_mode: bool = False,
) -> Dict[str, Any]:
    """
    Part 4 (Sections 7, 8, 9): Generates a truthful, non-fabricated, versioned daily summary
    strictly from persisted records (scheduled_activities, proof_reviews, evidence_files,
    focus_sessions, score_events, activity_journal_events).
    Idempotent: Reopening Today's Summary returns the cached snapshot unless new activity
    or review state has arrived, and never creates duplicate point transactions.
    """
    import hashlib

    dev_flag = 1 if is_dev_mode else 0
    with db._connect() as conn:
        if activities is None:
            act_rows = conn.execute(
                "SELECT * FROM scheduled_activities WHERE date = ? AND account_id = ? ORDER BY scheduled_time ASC",
                (date, account_id),
            ).fetchall()
            act_list = [dict(r) for r in act_rows]
        else:
            act_list = [dict(a) for a in activities]

        if reviews is None:
            rev_rows = conn.execute(
                "SELECT * FROM proof_reviews WHERE date = ? AND user_id = ? ORDER BY submitted_at ASC",
                (date, account_id),
            ).fetchall()
            rev_list = [dict(r) for r in rev_rows]
        else:
            rev_list = [dict(r) for r in reviews]

        ev_rows = conn.execute(
            """SELECT ef.evidence_id, ef.record_type, ef.sha256_hash, ef.metadata_json, ef.timestamp
            FROM evidence_files ef WHERE ef.date=? AND ef.is_dev_mode=? AND ef.is_deleted=0
            AND (EXISTS(SELECT 1 FROM proof_reviews pr WHERE pr.evidence_id=ef.evidence_id AND pr.user_id=?)
              OR EXISTS(SELECT 1 FROM devices d WHERE d.device_id=ef.device_id AND d.user_id=?))""",
            (date, dev_flag, account_id, account_id),
        ).fetchall()
        ev_list = [dict(r) for r in ev_rows]

        focus_rows = conn.execute(
            """SELECT fs.focus_id AS session_id, fs.session_type, fs.actual_minutes AS duration_minutes,
            1 AS completed FROM focus_sessions fs WHERE fs.date=? AND fs.is_dev_mode=? AND fs.status='COMPLETED'
            AND EXISTS(SELECT 1 FROM devices d WHERE d.device_id=fs.device_id AND d.user_id=?)""",
            (date, dev_flag, account_id),
        ).fetchall()
        focus_list = [dict(r) for r in focus_rows]

        hourly_rows = conn.execute(
            """SELECT hr.hour_slot, hr.primary_activity, hr.description, hr.status, hr.evidence_id
            FROM hourly_records hr WHERE hr.date=? AND hr.is_dev_mode=?
            AND (EXISTS(SELECT 1 FROM proof_reviews pr WHERE pr.evidence_id=hr.evidence_id AND pr.user_id=?)
              OR EXISTS(SELECT 1 FROM devices d WHERE d.device_id=hr.device_id AND d.user_id=?))""",
            (date, dev_flag, account_id, account_id),
        ).fetchall()
        hourly_list = [dict(r) for r in hourly_rows]

        journal_rows = conn.execute(
            "SELECT event_id, event_type, provenance, timestamp, activity_id, submission_id, evidence_id, category, description FROM activity_journal_events WHERE date = ? AND account_id = ? ORDER BY timestamp ASC",
            (date, account_id),
        ).fetchall()
        journal_list = [dict(r) for r in journal_rows]

        daily_row = conn.execute(
            "SELECT * FROM daily_records WHERE date = ? AND user_id = ? AND is_dev_mode = ?",
            (date, account_id, dev_flag),
        ).fetchone()
        daily_rec = dict(daily_row) if daily_row else {}

    if ledger is None:
        ledger = db.get_point_ledger_summary(date=date, is_dev_mode=is_dev_mode, account_id=account_id)

    # Compute deterministic snapshot fingerprint to detect whether underlying records changed
    fingerprint_payload = json.dumps(
        {
            "date": date,
            "account_id": account_id,
            "acts": [
                (
                    a.get("activity_id"),
                    a.get("status"),
                    a.get("submitted_at"),
                    a.get("points"),
                    a.get("penalty_points"),
                )
                for a in act_list
            ],
            "revs": [
                (
                    r.get("evidence_id"),
                    r.get("status"),
                    r.get("decision"),
                    r.get("points_awarded"),
                    r.get("reviewed_at"),
                )
                for r in rev_list
            ],
            "evs": [(e.get("evidence_id"), e.get("sha256_hash")) for e in ev_list],
            "focus": [(f.get("session_id"), f.get("duration_minutes")) for f in focus_list],
            "ledger": ledger,
            "daily_score": daily_rec.get("final_score"),
        },
        sort_keys=True,
        default=str,
    )
    snapshot_hash = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]

    with db._connect() as conn:
        existing_snap = conn.execute(
            "SELECT * FROM daily_summary_snapshots WHERE date = ? AND account_id = ? AND is_dev_mode = ?",
            (date, account_id, dev_flag),
        ).fetchone()
        if existing_snap and existing_snap["snapshot_hash"] == snapshot_hash:
            try:
                cached_summary = json.loads(existing_snap["summary_json"])
                cached_summary["snapshot_version"] = int(existing_snap["snapshot_version"])
                cached_summary["snapshot_hash"] = snapshot_hash
                cached_summary["cached_snapshot"] = True
                return cached_summary
            except Exception:
                pass

    # 1. Activity overview
    total_scheduled = len(act_list)
    submitted_count = 0
    verified_count = 0
    pending_review_count = 0
    missed_count = 0

    hourly_scheduled = 0
    hourly_submitted = 0
    hourly_verified = 0
    hourly_pending = 0
    hourly_missed = 0

    wakeup_status = str(daily_rec.get("wakeup_status") or "PENDING").upper()
    bath_status = str(daily_rec.get("bath_status") or "PENDING").upper()
    gym_status = str(daily_rec.get("gym_status") or "UPCOMING").upper()

    study_coding_checkins = 0
    wasted_checkins = 0

    for act in act_list:
        atype = str(act.get("type") or "").upper()
        st = str(act.get("status") or act.get("effective_status") or "SCHEDULED").upper()
        has_sub = bool(act.get("submitted_at") or st in (_VERIFIED | _AI_PENDING | _ADMIN_PENDING))

        if has_sub:
            submitted_count += 1
        if st in _VERIFIED:
            verified_count += 1
        elif st in (_AI_PENDING | _ADMIN_PENDING):
            pending_review_count += 1
        elif st in _MISSED:
            missed_count += 1

        if atype == "WAKE_UP":
            wakeup_status = st
        elif atype == "BATH":
            bath_status = st
        elif atype == "GYM":
            gym_status = st
        elif atype.startswith("HOURLY"):
            hourly_scheduled += 1
            if has_sub:
                hourly_submitted += 1
            if st in _VERIFIED:
                hourly_verified += 1
            elif st in (_AI_PENDING | _ADMIN_PENDING):
                hourly_pending += 1
            elif st in _MISSED:
                hourly_missed += 1

    for hr in hourly_list:
        cat = str(hr.get("primary_activity") or "").upper()
        if cat in ("CODING", "STUDY", "DEEP WORK", "READING", "PROJECT"):
            study_coding_checkins += 1
        elif cat in ("WASTED", "ENTERTAINMENT", "IDLE"):
            wasted_checkins += 1

    if study_coding_checkins == 0 and hourly_submitted > 0:
        study_coding_checkins = hourly_submitted

    # 2. Focus / Study duration (Truthful: report exact minutes ONLY if focus_sessions recorded them)
    tracked_focus_minutes = sum(int(f.get("duration_minutes") or 0) for f in focus_list)
    if tracked_focus_minutes > 0:
        focus_study_display = f"{tracked_focus_minutes} tracked focus min ({len(focus_list)} session(s)) + {study_coding_checkins} hourly check-in(s)"
    elif study_coding_checkins > 0:
        focus_study_display = f"{study_coding_checkins} reported check-in(s) (no separate timer minutes recorded)"
    else:
        focus_study_display = "0 tracked focus minutes / 0 study check-ins recorded"

    # 3. Evidence & OCR & Duplicate breakdown
    proofs_submitted_count = max(len(ev_list), len(rev_list))
    ocr_completed_count = 0
    ocr_no_text_count = 0
    ocr_unavailable_count = 0
    duplicate_warnings_count = 0
    awaiting_review_count = 0
    admin_approved_count = 0
    admin_rejected_count = 0

    seen_ocr_ev: set[str] = set()
    for ev in ev_list:
        meta = _json_object(ev.get("metadata_json"))
        ocr_st = str(meta.get("ocr_status") or "").upper()
        if ocr_st == "OCR_COMPLETED":
            ocr_completed_count += 1
            seen_ocr_ev.add(str(ev.get("evidence_id")))
        elif ocr_st == "NO_TEXT_DETECTED":
            ocr_no_text_count += 1
            seen_ocr_ev.add(str(ev.get("evidence_id")))
        elif ocr_st in ("OCR_UNAVAILABLE", "OCR_FAILED"):
            ocr_unavailable_count += 1

    for rev in rev_list:
        eid = str(rev.get("evidence_id") or "")
        rst = str(rev.get("status") or "PENDING").upper()
        rdec = str(rev.get("decision") or "").upper()
        rby = str(rev.get("reviewed_by") or "").upper()
        sig = _json_object(rev.get("signals_json"))
        ocr_sig = _json_object(sig.get("ocr"))
        dup_sig = _json_object(sig.get("duplicate"))
        checks_sig = _json_object(sig.get("checks"))

        if eid not in seen_ocr_ev:
            ocr_st = str(ocr_sig.get("status") or checks_sig.get("ocr_status") or "").upper()
            if ocr_st == "OCR_COMPLETED" or checks_sig.get("ocr_completed") is True:
                ocr_completed_count += 1
                seen_ocr_ev.add(eid)
            elif ocr_st == "NO_TEXT_DETECTED":
                ocr_no_text_count += 1
                seen_ocr_ev.add(eid)

        dup_status = str(
            dup_sig.get("status")
            or dup_sig.get("match_status")
            or checks_sig.get("duplicate_status")
            or "NO_MATCH_FOUND"
        ).upper()
        if dup_status in ("POSSIBLE_DUPLICATE", "HIGH_SIMILARITY", "EXACT_DUPLICATE") or rdec == "DUPLICATE_REVIEW":
            duplicate_warnings_count += 1

        if rst in ("APPROVED", "VERIFIED") and rby and rby != "STRICTBOT_AI":
            admin_approved_count += 1
        elif rst in _REJECTED and rby and rby != "STRICTBOT_AI":
            admin_rejected_count += 1
        elif rst in (_AI_PENDING | _ADMIN_PENDING | {"PROVISIONAL", "NEEDS_REVIEW", "PENDING", "UNDER_REVIEW"}):
            awaiting_review_count += 1

    # 4. Score and rewards
    verified_points = int(ledger.get("verified_points_awarded") or daily_rec.get("final_score") or 0)
    provisional_points = int(ledger.get("provisional_points_pending") or 0)
    penalties = abs(int(ledger.get("penalties_applied") or daily_rec.get("negative_points") or 0))
    net_verified_score = int(daily_rec.get("final_score") or ledger.get("net_verified_score") or 0)

    if net_verified_score >= 90:
        current_privilege = "ELITE UNLOCKED (All Rewards Allowed)"
        privilege_reason = f"Verified score {net_verified_score} meets Elite threshold (>= 90)."
    elif net_verified_score >= 80:
        current_privilege = "REWARD UNLOCKED (Entertainment Allowed)"
        privilege_reason = f"Verified score {net_verified_score} meets Reward threshold (>= 80)."
    elif net_verified_score >= 60:
        current_privilege = f"CONTROLLED MODE ({80 - net_verified_score} pts needed for full unlock)"
        privilege_reason = f"Verified score is {net_verified_score}/80; local music allowed, video streaming restricted."
    else:
        current_privilege = f"STRICT LOCKED ({max(0, 80 - net_verified_score)} pts needed for reward)"
        privilege_reason = f"Verified score is {net_verified_score}/80; distraction locks remain enforced."

    # 5. Truthful observations & next-day recommendations (never fabricates unrecorded events)
    observations: List[str] = []
    if submitted_count > 0:
        observations.append(
            f"Recorded {submitted_count} of {total_scheduled} scheduled activities today ({verified_count} verified, {pending_review_count} awaiting review)."
        )
    else:
        observations.append(
            f"No activity submissions have been recorded yet across {total_scheduled} scheduled slots for {date}."
        )

    if proofs_submitted_count > 0:
        observations.append(
            f"Processed {proofs_submitted_count} evidence file(s): {ocr_completed_count} with extracted OCR text, {duplicate_warnings_count} duplicate warning(s)."
        )
    else:
        observations.append("No evidence attachments have been uploaded for today's checkpoints yet.")

    recommendations: List[str] = []
    if wakeup_status in ("MISSED", "CLOSED", "PENDING"):
        recommendations.append("Complete tomorrow's Wake-Up verification inside the opening window to secure early momentum.")
    if hourly_missed > 0:
        recommendations.append(
            f"{hourly_missed} hourly checkpoint(s) closed without submission today; submit brief factual notes before each 20-minute window closes tomorrow."
        )
    if duplicate_warnings_count > 0:
        recommendations.append(
            f"{duplicate_warnings_count} submission(s) matched prior evidence; capture a fresh workspace screenshot or photo for each checkpoint."
        )
    if awaiting_review_count > 0:
        recommendations.append(
            f"{awaiting_review_count} proof submission(s) are currently provisional/pending administrator verification."
        )
    if not recommendations:
        recommendations.append("Maintain on-time checkpoint submissions tomorrow to preserve your discipline streak.")

    strictbot_paragraph = " ".join(observations + [recommendations[0]])

    # Provenance counts from activity_journal_events
    provenance_counts = {
        "USER_REPORTED": sum(1 for j in journal_list if j.get("provenance") == "USER_REPORTED"),
        "EVIDENCE_SUBMITTED": sum(1 for j in journal_list if j.get("provenance") == "EVIDENCE_SUBMITTED"),
        "OCR_COMPLETED": sum(1 for j in journal_list if j.get("provenance") == "OCR_COMPLETED"),
        "AI_RECOMMENDATION": sum(1 for j in journal_list if j.get("provenance") == "AI_RECOMMENDATION"),
        "ADMIN_VERIFIED": sum(1 for j in journal_list if j.get("provenance") == "ADMIN_VERIFIED"),
    }

    # Section 8 exact formatted text summary
    formatted_lines = [
        "STRICTRECORDS — DAILY SUMMARY",
        "",
        f"Date: {date}",
        "",
        "Activities",
        f"• Wake-Up: {wakeup_status}",
        f"• Morning Routine: {bath_status}",
        f"• Hourly Check-Ins: {hourly_submitted}/{hourly_scheduled} submitted ({hourly_verified} verified, {hourly_pending} pending, {hourly_missed} missed)",
        f"• Focus / Study: {focus_study_display}",
        f"• Exercise / Gym: {gym_status}",
        "",
        "Evidence & Verification",
        f"• Proofs Submitted: {proofs_submitted_count}",
        f"• OCR Completed: {ocr_completed_count}",
        f"• Duplicate Warnings: {duplicate_warnings_count}",
        f"• Pending Review: {awaiting_review_count}",
        "",
        "Score",
        f"• Verified Score: {net_verified_score}",
        f"• Provisional Points: {provisional_points}",
        f"• Penalties: {penalties}",
        f"• Current Privilege: {current_privilege}",
        "",
        "StrictBot Notes",
        f"• {observations[0]}",
        f"• {observations[1]}",
        f"• {recommendations[0]}",
    ]
    formatted_text = "\n".join(formatted_lines)

    next_version = (int(existing_snap["snapshot_version"]) + 1) if existing_snap else 1
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    summary_payload: Dict[str, Any] = {
        "date": date,
        "account_id": account_id,
        "timezone": "Asia/Kolkata",
        "snapshot_version": next_version,
        "snapshot_hash": snapshot_hash,
        "cached_snapshot": False,
        "generated_at": generated_at,
        "activity_overview": {
            "scheduled_tasks": total_scheduled,
            "submitted_tasks": submitted_count,
            "verified_tasks": verified_count,
            "pending_review": pending_review_count,
            "missed_tasks": missed_count,
        },
        "productivity_breakdown": {
            "wake_up_status": wakeup_status,
            "morning_routine_status": bath_status,
            "exercise_gym_status": gym_status,
            "hourly_scheduled": hourly_scheduled,
            "hourly_submitted": hourly_submitted,
            "hourly_verified": hourly_verified,
            "hourly_pending": hourly_pending,
            "hourly_missed": hourly_missed,
            "study_coding_checkins": study_coding_checkins,
            "tracked_focus_sessions": len(focus_list),
            "tracked_focus_minutes": tracked_focus_minutes,
            "focus_study_display": focus_study_display,
            "unproductive_or_missed_windows": hourly_missed + wasted_checkins,
        },
        "evidence_review": {
            "proofs_submitted": proofs_submitted_count,
            "ocr_completed": ocr_completed_count,
            "ocr_no_text_detected": ocr_no_text_count,
            "ocr_unavailable": ocr_unavailable_count,
            "duplicate_warnings": duplicate_warnings_count,
            "awaiting_review": awaiting_review_count,
            "admin_approved": admin_approved_count,
            "admin_rejected": admin_rejected_count,
        },
        "score_and_rewards": {
            "verified_score": net_verified_score,
            "verified_points_awarded": verified_points,
            "provisional_points": provisional_points,
            "penalties": penalties,
            "current_privilege": current_privilege,
            "privilege_reason": privilege_reason,
        },
        "provenance_counts": provenance_counts,
        "strictbot_summary": strictbot_paragraph,
        "observations": observations,
        "recommendations": recommendations,
        "formatted_text": formatted_text,
        "journal_events": journal_list[-50:],
    }

    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_summary_snapshots (
                date, account_id, is_dev_mode, snapshot_version,
                snapshot_hash, summary_json, formatted_text, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                account_id,
                dev_flag,
                next_version,
                snapshot_hash,
                json.dumps(summary_payload),
                formatted_text,
                generated_at,
            ),
        )

    return summary_payload


