# Shared client API, version 1

This contract describes the implemented release candidate. It is not an internet
enrollment API. Native Windows requests use the existing local principal; a paired
browser profile supplies its own `X-Device-Id` and `X-Device-Token`. The server
resolves the account from that credential and rejects another requested account.
An extension origin alone does not authorize a shared-client request. Never put
credentials in URLs. HTTPS or the existing private connection is required for
remote use; do not publicly expose the desktop control server.

| Method and route | Input | Result and effect |
|---|---|---|
| GET `/api/client/state?date=YYYY-MM-DD` | Optional IST date | Version-1 source projection, described by `client-state-v1.schema.json` |
| GET `/api/client/calendar?month=YYYY-MM` | Calendar month | Version 1, account, month, persisted activities; at most 2,000 rows |
| POST `/api/client/routine` | Stable request ID, date, College/Gym choices and their revisions, bath plans, wake target; Gym time and supported minutes only for Yes | One atomic confirmation through existing decision and schedule engines |
| GET `/api/client/account` | None | Separate Google, Drive, backup and local identity-link states; no tokens |
| POST `/api/client/account/{action}` | Action-specific body | Native local Windows only; asynchronous work returns `pending: true`, then poll status |
| GET `/api/client/demo` | None | Actual shared local allowance/cooldown; may persist expiry metadata |
| POST `/api/client/demo` | Empty object | Explicit activation, 81 effective access points for 30 seconds, seven-day cooldown; no score event |
| POST `/api/taufa/evaluate_unified` | DEMO or PRODUCTION, original image bytes as base64, stable submission ID, actual category/description; production activity ID | DEMO is isolated. PRODUCTION uses the existing canonical retained submission path. No successful AI result is implied by upload alone. |

Account actions are `import-config`, `sign-in`, `link-account`, `grant-drive`,
`verify-drive`, `select-folder`, `sync`, and `sign-out`. Import accepts an owner-
provided Desktop OAuth document. Identity linking requires explicit confirmation.
Folder selection requires a folder ID that passes verification. The extension
does not invoke these Windows mutation routes: its worker uses Chrome Identity
and separate Google Drive authorization, with tokens confined to trusted contexts.

Routine body example (example values, not a live command):

```json
{
  "request_id": "stable-routine-request-uuid",
  "date": "2026-11-02",
  "attends_college": true,
  "college_revision": 0,
  "bath_plans": [{"planned_time": "07:30", "window_start": "08:00", "window_end": "08:30"}],
  "gym_planned": false,
  "gym_revision": 0,
  "wake_up_time": "07:00"
}
```

Replay the identical body with the identical request ID. Reusing it for changed
choices fails. Stale choice revisions or a confirmed schedule require the existing
authorized change flow. Errors roll back the whole confirmation. Offline browser
queues contain planning changes only, scoped to the authenticated account; they
are not a receipt of schedule confirmation or an offline points award.

Shared-client JSON requests are bounded to 65,536 bytes. Evidence uses the separate
bounded canonical route (15 MiB request); the UI permits 1–10 original images with
at most 10 MiB combined raw bytes. File names, MIME types and SHA-256 accompany
originals. The backend retains and validates the actual bytes; client hashes cannot
approve a proof. Individual category/duplicate/temporal policies still apply.

Expected errors include 400 invalid input/revision conflict, 403 unauthorized
account or unsupported remote mutation, 404 unknown action, 409 `DEMO_COOLDOWN`,
and 503 `SERVICE_UNAVAILABLE`. Retain the same request ID after a transport failure.
An AI processing failure stays retryable and must not be presented as an invalid
proof or an approval. Physical ambiguity can still genuinely require human review.

Version 1 accepts additional response fields. Consumers must preserve unknown
source records and statuses rather than map them to success. A breaking change
requires a new schema version. Drive manifests are backups/pending claims and
cannot import ledger events, overwrite schedules, or impersonate authority.

Known incomplete contracts: independent cloud delivery of routine/evidence data,
automatic morning entry/resume, Android Accessibility-state ingestion, persistent
resumable uploads/change-token downloads, and all-category native verification.
See the feature-parity report; live Google, official Taufa, and three-client sync
acceptance are NOT VERIFIED.
