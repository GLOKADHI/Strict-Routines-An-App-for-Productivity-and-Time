"""
STRICTRECORDS & EXAM PROCTOR — Native Windows Installer
Packages and installs ExamProctor.exe to Local AppData,
creates Start Menu and Desktop shortcuts, registers registry uninstall,
and launches the application with zero terminal windows.
"""

import ctypes
import os
import shutil
import subprocess
import sys
import winreg

APP_NAME = "Exam Proctor"
REG_NAME = "ExamProctor"
VERSION = "2.4.1"
PUBLISHER = "StrictRecords AI Team"


def get_bundle_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def create_shortcut(target_path: str, shortcut_path: str, ico_path: str, work_dir: str, description: str):
    powershell_code = f"""
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut("{shortcut_path}")
    $sc.TargetPath = "{target_path}"
    $sc.WorkingDirectory = "{work_dir}"
    $sc.IconLocation = "{ico_path},0"
    $sc.Description = "{description}"
    $sc.Save()
    """
    subprocess.run(["powershell", "-NoProfile", "-Command", powershell_code], capture_output=True)


def register_uninstaller(install_dir: str, ico_path: str):
    reg_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{REG_NAME}"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, f"STRICTRECORDS — {APP_NAME}")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, f"{ico_path},0")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install_dir)
            uninstall_cmd = f'powershell -WindowStyle Hidden -Command "Remove-Item -Recurse -Force \'{install_dir}\'"'
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall_cmd)
    except Exception as e:
        pass


def install():
    bundle_dir = get_bundle_dir()

    # Locate source payload (ExamProctor.bin or ExamProctor.exe)
    source_exe = os.path.join(bundle_dir, "ExamProctor.bin")
    if not os.path.exists(source_exe):
        source_exe = os.path.join(bundle_dir, "ExamProctor.exe")
    if not os.path.exists(source_exe):
        # Look in dist dir if run from repo root
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dist", "ExamProctor.exe")
        if os.path.exists(candidate):
            source_exe = candidate
        else:
            candidate = os.path.join(os.path.dirname(sys.executable), "ExamProctor.exe")
            if os.path.exists(candidate):
                source_exe = candidate

    source_ico = os.path.join(bundle_dir, "app_icon.ico")
    if not os.path.exists(source_ico):
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
        if os.path.exists(candidate):
            source_ico = candidate

    local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    install_dir = os.path.join(local_app_data, "ExamProctor")
    os.makedirs(install_dir, exist_ok=True)

    target_exe = os.path.join(install_dir, "ExamProctor.exe")
    target_ico = os.path.join(install_dir, "app_icon.ico")

    # Terminate existing instance if running during reinstall
    subprocess.run(["taskkill", "/F", "/IM", "ExamProctor.exe"], capture_output=True)

    # Copy binary and icon
    if os.path.exists(source_exe):
        shutil.copy2(source_exe, target_exe)
    if os.path.exists(source_ico):
        shutil.copy2(source_ico, target_ico)

    # Create Desktop Shortcut
    desktop_dir = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\lokad"), "Desktop")
    if os.path.exists(desktop_dir):
        desktop_lnk = os.path.join(desktop_dir, f"{APP_NAME}.lnk")
        create_shortcut(
            target_path=target_exe,
            shortcut_path=desktop_lnk,
            ico_path=target_ico,
            work_dir=install_dir,
            description="StrictRecords — Daily Discipline Operating System & Exam Proctor AI",
        )

    # Create Start Menu Shortcut
    app_data = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
    start_menu_dir = os.path.join(app_data, r"Microsoft\Windows\Start Menu\Programs\ExamProctor")
    os.makedirs(start_menu_dir, exist_ok=True)
    start_menu_lnk = os.path.join(start_menu_dir, f"{APP_NAME}.lnk")
    create_shortcut(
        target_path=target_exe,
        shortcut_path=start_menu_lnk,
        ico_path=target_ico,
        work_dir=install_dir,
        description="StrictRecords — Daily Discipline Operating System & Exam Proctor AI",
    )

    # Register in Windows Add/Remove programs
    register_uninstaller(install_dir, target_ico)

    # Show native Windows success dialog
    msg = (
        f"STRICTRECORDS & EXAM PROCTOR\n\n"
        f"Version {VERSION} installed successfully!\n\n"
        f"• Location: {install_dir}\n"
        f"• Desktop shortcut created\n"
        f"• Start Menu shortcut created\n\n"
        f"Launching {APP_NAME}..."
    )
    ctypes.windll.user32.MessageBoxW(0, msg, "Exam Proctor Setup", 0x00000040 | 0x00000000)

    # Launch newly installed application detached
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen([target_exe], creationflags=DETACHED_PROCESS, close_fds=True)


if __name__ == "__main__":
    install()
