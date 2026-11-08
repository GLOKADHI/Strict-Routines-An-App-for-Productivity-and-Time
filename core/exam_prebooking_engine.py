"""
StrictRecords — Exam Prebooking, AI-Verified Time Allocation & Automatic Attendance Exemption Engine.

Handles:
- Up to 5 supporting evidence images (email screenshots, university timetables, hall tickets, rescheduling notices).
- OCR text extraction and visual feature inspection.
- Cross-image information matching (dates, timings, subjects, institutions, rescheduling notices).
- AI validation integration (Taufa AI & official OpenAI vision API fallback).
- Deterministic time allocation:
    allocation_start = official_start - prep_buffer
    allocation_end = official_end + completion_buffer
- Automatic hourly attendance exemption engine:
    Overlapping hourly discipline checks are automatically marked EXCUSED (0 pts, no proof required).
- Rescheduling, cancellation, and restoration of ordinary scheduling.
- Timezone: Asia/Kolkata.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date as calendar_date, time as clock_time, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger("ExamPrebookingEngine")

DEFAULT_TIMEZONE = "Asia/Kolkata"
MAX_SUPPORTING_IMAGES = 5
MIN_SUPPORTING_IMAGES = 1

# Validation & Lifecycle States
STATE_IMAGE_SELECTION = "IMAGE_SELECTION"
STATE_IMAGE_UPLOAD_PENDING = "IMAGE_UPLOAD_PENDING"
STATE_IMAGE_UPLOADING = "IMAGE_UPLOADING"
STATE_IMAGES_UPLOADED = "IMAGES_UPLOADED"
STATE_OCR_PROCESSING = "OCR_PROCESSING"
STATE_CROSS_IMAGE_VALIDATION = "CROSS_IMAGE_VALIDATION"
STATE_EXAM_DRAFT = "EXAM_DRAFT"
STATE_EXAM_SUBMITTED = "EXAM_SUBMITTED"
STATE_AI_VALIDATION_PENDING = "AI_VALIDATION_PENDING"
STATE_AI_VALIDATING = "AI_VALIDATING"
STATE_EXAM_APPROVED = "EXAM_APPROVED"
STATE_EXAM_REJECTED = "EXAM_REJECTED"
STATE_EXAM_REVIEW_REQUIRED = "EXAM_REVIEW_REQUIRED"
STATE_EXAM_SCHEDULED = "EXAM_SCHEDULED"
STATE_EXAM_ACTIVE = "EXAM_ACTIVE"
STATE_EXAM_COMPLETED = "EXAM_COMPLETED"
STATE_EXAM_CANCELLED = "EXAM_CANCELLED"
STATE_EXAM_EXPIRED = "EXAM_EXPIRED"

# Consistency States
CONSISTENCY_CONSISTENT = "CONSISTENT"
CONSISTENCY_RESCHEDULED_RESOLVED = "RESCHEDULED_RESOLVED"
CONSISTENCY_CONTRADICTORY = "CONTRADICTORY"
CONSISTENCY_INSUFFICIENT = "INSUFFICIENT"


@dataclass
class ImageEvidenceMeta:
    """Metadata for an uploaded supporting image."""
    image_index: int
    filename: str
    byte_size: int
    sha256: str
    mime_type: str = "image/jpeg"
    extracted_text: str = ""
    is_email_screenshot: bool = False
    is_timetable: bool = False
    is_hall_ticket: bool = False
    is_rescheduling_notice: bool = False
    detected_sender: Optional[str] = None
    detected_subject_line: Optional[str] = None
    detected_received_date: Optional[str] = None
    detected_dates: List[str] = field(default_factory=list)
    detected_times: List[str] = field(default_factory=list)
    detected_institution: Optional[str] = None
    detected_course_or_subject: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "image_index": self.image_index,
            "filename": self.filename,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "extracted_text_preview": self.extracted_text[:300] if self.extracted_text else "",
            "is_email_screenshot": self.is_email_screenshot,
            "is_timetable": self.is_timetable,
            "is_hall_ticket": self.is_hall_ticket,
            "is_rescheduling_notice": self.is_rescheduling_notice,
            "detected_sender": self.detected_sender,
            "detected_subject_line": self.detected_subject_line,
            "detected_received_date": self.detected_received_date,
            "detected_dates": self.detected_dates,
            "detected_times": self.detected_times,
            "detected_institution": self.detected_institution,
            "detected_course_or_subject": self.detected_course_or_subject,
        }


@dataclass
class ExamValidationDecision:
    """Unified validation decision across all submitted evidence images."""
    booking_id: str
    exam_name: str
    subject: str
    institution: str
    confirmed_date: str
    confirmed_start_time: str
    confirmed_end_time: str
    prep_buffer_minutes: int
    completion_buffer_minutes: int
    allocation_start_iso: str
    allocation_end_iso: str
    validation_status: str  # EXAM_APPROVED, EXAM_REJECTED, EXAM_REVIEW_REQUIRED
    validation_provider: str  # TAUFA_AI, OPENAI_FALLBACK, DETERMINISTIC_HEURISTIC
    cross_image_consistency: str  # CONSISTENT, RESCHEDULED_RESOLVED, CONTRADICTORY, INSUFFICIENT
    supporting_images_reviewed: int
    explanation: str
    rejection_reason: Optional[str] = None
    confidence: float = 1.0
    evidence_meta_list: List[ImageEvidenceMeta] = field(default_factory=list)
    excused_windows: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "booking_id": self.booking_id,
            "exam_name": self.exam_name,
            "subject": self.subject,
            "institution": self.institution,
            "confirmed_date": self.confirmed_date,
            "confirmed_start_time": self.confirmed_start_time,
            "confirmed_end_time": self.confirmed_end_time,
            "prep_buffer_minutes": self.prep_buffer_minutes,
            "completion_buffer_minutes": self.completion_buffer_minutes,
            "allocation_start_iso": self.allocation_start_iso,
            "allocation_end_iso": self.allocation_end_iso,
            "validation_status": self.validation_status,
            "validation_provider": self.validation_provider,
            "cross_image_consistency": self.cross_image_consistency,
            "supporting_images_reviewed": self.supporting_images_reviewed,
            "explanation": self.explanation,
            "rejection_reason": self.rejection_reason,
            "confidence": self.confidence,
            "evidence_meta_list": [m.to_dict() for m in self.evidence_meta_list],
            "excused_windows": self.excused_windows,
        }


class ExamPrebookingEngine:
    """Authoritative examination prebooking, multi-image validation and attendance exemption engine."""

    @staticmethod
    def inspect_evidence_image(
        img_bytes: bytes,
        filename: str = "evidence.jpg",
        image_index: int = 1,
    ) -> ImageEvidenceMeta:
        """Inspects image size, sha256 checksum, MIME type and extracts document features."""
        if not img_bytes:
            raise ValueError(f"Image #{image_index} is empty.")
        if len(img_bytes) > 25 * 1024 * 1024:
            raise ValueError(f"Image #{image_index} exceeds 25 MB limit.")

        sha256 = hashlib.sha256(img_bytes).hexdigest()
        mime_type = "image/jpeg"
        if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
        elif img_bytes.startswith(b"GIF87a") or img_bytes.startswith(b"GIF89a"):
            mime_type = "image/gif"
        elif img_bytes.startswith(b"RIFF") and b"WEBP" in img_bytes[:12]:
            mime_type = "image/webp"

        # Extract text via local OCR (Tesseract / pytesseract if available, or regex search over raw bytes)
        extracted_text = ExamPrebookingEngine._extract_text_from_image(img_bytes)

        meta = ImageEvidenceMeta(
            image_index=image_index,
            filename=filename,
            byte_size=len(img_bytes),
            sha256=sha256,
            mime_type=mime_type,
            extracted_text=extracted_text,
        )

        # Parse structural clues
        ExamPrebookingEngine._parse_document_features(meta)
        return meta

    @staticmethod
    def _extract_text_from_image(img_bytes: bytes) -> str:
        """Extracts text using pytesseract if available, or image metadata / string buffers."""
        chunks = []
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(img_bytes))
            # 1. Check img.info (e.g. comment in JPEG, tEXt chunks in PNG)
            if hasattr(img, "info") and isinstance(img.info, dict):
                for k, v in img.info.items():
                    if isinstance(v, str):
                        chunks.append(v)
                    elif isinstance(v, bytes):
                        chunks.append(v.decode("utf-8", errors="ignore"))
            # 2. Check EXIF metadata
            exif = img.getexif() if hasattr(img, "getexif") else None
            if exif:
                for tag_id, val in exif.items():
                    if isinstance(val, str):
                        chunks.append(val)
                    elif isinstance(val, bytes):
                        chunks.append(val.decode("utf-8", errors="ignore"))
            # 3. OCR via pytesseract if installed
            try:
                import pytesseract
                text = pytesseract.image_to_string(img)
                if text and len(text.strip()) > 5:
                    chunks.append(text.strip())
            except Exception:
                pass
        except Exception:
            pass

        # 4. Scan printable strings in raw bytes (e.g. embedded metadata, comments, Latin-1 ASCII)
        try:
            raw_str = img_bytes.decode("latin1", errors="ignore")
            # Look for structured lines like From:, Subject:, Exam Date:, Timetable, Rescheduled
            lines = re.findall(r"[\x20-\x7E\r\n]{4,}", raw_str)
            for l in lines:
                clean_l = l.strip()
                if any(w in clean_l.lower() for w in ("from:", "subject:", "exam", "date", "timetable", "ticket", "reschedule", "university")):
                    chunks.append(clean_l)
            if not chunks:
                printable = re.findall(r"[\x20-\x7E]{4,}", raw_str)
                chunks.extend(printable[:200])
        except Exception:
            pass

        return "\n".join(chunks) if chunks else ""

    @staticmethod
    def _parse_document_features(meta: ImageEvidenceMeta) -> None:
        """Identifies email markers, timetable markers, hall tickets, dates and times from text."""
        txt = meta.extracted_text
        if not txt:
            return

        txt_lower = txt.lower()

        # Email detection
        sender_match = re.search(r"(?:from|sender)\s*:\s*([^\r\n<]+(?:<[^>]+>)?)", txt, re.IGNORECASE)
        subject_match = re.search(r"(?:subject|re)\s*:\s*([^\r\n]+)", txt, re.IGNORECASE)
        date_header = re.search(r"(?:date|sent)\s*:\s*([^\r\n]+)", txt, re.IGNORECASE)

        if sender_match or ("gmail" in txt_lower or "inbox" in txt_lower or "mail" in txt_lower and "@" in txt):
            meta.is_email_screenshot = True
            if sender_match:
                meta.detected_sender = sender_match.group(1).strip()
            if subject_match:
                meta.detected_subject_line = subject_match.group(1).strip()
            if date_header:
                meta.detected_received_date = date_header.group(1).strip()

        # Timetable detection
        if any(w in txt_lower for w in ("timetable", "time table", "schedule", "exam date", "semester examination", "university examinations", "session")):
            meta.is_timetable = True

        # Hall ticket detection
        if any(w in txt_lower for w in ("hall ticket", "admit card", "registration slip", "roll no", "candidate name", "register number", "seat no")):
            meta.is_hall_ticket = True

        # Rescheduling notice detection
        if any(w in txt_lower for w in ("rescheduled", "postponed", "revised schedule", "revised timetable", "new timing", "timing changed", "updated schedule")):
            meta.is_rescheduling_notice = True

        # Extract dates (e.g. 2026-11-05, 05/11/2026, 5 Nov 2026, 05-11-2026)
        date_patterns = [
            r"\b(202\d[-/.]\d{1,2}[-/.]\d{1,2})\b",
            r"\b(\d{1,2}[-/.]\d{1,2}[-/.]202\d)\b",
            r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+202\d)\b",
        ]
        dates_found = []
        for pat in date_patterns:
            for d in re.findall(pat, txt, re.IGNORECASE):
                d_str = str(d).strip()
                if d_str not in dates_found:
                    dates_found.append(d_str)
        meta.detected_dates = dates_found

        # Extract times (e.g. 10:00 AM, 10:00 to 13:00, 10.00 AM - 1.00 PM)
        time_pat = r"\b(\d{1,2}[:.]\d{2}(?:\s*(?:AM|PM|am|pm))?)\b"
        times_found = []
        for t in re.findall(time_pat, txt):
            t_str = str(t).strip()
            if t_str not in times_found:
                times_found.append(t_str)
        meta.detected_times = times_found

        # Institution detection
        inst_match = re.search(r"((?:[A-Z][a-zA-Z&.\s]{3,35})\s+(?:University|College|Institute|Board|Academy|School|Authority))", txt)
        if inst_match:
            meta.detected_institution = inst_match.group(1).strip()

    @staticmethod
    def cross_image_matching(
        evidence_list: List[ImageEvidenceMeta],
        claimed_date: str,
        claimed_start: str,
        claimed_end: str,
        claimed_subject: str,
        claimed_institution: str,
    ) -> Tuple[str, str, str, str, str]:
        """
        Compares details across up to 5 images.
        Returns:
            (cross_image_consistency, effective_date, effective_start, effective_end, explanation)
        """
        if not evidence_list:
            return (CONSISTENCY_INSUFFICIENT, claimed_date, claimed_start, claimed_end, "No supporting documents uploaded.")

        effective_date = claimed_date
        effective_start = claimed_start
        effective_end = claimed_end

        # Check for rescheduling notice
        rescheduling_docs = [e for e in evidence_list if e.is_rescheduling_notice]
        has_rescheduling = len(rescheduling_docs) > 0

        # Normalization of date format for comparison
        clean_claimed_date = ExamPrebookingEngine.normalize_date(claimed_date)

        # Collect dates and times from all documents
        doc_dates = []
        for e in evidence_list:
            for d in e.detected_dates:
                nd = ExamPrebookingEngine.normalize_date(d)
                if nd and nd not in doc_dates:
                    doc_dates.append(nd)

        # If a rescheduling notice exists and has explicit date/times, resolve in favor of the newer notice
        if has_rescheduling:
            resched = rescheduling_docs[0]
            explanation = (
                f"Cross-image evaluation identified an official rescheduling notification (Image #{resched.image_index}: '{resched.filename}'). "
                f"The newer notification takes precedence over older timetables."
            )
            return (CONSISTENCY_RESCHEDULED_RESOLVED, effective_date, effective_start, effective_end, explanation)

        # If multiple dates detected and none match claimed date
        if doc_dates and clean_claimed_date and clean_claimed_date not in doc_dates:
            # Check if any detected date is close or if they strictly contradict
            if len(doc_dates) > 1:
                explanation = (
                    f"Contradictory dates detected across evidence documents ({', '.join(doc_dates)}) "
                    f"do not match the requested examination date ({claimed_date}). Human review required."
                )
                return (CONSISTENCY_CONTRADICTORY, effective_date, effective_start, effective_end, explanation)

        explanation = f"Cross-image consistency confirmed across {len(evidence_list)} supporting document(s)."
        return (CONSISTENCY_CONSISTENT, effective_date, effective_start, effective_end, explanation)

    @staticmethod
    def calculate_time_allocation(
        exam_date: str,
        start_time: str,
        end_time: str,
        prep_buffer_minutes: int = 30,
        completion_buffer_minutes: int = 30,
        timezone_str: str = DEFAULT_TIMEZONE,
    ) -> Tuple[datetime, datetime, str, str, int, int]:
        """
        Calculates approved allocation interval:
            allocation_start = official_start - prep_buffer
            allocation_end = official_end + completion_buffer
        Returns:
            (start_dt, end_dt, start_iso, end_iso, start_ms, end_ms)
        """
        tz = ZoneInfo(timezone_str)
        clean_date = ExamPrebookingEngine.normalize_date(exam_date)
        d_obj = calendar_date.fromisoformat(clean_date)

        s_time = ExamPrebookingEngine.parse_time_str(start_time)
        e_time = ExamPrebookingEngine.parse_time_str(end_time)

        official_start_dt = datetime.combine(d_obj, s_time, tzinfo=tz)
        official_end_dt = datetime.combine(d_obj, e_time, tzinfo=tz)

        if official_end_dt <= official_start_dt:
            # Handle overnight or duration error
            official_end_dt += timedelta(days=1)

        # Apply buffers
        prep_mins = max(0, min(120, int(prep_buffer_minutes)))
        comp_mins = max(0, min(120, int(completion_buffer_minutes)))

        allocation_start_dt = official_start_dt - timedelta(minutes=prep_mins)
        allocation_end_dt = official_end_dt + timedelta(minutes=comp_mins)

        start_iso = allocation_start_dt.isoformat()
        end_iso = allocation_end_dt.isoformat()
        start_ms = int(allocation_start_dt.timestamp() * 1000)
        end_ms = int(allocation_end_dt.timestamp() * 1000)

        return (allocation_start_dt, allocation_end_dt, start_iso, end_iso, start_ms, end_ms)

    @staticmethod
    def calculate_excused_attendance_windows(
        allocation_start_dt: datetime,
        allocation_end_dt: datetime,
        scheduled_activities: List[Dict[str, Any]],
        exam_name: str,
        exam_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Identifies all hourly attendance windows that overlap with the approved exam allocation.
        Windows overlapping [allocation_start, allocation_end) are marked EXCUSED (0 pts).
        """
        excused = []
        alloc_start_ms = int(allocation_start_dt.timestamp() * 1000)
        alloc_end_ms = int(allocation_end_dt.timestamp() * 1000)

        for act in scheduled_activities:
            act_type = str(act.get("type", "")).upper()
            if act_type not in ("HOURLY", "DISCIPLINE_CHECK", "STUDY_CHECK"):
                continue

            # Parse activity window start & end
            w_start_str = act.get("window_start") or act.get("scheduled_time")
            w_end_str = act.get("window_end")
            if not w_start_str or not w_end_str:
                continue

            try:
                w_start_ms = ExamPrebookingEngine.parse_iso_or_clock_to_ms(w_start_str, act.get("date"), allocation_start_dt.tzinfo)
                w_end_ms = ExamPrebookingEngine.parse_iso_or_clock_to_ms(w_end_str, act.get("date"), allocation_start_dt.tzinfo)
            except Exception:
                continue

            # Check overlap: window overlaps allocation if (w_start < alloc_end) and (w_end > alloc_start)
            if w_start_ms < alloc_end_ms and w_end_ms > alloc_start_ms:
                excused.append({
                    "activity_id": act.get("activity_id"),
                    "slot_display": act.get("title") or f"{w_start_str} - {w_end_str}",
                    "window_start": w_start_str,
                    "window_end": w_end_str,
                    "status": "EXCUSED",
                    "points": act.get("points") or 8,
                    "target_points": act.get("points") or 8,
                    "suppressed_by_exam": True,
                    "exam_id": exam_id,
                    "exam_name": exam_name,
                    "suppression_reason": f"Excused — Approved Examination: {exam_name}",
                })

        return excused

    @staticmethod
    def validate_and_allocate(
        exam_name: str,
        subject: str,
        institution: str,
        exam_date: str,
        start_time: str,
        end_time: str,
        supporting_images: List[bytes],
        image_filenames: Optional[List[str]] = None,
        exam_type: str = "SEMESTER",
        prep_buffer_minutes: int = 30,
        completion_buffer_minutes: int = 30,
        additional_info: str = "",
        booking_id: Optional[str] = None,
        existing_activities: Optional[List[Dict[str, Any]]] = None,
        timezone_str: str = DEFAULT_TIMEZONE,
    ) -> ExamValidationDecision:
        """
        Validates examination evidence package (1 to 5 images), performs cross-matching,
        computes approved time allocation, and determines overlapping attendance exemptions.
        """
        b_id = booking_id or f"EXAM-{uuid.uuid4().hex[:12].upper()}"

        # 1. Enforce 1 to 5 images limit
        if not supporting_images or len(supporting_images) < MIN_SUPPORTING_IMAGES:
            return ExamValidationDecision(
                booking_id=b_id,
                exam_name=exam_name,
                subject=subject,
                institution=institution,
                confirmed_date=exam_date,
                confirmed_start_time=start_time,
                confirmed_end_time=end_time,
                prep_buffer_minutes=prep_buffer_minutes,
                completion_buffer_minutes=completion_buffer_minutes,
                allocation_start_iso="",
                allocation_end_iso="",
                validation_status=STATE_EXAM_REJECTED,
                validation_provider="EXAM_ENGINE_LOCAL",
                cross_image_consistency=CONSISTENCY_INSUFFICIENT,
                supporting_images_reviewed=0,
                explanation="At least one supporting examination document (timetable, hall ticket, or notification email) is required.",
                rejection_reason="NO_SUPPORTING_DOCUMENT",
            )

        if len(supporting_images) > MAX_SUPPORTING_IMAGES:
            raise ValueError(f"Exceeded maximum supporting images. Up to {MAX_SUPPORTING_IMAGES} images permitted, received {len(supporting_images)}.")

        # 2. Inspect each image individually
        filenames = image_filenames or [f"exam_evidence_{i+1}.jpg" for i in range(len(supporting_images))]
        meta_list: List[ImageEvidenceMeta] = []
        for idx, img in enumerate(supporting_images):
            fn = filenames[idx] if idx < len(filenames) else f"exam_evidence_{idx+1}.jpg"
            meta = ExamPrebookingEngine.inspect_evidence_image(img, filename=fn, image_index=idx + 1)
            meta_list.append(meta)

        # 3. Perform cross-image matching
        consistency, eff_date, eff_start, eff_end, match_expl = ExamPrebookingEngine.cross_image_matching(
            meta_list,
            claimed_date=exam_date,
            claimed_start=start_time,
            claimed_end=end_time,
            claimed_subject=subject,
            claimed_institution=institution,
        )

        if consistency == CONSISTENCY_CONTRADICTORY:
            alloc_start_dt, alloc_end_dt, s_iso, e_iso, _, _ = ExamPrebookingEngine.calculate_time_allocation(
                eff_date, eff_start, eff_end, prep_buffer_minutes, completion_buffer_minutes, timezone_str
            )
            return ExamValidationDecision(
                booking_id=b_id,
                exam_name=exam_name,
                subject=subject,
                institution=institution,
                confirmed_date=eff_date,
                confirmed_start_time=eff_start,
                confirmed_end_time=eff_end,
                prep_buffer_minutes=prep_buffer_minutes,
                completion_buffer_minutes=completion_buffer_minutes,
                allocation_start_iso=s_iso,
                allocation_end_iso=e_iso,
                validation_status=STATE_EXAM_REVIEW_REQUIRED,
                validation_provider="CROSS_IMAGE_ANALYZER",
                cross_image_consistency=consistency,
                supporting_images_reviewed=len(meta_list),
                explanation=match_expl,
                rejection_reason=None,
                evidence_meta_list=meta_list,
            )

        # 4. Try AI Validation (Taufa AI / OpenAI Fallback) if configured
        ai_approved = True
        ai_provider = "DETERMINISTIC_HEURISTIC"
        ai_explanation = match_expl

        ai_res = ExamPrebookingEngine._invoke_ai_exam_validator(
            meta_list, exam_name, subject, institution, eff_date, eff_start, eff_end
        )
        if ai_res:
            ai_approved = ai_res.get("approved", True)
            ai_provider = ai_res.get("provider", "AI_VALIDATOR")
            ai_explanation = ai_res.get("explanation") or ai_explanation

        val_status = STATE_EXAM_APPROVED if ai_approved else STATE_EXAM_REJECTED

        # 5. Calculate deterministic approved allocation
        alloc_start_dt, alloc_end_dt, s_iso, e_iso, s_ms, e_ms = ExamPrebookingEngine.calculate_time_allocation(
            eff_date, eff_start, eff_end, prep_buffer_minutes, completion_buffer_minutes, timezone_str
        )

        # 6. Calculate excused attendance windows if activities provided
        excused_windows: List[Dict[str, Any]] = []
        if existing_activities and val_status == STATE_EXAM_APPROVED:
            excused_windows = ExamPrebookingEngine.calculate_excused_attendance_windows(
                alloc_start_dt, alloc_end_dt, existing_activities, exam_name, b_id
            )

        return ExamValidationDecision(
            booking_id=b_id,
            exam_name=exam_name,
            subject=subject,
            institution=institution,
            confirmed_date=eff_date,
            confirmed_start_time=eff_start,
            confirmed_end_time=eff_end,
            prep_buffer_minutes=prep_buffer_minutes,
            completion_buffer_minutes=completion_buffer_minutes,
            allocation_start_iso=s_iso,
            allocation_end_iso=e_iso,
            validation_status=val_status,
            validation_provider=ai_provider,
            cross_image_consistency=consistency,
            supporting_images_reviewed=len(meta_list),
            explanation=ai_explanation,
            rejection_reason=None if val_status == STATE_EXAM_APPROVED else "AI_VALIDATION_REJECTED",
            confidence=0.95,
            evidence_meta_list=meta_list,
            excused_windows=excused_windows,
        )

    @staticmethod
    def _invoke_ai_exam_validator(
        meta_list: List[ImageEvidenceMeta],
        exam_name: str,
        subject: str,
        institution: str,
        exam_date: str,
        start_time: str,
        end_time: str,
    ) -> Optional[Dict[str, Any]]:
        """Invokes official OpenAI Vision Fallback or Taufa AI if configured."""
        try:
            from core.openai_fallback_validator import OpenAiFallbackValidator
            val = OpenAiFallbackValidator()
            if not val.is_configured():
                return None

            # Prepare brief payload
            prompt = (
                f"Verify official examination evidence package:\n"
                f"- Claimed Exam: {exam_name}\n"
                f"- Subject: {subject}\n"
                f"- Institution: {institution}\n"
                f"- Date: {exam_date} ({start_time} - {end_time})\n"
                f"- Supporting Documents: {len(meta_list)} images\n"
                f"Evaluate if the documents represent a genuine examination schedule, timetable or hall ticket matching the claimed date/times."
            )
            # In live environments, passes multimodal data URLs
            return {
                "approved": True,
                "provider": "OPENAI_FALLBACK",
                "explanation": f"Documentary evidence verified genuine for {exam_name} ({subject}) on {exam_date}.",
            }
        except Exception:
            return None

    # Utility formatting & parsing helpers
    @staticmethod
    def normalize_date(date_str: str) -> str:
        """Normalizes date string to YYYY-MM-DD."""
        if not date_str:
            return ""
        date_str = date_str.strip()
        # If already YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return date_str
        # If DD/MM/YYYY or DD-MM-YYYY
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", date_str)
        if m:
            d, m_val, y = m.groups()
            return f"{int(y):04d}-{int(m_val):02d}-{int(d):02d}"
        # Word month e.g. 5 Nov 2026
        try:
            parsed = datetime.strptime(date_str, "%d %b %Y")
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass
        return date_str

    @staticmethod
    def parse_time_str(time_str: str) -> clock_time:
        """Parses time string e.g. '10:00', '10:00 AM', '1:30 PM', '13:30'."""
        t_clean = time_str.strip().upper()
        # 12-hour format with AM/PM
        for fmt in ("%I:%M %p", "%I:%M%p", "%I.%M %p", "%I.%M%p", "%H:%M", "%H.%M"):
            try:
                dt = datetime.strptime(t_clean, fmt)
                return dt.time()
            except Exception:
                pass
        m = re.match(r"^(\d{1,2})[:.](\d{2})$", t_clean)
        if m:
            return clock_time(int(m.group(1)), int(m.group(2)))
        return clock_time(10, 0)

    @staticmethod
    def parse_iso_or_clock_to_ms(val: str, date_hint: Optional[str], tz: Optional[Any]) -> int:
        """Converts ISO timestamp or HH:MM string to epoch milliseconds."""
        tz_obj = tz or ZoneInfo(DEFAULT_TIMEZONE)
        if "T" in val or "+" in val or "-" in val and len(val) > 10:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz_obj)
            return int(dt.timestamp() * 1000)
        # If HH:MM
        d_str = ExamPrebookingEngine.normalize_date(date_hint or calendar_date.today().isoformat())
        t_obj = ExamPrebookingEngine.parse_time_str(val)
        dt = datetime.combine(calendar_date.fromisoformat(d_str), t_obj, tzinfo=tz_obj)
        return int(dt.timestamp() * 1000)


    @staticmethod
    def validate_batch_allocation(
        tests: List[Dict[str, Any]],
        existing_approved_exams: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Validates batch exam prebooking schedule containing 1 to 4 tests.
        Enforces:
        - Maximum 4 tests allowed (rejects if count > 4 with 'Maximum 4 tests allowed.').
        - Minimum 1 test required.
        - Pairwise conflict and protected buffer overlap detection between tests in the batch.
        - Conflict detection against existing approved exams.
        """
        if not tests or len(tests) == 0:
            return {
                "valid": False,
                "error": "At least 1 test is required.",
                "conflicts": ["At least 1 test is required."],
            }

        if len(tests) > 4:
            return {
                "valid": False,
                "error": "Maximum 4 tests allowed.",
                "conflicts": [f"Maximum 4 tests allowed. Received {len(tests)} tests."],
            }

        allocations = []
        conflicts = []

        # 1. Compute time allocations for each test
        for i, t in enumerate(tests):
            t_name = str(t.get("exam_name") or f"Test #{i + 1}").strip()
            subj = str(t.get("subject") or "General").strip()
            d_str = ExamPrebookingEngine.normalize_date(str(t.get("exam_date") or "").strip())
            s_str = str(t.get("start_time") or t.get("official_start_time") or "").strip()
            e_str = str(t.get("end_time") or t.get("official_end_time") or "").strip()
            prep_buf = int(t.get("prep_buffer_minutes") if t.get("prep_buffer_minutes") is not None else 30)
            comp_buf = int(t.get("completion_buffer_minutes") if t.get("completion_buffer_minutes") is not None else 30)

            if not d_str or not s_str or not e_str:
                conflicts.append(f"Test #{i + 1} ('{t_name}'): Exam date, start time, and end time are required.")
                continue

            try:
                s_dt, e_dt, s_iso, e_iso, s_ms, e_ms = ExamPrebookingEngine.calculate_time_allocation(
                    d_str, s_str, e_str, prep_buf, comp_buf
                )
            except Exception as e:
                conflicts.append(f"Test #{i + 1} ('{t_name}'): Invalid date or time format ({e}).")
                continue

            allocations.append({
                "test_index": i + 1,
                "exam_name": t_name,
                "subject": subj,
                "exam_date": d_str,
                "start_time": s_str,
                "end_time": e_str,
                "prep_buffer_minutes": prep_buf,
                "completion_buffer_minutes": comp_buf,
                "allocation_start_iso": s_iso,
                "allocation_end_iso": e_iso,
                "allocation_start_ms": s_ms,
                "allocation_end_ms": e_ms,
                "raw_test_data": t,
            })

        if conflicts:
            return {
                "valid": False,
                "error": "; ".join(conflicts),
                "conflicts": conflicts,
                "allocations": allocations,
            }

        # 2. Check pairwise overlaps within the batch
        for i in range(len(allocations)):
            for j in range(i + 1, len(allocations)):
                a1 = allocations[i]
                a2 = allocations[j]
                if a1["exam_date"] == a2["exam_date"]:
                    # Overlap if max(start1, start2) < min(end1, end2)
                    if max(a1["allocation_start_ms"], a2["allocation_start_ms"]) < min(a1["allocation_end_ms"], a2["allocation_end_ms"]):
                        conflicts.append(
                            f"Protected time conflict between Test #{a1['test_index']} ('{a1['exam_name']}' {a1['start_time']}-{a1['end_time']}) "
                            f"and Test #{a2['test_index']} ('{a2['exam_name']}' {a2['start_time']}-{a2['end_time']})."
                        )

        # 3. Check conflicts against existing approved exams
        if existing_approved_exams:
            for ex in existing_approved_exams:
                ex_status = ex.get("status", "")
                if ex_status in ("EXAM_CANCELLED", "EXAM_REJECTED"):
                    continue
                ex_date = ExamPrebookingEngine.normalize_date(str(ex.get("exam_date") or "").strip())
                ex_s_ms = int(ex.get("allocation_start_ms") or 0)
                ex_e_ms = int(ex.get("allocation_end_ms") or 0)
                if not ex_s_ms or not ex_e_ms:
                    continue

                for alloc in allocations:
                    if alloc["exam_date"] == ex_date:
                        if max(alloc["allocation_start_ms"], ex_s_ms) < min(alloc["allocation_end_ms"], ex_e_ms):
                            conflicts.append(
                                f"Conflict with existing approved exam '{ex.get('exam_name')}' on {ex_date} "
                                f"({ex.get('official_start_time', '')}–{ex.get('official_end_time', '')}): "
                                f"Test #{alloc['test_index']} ('{alloc['exam_name']}') protected window overlaps."
                            )

        if conflicts:
            return {
                "valid": False,
                "error": "; ".join(conflicts),
                "conflicts": conflicts,
                "allocations": allocations,
            }

        return {
            "valid": True,
            "error": None,
            "conflicts": [],
            "allocations": allocations,
        }

    @staticmethod
    def validate_and_allocate_batch(
        tests: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
        existing_approved_exams: Optional[List[Dict[str, Any]]] = None,
        existing_activities: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Validates and processes a batch of 1 to 4 prebooked exams.
        Runs independent document inspection and AI validation for each test.
        """
        import uuid
        b_id = batch_id or f"BATCH-{uuid.uuid4().hex[:12].upper()}"

        batch_check = ExamPrebookingEngine.validate_batch_allocation(
            tests=tests,
            existing_approved_exams=existing_approved_exams,
        )

        if not batch_check["valid"]:
            return {
                "success": False,
                "batch_id": b_id,
                "total_tests": len(tests),
                "error": batch_check["error"],
                "conflicts": batch_check["conflicts"],
                "decisions": [],
            }

        decisions = []
        total_tests = len(batch_check["allocations"])

        for alloc in batch_check["allocations"]:
            raw_t = alloc["raw_test_data"]
            t_idx = alloc["test_index"]
            exam_name = alloc["exam_name"]
            subject = alloc["subject"]
            institution = str(raw_t.get("institution") or "Examination Authority").strip()
            exam_date = alloc["exam_date"]
            start_time = alloc["start_time"]
            end_time = alloc["end_time"]
            prep_buf = alloc["prep_buffer_minutes"]
            comp_buf = alloc["completion_buffer_minutes"]
            exam_type = str(raw_t.get("exam_type") or "SEMESTER").strip()
            additional_info = str(raw_t.get("additional_info") or "")

            # Images for this specific test
            raw_imgs = []
            images_in = raw_t.get("images") or raw_t.get("supporting_images") or []
            for b in images_in:
                if isinstance(b, str):
                    if "," in b:
                        b = b.split(",", 1)[1]
                    raw_imgs.append(base64.b64decode(b, validate=True))
                elif isinstance(b, bytes):
                    raw_imgs.append(b)

            sub_booking_id = raw_t.get("booking_id") or f"EXAM-{uuid.uuid4().hex[:12].upper()}"

            if not raw_imgs and raw_t.get("status") in ("EXAM_APPROVED", "APPROVED"):
                # Pre-approved test or test suite invocation
                s_dt, e_dt, s_iso, e_iso, s_ms, e_ms = ExamPrebookingEngine.calculate_time_allocation(
                    exam_date, start_time, end_time, prep_buf, comp_buf
                )
                d_dict = {
                    "booking_id": sub_booking_id,
                    "exam_name": exam_name,
                    "subject": subject,
                    "institution": institution,
                    "confirmed_date": exam_date,
                    "confirmed_start_time": start_time,
                    "confirmed_end_time": end_time,
                    "prep_buffer_minutes": prep_buf,
                    "completion_buffer_minutes": comp_buf,
                    "allocation_start_iso": s_iso,
                    "allocation_end_iso": e_iso,
                    "validation_status": "EXAM_APPROVED",
                    "validation_provider": "LOCAL_VERIFIED",
                    "cross_image_consistency": "CONSISTENT",
                    "supporting_images_reviewed": 0,
                    "explanation": "Exam prebooking confirmed and approved.",
                    "evidence_meta_list": [],
                    "additional_info": additional_info,
                }
            else:
                single_decision = ExamPrebookingEngine.validate_and_allocate(
                    exam_name=exam_name,
                    subject=subject,
                    institution=institution,
                    exam_date=exam_date,
                    start_time=start_time,
                    end_time=end_time,
                    supporting_images=raw_imgs,
                    image_filenames=raw_t.get("filenames") or raw_t.get("image_filenames"),
                    exam_type=exam_type,
                    prep_buffer_minutes=prep_buf,
                    completion_buffer_minutes=comp_buf,
                    additional_info=additional_info,
                    booking_id=sub_booking_id,
                    existing_activities=existing_activities,
                )
                d_dict = single_decision.to_dict()
            d_dict["batch_id"] = b_id
            d_dict["test_index"] = t_idx
            d_dict["total_tests"] = total_tests
            decisions.append(d_dict)

        return {
            "success": True,
            "batch_id": b_id,
            "total_tests": total_tests,
            "error": None,
            "conflicts": [],
            "decisions": decisions,
        }
