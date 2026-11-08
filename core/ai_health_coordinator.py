"""
StrictRecords — Authoritative AI Service Health Coordinator (Part A, Sections 1–6)
===================================================================================
Maintains a persistent AI service health record and performs a lightweight real
health-check request approximately once every 60 minutes (3,600,000 ms).

- Primary Provider: Taufa AI
- Fallback Provider: Official OpenAI API (OpenAiFallbackValidator)
- Never issues a new AI test request on window/screen open if cached record is < 60m old.
- Coordinates evidence validation across Taufa AI (primary) and OpenAI API (fallback).
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.database import get_connection
from core.taufa_ai_validator import TaufaAiValidator, TaufaValidationResult
from core.openai_fallback_validator import OpenAiFallbackValidator
from core.taufa_access_policy import evaluate_taufa_access_policy

logger = logging.getLogger("StrictRecords.AiHealthCoordinator")

IST = timezone(timedelta(hours=5, minutes=30))


class AiHealthCoordinator:
    """
    Singleton coordinator for persistent 60-minute AI health monitoring
    and automatic Taufa AI -> Official OpenAI API failover.
    """

    HEALTH_CHECK_INTERVAL_SECONDS = 3600  # 60 minutes
    HEALTH_CHECK_INTERVAL_MS = 3_600_000
    SETTINGS_KEY = "ai_service_health_record_v1"
    MINIMAL_HEALTH_PROMPT = 'Return the JSON object {"status":"ok"}.'

    # Standardized UI status strings (Section 4)
    UI_STATUS_CHECKING = "AI STATUS: CHECKING"
    UI_STATUS_CONNECTED_TAUFA = "AI STATUS: CONNECTED – TAUFA AI"
    UI_STATUS_CONNECTED_OPENAI = "AI STATUS: CONNECTED – OPENAI FALLBACK"
    UI_STATUS_DEGRADED = "AI STATUS: DEGRADED"
    UI_STATUS_UNAVAILABLE = "AI STATUS: UNAVAILABLE"
    UI_STATUS_OUTDATED = "AI STATUS: LAST CHECK OUTDATED"

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(AiHealthCoordinator, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        self._check_in_progress = False
        self._cached_record: Optional[Dict[str, Any]] = None
        self._initialized = True
        self._load_persisted_record()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(IST).isoformat()

    @staticmethod
    def _ms_to_iso(ms: int) -> str:
        if ms <= 0:
            return ""
        return datetime.fromtimestamp(ms / 1000.0, tz=IST).isoformat()

    def _default_record(self) -> Dict[str, Any]:
        return {
            "last_health_check": "",
            "last_health_check_ms": 0,
            "next_health_check": "",
            "next_health_check_ms": 0,
            "check_interval_ms": self.HEALTH_CHECK_INTERVAL_MS,
            "taufa_status": "NOT_CHECKED",
            "openai_status": "NOT_CHECKED",
            "active_provider": "UNAVAILABLE",
            "last_successful_provider": "",
            "response_latency": 0,
            "last_error": "",
            "health_check_request_id": "",
            "ui_status": self.UI_STATUS_OUTDATED,
            "is_checking": False,
            "check_count": 0,
        }

    def _load_persisted_record(self) -> Dict[str, Any]:
        with self._lock:
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)"
                )
                cur.execute(
                    "SELECT value FROM system_settings WHERE key = ?",
                    (self.SETTINGS_KEY,),
                )
                row = cur.fetchone()
                conn.close()
                if row and row["value"]:
                    parsed = json.loads(row["value"])
                    if isinstance(parsed, dict):
                        base = self._default_record()
                        base.update(parsed)
                        self._cached_record = base
                        return dict(self._cached_record)
            except Exception as exc:
                logger.warning("Failed to load persisted AI health record: %s", exc)
            if self._cached_record is None:
                self._cached_record = self._default_record()
            return dict(self._cached_record)

    def _persist_record(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._cached_record = dict(record)
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)"
                )
                cur.execute(
                    "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
                    (self.SETTINGS_KEY, json.dumps(record)),
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.warning("Failed to persist AI health record: %s", exc)

    def is_record_stale(self, record: Optional[Dict[str, Any]] = None, now_ms: Optional[int] = None) -> bool:
        rec = record or self._cached_record or self._load_persisted_record()
        current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        last_ms = int(rec.get("last_health_check_ms") or 0)
        if last_ms <= 0:
            return True
        return (current_ms - last_ms) >= self.HEALTH_CHECK_INTERVAL_MS

    def get_health_status(self, refresh_if_stale: bool = True) -> Dict[str, Any]:
        """
        Returns the authoritative persistent AI health record.
        Never runs a new AI test request if the cached record was checked within the last 60 minutes.
        """
        with self._lock:
            rec = dict(self._cached_record or self._load_persisted_record())
            now_ms = int(time.time() * 1000)
            stale = self.is_record_stale(rec, now_ms)

        if stale and refresh_if_stale:
            return self.run_health_check(force=False, reason="opportunistic_stale_refresh")

        if stale and not refresh_if_stale:
            rec["ui_status"] = self.UI_STATUS_OUTDATED
        rec["is_stale"] = stale
        rec["age_ms"] = max(0, now_ms - int(rec.get("last_health_check_ms") or 0)) if rec.get("last_health_check_ms") else -1
        try:
            from core.openai_fallback_validator import OpenAiMultiProfileManager
            profile_mgr = OpenAiMultiProfileManager.get_instance()
            safe_meta = profile_mgr.get_safe_metadata()
            rec["config_version"] = safe_meta.get("config_version", 1)
            rec["profiles"] = safe_meta.get("profiles", [])
        except Exception:
            pass
        return rec

    def _probe_taufa_ai(self, request_id: str, timeout_sec: float = 8.0) -> Dict[str, Any]:
        """
        Sends a lightweight real health-check probe to Taufa AI.
        """
        start_t = time.monotonic()
        validator = TaufaAiValidator()
        endpoint = validator.get_endpoint()
        api_key = validator.get_api_key()
        taufa_policy = evaluate_taufa_access_policy(validator)

        if not endpoint or not api_key:
            latency_ms = int((time.monotonic() - start_t) * 1000)
            if taufa_policy.get("taufa_ready"):
                return {
                    "ok": True,
                    "status": "CONNECTED",
                    "latency_ms": max(1, latency_ms),
                    "error": "",
                }
            return {
                "ok": False,
                "status": "UNCONFIGURED",
                "latency_ms": latency_ms,
                "error": taufa_policy.get("message") or "Taufa AI endpoint or session credentials not configured.",
            }

        conv_id = TaufaAiValidator.DESIGNATED_CONVERSATION_ID
        payload = {
            "conversationId": conv_id,
            "id": conv_id,
            "health_check_request_id": request_id,
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": self.MINIMAL_HEALTH_PROMPT,
                }
            ],
        }
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {api_key}",
            "X-Conversation-Id": conv_id,
            "X-Health-Check-Id": request_id,
            "User-Agent": "StrictRecords-AiHealthCoordinator/1.0",
        }
        if "=" in api_key or ";" in api_key:
            headers["Cookie"] = api_key

        try:
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
                try:
                    from core.taufa_access_policy import mark_verified
                    mark_verified(endpoint, api_key)
                except Exception:
                    pass
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        return {
                            "ok": True,
                            "status": "CONNECTED",
                            "latency_ms": latency_ms,
                            "error": "",
                        }
                except Exception:
                    if '"status"' in raw or '"ok"' in raw or '"score"' in raw:
                        return {
                            "ok": True,
                            "status": "CONNECTED",
                            "latency_ms": latency_ms,
                            "error": "",
                        }
                return {
                    "ok": False,
                    "status": "DEGRADED",
                    "latency_ms": latency_ms,
                    "error": "Taufa AI returned non-JSON health response.",
                }
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "latency_ms": latency_ms,
                "error": f"Taufa AI HTTP {code}",
            }
        except Exception as exc:
            latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "latency_ms": latency_ms,
                "error": f"Taufa AI unreachable: {exc}",
            }

    def _probe_openai_fallback(self, request_id: str, timeout_sec: float = 10.0) -> Dict[str, Any]:
        """
        Sends a lightweight real minimal health-check prompt to Official OpenAI API:
        `Return the JSON object {"status":"ok"}.`
        Checks OpenAiMultiProfileManager enabled configured profiles in priority order first,
        then falls back to default OpenAiFallbackValidator.
        """
        start_t = time.monotonic()
        fb_validator = OpenAiFallbackValidator()
        candidates = []
        if fb_validator.is_configured() and fb_validator.api_key:
            candidates.append((fb_validator.api_key, fb_validator.endpoint, fb_validator.model, None))
        try:
            from core.openai_fallback_validator import OpenAiMultiProfileManager
            mgr = OpenAiMultiProfileManager.get_instance()
            for prof in mgr.get_enabled_configured_profiles():
                if prof.api_key and not any(c[0] == prof.api_key for c in candidates):
                    candidates.append((prof.api_key, prof.endpoint, prof.model, prof))
        except Exception:
            pass

        if not candidates:
            latency_ms = int((time.monotonic() - start_t) * 1000)
            return {
                "ok": False,
                "status": "UNCONFIGURED",
                "latency_ms": latency_ms,
                "error": "OPENAI_API_KEY is not configured on the backend server.",
            }

        last_err = "OpenAI API unreachable"
        last_status = "UNAVAILABLE"
        for api_key, endpoint, model, prof_obj in candidates:
            t_prof = time.monotonic()
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": self.MINIMAL_HEALTH_PROMPT,
                    }
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 25,
            }
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Bearer {api_key}",
                "X-Health-Check-Id": request_id,
            }

            try:
                req = urllib.request.Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    code = resp.getcode()
                    raw = resp.read().decode("utf-8", errors="replace")
                latency_ms = max(1, int((time.monotonic() - t_prof) * 1000))
                if 200 <= code < 300:
                    outer = json.loads(raw)
                    choices = outer.get("choices", [])
                    content = choices[0].get("message", {}).get("content", "{}") if choices else outer.get("content", "{}")
                    if isinstance(content, dict):
                        inner = content
                    else:
                        inner = json.loads(content)
                    if isinstance(inner, dict) and str(inner.get("status", "")).lower() == "ok":
                        if prof_obj is not None:
                            prof_obj.last_status = "CONNECTED"
                            prof_obj.last_error = ""
                            prof_obj.response_latency_ms = latency_ms
                            prof_obj.last_checked = self._now_iso()
                        return {
                            "ok": True,
                            "status": "CONNECTED",
                            "latency_ms": latency_ms,
                            "error": "",
                        }
                last_status = "DEGRADED"
                last_err = f"OpenAI API returned unexpected payload (HTTP {code})."
            except Exception as exc:
                last_status = "UNAVAILABLE"
                last_err = f"OpenAI API check failed: {exc}"
                if prof_obj is not None:
                    prof_obj.last_status = "UNAVAILABLE"
                    prof_obj.last_error = str(exc)
                    prof_obj.last_checked = self._now_iso()

        latency_ms = max(1, int((time.monotonic() - start_t) * 1000))
        return {
            "ok": False,
            "status": last_status,
            "latency_ms": latency_ms,
            "error": last_err,
        }

    def run_health_check(self, force: bool = False, reason: str = "scheduled_60m") -> Dict[str, Any]:
        """
        Executes a lightweight real health check if force=True or if the cached record
        is older than 60 minutes.
        Tests Taufa AI first; if unavailable, tests Official OpenAI API fallback.
        """
        with self._lock:
            existing = dict(self._cached_record or self._load_persisted_record())
            now_ms = int(time.time() * 1000)
            if not force and not self.is_record_stale(existing, now_ms):
                existing["is_stale"] = False
                existing["age_ms"] = max(0, now_ms - int(existing.get("last_health_check_ms") or 0))
                existing["skipped_duplicate_check"] = True
                return existing

            request_id = f"ai_hc_{uuid.uuid4().hex[:16]}"
            self._check_in_progress = True

        try:
            # 1. Test Primary Provider: Taufa AI
            taufa_res = self._probe_taufa_ai(request_id=request_id)
            taufa_ok = bool(taufa_res.get("ok"))
            taufa_status = str(taufa_res.get("status") or "UNAVAILABLE")
            latency_ms = int(taufa_res.get("latency_ms") or 0)
            last_error = str(taufa_res.get("error") or "")

            openai_status = existing.get("openai_status") or "STANDBY"
            active_provider = "UNAVAILABLE"
            last_successful_provider = existing.get("last_successful_provider") or ""
            ui_status = self.UI_STATUS_UNAVAILABLE

            if taufa_ok:
                active_provider = "TAUFA_AI"
                last_successful_provider = "TAUFA_AI"
                ui_status = self.UI_STATUS_CONNECTED_TAUFA
                last_error = ""
                if OpenAiFallbackValidator().is_configured():
                    openai_status = "STANDBY"
                else:
                    openai_status = "UNCONFIGURED"
            else:
                # 2. Taufa AI unavailable -> Test Fallback Provider: Official OpenAI API
                openai_res = self._probe_openai_fallback(request_id=request_id)
                openai_ok = bool(openai_res.get("ok"))
                openai_status = str(openai_res.get("status") or "UNAVAILABLE")
                if openai_ok:
                    active_provider = "OPENAI_FALLBACK"
                    last_successful_provider = "OPENAI_FALLBACK"
                    ui_status = self.UI_STATUS_CONNECTED_OPENAI
                    latency_ms = int(openai_res.get("latency_ms") or latency_ms)
                    last_error = f"Primary (Taufa AI) unavailable ({taufa_res.get('error')}); using OpenAI Fallback."
                else:
                    active_provider = "UNAVAILABLE"
                    ui_status = (
                        self.UI_STATUS_DEGRADED
                        if (taufa_status == "DEGRADED" or openai_status == "DEGRADED")
                        else self.UI_STATUS_UNAVAILABLE
                    )
                    err_parts = [p for p in [taufa_res.get("error"), openai_res.get("error")] if p]
                    last_error = " | ".join(err_parts) if err_parts else "Both Taufa AI and OpenAI Fallback are unavailable."

            completed_ms = int(time.time() * 1000)
            next_ms = completed_ms + self.HEALTH_CHECK_INTERVAL_MS
            updated_record = {
                "last_health_check": self._ms_to_iso(completed_ms),
                "last_health_check_ms": completed_ms,
                "next_health_check": self._ms_to_iso(next_ms),
                "next_health_check_ms": next_ms,
                "check_interval_ms": self.HEALTH_CHECK_INTERVAL_MS,
                "taufa_status": taufa_status,
                "openai_status": openai_status,
                "active_provider": active_provider,
                "last_successful_provider": last_successful_provider,
                "response_latency": latency_ms,
                "last_error": last_error,
                "health_check_request_id": request_id,
                "ui_status": ui_status,
                "is_checking": False,
                "is_stale": False,
                "age_ms": 0,
                "last_check_reason": reason,
                "check_count": int(existing.get("check_count") or 0) + 1,
            }
            try:
                from core.openai_fallback_validator import OpenAiMultiProfileManager
                profile_mgr = OpenAiMultiProfileManager.get_instance()
                safe_meta = profile_mgr.get_safe_metadata()
                updated_record["config_version"] = safe_meta.get("config_version", 1)
                updated_record["profiles"] = safe_meta.get("profiles", [])
            except Exception:
                pass
            self._persist_record(updated_record)
            logger.info(
                "AI Health Check [%s] completed: active_provider=%s, taufa=%s, openai=%s, latency=%dms",
                request_id,
                active_provider,
                taufa_status,
                openai_status,
                latency_ms,
            )
            return updated_record
        finally:
            with self._lock:
                self._check_in_progress = False

    def record_runtime_provider_outcome(
        self,
        provider: str,
        success: bool,
        latency_ms: int = 0,
        error_msg: str = "",
    ) -> Dict[str, Any]:
        """
        Updates the persistent health record when a real proof validation call succeeds
        or fails, avoiding unnecessary extra health check probes.
        """
        with self._lock:
            rec = dict(self._cached_record or self._load_persisted_record())
            now_ms = int(time.time() * 1000)
            if provider == "TAUFA_AI":
                if success:
                    rec["taufa_status"] = "CONNECTED"
                    rec["active_provider"] = "TAUFA_AI"
                    rec["last_successful_provider"] = "TAUFA_AI"
                    rec["ui_status"] = self.UI_STATUS_CONNECTED_TAUFA
                    rec["last_error"] = ""
                    rec["last_health_check_ms"] = now_ms
                    rec["last_health_check"] = self._ms_to_iso(now_ms)
                    rec["next_health_check_ms"] = now_ms + self.HEALTH_CHECK_INTERVAL_MS
                    rec["next_health_check"] = self._ms_to_iso(now_ms + self.HEALTH_CHECK_INTERVAL_MS)
                else:
                    rec["taufa_status"] = "UNAVAILABLE"
                    if error_msg:
                        rec["last_error"] = error_msg
            elif provider == "OPENAI_FALLBACK":
                if success:
                    rec["openai_status"] = "CONNECTED"
                    rec["active_provider"] = "OPENAI_FALLBACK"
                    rec["last_successful_provider"] = "OPENAI_FALLBACK"
                    rec["ui_status"] = self.UI_STATUS_CONNECTED_OPENAI
                    rec["last_health_check_ms"] = now_ms
                    rec["last_health_check"] = self._ms_to_iso(now_ms)
                    rec["next_health_check_ms"] = now_ms + self.HEALTH_CHECK_INTERVAL_MS
                    rec["next_health_check"] = self._ms_to_iso(now_ms + self.HEALTH_CHECK_INTERVAL_MS)
                else:
                    rec["openai_status"] = "UNAVAILABLE"
                    if error_msg:
                        rec["last_error"] = error_msg
            if latency_ms > 0:
                rec["response_latency"] = latency_ms
            self._persist_record(rec)
            return rec

    def validate_hourly_submission_with_fallback(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        schedule_context: str = "",
        user_id: str = "user_default",
        historical_sha256s: Optional[Dict[str, str]] = None,
        task_type: str = "HOURLY",
        evaluation_id: Optional[str] = None,
        filenames: Optional[List[str]] = None,
    ) -> TaufaValidationResult:
        """
        Section 6: Shared validation provider selection.
        1. Checks latest provider health state (refreshing only if stale > 60 min).
        2. Uses Taufa AI if available (or attempts Taufa AI first when configured).
        3. Automatically falls back to Official OpenAI API (`OpenAiFallbackValidator`) if Taufa AI
           is unavailable, unconfigured, or fails with a retryable/network error.
        4. If both fail, keeps submission in `FAILED_RETRYABLE` (`Pending Validation`) without
           awarding or deducting points.
        """
        health = self.get_health_status(refresh_if_stale=True)
        active_provider = health.get("active_provider", "UNAVAILABLE")
        validator = TaufaAiValidator()
        fb_validator = OpenAiFallbackValidator()

        try_taufa = (
            active_provider == "TAUFA_AI"
            or validator.is_configured()
        )

        taufa_res: Optional[TaufaValidationResult] = None
        if try_taufa:
            t0 = time.monotonic()
            taufa_res = validator.validate_hourly_submission(
                submission_id=submission_id,
                category=category,
                description=description,
                images=images,
                task_type=task_type,
                metadata={"schedule_context": schedule_context, "user_id": user_id},
                image_filenames=filenames,
            )
            lat = max(1, int((time.monotonic() - t0) * 1000))
            if taufa_res.validator_status not in ("UNCONFIGURED", "FAILED_RETRYABLE", "DISABLED"):
                provider_used = taufa_res.validation_provider or "TAUFA_AI"
                self.record_runtime_provider_outcome(provider_used, True, latency_ms=lat)
                return taufa_res
            else:
                self.record_runtime_provider_outcome(
                    "TAUFA_AI",
                    False,
                    latency_ms=lat,
                    error_msg=taufa_res.reason,
                )

        # Fallback to Official OpenAI API
        if fb_validator.is_configured():
            t1 = time.monotonic()
            fb_res = validator._try_openai_fallback(
                submission_id=submission_id,
                category=category,
                description=description,
                images=images,
                image_filenames=filenames,
            )
            lat = max(1, int((time.monotonic() - t1) * 1000))
            if fb_res is not None and fb_res.validator_status not in ("UNCONFIGURED", "FAILED_RETRYABLE", "DISABLED"):
                fb_res.validation_provider = "OPENAI_FALLBACK"
                fb_res.validator_id = "OPENAI_FALLBACK"
                self.record_runtime_provider_outcome("OPENAI_FALLBACK", True, latency_ms=lat)
                return fb_res
            else:
                self.record_runtime_provider_outcome(
                    "OPENAI_FALLBACK",
                    False,
                    latency_ms=lat,
                    error_msg=(fb_res.reason if fb_res else "OpenAI fallback validation failed"),
                )
                if fb_res is not None:
                    return fb_res

        if taufa_res is not None:
            taufa_res.validator_status = "FAILED_RETRYABLE"
            taufa_res.review_status = "RETRY_REQUIRED"
            return taufa_res

        return TaufaValidationResult(
            submission_id=submission_id,
            score=None,
            validator_status="FAILED_RETRYABLE",
            review_status="RETRY_REQUIRED",
            reason="AI validation providers (Taufa AI primary and OpenAI fallback) are currently unavailable. Submission remains Pending Validation.",
            warnings=["AI_PROVIDERS_UNAVAILABLE", "PENDING_VALIDATION_RETRY"],
        )


ai_health_coordinator = AiHealthCoordinator()
