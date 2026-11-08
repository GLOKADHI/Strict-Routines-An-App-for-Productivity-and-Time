# Build provenance and reproduction

This release candidate uses the saved source from the completed 31 October build
plus two runtime repairs: `core.database` and `core.taufa_ai_validator`. On
1 November the shared workspace contained newer unrelated runtime edits. Those
were preserved and deliberately excluded rather than overwritten or implicitly
included without testing. The distributed source ZIP describes this candidate,
not the newer shared working directory.

The original full build used Python 3.14.7 and PyInstaller 6.22.3 on Windows 11,
with its hook set 2026.7. It completed successfully. The final build reused that
verified dependency graph and recompiles the two changed modules; no imports or
external dependencies were added by either repair. A comparison of actual PYZ
archives confirms that only those two of 3,357 compiled modules differ. The
native EXE was then rebuilt with PyInstaller and its frozen verification mode
passed without opening an application database or starting the backend.

For a full independent rebuild from the extracted source, install the Windows
application's runtime/build dependencies in an isolated environment and use:

```powershell
python -m PyInstaller --noconfirm --distpath dist --workpath build StrictRecordsApp.spec
```

Keep the resulting `StrictRecords.exe` and `_internal` folder together. User
databases, evidence, OAuth JSON and tokens are not build inputs. The source
archive includes the native browser bridge DLL and original public assets;
model training weights are not included. Torch, Qt and notebook runtimes remain
excluded by the spec. No substitute AI approval is enabled by the build.

Verification after building:

```powershell
StrictRecords.exe --verify-build-report C:\Temp\strictroutines-build-check.json
```

Use a fresh report path and check its actual result. This mode verifies frozen
imports and shared assets; it does not prove native GUI startup, browser
extension reload, Google consent, official Taufa access or live synchronization.
The full application requires the existing WebView2 runtime and its normal
protected-exit/restart flow. Do not bypass that flow to activate a test artifact.

The four Android source files in this source ZIP are read-only audit inputs for
the Google-removal regression; they are not an Android build project or an APK
update. The Android client was not rebuilt by this candidate packaging work.
