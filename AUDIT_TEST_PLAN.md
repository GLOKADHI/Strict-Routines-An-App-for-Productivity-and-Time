# STRICTROUTINES / STRICTRECORDS — COMPREHENSIVE ENGINEERING AUDIT & TEST PLAN

**Date**: 2026-10-13  
**Status**: ACTIVE AUDIT & DEFECT REMEDIATION  
**Platforms**: Windows Desktop EXE (Edge WebView2 / Python 3.14.7), Android Native APK (Kotlin / SDK 34), Shared Authoritative Backend (Python / SQLite), Admin Portal  

---

## 1. SYSTEM ARCHITECTURE & DISCOVERY BASELINE

### 1.1 Architecture Inventory
- **Windows Desktop Application**:
  - **Runtime & Language**: Python 3.14.7 on Windows x64.
  - **Framework & UI Shell**: Edge WebView2 desktop shell via `pywebview`, communicating with internal local API server on `http://127.0.0.1:8765`.
  - **Entry Point**: `desktop_app.py` / `StrictRecords.exe` (PyInstaller packaging with `StrictRecords.spec`).
  - **Lifecycle & Single Instance**: System-wide Win32 Mutex `Local\StrictRecords_App_Mutex_v2` and HTTP instance check via `windows/single_instance_manager.py`.
  - **Background Daemons**: Low-CPU Windows agent loop (`windows/agent.py`), topmost restriction monitor (`windows/restriction_manager.py`), system tray (`windows/tray_manager.py`), shutdown listener (`windows/shutdown_monitor.py`).
- **Android Native Application**:
  - **Runtime & Language**: Kotlin, Android Gradle Plugin, targetSdk 34, compileSdk 34, minSdk 24.
  - **Application Package**: `com.strictrecords.app` (versionCode 40, versionName 1.15.12).
  - **Entry Activity**: `MainActivity.kt` with Home, Tasks, Rewards, Devices, StrictBot views.
  - **Verification Controllers**: `WakeUpVerificationController.kt`, `WakeUpCameraSession.kt`, `WakeUpAudioSession.kt`, `CameraFaceAnalysisEngine.kt`.
  - **Background Services & Receivers**: `StrictEnforcementService.kt` (SpecialUse FGS), `StrictAccessibilityService.kt`, `WakeUpAlarmReceiver.kt`, `UpdateActionReceiver.kt`.
  - **Local Sandbox Store**: `StrictRecordsLocalEngine.kt` managing internal SQLite database.
- **Backend & Authoritative Store**:
  - **Technology**: Python HTTP server (`core/server.py`) powered by `ThreadingHTTPServer` on port 8765.
  - **Authoritative Database**: SQLite database `strictrecords.db` managed by `StrictRecordsDB` (`core/database.py`).
  - **Scoring Engine**: `ScoringEngine` (`core/scoring_engine.py`) with ledger tracking daily points, immutable transaction history, and 4-tier score states (`STRICT_MODE`: 0–59, `CONTROLLED_MODE`: 60–79, `REWARD_UNLOCKED`: 80–89, `ELITE_DAY`: 90–100).
  - **Time Engine**: `TimeWindowEngine` (`core/time_window_engine.py`) locked to IST (`Asia/Kolkata`) handling 07:00–09:30 wake-up window, 08:00–22:00 hourly checkpoints, and midnight reset.
  - **Device Pairing & Sync**: `ConnectionPipelineManager` (`core/connection_pipeline.py`) with UDP beacon discovery, SHA-256 device token authentication, and bidirectional event sync queue.
  - **StrictBot AI Engine**: `StrictBotAssistant` (`core/strictbot_assistant.py`) and `StrictBotAIReviewer` (`core/ai_robot_admin.py`).
- **Admin Portal**:
  - Embedded in `core/ui_dashboard.py` and served via `/api/admin/*` endpoints.
  - Admin RBAC: `admin_users` table with SHA-256 hashed salts, password/PIN validation, and session tokens.

### 1.2 Baseline Test Execution Results (Pre-Modification)
- **Android Unit Tests**:
  - Total: 68 tests across 8 suites (`CameraFaceAnalysisEngineTest`, `DisciplinePermissionManagerTest`, `DisciplineScoringRulesTest`, `StrictBotNetworkPolicyTest`, `TargetAppMonitoringPolicyTest`, `TargetForegroundTrackerTest`, `WakeUpAttemptRulesTest`, `WakeUpCameraPolicyTest`).
  - Baseline Result: **68 PASSED, 0 FAILED** (100% pass rate).
- **Python Backend & Windows Tests**:
  - Total: 404 tests collected across 50 test files.
  - Baseline Result: **342 PASSED, 62 FAILED**.
  - All original databases safely backed up to `scratch/backup_pre_audit/`.

---

## 2. FEATURE INVENTORY & CLASSIFICATION MATRIX

| Feature Domain | Feature Name | Platform | Classification | Notes / Root Cause |
|---|---|---|---|---|
| **A. Lifecycle** | Single-Instance Enforce & Mutex | Windows | IMPLEMENTED | Tested via `test_single_instance_enforcement.py` (Passed) |
| **A. Lifecycle** | Close to Tray & Background Daemon | Windows | IMPLEMENTED | Tested via `test_tray_and_background_lifecycle.py` (Passed) |
| **A. Lifecycle** | Android Activity Lifecycle & Recovery | Android | IMPLEMENTED | Verified in `MainActivity.kt` & unit tests |
| **B. Auth & Security** | Admin Authentication & Key Rotation | Shared | BROKEN | `is_default_admin_pin()` lock and test credentials mismatch blocking admin endpoints |
| **B. Auth & Security** | Device Token SHA-256 Validation | Shared | IMPLEMENTED | SHA-256 token validation enforced on protected routes |
| **C. Device Pairing** | UDP Discovery & Pairing Handshake | Windows/Android | PARTIALLY IMPLEMENTED | Sync transfer count & event queue tests failing in `test_connection_pipeline.py` |
| **D. Data Sync** | Cross-Device Event Synchronization | Windows/Android | BROKEN | 5 failures in `test_connection_pipeline.py` and 2 in `test_cross_device_ecosystem.py` |
| **E. Points Ledger** | Authoritative Daily Ledger & Integrity | Shared | IMPLEMENTED | Passed in `test_daily_reset_and_negative_score.py` & `test_score_audit_consistency.py` |
| **E. Points Ledger** | Controlled Manual Score Adjustment | Shared | BROKEN | Missing reason validation test failed in `test_admin_ecosystem.py` |
| **F. Midnight Reset** | Idempotent IST Midnight Boundary Reset | Shared | IMPLEMENTED | Passed in `test_daily_reset_and_negative_score.py` |
| **G. Wake-up 3-Step** | Step 1: Camera Face & Eye Verification | Android | IMPLEMENTED | 20s required observation, eye landmarks, head pose checks pass in `WakeUpCameraPolicyTest` |
| **G. Wake-up 3-Step** | Step 2: Bluetooth Earbuds Output Route | Android | IMPLEMENTED | Audio routing & connected state verified in `WakeUpAudioSession` |
| **G. Wake-up 3-Step** | Step 3: Full Track Completion | Android | IMPLEMENTED | Track progress without skip/timer bypass enforced |
| **G. Wake-up 3-Step** | Strict 3-Step Logical Conjunction Gate | Shared | BROKEN | Server/core wake-up validation tests failing in `test_core_phases.py` and `test_critical_integration_repair.py` |
| **H. Hourly Proof** | Windows Hourly Fullscreen Popup Lifecycle | Windows | BROKEN | Popup timing & deduplication failing in `test_fullscreen_hourly_popup_lifecycle.py` & `test_hourly_popup_single_instance.py` |
| **H. Hourly Proof** | Android Hourly Submission & Window Gate | Android/Shared | PARTIALLY IMPLEMENTED | Auto-closing missed proofs and event bus sync failing in `test_integration_spec_v2.py` |
| **I. StrictBot AI** | Proof Review & Grounded Evidence Analysis | Shared | PARTIALLY IMPLEMENTED | StrictBot mock/vlm tests failing in `test_hourly_submission_sync_and_ai_robot.py` |
| **J. Sleep Appeal** | Mi Fitness / Health Connect Sleep Appeal | Android/Shared | NOT IMPLEMENTED | Confirmed absent from current source tree |
| **K. Rewards** | Instagram & YouTube Desktop Restriction | Windows | IMPLEMENTED | Topmost modal & window closure verified in `test_youtube_instagram_restrictions.py` |
| **K. Rewards** | Android Target-App Allowlist & Foreground Filter | Android | IMPLEMENTED | Fails open, excludes banking/UPI, tested in `TargetAppMonitoringPolicyTest` |
| **L. Admin Approval** | Proof Approval / Rejection Audit Trail | Shared | BROKEN | Evidence image validation format throwing `ValueError: Evidence must be a valid image` |
| **M. Shutdown Bonus** | Early Windows PC Shutdown Bonus | Windows/Shared | IMPLEMENTED | 21/21 tests passing in `test_pc_shutdown_bonus.py` & `test_server_pc_shutdown_routes.py` |
| **N. UI Behavior** | Dark Cinematic UI & Responsiveness | Windows/Android | IMPLEMENTED | Verified on Edge WebView2 shell and Android layouts |
| **O. Admin Portal** | Web Dashboard & Administration API | Shared | BROKEN | Blocked by 403 Forbidden due to default admin PIN gate in test server |
| **P. Schedulers** | Routine Scheduler & Notification Delivery | Windows/Android | BROKEN | Dynamic boundaries & schedule anchor failing in `test_integration_spec_v2.py` |

---

## 3. PRIORITIZED DEFECT QUEUE

### Queue Priority Classification:
- **P0**: Security, data corruption, unauthorized scoring, or catastrophic failure.
- **P1**: Core scoring, authentication, wake-up verification, device pairing, or synchronization broken.
- **P2**: Significant feature malfunction or reliability problem (hourly popups, updater, exam proctor).
- **P3**: Smaller UI, usability, or noncritical reliability issues.

### Prioritized Bug List:
1. **BUG-P1-01**: Admin Authentication and Initial Key Rotation Policy Conflict (causes 403 Forbidden across admin API and session exit).
2. **BUG-P1-02**: Evidence Image Format Validation Rejection in Test / Headless Environments (throws `ValueError: Evidence must be a valid image at least 100x100 pixels` during synthetic and dev proof submissions).
3. **BUG-P1-03**: Controlled Manual Score Adjustment Missing Reason Validation (does not raise ValueError when reason is empty).
4. **BUG-P1-04**: APK Updater Status Code Inconsistency (returns 401 instead of 403 or 404 for releases API).
5. **BUG-P1-05**: Cross-Device Synchronization Event Transfer Inconsistency (transferred events count and idempotency in `ConnectionPipelineManager`).
6. **BUG-P1-06**: Wake-up 3-Step Verification Server Conjunction Enforcement (server rejection / state discrepancies in `test_core_phases.py` and `test_critical_integration_repair.py`).
7. **BUG-P2-01**: Windows Fullscreen Hourly Popup Trigger Timing & Single-Instance Deduplication.
8. **BUG-P2-02**: Dynamic Wake-Up Window Boundaries and Hourly Schedule Anchoring.
9. **BUG-P2-03**: Strict Windows Session Lifecycle & Privilege Score Fluctuation Key Errors.
10. **BUG-P2-04**: Exam Proctor Verification Endpoint and Element Regressions.

---

## 4. ONE-BUG-AT-A-TIME REMEDIATION WORKFLOW

Every fix will follow the mandatory cycle:
1. **Select highest-priority open bug.**
2. **Reproduce deterministically with isolated pytest command.**
3. **Identify exact root cause in source.**
4. **Apply smallest safe correction preserving all existing policies.**
5. **Run targeted regression test to verify fix.**
6. **Re-run neighboring tests to ensure zero regression.**
7. **Document resolution in audit log.**
8. **Advance to next defect.**
