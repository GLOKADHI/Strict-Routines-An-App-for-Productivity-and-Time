using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace StrictRecords
{
    static class Program
    {
        private const string LauncherMutexName = @"Local\StrictRecords_Launcher_Mutex_v2";
        private const string AppMutexName = @"Local\StrictRecords_App_Mutex_v2";
        private const string LegacyMutexName = @"Local\StrictRecords_SingleInstance_Mutex_v1";
        private const string SplashCloseEventName = @"Local\StrictRecords_Splash_Close_Event";
        private const string SplashStatusMmfName = @"Local\StrictRecords_Splash_Status";

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool BringWindowToTop(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern void SwitchToThisWindow(IntPtr hWnd, bool fAltTab);

        [DllImport("shell32.dll", SetLastError = true)]
        private static extern int SetCurrentProcessExplicitAppUserModelID([MarshalAs(UnmanagedType.LPWStr)] string AppID);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenMutex(uint dwDesiredAccess, bool bInheritHandle, string lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr hJob, int JobObjectInfoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        private const int SW_RESTORE = 9;
        private const int SW_SHOW = 5;
        private const uint SYNCHRONIZE = 0x00100000;
        private const int JobObjectExtendedLimitInformation = 9;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryLimit;
            public UIntPtr PeakJobMemoryLimit;
        }

        private static IntPtr _jobHandle = IntPtr.Zero;

        private static void AttachChildToJobObject(Process child)
        {
            try
            {
                _jobHandle = CreateJobObject(IntPtr.Zero, null);
                if (_jobHandle != IntPtr.Zero)
                {
                    JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                    int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                    IntPtr extendedInfoPtr = Marshal.AllocHGlobal(length);
                    try
                    {
                        Marshal.StructureToPtr(info, extendedInfoPtr, false);
                        if (SetInformationJobObject(_jobHandle, JobObjectExtendedLimitInformation, extendedInfoPtr, (uint)length))
                        {
                            AssignProcessToJobObject(_jobHandle, child.Handle);
                        }
                    }
                    finally
                    {
                        Marshal.FreeHGlobal(extendedInfoPtr);
                    }
                }
            }
            catch { }
        }

        private static bool CheckMutexExists(string name)
        {
            try
            {
                IntPtr h = OpenMutex(SYNCHRONIZE, false, name);
                if (h != IntPtr.Zero)
                {
                    CloseHandle(h);
                    return true;
                }
            }
            catch { }
            return false;
        }

        private static bool HasAppWindow()
        {
            try
            {
                string[] titles = new string[]
                {
                    "StrictRecords",
                    "StrictRecords • Desktop Application",
                    "StrictRoutines",
                    "StrictRoutines • Desktop Application"
                };
                foreach (string title in titles)
                {
                    IntPtr hwnd = FindWindow(null, title);
                    if (hwnd != IntPtr.Zero)
                    {
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }

                private static bool IsAppAlreadyRunning()
        {
            // 1. Check if named app mutex exists (authoritative proof of running GUI app)
            if (CheckMutexExists(AppMutexName) || CheckMutexExists(LegacyMutexName))
            {
                if (IsBackendAlive() || HasAppWindow())
                {
                    return true;
                }
            }

            // 2. Check if a top-level GUI window exists
            if (HasAppWindow())
            {
                return true;
            }

            // 3. Check if another active launcher process is running
            try
            {
                int myPid = Process.GetCurrentProcess().Id;
                string[] pNames = new string[] { "StrictRoutines", "StrictRecords", "StrictRecordsDesktop", "StrictRecordsApp" };
                foreach (string pn in pNames)
                {
                    Process[] procs = Process.GetProcessesByName(pn);
                    foreach (Process p in procs)
                    {
                        if (p.Id != myPid)
                        {
                            if (IsBackendAlive() || HasAppWindow())
                            {
                                return true;
                            }
                            try
                            {
                                Log("Terminating hung zombie process PID=" + p.Id + " (" + pn + ")");
                                p.Kill();
                                p.WaitForExit(1000);
                            }
                            catch { }
                        }
                    }
                }
            }
            catch { }

            return false;
        }

private static void NotifyUserDuplicateBlocked()
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8765/api/system/notify");
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Timeout = 1000;
                string json = "{\"title\":\"StrictRecords — Already Running\",\"message\":\"StrictRecords is already active. Only one instance can run at a time.\\nOpening your dashboard...\",\"warning\":false}";
                byte[] data = System.Text.Encoding.UTF8.GetBytes(json);
                req.ContentLength = data.Length;
                using (Stream stream = req.GetRequestStream())
                {
                    stream.Write(data, 0, data.Length);
                }
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse()) { }
            }
            catch { }
        }

        private static bool IsBackendAlive()
        {
            try
            {
                HttpWebRequest ping = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8765/api/system/health");
                ping.Method = "GET";
                ping.Timeout = 600;
                using (HttpWebResponse resp = (HttpWebResponse)ping.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private static bool TryActivateWindow()
        {
            bool ok = false;
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8765/api/system/activate_window");
                request.Method = "POST";
                request.ContentType = "application/json";
                request.Timeout = 1200;
                byte[] payload = System.Text.Encoding.UTF8.GetBytes("{}");
                request.ContentLength = payload.Length;
                using (Stream stream = request.GetRequestStream())
                {
                    stream.Write(payload, 0, payload.Length);
                }
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    ok = (response.StatusCode == HttpStatusCode.OK);
                }
            }
            catch { }

            RestoreWindowByWin32();
            return ok;
        }

        private static void RestoreWindowByWin32()
        {
            try
            {
                string[] titles = new string[]
                {
                    "StrictRecords",
                    "StrictRecords • Desktop Application",
                    "StrictRoutines",
                    "StrictRoutines • Desktop Application"
                };
                foreach (string title in titles)
                {
                    IntPtr hwnd = FindWindow(null, title);
                    if (hwnd != IntPtr.Zero)
                    {
                        ShowWindow(hwnd, SW_RESTORE);
                        ShowWindow(hwnd, SW_SHOW);
                        SetForegroundWindow(hwnd);
                        BringWindowToTop(hwnd);
                        try { SwitchToThisWindow(hwnd, true); } catch { }
                        break;
                    }
                }
            }
            catch { }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
        }

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        private static IntPtr FindChildDashboardWindow(int targetPid)
        {
            if (targetPid <= 0) return IntPtr.Zero;
            IntPtr found = IntPtr.Zero;
            try
            {
                EnumWindows(delegate(IntPtr hwnd, IntPtr lParam)
                {
                    uint winPid;
                    GetWindowThreadProcessId(hwnd, out winPid);
                    if (winPid == (uint)targetPid && IsWindowVisible(hwnd))
                    {
                        RECT r;
                        if (GetWindowRect(hwnd, out r))
                        {
                            int w = r.Right - r.Left;
                            int h = r.Bottom - r.Top;
                            if (w > 300 && h > 200)
                            {
                                found = hwnd;
                                return false; // Stop enumeration
                            }
                        }
                    }
                    return true;
                }, IntPtr.Zero);
            }
            catch { }
            return found;
        }

        private static MemoryMappedFile _statusMmf = null;
        private static MemoryMappedViewAccessor _statusAccessor = null;
        private static string _lastReadStatus = "";

        private static void InitSplashStatusMmf()
        {
            try
            {
                _statusMmf = MemoryMappedFile.CreateOrOpen(SplashStatusMmfName, 2048, MemoryMappedFileAccess.ReadWrite);
                _statusAccessor = _statusMmf.CreateViewAccessor(0, 2048, MemoryMappedFileAccess.ReadWrite);
                byte[] zero = new byte[2048];
                _statusAccessor.WriteArray(0, zero, 0, zero.Length);
                _lastReadStatus = "";
            }
            catch { }
        }

        private static string ReadSplashStatus()
        {
            try
            {
                if (_statusAccessor != null && _statusAccessor.CanRead)
                {
                    byte[] buf = new byte[512];
                    _statusAccessor.ReadArray(0, buf, 0, buf.Length);
                    int len = 0;
                    while (len < buf.Length && buf[len] != 0) len++;
                    if (len > 0)
                    {
                        string text = System.Text.Encoding.UTF8.GetString(buf, 0, len);
                        if (text != _lastReadStatus)
                        {
                            _lastReadStatus = text;
                            return text;
                        }
                    }
                }
            }
            catch { }
            return null;
        }

        private static void CloseSplashStatusMmf()
        {
            try { if (_statusAccessor != null) { _statusAccessor.Dispose(); _statusAccessor = null; } } catch { }
            try { if (_statusMmf != null) { _statusMmf.Dispose(); _statusMmf = null; } } catch { }
        }

        private static Image LoadSplashImage(string appDir, string baseDir)
        {
            try
            {
                Assembly asm = Assembly.GetExecutingAssembly();
                Stream s = asm.GetManifestResourceStream("StrictRecords.splash.png");
                if (s != null)
                {
                    using (s)
                    {
                        return Image.FromStream(s);
                    }
                }
            }
            catch { }

            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string[] candidates = new string[]
            {
                Path.Combine(appDir, "StrictRecords-splash.png"),
                Path.Combine(appDir, ".dist", "StrictRecords-splash.png"),
                Path.Combine(appDir, "assets", "StrictRecords-splash.png"),
                Path.Combine(baseDir, "StrictRecords-splash.png"),
                Path.Combine(baseDir, ".dist", "StrictRecords-splash.png"),
                Path.Combine(baseDir, "assets", "StrictRecords-splash.png"),
                Path.Combine(localApp, "StrictRecords", "StrictRecords-splash.png"),
                Path.Combine(localApp, "Programs", "StrictRecords", "StrictRecords-splash.png")
            };

            foreach (string path in candidates)
            {
                try
                {
                    if (File.Exists(path))
                    {
                        byte[] raw = File.ReadAllBytes(path);
                        MemoryStream ms = new MemoryStream(raw);
                        return Image.FromStream(ms);
                    }
                }
                catch { }
            }

            return null;
        }

        internal class SplashForm : Form
        {
            private Image _splashImage;
            private string _statusText = "Starting StrictRoutines...";
            private int _stage = 0;
            private int _animTick = 0;
            private System.Windows.Forms.Timer _animTimer;
            private readonly object _lock = new object();

            public SplashForm(Image splashImage)
            {
                _splashImage = splashImage;
                this.FormBorderStyle = FormBorderStyle.None;
                this.StartPosition = FormStartPosition.CenterScreen;
                this.ShowInTaskbar = false;
                this.TopMost = true;
                this.DoubleBuffered = true;
                this.ClientSize = _splashImage != null ? _splashImage.Size : new Size(760, 480);
                this.BackColor = Color.FromArgb(11, 15, 25);
                this.SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);

                _animTimer = new System.Windows.Forms.Timer();
                _animTimer.Interval = 40;
                _animTimer.Tick += delegate(object sender, EventArgs e)
                {
                    _animTick++;
                    try
                    {
                        if (this.IsHandleCreated && !this.IsDisposed)
                        {
                            this.Invalidate(new Rectangle(320, 370, 390, 95));
                        }
                    }
                    catch { }
                };
            }

            protected override void OnShown(EventArgs e)
            {
                base.OnShown(e);
                try
                {
                    this.Activate();
                    this.BringToFront();
                    if (_animTimer != null && !_animTimer.Enabled)
                    {
                        _animTimer.Start();
                    }
                }
                catch { }
            }

            public void SetStatus(string text)
            {
                if (string.IsNullOrEmpty(text)) return;
                lock (_lock)
                {
                    _statusText = text;
                    string lower = text.ToLowerInvariant();
                    if (lower.Contains("start")) _stage = 0;
                    else if (lower.Contains("config") || lower.Contains("check")) _stage = 1;
                    else if (lower.Contains("storage") || lower.Contains("record") || lower.Contains("score")) _stage = 2;
                    else if (lower.Contains("service") || lower.Contains("sync") || lower.Contains("connect")) _stage = 3;
                    else if (lower.Contains("scheduler") || lower.Contains("telemetry")) _stage = 3;
                    else if (lower.Contains("workspace") || lower.Contains("dashboard") || lower.Contains("prepar") || lower.Contains("ready")) _stage = 4;
                }
                try
                {
                    if (this.IsHandleCreated && !this.IsDisposed)
                    {
                        this.Invalidate(new Rectangle(320, 370, 390, 95));
                    }
                }
                catch { }
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                e.Graphics.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;

                if (_splashImage != null)
                {
                    e.Graphics.DrawImage(_splashImage, 0, 0, this.ClientSize.Width, this.ClientSize.Height);
                }

                int stage;
                string text;
                lock (_lock)
                {
                    stage = _stage;
                    text = _statusText;
                }

                // Render 5 stage progress indicator segments over splash base
                for (int i = 0; i < 5; i++)
                {
                    int x = 326 + i * 74;
                    Rectangle segRect = new Rectangle(x, 376, 66, 8);
                    Color segColor;
                    if (i < stage)
                    {
                        segColor = ColorTranslator.FromHtml("#00D69B"); // Completed
                    }
                    else if (i == stage)
                    {
                        double pulse = (Math.Sin(_animTick * 0.18) + 1.0) / 2.0;
                        int r = (int)(0 + pulse * 36);
                        int g = (int)(180 + pulse * 34);
                        int b = (int)(130 + pulse * 25);
                        segColor = Color.FromArgb(r, g, b);
                    }
                    else
                    {
                        segColor = ColorTranslator.FromHtml("#263247"); // Upcoming
                    }

                    using (GraphicsPath path = RoundedRect(segRect, 3))
                    using (SolidBrush brush = new SolidBrush(segColor))
                    {
                        e.Graphics.FillPath(brush, path);
                    }
                }

                // Render live status text at (326, 442)
                if (!string.IsNullOrEmpty(text))
                {
                    bool isError = text.StartsWith("ERROR", StringComparison.OrdinalIgnoreCase);
                    Color textColor = isError ? ColorTranslator.FromHtml("#FF6B6B") : ColorTranslator.FromHtml("#91A4B8");
                    using (Font font = new Font("Segoe UI", 9.5f, FontStyle.Regular))
                    using (SolidBrush brush = new SolidBrush(textColor))
                    {
                        e.Graphics.DrawString(text, font, brush, 326, 442);
                    }
                }
            }

            private static GraphicsPath RoundedRect(Rectangle bounds, int radius)
            {
                int diameter = radius * 2;
                Size size = new Size(diameter, diameter);
                Rectangle arc = new Rectangle(bounds.Location, size);
                GraphicsPath path = new GraphicsPath();

                if (radius == 0)
                {
                    path.AddRectangle(bounds);
                    return path;
                }

                path.AddArc(arc, 180, 90);
                arc.X = bounds.Right - diameter;
                path.AddArc(arc, 270, 90);
                arc.Y = bounds.Bottom - diameter;
                path.AddArc(arc, 0, 90);
                arc.X = bounds.Left;
                path.AddArc(arc, 90, 90);
                path.CloseFigure();
                return path;
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                {
                    if (_animTimer != null)
                    {
                        try { _animTimer.Stop(); _animTimer.Dispose(); } catch { }
                        _animTimer = null;
                    }
                    if (_splashImage != null)
                    {
                        _splashImage.Dispose();
                        _splashImage = null;
                    }
                }
                base.Dispose(disposing);
            }
        }

        internal class SplashController
        {
            private Thread _thread;
            private SplashForm _form;
            private ManualResetEvent _ready = new ManualResetEvent(false);

            public bool Start(Image img)
            {
                if (img == null) return false;
                try
                {
                    _thread = new Thread(new ThreadStart(delegate()
                    {
                        try
                        {
                            _form = new SplashForm(img);
                            _form.Shown += delegate(object sender, EventArgs e)
                            {
                                _ready.Set();
                            };
                            Application.Run(_form);
                        }
                        catch { }
                    }));
                    _thread.SetApartmentState(ApartmentState.STA);
                    _thread.IsBackground = true;
                    _thread.Start();
                    return _ready.WaitOne(2500);
                }
                catch
                {
                    return false;
                }
            }

            public void UpdateStatus(string status)
            {
                try
                {
                    if (_form != null && !_form.IsDisposed)
                    {
                        if (_form.InvokeRequired)
                        {
                            _form.BeginInvoke(new Action(delegate() { _form.SetStatus(status); }));
                        }
                        else
                        {
                            _form.SetStatus(status);
                        }
                    }
                }
                catch { }
            }

            public void Close()
            {
                try
                {
                    if (_form != null && !_form.IsDisposed)
                    {
                        if (_form.InvokeRequired)
                        {
                            _form.BeginInvoke(new Action(delegate()
                            {
                                try
                                {
                                    _form.Close();
                                    _form.Dispose();
                                }
                                catch { }
                            }));
                        }
                        else
                        {
                            _form.Close();
                            _form.Dispose();
                        }
                    }
                }
                catch { }
            }
        }

        private static void Log(string msg)
        {
            try
            {
                string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string logDir = Path.Combine(localApp, "StrictRecords");
                Directory.CreateDirectory(logDir);
                File.AppendAllText(Path.Combine(logDir, "launcher.log"), DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + msg + Environment.NewLine);
            }
            catch { }
        }

        [STAThread]
        static void Main(string[] args)
        {
            Log("Main started. Args: " + (args != null ? string.Join(" ", args) : "none"));
            try
            {
                SetCurrentProcessExplicitAppUserModelID("StrictRecords.StrictRoutines.Desktop");
            }
            catch { }

            bool isBackground = false;
            if (args != null)
            {
                foreach (string a in args)
                {
                    if (string.Equals(a, "--background", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(a, "-background", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(a, "/background", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(a, "-b", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(a, "--no-browser", StringComparison.OrdinalIgnoreCase))
                    {
                        isBackground = true;
                        break;
                    }
                }
            }

            // ── CHECK 1: IS STRICTRECORDS ALREADY RUNNING? ─────
            bool alreadyRunning = IsAppAlreadyRunning();
            Log("Check 1 alreadyRunning = " + alreadyRunning);
            if (alreadyRunning)
            {
                if (!isBackground)
                {
                    NotifyUserDuplicateBlocked();
                    TryActivateWindow();
                }
                Environment.Exit(0);
                return;
            }

            // ── CHECK 2: LAUNCHER CONCURRENCY LOCK ─────
            bool launcherAcquired = false;
            Mutex launcherMutex = null;
            try
            {
                launcherMutex = new Mutex(false, LauncherMutexName);
                launcherAcquired = launcherMutex.WaitOne(1500, false);
            }
            catch (AbandonedMutexException)
            {
                launcherAcquired = true;
            }
            catch (Exception ex)
            {
                Log("Launcher mutex warning: " + ex.Message);
                launcherAcquired = true;
            }

            Log("Check 2 launcherAcquired = " + launcherAcquired);

            // Double check if app was opened while waiting for launcher mutex
            if (CheckMutexExists(AppMutexName) || HasAppWindow())
            {
                Log("App became active while acquiring launcher mutex; restoring window and exiting.");
                if (!isBackground)
                {
                    NotifyUserDuplicateBlocked();
                    TryActivateWindow();
                }
                if (launcherMutex != null && launcherAcquired)
                {
                    try { launcherMutex.ReleaseMutex(); launcherMutex.Close(); } catch { }
                }
                Environment.Exit(0);
                return;
            }

            // ── PRIMARY INSTANCE LAUNCH ─────
            SplashController splash = null;
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string[] candidateDirs = new string[]
                {
                    @"c:\Users\lokad\Music\Practical\StrictRecords",
                    Path.Combine(localApp, "Programs", "StrictRecords"),
                    Path.Combine(localApp, "StrictRecords"),
                    Path.Combine(localApp, "StrictRoutines"),
                    baseDir
                };

                string appDir = "";
                string scriptPath = "";

                foreach (string dir in candidateDirs)
                {
                    if (Directory.Exists(dir))
                    {
                        string p = Path.Combine(dir, "desktop_app.py");
                        if (File.Exists(p))
                        {
                            appDir = dir;
                            scriptPath = p;
                            break;
                        }
                    }
                }

                if (string.IsNullOrEmpty(scriptPath))
                {
                    MessageBox.Show("Could not locate desktop_app.py in StrictRecords directories.", "StrictRecords Launch Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    if (launcherMutex != null)
                    {
                        try { launcherMutex.ReleaseMutex(); launcherMutex.Close(); } catch { }
                    }
                    Environment.Exit(1);
                    return;
                }

                string[] pythonwCandidates = new string[]
                {
                    @"C:\Program Files\Python314\pythonw.exe",
                    @"C:\Program Files\Python311\pythonw.exe",
                    Path.Combine(localApp, @"Programs\Python\Python314\pythonw.exe"),
                    Path.Combine(localApp, @"Programs\Python\Python311\pythonw.exe"),
                    "pythonw.exe",
                    "python.exe"
                };

                string pythonw = "";
                foreach (string py in pythonwCandidates)
                {
                    if (py.Contains("\\"))
                    {
                        if (File.Exists(py))
                        {
                            pythonw = py;
                            break;
                        }
                    }
                    else
                    {
                        pythonw = py;
                        break;
                    }
                }

                // isBackground was already evaluated from args at entry

                InitSplashStatusMmf();

                if (!isBackground)
                {
                    try
                    {
                        Image splashImg = LoadSplashImage(appDir, baseDir);
                        if (splashImg != null)
                        {
                            splash = new SplashController();
                            splash.Start(splashImg);
                            splash.UpdateStatus("Starting StrictRoutines...");
                        }
                    }
                    catch { }
                }

                EventWaitHandle closeEvent = null;
                try
                {
                    closeEvent = new EventWaitHandle(false, EventResetMode.ManualReset, SplashCloseEventName);
                    closeEvent.Reset();
                }
                catch { }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = string.IsNullOrEmpty(pythonw) ? "pythonw.exe" : pythonw;
                string extraArgs = args.Length > 0 ? " " + string.Join(" ", args) : "";
                psi.Arguments = "\"" + scriptPath + "\"" + extraArgs;
                psi.WorkingDirectory = appDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                psi.RedirectStandardError = true;

                Log("Starting child: " + psi.FileName + " " + psi.Arguments + " (WorkingDir: " + psi.WorkingDirectory + ")");
                Process childProcess = Process.Start(psi);
                if (childProcess != null)
                {
                    Thread errThread = new Thread(new ThreadStart(delegate()
                    {
                        try
                        {
                            string errLine;
                            string childLogDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StrictRecords");
                            Directory.CreateDirectory(childLogDir);
                            string childLogFile = Path.Combine(childLogDir, "launcher_child.log");
                            while ((errLine = childProcess.StandardError.ReadLine()) != null)
                            {
                                File.AppendAllText(childLogFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " [STDERR] " + errLine + Environment.NewLine);
                            }
                        }
                        catch { }
                    }));
                    errThread.IsBackground = true;
                    errThread.Start();
                }
                if (childProcess != null)
                {
                    Log("childProcess started. PID=" + childProcess.Id);
                    // AttachChildToJobObject(childProcess); // Disabled: prevents premature kill of child process

                    string lastError = null;
                    bool readySignaled = false;

                    if (splash != null)
                    {
                        splash.UpdateStatus("Starting StrictRoutines...");

                        DateTime startWait = DateTime.UtcNow;
                        const int timeoutSeconds = 45;
                        bool promptGiven = false;

                        while ((DateTime.UtcNow - startWait).TotalSeconds < (promptGiven ? 75 : timeoutSeconds))
                        {
                            if (childProcess.HasExited)
                            {
                                Log("childProcess exited early during splash wait! ExitCode=" + childProcess.ExitCode);
                                break;
                            }
                            if (closeEvent != null && closeEvent.WaitOne(0))
                            {
                                Log("closeEvent was signaled!");
                                readySignaled = true;
                                break;
                            }

                            string status = ReadSplashStatus();
                            if (!string.IsNullOrEmpty(status))
                            {
                                if (status.StartsWith("ERROR:", StringComparison.OrdinalIgnoreCase))
                                {
                                    Log("Startup error reported by child: " + status);
                                    lastError = status.Substring(6).Trim();
                                    break;
                                }
                                splash.UpdateStatus(status);
                            }

                            // Fallback: after 18 seconds, check if child dashboard window is actually visible
                            if ((DateTime.UtcNow - startWait).TotalSeconds > 18)
                            {
                                IntPtr childHwnd = FindChildDashboardWindow(childProcess.Id);
                                if (childHwnd != IntPtr.Zero)
                                {
                                    Log("Child dashboard window detected via fallback: HWND=" + childHwnd);
                                    readySignaled = true;
                                    break;
                                }
                            }

                            if (!promptGiven && (DateTime.UtcNow - startWait).TotalSeconds >= timeoutSeconds)
                            {
                                promptGiven = true;
                                DialogResult res = MessageBox.Show(
                                    "StrictRoutines is taking longer than expected to launch.\n\nWould you like to keep waiting?",
                                    "StrictRoutines Startup",
                                    MessageBoxButtons.YesNo,
                                    MessageBoxIcon.Question);
                                if (res == DialogResult.No)
                                {
                                    Log("User cancelled launch after timeout.");
                                    try { childProcess.Kill(); } catch { }
                                    break;
                                }
                            }

                            Thread.Sleep(30);
                        }

                        if (readySignaled)
                        {
                            splash.UpdateStatus("Ready.");
                            IntPtr childHwnd = FindChildDashboardWindow(childProcess.Id);
                            if (childHwnd != IntPtr.Zero)
                            {
                                ShowWindow(childHwnd, SW_RESTORE);
                                ShowWindow(childHwnd, SW_SHOW);
                                SetForegroundWindow(childHwnd);
                                BringWindowToTop(childHwnd);
                            }
                            Thread.Sleep(120);
                        }

                        splash.Close();
                        splash = null;
                    }

                    if (closeEvent != null)
                    {
                        try { closeEvent.Close(); } catch { }
                    }
                    CloseSplashStatusMmf();

                    if (!string.IsNullOrEmpty(lastError) || (childProcess.HasExited && childProcess.ExitCode != 0))
                    {
                        string msg = !string.IsNullOrEmpty(lastError)
                            ? "StrictRoutines could not start:\n\n" + lastError + "\n\nPlease check your configuration and ensure another instance is not running."
                            : "StrictRoutines failed to start (Process Exit Code: " + childProcess.ExitCode + ").\n\nPlease check launcher.log in %LOCALAPPDATA%\\StrictRecords for details.";
                        MessageBox.Show(msg, "StrictRoutines Startup Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }

                    // Keep StrictRecords.exe alive and holding LauncherMutexName until child terminates
                    childProcess.WaitForExit();
                    Log("childProcess.WaitForExit finished. ExitCode=" + childProcess.ExitCode);
                }

                if (launcherMutex != null)
                {
                    try { launcherMutex.ReleaseMutex(); launcherMutex.Close(); } catch { }
                }

                Log("Launcher exiting cleanly.");
                Environment.Exit(0);
            }
            catch (Exception ex)
            {
                Log("Launcher Exception: " + ex.ToString());
                if (splash != null)
                {
                    try { splash.Close(); } catch { }
                }
                if (launcherMutex != null)
                {
                    try { launcherMutex.ReleaseMutex(); launcherMutex.Close(); } catch { }
                }
                MessageBox.Show("Failed to launch StrictRecords: " + ex.Message, "StrictRecords Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Environment.Exit(1);
            }
        }
    }
}
