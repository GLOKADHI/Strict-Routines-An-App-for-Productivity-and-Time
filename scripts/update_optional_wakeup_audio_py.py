import sys

# 1. Update core/daily_routine_gate.py
gate_path = 'core/daily_routine_gate.py'
with open(gate_path, 'r', encoding='utf-8') as f:
    gate_content = f.read()

target_hw = '''    @staticmethod
    def hardware_requirements_met(photo_liveness_passed, earbuds_route_passed, full_track_passed) -> bool:
        """The mandatory production rule: all three requirements, no exceptions."""
        return bool(photo_liveness_passed) and bool(earbuds_route_passed) and bool(full_track_passed)'''

replacement_hw = '''    @staticmethod
    def hardware_requirements_met(photo_liveness_passed, earbuds_route_passed=None, full_track_passed=None) -> bool:
        """Camera face liveness is mandatory. Earbuds route and audio track playback are optional."""
        return bool(photo_liveness_passed)'''

assert target_hw in gate_content, "target_hw not found in daily_routine_gate.py"
gate_content = gate_content.replace(target_hw, replacement_hw)

target_ev = 'if not evidence_id or not (camera and audio and full_track):'
replacement_ev = 'if not evidence_id or not camera:'
assert target_ev in gate_content, "target_ev not found in daily_routine_gate.py"
gate_content = gate_content.replace(target_ev, replacement_ev)

with open(gate_path, 'w', encoding='utf-8') as f:
    f.write(gate_content)
print("Updated core/daily_routine_gate.py")

# 2. Update core/wakeup_attempt_validation.py
val_path = 'core/wakeup_attempt_validation.py'
with open(val_path, 'r', encoding='utf-8') as f:
    val_content = f.read()

target_attempt = '''    if not all(payload.get(k) is True for k in ("photo_liveness_passed", "earbuds_route_passed", "active_audio_output_verified", "full_track_passed")):
        return "MANDATORY_STEP_INCOMPLETE"
    live, playback = payload.get("liveness_metadata", {}), payload.get("playback_session", {})
    if not isinstance(live, dict) or not isinstance(playback, dict):
        return "INVALID_MEASUREMENTS"
    error = camera_policy_error(live) or playback_policy_error(playback, live, payload.get("attempt_id"))'''

replacement_attempt = '''    if not payload.get("photo_liveness_passed"):
        return "MANDATORY_STEP_INCOMPLETE"
    live = payload.get("liveness_metadata", {})
    if not isinstance(live, dict):
        return "INVALID_MEASUREMENTS"
    error = camera_policy_error(live)
    playback = payload.get("playback_session")
    if not error and isinstance(playback, dict) and playback.get("completed"):
        error = playback_policy_error(playback, live, payload.get("attempt_id"))'''

assert target_attempt in val_content, "target_attempt not found in wakeup_attempt_validation.py"
val_content = val_content.replace(target_attempt, replacement_attempt)

with open(val_path, 'w', encoding='utf-8') as f:
    f.write(val_content)
print("Updated core/wakeup_attempt_validation.py")
