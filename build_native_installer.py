"""
Build Native Windows Installer for Exam Proctor
Uses C# and csc.exe to generate a standalone Windows installer
that embeds ExamProctor.exe and app_icon.ico, installs to LocalAppData,
creates Desktop and Start Menu shortcuts, registers Windows uninstaller,
and launches ExamProctor.exe.
"""

import os
import shutil
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, ".dist")
ICO_PATH = os.path.join(ROOT_DIR, "app_icon.ico")
EXE_PATH = os.path.join(DIST_DIR, "ExamProctor.exe")
CSC_PATH = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

CS_SOURCE = r"""using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ExamProctorInstaller
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            try
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string installDir = Path.Combine(localAppData, "ExamProctor");
                Directory.CreateDirectory(installDir);

                string targetExe = Path.Combine(installDir, "ExamProctor.exe");
                string targetIco = Path.Combine(installDir, "app_icon.ico");

                // Terminate any running ExamProctor.exe
                try
                {
                    foreach (var proc in Process.GetProcessesByName("ExamProctor"))
                    {
                        proc.Kill();
                        proc.WaitForExit(2000);
                    }
                }
                catch { }

                // Extract embedded resources
                var assembly = Assembly.GetExecutingAssembly();
                ExtractResource(assembly, "ExamProctor.exe", targetExe);
                ExtractResource(assembly, "app_icon.ico", targetIco);

                // Create Desktop Shortcut
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string desktopShortcut = Path.Combine(desktop, "Exam Proctor.lnk");
                CreateShortcut(targetExe, desktopShortcut, targetIco, installDir, "StrictRecords Exam Proctor AI");

                // Create Start Menu Shortcut
                string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "ExamProctor");
                Directory.CreateDirectory(startMenu);
                string startMenuShortcut = Path.Combine(startMenu, "Exam Proctor.lnk");
                CreateShortcut(targetExe, startMenuShortcut, targetIco, installDir, "StrictRecords Exam Proctor AI");

                // Register Windows Uninstaller
                RegisterUninstaller(installDir, targetIco);

                // Show confirmation message
                MessageBox.Show(
                    "STRICTRECORDS \u2022 EXAM PROCTOR\n\nVersion 2.4.1 installed successfully!\n\n" +
                    "\u2022 Location: " + installDir + "\n" +
                    "\u2022 Desktop Shortcut Created\n" +
                    "\u2022 Start Menu Shortcut Created\n\nLaunching Exam Proctor...",
                    "Exam Proctor Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );

                // Launch installed application
                ProcessStartInfo psi = new ProcessStartInfo(targetExe);
                psi.WorkingDirectory = installDir;
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Installation failed:\n" + ex.Message, "Exam Proctor Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        static void ExtractResource(Assembly assembly, string resourceName, string targetPath)
        {
            // First check if file is alongside installer
            string currentDir = AppDomain.CurrentDomain.BaseDirectory;
            string localCandidate = Path.Combine(currentDir, resourceName);
            if (File.Exists(localCandidate))
            {
                File.Copy(localCandidate, targetPath, true);
                return;
            }

            // Otherwise extract from manifest resource
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
                string keyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\ExamProctor";
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(keyPath))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", "STRICTRECORDS \u2022 Exam Proctor");
                        key.SetValue("DisplayVersion", "2.4.1");
                        key.SetValue("Publisher", "StrictRecords AI Team");
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


def build():
    if not os.path.exists(EXE_PATH):
        raise FileNotFoundError(f"{EXE_PATH} not found!")

    cs_file = os.path.join(ROOT_DIR, "Installer.cs")
    with open(cs_file, "w", encoding="utf-8") as f:
        f.write(CS_SOURCE)

    # Output targets
    output_targets = [
        os.path.join(ROOT_DIR, "release", "ExamProctor_Install.exe"),
        os.path.join(DIST_DIR, "ExamProctor_Install.exe"),
        os.path.join(ROOT_DIR, "release", "Exam Proctor Setup.exe"),
        os.path.join(DIST_DIR, "Exam Proctor Setup.exe"),
        os.path.join(ROOT_DIR, "release", "Exam-Proctor-Setup.exe"),
        os.path.join(DIST_DIR, "Exam-Proctor-Setup.exe"),
    ]

    os.makedirs(os.path.join(ROOT_DIR, "release"), exist_ok=True)
    os.makedirs(DIST_DIR, exist_ok=True)

    primary_out = output_targets[0]

    cmd = [
        CSC_PATH,
        "/target:winexe",
        f"/out:{primary_out}",
        f"/win32icon:{ICO_PATH}",
        f"/resource:{EXE_PATH},ExamProctor.exe",
        f"/resource:{ICO_PATH},app_icon.ico",
        "/reference:System.dll",
        "/reference:System.Windows.Forms.dll",
        "/optimize+",
        cs_file,
    ]

    print("Compiling native installer with csc.exe:")
    print(" ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("Return code:", res.returncode)
    if res.stdout:
        print("Stdout:", res.stdout)
    if res.stderr:
        print("Stderr:", res.stderr)

    if res.returncode != 0:
        raise RuntimeError("C# Compilation failed!")

    size_mb = os.path.getsize(primary_out) / (1024 * 1024)
    print(f"\n[SUCCESS] Generated primary installer: {primary_out} ({size_mb:.2f} MB)")

    # Copy to all target names
    for target in output_targets[1:]:
        try:
            shutil.copy2(primary_out, target)
            print(f"  Copied to: {target}")
        except Exception as e:
            print(f"  Note: Copy to {target} failed: {e}")

    # Also clean up temporary cs file
    if os.path.exists(cs_file):
        os.remove(cs_file)


if __name__ == "__main__":
    build()
