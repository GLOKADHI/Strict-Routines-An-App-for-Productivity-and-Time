"""
STRICTROUTINES — Unified Desktop Application & Installer Builder
Builds:
1. StrictRoutines.exe (Standalone Edge WebView2 Windowed Executable, Zero Console)
2. StrictRoutinesSetup.exe (Standalone Windows Installer with Shortcuts & HKCU Uninstaller)
3. Updates manifest.json and desktop shortcuts
"""

import os
import shutil
import subprocess
import sys
import hashlib
import json
from datetime import datetime
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, ".dist")
WORK_DIR = os.path.join(ROOT_DIR, "build_pyinstaller")
CSC_PATH = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

os.makedirs(DIST_DIR, exist_ok=True)
os.makedirs(WORK_DIR, exist_ok=True)


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


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


def ensure_splash_image() -> str:
    splash_path = os.path.join(DIST_DIR, "StrictRecords-splash.png")
    assets_splash = os.path.join(ROOT_DIR, "assets", "StrictRecords-splash.png")
    if os.path.isfile(splash_path) and os.path.getsize(splash_path) > 1000:
        if not os.path.isfile(assets_splash):
            try:
                shutil.copy2(splash_path, assets_splash)
            except Exception:
                pass
        return splash_path
    if os.path.isfile(assets_splash) and os.path.getsize(assets_splash) > 1000:
        shutil.copy2(assets_splash, splash_path)
        return splash_path

    try:
        from build_release import generate_splash_image
        return generate_splash_image()
    except Exception as e:
        print(f"  [WARN] Could not generate splash image: {e}")
        return splash_path


def build_strict_routines_exe(ico_path: str):
    build_native_browser_bridge()
    splash_path = ensure_splash_image()
    print("\n[1/3] Compiling StrictRoutines.exe (Native Windows Windowed Launcher via csc.exe)...")
    launcher_cs = os.path.join(ROOT_DIR, "launcher", "StrictRecordsLauncher.cs")
    staged_exe = os.path.join(ROOT_DIR, "installer_dist", "StrictRoutines.exe")
    os.makedirs(os.path.join(ROOT_DIR, "installer_dist"), exist_ok=True)

    csc = CSC_PATH if os.path.exists(CSC_PATH) else "csc.exe"
    cmd = [
        csc,
        "/target:winexe",
        f"/win32icon:{ico_path}",
        f"/resource:{splash_path},StrictRecords.splash.png",
        f"/out:{staged_exe}",
        "/reference:System.dll",
        "/reference:System.Core.dll",
        "/reference:System.Drawing.dll",
        "/reference:System.Windows.Forms.dll",
        "/optimize+",
        launcher_cs
    ]

    print("Running CSC Compiler:", " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode != 0:
        if os.path.exists(staged_exe):
            print(f"  [NOTE] {staged_exe} is currently in use by running process; existing binary retained.")
        else:
            raise RuntimeError(f"C# Compiler failed with code {res.returncode}")

    if not os.path.exists(staged_exe):
        raise FileNotFoundError(f"Expected binary {staged_exe} not found.")

    # Deploy to LocalAppData, root, and .dist
    local_app_dir = os.path.expandvars(r"%LOCALAPPDATA%\StrictRoutines")
    local_app_records = os.path.expandvars(r"%LOCALAPPDATA%\StrictRecords")
    os.makedirs(local_app_dir, exist_ok=True)
    os.makedirs(local_app_records, exist_ok=True)
    local_exe = os.path.join(local_app_dir, "StrictRoutines.exe")
    local_rec_exe = os.path.join(local_app_records, "StrictRoutines.exe")
    root_exe = os.path.join(ROOT_DIR, "StrictRoutines.exe")
    dist_desk_exe = os.path.join(DIST_DIR, "StrictRoutines.exe")

    for dest in [local_exe, local_rec_exe, root_exe, dist_desk_exe]:
        try:
            shutil.copy2(staged_exe, dest)
            print(f"  [DEPLOY] Copied to: {dest}")
        except Exception as e:
            print(f"  [DEPLOY NOTE] {dest}: {e}")

    # Synchronize complete codebase to local_app_dir so standalone execution is always 100% up to date
    for app_d in [local_app_dir, local_app_records]:
        try:
            shutil.copy2(splash_path, os.path.join(app_d, "StrictRecords-splash.png"))
            shutil.copy2(splash_path, os.path.join(ROOT_DIR, "StrictRecords-splash.png"))
        except Exception:
            pass
        for fname in ["desktop_app.py", "app.py", "app_icon.ico", "app_logo.png", "StrictRecords-splash.png", "StrictRecordsBrowserBridge.dll", "StrictRecords-connect.html"]:
            src_f = os.path.join(ROOT_DIR, fname)
            if os.path.exists(src_f):
                try:
                    shutil.copy2(src_f, os.path.join(app_d, fname))
                except Exception:
                    pass
        for dname in ["core", "windows", "assets", "browser_extension", "shared", "browser_extension_monitor"]:
            src_d = os.path.join(ROOT_DIR, dname)
            dst_d = os.path.join(app_d, dname)
            if os.path.exists(src_d):
                try:
                    shutil.copytree(src_d, dst_d, dirs_exist_ok=True)
                except Exception:
                    pass

    size_mb = os.path.getsize(staged_exe) / (1024 * 1024)
    print(f"  [SUCCESS] StrictRoutines.exe generated: {staged_exe} ({size_mb:.2f} MB)")
    return staged_exe


def build_native_browser_bridge(output=None):
    """Compile the app's supported Windows UI Automation browser adapter."""
    framework = os.path.dirname(CSC_PATH)
    output = output or os.path.join(ROOT_DIR, "StrictRecordsBrowserBridge.dll")
    try:
        subprocess.run([
            CSC_PATH, "/target:library", "/optimize+", "/out:" + output,
            "/reference:System.dll", "/reference:" + os.path.join(framework, "WPF", "WindowsBase.dll"),
            "/reference:" + os.path.join(framework, "WPF", "UIAutomationClient.dll"),
            "/reference:" + os.path.join(framework, "WPF", "UIAutomationTypes.dll"),
            os.path.join(ROOT_DIR, "launcher", "StrictRecordsBrowserBridge.cs"),
        ], check=True)
    except subprocess.CalledProcessError as err:
        if os.path.exists(output):
            print(f"  [NOTE] {output} is currently in use by running process; existing DLL retained.")
        else:
            raise err
    return output


def build_strict_routines_installer(exe_path: str, ico_path: str):
    print("\n[2/3] Compiling StrictRoutinesSetup.exe (Native Windows Installer)...")
    cs_file = os.path.join(ROOT_DIR, "StrictRoutinesInstaller.cs")

    cs_code = r"""using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

namespace StrictRoutinesInstaller
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            try
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string installDir = Path.Combine(localAppData, "StrictRoutines");
                Directory.CreateDirectory(installDir);

                string targetExe = Path.Combine(installDir, "StrictRoutines.exe");
                string targetIco = Path.Combine(installDir, "app_icon.ico");

                // Terminate any running StrictRoutines or StrictRecords processes
                try
                {
                    foreach (var proc in Process.GetProcessesByName("StrictRoutines"))
                    {
                        proc.Kill();
                        proc.WaitForExit(2000);
                    }
                    foreach (var proc in Process.GetProcessesByName("StrictRecords"))
                    {
                        proc.Kill();
                        proc.WaitForExit(2000);
                    }
                }
                catch { }

                // Extract embedded resources
                var assembly = Assembly.GetExecutingAssembly();
                ExtractResource(assembly, "StrictRoutines.exe", targetExe);
                ExtractResource(assembly, "app_icon.ico", targetIco);

                // Create Desktop Shortcut (supporting standard Desktop and OneDrive Desktop)
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string desktopShortcut = Path.Combine(desktop, "StrictRoutines.lnk");
                CreateShortcut(targetExe, desktopShortcut, targetIco, installDir, "StrictRoutines — Daily Discipline Operating System");

                string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                string oneDriveDesktop = Path.Combine(userProfile, "OneDrive", "Desktop");
                if (Directory.Exists(oneDriveDesktop))
                {
                    string odShortcut = Path.Combine(oneDriveDesktop, "StrictRoutines.lnk");
                    CreateShortcut(targetExe, odShortcut, targetIco, installDir, "StrictRoutines — Daily Discipline Operating System");
                }

                // Clean up old Start Menu Programs\StrictRecords directory
                try
                {
                    string oldStartDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "StrictRecords");
                    if (Directory.Exists(oldStartDir))
                    {
                        Directory.Delete(oldStartDir, true);
                    }
                }
                catch { }

                // Create single Start Menu Shortcut under Programs\StrictRoutines
                string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "StrictRoutines");
                Directory.CreateDirectory(startMenu);
                string startMenuShortcut = Path.Combine(startMenu, "StrictRoutines.lnk");
                CreateShortcut(targetExe, startMenuShortcut, targetIco, installDir, "StrictRoutines — Daily Discipline Operating System");

                // Register Windows Uninstaller in HKCU
                RegisterUninstaller(installDir, targetIco);

                // Show confirmation message
                MessageBox.Show(
                    "STRICTROUTINES \u2022 DESKTOP APPLICATION\n\nInstalled successfully!\n\n" +
                    "\u2022 Location: " + installDir + "\n" +
                    "\u2022 Desktop Shortcut Created\n" +
                    "\u2022 Start Menu Shortcut Created\n\nLaunching StrictRoutines...",
                    "StrictRoutines Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );

                // Launch installed application detached
                ProcessStartInfo psi = new ProcessStartInfo(targetExe);
                psi.WorkingDirectory = installDir;
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Installation failed:\n" + ex.Message, "StrictRoutines Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        static void ExtractResource(Assembly assembly, string resourceName, string targetPath)
        {
            string currentDir = AppDomain.CurrentDomain.BaseDirectory;
            string localCandidate = Path.Combine(currentDir, resourceName);
            if (File.Exists(localCandidate))
            {
                File.Copy(localCandidate, targetPath, true);
                return;
            }

            foreach (string name in assembly.GetManifestResourceNames())
            {
                if (name.EndsWith(resourceName, StringComparison.OrdinalIgnoreCase))
                {
                    using (Stream stream = assembly.GetManifestResourceStream(name))
                    {
                        if (stream != null)
                        {
                            using (FileStream fs = new FileStream(targetPath, FileMode.Create, FileAccess.Write))
                            {
                                stream.CopyTo(fs);
                            }
                            return;
                        }
                    }
                }
            }
        }

        static void CreateShortcut(string targetPath, string shortcutPath, string iconPath, string workDir, string desc)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                dynamic shell = Activator.CreateInstance(shellType);
                dynamic shortcut = shell.CreateShortcut(shortcutPath);
                shortcut.TargetPath = targetPath;
                shortcut.WorkingDirectory = workDir;
                shortcut.IconLocation = iconPath + ",0";
                shortcut.Description = desc;
                shortcut.Save();
            }
            catch { }
        }

        static void RegisterUninstaller(string installDir, string iconPath)
        {
            try
            {
                string keyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\StrictRoutines";
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(keyPath))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", "StrictRoutines");
                        key.SetValue("DisplayVersion", "2.4.1");
                        key.SetValue("Publisher", "StrictRecords Team");
                        key.SetValue("DisplayIcon", iconPath + ",0");
                        key.SetValue("InstallLocation", installDir);
                        key.SetValue("UninstallString", "powershell.exe -WindowStyle Hidden -Command \"Remove-Item -Recurse -Force '" + installDir + "'\"");
                    }
                }
            }
            catch { }
        }
    }
}
"""

    with open(cs_file, "w", encoding="utf-8") as f:
        f.write(cs_code)

    primary_out = os.path.join(DIST_DIR, "StrictRoutinesSetup.exe")
    alt_out = os.path.join(DIST_DIR, "StrictRoutines_Setup.exe")

    cmd = [
        CSC_PATH,
        "/target:winexe",
        f"/out:{primary_out}",
        f"/win32icon:{ico_path}",
        f"/resource:{exe_path},StrictRoutines.exe",
        f"/resource:{ico_path},app_icon.ico",
        "/reference:System.dll",
        "/reference:System.Windows.Forms.dll",
        "/optimize+",
        cs_file,
    ]

    print("Compiling installer with csc.exe:")
    print(" ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Stdout:", res.stdout)
        print("Stderr:", res.stderr)
        raise RuntimeError("C# Compilation of StrictRoutinesSetup failed!")

    shutil.copy2(primary_out, alt_out)
    if os.path.exists(cs_file):
        os.remove(cs_file)

    size_mb = os.path.getsize(primary_out) / (1024 * 1024)
    print(f"  [SUCCESS] StrictRoutinesSetup.exe generated: {primary_out} ({size_mb:.2f} MB)")
    return primary_out


def update_desktop_shortcuts(exe_path: str, ico_path: str):
    print("\n[3/3] Updating Windows Desktop & Start Menu Shortcuts...")
    abs_exe = os.path.abspath(exe_path)
    abs_ico = os.path.abspath(ico_path)
    work_dir = os.path.dirname(abs_exe)

    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")

        target_installed_exe = abs_exe
        target_icon = abs_ico
        target_dir = ROOT_DIR

        desktops = [
            r"C:\Users\lokad\OneDrive\Desktop",
            os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
        ]

        for d in desktops:
            if os.path.exists(d):
                # Clean up legacy / duplicate shortcuts
                for old in ["Exam Proctor.lnk", "StrictRecords Desktop.lnk", "Strict Records.lnk", "StrictRecords.lnk"]:
                    old_path = os.path.join(d, old)
                    if os.path.exists(old_path):
                        try:
                            os.remove(old_path)
                            print(f"  [CLEANUP] Removed duplicate desktop shortcut: {old_path}")
                        except Exception:
                            pass

                # Create the single official StrictRoutines desktop shortcut
                try:
                    lnk_path = os.path.join(d, "StrictRoutines.lnk")
                    sc = shell.CreateShortcut(lnk_path)
                    sc.TargetPath = target_installed_exe
                    sc.WorkingDirectory = target_dir
                    sc.IconLocation = f"{target_icon},0"
                    sc.Description = "StrictRoutines — Daily Discipline Operating System"
                    sc.Save()
                    print(f"  [OK] Single Desktop Shortcut verified: {lnk_path}")
                except Exception as e:
                    print(f"  Note creating desktop shortcut: {e}")

        # Start menu: Purge old Programs\StrictRecords directory to prevent duplicates in Windows Search
        start_programs = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")
        old_records_folder = os.path.join(start_programs, "StrictRecords")
        if os.path.exists(old_records_folder):
            try:
                shutil.rmtree(old_records_folder, ignore_errors=True)
                print(f"  [CLEANUP] Deleted redundant Start Menu folder: {old_records_folder}")
            except Exception:
                pass

        # Cleanup obsolete StrictRecords.exe and shortcuts from LocalAppData Programs
        local_programs_records = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "StrictRecords")
        for stale in ["StrictRecords.exe", "StrictRecords.lnk"]:
            stale_f = os.path.join(local_programs_records, stale)
            if os.path.exists(stale_f):
                try:
                    os.remove(stale_f)
                    print(f"  [CLEANUP] Removed stale file: {stale_f}")
                except Exception:
                    pass
        for stale_exe in [
            os.path.join(ROOT_DIR, "StrictRecords.exe"),
            os.path.join(ROOT_DIR, "installer_dist", "StrictRecords.exe"),
            os.path.join(DIST_DIR, "StrictRecords.exe"),
        ]:
            if os.path.exists(stale_exe):
                try:
                    os.remove(stale_exe)
                    print(f"  [CLEANUP] Removed obsolete binary: {stale_exe}")
                except Exception:
                    pass

        # Create single Start Menu shortcut in Programs\StrictRoutines
        start_menu = os.path.join(start_programs, "StrictRoutines")
        os.makedirs(start_menu, exist_ok=True)
        try:
            lnk = os.path.join(start_menu, "StrictRoutines.lnk")
            sc = shell.CreateShortcut(lnk)
            sc.TargetPath = target_installed_exe
            sc.WorkingDirectory = ROOT_DIR
            sc.IconLocation = f"{abs_ico},0"
            sc.Description = "StrictRoutines — Daily Discipline Operating System"
            sc.Save()
            print(f"  [OK] Single Start Menu Shortcut verified: {lnk}")
        except Exception as e:
            pass

    except Exception as ex:
        print(f"  Shortcut creation error: {ex}")


def update_manifest(exe_path: str, setup_path: str):
    apk_path = os.path.join(DIST_DIR, "StrictRecords-debug.apk")
    apk_info = {}
    if os.path.exists(apk_path):
        apk_info = {
            "file": os.path.basename(apk_path),
            "size_bytes": os.path.getsize(apk_path),
            "sha256": compute_sha256(apk_path),
        }

    src_zip = os.path.join(DIST_DIR, "StrictRecords-source.zip")
    src_info = {}
    if os.path.exists(src_zip):
        src_info = {
            "file": os.path.basename(src_zip),
            "size_bytes": os.path.getsize(src_zip),
            "sha256": compute_sha256(src_zip),
        }

    manifest = {
        "release_name": "StrictRoutines-v2.4.1-DesktopApp",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": "Every update maintains compiled .exe, .apk, and source archive in .dist/",
        "artifacts": {
            "windows_executable": {
                "file": "StrictRoutines.exe",
                "size_bytes": os.path.getsize(exe_path),
                "sha256": compute_sha256(exe_path),
            },
            "windows_installer": {
                "file": "StrictRoutinesSetup.exe",
                "size_bytes": os.path.getsize(setup_path),
                "sha256": compute_sha256(setup_path),
            },
            "android_apk": apk_info,
            "source_code": src_info,
        },
    }

    manifest_path = os.path.join(DIST_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  [OK] Manifest updated: {manifest_path}")


def main():
    ico_path = ensure_icon()
    exe_path = build_strict_routines_exe(ico_path)
    setup_path = build_strict_routines_installer(exe_path, ico_path)
    update_desktop_shortcuts(exe_path, ico_path)
    update_manifest(exe_path, setup_path)
    print("\n=======================================================")
    print("STRICTROUTINES DESKTOP BUILD COMPLETE!")
    print(f"EXE:       {exe_path}")
    print(f"INSTALLER: {setup_path}")
    print("=======================================================")


if __name__ == "__main__":
    main()
