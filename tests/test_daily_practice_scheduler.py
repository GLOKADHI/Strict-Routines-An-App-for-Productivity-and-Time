"""
Test suite for StrictRecords Periodic Daily Practice Test Scheduler (2 to 4 Tests Per Day).
Verifies:
1. Daily recurring preparation activity strictly separated from official exam prebooking.
2. Default 2 mandatory tests: Quantitative Aptitude (30m) & Communication (30m).
3. Optional Test 3 & Test 4 (up to 4 tests max) with customizable categories:
   - Logical Reasoning
   - Technical Aptitude
   - Programming
   - Company-Specific Assessment (e.g. Cognizant CST)
   - Custom Test
4. Attempting to add a 5th test is strictly blocked with "Maximum 4 tests allowed."
5. Morning Daily Scheduling Window before 10:00 AM with countdown timer; locks at 10:00 AM.
6. If submission is missed past 10:00 AM deadline, marked MISSED (never fabricate results).
7. Controlled rescheduling for genuine exceptions.
8. Execution lifecycle: SCHEDULED -> IN_PROGRESS -> COMPLETED.
9. Regular points policy (+5 pts per completed practice test, distinct from official exam exemption).
10. Dashboard summary calculations (total, completed, pending, missed, time spent, completion percentage).
"""

import os
import shutil
import tempfile
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.database import StrictRecordsDB
from core.daily_practice_test_engine import (
    DailyPracticeTestEngine,
    CATEGORY_QUANTITATIVE_APTITUDE,
    CATEGORY_COMMUNICATION,
    CATEGORY_LOGICAL_REASONING,
    CATEGORY_COMPANY_SPECIFIC,
    CATEGORY_PROGRAMMING,
    DEFAULT_POINTS_PER_PRACTICE_TEST,
)


@pytest.fixture
def test_db():
    temp_dir = tempfile.mkdtemp(prefix="strict_practice_test_")
    db_path = os.path.join(temp_dir, "test.db")
    db = StrictRecordsDB(db_path=db_path)
    yield db
    del db
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_default_template_and_customization(test_db):
    """Verifies default template has 2 mandatory tests and can be customized up to 4."""
    # 1. Initial retrieval seeds default template
    tmpl = test_db.get_daily_practice_template()
    assert tmpl is not None
    assert tmpl["template_id"] == "DEFAULT"
    tests = tmpl["tests"]
    assert len(tests) == 2
    assert tests[0]["category"] == CATEGORY_QUANTITATIVE_APTITUDE
    assert tests[0]["duration_minutes"] == 30
    assert tests[1]["category"] == CATEGORY_COMMUNICATION
    assert tests[1]["duration_minutes"] == 30

    # 2. Add Test 3 (Logical Reasoning) and Test 4 (Cognizant CST)
    custom_tests = tests + [
        {
            "test_index": 3,
            "category": CATEGORY_LOGICAL_REASONING,
            "title": "Logical Reasoning Assessment",
            "start_time": "14:00",
            "duration_minutes": 30,
        },
        {
            "test_index": 4,
            "category": CATEGORY_COMPANY_SPECIFIC,
            "title": "Cognizant CST Round 1 CA",
            "start_time": "17:00",
            "duration_minutes": 40,
        },
    ]

    saved = test_db.save_daily_practice_template(custom_tests, template_name="4-Test Placement Prep Template")
    assert len(saved["tests"]) == 4
    assert saved["tests"][2]["category"] == CATEGORY_LOGICAL_REASONING
    assert saved["tests"][3]["category"] == CATEGORY_COMPANY_SPECIFIC
    assert saved["tests"][3]["title"] == "Cognizant CST Round 1 CA"
    assert saved["tests"][3]["end_time"] == "17:40"


def test_max_4_tests_enforcement():
    """Verifies that attempting 5 tests is blocked with 'Maximum 4 tests allowed.'"""
    tests_5 = [
        {"test_index": 1, "category": CATEGORY_QUANTITATIVE_APTITUDE, "start_time": "10:00", "duration_minutes": 30},
        {"test_index": 2, "category": CATEGORY_COMMUNICATION, "start_time": "11:00", "duration_minutes": 30},
        {"test_index": 3, "category": CATEGORY_LOGICAL_REASONING, "start_time": "12:00", "duration_minutes": 30},
        {"test_index": 4, "category": CATEGORY_COMPANY_SPECIFIC, "start_time": "14:00", "duration_minutes": 30},
        {"test_index": 5, "category": CATEGORY_PROGRAMMING, "start_time": "16:00", "duration_minutes": 30},
    ]

    val = DailyPracticeTestEngine.validate_practice_tests(tests_5)
    assert not val["valid"]
    assert "Maximum 4 tests allowed." in val["error"]


def test_morning_deadline_and_lock_enforcement(test_db):
    """Verifies morning deadline info, 10:00 AM lock, and missed schedule behavior."""
    tz = ZoneInfo("Asia/Kolkata")

    # 1. Before 10:00 AM (e.g. 08:30 AM)
    before_10 = datetime(2026, 11, 20, 8, 30, 0, tzinfo=tz)
    info_before = DailyPracticeTestEngine.get_morning_deadline_info("2026-11-20", current_dt=before_10)
    assert not info_before["is_locked"]
    assert info_before["remaining_seconds"] == (90 * 60)  # 1.5 hours remaining

    # 2. After 10:00 AM (e.g. 10:05 AM)
    after_10 = datetime(2026, 11, 20, 10, 5, 0, tzinfo=tz)
    info_after = DailyPracticeTestEngine.get_morning_deadline_info("2026-11-20", current_dt=after_10)
    assert info_after["is_locked"]
    assert info_after["remaining_seconds"] == 0

    # 3. If past deadline on target date, attempting to submit without force_unlock raises PermissionError and marks MISSED
    past_date = "2026-10-01"  # Definitely past
    tests = DailyPracticeTestEngine.get_default_template_tests()
    with pytest.raises(PermissionError) as exc_info:
        test_db.submit_daily_practice_schedule(past_date, tests, force_unlock=False)
    assert "10:00 AM" in str(exc_info.value)

    # Verify schedule was recorded as MISSED in database
    missed_sched = test_db.get_daily_practice_schedule(past_date)
    assert missed_sched is not None
    assert missed_sched["status"] == "MISSED"


def test_schedule_submission_and_execution_lifecycle(test_db):
    """
    Tests full execution lifecycle:
    Submission -> SCHEDULED -> IN_PROGRESS -> COMPLETED with +5 points ledger credit.
    """
    target_date = "2026-11-25"
    tests = [
        {
            "test_index": 1,
            "category": CATEGORY_QUANTITATIVE_APTITUDE,
            "title": "Quantitative Aptitude",
            "start_time": "10:30",
            "duration_minutes": 30,
        },
        {
            "test_index": 2,
            "category": CATEGORY_COMMUNICATION,
            "title": "Communication",
            "start_time": "11:30",
            "duration_minutes": 30,
        },
        {
            "test_index": 3,
            "category": CATEGORY_COMPANY_SPECIFIC,
            "title": "Cognizant CST Round 2 Aptitude",
            "start_time": "16:00",
            "duration_minutes": 80,
        },
    ]

    # Submit schedule (using force_unlock=True for future target date in test)
    sched = test_db.submit_daily_practice_schedule(target_date, tests, notes="Prepping for Cognizant recruitment", force_unlock=True)
    assert sched["status"] == "SUBMITTED"
    assert sched["total_tests"] == 3
    assert len(sched["executions"]) == 3

    exec_1_id = f"PRAC-{target_date}-1"
    exec_2_id = f"PRAC-{target_date}-2"
    exec_3_id = f"PRAC-{target_date}-3"

    # Start Test 1
    started_1 = test_db.start_practice_test_execution(exec_1_id)
    assert started_1["status"] == "IN_PROGRESS"
    assert started_1["started_at"] != ""

    # Complete Test 1 with score
    completed_1 = test_db.complete_practice_test_execution(exec_1_id, score=28.0, max_score=30.0, notes="Scored 28/30")
    assert completed_1["status"] == "COMPLETED"
    assert completed_1["points_awarded"] == DEFAULT_POINTS_PER_PRACTICE_TEST
    assert completed_1["score"] == 28.0

    # Verify score_events ledger received idempotent +5 points
    with test_db._connect() as conn:
        se_rows = conn.execute(
            "SELECT event_id, points, event_type FROM score_events WHERE related_task_id = ?", (exec_1_id,)
        ).fetchall()
        assert len(se_rows) == 1
        assert se_rows[0][1] == 5
        assert se_rows[0][2] == "PRACTICE_TEST_COMPLETED"

    # Reschedule Test 3 due to conflict
    rescheduled_3 = test_db.reschedule_practice_test_execution(exec_3_id, new_start_time="17:00", reason="College lab extension")
    assert rescheduled_3["status"] == "RESCHEDULED"
    assert rescheduled_3["start_time"] == "17:00"
    assert rescheduled_3["end_time"] == "18:20"

    # Dashboard summary check
    summary = test_db.get_practice_test_summary(target_date)
    assert summary["total_scheduled"] == 3
    assert summary["completed"] == 1
    assert summary["pending"] == 2
    assert summary["completion_rate_percent"] == 33.3
    assert summary["total_time_minutes"] == (30 + 30 + 80)
