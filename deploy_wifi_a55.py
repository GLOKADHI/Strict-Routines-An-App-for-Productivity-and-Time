import os
import sys
import time
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

APK_PATH = r"C:\Users\lokad\Desktop\StrictRecords.apk"
DEFAULT_IP = "192.168.29.182"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def run_cmd(cmd_list, timeout=60):
    try:
        res = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def find_open_ports(ip, port_range=range(30000, 50001)):
    open_ports = []
    def check_p(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            if s.connect_ex((ip, p)) == 0:
                open_ports.append(p)
        except:
            pass
        finally:
            s.close()

    # Also check standard 5555
    check_p(5555)

    with ThreadPoolExecutor(max_workers=200) as ex:
        ex.map(check_p, port_range)
    return open_ports

def get_connected_device():
    rc, out, _ = run_cmd(["adb", "devices", "-l"])
    lines = out.splitlines()
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            dev_id, status = parts[0], parts[1]
            if status == "device":
                return dev_id
    return None

def main():
    target_ip = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else DEFAULT_IP
    target_port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else (int(sys.argv[2]) if len(sys.argv) > 2 else None)

    log(f"Starting Wi-Fi ADB Deployment for {target_ip}...")
    log(f"Target APK: {APK_PATH} ({os.path.getsize(APK_PATH) / 1024 / 1024:.1f} MB)")

    # 1. Check existing connected devices
    connected = get_connected_device()
    if connected:
        log(f"Already connected to authorized device: {connected}")
        target_serial = connected
    else:
        # 2. Determine target port
        if target_port:
            ports_to_try = [target_port]
        else:
            log(f"Scanning {target_ip} for active wireless debugging ports (30000-50000)...")
            ports_to_try = find_open_ports(target_ip)
            log(f"Open ports discovered: {ports_to_try}")

        if not ports_to_try:
            log(f"ERROR: No open ports found on {target_ip}.")
            log("Please ensure:")
            log("1. Samsung Galaxy A55 is unlocked and awake.")
            log("2. Settings > Developer options > Wireless debugging is ON.")
            log("3. Note the 'IP address & Port' displayed under Wireless debugging.")
            return False

        target_serial = None
        for p in ports_to_try:
            log(f"Attempting adb connect {target_ip}:{p}...")
            run_cmd(["adb", "disconnect", f"{target_ip}:{p}"])
            rc, out, err = run_cmd(["adb", "connect", f"{target_ip}:{p}"], timeout=15)
            log(f"Connect result: {out} {err}")
            time.sleep(1)
            connected = get_connected_device()
            if connected and f"{target_ip}:{p}" in connected:
                target_serial = connected
                log(f"Successfully connected and authorized: {target_serial}")
                break

        if not target_serial:
            rc, out, _ = run_cmd(["adb", "devices", "-l"])
            log(f"Current adb devices:\n{out}")
            if "offline" in out or "unauthorized" in out:
                log("Device detected but is OFFLINE or UNAUTHORIZED.")
                log("Please check your phone screen and tap 'Always allow on this network'.")
            return False

    # 3. Stream and install APK
    log(f"Installing {APK_PATH} on {target_serial}...")
    rc, out, err = run_cmd(["adb", "-s", target_serial, "install", "-r", "-d", APK_PATH], timeout=180)
    log(f"Install output:\n{out}\n{err}")
    if rc != 0 or "Success" not in out:
        log("Installation failed!")
        return False

    log("APK successfully installed!")

    # 4. Verify package version
    log("Verifying installed package details...")
    rc, pkg_info, _ = run_cmd(["adb", "-s", target_serial, "shell", "dumpsys package com.strictrecords.app | grep -E 'versionCode|versionName|lastUpdateTime'"])
    log(f"Package info:\n{pkg_info}")

    # 5. Launch MainActivity
    log("Launching com.strictrecords.app.MainActivity...")
    run_cmd(["adb", "-s", target_serial, "shell", "am", "start", "-n", "com.strictrecords.app/.MainActivity"])
    time.sleep(3)

    # 6. Capture verification screenshot
    screenshot_remote = "/sdcard/screen_a55_update.png"
    screenshot_local = r"C:\Users\lokad\Music\Practical\StrictRecords\screen_a55_update.png"
    log("Capturing verification screenshot...")
    run_cmd(["adb", "-s", target_serial, "shell", f"screencap -p {screenshot_remote}"])
    run_cmd(["adb", "-s", target_serial, "pull", screenshot_remote, screenshot_local])
    if os.path.exists(screenshot_local):
        log(f"Screenshot saved to: {screenshot_local}")

    log("Deployment complete and verified!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
