"""
STRICTRECORDS & EXAM PROCTOR — Unified Build & Packaging Suite
Builds:
1. ExamProctor.exe (Standalone windowed executable with zero console window)
2. ExamProctorSetup.exe (Standalone Windows installer with Start Menu & Desktop shortcuts)
3. Updates distribution manifest & shortcuts
"""

import os
import shutil
import subprocess
import sys
import time
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, ".dist")
WORK_DIR = os.path.join(ROOT_DIR, "build_pyinstaller")


def ensure_icon():
    ico_path = os.path.join(ROOT_DIR, "app_icon.ico")
    if os.path.exists(ico_path) and os.path.getsize(ico_path) > 1000:
        return ico_path

    print("Generating app_icon.ico from source images...")
    logo_path = os.path.join(ROOT_DIR, "STRICTRECORDS", "app_logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(ROOT_DIR, "android", "app", "src", "main", "res", "drawable", "app_logo.png")
    img = Image.open(logo_path)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    return ico_path


def build_exam_proctor_exe(ico_path: str):
    print("\n[1/3] Compiling ExamProctor.exe (Windowed Standalone Binary)...")
    entry_script = os.path.join(ROOT_DIR, "desktop_app.py")
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "ExamProctor",
        "--icon",
        ico_path,
        "--onefile",
        "--windowed",
        "--noconsole",
        "--collect-all",
        "webview",
        "--hidden-import",
        "webview.platforms.winforms",
        "--hidden-import",
        "pythonnet",
        "--hidden-import",
        "clr",
        "--hidden-import",
        "win32gui",
        "--hidden-import",
        "win32con",
        "--hidden-import",
        "win32process",
        "--distpath",
        DIST_DIR,
        "--workpath",
        WORK_DIR,
        "--specpath",
        ROOT_DIR,
        "--noconfirm",
        entry_script,
    ]

    # Include data directory if exists
    data_dir = os.path.join(ROOT_DIR, "data")
    if os.path.exists(data_dir):
        cmd.extend(["--add-data", f"{data_dir};data"])

    print("Running PyInstaller:", " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with code {res.returncode}")

    exe_path = os.path.join(DIST_DIR, "ExamProctor.exe")
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"Expected binary {exe_path} not found.")

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print(f"  [SUCCESS] ExamProctor.exe generated: {exe_path} ({size_mb:.2f} MB)")
    return exe_path


if __name__ == "__main__":
    ico = ensure_icon()
    exe = build_exam_proctor_exe(ico)
    print("\nExamProctor.exe build complete!")
