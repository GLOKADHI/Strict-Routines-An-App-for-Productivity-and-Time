# Strict Routines - An App for Productivity and Time

**Plan your day. Protect your focus. Build consistency.**

**[Explore the live StrictRecords product website](https://glokadhi.github.io/Strict-Routines-An-App-for-Productivity-and-Time/)**

The website covers the platform architecture, device responsibilities, media and
reward policies, reliability, privacy safeguards, documentation, and maintained
development roadmap. Source walkthroughs are explicitly distinguished from live
product demonstrations.

StrictRoutines is a productivity and time-management project for Android, Windows,
and supported browser extensions. Earlier source names use **StrictRecords**;
they belong to the same application and remain in place for compatibility.

## Why we created this app

Making a timetable is easy. Following it while studying, working, exercising,
and switching between a phone and a PC is much harder. Reminders alone often
disappear into the background, and distracting apps make a short break stretch
far beyond its intended time.

We created StrictRoutines to connect planning with daily accountability. It brings
routine choices, scheduled check-ins, evidence of completed activities, focus
sessions, and a visible history together so progress can be understood and acted
on. Its purpose is to help people spend time deliberately and develop consistent
habits across the devices they actually use.

## What the project contains

- **Daily routines:** wake-up targets, hourly attendance, college-day plans, bath
  sessions, gym choices, and calendars based on saved schedules.
- **Focused work:** study-session workflows, reminders, and supported controls for
  distracting apps or websites.
- **Accountability:** evidence submission, verification states, and an auditable
  points ledger distinguishing planned, submitted, and approved activities.
- **StrictBot:** an animated companion for reminders, status, and guidance.
- **Device coordination:** Android, Windows, and browser components with pairing,
  synchronization queues, and explicit connection states.

This is an actively developed project. Availability depends on the platform,
permissions, configured services, and the particular build. Native Android
Accessibility and camera features run on Android; a browser extension cannot
monitor arbitrary desktop applications. Cloud deployment and third-party AI
evaluation require their own authorized configuration. A completed-looking
screen does not establish a verified synchronization or approval result.

## Project layout

| Folder | Purpose |
| --- | --- |
| `android/` | Native Android app and tests |
| `core/` | Existing schedule, evidence, ledger, database, and server logic |
| `windows/` | Windows integration, desktop controls, and native components |
| `browser_extension/` | Browser companion and supported website controls |
| `browser_extension_monitor/` | Separate browser status-helper source |
| `shared/` | Shared policy, contracts, and client assets |
| `strictfocus/` | Frontend integration sources |
| `ios/` | Companion experiments and camera integration source |
| `new_model/` | Local research code; private datasets and model weights excluded |
| `cloud_points/` | Transactional cloud-coordinator and deployment preparation |
| `tests/`, `docs/`, `tools/` | Automated checks, architecture notes, and tools |

## Development

The project was developed on Windows. The desktop app needs its Python/runtime
dependencies and Microsoft Edge WebView2; Android development needs Android SDK
tooling and a Gradle-compatible JDK. Machine-local SDK paths, signing keys, account
credentials, evidence, and existing databases are intentionally not published.

After preparing the required local development environment, run the server from
the project root:

```powershell
python core/server.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` for the local interface. The Windows desktop entry is
`desktop_app.py`; its platform integrations require Windows and the corresponding
installed dependencies. Keep the desktop control API on an authorized local or
private connection.

The Android project lives in `android/`. Open it in Android Studio and configure
your local SDK and signing settings. Unit checks use the Gradle task
`testDebugUnitTest`; building an APK is a separate task. Release signing secrets
belong in protected local configuration or environment variables.

Focused Python and JavaScript tests are under `tests/`. Some integration checks
require Windows, native dependencies, test devices, or explicitly configured
services; they should not be interpreted as environment-independent tests.
Personal fitness screenshots used during local verification are excluded; tests
depending on those private fixtures require an appropriate local test dataset.

## GitHub Pages

The GitHub Pages product showcase is built from `index.html` and `assets/site/`
by `.github/workflows/pages.yml` on pushes to `main`. It uses the same repository;
no second application project or copied frontend is required. Build and deployment
instructions are in [the website guide](docs/WEBSITE.md).

GitHub Pages serves the public documentation. The Python backend, Windows EXE,
Android APK, private device pairing, and live evidence processing run in their
own supported environments; they are not hosted by the static Pages site.

## Privacy and local data

The repository excludes personal databases, schedules and evidence exports,
browser profiles, cookies, tokens, OAuth secrets, signing identities, large model
weights, recordings, generated installers, and build caches. Keep these local.
Publishing source does not authorize uploading personal evidence or credentials.

The original workspace stays in place. Git tracks the reviewed source files;
ignored local application data is not deleted or reset.
