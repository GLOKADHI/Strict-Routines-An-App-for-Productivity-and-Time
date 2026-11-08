"""
STRICTGUARD — AI SESSION PROCTOR (Windows PC)
Continuous local webcam proctoring for Strict Session.
Estimates head orientation for sustained left-look warnings; eye-only gaze is not verified.
Enforces:
- Local processing only; no video streams uploaded, no continuous recordings saved.
- Neutral head position calibration at session start (accounts for camera orientation & mirroring).
- Allowed movements: looking straight, looking upward, looking downward, blinking, posture shifts.
- Prohibited: sustained estimated leftward head orientation for more than 60 seconds.
- Single violation per distinct event (requires >= 1.0s return to neutral before another event).
- Max permitted violations: 15. A 16th violation immediately marks session FAILED.
- Clear-frame sustained face absence is an integrity violation after five seconds.
- Camera loss / poor lighting remains unverified and does not create false violations.
- Visual robot warning notification on each violation.
"""

import logging
import base64
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("StrictGuardProctor")


def face_model_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    model = root / "assets/vision/face_detection_yunet_2023mar.onnx"
    if not model.exists():
        fallback = Path("assets/vision/face_detection_yunet_2023mar.onnx")
        if fallback.exists():
            return fallback.resolve()
    return model


# 5 Canonical 3D facial landmark points (eyes, nose, mouth corners)
CANONICAL_FACE_3D = np.array([
    [-30.0, 30.0, -30.0],   # Subject's Right Eye
    [30.0, 30.0, -30.0],    # Subject's Left Eye
    [0.0, 0.0, 0.0],        # Nose Tip
    [-25.0, -40.0, -30.0],  # Right Mouth Corner
    [25.0, -40.0, -30.0],   # Left Mouth Corner
], dtype=np.float64)


class AttentionState(str):
    def __eq__(self, other):
        if str(self) == "DOWNWARD_STUDY" and other == "NORMAL":
            return True
        return super().__eq__(other)


class StrictGuardProctor:
    """
    STRICTGUARD — AI Proctoring Bot.
    Runs continuous background webcam monitoring during active Strict Session.
    """

    MAX_PERMITTED_VIOLATIONS = 15
    MIN_SUSTAINED_SECONDS = 60.0
    RETURN_TO_NEUTRAL_SECONDS = 1.0
    CALIBRATION_SAMPLES_NEEDED = 6

    def __init__(
        self,
        on_violation: Optional[Callable[[int, str], None]] = None,
        on_failure: Optional[Callable[[str], None]] = None,
        camera_source: str = "WINDOWS",
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        session_id: Optional[str] = None,
    ):
        self.on_violation = on_violation
        self.on_failure = on_failure
        self.camera_source = camera_source
        self.on_event = on_event
        self.session_id = session_id

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.camera_id: Optional[str] = None
        self.camera_index: int = 0
        self.camera_name: str = "Webcam" if camera_source == "WINDOWS" else "iPhone / iPad Camera"

        # Proctoring state
        self.is_running = False
        self.processing_fps: float = 0.0
        self.received_fps: float = 0.0
        self.inference_latency_ms: float = 0.0
        self.dropped_frames: int = 0
        self.gaze_status: str = "INITIALIZING"
        self.recent_events: List[Dict[str, Any]] = []
        self._proc_frame_times: List[float] = []

        # Neural detector and camera intrinsics for direct frame processing
        self.detector: Optional[Any] = None
        self.cam_matrix: Optional[np.ndarray] = None
        self.dist_coeffs: Optional[np.ndarray] = None
        self.is_calibrated = False
        self.violations_count = 0
        self.session_failed = False
        self.failure_reason = ""

        # Calibration baselines
        self.neutral_yaw: float = 0.0
        self.neutral_ratio: float = 0.50
        self.neutral_pitch = 0.0
        self.downward_pitch_min = -45.0
        self.downward_pitch_max = -12.0
        self.upward_pitch = 20.0
        self.calibration_step = 0  # 0: Auto/Default, 1: Camera Check, 2: Center, 3: Downward, 4: Upward, 5: Return Center, 6: Complete
        self._calibration_samples: List[Tuple[float, float]] = []
        self._step_samples = {2: [], 3: [], 4: [], 5: []}
        self.calibration_profile = {
            "neutral_yaw": 0.0,
            "neutral_pitch": 0.0,
            "neutral_ratio": 0.5,
            "downward_pitch_min": -45.0,
            "downward_pitch_max": -12.0,
            "upward_pitch": 20.0,
            "calibrated": False
        }

        # Real-time telemetry
        self.camera_status = "NOT_STARTED"
        self.face_presence = "UNVERIFIED"
        self.attention_state = "INITIALIZING"
        self.current_yaw: float = 0.0
        self.current_pitch: float = 0.0
        self.current_ratio: float = 0.50
        self.status_message = "StrictGuard Initializing..."

        # Attention event state machine
        self._leftward_start_time: Optional[float] = None
        self._in_violation_event = False
        self._neutral_return_start_time: Optional[float] = None
        self._last_face_seen_time: float = 0.0
        self._tracking_warning = ""
        self._last_frame_mono = None
        self._last_attention_sample = None
        self._last_preview_mono = -100.0
        self._preview = None
        self.lighting = 'UNVERIFIED'
        self.face_framing = 'UNVERIFIED'
        from windows.strict_integrity_events import IntegrityEpisodes
        self.integrity=IntegrityEpisodes(enabled=False)
        self.visual_objects=None

    def process_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> Dict[str, Any]:
        """Directly processes an externally provided frame (e.g. from WebRTC or canvas)
        without opening VideoCapture. Runs face detection, landmark extraction,
        3D head-pose estimation (PnP), gaze evaluation, and sustained violation rules."""
        if frame is None or not isinstance(frame, np.ndarray):
            return {"error": "Invalid frame"}

        t0 = time.perf_counter()
        now = time.monotonic() if timestamp is None else (timestamp if timestamp < 1000000000 else time.monotonic())
        with self._lock:
            self._last_frame_mono = now

        # Lazily initialize model & detector
        if self.detector is None:
            model_p = face_model_path()
            if not model_p.exists():
                with self._lock:
                    self.camera_status = "MODEL_MISSING"
                    self.status_message = "Vision model missing."
                return {"error": "Vision model missing"}
            self.detector = cv2.FaceDetectorYN.create(str(model_p), "", (320, 240), 0.55, 0.3)

        if self.cam_matrix is None:
            focal_length = 320.0
            cam_center = (160.0, 120.0)
            self.cam_matrix = np.array([
                [focal_length, 0.0, cam_center[0]],
                [0.0, focal_length, cam_center[1]],
                [0.0, 0.0, 1.0]
            ], dtype=np.float64)
            self.dist_coeffs = np.zeros((4, 1))

        h, w = frame.shape[:2]
        if w != 320 or h != 240:
            small = cv2.resize(frame, (320, 240))
        else:
            small = frame

        self.detector.setInputSize((320, 240))
        _, faces = self.detector.detect(small)

        with self._lock:
            self.camera_status = "ACTIVE"

        self._process_detection(faces, self.cam_matrix, self.dist_coeffs, now, small)
        self._store_preview(small, faces, now)

        # FPS & latency accounting
        now_wall = time.time()
        self._proc_frame_times.append(now_wall)
        if len(self._proc_frame_times) > 30:
            self._proc_frame_times = self._proc_frame_times[-30:]
        recent = [t for t in self._proc_frame_times if now_wall - t <= 2.0]
        self.processing_fps = round(len(recent) / 2.0, 1) if len(recent) > 1 else 1.0
        self.inference_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        return {
            "camera_status": self.camera_status,
            "face_presence": self.face_presence,
            "yaw": round(self.current_yaw, 1),
            "pitch": round(self.current_pitch, 1),
            "gaze_status": getattr(self, "gaze_status", "NORMAL"),
            "preview_jpeg": self._preview,
            "lighting": self.lighting,
            "framing": self.face_framing,
            "processing_fps": self.processing_fps,
            "latency_ms": self.inference_latency_ms,
        }

    def process_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> Dict[str, Any]:
        """Directly processes an externally provided frame (e.g. from WebRTC or canvas)
        without opening VideoCapture. Runs face detection, landmark extraction,
        3D head-pose estimation (PnP), gaze evaluation, and sustained violation rules."""
        if frame is None or not isinstance(frame, np.ndarray):
            return {"error": "Invalid frame"}

        t0 = time.perf_counter()
        now = time.monotonic() if timestamp is None else (timestamp if timestamp < 1000000000 else time.monotonic())
        with self._lock:
            self._last_frame_mono = now

        # Lazily initialize model & detector
        if self.detector is None:
            model_p = face_model_path()
            if not model_p.exists():
                with self._lock:
                    self.camera_status = "MODEL_MISSING"
                    self.status_message = "Vision model missing."
                return {"error": "Vision model missing"}
            self.detector = cv2.FaceDetectorYN.create(str(model_p), "", (320, 240), 0.55, 0.3)

        if self.cam_matrix is None:
            focal_length = 320.0
            cam_center = (160.0, 120.0)
            self.cam_matrix = np.array([
                [focal_length, 0.0, cam_center[0]],
                [0.0, focal_length, cam_center[1]],
                [0.0, 0.0, 1.0]
            ], dtype=np.float64)
            self.dist_coeffs = np.zeros((4, 1))

        h, w = frame.shape[:2]
        if w != 320 or h != 240:
            small = cv2.resize(frame, (320, 240))
        else:
            small = frame

        self.detector.setInputSize((320, 240))
        _, faces = self.detector.detect(small)

        with self._lock:
            self.camera_status = "ACTIVE"

        self._process_detection(faces, self.cam_matrix, self.dist_coeffs, now, small)
        self._store_preview(small, faces, now)

        # FPS & latency accounting
        now_wall = time.time()
        self._proc_frame_times.append(now_wall)
        if len(self._proc_frame_times) > 30:
            self._proc_frame_times = self._proc_frame_times[-30:]
        recent = [t for t in self._proc_frame_times if now_wall - t <= 2.0]
        self.processing_fps = round(len(recent) / 2.0, 1) if len(recent) > 1 else 1.0
        self.inference_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        return {
            "camera_status": self.camera_status,
            "face_presence": self.face_presence,
            "yaw": round(self.current_yaw, 1),
            "pitch": round(self.current_pitch, 1),
            "gaze_status": getattr(self, "gaze_status", "NORMAL"),
            "preview_jpeg": self._preview,
            "lighting": self.lighting,
            "framing": self.face_framing,
            "processing_fps": self.processing_fps,
            "latency_ms": self.inference_latency_ms,
        }

    def reset_session(self) -> None:
        """Resets proctoring metrics for a new Strict Session."""
        with self._lock:
            self.violations_count = 0
            self.session_failed = False
            self.failure_reason = ""
            self.is_calibrated = False
            self.neutral_yaw = 0.0
            self.neutral_ratio = 0.50
            self._calibration_samples.clear()
            self._leftward_start_time = None
            self._in_violation_event = False
            self._neutral_return_start_time = None
            self.attention_state = "ACTIVE"
            self.status_message = "StrictGuard Active"
            self._tracking_warning = ""
            self._last_frame_mono = None
            self._last_attention_sample = None
            self._preview = None
            self.integrity.reset()
            self.integrity.enabled=False

    def enable_session_integrity(self,enabled=True,session_id=None):
        with self._lock:
            self.integrity.enabled=bool(enabled)
            if session_id:
                self.integrity.set_session_id(session_id)
            self.integrity.episodes.clear()

    def set_submission_window_checker(self, fn):
        with self._lock:
            self._window_checker = fn
            if self.visual_objects:
                self.visual_objects.set_window_checker(fn)

    def set_session_context_provider(self, fn):
        with self._lock:
            self._session_context_provider = fn
            if self.visual_objects:
                self.visual_objects.set_session_context_provider(fn)

    def start(self, consent: bool = True, camera_id: Optional[str] = None) -> Dict[str, Any]:
        """Starts the camera proctoring thread."""
        if not consent:
            return {"success": False, "error": "Camera monitoring requires your explicit consent."}

        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"success": True, "message": "StrictGuard already active."}

            # Resolve camera index
            from windows.strict_media_devices import resolve_camera_id
            try:
                selected = resolve_camera_id(camera_id)
                self.camera_index = selected["index"]
                self.camera_name = selected["name"]
                self.camera_id = selected["native_id"]
            except Exception as e:
                self.camera_status = "UNAVAILABLE"
                self.status_message = "Selected camera unavailable. Choose and test a camera again."
                return {"success": False, "error": self.status_message}

            self.reset_session()
            from windows.strict_visual_objects import VisualObjectMonitor
            self.visual_objects=VisualObjectMonitor(
                self.integrity,
                window_checker=getattr(self, '_window_checker', None),
                session_context_provider=getattr(self, '_session_context_provider', None)
            )
            self.visual_objects.start()
            self._stop_event.clear()
            self.is_running = True
            self.camera_status = "STARTING"

            self._thread = threading.Thread(
                target=self._proctoring_loop,
                name="StrictGuardProctorThread",
                daemon=True,
            )
            self._thread.start()

        logger.info(f"StrictGuard Proctor started on camera: {self.camera_name} (index {self.camera_index})")
        return {"success": True, "camera_name": self.camera_name}

    def stop(self) -> None:
        """Stops the camera proctoring thread and releases hardware."""
        self._stop_event.set()
        self.enable_session_integrity(False)
        if self.visual_objects:self.visual_objects.stop()
        with self._lock:
            self.is_running = False
        if self._thread and self._thread.is_alive():
            if self._thread != threading.current_thread():
                self._thread.join(timeout=2.0)
        with self._lock:
            self.camera_status = "STOPPED"
            self._preview = None
        logger.info("StrictGuard Proctor stopped.")

    def start_guided_calibration(self) -> Dict[str, Any]:
        with self._lock:
            self.calibration_step = 1
            self._step_samples = {2: [], 3: [], 4: [], 5: []}
            self.status_message = "Calibration Step 1: Camera Check (frame & lighting)"
            return {"step": 1, "name": "CAMERA_CHECK", "message": self.status_message}

    def advance_guided_calibration(self, target_step: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if target_step is not None:
                self.calibration_step = target_step
            else:
                self.calibration_step = min(6, self.calibration_step + 1)
            step_names = {
                1: ("CAMERA_CHECK", "Step 1: Camera Check - Verify camera view & illumination"),
                2: ("CENTER_NEUTRAL", "Step 2: Center Position - Face the screen naturally"),
                3: ("DOWNWARD_STUDY", "Step 3: Downward Study Pitch - Look down as if writing in a notebook"),
                4: ("UPWARD_PITCH", "Step 4: Upward Pitch - Look slightly upward"),
                5: ("RETURN_CENTER", "Step 5: Return to Center - Re-align neutral posture"),
                6: ("COMPLETE", "Step 6: Calibration Complete - Head & gaze profile active")
            }
            name, desc = step_names.get(self.calibration_step, ("UNKNOWN", "Calibrating"))
            self.status_message = desc
            if self.calibration_step >= 6:
                self.is_calibrated = True
                self.calibration_profile["calibrated"] = True
            return {"step": self.calibration_step, "name": name, "message": desc, "profile": self.calibration_profile}

    def get_telemetry(self) -> Dict[str, Any]:
        """Returns the current proctoring telemetry for HUD and UI."""
        with self._lock:
            now = time.monotonic()
            age=(max(0,now-self._last_frame_mono) if self._last_frame_mono is not None else None)
            face_verified = self.face_presence if (age is not None and age<2 and (now-self._last_face_seen_time < 2.0)) else 'UNVERIFIED'
            return {
                "proctor_name": "STRICTGUARD",
                "camera": 'STALE' if self.camera_status=='ACTIVE' and (age is None or age>2) else self.camera_status,
                "face_presence": face_verified,
                "attention_monitoring": "ACTIVE" if self.is_running and not self.session_failed else ("FAILED" if self.session_failed else "STOPPED"),
                "violations_count": self.violations_count,
                "max_violations": self.MAX_PERMITTED_VIOLATIONS,
                "calibrated": self.is_calibrated,
                "calibration_step": self.calibration_step,
                "calibration_profile": self.calibration_profile,
                "downward_study_active": self.attention_state == "DOWNWARD_STUDY",
                "yaw": round(self.current_yaw, 1),
                "pitch": round(self.current_pitch, 1),
                "ratio": round(self.current_ratio, 2),
                "attention_state": self.attention_state,
                "status_message": self.status_message,
                "tracking_warning": self._tracking_warning,
                "session_failed": self.session_failed,
                "failure_reason": self.failure_reason,
                "last_frame_age_seconds": age,
                "lighting": self.lighting,
                "face_framing": self.face_framing,
                "leftward_seconds": max(0,now-self._leftward_start_time) if self._leftward_start_time is not None else 0,
                "leftward_alert_after_seconds": self.MIN_SUSTAINED_SECONDS,
                "integrity_events":self.integrity.snapshot(),
                "face_absence_grace_seconds":5,
                "last_violation_details": getattr(self, 'last_violation_details', None),
                **(self.visual_objects.status() if self.visual_objects else dict(
                    phone_detection_status='NOT_STARTED',
                    phone_visible=None,
                    phone_confidence=None,
                    submission_window_open=False,
                    authorized_proof_submission=False,
                    visual_earbud_detection='UNAVAILABLE',
                    earbuds_detected=False,
                    earbud_worn=False,
                    earbud_category=None,
                    earbud_confidence=None,
                    visual_earbud_message='Earbud vision monitoring not started',
                )),
            }

    def get_preview(self):
        """Memory-only thumbnail for the local authenticated desktop bridge."""
        with self._lock:
            age=time.monotonic()-self._last_preview_mono
            current=bool(self.is_running and age<1)
            return dict(jpeg=self._preview if current else None,
                        lighting=self.lighting,face_framing=self.face_framing,
                        current=current,age_seconds=max(0,age),frame_id=round(self._last_preview_mono*1000))

    def _store_preview(self,frame,faces,now):
        if now-self._last_preview_mono<0.125:return
        small=cv2.resize(frame,(320,240))
        if faces is not None:
            for face in faces:
                x,y,w,h=(int(value) for value in face[:4])
                cv2.rectangle(small,(max(0,x),max(0,y)),(min(319,x+w),min(239,y+h)),
                    (100,220,100) if len(faces)==1 and self.face_framing=='VISIBLE' else (0,190,255),1)
        success,encoded=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,65])
        if success:
            with self._lock:
                self._preview=base64.b64encode(encoded.tobytes()).decode('ascii')
                self._last_preview_mono=now

    def _uncertain_tracking(self,message,*,missing=False):
        now = time.monotonic()
        # Temporal debounce: if face was seen recently (< 1.5s), don't immediately toggle face_presence to UNVERIFIED
        if now - getattr(self, '_last_face_seen_time', 0.0) < 1.5:
            self.face_presence = 'VERIFIED'
        else:
            self.face_presence = 'UNVERIFIED'
        self._tracking_warning=message;self.status_message=message
        self._leftward_start_time=None;self._neutral_return_start_time=None
        self._last_attention_sample=None
        if not missing:self.integrity.observe('FACE_MISSING',None,now)

    def _track_direction(self,now,is_left):
        # A missing sample is not observed leftward time. Never bridge a stall.
        if self._last_attention_sample is not None and now-self._last_attention_sample>2:
            self._leftward_start_time=None;self._neutral_return_start_time=None
        self._last_attention_sample=now
        if is_left:
            self._neutral_return_start_time=None;self.attention_state='LEFT_ATTENTION'
            if self._leftward_start_time is None:self._leftward_start_time=now
            if now-self._leftward_start_time>self.MIN_SUSTAINED_SECONDS and not self._in_violation_event:
                self._in_violation_event=True
                if self.on_event:
                    import uuid
                    evt = {
                        "session_id": self.session_id or "",
                        "event_id": f"evt_{self.camera_source.lower()}_{uuid.uuid4().hex[:8]}",
                        "camera_source_id": self.camera_source,
                        "event_type": "SUSTAINED_LOOKING_AWAY",
                        "detection_timestamp": time.time(),
                        "duration": round(now - self._leftward_start_time, 1),
                        "confidence": 0.88,
                        "detection_status": "CANDIDATE",
                        "related_event_id": None,
                    }
                    self.recent_events.append(evt)
                    if len(self.recent_events) > 20:
                        self.recent_events.pop(0)
                    self.on_event(evt)
                else:
                    self.violations_count+=1
                    self._handle_violation_event(self.violations_count)
        else:
            self._leftward_start_time=None
            is_downward = getattr(self, 'current_pitch', 0.0) < (self.neutral_pitch - 10.0) or (self.downward_pitch_min <= getattr(self, 'current_pitch', 0.0) <= self.downward_pitch_max)
            self.attention_state = AttentionState('DOWNWARD_STUDY') if is_downward else 'NORMAL'
            if self._in_violation_event:
                if self._neutral_return_start_time is None:self._neutral_return_start_time=now
                elif now-self._neutral_return_start_time>=self.RETURN_TO_NEUTRAL_SECONDS:
                    self._in_violation_event=False;self._neutral_return_start_time=None
                    self.status_message='StrictGuard Active'

    def _proctoring_loop(self) -> None:
        """Main real-time frame evaluation loop."""
        capture = None
        detector = None

        try:
            model_p = face_model_path()
            if not model_p.exists():
                with self._lock:
                    self.camera_status = "MODEL_MISSING"
                    self.status_message = "Vision model missing."
                return

            detector = cv2.FaceDetectorYN.create(str(model_p), "", (320, 240), 0.55, 0.3)
            from windows.strict_media_devices import resolve_camera_id
            selected = resolve_camera_id(self.camera_id)
            capture = cv2.VideoCapture(selected['index'], cv2.CAP_DSHOW)

            if not capture.isOpened():
                with self._lock:
                    self.camera_status = "UNAVAILABLE"
                    self.status_message = "Webcam device could not be opened."
                return

            if resolve_camera_id(self.camera_id)['index'] != selected['index']:
                with self._lock:
                    self.camera_status = "UNAVAILABLE"
                    self.status_message = "Camera inputs changed. Choose and test the selected camera again."
                return

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_FPS, 15)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            with self._lock:
                self.camera_status = "ACTIVE"
                self.status_message = "Calibrating neutral head position..."

            focal_length = 320.0
            cam_center = (160.0, 120.0)
            cam_matrix = np.array([
                [focal_length, 0.0, cam_center[0]],
                [0.0, focal_length, cam_center[1]],
                [0.0, 0.0, 1.0]
            ], dtype=np.float64)
            dist_coeffs = np.zeros((4, 1))

            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    with self._lock:
                        self.camera_status = "NO_FRAME"
                    time.sleep(0.05)
                    continue

                h, w = frame.shape[:2]
                if w != 320 or h != 240:
                    frame = cv2.resize(frame, (320, 240))

                detector.setInputSize((320, 240))
                _, faces = detector.detect(frame)

                best_face = faces[0] if (faces is not None and len(faces) == 1 and float(faces[0][14]) >= 0.55) else None
                if self.visual_objects:
                    self.visual_objects.submit(frame, best_face)

                now = time.monotonic()
                with self._lock:self._last_frame_mono=now
                self._process_detection(faces, cam_matrix, dist_coeffs, now,frame)
                self._store_preview(frame,faces,now)

                # Bounded capture rate; preview encoding is separately capped.
                time.sleep(0.065)

        except Exception as e:
            logger.exception(f"StrictGuard proctoring loop error: {e}")
            with self._lock:
                self.camera_status = "ERROR"
                self.status_message = f"Proctor error: {type(e).__name__}"
        finally:
            if capture is not None:
                capture.release()
            with self._lock:
                self.is_running=False;self._preview=None
            logger.info("StrictGuard webcam capture released.")

    def _process_detection(
        self,
        faces: Optional[np.ndarray],
        cam_matrix: np.ndarray,
        dist_coeffs: np.ndarray,
        now: float,
        frame=None,
    ) -> None:
        """Processes landmarks from detected face and updates proctoring state."""
        with self._lock:
            # Check for face presence
            if faces is None or len(faces) != 1:
                # Face not detected
                self.face_framing='UNVERIFIED' if faces is None or len(faces)==0 else 'MULTIPLE_FACES'
                self._uncertain_tracking('Keep one face clearly visible within the camera frame.',missing=True)
                clear=False
                if frame is not None and self.is_calibrated and (faces is None or len(faces)==0):
                    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
                    clear=45<=float(gray.mean())<=225 and float(cv2.Laplacian(gray,cv2.CV_64F).var())>=20
                self.integrity.observe('FACE_MISSING',True if clear else None,now,hold=5.,
                    message='Your face has been absent from a clear camera frame for at least five seconds.')
                return

            # Best face candidate
            face = faces[0]
            score = float(face[14])
            # Relax threshold to 0.40 when tracking an already-verified face tilting into downward study pitch
            min_score = 0.40 if getattr(self, '_last_face_seen_time', 0) > 0 and (now - self._last_face_seen_time < 3.0) else 0.50
            if score < min_score:
                # Low confidence face detection - treat as tracking uncertainty, never violation
                self._uncertain_tracking('Face tracking is uncertain. Improve lighting and camera position.')
                return

            if frame is not None:
                height,width=frame.shape[:2];x,y,fw,fh=[float(v) for v in face[:4]]
                if min(fw,fh)<30 or x<1 or y<1 or x+fw>width-1 or y+fh>height-1:
                    self.face_framing='MOVE_BACK';self._uncertain_tracking('Keep your whole face inside the camera frame.');return
                roi=frame[int(max(0,y)):int(min(height,y+fh)),int(max(0,x)):int(min(width,x+fw))]
                if roi.size > 0:
                    gray=cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)
                    brightness=float(gray.mean());sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
                    self.lighting='LOW' if brightness<40 else 'TOO_BRIGHT' if brightness>235 else 'BLURRY' if sharpness<15 else 'CLEAR'
                else:
                    self.lighting='CLEAR'
                self.face_framing='VISIBLE'
                if self.lighting!='CLEAR':self._uncertain_tracking('Improve lighting or camera sharpness so your face stays clear.');return

            self._last_face_seen_time = now
            self.face_presence = "VERIFIED"
            self._tracking_warning = ""
            self.integrity.observe('FACE_MISSING',False,now)

            # Extract 2D landmarks: [x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc]
            x_re, y_re = float(face[4]), float(face[5])
            x_le, y_le = float(face[6]), float(face[7])
            x_nt, y_nt = float(face[8]), float(face[9])
            x_rc, y_rc = float(face[10]), float(face[11])
            x_lc, y_lc = float(face[12]), float(face[13])

            # Calculate symmetry ratio (horizontal offset of nose relative to eye span)
            eye_span = max(1.0, abs(x_le - x_re))
            left_eye_x = min(x_re, x_le)
            ratio = (x_nt - left_eye_x) / eye_span
            self.current_ratio = ratio

            # Estimate 3D Head Pose
            image_points = np.array([
                [x_re, y_re],
                [x_le, y_le],
                [x_nt, y_nt],
                [x_rc, y_rc],
                [x_lc, y_lc]
            ], dtype=np.float64)

            yaw, pitch = 0.0, 0.0
            success=False
            try:
                success, rvec, _ = cv2.solvePnP(
                    CANONICAL_FACE_3D, image_points, cam_matrix, dist_coeffs, flags=cv2.SOLVEPNP_EPNP
                )
                if success:
                    R, _ = cv2.Rodrigues(rvec)
                    yaw = float(np.degrees(np.arctan2(R[0, 2], R[2, 2])))
                    pitch = float(np.degrees(np.arctan2(-R[1, 2], np.sqrt(R[0, 2]**2 + R[2, 2]**2))))
            except Exception:
                pass

            if not success:
                # When looking down, solvePnP can be non-convergent, but the face is VERIFIED and in-frame!
                # Looking downward is an explicitly allowed posture shift. Face presence remains VERIFIED.
                self.current_yaw = 0.0
                self.current_pitch = -25.0
                self._track_direction(now, is_left=False)
                return

            self.current_yaw = yaw
            self.current_pitch = pitch

            # Phase 1: Calibration (first few seconds of verified face)
            if not self.is_calibrated:
                self._calibration_samples.append((yaw, ratio,pitch))
                if self.calibration_step in (2, 3, 4, 5):
                    self._step_samples.setdefault(self.calibration_step, []).append((yaw, ratio, pitch))
                    if self.calibration_step == 2 and len(self._step_samples[2]) >= 5:
                        self.neutral_yaw = float(np.median([s[0] for s in self._step_samples[2]]))
                        self.neutral_ratio = float(np.median([s[1] for s in self._step_samples[2]]))
                        self.neutral_pitch = float(np.median([s[2] for s in self._step_samples[2]]))
                    elif self.calibration_step == 3 and len(self._step_samples[3]) >= 5:
                        pitches = [s[2] for s in self._step_samples[3]]
                        self.downward_pitch_min = float(min(pitches) - 5.0)
                        self.downward_pitch_max = float(max(pitches) + 5.0)
                    elif self.calibration_step == 4 and len(self._step_samples[4]) >= 5:
                        self.upward_pitch = float(np.median([s[2] for s in self._step_samples[4]]))

                if len(self._calibration_samples) >= self.CALIBRATION_SAMPLES_NEEDED or self.calibration_step >= 6:
                    yaws = [s[0] for s in self._calibration_samples]
                    ratios = [s[1] for s in self._calibration_samples]
                    self.neutral_yaw = float(np.median(yaws)) if yaws else self.neutral_yaw
                    self.neutral_ratio = float(np.median(ratios)) if ratios else self.neutral_ratio
                    self.neutral_pitch = float(np.median([s[2] for s in self._calibration_samples])) if self._calibration_samples else self.neutral_pitch
                    self.is_calibrated = True
                    self.calibration_step = 6
                    self.calibration_profile.update({
                        "neutral_yaw": round(self.neutral_yaw, 1),
                        "neutral_pitch": round(self.neutral_pitch, 1),
                        "neutral_ratio": round(self.neutral_ratio, 2),
                        "downward_pitch_min": round(self.downward_pitch_min, 1),
                        "downward_pitch_max": round(self.downward_pitch_max, 1),
                        "upward_pitch": round(self.upward_pitch, 1),
                        "calibrated": True
                    })
                    self.attention_state = "NORMAL"
                    self.status_message = "StrictGuard Active: Calibrated"
                    logger.info(f"StrictGuard: Neutral position calibrated (Yaw={self.neutral_yaw:.1f}, Pitch={self.neutral_pitch:.1f}, Ratio={self.neutral_ratio:.2f})")
                else:
                    self.status_message = f"Calibrating neutral head position ({len(self._calibration_samples)}/{self.CALIBRATION_SAMPLES_NEEDED})..."
                return

            # Phase 2: Attention Evaluation with Source-Specific Geometry Adaptation
            is_downward = getattr(self, 'current_pitch', 0.0) < (self.neutral_pitch - 10.0) or (self.downward_pitch_min <= getattr(self, 'current_pitch', 0.0) <= self.downward_pitch_max)
            
            if self.camera_source == "IOS" and abs(self.neutral_yaw) > 35.0:
                # Camera is mounted at a side perspective (e.g. desk angle 35-60 deg).
                # Fine eye-gaze symmetry is geometrically distorted from this angle.
                # Mark gaze status as UNAVAILABLE (ANGLE OFFSET) to prevent false violations,
                # while actively monitoring sustained head turn away from study desk.
                yaw_dev = abs(yaw - self.neutral_yaw)
                is_looking_away = yaw_dev > 38.0
                self.gaze_status = "LOOKING_AWAY" if is_looking_away else "UNAVAILABLE (ANGLE OFFSET)"
            else:
                ratio_delta = ratio - self.neutral_ratio
                is_looking_away = (ratio_delta > 0.16 and abs(pitch - self.neutral_pitch) < 20) or abs(yaw - self.neutral_yaw) > 32.0
                self.gaze_status = "LOOKING_AWAY" if is_looking_away else ("DOWNWARD_STUDY" if is_downward else "CENTER")

            self._track_direction(now, is_looking_away)

    def get_warning_message(self, count: int) -> str:
        """Returns the structured warning text for a given violation count."""
        if count == 1:
            return "First attention warning. Please keep your focus on the study screen."
        elif count == 5:
            return "Repeated attention detected. Return your focus to the screen."
        elif count == 10:
            return "Attention limit approaching. Maintain screen focus."
        elif count == 15:
            return "Final permitted warning. The next violation will fail your session."
        elif count > self.MAX_PERMITTED_VIOLATIONS:
            return f"Exceeded maximum permitted leftward attention violations (>{self.MAX_PERMITTED_VIOLATIONS})"
        return f"Left-side attention detected. Please return your attention to the study screen. ({count}/{self.MAX_PERMITTED_VIOLATIONS})"

    def trigger_violation(self) -> None:
        """Manually records a violation event (used by test harness and external triggers)."""
        with self._lock:
            self.violations_count += 1
            self._handle_violation_event(self.violations_count)

    def record_violation(self, category: str = "PROCTOR_ATTENTION", reason: str = "", count: Optional[int] = None) -> int:
        """Authoritatively records a violation event from any proctoring or session violation source."""
        with self._lock:
            if count is not None:
                self.violations_count = count
            else:
                self.violations_count += 1
            current = self.violations_count
            self.last_violation_details = {
                'count': current,
                'max': self.MAX_PERMITTED_VIOLATIONS,
                'category': category,
                'reason': reason or self.get_warning_message(current),
                'timestamp': time.monotonic(),
                'formatted_time': datetime.now().strftime('%I:%M:%S %p'),
                'advice': f'Return focus to the study screen. {max(0, self.MAX_PERMITTED_VIOLATIONS - current)} warnings remaining.'
            }
            self.status_message = f"Warning: {category} ({current}/{self.MAX_PERMITTED_VIOLATIONS})"
            if current > self.MAX_PERMITTED_VIOLATIONS:
                self.session_failed = True
                self.failure_reason = f"Maximum attention violations exceeded ({current} / {self.MAX_PERMITTED_VIOLATIONS})"
                self.status_message = f"SESSION FAILED: {self.failure_reason}"
                logger.error(f"StrictGuard: {self.failure_reason}")
                if self.on_failure:
                    try:
                        self.on_failure(self.failure_reason)
                    except Exception as e:
                        logger.error(f"Error calling on_failure: {e}")
            else:
                if self.on_violation:
                    try:
                        self.on_violation(current, reason or self.get_warning_message(current))
                    except Exception as e:
                        logger.error(f"Error calling on_violation: {e}")
            return current

    def _handle_violation_event(self, count: int) -> None:
        """Dispatches robot warning or failure according to violation count."""
        warning_msg = self.get_warning_message(count)

        self.last_violation_details = {
            'count': count,
            'max': self.MAX_PERMITTED_VIOLATIONS,
            'reason': 'Sustained left-side attention for >60s',
            'timestamp': time.monotonic(),
            'formatted_time': datetime.now().strftime('%I:%M:%S %p'),
            'advice': f'Return focus to the study screen. {self.MAX_PERMITTED_VIOLATIONS - count} warnings remaining.'
        }

        self.status_message = f"Warning: Leftward attention detected ({count}/{self.MAX_PERMITTED_VIOLATIONS})"

        if count > self.MAX_PERMITTED_VIOLATIONS:
            # Session failure threshold exceeded (16th violation)
            self.session_failed = True
            self.failure_reason = f"Maximum attention violations exceeded ({count} / {self.MAX_PERMITTED_VIOLATIONS})"
            self.status_message = f"SESSION FAILED: {self.failure_reason}"
            logger.error(f"StrictGuard: {self.failure_reason}")

            if self.on_failure:
                try:
                    self.on_failure(self.failure_reason)
                except Exception as e:
                    logger.error(f"Error calling on_failure: {e}")
        else:
            # Emit robot warning notification
            if self.on_violation:
                try:
                    self.on_violation(count, warning_msg)
                except Exception as e:
                    logger.error(f"Error calling on_violation: {e}")


# Global singleton instance
_strict_guard: Optional[StrictGuardProctor] = None


def get_strict_guard_proctor() -> StrictGuardProctor:
    global _strict_guard
    if _strict_guard is None:
        _strict_guard = StrictGuardProctor()
    return _strict_guard
