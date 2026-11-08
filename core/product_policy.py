"""Versioned product constants shared with generated Android source; clients use the API."""
import json
import os
import sys
from pathlib import Path

DEFAULT_POLICY = {
    "version": "strict-routines-policy-20261025",
    "bath": {
        "points_per_occurrence": 0,
        "reservation_minutes": 60,
        "college": [
            {
                "occurrence": 1,
                "planned_time": "07:30",
                "window_start": "07:30",
                "window_end": "08:30",
            },
            {
                "occurrence": 2,
                "planned_time": "16:00",
                "window_start": "16:00",
                "window_end": "21:00",
            },
        ],
        "home": {
            "occurrence": 1,
            "planned_time": "13:00",
            "window_start": "13:00",
            "window_end": "17:30",
        },
    },
    "gym": {
        "activation_start_hour": 17,
        "cutoff_hour": 20,
        "cutoff_minute": 30,
        "music_duration_seconds": 12600,
        "max_lives": 3,
        "default_workout_minutes": 90,
        "proof_window_minutes": 30,
        "supported_workout_minutes": [45, 60, 75, 90, 120],
        "music_targets": ["spotify", "samsung_music"],
        "status_lease_seconds": 30,
        "expiry_mode": "DAILY_CUTOFF",
    },
}


def _resolve_policy() -> dict:
    candidates = []

    # 1. PyInstaller bundled temp directory (_MEIPASS)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "shared" / "product_policy.json")

    # 2. Relative to this module file
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "shared" / "product_policy.json")
    except Exception:
        pass

    # 3. Relative to sys.executable
    try:
        candidates.append(Path(sys.executable).resolve().parent / "shared" / "product_policy.json")
    except Exception:
        pass

    # 4. Current working directory
    try:
        candidates.append(Path.cwd() / "shared" / "product_policy.json")
    except Exception:
        pass

    for candidate in candidates:
        try:
            if candidate and candidate.is_file():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue

    return DEFAULT_POLICY


POLICY = _resolve_policy()
GYM = POLICY["gym"]
BATH = POLICY["bath"]

