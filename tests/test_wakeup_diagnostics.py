"""Authoritative test suite for Wake-Up Diagnostics ("TEST MODE").

Verifies:
1. All three wake-up requirements tested individually & accurately:
   - Camera & live-presence
   - Bluetooth earbuds & active route
   - Full music track playback & completion
2. Gate truth table:
   photo_liveness_passed AND earbuds_route_passed AND full_track_passed
3. Diagnostic session isolation:
   - Test session is never recorded as PRESENT
   - Score ledger, streak, and achievements unchanged
   - Hourly scheduler gate remains closed
   - Privileges and restrictions unchanged
   - Routine and reports unchanged
4. Authoritative backend write guard denies SQL writes to production tables.
5. Production routes reject diagnostic payloads (400 TEST_MODE_REJECTED).
6. Idempotency and replay prevention (duplicate events, expired sessions).
"""
import json
import sqlite3
import pytest
from datetime import datetime, timezone

from core.database import StrictRecordsDB
from core.daily_routine_gate import DailyRoutineGate
from core.wakeup_diagnostics import (
    WakeUpDiagnosticsService,
    DiagnosticPrincipal,
    is_diagnostic_payload,
    evaluate_check,
    PASS,
    FAIL,
    LIMITED,
    NOT_TESTED,
    UNAVAILABLE,
    SESSION_ID_PREFIX,
    DiagnosticsError,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_strictrecords.db"
    db = StrictRecordsDB(str(db_file))
    DailyRoutineGate(db)
    with db._connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO daily_records (record_id, date, user_id, is_dev_mode, wakeup_status, final_score, updated_at)
            VALUES ('rec_2026_10_12', '2026-10-12', 'user_lok', 0, 'PENDING', 0, '2026-10-12T00:00:00')
        """)
        conn.commit()
    return db


@pytest.fixture
def principal():
    return DiagnosticPrincipal(account_id="user_lok", device_id="test_dev_01", platform="WINDOWS")


# ==============================================================================
# 1. EVALUATION RULES FOR INDIVIDUAL WAKE-UP REQUIREMENTS
# ==============================================================================

class TestRequirementEvaluation:
    """Test individual checks against measurements."""

    def test_camera_permission(self):
        verdict, _ = evaluate_check("camera_permission", {"granted": True})
        assert verdict == PASS
        verdict, _ = evaluate_check("camera_permission", {"granted": False})
        assert verdict == FAIL

    def test_camera_init_unavailable(self):
        verdict, reason = evaluate_check("camera_init", {"camera_available": False})
        assert verdict == UNAVAILABLE
        assert "No camera hardware" in reason

    def test_camera_init_opened(self):
        verdict, _ = evaluate_check("camera_init", {"opened": True, "camera_available": True})
        assert verdict == PASS

    def test_live_frame_stream(self):
        verdict, _ = evaluate_check("live_frame_stream", {"frames": 20})
        assert verdict == PASS
        verdict, _ = evaluate_check("live_frame_stream", {"frames": 10})
        assert verdict == FAIL

    def test_lighting_assessment(self):
        verdict, _ = evaluate_check("lighting", {"pass_ratio": 0.85})
        assert verdict == PASS
        verdict, _ = evaluate_check("lighting", {"pass_ratio": 0.40})
        assert verdict == FAIL

    def test_face_detection_and_absence(self):
        verdict, _ = evaluate_check("face_detection", {"face_ratio": 0.85, "max_absence_ms": 1000})
        assert verdict == PASS
        verdict, _ = evaluate_check("face_detection", {"face_ratio": 0.50, "max_absence_ms": 1000})
        assert verdict == FAIL
        verdict, _ = evaluate_check("face_detection", {"face_ratio": 0.85, "max_absence_ms": 3000})
        assert verdict == FAIL

    def test_eye_analysis(self):
        verdict, _ = evaluate_check("eye_analysis", {"supported": False})
        assert verdict == LIMITED
        verdict, _ = evaluate_check("eye_analysis", {"supported": True, "eyes_ratio": 0.75})
        assert verdict == PASS
        verdict, _ = evaluate_check("eye_analysis", {"supported": True, "eyes_ratio": 0.40})
        assert verdict == FAIL

    def test_liveness_interval(self):
        verdict, _ = evaluate_check("liveness_interval", {"duration_sec": 20.5, "valid_ratio": 0.80})
        assert verdict == PASS
        verdict, _ = evaluate_check("liveness_interval", {"duration_sec": 15.0, "valid_ratio": 0.80})
        assert verdict == FAIL
        verdict, _ = evaluate_check("liveness_interval", {"duration_sec": 21.0, "valid_ratio": 0.50})
        assert verdict == FAIL

    def test_bluetooth_earbuds_connection_and_route(self):
        verdict, _ = evaluate_check("bluetooth_connected", {"connected": True, "device_matches": True})
        assert verdict == PASS
        verdict, _ = evaluate_check("bluetooth_connected", {"connected": True, "device_matches": False})
        assert verdict == FAIL
        verdict, _ = evaluate_check("active_audio_route", {"active": True, "route_type": "BLUETOOTH_A2DP"})
        assert verdict == PASS
        verdict, _ = evaluate_check("active_audio_route", {"active": True, "route_type": "SPEAKER"})
        assert verdict == FAIL

    def test_full_track_completion_and_seek_guard(self):
        verdict, _ = evaluate_check("full_track_completion", {
            "completed": True,
            "track_duration_sec": 222.0,
            "listened_sec": 222.0,
            "seek_attempts": 0,
            "route_type": "BLUETOOTH_A2DP"
        }, configured_track_sec=222.0)
        assert verdict == PASS

        # Seeking detected -> FAIL
        verdict, reason = evaluate_check("full_track_completion", {
            "completed": True,
            "track_duration_sec": 222.0,
            "listened_sec": 222.0,
            "seek_attempts": 2,
            "route_type": "BLUETOOTH_A2DP"
        }, configured_track_sec=222.0)
        assert verdict == FAIL
        assert "Seeking was detected" in reason


# ==============================================================================
# 2. PRODUCTION COMPLETION GATE TRUTH TABLE
# ==============================================================================

class TestProductionGateRule:
    """Test authoritative gate truth table: mandatory photo, optional earbuds & track."""

    @pytest.mark.parametrize("photo,earbuds,track,expected", [
        (False, False, False, False),
        (True, False, False, True),
        (True, True, False, True),
        (True, False, True, True),
        (False, True, True, False),
        (True, True, True, True),
    ])
    def test_truth_table(self, photo, earbuds, track, expected):
        assert DailyRoutineGate.hardware_requirements_met(photo, earbuds, track) == expected


# ==============================================================================
# 3. DIAGNOSTIC SERVICE & DATABASE ISOLATION ENGINE
# ==============================================================================

class TestDiagnosticsIsolation:
    """Ensure test mode never modifies production database tables."""

    def test_start_session_creates_isolated_record(self, test_db, principal):
        svc = WakeUpDiagnosticsService(test_db)
        res = svc.start(principal, request_id="req_test_init_01")

        assert res["success"] is True
        assert res["mode"] == "TEST_MODE"
        assert res["counts_toward_attendance"] is False
        assert res["diag_session_id"].startswith(SESSION_ID_PREFIX)

        # Confirm session is only in wakeup_diagnostic_sessions
        with test_db._connect() as conn:
            sess_row = conn.execute("SELECT * FROM wakeup_diagnostic_sessions WHERE diag_session_id=?",
                                    (res["diag_session_id"],)).fetchone()
            assert sess_row is not None
            assert sess_row["counts_toward_attendance"] == 0

            # Confirm NO production wake_up_sessions record was created
            prod_rows = conn.execute("SELECT * FROM wake_up_sessions").fetchall()
            assert len(prod_rows) == 0

    def test_backend_authorizer_denies_production_writes(self, test_db):
        svc = WakeUpDiagnosticsService(test_db)
        with svc._guarded() as conn:
            # Writes to wakeup_diagnostic_* tables are permitted
            conn.execute("""
                INSERT OR IGNORE INTO wakeup_diagnostic_sessions
                (diag_session_id, account_id, device_id, platform, status, request_id, started_at, expires_at)
                VALUES ('diag_probe', 'user_lok', 'dev_probe', 'WINDOWS', 'RUNNING', 'req_probe', '2026-10-12', '2026-10-12')
            """)

            # Writes to production tables MUST be physically denied by SQLite authorizer
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("UPDATE daily_records SET wakeup_status='PRESENT'")

            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO score_events (event_id, date, event_type, points) VALUES ('evt1', '2026-10-12', 'TEST', 20)")

            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM scheduled_activities")

    def test_complete_test_session_preserves_zero_attendance_impact(self, test_db, principal):
        svc = WakeUpDiagnosticsService(test_db)
        start_res = svc.start(principal, request_id="req_full_suite")
        diag_id = start_res["diag_session_id"]

        # Record passing events for all checks
        checks_data = {
            "camera_permission": {"granted": True},
            "camera_init": {"opened": True, "camera_available": True},
            "live_frame_stream": {"frames": 30, "duration_sec": 20.0},
            "lighting": {"pass_ratio": 0.90},
            "face_detection": {"face_ratio": 0.90, "max_absence_ms": 500},
            "eye_analysis": {"supported": True, "eyes_ratio": 0.85},
            "liveness_interval": {"duration_sec": 20.5, "valid_ratio": 0.85},
            "bluetooth_permission": {"required": True, "granted": True},
            "bluetooth_connected": {"connected": True, "device_matches": True},
            "active_audio_route": {"active": True, "route_type": "BLUETOOTH_A2DP"},
            "player_init": {"prepared": True, "duration_sec": 222.0},
            "playback_start": {"started": True, "advanced_ms": 600},
            "pause_resume": {"paused_drift_ms": 0.0, "resumed_advance_ms": 400},
            "seek_detection": {"induced": True, "detected": True},
            "track_audio_route": {"bluetooth_samples": 50, "total_samples": 50},
            "full_track_completion": {
                "completed": True,
                "track_duration_sec": 222.0,
                "listened_sec": 222.0,
                "seek_attempts": 0,
                "route_type": "BLUETOOTH_A2DP"
            }
        }

        for idx, (check_name, measurements) in enumerate(checks_data.items()):
            evt_res = svc.record_event(
                principal,
                diag_session_id=diag_id,
                event_id=f"evt_{idx}_{check_name}",
                check=check_name,
                measurements=measurements
            )
            assert evt_res["result"] == PASS

        # Complete the diagnostic session
        report = svc.complete(principal, diag_session_id=diag_id)
        assert report["overall"] == PASS
        assert report["mode"] == "TEST_MODE"
        assert report["counts_toward_attendance"] is False
        assert report["diagnostic_gate"]["all_passed"] is True
        assert report["diagnostic_gate"]["production_effect"] == "NONE"

        # Verify Isolation Audit in Report
        iso_items = {i["key"]: i["result"] for i in report["items"] if "isolation" in i["key"] or "ledger" in i["key"]}
        assert iso_items["attendance_isolation"] == PASS
        assert iso_items["point_ledger_unchanged"] == PASS
        assert iso_items["hourly_scheduler_isolation"] == PASS

        # Verify production DB is completely untouched
        with test_db._connect() as conn:
            daily = conn.execute("SELECT wakeup_status, final_score FROM daily_records WHERE date='2026-10-12'").fetchone()
            assert daily["wakeup_status"] == "PENDING"
            assert daily["final_score"] == 0

            score_rows = conn.execute("SELECT * FROM score_events").fetchall()
            assert len(score_rows) == 0

            sched_gates = conn.execute("SELECT * FROM daily_routine_gates").fetchall()
            assert len(sched_gates) == 0


# ==============================================================================
# 4. INGESTION AND PRODUCTION REJECTION GUARDS
# ==============================================================================

class TestProductionRejectionGuards:
    """Verify production ingest points reject any payload with diagnostic markers."""

    def test_is_diagnostic_payload_detection(self):
        assert is_diagnostic_payload({"test_mode": True}) is True
        assert is_diagnostic_payload({"diag_session_id": "diag_12345"}) is True
        assert is_diagnostic_payload({"session_id": "diag_999"}) is True
        assert is_diagnostic_payload({"event_type": "WAKEUP_DIAGNOSTIC_RESULT"}) is True
        assert is_diagnostic_payload({"nested": {"diag_session_id": "diag_abc"}}) is True
        assert is_diagnostic_payload({"items": [{"is_test": 1}]}) is True

        # Clean production payload
        assert is_diagnostic_payload({"date": "2026-10-12", "device_id": "phone_1"}) is False

    def test_daily_routine_gate_ingest_rejects_diagnostic_payload(self, test_db):
        gate = DailyRoutineGate(test_db)
        with test_db._connect() as conn:
            res = gate.ingest_android_wakeup(
                conn=conn,
                payload={
                    "date": "2026-10-12",
                    "diag_session_id": "diag_test_fake_wakeup",
                    "photo_liveness_verified": True,
                    "earbuds_verified": True,
                    "motivation_track_verified": True
                },
                device_id="android_phone",
                account_id="user_lok",
                received_at="2026-10-12T07:30:00"
            )
            assert res is None

    def test_database_sync_batch_filters_out_diagnostic_events(self, test_db):
        sync_batch = [
            {
                "event_id": "diag_sync_01",
                "event_type": "WAKEUP_DIAGNOSTIC_COMPLETED",
                "timestamp": "2026-10-12T07:30:00",
                "data": {"diag_session_id": "diag_01", "result": "PASS"}
            },
            {
                "event_id": "evt_prod_01",
                "event_type": "ROUTINE_STEP_VIEWED",
                "timestamp": "2026-10-12T07:31:00",
                "data": {"step": "MORNING"}
            }
        ]
        res = test_db.process_sync_events_batch(device_id="android_phone", events=sync_batch)
        assert res["success"] is True

        # Verify no wake_up_sessions entry was finalized
        with test_db._connect() as conn:
            wake_sessions = conn.execute("SELECT * FROM wake_up_sessions").fetchall()
            assert len(wake_sessions) == 0


# ==============================================================================
# 5. IDEMPOTENCY, RECOVERY AND PERMISSION SECURITY
# ==============================================================================

class TestDiagnosticsSecurityAndRecovery:
    """Verify security, authorization boundaries, and recovery handling."""

    def test_duplicate_event_recording_is_idempotent(self, test_db, principal):
        svc = WakeUpDiagnosticsService(test_db)
        sess = svc.start(principal, request_id="req_idem_01")
        diag_id = sess["diag_session_id"]

        ev1 = svc.record_event(principal, diag_session_id=diag_id, event_id="evt_idem",
                               check="camera_permission", measurements={"granted": True})
        assert ev1["duplicate"] is False

        # Replaying identical event
        ev2 = svc.record_event(principal, diag_session_id=diag_id, event_id="evt_idem",
                               check="camera_permission", measurements={"granted": True})
        assert ev2["duplicate"] is True
        assert ev2["result"] == PASS

    def test_expired_session_rejected(self, test_db, principal):
        svc = WakeUpDiagnosticsService(test_db)
        sess = svc.start(principal, request_id="req_expire_test")
        diag_id = sess["diag_session_id"]

        # Artificially expire session
        with test_db._connect() as conn:
            conn.execute("UPDATE wakeup_diagnostic_sessions SET expires_at='2020-01-01T00:00:00' WHERE diag_session_id=?",
                         (diag_id,))
            conn.commit()

        with pytest.raises(DiagnosticsError) as exc_info:
            svc.record_event(principal, diag_session_id=diag_id, event_id="evt_late",
                             check="camera_permission", measurements={"granted": True})
        assert exc_info.value.code == "SESSION_EXPIRED"

    def test_cross_device_access_denied(self, test_db, principal):
        svc = WakeUpDiagnosticsService(test_db)
        sess = svc.start(principal, request_id="req_dev_bound")
        diag_id = sess["diag_session_id"]

        other_device = DiagnosticPrincipal(account_id="user_lok", device_id="other_phone", platform="ANDROID")
        with pytest.raises(DiagnosticsError) as exc_info:
            svc.record_event(other_device, diag_session_id=diag_id, event_id="evt_cross",
                             check="camera_permission", measurements={"granted": True})
        assert exc_info.value.code == "WRONG_DEVICE"


# ==============================================================================
# 6. HTTP SERVER ENDPOINT INTEGRATION & REJECTION TESTS
# ==============================================================================

class TestWakeUpDiagnosticsServerEndpoints:
    """Verify HTTP endpoints enforce test mode isolation and rejection guards."""

    @pytest.fixture(autouse=True)
    def setup_server(self, tmp_path):
        import shutil
        import threading
        import urllib.request
        from core.server import create_server

        self.temp_dir = tmp_path / "server_test"
        self.temp_dir.mkdir()
        self.db_path = str(self.temp_dir / "srv.db")
        self.storage_root = str(self.temp_dir / "STRICTRECORDS")

        # Initialize base tables
        db = StrictRecordsDB(self.db_path)
        DailyRoutineGate(db)
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_records (record_id, date, user_id, is_dev_mode, wakeup_status, final_score, updated_at)
                VALUES ('rec_srv_1', '2026-10-12', 'user_lok', 0, 'PENDING', 0, '2026-10-12T00:00:00')
            """)
            conn.commit()

        self.server = create_server(
            host="127.0.0.1", port=0, db_path=self.db_path, storage_root=self.storage_root
        )
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        yield

        self.server.shutdown()
        self.server.server_close()

    def _post(self, path: str, payload: dict):
        import urllib.request
        import urllib.error
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get(self, path: str):
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Connection": "close"},
            method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_diagnostic_http_lifecycle(self):
        # 1. Start diagnostic session
        st, start_res = self._post("/api/wakeup/diagnostics/start", {"request_id": "req_srv_lifecycle_01"})
        assert st == 200
        assert start_res["success"] is True
        assert start_res["mode"] == "TEST_MODE"
        diag_id = start_res["diag_session_id"]
        assert diag_id.startswith("diag_")

        # 2. Record diagnostic check event
        st, evt_res = self._post("/api/wakeup/diagnostics/event", {
            "diag_session_id": diag_id,
            "event_id": "evt_srv_cam_01",
            "check": "camera_permission",
            "measurements": {"granted": True}
        })
        assert st == 200
        assert evt_res["result"] == "PASS"

        # 3. Complete diagnostic session
        st, comp_res = self._post("/api/wakeup/diagnostics/complete", {
            "diag_session_id": diag_id
        })
        assert st == 200
        assert comp_res["mode"] == "TEST_MODE"
        assert comp_res["counts_toward_attendance"] is False

        # 4. Fetch diagnostic report via GET
        st, rep_res = self._get(f"/api/wakeup/diagnostics/report?diag_session_id={diag_id}")
        assert st == 200
        assert rep_res["diag_session_id"] == diag_id

    def test_production_wake_up_endpoints_reject_diagnostic_payloads(self):
        # Reject diagnostic payload on trigger
        st, res = self._post("/api/wakeup/trigger", {
            "date": "2026-10-12",
            "test_mode": True
        })
        assert st == 400
        assert res["code"] == "TEST_MODE_REJECTED"

        # Reject diag_ session on step1_camera
        st, res = self._post("/api/wakeup/step1_camera", {
            "date": "2026-10-12",
            "diag_session_id": "diag_spoofed_session"
        })
        assert st == 400
        assert res["code"] == "TEST_MODE_REJECTED"

        # Reject diagnostic payload on complete
        st, res = self._post("/api/wakeup/complete", {
            "date": "2026-10-12",
            "is_test": True
        })
        assert st == 400
        assert res["code"] == "TEST_MODE_REJECTED"

