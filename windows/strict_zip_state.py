"""Presentation adapter for the supplied Strict Session React frontend.

No scoring, session transitions, device enforcement or permission requests live
here. The backend's current observations are projected into the frontend's
provider interfaces; absence of evidence never becomes a positive indicator.
"""
from copy import deepcopy
from datetime import datetime
import base64
import binascii
import math
import time
from zoneinfo import ZoneInfo


def _number(value, default=None, *, minimum=0, maximum=None):
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def _boolean(value):
    return value if isinstance(value, bool) else None


def _observed(status, hardware, *keys):
    for key in keys:
        value = hardware.get(key, status.get(key))
        if value is not None:
            return value
    return None


def _text(value, default=None, limit=160):
    return value[:limit] if isinstance(value, str) and value else default


def _unverified(status, hardware, offline):
    return bool(offline or status.get('synthetic') is True or hardware.get('synthetic') is True
                or str(status.get('state', '')).startswith(('OFFLINE_', 'SIMULATED', 'SYNTHETIC'))
                or str(hardware.get('camera', '')).startswith(('OFFLINE_', 'SIMULATED', 'SYNTHETIC')))


def _jpeg(encoded):
    if not isinstance(encoded, str) or not 0 < len(encoded) < 65536:
        return None
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(image) < 4 or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
        return None
    return 'data:image/jpeg;base64,' + encoded


def _frame(hardware, offline):
    if offline or hardware.get('camera') != 'ACTIVE' or hardware.get('attention_monitoring') != 'ACTIVE':
        return None
    preview = hardware.get('camera_preview') or {}
    if not isinstance(preview, dict) or preview.get('current') is not True:
        return None
    age = hardware.get('last_frame_age_seconds')
    if age is not None and _number(age, maximum=2) is None:
        return None
    return _jpeg(preview.get('jpeg'))


def _ios_camera(hardware, unverified):
    """Keep secondary iPhone frames and primary Windows camera independent."""
    status = hardware.get('ios_camera') if not unverified else {}
    status = status if isinstance(status, dict) else {}
    preview = hardware.get('ios_camera_preview') if not unverified else {}
    preview = preview if isinstance(preview, dict) else {}
    now = time.time()
    frame_at = _number(status.get('last_frame_at'))
    observed_at = _number(preview.get('observed_at'))
    streaming = bool(not unverified and status.get('streaming') is True
                     and status.get('state') == 'IOS_CAMERA_STREAMING'
                     and frame_at is not None and 0 <= now - frame_at < 8)
    frame = (_jpeg(preview.get('jpeg')) if streaming and observed_at is not None
             and 0 <= now - observed_at < 3 and preview.get('source') == 'iPhone camera' else None)
    state = _text(status.get('state'), 'UNVERIFIED', 64)
    if state == 'IOS_CAMERA_STREAMING' and not streaming:
        state = 'IOS_CAMERA_INTERRUPTED'
    camera = dict(state=state, streaming=streaming, last_frame_at=frame_at,
                  source='iPhone camera')
    # Capture can be live even if the independent StrictGuard model is unavailable.
    # Preserve that distinction instead of inventing a positive monitoring state.
    result = dict(source='iPhone camera', jpeg=frame.split(',', 1)[1] if frame else None,
                  strictguard='ACTIVE' if frame and preview.get('strictguard') == 'ACTIVE' else 'UNVERIFIED',
                  face='VISIBLE' if frame and preview.get('face') == 'VISIBLE' else 'UNVERIFIED',
                  observed_at=observed_at if frame else None)
    return camera, result


def end_enabled(status, hardware, offline=False):
    """Keep the existing native End button gate; never infer completion locally."""
    if offline or status.get('mode') == 'DEMO':
        return True
    left = _number(hardware.get('leftward_violations'), 0)
    maximum = _number(hardware.get('max_leftward_violations'), 15)
    headphone = _number(hardware.get('headphone_violations'), 0)
    reward_ok = status.get('reward_eligible', True) is True and left <= maximum and headphone < 2
    return bool(status.get('end_enabled') is True
                and hardware.get('monitoring_ok') is True
                and hardware.get('notification_filter_active') is True
                and hardware.get('prohibited_audio_detected') is not True
                and reward_ok)


def frontend_snapshot(status, hardware, offline=False):
    """Return the ZIP's complete IStrictSessionState using host evidence only."""
    status = status if isinstance(status, dict) else {}
    hardware = hardware if isinstance(hardware, dict) else {}
    unverified = _unverified(status, hardware, offline)
    observed_hardware = {} if unverified else hardware
    observed_status = {} if unverified else status
    mode = 'DEMO' if offline or status.get('mode') == 'DEMO' else 'PRODUCTION'
    state = str(status.get('state') or '')
    adaptive = state == 'ADAPTIVE_COMPLETION'
    remaining = _number(status.get('adaptive_remaining_seconds') if adaptive else status.get('focus_remaining_seconds'))
    total = _number(2400 if adaptive else status.get('focus_target_seconds'))
    elapsed = _number(status.get('elapsed_seconds'), 0)
    timer_known = remaining is not None and total is not None and total > 0
    completed = bool(status.get('focus_completed_at'))
    if timer_known and completed:
        run_state = 'COMPLETED'
    elif timer_known and offline:
        run_state = 'PAUSED'
    elif timer_known and state in {'ACTIVE_FOCUS', 'ADAPTIVE_COMPLETION', 'MINIMUM_DURATION_COMPLETED'}:
        run_state = 'RUNNING' if status.get('monitoring_ok') is True and status.get('monitoring_resumed') is True else 'PAUSED'
    else:
        run_state = 'IDLE' if timer_known else 'AWAITING_HOST'

    percent = _number(observed_hardware.get('battery_percent'), maximum=100)
    charging = _boolean(observed_hardware.get('charging'))
    if charging is True:
        power = 'FULL' if percent == 100 else 'AC_CHARGING'
    elif charging is False:
        power = 'BATTERY_DISCHARGING'
    else:
        power = 'UNKNOWN'

    image = _frame(hardware, unverified)
    live_frame = image is not None
    face_verified = live_frame and observed_hardware.get('face_tracking') == 'VERIFIED'
    face = True if face_verified else False if live_frame and observed_hardware.get('face_present') is False else None
    gaze = 'UNVERIFIED'
    if face_verified and observed_hardware.get('proctor_calibrated') is True:
        # Current proctor reports head pose, not eye-only gaze. Never invent
        # up/down/right measurements it does not provide.
        gaze = 'LEFT' if _number(observed_hardware.get('leftward_seconds'), 0) > 0 else 'CENTER'
    screen_share = _boolean(_observed(observed_status, observed_hardware, 'screen_sharing_active', 'screen_capture_active'))
    screen_proctor = _boolean(_observed(observed_status, observed_hardware, 'screen_proctoring_active', 'screen_monitoring_active'))
    speaker = _boolean(observed_hardware.get('speaker_muted'))
    bluetooth_known = observed_hardware.get('audio_detection_known') is True
    bluetooth_connected = _boolean(observed_hardware.get('bluetooth_headphone_connected'))
    adapter = _observed(observed_status, observed_hardware, 'bluetooth_adapter_state', 'adapter_state')
    adapter = adapter if adapter in {'ON', 'OFF', 'RESTRICTED'} else 'AWAITING_HOST'
    peripherals = []
    if bluetooth_connected is True:
        peripherals.append(dict(id='host-detected-audio', name=_text(observed_hardware.get('prohibited_audio_name'), 'Bluetooth audio device'),
                                category='AUDIO_HEADSET', isAllowed=False, connected=True, rssiDbm=None))

    def device(platform, label, key):
        connected = _boolean(_observed(observed_status, observed_hardware, key))
        if platform == 'PC':
            connected = _boolean(_observed(observed_status, observed_hardware, 'windows_connected', 'pc_connected', 'host_connected'))
        return dict(platform=platform, label=label,
                    status=('HOST' if platform == 'PC' else 'CONNECTED') if connected is True else
                           'DISCONNECTED' if connected is False else 'AWAITING_HOST',
                    deviceName=_text(_observed(observed_status, observed_hardware, key.replace('_connected', '_device_name'))),
                    isVerified=connected is True, batteryPct=percent if platform == 'PC' else None,
                    lastHeartbeatMs=None, lockdownActive=False)

    devices = dict(PC=device('PC', 'PC', 'windows_connected'),
                   ANDROID=device('ANDROID', 'Android', 'android_connected'),
                   IOS=device('IOS', 'iOS', 'ios_connected'))
    browser_ready = not unverified and _observed(status, hardware, 'browser_restriction_verified') is True
    integrity=[] if unverified else [dict(event) for event in status.get('integrity_events',[]) if isinstance(event,dict)]
    integrity_total=0 if unverified else sum(int(_number(value,0)) for value in status.get('integrity_counts',{}).values())
    return {
        'meta': dict(sessionId=_text(status.get('session_id'), 'OFFLINE-PREVIEW' if offline else 'AWAITING-HOST', 128),
                     environmentMode=mode, hostBridgeConnected=True, hostApplicationVersion=None,
                     currentTimeFormatted=datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%I:%M %p, %a %b %d, %Y')),
        'timer': dict(providerStatus='CONNECTED' if timer_known else 'AWAITING_HOST', runState=run_state,
                      remainingSeconds=remaining or 0, totalDurationSeconds=total or 0, elapsedSeconds=elapsed,
                      allowUserPause=False, sessionStartedAtIso=_text(status.get('started_at')),
                      targetEndAtIso=_text(status.get('target_end_at'))),
        'battery': dict(providerStatus='CONNECTED' if percent is not None else 'AWAITING_HOST', percentage=percent,
                        isCharging=charging, powerSource=power, estimatedMinutesRemaining=None, powerSavingModeActive=False),
        'camera': dict(providerStatus='CONNECTED' if live_frame else 'AWAITING_HOST',
                       isConnected=live_frame, isStreaming=live_frame, sourceType='HOST_NATIVE_STREAM' if live_frame else 'NONE',
                       deviceName=_text(observed_hardware.get('camera_name')), resolution=_text(observed_hardware.get('camera_resolution')),
                       fps=_number(observed_hardware.get('camera_fps')), faceDetected=face, faceConfidencePct=None,
                       gazeDirection=gaze, isPrivacyMasked=False, externalFrameDataUrl=image),
        'screenCapture': dict(providerStatus='CONNECTED' if screen_share is not None else 'AWAITING_HOST',
                              screenShareActive=screen_share, displayCount=_number(observed_hardware.get('display_count')),
                              primaryResolution=_text(observed_hardware.get('primary_resolution')), captureFps=None,
                              multiMonitorBlocked=observed_hardware.get('multi_monitor_blocked') is True,
                              hardwareAcceleration=observed_hardware.get('hardware_acceleration') is True,
                              lastCaptureTimestampIso=_text(observed_hardware.get('last_capture_timestamp'))),
        'proctoring': dict(providerStatus='CONNECTED' if not unverified and 'monitoring_ok' in hardware else 'AWAITING_HOST',
                           monitoringActive=not unverified and observed_hardware.get('monitoring_ok') is True,
                           screenProctoringEnabled=screen_proctor, audioState='MUTED' if speaker is True else 'DISABLED' if speaker is False else 'UNVERIFIED',
                           ambientNoiseDb=None, violationCount=int(_number(observed_hardware.get('leftward_violations'), 0)),
                           maxAllowedViolations=int(_number(observed_hardware.get('max_leftward_violations'), 15)),
                           violations=integrity,totalViolationCount=integrity_total+int(_number(observed_hardware.get('leftward_violations'),0))+int(_number(observed_hardware.get('headphone_violations'),0)),
                           activeProcessGuard=observed_hardware.get('process_guard_active') is True, flaggedProcesses=[]),
        'bluetooth': dict(providerStatus='CONNECTED' if bluetooth_known else 'AWAITING_HOST', adapterState=adapter,
                          wirelessAudioBlocked=False, devices=peripherals),
        'deviceConnections': dict(providerStatus='CONNECTED' if any(d['isVerified'] for d in devices.values()) else 'AWAITING_HOST', devices=devices),
        'browserRestrictions': dict(providerStatus='CONNECTED' if browser_ready else 'AWAITING_HOST',
                                    allowedDomains=['chatgpt.com', 'leetcode.com'], focusLabel='DSA + Practice',
                                    sessionProfileLabel='Study Mode (DSA + Practice)', blockExternalClipboard=False,
                                    blockDevToolsShortcut=browser_ready, blockFileDownloads=False, navigationAuditLog=[]),
    }


def runtime_payload(status, hardware, offline=False, browser=None):
    """The host's RUNTIME_STATE envelope, without giving UI policy authority."""
    status = status if isinstance(status, dict) else {}
    hardware = hardware if isinstance(hardware, dict) else {}
    unverified = _unverified(status, hardware, offline)
    ios_camera, ios_camera_preview = _ios_camera(hardware, unverified)
    # Offline samples cannot leak mock device/image positives through raw fields
    # to a detailed UI component that does not consume the typed snapshot yet.
    observed = {} if unverified else deepcopy(hardware)
    if not unverified:
        observed['ios_camera'] = ios_camera
        observed['ios_camera_preview'] = ios_camera_preview
    return dict(snapshot=frontend_snapshot(status, hardware, offline), status=deepcopy(status),
                hardware=observed, offline=unverified, ios_camera=ios_camera, ios_camera_preview=ios_camera_preview,
                browser=deepcopy(browser) if isinstance(browser, dict) else {},
                endEnabled=end_enabled(status, hardware, offline))
