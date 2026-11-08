# StrictRoutines browser setup

For the independent Chrome interruption timer, use the profile-bound setup in
[Status Helper instructions](../browser_extension_monitor/README.md). The normal
extension cannot run after being disabled. The timer requires that separate,
visible helper and the updated native Windows companion; a heartbeat alone is
never proof of disablement. The new package defaults to standard installation.

Open **Controls → Browser companions** in StrictRecords.exe. The app detects
registered browser executables and standard installation locations.

**Set up all supported browsers** prepares a persistent, separate extension
folder for each supported installation and opens its extension manager. In each
browser, enable **Developer mode**, choose **Load unpacked**, and select the folder
shown in Controls. Use the browser profile in which you want restrictions active.
If the browser requests access to the local desktop connection, approve it there.

The current package is local and unpublished. Chrome and Edge do not offer silent
installation of this unpacked extension. The app does not edit browser profile
preferences or bypass browser confirmation. Firefox needs a Mozilla-signed package
and is reported as unsupported by this Chromium bundle.

An opened setup page is **Awaiting browser confirmation**. **Connected** requires a
recent heartbeat from that browser installation. A closed browser or stopped app
can lose the live connection without proving the extension was uninstalled.
Install separately in every profile you use. After an app update, run setup and
reload the existing extension on its manager page if required.

Prepared files live under `%LOCALAPPDATA%\StrictRecords\browser_extension`, outside
the EXE's temporary extraction directory. The toolbar popup connects this actual
extension. Targeted website permissions and score thresholds remain unchanged.

## Standard vs. Managed Installation

StrictRoutines supports two distinct installation modes:

1. **Standard Installation (Unmanaged)**:
   - The extension runs as an unpacked extension in Developer mode.
   - The user can disable or remove the extension at any time via `chrome://extensions`.
   - No Windows Registry management policies are applied.

2. **Managed Installation (Removal Protected)**:
   - Requires explicit user authorization during setup (`lock_prompt.html` or Controls).
   - Applies supported Chromium enterprise policies via `HKCU\Software\Policies\Google\Chrome\URLBlocklist` and `ExtensionSettings`.
   - Prevents casual or impulsive navigation to extension removal pages (`chrome://extensions`, `edge://extensions`) during active discipline sessions.
   - Transparently tracked in `HKCU\Software\StrictRecords\BrowserPolicyOwnership`.
   - Legitimate administrators can unlock the extension at any time via the 4-digit PIN in Controls or through standard Registry administration.
   - See [REMOVAL_AND_RECOVERY.md](REMOVAL_AND_RECOVERY.md) for full policy details and removal instructions.

## Optional native HTML connection

The native UI Automation fallback can be used without an extension, subject to
the browser's actual accessible controls. It does not establish extension status.

1. Start StrictRecords.exe.
2. Open [the local Connect page](http://127.0.0.1:8765/connect), or open StrictRecords-connect.html from the workspace.
3. Click **CONNECT TO STRICTRECORDS**.

The HTML page connects once to the native desktop app. There is no browser selector or separate setup per browser. The native app reads supported browser controls through Windows UI Automation and displays each observed browser's actual capabilities.

The legacy file popup.html in this folder also opens the native HTML connection page. It no longer requires chrome.storage, chrome.runtime, or an extension context.

Native exact-tab control requires a readable current address field, a stable selected-tab identity, and usable native browser controls. Incompatible or unresponsive browsers are reported explicitly; a successful desktop connection does not prove every browser supports tab closure. See NATIVE_BROWSER_CONNECTION.md in the workspace for the mechanism and remaining limitations.

The browser companion is separate from the HTML Connect flow. Neither setup path changes APK code, score awards, deductions, history or unlock thresholds.
