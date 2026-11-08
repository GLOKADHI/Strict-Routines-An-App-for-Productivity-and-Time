using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace StrictRoutines
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            try
            {
                string appDir = AppDomain.CurrentDomain.BaseDirectory;
                if (!File.Exists(Path.Combine(appDir, "desktop_app.py")))
                {
                    string workspaceDir = @"c:\Users\lokad\Music\Practical\StrictRecords";
                    if (Directory.Exists(workspaceDir))
                    {
                        appDir = workspaceDir;
                    }
                }

                string scriptPath = Path.Combine(appDir, "desktop_app.py");

                string pythonw = @"C:\Program Files\Python314\pythonw.exe";
                if (!File.Exists(pythonw))
                {
                    pythonw = "pythonw.exe";
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonw;
                string extraArgs = args.Length > 0 ? " " + string.Join(" ", args) : "";
                psi.Arguments = "\"" + scriptPath + "\"" + extraArgs;
                psi.WorkingDirectory = appDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to launch StrictRoutines: " + ex.Message, "StrictRoutines Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
