"""
STRICTRECORDS – STRICT SESSION LIFECYCLE STATE MACHINE
Defines authoritative states for Strict Session proctoring and watchdog lifecycle gating:
- IDLE
- PREREQUISITE_CHECK
- SESSION_READY
- START_PENDING
- SESSION_ACTIVE
- SESSION_PAUSED
- SESSION_COMPLETING
- SESSION_COMPLETED
- SESSION_CANCELLED
- SESSION_FAILED

Mandatory iOS camera interruption monitoring and incident creation operates ONLY during SESSION_ACTIVE.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("StrictSessionLifecycle")


class StrictLifecycleState(str, Enum):
    IDLE = "IDLE"
    PREREQUISITE_CHECK = "PREREQUISITE_CHECK"
    SESSION_READY = "SESSION_READY"
    START_PENDING = "START_PENDING"
    SESSION_ACTIVE = "SESSION_ACTIVE"
    SESSION_PAUSED = "SESSION_PAUSED"
    SESSION_COMPLETING = "SESSION_COMPLETING"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    SESSION_FAILED = "SESSION_FAILED"
    SESSION_ENDING = "SESSION_ENDING"
    SESSION_RESET = "SESSION_RESET"

    @property
    def is_active(self) -> bool:
        """True ONLY when Strict Session has been confirmed and is actively running."""
        return self == StrictLifecycleState.SESSION_ACTIVE

    @property
    def allows_camera_watchdog(self) -> bool:
        """True ONLY during confirmed active session proctoring."""
        return self == StrictLifecycleState.SESSION_ACTIVE

    @property
    def is_pre_session(self) -> bool:
        """Informational states prior to session confirmation."""
        return self in (
            StrictLifecycleState.IDLE,
            StrictLifecycleState.PREREQUISITE_CHECK,
            StrictLifecycleState.SESSION_READY,
            StrictLifecycleState.START_PENDING,
        )

    @property
    def is_terminal(self) -> bool:
        """Finalized or aborted states."""
        return self in (
            StrictLifecycleState.SESSION_COMPLETED,
            StrictLifecycleState.SESSION_CANCELLED,
            StrictLifecycleState.SESSION_FAILED,
            StrictLifecycleState.SESSION_RESET,
        )


class StrictLifecycleController:
    """
    Authoritative state controller managing Strict Session lifecycle transitions
    and notifying subscribed monitors (e.g. iPad camera health watchdog).
    """

    def __init__(self, initial_state: StrictLifecycleState = StrictLifecycleState.IDLE):
        self._state = initial_state
        self._session_id: str = ""
        self._listeners: List[Callable[[StrictLifecycleState, Optional[str], Dict[str, Any]], None]] = []

    @property
    def state(self) -> StrictLifecycleState:
        return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    def subscribe(self, listener: Callable[[StrictLifecycleState, Optional[str], Dict[str, Any]], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[StrictLifecycleState, Optional[str], Dict[str, Any]], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def transition_to(
        self,
        new_state: StrictLifecycleState,
        session_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> StrictLifecycleState:
        old_state = self._state
        if session_id is not None:
            self._session_id = session_id
        elif new_state in (
            StrictLifecycleState.IDLE,
            StrictLifecycleState.SESSION_COMPLETED,
            StrictLifecycleState.SESSION_CANCELLED,
            StrictLifecycleState.SESSION_FAILED,
            StrictLifecycleState.SESSION_RESET,
        ):
            self._session_id = ""

        self._state = new_state
        logger.info(f"Strict session lifecycle transition: {old_state.value} -> {new_state.value} (session_id={self._session_id})")

        payload = details or {}
        for listener in list(self._listeners):
            try:
                listener(new_state, self._session_id or None, payload)
            except Exception as err:
                logger.debug(f"Error in lifecycle transition listener: {err}")

        return self._state
