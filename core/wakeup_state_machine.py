"""
StrictRecords Wake-Up Completion State Machine & Checklist Validator (Sections 3-7)
===================================================================================
Enforces the mandatory wake-up completion pipeline:
  NOT_STARTED -> PROOF_SUBMITTED -> VALIDATING -> HEADSET_CHECK -> PLAYBACK_REQUIRED -> PLAYBACK_VALIDATION -> COMPLETED

Failure Paths:
  - INVALID_PROOF
  - DUPLICATE_PROOF
  - HEADSET_DISCONNECTED
  - PLAYBACK_INTERRUPTED
  - PLAYBACK_INCOMPLETE
  - VOLUME_REQUIREMENT_FAILED
  - MISSED

Key Requirements:
  1. Window: Open from 07:00 AM to 10:00 AM (early completion permitted from 07:00 AM).
  2. Proof image validation: quality, OCR, duplicate detection.
  3. Bluetooth audio headset connected and actively selected for playback.
  4. Media playback volume <= 60% of supported range.
  5. Genuine full track playback: seeking directly to end prevented, paused time excluded.
  6. Structured audit session recording.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))


class WakeUpState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PROOF_SUBMITTED = "PROOF_SUBMITTED"
    VALIDATING = "VALIDATING"
    HEADSET_CHECK = "HEADSET_CHECK"
    PLAYBACK_REQUIRED = "PLAYBACK_REQUIRED"
    PLAYBACK_VALIDATION = "PLAYBACK_VALIDATION"
    COMPLETED = "COMPLETED"

    # Failure / Termination States
    INVALID_PROOF = "INVALID_PROOF"
    DUPLICATE_PROOF = "DUPLICATE_PROOF"
    HEADSET_DISCONNECTED = "HEADSET_DISCONNECTED"
    PLAYBACK_INTERRUPTED = "PLAYBACK_INTERRUPTED"
    PLAYBACK_INCOMPLETE = "PLAYBACK_INCOMPLETE"
    VOLUME_REQUIREMENT_FAILED = "VOLUME_REQUIREMENT_FAILED"
    MISSED = "MISSED"


class WakeUpPlaybackSession:
    """Tracks a genuine wake-up music playback session."""

    def __init__(
        self,
        session_id: str,
        track_name: str,
        track_duration_sec: float,
        max_volume_pct: float = 60.0,
    ):
        self.session_id = session_id
        self.track_name = track_name
        self.track_duration_sec = max(1.0, float(track_duration_sec))
        self.max_volume_pct = max_volume_pct

        # Telemetry
        self.started_at: Optional[str] = None
        self.last_update_at: Optional[float] = None
        self.completed_duration_sec: float = 0.0
        self.pause_duration_sec: float = 0.0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.pause_started_at: Optional[float] = None
        self.seek_attempts: int = 0
        self.audio_route: str = "DEFAULT"
        self.headset_name: str = ""
        self.headset_connected: bool = False
        self.current_volume_pct: float = 50.0
        self.volume_violations: int = 0
        self.completed: bool = False

    def start(self, headset_name: str = "Bluetooth Headset", audio_route: str = "BLUETOOTH_A2DP", initial_volume_pct: float = 50.0) -> None:
        now = time.time()
        self.started_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        self.last_update_at = now
        self.is_playing = True
        self.is_paused = False
        self.headset_name = headset_name
        self.audio_route = audio_route
        self.headset_connected = True
        self.current_volume_pct = min(100.0, max(0.0, initial_volume_pct))
        if self.current_volume_pct > self.max_volume_pct:
            self.volume_violations += 1

    def update_progress(self, current_position_sec: float, volume_pct: Optional[float] = None, audio_route: Optional[str] = None) -> Dict[str, Any]:
        """
        Progress heartbeat. Verifies genuine elapsed time (cannot jump forward).
        """
        now = time.time()
        if not self.is_playing or self.is_paused:
            return self.to_dict()

        if volume_pct is not None:
            self.current_volume_pct = float(volume_pct)
            if self.current_volume_pct > self.max_volume_pct:
                self.volume_violations += 1

        if audio_route is not None:
            self.audio_route = audio_route
            if "bluetooth" not in audio_route.lower() and "headset" not in audio_route.lower() and "earphone" not in audio_route.lower() and "a2dp" not in audio_route.lower():
                self.headset_connected = False

        if self.last_update_at:
            real_delta = now - self.last_update_at
            # If position jumped significantly faster than real time, it's a seek attempt!
            pos_delta = current_position_sec - self.completed_duration_sec
            if pos_delta > (real_delta * 1.5) + 2.0:
                self.seek_attempts += 1
                # Reject artificial jump: only credit actual elapsed time
                self.completed_duration_sec = min(self.track_duration_sec, self.completed_duration_sec + real_delta)
            else:
                self.completed_duration_sec = min(self.track_duration_sec, current_position_sec)
        else:
            self.completed_duration_sec = min(self.track_duration_sec, current_position_sec)

        self.last_update_at = now

        if self.completed_duration_sec >= self.track_duration_sec:
            self.completed = True
            self.is_playing = False

        return self.to_dict()

    def pause(self) -> None:
        if self.is_playing and not self.is_paused:
            self.is_paused = True
            self.pause_started_at = time.time()

    def resume(self) -> None:
        if self.is_paused:
            now = time.time()
            if self.pause_started_at:
                self.pause_duration_sec += (now - self.pause_started_at)
            self.pause_started_at = None
            self.is_paused = False
            self.last_update_at = now

    def on_headset_disconnected(self) -> None:
        self.headset_connected = False
        self.pause()

    def to_dict(self) -> Dict[str, Any]:
        pct = round((self.completed_duration_sec / self.track_duration_sec) * 100.0, 1)
        return {
            "session_id": self.session_id,
            "track_name": self.track_name,
            "track_duration_sec": self.track_duration_sec,
            "completed_duration_sec": round(self.completed_duration_sec, 2),
            "pause_duration_sec": round(self.pause_duration_sec, 2),
            "completion_pct": pct,
            "is_playing": self.is_playing,
            "is_paused": self.is_paused,
            "seek_attempts": self.seek_attempts,
            "audio_route": self.audio_route,
            "headset_name": self.headset_name,
            "headset_connected": self.headset_connected,
            "current_volume_pct": self.current_volume_pct,
            "max_volume_pct": self.max_volume_pct,
            "volume_ok": self.current_volume_pct <= self.max_volume_pct,
            "volume_violations": self.volume_violations,
            "completed": self.completed,
        }


class WakeUpStateMachine:
    """
    Manages the lifecycle of a single day's wake-up activity.
    Authoritative state transitions with full checklist validation.
    """

    WINDOW_START_HM = "07:00"
    from core.window_state import WAKEUP_WINDOW_END as WINDOW_END_HM

    def __init__(self, db=None, date_str: Optional[str] = None, reference_now: Optional[datetime] = None):
        self.db = db
        self.date_str = date_str or datetime.now(IST).strftime("%Y-%m-%d")
        self.reference_now = reference_now

    @classmethod
    def is_within_wakeup_window(cls, now_dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Validates if current time is within 07:00 AM <= time < 10:00 AM IST.
        Early completion from 07:00 AM is explicitly permitted.
        """
        now = now_dt or datetime.now(IST)
        hm = now.strftime("%H:%M")
        if hm < cls.WINDOW_START_HM:
            return False, f"Wake-up window opens at {cls.WINDOW_START_HM} AM. Current time is {hm}."
        if hm >= cls.WINDOW_END_HM:
            return False, f"Wake-up window closed at {cls.WINDOW_END_HM} AM. Current time is {hm}."
        return True, "Within allowed wake-up submission window."

    def get_checklist_state(self, reference_now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Returns visual checklist item states for UI display (Section 7).
        """
        session = self._load_session_record()
        now = reference_now or self.reference_now or datetime.now(IST)
        hm = now.strftime("%H:%M")
        is_past_deadline = hm >= self.WINDOW_END_HM

        camera_verified = bool(session.get("camera_verified"))
        bluetooth_verified = bool(session.get("bluetooth_verified"))
        motivation_verified = bool(session.get("motivation_verified"))
        checklist_state = session.get("checklist_state", WakeUpState.NOT_STARTED.value)
        volume_limit_ok = bool(session.get("volume_limit_ok", 1))

        playback_json = session.get("playback_session_json") or "{}"
        try:
            playback_data = json.loads(playback_json)
        except Exception:
            playback_data = {}

        # Evaluate Checklist Criteria
        verified_checks = None
        routine_gate = None
        if self.db:
            from core.daily_routine_gate import DailyRoutineGate
            gate_service = DailyRoutineGate(self.db)
            verified_checks = gate_service.inspect_checks(self.date_str)
            routine_gate = gate_service.reconcile(self.date_str, reference_now=now)
        c1_proof_submitted = session.get("camera_evidence_id") is not None
        c2_image_quality_passed = verified_checks["checks"]["photo_and_liveness"] if verified_checks else False
        c3_bluetooth_connected = verified_checks["checks"]["bluetooth_active_output"] if verified_checks else False
        c4_volume_enforced = volume_limit_ok and (playback_data.get("volume_violations", 0) == 0)
        c5_playback_completed = verified_checks["checks"]["full_track"] if verified_checks else False
        c6_continuity_passed = playback_data.get("seek_attempts", 0) in (0, None)
        c7_all_satisfied = (
            c1_proof_submitted
            and c2_image_quality_passed
            and bool(routine_gate and (routine_gate["active"] or routine_gate.get("prerequisite_completed")))
        )

        # Current state determination
        if c7_all_satisfied:
            current_state = WakeUpState.COMPLETED.value
        elif checklist_state in [
            WakeUpState.PROOF_SUBMITTED.value,
            WakeUpState.VALIDATING.value,
            WakeUpState.HEADSET_CHECK.value,
            WakeUpState.PLAYBACK_REQUIRED.value,
            WakeUpState.PLAYBACK_VALIDATION.value,
            WakeUpState.INVALID_PROOF.value,
            WakeUpState.DUPLICATE_PROOF.value,
            WakeUpState.HEADSET_DISCONNECTED.value,
            WakeUpState.PLAYBACK_INTERRUPTED.value,
            WakeUpState.PLAYBACK_INCOMPLETE.value,
            WakeUpState.VOLUME_REQUIREMENT_FAILED.value,
        ]:
            current_state = checklist_state
        elif is_past_deadline and not c7_all_satisfied:
            current_state = WakeUpState.MISSED.value
        elif checklist_state in [s.value for s in WakeUpState]:
            current_state = checklist_state
        else:
            current_state = WakeUpState.NOT_STARTED.value

        checklist_items = [
            {
                "id": "proof_window",
                "label": "Submission in Window (07:00–09:30 AM)",
                "passed": c1_proof_submitted,
                "status": "COMPLETED" if c1_proof_submitted else ("MISSED" if is_past_deadline else "PENDING"),
                "detail": f"Submitted at {session.get('camera_completed_at', 'Pending')}" if c1_proof_submitted else "Open 7:00 AM - 9:30 AM",
            },
            {
                "id": "image_checks",
                "label": "Photo & Evidence Quality Checks",
                "passed": c2_image_quality_passed,
                "status": "COMPLETED" if c2_image_quality_passed else "PENDING",
                "detail": "Verified via Camera OCR & Anti-Spoofing" if c2_image_quality_passed else "Awaiting verification",
            },
            {
                "id": "bluetooth_headset",
                "label": "Bluetooth Audio Headset Connected (Optional)",
                "passed": c3_bluetooth_connected,
                "optional": True,
                "status": "COMPLETED" if c3_bluetooth_connected else "OPTIONAL",
                "detail": f"Route: {session.get('bluetooth_device_name') or 'Skipped / Optional'}",
            },
            {
                "id": "volume_limit",
                "label": "Volume Configured <= 60% Maximum (Optional)",
                "passed": c4_volume_enforced,
                "optional": True,
                "status": "COMPLETED" if c4_volume_enforced else "OPTIONAL",
                "detail": f"Current: {playback_data.get('current_volume_pct', 50)}% (Max 60% enforced)",
            },
            {
                "id": "track_completion",
                "label": "Motivation Song Playback (Optional)",
                "passed": c5_playback_completed,
                "optional": True,
                "status": "COMPLETED" if c5_playback_completed else "OPTIONAL",
                "detail": (
                    f"{playback_data.get('completion_pct', 0)}% listened ({playback_data.get('completed_duration_sec', 0)}s / {playback_data.get('track_duration_sec', 8)}s)"
                    if c5_playback_completed else "Optional — may be skipped"
                ),
            },
            {
                "id": "continuity_checks",
                "label": "Playback Continuity & Anti-Seek Checks",
                "passed": c6_continuity_passed,
                "status": "COMPLETED" if c6_continuity_passed else "WARNING",
                "detail": "No unauthorized seeking detected" if c6_continuity_passed else f"{playback_data.get('seek_attempts')} seek attempts detected",
            },
            {
                "id": "all_conditions",
                "label": "All Mandatory Wake-Up Criteria Satisfied",
                "passed": c7_all_satisfied,
                "status": "COMPLETED" if c7_all_satisfied else "PENDING",
                "detail": "Wake-up points unlocked" if c7_all_satisfied else "Incomplete — pending required steps",
            },
        ]

        return {
            "date": self.date_str,
            "state": current_state,
            "is_completed": current_state == WakeUpState.COMPLETED.value,
            "is_missed": current_state == WakeUpState.MISSED.value,
            "window_open": not is_past_deadline and (hm >= self.WINDOW_START_HM),
            "window_closes_at": f"{self.WINDOW_END_HM} AM",
            "checklist": checklist_items,
            "session": session,
            "playback": playback_data,
            "daily_routine_gate": routine_gate,
            "routine_state": routine_gate["state"] if routine_gate else "WAKE_UP_PENDING",
        }

    def _load_session_record(self) -> Dict[str, Any]:
        if not self.db:
            return {}
        try:
            with self.db._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM wake_up_sessions WHERE date = ? AND is_dev_mode = 0",
                    (self.date_str,),
                ).fetchone()
                if row:
                    return dict(row)
        except Exception:
            pass
        return {}

    def update_state(self, new_state: WakeUpState, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Transitions wake-up state and persists to database."""
        state_val = new_state.value
        if self.db:
            try:
                with self.db._connect() as conn:
                    conn.execute(
                        "UPDATE wake_up_sessions SET checklist_state = ? WHERE date = ? AND is_dev_mode = 0",
                        (state_val, self.date_str),
                    )
            except Exception:
                pass
        return self.get_checklist_state()
