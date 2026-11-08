"""
StrictRecords — Cloud Sync Manager (Windows Desktop)
Orchestrates Google Drive synchronization, conflict-safe append-only point ledger sync,
schedules, attendance, routines, device manifests, evidence files, and release updates.
Enforces the mandatory 5 GB free storage requirement and replaces Bluetooth transfer.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.database import StrictRecordsDB
from core.google_auth import GoogleAuthManager
from core.google_drive import GoogleDriveClient

logger = logging.getLogger("StrictRecords.CloudSync")

CURRENT_WINDOWS_VERSION_NAME = "1.16.0"
CURRENT_WINDOWS_VERSION_CODE = 11600
DEVICE_ID_WINDOWS = "windows_pc"


class CloudSyncManager:
    """Manages cloud backup and bidirectional metadata sync for StrictRecords."""

    def __init__(
        self,
        db: StrictRecordsDB,
        auth_manager: Optional[GoogleAuthManager] = None,
        poll_interval_seconds: int = 30,
    ):
        self.db = db
        self.auth_manager = auth_manager or GoogleAuthManager()
        self._sync_account_sub = None
        self.drive_client = GoogleDriveClient(
            self._get_drive_access_token,
            root_folder_id_getter=self.auth_manager.get_registered_root_folder_id,
            root_folder_id_setter=self.auth_manager.set_registered_root_folder_id,
            expected_email_getter=lambda: str(self.auth_manager.get_user_profile().get("email") or ""),
        )
        self.auth_manager.on_account_switched = self._handle_account_switch
        self.poll_interval = poll_interval_seconds

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_quota_check: float = 0.0
        self._cached_quota: Optional[Dict[str, Any]] = None
        self._sync_in_progress = False
        self._last_synced_time: Optional[str] = None
        self._last_sync_error: Optional[str] = None
        self._initial_migration_done = False

    def _require_sync_account(self) -> None:
        if self._sync_account_sub is not None and (
            self.auth_manager.get_account_sub() != self._sync_account_sub or
            self.auth_manager.pending_auth is True
        ):
            raise PermissionError("Google account changed; retry synchronization after reconnecting")

    def _get_drive_access_token(self):
        self._require_sync_account()
        return self.auth_manager.get_valid_access_token()

    def _handle_account_switch(self, previous_sub: str, new_sub: str) -> None:
        """Isolates local sync cache and clears pending upload queue from a previous Google account (sub)."""
        logger.info("Google account switched (%s -> %s); isolating Drive sync state.", previous_sub, new_sub)
        self.drive_client.clear_cache()
        self._cached_quota = None
        self._last_quota_check = 0.0
        self._last_synced_time = None
        self._last_sync_error = None
        self._initial_migration_done = False
        with self.db._connect() as conn:
            conn.execute("UPDATE cloud_sync_queue SET status='ACCOUNT_SWITCH_PENDING' WHERE status IN ('QUEUED','PENDING','IN_PROGRESS','FAILED')")
            conn.execute("UPDATE cloud_sync_files SET sync_status='ACCOUNT_SWITCH_PENDING' WHERE sync_status IN ('PENDING','UPLOADING','FAILED')")
            conn.commit()

    def _retain_drive_candidates(self, category, kind):
        """Preserve original remote JSON for trusted-backend reconciliation."""
        self._require_sync_account()
        sub = self.auth_manager.get_account_sub()
        folder = self.drive_client.resolve_target_folder(category)
        for item in self.drive_client.list_files(folder_id=folder, page_size=100):
            if not str(item.get('name', '')).endswith('.json') or not item.get('id'):
                continue
            raw = self.drive_client.download_file_bytes(item['id'])
            if len(raw) > 2_000_000:
                raise ValueError('Remote manifest exceeds the reconciliation size limit')
            json.loads(raw)
            self._require_sync_account()
            with self.db._connect() as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS drive_pending_records(
                    account_sub TEXT NOT NULL,drive_file_id TEXT NOT NULL,sha256 TEXT NOT NULL,
                    kind TEXT NOT NULL,payload BLOB NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING_RECONCILIATION',
                    PRIMARY KEY(account_sub,drive_file_id,sha256))''')
                conn.execute('''INSERT OR IGNORE INTO drive_pending_records
                    (account_sub,drive_file_id,sha256,kind,payload) VALUES(?,?,?,?,?)''',
                    (sub,item['id'],hashlib.sha256(raw).hexdigest(),kind,raw))

    def select_shared_root_folder(self, folder_id: str) -> Dict[str, Any]:
        """Validates and binds an existing shared StrictRecords root folder ID for the authenticated Google sub."""
        if not self.auth_manager.is_authenticated():
            raise PermissionError("Sign in with Google before selecting a shared StrictRecords folder.")
        sub = self.auth_manager.get_account_sub()
        result = self.drive_client.set_registered_root_folder_id(folder_id, expected_owner_sub=sub)
        verification = self.verify_drive_access()
        if not verification.get("success"):
            return verification
        session = self.auth_manager.get_session() or {}
        session["root_folder_id"] = result["root_folder_id"]
        session["drive_verified"] = True
        self.auth_manager.save_session(session)
        with self.auth_manager._lock:
            self.auth_manager.drive_status = "Connected"
        return result

    def start(self) -> None:
        """Starts background cloud sync worker thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._worker_loop, name="CloudSyncWorker", daemon=True
            )
            self._thread.start()
            logger.info("CloudSyncManager worker thread started.")

    def stop(self) -> None:
        """Stops background worker thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            logger.info("CloudSyncManager worker thread stopped.")

    def get_status(self) -> Dict[str, Any]:
        """
        Returns consolidated status of Google Auth, Drive Quota, and Sync Queue.
        """
        is_auth = self.auth_manager.is_authenticated()
        user_prof = self.auth_manager.get_user_profile() if is_auth else {}
        summary = self.db.get_cloud_sync_summary()

        quota_info = None
        if is_auth and (self.auth_manager.get_session() or {}).get("drive_verified"):
            try:
                quota_info = self.get_cached_quota()
            except Exception as e:
                logger.warning("Could not fetch quota for status: %s", e)
                quota_info = {
                    "error": str(e),
                    "is_below_5gb": False,
                }

        # Count pending downloads and uploads
        pending_uploads = summary.get("pending_count", 0) + summary.get("uploading_count", 0)
        failed_count = summary.get("failed_count", 0)

        session = self.auth_manager.get_session() or {}
        drive_verified = bool(session.get("drive_verified", False))
        drive_state = session.get("drive_state")
        if not is_auth:
            drive_state = "NOT_CONNECTED"
        elif not drive_state:
            drive_state = "DRIVE_CONNECTED_INITIALIZED" if drive_verified else "GOOGLE_CONNECTED_DRIVE_PERMISSION_REQUIRED"

        return {
            "authenticated": is_auth,
            "drive_verified": drive_verified,
            "drive_state": drive_state,
            "drive_error": session.get("drive_error"),
            "drive_error_code": session.get("drive_error_code"),
            "account_sub": self.auth_manager.get_account_sub() if is_auth else None,
            "root_folder_id": self.auth_manager.get_registered_root_folder_id() if (is_auth and drive_verified) else None,
            "connection": self.auth_manager.get_detailed_connection_status(),
            "user": user_prof,
            "quota": quota_info,
            "sync": {
                **summary,
                "in_progress": self._sync_in_progress,
                "last_synced": self._last_synced_time,
                "last_error": self._last_sync_error,
                "pending_uploads": pending_uploads,
                "pending_downloads": 0,
                "failed_items": failed_count,
            },
        }

    def verify_drive_access(self) -> Dict[str, Any]:
        """Runs live Drive verification check and returns result."""
        if not self.auth_manager.is_authenticated():
            return {
                "success": False,
                "state": "NOT_CONNECTED",
                "error_message": "Not authenticated with Google.",
                "error_code": "NOT_AUTHENTICATED"
            }
        result = self.drive_client.verify_drive_connection()
        session = self.auth_manager.get_session() or {}
        if result.get("success"):
            session["drive_verified"] = True
            session["drive_state"] = result.get("state")
            session["drive_error"] = None
            session["drive_error_code"] = None
            session["drive_verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.auth_manager.save_session(session)
            with self.auth_manager._lock:
                self.auth_manager.drive_status = "Connected"
        else:
            session["drive_verified"] = False
            session["drive_state"] = result.get("state")
            session["drive_error"] = result.get("error_message")
            session["drive_error_code"] = result.get("error_code")
            self.auth_manager.save_session(session)
            with self.auth_manager._lock:
                self.auth_manager.drive_status = "Connection failed"
        return result

    def get_cached_quota(self, max_age_seconds: int = 120) -> Dict[str, Any]:
        """Returns cached quota or fetches fresh from Google Drive API."""
        now = time.time()
        if self._cached_quota and (now - self._last_quota_check < max_age_seconds):
            return self._cached_quota

        quota = self.drive_client.get_storage_quota()
        self._cached_quota = quota
        self._last_quota_check = now
        return quota

    def retry_failed_items(self) -> Dict[str, Any]:
        """Resets failed cloud sync tasks and triggers immediate sync."""
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE cloud_sync_files SET sync_status='PENDING', error_message=NULL WHERE sync_status='FAILED'"
            )
            conn.execute(
                "UPDATE cloud_sync_queue SET status='QUEUED', retry_count=0, next_retry_at=NULL WHERE status='FAILED'"
            )
            conn.commit()
        return self.sync_now()

    def perform_initial_migration(self) -> Dict[str, Any]:
        """
        Safely connects existing installation to Google Drive:
        1. Backs up local database to backups/ and uploads to StrictRecords/Backups/
        2. Discovers existing Drive datasets
        3. Never replaces non-empty local DB with empty folder
        """
        if not self.auth_manager.is_authenticated():
            return {"success": False, "error": "Not authenticated with Google"}

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join("data", "backups")
        os.makedirs(backup_dir, exist_ok=True)
        local_db_path = self.db.db_path
        backup_file = os.path.join(backup_dir, f"backup_windows_{ts}.db")

        try:
            if os.path.exists(local_db_path):
                shutil.copy2(local_db_path, backup_file)
                logger.info("Local SQLite backup created at %s", backup_file)

                # Upload backup to Drive
                try:
                    self.drive_client.upload_file(
                        local_path=backup_file,
                        category="Backups",
                        description=f"StrictRecords Windows DB Backup {ts}",
                    )
                    logger.info("Local SQLite backup uploaded to Google Drive Backups/")
                except Exception as upload_err:
                    logger.warning("Could not upload initial backup to Drive: %s", upload_err)

            self._initial_migration_done = True
            return {"success": True, "backup_created": backup_file}
        except Exception as e:
            logger.error("Initial migration backup failed: %s", e)
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------------------
    # Bidirectional Data Synchronization
    # -------------------------------------------------------------------------

    def _sync_point_events_to_drive(self) -> int:
        """
        Uploads local score_events to Ledger/PointEvents/event_<event_id>.json.
        Preserves append-only ledger events with full audit metadata.
        """
        uploaded = 0
        with self.db._connect() as conn:
            # Get score events that haven't been synced to Drive yet
            rows = conn.execute(
                """
                SELECT se.event_id, se.date, se.is_dev_mode, se.timestamp, se.event_type,
                       se.device_id, se.description, se.points, se.rule_version,
                       se.parent_event_id, se.evidence_id, se.related_task_id, se.audit_json
                FROM score_events se
                LEFT JOIN cloud_sync_files csf ON csf.file_id = ('ledger_ev_' || se.event_id)
                WHERE csf.file_id IS NULL OR csf.sync_status != 'SYNCED'
                ORDER BY se.timestamp DESC
                LIMIT 50
                """
            ).fetchall()

            for r in rows:
                event_data = {
                    "schema_version": 1,
                    "event_id": r[0],
                    "date": r[1],
                    "is_dev_mode": r[2],
                    "timestamp": r[3],
                    "event_type": r[4],
                    "device_id": r[5],
                    "description": r[6],
                    "points": r[7],
                    "rule_version": r[8],
                    "parent_event_id": r[9],
                    "evidence_id": r[10],
                    "related_task_id": r[11],
                    "audit_json": r[12],
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
                filename = f"event_{r[0]}.json"
                file_key = f"ledger_ev_{r[0]}"

                try:
                    res = self.drive_client.upload_json_data(
                        category="Ledger/PointEvents",
                        filename=filename,
                        data=event_data,
                        description=f"Ledger Point Event {r[0]} ({r[4]}: {r[7]} pts)",
                    )
                    self.db.upsert_cloud_sync_file(
                        file_id=file_key,
                        local_path=filename,
                        category="Ledger/PointEvents",
                        file_size_bytes=res.get("file_size", 0),
                        sha256_hash=res.get("sha256_client", ""),
                        sync_status="SYNCED",
                    )
                    uploaded += 1
                except Exception as e:
                    logger.warning("Failed uploading ledger event %s: %s", r[0], e)

        return uploaded

    def _sync_point_events_from_drive(self) -> int:
        """Drive files are retained claims, never trusted score transactions."""
        self._retain_drive_candidates("Ledger/PointEvents", "LEDGER_BACKUP")
        return 0

    def _sync_schedules_to_drive(self) -> int:
        """Uploads confirmed daily schedules to Data/Schedules/schedule_<date>.json."""
        uploaded = 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.db._connect() as conn:
            # Check today's schedule and activities
            activities = conn.execute(
                """
                SELECT activity_id, type, title, window_start, window_end, validation_status, points
                FROM scheduled_activities
                WHERE date = ?
                """,
                (today,),
            ).fetchall()

            act_list = [
                {
                    "activity_id": a[0],
                    "task_type": a[1],
                    "category": a[2],
                    "start_time": a[3],
                    "end_time": a[4],
                    "validation_status": a[5],
                    "points": a[6],
                }
                for a in activities
            ]

            payload = {
                "schema_version": 1,
                "date": today,
                "activities": act_list,
                "source_device_id": DEVICE_ID_WINDOWS,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self.drive_client.upload_json_data(
                    category="Data/Schedules",
                    filename=f"schedule_{today}.json",
                    data=payload,
                    description=f"Schedule for {today} ({len(act_list)} activities)",
                )
                uploaded += 1
            except Exception as e:
                logger.debug("Could not upload schedule for %s: %s", today, e)

        return uploaded

    def _sync_schedules_from_drive(self) -> int:
        """Stale Drive snapshots cannot replace the current schedule revision."""
        self._retain_drive_candidates("Data/Schedules", "SCHEDULE_BACKUP")
        return 0

    def _sync_devices_metadata(self) -> None:
        """Registers this Windows device in Data/Devices/device_windows_pc.json."""
        payload = {
            "device_id": DEVICE_ID_WINDOWS,
            "device_name": "StrictRecords Windows PC",
            "platform": "WINDOWS",
            "version_name": CURRENT_WINDOWS_VERSION_NAME,
            "version_code": CURRENT_WINDOWS_VERSION_CODE,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.drive_client.upload_json_data(
                category="Data/Devices",
                filename=f"device_{DEVICE_ID_WINDOWS}.json",
                data=payload,
                description="StrictRecords Windows PC Registration",
            )
        except Exception as e:
            logger.debug("Could not upload device registration: %s", e)

    def scan_and_queue_local_evidence(self) -> int:
        """
        Scans SQLite evidence_files and queues any unsynced files.
        """
        queued_count = 0
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT ef.evidence_id, ef.file_path, ef.record_type, ef.file_size_bytes, ef.sha256_hash
                FROM evidence_files ef
                LEFT JOIN cloud_sync_files csf ON ef.evidence_id = csf.file_id
                WHERE ef.is_deleted = 0
                  AND (csf.file_id IS NULL OR csf.sync_status IN ('PENDING', 'FAILED'))
                LIMIT 50
                """
            ).fetchall()

            for r in rows:
                ev_id, file_path, rec_type, size, sha = r
                if not file_path or not os.path.exists(file_path):
                    continue

                ext = os.path.splitext(file_path)[1].lower()
                if ext in [".mp4", ".mov", ".avi"]:
                    cat = "Evidence/Videos"
                elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
                    cat = "Evidence/Images"
                elif ext in [".ocr.txt", ".ocr.json"]:
                    cat = "Evidence/OCR"
                else:
                    cat = "Evidence/Documents"

                self.db.upsert_cloud_sync_file(
                    file_id=ev_id,
                    local_path=file_path,
                    category=cat,
                    file_size_bytes=size or os.path.getsize(file_path),
                    sha256_hash=sha or "",
                    sync_status="PENDING",
                )
                self.db.enqueue_cloud_sync_task(ev_id, "UPLOAD")
                queued_count += 1

        return queued_count

    def sync_now(self) -> Dict[str, Any]:
        """Triggers an authoritative bidirectional synchronization pass."""
        if not self.auth_manager.is_authenticated():
            return {"success": False, "error": "Not authenticated with Google"}

        with self._lock:
            if self._sync_in_progress:
                return {"success": True, "message": "Sync already in progress"}
            self._sync_in_progress = True
            self._sync_account_sub = self.auth_manager.get_account_sub()
            self.auth_manager.set_sync_state("Syncing")

        try:
            # 1. Enforce 5 GB free storage requirement
            quota = self.get_cached_quota(max_age_seconds=30)
            if quota.get("is_below_5gb"):
                msg = f"Google Drive free storage is below 5 GB ({quota.get('free_bytes', 0) / (1024**3):.2f} GB free). Cloud sync paused."
                logger.warning(msg)
                self._last_sync_error = msg
                self.auth_manager.set_sync_state("Ready")
                return {
                    "success": False,
                    "error": msg,
                    "quota": quota,
                }

            # 2. Initial migration check (backup creation)
            if not self._initial_migration_done:
                self.perform_initial_migration()

            # 3. Bidirectional Point Ledger Sync (Append-only & Conflict-safe)
            ledger_uploaded = self._sync_point_events_to_drive()
            ledger_imported = self._sync_point_events_from_drive()

            # 4. Bidirectional Schedules Sync
            sched_uploaded = self._sync_schedules_to_drive()
            sched_imported = self._sync_schedules_from_drive()

            # 5. Device Registration Sync
            self._sync_devices_metadata()

            # 6. Queue local evidence
            self._require_sync_account()
            self.scan_and_queue_local_evidence()

            # 7. Process pending evidence upload queue
            pending = self.db.get_pending_cloud_sync_queue(limit=20)
            processed = 0
            failed = 0

            for item in pending:
                qid = item["queue_id"]
                fid = item["file_id"]
                lpath = item["local_path"]
                cat = item["category"]

                if not os.path.exists(lpath):
                    self.db.update_cloud_sync_file_status(
                        fid, "FAILED", error_message="Local file missing"
                    )
                    self.db.update_cloud_sync_queue_status(qid, "FAILED")
                    failed += 1
                    continue

                try:
                    self.db.update_cloud_sync_file_status(fid, "UPLOADING")
                    self.db.update_cloud_sync_queue_status(qid, "IN_PROGRESS")

                    upload_res = self.drive_client.upload_file(
                        local_path=lpath,
                        category=cat,
                        description=f"StrictRecords evidence {fid}",
                    )

                    drive_id = upload_res.get("id")
                    self.db.update_cloud_sync_file_status(
                        fid, "SYNCED", drive_file_id=drive_id
                    )
                    self.db.update_cloud_sync_queue_status(qid, "COMPLETED")
                    processed += 1
                except Exception as e:
                    logger.error("Failed uploading %s to Google Drive: %s", lpath, e)
                    retries = item["retry_count"] + 1
                    next_retry = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + (60 * (2 ** retries)))
                    )
                    self.db.update_cloud_sync_file_status(
                        fid, "FAILED", error_message=str(e)
                    )
                    self.db.update_cloud_sync_queue_status(
                        qid, "QUEUED", retry_count=retries, next_retry_at=next_retry
                    )
                    failed += 1

            self._last_synced_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            self._last_sync_error = None
            self.auth_manager.set_sync_state("Synced")

            return {
                "success": True,
                "ledger_uploaded": ledger_uploaded,
                "ledger_imported": ledger_imported,
                "sched_uploaded": sched_uploaded,
                "sched_imported": sched_imported,
                "evidence_processed": processed,
                "processed": processed,
                "evidence_failed": failed,
                "failed": failed,
                "last_synced": self._last_synced_time,
                "quota": quota,
            }
        except Exception as e:
            logger.error("Error during sync_now: %s", e)
            self._last_sync_error = str(e)
            self.auth_manager.set_sync_state("Sync failed", error_msg=str(e))
            return {"success": False, "error": str(e)}
        finally:
            with self._lock:
                self._sync_in_progress = False
                self._sync_account_sub = None

    def _worker_loop(self) -> None:
        """Continuous background synchronization loop."""
        while self._running:
            try:
                if self.auth_manager.is_authenticated() and (self.auth_manager.get_session() or {}).get("sync_enabled") is True:
                    self.sync_now()
            except Exception as e:
                logger.error("Error in CloudSyncManager worker loop: %s", e)

            for _ in range(self.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

    # -------------------------------------------------------------------------
    # Application Updates via Google Drive
    # -------------------------------------------------------------------------

    def check_for_updates(self) -> Dict[str, Any]:
        """
        Checks Releases/Manifests/manifest-windows.json in Google Drive.
        Returns update availability and release details.
        """
        if not self.auth_manager.is_authenticated():
            return {"update_available": False, "error": "Google authentication required to check releases."}

        try:
            folder_id = self.drive_client.resolve_target_folder("Releases/Manifests")
            manifest_file = self.drive_client.find_file_by_name("manifest-windows.json", folder_id)
            if not manifest_file:
                return {
                    "update_available": False,
                    "current_version": CURRENT_WINDOWS_VERSION_NAME,
                    "message": "No Windows release manifest published in Google Drive.",
                }

            manifest = self.drive_client.download_json(manifest_file["id"])
            remote_code = int(manifest.get("version_code", 0))
            remote_name = manifest.get("version_name", "")

            if remote_code > CURRENT_WINDOWS_VERSION_CODE:
                return {
                    "update_available": True,
                    "current_version": CURRENT_WINDOWS_VERSION_NAME,
                    "latest_version": remote_name,
                    "release_notes": manifest.get("release_notes", ""),
                    "file_size_bytes": manifest.get("file_size_bytes", 0),
                    "sha256": manifest.get("sha256", ""),
                    "drive_file_id": manifest.get("drive_file_id", ""),
                }

            return {
                "update_available": False,
                "current_version": CURRENT_WINDOWS_VERSION_NAME,
                "message": "Application is up to date.",
            }
        except Exception as e:
            logger.warning("Failed checking for Windows updates in Drive: %s", e)
            return {"update_available": False, "error": str(e)}

    def download_and_stage_update(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """
        Downloads the release binary from Releases/Windows/, verifies SHA-256 digest,
        and stages the update in .updates/StrictRecords-update.exe.
        """
        drive_file_id = manifest.get("drive_file_id")
        expected_sha = manifest.get("sha256", "").lower()
        if not drive_file_id or not expected_sha:
            return {"success": False, "error": "Invalid release manifest (missing drive_file_id or sha256)."}

        staging_dir = os.path.join(".", ".updates")
        os.makedirs(staging_dir, exist_ok=True)
        staged_exe = os.path.join(staging_dir, "StrictRecords-update.exe")

        try:
            self.drive_client.download_file(drive_file_id, staged_exe)

            # Verify SHA-256
            sha = hashlib.sha256()
            with open(staged_exe, "rb") as f:
                while chunk := f.read(65536):
                    sha.update(chunk)
            actual_sha = sha.hexdigest().lower()

            if actual_sha != expected_sha:
                if os.path.exists(staged_exe):
                    os.remove(staged_exe)
                return {
                    "success": False,
                    "error": f"Integrity check failed: expected {expected_sha[:12]}..., got {actual_sha[:12]}...",
                }

            logger.info("Successfully staged and verified Windows update at %s", staged_exe)
            return {
                "success": True,
                "staged_path": staged_exe,
                "version_name": manifest.get("version_name"),
            }
        except Exception as e:
            logger.error("Failed downloading and staging update: %s", e)
            return {"success": False, "error": str(e)}
