# Windows and browser companion update

This is a release candidate, not a claim of completed three-client parity.
See `WINDOWS_EXTENSION_FEATURE_PARITY.md` for tested functionality and blockers.

## Run the Windows package

Keep the entire `StrictRecords` directory with `StrictRecords.exe` and `_internal`.
The portable package needs no installer. Do not copy only its EXE to the Desktop.
Exit the older app normally using its existing exit controls before starting
the new release. A running older backend otherwise retains its older routes.
No forced process termination or protected-exit bypass is included in this update.

The updated client is in Settings → Account, shared routine, Calendar & Demo Unlock.
The existing dashboard and pairing controls remain available. The updated package
continues using the existing workspace database. It does not bundle user data.

## Windows Google sign-in

The existing Desktop OAuth JSON supplied by the owner is loaded from:
`%LOCALAPPDATA%/StrictRecords/GoogleAuth/google_client_config.json`.
It is not embedded in either distributable. Tokens use the existing Windows
DPAPI-protected `google_session.enc` store. No browser cookies are used for Google.

1. Select Sign in with Google. The Desktop flow opens the system browser and
   uses PKCE, a random state value, and a loopback callback.
2. Complete Google account selection yourself. In Testing mode, Google may
   require the selected account in the project's approved test-user list.
3. Confirm the Google identity for the existing local StrictRecords account.
   This binds an immutable Google subject without replacing the local account,
   history, device tokens, or ledger.
4. Select Grant Drive access separately. The client requests `drive.file` and
   verifies folder ownership and a temporary marker upload/read/delete before
   displaying Drive connected.
5. All clients must use the same visible `StrictRecords` folder ID. The folder
   selection control verifies the existing folder; it does not infer access
   from an email address or request public sharing.
6. Select Back up files & metadata now to opt into the persistent file queue.
   Successful backup enables periodic retry while the Windows backend is running.

An account switch retains old uploads for their original subject; it does not
delete evidence or silently deliver it to the new Google account. An unrelated
Google subject must be explicitly linked before files can be backed up.

Desktop OAuth reference: [Google installed-application flow](https://developers.google.com/identity/protocols/oauth2/native-app).

## Browser extension

The package is Manifest V3, version 1.8.1. Its existing signing key is retained,
so the unpacked extension ID remains `iincmekgilffpjmmmcgloliadppioajc`.
Prepared Arc, Brave, Chrome, Comet, Edge, Opera and Vivaldi source folders are
refreshed without changing their enrollment/setup identities or browser storage.
Reload an existing unpacked installation through the browser's extension page;
the update does not change browser security settings or silently install itself.

Open the toolbar → Account & full client. Pair that browser profile using a
fresh one-time code from the PC Device Center. Its credential is stored only in
trusted extension storage, and API calls are limited to the shared-client routes.
Website content scripts cannot read the device token or call account actions.

The extension OAuth client ID has **not been created** by the owner. Google
sign-in therefore shows setup required. After creating the correct public
Chrome-extension client for the installed ID, configure it before repackaging:

    python tools/configure_extension_oauth.py --client-id YOUR_PUBLIC_ID.apps.googleusercontent.com

Do not use the Desktop JSON or put a client secret in the extension. Native
`chrome.identity.getAuthToken` is supported only where the actual browser
provides that Google identity mechanism; availability in Comet/Edge/other
Chromium variants remains unverified. Unsupported browsers must show unavailable
rather than reuse desktop cookies or pretend authentication completed.

Identity reference: [Chrome Identity API](https://developer.chrome.com/docs/extensions/reference/api/identity).
Drive permission reference: [Google Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

## Taufa and cloud deployment

The owner confirmed there is no official Taufa API access yet. No live proof
evaluation is claimed by this release. Captured browser cookies are not accepted
as supported API authorization; the old capture-import route returns retired.
Unconfigured/network failures retain evidence and remain retryable, without
creating an admin ticket solely because of the outage. Genuine physical-proof
ambiguity follows the existing human-review policy.

An authorized API adapter must accept original image bytes with the exact
category, description and submission ID, and return that submission ID, a real
evaluation ID, and designated conversation
`891a3c1e-eed2-460e-bc05-6b8ac94bec5b`. Credentials belong in protected runtime
configuration (`TAUFA_AI_ENDPOINT` / `TAUFA_AI_API_KEY` supported by the existing
adapter), never in the extension. Configure only a documented authorized endpoint.
Do not guess or populate a URL from this document.

The existing `cloud_points` deployment package requires a real HTTPS host,
PostgreSQL, provisioned existing account/device identities, and an independent
worker. No such host has been provided. Its IST reconciliation scheduler is
contract-tested but not deployed. It schedules every two hours before 19:00 and
hourly thereafter; ordinary ledger synchronization does not depend on Taufa.
Google Drive is a file/backup store, not an atomic points database. Incoming
Drive JSON is retained as a pending claim and cannot directly insert score events
or overwrite a newer schedule.

Different-network access through the already configured private Tailscale PC
connection is retained. It requires the PC/backend to remain online and is not
a substitute for an independent cloud scheduler.

## Migration and recovery

- Keep a normal backup of the existing SQLite database and evidence directory.
  This release neither resets them nor uploads the raw credential-bearing database.
- Migrations add account-binding and upload-scope metadata to existing stores;
  daily routine confirmation adds an idempotency receipt table. The shared
  `demo_access_state` table retains the phone's existing weekly activation.
- Local routine confirmation is atomic across College, Bath, Gym, schedule,
  materialized activities, synchronization event, and confirmation receipt.
  Validation conflicts roll back the entire attempt.
- Offline routine requests retain their request IDs; conflicting requests need
  review rather than overwriting newer saved plans. Uploaded proof originals
  retain checksums; a missing or changed source is a retryable upload failure.
- Roll back application binaries by restarting the previously retained app.
  Do not restore an older database over newer ledger/evidence records.
- The separate paired-demo helper and its Tailscale port 8766 remain configured.
  It shares the same local cooldown table with Windows and the extension.
  No new real demo activation was consumed by these release tests.
- Persistent resumable upload across a process crash, Drive change-token download
  reconciliation, and a cloud-wide demo authority still need further work/testing.

The Android APK is not rebuilt or replaced by this Windows/extension release.
