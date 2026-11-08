"""Authoritative administrator inspection, editing, and point assignment service for hourly submissions."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.hourly_submission_service import HourlySubmissionError, _ALLOWED_CATEGORIES
from core.models import AdminActionType, ScoreEventType
from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist


DEFAULT_HOURLY_POINT_LIMIT = 8


class HourlyAdminService:
    """Manages Cockpit inspection, editing, and points ledger awards for Android hourly attendance."""

    def __init__(self, db: Any, scoring_engine: Any, storage_root: Optional[str] = None):
        self.db = db
        self.engine = scoring_engine
        self.storage_root = storage_root or getattr(scoring_engine, "storage_root", None) or os.path.join(os.getcwd(), "data", "evidence")

    def _format_time_label(self, iso_val: Optional[str]) -> str:
        if not iso_val:
            return ""
        try:
            dt = iso_to_dt(iso_val).astimezone(IST)
            return dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return str(iso_val)

    def _format_window_label(self, start_iso: Optional[str], end_iso: Optional[str]) -> str:
        if not start_iso or not end_iso:
            return ""
        return f"{self._format_time_label(start_iso)} – {self._format_time_label(end_iso)}"

    def get_configured_hourly_limit(self) -> int:
        try:
            cfg = self.db.get_active_configuration() or {}
            rules = cfg.get("rules") or {}
            if "hourly_points" in rules:
                return int(rules["hourly_points"])
        except Exception:
            pass
        return DEFAULT_HOURLY_POINT_LIMIT

    def list_hourly_submissions(
        self,
        date: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        occurrence_id: Optional[str] = None,
        account_id: str = "user_lok",
        is_dev_mode: bool = False,
        target_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Lists actual hourly schedule and submission records.
        Strictly does NOT invent fake or placeholder submissions when none exist.
        """
        now = now_ist()
        target_date = date or target_date
        if not target_date and occurrence_id:
            with self.db._connect() as conn:
                r = conn.execute("SELECT date FROM scheduled_activities WHERE activity_id = ?", (occurrence_id,)).fetchone()
                if r:
                    target_date = r["date"]
        if not target_date:
            target_date = now.strftime("%Y-%m-%d")
        dev_flag = 1 if is_dev_mode else 0

        # Ensure activities are in DB for target date
        from core.time_window_engine import TimeWindowEngine
        twe = TimeWindowEngine(self.db)
        twe.ensure_activities_in_db(target_date, account_id=account_id, reference_now=now)

        with self.db._connect() as conn:
            query = """
                SELECT * FROM scheduled_activities
                WHERE date = ? AND account_id = ? AND type = 'HOURLY'
            """
            params: List[Any] = [target_date, account_id]
            if occurrence_id:
                query += " AND activity_id = ?"
                params.append(occurrence_id)
            query += " ORDER BY window_start ASC"

            act_rows = conn.execute(query, params).fetchall()

            # Load hourly_records
            hr_rows = conn.execute(
                "SELECT * FROM hourly_records WHERE date = ? AND is_dev_mode = ?",
                (target_date, dev_flag),
            ).fetchall()
            hr_by_slot = {r["hour_slot"]: dict(r) for r in hr_rows}

            # Load unacknowledged Android sync events for sync status
            pending_deliveries = conn.execute(
                """SELECT e.submission_id, e.activity_id FROM proof_submission_events e
                   LEFT JOIN proof_submission_deliveries d ON d.event_id = e.event_id
                   WHERE e.account_id = ? AND d.event_id IS NULL""",
                (account_id,),
            ).fetchall()
            pending_sync_acts = {r["activity_id"] for r in pending_deliveries if r["activity_id"]}
            pending_sync_subs = {r["submission_id"] for r in pending_deliveries if r["submission_id"]}

        items: List[Dict[str, Any]] = []

        for row in act_rows:
            act = dict(row)
            details: Dict[str, Any] = {}
            try:
                details = json.loads(act.get("details_json") or "{}")
            except Exception:
                pass

            hour_slot = details.get("hour_slot") or act.get("title") or "Hourly"
            legacy_hr = hr_by_slot.get(hour_slot, {})

            # Extract evidence ID
            evidence_ids: List[str] = []
            try:
                evidence_ids = json.loads(act.get("evidence_ids_json") or "[]")
            except Exception:
                pass
            if not evidence_ids and details.get("evidence_id"):
                evidence_ids = [details["evidence_id"]]
            if not evidence_ids and legacy_hr.get("evidence_id"):
                evidence_ids = [legacy_hr["evidence_id"]]

            evidence_id = evidence_ids[0] if evidence_ids else None
            submission_id = act.get("submission_id") or details.get("submission_id") or legacy_hr.get("related_record_id")

            # Load evidence file if exists
            evidence_info: Optional[Dict[str, Any]] = None
            if evidence_id:
                with self.db._connect() as conn:
                    ef = conn.execute("SELECT * FROM evidence_files WHERE evidence_id = ?", (evidence_id,)).fetchone()
                    if ef:
                        ef_dict = dict(ef)
                        meta = {}
                        try:
                            meta = json.loads(ef_dict.get("metadata_json") or "{}")
                        except Exception:
                            pass
                        file_path = ef_dict.get("file_path", "")
                        exists_on_disk = bool(file_path and os.path.isfile(file_path))
                        evidence_info = {
                            "evidence_id": evidence_id,
                            "sha256_hash": ef_dict.get("sha256_hash"),
                            "file_path": file_path,
                            "file_size_bytes": ef_dict.get("file_size_bytes", 0),
                            "timestamp": ef_dict.get("timestamp"),
                            "device_id": ef_dict.get("device_id"),
                            "detected_mime_type": meta.get("detected_mime_type") or "image/jpeg",
                            "original_filename": meta.get("original_filename") or os.path.basename(file_path or ""),
                            "integrity_valid": exists_on_disk,
                            "view_url": f"/api/evidence/view?evidence_id={evidence_id}",
                            "metadata": meta,
                        }

            # Load proof review if exists
            review_info: Optional[Dict[str, Any]] = None
            if evidence_id:
                with self.db._connect() as conn:
                    pr = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()
                    if pr:
                        pr_dict = dict(pr)
                        signals = {}
                        try:
                            signals = json.loads(pr_dict.get("signals_json") or "{}")
                        except Exception:
                            pass
                        review_info = {
                            "review_id": pr_dict.get("review_id"),
                            "status": pr_dict.get("status"),
                            "decision": pr_dict.get("decision"),
                            "points_awarded": pr_dict.get("points_awarded", 0),
                            "reviewed_by": pr_dict.get("reviewed_by"),
                            "reviewed_at": pr_dict.get("reviewed_at"),
                            "reason_note": pr_dict.get("reason_note"),
                            "signals": signals,
                            "priority": pr_dict.get("priority", "NORMAL"),
                        }

            # Determine submission status bucket
            raw_status = str(act.get("status") or "").upper()
            w_start = iso_to_dt(act["window_start"])
            w_end = iso_to_dt(act["window_end"])

            has_submission = bool(submission_id or evidence_id or act.get("submitted_at"))
            is_pending_sync = bool((act["activity_id"] in pending_sync_acts or (submission_id and submission_id in pending_sync_subs)))

            if raw_status in ("APPROVED", "VERIFIED") or (review_info and review_info.get("status") == "APPROVED"):
                review_status = "APPROVED"
            elif raw_status in ("REJECTED",) or (review_info and review_info.get("status") == "REJECTED"):
                review_status = "REJECTED"
            elif raw_status in ("VALIDATING", "AI_PROVISIONAL") or (review_info and review_info.get("status") in ("AI_QUEUED", "AI_ANALYZING", "NEEDS_REVIEW", "PENDING_ADMIN_REVIEW")):
                review_status = "AI_REVIEW_PENDING"
            elif raw_status == "SUBMITTED":
                review_status = "SUBMITTED"
            elif is_pending_sync and has_submission:
                review_status = "PENDING_SYNC"
            elif raw_status in ("MISSED", "CLOSED", "REJECTED_LATE", "EXPIRED"):
                review_status = "MISSED_OR_EXPIRED"
            elif w_start <= now < w_end and not has_submission:
                review_status = "OPEN_WINDOW"
            elif now < w_start and not has_submission:
                review_status = "UPCOMING"
            elif now >= w_end and not has_submission:
                review_status = "MISSED_OR_EXPIRED"
            else:
                review_status = raw_status

            cat = details.get("activity_category") or legacy_hr.get("primary_activity") or "PRODUCTIVE"
            desc = act.get("description") or details.get("activity_description") or legacy_hr.get("description") or ""
            submitted_at = act.get("submitted_at") or details.get("accepted_at") or legacy_hr.get("submitted_at")
            confirmed_points = int(act.get("points") or (review_info.get("points_awarded") if review_info and review_status == "APPROVED" else 0) or 0)

            # Filtering
            if category and cat.upper() != category.upper():
                continue
            if status and review_status.upper() != status.upper() and raw_status.upper() != status.upper():
                continue

            # Hourly session label (e.g. 7:00 PM – 8:00 PM)
            try:
                ckpt_dt = iso_to_dt(act.get("scheduled_time") or act["window_start"]).astimezone(IST)
                start_h = ckpt_dt.replace(minute=0, second=0)
                end_h = start_h.replace(hour=(start_h.hour + 1) % 24)
                session_label = f"{start_h.strftime('%I:%M %p').lstrip('0')} – {end_h.strftime('%I:%M %p').lstrip('0')}"
            except Exception:
                session_label = hour_slot

            window_label = self._format_window_label(act.get("window_start"), act.get("window_end"))

            sig = (review_info.get("signals") or {}) if review_info else {}
            u_status = sig.get("upload_status") or ("UPLOADED" if has_submission else "UPLOAD_PENDING")
            ocr_meta = (evidence_info.get("metadata") or {}) if evidence_info else {}
            o_status = sig.get("ocr_status") or ocr_meta.get("ocr_status") or ("OCR_COMPLETED" if (ocr_meta.get("ocr_text") or sig.get("ocr_text")) else ("OCR_PENDING" if has_submission else "OCR_PENDING"))
            v_status = sig.get("validation_status") or (
                "APPROVED" if review_status == "APPROVED" else (
                    "REJECTED" if review_status == "REJECTED" else (
                        "REVIEW_REQUIRED" if review_status in ("NEEDS_REVIEW", "REVIEW_REQUIRED") else (
                            "PROVIDER_UNAVAILABLE" if sig.get("is_technical_failure") else "VALIDATION_PENDING"
                        )
                    )
                )
            )
            p_status = sig.get("points_status") or (
                "POINTS_POSTED" if (confirmed_points > 0 or review_status in ("APPROVED", "REJECTED")) else "POINTS_PENDING"
            )
            s_status = sig.get("sync_status") or (
                "SYNC_PENDING" if is_pending_sync else ("SYNCED_ANDROID" if (details.get("source_platform") == "ANDROID") else "SYNCED_WINDOWS")
            )

            items.append({
                "activity_id": act["activity_id"],
                "occurrence_id": act["activity_id"],
                "date": target_date,
                "hour_slot": hour_slot,
                "session_label": session_label,
                "window_start": act.get("window_start"),
                "window_end": act.get("window_end"),
                "submission_window_label": window_label,
                "submission_id": submission_id,
                "category": cat,
                "description": desc,
                "submitted_at": submitted_at,
                "review_status": review_status,
                "raw_status": raw_status,
                "points_awarded": confirmed_points,
                "configured_limit": self.get_configured_hourly_limit(),
                "has_actual_submission": has_submission,
                "evidence": evidence_info,
                "review": review_info,
                "source_platform": details.get("source_platform") or "ANDROID",
                "source_device_id": details.get("source_device_id") or legacy_hr.get("device_id") or "",
                "pending_sync": is_pending_sync,
                "upload_status": u_status,
                "ocr_status": o_status,
                "validation_status": v_status,
                "points_status": p_status,
                "sync_status": s_status,
            })

        return items

    def get_hourly_submission_detail(
        self,
        activity_id: str,
        account_id: str = "user_lok",
        is_dev_mode: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetches full inspection detail for a single hourly occurrence."""
        submissions = self.list_hourly_submissions(
            occurrence_id=activity_id,
            account_id=account_id,
            is_dev_mode=is_dev_mode,
        )
        if not submissions:
            return None
        detail = dict(submissions[0])

        evidence_id = detail["evidence"]["evidence_id"] if detail.get("evidence") else None
        # Load revisions if any
        revisions: List[Dict[str, Any]] = []
        if evidence_id:
            with self.db._connect() as conn:
                rev_rows = conn.execute(
                    "SELECT * FROM proof_revisions WHERE evidence_id = ? ORDER BY revision_number ASC",
                    (evidence_id,),
                ).fetchall()
                revisions = [dict(r) for r in rev_rows]
        detail["revisions"] = revisions

        # Load audit history
        audit_records: List[Dict[str, Any]] = []
        with self.db._connect() as conn:
            audit_rows = conn.execute(
                """SELECT * FROM admin_audit_logs
                   WHERE field_changed LIKE ? OR details_json LIKE ?
                   ORDER BY timestamp DESC LIMIT 20""",
                (f"%{activity_id}%", f"%{activity_id}%"),
            ).fetchall()
            audit_records = [dict(r) for r in audit_rows]
        detail["audit_history"] = audit_records

        return detail

    def edit_hourly_submission(
        self,
        activity_id: str,
        admin_user: str,
        updates: Dict[str, Any],
        account_id: str = "user_lok",
        is_dev_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Allows authorized administrator editing of category, text description, admin notes,
        and replacement evidence (versioned, non-destructive).
        Enforces review-state protection:
        - Saving text correction does NOT automatically award points.
        - Preserves original scheduled occurrence ID and window.
        - Does NOT reopen expired user submission window.
        - Preserves original evidence image and hash.
        """
        with self.db._connect() as conn:
            act_row = conn.execute(
                "SELECT * FROM scheduled_activities WHERE activity_id = ? AND account_id = ? AND type = 'HOURLY'",
                (activity_id, account_id),
            ).fetchone()
            if not act_row:
                raise HourlySubmissionError(
                    f"Hourly checkpoint '{activity_id}' not found.", status_code=404, code="OCCURRENCE_NOT_FOUND"
                )
            act = dict(act_row)

        details: Dict[str, Any] = {}
        try:
            details = json.loads(act.get("details_json") or "{}")
        except Exception:
            pass

        now_iso = dt_to_iso(now_ist())
        target_date = act["date"]
        dev_flag = 1 if is_dev_mode else 0

        fields_changed: List[str] = []
        old_values: Dict[str, Any] = {}
        new_values: Dict[str, Any] = {}

        # 1. Edit Category
        if "category" in updates and updates["category"]:
            new_cat = str(updates["category"]).strip().upper()
            if new_cat not in _ALLOWED_CATEGORIES:
                raise HourlySubmissionError(f"Category '{new_cat}' is not permitted.", status_code=422, code="INVALID_CATEGORY")
            old_cat = details.get("activity_category") or "PRODUCTIVE"
            if new_cat != old_cat:
                fields_changed.append("category")
                old_values["category"] = old_cat
                new_values["category"] = new_cat
                details["activity_category"] = new_cat

        # 2. Edit Description
        if "description" in updates and updates["description"] is not None:
            new_desc = str(updates["description"]).strip()
            if not new_desc:
                raise HourlySubmissionError("Description cannot be blank.", status_code=422, code="DESCRIPTION_REQUIRED")
            old_desc = act.get("description") or details.get("activity_description") or ""
            if new_desc != old_desc:
                fields_changed.append("description")
                old_values["description"] = old_desc
                new_values["description"] = new_desc
                act["description"] = new_desc
                details["activity_description"] = new_desc

        # 3. Edit Administrative Notes
        admin_notes = updates.get("admin_notes")

        # 4. Replacement Evidence (Versioned & Non-Destructive)
        replacement_bytes = updates.get("replacement_evidence_bytes")
        replacement_filename = updates.get("replacement_evidence_filename") or "replacement.jpg"
        new_evidence_id: Optional[str] = None

        if replacement_bytes:
            from core.hourly_submission_service import HourlySubmissionService
            file_info = HourlySubmissionService.validate_evidence(replacement_bytes, replacement_filename)
            raw = bytes(replacement_bytes)
            rep_hash = hashlib.sha256(raw).hexdigest()

            # Generate versioned evidence ID
            existing_evidence_ids = []
            try:
                existing_evidence_ids = json.loads(act.get("evidence_ids_json") or "[]")
            except Exception:
                pass
            parent_ev_id = existing_evidence_ids[0] if existing_evidence_ids else f"ev_{uuid.uuid4().hex[:16]}"
            version_num = len(existing_evidence_ids) + 1
            new_evidence_id = f"{parent_ev_id}_v{version_num}"

            # Save replacement file to separate path
            day_folder = f"{target_date}_DEV" if is_dev_mode else target_date
            target_dir = os.path.join(self.storage_root, day_folder, "Hourly")
            os.makedirs(target_dir, exist_ok=True)
            rep_path = os.path.join(target_dir, f"{new_evidence_id}{file_info['extension']}")
            with open(rep_path, "wb") as f:
                f.write(raw)

            from core.ai_robot_admin import StrictBotAIReviewer
            dhash_val = StrictBotAIReviewer.compute_dhash(raw)
            ahash_val = StrictBotAIReviewer.compute_ahash(raw)

            rep_metadata = {
                "task_type": "HOURLY",
                "activity_id": activity_id,
                "parent_evidence_id": parent_ev_id,
                "version": version_num,
                "replaced_by_admin": admin_user,
                "original_filename": file_info["filename"],
                "detected_mime_type": file_info["mime_type"],
                "sha256": rep_hash,
                "dhash": dhash_val,
                "ahash": ahash_val,
            }

            with self.db._connect() as conn:
                conn.execute(
                    """INSERT INTO evidence_files (
                        evidence_id, date, is_dev_mode, record_type, related_record_id,
                        file_path, timestamp, device_id, file_size_bytes, sha256_hash, metadata_json, is_deleted
                    ) VALUES (?, ?, ?, 'Hourly', ?, ?, ?, 'windows_admin', ?, ?, ?, 0)""",
                    (new_evidence_id, target_date, dev_flag, act.get("submission_id", activity_id), rep_path, now_iso, len(raw), rep_hash, json.dumps(rep_metadata)),
                )
                conn.execute(
                    """INSERT INTO proof_revisions (
                        revision_id, evidence_id, revision_number, status, submitted_at, reason_note, file_path
                    ) VALUES (?, ?, ?, 'PENDING', ?, ?, ?)""",
                    (f"rev_{new_evidence_id}", parent_ev_id, version_num, now_iso, f"Replacement evidence uploaded by {admin_user}", rep_path),
                )

            existing_evidence_ids.append(new_evidence_id)
            act["evidence_ids_json"] = json.dumps(existing_evidence_ids)
            details["evidence_id"] = new_evidence_id
            details["evidence_sha256"] = rep_hash
            details["ai_validation_state"] = "PENDING"
            act["validation_status"] = "PROCESSING"
            act["status"] = "VALIDATING"  # Reset status for re-evaluation; do NOT auto-approve

            fields_changed.append("evidence_attachment")
            old_values["evidence_attachment"] = parent_ev_id
            new_values["evidence_attachment"] = new_evidence_id

        # 5. Review Decision change (Explicit only)
        decision = updates.get("review_decision")
        if decision:
            decision_upper = str(decision).strip().upper()
            if decision_upper in ("APPROVE", "APPROVED"):
                act["status"] = "VERIFIED"
                act["validation_status"] = "VERIFIED"
                fields_changed.append("review_decision")
                old_values["review_decision"] = act_row["status"]
                new_values["review_decision"] = "APPROVED"
            elif decision_upper in ("REJECT", "REJECTED"):
                act["status"] = "REJECTED"
                act["validation_status"] = "REJECTED"
                fields_changed.append("review_decision")
                old_values["review_decision"] = act_row["status"]
                new_values["review_decision"] = "REJECTED"

        # Apply database updates
        hour_slot = details.get("hour_slot", "")
        with self.db._connect() as conn:
            conn.execute(
                """UPDATE scheduled_activities
                   SET description = ?, details_json = ?, evidence_ids_json = ?, status = ?, validation_status = ?
                   WHERE activity_id = ? AND account_id = ?""",
                (act.get("description"), json.dumps(details), act.get("evidence_ids_json"), act.get("status"), act.get("validation_status"), activity_id, account_id),
            )
            # Update hourly_records
            conn.execute(
                """UPDATE hourly_records
                   SET primary_activity = ?, description = ?
                   WHERE date = ? AND is_dev_mode = ? AND hour_slot = ?""",
                (details.get("activity_category", "PRODUCTIVE"), act.get("description"), target_date, dev_flag, hour_slot),
            )
            # Update proof_reviews if existing
            current_ev_id = details.get("evidence_id") or (json.loads(act.get("evidence_ids_json") or "[]") or [None])[0]
            if current_ev_id:
                pr_update_fields = []
                pr_params = []
                if "category" in new_values:
                    pr_update_fields.append("task_code = ?")
                    pr_params.append(new_values["category"])
                if admin_notes is not None:
                    pr_update_fields.append("reason_note = ?")
                    pr_params.append(admin_notes)
                if decision and str(decision).upper() in ("APPROVE", "APPROVED"):
                    pr_update_fields.extend(["status = 'APPROVED'", "decision = 'APPROVED'", "reviewed_by = ?", "reviewed_at = ?"])
                    pr_params.extend([admin_user, now_iso])
                elif decision and str(decision).upper() in ("REJECT", "REJECTED"):
                    pr_update_fields.extend(["status = 'REJECTED'", "decision = 'REJECTED'", "reviewed_by = ?", "reviewed_at = ?"])
                    pr_params.extend([admin_user, now_iso])

                if pr_update_fields:
                    pr_params.append(current_ev_id)
                    conn.execute(
                        f"UPDATE proof_reviews SET {', '.join(pr_update_fields)} WHERE evidence_id = ?",
                        pr_params,
                    )

        # Record admin audit trail
        reason = str(updates.get("reason") or "Administrator edit of hourly submission")
        audit_res = self.db.record_admin_audit(
            admin_user=admin_user,
            action_type=AdminActionType.PROOF_APPROVED.value if decision and str(decision).upper() in ("APPROVE", "APPROVED") else "HOURLY_SUBMISSION_EDIT",
            field_changed=f"activity:{activity_id}",
            old_value=old_values,
            new_value=new_values,
            device_id="windows_admin",
            timestamp=now_iso,
        )

        # Emit Android sync event
        sync_payload = {
            "event_id": f"evt_edit_{uuid.uuid4().hex[:12]}",
            "event_type": "HOURLY_SUBMISSION_EDITED",
            "activity_id": activity_id,
            "submission_id": act.get("submission_id", ""),
            "category": details.get("activity_category"),
            "description": act.get("description"),
            "evidence_id": details.get("evidence_id"),
            "status": act.get("status"),
            "admin_user": admin_user,
            "admin_notes": admin_notes,
            "timestamp": now_iso,
            "fields_changed": fields_changed,
        }
        sync_sub_id = f"sync_edit_{uuid.uuid4().hex[:12]}"
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO proof_submission_events (
                    event_id, account_id, submission_id, activity_id,
                    schedule_version, source_device_id, accepted_at, status, payload_json
                ) VALUES (?, ?, ?, ?, 1, 'windows_admin', ?, ?, ?)""",
                (sync_payload["event_id"], account_id, sync_sub_id, activity_id, now_iso, act.get("status", "SUBMITTED"), json.dumps(sync_payload)),
            )

        return {
            "success": True,
            "activity_id": activity_id,
            "fields_changed": fields_changed,
            "old_values": old_values,
            "new_values": new_values,
            "audit_id": audit_res["audit_id"],
            "timestamp": now_iso,
        }

    def award_hourly_points(
        self,
        activity_id: str,
        admin_user: str,
        points_to_award: int,
        reason: str = "",
        confirm_change: bool = False,
        account_id: str = "user_lok",
        is_dev_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Assigns or corrects points for an individual hourly occurrence through the authoritative ledger.
        Requirements:
        - Whole numbers >= 0 only.
        - Negative values strictly rejected.
        - Validated against configured administrative limit.
        - Unapproved proofs cannot be awarded points without approval.
        - Idempotent: duplicate awards do not award points twice.
        - Confirmed change requires explicit confirmation.
        - Only selected hourly occurrence is modified; Bath, Gym, Travel, Wake-Up unaffected.
        """
        # Validate point value
        try:
            pts = int(points_to_award)
        except (ValueError, TypeError) as exc:
            raise HourlySubmissionError("Point value must be an integer.", status_code=400, code="INVALID_POINT_FORMAT") from exc

        if pts < 0:
            raise HourlySubmissionError(
                "Point value must be 0 or a positive whole number. Negative points are not permitted.",
                status_code=400,
                code="NEGATIVE_POINTS_REJECTED",
            )

        max_limit = self.get_configured_hourly_limit()
        if pts > max_limit:
            raise HourlySubmissionError(
                f"Points to award ({pts}) exceeds the configured limit of {max_limit} points.",
                status_code=400,
                code="POINT_LIMIT_EXCEEDED",
            )

        with self.db._connect() as conn:
            act_row = conn.execute(
                "SELECT * FROM scheduled_activities WHERE activity_id = ? AND account_id = ? AND type = 'HOURLY'",
                (activity_id, account_id),
            ).fetchone()
            if not act_row:
                raise HourlySubmissionError(f"Hourly checkpoint '{activity_id}' not found.", status_code=404, code="OCCURRENCE_NOT_FOUND")
            act = dict(act_row)

            # Resolve evidence ID
            evidence_ids = []
            try:
                evidence_ids = json.loads(act.get("evidence_ids_json") or "[]")
            except Exception:
                pass
            evidence_id = evidence_ids[0] if evidence_ids else None

            # Check review state
            pr_row = None
            if evidence_id:
                pr_row = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()

        review_status = pr_row["status"] if pr_row else act.get("status")

        # Unapproved proofs cannot be awarded points without an explicit authorized approval decision
        if review_status not in ("APPROVED", "VERIFIED") and act.get("status") not in ("APPROVED", "VERIFIED"):
            raise HourlySubmissionError(
                "Unapproved proofs cannot be awarded points without an explicit authorized approval decision.",
                status_code=400,
                code="PROOF_NOT_APPROVED",
            )

        current_confirmed = int(act.get("points") or (pr_row["points_awarded"] if pr_row and pr_row["points_awarded"] is not None else 0) or 0)

        # Idempotent check: if points already equal the requested award
        if current_confirmed == pts:
            score_snapshot = self.engine.recalculate_daily_score(act["date"], is_dev_mode=is_dev_mode)
            return {
                "success": True,
                "already_applied": True,
                "activity_id": activity_id,
                "points_awarded": pts,
                "previous_points": current_confirmed,
                "daily_balance": score_snapshot.get("final_score", 0),
                "message": "Award value matches current confirmed ledger record. No duplicate points applied.",
            }

        # Require explicit confirmation when changing an existing positive award
        if current_confirmed > 0 and not confirm_change:
            return {
                "success": False,
                "requires_confirmation": True,
                "activity_id": activity_id,
                "current_points": current_confirmed,
                "proposed_points": pts,
                "diff": pts - current_confirmed,
                "message": f"Changing existing award from {current_confirmed} to {pts} points (Diff: {pts - current_confirmed:+d}) requires explicit administrator confirmation.",
            }

        # Ledger transaction execution
        now_iso = dt_to_iso(now_ist())
        target_date = act["date"]
        dev_flag = 1 if is_dev_mode else 0
        diff = pts - current_confirmed
        tx_id = f"tx_award_{activity_id}_{uuid.uuid4().hex[:10]}"

        with self.db._connect() as conn:
            # Find parent approval event if adjusting
            parent_event_id: Optional[str] = None
            if evidence_id:
                ev_row = conn.execute(
                    "SELECT event_id FROM score_events WHERE evidence_id = ? AND points > 0 ORDER BY timestamp DESC LIMIT 1",
                    (evidence_id,),
                ).fetchone()
                if ev_row:
                    parent_event_id = ev_row["event_id"]

        if current_confirmed == 0:
            # Initial award
            score_ev = self.engine.record_score_event(
                date=target_date,
                event_type=ScoreEventType.PROOF_APPROVED.value,
                points=pts,
                device_id="windows_admin",
                description=f"Hourly attendance award for {activity_id}: +{pts} points",
                evidence_id=evidence_id,
                related_task_id=f"HOURLY_{activity_id}",
                audit_details={
                    "admin_user": admin_user,
                    "activity_id": activity_id,
                    "reason": reason or "Administrator point award",
                    "points": pts,
                    "previous_points": 0,
                    "tx_id": tx_id,
                },
                is_dev_mode=is_dev_mode,
                timestamp=now_iso,
            )
        else:
            # Point adjustment / correction
            event_type = ScoreEventType.ADMIN_SCORE_ADJUSTMENT.value if diff > 0 else ScoreEventType.SCORE_CORRECTION.value
            score_ev = self.engine.record_score_event(
                date=target_date,
                event_type=event_type,
                points=diff,
                device_id="windows_admin",
                description=f"Admin point correction for {activity_id}: {diff:+d} points ({reason or 'Administrative adjustment'})",
                evidence_id=evidence_id,
                related_task_id=f"HOURLY_{activity_id}",
                parent_event_id=parent_event_id,
                audit_details={
                    "admin_user": admin_user,
                    "activity_id": activity_id,
                    "reason": reason or "Administrative correction",
                    "previous_points": current_confirmed,
                    "new_points": pts,
                    "delta": diff,
                    "tx_id": tx_id,
                },
                is_dev_mode=is_dev_mode,
                timestamp=now_iso,
            )

        # Update activity and review records
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE scheduled_activities SET points = ?, status = 'VERIFIED', validation_status = 'VERIFIED' WHERE activity_id = ?",
                (pts, activity_id),
            )
            if evidence_id:
                conn.execute(
                    "UPDATE proof_reviews SET points_awarded = ?, status = 'APPROVED', decision = 'APPROVED', reviewed_by = ?, reviewed_at = ? WHERE evidence_id = ?",
                    (pts, admin_user, now_iso, evidence_id),
                )

        # Refresh authoritative balance
        daily_score = self.engine.recalculate_daily_score(target_date, is_dev_mode=is_dev_mode)

        # Record admin audit trail
        audit_res = self.db.record_admin_audit(
            admin_user=admin_user,
            action_type=AdminActionType.POINT_ADJUSTED.value if current_confirmed > 0 else AdminActionType.PROOF_APPROVED.value,
            field_changed=f"activity:{activity_id}:points",
            old_value={"points": current_confirmed},
            new_value={"points": pts, "diff": diff, "reason": reason},
            device_id="windows_admin",
            timestamp=now_iso,
        )

        # Emit sync event to Android
        sync_payload = {
            "event_id": f"evt_award_{uuid.uuid4().hex[:12]}",
            "event_type": "ADMIN_REVIEW_COMPLETED",
            "activity_id": activity_id,
            "evidence_id": evidence_id,
            "status": "APPROVED",
            "action": "APPROVE",
            "points": pts,
            "admin_id": admin_user,
            "reason_note": reason or f"Awarded {pts} points by administrator",
            "accepted_at": now_iso,
            "transaction_id": tx_id,
            "shared_score": daily_score.get("final_score", 0),
        }
        sync_sub_id = f"sync_award_{uuid.uuid4().hex[:12]}"
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO proof_submission_events (
                    event_id, account_id, submission_id, activity_id,
                    schedule_version, source_device_id, accepted_at, status, payload_json
                ) VALUES (?, ?, ?, ?, 1, 'windows_admin', ?, 'APPROVED', ?)""",
                (sync_payload["event_id"], account_id, sync_sub_id, activity_id, now_iso, json.dumps(sync_payload)),
            )

        return {
            "success": True,
            "activity_id": activity_id,
            "points_awarded": pts,
            "previous_points": current_confirmed,
            "diff": diff,
            "daily_balance": daily_score.get("final_score", 0),
            "transaction_id": tx_id,
            "audit_id": audit_res["audit_id"],
            "timestamp": now_iso,
        }
