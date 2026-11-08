"""StrictRecords Watermark-Aware Proof Validation Pipeline.

Extracts, normalizes, and validates camera watermark timestamps and dates from
evidence photographs using local Tesseract OCR and region-targeted heuristics.
Authoritative evaluation against scheduled activity windows and local timezone (IST, UTC+5:30).
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageOps

logger = logging.getLogger("StrictRecords.WatermarkValidator")

# Authoritative Local Timezone: IST (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

MONTH_MAP: Dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


class WatermarkValidator:
    """Extracts and verifies camera watermark timestamps against activity windows."""

    def __init__(self, tesseract_cmd: Optional[str] = None, timeout_seconds: float = 10.0):
        self.tesseract_cmd = tesseract_cmd or shutil.which("tesseract")
        if not self.tesseract_cmd:
            candidate = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.isfile(candidate):
                self.tesseract_cmd = candidate
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def crop_candidate_regions(image: Image.Image) -> List[Tuple[str, Image.Image]]:
        """Returns prioritized candidate regions where camera watermarks appear."""
        w, h = image.size
        crops: List[Tuple[str, Image.Image]] = []

        # 1. Bottom-left watermark window (exact band used by camera timestamp overlays)
        crops.append(("bottom_left_watermark", image.crop((0, int(0.88 * h), int(0.40 * w), int(0.96 * h)))))

        # 2. Bottom-left tight
        crops.append(("bottom_left_tight", image.crop((0, int(0.85 * h), int(0.45 * w), int(0.98 * h)))))

        # 2. Bottom-left standard
        crops.append(("bottom_left", image.crop((0, int(0.80 * h), int(0.50 * w), int(0.99 * h)))))

        # 3. Bottom banner
        crops.append(("bottom_banner", image.crop((0, int(0.83 * h), w, h))))

        # 4. Bottom-right
        crops.append(("bottom_right", image.crop((int(0.50 * w), int(0.80 * h), w, int(0.99 * h)))))

        # 5. Top-left
        crops.append(("top_left", image.crop((0, 0, int(0.50 * w), int(0.20 * h)))))

        # 6. Top-right
        crops.append(("top_right", image.crop((int(0.50 * w), 0, w, int(0.20 * h)))))

        return crops

    @staticmethod
    def normalize_ocr_text(text: str) -> str:
        """Normalizes common OCR digit/character confusions in watermark timestamps."""
        s = text.strip()

        # Month name OCR errors (e.g. 0ctober -> october)
        s = re.sub(r"0ct(ober)?", r"oct\1", s, flags=re.IGNORECASE)
        s = re.sub(r"nov3mber", "november", s, flags=re.IGNORECASE)
        s = re.sub(r"d3cember", "december", s, flags=re.IGNORECASE)

        # Period immediately followed by letter: "14.October" -> "14 October"
        s = re.sub(r"(\d{1,2})\.([a-zA-Z]+)", r"\1 \2", s)

        # Month immediately followed by year: "october2026" -> "october 2026"
        s = re.sub(r"([a-zA-Z]+)(20\d{2}|00\d{2})", r"\1 \2 ", s)

        # Time with 's' or '$' representing '4' or '4:': "2026s49'pm" -> "2026 4:49 pm"
        s = re.sub(r"\b[s$](\d{2})['\":.]*(pm|am)\b", r"4:\1 \2", s, flags=re.IGNORECASE)

        # Concatenated year and time: "20264149 pm" or "2026 4149 pm" -> "2026 4:49 pm"
        s = re.sub(r"\b(20\d{2}|00\d{2})[\s.]*([012]?\d)[1:.](\d{2})\s*(am|pm)\b", r"\1 \2:\3 \4", s, flags=re.IGNORECASE)

        # Truncated year like "00649 pm" -> "2026 4:49 pm"
        s = re.sub(r"\b00649\s*(pm|am)\b", r"2026 4:49 \1", s, flags=re.IGNORECASE)

        # Standard time with '1', '.', or quote as colon separator: "4149 pm" -> "4:49 pm", "4'49 pm" -> "4:49 pm"
        s = re.sub(r"\b([012]?\d)[1.'\":](\d{2})\s*(am|pm)\b", r"\1:\2 \3", s, flags=re.IGNORECASE)

        # Clean spaces
        s = re.sub(r"[ \t]+", " ", s)
        return s

    @classmethod
    def parse_datetime_stamp(
        cls, text: str, default_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Parses date and time from raw or normalized watermark text."""
        norm = cls.normalize_ocr_text(text)

        # 1. Pattern: "14 October 2026 4:49 pm" / "14 Oct 2026 16:49:00"
        m1 = re.search(
            r"\b(\d{1,2})\s+([a-zA-Z]+)\s+(20\d{2}|00\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
            norm,
            flags=re.IGNORECASE,
        )
        if m1:
            day = int(m1.group(1))
            month_str = m1.group(2).lower()
            year_raw = m1.group(3)
            year = 2026 if year_raw.startswith("00") else int(year_raw)
            hour = int(m1.group(4))
            minute = int(m1.group(5))
            second = int(m1.group(6) or 0)
            ampm = (m1.group(7) or "").lower()

            month = MONTH_MAP.get(month_str)
            if month and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
                dt = datetime(year, month, day, hour, minute, second, tzinfo=IST)
                return {
                    "datetime_iso": dt.isoformat(),
                    "dt": dt,
                    "date_str": dt.strftime("%Y-%m-%d"),
                    "time_str": dt.strftime("%H:%M:%S"),
                    "raw_match": m1.group(0),
                    "confidence": 0.95,
                    "has_ampm": bool(ampm),
                    "ampm": ampm.upper() if ampm else None,
                }

        # 2. Pattern: ISO "2026-10-14 16:49[:00]" or "2026-10-14T16:49[:00]"
        m2 = re.search(
            r"\b(20\d{2})[-/.](0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
            norm,
            flags=re.IGNORECASE,
        )
        if m2:
            year = int(m2.group(1))
            month = int(m2.group(2))
            day = int(m2.group(3))
            hour = int(m2.group(4))
            minute = int(m2.group(5))
            second = int(m2.group(6) or 0)
            ampm = (m2.group(7) or "").lower()
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
                dt = datetime(year, month, day, hour, minute, second, tzinfo=IST)
                return {
                    "datetime_iso": dt.isoformat(),
                    "dt": dt,
                    "date_str": dt.strftime("%Y-%m-%d"),
                    "time_str": dt.strftime("%H:%M:%S"),
                    "raw_match": m2.group(0),
                    "confidence": 0.95,
                    "has_ampm": bool(ampm),
                    "ampm": ampm.upper() if ampm else None,
                }

        # 3. Pattern: "DD-MM-YYYY HH:MM" or "DD/MM/YYYY HH:MM [AM|PM]"
        m3 = re.search(
            r"\b(0[1-9]|[12]\d|3[01])[-/.](0[1-9]|1[0-2])[-/.](20\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
            norm,
            flags=re.IGNORECASE,
        )
        if m3:
            day = int(m3.group(1))
            month = int(m3.group(2))
            year = int(m3.group(3))
            hour = int(m3.group(4))
            minute = int(m3.group(5))
            second = int(m3.group(6) or 0)
            ampm = (m3.group(7) or "").lower()
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
                dt = datetime(year, month, day, hour, minute, second, tzinfo=IST)
                return {
                    "datetime_iso": dt.isoformat(),
                    "dt": dt,
                    "date_str": dt.strftime("%Y-%m-%d"),
                    "time_str": dt.strftime("%H:%M:%S"),
                    "raw_match": m3.group(0),
                    "confidence": 0.90,
                    "has_ampm": bool(ampm),
                    "ampm": ampm.upper() if ampm else None,
                }

        # 4. Pattern: Date + Time on separate words/lines (e.g. "14 October 2026" and later "4:49 pm")
        date_part = re.search(
            r"\b(\d{1,2})\s+([a-zA-Z]+)(?:\s+(20\d{2}|00\d{2}))?\b",
            norm,
            flags=re.IGNORECASE,
        )
        time_part = re.search(
            r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
            norm,
            flags=re.IGNORECASE,
        )
        if date_part and time_part:
            day = int(date_part.group(1))
            m_str = date_part.group(2).lower()
            month = MONTH_MAP.get(m_str)
            if month and 1 <= day <= 31:
                year_str = date_part.group(3)
                if year_str:
                    year = 2026 if year_str.startswith("00") else int(year_str)
                elif default_date:
                    year = int(default_date.split("-")[0])
                else:
                    year = 2026

                hour = int(time_part.group(1))
                minute = int(time_part.group(2))
                second = int(time_part.group(3) or 0)
                ampm = (time_part.group(4) or "").lower()

                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    if ampm == "pm" and hour < 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0
                    dt = datetime(year, month, day, hour, minute, second, tzinfo=IST)
                    return {
                        "datetime_iso": dt.isoformat(),
                        "dt": dt,
                        "date_str": dt.strftime("%Y-%m-%d"),
                        "time_str": dt.strftime("%H:%M:%S"),
                        "raw_match": f"{date_part.group(0)} {time_part.group(0)}",
                        "confidence": 0.85,
                        "has_ampm": bool(ampm),
                        "ampm": ampm.upper() if ampm else None,
                    }

        # 5. Time only with default_date supplied
        if default_date and time_part:
            try:
                y, m, d = map(int, default_date.split("-"))
                hour = int(time_part.group(1))
                minute = int(time_part.group(2))
                second = int(time_part.group(3) or 0)
                ampm = (time_part.group(4) or "").lower()
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    if ampm == "pm" and hour < 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0
                    dt = datetime(y, m, d, hour, minute, second, tzinfo=IST)
                    return {
                        "datetime_iso": dt.isoformat(),
                        "dt": dt,
                        "date_str": default_date,
                        "time_str": dt.strftime("%H:%M:%S"),
                        "raw_match": time_part.group(0),
                        "confidence": 0.70,
                        "has_ampm": bool(ampm),
                        "ampm": ampm.upper() if ampm else None,
                    }
            except Exception:
                pass

        return None

    def _run_tesseract(self, image: Image.Image, psm: str = "6") -> str:
        """Executes local Tesseract binary on an image in-memory."""
        if not self.tesseract_cmd:
            return ""
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        try:
            res = subprocess.run(
                [self.tesseract_cmd, "stdin", "stdout", "--psm", psm],
                input=buf.getvalue(),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            return res.stdout.decode("utf-8", "ignore")
        except Exception as e:
            logger.warning(f"Tesseract execution error (PSM {psm}): {e}")
            return ""

    def extract_watermark(
        self,
        image_input: Union[bytes, str, Image.Image],
        scheduled_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts watermark timestamp by scanning candidate regions and full image."""
        if not self.tesseract_cmd:
            return {
                "detected": False,
                "status": "OCR_UNAVAILABLE",
                "reason": "Local Tesseract OCR binary is not configured or unavailable on this system.",
                "watermark_text": "",
                "parsed": None,
            }

        # Open image
        try:
            if isinstance(image_input, Image.Image):
                pil_img = image_input.convert("RGB")
            elif isinstance(image_input, bytes):
                pil_img = Image.open(io.BytesIO(image_input)).convert("RGB")
            elif isinstance(image_input, str) and os.path.isfile(image_input):
                pil_img = Image.open(image_input).convert("RGB")
            else:
                return {
                    "detected": False,
                    "status": "INVALID_IMAGE",
                    "reason": "Provided evidence image input is invalid or unreadable.",
                    "watermark_text": "",
                    "parsed": None,
                }
        except Exception as e:
            return {
                "detected": False,
                "status": "INVALID_IMAGE",
                "reason": f"Failed to load evidence image: {e}",
                "watermark_text": "",
                "parsed": None,
            }

        # 1. Scan candidate crop regions (fast, prioritized)
        crops = self.crop_candidate_regions(pil_img)
        for region_name, crop_img in crops:
            # Test raw crop with PSM 6
            ocr_text = self._run_tesseract(crop_img, psm="6")
            parsed = self.parse_datetime_stamp(ocr_text, default_date=scheduled_date)
            if parsed:
                return {
                    "detected": True,
                    "status": "WATERMARK_DETECTED",
                    "region": region_name,
                    "psm": "6",
                    "watermark_text": ocr_text.strip(),
                    "parsed": parsed,
                    "datetime_iso": parsed["datetime_iso"],
                    "date_str": parsed["date_str"],
                    "time_str": parsed["time_str"],
                    "raw_match": parsed["raw_match"],
                    "confidence": parsed["confidence"],
                }

            # If tight bottom crop didn't match directly, try PSM 7 or autocontrast
            if "bottom_left" in region_name:
                ocr_text7 = self._run_tesseract(crop_img, psm="7")
                parsed7 = self.parse_datetime_stamp(ocr_text7, default_date=scheduled_date)
                if parsed7:
                    return {
                        "detected": True,
                        "status": "WATERMARK_DETECTED",
                        "region": region_name,
                        "psm": "7",
                        "watermark_text": ocr_text7.strip(),
                        "parsed": parsed7,
                        "datetime_iso": parsed7["datetime_iso"],
                        "date_str": parsed7["date_str"],
                        "time_str": parsed7["time_str"],
                        "raw_match": parsed7["raw_match"],
                        "confidence": parsed7["confidence"],
                    }

        # 2. Fallback to full image (PSM 3)
        ocr_text_full = self._run_tesseract(pil_img, psm="3")
        if ocr_text_full.strip():
            parsed_full = self.parse_datetime_stamp(ocr_text_full, default_date=scheduled_date)
            if parsed_full:
                return {
                    "detected": True,
                    "status": "WATERMARK_DETECTED",
                    "region": "full_image",
                    "psm": "3",
                    "watermark_text": ocr_text_full.strip(),
                    "parsed": parsed_full,
                    "datetime_iso": parsed_full["datetime_iso"],
                    "date_str": parsed_full["date_str"],
                    "time_str": parsed_full["time_str"],
                    "raw_match": parsed_full["raw_match"],
                    "confidence": parsed_full["confidence"],
                }

        return {
            "detected": False,
            "status": "NO_WATERMARK_DETECTED",
            "reason": "No camera date/time watermark detected in candidate image regions.",
            "watermark_text": "",
            "parsed": None,
        }

    @classmethod
    def validate_watermark_timestamp(
        cls,
        watermark_info: Dict[str, Any],
        window_start_iso: Optional[str],
        window_end_iso: Optional[str],
        scheduled_date: Optional[str],
        task_hour_slot: Optional[str] = None,
        tolerance_minutes_before: int = 65,
        tolerance_minutes_after: int = 5,
    ) -> Dict[str, Any]:
        """Validates extracted watermark against scheduled activity window in local IST.

        Rules:
        1. Watermark must be detected.
        2. Watermark date must match scheduled_date.
        3. Watermark timestamp must be in or immediately around the activity window.
        4. Detects AM/PM confusions (e.g. 4:49 AM submitted for a 4 PM window).
        """
        if not watermark_info or not watermark_info.get("detected"):
            status = watermark_info.get("status") if watermark_info else "NO_WATERMARK_DETECTED"
            return {
                "valid": False,
                "status": status,
                "reason": watermark_info.get("reason", "No camera timestamp watermark was detected.") if watermark_info else "No watermark data.",
                "watermark_iso": None,
                "is_out_of_window": False,
                "is_date_mismatch": False,
                "is_ampm_mismatch": False,
            }

        parsed = watermark_info.get("parsed") or {}
        wm_dt: Optional[datetime] = parsed.get("dt")
        if not wm_dt and watermark_info.get("datetime_iso"):
            try:
                wm_dt = datetime.fromisoformat(watermark_info["datetime_iso"])
                if wm_dt.tzinfo is None:
                    wm_dt = wm_dt.replace(tzinfo=IST)
            except Exception:
                wm_dt = None

        if not wm_dt:
            return {
                "valid": False,
                "status": "UNPARSEABLE_TIMESTAMP",
                "reason": "Watermark timestamp could not be converted to a valid datetime.",
                "watermark_iso": None,
                "is_out_of_window": False,
                "is_date_mismatch": False,
                "is_ampm_mismatch": False,
            }

        # 1. Date match check
        wm_date_str = wm_dt.strftime("%Y-%m-%d")
        if scheduled_date and wm_date_str != scheduled_date:
            return {
                "valid": False,
                "status": "DATE_MISMATCH",
                "reason": f"Watermark date ({wm_date_str}) does not match scheduled checkpoint date ({scheduled_date}).",
                "watermark_iso": wm_dt.isoformat(),
                "is_out_of_window": True,
                "is_date_mismatch": True,
                "is_ampm_mismatch": False,
            }

        # 2. Window boundary check
        w_start_dt: Optional[datetime] = None
        w_end_dt: Optional[datetime] = None

        if window_start_iso:
            try:
                w_start_dt = datetime.fromisoformat(window_start_iso)
                if w_start_dt.tzinfo is None:
                    w_start_dt = w_start_dt.replace(tzinfo=IST)
            except Exception:
                pass

        if window_end_iso:
            try:
                w_end_dt = datetime.fromisoformat(window_end_iso)
                if w_end_dt.tzinfo is None:
                    w_end_dt = w_end_dt.replace(tzinfo=IST)
            except Exception:
                pass

        # If window bounds not supplied, infer from task_hour_slot and scheduled_date
        if (not w_start_dt or not w_end_dt) and task_hour_slot and scheduled_date:
            try:
                start_part = task_hour_slot.split("-")[0].strip()
                sh, sm = map(int, start_part.split(":"))
                y, m, d = map(int, scheduled_date.split("-"))
                w_start_dt = datetime(y, m, d, sh, sm, tzinfo=IST)
                if "-" in task_hour_slot:
                    end_part = task_hour_slot.split("-")[1].strip()
                    eh, em = map(int, end_part.split(":"))
                    w_end_dt = datetime(y, m, d, eh, em, tzinfo=IST)
                else:
                    w_end_dt = w_start_dt + timedelta(hours=1, minutes=15)
            except Exception:
                pass

        if w_start_dt and w_end_dt:
            # Check for AM / PM mismatch
            window_mid = w_start_dt + (w_end_dt - w_start_dt) / 2
            is_window_pm = window_mid.hour >= 12
            is_wm_pm = wm_dt.hour >= 12
            hour_diff_24 = abs(window_mid.hour - wm_dt.hour)
            if is_window_pm != is_wm_pm and (hour_diff_24 >= 10 or hour_diff_24 <= 14):
                return {
                    "valid": False,
                    "status": "AM_PM_MISMATCH",
                    "reason": f"Watermark timestamp ({wm_dt.strftime('%I:%M %p')}) has AM/PM mismatch with scheduled window ({w_start_dt.strftime('%I:%M %p')}-{w_end_dt.strftime('%I:%M %p')}).",
                    "watermark_iso": wm_dt.isoformat(),
                    "is_out_of_window": True,
                    "is_date_mismatch": False,
                    "is_ampm_mismatch": True,
                }

            earliest_allowed = w_start_dt - timedelta(minutes=tolerance_minutes_before)
            latest_allowed = w_end_dt + timedelta(minutes=tolerance_minutes_after)

            if wm_dt < earliest_allowed:
                diff_min = int((earliest_allowed - wm_dt).total_seconds() / 60)
                return {
                    "valid": False,
                    "status": "OUT_OF_WINDOW",
                    "reason": f"Watermark timestamp ({wm_dt.strftime('%H:%M:%S')}) is {diff_min} min prior to the allowed activity window (opened at {w_start_dt.strftime('%H:%M:%S')}).",
                    "watermark_iso": wm_dt.isoformat(),
                    "is_out_of_window": True,
                    "is_date_mismatch": False,
                    "is_ampm_mismatch": False,
                }

            if wm_dt > latest_allowed:
                diff_min = int((wm_dt - latest_allowed).total_seconds() / 60)
                return {
                    "valid": False,
                    "status": "OUT_OF_WINDOW",
                    "reason": f"Watermark timestamp ({wm_dt.strftime('%H:%M:%S')}) is past the allowed window deadline ({w_end_dt.strftime('%H:%M:%S')}).",
                    "watermark_iso": wm_dt.isoformat(),
                    "is_out_of_window": True,
                    "is_date_mismatch": False,
                    "is_ampm_mismatch": False,
                }

        return {
            "valid": True,
            "status": "PASSED",
            "reason": f"Watermark verified at {wm_dt.strftime('%Y-%m-%d %I:%M %p')} IST within activity window.",
            "watermark_iso": wm_dt.isoformat(),
            "is_out_of_window": False,
            "is_date_mismatch": False,
            "is_ampm_mismatch": False,
        }
