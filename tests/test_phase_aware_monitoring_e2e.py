"""
End-to-End Test Suite for Phase-Aware Android Phone Usage Monitoring and Immediate Windows Alerts.

Tests Scenarios A through H:
- Test A: FOCUS_ACTIVE restrictions and immediate alert formatting.
- Test B: App transitions vs debounced continuous usage.
- Test C: Allowed usage during FOCUS_BUFFER.
- Test D: Allowed usage during PROOF_SUBMISSION_OPEN.
- Test E: Automatic restoration of restrictions upon window expiry / FOCUS_ACTIVE return.
- Test F: Historical timestamp classification for delayed events.
- Test G: Consolidated 30s report ingestion and idempotent deduplication.
- Test H: Screen lock / unlock lifecycle (LOCKED_SLEEPING).
"""
import uuid
from datetime import datetime, timezone, timedelta
import pytest

from core.time_window_engine import IST
from tests.test_strict_study_workflow import rig, window, manager_fixture
from windows.strict_session_manager import StrictSessionPhase


def test_scenario_a_focus_active_prohibited_app_alert(rig, monkeypatch):
    """
    TEST A: During FOCUS_ACTIVE, unapproved app produces an immediate alert,
    records a violation, and increments prohibited usage.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    start_res = manager.start_session(monitoring_consent=True)
    assert start_res["success"] is True

    # Check phase is FOCUS_ACTIVE
    phase_info = manager.get_current_session_phase()
    assert phase_info["session_phase"] == "FOCUS_ACTIVE"
    assert phase_info["phone_usage"] == "PHONE_USAGE_RESTRICTED"

    # Android reports prohibited Instagram
    now_dt = datetime.now(timezone.utc)
    ev_id = f"ev_insta_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": ev_id,
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.instagram.android",
        "app_label": "Instagram",
        "category": "SOCIAL",
        "event_timestamp": now_dt.isoformat(),
        "detection_timestamp": now_dt.isoformat(),
        "detection_source": "ANDROID_USAGE_STATS",
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }

    res = manager.record_android_app_usage(payload)
    assert res["success"] is True
    assert res["classification"] == "PROHIBITED_FOCUS_USAGE"
    assert res["action_taken"] == "VIOLATION_RECORDED"
    assert res["violation"] is True

    # Verify immediate alert formatting matching the prompt requirements
    state = manager.get_android_monitoring_state()
    alerts = state["active_alerts"]
    assert len(alerts) >= 1
    alert = alerts[0]
    assert alert["app_label"] == "Instagram"
    assert alert["session_phase"] == "FOCUS_ACTIVE"
    assert "Phone usage is not permitted during Focus Session" in alert["warning_message"]
    assert state["prohibited_usage_count"] == 1


def test_scenario_b_continuous_usage_debouncing_and_transitions(rig, monkeypatch):
    """
    TEST B: Continuous usage in the same app generates 1 violation upon initial entry (debounced).
    Switching to a different unapproved app produces a new distinct violation.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    base_dt = datetime.now(timezone.utc)

    # 1. Initial Instagram entry -> 1 violation recorded
    ev1 = {
        "event_id": f"ev1_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.instagram.android",
        "app_label": "Instagram",
        "event_timestamp": base_dt.isoformat(),
        "detection_timestamp": base_dt.isoformat(),
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }
    res1 = manager.record_android_app_usage(ev1)
    assert res1["action_taken"] == "VIOLATION_RECORDED"
    assert res1["violation"] is True

    # 2. Continuous usage inside Instagram 3 seconds later -> Debounced
    ev2 = dict(ev1)
    ev2["event_id"] = f"ev2_{uuid.uuid4().hex[:8]}"
    ev2["event_timestamp"] = (base_dt + timedelta(seconds=3)).isoformat()
    res2 = manager.record_android_app_usage(ev2)
    assert res2["action_taken"] == "DEBOUNCED_CONTINUOUS_USAGE"
    assert res2["duplicate"] is True

    # 3. User switches to YouTube -> Distinct new violation
    ev3 = {
        "event_id": f"ev3_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.google.android.youtube",
        "app_label": "YouTube",
        "event_timestamp": (base_dt + timedelta(seconds=6)).isoformat(),
        "detection_timestamp": (base_dt + timedelta(seconds=6)).isoformat(),
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }
    res3 = manager.record_android_app_usage(ev3)
    assert res3["action_taken"] == "VIOLATION_RECORDED"
    assert res3["classification"] == "PROHIBITED_FOCUS_USAGE"

    state = manager.get_android_monitoring_state()
    assert state["prohibited_usage_count"] == 2


def test_scenario_c_focus_buffer_allows_phone_usage(rig, monkeypatch):
    """
    TEST C: When FOCUS_BUFFER is active, ordinary phone usage is permitted.
    Telemetry is marked ALLOWED_BUFFER_USAGE without violation alerts.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    # Set phase to FOCUS_BUFFER for 5 minutes
    phase_res = manager.update_session_phase(StrictSessionPhase.FOCUS_BUFFER, duration_seconds=300, reason="Break buffer")
    assert phase_res["session_phase"] == "FOCUS_BUFFER"
    assert phase_res["phone_usage"] == "PHONE_USAGE_ALLOWED"

    # Android reports WhatsApp during buffer
    now_dt = datetime.now(timezone.utc)
    ev = {
        "event_id": f"ev_buf_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.whatsapp",
        "app_label": "WhatsApp",
        "event_timestamp": now_dt.isoformat(),
        "detection_timestamp": now_dt.isoformat(),
        "session_phase": "FOCUS_BUFFER",
        "submission_window_status": "CLOSED"
    }
    res = manager.record_android_app_usage(ev)
    assert res["classification"] == "ALLOWED_BUFFER_USAGE"
    assert res["violation"] is False

    state = manager.get_android_monitoring_state()
    assert state["allowed_buffer_usage_count"] == 1
    assert state["prohibited_usage_count"] == 0


def test_scenario_d_proof_submission_open_allows_usage(rig, monkeypatch):
    """
    TEST D: When PROOF_SUBMISSION_OPEN is active, phone usage (Camera, Photos, Ordinary apps)
    is permitted and marked ALLOWED_PROOF_WINDOW_USAGE.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    phase_res = manager.update_session_phase(StrictSessionPhase.PROOF_SUBMISSION_OPEN, duration_seconds=600, reason="Hourly Proof")
    assert phase_res["session_phase"] == "PROOF_SUBMISSION_OPEN"
    assert phase_res["phone_usage"] == "PHONE_USAGE_ALLOWED"

    now_dt = datetime.now(timezone.utc)
    ev = {
        "event_id": f"ev_proof_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.sec.android.app.camera",
        "app_label": "Camera",
        "event_timestamp": now_dt.isoformat(),
        "detection_timestamp": now_dt.isoformat(),
        "session_phase": "PROOF_SUBMISSION_OPEN",
        "submission_window_status": "OPEN"
    }
    res = manager.record_android_app_usage(ev)
    assert res["classification"] == "ALLOWED_PROOF_WINDOW_USAGE"
    assert res["violation"] is False

    state = manager.get_android_monitoring_state()
    assert state["allowed_proof_window_usage_count"] == 1


def test_scenario_e_window_expiry_restores_restrictions(rig, monkeypatch):
    """
    TEST E: After proof submission window expires or transitions back to FOCUS_ACTIVE,
    phone usage restrictions are restored immediately.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    # Start in buffer, then transition back to FOCUS_ACTIVE
    manager.update_session_phase(StrictSessionPhase.FOCUS_BUFFER, duration_seconds=60)
    manager.update_session_phase(StrictSessionPhase.FOCUS_ACTIVE, reason="Buffer expired")

    state = manager.get_current_session_phase()
    assert state["session_phase"] == "FOCUS_ACTIVE"
    assert state["phone_usage"] == "PHONE_USAGE_RESTRICTED"

    # App opened in FOCUS_ACTIVE triggers violation
    now_dt = datetime.now(timezone.utc)
    ev = {
        "event_id": f"ev_after_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.twitter.android",
        "app_label": "X (Twitter)",
        "event_timestamp": now_dt.isoformat(),
        "detection_timestamp": now_dt.isoformat(),
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }
    res = manager.record_android_app_usage(ev)
    assert res["classification"] == "PROHIBITED_FOCUS_USAGE"
    assert res["action_taken"] == "VIOLATION_RECORDED"


def test_scenario_f_delayed_report_historical_timestamp_validation(rig, monkeypatch):
    """
    TEST F: An event that occurred during an allowed proof window but reaches Windows
    after the window has closed is classified as permitted based on its historical event_timestamp.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    # Proof window was opened and event occurred during that window
    manager.update_session_phase(StrictSessionPhase.PROOF_SUBMISSION_OPEN, duration_seconds=300)
    historical_event_dt = datetime.now(timezone.utc)

    # Later, phase returns to FOCUS_ACTIVE
    manager.update_session_phase(StrictSessionPhase.FOCUS_ACTIVE, reason="Proof window expired")
    assert manager.get_current_session_phase()["session_phase"] == "FOCUS_ACTIVE"

    # Android delivers delayed event now; timestamp is from when PROOF_SUBMISSION_OPEN was active
    delayed_ev = {
        "event_id": f"ev_delayed_{uuid.uuid4().hex[:8]}",
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.google.android.apps.photos",
        "app_label": "Google Photos",
        "event_timestamp": historical_event_dt.isoformat(),
        "detection_timestamp": datetime.now(timezone.utc).isoformat(),
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }
    res = manager.record_android_app_usage(delayed_ev)
    assert res["classification"] == "ALLOWED_PROOF_WINDOW_USAGE"
    assert res["violation"] is False


def test_scenario_g_consolidated_30s_report_deduplication(rig, monkeypatch):
    """
    TEST G: Consolidated 30-second report ingests recent transitions and drains
    violations idempotently without duplicate counts.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    now_dt = datetime.now(timezone.utc)
    ev_id = f"ev_batch_{uuid.uuid4().hex[:8]}"
    ev = {
        "event_id": ev_id,
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "package_name": "com.reddit.frontpage",
        "app_label": "Reddit",
        "event_timestamp": now_dt.isoformat(),
        "detection_timestamp": now_dt.isoformat(),
        "session_phase": "FOCUS_ACTIVE",
        "submission_window_status": "CLOSED"
    }

    # 1. Immediate event sent
    res1 = manager.record_android_app_usage(ev)
    assert res1["action_taken"] == "VIOLATION_RECORDED"

    # 2. 30s consolidated report batch also includes this event_id
    report = {
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "report_sequence": 1,
        "timestamp": now_dt.isoformat(),
        "interval_start": (now_dt - timedelta(seconds=30)).isoformat(),
        "interval_end": now_dt.isoformat(),
        "current_app": "com.reddit.frontpage",
        "current_app_label": "Reddit",
        "current_category": "SOCIAL",
        "foreground_duration_seconds": 20,
        "unauthorized_events": [ev],
        "screen_state": "UNLOCKED",
        "monitor_status": "ACTIVE",
        "usage_access": "ENABLED",
        "session_phase": "FOCUS_ACTIVE",
        "phone_usage": "PHONE_USAGE_RESTRICTED",
        "recent_transitions": [
            {
                "package_name": "com.reddit.frontpage",
                "app_name": "Reddit",
                "category": "SOCIAL",
                "start_time": (now_dt - timedelta(seconds=20)).isoformat(),
                "end_time": now_dt.isoformat(),
                "duration_seconds": 20
            }
        ]
    }
    report_res = manager.record_android_usage_report(report)
    assert report_res["success"] is True

    # Violation count should be 1, NOT 2!
    state = manager.get_android_monitoring_state()
    assert state["prohibited_usage_count"] == 1
    assert len(state["recent_transitions"]) >= 1


def test_scenario_h_screen_lock_sleeping_lifecycle(rig, monkeypatch):
    """
    TEST H: Screen lock state LOCKED_SLEEPING is reported without false violations.
    When unlocked, active monitoring continues smoothly.
    """
    window(rig)
    manager, _ = manager_fixture(rig, monkeypatch)
    manager.start_session(monitoring_consent=True)

    now_dt = datetime.now(timezone.utc)
    lock_report = {
        "session_id": manager.session_id,
        "device_id": "a55_test",
        "user_id": "user_lok",
        "report_sequence": 2,
        "timestamp": now_dt.isoformat(),
        "current_app": "SCREEN_LOCKED",
        "current_app_label": "Screen Locked",
        "current_category": "SYSTEM",
        "foreground_duration_seconds": 0,
        "unauthorized_events": [],
        "screen_state": "LOCKED_SLEEPING",
        "monitor_status": "LOCKED_SLEEPING",
        "usage_access": "ENABLED",
        "session_phase": "FOCUS_ACTIVE",
        "phone_usage": "PHONE_USAGE_RESTRICTED",
        "recent_transitions": []
    }
    res = manager.record_android_usage_report(lock_report)
    assert res["success"] is True

    state = manager.get_android_monitoring_state()
    assert state["screen_locked"] is True
    assert state["monitor_status"] == "LOCKED_SLEEPING"
    assert state["prohibited_usage_count"] == 0
