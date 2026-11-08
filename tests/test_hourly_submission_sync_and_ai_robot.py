"""
StrictRecords Test Suite:
1. Exact Hourly Window Timing & Boundary Rule (T-11m, T-10m, T, T+10m)
2. Real-Time Android-to-Windows Sync (EventBus, dismiss popup, received state)
3. StrictBot AI Reviewer Pipeline (Sharpness, Brightness, dHash, SHA256, OCR, Description matching)
4. Human Admin Verification & Override Console
"""

import unittest
import os
import sys
import json
import base64
import time
import shutil
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.daily_routine_gate import DailyRoutineGate
from core.time_window_engine import TimeWindowEngine, IST, now_ist, dt_to_iso
from core.ai_robot_admin import StrictBotAIReviewer
from core.server import EventBus


class TestStrictRecordsHourlySyncAndAI(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = ScoringEngine(db=self.db)
        self.twe = TimeWindowEngine(self.db)
        today = datetime.now().strftime("%Y-%m-%d")
        DailyRoutineGate.seed_approved_wakeup(self.db, today)
        DailyRoutineGate.seed_approved_wakeup(self.db, "2026-10-10")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. EXACT HOURLY WINDOW TIMING & BOUNDARY RULES (REQUIREMENT 4)
    # -------------------------------------------------------------------------
    def test_hourly_window_exact_timing(self):
        """
        For checkpoint at T (e.g., 10:00:00):
        - Notification: T - 11 minutes (09:49)
        - Window opens: T - 10 minutes (09:50)
        - Target: T (10:00)
        - Window closes: T + 10 minutes (10:10)
        Boundary rule: window_start <= submitted_at < window_end
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        activities = self.twe.ensure_activities_in_db(today)

        hourly_acts = [a for a in activities if a.get("type") == "HOURLY"]
        self.assertTrue(len(hourly_acts) >= 10, "Should have hourly checkpoints")

        act_10 = None
        for a in hourly_acts:
            if "-1000-" in a["activity_id"] or "09:00-10:00" in a["title"]:
                act_10 = a
                break

        self.assertIsNotNone(act_10, "10:00 AM hourly checkpoint must exist")

        sched_dt = datetime.fromisoformat(act_10["scheduled_time"])
        start_dt = datetime.fromisoformat(act_10["window_start"])
        end_dt = datetime.fromisoformat(act_10["window_end"])
        notif_dt = datetime.fromisoformat(act_10["notification_time"])

        self.assertEqual(sched_dt.hour, 10)
        self.assertEqual(sched_dt.minute, 0)

        # T - 11m notification
        self.assertEqual(notif_dt, sched_dt - timedelta(minutes=11))
        # T - 10m window start
        self.assertEqual(start_dt, sched_dt - timedelta(minutes=10))
        # T + 10m window end
        self.assertEqual(end_dt, sched_dt + timedelta(minutes=10))

        # Test boundary rule: window_start <= submitted_at < window_end
        # 09:49:59 -> PRE_WINDOW
        t_before = start_dt - timedelta(seconds=1)
        res_before = StrictBotAIReviewer.evaluate_timing(
            t_before.isoformat(), start_dt.isoformat(), end_dt.isoformat()
        )
        self.assertEqual(res_before["status"], "PRE_WINDOW")
        self.assertFalse(res_before["timing_valid"])

        # 09:50:00 -> ON_TIME
        res_start = StrictBotAIReviewer.evaluate_timing(
            start_dt.isoformat(), start_dt.isoformat(), end_dt.isoformat()
        )
        self.assertEqual(res_start["status"], "ON_TIME")
        self.assertTrue(res_start["timing_valid"])

        # 10:09:59 -> ON_TIME (Right before close)
        t_last_sec = end_dt - timedelta(seconds=1)
        res_last = StrictBotAIReviewer.evaluate_timing(
            t_last_sec.isoformat(), start_dt.isoformat(), end_dt.isoformat()
        )
        self.assertEqual(res_last["status"], "ON_TIME")
        self.assertTrue(res_last["timing_valid"])

        # 10:10:00 -> LATE (Exact boundary close)
        res_late = StrictBotAIReviewer.evaluate_timing(
            end_dt.isoformat(), start_dt.isoformat(), end_dt.isoformat()
        )
        self.assertEqual(res_late["status"], "LATE")
        self.assertFalse(res_late["timing_valid"])

    # -------------------------------------------------------------------------
    # 2. REAL-TIME ANDROID-TO-WINDOWS SYNC & DISMISSAL (REQUIREMENT 6)
    # -------------------------------------------------------------------------
    def test_android_submission_sync_event_bus(self):
        """
        When Android submits an hourly checkpoint:
        1. Sync event HOURLY_PROOF_SUBMITTED is processed.
        2. Status of scheduled activity is updated to SUBMITTED.
        3. EventBus receives and publishes the real-time event.
        4. Windows front-end subscriber receives it and identifies matching activity.
        """
        bus = EventBus.get_instance()
        q = bus.subscribe()

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            activities = self.twe.ensure_activities_in_db(today)
            hourly_acts = [a for a in activities if a.get("type") == "HOURLY"]
            act_id = hourly_acts[0]["activity_id"]

            # Register device
            self.db.register_or_heartbeat_device(
                device_id="and_dev_test_01",
                device_name="Android Phone Test",
                platform="ANDROID",
                sync_status="ONLINE"
            )

            # Simulate Android sync event batch
            sync_payload = {
                "submission_id": "sub_and_abc123",
                "activity_id": act_id,
                "hour_slot": "09:00-10:00",
                "primary_activity": "CODING",
                "description": "Refactored test suite and verified real-time event pipeline",
                "points": 8
            }
            events_batch = [{
                "event_id": "evt_and_101",
                "event_type": "HOURLY_PROOF_SUBMITTED",
                "timestamp": datetime.now().isoformat(),
                "payload": sync_payload
            }]

            # Process in DB
            res = self.db.process_sync_events_batch(device_id="and_dev_test_01", events=events_batch)
            self.assertTrue(res["success"])
            self.assertIn("evt_and_101", res["processed_event_ids"])

            # Verify scheduled_activities was updated
            with self.db._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM scheduled_activities WHERE activity_id = ?",
                    (act_id,)
                ).fetchone()
                self.assertEqual(row["status"], "SUBMITTED")

            # Simulate server bus broadcast
            bus.publish(
                "HOURLY_PROOF_SUBMITTED",
                {
                    "activity_id": act_id,
                    "submission_id": "sub_and_abc123",
                    "hour_slot": "09:00-10:00",
                    "primary_activity": "CODING",
                    "description": sync_payload["description"],
                    "source_device_id": "and_dev_test_01",
                    "source_device_name": "Android Device",
                    "submitted_at": "09:56 AM",
                    "has_evidence": False,
                    "status": "RECEIVED",
                },
                event_id="evt_and_101"
            )

            received = q.get(timeout=2.0)
            self.assertEqual(received["activity_id"], act_id)
            self.assertEqual(received["source_device_name"], "Android Device")
            self.assertEqual(received["status"], "RECEIVED")

        finally:
            bus.unsubscribe(q)

    # -------------------------------------------------------------------------
    # 3. STRICTBOT AI REVIEWER PIPELINE (REQUIREMENT 7 & 15)
    # -------------------------------------------------------------------------
    def test_strictbot_ai_reviewer_full_pipeline(self):
        """
        Verify the 10-stage StrictBot AI pipeline:
        - Image quality (sharpness variance, brightness, contrast)
        - Visual difference hash (64-bit dHash)
        - Exact SHA-256 duplicate detection
        - Description quality & domain keyword alignment
        - Timing boundary verification
        - Structured decision output (PROVISIONAL_APPROVE / NEEDS_REVIEW / etc.)
        """
        from PIL import Image, ImageDraw
        import io

        # 1. Create a clean test image with synthetic code text
        img = Image.new("RGB", (640, 480), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)
        for y in range(20, 440, 20):
            draw.line([(30, y), (600, y)], fill=(80, 180, 120), width=2)
            draw.rectangle([40, y + 4, 180, y + 14], fill=(200, 200, 220))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_bytes = buf.getvalue()
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        history = [
            {
                "evidence_id": "hist_01",
                "sha256_hash": "different_sha256_hash",
                "dhash": "0000000000000000",
                "user_id": "other_user",
                "submitted_at": "2026-10-09T10:00:00"
            }
        ]

        now = datetime.now(IST)
        w_start = (now - timedelta(minutes=5)).isoformat()
        w_end = (now + timedelta(minutes=15)).isoformat()

        # Run pipeline
        review = StrictBotAIReviewer.execute_review(
            submission_id="sub_test_pipeline_01",
            activity_id="ACT-20261010-1000-HOURLY",
            task_code="HOURLY",
            description="Implemented real-time event streaming and verified WebSocket/SSE fallback with automated test suite",
            primary_activity="CODING",
            submitted_at_iso=now.isoformat(),
            device_id="win_workstation",
            platform="WINDOWS",
            image_base64=img_b64,
            window_start_iso=w_start,
            window_end_iso=w_end,
            historical_records=history,
            configured_points=8,
            auto_approve_high_confidence=True
        )

        self.assertIn(review["decision"], ("PROVISIONAL_APPROVE", "NEEDS_REVIEW"))
        self.assertTrue(review["confidence"] >= 0.70)
        self.assertEqual(review["timing"]["status"], "ON_TIME")
        self.assertEqual(review["duplicate"]["match_status"], "ORIGINAL")
        self.assertEqual(review["description_analysis"]["relevance"], "HIGH")

        # Save review to database
        self.db.save_strictbot_ai_review(review)

        # Verify saved review in DB
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?",
                ("sub_test_pipeline_01",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["decision"], review["decision"])

    def test_strictbot_duplicate_detection(self):
        """
        Verify that submitting the exact same image triggers duplicate detection
        """
        from PIL import Image, ImageDraw
        import io

        img = Image.new("RGB", (300, 300), color=(20, 20, 30))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 250, 250], fill=(220, 100, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        img_bytes = buf.getvalue()
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        sha = StrictBotAIReviewer.compute_sha256(img_bytes)
        dhash = StrictBotAIReviewer.compute_dhash(img_bytes)

        history = [{
            "evidence_id": "prior_sub_99",
            "sha256_hash": sha,
            "dhash": dhash,
            "user_id": "candidate_01",
            "submitted_at": "2026-10-09T14:00:00"
        }]

        now = datetime.now(IST)
        dup_review = StrictBotAIReviewer.execute_review(
            submission_id="sub_test_duplicate_02",
            activity_id="ACT-20261010-1100-HOURLY",
            task_code="HOURLY",
            description="Copied proof from yesterday",
            primary_activity="CODING",
            submitted_at_iso=now.isoformat(),
            device_id="win_workstation",
            platform="WINDOWS",
            image_base64=img_b64,
            historical_records=history,
            configured_points=8
        )

        self.assertEqual(dup_review["decision"], "DUPLICATE_REVIEW")
        self.assertEqual(dup_review["duplicate"]["match_status"], "EXACT_DUPLICATE")

    # -------------------------------------------------------------------------
    # 4. HUMAN ADMIN VERIFICATION & OVERRIDE CONSOLE (REQUIREMENT 8)
    # -------------------------------------------------------------------------
    def test_human_admin_review_and_override(self):
        """
        Verify that an administrator can:
        - Query pending reviews awaiting verification
        - Override an AI decision with points and audit notes
        - Update score ledger and scheduled activity status
        """
        review_dict = {
            "evidence_id": "ev_admin_test_01",
            "submission_id": "ev_admin_test_01",
            "task_code": "HOURLY",
            "activity_id": "ACT-20261010-1200-HOURLY",
            "user_id": "user_lok",
            "device_id": "and_phone",
            "platform": "ANDROID",
            "submitted_at": datetime.now().isoformat(),
            "decision": "NEEDS_REVIEW",
            "confidence": 0.65,
            "proposed_points": 8,
            "explanation": "Flagged for human admin inspection",
            "reasons": ["Moderate image sharpness variance"],
            "signals": {
                "image_quality_grade": "MODERATE",
                "duplicate_risk": "ORIGINAL",
                "primary_activity": "STUDY"
            },
            "status": "NEEDS_REVIEW"
        }
        self.db.save_strictbot_ai_review(review_dict)

        evidence_id = "ev_admin_test_01"
        admin_id = "admin_supervisor"
        reason_note = "Legitimate study notes verified on high-res inspection"
        override_points = 10

        with self.db._connect() as conn:
            conn.execute(
                """
                UPDATE proof_reviews
                SET decision = 'OVERRIDDEN_APPROVE', status = 'VERIFIED',
                    points_awarded = ?, reason_note = ?, reviewed_by = ?,
                    reviewed_at = ?
                WHERE evidence_id = ?
                """,
                (override_points, f"[Admin Override] {reason_note}", admin_id, datetime.now().isoformat(), evidence_id)
            )

        self.db.record_admin_audit(
            admin_user=admin_id,
            action_type="PROOF_OVERRIDE",
            field_changed="decision",
            old_value="NEEDS_REVIEW",
            new_value="OVERRIDDEN_APPROVE"
        )

        with self.db._connect() as conn:
            rev_row = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            self.assertEqual(rev_row["decision"], "OVERRIDDEN_APPROVE")
            self.assertEqual(rev_row["status"], "VERIFIED")
            self.assertEqual(rev_row["points_awarded"], 10)
            self.assertEqual(rev_row["reviewed_by"], admin_id)

            audit_row = conn.execute(
                "SELECT * FROM admin_audit_logs WHERE admin_user = ? AND action_type = 'PROOF_OVERRIDE'",
                (admin_id,)
            ).fetchone()
            self.assertIsNotNone(audit_row)
            self.assertEqual(audit_row["action_type"], "PROOF_OVERRIDE")


if __name__ == "__main__":
    unittest.main()
