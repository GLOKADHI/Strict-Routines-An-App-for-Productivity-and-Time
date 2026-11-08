"""
StrictRecords — Resumable / Chunked Evidence Upload & Asynchronous Server Validation Service
=============================================================================================
Implements Part B (Sections 7–12):
- Chunked/resumable evidence upload with byte-offset tracking and SHA-256 verification.
- Idempotent submission handling via `submission_id`.
- Decoupled server-side AI validation (Taufa AI primary -> Official OpenAI API fallback)
  so the Android phone does not need to stay awake after upload completion.
- Automatic reconciliation endpoint for APK resume / network restoration.
"""

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.database import get_connection, DATA_DIR
from core.ai_health_coordinator import ai_health_coordinator

logger = logging.getLogger("StrictRecords.ResumableUploadService")

IST = timezone(timedelta(hours=5, minutes=30))
ALLOWED_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
UPLOADS_DIR = os.path.join(DATA_DIR, "resumable_proof_uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)


class ResumableUploadService:
    """
    Manages resumable chunked uploads and server-side asynchronous AI validation.
    """

    STATES = (
        "CREATED",
        "QUEUED",
        "UPLOADING",
        "UPLOAD_PAUSED",
        "UPLOAD_RETRY_PENDING",
        "UPLOADED",
        "SERVER_PROCESSING",
        "AI_VALIDATION_PENDING",
        "AI_VALIDATING",
        "APPROVED",
        "REJECTED",
        "MANUAL_REVIEW",
        "COMPLETED",
    )

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(ResumableUploadService, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        self._ensure_tables()
        self._initialized = True

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(IST).isoformat()

    def _ensure_tables(self) -> None:
        with self._lock:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS resumable_proof_uploads (
                    submission_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    activity_id TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT 'HOURLY',
                    date TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'STUDY',
                    description TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT 'evidence.jpg',
                    mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    sha256_checksum TEXT NOT NULL DEFAULT '',
                    upload_offset INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_upload_error TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'CREATED',
                    server_acknowledgement TEXT NOT NULL DEFAULT '',
                    ai_validation_status TEXT NOT NULL DEFAULT 'PENDING',
                    ai_provider TEXT NOT NULL DEFAULT '',
                    ai_score INTEGER,
                    ai_reason TEXT NOT NULL DEFAULT '',
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    points_transaction_status TEXT NOT NULL DEFAULT 'PENDING',
                    temp_file_path TEXT NOT NULL DEFAULT '',
                    final_file_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        if row is None:
            return {}
        d = dict(row)
        return {
            "submission_id": d.get("submission_id", ""),
            "session_id": d.get("session_id", ""),
            "activity_id": d.get("activity_id", ""),
            "task_type": d.get("task_type", "HOURLY"),
            "date": d.get("date", ""),
            "category": d.get("category", "STUDY"),
            "description": d.get("description", ""),
            "filename": d.get("filename", "evidence.jpg"),
            "mime_type": d.get("mime_type", "image/jpeg"),
            "file_size": int(d.get("file_size") or 0),
            "sha256_checksum": d.get("sha256_checksum", ""),
            "upload_offset": int(d.get("upload_offset") or 0),
            "retry_count": int(d.get("retry_count") or 0),
            "last_upload_error": d.get("last_upload_error", ""),
            "state": d.get("state", "CREATED"),
            "server_acknowledgement": d.get("server_acknowledgement", ""),
            "ai_validation_status": d.get("ai_validation_status", "PENDING"),
            "ai_provider": d.get("ai_provider", ""),
            "ai_score": d.get("ai_score"),
            "ai_reason": d.get("ai_reason", ""),
            "points_awarded": int(d.get("points_awarded") or 0),
            "points_transaction_status": d.get("points_transaction_status", "PENDING"),
            "final_file_path": d.get("final_file_path", ""),
            "created_at": d.get("created_at", ""),
            "updated_at": d.get("updated_at", ""),
        }

    def get_submission(self, submission_id: str) -> Optional[Dict[str, Any]]:
        if not submission_id:
            return None
        with self._lock:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM resumable_proof_uploads WHERE submission_id = ?",
                (submission_id,),
            )
            row = cur.fetchone()
            conn.close()
            return self._row_to_dict(row) if row else None

    def init_upload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initializes or resumes a chunked upload session for `submission_id`.
        Idempotent: if `submission_id` already exists, returns current `upload_offset` and `state`.
        """
        submission_id = str(payload.get("submission_id") or "").strip()
        if not submission_id:
            return {"success": False, "error": "submission_id is required", "code": "MISSING_SUBMISSION_ID"}

        mime_type = str(payload.get("mime_type") or "image/jpeg").strip().lower()
        if mime_type not in ALLOWED_MIME_TYPES:
            return {
                "success": False,
                "error": f"Unsupported MIME type: {mime_type}",
                "code": "INVALID_MIME_TYPE",
            }

        file_size = int(payload.get("file_size") or 0)
        sha256_checksum = str(payload.get("sha256_checksum") or "").strip().lower()
        if file_size <= 0 or not sha256_checksum:
            return {
                "success": False,
                "error": "Positive file_size and sha256_checksum are required",
                "code": "INVALID_FILE_METADATA",
            }

        with self._lock:
            existing = self.get_submission(submission_id)
            if existing:
                # Verify actual bytes on disk if still uploading
                part_path = os.path.join(UPLOADS_DIR, f"{submission_id}.part")
                if existing["state"] in ("CREATED", "QUEUED", "UPLOADING", "UPLOAD_PAUSED", "UPLOAD_RETRY_PENDING"):
                    disk_offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                    if disk_offset != existing["upload_offset"]:
                        conn = get_connection()
                        conn.execute(
                            "UPDATE resumable_proof_uploads SET upload_offset = ?, updated_at = ? WHERE submission_id = ?",
                            (disk_offset, self._now_iso(), submission_id),
                        )
                        conn.commit()
                        conn.close()
                        existing["upload_offset"] = disk_offset
                return {
                    "success": True,
                    "resumed": True,
                    "submission_id": submission_id,
                    "upload_offset": existing["upload_offset"],
                    "file_size": existing["file_size"],
                    "state": existing["state"],
                    "record": existing,
                }

            now_iso = self._now_iso()
            part_path = os.path.join(UPLOADS_DIR, f"{submission_id}.part")
            # Ensure fresh part file
            with open(part_path, "wb") as _:
                pass

            ack_id = f"ack_init_{uuid.uuid4().hex[:12]}"
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO resumable_proof_uploads (
                    submission_id, session_id, activity_id, task_type, date,
                    category, description, filename, mime_type, file_size,
                    sha256_checksum, upload_offset, retry_count, last_upload_error,
                    state, server_acknowledgement, ai_validation_status,
                    points_transaction_status, temp_file_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', 'UPLOADING', ?, 'PENDING', 'PENDING', ?, ?, ?)
                """,
                (
                    submission_id,
                    str(payload.get("session_id") or ""),
                    str(payload.get("activity_id") or ""),
                    str(payload.get("task_type") or "HOURLY").upper(),
                    str(payload.get("date") or datetime.now(IST).strftime("%Y-%m-%d")),
                    str(payload.get("category") or "STUDY").upper(),
                    str(payload.get("description") or ""),
                    str(payload.get("filename") or "evidence.jpg"),
                    mime_type,
                    file_size,
                    sha256_checksum,
                    ack_id,
                    part_path,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
            conn.close()

            record = self.get_submission(submission_id)
            return {
                "success": True,
                "resumed": False,
                "submission_id": submission_id,
                "upload_offset": 0,
                "file_size": file_size,
                "state": "UPLOADING",
                "server_acknowledgement": ack_id,
                "record": record,
            }

    def upload_chunk(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Appends a chunk at `offset` for `submission_id`.
        Supports resumption if client retries from an earlier/current offset.
        """
        submission_id = str(payload.get("submission_id") or "").strip()
        if not submission_id:
            return {"success": False, "error": "submission_id is required", "code": "MISSING_SUBMISSION_ID"}

        with self._lock:
            record = self.get_submission(submission_id)
            if not record:
                return {"success": False, "error": "Upload session not initialized", "code": "NOT_INITIALIZED"}

            if record["state"] in ("UPLOADED", "SERVER_PROCESSING", "AI_VALIDATION_PENDING", "AI_VALIDATING", "APPROVED", "REJECTED", "MANUAL_REVIEW", "COMPLETED"):
                return {
                    "success": True,
                    "submission_id": submission_id,
                    "upload_offset": record["file_size"],
                    "file_size": record["file_size"],
                    "state": record["state"],
                    "already_completed": True,
                }

            offset = int(payload.get("offset") or 0)
            chunk_b64 = str(payload.get("chunk_base64") or "")
            try:
                chunk_bytes = base64.b64decode(chunk_b64) if chunk_b64 else b""
            except Exception as exc:
                return {"success": False, "error": f"Invalid base64 chunk: {exc}", "code": "INVALID_CHUNK"}

            part_path = os.path.join(UPLOADS_DIR, f"{submission_id}.part")
            current_disk_offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0

            # If client is retrying an already-written chunk, or offset < current_disk_offset, seek to offset
            if offset > current_disk_offset:
                return {
                    "success": False,
                    "error": f"Chunk offset {offset} exceeds confirmed server offset {current_disk_offset}",
                    "code": "OFFSET_MISMATCH",
                    "upload_offset": current_disk_offset,
                }

            mode = "r+b" if os.path.exists(part_path) else "wb"
            with open(part_path, mode) as f:
                f.seek(offset)
                f.write(chunk_bytes)
                f.truncate(offset + len(chunk_bytes))

            new_offset = offset + len(chunk_bytes)
            now_iso = self._now_iso()
            conn = get_connection()
            conn.execute(
                """
                UPDATE resumable_proof_uploads
                SET upload_offset = ?, state = 'UPLOADING', last_upload_error = '', updated_at = ?
                WHERE submission_id = ?
                """,
                (new_offset, now_iso, submission_id),
            )
            conn.commit()
            conn.close()

            return {
                "success": True,
                "submission_id": submission_id,
                "upload_offset": new_offset,
                "file_size": record["file_size"],
                "state": "UPLOADING",
            }

    def complete_upload(self, payload: Dict[str, Any], async_validation: bool = True) -> Dict[str, Any]:
        """
        Verifies total byte size and SHA-256 checksum, marks submission `UPLOADED`,
        and triggers server-side AI validation without requiring the phone to stay awake.
        """
        submission_id = str(payload.get("submission_id") or "").strip()
        if not submission_id:
            return {"success": False, "error": "submission_id is required", "code": "MISSING_SUBMISSION_ID"}

        with self._lock:
            record = self.get_submission(submission_id)
            if not record:
                return {"success": False, "error": "Upload session not found", "code": "NOT_FOUND"}

            if record["state"] in ("UPLOADED", "SERVER_PROCESSING", "AI_VALIDATION_PENDING", "AI_VALIDATING", "APPROVED", "REJECTED", "MANUAL_REVIEW", "COMPLETED"):
                return {
                    "success": True,
                    "submission_id": submission_id,
                    "state": record["state"],
                    "server_acknowledgement": record["server_acknowledgement"],
                    "record": record,
                }

            part_path = os.path.join(UPLOADS_DIR, f"{submission_id}.part")
            if not os.path.exists(part_path):
                return {"success": False, "error": "Uploaded part file missing on server", "code": "PART_MISSING"}

            actual_size = os.path.getsize(part_path)
            if actual_size != record["file_size"]:
                return {
                    "success": False,
                    "error": f"Incomplete upload: received {actual_size} of {record['file_size']} bytes",
                    "code": "INCOMPLETE_UPLOAD",
                    "upload_offset": actual_size,
                }

            with open(part_path, "rb") as f:
                file_bytes = f.read()

            actual_sha256 = hashlib.sha256(file_bytes).hexdigest().lower()
            expected_sha256 = record["sha256_checksum"].lower()
            if actual_sha256 != expected_sha256:
                return {
                    "success": False,
                    "error": f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}",
                    "code": "CHECKSUM_MISMATCH",
                    "upload_offset": 0,
                }

            ext = os.path.splitext(record["filename"])[1].lower() or ".jpg"
            final_path = os.path.join(UPLOADS_DIR, f"{submission_id}{ext}")
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
                os.replace(part_path, final_path)
            except Exception:
                with open(final_path, "wb") as out_f:
                    out_f.write(file_bytes)

            ack = f"ack_complete_{uuid.uuid4().hex[:16]}"
            now_iso = self._now_iso()
            conn = get_connection()
            conn.execute(
                """
                UPDATE resumable_proof_uploads
                SET upload_offset = ?,
                    state = 'AI_VALIDATION_PENDING',
                    server_acknowledgement = ?,
                    ai_validation_status = 'AI_VALIDATION_PENDING',
                    final_file_path = ?,
                    updated_at = ?
                WHERE submission_id = ?
                """,
                (actual_size, ack, final_path, now_iso, submission_id),
            )
            conn.commit()
            conn.close()

        if async_validation:
            threading.Thread(
                target=self._run_server_side_validation,
                args=(submission_id, file_bytes),
                daemon=True,
                name=f"ServerProofValidate-{submission_id[:12]}",
            ).start()
        else:
            self._run_server_side_validation(submission_id, file_bytes)

        updated = self.get_submission(submission_id)
        return {
            "success": True,
            "submission_id": submission_id,
            "state": updated["state"] if updated else "AI_VALIDATION_PENDING",
            "server_acknowledgement": ack,
            "sha256_verified": True,
            "record": updated,
        }

    def _run_server_side_validation(self, submission_id: str, file_bytes: Optional[bytes] = None) -> Dict[str, Any]:
        """
        Runs AI validation on the server using `ai_health_coordinator` (Taufa AI primary ->
        Official OpenAI API fallback) and updates the submission record + points transaction.
        """
        record = self.get_submission(submission_id)
        if not record:
            return {}

        with self._lock:
            conn = get_connection()
            conn.execute(
                """
                UPDATE resumable_proof_uploads
                SET state = 'AI_VALIDATING', ai_validation_status = 'AI_VALIDATING', updated_at = ?
                WHERE submission_id = ?
                """,
                (self._now_iso(), submission_id),
            )
            conn.commit()
            conn.close()

        if file_bytes is None:
            fpath = record.get("final_file_path") or ""
            if fpath and os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    file_bytes = f.read()
            else:
                file_bytes = b""

        task_type = str(record.get("task_type") or "HOURLY").upper()
        category = str(record.get("category") or "STUDY").upper()
        description = str(record.get("description") or "Completed scheduled study activity")
        if len(description.strip()) < 6:
            description = f"Completed {category} session ({description.strip() or 'verified evidence'})"

        try:
            if task_type == "HOURLY":
                val_res = ai_health_coordinator.validate_hourly_submission_with_fallback(
                    submission_id=submission_id,
                    category=category,
                    description=description,
                    images=[file_bytes],
                    schedule_context=f"Activity {record.get('activity_id')}: {category}",
                    task_type="HOURLY",
                    filenames=[record.get("filename") or "evidence.jpg"],
                )
                if val_res.validator_status in ("UNCONFIGURED", "FAILED_RETRYABLE", "DISABLED"):
                    new_state = "AI_VALIDATION_PENDING"
                    ai_status = "PENDING_VALIDATION"
                    pts = 0
                    pts_tx = "PENDING_AI_RECOVERY"
                    reason = val_res.reason or "AI provider temporarily unavailable; queued for retry."
                    provider = val_res.validation_provider or "UNAVAILABLE"
                    score = None
                elif val_res.is_valid and val_res.qualifies_for_points and (val_res.score or 0) > 0:
                    pts = int(val_res.score or 0)
                    score = pts
                    new_state = "APPROVED"
                    ai_status = "APPROVED"
                    pts_tx = "COMMITTED"
                    reason = val_res.reason or f"Approved ({pts}/8 pts)"
                    provider = val_res.validation_provider or "TAUFA_AI"
                elif val_res.validator_status in ("REVIEW_REQUIRED", "NEEDS_REVIEW", "AWAITING_APPROVAL"):
                    pts = 0
                    score = val_res.score
                    new_state = "MANUAL_REVIEW"
                    ai_status = "MANUAL_REVIEW"
                    pts_tx = "PENDING_REVIEW"
                    reason = val_res.reason or "Flagged for manual review"
                    provider = val_res.validation_provider or "TAUFA_AI"
                else:
                    pts = 0
                    score = int(val_res.score or 0)
                    new_state = "REJECTED"
                    ai_status = "REJECTED"
                    pts_tx = "ZERO_POINTS_COMMITTED"
                    reason = val_res.reason or "Evidence rejected (0 pts)"
                    provider = val_res.validation_provider or "TAUFA_AI"
            else:
                # Non-hourly proof (GYM / BATH) uploaded via resumable queue
                pts = 10 if task_type == "GYM" else (5 if task_type == "BATH" else 0)
                score = pts
                new_state = "APPROVED"
                ai_status = "APPROVED"
                pts_tx = "COMMITTED"
                reason = f"{task_type} evidence verified and stored on server."
                provider = "SERVER_VERIFIER"

            with self._lock:
                conn = get_connection()
                conn.execute(
                    """
                    UPDATE resumable_proof_uploads
                    SET state = ?,
                        ai_validation_status = ?,
                        ai_provider = ?,
                        ai_score = ?,
                        ai_reason = ?,
                        points_awarded = ?,
                        points_transaction_status = ?,
                        updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (
                        new_state,
                        ai_status,
                        provider,
                        score,
                        reason,
                        pts,
                        pts_tx,
                        self._now_iso(),
                        submission_id,
                    ),
                )
                conn.commit()
                conn.close()
        except Exception as exc:
            logger.error("Server-side AI validation error for %s: %s", submission_id, exc)
            with self._lock:
                conn = get_connection()
                conn.execute(
                    """
                    UPDATE resumable_proof_uploads
                    SET state = 'AI_VALIDATION_PENDING',
                        ai_validation_status = 'PENDING_VALIDATION',
                        ai_reason = ?,
                        points_transaction_status = 'PENDING_AI_RECOVERY',
                        updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (f"Server validation deferred: {exc}", self._now_iso(), submission_id),
                )
                conn.commit()
                conn.close()

        return self.get_submission(submission_id) or {}

    def reconcile_submissions(self, submission_ids: Any = None) -> Dict[str, Any]:
        """
        Returns authoritative server states for a list of `submission_ids` (or recent uploads)
        so the Android APK can reconcile any pending/uploading/validating items upon resume.
        Also retries any items stuck in `AI_VALIDATION_PENDING` if an AI provider is now healthy.
        """
        if isinstance(submission_ids, dict):
            submission_ids = submission_ids.get("submission_ids") or []
        with self._lock:
            conn = get_connection()
            cur = conn.cursor()
            if submission_ids:
                placeholders = ",".join(["?"] * len(submission_ids))
                cur.execute(
                    f"SELECT * FROM resumable_proof_uploads WHERE submission_id IN ({placeholders}) ORDER BY updated_at DESC",
                    tuple(submission_ids),
                )
            else:
                cur.execute(
                    "SELECT * FROM resumable_proof_uploads ORDER BY updated_at DESC LIMIT 50"
                )
            rows = [self._row_to_dict(r) for r in cur.fetchall()]
            conn.close()

        # Opportunistically retry any AI_VALIDATION_PENDING items if AI provider is healthy
        health = ai_health_coordinator.get_health_status(refresh_if_stale=False)
        if health.get("active_provider") in ("TAUFA_AI", "OPENAI_FALLBACK"):
            for r in rows:
                if r.get("state") == "AI_VALIDATION_PENDING":
                    threading.Thread(
                        target=self._run_server_side_validation,
                        args=(r["submission_id"], None),
                        daemon=True,
                    ).start()

        return {
            "success": True,
            "submissions": rows,
            "count": len(rows),
            "ai_health": health,
        }


resumable_upload_service = ResumableUploadService()
