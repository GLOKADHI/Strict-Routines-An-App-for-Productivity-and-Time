"""
StrictRecords — Fast Tablet APK Updater & Launcher (Lenovo TAB4 10 / 49e6d324)
Installs the latest compiled debug APK (`android/app/build/outputs/apk/debug/app-debug.apk`),
configures USB port forwarding (`adb reverse tcp:8765 tcp:8765`), and launches MainActivity.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ADB = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
if not os.path.exists(ADB):
    ADB = shutil.which("adb") or "adb"

DEVICE_SERIAL = "49e6d324"


def sync_latest_apk() -> str:
    built_apk = r"C:\Users\lokad\.strictrecords_build\app\outputs\apk\debug\app-debug.apk"
    workspace_apk = os.path.join(
        ROOT_DIR, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk"
    )
    dist_apk = os.path.join(ROOT_DIR, ".dist", "StrictRecords-debug.apk")
    root_apk = os.path.join(ROOT_DIR, "StrictRecords-debug.apk")
    prog_apk = r"C:\Users\lokad\AppData\Local\Programs\StrictRecords\StrictRecords-debug.apk"

    if os.path.exists(built_apk):
        for dest in [workspace_apk, dist_apk, root_apk, prog_apk]:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(built_apk, dest)
            except Exception:
                pass
        return built_apk
    if os.path.exists(workspace_apk):
        return workspace_apk
    return dist_apk


def main() -> int:
    apk_path = sync_latest_apk()
    print(f"Using ADB: {ADB}")
    print(f"Using APK: {apk_path} ({os.path.getsize(apk_path)} bytes)")

    env = os.environ.copy()
    env["ADB_MDNS"] = "0"
    env["ADB_MDNS_OPENSCREEN"] = "0"

    subprocess.Popen(
        [ADB, "start-server"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print("Checking for connected Lenovo tablet (49e6d324)...")
    online_serial = None
    for attempt in range(1, 16):
        out = subprocess.check_output(
            [ADB, "devices", "-l"], env=env, stdin=subprocess.DEVNULL, text=True
        )
        lines = [
            l.strip()
            for l in out.splitlines()
            if l.strip() and not l.startswith("List of") and not l.startswith("*")
        ]
        print(f"  Attempt {attempt}/15: {lines if lines else 'No USB device detected'}")
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                online_serial = parts[0]
                break
        if online_serial:
            break
        time.sleep(2.0)

    if not online_serial:
        print(
            "\n[!] Lenovo TAB4 10 (49e6d324) is not currently detected in 'device' state over USB."
        )
        print("    1. Make sure the USB cable is firmly plugged into both the tablet and PC.")
        print("    2. On the tablet notification shade, ensure USB mode is set to File Transfer (MTP) / USB Debugging.")
        print("    3. In Settings -> Developer options, toggle 'USB debugging' OFF and back ON.")
        return 1

    print(f"\nConnected to {online_serial}! Installing updated APK...")
    install_out = subprocess.check_output(
        [ADB, "-s", online_serial, "install", "-r", apk_path],
        env=env,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    print("Install Output:", install_out.strip())

    subprocess.run(
        [ADB, "-s", online_serial, "reverse", "tcp:8765", "tcp:8765"],
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    launch_out = subprocess.check_output(
        [
            ADB,
            "-s",
            online_serial,
            "shell",
            "am",
            "start",
            "-n",
            "com.strictrecords.app/.MainActivity",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print("Launch Output:", launch_out.strip())
    print("\nSUCCESS: StrictRecords app updated and launched on Lenovo tablet!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
