"""
Tests for Windows Strict Session Individual Violation Counting Engine:
- UNAUTHORIZED_TAB_SWITCH
- TASK_MANAGER_OPENED
- COMMAND_PROMPT_OPENED

Covers all 8 required scenarios:
1. Switching between ChatGPT and LeetCode panes -> 0 violations.
2. Switching to an unauthorized browser tab -> exactly 1 UNAUTHORIZED_TAB_SWITCH violation.
3. Opening Task Manager -> exactly 1 TASK_MANAGER_OPENED violation.
4. Opening CMD -> exactly 1 COMMAND_PROMPT_OPENED violation.
5. Performing all three actions -> exactly 3 independent violations.
6. Opening Task Manager repeatedly -> distinct count per launch after process exit.
7. Deduplication across multiple detection sources (shortcut + process, same PID polling).
8. Session restart / recovery persists and restores violation ledger and counts from DB.
"""

import os
import tempfile
import time
import pytest
from core.database import StrictRecordsDB
from windows.strict_violation_engine import (
    StrictViolationEngine,
    CATEGORY_TAB_SWITCH,
    CATEGORY_TASK_MANAGER,
    CATEGORY_COMMAND_PROMPT,
    is_allowed_study_url,
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StrictRecordsDB(db_path=path)
    yield db
    try:
        os.remove(path)
    except Exception:
        pass


@pytest.fixture
def engine(temp_db):
    eng = StrictViolationEngine(db=temp_db)
    return eng


def test_scenario_1_allowed_study_navigation_zero_violations(engine):
    """
    Scenario 1:
    Allowed study URLs (ChatGPT, LeetCode, auth/cdn domains) and switching
    between ChatGPT and LeetCode panes produce ZERO violations.
    """
    sid = "sess_study_001"
    engine.reset_session(sid)

    # Allowed URLs
    allowed_urls = [
        "https://chatgpt.com/",
        "https://chatgpt.com/c/12345",
        "https://cdn.oaistatic.com/assets/app.js",
        "https://auth0.openai.com/u/login",
        "https://leetcode.com/",
        "https://leetcode.com/problems/two-sum/",
        "https://assets.leetcode.com/static_assets/public/images/LeetCode_logo.png",
    ]

    for url in allowed_urls:
        assert is_allowed_study_url(url) is True
        rec = engine.record_browser_tab_switch(sid, target_url=url)
        assert rec is None, f"Allowed URL {url} should not generate a violation"

    assert engine.get_total_violations(sid) == 0
    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_TAB_SWITCH] == 0
    assert counts[CATEGORY_TASK_MANAGER] == 0
    assert counts[CATEGORY_COMMAND_PROMPT] == 0
    assert len(engine.get_ledger(sid)) == 0


def test_scenario_2_unauthorized_browser_tab_switch(engine):
    """
    Scenario 2:
    Switching to an unauthorized browser tab generates exactly 1 UNAUTHORIZED_TAB_SWITCH violation.
    """
    sid = "sess_tab_002"
    engine.reset_session(sid)

    unauthorized_url = "https://youtube.com/watch?v=12345"
    assert is_allowed_study_url(unauthorized_url) is False

    rec = engine.record_browser_tab_switch(sid, target_url=unauthorized_url)
    assert rec is not None
    assert rec.category == CATEGORY_TAB_SWITCH
    assert rec.count_before == 0
    assert rec.count_after == 1
    assert rec.session_id == sid

    assert engine.get_total_violations(sid) == 1
    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_TAB_SWITCH] == 1
    assert counts[CATEGORY_TASK_MANAGER] == 0
    assert counts[CATEGORY_COMMAND_PROMPT] == 0

    ledger = engine.get_ledger(sid)
    assert len(ledger) == 1
    assert ledger[0]["category"] == CATEGORY_TAB_SWITCH


def test_scenario_3_task_manager_opened(engine):
    """
    Scenario 3:
    Opening Task Manager generates exactly 1 TASK_MANAGER_OPENED violation.
    """
    sid = "sess_tm_003"
    engine.reset_session(sid)

    # Simulated Task Manager launch with PID 2040
    rec = engine.record_task_manager_process(sid, pid=2040)
    assert rec is not None
    assert rec.category == CATEGORY_TASK_MANAGER
    assert rec.count_before == 0
    assert rec.count_after == 1

    assert engine.get_total_violations(sid) == 1
    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_TASK_MANAGER] == 1
    assert counts[CATEGORY_TAB_SWITCH] == 0
    assert counts[CATEGORY_COMMAND_PROMPT] == 0

    ledger = engine.get_ledger(sid)
    assert len(ledger) == 1
    assert ledger[0]["category"] == CATEGORY_TASK_MANAGER


def test_scenario_4_command_prompt_opened(engine):
    """
    Scenario 4:
    Opening CMD generates exactly 1 COMMAND_PROMPT_OPENED violation.
    """
    sid = "sess_cmd_004"
    engine.reset_session(sid)

    # Simulated CMD launch with PID 3050
    rec = engine.record_command_prompt_process(sid, pid=3050, proc_name="cmd.exe")
    assert rec is not None
    assert rec.category == CATEGORY_COMMAND_PROMPT
    assert rec.count_before == 0
    assert rec.count_after == 1

    assert engine.get_total_violations(sid) == 1
    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_COMMAND_PROMPT] == 1
    assert counts[CATEGORY_TAB_SWITCH] == 0
    assert counts[CATEGORY_TASK_MANAGER] == 0

    ledger = engine.get_ledger(sid)
    assert len(ledger) == 1
    assert ledger[0]["category"] == CATEGORY_COMMAND_PROMPT


def test_scenario_5_all_three_distinct_actions_sum_to_three(engine):
    """
    Scenario 5:
    Performing all three actions (Tab Switch + Task Manager + CMD) in the same session
    must generate exactly 3 independent violations (never multiplied or clustered).
    """
    sid = "sess_all_005"
    engine.reset_session(sid)

    # Action 1: Unauthorized Tab Switch
    rec1 = engine.record_browser_tab_switch(sid, target_url="https://reddit.com/r/all")
    assert rec1 is not None
    assert rec1.category == CATEGORY_TAB_SWITCH
    assert rec1.count_after == 1

    # Action 2: Task Manager launch
    rec2 = engine.record_task_manager_process(sid, pid=4100)
    assert rec2 is not None
    assert rec2.category == CATEGORY_TASK_MANAGER
    assert rec2.count_after == 2

    # Action 3: Command Prompt launch
    rec3 = engine.record_command_prompt_process(sid, pid=5200, proc_name="powershell.exe")
    assert rec3 is not None
    assert rec3.category == CATEGORY_COMMAND_PROMPT
    assert rec3.count_after == 3

    assert engine.get_total_violations(sid) == 3
    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_TAB_SWITCH] == 1
    assert counts[CATEGORY_TASK_MANAGER] == 1
    assert counts[CATEGORY_COMMAND_PROMPT] == 1

    ledger = engine.get_ledger(sid)
    assert len(ledger) == 3
    assert [r["category"] for r in ledger] == [
        CATEGORY_TAB_SWITCH,
        CATEGORY_TASK_MANAGER,
        CATEGORY_COMMAND_PROMPT,
    ]


def test_scenario_6_repeated_task_manager_launches(engine):
    """
    Scenario 6:
    Opening Task Manager, closing it, and opening it again generates a distinct
    count for each launch.
    """
    sid = "sess_relaunch_006"
    engine.reset_session(sid)

    # First launch: PID 6001
    rec1 = engine.record_task_manager_process(sid, pid=6001)
    assert rec1 is not None
    assert rec1.count_after == 1
    assert engine.get_total_violations(sid) == 1

    # Subsequent supervisor ticks while PID 6001 is still running -> 0 new violations
    rec_dup = engine.record_task_manager_process(sid, pid=6001)
    assert rec_dup is None
    assert engine.get_total_violations(sid) == 1

    # Process 6001 closes
    # reconcile_active_processes with mock function where 6001 is no longer alive
    engine.reconcile_active_processes(sid, is_process_alive_fn=lambda p: False)

    # Second launch: new PID 6002
    rec2 = engine.record_task_manager_process(sid, pid=6002)
    assert rec2 is not None
    assert rec2.count_after == 2
    assert engine.get_total_violations(sid) == 2

    # Process 6002 closes
    engine.reconcile_active_processes(sid, is_process_alive_fn=lambda p: False)

    # Third launch: new PID 6003
    rec3 = engine.record_task_manager_process(sid, pid=6003)
    assert rec3 is not None
    assert rec3.count_after == 3
    assert engine.get_total_violations(sid) == 3

    counts = engine.get_category_counts(sid)
    assert counts[CATEGORY_TASK_MANAGER] == 3


def test_scenario_7_deduplication_multiple_detection_sources(engine):
    """
    Scenario 7:
    Multiple detection sources for the same event do NOT double count.
    - Ctrl+Shift+Esc shortcut followed immediately by taskmgr.exe process launch -> 1 violation.
    - Continuous polling of the same active terminal PID -> 1 violation.
    - Rapid redirects to unauthorized URLs within debounce window -> 1 violation.
    """
    sid = "sess_dedup_007"
    engine.reset_session(sid)

    # A. Shortcut + Process Launch correlation
    # 1. User presses Ctrl+Shift+Esc
    rec_sc = engine.record_task_manager_shortcut(sid)
    assert rec_sc is not None
    assert rec_sc.category == CATEGORY_TASK_MANAGER
    assert rec_sc.count_after == 1
    assert engine.get_total_violations(sid) == 1

    # 2. Within 0.5s, taskmgr.exe process spawns (PID 7010)
    rec_proc = engine.record_task_manager_process(sid, pid=7010)
    # Must be correlated to the shortcut -> NO additional violation
    assert rec_proc is None
    assert engine.get_total_violations(sid) == 1

    # 3. Repeated ticks with PID 7010 -> NO additional violation
    assert engine.record_task_manager_process(sid, pid=7010) is None
    assert engine.get_total_violations(sid) == 1

    # B. Continuous polling of same Terminal PID
    rec_term1 = engine.record_command_prompt_process(sid, pid=8020, proc_name="cmd.exe")
    assert rec_term1 is not None
    assert rec_term1.count_after == 2
    assert engine.get_total_violations(sid) == 2

    # Subsequent supervisor checks on the same PID
    for _ in range(5):
        assert engine.record_command_prompt_process(sid, pid=8020, proc_name="cmd.exe") is None
    assert engine.get_total_violations(sid) == 2

    # C. Rapid redirect URL debounce within 2.0s
    rec_tab1 = engine.record_browser_tab_switch(sid, target_url="https://badsite.com")
    assert rec_tab1 is not None
    assert rec_tab1.count_after == 3
    assert engine.get_total_violations(sid) == 3

    # Fast redirect to badsite.com/login within 0.1s
    rec_tab2 = engine.record_browser_tab_switch(sid, target_url="https://badsite.com/login")
    assert rec_tab2 is None
    assert engine.get_total_violations(sid) == 3


def test_scenario_8_persistence_and_recovery_across_restart(temp_db):
    """
    Scenario 8:
    Session restart / recovery correctly restores and persists the violation ledger and counts.
    """
    sid = "sess_persist_008"

    # Engine Instance 1 (Before restart)
    eng1 = StrictViolationEngine(db=temp_db)
    eng1.reset_session(sid)

    eng1.record_browser_tab_switch(sid, target_url="https://netflix.com")
    eng1.record_task_manager_process(sid, pid=9100)
    eng1.record_command_prompt_process(sid, pid=9200, proc_name="powershell.exe")

    assert eng1.get_total_violations(sid) == 3
    counts1 = eng1.get_category_counts(sid)
    ledger1 = eng1.get_ledger(sid)

    # Simulate EXE restart by creating completely new engine instance pointing to same DB
    eng2 = StrictViolationEngine(db=temp_db)

    # Verify counts restored from SQLite database
    assert eng2.get_total_violations(sid) == 3
    counts2 = eng2.get_category_counts(sid)
    assert counts2 == counts1
    assert counts2[CATEGORY_TAB_SWITCH] == 1
    assert counts2[CATEGORY_TASK_MANAGER] == 1
    assert counts2[CATEGORY_COMMAND_PROMPT] == 1

    # Verify full ledger restored
    ledger2 = eng2.get_ledger(sid)
    assert len(ledger2) == 3
    for r1, r2 in zip(ledger1, ledger2):
        assert r1["violation_id"] == r2["violation_id"]
        assert r1["category"] == r2["category"]
        assert r1["count_after"] == r2["count_after"]
        assert r1["timestamp"] == r2["timestamp"]

    # Verify next violation increments from restored count
    rec4 = eng2.record_command_prompt_process(sid, pid=9300, proc_name="cmd.exe")
    assert rec4 is not None
    assert rec4.count_before == 3
    assert rec4.count_after == 4
    assert eng2.get_total_violations(sid) == 4


def test_session_failure_threshold_exceeded(engine):
    """
    Verifies that when violations reach max permitted (15) and 16th violation occurs,
    session is marked failed.
    """
    sid = "sess_fail_009"
    engine.reset_session(sid)

    failed_reasons = []
    engine.add_failure_callback(lambda r: failed_reasons.append(r))

    # Generate 15 distinct violations
    for i in range(15):
        rec = engine.record_task_manager_process(sid, pid=10000 + i)
        assert rec is not None
        assert rec.count_after == i + 1
        # Reconcile so next launch counts
        engine.reconcile_active_processes(sid, is_process_alive_fn=lambda p: False)

    assert engine.get_total_violations(sid) == 15
    is_failed, _ = engine.is_session_failed(sid)
    assert is_failed is False
    assert len(failed_reasons) == 0

    # 16th violation -> session fails!
    rec16 = engine.record_task_manager_process(sid, pid=10016)
    assert rec16 is not None
    assert rec16.count_after == 16

    is_failed, reason = engine.is_session_failed(sid)
    assert is_failed is True
    assert "Maximum permitted violations exceeded (16 / 15)" in reason
    assert len(failed_reasons) == 1
