"""Comprehensive automated tests for the Final Strict Session Prerequisite Gate (Scenarios A-N)."""

import sqlite3
from unittest.mock import patch
import numpy as np

from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
from windows.strict_external_app_terminator import StrictExternalAppTerminator
from windows.strict_session_preparation import StrictSessionPreparation
from windows.strict_prerequisite_engine import StrictPrerequisiteEngine, PrerequisiteState as CheckStatus


class _InMemoryDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def _connect(self):
        return self.conn


def _make_dummy_frame(val: int = 128) -> np.ndarray:
    arr = np.full((240, 320, 3), val, dtype=np.uint8)
    arr[20:100, 30:150] = (val + 40) % 255
    return arr


def test_scenario_a_ios_screen_share_starts_within_10s():
    """Scenario A: iOS screen share starts within 10s and verifies after 3s of stable frames."""
    mon = IPadScreenShareMonitor()
    t0 = 1000.0
    neg = mon.start_negotiation(preparation_id="prep-A", now_mono=t0, force_restart=True)
    assert neg["state"] == "SCREEN_SHARE_NEGOTIATING"
    assert neg["negotiating"] is True
    assert neg["initial_verified"] is False

    # Frame arrives at t0 + 8s
    mon.start_track(track_id="trk-A", preparation_id="prep-A", now_mono=t0 + 8.0)
    r1 = mon.process_frame(_make_dummy_frame(100), now_mono=t0 + 8.0, source_id="IOS_FULL_SCREEN_SHARE")
    assert r1["accepted"] is True
    assert r1["initial_verified"] is False  # Needs 3s of stable frames

    # At t0 + 9.5s (1.5s of stability) -> still negotiating
    r2 = mon.process_frame(_make_dummy_frame(110), now_mono=t0 + 9.5, source_id="IOS_FULL_SCREEN_SHARE")
    assert r2["initial_verified"] is False

    # At t0 + 11.2s (3.2s of stability, >= 2 frames) -> VERIFIED!
    r3 = mon.process_frame(_make_dummy_frame(120), now_mono=t0 + 11.2, source_id="IOS_FULL_SCREEN_SHARE")
    assert r3["accepted"] is True
    assert r3["initial_verified"] is True
    snap = mon.get_status_snapshot(now_mono=t0 + 11.2)
    assert snap["initial_verified"] is True
    assert snap["is_negotiating"] is False
    assert snap["prerequisite_status"] == "IOS_SCREEN_SHARE: VERIFIED"
    assert snap["is_interrupted"] is False


def test_scenario_b_ios_screen_share_takes_45s_within_60s_grace():
    """Scenario B: iOS screen share takes 45s; stays negotiating with countdown and no false interruption."""
    mon = IPadScreenShareMonitor()
    t0 = 2000.0
    mon.start_negotiation(preparation_id="prep-B", now_mono=t0, force_restart=True)

    # Check at 18s -> 42s remaining, no interruption!
    snap_18 = mon.get_status_snapshot(now_mono=t0 + 18.0)
    assert snap_18["prerequisite_status"] == "SCREEN SHARE NEGOTIATING"
    assert snap_18["is_negotiating"] is True
    assert snap_18["is_interrupted"] is False
    assert snap_18["negotiation_failed"] is False
    assert "00:42" in snap_18["negotiation_countdown_label"]
    assert mon.check_watchdog(now_mono=t0 + 18.0)["interrupted"] is False

    # Check at 44s -> 16s remaining, still no interruption!
    snap_44 = mon.get_status_snapshot(now_mono=t0 + 44.0)
    assert snap_44["prerequisite_status"] == "SCREEN SHARE NEGOTIATING"
    assert snap_44["is_interrupted"] is False
    assert "00:16" in snap_44["negotiation_countdown_label"]

    # Frames start arriving at 45s and remain stable through 48.5s
    mon.start_track(track_id="trk-B", preparation_id="prep-B", now_mono=t0 + 45.0)
    mon.process_frame(_make_dummy_frame(90), now_mono=t0 + 45.0, source_id="IOS_FULL_SCREEN_SHARE")
    mon.process_frame(_make_dummy_frame(100), now_mono=t0 + 46.5, source_id="IOS_FULL_SCREEN_SHARE")
    res = mon.process_frame(_make_dummy_frame(110), now_mono=t0 + 48.5, source_id="IOS_FULL_SCREEN_SHARE")
    assert res["initial_verified"] is True
    snap_done = mon.get_status_snapshot(now_mono=t0 + 48.5)
    assert snap_done["prerequisite_status"] == "IOS_SCREEN_SHARE: VERIFIED"
    assert snap_done["initial_verified"] is True


def test_scenario_c_ios_screen_share_exceeds_60s_timeout():
    """Scenario C: iOS screen share exceeds 60s -> IOS_SCREEN_SHARE_FAILED, no session violation, retry works."""
    mon = IPadScreenShareMonitor()
    t0 = 3000.0
    mon.start_negotiation(preparation_id="prep-C", now_mono=t0, force_restart=True)

    # At 61s without frames -> IOS_SCREEN_SHARE_FAILED
    snap_61 = mon.get_status_snapshot(now_mono=t0 + 61.0)
    assert snap_61["prerequisite_status"] == "IOS_SCREEN_SHARE_FAILED"
    assert snap_61["negotiation_failed"] is True
    assert snap_61["initial_verified"] is False
    assert snap_61["is_interrupted"] is False  # Not an active-session violation
    assert mon.check_watchdog(now_mono=t0 + 61.0)["interrupted"] is False

    # Clicking Retry iOS Screen Share restarts the 60s negotiation window cleanly
    retry_snap = mon.start_negotiation(preparation_id="prep-C", now_mono=t0 + 65.0, force_restart=True)
    assert retry_snap["state"] == "SCREEN_SHARE_NEGOTIATING"
    assert retry_snap["failed"] is False
    assert retry_snap["negotiating"] is True


def test_scenario_d_and_e_independent_ios_front_camera_and_screen_share():
    """Scenarios D & E: Front camera and full-screen share are strictly independent; neither satisfies the other."""
    mon = IPadScreenShareMonitor()
    t0 = 4000.0
    mon.start_negotiation(preparation_id="prep-DE", now_mono=t0, force_restart=True)

    # Scenario D: Attempting to feed IOS_FRONT_CAMERA frames into IPadScreenShareMonitor must be rejected
    rejected = mon.process_frame(_make_dummy_frame(100), now_mono=t0 + 2.0, source_id="IOS_FRONT_CAMERA")
    assert rejected["accepted"] is False
    assert "IOS_FRONT_CAMERA" in rejected["error"]
    assert mon.get_status_snapshot(now_mono=t0 + 5.0)["initial_verified"] is False

    # Verify StrictSessionPreparation blocks when only front camera is verified (Scenario D)
    state_d = {"front_ok": True, "screen_ok": False}

    def cam_obs(account, pid):
        return {
            "camera_required": True,
            "front_camera_verified": state_d["front_ok"],
            "initial_check_passed": state_d["front_ok"],
            "screen_share_verified": state_d["screen_ok"],
            "screen_share_status": "IOS_SCREEN_SHARE: VERIFIED" if state_d["screen_ok"] else "SCREEN SHARE NEGOTIATING",
            "camera_ready": state_d["front_ok"] and state_d["screen_ok"],
            "camera_acceptable": state_d["front_ok"] and state_d["screen_ok"],
        }

    prep = StrictSessionPreparation(
        _InMemoryDB(),
        lambda refresh=False: {"ready": True, "mode": "DEMO", "internet": True, "secure_browser": True, "allowlist_configured": True, "hardware_ready": True},
        camera_observer=cam_obs,
        android_observer=lambda pid, refresh=False: {"ready": True, "status": "ANDROID_READY_ACKNOWLEDGED"},
        browser_observer=lambda pid, refresh=False: {"passed": True, "status": "EXTERNAL_BROWSERS_CLOSED"},
    )
    snap = prep.state("lok")
    prep_id = snap["preparation_id"]
    prep.host_report("lok", prep_id, {"camera": True, "visible": True, "online": True, "secure_context": True, "camera_native_id": "a" * 64})
    inv = prep.invite("lok", "IOS", "https://study.strictroutines.com")["invitation"]
    token = inv["url"].split("#")[1]
    joined = prep.join(token, "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")
    prep.heartbeat(prep_id, "IOS", joined["credential"], {"timer_ready": True, "online": True, "visible": True, "screen_sharing": False})

    # D: Front camera OK, Screen share False -> ready is False
    st_d = prep.state("lok")
    assert st_d["ready"] is False
    assert st_d["final_gate"]["IOS_FRONT_CAMERA_READY"] is True
    assert st_d["final_gate"]["IOS_SCREEN_SHARE_READY"] is False
    try:
        prep.authorize_start("lok", prep_id)
        assert False, "Expected ValueError when screen share is not verified"
    except ValueError:
        pass

    # E: Screen share OK, Front camera False -> ready is False
    state_d["front_ok"] = False
    state_d["screen_ok"] = True
    st_e = prep.state("lok")
    assert st_e["ready"] is False
    assert st_e["final_gate"]["IOS_FRONT_CAMERA_READY"] is False
    assert st_e["final_gate"]["IOS_SCREEN_SHARE_READY"] is True
    try:
        prep.authorize_start("lok", prep_id)
        assert False, "Expected ValueError when front camera is not verified"
    except ValueError:
        pass


def test_scenarios_f_g_h_android_apk_prerequisite_states():
    """Scenarios F, G, H: Android APK disconnected, Usage Access missing, and ANDROID_READY_ACKNOWLEDGED."""
    android_state = {"ready": False, "status": "ANDROID_DISCONNECTED", "message": "Android APK is offline."}

    prep = StrictSessionPreparation(
        _InMemoryDB(),
        lambda refresh=False: {"ready": True, "mode": "DEMO", "internet": True, "secure_browser": True, "allowlist_configured": True, "hardware_ready": True},
        camera_observer=lambda account, pid: {
            "camera_required": True,
            "front_camera_verified": True,
            "initial_check_passed": True,
            "screen_share_verified": True,
            "screen_share_status": "IOS_SCREEN_SHARE: VERIFIED",
            "camera_ready": True,
            "camera_acceptable": True,
        },
        android_observer=lambda pid, refresh=False: dict(android_state),
        browser_observer=lambda pid, refresh=False: {"passed": True, "status": "EXTERNAL_BROWSERS_CLOSED"},
    )
    s0 = prep.state("lok")
    pid = s0["preparation_id"]
    prep.host_report("lok", pid, {"camera": True, "visible": True, "online": True, "secure_context": True, "camera_native_id": "a" * 64})
    inv = prep.invite("lok", "IOS", "https://study.strictroutines.com")["invitation"]
    joined = prep.join(inv["url"].split("#")[1], "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")
    prep.heartbeat(pid, "IOS", joined["credential"], {"timer_ready": True, "online": True, "visible": True, "screen_sharing": True})

    # Scenario F: Android APK disconnected -> blocked
    sf = prep.state("lok")
    assert sf["ready"] is False
    assert sf["final_gate"]["ANDROID_READY"] is False
    assert sf["android_prerequisite"]["status"] == "ANDROID_DISCONNECTED"

    # Scenario G: Android Usage Access permission disabled -> blocked
    android_state.update({"ready": False, "status": "ANDROID_PERMISSION_REQUIRED", "usage_access_granted": False})
    sg = prep.state("lok")
    assert sg["ready"] is False
    assert sg["android_prerequisite"]["status"] == "ANDROID_PERMISSION_REQUIRED"

    # Scenario H: Android APK connected & ready -> ANDROID_READY_ACKNOWLEDGED -> passes!
    android_state.update({
        "ready": True,
        "status": "ANDROID_READY_ACKNOWLEDGED",
        "usage_access_granted": True,
        "monitoring_service_ready": True,
        "connectivity_status": "CONNECTED",
    })
    sh = prep.state("lok")
    assert sh["ready"] is True
    assert sh["final_gate"]["ANDROID_READY"] is True
    auth = prep.authorize_start("lok", pid)
    assert auth["preparation_id"] == pid


def test_scenarios_i_j_k_l_external_browser_cleanup_and_live_recheck():
    """Scenarios I, J, K, L: External browser cleanup with graceful WM_CLOSE, refusal handling, and reopen detection."""
    term = StrictExternalAppTerminator()

    # Scenario I: Chrome and Brave are open, then exit after WM_CLOSE
    scan_calls = {"count": 0}

    def fake_scan_success():
        scan_calls["count"] += 1
        if scan_calls["count"] == 1:
            return {
                "Chrome": {"browser_name": "Chrome", "process_name": "chrome.exe", "pids": [999991], "window_count": 2},
                "Brave": {"browser_name": "Brave", "process_name": "brave.exe", "pids": [999992], "window_count": 1},
            }
        return {}

    with patch.object(term, "_scan_external_browsers_detailed", side_effect=fake_scan_success):
        res_i = term.run_external_browser_cleanup(preparation_id="prep-I", grace_period=0.05)
        assert res_i["passed"] is True
        assert res_i["status"] == "EXTERNAL_BROWSERS_CLOSED"
        assert res_i["per_browser_summary"]["Chrome"] == "Closed – Verified"
        assert res_i["per_browser_summary"]["Brave"] == "Closed – Verified"
        assert res_i["per_browser_summary"]["Edge"] == "Not Running"
        assert res_i["browsers"]["Chrome"]["initial_pids"] == [999991]
        assert res_i["browsers"]["Chrome"]["close_request_timestamp"] is not None
        assert res_i["browsers"]["Chrome"]["actual_exit_timestamp"] is not None

    # Scenario J: Chrome refuses graceful close -> Open – Manual Closure Required
    def fake_scan_refused():
        return {
            "Chrome": {"browser_name": "Chrome", "process_name": "chrome.exe", "pids": [999993], "window_count": 1},
        }

    with patch.object(term, "_scan_external_browsers_detailed", side_effect=fake_scan_refused):
        res_j = term.run_external_browser_cleanup(preparation_id="prep-J", grace_period=0.05)
        assert res_j["passed"] is False
        assert res_j["status"] == "EXTERNAL_BROWSERS_OPEN"
        assert "Open – Manual Closure Required" in res_j["per_browser_summary"]["Chrome"]
        assert "Chrome" in res_j["blocking_browsers"]

    # Scenario K & L: All browsers closed -> PASSED, then user reopens Chrome before clicking Start -> reverts to EXTERNAL_BROWSERS_OPEN
    with patch.object(term, "_scan_external_browsers_detailed", return_value={}):
        res_k = term.verify_external_browsers_closed_live(preparation_id="prep-KL")
        assert res_k["passed"] is True
        assert res_k["status"] == "EXTERNAL_BROWSERS_CLOSED"

    with patch.object(term, "_scan_external_browsers_detailed", side_effect=fake_scan_refused):
        res_l = term.verify_external_browsers_closed_live(preparation_id="prep-KL")
        assert res_l["passed"] is False
        assert res_l["status"] == "EXTERNAL_BROWSERS_OPEN"
        assert "Open" in res_l["per_browser_summary"]["Chrome"]


def test_scenarios_m_and_n_full_gate_and_session_start_transition():
    """Scenarios M & N: All 5 prerequisites pass -> Start enabled -> active session cancels 60s timer & starts 10s watchdog."""
    mon = IPadScreenShareMonitor()
    t0 = 5000.0
    mon.start_negotiation(preparation_id="prep-MN", now_mono=t0, force_restart=True)
    mon.start_track(track_id="trk-MN", preparation_id="prep-MN", now_mono=t0 + 1.0)
    mon.process_frame(_make_dummy_frame(100), now_mono=t0 + 1.0, source_id="IOS_FULL_SCREEN_SHARE")
    mon.process_frame(_make_dummy_frame(110), now_mono=t0 + 2.5, source_id="IOS_FULL_SCREEN_SHARE")
    mon.process_frame(_make_dummy_frame(120), now_mono=t0 + 4.5, source_id="IOS_FULL_SCREEN_SHARE")
    assert mon.get_status_snapshot(now_mono=t0 + 4.5)["initial_verified"] is True

    # Scenario M: Verify StrictPrerequisiteEngine marks all mandatory checks PASSED
    engine = StrictPrerequisiteEngine()
    for chk in engine.checks.values():
        chk.set_result(CheckStatus.PASSED, "Verified")
    ev = engine.evaluate_readiness()
    assert ev["ready"] is True
    assert ev["remaining_mandatory"] == 0

    # Scenario N: Transition IPadScreenShareMonitor to active session monitoring
    mon.start_active_session_monitoring(session_id="sess-MN-1", now_mono=t0 + 5.0)
    snap_active = mon.get_status_snapshot(now_mono=t0 + 5.0)
    assert snap_active["active_session_monitoring"] is True
    assert snap_active["is_negotiating"] is False

    # During active session, a >10s stall triggers the active-session interruption watchdog
    wd = mon.check_watchdog(now_mono=t0 + 16.0)
    assert wd["interrupted"] is True
    assert wd["state"] == "IOS_SCREEN_SHARE_INTERRUPTED"


def test_prerequisites_no_external_browser_check():
    """Verify external browsers are NOT checked during prerequisites; prerequisites pass even if browsers are open."""
    prep = StrictSessionPreparation(
        _InMemoryDB(),
        lambda refresh=False: {"ready": True, "mode": "DEMO", "internet": True, "secure_browser": True, "allowlist_configured": True, "hardware_ready": True},
        camera_observer=lambda account, pid: {
            "camera_required": True,
            "front_camera_verified": True,
            "initial_check_passed": True,
            "screen_share_verified": True,
            "screen_share_status": "IOS_SCREEN_SHARE: VERIFIED",
            "camera_ready": True,
            "camera_acceptable": True,
        },
        android_observer=lambda pid, refresh=False: {"ready": True, "status": "ANDROID_READY_ACKNOWLEDGED"},
    )
    s0 = prep.state("lok")
    pid = s0["preparation_id"]
    prep.host_report("lok", pid, {"camera": True, "visible": True, "online": True, "secure_context": True, "camera_native_id": "a" * 64})
    inv = prep.invite("lok", "IOS", "https://study.strictroutines.com")["invitation"]
    joined = prep.join(inv["url"].split("#")[1], "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")
    prep.heartbeat(pid, "IOS", joined["credential"], {"timer_ready": True, "online": True, "visible": True, "screen_sharing": True})

    st = prep.state("lok")
    # Gate must not include EXTERNAL_BROWSERS_CLOSED
    assert "EXTERNAL_BROWSERS_CLOSED" not in st["final_gate"]
    assert st["final_gate"]["IOS_MEDIA_READY"] is True
    assert st["ready"] is True
    # authorize_start must succeed without browser gating
    auth = prep.authorize_start("lok", pid)
    assert auth["preparation_id"] == pid


def test_explicit_ios_stop_events_independence():
    """Verify explicit stop events affect ONLY their specific media source."""
    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
    cam_mon = IPadCameraStreamMonitor.get_instance()
    scr_mon = IPadScreenShareMonitor.get_instance()

    # Reset both
    cam_mon.reset()
    scr_mon.reset()

    # Start screen share track
    scr_mon.start_track(track_id="trk-stop-test", preparation_id="prep-stop-1", now_mono=100.0)
    scr_mon.process_frame(_make_dummy_frame(100), now_mono=100.0, source_id="IOS_FULL_SCREEN_SHARE")
    assert scr_mon.get_status_snapshot(now_mono=100.0)["is_active"] is True

    # 1. Stop ONLY Front Camera
    cam_stop = cam_mon.record_explicit_stop(
        source_id="IOS_FRONT_CAMERA",
        event_id="stop_cam_123",
        reason="iPad front camera stopped by user",
        timestamp=101.0,
        user_initiated=True,
    )
    assert cam_stop["source_id"] == "IOS_FRONT_CAMERA"
    assert cam_stop["reason"] == "iPad front camera stopped by user"
    assert cam_mon.health_state == "IOS_CAMERA_STOPPED"
    # Screen share MUST remain active!
    assert scr_mon.get_status_snapshot(now_mono=101.0)["is_active"] is True

    # 2. Stop ONLY Screen Share
    scr_mon.stop_track(reason="iPad full-screen sharing stopped by user", event_id="stop_scr_456", user_initiated=True)
    assert scr_mon.get_status_snapshot(now_mono=102.0)["is_active"] is False
    assert scr_mon.get_status_snapshot(now_mono=102.0)["state"] == "SCREEN_SHARE_STOPPED"
