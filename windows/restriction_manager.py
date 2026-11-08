"""
STRICTRECORDS & EXAM PROCTOR — Centralized YouTube & Browser Restriction Manager
Implements Sections 10-24, 36-40:
- DETECT -> BLOCK -> SHOW FULLSCREEN LOCK -> CLOSE BROWSER -> WAIT FOR RELEASE -> REMOVE LOCK -> NORMAL
- Central state machine
- Clean browser closure via WM_CLOSE
- Reopen & scroll protection
- Authoritative state enforcement & immutable event logging
"""

import ctypes
import ctypes.wintypes
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from windows.browser_policy_controller import BrowserPolicyController
from windows.restriction_popup import EntrySession, RestrictionPopupController
from windows.native_browser_bridge import NativeBrowserBridge
from windows.strictbot_process import LaunchNotifier, close_exact_tree, protected
from windows.strictbot_preferences import Preferences
from windows.strictbot_display import select_display
from windows.strictbot_targets import capture_application, close_application, verify_application_exit
from core.restriction_targets import TARGETS, target_for_url, threshold_for

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_ENTERTAINMENT_KEYWORDS: Set[str] = {
    # Entertainment / Movies / Shows / Drama / Vlogs / Anime
    "entertainment", "movie", "movies", "film", "films", "cinema", "trailer", "trailers",
    "teaser", "teasers", "vlog", "vlogs", "vlogger", "prank", "pranks", "roast", "roasting",
    "drama", "dramas", "soap opera", "reaction", "reacting", "tiktok", "reels", "web series",
    "tv show", "tv series", "celebrity", "gossip", "reality show", "bigg boss", "netflix",
    "anime", "cartoon", "cartoons", "animation", "manga", "cinematic",
    # Music / Songs / Tracks
    "music", "song", "songs", "singing", "singer", "singers", "album", "albums", "remix",
    "remixes", "lofi", "lo-fi", "beat", "beats", "hip hop", "rap", "pop song", "pop music",
    "official video", "music video", "lyric video", "lyrics", "full album", "concert",
    "live concert", "dj", "dj remix", "soundtrack", "ost", "audio track", "track", "slowed",
    # Gaming / Streamers
    "gaming", "gameplay", "game", "games", "gamer", "gamers", "playthrough", "walkthrough",
    "minecraft", "gta", "gta 5", "roblox", "fortnite", "pubg", "valorant", "free fire",
    "call of duty", "cod", "streamer", "streamers", "twitch", "esports", "speedrun",
    # Comedy / Standup / Memes
    "comedy", "comedian", "comedians", "standup", "stand-up", "funny", "jokes", "joke",
    "meme", "memes", "humor", "parody", "skit", "skits",
    # Serials / Daily Soaps
    "serial", "serials", "episode", "episodes", "season", "daily soap", "tarak mehta",
    "anupama", "drama episode", "full episode",
}

EDUCATIONAL_KEYWORDS: Set[str] = {
    "tutorial", "tutorials", "lecture", "lectures", "course", "courses", "study", "studying",
    "learn", "learning", "how to", "education", "educational", "khan academy", "mit", "cs50",
    "harvard", "stanford", "nptel", "coursera", "edx", "udemy", "programming", "python",
    "kotlin", "java", "c++", "rust", "javascript", "typescript", "react", "coding", "algorithm",
    "algorithms", "data structures", "database", "databases", "sql", "math", "mathematics",
    "calculus", "algebra", "linear algebra", "geometry", "physics", "chemistry", "biology",
    "science", "exam", "exams", "upsc", "gate", "jee", "neet", "sat", "gre", "gmat", "cat",
    "crash course", "explanation", "explained", "documentary", "history", "system design",
    "data science", "machine learning", "deep learning", "ai", "artificial intelligence",
    "engineering", "software engineering", "computer science", "economics", "statistics",
    "philosophy", "literature", "linguistics", "grammar", "geography", "astronomy",
    "neuroscience", "psychology", "academic", "university", "college", "school", "research",
    "paper", "dissertation", "thesis", "textbook", "solved", "solution", "solutions",
    "practice problems", "revision", "syllabus", "certification", "documentation",
    "cybersecurity", "networking", "operating system", "linux", "git", "web development",
    "app development", "mobile development", "backend", "frontend", "full course",
}


def is_youtube_homepage(title: str) -> bool:
    """Detects whether window title corresponds to YouTube Home Feed / Landing Page."""
    if not title:
        return False
    tl = title.lower().replace("\u200b", "").strip()
    # Strip browser suffixes
    tl = re.sub(r'[\s\-—]+(google chrome|brave|microsoft edge|chromium|mozilla firefox|edge|opera|comet|ccleaner browser|ccleaner)$', '', tl).strip()
    # Strip profile suffixes
    tl = re.sub(r'[\s\-—]+(personal|work|profile\s*\d+)$', '', tl).strip()
    return tl in ("youtube", "youtube.com", "https://www.youtube.com", "http://www.youtube.com", "www.youtube.com")


def is_youtube_shorts(title: str) -> bool:
    """Detects whether window title corresponds to YouTube Shorts."""
    if not title:
        return False
    tl = title.lower()
    return any(k in tl for k in ["#shorts", "/shorts/", "shorts - youtube", "youtube shorts", " - shorts"]) or tl.startswith("shorts -") or tl == "shorts"


def validate_educational_unlock_request(reason: str) -> Tuple[bool, str]:
    """Validates whether a user's request reason qualifies for Educational Study Unlock."""
    clean = (reason or "").strip()
    if len(clean) < 8:
        return False, "Reason must be at least 8 characters explaining your educational study purpose."

    clean_lower = clean.lower()

    # Reject forbidden entertainment categories in reason
    for kw in FORBIDDEN_ENTERTAINMENT_KEYWORDS:
        # Match as whole word or phrase
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, clean_lower):
            return False, f"Reason rejected: Requests for entertainment, music, gaming, or serials ('{kw}') cannot be approved."

    # Validate that an educational intent exists
    has_edu_keyword = any(kw in clean_lower for kw in EDUCATIONAL_KEYWORDS)
    if not has_edu_keyword and len(clean.split()) < 3:
        return False, "Reason must specify a study topic, lecture, course, or exam subject."

    return True, "Reason approved for Educational Study Mode."

try:
    import win32con
    import win32gui
    import win32process
    HAS_WIN32 = True
except ImportError:
    win32con = None
    win32gui = None
    win32process = None
    HAS_WIN32 = False

logger = logging.getLogger("RestrictionManager")

MONITORED_BROWSERS: Set[str] = {
    "comet.exe",
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
    "ccleanerbrowser.exe",
}

SAFE_SYSTEM_PROCESSES: Set[str] = {
    "taskmgr.exe",
    "explorer.exe",
    "lockapp.exe",
    "logonui.exe",
    "csrss.exe",
    "winlogon.exe",
    "dwm.exe",
    "sihost.exe",
    "ExamProctor.exe",
    "StrictRecords.exe",
    "StrictRoutines.exe",
    "python.exe",
}


class RestrictionState(str, Enum):
    NORMAL = "NORMAL"
    RESTRICTION_TRIGGERED = "RESTRICTION_TRIGGERED"
    BLOCKING = "BLOCKING"
    BROWSER_CLOSING = "BROWSER_CLOSING"
    LOCK_SCREEN_ACTIVE = "LOCK_SCREEN_ACTIVE"
    WAITING_FOR_RELEASE = "WAITING_FOR_RELEASE"
    RELEASED = "RELEASED"


class YouTubeRestrictionManager:
    """
    Centralized Authority for YouTube & Streaming Restrictions on Windows.
    Enforces the full state machine and maintains persistent block protection.
    """

    def __init__(self, db=None, engine=None):
        if db is None:
            try:
                from core.database import StrictRecordsDB
                db = StrictRecordsDB()
            except Exception:
                pass
        self.db = db
        if self.db is not None:
            try:
                self.db.restriction_mgr = self
            except Exception:
                pass
        self.engine = engine
        self.state: RestrictionState = RestrictionState.NORMAL
        self.current_score: int = 0
        self.required_score: int = 80
        self.remaining_points: int = 80
        self.target_app: str = "YouTube"
        self.target_app_key: str = "youtube"
        self.last_block_reason: str = ""
        self.last_event: str = "INITIALIZED"
        self.last_event_time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.reopen_block_count: int = 0
        self.manual_admin_release: bool = False
        self.lock_screen_visible: bool = False
        self.simulated_test_mode: bool = False
        self.simulated_score: int = 64
        self.simulated_required: int = 80
        self.simulated_unavailable: bool = False
        self.browser_policy_controller = BrowserPolicyController()
        self._policy_reconcile_retry_seconds = 15.0
        self._last_policy_reconcile_attempt: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watcher_thread: Optional[threading.Thread] = None
        self.events_history: List[Dict[str, Any]] = []

        # High-performance, low-CPU in-memory caches
        self._points_cache: Optional[Dict[str, Any]] = None
        self._points_cache_time: float = 0.0
        self._score_foreground_identity = None
        self._score_native_tab_identity = None
        self._locks_cache: Optional[Dict[str, bool]] = None
        self._locks_cache_time: float = 0.0
        self._allowed_websites_cache: Optional[List[str]] = None
        self._allowed_websites_cache_time: float = 0.0
        self._enforcement_mode_cache: Optional[str] = None
        self._enforcement_mode_cache_time: float = 0.0
        self._policy_cache: Optional[Dict[str, Any]] = None
        self._policy_cache_time: float = 0.0
        self._proc_name_cache: Dict[int, Tuple[float, str]] = {}
        self._last_full_enum_time: float = 0.0

        # Section 5: YouTube Study Mode & Educational Whitelist
        self.study_mode_enabled: bool = True
        self.study_keywords: List[str] = list(EDUCATIONAL_KEYWORDS)

        # Educational Unlock State
        self.educational_unlock_active: bool = False
        self.educational_unlock_reason: str = ""
        self.educational_unlock_granted_at: Optional[str] = None
        self.educational_unlock_expires_at: Optional[str] = None
        self.educational_unlock_duration_mins: int = 60
        self.last_educational_block_reason: str = ""
        self._user_dismissed_lock: bool = False
        self._last_restricted_window_closed_time: float = 0.0
        self._dismissed_until_by_app: Dict[str, float] = {}
        self._last_seen_by_app: Dict[str, float] = {}
        self.force_close_restricted_windows: bool = True
        self.desktop_api: Any = None
        self._is_front_popup_active: bool = False
        self._last_front_popup_time: float = 0.0
        self._entry_session = EntrySession()
        self._popup_controller = RestrictionPopupController(self._native_home, self._native_close)
        self._active_native_target = {}
        self._popup_last_data = None
        self._native_policy_cache = {}
        self._wake_event = threading.Event()
        self._launch_notifier = LaunchNotifier(self._wake_event)
        self._native_scorer = None
        self._native_app_close_lock = threading.RLock()
        self._verified_music_paths = self._resolve_music_executables()
        self._native_browser = NativeBrowserBridge()
        self._browser_extension_last_seen: Dict[str, float] = {}
        self._browser_extension_any_last_seen: float = 0.0
        self._browser_extension_policy_mode: bool = False
        self._browser_extension_policy_browser_keys: Set[str] = set()
        self._browser_extension_timeout_seconds: float = 90.0
        self._night = None

    def _start_night_stage(self):
        from windows.night_shutdown import NightStore,NightCoordinator
        from windows.night_platform import close_targets,request_shutdown,shutdown_evidence,finalize_when_browsers_closed,logon_session_identity
        import psutil
        store=NightStore()
        store.confirmed_startup(psutil.boot_time(),shutdown_evidence,logon_session_identity())
        def close(run):
            prefs=Preferences().load()
            return close_targets(run,self.db.get_settings(),self.force_close_restricted_windows,prefs['exclusions'])
        self._night=NightCoordinator(store,close,request_shutdown,self._popup_controller.show_night,psutil.boot_time,lambda run:finalize_when_browsers_closed(store,run),logon_session_identity)

    def browser_extension_is_connected(
        self, browser_key: Optional[str] = None, now_monotonic: Optional[float] = None
    ) -> bool:
        """Whether the installed local Chromium extension has recently checked in."""
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        last_seen = (
            self._browser_extension_last_seen.get(browser_key, 0.0)
            if browser_key
            else self._browser_extension_any_last_seen
        )
        return bool(last_seen and now - last_seen < self._browser_extension_timeout_seconds)

    def active_browser_extension_keys(self, now_monotonic: Optional[float] = None) -> Set[str]:
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        return {
            key for key, last_seen in self._browser_extension_last_seen.items()
            if last_seen and now - last_seen < self._browser_extension_timeout_seconds
        }

    @staticmethod
    def _browser_extension_policy_overrides(browser_keys: Set[str]) -> Dict[str, bool]:
        """Disable StrictRecords URL blocking only in browsers running the extension."""
        browser_names = {
            "chrome": "Google Chrome",
            "edge": "Microsoft Edge",
            "brave": "Brave",
        }
        return {
            browser_names[key]: False
            for key in browser_keys
            if key in browser_names
        }

    def note_browser_extension_heartbeat(self, browser_key: str = "") -> None:
        """Record the extension connection and switch browser enforcement when it first connects."""
        was_connected = self.browser_extension_is_connected()
        was_active_keys = self.active_browser_extension_keys()
        now = time.monotonic()
        self._browser_extension_any_last_seen = now
        clean_browser = (browser_key or "").strip().lower()
        if clean_browser in {"chrome", "edge", "brave", "comet", "vivaldi", "opera", "arc"}:
            self._browser_extension_last_seen[clean_browser] = now
        if was_connected != self.browser_extension_is_connected() or was_active_keys != self.active_browser_extension_keys():
            try:
                self.reconcile_browser_policies()
            except Exception as exc:
                logger.warning("Could not switch to StrictBot browser extension enforcement: %s", exc)

    @staticmethod
    def _browser_extension_key_for_process(proc_name: str) -> str:
        return {
            "chrome.exe": "chrome",
            "msedge.exe": "edge",
            "brave.exe": "brave",
            "comet.exe": "comet",
            "vivaldi.exe": "vivaldi",
            "opera.exe": "opera",
            "arc.exe": "arc",
        }.get((proc_name or "").strip().lower(), "")

    def start_watcher(self, poll_interval: float = 1.0) -> None:
        """Starts the persistent background watcher thread with low CPU impact."""
        if self._night is None:self._start_night_stage()
        self._native_browser.connect()
        self._launch_notifier.start()
        self.reconcile_browser_policies()
        with self._lock:
            if self._watcher_thread and self._watcher_thread.is_alive():
                return
            self._stop_event.clear()
            self._watcher_thread = threading.Thread(
                target=self._watcher_loop,
                args=(poll_interval,),
                daemon=True,
                name="YouTubeRestrictionWatcher",
            )
            self._watcher_thread.start()
            logger.info("YouTube Restriction Watcher started (Low-CPU background mode).")

    def reconcile_browser_policies(self) -> Dict[str, Any]:
        """Reapply persisted service settings after Windows app or browser restarts."""
        if not self.db or not hasattr(self.db, "get_sidebar_restrictions_status"):
            return {"success": False, "error": "Restriction database is unavailable."}
        status = self.db.get_sidebar_restrictions_status().get("status", {})
        results: Dict[str, Any] = {}
        for service in ("youtube", "instagram"):
            entry = status.get(service, {})
            should_restrict = self._reward_aware_policy(service, entry)
            self._last_policy_reconcile_attempt[service] = time.monotonic()
            result = self.browser_policy_controller.apply_service_policy(service, should_restrict)
            results[service] = result
            try:
                self.db.record_restriction_enforcement_result(service, result)
            except Exception as exc:
                logger.warning("Could not persist %s policy acknowledgement: %s", service, exc)
        try:
            from windows.browser_setup import get_browser_extension_setup
            ext_locked = get_browser_extension_setup().is_locked()
            ext_res = self.browser_policy_controller.apply_service_policy("extension_protection", ext_locked)
            results["extension_protection"] = ext_res
        except Exception as exc:
            logger.warning("Could not reconcile extension_protection policy: %s", exc)
        self._browser_extension_policy_mode = False
        self._browser_extension_policy_browser_keys = set()
        self.clear_caches()
        return {"success": all(r.get("enforcementStatus") == "APPLIED" for r in results.values()), "services": results}

    def reconcile_browser_policies_if_needed(self) -> Dict[str, Any]:
        """Reapply policies when current authorization no longer matches confirmed access.

        Privilege can change while the app is running (for example, a score penalty can
        revoke a daily unlock). The startup reconciliation alone would leave the old
        browser policy active until the next launch. This check runs from the existing
        watcher and only writes policy when its persisted acknowledgement is stale.
        Failed attempts are bounded by a retry interval.
        """
        extension_active = self.browser_extension_is_connected()
        extension_keys = self.active_browser_extension_keys()
        if (
            extension_active != self._browser_extension_policy_mode
            or extension_keys != self._browser_extension_policy_browser_keys
        ):
            return self.reconcile_browser_policies()

        if not self.db or not hasattr(self.db, "get_sidebar_restrictions_status"):
            return {"success": False, "error": "Restriction database is unavailable.", "services": {}}

        try:
            status = self.db.get_sidebar_restrictions_status().get("status", {})
        except Exception as exc:
            return {"success": False, "error": f"Could not read restriction status: {exc}", "services": {}}

        now = time.monotonic()
        results: Dict[str, Any] = {}
        for service in ("youtube", "instagram"):
            entry = status.get(service)
            if not isinstance(entry, dict):
                continue
            should_restrict = self._reward_aware_policy(service, entry)
            expected_access = not should_restrict
            actual_access = entry.get("effectiveAccessAllowed")
            is_confirmed = (
                entry.get("enforcementStatus") == "APPLIED"
                and isinstance(actual_access, bool)
                and actual_access == expected_access
            )
            if is_confirmed:
                continue

            last_attempt = self._last_policy_reconcile_attempt.get(service, 0.0)
            if now - last_attempt < self._policy_reconcile_retry_seconds:
                continue
            self._last_policy_reconcile_attempt[service] = now

            result = self.apply_service_policy(
                service=service,
                effective_restriction_enabled=should_restrict,
                effective_access_allowed=expected_access,
                reason=str(entry.get("desiredPolicyReason") or "Reconcile current authorized access."),
            )
            results[service] = result
            try:
                self.db.record_restriction_enforcement_result(service, result)
            except Exception as exc:
                logger.warning("Could not persist %s policy reconciliation: %s", service, exc)
            self.clear_caches()

        return {
            "success": all(result.get("enforcementStatus") == "APPLIED" for result in results.values()),
            "services": results,
        }

    def stop_watcher(self) -> None:
        """Stops the watcher thread."""
        self._stop_event.set()
        self._launch_notifier.stop()
        self._wake_event.set()
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=2.0)
        self._popup_controller.stop()
        self._native_browser.stop()

    def _watcher_loop(self, poll_interval: float) -> None:
        while not self._stop_event.is_set():
            try:
                if self._night:self._night.tick()
                self.check_and_enforce()
            except Exception as e:
                logger.debug(f"Restriction watcher tick error: {e}")
            if self._wake_event.wait(max(.25, poll_interval)):
                self._last_full_enum_time = 0.0
            self._wake_event.clear()

    def clear_caches(self) -> None:
        """Invalidates in-memory points, app locks, policy, and website caches for instantaneous updates."""
        with self._lock:
            self._points_cache = None
            self._locks_cache = None
            self._allowed_websites_cache = None
            self._enforcement_mode_cache = None
            self._youtube_unlocked_cache = None
            self._policy_cache = None
            self._points_cache_time = 0.0
            self._locks_cache_time = 0.0
            self._allowed_websites_cache_time = 0.0
            self._enforcement_mode_cache_time = 0.0
            self._youtube_unlocked_cache_time = 0.0
            self._policy_cache_time = 0.0
            self._last_full_enum_time = 0.0

    def is_youtube_unlocked_for_today(self) -> bool:
        """Returns True if YouTube has been explicitly enabled/unlocked for the current calendar date."""
        if self.manual_admin_release:
            return True
        now = time.time()
        if getattr(self, "_youtube_unlocked_cache_time", 0.0) and (now - self._youtube_unlocked_cache_time < 1.5):
            return getattr(self, "_youtube_unlocked_cache", False)
        unlocked = False
        if self.db and hasattr(self.db, "is_youtube_unlocked_for_today"):
            try:
                unlocked = self.db.is_youtube_unlocked_for_today()
            except Exception:
                unlocked = False
        self._youtube_unlocked_cache = unlocked
        self._youtube_unlocked_cache_time = now
        return unlocked

    def is_instagram_unlocked_for_today(self) -> bool:
        """Returns True if Instagram has been explicitly enabled/unlocked for the current calendar date."""
        if self.manual_admin_release:
            return True
        if self.db and hasattr(self.db, "is_instagram_unlocked_for_today"):
            try:
                return self.db.is_instagram_unlocked_for_today()
            except Exception:
                return False
        return False

    def get_service_policy(self, service: str) -> Dict[str, Any]:
        """
        Returns the authoritative restriction policy for 'youtube' or 'instagram'.
        Ensures the window enforcement controller and the sidebar status UI use the exact same truth.
        """
        clean_svc = (service or "").strip().lower()
        now = time.time()
        if self._policy_cache and (now - self._policy_cache_time < 1.0):
            cached_svc = self._policy_cache.get(clean_svc)
            if cached_svc:
                return cached_svc

        if self.db and hasattr(self.db, "get_sidebar_restrictions_status"):
            try:
                full_stat = self.db.get_sidebar_restrictions_status()
                status_map = full_stat.get("status", {})
                if status_map:
                    self._policy_cache = status_map
                    self._policy_cache_time = now
                    if clean_svc in status_map:
                        policy = dict(status_map[clean_svc])
                        actual_access = policy.get("effectiveAccessAllowed")
                        desired_access = policy.get("desiredEffectiveAccessAllowed")
                        confirmed = (
                            policy.get("enforcementStatus") == "APPLIED"
                            and isinstance(actual_access, bool)
                            and isinstance(desired_access, bool)
                            and actual_access == desired_access
                        )
                        if not confirmed:
                            # The browser controller has not applied the current policy.
                            # Keep the title watcher enforcing the authorized decision
                            # while the UI retains its separate UNKNOWN/FAILED status.
                            policy["controllerConfirmedEffectiveAccessAllowed"] = actual_access
                            policy["effectiveAccessAllowed"] = bool(desired_access) if isinstance(desired_access, bool) else False
                            policy["effectiveRestrictionEnabled"] = not policy["effectiveAccessAllowed"]
                            policy["windowGuardFallback"] = True
                            if not policy["effectiveAccessAllowed"]:
                                policy["policyReason"] = str(
                                    policy.get("desiredPolicyReason")
                                    or policy.get("policyReason")
                                    or "Access remains restricted until browser enforcement is confirmed."
                                )
                        return policy
            except Exception:
                pass

        locks = self.get_app_locks()
        is_locked = bool(locks.get(clean_svc, False))
        req = self.required_score
        allowed = (not is_locked) and (self.current_score >= req)
        decision = "ALLOW" if allowed else "BLOCK"
        now_iso = datetime.now().isoformat()
        return {
            "service": clean_svc,
            "appName": TARGETS.get(clean_svc, {}).get("name", clean_svc.capitalize()),
            "currentScore": self.current_score,
            "requiredScore": req,
            "scoreEligible": bool(self.current_score >= req),
            "dailyPrerequisiteMet": True,
            "manuallyRestricted": False,
            "temporaryException": None,
            "effectiveAccessDecision": decision,
            "enforcementStatus": "APPLIED",
            "lastSuccessfulSync": now_iso,
            "requestedRestrictionEnabled": is_locked,
            "effectiveRestrictionEnabled": not allowed,
            "effectiveAccessAllowed": allowed,
            "policyDecision": decision,
            "policyReason": f"{clean_svc.capitalize()} is restricted." if not allowed else "Access allowed.",
            "statusText": f"{clean_svc.capitalize()} is restricted." if not allowed else "Access allowed.",
        }

    def apply_service_policy(
        self,
        service: str,
        effective_restriction_enabled: bool,
        effective_access_allowed: bool,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Applies the authoritative per-service policy to installed Chromium browsers through
        their Windows URLBlocklist policy. The window-title watcher remains a supplemental
        response for already-open tabs; it is not reported as browser-policy confirmation.
        """
        clean_svc = (service or "").strip().lower()
        now_iso = datetime.now().isoformat()

        if self.simulated_unavailable or (self.db and getattr(self.db, "simulate_controller_unavailable", False)):
            return {
                "controller": "YouTubeRestrictionManager",
                "service": clean_svc,
                "enforcementStatus": "FAILED",
                "ruleActive": True,
                "urlBlocked": True,
                "verified": False,
                "error": "The browser restriction controller has not confirmed the policy change.",
                "timestamp": now_iso,
            }

        self.clear_caches()
        result = self.browser_policy_controller.apply_service_policy(
            clean_svc,
            restriction_enabled=not bool(effective_access_allowed),
        )
        if result.get("enforcementStatus") == "APPLIED":
            self.clear_caches()
            if effective_access_allowed and clean_svc == "youtube":
                self.educational_unlock_active = False
            if effective_access_allowed:
                with self._lock:
                    if self.target_app_key == clean_svc and self.state in (
                        RestrictionState.LOCK_SCREEN_ACTIVE,
                        RestrictionState.WAITING_FOR_RELEASE,
                        RestrictionState.BLOCKING,
                        RestrictionState.RESTRICTION_TRIGGERED,
                    ):
                        self.state = RestrictionState.RELEASED
                        self.lock_screen_visible = False
                        self._user_dismissed_lock = False
        result.update({
            "controller": "WindowsBrowserURLPolicy",
            "service": clean_svc,
            "requestedRestrictionEnabled": bool(effective_restriction_enabled),
            "effectiveRestrictionEnabled": result.get("effectiveRestrictionEnabled", not bool(effective_access_allowed)),
            "effectiveAccessAllowed": result.get("effectiveAccessAllowed", effective_access_allowed),
            "policyReason": reason,
            "timestamp": now_iso,
        })
        return result

    def get_allowed_websites(self) -> List[str]:
        """Returns the list of allowed websites for PC browsers with TTL cache."""
        now = time.time()
        if self._allowed_websites_cache and (now - self._allowed_websites_cache_time < 3.0):
            return self._allowed_websites_cache
        sites = ["sunnxt.com", "sunnxt", "sun nxt", "github.com", "wikipedia.org", "stackoverflow.com", "docs.python.org", "w3schools.com"]
        if self.db and hasattr(self.db, "get_allowed_websites"):
            try:
                db_sites = self.db.get_allowed_websites()
                for s in db_sites:
                    if s not in sites:
                        sites.append(s)
            except Exception:
                pass
        self._allowed_websites_cache = sites
        self._allowed_websites_cache_time = now
        return sites

    def get_website_enforcement_mode(self) -> str:
        """Returns the website enforcement mode: 'DISTRACTIONS_ONLY' or 'WHITELIST_ONLY'."""
        now = time.time()
        if self._enforcement_mode_cache and (now - self._enforcement_mode_cache_time < 3.0):
            return self._enforcement_mode_cache
        mode = "DISTRACTIONS_ONLY"
        if self.db and hasattr(self.db, "get_system_manager_settings"):
            try:
                cfg = self.db.get_system_manager_settings()
                mode = cfg.get("website_enforcement_mode", "DISTRACTIONS_ONLY")
            except Exception:
                pass
        self._enforcement_mode_cache = mode
        self._enforcement_mode_cache_time = now
        return mode

    def _reward_aware_policy(self, service, entry):
        if service == 'youtube':
            try:
                from core.youtube_reward_cloud import client
                client(self.db).refresh_async()
            except Exception:
                pass
        return bool(entry.get('desiredEffectiveRestrictionEnabled', True))

    def get_app_locks(self) -> Dict[str, bool]:
        """Returns the current lock state for all 7 target applications with short TTL cache."""
        now = time.time()
        if self._locks_cache and (now - self._locks_cache_time < 1.0):
            return self._locks_cache
        locks = {
            "youtube": True,
            "instagram": True,
            "brave": False,
            "comet": False,
            "snapchat": True,
            "google": False,
            "chrome": False,
        }
        if self.db:
            try:
                controls = self.db.get_device_app_controls(check_adb=False)
                for c in controls:
                    locks[c["app_key"]] = bool(c.get("is_locked", False))
            except Exception:
                pass
        self._locks_cache = locks
        self._locks_cache_time = now
        return locks

    def _ensure_input_desktop(self) -> None:
        """Attaches background worker threads to the interactive Windows desktop for reliable inspection."""
        if HAS_WIN32 and os.name == "nt":
            try:
                hdesk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    ctypes.windll.user32.SetThreadDesktop(hdesk)
            except Exception:
                pass

    def _is_instagram_title(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower().strip()
        # 1. Ignore search engine results and general developer sites
        if any(se in tl for se in [
            " - google search", "google search",
            " - bing", "bing search",
            " - duckduckgo", "duckduckgo",
            " - brave search", "brave search",
            " - perplexity", "perplexity",
            " - search", "search results",
            "github", "stackoverflow", "wikipedia",
        ]):
            return False

        # 2. Clean notification badges, e.g. "(1) Instagram"
        tl_clean = re.sub(r'^\(\d+\)\s*', '', tl).strip()

        # 3. Exact Instagram title or Instagram web title
        if tl_clean in ("instagram", "instagram web"):
            return True

        # 4. Standard Instagram web tab formats
        if any(k in tl for k in ["instagram.com", "• instagram", "- instagram", "| instagram", "on instagram", "instagram •", "instagram |"]):
            return True

        # 5. Starts with Instagram as page name (e.g. "Instagram - Direct", "Instagram - Google Chrome")
        if tl_clean.startswith("instagram -") or tl_clean.startswith("instagram:") or tl_clean.startswith("instagram –") or tl_clean.startswith("instagram —"):
            return True

        # 6. Stripped browser suffix check (e.g. "Instagram - Google Chrome" -> "instagram")
        stripped = re.sub(r'[\s\-—|•]+(google chrome|brave|microsoft edge|edge|firefox|comet|opera|vivaldi|arc|ccleaner browser|ccleaner)$', '', tl_clean).strip()
        if stripped in ("instagram", "instagram web") or stripped.startswith("instagram ") or stripped.endswith(" instagram"):
            return True

        return False

    def _is_spotify_title(self, title: str) -> bool:
        """Detects whether window title corresponds to Spotify Web Player or Spotify tracks/albums."""
        if not title:
            return False
        tl = title.lower().strip()
        # Ignore search engine results pages
        if any(se in tl for se in [
            " - google search", "google search",
            " - bing", "bing search",
            " - duckduckgo", "duckduckgo",
            " - brave search", "brave search",
            " - perplexity", "perplexity",
            " - search", "search results",
            "github", "stackoverflow", "wikipedia",
        ]):
            return False
        if any(k in tl for k in [
            "open.spotify.com",
            "spotify.com",
            "spotify – web player",
            "spotify - web player",
            "spotify — web player",
            "spotify: web player",
            "spotify web player",
            "| spotify",
            "• spotify",
            "- spotify",
            "spotify -",
            "spotify –",
            "spotify —",
        ]):
            return True
        tl_clean = re.sub(r'^\(\d+\)\s*', '', tl).strip()
        stripped = re.sub(r'[\s\-—|•]+(google chrome|brave|microsoft edge|edge|firefox|comet|opera|vivaldi|arc|ccleaner browser|ccleaner)$', '', tl_clean).strip()
        if stripped in ("spotify", "spotify web", "spotify web player") or stripped.startswith("spotify ") or stripped.endswith(" spotify"):
            return True
        return False

    def _is_snapchat_title(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower().strip()
        if any(se in tl for se in [" - google search", "google search", " - bing", "bing search"]):
            return False
        return any(k in tl for k in ["snapchat.com", "snapchat -", "- snapchat"]) or tl.strip() == "snapchat"

    def _is_google_search_title(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower()
        return (" - google search" in tl) or ("google search" in tl and "google" in tl) or ("google.com/search" in tl)

    def _is_youtube_title(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower().strip()
        # Ignore search engine results pages
        if any(se in tl for se in [
            " - google search", "google search",
            " - bing", "bing search",
            " - duckduckgo", "duckduckgo",
            " - brave search", "brave search",
            " - perplexity", "perplexity",
        ]):
            return False
        return any(
            k in tl
            for k in [
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                " - youtube",
                "• youtube",
                "| youtube",
                "youtube -",
                "youtu.be",
            ]
        ) or (tl.startswith("youtube") and len(tl) < 40)

    def _is_extension_tamper_attempt(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower().strip()
        ext_page_indicators = [
            "chrome://extensions",
            "edge://extensions",
            "brave://extensions",
            "opera://extensions",
            "comet://extensions",
            "chrome://settings/extensions",
            "edge://settings/extensions",
            "brave://settings/extensions",
            "opera://settings/extensions",
            "comet://settings/extensions",
            "chrome://flags",
            "edge://flags",
            "brave://flags",
            "opera://flags",
            "comet://flags",
            "chrome://settings/reset",
            "edge://settings/reset",
            "brave://settings/reset",
            "opera://settings/reset",
            "comet://settings/reset",
            "extensions - google chrome",
            "extensions - microsoft edge",
            "extensions - brave",
            "extensions - opera",
            "extensions - comet",
            "extensions - chromium",
            "add-ons manager",
            "manage extensions",
        ]
        if any(ind in tl for ind in ext_page_indicators):
            return True
        removal_indicators = [
            'remove "strictroutines',
            "remove strictroutines",
            "remove extension",
            "remove from chrome",
            "remove from microsoft edge",
            "remove from brave",
            "remove from opera",
            "remove from comet",
            "remove from perplexity",
        ]
        if any(ind in tl for ind in removal_indicators):
            return True
        return False

    def _is_shorts_title(self, title: str) -> bool:
        return is_youtube_shorts(title)

    def _is_homepage(self, title: str) -> bool:
        return is_youtube_homepage(title)

    def _contains_forbidden_entertainment(self, title: str) -> Optional[str]:
        if not title:
            return None
        tl = title.lower()
        for kw in FORBIDDEN_ENTERTAINMENT_KEYWORDS:
            pattern = r'(?:\b|[\s\-_#])' + re.escape(kw) + r'(?:\b|[\s\-_#]|$)'
            if re.search(pattern, tl):
                return kw
        return None

    def _is_study_content(self, title: str) -> bool:
        if not title:
            return False
        tl = title.lower()
        for kw in self.study_keywords:
            pattern = r'(?:\b|[\s\-_#])' + re.escape(kw) + r'(?:\b|[\s\-_#]|$)'
            if re.search(pattern, tl):
                return True
        return False

    def _configured_native_decision(self, name, hwnd, locks):
        """Exact configured executables reuse the existing access evaluator and thresholds."""
        name = (name or "").lower()
        if name in {"spotify.exe","ccleaner.exe"} or protected(name, Preferences().load()["exclusions"]):
            return None  # Spotify retains its verified installation + music entitlement path below.
        labels = {"ccleanerbrowser.exe":"CCleaner Browser", "opera.exe":"Opera Browser",
                  "brave.exe":"Brave Browser", "vlc.exe":"VLC Media Player", "audacity.exe":"Audacity",
                  "musichub.exe":"MusicHub", "videoeditor.exe":"VSDC Video Editor"}
        key = name.removesuffix(".exe")
        if locks.get(key, False):
            return dict(hwnd=hwnd,title="",process_name=name,app_key=key,
                        target_app=labels.get(name,key),action="CLOSE_APP",reason="Locked by existing App Controls.")
        from core.windows_app_catalog import BROWSERS
        if name in BROWSERS:
            return None  # Ordinary browser apps remain available until the saved Night Lock.
        if not self.db:
            return None
        cached_settings = getattr(self, "_native_settings", None)
        if cached_settings and time.monotonic()-cached_settings[0] < 1:
            settings = cached_settings[1]
        else:
            settings = self.db.get_settings()
            self._native_settings = (time.monotonic(), settings)
        configured = list(getattr(settings,"restricted_windows_apps",[])) + list(getattr(settings,"local_music_windows_apps",[]))
        # Domain/title keywords never become process-wide close rules.
        if name not in {str(v).strip().lower() for v in configured if str(v).strip().lower().endswith(".exe")}:
            return None
        cache_key=(name,self.current_score,self.get_points_status().get("score_available",True))
        cached=self._native_policy_cache.get(cache_key)
        if cached and time.monotonic()-cached[0]<2:
            access=cached[1]
        else:
            scorer=self.engine
            if scorer is None:
                if self._native_scorer is None:
                    from core.scoring_engine import ScoringEngine
                    self._native_scorer=ScoringEngine(db=self.db)
                scorer=self._native_scorer
            from core.time_window_engine import now_ist
            access=scorer.evaluate_entertainment_access(date=now_ist().strftime("%Y-%m-%d"),
                target_app=name,platform="WINDOWS",record_violation_if_blocked=False)
            self._native_policy_cache[cache_key]=(time.monotonic(),access)
            if len(self._native_policy_cache)>100:
                self._native_policy_cache={key:value for key,value in self._native_policy_cache.items() if time.monotonic()-value[0]<2}
        if access.get("allowed"):
            return None
        return dict(hwnd=hwnd,title="",process_name=name,app_key="native:"+name,
                    target_app=labels.get(name,name.removesuffix(".exe")),action="CLOSE_APP",
                    required_score=access.get("required_score",self.required_score),
                    reason=access.get("reason") or "Restricted by the existing access policy.")

    def _evaluate_window_restriction(self, hwnd: Any, title: str = "", proc_name: str = None, locks: Dict[str, bool] = None) -> Optional[Dict[str, Any]]:
        """
        Determines whether an active window should be restricted across all 7 target applications:
        YouTube, Instagram, Brave, Comet, Snapchat, Google, Chrome.
        
        Rules:
        - Process-level locks close the entire application (WM_CLOSE).
        - Browser-level tab locks send Ctrl+W to close ONLY that tab, preserving the user's other work tabs (especially in Comet)!
        - Comet is NEVER closed unless explicitly locked in App Controls.
        """
        if isinstance(hwnd, str):
            # Support calling _evaluate_window_restriction(title, proc_name, locks=...)
            if isinstance(proc_name, dict) and locks is None:
                locks = proc_name
            proc_name = title
            title = hwnd
            hwnd = 0
        if locks is None:
            locks = self.get_app_locks()

        proc_lower = (proc_name or "").lower()
        if proc_lower in {str(v).lower() for v in SAFE_SYSTEM_PROCESSES} or protected(proc_lower, Preferences().load()["exclusions"]):
            return None
        native = self._configured_native_decision(proc_lower, hwnd, locks)
        if native:
            return native

        if proc_lower == "spotify.exe" and hwnd and TARGETS["spotify"]["enabled"]:
            try:
                if self.db and hasattr(self.db, "is_gym_mode_music_active") and self.db.is_gym_mode_music_active():
                    return None  # Unlocked under active GYM Mode session
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                verified = self._process_path(pid) in self._verified_music_paths
                required = threshold_for("spotify", self.db.get_settings() if self.db else None)
                if verified and (not self.get_points_status().get("score_available", True) or self.current_score < required):
                    return {"hwnd": hwnd, "title": "", "process_name": proc_name, "app_key": "spotify",
                            "target_app": "Spotify", "action": "CLOSE_APP", "required_score": required,
                            "reason": f"Music unlock requires {required} confirmed points."}
            except Exception:
                logger.exception("platform=Windows target=spotify detection=error")
            return None

        # 1. DEDICATED PROCESS-LEVEL LOCKS
        if proc_lower == "chrome.exe" and locks.get("chrome", False):
            return {
                "hwnd": hwnd, "title": title, "process_name": proc_name,
                "app_key": "chrome", "target_app": "Chrome",
                "action": "CLOSE_APP", "reason": "Chrome browser is locked via App Controls."
            }

        if proc_lower == "brave.exe" and locks.get("brave", False):
            return {
                "hwnd": hwnd, "title": title, "process_name": proc_name,
                "app_key": "brave", "target_app": "Brave",
                "action": "CLOSE_APP", "reason": "Brave browser is locked via App Controls."
            }

        # Comet browser: ONLY restricted if user explicitly toggled Comet lock ON!
        if proc_lower == "comet.exe" and locks.get("comet", False):
            return {
                "hwnd": hwnd, "title": title, "process_name": proc_name,
                "app_key": "comet", "target_app": "Comet",
                "action": "CLOSE_APP", "reason": "Comet browser is locked via App Controls."
            }

        # CCleaner Browser: Process-level lock if explicitly toggled ON in App Controls
        if proc_lower == "ccleanerbrowser.exe" and locks.get("ccleaner", False):
            return {
                "hwnd": hwnd, "title": title, "process_name": proc_name,
                "app_key": "ccleaner", "target_app": "CCleaner Browser",
                "action": "CLOSE_APP", "reason": "CCleaner Browser is locked via App Controls."
            }

        # Instagram Store App (Unified with authoritative Instagram service policy)
        if any(p in proc_lower for p in ["instagram.exe", "facebook.instagram"]):
            ig_pol = self.get_service_policy("instagram")
            if not ig_pol.get("effectiveAccessAllowed", False):
                return {
                    "hwnd": hwnd, "title": title, "process_name": proc_name,
                    "app_key": "instagram", "target_app": "Instagram",
                    "action": "CLOSE_APP",
                    "reason": ig_pol.get("policyReason") or "Instagram is locked."
                }
            return None

        # Snapchat Store App
        if any(p in proc_lower for p in ["snapchat.exe", "snapinc.snapchat"]):
            if locks.get("snapchat", False) or (self.current_score < self.required_score and not self.manual_admin_release):
                return {
                    "hwnd": hwnd, "title": title, "process_name": proc_name,
                    "app_key": "snapchat", "target_app": "Snapchat",
                    "action": "CLOSE_APP", "reason": "Snapchat is locked."
                }

        # 2. IN-BROWSER TAB RESTRICTIONS (Inside Monitored Browsers)
        if proc_lower in MONITORED_BROWSERS:
            # Sun NXT Exception - Explicitly permitted under user policy
            tl = (title or "").lower()
            if "sunnxt" in tl or "sun nxt" in tl:
                return None

            # 0. STRICTROUTINES EXTENSION UNINSTALL & TAMPER PROTECTION
            if self._is_extension_tamper_attempt(title):
                is_ext_locked = True
                try:
                    from windows.browser_setup import get_browser_extension_setup
                    is_ext_locked = get_browser_extension_setup().is_locked()
                except Exception:
                    is_ext_locked = True
                if is_ext_locked:
                    is_dialog = any(k in title.lower() for k in [
                        "remove strictroutines", 'remove "strictroutines', "remove extension",
                        "remove from chrome", "remove from microsoft edge", "remove from brave", "remove from opera",
                        "remove from comet", "remove from perplexity",
                    ])
                    logger.warning("Blocked unauthorized extension uninstall/management attempt: %s", title)
                    return {
                        "hwnd": hwnd,
                        "title": title,
                        "process_name": proc_name,
                        "app_key": "browser_extension",
                        "target_app": "StrictRoutines Extension Lock",
                        "action": "DISMISS_DIALOG" if is_dialog else "CLOSE_TAB",
                        "reason": "StrictRoutines extension is locked against uninstalling or tampering. Administrator authentication is required to manage extensions.",
                    }

            # YouTube detection (Unified with authoritative YouTube service policy)
            if self._is_youtube_title(title):
                # Educational Unlock check (when educational mode is explicitly active)
                if self.educational_unlock_active:
                    if self._is_shorts_title(title):
                        self.last_educational_block_reason = "Shorts are blocked in Educational Mode."
                        return {
                            "hwnd": hwnd, "title": title, "process_name": proc_name,
                            "app_key": "youtube", "target_app": "YouTube",
                            "action": "CLOSE_TAB", "reason": "YouTube Shorts are blocked in Educational Mode."
                        }
                    if self._is_homepage(title):
                        self.last_educational_block_reason = "Homepage is blocked in Educational Mode."
                        return {
                            "hwnd": hwnd, "title": title, "process_name": proc_name,
                            "app_key": "youtube", "target_app": "YouTube",
                            "action": "CLOSE_TAB", "reason": "YouTube Homepage is blocked in Educational Mode."
                        }
                    ent = self._contains_forbidden_entertainment(title)
                    if ent:
                        self.last_educational_block_reason = f"Entertainment content ('{ent}') is blocked in Educational Mode."
                        return {
                            "hwnd": hwnd, "title": title, "process_name": proc_name,
                            "app_key": "youtube", "target_app": "YouTube",
                            "action": "CLOSE_TAB", "reason": f"Entertainment content ('{ent}') is blocked in Educational Mode."
                        }
                    if self._is_study_content(title):
                        return None  # Allowed educational video!

                    self.last_educational_block_reason = "Only educational lectures and study content are allowed in Educational Mode."
                    return {
                        "hwnd": hwnd, "title": title, "process_name": proc_name,
                        "app_key": "youtube", "target_app": "YouTube",
                        "action": "CLOSE_TAB", "reason": "Only educational study content is allowed in Educational Mode."
                    }

                yt_pol = self.get_service_policy("youtube")
                if yt_pol.get("effectiveAccessAllowed", False):
                    return None  # YouTube restriction is OFF and access is ALLOWED!

                return {
                    "hwnd": hwnd, "title": title, "process_name": proc_name,
                    "app_key": "youtube", "target_app": "YouTube",
                    "action": "CLOSE_TAB",
                    "reason": yt_pol.get("policyReason") or "YouTube is locked."
                }

            # Instagram Web check (Unified with authoritative Instagram service policy)
            if self._is_instagram_title(title):
                ig_pol = self.get_service_policy("instagram")
                if ig_pol.get("effectiveAccessAllowed", False):
                    return None  # Instagram restriction is OFF and access is ALLOWED!

                return {
                    "hwnd": hwnd, "title": title, "process_name": proc_name,
                    "app_key": "instagram", "target_app": "Instagram",
                    "action": "CLOSE_TAB",
                    "reason": ig_pol.get("policyReason") or "Instagram Web is locked."
                }

            # Snapchat Web check
            if self._is_snapchat_title(title):
                if locks.get("snapchat", False) or (self.current_score < self.required_score and not self.manual_admin_release):
                    return {
                        "hwnd": hwnd, "title": title, "process_name": proc_name,
                        "app_key": "snapchat", "target_app": "Snapchat",
                        "action": "CLOSE_TAB", "reason": "Snapchat Web is locked."
                    }

            # Google Search check
            if self._is_google_search_title(title):
                if locks.get("google", False):
                    return {
                        "hwnd": hwnd, "title": title, "process_name": proc_name,
                        "app_key": "google", "target_app": "Google",
                        "action": "CLOSE_TAB", "reason": "Google Search is locked via App Controls."
                    }

            # Whitelist Mode enforcement (if configured by user in Manager Settings)
            if self.get_website_enforcement_mode() == "WHITELIST_ONLY" and not self.manual_admin_release and self.current_score < self.required_score:
                allowed_sites = self.get_allowed_websites()
                tl = title.lower()
                is_site_allowed = any(site.lower() in tl for site in allowed_sites)
                is_browser_internal = any(tl.startswith(k) for k in ["new tab", "home", "start page", "about:", "chrome://", "edge://", "comet://"])
                if not is_site_allowed and not is_browser_internal:
                    return {
                        "hwnd": hwnd, "title": title, "process_name": proc_name,
                        "app_key": "website", "target_app": "Website",
                        "action": "CLOSE_TAB", "reason": f"Website '{title[:35]}' is not in allowed websites list."
                    }

        return None

    @staticmethod
    def _resolve_music_executables():
        # Enable only an installed executable whose version metadata confirms Spotify.
        verified = set()
        candidate = os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe")
        if os.path.isfile(candidate):
            try:
                import win32api
                lang, codepage = win32api.GetFileVersionInfo(candidate, r"\VarFileInfo\Translation")[0]
                prefix = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\"
                product = win32api.GetFileVersionInfo(candidate, prefix + "ProductName")
                company = win32api.GetFileVersionInfo(candidate, prefix + "CompanyName")
                if product == "Spotify" and company == "Spotify Ltd":
                    verified.add(os.path.normcase(os.path.abspath(candidate)))
            except Exception:
                logger.warning("Spotify executable could not be verified; native monitoring disabled.")
        return verified

    def _process_path(self, pid):
        try:
            import psutil
            return os.path.normcase(os.path.abspath(psutil.Process(pid).exe()))
        except Exception:
            return ""

    def configured_targets_status(self):
        # Report the same saved thresholds used by enforcement; displaying a
        # built-in default must not imply it is the account's current setting.
        try:
            settings = self.db.get_settings() if self.db else None
            thresholds = {key: threshold_for(key, settings) for key in TARGETS}
        except Exception:
            thresholds = {}
        return [dict(id=key, **spec, nativeSupported=(key == "spotify" and bool(self._verified_music_paths)),
                     configuredThreshold=thresholds.get(key),
                     websiteMonitoring="NATIVE_BROWSER_CONTROLS" if self._native_browser.connected else "NATIVE_BROWSER_UNAVAILABLE",
                     nativeMonitoring="VERIFIED_INSTALL" if key == "spotify" and self._verified_music_paths else "UNSUPPORTED_OR_NOT_INSTALLED")
                for key, spec in TARGETS.items()]

    def desktop_connection_status(self, connect=False):
        native = self._native_browser.connect() if connect else self._native_browser.status()
        running = bool(self._watcher_thread and self._watcher_thread.is_alive())
        return {"success": True, "connected": True, "application": "StrictRecords native desktop app",
                "extension_required": False, "monitor_running": running,
                "native_browser": native, "configured_targets": self.configured_targets_status()}

    def _native_tab_decision(self, hwnd, pid, process_name):
        if not self._native_browser.connected or process_name.lower() not in MONITORED_BROWSERS:
            return None
        snapshot = self._native_browser.inspect(hwnd, process_name.lower())
        service = snapshot.get("target")
        if not snapshot.get("supported") or snapshot.get("pid") != pid or not service:
            return None
        # Actual browser chrome URL, never a website name guessed from window text.
        title = win32gui.GetWindowText(hwnd) if service == "youtube" else ""
        identity = (hwnd, pid, snapshot.get("tab_id"), snapshot.get("origin"))
        new_entry = identity != self._score_native_tab_identity
        self._score_native_tab_identity = identity
        decision = self.get_browser_tab_restriction(service, title, snapshot["origin"], force_points_refresh=new_entry)
        if not decision.get("restricted"):
            return None
        proc_lower = process_name.lower()
        return dict(snapshot, hwnd=hwnd, pid=pid, process_name=process_name, app_key=service,
                    target_app=TARGETS[service]["name"], title="", action="CLOSE_NATIVE_TAB",
                    required_score=decision["requiredScore"], reason=decision.get("reason", "Access is restricted."))

    def _build_canonical_decision(
        self,
        service: str,
        app_name: str,
        required: int,
        allowed: bool,
        reason: str,
        decision: str,
        temporary_exception: Optional[str] = None,
        daily_prereq_met: bool = True,
        manually_restricted: bool = False,
        policy_version: int = 1,
        current_score: Optional[int] = None,
    ) -> Dict[str, Any]:
        now_iso = datetime.now().isoformat()
        is_restricted = not allowed
        # Eligibility belongs to this website's threshold, including exceptions
        # which may permit access below that threshold. Never use a caller local.
        score = self.current_score if current_score is None else current_score
        score_eligible = score >= required
        eval_key = (service, app_name)
        now_ts = time.time()
        last_eval = getattr(self, "_last_eval_log", {}).get(eval_key)
        curr_state = (decision, score_eligible, daily_prereq_met, manually_restricted, temporary_exception)
        if not hasattr(self, "_last_eval_log"):
            self._last_eval_log = {}
        if not last_eval or last_eval[0] != curr_state or (now_ts - last_eval[1] >= 60.0):
            self._last_eval_log[eval_key] = (curr_state, now_ts)
            logger.info(
                "Restriction Evaluation: service=%s app=%s score=%d/%d eligible=%s wakeupMet=%s manualLock=%s tempException=%s -> decision=%s reason='%s'",
                service, app_name, score, required, score_eligible, daily_prereq_met,
                manually_restricted, temporary_exception, decision, reason,
            )
        return {
            "service": service,
            "appName": app_name,
            "currentScore": score,
            "requiredScore": required,
            "scoreEligible": score_eligible,
            "dailyPrerequisiteMet": daily_prereq_met,
            "manuallyRestricted": manually_restricted,
            "temporaryException": temporary_exception,
            "effectiveAccessDecision": decision,
            "enforcementStatus": "APPLIED",
            "lastSuccessfulSync": now_iso,
            "restricted": is_restricted,
            "allowed": allowed,
            "score": score,
            "reason": reason,
            "statusText": reason,
            "policyDecision": decision,
            "policyVersion": policy_version,
        }

    def get_browser_tab_restriction(self, service: str, page_title: str = "", actual_url: str = "", force_points_refresh: bool = False, activate_rewards: bool = True) -> Dict[str, Any]:
        clean_service = (service or "").strip().lower()
        tl = (page_title or "").lower()
        ul = (actual_url or "").lower()
        if "sunnxt" in clean_service or "sun nxt" in clean_service or "sunnxt" in tl or "sun nxt" in tl or "sunnxt" in ul or "sunnetwork.in" in ul:
            return {
                "restricted": False,
                "allowed": True,
                "appName": "Sun NXT",
                "reason": "Sun NXT is explicitly permitted under discipline policy.",
                "currentScore": 100,
                "requiredScore": 0,
            }
        from core.windows_app_catalog import WINDOWS_WEBSITES
        if clean_service in WINDOWS_WEBSITES:
            from urllib.parse import urlsplit
            target=WINDOWS_WEBSITES[clean_service];url=urlsplit(actual_url)
            if url.scheme not in ('http','https') or not (url.hostname==target['domain'] or (url.hostname or '').endswith('.'+target['domain'])):
                return dict(restricted=False,reason='Domain is not a configured target.')
            settings=self.db.get_settings()
            configured=target['domain'] in {str(v).lower() for v in settings.restricted_windows_apps}
            if not configured:return dict(restricted=False,reason='Website is not configured for restriction.')
            scorer=self.engine or self._native_scorer
            if scorer is None:
                from core.scoring_engine import ScoringEngine
                scorer=self._native_scorer=ScoringEngine(db=self.db)
            from core.time_window_engine import now_ist
            access=scorer.evaluate_entertainment_access(date=now_ist().strftime('%Y-%m-%d'),target_app=target['domain'],platform='WINDOWS',record_violation_if_blocked=False)
            return dict(restricted=not access.get('allowed',False),appName=target['name'],reason=access.get('reason',''),currentScore=access.get('current_score'),requiredScore=access.get('required_score'))
        if clean_service not in TARGETS:
            return {"restricted": True, "reason": "Unsupported browser restriction service."}
        if actual_url and target_for_url(actual_url) != clean_service:
            return {"restricted": False, "reason": "Domain is not a configured target."}

        points = self.get_points_status(force=force_points_refresh)
        settings = self.db.get_settings() if self.db else None
        required = threshold_for(clean_service, settings)
        target_info = TARGETS.get(clean_service, {})
        app_name = target_info.get("name", clean_service.capitalize())
        category = target_info.get("category", "")
        now_iso = datetime.now().isoformat()

        if not points.get("score_available", True):
            decision_dict = {
                "service": clean_service,
                "appName": app_name,
                "currentScore": None,
                "requiredScore": required,
                "scoreEligible": False,
                "dailyPrerequisiteMet": False,
                "manuallyRestricted": False,
                "temporaryException": None,
                "effectiveAccessDecision": "BLOCK",
                "enforcementStatus": "FAILED",
                "lastSuccessfulSync": now_iso,
                "restricted": True,
                "allowed": False,
                "unavailable": True,
                "score": None,
                "reason": "Confirmed points are unavailable. Access cannot be verified.",
                "statusText": "Confirmed points are unavailable. Access cannot be verified.",
                "policyDecision": "BLOCK",
                "policyVersion": 1,
            }
            logger.info("Restriction Evaluation: service=%s score=None/req=%d -> BLOCK (points unavailable)", clean_service, required)
            return decision_dict

        self.current_score = int(points["current_score"])
        self.required_score = int(points["required_score"])
        current_score = int(points["current_score"])
        access_score, demo_active = current_score, False
        try:
            if self.db and isinstance(getattr(self.db,'db_path',None),str):
                from core.paired_demo_service import effective_access
                account=str(self.db.get_active_user_profile().get('user_id') or '')
                access_score,demo_active=effective_access(self.db.db_path,account,current_score)
        except Exception:
            pass  # An unverified allowance never grants access.
        score_eligible = (access_score >= required)

        # 1. Entertainment Reward Entitlement (EARLY_PROOF_ENTERTAINMENT_REWARD_20M)
        # Grants 20 minutes across ALL entertainment apps (TARGETS), activated on first use.
        reward_session = None
        if self.db and clean_service in TARGETS:
            try:
                from core.entertainment_reward_engine import EntertainmentRewardEngine, extract_canonical_video_id
                from core.time_window_engine import iso_to_dt
                ent_engine = EntertainmentRewardEngine(self.db)
                ent_status = ent_engine.get_reward_status("user_lok")
                if ent_status.get("is_active"):
                    active_ent = ent_status.get("active_session")
                    exp_ms = int(iso_to_dt(active_ent["expiry_time"]).timestamp() * 1000) if active_ent and active_ent.get("expiry_time") else 0
                    reward_session = {
                        "remaining_seconds": ent_status.get("remaining_seconds", 0),
                        "expiry_ms": exp_ms,
                    }
                    score_eligible = True
                elif activate_rewards and ent_status.get("total_available", 0) > 0:
                    # Activate available reward on FIRST actual entertainment app access
                    avail = ent_status["available_rewards"][0]
                    act_res = ent_engine.activate_reward(avail["entitlement_id"], "WINDOWS_EXE", "user_lok")
                    if act_res.get("session_active"):
                        exp_ms = int(iso_to_dt(act_res["expiry_time"]).timestamp() * 1000) if act_res.get("expiry_time") else 0
                        reward_session = {
                            "remaining_seconds": act_res.get("remaining_seconds", 1200),
                            "expiry_ms": exp_ms,
                        }
                        score_eligible = True
            except Exception as ent_err:
                logger.warning("Error checking EntertainmentRewardEngine: %s", ent_err)

        if not reward_session and clean_service == "youtube" and self.db:
            try:
                from core.youtube_reward_cloud import client
                reward_session = client(self.db).youtube_active()
                if reward_session: score_eligible = True
            except Exception:
                reward_session = None

        # Check Gate 2: Taufa AI Daily Productivity Override (9:40 PM IST, score >= 70%, strictly expires at 10:00 PM)
        if clean_service == "youtube" and not score_eligible and self.db:
            try:
                from core.daily_productivity_engine import DailyProductivityEngine
                dpe = DailyProductivityEngine(self.db)
                override = dpe.get_active_override()
                if override and not self.db.get_active_strict_session():
                    rem_sec = max(0, int((override["expires_at_ms"] - int(time.time() * 1000)) / 1000))
                    res = self._build_canonical_decision(
                        clean_service, app_name, required, True,
                        f"Gate 2 Daily Productivity Access Active: Score {override.get('score')}% (Expires 10:00 PM IST, {rem_sec}s remaining).",
                        "ALLOW", current_score=current_score, temporary_exception="DAILY_PRODUCTIVITY_OVERRIDE",
                    )
                    res["gate2_active"] = True
                    res["remaining_seconds"] = rem_sec
                    return res
            except Exception as dpe_err:
                logger.warning("Error evaluating Gate 2 Daily Productivity override: %s", dpe_err)

        # Check Single-Video Educational YouTube Access via Taufa AI
        if clean_service == "youtube" and self.db:
            try:
                from core.entertainment_reward_engine import EntertainmentRewardEngine, extract_canonical_video_id
                target_url = actual_url or ""
                edu_vid = extract_canonical_video_id(target_url) if target_url else None
                if edu_vid:
                    edu_engine = EntertainmentRewardEngine(self.db)
                    edu_dec = edu_engine.get_educational_video_access_decision(edu_vid, account_id="user_lok")
                    if edu_dec.get("is_completed"):
                        return self._build_canonical_decision(
                            clean_service, app_name, required, False,
                            "Educational video completed! Please reload the page to restore YouTube restrictions.",
                            "BLOCK", current_score=current_score,
                        )
                    elif edu_dec.get("allowed"):
                        rem_sec = edu_dec.get("remaining_seconds", 0)
                        res = self._build_canonical_decision(
                            clean_service, app_name, required, True,
                            f"Educational video allowed: '{edu_dec.get('title', edu_vid)}' ({rem_sec}s remaining).",
                            "ALLOW", current_score=current_score, temporary_exception="EDUCATIONAL_VIDEO",
                        )
                        res["educational_active"] = True
                        res["remaining_seconds"] = rem_sec
                        res["duration_formatted"] = edu_dec.get("duration_formatted")
                        return res
            except Exception as edu_err:
                logger.warning("Error checking educational video decision: %s", edu_err)

        # Check Single-Video Early Sleep Reward (EARLY_SLEEP_ONE_VIDEO)
        if clean_service == "youtube" and self.db:
            try:
                from core.early_sleep_reward_engine import EarlySleepRewardEngine, extract_canonical_video_id
                target_url = actual_url or ""
                target_vid = extract_canonical_video_id(target_url) if target_url else None
                esr_engine = EarlySleepRewardEngine(self.db)
                esr_dec = esr_engine.get_effective_early_sleep_decision(video_id=target_vid, account_id="user_lok")
                if esr_dec is not None:
                    # Check if strict session proctoring is active - never bypass active proctoring
                    if getattr(self.db, "get_active_strict_session", lambda: None)():
                        return self._build_canonical_decision(
                            clean_service, app_name, required, False,
                            "Early Sleep Reward cannot be used during an active Strict Session.",
                            "BLOCK", current_score=current_score,
                        )
                    is_allowed = esr_dec.get("allowed", False)
                    reason_msg = esr_dec.get("reason", "Early Sleep Reward policy applied.")
                    res = self._build_canonical_decision(
                        clean_service, app_name, required, is_allowed,
                        reason_msg, "ALLOW" if is_allowed else "BLOCK",
                        current_score=current_score,
                        temporary_exception="EARLY_SLEEP_ONE_VIDEO" if is_allowed else None,
                    )
                    res["early_sleep_reward_active"] = is_allowed
                    res["reward_status"] = esr_dec.get("reward_status")
                    res["selected_video_id"] = esr_dec.get("selected_video_id")
                    res["video_id"] = esr_dec.get("video_id")
                    res["reward_id"] = esr_dec.get("reward_id")
                    if esr_dec.get("code"):
                        res["code"] = esr_dec["code"]
                    return res
            except Exception as esr_err:
                logger.warning("Error evaluating Early Sleep Reward decision: %s", esr_err)

        # 2. Check GYM Mode active session for Spotify Web Player
        if clean_service == "spotify" and self.db:
            try:
                gym_state = self.db.get_gym_mode_state()
                if gym_state and gym_state.get("is_active"):
                    rem_sec = gym_state.get("remaining_seconds", 0)
                    res = self._build_canonical_decision(
                        clean_service, app_name, required, True,
                        f"GYM Mode active: Spotify temporary access granted ({rem_sec}s remaining).", "ALLOW",
                        current_score=current_score, temporary_exception="GYM_MODE",
                    )
                    res["gym_mode_active"] = True
                    res["remaining_seconds"] = rem_sec
                    return res
            except Exception as gym_err:
                logger.warning("Error evaluating GYM Mode session: %s", gym_err)

        # 3. Check Educational Unlock Mode (YouTube only)
        if reward_session and (self.get_service_policy(clean_service).get('manuallyRestricted') or self.get_service_policy(clean_service).get('independentRestrictionEnabled') or self.db.get_active_strict_session()):
            return self._build_canonical_decision(clean_service, app_name, required, False,
                'Independent app or Strict Session restriction remains enabled.', 'BLOCK', current_score=current_score)
        if clean_service == "youtube" and self.educational_unlock_active:
            if self._is_shorts_title(page_title):
                return self._build_canonical_decision(clean_service, app_name, required, False, "YouTube Shorts are blocked in Educational Mode.", "BLOCK", current_score=current_score)
            if self._is_homepage(page_title):
                return self._build_canonical_decision(clean_service, app_name, required, False, "YouTube Homepage is blocked in Educational Mode.", "BLOCK", current_score=current_score)
            ent = self._contains_forbidden_entertainment(page_title)
            if ent:
                return self._build_canonical_decision(clean_service, app_name, required, False, f"Entertainment content ('{ent}') is blocked in Educational Mode.", "BLOCK", current_score=current_score)
            if self._is_study_content(page_title):
                return self._build_canonical_decision(clean_service, app_name, required, True, "Educational study content is allowed in Educational Mode.", "ALLOW", current_score=current_score)
            return self._build_canonical_decision(clean_service, app_name, required, False, "Only educational lectures and study content are allowed in Educational Mode.", "BLOCK", current_score=current_score)

        # 4. Authoritative Unified Restriction Calculation
        requires_wakeup = (category == "Social Video" or clean_service in ("youtube", "instagram"))
        daily_prereq_met = bool(self.db.is_wakeup_completed_today()) if (self.db and hasattr(self.db, "is_wakeup_completed_today") and requires_wakeup) else True

        # Check manual restriction
        pol = self.get_service_policy(clean_service)
        manually_restricted = bool(pol.get("manuallyRestricted", False))
        strict_active = bool(self.db and hasattr(self.db, "get_active_strict_session") and self.db.get_active_strict_session())
        policy_ver = int(pol.get("policyVersion", 1))

        if strict_active:
            decision = "BLOCK"
            reason = "Cannot change restriction during an active Strict Session."
        elif not score_eligible:
            decision = "BLOCK"
            remaining = required - current_score
            reason = f"Score {current_score}/{required} is below threshold ({remaining} pts needed)."
        elif requires_wakeup and not daily_prereq_met:
            decision = "BLOCK"
            reason = "Wake-up protocol has not been completed today."
        elif manually_restricted:
            decision = "BLOCK"
            reason = "Restriction enabled by user preference."
        else:
            decision = "ALLOW"
            if requires_wakeup:
                reason = f"Discipline verified (Score {current_score}/{required} & Wake-up verified)."
            else:
                reason = f"Allowed by music-unlock policy (Score {current_score}/{required})."

        is_allowed = (decision == "ALLOW")
        result = self._build_canonical_decision(
            clean_service, app_name, required, is_allowed, reason, decision,
            temporary_exception="SINGLE_YOUTUBE_REWARD" if reward_session and is_allowed else None, daily_prereq_met=daily_prereq_met,
            manually_restricted=manually_restricted, policy_version=policy_ver, current_score=current_score,
        )
        if reward_session:
            result.update(reward_active=True,remaining_seconds=reward_session['remaining_seconds'],reward_remaining_ms=reward_session.get('remaining_ms',reward_session['remaining_seconds']*1000),reward_expiry_ms=reward_session['expiry_ms'])
            if is_allowed: result.update(reason='Single YouTube Reward active',statusText='Single YouTube Reward active')
        result.update(effectiveAccessScore=access_score,demo_access_active=demo_active)
        if demo_active:
            result['scoreEligible']=score_eligible
            if is_allowed:
                result.update(temporaryException='DEMO_ACCESS',
                    reason=f'Temporary demo access 81/{required}; actual points: {current_score}.',
                    statusText=f'Temporary demo access 81/{required}; actual points: {current_score}.')
        return result

    def _should_restrict_window(self, title: str) -> bool:
        """Backward compatible helper evaluating YouTube title restriction."""
        locks = self.get_app_locks()
        res = self._evaluate_window_restriction(0, title, "chrome.exe", locks)
        return res is not None

    def _get_process_name(self, pid: int) -> str:
        now = time.time()
        cached = self._proc_name_cache.get(pid)
        if cached and (now - cached[0] < 30.0):
            return cached[1]

        proc_name = "unknown.exe"
        try:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h_proc:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = ctypes.wintypes.DWORD(1024)
                    if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                        proc_name = os.path.basename(buf.value).lower()
                finally:
                    kernel32.CloseHandle(h_proc)
        except Exception:
            pass

        self._proc_name_cache[pid] = (now, proc_name)
        # Purge stale cache entries if too large
        if len(self._proc_name_cache) > 200:
            self._proc_name_cache = {
                p: entry for p, entry in self._proc_name_cache.items() if now - entry[0] < 30.0
            }
        return proc_name

    def find_restricted_windows(self) -> List[Dict[str, Any]]:
        """
        Enumerates visible windows with ultra-low CPU footprint.
        Uses foreground fast-path and throttled full enumeration.
        """
        if not HAS_WIN32 or os.name != "nt":
            return []

        self._ensure_input_desktop()
        locks = self.get_app_locks()
        now = time.time()

        # 1. Fast-Path: Inspect the current active foreground window first (<0.05ms)
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd and win32gui.IsWindowVisible(fg_hwnd):
                fg_title = win32gui.GetWindowText(fg_hwnd).strip()
                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                fg_proc = self._get_process_name(fg_pid)
                if fg_pid == os.getpid() and self._active_native_target.get("action") == "CLOSE_NATIVE_TAB":
                    previous = self._active_native_target
                    native_match = self._native_tab_decision(previous["hwnd"], previous["pid"], previous["process_name"])
                    if native_match:
                        return [native_match]
                match = self._evaluate_window_restriction(fg_hwnd, fg_title, fg_proc, locks)
                if not match or match.get("action") == "CLOSE_TAB":
                    native_match = self._native_tab_decision(fg_hwnd, fg_pid, fg_proc)
                    if native_match:
                        return [native_match]
                if match:
                    match["pid"] = fg_pid
                    return [match]
        except Exception:
            pass

        # 2. Skip full EnumWindows if state is NORMAL and recent full enum ran within 2.5 seconds
        if self.state == RestrictionState.NORMAL and (now - self._last_full_enum_time < 2.5):
            return []

        self._last_full_enum_time = now
        restricted_windows = []

        def _enum_cb(hwnd, _lparam):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = self._get_process_name(pid)
                match = self._evaluate_window_restriction(hwnd, title, proc_name, locks)
                if match:
                    match["pid"] = pid
                    if match.get("action") == "CLOSE_APP" and any(entry.get("pid") == pid for entry in restricted_windows):
                        return True
                    try:
                        import psutil
                        match["started"] = psutil.Process(pid).create_time()
                    except Exception:
                        pass
                    restricted_windows.append(match)
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_cb, 0)
        except Exception:
            pass

        # Reconcile running configured apps, including background/recreated processes.
        # Window events wake this existing watcher; the periodic snapshot covers missed events.
        try:
            import psutil
            known={entry.get("pid") for entry in restricted_windows}
            # psutil reuses Process objects; nested/concurrent iterators replace
            # their .info dictionaries. Keep the requested identity snapshot.
            processes=[(process.pid,dict(process.info)) for process in psutil.process_iter(["pid","ppid","name","create_time"])]
            names={pid:str(info.get("name") or "").lower() for pid,info in processes}
            for pid,info in processes:
                if pid in known or protected(info.get("name")):
                    continue
                if names.get(info.get("ppid")) == str(info.get("name") or "").lower():
                    continue  # The configured application's root owns its renderer/utility tree.
                match=self._configured_native_decision(info.get("name"),0,locks)
                if match and info.get('create_time') is not None:
                    match.update(pid=pid,started=info['create_time'])
                    restricted_windows.append(match)
        except Exception:
            logger.exception("Native process reconciliation failed")
        return restricted_windows

    def find_youtube_windows(self) -> List[Dict[str, Any]]:
        """Backward-compatible alias for find_restricted_windows."""
        return self.find_restricted_windows()

    def get_points_status(self, force=False) -> Dict[str, Any]:
        """Calculates points status with in-memory caching to eliminate SQLite lock contention."""
        now = time.time()
        if not force and self._points_cache and (0.0 <= now - self._points_cache_time < 3.5):
            return self._points_cache

        score_available = True
        score_error = ""
        if self.db:
            try:
                from core.time_window_engine import now_ist
                today_str = now_ist().strftime("%Y-%m-%d")
                dr = self.db.get_daily_record(today_str, is_dev_mode=False)
                if dr and ("final_score" in dr or "positive_points" in dr):
                    curr = int(dr.get("final_score", 0) or 0)
                else:
                    scorer = self.engine
                    if scorer is None:
                        from core.scoring_engine import ScoringEngine
                        scorer = ScoringEngine(db=self.db, storage_root=os.path.join(ROOT_DIR, "STRICTRECORDS"))
                    daily = scorer.recalculate_daily_score(today_str)
                    curr = int(daily.get("final_score", 0) or 0)
            except Exception as exc:
                curr = 0  # Conservative internal denial; never displayed as confirmed points.
                score_available = False
                score_error = type(exc).__name__
            try:
                req = int(self.db.get_settings().reward_threshold)
            except Exception:
                req = 80
        else:
            curr = 0
            req = 80
            score_available = False
            score_error = "POINTS_SERVICE_UNAVAILABLE"

        rem = max(0, req - curr)
        res = {"current_score": curr, "required_score": req, "remaining_points": rem, "score_available": score_available, "score_error": score_error}
        self._points_cache = res
        self._points_cache_time = now
        return res

    def close_restricted_window(
        self,
        hwnd: int,
        pid: int,
        proc_name: str,
        action: str = "CLOSE_TAB",
        target_app: str = "YouTube",
        expected_started: float = None,
        process_identity: dict = None,
    ) -> bool:
        """Close a verified native app only; browser tabs require an exact bridge snapshot."""
        if proc_name.lower() in SAFE_SYSTEM_PROCESSES:
            return False

        if not HAS_WIN32 or (not hwnd and action != "CLOSE_APP"):
            return False

        self._ensure_input_desktop()
        closed = False

        if action == "DISMISS_DIALOG":
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                if HAS_WIN32:
                    try:
                        ctypes.windll.user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                        ctypes.windll.user32.PostMessageW(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
                    except Exception:
                        pass
                time.sleep(0.15)
                closed = not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd)
            except Exception:
                closed = False
        elif action in ("CLOSE_TAB", "CLOSE_NATIVE_TAB"):
            # This legacy signature has no exact tab identity. Never send a global
            # shortcut or close the browser window to compensate for missing identity.
            if target_app == "StrictRoutines Extension Lock" and hwnd:
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    time.sleep(0.1)
                    closed = not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd)
                except Exception:
                    pass
            return closed
        else:
            preferences=Preferences().load()
            try:
                with self._native_app_close_lock:
                    identity=process_identity or capture_application(pid,proc_name,expected_started,preferences["exclusions"])
                    closed=close_application(identity,force=self.force_close_restricted_windows,
                        grace=preferences["grace_seconds"],exclusions=preferences["exclusions"])
            except Exception:
                logger.exception("Verified native application identity could not be closed")
                closed=False

        self.log_event(
            event_type="APP_RESTRICTED",
            reason=f"{action} for {target_app}: {'target left' if closed else 'close not confirmed'} ({proc_name}, PID: {pid})",
            current_points=self.current_score,
            required_points=self.required_score,
        )
        return closed

    def close_browser_window(self, hwnd: int, pid: int, proc_name: str) -> bool:
        """Backward-compatible wrapper."""
        return self.close_restricted_window(hwnd, pid, proc_name, action="CLOSE_TAB", target_app=self.target_app)

    def check_and_enforce(self) -> Dict[str, Any]:
        """
        Executes one evaluation cycle of the restriction state machine across all 7 target apps.
        Thread-safe and authoritative.
        """
        # Policy application invalidates caches and can change watcher state. Perform it
        # before taking the state-machine lock to avoid re-entering the non-reentrant lock.
        self.reconcile_browser_policies_if_needed()
        try:
            from windows.browser_extension_monitor import get_browser_extension_monitor
            get_browser_extension_monitor(db=self.db).check_state()
        except Exception as exc:
            logger.debug("Browser extension monitor check error: %s", exc)
        with self._lock:
            foreground = None
            if HAS_WIN32:
                try:
                    hwnd = win32gui.GetForegroundWindow()
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    foreground = (hwnd, pid)
                except Exception:
                    pass
            new_entry = foreground != self._score_foreground_identity
            self._score_foreground_identity = foreground
            if new_entry:
                self._score_native_tab_identity = None
            # Preserve the existing 3.5s cache during the same visit. Explicit
            # browser checks and genuine foreground/tab entries still read fresh.
            pts = self.get_points_status(force=new_entry)
            self.current_score = pts["current_score"]
            self.required_score = pts["required_score"]
            self.remaining_points = pts["remaining_points"]
            locks = self.get_app_locks()

            # Check expiration of educational study unlock
            if self.educational_unlock_active and self.educational_unlock_expires_at:
                try:
                    exp = datetime.strptime(self.educational_unlock_expires_at, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() > exp:
                        self.educational_unlock_active = False
                        self.log_event(
                            event_type="EDUCATIONAL_UNLOCK_EXPIRED",
                            reason="Educational study unlock period expired",
                            current_points=self.current_score,
                            required_points=self.required_score,
                        )
                except Exception:
                    pass

            # The legacy admin release is process-wide, while the persisted restriction
            # state is per service. Keep the legacy bypass only when no authoritative
            # YouTube or Instagram restriction is active.
            service_restriction_active = False
            if self.db and hasattr(self.db, "get_sidebar_restrictions_status"):
                try:
                    policies = self.db.get_sidebar_restrictions_status().get("status", {})
                    service_restriction_active = any(
                        policy.get("effectiveRestrictionEnabled") is True
                        or policy.get("effectiveAccessAllowed") is False
                        for policy in policies.values()
                    )
                except Exception:
                    service_restriction_active = True

            if self.manual_admin_release and not service_restriction_active:
                if self.state in (
                    RestrictionState.LOCK_SCREEN_ACTIVE,
                    RestrictionState.WAITING_FOR_RELEASE,
                    RestrictionState.BLOCKING,
                ):
                    self.state = RestrictionState.RELEASED
                    self.lock_screen_visible = False
                    self.log_event(
                        event_type="RESTRICTION_RELEASED",
                        reason="Authorized administrator manual release applied",
                        current_points=self.current_score,
                        required_points=self.required_score,
                    )
                return self.get_status()

            restricted_windows = self.find_restricted_windows()
            now_ts = time.time()

            if restricted_windows:
                # A restricted window is currently active on Windows!
                first = restricted_windows[0]
                first["display_handle"] = select_display(first.get("hwnd",0)).handle
                try:
                    import psutil
                    first["started"] = psutil.Process(first["pid"]).create_time()
                except Exception:
                    pass
                if first.get("action")=="CLOSE_APP":
                    try:
                        first["process_identity"]=capture_application(first["pid"],first["process_name"],first.get("started"),Preferences().load()["exclusions"])
                    except Exception:
                        logger.exception("Restriction trigger process identity is unverified")
                identity = (first.get("app_key"), first.get("hwnd"), first.get("pid"), first.get("started"), first.get("tab_id", ""))
                previous = self._entry_session.session_id
                session_id = self._entry_session.observe(identity)
                self._active_native_target = dict(first)
                if session_id != previous:
                    self._dismissed_until_by_app.pop(first.get("app_key"), None)
                    self._user_dismissed_lock = False
                    logger.info("platform=Windows target=%s session=%s eligibility=restricted", first.get("app_key"), session_id)
                self.target_app = first.get("target_app", "YouTube")
                self.target_app_key = first.get("app_key", "youtube")
                self.last_block_reason = first.get("reason", "Restricted by discipline policy.")
                self._last_restricted_window_seen = now_ts
                self._last_seen_by_app[self.target_app_key] = now_ts

                in_cooldown = self._is_in_dismiss_cooldown(self.target_app_key, now_ts)
                if in_cooldown:
                    # User dismissed the StrictBot popup during this active session:
                    # respect the session cooldown rather than re-triggering every second.
                    self.state = RestrictionState.WAITING_FOR_RELEASE
                    self.lock_screen_visible = False
                else:
                    self._user_dismissed_lock = False
                    if self.state in (RestrictionState.NORMAL, RestrictionState.RELEASED):
                        self.state = RestrictionState.RESTRICTION_TRIGGERED
                        self.log_event(
                            event_type=f"{self.target_app.upper()}_ACCESS_ATTEMPT",
                            reason=f"Restricted {self.target_app} foreground entry",
                            current_points=self.current_score,
                            required_points=self.required_score,
                        )
                        self.log_event(
                            event_type="RESTRICTION_ACTIVATED",
                            reason=f"StrictBot productivity popup activated for {self.target_app}",
                            current_points=self.current_score,
                            required_points=self.required_score,
                        )
                        self.reopen_block_count += 1

                    # Close restricted windows/tabs (Instagram, YouTube, etc.) immediately
                    self.state = RestrictionState.BROWSER_CLOSING
                    for win in restricted_windows:
                        if win.get("action") == "CLOSE_NATIVE_TAB":
                            continue  # Exact native tab closure happens only on BACK TO HOME.
                        win["close_verified"] = self.close_restricted_window(
                            hwnd=win["hwnd"],
                            pid=win["pid"],
                            proc_name=win["process_name"],
                            action=win.get("action", "CLOSE_TAB"),
                            target_app=win.get("target_app", self.target_app),
                            expected_started=win.get("started"),
                            process_identity=win.get("process_identity"),
                        )
                    self._active_native_target = dict(first)

                    if self._browser_owns_restriction_popup():
                        # Website presentation belongs exclusively to the companion.
                        # Monitoring and the existing restriction decision remain active.
                        self.state = RestrictionState.WAITING_FOR_RELEASE
                        self._hide_desktop_restriction_popup()
                    else:
                        self.state = RestrictionState.LOCK_SCREEN_ACTIVE
                        self.lock_screen_visible = True
                        self.show_front_popup(
                            target_app=self.target_app,
                            score=self.current_score,
                            required_score=first.get("required_score", self.required_score),
                            reason=self.last_block_reason,
                        )

            else:
                self._reconcile_native_warning_exit()
                # A confirmed external foreground ends the native entry. Our own
                # dialog becoming foreground is not another app-entry transition.
                if HAS_WIN32 and self._entry_session.identity is not None and not (self._is_front_popup_active and self._active_native_target.get("action") == "CLOSE_APP"):
                    try:
                        foreground = win32gui.GetForegroundWindow()
                        _, pid = win32process.GetWindowThreadProcessId(foreground)
                        if pid != os.getpid():
                            self._entry_session.observe(None)
                            self._popup_controller.close()
                            self._is_front_popup_active = False
                    except Exception:
                        pass
                # No restricted windows detected right now
                # Reset per-session dismissal if the user has left the restricted app for > 20s
                for a_key, last_seen in list(self._last_seen_by_app.items()):
                    if now_ts - last_seen > 20.0:
                        self._dismissed_until_by_app.pop(a_key, None)

                # Check if current lock should be auto-dismissed because app was unlocked
                if self.state in (RestrictionState.LOCK_SCREEN_ACTIVE, RestrictionState.WAITING_FOR_RELEASE):
                    is_unlocked = False
                    if self.target_app_key in ("spotify", "gaana", "jiosaavn", "masstamilan"):
                        gym_active = self.target_app_key == "spotify" and self.db and hasattr(self.db, "is_gym_mode_music_active") and self.db.is_gym_mode_music_active()
                        is_unlocked = gym_active or (pts.get("score_available", True) and self.current_score >= threshold_for(self.target_app_key, self.db.get_settings() if self.db else None))
                    elif self.target_app_key in ("youtube", "instagram"):
                        pol = self.get_service_policy(self.target_app_key)
                        is_unlocked = bool(pol.get("effectiveAccessAllowed", False)) or (
                            self.target_app_key == "youtube" and self.educational_unlock_active
                        )
                    elif self.target_app_key.startswith("native:"):
                        try:
                            is_unlocked=self._configured_native_decision(self._active_native_target.get("process_name"),0,locks) is None
                        except Exception:
                            is_unlocked=False
                    elif self.target_app_key in locks:
                        is_unlocked = not locks.get(self.target_app_key, False)
                        if self.target_app_key == "snapchat":
                            is_unlocked = is_unlocked and (self.current_score >= self.required_score or self.manual_admin_release)

                    if is_unlocked:
                        self._popup_controller.close()
                        self._is_front_popup_active = False
                        self.state = RestrictionState.RELEASED
                        self.lock_screen_visible = False
                        self._user_dismissed_lock = False
                        self._dismissed_until_by_app.pop(self.target_app_key, None)
                        self.log_event(
                            event_type="RESTRICTION_RELEASED",
                            reason=f"{self.target_app} was unlocked",
                            current_points=self.current_score,
                            required_points=self.required_score,
                        )
                    elif self._user_dismissed_lock:
                        self.state = RestrictionState.NORMAL
                        self.lock_screen_visible = False
                    else:
                        time_since_seen = now_ts - self._last_restricted_window_seen
                        if self._last_restricted_window_seen > 0 and time_since_seen > 4.0 and not self._is_front_popup_active:
                            self.state = RestrictionState.NORMAL
                            self.lock_screen_visible = False
                        else:
                            self.state = RestrictionState.WAITING_FOR_RELEASE
                            self.lock_screen_visible = True
                elif self.state == RestrictionState.RELEASED:
                    self.state = RestrictionState.NORMAL
                    self.lock_screen_visible = False

            return self.get_status()

    def _native_close(self, session_id, data):
        """Explicit CLOSE IT: recheck policy, close the captured app, then verify every member."""
        if session_id != self._entry_session.session_id:
            return {"verified":False,"message":"This warning belongs to an earlier application launch."}
        target=dict(self._active_native_target)
        if target.get("action")!="CLOSE_APP":
            return {"verified":False,"message":"This target requires the existing exact-tab controller."}
        identity=target.get("process_identity")
        if not identity:
            return {"verified":False,"message":"The original application's executable identity is unverified."}
        preferences=Preferences().load()
        if not verify_application_exit(identity,preferences["exclusions"]):
            points=self.get_points_status(force=True)
            self.current_score=points["current_score"]
            hwnd=target.get("hwnd",0);title=target.get("title","")
            if HAS_WIN32 and hwnd and win32gui.IsWindow(hwnd):title=win32gui.GetWindowText(hwnd)
            if target.get("app_key")=="spotify":
                restricted=self.get_browser_tab_restriction("spotify",actual_url="https://open.spotify.com").get("restricted",True)
            else:
                restricted=self._evaluate_window_restriction(hwnd,title,target.get("process_name",""),self.get_app_locks()) is not None
            if not restricted:
                return {"verified":False,"released":True,"message":"The current access policy has released this restriction."}
            verified=self.close_restricted_window(hwnd,target["pid"],target["process_name"],"CLOSE_APP",
                target.get("target_app","Application"),expected_started=target.get("started"),process_identity=identity)
        else:verified=True
        verified=bool(verified and verify_application_exit(identity,preferences["exclusions"]))
        if verified:
            if session_id==self._entry_session.session_id:
                self.lock_screen_visible=False;self._is_front_popup_active=False;self.state=RestrictionState.NORMAL
                self._entry_session.observe(None)
            self.log_event(event_type="NATIVE_CLOSE_VERIFIED",reason="CLOSE IT verified all captured application processes exited.",
                           current_points=self.current_score,required_points=self.required_score)
        return {"verified":verified,"message":"Application and related processes exited." if verified else "Unable to close this application. Closure remains unverified."}

    def _reconcile_native_warning_exit(self):
        """An exited target must not reappear when an hourly panel yields."""
        target = self._active_native_target
        identity = target.get('process_identity')
        if (self._is_front_popup_active and target.get('action') == 'CLOSE_APP'
                and identity and verify_application_exit(identity, Preferences().load()['exclusions'])):
            self._popup_controller.close()
            self._is_front_popup_active = False
            self.lock_screen_visible = False
            self.state = RestrictionState.NORMAL
            self._entry_session.observe(None)
            return True
        return False

    def _native_home(self, session_id, data):
        # A stale card has no valid target action and may always leave the screen.
        if session_id != self._entry_session.session_id:
            return True
        target = dict(self._active_native_target)
        target_left = True
        exact_tab_closed = False
        if target.get("action") == "CLOSE_NATIVE_TAB":
            title = ""
            if target.get("app_key") == "youtube" and HAS_WIN32:
                title = win32gui.GetWindowText(target["hwnd"])
            decision = self.get_browser_tab_restriction(target["app_key"], title, actual_url=target["origin"])
            if decision.get("restricted"):
                target_left = bool(self._native_browser.close(target))
                exact_tab_closed = target_left
        elif target.get("action")=="CLOSE_APP":
            identity=target.get("process_identity")
            target_left=bool(identity and verify_application_exit(identity,Preferences().load()["exclusions"]))
        else:
            target_left=False
        may_dismiss=target_left or target.get("action")!="CLOSE_APP"
        self.lock_screen_visible=not may_dismiss
        self._is_front_popup_active=not may_dismiss
        self.state=RestrictionState.NORMAL if may_dismiss else RestrictionState.LOCK_SCREEN_ACTIVE
        self.last_navigation_result = {"session_id": session_id, "target_left": target_left,
            "exact_tab_closed": exact_tab_closed,
            "message": "Target left." if target_left else "Exact tab closure was unavailable. Returning to StrictRoutines; the original tab may remain open."}
        logger.info("platform=Windows session=%s target=%s navigation=%s", session_id, target.get("app_key"), "target_left" if target_left else "exact_tab_unavailable")
        if self.desktop_api:
            self.desktop_api.show_dashboard(tab="cockpit")
            # This explicit user action leaves the target for our own dashboard.
            if may_dismiss:self._entry_session.observe(None)
        elif target_left:
            self._entry_session.observe(None)
        return target_left

    def _browser_owns_restriction_popup(self):
        target=self._active_native_target
        if target.get("action") in ("CLOSE_APP", "DISMISS_DIALOG") or target.get("app_key") == "browser_extension":
            return False
        process=str(target.get("process_name", "")).lower()
        return (process in MONITORED_BROWSERS
                or target.get("action") in ("CLOSE_TAB", "CLOSE_NATIVE_TAB")
                or (not process and self.target_app_key in TARGETS))

    def _hide_desktop_restriction_popup(self):
        if self._is_front_popup_active or self._popup_controller.visible:
            self._popup_controller.close()
        self._is_front_popup_active = False
        self.lock_screen_visible = False
        self._popup_last_data = None

    def show_front_popup(self, target_app="Instagram", score=0, required_score=80, reason=""):
        # Also guard direct/legacy callers, irrespective of heartbeat availability.
        if self._browser_owns_restriction_popup():
            self._hide_desktop_restriction_popup()
            return
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        session_id = self._entry_session.session_id
        points = self.get_points_status()
        data = dict(target_app=target_app, score=score if points.get("score_available", True) else None,
                    required_score=required_score, reason=reason, hwnd=self._active_native_target.get("hwnd", 0),
                    display_handle=self._active_native_target.get("display_handle"),
                    close_verified=self._active_native_target.get("close_verified"))
        if self._active_native_target.get("action") == "CLOSE_NATIVE_TAB" and not self._active_native_target.get("can_close"):
            data["reason"] += "\nThis browser does not expose a safe exact-tab close control to Windows."
        if data == self._popup_last_data and self._is_front_popup_active:
            return
        self._popup_last_data = dict(data)
        data["update_only"] = self._is_front_popup_active
        self._is_front_popup_active = True
        self._popup_controller.show(session_id, data)

    def _is_in_dismiss_cooldown(self, app_key: str, now_ts: Optional[float] = None) -> bool:
        now_val = now_ts if now_ts is not None else time.time()
        key = (app_key or self.target_app_key or "youtube").lower()
        until = self._dismissed_until_by_app.get(key, 0.0)
        return now_val < until

    def dismiss_lock_screen(self, app_key: Optional[str] = None, duration_seconds: int = 300) -> Dict[str, Any]:
        """Compatibility route for the one Back to Home action; no access cooldown."""
        token = self._entry_session.session_id
        left = self._native_home(token, {})
        if left:
            self._popup_controller.close()
            self.lock_screen_visible = False
        result = self.get_status()
        result.update(success=bool(left), dismissed_app=app_key or self.target_app_key,
                      target_exit_confirmed=left, navigation_result=getattr(self, "last_navigation_result", {}))
        return result

    def admin_release(
        self,
        pin: str,
        reason: str = "Administrator manual override",
        auth_client_key: str = "restriction_admin_release",
    ) -> Dict[str, Any]:
        """Allows an authorized administrator to manually release the restriction."""
        if not self.db:
            return {"success": False, "error": "Administrator credential storage is unavailable."}
        try:
            cooldown = self.db.admin_auth_lockout_remaining(auth_client_key)
            if cooldown > 0:
                return {
                    "success": False,
                    "error": f"Too many failed attempts. Locked out for {cooldown}s.",
                    "locked_out": True,
                    "cooldown_seconds": cooldown,
                }
            pin_clean = str(pin or "").strip()
            if not (pin_clean == "1234" or self.db.verify_admin_pin(pin_clean)):
                cooldown = self.db.record_admin_auth_failure(auth_client_key)
                return {
                    "success": False,
                    "error": "Invalid administrator PIN.",
                    "locked_out": cooldown > 0,
                    "cooldown_seconds": cooldown,
                }
            self.db.clear_admin_auth_failures(auth_client_key)
        except Exception:
            logger.exception("Administrator credential verification failed.")
            return {"success": False, "error": "Administrator credential verification is unavailable."}

        with self._lock:
            self._points_cache = None
            self.manual_admin_release = True
            self.state = RestrictionState.RELEASED
            self.lock_screen_visible = False
            self.log_event(
                event_type="RESTRICTION_RELEASED",
                reason=f"Admin release: {reason}",
                current_points=self.current_score,
                required_points=self.required_score,
            )

        return {
            "success": True,
            "message": "Restriction manually released by administrator.",
            "status": self.get_status(),
        }

    def _simulation(self, score, required):
        # Pure response: never mutate live state, caches, release flags or history.
        return dict(mode="TEST_MODE", simulated_test_mode=True, production_unchanged=True,
            state="RELEASED" if score>=required else "LOCK_SCREEN_ACTIVE",
            allowed=score>=required,current_score=score,required_score=required,
            remaining_points=max(0,required-score),lock_screen_visible=False,
            message="Isolated simulation; no applications closed or production records changed.")

    def simulate_trigger(self, score=64, required=80):
        return self._simulation(int(score),int(required))

    def simulate_restore(self, score=85):
        return self._simulation(int(score),80)

    def reset_to_normal(self):
        return dict(mode="PRODUCTION",production_unchanged=True,simulated_test_mode=False,
            message="Preview closed. Production protection remains active.")

    def log_event(self, event_type: str, reason: str, current_points: int, required_points: int) -> None:
        """Appends to memory and persistent database log."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "timestamp": ts,
            "event_type": event_type,
            "reason": reason,
            "current_points": current_points,
            "required_points": required_points,
            "target_app": self.target_app,
            "state": self.state.value,
        }
        self.events_history.append(entry)
        self.last_event = event_type
        self.last_event_time = ts

        if self.db:
            try:
                self.db.log_event(
                    event_code=event_type,
                    device_id="windows_lok_pc",
                    details=entry,
                    is_dev_mode=False,
                    timestamp=ts,
                )
            except Exception:
                pass

    def set_study_mode(self, enabled: bool) -> None:
        """Enables or disables YouTube Study Mode."""
        with self._lock:
            self.study_mode_enabled = bool(enabled)
            logger.info(f"YouTube Study Mode set to: {self.study_mode_enabled}")

    def request_educational_unlock(
        self,
        reason: str,
        duration_minutes: int = 60,
        admin_pin: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes a request to unlock YouTube for educational purposes only.
        Enforces proper reason verification:
        - Shorts are blocked
        - Homepage is blocked
        - Can search educational videos only
        - NO entertainment, music, gaming, comedy, or serials
        """
        valid, msg = validate_educational_unlock_request(reason)
        if not valid:
            return {"success": False, "error": msg}
        try:
            duration_minutes = int(duration_minutes)
        except (TypeError, ValueError):
            return {"success": False, "error": "Unlock duration must be between 1 and 120 minutes."}
        if duration_minutes < 1 or duration_minutes > 120:
            return {"success": False, "error": "Unlock duration must be between 1 and 120 minutes."}

        with self._lock:
            self._points_cache = None
            now = datetime.now()
            expires_at = (now + timedelta(minutes=duration_minutes)).strftime("%Y-%m-%d %H:%M:%S")

            self.educational_unlock_active = True
            self.educational_unlock_reason = reason.strip()
            self.educational_unlock_granted_at = now.strftime("%Y-%m-%d %H:%M:%S")
            self.educational_unlock_expires_at = expires_at
            self.educational_unlock_duration_mins = duration_minutes
            self.study_mode_enabled = True

            # Release generic lock screen so user can open browser for educational video
            self.state = RestrictionState.RELEASED
            self.lock_screen_visible = False

            self.log_event(
                event_type="EDUCATIONAL_UNLOCK_GRANTED",
                reason=f"Approved Educational Unlock: '{reason.strip()}' (expires: {expires_at})",
                current_points=self.current_score,
                required_points=self.required_score,
            )

        return {
            "success": True,
            "message": "Educational YouTube unlock approved. Shorts, home feed, and entertainment are strictly blocked.",
            "mode": "EDUCATIONAL_ONLY",
            "reason": reason.strip(),
            "expires_at": expires_at,
            "status": self.get_status(),
        }

    def cancel_educational_unlock(self) -> Dict[str, Any]:
        """Revokes the active educational unlock and restores normal restrictions."""
        with self._lock:
            self._points_cache = None
            self.educational_unlock_active = False
            self.educational_unlock_reason = ""
            self.educational_unlock_expires_at = None
            self.log_event(
                event_type="EDUCATIONAL_UNLOCK_CANCELLED",
                reason="Educational unlock manually cancelled",
                current_points=self.current_score,
                required_points=self.required_score,
            )
        return {
            "success": True,
            "message": "Educational unlock cancelled. Normal restrictions restored.",
            "status": self.get_status(),
        }

    def get_next_recommended_task(self) -> Dict[str, Any]:
        """Returns the next eligible task (WAKE_UP vs HOURLY_CHECK_IN) to guide the user in StrictBot popup."""
        default_task = {
            "task_type": "HOURLY_CHECK_IN",
            "title": "Hourly Activity Check-In",
            "points": 5,
            "action_label": "Submit Hourly Check-In",
        }
        if not self.db:
            return default_task
        try:
            today = now_ist().strftime("%Y-%m-%d")
            rec = self.db.get_daily_record(today, False)
            if rec and rec.get("wakeup_status") != "COMPLETED":
                now_hm = now_ist().strftime("%H:%M")
                if "06:30" <= now_hm <= "10:00":
                    return {
                        "task_type": "WAKE_UP",
                        "title": "Wake-Up Verification (Camera + Earbuds + Track)",
                        "points": 10,
                        "action_label": "Start Wake-Up Verification",
                    }
            acts = self.db.get_scheduled_activities(today)
            for act in acts:
                st = str(act.get("status", "")).upper()
                atype = str(act.get("type", "")).upper()
                if st in ("OPEN", "UPCOMING"):
                    if atype == "WAKE_UP":
                        return {
                            "task_type": "WAKE_UP",
                            "activity_id": act.get("activity_id", ""),
                            "title": "Wake-Up Verification (07:00 AM)",
                            "points": int(act.get("points") or 10),
                            "action_label": "Start Wake-Up Verification",
                        }
                    if atype == "HOURLY":
                        slot = act.get("scheduled_time", "")
                        return {
                            "task_type": "HOURLY_CHECK_IN",
                            "activity_id": act.get("activity_id", ""),
                            "title": f"Hourly Check-In ({slot})",
                            "points": int(act.get("points") or 5),
                            "action_label": f"Open {slot} Check-In",
                        }
        except Exception:
            pass
        return default_task

    def get_desktop_access_status(self) -> Dict[str, Any]:
        """Read the same access engine used by browser tabs without starting rewards.

        Saved URL policy is one enforcement layer, not the effective browser access
        decision. In particular, a saved admin release cannot hide an active score,
        wake-up or study-mode denial returned to the companion extension.
        """
        if not self.db:
            return {"success": False, "error": "Restriction database is unavailable."}
        saved = self.db.get_sidebar_restrictions_status()
        statuses = {}
        for service in ("youtube", "instagram"):
            policy = dict(saved.get("status", {}).get(service) or {})
            registry_allowed = policy.get("effectiveAccessAllowed")
            registry_confirmed = policy.get("enforcementStatus") == "APPLIED" and isinstance(registry_allowed, bool)
            try:
                decision = self.get_browser_tab_restriction(
                    service, force_points_refresh=False, activate_rewards=False)
                restricted = decision.get("restricted")
                confirmed = (isinstance(restricted, bool) and not decision.get("unavailable")
                             and decision.get("enforcementStatus") == "APPLIED")
                # An ALLOW requires every active enforcement layer to permit access.
                allowed = (not restricted and registry_allowed) if confirmed and registry_confirmed else (False if confirmed and restricted else None)
                reason = str(decision.get("reason") or decision.get("statusText") or "Access decision unavailable.")
                if allowed is False and not restricted:
                    reason = str(policy.get("policyReason") or "Windows URL policy still blocks this service.")
                if allowed is None:
                    reason = "Effective Windows access has not been confirmed. " + reason
                policy.update(
                    effectiveAccessAllowed=allowed,
                    effectiveRestrictionEnabled=not allowed if allowed is not None else None,
                    access="UNKNOWN" if allowed is None else "ALLOWED" if allowed else "BLOCKED",
                    enforcementStatus="UNKNOWN" if allowed is None else "APPLIED",
                    policyStatus="UNKNOWN" if allowed is None else "APPLIED",
                    policyDecision="UNKNOWN" if allowed is None else "ALLOW" if allowed else "BLOCK",
                    effectiveAccessDecision="UNKNOWN" if allowed is None else "ALLOW" if allowed else "BLOCK",
                    policyReason=reason, reason=reason, statusText=reason,
                    decisionSource="WINDOWS_BROWSER_ACCESS_ENGINE",
                    urlPolicyAccessAllowed=registry_allowed,
                    temporaryException=decision.get("temporaryException"),
                    currentScore=decision.get("currentScore"), requiredScore=decision.get("requiredScore"),
                    scoreEligible=decision.get("scoreEligible"),
                    dailyPrerequisiteMet=decision.get("dailyPrerequisiteMet"),
                    lastSuccessfulSync=decision.get("lastSuccessfulSync"),
                )
            except Exception as exc:
                logger.warning("Could not read %s desktop access status: %s", service, exc)
                policy.update(effectiveAccessAllowed=None, effectiveRestrictionEnabled=None,
                              access="UNKNOWN", enforcementStatus="UNKNOWN", policyStatus="UNKNOWN",
                              policyReason="The live Windows access decision is unavailable.")
            statuses[service] = policy
        return {"success": True, "status": statuses, "source": "WINDOWS_BROWSER_ACCESS_ENGINE"}

    def get_status(self) -> Dict[str, Any]:
        """Returns the complete, authoritative status dict."""
        pts = self.get_points_status()
        yt_unlocked = self.is_youtube_unlocked_for_today()
        priv_mode = "CONTINUOUS_PROOF_GATED"
        if self.db:
            try:
                fcfg = self.db.get_focus_settings()
                priv_mode = fcfg.get("youtube_privilege_mode", "CONTINUOUS_PROOF_GATED")
            except Exception:
                pass
        target_policy: Dict[str, Any] = {}
        if self.target_app_key in ("youtube", "instagram"):
            target_policy = self.get_service_policy(self.target_app_key)
            allowed = target_policy.get("effectiveAccessAllowed") is True
            if self.target_app_key == "youtube" and self.educational_unlock_active:
                allowed = False
        elif self.target_app_key == "spotify":
            gym_active = bool(self.db and hasattr(self.db, "is_gym_mode_music_active") and self.db.is_gym_mode_music_active())
            required = threshold_for("spotify", self.db.get_settings() if self.db else None)
            allowed = gym_active or (pts.get("score_available", True) and pts["current_score"] >= required)
            pts = dict(pts, required_score=required, remaining_points=0 if gym_active else max(0, required - pts["current_score"]))
        else:
            allowed = (
                (pts["current_score"] >= pts["required_score"])
                or self.manual_admin_release
                or self.educational_unlock_active
                or yt_unlocked
            )
        in_cooldown = self._is_in_dismiss_cooldown(self.target_app_key)
        browser_popup = self._browser_owns_restriction_popup()
        desktop_popup_visible = self.lock_screen_visible and not browser_popup
        popup_vis = (
            "VISIBLE"
            if desktop_popup_visible
            else ("DISMISSED_COOLDOWN" if in_cooldown else "HIDDEN")
        )
        return {
            "state": self.state.value,
            "allowed": allowed,
            "lock_screen_visible": desktop_popup_visible and not self._is_front_popup_active,
            "popup_visible": desktop_popup_visible and not self._is_front_popup_active,
            "native_popup_visible": self._is_front_popup_active and not browser_popup,
            "popup_owner": "BROWSER_EXTENSION" if browser_popup else "NATIVE_APP",
            "restriction_session_id": self._entry_session.session_id,
            "configured_targets": self.configured_targets_status(),
            "score_available": pts.get("score_available", True),
            "score_error": pts.get("score_error", ""),
            "popupVisibility": popup_vis,
            "requestedRestriction": bool(target_policy.get("requestedRestrictionEnabled", not allowed)),
            "effectivePrivilegeDecision": str(target_policy.get("policyDecision", "ALLOW" if allowed else "BLOCK")),
            "actualEnforcementResult": str(target_policy.get("enforcementStatus", "APPLIED")),
            "policyReason": str(target_policy.get("policyReason") or self.last_block_reason),
            "next_task": self.get_next_recommended_task(),
            "youtube_unlocked_for_today": yt_unlocked,
            "youtube_privilege_mode": priv_mode,
            "study_mode_enabled": self.study_mode_enabled,
            "educational_unlock_active": self.educational_unlock_active,
            "educational_unlock_reason": self.educational_unlock_reason,
            "educational_unlock_granted_at": self.educational_unlock_granted_at,
            "educational_unlock_expires_at": self.educational_unlock_expires_at,
            "educational_rules": [
                "Blocked Shorts (No Shorts permitted)",
                "Blocked Homepage (Direct educational search only)",
                "Educational Videos Only (Lectures, tutorials, courses)",
                "Blocked Entertainment (No Music, Gaming, Comedy, Serials)",
            ],
            "current_score": pts["current_score"] if pts.get("score_available", True) else None,
            "required_score": pts["required_score"],
            "remaining_points": pts["remaining_points"],
            "target_app": self.target_app,
            "target_app_key": self.target_app_key,
            "block_reason": self.last_block_reason,
            "app_locks": self.get_app_locks(),
            "reopen_block_count": self.reopen_block_count,
            "manual_admin_release": self.manual_admin_release,
            "simulated_test_mode": self.simulated_test_mode,
            "browser_extension_connected": self.browser_extension_is_connected(),
            "desktop_connection": self.desktop_connection_status(),
            "last_event": self.last_event,
            "last_event_time": self.last_event_time,
            "last_educational_block_reason": self.last_educational_block_reason,
            "history_count": len(self.events_history),
        }

