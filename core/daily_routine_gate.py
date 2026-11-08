"""Persist wake-up verification separately from scheduled hourly attendance."""
import base64
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

IST = timezone(timedelta(hours=5, minutes=30))


def _time(value):
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
    except (TypeError, ValueError):
        return None


def _json(value):
    try:
        result = value if isinstance(value, dict) else json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _number(value, fallback=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def is_bluetooth_route(route):
    return str(route or "").upper() in ("BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET", "BLE_SPEAKER", "TYPE_BLUETOOTH_A2DP", "TYPE_BLE_HEADSET")


class DailyRoutineGate:
    def __init__(self, db):
        self.db = db
        with db._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS daily_routine_gates (
                date TEXT NOT NULL, account_id TEXT NOT NULL, is_dev_mode INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'WAKE_UP_PENDING', activated_at TEXT,
                schedule_version INTEGER NOT NULL DEFAULT 1, source_session_id TEXT,
                activation_kind TEXT NOT NULL DEFAULT 'VERIFIED_WAKE_UP', recovery_reason TEXT,
                authorized_by TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY(date, account_id, is_dev_mode))""")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(wake_up_sessions)")}
            if "active_audio_output_verified" not in cols:
                conn.execute("ALTER TABLE wake_up_sessions ADD COLUMN active_audio_output_verified INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def hardware_requirements_met(photo_liveness_passed, earbuds_route_passed=None, full_track_passed=None) -> bool:
        """Camera face liveness is mandatory. Earbuds route and audio track playback are optional."""
        return bool(photo_liveness_passed)

    @staticmethod
    def seed_approved_wakeup(db, date: str, completed_hm: str = "07:30", account_id: str = "user_lok"):
        """Seeds a verified wake-up session and approved proof review to activate the daily routine gate."""
        from core.scoring_engine import ScoringEngine
        from PIL import Image
        import io
        scoring = ScoringEngine(db)
        db.ensure_daily_record(date)
        buffer = io.BytesIO()
        Image.new("RGB", (120, 120), (100, 140, 170)).save(buffer, format="JPEG")
        evidence = scoring.store_evidence_file(
            date, "WakeUp", f"wakeup_{date}_0", "fixture-phone",
            file_bytes=buffer.getvalue(),
            metadata={"liveness_status": "PASSED", "liveness_duration_sec": 20, "valid_observation_ratio": 0.9, "eyes_detected": True}
        )
        from core.wakeup_attempt_validation import configured_track_manifest
        track_sec = configured_track_manifest()["duration_sec"]
        playback = {
            "completed": True, "headset_connected": True, "volume_ok": True, "seek_attempts": 0,
            "audio_route": "BLUETOOTH_A2DP", "track_duration_sec": track_sec, "completed_duration_sec": track_sec
        }
        timestamp = f"{date} {completed_hm}:00"
        with db._connect() as conn:
            conn.execute("""UPDATE wake_up_sessions SET camera_verified=1, camera_evidence_id=?, bluetooth_verified=1,
                active_audio_output_verified=1, bluetooth_device_name='Bluetooth earbuds', motivation_verified=1,
                track_duration_sec=?, listened_duration_sec=?, completion_pct=100, completed_at=?, playback_session_json=?
                WHERE date=? AND is_dev_mode=0""", (evidence["evidence_id"], track_sec, track_sec, timestamp, json.dumps(playback), date))
            conn.execute("UPDATE proof_reviews SET status='APPROVED', reviewed_at=? WHERE evidence_id=?", (timestamp, evidence["evidence_id"]))
        gate = DailyRoutineGate(db).reconcile(date, account_id=account_id)
        return evidence

    def inspect_checks(self, date: str, account_id="user_lok", is_dev_mode=False) -> Dict[str, Any]:
        from core.database import is_valid_retained_proof_file
        settings = self.db.get_settings(account_id)
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM wake_up_sessions WHERE date=? AND is_dev_mode=?", (date, int(is_dev_mode))).fetchone()
            session = dict(row) if row else {}
            evidence_id = session.get("camera_evidence_id")
            row = conn.execute("""SELECT ef.*, pr.status AS review_status, pr.reviewed_at,
                pr.user_id AS proof_account FROM evidence_files ef
                LEFT JOIN proof_reviews pr ON pr.evidence_id=ef.evidence_id WHERE ef.evidence_id=?""", (evidence_id,)).fetchone() if evidence_id else None
            evidence = dict(row) if row else {}
        meta = _json(evidence.get("metadata_json"))
        live = _json(meta.get("liveness_metadata")) or meta
        retained = bool(evidence and evidence.get("date") == date and int(evidence.get("is_dev_mode", 0)) == int(is_dev_mode)
                        and evidence.get("proof_account") == account_id and not evidence.get("is_deleted")
                        and is_valid_retained_proof_file(evidence.get("file_path"), "WakeUp", evidence.get("sha256_hash")))
        duration = _number(live.get("liveness_duration_sec"), _number(live.get("duration_ms")) / 1000.0)
        ratio = _number(live.get("valid_observation_ratio"), _number(live.get("valid_sample_ratio")))
        liveness = (str(live.get("liveness_status", live.get("session_state", ""))).upper() == "PASSED"
                    and duration >= 20.0 and ratio >= 0.70 and live.get("eyes_detected") is True)
        playback = _json(session.get("playback_session_json"))
        route_ok = bool(session.get("active_audio_output_verified") and is_bluetooth_route(playback.get("audio_route")))
        required_device = str(getattr(settings, "required_bluetooth_device", "") or "").strip()
        name = str(session.get("bluetooth_device_name") or "")
        device_matches = bool(name) and (required_device in ("", "*", "ANY_BLUETOOTH_AUDIO", "Any Bluetooth Audio Output") or required_device.lower() in name.lower())
        from core.wakeup_attempt_validation import configured_track_manifest
        actual_duration = _number(session.get("track_duration_sec"))
        listened = min(_number(session.get("listened_duration_sec")), _number(playback.get("completed_duration_sec")))
        # Full configured playback is mandatory. The comparison tolerates only
        # sub-millisecond serialization differences, never a rounded 99% claim.
        track_ok = bool(session.get("motivation_verified") and playback.get("completed") is True
                        and playback.get("headset_connected") is True and playback.get("volume_ok") is True
                        and _number(playback.get("seek_attempts"), -1) == 0 and actual_duration > 0
                        and abs(actual_duration - configured_track_manifest()["duration_sec"]) <= .001
                        and abs(_number(playback.get("track_duration_sec")) - actual_duration) < 0.001
                        and listened + 0.001 >= actual_duration
                        and _number(session.get("completion_pct")) >= 100.0)
        if playback.get("route_source") == "MEDIA_PLAYER_ROUTED_DEVICE":
            from core.wakeup_attempt_validation import camera_policy_error, playback_policy_error
            liveness = liveness and camera_policy_error(live) is None
            verified_playback = playback_policy_error(playback, live, playback.get("attempt_id")) is None
            route_ok = route_ok and verified_playback
            device_matches = verified_playback
            track_ok = verified_playback
        checks = {
            "photo_and_liveness": bool(session.get("camera_verified") and retained and liveness),
            "bluetooth_active_output": bool(session.get("bluetooth_verified") and route_ok and device_matches),
            "full_track": track_ok,
            "proof_approved": bool(retained and evidence.get("review_status") == "APPROVED"),
        }
        mandatory_keys = ("photo_and_liveness", "proof_approved")
        hardware_passed = self.hardware_requirements_met(
            checks["photo_and_liveness"], checks["bluetooth_active_output"], checks["full_track"])
        proof_rejected = bool(evidence and evidence.get("review_status") in ("REJECTED", "REJECTED_LATE"))
        return {"checks": checks, "hardware_passed": hardware_passed,
                "proof_approved": checks["proof_approved"],
                "proof_rejected": proof_rejected,
                "optional_checks": {
                    "earbuds_used": checks["bluetooth_active_output"],
                    "audio_played": checks["full_track"],
                },
                "missing": [k for k in mandatory_keys if not checks[k]],
                "attempt_id": playback.get("attempt_id") or live.get("attempt_id") or "",
                "completed_at": session.get("completed_at") or session.get("camera_completed_at"),
                "reviewed_at": evidence.get("reviewed_at"),
                "session_id": session.get("session_id"), "started": bool(session.get("task_started_at") or session.get("camera_evidence_id")),
                "session": session}

    def reconcile(self, date: str, account_id="user_lok", reference_now: Optional[datetime]=None, is_dev_mode=False):
        from core.time_window_engine import now_ist, resolve_schedule_timezone
        now = _time(reference_now) or now_ist()
        checks = self.inspect_checks(date, account_id, is_dev_mode)
        schedule = self.db.get_confirmed_routine_schedule(date, account_id) or {}
        zone_name = schedule.get("timezone") or "Asia/Kolkata"
        zone = resolve_schedule_timezone(zone_name)
        version = int(schedule.get("schedule_version") or 1)
        completed_time = _time(checks["completed_at"])
        local_completion = completed_time.astimezone(zone) if completed_time else None
        from core.window_state import WAKEUP_WINDOW_END
        wake_window_passed = bool(local_completion and local_completion.date().isoformat() == date and "07:00" <= local_completion.strftime("%H:%M") < WAKEUP_WINDOW_END)
        passed = checks["hardware_passed"] and checks["proof_approved"] and wake_window_passed
        if checks["hardware_passed"] and completed_time and not wake_window_passed:
            checks["missing"].append("wake_up_window_or_admin_recovery")
        state = "WAKE_UP_VERIFICATION" if checks["hardware_passed"] else "WAKE_UP_IN_PROGRESS" if checks["started"] else "WAKE_UP_PENDING"
        with self.db._connect() as conn:
            existing = conn.execute("SELECT * FROM daily_routine_gates WHERE date=? AND account_id=? AND is_dev_mode=?", (date, account_id, int(is_dev_mode))).fetchone()
            old = dict(existing) if existing else {}
            recovery = old.get("activation_kind") == "ADMIN_RECOVERY" and bool(old.get("authorized_by"))
            active = bool(passed or recovery)
            prerequisite_completed = bool((checks["hardware_passed"] and checks["proof_approved"] and not checks.get("proof_rejected")) or recovery)
            activated = old.get("activated_at")
            if active:
                state = "DAILY_ROUTINE_ACTIVE"
                if not activated:
                    activation_time = max(_time(checks["completed_at"]) or now, _time(checks["reviewed_at"]) or _time(checks["completed_at"]) or now)
                    activated = activation_time.isoformat()
                active = bool(_time(activated) and _time(activated) <= now)
                if not active:
                    state = "WAKE_UP_VERIFICATION"
            conn.execute("""INSERT INTO daily_routine_gates
                (date, account_id, is_dev_mode, state, activated_at, schedule_version, source_session_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, account_id, is_dev_mode) DO UPDATE SET
                    state=excluded.state, activated_at=COALESCE(daily_routine_gates.activated_at, excluded.activated_at),
                    schedule_version=excluded.schedule_version, source_session_id=excluded.source_session_id, updated_at=excluded.updated_at""",
                (date, account_id, int(is_dev_mode), state, activated, version, checks["session_id"], now.isoformat()))
            event_id = f"DAILY-ROUTINE-{account_id}-{date}-{int(is_dev_mode)}-v{version}"
            wake_up_time = schedule.get("wake_up_time") or "07:00"
            result = {"date": date, "account_id": account_id, "active": active, "state": state,
                      "hardware_passed": checks["hardware_passed"],
                      "proof_approved": checks["proof_approved"],
                      "prerequisite_completed": prerequisite_completed,
                      "optional_checks": checks["optional_checks"],
                      "attempt_id": checks.get("attempt_id", ""),
                      "activated_at": activated, "schedule_version": version, "event_id": event_id,
                      "timezone": zone_name, "wake_up_time": wake_up_time,
                      "missing": checks["missing"], "checks": checks["checks"],
                      "recovery_required": not active and now.astimezone(zone).strftime("%H:%M") >= WAKEUP_WINDOW_END,
                      "recovery_message": "Complete the missing wake-up checks and required proof review. After the wake-up deadline, request an administrator-authorized recovery." if not active else ""}
            inserted = False
            if active:
                cursor = conn.execute("""INSERT OR IGNORE INTO sync_queue
                    (sync_id, device_id, created_at, payload_type, payload_json, version, status)
                    VALUES (?, 'windows_pc', ?, 'DAILY_ROUTINE_ACTIVE', ?, ?, 'PENDING_SYNC')""",
                    (event_id, now.isoformat(), json.dumps(result), version))
                inserted = cursor.rowcount > 0
        if inserted:
            from core.event_bus import EventBus
            EventBus.get_instance().publish("DAILY_ROUTINE_ACTIVE", result, event_id=event_id)
        return result

    @staticmethod
    def activity_is_eligible(activity, gate, reference_now=None, for_submission=False):
        """Hourly scheduling uses configured wake-up time, not wake-up approval.

        This checks occurrence eligibility only. Callers still enforce absolute
        window bounds, proof requirements, review state and approved holds.
        """
        if activity.get("type") != "HOURLY":
            return True
        if activity.get('status') in ('BLOCKED_WAKEUP', 'SKIPPED_PRE_GATE'):
            # Legacy windows already suppressed by the previous policy keep
            # their history. Reopen only live/future windows; never manufacture
            # retroactive missed deductions through a legacy scoring endpoint.
            from core.time_window_engine import now_ist
            end = _time(activity.get('window_end'))
            if end and (_time(reference_now) or now_ist()) >= end:
                return False
        return activity.get('status') not in ('EXCUSED', 'CANCELLED', 'SKIPPED')

    def authorize_recovery(self, date, admin_session_token, reason, account_id="user_lok", reference_now=None):
        identity = self.db.verify_admin_session(admin_session_token)
        if not identity or identity.get("role") != "ADMIN":
            raise PermissionError("An authenticated administrator session is required for routine recovery.")
        if not str(reason or "").strip():
            raise ValueError("A recovery reason is required.")
        from core.time_window_engine import now_ist, resolve_schedule_timezone
        now = _time(reference_now) or now_ist()
        schedule = self.db.get_confirmed_routine_schedule(date, account_id) or {}
        zone = resolve_schedule_timezone(schedule.get("timezone") or "Asia/Kolkata")
        if date != now.astimezone(zone).date().isoformat():
            raise ValueError("Recovery can activate only today's future hourly windows.")
        with self.db._connect() as conn:
            conn.execute("""INSERT INTO daily_routine_gates
                (date, account_id, state, activated_at, activation_kind, recovery_reason, authorized_by, updated_at)
                VALUES (?, ?, 'DAILY_ROUTINE_ACTIVE', ?, 'ADMIN_RECOVERY', ?, ?, ?)
                ON CONFLICT(date, account_id, is_dev_mode) DO UPDATE SET state='DAILY_ROUTINE_ACTIVE',
                activated_at=COALESCE(daily_routine_gates.activated_at, excluded.activated_at),
                activation_kind='ADMIN_RECOVERY', recovery_reason=excluded.recovery_reason,
                authorized_by=excluded.authorized_by, updated_at=excluded.updated_at""",
                (date, account_id, now.isoformat(), reason.strip(), identity["admin_id"], now.isoformat()))
        self.db.record_admin_audit(admin_user=identity.get("username", identity["admin_id"]),
            action_type="DAILY_ROUTINE_RECOVERY", field_changed=f"routine_gate:{date}", old_value="WAKE_UP_INCOMPLETE",
            new_value={"state": "DAILY_ROUTINE_ACTIVE", "reason": reason.strip()}, device_id="admin_portal")
        return self.reconcile(date, account_id, now)

    def ingest_android_wakeup(self, conn, payload, device_id, account_id, received_at):
        """Retain device measurements and the real photo, leaving review pending.

        Called inside the existing deduplicated sync transaction. Missing fields
        leave the session incomplete; client approval/points are never imported.
        """
        date = str(payload.get("date") or "")
        from core.wakeup_diagnostics import is_diagnostic_payload
        if is_diagnostic_payload(payload) or str(payload.get("session_id") or "").startswith("diag_") or str(payload.get("diag_session_id") or "").startswith("diag_"):
            return None
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None
        registered = conn.execute("SELECT user_id FROM devices WHERE device_id=? AND pairing_state!='REVOKED' AND status!='REVOKED'", (device_id,)).fetchone()
        credential = conn.execute("SELECT 1 FROM device_credentials WHERE device_id=? AND revoked_at IS NULL", (device_id,)).fetchone()
        if not registered or registered["user_id"] != account_id or not credential:
            return None
        camera = payload.get("photo_liveness_passed") is True
        audio = payload.get("earbuds_route_passed") is True and payload.get("active_audio_output_verified") is True
        full_track = payload.get("full_track_passed") is True
        playback = _json(payload.get("playback_session"))
        live = _json(payload.get("liveness_metadata"))
        from core.wakeup_attempt_validation import android_attempt_error
        if android_attempt_error(payload):
            return None
        completion = _time(payload.get("completed_at"))
        from core.window_state import WAKEUP_WINDOW_END
        if not completion or completion.astimezone(IST).date().isoformat() != date or not "07:00" <= completion.astimezone(IST).strftime("%H:%M") < WAKEUP_WINDOW_END:
            return None
        evidence_id = str(payload.get("evidence_id") or "").strip()
        if not evidence_id or not camera:
            return None
        row = conn.execute("SELECT ef.*, pr.user_id AS proof_account FROM evidence_files ef LEFT JOIN proof_reviews pr ON ef.evidence_id=pr.evidence_id WHERE ef.evidence_id=?", (evidence_id,)).fetchone()
        if row and (row["date"] != date or row["proof_account"] != account_id or row["device_id"] != device_id):
            return None
        if not row:
            try:
                raw = base64.b64decode(str(payload.get("image_base64") or ""), validate=True)
                if not raw or len(raw) > 10 * 1024 * 1024:
                    return None
                if hashlib.sha256(raw).hexdigest() != live.get("photo_sha256"):
                    return None
                from PIL import Image
                import io
                with Image.open(io.BytesIO(raw)) as image:
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    if min(image.size) < 100:
                        return None
            except Exception:
                return None
            digest = hashlib.sha256(raw).hexdigest()
            folder = os.path.join(os.path.dirname(os.path.abspath(self.db.db_path)), "wake_up_evidence", date)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, digest + ".jpg")
            if not os.path.isfile(path):
                with open(path, "wb") as output:
                    output.write(raw)
            conn.execute("""INSERT INTO evidence_files
                (evidence_id, date, record_type, related_record_id, file_path, timestamp, device_id,
                 file_size_bytes, sha256_hash, metadata_json) VALUES (?, ?, 'WakeUp', ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, date, f"wakeup_{date}_0", path, received_at, device_id, len(raw), digest, json.dumps({"liveness_metadata": live})))
            conn.execute("""INSERT OR IGNORE INTO proof_reviews
                (review_id, evidence_id, user_id, task_code, date, status, submitted_at, signals_json)
                VALUES (?, ?, ?, 'WAKE_UP', ?, 'PENDING', ?, ?)""",
                ("wake_sync_" + hashlib.sha256(evidence_id.encode()).hexdigest()[:24], evidence_id, account_id, date, received_at,
                 json.dumps({"source": "REGISTERED_ANDROID_HARDWARE", "liveness": live})))
        else:
            # Keep the retained evidence metadata authoritative. A later event
            # cannot relabel a failed photo as passed without another capture.
            live = _json(row["metadata_json"])
        duration = _number(playback.get("track_duration_sec"))
        listened = _number(playback.get("completed_duration_sec"))
        completed = _time(payload.get("completed_at"))
        if not completed or completed > (_time(received_at) or datetime.now(IST)) + timedelta(minutes=1):
            completed = _time(received_at)
        conn.execute("""INSERT OR IGNORE INTO wake_up_sessions(session_id, date, target_time)
            VALUES (?, ?, '07:00')""", (f"wakeup_{date}_0", date))
        conn.execute("""UPDATE wake_up_sessions SET camera_verified=1, camera_evidence_id=?,
            camera_completed_at=?, bluetooth_verified=?, active_audio_output_verified=?,
            bluetooth_device_name=?, bluetooth_completed_at=?, motivation_verified=?,
            track_name=?, track_duration_sec=?, listened_duration_sec=?, completion_pct=?,
            track_started_at=?, track_ended_at=?, playback_session_json=?, playback_state=?,
            checklist_state='VALIDATING', completed_at=COALESCE(completed_at, ?), device_id=?
            WHERE date=? AND is_dev_mode=0""",
            (evidence_id, received_at, int(audio), int(audio), str(payload.get("bt_device") or ""), received_at,
             int(full_track), str(payload.get("track_name") or ""), duration, listened,
             min(100.0, listened / duration * 100.0) if duration > 0 else 0.0,
             playback.get("started_at"), completed.isoformat() if completed else received_at, json.dumps(playback),
             "COMPLETED" if playback.get("completed") is True else "INCOMPLETE",
             completed.isoformat() if completed else received_at, device_id, date))
        return date
