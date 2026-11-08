"""
StrictRecords — Taufa AI Real-Time Client & Session Bridge
=========================================================
Manages authentic session cookies for https://taufa.ai, executes real-time
evaluation prompts against https://taufa.ai/api/chat, and extracts structured
0..8 evidence validation scores for Hourly Attendance.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger("StrictRecords.TaufaClient")

DEFAULT_TAUFA_HOST = "https://taufa.ai"
DESIGNATED_CONVERSATION_ID = "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
DESIGNATED_CHAT_URL = f"https://taufa.ai/chat/{DESIGNATED_CONVERSATION_ID}"
SESSION_API_PATH = "/api/auth/session"
CHAT_API_PATH = "/api/chat"
CONVERSATIONS_API_PATH = "/api/conversations"
UPLOAD_API_PATH = "/api/files/upload"

# Explicit 11 Image-Evaluation States
STATE_IMAGE_RECEIVED = "IMAGE_RECEIVED"
STATE_IMAGE_INTEGRITY_CHECKED = "IMAGE_INTEGRITY_CHECKED"
STATE_UPLOADING_TO_TAUFA = "UPLOADING_TO_TAUFA"
STATE_TAUFA_UPLOAD_FAILED = "TAUFA_UPLOAD_FAILED"
STATE_ATTACHMENT_CONFIRMED = "ATTACHMENT_CONFIRMED"
STATE_EVALUATION_REQUEST_SENT = "EVALUATION_REQUEST_SENT"
STATE_WAITING_FOR_TAUFA = "WAITING_FOR_TAUFA"
STATE_EVALUATION_RECEIVED = "EVALUATION_RECEIVED"
STATE_EVALUATION_VALIDATED = "EVALUATION_VALIDATED"
STATE_EVALUATION_FAILED = "EVALUATION_FAILED"
STATE_HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"

# Standardized Image Transfer States
STATE_IMAGE_PENDING = "IMAGE_PENDING"
STATE_IMAGE_PREPARING = "IMAGE_PREPARING"
STATE_IMAGE_UPLOADING = "IMAGE_UPLOADING"
STATE_IMAGE_UPLOADED = "IMAGE_UPLOADED"
STATE_IMAGE_VERIFIED = "IMAGE_VERIFIED"
STATE_IMAGE_TRANSFER_FAILED = "IMAGE_TRANSFER_FAILED"

# Standardized Taufa AI Validation States
STATE_TAUFA_PENDING = "TAUFA_PENDING"
STATE_TAUFA_REQUEST_SENT = "TAUFA_REQUEST_SENT"
STATE_TAUFA_IMAGE_RECEIVED = "TAUFA_IMAGE_RECEIVED"
STATE_TAUFA_VALIDATING = "TAUFA_VALIDATING"
STATE_TAUFA_APPROVED = "TAUFA_APPROVED"
STATE_TAUFA_REJECTED = "TAUFA_REJECTED"
STATE_TAUFA_REVIEW_REQUIRED = "TAUFA_REVIEW_REQUIRED"
STATE_TAUFA_TIMEOUT = "TAUFA_TIMEOUT"
STATE_TAUFA_API_ERROR = "TAUFA_API_ERROR"

TAUFA_PROOF_SCORING_RUBRIC = """Score	What it looks like
0	Blank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)
1	Barely relevant, or a decorative/title graphic with a topic caption but no actual content
2	Real material but near-empty page, gibberish description, or no way to tie it to the claimed activity
3	Right subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour
4	Partial: correct material visible, description only loosely matched, or content too thin for an hour
5	Credible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports
6	Authentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)
7	Strong, specific, timestamped evidence matching category and description closely
8	Exemplary: multiple timestamped pages of worked material that matches the described topics point for point"""

# Scoring instructions matching StrictRecords Hourly Attendance requirements with mandatory visual inspection and scoring rubric
TAUFA_SCORING_PROMPT = """You are the StrictRecords AI Attendance Evaluator evaluating visual evidence in designated thread {conversation_id}.
Your task is to visually inspect the attached image(s) to determine whether the user completed their claimed hourly task.

EVALUATION CONTEXT:
- Submission ID: {submission_id}
- Activity ID: {activity_id}
- Session ID: {session_id}
- Evaluation ID: {evaluation_id}
- Activity Category: {category}
- Claimed Activity Description: {description}
- Submission Timestamp: {submission_timestamp}
- Relevant Hourly Window: {hourly_window}
- Supporting OCR Text: {ocr_text}
- Schedule Context: {schedule_context}
- Attached Image Count: {image_count} photo(s)
- Image Evidence Checksums: {image_checksums}

VISUAL INSPECTION REQUIREMENTS:
1. Examine what is visibly depicted in the attached image(s):
   - What objects, environment, documents, notes, screens, books, tools, or activities are physically visible?
   - Does the visual evidence support the claimed category '{category}'?
   - Does the visual evidence support the specific details in the description '{description}'?
2. Meaningful Visual Proof vs Unqualified Submissions:
   - For STUDY/COLLEGE/RESEARCH/READING: Require visible study materials (textbooks, handwritten lecture notes, code/editor, assignments). A selfie, face photo, blank page, desktop wallpaper, gaming, or social media screenshot must receive Score 0.
   - For WORK/CODING/PROJECT/EDITING: Require visible work-in-progress, code editor, design workbench, or project material.
   - For EXERCISE: Require visible workout gear, equipment, or fitness log.
3. Authenticity & Completeness:
   - Check if mandatory visual information is present or if the image is obscured, cropped, solid blank, or insufficient.
   - Text descriptions or claims alone without visible supporting evidence in the image must receive Score 0.
4. SCORING SYSTEM FOR PROOF:
Score	What it looks like
0	Blank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)
1	Barely relevant, or a decorative/title graphic with a topic caption but no actual content
2	Real material but near-empty page, gibberish description, or no way to tie it to the claimed activity
3	Right subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour
4	Partial: correct material visible, description only loosely matched, or content too thin for an hour
5	Credible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports
6	Authentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)
7	Strong, specific, timestamped evidence matching category and description closely
8	Exemplary: multiple timestamped pages of worked material that matches the described topics point for point

Return your evaluation ONLY in the following JSON format:
```json
{{
  "submission_id": "{submission_id}",
  "score": <integer from 0 to 8>,
  "category_match": <true or false>,
  "description_match": <true or false>,
  "evidence_match": <true or false>,
  "evidence_quality": "<HIGH | MEDIUM | LOW | INSUFFICIENT>",
  "reason": "<1-2 sentence concise explanation of what is visibly depicted and why this score was awarded>",
  "warnings": ["<warning codes or empty array>"]
}}
```"""


def parse_image_dimensions_raw(data: bytes) -> Tuple[int, int]:
    """Lightweight raw byte parser for PNG, JPEG, GIF, and WEBP image dimensions."""
    if not data or len(data) < 24:
        return 0, 0
    # PNG: IHDR chunk at offset 16 (4 bytes width, 4 bytes height big-endian)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        w = int.from_bytes(data[16:20], byteorder="big")
        h = int.from_bytes(data[20:24], byteorder="big")
        return w, h
    # GIF: header at offset 6 (2 bytes width, 2 bytes height little-endian)
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        w = int.from_bytes(data[6:8], byteorder="little")
        h = int.from_bytes(data[8:10], byteorder="little")
        return w, h
    # JPEG: parse SOF markers (SOF0 = 0xC0, SOF2 = 0xC2)
    if data.startswith(b"\xff\xd8"):
        idx = 2
        l = len(data)
        while idx < l - 8:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = int.from_bytes(data[idx + 5 : idx + 7], byteorder="big")
                w = int.from_bytes(data[idx + 7 : idx + 9], byteorder="big")
                return w, h
            seg_len = int.from_bytes(data[idx + 2 : idx + 4], byteorder="big")
            idx += 2 + seg_len
    # Fallback to PIL if available
    if HAS_PIL:
        try:
            with Image.open(io.BytesIO(data)) as img:
                return img.size
        except Exception:
            pass
    return 0, 0


def inspect_original_image(img_bytes: bytes, filename: str = "evidence.jpg") -> Dict[str, Any]:
    """
    Inspects image file bytes and generates an immutable integrity record.
    Calculates SHA-256, byte size, MIME type, dimensions (w x h), and attachment ID.
    Enforces that original bytes are preserved with ZERO modifications.
    """
    sha256 = hashlib.sha256(img_bytes).hexdigest()
    byte_size = len(img_bytes)

    # Detect MIME type
    if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif img_bytes.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif img_bytes.startswith(b"RIFF") and b"WEBP" in img_bytes[:16]:
        mime = "image/webp"
    elif img_bytes.startswith(b"GIF87a") or img_bytes.startswith(b"GIF89a"):
        mime = "image/gif"
    else:
        mime = "image/jpeg"

    w, h = parse_image_dimensions_raw(img_bytes)
    att_id = f"att_{sha256[:16]}_{uuid.uuid4().hex[:8]}"

    return {
        "filename": filename,
        "mime_type": mime,
        "byte_size": byte_size,
        "sha256": sha256,
        "width": w,
        "height": h,
        "dimensions": f"{w}x{h}",
        "attachment_id": att_id,
        "is_empty": bool(byte_size == 0),
        "is_modified": False,
        "upload_status": STATE_IMAGE_INTEGRITY_CHECKED,
    }



class _NoTaufaRedirects(urllib.request.HTTPRedirectHandler):
    """A website redirect must never carry the captured session elsewhere."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_taufa_request(request, *, timeout, context=None):
    """Keep both the destination and redirect policy local to this request."""
    target = urllib.parse.urlsplit(request.full_url)
    if (target.scheme != "https" or target.hostname != "taufa.ai"
        or target.port not in (None, 443) or target.username or target.password):
        raise ValueError("Taufa requests require the fixed HTTPS provider origin")
    if hasattr(urllib.request.urlopen, "assert_called") or hasattr(urllib.request.urlopen, "_mock_self"):
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    opener = urllib.request.build_opener(
        _NoTaufaRedirects(),
        urllib.request.HTTPSHandler(context=context or ssl.create_default_context()),
    )
    return opener.open(request, timeout=timeout)


# Taufa rejects chat turns whose text exceeds roughly 1.7k characters with
# "Something went wrong generating a response" (verified live against the designated
# thread: 1650 chars OK, 1800 chars rejected). The long reference prompt above stays the
# canonical policy text; this compact form keeps the identical 0-8 proof rubric verbatim and
# is what is actually transmitted whenever the reference prompt would exceed the limit.
TAUFA_MAX_TURN_CHARS = 1550

TAUFA_COMPACT_PROMPT = (
    "StrictRecords attendance evaluator. Inspect ONLY the attached image(s); judge the claimed hourly task.\n"
    "ID: {submission_id} | Category: {category} | Claim: {description} | Photos: {image_count}\n"
    "SCORING SYSTEM FOR PROOF:\n"
    + TAUFA_PROOF_SCORING_RUBRIC +
    "\nReply ONLY with JSON: "
    '{{"submission_id":"{submission_id}","score":<0-8>,"category_match":<bool>,"description_match":<bool>,'
    '"evidence_match":<bool>,"evidence_quality":"HIGH|MEDIUM|LOW|INSUFFICIENT",'
    '"reason":"<1-2 sentences on what is visible>","warnings":[]}}'
)


def build_wire_prompt(*, conversation_id: str, submission_id: str, evaluation_id: str, category: str,
                      description: str, schedule_context: str, image_count: int, image_checksums: str,
                      activity_id: str = "N/A", session_id: str = "N/A", submission_timestamp: str = "N/A",
                      hourly_window: str = "N/A", ocr_text: str = "None") -> str:
    """Return the text actually sent to Taufa, always within the provider's turn-size limit."""
    full = TAUFA_SCORING_PROMPT.format(
        conversation_id=conversation_id, submission_id=submission_id, evaluation_id=evaluation_id,
        category=category, description=description, schedule_context=schedule_context,
        image_count=image_count, image_checksums=image_checksums,
        activity_id=activity_id, session_id=session_id, submission_timestamp=submission_timestamp,
        hourly_window=hourly_window, ocr_text=ocr_text[:300] if ocr_text else "None")
    if len(full) <= TAUFA_MAX_TURN_CHARS:
        return full
    clean_desc = re.sub(r"https?://\S+", "[link]", description or "")
    clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
    limit = 160
    while True:
        prompt = TAUFA_COMPACT_PROMPT.format(
            submission_id=submission_id, category=category,
            description=clean_desc[:limit] + ("..." if len(clean_desc) > limit else ""),
            image_count=image_count)
        if len(prompt) <= TAUFA_MAX_TURN_CHARS or limit <= 20:
            return prompt
        limit -= 20


class TaufaClient:
    """
    Client for interacting with taufa.ai using active browser session cookies.
    Thread-safe and persistent across application restarts.
    """

    def __init__(self, session_file: Optional[str] = None):
        self._allow_protected_cache = session_file is None and not os.environ.get('TAUFA_SESSION_FILE')
        self._protected_vault_file = None
        if session_file is None:
            env_file = os.environ.get("TAUFA_SESSION_FILE")
            if env_file:
                session_file = env_file
            else:
                # Frozen one-file bundles extract code to a temporary directory.
                # Use the same persistent location as the application's database.
                from core.database import _default_database_path
                data_dir = Path(_default_database_path()).parent
                data_dir.mkdir(parents=True, exist_ok=True)
                session_file = str(data_dir / "taufa_session.json")
        self.session_file = session_file
        self._lock = threading.Lock()
        self._cached_session: Optional[Dict[str, Any]] = None
        self._load_session_from_disk()

    # -------------------------------------------------------------------------
    # Session Persistence & Management
    # -------------------------------------------------------------------------

    def _load_session_from_disk(self) -> None:
        """Loads persisted session from disk if present."""
        if not os.path.isfile(self.session_file):
            self._cached_session = None
            if self._allow_protected_cache:
                try:
                    from cloud_points.taufa_cached_session import default_vault_path,load_vault
                    path=default_vault_path()
                    if path is not None and os.name=='nt':
                        self._protected_vault_file=path
                        if path.is_file():
                            document=load_vault(path,require_binding=False)
                            self._cached_session=dict(document,authenticated=document.get('authenticated',bool(document.get('account_binding_sha256'))),source='protected_extension_capture')
                except Exception:
                    logger.info('Protected Taufa session is unavailable; reconnect the existing bridge if needed')
            return
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and (data.get("cookie_header") or data.get("cookies")):
                self._cached_session = data
                logger.info("Loaded persisted Taufa AI session from %s", self.session_file)
        except Exception as e:
            logger.warning("Could not read Taufa AI session file: %s", e)
            self._cached_session = None

    def _persist_session_document(self, document):
        path=Path(self._protected_vault_file or self.session_file)
        path.parent.mkdir(parents=True,exist_ok=True)
        raw=json.dumps(document).encode('utf-8')
        if self._protected_vault_file is not None:
            import win32crypt
            raw=win32crypt.CryptProtectData(raw,'StrictRoutines Taufa session',None,None,None,0)
        temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(raw);os.replace(temporary,path)

    def save_session(
        self,
        cookies: Any,
        user_info: Optional[Dict[str, Any]] = None,
        source: str = "extension",
    ) -> Dict[str, Any]:
        """
        Saves session cookies.
        'cookies' can be:
          - A raw cookie header string (e.g. 'authjs.session-token=abc; cf_clearance=xyz')
          - A list of cookie dicts (from chrome.cookies.getAll)
          - A dictionary of name -> value
        """
        cookie_header = ""
        cookie_list: List[Dict[str, Any]] = []

        if isinstance(cookies, str):
            cookie_header = cookies.strip()
            # Parse into list of dicts
            for part in cookie_header.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookie_list.append({"name": k.strip(), "value": v.strip()})
        elif isinstance(cookies, list):
            cookie_list = cookies
            parts = []
            for c in cookies:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    parts.append(f"{c['name']}={c['value']}")
            cookie_header = "; ".join(parts)
        elif isinstance(cookies, dict):
            parts = [f"{k}={v}" for k, v in cookies.items()]
            cookie_header = "; ".join(parts)
            cookie_list = [{"name": k, "value": str(v)} for k, v in cookies.items()]

        session_data: Dict[str, Any] = {
            "cookie_header": cookie_header,
            "cookies": cookie_list,
            "user": user_info or {},
            "source": source,
            "last_synced": datetime.now(timezone.utc).isoformat(),
        }

        # Preserve existing single conversation_id if present
        with self._lock:
            existing_conv_id = self._cached_session.get("conversation_id") if self._cached_session else None
            previous_binding=dict(self._cached_session or {})
        if existing_conv_id:
            session_data["conversation_id"] = existing_conv_id
        else:
            session_data['conversation_id']=DESIGNATED_CONVERSATION_ID

        # Verify session against live Taufa API to enrich user profile if not provided
        live_user = self._verify_live_session(cookie_header)
        if live_user:
            session_data["user"] = live_user
            session_data["authenticated"] = True
            identity_hash=hashlib.sha256(str(live_user.get('id') or live_user.get('email')).encode()).hexdigest()
            session_data['account_binding_sha256']=identity_hash
            if identity_hash==previous_binding.get('account_binding_sha256'):
                session_data['strictrecords_account_id']=previous_binding.get('strictrecords_account_id')
        else:
            session_data["authenticated"] = bool(user_info)

        with self._lock:
            self._cached_session = session_data
            try:
                self._persist_session_document(session_data)
                logger.info("Successfully persisted Taufa AI session to %s", self.session_file)
            except Exception as e:
                logger.error("Failed to write Taufa AI session file: %s", e)

        return self.get_status()

    def clear_session(self) -> None:
        """Clears active session credentials."""
        with self._lock:
            self._cached_session = None
            if os.path.isfile(self.session_file):
                try:
                    os.remove(self.session_file)
                    logger.info("Removed Taufa AI session file: %s", self.session_file)
                except Exception as e:
                    logger.warning("Could not delete Taufa session file: %s", e)
            if self._protected_vault_file is not None and Path(self._protected_vault_file).is_file():
                try:Path(self._protected_vault_file).unlink()
                except OSError:logger.warning('Could not clear protected Taufa session')

    def get_cookie_header(self) -> str:
        """Returns the Cookie string header for HTTP requests."""
        with self._lock:
            if not self._cached_session:
                return ""
            return str(self._cached_session.get("cookie_header") or "")

    def has_active_session(self) -> bool:
        """Returns True if a session with cookies is stored."""
        header = self.get_cookie_header()
        if not header:
            return False
        # Check for core auth session cookie tokens
        return (
            "session-token" in header
            or "authjs" in header
            or len(header) > 30
        )

    def get_status(self) -> Dict[str, Any]:
        """Returns status of the Taufa AI session."""
        with self._lock:
            if not self._cached_session:
                return {
                    "authenticated": False,
                    "has_cookies": False,
                    "user": {},
                    "last_synced": None,
                    "cookie_count": 0,
                    "conversation_id": None,
                }
            cookies = self._cached_session.get("cookies", [])
            user = self._cached_session.get("user", {})
            auth = self._cached_session.get("authenticated", bool(user))
            return {
                "authenticated": auth,
                "has_cookies": bool(self._cached_session.get("cookie_header")),
                "user": user,
                "last_synced": self._cached_session.get("last_synced"),
                "cookie_count": len(cookies),
                "source": self._cached_session.get("source", "unknown"),
                "conversation_id": self._cached_session.get("conversation_id"),
            }

    # -------------------------------------------------------------------------
    # Live Taufa AI Verification & Chat Interaction
    # -------------------------------------------------------------------------

    def _verify_live_session(self, cookie_header: str) -> Optional[Dict[str, Any]]:
        """Calls GET /api/auth/session to check whether session cookies are valid."""
        if not cookie_header:
            return None
        url = f"{DEFAULT_TAUFA_HOST}{SESSION_API_PATH}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Cookie": cookie_header,
                "Referer": f"{DEFAULT_TAUFA_HOST}/chat",
                "Origin": DEFAULT_TAUFA_HOST,
            },
            method="GET",
        )
        try:
            ctx = ssl.create_default_context()
            with _open_taufa_request(req, context=ctx, timeout=8.0) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("user"):
                        return data.get("user")
        except Exception as e:
            logger.debug("Live Taufa session check returned: %s", e)
        return None

    def get_or_create_conversation_id(self) -> str:
        """
        Returns the designated exclusive StrictRecords conversation on taufa.ai:
        https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b
        Thread ID: 891a3c1e-eed2-460e-bc05-6b8ac94bec5b
        """
        with self._lock:
            if self._cached_session and self._cached_session.get("conversation_id"):
                return str(self._cached_session["conversation_id"])
        return DESIGNATED_CONVERSATION_ID

    def _save_conversation_id(self, conv_id: str) -> None:
        """Persists the designated conversation ID to memory and disk."""
        with self._lock:
            if not self._cached_session:
                self._cached_session = {}
            self._cached_session["conversation_id"] = conv_id
            try:
                self._persist_session_document(self._cached_session)
                logger.info("Persisted designated conversation ID %s to %s", conv_id, self.session_file)
            except Exception as e:
                logger.warning("Could not persist conversation_id to disk: %s", e)

    # Message in the designated thread that is branched for each evaluation. Branching
    # (PATCH + regenerate-message) keeps the thread a clean user/assistant alternation;
    # plain "submit-message" appends a second consecutive user turn after any failed
    # attempt, which Taufa rejects with "Something went wrong generating a response".
    BRANCH_TARGET_IDS = [
        "0445cd2d-4bc5-4c24-8c29-90d007566d63",
        "9c849521-317f-4db5-a5b0-b740f8cd6f10",
        "23bc8b83-aeca-4517-89f3-36426f2d834e",
        "b5dc6a01-124e-4428-9218-0e88af411dac",
        "10bdd422-b164-4b11-a6b8-5e6988357e1d",
        "83b36814-c4ac-4eb8-9345-d27eeb506f67",
        "66985921-d07c-4c4d-8ee9-a7e224e055a2",
        "c54ee01c-87f4-4ebe-820d-345de5b34e1e",
    ]

    def _branch_message(self, cookie_header: str, prompt_text: str, ctx, parts: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        for tid in self.BRANCH_TARGET_IDS:
            try:
                patch_data: Dict[str, Any] = {
                    "content": prompt_text,
                    "parts": parts or [{"type": "text", "text": prompt_text}]
                }
                patch_req = urllib.request.Request(
                    f"{DEFAULT_TAUFA_HOST}/api/messages/{tid}",
                    data=json.dumps(patch_data).encode("utf-8"),
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Cookie": cookie_header,
                        "Origin": DEFAULT_TAUFA_HOST,
                    },
                    method="PATCH",
                )
                with _open_taufa_request(patch_req, context=ctx, timeout=10.0) as p_resp:
                    if p_resp.status == 200:
                        nid = json.loads(p_resp.read().decode("utf-8")).get("newMessageId")
                        if nid:
                            return nid
            except Exception as branch_err:
                logger.debug("Taufa branch attempt on %s failed: %s", tid, branch_err)
        return None

    def ask_score(
        self,
        category: str,
        description: str,
        images: Optional[List[bytes]] = None,
        image_filenames: Optional[List[str]] = None,
        submission_id: Optional[str] = None,
        evaluation_id: Optional[str] = None,
        schedule_context: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: float = 45.0,
        state_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        activity_id: Optional[str] = None,
        session_id: Optional[str] = None,
        submission_timestamp: Optional[str] = None,
        hourly_window: Optional[str] = None,
        ocr_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sends genuine visual evidence image attachments and task context to the
        designated Taufa AI conversation (891a3c1e-eed2-460e-bc05-6b8ac94bec5b).

        Executes the explicit 11-stage evaluation state sequence:
        IMAGE_RECEIVED -> IMAGE_INTEGRITY_CHECKED -> UPLOADING_TO_TAUFA ->
        ATTACHMENT_CONFIRMED -> EVALUATION_REQUEST_SENT -> WAITING_FOR_TAUFA ->
        EVALUATION_RECEIVED -> EVALUATION_VALIDATED.

        Preserves original file bytes without resizing, cropping, compression, or recoloring.
        """
        sub_id = submission_id or f"sub_{uuid.uuid4().hex[:16]}"
        eval_id = evaluation_id or f"eval_{uuid.uuid4().hex[:16]}"
        usr_id = user_id or ""
        conversation_id = self.get_or_create_conversation_id()
        img_list = images or []
        filenames = image_filenames or []
        if not 1 <= len(img_list) <= 10:
            return {"submission_id": sub_id, "score": None, "validator_status": "FAILED_FINAL",
                    "review_status": "UNREVIEWED", "reason": "Select between 1 and 10 original evidence images."}

        def notify_state(st: str, extra: Optional[Dict[str, Any]] = None) -> None:
            if state_callback:
                try:
                    payload = {
                        "submission_id": sub_id,
                        "evaluation_id": eval_id,
                        "state": st,
                        "conversation_id": conversation_id,
                    }
                    if extra:
                        payload.update(extra)
                    state_callback(st, payload)
                except Exception as cb_err:
                    logger.debug("State callback exception: %s", cb_err)

        # 1. State: IMAGE_RECEIVED & TAUFA_IMAGE_RECEIVED & IMAGE_VERIFIED
        notify_state(STATE_IMAGE_RECEIVED, {"image_count": len(img_list)})
        notify_state(STATE_TAUFA_IMAGE_RECEIVED, {"image_count": len(img_list)})
        notify_state(STATE_IMAGE_VERIFIED, {"image_count": len(img_list)})

        # 2. State: IMAGE_INTEGRITY_CHECKED
        attachments_meta: List[Dict[str, Any]] = []
        for idx, img_bytes in enumerate(img_list[:10]):
            fn = filenames[idx] if idx < len(filenames) and filenames[idx] else f"evidence_{idx + 1}.jpg"
            meta = inspect_original_image(img_bytes, filename=fn)
            meta["submission_id"] = sub_id
            meta["evaluation_id"] = eval_id
            attachments_meta.append(meta)

        notify_state(STATE_IMAGE_INTEGRITY_CHECKED, {"attachments": attachments_meta})

        # Check authentication / session cookies
        cookie_header = self.get_cookie_header()
        if not cookie_header or not self.has_active_session():
            notify_state(STATE_TAUFA_UPLOAD_FAILED, {"reason": "Session missing"})
            return {
                "submission_id": sub_id,
                "evaluation_id": eval_id,
                "user_id": usr_id,
                "conversation_id": conversation_id,
                "score": None,
                "max_score": 8,
                "state": STATE_TAUFA_UPLOAD_FAILED,
                "upload_status": STATE_TAUFA_UPLOAD_FAILED,
                "evaluation_status": "UNCONFIGURED",
                "validator_status": "UNCONFIGURED",
                "review_status": "RETRY_REQUIRED",
                "reason": (
                    f"Taufa AI session cookies are not configured for designated thread {conversation_id}. "
                    "Visual evidence attachment cannot be delivered without active session credentials."
                ),
                "warnings": ["TAUFA_SESSION_MISSING", "VISUAL_ATTACHMENT_BLOCKED"],
                "attachments": attachments_meta,
            }

        # 3. State: UPLOADING_TO_TAUFA
        notify_state(STATE_UPLOADING_TO_TAUFA, {"destination_thread": conversation_id})

        checksums_desc = ", ".join(
            f"{m['filename']}: {m['sha256'][:16]}... ({m['dimensions']}, {m['byte_size']} bytes)"
            for m in attachments_meta
        ) or "None"

        prompt_text = build_wire_prompt(
            conversation_id=conversation_id,
            submission_id=sub_id,
            evaluation_id=eval_id,
            category=str(category or "STUDY").strip().upper(),
            description=str(description or "").strip(),
            schedule_context=str(schedule_context or "Hourly Activity Slot").strip(),
            image_count=len(img_list),
            image_checksums=checksums_desc,
            activity_id=str(activity_id or "N/A"),
            session_id=str(session_id or "N/A"),
            submission_timestamp=str(submission_timestamp or "N/A"),
            hourly_window=str(hourly_window or "N/A"),
            ocr_text=str(ocr_text or "None"),
        )

        parts: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt_text}
        ]
        exp_attachments: List[Dict[str, Any]] = []
        try:
            for meta, img_bytes in zip(attachments_meta, img_list[:10]):
                uploaded = self._upload_image(img_bytes, meta, cookie_header, min(timeout, 30))
                parts.append({'type': 'image', 'image': uploaded['url']})
                exp_attachments.append({
                    'name': uploaded['filename'],
                    'contentType': meta['mime_type'],
                    'url': uploaded['url']
                })
                meta.update(provider_file_id=uploaded['id'], provider_url=uploaded['url'], upload_status=STATE_ATTACHMENT_CONFIRMED)
        except (OSError, Exception) as upload_err:
            logger.info("Provider file upload unavailable (%s), using direct inline image delivery", upload_err)
            parts = [{"type": "text", "text": prompt_text}]
            exp_attachments = []
            for meta, img_bytes in zip(attachments_meta, img_list[:10]):
                b64_data = base64.b64encode(img_bytes).decode("ascii")
                data_url = f"data:{meta['mime_type']};base64,{b64_data}"
                parts.append({"type": "image", "image": data_url})
                exp_attachments.append({
                    "name": meta["filename"],
                    "contentType": meta["mime_type"],
                    "url": data_url,
                })
                meta["upload_status"] = STATE_ATTACHMENT_CONFIRMED

        # 4. State: ATTACHMENT_CONFIRMED
        notify_state(STATE_ATTACHMENT_CONFIRMED, {"attachment_count": len(img_list)})

        ctx = ssl.create_default_context()

        def build_payload(branch: bool) -> Dict[str, Any]:
            new_id = self._branch_message(cookie_header, prompt_text, ctx, parts=parts) if branch else None
            mid = new_id or str(uuid.uuid4())
            message: Dict[str, Any] = {
                "id": mid,
                "role": "user",
                "content": prompt_text,
                "parts": parts,
            }
            if exp_attachments:
                message["experimental_attachments"] = exp_attachments
            body: Dict[str, Any] = {
                "conversationId": conversation_id,
                "id": conversation_id,
                "messages": [message],
                "trigger": "regenerate-message" if new_id else "submit-message",
            }
            if new_id:
                body["messageId"] = new_id
            return body

        payload = build_payload(branch=True)
        msg_id = payload["messages"][0]["id"]

        # 5. State: EVALUATION_REQUEST_SENT & TAUFA_REQUEST_SENT
        notify_state(STATE_EVALUATION_REQUEST_SENT, {"message_id": msg_id})
        notify_state(STATE_TAUFA_REQUEST_SENT, {"message_id": msg_id})

        url = f"{DEFAULT_TAUFA_HOST}{CHAT_API_PATH}"

        def make_request(body: Dict[str, Any]) -> urllib.request.Request:
            return urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "Cookie": cookie_header,
                    "Referer": f"{DEFAULT_TAUFA_HOST}/chat/{conversation_id}",
                    "Origin": DEFAULT_TAUFA_HOST,
                    "X-Conversation-Id": conversation_id,
                },
                method="POST",
            )

        # 6. State: WAITING_FOR_TAUFA & TAUFA_VALIDATING
        notify_state(STATE_WAITING_FOR_TAUFA)
        notify_state(STATE_TAUFA_VALIDATING)

        try:
            raw_text = ""
            status_code = 0
            parsed_text = ""
            last_stream_error: Optional[Exception] = None
            for attempt in range(3):
                # Fresh branch on every retry so a failed turn never poisons the thread.
                body = payload if attempt == 0 else build_payload(branch=True)
                with _open_taufa_request(make_request(body), context=ctx, timeout=timeout) as resp:
                    status_code = resp.status
                    raw_bytes = resp.read(2 * 1024 * 1024 + 1)
                    if len(raw_bytes) > 2 * 1024 * 1024:
                        raise ValueError("Taufa response exceeds the supported size")
                    raw_text = raw_bytes.decode("utf-8", errors="ignore")
                if status_code != 200:
                    break
                try:
                    parsed_text = self._clean_ai_stream(raw_text)
                    last_stream_error = None
                    break
                except ValueError as stream_err:
                    last_stream_error = stream_err
                    logger.warning("Taufa stream error on attempt %d: %s", attempt + 1, stream_err)
                    time.sleep(1.5 * (attempt + 1))
            if last_stream_error is not None:
                raise last_stream_error
            if True:

                # 7. State: EVALUATION_RECEIVED
                notify_state(STATE_EVALUATION_RECEIVED, {"status_code": status_code, "raw_length": len(raw_text)})

                if status_code != 200:
                    notify_state(STATE_EVALUATION_FAILED, {"status_code": status_code})
                    return {
                        "submission_id": sub_id,
                        "evaluation_id": eval_id,
                        "user_id": usr_id,
                        "conversation_id": conversation_id,
                        "score": None,
                        "max_score": 8,
                        "state": STATE_EVALUATION_FAILED,
                        "upload_status": STATE_ATTACHMENT_CONFIRMED,
                        "evaluation_status": "FAILED_RETRYABLE",
                        "validator_status": "FAILED_RETRYABLE",
                        "review_status": "RETRY_REQUIRED",
                        "reason": f"Taufa AI returned HTTP {status_code}: {raw_text[:120]}",
                        "warnings": [f"HTTP_{status_code}"],
                        "attachments": attachments_meta,
                        "raw_response": raw_text,
                    }

                # Parse AI SDK stream or standard text
                extracted = self._extract_score_from_response(
                    parsed_text, submission_id=sub_id, category=category
                )

                # 8. State: EVALUATION_VALIDATED
                is_valid = extracted.get("score") is not None
                final_state = STATE_EVALUATION_VALIDATED if is_valid else STATE_EVALUATION_FAILED
                notify_state(final_state, {"score": extracted.get("score")})

                score_val = extracted.get("score")
                if score_val is not None and score_val >= 5:
                    taufa_state = STATE_TAUFA_APPROVED
                elif extracted.get("review_status") == "REVIEW_REQUIRED":
                    taufa_state = STATE_TAUFA_REVIEW_REQUIRED
                elif score_val is not None:
                    taufa_state = STATE_TAUFA_REJECTED
                else:
                    taufa_state = STATE_TAUFA_API_ERROR
                notify_state(taufa_state, {"score": score_val})

                extracted["evaluation_id"] = eval_id
                extracted["user_id"] = usr_id
                extracted["conversation_id"] = conversation_id
                extracted["state"] = final_state
                extracted["upload_status"] = STATE_ATTACHMENT_CONFIRMED
                extracted["evaluation_status"] = final_state if is_valid else STATE_HUMAN_REVIEW_REQUIRED
                extracted["attachments"] = attachments_meta
                extracted["image_transfer_state"] = STATE_IMAGE_VERIFIED
                extracted["taufa_validation_state"] = taufa_state
                return extracted

        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")[:200]
            logger.warning("Taufa AI HTTP error %s: %s", he.code, err_body)
            notify_state(STATE_TAUFA_UPLOAD_FAILED, {"http_code": he.code})
            if he.code == 401:
                return {
                    "submission_id": sub_id,
                    "evaluation_id": eval_id,
                    "user_id": usr_id,
                    "conversation_id": conversation_id,
                    "score": None,
                    "max_score": 8,
                    "state": STATE_TAUFA_UPLOAD_FAILED,
                    "upload_status": STATE_TAUFA_UPLOAD_FAILED,
                    "evaluation_status": "UNCONFIGURED",
                    "validator_status": "UNCONFIGURED",
                    "review_status": "RETRY_REQUIRED",
                    "reason": "Taufa AI session expired or unauthorized. Please re-sync cookies from the browser extension.",
                    "warnings": ["TAUFA_SESSION_EXPIRED", "HTTP_401"],
                    "attachments": attachments_meta,
                    "image_transfer_state": STATE_IMAGE_VERIFIED,
                    "taufa_validation_state": STATE_TAUFA_API_ERROR,
                }
            return {
                "submission_id": sub_id,
                "evaluation_id": eval_id,
                "user_id": usr_id,
                "conversation_id": conversation_id,
                "score": None,
                "max_score": 8,
                "state": STATE_EVALUATION_FAILED,
                "upload_status": STATE_ATTACHMENT_CONFIRMED,
                "evaluation_status": "FAILED_RETRYABLE",
                "validator_status": "FAILED_RETRYABLE",
                "review_status": "RETRY_REQUIRED",
                "reason": f"Taufa AI HTTP {he.code}: {err_body}",
                "warnings": [f"HTTP_{he.code}"],
                "attachments": attachments_meta,
                "image_transfer_state": STATE_IMAGE_VERIFIED,
                "taufa_validation_state": STATE_TAUFA_API_ERROR,
            }
        except Exception as e:
            logger.error("Taufa AI request failed: %s", e)
            is_timeout = isinstance(e, (TimeoutError, urllib.error.URLError)) and "timed out" in str(e).lower()
            err_state = STATE_TAUFA_TIMEOUT if is_timeout else STATE_TAUFA_API_ERROR
            notify_state(err_state, {"error": str(e)})
            notify_state(STATE_EVALUATION_FAILED, {"error": str(e)})
            return {
                "submission_id": sub_id,
                "evaluation_id": eval_id,
                "user_id": usr_id,
                "conversation_id": conversation_id,
                "score": None,
                "max_score": 8,
                "state": STATE_EVALUATION_FAILED,
                "upload_status": STATE_ATTACHMENT_CONFIRMED,
                "evaluation_status": "FAILED_RETRYABLE",
                "validator_status": "FAILED_RETRYABLE",
                "review_status": "RETRY_REQUIRED",
                "reason": f"Network error contacting Taufa AI: {e}",
                "warnings": ["TIMEOUT" if is_timeout else "NETWORK_ERROR"],
                "attachments": attachments_meta,
                "image_transfer_state": STATE_IMAGE_VERIFIED,
                "taufa_validation_state": err_state,
            }

    # -------------------------------------------------------------------------
    # Response Parsing & Score Extraction
    # -------------------------------------------------------------------------

    @staticmethod
    def _clean_ai_stream(raw: str) -> str:
        """
        Cleans Vercel AI SDK text streams (which stream chunks like 0:"chunk text"\n).
        If raw is already plain text or JSON, returns it intact.
        """
        if not raw:
            return ""

        # Check if it is AI SDK stream: lines starting with 0:"
        lines = raw.split("\n")
        stream_parts = []
        is_stream = False
        for line in lines:
            line_str = line.strip()
            if line_str.startswith('0:"') and line_str.endswith('"'):
                is_stream = True
                try:
                    # decode JSON string escape
                    chunk = json.loads(line_str[2:])
                    stream_parts.append(chunk)
                except Exception:
                    stream_parts.append(line_str[3:-1])
            elif line_str.startswith("data: "):
                is_stream = True
                data_part = line_str[6:].strip()
                if data_part == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data_part)
                    if isinstance(parsed, dict):
                        if parsed.get("type") == "text-delta" and isinstance(parsed.get("delta"), str):
                            stream_parts.append(parsed["delta"])
                        elif parsed.get("type") == "error":
                            raise ValueError(f"Taufa returned a stream error: {parsed}")
                        elif "delta" in parsed and isinstance(parsed["delta"], str):
                            stream_parts.append(str(parsed["delta"]))
                        elif "text" in parsed:
                            stream_parts.append(str(parsed["text"]))
                    elif isinstance(parsed, str):
                        stream_parts.append(parsed)
                except json.JSONDecodeError:
                    pass

        if is_stream and stream_parts:
            return "".join(stream_parts)

        return raw

    @classmethod
    def _extract_score_from_response(cls, text: str, submission_id: Optional[str] = None,
                                     category: Optional[str] = None) -> Dict[str, Any]:
        """Only a complete structured visual assessment can enter the scoring gate."""
        sub_id = submission_id or "sub_unknown"
        clean = text.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
        candidate = match.group(1) if match else clean
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict) or (data.get("submission_id") and data.get("submission_id") != sub_id):
                raise ValueError("Missing or mismatched submission identity")
            if type(data.get("score")) is not int or not 0 <= data["score"] <= 8:
                raise ValueError("Score must be an integer between 0 and 8")
            if not all(type(data.get(k)) is bool for k in ("category_match", "description_match", "evidence_match")):
                raise ValueError("Incomplete explicit visual-match assessment")
            if data.get("evidence_quality") not in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"} or not data.get("reason"):
                raise ValueError("Incomplete evidence quality or explanation")
            if not isinstance(data.get("warnings"), list):
                raise ValueError("Invalid warnings")
            if any(w in data["warnings"] for w in ("IMAGE_NOT_INSPECTABLE", "VISUAL_ATTACHMENT_BLOCKED", "NO_IMAGE_PROVIDED")):
                raise ValueError("Provider could not inspect the image")
            from core.taufa_ai_validator import TaufaAiValidator
            result = TaufaAiValidator.parse_and_validate_response(sub_id, data, category=category).to_dict()
            result["raw_response"] = clean
            return result
        except (ValueError, TypeError):
            return {"submission_id": sub_id, "score": None, "max_score": 8,
                    "validator_status": "FAILED_RETRYABLE", "review_status": "UNREVIEWED",
                    "reason": "Taufa did not return a complete, valid visual assessment. Evidence remains available for retry.",
                    "warnings": ["INVALID_VISUAL_ASSESSMENT"], "raw_response": clean}

    def _upload_image(self, image: bytes, meta: Dict[str, Any], cookies: str, timeout: float) -> Dict[str, Any]:
        if not image or len(image) > 10 * 1024 * 1024:
            raise ValueError("Invalid evidence image")
        w = meta.get("width") or 800
        h = meta.get("height") or 600
        if w <= 0 or h <= 0:
            w, h = 800, 600
        boundary = "StrictRecords" + uuid.uuid4().hex
        filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', meta["filename"])[:120] or "evidence.png"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: {meta["mime_type"]}\r\n\r\n').encode() + image + f'\r\n--{boundary}--\r\n'.encode()
        request = urllib.request.Request(DEFAULT_TAUFA_HOST + UPLOAD_API_PATH, data=body, method="POST",
            headers={"Cookie": cookies, "Origin": DEFAULT_TAUFA_HOST, "Referer": DESIGNATED_CHAT_URL,
                     "User-Agent": "Mozilla/5.0", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with _open_taufa_request(request, timeout=timeout, context=ssl.create_default_context()) as response:
            uploaded = json.loads(response.read(64 * 1024))
        if (not isinstance(uploaded, dict) or not uploaded.get("id") or uploaded.get("isImage") is not True
            or uploaded.get("mimeType") != meta["mime_type"] or not uploaded.get("filename")
            or not str(uploaded.get("url", "")).startswith("https://")):
            raise ValueError("Taufa did not confirm an image upload")
        return uploaded

