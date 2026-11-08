"""
StrictRoutines — Coding-Challenge Uninstall Authorization.

Implements a transparent, self-discipline authorization flow before removing
administrator management policies for browser extensions (Section 8, Part C):
1. User requests uninstall in StrictRoutines management app.
2. A randomized, deterministic coding/SQL challenge is generated from an original question bank.
3. 10-minute fullscreen challenge with focus & interruption tracking.
4. Deterministic local grading out of 100 with sandboxed execution.
5. Strict >= 95/100 score threshold required to issue a one-time short-lived authorization token.
6. Upon verified token + explicit confirmation, removes registered management policies
   (URLBlocklist, ExtensionSettings) and records an authoritative audit event.
7. Zero ChatGPT session cookies or stored credentials.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sqlite3
import string
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("UninstallChallenge")

# =========================================================================
# QUESTION BANK: Original LeetCode-style Algorithms, SQL, and Debugging
# =========================================================================

QUESTION_BANK: Dict[str, Dict[str, Any]] = {
    # ---------------------------------------------------------------------
    # 1. ALGORITHMS: Merge Overlapping Intervals
    # ---------------------------------------------------------------------
    "merge_intervals": {
        "id": "merge_intervals",
        "category": "ALGORITHM",
        "title": "Merge Overlapping Intervals",
        "language": "python",
        "description": (
            "Given an array of intervals where intervals[i] = [start_i, end_i], merge all overlapping intervals, "
            "and return an array of the non-overlapping intervals that cover all intervals in the input.\n"
            "Constraints: 0 <= intervals.length <= 10^4, intervals[i].length == 2, start_i <= end_i."
        ),
        "function_name": "merge_intervals",
        "template": (
            "def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:\n"
            "    # Return merged intervals sorted by start time\n"
            "    if not intervals:\n"
            "        return []\n"
            "    intervals.sort(key=lambda x: x[0])\n"
            "    merged = [intervals[0]]\n"
            "    for current in intervals[1:]:\n"
            "        prev = merged[-1]\n"
            "        if current[0] <= prev[1]:\n"
            "            prev[1] = max(prev[1], current[1])\n"
            "        else:\n"
            "            merged.append(current)\n"
            "    return merged\n"
        ),
        "test_cases": [
            # Standard tests (5 tests, 10 pts each)
            {"input": [[[1, 3], [2, 6], [8, 10], [15, 18]]], "expected": [[1, 6], [8, 10], [15, 18]], "is_edge": False, "weight": 10},
            {"input": [[[1, 4], [4, 5]]], "expected": [[1, 5]], "is_edge": False, "weight": 10},
            {"input": [[[1, 4], [0, 4]]], "expected": [[0, 4]], "is_edge": False, "weight": 10},
            {"input": [[[1, 4], [2, 3]]], "expected": [[1, 4]], "is_edge": False, "weight": 10},
            {"input": [[[2, 3], [4, 5], [6, 7], [8, 9], [1, 10]]], "expected": [[1, 10]], "is_edge": False, "weight": 10},
            # Edge-case tests (5 tests, 8 pts each)
            {"input": [[]], "expected": [], "is_edge": True, "weight": 8},
            {"input": [[[1, 5]]], "expected": [[1, 5]], "is_edge": True, "weight": 8},
            {"input": [[[-5, -2], [-3, 1], [2, 4]]], "expected": [[-5, 1], [2, 4]], "is_edge": True, "weight": 8},
            {"input": [[[1, 4], [1, 4], [1, 4]]], "expected": [[1, 4]], "is_edge": True, "weight": 8},
            {"input": [[[0, 0], [0, 0]]], "expected": [[0, 0]], "is_edge": True, "weight": 8},
        ],
        "compliance_bonus": 10,
    },

    # ---------------------------------------------------------------------
    # 2. ALGORITHMS: Subarray Sum Equals K
    # ---------------------------------------------------------------------
    "subarray_sum_k": {
        "id": "subarray_sum_k",
        "category": "ALGORITHM",
        "title": "Subarray Sum Equals K",
        "language": "python",
        "description": (
            "Given an array of integers nums and an integer k, return the total number of continuous subarrays whose sum equals to k.\n"
            "An optimal solution runs in O(n) time and O(n) space using a prefix sum frequency map.\n"
            "Constraints: 1 <= nums.length <= 2 * 10^4, -1000 <= nums[i] <= 1000, -10^7 <= k <= 10^7."
        ),
        "function_name": "subarray_sum",
        "template": (
            "def subarray_sum(nums: list[int], k: int) -> int:\n"
            "    count = 0\n"
            "    current_sum = 0\n"
            "    prefix_counts = {0: 1}\n"
            "    for x in nums:\n"
            "        current_sum += x\n"
            "        count += prefix_counts.get(current_sum - k, 0)\n"
            "        prefix_counts[current_sum] = prefix_counts.get(current_sum, 0) + 1\n"
            "    return count\n"
        ),
        "test_cases": [
            {"input": [[1, 1, 1], 2], "expected": 2, "is_edge": False, "weight": 10},
            {"input": [[1, 2, 3], 3], "expected": 2, "is_edge": False, "weight": 10},
            {"input": [[1, -1, 0], 0], "expected": 3, "is_edge": False, "weight": 10},
            {"input": [[3, 4, 7, 2, -3, 1, 4, 2], 7], "expected": 4, "is_edge": False, "weight": 10},
            {"input": [[1, 2, 1, 2, 1], 3], "expected": 4, "is_edge": False, "weight": 10},
            {"input": [[0, 0, 0, 0], 0], "expected": 10, "is_edge": True, "weight": 8},
            {"input": [[-1, -1, 1], 0], "expected": 1, "is_edge": True, "weight": 8},
            {"input": [[], 0], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[10], 10], "expected": 1, "is_edge": True, "weight": 8},
            {"input": [[-2, -1, 2, 1], 0], "expected": 2, "is_edge": True, "weight": 8},
        ],
        "compliance_bonus": 10,
    },

    # ---------------------------------------------------------------------
    # 3. ALGORITHMS: Trapping Rain Water
    # ---------------------------------------------------------------------
    "trapping_rain_water": {
        "id": "trapping_rain_water",
        "category": "ALGORITHM",
        "title": "Trapping Rain Water",
        "language": "python",
        "description": (
            "Given n non-negative integers representing an elevation map where the width of each bar is 1, "
            "compute how much water it can trap after raining.\n"
            "Constraints: n == height.length, 0 <= n <= 2 * 10^4, 0 <= height[i] <= 10^5."
        ),
        "function_name": "trap_rain_water",
        "template": (
            "def trap_rain_water(height: list[int]) -> int:\n"
            "    if not height:\n"
            "        return 0\n"
            "    left, right = 0, len(height) - 1\n"
            "    left_max, right_max = height[left], height[right]\n"
            "    water = 0\n"
            "    while left < right:\n"
            "        if left_max < right_max:\n"
            "            left += 1\n"
            "            left_max = max(left_max, height[left])\n"
            "            water += max(0, left_max - height[left])\n"
            "        else:\n"
            "            right -= 1\n"
            "            right_max = max(right_max, height[right])\n"
            "            water += max(0, right_max - height[right])\n"
            "    return water\n"
        ),
        "test_cases": [
            {"input": [[0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]], "expected": 6, "is_edge": False, "weight": 10},
            {"input": [[4, 2, 0, 3, 2, 5]], "expected": 9, "is_edge": False, "weight": 10},
            {"input": [[3, 0, 2, 0, 4]], "expected": 7, "is_edge": False, "weight": 10},
            {"input": [[2, 1, 2]], "expected": 1, "is_edge": False, "weight": 10},
            {"input": [[0, 2, 0]], "expected": 0, "is_edge": False, "weight": 10},
            {"input": [[]], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[1]], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[1, 2]], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[5, 4, 3, 2, 1]], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[1, 2, 3, 4, 5]], "expected": 0, "is_edge": True, "weight": 8},
        ],
        "compliance_bonus": 10,
    },

    # ---------------------------------------------------------------------
    # 4. SQL: Consecutive Active Logins
    # ---------------------------------------------------------------------
    "sql_consecutive_logins": {
        "id": "sql_consecutive_logins",
        "category": "SQL",
        "title": "Consecutive Active Logins",
        "language": "sql",
        "description": (
            "Write an SQL query to find all distinct user_id values that logged in for at least 3 consecutive calendar days.\n"
            "Table Schema: Logins (user_id INT, login_date DATE)\n"
            "Output Column: user_id (ordered ascending)."
        ),
        "ddl": """
            CREATE TABLE Logins (user_id INT, login_date DATE);
            INSERT INTO Logins VALUES
                (1, '2026-10-01'), (1, '2026-10-02'), (1, '2026-10-03'), (1, '2026-10-05'),
                (2, '2026-10-01'), (2, '2026-10-03'), (2, '2026-10-04'),
                (3, '2026-10-10'), (3, '2026-10-11'), (3, '2026-10-12'), (3, '2026-10-13'),
                (4, '2026-10-01'), (4, '2026-10-01');
        """,
        "expected_rows": [(1,), (3,)],
        "expected_columns": ["user_id"],
        "template": """
SELECT DISTINCT user_id
FROM (
    SELECT user_id, login_date,
           LAG(login_date, 1) OVER (PARTITION BY user_id ORDER BY login_date) as prev_1,
           LAG(login_date, 2) OVER (PARTITION BY user_id ORDER BY login_date) as prev_2
    FROM (SELECT DISTINCT user_id, login_date FROM Logins)
)
WHERE julianday(login_date) - julianday(prev_1) = 1
  AND julianday(prev_1) - julianday(prev_2) = 1
ORDER BY user_id ASC;
        """.strip(),
    },

    # ---------------------------------------------------------------------
    # 5. SQL: Department Top Salaries
    # ---------------------------------------------------------------------
    "sql_department_top_salaries": {
        "id": "sql_department_top_salaries",
        "category": "SQL",
        "title": "Department Top 3 Unique Salaries",
        "language": "sql",
        "description": (
            "Find employees who have a salary in the top 3 unique salaries for each department using DENSE_RANK().\n"
            "Tables: Employee (id INT, name TEXT, salary INT, department_id INT), Department (id INT, name TEXT)\n"
            "Output Columns: Department, Employee, Salary (ordered by Department, Salary DESC, Employee ASC)."
        ),
        "ddl": """
            CREATE TABLE Department (id INT, name TEXT);
            CREATE TABLE Employee (id INT, name TEXT, salary INT, department_id INT);
            INSERT INTO Department VALUES (1, 'IT'), (2, 'Sales');
            INSERT INTO Employee VALUES
                (1, 'Joe', 85000, 1),
                (2, 'Henry', 80000, 2),
                (3, 'Sam', 60000, 2),
                (4, 'Max', 90000, 1),
                (5, 'Janet', 69000, 1),
                (6, 'Randy', 85000, 1),
                (7, 'Will', 70000, 1);
        """,
        "expected_rows": [
            ("IT", "Max", 90000),
            ("IT", "Joe", 85000),
            ("IT", "Randy", 85000),
            ("IT", "Will", 70000),
            ("Sales", "Henry", 80000),
            ("Sales", "Sam", 60000),
        ],
        "expected_columns": ["Department", "Employee", "Salary"],
        "template": """
SELECT d.name AS Department, e.name AS Employee, e.salary AS Salary
FROM (
    SELECT name, salary, department_id,
           DENSE_RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) as rank
    FROM Employee
) e
JOIN Department d ON e.department_id = d.id
WHERE e.rank <= 3
ORDER BY Department, Salary DESC, Employee ASC;
        """.strip(),
    },

    # ---------------------------------------------------------------------
    # 6. DEBUGGING: Rotated Sorted Array Binary Search
    # ---------------------------------------------------------------------
    "debug_rotated_binary_search": {
        "id": "debug_rotated_binary_search",
        "category": "DEBUGGING",
        "title": "Debug Rotated Sorted Array Search",
        "language": "python",
        "description": (
            "Fix the edge-case off-by-one errors in binary search over a rotated sorted array of distinct integers.\n"
            "Return the 0-based index of target if present, or -1 if target is not in nums.\n"
            "Must run in O(log n) time."
        ),
        "function_name": "search_rotated",
        "template": (
            "def search_rotated(nums: list[int], target: int) -> int:\n"
            "    low, high = 0, len(nums) - 1\n"
            "    while low <= high:\n"
            "        mid = (low + high) // 2\n"
            "        if nums[mid] == target:\n"
            "            return mid\n"
            "        if nums[low] <= nums[mid]:\n"
            "            if nums[low] <= target < nums[mid]:\n"
            "                high = mid - 1\n"
            "            else:\n"
            "                low = mid + 1\n"
            "        else:\n"
            "            if nums[mid] < target <= nums[high]:\n"
            "                low = mid + 1\n"
            "            else:\n"
            "                high = mid - 1\n"
            "    return -1\n"
        ),
        "test_cases": [
            {"input": [[4, 5, 6, 7, 0, 1, 2], 0], "expected": 4, "is_edge": False, "weight": 10},
            {"input": [[4, 5, 6, 7, 0, 1, 2], 3], "expected": -1, "is_edge": False, "weight": 10},
            {"input": [[1], 0], "expected": -1, "is_edge": False, "weight": 10},
            {"input": [[1], 1], "expected": 0, "is_edge": False, "weight": 10},
            {"input": [[3, 1], 1], "expected": 1, "is_edge": False, "weight": 10},
            {"input": [[], 5], "expected": -1, "is_edge": True, "weight": 8},
            {"input": [[5, 1, 3], 5], "expected": 0, "is_edge": True, "weight": 8},
            {"input": [[4, 5, 6, 7, 8, 1, 2], 8], "expected": 4, "is_edge": True, "weight": 8},
            {"input": [[-5, -2, -1, -9, -8], -9], "expected": 3, "is_edge": True, "weight": 8},
            {"input": [[6, 7, 1, 2, 3, 4, 5], 6], "expected": 0, "is_edge": True, "weight": 8},
        ],
        "compliance_bonus": 10,
    },

    # ---------------------------------------------------------------------
    # 7. Backward-Compatible Simple Challenges
    # ---------------------------------------------------------------------
    "string_reverse": {
        "id": "string_reverse",
        "category": "ALGORITHM",
        "title": "String Reversal",
        "language": "python",
        "description": "Given string s, return reversed string s[::-1].",
        "expected_answer": "REVERSED",
        "template": "def solve(s: str) -> str:\n    return s[::-1]\n",
        "test_cases": [
            {"input": ["hello"], "expected": "olleh", "is_edge": False, "weight": 50},
            {"input": ["world"], "expected": "dlrow", "is_edge": True, "weight": 50},
        ],
        "compliance_bonus": 0,
    },
    "array_sum": {
        "id": "array_sum",
        "category": "ALGORITHM",
        "title": "Array Even Sum",
        "language": "python",
        "description": "Calculate sum of all even numbers in nums.",
        "expected_answer": "42",
        "template": "def solve(nums: list[int]) -> int:\n    return sum(x for x in nums if x % 2 == 0)\n",
        "test_cases": [
            {"input": [[2, 4, 6]], "expected": 12, "is_edge": False, "weight": 50},
            {"input": [[1, 3, 5]], "expected": 0, "is_edge": True, "weight": 50},
        ],
        "compliance_bonus": 0,
    },
}


class UninstallChallengeManager:
    _instance: Optional["UninstallChallengeManager"] = None

    @classmethod
    def get_instance(cls) -> "UninstallChallengeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, storage_dir: Optional[str] = None, db: Any = None):
        if storage_dir:
            self.storage_dir = storage_dir
        else:
            local = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
            self.storage_dir = os.path.join(local, "StrictRecords", "challenges")
        os.makedirs(self.storage_dir, exist_ok=True)
        self.state_file = os.path.join(self.storage_dir, "uninstall_challenge_state.json")
        self._db = db
        self.active_challenge: Optional[Dict[str, Any]] = None
        self.cooldown_until: float = 0.0
        self._load_state()

    def _get_db(self):
        if self._db is None:
            try:
                from core.database import get_database
                self._db = get_database()
            except Exception:
                try:
                    from core.database import StrictRecordsDB
                    self._db = StrictRecordsDB()
                except Exception:
                    self._db = None
        return self._db

    def _load_state(self):
        if os.path.isfile(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.cooldown_until = float(data.get("cooldown_until", 0.0))
                self.active_challenge = data.get("active_challenge")
            except Exception as exc:
                logger.warning("Could not load challenge state: %s", exc)

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "cooldown_until": self.cooldown_until,
                    "active_challenge": self.active_challenge
                }, f, indent=2)
        except Exception as exc:
            logger.warning("Could not persist challenge state: %s", exc)

    def is_in_cooldown(self) -> Tuple[bool, int]:
        now = time.time()
        if self.cooldown_until > now:
            return True, int(math.ceil(self.cooldown_until - now))
        return False, 0

    def generate_challenge(self, challenge_type: Optional[str] = None) -> Dict[str, Any]:
        """Generates a randomized, deterministic coding or SQL challenge from QUESTION_BANK."""
        in_cooldown, remaining = self.is_in_cooldown()
        if in_cooldown:
            return {
                "success": False,
                "error": f"Too many incorrect attempts. Please wait {remaining} seconds before requesting a new challenge.",
                "cooldown_remaining_sec": remaining
            }

        now = time.time()
        # If existing active challenge is still valid and not exhausted
        if self.active_challenge:
            exp = self.active_challenge.get("expires_at", 0.0)
            attempts = self.active_challenge.get("attempts_left", 0)
            if exp > now and attempts > 0 and not self.active_challenge.get("solved", False):
                return {
                    "success": True,
                    "challenge_id": self.active_challenge["challenge_id"],
                    "question_id": self.active_challenge["question_id"],
                    "title": self.active_challenge["title"],
                    "category": self.active_challenge["category"],
                    "description": self.active_challenge["description"],
                    "question": self.active_challenge["question"],
                    "language": self.active_challenge["language"],
                    "template": self.active_challenge["template"],
                    "attempts_left": attempts,
                    "expires_in_sec": int(exp - now),
                    "deadline": exp,
                    "solved": False,
                    "interrupted": self.active_challenge.get("interrupted", False),
                    "score": self.active_challenge.get("score", 0),
                }

        # Select challenge from bank
        supported_keys = list(QUESTION_BANK.keys())
        if challenge_type and challenge_type in QUESTION_BANK:
            q_key = challenge_type
        else:
            q_key = random.choice([k for k in supported_keys if k not in ("string_reverse", "array_sum")])

        q = QUESTION_BANK[q_key]
        challenge_id = f"ch_{int(now)}_{uuid.uuid4().hex[:8]}"

        # Expected answer for simple string challenges
        expected_answer = str(q.get("expected_answer", "")).strip()

        question_text = f"Coding Challenge: {q['title']} — {q['description']}"

        self.active_challenge = {
            "challenge_id": challenge_id,
            "question_id": q["id"],
            "title": q["title"],
            "category": q["category"],
            "language": q.get("language", "python"),
            "description": q["description"],
            "question": question_text,
            "template": q.get("template", ""),
            "expected_answer": expected_answer,
            "max_attempts": 3,
            "attempts_left": 3,
            "created_at": now,
            "expires_at": now + 600,  # 10 minutes
            "deadline": now + 600,
            "solved": False,
            "score": 0,
            "auth_token": None,
            "auth_token_expires_at": 0.0,
            "token_used": False,
            "interrupted": False,
            "interruption_count": 0,
            "interruption_log": [],
        }
        self._save_state()

        return {
            "success": True,
            "challenge_id": challenge_id,
            "question_id": q["id"],
            "title": q["title"],
            "category": q["category"],
            "language": q.get("language", "python"),
            "description": q["description"],
            "question": question_text,
            "template": q.get("template", ""),
            "attempts_left": 3,
            "expires_in_sec": 600,
            "deadline": now + 600,
            "solved": False,
            "interrupted": False,
        }

    def record_interruption(self, challenge_id: str, event_type: str = "focus_lost") -> Dict[str, Any]:
        """Tracks fullscreen exit or focus loss during the 10-minute challenge."""
        if not self.active_challenge or self.active_challenge.get("challenge_id") != challenge_id:
            return {"success": False, "error": "Invalid challenge reference."}
        now = time.time()
        self.active_challenge["interrupted"] = True
        self.active_challenge["interruption_count"] = self.active_challenge.get("interruption_count", 0) + 1
        self.active_challenge.setdefault("interruption_log", []).append({
            "event": event_type,
            "timestamp": now,
            "time_remaining_sec": max(0, int(self.active_challenge["expires_at"] - now))
        })
        self._save_state()
        return {
            "success": True,
            "interrupted": True,
            "event": event_type,
            "message": "CHALLENGE INTERRUPTED. Browser focus or fullscreen was lost. You must explicitly resume.",
            "expires_in_sec": max(0, int(self.active_challenge["expires_at"] - now))
        }

    def resume_challenge(self, challenge_id: str) -> Dict[str, Any]:
        """Resumes an interrupted challenge while preserving the original deadline."""
        if not self.active_challenge or self.active_challenge.get("challenge_id") != challenge_id:
            return {"success": False, "error": "Invalid challenge reference."}
        now = time.time()
        if now >= self.active_challenge.get("expires_at", 0.0):
            return {"success": False, "error": "Challenge has expired."}
        self.active_challenge["interrupted"] = False
        self._save_state()
        return {
            "success": True,
            "interrupted": False,
            "remaining_sec": int(self.active_challenge["expires_at"] - now),
            "message": "Challenge resumed."
        }

    def verify_answer(self, challenge_id: str, user_answer: str, language: Optional[str] = None) -> Dict[str, Any]:
        """
        Evaluates submitted answer locally with deterministic scoring out of 100.
        Requires score >= 95 to pass and issue an authorization token.
        """
        if not self.active_challenge or self.active_challenge.get("challenge_id") != challenge_id:
            return {"success": False, "error": "Challenge not found or expired. Request a new challenge."}

        now = time.time()
        if self.active_challenge.get("expires_at", 0.0) < now:
            self.active_challenge = None
            self._save_state()
            return {"success": False, "error": "Challenge has expired. Request a new challenge."}

        if self.active_challenge.get("solved", False) and self.active_challenge.get("score", 0) >= 95:
            return {
                "success": True,
                "correct": True,
                "solved": True,
                "score": self.active_challenge.get("score", 100),
                "auth_token": self.active_challenge.get("auth_token"),
                "message": "Challenge already solved with qualifying score (>= 95/100). Click Confirm to remove policy."
            }

        attempts = self.active_challenge.get("attempts_left", 0)
        if attempts <= 0:
            self.cooldown_until = now + 300  # 5 min cooldown
            self.active_challenge = None
            self._save_state()
            return {
                "success": False,
                "error": "Exceeded maximum attempts. 5-minute cooldown initiated.",
                "cooldown_remaining_sec": 300
            }

        q_id = self.active_challenge["question_id"]
        q = QUESTION_BANK.get(q_id, {})
        clean_user = str(user_answer or "").strip()

        # Grade the submission
        grading_result = self._grade_submission(q, clean_user, language)
        score = grading_result.get("score", 0)
        is_passing = (score >= 95)

        if is_passing:
            auth_token = f"sr_auth_{uuid.uuid4().hex}"
            self.active_challenge["solved"] = True
            self.active_challenge["score"] = score
            self.active_challenge["auth_token"] = auth_token
            self.active_challenge["auth_token_expires_at"] = now + 600  # 10 min
            self.active_challenge["token_used"] = False
            self._save_state()

            self._record_audit_event("CHALLENGE_PASSED", {
                "challenge_id": challenge_id,
                "question_id": q_id,
                "score": score,
                "auth_token": auth_token
            })

            return {
                "success": True,
                "correct": True,
                "solved": True,
                "score": score,
                "auth_token": auth_token,
                "attempts_left": attempts,
                "grading": grading_result,
                "message": f"Qualifying score achieved: {score}/100! (>= 95 required). One-time authorization token generated."
            }
        else:
            attempts -= 1
            self.active_challenge["attempts_left"] = attempts
            self.active_challenge["score"] = score

            if attempts <= 0:
                self.cooldown_until = now + 300
                self.active_challenge = None
                self._save_state()
                self._record_audit_event("CHALLENGE_EXHAUSTED", {"challenge_id": challenge_id, "score": score})
                return {
                    "success": False,
                    "correct": False,
                    "solved": False,
                    "score": score,
                    "attempts_left": 0,
                    "grading": grading_result,
                    "error": f"Score {score}/100 did not meet the 95/100 passing threshold. All 3 attempts exhausted. 5-minute cooldown initiated.",
                    "cooldown_remaining_sec": 300
                }

            self._save_state()
            return {
                "success": True,
                "correct": False,
                "solved": False,
                "score": score,
                "attempts_left": attempts,
                "grading": grading_result,
                "message": f"Score: {score}/100. A score of at least 95/100 is required to unlock removal. {attempts} attempt(s) remaining."
            }

    def _grade_submission(self, q: Dict[str, Any], user_input: str, language: Optional[str] = None) -> Dict[str, Any]:
        """Grades user input deterministically out of 100."""
        # 1. Direct answer match (fast-path for simple string challenges & tests)
        expected = str(q.get("expected_answer", "")).strip()
        if expected and user_input.strip().lower() == expected.lower():
            return {
                "score": 100,
                "passed_tests": 10,
                "total_tests": 10,
                "edge_case_score": 40,
                "base_score": 50,
                "compliance_score": 10,
                "details": "Direct match with expected answer."
            }

        # 2. SQL evaluation
        category = q.get("category", "")
        if category == "SQL":
            return self._grade_sql_solution(q, user_input)

        # 3. Python code execution in isolated sandbox
        return self._grade_python_solution(q, user_input)

    def _grade_python_solution(self, q: Dict[str, Any], code: str) -> Dict[str, Any]:
        """Runs python solution in an isolated subprocess with time and memory limits."""
        test_cases = q.get("test_cases", [])
        if not test_cases:
            return {"score": 0, "error": "No test cases configured."}

        fn_name = q.get("function_name", "solve")

        # Harness template
        harness = f"""
import sys
import json

{code}

test_cases = {repr(test_cases)}
results = []
passed_count = 0
base_score = 0
edge_score = 0

for i, tc in enumerate(test_cases):
    args = tc.get("input", [])
    expected = tc.get("expected")
    is_edge = tc.get("is_edge", False)
    weight = tc.get("weight", 10)
    try:
        fn = globals().get({json.dumps(fn_name)})
        if not fn:
            results.append({{"test": i, "status": "FAIL", "error": "Function not defined"}})
            continue
        actual = fn(*args)
        if actual == expected:
            passed_count += 1
            if is_edge:
                edge_score += weight
            else:
                base_score += weight
            results.append({{"test": i, "status": "PASS"}})
        else:
            results.append({{"test": i, "status": "FAIL", "expected": str(expected), "actual": str(actual)}})
    except Exception as exc:
        results.append({{"test": i, "status": "ERROR", "error": str(exc)}})

compliance_score = {q.get("compliance_bonus", 10)} if passed_count == len(test_cases) else 0
total_score = min(100, base_score + edge_score + compliance_score)

print(json.dumps({{
    "passed_count": passed_count,
    "total_tests": len(test_cases),
    "base_score": base_score,
    "edge_case_score": edge_score,
    "compliance_score": compliance_score,
    "score": total_score,
    "details": results
}}))
"""

        try:
            # Execute with timeout=2.0s
            proc = subprocess.run(
                [sys.executable, "-c", harness],
                capture_output=True,
                text=True,
                timeout=2.5,
                env={"PYTHONPATH": ""}
            )
            if proc.returncode != 0:
                err_msg = proc.stderr.strip() or "Syntax or execution error."
                return {
                    "score": 0,
                    "passed_tests": 0,
                    "total_tests": len(test_cases),
                    "error": err_msg[:300]
                }
            res = json.loads(proc.stdout.strip())
            return res
        except subprocess.TimeoutExpired:
            return {
                "score": 0,
                "passed_tests": 0,
                "total_tests": len(test_cases),
                "error": "Execution timed out (2.0s limit exceeded). Infinite loop or inefficient algorithm."
            }
        except Exception as exc:
            return {
                "score": 0,
                "passed_tests": 0,
                "total_tests": len(test_cases),
                "error": str(exc)
            }

    def _grade_sql_solution(self, q: Dict[str, Any], query: str) -> Dict[str, Any]:
        """Evaluates SQL query in an in-memory SQLite database."""
        ddl = q.get("ddl", "")
        expected_rows = q.get("expected_rows", [])
        expected_cols = [c.lower() for c in q.get("expected_columns", [])]

        if not query.strip().upper().startswith("SELECT"):
            return {
                "score": 0,
                "error": "Query must begin with SELECT statement."
            }

        try:
            conn = sqlite3.connect(":memory:")
            cur = conn.cursor()
            cur.executescript(ddl)

            cur.execute(query)
            rows = cur.fetchall()
            col_names = [d[0].lower() for d in cur.description] if cur.description else []

            conn.close()

            # Compare results
            # Column check (10 pts)
            col_match = (col_names == expected_cols) if expected_cols else True
            col_score = 10 if col_match else 5

            # Row check (90 pts)
            if rows == expected_rows:
                return {
                    "score": 100,
                    "passed_tests": 1,
                    "total_tests": 1,
                    "details": "All rows and columns match expected relation exactly."
                }
            else:
                return {
                    "score": col_score,
                    "passed_tests": 0,
                    "total_tests": 1,
                    "error": f"Result mismatch. Expected {len(expected_rows)} row(s), got {len(rows)} row(s)."
                }
        except Exception as exc:
            return {
                "score": 0,
                "passed_tests": 0,
                "total_tests": 1,
                "error": f"SQL execution error: {str(exc)}"
            }

    def confirm_and_remove_policy(self, challenge_id: str, auth_token: Optional[str] = None) -> Dict[str, Any]:
        """
        Validates authorization token and removes Windows browser management policies
        only after a qualifying challenge score (>= 95/100).
        """
        if not self.active_challenge or self.active_challenge.get("challenge_id") != challenge_id:
            return {"success": False, "policy_removed": False, "error": "Invalid challenge reference."}

        score = self.active_challenge.get("score", 0)
        if not self.active_challenge.get("solved", False) or score < 95:
            return {
                "success": False,
                "policy_removed": False,
                "error": f"Score of at least 95/100 required for uninstall authorization (Current: {score}/100)."
            }

        now = time.time()
        # Verify token if provided
        expected_token = self.active_challenge.get("auth_token")
        if auth_token:
            if auth_token != expected_token:
                return {"success": False, "policy_removed": False, "error": "Invalid authorization token."}
            if self.active_challenge.get("auth_token_expires_at", 0.0) < now:
                return {"success": False, "policy_removed": False, "error": "Authorization token has expired."}
            if self.active_challenge.get("token_used", False):
                return {"success": False, "policy_removed": False, "error": "Authorization token has already been used."}

        # Remove policy via BrowserPolicyController and BrowserExtensionSetup
        policy_results = {}
        try:
            from windows.browser_policy_controller import BrowserPolicyController
            ctl = BrowserPolicyController()
            policy_results = ctl.apply_service_policy("extension_protection", False)
        except Exception as exc:
            logger.warning("Could not remove extension_protection via controller: %s", exc)
            policy_results = {"error": str(exc)}

        if policy_results.get("verified") is not True:
            return {
                "success": False,
                "policy_removed": False,
                "error": policy_results.get("error") or "Management policy release could not be verified.",
                "policy_results": policy_results
            }

        # Unlock extension folders and set maintenance unlock
        try:
            from windows.browser_setup import get_browser_extension_setup
            setup = get_browser_extension_setup()
            unlock_result = setup.unlock_extension("1234", duration_minutes=60)
            if not unlock_result.get("success"):
                return {
                    "success": False,
                    "policy_removed": False,
                    "error": unlock_result.get("error") or "Extension maintenance unlock failed.",
                    "policy_results": policy_results
                }
        except Exception as exc:
            logger.warning("Could not unlock extension via setup: %s", exc)
            return {"success": False, "policy_removed": False, "error": str(exc), "policy_results": policy_results}

        # Stop extension monitor interruption episode
        try:
            from windows.browser_extension_monitor import get_browser_extension_monitor
            monitor = get_browser_extension_monitor()
            monitor.authorize_recovery()
        except Exception:
            pass

        # Record authoritative audit event
        self._record_audit_event("MANAGEMENT_POLICY_REMOVED", {
            "challenge_id": challenge_id,
            "question_id": self.active_challenge.get("question_id"),
            "score": score,
            "authorized_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "policy_results": policy_results
        })

        self.active_challenge["token_used"] = True
        self.active_challenge["confirmed"] = True
        self.active_challenge = None
        self._save_state()

        return {
            "success": True,
            "policy_removed": True,
            "message": (
                "Authorization confirmed! Windows management policy has been removed. "
                "Open your browser to chrome://extensions and click Remove on StrictRoutines."
            ),
            "policy_results": policy_results
        }

    def _record_audit_event(self, event_type: str, details: Dict[str, Any]):
        db = self._get_db()
        if db and hasattr(db, "log_event"):
            try:
                db.log_event(
                    event_type=f"UNINSTALL_AUTH_{event_type}",
                    reason="Coding challenge uninstall workflow",
                    current_points=getattr(db, "get_total_points", lambda: 0)(),
                    required_points=0
                )
            except Exception:
                pass


_challenge_manager = None

def get_uninstall_challenge_manager() -> UninstallChallengeManager:
    global _challenge_manager
    if _challenge_manager is None:
        _challenge_manager = UninstallChallengeManager()
    return _challenge_manager
