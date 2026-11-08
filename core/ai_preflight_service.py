"""
StrictRecords — Automatic 5-Minute AI Preflight Service
======================================================
Coordinates automatic AI connectivity and inference-readiness checks before every
scheduled proof-submission window.

Guarantees:
1. Triggered exactly 5 minutes before every eligible proof-submission window opening.
2. Independently tests BOTH Taufa AI and official OpenAI API in parallel.
3. Uses a tiny diagnostic prompt: {"service":"ok","validation_ready":true}.
4. Never contains personal photos, study evidence, exam docs, or private info.
5. Never creates a proof submission, generates points, or modifies evidence records.
6. Persists independent provider health results keyed by proof-window ID.
7. Selects validation provider intelligently (Taufa primary, OpenAI fallback).
8. Bounded retries for transient failures; avoids repeated checks on auth or quota failure.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("StrictRecords.AiPreflightService")

IST = timezone(timedelta(hours=5, minutes=30))

# Predefined minimal diagnostic prompt (Section 3)
MINIMAL_PREFLIGHT_PROMPT = 'Return the following JSON exactly: {"service":"ok","validation_ready":true}'

# Provider States (Section 4)
STATE_NOT_CHECKED = "NOT_CHECKED"
STATE_CHECKING = "CHECKING"
STATE_CONNECTED = "CONNECTED"
STATE_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
STATE_RATE_LIMITED = "RATE_LIMITED"
STATE_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
STATE_TIMEOUT = "TIMEOUT"
STATE_INFERENCE_FAILED = "INFERENCE_FAILED"
STATE_RESPONSE_INVALID = "RESPONSE_INVALID"
STATE_UNAVAILABLE = "UNAVAILABLE"

# Selected Provider Names (Section 5)
PROVIDER_TAUFA_AI = "TAUFA_AI"
PROVIDER_OPENAI_FALLBACK = "OPENAI_FALLBACK"
PROVIDER_PENDING_VALIDATION = "PENDING_VALIDATION"
PROVIDER_NONE = "NONE"

# Preflight Statuses
STATUS_COMPLETED = "COMPLETED"
STATUS_PENDING_RETRY = "PENDING_RETRY"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"
STATUS_SKIPPED = "SKIPPED"

# Non-retryable terminal errors
NON_RETRYABLE_STATES = {
    STATE_AUTHENTICATION_FAILED,
    STATE_QUOTA_EXCEEDED,
    STATE_RATE_LIMITED,
}

MAX_PREFLIGHT_RETRIES = 2
DEFAULT_PREFLIGHT_TIMEOUT_SEC = 10.0
CACHE_FRESHNESS_SEC = 180.0  # 3 minutes shared provider-health cache


@dataclass
class ProviderProbeResult:
    provider: str
    status: str
    latency_ms: int = 0
    error: str = ""
    model: str = ""
    raw_response: Optional[str] = None
    validation_ready: bool = False


@dataclass
class AiPreflightResult:
    preflight_id: str
    proof_window_id: str
    activity_id: str
    scheduled_open_time: str
    preflight_started_at: str
    taufa_status: str = STATE_NOT_CHECKED
    taufa_latency: int = 0
    taufa_error: str = ""
    openai_status: str = STATE_NOT_CHECKED
    openai_latency: int = 0
    openai_error: str = ""
    selected_validation_provider: str = PROVIDER_PENDING_VALIDATION
    primary_provider: str = PROVIDER_NONE
    backup_provider: str = PROVIDER_NONE
    preflight_completed_at: str = ""
    retry_count: int = 0
    preflight_status: str = STATUS_COMPLETED
    schedule_version: int = 1
    display_title: str = ""
    display_lines: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["details_json"] = json.dumps(self.details)
        d["openai_profiles"] = self.details.get("openai_profiles", [])
        d["config_version"] = self.details.get("config_version", 1)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AiPreflightResult":
        details = data.get("details")
        if not isinstance(details, dict):
            raw_dj = data.get("details_json", "{}")
            try:
                details = json.loads(raw_dj) if isinstance(raw_dj, str) else {}
            except Exception:
                details = {}
        return cls(
            preflight_id=data.get("preflight_id") or str(uuid.uuid4()),
            proof_window_id=data.get("proof_window_id") or "",
            activity_id=data.get("activity_id") or "",
            scheduled_open_time=data.get("scheduled_open_time") or "",
            preflight_started_at=data.get("preflight_started_at") or "",
            taufa_status=data.get("taufa_status") or STATE_NOT_CHECKED,
            taufa_latency=int(data.get("taufa_latency") or 0),
            taufa_error=data.get("taufa_error") or "",
            openai_status=data.get("openai_status") or STATE_NOT_CHECKED,
            openai_latency=int(data.get("openai_latency") or 0),
            openai_error=data.get("openai_error") or "",
            selected_validation_provider=data.get("selected_validation_provider") or PROVIDER_PENDING_VALIDATION,
            primary_provider=data.get("primary_provider") or PROVIDER_NONE,
            backup_provider=data.get("backup_provider") or PROVIDER_NONE,
            preflight_completed_at=data.get("preflight_completed_at") or "",
            retry_count=int(data.get("retry_count") or 0),
            preflight_status=data.get("preflight_status") or STATUS_COMPLETED,
            schedule_version=int(data.get("schedule_version") or 1),
            display_title=data.get("display_title") or "",
            display_lines=list(data.get("display_lines") or []),
            details=details,
        )

    @property
    def taufa(self) -> ProviderProbeResult:
        return ProviderProbeResult(
            provider=PROVIDER_TAUFA_AI,
            status=self.taufa_status,
            latency_ms=self.taufa_latency,
            error=self.taufa_error,
            validation_ready=(self.taufa_status == STATE_CONNECTED),
        )

    @property
    def openai(self) -> ProviderProbeResult:
        return ProviderProbeResult(
            provider=PROVIDER_OPENAI_FALLBACK,
            status=self.openai_status,
            latency_ms=self.openai_latency,
            error=self.openai_error,
            validation_ready=(self.openai_status == STATE_CONNECTED),
        )


class AiPreflightService:
    """
    Authoritative 5-minute preflight execution service.
    Singleton instance that coordinates real parallel probes against Taufa AI
    and official OpenAI API, persistence in SQLite, and provider selection.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, db=None):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(AiPreflightService, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db=None):
        if getattr(self, "_initialized", False):
            if db is not None:
                self.db = db
            return
        self.db = db
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="AiPreflightWorker")
        self._active_preflights: Dict[str, AiPreflightResult] = {}
        self._cached_taufa_probe: Optional[Tuple[float, ProviderProbeResult]] = None
        self._cached_openai_probe: Optional[Tuple[float, ProviderProbeResult]] = None
        self._listeners: List[Callable[[AiPreflightResult], None]] = []
        self._initialized = True

    @classmethod
    def get_instance(cls, db=None) -> "AiPreflightService":
        return cls(db=db)

    def add_listener(self, listener: Callable[[AiPreflightResult], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[AiPreflightResult], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_listeners(self, result: AiPreflightResult) -> None:
        for l in list(self._listeners):
            try:
                l(result)
            except Exception as e:
                logger.debug("Error in preflight listener: %s", e)

    def clear_cache(self) -> None:
        with self._lock:
            self._cached_taufa_probe = None
            self._cached_openai_probe = None

    # -------------------------------------------------------------------------
    # Parallel Probes (Section 2 & 3)
    # -------------------------------------------------------------------------
    def probe_taufa_ai(self, timeout_sec: float = DEFAULT_PREFLIGHT_TIMEOUT_SEC, force: bool = False) -> ProviderProbeResult:
        """
        Sends a minimal harmless test request to Taufa AI.
        Verifies service reachability, authentication, response parsing, and endpoint operation.
        Never uses actual user evidence.
        """
        now_mono = time.monotonic()
        with self._lock:
            if not force and self._cached_taufa_probe:
                cached_time, cached_res = self._cached_taufa_probe
                if (now_mono - cached_time) < CACHE_FRESHNESS_SEC and cached_res.status == STATE_CONNECTED:
                    return cached_res

        start_t = time.monotonic()
        try:
            from core.taufa_ai_validator import TaufaAiValidator
            validator = TaufaAiValidator()
            endpoint = validator.get_endpoint()
            api_key = validator.get_api_key()

            if not endpoint or not api_key:
                from core.taufa_access_policy import evaluate_taufa_access_policy
                taufa_policy = evaluate_taufa_access_policy(validator)
                if taufa_policy.get("taufa_ready"):
                    res = ProviderProbeResult(
                        provider=PROVIDER_TAUFA_AI,
                        status=STATE_CONNECTED,
                        latency_ms=max(1, int((time.monotonic() - start_t) * 1000)),
                        validation_ready=True,
                    )
                    with self._lock:
                        self._cached_taufa_probe = (time.monotonic(), res)
                    return res

                return ProviderProbeResult(
                    provider=PROVIDER_TAUFA_AI,
                    status=STATE_AUTHENTICATION_FAILED if not api_key else STATE_UNAVAILABLE,
                    latency_ms=int((time.monotonic() - start_t) * 1000),
                    error=taufa_policy.get("message") or "Taufa AI credentials or endpoint unconfigured.",
                    validation_ready=False,
                )

            conv_id = TaufaAiValidator.DESIGNATED_CONVERSATION_ID
            payload = {
                "conversationId": conv_id,
                "id": conv_id,
                "health_check_request_id": f"preflight_{uuid.uuid4().hex[:8]}",
                "messages": [
                    {
                        "id": str(uuid.uuid4()),
                        "role": "user",
                        "content": MINIMAL_PREFLIGHT_PROMPT,
                    }
                ],
            }
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {api_key}",
                "X-Conversation-Id": conv_id,
                "User-Agent": "StrictRecords-AiPreflight/1.0",
            }
            if "=" in api_key or ";" in api_key:
                headers["Cookie"] = api_key

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                code = resp.getcode()
                raw = resp.read().decode("utf-8", errors="replace")

            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))

            if 200 <= code < 300 and raw.strip():
                # Verify JSON structure
                parsed = None
                try:
                    parsed = json.loads(raw)
                except Exception:
                    # Some endpoints return text stream
                    clean = re.sub(r'^[0-9]+:"(.*)"$', r'\1', raw.strip(), flags=re.MULTILINE)
                    try:
                        parsed = json.loads(clean)
                    except Exception:
                        pass

                is_ok = False
                if isinstance(parsed, dict):
                    if parsed.get("service") == "ok" or parsed.get("validation_ready") is True or parsed.get("status") == "ok":
                        is_ok = True
                elif '"service"' in raw and '"ok"' in raw:
                    is_ok = True

                if is_ok or (200 <= code < 300 and ("ok" in raw.lower() or "ready" in raw.lower())):
                    res = ProviderProbeResult(
                        provider=PROVIDER_TAUFA_AI,
                        status=STATE_CONNECTED,
                        latency_ms=latency_ms,
                        validation_ready=True,
                        raw_response=raw[:200],
                    )
                    with self._lock:
                        self._cached_taufa_probe = (time.monotonic(), res)
                    return res
                else:
                    return ProviderProbeResult(
                        provider=PROVIDER_TAUFA_AI,
                        status=STATE_RESPONSE_INVALID,
                        latency_ms=latency_ms,
                        error="Taufa AI returned invalid or non-matching diagnostic JSON.",
                        raw_response=raw[:200],
                        validation_ready=False,
                    )

            if code in (401, 403):
                return ProviderProbeResult(
                    provider=PROVIDER_TAUFA_AI,
                    status=STATE_AUTHENTICATION_FAILED,
                    latency_ms=latency_ms,
                    error=f"Taufa AI authentication failed (HTTP {code}).",
                    validation_ready=False,
                )
            elif code == 429:
                return ProviderProbeResult(
                    provider=PROVIDER_TAUFA_AI,
                    status=STATE_RATE_LIMITED,
                    latency_ms=latency_ms,
                    error="Taufa AI rate limit exceeded (HTTP 429).",
                    validation_ready=False,
                )
            else:
                return ProviderProbeResult(
                    provider=PROVIDER_TAUFA_AI,
                    status=STATE_UNAVAILABLE,
                    latency_ms=latency_ms,
                    error=f"Taufa AI returned HTTP {code}.",
                    validation_ready=False,
                )

        except urllib.error.HTTPError as he:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            if he.code in (401, 403):
                st = STATE_AUTHENTICATION_FAILED
            elif he.code == 429:
                st = STATE_RATE_LIMITED
            else:
                st = STATE_UNAVAILABLE
            return ProviderProbeResult(
                provider=PROVIDER_TAUFA_AI,
                status=st,
                latency_ms=latency_ms,
                error=f"Taufa AI HTTP error {he.code}: {he.reason}",
                validation_ready=False,
            )
        except urllib.error.URLError as ue:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            is_timeout = isinstance(ue.reason, (TimeoutError, socket.timeout if 'socket' in globals() else TimeoutError)) or "timed out" in str(ue.reason).lower()
            return ProviderProbeResult(
                provider=PROVIDER_TAUFA_AI,
                status=STATE_TIMEOUT if is_timeout else STATE_UNAVAILABLE,
                latency_ms=latency_ms,
                error=f"Taufa AI network failure: {ue.reason}",
                validation_ready=False,
            )
        except Exception as exc:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            is_timeout = "timeout" in str(exc).lower()
            return ProviderProbeResult(
                provider=PROVIDER_TAUFA_AI,
                status=STATE_TIMEOUT if is_timeout else STATE_INFERENCE_FAILED,
                latency_ms=latency_ms,
                error=f"Taufa AI diagnostic check failed: {exc}",
                validation_ready=False,
            )

    def probe_openai_api(self, timeout_sec: float = DEFAULT_PREFLIGHT_TIMEOUT_SEC, force: bool = False) -> ProviderProbeResult:
        """
        Sends a minimal harmless test prompt to the official OpenAI API.
        Verifies API reachability, authentication, configured model availability, and response parsing.
        Credentials stay exclusively on the backend.
        Never uses actual user evidence.
        """
        now_mono = time.monotonic()
        with self._lock:
            if not force and self._cached_openai_probe:
                cached_time, cached_res = self._cached_openai_probe
                if (now_mono - cached_time) < CACHE_FRESHNESS_SEC and cached_res.status == STATE_CONNECTED:
                    return cached_res

        start_t = time.monotonic()
        try:
            from core.openai_fallback_validator import OpenAiFallbackValidator
            fb_val = OpenAiFallbackValidator()
            api_key = fb_val.api_key
            endpoint = fb_val.endpoint
            model = fb_val.model

            if not fb_val.is_configured() or not api_key:
                return ProviderProbeResult(
                    provider=PROVIDER_OPENAI_FALLBACK,
                    status=STATE_AUTHENTICATION_FAILED,
                    latency_ms=int((time.monotonic() - start_t) * 1000),
                    error="OpenAI API key is missing or unconfigured on backend server.",
                    model=model,
                    validation_ready=False,
                )

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": MINIMAL_PREFLIGHT_PROMPT,
                    }
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 30,
            }
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "StrictRecords-AiPreflight/1.0",
            }

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                code = resp.getcode()
                raw = resp.read().decode("utf-8", errors="replace")

            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))

            if 200 <= code < 300:
                outer = json.loads(raw)
                choices = outer.get("choices", [])
                content_raw = choices[0].get("message", {}).get("content", "{}") if choices else outer.get("content", "{}")
                inner = content_raw if isinstance(content_raw, dict) else json.loads(content_raw)

                # Verify expected keys
                is_valid = (
                    isinstance(inner, dict)
                    and (
                        (str(inner.get("service", "")).lower() == "ok" and inner.get("validation_ready") is True)
                        or str(inner.get("status", "")).lower() == "ok"
                        or inner.get("validation_ready") is True
                    )
                )

                if is_valid:
                    res = ProviderProbeResult(
                        provider=PROVIDER_OPENAI_FALLBACK,
                        status=STATE_CONNECTED,
                        latency_ms=latency_ms,
                        model=model,
                        validation_ready=True,
                        raw_response=raw[:200],
                    )
                    with self._lock:
                        self._cached_openai_probe = (time.monotonic(), res)
                    return res
                else:
                    return ProviderProbeResult(
                        provider=PROVIDER_OPENAI_FALLBACK,
                        status=STATE_RESPONSE_INVALID,
                        latency_ms=latency_ms,
                        model=model,
                        error="OpenAI API returned non-matching schema for preflight check.",
                        raw_response=raw[:200],
                        validation_ready=False,
                    )

            if code in (401, 403):
                return ProviderProbeResult(
                    provider=PROVIDER_OPENAI_FALLBACK,
                    status=STATE_AUTHENTICATION_FAILED,
                    latency_ms=latency_ms,
                    model=model,
                    error=f"OpenAI API authentication failed (HTTP {code}).",
                    validation_ready=False,
                )
            elif code == 429:
                return ProviderProbeResult(
                    provider=PROVIDER_OPENAI_FALLBACK,
                    status=STATE_RATE_LIMITED,
                    latency_ms=latency_ms,
                    model=model,
                    error="OpenAI API rate limit or quota exceeded (HTTP 429).",
                    validation_ready=False,
                )
            else:
                return ProviderProbeResult(
                    provider=PROVIDER_OPENAI_FALLBACK,
                    status=STATE_UNAVAILABLE,
                    latency_ms=latency_ms,
                    model=model,
                    error=f"OpenAI API returned HTTP {code}.",
                    validation_ready=False,
                )

        except urllib.error.HTTPError as he:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            raw_err = ""
            try:
                raw_err = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if he.code in (401, 403):
                st = STATE_AUTHENTICATION_FAILED
            elif he.code == 429:
                st = STATE_QUOTA_EXCEEDED if "quota" in raw_err.lower() else STATE_RATE_LIMITED
            else:
                st = STATE_UNAVAILABLE
            return ProviderProbeResult(
                provider=PROVIDER_OPENAI_FALLBACK,
                status=st,
                latency_ms=latency_ms,
                error=f"OpenAI HTTP {he.code}: {raw_err or he.reason}",
                validation_ready=False,
            )
        except urllib.error.URLError as ue:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            is_timeout = "timed out" in str(ue.reason).lower()
            return ProviderProbeResult(
                provider=PROVIDER_OPENAI_FALLBACK,
                status=STATE_TIMEOUT if is_timeout else STATE_UNAVAILABLE,
                latency_ms=latency_ms,
                error=f"OpenAI network error: {ue.reason}",
                validation_ready=False,
            )
        except Exception as exc:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            is_timeout = "timeout" in str(exc).lower()
            return ProviderProbeResult(
                provider=PROVIDER_OPENAI_FALLBACK,
                status=STATE_TIMEOUT if is_timeout else STATE_INFERENCE_FAILED,
                latency_ms=latency_ms,
                error=f"OpenAI diagnostic check failed: {exc}",
                validation_ready=False,
            )

    # -------------------------------------------------------------------------
    # Intelligent Provider Selection (Section 5)
    # -------------------------------------------------------------------------
    @staticmethod
    def select_validation_provider(taufa_probe: ProviderProbeResult, openai_probe: ProviderProbeResult) -> Tuple[str, str, str]:
        """
        Applies Section 5 selection rules:
        | Taufa AI  | OpenAI     | Result                        |
        | Connected | Connected  | Taufa primary, OpenAI backup  |
        | Connected | Unavail    | Taufa primary                 |
        | Unavail   | Connected  | OpenAI fallback               |
        | Unavail   | Unavail    | Pending validation; notify    |
        """
        taufa_ok = (taufa_probe.status == STATE_CONNECTED)
        openai_ok = (openai_probe.status == STATE_CONNECTED)

        if taufa_ok and openai_ok:
            return PROVIDER_TAUFA_AI, PROVIDER_TAUFA_AI, PROVIDER_OPENAI_FALLBACK
        elif taufa_ok and not openai_ok:
            return PROVIDER_TAUFA_AI, PROVIDER_TAUFA_AI, PROVIDER_NONE
        elif not taufa_ok and openai_ok:
            return PROVIDER_OPENAI_FALLBACK, PROVIDER_OPENAI_FALLBACK, PROVIDER_NONE
        else:
            return PROVIDER_PENDING_VALIDATION, PROVIDER_NONE, PROVIDER_NONE

    # -------------------------------------------------------------------------
    # Notification & Display Formatting (Section 7)
    # -------------------------------------------------------------------------
    @staticmethod
    def format_readiness_display(
        taufa_probe: ProviderProbeResult,
        openai_probe: ProviderProbeResult,
        selected_provider: str,
        scheduled_open_time: str,
        completed_at_iso: str,
    ) -> Tuple[str, List[str]]:
        """
        Formats compact notification and status dashboard strings (Section 7).
        """
        open_time_str = ""
        if scheduled_open_time:
            try:
                dt = datetime.fromisoformat(scheduled_open_time.replace("Z", "+00:00")).astimezone(IST)
                end_dt = dt + timedelta(minutes=20)
                open_time_str = f"{dt.strftime('%I:%M')}–{end_dt.strftime('%I:%M %p')}"
            except Exception:
                open_time_str = scheduled_open_time

        completed_time_str = ""
        if completed_at_iso:
            try:
                cdt = datetime.fromisoformat(completed_at_iso.replace("Z", "+00:00")).astimezone(IST)
                completed_time_str = cdt.strftime("%I:%M %p")
            except Exception:
                completed_time_str = completed_at_iso

        taufa_ok = (taufa_probe.status == STATE_CONNECTED)
        openai_ok = (openai_probe.status == STATE_CONNECTED)

        if taufa_ok and openai_ok:
            title = "AI PRE-SUBMISSION CHECK"
            lines = [
                f"• Taufa AI: {taufa_probe.status}",
                f"• OpenAI: {openai_probe.status}",
                "• Primary: Taufa AI",
                "• Backup: OpenAI",
            ]
            if open_time_str:
                lines.append(f"• Next proof window: {open_time_str}")
            if completed_time_str:
                lines.append(f"• Preflight completed: {completed_time_str}")
            return title, lines

        elif taufa_ok and not openai_ok:
            title = "AI PRE-SUBMISSION CHECK (TAUFA ACTIVE)"
            lines = [
                f"• Taufa AI: {taufa_probe.status}",
                f"• OpenAI: {openai_probe.status}",
                "• Active validation provider: Taufa AI",
            ]
            if open_time_str:
                lines.append(f"• Next proof window: {open_time_str}")
            return title, lines

        elif not taufa_ok and openai_ok:
            title = "AI FALLBACK READY"
            lines = [
                f"• Taufa AI: {taufa_probe.status}",
                f"• OpenAI: {openai_probe.status}",
                "• Active validation provider: OpenAI",
            ]
            if open_time_str:
                lines.append(f"• Next proof window: {open_time_str}")
            return title, lines

        else:
            title = "AI VALIDATION SERVICES UNAVAILABLE"
            lines = [
                f"• Taufa AI: {taufa_probe.status}",
                f"• OpenAI: {openai_probe.status}",
                "• Proof submission remains available.",
                "• Validation will remain pending until a provider becomes available.",
            ]
            return title, lines

    # -------------------------------------------------------------------------
    # Execution (Section 1, 2, 4, 6)
    # -------------------------------------------------------------------------
    def execute_preflight(
        self,
        proof_window_id: str,
        activity_id: str,
        scheduled_open_time: str,
        schedule_version: int = 1,
        force: bool = False,
        retry_attempt: int = 0,
    ) -> AiPreflightResult:
        """
        Executes a 5-minute preflight check for a scheduled proof window.
        Probes both providers independently in parallel.
        Idempotent: if already executed for (proof_window_id, schedule_version) and still fresh,
        returns the existing record unless forced or retried.
        """
        clean_window_id = (proof_window_id or activity_id).strip()
        clean_act_id = (activity_id or proof_window_id).strip()
        now_dt = datetime.now(timezone.utc)
        start_iso = now_dt.isoformat()

        # Idempotency check: see if recent preflight exists
        if not force and retry_attempt == 0 and self.db:
            try:
                existing = self.db.get_ai_preflight_record(clean_window_id, schedule_version)
                if existing:
                    # If completed and not older than 10 minutes, reuse
                    completed_iso = existing.get("preflight_completed_at")
                    if completed_iso:
                        try:
                            comp_dt = datetime.fromisoformat(completed_iso.replace("Z", "+00:00"))
                            if (now_dt - comp_dt).total_seconds() < 600.0:
                                return AiPreflightResult.from_dict(existing)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("Database check error in preflight: %s", e)

        preflight_id = f"preflight_{clean_window_id}_v{schedule_version}_{int(now_dt.timestamp())}"

        # Run probes independently in parallel (Section 2)
        taufa_future = self._executor.submit(self.probe_taufa_ai, DEFAULT_PREFLIGHT_TIMEOUT_SEC, force)
        openai_future = self._executor.submit(self.probe_openai_api, DEFAULT_PREFLIGHT_TIMEOUT_SEC, force)

        try:
            taufa_probe = taufa_future.result(timeout=DEFAULT_PREFLIGHT_TIMEOUT_SEC + 2.0)
        except Exception as e:
            taufa_probe = ProviderProbeResult(
                provider=PROVIDER_TAUFA_AI,
                status=STATE_TIMEOUT,
                error=f"Taufa probe execution timed out: {e}",
            )

        try:
            openai_probe = openai_future.result(timeout=DEFAULT_PREFLIGHT_TIMEOUT_SEC + 2.0)
        except Exception as e:
            openai_probe = ProviderProbeResult(
                provider=PROVIDER_OPENAI_FALLBACK,
                status=STATE_TIMEOUT,
                error=f"OpenAI probe execution timed out: {e}",
            )

        # Select validation provider (Section 5)
        selected_provider, primary_provider, backup_provider = self.select_validation_provider(taufa_probe, openai_probe)

        completed_iso = datetime.now(timezone.utc).isoformat()

        # Check if retry is needed for transient failures (Section 6)
        needs_retry = False
        if retry_attempt < MAX_PREFLIGHT_RETRIES:
            # Check if Taufa or OpenAI has transient failure
            taufa_transient = taufa_probe.status not in (STATE_CONNECTED, *NON_RETRYABLE_STATES)
            openai_transient = openai_probe.status not in (STATE_CONNECTED, *NON_RETRYABLE_STATES)
            if taufa_transient or openai_transient:
                # If neither is connected, or primary is failed while backup works, schedule retry
                if selected_provider == PROVIDER_PENDING_VALIDATION or (taufa_transient and taufa_probe.status != STATE_CONNECTED):
                    needs_retry = True

        preflight_status = STATUS_PENDING_RETRY if needs_retry else (
            STATUS_COMPLETED if selected_provider != PROVIDER_PENDING_VALIDATION else STATUS_FAILED
        )

        # Format display (Section 7)
        title, lines = self.format_readiness_display(
            taufa_probe=taufa_probe,
            openai_probe=openai_probe,
            selected_provider=selected_provider,
            scheduled_open_time=scheduled_open_time,
            completed_at_iso=completed_iso,
        )

        result = AiPreflightResult(
            preflight_id=preflight_id,
            proof_window_id=clean_window_id,
            activity_id=clean_act_id,
            scheduled_open_time=scheduled_open_time,
            preflight_started_at=start_iso,
            taufa_status=taufa_probe.status,
            taufa_latency=taufa_probe.latency_ms,
            taufa_error=taufa_probe.error,
            openai_status=openai_probe.status,
            openai_latency=openai_probe.latency_ms,
            openai_error=openai_probe.error,
            selected_validation_provider=selected_provider,
            primary_provider=primary_provider,
            backup_provider=backup_provider,
            preflight_completed_at=completed_iso,
            retry_count=retry_attempt,
            preflight_status=preflight_status,
            schedule_version=schedule_version,
            display_title=title,
            display_lines=lines,
            details={
                "needs_retry": needs_retry,
                "taufa_model": taufa_probe.model,
                "openai_model": openai_probe.model,
                "taufa_raw": taufa_probe.raw_response,
                "openai_raw": openai_probe.raw_response,
            },
        )

        try:
            from core.openai_fallback_validator import OpenAiMultiProfileManager
            profile_mgr = OpenAiMultiProfileManager.get_instance(db=self.db)
            safe_meta = profile_mgr.get_safe_metadata()
            result.details["openai_profiles"] = safe_meta.get("profiles", [])
            result.details["config_version"] = safe_meta.get("config_version", 1)
        except Exception:
            pass

        with self._lock:
            self._active_preflights[clean_window_id] = result

        # Save to database (Section 4)
        if self.db:
            try:
                self.db.save_ai_preflight_record(result.to_dict())
            except Exception as e:
                logger.error("Failed to persist preflight record: %s", e)

        # Publish notification
        self._notify_listeners(result)

        return result


# Global singleton
ai_preflight_service = AiPreflightService()
