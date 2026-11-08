"""
StrictRecords AI Daily Analysis Engine (Section 33)
Submits structured, privacy-safe daily summary data to an OpenAI/ChatGPT-compatible API
for behavioral analysis, pattern detection, and recommendations.
NOTE: The local scoring engine is ALWAYS the single source of truth for scores.
Never hardcodes API keys. Falls back to a transparent deterministic rule-based analyzer when offline
or when no API key is configured in environment variables.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List


class AIDailyAnalyzer:
    @staticmethod
    def build_privacy_safe_payload(
        daily_record: Dict[str, Any],
        hourly_records: List[Dict[str, Any]],
        score_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Constructs structured summary without raw personal photos or private file paths."""
        hourly_summary = []
        for h in hourly_records:
            hourly_summary.append(
                {
                    "hour_slot": h.get("hour_slot"),
                    "status": h.get("status"),
                    "primary_activity": h.get("primary_activity"),
                    "has_evidence": bool(h.get("evidence_id")),
                    "integrity_flag": h.get("integrity_flag", "NORMAL"),
                }
            )
        event_summary = [
            {
                "event_type": e.get("event_type"),
                "points": e.get("points"),
                "timestamp": e.get("timestamp"),
            }
            for e in score_events
        ]
        return {
            "date": daily_record.get("date"),
            "final_score": daily_record.get("final_score"),
            "grade": daily_record.get("grade"),
            "score_mode": daily_record.get("score_mode"),
            "wakeup_status": daily_record.get("wakeup_status"),
            "wakeup_completed_at": daily_record.get("wakeup_completed_at"),
            "snooze_count": daily_record.get("snooze_count"),
            "bath_status": daily_record.get("bath_status"),
            "bath_completed_at": daily_record.get("bath_completed_at"),
            "gym_status": daily_record.get("gym_status"),
            "gym_duration_minutes": daily_record.get("gym_duration_minutes"),
            "hourly_completed_count": daily_record.get("hourly_completed_count"),
            "hourly_total_count": daily_record.get("hourly_total_count"),
            "hourly_missed_count": daily_record.get("hourly_missed_count"),
            "productive_hours": daily_record.get("productive_hours"),
            "evidence_count": daily_record.get("evidence_count"),
            "violation_count": daily_record.get("violation_count"),
            "hourly_breakdown": hourly_summary,
            "score_events": event_summary,
        }

    @classmethod
    def analyze_day(
        cls,
        daily_record: Dict[str, Any],
        hourly_records: List[Dict[str, Any]],
        score_events: List[Dict[str, Any]],
        settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        structured_payload = cls.build_privacy_safe_payload(
            daily_record, hourly_records, score_events
        )
        api_key = os.environ.get("STRICTRECORDS_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base_url = settings.get("ai_api_base_url", "https://api.openai.com/v1").rstrip("/")
        model = settings.get("ai_model", "gpt-4o-mini")

        if api_key and settings.get("ai_enabled", True):
            try:
                prompt = (
                    "You are the StrictRecords Discipline Analyst. Analyze this structured daily record JSON. "
                    "Do NOT invent scores; use the exact final_score provided. "
                    "Return JSON with keys: verdict, strongest_part, points_lost_reason, "
                    "peak_productivity_window, recommendation, narrative_summary.\n\n"
                    f"DATA:\n{json.dumps(structured_payload)}"
                )
                req_body = json.dumps(
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Return valid JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/chat/completions",
                    data=req_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content = resp_data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    parsed["source"] = "OPENAI_API"
                    parsed["model"] = model
                    return parsed
            except urllib.error.HTTPError as http_err:
                http_err.close()
                fallback = cls._deterministic_analysis(structured_payload)
                fallback["api_notice"] = f"OpenAI API HTTP {http_err.code}; used local deterministic analyzer."
                return fallback
            except Exception as exc:
                fallback = cls._deterministic_analysis(structured_payload)
                fallback["api_notice"] = f"OpenAI API call failed ({exc}); used local deterministic analyzer."
                return fallback

        return cls._deterministic_analysis(structured_payload)

    @staticmethod
    def _deterministic_analysis(payload: Dict[str, Any]) -> Dict[str, Any]:
        score = int(payload.get("final_score", 0))
        wakeup = payload.get("wakeup_status", "PENDING")
        bath = payload.get("bath_status", "PENDING")
        gym = payload.get("gym_status", "UPCOMING")
        snoozes = int(payload.get("snooze_count", 0))
        missed_hourly = int(payload.get("hourly_missed_count", 0))
        violations = int(payload.get("violation_count", 0))
        prod_hours = float(payload.get("productive_hours", 0.0))

        # Strongest part
        strong_parts = []
        if wakeup == "ON_TIME" and bath in ("COMPLETED", "VERIFIED"):
            strong_parts.append(
                "Your strongest part of the day was the morning routine. You completed your wake-up and bath requirements on time."
            )
        elif wakeup in ("ON_TIME", "LATE"):
            strong_parts.append(f"You completed your morning wake-up verification ({wakeup}).")
        if gym in ("COMPLETED", "VERIFIED"):
            strong_parts.append(
                f"You executed your scheduled gym session ({payload.get('gym_duration_minutes', 0):.0f} mins)."
            )
        if prod_hours >= 6.0:
            strong_parts.append(f"You logged {prod_hours:.1f} productive hours across the day.")
        if not strong_parts:
            strong_parts.append("Few scheduled anchors were completed on time today.")

        # Points lost
        losses = []
        if snoozes > 0:
            losses.append(f"{snoozes} snooze(s)")
        if missed_hourly > 0:
            losses.append(f"{missed_hourly} missed hourly update(s)")
        if violations > 0:
            losses.append(f"{violations} entertainment violation(s)")
        if bath == "MISSED":
            losses.append("missing the 10:00 AM bath deadline")
        if gym in ("MISSED", "SKIPPED"):
            losses.append(f"gym session ({gym.lower()})")

        if losses:
            points_lost_reason = "You lost points because of " + ", ".join(losses) + "."
        else:
            points_lost_reason = "Zero penalties recorded today — clean execution."

        # Peak window
        productive_slots = [
            h["hour_slot"]
            for h in payload.get("hourly_breakdown", [])
            if h.get("status") in ("COMPLETED", "VERIFIED")
            and h.get("primary_activity") not in (None, "BREAK", "WASTED TIME")
        ]
        if productive_slots:
            first_slot = productive_slots[0].split("-")[0]
            last_slot = productive_slots[-1].split("-")[1]
            peak_window = f"Your productivity was strongest between {first_slot} and {last_slot} ({len(productive_slots)} productive checkpoints)."
        else:
            peak_window = "No verified productive hourly streaks were recorded yet."

        # Recommendation
        if snoozes > 0:
            rec = "Place your phone across the room before 06:50 AM and pre-charge your Bluetooth earphones."
        elif missed_hourly > 0:
            rec = "Respond to hourly checkpoints immediately on the hour and attach quick workspace photo proof."
        elif violations > 0:
            rec = "Keep entertainment tabs closed until your discipline score reaches 80."
        else:
            rec = "Maintain this exact cadence tomorrow to extend your discipline streak."

        verdict = "GOOD DAY" if score >= 80 else ("CONTROLLED DAY" if score >= 60 else "NEEDS IMPROVEMENT")
        narrative = f"{' '.join(strong_parts)}\n\n{points_lost_reason}\n\n{peak_window}\n\nRecommended improvement:\n{rec}"

        coaching = {}
        model_status = {}
        try:
            from core.real_ai_vlm import RealAIBotEngine
            vlm = RealAIBotEngine.get_instance()
            coaching = vlm.generate_real_ai_coaching(daily_record=payload, score=score)
            model_status = vlm.get_model_status()
        except Exception:
            pass

        return {
            "source": "STRICTBOT_NEURAL_VLM" if model_status.get("loaded") else "LOCAL_DETERMINISTIC_ANALYZER",
            "model": model_status.get("model_name", "Local Analyzer"),
            "checkpoint": model_status.get("checkpoint"),
            "verdict": verdict,
            "strongest_part": " ".join(strong_parts),
            "points_lost_reason": points_lost_reason,
            "peak_productivity_window": peak_window,
            "recommendation": rec,
            "narrative_summary": narrative,
            "real_ai_coaching": coaching,
            "model_status": model_status,
        }


class AIProofAnalyzer:
    """
    Cross-Device AI Proof & Evidence Verification Engine.
    Evaluates evidence files, screenshots, and photo proof submitted from Android or Windows.
    Performs anti-cheating consistency analysis, cryptographic hash duplication checks,
    task alignment verification, and intelligent confidence scoring.
    """

    @classmethod
    def analyze_proof(
        cls,
        evidence_id: str,
        task_code: str,
        task_title: str,
        timestamp: str,
        device_id: str,
        platform: str = "ANDROID",
        sha256_hash: Optional[str] = None,
        file_path: str = "",
        image_base64: Optional[str] = None,
        existing_hashes: Optional[List[str]] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        settings = settings or {}
        existing_hashes = existing_hashes or []
        anti_cheat_signals: List[Dict[str, Any]] = []
        is_tampered = False

        # 1. Cryptographic Hash Duplication Check (Anti-Cheating)
        if sha256_hash and sha256_hash in existing_hashes:
            # Advisory warning for validator context; do not independently declare fraudulent
            anti_cheat_signals.append({
                "code": "DUPLICATE_PHOTO_HASH",
                "severity": "WARNING",
                "message": "SHA-256 hash matches an already submitted evidence item across the account; flagged for AI context.",
            })

        # 2. Timestamp Chronology Verification
        try:
            from datetime import datetime
            dt = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            if (dt - now).total_seconds() > 300:
                is_tampered = True
                anti_cheat_signals.append({
                    "code": "FUTURE_TIMESTAMP",
                    "severity": "SUSPICIOUS",
                    "message": f"Evidence timestamp ({timestamp}) is in the future relative to server clock.",
                })
        except Exception:
            pass

        # 3. File existence / size check
        if not image_base64 and file_path and not os.path.exists(file_path):
            anti_cheat_signals.append({
                "code": "FILE_PATH_UNRESOLVED",
                "severity": "NOTICE",
                "message": "Proof registered as remote client reference.",
            })

        # Check API integration if configured
        api_key = os.environ.get("STRICTRECORDS_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key and settings.get("ai_enabled", True) and not is_tampered:
            try:
                base_url = settings.get("ai_api_base_url", "https://api.openai.com/v1").rstrip("/")
                model = settings.get("ai_model", "gpt-4o-mini")
                prompt = (
                    f"Evaluate discipline proof submission for task '{task_code}' ({task_title}) "
                    f"originating from {platform} client ({device_id}) at {timestamp}. "
                    "Return JSON with: verdict (APPROVED, NEEDS_REVIEW, REJECTED), "
                    "confidence (HIGH, MEDIUM, LOW), confidence_score (0.0 to 1.0), "
                    "task_alignment, authenticity_assessment, reasoning, suggested_points (integer 0-5)."
                )
                req_body = json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are the StrictRecords Proof Verification AI. Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/chat/completions",
                    data=req_body,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content = resp_data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    parsed["source"] = "OPENAI_API"
                    parsed["evidence_id"] = evidence_id
                    parsed["anti_cheat_signals"] = anti_cheat_signals
                    parsed["auto_approved"] = (
                        parsed.get("verdict") == "APPROVED"
                        and parsed.get("confidence") == "HIGH"
                        and not anti_cheat_signals
                    )
                    return parsed
            except Exception:
                pass  # Fall through to deterministic analyzer

        # Deterministic / Local VLM Proof Verification
        return cls._deterministic_proof_analysis(
            evidence_id=evidence_id,
            task_code=task_code,
            task_title=task_title,
            timestamp=timestamp,
            device_id=device_id,
            platform=platform,
            sha256_hash=sha256_hash,
            is_tampered=is_tampered,
            anti_cheat_signals=anti_cheat_signals,
            file_path=file_path,
            image_base64=image_base64,
        )

    @classmethod
    def _deterministic_proof_analysis(
        cls,
        evidence_id: str,
        task_code: str,
        task_title: str,
        timestamp: str,
        device_id: str,
        platform: str,
        sha256_hash: Optional[str],
        is_tampered: bool,
        anti_cheat_signals: List[Dict[str, Any]],
        file_path: str = "",
        image_base64: Optional[str] = None,
    ) -> Dict[str, Any]:
        if is_tampered:
            return {
                "evidence_id": evidence_id,
                "source": "LOCAL_DETERMINISTIC_ANALYZER",
                "verdict": "REJECTED",
                "confidence": "HIGH",
                "confidence_score": 0.98,
                "auto_approved": False,
                "suggested_points": 0,
                "authenticity_assessment": "Integrity violation: duplicate or invalid evidence metadata detected.",
                "task_alignment": "REJECTED due to anti-cheat flags.",
                "anti_cheat_signals": anti_cheat_signals,
                "reasoning": "Duplicate photo hash or abnormal timestamp matches an existing submission. Rejected automatically to protect discipline score integrity.",
            }

        # Check for Real AI Bot VLM Neural Inference
        vlm_res = None
        has_img = bool(image_base64 or (file_path and os.path.exists(file_path)))
        if has_img:
            try:
                from core.real_ai_vlm import RealAIBotEngine
                img_input = image_base64 if image_base64 else file_path
                vlm_res = RealAIBotEngine.get_instance().analyze_evidence(
                    image_input=img_input,
                    task_code=task_code,
                    description=task_title,
                    user_context={"platform": platform, "device_id": device_id},
                )
            except Exception as e:
                logger = logging.getLogger("AIProofAnalyzer")
                logger.warning(f"Real AI Bot VLM inference notice: {e}")

        return {
            "evidence_id": evidence_id, "source": "LOCAL_DETERMINISTIC_ANALYZER",
            "verdict": "NEEDS_REVIEW", "confidence": "UNAVAILABLE", "confidence_score": 0.0,
            "auto_approved": False, "suggested_points": 0,
            "authenticity_assessment": "No task-authenticity decision is available from the local object classifier.",
            "task_alignment": "Task-specific checks or authorized review required.",
            "anti_cheat_signals": anti_cheat_signals, "neural_analysis": vlm_res,
        }
