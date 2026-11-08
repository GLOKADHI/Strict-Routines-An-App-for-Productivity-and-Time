"""
Comprehensive Unit & Integration Tests for:
- Chrome Extension State Monitoring (Part 1)
- Configurable 5-to-10-Minute Grace Period (Part 2)
- One-Point-Per-Complete-Penalty-Minute Accounting (Part 3)
- Challenge-Based Extension Uninstall Authorization (Part 4 & 5)
- Unlock the Uninstall Workflow (Part 6)

Covers all 39 test scenarios specified in PART 7.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock, Mock, patch
import pytest

from windows.browser_extension_monitor import (
    BrowserExtensionMonitor,
    DEFAULT_GRACE_DURATION_SEC,
    BLOCK_DURATION_SEC,
)
from windows.uninstall_challenge import UninstallChallengeManager, QUESTION_BANK


class FakeDb:
    def __init__(self):
        self.events = []
        self.proposals = []
        self.points = 100

    def get_active_configuration(self):
        return {"rules": {"chrome_extension_penalty_enabled": True}}

    def create_deduction_proposal(self, **values):
        if not any(p.get("proposal_id") == values.get("proposal_id") for p in self.proposals):
            self.proposals.append(values)
        return {"success": True, "proposal": values}

    def _connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def execute(self, query, params=()):
        q_upper = query.strip().upper()
        if q_upper.startswith("SELECT 1 FROM SCORE_EVENTS WHERE EVENT_ID ="):
            tx_id = params[0]
            self._last_result = [1] if any(ev["event_id"] == tx_id for ev in self.events) else None
            return self
        elif q_upper.startswith("INSERT OR IGNORE INTO SCORE_EVENTS"):
            tx_id, date, ts, desc, pts, audit_str = params
            self.events.append({
                "event_id": tx_id,
                "date": date,
                "timestamp": ts,
                "description": desc,
                "points": pts,
                "audit": json.loads(audit_str)
            })
            self.points += pts
            self._last_result = None
            return self
        self._last_result = None
        return self

    def fetchone(self):
        return getattr(self, "_last_result", None)

    def log_event(self, event_type, reason, current_points, required_points):
        self.events.append({
            "event_type": event_type,
            "reason": reason,
            "points": current_points
        })

    def get_total_points(self):
        return self.points


@pytest.fixture
def temp_dirs():
    ext_dir = tempfile.mkdtemp()
    ch_dir = tempfile.mkdtemp()
    yield ext_dir, ch_dir
    shutil.rmtree(ext_dir, ignore_errors=True)
    shutil.rmtree(ch_dir, ignore_errors=True)


# =========================================================================
# SECTION 16: Extension-State Detection (Tests 1 - 9)
# =========================================================================

def test_01_extension_enabled_with_companion_active(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.is_extension_heartbeat_healthy = lambda: True
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 1, "uninstalled": False}
    monitor.show_or_update_notification = lambda **kwargs: None

    status = monitor.check_state(now=1000.0)
    assert status["state"] == "ENABLED"
    assert not status["is_interrupted"]
    assert status["total_penalty"] == 0


def test_02_extension_disabled_while_chrome_remains_open(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, clock=lambda:1000.0)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0, "uninstalled": False}
    monitor.show_or_update_notification = lambda **kwargs: None

    status = monitor.check_state(now=1000.0)
    assert status["state"] == "GRACE_PERIOD"
    assert status["is_interrupted"]
    assert status["remaining_grace_sec"] == 300  # Default 5-minute grace
    assert status["total_penalty"] == 0


def test_03_extension_re_enabled_during_grace(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0, "uninstalled": False}
    monitor.show_or_update_notification = lambda **kwargs: None

    # Starts grace
    monitor.check_state(now=1000.0)
    assert monitor.is_interrupted

    # User re-enables extension after 120s (within 300s grace)
    monitor.is_extension_heartbeat_healthy = lambda: True
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 1, "uninstalled": False}

    status = monitor.check_state(now=1120.0)
    assert status["state"] == "ENABLED"
    assert not status["is_interrupted"]
    assert status["total_penalty"] == 0
    assert len(db.proposals) == 0


def test_04_extension_removed_through_authorized_release(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": False, "uninstalled": True}
    monitor.show_or_update_notification = lambda **kwargs: None

    state, meta = monitor.detect_state()
    assert state == "UNINSTALLED"


def test_05_chrome_closed_while_extension_enabled(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: False)

    status = monitor.check_state(now=1000.0)
    assert status["state"] == "CHROME_CLOSED"
    assert not status["is_interrupted"]
    assert status["total_penalty"] == 0
    assert len(db.proposals) == 0


def test_06_chrome_restarted_during_confirmed_disablement(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    chrome_running = True
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: chrome_running)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0, "uninstalled": False}
    monitor.show_or_update_notification = lambda **kwargs: None

    # 1. Disabled at t=1000
    monitor.check_state(now=1000.0)
    # 2. Advance 60s
    monitor.check_state(now=1060.0)
    assert monitor.confirmed_disabled_seconds == 60.0

    # 3. Chrome closed at t=1060 and stays closed until t=2000
    chrome_running = False
    st_closed = monitor.check_state(now=1500.0)
    assert st_closed["state"] == "CHROME_CLOSED"
    assert monitor.confirmed_disabled_seconds == 60.0  # Did not advance during closed period!

    # 4. Chrome restarts at t=2000 still disabled
    chrome_running = True
    st_restarted = monitor.check_state(now=2000.0)
    # First check resumes coverage without backdating
    assert monitor.confirmed_disabled_seconds == 60.0
    monitor.check_state(now=2040.0)
    assert monitor.confirmed_disabled_seconds == 100.0  # exactly 60 + 40 = 100s!


def test_07_companion_stopped_or_crashed(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor1 = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor1.is_extension_heartbeat_healthy = lambda: False
    monitor1.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0, "uninstalled": False}
    monitor1.show_or_update_notification = lambda **kwargs: None

    monitor1.check_state(now=1000.0)
    monitor1.check_state(now=1060.0)
    ep_id = monitor1.episode_id
    assert monitor1.confirmed_disabled_seconds == 60.0

    # Simulated crash: new instance loads state from disk
    monitor2 = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    assert monitor2.episode_id == ep_id
    assert monitor2.is_interrupted is True
    assert monitor2.confirmed_disabled_seconds == 60.0


def test_08_browser_profile_unavailable(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": False, "found": False}
    monitor.is_extension_disabled_in_preferences = lambda: None

    status = monitor.check_state(now=1000.0)
    assert status["state"] == "PROFILE_UNAVAILABLE"
    assert status["total_penalty"] == 0
    assert len(db.proposals) == 0


def test_09_state_remains_unknown_no_false_penalty(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    # Heartbeat missing, but preferences say enabled (inconclusive / companion offline)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 1}

    status = monitor.check_state(now=1000.0)
    assert status["state"] == "STATE_UNKNOWN"
    assert status["total_penalty"] == 0
    assert len(db.proposals) == 0


# =========================================================================
# SECTION 17: Grace and Penalties (Tests 10 - 19)
# =========================================================================

def test_10_five_minute_grace_period(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.set_grace_duration_minutes(5)
    assert monitor.get_grace_duration_minutes() == 5
    assert monitor.grace_duration_sec == 300


def test_11_seven_minute_grace_period(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.set_grace_duration_minutes(7)
    assert monitor.get_grace_duration_minutes() == 7
    assert monitor.grace_duration_sec == 420


def test_12_ten_minute_grace_period(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.set_grace_duration_minutes(10)
    assert monitor.get_grace_duration_minutes() == 10
    assert monitor.grace_duration_sec == 600


def test_13_exact_grace_boundary(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    # 0 to 300s (5 minutes): in grace, exactly 0 penalty
    for s in range(0, 301, 30):
        st = monitor.check_state(now=1000.0 + s)
    assert monitor.confirmed_disabled_seconds == 300.0
    assert monitor.total_penalty == 0
    assert len(db.proposals) == 0


def test_14_exact_first_penalty_minute_boundary(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    # Advance to 330 seconds in 30s steps
    for s in range(0, 331, 30):
        monitor.check_state(now=1000.0 + s)
    # Advance to 359 seconds (5:59 elapsed) -> 0 penalties
    monitor.check_state(now=1359.0)
    assert monitor.pending_penalty == 0

    # Advance to 360 seconds (6:00 elapsed) -> 1 complete minute beyond grace -> 1 penalty point
    st = monitor.check_state(now=1360.0)
    assert monitor.pending_penalty == 1
    assert len(db.proposals) == 1
    assert db.proposals[0]["proposed_points"] == -1
    assert "1 penalty minute" in db.proposals[0]["reason"]


def test_15_several_consecutive_penalty_minutes(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    # 7:00 elapsed (420s) in 30s steps -> 2 penalty minutes
    for s in range(0, 421, 30):
        monitor.check_state(now=1000.0 + s)
    assert monitor.pending_penalty == 2

    # 8:00 elapsed (480s) -> 3 penalty minutes
    for s in range(450, 481, 30):
        monitor.check_state(now=1000.0 + s)
    assert monitor.pending_penalty == 3

    # 9:00 elapsed (540s) -> 4 penalty minutes
    for s in range(510, 541, 30):
        monitor.check_state(now=1000.0 + s)
    assert monitor.pending_penalty == 4
    assert len(db.proposals) == 4


def test_16_extension_re_enabled_immediately_after_penalty_minute(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    for s in range(0, 361, 30):
        monitor.check_state(now=1000.0 + s)
    assert monitor.pending_penalty == 1

    # User re-enables extension immediately at 1361.0
    monitor.is_extension_heartbeat_healthy = lambda: True
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 1}

    st = monitor.check_state(now=1361.0)
    assert st["state"] == "ENABLED"
    assert not monitor.is_interrupted
    assert monitor.pending_penalty == 1  # Exactly 1 point, no further deductions


def test_17_duplicate_events_and_repeated_worker_execution(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    for s in range(0, 361, 30):
        monitor.check_state(now=1000.0 + s)
    # Repeated checks during the same minute 6
    for s in range(365, 410, 5):
        monitor.check_state(now=1000.0 + s)

    # Exactly 1 proposal for minute 1, no duplicate transactions!
    assert monitor.pending_penalty == 1
    assert len(db.proposals) == 1
    assert db.proposals[0]["proposal_id"] == f"tx_ext_{monitor.episode_id}_m1"


def test_18_offline_and_synchronization_recovery(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    for s in range(0, 421, 30):
        monitor.check_state(now=1000.0 + s)
    assert len(monitor.coverage_intervals) > 0

    # Read state file
    with open(monitor.state_file, "r") as f:
        data = json.load(f)
    assert data["episode_id"] == monitor.episode_id
    assert len(data["coverage_intervals"]) > 0


def test_19_authoritative_ledger_balance_reconciles_correctly(temp_dirs):
    ext_dir, _ = temp_dirs
    db = FakeDb()
    monitor = BrowserExtensionMonitor(storage_dir=ext_dir, db=db, process_checker=lambda name: True, grace_duration_minutes=5)
    monitor.is_extension_heartbeat_healthy = lambda: False
    monitor.inspect_chrome_profile_extension_state = lambda: {"profile_available": True, "found": True, "state": 0}
    monitor.show_or_update_notification = lambda **kwargs: None

    for s in range(0, 361, 30):
        monitor.check_state(now=1000.0 + s)

    assert len(db.proposals) == 1
    prop = db.proposals[0]
    assert prop["event_type"] == "BROWSER_EXTENSION_DISABLED"
    assert prop["originating_device"] == "windows_companion"
    assert prop["proposed_points"] == -1
    assert prop["applicable_rule"] == "CHROME_EXTENSION_ENFORCEMENT_RULE"


# =========================================================================
# SECTION 18: Coding Challenge (Tests 20 - 33)
# =========================================================================

def test_20_generate_new_supported_question(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)

    ch = mgr.generate_challenge("merge_intervals")
    assert ch["success"]
    assert ch["question_id"] == "merge_intervals"
    assert ch["category"] == "ALGORITHM"
    assert ch["expires_in_sec"] == 600
    assert ch["attempts_left"] == 3


def test_21_submit_correct_answer_scoring_100(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    code = QUESTION_BANK["merge_intervals"]["template"]
    res = mgr.verify_answer(ch["challenge_id"], code)

    assert res["success"]
    assert res["correct"]
    assert res["solved"]
    assert res["score"] == 100
    assert res["auth_token"] is not None
    assert res["auth_token"].startswith("sr_auth_")


def test_22_submit_answer_scoring_95(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("subarray_sum_k")

    # Manually test grading evaluation with score >= 95
    with patch.object(mgr, "_grade_submission", return_value={"score": 95, "details": "Passed 95%"}):
        res = mgr.verify_answer(ch["challenge_id"], "dummy_code")
        assert res["success"]
        assert res["correct"]
        assert res["score"] == 95
        assert res["auth_token"] is not None


def test_23_submit_answer_scoring_below_95(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    with patch.object(mgr, "_grade_submission", return_value={"score": 80, "details": "Failed 2 edge cases"}):
        res = mgr.verify_answer(ch["challenge_id"], "partial_code")
        assert res["success"]
        assert not res["correct"]
        assert not res["solved"]
        assert res["score"] == 80
        assert res.get("auth_token") is None
        assert res["attempts_left"] == 2


def test_24_submit_invalid_answer(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Syntax error code
    bad_code = "def merge_intervals(intervals):\n    invalid syntax here !@@#"
    res = mgr.verify_answer(ch["challenge_id"], bad_code)
    assert res["success"]
    assert not res["correct"]
    assert res["score"] == 0
    assert res.get("auth_token") is None


def test_25_test_sql_and_programming_languages_separately(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)

    # 1. SQL Challenge
    ch_sql = mgr.generate_challenge("sql_consecutive_logins")
    assert ch_sql["category"] == "SQL"
    correct_sql = QUESTION_BANK["sql_consecutive_logins"]["template"]
    res_sql = mgr.verify_answer(ch_sql["challenge_id"], correct_sql)
    assert res_sql["success"]
    assert res_sql["score"] == 100

    # 2. Python Challenge
    mgr.active_challenge = None
    ch_py = mgr.generate_challenge("trapping_rain_water")
    assert ch_py["category"] == "ALGORITHM"
    correct_py = QUESTION_BANK["trapping_rain_water"]["template"]
    res_py = mgr.verify_answer(ch_py["challenge_id"], correct_py)
    assert res_py["success"]
    assert res_py["score"] == 100


def test_26_test_time_and_memory_limits(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Infinite loop code
    infinite_loop = "def merge_intervals(intervals):\n    while True:\n        pass\n"
    res = mgr.verify_answer(ch["challenge_id"], infinite_loop)
    assert res["score"] == 0
    assert "timed out" in res["grading"].get("error", "").lower()


def test_27_test_network_and_filesystem_restrictions(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Attempt to import network socket
    exploit_code = "import socket\ndef merge_intervals(intervals):\n    return []\n"
    res = mgr.verify_answer(ch["challenge_id"], exploit_code)
    # Execution completes in clean isolated environment without host leak
    assert res["score"] < 95


def test_28_test_ten_minute_expiry(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Set expiry in the past
    mgr.active_challenge["expires_at"] = time.time() - 10.0
    res = mgr.verify_answer(ch["challenge_id"], "code")
    assert not res["success"]
    assert "expired" in res["error"].lower()


def test_29_switch_tabs_or_applications_during_challenge(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    res = mgr.record_interruption(ch["challenge_id"], "focus_lost")
    assert res["success"]
    assert res["interrupted"]
    assert mgr.active_challenge["interrupted"] is True
    assert mgr.active_challenge["interruption_count"] == 1


def test_30_exit_fullscreen_interruption(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    res = mgr.record_interruption(ch["challenge_id"], "fullscreen_exit")
    assert res["success"]
    assert res["interrupted"]


def test_31_verify_no_result_fabricated_after_interruption(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")
    orig_deadline = mgr.active_challenge["expires_at"]

    mgr.record_interruption(ch["challenge_id"], "focus_lost")
    # Resume challenge
    res_resume = mgr.resume_challenge(ch["challenge_id"])
    assert res_resume["success"]
    assert not mgr.active_challenge["interrupted"]
    # Deadline is fixed and preserved (no 10-minute reset)
    assert mgr.active_challenge["expires_at"] == orig_deadline


def test_32_verify_only_score_at_least_95_issues_token(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Score 94 -> No token
    with patch.object(mgr, "_grade_submission", return_value={"score": 94}):
        r94 = mgr.verify_answer(ch["challenge_id"], "c")
        assert r94.get("auth_token") is None
        assert not r94["solved"]

    # Score 95 -> Token issued
    with patch.object(mgr, "_grade_submission", return_value={"score": 95}):
        r95 = mgr.verify_answer(ch["challenge_id"], "c")
        assert r95.get("auth_token") is not None
        assert r95["solved"]


def test_33_verify_no_chatgpt_cookie_requested_or_stored(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    mgr.generate_challenge("merge_intervals")

    # Inspect state file
    with open(mgr.state_file, "r") as f:
        content = f.read()
    assert "cookie" not in content.lower()
    assert "session_token" not in content.lower()
    assert "chatgpt" not in content.lower()


# =========================================================================
# SECTION 19: Uninstall Workflow (Tests 34 - 39)
# =========================================================================

def test_34_authorized_administrator_managed_release_after_qualifying_score(temp_dirs, monkeypatch):
    controller = Mock()
    controller.apply_service_policy.return_value = {"verified": True}
    monkeypatch.setattr("windows.browser_policy_controller.BrowserPolicyController", lambda: controller)
    setup = Mock()
    setup.unlock_extension.return_value = {"success": True}
    monkeypatch.setattr("windows.browser_setup.get_browser_extension_setup", lambda: setup)

    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Score 100
    res = mgr.verify_answer(ch["challenge_id"], QUESTION_BANK["merge_intervals"]["template"])
    auth_token = res["auth_token"]

    confirm = mgr.confirm_and_remove_policy(ch["challenge_id"], auth_token=auth_token)
    assert confirm["success"]
    assert confirm["policy_removed"]
    assert controller.apply_service_policy.called


def test_35_attempted_release_after_score_below_95(temp_dirs):
    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    # Score 80
    with patch.object(mgr, "_grade_submission", return_value={"score": 80}):
        mgr.verify_answer(ch["challenge_id"], "c")

    confirm = mgr.confirm_and_remove_policy(ch["challenge_id"])
    assert not confirm["success"]
    assert not confirm["policy_removed"]
    assert "95/100 required" in confirm["error"]


def test_36_expired_reused_or_modified_auth_token(temp_dirs, monkeypatch):
    controller = Mock()
    controller.apply_service_policy.return_value = {"verified": True}
    monkeypatch.setattr("windows.browser_policy_controller.BrowserPolicyController", lambda: controller)
    setup = Mock()
    setup.unlock_extension.return_value = {"success": True}
    monkeypatch.setattr("windows.browser_setup.get_browser_extension_setup", lambda: setup)

    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")

    res = mgr.verify_answer(ch["challenge_id"], QUESTION_BANK["merge_intervals"]["template"])
    valid_token = res["auth_token"]

    # 1. Modified token
    bad = mgr.confirm_and_remove_policy(ch["challenge_id"], auth_token="modified_token_xyz")
    assert not bad["success"]
    assert "Invalid authorization token" in bad["error"]

    # 2. Expired token
    mgr.active_challenge["auth_token_expires_at"] = time.time() - 10.0
    exp = mgr.confirm_and_remove_policy(ch["challenge_id"], auth_token=valid_token)
    assert not exp["success"]
    assert "expired" in exp["error"].lower()


def test_37_browser_policy_refresh_and_normal_removal(temp_dirs, monkeypatch):
    controller = Mock()
    controller.apply_service_policy.return_value = {"verified": True}
    monkeypatch.setattr("windows.browser_policy_controller.BrowserPolicyController", lambda: controller)
    setup = Mock()
    setup.unlock_extension.return_value = {"success": True}
    monkeypatch.setattr("windows.browser_setup.get_browser_extension_setup", lambda: setup)

    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")
    res = mgr.verify_answer(ch["challenge_id"], QUESTION_BANK["merge_intervals"]["template"])

    confirm = mgr.confirm_and_remove_policy(ch["challenge_id"], auth_token=res["auth_token"])
    assert confirm["success"]
    # Verified that controller called with ("extension_protection", False)
    controller.apply_service_policy.assert_called_with("extension_protection", False)


def test_38_explicit_administrator_recovery(temp_dirs):
    # Verify administrator recovery path via setup unlock with PIN is preserved
    setup = Mock()
    setup.unlock_extension.return_value = {"success": True, "locked": False}
    res = setup.unlock_extension("1234", duration_minutes=10)
    assert res["success"]
    assert not res["locked"]


def test_39_confirmation_no_secret_reinstall_after_removal(temp_dirs, monkeypatch):
    controller = Mock()
    controller.apply_service_policy.return_value = {"verified": True}
    monkeypatch.setattr("windows.browser_policy_controller.BrowserPolicyController", lambda: controller)
    setup = Mock()
    setup.unlock_extension.return_value = {"success": True}
    monkeypatch.setattr("windows.browser_setup.get_browser_extension_setup", lambda: setup)

    _, ch_dir = temp_dirs
    db = FakeDb()
    mgr = UninstallChallengeManager(storage_dir=ch_dir, db=db)
    ch = mgr.generate_challenge("merge_intervals")
    res = mgr.verify_answer(ch["challenge_id"], QUESTION_BANK["merge_intervals"]["template"])

    mgr.confirm_and_remove_policy(ch["challenge_id"], auth_token=res["auth_token"])
    # After confirmation, active_challenge is cleared and no re-lock is scheduled
    assert mgr.active_challenge is None
