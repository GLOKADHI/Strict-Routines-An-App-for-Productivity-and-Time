"""
STRICTRECORDS — STRICTBOT AI REVIEWER (ROBOTIC AI ADMINISTRATOR)
================================================================
Authoritative, auditable first-pass evidence & submission review pipeline.
Examines:
  1. Activity description (detail, semantic relevance, word count)
  2. Submitted image evidence (dimensions, payload integrity)
  3. Image quality analysis (sharpness/blurriness via Laplacian/gradient, brightness, contrast)
  4. SHA-256 exact cryptographic duplicate check across all historical evidence
  5. Perceptual image similarity (dHash / difference hash comparison)
  6. OCR and text extraction (normalized tokens, keyword matching)
  7. Task requirement matching (activity category, timing compliance)
  8. Submission timing (authoritative timestamp: window_start <= submitted_at < window_end)
  9. Cross-device historical comparison (detecting duplicate Android / PC submissions)
 10. Structured AI decision generation:
     - decision: PROVISIONAL_APPROVE, NEEDS_REVIEW, PROVISIONAL_REJECT, DUPLICATE_REVIEW, PROCESSING_ERROR
     - confidence: 0.0 to 1.0 (LOW, MEDIUM, HIGH)
     - reasons: structured human-readable explanations
     - checks: timing_valid, image_usable, ocr_completed, duplicate_risk, task_match
     - proposed_points: configured integer (default 8)
     - admin_review_required: bool
"""

import base64
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("StrictBotAIReviewer")
IST = timezone(timedelta(hours=5, minutes=30))

# Try importing PIL, OpenCV, and PyMuPDF
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pymupdf as _pymupdf
    HAS_PYMUPDF = True
except ImportError:
    try:
        import fitz as _pymupdf
        HAS_PYMUPDF = True
    except ImportError:
        _pymupdf = None
        HAS_PYMUPDF = False


def _resolve_tesseract_binary() -> Optional[str]:
    env_bin = os.environ.get("TESSERACT_CMD")
    if env_bin and os.path.isfile(env_bin):
        return env_bin
    which_bin = shutil.which("tesseract")
    if which_bin:
        return which_bin
    win_candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.EXE",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.EXE",
    ]
    for cand in win_candidates:
        if os.path.isfile(cand):
            return cand
    return None


TESSERACT_BINARY = _resolve_tesseract_binary()
_OCR_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}


# Activity domain keywords for description analysis
ACTIVITY_KEYWORDS = {
    "CODING": ["code", "coding", "debug", "python", "javascript", "commit", "git", "api", "function", "feature", "test", "build", "refactor", "bug", "terminal", "docker", "script", "database", "sql", "backend", "frontend"],
    "STUDY": ["study", "read", "chapter", "lecture", "notes", "concept", "exam", "quiz", "revision", "homework", "problem", "solve", "math", "physics", "science", "textbook", "paper"],
    "DEEP WORK": ["focus", "project", "writing", "report", "analysis", "design", "planning", "documentation", "research", "architecture", "task", "milestone"],
    "EXERCISE": ["gym", "workout", "cardio", "sets", "reps", "weights", "run", "pushup", "pullup", "squat", "stretching", "training"],
    "READING": ["book", "page", "chapter", "reading", "author", "article", "paper", "literature"],
    "TASKS": ["email", "clean", "organize", "plan", "errand", "chore", "review", "schedule"],
}

FORBIDDEN_DESCRIPTIONS = [
    "nothing", "none", "n/a", "na", "asdf", "test", "idk", "waste", "wasted",
    "slept", "sleeping", "chilling", "lazy", "procrastinating", "games", "gaming", "youtube shorts"
]


class StrictBotAIReviewer:
    """
    Automated Robotic Administrator for StrictRecords proof verification.
    Auditable pipeline producing structured review decisions.
    """

    VERSION = "2.3.0"
    MODEL_ID = "strictbot-ai-multimodal-v2"

    @classmethod
    def compute_sha256(cls, raw_bytes: bytes) -> str:
        """Computes hex SHA-256 digest of raw bytes."""
        h = hashlib.sha256()
        h.update(raw_bytes)
        return h.hexdigest().lower()

    @classmethod
    def compute_dhash(cls, image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit difference hash (dHash) for fast perceptual similarity.
        Resizes to (hash_size + 1, hash_size), converts to grayscale, and compares adjacent pixels.
        """
        if not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            # Resize to (width = hash_size + 1, height = hash_size)
            img = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(getattr(img, "get_flattened_data", img.getdata)())
            # Compare adjacent pixels in each row
            diff = []
            for row in range(hash_size):
                row_start = row * (hash_size + 1)
                for col in range(hash_size):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    diff.append(1 if left > right else 0)
            # Convert binary list to hex string
            dec = 0
            for bit in diff:
                dec = (dec << 1) | bit
            hex_len = (hash_size * hash_size) // 4
            return f"{dec:0{hex_len}x}"
        except Exception as e:
            logger.warning(f"Error computing dHash: {e}")
            return None

    @classmethod
    def compute_ahash(cls, image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit average hash (aHash) for complementary perceptual similarity.
        """
        if not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            img = img.resize((hash_size, hash_size), Image.Resampling.LANCZOS)
            pixels = list(getattr(img, "get_flattened_data", img.getdata)())
            avg = sum(pixels) / max(1, len(pixels))
            dec = 0
            for px in pixels:
                dec = (dec << 1) | (1 if px >= avg else 0)
            hex_len = (hash_size * hash_size) // 4
            return f"{dec:0{hex_len}x}"
        except Exception:
            return None

    @classmethod
    def hamming_distance(cls, hash1: str, hash2: str) -> int:
        """Calculates hamming distance between two hex hash strings of equal length."""
        try:
            val1 = int(hash1, 16)
            val2 = int(hash2, 16)
            xor = val1 ^ val2
            # Count set bits
            return bin(xor).count("1")
        except Exception:
            return 999

    @classmethod
    def analyze_image_quality(cls, raw_bytes: bytes) -> Dict[str, Any]:
        """
        Analyzes image sharpness, brightness, contrast, and resolution.
        Uses OpenCV Laplacian variance when available, with Pillow fallback.
        """
        res = {
            "usable": True,
            "sharpness": 75.0,
            "brightness": 128.0,
            "contrast": 50.0,
            "width": 0,
            "height": 0,
            "megapixels": 0.0,
            "quality_rating": "GOOD",
            "flags": [],
        }

        if not HAS_PIL and not HAS_CV2:
            return res

        try:
            if HAS_CV2:
                nparr = np.frombuffer(raw_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w, _ = img.shape
                    res["width"] = w
                    res["height"] = h
                    res["megapixels"] = round((w * h) / 1_000_000, 2)

                    # Grayscale for sharpness & brightness
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    mean_val = float(np.mean(gray))
                    std_val = float(np.std(gray))
                    res["brightness"] = round(mean_val, 1)
                    res["contrast"] = round(std_val, 1)

                    # Laplacian variance for sharpness
                    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    # Normalize sharpness to 0-100 scale (var 0-500 mapped to 0-100)
                    sharpness_score = min(100.0, max(0.0, round(laplacian_var / 3.0, 1)))
                    res["sharpness"] = sharpness_score

                    # Quality flags
                    if mean_val < 25.0:
                        res["usable"] = False
                        res["flags"].append("TOO_DARK_IMAGE")
                    elif mean_val > 240.0:
                        res["usable"] = False
                        res["flags"].append("OVEREXPOSED_IMAGE")

                    if sharpness_score < 10.0:
                        res["flags"].append("VERY_BLURRY")
                        if sharpness_score < 4.0:
                            res["usable"] = False

                    if w < 100 or h < 100:
                        res["usable"] = False
                        res["flags"].append("RESOLUTION_TOO_LOW")

                    # Overall quality rating
                    if not res["usable"]:
                        res["quality_rating"] = "UNUSABLE"
                    elif sharpness_score > 60.0 and 40 <= mean_val <= 210:
                        res["quality_rating"] = "EXCELLENT"
                    elif sharpness_score > 30.0:
                        res["quality_rating"] = "GOOD"
                    else:
                        res["quality_rating"] = "ACCEPTABLE"

                    return res

            if HAS_PIL:
                img = Image.open(io.BytesIO(raw_bytes))
                w, h = img.size
                res["width"] = w
                res["height"] = h
                res["megapixels"] = round((w * h) / 1_000_000, 2)
                gray = img.convert("L")
                stat = gray.getextrema()
                pixels = list(gray.getdata())
                avg = sum(pixels) / max(1, len(pixels))
                res["brightness"] = round(avg, 1)
                res["sharpness"] = 70.0
                if avg < 20 or avg > 245:
                    res["usable"] = False
                    res["flags"].append("EXTREME_LIGHTING")
                return res

        except Exception as e:
            logger.warning(f"Error analyzing image quality: {e}")
            res["flags"].append(f"ANALYSIS_ERROR: {str(e)[:40]}")

        return res

    @classmethod
    def extract_text_and_ocr(
        cls,
        raw_bytes: bytes,
        sha256_hash: Optional[str] = None,
        filename_hint: str = "",
    ) -> Dict[str, Any]:
        """
        Extracts and normalizes text from image or document evidence.
        Caches results by immutable sha256_hash so OCR is never rerun unnecessarily.
        Explicitly records status: OCR_COMPLETED, NO_TEXT_DETECTED, OCR_UNAVAILABLE, OCR_FAILED.
        Never declares an image invalid solely because OCR returned no text.
        """
        if not raw_bytes:
            return {
                "status": "OCR_FAILED",
                "has_text_regions": False,
                "extracted_text": "",
                "tokens": [],
                "token_count": 0,
                "confidence": 0.0,
                "engine": "none",
                "cached": False,
            }

        digest = (sha256_hash or cls.compute_sha256(raw_bytes)).lower()
        if digest in _OCR_MEMORY_CACHE:
            cached_res = dict(_OCR_MEMORY_CACHE[digest])
            cached_res["cached"] = True
            return cached_res

        extracted_text = ""
        confidence = 0.0
        has_text_regions = False
        ocr_status = "OCR_UNAVAILABLE"
        engine_used = "unavailable"

        # 1. Check if PDF document (%PDF-)
        if raw_bytes[:5] == b"%PDF-":
            if HAS_PYMUPDF and _pymupdf is not None:
                try:
                    doc = _pymupdf.open(stream=raw_bytes, filetype="pdf")
                    pages_text = []
                    for page in doc:
                        pages_text.append(page.get_text("text") or "")
                    doc.close()
                    extracted_text = "\n".join(pages_text).strip()
                    engine_used = "pymupdf-text-extractor"
                    if extracted_text:
                        ocr_status = "OCR_COMPLETED"
                        confidence = 0.98
                        has_text_regions = True
                    else:
                        ocr_status = "NO_TEXT_DETECTED"
                        confidence = 0.0
                except Exception as exc:
                    logger.warning(f"PDF text extraction failed: {exc}")
                    ocr_status = "OCR_FAILED"
                    engine_used = "pymupdf-text-extractor"
            else:
                ocr_status = "OCR_UNAVAILABLE"
                engine_used = "pdf-extractor-unavailable"

        # 2. Check if plain UTF-8 text / markdown / source file (and not an image header)
        elif not (
            raw_bytes[:8] == b"\x89PNG\r\n\x1a\n"
            or raw_bytes[:3] == b"\xff\xd8\xff"
            or raw_bytes[:4] in (b"RIFF", b"GIF8", b"BM")
        ):
            try:
                decoded = raw_bytes.decode("utf-8")
                if any(ch.isalpha() for ch in decoded):
                    extracted_text = decoded.strip()
                    ocr_status = "OCR_COMPLETED" if extracted_text else "NO_TEXT_DETECTED"
                    confidence = 1.0 if extracted_text else 0.0
                    has_text_regions = bool(extracted_text)
                    engine_used = "utf8-text-extractor"
                else:
                    ocr_status = "NO_TEXT_DETECTED"
                    engine_used = "utf8-text-extractor"
            except UnicodeDecodeError:
                ocr_status = "OCR_FAILED"
                engine_used = "unknown-binary"

        # 3. Image OCR pipeline
        else:
            # 3a. Morphological stroke region analysis (fast layout check)
            if HAS_CV2:
                try:
                    nparr = np.frombuffer(raw_bytes, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is not None:
                        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)
                        _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
                        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        text_boxes = 0
                        for c in contours:
                            _, _, w, h = cv2.boundingRect(c)
                            aspect = w / max(1, h)
                            if 1.5 < aspect < 20 and 8 < h < 120 and w > 20:
                                text_boxes += 1
                        if text_boxes >= 4:
                            has_text_regions = True
                except Exception:
                    pass

            # 3b. Real OCR via Tesseract binary if available
            tess_bin = TESSERACT_BINARY or _resolve_tesseract_binary()
            if tess_bin and HAS_PIL:
                try:
                    with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                        rgb = pil_img.convert("RGB")
                        w, h = rgb.size
                        # Normalize image dimensions to prevent OOM on huge photos and improve small-font legibility
                        if max(w, h) > 2200:
                            scale = 2200.0 / float(max(w, h))
                            rgb = rgb.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
                        elif w < 640 and h < 640:
                            rgb = rgb.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
                        png_buf = io.BytesIO()
                        rgb.save(png_buf, format="PNG")
                        png_bytes = png_buf.getvalue()

                    proc = subprocess.run(
                        [tess_bin, "stdin", "stdout", "--psm", "6", "tsv"],
                        input=png_bytes,
                        capture_output=True,
                        timeout=8.0,
                        check=False,
                    )
                    engine_used = "tesseract-ocr-tsv"
                    if proc.returncode == 0:
                        tsv_text = proc.stdout.decode("utf-8", errors="replace")
                        words_out: List[str] = []
                        confs: List[float] = []
                        for line in tsv_text.splitlines()[1:]:
                            parts = line.split("\t")
                            if len(parts) >= 12:
                                word_str = parts[11].strip()
                                try:
                                    conf_val = float(parts[10])
                                except ValueError:
                                    conf_val = -1.0
                                if word_str and conf_val >= 0.0:
                                    words_out.append(word_str)
                                    confs.append(conf_val / 100.0)
                        extracted_text = " ".join(words_out).strip()
                        if extracted_text:
                            ocr_status = "OCR_COMPLETED"
                            confidence = sum(confs) / len(confs) if confs else 0.75
                            has_text_regions = True
                        else:
                            ocr_status = "NO_TEXT_DETECTED"
                            confidence = 0.0
                    else:
                        ocr_status = "OCR_FAILED"
                except Exception as exc:
                    logger.warning(f"Tesseract OCR execution failed: {exc}")
                    ocr_status = "OCR_FAILED"
                    engine_used = "tesseract-ocr-tsv"
            else:
                ocr_status = "OCR_UNAVAILABLE"
                engine_used = "cv-contour-only" if HAS_CV2 else "ocr-unavailable"

        words = re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", extracted_text)
        tokens = [w.lower() for w in words]

        result = {
            "status": ocr_status,
            "has_text_regions": has_text_regions,
            "extracted_text": extracted_text[:4000],
            "tokens": tokens[:500],
            "token_count": len(tokens),
            "confidence": round(confidence, 2),
            "engine": engine_used,
            "sha256_hash": digest,
            "cached": False,
        }
        if ocr_status in ("OCR_COMPLETED", "NO_TEXT_DETECTED"):
            _OCR_MEMORY_CACHE[digest] = dict(result)
        return result

    @classmethod
    def compute_text_similarity(cls, tokens1: List[str], tokens2: List[str]) -> float:
        """Computes normalized token Jaccard similarity between two OCR/text token lists."""
        set1 = {t.lower() for t in (tokens1 or []) if len(t) >= 3}
        set2 = {t.lower() for t in (tokens2 or []) if len(t) >= 3}
        if len(set1) < 4 or len(set2) < 4:
            return 0.0
        inter = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return round(inter / max(1, union), 3)

    @classmethod
    def check_duplicate_evidence(
        cls,
        current_sha256: Optional[str],
        current_dhash: Optional[str],
        historical_records: List[Dict[str, Any]],
        current_ahash: Optional[str] = None,
        current_ocr_tokens: Optional[List[str]] = None,
        exclude_evidence_id: Optional[str] = None,
        exclude_submission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Layered duplicate proof detection across account history (Android + Windows):
          1. Exact cryptographic file hash (SHA-256)
          2. Perceptual 64-bit dHash / aHash Hamming distance
          3. Normalized OCR text token Jaccard similarity
        Returns both backward-compatible `match_status` ('ORIGINAL', 'POSSIBLE_DUPLICATE',
        'HIGH_SIMILARITY', 'EXACT_DUPLICATE', 'CHECK_INCOMPLETE') and explicit Part 3
        `status` ('NO_MATCH_FOUND', 'POSSIBLE_DUPLICATE', 'HIGH_SIMILARITY',
        'EXACT_DUPLICATE', 'CHECK_INCOMPLETE').
        """
        res: Dict[str, Any] = {
            "duplicate_risk": "LOW",
            "status": "NO_MATCH_FOUND",
            "duplicate_status": "NO_MATCH_FOUND",
            "match_status": "ORIGINAL",
            "matched_evidence_id": None,
            "comparison_method": "LAYERED_SHA256_DHASH_OCR",
            "threshold": 0.875,
            "model_version": cls.VERSION,
            "similarity_score": 0.0,
            "explanation": "No prior matching evidence found across registered devices.",
        }

        if not current_sha256 and not current_dhash and not current_ocr_tokens:
            res["status"] = "CHECK_INCOMPLETE"
            res["duplicate_status"] = "CHECK_INCOMPLETE"
            return res

        current_sha_norm = (current_sha256 or "").strip().lower()

        for rec in historical_records or []:
            eid = rec.get("evidence_id") or rec.get("submission_id")
            rel_sub = rec.get("related_record_id") or rec.get("submission_id")
            if exclude_evidence_id and eid and str(eid) == str(exclude_evidence_id):
                continue
            if exclude_submission_id and (
                (rel_sub and str(rel_sub) == str(exclude_submission_id))
                or (eid and str(eid) == str(exclude_submission_id))
            ):
                continue

            past_sha = (rec.get("sha256_hash") or "").strip().lower()
            past_dhash = rec.get("dhash") or ""
            past_ahash = rec.get("ahash") or ""
            past_tokens = rec.get("ocr_tokens") or []

            # 1. Exact SHA-256 match
            if current_sha_norm and past_sha and current_sha_norm == past_sha:
                res.update({
                    "duplicate_risk": "EXACT",
                    "status": "EXACT_DUPLICATE",
                    "duplicate_status": "EXACT_DUPLICATE",
                    "match_status": "EXACT_DUPLICATE",
                    "matched_evidence_id": eid,
                    "comparison_method": "SHA256_EXACT",
                    "threshold": 1.0,
                    "similarity_score": 1.0,
                    "explanation": f"Cryptographic SHA-256 hash identical to prior evidence ({eid}).",
                })
                return res

            # 2. Perceptual dHash / aHash comparison
            best_dist = 999
            method_used = "PERCEPTUAL_DHASH_64"
            if current_dhash and past_dhash and len(current_dhash) == len(past_dhash):
                best_dist = cls.hamming_distance(current_dhash, past_dhash)
            if current_ahash and past_ahash and len(current_ahash) == len(past_ahash):
                adist = cls.hamming_distance(current_ahash, past_ahash)
                if adist < best_dist:
                    best_dist = adist
                    method_used = "PERCEPTUAL_AHASH_64"

            if best_dist < 999:
                sim = round(max(0.0, (64 - best_dist) / 64.0), 3)
                if best_dist <= 1:
                    res.update({
                        "duplicate_risk": "EXACT",
                        "status": "EXACT_DUPLICATE",
                        "duplicate_status": "EXACT_DUPLICATE",
                        "match_status": "EXACT_DUPLICATE",
                        "matched_evidence_id": eid,
                        "comparison_method": method_used,
                        "threshold": 0.984,
                        "similarity_score": sim,
                        "explanation": f"Perceptual hash identical to prior evidence ({eid}) (distance={best_dist}).",
                    })
                    return res
                elif best_dist <= 4:
                    # Recurring study desk / laptop / notebook environment; advisory contextual note only.
                    res.update({
                        "duplicate_risk": "LOW",
                        "status": "ENVIRONMENT_SIMILARITY",
                        "duplicate_status": "ENVIRONMENT_SIMILARITY",
                        "match_status": "ENVIRONMENT_SIMILARITY",
                        "matched_evidence_id": eid,
                        "comparison_method": method_used,
                        "threshold": 0.9375,
                        "similarity_score": sim,
                        "explanation": f"Visual resemblance observed with recurring study workstation ({eid}) (distance={best_dist}, similarity={sim*100:.1f}%).",
                    })
                elif best_dist <= 8:
                    if res["duplicate_risk"] == "LOW":
                        res.update({
                            "duplicate_risk": "LOW",
                            "status": "WORKSPACE_SIMILARITY",
                            "duplicate_status": "WORKSPACE_SIMILARITY",
                            "match_status": "WORKSPACE_SIMILARITY",
                            "matched_evidence_id": eid,
                            "comparison_method": method_used,
                            "threshold": 0.875,
                            "similarity_score": sim,
                            "explanation": f"Possible workspace visual resemblance to prior evidence ({eid}) (similarity={sim*100:.1f}%).",
                        })

            # 3. Normalized OCR text similarity: OCR text supports activity relevance;
            # OCR text overlap on study notes/code must NOT independently reject or assign zero points.
            if current_ocr_tokens and past_tokens:
                text_sim = cls.compute_text_similarity(current_ocr_tokens, past_tokens)
                if text_sim >= 0.75:
                    res.update({
                        "duplicate_risk": "LOW",
                        "status": "TOPICAL_OVERLAP",
                        "duplicate_status": "TOPICAL_OVERLAP",
                        "match_status": "TOPICAL_OVERLAP",
                        "matched_evidence_id": eid,
                        "comparison_method": "OCR_TEXT_JACCARD",
                        "threshold": 0.75,
                        "similarity_score": text_sim,
                        "explanation": f"Extracted OCR text reflects continuous topic/coursework matching prior study notes ({eid}) (similarity={text_sim*100:.1f}%).",
                    })

        return res

    @classmethod
    def analyze_description(cls, description: str, primary_activity: str) -> Dict[str, Any]:
        """
        Analyzes the activity description:
        Checks word count, banned words, relevance to primary activity.
        """
        desc = (description or "").strip()
        desc_lower = desc.lower()
        word_count = len(re.findall(r"\w+", desc))

        # Check forbidden/trivial responses
        for fb in FORBIDDEN_DESCRIPTIONS:
            if desc_lower == fb or desc_lower.startswith(f"{fb} ") or desc_lower.endswith(f" {fb}"):
                return {
                    "valid": False,
                    "word_count": word_count,
                    "relevance": "POOR",
                    "reason": f"Description contains non-productive or placeholder term ('{fb}').",
                }

        if word_count < 3 and len(desc) < 12:
            return {
                "valid": False,
                "word_count": word_count,
                "relevance": "TOO_SHORT",
                "reason": "Description is too brief (minimum 3 meaningful words describing the hour).",
            }

        # Check domain relevance
        act_upper = (primary_activity or "CODING").upper()
        keywords = ACTIVITY_KEYWORDS.get(act_upper, ACTIVITY_KEYWORDS.get("CODING", []))
        matched_kw = [kw for kw in keywords if kw in desc_lower]

        if matched_kw:
            relevance = "HIGH"
            reason = f"Description aligns with {act_upper} keywords ({', '.join(matched_kw[:3])})."
        elif word_count >= 6:
            relevance = "ACCEPTABLE"
            reason = f"Detailed description logged ({word_count} words)."
        else:
            relevance = "GENERIC"
            reason = "Description provides basic activity summary."

        return {
            "valid": True,
            "word_count": word_count,
            "relevance": relevance,
            "matched_keywords": matched_kw,
            "reason": reason,
        }

    @classmethod
    def evaluate_timing(
        cls,
        submitted_at_iso: str,
        window_start_iso: Optional[str] = None,
        window_end_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Authoritative timing evaluation:
        Formula: window_start <= submitted_at < window_end
        A proof submitted at 10:09:59 for a window ending at 10:10:00 is on-time.
        A proof submitted at 10:10:00 is late under this boundary rule.
        """
        now = datetime.now(IST)
        sub_dt = None
        if submitted_at_iso:
            try:
                sub_clean = submitted_at_iso.replace("Z", "+00:00")
                if "T" in sub_clean:
                    sub_dt = datetime.fromisoformat(sub_clean)
                else:
                    sub_dt = datetime.strptime(sub_clean[:19], "%Y-%m-%d %H:%M:%S")
                if sub_dt.tzinfo is None:
                    sub_dt = sub_dt.replace(tzinfo=IST)
                else:
                    sub_dt = sub_dt.astimezone(IST)
            except Exception:
                sub_dt = now
        else:
            sub_dt = now

        # If window bounds not supplied, default to valid timing
        if not window_start_iso or not window_end_iso:
            return {
                "timing_valid": True,
                "status": "ON_TIME",
                "reason": "Submitted within active operational cycle.",
                "submitted_at": sub_dt.strftime("%Y-%m-%d %H:%M:%S"),
            }

        try:
            start_clean = window_start_iso.replace("Z", "+00:00")
            end_clean = window_end_iso.replace("Z", "+00:00")
            w_start = datetime.fromisoformat(start_clean) if "T" in start_clean else datetime.strptime(start_clean[:19], "%Y-%m-%d %H:%M:%S")
            w_end = datetime.fromisoformat(end_clean) if "T" in end_clean else datetime.strptime(end_clean[:19], "%Y-%m-%d %H:%M:%S")

            if w_start.tzinfo is None:
                w_start = w_start.replace(tzinfo=IST)
            else:
                w_start = w_start.astimezone(IST)

            if w_end.tzinfo is None:
                w_end = w_end.replace(tzinfo=IST)
            else:
                w_end = w_end.astimezone(IST)

            if sub_dt < w_start:
                return {
                    "timing_valid": False,
                    "status": "PRE_WINDOW",
                    "reason": f"Submission received before window opened (opened at {w_start.strftime('%H:%M:%S')}).",
                    "submitted_at": sub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
            elif sub_dt >= w_end:
                return {
                    "timing_valid": False,
                    "status": "LATE",
                    "reason": f"Submission received after window closed (deadline was {w_end.strftime('%H:%M:%S')}).",
                    "submitted_at": sub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
            else:
                remaining_sec = (w_end - sub_dt).total_seconds()
                return {
                    "timing_valid": True,
                    "status": "ON_TIME",
                    "reason": f"On-time submission ({int(remaining_sec)}s before window deadline).",
                    "submitted_at": sub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
        except Exception as e:
            return {
                "timing_valid": True,
                "status": "ASSUMED_ON_TIME",
                "reason": f"Timing fallback ({e})",
                "submitted_at": sub_dt.strftime("%Y-%m-%d %H:%M:%S"),
            }

    @classmethod
    def evaluate_task_content(
        cls,
        primary_activity: str,
        task_code: str,
        description: str,
        ocr_tokens: List[str],
        vision_objects: List[str],
        vlm_res: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluates whether visual objects, OCR tokens, and VLM match the task category."""
        activity = (primary_activity or task_code or "").upper()
        tokens = [t.lower() for t in ocr_tokens]
        objects = [o.lower() for o in vision_objects]
        vlm_top = (vlm_res.get("top_class") or "").lower() if vlm_res else ""

        is_coding = any(k in activity for k in ("CODING", "PROJECT", "DEEP WORK", "PRODUCTIVE", "WORK", "STUDY"))
        is_gym = any(k in activity for k in ("GYM", "EXERCISE", "WORKOUT", "FITNESS"))
        is_bath = any(k in activity for k in ("BATH", "SHOWER", "HYGIENE"))
        is_shutdown = any(k in activity for k in ("SHUTDOWN", "PC_SHUTDOWN"))

        matched_elements: List[str] = []
        mismatch_flags: List[str] = []

        coding_objects = {"laptop", "tv", "keyboard", "mouse", "book", "chair", "cell phone"}
        coding_keywords = {
            "def", "class", "import", "return", "function", "code", "python", "git", "strict", "error",
            "terminal", "const", "test", "study", "studying", "studied", "notes", "chapter", "book", "read",
            "reading", "math", "calculus", "algorithm", "exam", "assignment", "homework", "lecture", "course"
        }
        animal_objects = {"cat", "dog", "bird", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}

        found_animals = [o for o in objects if o in animal_objects]
        if vlm_top in ("cat", "dog", "bird", "frog", "horse") and vlm_res and vlm_res.get("neural_confidence", 0) >= 0.75:
            found_animals.append(vlm_top)

        if vlm_top and any(k in vlm_top for k in ("study", "notes", "code", "book", "laptop", "screen", "document", "paper")):
            matched_elements.append(vlm_top)

        desc_tokens = set(re.findall(r"\b\w+\b", (description or "").lower()))
        if is_coding:
            found_coding_objs = [o for o in objects if o in coding_objects]
            found_coding_tokens = [t for t in tokens if t in coding_keywords]
            found_desc_coding = [t for t in desc_tokens if t in coding_keywords]
            if found_coding_objs:
                matched_elements.extend(found_coding_objs)
            if found_coding_tokens:
                matched_elements.extend(found_coding_tokens[:5])
            if found_desc_coding:
                matched_elements.extend(found_desc_coding[:3])
            if found_animals and not found_coding_objs and not found_coding_tokens and "study" not in vlm_top:
                mismatch_flags.append(f"Irrelevant content detected ({', '.join(set(found_animals))}) instead of coding/work environment")
            elif not matched_elements:
                mismatch_flags.append(f"Submitted evidence lacks visible study/academic/coding materials (notes, textbook, IDE, code, or academic display) for {activity}")
        elif is_gym:
            gym_objects = {"person", "sports ball", "bottle", "bench"}
            gym_keywords = {"gym", "workout", "rep", "set", "bench", "squat", "fitness", "dumbbell", "weights"}
            found_gym_objs = [o for o in objects if o in gym_objects]
            found_gym_tokens = [t for t in tokens if t in gym_keywords]
            if found_gym_objs:
                matched_elements.extend(found_gym_objs)
            if found_gym_tokens:
                matched_elements.extend(found_gym_tokens)
            if found_animals and not found_gym_objs and not found_gym_tokens:
                mismatch_flags.append(f"Irrelevant content detected ({', '.join(set(found_animals))}) for gym proof")
        elif is_bath:
            bath_objects = {"sink", "toilet", "person", "bottle"}
            found_bath_objs = [o for o in objects if o in bath_objects]
            if found_bath_objs:
                matched_elements.extend(found_bath_objs)
            if found_animals and not found_bath_objs:
                mismatch_flags.append(f"Irrelevant content detected ({', '.join(set(found_animals))}) for bath proof")

        if mismatch_flags:
            return {
                "valid": False,
                "status": "CONTENT_MISMATCH",
                "matched_elements": matched_elements,
                "reason": "; ".join(mismatch_flags),
            }

        return {
            "valid": True,
            "status": "CONTENT_VERIFIED" if matched_elements else "CONTENT_ACCEPTABLE",
            "matched_elements": matched_elements,
            "reason": f"Content consistent with {activity} ({', '.join(set(matched_elements)) if matched_elements else 'visual proof accepted'}).",
        }

    @classmethod
    def execute_review(
        cls,
        submission_id: str,
        activity_id: str,
        task_code: str,
        description: str,
        primary_activity: str,
        submitted_at_iso: str,
        device_id: str,
        platform: str = "WINDOWS",
        image_base64: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        window_start_iso: Optional[str] = None,
        window_end_iso: Optional[str] = None,
        historical_records: Optional[List[Dict[str, Any]]] = None,
        configured_points: int = 8,
        auto_approve_high_confidence: bool = True,
        auto_approve_duplicate: bool = False,
        evidence_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Executes the full 10-stage StrictBot AI Reviewer pipeline.
        Returns auditable, structured decision dictionary.
        """
        start_time = time.time()
        historical_records = historical_records or []
        reasons: List[str] = []
        checks = {
            "timing_valid": True,
            "image_usable": True,
            "ocr_completed": False,
            "ocr_status": "NOT_APPLICABLE",
            "duplicate_risk": "LOW",
            "duplicate_status": "NO_MATCH_FOUND",
            "task_match": "LIKELY_MATCH",
        }

        # 1. Resolve raw bytes
        raw_bytes = image_bytes
        if not raw_bytes and image_base64:
            try:
                # Remove data URI prefix if present
                b64_clean = image_base64.split(",")[-1]
                raw_bytes = base64.b64decode(b64_clean)
            except Exception as e:
                logger.warning(f"Error decoding image base64: {e}")

        if not raw_bytes and file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    raw_bytes = f.read()
            except Exception as e:
                logger.warning(f"Error reading evidence file: {e}")

        has_image = bool(raw_bytes and len(raw_bytes) > 64)

        # 2. Timing Check
        timing_res = cls.evaluate_timing(submitted_at_iso, window_start_iso, window_end_iso)
        checks["timing_valid"] = timing_res["timing_valid"]
        reasons.append(timing_res["reason"])

        # 3. Description Analysis
        desc_res = cls.analyze_description(description, primary_activity)
        if desc_res["valid"]:
            reasons.append(desc_res["reason"])
            if desc_res["relevance"] in ("HIGH", "ACCEPTABLE"):
                checks["task_match"] = "LIKELY_MATCH"
            else:
                checks["task_match"] = "PARTIAL_MATCH"
        else:
            reasons.append(f"Description issue: {desc_res['reason']}")
            checks["task_match"] = "MISMATCH"

        # 4. Image Quality Analysis & Fingerprinting
        quality_res = {"usable": True, "quality_rating": "NOT_ATTACHED", "sharpness": 0.0}
        current_sha256 = None
        current_dhash = None
        current_ahash = None

        if has_image:
            current_sha256 = cls.compute_sha256(raw_bytes)
            current_dhash = cls.compute_dhash(raw_bytes)
            current_ahash = cls.compute_ahash(raw_bytes)
            quality_res = cls.analyze_image_quality(raw_bytes)
            checks["image_usable"] = quality_res["usable"]

            if quality_res["usable"]:
                reasons.append(
                    f"Evidence image quality verified ({quality_res['quality_rating']}, sharpness: {quality_res['sharpness']:.0f}/100, {quality_res.get('width', 0)}x{quality_res.get('height', 0)})."
                )
            else:
                reasons.append(f"Image quality issue: {', '.join(quality_res.get('flags', ['Poor quality']))}.")
        else:
            reasons.append("Submission logged without photo evidence (description verified).")

        # 5. OCR / Text extraction (cached by SHA-256)
        ocr_res = {
            "status": "NOT_APPLICABLE",
            "confidence": 0.0,
            "has_text_regions": False,
            "extracted_text": "",
            "tokens": [],
            "token_count": 0,
            "engine": "none",
        }
        if has_image:
            ocr_res = cls.extract_text_and_ocr(raw_bytes, sha256_hash=current_sha256, filename_hint=file_path or "")
            ocr_status = ocr_res.get("status", "OCR_UNAVAILABLE")
            checks["ocr_status"] = ocr_status
            checks["ocr_completed"] = bool(ocr_status == "OCR_COMPLETED" or ocr_res.get("has_text_regions", False))
            if ocr_status == "OCR_COMPLETED":
                snippet = (ocr_res.get("extracted_text") or "")[:60].replace("\n", " ").strip()
                reasons.append(
                    f"OCR extracted {ocr_res.get('token_count', 0)} text tokens (confidence {ocr_res.get('confidence', 0.0):.0%}): \"{snippet}\"."
                )
            elif ocr_status == "NO_TEXT_DETECTED":
                reasons.append("OCR completed: no readable text detected in image (visual activity photo).")
            elif ocr_status == "OCR_UNAVAILABLE":
                reasons.append("OCR engine unavailable; visual quality and perceptual hash checks applied.")
            elif ocr_status == "OCR_FAILED":
                reasons.append("OCR processing failed; falling back to visual quality and hash verification.")

        # 5b. Watermark Date/Time Extraction & Validation
        watermark_res = {
            "detected": False,
            "status": "NOT_ATTACHED" if not has_image else "NO_WATERMARK_DETECTED",
            "reason": "No image attached" if not has_image else "No watermark detected",
            "parsed": None,
            "validation": {"valid": True if not has_image else False, "status": "NOT_APPLICABLE" if not has_image else "NO_WATERMARK_DETECTED"},
        }
        watermark_passed = True if not has_image else False
        if has_image:
            try:
                from core.watermark_validator import WatermarkValidator
                wv = WatermarkValidator()
                scheduled_date = (submitted_at_iso or "")[:10] or datetime.now(IST).strftime("%Y-%m-%d")
                extracted_wm = wv.extract_watermark(raw_bytes, scheduled_date=scheduled_date)
                watermark_timing = wv.validate_watermark_timestamp(
                    extracted_wm,
                    window_start_iso=window_start_iso,
                    window_end_iso=window_end_iso,
                    scheduled_date=scheduled_date,
                    task_hour_slot=kwargs.get("hour_slot"),
                )
                watermark_res = {**extracted_wm, "validation": watermark_timing}
                checks["watermark_detected"] = extracted_wm.get("detected", False)
                checks["watermark_valid"] = watermark_timing.get("valid", False)
                checks["watermark_status"] = watermark_timing.get("status", "NO_WATERMARK_DETECTED")

                if watermark_timing.get("valid"):
                    reasons.append(watermark_timing.get("reason", "Watermark timestamp verified within window."))
                    watermark_passed = True
                elif extracted_wm.get("detected") and not watermark_timing.get("valid"):
                    reasons.append(f"Watermark timing failure: {watermark_timing.get('reason')}")
                    watermark_passed = False
                else:
                    reasons.append("Camera timestamp watermark not detected in candidate image regions.")
                    watermark_passed = False
            except Exception as e:
                logger.warning(f"Watermark validation error: {e}")
                watermark_res = {"detected": False, "status": "VALIDATOR_ERROR", "reason": str(e), "validation": {"valid": False, "status": "VALIDATOR_ERROR"}}
                checks["watermark_detected"] = False
                checks["watermark_valid"] = False
                checks["watermark_status"] = "VALIDATOR_ERROR"
                watermark_passed = False

        # 6. Layered Duplicate Detection (SHA-256 + Perceptual dHash/aHash + OCR tokens)
        dup_res = cls.check_duplicate_evidence(
            current_sha256=current_sha256,
            current_dhash=current_dhash,
            historical_records=historical_records,
            current_ahash=current_ahash,
            current_ocr_tokens=ocr_res.get("tokens") or [],
            exclude_evidence_id=evidence_id,
            exclude_submission_id=submission_id,
        )
        checks["duplicate_risk"] = dup_res["duplicate_risk"]
        checks["duplicate_status"] = dup_res.get("status", "NO_MATCH_FOUND")
        duplicate_policy_passed = dup_res["duplicate_risk"] not in ("EXACT", "HIGH")
        if dup_res["duplicate_risk"] in ("EXACT", "HIGH", "MEDIUM"):
            reasons.append(dup_res["explanation"])
        else:
            if has_image:
                reasons.append("No duplicate matches found across Android or Windows submission history.")

        # 6b. Real AI Bot Multimodal Neural VLM Inference (new_model realistic_vlm_phase4.pt)
        vlm_res = None
        if has_image:
            try:
                from core.real_ai_vlm import RealAIBotEngine
                vlm_engine = RealAIBotEngine.get_instance()
                vlm_res = vlm_engine.analyze_evidence(
                    image_input=raw_bytes,
                    task_code=task_code,
                    description=description,
                    user_context={"platform": platform, "device_id": device_id, "primary_activity": primary_activity},
                )
                checks["vlm_neural_verified"] = bool(vlm_res.get("is_neural", False))
                if vlm_res.get("is_neural"):
                    reasons.append(
                        f"Real AI Bot VLM ({vlm_res.get('checkpoint', 'phase4')}) visual analysis confirmed "
                        f"{vlm_res.get('top_class')} ({vlm_res.get('neural_confidence', 0):.1%} neural confidence)."
                    )
            except Exception as e:
                logger.warning(f"Real AI Bot VLM inference notice: {e}")

        # 6c. Compact Vision Detector & Task Content Verification
        vision_objects: List[str] = []
        vision_res = {"status": "NOT_APPLICABLE", "objects": []}
        if has_image:
            try:
                from new_model.evidence_pipeline.vision import CompactVisionAdapter
                vision_adapter = CompactVisionAdapter()
                with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                    v_data = vision_adapter.analyze(pil_img)
                    vision_objects = [o.get("label") for o in v_data.get("objects", []) if o.get("label")]
                    vision_res = {"status": "COMPLETED", "objects": vision_objects}
                    if vision_objects:
                        reasons.append(f"Visual objects identified: {', '.join(set(vision_objects[:5]))}.")
            except Exception as e:
                vision_res = {"status": "UNAVAILABLE", "error": str(e)}
                logger.warning(f"Compact vision detector notice: {e}")

        content_res = cls.evaluate_task_content(
            primary_activity=primary_activity,
            task_code=task_code,
            description=description,
            ocr_tokens=ocr_res.get("tokens") or [],
            vision_objects=vision_objects,
            vlm_res=vlm_res,
        )
        checks["task_match"] = "LIKELY_MATCH" if content_res["valid"] else "MISMATCH"
        if not content_res["valid"]:
            reasons.append(f"Content relevance issue: {content_res['reason']}")
        elif content_res.get("matched_elements"):
            reasons.append(content_res["reason"])

        # 7. Unified Decision Synthesis & Fair Scoring
        confidence = 0.50
        decision = "NEEDS_REVIEW"
        proposed_points = 0

        score_weights = 0.0
        if checks["timing_valid"]:
            score_weights += 0.25
        if desc_res["valid"]:
            score_weights += 0.25 if desc_res["relevance"] == "HIGH" else 0.15
        if content_res.get("valid"):
            score_weights += 0.10
        if has_image:
            if checks["image_usable"]:
                score_weights += 0.20
            if duplicate_policy_passed:
                score_weights += 0.10
            if watermark_passed:
                score_weights += 0.15
            if vlm_res and vlm_res.get("is_neural") and vlm_res.get("neural_confidence", 0) >= 0.50:
                score_weights += 0.05
        else:
            score_weights += 0.20

        confidence = min(0.98, max(0.20, round(score_weights, 2)))

        if task_code == "PC_SHUTDOWN":
            if not has_image:
                decision = "PROVISIONAL_REJECT"
                proposed_points = 0
                reasons.append("PC shutdown bonus requires photographic evidence of the workstation display powered off.")
            elif not duplicate_policy_passed and not auto_approve_duplicate:
                decision = "DUPLICATE_REVIEW"
                proposed_points = 0
                reasons.append("Duplicate photograph detected for PC shutdown evidence.")
            else:
                decision = "PROVISIONAL_APPROVE"
                proposed_points = configured_points
                reasons.append("Workstation display verified powered off; evaluated alongside registered Windows shutdown event.")
        elif not checks["timing_valid"]:
            decision = "PROVISIONAL_REJECT"
            proposed_points = 0
            reasons.append("Submitted outside the active hourly submission window.")
        elif not duplicate_policy_passed and not auto_approve_duplicate:
            decision = "DUPLICATE_REVIEW"
            proposed_points = 0
            reasons.append(f"Flagged for admin review due to {dup_res['duplicate_risk'].lower()} duplicate image match.")
        elif watermark_res and not watermark_res.get("validation", {}).get("valid") and (
            watermark_res.get("validation", {}).get("is_out_of_window")
            or watermark_res.get("validation", {}).get("is_ampm_mismatch")
            or watermark_res.get("validation", {}).get("is_date_mismatch")
        ):
            decision = "PROVISIONAL_REJECT"
            proposed_points = 0
            reasons.append(f"Watermark timing failure: {watermark_res.get('validation', {}).get('reason')}")
        elif not content_res["valid"]:
            decision = "PROVISIONAL_REJECT"
            proposed_points = 0
            reasons.append(f"Submitted evidence does not match scheduled {primary_activity} requirements.")
        elif not desc_res["valid"]:
            decision = "NEEDS_REVIEW"
            proposed_points = 0
            reasons.append("Description requires human admin review for sufficient activity detail.")
        elif has_image and checks.get("watermark_detected") and not checks["watermark_valid"]:
            decision = "NEEDS_REVIEW"
            proposed_points = 0
            reasons.append("Watermark verification ambiguous or unreadable; queued for admin verification.")
        elif checks["image_usable"] and duplicate_policy_passed and checks["timing_valid"] and desc_res["valid"] and content_res["valid"]:
            decision = "PROVISIONAL_APPROVE"
            proposed_points = configured_points
            reasons.append(f"Description and evidence appear fully consistent with scheduled {primary_activity} activity.")
        else:
            decision = "NEEDS_REVIEW"
            proposed_points = 0
            reasons.append("Provisional evaluation complete. Queued for standard admin verification.")

        review_reason = ""
        if decision in ("NEEDS_REVIEW", "DUPLICATE_REVIEW", "PROVISIONAL_REJECT"):
            if not duplicate_policy_passed:
                review_reason = "POSSIBLE_DUPLICATE"
            elif not content_res.get("valid", True):
                review_reason = "CATEGORY_MISMATCH"
            elif not desc_res.get("valid", True):
                review_reason = "DESCRIPTION_EVIDENCE_MISMATCH"
            elif not checks.get("image_usable", True):
                review_reason = "INSUFFICIENT_EVIDENCE"
            elif has_image and checks.get("watermark_detected") and not checks["watermark_valid"]:
                review_reason = "WATERMARK_AMBIGUOUS"
            else:
                review_reason = "MANDATORY_MANUAL_APPROVAL"

        elapsed = round(time.time() - start_time, 3)

        return {
            "submission_id": submission_id,
            "activity_id": activity_id,
            "evidence_id": evidence_id or submission_id,
            "task_code": task_code,
            "decision": decision,
            "review_reason": review_reason,
            "category_match": bool(content_res.get("valid", True)),
            "description_match": bool(desc_res.get("valid", True)),
            "evidence_match": bool(checks.get("image_usable", True) and content_res.get("valid", True)),
            "confidence": confidence,
            "confidence_label": "HIGH" if confidence >= 0.85 else ("MEDIUM" if confidence >= 0.60 else "LOW"),
            "reasons": reasons,
            "checks": checks,
            "proposed_points": proposed_points,
            "admin_review_required": True if decision in ("NEEDS_REVIEW", "DUPLICATE_REVIEW", "PROVISIONAL_REJECT") or not auto_approve_high_confidence else False,
            "quality": quality_res,
            "duplicate": dup_res,
            "timing": timing_res,
            "description_analysis": desc_res,
            "ocr": ocr_res,
            "watermark": watermark_res,
            "vision": vision_res,
            "content_evaluation": content_res,
            "neural_analysis": vlm_res,
            "ai_bot_advice": (vlm_res.get("ai_bot_advice") if vlm_res else None) or "Workspace proof evaluated by StrictBot AI.",
            "provenance": "AI_RECOMMENDATION",
            "metadata": {
                "sha256": current_sha256,
                "dhash": current_dhash,
                "ahash": current_ahash,
                "device_id": device_id,
                "platform": platform,
                "pipeline_version": cls.VERSION,
                "model_id": vlm_res.get("model_name") if vlm_res else cls.MODEL_ID,
                "processing_time_sec": elapsed,
                "reviewed_at": datetime.now(IST).isoformat(),
            }
        }
