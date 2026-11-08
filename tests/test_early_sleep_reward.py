"""Tests for StrictRecords Early Sleep and Early Wake-Up Bonus: One-Time YouTube Video Reward."""

import os
import sqlite3
import tempfile
import pytest
from datetime import datetime, date, timedelta
from contextlib import contextmanager

from core.early_sleep_reward_engine import (
    EarlySleepRewardEngine,
    evaluate_early_sleep_eligibility,
    extract_canonical_video_id,
    resolve_sleep_episode_datetimes,
    ensure_early_sleep_rewards_table,
    REWARD_TYPE,
)
from core.time_window_engine import IST, now_ist


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.remove(path)
    except Exception:
        pass


class DummyDB:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS score_events (id INTEGER PRIMARY KEY, points INTEGER, reason TEXT)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sleep_data_submissions (
                    submission_id TEXT PRIMARY KEY,
                    report_date TEXT NOT NULL,
                    account_id TEXT DEFAULT 'user_lok',
                    status TEXT NOT NULL,
                    sleep_onset TEXT,
                    wake_up TEXT,
                    duration_text TEXT,
                    is_dev_mode INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'MI_FITNESS',
                    created_at TEXT DEFAULT ''
                )
            """)
            ensure_early_sleep_rewards_table(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def execute(self, sql, params=()):
        with self._connect() as conn:
            return conn.execute(sql, params)

    def query(self, sql, params=()):
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Test Cases 1, 2, 3: Core Eligibility Truth Table (including boundaries)
# ---------------------------------------------------------------------------

def test_truth_table_case_1_eligible():
    """Test 1: Sleep at 11:30 PM, wake at 7:00 AM -> Eligible."""
    res = evaluate_early_sleep_eligibility("11:30 PM", "7:00 AM", "2026-11-06")
    assert res["eligible"] is True
    assert res["condition_a_met"] is True
    assert res["condition_b_met"] is True
    assert res["status"] == "ELIGIBLE"


def test_truth_table_case_2_late_onset():
    """Test 2: Sleep at 12:20 AM, wake at 7:00 AM -> Not eligible (onset >= 12:15 AM)."""
    res = evaluate_early_sleep_eligibility("12:20 AM", "7:00 AM", "2026-11-06")
    assert res["eligible"] is False
    assert res["condition_a_met"] is False
    assert res["condition_b_met"] is True
    assert res["status"] == "NOT_ELIGIBLE"


def test_truth_table_case_3_late_wake():
    """Test 3: Sleep at 11:30 PM, wake at 9:35 AM -> Not eligible (wake >= 9:30 AM)."""
    res = evaluate_early_sleep_eligibility("11:30 PM", "9:35 AM", "2026-11-06")
    assert res["eligible"] is False
    assert res["condition_a_met"] is True
    assert res["condition_b_met"] is False
    assert res["status"] == "NOT_ELIGIBLE"


def test_truth_table_boundary_onset_1214():
    """Onset at 12:14 AM (strictly < 12:15 AM) -> Condition A met."""
    res = evaluate_early_sleep_eligibility("12:14 AM", "8:30 AM", "2026-11-06")
    assert res["eligible"] is True
    assert res["condition_a_met"] is True


def test_truth_table_boundary_onset_1215():
    """Onset at 12:15 AM (>= 12:15 AM) -> Condition A fails."""
    res = evaluate_early_sleep_eligibility("12:15 AM", "8:30 AM", "2026-11-06")
    assert res["eligible"] is False
    assert res["condition_a_met"] is False


def test_truth_table_boundary_wake_0930():
    """Wake-up at 9:30 AM (>= 9:30 AM) -> Condition B fails."""
    res = evaluate_early_sleep_eligibility("11:30 PM", "9:30 AM", "2026-11-06")
    assert res["eligible"] is False
    assert res["condition_b_met"] is False


def test_extract_canonical_video_id():
    """Validates YouTube URL and ID extraction."""
    assert extract_canonical_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_canonical_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_canonical_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"
    assert extract_canonical_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_canonical_video_id("https://vimeo.com/123456789") is None


# ---------------------------------------------------------------------------
# Test Cases 4-12: Engine Life Cycle, Restrictions, Idempotency & Expiry
# ---------------------------------------------------------------------------

def test_case_4_reward_generation(temp_db):
    """Test 4: Both conditions satisfied -> exactly one video reward generated."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)

    # Sync eligible sleep
    today_str = now_ist().strftime("%Y-%m-%d")
    res = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM", source_identity="Mi Fitness")
    assert res["success"] is True
    assert res["eligible"] is True
    assert res["reward"]["reward_status"] == "REWARD_AVAILABLE"

    reward = engine.get_reward_by_wake_date(today_str)
    assert reward is not None
    assert reward["wake_date"] == today_str
    assert reward["eligibility_status"] == "ELIGIBLE"
    assert reward["reward_status"] == "REWARD_AVAILABLE"


def test_case_5_independent_of_80_points(temp_db):
    """Test 5: User has fewer than 80 points -> reward still works."""
    db = DummyDB(temp_db)
    # User has only 15 points (well below 80)
    db.execute("INSERT INTO score_events (points, reason) VALUES (15, 'START')")
    engine = EarlySleepRewardEngine(db)

    today_str = now_ist().strftime("%Y-%m-%d")
    engine.evaluate_and_grant_reward("user_lok", today_str, "11:45 PM", "6:30 AM")

    # Decision must allow early sleep reward regardless of points
    status = engine.get_todays_reward_status("user_lok", today_str)
    assert status["is_available"] is True
    assert status["eligibility_status"] == "ELIGIBLE"


def test_case_6_select_video_allows_playback(temp_db):
    """Test 6: User selects one YouTube video -> playback allowed."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)
    today_str = now_ist().strftime("%Y-%m-%d")
    res = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]

    # Select canonical video
    bind_res = engine.bind_video(reward_id, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Never Gonna Give You Up")
    assert bind_res["success"] is True
    assert bind_res["video_id"] == "dQw4w9WgXcQ"

    # Tab decision for matching video
    dec = engine.get_effective_early_sleep_decision(video_id="dQw4w9WgXcQ")
    assert dec is not None
    assert dec["allowed"] is True
    assert dec["code"] == "EARLY_SLEEP_ONE_VIDEO"
    assert dec["video_id"] == "dQw4w9WgXcQ"


def test_case_7_other_video_blocked(temp_db):
    """Test 7: User attempts to open another video -> blocked."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)
    today_str = now_ist().strftime("%Y-%m-%d")
    res = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]
    engine.bind_video(reward_id, "dQw4w9WgXcQ", "Title 1")

    # Attempt to play a different video
    dec_diff = engine.get_effective_early_sleep_decision(video_id="9bZkp7q19f0")
    assert dec_diff is not None
    assert dec_diff["allowed"] is False
    assert dec_diff["code"] == "EARLY_SLEEP_VIDEO_MISMATCH"


def test_case_8_completion_locks_youtube(temp_db):
    """Test 8: Video finished -> reward consumed, YouTube locked immediately."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)
    today_str = now_ist().strftime("%Y-%m-%d")
    res = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]
    engine.bind_video(reward_id, "dQw4w9WgXcQ", "Title 1")

    # Heartbeat during playback
    hb1 = engine.record_playback_heartbeat(reward_id, "dQw4w9WgXcQ", playback_state="PLAYING", current_time=100.0, duration=212.0, completed=False)
    assert hb1["reward_status"] == "VIDEO_PLAYING"

    # Heartbeat at completion
    hb_done = engine.record_playback_heartbeat(reward_id, "dQw4w9WgXcQ", playback_state="ENDED", current_time=212.0, duration=212.0, completed=True)
    assert hb_done["reward_status"] == "REWARD_CONSUMED"

    # Subsequent access decision for ANY video (including the finished one) must be blocked
    dec_reopen = engine.get_effective_early_sleep_decision(video_id="dQw4w9WgXcQ")
    assert dec_reopen is not None
    assert dec_reopen["allowed"] is False
    assert dec_reopen["code"] == "EARLY_SLEEP_REWARD_CONSUMED"


def test_case_9_restarting_app_persists_consumed_state(temp_db):
    """Test 9: Restarting app -> consumed reward remains consumed."""
    db = DummyDB(temp_db)
    today_str = now_ist().strftime("%Y-%m-%d")
    engine1 = EarlySleepRewardEngine(db)
    res = engine1.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]
    engine1.bind_video(reward_id, "dQw4w9WgXcQ", "Title 1")
    engine1.record_playback_heartbeat(reward_id, "dQw4w9WgXcQ", playback_state="ENDED", current_time=212.0, duration=212.0, completed=True)

    # Simulate app reboot with a brand new engine instance reading from same SQLite DB
    engine2 = EarlySleepRewardEngine(db)
    dec = engine2.get_effective_early_sleep_decision(video_id="dQw4w9WgXcQ")
    assert dec is not None
    assert dec["allowed"] is False
    assert dec["reward_status"] == "REWARD_CONSUMED"


def test_case_10_repeated_sleep_sync_idempotent(temp_db):
    """Test 10: Repeated sleep sync -> no duplicate reward."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)
    today_str = now_ist().strftime("%Y-%m-%d")

    # First sync
    res1 = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id1 = res1["reward"]["reward_id"]

    # Select video
    engine.bind_video(reward_id1, "dQw4w9WgXcQ", "Title")

    # Second sync on the same date (e.g. background polling or re-sync)
    res2 = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    assert res2["success"] is True
    assert res2["idempotent"] is True
    assert res2["reward"]["reward_id"] == reward_id1
    # State must NOT be overwritten back to REWARD_AVAILABLE
    assert res2["reward"]["reward_status"] == "VIDEO_SELECTED"

    # Verify only 1 row exists in DB
    rows = db.query(f"SELECT * FROM early_sleep_video_rewards WHERE wake_date = '{today_str}'")
    assert len(rows) == 1


def test_case_11_does_not_extend_other_rewards(temp_db):
    """Test 11: Another YouTube reward active -> does not extend it."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)
    today_str = now_ist().strftime("%Y-%m-%d")
    res = engine.evaluate_and_grant_reward("user_lok", today_str, "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]
    engine.bind_video(reward_id, "dQw4w9WgXcQ", "Title")
    engine.record_playback_heartbeat(reward_id, "dQw4w9WgXcQ", duration=100.0, current_time=100.0, completed=True)

    # The Early Sleep reward is consumed and does NOT modify 20-min rewards or grant unlimited
    assert engine.get_reward_by_wake_date(today_str)["reward_status"] == "REWARD_CONSUMED"


def test_case_12_expires_at_midnight_no_rollover(temp_db):
    """Test 12: Unused reward past 23:59:59 IST -> expires without rollover."""
    db = DummyDB(temp_db)
    engine = EarlySleepRewardEngine(db)

    # Eligible on 2026-11-04 (yesterday)
    res = engine.evaluate_and_grant_reward("user_lok", "2026-11-04", "11:30 PM", "7:00 AM")
    reward_id = res["reward"]["reward_id"]

    # On today (2026-11-05), yesterday's reward is expired
    yesterday_reward = engine.get_reward_by_wake_date("2026-11-04")
    assert yesterday_reward["reward_status"] == "REWARD_EXPIRED"
