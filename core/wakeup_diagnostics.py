"""Wake-up verification diagnostics ("TEST MODE").

A diagnostic session proves that the wake-up *mechanism* works.  It is never
attendance.  Isolation is enforced here, in the backend, by construction:

* Diagnostic data lives only in ``wakeup_diagnostic_*`` tables with its own
  ``diag_`` identifier namespace.  No production query reads those tables.
* Every diagnostic database operation runs on a connection guarded by a SQLite
  authorizer that DENIES INSERT/UPDATE/DELETE/DDL on any other table.  A bug in
  this module therefore cannot mark attendance, change points, activate hourly
  checkpoints or alter schedules.
* Clients report raw measurements.  The verdict (PASS/FAIL/...) is computed
  here using the same thresholds as the production gate; a client "passed"
  flag is never trusted.
* Production ingestion paths reject anything carrying a diagnostic marker
  (``is_diagnostic_payload``), so a diagnostic result cannot be replayed or
  relabelled as a real wake-up.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

TEST_MODE_NOTICE = ("Test mode checks whether wake-up verification is working. Test results do not "
                    "count toward attendance, points, streaks, or privileges.")
SESSION_ID_PREFIX = "diag_"
EVENT_NAMESPACE = "wakeup_diag"
SESSION_LIFETIME = timedelta(minutes=90)
PASS, FAIL, LIMITED, NOT_TESTED, UNAVAILABLE = "PASS", "FAIL", "LIMITED", "NOT_TESTED", "UNAVAILABLE"
RESULTS = {PASS, FAIL, LIMITED, NOT_TESTED, UNAVAILABLE}

# Thresholds mirror the production gate (core/daily_routine_gate.py).
LIVENESS_MIN_SECONDS = 20.0
LIVENESS_MIN_VALID_RATIO = 0.70
MIN_FRAMES = 15
LIGHTING_MIN_RATIO = 0.65
FACE_MIN_RATIO = 0.70
FACE_MAX_ABSENCE_MS = 2500
EYES_MIN_RATIO = 0.60
PAUSE_DRIFT_MAX_MS = 300
RESUME_ADVANCE_MIN_MS = 300
START_ADVANCE_MIN_MS = 500
BLUETOOTH_ROUTES = {"BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET", "BLE_SPEAKER",
                    "TYPE_BLUETOOTH_A2DP", "TYPE_BLE_HEADSET"}

# key -> (label, component, required_for_overall)
CHECKS: Dict[str, Tuple[str, str, bool]] = {
    "camera_permission": ("Camera permission", "camera", True),
    "camera_init": ("Camera initialization", "camera", True),
    "live_frame_stream": ("Live frame stream", "camera", True),
    "lighting": ("Lighting assessment", "camera", True),
    "face_detection": ("Face detection", "camera", True),
    "eye_analysis": ("Eye analysis", "camera", True),
    "liveness_interval": ("Live-presence interval", "camera", True),
    "camera_recovery": ("Camera failure recovery", "camera", False),
    "bluetooth_permission": ("Bluetooth permission (Optional)", "bluetooth", False),
    "bluetooth_connected": ("Bluetooth connection (Optional)", "bluetooth", False),
    "active_audio_route": ("Active audio route (Optional)", "bluetooth", False),
    "bluetooth_disconnect_handling": ("Bluetooth disconnect handling", "bluetooth", False),
    "bluetooth_reconnect_handling": ("Bluetooth reconnect handling", "bluetooth", False),
    "player_init": ("Player initialization (Optional)", "track", False),
    "playback_start": ("Playback start (Optional)", "track", False),
    "pause_resume": ("Pause and resume (Optional)", "track", False),
    "seek_detection": ("Seek detection (Optional)", "track", False),
    "interruption_detection": ("Unexpected interruption", "track", False),
    "track_audio_route": ("Audio route during playback (Optional)", "track", False),
    "full_track_completion": ("Full-track playback (Optional)", "track", False),
}
ISOLATION_ITEMS: Dict[str, str] = {
    "attendance_isolation": "Attendance isolation",
    "point_ledger_unchanged": "Production point ledger unchanged",
    "hourly_scheduler_isolation": "Hourly scheduler isolation",
    "privileges_unchanged": "Streak and privileges unchanged",
    "backend_write_guard": "Backend write guard",
    "production_gate_rule": "Production completion rule",
}

# Whitelisted measurement fields per check.  Anything else is discarded, so a
# client cannot smuggle tokens, passwords or camera data into diagnostic logs.
_NUM, _BOOL, _STR = "num", "bool", "str"
MEASUREMENT_FIELDS: Dict[str, Dict[str, str]] = {
    "camera_permission": {"granted": _BOOL},
    "camera_init": {"opened": _BOOL, "camera_available": _BOOL, "init_ms": _NUM},
    "live_frame_stream": {"frames": _NUM, "duration_sec": _NUM, "fps": _NUM},
    "lighting": {"pass_ratio": _NUM, "avg_brightness": _NUM},
    "face_detection": {"face_ratio": _NUM, "max_absence_ms": _NUM},
    "eye_analysis": {"supported": _BOOL, "eyes_ratio": _NUM},
    "liveness_interval": {"duration_sec": _NUM, "valid_ratio": _NUM},
    "camera_recovery": {"interrupted": _BOOL, "recovered": _BOOL, "recovery_ms": _NUM},
    "bluetooth_permission": {"required": _BOOL, "granted": _BOOL},
    "bluetooth_connected": {"connected": _BOOL, "device_matches": _BOOL, "device_name": _STR},
    "active_audio_route": {"route_type": _STR, "active": _BOOL, "route_source": _STR},
    "bluetooth_disconnect_handling": {"attempted": _BOOL, "disconnect_observed": _BOOL,
                                      "playback_paused": _BOOL, "latency_ms": _NUM},
    "bluetooth_reconnect_handling": {"attempted": _BOOL, "reconnect_observed": _BOOL,
                                     "route_restored": _BOOL, "latency_ms": _NUM},
    "player_init": {"prepared": _BOOL, "duration_sec": _NUM},
    "playback_start": {"started": _BOOL, "advanced_ms": _NUM},
    "pause_resume": {"paused_drift_ms": _NUM, "resumed_advance_ms": _NUM},
    "seek_detection": {"induced": _BOOL, "detected": _BOOL},
    "interruption_detection": {"observed": _BOOL, "handled": _BOOL},
    "track_audio_route": {"bluetooth_samples": _NUM, "total_samples": _NUM},
    "full_track_completion": {"completed": _BOOL, "track_duration_sec": _NUM, "listened_sec": _NUM,
                              "seek_attempts": _NUM, "route_type": _STR},
}
CORRECTIVE_ACTIONS: Dict[str, str] = {
    "camera_permission": "Grant Camera access in Settings, then rerun the camera test.",
    "camera_init": "Close other apps using the camera and rerun; if it persists, restart the device.",
    "live_frame_stream": "Keep the camera preview open for the full interval; restart the camera if frames stall.",
    "lighting": "Face a light source and avoid backlighting or glare, then rerun.",
    "face_detection": "Keep your whole face centred and visible without leaving the frame.",
    "eye_analysis": "Remove obstructions (glasses glare, hair) and look at the camera. If eye landmarks are "
                    "unsupported on this device the production wake-up cannot pass.",
    "liveness_interval": "Stay in view for the full 20-second interval with a valid face for at least 70% of frames.",
    "camera_recovery": "Rerun and briefly cover/uncover or reopen the camera to confirm recovery.",
    "bluetooth_permission": "Grant Nearby devices / Bluetooth permission in Settings, then rerun.",
    "bluetooth_connected": "Connect the configured earbuds in system Bluetooth settings and rerun.",
    "active_audio_route": "Select the earbuds as the active audio output and rerun.",
    "bluetooth_disconnect_handling": "Rerun and switch the earbuds off during the prompt.",
    "bluetooth_reconnect_handling": "Rerun and switch the earbuds back on during the prompt.",
    "player_init": "Reinstall or update the app if the bundled track cannot be prepared.",
    "playback_start": "Check media volume/audio focus, close other players, then rerun.",
    "pause_resume": "Rerun; if position drifts while paused the media player is unreliable on this device.",
    "seek_detection": "Rerun; the seek guard must flag a forced forward seek.",
    "interruption_detection": "Rerun and interrupt playback (e.g. incoming notification sound) to test handling.",
    "track_audio_route": "Keep the earbuds as the active output for the whole track.",
    "full_track_completion": "Play the whole track without seeking, on the earbuds, to the end.",
}
_ISOLATION_ACTIONS = {
    "attendance_isolation": "Investigate other activity (real wake-up, sync, admin edits) during the test window and rerun.",
    "point_ledger_unchanged": "Review the score ledger for entries created during the test window and rerun.",
    "hourly_scheduler_isolation": "Review hourly checkpoints created during the test window and rerun.",
    "privileges_unchanged": "Review privilege/restriction events created during the test window and rerun.",
    "backend_write_guard": "Do not use diagnostics; report this defect immediately.",
    "production_gate_rule": "Do not rely on wake-up gating; report this defect immediately.",
}

_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{8,160}")
_SECRET_RE = re.compile(r"(?i)(password|passwd|pin|token|secret|authorization|bearer)\s*[=:]\s*\S+|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{60,}={0,2}")
_DIAG_KEYS = ("diag_session_id", "diagnostic_session_id", "diagnostic", "test_mode", "is_test", "is_diagnostic")


class DiagnosticsError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class DiagnosticPrincipal:
    account_id: str
    device_id: str
    platform: str  # derived by the server from the authenticated device, never from the request body


def is_diagnostic_payload(value: Any, _depth: int = 0) -> bool:
    """True if a production-bound payload carries any diagnostic marker."""
    if _depth > 4:
        return False
    if isinstance(value, dict):
        for key in _DIAG_KEYS:
            marker = value.get(key)
            if marker not in (None, False, "", 0, "0", "false", "False"):
                return True
        for key in ("event_id", "session_id", "sync_id", "source_sync_event_id"):
            if str(value.get(key) or "").startswith(SESSION_ID_PREFIX):
                return True
        if str(value.get("event_type") or "").upper().startswith("WAKEUP_DIAGNOSTIC"):
            return True
        return any(is_diagnostic_payload(v, _depth + 1) for v in value.values() if isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(is_diagnostic_payload(v, _depth + 1) for v in value[:50])
    return False


def sanitize_text(value: Any, limit: int = 300) -> str:
    text = str(value or "")[:limit * 2]
    return _SECRET_RE.sub("[redacted]", text)[:limit]


def sanitize_measurements(check: str, raw: Any) -> Dict[str, Any]:
    allowed = MEASUREMENT_FIELDS.get(check, {})
    clean: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return clean
    for key, kind in allowed.items():
        if key not in raw:
            continue
        value = raw[key]
        if kind == _BOOL and isinstance(value, bool):
            clean[key] = value
        elif kind == _NUM and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            clean[key] = round(float(value), 3)
        elif kind == _STR and isinstance(value, str):
            clean[key] = sanitize_text(value, 80)
    return clean


def _num(m: Dict[str, Any], key: str, default: float = 0.0) -> float:
    value = m.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def evaluate_check(check: str, m: Dict[str, Any], configured_track_sec: float = 222.0) -> Tuple[str, str]:
    """Deterministic verdict from measurements.  Returns (result, reason)."""
    if not m:
        return NOT_TESTED, "No measurements were reported for this check."
    if check == "camera_permission":
        return (PASS, "Camera permission is granted.") if m.get("granted") is True else (FAIL, "Camera permission is not granted.")
    if check == "camera_init":
        if m.get("camera_available") is False:
            return UNAVAILABLE, "No camera hardware is available on this device."
        return (PASS, "Camera opened.") if m.get("opened") is True else (FAIL, "The camera could not be opened.")
    if check == "live_frame_stream":
        frames = _num(m, "frames")
        if frames >= MIN_FRAMES:
            return PASS, f"{int(frames)} frames received (minimum {MIN_FRAMES})."
        return FAIL, f"Only {int(frames)} frames received (minimum {MIN_FRAMES})."
    if check == "lighting":
        ratio = _num(m, "pass_ratio")
        return ((PASS, f"Lighting acceptable in {ratio:.0%} of frames.") if ratio >= LIGHTING_MIN_RATIO
                else (FAIL, f"Lighting acceptable in only {ratio:.0%} of frames (minimum {LIGHTING_MIN_RATIO:.0%})."))
    if check == "face_detection":
        ratio, gap = _num(m, "face_ratio"), _num(m, "max_absence_ms")
        if ratio < FACE_MIN_RATIO:
            return FAIL, f"Face visible in only {ratio:.0%} of frames (minimum {FACE_MIN_RATIO:.0%})."
        if gap > FACE_MAX_ABSENCE_MS:
            return FAIL, f"Face disappeared for {gap / 1000:.1f}s (maximum {FACE_MAX_ABSENCE_MS / 1000:.1f}s)."
        return PASS, f"Face visible in {ratio:.0%} of frames."
    if check == "eye_analysis":
        if m.get("supported") is False:
            return LIMITED, "Eye landmarks are not supported on this device; production wake-up requires eye detection."
        ratio = _num(m, "eyes_ratio")
        return ((PASS, f"Eyes visible in {ratio:.0%} of frames.") if ratio >= EYES_MIN_RATIO
                else (FAIL, f"Eyes visible in only {ratio:.0%} of frames (minimum {EYES_MIN_RATIO:.0%})."))
    if check == "liveness_interval":
        duration, ratio = _num(m, "duration_sec"), _num(m, "valid_ratio")
        if duration < LIVENESS_MIN_SECONDS:
            return FAIL, f"Live presence observed for {duration:.1f}s (required {LIVENESS_MIN_SECONDS:.0f}s)."
        if ratio < LIVENESS_MIN_VALID_RATIO:
            return FAIL, f"Valid observations were {ratio:.0%} (required {LIVENESS_MIN_VALID_RATIO:.0%})."
        return PASS, f"{duration:.1f}s of live presence with {ratio:.0%} valid observations."
    if check == "camera_recovery":
        if m.get("interrupted") is not True:
            return NOT_TESTED, "The camera was not interrupted, so recovery was not exercised."
        return ((PASS, "Camera recovered after interruption.") if m.get("recovered") is True
                else (FAIL, "Camera did not recover after interruption."))
    if check == "bluetooth_permission":
        if m.get("required") is False:
            return PASS, "Bluetooth permission is not required on this Android version."
        return (PASS, "Bluetooth permission is granted.") if m.get("granted") is True else (FAIL, "Bluetooth permission is not granted.")
    if check == "bluetooth_connected":
        if m.get("connected") is not True:
            return FAIL, "No Bluetooth audio device is connected."
        if m.get("device_matches") is False:
            return FAIL, "The connected Bluetooth device is not the configured earbuds."
        return PASS, "Configured Bluetooth earbuds are connected."
    if check == "active_audio_route":
        route = str(m.get("route_type") or "").upper()
        if m.get("active") is not True or route not in BLUETOOTH_ROUTES:
            return FAIL, f"The active audio output is not Bluetooth earbuds ({route or 'none'})."
        if str(m.get("route_source") or "").upper() == "LABEL_HEURISTIC":
            return LIMITED, "Output device name matches Bluetooth earbuds, but this platform cannot confirm the active route."
        return PASS, f"Audio is routed to Bluetooth ({route})."
    if check == "bluetooth_disconnect_handling":
        if m.get("attempted") is not True:
            return NOT_TESTED, "Disconnect handling was not attempted."
        if m.get("disconnect_observed") is not True:
            return NOT_TESTED, "No disconnect was observed before the test timed out."
        return ((PASS, "Disconnect detected and playback paused.") if m.get("playback_paused") is True
                else (FAIL, "Disconnect detected but playback was not paused."))
    if check == "bluetooth_reconnect_handling":
        if m.get("attempted") is not True:
            return NOT_TESTED, "Reconnect handling was not attempted."
        if m.get("reconnect_observed") is not True:
            return NOT_TESTED, "No reconnect was observed before the test timed out."
        return ((PASS, "Reconnect detected and the Bluetooth route restored.") if m.get("route_restored") is True
                else (FAIL, "Reconnect detected but the Bluetooth route was not restored."))
    if check == "player_init":
        if m.get("prepared") is True and _num(m, "duration_sec") > 0:
            return PASS, f"Player prepared a {_num(m, 'duration_sec'):.0f}s track."
        return FAIL, "The player could not prepare the track."
    if check == "playback_start":
        if m.get("started") is True and _num(m, "advanced_ms") >= START_ADVANCE_MIN_MS:
            return PASS, f"Playback advanced {_num(m, 'advanced_ms'):.0f}ms after start."
        return FAIL, "Playback did not start or the position did not advance."
    if check == "pause_resume":
        if "paused_drift_ms" not in m or "resumed_advance_ms" not in m:
            return NOT_TESTED, "Pause and resume were not measured."
        if abs(_num(m, "paused_drift_ms")) > PAUSE_DRIFT_MAX_MS:
            return FAIL, f"Position drifted {_num(m, 'paused_drift_ms'):.0f}ms while paused."
        if _num(m, "resumed_advance_ms") < RESUME_ADVANCE_MIN_MS:
            return FAIL, "Playback did not advance after resume."
        return PASS, "Playback held position while paused and advanced after resume."
    if check == "seek_detection":
        if m.get("induced") is not True:
            return NOT_TESTED, "No seek was induced."
        return ((PASS, "The forced forward seek was detected.") if m.get("detected") is True
                else (FAIL, "A forced forward seek was NOT detected; seek-skipping would go unnoticed."))
    if check == "interruption_detection":
        if m.get("observed") is not True:
            return NOT_TESTED, "No unexpected interruption occurred during the test."
        return ((PASS, "The interruption was detected and handled.") if m.get("handled") is True
                else (FAIL, "An interruption occurred but was not handled."))
    if check == "track_audio_route":
        total, bt = _num(m, "total_samples"), _num(m, "bluetooth_samples")
        if total <= 0:
            return NOT_TESTED, "The audio route was not sampled during playback."
        return ((PASS, f"Audio stayed on Bluetooth for all {int(total)} samples.") if bt >= total
                else (FAIL, f"Audio left the Bluetooth route in {int(total - bt)} of {int(total)} samples."))
    if check == "full_track_completion":
        duration, listened = _num(m, "track_duration_sec"), _num(m, "listened_sec")
        if m.get("completed") is not True:
            return FAIL, "The track did not play to completion."
        if duration <= 0 or duration + 1.0 < configured_track_sec:
            return FAIL, f"The played track ({duration:.0f}s) is shorter than the configured full track ({configured_track_sec:.0f}s)."
        if _num(m, "seek_attempts", 1) != 0:
            return FAIL, "Seeking was detected during playback."
        if listened + 0.001 < duration:
            return FAIL, f"Only {listened:.1f}s of {duration:.1f}s was listened."
        if str(m.get("route_type") or "").upper() not in BLUETOOTH_ROUTES:
            return FAIL, "The track did not finish on a Bluetooth route."
        return PASS, f"Full {duration:.0f}s track completed on Bluetooth with no seeking."
    return NOT_TESTED, "Unknown check."


def _hash_rows(rows) -> Tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for row in sorted(tuple("" if v is None else str(v) for v in r) for r in rows):
        digest.update(("\x1f".join(row) + "\x1e").encode("utf-8", "replace"))
        count += 1
    return digest.hexdigest(), count


# group -> list of read-only queries over *production* tables.  Status columns
# that move with the clock (e.g. SCHEDULED->OPEN) are deliberately excluded so
# ordinary time passing is not mistaken for a diagnostic side effect.
FINGERPRINTS: Dict[str, List[str]] = {
    "wake_up_sessions": ["SELECT session_id,date,is_dev_mode,camera_verified,bluetooth_verified,motivation_verified,"
                         "attendance_status,completed_at,device_id,checklist_state FROM wake_up_sessions"],
    "daily_attendance": ["SELECT date,user_id,is_dev_mode,wakeup_status,wakeup_completed_at,snooze_count,"
                         "hourly_completed_count,hourly_missed_count FROM daily_records"],
    "submissions": ["SELECT evidence_id,date,record_type FROM evidence_files",
                    "SELECT review_id,evidence_id,status,decision,reviewed_by FROM proof_reviews"],
    "routine": ["SELECT schedule_id,schedule_version,status FROM routine_schedules"],
    "reports": ["SELECT date,account_id,snapshot_hash FROM daily_summary_snapshots"],
    "score_ledger": ["SELECT event_id,date,is_dev_mode,event_type,points FROM score_events",
                     "SELECT date,user_id,is_dev_mode,final_score,positive_points,negative_points FROM daily_records"],
    "streak": ["SELECT * FROM achievements_unlocked"],
    "hourly_scheduler": ["SELECT activity_id,date,schedule_version FROM scheduled_activities WHERE type='HOURLY'",
                         "SELECT hourly_id,date,is_dev_mode FROM hourly_records",
                         "SELECT date,account_id,is_dev_mode,state,activated_at FROM daily_routine_gates"],
    "privileges": ["SELECT restriction_id,date,application,action FROM restriction_events",
                   "SELECT date,user_id,is_dev_mode,reward_unlocked,score_state FROM daily_records"],
}
ISOLATION_GROUPS: Dict[str, List[str]] = {
    "attendance_isolation": ["wake_up_sessions", "daily_attendance", "submissions", "routine", "reports"],
    "point_ledger_unchanged": ["score_ledger", "streak"],
    "hourly_scheduler_isolation": ["hourly_scheduler"],
    "privileges_unchanged": ["privileges"],
}

# Canonical gate truth table: (photo, earbuds_optional, track_optional) -> complete?
GATE_CASES = [
    ("no requirements passed", (False, False, False), False),
    ("photo only (earbuds & audio skipped)", (True, False, False), True),
    ("photo and earbuds (audio skipped)", (True, True, False), True),
    ("photo and music (earbuds skipped)", (True, False, True), True),
    ("earbuds and music, photo failed", (False, True, True), False),
    ("all three passed", (True, True, True), True),
]


class WakeUpDiagnosticsService:
    _lock = threading.RLock()

    def __init__(self, db, clock=None):
        self.db = db
        if clock is None:
            from core.time_window_engine import now_ist
            clock = now_ist
        self.clock = clock
        self._initialize()

    # ------------------------------------------------------------------ storage
    def _initialize(self) -> None:
        with self.db._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS wakeup_diagnostic_sessions (
              diag_session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, device_id TEXT NOT NULL,
              platform TEXT NOT NULL, status TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT 'DIAGNOSTIC',
              counts_toward_attendance INTEGER NOT NULL DEFAULT 0 CHECK (counts_toward_attendance = 0),
              request_id TEXT NOT NULL, started_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              completed_at TEXT, baseline_json TEXT NOT NULL DEFAULT '{}', report_json TEXT,
              UNIQUE(account_id, device_id, request_id));
            CREATE TABLE IF NOT EXISTS wakeup_diagnostic_events (
              event_key TEXT PRIMARY KEY, diag_session_id TEXT NOT NULL, client_event_id TEXT NOT NULL,
              check_key TEXT NOT NULL, result TEXT NOT NULL, reason TEXT NOT NULL,
              measurements_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
              recorded_at TEXT NOT NULL,
              FOREIGN KEY(diag_session_id) REFERENCES wakeup_diagnostic_sessions(diag_session_id));
            CREATE INDEX IF NOT EXISTS idx_wakeup_diag_events_session
              ON wakeup_diagnostic_events(diag_session_id, check_key);
            """)

    @staticmethod
    def _authorizer(action, arg1, arg2, dbname, source):
        read_only = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION,
                     sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_RECURSIVE}
        if action in read_only:
            return sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_OK if str(arg1 or "").startswith("wakeup_diagnostic_") else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY  # DDL, ATTACH, PRAGMA and everything else

    @contextmanager
    def _guarded(self):
        """A connection that physically cannot write any production table."""
        with self.db._connect() as conn:
            conn.set_authorizer(self._authorizer)
            yield conn

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    def _configured_track_seconds(self, account_id: str) -> float:
        try:
            settings = self.db.get_settings(account_id)
            return float(getattr(settings, "motivation_track_duration_sec", 222) or 222)
        except Exception:
            return 222.0

    # -------------------------------------------------------------- fingerprints
    def _fingerprint(self, conn) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for group, queries in FINGERPRINTS.items():
            try:
                rows: List[tuple] = []
                for query in queries:
                    rows.extend((query[:40],) + tuple(r) for r in conn.execute(query).fetchall())
                digest, count = _hash_rows(rows)
                result[group] = {"hash": digest, "rows": count}
            except sqlite3.DatabaseError as exc:
                result[group] = {"unavailable": sanitize_text(exc, 120)}
        return result

    def _isolation_items(self, conn, baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
        current = self._fingerprint(conn)
        items: List[Dict[str, Any]] = []
        for key, groups in ISOLATION_GROUPS.items():
            changed, missing = [], []
            for group in groups:
                before, after = baseline.get(group, {}), current.get(group, {})
                if "unavailable" in before and "unavailable" in after and before["unavailable"] == after["unavailable"]:
                    pass  # table absent both before and after: no change occurred
                elif "unavailable" in before or "unavailable" in after or not before:
                    missing.append(group)
                elif before.get("hash") != after.get("hash"):
                    changed.append(group)
            if changed:
                items.append(self._item(key, ISOLATION_ITEMS[key], FAIL,
                    "Production records changed during the test window: " + ", ".join(changed) +
                    ". Diagnostic writes are blocked from these tables, so this reflects other activity.",
                    action=_ISOLATION_ACTIONS[key]))
            elif missing:
                items.append(self._item(key, ISOLATION_ITEMS[key], LIMITED,
                    "Could not compare: " + ", ".join(missing) + " (no baseline or table unavailable)."))
            else:
                items.append(self._item(key, ISOLATION_ITEMS[key], PASS,
                    "Compared production records before and after: " + ", ".join(
                        f"{g}={current[g].get('rows', 0)} rows" for g in groups) + "; no change."))
        # Runtime proof that the guard denies production writes.  The probes match
        # zero rows, so they are harmless even if the guard were broken.
        denied = []
        for statement in ("UPDATE score_events SET points=points WHERE 0",
                          "DELETE FROM wake_up_sessions WHERE 0",
                          "UPDATE daily_records SET final_score=final_score WHERE 0",
                          "INSERT INTO scheduled_activities(activity_id) SELECT NULL WHERE 0"):
            try:
                conn.execute(statement)
                denied.append(False)
            except sqlite3.DatabaseError:
                denied.append(True)
        guard_ok = all(denied)
        items.append(self._item("backend_write_guard", ISOLATION_ITEMS["backend_write_guard"],
            PASS if guard_ok else FAIL,
            f"Writes to production attendance, score and schedule tables were denied ({sum(denied)}/{len(denied)} probes)."
            if guard_ok else "A production write probe was NOT denied by the diagnostic connection.",
            action=None if guard_ok else _ISOLATION_ACTIONS["backend_write_guard"]))
        from core.daily_routine_gate import DailyRoutineGate
        failures = [name for name, flags, expected in GATE_CASES
                    if DailyRoutineGate.hardware_requirements_met(*flags) != expected]
        items.append(self._item("production_gate_rule", ISOLATION_ITEMS["production_gate_rule"],
            PASS if not failures else FAIL,
            "mandatory camera liveness (with optional earbuds & audio) enforced for " + ", ".join(n for n, _, _ in GATE_CASES) + "."
            if not failures else "Gate truth table failed for: " + ", ".join(failures),
            action=None if not failures else _ISOLATION_ACTIONS["production_gate_rule"]))
        return items

    # ---------------------------------------------------------------- sessions
    @staticmethod
    def _item(key, label, result, detail, error="", event_id=None, action=None) -> Dict[str, Any]:
        return {"key": key, "label": label, "result": result, "detail": detail, "error": error,
                "event_id": event_id, "recommended_action": action if result in (FAIL, LIMITED) else None}

    def _row(self, conn, principal: DiagnosticPrincipal, diag_session_id: str, same_device=False):
        row = conn.execute("SELECT * FROM wakeup_diagnostic_sessions WHERE diag_session_id=? AND account_id=?",
                           (str(diag_session_id or ""), principal.account_id)).fetchone()
        if not row:
            raise DiagnosticsError("SESSION_NOT_FOUND", "This diagnostic session is unavailable to this account.", 404)
        if same_device and row["device_id"] != principal.device_id:
            raise DiagnosticsError("WRONG_DEVICE", "Only the device that started a diagnostic session can add results to it.", 403)
        return row

    def start(self, principal: DiagnosticPrincipal, request_id: str) -> Dict[str, Any]:
        if not _ID_RE.fullmatch(str(request_id or "")):
            raise DiagnosticsError("REQUEST_ID_REQUIRED", "Provide a stable request_id (8-160 safe characters).")
        diag_id = SESSION_ID_PREFIX + hashlib.sha256(
            f"{principal.account_id}:{principal.device_id}:{request_id}".encode()).hexdigest()[:28]
        now = self._now()
        with self._lock, self._guarded() as conn:
            row = conn.execute("SELECT * FROM wakeup_diagnostic_sessions WHERE diag_session_id=?", (diag_id,)).fetchone()
            if not row:
                # Only one live test per device; an earlier unfinished one is closed, never reused.
                conn.execute("""UPDATE wakeup_diagnostic_sessions SET status='SUPERSEDED', completed_at=?
                    WHERE account_id=? AND device_id=? AND status='RUNNING'""",
                    (now.isoformat(), principal.account_id, principal.device_id))
                conn.execute("""INSERT INTO wakeup_diagnostic_sessions
                    (diag_session_id,account_id,device_id,platform,status,request_id,started_at,expires_at,baseline_json)
                    VALUES (?,?,?,?, 'RUNNING', ?,?,?,?)""",
                    (diag_id, principal.account_id, principal.device_id, principal.platform, request_id,
                     now.isoformat(), (now + SESSION_LIFETIME).isoformat(), json.dumps(self._fingerprint(conn))))
                row = conn.execute("SELECT * FROM wakeup_diagnostic_sessions WHERE diag_session_id=?", (diag_id,)).fetchone()
        return self._envelope(row, {"success": True, "notice": TEST_MODE_NOTICE,
            "checks": [{"key": k, "label": v[0], "component": v[1]} for k, v in CHECKS.items()]})

    @staticmethod
    def _envelope(row, extra=None) -> Dict[str, Any]:
        result = {"success": True, "mode": "TEST_MODE", "counts_toward_attendance": False,
                  "namespace": "DIAGNOSTIC", "diag_session_id": row["diag_session_id"],
                  "status": row["status"], "platform": row["platform"], "device_id": row["device_id"],
                  "started_at": row["started_at"], "expires_at": row["expires_at"],
                  "completed_at": row["completed_at"]}
        result.update(extra or {})
        return result

    def record_event(self, principal: DiagnosticPrincipal, diag_session_id: str, event_id: str, check: str,
                     measurements: Any = None, unavailable_reason: str = "", error: str = "") -> Dict[str, Any]:
        if check not in CHECKS:
            raise DiagnosticsError("UNKNOWN_CHECK", "This is not a recognized wake-up diagnostic check.")
        if not _ID_RE.fullmatch(str(event_id or "")):
            raise DiagnosticsError("EVENT_ID_REQUIRED", "Provide a stable event_id (8-160 safe characters).")
        now = self._now()
        with self._lock, self._guarded() as conn:
            session = self._row(conn, principal, diag_session_id, same_device=True)
            key = EVENT_NAMESPACE + ":" + hashlib.sha256(f"{diag_session_id}:{event_id}".encode()).hexdigest()
            existing = conn.execute("SELECT * FROM wakeup_diagnostic_events WHERE event_key=?", (key,)).fetchone()
            if existing:
                return self._event_result(existing, duplicate=True)
            if session["status"] != "RUNNING":
                raise DiagnosticsError("SESSION_CLOSED", "This diagnostic session is closed; start a new test.", 409)
            exp_dt = datetime.fromisoformat(session["expires_at"])
            if not exp_dt.tzinfo:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if now > exp_dt:
                conn.execute("UPDATE wakeup_diagnostic_sessions SET status='EXPIRED', completed_at=? WHERE diag_session_id=?",
                             (now.isoformat(), diag_session_id))
                raise DiagnosticsError("SESSION_EXPIRED", "This diagnostic session expired; start a new test.", 409)
            clean = sanitize_measurements(check, measurements)
            reason_text = sanitize_text(unavailable_reason, 200)
            if reason_text and not clean:
                # The client could not run the check at all (e.g. no hardware): never a PASS.
                result, reason = UNAVAILABLE, reason_text
            else:
                result, reason = evaluate_check(check, clean, self._configured_track_seconds(principal.account_id))
            conn.execute("""INSERT INTO wakeup_diagnostic_events
                (event_key,diag_session_id,client_event_id,check_key,result,reason,measurements_json,error,recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", (key, diag_session_id, event_id, check, result, reason,
                json.dumps(clean), sanitize_text(error, 300), now.isoformat()))
            saved = conn.execute("SELECT * FROM wakeup_diagnostic_events WHERE event_key=?", (key,)).fetchone()
        return self._event_result(saved, duplicate=False)

    @staticmethod
    def _event_result(row, duplicate: bool) -> Dict[str, Any]:
        check = row["check_key"]
        return {"success": True, "mode": "TEST_MODE", "counts_toward_attendance": False,
                "diag_session_id": row["diag_session_id"], "event_id": row["client_event_id"],
                "check": check, "label": CHECKS[check][0], "result": row["result"], "reason": row["reason"],
                "recommended_action": CORRECTIVE_ACTIONS.get(check) if row["result"] in (FAIL, LIMITED) else None,
                "duplicate": duplicate}

    # ------------------------------------------------------------------ report
    def _check_items(self, conn, diag_session_id: str) -> List[Dict[str, Any]]:
        latest: Dict[str, Any] = {}
        for row in conn.execute("""SELECT * FROM wakeup_diagnostic_events WHERE diag_session_id=?
                                   ORDER BY rowid""", (diag_session_id,)):
            latest[row["check_key"]] = row  # the newest measurement of a check supersedes earlier retries
        items = []
        for key, (label, _, _) in CHECKS.items():
            row = latest.get(key)
            if not row:
                items.append(self._item(key, label, NOT_TESTED, "This check was not run in this session."))
            else:
                items.append(self._item(key, label, row["result"], row["reason"], error=row["error"],
                    event_id=row["client_event_id"], action=CORRECTIVE_ACTIONS.get(key)))
        return items

    @staticmethod
    def _gate_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = {i["key"]: i["result"] for i in items}
        photo = all(result.get(k) == PASS for k in (
            "camera_permission", "camera_init", "live_frame_stream", "lighting", "face_detection",
            "eye_analysis", "liveness_interval"))
        earbuds = all(result.get(k) == PASS for k in ("bluetooth_permission", "bluetooth_connected", "active_audio_route"))
        track = all(result.get(k) == PASS for k in ("player_init", "playback_start", "full_track_completion"))
        return {"photo_liveness_passed": photo, "earbuds_route_passed": earbuds, "full_track_passed": track,
                "earbuds_optional": True, "audio_optional": True,
                "all_passed": photo,
                "meaning": "TEST PASS means the verification mechanism works. It does not mean the user attended.",
                "production_effect": "NONE"}

    def _build_report(self, row, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        required = {k for k, v in CHECKS.items() if v[2]} | set(ISOLATION_ITEMS)
        required_items = [i for i in items if i["key"] in required]
        if any(i["result"] == FAIL for i in required_items):
            overall = "FAIL"
        elif any(i["result"] in (NOT_TESTED, UNAVAILABLE, LIMITED) for i in required_items):
            overall = "INCOMPLETE"
        else:
            overall = "PASS"
        failures = [{"component": i["label"], "error": i["error"] or i["detail"], "diagnostic_event": i["event_id"],
                     "recommended_action": i["recommended_action"]} for i in required_items if i["result"] == FAIL]
        components = {}
        for comp in ("camera", "bluetooth", "track"):
            if comp == "camera":
                states = [i["result"] for i in items if i["key"] in CHECKS and CHECKS[i["key"]][1] == comp
                          and CHECKS[i["key"]][2]]
                components[comp] = FAIL if FAIL in states else PASS if states and all(s == PASS for s in states) else "INCOMPLETE"
            else:
                states = [i["result"] for i in items if i["key"] in CHECKS and CHECKS[i["key"]][1] == comp]
                tested = [s for s in states if s != NOT_TESTED]
                if not tested:
                    components[comp] = "SKIPPED_OPTIONAL"
                elif FAIL in tested:
                    components[comp] = FAIL
                elif all(s == PASS for s in tested):
                    components[comp] = PASS
                else:
                    components[comp] = "OPTIONAL_PARTIAL"
        return self._envelope(row, {"title": "WAKE-UP DIAGNOSTICS", "notice": TEST_MODE_NOTICE, "overall": overall,
                                    "items": items, "failures": failures, "components": components,
                                    "diagnostic_gate": self._gate_summary(items)})

    def complete(self, principal: DiagnosticPrincipal, diag_session_id: str) -> Dict[str, Any]:
        now = self._now()
        with self._lock, self._guarded() as conn:
            row = self._row(conn, principal, diag_session_id, same_device=True)
            if row["status"] != "RUNNING":
                if row["report_json"]:
                    return json.loads(row["report_json"])  # idempotent: the stored report is stable
                raise DiagnosticsError("SESSION_CLOSED", "This diagnostic session did not produce a report.", 409)
            items = self._check_items(conn, diag_session_id)
            items += self._isolation_items(conn, json.loads(row["baseline_json"] or "{}"))
            conn.execute("UPDATE wakeup_diagnostic_sessions SET status='COMPLETED', completed_at=? WHERE diag_session_id=?",
                         (now.isoformat(), diag_session_id))
            row = conn.execute("SELECT * FROM wakeup_diagnostic_sessions WHERE diag_session_id=?", (diag_session_id,)).fetchone()
            report = self._build_report(row, items)
            conn.execute("UPDATE wakeup_diagnostic_sessions SET report_json=? WHERE diag_session_id=?",
                         (json.dumps(report), diag_session_id))
        self._publish(report)
        return report

    def report(self, principal: DiagnosticPrincipal, diag_session_id: str) -> Dict[str, Any]:
        with self._guarded() as conn:
            row = self._row(conn, principal, diag_session_id)
            if row["report_json"]:
                return json.loads(row["report_json"])
            partial = self._build_report(row, self._check_items(conn, diag_session_id))
            partial["partial"] = True
            return partial

    def sessions(self, principal: DiagnosticPrincipal, limit: int = 10) -> Dict[str, Any]:
        with self._guarded() as conn:
            rows = conn.execute("""SELECT diag_session_id,platform,device_id,status,started_at,completed_at,report_json
                FROM wakeup_diagnostic_sessions WHERE account_id=? ORDER BY started_at DESC LIMIT ?""",
                (principal.account_id, max(1, min(int(limit), 50)))).fetchall()
        out = []
        for r in rows:
            report = json.loads(r["report_json"]) if r["report_json"] else {}
            out.append({"diag_session_id": r["diag_session_id"], "platform": r["platform"], "device_id": r["device_id"],
                        "status": r["status"], "started_at": r["started_at"], "completed_at": r["completed_at"],
                        "overall": report.get("overall"), "mode": "TEST_MODE", "counts_toward_attendance": False})
        return {"success": True, "mode": "TEST_MODE", "counts_toward_attendance": False, "sessions": out}

    def _publish(self, report: Dict[str, Any]) -> None:
        """Publishes a diagnostics-only event.  Its type is never consumed by production sync."""
        try:
            from core.event_bus import EventBus
            EventBus.get_instance().publish("WAKEUP_DIAGNOSTIC_COMPLETED", {
                "account_id": self._account_of(report["diag_session_id"]),
                "diag_session_id": report["diag_session_id"], "overall": report["overall"],
                "mode": "TEST_MODE", "counts_toward_attendance": False,
            }, event_id=f"diag_done_{report['diag_session_id']}")
        except Exception:
            pass  # event delivery is best effort; the stored report is authoritative

    def _account_of(self, diag_session_id: str) -> str:
        with self._guarded() as conn:
            row = conn.execute("SELECT account_id FROM wakeup_diagnostic_sessions WHERE diag_session_id=?",
                               (diag_session_id,)).fetchone()
        return row["account_id"] if row else ""
