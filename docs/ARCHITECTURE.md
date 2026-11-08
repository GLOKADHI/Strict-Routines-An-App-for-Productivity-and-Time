# STRICTRECORDS — System Architecture & Technical Specification

> **Tagline:** *"Earn Your Freedom."*  
> **Primary Purpose:** Make the user accountable for the entire day via a connected daily-record operating system across Android and Windows.

---

## 1. Architectural Overview

StrictRecords is architected around a **Single Connected DailyRecord** (`DailyRecord` keyed by `date` + `user_id`). Every action, alarm, verification step, hourly checkpoint, device telemetry sample, photo evidence, score change, entertainment restriction decision, and end-of-day AI review reads from and writes to this unified daily record.

```
+-----------------------------------------------------------------------------------+
|                           STRICTRECORDS ECOSYSTEM                                 |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  +-----------------------------+               +-------------------------------+  |
|  |   DEVICE 1: ANDROID APP     |               |  DEVICE 2: WINDOWS PC AGENT   |  |
|  |  (Native Kotlin + SQLite)   |               | (Win32 Telemetry + StrictBot) |  |
|  +--------------+--------------+               +---------------+---------------+  |
|                 |                                              |                  |
|                 |  Offline-First Sync Queue (Idempotent UUID)  |                  |
|                 +----------------------+-----------------------+                  |
|                                        |                                          |
|                                        v                                          |
|  +-----------------------------------------------------------------------------+  |
|  |                 CENTRAL CORE ENGINE & SYNC SERVER (Python/SQLite)           |  |
|  |  - Connected DailyRecord Manager      - Centralized Scoring Engine          |  |
|  |  - Cross-Device Verification Engine   - Anti-Cheating Consistency Checker   |  |
|  |  - StrictBot State & Message Engine   - DailyRecord.json Evidence Exporter  |  |
|  |  - OpenAI-Compatible AI Analyzer      - Web/Desktop Command Center UI       |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Connected DailyRecord Data Model

All entities link directly to `daily_records(date, user_id)`:

1. **`users`**: `user_id`, `username`, `created_at`
2. **`devices`**: `device_id`, `user_id`, `device_name`, `platform` (`ANDROID` | `WINDOWS`), `app_version`, `last_seen`, `sync_status` (`ONLINE` | `OFFLINE` | `PENDING_SYNC` | `SYNCING` | `SYNCED`)
3. **`user_settings`**: All 25+ configurable parameters (wake-up target `07:00`, snooze penalty `-5`, wake-up points `+15`, on-time bonus `+5`, motivation track points `+10`, required track completion `100%`, required Bluetooth device, bath deadline `10:00`, bath points `+10`, bath missed penalty `-10`, gym scheduled time `18:00`, gym min duration `30` min, gym points `+15`, gym skip penalty `-10`, hourly start `08:00`, hourly end `22:00`, hourly productive points `+5`, hourly missed penalty `-5`, evidence points `+3`, entertainment violation `-10`, reward threshold `80`, controlled mode threshold `60`, restriction reset time `07:00`, restricted Android apps, restricted Windows apps, local music apps, AI API endpoint/model, privacy controls).
4. **`daily_records`**: `record_id`, `date`, `user_id`, `is_dev_mode`, `wakeup_status`, `wakeup_completed_at`, `snooze_count`, `bath_status`, `bath_completed_at`, `gym_status`, `gym_duration_minutes`, `hourly_completed_count`, `hourly_total_count`, `hourly_missed_count`, `productive_hours`, `evidence_count`, `violation_count`, `positive_points`, `negative_points`, `final_score`, `score_mode` (`REWARD_UNLOCKED` | `CONTROLLED_MODE` | `STRICT_MODE`), `reward_unlocked`, `grade`, `ai_report_json`, `updated_at`.
5. **`scheduled_tasks`**: Tracks every daily task (`WAKE_UP`, `BATH`, `GYM`, `HOURLY_08`..`HOURLY_22`) with distinct semantics:
   - `EXACT_TIME` (`WAKE_UP` at `07:00`)
   - `DEADLINE` (`BATH` before `10:00`)
   - `SCHEDULED_START` (`GYM` at `18:00`)
   - `RECURRING_CHECKPOINT` (`HOURLY` every hour `08:00`–`22:00`)
   - Task states: `UPCOMING`, `ACTIVE`, `COMPLETED`, `LATE`, `MISSED`, `SKIPPED`, `PENDING`, `VERIFIED`.
6. **`wake_up_sessions`**: Tracks the 3-step verification state machine (`camera_verified`, `camera_evidence_id`, `bluetooth_verified`, `bluetooth_device_name`, `motivation_verified`, `track_name`, `track_duration_sec`, `listened_duration_sec`, `completion_pct`, `interruption_count`, `attendance_status`).
7. **`bath_records`**: `bath_id`, `date`, `deadline_time`, `completed_at`, `verification_mode` (`MANUAL`, `PHOTO_PROOF`, `EXTERNAL_EVIDENCE`), `evidence_id`, `status`.
8. **`gym_sessions`**: `gym_id`, `date`, `scheduled_time`, `start_timestamp`, `end_timestamp`, `duration_minutes`, `min_required_minutes`, `device_id`, `user_confirmed`, `evidence_id`, `notes`, `status`.
9. **`hourly_records`**: `hourly_id`, `date`, `hour_slot` (e.g. `10:00-11:00`), `primary_activity`, `secondary_activities_json`, `description`, `evidence_id`, `android_summary_json`, `windows_summary_json`, `verification_result`, `integrity_flag` (`VERIFIED` | `NORMAL` | `INCONSISTENT_EVIDENCE`), `status`.
10. **`evidence_files`**: `evidence_id`, `date`, `record_type`, `related_record_id`, `file_path`, `timestamp`, `device_id`, `file_size_bytes`, `sha256_hash`, `metadata_json`, `is_deleted`.
11. **`score_events`**: `event_id`, `date`, `timestamp`, `event_type`, `device_id`, `description`, `points`, `evidence_id`, `related_task_id`, `audit_json`, `is_dev_mode`.
12. **`device_activity_logs`**: Timestamped telemetry from Android (`UsageStatsManager` app usage, screen time, audio state) and Windows (`Win32` active window title, process name, category, idle seconds).
13. **`structured_logs`**: Immutable audit log (`timestamp`, `date`, `event_code`, `device_id`, `details_json`).
14. **`sync_queue`**: Offline-first queue storing `sync_id` (UUID), `payload_type`, `payload_json`, `status` (`PENDING_SYNC`, `SYNCING`, `SYNCED`), preventing duplicate application via idempotent `sync_id` tracking.
15. **`scoring_rule_versions`**: Immutable snapshots of scoring rules (`version_number`, `created_at`, `created_by`, `reason`, `rules_json`) so past `DailyRecord` scores are never silently rewritten when Admin updates rules.
16. **`admin_audit_logs`**: Role-enforced audit log (`audit_id`, `timestamp`, `actor_role`, `action_type`, `target_entity`, `before_json`, `after_json`, `reason`) recording every Admin policy change or score correction.
17. **`device_pairings`**: USB/Token challenge-response trust registry (`pairing_id`, `device_id`, `device_name`, `platform`, `usb_serial`, `challenge_code`, `pairing_token`, `trust_state`).
18. **`focus_sessions`**: Timed Focus & Deep Work sessions (`session_id`, `date`, `title`, `mode`, `target_minutes`, `completed_minutes`, `interruption_count`, `status`, `points_awarded`).
19. **`break_logs`**: Categorized break tracking (`break_id`, `date`, `category`, `planned_minutes`, `actual_minutes`, `overrun_minutes`, `status`, `points_delta`).
20. **`restriction_events`**: Full enforcement history (`restriction_id`, `date`, `device_id`, `platform`, `target_app_or_url`, `action_taken`, `score_at_event`, `required_threshold`).
21. **`integrity_signals`**: Anti-cheat and clock/telemetry consistency signals (`signal_id`, `date`, `signal_type`, `severity`, `confidence`, `details_json`).
22. **`daily_journals`**: End-of-day reflection & tomorrow preparation (`journal_id`, `date`, `reflection_text`, `went_well`, `distractions`, `tomorrow_plan`).
23. **`achievements_unlocked`**: Milestone badges unlocked (`achievement_id`, `date`, `title`, `description`, `xp_bonus`).
24. **`sync_conflicts`**: Explicit conflict resolution log (`conflict_id`, `sync_id`, `entity_type`, `resolution_strategy`, `client_json`, `server_json`).

---

## 3. Centralized Scoring Engine, Rule Versioning & Auditability

- Every score mutation MUST occur by recording an immutable `ScoreEvent` pinned to the active `rule_version`.
- `DailyScore = clamp(PositivePoints - NegativePoints, 0, MaxScore)` (where `PositivePoints` is the sum of positive `ScoreEvent`s and `NegativePoints` is the magnitude of negative `ScoreEvent`s).
- **4-Tier Score States**:
  - `0–59`: `STRICT_MODE` (entertainment restricted, strict accountability)
  - `60–79`: `CONTROLLED_MODE` (local MP3 allowed, streaming locked)
  - `80–89`: `REWARD_UNLOCKED` (full privileges unlocked)
  - `90–100`: `ELITE_DAY` (elite badge, XP bonus, sovereign progression)
- **Immutable Score Corrections**: Admin score adjustments create a `SCORE_CORRECTION` `ScoreEvent` and an `AdminAuditEvent` rather than mutating historical events.

---

## 4. Platform Capability Disclosures & Safe Recovery Principles

1. **Android App Monitoring & Overlay**:
   - Uses `UsageStatsManager` (`PACKAGE_USAGE_STATS`) for foreground app detection and `MediaStore.ACTION_IMAGE_CAPTURE` for real camera verification.
   - Standard Android apps cannot forcefully kill other processes without Accessibility/MDM root privileges; StrictRecords implements legitimate foreground detection + StrictBot warning dialogs and never fakes permissions.
2. **Windows Process, Browser Tab & Workstation Lock**:
   - Uses Win32 `GetForegroundWindow`, `GetWindowThreadProcessId`, `GetLastInputInfo`, and window title inspection to detect active applications and browser tabs (`YouTube`, `Netflix`, `Reddit`, `Twitch`, etc.).
   - Supports optional `LockWorkStation()` (`ctypes.windll.user32.LockWorkStation`) only when explicitly enabled by Admin (`windows_workstation_lock_enabled = True`) and never blocks `Ctrl+Alt+Del`, Task Manager, or emergency OS recovery.
3. **Privacy**:
   - No secret camera/microphone recording, no background screenshot uploads, no intimate photos ever requested for bath verification. Users can delete any evidence file at any time (`[ DELETE FILE ]` soft-deletes and zeroes disk bytes while preserving audit metadata).

