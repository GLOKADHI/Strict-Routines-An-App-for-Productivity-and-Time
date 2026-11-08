"""
Automated test suite verifying the Adaptive Strict Session Timer Calculation
and 5-minute buffer enforcement across all required scenarios.
"""

import io
import json
import os
import time
from datetime import datetime, timedelta

import pytest
from PIL import Image

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST
from windows.strict_study_workflow import StrictStudyWorkflow


class VirtualClock:
    """Controllable virtual clock providing decoupled wall-clock and monotonic progression."""
    def __init__(self, start_dt=None):
        self.now = start_dt or datetime(2026, 11, 4, 15, 10, tzinfo=IST)
        self.mono = 1000.0

    def wall(self):
        return self.now

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.mono += float(seconds)

    def jump_wall_only(self, seconds):
        """Simulate Windows clock tampering or NTP sync without monotonic change."""
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def rig(tmp_path):
    db_file = tmp_path / "adaptive_timer.db"
    db = StrictRecordsDB(str(db_file))
    clock = VirtualClock()
    with db._connect() as c:
        c.execute(
            """INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status)
            VALUES('a55','user_lok','Galaxy A55','ANDROID','test',?,'CONNECTED')""",
            (clock.now.isoformat(),)
        )
    workflow = StrictStudyWorkflow(db, clock=clock.wall, monotonic=clock.monotonic)
    return db, clock, workflow, tmp_path


def create_window(rig, target_dt, ident='hourly_window', duration_minutes=20):
    db, _, _, _ = rig
    end_dt = target_dt + timedelta(minutes=duration_minutes)
    with db._connect() as c:
        c.execute(
            """INSERT INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,
            window_start,window_end,notification_time,status) VALUES(?,'user_lok','HOURLY',?,?,?,?,?,'PENDING')""",
            (
                ident,
                target_dt.date().isoformat(),
                (target_dt + timedelta(minutes=10)).isoformat(),
                target_dt.isoformat(),
                end_dt.isoformat(),
                target_dt.isoformat(),
            )
        )
    return target_dt


def step_study(rig, seconds, monitored=True):
    _, clock, workflow, _ = rig
    while seconds > 0:
        step = min(seconds, 10)
        clock.advance(step)
        workflow.tick(monitoring_ok=monitored)
        seconds -= step


def submit_android_proof(rig, activity_id='hourly_window', submission_id='sub-adaptive-1'):
    db, clock, _, path = rig
    raw = io.BytesIO()
    Image.new('RGB', (120, 120), (50, 95, 150)).save(raw, format='JPEG')
    proof = ScoringEngine(db=db, storage_root=str(path)).store_evidence_file(
        date=clock.now.date().isoformat(),
        record_type='Hourly',
        related_record_id=activity_id,
        device_id='a55',
        file_bytes=raw.getvalue(),
        filename_hint='proof.jpg'
    )
    payload = dict(
        submission_id=submission_id,
        activity_id=activity_id,
        account_id='user_lok',
        schedule_version=1,
        source_device_id='a55',
        source_platform='ANDROID',
        task_type='HOURLY',
        status='SUBMITTED',
        accepted_at=clock.now.isoformat(),
        category='STUDY',
        description='Adaptive timer proof verification',
        has_evidence=True,
        evidence_id=proof['evidence_id'],
        evidence_sha256=proof['sha256_hash']
    )
    with db._connect() as c:
        c.execute(
            "UPDATE scheduled_activities SET status='VALIDATING',submission_id=? WHERE activity_id=?",
            (submission_id, activity_id)
        )
        c.execute(
            'INSERT INTO proof_submission_events VALUES(?,?,?,?,?,?,?,?,?)',
            (
                f'evt-{submission_id}',
                payload['account_id'],
                submission_id,
                activity_id,
                payload['schedule_version'],
                payload['source_device_id'],
                payload['accepted_at'],
                'SUBMITTED',
                json.dumps(payload)
            )
        )
    return payload


# ══════════════════════════════════════════════════════════════════════════════════
# 1. Exact 3:50 PM Submission Window Table Examples (Requirement 2)
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("start_time_str,until_min,focus_min,buffer_min", [
    ("15:10", 40, 35, 5),
    ("15:15", 35, 30, 5),
    ("15:20", 30, 25, 5),
    ("15:25", 25, 20, 5),
    ("15:30", 20, 15, 5),
    ("15:35", 15, 10, 5),
    ("15:40", 10, 5, 5),
])
def test_350_pm_submission_window_table_examples(rig, start_time_str, until_min, focus_min, buffer_min):
    """
    Verifies the exact table from Requirement 2:
    Next submission window is 3:50 PM (15:50).
    Focus Duration = Time Until Next Submission Window - 5 Minutes.
    Buffer = 5 Minutes.
    """
    db, clock, workflow, _ = rig

    # Parse start time on reference date 2026-11-04
    h, m = map(int, start_time_str.split(":"))
    clock.now = datetime(2026, 11, 4, h, m, tzinfo=IST)
    window_open_dt = datetime(2026, 11, 4, 15, 50, tzinfo=IST)

    create_window(rig, window_open_dt, ident=f'win-350-{until_min}')
    view = workflow.begin(f'sess-{until_min}', 'user_lok', 'a55')

    # Verify initial calculation
    assert view['focus_target_seconds'] == focus_min * 60, (
        f"At {start_time_str} ({until_min}m until window), focus duration must be {focus_min}m"
    )
    assert view['timing_rule'] == 'WINDOW_MINUS_FIVE_MINUTES'
    assert view['initial_focus_target_seconds'] == focus_min * 60
    assert view['window_remaining_seconds'] == until_min * 60

    # Advance until 1 second before focus completes
    step_study(rig, (focus_min * 60) - 1)
    assert not workflow.s.get('focus_completed_at')
    assert workflow.s['state'] == 'ACTIVE_FOCUS'

    # Advance 1 second -> Focus completion
    step_study(rig, 1)
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_PROOF_WINDOW'

    # Exactly 5-minute buffer must be remaining before submission window opens
    view_at_comp = workflow.view()
    assert view_at_comp['window_remaining_seconds'] == buffer_min * 60
    assert view_at_comp['focus_remaining_seconds'] == 0

    # Advance through the 5-minute buffer (up to 3:50 PM window opening)
    step_study(rig, (buffer_min * 60) - 1)
    assert workflow.s['state'] == 'AWAITING_PROOF_WINDOW'

    # Reaching 3:50 PM opens the window
    step_study(rig, 1)
    assert clock.now == window_open_dt
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'
    assert not workflow.view()['end_enabled']


# ══════════════════════════════════════════════════════════════════════════════════
# 2. Windows More Than 40 Minutes Away (Requirement 3)
# ══════════════════════════════════════════════════════════════════════════════════

def test_window_more_than_40_minutes_away_standard_timing(rig):
    """
    Windows More Than 40 Minutes Away:
    - Standard focus duration: 35 minutes (2100s).
    - Additional adaptive flexibility: maximum 5 minutes (up to 40 min / 2400s).
    - If window does not open within 40 min, complete study phase and wait for actual scheduled window.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 14, 50, tzinfo=IST) # 60 minutes until 15:50
    window_open_dt = datetime(2026, 11, 4, 15, 50, tzinfo=IST)
    create_window(rig, window_open_dt, ident='win-60m')

    view = workflow.begin('sess-60m', 'user_lok', 'a55')
    assert view['focus_target_seconds'] == 35 * 60 # 35 min standard target
    assert view['timing_rule'] == '35_MINUTES_PLUS_ADAPTIVE_MAX_40'

    # Advance 35 minutes -> minimum duration completed, transitions to ADAPTIVE_COMPLETION
    step_study(rig, 35 * 60)
    assert workflow.s['state'] == 'ADAPTIVE_COMPLETION'
    assert not workflow.s.get('focus_completed_at')

    # Study for additional 5 minutes of adaptive flexibility (total 40 minutes)
    step_study(rig, 5 * 60)
    assert workflow.s['elapsed_seconds'] == 40 * 60
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_PROOF_WINDOW'

    # Remaining time until window (still 20 minutes away)
    assert workflow.view()['window_remaining_seconds'] == 20 * 60


def test_window_opens_during_adaptive_five_minute_period(rig):
    """
    If the submission window opens during the additional five-minute period (between min 35 and 40),
    immediately complete the study phase.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    # Window opens at 38 minutes
    window_open_dt = datetime(2026, 11, 4, 15, 38, tzinfo=IST)
    create_window(rig, window_open_dt, ident='win-38m')

    # Schedule changes or start where window is at min 38:
    # If scheduled at 38m, target was WINDOW_MINUS_FIVE_MINUTES (33m).
    # To test opening during additional period of a >40m start:
    # Start at 50m window, then admin moves window to open at min 38!
    window_50m = datetime(2026, 11, 4, 15, 50, tzinfo=IST)
    with db._connect() as c:
        c.execute("DELETE FROM scheduled_activities")
    create_window(rig, window_50m, ident='win-adaptive-open')

    workflow.begin('sess-adaptive-early-win', 'user_lok', 'a55')
    assert workflow.s['timing_rule'] == '35_MINUTES_PLUS_ADAPTIVE_MAX_40'

    # Study 35 minutes -> enters ADAPTIVE_COMPLETION
    step_study(rig, 35 * 60)
    assert workflow.s['state'] == 'ADAPTIVE_COMPLETION'

    # Now at minute 37, window opens!
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?, schedule_version=2", (clock.now.isoformat(),))

    workflow.tick(refresh=True)
    # Study phase must immediately complete
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'


# ══════════════════════════════════════════════════════════════════════════════════
# 3. Windows Five Minutes or Less Away (Requirement 4)
# ══════════════════════════════════════════════════════════════════════════════════

def test_imminent_window_skips_to_following_eligible_window(rig):
    """
    If insufficient time remains for the 5-minute buffer:
    Skip the imminent window and select the following scheduled window.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    # Window 1: 4 minutes away (imminent)
    create_window(rig, clock.now + timedelta(minutes=4), ident='imminent-1')
    # Window 2: 50 minutes away (following)
    create_window(rig, clock.now + timedelta(minutes=50), ident='following-1')

    view = workflow.begin('sess-skip-imminent', 'user_lok', 'a55')
    assert workflow.s['window']['activity_id'] == 'following-1'
    assert 'skipped' in workflow.s['message'].lower()
    assert view['focus_target_seconds'] == 35 * 60 # Standard focus for 50m window


def test_no_eligible_window_more_than_five_minutes_away_is_rejected(rig):
    """
    Do not create a negative or zero-duration timer.
    Displays clear explanation that the window is imminent.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    create_window(rig, clock.now + timedelta(minutes=3), ident='only-imminent')

    with pytest.raises(ValueError, match="more than five minutes"):
        workflow.begin('sess-fail-imminent', 'user_lok', 'a55')


# ══════════════════════════════════════════════════════════════════════════════════
# 4. Window Opens Before 5-Minute Buffer & Window Opens Exactly at Focus Completion
# ══════════════════════════════════════════════════════════════════════════════════

def test_window_opens_before_five_minute_buffer(rig):
    """
    If window opens unexpectedly before the planned 5-minute buffer:
    The study phase completes immediately and transitions to AWAITING_ANDROID_SUBMISSION.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    # 25 min until window -> focus target is 20 min
    create_window(rig, clock.now + timedelta(minutes=25), ident='win-early-open')
    workflow.begin('sess-early-open', 'user_lok', 'a55')
    assert workflow.s['focus_target_seconds'] == 20 * 60

    # User studies for 12 minutes
    step_study(rig, 12 * 60)
    assert workflow.s['state'] == 'ACTIVE_FOCUS'

    # Window opens unexpectedly now (e.g. administrator manual window open or early gate)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?, schedule_version=2", (clock.now.isoformat(),))

    workflow.tick(refresh=True)
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'
    assert workflow.s['elapsed_seconds'] == 12 * 60


def test_window_opens_exactly_at_focus_completion(rig):
    """
    If the schedule shifted such that the window opens at the exact second focus completes:
    Transitions directly from focus completion to AWAITING_ANDROID_SUBMISSION.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    create_window(rig, clock.now + timedelta(minutes=25), ident='win-exact-comp')
    workflow.begin('sess-exact-comp', 'user_lok', 'a55')

    # Study for 20 minutes (focus target)
    step_study(rig, 20 * 60 - 1)
    # Right at the last second, update window start to open at exact completion time
    comp_time = clock.now + timedelta(seconds=1)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?, schedule_version=2", (comp_time.isoformat(),))

    clock.advance(1)
    workflow.tick(refresh=True, monitoring_ok=True)
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'


# ══════════════════════════════════════════════════════════════════════════════════
# 5. Schedule Changes During Active Timer (Requirement 5)
# ══════════════════════════════════════════════════════════════════════════════════

def test_schedule_moves_earlier_adapts_target_and_preserves_buffer(rig):
    """
    If administrator moves window earlier during active session:
    - Recalculates remaining focus safely.
    - Never resets accumulated focus time.
    - Preserves 5-minute buffer where possible.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 0, tzinfo=IST)
    # Window initially 50 min away (target 35m)
    create_window(rig, clock.now + timedelta(minutes=50), ident='win-move-early')
    workflow.begin('sess-move-early', 'user_lok', 'a55')
    assert workflow.s['focus_target_seconds'] == 35 * 60

    # User studies for 10 minutes (600s)
    step_study(rig, 10 * 60)
    assert workflow.s['elapsed_seconds'] == 600.0

    # Admin moves window to 10 minutes from now (total 20 min from session start)
    new_window_start = clock.now + timedelta(minutes=10)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?, schedule_version=2", (new_window_start.isoformat(),))

    workflow.tick(refresh=True)
    # Total time from start = 10m elapsed + 10m remaining = 20m.
    # Focus Duration = 20m - 5m = 15m (900s).
    assert workflow.s['elapsed_seconds'] == 600.0
    assert workflow.s['focus_target_seconds'] == 15 * 60 # 900s
    assert workflow.view()['focus_remaining_seconds'] == 5 * 60 # 300s remaining to study

    # Study the remaining 5 minutes
    step_study(rig, 5 * 60)
    assert workflow.s['focus_completed_at'] == clock.now.isoformat()
    assert workflow.s['state'] == 'AWAITING_PROOF_WINDOW'
    # Exactly 5-minute buffer remaining before window opens
    assert workflow.view()['window_remaining_seconds'] == 5 * 60


def test_schedule_moves_later_does_not_unnecessarily_extend_focus(rig):
    """
    If administrator moves window later during active session:
    - Does NOT unnecessarily extend the existing focus duration.
    - Original timer allocation is preserved.
    """
    db, clock, workflow, _ = rig
    clock.now = datetime(2026, 11, 4, 15, 15, tzinfo=IST)
    # Window at 35 minutes away (30 min target)
    create_window(rig, clock.now + timedelta(minutes=35), ident='win-move-late')
    workflow.begin('sess-move-late', 'user_lok', 'a55')
    assert workflow.s['focus_target_seconds'] == 30 * 60
    assert workflow.s['initial_focus_target_seconds'] == 30 * 60

    # User studies for 10 minutes
    step_study(rig, 10 * 60)

    # Admin moves window 60 minutes later (to 16:50)
    with db._connect() as c:
        c.execute("UPDATE scheduled_activities SET window_start=?, schedule_version=2", ((clock.now + timedelta(minutes=60)).isoformat(),))

    workflow.tick(refresh=True)
    # Must NOT extend existing focus duration to 35m or 40m!
    assert workflow.s['focus_target_seconds'] == 30 * 60
    assert workflow.s['initial_focus_target_seconds'] == 30 * 60


def test_schedule_change_does_not_modify_database_schedule(rig):
    """Recalculation must never modify the authoritative submission schedule in the DB."""
    db, clock, workflow, _ = rig
    target_dt = clock.now + timedelta(minutes=35)
    create_window(rig, target_dt, ident='win-readonly')
    workflow.begin('sess-readonly', 'user_lok', 'a55')

    workflow.tick(refresh=True)
    with db._connect() as c:
        row = c.execute("SELECT window_start FROM scheduled_activities WHERE activity_id='win-readonly'").fetchone()
        assert row[0] == target_dt.isoformat()


# ══════════════════════════════════════════════════════════════════════════════════
# 6. Windows Clock Tampering Immunity (Monotonic Timer) (Requirement 5)
# ══════════════════════════════════════════════════════════════════════════════════

def test_windows_clock_changes_do_not_manipulate_focus_duration(rig):
    """
    Simulate user or OS altering Windows system clock during active study.
    Monotonic clock ensures elapsed study time only credits genuine observed time.
    """
    db, clock, workflow, _ = rig
    create_window(rig, clock.now + timedelta(minutes=35), ident='win-clock-tamper')
    workflow.begin('sess-tamper', 'user_lok', 'a55')
    assert workflow.s['focus_target_seconds'] == 30 * 60

    # Study genuinely for 5 minutes (300s monotonic)
    step_study(rig, 300)
    assert workflow.s['elapsed_seconds'] == 300.0

    # User changes Windows wall clock 2 hours into the future!
    clock.jump_wall_only(7200) # Wall clock moves +2h, monotonic does NOT move
    workflow.tick(monitoring_ok=True)

    # Elapsed focus MUST NOT jump! It must remain exactly 300s!
    assert workflow.s['elapsed_seconds'] == 300.0
    assert not workflow.s.get('focus_completed_at')

    # User changes Windows wall clock 5 hours backwards!
    clock.jump_wall_only(-18000)
    workflow.tick(monitoring_ok=True)
    assert workflow.s['elapsed_seconds'] == 300.0


# ══════════════════════════════════════════════════════════════════════════════════
# 7. EXE Restart & Persistence (Requirement 5 & 7)
# ══════════════════════════════════════════════════════════════════════════════════

def test_exe_restart_persists_start_time_and_timer_allocation(rig):
    """
    Original session start timestamp and timer allocation are persisted in SQLite
    and recovered safely upon EXE restart.
    """
    db, clock, workflow, _ = rig
    start_iso = clock.now.isoformat()
    create_window(rig, clock.now + timedelta(minutes=25), ident='win-persist')
    workflow.begin('sess-persist-1', 'user_lok', 'a55')
    assert workflow.s['focus_target_seconds'] == 20 * 60
    assert workflow.s['initial_focus_target_seconds'] == 20 * 60

    # Complete focus
    step_study(rig, 20 * 60)
    assert workflow.s['focus_completed_at'] is not None

    # Simulate EXE restart by creating fresh StrictStudyWorkflow and recovering
    recovered = StrictStudyWorkflow(db, clock=clock.wall, monotonic=clock.monotonic)
    recovered.recover('user_lok')

    assert recovered.s['session_id'] == 'sess-persist-1'
    assert recovered.s['started_at'] == start_iso
    assert recovered.s['initial_focus_target_seconds'] == 20 * 60
    assert recovered.s['focus_target_seconds'] == 20 * 60
    assert recovered.s['focus_completed_at'] is not None


# ══════════════════════════════════════════════════════════════════════════════════
# 8. Android Proof Upload Delay, Network Recovery & End Session Confirmation (Req 6)
# ══════════════════════════════════════════════════════════════════════════════════

def test_android_proof_delay_network_recovery_and_manual_end_session(rig):
    """
    Preserve Existing Completion Workflow:
    1. Wait for actual submission window.
    2. Android proof upload delayed.
    3. Synchronize proof when delivered.
    4. Enable End Session.
    5. Manual End Session confirmation required.
    6. Session finalized & YouTube reward eligible.
    """
    db, clock, workflow, _ = rig
    create_window(rig, clock.now + timedelta(minutes=25), ident='win-workflow-e2e')
    workflow.begin('sess-e2e', 'user_lok', 'a55')

    # Study 20 minutes -> focus completes
    step_study(rig, 20 * 60)
    assert workflow.s['state'] == 'AWAITING_PROOF_WINDOW'

    # Wait 5 minutes buffer -> window opens
    step_study(rig, 5 * 60)
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'
    assert not workflow.view()['end_enabled']

    # Android proof upload is delayed for 10 minutes (network outage or slow user)
    step_study(rig, 10 * 60)
    assert workflow.s['state'] == 'AWAITING_ANDROID_SUBMISSION'
    assert not workflow.view()['end_enabled']
    # Study timer completion must NEVER auto-complete the overall session!
    assert workflow.s['state'] != 'SESSION_FINALIZED'

    # Network recovers and Android proof is submitted
    submit_android_proof(rig, activity_id='win-workflow-e2e', submission_id='sub-e2e-ok')
    workflow.tick(refresh=True)

    # Synchronized and End Session enabled
    assert workflow.s['sync_verified']
    assert workflow.s['state'] == 'END_SESSION_ENABLED'
    assert workflow.view()['end_enabled']
    assert not workflow.s['manually_completed']

    # End Session requires manual confirmation
    workflow.complete('sess-e2e')
    assert workflow.s['state'] == 'SESSION_FINALIZED'
    assert workflow.s['manually_completed']
    assert workflow.s['reward_status'] == 'PENDING_EXISTING_ELIGIBILITY'
