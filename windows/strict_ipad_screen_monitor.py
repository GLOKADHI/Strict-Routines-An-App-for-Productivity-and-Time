"""

STRICTRECORDS – iPAD SCREEN SHARING MONITOR & STRUCTURAL ACTIVITY DETECTOR

Third independent monitoring source alongside Windows Webcam and iOS Front Camera.



Responsibilities:

1. Screen Sharing WebRTC Track Receiver & Ingestion (IOS_FULL_SCREEN_SHARE / IOS_SCREEN_SHARE).

   Strictly separated from IOS_FRONT_CAMERA — front-camera frames can never satisfy screen sharing.

2. 60-Second Initial Negotiation Grace Period during prerequisite checks:

   - Displays SCREEN SHARE NEGOTIATING with live countdown (Waiting for first valid frame: MM:SS remaining).

   - Suppresses false interruption alerts and session violations during negotiation.

   - Requires >= 3.0 seconds of stable decoded frames entering the Windows screen-monitoring pipeline

     before marking IOS_SCREEN_SHARE: VERIFIED and cancelling the 60-second timer.

   - Transitions to IOS_SCREEN_SHARE_FAILED if no valid screen frames arrive within 60 seconds.

3. Frame decoding, rate estimation (Rx FPS, Decoded FPS, Processing FPS).

4. Structural change detection via Mean Absolute Difference (MAD):

   - MAD < 2.0        => STATIC (reading documentation, code, or still text)

   - 2.0 <= MAD < 25.0 => ACTIVE (scrolling, typing, navigating)

   - MAD >= 25.0       => RAPID_TRANSITION (app switching, video playback)

5. Independent Active-Session Interruption Watchdog (10.0s gap threshold):

   - Activated only after Strict Session transitions to SESSION_ACTIVE.

   - Emits IOS_SCREEN_SHARE_INTERRUPTED without affecting front camera or webcam.

   - Verifies recovery after 3.0s of continuous stable frames.

6. Non-blocking frame queue with bounded memory and preview JPEG generation.

"""



from __future__ import annotations



import base64

import collections

import logging

import math

import threading

import time

import uuid

from typing import Any, Callable, Dict, List, Optional, Tuple



import cv2

import numpy as np



logger = logging.getLogger("IPadScreenShareMonitor")





class IPadScreenShareState:

    # Dashboard states

    NOT_CONFIGURED = "NOT_CONFIGURED"

    WAITING_FOR_PERMISSION = "WAITING_FOR_PERMISSION"

    NEGOTIATING = "SCREEN SHARE NEGOTIATING"

    STARTING = "STARTING"

    VERIFIED = "IOS_SCREEN_SHARE: VERIFIED"

    LIVE = "LIVE"

    TEMPORARILY_PAUSED = "TEMPORARILY_PAUSED"

    INTERRUPTED = "INTERRUPTED"

    RECOVERING = "RECOVERING"

    FAILED = "IOS_SCREEN_SHARE_FAILED"

    STOPPED = "STOPPED"



    SCREEN_SHARE_STOPPED = "SCREEN_SHARE_STOPPED"

    SCREEN_SHARE_PENDING = "SCREEN_SHARE_PENDING"

    SCREEN_SHARE_NEGOTIATING = "SCREEN_SHARE_NEGOTIATING"

    SCREEN_SHARE_CONNECTING = "SCREEN_SHARE_CONNECTING"

    SCREEN_SHARE_VERIFIED = "IOS_SCREEN_SHARE_VERIFIED"

    SCREEN_SHARE_STREAMING = "SCREEN_SHARE_STREAMING"

    SCREEN_SHARE_FAILED = "IOS_SCREEN_SHARE_FAILED"

    SCREEN_SHARE_INTERRUPTED = "IOS_SCREEN_SHARE_INTERRUPTED"

    SCREEN_SHARE_RECOVERED = "SCREEN_SHARE_RECOVERED"



    # Activity classification

    ACTIVITY_STATIC = "STATIC"

    ACTIVITY_ACTIVE = "ACTIVE"

    ACTIVITY_RAPID_TRANSITION = "RAPID_TRANSITION"



    # Thresholds

    MAD_STATIC_MAX: float = 2.0

    MAD_ACTIVE_MAX: float = 25.0



    NEGOTIATION_GRACE_SEC: float = 60.0

    VERIFICATION_STABLE_SEC: float = 3.0

    STALL_THRESHOLD_SEC: float = 10.0

    RECOVERY_STABLE_SEC: float = 3.0





class IPadScreenShareMonitor:

    """

    Dedicated continuous health monitor and activity analyzer for full-device iPad screen sharing

    (IOS_FULL_SCREEN_SHARE). Strictly independent from IOS_FRONT_CAMERA and Windows webcam pipelines.

    """



    _instance: Optional[IPadScreenShareMonitor] = None

    _instance_lock = threading.Lock()



    @classmethod

    def get_instance(cls) -> IPadScreenShareMonitor:

        with cls._instance_lock:

            if cls._instance is None:

                cls._instance = cls()

            return cls._instance



    @classmethod

    def reset_instance(cls) -> None:

        with cls._instance_lock:

            if cls._instance is not None:

                cls._instance.reset()

                cls._instance = None



    def __init__(

        self,

        on_interruption: Optional[Callable[[Dict[str, Any]], None]] = None,

        on_recovery: Optional[Callable[[Dict[str, Any]], None]] = None,

    ):

        self._lock = threading.RLock()

        self.on_interruption = on_interruption

        self.on_recovery = on_recovery



        self._preparation_id: Optional[str] = None

        self._session_id: Optional[str] = None

        self._session_active: bool = False



        self._track_id: Optional[str] = None

        self._webrtc_connected: bool = False

        self._is_active: bool = False

        self._is_streaming: bool = False

        self._is_interrupted: bool = False

        self._state: str = IPadScreenShareState.SCREEN_SHARE_STOPPED



        # 60-Second Initial Negotiation & 3.0-Second Stability Verification

        self._negotiation_started_mono: Optional[float] = None

        self._negotiation_started_epoch: Optional[float] = None

        self._stable_start_mono: Optional[float] = None

        self._stable_frame_count: int = 0

        self._initial_verified: bool = False

        self._verified_at_mono: Optional[float] = None

        self._verified_at_epoch: Optional[float] = None

        self._negotiation_failed: bool = False

        self._failure_reason: Optional[str] = None



        # Sequence and Timestamps

        self._sequence: int = 0

        self._last_frame_mono: Optional[float] = None

        self._last_frame_epoch: Optional[float] = None

        self._first_frame_mono: Optional[float] = None

        self._recovery_start_mono: Optional[float] = None



        # Frame rate calculation (sliding windows)

        self._rx_timestamps: collections.deque = collections.deque(maxlen=60)

        self._dec_timestamps: collections.deque = collections.deque(maxlen=60)

        self._proc_timestamps: collections.deque = collections.deque(maxlen=60)



        # Metrics

        self._rx_fps: float = 0.0

        self._decoded_fps: float = 0.0

        self._proc_fps: float = 0.0

        self._resolution: Tuple[int, int] = (0, 0)

        self._dropped_frames: int = 0



        # Change detection & activity analysis

        self._prev_gray_small: Optional[np.ndarray] = None

        self._last_mad: float = 0.0

        self._activity_level: str = IPadScreenShareState.ACTIVITY_STATIC

        self._recent_activities: collections.deque = collections.deque(maxlen=30)

        self._rapid_transition_count: int = 0



        # Interruption tracking (active session only)

        self._incidents: List[Dict[str, Any]] = []

        self._current_incident: Optional[Dict[str, Any]] = None



        # Preview JPEG thumbnail

        self._preview_jpeg: Optional[str] = None

        self._last_preview_gen_mono: float = 0.0



    def reset(self) -> None:

        with self._lock:

            self._preparation_id = None

            self._session_id = None

            self._session_active = False

            self._track_id = None

            self._webrtc_connected = False

            self._is_active = False

            self._is_streaming = False

            self._is_interrupted = False

            self._state = IPadScreenShareState.SCREEN_SHARE_STOPPED

            self._negotiation_started_mono = None

            self._negotiation_started_epoch = None

            self._stable_start_mono = None

            self._stable_frame_count = 0

            self._initial_verified = False

            self._verified_at_mono = None

            self._verified_at_epoch = None

            self._negotiation_failed = False

            self._failure_reason = None

            self._sequence = 0

            self._last_frame_mono = None

            self._last_frame_epoch = None

            self._first_frame_mono = None

            self._recovery_start_mono = None

            self._rx_timestamps.clear()

            self._dec_timestamps.clear()

            self._proc_timestamps.clear()

            self._rx_fps = 0.0

            self._decoded_fps = 0.0

            self._proc_fps = 0.0

            self._resolution = (0, 0)

            self._dropped_frames = 0

            self._prev_gray_small = None

            self._last_mad = 0.0

            self._activity_level = IPadScreenShareState.ACTIVITY_STATIC

            self._recent_activities.clear()

            self._rapid_transition_count = 0

            self._incidents.clear()

            self._current_incident = None

            self._preview_jpeg = None
            try:
                from windows.strict_ios_media_health import IOSMediaHealthManager, SOURCE_SCREEN_SHARE
                IOSMediaHealthManager.get_instance().record_explicit_stop(
                    SOURCE_SCREEN_SHARE,
                    {"reason": reason or "iPad full-screen sharing stopped", "event_id": kwargs.get("event_id"), "user_initiated": kwargs.get("user_initiated", True)}
                )
            except Exception:
                pass

            self._last_preview_gen_mono = 0.0



    def start_negotiation(

        self,

        preparation_id: Optional[str] = None,

        now_mono: Optional[float] = None,

        force_restart: bool = False,

    ) -> Dict[str, Any]:

        """

        Begins or retries the 60-second initial iOS full-screen share negotiation window.

        During this 60s window, no interruption alerts or failures are triggered while waiting

        for the user to approve the iPad broadcast prompt and establish WebRTC media.

        """

        now_m = now_mono if now_mono is not None else time.monotonic()

        with self._lock:

            if preparation_id and self._preparation_id and self._preparation_id != preparation_id:

                force_restart = True

            if preparation_id:

                self._preparation_id = preparation_id



            if force_restart or self._negotiation_failed or not self._initial_verified:

                self._negotiation_failed = False

                self._failure_reason = None

                self._is_interrupted = False

                if force_restart or self._negotiation_started_mono is None:

                    self._negotiation_started_mono = now_m

                    self._negotiation_started_epoch = time.time()

                if force_restart:

                    self._initial_verified = False

                    self._stable_start_mono = None

                    self._stable_frame_count = 0

                    self._last_frame_mono = None

                    self._is_streaming = False

                if not self._initial_verified:

                    self._state = IPadScreenShareState.SCREEN_SHARE_NEGOTIATING

                logger.info(

                    f"iPad Full-Screen Share 60s negotiation started (preparation_id={self._preparation_id})"

                )

            return self.get_telemetry(now_mono=now_m)



    def start_track(

        self,

        track_id: str,

        preparation_id: Optional[str] = None,

        now_mono: Optional[float] = None,

    ) -> None:

        now_m = now_mono if now_mono is not None else time.monotonic()

        with self._lock:

            if preparation_id:

                self._preparation_id = preparation_id

            self._track_id = track_id

            self._webrtc_connected = True

            self._is_active = True

            self._negotiation_failed = False

            self._failure_reason = None

            if not self._initial_verified:

                if self._negotiation_started_mono is None:

                    self._negotiation_started_mono = now_m

                    self._negotiation_started_epoch = time.time()

                self._state = IPadScreenShareState.SCREEN_SHARE_NEGOTIATING

            else:

                self._state = IPadScreenShareState.SCREEN_SHARE_CONNECTING

            logger.info(f"iPad Full-Screen Share track initiated: {track_id}")



    def stop_track(self, reason: Optional[str] = None, **kwargs) -> None:

        with self._lock:

            self._is_active = False

            self._is_streaming = False

            self._webrtc_connected = False

            self._preview_jpeg = None

            if not self._session_active:

                self._initial_verified = False

                self._stable_start_mono = None

                self._stable_frame_count = 0

                if kwargs.get('user_initiated', True):
                    self._negotiation_started_mono = None
                    self._state = IPadScreenShareState.SCREEN_SHARE_STOPPED
                    if reason:
                        self._failure_reason = reason
                elif self._negotiation_started_mono is not None:

                    # Keep negotiating if still within 60s window unless explicitly failed

                    pass

                else:

                    self._state = IPadScreenShareState.SCREEN_SHARE_STOPPED

                    if reason:

                        self._failure_reason = reason

            else:

                self._state = IPadScreenShareState.SCREEN_SHARE_STOPPED

            logger.info("iPad Full-Screen Share track stopped.")



    def start_active_session_monitoring(

        self,

        session_id: str,

        now_mono: Optional[float] = None,

    ) -> None:

        """

        Transitions from prerequisite verification to active session monitoring.

        Cancels the 60-second startup negotiation timer and activates the live 10s frame-gap policy.

        """

        now_m = now_mono if now_mono is not None else time.monotonic()

        with self._lock:

            self._session_id = session_id

            self._session_active = True

            self._negotiation_started_mono = None

            self._negotiation_failed = False

            self._failure_reason = None

            self._is_interrupted = False

            self._current_incident = None

            self._recovery_start_mono = None

            if self._last_frame_mono is not None:

                self._last_frame_mono = now_m

            logger.info(f"iPad Full-Screen Share active session monitoring started for session {session_id}")



    def stop_active_session_monitoring(self, reason: str = "Session ended") -> None:

        with self._lock:

            self._session_active = False

            self._session_id = None

            self._is_interrupted = False

            self._current_incident = None

            self._recovery_start_mono = None

            logger.info(f"iPad Full-Screen Share active session monitoring stopped ({reason})")



    def record_received_frame(self, timestamp: Optional[float] = None) -> None:

        """Records reception of a screen frame packet before decoding."""

        with self._lock:

            now = timestamp or time.time()

            self._rx_timestamps.append(now)

            self._recalculate_rates(now)



    def process_frame(

        self,

        frame: np.ndarray,

        timestamp_epoch: Optional[float] = None,

        now_mono: Optional[float] = None,

        source_id: str = "IOS_FULL_SCREEN_SHARE",

    ) -> Dict[str, Any]:

        """

        Ingests a decoded video frame from the IOS_FULL_SCREEN_SHARE track.

        Strictly rejects IOS_FRONT_CAMERA frames.

        Verifies 3.0s stability during prerequisites and cancels the 60s negotiation timer once verified.

        """

        if str(source_id or "").upper() == "IOS_FRONT_CAMERA":

            logger.warning("Rejected IOS_FRONT_CAMERA frame in IPadScreenShareMonitor (strict source separation).")

            return {

                "accepted": False,

                "error": "IOS_FRONT_CAMERA frames cannot satisfy IOS_FULL_SCREEN_SHARE readiness.",

            }



        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:

            with self._lock:

                self._dropped_frames += 1

            return {"accepted": False, "error": "Invalid frame buffer"}



        now_epoch = timestamp_epoch if timestamp_epoch is not None else time.time()

        now_m = now_mono if now_mono is not None else time.monotonic()



        with self._lock:

            prev_frame_mono = self._last_frame_mono

            self._sequence += 1

            self._dec_timestamps.append(now_epoch)

            self._proc_timestamps.append(now_epoch)

            self._last_frame_epoch = now_epoch

            self._last_frame_mono = now_m



            if self._first_frame_mono is None:

                self._first_frame_mono = now_m



            h, w = frame.shape[:2]

            self._resolution = (w, h)

            self._is_streaming = True

            self._is_active = True

            self._webrtc_connected = True

            self._negotiation_failed = False

            self._failure_reason = None



            # Track continuous frame stability for initial prerequisite verification (3.0s window)

            if prev_frame_mono is not None and (now_m - prev_frame_mono) > 3.0:

                # Gap too large during initial stabilization; restart stability window

                self._stable_start_mono = now_m

                self._stable_frame_count = 1

            else:

                if self._stable_start_mono is None:

                    self._stable_start_mono = now_m

                self._stable_frame_count += 1



            stable_duration = now_m - (self._stable_start_mono or now_m)

            if not self._initial_verified:

                if stable_duration >= IPadScreenShareState.VERIFICATION_STABLE_SEC and self._stable_frame_count >= 2:

                    self._initial_verified = True

                    self._verified_at_mono = now_m

                    self._verified_at_epoch = now_epoch

                    # Reset / cancel the 60-second initial negotiation timer on successful verification!

                    self._negotiation_started_mono = None

                    self._negotiation_failed = False

                    self._failure_reason = None

                    self._state = (

                        IPadScreenShareState.SCREEN_SHARE_STREAMING

                        if self._session_active

                        else IPadScreenShareState.SCREEN_SHARE_VERIFIED

                    )

                    logger.info(

                        f"IOS_SCREEN_SHARE: VERIFIED after {stable_duration:.1f}s stable frames "

                        f"({self._stable_frame_count} frames, {w}x{h}). 60s negotiation timer cancelled."

                    )

                else:

                    self._state = IPadScreenShareState.SCREEN_SHARE_NEGOTIATING

            else:

                # Already verified; check active-session recovery if interrupted

                if self._session_active and self._is_interrupted:

                    if self._recovery_start_mono is None:

                        self._recovery_start_mono = now_m

                    elif (now_m - self._recovery_start_mono) >= IPadScreenShareState.RECOVERY_STABLE_SEC:

                        self._is_interrupted = False

                        self._state = IPadScreenShareState.SCREEN_SHARE_RECOVERED

                        recovery_time = now_m - self._recovery_start_mono

                        logger.info(f"iPad Screen Share recovered after stable {recovery_time:.1f}s.")

                        if self._current_incident:

                            self._current_incident["resolved_at"] = now_epoch

                            self._current_incident["duration_sec"] = round(

                                now_epoch - self._current_incident["started_at"], 2

                            )

                            self._incidents.append(self._current_incident)

                            if self.on_recovery:

                                try:

                                    self.on_recovery(dict(self._current_incident))

                                except Exception as err:

                                    logger.error(f"Error in on_recovery callback: {err}")

                            self._current_incident = None

                        self._recovery_start_mono = None

                else:

                    self._state = (

                        IPadScreenShareState.SCREEN_SHARE_STREAMING

                        if self._session_active

                        else IPadScreenShareState.SCREEN_SHARE_VERIFIED

                    )



            # Recalculate frame rates

            self._recalculate_rates(now_epoch)



            # Change detection via Mean Absolute Difference (MAD)

            try:

                small = cv2.resize(frame, (160, 120), interpolation=cv2.INTER_AREA)

                if len(small.shape) == 3:

                    gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

                else:

                    gray_small = small



                if self._prev_gray_small is not None:

                    diff = cv2.absdiff(gray_small, self._prev_gray_small)

                    mad = float(np.mean(diff))

                    self._last_mad = round(mad, 2)



                    if mad < IPadScreenShareState.MAD_STATIC_MAX:

                        self._activity_level = IPadScreenShareState.ACTIVITY_STATIC

                    elif mad < IPadScreenShareState.MAD_ACTIVE_MAX:

                        self._activity_level = IPadScreenShareState.ACTIVITY_ACTIVE

                    else:

                        self._activity_level = IPadScreenShareState.ACTIVITY_RAPID_TRANSITION

                        self._rapid_transition_count += 1

                else:

                    self._last_mad = 0.0

                    self._activity_level = IPadScreenShareState.ACTIVITY_STATIC



                self._prev_gray_small = gray_small

                self._recent_activities.append(self._activity_level)

            except Exception as err:

                logger.warning(f"Error computing screen delta: {err}")

                self._last_mad = 0.0

                self._activity_level = IPadScreenShareState.ACTIVITY_ACTIVE



            # Periodic preview thumbnail generation (limit to ~4 FPS to minimize CPU)

            visual_paused = False

            try:

                from windows.strict_sensitive_page_protection import SensitivePageProtectionManager

                visual_paused = SensitivePageProtectionManager.get_instance().is_visual_monitoring_paused()

            except Exception:

                pass

            if visual_paused:

                self._preview_jpeg = None

            elif now_m - self._last_preview_gen_mono >= 0.25:

                try:

                    thumb_w = min(w, 400)

                    thumb_h = int(h * (thumb_w / max(1, w)))

                    thumb = cv2.resize(frame, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)

                    success, enc = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 60])

                    if success:

                        self._preview_jpeg = base64.b64encode(enc.tobytes()).decode("ascii")

                        self._last_preview_gen_mono = now_m

                except Exception as err:

                    logger.debug(f"Could not encode screen thumbnail: {err}")



            return {

                "accepted": True,

                "source": "IOS_FULL_SCREEN_SHARE",

                "sequence": self._sequence,

                "initial_verified": self._initial_verified,

                "stable_duration_sec": round(stable_duration, 2),

                "activity_level": self._activity_level,

                "mad": self._last_mad,

                "rx_fps": self._rx_fps,

                "proc_fps": self._proc_fps,

            }



    def check_watchdog(self, now_mono: Optional[float] = None) -> Dict[str, Any]:

        """

        Evaluates either:

        1. Prerequisite 60-second initial negotiation timer (when _session_active is False), OR

        2. Active-session 10.0-second frame-gap interruption watchdog (when _session_active is True).

        """

        now_m = now_mono if now_mono is not None else time.monotonic()

        with self._lock:

            gap = (now_m - self._last_frame_mono) if self._last_frame_mono is not None else None



            # CASE 1: Prerequisite Phase (Session has NOT started yet)

            if not self._session_active:

                # If already verified, check that stream hasn't died before session start

                if self._initial_verified:

                    if gap is not None and gap > IPadScreenShareState.STALL_THRESHOLD_SEC:

                        self._initial_verified = False

                        self._is_streaming = False

                        self._stable_start_mono = None

                        self._stable_frame_count = 0

                        self._negotiation_failed = True

                        self._state = IPadScreenShareState.SCREEN_SHARE_FAILED

                        self._failure_reason = (

                            "iOS screen sharing stopped delivering frames before Strict Session started. "

                            "Tap Retry Screen Share."

                        )

                        return {

                            "interrupted": False,

                            "verified": False,

                            "failed": True,

                            "state": self._state,

                            "dashboard_state": IPadScreenShareState.FAILED,

                            "failure_reason": self._failure_reason,

                            "gap_sec": round(gap, 1),

                        }

                    return {

                        "interrupted": False,

                        "verified": True,

                        "failed": False,

                        "state": IPadScreenShareState.SCREEN_SHARE_VERIFIED,

                        "dashboard_state": IPadScreenShareState.VERIFIED,

                        "gap_sec": round(gap or 0.0, 1),

                    }



                # Not yet verified: check 60-second negotiation grace period

                if self._negotiation_started_mono is not None:

                    elapsed = max(0.0, now_m - self._negotiation_started_mono)

                    remaining = max(0.0, IPadScreenShareState.NEGOTIATION_GRACE_SEC - elapsed)

                    # If frames are currently arriving and stabilizing, allow completion even near boundary

                    stabilizing = (

                        self._last_frame_mono is not None

                        and gap is not None

                        and gap <= 3.0

                    )

                    if elapsed >= IPadScreenShareState.NEGOTIATION_GRACE_SEC and not stabilizing:

                        self._negotiation_failed = True

                        self._negotiation_started_mono = None

                        self._is_streaming = False

                        self._state = IPadScreenShareState.SCREEN_SHARE_FAILED

                        self._failure_reason = (

                            "No valid iOS full-screen share frames arrived within 60 seconds. "

                            "Approve screen broadcast on your iPad and click Retry iOS Screen Share."

                        )

                        logger.warning("iPad Full-Screen Share negotiation timed out after 60s without verified frames.")

                        return {

                            "interrupted": False,

                            "verified": False,

                            "failed": True,

                            "negotiating": False,

                            "remaining_sec": 0,

                            "state": self._state,

                            "dashboard_state": IPadScreenShareState.FAILED,

                            "failure_reason": self._failure_reason,

                            "gap_sec": round(gap or 0.0, 1),

                        }



                    rem_int = int(math.ceil(remaining))

                    mm = rem_int // 60

                    ss = rem_int % 60

                    self._state = IPadScreenShareState.SCREEN_SHARE_NEGOTIATING

                    return {

                        "interrupted": False,

                        "verified": False,

                        "failed": False,

                        "negotiating": True,

                        "remaining_sec": rem_int,

                        "countdown_text": f"Waiting for first valid frame: {mm:02d}:{ss:02d} remaining",

                        "state": self._state,

                        "dashboard_state": IPadScreenShareState.NEGOTIATING,

                        "gap_sec": round(gap or 0.0, 1),

                    }



                if self._negotiation_failed:

                    return {

                        "interrupted": False,

                        "verified": False,

                        "failed": True,

                        "negotiating": False,

                        "remaining_sec": 0,

                        "state": IPadScreenShareState.SCREEN_SHARE_FAILED,

                        "dashboard_state": IPadScreenShareState.FAILED,

                        "failure_reason": self._failure_reason,

                        "gap_sec": round(gap or 0.0, 1),

                    }



                dash_state = (

                    IPadScreenShareState.STOPPED

                    if not self._is_active

                    else IPadScreenShareState.WAITING_FOR_PERMISSION

                )

                return {

                    "interrupted": False,

                    "verified": False,

                    "failed": False,

                    "negotiating": False,

                    "state": self._state,

                    "dashboard_state": dash_state,

                    "gap_sec": 0.0,

                }



            # CASE 2: Active Session Watchdog (_session_active is True)

            if not self._is_streaming or self._last_frame_mono is None:

                dash_state = (

                    IPadScreenShareState.STOPPED

                    if not self._is_active

                    else IPadScreenShareState.WAITING_FOR_PERMISSION

                )

                return {

                    "interrupted": False,

                    "state": self._state,

                    "dashboard_state": dash_state,

                    "gap_sec": 0.0,

                }



            assert gap is not None

            if gap > IPadScreenShareState.STALL_THRESHOLD_SEC:

                if not self._is_interrupted:

                    self._is_interrupted = True

                    self._state = IPadScreenShareState.SCREEN_SHARE_INTERRUPTED

                    self._recovery_start_mono = None

                    incident_id = f"inc_screen_{uuid.uuid4().hex[:8]}"

                    self._current_incident = {

                        "incident_id": incident_id,

                        "source": "IOS_FULL_SCREEN_SHARE",

                        "started_at": time.time(),

                        "started_mono": now_m,

                        "gap_sec": round(gap, 1),

                        "resolved_at": None,

                    }

                    logger.warning(

                        f"iPad Screen Share watchdog triggered: frame gap {gap:.1f}s > "

                        f"{IPadScreenShareState.STALL_THRESHOLD_SEC}s (Incident: {incident_id})"

                    )

                    if self.on_interruption:

                        try:

                            self.on_interruption(dict(self._current_incident))

                        except Exception as err:

                            logger.error(f"Error in on_interruption callback: {err}")



                return {

                    "interrupted": True,

                    "state": IPadScreenShareState.SCREEN_SHARE_INTERRUPTED,

                    "dashboard_state": IPadScreenShareState.INTERRUPTED,

                    "gap_sec": round(gap, 1),

                    "incident": self._current_incident,

                }



            if not self._is_active:

                dash_state = IPadScreenShareState.STOPPED

            elif self._recovery_start_mono is not None:

                dash_state = IPadScreenShareState.RECOVERING

            elif self._is_interrupted:

                dash_state = IPadScreenShareState.INTERRUPTED

            elif gap >= 3.0:

                dash_state = IPadScreenShareState.TEMPORARILY_PAUSED

            else:

                dash_state = IPadScreenShareState.LIVE



            return {

                "interrupted": self._is_interrupted,

                "state": self._state,

                "dashboard_state": dash_state,

                "gap_sec": round(gap, 1),

            }



    def _recalculate_rates(self, now: float) -> None:

        cutoff = now - 2.0

        while self._rx_timestamps and self._rx_timestamps[0] < cutoff:

            self._rx_timestamps.popleft()

        while self._dec_timestamps and self._dec_timestamps[0] < cutoff:

            self._dec_timestamps.popleft()

        while self._proc_timestamps and self._proc_timestamps[0] < cutoff:

            self._proc_timestamps.popleft()



        dur = 2.0

        self._rx_fps = round(len(self._rx_timestamps) / dur, 1)

        self._decoded_fps = round(len(self._dec_timestamps) / dur, 1)

        self._proc_fps = round(len(self._proc_timestamps) / dur, 1)



    def get_telemetry(self, now_mono: Optional[float] = None) -> Dict[str, Any]:

        """Provides full real-time telemetry and prerequisite status for IOS_FULL_SCREEN_SHARE."""

        now_m = now_mono if now_mono is not None else time.monotonic()

        with self._lock:

            # Evaluate watchdog/negotiation state first so timeouts are up to date

            wd = self.check_watchdog(now_mono=now_m)



            gap = round(now_m - self._last_frame_mono, 1) if self._last_frame_mono is not None else None

            self._recalculate_rates(time.time())

            is_fresh = (

                self._is_streaming

                and not self._is_interrupted

                and gap is not None

                and gap < IPadScreenShareState.STALL_THRESHOLD_SEC

                and (self._proc_fps > 0 or self._sequence > 0)

            )



            stable_dur = (

                round(now_m - self._stable_start_mono, 1)

                if (self._stable_start_mono is not None and is_fresh)

                else 0.0

            )

            verified_ready = bool(self._initial_verified and is_fresh)



            negotiating = bool(

                not verified_ready

                and not self._negotiation_failed

                and self._negotiation_started_mono is not None

            )

            remaining_sec = int(wd.get("remaining_sec", 0) or 0) if negotiating else 0

            mm = remaining_sec // 60

            ss = remaining_sec % 60



            if verified_ready:

                prereq_status = "IOS_SCREEN_SHARE: VERIFIED"

                countdown_label = f"Verified ({self._resolution[0]}x{self._resolution[1]} @ {max(self._proc_fps, 1.0):.1f} FPS)"

            elif self._negotiation_failed:

                prereq_status = "IOS_SCREEN_SHARE_FAILED"

                countdown_label = self._failure_reason or "Screen share failed to initialize within 60 seconds."

            elif negotiating:

                prereq_status = "SCREEN SHARE NEGOTIATING"

                if is_fresh and stable_dur < IPadScreenShareState.VERIFICATION_STABLE_SEC:

                    countdown_label = (

                        f"Verifying stable screen stream ({stable_dur:.1f}s / "

                        f"{IPadScreenShareState.VERIFICATION_STABLE_SEC:.0f}s) · {mm:02d}:{ss:02d} remaining"

                    )

                else:

                    countdown_label = f"Waiting for first valid frame: {mm:02d}:{ss:02d} remaining"

            else:

                prereq_status = "WAITING_FOR_SCREEN_SHARE"

                countdown_label = "Tap Share iPad Screen on the companion page (60s negotiation grace period)."



            status_str = (

                "VERIFIED"

                if verified_ready and not self._session_active

                else (

                    "STREAMING"

                    if is_fresh

                    else (

                        "FAILED"

                        if self._negotiation_failed

                        else (

                            "NEGOTIATING"

                            if negotiating

                            else ("INTERRUPTED" if self._is_interrupted else ("CONNECTING" if self._is_active else "STOPPED"))

                        )

                    )

                )

            )

            dash_state = wd.get("dashboard_state", IPadScreenShareState.STOPPED)



            recent = list(self._recent_activities)

            static_pct = round(

                100.0 * recent.count(IPadScreenShareState.ACTIVITY_STATIC) / max(1, len(recent)), 1

            )

            active_pct = round(

                100.0 * recent.count(IPadScreenShareState.ACTIVITY_ACTIVE) / max(1, len(recent)), 1

            )

            rapid_pct = round(

                100.0 * recent.count(IPadScreenShareState.ACTIVITY_RAPID_TRANSITION) / max(1, len(recent)), 1

            )



            try:
                from windows.strict_ios_media_health import IOSMediaHealthManager, SOURCE_SCREEN_SHARE
                _scr_diag = IOSMediaHealthManager.get_instance().get_source_diagnostics(SOURCE_SCREEN_SHARE)
                _scr_cls = _scr_diag.get("classification", "IOS_STATUS_UNKNOWN")
                _scr_hb = _scr_diag.get("last_heartbeat_timestamp")
            except Exception:
                _scr_cls = "IOS_STATUS_UNKNOWN"
                _scr_hb = None

            return {
                "ios_media_classification": _scr_cls,
                "last_heartbeat_timestamp": _scr_hb,
                "source": "IOS_FULL_SCREEN_SHARE",

                "source_id": "IOS_FULL_SCREEN_SHARE",

                "status": status_str,

                "state": self._state,

                "prerequisite_status": prereq_status,

                "dashboard_state": dash_state,

                "initial_verified": verified_ready,

                "verified": verified_ready,

                "negotiating": negotiating,

                "is_negotiating": negotiating,

                "negotiation_grace_sec": int(IPadScreenShareState.NEGOTIATION_GRACE_SEC),

                "negotiation_remaining_sec": remaining_sec,

                "negotiation_countdown_label": countdown_label,

                "stable_duration_sec": stable_dur,

                "required_stable_sec": IPadScreenShareState.VERIFICATION_STABLE_SEC,

                "failed": self._negotiation_failed,

                "negotiation_failed": self._negotiation_failed,

                "failure_reason": self._failure_reason,

                "session_active": self._session_active,

                "active_session_monitoring": self._session_active,

                "webrtc_connected": self._webrtc_connected,

                "is_streaming": is_fresh,

                "is_active": self._is_active,

                "is_interrupted": self._is_interrupted if self._session_active else False,

                "track_id": self._track_id,

                "resolution": f"{self._resolution[0]}x{self._resolution[1]}" if self._resolution[0] > 0 else "0x0",

                "width": self._resolution[0],

                "height": self._resolution[1],

                "frame_sequence": self._sequence,

                "rx_fps": self._rx_fps if is_fresh else 0.0,

                "decoded_fps": self._decoded_fps if is_fresh else 0.0,

                "processing_fps": self._proc_fps if is_fresh else 0.0,

                "last_frame_gap_sec": gap,

                "last_frame_epoch": self._last_frame_epoch,

                "dropped_frames": self._dropped_frames,

                "activity_level": self._activity_level if is_fresh else "OFF",

                "structural_delta_mad": self._last_mad if is_fresh else 0.0,

                "activity_breakdown": {

                    "static_pct": static_pct,

                    "active_pct": active_pct,

                    "rapid_transition_pct": rapid_pct,

                    "rapid_transition_count": self._rapid_transition_count,

                },

                "incidents_count": len(self._incidents) + (1 if (self._session_active and self._is_interrupted) else 0),

                "preview_jpeg": self._preview_jpeg if is_fresh else None,

            }



    def get_status_snapshot(self, now_mono: Optional[float] = None) -> Dict[str, Any]:

        return self.get_telemetry(now_mono=now_mono)

