"""StrictBot service and real HTTP contracts against isolated SQLite databases.

Hardware records below are explicit test fixtures. These tests exercise real saved
evidence, application services, authorization and transport, not phone hardware.
"""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image

from core.database import StrictRecordsDB
from core.event_bus import EventBus
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler
from core.strictbot_assistant import AssistantError, AssistantPrincipal, StrictBotAssistant
from core.time_window_engine import IST, TimeWindowEngine
from tests.test_daily_routine_gate import seed_approved_wakeup


class AssistantFixture(unittest.TestCase):
    date = "2026-10-20"
    account = "user_lok"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now = datetime(2026, 10, 20, 10, 59, tzinfo=IST)
        self.time_patch = patch("core.time_window_engine.now_ist", side_effect=lambda: self.now)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.arrival_patch=patch('core.server.now_ist',side_effect=lambda:self.now)
        self.arrival_patch.start();self.addCleanup(self.arrival_patch.stop)
        self.db = StrictRecordsDB(os.path.join(self.folder.name, "assistant.db"))
        self.scoring = ScoringEngine(self.db, storage_root=os.path.join(self.folder.name, "proofs"))
        self.tw = TimeWindowEngine(self.db)
        self.now = datetime(2026, 10, 20, 10, 59, tzinfo=IST)
        self.env_patch = patch.dict(os.environ, {"OPENAI_API_KEY": "", "STRICTRECORDS_AI_API_KEY": ""})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.events = EventBus()
        self.bus_patch = patch("core.event_bus.EventBus.get_instance", return_value=self.events)
        self.bus_patch.start()
        self.addCleanup(self.bus_patch.stop)
        self.db.ensure_daily_record(self.date)
        self.assertTrue(self.db.change_admin_pin("1234", "5678")["success"])
        credentials = self.db.authenticate_admin("admin", "5678", device_id="fixture-windows")
        self.admin = AssistantPrincipal(self.account, credentials["admin_id"], "fixture-windows", credentials["role"], credentials["token"])
        self.user = AssistantPrincipal(self.account, self.account, "fixture-a55")
        self.other = AssistantPrincipal("other-user", "other-user", "other-phone")
        self.service = StrictBotAssistant(self.db, self.scoring, clock=lambda: self.now)
        self.confirm_schedule(self.date)

    def confirm_schedule(self, date):
        result = self.tw.confirm_next_day_schedule({"schedule_date": date, "timezone": "Asia/Kolkata",
            "wake_up_time": "07:00", "bath_time": "07:30", "gym_time": "18:30", "gym_enabled": True},
            account_id=self.account, actor=self.account, device_id="fixture-windows")
        self.assertTrue(result["success"], result)
        return result

    def activate(self, date=None):
        date = date or self.date
        seed_approved_wakeup(self.db, self.scoring, date, "07:30")
        self.tw.ensure_activities_in_db(date, account_id=self.account, reference_now=self.now)

    def action(self, name, inputs=None, request_id=None, principal=None, confirmation=True):
        return self.service.action(principal or self.user, name, inputs or {},
            request_id or "request-" + name, confirmation=confirmation)

    def propose(self, request_id="travel-proposal", **inputs):
        self.activate()
        values = {"date": self.date, "time": "17:00", "reason": "Travel", "duration_minutes": 180}
        values.update(inputs)
        result = self.action("propose_schedule_change", values, request_id=request_id)
        self.assertTrue(result["success"], result)
        return result["proposals"][0]

    def confirm(self, proposal, request_id="travel-confirm"):
        return self.action("confirm_schedule_change", {"proposal_id": proposal["proposal_id"]}, request_id=request_id)

    def approve(self, proposal, request_id="travel-approve", **extra):
        return self.action("approve_schedule_change", dict(proposal_id=proposal["proposal_id"], **extra),
            request_id=request_id, principal=self.admin)

    def count(self, table, where="", params=()):
        # Table names are fixed test constants, never supplied to assistant actions.
        with self.db._connect() as conn:
            return conn.execute("SELECT count(*) FROM " + table + (" WHERE " + where if where else ""), params).fetchone()[0]


class StrictBotContextTests(AssistantFixture):
    def test_next_task_preserves_independent_hourly_window_when_wakeup_is_pending(self):
        result = self.service.context(self.user, "next")
        self.assertEqual(result["mode"], "USER_INSTRUCTOR")
        self.assertFalse(result["context"]["wakeup"]["gate"]["active"])
        self.assertEqual(result["context"]["next_action"]["type"], "HOURLY")
        self.assertEqual(result["actions"][0]["target"], "TASKS")
        self.assertGreater(self.count("scheduled_activities", "type='HOURLY' AND status='OPEN'"), 0)
        self.assertEqual(self.count('score_events'),0)

    def test_verified_gate_recommends_saved_hourly_task_deadline_and_evidence(self):
        self.activate()
        result = self.service.context(self.user, "next")
        next_action = result["context"]["next_action"]
        self.assertTrue(result["context"]["wakeup"]["gate"]["active"])
        self.assertEqual(next_action["type"], "HOURLY")
        self.assertIn("11:10", next_action["window_end"])
        self.assertIn(next_action["window_end"], result["reply"])
        self.assertIn(next_action["proof_requirements"], result["reply"])
        self.assertEqual(result["actions"][0]["target"], "TASKS")

    def test_current_schedule_returns_confirmed_version_without_private_credentials(self):
        result = self.action("get_current_schedule")
        schedule = result["context"]["schedule"]
        self.assertEqual(schedule["schedule_date"], self.date)
        self.assertEqual(schedule["schedule_version"], 1)
        self.assertEqual(schedule["gym_time"], "18:30")
        encoded = json.dumps(result)
        self.assertNotIn(self.admin.admin_token, encoded)
        self.assertNotIn("pin_hash", encoded)
        self.assertNotIn("password_hash", encoded)

    def test_summary_distinguishes_user_report_from_verified_records_and_is_read_only(self):
        self.db.record_activity_journal_event("REPORTED_STUDY", "USER_REPORTED", self.date,
            description="I read a chapter", details={"private_file_path": "C:/private/chapter.pdf"}, event_id="study-report")
        before_scores = self.count("score_events")
        result = self.service.context(self.user, "summary")
        self.assertTrue(result["success"])
        self.assertEqual(result["context"]["reported_activity"][0]["provenance"], "USER_REPORTED")
        self.assertIn("verified", result["reply"])
        self.assertIn("reported", result["reply"].lower())
        self.assertNotIn("C:/private/chapter.pdf", json.dumps(result))
        self.service.context(self.user, "summary")
        self.assertEqual(self.count("score_events"), before_scores)
        self.assertEqual(self.count("activity_journal_events", "event_id='study-report'"), 1)

    def test_actual_evidence_failure_is_explained_without_claiming_an_unreceived_analysis(self):
        self.db.register_proof_review("failed-proof", self.date, "HOURLY",
            signals={"ocr": {"status": "FAILED", "extracted_text": ""},
                "duplicate": {"status": "EXACT_DUPLICATE", "similarity_score": 1.0},
                "quality": "LOW_LIGHT", "reasons": ["Document text could not be read"]})
        with self.db._connect() as conn:
            conn.execute("UPDATE proof_reviews SET status='REJECTED',decision='REJECT',reason_note='Unreadable' WHERE evidence_id='failed-proof'")
        result = self.service.context(self.user, "evidence", {"evidence_id": "failed-proof"})
        review = result["context"]["evidence"][0]
        self.assertFalse(review["file_saved"])
        self.assertEqual(review["ocr_status"], "FAILED")
        self.assertEqual(review["duplicate_status"], "EXACT_DUPLICATE")
        self.assertEqual(review["status"], "REJECTED")
        self.assertIn("Document text could not be read", result["reply"])
        missing = self.service.context(self.user, "evidence", {"evidence_id": "never-uploaded"})
        self.assertEqual(missing["context"]["evidence"], [])
        self.assertIn("cannot analyze", missing["reply"])

    def test_permission_observations_are_reported_not_granted_and_become_stale(self):
        unknown = self.service.context(self.user, "permissions")
        self.assertEqual(unknown["context"]["permissions"]["state"], "UNKNOWN")
        snapshot = {"CAMERA": {"status": "NOT_GRANTED", "required": True},
            "VOICE_INPUT": {"supported": True, "microphone_granted": False, "required": False}}
        self.service.save_capabilities(self.user, snapshot)
        current = self.service.context(self.user, "permissions")
        self.assertEqual(current["context"]["permissions"]["state"], "CLIENT_REPORTED")
        self.assertFalse(current["context"]["permissions"]["stale"])
        self.assertEqual(current["context"]["permissions"]["capabilities"], snapshot)
        self.assertEqual(current["actions"][0]["target"], "PERMISSIONS")
        self.now += timedelta(minutes=6)
        self.assertTrue(self.service.context(self.user, "permissions")["context"]["permissions"]["stale"])

    def test_privilege_request_does_not_award_points_or_unlock_unknown_host_enforcement(self):
        before = self.db.get_settings().to_dict()
        result = self.action("request_privilege_change", {"reason": "Educational video", "privilege": "YOUTUBE"})
        self.assertEqual(result["state"], "PENDING")
        self.assertEqual(self.db.get_settings().to_dict(), before)
        self.assertEqual(self.count("score_events"), 0)
        current = self.service.context(self.user, "privileges")
        self.assertEqual(current["context"]["privileges"]["host_enforcement"]["state"], "UNKNOWN")

    def test_account_cannot_read_other_private_evidence_or_reported_activity(self):
        self.db.register_proof_review("private-proof", self.date, "HOURLY", user_id="other-user",
            signals={"ocr": {"status": "SUCCESS", "extracted_text": "SECRET OTHER ACCOUNT"}})
        self.db.record_activity_journal_event("PRIVATE", "USER_REPORTED", self.date, account_id="other-user", description="SECRET OTHER JOURNAL")
        self.assertEqual(self.service.context(self.user, "evidence")["context"]["evidence"], [])
        summary = self.service.context(self.user, "summary")
        self.assertNotIn("SECRET OTHER", json.dumps(summary))


class StrictBotScheduleTests(AssistantFixture):
    def test_six_hour_deadline_boundary_1059_1100_1101(self):
        for minute, expected in ((59, True), (0, True), (1, False)):
            self.now = datetime(2026, 10, 20, 10 if minute == 59 else 11, minute, tzinfo=IST)
            proposal = self.propose(request_id="boundary-" + str(minute))
            self.assertEqual(proposal["notice_eligible"], expected)
            self.assertEqual(proposal["deadline"], "2026-10-20T11:00:00+05:30")
            self.assertEqual(proposal["status"], "PROPOSED")
            self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)

    def test_proposal_uses_actual_conflicting_gym_and_hourly_windows(self):
        proposal = self.propose()
        self.assertTrue(proposal["affected_windows"])
        self.assertTrue(any(item["type"] == "GYM" for item in proposal["affected_tasks"]))
        self.assertEqual(proposal["before"][0]["gym_time"], "18:30")
        self.assertIn("unchanged", proposal["after"]["privilege_thresholds"])
        self.assertEqual(self.count("schedule_change_proposals"), 1)

    def test_missing_date_or_time_requests_essential_details_without_mutation(self):
        # Separate authenticated actors provide independent conversations here.
        for index, text in enumerate(("I am travelling at 5 PM", "I am travelling today", "I am travelling today at 5")):
            principal = AssistantPrincipal(self.account, "case-" + str(index), "fixture-a55")
            result = self.service.chat(principal, text, "missing-" + str(len(text)))
            self.assertEqual(result["state"], "PENDING")
            self.assertEqual(result["code"], "DETAILS_REQUIRED")
        self.assertEqual(self.count("schedule_change_proposals"), 0)

    def test_user_confirmation_does_not_apply_and_administrator_approval_does(self):
        proposal = self.propose()
        confirmed = self.confirm(proposal)
        self.assertEqual(confirmed["state"], "PENDING")
        self.assertEqual(confirmed["proposals"][0]["status"], "AWAITING_ADMIN")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)
        applied = self.approve(proposal)
        self.assertTrue(applied["success"], applied)
        self.assertEqual(applied["state"], "COMPLETED")
        self.assertEqual(applied["proposals"][0]["status"], "APPLIED")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 2)
        self.assertTrue(self.events.get_events_since())

    def test_confirmation_is_required_before_any_consequential_action(self):
        proposal = self.propose()
        with self.assertRaises(AssistantError) as caught:
            self.action("confirm_schedule_change", {"proposal_id": proposal["proposal_id"]}, confirmation=False)
        self.assertEqual(caught.exception.code, "CONFIRMATION_REQUIRED")
        before_confirm = self.approve(proposal)
        self.assertFalse(before_confirm["success"])
        self.assertEqual(before_confirm["code"], "USER_CONFIRMATION_REQUIRED")

    def test_late_request_needs_explicit_admin_exception_and_reason(self):
        self.now = datetime(2026, 10, 20, 11, 1, tzinfo=IST)
        proposal = self.propose()
        self.assertFalse(proposal["notice_eligible"])
        self.assertTrue(self.confirm(proposal)["success"])
        refused = self.approve(proposal, request_id="late-without-reason")
        self.assertFalse(refused["success"])
        self.assertEqual(refused["code"], "LATE_EXCEPTION_REQUIRED")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)
        accepted = self.approve(proposal, request_id="late-with-reason", exception_reason="Documented transport emergency")
        self.assertTrue(accepted["success"], accepted)
        audits = self.db.get_schedule_reschedule_audits(self.date)
        self.assertTrue(any("Documented transport emergency" in row["reason"] for row in audits))

    def test_reject_late_policy_cannot_be_overridden_by_a_chat_sentence(self):
        self.action("set_schedule_change_policy", {"late_requests": "REJECT", "reason": "Require advance notice"}, principal=self.admin)
        self.now = datetime(2026, 10, 20, 11, 1, tzinfo=IST)
        proposal = self.propose()
        result = self.confirm(proposal)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "NOTICE_TOO_SHORT")
        chat = self.service.chat(self.user, "yes", "chat-yes")
        self.assertEqual(chat["state"], "PENDING")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)

    def test_administrator_rejection_preserves_current_routine(self):
        proposal = self.propose()
        self.confirm(proposal)
        result = self.action("reject_schedule_change", {"proposal_id": proposal["proposal_id"], "reason": "Insufficient justification"}, principal=self.admin)
        self.assertEqual(result["proposals"][0]["status"], "REJECTED")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)
        self.assertEqual(self.count("scheduled_activities", "status='EXCUSED'"), 0)

    def test_user_and_fabricated_admin_flag_cannot_approve_or_change_policy(self):
        proposal = self.propose()
        self.confirm(proposal)
        forged = AssistantPrincipal(self.account, self.account, "fixture-a55", "ADMIN", "forged-token")
        for principal in (self.user, forged):
            for name, values in (("approve_schedule_change", {"proposal_id": proposal["proposal_id"]}),
                ("set_schedule_change_policy", {"late_requests": "REJECT", "reason": "Forged override"})):
                with self.assertRaises(AssistantError) as caught:
                    self.action(name, values, request_id="deny-" + name, principal=principal)
                self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)

    def test_event_spanning_midnight_requires_both_confirmed_dates_and_correct_deadline(self):
        next_date = "2026-10-21"
        failed = self.action("propose_schedule_change", {"date": self.date, "time": "23:00", "duration_minutes": 600, "reason": "Overnight travel"}, request_id="no-next-date")
        self.assertFalse(failed["success"])
        self.assertEqual(failed["code"], "SCHEDULE_REQUIRED")
        self.confirm_schedule(next_date)
        proposal = self.propose(request_id="midnight", time="23:00", duration_minutes=600)
        self.assertEqual(proposal["event_end"], "2026-10-21T09:00:00+05:30")
        self.assertEqual(proposal["deadline"], "2026-10-20T17:00:00+05:30")
        self.assertEqual({row["schedule_date"] for row in proposal["before"]}, {self.date, next_date})
        self.confirm(proposal)
        applied = self.approve(proposal)
        self.assertTrue(applied["success"], applied)
        self.assertEqual(self.db.get_confirmed_routine_schedule(next_date)["schedule_version"], 2)

    def test_request_after_event_started_and_invalid_duration_are_rejected(self):
        for fields, code in (({"date": self.date, "time": "10:00", "reason": "Travel"}, "EVENT_NOT_FUTURE"),
            ({"date": self.date, "time": "17:00", "reason": "Travel", "duration_minutes": 0}, "INVALID_DURATION")):
            result = self.action("propose_schedule_change", fields, request_id=code)
            self.assertFalse(result["success"])
            self.assertEqual(result["code"], code)
        self.assertEqual(self.count("schedule_change_proposals"), 0)

    def test_revision_preserves_submissions_ledger_evidence_policy_and_syncs_registered_clients(self):
        proposal = self.propose()
        # A genuine past/submitted record is protected even if it overlaps the later event.
        with self.db._connect() as conn:
            row = conn.execute("SELECT activity_id FROM scheduled_activities WHERE type='GYM'").fetchone()
            conn.execute("UPDATE scheduled_activities SET status='SUBMITTED',submitted_at=?,points=0 WHERE activity_id=?", ("2026-10-20T10:30:00+05:30", row[0]))
            protected = tuple(conn.execute("SELECT status,submitted_at,points,proof_requirements FROM scheduled_activities WHERE activity_id=?", (row[0],)).fetchone())
            settings = self.db.get_settings().to_dict()
            ledger = [tuple(r) for r in conn.execute("SELECT * FROM score_events")]
        for device in ("sync-android", "sync-windows"):
            self.db.register_or_heartbeat_device(device, device, "ANDROID" if "android" in device else "WINDOWS")
        self.confirm(proposal)
        result = self.approve(proposal)
        self.assertTrue(result["success"], result)
        with self.db._connect() as conn:
            self.assertEqual(tuple(conn.execute("SELECT status,submitted_at,points,proof_requirements FROM scheduled_activities WHERE activity_id=?", (row[0],)).fetchone()), protected)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM score_events")], ledger)
            rows = [dict(r) for r in conn.execute("SELECT * FROM sync_queue WHERE sync_id LIKE 'ROUTINE-CHANGE-%'")]
        self.assertEqual(self.db.get_settings().to_dict(), settings)
        self.assertEqual({row["device_id"] for row in rows}, {"sync-android", "sync-windows"})
        self.assertTrue(all(json.loads(row["payload_json"])["schedule"]["schedule_version"] == 2 for row in rows))
        self.assertTrue(any(item["status"] == "EXCUSED" for item in json.loads(rows[0]["payload_json"])["activities"]))

    def test_duplicate_operation_and_restart_do_not_duplicate_revision_or_audit(self):
        proposal = self.propose()
        self.confirm(proposal)
        first = self.approve(proposal)
        audit_count = self.count("schedule_reschedule_audit")
        event_count = len(self.events.get_events_since())
        self.service = StrictBotAssistant(self.db, self.scoring, clock=lambda: self.now)
        second = self.approve(proposal)
        self.assertEqual(first, second)
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 2)
        self.assertEqual(self.count("schedule_reschedule_audit"), audit_count)
        self.assertEqual(len(self.events.get_events_since()), event_count)
        repeated_new_id = self.approve(proposal, request_id="second-confirm-click")
        self.assertTrue(repeated_new_id["success"])
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 2)

    def test_another_account_cannot_confirm_the_saved_proposal(self):
        proposal = self.propose()
        result = self.action("confirm_schedule_change", {"proposal_id": proposal["proposal_id"]}, principal=self.other)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "PROPOSAL_NOT_FOUND")


class StrictBotResilienceTests(AssistantFixture):
    def test_essential_travel_details_are_retained_across_conversation_restart(self):
        self.activate()
        partial = self.service.chat(self.user, "I am travelling at 5 PM for 3 hours", "partial-travel")
        self.assertEqual(partial["code"], "DETAILS_REQUIRED")
        self.service = StrictBotAssistant(self.db, self.scoring, clock=lambda: self.now)
        clarified = self.service.chat(self.user, "Today", "clarified-travel", partial["conversation_id"])
        self.assertTrue(clarified["success"], clarified)
        self.assertEqual(clarified["proposals"][0]["event_start"], "2026-10-20T17:00:00+05:30")
        self.assertEqual(clarified["proposals"][0]["event_end"], "2026-10-20T20:00:00+05:30")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 1)

    def test_simultaneous_retry_creates_one_chat_result_and_two_messages(self):
        start = threading.Barrier(6)
        def ask(_):
            start.wait(timeout=5)
            return self.service.chat(self.user, "What should I do next?", "concurrent-chat")
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(ask, range(6)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertEqual(self.count("strictbot_messages"), 2)
        self.assertEqual(self.count("strictbot_operations", "request_id='concurrent-chat'"), 1)

    def test_simultaneous_approval_retry_publishes_one_revision_and_event(self):
        proposal = self.propose()
        self.confirm(proposal)
        start = threading.Barrier(5)
        def approve(_):
            start.wait(timeout=5)
            return self.approve(proposal, request_id="concurrent-approval")
        with ThreadPoolExecutor(max_workers=5) as pool:
            replies = list(pool.map(approve, range(5)))
        self.assertTrue(all(reply == replies[0] for reply in replies), replies)
        self.assertTrue(replies[0]["success"], replies[0])
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 2)
        self.assertEqual(self.count("schedule_reschedule_audit"), 1)
        self.assertEqual(len([e for e in self.events.get_events_since() if e["event_id"].startswith("ASSISTANT-")]), 1)

    def test_chat_restart_and_duplicate_request_preserve_exact_history(self):
        first = self.service.chat(self.user, "What should I do next?", "chat-once")
        self.service = StrictBotAssistant(self.db, self.scoring, clock=lambda: self.now)
        second = self.service.chat(self.user, "What should I do next?", "chat-once")
        self.assertEqual(first, second)
        history = self.service.history(self.user, first["conversation_id"])
        self.assertEqual(len(history["history"]), 2)
        self.assertEqual(history["history"][0]["status"], "USER_REPORTED")
        self.assertEqual(history["history"][1]["actions"][0]["target"], "TASKS")

    def test_history_is_scoped_to_both_account_and_authenticated_actor(self):
        own = self.service.chat(self.user, "My private question", "own-chat")
        for principal in (self.other, self.admin):
            with self.assertRaises(AssistantError) as caught:
                self.service.history(principal, own["conversation_id"])
            self.assertEqual(caught.exception.code, "CONVERSATION_NOT_FOUND")
        self.assertNotEqual(self.service.history(self.other)["conversation_id"], own["conversation_id"])

    def test_request_id_reuse_for_different_input_fails_without_second_operation(self):
        self.service.chat(self.user, "What should I do next?", "same-id")
        with self.assertRaises(AssistantError) as caught:
            self.service.chat(self.user, "Unlock YouTube", "same-id")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.count("strictbot_messages"), 2)

    def test_unknown_action_extra_inputs_and_invalid_types_cannot_change_records(self):
        for name, inputs in (("execute_shell", {"command": "anything"}),
            ("get_current_schedule", {"admin": True}), ("propose_schedule_change", {"duration_minutes": True})):
            with self.assertRaises(AssistantError):
                self.action(name, inputs)
        self.assertEqual(self.count("strictbot_operations"), 0)

    def test_service_failure_returns_failed_operation_and_never_says_done(self):
        with patch.object(self.service, "context", side_effect=RuntimeError("fixture failure")):
            result = self.service.chat(self.user, "What should I do next?", "failed-service")
        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["code"], "SERVICE_ERROR")
        self.assertIn("No completion", result["reply"])
        self.assertEqual(self.service.chat(self.user, "What should I do next?", "failed-service"), result)

    def test_model_unavailable_uses_real_records_and_deterministic_intent(self):
        with patch.dict(os.environ, {"STRICTRECORDS_AI_API_KEY": "fixture-key"}), patch(
            "core.strictbot_assistant.urllib.request.urlopen", side_effect=urllib.error.URLError("fixture offline")) as model:
            result = self.service.chat(self.user, "What should I do next?", "model-offline")
        # urlopen is process-global; ignore unrelated requests from leaked background threads.
        model_calls = [c for c in model.call_args_list
            if "chat/completions" in str(getattr(c.args[0], "full_url", c.args[0]))]
        self.assertEqual(len(model_calls), 1)
        self.assertTrue(result["success"])
        self.assertEqual(result["model_status"], "UNAVAILABLE")
        self.assertFalse(result["context"]["wakeup"]["gate"]["active"])

    def test_ambiguous_intent_requests_clarification_without_mutation(self):
        result = self.service.chat(self.user, "That thing from earlier", "ambiguous")
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("Which", result["reply"])
        self.assertEqual(self.count("schedule_change_proposals"), 0)

    def test_expired_admin_session_cannot_authorize_policy_or_approval(self):
        with self.db._connect() as conn:
            conn.execute("UPDATE admin_sessions SET expires_at='2000-01-01 00:00:00' WHERE token=?", (self.admin.admin_token,))
        with self.assertRaises(AssistantError) as caught:
            self.action("set_schedule_change_policy", {"late_requests": "REJECT", "reason": "Unauthorized"}, principal=self.admin)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual(self.count("strictbot_policy"), 0)


class StrictBotHttpTests(AssistantFixture):
    def setUp(self):
        super().setUp()
        registration = self.db.create_pairing_session(server_endpoint="http://192.168.1.20:8765")
        self.paired = self.db.handshake_pairing(session_key=registration["session_key"],
            device_id="http-a55", device_name="SM-A556E", app_version="1.15.0")
        self.assertTrue(self.paired["success"])
        fixture = self

        class TestHandler(StrictRecordsRequestHandler):
            # Requests travel over real HTTP on loopback. Require device credentials
            # exactly as a remote phone would, instead of local Windows identity.
            db = fixture.db
            engine = fixture.scoring
            strictbot_service = fixture.service
            restriction_mgr = None

            def _is_local_request(self):
                return False

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def http(self, path, body=None, headers=None, authenticated=True):
        auth = {"Content-Type": "application/json"}
        if authenticated:
            auth.update({"X-Device-Id": self.paired["device_id"], "X-Device-Token": self.paired["device_token"]})
        auth.update(headers or {})
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
            headers=auth, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as failure:
            return failure.code, json.loads(failure.read())

    def test_actual_authenticated_chat_context_and_history_routes(self):
        code, context = self.http("/api/strictbot/context?topic=next")
        self.assertEqual(code, 200)
        self.assertEqual(context["actions"][0]["target"], "TASKS")
        code, chat = self.http("/api/strictbot/chat", {"message": "What should I do next?", "request_id": "http-chat"})
        self.assertEqual(code, 200)
        self.assertEqual(chat["mode"], "USER_INSTRUCTOR")
        code, history = self.http("/api/strictbot/history?conversation_id=" + chat["conversation_id"])
        self.assertEqual(code, 200)
        self.assertEqual(len(history["history"]), 2)

    def test_foreign_browser_origin_cannot_use_device_or_loopback_identity(self):
        code, result = self.http("/api/strictbot/context", headers={"Origin": "https://foreign.example"})
        self.assertEqual(code, 403)
        self.assertFalse(result["success"])
        code, result = self.http("/api/strictbot/context", headers={"Origin": self.base})
        self.assertEqual(code, 200)

    def test_missing_wrong_and_revoked_credentials_are_rejected(self):
        code, missing = self.http("/api/strictbot/context", authenticated=False)
        self.assertEqual(code, 401)
        self.assertFalse(missing["success"])
        code, wrong = self.http("/api/strictbot/context", headers={"X-Device-Token": "wrong"})
        self.assertEqual(code, 403)
        self.db.revoke_registered_device(self.paired["device_id"])
        code, revoked = self.http("/api/strictbot/context")
        self.assertEqual(code, 403)
        self.assertFalse(revoked["success"])

    def test_frontend_role_and_account_fields_cannot_select_admin_or_other_account(self):
        code, result = self.http("/api/strictbot/chat", {"message": "What is my schedule?", "request_id": "role-forgery",
            "role": "ADMIN", "is_admin": True, "account_id": "other-user"})
        self.assertEqual(code, 200)
        self.assertEqual(result["mode"], "USER_INSTRUCTOR")
        self.assertEqual(result["context"]["schedule"]["schedule_date"], self.date)
        code, forbidden = self.http("/api/strictbot/action", {"action": "set_schedule_change_policy", "inputs": {
            "late_requests": "REJECT", "reason": "Frontend flag"}, "request_id": "forged-policy", "confirmation": True, "role": "ADMIN"})
        self.assertEqual(code, 403)
        self.assertFalse(forbidden["success"])

    def test_actual_permission_header_records_only_authenticated_device_observation(self):
        snapshot = {"CAMERA": {"status": "NOT_GRANTED", "required": True}}
        code, result = self.http("/api/strictbot/context?topic=permissions", headers={"X-Client-Permission-State": json.dumps(snapshot)})
        self.assertEqual(code, 200)
        self.assertEqual(result["context"]["permissions"]["capabilities"], snapshot)
        code, invalid = self.http("/api/strictbot/context?topic=permissions", headers={"X-Client-Permission-State": "not JSON"})
        self.assertEqual(code, 400)
        self.assertEqual(invalid["code"], "INVALID_PERMISSIONS")

    def test_actual_user_proposal_confirmation_and_admin_approval_http_routes(self):
        self.activate()
        code, proposal = self.http("/api/strictbot/chat", {"message": "I am travelling today at 5 PM for 3 hours", "request_id": "http-travel"})
        self.assertEqual(code, 200)
        pid = proposal["proposals"][0]["proposal_id"]
        code, confirmed = self.http("/api/strictbot/action", {"action": "confirm_schedule_change", "inputs": {"proposal_id": pid},
            "request_id": "http-confirm", "confirmation": True})
        self.assertEqual(code, 200)
        self.assertEqual(confirmed["state"], "PENDING")
        code, applied = self.http("/api/strictbot/action", {"action": "approve_schedule_change", "inputs": {"proposal_id": pid},
            "request_id": "http-approve", "confirmation": True}, headers={"X-Admin-Session": self.admin.admin_token})
        self.assertEqual(code, 200, applied)
        self.assertEqual(applied["state"], "COMPLETED")
        self.assertEqual(self.db.get_confirmed_routine_schedule(self.date)["schedule_version"], 2)

    def test_expired_administrator_header_is_not_treated_as_a_user_success(self):
        with self.db._connect() as conn:
            conn.execute("UPDATE admin_sessions SET expires_at='2000-01-01 00:00:00' WHERE token=?", (self.admin.admin_token,))
        code, result = self.http("/api/strictbot/context?topic=summary", headers={"X-Admin-Session": self.admin.admin_token})
        self.assertEqual(code, 401)
        self.assertEqual(result["code"], "SESSION_EXPIRED")

    def test_http_failures_and_oversize_input_return_meaningful_errors(self):
        code, result = self.http("/api/strictbot/action", {"action": "execute_shell", "inputs": {}, "request_id": "bad-action", "confirmation": True})
        self.assertEqual(code, 400)
        self.assertEqual(result["code"], "UNKNOWN_ACTION")
        code, result = self.http("/api/strictbot/chat", {"message": "x" * 40000, "request_id": "oversize"})
        self.assertEqual(code, 413)
        self.assertEqual(result["code"], "REQUEST_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
