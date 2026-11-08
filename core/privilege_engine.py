"""
STRICTRECORDS PRIVILEGE ENGINE
Authoritative Server-Side Privilege Authority (Section 8, 10).
Evaluates current score, verified points, hourly proof checkpoints,
hardware telemetry, and session state to govern entertainment and application privileges.
Frontend JavaScript NEVER decides privilege status.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from core.models import HourlyProofStatus, PrivilegeOutput, StrictSessionState

logger = logging.getLogger("PrivilegeEngine")


class PrivilegeEngine:
    """
    Evaluates dynamic privilege access during normal and Strict Windows sessions.
    Strictly deterministic and server-side.
    """

    def __init__(self, db=None, default_threshold: int = 80):
        self.db = db
        self.default_threshold = default_threshold

    def evaluate_privilege(
        self,
        current_score: int,
        verified_points: int = 0,
        provisional_points: int = 0,
        hourly_proof_status: Union[HourlyProofStatus, str] = HourlyProofStatus.VALID,
        critical_violations: int = 0,
        camera_health: bool = True,
        microphone_health: bool = True,
        strict_session_state: Union[StrictSessionState, str] = StrictSessionState.ACTIVE,
        required_score: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Calculates authoritative privilege verdict.
        Output: ENTERTAINMENT_ALLOWED or ENTERTAINMENT_BLOCKED with structured reason list.
        """
        threshold = required_score if required_score is not None else self.default_threshold
        proof_status_str = (
            hourly_proof_status.value
            if isinstance(hourly_proof_status, HourlyProofStatus)
            else str(hourly_proof_status).upper()
        )
        session_state_str = str(strict_session_state)

        reasons: List[str] = []
        is_blocked = False

        # Rule 1: Score threshold comparison
        if current_score < threshold:
            is_blocked = True
            remaining = threshold - current_score
            reasons.append(f"Score {current_score} is below required threshold {threshold} ({remaining} points needed)")

        # Rule 2: Critical violations flag
        if critical_violations > 0:
            is_blocked = True
            reasons.append(f"Session has {critical_violations} critical integrity violation(s)")

        # Rule 3: Strict session state check
        if session_state_str in (StrictSessionState.RESTRICTED.value, StrictSessionState.ERROR.value):
            is_blocked = True
            reasons.append(f"Session is in restricted/error state ({session_state_str})")

        # Rule 4: Hourly proof validation
        if proof_status_str != HourlyProofStatus.VALID.value:
            is_blocked = True
            reasons.append(f"Hourly proof checkpoint is not approved ({proof_status_str})")

        # Rule 5: Camera / Microphone telemetry integrity
        if not camera_health:
            is_blocked = True
            reasons.append("Proctoring camera stream is disconnected or obscured")

        if not microphone_health:
            is_blocked = True
            reasons.append("Proctoring microphone is offline or muted")

        output_state = (
            PrivilegeOutput.ENTERTAINMENT_BLOCKED
            if is_blocked
            else PrivilegeOutput.ENTERTAINMENT_ALLOWED
        )
        remaining_points = max(0, threshold - current_score)

        result = {
            "output": output_state.value,
            "allowed": not is_blocked,
            "current_score": current_score,
            "required_score": threshold,
            "remaining_points": remaining_points,
            "verified_points": verified_points,
            "provisional_points": provisional_points,
            "hourly_proof_status": proof_status_str,
            "critical_violations": critical_violations,
            "camera_health": camera_health,
            "microphone_health": microphone_health,
            "strict_session_state": session_state_str,
            "reasons": reasons if is_blocked else ["All privilege criteria satisfied"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        logger.info(
            f"PrivilegeEngine evaluation: score={current_score}/{threshold}, "
            f"violations={critical_violations}, proof={proof_status_str} -> {output_state.value}"
        )
        return result

    def evaluate_youtube_privilege(
        self,
        current_score: int,
        required_score: int = 80,
        privilege_mode: str = "CONTINUOUS_PROOF_GATED",
        is_daily_unlock: bool = False,
        last_hourly_proof_status: Optional[str] = None,
        hourly_missed_count_today: int = 0,
        wakeup_completed: bool = True,
        current_time_hhmm: Optional[str] = None,
        allow_windows: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Section 11: Shared YouTube Privilege Authority.
        Modes:
          - CONTINUOUS_PROOF_GATED: Locked until hourly proof verified on time; once verified, unlocked for that hour.
          - ALTERNATING_HOURS: Alternating proof hour vs. reward hour (if preceding proof verified).
          - SCHEDULED_ALLOW_WINDOWS: Only during defined allow windows, conditional on score and no missed proofs.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur_hm = current_time_hhmm or datetime.now().strftime("%H:%M")

        # Wrap with canonical result model
        def _canonical(res: Dict[str, Any]) -> Dict[str, Any]:
            decision = "ALLOW" if res.get("allowed") else "BLOCK"
            return dict(
                res,
                currentScore=current_score,
                requiredScore=required_score,
                scoreEligible=bool(current_score >= required_score),
                dailyPrerequisiteMet=bool(wakeup_completed),
                manuallyRestricted=False,
                temporaryException=None,
                effectiveAccessDecision=decision,
                enforcementStatus="APPLIED",
                lastSuccessfulSync=datetime.now().isoformat(),
                statusText=res.get("reason", ""),
                policyDecision=decision,
            )

        # Priority 0: Explicit daily unlock from Administrator
        if is_daily_unlock:
            return _canonical({
                "allowed": True,
                "privilege_mode": privilege_mode,
                "reason": "YouTube unlocked for today via Administrator release.",
                "current_score": current_score,
                "required_score": required_score,
                "timestamp": ts,
            })

        # Rule 1: Score threshold
        if current_score < required_score:
            remaining = required_score - current_score
            return _canonical({
                "allowed": False,
                "privilege_mode": privilege_mode,
                "reason": f"Score {current_score} is below required threshold {required_score} ({remaining} points needed).",
                "current_score": current_score,
                "required_score": required_score,
                "timestamp": ts,
            })

        # Rule 2: Wake-up completion prerequisite
        if not wakeup_completed:
            return _canonical({
                "allowed": False,
                "privilege_mode": privilege_mode,
                "reason": "Wake-up protocol has not been completed today.",
                "current_score": current_score,
                "required_score": required_score,
                "timestamp": ts,
            })

        mode = (privilege_mode or "CONTINUOUS_PROOF_GATED").upper()

        # Mode A: CONTINUOUS_PROOF_GATED
        if mode == "CONTINUOUS_PROOF_GATED":
            if last_hourly_proof_status is not None:
                if last_hourly_proof_status == "VERIFIED":
                    return _canonical({
                        "allowed": True,
                        "privilege_mode": mode,
                        "reason": "Latest hourly discipline proof verified on time.",
                        "current_score": current_score,
                        "required_score": required_score,
                        "timestamp": ts,
                    })
                else:
                    return _canonical({
                        "allowed": False,
                        "privilege_mode": mode,
                        "reason": f"YouTube is locked: latest hourly proof status is '{last_hourly_proof_status}'.",
                        "current_score": current_score,
                        "required_score": required_score,
                        "timestamp": ts,
                    })
            else:
                # Before first hourly checkpoint of the day: allowed if wake-up completed & score ok
                return _canonical({
                    "allowed": True,
                    "privilege_mode": mode,
                    "reason": "Wake-up protocol verified and score threshold met.",
                    "current_score": current_score,
                    "required_score": required_score,
                    "timestamp": ts,
                })

        # Mode B: ALTERNATING_HOURS
        elif mode == "ALTERNATING_HOURS":
            try:
                cur_h = int(cur_hm.split(":")[0])
            except Exception:
                cur_h = 12

            is_reward_hour = (cur_h % 2 == 1)
            if is_reward_hour:
                if last_hourly_proof_status in ("VERIFIED", None):
                    return _canonical({
                        "allowed": True,
                        "privilege_mode": mode,
                        "reason": f"Alternating reward hour ({cur_h:02d}:00) active and preceding proof verified.",
                        "current_score": current_score,
                        "required_score": required_score,
                        "timestamp": ts,
                    })
                else:
                    return _canonical({
                        "allowed": False,
                        "privilege_mode": mode,
                        "reason": f"Alternating reward hour blocked: preceding proof was '{last_hourly_proof_status}'.",
                        "current_score": current_score,
                        "required_score": required_score,
                        "timestamp": ts,
                    })
            else:
                return _canonical({
                    "allowed": False,
                    "privilege_mode": mode,
                    "reason": f"Hour {cur_h:02d}:00 is an alternating focus/proof hour. YouTube is locked.",
                    "current_score": current_score,
                    "required_score": required_score,
                    "timestamp": ts,
                })

        # Mode C: SCHEDULED_ALLOW_WINDOWS
        elif mode == "SCHEDULED_ALLOW_WINDOWS":
            if hourly_missed_count_today > 0:
                return _canonical({
                    "allowed": False,
                    "privilege_mode": mode,
                    "reason": f"YouTube locked: {hourly_missed_count_today} hourly proof(s) missed today.",
                    "current_score": current_score,
                    "required_score": required_score,
                    "timestamp": ts,
                })

            windows = allow_windows or [{"start": "20:00", "end": "22:00"}]
            in_window = False
            matched_win = None
            for w in windows:
                if w.get("start", "20:00") <= cur_hm < w.get("end", "22:00"):
                    in_window = True
                    matched_win = w
                    break

            if in_window:
                return _canonical({
                    "allowed": True,
                    "privilege_mode": mode,
                    "reason": f"Within scheduled allow window ({matched_win['start']}–{matched_win['end']}) with 0 missed proofs.",
                    "current_score": current_score,
                    "required_score": required_score,
                    "timestamp": ts,
                })
            else:
                return _canonical({
                    "allowed": False,
                    "privilege_mode": mode,
                    "reason": "Outside scheduled allow windows (Allowed: 8:00 PM – 10:00 PM).",
                    "current_score": current_score,
                    "required_score": required_score,
                    "timestamp": ts,
                })

        # Default fallback
        return _canonical({
            "allowed": current_score >= required_score,
            "privilege_mode": mode,
            "reason": "Standard score threshold enforcement.",
            "current_score": current_score,
            "required_score": required_score,
            "timestamp": ts,
        })

    def evaluate_entertainment_privilege(
        self,
        app_key: str,
        current_score: int,
        required_score: int = 80,
        is_daily_unlock: bool = False,
        wakeup_completed: bool = True,
        last_hourly_proof_status: Optional[str] = None,
        hourly_missed_count_today: int = 0,
        manually_restricted: bool = False,
        temporary_exception: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Unified Authoritative Privilege Authority for YouTube, Instagram, Spotify, and other targets.
        Evaluates whether entertainment/media access is permitted or blocked based on:
        1. Temporary exceptions (e.g. Single-Video YouTube Reward, GYM Mode for Spotify) or explicit daily unlock
        2. Manual restriction preference
        3. Score threshold (required 80 for Social Video, 60 for Music/Audio)
        4. Wake-up completion prerequisite (mandatory for Social Video, not required for Music)
        5. Hourly proof discipline status
        """
        clean_key = (app_key or "").strip().lower()
        from core.restriction_targets import TARGETS
        spec = TARGETS.get(clean_key, {})
        target_name = spec.get("name") or ("YouTube" if clean_key == "youtube" else ("Instagram" if clean_key == "instagram" else ("Spotify" if clean_key == "spotify" else clean_key.capitalize())))
        target_cat = category or spec.get("category", "")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        now_iso = datetime.now().isoformat()

        requires_wakeup = (target_cat == "Social Video" or clean_key in ("youtube", "instagram"))
        score_eligible = (current_score >= required_score)
        youtube_reward = clean_key == 'youtube' and temporary_exception == 'SINGLE_YOUTUBE_REWARD'
        daily_prereq_met = bool(wakeup_completed) if requires_wakeup else True

        def _build_result(allowed: bool, reason: str, temp_exc: Optional[str] = None) -> Dict[str, Any]:
            decision = "ALLOW" if allowed else "BLOCK"
            return {
                "app_key": clean_key,
                "service": clean_key,
                "appName": target_name,
                "currentScore": current_score,
                "requiredScore": required_score,
                "scoreEligible": score_eligible,
                "dailyPrerequisiteMet": daily_prereq_met,
                "manuallyRestricted": bool(manually_restricted),
                "temporaryException": temp_exc or ('SINGLE_YOUTUBE_REWARD' if allowed and youtube_reward else None),
                "effectiveAccessDecision": decision,
                "enforcementStatus": "APPLIED",
                "lastSuccessfulSync": now_iso,
                "allowed": allowed,
                "restricted": not allowed,
                "reason": reason,
                "statusText": reason,
                "policyDecision": decision,
                "current_score": current_score,
                "required_score": required_score,
                "timestamp": ts,
            }

        # 0. Temporary Exception / Admin Override / Daily unlock
        if temporary_exception in ("SINGLE_VIDEO_REWARD", "GYM_MODE"):
            msg = f"Authorized {temporary_exception} active for {target_name}."
            return _build_result(True, msg, temp_exc=temporary_exception)

        if is_daily_unlock:
            return _build_result(True, f"{target_name} unlocked via Administrator release.", temp_exc=temporary_exception)

        # 1. Manual restriction
        if manually_restricted:
            return _build_result(False, "Restriction enabled by user preference.")

        # 2. Score threshold
        if not score_eligible and not youtube_reward:
            remaining = required_score - current_score
            return _build_result(False, f"Score {current_score}/{required_score} is below threshold ({remaining} pts needed).")

        # 3. Wake-up completion prerequisite (if applicable)
        if requires_wakeup and not daily_prereq_met:
            return _build_result(False, "Wake-up protocol has not been completed today.")

        # 4. Missed hourly proofs
        if hourly_missed_count_today > 0:
            return _build_result(False, f"{hourly_missed_count_today} hourly proof(s) missed today. Access blocked.")

        # 5. Hourly proof pending / invalid
        if last_hourly_proof_status in ("INVALID", "DUPLICATE", "LOW_CONFIDENCE", "PENDING"):
            return _build_result(False, f"Hourly proof pending or unverified ({last_hourly_proof_status}).")

        # All criteria satisfied
        if requires_wakeup:
            success_reason = f"Discipline verified (Score {current_score}/{required_score} & Wake-up verified)."
        else:
            success_reason = f"Allowed by access policy (Score {current_score}/{required_score})."
        return _build_result(True, success_reason)

