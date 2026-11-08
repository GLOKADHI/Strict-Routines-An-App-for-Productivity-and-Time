"""
StrictRecords — Email-Based Scheduler Prebooking Engine
======================================================
Integrates browser extension email capture with ChatGPT (OpenAI API)
and deterministic scheduling engines for Movie, Exam, and Travel Prebooking.

Handles:
- Input sanitization (prompt-injection defense, HTML/script stripping, size limits).
- ChatGPT / OpenAI API validation and structured parsing (gpt-4o-mini).
- High-availability heuristic fallback when OpenAI API key is unconfigured or offline.
- Batch exam scheduling (up to 4 tests with independent buffers & docs).
- Periodic exam schedule configuration.
- Deterministic conflict checking (locked sessions, exams, events, buffer bounds).
- Idempotent booking persistence and cross-device sync enqueuing (Android + Windows).
- Automatic attendance exemption with full points during verified exams.
- Movie usage monitoring & travel presence check suppression flags.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date as calendar_date, time as clock_time, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger("StrictRecords.EmailPrebookingEngine")

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
OPENAI_CHAT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

# Validation States
STATE_EXTRACTED = "EXTRACTED"
STATE_VALIDATING = "VALIDATING"
STATE_APPROVED = "APPROVED"
STATE_NEEDS_REVIEW = "NEEDS_REVIEW"
STATE_INCONSISTENT = "INCONSISTENT"
STATE_INCOMPLETE = "INCOMPLETE"
STATE_REJECTED = "REJECTED"

# Booking Categories
CAT_MOVIE = "MOVIE_PREBOOK"
CAT_EXAM = "EXAM_PREBOOK"
CAT_TRAVEL = "TRAVEL_PREBOOK"

# In-memory idempotency cache for prebooking confirmations
_IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}


def sanitize_email_content(raw_text: str, max_length: int = 12000) -> str:
    """
    Sanitizes untrusted email content:
    - Strips CSS <style>, <script>, <svg>, <iframe> blocks.
    - Strips HTML tags while preserving line breaks.
    - Strips prompt-injection phrases.
    - Truncates excessively long text.
    """
    if not raw_text:
        return ""

    text = str(raw_text)

    # 1. Remove style, script, svg blocks
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<svg.*?>.*?</svg>", " ", text)
    text = re.sub(r"(?is)<iframe.*?>.*?</iframe>", " ", text)

    # 2. Convert common HTML breaks to newline
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|tr|li|h[1-6])>", "\n", text)

    # 3. Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # 4. Strip prompt injection attempts
    injection_patterns = [
        r"(?i)\bignore\s+(?:all\s+)?previous\s+instructions\b",
        r"(?i)\bdisregard\s+(?:all\s+)?(?:above|previous|system)\b",
        r"(?i)\bsystem\s+prompt\b",
        r"(?i)\boverride\s+(?:all\s+)?(?:rules|policies|guardrails)\b",
        r"(?i)\byou\s+are\s+now\s+in\s+developer\s+mode\b",
        r"(?i)\bjailbreak\b",
    ]
    for pattern in injection_patterns:
        text = re.sub(pattern, "[FILTERED_SECURITY_STRING]", text)

    # 5. Normalize whitespace and carriage returns
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. Truncate
    return text[:max_length].strip()


def parse_date_candidates(text: str) -> List[str]:
    """Finds candidate dates in YYYY-MM-DD or DD/MM/YYYY or Month DD, YYYY formats."""
    results = []
    # YYYY-MM-DD
    for m in re.finditer(r"\b(202[4-9])-([01]\d)-([0-3]\d)\b", text):
        results.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")

    # DD/MM/YYYY or DD-MM-YYYY
    for m in re.finditer(r"\b([0-3]?\d)[/-]([01]?\d)[/-](202[4-9])\b", text):
        d, mo, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            results.append(f"{yr:04d}-{mo:02d}-{d:02d}")

    # Month DD, YYYY (e.g. November 15, 2026 or Nov 15 2026)
    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    m_pattern = r"(?i)\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+([0-3]?\d)(?:st|nd|rd|th)?,?\s+(202[4-9])\b"
    for m in re.finditer(m_pattern, text):
        mo_str = m.group(1).lower()
        mo_val = months.get(mo_str, 1)
        d_val = int(m.group(2))
        yr_val = int(m.group(3))
        if 1 <= d_val <= 31:
            results.append(f"{yr_val:04d}-{mo_val:02d}-{d_val:02d}")

    # Deduplicate while preserving order
    seen = set()
    dedup = []
    for d in results:
        if d not in seen:
            seen.add(d)
            dedup.append(d)
    return dedup


def parse_time_candidates(text: str) -> List[str]:
    """Finds candidate times in HH:MM AM/PM or HH:MM 24h formats."""
    results = []
    # 12-hour AM/PM e.g. 09:30 AM, 2:00 PM, 10am
    for m in re.finditer(r"\b([01]?\d)(?::([0-5]\d))?\s*(am|pm)\b", text, re.IGNORECASE):
        hr = int(m.group(1))
        mn = int(m.group(2) or 0)
        mer = m.group(3).lower()
        if 1 <= hr <= 12 and 0 <= mn <= 59:
            if mer == "pm" and hr < 12:
                hr += 12
            elif mer == "am" and hr == 12:
                hr = 0
            results.append(f"{hr:02d}:{mn:02d}")

    # 24-hour HH:MM (e.g. 09:30, 14:00)
    for m in re.finditer(r"\b([0-2]\d):([0-5]\d)\b", text):
        hr = int(m.group(1))
        mn = int(m.group(2))
        if 0 <= hr <= 23 and 0 <= mn <= 59:
            val = f"{hr:02d}:{mn:02d}"
            if val not in results:
                results.append(val)

    return results


class OpenAiEmailValidator:
    """Official OpenAI API integration for email schedule validation."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self._custom_api_key = api_key
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.endpoint = os.environ.get("OPENAI_ENDPOINT") or OPENAI_CHAT_ENDPOINT

    @property
    def api_key(self) -> Optional[str]:
        if self._custom_api_key and str(self._custom_api_key).strip():
            return self._custom_api_key.strip()
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("STRICTRECORDS_AI_API_KEY")
        if key and str(key).strip():
            return str(key).strip()
        try:
            import keyring
            stored = keyring.get_password("StrictRecords", "OpenAI_API_Key") or keyring.get_password("StrictRecords", "OpenAI_Key")
            if stored and stored.strip():
                return stored.strip()
        except Exception:
            pass
        return None

    def is_configured(self) -> bool:
        k = self.api_key
        return bool(k and len(k) >= 8 and (k.startswith("sk-") or k.startswith("sess-") or "openai" not in self.endpoint))

    def validate(self, email_data: Dict[str, Any], category_hint: Optional[str] = None) -> Dict[str, Any]:
        """Calls OpenAI Chat Completions API with structured system instructions."""
        cleaned_body = sanitize_email_content(email_data.get("body", ""))
        subject = sanitize_email_content(email_data.get("subject", ""), max_length=300)
        sender = sanitize_email_content(f"{email_data.get('sender_name', '')} <{email_data.get('sender_email', '')}>", max_length=200)
        date_received = str(email_data.get("date") or "").strip()
        attachments = email_data.get("attachments") or []

        att_summary = ", ".join([f"{a.get('name', 'file')} ({a.get('size_str', 'unknown size')})" for a in attachments[:5]]) if attachments else "None"

        system_prompt = (
            "You are the authoritative Email Scheduling Extractor and Validator for StrictRecords.\n"
            "Analyze the provided email text to determine if it is a Movie Prebook, Exam Prebook, or Travel Prebook.\n"
            "Rules:\n"
            "1. Output MUST be valid JSON only, without markdown fences or additional commentary.\n"
            "2. Verify date and time consistency, event duration, internal contradictions, and missing info.\n"
            "3. Detect rescheduling notices (e.g. 'exam postponed to') or cancellation notices.\n"
            "4. Classify category into one of: 'MOVIE_PREBOOK', 'EXAM_PREBOOK', 'TRAVEL_PREBOOK'.\n"
            "5. For EXAM_PREBOOK: extract up to 4 distinct tests if present (exam_name, subject, exam_date (YYYY-MM-DD), start_time (HH:MM), end_time (HH:MM), prep_buffer_minutes, completion_buffer_minutes).\n"
            "6. For MOVIE_PREBOOK: extract movie_name, cinema_venue, exam_date/date (YYYY-MM-DD), start_time (HH:MM), end_time (HH:MM), prep_buffer_minutes, completion_buffer_minutes.\n"
            "7. For TRAVEL_PREBOOK: extract travel_type (FLIGHT, TRAIN, BUS, CAB, PERSONAL), purpose, origin, destination, departure_date (YYYY-MM-DD), departure_time (HH:MM), return_date, return_time, prep_buffer_minutes, completion_buffer_minutes.\n"
            "8. Default buffers if not specified: Movie 30m prep / 30m comp; Exam 45m prep / 30m comp; Travel 90m prep / 60m comp.\n"
            "9. Set validation_status to: 'APPROVED' (all clear & complete), 'NEEDS_REVIEW' (minor ambiguities), 'INCONSISTENT' (contradicting dates/times), 'INCOMPLETE' (missing critical timing/venue), or 'REJECTED' (cancelled or unrelated).\n"
            "JSON structure:\n"
            "{\n"
            '  "validation_status": "APPROVED|NEEDS_REVIEW|INCONSISTENT|INCOMPLETE|REJECTED",\n'
            '  "confidence_score": 0.0 to 1.0,\n'
            '  "extracted_category": "MOVIE_PREBOOK|EXAM_PREBOOK|TRAVEL_PREBOOK",\n'
            '  "event_name": "string",\n'
            '  "organization_or_venue": "string",\n'
            '  "is_rescheduled": false,\n'
            '  "is_cancelled": false,\n'
            '  "parsed_slots": [\n'
            '    {\n'
            '      "slot_name": "string",\n'
            '      "subject": "string",\n'
            '      "date": "YYYY-MM-DD",\n'
            '      "start_time": "HH:MM",\n'
            '      "end_time": "HH:MM",\n'
            '      "prep_buffer_minutes": 30,\n'
            '      "completion_buffer_minutes": 30\n'
            '    }\n'
            '  ],\n'
            '  "travel_details": {\n'
            '    "travel_type": "FLIGHT|TRAIN|BUS|CAB|PERSONAL",\n'
            '    "origin": "string",\n'
            '    "destination": "string",\n'
            '    "departure_date": "YYYY-MM-DD",\n'
            '    "departure_time": "HH:MM",\n'
            '    "return_date": "YYYY-MM-DD or null",\n'
            '    "return_time": "HH:MM or null"\n'
            '  },\n'
            '  "contradictions": ["string"],\n'
            '  "missing_fields": ["string"],\n'
            '  "recommendations": ["string"],\n'
            '  "summary": "string"\n'
            "}"
        )

        user_content = (
            f"Sender: {sender}\n"
            f"Subject: {subject}\n"
            f"Date Received: {date_received}\n"
            f"Attachments: {att_summary}\n"
            f"Category Hint: {category_hint or 'None'}\n\n"
            f"Email Body:\n{cleaned_body}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choice_content = data["choices"][0]["message"]["content"]
            parsed = json.loads(choice_content)
            parsed["provider"] = "OPENAI_API"
            return parsed


class HeuristicEmailValidator:
    """High-availability fallback validator when OpenAI API key is unavailable."""

    @staticmethod
    def validate(email_data: Dict[str, Any], category_hint: Optional[str] = None) -> Dict[str, Any]:
        body = sanitize_email_content(email_data.get("body", ""))
        subject = sanitize_email_content(email_data.get("subject", ""))
        combined = f"{subject}\n{body}".lower()

        # Category determination
        category = category_hint or CAT_EXAM
        if not category_hint:
            movie_keywords = ["movie", "cinema", "theatre", "theater", "showtime", "ticket", "imax", "pvr", "inox", "cinepolis", "seat no", "screen"]
            travel_keywords = ["flight", "boarding", "airline", "pnr", "train", "irctc", "bus", "departure", "arrival", "hotel", "travel", "indigo", "air india", "vistara"]
            exam_keywords = ["exam", "test", "assessment", "hall ticket", "admit card", "examination", "semester", "quiz", "interview", "hcltech", "tcs", "infosys", "gate", "neet", "jee"]

            movie_score = sum(1 for k in movie_keywords if k in combined)
            travel_score = sum(1 for k in travel_keywords if k in combined)
            exam_score = sum(1 for k in exam_keywords if k in combined)

            if movie_score > travel_score and movie_score > exam_score:
                category = CAT_MOVIE
            elif travel_score > exam_score:
                category = CAT_TRAVEL
            else:
                category = CAT_EXAM

        # Dates & Times
        dates = parse_date_candidates(f"{subject}\n{body}")
        times = parse_time_candidates(f"{subject}\n{body}")

        # Fallback date to today or tomorrow if none found
        today_str = calendar_date.today().isoformat()
        primary_date = dates[0] if dates else today_str
        start_t = times[0] if times else "10:00"
        end_t = times[1] if len(times) > 1 else "12:00"

        # Determine venue/institution
        org_or_venue = "Institution / Venue"
        for line in (subject + "\n" + body).splitlines():
            line_s = line.strip()
            if any(term in line_s.lower() for term in ["university", "college", "institute", "technologies", "limited", "cinema", "pvr", "inox", "airlines", "railways"]):
                org_or_venue = line_s[:60]
                break

        is_cancelled = any(w in combined for w in ["cancelled", "canceled", "call off", "withdrawn"])
        is_rescheduled = any(w in combined for w in ["rescheduled", "postponed", "revised schedule", "new date"])

        missing_fields = []
        if not dates:
            missing_fields.append("Explicit event date")
        if not times:
            missing_fields.append("Explicit event start time")

        status = STATE_APPROVED
        if is_cancelled:
            status = STATE_REJECTED
        elif missing_fields:
            status = STATE_INCOMPLETE
        elif is_rescheduled:
            status = STATE_NEEDS_REVIEW

        slots = [{
            "slot_name": subject[:60] or "Scheduled Event",
            "subject": subject[:40] or "General",
            "date": primary_date,
            "start_time": start_t,
            "end_time": end_t,
            "prep_buffer_minutes": 45 if category == CAT_EXAM else 30 if category == CAT_MOVIE else 90,
            "completion_buffer_minutes": 30 if category != CAT_TRAVEL else 60,
        }]

        return {
            "validation_status": status,
            "confidence_score": 0.82 if (dates and times) else 0.55,
            "extracted_category": category,
            "event_name": subject[:80] or "Scheduled Event",
            "organization_or_venue": org_or_venue,
            "is_rescheduled": is_rescheduled,
            "is_cancelled": is_cancelled,
            "parsed_slots": slots,
            "travel_details": {
                "travel_type": "FLIGHT" if "flight" in combined else "TRAIN" if "train" in combined else "BUS",
                "origin": "Origin City",
                "destination": "Destination City",
                "departure_date": primary_date,
                "departure_time": start_t,
                "return_date": dates[1] if len(dates) > 1 else None,
                "return_time": times[2] if len(times) > 2 else None,
            },
            "contradictions": [],
            "missing_fields": missing_fields,
            "recommendations": [
                "Review extracted times against your official document.",
                "Ensure sufficient buffer before and after the event."
            ],
            "summary": f"Extracted {category} from email '{subject[:60]}'.",
            "provider": "HEURISTIC_PARSER",
        }


def validate_email_schedule(email_data: Dict[str, Any], category_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    Main entry point for email schedule validation:
    1. Attempts OpenAI validation if configured.
    2. Falls back cleanly to Heuristic validation if OpenAI is unconfigured, times out, or errors.
    3. Merges deterministic conflict checks against existing database records.
    """
    ai_validator = OpenAiEmailValidator()
    result = None

    if ai_validator.is_configured():
        try:
            logger.info("Evaluating email using official OpenAI API...")
            result = ai_validator.validate(email_data, category_hint=category_hint)
        except Exception as e:
            logger.warning("OpenAI validation failed or timed out: %s. Falling back to heuristic parser.", e)

    if not result:
        result = HeuristicEmailValidator.validate(email_data, category_hint=category_hint)

    # Perform deterministic conflict check on proposed slots
    category = result.get("extracted_category") or category_hint or CAT_EXAM
    slots = result.get("parsed_slots") or []
    conflict_report = check_schedule_conflicts(category, slots)

    result["conflict_check"] = conflict_report
    if conflict_report.get("conflicts"):
        if result.get("validation_status") == STATE_APPROVED:
            result["validation_status"] = STATE_NEEDS_REVIEW
        result.setdefault("contradictions", []).extend(conflict_report["conflicts"])

    return result


def check_schedule_conflicts(category: str, slots: List[Dict[str, Any]], db: Optional[Any] = None) -> Dict[str, Any]:
    """
    Checks deterministic schedule conflicts against existing approved exams,
    official events, and buffer policy constraints.
    """
    if db is None:
        from core.database import StrictRecordsDB
        db = StrictRecordsDB()
    conflicts = []

    # 1. Buffer bound check
    for idx, slot in enumerate(slots):
        p_buf = int(slot.get("prep_buffer_minutes") or 0)
        c_buf = int(slot.get("completion_buffer_minutes") or 0)
        if p_buf > 180:
            conflicts.append(f"Slot #{idx+1}: Preparation buffer ({p_buf}m) exceeds maximum allowable 180m.")
        if c_buf > 180:
            conflicts.append(f"Slot #{idx+1}: Completion buffer ({c_buf}m) exceeds maximum allowable 180m.")

    # 2. Query existing bookings
    try:
        existing_exams = db.list_exam_prebookings(include_past=False)
        for slot in slots:
            s_date = str(slot.get("date") or "").strip()
            s_start = str(slot.get("start_time") or "").strip()
            s_end = str(slot.get("end_time") or "").strip()
            if not s_date or not s_start or not s_end:
                continue

            for ex in existing_exams:
                if str(ex.get("exam_date")) == s_date:
                    ex_start = str(ex.get("official_start_time") or "")
                    ex_end = str(ex.get("official_end_time") or "")
                    # Overlap condition: start < other_end and end > other_start
                    if s_start < ex_end and s_end > ex_start:
                        conflicts.append(
                            f"Proposed slot on {s_date} ({s_start}-{s_end}) conflicts with existing Exam '{ex.get('exam_name')}' ({ex_start}-{ex_end})."
                        )
    except Exception as e:
        logger.warning("Could not query existing exams for conflict check: %s", e)

    try:
        existing_events = db.list_official_event_bookings(include_past=False)
        for slot in slots:
            s_date = str(slot.get("date") or "").strip()
            s_start = str(slot.get("start_time") or "").strip()
            s_end = str(slot.get("end_time") or "").strip()
            if not s_date or not s_start or not s_end:
                continue

            for ev in existing_events:
                if str(ev.get("event_date")) == s_date:
                    ev_start = str(ev.get("start_time") or "")
                    ev_end = str(ev.get("end_time") or "")
                    if s_start < ev_end and s_end > ev_start:
                        conflicts.append(
                            f"Proposed slot on {s_date} ({s_start}-{s_end}) conflicts with existing Event '{ev.get('title')}' ({ev_start}-{ev_end})."
                        )
    except Exception as e:
        logger.warning("Could not query existing events for conflict check: %s", e)

    return {
        "valid": len(conflicts) == 0,
        "conflicts": conflicts,
    }


def confirm_email_prebook(
    idempotency_key: str,
    category: str,
    booking_details: Dict[str, Any],
    device_id: str = "browser_extension",
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Authoritatively creates the prebooking in the database.
    - Idempotency check: returns existing booking if same idempotency key is submitted.
    - For EXAM_PREBOOK: invokes create_exam_prebooking_batch (supporting 1-4 tests),
      reconciles hourly attendance exemptions with full points.
    - For MOVIE_PREBOOK / TRAVEL_PREBOOK: invokes create_official_event_booking.
    - Enqueues sync events for Android APK and Windows EXE.
    """
    global _IDEMPOTENCY_CACHE

    if idempotency_key and idempotency_key in _IDEMPOTENCY_CACHE:
        logger.info("Idempotent prebook hit for key '%s'", idempotency_key)
        return _IDEMPOTENCY_CACHE[idempotency_key]

    if db is None:
        from core.database import StrictRecordsDB
        db = StrictRecordsDB()

    category_clean = str(category or "").upper().strip()

    if category_clean == CAT_EXAM:
        tests = booking_details.get("tests") or []
        if not tests:
            # Construct single test from top-level fields
            tests = [{
                "exam_name": booking_details.get("exam_name") or booking_details.get("title") or "Scheduled Examination",
                "subject": booking_details.get("subject") or "General",
                "institution": booking_details.get("institution") or "Institution",
                "exam_date": booking_details.get("exam_date") or booking_details.get("date"),
                "start_time": booking_details.get("start_time"),
                "end_time": booking_details.get("end_time"),
                "prep_buffer_minutes": booking_details.get("prep_buffer_minutes", 45),
                "completion_buffer_minutes": booking_details.get("completion_buffer_minutes", 30),
                "supporting_documents": booking_details.get("supporting_documents") or [],
            }]

        # Enforce maximum 4 tests per batch
        if len(tests) > 4:
            raise ValueError(f"Maximum 4 tests allowed in a single exam booking batch. Received {len(tests)}.")

        # Handle Periodic Exam recurrence if configured
        periodic = booking_details.get("periodic_schedule")
        if periodic and periodic.get("enabled"):
            recurrence = str(periodic.get("recurrence", "DAILY")).upper()
            start_d_str = str(periodic.get("start_date") or tests[0].get("exam_date") or calendar_date.today().isoformat())
            end_d_str = str(periodic.get("end_date") or start_d_str)
            try:
                cur_d = calendar_date.fromisoformat(start_d_str)
                end_d = calendar_date.fromisoformat(end_d_str)
                template_test = tests[0]
                tests = []
                step_days = 7 if recurrence == "WEEKLY" else 1
                while cur_d <= end_d and len(tests) < 4:
                    t_copy = dict(template_test)
                    t_copy["exam_date"] = cur_d.isoformat()
                    t_copy["exam_name"] = f"{template_test.get('exam_name')} ({cur_d.strftime('%b %d')})"
                    tests.append(t_copy)
                    cur_d += timedelta(days=step_days)
            except Exception as e:
                logger.warning("Error expanding periodic schedule: %s", e)

        batch_payload = {
            "batch_id": f"EB-{uuid.uuid4().hex[:10].upper()}",
            "account_id": "user_default",
            "tests": tests,
            "source": "EMAIL_PREBOOK_EXTENSION",
            "supporting_documents": booking_details.get("supporting_documents") or [],
        }

        result = db.create_exam_prebooking_batch(batch_payload, device_id=device_id)
        if not result.get("success"):
            raise ValueError(result.get("error") or "Failed to create exam prebooking batch.")

        confirmation = {
            "success": True,
            "booking_id": result.get("batch_id"),
            "category": CAT_EXAM,
            "batch": result,
            "message": f"Successfully prebooked exam batch with {result.get('total_tests')} tests. Attendance excused with full points.",
            "confirmed_at": datetime.now().isoformat(),
        }

    elif category_clean in (CAT_MOVIE, CAT_TRAVEL):
        event_date = str(booking_details.get("date") or booking_details.get("event_date") or calendar_date.today().isoformat())
        start_t = str(booking_details.get("start_time") or "18:00")
        end_t = str(booking_details.get("end_time") or "21:00")

        # Calculate duration
        try:
            s_dt = datetime.strptime(f"{event_date} {start_t}", "%Y-%m-%d %H:%M")
            e_dt = datetime.strptime(f"{event_date} {end_t}", "%Y-%m-%d %H:%M")
            if e_dt <= s_dt:
                e_dt += timedelta(days=1)
            duration_minutes = int((e_dt - s_dt).total_seconds() / 60)
        except Exception:
            duration_minutes = 150

        event_payload = {
            "event_id": f"pre_{uuid.uuid4().hex[:10]}",
            "title": str(booking_details.get("title") or booking_details.get("movie_name") or booking_details.get("purpose") or "Prebooked Event").strip(),
            "category": category_clean,
            "organization": str(booking_details.get("cinema_venue") or booking_details.get("destination") or booking_details.get("organization") or "Event Venue").strip(),
            "event_date": event_date,
            "start_time": start_t,
            "end_time": end_t,
            "expected_duration_minutes": duration_minutes,
            "time_zone": DEFAULT_TIMEZONE,
            "event_mode": "PHYSICAL",
            "venue_or_platform": str(booking_details.get("cinema_venue") or booking_details.get("destination") or "Venue").strip(),
            "reporting_time": start_t,
            "shortlist_status": "VERIFIED_INDIVIDUAL",
            "status": "CONFIRMED",
            "extracted_data": booking_details,
            "evidence_refs": booking_details.get("supporting_documents") or [],
            "mobile_usage_monitoring": True if category_clean == CAT_MOVIE else False,
            "suppress_presence_checks": True if category_clean == CAT_TRAVEL else False,
        }

        created = db.create_official_event_booking(event_payload, device_id=device_id)
        confirmation = {
            "success": True,
            "booking_id": created.get("event_id"),
            "category": category_clean,
            "event": created,
            "message": f"Successfully prebooked {category_clean}: '{event_payload['title']}' on {event_date}.",
            "confirmed_at": datetime.now().isoformat(),
        }

    else:
        raise ValueError(f"Unsupported booking category '{category}'. Must be MOVIE_PREBOOK, EXAM_PREBOOK, or TRAVEL_PREBOOK.")

    if idempotency_key:
        _IDEMPOTENCY_CACHE[idempotency_key] = confirmation

    return confirmation
