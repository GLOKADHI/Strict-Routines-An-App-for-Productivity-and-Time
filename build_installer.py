"""
Installer build runner for StrictRecords & Exam Proctor
"""
import os
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
script = os.path.join(ROOT_DIR, "build_native_installer.py")
res = subprocess.run([sys.executable, script])
sys.exit(res.returncode)
