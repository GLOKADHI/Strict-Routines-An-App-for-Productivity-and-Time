"""Validate measured Android wake-up attempts; never import client point claims."""
import hashlib
import math
from pathlib import Path
import wave
from core.window_state import WAKEUP_WINDOW_END


def configured_track_manifest():
    path = Path(__file__).resolve().parent.parent / "data" / "motivation_track.wav"
    with wave.open(str(path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "duration_sec": duration}


def camera_policy_error(live):
    try:
        valid = int(live["valid_samples"])
        total = int(live["total_samples"])
        duration = int(live["valid_duration_ms"])
        first, last, accepted = (int(live[k]) for k in ("first_frame_elapsed_ms", "last_frame_elapsed_ms", "accepted_elapsed_ms"))
        ratio = valid / total if total > 0 else 0
        claimed = float(live["valid_sample_ratio"])
        if live.get("policy_version") != 2 or live.get("session_state") != "PASSED":
            return "CAMERA_POLICY_VERSION"
        if duration < 20000 or duration != int(live["duration_ms"]) or valid < 40 or valid > total or int(live["unique_frames"]) != total:
            return "CAMERA_OBSERVATION_INCOMPLETE"
        if ratio < .70 or not math.isfinite(claimed) or abs(claimed - ratio) > .000001:
            return "CAMERA_SAMPLE_RATIO"
        if first <= 0 or last <= first or not duration <= last - first <= 180000 or not 0 <= accepted - last <= 750:
            return "CAMERA_TIMESTAMPS_INVALID"
        if not all(live.get(k) is True for k in ("final_frame_valid", "stable_face", "eyes_detected", "lighting_ok", "head_pose_ok")):
            return "CAMERA_FINAL_QUALITY"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "CAMERA_METADATA_MISSING"
    return None


def playback_policy_error(playback, live, attempt_id):
    if not attempt_id or live.get("attempt_id") != attempt_id or playback.get("attempt_id") != attempt_id:
        return "ATTEMPT_MISMATCH"
    if playback.get("route_source") != "MEDIA_PLAYER_ROUTED_DEVICE" or playback.get("audio_route") not in ("BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET"):
        return "AUDIO_ROUTE_UNVERIFIED"
    selected = str(playback.get("selected_device_name") or "").strip()
    selected_address = str(playback.get("selected_device_address") or "").strip()
    route_name = str(playback.get("routed_device_name") or "").strip()
    route_address = str(playback.get("routed_device_address") or "").strip()
    if not selected or selected.lower() in ("none configured", "any bluetooth audio output"):
        return "EARBUDS_NOT_SELECTED"
    if (selected_address and selected_address.lower() != route_address.lower()) or (not selected_address and selected.lower() != route_name.lower()):
        return "SELECTED_EARBUDS_MISMATCH"
    try:
        manifest = configured_track_manifest()
        duration = float(playback["track_duration_sec"])
        listened = float(playback["completed_duration_sec"])
        if not math.isfinite(duration) or playback.get("track_sha256") != manifest["sha256"] or abs(duration - manifest["duration_sec"]) > .001:
            return "CONFIGURED_TRACK_MISMATCH"
        if not math.isfinite(listened) or listened + .001 < duration or int(playback.get("route_verified_duration_ms", 0)) < round(duration * 1000):
            return "FULL_TRACK_INCOMPLETE"
        if int(playback.get("seek_attempts", -1)) != 0 or int(playback.get("route_observations", 0)) < 5:
            return "PLAYBACK_EVIDENCE_INCOMPLETE"
        if not all(playback.get(k) is True for k in ("completed", "headset_connected", "volume_ok")):
            return "PLAYBACK_NOT_COMPLETED"
    except (KeyError, TypeError, ValueError, OverflowError, OSError, wave.Error):
        return "PLAYBACK_METADATA_INVALID"
    return None


def android_attempt_error(payload, *, allow_late_prerequisite=False):
    if payload.get("demo") or payload.get("is_dev_mode"):
        return "DEMO_CANNOT_SUBMIT"
    if not payload.get("photo_liveness_passed"):
        return "MANDATORY_STEP_INCOMPLETE"
    live = payload.get("liveness_metadata", {})
    if not isinstance(live, dict):
        return "INVALID_MEASUREMENTS"
    attempt_id = payload.get("attempt_id")
    if not attempt_id or live.get("attempt_id") != attempt_id:
        return "ATTEMPT_MISMATCH"
    error = camera_policy_error(live)
    playback = payload.get("playback_session")
    if not error and isinstance(playback, dict) and playback.get("attempt_id") and playback.get("attempt_id") != attempt_id:
        error = "ATTEMPT_MISMATCH"
    if not error and (payload.get("earbuds_route_passed") or payload.get("active_audio_output_verified")) and payload.get("full_track_passed"):
        if not isinstance(playback, dict):
            error = "PLAYBACK_METADATA_INVALID"
        else:
            error = playback_policy_error(playback, live, attempt_id)
    if error:
        return error
    from core.time_window_engine import iso_to_dt, IST
    try:
        completed = iso_to_dt(payload["completed_at"]).astimezone(IST)
        if completed.date().isoformat() != payload.get("date"):
            return "WAKEUP_WINDOW_CLOSED"
        if not allow_late_prerequisite and not "07:00" <= completed.strftime("%H:%M") < WAKEUP_WINDOW_END:
            return "WAKEUP_WINDOW_CLOSED"
        if allow_late_prerequisite and completed.strftime("%H:%M") < "07:00":
            return "WAKEUP_WINDOW_CLOSED"
    except (KeyError, TypeError, ValueError):
        return "COMPLETION_TIME_INVALID"
    return None
