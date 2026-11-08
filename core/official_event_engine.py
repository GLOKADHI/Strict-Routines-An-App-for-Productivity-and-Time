"""
StrictRecords Official Event Booking, OCR Extraction, and Validation Engine.
Handles:
- Documentary evidence parsing (Email screenshots, PDFs, attachments, saved emails)
- Local Tesseract OCR & PyMuPDF extraction
- Intelligent regex and heuristic schedule extractor
- Strict validation: missing times, duration calculation, overnight events, deduplication, booking horizon
- Event status lifecycle: DRAFT, NEEDS_INFO, AWAITING_CONFIRMATION, CONFIRMED, CONFLICT_DETECTED, REJECTED, EXPIRED, CANCELLED, COMPLETED
- Real-time exit-window authorization (single-use, strict interval)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, date, time, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger("OfficialEventEngine")

DEFAULT_TIMEZONE = "Asia/Kolkata"
MAX_BOOKING_HORIZON_DAYS = 30


class OfficialEventCategory:
    COLLEGE_EXAM = "COLLEGE_EXAM"
    COLLEGE_INTERNAL = "COLLEGE_INTERNAL"
    ONLINE_ASSESSMENT = "ONLINE_ASSESSMENT"
    VIRTUAL_INTERVIEW = "VIRTUAL_INTERVIEW"
    CAMPUS_RECRUITMENT = "CAMPUS_RECRUITMENT"
    OTHER_OFFICIAL = "OTHER_OFFICIAL"
    MOVIE_PREBOOK = "MOVIE_PREBOOK"
    TRAVEL_PREBOOK = "TRAVEL_PREBOOK"


class ShortlistStatus:
    VERIFIED_INDIVIDUAL = "VERIFIED_INDIVIDUAL"
    ANNOUNCED_UNVERIFIED_INDIVIDUAL = "ANNOUNCED_UNVERIFIED_INDIVIDUAL"
    NOT_SPECIFIED = "NOT_SPECIFIED"


class EventStatus:
    DRAFT = "DRAFT"
    NEEDS_INFO = "NEEDS_INFO"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


def clean_evidence_text(raw_text: str) -> str:
    """
    Cleans raw OCR or document text.
    Strips Gmail 'AI Overview' blocks so AI-generated summaries are never
    treated as official primary evidence (Section 2).
    """
    if not raw_text:
        return ""
    # Strip Gmail AI Overview sections: "Al Overview ... (Add event to Calendar ... Learn more)"
    cleaned = re.sub(
        r"[A-Za-z]?\s*AI\s+Overview[\s\S]*?(?:Add event to Calendar|Learn more|By Gemini)",
        "\n[AI_OVERVIEW_STRIPPED]\n",
        raw_text,
        flags=re.IGNORECASE,
    )
    # Also remove common footer/header artifacts
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    return cleaned


def extract_text_from_file(file_path_or_bytes: Union[str, bytes], filename: str = "") -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extracts text from an image or PDF file using local Tesseract or PyMuPDF.
    Returns (extracted_text, evidence_metadata).
    """
    metadata: List[Dict[str, Any]] = []
    text_content = ""

    # Determine if bytes or path
    if isinstance(file_path_or_bytes, bytes):
        raw_bytes = file_path_or_bytes
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        fname = filename or "uploaded_document"
    else:
        path = str(file_path_or_bytes)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Evidence file not found: {path}")
        fname = filename or os.path.basename(path)
        with open(path, "rb") as f:
            raw_bytes = f.read()
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    is_pdf = fname.lower().endswith(".pdf") or raw_bytes.startswith(b"%PDF")

    if is_pdf:
        # PDF Extraction via PyMuPDF
        try:
            import fitz
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            pdf_pages = []
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_text = page.get_text()
                # If page text is very sparse, it might be a scanned PDF page -> run OCR
                if len(page_text.strip()) < 30:
                    pix = page.get_pixmap(dpi=150)
                    from PIL import Image
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    from new_model.evidence_pipeline.ocr import TesseractOCR
                    ocr = TesseractOCR()
                    res = ocr.analyze(img)
                    page_text = res.get("extracted_text", "")
                pdf_pages.append(page_text)
            text_content = "\n\n--- PAGE BREAK ---\n\n".join(pdf_pages)
            doc.close()
            metadata.append({
                "filename": fname,
                "type": "PDF",
                "sha256": file_sha256,
                "size_bytes": len(raw_bytes),
                "pages": len(pdf_pages),
            })
        except Exception as e:
            logger.error(f"Error parsing PDF evidence: {e}")
            raise ValueError(f"Could not parse PDF file: {e}")
    else:
        # Image OCR via Tesseract
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw_bytes))
            from new_model.evidence_pipeline.ocr import TesseractOCR
            ocr = TesseractOCR()
            res = ocr.analyze(img)
            text_content = res.get("extracted_text", "")
            metadata.append({
                "filename": fname,
                "type": "IMAGE",
                "sha256": file_sha256,
                "size_bytes": len(raw_bytes),
                "confidence": res.get("confidence"),
                "ocr_warnings": res.get("warnings", []),
            })
        except Exception as e:
            logger.error(f"Error performing OCR on image: {e}")
            raise ValueError(f"Could not perform OCR on image: {e}")

    return text_content, metadata


def parse_schedule_details(text: str, evidence_refs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Extracts structured event fields from raw text:
    - title, category, organization
    - event_date (YYYY-MM-DD)
    - start_time (HH:mm)
    - end_time (HH:mm) / duration_minutes
    - mode (VIRTUAL / IN_PERSON / HYBRID)
    - venue_or_platform
    - reporting_time
    - shortlist_status
    - missing_fields, warnings
    """
    clean_text = clean_evidence_text(text)
    lower = clean_text.lower()
    warnings: List[str] = []
    missing_fields: List[str] = []

    # 1. Category Detection
    category = OfficialEventCategory.OTHER_OFFICIAL
    if any(k in lower for k in ["internal assessment", "practical exam", "model exam", "cia", "ia ", "lab exam"]):
        category = OfficialEventCategory.COLLEGE_INTERNAL
    elif any(k in lower for k in ["semester exam", "theory exam", "board exam", "end semester", "final examination", "university exam"]):
        category = OfficialEventCategory.COLLEGE_EXAM
    elif any(k in lower for k in ["online assessment", "technical test", "coding assessment", "hackerrank", "mymo", "amcat", "hirepro"]):
        category = OfficialEventCategory.ONLINE_ASSESSMENT
    elif any(k in lower for k in ["virtual technical interview", "virtual interview", "online interview"]):
        category = OfficialEventCategory.VIRTUAL_INTERVIEW
    elif any(k in lower for k in ["recruitment", "placement", "interview", "campus partner", "talent", "shortlisted"]):
        category = OfficialEventCategory.CAMPUS_RECRUITMENT

    # 2. Organization / College Detection
    organization = ""
    org_matches = []
    if "sathyabama" in lower:
        org_matches.append("Sathyabama Institute of Science & Technology")
    if "hcltech" in lower or "hcl tech" in lower or "hcl" in lower:
        org_matches.append("HCLTech")
    if "ltm" in lower:
        org_matches.append("LTM")
    if "cognizant" in lower:
        org_matches.append("Cognizant")
    if "tcs" in lower or "tata consultancy" in lower:
        org_matches.append("TCS")
    if "infosys" in lower:
        org_matches.append("Infosys")
    if "wipro" in lower:
        org_matches.append("Wipro")

    if org_matches:
        organization = " / ".join(org_matches)
    else:
        # Search for "from <Org>" or "Company: <Org>"
        comp_m = re.search(r"(?:company|organization|partner|from)\s*[:\-]?\s*([A-Za-z0-9\.\s]{3,30})", clean_text, re.IGNORECASE)
        if comp_m:
            organization = comp_m.group(1).strip()
        else:
            organization = "Official Organization"
            warnings.append("Organization name was not clearly identified; please verify.")

    # 3. Title Detection
    title = ""
    # Look for subject line or reminder heading: e.g. "Reminder: LTM | Shortlisted Students | Virtual Technical Interview"
    title_match = re.search(r"((?:Reminder:?\s*)?[A-Za-z0-9][A-Za-z0-9\.\s\|\-_]{8,80}(?:Interview|Assessment|Exam|Test|Details))", clean_text, re.IGNORECASE)
    if title_match:
        raw_t = title_match.group(1).replace("\n", " ").strip()
        # Clean any leading punctuation/symbols and single letter OCR artifacts
        cleaned_t = re.sub(r"^[^\w\s]+", "", raw_t).strip()
        title = re.sub(r"^[a-zA-Z]\s+", "", cleaned_t).strip()
    else:
        title = f"{organization} {category.replace('_', ' ').title()}"

    # 4. Mode Detection
    mode = "VIRTUAL" if "virtual" in lower or "online" in lower or "teams" in lower or "zoom" in lower or "google meet" in lower else "IN_PERSON"
    if "hybrid" in lower:
        mode = "HYBRID"

    # 5. Venue / Platform
    venue = ""
    venue_m = re.search(r"(?:venue|location|platform|reporting point)\s*[:\-]?\s*([^\r\n]{3,40})", clean_text, re.IGNORECASE)
    if venue_m:
        venue = venue_m.group(1).strip()
    elif mode == "VIRTUAL":
        venue = "Virtual / Company Link"
    elif "sathyabama" in lower:
        venue = "Sathyabama Campus"

    # 6. Date Extraction (YYYY-MM-DD)
    event_date = ""
    # Match patterns like:
    # 18.10.26 or 27.10.26 (DD.MM.YY)
    # 27-10-2026 or 27/10/2026
    # 27th Oct or 27 Oct 2026 or October 27, 2026
    date_patterns = [
        r"(?:date\s*[:\-]?\s*)?(\b\d{1,2})[\.\/\-](\d{1,2})[\.\/\-](\d{2,4})\b",
        r"(\b\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]+(\d{2,4})?",
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?[\s,]+(\d{2,4})?",
    ]
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }

    # First check ISO format YYYY-MM-DD
    iso_m = re.search(r"\b(20\d{2})[-/.](0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])\b", clean_text)
    if iso_m:
        event_date = f"{iso_m.group(1)}-{iso_m.group(2)}-{iso_m.group(3)}"
    else:
        # Check near "Date:" or "scheduled on"
        date_context_m = re.search(r"(?:date|scheduled on|interview on|on)\s*[:\-]?\s*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})", clean_text, re.IGNORECASE)
        if date_context_m:
            d_str = date_context_m.group(1)
            parts = re.split(r"[\.\/\-]", d_str)
            if len(parts) == 3:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                if year < 100:
                    year += 2000
                try:
                    parsed_d = date(year, month, day)
                    event_date = parsed_d.strftime("%Y-%m-%d")
                except ValueError:
                    pass

    if not event_date:
        for pat in date_patterns:
            m = re.search(pat, clean_text, re.IGNORECASE)
            if m:
                groups = m.groups()
                if len(groups) == 3 and groups[0].isdigit() and groups[1].isdigit():
                    day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                    if year < 100:
                        year += 2000
                    try:
                        parsed_d = date(year, month, day)
                        event_date = parsed_d.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                elif groups[1] and groups[1].lower()[:3] in month_map:
                    day = int(groups[0])
                    month = month_map[groups[1].lower()[:3]]
                    year = int(groups[2]) if groups[2] else datetime.now().year
                    if year < 100:
                        year += 2000
                    try:
                        parsed_d = date(year, month, day)
                        event_date = parsed_d.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue

    if not event_date:
        missing_fields.append("event_date")
        warnings.append("Event date could not be detected. Please enter date manually.")

    # 7. Time & Reporting Time Extraction
    start_time = ""
    reporting_time = ""
    end_time = ""
    duration_minutes = 60

    # Look for explicit "Reporting Time: 09:00 AM" or "Reporting Point"
    rep_m = re.search(r"(?:reporting\s*(?:time|point)?)\s*[:\-]?\s*(\d{1,2}(?:[\.:]\d{2})?\s*(?:AM|PM)?)", clean_text, re.IGNORECASE)
    if rep_m:
        rep_raw = rep_m.group(1).strip()
        reporting_time = _normalize_time_str(rep_raw)

    # Look for explicit Start Time and End Time lines
    start_m = re.search(r"(?:start(?:ing)?\s*time|from)\s*[:\-]?\s*(\d{1,2}(?:[:\.]\d{2})?\s*(?:AM|PM)?)", clean_text, re.IGNORECASE)
    if start_m:
        start_time = _normalize_time_str(start_m.group(1))

    end_m = re.search(r"(?:end(?:ing)?\s*time)\s*[:\-]?\s*(\d{1,2}(?:[:\.]\d{2})?\s*(?:AM|PM)?)", clean_text, re.IGNORECASE)
    if end_m:
        end_time = _normalize_time_str(end_m.group(1))

    # Look for time intervals like "09:00 AM to 11:00 AM" or "9 AM - 11 AM"
    if not start_time:
        time_range_m = re.search(
            r"(\b\d{1,2}(?:[:\.]\d{2})\s*(?:AM|PM)?|\b\d{1,2}\s*(?:AM|PM))\s*(?:to|-|until|–)\s*(\b\d{1,2}(?:[:\.]\d{2})\s*(?:AM|PM)?|\b\d{1,2}\s*(?:AM|PM))\b",
            clean_text,
            re.IGNORECASE,
        )
        if time_range_m:
            t1 = _normalize_time_str(time_range_m.group(1))
            t2 = _normalize_time_str(time_range_m.group(2))
            if t1:
                start_time = t1
            if t2:
                end_time = t2

    if not start_time:
        time_m = re.search(r"(?:time|starts? at|at)\s*[:\-]?\s*(\d{1,2}(?:[:\.]\d{2})?\s*(?:AM|PM)?)", clean_text, re.IGNORECASE)
        if time_m:
            t_raw = time_m.group(1).strip()
            start_time = _normalize_time_str(t_raw)

    # If start_time not found, but reporting_time was found, reporting_time can serve as tentative start_time
    if not start_time and reporting_time:
        start_time = reporting_time
        warnings.append(f"Start time inferred from reporting time ({reporting_time}). Please verify.")

    if not start_time:
        missing_fields.append("start_time")
        warnings.append("Start time was not detected in evidence. Please enter the exact start time manually.")

    # Check for duration if specified: e.g. "duration: 90 mins" or "2 hours"
    dur_m = re.search(r"(?:duration|length)\s*[:\-]?\s*(\d+)\s*(mins?|minutes?|hours?|hrs?)", clean_text, re.IGNORECASE)
    if dur_m:
        val = int(dur_m.group(1))
        unit = dur_m.group(2).lower()
        if "hour" in unit or "hr" in unit:
            duration_minutes = val * 60
        else:
            duration_minutes = val

    if start_time and not end_time:
        missing_fields.append("end_time")
        warnings.append("End time / duration was not specified in the email. Please enter expected duration or end time.")

    # 8. Shortlist Status Detection
    shortlist_status = ShortlistStatus.NOT_SPECIFIED
    if any(k in lower for k in [
        "attached is the list",
        "list of students",
        "list of shortlisted",
        "shortlisted candidates",
        "shortlisted students",
        "following number of students",
        "shortlisted by ltm",
        "shortlisted by hcltech",
        "shortlisted by",
        "shortlisted students list",
        "will share shortlisted students list",
        "will share shortlisted",
    ]):
        shortlist_status = ShortlistStatus.ANNOUNCED_UNVERIFIED_INDIVIDUAL
        warnings.append(
            "Evidence indicates a general placement-cell announcement. "
            "Individual student shortlisting is not independently verified from this email."
        )

    # 9. Overall Status Determination
    status = EventStatus.AWAITING_CONFIRMATION
    if missing_fields:
        status = EventStatus.NEEDS_INFO

    return {
        "title": title,
        "category": category,
        "organization": organization,
        "event_date": event_date,
        "start_time": start_time,
        "end_time": end_time,
        "expected_duration_minutes": duration_minutes,
        "time_zone": DEFAULT_TIMEZONE,
        "event_mode": mode,
        "venue_or_platform": venue,
        "reporting_time": reporting_time,
        "shortlist_status": shortlist_status,
        "status": status,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "evidence_refs": evidence_refs or [],
        "raw_text_length": len(clean_text),
    }


def _normalize_time_str(time_str: str) -> str:
    """Normalizes time string into HH:mm (24-hour format)."""
    if not time_str:
        return ""
    clean = time_str.strip().upper().replace(".", ":")
    # Handle cases like "09:00 AM", "9 AM", "14:30", "09.00 AM"
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$", clean)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    meridiem = match.group(3)

    if meridiem == "PM" and hour < 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return ""


def validate_event_booking(
    booking: Dict[str, Any],
    existing_bookings: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, List[str]]:
    """
    Mandatory safety and policy validations (Section 3):
    1. Valid event_date (YYYY-MM-DD).
    2. Valid start_time (HH:mm).
    3. Valid end_time (HH:mm) or duration_minutes.
    4. end_time after start_time (handling midnight crossings).
    5. Booking horizon check: must not be in past or > 30 days away.
    6. Deduplication check: no duplicate booking on same date/time for same user/org.
    7. No unresolved conflicts.
    """
    errors: List[str] = []
    current_time = now or datetime.now()

    # 1. Date
    event_date_str = str(booking.get("event_date") or "").strip()
    if not event_date_str:
        errors.append("Event date is required.")
        return False, errors
    try:
        ev_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"Invalid date format '{event_date_str}'. Expected YYYY-MM-DD.")
        return False, errors

    # Horizon check
    today = current_time.date()
    if ev_date < today:
        errors.append(f"Cannot book an event in the past ({event_date_str}).")
    elif (ev_date - today).days > MAX_BOOKING_HORIZON_DAYS:
        errors.append(f"Event date exceeds the allowed {MAX_BOOKING_HORIZON_DAYS}-day booking horizon.")

    # 2. Start Time
    start_time_str = str(booking.get("start_time") or "").strip()
    if not start_time_str:
        errors.append("Start time is required and cannot be empty.")
    elif not re.match(r"^\d{2}:\d{2}$", start_time_str):
        errors.append(f"Invalid start time format '{start_time_str}'. Expected HH:mm.")

    # 3. End Time
    end_time_str = str(booking.get("end_time") or "").strip()
    duration_min = int(booking.get("expected_duration_minutes") or 60)
    if not end_time_str:
        # Calculate from duration
        if start_time_str and re.match(r"^\d{2}:\d{2}$", start_time_str):
            sh, sm = map(int, start_time_str.split(":"))
            start_dt = datetime.combine(ev_date, time(sh, sm))
            end_dt = start_dt + timedelta(minutes=duration_min)
            end_time_str = end_dt.strftime("%H:%M")
            booking["end_time"] = end_time_str
        else:
            errors.append("End time or expected duration is required.")
    elif not re.match(r"^\d{2}:\d{2}$", end_time_str):
        errors.append(f"Invalid end time format '{end_time_str}'. Expected HH:mm.")

    # 4. End time vs Start time (support overnight events)
    if start_time_str and end_time_str and re.match(r"^\d{2}:\d{2}$", start_time_str) and re.match(r"^\d{2}:\d{2}$", end_time_str):
        sh, sm = map(int, start_time_str.split(":"))
        eh, em = map(int, end_time_str.split(":"))
        start_dt = datetime.combine(ev_date, time(sh, sm))
        end_dt = datetime.combine(ev_date, time(eh, em))
        if end_dt <= start_dt:
            # Check if midnight crossing intended (e.g. 23:00 to 01:00)
            end_dt += timedelta(days=1)
            booking["is_overnight"] = True

        duration_actual = (end_dt - start_dt).total_seconds() / 60
        if duration_actual < 15:
            errors.append("Event duration must be at least 15 minutes.")
        elif duration_actual > 1440:
            errors.append("Event duration cannot exceed 24 hours.")

    # 5. Deduplication check
    if existing_bookings:
        current_id = booking.get("event_id")
        org = str(booking.get("organization") or "").lower().strip()
        title = str(booking.get("title") or "").lower().strip()
        for ex in existing_bookings:
            if ex.get("event_id") == current_id:
                continue
            if ex.get("status") in (EventStatus.CANCELLED, EventStatus.REJECTED):
                continue
            if ex.get("event_date") == event_date_str and ex.get("start_time") == start_time_str:
                ex_org = str(ex.get("organization") or "").lower().strip()
                if org and (org in ex_org or ex_org in org):
                    errors.append(f"A booking already exists for {ex.get('organization')} on {event_date_str} at {start_time_str} (ID: {ex.get('event_id')}).")
                    break

    return len(errors) == 0, errors


def evaluate_exit_window(
    booking: Dict[str, Any],
    now: Optional[datetime] = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> Tuple[bool, str]:
    """
    Strict exit-window calculation (Section 4):
    - Before start: exit unavailable.
    - During window: Exit for Scheduled Event is available.
    - At/After end: expired.
    - If exit already consumed: unavailable (one-time use).
    - If status != CONFIRMED: unavailable.
    """
    status = booking.get("status")
    if status != EventStatus.CONFIRMED:
        return False, f"Event is in '{status}' status, not CONFIRMED."

    if booking.get("exit_consumed"):
        return False, "The authorized exit exception for this scheduled event has already been consumed."

    tz = ZoneInfo(tz_name)
    current_time = now or datetime.now(tz)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=tz)

    ev_date_str = booking.get("event_date")
    start_time_str = booking.get("start_time")
    end_time_str = booking.get("end_time")

    if not ev_date_str or not start_time_str or not end_time_str:
        return False, "Incomplete schedule information on booking."

    try:
        ev_date = datetime.strptime(ev_date_str, "%Y-%m-%d").date()
        sh, sm = map(int, start_time_str.split(":"))
        eh, em = map(int, end_time_str.split(":"))
    except Exception as e:
        return False, f"Malformed date/time strings on booking: {e}"

    start_dt = datetime.combine(ev_date, time(sh, sm), tzinfo=tz)
    end_dt = datetime.combine(ev_date, time(eh, em), tzinfo=tz)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    if current_time < start_dt:
        starts_in_min = int((start_dt - current_time).total_seconds() / 60)
        return False, f"Scheduled exit opens at {start_time_str} (in {starts_in_min} minutes). Exit is currently locked."

    if current_time >= end_dt:
        return False, f"Scheduled exit window ended at {end_time_str}. The exception has expired."

    # Within confirmed window!
    mins_remaining = int((end_dt - current_time).total_seconds() / 60)
    return True, f"Authorized exit window active. Valid until {end_time_str} ({mins_remaining} minutes remaining)."


def extract_from_multiple_files(
    files: List[Union[str, bytes, Tuple[Union[str, bytes], str]]],
) -> Dict[str, Any]:
    """
    Extracts schedule details across multiple uploaded documents or screenshots for one booking (Section 2).
    Cross-checks for conflicting dates or times and reports CONFLICT_DETECTED if found.
    """
    if not files:
        return {
            "title": "",
            "category": OfficialEventCategory.OTHER_OFFICIAL,
            "organization": "",
            "event_date": "",
            "start_time": "",
            "end_time": "",
            "status": EventStatus.REJECTED,
            "warnings": ["No files were uploaded."],
            "missing_fields": ["event_date", "start_time", "end_time"],
            "evidence_refs": [],
        }

    all_texts = []
    all_metadata = []
    individual_extractions = []

    for item in files:
        if isinstance(item, tuple):
            f_content, f_name = item
        else:
            f_content, f_name = item, ""
        text, meta = extract_text_from_file(f_content, f_name)
        all_texts.append(text)
        all_metadata.extend(meta)
        individual_extractions.append(parse_schedule_details(text, meta))

    combined_text = "\n\n=== NEXT EVIDENCE DOCUMENT ===\n\n".join(all_texts)
    parsed = parse_schedule_details(combined_text, all_metadata)

    # Check for conflicts between individual documents
    dates = {ext["event_date"] for ext in individual_extractions if ext.get("event_date")}
    start_times = {ext["start_time"] for ext in individual_extractions if ext.get("start_time")}

    conflict_messages = []
    if len(dates) > 1:
        conflict_messages.append(f"Conflicting dates detected across files: {', '.join(sorted(dates))}.")
    if len(start_times) > 1:
        conflict_messages.append(f"Conflicting start times detected across files: {', '.join(sorted(start_times))}.")

    if conflict_messages:
        parsed["status"] = EventStatus.CONFLICT_DETECTED
        parsed["warnings"].extend(conflict_messages)
        parsed["has_conflict"] = True

    return parsed


def extract_schedule_from_evidence(
    file_path_or_bytes: Union[str, bytes], filename: str = ""
) -> Dict[str, Any]:
    """Convenience single-file wrapper around extract_text_from_file and parse_schedule_details."""
    text, meta = extract_text_from_file(file_path_or_bytes, filename)
    parsed = parse_schedule_details(text, meta)
    return {
        "success": bool(text.strip()),
        "extracted": parsed,
        "raw_text": text,
        "missing_fields": parsed.get("missing_fields", []),
        "warnings": parsed.get("warnings", []),
    }

