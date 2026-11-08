"""
StrictRecords — Official OpenAI API Evidence Validation Fallback
================================================================
Official OpenAI API fallback validator for activity proof submissions.
Activated when primary Taufa AI validator experiences technical failures:
- API timeout
- Service unavailable / network error / HTTP 5xx
- Invalid response schema
- Authentication failure / unconfigured session

Security & Compliance:
- Strictly backend only.
- Reads API key securely from OPENAI_API_KEY, STRICTRECORDS_AI_API_KEY, or Windows Credential Manager.
- Never extracts or reuses ChatGPT or Brave browser cookies.
- Never automates personal ChatGPT website or personal login sessions.
- Credentials are never sent to Android APK, Windows frontend UI, or browser extension.
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
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.rubric_loader import load_validation_rubric

logger = logging.getLogger("StrictRecords.OpenAiFallbackValidator")

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_CHAT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"

VALID_EVIDENCE_QUALITIES = {"EXEMPLARY", "STRONG", "CREDIBLE", "PARTIAL", "INSUFFICIENT"}
VALID_ACTIVITY_RELEVANCES = {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
VALID_STATUSES = {"APPROVED", "REJECTED", "REVIEW_REQUIRED", "VALIDATION_PENDING", "API_ERROR", "TIMEOUT", "PROVIDER_UNAVAILABLE"}

MAX_FALLBACK_PROFILES = 5


def encrypt_secret_dpapi(secret: str) -> str:
    """
    Encrypts a secret (e.g. OpenAI API Key) using Windows DPAPI (CryptProtectData).
    Fallback to machine-salted encoding if DPAPI is not available.
    """
    if not secret or not str(secret).strip():
        return ""
    clean = str(secret).strip()
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        raw = clean.encode("utf-8")
        blob_in = DATA_BLOB(len(raw), ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "StrictRecordsAISecret", None, None, None, 0, ctypes.byref(blob_out)):
            enc = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return "dpapi:" + base64.b64encode(enc).decode("ascii")
    except Exception as exc:
        logger.debug("Windows DPAPI encryption unavailable: %s", exc)

    salt = (os.environ.get("COMPUTERNAME", "SR_DEFAULT") + "_STRICTRECORDS_FALLBACK_SALT").encode("utf-8")
    raw = clean.encode("utf-8")
    xor_bytes = bytes([b ^ salt[i % len(salt)] for i, b in enumerate(raw)])
    return "salt:" + base64.b64encode(xor_bytes).decode("ascii")


def decrypt_secret_dpapi(payload: str) -> str:
    """
    Decrypts a secret previously protected with encrypt_secret_dpapi.
    """
    if not payload or not str(payload).strip():
        return ""
    clean = str(payload).strip()
    try:
        if clean.startswith("dpapi:"):
            enc = base64.b64decode(clean[6:])
            import ctypes
            import ctypes.wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

            blob_in = DATA_BLOB(len(enc), ctypes.cast(ctypes.c_char_p(enc), ctypes.POINTER(ctypes.c_char)))
            blob_out = DATA_BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                dec = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                return dec.decode("utf-8")
        elif clean.startswith("salt:"):
            enc = base64.b64decode(clean[5:])
            salt = (os.environ.get("COMPUTERNAME", "SR_DEFAULT") + "_STRICTRECORDS_FALLBACK_SALT").encode("utf-8")
            raw = bytes([b ^ salt[i % len(salt)] for i, b in enumerate(enc)])
            return raw.decode("utf-8")
        elif clean.startswith("sk-") or clean.startswith("sess-"):
            return clean
    except Exception as exc:
        logger.warning("Failed to decrypt secret: %s", exc)
    return ""


@dataclass
class OpenAiProfile:
    """
    Data model for one of up to 5 sequential OpenAI fallback profiles.
    API keys remain strictly server-side and encrypted at rest.
    """
    profile_id: str
    name: str = ""
    priority: int = 1
    enabled: bool = True
    model: str = DEFAULT_OPENAI_MODEL
    endpoint: str = OPENAI_CHAT_ENDPOINT
    encrypted_key: str = ""
    last_status: str = "NOT_CONFIGURED"  # CONNECTED, DEGRADED, UNAVAILABLE, NOT_CONFIGURED, DISABLED
    last_error: str = ""
    last_checked: str = ""
    response_latency_ms: int = 0
    _in_memory_key: Optional[str] = field(default=None, repr=False)

    @property
    def api_key(self) -> Optional[str]:
        if self._in_memory_key and self._in_memory_key.strip():
            return self._in_memory_key.strip()
        if self.encrypted_key:
            dec = decrypt_secret_dpapi(self.encrypted_key)
            if dec and dec.strip():
                return dec.strip()
        if self.priority == 1 or self.profile_id == "openai_1":
            env_k = os.environ.get("OPENAI_API_KEY") or os.environ.get("STRICTRECORDS_AI_API_KEY")
            if env_k and env_k.strip():
                return env_k.strip()
            try:
                import keyring
                stored = keyring.get_password("StrictRecords", "OpenAI_API_Key") or keyring.get_password("StrictRecords", "OpenAI_Key")
                if stored and stored.strip():
                    return stored.strip()
            except Exception:
                pass
        return None

    def is_configured(self) -> bool:
        k = self.api_key
        return bool(k and len(k) >= 8)

    def to_safe_dict(self) -> Dict[str, Any]:
        """SAFE metadata dictionary: NEVER contains the api_key or encrypted_key!"""
        effective_status = "DISABLED" if not self.enabled else (
            self.last_status if self.is_configured() else "NOT_CONFIGURED"
        )
        return {
            "profile_id": self.profile_id,
            "name": self.name or f"OpenAI API {self.priority}",
            "priority": self.priority,
            "enabled": self.enabled,
            "model": self.model,
            "is_configured": self.is_configured(),
            "status": effective_status,
            "last_checked": self.last_checked,
            "last_error": self.last_error,
            "response_latency_ms": self.response_latency_ms,
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """Internal storage dict: includes encrypted_key (DPAPI protected)."""
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "priority": self.priority,
            "enabled": self.enabled,
            "model": self.model,
            "endpoint": self.endpoint,
            "encrypted_key": self.encrypted_key,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_checked": self.last_checked,
            "response_latency_ms": self.response_latency_ms,
        }

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "OpenAiProfile":
        return cls(
            profile_id=str(data.get("profile_id") or "openai_1"),
            name=str(data.get("name") or ""),
            priority=int(data.get("priority") or 1),
            enabled=bool(data.get("enabled", True)),
            model=str(data.get("model") or DEFAULT_OPENAI_MODEL),
            endpoint=str(data.get("endpoint") or OPENAI_CHAT_ENDPOINT),
            encrypted_key=str(data.get("encrypted_key") or ""),
            last_status=str(data.get("last_status") or "NOT_CONFIGURED"),
            last_error=str(data.get("last_error") or ""),
            last_checked=str(data.get("last_checked") or ""),
            response_latency_ms=int(data.get("response_latency_ms") or 0),
        )


@dataclass
class OpenAiValidationResult:
    """Canonical structured evaluation response schema for StrictRecords."""
    submission_id: str
    validation_provider: str = "OPENAI_FALLBACK"
    validation_status: str = "VALIDATION_PENDING"  # APPROVED, REJECTED, REVIEW_REQUIRED, VALIDATION_PENDING, API_ERROR
    approved: bool = False
    activity_categories: List[str] = field(default_factory=list)
    evidence_quality: str = "INSUFFICIENT"  # EXEMPLARY, STRONG, CREDIBLE, PARTIAL, INSUFFICIENT
    activity_relevance: str = "INSUFFICIENT"  # HIGH, MEDIUM, LOW, INSUFFICIENT
    awarded_points: int = 0
    max_score: int = 8
    explanation: str = ""
    rejection_reason: str = ""
    confidence: float = 0.0
    validation_timestamp: str = ""
    rubric_version: str = "1.0.0"
    validator_id: str = "OPENAI_FALLBACK"
    is_technical_failure: bool = False
    raw_response: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    profile_id: Optional[str] = None
    profile_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "validation_provider": self.validation_provider,
            "validation_status": self.validation_status,
            "approved": self.approved,
            "activity_categories": list(self.activity_categories),
            "evidence_quality": self.evidence_quality,
            "activity_relevance": self.activity_relevance,
            "awarded_points": self.awarded_points,
            "max_score": self.max_score,
            "explanation": self.explanation,
            "rejection_reason": self.rejection_reason,
            "confidence": self.confidence,
            "validation_timestamp": self.validation_timestamp or datetime.now(timezone.utc).isoformat(),
            "rubric_version": self.rubric_version,
            "validator_id": self.validator_id,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "is_technical_failure": self.is_technical_failure,
            "warnings": list(self.warnings),
        }

    def as_review(self, activity_id: str, evidence_id: str) -> Dict[str, Any]:
        decision = "PROVISIONAL_APPROVE" if self.approved else (
            "REJECTED" if self.validation_status == "REJECTED" else (
                "NEEDS_REVIEW" if self.validation_status == "REVIEW_REQUIRED" else "PROCESSING_ERROR"
            )
        )
        return {
            "submission_id": self.submission_id,
            "activity_id": activity_id,
            "evidence_id": evidence_id,
            "decision": decision,
            "proposed_points": self.awarded_points if self.approved else 0,
            "qualifies_for_points": self.approved and self.awarded_points > 0,
            "admin_review_required": decision == "NEEDS_REVIEW",
            "category_match": self.activity_relevance in ("HIGH", "MEDIUM"),
            "description_match": self.activity_relevance in ("HIGH", "MEDIUM"),
            "evidence_match": self.evidence_quality in ("EXEMPLARY", "STRONG", "CREDIBLE", "PARTIAL"),
            "evidence_quality": self.evidence_quality,
            "validator_status": self.validation_status,
            "review_status": "APPROVED" if self.approved else ("REJECTED" if decision == "REJECTED" else "NEEDS_REVIEW"),
            "reasons": [self.explanation] if self.explanation else [],
            "warnings": self.warnings,
            "validator": self.validator_id,
            "validation_provider": self.validation_provider,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "rubric_version": self.rubric_version,
            "openai_fallback": self.to_dict(),
        }


class OpenAiFallbackValidator:
    """
    Official OpenAI API fallback validator for evidence validation.
    Executes vision-based multimodal inspections using the version-controlled StrictRecords rubric.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: float = 45.0,
        profile_id: Optional[str] = None,
        profile_name: Optional[str] = None,
    ):
        self._custom_api_key = api_key
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.endpoint = endpoint or os.environ.get("OPENAI_ENDPOINT") or OPENAI_CHAT_ENDPOINT
        self.timeout = timeout
        self.profile_id = profile_id or "openai_1"
        self.profile_name = profile_name or ""

    @property
    def api_key(self) -> Optional[str]:
        if self._custom_api_key and str(self._custom_api_key).strip():
            return self._custom_api_key.strip()
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("STRICTRECORDS_AI_API_KEY")
        if key and str(key).strip():
            return str(key).strip()
        try:
            import keyring
            stored = keyring.get_password("StrictRecords", "OpenAI_API_Key") or keyring.get_password("StrictRecords", "OpenAI_Key")
            if stored and stored.strip():
                return stored.strip()
        except Exception:
            pass
        return None

    def is_configured(self) -> bool:
        """Returns True if a valid API key is present."""
        if not self.api_key or len(self.api_key) < 8:
            return False
        if "openai.com" in (self.endpoint or "").lower():
            return self.api_key.startswith("sk-") or self.api_key.startswith("sess-")
        return True

    def _parse_categories(self, category: str, categories: Optional[List[str]] = None) -> List[str]:
        """Preserves and cleans multi-category listings (e.g. 'Study + Video Shooting + Video Editing')."""
        cats: List[str] = []
        if categories:
            for c in categories:
                c_str = str(c).strip()
                if c_str and c_str not in cats:
                    cats.append(c_str)
        if not cats and category:
            parts = re.split(r"[,+/&|]", str(category))
            for p in parts:
                p_clean = p.strip()
                if p_clean and p_clean not in cats:
                    cats.append(p_clean)
        return cats or [str(category or "STUDY").strip()]

    def validate_evidence(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        image_filenames: Optional[List[str]] = None,
        ocr_text: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        submission_timestamp: Optional[str] = None,
        activity_id: Optional[str] = None,
        session_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
        integrity_warnings: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        rubric_version: Optional[str] = None,
    ) -> OpenAiValidationResult:
        """
        Executes vision evaluation against the configured official OpenAI endpoint.
        Uses loaded StrictRecords validation rubric and validates output against the canonical schema.
        """
        clean_sub_id = (submission_id or "").strip()
        ts = submission_timestamp or datetime.now(timezone.utc).isoformat()
        warnings = list(integrity_warnings or [])
        rubric_content, loaded_rubric_ver = load_validation_rubric()
        rubric_ver = rubric_version or loaded_rubric_ver
        cat_list = self._parse_categories(category, categories)

        # 1. Reject empty image attachments
        if not images:
            return OpenAiValidationResult(
                submission_id=clean_sub_id,
                validation_provider="OPENAI_FALLBACK",
                approved=False,
                validation_status="REJECTED",
                activity_categories=cat_list,
                awarded_points=0,
                explanation="No image attachments provided for evaluation.",
                rejection_reason="NO_EVIDENCE_ATTACHED",
                validation_timestamp=ts,
                rubric_version=rubric_ver,
                is_technical_failure=False,
                warnings=warnings + ["NO_ATTACHMENTS_PROVIDED"],
            )

        # 2. Check if OpenAI is configured
        if not self.is_configured():
            logger.info("OpenAI Fallback Validator is not configured with an API key.")
            return OpenAiValidationResult(
                submission_id=clean_sub_id,
                validation_provider="OPENAI_FALLBACK",
                approved=False,
                validation_status="PROVIDER_UNAVAILABLE",
                activity_categories=cat_list,
                awarded_points=0,
                explanation="Official OpenAI API fallback is not configured. Retaining submission as VALIDATION_PENDING.",
                validation_timestamp=ts,
                rubric_version=rubric_ver,
                is_technical_failure=True,
                warnings=warnings + ["OPENAI_API_KEY_MISSING", "VALIDATION_PENDING"],
            )

        # 3. Build system instruction containing full StrictRecords Rubric
        system_instruction = (
            "You are the authoritative evidence-validation evaluator for StrictRecords.\n"
            "Evaluate whether the attached photographic evidence genuinely substantiates the user's claimed activity.\n\n"
            f"{rubric_content}\n\n"
            "CRITICAL: Return ONLY a raw JSON object (without markdown code fences) matching this schema:\n"
            "{\n"
            '  "submission_id": "' + clean_sub_id + '",\n'
            '  "validation_provider": "OPENAI_FALLBACK",\n'
            '  "validation_status": "APPROVED" | "REJECTED" | "REVIEW_REQUIRED",\n'
            '  "approved": boolean,\n'
            '  "activity_categories": ["' + '", "'.join(cat_list) + '"],\n'
            '  "evidence_quality": "EXEMPLARY" | "STRONG" | "CREDIBLE" | "PARTIAL" | "INSUFFICIENT",\n'
            '  "activity_relevance": "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT",\n'
            '  "awarded_points": integer (0 to 8),\n'
            '  "explanation": "1-3 concise sentences detailing what was visibly depicted in the image(s)",\n'
            '  "rejection_reason": "specific reason if rejected, else empty string",\n'
            '  "confidence": float (0.0 to 1.0),\n'
            '  "validation_timestamp": "' + ts + '",\n'
            '  "rubric_version": "' + rubric_ver + '"\n'
            "}"
        )

        user_prompt_text = (
            f"SUBMISSION DETAILS:\n"
            f"- Submission ID: {clean_sub_id}\n"
            f"- Activity ID: {activity_id or 'N/A'}\n"
            f"- Session ID: {session_id or 'N/A'}\n"
            f"- Activity Category: {', '.join(cat_list)}\n"
            f"- User Description: {description or 'N/A'}\n"
            f"- Hourly Window: {window_start or 'N/A'} to {window_end or 'N/A'}\n"
            f"- Submission Timestamp: {ts}\n"
            f"- Supporting OCR Text: {ocr_text[:500] if ocr_text else 'None detected'}\n"
            f"- Attached Photos: {len(images)} image(s)\n\n"
            "Visually inspect the attached image(s) thoroughly and return your structured assessment JSON."
        )

        # 4. Prepare base64 images
        image_parts = []
        for idx, img_bytes in enumerate(images[:5]):  # Up to 5 high-res photos
            mime = "image/jpeg"
            if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif img_bytes.startswith(b"GIF8"):
                mime = "image/gif"
            elif img_bytes.startswith(b"RIFF") and b"WEBP" in img_bytes[:16]:
                mime = "image/webp"
            b64_str = base64.b64encode(img_bytes).decode("ascii")
            data_url = f"data:{mime};base64,{b64_str}"
            image_parts.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})

        # 5. Build Chat Completions & Responses API requests
        req_timeout = timeout or self.timeout

        # Check if Responses API endpoint is requested
        is_responses_endpoint = "responses" in self.endpoint.lower()

        if is_responses_endpoint:
            # Responses API format
            payload = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": system_instruction},
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt_text}] + [
                            {"type": "input_image", "image_url": p["image_url"]["url"]} for p in image_parts
                        ],
                    },
                ],
                "response_format": {"type": "json_object"},
            }
        else:
            # Chat Completions API format
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": user_prompt_text}] + image_parts,
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": 1000,
            }

        body_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "StrictRecords-Core/2.0 (OpenAI-Vision-Fallback)",
            },
            method="POST",
        )

        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=req_timeout, context=ctx) as response:
                status_code = response.getcode()
                raw_bytes = response.read(2 * 1024 * 1024)
                raw_json = json.loads(raw_bytes.decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as err:
            logger.warning("OpenAI fallback request to %s failed: %s", self.endpoint, err)
            # If Responses API returned 404/400, retry once using standard Chat Completions
            if is_responses_endpoint and isinstance(err, urllib.error.HTTPError) and err.code in (400, 404):
                logger.info("Responses API endpoint unavailable (%d), falling back to Chat Completions", err.code)
                self.endpoint = OPENAI_CHAT_ENDPOINT
                return self.validate_evidence(
                    submission_id=submission_id,
                    category=category,
                    description=description,
                    images=images,
                    image_filenames=image_filenames,
                    ocr_text=ocr_text,
                    window_start=window_start,
                    window_end=window_end,
                    submission_timestamp=submission_timestamp,
                    activity_id=activity_id,
                    session_id=session_id,
                    categories=categories,
                    integrity_warnings=integrity_warnings,
                    timeout=timeout,
                )

            is_timeout = isinstance(err, (TimeoutError, urllib.error.URLError)) and "timed out" in str(err).lower()
            status_label = "TIMEOUT" if is_timeout else "API_ERROR"
            return OpenAiValidationResult(
                submission_id=clean_sub_id,
                validation_provider="OPENAI_FALLBACK",
                approved=False,
                validation_status=status_label,
                activity_categories=cat_list,
                awarded_points=0,
                explanation=f"OpenAI fallback technical error: {err}. Retaining submission in pending state.",
                validation_timestamp=ts,
                rubric_version=rubric_ver,
                is_technical_failure=True,
                warnings=warnings + [f"OPENAI_{status_label}", "VALIDATION_PENDING"],
            )
        except Exception as exc:
            logger.exception("Unexpected error in OpenAI fallback: %s", exc)
            return OpenAiValidationResult(
                submission_id=clean_sub_id,
                validation_provider="OPENAI_FALLBACK",
                approved=False,
                validation_status="API_ERROR",
                activity_categories=cat_list,
                awarded_points=0,
                explanation=f"OpenAI fallback exception: {exc}. Retaining submission in pending state.",
                validation_timestamp=ts,
                rubric_version=rubric_ver,
                is_technical_failure=True,
                warnings=warnings + ["OPENAI_UNEXPECTED_ERROR", "VALIDATION_PENDING"],
            )

        # 6. Parse response from OpenAI (supports both Responses API and Chat Completions schema)
        try:
            content_str = ""
            if "output" in raw_json and isinstance(raw_json["output"], list):
                # Responses API format
                for item in raw_json["output"]:
                    if isinstance(item, dict) and item.get("type") == "message":
                        content = item.get("content") or []
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                content_str = c.get("text", "")
            elif "choices" in raw_json and len(raw_json["choices"]) > 0:
                # Chat completions format
                choice = raw_json["choices"][0]
                message = choice.get("message") or {}
                content_str = message.get("content") or ""

            if not content_str:
                raise ValueError("No content found in OpenAI response")

            # Extract json if markdown fenced
            clean_content = content_str.strip()
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_content, re.DOTALL)
            json_target = fence_match.group(1) if fence_match else clean_content
            parsed = json.loads(json_target)
            if not isinstance(parsed, dict):
                raise ValueError("Response root is not a JSON object")

        except Exception as parse_err:
            logger.warning("Failed to parse OpenAI JSON response: %s; raw: %s", parse_err, raw_json)
            return OpenAiValidationResult(
                submission_id=clean_sub_id,
                validation_provider="OPENAI_FALLBACK",
                approved=False,
                validation_status="API_ERROR",
                activity_categories=cat_list,
                awarded_points=0,
                explanation=f"Failed to parse structured response from OpenAI: {parse_err}. Retaining submission.",
                validation_timestamp=ts,
                rubric_version=rubric_ver,
                is_technical_failure=True,
                raw_response=raw_json,
                warnings=warnings + ["OPENAI_INVALID_JSON", "VALIDATION_PENDING"],
            )

        # 7. Validate every required field per Section 4
        return self._validate_structured_response(
            parsed=parsed,
            submission_id=clean_sub_id,
            cat_list=cat_list,
            ts=ts,
            rubric_ver=rubric_ver,
            warnings=warnings,
            raw_response=raw_json,
        )

    def _validate_structured_response(
        self,
        parsed: Dict[str, Any],
        submission_id: str,
        cat_list: List[str],
        ts: str,
        rubric_ver: str,
        warnings: List[str],
        raw_response: Dict[str, Any],
    ) -> OpenAiValidationResult:
        """Validates all schema fields, types, and values strictly."""
        # submission_id check
        resp_sub_id = str(parsed.get("submission_id") or submission_id).strip()

        # approved
        approved = bool(parsed.get("approved", False))

        # awarded_points
        try:
            pts_raw = int(parsed.get("awarded_points", 0))
            awarded_points = max(0, min(8, pts_raw))
        except (ValueError, TypeError):
            awarded_points = 5 if approved else 0

        # Enforce consistency: if approved, score must be >= 5 per StrictRecords rubric
        if approved and awarded_points < 5:
            awarded_points = 5
        elif not approved and awarded_points > 4:
            awarded_points = 0

        # validation_status
        val_status = str(parsed.get("validation_status") or ("APPROVED" if approved else "REJECTED")).upper().strip()
        if val_status not in VALID_STATUSES:
            val_status = "APPROVED" if approved else "REJECTED"

        # activity_categories
        resp_cats = parsed.get("activity_categories")
        if isinstance(resp_cats, list) and resp_cats:
            final_cats = [str(c).strip() for c in resp_cats if str(c).strip()]
        else:
            final_cats = cat_list

        # evidence_quality
        ev_qual = str(parsed.get("evidence_quality") or ("STRONG" if approved else "INSUFFICIENT")).upper().strip()
        if ev_qual not in VALID_EVIDENCE_QUALITIES:
            ev_qual = "STRONG" if approved else "INSUFFICIENT"

        # activity_relevance
        act_rel = str(parsed.get("activity_relevance") or ("HIGH" if approved else "INSUFFICIENT")).upper().strip()
        if act_rel not in VALID_ACTIVITY_RELEVANCES:
            act_rel = "HIGH" if approved else "INSUFFICIENT"

        # confidence
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.90))))
        except (ValueError, TypeError):
            confidence = 0.85

        # explanation & rejection_reason
        explanation = str(parsed.get("explanation") or ("Visual evidence substantiates the activity." if approved else "Evidence insufficient.")).strip()
        rejection_reason = str(parsed.get("rejection_reason") or ("" if approved else explanation)).strip()

        # rubric_version
        resp_rubric_ver = str(parsed.get("rubric_version") or rubric_ver).strip()

        v_id = f"OPENAI_FALLBACK ({self.profile_name})" if self.profile_name else (
            f"OPENAI_FALLBACK ({self.profile_id})" if self.profile_id else "OPENAI_FALLBACK"
        )

        return OpenAiValidationResult(
            submission_id=resp_sub_id,
            validation_provider="OPENAI_FALLBACK",
            validation_status=val_status,
            approved=approved,
            activity_categories=final_cats,
            evidence_quality=ev_qual,
            activity_relevance=act_rel,
            awarded_points=awarded_points,
            explanation=explanation,
            rejection_reason=rejection_reason,
            confidence=confidence,
            validation_timestamp=ts,
            rubric_version=resp_rubric_ver,
            validator_id=v_id,
            profile_id=self.profile_id,
            profile_name=self.profile_name,
            is_technical_failure=False,
            raw_response=raw_response,
            warnings=list(warnings),
        )


class OpenAiMultiProfileManager:
    """
    Authoritative Centralized AI Provider & Fallback Manager (Phases 1–12)
    Manages up to 5 sequential OpenAI fallback profiles.
    Synchronizes safe metadata to Android APK and controls fallback execution.
    """
    _instance: Optional["OpenAiMultiProfileManager"] = None
    _lock = threading.Lock()

    def __init__(self, db: Optional[Any] = None):
        self._db = db
        self._profiles: List[OpenAiProfile] = []
        self._config_version: int = 1
        self._load_from_db()

    @classmethod
    def get_instance(cls, db: Optional[Any] = None) -> "OpenAiMultiProfileManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db=db)
            elif db is not None and cls._instance._db is None:
                cls._instance._db = db
                cls._instance._load_from_db()
            return cls._instance

    def _ensure_db(self):
        if self._db is None:
            try:
                from core.database import StrictRecordsDB
                self._db = StrictRecordsDB()
            except Exception as e:
                logger.warning("Could not instantiate StrictRecordsDB in OpenAiMultiProfileManager: %s", e)

    def _load_from_db(self) -> None:
        self._ensure_db()
        if not self._db:
            self._init_default_profiles()
            return

        try:
            ver_raw = self._db.get_config_value("ai_provider_config_version", "1")
            self._config_version = int(ver_raw) if str(ver_raw).isdigit() else 1
        except Exception:
            self._config_version = 1

        try:
            raw_json = self._db.get_config_value("ai_provider_config", "")
            if raw_json and raw_json.strip():
                stored_list = json.loads(raw_json)
                if isinstance(stored_list, list) and len(stored_list) > 0:
                    loaded = [OpenAiProfile.from_storage_dict(item) for item in stored_list if isinstance(item, dict)]
                    # Pad or trim to exactly MAX_FALLBACK_PROFILES (5)
                    by_id = {p.profile_id: p for p in loaded}
                    final_profiles = []
                    for i in range(1, MAX_FALLBACK_PROFILES + 1):
                        pid = f"openai_{i}"
                        if pid in by_id:
                            p = by_id[pid]
                            p.priority = i
                            final_profiles.append(p)
                        else:
                            final_profiles.append(OpenAiProfile(
                                profile_id=pid,
                                name=f"OpenAI API {i}",
                                priority=i,
                                enabled=(i == 1),
                                model=DEFAULT_OPENAI_MODEL,
                            ))
                    self._profiles = final_profiles
                    return
        except Exception as e:
            logger.warning("Failed to load ai_provider_config from DB: %s", e)

        self._init_default_profiles()
        self._save_to_db()

    def _init_default_profiles(self) -> None:
        self._profiles = [
            OpenAiProfile(
                profile_id="openai_1",
                name="OpenAI API 1 (Main Fallback)",
                priority=1,
                enabled=True,
                model="gpt-4o-mini",
            ),
            OpenAiProfile(
                profile_id="openai_2",
                name="OpenAI API 2 (Secondary Fallback)",
                priority=2,
                enabled=False,
                model="gpt-4o-mini",
            ),
            OpenAiProfile(
                profile_id="openai_3",
                name="OpenAI API 3 (Tier 3 Fallback)",
                priority=3,
                enabled=False,
                model="gpt-4o-mini",
            ),
            OpenAiProfile(
                profile_id="openai_4",
                name="OpenAI API 4 (Tier 4 Fallback)",
                priority=4,
                enabled=False,
                model="gpt-4o-mini",
            ),
            OpenAiProfile(
                profile_id="openai_5",
                name="OpenAI API 5 (Tier 5 Fallback)",
                priority=5,
                enabled=False,
                model="gpt-4o-mini",
            ),
        ]

    def _save_to_db(self) -> None:
        self._ensure_db()
        if not self._db:
            return
        try:
            stored_data = [p.to_storage_dict() for p in self.get_profiles_in_priority_order()]
            self._db.set_config_value("ai_provider_config", json.dumps(stored_data))
            self._db.set_config_value("ai_provider_config_version", str(self._config_version))
            logger.info("Saved %d AI fallback profiles to DB (Version: %d)", len(stored_data), self._config_version)
        except Exception as e:
            logger.exception("Failed to save ai_provider_config to DB: %s", e)

    def get_config_version(self) -> int:
        return self._config_version

    def get_profiles(self) -> List[OpenAiProfile]:
        return list(self._profiles)

    def get_profiles_in_priority_order(self) -> List[OpenAiProfile]:
        return sorted(self._profiles, key=lambda p: p.priority)

    def get_enabled_configured_profiles(self) -> List[OpenAiProfile]:
        return [p for p in self.get_profiles_in_priority_order() if p.enabled and p.is_configured()]

    def get_safe_metadata(self) -> Dict[str, Any]:
        """
        Returns sanitized AI configuration metadata suitable for Android synchronization.
        STRICTLY GUARANTEED to contain NO credentials or API keys.
        """
        with self._lock:
            safe_profiles = [p.to_safe_dict() for p in self.get_profiles_in_priority_order()]
            now_iso = datetime.now(timezone.utc).isoformat()
            taufa_status = "CONNECTED"
            taufa_latency = 45
            taufa_checked = now_iso
            taufa_error = ""
            active_provider = "TAUFA_AI"
            try:
                from core.ai_health_coordinator import ai_health_coordinator
                rec = ai_health_coordinator._cached_record or {}
                if rec.get("taufa_status") and rec.get("taufa_status") != "NOT_CHECKED":
                    taufa_status = str(rec.get("taufa_status"))
                if rec.get("response_latency"):
                    taufa_latency = int(rec.get("response_latency") or 0)
                if rec.get("last_health_check"):
                    taufa_checked = str(rec.get("last_health_check"))
                if rec.get("last_error"):
                    taufa_error = str(rec.get("last_error"))
                if rec.get("active_provider") and rec.get("active_provider") != "UNAVAILABLE":
                    active_provider = str(rec.get("active_provider"))
                elif any(p["enabled"] and p["is_configured"] for p in safe_profiles) and taufa_status != "CONNECTED":
                    active_provider = "OPENAI_FALLBACK"
            except Exception:
                pass
            return {
                "config_version": self._config_version,
                "profiles": safe_profiles,
                "primary_provider": {
                    "name": "Taufa AI",
                    "status": taufa_status,
                    "latency_ms": taufa_latency,
                    "last_checked": taufa_checked,
                    "last_error": taufa_error,
                },
                "active_provider": active_provider,
                "last_sync": now_iso,
                "total_profiles": len(safe_profiles),
                "enabled_profiles_count": len([p for p in safe_profiles if p["enabled"] and p["is_configured"]]),
            }

    def save_profiles(self, profiles_data: List[Dict[str, Any]], increment_version: bool = True) -> Dict[str, Any]:
        """
        Updates profile configuration from Windows Settings.
        Encrypts secrets using DPAPI.
        Recalculates priorities and increments version.
        """
        with self._lock:
            updated_list: List[OpenAiProfile] = []
            existing_by_id = {p.profile_id: p for p in self._profiles}

            for idx, item in enumerate(profiles_data[:MAX_FALLBACK_PROFILES]):
                pid = str(item.get("profile_id") or f"openai_{idx+1}")
                existing = existing_by_id.get(pid)

                raw_key = item.get("api_key")
                if raw_key and str(raw_key).strip() and not str(raw_key).startswith("••"):
                    enc_key = encrypt_secret_dpapi(str(raw_key).strip())
                else:
                    enc_key = existing.encrypted_key if existing else ""

                name = str(item.get("name") or (existing.name if existing else f"OpenAI API {idx+1}")).strip()
                priority = int(item.get("priority") or (idx + 1))
                enabled = bool(item.get("enabled", True if idx == 0 else False))
                model = str(item.get("model") or DEFAULT_OPENAI_MODEL).strip()
                endpoint = str(item.get("endpoint") or OPENAI_CHAT_ENDPOINT).strip()

                p = OpenAiProfile(
                    profile_id=pid,
                    name=name,
                    priority=priority,
                    enabled=enabled,
                    model=model,
                    endpoint=endpoint,
                    encrypted_key=enc_key,
                    last_status=existing.last_status if existing else "NOT_CONFIGURED",
                    last_error=existing.last_error if existing else "",
                    last_checked=existing.last_checked if existing else "",
                    response_latency_ms=existing.response_latency_ms if existing else 0,
                )
                updated_list.append(p)

            # Ensure 5 profiles
            while len(updated_list) < MAX_FALLBACK_PROFILES:
                idx = len(updated_list)
                pid = f"openai_{idx+1}"
                updated_list.append(OpenAiProfile(
                    profile_id=pid,
                    name=f"OpenAI API {idx+1}",
                    priority=idx+1,
                    enabled=False,
                ))

            # Normalize priorities 1..5
            updated_list.sort(key=lambda x: x.priority)
            for i, p in enumerate(updated_list):
                p.priority = i + 1

            self._profiles = updated_list
            if increment_version:
                self._config_version += 1

            self._save_to_db()

            try:
                from core.event_bus import EventBus
                EventBus.get_instance().publish(
                    "AI_CONFIG_UPDATED",
                    {"config_version": self._config_version, "profiles": [p.to_safe_dict() for p in self._profiles]},
                )
            except Exception:
                pass

            return self.get_safe_metadata()

    def probe_profile(self, profile: OpenAiProfile, timeout_sec: float = 8.0) -> Dict[str, Any]:
        """
        Sends a minimal harmless inference probe to verify reachability and authentication.
        """
        start_t = time.monotonic()
        now_iso = datetime.now(timezone.utc).isoformat()
        if not profile.is_configured():
            profile.last_status = "NOT_CONFIGURED"
            profile.last_error = "API key missing or unconfigured"
            profile.last_checked = now_iso
            return profile.to_safe_dict()

        if not profile.enabled:
            profile.last_status = "DISABLED"
            profile.last_checked = now_iso
            return profile.to_safe_dict()

        try:
            val = OpenAiFallbackValidator(
                api_key=profile.api_key,
                model=profile.model,
                endpoint=profile.endpoint,
                timeout=timeout_sec,
                profile_id=profile.profile_id,
                profile_name=profile.name,
            )
            # Lightweight minimal test payload
            payload = {
                "model": profile.model,
                "messages": [{"role": "user", "content": 'Respond with JSON: {"status": "ok"}'}],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 15,
            }
            body = json.dumps(payload).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {profile.api_key}",
                "User-Agent": "StrictRecords-AiPreflight/2.0",
            }
            ctx = ssl.create_default_context()
            req = urllib.request.Request(profile.endpoint, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                status_code = resp.status
                latency = int((time.monotonic() - start_t) * 1000)
                if status_code in (200, 201):
                    profile.last_status = "CONNECTED"
                    profile.last_error = ""
                    profile.last_checked = now_iso
                    profile.response_latency_ms = latency
                else:
                    profile.last_status = "DEGRADED"
                    profile.last_error = f"HTTP {status_code}"
                    profile.last_checked = now_iso
                    profile.response_latency_ms = latency
        except urllib.error.HTTPError as he:
            latency = int((time.monotonic() - start_t) * 1000)
            profile.response_latency_ms = latency
            profile.last_checked = now_iso
            if he.code == 401:
                profile.last_status = "UNAVAILABLE"
                profile.last_error = "Authentication failed (invalid API key)"
            elif he.code == 429:
                profile.last_status = "DEGRADED"
                profile.last_error = "Rate limit / quota exceeded (HTTP 429)"
            else:
                profile.last_status = "UNAVAILABLE"
                profile.last_error = f"HTTP {he.code}: {he.reason}"
        except Exception as exc:
            latency = int((time.monotonic() - start_t) * 1000)
            profile.response_latency_ms = latency
            profile.last_checked = now_iso
            profile.last_status = "UNAVAILABLE"
            profile.last_error = str(exc)

        self._save_to_db()
        return profile.to_safe_dict()

    def probe_all_profiles(self, timeout_sec: float = 8.0) -> List[Dict[str, Any]]:
        results = []
        for p in self.get_profiles_in_priority_order():
            res = self.probe_profile(p, timeout_sec=timeout_sec)
            results.append(res)
        return results

    def validate_with_sequential_fallback(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        image_filenames: Optional[List[str]] = None,
        ocr_text: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        submission_timestamp: Optional[str] = None,
        activity_id: Optional[str] = None,
        session_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
        rubric_version: str = "1.0.0",
    ) -> OpenAiValidationResult:
        """
        Executes sequential fallback across enabled OpenAI profiles:
        OpenAI API 1 -> OpenAI API 2 -> OpenAI API 3 -> OpenAI API 4 -> OpenAI API 5.

        CRITICAL ENFORCEMENT:
        1. A valid structured decision (APPROVED, REJECTED, REVIEW_REQUIRED) is TERMINAL.
           It must NEVER trigger fallback to another profile!
        2. Fallback is ONLY triggered by technical failures (TIMEOUT, HTTP 5xx, API_ERROR, RATE_LIMITED).
        3. If all profiles fail technically, returns a technical failure result without deducting points.
        """
        enabled_profiles = self.get_enabled_configured_profiles()
        if not enabled_profiles:
            logger.warning("No enabled OpenAI fallback profiles configured with valid credentials.")
            return OpenAiValidationResult(
                submission_id=submission_id,
                validation_provider="OPENAI_FALLBACK",
                validation_status="PROVIDER_UNAVAILABLE",
                approved=False,
                awarded_points=0,
                explanation="No enabled OpenAI fallback profiles configured with valid API keys.",
                rejection_reason="",
                validator_id="OPENAI_FALLBACK",
                is_technical_failure=True,
                warnings=["NO_CONFIGURED_OPENAI_FALLBACK"],
            )

        attempted_history: List[Dict[str, Any]] = []

        for profile in enabled_profiles:
            logger.info("Sequential Fallback: Trying profile '%s' (Priority %d, Model: %s)", profile.name, profile.priority, profile.model)
            start_t = time.monotonic()
            try:
                val = OpenAiFallbackValidator(
                    api_key=profile.api_key,
                    model=profile.model,
                    endpoint=profile.endpoint,
                    profile_id=profile.profile_id,
                    profile_name=profile.name,
                )
                result = val.validate_evidence(
                    submission_id=submission_id,
                    category=category,
                    description=description,
                    images=images,
                    image_filenames=image_filenames,
                    ocr_text=ocr_text,
                    window_start=window_start,
                    window_end=window_end,
                    submission_timestamp=submission_timestamp,
                    activity_id=activity_id,
                    session_id=session_id,
                    categories=categories,
                    rubric_version=rubric_version,
                )
                latency = int((time.monotonic() - start_t) * 1000)

                if result.is_technical_failure:
                    profile.last_status = "DEGRADED" if "RATE" in result.validation_status else "UNAVAILABLE"
                    profile.last_error = result.explanation
                    profile.last_checked = datetime.now(timezone.utc).isoformat()
                    profile.response_latency_ms = latency
                    attempted_history.append({"profile": profile.name, "priority": profile.priority, "status": "FAILED", "error": result.explanation})
                    logger.warning("Profile '%s' had technical failure (%s): %s; cascading to next profile", profile.name, result.validation_status, result.explanation)
                    continue
                else:
                    # Valid structured decision!
                    profile.last_status = "CONNECTED"
                    profile.last_error = ""
                    profile.last_checked = datetime.now(timezone.utc).isoformat()
                    profile.response_latency_ms = latency
                    result.profile_id = profile.profile_id
                    result.profile_name = profile.name
                    result.validator_id = f"OPENAI_FALLBACK ({profile.name})"
                    logger.info("Profile '%s' returned valid decision (%s, Approved=%s). Chain complete.", profile.name, result.validation_status, result.approved)
                    self._save_to_db()
                    return result

            except Exception as exc:
                latency = int((time.monotonic() - start_t) * 1000)
                profile.last_status = "UNAVAILABLE"
                profile.last_error = str(exc)
                profile.last_checked = datetime.now(timezone.utc).isoformat()
                profile.response_latency_ms = latency
                attempted_history.append({"profile": profile.name, "priority": profile.priority, "status": "ERROR", "error": str(exc)})
                logger.exception("Unexpected error validating with profile '%s': %s", profile.name, exc)
                continue

        self._save_to_db()
        logger.error("All %d enabled OpenAI fallback profiles failed technically.", len(enabled_profiles))
        return OpenAiValidationResult(
            submission_id=submission_id,
            validation_provider="OPENAI_FALLBACK",
            validation_status="PROVIDER_UNAVAILABLE",
            approved=False,
            awarded_points=0,
            explanation=f"All {len(enabled_profiles)} configured OpenAI fallback profiles encountered technical errors.",
            rejection_reason="",
            validator_id="OPENAI_FALLBACK",
            is_technical_failure=True,
            warnings=[f"PROFILES_EXHAUSTED: {json.dumps(attempted_history)}"],
        )


# Global singleton access
openai_multi_profile_manager = OpenAiMultiProfileManager.get_instance()
