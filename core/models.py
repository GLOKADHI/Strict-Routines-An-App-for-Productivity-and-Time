"""
StrictRecords Core Data Models
Defines all domain entities, enums, task types, task states, score modes/states,
design system tokens, and configurable settings across all 20 stages.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any


class TaskSemantics(str, Enum):
    EXACT_TIME = "EXACT_TIME"                    # Wake up at 07:00 AM
    DEADLINE = "DEADLINE"                        # Bath before 10:00 AM
    SCHEDULED_START = "SCHEDULED_START"          # Gym at 06:00 PM
    RECURRING_CHECKPOINT = "RECURRING_CHECKPOINT"  # Hourly updates 08:00 AM - 10:00 PM


class TaskType(str, Enum):
    """Section 6: All 10 supported task types."""
    TIME_TASK = "TIME_TASK"
    DEADLINE_TASK = "DEADLINE_TASK"
    DURATION_TASK = "DURATION_TASK"
    CHECK_IN_TASK = "CHECK_IN_TASK"
    RECURRING_TASK = "RECURRING_TASK"
    SEQUENTIAL_TASK = "SEQUENTIAL_TASK"
    EVIDENCE_TASK = "EVIDENCE_TASK"
    VERIFICATION_TASK = "VERIFICATION_TASK"
    FOCUS_SESSION = "FOCUS_SESSION"
    DEEP_WORK = "DEEP_WORK"
    CUSTOM_TASK = "CUSTOM_TASK"


class TaskState(str, Enum):
    """Section 8: Full Task Status Engine."""
    UPCOMING = "UPCOMING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"
    LATE = "LATE"
    MISSED = "MISSED"
    SKIPPED = "SKIPPED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"
    PENDING_EVIDENCE = "PENDING_EVIDENCE"
    PENDING_SYNC = "PENDING_SYNC"


class ActivityWindowStatus(str, Enum):
    """Authoritative Time-Window State Machine."""
    SCHEDULED = "SCHEDULED"
    UPCOMING = "UPCOMING"
    NOTIFIED = "NOTIFIED"
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    MISSED = "MISSED"
    CLOSED = "CLOSED"
    REJECTED_LATE = "REJECTED_LATE"
    TOO_EARLY = "TOO_EARLY"


class WakeUpAttendance(str, Enum):
    PENDING = "PENDING"
    ON_TIME = "ON_TIME"      # Before 07:05
    LATE = "LATE"            # 07:05 - 08:00
    VERY_LATE = "VERY_LATE"  # After 08:00
    MISSED = "MISSED"        # No completion


class BathVerificationMode(str, Enum):
    MANUAL = "MANUAL"
    PHOTO_PROOF = "PHOTO_PROOF"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"


class ScoreMode(str, Enum):
    REWARD_UNLOCKED = "REWARD_UNLOCKED"  # 80 - 100
    CONTROLLED_MODE = "CONTROLLED_MODE"  # 60 - 79
    STRICT_MODE = "STRICT_MODE"          # 0 - 59


class ScoreState(str, Enum):
    """Section 12: 4-tier discipline states."""
    STRICT_MODE = "STRICT_MODE"          # 0 - 59
    CONTROLLED_MODE = "CONTROLLED_MODE"  # 60 - 79
    REWARD_UNLOCKED = "REWARD_UNLOCKED"  # 80 - 89
    ELITE_DAY = "ELITE_DAY"              # 90 - 100


class EvidenceConfidence(str, Enum):
    """Section 29: Activity & Evidence Confidence."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IntegritySeverity(str, Enum):
    """Section 30: Anti-cheat & Integrity Engine severity levels."""
    NORMAL = "NORMAL"
    NOTICE = "NOTICE"
    SUSPICIOUS = "SUSPICIOUS"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class StrictBotState(str, Enum):
    """Section 43: All 10 StrictBot states."""
    WELCOME = "WELCOME"
    MOTIVATING = "MOTIVATING"
    REMINDER = "REMINDER"
    WARNING = "WARNING"
    MISSED = "MISSED"
    RESTRICTED = "RESTRICTED"
    SUCCESS = "SUCCESS"
    REWARD = "REWARD"
    SYNC = "SYNC"
    ERROR = "ERROR"


class DisciplineLevel(str, Enum):
    """Section 46: Long-term supplementary discipline levels."""
    STARTER = "STARTER"
    DISCIPLINED = "DISCIPLINED"
    CONSISTENT = "CONSISTENT"
    FOCUSED = "FOCUSED"
    ELITE = "ELITE"


class DayClassification(str, Enum):
    """Section 48: Daily streak protection classifications."""
    PERFECT_DAY = "PERFECT_DAY"
    REWARD_DAY = "REWARD_DAY"
    PARTIAL_DAY = "PARTIAL_DAY"
    FAILED_DAY = "FAILED_DAY"
    INCOMPLETE_DAY = "INCOMPLETE_DAY"


class BreakCategory(str, Enum):
    """Section 55: Break Management categories."""
    SHORT_BREAK = "SHORT_BREAK"
    MEAL = "MEAL"
    REST = "REST"
    TRAVEL = "TRAVEL"
    PERSONAL = "PERSONAL"


class SyncRecordState(str, Enum):
    """Section 80: Sync Engine states."""
    PENDING = "PENDING"
    PENDING_SYNC = "PENDING_SYNC"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


class HourlyActivityCategory(str, Enum):
    PRODUCTIVE = "PRODUCTIVE"
    STUDY = "STUDY"
    CODING = "CODING"
    COLLEGE = "COLLEGE"
    PROJECT = "PROJECT"
    RESEARCH = "RESEARCH"
    EXERCISE = "EXERCISE"
    GYM = "GYM"
    READING = "READING"
    WORK = "WORK"
    BREAK = "BREAK"
    ENTERTAINMENT = "ENTERTAINMENT"
    IDLE = "IDLE"
    UNKNOWN = "UNKNOWN"
    WASTED_TIME = "WASTED TIME"
    CUSTOM = "CUSTOM"


PRODUCTIVE_ACTIVITIES = {
    HourlyActivityCategory.PRODUCTIVE.value,
    HourlyActivityCategory.STUDY.value,
    HourlyActivityCategory.CODING.value,
    HourlyActivityCategory.COLLEGE.value,
    HourlyActivityCategory.PROJECT.value,
    HourlyActivityCategory.RESEARCH.value,
    HourlyActivityCategory.EXERCISE.value,
    HourlyActivityCategory.GYM.value,
    HourlyActivityCategory.READING.value,
    HourlyActivityCategory.WORK.value,
}


class ScoreEventType(str, Enum):
    WAKEUP_COMPLETED = "WAKEUP_COMPLETED"
    WAKEUP_ON_TIME_BONUS = "WAKEUP_ON_TIME_BONUS"
    MOTIVATION_TRACK_COMPLETED = "MOTIVATION_TRACK_COMPLETED"
    WAKEUP_MISSED = "WAKEUP_MISSED"
    SNOOZE = "SNOOZE"
    BATH_COMPLETED = "BATH_COMPLETED"
    BATH_MISSED = "BATH_MISSED"
    HOURLY_PRODUCTIVE = "HOURLY_PRODUCTIVE"
    HOURLY_WASTED = "HOURLY_WASTED"
    HOURLY_MISSED = "HOURLY_MISSED"
    HOURLY_EVIDENCE = "HOURLY_EVIDENCE"
    GYM_COMPLETED = "GYM_COMPLETED"
    GYM_SKIPPED = "GYM_SKIPPED"
    GYM_EVIDENCE = "GYM_EVIDENCE"
    FOCUS_SESSION_COMPLETED = "FOCUS_SESSION_COMPLETED"
    DEEP_WORK_COMPLETED = "DEEP_WORK_COMPLETED"
    CUSTOM_TASK_COMPLETED = "CUSTOM_TASK_COMPLETED"
    CUSTOM_TASK_MISSED = "CUSTOM_TASK_MISSED"
    BREAK_LOGGED = "BREAK_LOGGED"
    ENTERTAINMENT_VIOLATION = "ENTERTAINMENT_VIOLATION"
    STREAK_BONUS = "STREAK_BONUS"
    CONSISTENCY_BONUS = "CONSISTENCY_BONUS"
    SCORE_CORRECTION = "SCORE_CORRECTION"
    PROOF_APPROVED = "PROOF_APPROVED"
    PROOF_REJECTED = "PROOF_REJECTED"
    ADMIN_SCORE_ADJUSTMENT = "ADMIN_SCORE_ADJUSTMENT"
    PC_SHUTDOWN_BONUS = "PC_SHUTDOWN_BONUS"
    DAILY_RESET = "DAILY_RESET"
    INITIAL_RESET = "INITIAL_RESET"
    REVERSAL = "REVERSAL"
    EXAM_EXCUSED = "EXAM_EXCUSED"
    PRACTICE_TEST_COMPLETED = "PRACTICE_TEST_COMPLETED"


class UserRole(str, Enum):
    USER = "USER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class ProofStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESUBMISSION_ALLOWED = "RESUBMISSION_ALLOWED"
    RESUBMITTED = "RESUBMITTED"
    DISPUTED = "DISPUTED"
    FINALIZED = "FINALIZED"


class ProofRejectionReason(str, Enum):
    WRONG_TASK = "Wrong task"
    INSUFFICIENT_EVIDENCE = "Insufficient evidence"
    UNCLEAR_EVIDENCE = "Unclear evidence"
    DUPLICATE = "Duplicate"
    LATE_SUBMISSION = "Late submission"
    DOES_NOT_MATCH_DESCRIPTION = "Does not match description"
    OTHER = "Other"


class ApprovalPolicy(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    ADMIN_REVIEW_REQUIRED = "ADMIN_REVIEW_REQUIRED"
    TWO_ADMINS_REQUIRED = "TWO_ADMINS_REQUIRED"


class VerificationType(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    HYBRID = "HYBRID"


class ProofRequirement(str, Enum):
    NO_PROOF = "NO_PROOF"
    OPTIONAL = "OPTIONAL"
    REQUIRED = "REQUIRED"
    REQUIRED_ADMIN_APPROVAL = "REQUIRED_ADMIN_APPROVAL"


class AdminActionType(str, Enum):
    ADMIN_LOGIN = "ADMIN_LOGIN"
    ADMIN_LOGOUT = "ADMIN_LOGOUT"
    TASK_CREATED = "TASK_CREATED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_DISABLED = "TASK_DISABLED"
    TASK_ARCHIVED = "TASK_ARCHIVED"
    POINT_RULE_CHANGED = "POINT_RULE_CHANGED"
    REWARD_RULE_CHANGED = "REWARD_RULE_CHANGED"
    RESTRICTION_CHANGED = "RESTRICTION_CHANGED"
    SCHEDULE_CHANGED = "SCHEDULE_CHANGED"
    PROOF_APPROVED = "PROOF_APPROVED"
    PROOF_REJECTED = "PROOF_REJECTED"
    POINT_ADJUSTED = "POINT_ADJUSTED"
    DEVICE_REVOKED = "DEVICE_REVOKED"
    USER_SETTING_CHANGED = "USER_SETTING_CHANGED"
    CONFIG_PUBLISHED = "CONFIG_PUBLISHED"
    CONFIG_RESTORED = "CONFIG_RESTORED"
    REPORT_EXPORTED = "REPORT_EXPORTED"
    ADMIN_OVERRIDE = "ADMIN_OVERRIDE"
    RESCHEDULE_ROUTINE = "RESCHEDULE_ROUTINE"
    ADMIN_PIN_CHANGED = "ADMIN_PIN_CHANGED"
    PERMISSION_CHANGED = "PERMISSION_CHANGED"


# =============================================================================
# CENTRALIZED DESIGN SYSTEM TOKENS (Section 154)
# =============================================================================
DESIGN_SYSTEM_TOKENS: Dict[str, Any] = {
    "version": "2026.1.0",
    "dark": {
        "background_primary": "#07090D",
        "background_secondary": "#0E1117",
        "background_tertiary": "#141923",
        "surface_border": "#232B3A",
        "text_primary": "#F9FAFB",
        "text_secondary": "#9CA3AF",
        "text_muted": "#6B7280",
        "primary_brand": "#6366F1",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "info": "#3B82F6",
    },
    "light": {
        "background_primary": "#F8FAFC",
        "background_secondary": "#FFFFFF",
        "background_tertiary": "#F1F5F9",
        "surface_border": "#E2E8F0",
        "text_primary": "#0F172A",
        "text_secondary": "#475569",
        "text_muted": "#94A3B8",
        "primary_brand": "#4F46E5",
        "success": "#16A34A",
        "warning": "#D97706",
        "danger": "#DC2626",
        "info": "#2563EB",
    },
    "colors": {
        "dark": {
            "bg_primary": "#090C12",
            "bg_elevated": "#111622",
            "bg_subtle": "#171E2E",
            "bg_glass": "rgba(17, 22, 34, 0.85)",
            "text_primary": "#F8FAFC",
            "text_secondary": "#CBD5E1",
            "text_muted": "#94A3B8",
            "border_subtle": "#1E293B",
            "border_focus": "#334155",
            "accent_primary": "#6366F1",
            "accent_elite": "#8B5CF6",
            "accent_success": "#10B981",
            "accent_warning": "#F59E0B",
            "accent_danger": "#EF4444",
        },
        "light": {
            "bg_primary": "#F8FAFC",
            "bg_elevated": "#FFFFFF",
            "bg_subtle": "#F1F5F9",
            "bg_glass": "rgba(255, 255, 255, 0.90)",
            "text_primary": "#0F172A",
            "text_secondary": "#334155",
            "text_muted": "#64748B",
            "border_subtle": "#E2E8F0",
            "border_focus": "#CBD5E1",
            "accent_primary": "#4F46E5",
            "accent_elite": "#7C3AED",
            "accent_success": "#059669",
            "accent_warning": "#D97706",
            "accent_danger": "#DC2626",
        },
    },
    "typography": {
        "font_sans": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
        "font_mono": "'JetBrains Mono', 'Cascadia Code', monospace",
        "hero_score_size": "3.4rem",
        "clock_size": "2.6rem",
        "h1_size": "1.35rem",
        "h2_size": "1.05rem",
        "body_size": "0.875rem",
        "caption_size": "0.75rem",
    },
    "spacing": {
        "xs": "4px",
        "sm": "8px",
        "md": "14px",
        "lg": "20px",
        "xl": "28px",
        "xxl": "40px",
    },
    "radii": {
        "sm": "8px",
        "md": "14px",
        "lg": "20px",
        "pill": "9999px",
    },
    "elevation": {
        "dark_card": "0 12px 32px rgba(0, 0, 0, 0.45)",
        "light_card": "0 6px 20px rgba(15, 23, 42, 0.06)",
        "hero_glow": "0 0 28px rgba(99, 102, 241, 0.22)",
    },
    "navigation": {
        "mobile_tabs": ["HOME", "TASKS", "TIMELINE", "REWARDS", "PROFILE"],
        "desktop_sections": [
            "COCKPIT",
            "TASKS",
            "TIMELINE",
            "REWARDS",
            "ANALYTICS",
            "PROFILE",
            "ADMIN",
            "DIAGNOSTICS",
        ],
        "admin_sections": [
            "Dashboard",
            "Users",
            "Devices",
            "Schedules",
            "Tasks",
            "Scoring",
            "Rewards",
            "Restrictions",
            "Evidence",
            "Reports",
            "Synchronization",
            "Audit Logs",
            "Security",
            "Settings",
        ],
        "admin_portal_sections": [
            "Dashboard",
            "Users",
            "Devices",
            "Schedules",
            "Tasks",
            "Scoring",
            "Rewards",
            "Restrictions",
            "Evidence",
            "Reports",
            "Synchronization",
            "Audit Logs",
            "Security",
            "Settings",
        ],
    },
}


@dataclass
class UserSettings:
    user_id: str = "user_lok"
    # 1. Default Daily Routine & Wake-up (Section 4, 13-19)
    wakeup_prep_time: str = "06:50"
    wakeup_time: str = "07:00"
    wakeup_on_time_limit: str = "07:05"
    wakeup_late_limit: str = "08:00"
    snooze_penalty: int = -5
    wakeup_points: int = 15
    wakeup_on_time_bonus: int = 5
    wakeup_missed_penalty: int = -15
    # Motivation track & Bluetooth
    motivation_track_name: str = "StrictRecords Morning Protocol - Rise & Execute.wav"
    motivation_track_duration_sec: int = 222  # 03:42 default display duration
    motivation_required_completion_pct: float = 100.0
    motivation_track_points: int = 10
    pause_on_track_interruption: bool = True
    required_bluetooth_device: str = "ANY_BLUETOOTH_AUDIO"
    # Bath Deadline (Section 20)
    bath_deadline: str = "10:00"
    bath_grace_minutes: int = 0
    bath_verification_mode: str = BathVerificationMode.MANUAL.value
    bath_points: int = 10
    bath_missed_penalty: int = -10
    # Gym Requirement (Section 5, 21)
    gym_time: str = "18:00"
    gym_deadline: str = "20:00"
    gym_grace_minutes: int = 20
    gym_min_duration_minutes: int = 30
    gym_points: int = 15
    gym_evidence_points: int = 3
    gym_missed_penalty: int = -10
    # Hourly Checkpoints (Section 22-25)
    hourly_start_time: str = "08:00"
    hourly_end_time: str = "22:00"
    hourly_grace_minutes: int = 15
    hourly_productive_points: int = 5
    hourly_missed_penalty: int = -5
    hourly_wasted_penalty: int = -3
    evidence_points: int = 3
    # Focus Sessions, Deep Work, Breaks & Bonuses (Section 32, 53-55)
    focus_session_points: int = 10
    deep_work_points: int = 20
    break_points_impact: int = 0
    streak_bonus_points: int = 5
    consistency_bonus_points: int = 5
    # PC Shutdown Bonus & Next-Day Points (Part 6)
    pc_shutdown_bonus_enabled: bool = True
    pc_shutdown_bonus_points: int = 15
    pc_shutdown_cutoff_time: str = "23:45"
    pc_shutdown_timezone: str = "Asia/Kolkata"
    pc_shutdown_approval_required: bool = True
    pc_shutdown_deadline_time: str = "23:59"
    # Entertainment & Thresholds (Section 12, 37-42)
    entertainment_violation_penalty: int = 0
    elite_threshold: int = 90
    reward_threshold: int = 80
    controlled_mode_threshold: int = 60
    max_daily_score: int = 100
    allow_bonus_overflow: bool = False
    restriction_end_time: str = "07:00"  # Next day's daily reset
    allow_local_music_in_controlled_mode: bool = True
    appearance_mode: str = "DARK"  # LIGHT, DARK, SYSTEM
    # Overnight & Workstation Lock (Section 68-71)
    overnight_start_time: str = "22:30"
    overnight_end_time: str = "07:00"
    overnight_lock_enabled: bool = True
    windows_workstation_lock_enabled: bool = False
    deadline_warnings_enabled: bool = True
    # Weekly Mission Goals (Section 160)
    weekly_target_score: int = 80
    weekly_target_gym_sessions: int = 4
    weekly_target_hourly_pct: int = 90
    weekly_target_wakeup_days: int = 6
    # Retention & Privacy Policies (Section 117, 169)
    evidence_retention_days: int = 90
    activity_retention_days: int = 90
    report_retention_days: int = 365
    admin_expose_evidence: bool = False
    protocol_version: str = "2.0.0"
    restricted_android_apps: List[str] = field(default_factory=lambda: [
        "com.google.android.youtube",
        "com.android.youtube",
        "com.instagram.android",
        "com.android.instagram",
        "com.google.android.apps.youtube.music",
    ])
    local_music_android_apps: List[str] = field(default_factory=lambda: [
        "com.strictrecords.app.localplayer",
        "in.krosbits.musicolet",
        "org.videolan.vlc",
    ])
    restricted_windows_apps: List[str] = field(default_factory=lambda: [
        "youtube.com",
        "youtube",
        "instagram.com",
        "instagram",
        "spotify.exe",
        "music.youtube.com",
        "netflix.com",
        "steam.exe",
    ])
    productive_windows_apps: List[str] = field(default_factory=lambda: [
        "code.exe",
        "visual studio code",
        "pycharm64.exe",
        "pycharm",
        "idea64.exe",
        "studio64.exe",
        "android studio",
        "devenv.exe",
        "powershell.exe",
        "windowsterminal.exe",
        "winword.exe",
        "excel.exe",
    ])
    study_browser_keywords: List[str] = field(default_factory=lambda: [
        "docs.python.org",
        "developer.android.com",
        "stackoverflow.com",
        "github.com",
        "arxiv.org",
        "coursera.org",
        "edx.org",
        "leetcode.com",
        "wikipedia.org",
        "lecture",
        "documentation",
        "course",
    ])
    local_music_windows_apps: List[str] = field(default_factory=lambda: [
        "wmplayer.exe",
        "foobar2000.exe",
        "musicbee.exe",
        "aimp.exe",
        ".mp3",
        ".flac",
        ".wav",
    ])
    reward_categories: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"code": "YOUTUBE", "name": "YouTube", "min_score": 80, "category": "STREAMING_VIDEO"},
        {"code": "SPOTIFY", "name": "Spotify", "min_score": 80, "category": "STREAMING_MUSIC"},
        {"code": "YOUTUBE_MUSIC", "name": "YouTube Music", "min_score": 80, "category": "STREAMING_MUSIC"},
        {"code": "LOCAL_MUSIC", "name": "Local Music (MP3/FLAC)", "min_score": 60, "category": "LOCAL_MEDIA"},
        {"code": "GAMING", "name": "Gaming (Steam/Console)", "min_score": 80, "category": "GAMING"},
        {"code": "ENTERTAINMENT_APPS", "name": "Entertainment Apps (Netflix/IG)", "min_score": 80, "category": "SOCIAL_VIDEO"},
        {"code": "WEBSITES", "name": "Entertainment Websites", "min_score": 80, "category": "WEB"},
    ])
    reward_definition: str = "YouTube & Full Streaming Entertainment Unlocked"
    end_of_day_review_time: str = "22:30"
    # AI & Sync & Privacy
    ai_enabled: bool = True
    ai_api_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    cloud_sync_enabled: bool = True
    privacy_allow_app_usage: bool = True
    privacy_allow_window_titles: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreEvent:
    event_id: str
    date: str
    timestamp: str
    event_type: str
    device_id: str
    description: str
    points: int
    evidence_id: Optional[str] = None
    related_task_id: Optional[str] = None
    rule_version: int = 1
    parent_event_id: Optional[str] = None
    audit_details: Dict[str, Any] = field(default_factory=dict)
    is_dev_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RestrictionEvent:
    """Section 113: Android & Windows App Restriction Event."""
    restriction_event_id: str
    timestamp: str
    device_id: str
    package_name: str
    application_name: str
    score_at_detection: int
    required_score: int
    action: str  # RESTRICTED, ALLOWED
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdminUser:
    admin_id: str
    username: str
    role: str = UserRole.ADMIN.value
    created_at: str = ""
    last_login: Optional[str] = None
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdminSession:
    session_id: str
    admin_id: str
    token: str
    created_at: str
    expires_at: str
    device_id: str = "windows_admin_console"
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProofReview:
    review_id: str
    evidence_id: str
    user_id: str
    task_code: str
    date: str
    hour_slot: str = ""
    status: str = ProofStatus.PENDING.value
    submitted_at: str = ""
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    decision: Optional[str] = None
    reason_category: Optional[str] = None
    reason_note: Optional[str] = None
    points_awarded: int = 0
    revision_number: int = 1
    signals: Dict[str, Any] = field(default_factory=dict)
    priority: str = "NORMAL"
    file_path: str = ""
    sha256_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProofRevision:
    revision_id: str
    evidence_id: str
    revision_number: int
    status: str
    submitted_at: str
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reason_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConfigurationVersion:
    version_code: str  # e.g. "v12"
    version_number: int
    status: str = "PUBLISHED"  # DRAFT, REVIEW, PUBLISHED, ARCHIVED
    rules_json: Dict[str, Any] = field(default_factory=dict)
    created_by: str = "Administrator"
    created_at: str = ""
    published_at: Optional[str] = None
    effective_from: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdminAuditEvent:
    audit_id: str
    admin_user: str
    action_type: str
    field_changed: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    timestamp: str = ""
    device_id: str = "admin_portal"
    details_json: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProofApprovalEvent:
    proof_id: str
    admin_id: str
    timestamp: str
    decision: str = "APPROVED"
    reason: str = "Evidence verified by Administrator"
    points_awarded: int = 3
    rule_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProofRejectionEvent:
    proof_id: str
    admin_id: str
    timestamp: str
    reason: str
    status: str = "REJECTED"
    allow_resubmission: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# SECURE-EXAM-BROWSER STYLE STRICT WINDOWS SESSION MODELS
# =============================================================================

class StrictSessionState(str, Enum):
    NORMAL = "NORMAL"
    PREPARING = "PREPARING"
    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    RESTRICTED = "RESTRICTED"
    PRIVILEGED = "PRIVILEGED"
    ENDING = "ENDING"
    RESTORING = "RESTORING"
    ERROR = "ERROR"


class PrivilegeOutput(str, Enum):
    ENTERTAINMENT_ALLOWED = "ENTERTAINMENT_ALLOWED"
    ENTERTAINMENT_BLOCKED = "ENTERTAINMENT_BLOCKED"


class HourlyProofStatus(str, Enum):
    PENDING = "PENDING"
    VALID = "VALID"
    INVALID = "INVALID"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    DUPLICATE = "DUPLICATE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class StrictAuditAction(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    SESSION_RESET = "SESSION_RESET"
    POLICY_LOADED = "POLICY_LOADED"
    APP_BLOCKED = "APP_BLOCKED"
    SITE_BLOCKED = "SITE_BLOCKED"
    PRIVILEGE_GRANTED = "PRIVILEGE_GRANTED"
    PRIVILEGE_REVOKED = "PRIVILEGE_REVOKED"
    PROOF_VERIFIED = "PROOF_VERIFIED"
    PROOF_REJECTED = "PROOF_REJECTED"
    ADMIN_OVERRIDE = "ADMIN_OVERRIDE"


@dataclass
class StrictSessionPolicy:
    policy_version: str = "StrictRecords Policy v12"
    allowed_applications: List[str] = field(
        default_factory=lambda: [
            "StrictRecords.exe",
            "Comet.exe",
            "CalculatorApp.exe",
            "calc.exe",
            "Code.exe",
            "notepad.exe",
        ]
    )
    approved_browser: str = "Comet.exe"
    allowed_domains: List[str] = field(
        default_factory=lambda: [
            "youtube.com",
            "instagram.com",
            "facebook.com",
            "vimeo.com",
            "github.com",
            "stackoverflow.com",
            "wikipedia.org",
        ]
    )
    blocked_domains: List[str] = field(default_factory=list)
    settings_restricted: bool = True
    score_threshold: int = 80
    hourly_proof_interval_minutes: int = 60
    emergency_exit_policy: str = "ADMIN_PIN_REQUIRED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrictSessionRecord:
    session_id: str
    user_id: str = "user_lok"
    device_id: str = "windows_lok_pc"
    policy_version: str = "StrictRecords Policy v12"
    state: str = "NORMAL"
    start_time: str = ""
    end_time: Optional[str] = None
    precheck_passed: bool = False
    precheck_details: Dict[str, Any] = field(default_factory=dict)
    crash_recovered: bool = False
    exit_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# NEXT-DAY ROUTINE SCHEDULER MODELS
# =============================================================================

class RoutineScheduleStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    LOCKED = "LOCKED"
    SUPERSEDED = "SUPERSEDED"


@dataclass
class RoutineActivitySpec:
    activity_id: str
    task_type: str  # WAKE_UP, BATH, GYM, STUDY, PROGRAMMING, EXERCISE, PERSONAL, CUSTOM, HOURLY
    title: str
    scheduled_time: str  # HH:MM (24-hr)
    window_duration_minutes: int = 20
    window_start: str = ""
    window_end: str = ""
    notification_time: str = ""
    closing_warning_time: str = ""
    proof_requirements: str = "REQUIRED"
    points: int = 10
    penalty_points: int = 0
    validation_policy: str = "STRICT_TIME_WINDOW_20M"
    enabled: bool = True
    required_by_admin: bool = False
    schedule_date: str = ""
    status: str = ActivityWindowStatus.SCHEDULED.value
    submitted_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RoutineScheduleRecord:
    schedule_id: str
    account_id: str
    schedule_version: int
    schedule_date: str
    timezone: str = "Asia/Kolkata"
    status: str = RoutineScheduleStatus.DRAFT.value
    wake_up_time: str = "07:00"
    bath_time: str = "07:30"
    gym_time: str = "18:30"
    gym_enabled: bool = True
    gym_required_by_admin: bool = False
    custom_activities: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    confirmed_at: Optional[str] = None
    confirmed_by_actor: Optional[str] = None
    confirmed_by_device: Optional[str] = None
    locked_at: Optional[str] = None
    cutoff_time: str = "23:59"
    reschedule_audit_log: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeductionProposal:
    proposal_id: str
    date: str
    timestamp: str
    event_type: str
    device_id: str
    description: str
    proposed_points: int
    rule_name: str
    reason: str
    platform: str = "WINDOWS"
    status: str = "PROPOSED"  # PROPOSED, APPROVED, REJECTED, CANCELLED
    human_authorized: bool = False
    authorized_by: Optional[str] = None
    authorized_at: Optional[str] = None
    final_event_id: Optional[str] = None
    audit_json: str = "{}"
    is_dev_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransactionRecord:
    transaction_id: str
    title: str
    amount: int
    amount_formatted: str
    reason: str
    timestamp: str
    timestamp_formatted: str
    date: str
    category: str
    status: str
    origin: str
    reference_id: str = ""
    rule_applied: str = ""
    human_confirmation: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    is_negative_proposal: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


