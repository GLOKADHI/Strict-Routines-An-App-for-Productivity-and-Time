"""
StrictRecords — Windows Google Authentication Module (RFC 7636 PKCE & RFC 8252 Loopback)
Provides secure desktop OAuth 2.0 authentication for dedicated Google accounts.
Encrypted token storage via Windows DPAPI (CryptProtectData).
"""

import base64
import hashlib
import html
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("StrictRecords.GoogleAuth")

try:
    import win32crypt
    HAS_WIN32CRYPT = True
except ImportError:
    HAS_WIN32CRYPT = False

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Connection Stages (Real connection sequence)
STAGE_IDLE = "IDLE"
STAGE_ACCOUNT_SELECTED = "ACCOUNT_SELECTED"
STAGE_SIGNING_IN = "SIGNING_IN"
STAGE_ACCOUNT_CONNECTED = "ACCOUNT_CONNECTED"
STAGE_REQUESTING_DRIVE = "REQUESTING_DRIVE"
STAGE_VERIFYING_DRIVE = "VERIFYING_DRIVE"
STAGE_DRIVE_CONNECTED = "DRIVE_CONNECTED"
STAGE_READY_TO_SYNC = "READY_TO_SYNC"

# Three Distinct Status Indicators
# 1. Google Account Status
ACCOUNT_STATUS_NOT_CONNECTED = "Not connected"
ACCOUNT_STATUS_SIGNING_IN = "Signing in"
ACCOUNT_STATUS_CONNECTED = "Connected"
ACCOUNT_STATUS_AUTH_FAILED = "Authentication failed"
ACCOUNT_STATUS_REAUTH_REQUIRED = "Reauthentication required"

# 2. Google Drive Status
DRIVE_STATUS_NOT_AUTHORIZED = "Not authorized"
DRIVE_STATUS_AUTHORIZATION_REQUIRED = "Authorization required"
DRIVE_STATUS_AUTHORIZING = "Authorizing"
DRIVE_STATUS_VERIFYING_ACCESS = "Verifying access"
DRIVE_STATUS_CONNECTED = "Connected"
DRIVE_STATUS_PERMISSION_DENIED = "Permission denied"
DRIVE_STATUS_CONNECTION_FAILED = "Connection failed"

# 3. Synchronization Status
SYNC_STATUS_NOT_INITIALIZED = "Not initialized"
SYNC_STATUS_INITIALIZING = "Initializing"
SYNC_STATUS_READY = "Ready"
SYNC_STATUS_SYNCING = "Syncing"
SYNC_STATUS_SYNCED = "Synced"
SYNC_STATUS_SYNC_FAILED = "Sync failed"
SYNC_STATUS_OFFLINE = "Offline"

# Suggested User Actions on Failure
ACTION_RETRY = "Retry"
ACTION_GRANT_DRIVE = "Grant Drive Access"
ACTION_RECONNECT = "Reconnect Google Account"

IDENTITY_ONLY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

DEFAULT_SCOPES = [
    *IDENTITY_ONLY_SCOPES,
    DRIVE_FILE_SCOPE,
]

DRIVE_AUTH_REQUIRED_MESSAGE = "Google account connected — Drive authorization required."

SESSION_FILE_PATH = os.path.join("data", "google_session.enc")
CONFIG_FILE_PATH = os.path.join("data", "google_client_config.json")


def runtime_google_auth_dir(db_path: str) -> str:
    """User credentials never belong to a source checkout or executable bundle."""
    appdata = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    if appdata:
        return os.path.join(appdata, "StrictRecords", "GoogleAuth")
    return os.path.dirname(os.path.abspath(db_path))


def _dpapi_encrypt(data_bytes: bytes) -> bytes:
    """Encrypts bytes using Windows DPAPI tied to current user session."""
    if HAS_WIN32CRYPT:
        return win32crypt.CryptProtectData(
            data_bytes,
            "StrictRecords Google Token",
            None,
            None,
            None,
            0
        )
    if os.name == "nt":
        raise RuntimeError("Windows credential encryption is unavailable. Install pywin32 before signing in.")
    # Non-Windows test hosts only; Windows never writes plaintext-equivalent tokens.
    return base64.b64encode(data_bytes)


def _dpapi_decrypt(cipher_bytes: bytes) -> bytes:
    """Decrypts bytes using Windows DPAPI."""
    if HAS_WIN32CRYPT:
        _, decrypted = win32crypt.CryptUnprotectData(
            cipher_bytes,
            None,
            None,
            None,
            0
        )
        return decrypted
    # Non-Windows or testing fallback
    return base64.b64decode(cipher_bytes)


def generate_pkce_pair() -> Tuple[str, str]:
    """Generates PKCE code_verifier and code_challenge (S256)."""
    verifier_bytes = secrets.token_bytes(64)
    verifier = base64.urlsafe_b64encode(verifier_bytes).decode("utf-8").rstrip("=")
    # RFC 7636: challenge = base64url(sha256(verifier)) without padding
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


DEFAULT_CLIENT_ID = "891283897103-strictrecords.apps.googleusercontent.com"


class GoogleAuthConfig:
    """Manages Google OAuth client credentials."""

    def __init__(self, config_path: str = CONFIG_FILE_PATH):
        self.config_path = config_path
        self.client_id = os.environ.get("STRICTRECORDS_GOOGLE_CLIENT_ID", "")
        self.client_secret = os.environ.get("STRICTRECORDS_GOOGLE_CLIENT_SECRET", "")
        self.load()
        # The former embedded value was a placeholder, not a registered OAuth client.
        if self.client_id == DEFAULT_CLIENT_ID:
            self.client_id = ""

    def load(self) -> None:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # Support standard web/installed or direct client_id format
                    if "installed" in cfg:
                        installed = cfg["installed"]
                        self.client_id = installed.get("client_id", self.client_id)
                        self.client_secret = installed.get("client_secret", self.client_secret)
                    elif "web" in cfg:
                        raise ValueError("Use a Desktop app OAuth client JSON, not a Web application client")
                    else:
                        self.client_id = cfg.get("client_id", self.client_id)
                        self.client_secret = cfg.get("client_secret", self.client_secret)
            except Exception as e:
                logger.warning("Failed to load Google client config: %s", e)

    def save(self, client_id: str, client_secret: str = "") -> None:
        candidate = client_id.strip()
        if candidate == DEFAULT_CLIENT_ID or not candidate.endswith(".apps.googleusercontent.com") or any(c.isspace() for c in candidate):
            raise ValueError("Supply a valid Google Desktop OAuth client ID")
        os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
        temporary = self.config_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump({
                "client_id": candidate,
                "client_secret": client_secret.strip(),
                "auth_uri": GOOGLE_AUTH_URL,
                "token_uri": GOOGLE_TOKEN_URL,
            }, f, indent=2)
        os.replace(temporary, self.config_path)
        self.client_id = candidate
        self.client_secret = client_secret.strip()

    def import_document(self, document: Dict[str, Any]) -> None:
        if not isinstance(document, dict) or "web" in document:
            raise ValueError("Choose the OAuth JSON downloaded for a Desktop app")
        values = document.get("installed", document)
        if not isinstance(values, dict):
            raise ValueError("Invalid Desktop OAuth JSON")
        self.save(str(values.get("client_id") or ""), str(values.get("client_secret") or ""))

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_id != DEFAULT_CLIENT_ID and self.client_id.endswith(".apps.googleusercontent.com"))


class LoopbackCallbackHandler(BaseHTTPRequestHandler):
    """Handles OAuth 2.0 loopback redirect from Google."""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        state = query.get("state", [""])[0]
        expected = getattr(self.server, "expected_state", "")
        if parsed.path != "/callback":
            self.send_error(404)
            return
        if not expected or not secrets.compare_digest(state, expected):
            self.send_error(400, "Invalid sign-in state; return to the app and retry")
            return
        code = query.get("code", [None])[0]
        error = query.get("error", [None])[0]
        if not code and not error:
            self.send_error(400, "Missing authorization result")
            return
        if self.server.auth_result is not None:
            self.send_error(409, "This callback has already been received")
            return
        self.server.auth_result = {"code": code, "state": state, "error": error}
        message = ("Google returned: " + html.escape(str(error))) if error else "Authorization response received. Return to StrictRoutines while the app validates your account and checks Drive access."
        page = ("<!doctype html><html><head><meta charset='utf-8'><title>StrictRoutines sign-in</title>"
                "<style>body{background:#0d1420;color:#e7f1fa;font:16px Segoe UI,sans-serif;max-width:560px;margin:12vh auto;padding:24px;line-height:1.7}</style>"
                "</head><body><h1>Return to StrictRoutines</h1><p>" + message + "</p>"
                "<p>Receiving this response does not yet confirm sign-in, Drive access or synchronization.</p></body></html>")
        self.send_response(400 if error else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard HTTP request logging
        pass


class GoogleAuthManager:
    """Authoritative Windows Google OAuth 2.0 PKCE & Session Manager."""

    def __init__(
        self,
        config: Optional[GoogleAuthConfig] = None,
        session_file: str = SESSION_FILE_PATH,
        folder_registry_file: Optional[str] = None,
    ):
        self.config = config or GoogleAuthConfig()
        self.session_file = session_file
        base_dir = os.path.dirname(session_file) or "data"
        self.folder_registry_file = folder_registry_file or os.path.join(base_dir, "google_drive_folders.json")
        self._lock = threading.RLock()
        self._session_data: Optional[Dict[str, Any]] = None
        self.current_stage: str = STAGE_IDLE
        self.pending_auth: bool = False
        self.error_details: Optional[Dict[str, Any]] = None
        self.on_account_switched: Optional[Any] = None

        self.account_status: str = ACCOUNT_STATUS_NOT_CONNECTED
        self.drive_status: str = DRIVE_STATUS_NOT_AUTHORIZED
        self.sync_status: str = SYNC_STATUS_NOT_INITIALIZED
        self._active_loopback_server: Optional[Any] = None

        self.load_session()
        self._last_known_sub: str = self.get_account_sub()
        self._initialize_status_from_session()

    def get_account_sub(self) -> str:
        """Returns the immutable Google subject identifier (sub) for the connected account."""
        prof = self.get_user_profile() or {}
        return str(prof.get("sub") or prof.get("uid") or "").strip()

    def _load_folder_registry(self) -> Dict[str, Any]:
        if not os.path.exists(self.folder_registry_file):
            return {}
        try:
            with open(self.folder_registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_folder_registry(self, registry: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.folder_registry_file) or ".", exist_ok=True)
        with open(self.folder_registry_file, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)

    def get_registered_root_folder_id(self, sub: Optional[str] = None) -> Optional[str]:
        """Returns the persisted StrictRecords Drive root folder ID strictly scoped to the given Google sub."""
        with self._lock:
            target_sub = str(sub if sub is not None else self.get_account_sub()).strip()
            if not target_sub:
                return None
            if self._session_data:
                sess_sub = str((self._session_data.get("user") or {}).get("sub") or "").strip()
                sess_fid = str(self._session_data.get("root_folder_id") or "").strip()
                if sess_sub == target_sub and sess_fid:
                    return sess_fid
            entry = self._load_folder_registry().get(target_sub)
            if isinstance(entry, dict) and entry.get("root_folder_id"):
                return str(entry["root_folder_id"]).strip()
            return None

    def set_registered_root_folder_id(self, folder_id: str, sub: Optional[str] = None) -> None:
        """Persists the shared StrictRecords Drive root folder ID strictly under the authenticated Google sub."""
        fid = str(folder_id or "").strip()
        if not fid:
            return
        with self._lock:
            target_sub = str(sub if sub is not None else self.get_account_sub()).strip()
            if self._session_data:
                sess_sub = str((self._session_data.get("user") or {}).get("sub") or "").strip()
                if not target_sub or sess_sub == target_sub:
                    self._session_data["root_folder_id"] = fid
                    self.save_session(self._session_data)
            if target_sub:
                reg = self._load_folder_registry()
                reg[target_sub] = {
                    "root_folder_id": fid,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._save_folder_registry(reg)

    def _initialize_status_from_session(self) -> None:
        """Initializes 3 status indicators and stage based on existing session."""
        with self._lock:
            if not self._session_data:
                self.account_status = ACCOUNT_STATUS_NOT_CONNECTED
                self.drive_status = DRIVE_STATUS_NOT_AUTHORIZED
                self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                self.current_stage = STAGE_IDLE
                return

            access_token = self._session_data.get("access_token")
            refresh_token = self._session_data.get("refresh_token")
            if not access_token and not refresh_token:
                self.account_status = ACCOUNT_STATUS_NOT_CONNECTED
                self.drive_status = DRIVE_STATUS_NOT_AUTHORIZED
                self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                self.current_stage = STAGE_IDLE
                return

            self.account_status = ACCOUNT_STATUS_CONNECTED
            scopes = str(self._session_data.get("scope", ""))
            drive_verified = bool(self._session_data.get("drive_verified", False))
            if DRIVE_FILE_SCOPE in scopes.split():
                if drive_verified:
                    self.drive_status = DRIVE_STATUS_CONNECTED
                    self.sync_status = SYNC_STATUS_READY
                    self.current_stage = STAGE_READY_TO_SYNC
                else:
                    drive_error = self._session_data.get("drive_error")
                    self.drive_status = DRIVE_STATUS_CONNECTION_FAILED if drive_error else DRIVE_STATUS_AUTHORIZATION_REQUIRED
                    self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                    self.current_stage = STAGE_VERIFYING_DRIVE
            else:
                self.drive_status = DRIVE_STATUS_AUTHORIZATION_REQUIRED
                self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                self.current_stage = STAGE_REQUESTING_DRIVE

    def get_detailed_connection_status(self) -> Dict[str, Any]:
        """Returns distinct 3-indicator status, stage sequence, user profile, folder binding, and errors."""
        with self._lock:
            is_auth = self.is_authenticated()
            user_info = None
            account_sub = ""
            root_folder_id = None
            # Strictly display user info ONLY when Google account is authenticated and connected
            if is_auth and self.account_status == ACCOUNT_STATUS_CONNECTED:
                user_info = self.get_user_profile()
                account_sub = str((user_info or {}).get("sub") or "").strip()
                if self.drive_status == DRIVE_STATUS_CONNECTED:
                    root_folder_id = self.get_registered_root_folder_id(account_sub)

            if is_auth and self.account_status == ACCOUNT_STATUS_CONNECTED and self.drive_status in (
                DRIVE_STATUS_NOT_AUTHORIZED,
                DRIVE_STATUS_AUTHORIZATION_REQUIRED,
                DRIVE_STATUS_PERMISSION_DENIED,
            ):
                status_message = DRIVE_AUTH_REQUIRED_MESSAGE
            elif is_auth and self.account_status == ACCOUNT_STATUS_CONNECTED and self.drive_status == DRIVE_STATUS_CONNECTED:
                status_message = "Google account and Google Drive connected."
            elif self.error_details and self.error_details.get("message"):
                status_message = self.error_details["message"]
            else:
                status_message = f"Account: {self.account_status} | Drive: {self.drive_status}"

            return {
                "stage": self.current_stage,
                "account_status": self.account_status,
                "drive_status": self.drive_status,
                "sync_status": self.sync_status,
                "status_message": status_message,
                "is_authenticated": is_auth,
                "account_sub": account_sub or None,
                "root_folder_id": root_folder_id,
                "user": user_info,
                "error": self.error_details,
                "last_error": (self.error_details or {}).get("code"),
                "recommended_action": (self.error_details or {}).get("action"),
                "in_progress": self.pending_auth,
                "configured": self.config.is_configured,
                "sync_enabled": bool((self.get_session() or {}).get("sync_enabled", False)),
            }

    def set_sync_state(self, sync_status: str, error_msg: Optional[str] = None) -> None:
        """Updates synchronization status indicator and error state."""
        with self._lock:
            self.sync_status = sync_status
            if error_msg:
                self.error_details = {
                    "stage": STAGE_READY_TO_SYNC,
                    "code": "SYNC_FAILED",
                    "message": error_msg,
                    "action": ACTION_RETRY,
                }
            elif sync_status in (SYNC_STATUS_READY, SYNC_STATUS_SYNCED):
                if self.error_details and self.error_details.get("code") == "SYNC_FAILED":
                    self.error_details = None

    def set_error(self, stage: str, code: str, message: str, action: str = ACTION_RETRY) -> None:
        """Sets safe error details for UI consumption."""
        with self._lock:
            self.error_details = {
                "stage": stage,
                "code": code,
                "message": message,
                "action": action,
            }

    def handle_connection_failure(
        self,
        stage: str,
        error_code: str,
        message: str,
        action: str = ACTION_RETRY,
    ) -> None:
        """Handles authentication or drive authorization failures cleanly with observable error details."""
        with self._lock:
            self.current_stage = stage
            if stage == STAGE_SIGNING_IN:
                self.account_status = ACCOUNT_STATUS_AUTH_FAILED
            elif stage in (STAGE_REQUESTING_DRIVE, STAGE_VERIFYING_DRIVE):
                if error_code == "DRIVE_PERMISSION_DENIED":
                    self.drive_status = DRIVE_STATUS_PERMISSION_DENIED
                else:
                    self.drive_status = DRIVE_STATUS_CONNECTION_FAILED
            self.error_details = {
                "stage": stage,
                "code": error_code,
                "message": message,
                "action": action,
            }
            self.pending_auth = False

    def load_session(self) -> Optional[Dict[str, Any]]:
        """Loads and decrypts saved session from DPAPI storage."""
        with self._lock:
            if not os.path.exists(self.session_file):
                self._session_data = None
                return None
            try:
                with open(self.session_file, "rb") as f:
                    cipher_bytes = f.read()
                plain_bytes = _dpapi_decrypt(cipher_bytes)
                self._session_data = json.loads(plain_bytes.decode("utf-8"))
                return self._session_data
            except Exception as e:
                logger.error("Failed to decrypt or parse Google session: %s", e)
                self._session_data = None
                return None

    def save_session(self, session_dict: Dict[str, Any]) -> None:
        """Encrypts and persists session using DPAPI and detects account switches by immutable sub."""
        switched_pair: Optional[Tuple[str, str]] = None
        callback = None
        with self._lock:
            new_sub = str(((session_dict or {}).get("user") or {}).get("sub") or "").strip()
            prev_sub = self._last_known_sub
            if prev_sub and new_sub and prev_sub != new_sub:
                switched_pair = (prev_sub, new_sub)
                callback = self.on_account_switched
            if new_sub:
                if not session_dict.get("root_folder_id"):
                    existing_fid = self._load_folder_registry().get(new_sub, {})
                    if isinstance(existing_fid, dict) and existing_fid.get("root_folder_id"):
                        session_dict["root_folder_id"] = existing_fid["root_folder_id"]
            os.makedirs(os.path.dirname(self.session_file) or ".", exist_ok=True)
            cipher_bytes = _dpapi_encrypt(json.dumps(session_dict).encode("utf-8"))
            temporary = self.session_file + ".tmp"
            with open(temporary, "wb") as f:
                f.write(cipher_bytes)
            os.replace(temporary, self.session_file)
            self._session_data = session_dict
            if new_sub:
                self._last_known_sub = new_sub
            if (session_dict or {}).get("access_token"):
                self.account_status = ACCOUNT_STATUS_CONNECTED
                if (session_dict or {}).get("drive_verified"):
                    self.drive_status = DRIVE_STATUS_CONNECTED
                elif self.drive_status not in (DRIVE_STATUS_CONNECTION_FAILED, DRIVE_STATUS_PERMISSION_DENIED):
                    self.drive_status = DRIVE_STATUS_AUTHORIZATION_REQUIRED
        if switched_pair and callback:
            try:
                callback(switched_pair[0], switched_pair[1])
            except Exception as err:
                logger.warning("Account switch isolation callback error: %s", err)

    def clear_session(self) -> None:
        """Clears local session and deletes DPAPI encrypted storage."""
        with self._lock:
            self._session_data = None
            if os.path.exists(self.session_file):
                try:
                    os.remove(self.session_file)
                except OSError as e:
                    logger.warning("Could not delete session file: %s", e)

    def is_authenticated(self) -> bool:
        """Returns True if a valid session exists with access or refresh tokens."""
        session = self.get_session()
        if not session:
            return False
        return bool(session.get("access_token") or session.get("refresh_token"))

    def is_drive_connected(self) -> bool:
        """Returns True only when Google account is authenticated AND Drive verification passed."""
        if not self.is_authenticated():
            return False
        session = self.get_session() or {}
        return bool(session.get("drive_verified", False)) and bool(self.get_valid_access_token())

    def get_session(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._session_data

    def get_user_profile(self) -> Dict[str, Any]:
        session = self.get_session()
        if not session:
            return {}
        return session.get("user", {})

    def get_valid_access_token(self) -> Optional[str]:
        """
        Returns a valid access token, automatically refreshing if expired or expiring soon.
        """
        session = self.get_session()
        if not session:
            return None

        expires_at = session.get("expires_at", 0)
        now = time.time()

        # If token is still valid for at least 5 minutes, return it
        if expires_at - now > 300:
            return session.get("access_token")

        refresh_token = session.get("refresh_token")
        if not refresh_token:
            # If no refresh token, return current access token if not completely expired
            if expires_at > now:
                return session.get("access_token")
            return None

        # Perform token refresh
        refreshed = self.refresh_token(refresh_token)
        if refreshed and "access_token" in refreshed:
            return refreshed["access_token"]

        return None

    def refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refreshes access token with Google OAuth token endpoint."""
        if not self.config.is_configured:
            logger.error("Cannot refresh token: Client ID is not configured.")
            return None

        payload = {
            "client_id": self.config.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if self.config.client_secret:
            payload["client_secret"] = self.config.client_secret

        try:
            resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=15)
            if resp.status_code != 200:
                logger.error("Token refresh failed: HTTP %s", resp.status_code)
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_REAUTH_REQUIRED
                    self.drive_status = DRIVE_STATUS_AUTHORIZATION_REQUIRED
                    self.sync_status = SYNC_STATUS_OFFLINE
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": f"REFRESH_FAILED_{resp.status_code}",
                        "message": "Session token expired or revoked. Please reconnect your Google account.",
                        "action": ACTION_RECONNECT,
                    }
                return None

            data = resp.json()
            if not isinstance(data.get("access_token"), str) or not data["access_token"].strip():
                raise ValueError("Google refresh response did not contain an access token")
            if int(data.get("expires_in", 0)) <= 0:
                raise ValueError("Google refresh response had an invalid expiration")
            session = self.get_session() or {}
            session["access_token"] = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            session["expires_at"] = time.time() + expires_in
            if "refresh_token" in data:
                session["refresh_token"] = data["refresh_token"]

            self.save_session(session)
            with self._lock:
                self.account_status = ACCOUNT_STATUS_CONNECTED
                if self.error_details and "REFRESH" in str(self.error_details.get("code", "")):
                    self.error_details = None
            logger.info("Successfully refreshed Google OAuth token.")
            return session
        except Exception as e:
            logger.error("Token refresh could not complete: %s", type(e).__name__)
            with self._lock:
                self.account_status = ACCOUNT_STATUS_REAUTH_REQUIRED
                self.drive_status = DRIVE_STATUS_AUTHORIZATION_REQUIRED
                self.sync_status = SYNC_STATUS_OFFLINE
                self.error_details = {"stage": STAGE_SIGNING_IN, "code": "REFRESH_FAILED",
                    "message": "Google could not refresh this session. Reconnect the account.", "action": ACTION_RECONNECT}
            return None

    def build_authorization_url(
        self,
        redirect_uri: str = "http://127.0.0.1:8765/callback",
        code_challenge: str = "challenge123",
        state: str = "state123",
        include_drive_scope: bool = True,
        login_hint: str = "",
        force_account_select: bool = False,
    ) -> str:
        """Constructs RFC 7636 PKCE authorization URL with narrow or combined scopes."""
        requested_scopes = DEFAULT_SCOPES if include_drive_scope else IDENTITY_ONLY_SCOPES
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(requested_scopes),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        if login_hint:
            params["login_hint"] = login_hint
        params["prompt"] = "select_account consent" if force_account_select else "consent"
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def handle_oauth_callback(self, code: Optional[str], state: Optional[str], error: Optional[str]) -> bool:
        """Handles OAuth callback delivered to central server, forwarding to active loopback flow."""
        with self._lock:
            active = getattr(self, "_active_loopback_server", None)
            expected = getattr(active, "expected_state", "") if active else ""
            if active and not active.auth_result and expected and state and secrets.compare_digest(state, expected) and (code or error):
                self._active_loopback_server.auth_result = {
                    "code": code,
                    "state": state,
                    "error": error,
                }
                return True
        return False

    def start_pkce_flow(
        self,
        port: int = 0,
        timeout_seconds: int = 120,
        open_browser: bool = True,
        drive_verifier: Optional[Any] = None,
        sync_initializer: Optional[Any] = None,
        include_drive_scope: bool = True,
        login_hint: str = "",
        force_account_select: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes complete RFC 7636 PKCE & RFC 8252 Loopback authentication flow
        with visible real-time multi-stage status tracking:
        1. Account selected — Google account selection returned successfully; validating.
        2. Signing in — Identity verification flow in progress.
        3. Google account connected — Authentication succeeded, session established.
        4. Requesting Drive access — Requesting Google Drive authorization scope.
        5. Verifying Drive access — Testing live Drive API access & storage quota.
        6. Google Drive connected — Drive API verification succeeded.
        7. Ready to sync — Account & Drive connected, sync service initialized.
        """
        with self._lock:
            if self.pending_auth:
                raise RuntimeError("A Google sign-in is already in progress. Finish or cancel it before retrying.")
            self.pending_auth = True
            self.error_details = None
            self.current_stage = STAGE_SIGNING_IN
            self.account_status = ACCOUNT_STATUS_SIGNING_IN
            self.drive_status = DRIVE_STATUS_NOT_AUTHORIZED
            self.sync_status = SYNC_STATUS_NOT_INITIALIZED

        if not self.config.is_configured:
            err_msg = (
                "Google Client ID is not configured. Please supply a Desktop OAuth Client ID "
                "in data/google_client_config.json or set STRICTRECORDS_GOOGLE_CLIENT_ID."
            )
            with self._lock:
                self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                self.error_details = {
                    "stage": STAGE_SIGNING_IN,
                    "code": "CONFIG_ERROR",
                    "message": err_msg,
                    "action": ACTION_RETRY,
                }
                self.pending_auth = False
            raise ValueError(err_msg)

        try:
            # 1. Ephemeral loopback server
            server = HTTPServer(("127.0.0.1", port), LoopbackCallbackHandler)
            actual_port = server.server_port
            redirect_uri = f"http://127.0.0.1:{actual_port}/callback"
            server.auth_result = None
            with self._lock:
                self._active_loopback_server = server

            # 2. PKCE parameters
            verifier, challenge = generate_pkce_pair()
            state = secrets.token_urlsafe(32)
            server.expected_state = state

            # 3. Construct Auth URL
            requested_scopes = DEFAULT_SCOPES if include_drive_scope else IDENTITY_ONLY_SCOPES
            prompt_value = "select_account consent" if force_account_select else "consent"
            params = {
                "client_id": self.config.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(requested_scopes),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": prompt_value,  # Ensures refresh_token is returned
            }
            if login_hint:
                params["login_hint"] = login_hint
            auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

            # 4. Browser launch
            if open_browser:
                import webbrowser
                threading.Thread(target=lambda: webbrowser.open(auth_url), daemon=True).start()

            # 5. Wait for request with timeout
            server.timeout = timeout_seconds
            start_t = time.time()
            while time.time() - start_t < timeout_seconds:
                server.timeout = min(1.0, max(0.1, timeout_seconds - (time.time() - start_t)))
                server.handle_request()
                if server.auth_result is not None:
                    break

            server.server_close()
            with self._lock:
                self._active_loopback_server = None

            result = server.auth_result
            if not result:
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": "TIMEOUT",
                        "message": "Google sign-in timed out waiting for browser callback.",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise TimeoutError("Google sign-in timed out waiting for browser callback.")

            if result.get("error"):
                err_code = str(result["error"])
                is_cancelled = err_code in ("access_denied", "consent_declined")
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_ACCOUNT_SELECTED if is_cancelled else STAGE_SIGNING_IN,
                        "code": "USER_CANCELLED" if is_cancelled else "AUTH_FAILED",
                        "message": "Google sign-in was cancelled by the user." if is_cancelled else f"Google sign-in returned error: {err_code}",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise RuntimeError(self.error_details["message"])

            if result.get("state") != state:
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": "CSRF_ERROR",
                        "message": "OAuth state mismatch detected.",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise ValueError("OAuth state mismatch; potential CSRF detected.")

            code = result.get("code")
            if not code:
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": "NO_CODE",
                        "message": "No authorization code received from Google.",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise RuntimeError("No authorization code received from Google.")

            # Stage 1: Account selected
            with self._lock:
                self.current_stage = STAGE_ACCOUNT_SELECTED
            time.sleep(0.3)

            # Stage 2: Signing in
            with self._lock:
                self.current_stage = STAGE_SIGNING_IN

            # 6. Exchange code for tokens
            token_payload = {
                "client_id": self.config.client_id,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
            if self.config.client_secret:
                token_payload["client_secret"] = self.config.client_secret

            try:
                resp = requests.post(GOOGLE_TOKEN_URL, data=token_payload, timeout=20)
            except requests.RequestException as net_err:
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": "NETWORK_ERROR",
                        "message": f"Network unavailable during token exchange: {net_err}",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise RuntimeError(f"Network error during token exchange: {net_err}")

            if resp.status_code != 200:
                with self._lock:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                    self.error_details = {
                        "stage": STAGE_SIGNING_IN,
                        "code": f"TOKEN_HTTP_{resp.status_code}",
                        "message": f"Token exchange failed: HTTP {resp.status_code}",
                        "action": ACTION_RETRY,
                    }
                    self.pending_auth = False
                raise RuntimeError(f"Failed to exchange code for tokens: HTTP {resp.status_code}")

            token_data = resp.json()
            access_token = token_data.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("Google did not return an access token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            scope = token_data.get("scope", "")

            # 7. Fetch user profile
            user_info = {}
            try:
                u_resp = requests.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10
                )
                if u_resp.status_code == 200:
                    user_info = u_resp.json()
            except Exception as e:
                logger.warning("Could not fetch user profile: %s", e)

            if not user_info.get("sub") or not user_info.get("email") or user_info.get("email_verified") is not True:
                raise ValueError("Google identity could not be verified. No session was saved.")
            previous = self.get_session() or {}
            if not refresh_token and previous.get("user", {}).get("sub") == user_info.get("sub"):
                refresh_token = previous.get("refresh_token")

            # 8. Persist session
            session = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": time.time() + expires_in,
                "token_type": token_data.get("token_type", "Bearer"),
                "scope": scope,
                "user": {
                    "email": user_info.get("email", ""),
                    "name": user_info.get("name", ""),
                    "picture": user_info.get("picture", ""),
                    "sub": user_info.get("sub", ""),
                },
                "signed_in_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "sync_enabled": False,
            }
            self.save_session(session)

            # Stage 3: Google account connected
            with self._lock:
                self.account_status = ACCOUNT_STATUS_CONNECTED
                self.current_stage = STAGE_ACCOUNT_CONNECTED
            logger.info("Stage 3 reached: Google account connected")
            time.sleep(0.3)

            if not include_drive_scope:
                with self._lock:
                    self.drive_status = DRIVE_STATUS_AUTHORIZATION_REQUIRED
                    self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                    self.error_details = {
                        "stage": STAGE_REQUESTING_DRIVE,
                        "code": "DRIVE_AUTHORIZATION_REQUIRED",
                        "message": DRIVE_AUTH_REQUIRED_MESSAGE,
                        "action": ACTION_GRANT_DRIVE,
                    }
                    self.pending_auth = False
                return session

            # Stage 4: Requesting Drive access
            with self._lock:
                self.current_stage = STAGE_REQUESTING_DRIVE
                self.drive_status = DRIVE_STATUS_AUTHORIZING
            logger.info("Stage 4 reached: Requesting Drive access")
            time.sleep(0.3)

            # Verify Drive Scope
            has_drive_scope = DRIVE_FILE_SCOPE in scope.split()
            if not has_drive_scope:
                with self._lock:
                    self.drive_status = DRIVE_STATUS_PERMISSION_DENIED
                    self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                    self.error_details = {
                        "stage": STAGE_REQUESTING_DRIVE,
                        "code": "DRIVE_PERMISSION_DENIED",
                        "message": f"{DRIVE_AUTH_REQUIRED_MESSAGE} Google Drive access scope was not granted.",
                        "action": ACTION_GRANT_DRIVE,
                    }
                    self.pending_auth = False
                logger.warning("Google Drive scope missing from token grant.")
                return session

            # Stage 5: Verifying Drive access
            with self._lock:
                self.current_stage = STAGE_VERIFYING_DRIVE
                self.drive_status = DRIVE_STATUS_VERIFYING_ACCESS
            logger.info("Stage 5 reached: Verifying Drive access")
            time.sleep(0.3)

            verification_succeeded = False
            if drive_verifier:
                try:
                    quota = drive_verifier()
                    if not isinstance(quota, dict) or quota.get("success") is not True:
                        failure = quota if isinstance(quota, dict) else {}
                        code = failure.get("error_code") or "DRIVE_VERIFICATION_FAILED"
                        message = failure.get("error_message") or failure.get("error") or "Drive verification did not confirm success"
                        session.update(drive_verified=False, drive_error=message, drive_error_code=code)
                        self.save_session(session)
                        self.handle_connection_failure(STAGE_VERIFYING_DRIVE, code, message)
                        return session
                    verification_succeeded = True
                except Exception as d_err:
                    err_str = str(d_err)
                    is_disabled = "403" in err_str or "disabled" in err_str.lower()
                    with self._lock:
                        self.drive_status = DRIVE_STATUS_CONNECTION_FAILED
                        self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                        self.error_details = {
                            "stage": STAGE_VERIFYING_DRIVE,
                            "code": "DRIVE_API_DISABLED" if is_disabled else "DRIVE_VERIFICATION_FAILED",
                            "message": "Google Drive API is disabled in Google Cloud Console." if is_disabled else f"Google Drive verification failed: {err_str}",
                            "action": ACTION_RETRY,
                        }
                        self.pending_auth = False
                    session["drive_verified"] = False
                    session["drive_error"] = err_str
                    self.save_session(session)
                    logger.error("Drive verification failed: %s", d_err)
                    return session
            else:
                from core.google_drive import GoogleDriveClient
                try:
                    drive_client = GoogleDriveClient(lambda: access_token)
                    v_res = drive_client.verify_drive_connection()
                    if not v_res.get("success"):
                        err_code = v_res.get("error_code", "DRIVE_VERIFICATION_FAILED")
                        err_msg = v_res.get("error_message", "Google Drive API verification failed.")
                        with self._lock:
                            self.drive_status = DRIVE_STATUS_CONNECTION_FAILED
                            self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                            self.error_details = {
                                "stage": STAGE_VERIFYING_DRIVE,
                                "code": err_code,
                                "message": err_msg,
                                "action": ACTION_RETRY,
                            }
                            self.pending_auth = False
                        session["drive_verified"] = False
                        session["drive_error"] = err_msg
                        session["drive_error_code"] = err_code
                        self.save_session(session)
                        logger.error("Drive verification failed (%s): %s", err_code, err_msg)
                        return session
                    verification_succeeded = True
                except Exception as d_err:
                    err_str = str(d_err)
                    is_disabled = "403" in err_str or "disabled" in err_str.lower()
                    with self._lock:
                        self.drive_status = DRIVE_STATUS_CONNECTION_FAILED
                        self.sync_status = SYNC_STATUS_NOT_INITIALIZED
                        self.error_details = {
                            "stage": STAGE_VERIFYING_DRIVE,
                            "code": "DRIVE_API_DISABLED" if is_disabled else "DRIVE_VERIFICATION_FAILED",
                            "message": "Google Drive API is disabled in Google Cloud Console." if is_disabled else f"Google Drive verification failed: {err_str}",
                            "action": ACTION_RETRY,
                        }
                        self.pending_auth = False
                    session["drive_verified"] = False
                    session["drive_error"] = err_str
                    self.save_session(session)
                    logger.error("Drive verification exception: %s", d_err)
                    return session

            if verification_succeeded:
                session["drive_verified"] = True
                session["drive_verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                session["drive_error"] = None
                session["drive_error_code"] = None
                self.save_session(session)

            # Stage 6: Google Drive connected
            with self._lock:
                self.drive_status = DRIVE_STATUS_CONNECTED
                self.current_stage = STAGE_DRIVE_CONNECTED
            logger.info("Stage 6 reached: Google Drive connected and verified")
            time.sleep(0.3)

            # Stage 7: Ready to sync
            with self._lock:
                self.current_stage = STAGE_READY_TO_SYNC
                self.sync_status = SYNC_STATUS_INITIALIZING
            logger.info("Stage 7 reached: Initializing sync engine")
            time.sleep(0.3)

            if sync_initializer:
                try:
                    initialized = sync_initializer()
                    if isinstance(initialized, dict) and initialized.get("success") is False:
                        raise RuntimeError(initialized.get("error") or "Synchronization initialization was not confirmed")
                except Exception as s_err:
                    with self._lock:
                        self.sync_status = SYNC_STATUS_SYNC_FAILED
                        self.error_details = {
                            "stage": STAGE_READY_TO_SYNC,
                            "code": "SYNC_INIT_FAILED",
                            "message": f"Synchronization engine initialization failed: {s_err}",
                            "action": ACTION_RETRY,
                        }
                        self.pending_auth = False
                    logger.error("Sync initialization failed: %s", s_err)
                    return session

            with self._lock:
                self.sync_status = SYNC_STATUS_READY
                self.current_stage = STAGE_READY_TO_SYNC
                self.pending_auth = False
                self.error_details = None
            logger.info("Authentication and Drive connection fully complete. Ready to sync.")
            return session

        except Exception as e:
            with self._lock:
                self.pending_auth = False
                if self.account_status == ACCOUNT_STATUS_SIGNING_IN:
                    self.account_status = ACCOUNT_STATUS_AUTH_FAILED
                if not self.error_details:
                    self.error_details = {
                        "stage": self.current_stage or STAGE_SIGNING_IN,
                        "code": "UNEXPECTED_ERROR",
                        "message": str(e),
                        "action": ACTION_RETRY,
                    }
            raise
        finally:
            if "server" in locals():
                server.server_close()
            with self._lock:
                self._active_loopback_server = None

    def grant_drive_access(
        self,
        port: int = 0,
        timeout_seconds: int = 120,
        open_browser: bool = True,
        drive_verifier: Optional[Any] = None,
        sync_initializer: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Requests incremental Google Drive (drive.file) authorization for the currently connected account."""
        hint = str((self.get_user_profile() or {}).get("email") or "").strip()
        return self.start_pkce_flow(
            port=port,
            timeout_seconds=timeout_seconds,
            open_browser=open_browser,
            drive_verifier=drive_verifier,
            sync_initializer=sync_initializer,
            include_drive_scope=True,
            login_hint=hint,
            force_account_select=False,
        )

    def sign_out(self) -> None:
        """Signs out and removes persisted encrypted credentials."""
        self.clear_session()
        with self._lock:
            self.current_stage = STAGE_IDLE
            self.pending_auth = False
            self.error_details = None
            self.account_status = ACCOUNT_STATUS_NOT_CONNECTED
            self.drive_status = DRIVE_STATUS_NOT_AUTHORIZED
            self.sync_status = SYNC_STATUS_NOT_INITIALIZED
        logger.info("Google session signed out and cleared.")
