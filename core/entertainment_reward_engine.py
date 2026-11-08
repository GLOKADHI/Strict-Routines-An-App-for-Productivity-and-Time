"""
StrictRecords — Entertainment Reward & Educational Video Engine
=============================================================
Authoritative coordination for:
1. Early Proof Submission Entertainment Reward (EARLY_PROOF_ENTERTAINMENT_REWARD_20M)
   - Submitted >= 10 minutes before scheduled deadline
   - Approved with points awarded > 6
   - 20 minutes (1200 seconds) of entertainment access to all configured entertainment apps
   - Countdown begins on FIRST actual activation/use (not on submit or approval)
   - Monotonic wall-clock elapsed time across Android, Windows, and Browser Extension
   - Shared atomic enforcement, idempotent on proof ID, zero points added/deducted

2. Educational YouTube Video Access via Taufa AI & Playback Tracking
   - Single-video access based on verified educational content
   - Exact video duration in MM:SS and seconds (no rounding 20:05 to 20m)
   - Playback tracking with disjoint interval bookkeeping
   - Forward seek does not credit skipped time; backward seek does not grant extra time
   - Playback speed (e.g. 2x) accurately consumed
   - Single authoritative session with device lease to prevent concurrent playback
   - Upon completion, timer reaches 00:00, marks session COMPLETED, locks YouTube,
     and instructs user to reload page to restore restrictions
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist

logger = logging.getLogger("EntertainmentRewardEngine")

# Business Rules
EARLY_PROOF_POINTS_THRESHOLD = 6  # Points awarded must be strictly greater than 6 (i.e. 7 or 8)
EARLY_PROOF_ADVANCE_MINUTES = 10  # Must submit at least 10 minutes before scheduled deadline
EARLY_PROOF_REWARD_DURATION_SECONDS = 1200  # 20 minutes (1,200 seconds)
ENTITLEMENT_TYPE_EARLY_PROOF = "EARLY_PROOF_ENTERTAINMENT_REWARD_20M"

YOUTUBE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def extract_canonical_video_id(url_or_id: str) -> Optional[str]:
    """
    Extracts canonical 11-character YouTube video ID from watch URLs, short URLs,
    embeds, or raw 11-char IDs. Rejects playlists without v= and unrelated URLs.
    """
    if not url_or_id or not isinstance(url_or_id, str):
        return None
    clean = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", clean):
        return clean

    try:
        parsed = urllib.parse.urlparse(clean)
        domain = (parsed.hostname or "").lower()
        if domain not in YOUTUBE_DOMAINS:
            return None

        if domain == "youtu.be":
            path = parsed.path.strip("/")
            match = re.match(r"^[A-Za-z0-9_-]{11}", path)
            return match.group(0) if match else None

        if parsed.path in ("/watch", "/watch_popup"):
            qs = urllib.parse.parse_qs(parsed.query)
            v_list = qs.get("v")
            if v_list and len(v_list[0]) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", v_list[0]):
                return v_list[0]

        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] in ("embed", "v", "shorts"):
            candidate = path_parts[1]
            if len(candidate) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

        return None
    except Exception:
        return None


def format_seconds_to_mmss(total_seconds: int) -> str:
    """Formats integer seconds to MM:SS or HH:MM:SS string."""
    secs = max(0, int(total_seconds))
    hrs = secs // 3600
    mins = (secs % 3600) // 60
    rem_secs = secs % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{rem_secs:02d}"
    return f"{mins:02d}:{rem_secs:02d}"


def parse_mmss_to_seconds(time_str: str) -> Optional[int]:
    """Parses MM:SS or HH:MM:SS or integer string to total seconds."""
    if not time_str or not isinstance(time_str, str):
        return None
    clean = time_str.strip()
    parts = clean.split(":")
    try:
        if len(parts) == 1:
            return int(float(parts[0]))
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        return None
    return None


def is_early_submission(submission_received_iso: str, scheduled_deadline_iso: str) -> bool:
    """
    Evaluates whether proof was submitted at least 10 minutes before the scheduled deadline.
    Uses actual submission-received timestamp, not approval timestamp.
    """
    try:
        sub_dt = iso_to_dt(submission_received_iso)
        deadline_dt = iso_to_dt(scheduled_deadline_iso)
        return sub_dt <= deadline_dt - timedelta(minutes=EARLY_PROOF_ADVANCE_MINUTES)
    except Exception as exc:
        logger.warning("Error evaluating early submission timeliness: %s", exc)
        return False


def evaluate_early_proof_eligibility(
    proof_id: str,
    submission_received_iso: str,
    scheduled_deadline_iso: str,
    points_awarded: int,
    status: str,
    duplicate_detected: bool = False,
    task_type: str = "HOURLY",
) -> Tuple[bool, str]:
    """
    Evaluates all conditions for EARLY_PROOF_ENTERTAINMENT_REWARD_20M:
    1. Submission received >= 10 minutes before deadline.
    2. Proof approved / verified.
    3. Points awarded > 6.
    4. Not expired, rejected, duplicate, or invalid.
    """
    clean_status = (status or "").strip().upper()
    if clean_status not in ("APPROVED", "VERIFIED"):
        return False, f"Proof submission {proof_id} is not approved (status: {clean_status})."

    if duplicate_detected:
        return False, "Evidence was flagged as duplicate; reward not eligible."

    if points_awarded is None or points_awarded <= EARLY_PROOF_POINTS_THRESHOLD:
        return False, f"Points awarded ({points_awarded}) does not exceed the required threshold of > {EARLY_PROOF_POINTS_THRESHOLD}."

    if not is_early_submission(submission_received_iso, scheduled_deadline_iso):
        return False, f"Submission was not at least {EARLY_PROOF_ADVANCE_MINUTES} minutes before scheduled deadline."

    return True, "Eligible for 20-Minute Early Proof Entertainment Reward."


def merge_watched_intervals(intervals: List[Any]) -> List[Tuple[float, float]]:
    """
    Merges a list of [start, end] intervals into disjoint, sorted intervals.
    Prevents double-counting of overlapping or replayed video segments.
    """
    if not intervals:
        return []
    parsed: List[Tuple[float, float]] = []
    for item in intervals:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                s = float(item[0])
                e = float(item[1])
                if e > s:
                    parsed.append((s, e))
            except (ValueError, TypeError):
                pass
    if not parsed:
        return []
    parsed.sort(key=lambda x: x[0])
    merged = [parsed[0]]
    for cur_s, cur_e in parsed[1:]:
        last_s, last_e = merged[-1]
        if cur_s <= last_e + 0.5:  # 0.5s tolerance
            merged[-1] = (last_s, max(last_e, cur_e))
        else:
            merged.append((cur_s, cur_e))
    return merged


def calculate_watched_seconds(intervals: List[Any]) -> float:
    """Calculates total unique watched seconds from intervals."""
    merged = merge_watched_intervals(intervals)
    return sum(end - start for start, end in merged)


class EntertainmentRewardEngine:
    """Authoritative lifecycle coordinator for Entertainment Rewards and Educational Video Access."""

    def __init__(self, db: Any):
        self.db = db

    # =========================================================================
    # FEATURE 1: Early Proof Submission Entertainment Reward
    # =========================================================================

    def grant_early_proof_reward(
        self,
        account_id: str,
        device_id: str,
        proof_id: str,
        submission_received_iso: str,
        scheduled_deadline_iso: str,
        points_awarded: int,
        status: str = "APPROVED",
        duplicate_detected: bool = False,
        task_type: str = "HOURLY",
    ) -> Dict[str, Any]:
        """
        Grants a 20-minute entertainment reward if all eligibility conditions are met.
        Idempotent: uses proof_id as the idempotency key.
        Does NOT modify points ledger (0 points added or deducted).
        """
        existing = self.db.get_entertainment_entitlement_by_proof(proof_id)
        if existing:
            return {"success": True, "reward": existing, "idempotent": True, "eligible": True}

        eligible, reason = evaluate_early_proof_eligibility(
            proof_id=proof_id,
            submission_received_iso=submission_received_iso,
            scheduled_deadline_iso=scheduled_deadline_iso,
            points_awarded=points_awarded,
            status=status,
            duplicate_detected=duplicate_detected,
            task_type=task_type,
        )

        if not eligible:
            logger.info("Early proof reward not eligible for %s: %s", proof_id, reason)
            return {"success": False, "eligible": False, "reason": reason}

        entitlement_id = "ent_" + hashlib.sha256(f"{account_id}:{proof_id}".encode()).hexdigest()[:40]
        created = self.db.create_entertainment_entitlement(
            entitlement_id=entitlement_id,
            account_id=account_id,
            device_id=device_id,
            proof_id=proof_id,
            submission_received_at=submission_received_iso,
            scheduled_deadline_at=scheduled_deadline_iso,
            points_awarded=points_awarded,
            duration_seconds=EARLY_PROOF_REWARD_DURATION_SECONDS,
            entitlement_type=ENTITLEMENT_TYPE_EARLY_PROOF,
            state="AVAILABLE",
        )
        return {"success": True, "reward": created, "idempotent": False, "eligible": True}

    def activate_reward(
        self,
        entitlement_id: str,
        client_id: str,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Activates an AVAILABLE entertainment reward on FIRST actual use.
        Starts 20-minute elapsed wall-time countdown.
        Does not reset on device switch or browser refresh.
        """
        current = now_ist()
        entitlement = self.db.get_entertainment_entitlement(entitlement_id)
        if not entitlement:
            return {"success": False, "code": "NOT_FOUND", "error": "Reward entitlement not found."}

        if account_id and entitlement["account_id"] != account_id:
            return {"success": False, "code": "UNAUTHORIZED", "error": "Reward belongs to another account."}

        from windows.strict_study_workflow import reward_hold
        with self.db._connect() as conn:
            row = conn.execute("SELECT activity_id,submission_id FROM proof_submission_events WHERE account_id=? AND json_extract(payload_json,'$.evidence_id')=?", (entitlement['account_id'], entitlement['proof_id'])).fetchone()
        if reward_hold(self.db, entitlement['account_id'], activity_id=row['activity_id'] if row else None,
                       submission_id=row['submission_id'] if row else None):
            return {'success':False,'code':'STRICT_SESSION_PENDING','error':'Finish the corresponding Strict Session manually after verified Android proof.'}

        state = entitlement.get("state")
        if state in ("EXPIRED", "CANCELLED"):
            return {"success": False, "code": "REWARD_CONSUMED", "error": "This reward has already ended."}

        # Check if an active session already exists for this account
        active = self.db.get_active_entertainment_entitlement(entitlement["account_id"])
        if active:
            if active["expiry_time"]:
                exp_dt = iso_to_dt(active["expiry_time"])
                if exp_dt <= current:
                    self.db.update_entertainment_entitlement(
                        active["entitlement_id"],
                        state="EXPIRED",
                        termination_result="DEADLINE_EXPIRED",
                    )
                elif active["entitlement_id"] != entitlement_id:
                    rem = int((exp_dt - current).total_seconds())
                    return {
                        "success": True,
                        "reward": active,
                        "session_active": True,
                        "remaining_seconds": max(0, rem),
                        "expiry_time": active["expiry_time"],
                        "note": "Existing active reward session returned.",
                    }

        if state == "ACTIVE":
            exp_dt = iso_to_dt(entitlement["expiry_time"])
            rem = max(0, int((exp_dt - current).total_seconds()))
            return {
                "success": True,
                "reward": entitlement,
                "session_active": rem > 0,
                "remaining_seconds": rem,
                "expiry_time": entitlement["expiry_time"],
                "active_client_id": entitlement.get("active_client_id"),
            }

        # Activate the 20-minute window
        duration = entitlement.get("duration_seconds") or EARLY_PROOF_REWARD_DURATION_SECONDS
        expiry = dt_to_iso(current + timedelta(seconds=duration))
        updated = self.db.update_entertainment_entitlement(
            entitlement_id,
            state="ACTIVE",
            session_start_time=dt_to_iso(current),
            expiry_time=expiry,
            active_client_id=client_id,
        )
        return {
            "success": True,
            "reward": updated,
            "session_active": True,
            "remaining_seconds": duration,
            "expiry_time": expiry,
            "active_client_id": client_id,
        }

    def get_reward_status(self, account_id: str) -> Dict[str, Any]:
        """
        Returns real-time entertainment reward state: active session and available rewards.
        Automatically marks active session EXPIRED if wall-clock deadline has passed.
        """
        current = now_ist()
        active = self.db.get_active_entertainment_entitlement(account_id)
        if active and active.get("expiry_time"):
            exp_dt = iso_to_dt(active["expiry_time"])
            rem = int((exp_dt - current).total_seconds())
            if rem <= 0:
                self.db.update_entertainment_entitlement(
                    active["entitlement_id"],
                    state="EXPIRED",
                    termination_result="DEADLINE_EXPIRED",
                )
                active = None

        active_data = None
        rem_secs = 0
        if active and active.get("expiry_time"):
            rem_secs = max(0, int((iso_to_dt(active["expiry_time"]) - current).total_seconds()))
            active_data = {
                "entitlement_id": active["entitlement_id"],
                "account_id": active["account_id"],
                "entitlement_type": active.get("entitlement_type", ENTITLEMENT_TYPE_EARLY_PROOF),
                "proof_id": active["proof_id"],
                "state": "ACTIVE",
                "session_start_time": active.get("session_start_time"),
                "expiry_time": active.get("expiry_time"),
                "remaining_seconds": rem_secs,
                "active_client_id": active.get("active_client_id"),
            }

        available = self.db.get_available_entertainment_entitlements(account_id)
        return {
            "success": True,
            "active_session": active_data,
            "available_rewards": available,
            "total_available": len(available),
            "is_active": active_data is not None and rem_secs > 0,
            "remaining_seconds": rem_secs,
        }

    def is_entertainment_access_allowed(self, account_id: str) -> bool:
        """Quick boolean check whether entertainment access is granted by an active reward."""
        status = self.get_reward_status(account_id)
        return bool(status.get("is_active"))

    # =========================================================================
    # FEATURE 2: Educational YouTube Video Access & Playback Tracking
    # =========================================================================

    def save_educational_approval(
        self,
        video_id: str,
        canonical_url: str,
        decision: str,
        duration_seconds: int,
        duration_formatted: str,
        reason: Optional[str] = None,
        confidence: Optional[float] = None,
        timeline: Optional[List[Dict[str, Any]]] = None,
        title: Optional[str] = None,
        channel: Optional[str] = None,
        account_id: str = "user_lok",
        review_status: str = "COMPLETED",
    ) -> Dict[str, Any]:
        """Saves a verified educational video approval or rejection."""
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        approval_id = f"app_{clean_vid}_{account_id}"
        timeline_json = json.dumps(timeline or [])
        return self.db.save_educational_video_approval(
            approval_id=approval_id,
            video_id=clean_vid,
            canonical_url=canonical_url or f"https://www.youtube.com/watch?v={clean_vid}",
            title=title,
            channel=channel,
            duration_seconds=duration_seconds,
            duration_formatted=duration_formatted or format_seconds_to_mmss(duration_seconds),
            decision=decision,
            reason=reason,
            confidence=confidence,
            timeline_json=timeline_json,
            review_status=review_status,
            account_id=account_id,
        )

    def get_educational_approval(self, video_id: str, account_id: str = "user_lok") -> Optional[Dict[str, Any]]:
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        return self.db.get_educational_video_approval(clean_vid, account_id=account_id)

    def start_or_resume_playback_session(
        self,
        video_id: str,
        account_id: str = "user_lok",
        device_id: str = "WINDOWS_EXE",
    ) -> Dict[str, Any]:
        """
        Starts or resumes an educational video playback session.
        Prevents concurrent playback abuse via device lease and heartbeat check.
        Preserves already watched intervals across restarts.
        """
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        approval = self.get_educational_approval(clean_vid, account_id=account_id)
        if not approval or approval.get("decision") != "EDUCATIONAL":
            return {
                "success": False,
                "code": "NOT_APPROVED",
                "error": "This video has not been approved as educational by Taufa AI.",
                "allowed": False,
            }

        allowed_seconds = int(approval.get("duration_seconds") or 0)
        session_id = f"sess_{clean_vid}_{account_id}"
        session = self.db.get_educational_playback_session(clean_vid, account_id=account_id)

        now = now_ist()
        if session:
            # Check if already completed
            if session.get("status") == "COMPLETED":
                return {
                    "success": True,
                    "session": session,
                    "status": "COMPLETED",
                    "allowed": False,
                    "is_completed": True,
                    "remaining_seconds": 0,
                    "duration_formatted": approval.get("duration_formatted"),
                    "note": "Educational video already completed.",
                }

            # Device lease / concurrency check
            cur_device = session.get("device_id")
            last_hb_str = session.get("last_heartbeat_at")
            last_hb = iso_to_dt(last_hb_str) if last_hb_str else now - timedelta(seconds=60)
            hb_age_sec = (now - last_hb).total_seconds()

            if cur_device and cur_device != device_id and session.get("last_playback_state") == "PLAYING" and hb_age_sec < 10:
                # Concurrent playback disallowed
                return {
                    "success": False,
                    "code": "CONCURRENT_PLAYBACK_DISALLOWED",
                    "error": f"Educational video is currently being watched on another device ({cur_device}). Pause it before switching.",
                    "allowed": False,
                }

            # Controlled handover to this device
            self.db.update_educational_playback_session(
                session["session_id"],
                device_id=device_id,
                last_heartbeat_at=dt_to_iso(now),
            )
            session = self.db.get_educational_playback_session(clean_vid, account_id=account_id)
        else:
            session = self.db.get_or_create_educational_playback_session(
                session_id=session_id,
                approval_id=approval["approval_id"],
                video_id=clean_vid,
                allowed_seconds=allowed_seconds,
                account_id=account_id,
                device_id=device_id,
            )

        consumed = float(session.get("consumed_seconds") or 0.0)
        rem = max(0, int(allowed_seconds - consumed))
        return {
            "success": True,
            "session": session,
            "status": session.get("status", "ACTIVE"),
            "allowed": session.get("status") != "COMPLETED",
            "is_completed": session.get("status") == "COMPLETED",
            "duration_seconds": allowed_seconds,
            "duration_formatted": approval.get("duration_formatted") or format_seconds_to_mmss(allowed_seconds),
            "consumed_seconds": consumed,
            "remaining_seconds": rem,
            "remaining_formatted": format_seconds_to_mmss(rem),
        }

    def record_playback_heartbeat(
        self,
        video_id: str,
        account_id: str,
        device_id: str,
        playback_state: str,
        playback_rate: float = 1.0,
        interval_start: Optional[float] = None,
        interval_end: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Reconciles playback progress and updates disjoint watched intervals.
        Enforces:
        - Forward seeks do not credit skipped content.
        - Backward seeks do not grant extra time.
        - Speed (e.g. 2x) accurately consumed.
        - Ends session at 00:00 when entire video duration is watched.
        """
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        session = self.db.get_educational_playback_session(clean_vid, account_id=account_id)
        if not session:
            return {"success": False, "code": "NO_SESSION", "error": "No active session for this video."}

        if session.get("status") == "COMPLETED":
            return {
                "success": True,
                "status": "COMPLETED",
                "completed": True,
                "remaining_seconds": 0,
                "consumed_seconds": session.get("allowed_seconds", 0),
                "last_playback_state": playback_state,
            }

        allowed_seconds = int(session.get("allowed_seconds") or 0)
        existing_intervals = []
        try:
            existing_intervals = json.loads(session.get("watched_intervals_json") or "[]")
        except Exception:
            existing_intervals = []

        # Add new interval if valid
        if interval_start is not None and interval_end is not None:
            try:
                s = float(interval_start)
                e = float(interval_end)
                if e > s and s >= 0 and e <= allowed_seconds + 5:  # within video boundaries
                    existing_intervals.append([s, e])
            except (ValueError, TypeError):
                pass

        merged = merge_watched_intervals(existing_intervals)
        consumed = calculate_watched_seconds(merged)
        is_completed = bool(allowed_seconds > 0 and consumed >= allowed_seconds)

        now = now_ist()
        updates: Dict[str, Any] = {
            "consumed_seconds": min(allowed_seconds, consumed) if is_completed else consumed,
            "watched_intervals_json": json.dumps(merged),
            "last_playback_state": playback_state,
            "playback_rate": playback_rate,
            "last_heartbeat_at": dt_to_iso(now),
            "device_id": device_id,
        }

        if is_completed:
            updates["status"] = "COMPLETED"
            updates["completed_at"] = dt_to_iso(now)

        updated_session = self.db.update_educational_playback_session(session["session_id"], **updates)
        rem = max(0, int(allowed_seconds - (allowed_seconds if is_completed else consumed)))

        return {
            "success": True,
            "status": "COMPLETED" if is_completed else "ACTIVE",
            "completed": is_completed,
            "allowed_seconds": allowed_seconds,
            "consumed_seconds": consumed,
            "remaining_seconds": rem,
            "remaining_formatted": format_seconds_to_mmss(rem),
            "intervals_count": len(merged),
            "last_playback_state": playback_state,
        }

    def get_educational_video_access_decision(
        self,
        video_id: str,
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """
        Determines whether a specific YouTube video is permitted under Educational Mode.
        Returns access decision (ALLOW/BLOCK) and remaining playback allowance.
        """
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        approval = self.get_educational_approval(clean_vid, account_id=account_id)
        if not approval:
            return {"allowed": False, "reason": "No educational review found for this video."}

        if approval.get("decision") != "EDUCATIONAL":
            return {
                "allowed": False,
                "reason": approval.get("reason") or "Video was classified as NOT_EDUCATIONAL.",
                "decision": approval.get("decision"),
            }

        session = self.db.get_educational_playback_session(clean_vid, account_id=account_id)
        if session and session.get("status") == "COMPLETED":
            return {
                "allowed": False,
                "is_completed": True,
                "reason": "Educational video completed. Please reload the page to restore YouTube restrictions.",
            }

        allowed_sec = int(approval.get("duration_seconds") or 0)
        consumed_sec = float(session.get("consumed_seconds") or 0.0) if session else 0.0
        rem_sec = max(0, int(allowed_sec - consumed_sec))

        return {
            "allowed": True,
            "video_id": clean_vid,
            "title": approval.get("title"),
            "duration_seconds": allowed_sec,
            "duration_formatted": approval.get("duration_formatted"),
            "consumed_seconds": consumed_sec,
            "remaining_seconds": rem_sec,
            "remaining_formatted": format_seconds_to_mmss(rem_sec),
            "status": session.get("status", "ACTIVE") if session else "AVAILABLE",
        }
