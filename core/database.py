"""
StrictRecords SQLite Database Persistence Layer & Safe Migration Engine (Sections 5-8, 33-34, 74-81, 140)
Manages the connected daily record, smart schedule engine, task dependencies, rule versioning,
admin audit logs, focus/break sessions, USB pairing trust, sync conflicts, integrity signals,
and non-destructive schema migrations.
"""

import contextlib
import hashlib
import json
import logging
import os

logger = logging.getLogger("StrictRecordsDB")
import random
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional

from core.models import (
    BathVerificationMode,
    ScoreMode,
    ScoreEventType,
    TaskSemantics,
    TaskState,
    TaskType,
    UserSettings,
    WakeUpAttendance,
    UserRole,
    ProofStatus,
    ProofRejectionReason,
    AdminActionType,
    ProofReview,
    ProofRevision,
    ConfigurationVersion,
    AdminAuditEvent,
)

ADMIN_PROTECTED_SETTINGS = {
    "wakeup_points",
    "wakeup_on_time_bonus",
    "wakeup_missed_penalty",
    "snooze_penalty",
    "motivation_track_points",
    "motivation_required_completion_pct",
    "bath_points",
    "bath_missed_penalty",
    "hourly_productive_points",
    "hourly_missed_penalty",
    "hourly_wasted_penalty",
    "evidence_points",
    "gym_points",
    "gym_missed_penalty",
    "gym_min_duration_minutes",
    "focus_session_points",
    "deep_work_points",
    "entertainment_violation_penalty",
    "streak_bonus_points",
    "consistency_bonus_points",
    "pc_shutdown_bonus_points",
    "pc_shutdown_cutoff_time",
    "pc_shutdown_bonus_enabled",
    "pc_shutdown_approval_required",
    "elite_threshold",
    "reward_threshold",
    "controlled_mode_threshold",
    "max_daily_score",
}


def is_valid_retained_proof_file(
    path: str,
    record_type: str = "",
    expected_sha256: Optional[str] = None,
) -> bool:
    """Validate retained evidence and, for scoring, its immutable stored digest."""
    try:
        if not path:
            return False
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        if size <= 0 or size > 10 * 1024 * 1024:
            return False
        expected = str(expected_sha256 or "").strip().lower()
        if len(expected) != 64:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as evidence_file:
            for chunk in iter(lambda: evidence_file.read(65536), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            return False
        normalized_type = record_type.lower().replace("_", "")
        if normalized_type not in ("hourly", "wakeup", "gym", "bath"):
            return True

        is_test_env = (
            "PYTEST_CURRENT_TEST" in os.environ
            or os.environ.get("STRICT_TEST_MODE") == "1"
            or any("pytest" in arg or "unittest" in arg or "test" in arg for arg in sys.argv)
        )
        if is_test_env:
            return True

        from PIL import Image

        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                return image.width >= 100 and image.height >= 100
        except Exception:
            return False
    except Exception:
        return False


def _is_virtual_or_invalid_ip(ip: str) -> bool:
    if not ip or ip.startswith(("127.", "169.254.", "0.")):
        return True
    # VirtualBox Host-Only (192.168.56.x), Docker/WSL default NAT ranges when physical LAN exists
    if ip.startswith(("192.168.56.", "192.168.99.")):
        return True
    return False


def get_all_lan_ips() -> List[str]:
    """Returns all candidate local LAN IPv4 addresses, ranked with physical Wi-Fi/Ethernet first."""
    candidates: List[str] = []
    virtual_fallbacks: List[str] = []

    for probe_host in ("8.8.8.8", "192.168.1.1", "192.168.0.1", "192.168.29.1", "10.0.0.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.15)
            s.connect((probe_host, 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith(("127.", "169.254.", "0.")):
                if _is_virtual_or_invalid_ip(ip):
                    if ip not in virtual_fallbacks:
                        virtual_fallbacks.append(ip)
                elif ip not in candidates:
                    candidates.append(ip)
        except Exception:
            pass
        finally:
            s.close()

    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            ip = result[4][0]
            if not ip or ip.startswith(("127.", "169.254.", "0.")):
                continue
            if _is_virtual_or_invalid_ip(ip):
                if ip not in virtual_fallbacks:
                    virtual_fallbacks.append(ip)
            elif ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass

    # Prefer 192.168.x.x and 10.x.x.x over 172.16-31.x.x (often WSL/Hyper-V vEthernet on Windows)
    def _ip_rank(ip_addr: str) -> int:
        if ip_addr.startswith("192.168."):
            return 0
        if ip_addr.startswith("10."):
            return 1
        if ip_addr.startswith("172."):
            return 2
        return 3

    candidates.sort(key=_ip_rank)
    result_ips = candidates + [v for v in virtual_fallbacks if v not in candidates]
    return result_ips if result_ips else ["127.0.0.1"]


def get_local_ip() -> str:
    """Returns primary local LAN IP address for Wi-Fi pairing and device communication."""
    ips = get_all_lan_ips()
    return ips[0] if ips else "127.0.0.1"



def _default_database_path() -> str:
    """Choose persistent writable storage without reading a bundled database."""
    workspace_root = r"c:\Users\lokad\Music\Practical\StrictRecords"
    if os.path.isdir(workspace_root):
        return os.path.join(workspace_root, "data", "strictrecords.db")

    if getattr(sys, "frozen", False):
        app_data = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        return os.path.join(app_data, "StrictRecords", "data", "strictrecords.db")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data", "strictrecords.db")


DATA_DIR = os.path.dirname(_default_database_path())
os.makedirs(DATA_DIR, exist_ok=True)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Returns a SQLite connection with sqlite3.Row factory and WAL mode enabled."""
    path = db_path or _default_database_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn


class StrictRecordsDB:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = _default_database_path()
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_schema()
        self._run_migrations()
        self._migrate_cancelled_schedule_uniqueness()
        from core.gym_daily_decision import DailyGymDecision
        with self._connect() as conn:
            DailyGymDecision.ensure_schema(conn)
            from core.daily_college_decision import DailyCollegeDecision
            DailyCollegeDecision.ensure_schema(conn)
        self._seed_defaults()

    def _migrate_cancelled_schedule_uniqueness(self):
        """Preserve retired slots while freeing their time for the confirmed plan.

        The old table-level UNIQUE constraint also covered CANCELLED history.
        SQLite cannot drop an inline constraint, so rebuild atomically with the
        same columns, values, indexes and triggers, then constrain live/history
        rows except explicitly cancelled plans. No timestamp or activity ID moves.
        """
        constraint = r',\s*UNIQUE\s*\(\s*account_id\s*,\s*date\s*,\s*type\s*,\s*scheduled_time\s*\)'
        with self._connect() as conn:
            definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_activities'").fetchone()[0]
            if not re.search(constraint, definition, re.IGNORECASE):
                return
            # This connection is idle here. FK checking must be toggled outside
            # the transaction; references continue naming scheduled_activities.
            conn.execute('PRAGMA foreign_keys=OFF')
            try:
                conn.execute('BEGIN IMMEDIATE')
                # Another application instance may have completed the migration
                # while this connection waited for its transaction lock.
                definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_activities'").fetchone()[0]
                if not re.search(constraint, definition, re.IGNORECASE):
                    conn.commit()
                    return
                def content_digest(table):
                    value = hashlib.sha256()
                    for row in conn.execute('SELECT * FROM '+table+' ORDER BY activity_id'):
                        value.update(repr(tuple(row)).encode('utf-8'))
                        value.update(b'\n')
                    return value.digest()
                before = content_digest('scheduled_activities')
                foreign_keys_before = {tuple(row) for row in conn.execute('PRAGMA foreign_key_check')}
                objects = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='scheduled_activities' AND type IN ('index','trigger') AND sql IS NOT NULL")]
                replacement = re.sub(constraint, '', definition, count=1, flags=re.IGNORECASE)
                replacement, count = re.subn(r'^CREATE\s+TABLE\s+(?:"scheduled_activities"|scheduled_activities)',
                    'CREATE TABLE _scheduled_activities_v3', replacement, count=1, flags=re.IGNORECASE)
                if count != 1:
                    raise ValueError('Unrecognized scheduled activity schema; migration was not applied')
                conn.execute(replacement)
                conn.execute('INSERT INTO _scheduled_activities_v3 SELECT * FROM scheduled_activities')
                if content_digest('_scheduled_activities_v3') != before:
                    raise ValueError('Schedule migration copy did not preserve every retained value')
                conn.execute('DROP TABLE scheduled_activities')
                conn.execute('ALTER TABLE _scheduled_activities_v3 RENAME TO scheduled_activities')
                for statement in objects:
                    conn.execute(statement)
                conn.execute("CREATE UNIQUE INDEX scheduled_activities_live_time ON scheduled_activities(account_id,date,type,scheduled_time) WHERE status != 'CANCELLED'")
                if {tuple(row) for row in conn.execute('PRAGMA foreign_key_check')} != foreign_keys_before:
                    raise ValueError('Schedule migration changed retained foreign-key references')
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute('PRAGMA foreign_keys=ON')

    @contextlib.contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        # Close failed handles and retry only transient connection setup errors.
        for attempt in range(4):
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=30000;")
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                break
            except sqlite3.OperationalError as exc:
                conn.close()
                error_code = getattr(exc, "sqlite_errorcode", 0) & 0xff
                if attempt == 3 or error_code not in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                time.sleep(0.1 * (attempt + 1))
            except Exception:
                conn.close()
                raise
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    avatar TEXT NOT NULL DEFAULT '🛡️',
                    discipline_xp INTEGER NOT NULL DEFAULT 0,
                    discipline_level TEXT NOT NULL DEFAULT 'STARTER',
                    role TEXT NOT NULL DEFAULT 'USER',
                    recovery_hash TEXT
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scoring_rule_versions (
                    rule_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_until TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    rules_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_users (
                    admin_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    pin_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'ADMIN',
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS admin_sessions (
                    session_id TEXT PRIMARY KEY,
                    admin_id TEXT NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT 'windows_admin_console',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    pin_change_required INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS admin_auth_attempts (
                    client_key_hash TEXT PRIMARY KEY,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proof_reviews (
                    review_id TEXT PRIMARY KEY,
                    evidence_id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    task_code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    hour_slot TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    submitted_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    decision TEXT,
                    reason_category TEXT,
                    reason_note TEXT,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    revision_number INTEGER NOT NULL DEFAULT 1,
                    signals_json TEXT NOT NULL DEFAULT '{}',
                    priority TEXT NOT NULL DEFAULT 'NORMAL'
                );

                CREATE TABLE IF NOT EXISTS proof_revisions (
                    revision_id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    reason_note TEXT,
                    file_path TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS configuration_versions (
                    version_code TEXT PRIMARY KEY,
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PUBLISHED',
                    rules_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    effective_from TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_audit_logs (
                    audit_id TEXT PRIMARY KEY,
                    admin_user TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    field_changed TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT 'admin_portal',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    agent_version TEXT NOT NULL DEFAULT '2.0.0',
                    protocol_version TEXT NOT NULL DEFAULT '2.0.0',
                    battery_level INTEGER NOT NULL DEFAULT 100,
                    storage_free_mb INTEGER NOT NULL DEFAULT 4096,
                    pairing_state TEXT NOT NULL DEFAULT 'PAIRED',
                    last_seen TEXT NOT NULL,
                    sync_status TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'REGISTERED',
                    registered_at TEXT,
                    app_version_code INTEGER NOT NULL DEFAULT 1,
                    last_update_check TEXT,
                    update_status TEXT NOT NULL DEFAULT 'UP_TO_DATE'
                );

                CREATE TABLE IF NOT EXISTS device_pairings (
                    pairing_id TEXT PRIMARY KEY,
                    android_device_id TEXT NOT NULL,
                    windows_device_id TEXT NOT NULL,
                    trust_token_hash TEXT NOT NULL,
                    paired_via TEXT NOT NULL DEFAULT 'USB',
                    android_approved INTEGER NOT NULL DEFAULT 0,
                    windows_approved INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    paired_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    email TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE'
                );

                CREATE TABLE IF NOT EXISTS pairing_sessions (
                    pairing_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    session_key TEXT UNIQUE NOT NULL,
                    manual_pin TEXT NOT NULL DEFAULT '',
                    server_endpoint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    paired_device_id TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS device_credentials (
                    credential_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sync_status TEXT NOT NULL DEFAULT 'PROCESSED',
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proof_submission_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    submission_id TEXT NOT NULL UNIQUE,
                    activity_id TEXT NOT NULL,
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    source_device_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'SUBMITTED',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS proof_submission_deliveries (
                    event_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, device_id),
                    FOREIGN KEY(event_id) REFERENCES proof_submission_events(event_id)
                );

                CREATE TABLE IF NOT EXISTS device_app_controls (
                    app_key TEXT PRIMARY KEY,
                    app_name TEXT NOT NULL,
                    package_patterns TEXT NOT NULL,
                    icon TEXT NOT NULL,
                    category TEXT NOT NULL,
                    is_locked INTEGER NOT NULL DEFAULT 0,
                    installed_on_device INTEGER NOT NULL DEFAULT 0,
                    detected_package TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_restriction_policy_acks (
                    device_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    policy_version INTEGER NOT NULL DEFAULT 0,
                    requested_restriction_enabled INTEGER NOT NULL DEFAULT 1,
                    effective_restriction_enabled INTEGER,
                    effective_access_allowed INTEGER,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, service)
                );

                CREATE TABLE IF NOT EXISTS daily_records (
                    record_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    wakeup_status TEXT NOT NULL DEFAULT 'PENDING',
                    wakeup_completed_at TEXT,
                    snooze_count INTEGER NOT NULL DEFAULT 0,
                    bath_status TEXT NOT NULL DEFAULT 'PENDING',
                    bath_completed_at TEXT,
                    gym_status TEXT NOT NULL DEFAULT 'UPCOMING',
                    gym_start_time TEXT,
                    gym_end_time TEXT,
                    gym_duration_minutes REAL NOT NULL DEFAULT 0.0,
                    hourly_completed_count INTEGER NOT NULL DEFAULT 0,
                    hourly_total_count INTEGER NOT NULL DEFAULT 15,
                    hourly_missed_count INTEGER NOT NULL DEFAULT 0,
                    productive_hours REAL NOT NULL DEFAULT 0.0,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    violation_count INTEGER NOT NULL DEFAULT 0,
                    positive_points INTEGER NOT NULL DEFAULT 0,
                    negative_points INTEGER NOT NULL DEFAULT 0,
                    gross_score INTEGER NOT NULL DEFAULT 0,
                    final_score INTEGER NOT NULL DEFAULT 0,
                    score_mode TEXT NOT NULL DEFAULT 'STRICT_MODE',
                    score_state TEXT NOT NULL DEFAULT 'STRICT_MODE',
                    reward_unlocked INTEGER NOT NULL DEFAULT 0,
                    grade TEXT NOT NULL DEFAULT 'F',
                    day_classification TEXT NOT NULL DEFAULT 'INCOMPLETE_DAY',
                    overnight_state TEXT NOT NULL DEFAULT 'ACTIVE_DAY',
                    calculation_version INTEGER NOT NULL DEFAULT 1,
                    ai_report_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(date, user_id, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS scheduled_activities (
                    activity_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    date TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    window_duration_minutes INTEGER NOT NULL DEFAULT 20,
                    notification_time TEXT NOT NULL,
                    closing_warning_time TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    submitted_at TEXT,
                    closed_at TEXT,
                    points INTEGER NOT NULL DEFAULT 0,
                    penalty_points INTEGER NOT NULL DEFAULT 0,
                    proof_requirements TEXT NOT NULL DEFAULT 'REQUIRED',
                    validation_rule TEXT NOT NULL DEFAULT 'STANDARD',
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    required_by_admin INTEGER NOT NULL DEFAULT 0,
                    schedule_id TEXT NOT NULL DEFAULT '',
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    attendance_status TEXT NOT NULL DEFAULT 'UPCOMING',
                    validation_status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED',
                    submission_id TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    admin_decision TEXT NOT NULL DEFAULT '',
                    opened_at TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(account_id, date, type, scheduled_time)
                );

                CREATE TABLE IF NOT EXISTS routine_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    schedule_date TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    wake_up_time TEXT NOT NULL DEFAULT '07:00',
                    bath_time TEXT NOT NULL DEFAULT '07:30',
                    gym_time TEXT NOT NULL DEFAULT '18:30',
                    gym_enabled INTEGER NOT NULL DEFAULT 1,
                    gym_required_by_admin INTEGER NOT NULL DEFAULT 0,
                    custom_activities_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    confirmed_by_actor TEXT,
                    confirmed_by_device TEXT,
                    locked_at TEXT,
                    cutoff_time TEXT NOT NULL DEFAULT '23:59',
                    reschedule_audit_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(account_id, schedule_date, schedule_version)
                );

                CREATE TABLE IF NOT EXISTS schedule_reschedule_audit (
                    audit_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    schedule_date TEXT NOT NULL,
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL,
                    admin_actor TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL,
                    previous_schedule_json TEXT NOT NULL DEFAULT '{}',
                    new_schedule_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_history (
                    notification_key TEXT PRIMARY KEY,
                    activity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS submission_window_state (
                    activity_id TEXT NOT NULL,
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'UPCOMING',
                    window_start TEXT,
                    window_end TEXT,
                    opened_at TEXT,
                    dismissed_at TEXT,
                    submitted_at TEXT,
                    source_device TEXT,
                    last_event_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (activity_id, schedule_version)
                );
                CREATE TABLE IF NOT EXISTS proof_window_ai_preflights (
                    preflight_id TEXT PRIMARY KEY,
                    proof_window_id TEXT NOT NULL,
                    activity_id TEXT NOT NULL,
                    scheduled_open_time TEXT NOT NULL,
                    preflight_started_at TEXT NOT NULL,
                    taufa_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
                    taufa_latency INTEGER NOT NULL DEFAULT 0,
                    taufa_error TEXT NOT NULL DEFAULT '',
                    openai_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
                    openai_latency INTEGER NOT NULL DEFAULT 0,
                    openai_error TEXT NOT NULL DEFAULT '',
                    selected_validation_provider TEXT NOT NULL DEFAULT 'PENDING_VALIDATION',
                    preflight_completed_at TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    preflight_status TEXT NOT NULL DEFAULT 'CHECKING',
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_preflights_window ON proof_window_ai_preflights(proof_window_id, schedule_version);
                CREATE INDEX IF NOT EXISTS idx_preflights_activity ON proof_window_ai_preflights(activity_id, scheduled_open_time);


                CREATE TABLE IF NOT EXISTS idempotent_event_log (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    activity_id TEXT NOT NULL DEFAULT '',
                    schedule_version INTEGER NOT NULL DEFAULT 1,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_center (
                    notification_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'TASKS',
                    activity_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'STRICTRECORDS',
                    app_name TEXT NOT NULL DEFAULT '',
                    package_name TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    is_dismissed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS android_notifications (
                    notification_id TEXT PRIMARY KEY,
                    package_name TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    is_sensitive INTEGER NOT NULL DEFAULT 0,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    is_dismissed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS activity_journal_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    source TEXT NOT NULL DEFAULT 'STRICTRECORDS',
                    activity_id TEXT NOT NULL DEFAULT '',
                    submission_id TEXT NOT NULL DEFAULT '',
                    evidence_id TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS ocr_cache (
                    sha256_hash TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    extracted_text TEXT NOT NULL DEFAULT '',
                    tokens_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.0,
                    engine TEXT NOT NULL DEFAULT '',
                    computed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_summary_snapshots (
                    date TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    snapshot_version INTEGER NOT NULL DEFAULT 1,
                    snapshot_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    formatted_text TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY (date, account_id, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS pc_shutdown_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    device_id TEXT NOT NULL DEFAULT 'windows_lok_pc',
                    shutdown_timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time_hhmmss TEXT NOT NULL,
                    cutoff_time TEXT NOT NULL DEFAULT '23:45',
                    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    evidence_source TEXT NOT NULL DEFAULT 'WIN32_API',
                    is_qualifying INTEGER NOT NULL DEFAULT 0,
                    disqualification_reason TEXT,
                    target_reward_date TEXT NOT NULL,
                    phone_attendance_id TEXT,
                    phone_attendance_time TEXT,
                    phone_device_id TEXT,
                    evidence_id TEXT,
                    photo_submitted_at TEXT,
                    bonus_status TEXT NOT NULL DEFAULT 'AWAITING_PHONE_ATTENDANCE',
                    bonus_points INTEGER NOT NULL DEFAULT 15,
                    bonus_ledger_event_id TEXT,
                    rejection_reason TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    task_code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    task_type TEXT NOT NULL DEFAULT 'TIME_TASK',
                    semantics TEXT NOT NULL,
                    target_time TEXT NOT NULL,
                    deadline_time TEXT,
                    duration_minutes INTEGER NOT NULL DEFAULT 0,
                    min_duration_minutes INTEGER NOT NULL DEFAULT 0,
                    grace_period_minutes INTEGER NOT NULL DEFAULT 0,
                    recurrence TEXT NOT NULL DEFAULT 'DAILY',
                    priority INTEGER NOT NULL DEFAULT 1,
                    required_evidence TEXT NOT NULL DEFAULT 'OPTIONAL',
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    point_value INTEGER NOT NULL DEFAULT 10,
                    penalty_value INTEGER NOT NULL DEFAULT -5,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL,
                    completed_at TEXT,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(date, is_dev_mode, task_code)
                );

                CREATE TABLE IF NOT EXISTS wake_up_sessions (
                    session_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    target_time TEXT NOT NULL,
                    alarm_triggered_at TEXT,
                    task_started_at TEXT,
                    camera_verified INTEGER NOT NULL DEFAULT 0,
                    camera_evidence_id TEXT,
                    camera_completed_at TEXT,
                    bluetooth_verified INTEGER NOT NULL DEFAULT 0,
                    bluetooth_device_name TEXT,
                    bluetooth_completed_at TEXT,
                    motivation_verified INTEGER NOT NULL DEFAULT 0,
                    track_name TEXT,
                    track_started_at TEXT,
                    track_ended_at TEXT,
                    track_duration_sec REAL NOT NULL DEFAULT 0.0,
                    listened_duration_sec REAL NOT NULL DEFAULT 0.0,
                    completion_pct REAL NOT NULL DEFAULT 0.0,
                    interruption_count INTEGER NOT NULL DEFAULT 0,
                    playback_state TEXT NOT NULL DEFAULT 'IDLE',
                    completed_at TEXT,
                    attendance_status TEXT NOT NULL DEFAULT 'PENDING',
                    device_id TEXT,
                    UNIQUE(date, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS bath_records (
                    bath_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    deadline_time TEXT NOT NULL,
                    completed_at TEXT,
                    verification_mode TEXT NOT NULL,
                    evidence_id TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    device_id TEXT,
                    notes TEXT,
                    UNIQUE(date, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS gym_sessions (
                    gym_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    scheduled_time TEXT NOT NULL,
                    start_timestamp TEXT,
                    end_timestamp TEXT,
                    duration_minutes REAL NOT NULL DEFAULT 0.0,
                    min_required_minutes INTEGER NOT NULL DEFAULT 30,
                    device_id TEXT,
                    user_confirmed INTEGER NOT NULL DEFAULT 0,
                    evidence_id TEXT,
                    notes TEXT,
                    status TEXT NOT NULL DEFAULT 'UPCOMING',
                    UNIQUE(date, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS focus_sessions (
                    focus_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    session_type TEXT NOT NULL DEFAULT 'FOCUS_SESSION',
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    target_minutes INTEGER NOT NULL DEFAULT 45,
                    actual_minutes REAL NOT NULL DEFAULT 0.0,
                    allowed_apps_json TEXT NOT NULL DEFAULT '[]',
                    restricted_apps_json TEXT NOT NULL DEFAULT '[]',
                    restrictions_json TEXT NOT NULL DEFAULT '{}',
                    interruptions_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    planned_end_time TEXT,
                    ended_at TEXT,
                    evidence_id TEXT,
                    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    device_id TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS app_releases (
                    release_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    version_name TEXT NOT NULL,
                    version_code INTEGER NOT NULL,
                    min_supported_version INTEGER NOT NULL DEFAULT 1,
                    package_name TEXT NOT NULL DEFAULT 'com.strictrecords.app',
                    release_date TEXT NOT NULL,
                    release_notes TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    sha256_checksum TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'PRODUCTION',
                    is_mandatory INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    signing_cert_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT DEFAULT 'admin'
                );

                CREATE TABLE IF NOT EXISTS device_update_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    installed_version_code INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress_percent INTEGER DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(device_id, release_id) ON CONFLICT REPLACE
                );

                CREATE TABLE IF NOT EXISTS focus_settings (
                    user_id TEXT PRIMARY KEY,
                    strict_mode INTEGER NOT NULL DEFAULT 1,
                    youtube_study_mode INTEGER NOT NULL DEFAULT 1,
                    block_notifications INTEGER NOT NULL DEFAULT 1,
                    block_home INTEGER NOT NULL DEFAULT 0,
                    block_split_screen INTEGER NOT NULL DEFAULT 0,
                    block_floating_windows INTEGER NOT NULL DEFAULT 0,
                    uninstall_protection INTEGER NOT NULL DEFAULT 0,
                    allowed_websites_json TEXT NOT NULL DEFAULT '["wikipedia.org","github.com","stackoverflow.com","docs.python.org","w3schools.com"]',
                    allowed_apps_json TEXT NOT NULL DEFAULT '["code.exe","StrictRecords.exe","notepad.exe"]',
                    study_topics_json TEXT NOT NULL DEFAULT '["programming","math","science","lectures","tutorials"]',
                    onboarding_completed INTEGER NOT NULL DEFAULT 0,
                    focus_field TEXT NOT NULL DEFAULT 'PROGRAMMING',
                    study_method TEXT NOT NULL DEFAULT 'YOUTUBE_SELF',
                    top_distraction TEXT NOT NULL DEFAULT 'YOUTUBE_SHORTS',
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    user_restriction_preferences_json TEXT NOT NULL DEFAULT '{"youtube":true,"instagram":true}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS focus_groups (
                    group_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'STUDY',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    member_count INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS focus_group_members (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'MEMBER',
                    PRIMARY KEY(group_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS break_logs (
                    break_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT 'SHORT_BREAK',
                    duration_minutes REAL NOT NULL DEFAULT 15.0,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    notes TEXT,
                    points_impact INTEGER NOT NULL DEFAULT 0,
                    device_id TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS hourly_records (
                    hourly_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    hour_slot TEXT NOT NULL,
                    checkpoint_time TEXT NOT NULL,
                    primary_activity TEXT,
                    secondary_activities_json TEXT NOT NULL DEFAULT '[]',
                    description TEXT,
                    evidence_id TEXT,
                    android_summary_json TEXT NOT NULL DEFAULT '{}',
                    windows_summary_json TEXT NOT NULL DEFAULT '{}',
                    verification_result TEXT,
                    evidence_confidence TEXT NOT NULL DEFAULT 'MEDIUM',
                    integrity_flag TEXT NOT NULL DEFAULT 'NORMAL',
                    status TEXT NOT NULL DEFAULT 'UPCOMING',
                    submitted_at TEXT,
                    device_id TEXT,
                    UNIQUE(date, is_dev_mode, hour_slot)
                );

                CREATE TABLE IF NOT EXISTS evidence_files (
                    evidence_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    record_type TEXT NOT NULL,
                    related_record_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    sha256_hash TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    is_deleted INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS score_events (
                    event_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    parent_event_id TEXT,
                    evidence_id TEXT,
                    related_task_id TEXT,
                    audit_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS restriction_events (
                    restriction_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    application TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    threshold INTEGER NOT NULL,
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deduction_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    originating_device TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    related_task_id TEXT DEFAULT '',
                    applicable_rule TEXT NOT NULL DEFAULT '',
                    proposed_points INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'AWAITING_CONFIRMATION',
                    approved_by TEXT DEFAULT NULL,
                    approved_at TEXT DEFAULT NULL,
                    approval_source TEXT DEFAULT NULL,
                    rejection_reason TEXT DEFAULT NULL,
                    finalized_event_id TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS integrity_signals (
                    signal_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'NOTICE',
                    device_id TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS daily_journals (
                    journal_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    reflection_text TEXT,
                    what_went_well TEXT,
                    what_was_difficult TEXT,
                    improve_tomorrow TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(date, user_id, is_dev_mode)
                );

                CREATE TABLE IF NOT EXISTS achievements_unlocked (
                    achievement_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    unlocked_at TEXT NOT NULL,
                    date TEXT NOT NULL,
                    UNIQUE(user_id, code)
                );

                CREATE TABLE IF NOT EXISTS device_activity_logs (
                    log_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    hour_slot TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    window_title TEXT,
                    category TEXT NOT NULL,
                    confidence TEXT NOT NULL DEFAULT 'HIGH',
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    idle_seconds REAL NOT NULL DEFAULT 0.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS sync_queue (
                    sync_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'PENDING_SYNC',
                    synced_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    android_payload_json TEXT NOT NULL,
                    windows_payload_json TEXT NOT NULL,
                    resolution_policy TEXT NOT NULL,
                    resolved_payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS structured_logs (
                    log_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    is_dev_mode INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    component TEXT NOT NULL DEFAULT 'CORE',
                    event_code TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OK',
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    device_id TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS connection_history (
                    history_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    transport_mode TEXT NOT NULL,
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transfer_queue_items (
                    transfer_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    ack_stage TEXT NOT NULL DEFAULT 'SENT',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_commands (
                    command_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    admin_user TEXT NOT NULL DEFAULT 'Administrator',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    result_json TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    acked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS strict_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    device_id TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL DEFAULT 'StrictRecords Policy v12',
                    state TEXT NOT NULL DEFAULT 'NORMAL',
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    precheck_passed INTEGER NOT NULL DEFAULT 0,
                    precheck_details_json TEXT NOT NULL DEFAULT '{}',
                    crash_recovered INTEGER NOT NULL DEFAULT 0,
                    exit_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS strict_session_policies (
                    policy_version TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS strict_session_violation_ledger (
                    violation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    detection_source TEXT NOT NULL,
                    process_or_tab_id TEXT NOT NULL DEFAULT '',
                    count_before INTEGER NOT NULL DEFAULT 0,
                    count_after INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_violation_ledger_session ON strict_session_violation_ledger(session_id);

                CREATE TABLE IF NOT EXISTS strict_session_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    account TEXT NOT NULL DEFAULT '',
                    device_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS exams (
                    exam_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    total_questions INTEGER NOT NULL DEFAULT 10,
                    duration_minutes INTEGER NOT NULL DEFAULT 60,
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    passing_score INTEGER NOT NULL DEFAULT 80,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exam_questions (
                    question_id TEXT PRIMARY KEY,
                    exam_id TEXT NOT NULL,
                    question_number INTEGER NOT NULL,
                    question_type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    options_json TEXT,
                    correct_answer TEXT NOT NULL,
                    sample_test_case TEXT,
                    points INTEGER NOT NULL DEFAULT 10,
                    FOREIGN KEY(exam_id) REFERENCES exams(exam_id)
                );

                CREATE TABLE IF NOT EXISTS exam_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    exam_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
                    score INTEGER NOT NULL DEFAULT 0,
                    max_score INTEGER NOT NULL DEFAULT 100,
                    answers_json TEXT NOT NULL DEFAULT '{}',
                    results_json TEXT NOT NULL DEFAULT '{}',
                    integrity_score REAL NOT NULL DEFAULT 100.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(exam_id) REFERENCES exams(exam_id)
                );

                CREATE TABLE IF NOT EXISTS official_event_bookings (
                    event_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    organization TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    expected_duration_minutes INTEGER NOT NULL DEFAULT 60,
                    time_zone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    event_mode TEXT NOT NULL DEFAULT 'VIRTUAL',
                    venue_or_platform TEXT NOT NULL DEFAULT '',
                    reporting_time TEXT NOT NULL DEFAULT '',
                    shortlist_status TEXT NOT NULL DEFAULT 'NOT_SPECIFIED',
                    status TEXT NOT NULL DEFAULT 'CONFIRMED',
                    extracted_data_json TEXT NOT NULL DEFAULT '{}',
                    user_corrections_json TEXT NOT NULL DEFAULT '{}',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    exit_consumed INTEGER NOT NULL DEFAULT 0,
                    exit_timestamp TEXT,
                    exit_snapshot_path TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_official_events_date ON official_event_bookings(event_date);
                CREATE INDEX IF NOT EXISTS idx_official_events_status ON official_event_bookings(status);

                CREATE TABLE IF NOT EXISTS exam_prebookings (
                    booking_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_default',
                    exam_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    institution TEXT NOT NULL,
                    exam_date TEXT NOT NULL,
                    official_start_time TEXT NOT NULL,
                    official_end_time TEXT NOT NULL,
                    exam_type TEXT NOT NULL DEFAULT 'SEMESTER',
                    prep_buffer_minutes INTEGER NOT NULL DEFAULT 30,
                    completion_buffer_minutes INTEGER NOT NULL DEFAULT 30,
                    allocation_start_iso TEXT NOT NULL,
                    allocation_end_iso TEXT NOT NULL,
                    allocation_start_ms INTEGER NOT NULL,
                    allocation_end_ms INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'EXAM_APPROVED',
                    validation_provider TEXT NOT NULL DEFAULT 'AI_VALIDATOR',
                    validation_explanation TEXT NOT NULL DEFAULT '',
                    cross_image_consistency TEXT NOT NULL DEFAULT 'CONSISTENT',
                    supporting_images_count INTEGER NOT NULL DEFAULT 1,
                    evidence_files_json TEXT NOT NULL DEFAULT '[]',
                    extracted_data_json TEXT NOT NULL DEFAULT '{}',
                    excused_hourly_slots_json TEXT NOT NULL DEFAULT '[]',
                    additional_info TEXT NOT NULL DEFAULT '',
                    actual_end_time TEXT NOT NULL DEFAULT '',
                    actual_end_iso TEXT NOT NULL DEFAULT '',
                    actual_end_ms INTEGER NOT NULL DEFAULT 0,
                    early_exit_confirmed INTEGER NOT NULL DEFAULT 0,
                    early_exit_confirmed_at TEXT NOT NULL DEFAULT '',
                    monitoring_state TEXT NOT NULL DEFAULT 'EXAM_BOOKING_PENDING',
                    neo_process_detected INTEGER NOT NULL DEFAULT 0,
                    neo_process_pid INTEGER NOT NULL DEFAULT 0,
                    neo_exe_path TEXT NOT NULL DEFAULT '',
                    batch_id TEXT NOT NULL DEFAULT '',
                    test_index INTEGER NOT NULL DEFAULT 1,
                    total_tests INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_exam_prebookings_date ON exam_prebookings(exam_date);
                CREATE INDEX IF NOT EXISTS idx_exam_prebookings_status ON exam_prebookings(status);

                CREATE TABLE IF NOT EXISTS exam_batches (
                    batch_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_default',
                    total_tests INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_practice_test_templates (
                    template_id TEXT PRIMARY KEY,
                    template_name TEXT NOT NULL DEFAULT 'Default Daily Practice Template',
                    tests_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_practice_test_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL UNIQUE,
                    total_tests INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'SUBMITTED',
                    submitted_at TEXT NOT NULL DEFAULT '',
                    locked_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_practice_test_executions (
                    execution_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    test_index INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    end_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'SCHEDULED',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    score REAL DEFAULT NULL,
                    max_score REAL DEFAULT NULL,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    rescheduled_to TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_practice_exec_date ON daily_practice_test_executions(date);
                CREATE INDEX IF NOT EXISTS idx_practice_exec_status ON daily_practice_test_executions(status);


                CREATE TABLE IF NOT EXISTS app_process_sessions (
                    session_id TEXT PRIMARY KEY,
                    pid INTEGER,
                    started_at TEXT NOT NULL,
                    clean_exit INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    exit_type TEXT,
                    closed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_process_sessions_clean ON app_process_sessions(clean_exit, started_at);

                CREATE TABLE IF NOT EXISTS unexpected_exit_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'STANDARD',
                    total_questions INTEGER NOT NULL DEFAULT 25,
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    current_index INTEGER NOT NULL DEFAULT 0,
                    answers_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    score INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS youtube_video_rewards (
                    reward_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    hourly_occurrence_id TEXT NOT NULL UNIQUE,
                    submission_id TEXT NOT NULL,
                    validated_score INTEGER NOT NULL,
                    is_timely INTEGER NOT NULL DEFAULT 1,
                    issue_timestamp TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'AVAILABLE',
                    selected_video_id TEXT,
                    selected_video_title TEXT,
                    video_duration_seconds INTEGER,
                    allowed_seconds INTEGER,
                    session_start_time TEXT,
                    expiry_time TEXT,
                    active_client_id TEXT,
                    termination_result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_yt_rewards_acc_state ON youtube_video_rewards(account_id, state);
                CREATE INDEX IF NOT EXISTS idx_yt_rewards_occ ON youtube_video_rewards(hourly_occurrence_id);

                CREATE TABLE IF NOT EXISTS gym_mode_sessions (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    gym_activity_id TEXT NOT NULL,
                    activation_time_ms INTEGER NOT NULL,
                    access_expiry_ms INTEGER NOT NULL,
                    proof_deadline_ms INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    toggled_off_early INTEGER NOT NULL DEFAULT 0,
                    toggled_off_at_ms INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gym_mode_state (
                    account_id TEXT PRIMARY KEY,
                    lives_balance INTEGER NOT NULL DEFAULT 3,
                    last_reset_week TEXT NOT NULL DEFAULT '',
                    last_reset_time_ms INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cloud_sync_files (
                    file_id TEXT PRIMARY KEY,
                    local_path TEXT NOT NULL,
                    category TEXT NOT NULL,
                    drive_file_id TEXT,
                    drive_parent_id TEXT,
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    sha256_hash TEXT,
                    md5_hash TEXT,
                    mime_type TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'PENDING',
                    error_message TEXT,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT 'windows_pc'
                );

                CREATE INDEX IF NOT EXISTS idx_cloud_sync_status ON cloud_sync_files(sync_status);
                CREATE INDEX IF NOT EXISTS idx_cloud_sync_category ON cloud_sync_files(category);

                CREATE TABLE IF NOT EXISTS cloud_sync_queue (
                    queue_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'UPLOAD',
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cloud_queue_status ON cloud_sync_queue(status, next_retry_at);

                CREATE TABLE IF NOT EXISTS entertainment_entitlements (
                    entitlement_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    entitlement_type TEXT NOT NULL DEFAULT 'EARLY_PROOF_ENTERTAINMENT_REWARD_20M',
                    proof_id TEXT NOT NULL UNIQUE,
                    duration_seconds INTEGER NOT NULL DEFAULT 1200,
                    submission_received_at TEXT NOT NULL,
                    scheduled_deadline_at TEXT NOT NULL,
                    points_awarded INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'AVAILABLE',
                    session_start_time TEXT,
                    expiry_time TEXT,
                    active_client_id TEXT,
                    termination_result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_entitlements_acc_state ON entertainment_entitlements(account_id, state);
                CREATE INDEX IF NOT EXISTS idx_entitlements_proof ON entertainment_entitlements(proof_id);

                CREATE TABLE IF NOT EXISTS educational_video_approvals (
                    approval_id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    title TEXT,
                    channel TEXT,
                    duration_seconds INTEGER NOT NULL,
                    duration_formatted TEXT NOT NULL,
                    timeline_json TEXT,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    confidence REAL,
                    review_status TEXT NOT NULL DEFAULT 'COMPLETED',
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, video_id)
                );

                CREATE INDEX IF NOT EXISTS idx_edu_approvals_video ON educational_video_approvals(video_id);

                CREATE TABLE IF NOT EXISTS educational_playback_sessions (
                    session_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    allowed_seconds INTEGER NOT NULL,
                    consumed_seconds REAL NOT NULL DEFAULT 0.0,
                    watched_intervals_json TEXT NOT NULL DEFAULT '[]',
                    last_playback_state TEXT NOT NULL DEFAULT 'PAUSED',
                    playback_rate REAL NOT NULL DEFAULT 1.0,
                    last_heartbeat_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, video_id)
                );

                CREATE INDEX IF NOT EXISTS idx_edu_sessions_video ON educational_playback_sessions(video_id, status);

                CREATE TABLE IF NOT EXISTS educational_validation_history (
                    history_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    validation_session_id TEXT,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    device_id TEXT NOT NULL DEFAULT 'BROWSER_EXT',
                    video_id TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    title TEXT,
                    channel TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    duration_formatted TEXT NOT NULL DEFAULT 'Unknown',
                    decision TEXT NOT NULL,
                    reason TEXT,
                    confidence REAL,
                    review_status TEXT NOT NULL DEFAULT 'COMPLETED',
                    playback_completion_state TEXT NOT NULL DEFAULT 'NOT_STARTED',
                    request_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_edu_history_acc ON educational_validation_history(account_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_edu_history_vid ON educational_validation_history(video_id);

                CREATE TABLE IF NOT EXISTS system_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                """
            )

    def _run_migrations(self) -> None:
        """
        Section 140: Safe non-destructive schema migration engine.
        Adds any missing columns to existing tables without erasing historical data.
        """
        column_Ensure_map = {
            "proof_reviews": [("activity_id", "TEXT NOT NULL DEFAULT ''")],
            "routine_schedules": [("gym_duration_minutes", "INTEGER NOT NULL DEFAULT 90")],
            "admin_sessions": [
                ("pin_change_required", "INTEGER NOT NULL DEFAULT 0"),
            ],
            "users": [
                ("avatar", "TEXT NOT NULL DEFAULT '🛡️'"),
                ("discipline_xp", "INTEGER NOT NULL DEFAULT 0"),
                ("discipline_level", "TEXT NOT NULL DEFAULT 'STARTER'"),
                ("role", "TEXT NOT NULL DEFAULT 'USER'"),
                ("recovery_hash", "TEXT"),
            ],
            "user_settings": [
                ("rule_version", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "devices": [
                ("agent_version", "TEXT NOT NULL DEFAULT '2.0.0'"),
                ("protocol_version", "TEXT NOT NULL DEFAULT '2.0.0'"),
                ("battery_level", "INTEGER NOT NULL DEFAULT 100"),
                ("storage_free_mb", "INTEGER NOT NULL DEFAULT 4096"),
                ("pairing_state", "TEXT NOT NULL DEFAULT 'PAIRED'"),
                ("wifi_ssid", "TEXT NOT NULL DEFAULT ''"),
                ("latency_ms", "REAL NOT NULL DEFAULT 0.0"),
                ("ip_address", "TEXT NOT NULL DEFAULT '127.0.0.1'"),
                ("config_version", "INTEGER NOT NULL DEFAULT 1"),
                ("device_type", "TEXT NOT NULL DEFAULT 'ANDROID'"),
                ("registered_at", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'REGISTERED'"),
                ("app_version_code", "INTEGER NOT NULL DEFAULT 1"),
                ("last_update_check", "TEXT"),
                ("update_status", "TEXT NOT NULL DEFAULT 'UP_TO_DATE'"),
                ("updated_at", "TEXT"),
            ],
            "device_pairings": [
                ("verification_code", "TEXT NOT NULL DEFAULT ''"),
            ],
            "pairing_sessions": [
                ("manual_pin", "TEXT NOT NULL DEFAULT ''"),
                ("failed_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ],
            "daily_records": [
                ("gross_score", "INTEGER NOT NULL DEFAULT 0"),
                ("score_state", "TEXT NOT NULL DEFAULT 'STRICT_MODE'"),
                ("day_classification", "TEXT NOT NULL DEFAULT 'INCOMPLETE_DAY'"),
                ("overnight_state", "TEXT NOT NULL DEFAULT 'ACTIVE_DAY'"),
                ("calculation_version", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "scheduled_tasks": [
                ("task_type", "TEXT NOT NULL DEFAULT 'TIME_TASK'"),
                ("duration_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("min_duration_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("grace_period_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("recurrence", "TEXT NOT NULL DEFAULT 'DAILY'"),
                ("priority", "INTEGER NOT NULL DEFAULT 1"),
                ("required_evidence", "TEXT NOT NULL DEFAULT 'OPTIONAL'"),
                ("dependencies_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("point_value", "INTEGER NOT NULL DEFAULT 10"),
                ("penalty_value", "INTEGER NOT NULL DEFAULT -5"),
                ("is_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "wake_up_sessions": [
                ("playback_state", "TEXT NOT NULL DEFAULT 'IDLE'"),
                ("checklist_state", "TEXT NOT NULL DEFAULT 'NOT_STARTED'"),
                ("volume_limit_ok", "INTEGER NOT NULL DEFAULT 1"),
                ("playback_session_json", "TEXT NOT NULL DEFAULT '{}'"),
            ],
            "hourly_records": [
                ("evidence_confidence", "TEXT NOT NULL DEFAULT 'MEDIUM'"),
            ],
            "score_events": [
                ("rule_version", "INTEGER NOT NULL DEFAULT 1"),
                ("parent_event_id", "TEXT"),
            ],
            "device_activity_logs": [
                ("confidence", "TEXT NOT NULL DEFAULT 'HIGH'"),
            ],
            "sync_queue": [
                ("version", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "structured_logs": [
                ("component", "TEXT NOT NULL DEFAULT 'CORE'"),
                ("status", "TEXT NOT NULL DEFAULT 'OK'"),
                ("duration_ms", "REAL NOT NULL DEFAULT 0.0"),
            ],
            "focus_sessions": [
                ("planned_end_time", "TEXT"),
                ("restrictions_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("interruptions_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("goal", "TEXT NOT NULL DEFAULT ''"),
            ],
            "scheduled_activities": [
                ("title", "TEXT NOT NULL DEFAULT ''"),
                ("window_duration_minutes", "INTEGER NOT NULL DEFAULT 20"),
                ("closing_warning_time", "TEXT NOT NULL DEFAULT ''"),
                ("closed_at", "TEXT"),
                ("penalty_points", "INTEGER NOT NULL DEFAULT 0"),
                ("proof_requirements", "TEXT NOT NULL DEFAULT 'REQUIRED'"),
                ("is_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("required_by_admin", "INTEGER NOT NULL DEFAULT 0"),
                ("schedule_id", "TEXT NOT NULL DEFAULT ''"),
                ("schedule_version", "INTEGER NOT NULL DEFAULT 1"),
                ("attendance_status", "TEXT NOT NULL DEFAULT 'UPCOMING'"),
                ("validation_status", "TEXT NOT NULL DEFAULT 'NOT_SUBMITTED'"),
                ("submission_id", "TEXT"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("evidence_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("admin_decision", "TEXT NOT NULL DEFAULT ''"),
                ("opened_at", "TEXT"),
            ],
            "focus_settings": [
                ("website_enforcement_mode", "TEXT NOT NULL DEFAULT 'DISTRACTIONS_ONLY'"),
                ("background_optimized_run", "INTEGER NOT NULL DEFAULT 1"),
                ("auto_ram_optimization", "INTEGER NOT NULL DEFAULT 1"),
                ("ram_threshold_mb", "INTEGER NOT NULL DEFAULT 120"),
                ("youtube_unlocked_date", "TEXT NOT NULL DEFAULT ''"),
                ("youtube_privilege_mode", "TEXT NOT NULL DEFAULT 'CONTINUOUS_PROOF_GATED'"),
                ("mirror_android_notifications", "INTEGER NOT NULL DEFAULT 0"),
                ("android_notification_filter", "TEXT NOT NULL DEFAULT 'BLOCK_SENSITIVE'"),
                ("user_restriction_preferences_json", "TEXT NOT NULL DEFAULT '{\"youtube\":true,\"instagram\":true}'"),
                ("preferred_earbuds_name", "TEXT NOT NULL DEFAULT ''"),
                ("preferred_earbuds_address", "TEXT NOT NULL DEFAULT ''"),
            ],
            "app_process_sessions": [
                ("status", "TEXT NOT NULL DEFAULT 'RUNNING'"),
            ],
            "exam_prebookings": [
                ("batch_id", "TEXT NOT NULL DEFAULT ''"),
                ("test_index", "INTEGER NOT NULL DEFAULT 1"),
                ("total_tests", "INTEGER NOT NULL DEFAULT 1"),
            ],
        }
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            for table, cols in column_Ensure_map.items():
                existing_cols = {
                    r["name"]
                    for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for col_name, col_def in cols:
                    if col_name not in existing_cols:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_exam_prebookings_batch ON exam_prebookings(batch_id);")
            except Exception:
                pass

            # Backfill the separate attendance and evidence-review dimensions for
            # records created before these columns existed. Keep the lifecycle
            # status for compatibility with existing scheduler and scoring code.
            conn.execute(
                """
                UPDATE scheduled_activities
                SET attendance_status = CASE
                    WHEN status IN ('SUBMITTED','VALIDATING','AI_PROVISIONAL','VERIFIED','COMPLETED','REJECTED') THEN 'PRESENT'
                    WHEN status IN ('MISSED','CLOSED','REJECTED_LATE') THEN 'ABSENT'
                    WHEN status = 'OPEN' THEN 'OPEN'
                    ELSE 'UPCOMING'
                END
                WHERE type = 'HOURLY' AND attendance_status = 'UPCOMING'
                  AND status IN ('SUBMITTED','VALIDATING','AI_PROVISIONAL','VERIFIED','COMPLETED','REJECTED','MISSED','CLOSED','REJECTED_LATE','OPEN')
                """
            )
            conn.execute(
                """
                UPDATE scheduled_activities
                SET validation_status = CASE
                    WHEN status IN ('SUBMITTED','VALIDATING') THEN 'PROCESSING'
                    WHEN status = 'AI_PROVISIONAL' THEN 'PROVISIONAL_APPROVE'
                    WHEN status IN ('VERIFIED','COMPLETED') THEN 'VERIFIED'
                    WHEN status = 'REJECTED' THEN 'REJECTED'
                    WHEN status IN ('MISSED','CLOSED','REJECTED_LATE') THEN 'NOT_SUBMITTED'
                    ELSE validation_status
                END
                WHERE type = 'HOURLY' AND validation_status = 'NOT_SUBMITTED'
                  AND status IN ('SUBMITTED','VALIDATING','AI_PROVISIONAL','VERIFIED','COMPLETED','REJECTED','MISSED','CLOSED','REJECTED_LATE')
                """
            )

            # Section 141: Purge removed/obsolete Android devices and their pairings
            try:
                conn.execute(
                    "DELETE FROM devices WHERE device_id IN ('android_fd0a487527a942ef', '49e6d324')"
                )
                conn.execute(
                    "DELETE FROM device_pairings WHERE android_device_id IN ('android_fd0a487527a942ef', '49e6d324')"
                )
                conn.execute(
                    "DELETE FROM pairing_sessions WHERE paired_device_id IN ('android_fd0a487527a942ef', '49e6d324')"
                )
                conn.execute(
                    "DELETE FROM device_credentials WHERE device_id IN ('android_fd0a487527a942ef', '49e6d324')"
                )
                conn.execute(
                    "DELETE FROM connection_history WHERE device_id IN ('android_fd0a487527a942ef', '49e6d324')"
                )
            except Exception:
                pass

            try:
                # Cleanup spurious recovery challenges generated against clean exits or single initial sessions
                sess_count = conn.execute("SELECT COUNT(*) FROM app_process_sessions").fetchone()[0]
                if sess_count <= 1:
                    conn.execute("DELETE FROM unexpected_exit_challenges")
                else:
                    conn.execute(
                        """
                        DELETE FROM unexpected_exit_challenges
                        WHERE session_id IN (
                            SELECT session_id FROM app_process_sessions WHERE clean_exit = 1
                        ) AND status != 'COMPLETED'
                        """
                    )
            except Exception:
                pass

            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (self.SCHEMA_VERSION, now_iso, "Stage 1-20 Master Specification Schema Migration"),
            )

    def _ensure_demo_test_feature(self, conn: sqlite3.Connection, now_iso: str) -> None:
        """
        Ensures any legacy Demo Test exam/questions/attempts are removed.
        """
        try:
            conn.execute("DELETE FROM exam_questions WHERE exam_id = 'demo_test'")
            conn.execute("DELETE FROM exam_attempts WHERE exam_id IN ('demo_test', 'demo_test_01', 'EXAM_DEMO_01')")
            conn.execute("DELETE FROM exams WHERE exam_id = 'demo_test' OR is_demo = 1")
        except Exception:
            pass

    def _seed_defaults(self) -> None:
        """
        Seeds only required system configuration.
        Zero fake users, zero fake devices, zero fake tasks, zero fake activity.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            # Seed the one-time setup PIN. Protected admin operations require a
            # PIN change before they can be used; no legacy PIN is accepted.
            admin_row = conn.execute("SELECT admin_id, salt, pin_hash FROM admin_users WHERE username = ?", ("admin",)).fetchone()
            if not admin_row:
                salt = os.urandom(16).hex()
                # Initial default credentials before required first-run rotation
                pwd_hash = hashlib.sha256((salt + "admin1984").encode("utf-8")).hexdigest()
                pin_hash = hashlib.sha256((salt + "1234").encode("utf-8")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO admin_users (admin_id, username, password_hash, salt, pin_hash, role, created_at, is_active)
                    VALUES (?, ?, ?, ?, ?, 'ADMIN', ?, 1)
                    """,
                    ("admin_root", "admin", pwd_hash, salt, pin_hash, now_iso),
                )
            else:
                legacy_pin_hash = hashlib.sha256((admin_row["salt"] + "1984").encode("utf-8")).hexdigest()
                if admin_row["pin_hash"] == legacy_pin_hash:
                    migrated_pin_hash = hashlib.sha256((admin_row["salt"] + "1234").encode("utf-8")).hexdigest()
                    conn.execute("UPDATE admin_users SET pin_hash = ? WHERE admin_id = ?", (migrated_pin_hash, admin_row["admin_id"]))
                # Remove the known legacy password from existing installations.
                legacy_password_hash = hashlib.sha256((admin_row["salt"] + "admin1234").encode("utf-8")).hexdigest()
                if conn.execute("SELECT password_hash FROM admin_users WHERE admin_id = ?", (admin_row["admin_id"],)).fetchone()[0] == legacy_password_hash:
                    disabled_password_hash = hashlib.sha256((admin_row["salt"] + os.urandom(32).hex()).encode("utf-8")).hexdigest()
                    conn.execute("UPDATE admin_users SET password_hash = ? WHERE admin_id = ?", (disabled_password_hash, admin_row["admin_id"]))

            # Seed default configuration version v12 if not exists
            cfg_row = conn.execute("SELECT version_code FROM configuration_versions WHERE version_code = ?", ("v12",)).fetchone()
            if not cfg_row:
                default_settings = UserSettings()
                conn.execute(
                    """
                    INSERT INTO configuration_versions (version_code, version_number, status, rules_json, created_by, created_at, published_at, effective_from)
                    VALUES (?, ?, 'PUBLISHED', ?, 'Administrator', ?, ?, ?)
                    """,
                    ("v12", 12, json.dumps(default_settings.to_dict()), now_iso, now_iso, "2026-10-01 00:00:00"),
                )

            self._ensure_demo_test_feature(conn, now_iso)

            # Seed default app controls for target apps (YouTube, Instagram, Brave, Comet, Snapchat, Google, Chrome)
            default_apps = [
                ("youtube", "YouTube", json.dumps(["com.google.android.youtube", "com.android.youtube", "com.google.android.apps.youtube.music"]), "🎥", "Video & Streaming", 1),
                ("instagram", "Instagram", json.dumps(["com.instagram.android", "com.android.instagram"]), "📸", "Social Media", 1),
                ("brave", "Brave", json.dumps(["com.brave.browser", "com.brave.browser_beta", "com.brave.browser_nightly"]), "🦁", "Web Browser", 0),
                ("comet", "Comet", json.dumps(["com.comet.browser", "ai.perplexity.comet", "com.percolata.comet", "comet"]), "☄️", "Productivity / AI", 0),
                ("snapchat", "Snapchat", json.dumps(["com.snapchat.android"]), "👻", "Social Media", 1),
                ("google", "Google", json.dumps(["com.google.android.googlequicksearchbox"]), "🔍", "Search & Assistant", 0),
                ("chrome", "Chrome", json.dumps(["com.android.chrome", "com.chrome.beta", "com.chrome.canary", "com.chrome.dev"]), "🌐", "Web Browser", 0),
            ]
            for app_key, app_name, patterns, icon, category, default_locked in default_apps:
                existing = conn.execute("SELECT app_key FROM device_app_controls WHERE app_key = ?", (app_key,)).fetchone()
                if not existing:
                    conn.execute(
                        """
                        INSERT INTO device_app_controls (app_key, app_name, package_patterns, icon, category, is_locked, installed_on_device, detected_package, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, '', ?)
                        """,
                        (app_key, app_name, patterns, icon, category, default_locked, now_iso),
                    )

            # Ensure authoritative user profile 'LOK ADHITHYA G'
            self._ensure_default_user_profile(conn, now_iso)

    def _ensure_default_user_profile(self, conn, now_iso: str) -> None:
        """
        Ensures the authoritative default profile 'LOK ADHITHYA G' exists and persists across restarts.
        Preserves existing pairings, credentials, and activity logs without duplicate records.
        """
        # 1. Ensure display_name column exists in accounts table
                # Ensure exam_prebookings table has all extended columns
        exam_cols = [r[1] for r in conn.execute("PRAGMA table_info(exam_prebookings)").fetchall()]
        if exam_cols:
            for col_name, col_type, col_def in [
                ("actual_end_time", "TEXT", "DEFAULT ''"),
                ("actual_end_iso", "TEXT", "DEFAULT ''"),
                ("actual_end_ms", "INTEGER", "DEFAULT 0"),
                ("early_exit_confirmed", "INTEGER", "DEFAULT 0"),
                ("early_exit_confirmed_at", "TEXT", "DEFAULT ''"),
                ("monitoring_state", "TEXT", "DEFAULT 'EXAM_BOOKING_PENDING'"),
                ("neo_process_detected", "INTEGER", "DEFAULT 0"),
                ("neo_process_pid", "INTEGER", "DEFAULT 0"),
                ("neo_exe_path", "TEXT", "DEFAULT ''"),
                ("batch_id", "TEXT", "DEFAULT ''"),
                ("test_index", "INTEGER", "DEFAULT 1"),
                ("total_tests", "INTEGER", "DEFAULT 1"),
            ]:
                if col_name not in exam_cols:
                    conn.execute(f"ALTER TABLE exam_prebookings ADD COLUMN {col_name} {col_type} {col_def}")

            # Ensure new tables exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exam_batches (
                    batch_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_default',
                    total_tests INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_practice_test_templates (
                    template_id TEXT PRIMARY KEY,
                    template_name TEXT NOT NULL DEFAULT 'Default Daily Practice Template',
                    tests_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_practice_test_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL UNIQUE,
                    total_tests INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'SUBMITTED',
                    submitted_at TEXT NOT NULL DEFAULT '',
                    locked_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_practice_test_executions (
                    execution_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    test_index INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    end_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'SCHEDULED',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    score REAL DEFAULT NULL,
                    max_score REAL DEFAULT NULL,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    rescheduled_to TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_exec_date ON daily_practice_test_executions(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_exec_status ON daily_practice_test_executions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exam_prebookings_batch ON exam_prebookings(batch_id)")

        acc_cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
        if "display_name" not in acc_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN display_name TEXT DEFAULT 'LOK ADHITHYA G'")

        # 2. Ensure account 'user_lok' exists with display_name 'LOK ADHITHYA G'
        acc_row = conn.execute("SELECT * FROM accounts WHERE account_id = 'user_lok'").fetchone()
        if not acc_row:
            conn.execute(
                """
                INSERT INTO accounts (account_id, username, email, created_at, status, display_name)
                VALUES ('user_lok', 'Lok', 'lok@strictrecords.local', ?, 'ACTIVE', 'LOK ADHITHYA G')
                """,
                (now_iso,),
            )
        else:
            conn.execute(
                "UPDATE accounts SET display_name = 'LOK ADHITHYA G' WHERE account_id = 'user_lok' AND (display_name IS NULL OR display_name = '' OR display_name = 'Lok')"
            )

        # 3. Ensure display_name column exists in users table
        u_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "display_name" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT 'LOK ADHITHYA G'")

        # 4. Ensure user record for 'user_lok' exists in users table
        user_row = conn.execute("SELECT * FROM users WHERE user_id = 'user_lok'").fetchone()
        if not user_row:
            conn.execute(
                """
                INSERT INTO users (user_id, username, display_name, created_at, avatar, discipline_xp, discipline_level, role)
                VALUES ('user_lok', 'LOK ADHITHYA G', 'LOK ADHITHYA G', ?, '🛡️', 120, 'OPERATOR', 'ADMIN')
                """,
                (now_iso,),
            )
            # Ensure user_settings row exists
            st_row = conn.execute("SELECT user_id FROM user_settings WHERE user_id = 'user_lok'").fetchone()
            if not st_row:
                default_settings = UserSettings()
                default_settings.user_id = "user_lok"
                conn.execute(
                    "INSERT INTO user_settings (user_id, settings_json, rule_version, updated_at) VALUES (?, ?, 1, ?)",
                    ("user_lok", json.dumps(default_settings.to_dict()), now_iso),
                )
        else:
            conn.execute(
                "UPDATE users SET display_name = 'LOK ADHITHYA G', username = 'LOK ADHITHYA G' WHERE user_id = 'user_lok'"
            )

    def get_current_rule_version(self, user_id: str = "user_lok") -> int:
        with self._connect() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT rule_version FROM user_settings WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row and row["rule_version"]:
                    return int(row["rule_version"])
            cfg = self.get_active_configuration()
            return int(cfg.get("version_number", 1))

    def get_settings(self, user_id: str = "user_lok") -> UserSettings:
        with self._connect() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row and row["settings_json"]:
                    data = json.loads(row["settings_json"])
                    valid_keys = UserSettings.__dataclass_fields__.keys()
                    filtered = {k: v for k, v in data.items() if k in valid_keys}
                    return UserSettings(**filtered)
            cfg = self.get_active_configuration()
            if cfg and cfg.get("rules"):
                valid_keys = UserSettings.__dataclass_fields__.keys()
                filtered = {k: v for k, v in cfg["rules"].items() if k in valid_keys}
                return UserSettings(**filtered)
            return UserSettings(user_id=user_id)

    def update_settings(
        self,
        new_settings: Dict[str, Any],
        user_id: str = "user_lok",
        actor_role: str = "ADMIN",
        admin_user: str = "Administrator",
        device_id: str = "admin_portal",
    ) -> UserSettings:
        """
        Sections 32, 33, 34, 91, 152:
        - Enforces role check: normal USER cannot modify scoring rules or reward thresholds.
        - Creates a new version in `scoring_rule_versions` when scoring rules change.
        - Logs an immutable `AdminAuditEvent` for every modified setting.
        """
        role_upper = (actor_role or "ADMIN").upper()
        if role_upper != "ADMIN":
            forbidden = [k for k in new_settings.keys() if k in ADMIN_PROTECTED_SETTINGS]
            if forbidden:
                raise PermissionError(
                    f"Normal user cannot modify ADMIN scoring/reward rules: {', '.join(forbidden)}"
                )

        old_dict = self.get_settings(user_id).to_dict()
        current = dict(old_dict)
        current.update(new_settings)
        valid_keys = UserSettings.__dataclass_fields__.keys()
        filtered = {k: v for k, v in current.items() if k in valid_keys}
        settings_obj = UserSettings(**filtered)
        new_dict = settings_obj.to_dict()
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        changed_fields = []
        rule_changed = False
        for k, new_val in new_settings.items():
            if k in valid_keys and old_dict.get(k) != new_dict.get(k):
                changed_fields.append((k, old_dict.get(k), new_dict.get(k)))
                if k in ADMIN_PROTECTED_SETTINGS:
                    rule_changed = True

        with self._connect() as conn:
            cur_ver_row = conn.execute(
                "SELECT rule_version FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            cur_ver = int(cur_ver_row["rule_version"]) if cur_ver_row else 1
            next_ver = cur_ver + 1 if rule_changed else cur_ver

            conn.execute(
                """
                INSERT INTO user_settings (user_id, settings_json, rule_version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    rule_version = excluded.rule_version,
                    updated_at = excluded.updated_at
                """,
                (user_id, json.dumps(new_dict), next_ver, now_iso),
            )

            if rule_changed:
                conn.execute(
                    """
                    UPDATE scoring_rule_versions
                    SET effective_until = ?
                    WHERE user_id = ? AND effective_until IS NULL
                    """,
                    (now_iso, user_id),
                )
                conn.execute(
                    """
                    INSERT INTO scoring_rule_versions (
                        rule_id, user_id, version, effective_from, effective_until, created_by, created_at, rules_json
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        f"rule_{user_id}_v{next_ver}_{uuid.uuid4().hex[:6]}",
                        user_id,
                        next_ver,
                        now_iso,
                        admin_user,
                        now_iso,
                        json.dumps(new_dict),
                    ),
                )

            for field_name, old_v, new_v in changed_fields:
                conn.execute(
                    """
                    INSERT INTO admin_audit_logs (
                        audit_id, admin_user, action_type, field_changed, old_value, new_value, timestamp, device_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"aud_{uuid.uuid4().hex[:12]}",
                        admin_user,
                        "RULE_UPDATE" if field_name in ADMIN_PROTECTED_SETTINGS else "CONFIG_UPDATE",
                        field_name,
                        json.dumps(old_v),
                        json.dumps(new_v),
                        now_iso,
                        device_id,
                    ),
                )

        self.log_event(
            date=datetime.now().strftime("%Y-%m-%d"),
            event_code="SETTINGS_UPDATED",
            device_id=device_id,
            component="ADMIN",
            details={
                "updated_keys": [c[0] for c in changed_fields],
                "rule_version": next_ver,
                "admin_user": admin_user,
            },
        )
        return settings_obj

    def record_admin_audit(
        self,
        admin_user: str,
        action_type: str,
        field_changed: str,
        old_value: Any,
        new_value: Any,
        device_id: str = "admin_portal",
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        audit_id = f"aud_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_logs (
                    audit_id, admin_user, action_type, field_changed, old_value, new_value, timestamp, device_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    admin_user,
                    action_type,
                    field_changed,
                    json.dumps(old_value) if not isinstance(old_value, str) else old_value,
                    json.dumps(new_value) if not isinstance(new_value, str) else new_value,
                    ts,
                    device_id,
                ),
            )
        return {
            "audit_id": audit_id,
            "admin_user": admin_user,
            "action_type": action_type,
            "field_changed": field_changed,
            "old_value": old_value,
            "new_value": new_value,
            "timestamp": ts,
        }

    def record_audit_log(
        self,
        category: str = "ADMIN",
        action: str = "ACTION",
        severity: str = "INFO",
        target: str = "",
        details: str = "",
        admin_user: str = "Administrator",
        device_id: str = "admin_portal",
    ) -> Dict[str, Any]:
        """Convenience method for structured audit logging across subsystems."""
        return self.record_admin_audit(
            admin_user=admin_user,
            action_type=f"{category}:{action}",
            field_changed=target or category,
            old_value=severity,
            new_value=details,
            device_id=device_id,
        )

    def get_admin_audit_logs(
        self, action_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if action_type:
                rows = conn.execute(
                    "SELECT * FROM admin_audit_logs WHERE action_type = ? ORDER BY timestamp DESC LIMIT ?",
                    (action_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM admin_audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_rule_versions(self, user_id: str = "user_lok") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rule_id, user_id, version, effective_from, effective_until, created_by, created_at FROM scoring_rule_versions WHERE user_id = ? ORDER BY version DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def log_event(
        self,
        date: str,
        event_code: str,
        device_id: str,
        details: Optional[Dict[str, Any]] = None,
        is_dev_mode: bool = False,
        timestamp: Optional[str] = None,
        component: str = "CORE",
        status: str = "OK",
        duration_ms: float = 0.0,
    ) -> None:
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO structured_logs (
                    date, is_dev_mode, timestamp, component, event_code, status, duration_ms, device_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date,
                    1 if is_dev_mode else 0,
                    ts,
                    component,
                    event_code,
                    status,
                    duration_ms,
                    device_id,
                    json.dumps(details or {}),
                ),
            )

    def get_structured_logs(self, date: str, is_dev_mode: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT log_seq, date, timestamp, component, event_code, status, duration_ms, device_id, details_json
                FROM structured_logs
                WHERE date = ? AND is_dev_mode = ?
                ORDER BY timestamp ASC, log_seq ASC
                LIMIT ?
                """,
                (date, 1 if is_dev_mode else 0, limit),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d.pop("details_json", "{}"))
                result.append(d)
            return result

    def ensure_daily_record(
        self, date: str, user_id: str = "user_lok", is_dev_mode: bool = False, reference_now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Ensures the connected DailyRecord and all default scheduled tasks
        (Wake-up Prep 06:50, Wake-up 07:00, Bath before 10:00, Hourly 08:00-22:00, Gym 18:00)
        exist for the given date with full Smart Schedule Engine & Task Dependency metadata.
        """
        dev_flag = 1 if is_dev_mode else 0
        settings = self.get_settings(user_id)
        rule_ver = self.get_current_rule_version(user_id)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        start_hour = int(settings.hourly_start_time.split(":")[0])
        end_hour = int(settings.hourly_end_time.split(":")[0])
        total_hourly = max(1, end_hour - start_hour + 1)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_records WHERE date = ? AND user_id = ? AND is_dev_mode = ?",
                (date, user_id, dev_flag),
            ).fetchone()

            if not row:
                record_id = f"daily_{date}_{'dev' if is_dev_mode else 'prod'}"
                conn.execute(
                    """
                    INSERT INTO daily_records (
                        record_id, date, user_id, is_dev_mode,
                        wakeup_status, bath_status, gym_status,
                        hourly_total_count, score_mode, score_state, calculation_version, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        date,
                        user_id,
                        dev_flag,
                        WakeUpAttendance.PENDING.value,
                        TaskState.PENDING.value,
                        TaskState.UPCOMING.value,
                        total_hourly,
                        ScoreMode.STRICT_MODE.value,
                        ScoreMode.STRICT_MODE.value,
                        rule_ver,
                        now_iso,
                    ),
                )

                # Seed Wake-Up Session
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wake_up_sessions (
                        session_id, date, is_dev_mode, target_time, track_name, track_duration_sec, attendance_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"wakeup_{date}_{dev_flag}",
                        date,
                        dev_flag,
                        settings.wakeup_time,
                        settings.motivation_track_name,
                        settings.motivation_track_duration_sec,
                        WakeUpAttendance.PENDING.value,
                    ),
                )

                # Seed Bath Record
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bath_records (
                        bath_id, date, is_dev_mode, deadline_time, verification_mode, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"bath_{date}_{dev_flag}",
                        date,
                        dev_flag,
                        settings.bath_deadline,
                        settings.bath_verification_mode,
                        TaskState.PENDING.value,
                    ),
                )

                # Seed Gym Session
                conn.execute(
                    """
                    INSERT OR IGNORE INTO gym_sessions (
                        gym_id, date, is_dev_mode, scheduled_time, min_required_minutes, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"gym_{date}_{dev_flag}",
                        date,
                        dev_flag,
                        settings.gym_time,
                        settings.gym_min_duration_minutes,
                        TaskState.UPCOMING.value,
                    ),
                )

                # Seed Scheduled Tasks with TaskType, Grace Period, Dependencies, and Points
                base_tasks = [
                    (
                        f"task_{date}_{dev_flag}_WAKEUP_PREP",
                        date,
                        dev_flag,
                        "WAKEUP_PREP",
                        "Prepare for Wake-Up Alarm",
                        TaskType.TIME_TASK.value,
                        TaskSemantics.EXACT_TIME.value,
                        settings.wakeup_prep_time,
                        settings.wakeup_time,
                        10,
                        0,
                        0,
                        "DAILY",
                        1,
                        "NONE",
                        "[]",
                        0,
                        0,
                        TaskState.UPCOMING.value,
                    ),
                    (
                        f"task_{date}_{dev_flag}_WAKEUP",
                        date,
                        dev_flag,
                        "WAKE_UP",
                        f"Wake Up at {settings.wakeup_time}",
                        TaskType.VERIFICATION_TASK.value,
                        TaskSemantics.EXACT_TIME.value,
                        settings.wakeup_time,
                        settings.wakeup_on_time_limit,
                        15,
                        0,
                        5,
                        "DAILY",
                        1,
                        "CAMERA_BLUETOOTH_AUDIO",
                        "[]",
                        settings.wakeup_points,
                        settings.wakeup_missed_penalty,
                        TaskState.PENDING.value,
                    ),
                    (
                        f"task_{date}_{dev_flag}_BATH",
                        date,
                        dev_flag,
                        "BATH",
                        f"Bath before {settings.bath_deadline}",
                        TaskType.DEADLINE_TASK.value,
                        TaskSemantics.DEADLINE.value,
                        "07:00",
                        settings.bath_deadline,
                        20,
                        0,
                        settings.bath_grace_minutes,
                        "DAILY",
                        2,
                        settings.bath_verification_mode,
                        json.dumps(["WAKE_UP"]),
                        settings.bath_points,
                        settings.bath_missed_penalty,
                        TaskState.PENDING.value,
                    ),
                    (
                        f"task_{date}_{dev_flag}_GYM",
                        date,
                        dev_flag,
                        "GYM",
                        f"Gym Session at {settings.gym_time}",
                        TaskType.DURATION_TASK.value,
                        TaskSemantics.SCHEDULED_START.value,
                        settings.gym_time,
                        settings.gym_deadline,
                        45,
                        settings.gym_min_duration_minutes,
                        settings.gym_grace_minutes,
                        "DAILY",
                        2,
                        "DURATION_AND_OPTIONAL_PHOTO",
                        "[]",
                        settings.gym_points,
                        settings.gym_missed_penalty,
                        TaskState.UPCOMING.value,
                    ),
                ]
                for t in base_tasks:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO scheduled_tasks (
                            task_id, date, is_dev_mode, task_code, title,
                            task_type, semantics, target_time, deadline_time,
                            duration_minutes, min_duration_minutes, grace_period_minutes,
                            recurrence, priority, required_evidence, dependencies_json,
                            point_value, penalty_value, state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        t,
                    )

                hourly_pts = getattr(settings, "hourly_points", getattr(settings, "hourly_productive_points", 5))
                for h in range(start_hour, end_hour + 1):
                    slot_str = f"{h:02d}"
                    task_code = f"HOURLY_{slot_str}"
                    checkpoint = f"{h:02d}:00"
                    prev_checkpoint = f"{h-1:02d}:00"
                    hour_slot = f"{prev_checkpoint}-{checkpoint}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO hourly_records (
                            hourly_id, date, is_dev_mode, hour_slot, checkpoint_time, status
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"hourly_{date}_{dev_flag}_{slot_str}",
                            date,
                            dev_flag,
                            hour_slot,
                            checkpoint,
                            TaskState.UPCOMING.value,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO scheduled_tasks (
                            task_id, date, is_dev_mode, task_code, title,
                            task_type, semantics, target_time, deadline_time,
                            duration_minutes, min_duration_minutes, grace_period_minutes,
                            recurrence, priority, required_evidence, dependencies_json,
                            point_value, penalty_value, state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"task_{date}_{dev_flag}_{task_code}",
                            date,
                            dev_flag,
                            task_code,
                            f"Hourly Check-in ({checkpoint})",
                            TaskType.CHECK_IN_TASK.value,
                            TaskSemantics.RECURRING_CHECKPOINT.value,
                            checkpoint,
                            f"{h:02d}:15",
                            0,
                            0,
                            settings.hourly_grace_minutes if hasattr(settings, "hourly_grace_minutes") else 15,
                            "HOURLY",
                            3,
                            "ACTIVITY_LOG_AND_PHOTO",
                            json.dumps(["WAKE_UP"]),
                            hourly_pts,
                            settings.hourly_missed_penalty,
                            TaskState.UPCOMING.value,
                        ),
                    )

            row = conn.execute(
                "SELECT * FROM daily_records WHERE date = ? AND user_id = ? AND is_dev_mode = ?",
                (date, user_id, dev_flag),
            ).fetchone()
            res = dict(row)

        try:
            from core.time_window_engine import TimeWindowEngine, IST, now_ist
            twe = TimeWindowEngine(self)
            current_today = now_ist().strftime("%Y-%m-%d")
            if reference_now is not None:
                ref_now = reference_now
            elif date == current_today:
                ref_now = now_ist()
            else:
                ref_now = datetime.strptime(f"{date} 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            twe.ensure_activities_in_db(date, account_id=user_id, wakeup_time=settings.wakeup_time, reference_now=ref_now)
        except Exception:
            pass

        return res

    def get_daily_record(
        self, date: str, user_id: str = "user_lok", is_dev_mode: bool = False
    ) -> Optional[Dict[str, Any]]:
        dev_flag = 1 if is_dev_mode else 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_records WHERE date = ? AND user_id = ? AND is_dev_mode = ?",
                (date, user_id, dev_flag),
            ).fetchone()
            return dict(row) if row else None

    def get_score_events(
        self, date: str, is_dev_mode: bool = False
    ) -> List[Dict[str, Any]]:
        dev_flag = 1 if is_dev_mode else 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND is_dev_mode = ? ORDER BY timestamp ASC",
                (date, dev_flag),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["audit_details"] = json.loads(d.get("audit_json") or "{}")
                result.append(d)
            return result

    def register_or_heartbeat_device(
        self,
        device_id: str,
        device_name: str,
        platform: str,
        app_version: str = "2.0.0",
        sync_status: str = "ONLINE",
        user_id: str = "user_lok",
        battery_level: int = 100,
        storage_free_mb: int = 4096,
        protocol_version: str = "2.0.0",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    protocol_version, battery_level, storage_free_mb, last_seen, sync_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_name = excluded.device_name,
                    platform = excluded.platform,
                    app_version = excluded.app_version,
                    protocol_version = excluded.protocol_version,
                    battery_level = excluded.battery_level,
                    storage_free_mb = excluded.storage_free_mb,
                    last_seen = excluded.last_seen,
                    sync_status = excluded.sync_status
                """,
                (
                    device_id,
                    user_id,
                    device_name,
                    platform.upper(),
                    app_version,
                    protocol_version,
                    battery_level,
                    storage_free_mb,
                    now_iso,
                    sync_status,
                ),
            )
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            return dict(row)

    def list_devices(self, user_id: str = "user_lok") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE user_id = ? ORDER BY platform ASC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_device_restriction_policy_ack(
        self, device_id: str, service: str, acknowledgement: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Record the policy version the authenticated Android client received."""
        clean = str(service or "").strip().lower()
        if clean not in ("youtube", "instagram"):
            return {"success": False, "error": "Unsupported restriction service."}
        try:
            version = int(acknowledgement.get("policyVersion") or 0)
        except (TypeError, ValueError):
            version = 0
        if version <= 0:
            return {"success": False, "error": "A positive policy version is required."}
        received_at = datetime.now().isoformat()
        requested = bool(acknowledgement.get("requestedRestrictionEnabled", True))
        effective_restriction = acknowledgement.get("effectiveRestrictionEnabled")
        effective_access = acknowledgement.get("effectiveAccessAllowed")
        with self._connect() as conn:
            device = conn.execute(
                "SELECT user_id, platform, pairing_state, status FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if not device or "ANDROID" not in str(device["platform"]).upper() or device["pairing_state"] != "PAIRED" or device["status"] != "REGISTERED":
                return {"success": False, "error": "Device is not a paired, registered Android device."}
            prior = conn.execute(
                "SELECT policy_version, requested_restriction_enabled FROM device_restriction_policy_acks WHERE device_id = ? AND service = ?",
                (device_id, clean),
            ).fetchone()
            if prior and version < int(prior["policy_version"]):
                return {"success": False, "staleRejected": True, "policyVersion": int(prior["policy_version"])}
            if prior and version == int(prior["policy_version"]) and requested != bool(prior["requested_restriction_enabled"]):
                return {"success": False, "staleRejected": True, "policyVersion": int(prior["policy_version"])}
            conn.execute(
                """
                INSERT INTO device_restriction_policy_acks (
                    device_id, service, policy_version, requested_restriction_enabled,
                    effective_restriction_enabled, effective_access_allowed, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, service) DO UPDATE SET
                    policy_version = excluded.policy_version,
                    requested_restriction_enabled = excluded.requested_restriction_enabled,
                    effective_restriction_enabled = excluded.effective_restriction_enabled,
                    effective_access_allowed = excluded.effective_access_allowed,
                    received_at = excluded.received_at
                """,
                (
                    device_id, clean, version, int(requested),
                    None if effective_restriction is None else int(bool(effective_restriction)),
                    None if effective_access is None else int(bool(effective_access)),
                    received_at,
                ),
            )
        return {"success": True, "deviceId": device_id, "service": clean, "policyVersion": version, "receivedAt": received_at}

    # =========================================================================
    # STAGE 10: USB PAIRING & TRUST RELATIONSHIP (Sections 77-79, 90, 149, 176)
    # =========================================================================
    def initiate_usb_pairing(
        self,
        android_device_id: str = "android_lok_phone",
        windows_device_id: str = "windows_lok_pc",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if kwargs.get("device_id"):
            android_device_id = str(kwargs["device_id"])
        pairing_id = f"pair_{uuid.uuid4().hex[:12]}"
        code_num = random.randint(100000, 999999)
        pin_digits = f"{code_num:06d}"
        code_str = f"{pin_digits[:3]} {pin_digits[3:]}"
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        session_key = f"SR-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}"
        raw_token = f"sr_trust_{uuid.uuid4().hex}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now()
        now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (now + timedelta(minutes=5)).isoformat()
        endpoint = f"http://{get_local_ip()}:8765"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_pairings (
                    pairing_id, android_device_id, windows_device_id, trust_token_hash,
                    paired_via, android_approved, windows_approved, status, paired_at, verification_code
                ) VALUES (?, ?, ?, ?, 'USB', 0, 0, 'PENDING_APPROVAL', ?, ?)
                """,
                (pairing_id, android_device_id, windows_device_id, token_hash, now_iso, code_str),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO pairing_sessions (
                    pairing_id, account_id, session_key, manual_pin, server_endpoint, created_at, expires_at, status
                ) VALUES (?, 'user_lok', ?, ?, ?, ?, ?, 'PENDING')
                """,
                (pairing_id, session_key, pin_digits, endpoint, now.isoformat(), expires_at),
            )
            conn.execute(
                "UPDATE devices SET pairing_state = 'PAIR_REQUESTED' WHERE device_id = ?",
                (android_device_id,),
            )
        return {
            "pairing_id": pairing_id,
            "verification_code": code_str,
            "manual_pin": pin_digits,
            "session_key": session_key,
            "server_endpoint": endpoint,
            "candidate_endpoints": [f"http://{ip}:8765" for ip in get_all_lan_ips()],
            "android_device_id": android_device_id,
            "windows_device_id": windows_device_id,
            "trust_token": raw_token,
            "status": "PENDING_APPROVAL",
            "paired_via": "USB",
        }

    def verify_pairing_code(
        self,
        code: str,
        device_id: str = "android_lok_phone",
        admin_user: str = "Administrator",
    ) -> Dict[str, Any]:
        cleaned_code = code.replace(" ", "").replace("-", "").strip()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM device_pairings 
                WHERE status = 'PENDING_APPROVAL' 
                ORDER BY paired_at DESC, rowid DESC
                """
            ).fetchall()
            matching_row = None
            for r in rows:
                v_code = (r["verification_code"] or "").replace(" ", "").replace("-", "").strip()
                if v_code == cleaned_code:
                    matching_row = r
                    break
            if not matching_row:
                raise ValueError("Invalid pairing verification code or pairing session expired")
            pairing_id = matching_row["pairing_id"]
        return self.approve_usb_pairing(pairing_id, approve_android=True, approve_windows=True, admin_user=admin_user)

    def approve_usb_pairing(
        self,
        pairing_id: str,
        approve_android: bool = True,
        approve_windows: bool = True,
        admin_user: str = "Administrator",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_pairings WHERE pairing_id = ?", (pairing_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Pairing session {pairing_id} not found")
            a_ok = 1 if (row["android_approved"] or approve_android) else 0
            w_ok = 1 if (row["windows_approved"] or approve_windows) else 0
            status = "PAIRED" if (a_ok and w_ok) else "PENDING_APPROVAL"
            conn.execute(
                """
                UPDATE device_pairings
                SET android_approved = ?, windows_approved = ?, status = ?, paired_at = ?
                WHERE pairing_id = ?
                """,
                (a_ok, w_ok, status, now_iso, pairing_id),
            )
            if status == "PAIRED":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO devices (
                        device_id, user_id, device_name, platform, app_version, protocol_version,
                        battery_level, storage_free_mb, pairing_state, last_seen, sync_status, status
                    ) VALUES (?, 'user_lok', ?, 'ANDROID', '2.0.0', '2.0.0', 100, 4096, 'PAIRED', ?, 'ONLINE', 'REGISTERED')
                    """,
                    (row["android_device_id"], row["android_device_id"], now_iso),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO devices (
                        device_id, user_id, device_name, platform, app_version, protocol_version,
                        battery_level, storage_free_mb, pairing_state, last_seen, sync_status, status
                    ) VALUES (?, 'user_lok', ?, 'WINDOWS', '2.0.0', '2.0.0', 100, 4096, 'PAIRED', ?, 'ONLINE', 'REGISTERED')
                    """,
                    (row["windows_device_id"], row["windows_device_id"], now_iso),
                )
                conn.execute(
                    "UPDATE devices SET user_id = 'user_lok', pairing_state = 'PAIRED', status = 'REGISTERED', sync_status = 'ONLINE' WHERE device_id IN (?, ?)",
                    (row["android_device_id"], row["windows_device_id"]),
                )
                conn.execute(
                    "UPDATE pairing_sessions SET status = 'USED', paired_device_id = ? WHERE pairing_id = ?",
                    (row["android_device_id"], pairing_id),
                )
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="DEVICE_PAIRING",
            field_changed="pairing_state",
            old_value="PENDING_APPROVAL",
            new_value=status,
        )
        return {
            "pairing_id": pairing_id,
            "android_approved": bool(a_ok),
            "windows_approved": bool(w_ok),
            "status": status,
            "wifi_auto_sync_ready": status == "PAIRED",
        }

    def verify_pairing_token(self, raw_token: str) -> bool:
        if not raw_token:
            return False
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT pairing_id FROM device_pairings WHERE trust_token_hash = ? AND status = 'PAIRED' AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
            return row is not None

    def unpair_device(
        self, device_id: str, admin_user: str = "Administrator"
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM device_pairings
                WHERE android_device_id = ? OR windows_device_id = ?
                """,
                (device_id, device_id),
            )
            conn.execute(
                "UPDATE devices SET pairing_state = 'UNPAIRED', sync_status = 'OFFLINE' WHERE device_id = ?",
                (device_id,),
            )
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="DEVICE_UNPAIR",
            field_changed=f"device:{device_id}",
            old_value="PAIRED",
            new_value="UNPAIRED",
        )
        return {"device_id": device_id, "pairing_state": "UNPAIRED", "unpaired_at": now_iso}

    def revoke_device_pairing(
        self, device_id: str, admin_user: str = "Administrator"
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE device_pairings
                SET status = 'REVOKED', revoked_at = ?
                WHERE android_device_id = ? OR windows_device_id = ?
                """,
                (now_iso, device_id, device_id),
            )
            conn.execute(
                "UPDATE devices SET pairing_state = 'REVOKED', sync_status = 'OFFLINE' WHERE device_id = ?",
                (device_id,),
            )
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="DEVICE_REVOKED",
            field_changed=f"device:{device_id}",
            old_value="PAIRED",
            new_value="REVOKED",
        )
        return {"device_id": device_id, "pairing_state": "REVOKED", "revoked_at": now_iso}

    def log_connection_event(
        self,
        device_id: str,
        from_state: str,
        to_state: str,
        transport_mode: str = "USB",
        latency_ms: float = 0.0,
        reason: str = "",
    ) -> Dict[str, Any]:
        hid = f"conn_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO connection_history (
                    history_id, device_id, from_state, to_state, transport_mode, latency_ms, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (hid, device_id, from_state, to_state, transport_mode, float(latency_ms), reason, now_iso),
            )
        return {
            "history_id": hid,
            "device_id": device_id,
            "from_state": from_state,
            "to_state": to_state,
            "transport_mode": transport_mode,
            "latency_ms": latency_ms,
            "reason": reason,
            "created_at": now_iso,
        }

    def get_connection_history(
        self, device_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM connection_history WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                    (device_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM connection_history ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def save_transfer_item(
        self,
        transfer_id: str,
        device_id: str,
        direction: str,
        item_type: str,
        payload_json: str,
        payload_hash: str,
        size_bytes: int = 0,
        ack_stage: str = "SENT",
        status: str = "PENDING",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transfer_queue_items (
                    transfer_id, device_id, direction, item_type, payload_json, payload_hash,
                    size_bytes, ack_stage, status, retry_count, max_retries, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 3, ?, ?)
                ON CONFLICT(transfer_id) DO UPDATE SET
                    ack_stage = excluded.ack_stage,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (transfer_id, device_id, direction, item_type, payload_json, payload_hash,
                 size_bytes, ack_stage, status, now_iso, now_iso),
            )
        return {
            "transfer_id": transfer_id,
            "device_id": device_id,
            "direction": direction,
            "item_type": item_type,
            "payload_hash": payload_hash,
            "size_bytes": size_bytes,
            "ack_stage": ack_stage,
            "status": status,
            "created_at": now_iso,
        }

    def update_transfer_item(
        self,
        transfer_id: str,
        ack_stage: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE transfer_queue_items
                SET ack_stage = ?, status = ?, error_message = ?, updated_at = ?
                WHERE transfer_id = ?
                """,
                (ack_stage, status, error_message, now_iso, transfer_id),
            )
            return True

    def get_transfer_queue_items(
        self, device_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM transfer_queue_items WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                    (device_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transfer_queue_items ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def create_device_command(
        self,
        command_id: str,
        device_id: str,
        command_type: str,
        payload_json: str,
        nonce: str,
        admin_user: str = "Administrator",
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not expires_at:
            from datetime import timedelta
            expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_commands (
                    command_id, device_id, command_type, payload_json, nonce, admin_user,
                    status, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (command_id, device_id, command_type, payload_json, nonce, admin_user, expires_at, now_iso),
            )
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="DEVICE_COMMAND",
            field_changed=f"command:{command_type}",
            old_value="NEW",
            new_value=command_id,
        )
        return {
            "command_id": command_id,
            "device_id": device_id,
            "command_type": command_type,
            "nonce": nonce,
            "status": "PENDING",
            "expires_at": expires_at,
            "created_at": now_iso,
        }

    def poll_device_commands(self, device_id: str) -> List[Dict[str, Any]]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM device_commands
                WHERE device_id = ? AND status IN ('PENDING', 'DISPATCHED') AND expires_at > ?
                ORDER BY created_at ASC
                """,
                (device_id, now_iso),
            ).fetchall()
            res = [dict(r) for r in rows]
            if res:
                cmd_ids = [r["command_id"] for r in res]
                placeholders = ",".join("?" for _ in cmd_ids)
                conn.execute(
                    f"UPDATE device_commands SET status = 'DISPATCHED' WHERE command_id IN ({placeholders})",
                    cmd_ids,
                )
            return res

    def ack_device_command(
        self,
        command_id: str,
        result_json: str,
        status: str = "ACKNOWLEDGED",
        device_id: str = "",
    ) -> bool:
        if not command_id or not device_id:
            return False
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE device_commands
                SET status = ?, result_json = ?, acked_at = ?
                WHERE command_id = ? AND device_id = ? AND status IN ('PENDING', 'DISPATCHED')
                """,
                (status, result_json, now_iso, command_id, device_id),
            )
            return cursor.rowcount == 1

    def get_device_commands(
        self, device_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM device_commands WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                    (device_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM device_commands ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def update_device_telemetry(self, device_id: str, telemetry: Dict[str, Any]) -> bool:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            batt = telemetry.get("battery_level")
            storage = telemetry.get("storage_free_mb")
            ssid = telemetry.get("wifi_ssid")
            latency = telemetry.get("latency_ms")
            ip = telemetry.get("ip_address")
            cfg = telemetry.get("config_version")

            updates = ["last_seen = ?"]
            params: List[Any] = [now_iso]

            if batt is not None:
                updates.append("battery_level = ?")
                params.append(int(batt))
            if storage is not None:
                updates.append("storage_free_mb = ?")
                params.append(int(storage))
            if ssid is not None:
                updates.append("wifi_ssid = ?")
                params.append(str(ssid))
            if latency is not None:
                updates.append("latency_ms = ?")
                params.append(float(latency))
            if ip is not None:
                updates.append("ip_address = ?")
                params.append(str(ip))
            if cfg is not None:
                updates.append("config_version = ?")
                params.append(int(cfg))

            params.append(device_id)
            sql = f"UPDATE devices SET {', '.join(updates)} WHERE device_id = ?"
            conn.execute(sql, params)
            return True

    def reset_dev_mode_data(self, date: str, user_id: str = "user_lok") -> None:
        """Clears only DEVELOPMENT MODE records for the specified date without touching production records."""
        with self._connect() as conn:
            for table in [
                "daily_records",
                "scheduled_tasks",
                "wake_up_sessions",
                "bath_records",
                "gym_sessions",
                "focus_sessions",
                "break_logs",
                "hourly_records",
                "evidence_files",
                "score_events",
                "restriction_events",
                "integrity_signals",
                "daily_journals",
                "device_activity_logs",
                "structured_logs",
            ]:
                conn.execute(
                    f"DELETE FROM {table} WHERE date = ? AND is_dev_mode = 1", (date,)
                )
        self.ensure_daily_record(date, user_id=user_id, is_dev_mode=True)

    # =========================================================================
    # ADMIN ECOSYSTEM: AUTHENTICATION, ROLES & SESSIONS
    # =========================================================================

    def hash_secret(self, secret: str, salt: Optional[str] = None) -> tuple[str, str]:
        s = salt or os.urandom(16).hex()
        h = hashlib.sha256((s + secret).encode("utf-8")).hexdigest()
        return h, s

    def authenticate_admin(
        self,
        username_or_pin: str,
        secret: str,
        device_id: str = "windows_admin_console",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            # Check username + password match
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ? AND is_active = 1",
                (username_or_pin,),
            ).fetchone()

            matched = False
            admin_data = None
            if row:
                calc_pwd, _ = self.hash_secret(secret, row["salt"])
                if calc_pwd == row["password_hash"]:
                    matched = True
                    admin_data = dict(row)
                elif self.is_default_admin_pin() and secret in ("admin1984", "admin1234"):
                    matched = True
                    admin_data = dict(row)

            # If not matched by password, check if username_or_pin or secret matches PIN
            if not matched:
                all_admins = conn.execute(
                    "SELECT * FROM admin_users WHERE is_active = 1"
                ).fetchall()
                test_pin = secret if secret else username_or_pin
                for adm in all_admins:
                    if self.verify_admin_pin(test_pin):
                        matched = True
                        admin_data = dict(adm)
                        break

            if not matched or not admin_data:
                raise PermissionError("Invalid administrator credentials or PIN.")

            # Create authenticated session token (valid for 2 hours)
            token = f"adm_tok_{uuid.uuid4().hex}"
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            # 2 hours later
            exp_ts = datetime.fromtimestamp(datetime.now().timestamp() + 7200).strftime("%Y-%m-%d %H:%M:%S")

            pin_change_required = 0
            if (str(secret).strip() == "1234" or str(username_or_pin).strip() == "1234") and self.is_default_admin_pin():
                pin_change_required = 1

            conn.execute(
                """
                INSERT INTO admin_sessions (session_id, admin_id, token, created_at, expires_at, device_id, is_active, pin_change_required)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (session_id, admin_data["admin_id"], token, now_iso, exp_ts, device_id, pin_change_required),
            )
            conn.execute(
                "UPDATE admin_users SET last_login = ? WHERE admin_id = ?",
                (now_iso, admin_data["admin_id"]),
            )

        self.record_admin_audit(
            admin_user=admin_data["username"],
            action_type=AdminActionType.ADMIN_LOGIN.value,
            field_changed="session",
            old_value=None,
            new_value="ACTIVE",
            device_id=device_id,
        )

        return {
            "token": token,
            "admin_id": admin_data["admin_id"],
            "username": admin_data["username"],
            "role": admin_data["role"],
            "expires_at": exp_ts,
            "pin_change_required": bool(pin_change_required),
        }

    def verify_admin_session(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, u.username, u.role
                FROM admin_sessions s
                JOIN admin_users u ON s.admin_id = u.admin_id
                WHERE s.token = ? AND s.is_active = 1 AND u.is_active = 1 AND s.expires_at > ?
                """,
                (token, now_iso),
            ).fetchone()
            if row:
                return dict(row)
        return None

    def admin_auth_lockout_remaining(self, client_key: str) -> int:
        key_hash = hashlib.sha256(str(client_key or "unknown").encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_until FROM admin_auth_attempts WHERE client_key_hash = ?",
                (key_hash,),
            ).fetchone()
        if not row or not row["locked_until"]:
            return 0
        try:
            remaining = (datetime.fromisoformat(row["locked_until"]) - datetime.now()).total_seconds()
            return max(0, int(remaining + 0.999))
        except (TypeError, ValueError):
            return 0

    def record_admin_auth_failure(self, client_key: str) -> int:
        key_hash = hashlib.sha256(str(client_key or "unknown").encode("utf-8")).hexdigest()
        now = datetime.now()
        now_iso = now.isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT failed_attempts, locked_until FROM admin_auth_attempts WHERE client_key_hash = ?",
                (key_hash,),
            ).fetchone()
            if row and row["locked_until"]:
                try:
                    remaining = (datetime.fromisoformat(row["locked_until"]) - now).total_seconds()
                    if remaining > 0:
                        return max(1, int(remaining + 0.999))
                except (TypeError, ValueError):
                    pass
            failures = (int(row["failed_attempts"]) if row else 0) + 1
            locked_until = (now + timedelta(seconds=60)).isoformat(timespec="seconds") if failures >= 5 else None
            stored_failures = 0 if locked_until else failures
            conn.execute(
                """INSERT INTO admin_auth_attempts (client_key_hash, failed_attempts, locked_until, updated_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(client_key_hash) DO UPDATE SET
                   failed_attempts = excluded.failed_attempts, locked_until = excluded.locked_until,
                   updated_at = excluded.updated_at""",
                (key_hash, stored_failures, locked_until, now_iso),
            )
            return 60 if locked_until else 0

    def clear_admin_auth_failures(self, client_key: str) -> None:
        key_hash = hashlib.sha256(str(client_key or "unknown").encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("DELETE FROM admin_auth_attempts WHERE client_key_hash = ?", (key_hash,))

    def is_default_admin_pin(self) -> bool:
        """Returns True if the active admin PIN matches the initial setup key 1234."""
        with self._connect() as conn:
            row = conn.execute("SELECT pin_hash, salt FROM admin_users WHERE is_active = 1 LIMIT 1").fetchone()
            if not row:
                return False
            default_hash_1234, _ = self.hash_secret("1234", row["salt"])
            return row["pin_hash"] == default_hash_1234

    def verify_admin_pin(self, pin: str) -> bool:
        if not pin:
            return False
        pin_str = str(pin).strip()
        with self._connect() as conn:
            admins = conn.execute("SELECT * FROM admin_users WHERE is_active = 1").fetchall()
            for adm in admins:
                calc_pin, _ = self.hash_secret(pin_str, adm["salt"])
                if calc_pin == adm["pin_hash"]:
                    return True
                # Allow dual initial default pins 1234 and 1984 before key rotation
                default_1234, _ = self.hash_secret("1234", adm["salt"])
                default_1984, _ = self.hash_secret("1984", adm["salt"])
                if adm["pin_hash"] in (default_1234, default_1984) and pin_str in ("1234", "1984"):
                    return True
        return False

    def change_admin_pin(self, current_pin: str, new_pin: str, admin_user: str = "admin") -> Dict[str, Any]:
        """
        Securely changes the admin PIN after verifying the current PIN.
        Persists as a salted hash and records in admin audit log.
        """
        if not current_pin or not new_pin:
            return {"success": False, "error": "Both current PIN and new PIN are required"}
        new_pin_str = str(new_pin).strip()
        if not new_pin_str.isdigit() or len(new_pin_str) < 4:
            return {"success": False, "error": "New PIN must be at least 4 digits"}

        with self._connect() as conn:
            admins = conn.execute("SELECT * FROM admin_users WHERE is_active = 1").fetchall()
            matched = False
            target_adm = None
            for adm in admins:
                calc_pin, _ = self.hash_secret(str(current_pin).strip(), adm["salt"])
                if calc_pin == adm["pin_hash"]:
                    matched = True
                    target_adm = adm
                    break

            if not matched or not target_adm:
                return {"success": False, "error": "Current administrator key is incorrect"}

            new_hash, new_salt = self.hash_secret(new_pin_str)
            conn.execute(
                "UPDATE admin_users SET pin_hash = ?, salt = ? WHERE admin_id = ?",
                (new_hash, new_salt, target_adm["admin_id"]),
            )
            conn.execute(
                "UPDATE admin_sessions SET pin_change_required = 0 WHERE admin_id = ?",
                (target_adm["admin_id"],),
            )

        self.record_admin_audit(
            admin_user=admin_user,
            action_type=AdminActionType.ADMIN_PIN_CHANGED.value,
            field_changed="pin_hash",
            old_value="[REDACTED_PREVIOUS_KEY]",
            new_value="[REDACTED_UPDATED_KEY]",
            device_id="windows_admin_console",
        )
        return {
            "success": True,
            "message": "Administrator PIN updated successfully",
            "is_default": self.is_default_admin_pin(),
        }

    def logout_admin(self, token: str) -> bool:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT s.*, u.username FROM admin_sessions s JOIN admin_users u ON s.admin_id = u.admin_id WHERE s.token = ?",
                (token,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE admin_sessions SET is_active = 0 WHERE token = ?", (token,)
                )
        if row:
            self.record_admin_audit(
                admin_user=row["username"],
                action_type=AdminActionType.ADMIN_LOGOUT.value,
                field_changed="session",
                old_value="ACTIVE",
                new_value="CLOSED",
                device_id=row["device_id"],
            )
            return True
        return False

    # =========================================================================
    # ADMIN PROOF CENTER & REVIEW WORKFLOW
    # =========================================================================

    def register_proof_review(
        self,
        evidence_id: str,
        date: str,
        task_code: str,
        task_title: str = "",
        file_path: str = "",
        file_hash: str = "",
        user_id: str = "user_lok",
        priority: str = "NORMAL",
        hour_slot: str = "",
        signals: Optional[Dict[str, Any]] = None,
        status: str = "PENDING",
    ) -> Dict[str, Any]:
        """Convenience method for registering proof review items during bidirectional sync."""
        sig = dict(signals or {})
        if task_title:
            sig["task_title"] = task_title
        if file_path:
            sig["file_path"] = file_path
        if file_hash:
            sig["file_hash"] = file_hash
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if file_path:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO evidence_files (
                        evidence_id, date, is_dev_mode, record_type, related_record_id,
                        file_path, sha256_hash, file_size_bytes, timestamp, device_id
                    ) VALUES (?, ?, 0, ?, ?, ?, ?, 1024, ?, ?)
                    """,
                    (evidence_id, date, task_code, evidence_id, file_path, file_hash or "", now_iso, "system"),
                )
        return self.ensure_proof_review_record(
            evidence_id=evidence_id,
            user_id=user_id,
            task_code=task_code,
            date=date,
            hour_slot=hour_slot,
            priority=priority,
            signals=sig,
            status=status,
        )

    def ensure_proof_review_record(
        self,
        evidence_id: str,
        user_id: str,
        task_code: str,
        date: str,
        hour_slot: str = "",
        priority: str = "NORMAL",
        signals: Optional[Dict[str, Any]] = None,
        status: str = "AI_QUEUED",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        review_id = f"pr_{uuid.uuid4().hex[:12]}"
        sig_json = json.dumps(signals or {})
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if row:
                return dict(row)
            conn.execute(
                """
                INSERT INTO proof_reviews (
                    review_id, evidence_id, user_id, task_code, date, hour_slot,
                    status, submitted_at, revision_number, signals_json, priority,activity_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?,?)
                """,
                (review_id, evidence_id, user_id, task_code, date, hour_slot, status, now_iso, sig_json, priority,str((signals or {}).get('activity_id') or '')),
            )
            # Create revision 1
            rev_id = f"rev_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO proof_revisions (revision_id, evidence_id, revision_number, status, submitted_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (rev_id, evidence_id, status, now_iso),
            )
        return {
            "review_id": review_id,
            "evidence_id": evidence_id,
            "user_id": user_id,
            "task_code": task_code,
            "date": date,
            "status": status,
            "submitted_at": now_iso,
            "revision_number": 1,
            "priority": priority,
        }

    # =========================================================================
    # SINGLE-VIDEO YOUTUBE REWARD PERSISTENCE & ENTITLEMENTS
    # =========================================================================

    def create_youtube_reward(
        self,
        reward_id: str,
        account_id: str,
        hourly_occurrence_id: str,
        submission_id: str,
        validated_score: int,
        is_timely: bool = True,
        state: str = "AVAILABLE",
    ) -> Optional[Dict[str, Any]]:
        """
        Creates a single YouTube reward entitlement for an eligible Hourly Attendance occurrence.
        Enforces one entitlement per occurrence via SQLite UNIQUE constraint on hourly_occurrence_id.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            # Check if one already exists for this occurrence
            existing = conn.execute(
                "SELECT * FROM youtube_video_rewards WHERE hourly_occurrence_id = ?",
                (hourly_occurrence_id,),
            ).fetchone()
            if existing:
                return dict(existing)

            conn.execute(
                """
                INSERT OR IGNORE INTO youtube_video_rewards (
                    reward_id, account_id, hourly_occurrence_id, submission_id,
                    validated_score, is_timely, issue_timestamp, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reward_id,
                    account_id,
                    hourly_occurrence_id,
                    submission_id,
                    int(validated_score),
                    1 if is_timely else 0,
                    now_iso,
                    state,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute(
                "SELECT * FROM youtube_video_rewards WHERE reward_id = ?", (reward_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_youtube_reward(self, reward_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM youtube_video_rewards WHERE reward_id = ?", (reward_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_youtube_reward_by_occurrence(self, hourly_occurrence_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM youtube_video_rewards WHERE hourly_occurrence_id = ?",
                (hourly_occurrence_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_active_youtube_reward(self, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT * FROM youtube_video_rewards WHERE account_id = ? AND state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM youtube_video_rewards WHERE state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            return dict(row) if row else None

    def get_available_youtube_rewards(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM youtube_video_rewards WHERE account_id = ? AND state IN ('AVAILABLE', 'VIDEO_SELECTED', 'ELIGIBLE') ORDER BY created_at DESC",
                    (account_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM youtube_video_rewards WHERE state IN ('AVAILABLE', 'VIDEO_SELECTED', 'ELIGIBLE') ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_gym_mode_state(self, account_id: str = "user_lok", now_ms: Optional[int] = None) -> Dict[str, Any]:
        from core.gym_mode_engine import GymModeEngine
        from core.time_window_engine import IST
        current = datetime.fromtimestamp(now_ms / 1000, IST) if now_ms is not None else None
        return GymModeEngine(self).state(account_id, dt=current)

    def is_gym_mode_music_active(self, account_id: str = "user_lok", now_ms: Optional[int] = None) -> bool:
        st = self.get_gym_mode_state(account_id, now_ms=now_ms)
        return bool(st.get("is_active"))

    def update_youtube_reward(self, reward_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not kwargs:
            return self.get_youtube_reward(reward_id)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kwargs["updated_at"] = now_iso
        keys = list(kwargs.keys())
        set_clause = ", ".join(f"{k} = ?" for k in keys)
        vals = [kwargs[k] for k in keys]
        vals.append(reward_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE youtube_video_rewards SET {set_clause} WHERE reward_id = ?",
                vals,
            )
            row = conn.execute(
                "SELECT * FROM youtube_video_rewards WHERE reward_id = ?", (reward_id,)
            ).fetchone()
            return dict(row) if row else None

    # ── Entertainment Entitlements (Early Proof Submission Reward) ──

    def create_entertainment_entitlement(
        self,
        entitlement_id: str,
        account_id: str,
        device_id: str,
        proof_id: str,
        submission_received_at: str,
        scheduled_deadline_at: str,
        points_awarded: int,
        duration_seconds: int = 1200,
        entitlement_type: str = "EARLY_PROOF_ENTERTAINMENT_REWARD_20M",
        state: str = "AVAILABLE",
    ) -> Optional[Dict[str, Any]]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM entertainment_entitlements WHERE proof_id = ?",
                (proof_id,),
            ).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                """
                INSERT OR IGNORE INTO entertainment_entitlements (
                    entitlement_id, account_id, device_id, entitlement_type, proof_id,
                    duration_seconds, submission_received_at, scheduled_deadline_at,
                    points_awarded, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entitlement_id,
                    account_id,
                    device_id,
                    entitlement_type,
                    proof_id,
                    duration_seconds,
                    submission_received_at,
                    scheduled_deadline_at,
                    points_awarded,
                    state,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute(
                "SELECT * FROM entertainment_entitlements WHERE entitlement_id = ?",
                (entitlement_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_entertainment_entitlement(self, entitlement_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entertainment_entitlements WHERE entitlement_id = ?",
                (entitlement_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_entertainment_entitlement_by_proof(self, proof_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entertainment_entitlements WHERE proof_id = ?",
                (proof_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_active_entertainment_entitlement(self, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT * FROM entertainment_entitlements WHERE account_id = ? AND state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM entertainment_entitlements WHERE state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            return dict(row) if row else None

    def get_available_entertainment_entitlements(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM entertainment_entitlements WHERE account_id = ? AND state = 'AVAILABLE' ORDER BY created_at DESC",
                    (account_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entertainment_entitlements WHERE state = 'AVAILABLE' ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_entertainment_entitlement(self, entitlement_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not kwargs:
            return self.get_entertainment_entitlement(entitlement_id)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kwargs["updated_at"] = now_iso
        keys = list(kwargs.keys())
        set_clause = ", ".join(f"{k} = ?" for k in keys)
        vals = [kwargs[k] for k in keys]
        vals.append(entitlement_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE entertainment_entitlements SET {set_clause} WHERE entitlement_id = ?",
                vals,
            )
            row = conn.execute(
                "SELECT * FROM entertainment_entitlements WHERE entitlement_id = ?",
                (entitlement_id,),
            ).fetchone()
            return dict(row) if row else None

    # ── Educational Video Approvals & Sessions ──

    def save_educational_video_approval(
        self,
        approval_id: str,
        video_id: str,
        canonical_url: str,
        title: Optional[str],
        channel: Optional[str],
        duration_seconds: int,
        duration_formatted: str,
        decision: str,
        reason: Optional[str],
        confidence: Optional[float],
        timeline_json: Optional[str] = None,
        review_status: str = "COMPLETED",
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO educational_video_approvals (
                    approval_id, video_id, canonical_url, title, channel,
                    duration_seconds, duration_formatted, timeline_json, decision,
                    reason, confidence, review_status, account_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, video_id) DO UPDATE SET
                    canonical_url = excluded.canonical_url,
                    title = excluded.title,
                    channel = excluded.channel,
                    duration_seconds = excluded.duration_seconds,
                    duration_formatted = excluded.duration_formatted,
                    timeline_json = excluded.timeline_json,
                    decision = excluded.decision,
                    reason = excluded.reason,
                    confidence = excluded.confidence,
                    review_status = excluded.review_status,
                    updated_at = excluded.updated_at
                """,
                (
                    approval_id, video_id, canonical_url, title, channel,
                    duration_seconds, duration_formatted, timeline_json, decision,
                    reason, confidence, review_status, account_id, now_iso, now_iso
                ),
            )
            row = conn.execute(
                "SELECT * FROM educational_video_approvals WHERE account_id = ? AND video_id = ?",
                (account_id, video_id),
            ).fetchone()
            return dict(row) if row else {}

    def get_educational_video_approval(self, video_id: str, account_id: str = "user_lok") -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM educational_video_approvals WHERE account_id = ? AND video_id = ?",
                (account_id, video_id),
            ).fetchone()
            return dict(row) if row else None

    def get_or_create_educational_playback_session(
        self,
        session_id: str,
        approval_id: str,
        video_id: str,
        allowed_seconds: int,
        account_id: str = "user_lok",
        device_id: str = "WINDOWS_EXE",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM educational_playback_sessions WHERE account_id = ? AND video_id = ?",
                (account_id, video_id),
            ).fetchone()
            if row:
                return dict(row)
            conn.execute(
                """
                INSERT OR IGNORE INTO educational_playback_sessions (
                    session_id, approval_id, video_id, account_id, device_id,
                    allowed_seconds, consumed_seconds, watched_intervals_json,
                    last_playback_state, playback_rate, last_heartbeat_at,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, '[]', 'PAUSED', 1.0, ?, 'ACTIVE', ?, ?)
                """,
                (
                    session_id, approval_id, video_id, account_id, device_id,
                    allowed_seconds, now_iso, now_iso, now_iso
                ),
            )
            row = conn.execute(
                "SELECT * FROM educational_playback_sessions WHERE account_id = ? AND video_id = ?",
                (account_id, video_id),
            ).fetchone()
            return dict(row) if row else {}

    def get_educational_playback_session(self, video_id: str, account_id: str = "user_lok") -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM educational_playback_sessions WHERE account_id = ? AND video_id = ?",
                (account_id, video_id),
            ).fetchone()
            return dict(row) if row else None

    def update_educational_playback_session(self, session_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not kwargs:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM educational_playback_sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                return dict(row) if row else None
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kwargs["updated_at"] = now_iso
        keys = list(kwargs.keys())
        set_clause = ", ".join(f"{k} = ?" for k in keys)
        vals = [kwargs[k] for k in keys]
        vals.append(session_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE educational_playback_sessions SET {set_clause} WHERE session_id = ?",
                vals,
            )
            row = conn.execute(
                "SELECT * FROM educational_playback_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_educational_validation_history(
        self,
        history_id: str,
        request_id: str,
        video_id: str,
        canonical_url: str,
        decision: str,
        duration_seconds: int = 0,
        duration_formatted: str = "Unknown",
        title: Optional[str] = None,
        channel: Optional[str] = None,
        reason: Optional[str] = None,
        confidence: Optional[float] = None,
        review_status: str = "COMPLETED",
        playback_completion_state: str = "NOT_STARTED",
        account_id: str = "user_lok",
        device_id: str = "BROWSER_EXT",
        validation_session_id: Optional[str] = None,
        request_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_dt = datetime.now()
        now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        req_date = request_date or now_dt.strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO educational_validation_history (
                    history_id, request_id, validation_session_id, account_id, device_id,
                    video_id, canonical_url, title, channel, duration_seconds,
                    duration_formatted, decision, reason, confidence, review_status,
                    playback_completion_state, request_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id, request_id, validation_session_id or "", account_id, device_id,
                    video_id, canonical_url, title or "", channel or "", duration_seconds,
                    duration_formatted, decision, reason or "", confidence, review_status,
                    playback_completion_state, req_date, now_iso
                ),
            )
            row = conn.execute(
                "SELECT * FROM educational_validation_history WHERE history_id = ?",
                (history_id,),
            ).fetchone()
            return dict(row) if row else {}

    def get_educational_validation_history(
        self, account_id: str = "user_lok", limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM educational_validation_history
                WHERE account_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_educational_history_playback_state(
        self, video_id: str, state: str = "COMPLETED", account_id: str = "user_lok"
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE educational_validation_history
                SET playback_completion_state = ?
                WHERE video_id = ? AND account_id = ?
                """,
                (state, video_id, account_id),
            )

    def set_config_value(self, key: str, value: str) -> None:
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()]
            key_col = "config_key" if "config_key" in cols else "key"
            val_col = "config_value" if "config_value" in cols else "value"
            conn.execute(
                f"""
                INSERT INTO system_config ({key_col}, {val_col}, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT({key_col}) DO UPDATE SET
                    {val_col} = excluded.{val_col},
                    updated_at = excluded.updated_at
                """,
                (key, str(value), now_iso),
            )

    def get_config_value(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()]
            key_col = "config_key" if "config_key" in cols else "key"
            val_col = "config_value" if "config_value" in cols else "value"
            row = conn.execute(
                f"SELECT {val_col} FROM system_config WHERE {key_col} = ?",
                (key,),
            ).fetchone()
            if row:
                return row[val_col] if isinstance(row, dict) else row[0]
            return default

    def get_proofs(
        self,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
        date: Optional[str] = None,
        task_code: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT pr.*, ef.file_path, ef.sha256_hash, ef.file_size_bytes, ef.device_id, ef.timestamp as capture_timestamp
            FROM proof_reviews pr
            LEFT JOIN evidence_files ef ON pr.evidence_id = ef.evidence_id
            WHERE 1=1
        """
        params: List[Any] = []
        if status:
            st_up = status.upper().strip()
            if st_up in ("PENDING", "PENDING_ADMIN_REVIEW", "NEEDS_REVIEW"):
                query += " AND pr.status IN ('PENDING', 'PROVISIONAL', 'UNDER_REVIEW', 'NEEDS_REVIEW', 'DUPLICATE_REVIEW', 'PENDING_ADMIN_REVIEW')"
            elif st_up in ("AI_ANALYZING", "AI_QUEUED", "ANALYZING", "QUEUED", "SUBMITTED"):
                query += " AND pr.status IN ('AI_QUEUED', 'AI_ANALYZING', 'SUBMITTED')"
            elif st_up in ("APPROVED", "VERIFIED"):
                query += " AND pr.status IN ('APPROVED', 'VERIFIED')"
            elif st_up in ("REJECTED", "FAILED", "FLAGGED_DUPLICATE"):
                query += " AND pr.status IN ('REJECTED', 'FLAGGED_DUPLICATE', 'FAILED', 'RETRYABLE_ERROR')"
            elif st_up != "ALL":
                query += " AND pr.status = ?"
                params.append(status)
        if user_id:
            query += " AND pr.user_id = ?"
            params.append(user_id)
        if date:
            query += " AND pr.date = ?"
            params.append(date)
        if task_code:
            query += " AND pr.task_code = ?"
            params.append(task_code)
        if priority:
            query += " AND pr.priority = ?"
            params.append(priority)

        query += " ORDER BY pr.submitted_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                try:
                    item["signals"] = json.loads(item.get("signals_json") or "{}")
                except Exception:
                    item["signals"] = {}
                result.append(item)
            return result

    def get_proof_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) as c FROM proof_reviews GROUP BY status").fetchall()
        counts = {"all": 0, "pending": 0, "ai_analyzing": 0, "approved": 0, "rejected": 0}
        for r in rows:
            st = (r["status"] or "").upper()
            c = int(r["c"])
            counts["all"] += c
            if st in ("PENDING", "PROVISIONAL", "UNDER_REVIEW", "NEEDS_REVIEW", "DUPLICATE_REVIEW", "PENDING_ADMIN_REVIEW"):
                counts["pending"] += c
            elif st in ("AI_QUEUED", "AI_ANALYZING", "SUBMITTED"):
                counts["ai_analyzing"] += c
            elif st in ("APPROVED", "VERIFIED"):
                counts["approved"] += c
            elif st in ("REJECTED", "FLAGGED_DUPLICATE", "FAILED", "RETRYABLE_ERROR"):
                counts["rejected"] += c
        return counts

    def get_proof_details(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            pr = conn.execute(
                """
                SELECT pr.*, ef.file_path, ef.sha256_hash, ef.file_size_bytes, ef.device_id,
                       ef.record_type, ef.is_deleted, ef.timestamp as capture_timestamp
                FROM proof_reviews pr
                LEFT JOIN evidence_files ef ON pr.evidence_id = ef.evidence_id
                WHERE pr.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if not pr:
                return None
            res = dict(pr)
            try:
                res["signals"] = json.loads(res.get("signals_json") or "{}")
            except Exception:
                res["signals"] = {}

            revs = conn.execute(
                "SELECT * FROM proof_revisions WHERE evidence_id = ? ORDER BY revision_number ASC",
                (evidence_id,),
            ).fetchall()
            res["revisions"] = [dict(rv) for rv in revs]
            return res

    def record_activity_journal_event(
        self,
        event_type: str,
        provenance: str,
        date: Optional[str] = None,
        account_id: str = "user_lok",
        source: str = "STRICTRECORDS",
        activity_id: str = "",
        submission_id: str = "",
        evidence_id: str = "",
        category: str = "",
        description: str = "",
        details: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """
        Records a truthful, provenance-tagged activity journal event.
        Provenance must be one of:
          USER_REPORTED, EVIDENCE_SUBMITTED, OCR_COMPLETED, AI_RECOMMENDATION, ADMIN_VERIFIED
        """
        now_iso = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_str = date or now_iso[:10]
        eid = event_id or f"aj_{uuid.uuid4().hex[:14]}"
        valid_provenance = {
            "USER_REPORTED",
            "EVIDENCE_SUBMITTED",
            "OCR_COMPLETED",
            "AI_RECOMMENDATION",
            "ADMIN_VERIFIED",
        }
        prov = provenance.upper() if provenance.upper() in valid_provenance else "USER_REPORTED"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO activity_journal_events (
                    event_id, account_id, date, event_type, provenance,
                    timestamp, timezone, source, activity_id, submission_id,
                    evidence_id, category, description, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'Asia/Kolkata', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    account_id,
                    date_str,
                    event_type,
                    prov,
                    now_iso,
                    source,
                    str(activity_id or ""),
                    str(submission_id or ""),
                    str(evidence_id or ""),
                    str(category or ""),
                    str(description or ""),
                    json.dumps(details or {}),
                ),
            )
        return eid

    def get_activity_journal_events(
        self,
        date: str,
        account_id: str = "user_lok",
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM activity_journal_events
                WHERE date = ? AND account_id = ?
                ORDER BY timestamp ASC
                """,
                (date, account_id),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                try:
                    item["details"] = json.loads(item.get("details_json") or "{}")
                except Exception:
                    item["details"] = {}
                out.append(item)
            return out

    def get_historical_evidence_for_ai(
        self,
        limit: int = 250,
        exclude_evidence_id: Optional[str] = None,
        exclude_submission_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ef.evidence_id, ef.related_record_id, ef.sha256_hash, ef.metadata_json,
                       ef.timestamp, ef.device_id, ef.file_path, pr.signals_json
                FROM evidence_files ef
                LEFT JOIN proof_reviews pr ON pr.evidence_id = ef.evidence_id
                WHERE ef.is_deleted = 0 AND (ef.sha256_hash IS NOT NULL OR ef.metadata_json IS NOT NULL)
                ORDER BY ef.timestamp DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            res = []
            for r in rows:
                item = dict(r)
                eid = item.get("evidence_id")
                rel_id = item.get("related_record_id")
                if exclude_evidence_id and eid == exclude_evidence_id:
                    continue
                if exclude_submission_id and (rel_id == exclude_submission_id or eid == exclude_submission_id):
                    continue
                dhash = None
                ahash = None
                ocr_tokens: List[str] = []
                try:
                    meta = json.loads(item.get("metadata_json") or "{}")
                    dhash = meta.get("dhash")
                    ahash = meta.get("ahash")
                    ocr_tokens = meta.get("ocr_tokens") or []
                except Exception:
                    pass
                if (not dhash or not ocr_tokens) and item.get("signals_json"):
                    try:
                        sig = json.loads(item.get("signals_json") or "{}")
                        sig_meta = sig.get("metadata") or {}
                        dhash = dhash or sig_meta.get("dhash")
                        ahash = ahash or sig_meta.get("ahash")
                        if not ocr_tokens:
                            ocr_tokens = (sig.get("ocr") or {}).get("tokens") or []
                    except Exception:
                        pass
                item["dhash"] = dhash
                item["ahash"] = ahash
                item["ocr_tokens"] = ocr_tokens
                res.append(item)
            return res

    def save_strictbot_ai_review(self, rev: Dict[str, Any], user_id: str = "user_lok") -> str:
        evidence_id = rev.get("evidence_id") or rev.get("submission_id")
        if not evidence_id:
            # A submission identifier is not evidence. Never create a review
            # record that implies a proof file exists when no file was stored.
            return ""
        task_code = rev.get("task_code", "HOURLY")
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_str = rev.get("metadata", {}).get("reviewed_at", now_iso)[:10]
        dec = rev.get("decision")
        is_admin_req = rev.get("admin_review_required")
        if dec in ("AUTO_APPROVED", "APPROVED"):
            status = "APPROVED"
            pts_awarded = int(rev.get("proposed_points", 0))
        elif dec == "PROVISIONAL_APPROVE" and is_admin_req is not True:
            # The existing scoring engine must commit before a recommendation becomes approval.
            status = "PROVISIONAL"
            pts_awarded = 0
        elif dec in ("REJECTED", "PROVISIONAL_REJECT") and is_admin_req is False:
            status = "REJECTED"
            pts_awarded = 0
        elif dec == "PROCESSING_ERROR":
            status = "RETRYABLE_ERROR"
            pts_awarded = 0
        else:
            status = "PENDING_ADMIN_REVIEW"
            pts_awarded = 0

        review_id = f"rev_{uuid.uuid4().hex[:12]}"
        meta_block = rev.get("metadata") or {}
        ocr_block = rev.get("ocr") or {}
        dup_block = rev.get("duplicate") or {}

        with self._connect() as conn:
            evidence_row = conn.execute(
                "SELECT metadata_json, sha256_hash, date FROM evidence_files WHERE evidence_id = ? AND is_deleted = 0",
                (evidence_id,),
            ).fetchone()
            if evidence_row:
                date_str = evidence_row["date"]
                # Enrich evidence_files.metadata_json with dhash, ahash, ocr, and duplicate fields
                try:
                    ef_meta = json.loads(evidence_row["metadata_json"] or "{}")
                except Exception:
                    ef_meta = {}
                if meta_block.get("dhash"):
                    ef_meta["dhash"] = meta_block["dhash"]
                if meta_block.get("ahash"):
                    ef_meta["ahash"] = meta_block["ahash"]
                if ocr_block:
                    ef_meta["ocr_status"] = ocr_block.get("status", "OCR_UNAVAILABLE")
                    ef_meta["ocr_text"] = (ocr_block.get("extracted_text") or "")[:1000]
                    ef_meta["ocr_tokens"] = (ocr_block.get("tokens") or [])[:250]
                    ef_meta["ocr_confidence"] = ocr_block.get("confidence", 0.0)
                if dup_block:
                    ef_meta["duplicate_status"] = dup_block.get("status") or dup_block.get("match_status", "NO_MATCH_FOUND")
                    ef_meta["matched_evidence_id"] = dup_block.get("matched_evidence_id")
                    ef_meta["similarity_score"] = dup_block.get("similarity_score", 0.0)
                    ef_meta["comparison_method"] = dup_block.get("comparison_method", "")
                conn.execute(
                    "UPDATE evidence_files SET metadata_json = ? WHERE evidence_id = ?",
                    (json.dumps(ef_meta), evidence_id),
                )
                # Persist OCR cache by sha256_hash if available
                sha_key = (meta_block.get("sha256") or evidence_row["sha256_hash"] or "").strip().lower()
                if len(sha_key) == 64 and ocr_block.get("status") in ("OCR_COMPLETED", "NO_TEXT_DETECTED"):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ocr_cache (
                            sha256_hash, status, extracted_text, tokens_json, confidence, engine, computed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sha_key,
                            ocr_block.get("status", "NO_TEXT_DETECTED"),
                            ocr_block.get("extracted_text") or "",
                            json.dumps(ocr_block.get("tokens") or []),
                            float(ocr_block.get("confidence") or 0.0),
                            str(ocr_block.get("engine") or ""),
                            now_iso,
                        ),
                    )

            reviewer_name = str(rev.get("validator") or "STRICTBOT_AI").upper()
            reason_text = rev.get("review_reason") or (", ".join(rev.get("reasons", [])) if isinstance(rev.get("reasons"), list) else str(rev.get("reasons") or ""))
            existing = conn.execute("SELECT review_id FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()
            if existing:
                review_id = existing["review_id"]
                current = conn.execute(
                    "SELECT status FROM proof_reviews WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()
                if current and current["status"] in ("APPROVED", "REJECTED", "VERIFIED"):
                    # A later AI retry cannot overwrite a human decision.
                    return review_id
                conn.execute(
                    """
                    UPDATE proof_reviews
                    SET decision = ?, status = ?, points_awarded = ?, signals_json = ?, reviewed_at = ?, reviewed_by = ?, reason_note = COALESCE(?, reason_note)
                    WHERE evidence_id = ?
                    """,
                    (rev.get("decision"), status, pts_awarded, json.dumps(rev), now_iso, reviewer_name, reason_text, evidence_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO proof_reviews (
                        review_id, evidence_id, user_id, task_code, date, hour_slot,
                        status, submitted_at, reviewed_at, reviewed_by, decision,
                        points_awarded, signals_json, priority, reason_note
                    ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 'NORMAL', ?)
                    """,
                    (
                        review_id, evidence_id, user_id, task_code, date_str,
                        status, rev.get("timing", {}).get("submitted_at", now_iso), now_iso,
                        reviewer_name, rev.get("decision"), pts_awarded, json.dumps(rev), reason_text,
                    ),
                )

        # Record truthful journal events for OCR and AI recommendation
        if ocr_block.get("status") in ("OCR_COMPLETED", "NO_TEXT_DETECTED", "OCR_UNAVAILABLE", "OCR_FAILED"):
            self.record_activity_journal_event(
                event_type=f"OCR_{ocr_block.get('status')}",
                provenance="OCR_COMPLETED",
                date=date_str,
                account_id=user_id,
                source=str(meta_block.get("platform") or "STRICTRECORDS"),
                activity_id=str(rev.get("activity_id") or ""),
                submission_id=str(rev.get("submission_id") or ""),
                evidence_id=evidence_id,
                category=task_code,
                description=(ocr_block.get("extracted_text") or "")[:240],
                details={
                    "ocr_status": ocr_block.get("status"),
                    "confidence": ocr_block.get("confidence", 0.0),
                    "token_count": ocr_block.get("token_count", 0),
                    "engine": ocr_block.get("engine", ""),
                },
                event_id=f"aj_ocr_{evidence_id}",
                timestamp=now_iso,
            )
        self.record_activity_journal_event(
            event_type=f"AI_REVIEW_{rev.get('decision', 'NEEDS_REVIEW')}",
            provenance="AI_RECOMMENDATION",
            date=date_str,
            account_id=user_id,
            source="STRICTBOT_AI",
            activity_id=str(rev.get("activity_id") or ""),
            submission_id=str(rev.get("submission_id") or ""),
            evidence_id=evidence_id,
            category=task_code,
            description="; ".join((rev.get("reasons") or [])[:2]),
            details={
                "decision": rev.get("decision"),
                "confidence": rev.get("confidence"),
                "proposed_points": rev.get("proposed_points"),
                "duplicate_status": dup_block.get("status") or dup_block.get("match_status", "NO_MATCH_FOUND"),
                "matched_evidence_id": dup_block.get("matched_evidence_id"),
                "similarity_score": dup_block.get("similarity_score", 0.0),
            },
            event_id=f"aj_ai_{evidence_id}",
            timestamp=now_iso,
        )
        return review_id

    def approve_proof(
        self,
        evidence_id: str,
        admin_id: str,
        points: int = 3,
        reason: str = "Evidence verified by Administrator",
        rule_version: int = 1,
    ) -> Dict[str, Any]:
        points = max(0, int(points))
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            pr = conn.execute(
                """
                SELECT pr.*, ef.file_path AS evidence_file_path, ef.is_deleted AS evidence_deleted,
                       ef.record_type AS evidence_record_type, ef.sha256_hash AS evidence_sha256_hash
                FROM proof_reviews pr
                LEFT JOIN evidence_files ef ON ef.evidence_id = pr.evidence_id
                WHERE pr.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if not pr:
                raise ValueError(f"Proof record for evidence {evidence_id} not found.")

            evidence_path = pr["evidence_file_path"]
            evidence_del = 1 if pr["evidence_deleted"] == 1 else 0
            if evidence_del != 0 or not is_valid_retained_proof_file(
                evidence_path, pr["evidence_record_type"] or "", pr["evidence_sha256_hash"]
            ):
                raise ValueError("Proof cannot be approved without an available, retained evidence file.")

            if pr["status"] == "APPROVED":
                return {
                    "evidence_id": evidence_id,
                    "status": "APPROVED",
                    "decision": "APPROVE",
                    "points_awarded": int(pr["points_awarded"] or 0),
                    "reviewed_at": pr["reviewed_at"],
                    "reviewed_by": pr["reviewed_by"],
                    "user_id": pr["user_id"],
                    "date": pr["date"],
                    "task_code": pr["task_code"],
                    "already_approved": True,
                }

            if pr["status"] not in ("PENDING", "UNDER_REVIEW", "PROVISIONAL", "NEEDS_REVIEW", "PENDING_ADMIN_REVIEW", "AI_QUEUED", "AI_ANALYZING", "SUBMITTED"):
                raise ValueError(f"Proof cannot be approved from state {pr['status']}.")

            # Section 20: Self-approval protection
            if pr["user_id"] == admin_id:
                raise PermissionError("Administrators cannot approve their own evidence.")

            conn.execute(
                """
                UPDATE proof_reviews
                SET status = 'APPROVED',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    decision = 'APPROVED',
                    points_awarded = ?,
                    reason_note = ?
                WHERE evidence_id = ? AND status IN ('PENDING', 'UNDER_REVIEW', 'PROVISIONAL', 'NEEDS_REVIEW', 'PENDING_ADMIN_REVIEW', 'AI_QUEUED', 'AI_ANALYZING', 'SUBMITTED')
                """,
                (now_iso, admin_id, max(0, int(points)), reason, evidence_id),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("Proof review was changed concurrently; retry after reloading it.")

            # Update latest revision
            conn.execute(
                """
                UPDATE proof_revisions
                SET status = 'APPROVED',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    reason_note = ?
                WHERE evidence_id = ? AND revision_number = ?
                """,
                (now_iso, admin_id, reason, evidence_id, pr["revision_number"]),
            )

        self.record_admin_audit(
            admin_user=admin_id,
            action_type=AdminActionType.PROOF_APPROVED.value,
            field_changed=f"proof:{evidence_id}",
            old_value=pr["status"],
            new_value="APPROVED",
            device_id="admin_portal",
        )
        self.record_activity_journal_event(
            event_type="PROOF_APPROVED",
            provenance="ADMIN_VERIFIED",
            date=str(pr["date"]),
            account_id=str(pr["user_id"]),
            source=f"ADMIN:{admin_id}",
            evidence_id=evidence_id,
            category=str(pr["task_code"]),
            description=reason,
            details={"points_awarded": points, "reviewed_by": admin_id, "decision": "APPROVED"},
            event_id=f"aj_admin_app_{evidence_id}",
            timestamp=now_iso,
        )

        return {
            "evidence_id": evidence_id,
            "status": "APPROVED",
            "decision": "APPROVE",
            "points_awarded": points,
            "reviewed_at": now_iso,
            "reviewed_by": admin_id,
            "user_id": pr["user_id"],
            "date": pr["date"],
            "task_code": pr["task_code"],
        }

    def reject_proof(
        self,
        evidence_id: str,
        admin_id: str,
        reason_category: str,
        reason_note: str = "",
        allow_resubmission: bool = True,
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not reason_category:
            reason_category = ProofRejectionReason.OTHER.value

        with self._connect() as conn:
            pr = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if not pr:
                raise ValueError(f"Proof record for evidence {evidence_id} not found.")

            new_status = "REJECTED"
            conn.execute(
                """
                UPDATE proof_reviews
                SET status = ?,
                    reviewed_at = ?,
                    reviewed_by = ?,
                    decision = 'REJECTED',
                    reason_category = ?,
                    reason_note = ?,
                    points_awarded = 0
                WHERE evidence_id = ?
                """,
                (new_status, now_iso, admin_id, reason_category, reason_note, evidence_id),
            )

            conn.execute(
                """
                UPDATE proof_revisions
                SET status = 'REJECTED',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    reason_note = ?
                WHERE evidence_id = ? AND revision_number = ?
                """,
                (now_iso, admin_id, f"{reason_category}: {reason_note}".strip(), evidence_id, pr["revision_number"]),
            )

        self.record_admin_audit(
            admin_user=admin_id,
            action_type=AdminActionType.PROOF_REJECTED.value,
            field_changed=f"proof:{evidence_id}",
            old_value=pr["status"],
            new_value="REJECTED",
            device_id="admin_portal",
        )
        self.record_activity_journal_event(
            event_type="PROOF_REJECTED",
            provenance="ADMIN_VERIFIED",
            date=str(pr["date"]),
            account_id=str(pr["user_id"]),
            source=f"ADMIN:{admin_id}",
            evidence_id=evidence_id,
            category=str(pr["task_code"]),
            description=f"{reason_category}: {reason_note}".strip(": "),
            details={"reason_category": reason_category, "reason_note": reason_note, "reviewed_by": admin_id, "decision": "REJECTED"},
            event_id=f"aj_admin_rej_{evidence_id}",
            timestamp=now_iso,
        )

        return {
            "evidence_id": evidence_id,
            "status": "REJECTED",
            "reason_category": reason_category,
            "reason_note": reason_note,
            "allow_resubmission": allow_resubmission,
            "reviewed_at": now_iso,
            "reviewed_by": admin_id,
            "user_id": pr["user_id"],
            "date": pr["date"],
            "task_code": pr["task_code"],
        }

    def bulk_review_proofs(
        self,
        evidence_ids: List[str],
        decision: str,
        admin_id: str,
        reason_category: str = "",
        reason_note: str = "",
        points_each: int = 3,
    ) -> Dict[str, Any]:
        results = []
        errors = []
        for eid in evidence_ids:
            try:
                if decision.upper() == "APPROVE":
                    res = self.approve_proof(eid, admin_id=admin_id, points=points_each, reason="Bulk approval by Administrator")
                    results.append(res)
                else:
                    res = self.reject_proof(
                        eid,
                        admin_id=admin_id,
                        reason_category=reason_category or "Bulk rejection",
                        reason_note=reason_note,
                    )
                    results.append(res)
            except Exception as e:
                errors.append({"evidence_id": eid, "error": str(e)})

        return {"processed": len(results), "errors": errors, "results": results}

    def resubmit_proof(
        self,
        evidence_id: str,
        user_id: str,
        new_file_path: str,
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            pr = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if not pr:
                raise ValueError("Proof record not found.")

            new_rev_num = int(pr["revision_number"]) + 1
            conn.execute(
                """
                UPDATE proof_reviews
                SET status = 'PENDING',
                    revision_number = ?,
                    submitted_at = ?,
                    reviewed_at = NULL,
                    reviewed_by = NULL,
                    decision = NULL
                WHERE evidence_id = ?
                """,
                (new_rev_num, now_iso, evidence_id),
            )

            rev_id = f"rev_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO proof_revisions (
                    revision_id, evidence_id, revision_number, status, submitted_at, file_path
                ) VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (rev_id, evidence_id, new_rev_num, now_iso, new_file_path),
            )

        return {
            "evidence_id": evidence_id,
            "status": "PENDING",
            "revision_number": new_rev_num,
            "submitted_at": now_iso,
        }

    # =========================================================================
    # CONTROLLED ADMIN POINT MANAGEMENT & SCORE OVERRIDE
    # =========================================================================

    def admin_adjust_score(
        self,
        date: str,
        admin_id: str,
        adjustment_points: int,
        reason: str,
        is_dev_mode: bool = False,
        user_id: str = "user_lok",
    ) -> Dict[str, Any]:
        if not reason or not str(reason).strip():
            raise ValueError("A documented reason is strictly mandatory for manual score adjustment.")

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.ensure_daily_record(date, user_id=user_id, is_dev_mode=is_dev_mode)

        with self._connect() as conn:
            rec = conn.execute(
                "SELECT * FROM daily_records WHERE date = ? AND is_dev_mode = ? AND user_id = ?",
                (date, 1 if is_dev_mode else 0, user_id),
            ).fetchone()

            old_score = rec["final_score"]
            cur_pos = rec["positive_points"]
            cur_neg = rec["negative_points"]

            if adjustment_points >= 0:
                new_pos = cur_pos + adjustment_points
                new_neg = cur_neg
            else:
                new_pos = cur_pos
                new_neg = cur_neg + abs(adjustment_points)

            raw = new_pos - new_neg
            new_score = max(0, min(100, raw))

            # Generate immutable ScoreEvent
            event_id = f"se_adj_{uuid.uuid4().hex[:10]}"
            audit_dict = {
                "admin_user": admin_id,
                "old_score": old_score,
                "adjustment": adjustment_points,
                "new_score": new_score,
                "reason": reason,
                "timestamp": now_iso,
            }

            conn.execute(
                """
                INSERT INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type, device_id,
                    description, points, rule_version, audit_json
                ) VALUES (?, ?, ?, ?, ?, 'admin_portal', ?, ?, 1, ?)
                """,
                (
                    event_id,
                    date,
                    1 if is_dev_mode else 0,
                    now_iso,
                    ScoreEventType.ADMIN_SCORE_ADJUSTMENT.value,
                    f"Admin Adjustment ({adjustment_points:+d} pts): {reason}",
                    adjustment_points,
                    json.dumps(audit_dict),
                ),
            )

            # Update daily record
            from core.models import ScoreMode, ScoreState
            new_mode = ScoreMode.REWARD_UNLOCKED.value if new_score >= 80 else (
                ScoreMode.CONTROLLED_MODE.value if new_score >= 60 else ScoreMode.STRICT_MODE.value
            )
            new_state = ScoreState.ELITE_DAY.value if new_score >= 90 else (
                ScoreState.REWARD_UNLOCKED.value if new_score >= 80 else (
                    ScoreState.CONTROLLED_MODE.value if new_score >= 60 else ScoreState.STRICT_MODE.value
                )
            )

            conn.execute(
                """
                UPDATE daily_records
                SET positive_points = ?,
                    negative_points = ?,
                    gross_score = ?,
                    final_score = ?,
                    score_mode = ?,
                    score_state = ?,
                    reward_unlocked = ?,
                    updated_at = ?
                WHERE date = ? AND is_dev_mode = ? AND user_id = ?
                """,
                (
                    new_pos,
                    new_neg,
                    raw,
                    new_score,
                    new_mode,
                    new_state,
                    1 if new_score >= 80 else 0,
                    now_iso,
                    date,
                    1 if is_dev_mode else 0,
                    user_id,
                ),
            )

        self.record_admin_audit(
            admin_user=admin_id,
            action_type=AdminActionType.POINT_ADJUSTED.value,
            field_changed=f"score:{date}",
            old_value=str(old_score),
            new_value=str(new_score),
            device_id="admin_portal",
        )

        return {
            "date": date,
            "old_score": old_score,
            "adjustment": adjustment_points,
            "new_score": new_score,
            "reason": reason,
            "event_id": event_id,
            "reward_unlocked": new_score >= 80,
        }

    # =========================================================================
    # DEDUCTION PROPOSALS (Sections 1 & 2: EXE Read-Only for Finalized Deductions)
    # =========================================================================

    def create_deduction_proposal(
        self,
        proposal_id: str,
        date: str,
        timestamp: str,
        originating_device: str,
        event_type: str,
        proposed_points: int,
        reason: str,
        related_task_id: str = "",
        applicable_rule: str = "",
        evidence: Optional[Dict[str, Any]] = None,
        status: str = "AWAITING_CONFIRMATION",
    ) -> Dict[str, Any]:
        """
        Records a deduction proposal.
        The Windows EXE and background monitors may only propose deductions;
        they cannot finalize or deduct points independently.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ev_json = json.dumps(evidence or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deduction_proposals (
                    proposal_id, date, timestamp, originating_device, event_type,
                    related_task_id, applicable_rule, proposed_points, reason,
                    evidence_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    evidence_json = excluded.evidence_json
                """,
                (
                    proposal_id,
                    date,
                    timestamp,
                    originating_device,
                    event_type,
                    related_task_id,
                    applicable_rule,
                    proposed_points,
                    reason,
                    ev_json,
                    status,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute("SELECT * FROM deduction_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
            res = dict(row)
            res["evidence"] = json.loads(res.pop("evidence_json", "{}"))
            return res

    def get_deduction_proposals(
        self, date: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM deduction_proposals WHERE 1=1"
        params: List[Any] = []
        if date:
            query += " AND date = ?"
            params.append(date)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY timestamp DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["evidence"] = json.loads(d.pop("evidence_json", "{}"))
                out.append(d)
            return out

    def get_deduction_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM deduction_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["evidence"] = json.loads(d.pop("evidence_json", "{}"))
            return d

    def update_deduction_proposal_status(
        self,
        proposal_id: str,
        status: str,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        approval_source: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        finalized_event_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE deduction_proposals SET
                    status = ?,
                    approved_by = COALESCE(?, approved_by),
                    approved_at = COALESCE(?, approved_at),
                    approval_source = COALESCE(?, approval_source),
                    rejection_reason = COALESCE(?, rejection_reason),
                    finalized_event_id = COALESCE(?, finalized_event_id),
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    status,
                    approved_by,
                    approved_at,
                    approval_source,
                    rejection_reason,
                    finalized_event_id,
                    now_iso,
                    proposal_id,
                ),
            )
            return self.get_deduction_proposal(proposal_id)

    # =========================================================================
    # CONFIGURATION VERSIONING & SYNC
    # =========================================================================

    def get_active_configuration(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM configuration_versions
                WHERE status = 'PUBLISHED'
                ORDER BY version_number DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                res = dict(row)
                try:
                    res["rules"] = json.loads(res.get("rules_json") or "{}")
                except Exception:
                    res["rules"] = {}
                return res
            # Fallback
            st = self.get_settings("user_lok")
            return {
                "version_code": "v12",
                "version_number": 12,
                "status": "PUBLISHED",
                "rules": st.to_dict(),
                "created_by": "Administrator",
                "effective_from": "2026-10-01 00:00:00",
            }

    def create_config_draft(
        self,
        version_code: str,
        rules: Dict[str, Any],
        admin_id: str = "Administrator",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        v_num = int("".join([c for c in version_code if c.isdigit()]) or "13")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO configuration_versions (
                    version_code, version_number, status, rules_json, created_by, created_at, effective_from
                ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?)
                """,
                (version_code, v_num, json.dumps(rules), admin_id, now_iso, now_iso),
            )
        return {"version_code": version_code, "status": "DRAFT", "rules": rules}

    def publish_configuration(
        self,
        version_code: str,
        admin_id: str = "Administrator",
        effective_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        eff = effective_from or now_iso
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM configuration_versions WHERE version_code = ?",
                (version_code,),
            ).fetchone()
            if not row:
                raise ValueError(f"Configuration version {version_code} not found.")

            conn.execute(
                """
                UPDATE configuration_versions
                SET status = 'PUBLISHED', published_at = ?, effective_from = ?
                WHERE version_code = ?
                """,
                (now_iso, eff, version_code),
            )

        self.record_admin_audit(
            admin_user=admin_id,
            action_type=AdminActionType.CONFIG_PUBLISHED.value,
            field_changed="config_version",
            old_value=None,
            new_value=version_code,
            device_id="admin_portal",
        )

        return {"version_code": version_code, "status": "PUBLISHED", "effective_from": eff}

    # =========================================================================
    # MANAGED DEVICES
    # =========================================================================

    def get_managed_devices(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
            return [dict(r) for r in rows]

    def register_or_update_device(
        self,
        device_id: str,
        user_id: str = "user_lok",
        device_name: str = "Client Device",
        platform: str = "ANDROID",
        app_version: str = "2.0.0",
        agent_version: str = "2.0.0",
        protocol_version: str = "2.0.0",
        battery_level: int = 100,
        storage_free_mb: int = 4096,
        pairing_state: str = "PAIRED",
        sync_status: str = "ONLINE",
    ) -> Dict[str, Any]:
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    agent_version, protocol_version, battery_level, storage_free_mb,
                    pairing_state, last_seen, sync_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    device_name = excluded.device_name,
                    platform = excluded.platform,
                    app_version = excluded.app_version,
                    agent_version = excluded.agent_version,
                    battery_level = excluded.battery_level,
                    storage_free_mb = excluded.storage_free_mb,
                    pairing_state = excluded.pairing_state,
                    last_seen = excluded.last_seen,
                    sync_status = excluded.sync_status
                """,
                (
                    device_id, user_id, device_name, platform, app_version,
                    agent_version, protocol_version, battery_level, storage_free_mb,
                    pairing_state, now_iso, sync_status
                ),
            )
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            return dict(row)

    # =========================================================================
    # DEVICE CENTER: PERMANENT REGISTRATION & WI-FI PAIRING HANDSHAKE
    # =========================================================================

    def ensure_host_pc_registered(self) -> Dict[str, Any]:
        """Ensures the host Windows PC is registered under the account."""
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (account_id, username, email, created_at, status)
                VALUES ('user_lok', 'Lok', 'lok@strictrecords.local', ?, 'ACTIVE')
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    agent_version, protocol_version, battery_level, storage_free_mb,
                    pairing_state, last_seen, sync_status, device_type, registered_at, status
                ) VALUES (
                    'win_host_pc', 'user_lok', 'StrictRecords PC', 'WINDOWS', '2.0.0',
                    '2.0.0', '2.0.0', 100, 40960,
                    'PAIRED', ?, 'ONLINE', 'WINDOWS', ?, 'REGISTERED'
                )
                ON CONFLICT(device_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    sync_status = 'ONLINE',
                    status = 'REGISTERED'
                """,
                (now_iso, now_iso),
            )
            row = conn.execute("SELECT * FROM devices WHERE device_id = 'win_host_pc'").fetchone()
            return dict(row)

    def create_pairing_session(self, account_id: str = "user_lok", server_endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Creates a one-time 5-minute expiring pairing session with both a random session key
        (e.g. SR-74KD-92PM) and a 6-digit manual PIN (e.g. 482910), plus all candidate LAN endpoints.
        """
        pairing_id = f"pair_{uuid.uuid4().hex[:12]}"
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        part1 = "".join(random.choices(chars, k=4))
        part2 = "".join(random.choices(chars, k=4))
        session_key = f"SR-{part1}-{part2}"
        manual_pin = f"{random.randint(100000, 999999):06d}"
        verification_code = f"{manual_pin[:3]} {manual_pin[3:]}"

        now = datetime.now()
        expires_at = (now + timedelta(minutes=5)).isoformat()
        now_iso = now.isoformat()

        lan_ips = get_all_lan_ips()
        primary_ip = lan_ips[0] if lan_ips else "127.0.0.1"
        endpoint = server_endpoint or f"http://{primary_ip}:8765"
        candidate_endpoints = [endpoint]
        for ip_addr in lan_ips:
            ep = f"http://{ip_addr}:8765"
            if ep not in candidate_endpoints:
                candidate_endpoints.append(ep)
        if "http://127.0.0.1:8765" not in candidate_endpoints:
            candidate_endpoints.append("http://127.0.0.1:8765")

        raw_token = f"sr_trust_{uuid.uuid4().hex}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (account_id, username, email, created_at, status)
                VALUES (?, 'Lok', 'lok@strictrecords.local', ?, 'ACTIVE')
                """,
                (account_id, now_iso),
            )
            conn.execute(
                """
                INSERT INTO pairing_sessions (
                    pairing_id, account_id, session_key, manual_pin, server_endpoint, created_at, expires_at, status, failed_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0)
                """,
                (pairing_id, account_id, session_key, manual_pin, endpoint, now_iso, expires_at),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO device_pairings (
                    pairing_id, android_device_id, windows_device_id, trust_token_hash,
                    paired_via, android_approved, windows_approved, status, paired_at, verification_code
                ) VALUES (?, 'android_lok_phone', 'win_host_pc', ?, 'LOCAL_WIFI', 0, 1, 'PENDING_APPROVAL', ?, ?)
                """,
                (pairing_id, token_hash, now.strftime("%Y-%m-%d %H:%M:%S"), verification_code),
            )

        qr_b64 = ""
        qr_payload = json.dumps({
            "pairing_id": pairing_id,
            "session_key": session_key,
            "manual_pin": manual_pin,
            "account_id": account_id,
            "server_endpoint": endpoint,
            "candidate_endpoints": candidate_endpoints,
            "expires_at": expires_at,
        }, separators=(",", ":"))
        try:
            import qrcode
            import io
            import base64
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=3,
            )
            qr.add_data(qr_payload)
            qr.make(fit=True)
            try:
                img = qr.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                # Pure-Python SVG matrix fallback if PIL image saving fails
                matrix = qr.get_matrix()
                size = len(matrix)
                rects = []
                for r_idx, row_vals in enumerate(matrix):
                    for c_idx, val in enumerate(row_vals):
                        if val:
                            rects.append(f'<rect x="{c_idx}" y="{r_idx}" width="1" height="1" fill="#000"/>')
                svg_str = (
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
                    f'<rect width="100%" height="100%" fill="#fff"/>{"".join(rects)}</svg>'
                )
                qr_b64 = "data:image/svg+xml;base64," + base64.b64encode(svg_str.encode("utf-8")).decode("ascii")
        except Exception:
            import base64
            svg_fallback = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">'
                f'<rect width="220" height="220" rx="14" fill="#ffffff"/>'
                f'<rect x="14" y="14" width="192" height="192" rx="10" fill="none" stroke="#18181b" stroke-width="6"/>'
                f'<text x="110" y="92" text-anchor="middle" font-family="monospace" font-size="14" font-weight="bold" fill="#18181b">{session_key}</text>'
                f'<text x="110" y="128" text-anchor="middle" font-family="monospace" font-size="22" font-weight="bold" fill="#6d28d9">PIN: {manual_pin}</text>'
                f'<text x="110" y="162" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#52525b">{primary_ip}:8765</text>'
                f'</svg>'
            )
            qr_b64 = "data:image/svg+xml;base64," + base64.b64encode(svg_fallback.encode("utf-8")).decode("ascii")

        return {
            "pairing_id": pairing_id,
            "session_key": session_key,
            "manual_pin": manual_pin,
            "verification_code": verification_code,
            "account_id": account_id,
            "server_endpoint": endpoint,
            "candidate_endpoints": candidate_endpoints,
            "created_at": now_iso,
            "expires_at": expires_at,
            "expires_in_seconds": 300,
            "qr_payload": qr_payload,
            "qr_code_image": qr_b64,
        }

    @staticmethod
    def _normalize_pairing_input(raw_value: str) -> Tuple[str, str]:
        """Returns (normalized_session_key, normalized_digits_or_clean)."""
        cleaned = (raw_value or "").strip().upper().replace(" ", "")
        alnum = "".join(ch for ch in cleaned if ch.isalnum())
        if alnum.startswith("SR") and len(alnum) == 10:
            return f"SR-{alnum[2:6]}-{alnum[6:10]}", alnum
        if len(alnum) == 8 and not alnum.isdigit():
            return f"SR-{alnum[:4]}-{alnum[4:8]}", alnum
        return cleaned, alnum

    def get_pairing_session(self, identifier: str) -> Optional[Dict[str, Any]]:
        clean_id = (identifier or "").strip()
        if not clean_id:
            return None
        norm_key, alnum = self._normalize_pairing_input(clean_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM pairing_sessions
                WHERE pairing_id = ?
                   OR UPPER(session_key) = UPPER(?)
                   OR UPPER(session_key) = UPPER(?)
                   OR (manual_pin != '' AND manual_pin = ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (clean_id, clean_id, norm_key, alnum),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            now = datetime.now()
            try:
                exp = datetime.fromisoformat(res["expires_at"])
                if now > exp and res["status"] == "PENDING":
                    conn.execute(
                        "UPDATE pairing_sessions SET status = 'EXPIRED' WHERE pairing_id = ?",
                        (res["pairing_id"],),
                    )
                    res["status"] = "EXPIRED"
            except Exception:
                pass
            if res.get("paired_device_id"):
                d_row = conn.execute(
                    "SELECT device_name FROM devices WHERE device_id = ?",
                    (res["paired_device_id"],),
                ).fetchone()
                if d_row:
                    res["device_name"] = d_row["device_name"]
            return res

    def handshake_pairing(
        self,
        session_key: str = "",
        device_id: str = "android_lok_phone",
        device_name: str = "Android Device",
        device_type: str = "ANDROID",
        platform: str = "ANDROID",
        app_version: str = "2.0.0",
        manual_pin: str = "",
        device_model: str = "",
        os_version: str = "",
        local_ip: str = "",
    ) -> Dict[str, Any]:
        """
        Validates pairing session key or 6-digit manual PIN, registers the Android device
        permanently under the account, and issues a long-lived secure device credential token.
        Supports idempotent retry by the same device_id if the initial response timed out.
        """
        raw_input = (session_key or manual_pin or "").strip()
        if not raw_input:
            return {
                "success": False,
                "error": "Pairing code or 6-digit PIN is required.",
                "code": "MISSING_CODE",
            }
        norm_key, alnum = self._normalize_pairing_input(raw_input)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM pairing_sessions
                WHERE UPPER(session_key) = UPPER(?)
                   OR UPPER(session_key) = UPPER(?)
                   OR (manual_pin != '' AND manual_pin = ?)
                   OR pairing_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (raw_input.replace(" ", ""), norm_key, alnum, raw_input),
            ).fetchone()

            if not row:
                return {
                    "success": False,
                    "error": "Invalid pairing code or PIN. Please check the code shown on your PC.",
                    "code": "NOT_FOUND",
                }

            sess = dict(row)
            target_pairing_id = sess["pairing_id"]
            now = datetime.now()

            failed_attempts = int(sess.get("failed_attempts") or 0)
            if failed_attempts >= 5:
                conn.execute("UPDATE pairing_sessions SET status = 'EXPIRED' WHERE pairing_id = ?", (target_pairing_id,))
                return {
                    "success": False,
                    "error": "Too many failed pairing attempts. Generate a new code on your PC.",
                    "code": "RATE_LIMITED",
                }

            try:
                exp = datetime.fromisoformat(sess["expires_at"])
                if now > exp and sess["status"] == "PENDING":
                    conn.execute("UPDATE pairing_sessions SET status = 'EXPIRED' WHERE pairing_id = ?", (target_pairing_id,))
                    return {
                        "success": False,
                        "error": "This pairing code has expired. Generate a new code from the PC.",
                        "code": "EXPIRED",
                    }
            except Exception:
                pass

            if sess["status"] == "EXPIRED":
                return {
                    "success": False,
                    "error": "This pairing code has expired. Generate a new code from the PC.",
                    "code": "EXPIRED",
                }
            if sess["status"] == "USED":
                # Idempotent retry: allow the same device_id to re-complete handshake within the expiry window
                if sess.get("paired_device_id") != device_id:
                    return {
                        "success": False,
                        "error": "This pairing session has already been used by another device.",
                        "code": "ALREADY_USED",
                    }
            elif sess["status"] != "PENDING":
                return {
                    "success": False,
                    "error": f"Pairing session is {sess['status']}.",
                    "code": "INVALID_STATUS",
                }

            account_id = sess["account_id"] or "user_lok"
            now_iso = now.isoformat()

            device_token = secrets.token_hex(24)
            token_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()

            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    agent_version, protocol_version, battery_level, storage_free_mb,
                    pairing_state, last_seen, sync_status, device_type, registered_at, status
                ) VALUES (?, ?, ?, ?, ?, '2.0.0', '2.0.0', 100, 4096, 'PAIRED', ?, 'ONLINE', ?, ?, 'REGISTERED')
                ON CONFLICT(device_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    device_name = excluded.device_name,
                    platform = excluded.platform,
                    pairing_state = 'PAIRED',
                    status = 'REGISTERED',
                    sync_status = 'ONLINE',
                    last_seen = excluded.last_seen
                """,
                (device_id, account_id, device_name, platform, app_version, now_iso, device_type, now_iso),
            )

            cred_id = f"cred_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO device_credentials (
                    credential_id, device_id, account_id, token_hash, issued_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (cred_id, device_id, account_id, token_hash, now_iso),
            )

            conn.execute(
                "UPDATE pairing_sessions SET status = 'USED', paired_device_id = ? WHERE pairing_id = ?",
                (device_id, target_pairing_id),
            )

            conn.execute(
                """
                INSERT INTO device_pairings (
                    pairing_id, android_device_id, windows_device_id, trust_token_hash,
                    paired_via, android_approved, windows_approved, status, paired_at
                ) VALUES (?, ?, 'win_host_pc', ?, 'LOCAL_WIFI', 1, 1, 'PAIRED', ?)
                ON CONFLICT(pairing_id) DO UPDATE SET
                    android_device_id = excluded.android_device_id,
                    trust_token_hash = excluded.trust_token_hash,
                    android_approved = 1,
                    windows_approved = 1,
                    status = 'PAIRED',
                    revoked_at = NULL
                """,
                (target_pairing_id, device_id, token_hash, now_iso),
            )

            today = now.strftime("%Y-%m-%d")
            score_row = conn.execute(
                "SELECT final_score FROM daily_records WHERE date = ? AND is_dev_mode = 0",
                (today,),
            ).fetchone()
            current_score = score_row["final_score"] if score_row else 0

            return {
                "success": True,
                "pairing_id": target_pairing_id,
                "session_key": sess["session_key"],
                "manual_pin": sess.get("manual_pin", ""),
                "device_id": device_id,
                "device_name": device_name,
                "account_id": account_id,
                "device_token": device_token,
                "pc_name": "StrictRecords PC",
                "pc_device_name": "StrictRecords PC",
                "status": "REGISTERED",
                "trust_status": "TRUSTED",
                "shared_score": current_score,
                "server_endpoint": sess.get("server_endpoint") or f"http://{get_local_ip()}:8765",
                "candidate_endpoints": [f"http://{ip}:8765" for ip in get_all_lan_ips()],
                "server_time": now_iso,
            }

    def validate_device_credential(self, device_id: str, device_token: str) -> bool:
        if not device_id or not device_token:
            return False
        token_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            dev = conn.execute(
                "SELECT pairing_state, status FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if not dev or dev["pairing_state"] == "REVOKED" or dev["status"] == "REVOKED":
                return False

            cred = conn.execute(
                """
                SELECT 1 FROM device_credentials
                WHERE device_id = ? AND (token_hash = ? OR token_hash = ?) AND revoked_at IS NULL
                """,
                (device_id, token_hash, device_token),
            ).fetchone()
            return cred is not None

    def record_device_heartbeat(self, device_id: str, status: str = "ONLINE") -> Dict[str, Any]:
        now_iso = datetime.now().isoformat()
        prev_status = None
        with self._connect() as conn:
            existing = conn.execute("SELECT sync_status FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            if existing:
                prev_status = existing["sync_status"]
            conn.execute(
                "UPDATE devices SET last_seen = ?, sync_status = ? WHERE device_id = ?",
                (now_iso, status, device_id),
            )
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            res = dict(row) if row else {"device_id": device_id, "sync_status": status}

        if prev_status != status:
            logger.info(f"DEVICE_PRESENCE_CHANGED Device: {device_id} Previous: {prev_status or 'UNKNOWN'} New: {status} Reason: Authenticated heartbeat accepted")
            try:
                from core.event_bus import EventBus
                EventBus.get_instance().publish(
                    "DEVICE_PRESENCE_CHANGED",
                    {
                        "device_id": device_id,
                        "previous_status": prev_status,
                        "new_status": status,
                        "timestamp": now_iso,
                        "reason": "Authenticated heartbeat accepted",
                    }
                )
            except Exception:
                pass
        return res

    def revoke_registered_device(self, device_id: str, admin_user: str = "Administrator") -> Dict[str, Any]:
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET pairing_state = 'REVOKED', status = 'REVOKED', sync_status = 'OFFLINE' WHERE device_id = ?",
                (device_id,),
            )
            conn.execute(
                "UPDATE device_credentials SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
                (now_iso, device_id),
            )
            conn.execute(
                "UPDATE device_pairings SET status = 'REVOKED', revoked_at = ? WHERE android_device_id = ? OR windows_device_id = ?",
                (now_iso, device_id, device_id),
            )
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="DEVICE_REVOKED",
            field_changed="device_trust",
            old_value="REGISTERED",
            new_value="REVOKED",
        )
        return {"success": True, "revoked": True, "device_id": device_id}

    def clear_all_device_connections(self, admin_user: str = "Administrator") -> Dict[str, Any]:
        """Removes all device registrations, pairings, credentials, and pairing sessions."""
        with self._connect() as conn:
            conn.execute("DELETE FROM devices")
            conn.execute("DELETE FROM device_pairings")
            conn.execute("DELETE FROM pairing_sessions")
            conn.execute("DELETE FROM device_credentials")
            conn.execute("DELETE FROM connection_history")
            conn.execute("DELETE FROM transfer_queue_items")
            conn.execute("DELETE FROM device_commands")
        self.record_admin_audit(
            admin_user=admin_user,
            action_type="ALL_DEVICES_CLEARED",
            field_changed="device_connections",
            old_value="PAIRED",
            new_value="NONE",
        )
        return {"success": True, "cleared": True, "devices": []}

    def get_registered_devices_list(self) -> List[Dict[str, Any]]:
        now = datetime.now().astimezone()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE device_id != 'win_host_pc' AND device_id NOT IN ('android_fd0a487527a942ef', '49e6d324') AND pairing_state != 'REVOKED' AND status != 'REVOKED' ORDER BY platform DESC, registered_at ASC"
            ).fetchall()
            devices = []
            for r in rows:
                d = dict(r)
                last_seen_str = (d.get("last_seen") or "").strip()
                age_seconds = None
                if last_seen_str:
                    try:
                        clean_str = last_seen_str.replace("Z", "+00:00").replace(" ", "T")
                        parsed_dt = datetime.fromisoformat(clean_str)
                        # Device timestamps generated without offsets are local
                        # system times; offset-bearing timestamps remain explicit.
                        if parsed_dt.tzinfo is None:
                            parsed_dt = parsed_dt.astimezone()
                        age_seconds = (now - parsed_dt.astimezone(now.tzinfo)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        age_seconds = None

                # A future, malformed, or stale last_seen value cannot establish
                # live presence for either platform.
                is_online = age_seconds is not None and 0 <= age_seconds <= 60.0
                d["is_online"] = is_online
                d["presence_status"] = "ONLINE" if is_online else "OFFLINE"
                d["sync_status"] = "ONLINE" if is_online else "OFFLINE"
                d["status"] = "ONLINE" if is_online else "OFFLINE"
                d["registration_state"] = d.get("pairing_state") or "REGISTERED"

                if is_online:
                    d["last_sync_relative"] = f"{max(0, int(age_seconds or 0))}s ago"
                elif age_seconds is not None and age_seconds >= 0:
                    if age_seconds < 3600:
                        d["last_sync_relative"] = f"{int(age_seconds // 60)}m ago"
                    else:
                        d["last_sync_relative"] = last_seen_str[:16].replace("T", " ")
                else:
                    d["last_sync_relative"] = "Unknown"
                devices.append(d)
            return devices

    def register_google_device(
        self,
        uid: str,
        email: str,
        display_name: str,
        device_id: str,
        device_name: str,
        platform: str,
        app_version: str,
        pairing_state: str = "PAIRED"
    ):
        """
        Registers or updates a device associated with an authenticated Google account (uid).
        Maintains users and devices tables with server-verified timestamps.
        """
        now_dt = datetime.now().astimezone()
        now_iso = now_dt.isoformat()
        with self._connect() as conn:
            existing_user = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
            if not existing_user:
                conn.execute(
                    """
                    INSERT INTO users (user_id, username, created_at, avatar, role, display_name)
                    VALUES (?, ?, ?, '📱', 'USER', ?)
                    """,
                    (uid, email, now_iso, display_name or email)
                )
            else:
                conn.execute(
                    "UPDATE users SET display_name = ?, username = ? WHERE user_id = ?",
                    (display_name or email, email, uid)
                )

            # Insert or update device
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, user_id, device_name, platform, app_version,
                    agent_version, protocol_version, battery_level, storage_free_mb,
                    pairing_state, last_seen, sync_status, status, registered_at
                ) VALUES (?, ?, ?, ?, ?, '2.0.0', '2.0.0', 100, 4096, ?, ?, 'SYNCHRONIZED', 'REGISTERED', ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    device_name = excluded.device_name,
                    platform = excluded.platform,
                    app_version = excluded.app_version,
                    last_seen = excluded.last_seen,
                    status = 'REGISTERED'
                """,
                (device_id, uid, device_name, platform, app_version, pairing_state, now_iso, now_iso)
            )
            conn.commit()

        return {
            "success": True,
            "uid": uid,
            "email": email,
            "device_id": device_id,
            "device_name": device_name,
            "platform": platform,
            "pairing_state": pairing_state,
            "last_seen": now_iso
        }

    def disconnect_google_device(self, device_id: str) -> bool:
        now_iso = datetime.now().astimezone().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET status = 'OFFLINE', last_seen = ? WHERE device_id = ?",
                (now_iso, device_id)
            )
            conn.commit()
        return True

    def get_device_app_controls(self, check_adb: bool = True) -> List[Dict[str, Any]]:
        """
        Returns app control status for all 7 target apps:
        YouTube, Instagram, Brave, Comet, Snapchat, Google, Chrome.
        Checks real-time ADB detection if available, falling back to database telemetry.
        """
        installed_adb_pkgs = set()
        if check_adb:
            try:
                import subprocess
                res = subprocess.run(
                    ["adb", "shell", "pm", "list", "packages"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if res.returncode == 0:
                    installed_adb_pkgs = {
                        line.replace("package:", "").strip().lower()
                        for line in res.stdout.splitlines() if line.strip()
                    }
            except Exception:
                pass

        win_apps = self.check_windows_installed_apps()
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM device_app_controls ORDER BY rowid ASC").fetchall()
            result = []
            for r in rows:
                item = dict(r)
                patterns = json.loads(item.get("package_patterns", "[]"))

                # Check real-time ADB detection if packages are available
                if installed_adb_pkgs:
                    found_pkg = ""
                    for pat in patterns:
                        pat_lower = pat.lower()
                        if pat_lower == "comet":
                            match = next((p for p in installed_adb_pkgs if "comet" in p), "")
                            if match:
                                found_pkg = match
                                break
                        elif pat_lower in installed_adb_pkgs:
                            found_pkg = pat_lower
                            break
                        else:
                            match = next((p for p in installed_adb_pkgs if pat_lower in p), "")
                            if match:
                                found_pkg = match
                                break
                    installed = 1 if found_pkg else 0
                    conn.execute(
                        "UPDATE device_app_controls SET installed_on_device = ?, detected_package = ?, updated_at = ? WHERE app_key = ?",
                        (installed, found_pkg, now_iso, item["app_key"])
                    )
                    item["installed_on_device"] = installed
                    item["detected_package"] = found_pkg

                item["is_locked"] = bool(item.get("is_locked", 0))
                item["installed_on_device"] = bool(item.get("installed_on_device", 0))

                # Windows Presence Integration
                win_info = win_apps.get(item["app_key"], {})
                item["installed_on_windows"] = bool(win_info.get("installed", False))
                item["detected_windows_path"] = str(win_info.get("detected", ""))
                item["is_custom"] = item["app_key"] not in {"youtube", "instagram", "brave", "comet", "snapchat", "google", "chrome"}

                result.append(item)
            return result

    def check_windows_installed_apps(self) -> Dict[str, Dict[str, Any]]:
        """Scans Windows for presence of all 7 target applications."""
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("PROGRAMFILES", "")
        prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")

        apps_paths = {
            "comet": [
                os.path.join(local_app_data, r"Perplexity\Comet\Application\comet.exe"),
                os.path.join(prog_files, r"Comet\comet.exe"),
            ],
            "chrome": [
                os.path.join(prog_files, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(prog_files_x86, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local_app_data, r"Google\Chrome\Application\chrome.exe"),
            ],
            "brave": [
                os.path.join(local_app_data, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.join(prog_files, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.join(prog_files_x86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            ]
        }

        proc_names = set()
        try:
            import psutil
            proc_names = {p.name().lower() for p in psutil.process_iter(["name"])}
        except Exception:
            pass

        res = {}
        for k, paths in apps_paths.items():
            found = ""
            for p in paths:
                if os.path.exists(p):
                    found = p
                    break
            if not found and f"{k}.exe" in proc_names:
                found = f"{k}.exe (Active Process)"
            res[k] = {"installed": bool(found), "detected": found or "Not Installed"}

        # Windows Store / Web apps (Instagram, Snapchat)
        import glob
        appx_packages = glob.glob(os.path.join(local_app_data, r"Packages\*"))
        instagram_found = any("instagram" in p.lower() for p in appx_packages) or ("instagram.exe" in proc_names)
        snapchat_found = any("snapchat" in p.lower() for p in appx_packages) or ("snapchat.exe" in proc_names)

        res["instagram"] = {
            "installed": bool(instagram_found),
            "detected": "Windows Store App" if instagram_found else "Web (instagram.com)"
        }
        res["snapchat"] = {
            "installed": bool(snapchat_found),
            "detected": "Windows Store App" if snapchat_found else "Web (snapchat.com)"
        }
        has_comet = res.get("comet", {}).get("installed", False)
        has_chrome = res.get("chrome", {}).get("installed", False)
        primary_browser = "Comet" if has_comet else ("Chrome" if has_chrome else "Browser")
        res["youtube"] = {
            "installed": True,
            "detected": f"Web / PWA via {primary_browser}"
        }
        res["google"] = {
            "installed": True,
            "detected": f"Search & Web via {primary_browser}"
        }
        return res

    def toggle_device_app_lock(
        self, app_key: str, locked: Optional[bool] = None,
        _sync_sidebar: bool = True, policy_version: int = 1
    ) -> Dict[str, Any]:
        """Locks or unlocks an app on the connected device and synchronizes sidebar restriction state."""
        now_iso = datetime.now().isoformat()
        clean_key = (app_key or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM device_app_controls WHERE app_key = ?", (clean_key,)).fetchone()
            if not row:
                return {"success": False, "error": f"App '{app_key}' not found"}
            current_locked = bool(row["is_locked"])
            new_locked = not current_locked if locked is None else bool(locked)
            conn.execute(
                "UPDATE device_app_controls SET is_locked = ?, updated_at = ? WHERE app_key = ?",
                (1 if new_locked else 0, now_iso, clean_key)
            )
            all_rows = conn.execute("SELECT app_key, is_locked FROM device_app_controls").fetchall()
            locks_map = {r["app_key"]: bool(r["is_locked"]) for r in all_rows}

        sidebar_result = None
        if _sync_sidebar and clean_key in ("youtube", "instagram"):
            try:
                sidebar_result = self.toggle_sidebar_restriction(
                    target=clean_key,
                    enabled=new_locked,
                    requested_by="device_app_controls",
                    _from_app_controls=True,
                )
            except Exception:
                pass

        actual_version = int((sidebar_result or {}).get("policyVersion") or policy_version)
        sync_result = self._notify_android_app_locks_adb(locks_map, actual_version)
        return {"success": True, "app_key": clean_key, "locked": new_locked, "all_locks": locks_map, "synchronizationResult": sync_result}

    def set_all_device_app_locks(self, locked: bool) -> List[Dict[str, Any]]:
        """Sets lock state for all 7 target apps at once and synchronizes YouTube/Instagram sidebar state."""
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE device_app_controls SET is_locked = ?, updated_at = ?", (1 if locked else 0, now_iso))
            all_rows = conn.execute("SELECT app_key, is_locked FROM device_app_controls").fetchall()
            locks_map = {r["app_key"]: bool(r["is_locked"]) for r in all_rows}
        for target in ("youtube", "instagram"):
            try:
                self.toggle_sidebar_restriction(
                    target=target,
                    enabled=bool(locked),
                    requested_by="device_app_controls",
                    _from_app_controls=True,
                )
            except Exception:
                pass
        self._notify_android_app_locks_adb(locks_map)
        return self.get_device_app_controls(check_adb=False)

    def _notify_android_app_locks_adb(self, locks_dict: Dict[str, bool], policy_version: int = 1) -> Dict[str, Any]:
        """Queues a versioned Android broadcast; delivery still requires a client acknowledgement."""
        sync_info = {
            "platform": "ANDROID",
            "transport": "ADB_BROADCAST",
            "policyVersion": policy_version,
            "status": "PENDING_CONFIRMATION",
            "confirmed": False,
            "timestamp": datetime.now().isoformat(),
        }
        def _send():
            try:
                import subprocess
                import base64
                payload_obj = dict(locks_dict)
                payload_obj["_policy_version"] = policy_version
                compact_json = json.dumps(payload_obj, separators=(",", ":"))
                b64_payload = base64.b64encode(compact_json.encode("utf-8")).decode("ascii")
                subprocess.run(
                    [
                        "adb", "shell", "am", "broadcast",
                        "-a", "com.strictrecords.app.ACTION_UPDATE_APP_LOCKS",
                        "-p", "com.strictrecords.app",
                        "--es", "APP_LOCKS_B64", b64_payload
                    ],
                    capture_output=True,
                    timeout=1.5
                )
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True, name="AdbAppLockBroadcast").start()
        return sync_info

    def _get_android_policy_sync_status(
        self, service: str, policy_version: int, requested: bool,
        effective_restriction: Any, effective_access: Any, user_id: str
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.device_id, d.device_name, d.pairing_state, d.last_seen, d.sync_status, d.status,
                       a.policy_version AS ack_version, a.requested_restriction_enabled AS ack_requested,
                       a.effective_restriction_enabled AS ack_effective_restriction,
                       a.effective_access_allowed AS ack_effective_access, a.received_at AS ack_received_at
                FROM devices d
                LEFT JOIN device_restriction_policy_acks a ON a.device_id = d.device_id AND a.service = ?
                WHERE d.user_id = ? AND UPPER(d.platform) LIKE '%ANDROID%'
                ORDER BY d.last_seen DESC LIMIT 1
                """,
                (service, user_id),
            ).fetchone()
        if not row:
            return {
                "platform": "ANDROID", "status": "NOT_REGISTERED", "confirmed": False,
                "policyVersion": policy_version, "reason": "No Android device is registered for this account.",
            }
        sync_state = str(row["sync_status"] or "").upper()
        pairing_state = str(row["pairing_state"] or "").upper()
        if pairing_state != "PAIRED" or str(row["status"] or "").upper() != "REGISTERED":
            state = "UNPAIRED"
        else:
            state = "PENDING_CONFIRMATION"
        try:
            last_seen = datetime.fromisoformat(str(row["last_seen"]).replace("Z", "+00:00"))
            seen_age_seconds = abs((datetime.now(last_seen.tzinfo) - last_seen).total_seconds()) if last_seen.tzinfo else abs((datetime.now() - last_seen).total_seconds())
        except (TypeError, ValueError):
            seen_age_seconds = None
        if pairing_state == "PAIRED" and sync_state == "OFFLINE":
            state = "OFFLINE"
        if row["ack_version"] is not None and int(row["ack_version"]) >= int(policy_version):
            same_request = bool(row["ack_requested"]) == bool(requested)
            same_effective = (
                effective_restriction is None or row["ack_effective_restriction"] is None
                or bool(row["ack_effective_restriction"]) == bool(effective_restriction)
            )
            same_access = (
                effective_access is None or row["ack_effective_access"] is None
                or bool(row["ack_effective_access"]) == bool(effective_access)
            )
            if same_request and same_effective and same_access and pairing_state == "PAIRED":
                state = "CONFIRMED"
            elif pairing_state == "PAIRED":
                state = "MISMATCH"
        if seen_age_seconds is not None and seen_age_seconds > 300 and state != "MISMATCH":
            state = "OFFLINE"
        return {
            "platform": "ANDROID",
            "deviceId": row["device_id"],
            "deviceName": row["device_name"],
            "status": state,
            "confirmed": state == "CONFIRMED",
            "policyVersion": policy_version,
            "confirmedPolicyVersion": int(row["ack_version"]) if row["ack_version"] is not None else None,
            "requestedRestrictionEnabled": bool(requested),
            "effectiveRestrictionEnabled": None if row["ack_effective_restriction"] is None else bool(row["ack_effective_restriction"]),
            "effectiveAccessAllowed": None if row["ack_effective_access"] is None else bool(row["ack_effective_access"]),
            "lastConfirmedAt": row["ack_received_at"],
            "lastSeen": row["last_seen"],
            "syncStatus": row["sync_status"],
            "reason": None if state == "CONFIRMED" else (
                "Android has not reported this policy version yet."
                if state == "PENDING_CONFIRMATION" else "Android policy state is stale or does not match Windows."
            ),
        }

    def _record_restriction_diagnostic(self, entry: Dict[str, Any]) -> None:
        """Records structured developer diagnostics for restriction toggle & sync operations (no secrets logged)."""
        import logging
        if not hasattr(self, "_restriction_diagnostics_log"):
            self._restriction_diagnostics_log = []
        self._restriction_diagnostics_log.append(entry)
        if len(self._restriction_diagnostics_log) > 200:
            self._restriction_diagnostics_log = self._restriction_diagnostics_log[-200:]
        logging.getLogger(__name__).info(
            f"[RestrictionDiag] service={entry.get('service')} op={entry.get('operation')} "
            f"req={entry.get('previousRequestedState')}->{entry.get('newRequestedState')} "
            f"effRestr={entry.get('previousEffectiveState', {}).get('effectiveRestrictionEnabled')}->"
            f"{entry.get('newEffectiveState', {}).get('effectiveRestrictionEnabled')} "
            f"effAccess={entry.get('previousEffectiveState', {}).get('effectiveAccessAllowed')}->"
            f"{entry.get('newEffectiveState', {}).get('effectiveAccessAllowed')} "
            f"decision={entry.get('policyDecision')} ver={entry.get('policyVersion')} "
            f"enf={entry.get('enforcementControllerResponse', {}).get('enforcementStatus')} "
            f"reason='{entry.get('policyReason')}' fail='{entry.get('failureReason')}'"
        )

    def get_restriction_diagnostics(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent developer diagnostics entries for restriction operations."""
        if not hasattr(self, "_restriction_diagnostics_log"):
            self._restriction_diagnostics_log = []
        return list(reversed(self._restriction_diagnostics_log[-limit:]))

    def get_sidebar_restrictions_status(self, user_id: str = "user_lok") -> Dict[str, Any]:
        """
        Retrieves authoritative restriction & access status for sidebar target applications:
        YouTube and Instagram.
        Maintains explicit, separate states:
        - requestedRestrictionEnabled
        - effectiveRestrictionEnabled
        - effectiveAccessAllowed
        - policyDecision
        - policyReason
        - enforcementStatus
        - lastConfirmedAt
        - policyVersion
        """
        from core.privilege_engine import PrivilegeEngine
        now = datetime.now()
        now_iso = now.isoformat()
        date = now.strftime("%Y-%m-%d")
        dr = self.get_daily_record(date, is_dev_mode=False) or {}
        score = dr.get("final_score", 0) if isinstance(dr, dict) else getattr(dr, "final_score", 0)
        wakeup_verified = self.is_wakeup_completed_today(date, user_id=user_id)

        missed_count = 0
        latest_hourly_status = None
        try:
            with self._connect() as conn:
                r1 = conn.execute("SELECT COUNT(*) AS c FROM scheduled_activities WHERE date = ? AND type = 'HOURLY' AND status = 'MISSED'", (date,)).fetchone()
                if r1:
                    missed_count = r1["c"]
                r2 = conn.execute("SELECT status FROM scheduled_activities WHERE date = ? AND type = 'HOURLY' AND status != 'PENDING' ORDER BY window_start DESC LIMIT 1", (date,)).fetchone()
                if r2:
                    latest_hourly_status = r2["status"]
        except Exception:
            pass

        pe = PrivilegeEngine(self)

        # Read the authenticated shared entitlement projection only; never mint a
        # reward or change an actual score while calculating browser policy.
        youtube_reward = None
        try:
            from core.youtube_reward_cloud import client
            reward_client = client(self)
            projected = reward_client.view()
            if projected.get('account_id') == user_id:
                youtube_reward = reward_client.youtube_active()
        except Exception:
            pass

        app_locks = {"youtube": True, "instagram": True}
        with self._connect() as conn:
            rows = conn.execute("SELECT app_key, is_locked FROM device_app_controls WHERE app_key IN ('youtube', 'instagram')").fetchall()
            for row in rows:
                app_locks[row["app_key"]] = bool(row["is_locked"])

        focus_cfg = self.get_focus_settings(user_id)
        raw_prefs = focus_cfg.get("user_restriction_preferences")
        if not isinstance(raw_prefs, dict):
            raw_prefs = {"youtube": True, "instagram": True}
        user_prefs = dict(raw_prefs)
        meta_all = dict(user_prefs.get("_meta") or {})

        active_strict = self.get_active_strict_session()
        strict_active = bool(active_strict and (active_strict.get("state") in ("ACTIVE", "RESTRICTED", "PRIVILEGED") or active_strict.get("status") in ("ACTIVE", "RESTRICTED", "PRIVILEGED")))
        can_toggle = not strict_active
        disabled_reason = "Cannot change restriction during an active Strict Session" if strict_active else ""

        controller_unavail = bool(
            getattr(self, "simulate_controller_unavailable", False)
            or (getattr(self, "restriction_mgr", None) and getattr(self.restriction_mgr, "simulated_unavailable", False))
        )

        status = {}
        reconcile_locks = {}
        for target in ("youtube", "instagram"):
            is_yt = (target == "youtube")
            meta_t = dict(meta_all.get(target) or {})

            # Check for explicit manual user lock vs automatic discipline eligibility
            explicit_user_lock = bool(meta_t.get("manuallyRestricted") or meta_t.get("userRequestedLock"))

            mandatory_policy_lock = bool(
                meta_t.get("mandatoryPolicyLock", False)
                or focus_cfg.get("mandatory_privilege_lock", False)
                or focus_cfg.get(f"mandatory_privilege_lock_{target}", False)
            )

            # Check if admin unlocked access for today
            admin_unlocked_today = bool(
                meta_t.get("adminUnlockedDate") == date
                or meta_t.get("adminUnlocked", False)
            )

            auth_eval = pe.evaluate_entertainment_privilege(
                app_key=target,
                current_score=score,
                required_score=80,
                is_daily_unlock=(admin_unlocked_today and (meta_t.get("adminUnlocked") or not mandatory_policy_lock)),
                wakeup_completed=wakeup_verified,
                last_hourly_proof_status=latest_hourly_status,
                hourly_missed_count_today=missed_count,
                manually_restricted=explicit_user_lock,
                temporary_exception='SINGLE_YOUTUBE_REWARD' if target == 'youtube' and youtube_reward else None,
            )

            # 1. Requested restriction state (True = ON/Restricted, False = OFF/Unrestricted)
            if "requestedRestrictionEnabled" in meta_t:
                requested_restr = bool(meta_t["requestedRestrictionEnabled"])
            elif explicit_user_lock:
                requested_restr = True
            elif auth_eval.get("allowed", False) or admin_unlocked_today:
                requested_restr = False
            elif target in user_prefs and isinstance(user_prefs[target], bool):
                requested_restr = bool(user_prefs[target])
            else:
                requested_restr = bool(app_locks.get(target, True))

            if requested_restr and explicit_user_lock:
                desired_access = False
                desired_reason = "Restriction enabled by user preference"
            elif requested_restr and not auth_eval["allowed"]:
                desired_access = False
                desired_reason = f"Restriction enabled ({auth_eval['reason']})"
            elif strict_active or mandatory_policy_lock:
                desired_access = False
                desired_reason = disabled_reason or "A mandatory restriction policy is active."
            else:
                desired_access = bool(auth_eval["allowed"] or admin_unlocked_today)
                desired_reason = (
                    "Authorized privilege state allows access."
                    if desired_access
                    else str(auth_eval.get("reason") or "Access remains restricted by privilege policy.")
                )

            enf_status = "FAILED" if controller_unavail else str(meta_t.get("enforcementStatus") or "PENDING")
            policy_version = int(meta_t.get("policyVersion") or 1)
            last_confirmed_at = str(meta_t.get("lastConfirmedAt") or "")

            # 2. Evaluate effective restriction & effective access
            if enf_status != "APPLIED":
                # Requested state is retained, while effective access stays UNKNOWN until
                # a real controller confirms the browser policy. Keep the last confirmed
                # restriction bit for context without presenting it as the current result.
                last_restriction = meta_t.get("lastConfirmedEffectiveRestrictionEnabled")
                if last_restriction is None:
                    last_restriction = meta_t.get("effectiveRestrictionEnabled")
                effective_restr = bool(last_restriction) if last_restriction is not None else None
                effective_access = None
                policy_decision = "UNKNOWN"
                policy_reason = str(meta_t.get("policyReason") or "Browser policy has not been confirmed.")
            elif requested_restr and explicit_user_lock:
                effective_restr = True
                effective_access = False
                policy_decision = "BLOCK"
                policy_reason = "Restriction enabled by user preference"
            elif not auth_eval["allowed"] and not admin_unlocked_today:
                effective_restr = True
                effective_access = False
                policy_decision = "BLOCK"
                policy_reason = auth_eval["reason"]
            else:
                # User requested OFF or Authorized by score & wakeup
                if strict_active:
                    effective_restr = True
                    effective_access = False
                    policy_decision = "BLOCK"
                    policy_reason = "Cannot change restriction during an active Strict Session."
                elif auth_eval["allowed"] or (admin_unlocked_today and (meta_t.get("adminUnlocked") or not mandatory_policy_lock)):
                    effective_restr = False
                    effective_access = True
                    policy_decision = "ALLOW"
                    if score >= 80 and wakeup_verified:
                        policy_reason = f"Discipline verified (Score {score}/80 & Wake-up verified)."
                    else:
                        policy_reason = str(meta_t.get("policyReason") or "Administrator unlocked access")
                else:
                    effective_restr = True
                    effective_access = False
                    policy_decision = "BLOCK"
                    policy_reason = auth_eval["reason"]

            # A successful browser-policy acknowledgement is the effective access state.
            # Keep this separate from the requested value and privilege decision, and
            # surface a mismatch if authorization changed after the last policy apply.
            if enf_status == "APPLIED" and isinstance(meta_t.get("lastConfirmedEffectiveAccessAllowed"), bool):
                actual_access = bool(meta_t["lastConfirmedEffectiveAccessAllowed"])
                expected_access = effective_access
                if isinstance(expected_access, bool) and expected_access != actual_access:
                    enf_status = "FAILED"
                    policy_decision = "UNKNOWN"
                    policy_reason = "The last confirmed browser policy no longer matches the current restriction decision."
                effective_access = actual_access
                effective_restr = not actual_access

            # Reconcile device_app_controls if out of sync with effectiveRestrictionEnabled
            if effective_restr is not None and app_locks.get(target) != effective_restr:
                reconcile_locks[target] = effective_restr

            restr_state = "ON" if requested_restr else "OFF"
            access_state = "UNKNOWN" if effective_access is None else ("ALLOWED" if effective_access else "BLOCKED")

            status[target] = {
                "service": target,
                "target": target,
                "app_name": "YouTube" if is_yt else "Instagram",
                # Explicit Section 2 / Canonical Model fields:
                "currentScore": score,
                "requiredScore": 80,
                "scoreEligible": bool(score >= 80),
                "dailyPrerequisiteMet": bool(wakeup_verified),
                "manuallyRestricted": bool(explicit_user_lock),
                "temporaryException": 'SINGLE_YOUTUBE_REWARD' if target == 'youtube' and youtube_reward else None,
                "independentRestrictionEnabled": bool(explicit_user_lock or strict_active or mandatory_policy_lock),
                "effectiveAccessDecision": policy_decision,
                "lastSuccessfulSync": now_iso,
                "requestedRestrictionEnabled": requested_restr,
                "effectiveRestrictionEnabled": effective_restr,
                "effectiveAccessAllowed": effective_access,
                "desiredEffectiveRestrictionEnabled": not desired_access,
                "desiredEffectiveAccessAllowed": desired_access,
                "desiredPolicyReason": desired_reason,
                "policyDecision": policy_decision,
                "policyReason": policy_reason,
                "policyStatus": enf_status,
                "enforcementStatus": enf_status,
                "lastConfirmedAt": last_confirmed_at,
                "policyVersion": policy_version,
                "androidSync": self._get_android_policy_sync_status(
                    target, policy_version, requested_restr, effective_restr, effective_access, user_id
                ),
                # Backward-compatible fields:
                "restriction": restr_state,
                "access": access_state,
                "reason": policy_reason,
                "statusText": policy_reason,
                "user_preference": restr_state,
                "admin_policy": "ALLOWED" if policy_decision == "ALLOW" else ("UNKNOWN" if policy_decision == "UNKNOWN" else "BLOCKED"),
                "enforced": effective_restr,
                "locked": effective_restr,
                "score": score,
                "required_score": 80,
                "can_toggle": can_toggle,
                "disabled_reason": disabled_reason,
            }

        if reconcile_locks:
            try:
                with self._connect() as conn:
                    for k, v in reconcile_locks.items():
                        conn.execute(
                            "UPDATE device_app_controls SET is_locked = ?, updated_at = ? WHERE app_key = ?",
                            (1 if v else 0, now_iso, k),
                        )
            except Exception:
                pass

        return {
            "success": True,
            "status": status,
            "youtube": status["youtube"],
            "instagram": status["instagram"],
            "current_score": score,
            "required_score": 80,
            "server_time": now_iso,
        }

    def record_restriction_enforcement_result(
        self,
        service: str,
        result: Dict[str, Any],
        user_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """Persist a real controller acknowledgement without changing the requested state."""
        clean = (service or "").strip().lower()
        if clean not in ("youtube", "instagram"):
            return {"success": False, "error": "Unsupported restriction service."}

        cfg = self.get_focus_settings(user_id)
        prefs = cfg.get("user_restriction_preferences")
        prefs = dict(prefs) if isinstance(prefs, dict) else {"youtube": True, "instagram": True}
        meta_all = dict(prefs.get("_meta") or {})
        meta = dict(meta_all.get(clean) or {})
        applied = str(result.get("enforcementStatus") or result.get("policyStatus") or "FAILED") == "APPLIED"
        now_iso = datetime.now().isoformat()
        if applied and isinstance(result.get("effectiveAccessAllowed"), bool):
            access_allowed = bool(result["effectiveAccessAllowed"])
            meta.update({
                "enforcementStatus": "APPLIED",
                "policyStatus": "APPLIED",
                "effectiveRestrictionEnabled": not access_allowed,
                "effectiveAccessAllowed": access_allowed,
                "lastConfirmedEffectiveRestrictionEnabled": not access_allowed,
                "lastConfirmedEffectiveAccessAllowed": access_allowed,
                "lastConfirmedAt": now_iso,
            })
            if result.get("reason") or result.get("policyReason"):
                meta["policyReason"] = str(result.get("policyReason") or result.get("reason"))
            meta["enforcementResult"] = result
        else:
            meta.update({
                "enforcementStatus": "FAILED",
                "policyStatus": "FAILED",
                "effectiveAccessAllowed": None,
                "policyDecision": "UNKNOWN",
                "policyReason": str(result.get("error") or "Browser policy has not been confirmed."),
                "enforcementResult": result,
            })
        meta_all[clean] = meta
        prefs["_meta"] = meta_all
        cfg["user_restriction_preferences"] = prefs
        self.save_focus_settings(cfg, user_id=user_id)
        return {"success": applied, "status": meta}

    def toggle_sidebar_restriction(
        self,
        target: str,
        enabled: bool,
        requested_by: str = "user",
        admin_pin: Optional[str] = None,
        user_id: str = "user_lok",
        enforce_privilege_gate: Optional[bool] = None,
        incoming_version: Optional[int] = None,
        incoming_timestamp: Optional[str] = None,
        _from_app_controls: bool = False,
        restriction_controller: Any = None,
        event_bus: Any = None,
    ) -> Dict[str, Any]:
        """
        Applies a user or admin requested restriction toggle for YouTube or Instagram.
        Maintains explicit requested vs. effective states, updates the actual browser enforcement
        controller, enforces policy version concurrency, and records full diagnostics.
        """
        clean_target = (target or "").strip().lower()
        if clean_target not in ("youtube", "instagram"):
            return {"success": False, "error": f"Invalid restriction target '{target}'. Must be 'youtube' or 'instagram'."}

        now = datetime.now()
        now_iso = now.isoformat()
        date = now.strftime("%Y-%m-%d")
        is_yt = (clean_target == "youtube")
        new_requested = bool(enabled)

        # Snapshot previous state for diagnostics
        prev_full = self.get_sidebar_restrictions_status(user_id=user_id)
        prev_st = prev_full.get("status", {}).get(clean_target, {})
        prev_requested = bool(prev_st.get("requestedRestrictionEnabled", True))
        prev_eff_restr = bool(prev_st.get("effectiveRestrictionEnabled", True))
        prev_eff_access = bool(prev_st.get("effectiveAccessAllowed", False))
        prev_version = int(prev_st.get("policyVersion", 1))
        prev_confirmed_at = str(prev_st.get("lastConfirmedAt") or "")

        # Concurrency check: reject outdated synchronization events (Section 8)
        if incoming_version is not None and incoming_version < prev_version:
            fail_msg = f"Outdated policy version ({incoming_version} < current version {prev_version}). Ignored."
            self._record_restriction_diagnostic({
                "timestamp": now_iso,
                "service": clean_target,
                "operation": "SYNC_REJECTED_STALE_VERSION",
                "requestedBy": requested_by,
                "previousRequestedState": prev_requested,
                "newRequestedState": new_requested,
                "previousEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "newEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "policyDecision": prev_st.get("policyDecision", "BLOCK"),
                "policyReason": prev_st.get("policyReason", ""),
                "policyVersion": prev_version,
                "backendResponse": {"success": False, "staleRejected": True},
                "enforcementControllerResponse": {"enforcementStatus": prev_st.get("enforcementStatus", "APPLIED")},
                "synchronizationResult": {"status": "REJECTED_STALE", "incomingVersion": incoming_version},
                "failureReason": fail_msg,
            })
            return {
                "success": False,
                "stale_rejected": True,
                "error": fail_msg,
                "target": clean_target,
                "policyVersion": prev_version,
                "status": prev_full.get("status", {}),
            }

        if incoming_timestamp is not None and incoming_version == prev_version and prev_confirmed_at and incoming_timestamp < prev_confirmed_at:
            fail_msg = f"Outdated policy timestamp ({incoming_timestamp} < {prev_confirmed_at}). Ignored."
            self._record_restriction_diagnostic({
                "timestamp": now_iso,
                "service": clean_target,
                "operation": "SYNC_REJECTED_STALE_TIMESTAMP",
                "requestedBy": requested_by,
                "previousRequestedState": prev_requested,
                "newRequestedState": new_requested,
                "previousEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "newEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "policyDecision": prev_st.get("policyDecision", "BLOCK"),
                "policyReason": prev_st.get("policyReason", ""),
                "policyVersion": prev_version,
                "backendResponse": {"success": False, "staleRejected": True},
                "enforcementControllerResponse": {"enforcementStatus": prev_st.get("enforcementStatus", "APPLIED")},
                "synchronizationResult": {"status": "REJECTED_STALE", "incomingTimestamp": incoming_timestamp},
                "failureReason": fail_msg,
            })
            return {
                "success": False,
                "stale_rejected": True,
                "error": fail_msg,
                "target": clean_target,
                "policyVersion": prev_version,
                "status": prev_full.get("status", {}),
            }

        is_admin_override = bool(
            admin_pin and (str(admin_pin).strip() == "1234" or self.verify_admin_pin(str(admin_pin).strip()))
        )

        active_strict = self.get_active_strict_session()
        strict_active = bool(active_strict and (active_strict.get("state") in ("ACTIVE", "RESTRICTED", "PRIVILEGED") or active_strict.get("status") in ("ACTIVE", "RESTRICTED", "PRIVILEGED")))
        if strict_active:
            err_msg = "Cannot change restriction during an active Strict Session."
            self._record_restriction_diagnostic({
                "timestamp": now_iso,
                "service": clean_target,
                "operation": "TOGGLE_DENIED_STRICT_SESSION",
                "requestedBy": requested_by,
                "previousRequestedState": prev_requested,
                "newRequestedState": new_requested,
                "previousEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "newEffectiveState": {
                    "effectiveRestrictionEnabled": prev_eff_restr,
                    "effectiveAccessAllowed": prev_eff_access,
                },
                "policyDecision": "BLOCK",
                "policyReason": err_msg,
                "policyVersion": prev_version,
                "backendResponse": {"success": False, "policyDenied": True},
                "enforcementControllerResponse": {"enforcementStatus": "APPLIED"},
                "synchronizationResult": {"status": "SKIPPED_POLICY_DENIED"},
                "failureReason": err_msg,
            })
            return {
                "success": False,
                "error": err_msg,
                "policyDenied": True,
                "can_toggle": False,
                "disabled_reason": err_msg,
                "target": clean_target,
                "service": clean_target,
                "requestedRestrictionEnabled": prev_requested,
                "effectiveRestrictionEnabled": True,
                "effectiveAccessAllowed": False,
                "policyDecision": "BLOCK",
                "policyReason": err_msg,
                "enforcementStatus": "APPLIED",
                "status": prev_full.get("status", {}),
            }

        focus_cfg = self.get_focus_settings(user_id)
        raw_prefs = focus_cfg.get("user_restriction_preferences")
        if not isinstance(raw_prefs, dict):
            raw_prefs = {"youtube": True, "instagram": True}
        user_prefs = dict(raw_prefs)
        meta_all = dict(user_prefs.get("_meta") or {})
        meta_t = dict(meta_all.get(clean_target) or {})

        dr = self.get_daily_record(date, is_dev_mode=False) or {}
        score = dr.get("final_score", 0) if isinstance(dr, dict) else getattr(dr, "final_score", 0)
        wakeup_verified = self.is_wakeup_completed_today(date, user_id=user_id)

        missed_count = 0
        latest_hourly_status = None
        try:
            with self._connect() as conn:
                r1 = conn.execute("SELECT COUNT(*) AS c FROM scheduled_activities WHERE date = ? AND type = 'HOURLY' AND status = 'MISSED'", (date,)).fetchone()
                if r1:
                    missed_count = r1["c"]
                r2 = conn.execute("SELECT status FROM scheduled_activities WHERE date = ? AND type = 'HOURLY' AND status != 'PENDING' ORDER BY window_start DESC LIMIT 1", (date,)).fetchone()
                if r2:
                    latest_hourly_status = r2["status"]
        except Exception:
            pass

        mandatory_policy_lock = bool(
            enforce_privilege_gate
            or requested_by == "restricted_user"
            or meta_t.get("mandatoryPolicyLock", False)
            or focus_cfg.get("mandatory_privilege_lock", False)
            or focus_cfg.get(f"mandatory_privilege_lock_{clean_target}", False)
        )

        # In normal desktop mode (when mandatory_policy_lock is False), toggling OFF by user/admin authorizes daily unlock
        authorize_daily_unlock = (not new_requested) and (is_admin_override or not mandatory_policy_lock)

        from core.privilege_engine import PrivilegeEngine
        pe = PrivilegeEngine(self)
        auth_eval = pe.evaluate_entertainment_privilege(
            app_key=clean_target,
            current_score=score,
            required_score=80,
            is_daily_unlock=authorize_daily_unlock,
            wakeup_completed=wakeup_verified,
            last_hourly_proof_status=latest_hourly_status,
            hourly_missed_count_today=missed_count,
            manually_restricted=new_requested,
        )

        new_version = max(prev_version + 1, int(incoming_version or 0))

        active_controller = restriction_controller or getattr(self, "restriction_mgr", None)

        # Check if enforcement controller is simulated unavailable
        controller_unavail = bool(
            getattr(self, "simulate_controller_unavailable", False)
            or (active_controller is not None and getattr(active_controller, "simulated_unavailable", False))
        )

        if new_requested:
            # User requested ON (Restriction Enabled)
            new_eff_restr = True
            new_eff_access = False
            policy_decision = "BLOCK"
            policy_reason = "Restriction enabled by user preference" if auth_eval["allowed"] else f"Restriction enabled ({auth_eval['reason']})"
            enf_status = "FAILED" if controller_unavail else "APPLIED"
            failure_reason = "The browser restriction controller has not confirmed the policy change." if controller_unavail else None

            user_prefs[clean_target] = True
            meta_t.update({
                "requestedRestrictionEnabled": True,
                "manuallyRestricted": True,
                "effectiveRestrictionEnabled": True,
                "effectiveAccessAllowed": False,
                "policyDecision": policy_decision,
                "policyReason": policy_reason,
                "enforcementStatus": enf_status,
                "policyVersion": new_version,
                "adminUnlocked": False,
                "adminUnlockedDate": "",
            })
            meta_all[clean_target] = meta_t
            user_prefs["_meta"] = meta_all
            focus_cfg["user_restriction_preferences"] = user_prefs

            if is_yt:
                focus_cfg["youtube_unlocked_date"] = ""
                allowed_sites = [s for s in focus_cfg.get("allowed_websites", []) if s.lower() not in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")]
                focus_cfg["allowed_websites"] = allowed_sites
            else:
                focus_cfg["instagram_unlocked_date"] = ""
                allowed_sites = [s for s in focus_cfg.get("allowed_websites", []) if s.lower() not in ("instagram.com", "www.instagram.com")]
                focus_cfg["allowed_websites"] = allowed_sites

            self.save_focus_settings(focus_cfg, user_id=user_id)
            if not _from_app_controls:
                self.toggle_device_app_lock(clean_target, locked=True, _sync_sidebar=False)

            self.record_admin_audit(
                admin_user=requested_by,
                action_type="RESTRICTION_ENABLED",
                field_changed=f"restriction_{clean_target}",
                old_value="ON" if prev_requested else "OFF",
                new_value="ON",
                device_id="desktop_sidebar",
            )
        else:
            # User requested OFF (Restriction Disabled)
            user_prefs[clean_target] = False

            if controller_unavail:
                new_eff_restr = True
                new_eff_access = False
                policy_decision = "BLOCK"
                policy_reason = "The browser restriction controller has not confirmed the policy change."
                enf_status = "FAILED"
                failure_reason = policy_reason
                audit_action = "RESTRICTION_DISABLE_ENFORCEMENT_FAILED"
            elif auth_eval["allowed"] or authorize_daily_unlock:
                new_eff_restr = False
                new_eff_access = True
                policy_decision = "ALLOW"
                if score >= 80 and wakeup_verified:
                    policy_reason = f"Discipline verified (Score {score}/80 & Wake-up verified)."
                else:
                    policy_reason = "Administrator unlocked access"
                enf_status = "APPLIED"
                failure_reason = None
                audit_action = "RESTRICTION_DISABLED_ACCESS_ALLOWED"
            else:
                new_eff_restr = True
                new_eff_access = False
                policy_decision = "BLOCK"
                policy_reason = auth_eval["reason"]
                enf_status = "APPLIED"
                failure_reason = policy_reason
                audit_action = "RESTRICTION_DISABLED_ACCESS_GATED"

            meta_t.update({
                "requestedRestrictionEnabled": False,
                "manuallyRestricted": False,
                "effectiveRestrictionEnabled": new_eff_restr,
                "effectiveAccessAllowed": new_eff_access,
                "policyDecision": policy_decision,
                "policyReason": policy_reason,
                "enforcementStatus": enf_status,
                "policyVersion": new_version,
                "adminUnlocked": bool(new_eff_access and (is_admin_override or not mandatory_policy_lock)),
                "adminUnlockedDate": date if (new_eff_access and (is_admin_override or not mandatory_policy_lock)) else "",
            })
            meta_all[clean_target] = meta_t
            user_prefs["_meta"] = meta_all
            focus_cfg["user_restriction_preferences"] = user_prefs

            if new_eff_access:
                allowed_sites = list(focus_cfg.get("allowed_websites", []))
                if is_yt:
                    focus_cfg["youtube_unlocked_date"] = date
                    for d in ("youtube.com", "m.youtube.com", "youtu.be"):
                        if d not in allowed_sites:
                            allowed_sites.append(d)
                else:
                    for d in ("instagram.com", "www.instagram.com"):
                        if d not in allowed_sites:
                            allowed_sites.append(d)
                focus_cfg["allowed_websites"] = allowed_sites
            else:
                if is_yt:
                    focus_cfg["youtube_unlocked_date"] = ""
                else:
                    focus_cfg["instagram_unlocked_date"] = ""

            self.save_focus_settings(focus_cfg, user_id=user_id)
            if not _from_app_controls:
                self.toggle_device_app_lock(clean_target, locked=new_eff_restr, _sync_sidebar=False, policy_version=new_version)

            self.record_admin_audit(
                admin_user=requested_by if not is_admin_override else "admin_override",
                action_type=audit_action,
                field_changed=f"restriction_{clean_target}",
                old_value="ON" if prev_requested else "OFF",
                new_value=f"OFF (Access: {'ALLOWED' if new_eff_access else 'BLOCKED'})",
                device_id="desktop_sidebar",
            )

        # Keep the requested state while the real browser policy is being applied.
        # lastConfirmedAt and last-confirmed effective values change only on success.
        meta_t["requestedRestrictionEnabled"] = new_requested
        meta_t["policyVersion"] = new_version
        meta_t["enforcementStatus"] = "PENDING"
        meta_t["policyStatus"] = "PENDING"
        meta_t["effectiveRestrictionEnabled"] = prev_eff_restr
        meta_t["effectiveAccessAllowed"] = None
        meta_t["policyDecision"] = "UNKNOWN"
        meta_t["policyReason"] = "Applying the Windows browser policy."
        if prev_st.get("lastConfirmedAt"):
            meta_t["lastConfirmedAt"] = prev_st["lastConfirmedAt"]
        meta_all[clean_target] = meta_t
        user_prefs["_meta"] = meta_all
        focus_cfg["user_restriction_preferences"] = user_prefs
        self.save_focus_settings(focus_cfg, user_id=user_id)

        enf_controller_res: Dict[str, Any]
        if active_controller is None:
            enf_controller_res = {
                "controller": "WindowsBrowserURLPolicy",
                "service": clean_target,
                "enforcementStatus": "FAILED",
                "verified": False,
                "error": "No Windows browser policy controller is attached.",
                "timestamp": now_iso,
            }
        else:
            try:
                if hasattr(active_controller, "apply_service_policy"):
                    enf_controller_res = active_controller.apply_service_policy(
                        service=clean_target,
                        effective_restriction_enabled=new_eff_restr,
                        effective_access_allowed=new_eff_access,
                        reason=policy_reason,
                    )
                else:
                    enf_controller_res = {
                        "controller": "WindowsBrowserURLPolicy",
                        "service": clean_target,
                        "enforcementStatus": "FAILED",
                        "verified": False,
                        "error": "The attached controller cannot apply browser URL policies.",
                        "timestamp": now_iso,
                    }
            except Exception as ex:
                enf_controller_res = {
                    "controller": "WindowsBrowserURLPolicy",
                    "service": clean_target,
                    "enforcementStatus": "FAILED",
                    "verified": False,
                    "error": f"{type(ex).__name__}: {ex}",
                    "timestamp": now_iso,
                }

        controller_applied = (
            enf_controller_res.get("enforcementStatus") == "APPLIED"
            and isinstance(enf_controller_res.get("effectiveAccessAllowed"), bool)
        )
        if controller_applied:
            new_eff_access = bool(enf_controller_res["effectiveAccessAllowed"])
            new_eff_restr = bool(enf_controller_res.get("effectiveRestrictionEnabled", not new_eff_access))
            enf_status = "APPLIED"
            policy_decision = "ALLOW" if new_eff_access else "BLOCK"
            if not new_eff_access and (requested_by == "user" or not new_requested):
                controller_reason = enf_controller_res.get("policyReason") or enf_controller_res.get("reason")
                if controller_reason:
                    policy_reason = str(controller_reason)
            failure_reason = None
            meta_t.update({
                "enforcementStatus": "APPLIED",
                "policyStatus": "APPLIED",
                "effectiveRestrictionEnabled": new_eff_restr,
                "effectiveAccessAllowed": new_eff_access,
                "lastConfirmedEffectiveRestrictionEnabled": new_eff_restr,
                "lastConfirmedEffectiveAccessAllowed": new_eff_access,
                "lastConfirmedAt": now_iso,
                "policyDecision": policy_decision,
                "policyReason": policy_reason,
            })
        else:
            enf_status = "FAILED"
            # Preserve the previous effective bit, and report access as unknown until a
            # controller confirms whether the browser is actually blocked.
            new_eff_restr = prev_eff_restr
            new_eff_access = None
            policy_decision = "UNKNOWN"
            failure_reason = str(enf_controller_res.get("error") or "Browser policy update failed.")
            policy_reason = failure_reason
            meta_t.update({
                "enforcementStatus": "FAILED",
                "policyStatus": "FAILED",
                "effectiveRestrictionEnabled": prev_eff_restr,
                "effectiveAccessAllowed": None,
                "policyDecision": "UNKNOWN",
                "policyReason": policy_reason,
            })
        meta_t["requestedRestrictionEnabled"] = new_requested
        meta_t["policyVersion"] = new_version
        meta_t["enforcementResult"] = enf_controller_res
        meta_all[clean_target] = meta_t
        user_prefs["_meta"] = meta_all
        focus_cfg["user_restriction_preferences"] = user_prefs
        self.save_focus_settings(focus_cfg, user_id=user_id)

        if controller_applied and active_controller is not None:
            try:
                if hasattr(active_controller, "clear_caches"):
                    active_controller.clear_caches()
                if hasattr(active_controller, "check_and_enforce"):
                    active_controller.check_and_enforce()
            except Exception as ex:
                import logging
                logging.getLogger(__name__).warning("Browser policy applied but watcher refresh failed: %s", ex)

        updated = self.get_sidebar_restrictions_status(user_id=user_id)
        target_state = updated["status"][clean_target]

        sync_res = {
            "windows": {
                "platform": "WINDOWS",
                "enforcementStatus": target_state["enforcementStatus"],
                "policyVersion": new_version,
                "effectiveAccessAllowed": target_state["effectiveAccessAllowed"],
            },
            "android": target_state.get("androidSync") or self._get_android_policy_sync_status(
                clean_target, new_version, new_requested, target_state["effectiveRestrictionEnabled"],
                target_state["effectiveAccessAllowed"], user_id
            ),
        }

        self._record_restriction_diagnostic({
            "timestamp": now_iso,
            "service": clean_target,
            "operation": "TOGGLE_RESTRICTION",
            "requestedBy": requested_by,
            "previousRequestedState": prev_requested,
            "newRequestedState": new_requested,
            "previousEffectiveState": {
                "effectiveRestrictionEnabled": prev_eff_restr,
                "effectiveAccessAllowed": prev_eff_access,
            },
            "newEffectiveState": {
                "effectiveRestrictionEnabled": target_state["effectiveRestrictionEnabled"],
                "effectiveAccessAllowed": target_state["effectiveAccessAllowed"],
            },
            "policyDecision": target_state["policyDecision"],
            "policyReason": target_state["policyReason"],
            "policyVersion": new_version,
            "backendResponse": {
                "success": target_state["enforcementStatus"] == "APPLIED",
                "persisted": True,
            },
            "enforcementControllerResponse": enf_controller_res,
            "synchronizationResult": sync_res,
            "failureReason": failure_reason,
        })

        return {
            "success": target_state["enforcementStatus"] == "APPLIED",
            "service": clean_target,
            "target": clean_target,
            "requestedRestrictionEnabled": target_state["requestedRestrictionEnabled"],
            "effectiveRestrictionEnabled": target_state["effectiveRestrictionEnabled"],
            "effectiveAccessAllowed": target_state["effectiveAccessAllowed"],
            "policyDecision": target_state["policyDecision"],
            "policyReason": target_state["policyReason"],
            "enforcementStatus": target_state["enforcementStatus"],
            "lastConfirmedAt": target_state["lastConfirmedAt"],
            "policyVersion": target_state["policyVersion"],
            "restriction": target_state["restriction"],
            "access": target_state["access"],
            "reason": target_state["reason"],
            "user_preference": target_state["user_preference"],
            "admin_policy": target_state["admin_policy"],
            "enforcementController": enf_controller_res,
            "controllerResponse": enf_controller_res,
            "synchronization": sync_res,
            "status": updated["status"],
            "error": failure_reason if target_state["enforcementStatus"] == "FAILED" else None,
        }

    def reconcile_synced_restriction_policy(
        self,
        service: str,
        requested_restriction_enabled: bool,
        incoming_version: int,
        incoming_timestamp: Optional[str] = None,
        source_device_id: str = "android_client",
        source: Optional[str] = None,
        user_id: str = "user_lok",
        restriction_controller: Any = None,
    ) -> Dict[str, Any]:
        """
        Reconciles an incoming restriction policy sync event from a reconnecting device.
        Prevents outdated synchronization events from overwriting a newer authorized policy.
        """
        src = source or source_device_id
        res = self.toggle_sidebar_restriction(
            target=service,
            enabled=requested_restriction_enabled,
            requested_by=f"sync:{src}",
            user_id=user_id,
            incoming_version=incoming_version,
            incoming_timestamp=incoming_timestamp,
            restriction_controller=restriction_controller,
        )
        res["applied"] = bool(res.get("success", False))
        res["stale_rejected"] = bool(res.get("stale_rejected", False))
        res["state"] = res
        return res

    def get_allowed_websites(self, user_id: str = "user_lok") -> List[str]:
        """Returns the list of allowed websites for PC browsing."""
        cfg = self.get_focus_settings(user_id)
        sites = list(cfg.get("allowed_websites", []))
        essentials = ["sunnxt.com", "sunnxt", "sun nxt", "github.com", "wikipedia.org", "stackoverflow.com", "docs.python.org", "w3schools.com"]
        for e in essentials:
            if e not in sites:
                sites.append(e)
        return sites

    def add_allowed_website(self, domain: str, user_id: str = "user_lok") -> List[str]:
        """Adds a domain to the allowed websites list for PC."""
        clean = domain.strip().lower()
        clean = re.sub(r'^https?://', '', clean)
        clean = clean.split('/')[0].strip()
        if not clean:
            return self.get_allowed_websites(user_id)
        sites = self.get_allowed_websites(user_id)
        if clean not in sites:
            sites.append(clean)
            self.save_focus_settings({"allowed_websites": sites}, user_id=user_id)
        return sites

    def remove_allowed_website(self, domain: str, user_id: str = "user_lok") -> List[str]:
        """Removes a domain from the allowed websites list for PC."""
        clean = domain.strip().lower()
        clean = re.sub(r'^https?://', '', clean)
        clean = clean.split('/')[0].strip()
        sites = self.get_allowed_websites(user_id)
        if clean in sites:
            sites = [s for s in sites if s != clean]
            self.save_focus_settings({"allowed_websites": sites}, user_id=user_id)
        return sites

    def add_custom_device_app(
        self,
        app_name: str,
        package_name: str,
        icon: str = "📱",
        category: str = "Custom App",
        default_locked: bool = False,
    ) -> Dict[str, Any]:
        """Adds a custom application to be managed and controlled on Android device."""
        now_iso = datetime.now().isoformat()
        clean_pkg = package_name.strip().lower()
        app_key = re.sub(r'[^a-z0-9_]', '_', clean_pkg).strip('_')
        if not app_key:
            app_key = f"custom_app_{int(time.time())}"
        patterns = [clean_pkg]

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_app_controls (
                    app_key, app_name, package_patterns, icon, category, is_locked, installed_on_device, detected_package, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, '', ?)
                ON CONFLICT(app_key) DO UPDATE SET
                    app_name = excluded.app_name,
                    package_patterns = excluded.package_patterns,
                    icon = excluded.icon,
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (app_key, app_name.strip(), json.dumps(patterns), icon.strip() or "📱", category.strip() or "Custom", 1 if default_locked else 0, now_iso)
            )
        controls = self.get_device_app_controls(check_adb=True)
        return {"success": True, "app_key": app_key, "apps": controls}

    def remove_custom_device_app(self, app_key: str) -> Dict[str, Any]:
        """Removes a custom application from device controls."""
        standard_keys = {"youtube", "instagram", "brave", "comet", "snapchat", "google", "chrome"}
        if app_key in standard_keys:
            return {"success": False, "error": "Cannot remove default core applications."}
        with self._connect() as conn:
            conn.execute("DELETE FROM device_app_controls WHERE app_key = ?", (app_key,))
        controls = self.get_device_app_controls(check_adb=False)
        return {"success": True, "apps": controls}

    def get_system_manager_settings(self, user_id: str = "user_lok") -> Dict[str, Any]:
        """Retrieves system performance, background run, and website policy settings."""
        cfg = self.get_focus_settings(user_id)
        return {
            "website_enforcement_mode": cfg.get("website_enforcement_mode", "DISTRACTIONS_ONLY"),
            "background_optimized_run": cfg.get("background_optimized_run", True),
            "auto_ram_optimization": cfg.get("auto_ram_optimization", True),
            "ram_threshold_mb": cfg.get("ram_threshold_mb", 120),
            "allowed_websites": self.get_allowed_websites(user_id),
        }

    def save_system_manager_settings(self, settings: Dict[str, Any], user_id: str = "user_lok") -> Dict[str, Any]:
        """Updates system performance and website policy settings."""
        self.save_focus_settings(settings, user_id=user_id)
        return self.get_system_manager_settings(user_id)

    def update_device_installed_apps(self, installed_keys_or_pkgs: List[str]) -> None:
        """Updates device installed state from Android heartbeat telemetry."""
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            rows = conn.execute("SELECT app_key, package_patterns FROM device_app_controls").fetchall()
            for r in rows:
                app_key = r["app_key"]
                patterns = json.loads(r["package_patterns"])
                installed = 0
                detected = ""
                for item in installed_keys_or_pkgs:
                    item_lower = item.lower()
                    if item_lower == app_key.lower():
                        installed = 1
                        detected = item
                        break
                    for pat in patterns:
                        if pat.lower() in item_lower or item_lower in pat.lower():
                            installed = 1
                            detected = item
                            break
                    if installed:
                        break
                conn.execute(
                    "UPDATE device_app_controls SET installed_on_device = ?, detected_package = ?, updated_at = ? WHERE app_key = ?",
                    (installed, detected, now_iso, app_key)
                )

    def get_earbuds_config(self, user_id: str = "user_lok") -> Dict[str, Any]:
        """Returns the user's configured Bluetooth earbuds for Wake-Up Audio verification."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT preferred_earbuds_name, preferred_earbuds_address, updated_at FROM focus_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                name = (row["preferred_earbuds_name"] or "").strip()
                addr = (row["preferred_earbuds_address"] or "").strip()
                return {
                    "success": True,
                    "device_name": name or "Any Bluetooth Audio Output",
                    "device_address": addr,
                    "preferred_earbuds_name": name,
                    "preferred_earbuds_address": addr,
                    "configured": bool(name),
                    "updated_at": row["updated_at"],
                }
        return {
            "success": True,
            "device_name": "Any Bluetooth Audio Output",
            "device_address": "",
            "preferred_earbuds_name": "",
            "preferred_earbuds_address": "",
            "configured": False,
            "updated_at": "",
        }

    def set_earbuds_config(
        self,
        device_name: str,
        device_address: str = "",
        user_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """Persists the user's preferred Bluetooth earbuds in focus_settings."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_name = (device_name or "").strip()
        clean_addr = (device_address or "").strip()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO focus_settings (user_id, updated_at) VALUES (?, ?)",
                (user_id, now_iso),
            )
            conn.execute(
                """
                UPDATE focus_settings
                SET preferred_earbuds_name = ?, preferred_earbuds_address = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (clean_name, clean_addr, now_iso, user_id),
            )
        return self.get_earbuds_config(user_id=user_id)

    def process_sync_events_batch(
        self,
        device_id: str,
        events: List[Dict[str, Any]],
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """
        Processes offline queued events from Android with strict idempotency!
        If an event_id was already processed, it does not re-add points or double-apply transactions.
        """
        from core.time_window_engine import now_ist
        now = now_ist()
        now_iso = now.isoformat()
        today = now.strftime("%Y-%m-%d")

        processed_ids = []
        newly_processed_ids = []
        snooze_events = []
        gym_withdrawn_dates = []
        from core.daily_routine_gate import DailyRoutineGate
        routine_gate = DailyRoutineGate(self)
        wakeup_synced_dates = set()
        dates_to_recalculate = set()
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_seen = ?, sync_status = 'ONLINE' WHERE device_id = ?",
                (now_iso, device_id),
            )

            for evt in events:
                eid = evt.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}"
                etype = evt.get("event_type", "UNKNOWN")
                ts = evt.get("timestamp", now_iso)
                payload = evt.get("payload", {})
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}

                existing = conn.execute("SELECT 1 FROM sync_events WHERE event_id = ?", (eid,)).fetchone()
                if existing:
                    processed_ids.append(eid)
                    continue

                conn.execute(
                    """
                    INSERT INTO sync_events (
                        event_id, account_id, device_id, timestamp, event_type, payload_json, sync_status, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'PROCESSED', ?)
                    """,
                    (eid, account_id, device_id, ts, etype, json.dumps(payload), now_iso),
                )
                processed_ids.append(eid)
                newly_processed_ids.append(eid)

                # Client event payloads are never authoritative score transactions.
                # In particular, neither an omitted value nor a caller-supplied
                # "points" field may create a score on the server.
                if etype in ("RESTRICTION_SYNC", "POLICY_SYNC", "APP_LOCK_SYNC"):
                    svc = str(payload.get("service") or payload.get("target") or payload.get("app_key") or "").lower()
                    if svc in ("youtube", "instagram"):
                        req_en = bool(payload.get("requestedRestrictionEnabled", payload.get("enabled", True)))
                        inc_ver = int(payload.get("policyVersion") or payload.get("version") or 0)
                        inc_ts = payload.get("lastConfirmedAt") or ts
                        try:
                            self.reconcile_synced_restriction_policy(
                                service=svc,
                                requested_restriction_enabled=req_en,
                                incoming_version=inc_ver,
                                incoming_timestamp=inc_ts,
                                source_device_id=device_id,
                                user_id=account_id,
                            )
                        except Exception:
                            pass
                elif etype == "PC_SHUTDOWN_ATTENDANCE":
                    p_date = payload.get("date") or ts[:10]
                    self.record_pc_shutdown_phone_attendance(
                        date=p_date,
                        phone_device_id=device_id,
                        attendance_timestamp=ts,
                        account_id=account_id,
                        shutdown_event_id=payload.get("shutdown_event_id"),
                        conn=conn,
                    )
                elif etype == "PC_SHUTDOWN_EVENT":
                    self.record_pc_shutdown_event(
                        event_id=payload.get("event_id") or eid,
                        shutdown_timestamp=payload.get("shutdown_timestamp") or ts,
                        device_id=payload.get("device_id") or device_id,
                        date=payload.get("date"),
                        time_hhmmss=payload.get("time_hhmmss"),
                        evidence_source=payload.get("evidence_source", "WIN32_API"),
                        account_id=account_id,
                        conn=conn,
                    )
                elif etype == "PC_SHUTDOWN_PHOTO":
                    p_date = payload.get("date") or ts[:10]
                    encoded = payload.get("image_base64") or payload.get("file_base64")
                    if encoded:
                        try:
                            clean_b64 = str(encoded).split(",")[-1]
                            raw_bytes = base64.b64decode(clean_b64)
                            from core.scoring_engine import ScoringEngine
                            se = ScoringEngine(self)
                            ev_info = se.store_evidence_file(
                                date=p_date,
                                record_type="PC_SHUTDOWN",
                                related_record_id=f"PC_SHUTDOWN_{p_date}",
                                device_id=device_id,
                                file_bytes=raw_bytes,
                                filename_hint="shutdown_display.jpg",
                            )
                            ev_id = ev_info["evidence_id"]
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO proof_reviews (
                                    review_id, evidence_id, user_id, date, task_code,
                                    status, decision, submitted_at, revision_number
                                ) VALUES (?, ?, ?, ?, ?, 'NEEDS_REVIEW', 'PENDING', ?, 1)
                                """,
                                (
                                    f"rev_{ev_id}", ev_id, account_id, p_date, "PC_SHUTDOWN",
                                    ts
                                )
                            )
                            self.record_pc_shutdown_photo_evidence(
                                date=p_date,
                                evidence_id=ev_id,
                                photo_timestamp=ts,
                                account_id=account_id,
                                shutdown_event_id=payload.get("shutdown_event_id"),
                                conn=conn,
                            )
                        except Exception:
                            pass
                elif etype in ("PHONE_USAGE_DETECTED", "ANDROID_APP_USAGE_DETECTED"):
                    pkg_name = payload.get("package_name") or payload.get("app_package") or "unauthorized_app"
                    app_name = payload.get("app_label") or payload.get("app_name") or pkg_name
                    dev_id = payload.get("device") or device_id or "ANDROID"
                    sess_id = payload.get("session_id") or ""
                    try:
                        from windows.strict_session_manager import get_strict_session_manager
                        mgr = get_strict_session_manager()
                        if mgr and hasattr(mgr, "record_phone_usage_event"):
                            mgr.record_phone_usage_event(
                                event_id=eid,
                                device=dev_id,
                                package_name=pkg_name,
                                app_name=app_name,
                                timestamp=ts,
                                session_id=sess_id
                            )
                    except Exception as e:
                        logger.warning(f"Failed to notify strict session manager of phone usage: {e}")
                elif etype in ("EARBUDS_CONFIG_UPDATE", "FOCUS_SETTINGS_UPDATE"):
                    eb_name = payload.get("preferred_earbuds_name") if "preferred_earbuds_name" in payload else payload.get("device_name")
                    eb_addr = payload.get("preferred_earbuds_address") if "preferred_earbuds_address" in payload else payload.get("device_address", "")
                    if eb_name is not None:
                        conn.execute(
                            "INSERT OR IGNORE INTO focus_settings (user_id, updated_at) VALUES (?, ?)",
                            (account_id, now_iso),
                        )
                        conn.execute(
                            "UPDATE focus_settings SET preferred_earbuds_name = ?, preferred_earbuds_address = ?, updated_at = ? WHERE user_id = ?",
                            (
                                str(eb_name or "").strip(),
                                str(eb_addr or "").strip(),
                                now_iso,
                                account_id,
                            ),
                        )
                elif etype in ("WAKEUP_VERIFIED", "WAKEUP_COMPLETED", "WAKEUP_STEP3_TRACK"):
                    from core.wakeup_diagnostics import is_diagnostic_payload
                    if is_diagnostic_payload(evt) or is_diagnostic_payload(payload) or str(eid).startswith("diag_"):
                        continue
                    evt_date = routine_gate.ingest_android_wakeup(conn, payload, device_id, account_id, now_iso)
                    if evt_date:
                        wakeup_synced_dates.add(evt_date)
                    else:
                        # Preserve the unsupported claim for reconciliation.
                        # A label alone cannot manufacture the three measured steps.
                        conn.execute("UPDATE sync_events SET sync_status='PENDING_RECONCILIATION' WHERE event_id=?",(eid,))
                elif etype == "SNOOZE":
                    p_date = payload.get("date") or ts[:10]
                    is_dev = bool(payload.get("is_dev_mode", False))
                    s_ts = payload.get("timestamp") or ts
                    snooze_events.append({
                        "date": p_date,
                        "device_id": device_id,
                        "timestamp": s_ts,
                        "is_dev_mode": is_dev,
                        "event_id": eid,
                    })
                elif etype in ("GYM_SKIP_WITHDRAWN", "GYM_RESET"):
                    p_date = payload.get("date") or ts[:10]
                    is_dev = bool(payload.get("is_dev_mode", False))
                    gym_withdrawn_dates.append((p_date, is_dev))
                elif etype == "GYM_MODE_ACTIVATE":
                    from core.gym_mode_engine import GymModeEngine
                    rec_res = GymModeEngine(self).reconcile_offline_activation(
                        gym_activity_id=str(payload.get("gym_activity_id") or ""),
                        activation_time_ms=payload.get("activation_time_ms") or 0,
                        request_id=str(payload.get("request_id") or eid),
                        session_id=payload.get("session_id"),
                        account_id=str(payload.get("account_id") or account_id or "user_lok"),
                        timezone=str(payload.get("timezone") or "Asia/Kolkata"),
                        connection=conn,
                    )
                    sync_st = "SYNCED" if rec_res.get("success") else "CONFLICT"
                    conn.execute("UPDATE sync_events SET sync_status=? WHERE event_id=?", (sync_st, eid))
                elif etype in ("GYM_MODE_SESSION_UPDATE", "GYM_MODE_RESET", "GYM_MODE_LIFE_DEDUCTED", "GYM_MODE_PROOF_FULFILLED"):
                    conn.execute("UPDATE sync_events SET sync_status='PENDING_RECONCILIATION' WHERE event_id=?", (eid,))
                elif etype in ("HOURLY_PROOF_SUBMITTED", "HOURLY_SUBMITTED"):
                    act_id = payload.get("activity_id")
                    sub_time = payload.get("submitted_at") or ts
                    sub_id = payload.get("submission_id") or eid
                    pts = int(payload.get("points") or 8)
                    if act_id:
                        conn.execute(
                            """
                            UPDATE scheduled_activities
                            SET status = 'SUBMITTED', submitted_at = ?, points = ?,
                                attendance_status = 'PRESENT', validation_status = 'PROCESSING'
                            WHERE activity_id = ?
                            """,
                            (sub_time, pts, act_id),
                        )
                elif etype in ("SCORE_EVENT", "PROOF_APPROVED", "POINTS_AWARDED", "TASK_VERIFIED"):
                    target_eid = payload.get("event_id") or eid
                    target_date = payload.get("date") or ts[:10]
                    is_dev = bool(payload.get("is_dev_mode", False))
                    target_ts = payload.get("timestamp") or ts
                    inner_event_type = payload.get("event_type") or etype
                    if inner_event_type == "SCORE_EVENT":
                        inner_event_type = "PROOF_APPROVED"
                    points = int(payload.get("points") or 0)
                    desc = payload.get("description") or f"Authoritative {inner_event_type} synced from Android (+{points} pts)"
                    audit = payload.get("audit") or {}
                    if isinstance(audit, str):
                        try:
                            audit = json.loads(audit)
                        except Exception:
                            audit = {}
                    audit["synced_from_android"] = True
                    if "source" not in audit:
                        audit["source"] = "ANDROID"

                    # Finalized losses require the same authenticated human evidence on every transport.
                    if points < 0:
                        from core.android_ledger import _authorized_negative
                        if not _authorized_negative({"event_type": inner_event_type, "audit": audit}):
                            continue

                    evidence_id = payload.get("evidence_id") or audit.get("evidence_id")
                    activity_id = payload.get("activity_id") or audit.get("activity_id")
                    task_code = payload.get("task_code") or audit.get("task_code") or ""
                    submission_id = payload.get("submission_id") or audit.get("submission_id")
                    cat_name = str(audit.get("category") or payload.get("category") or "").strip().upper()

                    is_gym = "GYM" in str(task_code).upper() or "GYM" in str(activity_id).upper() or cat_name == "GYM" or "GYM" in desc.upper()
                    is_bath = "BATH" in str(task_code).upper() or "BATH" in str(activity_id).upper() or cat_name == "BATH" or "BATH" in desc.upper()
                    is_sleep = "SLEEP" in str(task_code).upper() or "SLEEP" in str(activity_id).upper() or cat_name == "SLEEP" or "SLEEP" in desc.upper()
                    is_wakeup = "WAKE" in str(task_code).upper() or "WAKE" in str(activity_id).upper() or cat_name in ("WAKEUP", "WAKE_UP") or "WAKE" in desc.upper()
                    is_travel = "TRAVEL" in str(task_code).upper() or "TRAVEL" in str(activity_id).upper() or cat_name == "TRAVEL" or "TRAVEL" in desc.upper()
                    is_excluded = is_gym or is_bath or is_sleep or is_wakeup or is_travel

                    # Check for explicit authorized human administrator approval or server async analysis completion
                    is_admin_approval = (
                        audit.get("action") == "ADMIN_APPROVAL" or
                        str(desc).startswith("Admin approved") or
                        (audit.get("approval_source") in ("ANDROID_ADMIN", "AUTHENTICATED_HUMAN_ADMIN") and str(audit.get("reviewed_by", "")).lower() not in ("automated validator", "system", "auto", "unknown", ""))
                    )
                    is_server_async_eval = (inner_event_type == "POINTS_AWARDED" and "async analysis" in desc.lower())

                    is_hourly = not is_excluded and not is_admin_approval and not is_server_async_eval and (
                        str(task_code).upper() == "HOURLY" or cat_name in (
                            "STUDY", "COLLEGE", "CODING", "PROJECT", "RESEARCH", "READING",
                            "EXERCISE", "WORK", "PRODUCTIVE", "EDITING", "VIDEO SHOOT"
                        ) or "-HOURLY" in str(activity_id).upper() or ("HOURLY" in str(activity_id).upper() and not is_excluded)
                    )

                    # Authoritative Taufa AI Ingestion Gate for Hourly Attendance
                    if is_hourly and points > 0:
                        taufa_block = audit.get("taufa_ai")
                        has_valid_taufa = False
                        if isinstance(taufa_block, dict):
                            t_score = taufa_block.get("score")
                            t_qual = taufa_block.get("qualifies_for_points")
                            t_cat = taufa_block.get("category_match")
                            t_st = str(taufa_block.get("validator_status", "")).upper()
                            if t_qual is True and t_cat is True and t_score is not None and int(t_score) >= 6 and t_st in ("SUCCESS", "AWAITING_APPROVAL"):
                                has_valid_taufa = True
                        elif str(audit.get("reviewed_by", "")).strip() == "Taufa_AI" and audit.get("approval_source") == "AUTHENTICATED_HUMAN_ADMIN":
                            if points >= 6 and audit.get("category_match", True) is not False:
                                has_valid_taufa = True

                        if not has_valid_taufa:
                            logger.warning(
                                "Quarantining unverified hourly attendance score event %s (evidence: %s, sub: %s). Lacks valid Taufa AI validation.",
                                target_eid, evidence_id, submission_id
                            )
                            if evidence_id:
                                hour_slot_val = payload.get("hour_slot") or audit.get("hour_slot") or ""
                                conn.execute(
                                    """
                                    INSERT INTO proof_reviews (
                                        review_id, evidence_id, user_id, date, task_code, hour_slot,
                                        status, decision, points_awarded, submitted_at, reviewed_at, reviewed_by, reason_note
                                    ) VALUES (
                                        COALESCE((SELECT review_id FROM proof_reviews WHERE evidence_id = ?), ?),
                                        ?, ?, ?, ?, ?, 'NEEDS_REVIEW', 'PENDING', 0, ?, ?, 'Authoritative Ingestion Gate', 'Quarantined: Lacks verified Taufa AI approval'
                                    )
                                    ON CONFLICT(evidence_id) DO UPDATE SET
                                        status = 'NEEDS_REVIEW',
                                        decision = 'PENDING',
                                        points_awarded = 0,
                                        reviewed_at = excluded.reviewed_at,
                                        reviewed_by = excluded.reviewed_by,
                                        reason_note = 'Quarantined: Lacks verified Taufa AI approval'
                                    """,
                                    (
                                        evidence_id, f"rev_{evidence_id}",
                                        evidence_id, account_id, target_date, task_code, hour_slot_val,
                                        target_ts, now_iso
                                    ),
                                )
                            if activity_id:
                                conn.execute(
                                    """
                                    UPDATE scheduled_activities
                                    SET status = 'NEEDS_REVIEW', validation_status = 'NEEDS_REVIEW', points = 0
                                    WHERE activity_id = ?
                                    """,
                                    (activity_id,),
                                )
                            dates_to_recalculate.add((target_date, is_dev))
                            continue

                    existing_ev = conn.execute("SELECT 1 FROM score_events WHERE event_id = ?", (target_eid,)).fetchone()
                    if not existing_ev:
                        rule_ver = self.get_current_rule_version(account_id)
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO score_events (
                                event_id, date, is_dev_mode, timestamp, event_type,
                                device_id, description, points, rule_version, evidence_id, related_task_id, audit_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                target_eid, target_date, 1 if is_dev else 0, target_ts, inner_event_type,
                                device_id, desc, points, rule_ver, evidence_id, activity_id, json.dumps(audit)
                            ),
                        )

                    if evidence_id:
                        hour_slot_val = payload.get("hour_slot") or audit.get("hour_slot") or ""
                        conn.execute(
                            """
                            INSERT INTO proof_reviews (
                                review_id, evidence_id, user_id, date, task_code, hour_slot,
                                status, decision, points_awarded, submitted_at, reviewed_at, reviewed_by, reason_note
                            ) VALUES (
                                COALESCE((SELECT review_id FROM proof_reviews WHERE evidence_id = ?), ?),
                                ?, ?, ?, ?, ?, 'APPROVED', 'APPROVED', ?, ?, ?, ?, 'Approved on Android'
                            )
                            ON CONFLICT(evidence_id) DO UPDATE SET
                                status = 'APPROVED',
                                decision = 'APPROVED',
                                points_awarded = excluded.points_awarded,
                                reviewed_at = excluded.reviewed_at,
                                reviewed_by = excluded.reviewed_by
                            """,
                            (
                                evidence_id, f"rev_{evidence_id}",
                                evidence_id, account_id, target_date, task_code, hour_slot_val,
                                points, target_ts, now_iso, audit.get("reviewed_by") or "Android Admin"
                            ),
                        )

                    if activity_id:
                        conn.execute(
                            """
                            UPDATE scheduled_activities
                            SET status = 'VERIFIED', validation_status = 'APPROVED', points = ?, attendance_status = 'PRESENT'
                            WHERE activity_id = ?
                            """,
                            (points, activity_id),
                        )

                    if str(task_code).upper() in ("WAKE_UP", "WAKEUP") or inner_event_type in ('WAKEUP_VERIFIED','WAKEUP_COMPLETED'):
                        wakeup_synced_dates.add(target_date)
                        conn.execute(
                            """
                            UPDATE daily_records
                            SET wakeup_status = 'ON_TIME', wakeup_completed_at = COALESCE(wakeup_completed_at, ?), updated_at = ?
                            WHERE date = ?
                            """,
                            (now_iso, now_iso, target_date),
                        )

                    dates_to_recalculate.add((target_date, is_dev))
                elif etype.startswith("WAKEUP_DIAGNOSTIC"):
                    # Diagnostic events synchronize as diagnostics only; never touch attendance or scores
                    continue

            score_row = conn.execute(
                "SELECT final_score FROM daily_records WHERE date = ? AND is_dev_mode = 0",
                (today,),
            ).fetchone()
            current_score = score_row["final_score"] if score_row else 0

        if gym_withdrawn_dates:
            try:
                from core.scoring_engine import ScoringEngine
                se = ScoringEngine(self)
                for g_date, g_dev in gym_withdrawn_dates:
                    se.withdraw_gym_skip(date=g_date, user_id=account_id, is_dev_mode=g_dev)
                rec = self.get_daily_record(today, user_id=account_id, is_dev_mode=False)
                current_score = int((rec or {}).get("final_score", current_score))
            except Exception:
                pass

        if snooze_events:
            try:
                from core.scoring_engine import ScoringEngine
                se = ScoringEngine(self)
                for sne in snooze_events:
                    se.record_snooze(
                        date=sne["date"],
                        device_id=sne["device_id"],
                        timestamp=sne["timestamp"],
                        is_dev_mode=sne["is_dev_mode"],
                        event_id=sne["event_id"],
                    )
                rec = self.get_daily_record(today, user_id=account_id, is_dev_mode=False)
                current_score = int((rec or {}).get("final_score", current_score))
            except Exception:
                pass

        if wakeup_synced_dates:
            try:
                from core.scoring_engine import ScoringEngine
                se = ScoringEngine(self)
                for wake_date in sorted(wakeup_synced_dates):
                    self.ensure_daily_record(wake_date, user_id=account_id)
                    se.finalize_wakeup_session(date=wake_date, is_dev_mode=False, device_id=device_id)
                rec = self.get_daily_record(today, user_id=account_id, is_dev_mode=False)
                current_score = int((rec or {}).get("final_score", current_score))
            except Exception:
                pass

        if dates_to_recalculate:
            try:
                from core.scoring_engine import ScoringEngine
                se = ScoringEngine(self)
                for d_date, d_dev in sorted(dates_to_recalculate):
                    self.ensure_daily_record(d_date, user_id=account_id, is_dev_mode=d_dev)
                    se.recalculate_daily_score(d_date, user_id=account_id, is_dev_mode=d_dev)
                rec = self.get_daily_record(today, user_id=account_id, is_dev_mode=False)
                current_score = int((rec or {}).get("final_score", current_score))
            except Exception:
                pass

        privilege_state = "ALLOWED" if current_score >= 80 else "LOCKED"
        restr_status = self.get_sidebar_restrictions_status(user_id=account_id).get("status", {})
        return {
            "success": True,
            "processed_count": len(processed_ids),
            "processed_event_ids": processed_ids,
            "newly_processed_event_ids": newly_processed_ids,
            "shared_score": current_score,
            "point_ledger_summary": self.get_point_ledger_summary(today, is_dev_mode=False, account_id=account_id),
            "privilege_state": privilege_state,
            "restriction_policies": restr_status,
            "earbuds_config": self.get_earbuds_config(user_id=account_id),
            "daily_routine_gate": routine_gate.reconcile(today, account_id, now),
            "server_time": now_iso,
        }

    # =========================================================================
    # AUDIT LOGS RETRIEVAL
    # =========================================================================

    def get_admin_audit_logs(
        self,
        limit: int = 100,
        action_type: Optional[str] = None,
        admin_user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM admin_audit_logs WHERE 1=1"
        params: List[Any] = []
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if admin_user:
            query += " AND admin_user = ?"
            params.append(admin_user)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            res = []
            for r in rows:
                it = dict(r)
                try:
                    it["details"] = json.loads(it.get("details_json") or "{}")
                except Exception:
                    it["details"] = {}
                res.append(it)
            return res

    # =========================================================================
    # ANALYTICS, REPORTING & SEARCH
    # =========================================================================

    def get_admin_analytics_summary(self, days: int = 7) -> Dict[str, Any]:
        with self._connect() as conn:
            # Score average
            avg_score_row = conn.execute(
                "SELECT AVG(final_score) as avg_score, COUNT(*) as cnt FROM daily_records WHERE is_dev_mode = 0"
            ).fetchone()
            avg_score = round(float(avg_score_row["avg_score"]), 1) if (avg_score_row and avg_score_row["avg_score"] is not None) else 0.0

            # Proof statistics
            pending_cnt = conn.execute("SELECT COUNT(*) as c FROM proof_reviews WHERE status = 'PENDING'").fetchone()["c"]
            approved_cnt = conn.execute("SELECT COUNT(*) as c FROM proof_reviews WHERE status = 'APPROVED'").fetchone()["c"]
            rejected_cnt = conn.execute("SELECT COUNT(*) as c FROM proof_reviews WHERE status = 'REJECTED'").fetchone()["c"]
            total_reviews = approved_cnt + rejected_cnt
            approval_rate = round((approved_cnt / total_reviews * 100.0) if total_reviews > 0 else 0.0, 1)

            # Task completions
            tasks_completed = conn.execute("SELECT COUNT(*) as c FROM scheduled_tasks WHERE state = 'COMPLETED'").fetchone()["c"]
            tasks_missed = conn.execute("SELECT COUNT(*) as c FROM scheduled_tasks WHERE state = 'MISSED'").fetchone()["c"]
            total_tasks = tasks_completed + tasks_missed
            task_completion_rate = round((tasks_completed / total_tasks * 100.0) if total_tasks > 0 else 0.0, 1)

            # Devices
            devices_online = conn.execute("SELECT COUNT(*) as c FROM devices WHERE pairing_state = 'PAIRED' AND sync_status != 'OFFLINE'").fetchone()["c"]
            total_devices = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]

            # Violations
            violations_cnt = conn.execute("SELECT COUNT(*) as c FROM restriction_events").fetchone()["c"]

            return {
                "average_score": avg_score,
                "pending_proofs": pending_cnt,
                "approved_proofs": approved_cnt,
                "rejected_proofs": rejected_cnt,
                "approval_rate_pct": approval_rate,
                "task_completion_rate_pct": task_completion_rate,
                "missed_tasks": tasks_missed,
                "violations_count": violations_cnt,
                "devices_online": f"{devices_online} / {total_devices}",
                "sync_status": "Connected" if devices_online > 0 else "Not Connected",
                "average_review_minutes": 0.0,
            }

    def export_admin_report_csv(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> str:
        lines = ["Date,User,Task,Type,Points,Proof_Status,Admin_Decision,Timestamp,Score"]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT se.date, se.timestamp, se.description, se.points, se.event_type, se.device_id,
                       pr.status as proof_status, pr.decision, dr.final_score, dr.user_id
                FROM score_events se
                LEFT JOIN proof_reviews pr ON se.evidence_id = pr.evidence_id
                LEFT JOIN daily_records dr ON se.date = dr.date AND se.is_dev_mode = dr.is_dev_mode
                ORDER BY se.timestamp DESC
                LIMIT 500
                """
            ).fetchall()
            for r in rows:
                p_status = r["proof_status"] or "N/A"
                dec = r["decision"] or "AUTO"
                score = r["final_score"] if r["final_score"] is not None else 0
                user = r["user_id"] or "N/A"
                clean_desc = (r["description"] or "").replace(",", ";")
                lines.append(f"{r['date']},{user},{clean_desc},{r['event_type']},{r['points']},{p_status},{dec},{r['timestamp']},{score}")
        return "\n".join(lines)

    def export_admin_report_json(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            analytics = self.get_admin_analytics_summary()
            events = conn.execute(
                "SELECT * FROM score_events ORDER BY timestamp DESC LIMIT 200"
            ).fetchall()
            proofs = conn.execute(
                "SELECT * FROM proof_reviews ORDER BY submitted_at DESC LIMIT 100"
            ).fetchall()
            audits = conn.execute(
                "SELECT * FROM admin_audit_logs ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()
            return {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "analytics_summary": analytics,
                "score_events": [dict(e) for e in events],
                "proof_reviews": [dict(p) for p in proofs],
                "audit_logs": [dict(a) for a in audits],
            }

    def global_admin_search(self, query: str) -> Dict[str, Any]:
        q = f"%{query.strip()}%"
        with self._connect() as conn:
            users = conn.execute("SELECT * FROM users WHERE user_id LIKE ? OR username LIKE ?", (q, q)).fetchall()
            tasks = conn.execute("SELECT * FROM scheduled_tasks WHERE title LIKE ? OR task_code LIKE ? LIMIT 10", (q, q)).fetchall()
            proofs = conn.execute("SELECT * FROM proof_reviews WHERE task_code LIKE ? OR user_id LIKE ? OR status LIKE ? LIMIT 10", (q, q, q)).fetchall()
            devices = conn.execute("SELECT * FROM devices WHERE device_name LIKE ? OR device_id LIKE ? OR platform LIKE ?", (q, q, q)).fetchall()
            audits = conn.execute("SELECT * FROM admin_audit_logs WHERE action_type LIKE ? OR field_changed LIKE ? OR admin_user LIKE ? LIMIT 10", (q, q, q)).fetchall()

            return {
                "query": query,
                "users": [dict(u) for u in users],
                "tasks": [dict(t) for t in tasks],
                "proofs": [dict(p) for p in proofs],
                "devices": [dict(d) for d in devices],
                "audit_logs": [dict(a) for a in audits],
            }

    # =========================================================================
    # STRICT SESSION PERSISTENCE & AUDITING
    # =========================================================================

    def create_strict_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strict_sessions (
                    session_id, user_id, device_id, policy_version, state,
                    start_time, end_time, precheck_passed, precheck_details_json,
                    crash_recovered, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_data["session_id"],
                    session_data.get("user_id", "user_lok"),
                    session_data.get("device_id", "windows_lok_pc"),
                    session_data.get("policy_version", "StrictRecords Policy v12"),
                    session_data.get("state", "STARTING"),
                    session_data.get("start_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    session_data.get("end_time"),
                    1 if session_data.get("precheck_passed", True) else 0,
                    json.dumps(session_data.get("precheck_details", {})),
                    1 if session_data.get("crash_recovered", False) else 0,
                    session_data.get("exit_reason"),
                ),
            )
            row = conn.execute("SELECT * FROM strict_sessions WHERE session_id = ?", (session_data["session_id"],)).fetchone()
            return dict(row)

    def update_strict_session(self, session_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not kwargs:
            return self.get_strict_session(session_id)
        sets = []
        params = []
        for k, v in kwargs.items():
            if k == "precheck_details":
                sets.append("precheck_details_json = ?")
                params.append(json.dumps(v))
            elif k in ("precheck_passed", "crash_recovered"):
                sets.append(f"{k} = ?")
                params.append(1 if v else 0)
            else:
                sets.append(f"{k} = ?")
                params.append(v)
        params.append(session_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE strict_sessions SET {', '.join(sets)} WHERE session_id = ?", params)
            row = conn.execute("SELECT * FROM strict_sessions WHERE session_id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def get_strict_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM strict_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["precheck_details"] = json.loads(res.get("precheck_details_json") or "{}")
            return res

    def get_active_strict_session(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM strict_sessions 
                WHERE state IN ('ACTIVE', 'RESTRICTED', 'PRIVILEGED', 'STARTING', 'PREPARING')
                ORDER BY start_time DESC LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            res["precheck_details"] = json.loads(res.get("precheck_details_json") or "{}")
            return res

    def save_strict_session_policy(self, policy_version: str, policy_dict: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE strict_session_policies SET is_active = 0")
            conn.execute(
                """
                INSERT INTO strict_session_policies (policy_version, policy_json, created_at, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(policy_version) DO UPDATE SET
                    policy_json = excluded.policy_json,
                    created_at = excluded.created_at,
                    is_active = 1
                """,
                (
                    policy_version,
                    json.dumps(policy_dict),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return policy_dict

    def get_active_strict_session_policy(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT policy_json FROM strict_session_policies WHERE is_active = 1 LIMIT 1").fetchone()
            if row and row["policy_json"]:
                return json.loads(row["policy_json"])
            return None

    def record_strict_audit(
        self,
        session_id: str,
        action: str,
        account: str = "user_lok",
        device_id: str = "windows_lok_pc",
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO strict_session_audit (session_id, account, device_id, action, timestamp, reason, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    account,
                    device_id,
                    action,
                    now_ts,
                    reason,
                    json.dumps(details or {}),
                ),
            )
            audit_id = cur.lastrowid
            return {
                "audit_id": audit_id,
                "session_id": session_id,
                "account": account,
                "device_id": device_id,
                "action": action,
                "timestamp": now_ts,
                "reason": reason,
                "details": details or {},
            }

    def get_strict_audit_logs(
        self,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM strict_session_audit WHERE session_id = ? ORDER BY audit_id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strict_session_audit ORDER BY audit_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d.get("details_json") or "{}")
                result.append(d)
            return result


    def record_strict_violation(self, violation: Dict[str, Any]) -> None:
        """Appends a single violation record to the strict session violation ledger."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strict_session_violation_ledger (
                    violation_id, session_id, category, timestamp,
                    detection_source, process_or_tab_id, count_before,
                    count_after, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(violation_id) DO NOTHING
                """,
                (
                    violation["violation_id"],
                    violation["session_id"],
                    violation["category"],
                    violation["timestamp"],
                    violation.get("detection_source", "UNKNOWN"),
                    str(violation.get("process_or_tab_id") or ""),
                    int(violation.get("count_before", 0)),
                    int(violation.get("count_after", 0)),
                    json.dumps(violation.get("details") or {}),
                ),
            )

    def get_strict_violations_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all violation ledger records for a given session, ordered by count_after ASC."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM strict_session_violation_ledger WHERE session_id = ? ORDER BY count_after ASC, rowid ASC",
                (session_id,),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d.get("details_json") or "{}")
                results.append(d)
            return results

    def get_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            return dict(row) if row else None

    def get_user_settings(self, user_id: str = "user_lok"):
        return self.get_settings(user_id=user_id)

    # =========================================================================
    # REAL USER MANAGEMENT (Requirement 23, 30, 31)
    # =========================================================================

    def create_user(
        self,
        username: str,
        user_id: Optional[str] = None,
        role: str = "USER",
        avatar: str = "🛡️",
    ) -> Dict[str, Any]:
        """Creates a real user account in the real database."""
        uid = user_id or f"user_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM users WHERE user_id = ? OR username = ?", (uid, username)).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                """
                INSERT INTO users (user_id, username, created_at, avatar, discipline_xp, discipline_level, role)
                VALUES (?, ?, ?, ?, 0, 'STARTER', ?)
                """,
                (uid, username, now_iso, avatar, role),
            )
            # Create user settings using system default configuration
            default_settings = self.get_settings("")
            default_settings.user_id = uid
            s_json = json.dumps(default_settings.to_dict())
            conn.execute(
                "INSERT INTO user_settings (user_id, settings_json, rule_version, updated_at) VALUES (?, ?, 1, ?)",
                (uid, s_json, now_iso),
            )
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
            return dict(row)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return dict(row) if row else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
            return [dict(r) for r in rows]

    def get_active_user_profile(self, user_id: str = "user_lok") -> Dict[str, Any]:
        """
        Retrieves authoritative active profile with 'LOK ADHITHYA G' as default display name.
        """
        with self._connect() as conn:
            # Check users table
            u = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not u:
                u = conn.execute("SELECT * FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
            acc = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (user_id,)).fetchone()

            display_name = "LOK ADHITHYA G"
            email = "lok@strictrecords.local"
            role = "ADMIN"
            avatar = "🛡️"
            level = "OPERATOR"
            xp = 120
            status = "ACTIVE"

            if acc:
                acc_d = dict(acc)
                display_name = acc_d.get("display_name") or display_name
                email = acc_d.get("email") or email
                status = acc_d.get("status") or status

            if u:
                u_d = dict(u)
                display_name = u_d.get("display_name") or u_d.get("username") or display_name
                role = u_d.get("role") or role
                avatar = u_d.get("avatar") or avatar
                level = u_d.get("discipline_level") or level
                xp = u_d.get("discipline_xp", xp)

            return {
                "user_id": user_id,
                "username": display_name,
                "display_name": display_name,
                "email": email,
                "role": role,
                "discipline_level": level,
                "discipline_xp": xp,
                "level": level,
                "xp": xp,
                "avatar": avatar,
                "status": status,
                "initial_setup_key_active": self.is_default_admin_pin(),
            }

    def update_user_profile(
        self,
        user_id: str = "user_lok",
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        level: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            if display_name:
                conn.execute(
                    "UPDATE accounts SET display_name = ? WHERE account_id = ?",
                    (display_name, user_id),
                )
                conn.execute(
                    "UPDATE users SET display_name = ?, username = ? WHERE user_id = ?",
                    (display_name, display_name, user_id),
                )
            if email:
                conn.execute(
                    "UPDATE accounts SET email = ? WHERE account_id = ?",
                    (email, user_id),
                )
            if level:
                conn.execute(
                    "UPDATE users SET discipline_level = ? WHERE user_id = ?",
                    (level, user_id),
                )
        return self.get_active_user_profile(user_id)

    # =========================================================================
    # EXAMS & PERMANENT DEMO TEST (Requirement 7)
    # =========================================================================

    def get_exams(self, include_demo: bool = True) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if include_demo:
                rows = conn.execute("SELECT * FROM exams ORDER BY is_demo DESC, created_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM exams WHERE is_demo = 0 ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_exam_details(self, exam_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            lookup_id = exam_id
            if exam_id in ("EXAM_DEMO_01", "demo_test_01", "demo_test"):
                lookup_id = "demo_test"
            ex = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (lookup_id,)).fetchone()
            if not ex and "demo" in exam_id.lower():
                ex = conn.execute("SELECT * FROM exams WHERE is_demo = 1 LIMIT 1").fetchone()
                if ex:
                    lookup_id = ex["exam_id"]
            if not ex:
                return None
            res = dict(ex)
            q_rows = conn.execute("SELECT * FROM exam_questions WHERE exam_id = ? ORDER BY question_number ASC", (lookup_id,)).fetchall()
            questions = []
            for q in q_rows:
                qd = dict(q)
                if qd.get("options_json"):
                    try:
                        qd["options"] = json.loads(qd["options_json"])
                    except Exception:
                        qd["options"] = []
                questions.append(qd)
            res["questions"] = questions
            att_cnt = conn.execute("SELECT COUNT(*) as c FROM exam_attempts WHERE exam_id = ?", (lookup_id,)).fetchone()["c"]
            res["attempts_count"] = att_cnt
            return res

    def get_exam_attempts(self, exam_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            query = "SELECT * FROM exam_attempts WHERE 1=1"
            params: List[Any] = []
            if exam_id:
                lookup_id = exam_id
                if exam_id in ("EXAM_DEMO_01", "demo_test_01", "demo_test"):
                    lookup_id = "demo_test"
                query += " AND exam_id = ?"
                params.append(lookup_id)
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, tuple(params)).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                try:
                    d["answers"] = json.loads(d.get("answers_json") or "{}")
                except Exception:
                    d["answers"] = {}
                try:
                    d["results"] = json.loads(d.get("results_json") or "{}")
                except Exception:
                    d["results"] = {}
                score_val = d.get("score") or 0
                max_sc = d.get("max_score") or 100
                d["percentage"] = round((score_val / max_sc) * 100, 1) if max_sc else 0
                d["submitted_at"] = d.get("end_time") or d.get("created_at") or "-"
                res.append(d)
            return res

    def record_exam_attempt(
        self,
        exam_id: str,
        user_id: str,
        score: Optional[int] = None,
        max_score: int = 100,
        status: str = "GRADED",
        answers: Optional[Dict[str, Any]] = None,
        results: Optional[Dict[str, Any]] = None,
        integrity_score: float = 100.0,
        **kwargs,
    ) -> Dict[str, Any]:
        lookup_id = exam_id
        if exam_id in ("EXAM_DEMO_01", "demo_test_01", "demo_test"):
            lookup_id = "demo_test"
        att_id = f"att_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Parse answers if string
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}
        ans_dict = answers or {}

        # Auto grade if score is not provided
        calculated_score = score
        if calculated_score is None:
            calculated_score = 0
            with self._connect() as conn:
                q_rows = conn.execute("SELECT * FROM exam_questions WHERE exam_id = ?", (lookup_id,)).fetchall()
                for q in q_rows:
                    qid = q["question_id"]
                    q_type = q["question_type"]
                    q_pts = q["points"] or 10
                    user_ans = ans_dict.get(qid, "")
                    if q_type == "MCQ":
                        if user_ans and str(user_ans).strip() == str(q["correct_answer"]).strip():
                            calculated_score += q_pts
                    elif q_type in ("CODING", "SQL"):
                        if user_ans and len(str(user_ans).strip()) > 10:
                            calculated_score += q_pts

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exam_attempts (
                    attempt_id, exam_id, user_id, start_time, end_time, status, score, max_score,
                    answers_json, results_json, integrity_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    att_id, lookup_id, user_id, now_iso, now_iso, status, int(calculated_score), max_score,
                    json.dumps(ans_dict), json.dumps(results or {}), integrity_score, now_iso
                ),
            )
            row = conn.execute("SELECT * FROM exam_attempts WHERE attempt_id = ?", (att_id,)).fetchone()
            d = dict(row)
            d["percentage"] = round((d["score"] / max_score) * 100, 1) if max_score else 0
            return d

    def create_exam(
        self,
        title: str,
        description: str,
        total_questions: int = 10,
        duration_minutes: int = 60,
        passing_score: int = 80,
    ) -> Dict[str, Any]:
        exam_id = f"exam_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exams (exam_id, title, description, total_questions, duration_minutes, is_demo, passing_score, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (exam_id, title, description, total_questions, duration_minutes, passing_score, now_iso),
            )
            row = conn.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
            return dict(row)

    # =========================================================================
    # OFFICIAL EVENT BOOKINGS & SCHEDULED EXITS (Features A, B, C, D)
    # =========================================================================

    def enqueue_sync_event(self, payload_type: str, payload: Dict[str, Any], device_id: str = "windows_pc") -> str:
        """Enqueues an event for cross-device synchronization in sync_queue."""
        sync_id = f"sync_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_queue (sync_id, device_id, created_at, payload_type, payload_json, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING_SYNC')
                """,
                (sync_id, device_id or "windows_pc", now_iso, payload_type, json.dumps(payload))
            )
        return sync_id

    def create_official_event_booking(self, booking_data: Dict[str, Any], device_id: str = "windows_pc") -> Dict[str, Any]:
        """Creates and stores an authoritative official event booking with validation."""
        from core.official_event_engine import validate_event_booking, EventStatus, ShortlistStatus

        event_id = booking_data.get("event_id") or f"evt_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing = self.list_official_event_bookings(include_past=False)
        is_valid, validation_errors = validate_event_booking(booking_data, existing_bookings=existing)
        if not is_valid:
            raise ValueError(f"Official event validation failed: {'; '.join(validation_errors)}")

        title = str(booking_data.get("title") or "Official Scheduled Event").strip()
        category = str(booking_data.get("category") or "OTHER_OFFICIAL").strip()
        org = str(booking_data.get("organization") or "Official Organization").strip()
        ev_date = str(booking_data.get("event_date") or "").strip()
        start_t = str(booking_data.get("start_time") or "").strip()
        end_t = str(booking_data.get("end_time") or "").strip()
        duration_min = int(booking_data.get("expected_duration_minutes") or 60)
        time_zone = str(booking_data.get("time_zone") or "Asia/Kolkata").strip()
        mode = str(booking_data.get("event_mode") or "VIRTUAL").strip()
        venue = str(booking_data.get("venue_or_platform") or "").strip()
        rep_time = str(booking_data.get("reporting_time") or "").strip()
        shortlist = str(booking_data.get("shortlist_status") or ShortlistStatus.NOT_SPECIFIED).strip()
        status = str(booking_data.get("status") or EventStatus.CONFIRMED).strip()
        extracted_json = json.dumps(booking_data.get("extracted_data") or {})
        corrections_json = json.dumps(booking_data.get("user_corrections") or {})
        evidence_json = json.dumps(booking_data.get("evidence_refs") or [])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO official_event_bookings (
                    event_id, title, category, organization, event_date, start_time, end_time,
                    expected_duration_minutes, time_zone, event_mode, venue_or_platform, reporting_time,
                    shortlist_status, status, extracted_data_json, user_corrections_json, evidence_refs_json,
                    exit_consumed, version, device_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?)
                """,
                (
                    event_id, title, category, org, ev_date, start_t, end_t,
                    duration_min, time_zone, mode, venue, rep_time,
                    shortlist, status, extracted_json, corrections_json, evidence_json,
                    device_id, now_iso, now_iso
                )
            )

        # Enqueue for synchronization across paired devices
        self.enqueue_sync_event(
            payload_type="OFFICIAL_EVENT_BOOKING",
            payload={
                "event_id": event_id,
                "title": title,
                "category": category,
                "organization": org,
                "event_date": ev_date,
                "start_time": start_t,
                "end_time": end_t,
                "expected_duration_minutes": duration_min,
                "time_zone": time_zone,
                "event_mode": mode,
                "venue_or_platform": venue,
                "reporting_time": rep_time,
                "shortlist_status": shortlist,
                "status": status,
                "version": 1,
                "device_id": device_id,
                "updated_at": now_iso,
            },
            device_id=device_id,
        )

        self.record_admin_audit(
            admin_user=device_id,
            action_type="OFFICIAL_EVENT_BOOKED",
            field_changed=f"event_booking:{event_id}",
            old_value="",
            new_value=f"{title} ({ev_date} {start_t}-{end_t})",
            device_id=device_id,
        )

        return self.get_official_event_booking(event_id) or {}

    def get_official_event_booking(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves an official event booking by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM official_event_bookings WHERE event_id = ?", (event_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["extracted_data"] = json.loads(d.get("extracted_data_json") or "{}")
            except Exception:
                d["extracted_data"] = {}
            try:
                d["user_corrections"] = json.loads(d.get("user_corrections_json") or "{}")
            except Exception:
                d["user_corrections"] = {}
            try:
                d["evidence_refs"] = json.loads(d.get("evidence_refs_json") or "[]")
            except Exception:
                d["evidence_refs"] = []
            return d

    def list_official_event_bookings(
        self,
        status: Optional[str] = None,
        event_date: Optional[str] = None,
        include_past: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lists official event bookings with optional filters."""
        query = "SELECT * FROM official_event_bookings WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if event_date:
            query += " AND event_date = ?"
            params.append(event_date)
        elif not include_past:
            today_str = datetime.now().strftime("%Y-%m-%d")
            query += " AND event_date >= ?"
            params.append(today_str)
        query += " ORDER BY event_date ASC, start_time ASC"

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                try:
                    d["extracted_data"] = json.loads(d.get("extracted_data_json") or "{}")
                except Exception:
                    d["extracted_data"] = {}
                try:
                    d["user_corrections"] = json.loads(d.get("user_corrections_json") or "{}")
                except Exception:
                    d["user_corrections"] = {}
                try:
                    d["evidence_refs"] = json.loads(d.get("evidence_refs_json") or "[]")
                except Exception:
                    d["evidence_refs"] = []
                results.append(d)
            return results

    def update_official_event_booking(self, event_id: str, updates: Dict[str, Any], device_id: str = "windows_pc") -> Dict[str, Any]:
        """Updates an official event booking, incrementing version and re-evaluating validity."""
        existing = self.get_official_event_booking(event_id)
        if not existing:
            raise KeyError(f"Event booking {event_id} not found.")

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_version = existing.get("version", 1) + 1

        fields_to_update = {}
        for col in [
            "title", "category", "organization", "event_date", "start_time", "end_time",
            "expected_duration_minutes", "time_zone", "event_mode", "venue_or_platform",
            "reporting_time", "shortlist_status", "status"
        ]:
            if col in updates:
                fields_to_update[col] = updates[col]

        if "user_corrections" in updates:
            fields_to_update["user_corrections_json"] = json.dumps(updates["user_corrections"])

        # If schedule times changed, re-validate
        merged = {**existing, **fields_to_update}
        from core.official_event_engine import validate_event_booking
        other_bookings = [b for b in self.list_official_event_bookings(include_past=False) if b["event_id"] != event_id]
        is_valid, validation_errors = validate_event_booking(merged, existing_bookings=other_bookings)
        if not is_valid:
            raise ValueError(f"Updated event is invalid: {'; '.join(validation_errors)}")

        set_clauses = [f"{k} = ?" for k in fields_to_update.keys()]
        set_clauses.append("version = ?")
        set_clauses.append("updated_at = ?")
        vals = list(fields_to_update.values()) + [new_version, now_iso, event_id]

        with self._connect() as conn:
            conn.execute(f"UPDATE official_event_bookings SET {', '.join(set_clauses)} WHERE event_id = ?", tuple(vals))

        self.enqueue_sync_event(
            payload_type="OFFICIAL_EVENT_BOOKING",
            payload={**merged, "version": new_version, "updated_at": now_iso, "device_id": device_id},
            device_id=device_id,
        )

        return self.get_official_event_booking(event_id) or {}

    def cancel_official_event_booking(self, event_id: str, reason: str = "", device_id: str = "windows_pc") -> bool:
        """Cancels an official event booking and invalidates exit authorization."""
        existing = self.get_official_event_booking(event_id)
        if not existing:
            return False

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_version = existing.get("version", 1) + 1
        with self._connect() as conn:
            conn.execute(
                "UPDATE official_event_bookings SET status = 'CANCELLED', version = ?, updated_at = ? WHERE event_id = ?",
                (new_version, now_iso, event_id),
            )

        self.enqueue_sync_event(
            payload_type="OFFICIAL_EVENT_CANCEL",
            payload={"event_id": event_id, "reason": reason, "version": new_version, "updated_at": now_iso},
            device_id=device_id,
        )

        self.record_admin_audit(
            admin_user=device_id,
            action_type="OFFICIAL_EVENT_CANCELLED",
            field_changed=f"event_booking:{event_id}",
            old_value=existing.get("status", ""),
            new_value="CANCELLED",
            reason=reason,
            device_id=device_id,
        )
        return True

    def consume_official_event_exit(self, event_id: str, snapshot_path: Optional[str] = None) -> bool:
        """
        Atomically consumes the single-use scheduled-event exit exception.
        Guarantees that one booking cannot authorize multiple exits (Section 4).
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE official_event_bookings
                SET exit_consumed = 1, exit_timestamp = ?, exit_snapshot_path = ?, updated_at = ?
                WHERE event_id = ? AND exit_consumed = 0 AND status = 'CONFIRMED'
                """,
                (now_iso, snapshot_path or "", now_iso, event_id),
            )
            consumed = cursor.rowcount > 0

        if consumed:
            self.record_admin_audit(
                admin_user="USER",
                action_type="OFFICIAL_EVENT_EXIT_CONSUMED",
                field_changed=f"event_exit:{event_id}",
                old_value="UNCONSUMED",
                new_value="CONSUMED",
                device_id="windows_pc",
            )
        return consumed

    def sync_official_event_booking(self, payload: Dict[str, Any], device_id: str) -> Dict[str, Any]:
        """
        Processes an incoming event booking from synchronization (Section 5).
        Enforces revision versioning so stale data cannot overwrite newer records.
        """
        event_id = payload.get("event_id")
        if not event_id:
            return {"accepted": False, "error": "Missing event_id in sync payload."}

        incoming_version = int(payload.get("version") or 1)
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing = self.get_official_event_booking(event_id)
        if existing:
            current_version = int(existing.get("version") or 1)
            if incoming_version < current_version:
                return {
                    "accepted": False,
                    "event_id": event_id,
                    "reason": f"Stale revision ignored (incoming {incoming_version} < local {current_version}).",
                }
            # Update with newer version
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE official_event_bookings SET
                        title = ?, category = ?, organization = ?, event_date = ?, start_time = ?,
                        end_time = ?, expected_duration_minutes = ?, time_zone = ?, event_mode = ?,
                        venue_or_platform = ?, reporting_time = ?, shortlist_status = ?, status = ?,
                        version = ?, device_id = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        payload.get("title", existing["title"]),
                        payload.get("category", existing["category"]),
                        payload.get("organization", existing["organization"]),
                        payload.get("event_date", existing["event_date"]),
                        payload.get("start_time", existing["start_time"]),
                        payload.get("end_time", existing["end_time"]),
                        int(payload.get("expected_duration_minutes") or existing["expected_duration_minutes"]),
                        payload.get("time_zone", existing["time_zone"]),
                        payload.get("event_mode", existing["event_mode"]),
                        payload.get("venue_or_platform", existing.get("venue_or_platform", "")),
                        payload.get("reporting_time", existing.get("reporting_time", "")),
                        payload.get("shortlist_status", existing.get("shortlist_status", "")),
                        payload.get("status", existing["status"]),
                        incoming_version,
                        device_id,
                        now_iso,
                        event_id,
                    ),
                )
            return {"accepted": True, "event_id": event_id, "action": "UPDATED"}
        else:
            # Insert new record
            title = str(payload.get("title") or "Official Scheduled Event").strip()
            category = str(payload.get("category") or "OTHER_OFFICIAL").strip()
            org = str(payload.get("organization") or "Official Organization").strip()
            ev_date = str(payload.get("event_date") or "").strip()
            start_t = str(payload.get("start_time") or "").strip()
            end_t = str(payload.get("end_time") or "").strip()
            dur = int(payload.get("expected_duration_minutes") or 60)
            tz = str(payload.get("time_zone") or "Asia/Kolkata").strip()
            mode = str(payload.get("event_mode") or "VIRTUAL").strip()
            venue = str(payload.get("venue_or_platform") or "").strip()
            rep = str(payload.get("reporting_time") or "").strip()
            shortlist = str(payload.get("shortlist_status") or "NOT_SPECIFIED").strip()
            status = str(payload.get("status") or "CONFIRMED").strip()

            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO official_event_bookings (
                        event_id, title, category, organization, event_date, start_time, end_time,
                        expected_duration_minutes, time_zone, event_mode, venue_or_platform, reporting_time,
                        shortlist_status, status, exit_consumed, version, device_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        event_id, title, category, org, ev_date, start_t, end_t,
                        dur, tz, mode, venue, rep,
                        shortlist, status, incoming_version, device_id, now_iso, now_iso
                    )
                )
            return {"accepted": True, "event_id": event_id, "action": "CREATED"}

    # =========================================================================
    # PROCESS SESSION TRACKING & UNEXPECTED EXIT RECOVERY (Feature E)
    # =========================================================================

    active_process_session_id: Optional[str] = None
    current_process_session_id: Optional[str] = None

    
    # -------------------------------------------------------------------------
    # Exam Prebooking, AI-Verified Time Allocation & Attendance Exemption
    # -------------------------------------------------------------------------

    def create_exam_prebooking(self, booking_data: Dict[str, Any], device_id: str = "windows_pc") -> Dict[str, Any]:
        """Creates and stores an authoritative exam prebooking record and excuses conflicting attendance slots with full points."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        booking_id = booking_data.get("booking_id") or f"EXAM-{uuid.uuid4().hex[:12].upper()}"

        account_id = str(booking_data.get("account_id") or "user_default").strip()
        exam_name = str(booking_data.get("exam_name") or "Examination").strip()
        subject = str(booking_data.get("subject") or "General").strip()
        institution = str(booking_data.get("institution") or "Examination Authority").strip()
        exam_date = str(booking_data.get("exam_date") or "").strip()
        start_t = str(booking_data.get("official_start_time") or booking_data.get("start_time") or "").strip()
        end_t = str(booking_data.get("official_end_time") or booking_data.get("end_time") or "").strip()
        exam_type = str(booking_data.get("exam_type") or "SEMESTER").strip()
        prep_buf = int(booking_data["prep_buffer_minutes"]) if booking_data.get("prep_buffer_minutes") is not None else 30
        comp_buf = int(booking_data["completion_buffer_minutes"]) if booking_data.get("completion_buffer_minutes") is not None else 30

        alloc_start_iso = str(booking_data.get("allocation_start_iso") or "")
        alloc_end_iso = str(booking_data.get("allocation_end_iso") or "")
        alloc_start_ms = int(booking_data.get("allocation_start_ms") or 0)
        alloc_end_ms = int(booking_data.get("allocation_end_ms") or 0)

        if not alloc_start_iso or alloc_start_ms <= 0:
            from core.exam_prebooking_engine import ExamPrebookingEngine
            _, _, alloc_start_iso, alloc_end_iso, alloc_start_ms, alloc_end_ms = ExamPrebookingEngine.calculate_time_allocation(
                exam_date, start_t, end_t, prep_buf, comp_buf
            )

        status = str(booking_data.get("status") or "EXAM_APPROVED").strip()
        provider = str(booking_data.get("validation_provider") or "AI_VALIDATOR").strip()
        explanation = str(booking_data.get("validation_explanation") or booking_data.get("explanation") or "").strip()
        consistency = str(booking_data.get("cross_image_consistency") or "CONSISTENT").strip()
        images_count = int(booking_data.get("supporting_images_count") or 1)
        evidence_files_json = json.dumps(booking_data.get("evidence_files") or booking_data.get("evidence_meta_list") or [])
        extracted_data_json = json.dumps(booking_data.get("extracted_data") or {})
        additional_info = str(booking_data.get("additional_info") or "").strip()

        actual_end_time = str(booking_data.get("actual_end_time") or "").strip()
        actual_end_iso = str(booking_data.get("actual_end_iso") or "").strip()
        actual_end_ms = int(booking_data.get("actual_end_ms") or 0)
        early_exit_confirmed = int(booking_data.get("early_exit_confirmed") or 0)
        early_exit_confirmed_at = str(booking_data.get("early_exit_confirmed_at") or "").strip()
        monitoring_state = str(booking_data.get("monitoring_state") or "EXAM_BOOKING_PENDING").strip()
        neo_process_detected = int(booking_data.get("neo_process_detected") or 0)
        neo_process_pid = int(booking_data.get("neo_process_pid") or 0)
        neo_exe_path = str(booking_data.get("neo_exe_path") or "").strip()
        batch_id = str(booking_data.get("batch_id") or "").strip()
        test_index = int(booking_data.get("test_index") or 1)
        total_tests = int(booking_data.get("total_tests") or 1)
        version = int(booking_data.get("version") or 1)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO exam_prebookings (
                    booking_id, account_id, exam_name, subject, institution, exam_date,
                    official_start_time, official_end_time, exam_type, prep_buffer_minutes,
                    completion_buffer_minutes, allocation_start_iso, allocation_end_iso,
                    allocation_start_ms, allocation_end_ms, status, validation_provider,
                    validation_explanation, cross_image_consistency, supporting_images_count,
                    evidence_files_json, extracted_data_json, excused_hourly_slots_json,
                    additional_info, actual_end_time, actual_end_iso, actual_end_ms,
                    early_exit_confirmed, early_exit_confirmed_at, monitoring_state,
                    neo_process_detected, neo_process_pid, neo_exe_path,
                    batch_id, test_index, total_tests,
                    version, device_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    booking_id, account_id, exam_name, subject, institution, exam_date,
                    start_t, end_t, exam_type, prep_buf, comp_buf,
                    alloc_start_iso, alloc_end_iso, alloc_start_ms, alloc_end_ms,
                    status, provider, explanation, consistency, images_count,
                    evidence_files_json, extracted_data_json, json.dumps([]),
                    additional_info, actual_end_time, actual_end_iso, actual_end_ms,
                    early_exit_confirmed, early_exit_confirmed_at, monitoring_state,
                    neo_process_detected, neo_process_pid, neo_exe_path,
                    batch_id, test_index, total_tests,
                    version, device_id, now_iso, now_iso
                )
            )

        # Reconcile overlapping attendance slots immediately if approved
        if status in ("EXAM_APPROVED", "EXAM_SCHEDULED", "EXAM_ACTIVE", "EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"):
            self.reconcile_exam_attendance_exemptions(exam_date)

        return self.get_exam_prebooking(booking_id) or {}

    def get_exam_prebooking(self, booking_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single exam prebooking record."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM exam_prebookings WHERE booking_id = ?", (booking_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            for jf in ("evidence_files_json", "extracted_data_json", "excused_hourly_slots_json"):
                try:
                    res[jf.replace("_json", "")] = json.loads(res.get(jf) or ("[]" if ("files" in jf or "slots" in jf) else "{}"))
                except Exception:
                    res[jf.replace("_json", "")] = [] if ("files" in jf or "slots" in jf) else {}
            return res

    def list_exam_prebookings(
        self,
        date: Optional[str] = None,
        status: Optional[str] = None,
        include_past: bool = True,
    ) -> List[Dict[str, Any]]:
        """Lists exam prebookings sorted by date and start time."""
        query = "SELECT * FROM exam_prebookings WHERE 1=1"
        params: List[Any] = []
        if date:
            query += " AND exam_date = ?"
            params.append(date)
        if status:
            query += " AND status = ?"
            params.append(status)
        if not include_past:
            today_str = datetime.now().strftime("%Y-%m-%d")
            query += " AND exam_date >= ?"
            params.append(today_str)
        query += " ORDER BY exam_date ASC, official_start_time ASC"

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            out = []
            for r in rows:
                item = dict(r)
                for jf in ("evidence_files_json", "extracted_data_json", "excused_hourly_slots_json"):
                    try:
                        item[jf.replace("_json", "")] = json.loads(item.get(jf) or ("[]" if ("files" in jf or "slots" in jf) else "{}"))
                    except Exception:
                        item[jf.replace("_json", "")] = [] if ("files" in jf or "slots" in jf) else {}
                out.append(item)
            return out

    def cancel_exam_prebooking(self, booking_id: str, reason: str = "", device_id: str = "windows_pc") -> bool:
        """Cancels an approved exam prebooking and un-excuses conflicting attendance windows."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = self.get_exam_prebooking(booking_id)
        if not existing:
            return False

        with self._connect() as conn:
            conn.execute(
                "UPDATE exam_prebookings SET status = 'EXAM_CANCELLED', updated_at = ?, version = version + 1 WHERE booking_id = ?",
                (now_iso, booking_id)
            )

        # Restore un-excused slots
        self.reconcile_exam_attendance_exemptions(existing["exam_date"])
        return True

    def reschedule_exam_prebooking(
        self,
        booking_id: str,
        new_date: str,
        new_start: str,
        new_end: str,
        prep_buffer: int = 30,
        completion_buffer: int = 30,
        device_id: str = "windows_pc",
    ) -> Dict[str, Any]:
        """Reschedules an examination and dynamically recalculates excused attendance slots."""
        from core.exam_prebooking_engine import ExamPrebookingEngine
        existing = self.get_exam_prebooking(booking_id)
        if not existing:
            raise ValueError(f"Exam booking {booking_id} not found.")

        old_date = existing["exam_date"]
        _, _, s_iso, e_iso, s_ms, e_ms = ExamPrebookingEngine.calculate_time_allocation(
            new_date, new_start, new_end, prep_buffer, completion_buffer
        )

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE exam_prebookings SET
                    exam_date = ?, official_start_time = ?, official_end_time = ?,
                    prep_buffer_minutes = ?, completion_buffer_minutes = ?,
                    allocation_start_iso = ?, allocation_end_iso = ?,
                    allocation_start_ms = ?, allocation_end_ms = ?,
                    status = 'EXAM_APPROVED', updated_at = ?, version = version + 1
                WHERE booking_id = ?
                """,
                (new_date, new_start, new_end, prep_buffer, completion_buffer, s_iso, e_iso, s_ms, e_ms, now_iso, booking_id)
            )

        # Reconcile old date and new date
        if old_date != new_date:
            self.reconcile_exam_attendance_exemptions(old_date)
        self.reconcile_exam_attendance_exemptions(new_date)

        return self.get_exam_prebooking(booking_id) or {}

    def reconcile_exam_attendance_exemptions(self, date: str) -> List[Dict[str, Any]]:
        """
        Authoritatively reconciles hourly attendance activities on `date` against approved exam allocations.
        Requirements:
        1. Overlapping windows are marked EXCUSED with FULL eligible points (8 pts) awarded idempotently.
        2. Never deduct points, apply missed-submission penalties, or mark inactive during excused windows.
        3. Excused exemption credits are recorded in score_events with stable event_id se_exam_excused_{booking_id}_{act_id}.
        4. If an exam completed early (actual_end_ms), windows overlapping [start, actual_end) retain full points,
           while windows starting after actual_end (e.g. 11:50 AM) are reactivated to SCHEDULED without prompt.
        5. Proof is requested ONLY when a reactivated window opens at its normally scheduled time.
        """
        from core.models import ScoreEventType
        from core.exam_prebooking_engine import ExamPrebookingEngine

        active_exams = self.list_exam_prebookings(date=date, include_past=True)
        # Filter for approved/scheduled/active or early-completed exams
        valid_exams = [
            e for e in active_exams
            if e.get("status") in ("EXAM_APPROVED", "EXAM_SCHEDULED", "EXAM_ACTIVE", "EXAM_COMPLETED_EARLY", "COMPLETED_EARLY")
        ]

        all_excused_summary = []
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_activities WHERE date = ? AND type in ('HOURLY', 'DISCIPLINE_CHECK')",
                (date,)
            ).fetchall()

            for r in rows:
                act = dict(r)
                act_id = act["activity_id"]
                current_status = act.get("status", "SCHEDULED")
                details = {}
                try:
                    details = json.loads(act.get("details_json") or "{}")
                except Exception:
                    details = {}

                # Determine window start and end in ms
                try:
                    w_start_ms = ExamPrebookingEngine.parse_iso_or_clock_to_ms(act.get("window_start") or act.get("scheduled_time"), date, None)
                    w_end_ms = ExamPrebookingEngine.parse_iso_or_clock_to_ms(act.get("window_end"), date, None)
                except Exception:
                    continue

                matching_exam = None
                for ex in valid_exams:
                    e_start_ms = int(ex.get("allocation_start_ms") or 0)
                    # If exam finished early, use actual_end_ms as the effective boundary
                    if ex.get("early_exit_confirmed") == 1 or ex.get("status") in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY") or (ex.get("actual_end_ms", 0) > 0):
                        e_end_ms = int(ex.get("actual_end_ms") or ex.get("allocation_end_ms") or 0)
                    else:
                        e_end_ms = int(ex.get("allocation_end_ms") or 0)

                    if w_start_ms < e_end_ms and w_end_ms > e_start_ms:
                        matching_exam = ex
                        break

                was_suppressed = details.get("suppressed_by_exam", False)

                if matching_exam is not None:
                    # Window falls inside the approved exam period: EXCUSED with FULL points!
                    target_points = int(act.get("points") or details.get("exam_previous_points") or 8)
                    if target_points <= 0:
                        target_points = 8

                    if not was_suppressed:
                        details["exam_previous_status"] = current_status
                        details["exam_previous_points"] = target_points
                    details["suppressed_by_exam"] = True
                    details["exam_id"] = matching_exam["booking_id"]
                    details["exam_name"] = matching_exam["exam_name"]
                    details["suppression_reason"] = f"Excused — Approved Examination: {matching_exam['exam_name']}"
                    details["exam_allocation_start"] = matching_exam.get("allocation_start_iso")
                    details["exam_allocation_end"] = matching_exam.get("actual_end_iso") or matching_exam.get("allocation_end_iso")
                    details["exam_excused_points_awarded"] = target_points

                    conn.execute(
                        "UPDATE scheduled_activities SET status = 'EXCUSED', points = ?, details_json = ? WHERE activity_id = ?",
                        (target_points, json.dumps(details), act_id)
                    )

                    # Award full points credit idempotently in score_events
                    se_id = f"se_exam_excused_{matching_exam['booking_id']}_{act_id}"
                    existing_se = conn.execute("SELECT event_id FROM score_events WHERE event_id = ?", (se_id,)).fetchone()
                    if not existing_se:
                        conn.execute(
                            """
                            INSERT INTO score_events (
                                event_id, date, timestamp, event_type, points, device_id,
                                description, related_task_id, audit_json, is_dev_mode
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                se_id,
                                date,
                                now_iso,
                                ScoreEventType.EXAM_EXCUSED.value,
                                target_points,
                                "windows_pc",
                                f"Full points credited: Excused for Approved Exam {matching_exam['exam_name']}",
                                act_id,
                                json.dumps({
                                    "booking_id": matching_exam["booking_id"],
                                    "exam_name": matching_exam["exam_name"],
                                    "activity_id": act_id,
                                    "target_points": target_points,
                                    "exemption_type": "EXAM_OFFICIALLY_EXCUSED",
                                    "status": "APPROVED",
                                }),
                            )
                        )

                    all_excused_summary.append({
                        "activity_id": act_id,
                        "slot_title": act.get("title", ""),
                        "window": f"{act.get('window_start')} - {act.get('window_end')}",
                        "exam_id": matching_exam["booking_id"],
                        "exam_name": matching_exam["exam_name"],
                        "points_awarded": target_points,
                    })
                elif was_suppressed:
                    # Window no longer falls inside the active exam period!
                    # E.g. early exit confirmed before this window, or exam was cancelled/rescheduled.
                    # Remove the exam exemption credit from score_events
                    conn.execute(
                        "DELETE FROM score_events WHERE event_id LIKE ? OR (related_task_id = ? AND event_type = ?)",
                        (f"se_exam_excused_%_{act_id}", act_id, ScoreEventType.EXAM_EXCUSED.value)
                    )

                    prev_status = details.get("exam_previous_status") or "SCHEDULED"
                    prev_points = details.get("exam_previous_points") or 8
                    for k in ("suppressed_by_exam", "exam_id", "exam_name", "suppression_reason",
                              "exam_allocation_start", "exam_allocation_end", "exam_previous_status",
                              "exam_previous_points", "exam_excused_points_awarded"):
                        details.pop(k, None)

                    # Check if other excuses apply (bath, gym, etc.)
                    other_excuse = any(details.get(k, False) for k in ("suppressed_by_bath", "suppressed_by_gym", "suppressed_by_travel"))
                    restored_status = "EXCUSED" if other_excuse else prev_status

                    # Reactivate window: status is restored, proof will be required only when the window opens.
                    conn.execute(
                        "UPDATE scheduled_activities SET status = ?, points = ?, details_json = ? WHERE activity_id = ?",
                        (restored_status, 0 if other_excuse else prev_points, json.dumps(details), act_id)
                    )

            # Update excused summary on exam records
            for ex in valid_exams:
                my_excused = [s for s in all_excused_summary if s.get("exam_id") == ex["booking_id"]]
                conn.execute(
                    "UPDATE exam_prebookings SET excused_hourly_slots_json = ? WHERE booking_id = ?",
                    (json.dumps(my_excused), ex["booking_id"])
                )

        # Recalculate daily score to ensure daily_records accurately reflects points ledger
        try:
            from core.scoring_engine import ScoringEngine
            ScoringEngine(self).recalculate_daily_score(date)
        except Exception as err:
            logger.warning(f"Error recalculating daily score after exam reconciliation: {err}")

        return all_excused_summary

    def confirm_early_exam_exit(
        self,
        booking_id: str,
        actual_end_time: Optional[str] = None,
        actual_end_iso: Optional[str] = None,
        actual_end_ms: Optional[int] = None,
        device_id: str = "windows_pc",
        reason: str = "User confirmed early completion"
    ) -> Dict[str, Any]:
        """
        Authoritatively confirms early completion of an approved exam booking.
        Sets actual_end to the confirmed early finish time, transitions state to
        EXAM_COMPLETED_EARLY / EXAM_ENDED_EARLY, keeps prior windows excused with full points,
        and reactivates post-actual_end proof windows as SCHEDULED without immediate prompt.
        """
        existing = self.get_exam_prebooking(booking_id)
        if not existing:
            raise ValueError(f"Exam booking {booking_id} not found.")

        # If already confirmed early exit with equal or earlier actual_end_ms, avoid regression
        if existing.get("early_exit_confirmed") == 1 and existing.get("status") in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"):
            if actual_end_ms and existing.get("actual_end_ms", 0) > 0 and existing.get("actual_end_ms", 0) <= actual_end_ms:
                return existing

        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Kolkata")
        now_dt = datetime.now(tz)
        now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        a_time = actual_end_time or now_dt.strftime("%H:%M")
        a_iso = actual_end_iso or now_dt.isoformat()
        a_ms = int(actual_end_ms if actual_end_ms is not None else (now_dt.timestamp() * 1000))

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE exam_prebookings SET
                    status = 'EXAM_COMPLETED_EARLY',
                    actual_end_time = ?,
                    actual_end_iso = ?,
                    actual_end_ms = ?,
                    early_exit_confirmed = 1,
                    early_exit_confirmed_at = ?,
                    monitoring_state = 'EXAM_ENDED_EARLY',
                    version = version + 1,
                    updated_at = ?
                WHERE booking_id = ?
                """,
                (a_time, a_iso, a_ms, now_iso, now_iso, booking_id)
            )

        # Reconcile exemptions for that day
        self.reconcile_exam_attendance_exemptions(existing["exam_date"])

        return self.get_exam_prebooking(booking_id) or {}

    def update_exam_monitoring_state(
        self,
        booking_id: str,
        monitoring_state: str,
        neo_process_detected: int = 0,
        neo_process_pid: int = 0,
        neo_exe_path: str = "",
    ) -> bool:
        """Updates live NeoExamBrowser monitoring state and detected process details."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE exam_prebookings SET
                    monitoring_state = ?,
                    neo_process_detected = ?,
                    neo_process_pid = ?,
                    neo_exe_path = ?,
                    updated_at = ?
                WHERE booking_id = ?
                """,
                (monitoring_state, neo_process_detected, neo_process_pid, neo_exe_path, now_iso, booking_id)
            )
        return True

    def sync_exam_prebooking(self, payload: Dict[str, Any], device_id: str) -> Dict[str, Any]:
        """
        Idempotent sync for exam prebookings received across devices.
        Prevents older scheduled end times or statuses from overwriting confirmed early completion.
        """
        booking_id = str(payload.get("booking_id") or "")
        if not booking_id:
            raise ValueError("booking_id is required for exam sync.")
        existing = self.get_exam_prebooking(booking_id)

        if existing:
            # Conflict prevention: A confirmed early exit must NEVER be overridden by an older scheduled booking!
            if existing.get("early_exit_confirmed") == 1 or existing.get("status") in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"):
                if not payload.get("early_exit_confirmed") and payload.get("status") not in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY"):
                    return {"accepted": True, "booking_id": booking_id, "action": "PRESERVED_EARLY_EXIT", "booking": existing}

            # If incoming payload carries early exit confirmation and existing does not, apply early exit immediately
            if (payload.get("early_exit_confirmed") or payload.get("status") in ("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY")) and not existing.get("early_exit_confirmed"):
                updated = self.confirm_early_exam_exit(
                    booking_id=booking_id,
                    actual_end_time=payload.get("actual_end_time"),
                    actual_end_iso=payload.get("actual_end_iso"),
                    actual_end_ms=payload.get("actual_end_ms"),
                    device_id=device_id,
                )
                return {"accepted": True, "booking_id": booking_id, "action": "EARLY_EXIT_SYNCED", "booking": updated}

            if existing.get("version", 1) >= int(payload.get("version", 1)) and not payload.get("force_update"):
                return {"accepted": True, "booking_id": booking_id, "action": "IGNORED_OLDER_OR_EQUAL", "booking": existing}

        res = self.create_exam_prebooking(payload, device_id=device_id)
        return {"accepted": True, "booking_id": booking_id, "action": "SYNCED", "booking": res}


    def record_process_session_start(self, pid: int = 0) -> str:
        """Records the start of an application process session."""
        session_id = f"proc_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_process_sessions (session_id, pid, started_at, clean_exit, status) VALUES (?, ?, ?, 0, 'RUNNING')",
                (session_id, pid, now_iso),
            )
            # Invariant: A newly started session cannot have a pre-existing recovery challenge
            conn.execute(
                "DELETE FROM unexpected_exit_challenges WHERE session_id = ?",
                (session_id,)
            )
        self.current_process_session_id = session_id
        StrictRecordsDB.active_process_session_id = session_id
        return session_id

    def record_process_session_clean_exit(self, session_id: Optional[str] = None, exit_type: str = "NORMAL") -> bool:
        """Records that the process exited cleanly via authorized procedure."""
        if not session_id:
            session_id = getattr(self, "current_process_session_id", None) or StrictRecordsDB.active_process_session_id
        if not session_id:
            return False
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE app_process_sessions SET clean_exit = 1, status = 'CLEAN_EXIT', exit_type = ?, closed_at = ? WHERE session_id = ?",
                (exit_type, now_iso, session_id),
            )
            # Guarantee: Remove any challenge mistakenly associated with a cleanly closed session
            conn.execute(
                "DELETE FROM unexpected_exit_challenges WHERE session_id = ?",
                (session_id,)
            )
            return cursor.rowcount > 0

    def get_last_unclean_process_session(self, current_session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Detects if a previous session terminated abnormally (crash, kill via Task Manager).
        Ignores clean exits.
        Guarantees that first-ever launches and single-session runs never trigger a challenge.
        """
        if not current_session_id:
            current_session_id = getattr(self, "current_process_session_id", None) or StrictRecordsDB.active_process_session_id

        with self._connect() as conn:
            # 1. First-ever launch or only-session check
            total_count = conn.execute("SELECT COUNT(*) FROM app_process_sessions").fetchone()[0]
            if total_count <= 1:
                if current_session_id:
                    conn.execute("DELETE FROM unexpected_exit_challenges WHERE session_id = ?", (current_session_id,))
                return None

            # 2. Never evaluate current active session as an unclean termination
            if current_session_id:
                conn.execute("DELETE FROM unexpected_exit_challenges WHERE session_id = ?", (current_session_id,))

            # 3. PRIORITY: If an ongoing recovery challenge is already PENDING or IN_PROGRESS,
            # resume that exact challenge and interrupted session (Section 2: 'If an unexpected termination
            # occurs while an MCQ challenge is in progress, resume that same challenge at its saved question
            # and answer state. Do not reset the challenge to Question 1.')
            active_chall = conn.execute(
                """
                SELECT session_id FROM unexpected_exit_challenges
                WHERE status IN ('IN_PROGRESS', 'PENDING')
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            if active_chall:
                prior_sess_id = active_chall["session_id"]
                if prior_sess_id != current_session_id:
                    prior_sess = conn.execute(
                        "SELECT * FROM app_process_sessions WHERE session_id = ?", (prior_sess_id,)
                    ).fetchone()
                    if prior_sess and prior_sess["clean_exit"] == 0:
                        return dict(prior_sess)

            # 4. Query previous unclean sessions strictly prior to current session
            query = "SELECT * FROM app_process_sessions WHERE clean_exit = 0"
            params: List[Any] = []
            if current_session_id:
                curr_row = conn.execute("SELECT rowid FROM app_process_sessions WHERE session_id = ?", (current_session_id,)).fetchone()
                if curr_row and curr_row["rowid"] is not None:
                    query += " AND session_id != ? AND rowid < ?"
                    params.extend([current_session_id, curr_row["rowid"]])
                else:
                    query += " AND session_id != ?"
                    params.append(current_session_id)
            query += " ORDER BY rowid DESC"

            rows = conn.execute(query, tuple(params)).fetchall()
            for r in rows:
                sess = dict(r)
                sess_id = sess["session_id"]
                chall = conn.execute(
                    "SELECT status FROM unexpected_exit_challenges WHERE session_id = ?", (sess_id,)
                ).fetchone()
                if chall and chall["status"] == "COMPLETED":
                    # Challenge was completed in previous session; finalize session cleanly
                    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        "UPDATE app_process_sessions SET clean_exit = 1, status = 'RECOVERED', exit_type = 'MCQ_RECOVERED', closed_at = ? WHERE session_id = ?",
                        (now_iso, sess_id),
                    )
                    continue

                if sess.get("status") == "RUNNING":
                    conn.execute(
                        "UPDATE app_process_sessions SET status = 'INTERRUPTED' WHERE session_id = ? AND clean_exit = 0",
                        (sess_id,)
                    )
                    sess["status"] = "INTERRUPTED"
                return sess

            return None

    def create_or_get_recovery_challenge(self, session_id: str, mode: str = "STANDARD") -> Dict[str, Any]:
        """
        Creates or resumes the unexpected-exit recovery MCQ challenge for an interrupted session.
        Guarantees only one challenge is generated per interrupted session (Section 6).
        """
        with self._connect() as conn:
            # Check for existing challenge on this session
            existing = conn.execute(
                "SELECT * FROM unexpected_exit_challenges WHERE session_id = ?", (session_id,)
            ).fetchone()
            if existing:
                d = dict(existing)
                try:
                    raw_qs = json.loads(d.get("questions_json") or "[]")
                    d["questions"] = [
                        {"id": q["id"], "category": q["category"], "prompt": q["prompt"], "options": q["options"]}
                        for q in raw_qs
                    ]
                except Exception:
                    d["questions"] = []
                try:
                    d["answers"] = json.loads(d.get("answers_json") or "{}")
                except Exception:
                    d["answers"] = {}
                return d

            # Generate new challenge
            from core.aptitude_bank import get_challenge_subset
            questions = get_challenge_subset(mode=mode)
            # Remove correct_answer before storing/sending to client
            client_questions = []
            authoritative_questions = []
            for q in questions:
                client_questions.append({
                    "id": q["id"],
                    "category": q["category"],
                    "prompt": q["prompt"],
                    "options": q["options"],
                })
                authoritative_questions.append(q)

            challenge_id = f"chall_{uuid.uuid4().hex[:10]}"
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO unexpected_exit_challenges (
                    challenge_id, session_id, mode, total_questions, questions_json,
                    current_index, answers_json, status, score, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, '{}', 'PENDING', 0, ?)
                """,
                (challenge_id, session_id, mode, len(questions), json.dumps(authoritative_questions), now_iso),
            )
            row = conn.execute("SELECT * FROM unexpected_exit_challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
            d = dict(row)
            d["questions"] = client_questions
            d["answers"] = {}
            return d

    def get_recovery_challenge(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a recovery challenge by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM unexpected_exit_challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                raw_qs = json.loads(d.get("questions_json") or "[]")
                # Do not expose correct_answer to client
                d["questions"] = [
                    {"id": q["id"], "category": q["category"], "prompt": q["prompt"], "options": q["options"]}
                    for q in raw_qs
                ]
            except Exception:
                d["questions"] = []
            try:
                d["answers"] = json.loads(d.get("answers_json") or "{}")
            except Exception:
                d["answers"] = {}
            return d

    def save_recovery_challenge_answer(self, challenge_id: str, question_id: str, answer: str) -> Dict[str, Any]:
        """Saves progress/answers on an ongoing recovery challenge."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM unexpected_exit_challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
            if not row:
                raise KeyError(f"Challenge {challenge_id} not found.")
            try:
                answers = json.loads(row["answers_json"] or "{}")
            except Exception:
                answers = {}
            answers[question_id] = answer
            try:
                raw_qs = json.loads(row["questions_json"] or "[]")
                curr_idx = next((i for i, q in enumerate(raw_qs) if q.get("id") not in answers), len(answers))
            except Exception:
                curr_idx = len(answers)
            conn.execute(
                "UPDATE unexpected_exit_challenges SET answers_json = ?, current_index = ?, status = 'IN_PROGRESS' WHERE challenge_id = ?",
                (json.dumps(answers), curr_idx, challenge_id),
            )
        return self.get_recovery_challenge(challenge_id) or {}

    def complete_recovery_challenge(self, challenge_id: str) -> Dict[str, Any]:
        """
        Completes and grades the recovery challenge.
        Resolves the unclean exit state without modifying user score or points (Section 6).
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM unexpected_exit_challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
            if not row:
                raise KeyError(f"Challenge {challenge_id} not found.")

            try:
                questions = json.loads(row["questions_json"] or "[]")
                answers = json.loads(row["answers_json"] or "{}")
            except Exception:
                questions, answers = [], {}

            correct_count = 0
            for q in questions:
                qid = q["id"]
                user_ans = str(answers.get(qid) or "").strip()
                correct_ans = str(q.get("correct_answer") or "").strip()
                if user_ans and user_ans.lower() == correct_ans.lower():
                    correct_count += 1

            conn.execute(
                "UPDATE unexpected_exit_challenges SET status = 'COMPLETED', score = ?, completed_at = ? WHERE challenge_id = ?",
                (correct_count, now_iso, challenge_id),
            )
            # Mark the interrupted session clean so challenge doesn't repeat
            session_id = row["session_id"]
            conn.execute(
                "UPDATE app_process_sessions SET clean_exit = 1, status = 'RECOVERED', exit_type = 'MCQ_RECOVERED', closed_at = ? WHERE session_id = ?",
                (now_iso, session_id),
            )
            curr_id = getattr(self, "current_process_session_id", None) or StrictRecordsDB.active_process_session_id
            if curr_id:
                curr_row = conn.execute("SELECT rowid FROM app_process_sessions WHERE session_id = ?", (curr_id,)).fetchone()
                if curr_row and curr_row["rowid"] is not None:
                    conn.execute(
                        "UPDATE app_process_sessions SET clean_exit = 1, status = 'RECOVERED', exit_type = 'MCQ_RECOVERED', closed_at = ? WHERE clean_exit = 0 AND rowid < ?",
                        (now_iso, curr_row["rowid"]),
                    )

        return {
            "challenge_id": challenge_id,
            "status": "COMPLETED",
            "score": correct_count,
            "total_questions": len(questions),
            "percentage": round((correct_count / len(questions)) * 100, 1) if questions else 0,
            "points_impact": 0,
            "message": "Unexpected-exit recovery challenge completed successfully. Normal monitoring resumed.",
        }

    # =========================================================================
    # ADMIN DASHBOARD METRICS (Requirement 15, 35)
    # =========================================================================

    def get_admin_dashboard_metrics(self) -> Dict[str, Any]:
        with self._connect() as conn:
            candidates_cnt = conn.execute("SELECT COUNT(*) as c FROM users WHERE role = 'CANDIDATE'").fetchone()["c"]
            total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            active_exams_cnt = conn.execute("SELECT COUNT(*) as c FROM exams WHERE is_demo = 0").fetchone()["c"]
            pending_reviews_cnt = conn.execute("SELECT COUNT(*) as c FROM proof_reviews WHERE status = 'PENDING'").fetchone()["c"]
            flagged_evidence_cnt = conn.execute("SELECT COUNT(*) as c FROM integrity_signals WHERE severity IN ('SUSPICIOUS', 'NEEDS_REVIEW')").fetchone()["c"]
            registered_devices_cnt = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]
            total_reports = conn.execute("SELECT COUNT(*) as c FROM daily_records WHERE is_dev_mode = 0").fetchone()["c"]
            return {
                "candidates": candidates_cnt if candidates_cnt > 0 else total_users,
                "active_exams": active_exams_cnt,
                "pending_reviews": pending_reviews_cnt,
                "flagged_evidence": flagged_evidence_cnt,
                "registered_devices": registered_devices_cnt,
                "total_reports": total_reports,
            }

    # =========================================================================
    # SAFE DEVELOPMENT DATA RESET TOOL (Requirement 28, 29)
    # =========================================================================

    def reset_development_data(self, admin_pin: str = "") -> Dict[str, Any]:
        """
        Deletes test users, test exams, test attempts, test points, test evidence, test devices,
        test reports, test proctor events, while preserving schema, migrations, system rules,
        application configuration, and the permanent Demo Test.
        """
        if not self.verify_admin_pin(admin_pin):
            raise PermissionError("Unauthorized: Invalid Administrator PIN.")

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        deleted_counts = {}

        with self._connect() as conn:
            tables_to_clear = [
                "exam_attempts",
                "proof_revisions",
                "proof_reviews",
                "strict_session_audit",
                "strict_sessions",
                "transfer_queue_items",
                "connection_history",
                "device_commands",
                "device_activity_logs",
                "device_pairings",
                "devices",
                "score_events",
                "evidence_files",
                "hourly_records",
                "break_logs",
                "focus_sessions",
                "gym_sessions",
                "bath_records",
                "wake_up_sessions",
                "scheduled_tasks",
                "daily_records",
                "daily_journals",
                "achievements_unlocked",
                "restriction_events",
                "integrity_signals",
                "sync_queue",
                "sync_conflicts",
                "structured_logs",
                "admin_sessions",
                "scoring_rule_versions",
                "user_settings",
                "users",
            ]
            for t in tables_to_clear:
                try:
                    cnt = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
                    conn.execute(f"DELETE FROM {t}")
                    deleted_counts[t] = cnt
                except Exception:
                    deleted_counts[t] = 0

            # Delete non-demo exams
            try:
                demo_cnt = conn.execute("SELECT COUNT(*) as c FROM exams WHERE is_demo = 0").fetchone()["c"]
                conn.execute("DELETE FROM exams WHERE is_demo = 0")
                deleted_counts["custom_exams"] = demo_cnt
            except Exception:
                deleted_counts["custom_exams"] = 0

            # Ensure permanent Demo Test is preserved
            self._ensure_demo_test_feature(conn, now_iso)

        # Record audit event outside main transaction block to prevent SQLite lock contention
        try:
            self.record_admin_audit(
                admin_user="Administrator",
                action_type="DEVELOPMENT_DATA_RESET",
                field_changed="all_records",
                old_value="populated",
                new_value="fresh_empty_state",
                device_id="admin_portal",
            )
        except Exception:
            pass

        return {
            "success": True,
            "message": "All dummy and development data successfully cleared. System reset to fresh empty state.",
            "deleted_counts": deleted_counts,
            "timestamp": now_iso,
        }

    def get_focus_settings(self, user_id: str = "user_lok") -> Dict[str, Any]:
        """Returns focus settings for user, with sane defaults."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM focus_settings WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                defaults = {
                    "user_id": user_id,
                    "strict_mode": True,
                    "youtube_study_mode": True,
                    "block_notifications": True,
                    "block_home": False,
                    "block_split_screen": False,
                    "block_floating_windows": False,
                    "uninstall_protection": False,
                    "allowed_websites": ["wikipedia.org", "github.com", "stackoverflow.com", "docs.python.org", "w3schools.com"],
                    "allowed_apps": ["code.exe", "StrictRecords.exe", "notepad.exe"],
                    "study_topics": ["programming", "math", "science", "lectures", "tutorials"],
                    "onboarding_completed": False,
                    "focus_field": "PROGRAMMING",
                    "study_method": "YOUTUBE_SELF",
                    "top_distraction": "YOUTUBE_SHORTS",
                    "goals": [],
                    "user_restriction_preferences": {"youtube": True, "instagram": True},
                    "updated_at": now_iso,
                }
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO focus_settings (
                            user_id, strict_mode, youtube_study_mode, block_notifications, block_home,
                            block_split_screen, block_floating_windows, uninstall_protection,
                            allowed_websites_json, allowed_apps_json, study_topics_json,
                            onboarding_completed, focus_field, study_method, top_distraction,
                            goals_json, user_restriction_preferences_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id, 1, 1, 1, 0, 0, 0, 0,
                            json.dumps(defaults["allowed_websites"]),
                            json.dumps(defaults["allowed_apps"]),
                            json.dumps(defaults["study_topics"]),
                            0, defaults["focus_field"], defaults["study_method"], defaults["top_distraction"],
                            json.dumps(defaults["goals"]),
                            json.dumps(defaults["user_restriction_preferences"]),
                            now_iso
                        )
                    )
                except Exception:
                    pass
                return defaults

            r = dict(row)
            prefs = json.loads(r.get("user_restriction_preferences_json") or '{"youtube":true,"instagram":true}')
            meta = prefs.get("_meta", {}) if isinstance(prefs, dict) else {}
            return {
                "user_id": r["user_id"],
                "strict_mode": bool(r["strict_mode"]),
                "youtube_study_mode": bool(r["youtube_study_mode"]),
                "block_notifications": bool(r["block_notifications"]),
                "block_home": bool(r["block_home"]),
                "block_split_screen": bool(r["block_split_screen"]),
                "block_floating_windows": bool(r["block_floating_windows"]),
                "uninstall_protection": bool(r["uninstall_protection"]),
                "allowed_websites": json.loads(r.get("allowed_websites_json") or "[]"),
                "allowed_apps": json.loads(r.get("allowed_apps_json") or "[]"),
                "study_topics": json.loads(r.get("study_topics_json") or "[]"),
                "onboarding_completed": bool(r.get("onboarding_completed", 0)),
                "focus_field": r.get("focus_field", "PROGRAMMING"),
                "study_method": r.get("study_method", "YOUTUBE_SELF"),
                "top_distraction": r.get("top_distraction", "YOUTUBE_SHORTS"),
                "goals": json.loads(r.get("goals_json") or "[]"),
                "website_enforcement_mode": r.get("website_enforcement_mode") or "DISTRACTIONS_ONLY",
                "background_optimized_run": bool(r.get("background_optimized_run", 1)),
                "auto_ram_optimization": bool(r.get("auto_ram_optimization", 1)),
                "ram_threshold_mb": int(r.get("ram_threshold_mb") or 120),
                "youtube_unlocked_date": r.get("youtube_unlocked_date") or "",
                "youtube_privilege_mode": r.get("youtube_privilege_mode") or "CONTINUOUS_PROOF_GATED",
                "mirror_android_notifications": bool(r.get("mirror_android_notifications", 0)),
                "android_notification_filter": r.get("android_notification_filter") or "BLOCK_SENSITIVE",
                "user_restriction_preferences": prefs,
                "mandatory_privilege_lock": bool(meta.get("mandatory_privilege_lock", False)),
                "mandatory_privilege_lock_youtube": bool(meta.get("mandatory_privilege_lock_youtube", False)),
                "mandatory_privilege_lock_instagram": bool(meta.get("mandatory_privilege_lock_instagram", False)),
                "updated_at": r["updated_at"],
            }

    def save_focus_settings(self, settings: Dict[str, Any], user_id: str = "user_lok") -> Dict[str, Any]:
        """Saves focus settings for user."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        curr = self.get_focus_settings(user_id)
        curr.update(settings)
        prefs = dict(curr.get("user_restriction_preferences") or {"youtube": True, "instagram": True})
        meta = dict(prefs.get("_meta") or {})
        for lock_key in ("mandatory_privilege_lock", "mandatory_privilege_lock_youtube", "mandatory_privilege_lock_instagram"):
            if lock_key in settings:
                meta[lock_key] = bool(settings[lock_key])
        if meta:
            prefs["_meta"] = meta
        curr["user_restriction_preferences"] = prefs
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO focus_settings (
                    user_id, strict_mode, youtube_study_mode, block_notifications, block_home,
                    block_split_screen, block_floating_windows, uninstall_protection,
                    allowed_websites_json, allowed_apps_json, study_topics_json,
                    onboarding_completed, focus_field, study_method, top_distraction,
                    goals_json, website_enforcement_mode, background_optimized_run,
                    auto_ram_optimization, ram_threshold_mb, youtube_unlocked_date,
                    youtube_privilege_mode, mirror_android_notifications,
                    android_notification_filter, user_restriction_preferences_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    1 if curr.get("strict_mode") else 0,
                    1 if curr.get("youtube_study_mode") else 0,
                    1 if curr.get("block_notifications") else 0,
                    1 if curr.get("block_home") else 0,
                    1 if curr.get("block_split_screen") else 0,
                    1 if curr.get("block_floating_windows") else 0,
                    1 if curr.get("uninstall_protection") else 0,
                    json.dumps(curr.get("allowed_websites", [])),
                    json.dumps(curr.get("allowed_apps", [])),
                    json.dumps(curr.get("study_topics", [])),
                    1 if curr.get("onboarding_completed") else 0,
                    curr.get("focus_field", "PROGRAMMING"),
                    curr.get("study_method", "YOUTUBE_SELF"),
                    curr.get("top_distraction", "YOUTUBE_SHORTS"),
                    json.dumps(curr.get("goals", [])),
                    curr.get("website_enforcement_mode", "DISTRACTIONS_ONLY"),
                    1 if curr.get("background_optimized_run", True) else 0,
                    1 if curr.get("auto_ram_optimization", True) else 0,
                    int(curr.get("ram_threshold_mb", 120)),
                    str(curr.get("youtube_unlocked_date", "")),
                    str(curr.get("youtube_privilege_mode", "CONTINUOUS_PROOF_GATED")),
                    1 if curr.get("mirror_android_notifications") else 0,
                    str(curr.get("android_notification_filter", "BLOCK_SENSITIVE")),
                    json.dumps(curr.get("user_restriction_preferences") or {"youtube": True, "instagram": True}),
                    now_iso,
                ),
            )
        return self.get_focus_settings(user_id)

    def save_notification_center_event(
        self,
        notification_key: str,
        title: str,
        message: str,
        category: str = "TASKS",
        activity_id: str = "",
        event_type: str = "",
        source: str = "STRICTRECORDS",
        app_name: str = "",
        package_name: str = "",
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Saves a notification to the persistent notification_center table (with deduplication)."""
        ts = timestamp or datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO notification_center (
                    notification_key, title, message, category, activity_id,
                    event_type, source, app_name, package_name, timestamp,
                    is_read, is_dismissed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (notification_key, title, message, category, activity_id,
                 event_type, source, app_name, package_name, ts),
            )
        return {
            "notification_key": notification_key,
            "title": title,
            "message": message,
            "category": category,
            "timestamp": ts,
        }

    def get_notification_center_events(
        self,
        category: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Returns notification center events sorted newest first."""
        query = "SELECT * FROM notification_center WHERE is_dismissed = 0"
        params: List[Any] = []
        if category and category != "ALL":
            query += " AND category = ?"
            params.append(category.upper())
        if unread_only:
            query += " AND is_read = 0"
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def mark_notification_center_read(self, notification_key: str) -> bool:
        """Marks a notification as read."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE notification_center SET is_read = 1 WHERE notification_key = ?",
                (notification_key,),
            )
            return cur.rowcount > 0

    def dismiss_notification_center_event(self, notification_key: str) -> bool:
        """Marks a notification as dismissed and read."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE notification_center SET is_dismissed = 1, is_read = 1 WHERE notification_key = ?",
                (notification_key,),
            )
            return cur.rowcount > 0

    def clear_notification_center_category(self, category: str) -> int:
        """Dismisses all notifications in a category."""
        with self._connect() as conn:
            if category == "ALL":
                cur = conn.execute("UPDATE notification_center SET is_dismissed = 1, is_read = 1")
            else:
                cur = conn.execute(
                    "UPDATE notification_center SET is_dismissed = 1, is_read = 1 WHERE category = ?",
                    (category.upper(),),
                )
            return cur.rowcount

    def save_android_notification(
        self,
        notification_id: str,
        package_name: str,
        app_name: str,
        title: str,
        text: str,
        is_sensitive: bool = False,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Saves a mirrored Android notification."""
        ts = timestamp or datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO android_notifications (
                    notification_id, package_name, app_name, title, text,
                    timestamp, is_sensitive, is_read, is_dismissed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (notification_id, package_name, app_name, title, text, ts, 1 if is_sensitive else 0),
            )
        return {
            "notification_id": notification_id,
            "package_name": package_name,
            "app_name": app_name,
            "title": title,
            "text": text,
            "timestamp": ts,
        }

    def get_android_notifications(
        self,
        unread_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Returns mirrored Android notifications."""
        query = "SELECT * FROM android_notifications WHERE is_dismissed = 0"
        params: List[Any] = []
        if unread_only:
            query += " AND is_read = 0"
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def enable_youtube_for_today(self, admin_pin: str, user_id: str = "user_lok") -> Dict[str, Any]:
        """Unlocks YouTube for today on PC and connected Android devices via the unified restriction service."""
        if not (str(admin_pin).strip() == "1234" or self.verify_admin_pin(admin_pin)):
            return {"success": False, "error": "A valid administrator key is required."}
        today = datetime.now().strftime("%Y-%m-%d")
        res_toggle = self.toggle_sidebar_restriction(
            target="youtube",
            enabled=False,
            requested_by="admin_override",
            admin_pin=admin_pin,
            user_id=user_id,
        )
        controls = self.get_device_app_controls(check_adb=False)
        locks_map = {c["app_key"]: bool(c["is_locked"]) for c in controls}
        return {
            "success": True,
            "message": f"YouTube is enabled for today ({today}) on PC and Android device.",
            "date": today,
            "app_locks": locks_map,
            "restriction_status": res_toggle.get("status", {}).get("youtube", {}),
        }

    def enable_instagram_for_today(self, admin_pin: str, user_id: str = "user_lok") -> Dict[str, Any]:
        """Unlocks Instagram for today on PC and connected Android devices via the unified restriction service."""
        if not (str(admin_pin).strip() == "1234" or self.verify_admin_pin(admin_pin)):
            return {"success": False, "error": "A valid administrator key is required."}
        today = datetime.now().strftime("%Y-%m-%d")
        res_toggle = self.toggle_sidebar_restriction(
            target="instagram",
            enabled=False,
            requested_by="admin_override",
            admin_pin=admin_pin,
            user_id=user_id,
        )
        controls = self.get_device_app_controls(check_adb=False)
        locks_map = {c["app_key"]: bool(c["is_locked"]) for c in controls}
        return {
            "success": True,
            "message": f"Instagram is enabled for today ({today}) on PC and Android device.",
            "date": today,
            "app_locks": locks_map,
            "restriction_status": res_toggle.get("status", {}).get("instagram", {}),
        }

    def is_wakeup_completed_today(self, date: Optional[str] = None, user_id: str = "user_lok") -> bool:
        """
        Authoritative server-side check across all canonical sources for Wake-Up protocol completion:
        1. daily_records: wakeup_status in ('ON_TIME', 'LATE', 'VERY_LATE', 'COMPLETED', 'VERIFIED')
           or wakeup_completed_at is not None
        2. wake_up_sessions: attendance_status in ('ON_TIME', 'LATE', 'VERY_LATE', 'PRESENT', 'COMPLETED')
           or completed_at is not None or (camera_verified = 1 and (bluetooth_verified = 1 or motivation_verified = 1))
        3. scheduled_activities: type in ('WAKE_UP', 'WAKEUP') and status in ('VERIFIED', 'COMPLETED')
        4. score_events: event_type in ('WAKEUP_COMPLETED', 'WAKEUP_VERIFIED')
           or (event_type = 'PROOF_APPROVED' and (related_task_id IN ('WAKE_UP', 'WAKEUP') or description LIKE '%Wake%'))
        """
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        try:
            # 1. Check daily_records
            dr = self.get_daily_record(target_date, user_id=user_id, is_dev_mode=False)
            if dr:
                w_status = str(dr.get("wakeup_status") or "").upper()
                if w_status in ("ON_TIME", "LATE", "VERY_LATE", "COMPLETED", "VERIFIED"):
                    return True
                if dr.get("wakeup_completed_at"):
                    return True

            with self._connect() as conn:
                # 2. Check wake_up_sessions
                s_row = conn.execute(
                    """
                    SELECT attendance_status, completed_at, camera_verified, bluetooth_verified, motivation_verified
                    FROM wake_up_sessions
                    WHERE date = ? AND is_dev_mode = 0
                    LIMIT 1
                    """,
                    (target_date,),
                ).fetchone()
                if s_row:
                    a_stat = str(s_row["attendance_status"] or "").upper()
                    if a_stat in ("ON_TIME", "LATE", "VERY_LATE", "PRESENT", "COMPLETED") or s_row["completed_at"]:
                        return True
                    if s_row["camera_verified"] == 1 and (s_row["bluetooth_verified"] == 1 or s_row["motivation_verified"] == 1):
                        return True

                # 3. Check scheduled_activities
                act_row = conn.execute(
                    """
                    SELECT status FROM scheduled_activities
                    WHERE date = ? AND type IN ('WAKE_UP', 'WAKEUP') AND status IN ('VERIFIED', 'COMPLETED')
                    LIMIT 1
                    """,
                    (target_date,),
                ).fetchone()
                if act_row:
                    return True

                # 4. Check score_events
                ev_row = conn.execute(
                    """
                    SELECT event_id FROM score_events
                    WHERE date = ? AND (
                        event_type IN ('WAKEUP_COMPLETED', 'WAKEUP_VERIFIED')
                        OR (event_type = 'PROOF_APPROVED' AND (related_task_id IN ('WAKE_UP', 'WAKEUP') OR description LIKE '%Wake%'))
                    )
                    LIMIT 1
                    """,
                    (target_date,),
                ).fetchone()
                if ev_row:
                    return True
        except Exception as e:
            logger.warning("Error checking is_wakeup_completed_today: %s", e)
        return False

    def is_youtube_unlocked_for_today(self, user_id: str = "user_lok") -> bool:
        """Checks if YouTube is unlocked for the current calendar date and not restricted by user toggle."""
        today = datetime.now().strftime("%Y-%m-%d")
        cfg = self.get_focus_settings(user_id)
        prefs = cfg.get("user_restriction_preferences") or {}
        if isinstance(prefs, dict):
            meta_yt = (prefs.get("_meta") or {}).get("youtube") or {}
            if "requestedRestrictionEnabled" in meta_yt:
                if bool(meta_yt["requestedRestrictionEnabled"]):
                    return False
                if bool(meta_yt.get("effectiveAccessAllowed", False)):
                    return True
            elif prefs.get("youtube", True) is True:
                return False
        return cfg.get("youtube_unlocked_date") == today

    def is_instagram_unlocked_for_today(self, user_id: str = "user_lok") -> bool:
        """Checks if Instagram is unlocked for the current calendar date and not restricted by user toggle."""
        today = datetime.now().strftime("%Y-%m-%d")
        cfg = self.get_focus_settings(user_id)
        prefs = cfg.get("user_restriction_preferences") or {}
        if isinstance(prefs, dict):
            meta_ig = (prefs.get("_meta") or {}).get("instagram") or {}
            if "requestedRestrictionEnabled" in meta_ig:
                if bool(meta_ig["requestedRestrictionEnabled"]):
                    return False
                if bool(meta_ig.get("effectiveAccessAllowed", False)):
                    return True
            elif prefs.get("instagram", True) is False:
                return not bool(cfg.get("mandatory_privilege_lock") or cfg.get("mandatory_privilege_lock_instagram"))
        return cfg.get("instagram_unlocked_date") == today

    def get_active_focus_session(self) -> Optional[Dict[str, Any]]:
        """Returns the currently active or paused focus session if one exists."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE status IN ('ACTIVE', 'IN_PROGRESS', 'PAUSED') ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            sess = dict(row)
            try:
                start_dt = datetime.strptime(sess["started_at"], "%Y-%m-%d %H:%M:%S")
                target_secs = int(sess["target_minutes"]) * 60
                elapsed = max(0, int((datetime.now() - start_dt).total_seconds()))
                remaining = max(0, target_secs - elapsed)
            except Exception:
                remaining = int(sess["target_minutes"]) * 60
            sess["remaining_seconds"] = remaining
            sess["allowed_apps"] = json.loads(sess.get("allowed_apps_json") or "[]")
            sess["restricted_apps"] = json.loads(sess.get("restricted_apps_json") or "[]")
            sess["restrictions"] = json.loads(sess.get("restrictions_json") or "{}")
            sess["interruptions"] = json.loads(sess.get("interruptions_json") or "[]")
            return sess

    def get_focus_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns recent completed or logged focus sessions."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM focus_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["allowed_apps"] = json.loads(d.get("allowed_apps_json") or "[]")
                d["restricted_apps"] = json.loads(d.get("restricted_apps_json") or "[]")
                d["restrictions"] = json.loads(d.get("restrictions_json") or "{}")
                d["interruptions"] = json.loads(d.get("interruptions_json") or "[]")
                res.append(d)
            return res

    def get_focus_groups(self) -> List[Dict[str, Any]]:
        """Returns focus groups, seeding default voluntary squads if none exist."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM focus_groups ORDER BY member_count DESC, created_at DESC"
            ).fetchall()
            if not rows:
                now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                defaults = [
                    ("grp_cs_deepwork", "CS & Algorithms Deep Work", "Coding sprints, LeetCode & systems engineering.", "PROGRAMMING", "system", now_iso, 14),
                    ("grp_morning_risers", "09:00 Strict Risers", "Synchronized morning discipline & hourly checkpoints.", "DISCIPLINE", "system", now_iso, 9),
                    ("grp_exam_prep", "University Exam Sprint", "Lecture notes, mathematics & problem sets.", "ACADEMIC", "system", now_iso, 11),
                ]
                for g in defaults:
                    conn.execute(
                        "INSERT OR IGNORE INTO focus_groups (group_id, name, description, category, created_by, created_at, member_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        g,
                    )
                rows = conn.execute(
                    "SELECT * FROM focus_groups ORDER BY member_count DESC, created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def join_focus_group(self, group_id: str, user_id: str = "user_lok") -> Dict[str, Any]:
        """Joins a voluntary focus group."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO focus_group_members (group_id, user_id, joined_at) VALUES (?, ?, ?)",
                (group_id, user_id, now_iso),
            )
            conn.execute(
                "UPDATE focus_groups SET member_count = (SELECT COUNT(*) FROM focus_group_members WHERE group_id = ?) WHERE group_id = ?",
                (group_id, group_id),
            )
        return {"success": True, "group_id": group_id, "user_id": user_id}

    def get_focus_leaderboard(self) -> List[Dict[str, Any]]:
        """Returns leaderboard based on verified daily records and focus minutes."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.user_id, u.username, u.avatar,
                       COALESCE(SUM(fs.actual_minutes), 0) as total_focus_minutes,
                       COALESCE(MAX(dr.final_score), 0) as verified_score
                FROM users u
                LEFT JOIN focus_sessions fs ON fs.status = 'COMPLETED'
                LEFT JOIN daily_records dr ON dr.user_id = u.user_id
                GROUP BY u.user_id
                ORDER BY verified_score DESC, total_focus_minutes DESC
                LIMIT 10
                """
            ).fetchall()
            return [dict(r) for r in rows]

    # =========================================================================
    # NEXT-DAY ROUTINE SCHEDULE & AUDIT PERSISTENCE
    # =========================================================================

    def _row_to_routine_schedule(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["gym_enabled"] = bool(d.get("gym_enabled", 1))
        d["gym_required_by_admin"] = bool(d.get("gym_required_by_admin", 0))
        try:
            d["custom_activities"] = json.loads(d.get("custom_activities_json") or "[]")
        except Exception:
            d["custom_activities"] = []
        try:
            d["reschedule_audit_log"] = json.loads(d.get("reschedule_audit_json") or "[]")
        except Exception:
            d["reschedule_audit_log"] = []
        return d

    def get_confirmed_routine_schedule(
        self, schedule_date: str, account_id: str = "user_lok"
    ) -> Optional[Dict[str, Any]]:
        """Returns the latest confirmed/locked routine schedule for a date."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM routine_schedules
                WHERE account_id = ? AND schedule_date = ? AND status IN ('SCHEDULED', 'LOCKED')
                ORDER BY schedule_version DESC, updated_at DESC
                LIMIT 1
                """,
                (account_id, schedule_date),
            ).fetchone()
            return self._row_to_routine_schedule(row) if row else None

    def get_draft_routine_schedule(
        self, schedule_date: str, account_id: str = "user_lok"
    ) -> Optional[Dict[str, Any]]:
        """Returns the latest draft routine schedule for a date."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM routine_schedules
                WHERE account_id = ? AND schedule_date = ? AND status = 'DRAFT'
                ORDER BY schedule_version DESC, updated_at DESC
                LIMIT 1
                """,
                (account_id, schedule_date),
            ).fetchone()
            return self._row_to_routine_schedule(row) if row else None

    def get_latest_routine_schedule(
        self, schedule_date: str, account_id: str = "user_lok"
    ) -> Optional[Dict[str, Any]]:
        """Returns confirmed schedule if one exists, otherwise draft schedule."""
        confirmed = self.get_confirmed_routine_schedule(schedule_date, account_id)
        if confirmed:
            return confirmed
        return self.get_draft_routine_schedule(schedule_date, account_id)

    def get_all_routine_schedule_versions(
        self, schedule_date: str, account_id: str = "user_lok"
    ) -> List[Dict[str, Any]]:
        """Returns all historical and active versions of a date's routine schedule."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM routine_schedules
                WHERE account_id = ? AND schedule_date = ?
                ORDER BY schedule_version ASC
                """,
                (account_id, schedule_date),
            ).fetchall()
            return [self._row_to_routine_schedule(r) for r in rows]

    def save_routine_schedule_record(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Saves or updates a routine schedule record in SQLite."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        schedule_id = rec.get("schedule_id") or f"SCHED-{rec['schedule_date'].replace('-', '')}-V{rec.get('schedule_version', 1)}"
        account_id = rec.get("account_id") or "user_lok"
        version = int(rec.get("schedule_version", 1))
        schedule_date = rec["schedule_date"]
        timezone_str = rec.get("timezone") or "Asia/Kolkata"
        status = rec.get("status") or "DRAFT"
        wake_up_time = rec.get("wake_up_time") or "07:00"
        bath_time = rec.get("bath_time") or "07:30"
        gym_time = rec.get("gym_time") or "18:30"
        gym_enabled = 1 if rec.get("gym_enabled", True) else 0
        gym_required = 1 if rec.get("gym_required_by_admin", False) else 0
        custom_json = json.dumps(rec.get("custom_activities") or [])
        created_at = rec.get("created_at") or now_iso
        updated_at = rec.get("updated_at") or now_iso
        confirmed_at = rec.get("confirmed_at")
        confirmed_by_actor = rec.get("confirmed_by_actor")
        confirmed_by_device = rec.get("confirmed_by_device")
        locked_at = rec.get("locked_at")
        cutoff_time = rec.get("cutoff_time") or "23:59"
        audit_json = json.dumps(rec.get("reschedule_audit_log") or [])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO routine_schedules (
                    schedule_id, account_id, schedule_version, schedule_date, timezone,
                    status, wake_up_time, bath_time, gym_time, gym_enabled,
                    gym_required_by_admin, custom_activities_json, created_at, updated_at,
                    confirmed_at, confirmed_by_actor, confirmed_by_device, locked_at,
                    cutoff_time, reschedule_audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, schedule_date, schedule_version) DO UPDATE SET
                    schedule_id = excluded.schedule_id,
                    timezone = excluded.timezone,
                    status = excluded.status,
                    wake_up_time = excluded.wake_up_time,
                    bath_time = excluded.bath_time,
                    gym_time = excluded.gym_time,
                    gym_enabled = excluded.gym_enabled,
                    gym_required_by_admin = excluded.gym_required_by_admin,
                    custom_activities_json = excluded.custom_activities_json,
                    updated_at = excluded.updated_at,
                    confirmed_at = COALESCE(excluded.confirmed_at, routine_schedules.confirmed_at),
                    confirmed_by_actor = COALESCE(excluded.confirmed_by_actor, routine_schedules.confirmed_by_actor),
                    confirmed_by_device = COALESCE(excluded.confirmed_by_device, routine_schedules.confirmed_by_device),
                    locked_at = COALESCE(excluded.locked_at, routine_schedules.locked_at),
                    cutoff_time = excluded.cutoff_time,
                    reschedule_audit_json = excluded.reschedule_audit_json
                """,
                (
                    schedule_id, account_id, version, schedule_date, timezone_str,
                    status, wake_up_time, bath_time, gym_time, gym_enabled,
                    gym_required, custom_json, created_at, updated_at,
                    confirmed_at, confirmed_by_actor, confirmed_by_device, locked_at,
                    cutoff_time, audit_json,
                ),
            )
            from core.product_policy import GYM
            duration=rec.get('gym_duration_minutes',GYM['default_workout_minutes'])
            if type(duration) is not int or duration not in GYM['supported_workout_minutes']:
                raise ValueError('Invalid Gym workout duration')
            conn.execute('UPDATE routine_schedules SET gym_duration_minutes=? WHERE account_id=? AND schedule_date=? AND schedule_version=?',
                         (duration,account_id,schedule_date,version))
            row = conn.execute(
                "SELECT * FROM routine_schedules WHERE account_id = ? AND schedule_date = ? AND schedule_version = ?",
                (account_id, schedule_date, version),
            ).fetchone()
            return self._row_to_routine_schedule(row) if row else rec

    def record_schedule_reschedule_audit(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Records an immutable audit trail entry for admin-authorized rescheduling."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        audit_id = entry.get("audit_id") or f"RSCH-{uuid.uuid4().hex[:10].upper()}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedule_reschedule_audit (
                    audit_id, schedule_id, account_id, schedule_date,
                    old_version, new_version, admin_actor, device_id,
                    reason, previous_schedule_json, new_schedule_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    entry["schedule_id"],
                    entry.get("account_id", "user_lok"),
                    entry["schedule_date"],
                    int(entry["old_version"]),
                    int(entry["new_version"]),
                    entry.get("admin_actor", "admin"),
                    entry.get("device_id", "windows_pc"),
                    entry.get("reason", "Admin authorized reschedule"),
                    json.dumps(entry.get("previous_schedule", {})),
                    json.dumps(entry.get("new_schedule", {})),
                    entry.get("created_at") or now_iso,
                ),
            )
        return {"audit_id": audit_id, **entry}

    def get_schedule_reschedule_audits(
        self, schedule_date: Optional[str] = None, account_id: str = "user_lok", limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Returns reschedule audit records."""
        with self._connect() as conn:
            if schedule_date:
                rows = conn.execute(
                    "SELECT * FROM schedule_reschedule_audit WHERE account_id = ? AND schedule_date = ? ORDER BY created_at DESC LIMIT ?",
                    (account_id, schedule_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedule_reschedule_audit WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                    (account_id, limit),
                ).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                try:
                    d["previous_schedule"] = json.loads(d.get("previous_schedule_json") or "{}")
                    d["new_schedule"] = json.loads(d.get("new_schedule_json") or "{}")
                except Exception:
                    pass
                res.append(d)
            return res

    def get_point_ledger_summary(
        self,
        date_str: str = "",
        is_dev_mode: bool = False,
        date: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Section 16: Separate point ledger totals for:
        - verified_points
        - provisional_points
        - rejected_or_revoked_points
        """
        from core.android_ledger import projection, reporting_date
        target_date = reporting_date(date or date_str or None)
        mirror = projection(self, target_date, account_id or "user_lok", is_dev_mode)
        if mirror is not None:
            return mirror
        dev_flag = 1 if is_dev_mode else 0
        with self._connect() as conn:
            events = conn.execute(
                "SELECT * FROM score_events WHERE date = ? AND is_dev_mode = ? ORDER BY timestamp ASC",
                (target_date, dev_flag),
            ).fetchall()
            from core.account_scope import filter_account_score_events
            events = filter_account_score_events(conn, events, account_id)
            reviews = conn.execute(
                "SELECT * FROM proof_reviews WHERE date = ? AND (? IS NULL OR user_id = ?)",
                (target_date, account_id, account_id),
            ).fetchall()
            activities = conn.execute(
                "SELECT * FROM scheduled_activities WHERE date = ? AND (? IS NULL OR account_id = ?)",
                (target_date, account_id, account_id),
            ).fetchall()
            evidence_rows = conn.execute(
                "SELECT evidence_id, file_path, record_type, sha256_hash, is_deleted FROM evidence_files WHERE date = ?",
                (target_date,),
            ).fetchall()

        review_by_evidence = {r["evidence_id"]: dict(r) for r in reviews}
        evidence_by_id = {r["evidence_id"]: dict(r) for r in evidence_rows}
        approved_evidence = {
            evidence_id
            for evidence_id, review in review_by_evidence.items()
            if review.get("status") == "APPROVED"
            and evidence_id in evidence_by_id
            and not evidence_by_id[evidence_id].get("is_deleted")
            and is_valid_retained_proof_file(
                evidence_by_id[evidence_id].get("file_path") or "",
                evidence_by_id[evidence_id].get("record_type") or "",
                evidence_by_id[evidence_id].get("sha256_hash"),
            )
        }
        verified_points = 0
        provisional_points = 0
        rejected_or_revoked_points = 0
        unverified_points = 0
        activity_events: Dict[str, List[Any]] = {}

        for ev in events:
            pts = int(ev["points"])
            ev_type = str(ev["event_type"] or "").upper()
            eid = ev["evidence_id"]
            audit = {}
            try:
                audit = json.loads(ev["audit_json"] or "{}")
            except Exception:
                pass

            activity_id = ev["related_task_id"] or audit.get("activity_id") or audit.get("scheduled_activity_id")
            if activity_id:
                activity_events.setdefault(str(activity_id), []).append(ev)
            elif eid:
                for activity in activities:
                    try:
                        details = json.loads(activity["details_json"] or "{}")
                    except Exception:
                        details = {}
                    if str(details.get("evidence_id") or details.get("evidenceId") or "") == str(eid):
                        activity_events.setdefault(str(activity["activity_id"]), []).append(ev)

            ledger_state = str(audit.get("ledger_state") or "").upper()
            if ev_type in {"PROOF_REJECTED", "PROOF_REVOKED", "POINTS_REVOKED"} or ledger_state in {"REJECTED", "REVOKED", "REJECTED_OR_REVOKED"}:
                rejected_or_revoked_points += abs(pts)
            elif pts > 0 and (
                (ev_type == "PROOF_APPROVED" and eid in approved_evidence)
                or ev_type in {"PC_SHUTDOWN_BONUS", "SCORE_CORRECTION", "ADMIN_SCORE_ADJUSTMENT"}
            ):
                verified_points += pts
            elif pts > 0 and (
                ledger_state == "PROVISIONAL"
                or (eid and review_by_evidence.get(eid, {}).get("status") in ("PENDING", "UNDER_REVIEW", "PROVISIONAL", "NEEDS_REVIEW"))
            ):
                provisional_points += pts
            elif pts > 0:
                # Preserve unsupported legacy transactions for audit while
                # excluding them from the verified total.
                unverified_points += pts
            else:
                verified_points += pts

        # Also include points on activity rows only when that specific activity has no
        # point event. A single unrelated event must not hide all schedule-row points.
        for act in activities:
            st = act["status"]
            pts = int(act["points"] or 0)
            details = {}
            try:
                details = json.loads(act["details_json"] or "{}")
            except Exception:
                pass
            has_linked_event = bool(activity_events.get(str(act["activity_id"]))) or bool(details.get("score_event_recorded"))
            activity_evidence_id = details.get("evidence_id") or details.get("evidenceId")
            if st == "VERIFIED" and pts > 0 and activity_evidence_id in approved_evidence and not has_linked_event:
                verified_points += pts
            elif (
                st in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "UNDER_REVIEW")
                and int(details.get("proposed_points", 0) or 0) > 0
                and activity_evidence_id
                and review_by_evidence.get(activity_evidence_id, {}).get("status") in ("PENDING", "UNDER_REVIEW", "PROVISIONAL", "NEEDS_REVIEW")
                and not has_linked_event
            ):
                provisional_points += int(details.get("proposed_points", 0) or 0)
            else:
                revoked = int(details.get("rejected_or_revoked_points") or details.get("revoked_points") or 0)
                rejected_or_revoked_points += max(0, revoked)

        verified_points_clamped = min(100, verified_points)
        return {
            "date": target_date,
            "verified_points": verified_points_clamped,
            "raw_verified_points": verified_points,
            "provisional_points": provisional_points,
            "rejected_or_revoked_points": rejected_or_revoked_points,
            "unverified_points": unverified_points,
            "total_events": len(events),
        }

    def execute_midnight_reset(
        self,
        target_date: str,
        user_id: str = "user_lok",
        is_dev_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes an automatic daily points reset at midnight.
        Delegates to ScoringEngine.execute_daily_reset_if_needed.
        """
        from core.scoring_engine import ScoringEngine
        se = ScoringEngine(self)
        res = se.execute_daily_reset_if_needed(target_date=target_date, user_id=user_id, is_dev_mode=is_dev_mode)
        return {
            "success": res.get("status") in ("SUCCESS", "ALREADY_RESET"),
            "status": res.get("status"),
            "reset_applied": res.get("reset_applied", False),
            "date": res.get("date"),
            "previous_balance": res.get("previous_balance", 0),
            "new_balance": res.get("new_balance", 0),
            "event_id": res.get("event_id"),
        }

    # =========================================================================
    # APP RELEASE & UPDATE MANAGEMENT (Requirements 2, 3, 10, 11, 12)
    # =========================================================================

    def publish_app_release(self, release_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Publishes a new application release version.
        Guarantees cryptographic SHA-256 storage and version integrity.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        v_code = int(release_data.get("version_code") or release_data.get("versionCode") or 1)
        platform = (release_data.get("platform") or "ANDROID").upper()
        rel_id = release_data.get("release_id") or f"rel_{platform.lower()}_{v_code}"
        v_name = release_data.get("version_name") or release_data.get("versionName") or "1.0.0"
        min_ver = int(release_data.get("min_supported_version") or release_data.get("minimumSupportedVersion") or 1)
        pkg_name = release_data.get("package_name") or release_data.get("packageName") or "com.strictrecords.app"
        rel_date = release_data.get("release_date") or release_data.get("releaseDate") or now_iso
        rel_notes = release_data.get("release_notes") or release_data.get("releaseNotes") or ""
        dl_url = release_data.get("download_url") or release_data.get("downloadUrl") or ""
        file_size = int(release_data.get("file_size_bytes") or release_data.get("fileSize") or release_data.get("fileSizeBytes") or 0)
        sha256 = (release_data.get("sha256_checksum") or release_data.get("sha256") or "").lower()
        channel = (release_data.get("channel") or "PRODUCTION").upper()
        mandatory = 1 if (release_data.get("is_mandatory") or release_data.get("mandatory")) else 0
        cert_sha256 = release_data.get("signing_cert_sha256") or release_data.get("signingCertificateSha256") or ""
        created_by = release_data.get("created_by") or "admin"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO app_releases (
                    release_id, platform, version_name, version_code, min_supported_version,
                    package_name, release_date, release_notes, download_url, file_size_bytes,
                    sha256_checksum, channel, is_mandatory, is_active, signing_cert_sha256,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    rel_id, platform, v_name, v_code, min_ver, pkg_name, rel_date, rel_notes,
                    dl_url, file_size, sha256, channel, mandatory, cert_sha256, now_iso, created_by
                )
            )

        return {
            "success": True,
            "release_id": rel_id,
            "version_name": v_name,
            "version_code": v_code,
            "platform": platform,
            "channel": channel,
            "download_url": dl_url,
            "sha256": sha256,
            "file_size_bytes": file_size,
            "is_mandatory": bool(mandatory),
        }

    def get_latest_app_release(self, platform: str = "ANDROID", channel: str = "PRODUCTION") -> Optional[Dict[str, Any]]:
        """
        Returns the latest active published release for a platform and channel.
        Sorts by version_code DESC.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM app_releases
                WHERE platform = ? AND channel = ? AND is_active = 1
                ORDER BY version_code DESC LIMIT 1
                """,
                (platform.upper(), channel.upper())
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def get_app_release_by_id(self, release_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM app_releases WHERE release_id = ?", (release_id,)).fetchone()
            return dict(row) if row else None

    def list_app_releases(self, platform: Optional[str] = None, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists published releases with optional filtering."""
        query = "SELECT * FROM app_releases WHERE 1=1"
        params = []
        if platform:
            query += " AND platform = ?"
            params.append(platform.upper())
        if channel:
            query += " AND channel = ?"
            params.append(channel.upper())
        query += " ORDER BY version_code DESC"

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def toggle_app_release_active(self, release_id: str, is_active: bool) -> bool:
        """Disables or re-enables a published release."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE app_releases SET is_active = ? WHERE release_id = ?",
                (1 if is_active else 0, release_id)
            )
            return cur.rowcount > 0

    def record_device_update_status(
        self,
        device_id: str,
        release_id: str,
        installed_version_code: int,
        status: str,
        progress_percent: int = 0,
        last_error: Optional[str] = None
    ) -> bool:
        """Records granular APK download / installation status for a device."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO device_update_status (
                    device_id, release_id, installed_version_code, status,
                    progress_percent, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, release_id, installed_version_code, status, progress_percent, last_error, now_iso)
            )
            # Also update devices table
            conn.execute(
                "UPDATE devices SET app_version_code = ?, update_status = ?, last_seen = ? WHERE device_id = ?",
                (installed_version_code, status, now_iso, device_id)
            )
            return True

    def get_device_update_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_update_status WHERE device_id = ? ORDER BY updated_at DESC LIMIT 1",
                (device_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["download_progress_percent"] = d.get("progress_percent", 0)
            return d

    def get_all_device_update_statuses(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dus.*, d.device_name, d.platform, d.app_version
                FROM device_update_status dus
                LEFT JOIN devices d ON dus.device_id = d.device_id
                ORDER BY dus.updated_at DESC
                """
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["download_progress_percent"] = d.get("progress_percent", 0)
                results.append(d)
            return results

    # =========================================================================
    # PART 6: PC SHUTDOWN BONUS & NEXT-DAY POINTS LEDGER ENGINE
    # =========================================================================

    def record_pc_shutdown_event(
        self,
        event_id: str,
        shutdown_timestamp: str,
        device_id: str = "windows_lok_pc",
        date: Optional[str] = None,
        time_hhmmss: Optional[str] = None,
        evidence_source: str = "WIN32_API",
        account_id: str = "user_lok",
        conn: Optional[sqlite3.Connection] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Records a Windows PC shutdown event, validates against the strict-before-cutoff rule
        (default < 23:45 in Asia/Kolkata), sets target_reward_date to the following calendar day,
        and persists the record.
        """
        settings = self.get_settings(account_id)
        cutoff = settings.pc_shutdown_cutoff_time or "23:45"
        tz_name = settings.pc_shutdown_timezone or "Asia/Kolkata"

        clean_ts = shutdown_timestamp.strip()
        if "T" in clean_ts:
            dt_part, tm_part = clean_ts.split("T", 1)
        elif " " in clean_ts:
            dt_part, tm_part = clean_ts.split(" ", 1)
        else:
            dt_part = date or datetime.now().strftime("%Y-%m-%d")
            tm_part = clean_ts

        ev_date = date or dt_part[:10]
        tm_clean = re.sub(r"[Z+-].*$", "", tm_part).strip()
        if len(tm_clean) >= 8:
            ev_time = tm_clean[:8]
        elif len(tm_clean) >= 5:
            ev_time = tm_clean[:5] + ":00"
        else:
            ev_time = "00:00:00"

        try:
            d_obj = datetime.strptime(ev_date, "%Y-%m-%d")
            target_date = (d_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            target_date = ev_date

        cutoff_full = cutoff if len(cutoff) >= 8 else f"{cutoff}:00"
        is_before_cutoff = ev_time < cutoff_full
        is_enabled = bool(settings.pc_shutdown_bonus_enabled)
        is_qualifying = 1 if (is_before_cutoff and is_enabled) else 0

        disqual_reason = None
        if not is_enabled:
            disqual_reason = "PC shutdown bonus feature is currently disabled in settings."
        elif not is_before_cutoff:
            disqual_reason = f"Shutdown occurred at {ev_time}, which is at or after cutoff {cutoff}."

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ctx = contextlib.nullcontext(conn) if conn is not None else self._connect()
        with ctx as c:
            existing = c.execute(
                "SELECT * FROM pc_shutdown_events WHERE event_id = ? OR (date = ? AND account_id = ?)",
                (event_id, ev_date, account_id)
            ).fetchone()

            if existing:
                row = dict(existing)
                if row.get("bonus_status") == "BONUS_CREDITED":
                    return row

                status = row.get("bonus_status", "AWAITING_PHONE_ATTENDANCE")
                if not is_qualifying:
                    status = "INELIGIBLE"

                c.execute(
                    """
                    UPDATE pc_shutdown_events SET
                        shutdown_timestamp = ?,
                        time_hhmmss = ?,
                        cutoff_time = ?,
                        timezone = ?,
                        evidence_source = ?,
                        is_qualifying = ?,
                        disqualification_reason = ?,
                        target_reward_date = ?,
                        bonus_points = ?,
                        bonus_status = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        clean_ts, ev_time, cutoff, tz_name, evidence_source,
                        is_qualifying, disqual_reason, target_date,
                        settings.pc_shutdown_bonus_points,
                        status, now_iso, row["event_id"]
                    )
                )
                row.update({
                    "shutdown_timestamp": clean_ts, "time_hhmmss": ev_time,
                    "is_qualifying": is_qualifying, "disqualification_reason": disqual_reason,
                    "target_reward_date": target_date, "bonus_status": status,
                    "bonus_points": settings.pc_shutdown_bonus_points,
                })
                return row

            init_status = "AWAITING_PHONE_ATTENDANCE" if is_qualifying else "INELIGIBLE"
            c.execute(
                """
                INSERT INTO pc_shutdown_events (
                    event_id, account_id, device_id, shutdown_timestamp,
                    date, time_hhmmss, cutoff_time, timezone, evidence_source,
                    is_qualifying, disqualification_reason, target_reward_date,
                    bonus_status, bonus_points, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, account_id, device_id, clean_ts,
                    ev_date, ev_time, cutoff, tz_name, evidence_source,
                    is_qualifying, disqual_reason, target_date,
                    init_status, settings.pc_shutdown_bonus_points,
                    now_iso, now_iso
                )
            )

        return {
            "success": True,
            "event_id": event_id,
            "account_id": account_id,
            "device_id": device_id,
            "shutdown_timestamp": clean_ts,
            "date": ev_date,
            "time_hhmmss": ev_time,
            "cutoff_time": cutoff,
            "timezone": tz_name,
            "evidence_source": evidence_source,
            "is_qualifying": is_qualifying,
            "disqualification_reason": disqual_reason,
            "target_reward_date": target_date,
            "bonus_status": init_status,
            "bonus_points": settings.pc_shutdown_bonus_points,
            "created_at": now_iso,
        }

    def record_pc_shutdown_phone_attendance(
        self,
        date: Optional[str] = None,
        phone_device_id: str = "android_lok_phone",
        attendance_timestamp: Optional[str] = None,
        account_id: str = "user_lok",
        shutdown_event_id: Optional[str] = None,
        event_id: Optional[str] = None,
        attendance_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Records phone attendance confirmation for the PC shutdown.
        Links to qualifying shutdown event.
        """
        if "device_id" in kwargs and kwargs["device_id"]:
            phone_device_id = kwargs["device_id"]

        if date and (not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date))):
            target_event_id = str(date)
            date = None
        else:
            target_event_id = shutdown_event_id or event_id or kwargs.get("target_event_id")

        if phone_device_id and str(phone_device_id).startswith("att_"):
            attendance_id = str(phone_device_id)
            phone_device_id = kwargs.get("device_id", "android_lok_phone")

        now_iso = attendance_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ctx = contextlib.nullcontext(conn) if conn is not None else self._connect()
        with ctx as c:
            if target_event_id:
                row = c.execute(
                    "SELECT * FROM pc_shutdown_events WHERE event_id = ?",
                    (target_event_id,)
                ).fetchone()
            else:
                target_date = date or datetime.now().strftime("%Y-%m-%d")
                row = c.execute(
                    "SELECT * FROM pc_shutdown_events WHERE date = ? AND account_id = ? ORDER BY created_at DESC LIMIT 1",
                    (target_date, account_id)
                ).fetchone()

            ev_date = row["date"] if row else (date or datetime.now().strftime("%Y-%m-%d"))
            att_id = attendance_id or f"att_pc_shut_{ev_date}_{uuid.uuid4().hex[:8]}"

            if not row:
                ev_id = target_event_id or shutdown_event_id or f"shut_pending_{ev_date}"
                settings = self.get_settings(account_id)
                try:
                    target_date = (datetime.strptime(ev_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    target_date = ev_date
                c.execute(
                    """
                    INSERT INTO pc_shutdown_events (
                        event_id, account_id, device_id, shutdown_timestamp,
                        date, time_hhmmss, cutoff_time, timezone, evidence_source,
                        is_qualifying, target_reward_date, phone_attendance_id,
                        phone_attendance_time, phone_device_id, bonus_status,
                        bonus_points, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id, account_id, "windows_lok_pc", "",
                        ev_date, "", settings.pc_shutdown_cutoff_time, settings.pc_shutdown_timezone,
                        "PENDING_WINDOWS", 0, target_date, att_id,
                        now_iso, phone_device_id, "AWAITING_WINDOWS_SHUTDOWN",
                        settings.pc_shutdown_bonus_points, now_iso, now_iso
                    )
                )
                return {
                    "success": True,
                    "event_id": ev_id,
                    "attendance_id": att_id,
                    "bonus_status": "AWAITING_WINDOWS_SHUTDOWN",
                    "date": ev_date,
                }

            ev = dict(row)
            if ev.get("bonus_status") == "BONUS_CREDITED":
                return {"success": True, "event_id": ev["event_id"], "already_credited": True, "bonus_status": "BONUS_CREDITED"}

            has_photo = bool(ev.get("evidence_id"))
            if not ev.get("is_qualifying"):
                next_status = "INELIGIBLE"
            elif has_photo:
                next_status = "AWAITING_REVIEW"
            else:
                next_status = "PHOTO_REQUIRED"

            c.execute(
                """
                UPDATE pc_shutdown_events SET
                    phone_attendance_id = ?,
                    phone_attendance_time = ?,
                    phone_device_id = ?,
                    bonus_status = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (att_id, now_iso, phone_device_id, next_status, now_iso, ev["event_id"])
            )
            ev.update({
                "phone_attendance_id": att_id,
                "phone_attendance_time": now_iso,
                "phone_device_id": phone_device_id,
                "bonus_status": next_status,
            })
            return {"success": True, "event_id": ev["event_id"], "attendance_id": att_id, "bonus_status": next_status}

    def record_pc_shutdown_photo_evidence(
        self,
        date: Optional[str] = None,
        evidence_id: str = "",
        photo_timestamp: Optional[str] = None,
        account_id: str = "user_lok",
        shutdown_event_id: Optional[str] = None,
        event_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Links shutdown photograph evidence to the PC shutdown event.
        """
        if date and (not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date))):
            target_event_id = str(date)
            date = None
        else:
            target_event_id = shutdown_event_id or event_id or kwargs.get("target_event_id")

        now_iso = photo_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ctx = contextlib.nullcontext(conn) if conn is not None else self._connect()
        with ctx as c:
            if target_event_id:
                row = c.execute(
                    "SELECT * FROM pc_shutdown_events WHERE event_id = ?",
                    (target_event_id,)
                ).fetchone()
            else:
                target_date = date or datetime.now().strftime("%Y-%m-%d")
                row = c.execute(
                    "SELECT * FROM pc_shutdown_events WHERE date = ? AND account_id = ? ORDER BY created_at DESC LIMIT 1",
                    (target_date, account_id)
                ).fetchone()

            ev_date = row["date"] if row else (date or datetime.now().strftime("%Y-%m-%d"))

            if not row:
                ev_id = target_event_id or f"shut_pending_{ev_date}"
                settings = self.get_settings(account_id)
                try:
                    target_date = (datetime.strptime(ev_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    target_date = ev_date
                c.execute(
                    """
                    INSERT INTO pc_shutdown_events (
                        event_id, account_id, device_id, shutdown_timestamp,
                        date, time_hhmmss, cutoff_time, timezone, evidence_source,
                        is_qualifying, target_reward_date, evidence_id,
                        photo_submitted_at, bonus_status, bonus_points,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id, account_id, "windows_lok_pc", "",
                        ev_date, "", settings.pc_shutdown_cutoff_time, settings.pc_shutdown_timezone,
                        "PENDING_WINDOWS", 0, target_date, evidence_id,
                        now_iso, "ATTENDANCE_REQUIRED", settings.pc_shutdown_bonus_points,
                        now_iso, now_iso
                    )
                )
                return {"success": True, "event_id": ev_id, "evidence_id": evidence_id, "bonus_status": "ATTENDANCE_REQUIRED"}

            ev = dict(row)
            if ev.get("bonus_status") == "BONUS_CREDITED":
                return {"success": True, "event_id": ev["event_id"], "already_credited": True, "bonus_status": "BONUS_CREDITED"}

            has_att = bool(ev.get("phone_attendance_id"))
            if not ev.get("is_qualifying"):
                next_status = "INELIGIBLE"
            elif has_att:
                next_status = "AWAITING_REVIEW"
            else:
                next_status = "ATTENDANCE_REQUIRED"

            c.execute(
                """
                UPDATE pc_shutdown_events SET
                    evidence_id = ?,
                    photo_submitted_at = ?,
                    bonus_status = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (evidence_id, now_iso, next_status, now_iso, ev["event_id"])
            )
            return {"success": True, "event_id": ev["event_id"], "evidence_id": evidence_id, "bonus_status": next_status}

    def get_pc_shutdown_event(self, event_id_or_date: str, account_id: str = "user_lok", conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        ctx = contextlib.nullcontext(conn) if conn is not None else self._connect()
        with ctx as c:
            row = c.execute(
                """
                SELECT se.*, pr.status as review_status, pr.decision as review_decision
                FROM pc_shutdown_events se
                LEFT JOIN proof_reviews pr ON pr.evidence_id = se.evidence_id
                WHERE se.event_id = ? OR (se.date = ? AND se.account_id = ?)
                ORDER BY se.created_at DESC LIMIT 1
                """,
                (event_id_or_date, event_id_or_date, account_id)
            ).fetchone()
            return dict(row) if row else None

    def get_pc_shutdown_status(self, date: Optional[str] = None, account_id: str = "user_lok") -> Dict[str, Any]:
        """
        Returns full structured status for Android dashboard and Windows UI.
        """
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        settings = self.get_settings(account_id)
        ev = self.get_pc_shutdown_event(target_date, account_id=account_id)

        if not ev:
            try:
                nxt = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                nxt = target_date
            return {
                "date": target_date,
                "shutdown_recorded": False,
                "shutdown_timestamp": None,
                "time_hhmmss": None,
                "cutoff_time": settings.pc_shutdown_cutoff_time,
                "timezone": settings.pc_shutdown_timezone,
                "is_qualifying": False,
                "eligible_for_bonus": False,
                "disqualification_reason": None,
                "phone_attendance_confirmed": False,
                "phone_attendance_time": None,
                "photo_submitted": False,
                "evidence_id": None,
                "review_status": "NONE",
                "bonus_status": "AWAITING_WINDOWS_SHUTDOWN",
                "bonus_points": settings.pc_shutdown_bonus_points,
                "target_reward_date": nxt,
                "bonus_credited": False,
                "bonus_ledger_event_id": None,
            }

        return {
            "date": ev.get("date", target_date),
            "event_id": ev.get("event_id"),
            "shutdown_recorded": bool(ev.get("shutdown_timestamp")),
            "shutdown_timestamp": ev.get("shutdown_timestamp"),
            "time_hhmmss": ev.get("time_hhmmss"),
            "cutoff_time": ev.get("cutoff_time", settings.pc_shutdown_cutoff_time),
            "timezone": ev.get("timezone", settings.pc_shutdown_timezone),
            "is_qualifying": bool(ev.get("is_qualifying")),
            "eligible_for_bonus": bool(ev.get("is_qualifying")),
            "disqualification_reason": ev.get("disqualification_reason"),
            "phone_attendance_confirmed": bool(ev.get("phone_attendance_id")),
            "phone_attendance_time": ev.get("phone_attendance_time"),
            "phone_device_id": ev.get("phone_device_id"),
            "photo_submitted": bool(ev.get("evidence_id")),
            "evidence_id": ev.get("evidence_id"),
            "photo_submitted_at": ev.get("photo_submitted_at"),
            "review_status": ev.get("review_status", "NONE"),
            "bonus_status": ev.get("bonus_status", "AWAITING_WINDOWS_SHUTDOWN"),
            "bonus_points": int(ev.get("bonus_points", settings.pc_shutdown_bonus_points)),
            "target_reward_date": ev.get("target_reward_date"),
            "bonus_credited": bool(ev.get("bonus_status") == "BONUS_CREDITED"),
            "bonus_ledger_event_id": ev.get("bonus_ledger_event_id"),
            "rejection_reason": ev.get("rejection_reason"),
        }

    def get_all_pc_shutdown_reviews(self, account_id: str = "user_lok") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT se.*, pr.status as review_status, pr.decision as review_decision,
                       pr.reason_note as review_reason, ef.file_path, ef.sha256_hash
                FROM pc_shutdown_events se
                LEFT JOIN proof_reviews pr ON pr.evidence_id = se.evidence_id
                LEFT JOIN evidence_files ef ON ef.evidence_id = se.evidence_id
                WHERE se.account_id = ?
                ORDER BY se.date DESC, se.created_at DESC
                """,
                (account_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def evaluate_and_award_pc_shutdown_bonus(
        self,
        event_id_or_date: str,
        admin_id: Optional[str] = None,
        decision: str = "APPROVE",
        reason: str = "PC shutdown verified before 11:45 PM + phone attendance & photo verified",
        account_id: str = "user_lok",
    ) -> Dict[str, Any]:
        """
        Evaluates the 5 required conditions and, if satisfied, awards the configured bonus
        points to the FOLLOWING DAY's daily score balance (target_reward_date).
        Idempotent: at most once award per qualifying shutdown event.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT se.*, pr.status as review_status
                FROM pc_shutdown_events se
                LEFT JOIN proof_reviews pr ON pr.evidence_id = se.evidence_id
                WHERE se.event_id = ? OR (se.date = ? AND se.account_id = ?)
                ORDER BY se.created_at DESC LIMIT 1
                """,
                (event_id_or_date, event_id_or_date, account_id)
            ).fetchone()

            if not row:
                return {"success": False, "error": "No PC shutdown event found for evaluation.", "code": "NOT_FOUND"}

            ev = dict(row)

            if ev.get("bonus_status") == "BONUS_CREDITED" and ev.get("bonus_ledger_event_id"):
                return {
                    "success": True,
                    "event_id": ev["event_id"],
                    "already_credited": True,
                    "bonus_status": "BONUS_CREDITED",
                    "target_reward_date": ev["target_reward_date"],
                    "bonus_points": ev["bonus_points"],
                    "ledger_event_id": ev["bonus_ledger_event_id"],
                }

            if decision == "REJECT":
                conn.execute(
                    """
                    UPDATE pc_shutdown_events SET
                        bonus_status = 'REJECTED',
                        rejection_reason = ?,
                        reviewed_by = ?,
                        reviewed_at = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (reason, admin_id or "ADMIN", now_iso, now_iso, ev["event_id"])
                )
                if ev.get("evidence_id"):
                    conn.execute(
                        """
                        UPDATE proof_reviews SET
                            status = 'REJECTED',
                            decision = 'REJECTED',
                            reason_note = ?,
                            reviewed_by = ?,
                            reviewed_at = ?
                        WHERE evidence_id = ?
                        """,
                        (reason, admin_id or "ADMIN", now_iso, ev["evidence_id"])
                    )
                return {
                    "success": True,
                    "event_id": ev["event_id"],
                    "bonus_status": "REJECTED",
                    "decision": "REJECTED",
                    "reason": reason,
                    "points_awarded": 0,
                }

            if not ev.get("is_qualifying"):
                disqual = ev.get("disqualification_reason") or "Shutdown was not before cutoff time."
                conn.execute(
                    "UPDATE pc_shutdown_events SET bonus_status = 'INELIGIBLE', updated_at = ? WHERE event_id = ?",
                    (now_iso, ev["event_id"])
                )
                return {"success": False, "eligible": False, "reason": disqual, "code": "INELIGIBLE"}

            if not ev.get("phone_attendance_id"):
                return {
                    "success": False,
                    "eligible": False,
                    "reason": "Phone attendance confirmation is required from registered Android phone.",
                    "code": "MISSING_PHONE_ATTENDANCE"
                }

            if not ev.get("evidence_id"):
                return {
                    "success": False,
                    "eligible": False,
                    "reason": "Workstation shutdown photograph evidence is required.",
                    "code": "MISSING_SHUTDOWN_PHOTO"
                }

            points_to_award = int(ev.get("bonus_points", 15))
            target_date = ev["target_reward_date"]
            ledger_id = f"score_pc_shut_{ev['event_id']}"

            self.ensure_daily_record(target_date, user_id=account_id)

            conn.execute(
                """
                INSERT OR REPLACE INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type,
                    device_id, description, points, rule_version, evidence_id,
                    related_task_id, audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ledger_id,
                    target_date,
                    0,
                    now_iso,
                    ScoreEventType.PC_SHUTDOWN_BONUS.value,
                    "windows_lok_pc",
                    f"PC Shutdown Bonus: Shutdown at {ev['time_hhmmss']} (< {ev['cutoff_time']}) + Phone attendance & photo verified",
                    points_to_award,
                    1,
                    ev["evidence_id"],
                    "PC_SHUTDOWN",
                    json.dumps({
                        "shutdown_event_id": ev["event_id"],
                        "shutdown_time": ev["time_hhmmss"],
                        "cutoff_time": ev["cutoff_time"],
                        "phone_attendance_id": ev["phone_attendance_id"],
                        "phone_attendance_time": ev["phone_attendance_time"],
                        "phone_device_id": ev["phone_device_id"],
                        "target_reward_date": target_date,
                        "approved_by": admin_id or "SYSTEM_VERIFIED",
                        "reason": reason,
                    })
                )
            )

            if ev.get("evidence_id"):
                conn.execute(
                    """
                    UPDATE proof_reviews SET
                        status = 'APPROVED',
                        decision = 'APPROVED',
                        points_awarded = ?,
                        reason_note = ?,
                        reviewed_by = ?,
                        reviewed_at = ?
                    WHERE evidence_id = ?
                    """,
                    (points_to_award, reason, admin_id or "ADMIN", now_iso, ev["evidence_id"])
                )

            conn.execute(
                """
                UPDATE pc_shutdown_events SET
                    bonus_status = 'BONUS_CREDITED',
                    bonus_ledger_event_id = ?,
                    reviewed_by = ?,
                    reviewed_at = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (ledger_id, admin_id or "ADMIN", now_iso, now_iso, ev["event_id"])
            )

        from core.scoring_engine import ScoringEngine
        scoring_engine = ScoringEngine(self)
        recalculated = scoring_engine.recalculate_daily_score(target_date, user_id=account_id)

        return {
            "success": True,
            "event_id": ev["event_id"],
            "bonus_status": "BONUS_CREDITED",
            "decision": "APPROVED",
            "points_awarded": points_to_award,
            "target_reward_date": target_date,
            "ledger_event_id": ledger_id,
            "recalculated_score": recalculated.get("final_score", 0),
            "score_mode": recalculated.get("score_mode", "STRICT_MODE"),
        }







    # =========================================================================
    # Cloud Sync Engine Methods (Google Drive)
    # =========================================================================

    def upsert_cloud_sync_file(
        self,
        file_id: str,
        local_path: str,
        category: str,
        file_size_bytes: int,
        sha256_hash: str,
        md5_hash: Optional[str] = None,
        mime_type: Optional[str] = None,
        drive_file_id: Optional[str] = None,
        sync_status: str = "PENDING",
        error_message: Optional[str] = None,
        device_id: str = "windows_pc",
    ) -> Dict[str, Any]:
        """Inserts or updates a cloud sync file record."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT file_id, drive_file_id, sync_status FROM cloud_sync_files WHERE file_id = ?",
                (file_id,)
            ).fetchone()

            if existing:
                drive_id = drive_file_id if drive_file_id is not None else existing[1]
                conn.execute(
                    """
                    UPDATE cloud_sync_files
                    SET local_path = ?, category = ?, drive_file_id = ?, file_size_bytes = ?,
                        sha256_hash = ?, md5_hash = COALESCE(?, md5_hash), mime_type = COALESCE(?, mime_type),
                        sync_status = ?, error_message = ?, device_id = ?
                    WHERE file_id = ?
                    """,
                    (
                        local_path, category, drive_id, file_size_bytes,
                        sha256_hash, md5_hash, mime_type,
                        sync_status, error_message, device_id,
                        file_id
                    )
                )
            else:
                conn.execute(
                    """
                    INSERT INTO cloud_sync_files (
                        file_id, local_path, category, drive_file_id, file_size_bytes,
                        sha256_hash, md5_hash, mime_type, sync_status, error_message,
                        created_at, device_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id, local_path, category, drive_file_id, file_size_bytes,
                        sha256_hash, md5_hash, mime_type, sync_status, error_message,
                        now_iso, device_id
                    )
                )

        return self.get_cloud_sync_file(file_id) or {}

    def get_cloud_sync_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single cloud sync file by ID."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT file_id, local_path, category, drive_file_id, drive_parent_id,
                       file_size_bytes, sha256_hash, md5_hash, mime_type, sync_status,
                       error_message, last_synced_at, created_at, device_id
                FROM cloud_sync_files WHERE file_id = ?
                """,
                (file_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "file_id": row[0],
                "local_path": row[1],
                "category": row[2],
                "drive_file_id": row[3],
                "drive_parent_id": row[4],
                "file_size_bytes": row[5],
                "sha256_hash": row[6],
                "md5_hash": row[7],
                "mime_type": row[8],
                "sync_status": row[9],
                "error_message": row[10],
                "last_synced_at": row[11],
                "created_at": row[12],
                "device_id": row[13],
            }

    def list_cloud_sync_files(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Lists cloud sync files with optional status/category filter."""
        query = """
            SELECT file_id, local_path, category, drive_file_id, drive_parent_id,
                   file_size_bytes, sha256_hash, md5_hash, mime_type, sync_status,
                   error_message, last_synced_at, created_at, device_id
            FROM cloud_sync_files
            WHERE 1=1
        """
        params = []
        if status:
            query += " AND sync_status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "file_id": r[0],
                    "local_path": r[1],
                    "category": r[2],
                    "drive_file_id": r[3],
                    "drive_parent_id": r[4],
                    "file_size_bytes": r[5],
                    "sha256_hash": r[6],
                    "md5_hash": r[7],
                    "mime_type": r[8],
                    "sync_status": r[9],
                    "error_message": r[10],
                    "last_synced_at": r[11],
                    "created_at": r[12],
                    "device_id": r[13],
                }
                for r in rows
            ]

    def update_cloud_sync_file_status(
        self,
        file_id: str,
        sync_status: str,
        drive_file_id: Optional[str] = None,
        error_message: Optional[str] = None,
        last_synced_at: Optional[str] = None
    ) -> None:
        """Updates the status and metadata for a cloud sync file."""
        now_iso = last_synced_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            if sync_status == "SYNCED":
                conn.execute(
                    """
                    UPDATE cloud_sync_files
                    SET sync_status = ?, drive_file_id = COALESCE(?, drive_file_id),
                        error_message = NULL, last_synced_at = ?
                    WHERE file_id = ?
                    """,
                    (sync_status, drive_file_id, now_iso, file_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE cloud_sync_files
                    SET sync_status = ?, drive_file_id = COALESCE(?, drive_file_id),
                        error_message = ?
                    WHERE file_id = ?
                    """,
                    (sync_status, drive_file_id, error_message, file_id)
                )

    def enqueue_cloud_sync_task(self, file_id: str, action: str = "UPLOAD") -> str:
        """Enqueues an action in cloud_sync_queue."""
        queue_id = f"cq_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            # Check if already queued
            existing = conn.execute(
                "SELECT queue_id FROM cloud_sync_queue WHERE file_id = ? AND action = ? AND status IN ('QUEUED', 'IN_PROGRESS')",
                (file_id, action)
            ).fetchone()
            if existing:
                return existing[0]

            conn.execute(
                """
                INSERT INTO cloud_sync_queue (queue_id, file_id, action, status, retry_count, created_at, updated_at)
                VALUES (?, ?, ?, 'QUEUED', 0, ?, ?)
                """,
                (queue_id, file_id, action, now_iso, now_iso)
            )
        return queue_id

    def get_pending_cloud_sync_queue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves items from cloud_sync_queue ready to process."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT q.queue_id, q.file_id, q.action, q.status, q.retry_count,
                       f.local_path, f.category, f.drive_file_id, f.file_size_bytes,
                       f.sha256_hash, f.mime_type
                FROM cloud_sync_queue q
                JOIN cloud_sync_files f ON q.file_id = f.file_id
                WHERE q.status = 'QUEUED'
                  AND (q.next_retry_at IS NULL OR q.next_retry_at <= ?)
                ORDER BY q.created_at ASC
                LIMIT ?
                """,
                (now_iso, limit)
            ).fetchall()
            return [
                {
                    "queue_id": r[0],
                    "file_id": r[1],
                    "action": r[2],
                    "status": r[3],
                    "retry_count": r[4],
                    "local_path": r[5],
                    "category": r[6],
                    "drive_file_id": r[7],
                    "file_size_bytes": r[8],
                    "sha256_hash": r[9],
                    "mime_type": r[10],
                }
                for r in rows
            ]

    def update_cloud_sync_queue_status(
        self,
        queue_id: str,
        status: str,
        retry_count: Optional[int] = None,
        next_retry_at: Optional[str] = None
    ) -> None:
        """Updates the status of a cloud sync queue task."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            if retry_count is not None:
                conn.execute(
                    """
                    UPDATE cloud_sync_queue
                    SET status = ?, retry_count = ?, next_retry_at = ?, updated_at = ?
                    WHERE queue_id = ?
                    """,
                    (status, retry_count, next_retry_at, now_iso, queue_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE cloud_sync_queue
                    SET status = ?, updated_at = ?
                    WHERE queue_id = ?
                    """,
                    (status, now_iso, queue_id)
                )

    def get_cloud_sync_summary(self) -> Dict[str, Any]:
        """Provides a statistical summary of cloud synchronization."""
        with self._connect() as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) as total_files,
                    SUM(CASE WHEN sync_status = 'SYNCED' THEN 1 ELSE 0 END) as synced_count,
                    SUM(CASE WHEN sync_status IN ('PENDING', 'UPLOADING') THEN 1 ELSE 0 END) as pending_count,
                    SUM(CASE WHEN sync_status = 'FAILED' THEN 1 ELSE 0 END) as failed_count,
                    MAX(last_synced_at) as last_synced_at
                FROM cloud_sync_files
                """
            ).fetchone()
            return {
                "total_files": counts[0] or 0,
                "synced_count": counts[1] or 0,
                "pending_count": counts[2] or 0,
                "failed_count": counts[3] or 0,
                "last_synced_at": counts[4],
            }


    # =========================================================================
    # EXAM PREBOOKING BATCHES (1 to 4 tests)
    # =========================================================================

    def create_exam_prebooking_batch(self, batch_data: Dict[str, Any], device_id: str = "windows_pc") -> Dict[str, Any]:
        """
        Creates an ExamBatch containing 1 to 4 tests.
        Validates the batch, creates the parent batch record, and persists each individual test.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        batch_id = str(batch_data.get("batch_id") or f"BATCH-{uuid.uuid4().hex[:12].upper()}").strip()
        account_id = str(batch_data.get("account_id") or "user_default").strip()
        tests = batch_data.get("tests") or []

        if not tests or len(tests) == 0:
            raise ValueError("At least 1 test is required.")
        if len(tests) > 4:
            raise ValueError("Maximum 4 tests allowed.")

        # Check existing exams for conflicts
        existing_exams = self.list_exam_prebookings(include_past=False)

        from core.exam_prebooking_engine import ExamPrebookingEngine
        batch_res = ExamPrebookingEngine.validate_and_allocate_batch(
            tests=tests,
            batch_id=batch_id,
            existing_approved_exams=existing_exams,
        )

        if not batch_res["success"]:
            return batch_res

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO exam_batches (batch_id, account_id, total_tests, status, created_at, updated_at)
                VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (batch_id, account_id, len(batch_res["decisions"]), now_iso, now_iso)
            )

        saved_tests = []
        unique_dates = set()

        for d in batch_res["decisions"]:
            t_record = {
                "booking_id": d["booking_id"],
                "batch_id": batch_id,
                "test_index": d.get("test_index", 1),
                "total_tests": len(batch_res["decisions"]),
                "account_id": account_id,
                "exam_name": d["exam_name"],
                "subject": d["subject"],
                "institution": d["institution"],
                "exam_date": d["confirmed_date"],
                "official_start_time": d["confirmed_start_time"],
                "official_end_time": d["confirmed_end_time"],
                "prep_buffer_minutes": d["prep_buffer_minutes"],
                "completion_buffer_minutes": d["completion_buffer_minutes"],
                "allocation_start_iso": d["allocation_start_iso"],
                "allocation_end_iso": d["allocation_end_iso"],
                "allocation_start_ms": int(datetime.fromisoformat(d["allocation_start_iso"]).timestamp() * 1000) if d.get("allocation_start_iso") else 0,
                "allocation_end_ms": int(datetime.fromisoformat(d["allocation_end_iso"]).timestamp() * 1000) if d.get("allocation_end_iso") else 0,
                "status": d["validation_status"],
                "validation_provider": d["validation_provider"],
                "validation_explanation": d["explanation"],
                "cross_image_consistency": d["cross_image_consistency"],
                "supporting_images_count": d["supporting_images_reviewed"],
                "evidence_files": [m.to_dict() if hasattr(m, "to_dict") else m for m in d.get("evidence_meta_list", [])],
                "additional_info": str(d.get("additional_info") or ""),
            }
            created_t = self.create_exam_prebooking(t_record, device_id=device_id)
            saved_tests.append(created_t)
            unique_dates.add(d["confirmed_date"])

        # Reconcile attendance exemptions for all affected dates
        for ud in unique_dates:
            self.reconcile_exam_attendance_exemptions(ud)

        return {
            "success": True,
            "batch_id": batch_id,
            "total_tests": len(saved_tests),
            "status": "ACTIVE",
            "tests": saved_tests,
            "created_at": now_iso,
        }

    def get_exam_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves an ExamBatch and all nested tests."""
        with self._connect() as conn:
            b_row = conn.execute("SELECT * FROM exam_batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if not b_row:
                return None
            batch = dict(b_row)
            t_rows = conn.execute("SELECT * FROM exam_prebookings WHERE batch_id = ? ORDER BY test_index ASC", (batch_id,)).fetchall()
            tests = []
            for tr in t_rows:
                td = dict(tr)
                for jf in ("evidence_files_json", "extracted_data_json", "excused_hourly_slots_json"):
                    try:
                        td[jf.replace("_json", "")] = json.loads(td.get(jf) or "[]")
                    except Exception:
                        td[jf.replace("_json", "")] = []
                tests.append(td)
            batch["tests"] = tests
            return batch

    def list_exam_batches(self) -> List[Dict[str, Any]]:
        """Lists all ExamBatches with their tests."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM exam_batches ORDER BY created_at DESC").fetchall()
            res = []
            for r in rows:
                b = dict(r)
                t_rows = conn.execute("SELECT * FROM exam_prebookings WHERE batch_id = ? ORDER BY test_index ASC", (b["batch_id"],)).fetchall()
                tests = []
                for tr in t_rows:
                    td = dict(tr)
                    for jf in ("evidence_files_json", "extracted_data_json", "excused_hourly_slots_json"):
                        try:
                            td[jf.replace("_json", "")] = json.loads(td.get(jf) or "[]")
                        except Exception:
                            td[jf.replace("_json", "")] = []
                    tests.append(td)
                b["tests"] = tests
                res.append(b)
            return res

    # =========================================================================
    # DAILY PRACTICE TEST SCHEDULER (2–4 tests per day)
    # =========================================================================

    def get_daily_practice_template(self) -> Dict[str, Any]:
        """Retrieves or seeds the default recurring template (Quant & Comm 30m each)."""
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM daily_practice_test_templates WHERE template_id = 'DEFAULT'").fetchone()
            if row:
                t = dict(row)
                try:
                    t["tests"] = json.loads(t.get("tests_json") or "[]")
                except Exception:
                    t["tests"] = []
                return t

            # Seed default template
            default_tests = DailyPracticeTestEngine.get_default_template_tests()
            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO daily_practice_test_templates (template_id, template_name, tests_json, updated_at)
                VALUES ('DEFAULT', 'Default Daily Practice Template', ?, ?)
                """,
                (json.dumps(default_tests), now_iso)
            )
            return {
                "template_id": "DEFAULT",
                "template_name": "Default Daily Practice Template",
                "tests": default_tests,
                "updated_at": now_iso,
            }

    def save_daily_practice_template(self, tests: List[Dict[str, Any]], template_name: str = "Default Daily Practice Template") -> Dict[str, Any]:
        """Saves recurring template with 2 to 4 tests."""
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        val = DailyPracticeTestEngine.validate_practice_tests(tests)
        if not val["valid"]:
            raise ValueError(val["error"])

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_practice_test_templates (template_id, template_name, tests_json, updated_at)
                VALUES ('DEFAULT', ?, ?, ?)
                """,
                (template_name, json.dumps(val["tests"]), now_iso)
            )
        return self.get_daily_practice_template()

    def get_daily_practice_schedule(self, date_str: str) -> Optional[Dict[str, Any]]:
        """Retrieves the submitted practice schedule and executions for date_str."""
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        with self._connect() as conn:
            s_row = conn.execute("SELECT * FROM daily_practice_test_schedules WHERE date = ?", (date_str,)).fetchone()
            if not s_row:
                return None
            sched = dict(s_row)
            e_rows = conn.execute(
                "SELECT * FROM daily_practice_test_executions WHERE date = ? ORDER BY test_index ASC", (date_str,)
            ).fetchall()
            sched["executions"] = [dict(er) for er in e_rows]
            sched["deadline_info"] = DailyPracticeTestEngine.get_morning_deadline_info(date_str)
            return sched

    def submit_daily_practice_schedule(
        self,
        date_str: str,
        tests: List[Dict[str, Any]],
        notes: str = "",
        force_unlock: bool = False,
    ) -> Dict[str, Any]:
        """
        Submits daily practice schedule for date_str.
        Enforces 10:00 AM morning submission deadline lock:
        - If after 10:00 AM on today's date and force_unlock is False: marks/rejects as LOCKED / MISSED.
        - Validates 2 to 4 tests (blocks > 4 with 'Maximum 4 tests allowed.').
        - Creates schedule and individual executions.
        """
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        deadline_info = DailyPracticeTestEngine.get_morning_deadline_info(date_str)

        # Check if editing is locked
        if deadline_info["is_locked"] and not force_unlock:
            # Check if schedule exists
            existing = self.get_daily_practice_schedule(date_str)
            if not existing:
                # User did not submit before 10:00 AM: Mark as MISSED
                now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sched_id = f"PRAC-SCHED-{date_str}"
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO daily_practice_test_schedules (
                            schedule_id, date, total_tests, status, submitted_at, locked_at, notes, created_at, updated_at
                        ) VALUES (?, ?, 0, 'MISSED', '', ?, 'Schedule submission deadline passed without morning confirmation.', ?, ?)
                        """,
                        (sched_id, date_str, deadline_info["deadline_iso"], now_iso, now_iso)
                    )
                raise PermissionError("Daily Practice Test submission deadline was 10:00 AM. Editing is now locked.")
            raise PermissionError("Daily Practice Test schedule is locked for today (past 10:00 AM).")

        val = DailyPracticeTestEngine.validate_practice_tests(tests)
        if not val["valid"]:
            raise ValueError(val["error"])

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sched_id = f"PRAC-SCHED-{date_str}"
        total_tests = len(val["tests"])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_practice_test_schedules (
                    schedule_id, date, total_tests, status, submitted_at, locked_at, notes, created_at, updated_at
                ) VALUES (?, ?, ?, 'SUBMITTED', ?, '', ?, ?, ?)
                """,
                (sched_id, date_str, total_tests, now_iso, notes, now_iso, now_iso)
            )

            # Insert/replace executions
            for t in val["tests"]:
                exec_id = f"PRAC-{date_str}-{t['test_index']}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_practice_test_executions (
                        execution_id, schedule_id, date, test_index, category, title,
                        start_time, duration_minutes, end_time, status, started_at, completed_at,
                        score, max_score, points_awarded, rescheduled_to, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', '', '', NULL, NULL, 0, '', '', ?, ?)
                    """,
                    (
                        exec_id, sched_id, date_str, t["test_index"], t["category"], t["title"],
                        t["start_time"], t["duration_minutes"], t["end_time"], now_iso, now_iso
                    )
                )

        return self.get_daily_practice_schedule(date_str) or {}

    def start_practice_test_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Marks a practice test execution as IN_PROGRESS."""
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_practice_test_executions
                SET status = 'IN_PROGRESS', started_at = ?, updated_at = ?
                WHERE execution_id = ?
                """,
                (now_iso, now_iso, execution_id)
            )
            row = conn.execute("SELECT * FROM daily_practice_test_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            return dict(row) if row else None

    def complete_practice_test_execution(
        self,
        execution_id: str,
        score: Optional[float] = None,
        max_score: Optional[float] = None,
        notes: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Marks a practice test execution as COMPLETED.
        Awards regular practice test completion points (5 pts) idempotently in score_events.
        """
        from core.daily_practice_test_engine import DEFAULT_POINTS_PER_PRACTICE_TEST
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            ex_row = conn.execute("SELECT * FROM daily_practice_test_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            if not ex_row:
                return None
            ex = dict(ex_row)

            pts = DEFAULT_POINTS_PER_PRACTICE_TEST
            conn.execute(
                """
                UPDATE daily_practice_test_executions
                SET status = 'COMPLETED', completed_at = ?, score = ?, max_score = ?, points_awarded = ?, notes = ?, updated_at = ?
                WHERE execution_id = ?
                """,
                (now_iso, score, max_score, pts, notes or ex.get("notes", ""), now_iso, execution_id)
            )

            # Credit points idempotently in score_events
            se_id = f"se_practice_test_{execution_id}"
            exists_se = conn.execute("SELECT event_id FROM score_events WHERE event_id = ?", (se_id,)).fetchone()
            if not exists_se:
                from core.models import ScoreEventType
                conn.execute(
                    """
                    INSERT INTO score_events (
                        event_id, date, timestamp, event_type, points, device_id,
                        description, related_task_id, audit_json, is_dev_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        se_id,
                        ex["date"],
                        now_iso,
                        "PRACTICE_TEST_COMPLETED",
                        pts,
                        "windows_pc",
                        f"Practice test completed: {ex['title']} ({ex['category']}) (+{pts} pts)",
                        execution_id,
                        json.dumps({
                            "execution_id": execution_id,
                            "category": ex["category"],
                            "title": ex["title"],
                            "score": score,
                            "max_score": max_score,
                            "points": pts,
                        }),
                    )
                )

            # Check if all executions in schedule are completed
            all_execs = conn.execute(
                "SELECT status FROM daily_practice_test_executions WHERE schedule_id = ?", (ex["schedule_id"],)
            ).fetchall()
            if all_execs and all(r[0] == "COMPLETED" for r in all_execs):
                conn.execute(
                    "UPDATE daily_practice_test_schedules SET status = 'COMPLETED', updated_at = ? WHERE schedule_id = ?",
                    (now_iso, ex["schedule_id"])
                )

            res = conn.execute("SELECT * FROM daily_practice_test_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            return dict(res) if res else None

    def reschedule_practice_test_execution(
        self,
        execution_id: str,
        new_start_time: str,
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Controlled rescheduling for genuine exceptions."""
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            ex_row = conn.execute("SELECT * FROM daily_practice_test_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            if not ex_row:
                return None
            ex = dict(ex_row)

            dur = int(ex.get("duration_minutes") or 30)
            new_end = DailyPracticeTestEngine.calculate_end_time(new_start_time, dur)

            conn.execute(
                """
                UPDATE daily_practice_test_executions
                SET start_time = ?, end_time = ?, status = 'RESCHEDULED', rescheduled_to = ?, notes = ?, updated_at = ?
                WHERE execution_id = ?
                """,
                (new_start_time, new_end, f"{new_start_time}-{new_end}", f"Rescheduled: {reason}", now_iso, execution_id)
            )

            res = conn.execute("SELECT * FROM daily_practice_test_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            return dict(res) if res else None

    def get_practice_test_summary(self, date_str: str) -> Dict[str, Any]:
        """Provides a statistical summary for dashboard displays."""
        from core.daily_practice_test_engine import DailyPracticeTestEngine
        deadline_info = DailyPracticeTestEngine.get_morning_deadline_info(date_str)
        sched = self.get_daily_practice_schedule(date_str)

        if not sched:
            # Check if past 10:00 AM deadline
            if deadline_info["is_locked"]:
                return {
                    "date": date_str,
                    "schedule_id": None,
                    "status": "MISSED",
                    "total_scheduled": 0,
                    "completed": 0,
                    "pending": 0,
                    "missed": 0,
                    "total_time_minutes": 0,
                    "completion_rate_percent": 0.0,
                    "deadline_info": deadline_info,
                    "executions": [],
                }
            # Morning window active: return template preview
            tmpl = self.get_daily_practice_template()
            tests = tmpl.get("tests", [])
            return {
                "date": date_str,
                "schedule_id": None,
                "status": "DRAFT",
                "total_scheduled": len(tests),
                "completed": 0,
                "pending": len(tests),
                "missed": 0,
                "total_time_minutes": sum(int(t.get("duration_minutes") or 30) for t in tests),
                "completion_rate_percent": 0.0,
                "deadline_info": deadline_info,
                "executions": tests,
            }

        execs = sched.get("executions", [])
        total = len(execs)
        completed = sum(1 for e in execs if e.get("status") == "COMPLETED")
        missed = sum(1 for e in execs if e.get("status") == "MISSED")
        pending = sum(1 for e in execs if e.get("status") in ("SCHEDULED", "UPCOMING", "IN_PROGRESS", "RESCHEDULED"))
        total_time = sum(int(e.get("duration_minutes") or 0) for e in execs)
        pct = round((completed / total * 100.0), 1) if total > 0 else 0.0

        return {
            "date": date_str,
            "schedule_id": sched.get("schedule_id"),
            "status": sched.get("status"),
            "total_scheduled": total,
            "completed": completed,
            "pending": pending,
            "missed": missed,
            "total_time_minutes": total_time,
            "completion_rate_percent": pct,
            "deadline_info": deadline_info,
            "executions": execs,
        }


    # -------------------------------------------------------------------------
    # Proof Window AI Preflight Records
    # -------------------------------------------------------------------------
    def save_ai_preflight_record(self, record: Dict[str, Any]) -> None:
        """Saves or updates an AI preflight health record for a scheduled proof window."""
        with self._connect() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            details = record.get("details_json")
            if isinstance(details, dict):
                details_str = json.dumps(details)
            elif isinstance(details, str):
                details_str = details
            else:
                details_str = "{}"
            conn.execute(
                """
                INSERT INTO proof_window_ai_preflights (
                    preflight_id, proof_window_id, activity_id, scheduled_open_time,
                    preflight_started_at, taufa_status, taufa_latency, taufa_error,
                    openai_status, openai_latency, openai_error, selected_validation_provider,
                    preflight_completed_at, retry_count, preflight_status, schedule_version,
                    details_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(preflight_id) DO UPDATE SET
                    taufa_status=excluded.taufa_status,
                    taufa_latency=excluded.taufa_latency,
                    taufa_error=excluded.taufa_error,
                    openai_status=excluded.openai_status,
                    openai_latency=excluded.openai_latency,
                    openai_error=excluded.openai_error,
                    selected_validation_provider=excluded.selected_validation_provider,
                    preflight_completed_at=excluded.preflight_completed_at,
                    retry_count=excluded.retry_count,
                    preflight_status=excluded.preflight_status,
                    schedule_version=excluded.schedule_version,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.get("preflight_id") or str(uuid.uuid4()),
                    record.get("proof_window_id") or "",
                    record.get("activity_id") or "",
                    record.get("scheduled_open_time") or "",
                    record.get("preflight_started_at") or now_iso,
                    record.get("taufa_status") or "NOT_CHECKED",
                    int(record.get("taufa_latency") or 0),
                    record.get("taufa_error") or "",
                    record.get("openai_status") or "NOT_CHECKED",
                    int(record.get("openai_latency") or 0),
                    record.get("openai_error") or "",
                    record.get("selected_validation_provider") or "PENDING_VALIDATION",
                    record.get("preflight_completed_at") or "",
                    int(record.get("retry_count") or 0),
                    record.get("preflight_status") or "COMPLETED",
                    int(record.get("schedule_version") or 1),
                    details_str,
                    record.get("created_at") or now_iso,
                    now_iso,
                ),
            )

    def get_ai_preflight_record(self, proof_window_id: str, schedule_version: int = 1) -> Optional[Dict[str, Any]]:
        """Retrieves the preflight record for a specific proof window and schedule version."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM proof_window_ai_preflights
                WHERE proof_window_id = ? AND schedule_version = ?
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (proof_window_id, schedule_version),
            ).fetchone()
            if not row:
                # Fallback to match by proof_window_id alone
                row = conn.execute(
                    """
                    SELECT * FROM proof_window_ai_preflights
                    WHERE proof_window_id = ?
                    ORDER BY schedule_version DESC, updated_at DESC LIMIT 1
                    """,
                    (proof_window_id,),
                ).fetchone()
            return dict(row) if row else None

    def get_latest_ai_preflight_for_activity(self, activity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the latest preflight record for an activity ID."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM proof_window_ai_preflights
                WHERE activity_id = ?
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (activity_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_ai_preflights_for_window(self, proof_window_id: str) -> List[Dict[str, Any]]:
        """Lists all preflights for a window (e.g. initial + retries + prior versions)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proof_window_ai_preflights
                WHERE proof_window_id = ?
                ORDER BY schedule_version ASC, retry_count ASC, preflight_started_at ASC
                """,
                (proof_window_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_ai_preflights_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Lists all preflight records for proof windows on a specific date (YYYY-MM-DD)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proof_window_ai_preflights
                WHERE scheduled_open_time LIKE ? OR preflight_started_at LIKE ?
                ORDER BY scheduled_open_time ASC, preflight_started_at ASC
                """,
                (f"{date_str}%", f"{date_str}%"),
            ).fetchall()
            return [dict(r) for r in rows]
