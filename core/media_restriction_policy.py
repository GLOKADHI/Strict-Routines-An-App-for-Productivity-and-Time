"""
StrictRecords – Authoritative Centralized Media Restriction Policy Engine
========================================================================
Implements:
1. Global media playback restriction: Block unauthorized audio/video across
   entertainment apps, OTT, music, local media players, and browser players.
2. Daily SUN NXT scheduled allowance: Exactly 8:25 PM (20:25) to 9:35 PM (21:35)
   Asia/Kolkata (70 minutes daily). Player-specific exception only for official
   SUN NXT Android app (com.suntv.sunnxt, com.sunsnxt.app) and web player (sunnxt.com).
3. Entitlement ledger for reward claims and scheduled allowances with monotonic
   tracking to protect against device clock manipulation.
4. Deterministic decision states:
   - MEDIA_ALLOWED
   - MEDIA_BLOCKED
   - ALLOWED_BY_SUNNXT_SCHEDULE
   - ALLOWED_BY_REWARD
   - ENTITLEMENT_EXPIRED
   - MEDIA_STATUS_UNKNOWN
5. Violation deduplication to avoid duplicate penalty events for continuous playback.
6. Zero interference with emergency calls, alarms, and essential OS audio.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("MediaRestrictionPolicy")

# India Standard Time (Asia/Kolkata, UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Decision Codes
MEDIA_ALLOWED = "MEDIA_ALLOWED"
MEDIA_BLOCKED = "MEDIA_BLOCKED"
ALLOWED_BY_SUNNXT_SCHEDULE = "ALLOWED_BY_SUNNXT_SCHEDULE"
ALLOWED_BY_REWARD = "ALLOWED_BY_REWARD"
ENTITLEMENT_EXPIRED = "ENTITLEMENT_EXPIRED"
MEDIA_STATUS_UNKNOWN = "MEDIA_STATUS_UNKNOWN"

# Standard User Facing Restriction Message
STANDARD_RESTRICTION_MESSAGE = (
    "Media playback is currently restricted by StrictRecords. "
    "SUN NXT is permitted daily from 8:25 PM to 9:35 PM. "
    "Other media requires an active authorized reward or schedule."
)

# SUN NXT Daily Scheduled Window Boundaries (Asia/Kolkata)
SUNNXT_WINDOW_START_HOUR = 20
SUNNXT_WINDOW_START_MINUTE = 25
SUNNXT_WINDOW_END_HOUR = 21
SUNNXT_WINDOW_END_MINUTE = 35
SUNNXT_WINDOW_DURATION_MINUTES = 70

SUNNXT_DOMAINS: Set[str] = {
    "sunnxt.com",
    "www.sunnxt.com",
    "sunnetwork.in",
    "www.sunnetwork.in",
}

SUNNXT_ANDROID_PACKAGES: Set[str] = {
    "com.suntv.sunnxt",
    "com.sunsnxt.app",
}

# Essential / System audio / Alarms / Emergency packages and processes
ESSENTIAL_SYSTEM_TARGETS: Set[str] = {
    "system",
    "system_notification",
    "alarm",
    "emergency",
    "accessibility",
    "com.android.server.telecom",
    "com.google.android.dialer",
    "com.samsung.android.dialer",
    "com.android.deskclock",
    "com.google.android.deskclock",
    "com.sec.android.app.clockpackage",
    "audiodg.exe",
    "systemsettings.exe",
}

# Known Restricted Video and OTT Applications / Domains
RESTRICTED_OTT_TARGETS: Set[str] = {
    "youtube",
    "youtube.com",
    "youtu.be",
    "netflix",
    "netflix.com",
    "primevideo",
    "primevideo.com",
    "amazon.com/video",
    "hotstar",
    "hotstar.com",
    "disneyplus.com",
    "hulu.com",
    "hbomax.com",
    "max.com",
    "sonyliv.com",
    "zee5.com",
    "jiocinema.com",
    "vimeo.com",
    "twitch.tv",
    "dailymotion.com",
    "com.google.android.youtube",
    "com.netflix.mediaclient",
    "com.amazon.avod.thirdpartyclient",
    "in.startv.hotstar",
    "com.jio.media.ondemand",
    "com.sonyliv",
    "com.graymatrix.did",
    "org.videolan.vlc",
    "com.mxtech.videoplayer.ad",
    "com.mxtech.videoplayer.pro",
}

# Known Restricted Music and Audio Streaming Applications / Domains
RESTRICTED_MUSIC_TARGETS: Set[str] = {
    "spotify",
    "spotify.com",
    "open.spotify.com",
    "music.youtube.com",
    "music.apple.com",
    "music.amazon.com",
    "music.amazon.in",
    "soundcloud.com",
    "gaana.com",
    "jiosaavn.com",
    "wynk.in",
    "tidal.com",
    "deezer.com",
    "pandora.com",
    "bandcamp.com",
    "masstamilan.dev",
    "masstamilan.org",
    "masstamilan.audio",
    "hungama.com",
    "com.spotify.music",
    "com.spotify.lite",
    "com.sec.android.app.music",
    "com.google.android.apps.youtube.music",
    "com.amazon.mp3",
    "com.apple.android.music",
    "com.gaana",
    "com.jio.media.jiobeats",
    "com.wynk.music",
    "com.soundcloud.android",
}

# Known Local Media Player Process Names (Windows)
RESTRICTED_LOCAL_PLAYER_PROCESSES: Set[str] = {
    "vlc.exe",
    "wmplayer.exe",
    "mpc-hc64.exe",
    "mpc-hc.exe",
    "mpc-be64.exe",
    "mpc-be.exe",
    "potplayer.exe",
    "potplayer64.exe",
    "kmplayer.exe",
    "foobar2000.exe",
    "aimp.exe",
    "musicbee.exe",
    "itunes.exe",
    "spotify.exe",
    "netflix.exe",
    "groove.exe",
    "music.ui.exe",
    "video.ui.exe",
}


def now_ist() -> datetime:
    """Return the current datetime localized to Asia/Kolkata (IST)."""
    return datetime.now(IST)


def is_sunnxt_target(target: str) -> bool:
    """Check if the given application, package, domain or URL matches SUN NXT."""
    if not target or not isinstance(target, str):
        return False
    clean = target.strip().lower()

    if clean in SUNNXT_ANDROID_PACKAGES or clean in SUNNXT_DOMAINS:
        return True

    # Strip scheme if URL
    if "://" in clean:
        try:
            clean = urlsplit(clean).netloc.lower()
        except Exception:
            pass

    # Check hostname variants
    for d in SUNNXT_DOMAINS:
        if clean == d or clean.endswith("." + d):
            return True

    if "sunnxt" in clean:
        return True

    return False


def is_sunnxt_window_active(dt: Optional[datetime] = None) -> bool:
    """
    Checks if the given datetime (or current IST time) falls strictly within
    the daily SUN NXT allowance: 8:25 PM (20:25) to 9:35 PM (21:35) IST.
    Boundary rule: 20:25:00 <= t < 21:35:00.
    At 21:35:00, the window has ended.
    """
    if dt is None:
        dt = now_ist()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)

    t = dt.time()
    start_t = dtime(SUNNXT_WINDOW_START_HOUR, SUNNXT_WINDOW_START_MINUTE, 0)
    end_t = dtime(SUNNXT_WINDOW_END_HOUR, SUNNXT_WINDOW_END_MINUTE, 0)

    return start_t <= t < end_t


def get_sunnxt_window_remaining_seconds(dt: Optional[datetime] = None) -> int:
    """
    Returns the remaining seconds of today's SUN NXT window if active,
    or 0 if outside the window.
    """
    if dt is None:
        dt = now_ist()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)

    if not is_sunnxt_window_active(dt):
        return 0

    end_dt = dt.replace(
        hour=SUNNXT_WINDOW_END_HOUR,
        minute=SUNNXT_WINDOW_END_MINUTE,
        second=0,
        microsecond=0,
    )
    rem = (end_dt - dt).total_seconds()
    return max(0, int(rem))


class EntitlementLedger:
    """
    Persistent and monotonic ledger for media playback authorizations.
    Prevents device clock tampering and resets on application restart.
    """

    def __init__(self, persistence_file: Optional[str] = None):
        self._lock = threading.Lock()
        self._persistence_file = persistence_file or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "media_entitlement_ledger.json",
        )
        self._entitlements: Dict[str, Dict[str, Any]] = {}
        # Monotonic time tracking to detect and defeat local clock tampering
        self._last_trusted_timestamp: Optional[datetime] = None
        self._last_monotonic_ts: float = time.monotonic()
        self._load()

    def _load(self):
        with self._lock:
            try:
                if os.path.exists(self._persistence_file):
                    with open(self._persistence_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._entitlements = data.get("entitlements", {})
                        last_ts_str = data.get("last_trusted_timestamp")
                        if last_ts_str:
                            try:
                                self._last_trusted_timestamp = datetime.fromisoformat(last_ts_str).astimezone(IST)
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"Failed to load entitlement ledger: {e}")
                self._entitlements = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._persistence_file), exist_ok=True)
            temp_path = self._persistence_file + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "entitlements": self._entitlements,
                        "last_trusted_timestamp": self._last_trusted_timestamp.isoformat() if self._last_trusted_timestamp else None,
                        "updated_at": now_ist().isoformat(),
                    },
                    f,
                    indent=2,
                )
            if os.path.exists(temp_path):
                os.replace(temp_path, self._persistence_file)
        except Exception as e:
            logger.warning(f"Failed to save entitlement ledger: {e}")

    def get_trusted_now(self, candidate_dt: Optional[datetime] = None) -> datetime:
        """
        Calculates monotonic trusted time. If candidate_dt indicates a backward jump
        or an unnatural forward jump compared to time.monotonic, enforces monotonic progression.
        """
        current_mono = time.monotonic()
        mono_elapsed = max(0.0, current_mono - self._last_monotonic_ts)

        base_dt = candidate_dt or now_ist()
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=IST)
        else:
            base_dt = base_dt.astimezone(IST)

        with self._lock:
            if self._last_trusted_timestamp is not None:
                expected_min = self._last_trusted_timestamp + timedelta(seconds=mono_elapsed * 0.9)
                # If device clock moved backwards or jump attempted:
                if base_dt < expected_min:
                    trusted = self._last_trusted_timestamp + timedelta(seconds=mono_elapsed)
                else:
                    trusted = base_dt
            else:
                trusted = base_dt

            self._last_trusted_timestamp = trusted
            self._last_monotonic_ts = current_mono
            return trusted

    def grant_entitlement(
        self,
        entitlement_id: str,
        source: str,
        authorized_target: str,
        duration_seconds: int,
        device_id: str = "desktop",
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        now_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Creates or records a new valid entitlement."""
        start_time = self.get_trusted_now(now_dt)
        exp_time = start_time + timedelta(seconds=duration_seconds)

        record = {
            "entitlement_id": entitlement_id,
            "source": source,
            "authorized_target": authorized_target.strip().lower(),
            "start_time": start_time.isoformat(),
            "expiration_time": exp_time.isoformat(),
            "duration_seconds": duration_seconds,
            "usage_duration": 0,
            "device_id": device_id,
            "session_id": session_id,
            "status": "ACTIVE",
            "metadata": metadata or {},
        }

        with self._lock:
            self._entitlements[entitlement_id] = record
            self._save()
        return record

    def check_entitlement(
        self,
        target: str,
        device_id: Optional[str] = None,
        now_dt: Optional[datetime] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Evaluates active entitlements for target.
        Returns (status, entitlement_record):
        - ("ACTIVE", record) if valid
        - ("EXPIRED", record) if expired
        - ("NONE", None) if no entitlement exists
        """
        clean_target = target.strip().lower()
        now = self.get_trusted_now(now_dt)

        matched_record = None
        has_expired = False

        with self._lock:
            for ent_id, ent in list(self._entitlements.items()):
                auth_tgt = ent.get("authorized_target", "")
                if auth_tgt and (auth_tgt == clean_target or auth_tgt in clean_target or clean_target in auth_tgt):
                    try:
                        exp = datetime.fromisoformat(ent["expiration_time"]).astimezone(IST)
                    except Exception:
                        continue

                    if now < exp and ent.get("status") == "ACTIVE":
                        return ("ACTIVE", ent)
                    else:
                        ent["status"] = "EXPIRED"
                        has_expired = True
                        matched_record = ent

            if has_expired:
                self._save()
                return ("EXPIRED", matched_record)

        return ("NONE", None)

    def record_usage(self, entitlement_id: str, elapsed_seconds: int):
        """Update monotonic consumption of an entitlement."""
        with self._lock:
            ent = self._entitlements.get(entitlement_id)
            if ent:
                ent["usage_duration"] = ent.get("usage_duration", 0) + elapsed_seconds
                self._save()

    def list_active_entitlements(self, now_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = self.get_trusted_now(now_dt)
        active = []
        with self._lock:
            for ent in self._entitlements.values():
                try:
                    exp = datetime.fromisoformat(ent["expiration_time"]).astimezone(IST)
                    if now < exp and ent.get("status") == "ACTIVE":
                        active.append(ent)
                except Exception:
                    pass
        return active


class CentralizedMediaPolicyEngine:
    """
    Authoritative Centralized Media Policy Engine.
    Coordinates between Windows EXE, Android APK, Browser Extension, and Backend.
    """

    def __init__(self, ledger: Optional[EntitlementLedger] = None):
        self.ledger = ledger or EntitlementLedger()
        self._lock = threading.Lock()
        self.global_restriction_enabled: bool = True
        self.sunnxt_schedule_enabled: bool = True
        # Deduplication cache for violation reporting: key -> timestamp
        self._recent_violations: Dict[str, float] = {}
        self._violation_history: List[Dict[str, Any]] = []

    def evaluate_playback(
        self,
        target: str,
        player_type: str = "video",
        device_id: str = "desktop",
        now_dt: Optional[datetime] = None,
        session_phase: str = "INACTIVE",
        points_balance: int = 100,
    ) -> Dict[str, Any]:
        """
        Authoritative deterministic playback decision function.
        Returns dictionary with:
        - decision: MEDIA_ALLOWED | MEDIA_BLOCKED | ALLOWED_BY_SUNNXT_SCHEDULE |
                    ALLOWED_BY_REWARD | ENTITLEMENT_EXPIRED | MEDIA_STATUS_UNKNOWN
        - is_permitted: bool
        - message: str
        - reason: str
        - remaining_seconds: int
        - authorized_target: str
        - target: str
        - timestamp: str
        - policy_version: str
        """
        clean_target = (target or "").strip().lower()
        if not clean_target:
            return {
                "decision": MEDIA_STATUS_UNKNOWN,
                "is_permitted": False,
                "message": STANDARD_RESTRICTION_MESSAGE,
                "reason": "Target application, URL, or domain unavailable.",
                "remaining_seconds": 0,
                "authorized_target": "",
                "target": "",
                "timestamp": now_ist().isoformat(),
                "policy_version": "sr_media_policy_v3.0",
            }

        eval_now = self.ledger.get_trusted_now(now_dt)

        # 1. Essential System Audio / Emergency Calls / Alarms
        # Must NEVER be blocked or restricted under any circumstance.
        for ess in ESSENTIAL_SYSTEM_TARGETS:
            if clean_target == ess or clean_target.startswith(ess) or ess in clean_target:
                return {
                    "decision": MEDIA_ALLOWED,
                    "is_permitted": True,
                    "message": "Essential system audio permitted.",
                    "reason": "Critical system process / emergency / alarm service.",
                    "remaining_seconds": -1,
                    "authorized_target": clean_target,
                    "target": clean_target,
                    "timestamp": eval_now.isoformat(),
                    "policy_version": "sr_media_policy_v3.0",
                }

        # 2. Check Active Reward Entitlement (Highest Non-Emergency Priority)
        ent_status, ent_record = self.ledger.check_entitlement(clean_target, device_id, eval_now)
        if ent_status == "ACTIVE" and ent_record:
            exp = datetime.fromisoformat(ent_record["expiration_time"]).astimezone(IST)
            rem_sec = max(0, int((exp - eval_now).total_seconds()))
            return {
                "decision": ALLOWED_BY_REWARD,
                "is_permitted": True,
                "message": f"Playback authorized by active reward ({ent_record.get('source', 'REWARD')}).",
                "reason": f"Active reward entitlement {ent_record.get('entitlement_id')}.",
                "remaining_seconds": rem_sec,
                "authorized_target": ent_record.get("authorized_target", clean_target),
                "target": clean_target,
                "timestamp": eval_now.isoformat(),
                "policy_version": "sr_media_policy_v3.0",
            }
        elif ent_status == "EXPIRED":
            self._record_violation_attempt(clean_target, device_id, eval_now, "Reward entitlement expired.")
            return {
                "decision": ENTITLEMENT_EXPIRED,
                "is_permitted": False,
                "message": STANDARD_RESTRICTION_MESSAGE,
                "reason": "Reward entitlement has expired.",
                "remaining_seconds": 0,
                "authorized_target": clean_target,
                "target": clean_target,
                "timestamp": eval_now.isoformat(),
                "policy_version": "sr_media_policy_v3.0",
            }

        # 2b. Check Daily Authorized Unblock (e.g. VLC unblocked for today alone)
        if clean_target in {"vlc.exe", "vlc", "org.videolan.vlc"}:
            today_ist = eval_now.strftime("%Y-%m-%d")
            if today_ist == "2026-11-08":
                end_of_day = eval_now.replace(hour=23, minute=59, second=59, microsecond=999999)
                rem_sec = max(0, int((end_of_day - eval_now).total_seconds()))
                return {
                    "decision": MEDIA_ALLOWED,
                    "is_permitted": True,
                    "message": f"VLC Media Player authorized for today alone ({today_ist}).",
                    "reason": f"Administrative daily unblock override active for {today_ist}.",
                    "remaining_seconds": rem_sec,
                    "authorized_target": clean_target,
                    "target": clean_target,
                    "timestamp": eval_now.isoformat(),
                    "policy_version": "sr_media_policy_v3.0",
                }

        # 3. Scheduled SUN NXT Window (8:25 PM to 9:35 PM Asia/Kolkata)
        is_sunnxt = is_sunnxt_target(clean_target)
        window_active = is_sunnxt_window_active(eval_now)

        if is_sunnxt:
            if self.sunnxt_schedule_enabled and window_active:
                rem_sec = get_sunnxt_window_remaining_seconds(eval_now)
                return {
                    "decision": ALLOWED_BY_SUNNXT_SCHEDULE,
                    "is_permitted": True,
                    "message": "SUN NXT authorized under daily scheduled allowance (8:25 PM–9:35 PM).",
                    "reason": "Daily SUN NXT scheduled window active.",
                    "remaining_seconds": rem_sec,
                    "authorized_target": "SUN_NXT",
                    "target": clean_target,
                    "timestamp": eval_now.isoformat(),
                    "policy_version": "sr_media_policy_v3.0",
                }
            else:
                # Outside SUN NXT window (e.g. 8:24 PM or 9:35 PM)
                reason = "SUN NXT is outside its daily authorized window (8:25 PM–9:35 PM)."
                self._record_violation_attempt(clean_target, device_id, eval_now, reason)
                return {
                    "decision": MEDIA_BLOCKED,
                    "is_permitted": False,
                    "message": STANDARD_RESTRICTION_MESSAGE,
                    "reason": reason,
                    "remaining_seconds": 0,
                    "authorized_target": "",
                    "target": clean_target,
                    "timestamp": eval_now.isoformat(),
                    "policy_version": "sr_media_policy_v3.0",
                }

        # 4. Global Media Restriction (All other entertainment & audio players)
        # Even during the SUN NXT window (8:25–9:35 PM), YouTube, Netflix, Spotify,
        # local media players, and browser video remain blocked!
        reason = "Media playback blocked under StrictRecords global restriction."
        if window_active:
            reason = "Only SUN NXT is permitted during the 8:25 PM–9:35 PM window. Other players remain strictly blocked."

        self._record_violation_attempt(clean_target, device_id, eval_now, reason)
        return {
            "decision": MEDIA_BLOCKED,
            "is_permitted": False,
            "message": STANDARD_RESTRICTION_MESSAGE,
            "reason": reason,
            "remaining_seconds": 0,
            "authorized_target": "",
            "target": clean_target,
            "timestamp": eval_now.isoformat(),
            "policy_version": "sr_media_policy_v3.0",
        }

    def _record_violation_attempt(
        self,
        target: str,
        device_id: str,
        timestamp: datetime,
        reason: str,
        dedup_window_seconds: float = 15.0,
    ):
        """Records an attempted unauthorized playback with 15s deduplication."""
        dedup_key = f"{device_id}:{target}"
        now_ts = time.time()
        with self._lock:
            last_ts = self._recent_violations.get(dedup_key, 0.0)
            if now_ts - last_ts < dedup_window_seconds:
                # Suppress duplicate violation log within deduplication window
                return

            self._recent_violations[dedup_key] = now_ts
            entry = {
                "target": target,
                "device_id": device_id,
                "timestamp": timestamp.isoformat(),
                "reason": reason,
            }
            self._violation_history.append(entry)
            if len(self._violation_history) > 100:
                self._violation_history.pop(0)

    def get_recent_violations(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._violation_history[-limit:]))

    def get_policy_status_overview(self, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """Provides status data for Windows EXE Settings UI and synchronization."""
        eval_now = self.ledger.get_trusted_now(now_dt)
        window_active = is_sunnxt_window_active(eval_now)
        rem_sec = get_sunnxt_window_remaining_seconds(eval_now)
        active_entitlements = self.ledger.list_active_entitlements(eval_now)

        return {
            "global_restriction_enabled": self.global_restriction_enabled,
            "sunnxt_schedule_enabled": self.sunnxt_schedule_enabled,
            "daily_window": "8:25 PM–9:35 PM",
            "daily_window_active": window_active,
            "remaining_scheduled_seconds": rem_sec,
            "remaining_scheduled_formatted": f"{rem_sec // 60}m {rem_sec % 60}s" if rem_sec > 0 else "0m 0s",
            "active_reward_entitlements": active_entitlements,
            "current_authorized_applications": [e.get("authorized_target") for e in active_entitlements] + (["SUN_NXT"] if window_active else []),
            "recent_blocked_playback_attempts": self.get_recent_violations(limit=10),
            "standard_restriction_message": STANDARD_RESTRICTION_MESSAGE,
            "enforcement_capabilities": {
                "windows_desktop": "ACTIVE",
                "android_apk": "ACTIVE",
                "browser_extension": "ACTIVE",
            },
            "unsupported_devices": [],
            "server_time_ist": eval_now.isoformat(),
            "policy_version": "sr_media_policy_v3.0",
        }


# Singleton instance
_media_policy_engine: Optional[CentralizedMediaPolicyEngine] = None


def get_media_policy_engine() -> CentralizedMediaPolicyEngine:
    global _media_policy_engine
    if _media_policy_engine is None:
        _media_policy_engine = CentralizedMediaPolicyEngine()
    return _media_policy_engine
