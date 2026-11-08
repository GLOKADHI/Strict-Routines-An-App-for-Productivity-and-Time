# StrictRoutines Windows and extension parity report

Prepared 31 October; final repairs verified 1 November 2026, Asia/Kolkata. Reference: Android v1.18.12 and the supplied
Windows/extension parity request. **Final acceptance is not met.** The artifact is
a release candidate with the limitations below, not a verified production rollout.

The owner confirmed that the extension OAuth client ID does not exist and that
official Taufa API access has not been supplied. No cloud host/database was supplied.
Live Google consent, shared Drive access, official AI grading and cloud-independent
three-client synchronization are therefore NOT VERIFIED.

| Feature | APK reference | Windows implementation | Extension implementation | Shared dependency | Tests completed | Limitation / classification | Build scope |
|---|---|---|---|---|---|---|---|
| Google identity | Latest APK currently has Google removed | Restored Desktop PKCE/loopback flow, existing OAuth configuration, separate local account link | Chrome Identity integration with setup-required state | Google OAuth plus immutable local account binding | Consent separation, account isolation, secret exclusion | **Blocked by API or permissions** for real consent; APK identity restoration outside this build | Windows + MV3 source |
| Drive consent | Account authorization separate from files | Separate grant and read/write verification; DPAPI token storage retained | Separate `drive.file` request and folder marker verification | Google Drive | Mock contracts; no real grant | **Implemented but not adequately tested**; correct extension client absent | Windows + MV3 source |
| Shared visible folder | StrictRecords folder ID | Existing folder registry and explicit folder selection | Reads paired Windows folder ID only for matching Google subject, or explicit selection | Visible My Drive folder | Ownership/read-write code paths reviewed | **Partially implemented**; same-folder live check NOT VERIFIED | Packaged |
| File backup | Retained original evidence | Account-scoped durable queue, SHA-256 check, original file upload, sanitized manifest | Original image bytes sent to the shared evidence endpoint | Existing upload queue and Drive client | Queue deduplication, account scope, changed source rejection paths | **Partially implemented**; no crash-resume session persistence or live Drive verification | Packaged |
| Cloud synchronization | Same account and revisions | Existing transactional replica package retained | Shared PC client projection; private PC pairing supported | Undeployed HTTPS/PostgreSQL service | Transactional coordinator fixtures and retry contracts | **Blocked by API or permissions**; cloud host/provisioning absent, extension still PC-dependent | Cloud package retained |
| Replace temporary Bluetooth transfer | Cloud is the requested replacement | Existing transfer fallback retained pending a verified migration | Not exposed as extension-native Bluetooth | Actual cloud deployment | No live replacement acceptance | **Not implemented**; removal is deferred while the replacement is unavailable, rather than silently removing a working recovery path | Existing fallback retained |
| Single points ledger | Android authoritative receipts | Reads confirmed receipt; shows unavailable if absent | Same authenticated projection; no local score replacement | Existing Android ledger checkpoint | Projection and scope tests | **Implemented and tested** in fixtures; live three-client revision comparison NOT VERIFIED | Packaged |
| Taufa destination | Designated attendance conversation | Captured-cookie fallback removed from active validation readiness; configured API must identify exact conversation/submission/evaluation | Calls same shared endpoint | Authorized Taufa API | Unconfigured retry, correlation and mode policy checks | **Blocked by API or permissions**; no official access, live evaluation NOT VERIFIED | Packaged guard |
| Scheduled reconciliation | IST two-hour / evening hourly cadence | Existing independent cloud-worker package retained | Can consume backend result when provisioned | Cloud scheduler + PostgreSQL | Existing cadence, duplicate scheduler, outage fixtures | **Partially implemented**; not deployed; EXE alone is not represented as scheduler | Cloud package |
| Demo/Main validation | Same policy with isolated Demo | Unified section; standalone evaluator is pure; Main uses canonical retained submission path | Same mode selector and endpoint, original files | Existing validator and approval engine | Unified validation suite; no Demo score writes | **Partially implemented**; canonical hourly path tested, all live category samples NOT VERIFIED | Packaged |
| Physical proof policy | Genuine Wake-Up/Bath/Gym checks | Replaced hardcoded physical-proof scores with NEEDS_REVIEW; no fabricated grade | Displays actual response | Existing native/manual policy | Physical outcomes and no-award assertions | **Implemented and tested** for evaluator fixtures; legacy non-hourly reviewer paths need further audit | Packaged |
| Original multi-image files | Up to ten originals | Uses canonical retained attachment bundle | Reads original bytes, SHA-256, MIME and stable submission ID | Existing evidence store | Canonical tests plus input-size contracts | **Implemented but not adequately tested** live; no official attachment API available | Packaged |
| Daily entry | Saved College, Bath, Gym choices | Shared setup form and atomic confirmation | Same form and durable offline request IDs | Existing DailyCollegeDecision/DailyGymDecision/TimeWindowEngine | Whole-routine rollback, confirmation replay, revision conflicts | **Partially implemented**; automatic first-morning/resume prompt not completed | Packaged |
| College Bath windows | 07:30–08:00 activity; 08:00–08:30 proof | Existing fixed-window engine reused; cancelled-plan uniqueness migration retains history | Same saved records; one/two Bath fields | Existing shared policy | Routine, college, original collision reproduction and migration rollback/preservation fixtures | **Implemented and tested** in isolated fixtures; live upgrade NOT VERIFIED | Packaged |
| One / two baths | No phantom second task for one | One-bath confirmation creates one activity | Same count and form | Existing college decision policy | One-bath / No-Gym atomic confirmation test | **Implemented and tested** in fixtures; live two-bath migration NOT VERIFIED | Packaged |
| Wake-Up verification | Camera/actual checks mandatory; earbuds/audio optional | Existing native result read; no simulated camera | Android-only capability stated | Existing wake-up gate | Existing optional-check contracts retained | **Implemented but not adequately tested** across live clients | Existing engine retained |
| Wake-Up target editor | Entry-only target confirmation | Old ordinary Schedule selector disabled; entry form has target | Entry form target | Existing schedule validation | UI rendering; backend range validation | **Partially implemented**; existing admin reschedule override remains | Packaged |
| Gym Yes/No | No creates no session; Yes asks time/duration | Conditional controls and existing decision engine | Same control and supported-duration policy | Existing Gym rules | Conditional UI, No-Gym materialization, idempotency | **Implemented and tested** in fixtures | Packaged |
| Alternate-day Gym history | Anchor, rest, pause/resume | Existing GymRoutine engine retained | Reads shared state | Existing account database | Gym routine regression fixtures | **Implemented and tested** existing engine; live multi-client history NOT VERIFIED | Retained |
| Offline Gym activation | After 17:00 with eligible heart; no PC dependency on phone | Existing engine and state retained | Shared state; browser cannot activate an Android session while PC unreachable | Existing heart reservation / activation receipts | Existing morning/offline-Gym sync suite | **Partially implemented** cross-platform; Android-native behavior unchanged | Retained |
| Demo Apps Unlocked | 81 effective points, 30s, seven-day cooldown | Same persisted local demo table; real score kept separate | Shared control and effective browser score | Existing paired-PC demo authority | Account-sharing, concurrent requests, expiry, real ledger isolation | **Implemented and tested** on one paired PC; cloud-wide multi-PC allowance NOT VERIFIED | Packaged |
| Android Accessibility | Five-minute grace, countdown and native warning | Does not claim native monitoring; capability scope stated | Does not claim native monitoring | APK persisted status ingestion not yet available | Capability assertions | **Partially implemented**; exact live Android grace state is not mirrored | Android unchanged |
| Entertainment websites | Actual per-target thresholds | Existing policy decisions retained; demo affects only effective score | Exact-tab content enforcement retained | Existing restriction manager | 33 JS checks plus threshold and independent-policy fixtures | **Implemented and tested** in isolated tests; live Comet reload/site audit NOT VERIFIED | Packaged |
| StrictBot messages | Persisted facts, exact enforcement copy | Summary uses persisted source records | Shared summary; existing website popup copy retained | Existing summary and restriction engines | Summary/activity count consistency and JS regressions | **Partially implemented**; every old popup/message not re-audited live | Packaged |
| Robot animation | Approved articulated premium robot | Small shared vector rig with separate hips, knees, shoulders/elbows, head, cape; walks/slows/idles | Same in-app rig; no desktop-overlay claim | Shared UI assets | Rendered at three widths; no JS errors | **Partially implemented**; cinematic/native visual parity and full speaking choreography NOT VERIFIED | Packaged shared UI |
| Calendar | Actual task windows and states | Persisted activity calendar with monthly selection | Same authenticated monthly projection | Existing scheduled_activities | Calendar/summary same activity set, responsive screenshots | **Partially implemented**; every routine/activation overlay not represented | Packaged |
| Device Center | Compact readable pairing/status | Existing pairing retained; compact account/Drive/sync controls in shared panel | Profile pairing plus account states | Device credential registry | Setup copy tests, scope checks, layouts | **Partially implemented**; original Device Center not fully redesigned | Packaged |
| Daily Summary | Source-derived totals | Existing summary with same persisted activity set; no fabricated balance | Same source projection | Existing summary/ledger | Count-set and account-scope tests | **Partially implemented**; legacy proof-count versus file-count and penalty text require more audit | Packaged |
| Data preservation | Existing records remain | Additive metadata and idempotency receipts; no raw DB upload, reset or account replacement | Existing extension key/enrollment retained | Existing database and profile storage | Account switch retains pending records; token digest not reusable as bearer | **Implemented and tested** in fixtures; live migration rollback NOT VERIFIED | Packaged migrations |

## Actual verification record

- Focused Python suites: **138 passed**, including real isolated HTTP API checks,
  published schema validation, cross-account rejection, consent separation,
  original-file backup queue, routine transaction rollback and shared demo state.
- JavaScript extension suites: 33 passed, including trusted storage, no token
  exposure, public-HTTP rejection, route allowlist and existing popup regressions.
- Shared UI: six sections rendered at 1180, 800 and 420 px without horizontal
  overflow or JavaScript errors; conditional Gym controls checked. These use an
  isolated database, not fabricated claims about the installed user's state.
- The final separate regression run recorded **121 passed**. The reproduced
  legacy College-plan uniqueness collision is fixed with a tested, atomic,
  data-preserving constraint migration. No real user database was upgraded in
  this release check. Migration tests cover copy preservation and rollback.
- The date-sensitive cloud test now queries its historical fixture reporting
  date. Production reporting-date selection is unchanged. An obsolete hourly
  wake-up-gate assertion now follows the approved independent hourly policy;
  College Bath 1 still requires real Wake-Up Verification.
- Google browser consent, Drive live round trip, live Comet extension installation,
  production proof awards, native Android checks, independent cloud deployment,
  and full three-client record parity are **NOT VERIFIED**.

- Windows v2.5.0 compiled successfully. The actual frozen EXE imported the server,
  verified the shared assets, retained extension ID and Google runtime presence
  without opening the user database or starting background services. This is not
  a live native-window startup or a Google consent test. The running older app
  was not force-closed or replaced.
- Extension manifest version is **1.8.1**. Both packaged copies and all runtime
  source files match the recorded build snapshot. Existing browser profiles still
  require a normal reload; no live-site acceptance is implied.
- Experimental Torch/Qt/notebook training runtimes are excluded from this Windows
  build. WebView2 remains the UI. A local experimental model is not a substitute
  for a configured Taufa service.

## Remaining owner inputs and work

1. Create the extension-specific public OAuth client ID for the retained extension
   ID. Approve Google test-user access / app consent configuration as required.
2. Supply official authorized Taufa API access. No scraped-cookie integration is
   substituted, and no successful AI completion is claimed.
3. Provision the HTTPS cloud host and PostgreSQL identities before independent
   scheduler and different-network three-client sync can be verified.
4. Complete Drive crash-resume/change tracking, Android accessibility-state
   ingestion, live migration checks and live visual QA.
5. Restart the prepared Windows release normally and reload the extension before
   claiming this new code is active in the installed clients.
6. Complete live evaluation receipt correlation once official access is supplied.
   The diagnostic wrapper now retains the provider's returned evaluation ID;
   local IDs are used only when no provider receipt exists. Tests cover this,
   but no live official evaluation was performed.

Delivery includes the source archive, portable Windows folder/archive, extension
archive, prepared cloud bundle, versioned API/schema and additive migration notes.
Archive hashes and machine-readable statuses are recorded in BUILD_INFO.json.

The final candidate is built from the saved 31 October source snapshot plus the
database constraint migration and provider evaluation-ID fix. A compiled-module
comparison verifies all other 3,355 bundled modules remain identical. Newer
1 November workspace edits, including a native robot runtime and changed Taufa
session readiness, were detected and preserved. They are deliberately excluded
from this candidate and have not been reviewed or merged here. Installing this
candidate over that newer runtime is not an automatic update performed by this work.
