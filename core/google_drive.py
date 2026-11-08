"""
StrictRecords — Google Drive API v3 Integration Client
Handles hierarchical folder structures, storage quota verification (5 GB threshold),
multipart uploads (< 5MB), chunked resumable uploads (>= 5MB), and cross-device sync.
"""

import hashlib
import json
import logging
import mimetypes
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("StrictRecords.GoogleDrive")

class DriveConnectionState:
    NOT_CONNECTED = "NOT_CONNECTED"
    GOOGLE_CONNECTED_DRIVE_PERMISSION_REQUIRED = "GOOGLE_CONNECTED_DRIVE_PERMISSION_REQUIRED"
    WAITING_FOR_CONSENT = "WAITING_FOR_CONSENT"
    VERIFYING_DRIVE_ACCESS = "VERIFYING_DRIVE_ACCESS"
    DRIVE_CONNECTED_INITIALIZED = "DRIVE_CONNECTED_INITIALIZED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"

ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3/files"

FIVE_GB_BYTES = 5 * 1024 * 1024 * 1024  # 5,368,709,120 bytes
CHUNK_SIZE = 512 * 1024  # 512 KB (multiple of 256 KB per Drive spec)
RESUMABLE_THRESHOLD_BYTES = 5 * 1024 * 1024  # 5 MB threshold

FOLDER_STRUCTURE = {
    "Schedules": [],
    "Attendance": [],
    "Routines": [],
    "Manifests": [],
    "Reconciliation": [],
    "Data": ["Manifest", "Schedules", "Attendance", "Routines", "DailySummary", "Devices"],
    "Ledger": ["PointEvents", "LedgerSnapshots"],
    "Evidence": ["Images", "Videos", "Documents", "OCR", "VerificationReports"],
    "Reports": [],
    "Metadata": [],
    "Sync": [],
    "Backups": [],
    "Releases": ["Android", "Windows", "Manifests"],
}


def compute_file_hashes(file_path: str) -> Tuple[str, str, int]:
    """Computes SHA-256, MD5, and size in bytes for a local file."""
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return sha256.hexdigest(), md5.hexdigest(), size


def guess_mime_type(file_path: str) -> str:
    """Guesses MIME type from file extension with fallback to octet-stream."""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime
    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    elif ext == ".png":
        return "image/png"
    elif ext == ".webp":
        return "image/webp"
    elif ext == ".mp4":
        return "video/mp4"
    elif ext == ".json":
        return "application/json"
    elif ext == ".pdf":
        return "application/pdf"
    elif ext == ".csv":
        return "text/csv"
    elif ext == ".txt":
        return "text/plain"
    return "application/octet-stream"


class GoogleDriveClient:
    """Production Google Drive REST API v3 Client."""

    def __init__(
        self,
        token_provider: Callable[[], Optional[str]],
        *,
        root_folder_id_getter: Optional[Callable[[], Optional[str]]] = None,
        root_folder_id_setter: Optional[Callable[[str], None]] = None,
        expected_email_getter: Optional[Callable[[], str]] = None,
    ):
        self._token_provider = token_provider
        self._root_folder_id_getter = root_folder_id_getter
        self._root_folder_id_setter = root_folder_id_setter
        self._expected_email_getter = expected_email_getter
        self._folder_cache: Dict[str, str] = {}

    def clear_cache(self) -> None:
        """Clears cached folder IDs (used on sign-out or account switch)."""
        self._folder_cache.clear()

    def validate_folder_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Validates that a Google Drive folder ID exists, is accessible, is a folder, and is not trashed."""
        fid = str(folder_id or "").strip()
        if not fid:
            return None
        url = f"{DRIVE_API_BASE}/files/{requests.utils.quote(fid)}?fields=id,name,mimeType,trashed,permissions"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code != 200:
            return None
        meta = resp.json()
        if meta.get("trashed") or meta.get("mimeType") != "application/vnd.google-apps.folder":
            return None
        perms = meta.get("permissions") or []
        meta["is_public"] = any(p.get("type") == "anyone" for p in perms if isinstance(p, dict))
        return meta

    def check_folder_is_public(self, folder_id: str) -> bool:
        """Returns True if the folder has public ('anyone') sharing permissions, False otherwise."""
        meta = self.validate_folder_id(folder_id)
        if not meta:
            return False
        return bool(meta.get("is_public", False))

    def set_registered_root_folder_id(self, folder_id: str, expected_owner_sub: str = "") -> Dict[str, Any]:
        """Validates and registers an existing shared StrictRecords root folder ID without creating duplicates."""
        meta = self.validate_folder_id(folder_id)
        if not meta:
            raise ValueError(
                "The specified StrictRecords folder is not accessible under the current Google Drive authorization."
            )
        fid = meta["id"]
        existing_manifest_meta = self.find_file_by_name("strictrecords_folder_manifest.json", fid)
        if existing_manifest_meta and existing_manifest_meta.get("id"):
            try:
                manifest = self.download_json(existing_manifest_meta["id"])
                manifest_sub = str(manifest.get("owner_sub") or "").strip()
                if expected_owner_sub and manifest_sub and manifest_sub != expected_owner_sub:
                    raise PermissionError(
                        "ACCOUNT_MISMATCH: Selected StrictRecords folder belongs to a different Google account."
                    )
            except PermissionError:
                raise
            except Exception as err:
                logger.debug("Could not inspect folder manifest: %s", err)

        self._folder_cache.clear()
        self._folder_cache["root/StrictRecords"] = fid
        if self._root_folder_id_setter:
            self._root_folder_id_setter(fid)
        hierarchy = self.ensure_folder_hierarchy()
        if expected_owner_sub:
            try:
                self.upload_json_data(
                    category="StrictRecords",
                    filename="strictrecords_folder_manifest.json",
                    data={
                        "schema_version": 1,
                        "root_folder_id": fid,
                        "folder_name": meta.get("name", "StrictRecords"),
                        "owner_sub": expected_owner_sub,
                        "visibility": "PRIVATE_TO_ACCOUNT",
                    },
                    description="StrictRecords shared root folder identity manifest",
                )
            except Exception as err:
                logger.debug("Could not write folder manifest: %s", err)
        return {
            "success": True,
            "root_folder_id": fid,
            "folder_name": meta.get("name", "StrictRecords"),
            "is_public": bool(meta.get("is_public", False)),
            "hierarchy_count": len(hierarchy),
        }

    def _headers(self, additional: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        token = self._token_provider()
        if not token:
            raise PermissionError("Not authenticated with Google. Please sign in.")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if additional:
            headers.update(additional)
        return headers

    def get_storage_quota(self) -> Dict[str, Any]:
        """
        Retrieves current storage quota and user information.
        Evaluates the mandatory 5 GB free storage requirement.
        """
        url = f"{DRIVE_API_BASE}/about?fields=storageQuota,user"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch storage quota: {resp.status_code} {resp.text}")

        data = resp.json()
        quota = data.get("storageQuota", {})
        user = data.get("user", {})

        limit = quota.get("limit")
        usage = int(quota.get("usage", 0))
        usage_in_drive = int(quota.get("usageInDrive", 0))

        if limit is not None:
            limit_bytes = int(limit)
            free_bytes = max(0, limit_bytes - usage)
            is_unlimited = False
        else:
            # Workspace accounts or unlimited quotas
            limit_bytes = 0
            free_bytes = 100 * 1024 * 1024 * 1024  # placeholder for unlimited
            is_unlimited = True

        is_below_5gb = (not is_unlimited) and (free_bytes < FIVE_GB_BYTES)

        return {
            "limit_bytes": limit_bytes,
            "usage_bytes": usage,
            "usage_in_drive_bytes": usage_in_drive,
            "free_bytes": free_bytes,
            "is_unlimited": is_unlimited,
            "is_below_5gb": is_below_5gb,
            "user_email": user.get("emailAddress", ""),
            "display_name": user.get("displayName", ""),
            "permission_id": user.get("permissionId", ""),
        }

    def find_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Finds or creates a folder with the given name and parent."""
        return self._find_or_create_folder(name, parent_id)

    def _find_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Finds or creates a folder with the given name and parent, reusing registered root folder IDs."""
        cache_key = f"{parent_id or 'root'}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        is_root_app_folder = (name == "StrictRecords" and parent_id is None)
        if is_root_app_folder and self._root_folder_id_getter:
            registered_id = self._root_folder_id_getter()
            if registered_id:
                meta = self.validate_folder_id(registered_id)
                if meta and meta.get("id"):
                    fid = meta["id"]
                    self._folder_cache[cache_key] = fid
                    return fid

        # Query existing non-trashed folder ordered by creation time so reconnects never duplicate
        query = (
            f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"
        else:
            query += " and 'root' in parents"

        url = (
            f"{DRIVE_API_BASE}/files?q={requests.utils.quote(query)}"
            f"&orderBy=createdTime&fields=files(id,name,createdTime)"
        )
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            if files:
                folder_id = files[0]["id"]
                self._folder_cache[cache_key] = folder_id
                if is_root_app_folder and self._root_folder_id_setter:
                    self._root_folder_id_setter(folder_id)
                return folder_id

        # Folder does not exist, create visible user-owned folder (private to account by default)
        create_payload: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            create_payload["parents"] = [parent_id]

        create_resp = requests.post(
            f"{DRIVE_API_BASE}/files?fields=id,name",
            headers=self._headers({"Content-Type": "application/json"}),
            json=create_payload,
            timeout=15,
        )
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create folder '{name}': {create_resp.status_code} {create_resp.text}")

        folder_id = create_resp.json()["id"]
        self._folder_cache[cache_key] = folder_id
        if is_root_app_folder and self._root_folder_id_setter:
            self._root_folder_id_setter(folder_id)
        return folder_id

    def ensure_folder_hierarchy(self) -> Dict[str, str]:
        """
        Ensures the complete StrictRecords folder hierarchy exists in Drive:
        StrictRecords/
          ├── Evidence/
          │   ├── Images/
          │   ├── Videos/
          │   └── Documents/
          ├── Schedules/
          ├── Attendance/
          ├── Reports/
          ├── Metadata/
          └── Sync/
        Returns a mapping of logical path to Drive folder ID.
        """
        mapping: Dict[str, str] = {}
        root_id = self._find_or_create_folder("StrictRecords", parent_id=None)
        mapping["StrictRecords"] = root_id

        for top_level, subfolders in FOLDER_STRUCTURE.items():
            top_id = self._find_or_create_folder(top_level, parent_id=root_id)
            mapping[f"StrictRecords/{top_level}"] = top_id
            for sub in subfolders:
                sub_id = self._find_or_create_folder(sub, parent_id=top_id)
                mapping[f"StrictRecords/{top_level}/{sub}"] = sub_id
                alias_key = f"StrictRecords/{sub}"
                if alias_key not in mapping:
                    mapping[alias_key] = sub_id

        return mapping

    def resolve_target_folder(self, category: str) -> str:
        """Resolves target Google Drive folder ID for a category or relative path."""
        hierarchy = self.ensure_folder_hierarchy()
        norm = category.strip("/").replace("\\", "/")
        if not norm.startswith("StrictRecords/"):
            norm = f"StrictRecords/{norm}"

        if norm in hierarchy:
            return hierarchy[norm]

        # Fallback to closest match or root
        for path, fid in hierarchy.items():
            if norm.startswith(path):
                return fid
        return hierarchy["StrictRecords"]

    def upload_file(
        self,
        local_path: str,
        category: str = "Evidence/Documents",
        description: str = "",
        custom_metadata: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Uploads a local file to the appropriate Google Drive folder.
        Uses multipart upload for < 5MB; chunked resumable upload for >= 5MB.
        Returns Drive file metadata including ID, webViewLink, and md5Checksum.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        sha256_hash, md5_hash, file_size = compute_file_hashes(local_path)
        mime_type = guess_mime_type(local_path)
        filename = os.path.basename(local_path)
        folder_id = self.resolve_target_folder(category)

        metadata: Dict[str, Any] = {
            "name": filename,
            "parents": [folder_id],
            "description": description or f"StrictRecords proof SHA256:{sha256_hash}",
            "properties": {
                "sha256": sha256_hash,
                "category": category,
                **(custom_metadata or {}),
            },
        }

        if file_size < RESUMABLE_THRESHOLD_BYTES:
            # Multipart upload
            result = self._upload_multipart(local_path, metadata, mime_type)
        else:
            # Resumable upload
            result = self._upload_resumable(
                local_path, metadata, mime_type, file_size, progress_callback
            )

        result["sha256_client"] = sha256_hash
        result["file_size"] = file_size
        return result

    def _upload_multipart(
        self, local_path: str, metadata: Dict[str, Any], mime_type: str
    ) -> Dict[str, Any]:
        """Performs standard multipart upload for files < 5 MB."""
        url = f"{DRIVE_UPLOAD_BASE}?uploadType=multipart&fields=id,name,mimeType,size,md5Checksum,webViewLink,createdTime"

        metadata_bytes = json.dumps(metadata).encode("utf-8")
        with open(local_path, "rb") as f:
            file_bytes = f.read()

        boundary = "StrictRecordsBoundaryMultipart12345"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode("utf-8") + metadata_bytes + (
            f"\r\n--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8") + file_bytes + (
            f"\r\n--{boundary}--\r\n"
        ).encode("utf-8")

        headers = self._headers({
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        })

        resp = requests.post(url, headers=headers, data=body, timeout=60)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Multipart upload failed: {resp.status_code} {resp.text}")

        return resp.json()

    def _upload_resumable(
        self,
        local_path: str,
        metadata: Dict[str, Any],
        mime_type: str,
        total_size: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Performs chunked resumable upload for files >= 5 MB."""
        init_url = (
            f"{DRIVE_UPLOAD_BASE}?uploadType=resumable&fields=id,name,mimeType,size,md5Checksum,webViewLink,createdTime"
        )
        init_headers = self._headers({
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(total_size),
            "Content-Type": "application/json; charset=UTF-8",
        })

        init_resp = requests.post(init_url, headers=init_headers, json=metadata, timeout=30)
        if init_resp.status_code != 200:
            raise RuntimeError(f"Resumable session initiation failed: {init_resp.status_code} {init_resp.text}")

        session_url = init_resp.headers.get("Location")
        if not session_url:
            raise RuntimeError("Drive API did not provide a Location header for resumable upload.")

        bytes_uploaded = 0
        with open(local_path, "rb") as f:
            while bytes_uploaded < total_size:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break

                chunk_len = len(chunk)
                start_byte = bytes_uploaded
                end_byte = start_byte + chunk_len - 1

                chunk_headers = {
                    "Content-Range": f"bytes {start_byte}-{end_byte}/{total_size}",
                    "Content-Length": str(chunk_len),
                    "Content-Type": mime_type,
                }

                chunk_resp = requests.put(
                    session_url, headers=chunk_headers, data=chunk, timeout=60
                )

                if chunk_resp.status_code in (200, 201):
                    # Finished upload
                    bytes_uploaded += chunk_len
                    if progress_callback:
                        progress_callback(bytes_uploaded, total_size)
                    return chunk_resp.json()
                elif chunk_resp.status_code == 308:
                    # Incomplete, continue
                    bytes_uploaded += chunk_len
                    if progress_callback:
                        progress_callback(bytes_uploaded, total_size)
                else:
                    raise RuntimeError(
                        f"Chunk upload failed at byte {start_byte}: {chunk_resp.status_code} {chunk_resp.text}"
                    )

        raise RuntimeError("Resumable upload loop finished without completion response.")

    def download_file(self, drive_file_id: str, destination_path: str) -> str:
        """
        Downloads a file from Google Drive and verifies integrity.
        """
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        url = f"{DRIVE_API_BASE}/files/{drive_file_id}?alt=media"

        resp = requests.get(url, headers=self._headers(), stream=True, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download file: {resp.status_code} {resp.text}")

        with open(destination_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        return destination_path

    def update_file_content(self, drive_file_id: str, local_path: str) -> Dict[str, Any]:
        """Updates binary contents of an existing Drive file."""
        sha256_hash, md5_hash, file_size = compute_file_hashes(local_path)
        mime_type = guess_mime_type(local_path)
        url = f"{DRIVE_UPLOAD_BASE}/{drive_file_id}?uploadType=media"
        with open(local_path, "rb") as f:
            data = f.read()
        resp = requests.patch(
            url,
            headers=self._headers({"Content-Type": mime_type}),
            data=data,
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to update file {drive_file_id}: {resp.status_code} {resp.text}")
        res = resp.json()
        res["sha256"] = sha256_hash
        res["size"] = file_size
        return res

    def list_files(
        self, folder_id: Optional[str] = None, page_size: int = 50
    ) -> List[Dict[str, Any]]:
        """Lists files in Drive, optionally filtered by folder ID."""
        query = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        if folder_id:
            query += f" and '{folder_id}' in parents"

        url = (
            f"{DRIVE_API_BASE}/files?q={requests.utils.quote(query)}"
            f"&fields=files(id,name,mimeType,size,md5Checksum,createdTime,properties)"
            f"&pageSize={page_size}"
        )

        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list files: {resp.status_code} {resp.text}")

        return resp.json().get("files", [])

    def delete_file(self, drive_file_id: str) -> None:
        """Deletes a file permanently from Google Drive."""
        url = f"{DRIVE_API_BASE}/files/{drive_file_id}"
        resp = requests.delete(url, headers=self._headers(), timeout=15)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Failed to delete file {drive_file_id}: {resp.status_code} {resp.text}")

    def download_file_bytes(self, drive_file_id: str) -> bytes:
        """Downloads raw bytes of a file from Google Drive."""
        url = f"{DRIVE_API_BASE}/files/{drive_file_id}?alt=media"
        resp = requests.get(url, headers=self._headers(), stream=True, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download file {drive_file_id}: {resp.status_code} {resp.text}")
        return resp.content

    def download_text(self, drive_file_id: str) -> str:
        """Downloads a text file from Google Drive."""
        return self.download_file_bytes(drive_file_id).decode("utf-8", errors="replace")

    def download_json(self, drive_file_id: str) -> Dict[str, Any]:
        """Downloads and parses a JSON file from Google Drive."""
        raw = self.download_text(drive_file_id)
        return json.loads(raw)

    def upload_json_data(
        self,
        category: str,
        filename: str,
        data: Dict[str, Any],
        description: str = "",
    ) -> Dict[str, Any]:
        """Directly writes JSON data to a temp file and uploads it to Drive."""
        import tempfile
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".json") as tmp:
            tmp.write(json_bytes)
            tmp_path = tmp.name

        try:
            folder_id = self.resolve_target_folder(category)
            existing = self.find_file_by_name(filename, folder_id)
            if existing:
                try:
                    self.delete_file(existing["id"])
                except Exception:
                    pass

            target_path = os.path.join(os.path.dirname(tmp_path), filename)
            if os.path.exists(target_path):
                os.remove(target_path)
            os.rename(tmp_path, target_path)
            res = self.upload_file(target_path, category=category, description=description)
            if os.path.exists(target_path):
                os.remove(target_path)
            return res
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def find_file_by_name(
        self, filename: str, folder_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Finds a single file by name in a folder."""
        query = f"name = '{filename}' and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        if folder_id:
            query += f" and '{folder_id}' in parents"
        url = (
            f"{DRIVE_API_BASE}/files?q={requests.utils.quote(query)}"
            f"&fields=files(id,name,mimeType,size,md5Checksum,createdTime,properties)"
            f"&pageSize=1"
        )
        resp = requests.get(url, headers=self._headers(), timeout=15)
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            return files[0] if files else None
        return None

    def parse_drive_error(self, e: Exception) -> Tuple[str, str, str]:
        """
        Parses Google Drive API error into (state, error_code, error_message).
        """
        err_str = str(e)
        if "401" in err_str or "Unauthorized" in err_str:
            return (
                DriveConnectionState.PERMISSION_DENIED,
                "UNAUTHORIZED",
                "Google Drive authentication token expired or invalid. Please re-authenticate."
            )
        if any(term in err_str.lower() for term in ["accessnotconfigured", "is disabled", "has not been used in project"]):
            return (
                DriveConnectionState.CONFIGURATION_ERROR,
                "DRIVE_API_DISABLED",
                "Google Drive API is disabled in your Google Cloud Console project. Please enable Google Drive API."
            )
        if any(term in err_str.lower() for term in ["insufficientpermissions", "insufficientfilepermissions", "403"]):
            return (
                DriveConnectionState.PERMISSION_DENIED,
                "INSUFFICIENT_PERMISSIONS",
                "StrictRecords lacks permission to create or manage application files in Google Drive. Please consent to drive.file scope."
            )
        if any(term in err_str.lower() for term in ["connection refused", "timeout", "name resolution", "failed to establish a new connection"]):
            return (
                DriveConnectionState.CONFIGURATION_ERROR,
                "NETWORK_ERROR",
                f"Network error connecting to Google Drive API: {err_str}"
            )
        return (
            DriveConnectionState.CONFIGURATION_ERROR,
            "DRIVE_API_ERROR",
            f"Google Drive API request failed: {err_str}"
        )

    def verify_drive_connection(self) -> Dict[str, Any]:
        """
        Executes strict live Drive verification sequence:
        1. Calls /about endpoint to verify identity and check storage quota.
        2. Creates or locates root StrictRecords folder.
        3. Uploads uniquely-named temporary test marker file.
        4. Downloads marker file and verifies integrity via nonce.
        5. Deletes temporary test marker file.
        6. Ensures complete folder hierarchy is initialized.
        Returns structured verification result dictionary.
        """
        import time
        import uuid

        try:
            # 1. Quota & Identity Check
            quota = self.get_storage_quota()
            expected = self._expected_email_getter() if self._expected_email_getter else ""
            if expected and str(quota.get("user_email", "")).casefold() != expected.casefold():
                raise PermissionError("Drive token belongs to a different Google account")
        except Exception as e:
            state, code, msg = self.parse_drive_error(e)
            logger.warning("Drive verification quota check failed (%s): %s", code, msg)
            return {
                "success": False,
                "state": state,
                "error_code": code,
                "error_message": msg,
            }

        # 2. Application root folder
        try:
            root_id = self._find_or_create_folder("StrictRecords", parent_id=None)
        except Exception as e:
            state, code, msg = self.parse_drive_error(e)
            logger.warning("Drive verification root folder check failed (%s): %s", code, msg)
            return {
                "success": False,
                "state": state,
                "error_code": code,
                "error_message": msg,
            }

        # 3. Unique test marker upload
        try:
            response = requests.get(
                f"{DRIVE_API_BASE}/files/{requests.utils.quote(root_id)}?fields=id,name,mimeType,trashed,ownedByMe,owners(emailAddress,permissionId)",
                headers=self._headers(), timeout=15)
            if response.status_code != 200:
                raise PermissionError("Application folder is not accessible")
            folder = response.json()
            expected_email = str(quota.get("user_email") or "").casefold()
            if (folder.get("ownedByMe") is not True or folder.get("trashed") or
                    folder.get("mimeType") != "application/vnd.google-apps.folder" or folder.get("name") != "StrictRecords" or
                    not expected_email or not any(str(owner.get("emailAddress") or "").casefold() == expected_email for owner in folder.get("owners", []))):
                raise PermissionError("Application folder does not belong to the authenticated Google account")
        except Exception:
            self.clear_cache()
            return {"success": False, "state": DriveConnectionState.CONFIGURATION_ERROR,
                    "error_code": "FOLDER_OWNER_MISMATCH", "error_message": "The StrictRecords folder ownership could not be verified for this account."}

        # 3. Unique test marker upload
        nonce = uuid.uuid4().hex
        marker_name = f"strictrecords_verify_{int(time.time())}_{nonce[:8]}.json"
        marker_data = {
            "client": "StrictRecords Windows",
            "nonce": nonce,
            "timestamp": time.time(),
            "verification": True,
        }

        marker_file_id = None
        try:
            upload_res = self.upload_json_data(
                category="StrictRecords",
                filename=marker_name,
                data=marker_data,
                description="StrictRecords temporary Drive connection verification marker"
            )
            marker_file_id = upload_res.get("id")

            # 4. Read marker back and verify nonce
            if not marker_file_id:
                raise RuntimeError("Drive upload succeeded but returned no file ID.")

            downloaded = self.download_json(marker_file_id)
            if downloaded.get("nonce") != nonce:
                raise ValueError("Verification integrity mismatch: downloaded marker nonce did not match.")

            # 5. Delete marker
            try:
                self.delete_file(marker_file_id)
            except Exception as e:
                logger.debug("Test marker cleanup notice: %s", e)

            # 6. Ensure full hierarchy
            hierarchy = self.ensure_folder_hierarchy()

            logger.info("Drive verification succeeded for user: %s", quota.get("user_email"))
            return {
                "success": True,
                "state": DriveConnectionState.DRIVE_CONNECTED_INITIALIZED,
                "root_folder_id": root_id,
                "user_email": quota.get("user_email", ""),
                "display_name": quota.get("display_name", ""),
                "storage_usage_bytes": quota.get("usage_bytes", 0),
                "storage_limit_bytes": quota.get("limit_bytes", 0),
                "free_bytes": quota.get("free_bytes", 0),
                "is_below_5gb": quota.get("is_below_5gb", False),
                "hierarchy_count": len(hierarchy),
            }
        except Exception as e:
            if marker_file_id:
                try:
                    self.delete_file(marker_file_id)
                except Exception:
                    pass
            state, code, msg = self.parse_drive_error(e)
            logger.warning("Drive verification failed (%s): %s", code, msg)
            return {
                "success": False,
                "state": state,
                "error_code": code,
                "error_message": msg,
            }
