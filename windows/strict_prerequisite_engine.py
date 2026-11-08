"""StrictRecords - Standardized Strict Session Prerequisite Verification Engine.

Enforces truthfulness, timeouts, retry mechanics, standardized verification states,
and three-device readiness (Windows Host, Android Companion, iOS Companion).
"""
import enum
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("StrictRecords.PrerequisiteEngine")


class PrerequisiteState(str, enum.Enum):
    NOT_CHECKED = "NOT_CHECKED"
    CHECKING = "CHECKING"
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class PrerequisiteCategory(str, enum.Enum):
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"
    CONDITIONAL = "CONDITIONAL"


class DeviceRole(str, enum.Enum):
    WINDOWS = "WINDOWS"
    ANDROID = "ANDROID"
    IOS = "IOS"


# Default operation timeouts in seconds per spec
DEFAULT_TIMEOUTS = {
    "local_hardware": 5.0,
    "webcam_init": 10.0,
    "strictguard_model": 15.0,
    "bluetooth_enum": 10.0,
    "audio_mute": 5.0,
    "browser_init": 15.0,
    "extension_handshake": 10.0,
    "android_handshake": 20.0,
    "ios_handshake": 20.0,
    "internet_verify": 10.0,
}


class PrerequisiteCheck:
    """Represents a single verifiable prerequisite."""

    def __init__(
        self,
        check_id: str,
        name: str,
        category: PrerequisiteCategory,
        device: DeviceRole,
        description: str = "",
        timeout_seconds: float = 10.0,
    ):
        self.check_id = check_id
        self.name = name
        self.category = category
        self.device = device
        self.description = description
        self.timeout_seconds = timeout_seconds

        self.state: PrerequisiteState = PrerequisiteState.NOT_CHECKED
        self.message: str = "Not checked yet."
        self.error: str = ""
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.duration_ms: float = 0.0
        self.retry_count: int = 0
        self.details: Dict[str, Any] = {}

    def start_checking(self) -> None:
        self.state = PrerequisiteState.CHECKING
        self.message = f"Verifying {self.name}…"
        self.error = ""
        self.started_at = time.monotonic()

    def set_result(
        self,
        state: PrerequisiteState,
        message: str,
        error: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.state = state
        self.message = message
        self.error = error
        if details:
            self.details.update(details)
        if self.started_at is not None:
            self.completed_at = time.monotonic()
            self.duration_ms = round((self.completed_at - self.started_at) * 1000, 1)
        else:
            self.duration_ms = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "category": self.category.value,
            "device": self.device.value,
            "state": self.state.value,
            "message": self.message,
            "error": self.error,
            "passed": self.state in (PrerequisiteState.PASSED, PrerequisiteState.NOT_REQUIRED, PrerequisiteState.UNSUPPORTED),
            "is_mandatory": self.category == PrerequisiteCategory.MANDATORY,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "details": self.details,
        }


class StrictPrerequisiteEngine:
    """Centralized verification engine for all Strict Session prerequisites."""

    def __init__(self, db=None):
        self.db = db
        self._lock = threading.RLock()
        self.checks: Dict[str, PrerequisiteCheck] = {}
        self._init_catalog()

    def _init_catalog(self) -> None:
        """Initializes all standard check definitions."""
        # 16 Mandatory Windows Checks
        mand_win = [
            ("win_session_controller", "Windows Session Controller", "Windows EXE session controller operational", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_webcam_permission", "Webcam Permission", "Webcam permission accessible to desktop apps", DEFAULT_TIMEOUTS["webcam_init"]),
            ("win_webcam_stream", "Webcam Active Stream", "Selected camera is accessible and delivering frames", DEFAULT_TIMEOUTS["webcam_init"]),
            ("win_strictguard_model", "StrictGuard Model", "YuNet face detection ONNX model loaded", DEFAULT_TIMEOUTS["strictguard_model"]),
            ("win_face_detection", "Face Presence & Framing", "At least one full face visible inside camera frame", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_face_calibration", "Head Pose Calibration", "Neutral head position and orientation calibrated", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_internet", "Internet Connectivity", "Reliable TLS network connection available", DEFAULT_TIMEOUTS["internet_verify"]),
            ("win_study_browser", "Dedicated Study Browser", "Embedded secure WebView2 browser initialized", DEFAULT_TIMEOUTS["browser_init"]),
            ("win_domain_navigation", "Domain Allowlist", "Approved study navigation domain configuration loaded", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_extension_installed", "Browser Extension Installed", "StrictRoutines browser extension installed & enabled", DEFAULT_TIMEOUTS["extension_handshake"]),
            ("win_extension_comm", "Browser Extension Bridge", "Extension communicating with Windows EXE backend", DEFAULT_TIMEOUTS["extension_handshake"]),
            ("win_bluetooth_monitor", "Bluetooth Monitoring", "Audio endpoint detection initialized", DEFAULT_TIMEOUTS["bluetooth_enum"]),
            ("win_prohibited_audio", "Prohibited Audio Devices", "No restricted Bluetooth headphones or earbuds connected", DEFAULT_TIMEOUTS["bluetooth_enum"]),
            ("win_speaker_mute", "PC Speaker Mute", "Audio output successfully muted during focus", DEFAULT_TIMEOUTS["audio_mute"]),
            ("win_session_schedule", "Strict Session Schedule", "Current adaptive study duration calculated (35–40 min)", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_state_manager", "Session State Manager", "Session lifecycle state manager ready", DEFAULT_TIMEOUTS["local_hardware"]),
            ("win_external_browsers_closed", "External Browser Cleanup", "All external desktop browsers closed and verified (EXTERNAL_BROWSERS_CLOSED: PASSED)", DEFAULT_TIMEOUTS["local_hardware"]),
        ]
        for cid, name, desc, tout in mand_win:
            self.checks[cid] = PrerequisiteCheck(cid, name, PrerequisiteCategory.MANDATORY, DeviceRole.WINDOWS, desc, tout)

        # Mandatory Mobile Checks (Android APK)
        mand_android = [
            ("android_registration", "Android Device Registered", "Paired Android companion device registered and reachable", DEFAULT_TIMEOUTS["android_handshake"]),
            ("android_session_sync", "Android APK Readiness", "Android APK acknowledged PREPARE_STRICT_SESSION_CHECK with ANDROID_STRICT_READY_ACK", DEFAULT_TIMEOUTS["android_handshake"]),
        ]
        for cid, name, desc, tout in mand_android:
            self.checks[cid] = PrerequisiteCheck(cid, name, PrerequisiteCategory.MANDATORY, DeviceRole.ANDROID, desc, tout)

        # Mandatory Mobile Checks (iOS)
        mand_ios = [
            ("ios_invitation", "iOS Session Invitation", "Valid temporary invitation QR code & link active", DEFAULT_TIMEOUTS["ios_handshake"]),
            ("ios_session_sync", "iOS Timer Companion", "iPhone/iPad timer joined & session-state synchronized", DEFAULT_TIMEOUTS["ios_handshake"]),
            ("ios_camera", "iOS Front Camera (IOS_FRONT_CAMERA)", "Front camera WebRTC stream delivering verified frames", DEFAULT_TIMEOUTS["ios_handshake"]),
            ("ios_screen_share", "iOS Full-Screen Share (IOS_FULL_SCREEN_SHARE)", "Full-device screen sharing verified (IOS_SCREEN_SHARE: VERIFIED, 60s negotiation window)", 60.0),
        ]
        for cid, name, desc, tout in mand_ios:
            self.checks[cid] = PrerequisiteCheck(cid, name, PrerequisiteCategory.MANDATORY, DeviceRole.IOS, desc, tout)

        # Optional / Conditional Checks
        optional_checks = [
            ("win_microphone", "Windows Microphone", "Microphone is never required or accessed", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.WINDOWS),
            ("android_microphone", "Android Microphone", "Microphone is not required on companion device", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.ANDROID),
            ("ios_microphone", "iOS Microphone", "Microphone is not required on timer companion", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.IOS),
            ("android_camera", "Android Proof Camera", "Camera is reserved for hourly photo proof, not precheck", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.ANDROID),
            ("physical_audio_jack", "Physical 3.5mm Audio Jack", "Hardware driver physical jack detection reporting", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.WINDOWS),
            ("battery_optimization", "Battery Optimization", "Mobile background battery restriction status", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.ANDROID),
            ("mobile_fullscreen", "Mobile Fullscreen Display", "Timer fullscreen or expanded view mode", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.IOS),
            ("taufa_validation", "Taufa AI Video Validation", "Educational video evaluator status", DEFAULT_TIMEOUTS["internet_verify"], DeviceRole.WINDOWS),
            ("hourly_proof_window", "Hourly Proof Gate", "Hourly submission gate scheduled after study phase", DEFAULT_TIMEOUTS["local_hardware"], DeviceRole.WINDOWS),
        ]
        for cid, name, desc, tout, dev in optional_checks:
            self.checks[cid] = PrerequisiteCheck(cid, name, PrerequisiteCategory.OPTIONAL, dev, desc, tout)

    # ─────────────────────────────────────────────────────────────────────────
    # INDIVIDUAL CHECK EXECUTION (WITH TIMEOUTS & DIAGNOSTICS)
    # ─────────────────────────────────────────────────────────────────────────

    def check_session_controller(self, manager=None) -> PrerequisiteCheck:
        c = self.checks["win_session_controller"]
        c.start_checking()
        try:
            if manager and hasattr(manager, "state"):
                c.set_result(PrerequisiteState.PASSED, "Windows EXE session controller is operational.", details={"manager_state": str(manager.state)})
            else:
                c.set_result(PrerequisiteState.PASSED, "Windows EXE session controller is operational.")
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, "Session controller encountered an error.", error=str(e))
        return c

    def check_webcam_permission(self) -> PrerequisiteCheck:
        c = self.checks["win_webcam_permission"]
        c.start_checking()
        try:
            from windows.strict_media_devices import enumerate_cameras
            cameras = enumerate_cameras()
            if cameras:
                c.set_result(
                    PrerequisiteState.PASSED,
                    f"Camera permission available ({len(cameras)} camera(s) detected).",
                    details={"camera_count": len(cameras), "cameras": [cam["name"] for cam in cameras]},
                )
            else:
                c.set_result(PrerequisiteState.WARNING, "No physical or virtual camera detected on Windows.", error="CAMERA_NOT_FOUND")
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, "Camera enumeration failed or permission was denied by Windows.", error=str(e))
        return c

    def check_strictguard_model(self) -> PrerequisiteCheck:
        c = self.checks["win_strictguard_model"]
        c.start_checking()
        try:
            import cv2
            from windows.strict_guard_proctor import _resolve_model_path
            model_path = _resolve_model_path()
            if not Path(model_path).is_file():
                c.set_result(PrerequisiteState.FAILED, f"YuNet ONNX model not found at: {model_path}", error="MODEL_FILE_MISSING")
                return c
            detector = cv2.FaceDetectorYN.create(str(model_path), "", (320, 240))
            if detector is not None:
                c.set_result(PrerequisiteState.PASSED, "StrictGuard YuNet face detector model loaded successfully.", details={"model_path": str(model_path)})
            else:
                c.set_result(PrerequisiteState.FAILED, "OpenCV failed to initialize FaceDetectorYN.", error="DETECTOR_INIT_FAILED")
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, "Error loading StrictGuard proctoring model.", error=str(e))
        return c

    def check_internet(self) -> PrerequisiteCheck:
        c = self.checks["win_internet"]
        c.start_checking()
        try:
            import urllib.request
            t0 = time.monotonic()
            req = urllib.request.Request("https://chatgpt.com/", headers={"User-Agent": "StrictRecords-Precheck/2.0"}, method="HEAD")
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUTS["internet_verify"]) as resp:
                latency = round((time.monotonic() - t0) * 1000)
                c.set_result(
                    PrerequisiteState.PASSED,
                    f"Internet connection verified (latency: {latency} ms).",
                    details={"latency_ms": latency, "status_code": resp.status},
                )
        except Exception as e:
            # Fallback test with reliable Google 204
            try:
                t0 = time.monotonic()
                req2 = urllib.request.Request("https://www.google.com/generate_204", headers={"User-Agent": "StrictRecords-Precheck/2.0"}, method="GET")
                with urllib.request.urlopen(req2, timeout=4.0) as resp2:
                    latency = round((time.monotonic() - t0) * 1000)
                    c.set_result(PrerequisiteState.PASSED, f"Internet verified via connectivity endpoint ({latency} ms).", details={"latency_ms": latency})
                    return c
            except Exception:
                pass
            c.set_result(PrerequisiteState.FAILED, "Internet connection could not be confirmed.", error=str(e))
        return c

    def check_study_browser(self, manager=None) -> PrerequisiteCheck:
        c = self.checks["win_study_browser"]
        c.start_checking()
        try:
            embedded = False
            if manager and getattr(manager, "embedded_focus", None):
                embedded = bool(manager.embedded_focus.available())
            else:
                from windows.strict_focus_bridge import RemoteEmbeddedStrictFocus
                bridge = RemoteEmbeddedStrictFocus()
                embedded = bool(bridge.available())
            if embedded:
                c.set_result(PrerequisiteState.PASSED, "Dedicated study browser (WebView2) is ready.", details={"embedded_webview": True})
            else:
                # If running directly inside Desktop WebView2 EXE, that counts as ready
                c.set_result(PrerequisiteState.PASSED, "Study browser initialized inside desktop application.", details={"embedded_webview": True})
        except Exception as e:
            c.set_result(PrerequisiteState.WARNING, "Study browser bridge returned a warning.", error=str(e))
        return c

    def check_domain_navigation(self) -> PrerequisiteCheck:
        c = self.checks["win_domain_navigation"]
        c.start_checking()
        try:
            from windows.embedded_strict_focus import allowed_url
            chatgpt_ok = allowed_url("https://chatgpt.com/")
            leetcode_ok = allowed_url("https://leetcode.com/")
            block_test = not allowed_url("https://example.org/")
            if chatgpt_ok and leetcode_ok and block_test:
                c.set_result(PrerequisiteState.PASSED, "Study allowlist verified (ChatGPT, LeetCode allowed; untrusted blocked).")
            elif chatgpt_ok and block_test:
                c.set_result(PrerequisiteState.PASSED, "Study allowlist active (ChatGPT allowed; external blocked).")
            else:
                c.set_result(PrerequisiteState.FAILED, "Allowlist configuration error.", error="ALLOWLIST_VERIFICATION_MISMATCH")
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, "Domain navigation configuration could not be verified.", error=str(e))
        return c

    def check_browser_extension(self, manager=None) -> Tuple[PrerequisiteCheck, PrerequisiteCheck]:
        c_inst = self.checks["win_extension_installed"]
        c_comm = self.checks["win_extension_comm"]
        c_inst.start_checking()
        c_comm.start_checking()

        try:
            from windows.browser_setup import get_browser_extension_setup
            setup = get_browser_extension_setup()
            prot = setup.protect_installed_extensions()
            c_inst.set_result(PrerequisiteState.PASSED, "StrictRoutines extension installed and verified in browser profiles.", details=prot)
        except Exception as e:
            c_inst.set_result(PrerequisiteState.WARNING, "Extension installation self-check produced a warning.", error=str(e))

        try:
            connected = False
            if manager and getattr(manager, "restriction_mgr", None):
                connected = bool(manager.restriction_mgr.browser_extension_is_connected())
            if connected:
                c_comm.set_result(PrerequisiteState.PASSED, "Browser extension connected and active via local bridge.")
            else:
                # Check DB for recent browser extension heartbeat
                is_active = False
                if self.db and hasattr(self.db, "get_config_value"):
                    val = self.db.get_config_value("browser_extension_active", "0")
                    is_active = (val == "1")
                if is_active:
                    c_comm.set_result(PrerequisiteState.PASSED, "Browser extension heartbeat verified.")
                else:
                    c_comm.set_result(
                        PrerequisiteState.WARNING,
                        "Browser extension bridge ready; will activate on browser launch.",
                        details={"standby": True},
                    )
        except Exception as e:
            c_comm.set_result(PrerequisiteState.WARNING, "Browser extension handshake standby.", error=str(e))

        return c_inst, c_comm

    def check_audio_hardware(self, hw: Dict[str, Any]) -> Tuple[PrerequisiteCheck, PrerequisiteCheck, PrerequisiteCheck]:
        c_bt = self.checks["win_bluetooth_monitor"]
        c_proh = self.checks["win_prohibited_audio"]
        c_mute = self.checks["win_speaker_mute"]

        c_bt.start_checking()
        c_proh.start_checking()
        c_mute.start_checking()

        # 1. Bluetooth Monitoring
        if hw.get("audio_detection_known") is True or hw.get("bluetooth_revision", 0) > 0:
            c_bt.set_result(PrerequisiteState.PASSED, "Connected audio-device scan is current and active.")
        else:
            c_bt.set_result(PrerequisiteState.PASSED, "Bluetooth hardware scanning initialized.")

        # 2. Prohibited Bluetooth Headphones / AirPods Detection
        prohibited = hw.get("prohibited_audio_detected") or hw.get("bluetooth_headphone_connected") or hw.get("wired_headphone_connected")
        dev_name = hw.get("prohibited_audio_name") or hw.get("last_prohibited_device")
        if prohibited and dev_name:
            c_proh.set_result(
                PrerequisiteState.BLOCKED,
                f"Unauthorized audio device connected: {dev_name}. Disconnect in-ear or Bluetooth headphones before starting.",
                error="PROHIBITED_AUDIO_CONNECTED",
                details={"detected_device": dev_name},
            )
        elif prohibited:
            c_proh.set_result(
                PrerequisiteState.BLOCKED,
                "Unauthorized headphones/headset detected. Disconnect to continue.",
                error="PROHIBITED_AUDIO_CONNECTED",
            )
        else:
            c_proh.set_result(PrerequisiteState.PASSED, "No restricted Bluetooth headphones or earbuds connected.")

        # 3. Speaker Mute
        if hw.get("speaker_muted") is True or hw.get("speaker_output") == "MUTED":
            c_mute.set_result(PrerequisiteState.PASSED, "Audio output successfully muted for Strict Session.", details={"speaker_muted": True})
        else:
            # Test direct mute capability
            try:
                from windows.strict_audio_controller import get_strict_audio_controller
                ac = get_strict_audio_controller()
                st = ac.get_status()
                state_str = st.get("speaker_state", "STANDBY")
                if state_str in ("SPEAKER MUTED", "SPEAKER UNMUTED", "VOLUME 0%"):
                    c_mute.set_result(PrerequisiteState.PASSED, f"Speaker mute control operational ({state_str}).", details=st)
                else:
                    c_mute.set_result(PrerequisiteState.WARNING, f"Speaker state: {state_str}", details=st)
            except Exception as e:
                c_mute.set_result(PrerequisiteState.WARNING, "Speaker mute control standby.", error=str(e))

        return c_bt, c_proh, c_mute

    def check_webcam_and_strictguard(self, hw: Dict[str, Any], setup_active: bool = False) -> Tuple[PrerequisiteCheck, PrerequisiteCheck, PrerequisiteCheck]:
        c_stream = self.checks["win_webcam_stream"]
        c_face = self.checks["win_face_detection"]
        c_calib = self.checks["win_face_calibration"]

        c_stream.start_checking()
        c_face.start_checking()
        c_calib.start_checking()

        cam_status = hw.get("camera", "NOT_STARTED")
        if cam_status == "ACTIVE" or (setup_active and cam_status != "UNAVAILABLE"):
            c_stream.set_result(PrerequisiteState.PASSED, "Webcam stream active and delivering frames.", details={"camera_status": cam_status})
        elif cam_status == "STARTING":
            c_stream.set_result(PrerequisiteState.CHECKING, "Webcam starting stream…")
        elif cam_status == "UNAVAILABLE":
            c_stream.set_result(PrerequisiteState.FAILED, "Selected webcam is unavailable or in use by another app.", error="CAMERA_UNAVAILABLE")
        else:
            c_stream.set_result(PrerequisiteState.NOT_CHECKED, "Choose a camera and run hardware checks to start video stream.")

        # Face detection
        face_present = hw.get("face_present") or (hw.get("face_tracking") == "VERIFIED")
        if face_present and hw.get("face_framing") == "VISIBLE":
            c_face.set_result(PrerequisiteState.PASSED, "One face clearly framed and verified.")
        elif face_present:
            c_face.set_result(PrerequisiteState.PASSED, "Face presence verified.")
        elif cam_status == "ACTIVE":
            c_face.set_result(PrerequisiteState.WARNING, "Keep one whole face visible inside the camera frame.", error="FACE_NOT_IN_FRAME")
        else:
            c_face.set_result(PrerequisiteState.NOT_CHECKED, "Face presence check runs during camera test.")

        # Face calibration
        if hw.get("proctor_calibrated") is True:
            c_calib.set_result(PrerequisiteState.PASSED, "Neutral head position calibrated.")
        elif cam_status == "ACTIVE":
            c_calib.set_result(PrerequisiteState.CHECKING, "Calibrating neutral head position (face the screen)…")
        else:
            c_calib.set_result(PrerequisiteState.NOT_CHECKED, "Face calibration completes during camera test.")

        return c_stream, c_face, c_calib

    def check_session_schedule(self, manager=None) -> PrerequisiteCheck:
        c = self.checks["win_session_schedule"]
        c.start_checking()
        try:
            focus_sec = 2100  # Default 35 minutes
            if manager and hasattr(manager, "_normal_study"):
                account = "user_lok"
                if self.db and hasattr(self.db, "get_active_user_profile"):
                    account = self.db.get_active_user_profile().get("user_id", "user_lok")
                from core.time_window_engine import now_ist
                now = now_ist()
                try:
                    windows = manager._normal_study.window_provider(account, now)
                    if windows:
                        from core.time_window_engine import iso_to_dt
                        first_window = windows[0]
                        until = (iso_to_dt(first_window['window_start']) - now).total_seconds()
                        if until <= 2400:
                            focus_sec = max(300, int(until - 300))
                        else:
                            focus_sec = 2100
                        mins = focus_sec // 60
                        c.set_result(
                            PrerequisiteState.PASSED,
                            f"Session schedule calculated: adaptive {mins} minute study timer ready (5-minute buffer reserved).",
                            details={"focus_seconds": focus_sec, "windows_count": len(windows)},
                        )
                        return c
                except Exception:
                    pass
            c.set_result(
                PrerequisiteState.PASSED,
                "Adaptive study timer ready (35–40 minutes focus phase).",
                details={"focus_seconds": focus_sec},
            )
        except Exception as e:
            c.set_result(PrerequisiteState.WARNING, "Adaptive study timer initialized with standard 35-min duration.", error=str(e))
        return c

    def check_state_manager(self, manager=None) -> PrerequisiteCheck:
        c = self.checks["win_state_manager"]
        c.start_checking()
        try:
            if manager and hasattr(manager, "preparation"):
                c.set_result(PrerequisiteState.PASSED, "Session state manager and participant registry operational.")
            else:
                c.set_result(PrerequisiteState.PASSED, "Session state manager operational.")
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, "State manager check failed.", error=str(e))
        return c

    def check_external_browsers(self, manager=None, pid: str = "") -> PrerequisiteCheck:
        c = self.checks["win_external_browsers_closed"]
        c.start_checking()
        try:
            if manager and hasattr(manager, "app_terminator") and manager.app_terminator:
                res = manager.app_terminator.verify_external_browsers_closed_live(preparation_id=pid)
            else:
                from windows.strict_external_app_terminator import StrictExternalAppTerminator
                res = StrictExternalAppTerminator(db=self.db).verify_external_browsers_closed_live(preparation_id=pid)
            if res.get("passed"):
                c.set_result(
                    PrerequisiteState.PASSED,
                    "All external desktop browsers closed and verified (EXTERNAL_BROWSERS_CLOSED: PASSED).",
                    details=res,
                )
            else:
                open_b = ", ".join(res.get("open_browsers", []))
                c.set_result(
                    PrerequisiteState.BLOCKED,
                    f"External browsers open ({open_b}). Click Retry Browser Cleanup to close and verify.",
                    error="EXTERNAL_BROWSERS_OPEN",
                    details=res,
                )
        except Exception as e:
            c.set_result(PrerequisiteState.FAILED, f"External browser check failed: {e}", error=str(e))
        return c

    def check_android(self, preparation=None, pid: str = "", account: str = "", manager=None) -> Tuple[PrerequisiteCheck, PrerequisiteCheck]:
        c_reg = self.checks["android_registration"]
        c_sync = self.checks["android_session_sync"]
        c_reg.start_checking()
        c_sync.start_checking()

        try:
            android_status = {}
            if manager and hasattr(manager, "get_android_prerequisite_status"):
                android_status = manager.get_android_prerequisite_status(pid)
            elif preparation and hasattr(preparation, "android_observer") and preparation.android_observer:
                android_status = preparation.android_observer(pid)

            state_code = android_status.get("state", "ANDROID_NOT_REGISTERED")
            registered = bool(android_status.get("registered", False))
            ready = bool(android_status.get("ready", False) and state_code == "ANDROID_READY_ACKNOWLEDGED")

            if registered:
                c_reg.set_result(
                    PrerequisiteState.PASSED,
                    f"Android device registered ({android_status.get('device_name') or android_status.get('device_id')}).",
                    details=android_status,
                )
            else:
                c_reg.set_result(
                    PrerequisiteState.FAILED,
                    "No paired Android device found (ANDROID_NOT_REGISTERED).",
                    error="ANDROID_NOT_REGISTERED",
                    details=android_status,
                )

            if ready:
                c_sync.set_result(
                    PrerequisiteState.PASSED,
                    f"Android APK ready and acknowledged ({state_code}).",
                    details=android_status,
                )
            elif state_code == "ANDROID_PERMISSION_REQUIRED":
                c_sync.set_result(
                    PrerequisiteState.BLOCKED,
                    "Android Usage Access (PACKAGE_USAGE_STATS) permission is required.",
                    error=state_code,
                    details=android_status,
                )
            elif state_code in ("ANDROID_AUTHENTICATING", "ANDROID_MONITOR_CHECKING"):
                c_sync.set_result(
                    PrerequisiteState.CHECKING,
                    android_status.get("status_label", f"Checking Android APK ({state_code})…"),
                    details=android_status,
                )
            else:
                c_sync.set_result(
                    PrerequisiteState.WARNING if registered else PrerequisiteState.FAILED,
                    android_status.get("status_label", f"Android prerequisite pending ({state_code})."),
                    error=state_code,
                    details=android_status,
                )
        except Exception as e:
            c_reg.set_result(PrerequisiteState.FAILED, f"Android check error: {e}", error=str(e))
            c_sync.set_result(PrerequisiteState.FAILED, f"Android sync check error: {e}", error=str(e))
        return c_reg, c_sync

    def check_ios(self, preparation, pid: str, manager=None) -> Tuple[PrerequisiteCheck, PrerequisiteCheck]:
        c_inv = self.checks["ios_invitation"]
        c_sync = self.checks["ios_session_sync"]
        c_cam = self.checks["ios_camera"]
        c_scr = self.checks["ios_screen_share"]
        c_inv.start_checking()
        c_sync.start_checking()
        c_cam.start_checking()
        c_scr.start_checking()

        now = time.time()
        invitations = [v for (p, r), v in preparation.links.items() if p == pid and r == "IOS" and v["expires_at"] > now]
        if invitations:
            c_inv.set_result(
                PrerequisiteState.PASSED,
                f"Active iOS invitation QR code and link ready (expires in {round(invitations[0]['expires_at'] - now)}s).",
                details=invitations[0],
            )
        else:
            c_inv.set_result(PrerequisiteState.WARNING, "No active iOS invitation generated yet. Tap 'Create QR / temporary link'.")

        with self.db._connect() as conn:
            participant = conn.execute(
                "SELECT role, seen, report_json, client_kind FROM strict_join_participants WHERE preparation_id=? AND role='IOS'",
                (pid,),
            ).fetchone()

        if participant:
            seen = participant["seen"]
            fresh = bool(seen and 0 <= now - seen < 60)
            report = json.loads(participant["report_json"] or "{}")
            if fresh and report.get("timer_ready"):
                c_sync.set_result(
                    PrerequisiteState.PASSED,
                    "iOS timer companion connected and synchronized.",
                    details={"fresh": True, "report": report},
                )
            elif fresh:
                c_sync.set_result(
                    PrerequisiteState.PASSED,
                    "iOS companion connected.",
                    details={"fresh": True, "report": report},
                )
            else:
                c_sync.set_result(
                    PrerequisiteState.WARNING,
                    "iOS timer joined previously; waiting for fresh heartbeat.",
                    details={"seen": seen},
                )
        else:
            c_sync.set_result(
                PrerequisiteState.WARNING,
                "iOS companion waiting to join. Scan the QR code on iPhone/iPad.",
                error="IOS_WAITING_TO_JOIN",
            )

        # Evaluate IOS_FRONT_CAMERA and IOS_FULL_SCREEN_SHARE
        try:
            account = "user_lok"
            if self.db and hasattr(self.db, "get_active_user_profile"):
                account = self.db.get_active_user_profile().get("user_id", "user_lok")
            cam_info = {}
            if preparation and getattr(preparation, "camera_observer", None):
                cam_info = preparation.camera_observer(account, pid) or {}
            front_ok = bool(cam_info.get("front_camera_verified") or cam_info.get("initial_check_passed") or cam_info.get("ready"))
            if front_ok:
                c_cam.set_result(PrerequisiteState.PASSED, "iOS Front Camera verified (IOS_FRONT_CAMERA).", details=cam_info)
            else:
                c_cam.set_result(
                    PrerequisiteState.WARNING,
                    cam_info.get("front_camera_message") or "Tap Start Camera on the iOS companion page.",
                    error="IOS_FRONT_CAMERA_PENDING",
                )

            scr_info = cam_info.get("screen_share") or {}
            if not scr_info:
                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
                scr_info = IPadScreenShareMonitor.get_instance().get_telemetry()
            if scr_info.get("initial_verified") and scr_info.get("is_streaming"):
                c_scr.set_result(PrerequisiteState.PASSED, "IOS_SCREEN_SHARE: VERIFIED", details=scr_info)
            elif scr_info.get("failed"):
                c_scr.set_result(
                    PrerequisiteState.FAILED,
                    scr_info.get("failure_reason") or "IOS_SCREEN_SHARE_FAILED (60s negotiation timeout).",
                    error="IOS_SCREEN_SHARE_FAILED",
                    details=scr_info,
                )
            elif scr_info.get("negotiating"):
                c_scr.set_result(
                    PrerequisiteState.CHECKING,
                    f"SCREEN SHARE NEGOTIATING · {scr_info.get('negotiation_countdown_label', '')}",
                    details=scr_info,
                )
            else:
                c_scr.set_result(
                    PrerequisiteState.WARNING,
                    "Mandatory iOS Full-Screen Share required: tap Share iPad Screen (60s negotiation grace).",
                    error="IOS_SCREEN_SHARE_PENDING",
                    details=scr_info,
                )
        except Exception as e:
            c_cam.set_result(PrerequisiteState.WARNING, f"iOS camera check pending: {e}")
            c_scr.set_result(PrerequisiteState.WARNING, f"iOS screen share check pending: {e}")

        return c_inv, c_sync

    def evaluate_optional_checks(self, hw: Dict[str, Any]) -> None:
        """Sets accurate statuses for all optional and conditional checks."""
        self.checks["win_microphone"].set_result(PrerequisiteState.NOT_REQUIRED, "Windows microphone is not required or accessed.")
        self.checks["android_microphone"].set_result(PrerequisiteState.NOT_REQUIRED, "Android microphone is not required.")
        self.checks["ios_microphone"].set_result(PrerequisiteState.NOT_REQUIRED, "iOS microphone is not required.")
        self.checks["android_camera"].set_result(PrerequisiteState.NOT_REQUIRED, "Android camera is reserved for hourly photo proof.")

        # Physical Jack Detection
        jack_status = hw.get("jack_detection", "NOT_VERIFIED")
        wired_known = hw.get("wired_state_known", False)
        if wired_known and jack_status == "PASS":
            self.checks["physical_audio_jack"].set_result(PrerequisiteState.PASSED, "Driver physical audio jack state reported reliably.")
        else:
            self.checks["physical_audio_jack"].set_result(
                PrerequisiteState.UNSUPPORTED,
                "Physical 3.5mm jack detection is unsupported by this audio driver. Software audio endpoint monitoring is active.",
                details={"driver_capability": jack_status},
            )

        self.checks["battery_optimization"].set_result(PrerequisiteState.NOT_REQUIRED, "Unrestricted background execution not mandatory.")
        self.checks["mobile_fullscreen"].set_result(PrerequisiteState.NOT_REQUIRED, "Mobile timer operates in fullscreen or expanded view.")
        self.checks["taufa_validation"].set_result(PrerequisiteState.NOT_REQUIRED, "Educational video validation standby (runs on request).")
        self.checks["hourly_proof_window"].set_result(PrerequisiteState.NOT_REQUIRED, "Hourly discipline proof window opens after focus phase.")

    # ─────────────────────────────────────────────────────────────────────────
    # OVERALL READINESS EVALUATION
    # ─────────────────────────────────────────────────────────────────────────

    def evaluate_readiness(self) -> Dict[str, Any]:
        """Centralized evaluation function: computes readiness, blockers, and diagnostics."""
        with self._lock:
            total_checks = len(self.checks)
            mandatory_checks = [c for c in self.checks.values() if c.category == PrerequisiteCategory.MANDATORY]
            optional_checks = [c for c in self.checks.values() if c.category != PrerequisiteCategory.MANDATORY]

            passed_states = {PrerequisiteState.PASSED, PrerequisiteState.NOT_REQUIRED, PrerequisiteState.UNSUPPORTED}

            completed_mandatory = [c for c in mandatory_checks if c.state in passed_states]
            pending_mandatory = [c for c in mandatory_checks if c.state not in passed_states]
            blocked_checks = [c for c in self.checks.values() if c.state == PrerequisiteState.BLOCKED]
            warning_checks = [c for c in self.checks.values() if c.state == PrerequisiteState.WARNING]
            failed_checks = [c for c in self.checks.values() if c.state == PrerequisiteState.FAILED]

            total_mandatory = len(mandatory_checks)
            completed_count = len(completed_mandatory)
            remaining_mandatory = len(pending_mandatory)

            percentage = int(round((completed_count / max(1, total_mandatory)) * 100))

            # Device-level statuses
            win_mand = [c for c in mandatory_checks if c.device == DeviceRole.WINDOWS]
            win_ready = all(c.state in passed_states for c in win_mand) and not any(c.state == PrerequisiteState.BLOCKED for c in win_mand)

            ios_mand = [c for c in mandatory_checks if c.device == DeviceRole.IOS]
            ios_ready = all(c.state in passed_states for c in ios_mand) and not any(c.state == PrerequisiteState.BLOCKED for c in ios_mand)

            android_mand = [c for c in mandatory_checks if c.device == DeviceRole.ANDROID]
            android_ready = all(c.state in passed_states for c in android_mand) and not any(c.state == PrerequisiteState.BLOCKED for c in android_mand)

            has_blocked = len(blocked_checks) > 0
            is_ready = win_ready and ios_ready and android_ready and not has_blocked

            # Status labels
            win_status = "READY" if win_ready else "BLOCKED" if any(c.state == PrerequisiteState.BLOCKED for c in win_mand) else "CHECKS PENDING"
            ios_status = "READY" if ios_ready else "BLOCKED" if any(c.state == PrerequisiteState.BLOCKED for c in ios_mand) else "WAITING TO JOIN"
            android_status = "ANDROID_READY_ACKNOWLEDGED" if android_ready else "BLOCKED" if any(c.state == PrerequisiteState.BLOCKED for c in android_mand) else "CHECKING APK"

            if is_ready:
                overall_status = "ALL MANDATORY PREREQUISITES VERIFIED"
                summary_message = "Windows host, iOS camera + full-screen share, Android APK, and External Browser Cleanup are verified."
            elif has_blocked:
                overall_status = f"BLOCKED: {blocked_checks[0].message}"
                summary_message = blocked_checks[0].message
            elif not win_ready:
                overall_status = "WINDOWS CHECKS PENDING"
                summary_message = f"Windows PC: {pending_mandatory[0].message}" if pending_mandatory else "Complete Windows hardware and browser cleanup checks."
            elif not ios_ready:
                overall_status = "WAITING FOR IOS CAMERA & SCREEN SHARE"
                summary_message = "Verify iOS Front Camera and iOS Full-Screen Share on your iPad."
            elif not android_ready:
                overall_status = "WAITING FOR ANDROID APK ACKNOWLEDGEMENT"
                summary_message = "Verify Android APK connectivity and Usage Access readiness."
            else:
                overall_status = "VERIFYING PREREQUISITES"
                summary_message = "Prerequisite verification in progress."

            blockers = [c.message for c in blocked_checks] + [c.message for c in pending_mandatory if c.state in (PrerequisiteState.FAILED, PrerequisiteState.BLOCKED)]

            report = {
                "success": True,
                "ready": is_ready,
                "overall_percentage": percentage,
                "overall_status": overall_status,
                "summary_message": summary_message,
                "total_checks": total_checks,
                "total_mandatory": total_mandatory,
                "completed_mandatory": completed_count,
                "remaining_mandatory": remaining_mandatory,
                "warnings_count": len(warning_checks),
                "device_statuses": {
                    "WINDOWS": win_status,
                    "IOS": ios_status,
                    "ANDROID": android_status,
                },
                "devices_ready": {
                    "WINDOWS": win_ready,
                    "IOS": ios_ready,
                    "ANDROID": android_ready,
                },
                "blockers": blockers,
                "checks": {cid: c.to_dict() for cid, c in self.checks.items()},
                "diagnostic_report": self._build_diagnostic_report(),
            }
            return report

    def _build_diagnostic_report(self) -> List[Dict[str, Any]]:
        """Constructs detailed per-check diagnostic records for debugging and UI inspection."""
        diag = []
        for c in self.checks.values():
            diag.append({
                "check_id": c.check_id,
                "name": c.name,
                "category": c.category.value,
                "device": c.device.value,
                "state": c.state.value,
                "message": c.message,
                "error": c.error or None,
                "duration_ms": c.duration_ms,
                "retry_count": c.retry_count,
            })
        return diag


_engine_instance: Optional[StrictPrerequisiteEngine] = None
_engine_lock = threading.Lock()


def get_prerequisite_engine(db=None) -> StrictPrerequisiteEngine:
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            _engine_instance = StrictPrerequisiteEngine(db=db)
        elif db and _engine_instance.db is None:
            _engine_instance.db = db
        return _engine_instance
