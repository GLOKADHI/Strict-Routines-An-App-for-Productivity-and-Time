"""
STRICTRECORDS — Strict Headphone & Bluetooth Audio Enforcement Detector
Monitors connected Bluetooth earbuds/headphones and wired 3.5mm/USB headsets
on Windows PC throughout the active Strict Session.

Enforces:
- Prohibited Bluetooth earbuds: Apple AirPods, AirPods Pro, CMF Buds, Sony WF-1000XM5,
  Sony WF-series, other wireless audio headsets.
- Wired headphones: 3.5mm jack earphones, wired headsets, USB headphones.
- Non-audio Bluetooth devices (keyboards, mice, gamepads) are explicitly ignored.
- Distinguishes between paired-only and actively connected devices.
- Reports physical jack detection capability explicitly (Supported / Unsupported / Not verified).
- Violation policy:
  1st confirmed connection: warning, pause timer, wait for disconnection, resume when clear.
  2nd distinct confirmed connection: fail session, apply 2-point deduction, revoke YouTube reward.
"""

import ctypes
import logging
import hashlib
import json
import re
import threading
import time
from ctypes import wintypes as wt
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("StrictHeadphoneDetector")
from windows.strict_audio_endpoints import audio_devices,is_active_render,form_factor,jack_descriptions

# Prohibited Bluetooth audio keywords & model identifiers
PROHIBITED_BLUETOOTH_KEYWORDS = [
    "airpod", "airpods", "airpods pro", "airpods max",
    "cmf bud", "cmf buds", "cmf by nothing", "nothing ear",
    "wf-1000", "wh-1000", "wf-c", "wf-sp", "sony wf", "sony wh",
    "galaxy bud", "galaxy buds", "pixel bud", "pixel buds",
    "freebud", "freebuds", "bose quietcomfort", "soundlink",
    "earbud", "earbuds", "headphone", "headphones", "headset",
    "wireless stereo", "bluetooth audio", "hands-free",
    "jabra elite", "sennheiser momentum", "beats fit", "beats studio",
]

# Non-audio Bluetooth device classes (to ensure keyboards/mice are NEVER flagged)
EXCLUDED_BLUETOOTH_CLASSES = {
    0x0540,  # Keyboard
    0x0580,  # Mouse / pointing device
    0x05C0,  # Combo keyboard/pointing
    0x0504,  # Joystick
    0x0508,  # Gamepad
    0x0200,  # Phone (cellular)
    0x0100,  # Computer desktop/server
}


class SystemTime(ctypes.Structure):
    _fields_ = [(name, wt.WORD) for name in ("year", "month", "dayOfWeek", "day", "hour", "minute", "second", "milliseconds")]


class DeviceInfo(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("address", ctypes.c_ulonglong),
        ("deviceClass", wt.DWORD),
        ("connected", wt.BOOL),
        ("remembered", wt.BOOL),
        ("authenticated", wt.BOOL),
        ("lastSeen", SystemTime),
        ("lastUsed", SystemTime),
        ("name", wt.WCHAR * 248),
    ]


class SearchParams(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("authenticated", wt.BOOL),
        ("remembered", wt.BOOL),
        ("unknown", wt.BOOL),
        ("connected", wt.BOOL),
        ("inquiry", wt.BOOL),
        ("timeout", ctypes.c_ubyte),
        ("radio", wt.HANDLE),
    ]


def _enumerate_radio_audio(radio) -> List[Dict[str, Any]]:
    """
    Enumerates actively connected Bluetooth devices via Win32 BluetoothApis.dll.
    Filters specifically for Audio/Video major class (0x0400) or audio model names.
    Explicitly ignores keyboards, mice, and other non-audio peripherals.
    """
    lib = ctypes.WinDLL("BluetoothApis.dll", use_last_error=True)
    lib.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(SearchParams), ctypes.POINTER(DeviceInfo)]
    lib.BluetoothFindFirstDevice.restype = wt.HANDLE
    lib.BluetoothFindNextDevice.argtypes = [wt.HANDLE, ctypes.POINTER(DeviceInfo)]
    lib.BluetoothFindDeviceClose.argtypes = [wt.HANDLE]

    query = SearchParams()
    query.dwSize = ctypes.sizeof(query)
    query.connected = True  # ACTIVELY CONNECTED only, not merely remembered
    query.radio = radio

    info = DeviceInfo()
    info.dwSize = ctypes.sizeof(info)

    ctypes.set_last_error(0)
    handle = lib.BluetoothFindFirstDevice(ctypes.byref(query), ctypes.byref(info))
    if not handle:
        error=ctypes.get_last_error()
        if error not in (0,259):raise OSError(error,"Bluetooth enumeration unavailable")
        return []

    detected_devices = []
    try:
        while True:
            dev_name = str(info.name).strip()
            dev_class = info.deviceClass
            major_class = dev_class & 0x1F00

            # Exclude keyboards, mice, peripherals
            if dev_class in EXCLUDED_BLUETOOTH_CLASSES or major_class == 0x0500:
                pass
            elif info.connected:
                is_audio_class = (major_class == 0x0400)
                name_lower = dev_name.lower()
                is_audio_name = StrictHeadphoneDetector.is_prohibited_audio_name(dev_name)

                if is_audio_class or is_audio_name:
                    detected_devices.append({
                        "name": dev_name,
                        "class": dev_class,
                        "type": "BLUETOOTH",
                        "connected": True,
                    })

            if not lib.BluetoothFindNextDevice(handle, ctypes.byref(info)):
                break
    finally:
        lib.BluetoothFindDeviceClose(handle)

    return detected_devices


def enumerate_connected_bluetooth_audio():
    lib=ctypes.WinDLL('BluetoothApis.dll',use_last_error=True)
    class RadioParams(ctypes.Structure):_fields_=[('size',wt.DWORD)]
    lib.BluetoothFindFirstRadio.argtypes=[ctypes.POINTER(RadioParams),ctypes.POINTER(wt.HANDLE)]
    lib.BluetoothFindFirstRadio.restype=wt.HANDLE
    lib.BluetoothFindNextRadio.argtypes=[wt.HANDLE,ctypes.POINTER(wt.HANDLE)]
    lib.BluetoothFindNextRadio.restype=wt.BOOL
    lib.BluetoothFindRadioClose.argtypes=[wt.HANDLE]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CloseHandle.argtypes=[wt.HANDLE]
    params=RadioParams(ctypes.sizeof(RadioParams));radio=wt.HANDLE()
    ctypes.set_last_error(0)
    search=lib.BluetoothFindFirstRadio(ctypes.byref(params),ctypes.byref(radio))
    if not search:
        error=ctypes.get_last_error()
        if error in (0,259,1168):return []
        raise OSError(error,'Bluetooth radio scan unavailable')
    result=[]
    try:
        while True:
            try:result.extend(_enumerate_radio_audio(radio))
            finally:kernel.CloseHandle(radio)
            if not lib.BluetoothFindNextRadio(search,ctypes.byref(radio)):break
    finally:lib.BluetoothFindRadioClose(search)
    return result


class StrictHeadphoneDetector:
    """
    Continuous detection service for prohibited Bluetooth & wired headphones.
    Tracks session violations according to the two-strike policy.
    """

    def __init__(self, db=None):
        self.db = db
        self._lock = threading.RLock()
        self._baseline_endpoints: Set[str] = set()
        self._monitoring_active = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # State tracking
        self.violations_count = 0
        self.current_violation: Optional[Dict[str, Any]] = None
        self.is_paused_for_headphone = False
        self._last_detected_device_signature: Optional[str] = None
        self._clean_samples_count = 0

        # Physical jack detection reporting
        self.jack_detection_status = "NOT_VERIFIED"
        self._snapshot = {}
        self._last_sample = None
        self._endpoint_wireless = []
        self._prev_bt_devices: Set[str] = set()
        self._camera_earbuds_worn = False
        self._camera_earbud_category = None
        self._camera_earbud_confidence = 0.0
        self.integrity = None

    def set_integrity(self, integrity) -> None:
        """Connects session integrity episode recorder."""
        with self._lock:
            self.integrity = integrity

    def update_camera_earbuds(self, worn: bool, category: str = "AIRPODS", confidence: float = 0.0) -> None:
        """Receives visual camera earbud wearing evidence from StrictGuard."""
        with self._lock:
            self._camera_earbuds_worn = bool(worn)
            self._camera_earbud_category = category if worn else None
            self._camera_earbud_confidence = float(confidence) if worn else 0.0

    @classmethod
    def is_prohibited_audio_name(cls, name: str) -> bool:
        """Determines if a device name matches prohibited audio hardware."""
        n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        return any(re.sub(r"[^a-z0-9]", "", kw) in n for kw in PROHIBITED_BLUETOOTH_KEYWORDS)

    def get_jack_detection_status(self) -> str:
        """Returns the hardware audio jack detection capability."""
        return self.jack_detection_status

    def capture_baseline(self) -> None:
        """Records initial active audio endpoints before session starts."""
        with self._lock:
            self._baseline_endpoints = set(self._get_active_endpoint_ids())
            logger.info(f"Captured {len(self._baseline_endpoints)} baseline audio endpoints.")

    def start_monitoring(self) -> None:
        """Starts the background audio device polling thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._monitoring_active = True
            self.capture_baseline()
            self._thread = threading.Thread(
                target=self._monitoring_loop,
                name="StrictHeadphoneDetectorThread",
                daemon=True,
            )
            self._thread.start()
            logger.info("StrictHeadphoneDetector: Monitoring started.")

    def stop_monitoring(self) -> None:
        """Stops the audio monitoring thread."""
        self._stop_event.set()
        with self._lock:
            self._monitoring_active = False
        if self._thread and self._thread.is_alive():
            if self._thread != threading.current_thread():
                self._thread.join(timeout=1.5)
            logger.info("StrictHeadphoneDetector: Monitoring stopped.")

    def reset_session(self) -> None:
        """Resets violation counts for a new session."""
        with self._lock:
            self.violations_count = 0
            self.current_violation = None
            self.is_paused_for_headphone = False
            self._last_detected_device_signature = None
            self._clean_samples_count = 0
            self._snapshot={};self._last_sample=None
            self._prev_bt_devices = set()
            self._camera_earbuds_worn = False
            self._camera_earbud_category = None
            self._camera_earbud_confidence = 0.0

    def check_audio_devices(self) -> Dict[str, Any]:
        """
        Executes a synchronous check across Bluetooth and wired audio endpoints.
        Returns comprehensive detection telemetry.
        """
        known=True
        try:bluetooth_devices = enumerate_connected_bluetooth_audio()
        except Exception:bluetooth_devices=[];known=False
        try:
            wired_devices = self._detect_wired_headphones()
            bluetooth_devices += [d for d in self._endpoint_wireless if d['name'] not in {item['name'] for item in bluetooth_devices}]
        except Exception:wired_devices=[];known=False;self.jack_detection_status='NOT_VERIFIED' 

        prohibited_detected = []
        for bt in bluetooth_devices:
            prohibited_detected.append({
                "type": "BLUETOOTH",
                "name": bt["name"],
                "description": f"Bluetooth Earbuds/Headphone: {bt['name']}",
            })

        for wd in wired_devices:
            prohibited_detected.append({
                "type": "WIRED",
                "name": wd["name"],
                "description": f"Wired Headphone/Headset: {wd['name']}",
            })

        if self._camera_earbuds_worn:
            cat = self._camera_earbud_category or "AirPods"
            prohibited_detected.append({
                "type": "CAMERA",
                "name": cat,
                "description": f"Camera-detected worn earbuds: {cat}",
                "confidence": self._camera_earbud_confidence,
            })

        has_prohibited = len(prohibited_detected) > 0
        primary_detected = prohibited_detected[0] if has_prohibited else None
        evidence_sources = list({d["type"] for d in prohibited_detected})

        return {
            "prohibited_connected": has_prohibited,
            "detection_known": known,
            "detected_device": primary_detected["name"] if primary_detected else None,
            "detected_type": primary_detected["type"] if primary_detected else None,
            "detected_description": primary_detected["description"] if primary_detected else None,
            "bluetooth_headphone_connected": any(d["type"] == "BLUETOOTH" for d in prohibited_detected),
            "wired_headphone_connected": any(d["type"] == "WIRED" for d in prohibited_detected),
            "bluetooth_devices": [d["name"] for d in bluetooth_devices],
            "wired_devices": [d["name"] for d in wired_devices],
            "jack_detection": self.jack_detection_status,
            "wired_state_known":getattr(self,"_wired_state_known",False),
            "violations_count": self.violations_count,
            "is_paused": self.is_paused_for_headphone,
            "evidence_sources": evidence_sources,
            "camera_earbuds_worn": self._camera_earbuds_worn,
            "camera_earbud_category": self._camera_earbud_category,
        }

    def _get_active_endpoint_ids(self):
        try:
            with audio_devices() as devices:return [d.id for d in devices if is_active_render(d)]
        except Exception:return []

    def _detect_wired_headphones(self):
        detected=[];wireless=[];jack_supported=False;jack_unverified=False;self._wired_state_known=True
        with audio_devices() as devices:
            for device in devices:
                if not str(device.id).startswith('{0.0.0.'):continue
                name=str(device.FriendlyName or 'Audio endpoint');lower=name.lower()
                factor=form_factor(device);active=is_active_render(device)
                try:
                    from core.database import _default_database_path
                    from pathlib import Path
                    calibration=json.loads((Path(_default_database_path()).parent/'strict_audio_driver_validation.json').read_text())
                    reliable=hashlib.sha256(device.id.encode()).hexdigest() not in calibration.get('unreliable_jack_ids',[])
                except (OSError,ValueError):reliable=True
                compact=re.sub(r'[^a-z0-9]','',lower)
                model=any(word in compact for word in ('sonywf','sonywh','wf1000','wh1000','cmfbud','airpod','galaxybud','nothingear'))
                transport=any('BTHENUM' in str(v).upper() or 'BTHHFPAUDIO' in str(v).upper() for v in device.properties.values())
                if active and (model or transport):
                    wireless.append(dict(name=name,type='BLUETOOTH',connected=True));continue
                # An active explicit headphone endpoint is sufficient evidence.
                named=any(word in lower for word in ('headphone','headset','earphone','3.5mm'))
                headphone=active and (factor in (3,5,6) or named)
                # Shared Realtek endpoints may expose a real physical jack bit.
                try:
                    jacks=jack_descriptions(device)
                    physical=[j for j in jacks if j['connection_type'] in (1,2) and j['port'] in (0,2)]
                    if physical and not reliable:
                        headphone=False;self._wired_state_known=False;jack_unverified=True
                    elif physical:
                        jack_supported=jack_supported or any(j['presence_supported'] for j in physical)
                        # Without presence-detect capability Windows specifies
                        # IsConnected=TRUE even for an empty jack. Do not flag it.
                        headphone=any(j['connected'] and j['presence_supported'] for j in physical)
                        if not any(j['presence_supported'] for j in physical):self._wired_state_known=False
                except Exception:pass
                if headphone:detected.append(dict(id=device.id,name=name,form_factor=factor,type='WIRED'))
        self._endpoint_wireless=wireless
        self.jack_detection_status='NOT_VERIFIED' if jack_unverified else 'SUPPORTED' if jack_supported else 'UNSUPPORTED'
        return detected

    def _apply_observation(self,res):
        with self._lock:
            if res.get('detection_known'):
                current_bt_set = set(res.get('bluetooth_devices', []))
                if self.integrity is not None:
                    for dev in (current_bt_set - self._prev_bt_devices):
                        if self.is_prohibited_audio_name(dev):
                            self.integrity.record(
                                'BLUETOOTH_AUDIO_DEVICE_CONNECTED',
                                f'Prohibited Bluetooth audio device connected: {dev}',
                                source='BLUETOOTH',
                                device_name=dev
                            )
                    for dev in (self._prev_bt_devices - current_bt_set):
                        if self.is_prohibited_audio_name(dev):
                            self.integrity.record(
                                'BLUETOOTH_AUDIO_DEVICE_DISCONNECTED',
                                f'Prohibited Bluetooth audio device disconnected: {dev}',
                                source='BLUETOOTH',
                                device_name=dev
                            )
                self._prev_bt_devices = current_bt_set

                if res.get('prohibited_connected'):
                    self._clean_samples_count = 0
                    if not self.is_paused_for_headphone:
                        self.violations_count += 1
                        self.is_paused_for_headphone = True
                        if self.integrity is not None:
                            self.integrity.record(
                                'HEADPHONE_VIOLATION_CONFIRMED',
                                f'Prohibited audio device confirmed: {res.get("detected_device")}.',
                                source='AUDIO_ENFORCEMENT',
                                violation_number=self.violations_count,
                                evidence_sources=res.get('evidence_sources', []),
                                device_name=res.get('detected_device')
                            )
                    self.current_violation = dict(
                        device_name=res.get('detected_device'),
                        device_type=res.get('detected_type'),
                        violation_number=self.violations_count,
                        evidence_sources=res.get('evidence_sources', [])
                    )
                elif self.is_paused_for_headphone:
                    self._clean_samples_count += 1
                    if self._clean_samples_count >= 2:
                        self.is_paused_for_headphone = False
                        self.current_violation = None
                        self._last_detected_device_signature = None
                        if self.integrity is not None:
                            self.integrity.record(
                                'HEADPHONE_VIOLATION_CLEARED',
                                'Prohibited headphone violation cleared; audio devices clear.',
                                source='AUDIO_ENFORCEMENT'
                            )
            else:
                self._clean_samples_count = 0
            self._snapshot = dict(res, violations_count=self.violations_count, is_paused=self.is_paused_for_headphone)
            self._last_sample = time.monotonic()
            return dict(self._snapshot)

    def get_status(self):
        with self._lock:
            snapshot=dict(self._snapshot)
            fresh=self._last_sample is not None and time.monotonic()-self._last_sample<4.5
            snapshot['detection_known']=bool(self._monitoring_active and fresh and snapshot.get('detection_known'))
            return snapshot

    def _monitoring_loop(self):
        while not self._stop_event.is_set():
            try:self._apply_observation(self.check_audio_devices())
            except Exception:self._apply_observation(dict(detection_known=False,prohibited_connected=False))
            self._stop_event.wait(1.5)


# Global singleton instance
_headphone_detector: Optional[StrictHeadphoneDetector] = None


def get_strict_headphone_detector() -> StrictHeadphoneDetector:
    global _headphone_detector
    if _headphone_detector is None:
        _headphone_detector = StrictHeadphoneDetector()
    return _headphone_detector
