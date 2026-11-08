# StrictRecords pre-beta audit status

Updated: 2026-10-11 (Asia/Kolkata)

## Findings and verification

| ID | Severity | Finding and change | Automated verification | Device/manual status |
|---|---|---|---|---|
| SCORE-001 | P0 | Positive score entries now require an approved review and a valid retained proof image. Client activity claims cannot award positive points. | Android scoring rules and Python proof/ledger regressions pass. | Tablet build displays **0/100** with no current score; installed APK matches the built artifact. A fresh data wipe was intentionally not performed. |
| SCORE-002 | P0 | Windows startup recalculates the daily total from the verified ledger and retains historical rows. Android derives its score from eligible ledger events. | Startup regression confirms an unproved stale total becomes 0 without deleting the event. | Existing tablet data was preserved; current dashboard score is 0/100. |
| SCHED-001 | P0 | Schedule and submission-window logic uses persisted timestamps and half-open boundaries; exact boundary and timezone regressions are present. | Focused submission-window tests pass. | Android starts and displays a scheduled window. Full cross-device restart and delayed-submission behavior remains unverified on physical Windows/Android sessions. |
| ADMIN-001 | P0 | Protected server APIs now bind requests to a resolved administrator session and restrict the initial `1234` setup key to key rotation/logout. Android blocks its protected console until the setup key is changed; PIN lockout state persists in its database. | Default-key server flow and authorization regressions pass; Android unit suite passes. | The actual PIN-change interaction was not exercised on the user's tablet. |
| DEVICE-001 | P0 | Device sync, command polling, transfer queues, and acknowledgements require valid device credentials and command ownership. Revoked credentials are rejected. | Focused server credential/revocation tests pass. | Full Android-to-Windows reconnect and popup reconciliation was not manually exercised. |
| RESTRICT-001 | P0 | Restriction status distinguishes requested policy from confirmed enforcement; live reconciliation retries policy changes, and browser rules that override the requested result are surfaced as unknown/conflicting. | Browser-policy and live-reconciliation focused suite passed (13 tests). | Actual YouTube/Instagram access in Chrome/Edge and policy persistence after browser restart still need physical Windows verification. Tablet currently displays YouTube Allowed and Instagram Restricted; this snapshot alone does not confirm actual access. |
| DB-001 | P1 | Replaced short-lived Android database-helper construction with one application-context helper shared by activities and services. | Android unit suite passes. | Before the fix, device logs repeatedly reported leaked SQLite connections. After installing the fix, the app launched and no such warning appeared in the sampled startup log window (~15 seconds). Long-duration soak testing remains open. |
| RELEASE-001 | P0 | Distribution artifacts have checksums and the APK signing certificate was verified. | Build/package succeeded; manifest hashes and sizes match all three artifacts; source archive contains no database or proof records. | Only a debug-signed APK is available. Production signing credentials were not configured, so this is not a production release. |

## Test results

- Android JVM unit suite: **16 tests, 0 failures** (`:app:testDebugUnitTest`).
- Python proof and strict-session regressions: **11 passed**.
- Python administrator session/default-key authorization regressions: **2 passed**.
- Browser policy and live restriction reconciliation suite: **13 passed**.
- Android tablet: updated with `adb install -r`; first install time remained `2026-10-05 14:46:15`, version is **1.14.0 (24)**, app opened without a fatal exception, and dashboard score showed **0/100**.
- Installed tablet APK SHA-256 matches the built APK: `d8dbe6afd6e3dac1827f5a7b46aadd9768fd1fce575df195e227f76f0b670d29`.

## Distribution artifacts

- Windows executable: `.dist/StrictRecords.exe` — 326,970,681 bytes; SHA-256 `6e682119537f5038d9e3dffcc527e9140ded425f108d22e08828c632a8080182`.
- Android debug APK: `.dist/StrictRecords-debug.apk` — 5,598,845 bytes; SHA-256 `d8dbe6afd6e3dac1827f5a7b46aadd9768fd1fce575df195e227f76f0b670d29`.
- Clean source archive: `.dist/StrictRecords-source.zip`; its size and SHA-256 are recorded in the manifest.
- Manifest: `.dist/manifest.json`.

## Release gate

**Do not mark beta-ready yet.** Production signing is missing. Physical Windows browser enforcement, strict schedule/popup behavior across both devices, update installation, and longer-duration Android/Windows resource checks remain unverified. The tablet was updated in place; no app data or proof history was cleared.

## Main changed areas

Scoring and proof validation (`core/scoring_engine.py`, `core/database.py`, Android `StrictRecordsLocalEngine.kt`), server authorization and device credentials (`core/server.py`, `core/connection_pipeline.py`), Windows restriction reconciliation (`windows/restriction_manager.py`, `windows/browser_policy_controller.py`), Android administrator access and shared SQLite helper use (`MainActivity.kt`, `DisciplinePermissionManager.kt`, `AppUpdateManager.kt`, `StrictEnforcementService.kt`, `StrictAccessibilityService.kt`, `RestrictionLockActivity.kt`, `StrictRecordsWidgetProvider.kt`, `WakeUpAlarmReceiver.kt`), and focused Python/Kotlin regressions.
