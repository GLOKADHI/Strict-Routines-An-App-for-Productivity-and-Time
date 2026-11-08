"""
Read-only readiness from decoded receiver frames, never browser claims.

Maintains two strictly independent iOS proctoring sources:
  1. ``IOS_FRONT_CAMERA`` — Front camera providing face, gaze, and head-pose monitoring.
  2. ``IOS_FULL_SCREEN_SHARE`` — Full-device iPad screen sharing providing visibility into screen activity.

Front-camera frames never satisfy ``IOS_FULL_SCREEN_SHARE``, and screen-share frames never satisfy
``IOS_FRONT_CAMERA``. Both sources must be independently verified for Strict Session prerequisites.
"""
import math
import time

from windows.strict_ipad_camera_monitor import IPadCameraState, DualInitialStatus


def camera_readiness(gateway, account, preparation_id, *, now=None):
    """
    Returns a dict describing both ``IOS_FRONT_CAMERA`` and ``IOS_FULL_SCREEN_SHARE`` states.
    """
    screen_telemetry = {}
    try:
        from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
        screen_telemetry = IPadScreenShareMonitor.get_instance().get_telemetry()
    except Exception:
        screen_telemetry = {
            "source": "IOS_FULL_SCREEN_SHARE",
            "prerequisite_status": "WAITING_FOR_SCREEN_SHARE",
            "initial_verified": False,
            "verified": False,
            "negotiating": False,
            "negotiation_remaining_sec": 0,
            "negotiation_countdown_label": "Tap Share iPad Screen on the companion page (60s negotiation grace period).",
            "failed": False,
            "failure_reason": None,
        }

    screen_verified = bool(screen_telemetry.get("initial_verified") and screen_telemetry.get("is_streaming"))

    result = dict(
        ready=False,
        front_camera_ready=False,
        front_camera_verified=False,
        screen_share_verified=screen_verified,
        screen_share_status=screen_telemetry.get("prerequisite_status", "WAITING_FOR_SCREEN_SHARE"),
        screen_share_negotiating=bool(screen_telemetry.get("negotiating", False)),
        screen_share_remaining_sec=int(screen_telemetry.get("negotiation_remaining_sec", 0) or 0),
        screen_share_countdown_label=screen_telemetry.get("negotiation_countdown_label", ""),
        screen_share_failed=bool(screen_telemetry.get("failed", False)),
        screen_share_failure_reason=screen_telemetry.get("failure_reason"),
        screen_share=screen_telemetry,
        both_sources_verified=False,
        initial_check_passed=False,
        initial_verified=False,
        initial_verification_record=None,
        initial_check_state='IOS_CAMERA_INITIAL_CHECK: PENDING',
        initializing=False,
        within_grace=False,
        device_connected=False,
        state='IOS_CAMERA_STOPPED',
        frames_received=0,
        elapsed_no_frames=0.0,
        message='Open the secure iOS timer, tap Start Camera, and tap Share iPad Screen.',
        camera={},
        ipad_camera_diagnostics={},
    )

    if not gateway or gateway.ready is not True:
        result['initial_check_state'] = 'IOS_CAMERA_INITIAL_CHECK: FAILED'
        result['message'] = getattr(gateway, 'error', '') or 'Verified iOS camera HTTPS gateway is required.'
        return result

    try:
        incoming = gateway.preview(account, preparation_id)
        status, preview = incoming['status'], incoming['preview']
        ipad_diag = incoming.get('ipad_camera_diagnostics') or {}
        init_prereq = incoming.get('initial_prerequisite') or {}
        if incoming.get('ipad_screen_share'):
            screen_telemetry = incoming['ipad_screen_share']
            screen_verified = bool(screen_telemetry.get("initial_verified") and screen_telemetry.get("is_streaming"))

        result.update(
            state=status.get('state', 'IOS_CAMERA_STOPPED'),
            camera=status,
            ipad_camera_diagnostics=ipad_diag,
            screen_share=screen_telemetry,
            screen_share_verified=screen_verified,
            screen_share_status=screen_telemetry.get("prerequisite_status", "WAITING_FOR_SCREEN_SHARE"),
            screen_share_negotiating=bool(screen_telemetry.get("negotiating", False)),
            screen_share_remaining_sec=int(screen_telemetry.get("negotiation_remaining_sec", 0) or 0),
            screen_share_countdown_label=screen_telemetry.get("negotiation_countdown_label", ""),
            screen_share_failed=bool(screen_telemetry.get("failed", False)),
            screen_share_failure_reason=screen_telemetry.get("failure_reason"),
        )
        count = status.get('frame_sequence')
        now = time.time() if now is None else now

        t_recover = IPadCameraState.THRESHOLD_RECOVERING
        t_paused = IPadCameraState.THRESHOLD_TEMP_PAUSED
        t_degraded = IPadCameraState.THRESHOLD_DEGRADED

        def fresh(value):
            return (isinstance(value, (float, int))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and 0 <= now - value < 3)

        device_connected = bool(
            status.get('state') not in ('IOS_CAMERA_STOPPED', 'IOS_CAMERA_REVOKED', 'IOS_INVITE_EXPIRED', None, '')
        )
        result['device_connected'] = device_connected

        # Source 1: IOS_FRONT_CAMERA verification
        has_valid_frame_count = isinstance(count, int) and not isinstance(count, bool) and count > 0
        has_valid_source = preview.get('source') in ('iPhone camera', 'IOS_FRONT_CAMERA', None, '')
        initial_check_passed = bool(
            device_connected
            and has_valid_frame_count
            and has_valid_source
            and (
                bool(preview.get('jpeg'))
                or isinstance(status.get('last_frame_at'), (int, float))
                or init_prereq.get('initial_check_passed') is True
            )
        )
        initial_verified = bool(
            init_prereq.get('initial_verified')
            or initial_check_passed
        )
        if initial_verified:
            initial_check_passed = True
            initial_check_state = DualInitialStatus('IOS_CAMERA_INITIAL_VERIFIED')
        elif status.get('state') in ('IOS_CAMERA_REVOKED', 'IOS_INVITE_EXPIRED'):
            initial_check_state = 'IOS_CAMERA_INITIAL_CHECK: FAILED'
        else:
            initial_check_state = 'IOS_CAMERA_INITIAL_CHECK: PENDING'

        front_ready = bool(
            status.get('streaming') is True
            and status.get('state') == 'IOS_CAMERA_STREAMING'
            and has_valid_frame_count
            and fresh(status.get('last_frame_at'))
            and fresh(preview.get('observed_at'))
            and preview.get('source') in ('iPhone camera', 'IOS_FRONT_CAMERA')
            and isinstance(preview.get('jpeg'), str)
            and bool(preview['jpeg'])
        )

        last_frame_at = status.get('last_frame_at')
        if isinstance(last_frame_at, (float, int)) and not isinstance(last_frame_at, bool) and math.isfinite(last_frame_at):
            elapsed = max(0.0, round(now - last_frame_at, 2))
        else:
            elapsed = 0.0
        result['elapsed_no_frames'] = elapsed

        rx_fps = preview.get('received_fps', ipad_diag.get('received_fps', 0.0))
        proc_fps = preview.get('processing_fps', ipad_diag.get('processing_fps', 0.0))
        diag_state = ipad_diag.get('health_state')

        if diag_state == IPadCameraState.IOS_PROCESSING_STALLED or (
            front_ready and isinstance(rx_fps, (int, float)) and rx_fps > 0 and proc_fps == 0.0 and count and count > 5
        ):
            health_state = IPadCameraState.IOS_PROCESSING_STALLED
            front_message = 'VIDEO RECEIVING – PROCTORING PROCESSING STALLED. Recovering inference worker.'
            within_grace = True
        elif front_ready:
            health_state = 'VIDEO_FRAMES_RECEIVED'
            front_message = 'Windows is receiving fresh iOS front-camera frames.'
            within_grace = True
        elif device_connected and not has_valid_frame_count:
            health_state = 'CAMERA_INITIALIZING'
            front_message = 'iPad camera connected – waiting for first video frame to complete initial check.'
            within_grace = True
        elif device_connected and elapsed <= t_recover:
            health_state = 'CAMERA_RECOVERING'
            front_message = 'iPad camera connected – active / temporary gap (0–5s).'
            within_grace = True
        elif device_connected and elapsed <= t_paused:
            health_state = 'CAMERA_TEMPORARILY_PAUSED'
            front_message = f'iPad camera temporarily paused ({int(elapsed)}s). Session continues.'
            within_grace = True
        elif device_connected and elapsed <= t_degraded:
            health_state = 'CAMERA_DEGRADED'
            front_message = f'iPad camera degraded ({int(elapsed)}s without frames). Attempting recovery.'
            within_grace = True
        elif device_connected:
            health_state = 'CAMERA_INTERRUPTED'
            front_message = f'iPad camera interrupted ({int(elapsed)}s > 15s). Session flagged. Attempting recovery.'
            within_grace = False
        else:
            health_state = 'IOS_CAMERA_STOPPED'
            front_message = 'iOS front camera required: tap Start Camera and allow access.'
            within_grace = False

        both_verified = bool(initial_verified and screen_verified)
        if not initial_verified:
            overall_message = front_message
        elif not screen_verified:
            if screen_telemetry.get("failed"):
                overall_message = screen_telemetry.get("failure_reason") or "iOS Full-Screen Share failed (60s timeout). Retry Screen Share."
            elif screen_telemetry.get("negotiating"):
                overall_message = f"SCREEN SHARE NEGOTIATING · {screen_telemetry.get('negotiation_countdown_label', '')}"
            else:
                overall_message = "iOS Front Camera verified. Mandatory iOS Full-Screen Share required: tap Share iPad Screen."
        else:
            overall_message = "iOS Front Camera and Full-Screen Share verified (IOS_SCREEN_SHARE: VERIFIED)."

        initializing = bool(device_connected and initial_check_passed and not front_ready)
        result.update(
            ready=front_ready,
            front_camera_ready=front_ready,
            front_camera_verified=initial_verified,
            screen_share_verified=screen_verified,
            both_sources_verified=both_verified,
            initial_check_passed=initial_check_passed,
            initial_verified=initial_verified,
            initial_verification_record=init_prereq.get('initial_verification_record'),
            initial_check_state=initial_check_state,
            initializing=initializing,
            within_grace=within_grace,
            state=health_state,
            frames_received=count if isinstance(count, int) and not isinstance(count, bool) else 0,
            message=overall_message,
            front_camera_message=front_message,
        )

    except Exception:
        result['message'] = 'iOS camera status is unverified. Reconnect the current secure timer page.'

    return result
