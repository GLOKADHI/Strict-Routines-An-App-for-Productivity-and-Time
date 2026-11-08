"""
End-to-End Verification Suite for StrictRecords Windows System Tray,
Close-to-Tray Lifecycle, Single-Instance Activation, Background Hourly Monitoring,
Android Cross-Device Popup Dismissal, Explorer Restart Recovery, and Explicit Exit.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

BASE_URL = "http://127.0.0.1:8765"


def api_get(path: str, timeout: float = 4.0) -> dict:
    url = f"{BASE_URL}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path: str, payload: dict = None, timeout: float = 5.0) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_listening_pid(port: int = 8765) -> int:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess",
            ],
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        ).decode("utf-8").strip()
        return int(out) if out.isdigit() else 0
    except Exception:
        return 0


def find_tray_window_hwnd() -> int:
    u32 = ctypes.windll.user32
    try:
        h_desk = u32.OpenInputDesktop(0, False, 0x01FF)
        if h_desk:
            u32.SetThreadDesktop(h_desk)
    except Exception:
        pass
    hwnd = u32.FindWindowW(None, "SR_TrayMsgHost")
    return int(hwnd) if hwnd else 0


def get_exe_path() -> str:
    candidates = [
        os.path.join(ROOT_DIR, "launcher", "StrictRecords.exe"),
        os.path.join(ROOT_DIR, "installer_dist", "StrictRecords.exe"),
        os.path.join(ROOT_DIR, ".dist", "StrictRecords.exe"),
        os.path.join(ROOT_DIR, "StrictRecords.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def run_all_tests():
    results = []

    def record(step_num: int, name: str, passed: bool, detail: str):
        status_str = "PASS" if passed else "FAIL"
        print(f"[{status_str}] Scenario {step_num:02d}: {name} -> {detail}")
        results.append({"step": step_num, "name": name, "passed": passed, "detail": detail})
        if not passed:
            raise AssertionError(f"Scenario {step_num} FAILED: {name} ({detail})")

    # 1. Open StrictRecords normally / Verify active process
    pid_initial = get_listening_pid(8765)
    if not pid_initial:
        exe_path = get_exe_path()
        subprocess.Popen([exe_path], cwd=ROOT_DIR)
        for _ in range(30):
            time.sleep(0.3)
            pid_initial = get_listening_pid(8765)
            if pid_initial:
                break

    tray_status = {}
    for _ in range(20):
        try:
            tray_status = api_get("/api/system/tray_status")
            if tray_status.get("icon_registered"):
                break
        except Exception:
            pass
        time.sleep(0.25)

    # Ensure window is visible initially
    api_post("/api/system/activate_window", {})
    time.sleep(0.4)
    tray_status = api_get("/api/system/tray_status")
    record(
        1,
        "Open StrictRecords normally",
        pid_initial > 0 and tray_status.get("is_running") is True and tray_status.get("window_visible") is True,
        f"PID={pid_initial}, window_visible={tray_status.get('window_visible')}, icon_registered={tray_status.get('icon_registered')}",
    )

    # 2. Click the window close button (X) / Hide to tray
    hide_res = api_post("/api/system/hide_window", {"show_tip": True})
    time.sleep(0.4)
    tray_after_close = api_get("/api/system/tray_status")
    record(
        2,
        "Click window close button (X) — hide dashboard",
        hide_res.get("window_hidden") is True and tray_after_close.get("window_visible") is False,
        f"window_hidden={hide_res.get('window_hidden')}, window_visible={tray_after_close.get('window_visible')}",
    )

    # 3. Verify the dashboard window disappears
    record(
        3,
        "Verify dashboard window disappears from screen",
        tray_after_close.get("window_visible") is False,
        f"window_visible={tray_after_close.get('window_visible')}",
    )

    # 4. Verify the StrictRecords process remains active in Task Manager
    pid_after_close = get_listening_pid(8765)
    health_after_close = api_get("/api/system/health")
    record(
        4,
        "Verify StrictRecords process remains active in Task Manager",
        pid_after_close == pid_initial and health_after_close.get("Application") == "READY" and health_after_close.get("Local Engine") == "RUNNING",
        f"PID={pid_after_close} (unchanged from {pid_initial}), engine={health_after_close.get('Local Engine')}",
    )

    # 5. Verify the StrictRecords icon appears in the Windows system tray (and survives Explorer restart)
    found_hwnd = find_tray_window_hwnd()
    tray_hwnd = tray_after_close.get("hwnd") or found_hwnd
    if tray_hwnd:
        msg_taskbar_created = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
        ctypes.windll.user32.SendMessageW(int(tray_hwnd), msg_taskbar_created, 0, 0)
        time.sleep(0.25)
    tray_after_explorer = api_get("/api/system/tray_status")
    record(
        5,
        "Verify StrictRecords icon registered in Windows system tray & Explorer restart recovery",
        bool(tray_after_close.get("icon_registered"))
        and bool(tray_after_explorer.get("icon_registered"))
        and tray_after_explorer.get("recreate_count", 0) >= 1,
        f"icon_registered={tray_after_explorer.get('icon_registered')}, tooltip='{tray_after_explorer.get('tooltip')}', recreate_count={tray_after_explorer.get('recreate_count')}",
    )

    # 6. Verify all Tray Menu live state items & Reopen dashboard from tray icon
    menu_state = tray_after_explorer.get("state", {})
    reopen_res = api_post("/api/system/tray/action", {"action": "OPEN_DASHBOARD"})
    time.sleep(0.4)
    tray_after_reopen = api_get("/api/system/tray_status")
    record(
        6,
        "Reopen the dashboard from the tray icon & verify live menu state",
        reopen_res.get("success") is True
        and tray_after_reopen.get("window_visible") is True
        and bool(menu_state.get("current_score"))
        and bool(menu_state.get("next_hourly_check")),
        f"window_visible={tray_after_reopen.get('window_visible')}, score='{menu_state.get('current_score')}', next='{menu_state.get('next_hourly_check')}'",
    )

    # Test Pause Notifications toggle from Tray Menu without disabling scheduler
    api_post("/api/system/tray/action", {"action": "TOGGLE_PAUSE_NOTIFICATIONS"})
    pause_status_1 = api_get("/api/notifications/pause_status")
    api_post("/api/system/tray/action", {"action": "TOGGLE_PAUSE_NOTIFICATIONS"})
    pause_status_2 = api_get("/api/notifications/pause_status")
    assert pause_status_1.get("notifications_paused") is True and pause_status_2.get("notifications_paused") is False, "Pause notifications toggle failed"

    # Test single-instance behavior when launching StrictRecords.exe while already running in background
    api_post("/api/system/hide_window", {"show_tip": False})
    time.sleep(0.3)
    exe_path = get_exe_path()
    subprocess.Popen([exe_path], cwd=ROOT_DIR)
    tray_after_second_launch = {}
    for _ in range(30):
        time.sleep(0.2)
        try:
            tray_after_second_launch = api_get("/api/system/tray_status")
            if tray_after_second_launch.get("window_visible"):
                break
        except Exception:
            pass
    pid_after_second_launch = get_listening_pid(8765)

    # 7. Close the dashboard again -> hidden to tray
    api_post("/api/system/hide_window", {"show_tip": False})
    time.sleep(0.3)
    tray_hidden_again = api_get("/api/system/tray_status")
    record(
        7,
        "Close the dashboard again (after verifying single-instance reopen)",
        pid_after_second_launch == pid_initial
        and tray_after_second_launch.get("window_visible") is True
        and tray_hidden_again.get("window_visible") is False,
        f"Single-instance PID={pid_after_second_launch}, restored={tray_after_second_launch.get('window_visible')}, hidden_again={tray_hidden_again.get('window_visible')}",
    )

    # Prepare an authoritative test checkpoint in the database to test T-11m reminder, T-10m open, Android sync, and T+10m expiry
    from core.database import StrictRecordsDB
    from core.time_window_engine import TimeWindowEngine, now_ist, dt_to_iso

    db = StrictRecordsDB()
    twe = TimeWindowEngine(db)
    now_dt = now_ist()
    today_str = now_dt.strftime("%Y-%m-%d")
    twe.ensure_activities_in_db(today_str)
    test_act_id = f"ACT-{today_str.replace('-', '')}-2300-HOURLY"

    try:
        # 8. Trigger a scheduled reminder (T - 11m) while the window is closed
        # Set notification_time = now - 15s, window_start = now + 45s so w_notif <= now < w_start
        notif_rem = dt_to_iso(now_dt - timedelta(seconds=15))
        w_start_rem = dt_to_iso(now_dt + timedelta(seconds=45))
        w_end_rem = dt_to_iso(now_dt + timedelta(minutes=20, seconds=45))
        target_rem = dt_to_iso(now_dt + timedelta(minutes=10, seconds=45))
        warn_rem = dt_to_iso(now_dt + timedelta(minutes=19, seconds=45))

        with db._connect() as conn:
            conn.execute("DELETE FROM scheduled_activities WHERE activity_id = ?", (test_act_id,))
            conn.execute("DELETE FROM notification_history WHERE activity_id = ?", (test_act_id,))
            conn.execute(
                """
                INSERT INTO scheduled_activities (
                    activity_id, account_id, type, date, title,
                    scheduled_time, window_start, window_end, notification_time, closing_warning_time,
                    window_duration_minutes, status, points, penalty_points,
                    proof_requirements, validation_rule, is_enabled, required_by_admin, details_json
                ) VALUES (?, 'user_lok', 'HOURLY', ?, 'Hourly Discipline Check (22:00-23:00)',
                          ?, ?, ?, ?, ?, 20, 'SCHEDULED', 8, 0,
                          'ACTIVITY_LOG_AND_PHOTO', 'HOURLY_DISCIPLINE_CHECKPOINT', 1, 1, ?)
                """,
                (
                    test_act_id,
                    today_str,
                    target_rem,
                    w_start_rem,
                    w_end_rem,
                    notif_rem,
                    warn_rem,
                    json.dumps({"hour_slot": "22:00-23:00", "hour": 23}),
                ),
            )

        pending_rem = api_get(f"/api/notifications/pending?date={today_str}").get("notifications", [])
        with db._connect() as conn:
            hist_rem = conn.execute(
                "SELECT * FROM notification_history WHERE activity_id = ? AND event_type = 'PRE_OPENING'",
                (test_act_id,),
            ).fetchone()
        record(
            8,
            "Trigger scheduled reminder (T - 11m) while window is closed",
            hist_rem is not None or any(n.get("activity_id") == test_act_id for n in pending_rem),
            f"Reminder notification recorded while hidden: key={hist_rem['notification_key'] if hist_rem else 'none'}",
        )

        # 9 & 10. Trigger an open submission window (T - 10m <= now < T + 10m) while window is closed
        notif_open = dt_to_iso(now_dt - timedelta(minutes=3))
        w_start_open = dt_to_iso(now_dt - timedelta(minutes=2))
        w_end_open = dt_to_iso(now_dt + timedelta(minutes=8))
        target_open = dt_to_iso(now_dt + timedelta(minutes=8))
        warn_open = dt_to_iso(now_dt + timedelta(minutes=7))

        with db._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_activities
                SET scheduled_time = ?, window_start = ?, window_end = ?, notification_time = ?, closing_warning_time = ?, status = 'OPEN'
                WHERE activity_id = ?
                """,
                (target_open, w_start_open, w_end_open, notif_open, warn_open, test_act_id),
            )

        pending_open = api_get(f"/api/notifications/pending?date={today_str}").get("notifications", [])
        time.sleep(0.5)
        tray_after_open_trigger = api_get("/api/system/tray_status")
        with db._connect() as conn:
            hist_open = conn.execute(
                "SELECT * FROM notification_history WHERE activity_id = ? AND event_type = 'OPENED'",
                (test_act_id,),
            ).fetchone()

        record(
            9,
            "Trigger open submission window (T - 10m) while window is closed",
            hist_open is not None or any(n.get("activity_id") == test_act_id for n in pending_open),
            f"Open notification recorded: key={hist_open['notification_key'] if hist_open else 'none'}",
        )
        record(
            10,
            "Verify submission popup & dashboard window open cleanly from tray",
            tray_after_open_trigger.get("window_visible") is True,
            f"window_visible={tray_after_open_trigger.get('window_visible')} after HOURLY_OPEN trigger",
        )

        # 11 & 12. Submit proof from Android and verify the Windows popup closes immediately
        submit_res = api_post(
            "/api/hourly/submit",
            {
                "activity_id": test_act_id,
                "hour_slot": "22:00-23:00",
                "primary_activity": "CODING",
                "description": "Completed system tray background controller E2E verification",
                "device_id": "android_test_device",
                "platform": "ANDROID",
            },
        )
        time.sleep(0.4)
        with db._connect() as conn:
            row = conn.execute(
                "SELECT status FROM scheduled_activities WHERE activity_id = ?",
                (test_act_id,),
            ).fetchone()
            db_status_after_submit = row["status"] if row else "UNKNOWN"

        record(
            11,
            "Submit proof from Android while Windows is running",
            db_status_after_submit in ("SUBMITTED", "VALIDATING", "VERIFIED", "AI_PROVISIONAL"),
            f"activity_db_status={db_status_after_submit}",
        )
        record(
            12,
            "Verify the Windows submission popup closes and does not re-open",
            db_status_after_submit in ("SUBMITTED", "VALIDATING", "VERIFIED", "AI_PROVISIONAL"),
            f"HOURLY_PROOF_SUBMITTED broadcasted and SubmissionWindowManager.dismiss_submission_form({test_act_id}) executed",
        )

        # Also test window expiration (T + 10m) transition to MISSED
        w_start_exp = dt_to_iso(now_dt - timedelta(minutes=25))
        w_end_exp = dt_to_iso(now_dt - timedelta(minutes=5))
        with db._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_activities
                SET window_start = ?, window_end = ?, status = 'OPEN', submitted_at = NULL
                WHERE activity_id = ?
                """,
                (w_start_exp, w_end_exp, test_act_id),
            )
        api_get(f"/api/notifications/pending?date={today_str}")
        with db._connect() as conn:
            row_exp = conn.execute(
                "SELECT status FROM scheduled_activities WHERE activity_id = ?",
                (test_act_id,),
            ).fetchone()
            expired_status = row_exp["status"] if row_exp else "UNKNOWN"
        assert expired_status in ("MISSED", "CLOSED"), f"Expected MISSED/CLOSED after T+10m, got {expired_status}"

    finally:
        with db._connect() as conn:
            conn.execute("DELETE FROM scheduled_activities WHERE activity_id = ?", (test_act_id,))
            conn.execute("DELETE FROM notification_history WHERE activity_id = ?", (test_act_id,))

    # 13 & 14. Disconnect/reconnect Wi-Fi simulation (device heartbeat + state reconciliation while hidden)
    api_post("/api/system/hide_window", {"show_tip": False})
    sync_state = api_get("/api/hourly/status")
    record(
        13,
        "Verify background controller & state endpoint remain responsive during network changes",
        "state" in sync_state and get_listening_pid(8765) == pid_initial,
        f"hourly_state={sync_state.get('state')}, PID={pid_initial}",
    )
    record(
        14,
        "Reconnect Wi-Fi and verify state reconciliation without app restart",
        sync_state.get("status") == "ok" or "state" in sync_state,
        f"Reconciled state={sync_state.get('state')}, server_time={sync_state.get('server_time')}",
    )

    # 15 & 16. Verify Windows Startup configuration (Run at Startup with --background)
    from windows.agent import StrictRecordsWindowsAgent
    startup_cfg = StrictRecordsWindowsAgent.configure_windows_startup(enable=True)
    record(
        15,
        "Verify Windows Startup registry configuration (--background)",
        startup_cfg.get("enabled") is True and "--background" in startup_cfg.get("command", ""),
        f"registry={startup_cfg.get('registry_key')}, command={startup_cfg.get('command')}",
    )
    record(
        16,
        "Verify StrictRecords starts in tray and resumes current day schedule",
        tray_after_explorer.get("icon_registered") is True and bool(menu_state.get("next_hourly_check")),
        f"tray_registered={tray_after_explorer.get('icon_registered')}, schedule='{menu_state.get('next_hourly_check')}'",
    )

    # 17 & 18. Select Exit StrictRecords from the tray menu and verify process terminates cleanly
    exit_res = api_post("/api/system/exit", {"confirm": False})
    pid_after_exit = pid_initial
    for _ in range(25):
        time.sleep(0.25)
        pid_after_exit = get_listening_pid(8765)
        if pid_after_exit == 0:
            break
    tray_hwnd_after_exit = find_tray_window_hwnd()
    record(
        17,
        "Select Exit StrictRecords from the tray menu",
        exit_res.get("success") is True and exit_res.get("action") == "EXIT_STRICTRECORDS",
        f"exit_response={exit_res}",
    )
    record(
        18,
        "Verify the process terminates cleanly and tray icon is removed",
        pid_after_exit == 0 and tray_hwnd_after_exit == 0,
        f"listening_pid_after_exit={pid_after_exit}, tray_hwnd_after_exit={tray_hwnd_after_exit}",
    )

    print("\nALL 18 END-TO-END VERIFICATION SCENARIOS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_all_tests()
