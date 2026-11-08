# StrictRoutines Status Helper

This is a visible, separate extension because Chrome stops running an extension
when it is disabled. The Windows companion cannot reliably infer disablement from
a missing heartbeat or undocumented Chrome preferences.

The helper uses `chrome.management.get` for **only** the pinned StrictRoutines ID,
and listens for that ID's enable, disable, install and uninstall events. It never
calls `getAll`, reads tabs, or collects unrelated extension information. Chrome's
management permission has a broad browser warning; the code restricts its use to
this one target. Disabling/removing the helper leaves the state unknown and stops
new penalty coverage. It cannot observe its own disablement.

## Installation

Use the review package's `PrepareChromeMonitor.exe` once:

```powershell
.\PrepareChromeMonitor.exe --profile-label "My Chrome profile / its Profile Path" --output "$env:LOCALAPPDATA\StrictRecords\ChromeInstall-20261018"
```

Confirm the chosen profile's **Profile Path** in `chrome://version`. Use only that
profile for both Load unpacked actions in `chrome://extensions`. Follow the
generated `INSTALL.txt`. The setup utility prepares files and enrollment only;
it never installs extensions, changes Chrome policies, or registers startup work.
Do not load this unconfigured source folder: it contains no enrollment credential.

The updated native StrictRoutines Windows app must be running. No Native Messaging
host, registry entry, hosted service or additional resident process is required.
The HTML Connect feature remains separate and does not establish extension status.

The first verified helper-to-target handshake binds both extensions' unique local
profile IDs to this enrollment. A second Chrome profile is rejected. Chrome's
extension API does not reveal the filesystem profile path: its human-readable
label is checked manually, and the runtime binding uses the two profile IDs.

## Timing and points

Choose 5, 7 or 10 minutes in the helper popup. A running episode retains its
original grace policy and exact detection deadline; setting changes apply next
episode. Chrome alarms resume reports after worker suspension. The Windows app
persists episode IDs, verified coverage, deadlines, minute references and outbox
independently in `%LOCALAPPDATA%\StrictRecords\extension_monitor`.

After grace, each **complete** additional verified minute is eligible for a
one-point proposal, only when the active Windows configuration's existing
`rules.chrome_extension_penalty_enabled` is explicitly `true`. Absent/false means
no proposals. This package does not silently enable that setting.

Windows only creates existing authoritative deduction proposals. The established
confirmation/synchronization workflow finalizes losses. The helper has no points
balance and cannot overwrite Android-confirmed points. Unknown time, stale or
replayed reports, process downtime, clock jumps, missing sequence numbers and new
browser sessions cannot fill gaps in verified coverage. Removal is reported
separately; no removal penalty policy is silently invented.

## Updating and recovery

Generate a **new** output folder with the identical label and `--update-existing`.
This reuses the enrollment/credential and preserves the Windows timer state.
Replace code in each previously loaded extension folder while keeping its
generated `setup-config.js`, then Reload the extensions in the same profile.
Do not clear Chrome extension storage or substitute a different manifest key.

Remove either extension normally in `chrome://extensions`; no automatic reinstall
occurs. Normal Windows administrator recovery remains available. To re-pair a
different profile, stop the Windows app normally, archive the enrollment and timer
files together, and run setup for a new profile. Preserve the authoritative
application database and historical records. Archived outbox data is retained for
review rather than silently applied to a new profile.

An offline companion/helper cannot establish current operational state. If other
Chrome profiles remain open while the enrolled profile closes, the state may be
unknown rather than provably closed; both stop new penalty coverage. No attempt
is made to bypass browser confirmation or management security.
