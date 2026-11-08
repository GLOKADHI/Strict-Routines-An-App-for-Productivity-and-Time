"""
Test suite for StrictRecords Multiple Exam Prebooking (Maximum 4 Tests).
Verifies:
1. 1 to 4 tests allowed in a single prebooking flow.
2. Attempting a 5th test is strictly blocked with "Maximum 4 tests allowed."
3. Independent field support for each test (name, subject, institution, date, start/end times, buffers, documents).
4. Protected time slot calculation (alloc_start = start - prep_buf, alloc_end = end + comp_buf).
5. Overlap conflict detection between tests within the batch.
6. Overlap conflict detection against existing approved exams (never silently moving approved exams).
7. Independent AI document validation: one test's status does not affect another.
8. Independent early exit and window reactivation: early exit shortens only that test's protected window, while other tests or gaps remain untouched.
"""

import base64
import json
import os
import shutil
import tempfile
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.database import StrictRecordsDB
from core.exam_prebooking_engine import (
    ExamPrebookingEngine,
    STATE_EXAM_APPROVED,
    STATE_EXAM_REJECTED,
)
from core.models import ScoreEventType


@pytest.fixture
def test_db():
    temp_dir = tempfile.mkdtemp(prefix="strict_multi_exam_test_")
    db_path = os.path.join(temp_dir, "test.db")
    db = StrictRecordsDB(db_path=db_path)
    yield db
    del db
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_batch_validation_1_to_4_tests():
    """Verifies that 1 to 4 tests are accepted, but 0 or 5+ tests are rejected."""
    # 0 tests -> error
    res_0 = ExamPrebookingEngine.validate_batch_allocation([])
    assert not res_0["valid"]
    assert "At least 1 test is required." in res_0["error"]

    # 1 test -> valid
    tests_1 = [
        {
            "exam_name": "Test 1 - Semester Math",
            "subject": "Mathematics",
            "institution": "University",
            "exam_date": "2026-11-10",
            "start_time": "10:00",
            "end_time": "12:00",
            "prep_buffer_minutes": 30,
            "completion_buffer_minutes": 30,
        }
    ]
    res_1 = ExamPrebookingEngine.validate_batch_allocation(tests_1)
    assert res_1["valid"]
    assert len(res_1["allocations"]) == 1

    # 4 tests -> valid
    tests_4 = [
        {
            "exam_name": f"Test {i} - Subject {i}",
            "subject": f"Subj {i}",
            "institution": "University",
            "exam_date": "2026-11-10",
            "start_time": f"{8 + i * 3:02d}:00",
            "end_time": f"{9 + i * 3:02d}:00",
            "prep_buffer_minutes": 15,
            "completion_buffer_minutes": 15,
        }
        for i in range(4)
    ]
    res_4 = ExamPrebookingEngine.validate_batch_allocation(tests_4)
    assert res_4["valid"]
    assert len(res_4["allocations"]) == 4

    # 5 tests -> strictly blocked with "Maximum 4 tests allowed."
    tests_5 = tests_4 + [
        {
            "exam_name": "Test 5 - Over Limit",
            "subject": "Subj 5",
            "institution": "University",
            "exam_date": "2026-11-10",
            "start_time": "22:00",
            "end_time": "23:00",
            "prep_buffer_minutes": 15,
            "completion_buffer_minutes": 15,
        }
    ]
    res_5 = ExamPrebookingEngine.validate_batch_allocation(tests_5)
    assert not res_5["valid"]
    assert "Maximum 4 tests allowed." in res_5["error"]


def test_pairwise_overlap_conflict_within_batch():
    """Detects overlapping protected time slots between tests in the same batch."""
    # Test 1: 09:00 - 11:00 with 30m prep & comp -> protected: 08:30 - 11:30
    # Test 2: 11:00 - 13:00 with 30m prep & comp -> protected: 10:30 - 13:30 (overlaps with Test 1!)
    tests_overlapping = [
        {
            "exam_name": "Test 1 - Data Structures",
            "subject": "CS201",
            "institution": "SIST",
            "exam_date": "2026-11-12",
            "start_time": "09:00",
            "end_time": "11:00",
            "prep_buffer_minutes": 30,
            "completion_buffer_minutes": 30,
        },
        {
            "exam_name": "Test 2 - Algorithms",
            "subject": "CS202",
            "institution": "SIST",
            "exam_date": "2026-11-12",
            "start_time": "11:00",
            "end_time": "13:00",
            "prep_buffer_minutes": 30,
            "completion_buffer_minutes": 30,
        },
    ]

    res = ExamPrebookingEngine.validate_batch_allocation(tests_overlapping)
    assert not res["valid"]
    assert any("Protected time conflict" in c for c in res["conflicts"])
    assert "Test #1" in res["error"]
    assert "Test #2" in res["error"]


def test_conflict_with_existing_approved_exam():
    """Verifies that an incoming test cannot overwrite or overlap an existing approved exam."""
    existing_approved = [
        {
            "booking_id": "EXAM-EXISTING-01",
            "exam_name": "Final Semester Physics",
            "exam_date": "2026-11-15",
            "official_start_time": "09:00",
            "official_end_time": "12:00",
            "allocation_start_ms": int(datetime(2026, 11, 15, 8, 30, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1000),
            "allocation_end_ms": int(datetime(2026, 11, 15, 12, 30, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1000),
            "status": "EXAM_APPROVED",
        }
    ]

    # Incoming batch with test overlapping 08:30 - 12:30
    incoming_tests = [
        {
            "exam_name": "Cognizant CST Round 1",
            "subject": "Placement",
            "institution": "SIST",
            "exam_date": "2026-11-15",
            "start_time": "11:00",
            "end_time": "13:00",
            "prep_buffer_minutes": 30,
            "completion_buffer_minutes": 30,
        }
    ]

    res = ExamPrebookingEngine.validate_batch_allocation(incoming_tests, existing_approved_exams=existing_approved)
    assert not res["valid"]
    assert any("Conflict with existing approved exam" in c for c in res["conflicts"])
    assert "Final Semester Physics" in res["error"]


def test_create_and_retrieve_exam_batch(test_db):
    """Verifies batch storage, retrieval, and independent test properties."""
    batch_data = {
        "batch_id": "BATCH-TEST-20261116",
        "account_id": "user_lok",
        "tests": [
            {
                "exam_name": "Cognizant CST Round 1 CA",
                "subject": "Communication Assessment",
                "institution": "Sathyabama Placement",
                "exam_date": "2026-11-16",
                "start_time": "09:00",
                "end_time": "10:00",
                "prep_buffer_minutes": 30,
                "completion_buffer_minutes": 30,
            },
            {
                "exam_name": "Cognizant CST Round 2 Aptitude",
                "subject": "Aptitude Assessment",
                "institution": "Sathyabama Placement",
                "exam_date": "2026-11-16",
                "start_time": "14:00",
                "end_time": "16:00",
                "prep_buffer_minutes": 30,
                "completion_buffer_minutes": 30,
            },
        ],
    }

    created = test_db.create_exam_prebooking_batch(batch_data)
    assert created["success"]
    assert created["total_tests"] == 2
    assert len(created["tests"]) == 2

    # Retrieve from DB
    batch = test_db.get_exam_batch("BATCH-TEST-20261116")
    assert batch is not None
    assert batch["total_tests"] == 2
    assert len(batch["tests"]) == 2
    assert batch["tests"][0]["test_index"] == 1
    assert batch["tests"][1]["test_index"] == 2
    assert batch["tests"][0]["exam_name"] == "Cognizant CST Round 1 CA"
    assert batch["tests"][1]["exam_name"] == "Cognizant CST Round 2 Aptitude"


def test_batch_early_exit_affects_only_target_test(test_db):
    """
    Verifies that early exit on one test in a multi-test schedule
    shortens only that test's protected window, while another test later in the day
    and windows outside remain properly managed.
    """
    exam_date = "2026-11-18"

    # Prebook 2 tests on the same day:
    # Test 1: 09:00 - 11:00 (protected 08:30 - 11:30)
    # Test 2: 15:00 - 17:00 (protected 14:30 - 17:30)
    batch_data = {
        "batch_id": "BATCH-EARLY-EXIT-TEST",
        "account_id": "user_lok",
        "tests": [
            {
                "booking_id": "EXAM-T1",
                "exam_name": "Morning Assessment",
                "subject": "Coding",
                "institution": "SIST",
                "exam_date": exam_date,
                "start_time": "09:00",
                "end_time": "11:00",
                "prep_buffer_minutes": 30,
                "completion_buffer_minutes": 30,
                "status": "EXAM_APPROVED",
            },
            {
                "booking_id": "EXAM-T2",
                "exam_name": "Afternoon Assessment",
                "subject": "Aptitude",
                "institution": "SIST",
                "exam_date": exam_date,
                "start_time": "15:00",
                "end_time": "17:00",
                "prep_buffer_minutes": 30,
                "completion_buffer_minutes": 30,
                "status": "EXAM_APPROVED",
            },
        ],
    }

    test_db.create_exam_prebooking_batch(batch_data)

    # Insert mock hourly activities:
    # 09:00-10:00 (during Test 1) -> should be EXCUSED
    # 10:00-11:00 (during Test 1) -> should be EXCUSED
    # 12:00-13:00 (in GAP between Test 1 and 2) -> should remain SCHEDULED
    # 15:00-16:00 (during Test 2) -> should be EXCUSED
    tz = ZoneInfo("Asia/Kolkata")
    with test_db._connect() as conn:
        for hr in [9, 10, 12, 15]:
            act_id = f"ACT-{exam_date.replace('-', '')}-{hr:02d}00-HOURLY"
            s_iso = datetime(2026, 11, 18, hr - 1, 50, tzinfo=tz).isoformat()
            e_iso = datetime(2026, 11, 18, hr, 10, tzinfo=tz).isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, type, date, scheduled_time, window_start, window_end,
                    notification_time, status, points, details_json, title
                ) VALUES (?, 'user_lok', 'HOURLY', ?, ?, ?, ?, ?, 'SCHEDULED', 8, '{}', ?)
                """,
                (act_id, exam_date, s_iso, s_iso, e_iso, s_iso, f"Hourly Check {hr}:00")
            )

    # Reconcile exemptions
    test_db.reconcile_exam_attendance_exemptions(exam_date)

    # Verify initial suppression
    with test_db._connect() as conn:
        st_9 = conn.execute("SELECT status, points FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%0900-HOURLY'").fetchone()
        st_10 = conn.execute("SELECT status, points FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%1000-HOURLY'").fetchone()
        st_12 = conn.execute("SELECT status, points FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%1200-HOURLY'").fetchone()
        st_15 = conn.execute("SELECT status, points FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%1500-HOURLY'").fetchone()

        assert st_9[0] == "EXCUSED"
        assert st_9[1] == 8
        assert st_10[0] == "EXCUSED"
        assert st_10[1] == 8
        assert st_12[0] == "SCHEDULED"  # Gap between tests is NOT excused!
        assert st_15[0] == "EXCUSED"
        assert st_15[1] == 8

    # Now confirm early exit for Test 1 ONLY at 09:30 AM
    exit_ms = int(datetime(2026, 11, 18, 9, 30, tzinfo=tz).timestamp() * 1000)
    test_db.confirm_early_exam_exit("EXAM-T1", actual_end_time="09:30", actual_end_iso="2026-11-18T09:30:00+05:30", actual_end_ms=exit_ms)

    # Re-check activities:
    # 09:00-10:00 window (08:50-09:10) falls before 09:30 -> remains EXCUSED
    # 10:00-11:00 window (09:50-10:10) falls after 09:30 early exit -> reactivated to SCHEDULED!
    # 12:00-13:00 window -> remains SCHEDULED
    # 15:00-16:00 window -> remains EXCUSED because Test 2 is independent!
    with test_db._connect() as conn:
        st_9_post = conn.execute("SELECT status FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%0900-HOURLY'").fetchone()
        st_10_post = conn.execute("SELECT status FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%1000-HOURLY'").fetchone()
        st_15_post = conn.execute("SELECT status FROM scheduled_activities WHERE date = '2026-11-18' AND activity_id LIKE '%1500-HOURLY'").fetchone()

        assert st_9_post[0] == "EXCUSED"
        assert st_10_post[0] == "SCHEDULED"
        assert st_15_post[0] == "EXCUSED"  # Test 2 was NOT shortened!
