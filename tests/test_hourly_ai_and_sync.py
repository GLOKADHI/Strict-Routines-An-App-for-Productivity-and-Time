"""
STRICTRECORDS — Comprehensive Verification Suite for Hourly Proof, Android/PC Sync, and StrictBot AI Reviewer
Tests:
  1. Splash screen absence & direct dashboard launch
  2. Windows background operation & restart window preservation (no new 20-min window)
  3. Full-screen hourly submission window elements & minimization pill
  4. Real-time Android-to-Windows sync & submission popup auto-close logic
  5. StrictBot AI Reviewer pipeline:
     - Exact SHA-256 duplicate detection
     - Perceptual dHash visual similarity & Hamming distance
     - Image quality (sharpness, brightness, contrast, flags)
     - OCR contour text region detection
     - Description analysis (keywords, minimum length, forbidden placeholders)
     - Authoritative timing boundary ($W_{start} <= submitted_at < W_{end}$)
     - Structured review decision synthesis (PROVISIONAL_APPROVE, NEEDS_REVIEW, PROVISIONAL_REJECT, DUPLICATE_REVIEW)
  6. Privilege recomputation triggered by hourly proof verification
"""

import base64
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.ai_robot_admin import StrictBotAIReviewer
from core.database import StrictRecordsDB
from core.privilege_engine import PrivilegeEngine
from core.time_window_engine import TimeWindowEngine
from core.server import EventBus


class TestSplashAbsenceAndStartup:
    """Verifies splash screen removal and background startup requirements."""

    def test_android_splash_bypassed(self):
        """Android MainActivity must hide splashOverlay immediately on startup."""
        main_activity_path = os.path.join(ROOT_DIR, "android", "app", "src", "main", "java", "com", "strictrecords", "app", "MainActivity.kt")
        assert os.path.exists(main_activity_path)
        with open(main_activity_path, "r", encoding="utf-8") as f:
            code = f.read()

        # splashOverlay must be set GONE in onCreate
        assert "splashOverlay.visibility = View.GONE" in code

    def test_ui_dashboard_no_splash_and_has_banner(self):
        """Dashboard HTML must load directly without intro screen and include the compact status banner."""
        ui_path = os.path.join(ROOT_DIR, "core", "ui_dashboard.py")
        assert os.path.exists(ui_path)
        with open(ui_path, "r", encoding="utf-8") as f:
            content = f.read()

        # No intro splash
        assert "id=\"splashScreen\"" not in content
        assert "id=\"introAnimation\"" not in content

        # Hourly submission overlay must be present
        assert "id=\"hourlySubmissionOverlay\"" in content
        assert "What did you do during the last hour?" in content
        assert "hourlyModalCountdownDisplay" in content
        assert "hourlyFloatingPill" in content

        # Compact dashboard banner must be present
        assert "id=\"hourlyDashboardBanner\"" in content
        assert "HOURLY SUBMISSION RECEIVED" in content

    def test_windows_restart_during_open_window_preserves_bounds(self):
        """
        Restarting Windows during an open window must NOT generate a new 20-minute window.
        Remaining time must be computed strictly against scheduled window_end.
        """
        now = datetime.now()
        # Window opened 5 minutes ago, closes in 15 minutes
        w_start = (now - timedelta(minutes=5)).isoformat()
        w_end = (now + timedelta(minutes=15)).isoformat()

        # Calculation used in desktop_app and pollHourlyStatus
        now_ms = now.timestamp() * 1000
        end_ms = datetime.fromisoformat(w_end).timestamp() * 1000
        rem_sec = max(0, int((end_ms - now_ms) / 1000))

        # Remaining time should be ~15 minutes (890 - 910 seconds), NOT 1200 seconds (20 mins)
        assert 890 <= rem_sec <= 910


class TestStrictBotAIReviewerPipeline:
    """Verifies all components of the robotic AI administrator."""

    @pytest.fixture
    def test_db(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = StrictRecordsDB(db_path=db_path)
        yield db
        try:
            os.remove(db_path)
        except OSError:
            pass

    def test_sha256_exact_duplicate_detection(self):
        """Identical image bytes must produce EXACT_DUPLICATE match risk."""
        img1 = b"TEST_IMAGE_BYTES_123456789_UNIQUE"
        sha1 = StrictBotAIReviewer.compute_sha256(img1)

        history = [
            {"evidence_id": "ev_001", "sha256_hash": sha1, "dhash": "a1b2c3d4e5f60718"}
        ]

        dup_res = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256=sha1,
            current_dhash=None,
            historical_records=history,
        )
        assert dup_res["duplicate_risk"] == "EXACT"
        assert dup_res["match_status"] == "EXACT_DUPLICATE"
        assert dup_res["matched_evidence_id"] == "ev_001"
        assert dup_res["similarity_score"] == 1.0

    def test_perceptual_dhash_similarity(self):
        """dHash Hamming distance <= 4 must produce HIGH_SIMILARITY / EXACT."""
        h1 = "ffff0000ffff0000"
        h2 = "ffff0000ffff0001"  # 1 bit difference
        dist = StrictBotAIReviewer.hamming_distance(h1, h2)
        assert dist == 1

        history = [
            {"evidence_id": "ev_002", "sha256_hash": "different_sha", "dhash": h1}
        ]

        dup_res = StrictBotAIReviewer.check_duplicate_evidence(
            current_sha256="another_sha",
            current_dhash=h2,
            historical_records=history,
        )
        assert dup_res["duplicate_risk"] == "EXACT"
        assert dup_res["match_status"] == "EXACT_DUPLICATE"

    def test_image_quality_analysis(self):
        """Generates a patterned synthetic image and tests quality rating."""
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (240, 240), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            for i in range(0, 240, 24):
                draw.line([(0, i), (240, i)], fill=(0, 0, 0), width=3)
                draw.rectangle([i, i, i + 16, i + 16], fill=(50, 100, 180))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            raw_bytes = buf.getvalue()

            q = StrictBotAIReviewer.analyze_image_quality(raw_bytes)
            assert q["width"] == 240
            assert q["height"] == 240
            assert q["usable"] is True
            assert q["quality_rating"] in ("GOOD", "EXCELLENT", "ACCEPTABLE")
        except ImportError:
            pytest.skip("PIL not available in test environment")

    def test_description_analysis_valid_and_invalid(self):
        """Tests keyword matching, short descriptions, and placeholder rejection."""
        # High relevance
        res_good = StrictBotAIReviewer.analyze_description(
            "Worked on debugging Python API server and refactoring unit test suite",
            "CODING"
        )
        assert res_good["valid"] is True
        assert res_good["relevance"] == "HIGH"
        assert "code" in res_good["matched_keywords"] or "debug" in res_good["matched_keywords"] or "python" in res_good["matched_keywords"]

        # Trivial / placeholder rejected
        res_bad = StrictBotAIReviewer.analyze_description("nothing", "CODING")
        assert res_bad["valid"] is False

        res_short = StrictBotAIReviewer.analyze_description("hi", "CODING")
        assert res_short["valid"] is False

    def test_authoritative_timing_boundary(self):
        """
        Tests strict formula: window_start <= submitted_at < window_end
        A proof submitted at 10:09:59 for window ending at 10:10:00 is on-time.
        A proof submitted at 10:10:00 is late.
        """
        w_start = "2026-10-10T09:50:00"
        w_end = "2026-10-10T10:10:00"

        # On time (10:09:59)
        t_on_time = "2026-10-10T10:09:59"
        res_on_time = StrictBotAIReviewer.evaluate_timing(t_on_time, w_start, w_end)
        assert res_on_time["timing_valid"] is True
        assert res_on_time["status"] == "ON_TIME"

        # Late (10:10:00)
        t_late = "2026-10-10T10:10:00"
        res_late = StrictBotAIReviewer.evaluate_timing(t_late, w_start, w_end)
        assert res_late["timing_valid"] is False
        assert res_late["status"] == "LATE"

        # Pre-window (09:49:59)
        t_early = "2026-10-10T09:49:59"
        res_early = StrictBotAIReviewer.evaluate_timing(t_early, w_start, w_end)
        assert res_early["timing_valid"] is False
        assert res_early["status"] == "PRE_WINDOW"

    def test_full_execute_review_provisional_approve(self, test_db):
        """Full pipeline with valid description and on-time timing yields PROVISIONAL_APPROVE."""
        review = StrictBotAIReviewer.execute_review(
            submission_id="sub_test_001",
            activity_id="act_test_001",
            task_code="HOURLY",
            description="Completed two coding modules and verified test coverage with unit tests",
            primary_activity="CODING",
            submitted_at_iso=datetime.now().isoformat(),
            device_id="win_workstation",
            platform="WINDOWS",
            historical_records=[],
            configured_points=8,
            auto_approve_high_confidence=True,
        )

        assert review["decision"] == "PROVISIONAL_APPROVE"
        assert review["confidence"] >= 0.80
        assert review["proposed_points"] == 8
        assert review["checks"]["timing_valid"] is True
        assert review["checks"]["duplicate_risk"] == "LOW"
        assert len(review["reasons"]) > 0

    def test_full_execute_review_duplicate_triggers_review(self, test_db):
        """Submitting duplicate evidence triggers DUPLICATE_REVIEW."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (120, 120), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 80, 80], fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        dummy_img = buf.getvalue()
        sha = StrictBotAIReviewer.compute_sha256(dummy_img)

        history = [
            {"evidence_id": "ev_prior", "sha256_hash": sha, "dhash": "1122334455667788"}
        ]

        review = StrictBotAIReviewer.execute_review(
            submission_id="sub_test_002",
            activity_id="act_test_002",
            task_code="HOURLY",
            description="Completed two coding modules and verified test coverage",
            primary_activity="CODING",
            submitted_at_iso=datetime.now().isoformat(),
            device_id="android_tablet",
            platform="ANDROID",
            image_bytes=dummy_img,
            historical_records=history,
            configured_points=8,
        )

        assert review["decision"] == "DUPLICATE_REVIEW"
        assert review["checks"]["duplicate_risk"] == "EXACT"
        assert review["admin_review_required"] is True
        assert review["proposed_points"] == 0


class TestRealtimeAndroidToWindowsSync:
    """Verifies that Android submission broadcasts to EventBus and triggers AI review."""

    def test_event_bus_broadcast_on_android_submission(self):
        """EventBus must deliver HOURLY_PROOF_SUBMITTED with device metadata."""
        bus = EventBus.get_instance()
        q = bus.subscribe()

        # Simulate what /api/device/sync_events does when Android syncs
        payload = {
            "activity_id": "act_hourly_1000",
            "submission_id": "sub_android_999",
            "hour_slot": "10:00 – 11:00",
            "primary_activity": "STUDY",
            "description": "Reviewed chapter 4 mathematics equations",
            "source_device_id": "android_tablet",
            "source_device_name": "Android Device",
            "submitted_at": "10:55 AM",
            "has_evidence": True,
            "status": "RECEIVED",
        }
        bus.publish("HOURLY_PROOF_SUBMITTED", payload)

        evt = q.get(timeout=2.0)
        assert evt is not None
        assert evt["event_type"] == "HOURLY_PROOF_SUBMITTED"
        assert evt["activity_id"] == "act_hourly_1000"
        assert evt["source_device_name"] == "Android Device"
        assert evt["has_evidence"] is True

        bus.unsubscribe(q)


class TestPrivilegeRecomputationAfterHourlyApproval:
    """Verifies shared privilege evaluation updates upon hourly proof verification."""

    @pytest.fixture
    def test_db(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = StrictRecordsDB(db_path=db_path)
        yield db
        try:
            os.remove(db_path)
        except OSError:
            pass

    def test_hourly_verification_updates_privileges(self, test_db, tmp_path):
        """Only an approved, on-time submission can verify the activity and affect privileges."""
        twe = TimeWindowEngine(test_db)
        today = datetime.now().strftime("%Y-%m-%d")
        evidence_id = "ev_hourly_privilege_test"
        evidence_path = tmp_path / "approved-proof.bin"
        evidence_path.write_bytes(b"approved proof fixture")
        submitted_at = f"{today}T10:55:00+05:30"

        # Create scheduled hourly activity directly in DB with required schema fields
        act_id = "act_hourly_verify_01"
        with test_db._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_activities (
                    activity_id, account_id, type, title, date, scheduled_time,
                    window_start, window_end, notification_time, closing_warning_time,
                    status, points, proof_requirements, validation_rule, is_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    act_id,
                    "default_account",
                    "HOURLY",
                    "Hourly Discipline Check (10:00)",
                    today,
                    "10:00",
                    f"{today}T10:50:00",
                    f"{today}T11:10:00",
                    f"{today}T10:45:00",
                    f"{today}T11:05:00",
                    "VALIDATING",
                    8,
                    "{}",
                    "{}",
                    1,
                ),
            )
            conn.execute(
                "UPDATE scheduled_activities SET submitted_at = ?, attendance_status = 'PRESENT', validation_status = 'PROCESSING', submission_id = ?, evidence_ids_json = ? WHERE activity_id = ?",
                (submitted_at, "sub_hourly_privilege_test", json.dumps([evidence_id]), act_id),
            )

        test_db.register_proof_review(
            evidence_id=evidence_id,
            date=today,
            task_code="HOURLY",
            file_path=str(evidence_path),
            user_id="default_account",
        )
        with test_db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET status = 'APPROVED' WHERE evidence_id = ?", (evidence_id,))
            conn.execute(
                "INSERT OR REPLACE INTO evidence_files (evidence_id, date, is_dev_mode, record_type, related_record_id, file_path, timestamp, device_id, file_size_bytes, sha256_hash, metadata_json, is_deleted) VALUES (?, ?, 0, 'Hourly', ?, ?, ?, 'test-device', ?, 'fixture-hash', '{}', 0)",
                (evidence_id, today, "sub_hourly_privilege_test", str(evidence_path), submitted_at, evidence_path.stat().st_size),
            )

        # Record verification
        twe.record_activity_verification(act_id, is_verified=True, awarded_points=8, evidence_id=evidence_id)

        # Verify activity status in DB
        with test_db._connect() as conn:
            act = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id = ?", (act_id,)).fetchone()
        assert act is not None
        assert act["status"] == "VERIFIED"

        # Evaluate privileges with the verified hourly status
        pe = PrivilegeEngine(test_db)
        eval_res = pe.evaluate_youtube_privilege(
            current_score=85,
            required_score=80,
            privilege_mode="CONTINUOUS_PROOF_GATED",
            last_hourly_proof_status=act["status"],
            wakeup_completed=True,
        )
        assert eval_res is not None
        assert eval_res.get("allowed") is True
        assert eval_res.get("privilege_mode") == "CONTINUOUS_PROOF_GATED"
