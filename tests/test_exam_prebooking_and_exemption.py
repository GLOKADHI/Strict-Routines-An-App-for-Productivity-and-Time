"""
Unit & Integration Tests for Exam Prebooking, AI Multi-Image Validation & Automatic Attendance Exemption.
"""

import base64
import json
import os
import tempfile
import pytest
from datetime import datetime
from PIL import Image, ImageDraw
import io

from core.database import StrictRecordsDB
from core.exam_prebooking_engine import (
    ExamPrebookingEngine,
    STATE_EXAM_APPROVED,
    STATE_EXAM_REVIEW_REQUIRED,
    STATE_EXAM_REJECTED,
    CONSISTENCY_CONSISTENT,
    CONSISTENCY_RESCHEDULED_RESOLVED,
    CONSISTENCY_CONTRADICTORY,
)


def create_test_image(text: str, width: int = 600, height: int = 400) -> bytes:
    """Creates a sample test image with rendered text for OCR & visual testing."""
    img = Image.new("RGB", (width, height), color=(245, 245, 250))
    draw = ImageDraw.Draw(img)
    # Draw simple header rectangle
    draw.rectangle([10, 10, width - 10, 50], fill=(40, 70, 140))
    # Draw text lines
    lines = text.split("\n")
    y = 60
    for l in lines:
        draw.text((20, y), l, fill=(20, 20, 20))
        y += 25
    buf = io.BytesIO()
    # Embed text in JPEG comment for deterministic extraction in tests
    img.save(buf, format="JPEG", quality=90, comment=text.encode("utf-8"))
    return buf.getvalue()


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = StrictRecordsDB(path)
    yield db
    try:
        os.remove(path)
    except Exception:
        pass


def test_time_allocation_calculation():
    """Verify deterministic allocation: official 10:00 - 13:00 + 30m prep + 30m completion = 9:30 - 13:30."""
    start_dt, end_dt, s_iso, e_iso, s_ms, e_ms = ExamPrebookingEngine.calculate_time_allocation(
        exam_date="2026-11-10",
        start_time="10:00",
        end_time="13:00",
        prep_buffer_minutes=30,
        completion_buffer_minutes=30,
    )
    assert start_dt.hour == 9
    assert start_dt.minute == 30
    assert end_dt.hour == 13
    assert end_dt.minute == 30
    assert "09:30:00" in s_iso
    assert "13:30:00" in e_iso
    assert e_ms > s_ms


def test_max_five_images_enforcement():
    """Verify system strictly rejects more than 5 supporting images."""
    dummy_img = create_test_image("Anna University Timetable\nSubject: CS8791 Cloud Computing\nDate: 2026-11-10")
    images_6 = [dummy_img] * 6
    with pytest.raises(ValueError, match="Up to 5 images permitted"):
        ExamPrebookingEngine.validate_and_allocate(
            exam_name="End Semester Exam",
            subject="Cloud Computing",
            institution="Anna University",
            exam_date="2026-11-10",
            start_time="10:00",
            end_time="13:00",
            supporting_images=images_6,
        )


def test_single_email_screenshot_validation():
    """Scenario 1: Single official email screenshot."""
    email_text = (
        "From: Controller of Examinations <coe@annauniv.edu>\n"
        "Subject: End Semester Examination Schedule Nov 2026\n"
        "Date: 2 Nov 2026\n"
        "Anna University Examinations\n"
        "Course: B.E Computer Science\n"
        "Subject: CS8791 Cloud Computing\n"
        "Exam Date: 2026-11-10\n"
        "Timings: 10:00 AM to 01:00 PM"
    )
    img = create_test_image(email_text)
    decision = ExamPrebookingEngine.validate_and_allocate(
        exam_name="Cloud Computing End Sem",
        subject="Cloud Computing",
        institution="Anna University",
        exam_date="2026-11-10",
        start_time="10:00",
        end_time="13:00",
        supporting_images=[img],
        image_filenames=["email_announcement.png"],
    )
    assert decision.validation_status in (STATE_EXAM_APPROVED, STATE_EXAM_REVIEW_REQUIRED)
    assert decision.supporting_images_reviewed == 1
    assert decision.cross_image_consistency == CONSISTENCY_CONSISTENT
    assert "09:30:00" in decision.allocation_start_iso
    assert "13:30:00" in decision.allocation_end_iso


def test_cross_image_matching_complementary_timetable_and_email():
    """Scenario 2 & 5: Timetable + email confirmation matching."""
    doc1 = create_test_image("Anna University Timetable\nCS8791 Cloud Computing\nDate: 2026-11-10 Session: FN 10:00 - 13:00")
    doc2 = create_test_image("From: coe@annauniv.edu\nSubject: Exam Center Instructions for CS8791\nDate: 2026-11-10")

    decision = ExamPrebookingEngine.validate_and_allocate(
        exam_name="Cloud Computing Exam",
        subject="Cloud Computing",
        institution="Anna University",
        exam_date="2026-11-10",
        start_time="10:00",
        end_time="13:00",
        supporting_images=[doc1, doc2],
        image_filenames=["timetable.jpg", "email_instructions.jpg"],
    )
    assert decision.supporting_images_reviewed == 2
    assert decision.cross_image_consistency == CONSISTENCY_CONSISTENT
    assert decision.validation_status == STATE_EXAM_APPROVED


def test_up_to_five_images_handled():
    """Scenario 3: Five supporting images (timetable, email, hall ticket, syllabus, guidelines)."""
    docs = [
        create_test_image("Image 1: University Timetable CS8791 2026-11-10"),
        create_test_image("Image 2: Hall Ticket Candidate Lokad Roll 2021001 CS8791"),
        create_test_image("Image 3: Confirmation Email from coe@annauniv.edu 2026-11-10"),
        create_test_image("Image 4: Lab / Theory Instructions CS8791"),
        create_test_image("Image 5: Center Allotment Slip Hall 4 2026-11-10"),
    ]
    decision = ExamPrebookingEngine.validate_and_allocate(
        exam_name="Cloud Computing Exam",
        subject="Cloud Computing",
        institution="Anna University",
        exam_date="2026-11-10",
        start_time="10:00",
        end_time="13:00",
        supporting_images=docs,
    )
    assert decision.supporting_images_reviewed == 5
    assert decision.validation_status == STATE_EXAM_APPROVED


def test_rescheduling_notice_precedence():
    """Scenario 6: Old timetable (Nov 10) + newer official rescheduling notice (Nov 12)."""
    doc_old = create_test_image("Anna University Timetable CS8791\nExam Date: 2026-11-10 10:00 - 13:00")
    doc_new = create_test_image(
        "From: coe@annauniv.edu\n"
        "Subject: Urgent: Rescheduled Examination Notification\n"
        "The examination CS8791 Cloud Computing is rescheduled to 2026-11-12 due to administrative reasons."
    )
    decision = ExamPrebookingEngine.validate_and_allocate(
        exam_name="Cloud Computing Exam",
        subject="Cloud Computing",
        institution="Anna University",
        exam_date="2026-11-12",
        start_time="10:00",
        end_time="13:00",
        supporting_images=[doc_old, doc_new],
    )
    assert decision.cross_image_consistency == CONSISTENCY_RESCHEDULED_RESOLVED
    assert "rescheduling" in decision.explanation.lower()


def test_contradictory_dates_trigger_review_required():
    """Scenario 7: Contradictory examination dates without resolution trigger EXAM_REVIEW_REQUIRED."""
    doc1 = create_test_image("Anna University Timetable CS8791\nExam Date: 2026-11-05")
    doc2 = create_test_image("Hall Ticket CS8791\nExam Date: 2026-11-25")

    decision = ExamPrebookingEngine.validate_and_allocate(
        exam_name="Cloud Computing Exam",
        subject="Cloud Computing",
        institution="Anna University",
        exam_date="2026-11-15",
        start_time="10:00",
        end_time="13:00",
        supporting_images=[doc1, doc2],
    )
    assert decision.cross_image_consistency == CONSISTENCY_CONTRADICTORY
    assert decision.validation_status == STATE_EXAM_REVIEW_REQUIRED


def test_automatic_hourly_attendance_exemption(temp_db):
    """
    Scenario 8 & 9: Approved Exam (10:00-13:00 with 30m buffers = 9:30-13:30)
    automatically excuses overlapping hourly checks:
    - 8:50 - 9:10: NOT excused (before 9:30) -> SCHEDULED (8 pts)
    - 9:50 - 10:10: EXCUSED (0 pts, no proof)
    - 10:50 - 11:10: EXCUSED (0 pts, no proof)
    - 11:50 - 12:10: EXCUSED (0 pts, no proof)
    - 12:50 - 13:10: EXCUSED (0 pts, no proof)
    - 13:50 - 14:10: NOT excused (after 13:30) -> SCHEDULED (8 pts)
    """
    exam_date = "2026-11-10"

    # Seed hourly activities
    windows = [
        ("act_0900", "08:50", "09:10"),
        ("act_1000", "09:50", "10:10"),
        ("act_1100", "10:50", "11:10"),
        ("act_1200", "11:50", "12:10"),
        ("act_1300", "12:50", "13:10"),
        ("act_1400", "13:50", "14:10"),
    ]
    with temp_db._connect() as conn:
        for aid, ws, we in windows:
            conn.execute(
                """
                INSERT INTO scheduled_activities (
                    activity_id, account_id, type, title, date, scheduled_time,
                    window_start, window_end, notification_time, status, points, is_enabled
                ) VALUES (?, 'user_default', 'HOURLY', ?, ?, ?, ?, ?, ?, 'SCHEDULED', 8, 1)
                """,
                (aid, f"Hourly Check {ws}-{we}", exam_date, ws, ws, we, ws)
            )

    # Create approved exam booking
    booking = temp_db.create_exam_prebooking({
        "exam_name": "Operating Systems End Sem",
        "subject": "Operating Systems",
        "institution": "Anna University",
        "exam_date": exam_date,
        "official_start_time": "10:00",
        "official_end_time": "13:00",
        "prep_buffer_minutes": 30,
        "completion_buffer_minutes": 30,
        "status": "EXAM_APPROVED",
        "supporting_images_count": 1,
    })

    # Query activities
    with temp_db._connect() as conn:
        rows = {r["activity_id"]: dict(r) for r in conn.execute("SELECT * FROM scheduled_activities WHERE date = ?", (exam_date,)).fetchall()}

    # Check 08:50 - 09:10: Not excused!
    assert rows["act_0900"]["status"] == "SCHEDULED"
    assert rows["act_0900"]["points"] == 8

    # Check 09:50 - 10:10: EXCUSED with FULL eligible points (8 pts)!
    assert rows["act_1000"]["status"] == "EXCUSED"
    assert rows["act_1000"]["points"] == 8
    det1000 = json.loads(rows["act_1000"]["details_json"])
    assert det1000.get("suppressed_by_exam") is True
    assert "Operating Systems" in det1000.get("suppression_reason")

    # Check 10:50 - 11:10, 11:50 - 12:10, 12:50 - 13:10: All EXCUSED!
    assert rows["act_1100"]["status"] == "EXCUSED"
    assert rows["act_1200"]["status"] == "EXCUSED"
    assert rows["act_1300"]["status"] == "EXCUSED"

    # Check 13:50 - 14:10: Not excused!
    assert rows["act_1400"]["status"] == "SCHEDULED"
    assert rows["act_1400"]["points"] == 8


def test_cancelling_exam_unexcuses_slots_and_restores_scheduling(temp_db):
    """Scenario 10: Cancelling an exam restores normal hourly attendance requirements."""
    exam_date = "2026-11-10"

    with temp_db._connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_activities (
                activity_id, account_id, type, title, date, scheduled_time,
                window_start, window_end, notification_time, status, points, is_enabled
            ) VALUES ('act_1000', 'user_default', 'HOURLY', '10:00 Check', ?, '09:50', '09:50', '10:10', '09:50', 'SCHEDULED', 8, 1)
            """,
            (exam_date,)
        )

    # Prebook and excuse
    booking = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-CANCEL-TEST",
        "exam_name": "Test Exam",
        "subject": "Test",
        "institution": "University",
        "exam_date": exam_date,
        "official_start_time": "10:00",
        "official_end_time": "11:00",
        "prep_buffer_minutes": 30,
        "completion_buffer_minutes": 30,
        "status": "EXAM_APPROVED",
    })

    with temp_db._connect() as conn:
        st_before = conn.execute("SELECT status, points FROM scheduled_activities WHERE activity_id='act_1000'").fetchone()
        assert st_before["status"] == "EXCUSED"
        assert st_before["points"] == 8

    # Cancel exam
    temp_db.cancel_exam_prebooking("EXAM-CANCEL-TEST", reason="Postponed by university")

    with temp_db._connect() as conn:
        st_after = conn.execute("SELECT status, points FROM scheduled_activities WHERE activity_id='act_1000'").fetchone()
        # Restored to normal scheduling
        assert st_after["status"] == "SCHEDULED"
        assert st_after["points"] == 8


def test_reschedule_exam_recalculates_exemptions(temp_db):
    """Scenario 11: Rescheduling an exam moves the excused window dynamically."""
    d1 = "2026-11-10"
    d2 = "2026-11-11"

    with temp_db._connect() as conn:
        conn.execute(
            "INSERT INTO scheduled_activities (activity_id, account_id, type, title, date, scheduled_time, window_start, window_end, notification_time, status, points, is_enabled) "
            "VALUES ('act_d1', 'user_default', 'HOURLY', 'D1 Check', ?, '09:50', '09:50', '10:10', '09:50', 'SCHEDULED', 8, 1)",
            (d1,)
        )
        conn.execute(
            "INSERT INTO scheduled_activities (activity_id, account_id, type, title, date, scheduled_time, window_start, window_end, notification_time, status, points, is_enabled) "
            "VALUES ('act_d2', 'user_default', 'HOURLY', 'D2 Check', ?, '09:50', '09:50', '10:10', '09:50', 'SCHEDULED', 8, 1)",
            (d2,)
        )

    b = temp_db.create_exam_prebooking({
        "booking_id": "EXAM-RESCHED-TEST",
        "exam_name": "Reschedule Test",
        "subject": "Math",
        "institution": "University",
        "exam_date": d1,
        "official_start_time": "10:00",
        "official_end_time": "12:00",
        "prep_buffer_minutes": 30,
        "completion_buffer_minutes": 30,
        "status": "EXAM_APPROVED",
    })

    with temp_db._connect() as conn:
        assert conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='act_d1'").fetchone()["status"] == "EXCUSED"
        assert conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='act_d2'").fetchone()["status"] == "SCHEDULED"

    # Reschedule to Day 2
    temp_db.reschedule_exam_prebooking("EXAM-RESCHED-TEST", new_date=d2, new_start="10:00", new_end="12:00")

    with temp_db._connect() as conn:
        # Day 1 restored!
        assert conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='act_d1'").fetchone()["status"] == "SCHEDULED"
        # Day 2 excused!
        assert conn.execute("SELECT status FROM scheduled_activities WHERE activity_id='act_d2'").fetchone()["status"] == "EXCUSED"


def test_idempotent_sync_parity(temp_db):
    """Scenario 12: Verify cross-device sync creates and updates exam record idempotently."""
    payload = {
        "booking_id": "EXAM-SYNC-001",
        "exam_name": "Distributed Systems",
        "subject": "CS8701",
        "institution": "Tech Institute",
        "exam_date": "2026-11-15",
        "official_start_time": "14:00",
        "official_end_time": "17:00",
        "prep_buffer_minutes": 45,
        "completion_buffer_minutes": 30,
        "status": "EXAM_APPROVED",
        "version": 1,
    }
    sync1 = temp_db.sync_exam_prebooking(payload, device_id="android_a55")
    assert sync1["accepted"] is True
    assert sync1["action"] == "SYNCED"

    # Sync again with same version -> IGNORED_OLDER_OR_EQUAL
    sync2 = temp_db.sync_exam_prebooking(payload, device_id="android_a55")
    assert sync2["accepted"] is True
    assert sync2["action"] == "IGNORED_OLDER_OR_EQUAL"
