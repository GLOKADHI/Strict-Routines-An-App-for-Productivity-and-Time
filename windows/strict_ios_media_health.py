"""STRICTRECORDS – INDEPENDENT iOS MEDIA HEARTBEAT AND FRAME-RECEPTION FALLBACK DIAGNOSTICS

Authoritative diagnostic engine for comparing independent iOS companion media heartbeats
with Windows WebRTC receiver statistics.

Classifies media health across 10 distinct states:
1.  IOS_SHARING_ACTIVE_AND_RECEIVING
2.  IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING
3.  IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING
4.  WINDOWS_DECODER_STALLED
5.  WINDOWS_PROCESSING_STALLED
6.  IOS_SHARING_STOPPED_BY_USER
7.  IOS_PERMISSION_UNAVAILABLE
8.  IOS_CAPTURE_INTERRUPTED
9.  IOS_CONTROL_CHANNEL_DISCONNECTED
10. IOS_STATUS_UNKNOWN
"""

from __future__ import annotations

import collections
import copy
import logging
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("IOSMediaHealthManager")


class IOSMediaHealthClassification(str, Enum):
    IOS_SHARING_ACTIVE_AND_RECEIVING = "IOS_SHARING_ACTIVE_AND_RECEIVING"
    IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING = "IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING"
    IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING = "IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING"
    WINDOWS_DECODER_STALLED = "WINDOWS_DECODER_STALLED"
    WINDOWS_PROCESSING_STALLED = "WINDOWS_PROCESSING_STALLED"
    IOS_SHARING_STOPPED_BY_USER = "IOS_SHARING_STOPPED_BY_USER"
    IOS_PERMISSION_UNAVAILABLE = "IOS_PERMISSION_UNAVAILABLE"
    IOS_CAPTURE_INTERRUPTED = "IOS_CAPTURE_INTERRUPTED"
    IOS_CONTROL_CHANNEL_DISCONNECTED = "IOS_CONTROL_CHANNEL_DISCONNECTED"
    IOS_STATUS_UNKNOWN = "IOS_STATUS_UNKNOWN"


SOURCE_FRONT_CAMERA = "IOS_FRONT_CAMERA"
SOURCE_SCREEN_SHARE = "IOS_FULL_SCREEN_SHARE"
ALL_SOURCES = (SOURCE_FRONT_CAMERA, SOURCE_SCREEN_SHARE)


class IOSSourceHeartbeat:
    """Represents a single parsed heartbeat snapshot from iOS for one source."""

    def __init__(
        self,
        source_id: str,
        session_id: str = "",
        device_id: str = "",
        connection_generation: int = 1,
        heartbeat_seq: int = 0,
        timestamp: float = 0.0,
        user_sharing_enabled: bool = False,
        permission_status: str = "unknown",
        capture_session_status: str = "inactive",
        video_track_enabled: bool = False,
        video_track_ready_state: str = "none",
        video_track_muted: bool = False,
        captured_frames: int = 0,
        last_captured_timestamp: float = 0.0,
        encoded_frames: int = 0,
        transmitted_frames: int = 0,
        explicit_user_stop: Optional[Dict[str, Any]] = None,
        capture_error: Optional[str] = None,
        channel: str = "WSS",
        received_at: Optional[float] = None,
    ):
        self.source_id = source_id
        self.session_id = session_id
        self.device_id = device_id
        self.connection_generation = connection_generation
        self.heartbeat_seq = heartbeat_seq
        self.timestamp = timestamp or time.time()
        self.user_sharing_enabled = user_sharing_enabled
        self.permission_status = permission_status
        self.capture_session_status = capture_session_status
        self.video_track_enabled = video_track_enabled
        self.video_track_ready_state = video_track_ready_state
        self.video_track_muted = video_track_muted
        self.captured_frames = captured_frames
        self.last_captured_timestamp = last_captured_timestamp
        self.encoded_frames = encoded_frames
        self.transmitted_frames = transmitted_frames
        self.explicit_user_stop = explicit_user_stop
        self.capture_error = capture_error
        self.channel = channel
        self.received_at = received_at if received_at is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "connection_generation": self.connection_generation,
            "heartbeat_seq": self.heartbeat_seq,
            "timestamp": self.timestamp,
            "received_at": self.received_at,
            "age_seconds": round(max(0.0, time.time() - self.received_at), 1),
            "user_sharing_enabled": self.user_sharing_enabled,
            "permission_status": self.permission_status,
            "capture_session_status": self.capture_session_status,
            "video_track_enabled": self.video_track_enabled,
            "video_track_ready_state": self.video_track_ready_state,
            "video_track_muted": self.video_track_muted,
            "captured_frames": self.captured_frames,
            "last_captured_timestamp": self.last_captured_timestamp,
            "encoded_frames": self.encoded_frames,
            "transmitted_frames": self.transmitted_frames,
            "explicit_user_stop": copy.deepcopy(self.explicit_user_stop),
            "capture_error": self.capture_error,
            "channel": self.channel,
        }


class WindowsReceiverSnapshot:
    """Represents actual frame reception/decoding/inference stats observed on Windows."""

    def __init__(
        self,
        source_id: str,
        rx_fps: float = 0.0,
        rx_frames_count: int = 0,
        decoded_fps: float = 0.0,
        decoded_frames_count: int = 0,
        processing_fps: float = 0.0,
        processed_frames_count: int = 0,
        last_rx_epoch: Optional[float] = None,
        last_decoded_epoch: Optional[float] = None,
        last_processed_epoch: Optional[float] = None,
        webrtc_connected: bool = False,
    ):
        self.source_id = source_id
        self.rx_fps = rx_fps
        self.rx_frames_count = rx_frames_count
        self.decoded_fps = decoded_fps
        self.decoded_frames_count = decoded_frames_count
        self.processing_fps = processing_fps
        self.processed_frames_count = processed_frames_count
        self.last_rx_epoch = last_rx_epoch
        self.last_decoded_epoch = last_decoded_epoch
        self.last_processed_epoch = last_processed_epoch
        self.webrtc_connected = webrtc_connected
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "rx_fps": round(self.rx_fps, 2),
            "rx_frames_count": self.rx_frames_count,
            "decoded_fps": round(self.decoded_fps, 2),
            "decoded_frames_count": self.decoded_frames_count,
            "processing_fps": round(self.processing_fps, 2),
            "processed_frames_count": self.processed_frames_count,
            "last_rx_epoch": self.last_rx_epoch,
            "last_decoded_epoch": self.last_decoded_epoch,
            "last_processed_epoch": self.last_processed_epoch,
            "webrtc_connected": self.webrtc_connected,
            "updated_at": self.updated_at,
        }


class IOSMediaHealthManager:
    """Central singleton manager for independent iOS media heartbeat reporting

    and Windows receiver comparison.
    """

    _instance: Optional["IOSMediaHealthManager"] = None
    _lock: threading.RLock = threading.RLock()

    # Time thresholds (seconds)
    HEARTBEAT_TIMEOUT_SEC: float = 15.0
    FRAME_STALL_THRESHOLD_SEC: float = 5.0

    @classmethod
    def get_instance(cls) -> "IOSMediaHealthManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def __init__(self):
        self._lock = threading.RLock()
        self._heartbeats: Dict[str, IOSSourceHeartbeat] = {}
        self._prev_heartbeats: Dict[str, IOSSourceHeartbeat] = {}
        self._receiver_stats: Dict[str, WindowsReceiverSnapshot] = {
            SOURCE_FRONT_CAMERA: WindowsReceiverSnapshot(SOURCE_FRONT_CAMERA),
            SOURCE_SCREEN_SHARE: WindowsReceiverSnapshot(SOURCE_SCREEN_SHARE),
        }
        self._explicit_stops: Dict[str, Dict[str, Any]] = {}
        self._control_channel_connected: bool = False
        self._last_control_channel_activity: Optional[float] = None
        self._active_session_id: Optional[str] = None
        self._device_id: Optional[str] = None

    def set_active_session(self, session_id: Optional[str], device_id: Optional[str] = None) -> None:
        with self._lock:
            self._active_session_id = session_id
            if device_id:
                self._device_id = device_id

    def record_heartbeat(
        self,
        source_id: str,
        data: Dict[str, Any],
        channel: str = "WSS",
        received_at: Optional[float] = None,
    ) -> IOSMediaHealthClassification:
        """Parses and stores an incoming heartbeat for a specific source."""
        # Normalize source ID
        if source_id in ("IOS_SCREEN_SHARE", "screen", "IOS_FULL_SCREEN_SHARE"):
            source_id = SOURCE_SCREEN_SHARE
        elif source_id in ("IOS_CAMERA", "camera", "IOS_FRONT_CAMERA"):
            source_id = SOURCE_FRONT_CAMERA

        rx_time = received_at if received_at is not None else time.time()
        with self._lock:
            self._control_channel_connected = True
            self._last_control_channel_activity = rx_time

            # Archive previous heartbeat for delta calculations
            if source_id in self._heartbeats:
                self._prev_heartbeats[source_id] = self._heartbeats[source_id]

            hb = IOSSourceHeartbeat(
                source_id=source_id,
                session_id=str(data.get("session_id", self._active_session_id or "")),
                device_id=str(data.get("device_id", self._device_id or "")),
                connection_generation=int(data.get("connection_generation", 1)),
                heartbeat_seq=int(data.get("heartbeat_seq", data.get("heartbeat_sequence_number", 0))),
                timestamp=float(data.get("timestamp", data.get("heartbeat_timestamp", rx_time))),
                user_sharing_enabled=bool(data.get("user_sharing_enabled", data.get("user_sharing_enabled_status", False))),
                permission_status=str(data.get("permission_status", "unknown")).lower(),
                capture_session_status=str(data.get("capture_session_status", "inactive")).lower(),
                video_track_enabled=bool(data.get("video_track_enabled", data.get("video_track_enabled_status", False))),
                video_track_ready_state=str(data.get("video_track_ready_state", "none")).lower(),
                video_track_muted=bool(data.get("video_track_muted", data.get("video_track_muted_state", False))),
                captured_frames=int(data.get("captured_frames", data.get("actual_local_captured_frame_counter", 0))),
                last_captured_timestamp=float(data.get("last_captured_timestamp", data.get("last_captured_frame_timestamp", 0.0))),
                encoded_frames=int(data.get("encoded_frames", data.get("actual_encoded_frame_counter", 0))),
                transmitted_frames=int(data.get("transmitted_frames", data.get("actual_transmitted_frame_counter", 0))),
                explicit_user_stop=data.get("explicit_user_stop", data.get("explicit_user_stop_event")),
                capture_error=data.get("capture_error"),
                channel=channel,
                received_at=rx_time,
            )
            self._heartbeats[source_id] = hb

            # If heartbeat reports explicit user stop, record it
            if hb.explicit_user_stop:
                self._explicit_stops[source_id] = copy.deepcopy(hb.explicit_user_stop)
            elif not hb.user_sharing_enabled and data.get("stopped_by_user"):
                self._explicit_stops[source_id] = {
                    "reason": "USER_STOPPED",
                    "timestamp": rx_time,
                    "event_id": f"hb_stop_{int(rx_time)}",
                    "user_initiated": True,
                }
            elif hb.user_sharing_enabled:
                # User re-enabled sharing: clear explicit stop
                self._explicit_stops.pop(source_id, None)

            classification, _ = self.classify_source(source_id, now_epoch=rx_time)
            return classification

    def record_multi_source_heartbeat(
        self,
        payload: Dict[str, Any],
        channel: str = "WSS",
        received_at: Optional[float] = None,
    ) -> Dict[str, IOSMediaHealthClassification]:
        """Processes a bundled heartbeat payload that may contain both sources."""
        results = {}
        rx_time = received_at if received_at is not None else time.time()
        sources_dict = payload.get("sources")
        if isinstance(sources_dict, dict):
            for src_key, src_data in sources_dict.items():
                if isinstance(src_data, dict):
                    # Inherit top-level fields if not set in child
                    merged = dict(payload)
                    merged.update(src_data)
                    cls_res = self.record_heartbeat(src_key, merged, channel=channel, received_at=rx_time)
                    results[src_key] = cls_res
        else:
            # Single source payload
            src_id = payload.get("source_id", SOURCE_FRONT_CAMERA)
            cls_res = self.record_heartbeat(src_id, payload, channel=channel, received_at=rx_time)
            results[src_id] = cls_res
        return results

    def record_explicit_stop(
        self,
        source_id: str,
        stop_event: Dict[str, Any],
    ) -> None:
        """Records an explicit authenticated stop event from the user."""
        if source_id in ("IOS_SCREEN_SHARE", "screen", "IOS_FULL_SCREEN_SHARE"):
            source_id = SOURCE_SCREEN_SHARE
        elif source_id in ("IOS_CAMERA", "camera", "IOS_FRONT_CAMERA"):
            source_id = SOURCE_FRONT_CAMERA

        with self._lock:
            evt = dict(stop_event)
            if "reason" not in evt:
                evt["reason"] = "USER_STOPPED"
            if "timestamp" not in evt:
                evt["timestamp"] = time.time()
            evt["user_initiated"] = bool(evt.get("user_initiated", True))
            self._explicit_stops[source_id] = evt

            # Update existing heartbeat's sharing state if present
            hb = self._heartbeats.get(source_id)
            if hb:
                hb.user_sharing_enabled = False
                hb.explicit_user_stop = copy.deepcopy(evt)

    def record_receiver_stats(
        self,
        source_id: str,
        rx_fps: float = 0.0,
        rx_frames_count: int = 0,
        decoded_fps: float = 0.0,
        decoded_frames_count: int = 0,
        processing_fps: float = 0.0,
        processed_frames_count: int = 0,
        last_rx_epoch: Optional[float] = None,
        last_decoded_epoch: Optional[float] = None,
        last_processed_epoch: Optional[float] = None,
        webrtc_connected: bool = False,
    ) -> None:
        """Records receiver frame and pipeline performance observed on Windows."""
        if source_id in ("IOS_SCREEN_SHARE", "screen", "IOS_FULL_SCREEN_SHARE"):
            source_id = SOURCE_SCREEN_SHARE
        elif source_id in ("IOS_CAMERA", "camera", "IOS_FRONT_CAMERA"):
            source_id = SOURCE_FRONT_CAMERA

        with self._lock:
            snap = WindowsReceiverSnapshot(
                source_id=source_id,
                rx_fps=rx_fps,
                rx_frames_count=rx_frames_count,
                decoded_fps=decoded_fps,
                decoded_frames_count=decoded_frames_count,
                processing_fps=processing_fps,
                processed_frames_count=processed_frames_count,
                last_rx_epoch=last_rx_epoch,
                last_decoded_epoch=last_decoded_epoch,
                last_processed_epoch=last_processed_epoch,
                webrtc_connected=webrtc_connected,
            )
            self._receiver_stats[source_id] = snap

    def classify_source(
        self,
        source_id: str,
        now_epoch: Optional[float] = None,
    ) -> Tuple[IOSMediaHealthClassification, str]:
        """Compares the latest iOS heartbeat with Windows receiver statistics

        to classify the media health into one of 10 exact states.
        """
        if source_id in ("IOS_SCREEN_SHARE", "screen", "IOS_FULL_SCREEN_SHARE"):
            source_id = SOURCE_SCREEN_SHARE
        elif source_id in ("IOS_CAMERA", "camera", "IOS_FRONT_CAMERA"):
            source_id = SOURCE_FRONT_CAMERA

        now = now_epoch if now_epoch is not None else time.time()

        with self._lock:
            hb = self._heartbeats.get(source_id)
            rx = self._receiver_stats.get(source_id) or WindowsReceiverSnapshot(source_id)
            explicit_stop = self._explicit_stops.get(source_id)

            # 1. EXPLICIT STOP BY USER
            if explicit_stop is not None:
                reason = explicit_stop.get("reason", "USER_STOPPED")
                if reason in ("USER_STOPPED", "USER_STOPPED_CAMERA", "USER_STOPPED_BROADCAST") or explicit_stop.get("user_initiated", False):
                    return (
                        IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER,
                        f"Sharing intentionally stopped by user ({reason})",
                    )

            if hb is not None and hb.explicit_user_stop:
                stop_reason = hb.explicit_user_stop.get("reason", "USER_STOPPED")
                return (
                    IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER,
                    f"Sharing intentionally stopped by user ({stop_reason})",
                )

            # If user explicitly disabled sharing in UI
            if hb is not None and not hb.user_sharing_enabled and hb.heartbeat_seq > 0:
                # If explicit stop event was received or state is inactive
                if hb.capture_session_status in ("inactive", "stopped") or hb.video_track_ready_state in ("none", "ended"):
                    return (
                        IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER,
                        "Sharing is disabled by user in iOS companion",
                    )

            # 2. PERMISSION UNAVAILABLE
            if hb is not None and hb.permission_status in ("denied", "revoked"):
                return (
                    IOSMediaHealthClassification.IOS_PERMISSION_UNAVAILABLE,
                    f"iOS media permission unavailable ({hb.permission_status})",
                )

            # 3. CAPTURE INTERRUPTED
            if hb is not None:
                if hb.capture_error:
                    return (
                        IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED,
                        f"iOS capture error: {hb.capture_error}",
                    )
                if hb.user_sharing_enabled and hb.capture_session_status in ("interrupted", "error"):
                    return (
                        IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED,
                        f"iOS capture session status: {hb.capture_session_status}",
                    )
                if hb.user_sharing_enabled and hb.video_track_ready_state == "ended":
                    return (
                        IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED,
                        "iOS video track ended unexpectedly while sharing was enabled",
                    )

            # 4. CONTROL CHANNEL DISCONNECTED / HEARTBEAT TIMED OUT
            heartbeat_age = (now - hb.received_at) if hb else float("inf")
            heartbeat_stale = heartbeat_age > self.HEARTBEAT_TIMEOUT_SEC

            if heartbeat_stale:
                # If Windows is still receiving active frames despite heartbeat timeout:
                if rx.rx_fps > 0.0 or (rx.last_rx_epoch and (now - rx.last_rx_epoch) < self.FRAME_STALL_THRESHOLD_SEC):
                    # Check decoder and processing on Windows
                    if rx.decoded_fps == 0.0 and rx.rx_frames_count > 3:
                        return (
                            IOSMediaHealthClassification.WINDOWS_DECODER_STALLED,
                            "Windows receiving RTP packets but video decoder output stalled",
                        )
                    if (rx.decoded_fps > 0.0 or rx.rx_fps > 0.0) and rx.processing_fps == 0.0 and rx.rx_frames_count > 3:
                        return (
                            IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED,
                            "Windows receiving and decoding frames but inference processing stalled",
                        )
                    return (
                        IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING,
                        f"Frames receiving ({rx.rx_fps:.1f} FPS), but control heartbeat is stale ({heartbeat_age:.1f}s)",
                    )
                else:
                    # Both heartbeat and media frames are absent
                    if hb is not None or self._control_channel_connected:
                        return (
                            IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED,
                            f"iOS control heartbeat lost ({heartbeat_age:.1f}s ago) and no media frames arriving",
                        )
                    return (
                        IOSMediaHealthClassification.IOS_STATUS_UNKNOWN,
                        "Awaiting initial iOS media heartbeat and video frames",
                    )

            # From here on, heartbeat is FRESH and user_sharing_enabled is True
            # 5. WINDOWS DECODER STALLED
            if rx.rx_fps > 0.0 and rx.decoded_fps == 0.0 and rx.rx_frames_count > 3:
                return (
                    IOSMediaHealthClassification.WINDOWS_DECODER_STALLED,
                    f"Windows receiving RTP packets ({rx.rx_fps:.1f} FPS) but video decoder stalled",
                )

            # 6. WINDOWS PROCESSING STALLED
            if (rx.decoded_fps > 0.0 or rx.rx_fps > 0.0) and rx.processing_fps == 0.0 and rx.rx_frames_count > 3:
                return (
                    IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED,
                    f"Windows decoded frames ({rx.decoded_fps:.1f} FPS) but proctoring processing stalled",
                )

            # 7. ACTIVE AND RECEIVING
            # If frames are actively received, decoded, and processed
            if rx.rx_fps > 0.0 and (rx.decoded_fps > 0.0 or rx.decoded_frames_count > 0):
                return (
                    IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING,
                    f"Active: Rx {rx.rx_fps:.1f} FPS, Dec {rx.decoded_fps:.1f} FPS, Proc {rx.processing_fps:.1f} FPS",
                )

            # 8. IOS SHARING ACTIVE BUT NOT TRANSMITTING
            # iOS user enabled sharing, but track is muted, captured frames = 0, or capture stalled
            if hb.user_sharing_enabled:
                if hb.video_track_muted or hb.video_track_ready_state == "muted":
                    return (
                        IOSMediaHealthClassification.IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING,
                        "iOS video track is muted by system/user",
                    )
                if hb.captured_frames == 0 and hb.transmitted_frames == 0:
                    return (
                        IOSMediaHealthClassification.IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING,
                        "iOS sharing enabled but no frames captured or transmitted yet",
                    )
                # If capture counter hasn't advanced while sharing is active
                prev_hb = self._prev_heartbeats.get(source_id)
                if prev_hb and (now - prev_hb.received_at) >= 5.0:
                    if hb.captured_frames == prev_hb.captured_frames and hb.transmitted_frames == prev_hb.transmitted_frames:
                        return (
                            IOSMediaHealthClassification.IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING,
                            f"iOS local capture stalled: frame counter stopped at {hb.captured_frames}",
                        )

            # 9. IOS TRANSMITTING BUT WINDOWS NOT RECEIVING
            # iOS indicates it captured and transmitted frames, but Windows Rx FPS is 0
            if hb.user_sharing_enabled:
                if hb.transmitted_frames > 0 or hb.captured_frames > 0:
                    prev_hb = self._prev_heartbeats.get(source_id)
                    tx_advancing = (prev_hb and (hb.transmitted_frames > prev_hb.transmitted_frames or hb.captured_frames > prev_hb.captured_frames)) or (not prev_hb and hb.transmitted_frames > 0)
                    if tx_advancing and rx.rx_fps == 0.0:
                        return (
                            IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING,
                            f"iOS reports transmitting frames ({hb.transmitted_frames} sent, {hb.captured_frames} cap) but Windows received 0 frames",
                        )

            # 10. UNKNOWN / DEFAULT
            return (
                IOSMediaHealthClassification.IOS_STATUS_UNKNOWN,
                "Awaiting media stream data",
            )

    def get_source_diagnostics(self, source_id: str) -> Dict[str, Any]:
        """Returns comprehensive diagnostic dictionary for a source."""
        if source_id in ("IOS_SCREEN_SHARE", "screen", "IOS_FULL_SCREEN_SHARE"):
            source_id = SOURCE_SCREEN_SHARE
        elif source_id in ("IOS_CAMERA", "camera", "IOS_FRONT_CAMERA"):
            source_id = SOURCE_FRONT_CAMERA

        with self._lock:
            classification, reason = self.classify_source(source_id)
            hb = self._heartbeats.get(source_id)
            rx = self._receiver_stats.get(source_id) or WindowsReceiverSnapshot(source_id)
            explicit_stop = self._explicit_stops.get(source_id)

            return {
                "source_id": source_id,
                "classification": classification.value,
                "classification_reason": reason,
                "last_heartbeat": hb.to_dict() if hb else None,
                "last_heartbeat_timestamp": hb.timestamp if hb else None,
                "last_heartbeat_iso": (
                    datetime.fromtimestamp(hb.timestamp, tz=timezone.utc).isoformat()
                    if hb else None
                ),
                "heartbeat_age_seconds": (
                    round(max(0.0, time.time() - hb.received_at), 1)
                    if hb else None
                ),
                "heartbeat_channel": hb.channel if hb else None,
                "receiver_stats": rx.to_dict(),
                "explicit_stop": copy.deepcopy(explicit_stop),
                "user_sharing_enabled": hb.user_sharing_enabled if hb else False,
                "permission_status": hb.permission_status if hb else "unknown",
                "capture_session_status": hb.capture_session_status if hb else "inactive",
            }

    def get_compact_media_status(self) -> Dict[str, Any]:
        """Provides a compact visible iOS media status and expandable per-source diagnostics."""
        with self._lock:
            cam_diag = self.get_source_diagnostics(SOURCE_FRONT_CAMERA)
            scr_diag = self.get_source_diagnostics(SOURCE_SCREEN_SHARE)

            cam_cls = cam_diag["classification"]
            scr_cls = scr_diag["classification"]

            # Overall summary classification
            if (
                cam_cls == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING.value
                or scr_cls == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING.value
            ):
                summary_cls = IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING.value
                summary_label = "iOS Media: Active & Streaming"
            elif (
                cam_cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER.value
                and scr_cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER.value
            ):
                summary_cls = IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER.value
                summary_label = "iOS Media: Stopped by User"
            elif (
                cam_cls == IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED.value
                or scr_cls == IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED.value
            ):
                summary_cls = IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED.value
                summary_label = "iOS Media: Control Channel Disconnected"
            elif (
                cam_cls == IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING.value
                or scr_cls == IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING.value
            ):
                summary_cls = IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING.value
                summary_label = "iOS Media: Network / Transport Blocked"
            elif (
                cam_cls in (IOSMediaHealthClassification.WINDOWS_DECODER_STALLED.value, IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED.value)
                or scr_cls in (IOSMediaHealthClassification.WINDOWS_DECODER_STALLED.value, IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED.value)
            ):
                summary_cls = IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED.value
                summary_label = "iOS Media: Windows Processing Stalled"
            elif (
                cam_cls == IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED.value
                or scr_cls == IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED.value
            ):
                summary_cls = IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED.value
                summary_label = "iOS Media: Capture Interrupted"
            else:
                summary_cls = cam_cls if cam_cls != IOSMediaHealthClassification.IOS_STATUS_UNKNOWN.value else scr_cls
                summary_label = f"iOS Media: {summary_cls}"

            # Most recent heartbeat timestamp across sources
            cam_ts = cam_diag["last_heartbeat_timestamp"] or 0.0
            scr_ts = scr_diag["last_heartbeat_timestamp"] or 0.0
            latest_ts = max(cam_ts, scr_ts)

            return {
                "compact_status": summary_cls,
                "summary_label": summary_label,
                "last_heartbeat_timestamp": latest_ts if latest_ts > 0 else None,
                "last_heartbeat_iso": (
                    datetime.fromtimestamp(latest_ts, tz=timezone.utc).isoformat()
                    if latest_ts > 0 else None
                ),
                "camera_classification": cam_cls,
                "camera_label": cam_diag["classification_reason"],
                "screen_classification": scr_cls,
                "screen_label": scr_diag["classification_reason"],
                "control_channel_connected": self._control_channel_connected,
                "expandable_diagnostics": {
                    SOURCE_FRONT_CAMERA: cam_diag,
                    SOURCE_SCREEN_SHARE: scr_diag,
                },
            }
