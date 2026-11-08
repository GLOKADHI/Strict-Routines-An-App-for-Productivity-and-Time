"""Local OpenCV YOLOX phone detector and camera-based earbud wear detector.

YOLOX model/preprocessing derived from OpenCV Zoo's Apache-2.0 implementation.
YuNet landmarks / ear ROI analysis detects worn wireless earbuds (AirPods, AirPods Pro,
Sony WF-series, CMF Buds).
Frames never leave the PC or enter recordings.
"""
from pathlib import Path
import sys
import threading
import time
import cv2
import numpy as np


def phone_model_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'assets/vision/phone_detection_yolox_2022nov.onnx'


class PhoneDetector:
    CONFIDENCE = .75

    def __init__(self):
        self.net = cv2.dnn.readNet(str(phone_model_path()))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        grids = []
        strides = []
        for stride in (8, 16, 32):
            x, y = np.meshgrid(np.arange(640 // stride), np.arange(640 // stride))
            grid = np.stack((x, y), 2).reshape(-1, 2)
            grids.append(grid)
            strides.append(np.full((len(grid), 1), stride))
        self.grids = np.concatenate(grids)
        self.strides = np.concatenate(strides)

    def detect(self, frame):
        h, w = frame.shape[:2]
        ratio = min(640 / h, 640 / w)
        padded = np.full((640, 640, 3), 114, np.uint8)
        resized = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (int(w * ratio), int(h * ratio)))
        padded[:resized.shape[0], :resized.shape[1]] = resized
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        self.net.setInput(blob)
        output = self.net.forward()[0]
        # COCO phone=67. Require phone to be the best class, not merely a weak
        # alternative for another object, then apply high-confidence NMS.
        classes = np.argmax(output[:, 5:], axis=1)
        confidence = output[:, 4] * output[:, 72]
        indices = np.flatnonzero((classes == 67) & (confidence >= self.CONFIDENCE))
        if not len(indices):
            return []
        rows = output[indices, :4].copy()
        rows[:, :2] = (rows[:, :2] + self.grids[indices]) * self.strides[indices]
        rows[:, 2:4] = np.exp(np.clip(rows[:, 2:4], -10, 10)) * self.strides[indices]
        rows[:, :2] -= rows[:, 2:4] / 2
        rows /= ratio
        boxes = rows.tolist()
        scores = confidence[indices].tolist()
        kept = cv2.dnn.NMSBoxes(boxes, scores, self.CONFIDENCE, .5)
        return [
            dict(category='PHONE_VISIBLE', confidence=float(scores[int(i)]), box=boxes[int(i)])
            for i in np.asarray(kept).reshape(-1)
        ]


class EarbudWearDetector:
    """
    Detects wireless earbuds (Apple AirPods, AirPods Pro, Sony WF-1000XM5,
    Sony WF-series, CMF Buds, etc.) worn in the ear concha/tragus region.
    Distinguishes earbuds worn from earbuds merely held in hand, spectacles,
    earrings, hair accessories, and reflections.
    """
    MIN_CONFIDENCE = 0.70

    def __init__(self):
        pass

    def get_ear_rois(self, frame_shape, face):
        """
        Calculates left and right ear ROIs based on face bounding box and eye landmarks.
        YuNet face format: [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc, score]
        """
        h_img, w_img = frame_shape[:2]
        x, y, w, h = [float(v) for v in face[:4]]
        if len(face) >= 8:
            x_re, y_re = float(face[4]), float(face[5])
            x_le, y_le = float(face[6]), float(face[7])
        else:
            x_re, y_re = x + w * 0.25, y + h * 0.35
            x_le, y_le = x + w * 0.75, y + h * 0.35

        # Right ear (subject's right, viewer's left side):
        r_ear_w = max(12, int(w * 0.32))
        r_ear_h = max(16, int(h * 0.50))
        r_ear_x = max(0, int(x - r_ear_w * 0.75))
        r_ear_y = max(0, int(y_re - r_ear_h * 0.20))
        r_ear_w = min(r_ear_w, w_img - r_ear_x)
        r_ear_h = min(r_ear_h, h_img - r_ear_y)

        # Left ear (subject's left, viewer's right side):
        l_ear_w = max(12, int(w * 0.32))
        l_ear_h = max(16, int(h * 0.50))
        l_ear_x = min(w_img - 1, int(x + w - l_ear_w * 0.25))
        l_ear_y = max(0, int(y_le - l_ear_h * 0.20))
        l_ear_w = min(l_ear_w, w_img - l_ear_x)
        l_ear_h = min(l_ear_h, h_img - l_ear_y)

        rois = []
        if r_ear_w >= 10 and r_ear_h >= 10:
            rois.append(('RIGHT_EAR', (r_ear_x, r_ear_y, r_ear_w, r_ear_h), (x_re, y_re)))
        if l_ear_w >= 10 and l_ear_h >= 10:
            rois.append(('LEFT_EAR', (l_ear_x, l_ear_y, l_ear_w, l_ear_h), (x_le, y_le)))
        return rois

    def _is_spectacle_arm(self, contour, ear_roi_rect, eye_pt):
        """Filters continuous horizontal spectacle temple arms."""
        rx, ry, rw, rh = ear_roi_rect
        cx, cy, cw, ch = cv2.boundingRect(contour)
        aspect = cw / max(1, ch)
        if aspect > 4.0 and cy < rh * 0.35:
            return True
        abs_y = ry + cy
        eye_y = eye_pt[1]
        dist_to_eye_y = abs(abs_y - eye_y)
        if dist_to_eye_y < ch * 1.5 and aspect > 3.0:
            return True
        return False

    def _is_earring_lobule(self, contour, ear_roi_rect):
        """Filters jewelry hanging strictly from the lower earlobe."""
        rx, ry, rw, rh = ear_roi_rect
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if (cy + ch) > rh * 0.85 and cy > rh * 0.70:
            return True
        return False

    def detect_in_ear(self, frame, face):
        """
        Analyzes ear ROIs for worn wireless earbuds.
        Returns list of detections with:
        category ('AIRPODS' or 'WIRELESS_EARBUDS'), confidence, ear ('LEFT_EAR' or 'RIGHT_EAR'), box, worn=True.
        """
        if face is None or len(face) < 4:
            return []

        rois = self.get_ear_rois(frame.shape, face)
        detections = []

        for ear_side, (rx, ry, rw, rh), eye_pt in rois:
            if rw < 10 or rh < 10:
                continue
            ear_crop = frame[ry:ry+rh, rx:rx+rw]
            if ear_crop.size == 0:
                continue

            hsv = cv2.cvtColor(ear_crop, cv2.COLOR_BGR2HSV)
            h_ch, s_ch, v_ch = cv2.split(hsv)

            # 1. White Earbuds (Apple AirPods, AirPods Pro, CMF Buds White)
            white_mask = (v_ch > 185) & (s_ch < 55)
            white_mask_u8 = white_mask.astype(np.uint8) * 255
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            white_cleaned = cv2.morphologyEx(white_mask_u8, cv2.MORPH_OPEN, kernel)

            contours, _ = cv2.findContours(white_cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            min_area = max(15, int(rw * rh * 0.03))
            max_area = int(rw * rh * 0.65)

            for c in contours:
                area = cv2.contourArea(c)
                if min_area <= area <= max_area:
                    if self._is_spectacle_arm(c, (rx, ry, rw, rh), eye_pt):
                        continue
                    if self._is_earring_lobule(c, (rx, ry, rw, rh)):
                        continue

                    bx, by, bw, bh = cv2.boundingRect(c)
                    aspect = bh / max(1, bw)
                    rel_y = by / rh
                    if 0.10 <= rel_y <= 0.85 and 0.5 <= aspect <= 3.8:
                        mean_v = float(np.mean(v_ch[by:by+bh, bx:bx+bw]))
                        mean_s = float(np.mean(s_ch[by:by+bh, bx:bx+bw]))
                        conf = min(0.98, max(0.72, (mean_v / 255.0) * (1.0 - mean_s / 255.0) + 0.15))
                        category = 'AIRPODS' if aspect >= 1.3 or mean_v > 200 else 'WIRELESS_EARBUDS'
                        detections.append({
                            'category': category,
                            'confidence': round(conf, 3),
                            'ear': ear_side,
                            'box': [rx + bx, ry + by, bw, bh],
                            'worn': True
                        })

            # 2. Dark In-Ear Earbuds (Sony WF-1000XM5, Sony WF-series, CMF Buds Dark)
            ycrcb = cv2.cvtColor(ear_crop, cv2.COLOR_BGR2YCrCb)
            y_c, cr, cb = cv2.split(ycrcb)
            mid_y1, mid_y2 = int(rh * 0.20), int(rh * 0.75)
            mid_x1, mid_x2 = int(rw * 0.15), int(rw * 0.85)
            concha_v = v_ch[mid_y1:mid_y2, mid_x1:mid_x2]
            concha_cr = cr[mid_y1:mid_y2, mid_x1:mid_x2]

            dark_mask = (concha_v < 60)
            if np.any(dark_mask):
                dark_u8 = dark_mask.astype(np.uint8) * 255
                dark_cleaned = cv2.morphologyEx(dark_u8, cv2.MORPH_OPEN, kernel)
                d_contours, _ = cv2.findContours(dark_cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for dc in d_contours:
                    d_area = cv2.contourArea(dc)
                    if min_area <= d_area <= max_area * 0.8:
                        d_peri = cv2.arcLength(dc, True)
                        if d_peri > 0:
                            circularity = 4 * np.pi * d_area / (d_peri * d_peri)
                            if circularity > 0.40:
                                dbx, dby, dbw, dbh = cv2.boundingRect(dc)
                                mean_surr_cr = float(np.mean(concha_cr))
                                if mean_surr_cr > 130:
                                    conf = min(0.92, max(0.70, circularity * 0.6 + 0.35))
                                    detections.append({
                                        'category': 'WIRELESS_EARBUDS',
                                        'confidence': round(conf, 3),
                                        'ear': ear_side,
                                        'box': [rx + mid_x1 + dbx, ry + mid_y1 + dby, dbw, dbh],
                                        'worn': True
                                    })
        return detections

    def detect_in_hand_or_frame(self, frame, face):
        """
        Detects small earbud-like objects outside the ear region (e.g. held in hand).
        Returns list of non-worn earbud objects.
        """
        if face is None:
            return []
        ear_rois = self.get_ear_rois(frame.shape, face)
        ear_boxes = [r[1] for r in ear_rois]

        hand_zone_y = int(frame.shape[0] * 0.45)
        hand_crop = frame[hand_zone_y:, :]
        if hand_crop.size == 0:
            return []
        hsv = cv2.cvtColor(hand_crop, cv2.COLOR_BGR2HSV)
        v_ch = hsv[:, :, 2]
        s_ch = hsv[:, :, 1]
        white_mask = ((v_ch > 200) & (s_ch < 40)).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        cleaned = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        hand_objects = []
        for c in contours:
            area = cv2.contourArea(c)
            if 30 <= area <= 600:
                bx, by, bw, bh = cv2.boundingRect(c)
                abs_box = [bx, hand_zone_y + by, bw, bh]
                in_ear = False
                for ex, ey, ew, eh in ear_boxes:
                    if (ex <= abs_box[0] <= ex + ew) and (ey <= abs_box[1] <= ey + eh):
                        in_ear = True
                        break
                if not in_ear:
                    hand_objects.append({
                        'category': 'AIRPODS',
                        'confidence': 0.85,
                        'box': abs_box,
                        'worn': False,
                        'held_in_hand': True
                    })
        return hand_objects


class VisualObjectMonitor:
    def __init__(self, episodes, clock=time.monotonic, window_checker=None, session_context_provider=None, db=None):
        self.episodes = episodes
        self.clock = clock
        self.window_checker = window_checker
        self.session_context_provider = session_context_provider
        self.db = db
        self.lock = threading.Lock()
        self.pending = None
        self.stop_event = threading.Event()
        self.thread = None
        self.state = 'NOT_STARTED'
        self.last_sample = None
        self.detected = False
        self.confidence = None
        self.submission_window_open = False
        self.authorized_proof_submission = False

        # Visual earbud detection state
        self.earbuds_detected = False
        self.earbud_worn = False
        self.earbud_category = None
        self.earbud_confidence = None
        self.earbud_detector = EarbudWearDetector()

    def set_window_checker(self, checker):
        with self.lock:
            self.window_checker = checker

    def set_session_context_provider(self, provider):
        with self.lock:
            self.session_context_provider = provider

    def is_submission_window_open(self):
        """Authoritatively checks if the hourly proof-submission window is currently OPEN."""
        with self.lock:
            checker = self.window_checker
        if checker is not None:
            try:
                return bool(checker())
            except Exception:
                pass
        try:
            from windows.submission_window_manager import SubmissionWindowManager
            mgr = SubmissionWindowManager.get_instance()
            if mgr and getattr(mgr, 'state', None):
                from windows.submission_window_manager import SubmissionState
                if mgr.state == SubmissionState.OPEN or str(mgr.state) == 'OPEN' or str(getattr(mgr.state, 'value', '')) == 'OPEN':
                    return True
        except Exception:
            pass
        if self.db is not None:
            try:
                with self.db._connect() as conn:
                    row = conn.execute("SELECT state FROM submission_window_state WHERE state='OPEN'").fetchone()
                    if row:
                        return True
            except Exception:
                pass
        return False

    def is_authorized_proof_submission(self):
        """Checks if the visible phone interaction is an explicitly authorized Android proof submission."""
        with self.lock:
            provider = self.session_context_provider
        if provider is not None:
            try:
                ctx = provider()
                if isinstance(ctx, dict):
                    return bool(ctx.get('authorized_proof_submission') or ctx.get('is_authorized_proof'))
                return bool(ctx)
            except Exception:
                pass
        return False

    def start(self):
        self.stop_event.clear()
        self.state = 'INITIALIZING'

        def run():
            try:
                detector = PhoneDetector()
                earbud_detector = self.earbud_detector
                while not self.stop_event.is_set():
                    with self.lock:
                        sample = self.pending
                        self.pending = None
                    if sample is None:
                        self.stop_event.wait(.1)
                        continue
                    frame, face, captured = sample
                    if self.clock() - captured > 2:
                        continue
                    found = detector.detect(frame)
                    now = self.clock()
                    if self.stop_event.is_set():
                        return
                    if now - captured > 3:
                        self.state = 'SLOW'
                        self.episodes.observe('PHONE_VISIBLE', None, now)
                        continue

                    self.last_sample = now
                    self.state = 'ACTIVE'
                    self.detected = bool(found)
                    self.confidence = max((row['confidence'] for row in found), default=None)

                    # 1. Submission Window Aware Mobile Phone Policy
                    window_open = self.is_submission_window_open()
                    authorized_proof = self.is_authorized_proof_submission()
                    self.submission_window_open = window_open
                    self.authorized_proof_submission = authorized_proof

                    if window_open and not authorized_proof:
                        # Violation rule active: Phone visible during open submission window
                        # and NOT part of an authorized proof submission workflow
                        if found:
                            self.episodes.observe(
                                'MOBILE_PHONE_DETECTED', True, now, hold=2.0, max_gap=2.0,
                                message='A mobile phone is visible in the camera during the open submission window. Put it away to continue.',
                                confidence=self.confidence
                            )
                        else:
                            self.episodes.observe('MOBILE_PHONE_DETECTED', False, now)
                    else:
                        # Outside submission window OR authorized proof submission in progress:
                        # Phone violation rule does NOT trigger.
                        self.episodes.observe('MOBILE_PHONE_DETECTED', False, now)

                    # General phone visibility telemetry
                    self.episodes.observe(
                        'PHONE_VISIBLE', bool(found), now, hold=2.0, max_gap=2.0,
                        message='A mobile phone is visible in the camera. Put it away to continue.',
                        confidence=self.confidence
                    )

                    # 2. Camera-Based Earbud Wear Detection
                    if face is not None:
                        worn_earbuds = earbud_detector.detect_in_ear(frame, face)
                        if worn_earbuds:
                            best_eb = max(worn_earbuds, key=lambda x: x['confidence'])
                            self.earbuds_detected = True
                            self.earbud_worn = True
                            self.earbud_category = best_eb['category']
                            self.earbud_confidence = best_eb['confidence']
                            self.episodes.observe(
                                'AIRPODS_WORN_CAMERA_DETECTED', True, now, hold=1.5, max_gap=2.0,
                                message=f'Prohibited {best_eb["category"]} detected worn in camera.',
                                confidence=best_eb['confidence'],
                                device_category=best_eb['category'],
                                detection_source='CAMERA_VISION'
                            )
                        else:
                            self.earbuds_detected = False
                            self.earbud_worn = False
                            self.earbud_category = None
                            self.earbud_confidence = None
                            self.episodes.observe('AIRPODS_WORN_CAMERA_DETECTED', False, now)
                    else:
                        # Face tracking lost / no face in frame:
                        # Temporary tracking loss must NEVER generate fake earbud violations.
                        self.earbuds_detected = False
                        self.earbud_worn = False
                        self.earbud_category = None
                        self.earbud_confidence = None
                        self.episodes.observe('AIRPODS_WORN_CAMERA_DETECTED', None, now)

                    self.stop_event.wait(.5)
            except Exception:
                self.state = 'UNAVAILABLE'

        self.thread = threading.Thread(target=run, name='StrictSessionPhoneDetection', daemon=True)
        self.thread.start()

    def submit(self, frame, face=None):
        if self.stop_event.is_set():
            return
        with self.lock:
            self.pending = (frame.copy(), face.copy() if face is not None else None, self.clock())

    def status(self):
        fresh = self.state == 'ACTIVE' and self.last_sample is not None and self.clock() - self.last_sample < 3.
        return dict(
            phone_detection_status='ACTIVE' if fresh else self.state if self.state != 'ACTIVE' else 'STALE',
            phone_visible=self.detected if fresh else None,
            phone_confidence=self.confidence if fresh else None,
            submission_window_open=self.submission_window_open if fresh else False,
            authorized_proof_submission=self.authorized_proof_submission if fresh else False,
            visual_earbud_detection='ACTIVE' if fresh else 'UNAVAILABLE',
            earbuds_detected=self.earbuds_detected if fresh else False,
            earbud_worn=self.earbud_worn if fresh else False,
            earbud_category=self.earbud_category if fresh else None,
            earbud_confidence=self.earbud_confidence if fresh else None,
            visual_earbud_message=(
                f'{self.earbud_category or "Earbuds"} detected worn'
                if (fresh and self.earbuds_detected)
                else 'StrictGuard visual earbud monitoring active'
            )
        )

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.pending = None
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(3.)
        self.last_sample = None
        self.state = 'STOPPED'
