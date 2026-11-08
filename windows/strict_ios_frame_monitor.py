"""
Independent StrictGuard processing of decoded iPhone frames, memory only.
Coordinates with StrictDualCameraProctor for simultaneous multi-camera proctoring.
"""
from __future__ import annotations

import base64
import threading
import time
from typing import Any, Dict, Optional

import cv2
import numpy as np


class IOSFrameMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest: Dict[str, Any] = {}
        self.error = ''

        from windows.strict_dual_camera_proctor import StrictDualCameraProctor
        self.dual_proctor = StrictDualCameraProctor.get_instance()

    def process_jpeg(self, jpeg_data):
        if not jpeg_data:
            return
        try:
            if isinstance(jpeg_data, str):
                if ',' in jpeg_data:
                    jpeg_data = jpeg_data.split(',', 1)[1]
                raw = base64.b64decode(jpeg_data)
            else:
                raw = bytes(jpeg_data)
            nparr = np.frombuffer(raw, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is not None:
                self.process(frame)
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__

    def process(self, frame: np.ndarray, frame_seq: int = 0, session_id: str = ''):
        # Never opens VideoCapture: the primary Windows webcam is untouched.
        now = time.monotonic()
        now_wall = time.time()
        try:
            # 1. Forward frame to the asynchronous Dual Camera proctor queue
            self.dual_proctor.submit_ios_frame(frame, timestamp=now_wall)
            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                IPadCameraStreamMonitor.get_instance().record_valid_frame(
                    frame,
                    session_id=session_id or None,
                    frame_seq=frame_seq or None,
                    mark_processed=False,
                )
            except Exception:
                pass

            # 2. Extract synchronous telemetry from the dedicated iOS proctor
            ios_guard = self.dual_proctor.ios_proctor
            face_status = 'VISIBLE' if ios_guard.face_presence == 'VERIFIED' else 'UNVERIFIED'

            with self.lock:
                self.error = ''
                preview_jpeg = getattr(self.dual_proctor, '_ios_preview_jpeg', None) or getattr(ios_guard, '_preview', None)
                if not preview_jpeg:
                    small = cv2.resize(frame, (320, 240)) if (frame.shape[1] != 320 or frame.shape[0] != 240) else frame
                    ok, encoded = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    if ok:
                        preview_jpeg = base64.b64encode(encoded.tobytes()).decode('ascii')

                rx_fps, proc_fps = self.dual_proctor._recalculate_ios_fps(now_wall)
                if proc_fps == 0.0 and getattr(ios_guard, 'processing_fps', 0.0) > 0.0:
                    proc_fps = round(float(ios_guard.processing_fps), 1)

                self.latest = {
                    'jpeg': preview_jpeg,
                    'observed_at': now_wall,
                    'monotonic': now,
                    'source': 'iPhone camera',
                    'strictguard': 'ACTIVE' if ios_guard.is_running and not self.error else 'UNVERIFIED',
                    'face': face_status,
                    'face_presence': ios_guard.face_presence,
                    'gaze_status': getattr(ios_guard, 'gaze_status', 'NORMAL'),
                    'head_pose': {
                        'yaw': round(getattr(ios_guard, 'current_yaw', 0.0), 1),
                        'pitch': round(getattr(ios_guard, 'current_pitch', 0.0), 1),
                    },
                    'lighting': ios_guard.lighting,
                    'framing': ios_guard.face_framing,
                    'calibrated': ios_guard.is_calibrated,
                    'received_fps': rx_fps,
                    'processing_fps': proc_fps,
                    'latency_ms': getattr(ios_guard, 'inference_latency_ms', 0.0),
                    'error': self.error,
                }
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__
                self.latest['error'] = self.error

    def preview(self) -> Dict[str, Any]:
        with self.lock:
            result = dict(self.latest)
        rx_fps, proc_fps = self.dual_proctor._recalculate_ios_fps()
        result['received_fps'] = rx_fps
        result['processing_fps'] = proc_fps
        age = time.monotonic() - result.pop('monotonic', 0)
        if age > 4.0:
            result.pop('jpeg', None)
            result.update(strictguard='UNVERIFIED', face='UNVERIFIED', gaze_status='UNAVAILABLE', received_fps=0.0, processing_fps=0.0)
        return result
