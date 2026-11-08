import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from datetime import timedelta
from unittest.mock import patch

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import StrictRecordsDB
from core.hourly_submission_service import HourlySubmissionError, HourlySubmissionService
from core.server import StrictRecordsRequestHandler
from core.scoring_engine import ScoringEngine
from core.time_window_engine import IST, TimeWindowEngine, dt_to_iso, iso_to_dt, now_ist
from tests.test_daily_routine_gate import seed_approved_wakeup


class _WindowManagerStub:
    def has_opened_activity(self, activity_id, schedule_version=1):
        return False

    def open_submission_window(self, **kwargs):
        return {"action": "OPENED"}

    def expire_submission_window(self, activity_id, **kwargs):
        return {"action": "EXPIRED"}

    def dismiss_submission_form(self, **kwargs):
        return {"action": "SUBMITTED", **kwargs}


class HourlyAttendanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="strictrecords-hourly-")
        self.db = StrictRecordsDB(db_path=os.path.join(self.temp_dir, "attendance.db"))
        self.engine = TimeWindowEngine(self.db)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _activity(self, date_str):
        scoring = ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir, "wake-fixture"))
        seed_approved_wakeup(self.db, scoring, date_str)
        reference = iso_to_dt(f"{date_str}T09:00:00+05:30")
        # These tests inspect a simulated next-day attendance lifecycle. Its
        # authoritative clock must advance after that day's genuine wake result.
        clock = patch("core.time_window_engine.now_ist", return_value=reference)
        clock.start()
        self.addCleanup(clock.stop)
        activities = self.engine.ensure_activities_in_db(date_str, reference_now=reference)
        return next(
            item for item in activities
            if item.get("type") == "HOURLY" and iso_to_dt(item["scheduled_time"]).hour == 10
        )

    @staticmethod
    def _image_bytes():
        image = Image.new("RGB", (160, 120), (26, 90, 48))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _multipart(fields, evidence=None, filename="proof.png"):
        boundary = "----StrictRecordsHourlyProofBoundary"
        chunks = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        if evidence is not None:
            chunks.extend([
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"evidence\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode(),
                evidence,
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        return boundary, b"".join(chunks)

    def _hourly_route_handler(self):
        handler = object.__new__(StrictRecordsRequestHandler)
        handler.db = self.db
        handler.engine = ScoringEngine(
            self.db, storage_root=os.path.join(self.temp_dir, "route-evidence")
        )
        return handler

    def _post_multipart(self, handler, fields, evidence=None, filename="proof.png", received_at=None):
        boundary, raw = self._multipart(fields, evidence, filename)
        responses = []
        handler._send_json = lambda data, status=200: responses.append((data, status))
        handler._get_windows_submission_identity = lambda: ("user_lok", "windows-test")
        content_type = f"multipart/form-data; boundary={boundary}"
        handler._handle_hourly_submission_request(raw, content_type, received_at=received_at)
        return responses[-1]

    def test_hourly_api_requires_category_description_and_evidence(self):
        handler = self._hourly_route_handler()
        image = self._image_bytes()
        common = {"activity_id": "missing-is-checked-after-required-fields", "platform": "WINDOWS"}
        cases = [
            ({**common, "description": "A real description"}, image, "CATEGORY_REQUIRED", "Select an activity category."),
            ({**common, "category": "CODING", "description": "   "}, image, "DESCRIPTION_REQUIRED", "Describe what you did during the last hour."),
            ({**common, "category": "CODING", "description": "A real description"}, None, "ATTACHMENT_REQUIRED", "Attach at least one image or supported file."),
            ({**common, "category": "CODING"}, image, "DESCRIPTION_REQUIRED", "Describe what you did during the last hour."),
        ]
        for fields, evidence, code, message in cases:
            response, status = self._post_multipart(handler, fields, evidence)
            self.assertEqual(status, 422)
            self.assertEqual(response["code"], code)
            self.assertEqual(response["error"], message)

        invalid_fields = {**common, "category": "CODING", "description": "Valid description"}
        response, status = self._post_multipart(handler, invalid_fields, b"not an image")
        self.assertEqual(status, 422)
        self.assertEqual(response["code"], "INVALID_FILE")
        self.assertIn("corrupted", response["error"])

    def test_real_http_multipart_submission_returns_authoritative_response(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        accepted_at = iso_to_dt(activity["window_start"]) + timedelta(seconds=1)
        self.db.register_or_heartbeat_device(
            device_id="windows-test",
            device_name="Temporary Windows API Test",
            platform="WINDOWS",
        )

        class IsolatedRequestHandler(StrictRecordsRequestHandler):
            db = self.db
            engine = ScoringEngine(
                self.db, storage_root=os.path.join(self.temp_dir, "real-http-evidence")
            )

            def _start_hourly_ai_review(self, accepted, file_bytes):
                return None

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedRequestHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/hourly/submit"
        fields = {
            "activity_id": activity["activity_id"],
            "schedule_version": activity.get("schedule_version", 1),
            "submission_id": "sub_real_http_route_test",
            "platform": "WINDOWS",
            "category": "CODING",
            "description": "Submitted through the isolated Windows multipart endpoint.",
        }
        boundary, body = self._multipart(fields, self._image_bytes())
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with patch("core.server.now_ist", return_value=accepted_at), \
                 patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=_WindowManagerStub()):
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    status = response.status
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertEqual(status, 201)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["activity_id"], activity["activity_id"])
        self.assertEqual(payload["accepted_at"], dt_to_iso(accepted_at))
        with self.db._connect() as conn:
            evidence = conn.execute(
                "SELECT file_path, sha256_hash FROM evidence_files WHERE related_record_id = ?",
                (payload["submission_id"],),
            ).fetchone()
        self.assertTrue(os.path.isfile(evidence["file_path"]))
        self.assertEqual(evidence["sha256_hash"], payload["evidence_sha256"])

    def test_real_http_android_notification_poll_and_ack_are_idempotent(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        received_at = iso_to_dt(activity["window_start"]) + timedelta(seconds=1)
        android_id = "android-notification-target"
        windows_id = "windows-notification-target"
        device_token = "isolated-notification-token"
        self.db.register_or_heartbeat_device(
            device_id=android_id,
            device_name="Isolated Android Notification Target",
            platform="ANDROID",
        )
        self.db.register_or_heartbeat_device(
            device_id=windows_id,
            device_name="Isolated Windows Notification Target",
            platform="WINDOWS",
        )
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO device_credentials (credential_id, device_id, account_id, token_hash, issued_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "cred_android_notification_test",
                    android_id,
                    "user_lok",
                    hashlib.sha256(device_token.encode()).hexdigest(),
                    dt_to_iso(now_ist()),
                ),
            )

        service = HourlySubmissionService(
            self.db,
            ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir, "notification-evidence")),
        )
        accepted = service.accept(
            {
                "activity_id": activity["activity_id"],
                "submission_id": "sub_notification_http_test",
                "schedule_version": activity.get("schedule_version", 1),
                "category": "CODING",
                "description": "Accepted proof for Android notification delivery.",
            },
            file_bytes=self._image_bytes(),
            filename="proof.png",
            account_id="user_lok",
            device_id="windows-notification-source",
            platform="WINDOWS",
            received_at=received_at,
        )

        class IsolatedNotificationHandler(StrictRecordsRequestHandler):
            db = self.db
            engine = ScoringEngine(
                self.db, storage_root=os.path.join(self.temp_dir, "notification-http-storage")
            )

            def _start_hourly_ai_review(self, accepted_submission, file_bytes):
                return None

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedNotificationHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        poll_url = (
            f"{base_url}/api/device/submission_events?device_id={android_id}"
            f"&device_token={device_token}"
        )
        try:
            with urllib.request.urlopen(poll_url, timeout=20) as response:
                first_poll = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(poll_url, timeout=20) as response:
                repeated_poll = json.loads(response.read().decode("utf-8"))

            ack_body = json.dumps({
                "device_id": android_id,
                "device_token": device_token,
                "event_ids": [accepted["event_id"]],
            }).encode("utf-8")
            ack_request = urllib.request.Request(
                f"{base_url}/api/device/submission_events/ack",
                data=ack_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(ack_request, timeout=20) as response:
                first_ack = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(ack_request, timeout=20) as response:
                repeated_ack = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(poll_url, timeout=20) as response:
                after_ack = json.loads(response.read().decode("utf-8"))

            windows_poll_url = f"{base_url}/api/hourly/submission_events"
            with urllib.request.urlopen(windows_poll_url, timeout=20) as response:
                windows_poll = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(windows_poll_url, timeout=20) as response:
                windows_repeated_poll = json.loads(response.read().decode("utf-8"))
            windows_ack_body = json.dumps({"event_ids": [accepted["event_id"]]}).encode("utf-8")
            windows_ack_request = urllib.request.Request(
                f"{base_url}/api/hourly/submission_events/ack",
                data=windows_ack_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(windows_ack_request, timeout=20) as response:
                windows_first_ack = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(windows_ack_request, timeout=20) as response:
                windows_repeated_ack = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(windows_poll_url, timeout=20) as response:
                windows_after_ack = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertEqual([item["event_id"] for item in first_poll["events"]], [accepted["event_id"]])
        self.assertEqual([item["event_id"] for item in repeated_poll["events"]], [accepted["event_id"]])
        self.assertEqual(first_ack["acknowledged"], 1)
        self.assertEqual(repeated_ack["acknowledged"], 0)
        self.assertEqual(after_ack["events"], [])
        self.assertEqual([item["event_id"] for item in windows_poll["events"]], [accepted["event_id"]])
        self.assertEqual([item["event_id"] for item in windows_repeated_poll["events"]], [accepted["event_id"]])
        self.assertEqual(windows_first_ack["acknowledged"], 1)
        self.assertEqual(windows_repeated_ack["acknowledged"], 0)
        self.assertEqual(windows_after_ack["events"], [])

    def test_windows_multipart_upload_persists_and_retry_deduplicates(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        accepted_at = iso_to_dt(activity["window_end"]) - timedelta(seconds=1)
        processing_finished_at = iso_to_dt(activity["window_end"]) + timedelta(seconds=5)
        handler = self._hourly_route_handler()
        fields = {
            "activity_id": activity["activity_id"],
            "schedule_version": activity.get("schedule_version", 1),
            "submission_id": "sub_multipart_retry_test",
            "date": date_str,
            "platform": "WINDOWS",
            "category": "BREAK",
            "description": "Took a short restorative break and stretched.",
        }
        with patch("core.hourly_submission_service.now_ist", return_value=processing_finished_at), \
             patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=_WindowManagerStub()), \
             patch.object(handler, "_start_hourly_ai_review"):
            response, status = self._post_multipart(handler, fields, self._image_bytes(), received_at=accepted_at)
            retry, retry_status = self._post_multipart(handler, fields, self._image_bytes(), received_at=processing_finished_at)

        self.assertEqual(status, 201)
        self.assertTrue(response["accepted"])
        self.assertTrue(response["has_evidence"])
        self.assertEqual(response["category"], "BREAK")
        self.assertEqual(response["accepted_at"], dt_to_iso(accepted_at))
        self.assertEqual(retry_status, 200)
        self.assertTrue(retry["deduplicated"])
        self.assertEqual(retry["submission_id"], response["submission_id"])
        with self.db._connect() as conn:
            activity_row = conn.execute(
                "SELECT status, attendance_status, validation_status, points, details_json FROM scheduled_activities WHERE activity_id = ?",
                (activity["activity_id"],),
            ).fetchone()
            evidence_row = conn.execute("SELECT file_path FROM evidence_files WHERE related_record_id = ?", (response["submission_id"],)).fetchone()
            evidence_count = conn.execute("SELECT COUNT(*) FROM evidence_files WHERE related_record_id = ?", (response["submission_id"],)).fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM proof_submission_events WHERE submission_id = ?", (response["submission_id"],)).fetchone()[0]
            hourly_count = conn.execute("SELECT COUNT(*) FROM hourly_records WHERE date = ? AND hour_slot = ?", (date_str, json.loads(activity_row["details_json"]).get("hour_slot"))).fetchone()[0]
        self.assertTrue(evidence_row)
        self.assertTrue(os.path.isfile(evidence_row["file_path"]))
        self.assertEqual(evidence_count, 1)
        self.assertEqual(event_count, 1)
        self.assertEqual(hourly_count, 1)
        self.assertEqual(activity_row["attendance_status"], "PRESENT")
        self.assertEqual(activity_row["validation_status"], "PROCESSING")
        self.assertEqual(activity_row["points"], 0)
        self.assertEqual(activity_row["status"], "VALIDATING")
        service = HourlySubmissionService(self.db, handler.engine)
        pending = service.get_pending_events("user_lok", "android-test")
        self.assertEqual([event["event_id"] for event in pending], [response["event_id"]])
        self.assertEqual(service.acknowledge_events("user_lok", "android-test", [response["event_id"]]), 1)
        self.assertEqual(service.get_pending_events("user_lok", "android-test"), [])

    def test_hourly_acceptance_uses_half_open_server_window(self):
        service = HourlySubmissionService(
            self.db,
            ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir, "boundary-evidence")),
        )
        for offset_days, suffix in ((1, "start"), (2, "close")):
            date_str = (now_ist().date() + timedelta(days=offset_days)).isoformat()
            activity = self._activity(date_str)
            start = iso_to_dt(activity["window_start"])
            end = iso_to_dt(activity["window_end"])
            received_at = start if suffix == "start" else end
            payload = {
                "activity_id": activity["activity_id"],
                "submission_id": f"sub_boundary_{suffix}",
                "schedule_version": activity.get("schedule_version", 1),
                "category": "OTHER",
                "description": f"Boundary verification at {suffix}",
            }
            if suffix == "start":
                result = service.accept(
                    payload, file_bytes=self._image_bytes(), filename="proof.png",
                    account_id="user_lok", device_id="windows-test", platform="WINDOWS",
                    received_at=received_at,
                )
                self.assertTrue(result["accepted"])
                self.assertEqual(result["accepted_at"], dt_to_iso(start))
            else:
                with self.assertRaises(HourlySubmissionError) as error:
                    service.accept(
                        payload, file_bytes=self._image_bytes(), filename="proof.png",
                        account_id="user_lok", device_id="windows-test", platform="WINDOWS",
                        received_at=received_at,
                    )
                self.assertEqual(error.exception.code, "WINDOW_CLOSED")

    def test_simultaneous_device_submissions_create_one_accepted_record(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        received_at = iso_to_dt(activity["window_start"]) + timedelta(seconds=1)
        service = HourlySubmissionService(
            self.db,
            ScoringEngine(self.db, storage_root=os.path.join(self.temp_dir, "concurrent-evidence")),
        )
        evidence = self._image_bytes()

        def submit(source, submission_id):
            try:
                return service.accept(
                    {
                        "activity_id": activity["activity_id"],
                        "submission_id": submission_id,
                        "schedule_version": activity.get("schedule_version", 1),
                        "category": "CODING",
                        "description": f"Concurrent proof from {source}",
                    },
                    file_bytes=evidence,
                    filename="proof.png",
                    account_id="user_lok",
                    device_id=source,
                    platform=source.upper(),
                    received_at=received_at,
                )
            except HourlySubmissionError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda item: submit(*item), [
                ("windows-test", "sub_concurrent_windows"),
                ("android-test", "sub_concurrent_android"),
            ]))

        accepted = [result for result in results if isinstance(result, dict) and result.get("accepted")]
        rejected = [result for result in results if isinstance(result, str)]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, ["DUPLICATE_SUBMISSION"])
        with self.db._connect() as conn:
            evidence_count = conn.execute("SELECT COUNT(*) FROM evidence_files WHERE date = ? AND record_type = 'Hourly'", (date_str,)).fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM proof_submission_events WHERE activity_id = ?", (activity["activity_id"],)).fetchone()[0]
        self.assertEqual(evidence_count, 1)
        self.assertEqual(event_count, 1)

    def test_half_open_boundaries_and_duplicate_submit(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        start = iso_to_dt(activity["window_start"])
        end = iso_to_dt(activity["window_end"])

        too_early = self.engine.record_activity_submission(
            activity["activity_id"], sub_time=start - timedelta(seconds=1),
            submission_id="sub-early", description="Too early",
        )
        self.assertFalse(too_early["allowed"])
        self.assertEqual(too_early["status"], "TOO_EARLY")

        accepted = self.engine.record_activity_submission(
            activity["activity_id"], sub_time=start, submission_id="sub-on-time",
            description="Started the work session", begin_ai_validation=True,
        )
        self.assertTrue(accepted["allowed"])
        self.assertEqual(accepted["attendance_status"], "PRESENT")
        self.assertEqual(accepted["validation_status"], "PROCESSING")

        duplicate = self.engine.record_activity_submission(
            activity["activity_id"], sub_time=end - timedelta(seconds=1),
            submission_id="sub-duplicate", description="Duplicate retry",
        )
        self.assertFalse(duplicate["allowed"])
        self.assertEqual(duplicate["status"], "DUPLICATE")

    def test_ai_rejection_after_close_keeps_present_attendance(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        end = iso_to_dt(activity["window_end"])
        last_valid = end - timedelta(seconds=1)

        accepted = self.engine.record_activity_submission(
            activity["activity_id"], sub_time=last_valid, submission_id="sub-ai-late",
            description="Proof was captured before the close", begin_ai_validation=True,
        )
        self.assertTrue(accepted["allowed"])

        self.engine.get_activities_for_date(date_str, reference_now=end)
        result = self.engine.record_activity_validation_status(
            activity["activity_id"], "REJECTED", {"decision": "REJECTED", "reason": "evidence failed review"}
        )
        self.assertEqual(result["attendance_status"], "PRESENT")
        self.assertEqual(result["validation_status"], "REJECTED")

        row = next(
            item for item in self.engine.get_hourly_attendance_records(date_str, reference_now=end)
            if item["activity_id"] == activity["activity_id"]
        )
        self.assertEqual(row["attendance_status"], "PRESENT")
        self.assertEqual(row["validation_status"], "REJECTED")
        self.assertEqual(row["submitted_at"], dt_to_iso(last_valid))
        self.assertEqual(row["points"], 0)

    def test_open_and_closed_notifications_are_claimed_once(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        start = iso_to_dt(activity["window_start"])
        end = iso_to_dt(activity["window_end"])

        with patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=_WindowManagerStub()):
            opened = self.engine.get_pending_notifications(date_str, reference_now=start)
            opened_again = self.engine.get_pending_notifications(date_str, reference_now=start + timedelta(seconds=5))
            open_events = [event for event in opened if event.get("event_type") == "OPENED" and event.get("activity_id") == activity["activity_id"]]
            self.assertEqual(len(open_events), 1)
            self.assertFalse(any(event.get("activity_id") == activity["activity_id"] and event.get("event_type") == "OPENED" for event in opened_again))

            closed = self.engine.get_pending_notifications(date_str, reference_now=end)
            closed_again = self.engine.get_pending_notifications(date_str, reference_now=end + timedelta(seconds=5))
            close_events = [event for event in closed if event.get("event_type") == "CLOSED" and event.get("activity_id") == activity["activity_id"]]
            self.assertEqual(len(close_events), 1)
            self.assertEqual(close_events[0]["attendance_status"], "ABSENT")
            self.assertFalse(any(event.get("activity_id") == activity["activity_id"] and event.get("event_type") == "CLOSED" for event in closed_again))

        closed_row = next(
            item for item in self.engine.get_hourly_attendance_records(date_str, reference_now=end)
            if item["activity_id"] == activity["activity_id"]
        )
        self.assertEqual(closed_row["attendance_status"], "ABSENT")
        self.assertEqual(closed_row["validation_status"], "NOT_SUBMITTED")

    def test_offline_submission_cannot_reopen_a_closed_window(self):
        date_str = (now_ist().date() - timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        start = iso_to_dt(activity["window_start"])
        end = iso_to_dt(activity["window_end"])
        last_valid = end - timedelta(seconds=1)
        self.engine.get_activities_for_date(date_str, reference_now=end)

        service = HourlySubmissionService(
            self.db, ScoringEngine(db=self.db), storage_root=os.path.join(self.temp_dir, "evidence")
        )
        payload = {
            "activity_id": activity["activity_id"],
            "schedule_version": int(activity.get("schedule_version") or 1),
            "submission_id": "sub-offline-reconcile",
            "date": date_str,
            "primary_activity": "CODING",
            "description": "Finished the coding task while offline",
        }
        with self.assertRaises(HourlySubmissionError) as error:
            service.accept(
                payload,
                file_bytes=self._image_bytes(),
                filename="proof.png",
                account_id="user_lok",
                device_id="android-test",
                platform="ANDROID",
                received_at=last_valid,
            )
        self.assertEqual(error.exception.code, "WINDOW_CLOSED")

        record = next(
            item for item in self.engine.get_hourly_attendance_records(date_str, reference_now=now_ist())
            if item["activity_id"] == activity["activity_id"]
        )
        self.assertEqual(record["attendance_status"], "ABSENT")
        self.assertEqual(record["validation_status"], "NOT_SUBMITTED")
        self.assertIsNone(record["submitted_at"])
        self.assertEqual(record["points"], 0)

    def test_android_sync_endpoint_uses_server_receipt_time_not_client_timestamp(self):
        date_str = (now_ist().date() + timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        window_start = iso_to_dt(activity["window_start"])
        server_received_at = window_start + timedelta(seconds=1)
        client_timestamp = window_start - timedelta(seconds=1)
        handler = object.__new__(StrictRecordsRequestHandler)
        handler.db = self.db
        handler.engine = ScoringEngine(
            self.db, storage_root=os.path.join(self.temp_dir, "server-evidence")
        )
        device_id = "registered-android-test"
        device_token = "offline-device-token"
        self.db.register_or_heartbeat_device(
            device_id=device_id,
            device_name="Offline Sync Test Phone",
            platform="ANDROID",
        )
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO device_credentials (credential_id, device_id, account_id, token_hash, issued_at) VALUES (?, ?, ?, ?, ?)",
                ("cred_offline_sync_test", device_id, "user_lok", hashlib.sha256(device_token.encode()).hexdigest(), dt_to_iso(now_ist())),
            )
        payload = {
            "activity_id": activity["activity_id"],
            "schedule_version": int(activity.get("schedule_version") or 1),
            "submission_id": "sub_sync_endpoint_offline",
            "date": date_str,
            "category": "CODING",
            "description": "Worked offline before the deadline",
            "evidence_filename": "proof.png",
            "image_base64": base64.b64encode(self._image_bytes()).decode("ascii"),
        }
        body = {
            "device_id": device_id,
            "device_token": device_token,
            "events": [{
                "event_id": "evt_sync_endpoint_offline",
                "event_type": "HOURLY_PROOF_SUBMITTED",
                "timestamp": dt_to_iso(client_timestamp),
                "payload": payload,
            }],
        }
        with patch("core.time_window_engine.now_ist", return_value=server_received_at), \
             patch("windows.submission_window_manager.SubmissionWindowManager.get_instance", return_value=_WindowManagerStub()), \
             patch.object(handler, "_start_hourly_ai_review"), \
             patch.object(handler, "_dispatch_hourly_submission", wraps=handler._dispatch_hourly_submission) as dispatch, \
             patch.object(handler, "_send_json") as send_json:
            handler._handle_device_sync_events_request(body)

        self.assertIsNotNone(dispatch.call_args, send_json.call_args)
        args, kwargs = dispatch.call_args
        self.assertEqual(args[0]["activity_id"], activity["activity_id"])
        self.assertEqual(args[3:6], ("user_lok", "registered-android-test", "ANDROID"))
        self.assertEqual(kwargs["received_at"], server_received_at)
        self.assertNotIn("allow_offline_reconcile", kwargs)
        response = send_json.call_args.args[0]
        self.assertTrue(response["event_results"][0]["accepted"], response["event_results"][0])
        self.assertFalse(response["event_results"][0]["reconciled_offline"])
        self.assertEqual(response["event_results"][0]["attendance_status"], "PRESENT")
        self.assertEqual(response["event_results"][0]["submission"]["accepted_at"], dt_to_iso(server_received_at))
        record = next(
            item for item in self.engine.get_hourly_attendance_records(date_str, reference_now=server_received_at)
            if item["activity_id"] == activity["activity_id"]
        )
        self.assertEqual(record["attendance_status"], "PRESENT")
        self.assertEqual(record["submitted_at"], dt_to_iso(server_received_at))

    def test_offline_event_at_close_is_not_reconciled(self):
        date_str = (now_ist().date() - timedelta(days=1)).isoformat()
        activity = self._activity(date_str)
        end = iso_to_dt(activity["window_end"])
        self.engine.get_activities_for_date(date_str, reference_now=end)
        service = HourlySubmissionService(
            self.db, ScoringEngine(db=self.db), storage_root=os.path.join(self.temp_dir, "evidence")
        )
        with self.assertRaises(HourlySubmissionError) as error:
            service.accept(
                {
                    "activity_id": activity["activity_id"],
                    "submission_id": "sub-offline-at-close",
                    "primary_activity": "CODING",
                    "description": "Capture happened at the cutoff",
                },
                file_bytes=self._image_bytes(),
                filename="proof.png",
                account_id="user_lok",
                device_id="android-test",
                platform="ANDROID",
                received_at=end,
            )
        self.assertEqual(error.exception.code, "WINDOW_CLOSED")
        row = next(
            item for item in self.engine.get_hourly_attendance_records(date_str, reference_now=now_ist())
            if item["activity_id"] == activity["activity_id"]
        )
        self.assertEqual(row["attendance_status"], "ABSENT")
        self.assertIsNone(row["submitted_at"])


if __name__ == "__main__":
    unittest.main()
