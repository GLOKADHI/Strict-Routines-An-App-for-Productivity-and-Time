#!/usr/bin/env python3
"""
StrictRecords — Automated APK Release Publisher & Manifest Generator.
Publishes verified APK builds, calculates cryptographic SHA-256 checksums,
generates signed release manifests, and broadcasts real-time update notifications
to connected Android and Windows endpoints.
"""

import os
import sys
import json
import hashlib
import argparse
import urllib.request
import urllib.parse
import subprocess
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def android_build_metadata():
    gradle_path = os.path.join(ROOT_DIR, "android", "app", "build.gradle")
    with open(gradle_path, "r", encoding="utf-8") as gradle_file:
        text = gradle_file.read()
    version_name = re.search(r'(?m)^\s*versionName\s+["\']([^"\']+)["\']', text)
    version_code = re.search(r"(?m)^\s*versionCode\s+(\d+)", text)
    build_dir = re.search(r'(?m)^\s*buildDir\s*=\s*file\(\s*["\']([^"\']+)["\']\s*\)', text)
    if not version_name or not version_code:
        raise RuntimeError("Could not read APK version metadata from android/app/build.gradle")
    app_build_dir = build_dir.group(1) if build_dir else os.path.join(ROOT_DIR, "android", "app", "build")
    if not os.path.isabs(app_build_dir):
        app_build_dir = os.path.join(ROOT_DIR, "android", "app", app_build_dir)
    return version_name.group(1), int(version_code.group(1)), app_build_dir


def configured_apk_path(release_mode: bool = False) -> str:
    _, _, app_build_dir = android_build_metadata()
    variant = "release" if release_mode else "debug"
    return os.path.join(app_build_dir, "outputs", "apk", variant, f"app-{variant}.apk")

def compute_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest().lower()

def run_gradle_build(release_mode: bool = False) -> str:
    android_dir = os.path.join(ROOT_DIR, "android")
    gradle_cmd = os.path.join(android_dir, "gradlew.bat" if sys.platform == "win32" else "gradlew")
    task = "assembleRelease" if release_mode else "assembleDebug"
    print(f"[*] Running Gradle build task: {task}...")
    res = subprocess.run([gradle_cmd, task, "--no-daemon"], cwd=android_dir, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] Gradle build failed:\n{res.stderr or res.stdout}")
        sys.exit(1)
    
    candidate = configured_apk_path(release_mode=release_mode)
    if not os.path.isfile(candidate):
        print(f"[!] Gradle did not produce the configured APK: {candidate}")
        sys.exit(1)

    print(f"[+] Build successful: {candidate}")
    return candidate

def publish_release(
    apk_path: str,
    version_name: str,
    version_code: int,
    notes: str,
    channel: str = "PRODUCTION",
    is_mandatory: bool = False,
    server_url: str = "http://127.0.0.1:8765",
    admin_session: str = "",
):
    if not os.path.exists(apk_path):
        print(f"[!] APK file does not exist: {apk_path}")
        sys.exit(1)
    if not admin_session:
        print("[!] Set STRICTRECORDS_ADMIN_SESSION to an active administrator session before publishing.")
        sys.exit(1)

    file_size = os.path.getsize(apk_path)
    sha256_hash = compute_sha256(apk_path)
    print("\n" + "=" * 60)
    print(" STRICTRECORDS APK RELEASE PUBLISHER")
    print("=" * 60)
    print(f" Source APK:      {apk_path}")
    print(f" Version Name:    {version_name}")
    print(f" Version Code:    {version_code}")
    print(f" Channel:         {channel}")
    print(f" Mandatory:       {is_mandatory}")
    print(f" File Size:       {file_size} bytes ({file_size / (1024 * 1024):.2f} MB)")
    print(f" SHA-256 Digest:  {sha256_hash}")
    print(f" Target Server:   {server_url}")
    print("-" * 60)

    payload = {
        "version_name": version_name,
        "version_code": version_code,
        "channel": channel,
        "mandatory": is_mandatory,
        "release_notes": notes,
        "file_size_bytes": file_size,
        "sha256_checksum": sha256_hash,
        "platform": "ANDROID"
    }

    try:
        boundary = "StrictRecords" + hashlib.sha256(os.urandom(32)).hexdigest()
        filename = os.path.basename(apk_path).replace('"', "").replace("\r", "").replace("\n", "")
        with open(apk_path, "rb") as apk_file:
            apk_bytes = apk_file.read()
        multipart = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/vnd.android.package-archive\r\n\r\n"
        ).encode("utf-8") + apk_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        inspect_req = urllib.request.Request(
            f"{server_url}/api/releases/inspect",
            data=multipart,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Admin-Session": admin_session,
            },
        )
        with urllib.request.urlopen(inspect_req, timeout=60) as resp:
            inspected = json.loads(resp.read().decode("utf-8"))
        if not inspected.get("valid") or not inspected.get("staging_id"):
            print(f"[!] APK validation failed: {inspected.get('errors') or inspected.get('error')}")
            sys.exit(1)

        payload["staging_id"] = inspected["staging_id"]
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url}/api/releases/publish",
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "X-Admin-Session": admin_session,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            res = json.loads(resp_body)
            if res.get("success"):
                print("[+] Release successfully registered with server backend!")
                print(f"    Release ID:   {res.get('release_id')}")
            else:
                print(f"[!] Server error: {res.get('error')}")
                sys.exit(1)
    except Exception as e:
        print(f"[!] Network error contacting server: {e}")
        sys.exit(1)

    # Broadcast notification to devices
    try:
        notify_req = urllib.request.Request(
            f"{server_url}/api/releases/notify",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Admin-Session": admin_session,
            },
        )
        with urllib.request.urlopen(notify_req, timeout=5) as notify_resp:
            n_res = json.loads(notify_resp.read().decode("utf-8"))
            if n_res.get("success"):
                print("[+] APK update notification broadcasted to all connected devices.")
    except Exception as ne:
        print(f"[*] Note: Broadcast ping skipped ({ne})")

    print("\n[OK] Release publication finished successfully!")

def main():
    parser = argparse.ArgumentParser(description="StrictRecords APK Release Publisher")
    parser.add_argument("--apk", help="Path to APK binary file", default=None)
    parser.add_argument("--version-name", help="Version name (defaults to android/app/build.gradle)", default=None)
    parser.add_argument("--version-code", type=int, help="Version code (defaults to android/app/build.gradle)", default=None)
    parser.add_argument("--notes", help="Release notes", default="• In-app updater support\n• Improved real-time sync\n• Performance optimizations")
    parser.add_argument("--channel", choices=["PRODUCTION", "BETA"], default="PRODUCTION")
    parser.add_argument("--mandatory", action="store_true", help="Mark update as mandatory")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="Target StrictRecords server URL")
    parser.add_argument(
        "--admin-session",
        default=os.environ.get("STRICTRECORDS_ADMIN_SESSION", ""),
        help="Active admin session token (or set STRICTRECORDS_ADMIN_SESSION)",
    )
    parser.add_argument("--build", action="store_true", help="Compile APK using Gradle before publishing")
    parser.add_argument("--release", action="store_true", help="Build release APK instead of debug")

    args = parser.parse_args()
    if not args.admin_session:
        parser.error("Set STRICTRECORDS_ADMIN_SESSION or pass --admin-session with an active administrator session.")

    gradle_version_name, gradle_version_code, current_apk = android_build_metadata()
    apk_file = args.apk
    if args.build:
        apk_file = run_gradle_build(release_mode=args.release)
    elif not apk_file:
        apk_file = current_apk
        if not os.path.isfile(apk_file):
            apk_file = run_gradle_build(release_mode=args.release)

    publish_release(
        apk_path=apk_file,
        version_name=args.version_name or gradle_version_name,
        version_code=args.version_code if args.version_code is not None else gradle_version_code,
        notes=args.notes,
        channel=args.channel,
        is_mandatory=args.mandatory,
        server_url=args.server,
        admin_session=args.admin_session
    )

if __name__ == "__main__":
    main()
