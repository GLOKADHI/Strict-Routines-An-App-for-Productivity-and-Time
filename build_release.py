"""
STRICTRECORDS — Unified Release Builder & Desktop Distribution Packager
Mandatory Rule: For every update, build and keep one .exe, .apk, and clean source package in .dist/
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, ".dist")


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_icon():
    print("\n[1/5] Generating multi-resolution app_icon.ico...")
    logo_path = os.path.join(ROOT_DIR, "STRICTRECORDS", "app_logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(ROOT_DIR, "android", "app", "src", "main", "res", "drawable", "app_logo.png")
    ico_path = os.path.join(ROOT_DIR, "app_icon.ico")
    dist_ico = os.path.join(DIST_DIR, "app_icon.ico")

    os.makedirs(DIST_DIR, exist_ok=True)
    img = Image.open(logo_path)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    shutil.copy2(ico_path, dist_ico)
    print(f"  [OK] Saved ICO: {ico_path} and {dist_ico}")
    return ico_path


def _splash_font(size: int, bold: bool = False):
    font_names = [
        r"C:\Windows\Fonts\seguisb.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for font_path in font_names:
        if os.path.isfile(font_path):
            try:
                return ImageFont.truetype(font_path, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def generate_splash_image():
    """Create the branded one-file boot splash used while StrictRecords starts."""
    print("\n[1b/5] Preparing the Windows startup splash screen...")
    os.makedirs(DIST_DIR, exist_ok=True)
    splash_path = os.path.join(DIST_DIR, "StrictRecords-splash.png")
    logo_path = os.path.join(ROOT_DIR, "app_logo.png")
    if not os.path.isfile(logo_path):
        logo_path = os.path.join(ROOT_DIR, "android", "app", "src", "main", "res", "drawable", "app_logo.png")

    width, height = 760, 480
    image = Image.new("RGB", (width, height), "#0b0f19")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=26, fill="#101722", outline="#29364a", width=2)
    draw.rounded_rectangle((38, 38, 42, height - 38), radius=2, fill="#00d69b")

    # Faint decorative rings keep the splash lively without competing with the brand mark.
    draw.ellipse((58, 104, 282, 328), outline="#1c3d3d", width=2)
    draw.ellipse((72, 118, 268, 314), outline="#173039", width=1)
    if os.path.isfile(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((188, 188), Image.Resampling.LANCZOS)
            image.paste(logo, ((width - logo.width) // 2 - 210, (height - logo.height) // 2 - 10), logo)
        except Exception as exc:
            print(f"  [WARN] Could not add splash logo: {exc}")

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((324, 96, 502, 128), radius=16, fill="#12342d", outline="#1c5c4b", width=1)
    draw.ellipse((340, 108, 348, 116), fill="#00d69b")
    draw.text((360, 101), "FOCUS WORKSPACE", font=_splash_font(12, bold=True), fill="#47e3b2")
    draw.text((324, 151), "StrictRecords", font=_splash_font(37, bold=True), fill="#f5f7fb")
    draw.text((327, 210), "Focus. Evidence. Progress.", font=_splash_font(19), fill="#94a3b8")
    draw.line((326, 258, 690, 258), fill="#29364a", width=1)
    draw.text((326, 285), "Your workspace is getting ready", font=_splash_font(17, bold=True), fill="#e2e8f0")
    draw.text((326, 318), "Attendance  ·  Tasks  ·  Rewards  ·  Device sync", font=_splash_font(13), fill="#8392a9")

    # The bootloader overlays live status text here while the backend and dashboard load.
    draw.rounded_rectangle((326, 376, 690, 384), radius=4, fill="#263247")
    for index, color in enumerate(("#00d69b", "#246b59", "#246b59", "#263247", "#263247")):
        x0 = 326 + index * 74
        draw.rounded_rectangle((x0, 376, x0 + 66, 384), radius=4, fill=color)
    draw.text((326, 405), "SECURE LOCAL WORKSPACE", font=_splash_font(10, bold=True), fill="#55708a")
    image.save(splash_path, format="PNG", optimize=True)
    print(f"  [OK] Startup splash prepared: {splash_path}")
    return splash_path


def build_windows_exe(ico_path: str, splash_path: str, dist_dir: str = DIST_DIR):
    print("\n[2/5] Building unified Windows executable with startup splash (StrictRoutines.exe)...")
    if not os.path.isfile(ico_path):
        raise FileNotFoundError(f"Windows app icon is missing: {ico_path}")
    os.makedirs(dist_dir, exist_ok=True)
    from build_strict_records_desktop import build_strict_routines_exe
    exe_path = build_strict_routines_exe(ico_path)
    # Purge any obsolete StrictRecords.exe files so only StrictRoutines.exe exists
    for old_exe in [
        os.path.join(dist_dir, "StrictRecords.exe"),
        os.path.join(ROOT_DIR, "StrictRecords.exe"),
        os.path.join(ROOT_DIR, "installer_dist", "StrictRecords.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\StrictRecords\StrictRecords.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\StrictRecords\StrictRecords.lnk"),
    ]:
        if os.path.exists(old_exe):
            try:
                os.remove(old_exe)
                print(f"  [CLEANUP] Purged obsolete file: {old_exe}")
            except Exception:
                pass
    return exe_path


def update_desktop_shortcut(exe_path: str, ico_path: str):
    print("\n[3/5] Updating Windows Desktop Shortcut...")
    abs_exe = os.path.abspath(exe_path)
    abs_ico = os.path.abspath(ico_path)
    ps_script = f"""
    $desktop1 = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
    $desktop2 = "C:\\Users\\lokad\\Desktop"
    $desktop3 = "C:\\Users\\lokad\\OneDrive\\Desktop"
    $wsh = New-Object -ComObject WScript.Shell

    foreach ($d in @($desktop1, $desktop2, $desktop3)) {{
        if ($d -and (Test-Path $d)) {{
            foreach ($old in @("Strict Records.lnk", "StrictRecords.lnk", "StrictRecords Desktop.lnk", "Exam Proctor.lnk")) {{
                $oldPath = Join-Path $d $old
                if (Test-Path $oldPath) {{
                    Remove-Item -Path $oldPath -Force -ErrorAction SilentlyContinue
                    Write-Host "  [CLEANUP] Removed duplicate: $oldPath"
                }}
            }}
            $lnk = Join-Path $d "StrictRoutines.lnk"
            $sc = $wsh.CreateShortcut($lnk)
            $sc.TargetPath = "{abs_exe}"
            $sc.WorkingDirectory = "{ROOT_DIR}"
            $sc.IconLocation = "{abs_ico},0"
            $sc.Description = "StrictRoutines — Daily Discipline Operating System"
            $sc.Save()
            Write-Host "  [OK] Updated Desktop Shortcut: $lnk"

        }}
    }}
    """
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)


def package_source_archive():
    print("\n[4/5] Packaging clean Source Code archive (StrictRecords-source.zip)...")
    zip_path = os.path.join(DIST_DIR, "StrictRecords-source.zip")
    temporary_zip_path = zip_path + ".tmp"
    excludes_dirs = {
        ".git",
        ".gradle",
        "build",
        "build_pyinstaller",
        "build_onedir",
        "build_pyinstaller_onedir",
        "build_installer_work",
        "build_release_work",
        "__pycache__",
        ".codex-work",
        ".tmp",
        ".dist",
        ".idea",
        ".vscode",
        ".system_generated",
        ".pytest_cache",
        ".venv",
        "venv",
        "dist",
        "new_model",
        "scratch",
        "STRICTRECORDS",
        "installer_dist",
        "installer_payload",
        "release",
        "releases",
    }
    excludes_exts = {
        ".pyc",
        ".apk",
        ".exe",
        ".log",
        ".tmp",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".db-wal",
        ".db-shm",
        ".sqlite-wal",
        ".sqlite-shm",
    }

    with zipfile.ZipFile(temporary_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [
                d for d in dirs
                if d not in excludes_dirs
                and not d.startswith(".")
                and not d.lower().startswith(("dist_", ".win_build_"))
                and not d.lower().startswith("build_")
            ]
            for f in files:
                lower_name = f.lower()
                if lower_name.startswith("temp_"):
                    continue
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, ROOT_DIR)
                suffix = os.path.splitext(f)[1].lower()
                if (
                    rel_p.startswith(".dist")
                    or suffix in excludes_exts
                    or any(marker in lower_name for marker in (".db-", ".db.", ".sqlite-", ".sqlite."))
                ):
                    continue
                # Root-level screenshots are local QA artifacts, not project assets.
                if os.path.dirname(rel_p) in ("", ".") and os.path.splitext(f)[1].lower() in {".png", ".jpg", ".jpeg", ".webp"} and lower_name != "app_logo.png":
                    continue
                zf.write(full_p, rel_p)

    os.replace(temporary_zip_path, zip_path)

    print(f"  [OK] Source archive generated: {zip_path} ({os.path.getsize(zip_path) / (1024 * 1024):.2f} MB)")
    return zip_path


def _android_build_metadata():
    gradle_path = os.path.join(ROOT_DIR, "android", "app", "build.gradle")
    with open(gradle_path, "r", encoding="utf-8") as f:
        gradle_text = f.read()
    version_name = re.search(r'(?m)^\s*versionName\s+["\']([^"\']+)["\']', gradle_text)
    version_code = re.search(r"(?m)^\s*versionCode\s+(\d+)", gradle_text)
    app_id = re.search(r'(?m)^\s*applicationId\s+["\']([^"\']+)["\']', gradle_text)
    if not version_name or not version_code or not app_id:
        raise RuntimeError("Could not read Android package/version metadata from android/app/build.gradle")
    return {
        "version_name": version_name.group(1),
        "version_code": int(version_code.group(1)),
        "application_id": app_id.group(1),
    }


def write_manifest(exe_path: str, source_zip: str):
    print("\n[5/5] Generating release manifest (.dist/manifest.json)...")
    apk_path = os.path.join(DIST_DIR, "StrictRecords-debug.apk")
    for required_path in (exe_path, apk_path, source_zip):
        if not os.path.isfile(required_path) or os.path.getsize(required_path) <= 0:
            raise RuntimeError(f"Required distribution artifact is missing or empty: {required_path}")
    android = _android_build_metadata()

    manifest = {
        "release_name": f"StrictRecords-v{android['version_name']}-beta-build",
        "release_type": "development build; Android artifact is debug-signed",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": "The source archive excludes local databases, proof records, and generated build outputs.",
        "artifacts": {
            "windows_executable": {
                "file": os.path.basename(exe_path),
                "size_bytes": os.path.getsize(exe_path),
                "sha256": compute_sha256(exe_path),
            },
            "android_apk": {
                "file": os.path.basename(apk_path),
                "size_bytes": os.path.getsize(apk_path),
                "sha256": compute_sha256(apk_path),
                "build_type": "debug",
                **android,
            },
            "source_code": {
                "file": os.path.basename(source_zip),
                "size_bytes": os.path.getsize(source_zip),
                "sha256": compute_sha256(source_zip),
            },
        },
    }

    manifest_path = os.path.join(DIST_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    manifest_android = {
        "app_id": "com.strictrecords.app",
        "platform": "ANDROID",
        "channel": "PRODUCTION",
        "version_name": android["version_name"],
        "version_code": android["version_code"],
        "min_supported_version": 10000,
        "manifest_version": 1,
        "release_notes": "Google Drive cloud sync, unified append-only point ledger, and verified APK updates.",
        "file_name": os.path.basename(apk_path),
        "file_size_bytes": os.path.getsize(apk_path),
        "sha256": compute_sha256(apk_path),
        "published_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with open(os.path.join(DIST_DIR, "manifest-android.json"), "w", encoding="utf-8") as f:
        json.dump(manifest_android, f, indent=2)

    manifest_windows = {
        "app_id": "com.strictrecords.desktop",
        "platform": "WINDOWS",
        "channel": "PRODUCTION",
        "version_name": "1.16.0",
        "version_code": 11600,
        "min_supported_version": 10000,
        "manifest_version": 1,
        "release_notes": "Google Drive cloud sync, unified append-only point ledger, and verified Windows updates.",
        "file_name": os.path.basename(exe_path),
        "file_size_bytes": os.path.getsize(exe_path),
        "sha256": compute_sha256(exe_path),
        "published_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with open(os.path.join(DIST_DIR, "manifest-windows.json"), "w", encoding="utf-8") as f:
        json.dump(manifest_windows, f, indent=2)

    print(f"  [OK] Manifest recorded: {manifest_path}, manifest-android.json, manifest-windows.json")
    print("\n" + "=" * 65)
    print("ALL DISTRIBUTION ARTIFACTS VERIFIED AND PACKAGED IN .dist/")
    print("=" * 65)


def copy_android_apk():
    print("\n[3/6] Building and syncing the current Android Debug APK...")
    if os.name == "nt":
        gradle_wrapper = os.path.join(ROOT_DIR, "android", "gradlew.bat")
        if not os.path.isfile(gradle_wrapper):
            raise FileNotFoundError(f"Android Gradle wrapper is missing: {gradle_wrapper}")
        subprocess.run(
            ["cmd.exe", "/d", "/c", gradle_wrapper, "assembleDebug"],
            cwd=os.path.join(ROOT_DIR, "android"),
            check=True,
        )
    else:
        gradle_wrapper = os.path.join(ROOT_DIR, "android", "gradlew")
        if not os.path.isfile(gradle_wrapper):
            raise FileNotFoundError(f"Android Gradle wrapper is missing: {gradle_wrapper}")
        subprocess.run(
            [gradle_wrapper, "assembleDebug"],
            cwd=os.path.join(ROOT_DIR, "android"),
            check=True,
        )

    app_build_dir = os.path.join(ROOT_DIR, "android", "app", "build")
    gradle_file = os.path.join(ROOT_DIR, "android", "app", "build.gradle")
    with open(gradle_file, "r", encoding="utf-8") as f:
        gradle_text = f.read()
    configured_build_dir = re.search(r'(?m)^\s*buildDir\s*=\s*file\(\s*["\']([^"\']+)["\']\s*\)', gradle_text)
    if configured_build_dir:
        configured_path = configured_build_dir.group(1)
        app_build_dir = configured_path if os.path.isabs(configured_path) else os.path.join(ROOT_DIR, "android", "app", configured_path)
    src_apk = os.path.join(app_build_dir, "outputs", "apk", "debug", "app-debug.apk")
    if not os.path.isfile(src_apk):
        alt_apk = os.path.join(os.path.expanduser("~"), ".strictrecords_build", "app", "outputs", "apk", "debug", "app-debug.apk")
        if os.path.isfile(alt_apk):
            src_apk = alt_apk
    dst_apk = os.path.join(DIST_DIR, "StrictRecords-debug.apk")
    if not os.path.isfile(src_apk):
        raise FileNotFoundError(f"Gradle completed without producing the configured APK: {src_apk}")
    shutil.copy2(src_apk, dst_apk)
    print(f"  [OK] Copied current Android APK: {dst_apk} ({os.path.getsize(dst_apk) / (1024 * 1024):.2f} MB)")
    return dst_apk


def main():
    os.makedirs(DIST_DIR, exist_ok=True)
    rebuild_exe = "--rebuild-exe" in sys.argv or "--force" in sys.argv or True  # Always keep exe updated with latest code
    ico_path = generate_icon()
    splash_path = generate_splash_image()
    exe_path = os.path.join(DIST_DIR, "StrictRoutines.exe")
    if rebuild_exe or not os.path.exists(exe_path):
        exe_path = build_windows_exe(ico_path, splash_path)
    else:
        print("\n[2/6] Standalone executable exists in .dist/ (Skipping re-compilation).")

    copy_android_apk()
    update_desktop_shortcut(exe_path, ico_path)
    source_zip = package_source_archive()
    write_manifest(exe_path, source_zip)


if __name__ == "__main__":
    main()
