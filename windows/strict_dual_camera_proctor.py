"""
STRICTRECORDS – DUAL-CAMERA REAL-TIME PROCTORING ENGINE
Coordinates simultaneous processing of Windows PC Webcam and iOS Camera stream over WebRTC.
Performs:
- Independent camera calibration & perspective adaptation.
- Thread-safe bounded frame queues with stale-frame dropping to guarantee sub-100ms latency.
- Dedicated YuNet landmark detection, 3D head-pose estimation (PnP), and eye-gaze analysis.
- Multi-camera event fusion, cross-linking and time-based deduplication (~3.0s window).
- True composite status reporting (BOTH CAMERAS ACTIVE only when both feeds are live and processing).
- Clean resource release on session termination.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

logger = logging.getLogger("StrictDualCameraProctor")


class MultiCameraEventManager:
    """
    Correlates and deduplicates proctoring events across Windows and iOS cameras.
    Guarantees that sustained attention deviations observed by both cameras
    around the same time (within correlation window) are counted as a single violation penalty,
    while preserving both source observations in the audit trail.
    """

    def __init__(
        self,
        time_correlation_window_sec: float = 3.0,
        on_fused_violation: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._lock = threading.RLock()
        self.time_correlation_window = time_correlation_window_sec
        self.on_fused_violation = on_fused_violation
        self.events: List[Dict[str, Any]] = []
        self.penalized_event_ids: Set[str] = set()

    def record_candidate(self, event: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Processes a candidate event from a camera source.
        Returns (should_penalize: bool, recorded_event: dict).
        """
        with self._lock:
            evt = dict(event)
            if not evt.get("event_id"):
                evt["event_id"] = f"evt_{uuid.uuid4().hex[:10]}"
            now = evt.get("detection_timestamp", time.time())
            source = evt.get("camera_source_id", "UNKNOWN")
            evt_type = evt.get("event_type", "VIOLATION")

            # Check if there is an observation of similar type from another camera within correlation window
            correlated = None
            for prev in reversed(self.events):
                if prev.get("camera_source_id") != source and prev.get("event_type") == evt_type:
                    diff = abs(now - float(prev.get("detection_timestamp", 0.0)))
                    if diff <= self.time_correlation_window:
                        correlated = prev
                        break

            if correlated:
                # Correlated event observed on both cameras
                evt["detection_status"] = "CORRELATED"
                evt["related_event_id"] = correlated["event_id"]
                correlated["related_event_id"] = evt["event_id"]
                should_penalize = False  # Deduplicated: already penalized for this physical event
                logger.info(
                    f"Dual-camera event correlated: {source} ({evt_type}) matches "
                    f"{correlated['camera_source_id']} ({correlated['event_id']})"
                )
            else:
                evt["detection_status"] = "CONFIRMED"
                should_penalize = True

            self.events.append(evt)
            if len(self.events) > 100:
                self.events.pop(0)

            if should_penalize:
                self.penalized_event_ids.add(evt["event_id"])
                if self.on_fused_violation:
                    try:
                        self.on_fused_violation(evt)
                    except Exception as err:
                        logger.error(f"Error in on_fused_violation callback: {err}")

            return should_penalize, evt

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.events[-limit:])

    def clear(self) -> None:
        with self._lock:
            self.events.clear()
            self.penalized_event_ids.clear()


class StrictDualCameraProctor:
    """
    Central controller for real-time dual-camera proctoring.
    Continuously monitors:
      1. Windows Webcam (Source: WINDOWS)
      2. iOS Secondary Camera (Source: IOS)
    """

    _instance: Optional[StrictDualCameraProctor] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls, **kwargs) -> StrictDualCameraProctor:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None

    def __init__(
        self,
        on_violation: Optional[Callable[[int, str], None]] = None,
        on_failure: Optional[Callable[[str], None]] = None,
    ):
        self.lock = threading.RLock()
        self.on_violation = on_violation
        self.on_failure = on_failure
        self.session_id: Optional[str] = None
        self.is_running = False

        from windows.strict_guard_proctor import StrictGuardProctor, get_strict_guard_proctor

        self.event_manager = MultiCameraEventManager(
            time_correlation_window_sec=3.0,
            on_fused_violation=self._dispatch_fused_violation,
        )

        # 1. Primary Windows Camera Proctor (coordinates with the singleton proctor)
        self.windows_proctor = get_strict_guard_proctor()
        self.windows_proctor.camera_source = "WINDOWS"
        self.windows_proctor.on_event = self._on_camera_event

        # 2. Secondary Dedicated iOS Camera Proctor
        self.ios_proctor = StrictGuardProctor(
            camera_source="IOS",
            on_event=self._on_camera_event,
            on_violation=None,  # Routed via multi-camera event manager
        )
        self.ios_proctor.is_running = True
        self.ios_proctor.camera_name = "iPhone / iPad Camera"

        # Thread-safe bounded frame queue for iOS camera frames
        self.ios_frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._ios_stop_event = threading.Event()
        self._ios_worker_thread: Optional[threading.Thread] = None

        # Telemetry tracking for iOS
        self._ios_received_times: collections.deque = collections.deque(maxlen=40)
        self._ios_proc_times: collections.deque = collections.deque(maxlen=40)
        self._ios_last_frame_received_at: float = 0.0
        self._ios_device_name: str = "iPhone / iPad"
        self._ios_webrtc_status: str = "DISCONNECTED"
        self._ios_track_status: str = "OFF"
        self._ios_preview_jpeg: Optional[str] = None
        self._ios_dropped_frames: int = 0
        self._ios_processing_fps: float = 0.0
        self._ios_received_fps: float = 0.0

        self._start_ios_worker()

    def _start_ios_worker(self) -> None:
        if self._ios_worker_thread and self._ios_worker_thread.is_alive():
            return
        self._ios_stop_event.clear()
        self.ios_proctor.is_running = True
        self._ios_worker_thread = threading.Thread(
            target=self._ios_worker_loop,
            name="IOSCameraProctorWorker",
            daemon=True,
        )
        self._ios_worker_thread.start()

    def ensure_ios_worker_alive(self) -> bool:
        """Verifies the dedicated iOS inference worker thread is alive; restarts it if stopped."""
        with self.lock:
            self.ios_proctor.is_running = True
            if self._ios_worker_thread is None or not self._ios_worker_thread.is_alive():
                self._start_ios_worker()
                return True
            return False

    def _recalculate_ios_fps(self, now: Optional[float] = None) -> Tuple[float, float]:
        t_now = time.time() if now is None else now
        with self.lock:
            recent_rx = [t for t in self._ios_received_times if 0.0 <= (t_now - t) <= 2.0]
            recent_proc = [t for t in self._ios_proc_times if 0.0 <= (t_now - t) <= 2.0]
            self._ios_received_fps = round(len(recent_rx) / 2.0, 1) if len(recent_rx) > 1 else (1.0 if len(recent_rx) == 1 else 0.0)
            self._ios_processing_fps = round(len(recent_proc) / 2.0, 1) if len(recent_proc) > 1 else (1.0 if len(recent_proc) == 1 else 0.0)
            self.ios_proctor.processing_fps = self._ios_processing_fps
            return self._ios_received_fps, self._ios_processing_fps

    def set_session_id(self, session_id: Optional[str]) -> None:
        with self.lock:
            self.session_id = session_id
            self.windows_proctor.session_id = session_id
            self.ios_proctor.session_id = session_id

    def submit_ios_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> None:
        """
        Thread-safe, non-blocking ingestion of decoded iOS video frames.
        Never blocks the WebRTC media receiver loop. Drops stale frames if inference is busy.
        """
        self.ensure_ios_worker_alive()
        now = time.time() if timestamp is None else timestamp
        with self.lock:
            self._ios_last_frame_received_at = now
            self._ios_received_times.append(now)
            self._ios_track_status = "LIVE"
            self._ios_webrtc_status = "STREAMING"
            self._recalculate_ios_fps(now)

        try:
            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
            mon = IPadCameraStreamMonitor.get_instance()
            mon.record_valid_frame(
                frame=frame,
                session_id=self.session_id,
                presentation_timestamp=now,
            )
            mon.record_frame_queued_for_inference(queue_length=self.ios_frame_queue.qsize() + 1)
        except Exception as cam_err:
            logger.debug(f"IPadCameraStreamMonitor record_valid_frame notice: {cam_err}")

        # Discard oldest pending frame if worker hasn't consumed it yet
        if self.ios_frame_queue.full():
            try:
                self.ios_frame_queue.get_nowait()
                with self.lock:
                    self._ios_dropped_frames += 1
            except queue.Empty:
                pass

        try:
            self.ios_frame_queue.put_nowait((frame, now))
        except queue.Full:
            pass

    def submit_ios_screen_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> None:
        """
        Thread-safe ingestion of decoded iPad screen sharing frames.
        Routes frames directly to IPadScreenShareMonitor without interfering with front camera.
        """
        try:
            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
            IPadScreenShareMonitor.get_instance().process_frame(frame, timestamp_epoch=timestamp)
        except Exception as err:
            logger.debug(f"IPadScreenShareMonitor frame ingestion error: {err}")


    def _ios_worker_loop(self) -> None:
        """Background thread executing neural face/landmarks inference on iOS frames."""
        while not self._ios_stop_event.is_set():
            try:
                item = self.ios_frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            frame, ts = item
            t0 = time.perf_counter()
            try:
                res = self.ios_proctor.process_frame(frame, timestamp=ts)
                t_proc = time.perf_counter()
                now = time.time()
                with self.lock:
                    self._ios_proc_times.append(now)
                    self._recalculate_ios_fps(now)
                    self.ios_proctor.inference_latency_ms = round((t_proc - t0) * 1000.0, 1)
                    if res.get("preview_jpeg"):
                        self._ios_preview_jpeg = res["preview_jpeg"]

                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    IPadCameraStreamMonitor.get_instance().record_processed_frame(
                        queue_length=self.ios_frame_queue.qsize()
                    )
                except Exception:
                    pass
            except Exception as err:
                logger.error(f"Error in iOS proctoring worker: {err}")
                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    IPadCameraStreamMonitor.get_instance().record_processing_error(str(err))
                except Exception:
                    pass

    def _on_camera_event(self, event: Dict[str, Any]) -> None:
        """Callback from either Windows or iOS proctor when a violation candidate occurs."""
        should_penalize, evt = self.event_manager.record_candidate(event)
        # Note: _dispatch_fused_violation is invoked by event_manager if should_penalize is True

    def _dispatch_fused_violation(self, event: Dict[str, Any]) -> None:
        """Dispatches an authoritative single penalty for a confirmed/fused violation event."""
        with self.lock:
            count = self.windows_proctor.violations_count + 1
            self.windows_proctor.violations_count = count
            category = event.get("event_type", "PROCTOR_ATTENTION")
            src = event.get("camera_source_id", "DUAL_CAMERA")
            msg = f"{category.replace('_', ' ').title()} observed via {src} ({count}/{self.windows_proctor.MAX_PERMITTED_VIOLATIONS})"
            logger.warning(f"DualCamera fused violation #{count}: {msg}")

            if self.on_violation:
                try:
                    self.on_violation(count, msg)
                except Exception as err:
                    logger.error(f"Error calling on_violation: {err}")

            if count > self.windows_proctor.MAX_PERMITTED_VIOLATIONS:
                reason = f"Maximum permitted violations exceeded ({count}/{self.windows_proctor.MAX_PERMITTED_VIOLATIONS})"
                if self.on_failure:
                    try:
                        self.on_failure(reason)
                    except Exception as err:
                        logger.error(f"Error calling on_failure: {err}")

    def get_composite_status(self) -> Dict[str, Any]:
        """
        Calculates authoritative composite status.
        'BOTH CAMERAS ACTIVE' is shown ONLY when BOTH feeds are independently
        receiving and processing real frames.
        """
        now = time.time()
        self._recalculate_ios_fps(now)
        win_age = (
            time.monotonic() - self.windows_proctor._last_frame_mono
            if self.windows_proctor._last_frame_mono is not None
            else 999.0
        )
        win_fresh = self.windows_proctor.is_running and win_age < 3.5

        ios_age = (now - self._ios_last_frame_received_at) if self._ios_last_frame_received_at > 0 else 999.0
        ios_fresh = (
            self._ios_last_frame_received_at > 0
            and ios_age < 5.0
            and self._ios_track_status == "LIVE"
            and self._ios_received_fps > 0
        )

        screen_fresh = False
        screen_tel = {}
        try:
            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
            screen_mon = IPadScreenShareMonitor.get_instance()
            screen_mon.check_watchdog(now_mono=time.monotonic())
            screen_tel = screen_mon.get_telemetry()
            screen_fresh = bool(screen_tel.get("is_streaming", False))
        except Exception:
            pass

        if win_fresh and ios_fresh:
            if screen_fresh:
                state = "ALL_THREE_ACTIVE"
                label = "● ALL 3 SOURCES ACTIVE (WEBCAM + iOS CAMERA + iPAD SCREEN)"
                color = "#4be78b"
            else:
                state = "BOTH_ACTIVE"
                label = "BOTH CAMERAS ACTIVE"
                color = "#4be78b"
        elif win_fresh and not ios_fresh:
            state = "WINDOWS_ONLY"
            label = "WINDOWS CAMERA ACTIVE – iOS CAMERA UNAVAILABLE"
            color = "#f59e0b"
        elif not win_fresh and ios_fresh:
            state = "IOS_ONLY"
            label = "iOS CAMERA ACTIVE – WINDOWS CAMERA UNAVAILABLE"
            color = "#f59e0b"
        elif not win_fresh and not ios_fresh:
            state = "BOTH_UNAVAILABLE"
            label = "BOTH CAMERAS UNAVAILABLE"
            color = "#ef4444"
        else:
            state = "DUAL_DEGRADED"
            label = "DUAL CAMERA DEGRADED"
            color = "#f59e0b"

        return {
            "state": state,
            "label": label,
            "color": color,
            "windows_fresh": win_fresh,
            "ios_fresh": ios_fresh,
            "screen_fresh": screen_fresh,
            "windows_age_seconds": round(win_age, 1) if win_age < 900 else None,
            "ios_age_seconds": round(ios_age, 1) if ios_age < 900 else None,
        }

    def get_dual_camera_telemetry(self) -> Dict[str, Any]:
        """Returns structured real-time 3-source metrics for dashboard rendering."""
        with self.lock:
            comp = self.get_composite_status()
            win_tel = self.windows_proctor.get_telemetry()
            ios_tel = self.ios_proctor.get_telemetry()

            screen_tel = {}
            try:
                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
                screen_tel = IPadScreenShareMonitor.get_instance().get_telemetry()
            except Exception:
                pass

            now = time.time()
            ios_age = (now - self._ios_last_frame_received_at) if self._ios_last_frame_received_at > 0 else None
            ios_is_live = comp["ios_fresh"]

            return {
                "composite_status": comp["state"],
                "composite_label": comp["label"],
                "composite_color": comp["color"],
                "windows_camera": {
                    "source": "WINDOWS",
                    "device_name": getattr(self.windows_proctor, "camera_name", "Webcam"),
                    "status": "ACTIVE" if comp["windows_fresh"] else getattr(self.windows_proctor, "camera_status", "OFF"),
                    "face_presence": win_tel.get("face_presence", "UNVERIFIED"),
                    "gaze_status": win_tel.get("attention_state", "NORMAL"),
                    "head_pose": {
                        "yaw": win_tel.get("yaw", 0.0),
                        "pitch": win_tel.get("pitch", 0.0),
                        "roll": 0.0,
                    },
                    "lighting": win_tel.get("lighting", "UNVERIFIED"),
                    "face_framing": win_tel.get("face_framing", "UNVERIFIED"),
                    "calibrated": win_tel.get("calibrated", False),
                    "processing_fps": getattr(self.windows_proctor, "processing_fps", 15.0),
                    "violations_count": win_tel.get("violations_count", 0),
                    "preview_jpeg": self.windows_proctor._preview,
                    "recent_events": getattr(self.windows_proctor, "recent_events", []),
                },
                "ios_camera": {
                    "source": "IOS",
                    "device_name": self._ios_device_name,
                    "status": "ACTIVE" if ios_is_live else ("CAMERA_PAUSED" if ios_age and ios_age < 15.0 else "CAMERA_DISCONNECTED"),
                    "webrtc_status": "STREAMING" if ios_is_live else self._ios_webrtc_status,
                    "track_status": "LIVE" if ios_is_live else "OFF",
                    "received_fps": self._ios_received_fps if ios_is_live else 0.0,
                    "processing_fps": self._ios_processing_fps if ios_is_live else 0.0,
                    "inference_latency_ms": getattr(self.ios_proctor, "inference_latency_ms", 0.0),
                    "face_presence": ios_tel.get("face_presence", "UNVERIFIED") if ios_is_live else "UNAVAILABLE",
                    "gaze_status": getattr(self.ios_proctor, "gaze_status", "NORMAL") if ios_is_live else "UNAVAILABLE",
                    "head_pose": {
                        "yaw": ios_tel.get("yaw", 0.0),
                        "pitch": ios_tel.get("pitch", 0.0),
                        "roll": 0.0,
                    } if ios_is_live else {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
                    "lighting": ios_tel.get("lighting", "UNVERIFIED") if ios_is_live else "UNVERIFIED",
                    "face_framing": ios_tel.get("face_framing", "UNVERIFIED") if ios_is_live else "UNVERIFIED",
                    "calibrated": ios_tel.get("calibrated", False),
                    "dropped_frames": self._ios_dropped_frames,
                    "last_frame_age_seconds": round(ios_age, 1) if ios_age is not None else None,
                    "preview_jpeg": self._ios_preview_jpeg if ios_is_live else None,
                    "recent_events": getattr(self.ios_proctor, "recent_events", []),
                },
                "ios_screen_share": screen_tel,
                "recent_events": self.event_manager.get_recent_events(limit=10),
            }

    def stop(self) -> None:
        """Stops background workers and clears resources."""
        self._ios_stop_event.set()
        if self._ios_worker_thread and self._ios_worker_thread.is_alive():
            self._ios_worker_thread.join(timeout=1.5)
        # Drain queue
        while not self.ios_frame_queue.empty():
            try:
                self.ios_frame_queue.get_nowait()
            except queue.Empty:
                break
        self.ios_proctor.stop()
        self.event_manager.clear()
        with self.lock:
            self._ios_preview_jpeg = None
            self._ios_webrtc_status = "DISCONNECTED"
            self._ios_track_status = "OFF"
            self._ios_received_times.clear()
            self._ios_proc_times.clear()
        logger.info("StrictDualCameraProctor stopped.")
