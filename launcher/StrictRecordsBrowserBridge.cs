using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace StrictRecords.NativeBrowser
{
    public sealed class BrowserSnapshot
    {
        public bool Supported;
        public int ProcessId;
        public string Url = "";
        public string TabId = "";
        public int TabCount;
        public bool CanClose;
        public string Reason = "Browser controls are unavailable.";
    }

    // Read browser chrome only. Never traverse webpage Document controls or read
    // page content, credentials, clipboard data, or background tab URLs.
    public static class BrowserBridge
    {
        [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint count, Input[] inputs, int size);

        [StructLayout(LayoutKind.Sequential)] struct KeyboardInput
        {
            public ushort key, scan;
            public uint flags, time;
            public UIntPtr extra;
        }
        [StructLayout(LayoutKind.Sequential)] struct MouseInput
        {
            public int x, y;
            public uint data, flags, time;
            public UIntPtr extra;
        }
        [StructLayout(LayoutKind.Explicit)] struct InputUnion
        {
            [FieldOffset(0)] public KeyboardInput keyboard;
            [FieldOffset(0)] public MouseInput mouse;
        }
        [StructLayout(LayoutKind.Sequential)] struct Input
        {
            public uint type;
            public InputUnion value;
        }

        static bool SupportedShortcutBrowser(int pid)
        {
            string name = Process.GetProcessById(pid).ProcessName.ToLowerInvariant();
            return new HashSet<string> { "chrome", "msedge", "brave", "comet", "vivaldi", "opera", "arc", "ccleanerbrowser", "ccleaner" }.Contains(name);
        }

        static bool Shortcut(IntPtr hwnd, string tabId, string origin, ushort key)
        {
            // Keyboard control is used only after confirming the exact active tab,
            // its origin and the target browser window at the input boundary.
            if (GetForegroundWindow() != hwnd) return false;
            var current = Capture(hwnd, Controls(hwnd));
            if (current.TabId != tabId || current.Url != origin || !SupportedShortcutBrowser(current.ProcessId)) return false;
            if (key == 0x57 && current.TabCount < 2) return false;
            foreach (int modifier in new int[] { 0x10, 0x11, 0x12, 0x5B, 0x5C })
                if ((GetAsyncKeyState(modifier) & 0x8000) != 0) return false;
            var inputs = new Input[4];
            ushort[] keys = { 0x11, key, key, 0x11 };
            for (int i = 0; i < inputs.Length; i++)
            {
                inputs[i].type = 1;
                inputs[i].value.keyboard.key = keys[i];
                inputs[i].value.keyboard.flags = i >= 2 ? 2u : 0u;
            }
            if (GetForegroundWindow() != hwnd) return false;
            return SendInput(4, inputs, Marshal.SizeOf(typeof(Input))) == 4;
        }

        static string Identity(AutomationElement element)
        {
            return String.Join(".", element.GetRuntimeId());
        }

        static string WebUrl(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.IndexOf(' ') >= 0) return "";
            Uri uri;
            if (value.IndexOf("://", StringComparison.Ordinal) < 0) value = "https://" + value;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
                (uri.Scheme != "https" && uri.Scheme != "http") || String.IsNullOrEmpty(uri.Host)) return "";
            return uri.GetLeftPart(UriPartial.Authority);
        }

        static bool Selected(AutomationElement element)
        {
            object pattern;
            if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern))
                return ((SelectionItemPattern)pattern).Current.IsSelected;
            return false;
        }

        static List<AutomationElement> Controls(IntPtr hwnd)
        {
            var result = new List<AutomationElement>();
            var stack = new Stack<KeyValuePair<AutomationElement, int>>();
            stack.Push(new KeyValuePair<AutomationElement, int>(AutomationElement.FromHandle(hwnd), 0));
            var clock = Stopwatch.StartNew();
            var walker = TreeWalker.ControlViewWalker;
            while (stack.Count > 0 && result.Count < 350 && clock.ElapsedMilliseconds < 600)
            {
                var item = stack.Pop();
                var element = item.Key;
                if (element == null || item.Value > 12) continue;
                var type = element.Current.ControlType;
                if (type == ControlType.Document) continue;
                result.Add(element);
                var child = walker.GetFirstChild(element);
                while (child != null && stack.Count < 400)
                {
                    stack.Push(new KeyValuePair<AutomationElement, int>(child, item.Value + 1));
                    child = walker.GetNextSibling(child);
                }
            }
            return result;
        }

        static AutomationElement CloseControl(AutomationElement tab)
        {
            // Some Chromium providers expose the tab's close button in RawView
            // while omitting it from ControlView. Search only this tab's chrome.
            var walker = TreeWalker.RawViewWalker;
            var stack = new Stack<KeyValuePair<AutomationElement, int>>();
            stack.Push(new KeyValuePair<AutomationElement, int>(tab, 0));
            while (stack.Count > 0)
            {
                var item = stack.Pop();
                if (item.Value > 3) continue;
                var element = item.Key;
                if (item.Value > 0 && element.Current.ControlType == ControlType.Button)
                {
                    var name = element.Current.Name.Trim();
                    object pattern;
                    if ((name.Equals("Close", StringComparison.OrdinalIgnoreCase) ||
                         name.Equals("Close tab", StringComparison.OrdinalIgnoreCase)) &&
                        element.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) return element;
                }
                var child = walker.GetFirstChild(element);
                while (child != null)
                {
                    stack.Push(new KeyValuePair<AutomationElement, int>(child, item.Value + 1));
                    child = walker.GetNextSibling(child);
                }
            }
            return null;
        }

        static BrowserSnapshot Capture(IntPtr hwnd, List<AutomationElement> controls)
        {
            var snapshot = new BrowserSnapshot();
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            snapshot.ProcessId = (int)pid;
            string url = "";
            AutomationElement selected = null;
            foreach (var element in controls)
            {
                var current = element.Current;
                if (current.ControlType == ControlType.TabItem)
                {
                    snapshot.TabCount++;
                    if (Selected(element))
                    {
                        if (selected != null) { snapshot.Reason = "Multiple selected tabs; control is ambiguous."; return snapshot; }
                        selected = element;
                    }
                }
                if (current.ControlType != ControlType.Edit || current.IsOffscreen || current.HasKeyboardFocus) continue;
                // Stable toolbar IDs or explicit address-field names; never a page search box.
                var id = current.AutomationId;
                var name = current.Name;
                if (id != "view_1020" && id != "addressEditBox" && id != "urlbar-input" &&
                    name != "Address and search bar" && name != "Address field") continue;
                object pattern;
                if (!element.TryGetCurrentPattern(ValuePattern.Pattern, out pattern)) continue;
                var candidate = WebUrl(((ValuePattern)pattern).Current.Value);
                if (String.IsNullOrEmpty(candidate)) continue;
                if (url != "" && url != candidate) { snapshot.Reason = "Multiple address fields; URL is ambiguous."; return snapshot; }
                url = candidate;
            }
            if (url == "") { snapshot.Reason = "Current browser URL is not exposed by Windows UI Automation."; return snapshot; }
            snapshot.Supported = true;
            snapshot.Url = url;
            snapshot.TabId = selected == null ? "" : Identity(selected);
            // Count peers in this tab strip, not unrelated tab widgets in sidebars.
            if (selected != null)
            {
                snapshot.TabCount = 0;
                var walker = TreeWalker.ControlViewWalker;
                var parent = walker.GetParent(selected);
                var sibling = parent == null ? null : walker.GetFirstChild(parent);
                while (sibling != null)
                {
                    if (sibling.Current.ControlType == ControlType.TabItem) snapshot.TabCount++;
                    sibling = walker.GetNextSibling(sibling);
                }
            }
            snapshot.CanClose = selected != null && (CloseControl(selected) != null || SupportedShortcutBrowser(snapshot.ProcessId));
            snapshot.Reason = snapshot.CanClose ? "Verified URL and selected-tab identity are available for native browser control." :
                "Current URL is available; this browser does not expose an exact tab close control.";
            return snapshot;
        }

        public static BrowserSnapshot Inspect(long handle)
        {
            try
            {
                var hwnd = new IntPtr(handle);
                if (!IsWindow(hwnd)) return new BrowserSnapshot { Reason = "Browser window has left." };
                return Capture(hwnd, Controls(hwnd));
            }
            catch (Exception error) { return new BrowserSnapshot { Reason = "Windows browser control unavailable: " + error.GetType().Name }; }
        }

        private static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "");
        }

        public static string InspectJson(long handle)
        {
            try
            {
                BrowserSnapshot snap = Inspect(handle);
                return "{\"Supported\":" + (snap.Supported ? "true" : "false") +
                       ",\"ProcessId\":" + snap.ProcessId +
                       ",\"Url\":\"" + EscapeJson(snap.Url) + "\"" +
                       ",\"TabId\":\"" + EscapeJson(snap.TabId) + "\"" +
                       ",\"TabCount\":" + snap.TabCount +
                       ",\"CanClose\":" + (snap.CanClose ? "true" : "false") +
                       ",\"Reason\":\"" + EscapeJson(snap.Reason) + "\"}";
            }
            catch (Exception ex)
            {
                return "{\"Supported\":false,\"ProcessId\":0,\"Url\":\"\",\"TabId\":\"\",\"TabCount\":0,\"CanClose\":false,\"Reason\":\"" + EscapeJson(ex.GetType().Name) + "\"}";
            }
        }

        public static bool CloseVerifiedTab(long handle, int processId, string tabId, string expectedOrigin)
        {
            if (String.IsNullOrEmpty(tabId) || String.IsNullOrEmpty(expectedOrigin)) return false;
            try
            {
                var hwnd = new IntPtr(handle);
                var controls = Controls(hwnd);
                var snapshot = Capture(hwnd, controls);
                if (!snapshot.Supported || !snapshot.CanClose || snapshot.ProcessId != processId ||
                    snapshot.TabId != tabId || snapshot.Url != expectedOrigin) return false;
                AutomationElement selected = controls.Find(e => e.Current.ControlType == ControlType.TabItem && Identity(e) == tabId);
                if (selected == null) return false;
                var close = CloseControl(selected);
                if (close == null)
                {
                    // Chromium's documented Ctrl+W closes the current tab. This is
                    // a native adapter with live UIA checks, not a title-based shortcut.
                    if (!SupportedShortcutBrowser(processId)) return false;
                    SetForegroundWindow(hwnd);
                    if (GetForegroundWindow() != hwnd) return false;
                    if (snapshot.TabCount < 2)
                    {
                        if (!Shortcut(hwnd, tabId, expectedOrigin, 0x54)) return false; // Ctrl+T
                        var replacementTimer = Stopwatch.StartNew();
                        while (replacementTimer.ElapsedMilliseconds < 700 && Controls(hwnd).FindAll(e => e.Current.ControlType == ControlType.TabItem).Count < 2)
                            System.Threading.Thread.Sleep(40);
                        if (Controls(hwnd).FindAll(e => e.Current.ControlType == ControlType.TabItem).Count < 2) return false;
                        object selection;
                        if (!selected.TryGetCurrentPattern(SelectionItemPattern.Pattern, out selection)) return false;
                        ((SelectionItemPattern)selection).Select();
                    }
                    if (!Shortcut(hwnd, tabId, expectedOrigin, 0x57)) return false; // Ctrl+W only
                }
                else
                {
                if (snapshot.TabCount < 2)
                {
                    // Preserve the window before closing its last tab. Only explicit
                    // UIA controls are invoked; no blind keystrokes or WM_CLOSE fallback.
                    var newTab = controls.Find(e => e.Current.ControlType == ControlType.Button && e.Current.Name == "New Tab");
                    object pattern;
                    if (newTab == null || !newTab.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) return false;
                    ((InvokePattern)pattern).Invoke();
                    if (!selected.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern)) return false;
                    ((SelectionItemPattern)pattern).Select();
                    snapshot = Capture(hwnd, Controls(hwnd));
                    if (snapshot.TabCount < 2 || snapshot.TabId != tabId || snapshot.Url != expectedOrigin) return false;
                }
                close = CloseControl(selected);
                object closePattern;
                if (close == null || !close.TryGetCurrentPattern(InvokePattern.Pattern, out closePattern)) return false;
                ((InvokePattern)closePattern).Invoke();
                }
                var timer = Stopwatch.StartNew();
                while (timer.ElapsedMilliseconds < 800)
                {
                    if (!IsWindow(hwnd)) return false;
                    bool retained = Controls(hwnd).Exists(e => e.Current.ControlType == ControlType.TabItem && Identity(e) == tabId);
                    if (!retained) return true;
                    System.Threading.Thread.Sleep(40);
                }
            }
            catch (Exception) { }
            return false;
        }
    }
}
