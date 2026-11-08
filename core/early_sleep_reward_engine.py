"""StrictRecords — Early Sleep and Early Wake-Up Bonus: One-Time YouTube Video Reward.
===================================================================================
Authoritative coordination for the Early Sleep Reward (EARLY_SLEEP_ONE_VIDEO):
1. Eligibility:
   - Condition A (Early Sleep): Actual sleep onset strictly before 12:15 AM (00:15) Asia/Kolkata.
     Sleep onset >= 12:15 AM does not qualify.
   - Condition B (Early Wake-Up): Actual final wake-up strictly before 9:30 AM (09:30) Asia/Kolkata.
     Wake-up >= 9:30 AM does not qualify.
   - Both conditions must be satisfied in the same overnight sleep episode.
   - Midnight crossing handled correctly: associated with the calendar date on which the user wakes up.
2. Verified Sleep Source:
   - Mi Fitness / connected wearable sleep data.
   - Preserves actual sleep onset and wake-up times; never fabricates or assumes sleep from phone lock.
   - Unverified or missing sleep data enters SLEEP_DATA_PENDING or SLEEP_VERIFICATION_PENDING.
3. Entitlement:
   - Exactly ONE YouTube video reward (EARLY_SLEEP_ONE_VIDEO).
   - Independent of the 80-point threshold. Does not require 80 points.
   - Zero points added or deducted.
   - Does not extend 20-minute rewards or grant unlimited access.
   - Bound to exactly one selected YouTube video ID.
   - Watched to completion -> REWARD_CONSUMED, YouTube locked immediately.
   - Cannot replay the completed video.
   - Unused reward expires at the end of the wake-up date (23:59:59 IST) without carrying forward.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import zoneinfo

from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist

logger = logging.getLogger("EarlySleepRewardEngine")

REWARD_TYPE = "EARLY_SLEEP_ONE_VIDEO"

# Cutoff constants in local Asia/Kolkata time
SLEEP_ONSET_CUTOFF_HOUR = 0     # 12:00 AM midnight
SLEEP_ONSET_CUTOFF_MINUTE = 15   # 12:15 AM cutoff (onset must be < 00:15)
WAKE_UP_CUTOFF_HOUR = 9          # 9:00 AM
WAKE_UP_CUTOFF_MINUTE = 30       # 9:30 AM cutoff (wake-up must be < 09:30)

YOUTUBE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def extract_canonical_video_id(url_or_id: str) -> Optional[str]:
    """Extracts canonical 11-character YouTube video ID."""
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


def parse_clock_time(time_str: str) -> Optional[Tuple[int, int]]:
    """
    Parses '11:30 PM', '12:14 AM', '23:30', '07:00:00' into (hour_24, minute).
    """
    if not time_str or not isinstance(time_str, str):
        return None
    s = time_str.strip().upper()

    # Match 12-hour format e.g. 11:30 PM, 12:14 AM
    m12 = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)$", s)
    if m12:
        h = int(m12.group(1))
        m = int(m12.group(2))
        meridiem = m12.group(3)
        if meridiem == "PM" and h < 12:
            h += 12
        elif meridiem == "AM" and h == 12:
            h = 0
        return (h, m)

    # Match 24-hour format e.g. 23:30, 07:00, 00:14
    m24 = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", s)
    if m24:
        h = int(m24.group(1))
        m = int(m24.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)

    # Try full ISO datetime
    try:
        dt = iso_to_dt(time_str)
        if dt.tzinfo:
            dt = dt.astimezone(IST)
        return (dt.hour, dt.minute)
    except Exception:
        pass

    return None


def resolve_sleep_episode_datetimes(
    sleep_onset_raw: str,
    wake_up_raw: str,
    wake_date_str: str,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Resolves actual sleep onset and wake-up into datetime instances in Asia/Kolkata.
    Associates the overnight sleep episode with the wake_date.
    
    If sleep_onset is e.g. 11:30 PM (>= 18:00), it occurred on the day preceding wake_date.
    If sleep_onset is e.g. 12:10 AM (< 18:00), it occurred on wake_date.
    Wake-up always occurs on wake_date.
    """
    wake_date = datetime.strptime(wake_date_str, "%Y-%m-%d").date()
    prev_date = wake_date - timedelta(days=1)

    onset_dt: Optional[datetime] = None
    wake_dt: Optional[datetime] = None

    # Check if sleep_onset_raw is already full ISO string
    if "T" in sleep_onset_raw:
        try:
            parsed = iso_to_dt(sleep_onset_raw)
            onset_dt = parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
        except Exception:
            pass

    if onset_dt is None:
        onset_time = parse_clock_time(sleep_onset_raw)
        if onset_time:
            h, m = onset_time
            # Times from 18:00 to 23:59 represent sleep starting the night before wake_date
            # Times from 00:00 to 17:59 represent sleep starting on wake_date
            odate = prev_date if h >= 18 else wake_date
            onset_dt = datetime(odate.year, odate.month, odate.day, h, m, 0, tzinfo=IST)

    # Check if wake_up_raw is already full ISO string
    if "T" in wake_up_raw:
        try:
            parsed = iso_to_dt(wake_up_raw)
            wake_dt = parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
        except Exception:
            pass

    if wake_dt is None:
        wake_time = parse_clock_time(wake_up_raw)
        if wake_time:
            h, m = wake_time
            wake_dt = datetime(wake_date.year, wake_date.month, wake_date.day, h, m, 0, tzinfo=IST)

    return (onset_dt, wake_dt)


def format_time_12h(dt: datetime) -> str:
    """Formats a datetime to 12-hour AM/PM string, e.g. '11:30 PM'."""
    h = dt.hour % 12
    h = 12 if h == 0 else h
    am_pm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.minute:02d} {am_pm}"


def evaluate_early_sleep_eligibility(
    sleep_onset_raw: str,
    wake_up_raw: str,
    wake_date_str: str,
) -> Dict[str, Any]:
    """
    Evaluates Condition A and Condition B for Early Sleep Reward:
    - Condition A: actual sleep onset < 12:15 AM (00:15) local time.
      Onset >= 00:15 does NOT qualify.
    - Condition B: actual wake-up < 9:30 AM (09:30) local time.
      Wake-up >= 09:30 does NOT qualify.
    """
    onset_dt, wake_dt = resolve_sleep_episode_datetimes(sleep_onset_raw, wake_up_raw, wake_date_str)
    if not onset_dt or not wake_dt:
        return {
            "eligible": False,
            "status": "SLEEP_DATA_PENDING",
            "reason": "Sleep onset or wake-up timestamps could not be verified.",
            "condition_a_met": False,
            "condition_b_met": False,
            "onset_formatted": None,
            "wake_formatted": None,
            "actual_sleep_start": None,
            "actual_wake_time": None,
        }

    wake_date = datetime.strptime(wake_date_str, "%Y-%m-%d").date()

    # Sleep onset cutoff is wake_date at 00:15:00
    onset_cutoff = datetime(wake_date.year, wake_date.month, wake_date.day,
                            SLEEP_ONSET_CUTOFF_HOUR, SLEEP_ONSET_CUTOFF_MINUTE, 0, tzinfo=IST)

    # Wake-up cutoff is wake_date at 09:30:00
    wake_cutoff = datetime(wake_date.year, wake_date.month, wake_date.day,
                           WAKE_UP_CUTOFF_HOUR, WAKE_UP_CUTOFF_MINUTE, 0, tzinfo=IST)

    # Condition A: onset strictly before 00:15
    cond_a = onset_dt < onset_cutoff

    # Condition B: wake-up strictly before 09:30
    cond_b = wake_dt < wake_cutoff

    onset_disp = format_time_12h(onset_dt)
    wake_disp = format_time_12h(wake_dt)

    eligible = cond_a and cond_b
    if eligible:
        status = "ELIGIBLE"
        reason = f"Verified: Sleep onset {onset_disp} is before 12:15 AM and wake-up {wake_disp} is before 9:30 AM."
    elif not cond_a and not cond_b:
        status = "NOT_ELIGIBLE"
        reason = f"Sleep onset {onset_disp} (>= 12:15 AM) and wake-up {wake_disp} (>= 9:30 AM) both missed early sleep boundaries."
    elif not cond_a:
        status = "NOT_ELIGIBLE"
        reason = f"Sleep onset {onset_disp} is at or after the 12:15 AM cutoff (must be before 12:15 AM)."
    else:
        status = "NOT_ELIGIBLE"
        reason = f"Wake-up time {wake_disp} is at or after the 9:30 AM cutoff (must be before 9:30 AM)."

    return {
        "eligible": eligible,
        "status": status,
        "reason": reason,
        "condition_a_met": cond_a,
        "condition_b_met": cond_b,
        "onset_formatted": onset_disp,
        "wake_formatted": wake_disp,
        "actual_sleep_start": dt_to_iso(onset_dt),
        "actual_wake_time": dt_to_iso(wake_dt),
    }


def ensure_early_sleep_rewards_table(conn: Any) -> None:
    """Creates the early_sleep_video_rewards table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS early_sleep_video_rewards (
            reward_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            sleep_session_id TEXT NOT NULL,
            wake_date TEXT NOT NULL UNIQUE,
            actual_sleep_start TEXT NOT NULL,
            actual_wake_time TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            sync_timestamp TEXT,
            eligibility_status TEXT NOT NULL,
            reward_status TEXT NOT NULL,
            selected_video_id TEXT,
            selected_video_title TEXT,
            video_duration_seconds INTEGER,
            consumption_timestamp TEXT,
            expiration_timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_early_sleep_wake_date ON early_sleep_video_rewards(wake_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_early_sleep_account_status ON early_sleep_video_rewards(account_id, reward_status)")


class EarlySleepRewardEngine:
    """Authoritative lifecycle and session coordinator for Early Sleep Rewards."""

    def __init__(self, db: Any):
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            with self.db._connect() as conn:
                ensure_early_sleep_rewards_table(conn)
        except Exception as e:
            logger.warning("Could not ensure early_sleep_video_rewards table: %s", e)

    def evaluate_and_grant_reward(
        self,
        account_id: str,
        wake_date: str,
        sleep_onset_raw: str,
        wake_up_raw: str,
        source_identity: str = "Mi Fitness Wearable",
        sleep_session_id: Optional[str] = None,
        sync_timestamp: Optional[str] = None,
        verified: bool = True,
    ) -> Dict[str, Any]:
        """
        Evaluates sleep data and creates or returns the single EARLY_SLEEP_ONE_VIDEO entitlement.
        Idempotent: only one reward per wake_date. Repeated syncs do not create duplicates.
        """
        now = now_ist()
        now_iso = dt_to_iso(now)
        sess_id = sleep_session_id or f"sleep_{wake_date}_{account_id}"
        sync_ts = sync_timestamp or now_iso

        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_early_sleep_rewards_table(conn)

            # 1. Check existing record for wake_date
            row = conn.execute(
                "SELECT * FROM early_sleep_video_rewards WHERE wake_date=?",
                (wake_date,)
            ).fetchone()

            if row:
                rec = dict(row)
                # Check expiration
                exp_dt = iso_to_dt(rec["expiration_timestamp"])
                if rec["reward_status"] not in ("REWARD_CONSUMED", "REVOKED") and now > exp_dt:
                    conn.execute(
                        "UPDATE early_sleep_video_rewards SET reward_status='REWARD_EXPIRED', updated_at=? WHERE reward_id=?",
                        (now_iso, rec["reward_id"])
                    )
                    rec["reward_status"] = "REWARD_EXPIRED"
                return {"success": True, "reward": rec, "idempotent": True, "eligible": rec["eligibility_status"] == "ELIGIBLE"}

            # If not verified by connected wearable/process
            if not verified:
                return {
                    "success": False,
                    "status": "SLEEP_VERIFICATION_PENDING",
                    "reason": "Sleep data is pending verification.",
                    "eligible": False,
                }

            # 2. Evaluate eligibility
            eval_res = evaluate_early_sleep_eligibility(sleep_onset_raw, wake_up_raw, wake_date)
            elig_status = eval_res["status"]
            is_eligible = eval_res["eligible"]

            # Expiration timestamp: 23:59:59 IST of wake_date
            wake_d = datetime.strptime(wake_date, "%Y-%m-%d").date()
            exp_dt = datetime(wake_d.year, wake_d.month, wake_d.day, 23, 59, 59, tzinfo=IST)
            exp_iso = dt_to_iso(exp_dt)

            reward_status = "REWARD_AVAILABLE" if is_eligible else "NOT_ELIGIBLE"
            if is_eligible and now > exp_dt:
                reward_status = "REWARD_EXPIRED"

            reward_id = "esr_" + hashlib.sha256(f"{account_id}:{wake_date}:EARLY_SLEEP".encode()).hexdigest()[:32]

            conn.execute(
                """
                INSERT INTO early_sleep_video_rewards (
                    reward_id, account_id, sleep_session_id, wake_date,
                    actual_sleep_start, actual_wake_time, source_identity, sync_timestamp,
                    eligibility_status, reward_status, selected_video_id, selected_video_title,
                    video_duration_seconds, consumption_timestamp, expiration_timestamp,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    reward_id, account_id, sess_id, wake_date,
                    eval_res["actual_sleep_start"] or "",
                    eval_res["actual_wake_time"] or "",
                    source_identity, sync_ts,
                    elig_status, reward_status, exp_iso, now_iso, now_iso
                )
            )

            created_row = conn.execute(
                "SELECT * FROM early_sleep_video_rewards WHERE reward_id=?",
                (reward_id,)
            ).fetchone()
            rec = dict(created_row)
            return {"success": True, "reward": rec, "idempotent": False, "eligible": is_eligible, "eval_details": eval_res}

    def get_reward_by_wake_date(self, wake_date: str) -> Optional[Dict[str, Any]]:
        """Retrieves early sleep reward for a specific wake date."""
        with self.db._connect() as conn:
            ensure_early_sleep_rewards_table(conn)
            row = conn.execute(
                "SELECT * FROM early_sleep_video_rewards WHERE wake_date=?",
                (wake_date,)
            ).fetchone()
            if not row:
                return None
            rec = dict(row)
            # Check expiration
            now = now_ist()
            exp_dt = iso_to_dt(rec["expiration_timestamp"])
            if rec["reward_status"] not in ("REWARD_CONSUMED", "REVOKED") and now > exp_dt:
                conn.execute(
                    "UPDATE early_sleep_video_rewards SET reward_status='REWARD_EXPIRED', updated_at=? WHERE reward_id=?",
                    (dt_to_iso(now), rec["reward_id"])
                )
                rec["reward_status"] = "REWARD_EXPIRED"
            return rec

    def get_reward_by_id(self, reward_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves early sleep reward by its reward_id."""
        with self.db._connect() as conn:
            ensure_early_sleep_rewards_table(conn)
            row = conn.execute(
                "SELECT * FROM early_sleep_video_rewards WHERE reward_id=?",
                (reward_id,)
            ).fetchone()
            if not row:
                return None
            rec = dict(row)
            now = now_ist()
            exp_dt = iso_to_dt(rec["expiration_timestamp"])
            if rec["reward_status"] not in ("REWARD_CONSUMED", "REVOKED") and now > exp_dt:
                conn.execute(
                    "UPDATE early_sleep_video_rewards SET reward_status='REWARD_EXPIRED', updated_at=? WHERE reward_id=?",
                    (dt_to_iso(now), rec["reward_id"])
                )
                rec["reward_status"] = "REWARD_EXPIRED"
            return rec

    def get_todays_reward_status(self, account_id: str = "user_lok", target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns the real-time status of the early sleep reward for today (or target_date).
        If sleep data is not yet submitted, returns SLEEP_DATA_PENDING.
        """
        date_str = target_date or now_ist().strftime("%Y-%m-%d")
        reward = self.get_reward_by_wake_date(date_str)

        if not reward:
            # Check if sleep submission exists in local database
            onset_disp = None
            wake_disp = None
            source = "Mi Fitness Wearable"
            try:
                with self.db._connect() as conn:
                    cols = [r[1] for r in conn.execute("PRAGMA table_info(sleep_data_submissions)").fetchall()]
                    if cols:
                        s_row = conn.execute(
                            "SELECT * FROM sleep_data_submissions WHERE report_date=? AND account_id=?",
                            (date_str, account_id)
                        ).fetchone()
                        if s_row:
                            s_dict = dict(s_row)
                            if s_dict.get("sleep_onset") and s_dict.get("wake_up"):
                                return self.evaluate_and_grant_reward(
                                    account_id=account_id,
                                    wake_date=date_str,
                                    sleep_onset_raw=s_dict["sleep_onset"],
                                    wake_up_raw=s_dict["wake_up"],
                                    source_identity="Mi Fitness Wearable",
                                    sleep_session_id=s_dict.get("submission_id"),
                                    sync_timestamp=s_dict.get("created_at"),
                                    verified=s_dict.get("status") in ("APPROVED", "VERIFIED"),
                                )
            except Exception:
                pass

            return {
                "success": True,
                "wake_date": date_str,
                "reward_id": None,
                "reward_type": REWARD_TYPE,
                "eligibility_status": "SLEEP_DATA_PENDING",
                "reward_status": "SLEEP_DATA_PENDING",
                "sleep_onset": None,
                "wake_up": None,
                "verification_source": source,
                "summary": "Sleep Data Pending · Connect Mi Fitness or submit sleep report",
                "reward": None,
                "is_available": False,
                "is_active": False,
                "is_consumed": False,
            }

        # Format display fields
        onset_disp = None
        wake_disp = None
        if reward.get("actual_sleep_start"):
            try:
                onset_disp = format_time_12h(iso_to_dt(reward["actual_sleep_start"]))
            except Exception:
                onset_disp = reward.get("actual_sleep_start")
        if reward.get("actual_wake_time"):
            try:
                wake_disp = format_time_12h(iso_to_dt(reward["actual_wake_time"]))
            except Exception:
                wake_disp = reward.get("actual_wake_time")

        st = reward["reward_status"]
        is_avail = st in ("REWARD_AVAILABLE", "VIDEO_SELECTED")
        is_active = st in ("VIDEO_SELECTED", "VIDEO_PLAYING", "VIDEO_PAUSED")
        is_consumed = st == "REWARD_CONSUMED"

        summary = (
            "CONSUMED – YOUTUBE LOCKED" if is_consumed else
            "ONE YOUTUBE VIDEO AVAILABLE" if is_avail else
            "EXPIRED" if st == "REWARD_EXPIRED" else
            "NOT ELIGIBLE" if st == "NOT_ELIGIBLE" else
            st
        )

        return {
            "success": True,
            "wake_date": date_str,
            "reward_id": reward["reward_id"],
            "reward_type": REWARD_TYPE,
            "eligibility_status": reward["eligibility_status"],
            "reward_status": st,
            "sleep_onset": onset_disp,
            "wake_up": wake_disp,
            "verification_source": reward.get("source_identity", "Mi Fitness Wearable"),
            "summary": summary,
            "selected_video_id": reward.get("selected_video_id"),
            "selected_video_title": reward.get("selected_video_title"),
            "reward": reward,
            "is_available": is_avail,
            "is_active": is_active,
            "is_consumed": is_consumed,
        }

    def bind_video(
        self,
        reward_id: str,
        video_url_or_id: str,
        title: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Binds the Early Sleep Reward to exactly one canonical YouTube video ID.
        Rejects binding if already bound to a different video ID.
        """
        video_id = extract_canonical_video_id(video_url_or_id)
        if not video_id:
            return {"success": False, "code": "INVALID_YOUTUBE_URL", "error": "Select a valid YouTube video URL or ID."}

        now = now_ist()
        now_iso = dt_to_iso(now)

        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_early_sleep_rewards_table(conn)

            row = conn.execute("SELECT * FROM early_sleep_video_rewards WHERE reward_id=?", (reward_id,)).fetchone()
            if not row:
                return {"success": False, "code": "NOT_FOUND", "error": "Early Sleep Reward not found."}

            reward = dict(row)
            st = reward["reward_status"]
            if st in ("REWARD_CONSUMED", "REWARD_EXPIRED", "REVOKED", "NOT_ELIGIBLE"):
                return {"success": False, "code": "REWARD_NOT_AVAILABLE", "error": f"This reward is {st} and cannot be used."}

            bound_id = reward.get("selected_video_id")
            if bound_id:
                if bound_id == video_id:
                    return {"success": True, "reward": reward, "video_id": video_id, "already_bound": True}
                return {
                    "success": False,
                    "code": "VIDEO_ALREADY_BOUND",
                    "error": f"This Early Sleep Reward is already locked to video '{bound_id}'. Only that single video can be played.",
                    "bound_video_id": bound_id,
                }

            conn.execute(
                """
                UPDATE early_sleep_video_rewards
                SET selected_video_id=?, selected_video_title=?, video_duration_seconds=?, reward_status='VIDEO_SELECTED', updated_at=?
                WHERE reward_id=?
                """,
                (video_id, title or "Selected YouTube Video", duration_seconds, now_iso, reward_id)
            )

            updated = dict(conn.execute("SELECT * FROM early_sleep_video_rewards WHERE reward_id=?", (reward_id,)).fetchone())
            return {"success": True, "reward": updated, "video_id": video_id, "already_bound": False}

    def record_playback_heartbeat(
        self,
        reward_id: str,
        video_id: str,
        playback_state: str = "PLAYING",
        current_time: float = 0.0,
        duration: float = 0.0,
        completed: bool = False,
    ) -> Dict[str, Any]:
        """
        Records video playback heartbeat for the single-video Early Sleep Reward.
        Once the video completes: marks REWARD_CONSUMED and locks YouTube immediately.
        """
        clean_vid = extract_canonical_video_id(video_id) or video_id.strip()
        now = now_ist()
        now_iso = dt_to_iso(now)

        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_early_sleep_rewards_table(conn)

            row = conn.execute("SELECT * FROM early_sleep_video_rewards WHERE reward_id=?", (reward_id,)).fetchone()
            if not row:
                return {"success": False, "code": "NOT_FOUND", "error": "Early Sleep Reward not found."}

            reward = dict(row)
            if reward["reward_status"] == "REWARD_CONSUMED":
                return {"success": True, "completed": True, "locked": True, "reward_status": "REWARD_CONSUMED"}

            bound_id = reward.get("selected_video_id")
            if bound_id and bound_id != clean_vid:
                return {
                    "success": False,
                    "code": "VIDEO_MISMATCH",
                    "error": f"Early Sleep Reward is locked to video '{bound_id}'. Video '{clean_vid}' is blocked.",
                    "locked": True,
                }

            # If completed flag sent, or current_time is at or near end of duration
            is_finished = completed or (duration > 15.0 and current_time >= duration - 2.5)

            if is_finished:
                conn.execute(
                    """
                    UPDATE early_sleep_video_rewards
                    SET reward_status='REWARD_CONSUMED', consumption_timestamp=?, updated_at=?
                    WHERE reward_id=?
                    """,
                    (now_iso, now_iso, reward_id)
                )
                updated = dict(conn.execute("SELECT * FROM early_sleep_video_rewards WHERE reward_id=?", (reward_id,)).fetchone())
                logger.info("Early Sleep Reward %s marked CONSUMED (video %s finished)", reward_id, clean_vid)
                return {
                    "success": True,
                    "completed": True,
                    "locked": True,
                    "reward_status": "REWARD_CONSUMED",
                    "reward": updated,
                }

            new_status = "VIDEO_PLAYING" if playback_state.upper() == "PLAYING" else "VIDEO_PAUSED"
            conn.execute(
                """
                UPDATE early_sleep_video_rewards
                SET reward_status=?, updated_at=?
                WHERE reward_id=?
                """,
                (new_status, now_iso, reward_id)
            )
            return {
                "success": True,
                "completed": False,
                "locked": False,
                "reward_status": new_status,
                "video_id": clean_vid,
            }

    def get_effective_early_sleep_decision(
        self,
        video_id: Optional[str] = None,
        account_id: str = "user_lok",
    ) -> Optional[Dict[str, Any]]:
        """
        Determines if YouTube access is permitted under the active Early Sleep Reward.
        Returns:
          - None: if no active or relevant early sleep reward exists (fall back to normal rules)
          - Dict with 'allowed': True/False and reasons
        """
        date_str = now_ist().strftime("%Y-%m-%d")
        reward = self.get_reward_by_wake_date(date_str)
        if not reward:
            return None

        st = reward["reward_status"]
        if st in ("NOT_ELIGIBLE", "SLEEP_DATA_PENDING", "SLEEP_VERIFICATION_PENDING"):
            return None

        clean_vid = extract_canonical_video_id(video_id) if video_id else None
        bound_vid = reward.get("selected_video_id")

        if st == "REWARD_CONSUMED":
            # Consumed today -> Must be blocked under this reward (cannot replay)
            return {
                "allowed": False,
                "restricted": True,
                "code": "EARLY_SLEEP_REWARD_CONSUMED",
                "reason": "Early Sleep Reward has already been consumed today. Replay is locked.",
                "statusText": "Early Sleep Reward consumed · YouTube is locked.",
                "reward_status": "REWARD_CONSUMED",
                "reward_id": reward["reward_id"],
            }

        if st == "REWARD_EXPIRED":
            return {
                "allowed": False,
                "restricted": True,
                "code": "EARLY_SLEEP_REWARD_EXPIRED",
                "reason": "Early Sleep Reward expired at midnight.",
                "statusText": "Early Sleep Reward expired.",
                "reward_status": "REWARD_EXPIRED",
                "reward_id": reward["reward_id"],
            }

        if st in ("REWARD_AVAILABLE", "VIDEO_SELECTED", "VIDEO_PLAYING", "VIDEO_PAUSED"):
            # If a video is already bound
            if bound_vid:
                if clean_vid and clean_vid == bound_vid:
                    return {
                        "allowed": True,
                        "restricted": False,
                        "temporary_exception": REWARD_TYPE,
                        "temporaryException": REWARD_TYPE,
                        "code": "EARLY_SLEEP_ONE_VIDEO",
                        "video_id": bound_vid,
                        "selected_video_id": bound_vid,
                        "title": reward.get("selected_video_title") or "Early Sleep Reward Video",
                        "reward_id": reward["reward_id"],
                        "reward_status": st,
                        "reason": f"Early Sleep Reward active for selected video '{bound_vid}'.",
                        "statusText": f"Early Sleep Reward: Single Video Allowed ('{bound_vid}')",
                    }
                elif clean_vid and clean_vid != bound_vid:
                    return {
                        "allowed": False,
                        "restricted": True,
                        "code": "EARLY_SLEEP_VIDEO_MISMATCH",
                        "reason": f"Early Sleep Reward is locked to selected video '{bound_vid}'. Other YouTube videos are blocked.",
                        "statusText": f"Locked to selected video '{bound_vid}' only.",
                        "selected_video_id": bound_vid,
                        "reward_id": reward["reward_id"],
                    }
                else:
                    # Generic YouTube page (e.g. youtube.com home), guide user to selected video
                    return {
                        "allowed": True,
                        "restricted": False,
                        "temporary_exception": REWARD_TYPE,
                        "temporaryException": REWARD_TYPE,
                        "code": "EARLY_SLEEP_ONE_VIDEO",
                        "video_id": bound_vid,
                        "selected_video_id": bound_vid,
                        "reward_id": reward["reward_id"],
                        "reward_status": st,
                        "reason": f"Early Sleep Reward active: open your selected video '{bound_vid}'.",
                        "statusText": f"Early Sleep Reward: bound to '{bound_vid}'",
                    }
            else:
                # No video bound yet; if user is on a specific video, bind it!
                if clean_vid:
                    self.bind_video(reward["reward_id"], clean_vid)
                    return {
                        "allowed": True,
                        "restricted": False,
                        "temporary_exception": REWARD_TYPE,
                        "temporaryException": REWARD_TYPE,
                        "code": "EARLY_SLEEP_ONE_VIDEO",
                        "video_id": clean_vid,
                        "selected_video_id": clean_vid,
                        "reward_id": reward["reward_id"],
                        "reward_status": "VIDEO_SELECTED",
                        "reason": f"Early Sleep Reward bound to video '{clean_vid}'. Playback authorized.",
                        "statusText": f"Early Sleep Reward: bound to '{clean_vid}'",
                    }
                # User on youtube.com homepage to pick a video
                return {
                    "allowed": True,
                    "restricted": False,
                    "temporary_exception": REWARD_TYPE,
                    "temporaryException": REWARD_TYPE,
                    "code": "EARLY_SLEEP_ONE_VIDEO",
                    "reward_id": reward["reward_id"],
                    "reward_status": st,
                    "reason": "Early Sleep Reward available: select one YouTube video to watch.",
                    "statusText": "Early Sleep Reward: Select one video",
                }

        return None
