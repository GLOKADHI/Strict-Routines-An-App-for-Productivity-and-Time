"""Legacy local reward records retained for compatibility and history.

Production claims now use core.youtube_reward_cloud and the authenticated shared
cloud_points.youtube_rewards authority. These helpers cannot activate a second
independent reward through any production HTTP route. The historical one-per-
hourly-occurrence policy and its score/category requirements remain; current
eligibility is at least ten minutes before the actual submission deadline and
new activations last exactly twenty minutes.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist

logger = logging.getLogger("YouTubeRewardEngine")

HARD_MAX_ALLOWANCE_SECONDS = 1200  # 20 minutes
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
    Extracts the canonical 11-character YouTube video ID from standard watch links,
    short links (youtu.be), embed links, or raw video IDs.
    Rejects playlists (list= without v=), channel URLs, and unrelated domains.
    """
    if not url_or_id or not isinstance(url_or_id, str):
        return None

    clean = url_or_id.strip()

    # Raw 11-char video ID pattern
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", clean):
        return clean

    try:
        parsed = urllib.parse.urlparse(clean)
        domain = parsed.hostname.lower() if parsed.hostname else ""

        if domain not in YOUTUBE_DOMAINS:
            return None

        # youtu.be/<id>
        if domain == "youtu.be":
            path = parsed.path.strip("/")
            match = re.match(r"^[A-Za-z0-9_-]{11}", path)
            return match.group(0) if match else None

        # youtube.com/watch?v=<id>
        if parsed.path in ("/watch", "/watch_popup"):
            qs = urllib.parse.parse_qs(parsed.query)
            v_list = qs.get("v")
            if v_list and len(v_list[0]) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", v_list[0]):
                return v_list[0]

        # youtube.com/embed/<id> or /v/<id> or /shorts/<id>
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] in ("embed", "v", "shorts"):
            candidate = path_parts[1]
            if len(candidate) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

        return None
    except Exception:
        return None


def calculate_viewing_allowance(duration_seconds: Optional[int]) -> int:
    """Current entitlement duration is exactly 1,200 seconds, independent of video length."""
    return HARD_MAX_ALLOWANCE_SECONDS


def is_submission_timely(accepted_at_iso: str, scheduled_time_iso: str) -> bool:
    """Inclusive ten-minute boundary, with the persisted actual submission deadline."""
    try:
        accepted_dt = iso_to_dt(accepted_at_iso)
        cutoff_dt = iso_to_dt(scheduled_time_iso)
        return accepted_dt <= cutoff_dt - timedelta(minutes=10)
    except Exception as exc:
        logger.warning("Error evaluating timeliness: %s", exc)
        return False


def evaluate_eligibility(
    occurrence_id: str,
    submission_id: str,
    score: Optional[int],
    is_timely: bool,
    duplicate_detected: bool,
    task_type: str = "HOURLY",
    category_match: bool = True,
    description_match: bool = True,
) -> Tuple[bool, str]:
    """
    Evaluates whether an hourly occurrence qualifies for a single-video reward entitlement:
      1. Task must be eligible HOURLY occurrence.
      2. Category and evidence must match (category_match == True).
      3. Description must match evidence (description_match == True).
      4. Valid activity score >= 6/8.
      5. Accepted before occurrence cutoff (is_timely == True).
      6. Evidence not rejected for duplicate.
    """
    clean_type = (task_type or "HOURLY").strip().upper()
    if clean_type != "HOURLY":
        return False, f"Task type '{clean_type}' is excluded from video rewards."

    if not category_match:
        return False, "Category validation failed; submitted evidence does not support the selected activity."

    if not description_match:
        return False, "Description does not match submitted evidence."

    if score is None or score < 6:
        return False, f"Score {score}/8 does not meet the 6/8 threshold required for a video reward."

    if not is_timely:
        return False, "Submission was accepted at or after the occurrence cutoff (late for reward eligibility)."

    if duplicate_detected:
        return False, "Evidence was flagged as duplicate; reward not eligible."

    return True, "Eligible for Single-Video YouTube Reward."


class YouTubeRewardEngine:
    """Authoritative lifecycle and session coordinator for YouTube rewards."""

    def __init__(self, db: Any):
        self.db = db

    def evaluate_and_grant_reward(
        self,
        account_id: str,
        hourly_occurrence_id: str,
        submission_id: str,
        score: int,
        accepted_at_iso: str,
        scheduled_time_iso: str,
        duplicate_detected: bool = False,
        category_match: bool = True,
        description_match: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Creates a reward entitlement if all eligibility conditions are met.
        Idempotent: returns existing entitlement if already granted.
        """
        existing = self.db.get_youtube_reward_by_occurrence(hourly_occurrence_id)
        if existing:
            return existing

        timely = is_submission_timely(accepted_at_iso, scheduled_time_iso)
        eligible, reason = evaluate_eligibility(
            occurrence_id=hourly_occurrence_id,
            submission_id=submission_id,
            score=score,
            is_timely=timely,
            duplicate_detected=duplicate_detected,
            task_type="HOURLY",
            category_match=category_match,
            description_match=description_match,
        )

        if not eligible:
            logger.info("Reward not eligible for %s: %s", hourly_occurrence_id, reason)
            return None

        reward_id = "rew_" + __import__("hashlib").sha256(f"{account_id}:{hourly_occurrence_id}".encode()).hexdigest()[:40]
        return self.db.create_youtube_reward(
            reward_id=reward_id,
            account_id=account_id,
            hourly_occurrence_id=hourly_occurrence_id,
            submission_id=submission_id,
            validated_score=score,
            is_timely=timely,
            state="AVAILABLE",
        )

    def select_video(self,reward_id,youtube_url,duration_seconds=None):
        video=extract_canonical_video_id(youtube_url)
        if not video:return dict(success=False,code='INVALID_YOUTUBE_URL',error='Select one valid YouTube video.')
        if duration_seconds is not None and (type(duration_seconds) is not int or duration_seconds<=0):
            return dict(success=False,code='INVALID_DURATION',error='Video duration must be a positive number of whole seconds.')
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone()
            if not row:return dict(success=False,code='NOT_FOUND',error='Reward not found.')
            reward=dict(row)
            if reward['state'] in ('CONSUMED','EXPIRED','CANCELLED'):
                return dict(success=False,code='REWARD_CONSUMED',error='This reward has already ended.')
            if reward['selected_video_id']:
                if reward['selected_video_id']!=video:return dict(success=False,code='VIDEO_ALREADY_BOUND',error='This reward is already bound to one video.')
                return dict(success=True,reward=reward,allowed_seconds=reward['allowed_seconds'],video_id=video)
            allowance=calculate_viewing_allowance(duration_seconds)
            conn.execute("UPDATE youtube_video_rewards SET selected_video_id=?,video_duration_seconds=?,allowed_seconds=?,state='VIDEO_SELECTED',updated_at=? WHERE reward_id=?",(video,duration_seconds,allowance,dt_to_iso(now_ist()),reward_id))
            reward=dict(conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone())
            return dict(success=True,reward=reward,allowed_seconds=allowance,video_id=video)

    def start_session(self,reward_id,client_id):
        client=str(client_id or '').upper()
        if not client or len(client)>160:return dict(success=False,code='CLIENT_REQUIRED',error='A registered client is required.')
        current=now_ist()
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone()
            if not row:return dict(success=False,code='NOT_FOUND',error='Reward not found.')
            reward=dict(row)
            for active in conn.execute("SELECT * FROM youtube_video_rewards WHERE account_id=? AND state='ACTIVE'",(reward['account_id'],)).fetchall():
                if not active['expiry_time'] or iso_to_dt(active['expiry_time'])<=current:
                    conn.execute("UPDATE youtube_video_rewards SET state='EXPIRED',termination_result='DEADLINE_EXPIRED',updated_at=? WHERE reward_id=?",(dt_to_iso(current),active['reward_id']))
                elif active['reward_id']!=reward_id:
                    return dict(success=False,code='PARALLEL_SESSION_DISALLOWED',error='Another reward session is already active.')
            reward=dict(conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone())
            if reward['state'] in ('CONSUMED','EXPIRED','CANCELLED'):
                return dict(success=False,code='REWARD_CONSUMED',error='This reward has already ended.')
            
            if reward['state']=='ACTIVE':
                if reward['active_client_id']!=client:return dict(success=False,code='SESSION_OWNED_BY_OTHER_CLIENT',error='Transfer the existing session before opening it on another client.')
            else:
                allowance=HARD_MAX_ALLOWANCE_SECONDS
                expiry=dt_to_iso(current+timedelta(seconds=allowance))
                conn.execute("UPDATE youtube_video_rewards SET state='ACTIVE',session_start_time=?,expiry_time=?,active_client_id=?,updated_at=? WHERE reward_id=?",(dt_to_iso(current),expiry,client,dt_to_iso(current),reward_id))
                reward=dict(conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone())
            return dict(success=True,reward=reward,session_active=True,remaining_seconds=max(0,int((iso_to_dt(reward['expiry_time'])-current).total_seconds())),expiry_time=reward['expiry_time'],active_client_id=client)

    def get_session_status(self, reward_id: str) -> Dict[str, Any]:
        """
        Returns real-time session status with remaining seconds.
        Automatically marks expired if the monotonic deadline has passed.
        """
        reward = self.db.get_youtube_reward(reward_id)
        if not reward:
            return {"success": False, "error": "Reward not found.", "code": "NOT_FOUND"}

        state = reward.get("state")
        if state == "ACTIVE" and reward.get("expiry_time"):
            expiry_dt = iso_to_dt(reward["expiry_time"])
            now_dt = now_ist()
            rem = int((expiry_dt - now_dt).total_seconds())
            if rem <= 0:
                self.expire_session(reward_id, client_id=reward.get("active_client_id") or "SYSTEM", termination_result="DEADLINE_EXPIRED")
                reward = self.db.get_youtube_reward(reward_id)
                rem = 0
            return {
                "success": True,
                "reward": reward,
                "state": reward["state"],
                "remaining_seconds": max(0, rem),
                "is_expired": rem <= 0,
            }

        return {
            "success": True,
            "reward": reward,
            "state": state,
            "remaining_seconds": 0 if state in ("CONSUMED", "EXPIRED") else int(reward.get("allowed_seconds") or 0),
            "is_expired": state in ("CONSUMED", "EXPIRED"),
        }

    def transfer_session(self,reward_id,from_client,to_client):
        source=str(from_client or '').upper();target=str(to_client or '').upper();current=now_ist()
        if not target or len(target)>160:return dict(success=False,code='CLIENT_REQUIRED',error='Choose a registered destination client.')
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone()
            if not row or row['state']!='ACTIVE':return dict(success=False,code='NO_ACTIVE_SESSION',error='No active session to transfer.')
            reward=dict(row)
            if reward['active_client_id']!=source:return dict(success=False,code='NOT_SESSION_OWNER',error='Only the current client can transfer this session.')
            remaining=int((iso_to_dt(reward['expiry_time'])-current).total_seconds())
            if remaining<=0:
                conn.execute("UPDATE youtube_video_rewards SET state='EXPIRED',termination_result='DEADLINE_EXPIRED' WHERE reward_id=?",(reward_id,))
                return dict(success=False,code='SESSION_EXPIRED',error='The deadline has passed.')
            conn.execute('UPDATE youtube_video_rewards SET active_client_id=?,updated_at=? WHERE reward_id=?',(target,dt_to_iso(current),reward_id))
            reward['active_client_id']=target
            return dict(success=True,reward=reward,transferred_from=source,transferred_to=target,remaining_seconds=remaining,expiry_time=reward['expiry_time'])

    def expire_session(self,reward_id,client_id='SYSTEM',termination_result='DEADLINE_EXPIRED'):
        current=now_ist()
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone()
            if not row:return dict(success=False,code='NOT_FOUND',error='Reward not found.')
            reward=dict(row)
            if reward['state'] in ('CONSUMED','EXPIRED','CANCELLED'):return dict(success=True,reward=reward,state=reward['state'])
            elapsed=bool(reward['expiry_time'] and iso_to_dt(reward['expiry_time'])<=current)
            if not elapsed and reward['active_client_id']!=str(client_id).upper():return dict(success=False,code='NOT_SESSION_OWNER',error='Only the current client can end this session.')
            if termination_result!='USER_COMPLETED' and not elapsed:return dict(success=False,code='DEADLINE_NOT_REACHED',error='The immutable deadline has not been reached.')
            state='EXPIRED' if elapsed else 'CONSUMED'
            conn.execute('UPDATE youtube_video_rewards SET state=?,termination_result=?,updated_at=? WHERE reward_id=?',(state,termination_result,dt_to_iso(current),reward_id))
            reward=dict(conn.execute('SELECT * FROM youtube_video_rewards WHERE reward_id=?',(reward_id,)).fetchone())
            return dict(success=True,reward=reward,state=state)
