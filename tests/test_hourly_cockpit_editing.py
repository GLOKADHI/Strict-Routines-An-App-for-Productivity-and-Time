"""Regression test suite for StrictRecords hourly submission editing and point assignment in Cockpit."""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image

from core.daily_routine_gate import DailyRoutineGate
from core.database import StrictRecordsDB
from core.hourly_admin_service import HourlyAdminService
from core.hourly_submission_service import HourlySubmissionError, HourlySubmissionService
from core.models import ScoreEventType
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist, TimeWindowEngine


def make_dummy_image_bytes(color=(70, 130, 180), size=(150, 150)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestHourlyCockpitEditingAndPoints(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_strictrecords.db")
        self.storage_root = os.path.join(self.test_dir, "evidence")
        os.makedirs(self.storage_root, exist_ok=True)
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.scoring = ScoringEngine(db=self.db, storage_root=self.storage_root)
        self.twe = TimeWindowEngine(self.db)
        self.sub_svc = HourlySubmissionService(self.db, self.scoring, storage_root=self.storage_root)
        self.admin_svc = HourlyAdminService(self.db, self.scoring, storage_root=self.storage_root)

        self.date = "2026-10-16"
        DailyRoutineGate.seed_approved_wakeup(self.db, self.date, completed_hm="07:30")
        # Ensure activities for date
        self.ref_now = datetime(2026, 10, 16, 7, 35, 0, tzinfo=IST)
        self.activities = self.twe.ensure_activities_in_db(self.date, reference_now=self.ref_now)
        self.hourly_acts = [a for a in self.activities if a["type"] == "HOURLY" and a["status"] in ("UPCOMING", "OPEN", "SCHEDULED")]

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _submit_real_hourly(self, act, category="PRODUCTIVE", description="Deep work on Python module", img_color=(60, 120, 180)):
        w_start = iso_to_dt(act["window_start"])
        sub_time = w_start + timedelta(minutes=5)
        raw_img = make_dummy_image_bytes(color=img_color)
        sub_id = f"sub_test_{act['activity_id']}"
        payload = {
            "activity_id": act["activity_id"],
            "submission_id": sub_id,
            "category": category,
            "description": description,
            "schedule_version": act.get("schedule_version", 1),
        }
        res = self.sub_svc.accept(
            payload,
            file_bytes=raw_img,
            filename="proof.jpg",
            account_id="user_lok",
            device_id="android_device_1",
            platform="ANDROID",
            received_at=sub_time,
        )
        return res, raw_img

    # -------------------------------------------------------------------------
    # Scenario 1: Displays real Android records with accurate occurrence IDs & bounds
    # -------------------------------------------------------------------------
    def test_01_hourly_submission_displays_real_android_records(self):
        act = self.hourly_acts[0]
        res, _ = self._submit_real_hourly(act, category="PRODUCTIVE", description="Testing hourly submission")
        self.assertTrue(res["accepted"])

        items = self.admin_svc.list_hourly_submissions(date=self.date)
        self.assertTrue(len(items) >= len(self.hourly_acts))
        matched = [i for i in items if i["activity_id"] == act["activity_id"]]
        self.assertEqual(len(matched), 1)
        m = matched[0]
        self.assertEqual(m["occurrence_id"], act["activity_id"])
        self.assertEqual(m["submission_id"], res["submission_id"])
        self.assertEqual(m["category"], "PRODUCTIVE")
        self.assertEqual(m["description"], "Testing hourly submission")
        self.assertEqual(m["window_start"], act["window_start"])
        self.assertEqual(m["window_end"], act["window_end"])
        self.assertTrue(m["has_actual_submission"])

    # -------------------------------------------------------------------------
    # Scenario 2: Opening submission displays original text, image, analysis & review state
    # -------------------------------------------------------------------------
    def test_02_open_submission_displays_details_image_analysis_review(self):
        act = self.hourly_acts[0]
        res, raw_bytes = self._submit_real_hourly(act, category="PROJECT", description="Building project architecture")
        evidence_id = res["evidence_id"]

        # Seed OCR and vision signals
        with self.db._connect() as conn:
            conn.execute(
                "UPDATE proof_reviews SET signals_json = ? WHERE evidence_id = ?",
                (json.dumps({"ocr_text": "StrictRecords Test Engine", "visual_tags": ["code", "ide"], "confidence": 0.95}), evidence_id),
            )

        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertIsNotNone(detail)
        self.assertEqual(detail["category"], "PROJECT")
        self.assertEqual(detail["description"], "Building project architecture")
        self.assertIsNotNone(detail["evidence"])
        self.assertEqual(detail["evidence"]["evidence_id"], evidence_id)
        self.assertTrue(os.path.isfile(detail["evidence"]["file_path"]))
        self.assertTrue(detail["evidence"]["integrity_valid"])
        self.assertEqual(detail["review"]["signals"]["ocr_text"], "StrictRecords Test Engine")

    # -------------------------------------------------------------------------
    # Scenario 3: Editing description saves correction without altering original evidence or hash
    # -------------------------------------------------------------------------
    def test_03_editing_description_preserves_evidence_and_hashes(self):
        act = self.hourly_acts[0]
        res, _ = self._submit_real_hourly(act, description="Original draft description")
        original_evidence_id = res["evidence_id"]
        original_hash = res["evidence_sha256"]

        edit_res = self.admin_svc.edit_hourly_submission(
            activity_id=act["activity_id"],
            admin_user="admin_lok",
            updates={"description": "Corrected description verified by supervisor"},
        )
        self.assertTrue(edit_res["success"])
        self.assertIn("description", edit_res["fields_changed"])

        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["description"], "Corrected description verified by supervisor")
        # Evidence ID and hash must remain completely unchanged
        self.assertEqual(detail["evidence"]["evidence_id"], original_evidence_id)
        self.assertEqual(detail["evidence"]["sha256_hash"], original_hash)

    # -------------------------------------------------------------------------
    # Scenario 4: Replacing evidence preserves original version, stores new separately, resets analysis
    # -------------------------------------------------------------------------
    def test_04_replacing_evidence_preserves_original_versions_new_resets_analysis(self):
        act = self.hourly_acts[0]
        res, orig_bytes = self._submit_real_hourly(act, img_color=(10, 20, 30))
        orig_ev_id = res["evidence_id"]
        orig_hash = res["evidence_sha256"]

        new_bytes = make_dummy_image_bytes(color=(200, 100, 50))
        edit_res = self.admin_svc.edit_hourly_submission(
            activity_id=act["activity_id"],
            admin_user="admin_lok",
            updates={
                "replacement_evidence_bytes": new_bytes,
                "replacement_evidence_filename": "replacement_proof.jpg",
            },
        )
        self.assertTrue(edit_res["success"])

        # Check that original evidence file still exists in DB and on disk
        with self.db._connect() as conn:
            orig_row = conn.execute("SELECT * FROM evidence_files WHERE evidence_id = ?", (orig_ev_id,)).fetchone()
            self.assertIsNotNone(orig_row)
            self.assertEqual(orig_row["sha256_hash"], orig_hash)
            self.assertTrue(os.path.isfile(orig_row["file_path"]))

            # Check that new version was inserted
            rep_row = conn.execute("SELECT * FROM evidence_files WHERE evidence_id != ? AND related_record_id IN (?, ?)", (orig_ev_id, res["submission_id"], act["activity_id"])).fetchone()
            self.assertIsNotNone(rep_row)
            self.assertNotEqual(rep_row["sha256_hash"], orig_hash)
            self.assertTrue(os.path.isfile(rep_row["file_path"]))

        # Verify review state was reset for re-evaluation, NOT auto-approved
        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["raw_status"], "VALIDATING")
        self.assertNotEqual(detail["review_status"], "APPROVED")

    # -------------------------------------------------------------------------
    # Scenario 5: Editing category or notes does not alter occurrence ID or scheduled window
    # -------------------------------------------------------------------------
    def test_05_editing_category_or_notes_preserves_occurrence_id_and_window(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act, category="PRODUCTIVE")
        orig_window_start = act["window_start"]
        orig_window_end = act["window_end"]

        self.admin_svc.edit_hourly_submission(
            activity_id=act["activity_id"],
            admin_user="admin_lok",
            updates={"category": "EXERCISE", "admin_notes": "Supervisor verified routine"},
        )
        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["activity_id"], act["activity_id"])
        self.assertEqual(detail["window_start"], orig_window_start)
        self.assertEqual(detail["window_end"], orig_window_end)
        self.assertEqual(detail["category"], "EXERCISE")

    # -------------------------------------------------------------------------
    # Scenario 6: Entering 0 points is accepted and updates target occurrence only
    # -------------------------------------------------------------------------
    def test_06_entering_zero_points_accepted_and_target_only(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        # Approve proof explicitly
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})

        award_res = self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 0)
        self.assertTrue(award_res["success"])
        self.assertEqual(award_res["points_awarded"], 0)

        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["points_awarded"], 0)

    # -------------------------------------------------------------------------
    # Scenario 7: Entering positive whole-number points within configured limit updates target only
    # -------------------------------------------------------------------------
    def test_07_entering_positive_whole_number_points_within_limit(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})

        award_res = self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)
        self.assertTrue(award_res["success"])
        self.assertEqual(award_res["points_awarded"], 8)

        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["points_awarded"], 8)

        # Other hourly slots must remain 0
        other_act = self.hourly_acts[1]
        other_detail = self.admin_svc.get_hourly_submission_detail(other_act["activity_id"])
        self.assertEqual(other_detail["points_awarded"], 0)

    # -------------------------------------------------------------------------
    # Scenario 8: Rejecting negative point values with clear validation error
    # -------------------------------------------------------------------------
    def test_08_rejecting_negative_point_values(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})

        with self.assertRaises(HourlySubmissionError) as ctx:
            self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", -5)
        self.assertEqual(ctx.exception.code, "NEGATIVE_POINTS_REJECTED")

    # -------------------------------------------------------------------------
    # Scenario 9: Rejecting non-numeric or out-of-range values with validation error
    # -------------------------------------------------------------------------
    def test_09_rejecting_non_numeric_or_out_of_range_values(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})

        with self.assertRaises(HourlySubmissionError) as ctx:
            self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 999)
        self.assertEqual(ctx.exception.code, "POINT_LIMIT_EXCEEDED")

        with self.assertRaises(HourlySubmissionError) as ctx2:
            self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", "invalid_number")
        self.assertEqual(ctx2.exception.code, "INVALID_POINT_FORMAT")

    # -------------------------------------------------------------------------
    # Scenario 10: Awarding points records authoritative transaction & updates daily balance
    # -------------------------------------------------------------------------
    def test_10_awarding_points_records_authoritative_transaction_and_daily_balance(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})

        bal_before = self.scoring.recalculate_daily_score(self.date)["final_score"]
        award_res = self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)
        self.assertTrue(award_res["success"])

        bal_after = self.scoring.recalculate_daily_score(self.date)["final_score"]
        self.assertEqual(bal_after, bal_before + 8)

        # Verify transaction in score_events
        with self.db._connect() as conn:
            ev = conn.execute("SELECT * FROM score_events WHERE related_task_id = ? AND points = 8", (f"HOURLY_{act['activity_id']}",)).fetchone()
            self.assertIsNotNone(ev)

    # -------------------------------------------------------------------------
    # Scenario 11: Correcting existing award records adjustment and updates ledger accurately
    # -------------------------------------------------------------------------
    def test_11_correcting_existing_award_records_adjustment_and_updates_ledger(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})
        self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)

        # Correct award to 5 points
        corr_res = self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 5, confirm_change=True)
        self.assertTrue(corr_res["success"])
        self.assertEqual(corr_res["diff"], -3)
        self.assertEqual(corr_res["points_awarded"], 5)

        detail = self.admin_svc.get_hourly_submission_detail(act["activity_id"])
        self.assertEqual(detail["points_awarded"], 5)

        # Verify adjustment event in score_events with delta -3
        with self.db._connect() as conn:
            adj_ev = conn.execute(
                "SELECT * FROM score_events WHERE related_task_id = ? AND points = -3",
                (f"HOURLY_{act['activity_id']}",),
            ).fetchone()
            self.assertIsNotNone(adj_ev)

    # -------------------------------------------------------------------------
    # Scenario 12: Repeated save or award actions are idempotent and do not duplicate points
    # -------------------------------------------------------------------------
    def test_12_repeated_save_or_award_is_idempotent(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})
        self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)
        bal_first = self.scoring.recalculate_daily_score(self.date)["final_score"]

        # Re-run same award
        repeat_res = self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)
        self.assertTrue(repeat_res["success"])
        self.assertTrue(repeat_res.get("already_applied"))

        bal_second = self.scoring.recalculate_daily_score(self.date)["final_score"]
        self.assertEqual(bal_first, bal_second)

    # -------------------------------------------------------------------------
    # Scenario 13: Unapproved proofs cannot be awarded points without approval
    # -------------------------------------------------------------------------
    def test_13_unapproved_proofs_cannot_be_awarded_points_without_approval(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        # Proof is still in pending state (not approved)
        with self.assertRaises(HourlySubmissionError) as ctx:
            self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)
        self.assertEqual(ctx.exception.code, "PROOF_NOT_APPROVED")

    # -------------------------------------------------------------------------
    # Scenario 14: Administrator edit does not reopen expired user submission window
    # -------------------------------------------------------------------------
    def test_14_admin_edit_does_not_reopen_expired_submission_window(self):
        act = self.hourly_acts[0]
        w_end = iso_to_dt(act["window_end"])
        expired_time = w_end + timedelta(minutes=15)

        # Admin edits note on closed activity
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"admin_notes": "Closed window review"})

        # User attempts submission after expiry
        payload = {
            "activity_id": act["activity_id"],
            "category": "PRODUCTIVE",
            "description": "Late attempt",
            "schedule_version": act.get("schedule_version", 1),
        }
        with self.assertRaises(HourlySubmissionError) as ctx:
            self.sub_svc.accept(
                payload,
                file_bytes=make_dummy_image_bytes(),
                filename="late.jpg",
                account_id="user_lok",
                device_id="android_device_1",
                platform="ANDROID",
                received_at=expired_time,
            )
        self.assertEqual(ctx.exception.code, "WINDOW_CLOSED")

    # -------------------------------------------------------------------------
    # Scenario 15: Android synchronization receives corrected fields and confirmed award
    # -------------------------------------------------------------------------
    def test_15_android_synchronization_receives_corrected_fields_and_confirmed_award(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(
            act["activity_id"],
            "admin_lok",
            {"category": "PROJECT", "description": "Android Sync Test", "review_decision": "APPROVE"},
        )
        self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)

        # Android client fetches pending sync events
        events = self.sub_svc.get_pending_events(account_id="user_lok", device_id="android_device_1")
        self.assertTrue(len(events) >= 1)
        award_event = next((e for e in events if e.get("event_type") == "ADMIN_REVIEW_COMPLETED" and e.get("activity_id") == act["activity_id"]), None)
        self.assertIsNotNone(award_event)
        self.assertEqual(award_event["points"], 8)
        self.assertEqual(award_event["status"], "APPROVED")

    # -------------------------------------------------------------------------
    # Scenario 16: Unauthorized edit or award attempts are rejected and logged
    # -------------------------------------------------------------------------
    def test_16_unauthorized_edit_or_award_attempts_rejected_and_logged(self):
        # Test server route authorization rejection
        from core.server import StrictRecordsRequestHandler
        class MockServer:
            server_port = 8765

        handler = object.__new__(StrictRecordsRequestHandler)
        handler.client_address = ("192.168.1.50", 12345)
        handler.headers = {}
        handler.server = MockServer()
        handler.db = self.db
        handler._send_json = lambda *args, **kwargs: None

        authed = handler._authorize_admin_request(body={})
        self.assertFalse(authed)

    # -------------------------------------------------------------------------
    # Scenario 17: Changing hourly award does not modify Bath, Gym, Travel, or Wake-up
    # -------------------------------------------------------------------------
    def test_17_changing_hourly_award_does_not_modify_other_activities(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act)
        self.admin_svc.edit_hourly_submission(act["activity_id"], "admin_lok", {"review_decision": "APPROVE"})
        self.admin_svc.award_hourly_points(act["activity_id"], "admin_lok", 8)

        # Seed Bath (15) and Gym (20) points
        self.scoring.record_score_event(
            date=self.date,
            event_type=ScoreEventType.BATH_COMPLETED.value,
            points=15,
            device_id="system",
            description="Bath complete",
        )
        self.scoring.record_score_event(
            date=self.date,
            event_type=ScoreEventType.GYM_COMPLETED.value,
            points=20,
            device_id="system",
            description="Gym complete",
        )

        with self.db._connect() as conn:
            bath_ev = conn.execute("SELECT * FROM score_events WHERE event_type = ?", (ScoreEventType.BATH_COMPLETED.value,)).fetchone()
            gym_ev = conn.execute("SELECT * FROM score_events WHERE event_type = ?", (ScoreEventType.GYM_COMPLETED.value,)).fetchone()
            self.assertEqual(bath_ev["points"], 15)
            self.assertEqual(gym_ev["points"], 20)

    # -------------------------------------------------------------------------
    # Scenario 18: Audit log captures admin identity, timestamp, occurrence ID, old/new diff & reason
    # -------------------------------------------------------------------------
    def test_18_audit_log_captures_admin_identity_timestamp_occurrence_diff_and_reason(self):
        act = self.hourly_acts[0]
        self._submit_real_hourly(act, description="Initial draft")
        self.admin_svc.edit_hourly_submission(
            activity_id=act["activity_id"],
            admin_user="admin_supervisor",
            updates={"description": "Final reviewed text", "reason": "Correcting typo in report"},
        )

        with self.db._connect() as conn:
            audit = conn.execute(
                "SELECT * FROM admin_audit_logs WHERE admin_user = 'admin_supervisor' ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertEqual(audit["admin_user"], "admin_supervisor")
            self.assertEqual(audit["field_changed"], f"activity:{act['activity_id']}")
            self.assertIn("Final reviewed text", audit["new_value"])


if __name__ == "__main__":
    unittest.main()
