"""Non-destructive verification of the StrictRecords Android client.

Builds the debug APK, checks its identity against the installed package, updates
the app in place without clearing data, then verifies exported test intents cannot
manufacture proof, task completion, or positive score events.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ANDROID_DIR = ROOT_DIR / "android"
APP_GRADLE = ANDROID_DIR / "app" / "build.gradle"
PACKAGE_NAME = "com.strictrecords.app"
DEVICE_SERIAL = os.environ.get("STRICTRECORDS_ANDROID_DEVICE", "49e6d324")
ADB = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk") / "platform-tools" / "adb.exe"
APK_PATH = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".strictrecords_build" / "app" / "outputs" / "apk" / "debug" / "app-debug.apk"
TEST_COMMANDS = (
    "WAKEUP_STEP1_CAMERA",
    "ADMIN_APPROVE_FIRST",
    "SUBMIT_HOURLY",
    "COMPLETE_BATH",
    "COMPLETE_GYM",
    "COMPLETE_FOCUS",
    "SNOOZE",
)


def run(command: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    return result.stdout.decode("utf-8", errors="replace").strip()


def adb(*args: str, timeout: int = 90) -> str:
    return run([str(ADB), "-s", DEVICE_SERIAL, *args], timeout=timeout)


def adb_bytes(*args: str, timeout: int = 90) -> bytes:
    result = subprocess.run(
        [str(ADB), "-s", DEVICE_SERIAL, *args],
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    return result.stdout


def find_apksigner() -> Path:
    sdk_root = ADB.parent.parent
    candidates = sorted((sdk_root / "build-tools").glob("*/apksigner.bat"), reverse=True)
    if not candidates:
        raise RuntimeError("Android apksigner is unavailable; refusing to install an unchecked APK.")
    return candidates[0]


def signer_digest(apksigner: Path, apk: Path) -> str:
    cmd = [str(apksigner), "verify", "--print-certs", str(apk)]
    if os.name == "nt" and apksigner.suffix.lower() in (".bat", ".cmd"):
        cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *cmd]
    output = run(cmd, timeout=60)
    match = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)", output)
    if not match:
        raise RuntimeError(f"Could not verify APK signing certificate: {apk}")
    return match.group(1).lower()


def installed_apk_path() -> str:
    lines = adb("shell", "pm", "path", PACKAGE_NAME).splitlines()
    path = next((line.removeprefix("package:").strip() for line in lines if line.startswith("package:")), "")
    if not path:
        raise RuntimeError(f"{PACKAGE_NAME} is not installed; this verifier will not perform a fresh data-destructive install.")
    return path


def snapshot_local_state(device_date: str) -> tuple:
    """Read only compact scoring/proof/task aggregates, never user text or filenames."""
    raw = adb_bytes("exec-out", "run-as", PACKAGE_NAME, "cat", "databases/strictrecords_android.db")
    if not raw:
        raise RuntimeError("Could not read the installed app database using run-as.")
    with tempfile.NamedTemporaryFile(prefix="strictrecords-device-", suffix=".db", delete=False) as temp_db:
        temp_db.write(raw)
        db_path = temp_db.name
    conn = sqlite3.connect(db_path)
    try:
        score = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN points > 0 THEN points ELSE 0 END), 0) "
            "FROM score_events"
        ).fetchone()
        proof = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0) FROM proof_reviews"
        ).fetchone()
        daily = conn.execute(
            "SELECT COALESCE(SUM(wakeup_camera), 0), COALESCE(SUM(bath_status IN ('COMPLETED','VERIFIED')), 0), "
            "COALESCE(SUM(gym_status IN ('COMPLETED','VERIFIED')), 0), "
            "COALESCE(SUM(hourly_completed), 0), COALESCE(SUM(focus_sessions_count), 0) "
            "FROM daily_state WHERE date = ?",
            (device_date,),
        ).fetchone()
        hourly = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status IN ('SUBMITTED','VERIFIED','COMPLETED')), 0) FROM hourly_records"
        ).fetchone()
        return tuple(score or ()) + tuple(proof or ()) + tuple(daily or ()) + tuple(hourly or ())
    finally:
        conn.close()
        try:
            os.remove(db_path)
        except OSError:
            pass


def main() -> int:
    if not ADB.is_file():
        raise RuntimeError(f"ADB not found at {ADB}")
    devices = run([str(ADB), "devices", "-l"])
    print(devices)
    if not any(
        (fields := line.split())[:2] == [DEVICE_SERIAL, "device"]
        for line in devices.splitlines()
    ):
        raise RuntimeError(f"Android device {DEVICE_SERIAL} is not connected.")

    wrapper = ANDROID_DIR / "gradlew.bat"
    subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(wrapper), "assembleDebug"],
        cwd=ANDROID_DIR,
        check=True,
        timeout=900,
    )
    if not APK_PATH.is_file():
        raise RuntimeError(f"Built APK not found at {APK_PATH}")

    current_apk = Path(tempfile.gettempdir()) / f"strictrecords-installed-{os.getpid()}.apk"
    try:
        adb("pull", installed_apk_path(), str(current_apk), timeout=90)
        apksigner = find_apksigner()
        new_digest = signer_digest(apksigner, APK_PATH)
        installed_digest = signer_digest(apksigner, current_apk)
        if new_digest != installed_digest:
            raise RuntimeError("Installed app uses a different signing certificate; refusing to replace it.")

        install_result = adb("install", "-r", str(APK_PATH), timeout=120)
        print(f"In-place install: {install_result}")
        launch_result = adb("shell", "am", "start", "-W", "-n", f"{PACKAGE_NAME}/.MainActivity", timeout=45)
        print(f"App launch: {'Status: ok' if 'Status: ok' in launch_result else launch_result}")
        time.sleep(3)

        device_date = adb("shell", "date", "+%F").strip()
        before = snapshot_local_state(device_date)
        for command in TEST_COMMANDS:
            adb(
                "shell", "am", "start", "-W", "-n", f"{PACKAGE_NAME}/.MainActivity",
                "--es", "TEST_CMD", command, timeout=45,
            )
        time.sleep(2)
        after = snapshot_local_state(device_date)
        if before != after:
            raise RuntimeError("Exported test-intent probes changed proof, task, or score state.")

        process_id = adb("shell", "pidof", PACKAGE_NAME).strip()
        if not process_id:
            raise RuntimeError("Main app process exited after launch or intent probes.")

        print("On-device regression: external proof/approval/completion commands made no state changes.")
        print("Installed APK certificate matches the existing app; update used adb install -r without a data wipe.")
        print(f"APK: {APK_PATH} ({APK_PATH.stat().st_size:,} bytes)")
        return 0
    finally:
        try:
            current_apk.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DEVICE VERIFICATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
