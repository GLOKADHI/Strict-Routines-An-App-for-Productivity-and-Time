"""
StrictRecords — Periodic Daily Practice Test Scheduler Engine.
Handles:
- 2 to 4 practice tests per day (Default 2: Quantitative Aptitude & Communication; Optional 3 & 4: Logical Reasoning, Technical Aptitude, Programming, Company-Specific Assessment e.g. Cognizant CST, Custom Test).
- Maximum 4 tests allowed (5th test strictly blocked with error).
- Morning Daily Scheduling Window before 10:00 AM IST.
- Mandatory 10:00 AM submission deadline with real-time countdown; locks at 10:00 AM.
- If not submitted before 10:00 AM, marked MISSED (never fabricate results).
- Controlled rescheduling for genuine exceptions (e.g., approved official exams or explicit valid excuses).
- Periodic template persistence (daily_practice_test_templates) reloaded daily without overwriting submitted schedules.
- Independent test execution tracking (SCHEDULED, UPCOMING, IN_PROGRESS, COMPLETED, MISSED, RESCHEDULED).
- Points integration (idempotent regular completion points, distinct from official exam exemption points).
- Dashboard summary (total, completed, pending, missed, scores, time spent, completion percentage).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, date as calendar_date, time as clock_time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("DailyPracticeTestEngine")

DEFAULT_TIMEZONE = "Asia/Kolkata"
MIN_PRACTICE_TESTS = 2
MAX_PRACTICE_TESTS = 4
MORNING_DEADLINE_HOUR = 10
MORNING_DEADLINE_MINUTE = 0
DEFAULT_POINTS_PER_PRACTICE_TEST = 5

CATEGORY_QUANTITATIVE_APTITUDE = "Quantitative Aptitude"
CATEGORY_COMMUNICATION = "Communication"
CATEGORY_LOGICAL_REASONING = "Logical Reasoning"
CATEGORY_TECHNICAL_APTITUDE = "Technical Aptitude"
CATEGORY_PROGRAMMING = "Programming"
CATEGORY_COMPANY_SPECIFIC = "Company-Specific Assessment"
CATEGORY_CUSTOM = "Custom Test"

VALID_CATEGORIES = [
    CATEGORY_QUANTITATIVE_APTITUDE,
    CATEGORY_COMMUNICATION,
    CATEGORY_LOGICAL_REASONING,
    CATEGORY_TECHNICAL_APTITUDE,
    CATEGORY_PROGRAMMING,
    CATEGORY_COMPANY_SPECIFIC,
    CATEGORY_CUSTOM,
]

STATUS_SCHEDULED = "SCHEDULED"
STATUS_UPCOMING = "UPCOMING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_MISSED = "MISSED"
STATUS_RESCHEDULED = "RESCHEDULED"


class DailyPracticeTestEngine:
    """Core logic engine for daily practice test scheduling, execution, and verification."""

    @staticmethod
    def get_tz() -> ZoneInfo:
        return ZoneInfo(DEFAULT_TIMEZONE)

    @staticmethod
    def now(tz: Optional[ZoneInfo] = None) -> datetime:
        return datetime.now(tz or DailyPracticeTestEngine.get_tz())

    @staticmethod
    def today_str(tz: Optional[ZoneInfo] = None) -> str:
        return DailyPracticeTestEngine.now(tz).strftime("%Y-%m-%d")

    @staticmethod
    def get_default_template_tests() -> List[Dict[str, Any]]:
        """Returns the default mandatory 2 tests: Quantitative Aptitude & Communication."""
        return [
            {
                "test_index": 1,
                "category": CATEGORY_QUANTITATIVE_APTITUDE,
                "title": "Quantitative Aptitude",
                "start_time": "10:30",
                "duration_minutes": 30,
                "end_time": "11:00",
                "is_mandatory": True,
            },
            {
                "test_index": 2,
                "category": CATEGORY_COMMUNICATION,
                "title": "Communication",
                "start_time": "11:30",
                "duration_minutes": 30,
                "end_time": "12:00",
                "is_mandatory": True,
            },
        ]

    @staticmethod
    def calculate_end_time(start_time_str: str, duration_minutes: int) -> str:
        """Calculates HH:MM end time given start HH:MM and duration."""
        try:
            parts = start_time_str.strip().split(":")
            h, m = int(parts[0]), int(parts[1])
            tot = h * 60 + m + int(duration_minutes)
            end_h = (tot // 60) % 24
            end_m = tot % 60
            return f"{end_h:02d}:{end_m:02d}"
        except Exception:
            return start_time_str

    @staticmethod
    def get_morning_deadline_info(target_date: Optional[str] = None, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Calculates the 10:00 AM submission deadline and remaining seconds.
        Schedule locks at exactly 10:00 AM IST on target date.
        """
        tz = DailyPracticeTestEngine.get_tz()
        now_dt = current_dt or DailyPracticeTestEngine.now(tz)
        t_date_str = target_date or now_dt.strftime("%Y-%m-%d")

        try:
            d_parts = [int(p) for p in t_date_str.split("-")]
            deadline_dt = datetime(d_parts[0], d_parts[1], d_parts[2], MORNING_DEADLINE_HOUR, MORNING_DEADLINE_MINUTE, 0, tzinfo=tz)
        except Exception:
            deadline_dt = datetime(now_dt.year, now_dt.month, now_dt.day, MORNING_DEADLINE_HOUR, MORNING_DEADLINE_MINUTE, 0, tzinfo=tz)

        rem_sec = max(0, int((deadline_dt - now_dt).total_seconds()))
        is_locked = now_dt >= deadline_dt

        return {
            "date": t_date_str,
            "deadline_hour": MORNING_DEADLINE_HOUR,
            "deadline_minute": MORNING_DEADLINE_MINUTE,
            "deadline_display": "10:00 AM",
            "deadline_iso": deadline_dt.isoformat(),
            "now_iso": now_dt.isoformat(),
            "remaining_seconds": rem_sec,
            "is_locked": is_locked,
        }

    @staticmethod
    def validate_practice_tests(tests: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validates 2 to 4 tests for a daily practice schedule.
        - Rejects < 2 tests with error.
        - Rejects > 4 tests with error 'Maximum 4 tests allowed.'
        - Ensures test times are valid and detects overlaps.
        """
        if not tests or len(tests) < MIN_PRACTICE_TESTS:
            return {
                "valid": False,
                "error": f"At least {MIN_PRACTICE_TESTS} tests required (Quantitative Aptitude and Communication).",
                "conflicts": [f"Daily practice test scheduler requires at least {MIN_PRACTICE_TESTS} tests."],
            }

        if len(tests) > MAX_PRACTICE_TESTS:
            return {
                "valid": False,
                "error": "Maximum 4 tests allowed.",
                "conflicts": [f"Maximum 4 tests allowed. Received {len(tests)} tests."],
            }

        processed = []
        conflicts = []

        for idx, t in enumerate(tests):
            t_idx = idx + 1
            cat = str(t.get("category") or CATEGORY_CUSTOM).strip()
            title = str(t.get("title") or cat).strip()
            s_time = str(t.get("start_time") or "").strip()
            dur = int(t.get("duration_minutes") or 30)

            if not s_time:
                conflicts.append(f"Test #{t_idx} ('{title}'): Start time is required.")
                continue

            try:
                parts = s_time.split(":")
                sh, sm = int(parts[0]), int(parts[1])
                s_tot = sh * 60 + sm
                e_tot = s_tot + dur
                e_time = f"{(e_tot // 60) % 24:02d}:{e_tot % 60:02d}"
            except Exception as e:
                conflicts.append(f"Test #{t_idx} ('{title}'): Invalid start time '{s_time}' ({e}).")
                continue

            processed.append({
                "test_index": t_idx,
                "category": cat,
                "title": title,
                "start_time": s_time,
                "duration_minutes": dur,
                "end_time": e_time,
                "start_minutes": s_tot,
                "end_minutes": e_tot,
                "is_mandatory": idx < 2,
            })

        if conflicts:
            return {
                "valid": False,
                "error": "; ".join(conflicts),
                "conflicts": conflicts,
                "tests": processed,
            }

        # Pairwise overlap check between practice tests
        for i in range(len(processed)):
            for j in range(i + 1, len(processed)):
                p1 = processed[i]
                p2 = processed[j]
                if max(p1["start_minutes"], p2["start_minutes"]) < min(p1["end_minutes"], p2["end_minutes"]):
                    conflicts.append(
                        f"Practice test time overlap: Test #{p1['test_index']} ('{p1['title']}' {p1['start_time']}–{p1['end_time']}) "
                        f"overlaps with Test #{p2['test_index']} ('{p2['title']}' {p2['start_time']}–{p2['end_time']})."
                    )

        if conflicts:
            return {
                "valid": False,
                "error": "; ".join(conflicts),
                "conflicts": conflicts,
                "tests": processed,
            }

        return {
            "valid": True,
            "error": None,
            "conflicts": [],
            "tests": processed,
        }
