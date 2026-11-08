# Native HTML connection

Open `StrictRecords-connect.html`, or start StrictRecords.exe and open
`http://127.0.0.1:8765/connect`. Click **CONNECT TO STRICTRECORDS**.
This page uses the native app's existing loopback server. There is no hosted
replacement for the EXE, extension installation, browser selection, or new account.
The older `browser_extension/popup.html` file also opens this native connection UI.

The page checks the live desktop service, watcher thread, and native adapter. It
does not send a fake extension heartbeat or change score history, proofs, registrations,
awards, deductions, or configured unlock thresholds.

## Native browser control

The EXE includes a Windows UI Automation adapter. It reads the current browser
address field and selected tab identity from browser controls, skips webpage Document
controls, and discards origins that are not configured targets. One worker handles
these requests; a stalled provider is reported as unavailable without spawning more workers.

**BACK TO HOME** rechecks eligibility and the current tab identity/origin. Where the
browser exposes its tab close button, the adapter invokes that exact control. For
supported Chromium browsers that expose a selected tab and address field, it uses
the browser's documented current-tab shortcut after verifying the foreground browser
window and selected tab. It creates a replacement tab first when required to preserve
the browser window. It never falls back to closing an entire browser window.

The native reader covers compatible Chrome, Edge, Brave, Comet, Vivaldi, Opera, and
Arc controls. It attempts URL detection for Firefox; exact shortcut control is not
enabled for Firefox. Provider differences, localization, elevated browser processes,
system focus rules, or inaccessible browser controls can prevent detection or closure.
Those cases are reported in the connection diagnostics. A desktop connection is not
proof that every installed browser supports exact tab control.

The older telemetry agent continues its existing eligibility evaluation but delegates
browser/Spotify restriction UI to the central controller, avoiding a second dialog or
the legacy whole-browser close fallback.

## Evidence

- HTML connection and error/timeout states have automated regression coverage.
- Native adapter/session/target/score rules have isolated automated regression coverage.
- The existing C# native build process compiles the adapter and native launcher.
- Live Comet URL detection was observed through Windows UI Automation. Exact tab
  closure and other browser variants require physical checks; compilation alone is
  not evidence of those checks.
- The running native app served the HTML/JavaScript/connection API successfully;
  clicking the HTML button visibly displayed "Connected to StrictRecords".
- 28 isolated Python regressions and 9 HTML button regressions passed. The broader
  server/agent HTTP tests were initially blocked by sandbox loopback restrictions;
  they are run separately with temporary data and real window/registry actions disabled.
- On that separate HTTP run, live sampling/categorization and the dev-scenario API
  tests passed; the pre-existing dashboard/audio delivery test timed out reading its
  response. The new Connect page, script and desktop-connection endpoint returned HTTP
  200 in the running app, and their isolated route tests passed.

API references: [Windows UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview),
[UI Automation patterns](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-control-patterns-overview),
[Chrome current-tab and new-tab shortcuts](https://support.google.com/chrome/answer/157179).
