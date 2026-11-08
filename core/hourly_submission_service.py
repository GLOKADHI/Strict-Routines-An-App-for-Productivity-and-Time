"""Authoritative acceptance and durable event storage for hourly proof submissions."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.models import HourlyActivityCategory
from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist


MAX_HOURLY_EVIDENCE_BYTES = 10 * 1024 * 1024
_CATEGORY_ALIASES = {
    "PRODUCTIVE WORK": "PRODUCTIVE",
    "DEEP WORK": "PROJECT",
    "DEEP WORK / PROJECT": "PROJECT",
    "DEEP WORK/PROJECT": "PROJECT",
    "CODING / PROGRAMMING": "CODING",
    "CODING/PROGRAMMING": "CODING",
    "PROGRAMMING": "CODING",
    "ACADEMIC STUDY": "STUDY",
    "ACADEMIC": "STUDY",
    "EXERCISE / WORKOUT": "EXERCISE",
    "EXERCISE/WORKOUT": "EXERCISE",
    "WORKOUT": "EXERCISE",
    "BOOK READING": "READING",
    "WASTED": "WASTED TIME",
}
_ALLOWED_CATEGORIES = {item.value for item in HourlyActivityCategory} | {"OTHER"}
_EXTENSION_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}
_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class HourlySubmissionError(ValueError):
    def __init__(self, message: str, status_code: int = 422, code: str = "INVALID_SUBMISSION"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class HourlySubmissionService:
    """Persists evidence, the activity transition, and its delivery event as one acceptance."""

    def __init__(self, db: Any, engine: Any, storage_root: Optional[str] = None):
        self.db = db
        self.engine = engine
        self.storage_root = storage_root or engine.storage_root

    @staticmethod
    def _safe_write_evidence_file(temp_path: str, final_path: str, raw: bytes) -> None:
        """Resiliently write evidence to disk on Windows.
        Handles transient antivirus/search indexer read locks that cause MoveFile/os.replace
        to fail with [WinError 5] Access is denied.
        """
        import stat
        target_dir = os.path.dirname(final_path)
        os.makedirs(target_dir, exist_ok=True)

        # Quick check: if destination already exists and matches payload exactly, succeed immediately
        if os.path.isfile(final_path):
            try:
                if os.path.getsize(final_path) == len(raw):
                    with open(final_path, "rb") as existing_fp:
                        if hashlib.sha256(raw).hexdigest() == hashlib.sha256(existing_fp.read()).hexdigest():
                            return
            except Exception:
                pass
            try:
                os.chmod(final_path, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass

        # 1. Write the raw bytes to temp_path with protection against existing read-only attributes
        wrote_temp = False
        try:
            if os.path.exists(temp_path):
                try:
                    os.chmod(temp_path, stat.S_IWRITE | stat.S_IREAD)
                except Exception:
                    pass
            with open(temp_path, "wb") as evidence_file:
                evidence_file.write(raw)
                evidence_file.flush()
            wrote_temp = True
        except (PermissionError, OSError):
            pass

        # 2. Try atomic replace with progressive retries
        replaced = False
        if wrote_temp:
            for attempt in range(6):
                try:
                    os.replace(temp_path, final_path)
                    replaced = True
                    break
                except (PermissionError, OSError):
                    time.sleep(0.04 * (attempt + 1))

        if not replaced and wrote_temp:
            # 3. Fallback: shutil.copyfile does not require DELETE permission on source
            try:
                if os.path.isfile(temp_path):
                    shutil.copyfile(temp_path, final_path)
                    replaced = True
            except Exception:
                pass

        if not replaced:
            # 4. Fallback: direct write to final_path
            try:
                if os.path.exists(final_path):
                    try:
                        os.chmod(final_path, stat.S_IWRITE | stat.S_IREAD)
                    except Exception:
                        pass
                with open(final_path, "wb") as direct_file:
                    direct_file.write(raw)
                    direct_file.flush()
                replaced = True
            except Exception:
                pass

        # 5. Clean up temp_path safely
        if os.path.isfile(temp_path) and os.path.abspath(temp_path) != os.path.abspath(final_path):
            for _ in range(3):
                try:
                    os.chmod(temp_path, stat.S_IWRITE | stat.S_IREAD)
                    os.remove(temp_path)
                    break
                except OSError:
                    time.sleep(0.05)

        # 6. Verify written file integrity
        if not os.path.isfile(final_path) or os.path.getsize(final_path) != len(raw):
            raise OSError(f"Evidence file could not be written to {final_path}")

    @staticmethod
    def normalize_category(value: Any) -> str:
        raw_val = str(value or "").strip().upper()
        category = _CATEGORY_ALIASES.get(raw_val, raw_val)
        if category in _ALLOWED_CATEGORIES:
            return category
        # Multi-category or composite selection fallback (e.g. "Coding / Programming" or "Study, Project")
        for separator in ('/', ',', ';', '&', '+'):
            if separator in raw_val:
                parts = [p.strip() for p in raw_val.split(separator) if p.strip()]
                for part in parts:
                    mapped = _CATEGORY_ALIASES.get(part, part)
                    if mapped in _ALLOWED_CATEGORIES:
                        return mapped
        if category not in _ALLOWED_CATEGORIES:
            raise HourlySubmissionError("Select an activity category.", code="CATEGORY_REQUIRED")
        return category

    @staticmethod
    def validate_evidence(file_bytes: Optional[bytes], filename: str = "") -> Dict[str, str]:
        if not file_bytes:
            raise HourlySubmissionError(
                "Attach at least one image or supported file.", code="ATTACHMENT_REQUIRED"
            )
        if len(file_bytes) > MAX_HOURLY_EVIDENCE_BYTES:
            raise HourlySubmissionError(
                f"Evidence file exceeds the {MAX_HOURLY_EVIDENCE_BYTES // (1024 * 1024)} MB limit.",
                status_code=413,
                code="FILE_TOO_LARGE",
            )

        try:
            from PIL import Image

            with Image.open(io.BytesIO(file_bytes)) as image:
                detected_format = str(image.format or "").upper()
                width, height = image.size
                image.verify()
            with Image.open(io.BytesIO(file_bytes)) as image:
                image.load()
        except Exception as exc:
            raise HourlySubmissionError(
                "Evidence file is empty, corrupted, or not a supported image.", code="INVALID_FILE"
            ) from exc

        if detected_format not in _FORMAT_EXTENSIONS:
            raise HourlySubmissionError(
                "Supported evidence formats are JPEG, PNG, and WebP images.", code="UNSUPPORTED_FILE"
            )
        if width < 100 or height < 100:
            raise HourlySubmissionError(
                "Evidence image must be at least 100 × 100 pixels.", code="IMAGE_TOO_SMALL"
            )

        safe_name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        extension = Path(safe_name).suffix.lower()
        if extension and _EXTENSION_FORMATS.get(extension) != detected_format:
            raise HourlySubmissionError(
                "Evidence filename extension does not match the actual file content.", code="FILE_TYPE_MISMATCH"
            )
        return {
            "extension": extension or _FORMAT_EXTENSIONS[detected_format],
            "detected_format": detected_format,
            "mime_type": {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[detected_format],
            "filename": safe_name or f"evidence{_FORMAT_EXTENSIONS[detected_format]}",
        }

    def accept(
        self,
        payload: Dict[str, Any],
        *,
        file_bytes: Optional[bytes],
        filename: str,
        account_id: str,
        device_id: str,
        platform: str,
        received_at: Optional[datetime] = None,
        expected_type: str = "HOURLY",
    ) -> Dict[str, Any]:
        expected_type = str(expected_type).upper()
        if expected_type not in {"HOURLY", "BATH", "GYM"}:
            raise HourlySubmissionError("Unsupported proof type.", code="INVALID_TASK_TYPE")
        record_type = {"HOURLY": "Hourly", "BATH": "Bath", "GYM": "Gym"}[expected_type]
        # Bath/Gym evidence uses its own task review, not an hourly activity
        # category or a Taufa score. The occurrence type is checked below.
        category = (self.normalize_category(payload.get("category") or payload.get("primary_activity"))
                    if expected_type == "HOURLY" else expected_type)
        description = str(payload.get("description") or "").strip()
        if not description:
            raise HourlySubmissionError(
                "Describe what you did during the last hour.", code="DESCRIPTION_REQUIRED"
            )
        from core.taufa_ai_validator import TaufaAiValidator
        desc_ok, desc_err = TaufaAiValidator.validate_description(description)
        if not desc_ok:
            raise HourlySubmissionError(desc_err, code="INVALID_DESCRIPTION")
        bundle = []
        raw_items = payload.get("attachments") if expected_type == "HOURLY" else None
        if raw_items is not None:
            if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 10:
                raise HourlySubmissionError("Select between 1 and 10 photos.", code="INVALID_ATTACHMENTS")
            for item in raw_items:
                if not isinstance(item, dict) or not isinstance(item.get("image_base64"), str):
                    raise HourlySubmissionError("An attachment was not fully uploaded.", code="MISSING_ATTACHMENT")
                try:
                    data = base64.b64decode(item["image_base64"].split(",")[-1], validate=True)
                except (ValueError, base64.binascii.Error) as error:
                    raise HourlySubmissionError("An attachment is corrupted.", code="INVALID_FILE_ENCODING") from error
                bundle.append((data, self.validate_evidence(data, str(item.get("filename") or "evidence.jpg"))))
            if file_bytes and hashlib.sha256(file_bytes).digest() != hashlib.sha256(bundle[0][0]).digest():
                raise HourlySubmissionError("The primary evidence does not match its attachment.", code="ATTACHMENT_MISMATCH")
            file_bytes, file_info = bundle[0]
        else:
            file_info = self.validate_evidence(file_bytes, filename)
            bundle = [(bytes(file_bytes or b""), file_info)]
        try:
            image_count = int(payload.get("image_count") or len(bundle))
        except (TypeError, ValueError) as error:
            raise HourlySubmissionError("Image count is invalid.", code="INVALID_IMAGE_COUNT") from error
        if not 1 <= image_count <= 10:
            raise HourlySubmissionError("Maximum 10 photos are supported.", code="EXCEEDED_MAX_ATTACHMENTS")
        if image_count != len(bundle):
            raise HourlySubmissionError("Not all selected photos were uploaded. Retry the retained submission.", code="MISSING_ATTACHMENTS")
        if sum(len(data) for data, _ in bundle) > 125 * 1024 * 1024:
            raise HourlySubmissionError("Combined photos exceed the 125 MB evidence budget.", code="BATCH_TOO_LARGE")

        activity_id = str(payload.get("activity_id") or "").strip()
        if not activity_id:
            raise HourlySubmissionError("No hourly checkpoint was selected.", code="ACTIVITY_REQUIRED")
        submission_id = str(payload.get("submission_id") or f"sub_{uuid.uuid4().hex}").strip()
        if len(submission_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", submission_id):
            raise HourlySubmissionError("Submission ID is invalid.", code="INVALID_SUBMISSION_ID")

        received = received_at or now_ist()
        if received.tzinfo is None:
            received = received.replace(tzinfo=IST)
        received = received.astimezone(IST)
        accepted_at = dt_to_iso(received.astimezone(IST))
        schedule_version = payload.get("schedule_version")
        try:
            requested_version = int(schedule_version) if schedule_version not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HourlySubmissionError("Schedule version is invalid.", code="INVALID_SCHEDULE_VERSION") from exc

        raw = bytes(file_bytes or b"")
        sha256_hash = hashlib.sha256(raw).hexdigest()
        # Verify client-provided SHA-256 when present to catch transmission corruption
        client_sha256 = str(payload.get("evidence_sha256") or "").strip().lower()
        if client_sha256 and client_sha256 != sha256_hash:
            raise HourlySubmissionError(
                "Evidence checksum mismatch: the uploaded image was corrupted in transit. Resubmit the original photo.",
                code="CHECKSUM_MISMATCH"
            )
        evidence_id = f"ev_{uuid.uuid4().hex[:20]}"
        event_id = f"proof_submitted_{submission_id}"
        date_str = ""
        is_dev_mode = bool(payload.get("is_dev_mode", False))
        dev_flag = 1 if is_dev_mode else 0
        final_path = ""
        temp_path = ""
        bundle_paths = []
        attachment_ids = [evidence_id] + [f"{evidence_id}_{index}" for index in range(1, len(bundle))]
        accepted_payload: Dict[str, Any]
        from core.daily_routine_gate import DailyRoutineGate
        gate_service = DailyRoutineGate(self.db)
        with self.db._connect() as gate_connection:
            gate_activity = gate_connection.execute("SELECT date FROM scheduled_activities WHERE activity_id=? AND account_id=?", (activity_id, account_id)).fetchone()
        if not gate_activity:
            raise HourlySubmissionError("Occurrence not found for this account.", status_code=404, code="ACTIVITY_NOT_FOUND")
        gate_date = gate_activity["date"]
        gate = gate_service.reconcile(gate_date, account_id, received, is_dev_mode)

        try:
            with self.db._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                from core.daily_college_decision import DailyCollegeDecision
                DailyCollegeDecision.reconcile_bath_reservations(conn,account_id,gate_date,received)
                activity = conn.execute(
                    "SELECT * FROM scheduled_activities WHERE activity_id = ? AND account_id = ?",
                    (activity_id, account_id),
                ).fetchone()
                if not activity:
                    raise HourlySubmissionError(
                        "The selected hourly checkpoint was not found for this account.",
                        status_code=404,
                        code="ACTIVITY_NOT_FOUND",
                    )
                act = dict(activity)
                # Reuse this same proof pipeline, but the bound Strict Session
                # occurrence must come from its selected Android source.
                # Other hourly occurrences and ordinary Windows uploads retain
                # their existing behavior.
                strict_binding = None
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strict_study_sessions'").fetchone():
                    bound = conn.execute("""SELECT data_json FROM strict_study_sessions WHERE account_id=?
                        AND state NOT IN ('SESSION_FINALIZED','REWARD_ACTIVE','FAILED','ABANDONED')
                        AND json_extract(data_json,'$.window.activity_id')=?""", (account_id,activity_id)).fetchone()
                    strict_binding = json.loads(bound[0]) if bound else None
                if strict_binding:
                    if platform.upper() != 'ANDROID' or device_id != strict_binding['android_device_id']:
                        raise HourlySubmissionError('Submit this Strict Session proof using its selected paired Android device.',
                            status_code=409,code='STRICT_SESSION_ANDROID_REQUIRED')
                    if not strict_binding.get('focus_completed_at'):
                        raise HourlySubmissionError('Complete the Strict Session study phase before submitting its proof.',
                            status_code=409,code='STRICT_SESSION_FOCUS_PENDING')
                if str(act.get("type", "")).upper() != expected_type:
                    raise HourlySubmissionError("Proof type does not match the selected occurrence.", code="NOT_HOURLY" if expected_type == "HOURLY" else "TASK_TYPE_MISMATCH")

                if requested_version is not None and requested_version != int(act.get("schedule_version") or 1):
                    raise HourlySubmissionError(
                        "This checkpoint schedule changed. Refresh and submit the current checkpoint.",
                        status_code=409,
                        code="SCHEDULE_CHANGED",
                    )

                existing_by_id = conn.execute(
                    "SELECT * FROM proof_submission_events WHERE submission_id = ?",
                    (submission_id,),
                ).fetchone()
                if existing_by_id:
                    old = json.loads(existing_by_id["payload_json"] or "{}")
                    if old.get("activity_id") != activity_id or old.get("account_id") != account_id:
                        raise HourlySubmissionError(
                            "Submission ID is already attached to another checkpoint.",
                            status_code=409,
                            code="SUBMISSION_ID_CONFLICT",
                        )
                    current_hashes = [hashlib.sha256(data).hexdigest() for data, _ in bundle]
                    previous_hashes = old.get("attachment_sha256s") or [old.get("evidence_sha256")]
                    if previous_hashes != current_hashes:
                        raise HourlySubmissionError("This submission ID already refers to different evidence.", status_code=409, code="SUBMISSION_ID_CONFLICT")
                    old.update({"status": act["status"], "validation_status": act.get("validation_status") or "PROCESSING"})
                    old.update({"success": True, "accepted": True, "deduplicated": True})
                    return old

                status = str(act.get("status") or "").upper()
                if expected_type == "HOURLY" and not DailyRoutineGate.activity_is_eligible(act, gate, reference_now=received, for_submission=True):
                    raise HourlySubmissionError(
                        "This hourly occurrence is covered by an exception or is unavailable for submission.",
                        status_code=409, code="CHECKPOINT_INACTIVE")
                try:
                    window_start = iso_to_dt(act["window_start"])
                    window_end = iso_to_dt(act["window_end"])
                except (TypeError, ValueError) as exc:
                    raise HourlySubmissionError(
                        "The checkpoint window is invalid; submission was not accepted.",
                        status_code=500,
                        code="INVALID_WINDOW",
                    ) from exc
                from core.time_window_engine import resolve_schedule_timezone
                if str(act.get("date") or "") != received.astimezone(resolve_schedule_timezone(gate.get("timezone") or "Asia/Kolkata")).strftime("%Y-%m-%d"):
                    raise HourlySubmissionError(
                        "The recorded capture date does not match this checkpoint date.",
                        status_code=409,
                        code="SUBMISSION_DATE_MISMATCH",
                    )
                if received < window_start:
                    raise HourlySubmissionError(
                        f"This activity opens at {window_start.strftime('%I:%M %p').lstrip('0')}.",
                        status_code=409,
                        code="WINDOW_NOT_OPEN",
                    )
                if received >= window_end:
                    raise HourlySubmissionError(
                        "Activity window expired. Late submissions cannot be accepted.",
                        status_code=409,
                        code="WINDOW_CLOSED",
                    )
                if status in {"MISSED", "CLOSED", "REJECTED_LATE", "SKIPPED", "CANCELLED", "EXCUSED"}:
                    raise HourlySubmissionError(
                        "The submission window has closed.", status_code=409, code="WINDOW_CLOSED"
                    )

                is_bath = expected_type == "BATH" or "BATH" in activity_id
                if is_bath:
                    is_bath_1 = "-BATH-1" in activity_id or act.get("occurrence") == 1 or "BATH-2" not in activity_id
                    if is_bath_1:
                        from core.daily_college_decision import DailyCollegeDecision
                        college_rec = DailyCollegeDecision.read(conn, account_id, gate_date)
                        attends_college = college_rec.get("attends_college")
                        if attends_college is None:
                            raise HourlySubmissionError(
                                "Daily routine setup required. Please confirm today's schedule first.",
                                status_code=403,
                                code="DAILY_SETUP_REQUIRED",
                            )
                        if attends_college and not (gate.get("active") or gate.get("prerequisite_completed")):
                            raise HourlySubmissionError(
                                "Complete Wake-Up Verification first. Today's required wake-up verification is still pending. Complete it before proceeding with this activity.",
                                status_code=403,
                                code="WAKEUP_VERIFICATION_REQUIRED",
                            )

                is_resubmission = bool(
                    payload.get("allow_resubmission")
                    or payload.get("is_resubmission")
                    or payload.get("resubmit")
                    or str(payload.get("mode", "")).upper() == "RESUBMISSION"
                    or str(payload.get("action", "")).upper() in ("RESUBMIT", "SUBMIT_ANOTHER")
                )
                if status in {"SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED"}:
                    if not is_resubmission:
                        raise HourlySubmissionError(
                            "A submission for this hourly checkpoint was already received.",
                            status_code=409,
                            code="DUPLICATE_SUBMISSION",
                        )

                date_str = str(act["date"])
                slot_details: Dict[str, Any] = {}
                try:
                    slot_details = json.loads(act.get("details_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
                hour_slot = str(slot_details.get("hour_slot") or "")
                if not hour_slot:
                    checkpoint = str(act.get("scheduled_time") or "")
                    try:
                        checkpoint_dt = iso_to_dt(checkpoint)
                        start_label = (checkpoint_dt.replace(minute=0, second=0, microsecond=0)).strftime("%H:%M")
                        end_label = (checkpoint_dt.replace(minute=0, second=0, microsecond=0)).replace(hour=(checkpoint_dt.hour + 1) % 24).strftime("%H:%M")
                        hour_slot = f"{start_label}-{end_label}"
                    except (TypeError, ValueError):
                        hour_slot = checkpoint[11:16] or "HOURLY"
                end_hour = hour_slot.split("-")[-1].split(":")[0]
                hourly_id = f"hourly_{date_str}_{dev_flag}_{end_hour}"
                source_name = "Windows Workstation" if platform.upper() == "WINDOWS" else "Android Device"
                slot_details.update({
                    "hour_slot": hour_slot,
                    "activity_category": category,
                    "activity_description": description,
                    "submission_id": submission_id,
                    "evidence_id": evidence_id,
                    "evidence_sha256": sha256_hash,
                    "source_device_id": device_id,
                    "source_platform": platform.upper(),
                    "accepted_at": accepted_at,
                    "ai_validation_state": "PENDING",
                })
                day_folder = f"{date_str}_DEV" if is_dev_mode else date_str
                target_dir = os.path.join(self.storage_root, day_folder, record_type)
                os.makedirs(target_dir, exist_ok=True)
                final_path = os.path.join(target_dir, f"{evidence_id}{file_info['extension']}")
                temp_path = final_path + ".uploading"
                self._safe_write_evidence_file(temp_path, final_path, raw)
                temp_path = ""

                from core.ai_robot_admin import StrictBotAIReviewer
                dhash_val = StrictBotAIReviewer.compute_dhash(raw)
                ahash_val = StrictBotAIReviewer.compute_ahash(raw)
                evidence_metadata = {
                    "task_type": expected_type,
                    "hour_slot": hour_slot,
                    "activity_id": activity_id,
                    "submission_id": submission_id,
                    "category": category,
                    "source_platform": platform.upper(),
                    "original_filename": file_info["filename"],
                    "detected_mime_type": file_info["mime_type"],
                    "verified_non_empty": True,
                    "sha256": sha256_hash,
                    "dhash": dhash_val,
                    "ahash": ahash_val,
                }
                conn.execute(
                    "INSERT INTO evidence_files (evidence_id, date, is_dev_mode, record_type, related_record_id, file_path, timestamp, device_id, file_size_bytes, sha256_hash, metadata_json, is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (evidence_id, date_str, dev_flag, record_type, submission_id, final_path, accepted_at, device_id, len(raw), sha256_hash, json.dumps(evidence_metadata)),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO activity_journal_events (
                        event_id, account_id, date, event_type, provenance,
                        timestamp, timezone, source, activity_id, submission_id,
                        evidence_id, category, description, details_json
                    ) VALUES (?, ?, ?, ?, 'USER_REPORTED', ?, 'Asia/Kolkata', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"aj_rep_{submission_id}",
                        account_id,
                        date_str,
                        "HOURLY_ACTIVITY_REPORTED" if expected_type == "HOURLY" else "ACTIVITY_PROOF_REPORTED",
                        accepted_at,
                        f"{platform.upper()}:{device_id}",
                        activity_id,
                        submission_id,
                        evidence_id,
                        category,
                        description,
                        json.dumps({"hour_slot": hour_slot, "source_platform": platform.upper()}),
                    ),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO activity_journal_events (
                        event_id, account_id, date, event_type, provenance,
                        timestamp, timezone, source, activity_id, submission_id,
                        evidence_id, category, description, details_json
                    ) VALUES (?, ?, ?, 'EVIDENCE_FILE_SUBMITTED', 'EVIDENCE_SUBMITTED', ?, 'Asia/Kolkata', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"aj_ev_{evidence_id}",
                        account_id,
                        date_str,
                        accepted_at,
                        f"{platform.upper()}:{device_id}",
                        activity_id,
                        submission_id,
                        evidence_id,
                        category,
                        file_info["filename"],
                        json.dumps({
                            "sha256": sha256_hash,
                            "dhash": dhash_val,
                            "ahash": ahash_val,
                            "mime_type": file_info["mime_type"],
                            "size_bytes": len(raw),
                        }),
                    ),
                )

                # Persist every photo in the same acceptance transaction as the occurrence.
                for index, (attachment_bytes, attachment_info) in enumerate(bundle[1:], 1):
                    attachment_id = attachment_ids[index]
                    attachment_path = os.path.join(target_dir, f"{attachment_id}{attachment_info['extension']}")
                    attachment_temp = attachment_path + ".uploading"
                    bundle_paths.extend([attachment_temp, attachment_path])
                    self._safe_write_evidence_file(attachment_temp, attachment_path, attachment_bytes)
                    attachment_sha = hashlib.sha256(attachment_bytes).hexdigest()
                    metadata = {**evidence_metadata, "sha256": attachment_sha,
                        "dhash": StrictBotAIReviewer.compute_dhash(attachment_bytes),
                        "ahash": StrictBotAIReviewer.compute_ahash(attachment_bytes),
                        "original_filename": attachment_info["filename"],
                        "detected_mime_type": attachment_info["mime_type"],
                        "image_index": index, "attachment_count": len(bundle)}
                    conn.execute(
                        "INSERT INTO evidence_files (evidence_id,date,is_dev_mode,record_type,related_record_id,file_path,timestamp,device_id,file_size_bytes,sha256_hash,metadata_json,is_deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                        (attachment_id,date_str,dev_flag,record_type,submission_id,attachment_path,accepted_at,
                         device_id,len(attachment_bytes),attachment_sha,json.dumps(metadata)))
                evidence_metadata.update({"image_index": 0, "attachment_count": len(bundle), "attachment_ids": attachment_ids})
                conn.execute("UPDATE evidence_files SET metadata_json=? WHERE evidence_id=?", (json.dumps(evidence_metadata), evidence_id))
                slot_details["evidence_attachment_ids"] = attachment_ids
                existing_evidence = []
                try:
                    if act.get("evidence_ids_json"):
                        existing_evidence = json.loads(act["evidence_ids_json"])
                except Exception:
                    pass
                for attachment_id in attachment_ids:
                    if attachment_id not in existing_evidence:
                        existing_evidence.append(attachment_id)

                prev_points = int(act.get("points") or 0)
                keep_points = prev_points if status in ("VERIFIED", "COMPLETED") else 0
                new_status = "VALIDATING"

                details_json = json.dumps(slot_details)
                cur = conn.execute(
                    """UPDATE scheduled_activities
                    SET status = ?, submitted_at = ?, points = ?,
                        attendance_status = 'PRESENT', validation_status = 'PROCESSING',
                        submission_id = ?, description = ?, evidence_ids_json = ?, details_json = ?
                    WHERE activity_id = ? AND account_id = ?
                      AND status NOT IN ('MISSED','CLOSED','REJECTED_LATE','SKIPPED','CANCELLED','EXCUSED')""",
                    (new_status, accepted_at, keep_points, submission_id, description,
                     json.dumps(existing_evidence), details_json, activity_id, account_id),
                )
                if cur.rowcount != 1:
                    raise HourlySubmissionError(
                        "This hourly checkpoint is not eligible for submission.",
                        status_code=409,
                        code="SUBMISSION_NOT_ALLOWED",
                    )

                if expected_type == "HOURLY":
                    conn.execute(
                        """INSERT INTO hourly_records (
                            hourly_id, date, is_dev_mode, hour_slot, checkpoint_time, primary_activity,
                            secondary_activities_json, description, evidence_id, verification_result,
                            integrity_flag, status, submitted_at, device_id
                        ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, 'PENDING_REVIEW', 'PENDING_EVIDENCE', ?, ?)
                        ON CONFLICT(date, is_dev_mode, hour_slot) DO UPDATE SET
                            primary_activity = excluded.primary_activity,
                            description = excluded.description,
                            evidence_id = excluded.evidence_id,
                            verification_result = excluded.verification_result,
                            integrity_flag = excluded.integrity_flag,
                            status = excluded.status,
                            submitted_at = excluded.submitted_at,
                            device_id = excluded.device_id""",
                        (hourly_id, date_str, dev_flag, hour_slot, act["scheduled_time"], category, description,
                         evidence_id, f"Proof received for {category.title()}; awaiting review", accepted_at, device_id),
                    )

                event_payload = {
                    "event_id": event_id,
                    "event_type": "PROOF_SUBMITTED",
                    "task_type": expected_type,
                    "configured_points": int(act.get("configured_points") or act.get('points') or 0),
                    "submission_id": submission_id,
                    "activity_id": activity_id,
                    "account_id": account_id,
                    "schedule_version": int(act.get("schedule_version") or 1),
                    "source_device_id": device_id,
                    "source_device_name": source_name,
                    "source_platform": platform.upper(),
                    "accepted_at": accepted_at,
                    "status": "SUBMITTED",
                    "attendance_status": "PRESENT",
                    "validation_status": "PROCESSING",
                    "category": category,
                    "description": description,
                    "evidence_id": evidence_id,
                    "evidence_sha256": sha256_hash,
                    "evidence_filename": file_info["filename"],
                    "evidence_ids": attachment_ids,
                    "image_count": len(bundle),
                    "attachment_sha256s": [hashlib.sha256(data).hexdigest() for data, _ in bundle],
                    "evidence_mime_type": file_info["mime_type"],
                    "evidence_size_bytes": len(raw),
                    "window_start": act.get("window_start"),
                    "window_end": act.get("window_end"),
                    "hour_slot": hour_slot,
                    "has_evidence": True,
                    "reconciled_offline": False,
                }
                if strict_binding:
                    event_payload['strict_session_id'] = strict_binding['session_id']
                conn.execute(
                    "INSERT INTO proof_submission_events (event_id, account_id, submission_id, activity_id, schedule_version, source_device_id, accepted_at, status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'SUBMITTED', ?)",
                    (event_id, account_id, submission_id, activity_id, int(act.get("schedule_version") or 1), device_id, accepted_at, json.dumps(event_payload)),
                )

                accepted_payload = {
                    **event_payload,
                    "success": True,
                    "accepted": True,
                    "submitted_at": accepted_at,
                    "message": "Proof accepted and saved. AI review is processing.",
                }
        except Exception:
            for path in (temp_path, final_path, *bundle_paths):
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise

        try:
            self.db.ensure_proof_review_record(
                evidence_id=evidence_id,
                user_id=account_id,
                task_code=category.upper() if expected_type == "HOURLY" else expected_type,
                date=date_str,
                hour_slot=hour_slot,
                signals={"sha256": sha256_hash, "device_id": device_id, "category": category, "description": description,"activity_id":activity_id,"submission_id":submission_id},
                status="AI_QUEUED",
            )
        except Exception:
            # The accepted record and evidence are durable. AI/admin processing can retry separately.
            pass
        return accepted_payload

    def publish_review_event(
        self,
        account_id: str,
        activity_id: str,
        evidence_id: str,
        action: str,
        status: str,
        points: int,
        admin_id: str,
        reason_note: str = "",
        shared_score: int = 0,
    ) -> str:
        now_iso = dt_to_iso(now_ist())
        event_id = f"rev_evt_{uuid.uuid4().hex[:12]}"
        review_sub_id = f"rev_{evidence_id}_{int(time.time())}"
        payload = {
            "event_id": event_id,
            "event_type": "ADMIN_REVIEW_COMPLETED",
            "evidence_id": evidence_id,
            "activity_id": activity_id,
            "action": action,
            "status": status,
            "points": points,
            "admin_id": admin_id,
            "reason_note": reason_note,
            "shared_score": shared_score,
            "accepted_at": now_iso,
        }
        with self.db._connect() as conn:
            review = conn.execute("SELECT signals_json,date,reviewed_by FROM proof_reviews WHERE evidence_id=? AND user_id=?", (evidence_id,account_id)).fetchone()
            if review:
                signals = json.loads(review["signals_json"] or "{}")
                payload["date"] = review["date"]
                payload["reviewed_by"] = review["reviewed_by"]
                if isinstance(signals.get("taufa_ai"), dict):
                    payload["validation_result"] = signals["taufa_ai"]
            # Carry the original durable acceptance, not this review event's
            # timestamp. Android remains the enrolled reward-attesting authority.
            for original in conn.execute("SELECT payload_json FROM proof_submission_events WHERE account_id=? AND activity_id=? AND status='SUBMITTED' ORDER BY accepted_at", (account_id, activity_id)):
                accepted = json.loads(original['payload_json'])
                if accepted.get('evidence_id') != evidence_id or accepted.get('task_type') != 'HOURLY': continue
                validation = payload.get('validation_result') or {}
                duplicate = validation.get('duplicate_result') or {}
                payload['youtube_reward_submission'] = dict(
                    account_id=account_id, submission_id=accepted['submission_id'],activity_id=activity_id,
                    task_type='HOURLY',accepted_at=accepted['accepted_at'],submission_deadline=accepted['window_end'],
                    validation_status='VALID' if status in {'APPROVED','VERIFIED'} else 'INVALID' if status=='REJECTED' else 'PROCESSING_ERROR' if status=='RETRYABLE_ERROR' else 'NEEDS_REVIEW',
                    approved=status in {'APPROVED','VERIFIED'},validator='TAUFA_AI' if validation else 'UNCONFIRMED',
                    validated_score=validation.get('score'),category_match=validation.get('category_match') is True,
                    description_match=validation.get('description_match') is True,evidence_match=validation.get('evidence_match') is True,
                    duplicate_detected=duplicate.get('duplicate_detected',False))
                break
            conn.execute(
                """INSERT INTO proof_submission_events (
                    event_id, account_id, submission_id, activity_id,
                    schedule_version, source_device_id, accepted_at, status, payload_json
                ) VALUES (?, ?, ?, ?, 1, 'windows_admin', ?, ?, ?)""",
                (event_id, account_id, review_sub_id, activity_id, now_iso, status, json.dumps(payload)),
            )
        return event_id

    def get_pending_events(self, account_id: str, device_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                """SELECT e.* FROM proof_submission_events e
                LEFT JOIN proof_submission_deliveries d ON d.event_id = e.event_id AND d.device_id = ?
                WHERE e.account_id = ? AND e.source_device_id != ? AND d.event_id IS NULL
                ORDER BY e.accepted_at ASC LIMIT ?""",
                (device_id, account_id, device_id, max(1, min(int(limit), 100))),
            ).fetchall()
        results = []
        for row in rows:
            try:
                results.append(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                continue
        return results

    def acknowledge_events(self, account_id: str, device_id: str, event_ids: list[str]) -> int:
        acknowledged = 0
        now = dt_to_iso(now_ist())
        with self.db._connect() as conn:
            for event_id in event_ids:
                row = conn.execute(
                    "SELECT 1 FROM proof_submission_events WHERE event_id = ? AND account_id = ? AND source_device_id != ?",
                    (event_id, account_id, device_id),
                ).fetchone()
                if not row:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO proof_submission_deliveries (event_id, device_id, delivered_at) VALUES (?, ?, ?)",
                    (event_id, device_id, now),
                )
                acknowledged += cur.rowcount
        return acknowledged
