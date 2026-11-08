# StrictRoutines cloud points coordinator

**Prepared, not deployed.** There is no cloud URL, hosted database or enrolled cloud account yet. The clients correctly show **Not configured**. The live different-network/PC-off acceptance criteria remain **NOT VERIFIED**. Windows/extension optional Google connection code is prepared in the companion release; Android is unchanged. Live consent and shared-folder access are NOT VERIFIED.

This is an HTTPS transport and transactional projection of the **existing Android-authoritative `score_events` ledger**, not a new points-award engine. Android retains its existing proof/scoring rules. Windows source claims remain pending until that same authority confirms the corresponding stable event ID. A cloud credential authenticates an existing StrictRecords account/device binding; it does not let a replica award arbitrary points. The cloud does not independently inspect evidence images or prove the truth of a device's source record. Provisioning the correct trusted Android authority is a required security boundary.

## Deployment bundle

`compose.yaml` runs PostgreSQL, a limited cloud API and a separate always-running coordinator. Put these on an internet host that remains online when the PC is off. Do **not** expose the normal Windows control API to the internet. The API binds only `127.0.0.1:8080` on the host; put a trusted HTTPS reverse proxy in front of it. Forward the HTTPS scheme from that local proxy. Gunicorn's trusted-forwarded-header configuration must match the proxy address; never trust arbitrary internet-supplied forwarded headers. PostgreSQL has no published host port.

Set strong, separate `POINTS_DB_PASSWORD` and `POINTS_SCHEDULER_SECRET` values in a private host environment. Password characters used in a database URL must be URL-encoded. Keep the environment file out of source control. The optional `/internal/reconcile/tick` trigger requires its own scheduler bearer credential over HTTPS; the coordinator container already ticks every 30 seconds and does not require that trigger.

From the project root on the cloud host:

```sh
docker compose -f cloud_points/compose.yaml up --build -d
```

Production rejects a SQLite URL. SQLite is used only by the local contract/integration tests. PostgreSQL runtime, Docker startup, HTTPS reverse-proxy setup and real hosted concurrency have **NOT BEEN VERIFIED** in this environment; neither Docker nor PostgreSQL is installed here. Configure persistent database backups, a supervised always-on host and service monitoring before production use.

## Bind the existing account

Choose the actual registered Android authority. Do not infer ownership from an email, accept a client-supplied account ID, or expose a public enrollment endpoint. The operator imports an account/device mapping offline. Only a credential hash is uploaded for the existing Android registration; no Google/OAuth token is used.

After a real HTTPS origin exists, run the preparation utility on Windows with explicit values:

```powershell
python scripts/prepare_points_cloud_binding.py --database <existing-db-path> --account <existing-account-id> --authority-device <registered-android-device-id> --endpoint https://<your-points-origin> --provisioning-output <private-path>/points-provisioning.json
```

This reads the existing registration database in read-only mode, checks that the authority is its registered Android device, exports device-token hashes, and creates a separate random Windows replica credential protected by DPAPI in `%LOCALAPPDATA%/StrictRecords/PointsCloud/client.enc`. Tokens expire after 30 days on the cloud service. It never prints the raw Windows token, changes the old device credential, or edits ledger records. Treat the hash mapping as private operator data. Import it inside the API container using:

```sh
python -m cloud_points.manage provision --file /private/points-provisioning.json
```

Use a read-only private mount or a private operator shell to supply this file to the container. No public provisioning API exists. Restart Windows normally after importing the binding. The latest Android UI has its Cloud Points controls removed. A separately authorized Android configuration/update is required before deploying this origin to that client; do not claim that the present Windows package configures the phone. The APK uses its existing paired account/device credential. Never enable a guessed URL. Token rotation/revocation requires updating the operator mapping; expired credentials show Authentication required and retain pending records.

## Scheduler and reconciliation

The explicit Asia/Kolkata wall-clock slots are **00:00, 02:00, …, 18:00, 19:00, 20:00, 21:00, 22:00, 23:00** every day. No quiet-hours range was supplied. The server calculates these slots; APK and EXE do not run separate reconciliation schedulers. Each `(account_id, scheduled_at)` has one deterministic request ID and a database uniqueness constraint. Account row locks serialize revision commits. Worker leases have an attempt counter, expiry and fencing check. A lost/restarted worker resumes the same request ID; retries stop at three attempts.

After an outage, only the latest due slot is created. The scheduler does not replay every missed interval. Requests store scheduled and actual execution times, interval type, captured revision, status, safe snapshot and result. A result for an older ledger revision cannot be published as the current reconciliation. The service keeps serving the last confirmed ledger if reconciliation or Taufa fails.

The snapshot contains event IDs/idempotency keys, additions/deductions, timestamps, policy/rule versions and verification/task references. Descriptions, unnecessary profile fields, evidence images, OAuth tokens and device credentials are omitted. Administrator references are pseudonymized. Event identity/content is immutable: stale or conflicting snapshots and missing historical events require review, never deletion. Existing invalid/pending records are not silently promoted. Eligibility changes to retained financial content require an authorized append-only correction/review rather than rewriting old history.

The cloud computes the aggregate from the validated existing manifest, preserving the established `min(100, verified_sum)` behavior and negative balances. Clients cannot submit an arbitrary aggregate as authority. The APK checks the receipt against its sent snapshot and keeps its local authority. Windows reconciles the same manifest through `core/android_ledger.py`. Minimized cloud and original local-transfer envelopes can share a source revision only when their financial/source/approval content agrees; descriptions alone cannot create a points difference. Local networking remains available as a fallback until a real cloud deployment is configured and verified.

## Taufa — official access required

Exclusive destination: `https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b`.

The current parity requirement supersedes the earlier captured-session experiment.
The service factory does not load captured browser cookies or its old session
vault. It creates `UnavailableTaufa`, reporting
`UNAVAILABLE_NO_OFFICIAL_API_CONFIGURED`. No credentials are distributed and no
live AI evaluation or deployed reconciliation is claimed by this release.
The historical `taufa_cached_session.py` implementation is retained for source
compatibility, not selected by the service factory. Do not enable it as a
substitute for an official documented API or treat a browser cache as consent
for the new integration.

The owner confirmed that official Taufa access has not been supplied. An operator
must implement and verify a documented authorized conversation adapter before
live AI reconciliation can be accepted. Requests must correlate the exact thread,
account, submission/request ID and ledger revision with an actual structured
response. Evidence evaluation must retain and send the original files when its
category requires visual evidence. Never infer success from cookies, an email,
an HTTP connection, a missing answer, or a local heuristic.

Ordinary ledger delivery continues independently. AI results are audit reports;
they cannot directly commit, delete, credit, deduct or replace point events.
`COMPLETE_TAUFA_UNAVAILABLE` means deterministic stored-ledger processing without
AI, not Taufa verification. No authority receipt means
`WAITING_FOR_AUTHORITY_LEDGER`.

See `docs/WINDOWS_EXTENSION_FEATURE_PARITY.md` and
`docs/WINDOWS_EXTENSION_PARITY_SETUP.md` for the actual tested scope and remaining
hosting/OAuth/API inputs. Docker, PostgreSQL, HTTPS deployment and three-client
cross-network acceptance remain NOT VERIFIED.

## Client behavior

Windows polls every 30 seconds once explicitly configured, backing off to at most five minutes on failure. No worker or requests start without a protected configuration. It shows confirmed points, revision, pending changes, reconciliation state, last sync and Taufa availability. Connection/authentication failure keeps the last confirmed ledger.

Android posts the actual minimized existing manifest directly over HTTPS using **any network**, including mobile data. A persisted JobScheduler job requests a sync about every 15 minutes, plus explicit/manual/startup attempts. Android may defer background jobs during Doze/battery restrictions; exact client notification timing is not guaranteed. The fixed hourly/two-hour reconciliation cadence belongs to the cloud coordinator. Pending Windows source claims are displayed for the existing proof/admin review path and are never automatically awarded by the cloud UI.

## Validation

See `reports/cloud-points-20261030` for the original cloud preparation results and `reports/taufa-cache-reconciliation-20261030` for the subsequent captured-session verification, package metadata and remaining acceptance gaps. Local integration tests exercise real loopback HTTP with temporary SQLite fixtures, plus scheduling boundaries, catch-up, simultaneous writers/proposals, stable event IDs, expired/revoked credentials, bad source scopes, missing/changed history, worker restarts, stale AI results, unavailable Taufa and retention after failure. Fixture tests are not a deployed PostgreSQL, real device or separate-network demonstration. The separate historical Taufa check above used a real captured session and response.


## Separate Demo Access state

Demo Apps Unlocked uses `/v1/demo-access/state` (GET) and `/v1/demo-access/activate` (POST) with the same authenticated account/device binding. It stores only account reference, last successful activation, expiry and status in `demo_access_state`; it does not publish or mutate the points ledger, history, reviews, proposals, daily summaries or reconciliation snapshots. Production database time and an account row lock enforce one 30-second eligibility grant per rolling seven days across clients and reenrollments. The grant score is 81 only at the entertainment-access boundary.

Android keeps activation disabled until this authenticated HTTPS service is explicitly configured and reachable. No local clock or local-storage reset can authorize another activation. See `reports/demo-access-20261031/README.md` for timer behavior, test evidence and deployment/device limitations. Deployment is still not claimed complete.


## Single YouTube Reward

The same explicitly enrolled account now has a separate reward authority. These routes require HTTPS, the existing bearer credential and X-Device-Id; no Google token or image is sent:

- GET /v1/rewards/youtube/state returns the account's actual claimable rewards, active owner, fixed expiry and consumption history.
- POST /v1/rewards/youtube/reconcile accepts persisted submission/deadline and final evidence-approval attestations only from the enrolled Android authority. It cannot change an acceptance/deadline on retry.
- POST /v1/rewards/youtube/activate atomically claims one available entitlement for exactly 1,200,000 ms. An active reward belongs to the claiming authenticated device. Another device can view it but cannot claim or consume it independently.
- POST /v1/rewards/youtube/stop records early consumption only for the claiming device. Expiry is also recorded on the next authoritative state/claim check; no client timer can extend the fixed expiry.

The original reward policy is one entitlement per qualifying Hourly Attendance occurrence, score at least 6/8 with category/description/evidence matches and no disqualifying duplicate, after the existing approval policy completes. Eligibility includes exactly ten minutes before the original actual submission-window deadline, interpreted in Asia/Kolkata. No new daily/weekly cooldown is introduced.

Reward records use dedicated youtube_reward_* tables. Granting, activating, consuming and expiring rewards do not write the points ledger, points proposals, points history, points reconciliation requests or Daily Summary. Windows/its local browser companion is one PC playback integration; the Android package uses its own enrolled device credential. Independent app/security rules remain separate.

This service is still prepared, NOT deployed or configured. Keep claims unavailable until a real HTTPS origin and account/device enrollment exist and the clients are explicitly configured. The reward implementation does not fall back to a second local-PC entitlement or to Demo Access. PostgreSQL deployment and live A55/PC/browser switching must be verified after configuration. No production entitlement was consumed by the contract tests.
