"""
STRICTRECORDS — Hardware Proctoring & Device Enforcement Coordinator
Integrates:
- StrictGuardProctor: Continuous AI webcam proctoring (YuNet landmarks, leftward attention detection)
- StrictHeadphoneDetector: Prohibited Bluetooth earbuds & wired headphones detection
- StrictAudioController: Windows PC speaker muting (no microphone required)
- Battery & system telemetry
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from windows.strict_audio_controller import get_strict_audio_controller
from windows.strict_guard_proctor import get_strict_guard_proctor
from windows.strict_headphone_detector import get_strict_headphone_detector

logger = logging.getLogger("StrictStudyHardware")


class StrictStudyHardware:
    """
    Coordinates webcam proctoring, headphone detection, and audio muting
    for Strict Session. Guarantees completely local execution.
    """

    def __init__(self, db=None):
        self.db = db
        self.proctor = get_strict_guard_proctor()
        self.headphone_detector = get_strict_headphone_detector()
        self.audio_controller = get_strict_audio_controller()

        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.camera_id: Optional[str] = None
        self._data_lock=threading.RLock()
        self.data: Dict[str, Any] = {
            "camera": "NOT_STARTED",
            "face_present": None,
            "face_tracking": "UNVERIFIED",
            "attention_monitoring": "STOPPED",
            "leftward_violations": 0,
            "max_leftward_violations": 15,
            "proctor_calibrated": False,
            "proctor_message": "StrictGuard Initializing",
            "bluetooth_connected": False,
            "bluetooth_headphone_connected": False,
            "wired_headphone_connected": False,
            "prohibited_audio_detected": False,
            "prohibited_audio_name": None,
            "jack_detection": self.headphone_detector.jack_detection_status,
            "speaker_output": "NORMAL",
            "speaker_muted": False,
            "speaker_control_ready": True,
            "microphone": "NOT_REQUIRED",
            "microphone_required": False,
            "headphone_violations": 0,
            "is_paused_for_headphone": False,
            "battery_percent": None,
            "charging": None,
            "monitoring_ok": False,
        }

        self.last_frame_mono: float = 0.0
        self.bluetooth_revision: int = 0
        self._audio_peek_at=-100.
        from windows.strict_screen_monitor import StrictScreenMonitor
        self.screen_monitor=StrictScreenMonitor()
        self._session_integrity_active=False

    def enable_session_integrity(self,enabled=True,session_id=None):
        self._session_integrity_active=bool(enabled)
        self.proctor.enable_session_integrity(enabled,session_id=session_id)
        if hasattr(self.headphone_detector,'set_integrity'):
            self.headphone_detector.set_integrity(self.proctor.integrity)
        if enabled:self.screen_monitor.start()
        else:self.screen_monitor.stop()

    def start(self, consent: bool = False, camera_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Starts StrictGuard proctoring, headphone monitoring, and PC speaker muting.
        """
        if not consent:
            return {"success": False, "error": "Camera monitoring needs your explicit consent."}

        from windows.strict_media_devices import resolve_camera_id
        try:
            resolve_camera_id(camera_id)
        except Exception as error:
            return {"success": False, "error": str(error)}

        # 1. Start PC Speaker Muting (Speaker Muted; Mic not required)
        def _on_speaker_unmuted(ev):
            try:
                if hasattr(self, 'proctor') and hasattr(self.proctor, 'integrity'):
                    self.proctor.integrity.record('SPEAKER_UNMUTED', 'PC speaker was unmuted during active Strict Session. Mute automatically reapplied.', source='AUDIO')
            except Exception:
                pass
        self.audio_controller.set_on_unmute_callback(_on_speaker_unmuted)
        mute=self.audio_controller.save_and_mute()
        if not mute.get("speaker_muted"):
            self.audio_controller.restore()
            return {"success":False,"error":"Speaker mute could not be verified. Check the Windows output device and retry."}

        # 2. Start Headphone & Bluetooth Audio Monitoring
        self.headphone_detector.reset_session()
        if hasattr(self.headphone_detector,'set_integrity'):
            self.headphone_detector.set_integrity(self.proctor.integrity)
        self.headphone_detector.start_monitoring()

        # 3. Start StrictGuard AI Proctoring
        proctor_res = self.proctor.start(consent=consent, camera_id=camera_id)
        if not proctor_res.get("success"):
            self.stop()
            return proctor_res

        self.camera_id = camera_id
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_telemetry_loop, name="StrictStudyHardwareTelemetry", daemon=True)
        self.thread.start()

        logger.info("StrictStudyHardware: All proctoring, headphone and audio services active.")
        return {"success": True}

    def _run_telemetry_loop(self) -> None:
        """Polls battery and updates consolidated telemetry state."""
        last_battery_check = -100.0

        while not self.stop_event.is_set():
            try:
                now_mono = time.monotonic()

                # Get latest telemetry from proctor
                p_tel = self.proctor.get_telemetry()
                frame_sample_mono=time.monotonic()
                if hasattr(self.headphone_detector, 'update_camera_earbuds'):
                    self.headphone_detector.update_camera_earbuds(
                        worn=bool(p_tel.get('earbuds_detected', False)),
                        category=p_tel.get('earbud_category', 'AIRPODS') or 'AIRPODS',
                        confidence=float(p_tel.get('earbud_confidence', 0.0) or 0.0)
                    )
                # Get latest telemetry from headphone detector
                h_tel = self.headphone_detector.get_status()
                # Get audio mute status
                a_tel = self.audio_controller.get_status()

                # Update battery telemetry every 5 seconds
                if now_mono - last_battery_check >= 5.0:
                    last_battery_check = now_mono
                    battery = psutil.sensors_battery()
                    self.data.update(
                        battery_percent=round(battery.percent) if battery else None,
                        charging=battery.power_plugged if battery else None,
                    )
                    self.bluetooth_revision += 1

                camera_active = (p_tel["camera"] == "ACTIVE")
                age=p_tel.get("last_frame_age_seconds")
                self.last_frame_mono=frame_sample_mono-age if isinstance(age,(int,float)) and 0<=age<5 else 0

                prohibited_audio = h_tel.get("prohibited_connected",False)
                monitoring_ok = bool(camera_active and p_tel.get('face_presence')=='VERIFIED'
                    and p_tel.get('calibrated') and p_tel.get('lighting')=='CLEAR' and p_tel.get('face_framing')=='VISIBLE'
                    and h_tel.get('detection_known') and not h_tel.get('is_paused')
                    and a_tel.get('speaker_muted') and not prohibited_audio and not p_tel['session_failed']
                    and now_mono-self.last_frame_mono<2)

                self.data.update({
                    "camera": p_tel["camera"],
                    "face_present": (p_tel["face_presence"] == "VERIFIED"),
                    "face_tracking": p_tel["face_presence"],
                    "attention_monitoring": p_tel["attention_monitoring"],
                    "leftward_violations": p_tel["violations_count"],
                    "max_leftward_violations": p_tel["max_violations"],
                    "proctor_calibrated": p_tel["calibrated"],
                    "proctor_message": p_tel["status_message"],
                    "bluetooth_connected": h_tel.get("bluetooth_headphone_connected",False),
                    "bluetooth_headphone_connected": h_tel.get("bluetooth_headphone_connected",False),
                    "wired_headphone_connected": h_tel.get("wired_headphone_connected",False),
                    "prohibited_audio_detected": prohibited_audio,
                    "prohibited_audio_name": h_tel.get("detected_device",None),
                    "prohibited_audio_description": h_tel.get("detected_description",None),
                    "jack_detection": h_tel.get("jack_detection",'NOT_VERIFIED'),
                    "speaker_output": a_tel["speaker_output"],
                    "speaker_muted": a_tel["speaker_muted"],
                    "speaker_state": a_tel.get("speaker_state", "SPEAKER MUTED" if a_tel.get("speaker_muted") else "SPEAKER UNMUTED"),
                    "zero_volume": a_tel.get("zero_volume", False),
                    "master_mute": a_tel.get("master_mute", False),
                    "default_audio_device": a_tel.get("default_device_name"),
                    "audio_unmute_violations": a_tel.get("unmute_violations_count", 0),
                    "microphone": "NOT_REQUIRED",
                    "microphone_required": False,
                    "headphone_violations": h_tel.get("violations_count",0),
                    "is_paused_for_headphone": h_tel.get("is_paused",False),
                    "monitoring_ok": monitoring_ok,
                    "lighting":p_tel.get("lighting","UNVERIFIED"),
                    "face_framing":p_tel.get("face_framing","UNVERIFIED"),
                    "audio_detection_known":h_tel.get("detection_known",False),
                    "wired_state_known":h_tel.get("wired_state_known",False),
                    "leftward_seconds":p_tel.get("leftward_seconds",0),
                    "last_prohibited_device":h_tel.get("detected_device"),
                    "session_failed": p_tel["session_failed"],
                    "failure_reason": p_tel["failure_reason"],
                    "integrity_events":p_tel.get('integrity_events',[]),
                    "phone_detection_status":p_tel.get('phone_detection_status','NOT_STARTED'),
                    "phone_visible":p_tel.get('phone_visible'),
                    "phone_confidence":p_tel.get('phone_confidence'),
                    "submission_window_open":p_tel.get('submission_window_open', False),
                    "authorized_proof_submission":p_tel.get('authorized_proof_submission', False),
                    "visual_earbud_detection":p_tel.get('visual_earbud_detection','UNAVAILABLE'),
                    "visual_earbud_message":p_tel.get('visual_earbud_message'),
                    "earbuds_detected":p_tel.get('earbuds_detected', False),
                    "earbud_worn":p_tel.get('earbud_worn', False),
                    "earbud_category":p_tel.get('earbud_category'),
                    "earbud_confidence":p_tel.get('earbud_confidence'),
                })

            except Exception as e:
                self.data["monitoring_ok"]=False
                logger.debug(f"Telemetry loop note: {e}")

            self.stop_event.wait(0.5)

    def view(self) -> Dict[str, Any]:
        """Returns the consolidated hardware and proctoring view."""
        result = dict(self.data)
        try:
            from windows.strict_dual_camera_proctor import StrictDualCameraProctor
            result["dual_camera"] = StrictDualCameraProctor.get_instance().get_dual_camera_telemetry()
        except Exception:
            pass
        now_mono = time.monotonic()
        if (not self.thread or not self.thread.is_alive()) and now_mono-self._audio_peek_at>3:
            self._audio_peek_at=now_mono
            try:
                audio=self.headphone_detector.check_audio_devices()
                self.data.update(audio_detection_known=audio.get('detection_known',False),
                    bluetooth_connected=audio.get('bluetooth_headphone_connected',False),bluetooth_headphone_connected=audio.get('bluetooth_headphone_connected',False),
                    wired_headphone_connected=audio.get('wired_headphone_connected',False),prohibited_audio_detected=audio.get('prohibited_connected',False),
                    prohibited_audio_name=audio.get('detected_device'),jack_detection=audio.get('jack_detection','NOT_VERIFIED'),wired_state_known=audio.get('wired_state_known',False))
                result=dict(self.data)
            except Exception:result['audio_detection_known']=False
        result["monitoring_ok"] = bool(result.get('monitoring_ok') and now_mono-self.last_frame_mono<2)
        result.update(self.screen_monitor.status())
        try:
            from windows.strict_sensitive_page_protection import SensitivePageProtectionManager
            result["sensitive_page_protection"] = SensitivePageProtectionManager.get_instance().get_status()
        except Exception:
            pass
        if self._session_integrity_active:
            screen_ok = bool(result.get('screen_capture_active') or result.get('privacy_shield_active'))
            result['monitoring_ok']=bool(result['monitoring_ok'] and screen_ok and result.get('phone_visible') is not True)
        result['screen_proctoring_active']=False
        result['last_frame_age_seconds']=max(0,now_mono-self.last_frame_mono) if self.last_frame_mono else None
        result["bluetooth_revision"] = self.bluetooth_revision
        return result

    def preview(self):
        return self.proctor.get_preview()

    def stop(self) -> None:
        """Stops proctor, headphone detector, audio muting, and releases resources."""
        self.stop_event.set()
        self.enable_session_integrity(False)

        # 1. Stop StrictGuard Proctor
        try:
            self.proctor.stop()
        except Exception as e:
            logger.debug(f"Note stopping proctor: {e}")

        # 2. Stop Headphone Detector
        try:
            self.headphone_detector.stop_monitoring()
        except Exception as e:
            logger.debug(f"Note stopping headphone detector: {e}")

        # 3. Restore Audio Output
        try:
            self.audio_controller.restore()
        except Exception as e:
            logger.debug(f"Note restoring audio: {e}")

        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2.0)

        try:
            from windows.strict_dual_camera_proctor import StrictDualCameraProctor
            # Keep iOS proctoring worker independent of Windows setup hardware stops
            StrictDualCameraProctor.get_instance().ensure_ios_worker_alive()
        except Exception:
            pass
        self.last_frame_mono=0
        self.data.update(camera='STOPPED',monitoring_ok=False,face_present=None,face_tracking='UNVERIFIED',
            attention_monitoring='STOPPED',proctor_calibrated=False,audio_detection_known=False)
        self.data.update(self.audio_controller.get_status())
        logger.info("StrictStudyHardware: Stopped and cleaned up.")

    def promote_setup(self):
        """Reuse the checked camera; setup observations never count as strikes."""
        with self.proctor._lock:
            self.proctor.violations_count=0;self.proctor.session_failed=False;self.proctor.failure_reason=''
            self.proctor._leftward_start_time=None;self.proctor._in_violation_event=False
            self.proctor._neutral_return_start_time=None
        with self.headphone_detector._lock:
            self.headphone_detector.violations_count=0;self.headphone_detector.current_violation=None
            self.headphone_detector._snapshot.update(violations_count=0)
        self.data.update(leftward_violations=0,headphone_violations=0,session_failed=False,failure_reason='')
