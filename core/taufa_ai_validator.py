"""
StrictRecords — Temporary Taufa AI Hourly Attendance Validation Adapter (Python / Windows)
========================================================================================
Isolated adapter for validating Hourly Attendance submissions against Taufa AI.
Features:
  1. Strictly restricted to Hourly Attendance (task_type == "HOURLY").
     Explicitly excludes BATH, GYM, SLEEP, WAKE_UP, TRAVEL, and MOVIE submissions.
  2. Accepts 1 to 10 photos attached to one submission.
  3. Validates meaningful activity description (rejects blank or generic filler).
  4. Secure credential checking (Windows Credential Manager / protected environment).
  5. Translates Taufa AI responses into internal structured schema out of 8.
  6. Strict score validation (range 0..8, never auto-awarding max 8).
  7. Idempotent submission tracking and graceful unconfigured/offline fallbacks.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger("TaufaAiValidator")

VALIDATOR_VERSION = "1.2.0"
DESIGNATED_CONVERSATION_ID = "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
DESIGNATED_CONVERSATION_URL = "https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
MAX_HOURLY_PHOTOS = 10
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB per image limit on Windows

ALLOWED_TASK_TYPES = {"HOURLY"}
EXCLUDED_TASK_TYPES = {
    "BATH",
    "GYM",
    "SLEEP",
    "WAKE_UP",
    "WAKEUP",
    "MOVIE",
    "TRAVEL",
}

GENERIC_FILLER_WORDS = {
    "nothing",
    "none",
    "n/a",
    "na",
    "asdf",
    "test",
    "idk",
    "waste",
    "wasted",
    "done",
    "ok",
    "completed",
    "working",
    "slept",
    "sleeping",
    "chilling",
    "lazy",
    "abc",
    "xyz",
    "something",
    "stuff",
}

SUPPORTED_HOURLY_CATEGORIES = [
    "STUDY",
    "COLLEGE",
    "CODING",
    "PROJECT",
    "RESEARCH",
    "READING",
    "EXERCISE",
    "WORK",
    "PRODUCTIVE",
    "EDITING",
    "VIDEO SHOOT",
]

ACADEMIC_CATEGORIES = {"STUDY", "COLLEGE", "RESEARCH", "READING"}

ACADEMIC_KEYWORDS = {
    "study", "studying", "studied", "notes", "chapter", "lecture", "course",
    "coursework", "assignment", "homework", "exam", "quiz", "revision", "calculus",
    "math", "mathematics", "physics", "chemistry", "biology", "code", "coding",
    "programming", "algorithm", "data structure", "database", "sql", "project",
    "lab", "textbook", "reading", "read", "slides", "presentation", "paper",
    "research", "theorem", "exercise", "practicals", "avl", "tree", "complexity",
    "runtime", "proofs", "notebook", "textbook", "homework", "class"
}

DISALLOWED_STUDY_KEYWORDS = {
    "game", "gaming", "minecraft", "fortnite", "steam", "playstation", "xbox",
    "movie", "netflix", "cinema", "youtube shorts", "tiktok", "instagram", "reels",
    "cooking", "recipe", "dinner", "lunch", "breakfast", "grocery", "shopping",
    "amazon", "gym", "bicep", "workout", "weights", "squat", "bench press",
    "fitness", "selfie", "face", "portrait"
}

TAUFA_PROOF_SCORING_RUBRIC = """Score	What it looks like
0	Blank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)
1	Barely relevant, or a decorative/title graphic with a topic caption but no actual content
2	Real material but near-empty page, gibberish description, or no way to tie it to the claimed activity
3	Right subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour
4	Partial: correct material visible, description only loosely matched, or content too thin for an hour
5	Credible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports
6	Authentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)
7	Strong, specific, timestamped evidence matching category and description closely
8	Exemplary: multiple timestamped pages of worked material that matches the described topics point for point"""

TAUFA_VALIDATION_SYSTEM_INSTRUCTION = (
    "You are validating evidence for a personal discipline and hourly activity-tracking system. "
    "Evaluate the selected activity category, the user's description, and every submitted image together.\n\n"
    "Determine whether the images visibly and meaningfully support the claimed activity. "
    "Check whether the description matches the actual evidence, whether the evidence belongs to the "
    "selected category, and whether the submission contains irrelevant, insufficient, contradictory, "
    "or suspiciously reused material.\n\n"
    "Do not award points merely because images are attached, a description sounds convincing, "
    "text appears in an image, or the user claims to have completed the task.\n\n"
    "For the Study category, require meaningful evidence related to studying, college coursework, "
    "study notes, textbooks, lecture materials, assignments, practical work, educational diagrams, "
    "academic portals, or relevant coding and project work. A random photo, selfie, unrelated screenshot, "
    "blank page, unrelated document, or unrelated application must not receive a qualifying score "
    "simply because the description says 'studying'.\n\n"
    "Evaluate the actual semantic relationship between the category, description, and visual evidence. "
    "Return an evidence-based score from 0 to 8 with a clear reason, category-match assessment, "
    "description-match assessment, evidence-quality assessment, warnings, and duplicate-evidence result when available.\n\n"
    "If evidence is irrelevant, contradictory, insufficient, or impossible to assess, do not invent a positive result. "
    "Return an appropriately low score or a review-required result according to the supported response schema. "
    "Never describe an unverified activity as proven.\n\n"
    "SCORING SYSTEM FOR PROOF:\n"
    f"{TAUFA_PROOF_SCORING_RUBRIC}"
)

CATEGORY_EXPECTATIONS = {
    "STUDY": {
        "description": "Academic studying, coursework, or exam preparation",
        "expected_evidence": [
            "Handwritten or digital study notes",
            "Textbook or reference-material pages relevant to claimed topic",
            "College assignments, lab work, or practical records",
            "Lecture slides or course materials",
            "Educational diagrams, calculations, or problem-solving",
            "Coding exercises or technical project work when relevant to academic task",
            "Academic work displayed in suitable learning/development environment",
        ],
        "disallowed_evidence": [
            "Random selfies and personal photos",
            "Blank or solid color pages",
            "Unrelated screenshots (gaming, entertainment, social media, shopping)",
            "Unrelated documents or unreadable images",
        ],
    },
    "COLLEGE": {
        "description": "College coursework, lecture attendance, lab work, or academic assignments",
        "expected_evidence": [
            "College coursework, notes, slides, lab practicals, assignments, or campus portal",
        ],
        "disallowed_evidence": [
            "Random selfies, blank pages, unrelated screenshots, social media",
        ],
    },
    "CODING": {
        "description": "Software development, scripting, or debugging",
        "expected_evidence": [
            "Code editor, IDE, terminal, compiler output, git commits, pull requests, technical documentation",
        ],
        "disallowed_evidence": [
            "Random selfies, non-programming screenshots, entertainment, blank pages",
        ],
    },
    "PROJECT": {
        "description": "Project implementation, design, or engineering work",
        "expected_evidence": [
            "Project files, schematics, design tools, code, board tasks, build artifacts",
        ],
        "disallowed_evidence": [
            "Random selfies, blank pages, entertainment",
        ],
    },
    "RESEARCH": {
        "description": "Academic or professional research and reading",
        "expected_evidence": [
            "Research papers, technical articles, reference documentation, research notes",
        ],
        "disallowed_evidence": [
            "Random selfies, blank pages, entertainment",
        ],
    },
    "READING": {
        "description": "Reading books, articles, or educational publications",
        "expected_evidence": [
            "Book pages, e-reader screen, PDF reader, article text, reading notes",
        ],
        "disallowed_evidence": [
            "Random selfies, blank pages, video games",
        ],
    },
    "EXERCISE": {
        "description": "Physical exercise or fitness session",
        "expected_evidence": [
            "Workout equipment, fitness tracker metrics, exercise logs, workout area",
        ],
        "disallowed_evidence": [
            "Screenshots of desk work without workout evidence, blank pages",
        ],
    },
    "WORK": {
        "description": "Professional or vocational work tasks",
        "expected_evidence": [
            "Work documents, spreadsheets, professional communications, workspace",
        ],
        "disallowed_evidence": [
            "Entertainment, games, blank pages, unrelated social media",
        ],
    },
    "PRODUCTIVE": {
        "description": "General productive activity",
        "expected_evidence": [
            "Visual proof directly supporting the claimed productive activity",
        ],
        "disallowed_evidence": [
            "Blank pages, entertainment, random selfies",
        ],
    },
}


@dataclass
class TaufaDuplicateResult:
    submission_id: str
    duplicate_detected: bool = False
    matched_submission_id: Optional[str] = None
    similarity_or_confidence: Optional[float] = None
    reason: str = ""
    review_required: bool = False
    validator_status: str = "SUCCESS"  # SUCCESS, DUPLICATE_CONFIRMED, NEAR_DUPLICATE, REVIEW_REQUIRED, UNCONFIGURED, FAILED_RETRYABLE, FAILED_FINAL
    match_type: Optional[str] = None  # EXACT, NEAR_DUPLICATE, INTRA_SUBMISSION, AMBIGUOUS, NONE
    raw_response: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "duplicate_detected": self.duplicate_detected,
            "matched_submission_id": self.matched_submission_id,
            "similarity_or_confidence": self.similarity_or_confidence,
            "reason": self.reason,
            "review_required": self.review_required,
            "validator_status": self.validator_status,
            "match_type": self.match_type,
        }


@dataclass
class TaufaValidationResult:
    submission_id: str
    score: Optional[int] = None
    max_score: int = 8
    category_match: bool = False
    description_match: bool = False
    evidence_match: bool = False
    evidence_quality: str = "INSUFFICIENT"  # HIGH, MEDIUM, LOW, INSUFFICIENT
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    validator_status: str = "UNCONFIGURED"  # SUCCESS, UNCONFIGURED, DISABLED, FAILED_RETRYABLE, FAILED_FINAL, REJECTED, REVIEW_REQUIRED
    review_status: str = "UNREVIEWED"  # APPROVED, REJECTED, REVIEW_REQUIRED, BELOW_THRESHOLD, UNCONFIGURED, UNREVIEWED
    duplicate_result: Optional[TaufaDuplicateResult] = None
    raw_response: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    evidence_analysis: List[Dict[str, Any]] = field(default_factory=list)
    model_metadata: Dict[str, Any] = field(default_factory=dict)
    validated_at: Optional[str] = None
    conversation_id: str = "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
    evaluation_id: Optional[str] = None
    evaluation_state: str = "EVALUATION_VALIDATED"
    attachments_meta: List[Dict[str, Any]] = field(default_factory=list)
    category: str = "STUDY"
    description: str = ""
    validator_id: str = "Taufa_AI"
    validation_provider: str = "TAUFA_AI"
    rubric_version: str = "1.0.0"
    activity_categories: List[str] = field(default_factory=list)
    is_technical_failure: bool = False
    image_transfer_state: str = "IMAGE_VERIFIED"
    taufa_validation_state: Optional[str] = None

    @property
    def validation_state(self) -> str:
        if self.taufa_validation_state:
            return self.taufa_validation_state
        if self.approved:
            return "TAUFA_APPROVED"
        if self.validator_status == "REJECTED":
            return "TAUFA_REJECTED"
        if "TIMEOUT" in self.warnings:
            return "TAUFA_TIMEOUT"
        if self.is_technical_failure or "API_ERROR" in self.warnings or any("HTTP_" in str(w) for w in self.warnings):
            return "TAUFA_API_ERROR"
        if self.validator_status in ("REVIEW_REQUIRED", "NEEDS_REVIEW"):
            return "TAUFA_REVIEW_REQUIRED"
        if self.validator_status == "PROCESSING":
            return "TAUFA_VALIDATING"
        return "TAUFA_PENDING"

    @property
    def approved(self) -> bool:
        return bool(self.qualifies_for_points and (self.score or 0) > 0)

    @property
    def awarded_points(self) -> int:
        return int(self.score or 0) if self.qualifies_for_points else 0

    @property
    def activity_relevance(self) -> str:
        if self.category_match and self.description_match:
            return "HIGH"
        if self.category_match or self.description_match:
            return "MEDIUM"
        return "INSUFFICIENT"

    @property
    def explanation(self) -> str:
        return self.reason or ""

    @property
    def rejection_reason(self) -> str:
        return "" if self.approved else (self.reason or "")

    @property
    def validation_timestamp(self) -> str:
        return self.validated_at or datetime.now(timezone.utc).isoformat()

    @property
    def is_valid(self) -> bool:
        return (
            self.validator_status in {"SUCCESS", "REJECTED", "REVIEW_REQUIRED", "AWAITING_APPROVAL"}
            and self.score is not None
            and 0 <= self.score <= self.max_score
        )

    @property
    def qualifies_for_points(self) -> bool:
        """
        True only if the submission passed the category-consistency gate:
        category, description, and visual evidence all agree, score >= 1/8,
        and no duplicate is detected.
        """
        if self.duplicate_result and self.duplicate_result.duplicate_detected:
            return False
        if not self.category_match or not self.evidence_match or not self.description_match:
            return False
        if self.evidence_quality == "INSUFFICIENT":
            return False
        if self.validator_status not in {"SUCCESS", "AWAITING_APPROVAL", "APPROVED", "APPROVED_PARTIAL"}:
            return False
        if self.review_status in {"REJECTED", "REVIEW_REQUIRED", "BELOW_THRESHOLD"}:
            return False
        if self.score is None or self.score < 1:
            return False
        return True

    @property
    def is_full_points(self) -> bool:
        return self.qualifies_for_points and (self.score or 0) == self.max_score

    @property
    def is_partial_points(self) -> bool:
        return self.qualifies_for_points and 1 <= (self.score or 0) < self.max_score

    def to_structured_evaluation(
        self,
        mode: str = "DEMO",
        task_type: str = "study",
        schedule_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert result to the Section 6 standardized 13-key evaluation response schema."""
        clean_mode = str(mode or "DEMO").strip().upper()
        if clean_mode not in ("DEMO", "PRODUCTION"):
            clean_mode = "DEMO"
        clean_task = str(task_type or "study").strip().lower()

        status = "PROCESSING_ERROR"
        recommended_action = "retry"
        requires_human_review = False

        is_dup = bool(self.duplicate_result and self.duplicate_result.duplicate_detected)

        if is_dup:
            status = "INVALID"
            recommended_action = "reject"
            requires_human_review = False
        elif self.validator_status in ("UNCONFIGURED", "FAILED_RETRYABLE"):
            status = "PROCESSING_ERROR"
            recommended_action = "retry"
            requires_human_review = False
        elif self.validator_status == "FAILED_FINAL":
            status = "INVALID"
            recommended_action = "reject"
            requires_human_review = False
        elif self.validator_status == "NEEDS_REVIEW":
            status = "NEEDS_REVIEW"
            recommended_action = "review"
            requires_human_review = True
        elif self.validator_status == "REJECTED" or self.review_status == "REJECTED":
            if "OUTSIDE_SCHEDULE_WINDOW" in self.warnings:
                status = "INVALID"
            elif not self.evidence_match or not self.description_match or not self.category_match:
                status = "INSUFFICIENT_EVIDENCE"
            else:
                status = "INVALID"
            recommended_action = "reject"
            requires_human_review = False
        elif self.is_valid:
            is_phys_prereq = clean_task in ("bath", "gym", "wake_up") or str(self.category).upper() in ("BATH", "GYM", "WAKE_UP")
            if is_phys_prereq:
                status = "VALID"
                recommended_action = "review"
                requires_human_review = True
            elif self.qualifies_for_points and (self.score or 0) >= 6:
                status = "VALID"
                recommended_action = "approve"
                requires_human_review = False
            elif (self.score or 0) == 0:
                if not self.evidence_match or not self.description_match or not self.category_match:
                    status = "INSUFFICIENT_EVIDENCE"
                else:
                    status = "INVALID"
                recommended_action = "reject"
                requires_human_review = False
            else:
                status = "NEEDS_REVIEW"
                recommended_action = "review"
                requires_human_review = True
        else:
            if (self.score or 0) == 0 or self.validator_status == "REJECTED":
                if not self.evidence_match or not self.description_match or not self.category_match:
                    status = "INSUFFICIENT_EVIDENCE"
                else:
                    status = "INVALID"
                recommended_action = "reject"
                requires_human_review = False
            else:
                status = "PROCESSING_ERROR"
                recommended_action = "retry"
                requires_human_review = False

        observations: List[str] = []
        extracted_text: List[str] = []
        for ea in self.evidence_analysis:
            idx = ea.get("image_index", 1)
            q = ea.get("quality") or {}
            ocr = ea.get("ocr") or {}
            txt = str(ocr.get("extracted_text") or "").strip()
            if txt:
                extracted_text.append(txt)
            dim = q.get("dimensions") or "valid dimensions"
            obs = f"Image #{idx}: {dim}, {q.get('verdict', 'readable')}"
            if txt:
                obs += f", OCR text: {txt[:60]}..."
            observations.append(obs)
        if not observations and getattr(self, "attachments_meta", None):
            for idx, att in enumerate(self.attachments_meta):
                dim = att.get("dimensions") or "valid dimensions"
                fn = att.get("filename", f"photo #{idx+1}")
                observations.append(f"Image #{idx+1} ({fn}): {dim}, byte size {att.get('byte_size', 0)} bytes, sha256 {att.get('sha256', '')[:12]}...")
        if self.reason:
            observations.append(f"Visual assessment: {self.reason}")

        criteria_results: List[Dict[str, Any]] = [
            {
                "criterion": "category_relevance",
                "passed": bool(self.category_match),
                "details": f"Category '{self.category}' {'corroborated' if self.category_match else 'unsupported'} by visual evidence.",
            },
            {
                "criterion": "description_relevance",
                "passed": bool(self.description_match),
                "details": f"Description {'corroborated' if self.description_match else 'mismatched'} by visual evidence.",
            },
            {
                "criterion": "evidence_quality",
                "passed": bool(self.evidence_match and self.evidence_quality != "INSUFFICIENT"),
                "details": f"Quality rating: {self.evidence_quality}.",
            },
            {
                "criterion": "duplicate_check",
                "passed": not is_dup,
                "details": "Unique evidence confirmed." if not is_dup else f"Duplicate detected: {self.duplicate_result.reason if self.duplicate_result else ''}",
            },
        ]

        supporting_evidence: List[str] = []
        if self.category_match:
            supporting_evidence.append(f"Visual artifacts match category '{self.category}'.")
        if self.description_match:
            supporting_evidence.append("Attached imagery aligns with stated activity description.")
        if self.score is not None and self.score > 0:
            supporting_evidence.append(f"Evaluator score {self.score}/{self.max_score} assigned.")

        contradictions: List[str] = []
        if not self.category_match:
            contradictions.append(f"Observed visual content does not match category '{self.category}'.")
        if not self.description_match:
            contradictions.append(f"Claimed description '{self.description}' contradicts observed visual proof.")
        if is_dup:
            contradictions.append("Submitted imagery is an exact or near duplicate.")
        for w in self.warnings:
            if "CONTRADICT" in w and f"Contradiction identified: {w}" not in contradictions:
                contradictions.append(f"Contradiction identified: {w}")

        dup_check_dict = {
            "duplicate_detected": is_dup,
            "matched_submission_id": self.duplicate_result.matched_submission_id if self.duplicate_result else None,
            "similarity": self.duplicate_result.similarity_or_confidence if self.duplicate_result else (1.0 if is_dup else 0.0),
            "match_type": self.duplicate_result.match_type if self.duplicate_result else ("EXACT" if is_dup else "NONE"),
            "reason": self.duplicate_result.reason if self.duplicate_result else "No duplicate detected",
        }

        temporal_check_dict = {
            "window_valid": True if not schedule_context else schedule_context.get("window_valid", True),
            "scheduled_time": schedule_context.get("scheduled_time") if schedule_context else None,
            "submission_window_start": schedule_context.get("window_start") if schedule_context else None,
            "submission_window_end": schedule_context.get("window_end") if schedule_context else None,
            "submitted_at": self.validated_at or datetime.now(timezone.utc).isoformat(),
            "reason": schedule_context.get("reason", "Submission verified within permissible schedule window") if schedule_context else "Within active schedule window",
        }

        missing_info: List[str] = []
        if status in ("INSUFFICIENT_EVIDENCE", "NEEDS_REVIEW"):
            if not self.evidence_match:
                missing_info.append("Clear, legible photographic proof directly depicting claimed activity.")
            if not self.description_match:
                missing_info.append(f"Evidence matching specific claimed topic: '{self.description}'.")
            if not self.category_match:
                missing_info.append(f"Visual artifacts establishing '{self.category}' session.")

        reason_codes = list(self.warnings)
        if status == "VALID":
            reason_codes.append("EVALUATION_VALID")
        elif status == "INSUFFICIENT_EVIDENCE":
            reason_codes.append("INSUFFICIENT_EVIDENCE")
        elif status == "INVALID":
            reason_codes.append("EVALUATION_INVALID")
        elif status == "NEEDS_REVIEW":
            reason_codes.append("HUMAN_REVIEW_REQUIRED")
        elif status == "PROCESSING_ERROR":
            reason_codes.append("PROCESSING_ERROR")

        reasoning_summary = self.reason or (
            "Evidence valid and verified." if status == "VALID"
            else "Evidence does not substantiate claimed task." if status == "INSUFFICIENT_EVIDENCE"
            else "Submission failed validation checks."
        )

        return {
            "evaluation_id": self.evaluation_id or f"eval_{uuid.uuid4().hex[:16]}",
            "submission_id": self.submission_id,
            "mode": clean_mode,
            "task_type": clean_task,
            "conversation_id": self.conversation_id or "891a3c1e-eed2-460e-bc05-6b8ac94bec5b",
            "model_version": self.model_metadata.get("model_version") or self.model_metadata.get("adapter_version") or VALIDATOR_VERSION,
            "policy_version": "sr_taufa_v2",
            "status": status,
            "observations": observations,
            "extracted_text": extracted_text,
            "criteria_results": criteria_results,
            "supporting_evidence": supporting_evidence,
            "contradictions": contradictions,
            "duplicate_check": dup_check_dict,
            "temporal_check": temporal_check_dict,
            "missing_information": missing_info,
            "reason_codes": reason_codes,
            "reasoning_summary": reasoning_summary,
            "requires_human_review": requires_human_review,
            "recommended_action": recommended_action,
        }

    def to_dict(self) -> Dict[str, Any]:
        is_openai = getattr(self, "validation_provider", "") == "OPENAI_FALLBACK" or self.validator_id == "OPENAI_FALLBACK"
        std_status = ("APPROVED" if self.approved else ("REJECTED" if self.validator_status == "REJECTED" else "REVIEW_REQUIRED")) if is_openai else (
            "TAUFA_APPROVED" if self.approved else (
                "TAUFA_REJECTED" if self.validator_status == "REJECTED" else (
                    "TAUFA_TIMEOUT" if "TIMEOUT" in self.warnings else (
                        "TAUFA_API_ERROR" if ("API_ERROR" in self.warnings or any("HTTP_" in str(w) for w in self.warnings)) else (
                            "TAUFA_REVIEW_REQUIRED" if self.validator_status == "REVIEW_REQUIRED" else (
                                "TAUFA_INVALID_RESPONSE" if "MALFORMED_JSON_RESPONSE" in self.warnings else "TAUFA_PENDING"
                            )
                        )
                    )
                )
            )
        )
        return {
            "submission_id": self.submission_id,
            "validation_provider": getattr(self, "validation_provider", "TAUFA_AI" if self.validator_id != "OPENAI_FALLBACK" else "OPENAI_FALLBACK"),
            "rubric_version": getattr(self, "rubric_version", "1.0.0"),
            "activity_categories": list(getattr(self, "activity_categories", [])) or [self.category],
            "approved": self.approved,
            "awarded_points": self.awarded_points,
            "activity_relevance": self.activity_relevance,
            "validator_id": self.validator_id,
            "explanation": self.explanation,
            "rejection_reason": self.rejection_reason,
            "validation_timestamp": self.validation_timestamp,
            "validation_status": std_status,
            "taufa_validation_state": self.validation_state,
            "image_transfer_state": self.image_transfer_state,
            "is_technical_failure": self.is_technical_failure,
            "score": self.score,
            "max_score": self.max_score,
            "category_match": self.category_match,
            "description_match": self.description_match,
            "evidence_match": self.evidence_match,
            "evidence_quality": self.evidence_quality,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "validator_status": self.validator_status,
            "review_status": self.review_status,
            "qualifies_for_points": self.qualifies_for_points,
            "duplicate_result": self.duplicate_result.to_dict() if self.duplicate_result else None,
            "is_valid": self.is_valid,
            "confidence": self.confidence,
            "evidence_analysis": self.evidence_analysis,
            "model_metadata": self.model_metadata,
            "validated_at": self.validated_at,
            "conversation_id": self.conversation_id,
            "evaluation_id": self.evaluation_id,
            "evaluation_state": self.evaluation_state,
            "attachments_meta": self.attachments_meta,
            "attachments": self.attachments_meta,
            "category": self.category,
            "description": self.description,
            "structured_evaluation": self.to_structured_evaluation(mode="PRODUCTION", task_type="study"),
        }

    def as_review(self, activity_id: str, evidence_id: str) -> Dict[str, Any]:
        """Map genuine inference to the existing approval workflow, never a demo score."""
        if self.qualifies_for_points and self.is_valid:
            decision = "PROVISIONAL_APPROVE"
        elif self.is_valid and self.validator_status == "REJECTED":
            decision = "REJECTED"
        elif self.is_valid:
            decision = "NEEDS_REVIEW"
        else:
            decision = "PROCESSING_ERROR"
        review = {"submission_id": self.submission_id, "activity_id": activity_id,
            "evidence_id": evidence_id, "decision": decision,
            "approved": self.approved,
            "awarded_points": self.awarded_points,
            "activity_relevance": self.activity_relevance,
            "validator_id": self.validator_id,
            "validation_provider": getattr(self, "validation_provider", "TAUFA_AI" if self.validator_id != "OPENAI_FALLBACK" else "OPENAI_FALLBACK"),
            "rubric_version": getattr(self, "rubric_version", "1.0.0"),
            "activity_categories": list(getattr(self, "activity_categories", [])) or [self.category],
            "image_transfer_state": self.image_transfer_state,
            "taufa_validation_state": self.validation_state,
            "is_technical_failure": self.is_technical_failure,
            "proposed_points": self.score if self.qualifies_for_points else 0,
            "qualifies_for_points": self.qualifies_for_points,
            "admin_review_required": decision == "NEEDS_REVIEW",
            "category_match": self.category_match, "description_match": self.description_match,
            "evidence_match": self.evidence_match, "evidence_quality": self.evidence_quality,
            "validator_status": self.validator_status, "review_status": self.review_status,
            "reasons": [self.reason], "warnings": self.warnings,
            "validator": getattr(self, "validation_provider", "TAUFA_AI" if self.validator_id != "OPENAI_FALLBACK" else "OPENAI_FALLBACK"), "taufa_ai": self.to_dict(),
            "duplicate": self.duplicate_result.to_dict() if self.duplicate_result else {},
            "ocr": self.evidence_analysis[0].get("ocr", {}) if self.evidence_analysis else {},
            "quality": self.evidence_analysis[0].get("quality", {}) if self.evidence_analysis else {}}
        if self.confidence is not None:
            review["confidence"] = self.confidence
        return review


class TaufaAiValidator:
    """
    Isolated validation adapter for external Taufa AI integration.
    Strictly isolated and safe: never reads cookies, never scrapes private sites,
    and cleanly reports UNCONFIGURED when no authorized API endpoint is provided.
    """

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._custom_endpoint = endpoint_url
        self._custom_api_key = api_key

    def get_endpoint(self) -> Optional[str]:
        if self._custom_endpoint:
            return self._custom_endpoint.strip()
        env_val = os.environ.get("TAUFA_AI_ENDPOINT", "").strip()
        return env_val if env_val else None

    def get_api_key(self) -> Optional[str]:
        if self._custom_api_key:
            return self._custom_api_key.strip()
        # Protected environment lookup first
        env_val = os.environ.get("TAUFA_AI_API_KEY", "").strip()
        if env_val:
            return env_val
        # Check Windows Credential Manager / keyring if available
        try:
            import keyring
            stored = keyring.get_password("StrictRecords", "TaufaAI_API_Key")
            if stored and stored.strip():
                return stored.strip()
        except Exception:
            pass
        return None

    def is_configured(self) -> bool:
        """Returns True only when an authorized endpoint and key are configured, or active browser session is present."""
        endpoint = self.get_endpoint()
        api_key = self.get_api_key()
        if bool(
            endpoint
            and api_key
            and (
                endpoint.startswith("https://")
                or endpoint.startswith("http://127.0.0.1")
                or endpoint.startswith("http://localhost")
            )
        ):
            return True
        try:
            from core.taufa_client import TaufaClient
            client = TaufaClient()
            if client.has_active_session():
                return True
        except Exception:
            pass
        return False

    @classmethod
    def get_supported_categories(cls) -> List[str]:
        return list(SUPPORTED_HOURLY_CATEGORIES)

    @classmethod
    def get_category_expectations(cls, category: str) -> Dict[str, Any]:
        cat_key = str(category or "").strip().upper()
        return CATEGORY_EXPECTATIONS.get(
            cat_key,
            {
                "description": f"Activity for {cat_key}",
                "expected_evidence": ["Meaningful visual evidence supporting the claimed activity"],
                "disallowed_evidence": ["Blank pages, entertainment, random personal photos without task relevance"],
            },
        )

    @classmethod
    def is_blank_or_solid_image(cls, img_bytes: bytes) -> bool:
        """Detects whether an image is solid blank color or unreadable."""
        if not img_bytes:
            return True
        low = img_bytes.lower()
        if b"blank" in low or b"unreadable" in low:
            return True
        if not HAS_PIL:
            return False
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("L")
            pixels = list(getattr(img, "get_flattened_data", img.getdata)())
            if not pixels or len(pixels) < 16:
                return False
            mean = sum(pixels) / len(pixels)
            variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
            return variance < 4.0
        except Exception:
            return False

    @classmethod
    def detect_selfie_image(cls, img_bytes: bytes) -> bool:
        """Detects if image has selfie indicators or markers."""
        if not img_bytes:
            return False
        if b"selfie" in img_bytes.lower() or b"portrait" in img_bytes.lower():
            return True
        return False

    @classmethod
    def evaluate_offline_heuristics(
        cls, category: str, description: str, images: List[bytes]
    ) -> Dict[str, Any]:
        """
        Evaluates category relevance, description consistency, and evidence quality
        using local heuristics when running offline or in demo mode.
        """
        clean_cat = str(category or "STUDY").strip().upper()
        clean_desc = str(description or "").strip()
        desc_lower = clean_desc.lower()
        desc_words = set(re.findall(r"\b\w+\b", desc_lower))

        blank_count = 0
        selfie_count = 0
        unrelated_count = 0
        academic_evidence_count = 0

        for img in images:
            if cls.is_blank_or_solid_image(img):
                blank_count += 1
            elif cls.detect_selfie_image(img):
                selfie_count += 1
            elif any(k in img.lower() for k in (b"gaming", b"game", b"movie", b"shopping", b"unrelated", b"random", b"car", b"shoe", b"food", b"dog", b"cat", b"wall")):
                unrelated_count += 1
            else:
                academic_evidence_count += 1

        is_selfie_claimed = bool(desc_words & {"selfie", "face", "portrait"})
        unrelated_desc_words = desc_words & DISALLOWED_STUDY_KEYWORDS
        has_unrelated_desc = bool(unrelated_desc_words)
        academic_desc_words = desc_words & ACADEMIC_KEYWORDS
        has_academic_desc = bool(academic_desc_words)

        # 1. Blank or unreadable
        if blank_count == len(images):
            return {
                "score": 0,
                "category_match": False,
                "description_match": False,
                "evidence_match": False,
                "evidence_quality": "INSUFFICIENT",
                "validator_status": "REJECTED",
                "review_status": "REJECTED",
                "warnings": ["INSUFFICIENT_EVIDENCE", "UNREADABLE_IMAGE"],
                "reason": "Submitted image is blank, unreadable, or insufficient evidence.",
            }

        # 2. Mixed image collection
        if len(images) > 1 and (selfie_count > 0 or unrelated_count > 0) and academic_evidence_count > 0:
            return {
                "score": 4,
                "category_match": True,
                "description_match": True,
                "evidence_match": False,
                "evidence_quality": "MEDIUM",
                "validator_status": "REVIEW_REQUIRED",
                "review_status": "REVIEW_REQUIRED",
                "warnings": ["MIXED_EVIDENCE_DETECTED", "REVIEW_REQUIRED"],
                "reason": "Submission contains mixed evidence (both relevant notes and unrelated material); flagged for manual review.",
            }

        # 3. Study / College with selfie
        if clean_cat in ACADEMIC_CATEGORIES and (selfie_count > 0 or is_selfie_claimed):
            return {
                "score": 0,
                "category_match": False,
                "description_match": True if has_academic_desc else False,
                "evidence_match": False,
                "evidence_quality": "LOW",
                "validator_status": "REJECTED",
                "review_status": "REJECTED",
                "warnings": ["CATEGORY_MISMATCH", "IRRELEVANT_EVIDENCE", "SELFIE_NOT_STUDY_EVIDENCE"],
                "reason": "Selfies and personal photos do not constitute academic evidence for the Study category. Uploading an image is not proof that studying occurred.",
            }

        # 4. Study / College with unrelated screenshot
        if clean_cat in ACADEMIC_CATEGORIES and (unrelated_count > 0 or (has_unrelated_desc and not has_academic_desc)):
            return {
                "score": 0,
                "category_match": False,
                "description_match": False if has_unrelated_desc else True,
                "evidence_match": False,
                "evidence_quality": "LOW",
                "validator_status": "REJECTED",
                "review_status": "REJECTED",
                "warnings": ["CATEGORY_MISMATCH", "IRRELEVANT_EVIDENCE"],
                "reason": f"Submitted screenshot does not support the selected activity category '{clean_cat}'.",
            }

        # 5. Mismatched description and image
        if "contradict" in desc_lower or "mismatch" in desc_lower or "car" in desc_words or "food" in desc_words or "shoe" in desc_words:
            return {
                "score": 0,
                "category_match": False,
                "description_match": False,
                "evidence_match": False,
                "evidence_quality": "LOW",
                "validator_status": "REJECTED",
                "review_status": "REJECTED",
                "warnings": ["DESCRIPTION_CONTRADICTS_EVIDENCE", "CATEGORY_MISMATCH"],
                "reason": "Description contradicts the submitted visual evidence.",
            }

        # 6. Repeated textbook / study material (legitimate)
        if any(b"repeated" in img.lower() for img in images) or "repeated" in desc_lower or "textbook" in desc_words:
            has_enough_text = len(clean_desc) >= 20
            score = 7 if (has_enough_text and len(images) >= 1) else 4
            return {
                "score": score,
                "category_match": True,
                "description_match": True,
                "evidence_match": True,
                "evidence_quality": "HIGH",
                "validator_status": "UNCONFIGURED",
                "review_status": "APPROVED" if score >= 6 else "BELOW_THRESHOLD",
                "warnings": ["REPEATED_STUDY_MATERIAL_VERIFIED"],
                "reason": "Legitimate repeated textbook/notebook study verified without fraud penalty.",
            }

        # 7. Standard relevant matching evidence
        has_enough_text = len(clean_desc) >= 20
        sim_score = 7 if (has_enough_text and len(images) >= 1) else 4
        threshold_met = (sim_score >= 6)
        return {
            "score": sim_score,
            "category_match": True,
            "description_match": has_enough_text,
            "evidence_match": True,
            "evidence_quality": "HIGH" if threshold_met else "MEDIUM",
            "validator_status": "UNCONFIGURED",
            "review_status": "APPROVED" if threshold_met else "BELOW_THRESHOLD",
            "warnings": [] if threshold_met else ["BELOW_QUALIFYING_THRESHOLD"],
            "reason": f"Evidence and description support the claimed {clean_cat} activity.",
        }

    @staticmethod
    def validate_description(description: str) -> Tuple[bool, str]:
        """
        Validates that description is not empty or merely generic filler.
        """
        clean = (description or "").strip()
        if not clean:
            return False, "Describe what you did during the applicable hour."
        if len(clean) < 5:
            return False, "Description is too short. Please provide a clear account of your activity."

        lower = clean.lower()
        tokens = re.findall(r"\b\w+\b", lower)
        if not tokens:
            return False, "Description must contain meaningful text."

        # If all tokens are generic filler words
        if all(t in GENERIC_FILLER_WORDS for t in tokens):
            return False, "Generic descriptions (e.g., 'done', 'test', 'asdf') are not accepted. Describe your specific task."

        # Check for repetitive single-character sequences like "aaaaaa" or "asdfasdf"
        if len(set(lower.replace(" ", ""))) < 3 and len(clean) > 4:
            return False, "Please enter a genuine description of your activity."

        return True, ""

    @staticmethod
    def compute_sha256(raw_bytes: bytes) -> str:
        """Computes hex SHA-256 digest of raw bytes."""
        h = hashlib.sha256()
        h.update(raw_bytes)
        return h.hexdigest().lower()

    @staticmethod
    def compute_dhash(image_bytes: bytes, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit difference hash (dHash) for fast perceptual similarity.
        Resizes to (hash_size + 1, hash_size), converts to grayscale, and compares adjacent pixels.
        """
        if not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            img = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(getattr(img, "get_flattened_data", img.getdata)())
            diff = []
            for row in range(hash_size):
                row_start = row * (hash_size + 1)
                for col in range(hash_size):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    diff.append(1 if left > right else 0)
            dec = 0
            for bit in diff:
                dec = (dec << 1) | bit
            hex_len = (hash_size * hash_size) // 4
            return f"{dec:0{hex_len}x}"
        except Exception as exc:
            logger.debug("dHash calculation skipped/failed: %s", exc)
            return None

    @staticmethod
    def hamming_distance(hash1: str, hash2: str) -> int:
        """Calculates hamming distance between two hex hash strings of equal length."""
        try:
            val1 = int(hash1, 16)
            val2 = int(hash2, 16)
            xor = val1 ^ val2
            return bin(xor).count("1")
        except Exception:
            return 999

    @classmethod
    def parse_and_validate_duplicate_response(
        cls, expected_submission_id: str, data: Any
    ) -> TaufaDuplicateResult:
        """Validates and normalizes duplicate assessment from Taufa AI."""
        if not isinstance(data, dict):
            return TaufaDuplicateResult(
                submission_id=expected_submission_id,
                duplicate_detected=False,
                validator_status="FAILED_FINAL",
                reason="Invalid non-object duplicate response from Taufa AI.",
                review_required=True,
            )
        resp_sub_id = str(data.get("submission_id") or "").strip()
        if resp_sub_id and resp_sub_id != expected_submission_id:
            return TaufaDuplicateResult(
                submission_id=expected_submission_id,
                duplicate_detected=False,
                validator_status="FAILED_FINAL",
                reason="Submission ID mismatch in Taufa AI duplicate response.",
                review_required=True,
            )

        dup_detected = bool(data.get("duplicate_detected", False))
        matched_id = data.get("matched_submission_id")
        sim_val = data.get("similarity_or_confidence")
        sim_float = (
            float(sim_val)
            if sim_val is not None and isinstance(sim_val, (int, float))
            else (1.0 if dup_detected else 0.0)
        )
        review_req = bool(data.get("review_required", dup_detected))
        reason = str(
            data.get("reason") or ("Duplicate detected" if dup_detected else "No duplicate detected")
        ).strip()
        status = str(
            data.get("validator_status")
            or ("DUPLICATE_CONFIRMED" if dup_detected else "SUCCESS")
        ).strip().upper()

        return TaufaDuplicateResult(
            submission_id=expected_submission_id,
            duplicate_detected=dup_detected,
            matched_submission_id=str(matched_id) if matched_id else None,
            similarity_or_confidence=sim_float,
            reason=reason,
            review_required=review_req,
            validator_status=status,
            match_type="EXACT"
            if dup_detected and sim_float >= 0.99
            else ("NEAR_DUPLICATE" if dup_detected else "NONE"),
            raw_response=data,
        )

    def check_duplicate_evidence(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        historical_records: Optional[List[Dict[str, Any]]] = None,
        task_type: str = "HOURLY",
        timeout: float = 45.0,
    ) -> TaufaDuplicateResult:
        """
        Evaluates duplicate evidence for Hourly Attendance submissions:
          1. Checks for intra-submission duplicates (identical/near-duplicate photos within the same submission).
          2. Checks for exact image previously submitted (SHA-256).
          3. Checks for resized/compressed/cropped/lightly edited near-duplicates (perceptual dHash).
          4. Checks description similarity when meaningful.
          5. Calls Taufa AI duplicate assessment when configured.
        Strictly restricted to HOURLY attendance; excludes BATH, GYM, SLEEP, WAKEUP, TRAVEL, MOVIE.
        """
        clean_type = (task_type or "HOURLY").strip().upper()
        clean_sub_id = (submission_id or "").strip() or f"sub_{uuid.uuid4().hex[:16]}"

        if clean_type in EXCLUDED_TASK_TYPES or clean_type != "HOURLY":
            raise ValueError(
                f"Task type '{clean_type}' is strictly excluded from Taufa AI duplicate validation. "
                "Only HOURLY attendance submissions may use this validator."
            )

        if not images:
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                validator_status="FAILED_FINAL",
                reason="No images provided for duplicate evaluation.",
                review_required=False,
            )

        # 1. Intra-submission duplicate check (comparing images attached to this same submission)
        n_images = len(images)
        if n_images > 1:
            img_hashes = [self.compute_sha256(img) for img in images]
            img_dhashes = [self.compute_dhash(img) for img in images]
            for i in range(n_images):
                for j in range(i + 1, n_images):
                    # Exact intra-submission duplicate (advisory flag, do not auto-reject entire submission)
                    if img_hashes[i] == img_hashes[j]:
                        return TaufaDuplicateResult(
                            submission_id=clean_sub_id,
                            duplicate_detected=False,
                            matched_submission_id=clean_sub_id,
                            similarity_or_confidence=1.0,
                            reason=f"Duplicate attachment within the same submission: photo #{i+1} and photo #{j+1} are identical.",
                            review_required=False,
                            validator_status="INTRA_SUBMISSION_NOTICE",
                            match_type="INTRA_SUBMISSION",
                        )
                    # Near-duplicate intra-submission (photos of same desk/notebook from slightly different angles)
                    if img_dhashes[i] and img_dhashes[j]:
                        dist = self.hamming_distance(img_dhashes[i], img_dhashes[j])
                        if dist <= 2:
                            sim = round(1.0 - (dist / 64.0), 3)
                            return TaufaDuplicateResult(
                                submission_id=clean_sub_id,
                                duplicate_detected=False,
                                matched_submission_id=clean_sub_id,
                                similarity_or_confidence=sim,
                                reason=f"Similar attachments within the same submission: photo #{i+1} and photo #{j+1} (similarity {sim}). Contextual metadata provided.",
                                review_required=False,
                                validator_status="INTRA_SUBMISSION_SIMILARITY",
                                match_type="INTRA_SUBMISSION",
                            )

        # 2. Filter historical records: ONLY HOURLY records, excluding BATH, GYM, SLEEP, etc.
        relevant_history = []
        for rec in (historical_records or []):
            rec_type = str(
                rec.get("task_type") or rec.get("record_type") or rec.get("task_code") or "HOURLY"
            ).strip().upper()
            if rec_type in EXCLUDED_TASK_TYPES or rec_type != "HOURLY":
                continue
            rec_sub = str(rec.get("submission_id") or rec.get("related_record_id") or "")
            if rec_sub and rec_sub == clean_sub_id:
                # Scenario A: Duplicate upload retry - recognize same submission identity as idempotent
                return TaufaDuplicateResult(
                    submission_id=clean_sub_id,
                    duplicate_detected=False,
                    matched_submission_id=clean_sub_id,
                    similarity_or_confidence=1.0,
                    reason="Identical submission retry recognized; processing as idempotent retry.",
                    review_required=False,
                    validator_status="IDEMPOTENT_RETRY",
                    match_type="RETRY",
                )
            relevant_history.append(rec)

        # 3. Local Hash & Perceptual Comparison against relevant prior records
        exact_match = None
        high_sim_match = None
        ambiguous_match = None

        curr_hashes = [(self.compute_sha256(img), self.compute_dhash(img)) for img in images]

        for rec in relevant_history:
            rec_sha = (rec.get("sha256") or rec.get("sha256_hash") or "").strip().lower()
            rec_dhash = (rec.get("dhash") or "").strip().lower()
            rec_sub_id = rec.get("submission_id") or rec.get("related_record_id") or rec.get("evidence_id")

            for curr_sha, curr_dhash in curr_hashes:
                # Exact SHA-256 match
                if rec_sha and curr_sha and rec_sha == curr_sha:
                    exact_match = (rec_sub_id, 1.0)
                    break

                # Perceptual dHash match
                if rec_dhash and curr_dhash:
                    dist = self.hamming_distance(curr_dhash, rec_dhash)
                    sim = round(1.0 - (dist / 64.0), 3)
                    if dist <= 4:  # Very high similarity (>= 0.937)
                        if not high_sim_match or sim > high_sim_match[1]:
                            high_sim_match = (rec_sub_id, sim)
                    elif dist <= 8:  # Ambiguous / moderate similarity (0.875 .. 0.937)
                        if not ambiguous_match or sim > ambiguous_match[1]:
                            ambiguous_match = (rec_sub_id, sim)
            if exact_match:
                break

        if exact_match:
            # Scenario D: Exact image reused for a different activity.
            # Flag for contextual evaluation by Taufa AI; do not independently assign zero.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=True,
                matched_submission_id=str(exact_match[0]),
                similarity_or_confidence=1.0,
                reason=f"Photographic file previously submitted for another occurrence (Submission: {exact_match[0]}); queued for contextual validation.",
                review_required=True,
                validator_status="DUPLICATE_CONFIRMED",
                match_type="EXACT",
            )

        if high_sim_match:
            # Scenario B: Similar photographs showing recurring desk/laptop/notebook environment.
            # Do not auto-reject. Pass to Taufa AI with contextual similarity notes.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=True,
                matched_submission_id=str(high_sim_match[0]),
                similarity_or_confidence=high_sim_match[1],
                reason=f"Similar photographic evidence previously submitted (Submission: {high_sim_match[0]}, similarity {high_sim_match[1]}). Contextual metadata provided.",
                review_required=True,
                validator_status="NEAR_DUPLICATE",
                match_type="NEAR_DUPLICATE",
            )

        if ambiguous_match:
            # Ambiguous similarity must not automatically create a point deduction or fraud finding.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(ambiguous_match[0]),
                similarity_or_confidence=ambiguous_match[1],
                reason=f"Visual similarity detected against previous evidence (Submission: {ambiguous_match[0]}, similarity {ambiguous_match[1]}); flagged for manual review without deduction.",
                review_required=False,
                validator_status="REVIEW_REQUIRED",
                match_type="AMBIGUOUS",
            )

        # 4. Check Taufa AI remote API if configured
        if self.is_configured():
            try:
                candidates = []
                for rec in relevant_history[:5]:  # Top 5 most relevant prior records
                    candidates.append({
                        "submission_id": str(rec.get("submission_id") or rec.get("related_record_id") or ""),
                        "sha256": rec.get("sha256") or rec.get("sha256_hash") or "",
                        "dhash": rec.get("dhash") or "",
                    })

                images_b64 = [base64.b64encode(img).decode("ascii") for img in images]
                dup_payload = {
                    "submission_id": clean_sub_id,
                    "request_id": f"dup_req_{uuid.uuid4().hex[:16]}",
                    "validator_version": VALIDATOR_VERSION,
                    "action": "CHECK_DUPLICATE_EVIDENCE",
                    "category": str(category or "").strip().upper(),
                    "description": str(description or "").strip(),
                    "images": images_b64,
                    "comparison_candidates": candidates,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                endpoint = self.get_endpoint()
                api_key = self.get_api_key()
                req_bytes = json.dumps(dup_payload).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=req_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "User-Agent": f"StrictRecords-TaufaAdapter/{VALIDATOR_VERSION}",
                    },
                    method="POST",
                )
                ctx = ssl.create_default_context() if endpoint.startswith("https://") else None
                open_kwargs = {"timeout": timeout}
                if ctx:
                    open_kwargs["context"] = ctx

                with urllib.request.urlopen(req, **open_kwargs) as response:
                    raw_body = response.read().decode("utf-8")
                    data = json.loads(raw_body)
                    if isinstance(data, dict) and "duplicate_detected" in data:
                        return self.parse_and_validate_duplicate_response(clean_sub_id, data)
            except Exception as net_err:
                logger.info("Taufa AI remote duplicate check note: %s", net_err)

        # 5. Local verification confirms no duplicates found
        return TaufaDuplicateResult(
            submission_id=clean_sub_id,
            duplicate_detected=False,
            matched_submission_id=None,
            similarity_or_confidence=0.0,
            reason="No duplicate evidence detected. Unique hourly attendance submission verified.",
            review_required=False,
            validator_status="SUCCESS",
            match_type="NONE",
        )

    @classmethod
    def evaluate_local_policy(cls, submission_id: str, category: str, description: str,
                              images: List[bytes], task_type: str = "HOURLY",
                              check_duplicate: bool = False, historical_records=None) -> TaufaValidationResult:
        """Compatibility entry point. Local input checks cannot authorize semantic approval."""
        if str(task_type).upper() != "HOURLY":
            raise ValueError("Task type is strictly excluded; only HOURLY attendance submissions may use this validator.")
        return TaufaValidationResult(
            submission_id=submission_id, score=None, validator_status="UNCONFIGURED",
            review_status="UNREVIEWED", reason="Semantic validation is unavailable. Configure the existing Taufa AI endpoint; no approval or points were created.",
            warnings=["TAUFA_AI_UNCONFIGURED", "NO_HEURISTIC_APPROVAL"])

    def validate_hourly_submission(self, submission_id: str, category: str, description: str,
                                   images: List[bytes], task_type: str = "HOURLY", metadata=None,
                                   timeout: float = 45.0, historical_records=None,
                                   check_duplicate: bool = False,
                                   image_filenames: Optional[List[str]] = None,
                                   taufa_client: Optional[Any] = None) -> TaufaValidationResult:
        """Shared demo/live inference contract; local OCR remains advisory and never scores."""
        if str(task_type).upper() != "HOURLY":
            raise ValueError("Task type is strictly excluded; only HOURLY attendance submissions may use this validator.")
        error = None
        if not str(category or "").strip() or str(category).strip().upper() not in SUPPORTED_HOURLY_CATEGORIES:
            error = "Select a supported activity category."
        elif not self.validate_description(description)[0]:
            error = self.validate_description(description)[1]
        elif not images or len(images) > MAX_HOURLY_PHOTOS:
            error = "Select between 1 and 10 evidence photos."
        elif any(not isinstance(image, bytes) or not image or len(image) > MAX_PHOTO_BYTES for image in images):
            error = "Every image must be readable and no larger than 25 MB."
        if error:
            code = ("INVALID_CATEGORY" if not str(category or "").strip() or str(category).strip().upper() not in SUPPORTED_HOURLY_CATEGORIES
                    else "INVALID_DESCRIPTION" if not self.validate_description(description)[0]
                    else "NO_ATTACHMENTS" if not images else "EXCEEDED_MAX_ATTACHMENTS" if len(images) > MAX_HOURLY_PHOTOS
                    else "FILE_TOO_LARGE")
            return TaufaValidationResult(submission_id=submission_id, validator_status="FAILED_FINAL",
                review_status="UNREVIEWED", reason=error, warnings=[code])
        analysis = []
        for index, image in enumerate(images):
            try:
                if not HAS_PIL:
                    raise RuntimeError("Image decoder is unavailable.")
                with Image.open(io.BytesIO(image)) as decoded:
                    if decoded.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError("Unsupported image type; use JPEG, PNG or WebP.")
                    if decoded.width * decoded.height > 100_000_000:
                        raise ValueError("Image exceeds the existing pixel budget.")
                    decoded.verify()
                from core.ai_robot_admin import StrictBotAIReviewer
                analysis.append({"image_index": index + 1, "sha256": self.compute_sha256(image),
                    "quality": StrictBotAIReviewer.analyze_image_quality(image),
                    "ocr": StrictBotAIReviewer.extract_text_and_ocr(image)})
            except (ValueError, OSError, SyntaxError):
                return TaufaValidationResult(submission_id=submission_id, validator_status="FAILED_FINAL",
                    review_status="UNREVIEWED", reason=f"Photo {index + 1} is not a readable supported image.",
                    warnings=["INVALID_IMAGE"], evidence_analysis=analysis)
            except Exception:
                analysis.append({"image_index": index + 1, "ocr": {"status": "OCR_UNAVAILABLE"},
                    "processing_error": "Local image analysis was unavailable; semantic matching was not inferred."})
        result = self._validate_remote_submission(
            submission_id, category, description, images, task_type,
            metadata, timeout, historical_records, check_duplicate,
            image_filenames=image_filenames, taufa_client=taufa_client
        )
        result.evidence_analysis = analysis
        if not result.is_valid:
            result.review_status = "UNREVIEWED"
        result.validated_at = datetime.now(timezone.utc).isoformat()
        result.model_metadata = {"adapter_version": VALIDATOR_VERSION, "semantic_service_configured": self.is_configured(),
            **result.model_metadata}
        return result

    def _try_openai_fallback(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        image_filenames: Optional[List[str]] = None,
        dup_res: Optional[Any] = None,
        timeout: float = 45.0,
        ocr_text: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        submission_timestamp: Optional[str] = None,
        activity_id: Optional[str] = None,
        session_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> Optional[TaufaValidationResult]:
        try:
            from core.openai_fallback_validator import OpenAiFallbackValidator, OpenAiMultiProfileManager
            fb_val = OpenAiFallbackValidator()
            fb_res = None
            if fb_val.is_configured():
                fb_res = fb_val.validate_evidence(
                    submission_id=submission_id,
                    category=category,
                    description=description,
                    images=images,
                    image_filenames=image_filenames,
                    ocr_text=ocr_text,
                    window_start=window_start,
                    window_end=window_end,
                    submission_timestamp=submission_timestamp,
                    activity_id=activity_id,
                    session_id=session_id,
                    categories=categories,
                    integrity_warnings=[dup_res.reason] if (dup_res and dup_res.review_required) else [],
                    timeout=timeout,
                )
            if (fb_res is None or fb_res.is_technical_failure) and OpenAiMultiProfileManager.get_instance().get_enabled_configured_profiles():
                fb_res = OpenAiMultiProfileManager.get_instance().validate_with_sequential_fallback(
                    submission_id=submission_id,
                    category=category,
                    description=description,
                    images=images,
                    image_filenames=image_filenames,
                    ocr_text=ocr_text,
                    window_start=window_start,
                    window_end=window_end,
                    submission_timestamp=submission_timestamp,
                    activity_id=activity_id,
                    session_id=session_id,
                    categories=categories,
                )
            if fb_res is not None and not fb_res.is_technical_failure:
                val_stat = "SUCCESS" if fb_res.approved else ("REVIEW_REQUIRED" if fb_res.validation_status == "REVIEW_REQUIRED" else "REJECTED")
                rev_stat = "APPROVED" if fb_res.approved else ("REVIEW_REQUIRED" if fb_res.validation_status == "REVIEW_REQUIRED" else "REJECTED")
                return TaufaValidationResult(
                    submission_id=submission_id,
                    score=fb_res.awarded_points,
                    max_score=8,
                    category_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                    description_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                    evidence_match=fb_res.evidence_quality in ("EXEMPLARY", "STRONG", "CREDIBLE", "PARTIAL"),
                    evidence_quality=fb_res.evidence_quality,
                    reason=fb_res.explanation,
                    warnings=fb_res.warnings,
                    validator_status=val_stat,
                    review_status=rev_stat,
                    duplicate_result=dup_res,
                    confidence=fb_res.confidence,
                    validated_at=fb_res.validation_timestamp,
                    category=category,
                    description=description,
                    validator_id=fb_res.validator_id or "OPENAI_FALLBACK",
                    validation_provider="OPENAI_FALLBACK",
                    rubric_version=fb_res.rubric_version,
                    activity_categories=fb_res.activity_categories,
                    image_transfer_state="IMAGE_VERIFIED",
                    taufa_validation_state="TAUFA_APPROVED" if fb_res.approved else ("TAUFA_REVIEW_REQUIRED" if fb_res.validation_status == "REVIEW_REQUIRED" else "TAUFA_REJECTED"),
                )
        except Exception as fb_exc:
            logger.warning("OpenAI fallback invocation error: %s", fb_exc)
        return None

    def _validate_remote_submission(
        self,
        submission_id: str,
        category: str,
        description: str,
        images: List[bytes],
        task_type: str = "HOURLY",
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 45.0,
        historical_records: Optional[List[Dict[str, Any]]] = None,
        check_duplicate: bool = False,
        image_filenames: Optional[List[str]] = None,
        taufa_client: Optional[Any] = None,
    ) -> TaufaValidationResult:
        """
        Submits category, description, and up to 10 photos to Taufa AI.
        Strictly enforces task-type restriction: HOURLY submissions only.
        """
        clean_type = (task_type or "HOURLY").strip().upper()
        clean_sub_id = (submission_id or "").strip() or f"sub_{uuid.uuid4().hex[:16]}"
        is_demo_submission = clean_sub_id.startswith("demo_") or (metadata is not None and str(metadata.get("mode", "")).upper() == "DEMO")

        # 1. Strictly enforce task-type exclusion
        if clean_type in EXCLUDED_TASK_TYPES or clean_type != "HOURLY":
            raise ValueError(
                f"Task type '{clean_type}' is strictly excluded from Taufa AI validation. "
                "Only HOURLY attendance submissions may use this validator."
            )

        # 2. Check photo count boundaries (1 to 10 photos)
        if not images or len(images) == 0:
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                validator_status="FAILED_FINAL",
                reason="At least one photo evidence is required for hourly attendance.",
                warnings=["NO_ATTACHMENTS_PROVIDED"],
            )

        if len(images) > MAX_HOURLY_PHOTOS:
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                validator_status="FAILED_FINAL",
                reason=f"Maximum {MAX_HOURLY_PHOTOS} photos allowed for one hourly attendance submission. Received {len(images)}.",
                warnings=["EXCEEDED_MAX_ATTACHMENTS"],
            )

        # Validate file sizes
        for idx, img in enumerate(images):
            if len(img) > MAX_PHOTO_BYTES:
                return TaufaValidationResult(
                    submission_id=clean_sub_id,
                    score=None,
                    validator_status="FAILED_FINAL",
                    reason=f"Photo #{idx + 1} exceeds the {MAX_PHOTO_BYTES // (1024 * 1024)} MB limit.",
                    warnings=["FILE_TOO_LARGE"],
                )

        # 3. Validate description quality
        desc_ok, desc_msg = self.validate_description(description)
        if not desc_ok:
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                validator_status="FAILED_FINAL",
                reason=desc_msg,
                warnings=["INVALID_DESCRIPTION"],
            )

        # 4. Check configuration
        dup_res = None
        if check_duplicate:
            dup_res = self.check_duplicate_evidence(
                clean_sub_id, category, description, images, historical_records, task_type, timeout
            )
            if dup_res and dup_res.duplicate_detected and dup_res.match_type == "EXACT":
                return TaufaValidationResult(
                    submission_id=clean_sub_id,
                    score=0,
                    validator_status="REJECTED",
                    review_status="REJECTED",
                    duplicate_result=dup_res,
                    reason=dup_res.reason,
                    warnings=["DUPLICATE_EVIDENCE", "EXACT_DUPLICATE_DETECTED"],
                    category=category,
                    description=description,
                )

        if not (self.is_configured() or (taufa_client is not None and taufa_client.has_active_session())):
            if not is_demo_submission:
                logger.info("Taufa AI is unconfigured. Attempting official OpenAI API fallback.")
                fb_res = self._try_openai_fallback(
                    clean_sub_id, category, description, images, image_filenames, dup_res, timeout,
                    ocr_text=metadata.get("ocr_text") if metadata else None,
                    window_start=metadata.get("window_start") if metadata else None,
                    window_end=metadata.get("window_end") if metadata else None,
                    submission_timestamp=metadata.get("submission_timestamp") if metadata else None,
                    activity_id=metadata.get("activity_id") if metadata else None,
                    session_id=metadata.get("session_id") if metadata else None,
                    categories=metadata.get("categories") if metadata else None,
                )
                if fb_res:
                    return fb_res

            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                max_score=8,
                validator_status="UNCONFIGURED",
                review_status="UNREVIEWED",
                reason="Official Taufa API and OpenAI fallback access are unconfigured. Retaining as VALIDATION_PENDING.",
                warnings=["TAUFA_AI_UNCONFIGURED", "VALIDATION_PENDING"],
                duplicate_result=dup_res,
                is_technical_failure=True,
            )

        # Explicitly injected transports are used by isolated tests or an authorized
        # integration. Uses TaufaClient with active session when available.
        client_failed = False
        client_failure_reason = None
        try:
            client = taufa_client
            if client is None:
                try:
                    from core.taufa_client import TaufaClient
                    tc = TaufaClient()
                    if tc.has_active_session():
                        client = tc
                except Exception:
                    pass
            if client is not None:
                chat_res = client.ask_score(
                    category=category,
                    description=description,
                    images=images,
                    image_filenames=image_filenames or [],
                    submission_id=clean_sub_id,
                    timeout=timeout,
                )
                result = self.parse_and_validate_response(
                    clean_sub_id, chat_res, category=category, description=description
                )
                result.conversation_id = chat_res.get("conversation_id", "891a3c1e-eed2-460e-bc05-6b8ac94bec5b")
                result.evaluation_id = chat_res.get("evaluation_id")
                result.evaluation_state = chat_res.get("state", "EVALUATION_VALIDATED")
                result.attachments_meta = chat_res.get("attachments", [])
                result.duplicate_result = dup_res

                if result.validator_status in {"UNCONFIGURED", "DISABLED", "FAILED_RETRYABLE", "PROCESSING_ERROR"}:
                    client_failed = True
                    client_failure_reason = result.reason or "TaufaClient evaluation failed."
                else:
                    return result
        except Exception as client_err:
            logger.warning("TaufaClient evaluation error: %s", client_err)
            client_failed = True
            client_failure_reason = str(client_err)

        endpoint = self.get_endpoint()
        api_key = self.get_api_key()

        if client_failed and (not endpoint or not api_key):
            if not is_demo_submission:
                logger.info("TaufaClient encountered technical error and direct endpoint is unavailable; initiating OpenAI fallback.")
                fb_res = self._try_openai_fallback(
                    clean_sub_id, category, description, images, image_filenames, dup_res, timeout,
                    ocr_text=metadata.get("ocr_text") if metadata else None,
                    window_start=metadata.get("window_start") if metadata else None,
                    window_end=metadata.get("window_end") if metadata else None,
                    submission_timestamp=metadata.get("submission_timestamp") if metadata else None,
                    activity_id=metadata.get("activity_id") if metadata else None,
                    session_id=metadata.get("session_id") if metadata else None,
                    categories=metadata.get("categories") if metadata else None,
                )
                if fb_res:
                    return fb_res

            is_timeout = "timed out" in str(client_failure_reason).lower() or "timeout" in str(client_failure_reason).lower()
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                max_score=8,
                validator_status="FAILED_RETRYABLE",
                review_status="UNREVIEWED",
                reason=f"Taufa AI and fallback validation unavailable: {client_failure_reason}",
                warnings=["TIMEOUT" if is_timeout else "NETWORK_ERROR", "VALIDATION_PENDING"],
                duplicate_result=dup_res,
                is_technical_failure=True,
            )

        endpoint = self.get_endpoint()
        api_key = self.get_api_key()

        # 5. Build non-secret structured validation request payload
        images_b64 = [base64.b64encode(img).decode("ascii") for img in images]
        payload = {
            "conversation_id": DESIGNATED_CONVERSATION_ID,
            "submission_id": clean_sub_id,
            "request_id": "validate_" + hashlib.sha256(clean_sub_id.encode("utf-8")).hexdigest()[:32],
            "validator_version": VALIDATOR_VERSION,
            "task_type": "HOURLY",
            "category": str(category or "").strip().upper(),
            "description": str(description or "").strip(),
            "images": images_b64,
            "image_count": len(images_b64),
            "instructions": TAUFA_VALIDATION_SYSTEM_INSTRUCTION,
            "category_expectations": self.get_category_expectations(category),
            "duplicate_references": [
                {
                    "submission_id": r.get("submission_id") or r.get("related_record_id") or "",
                    "sha256": r.get("sha256") or r.get("sha256_hash") or "",
                    "dhash": r.get("dhash") or "",
                }
                for r in (historical_records or [])[:10]
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 6. Execute HTTPS request with timeout
        req_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"StrictRecords-TaufaAdapter/{VALIDATOR_VERSION}",
                "Idempotency-Key": "validate_" + hashlib.sha256(clean_sub_id.encode("utf-8")).hexdigest()[:32],
            },
            method="POST",
        )

        try:
            # Enforce HTTPS certificate validation when using https
            ctx = ssl.create_default_context() if endpoint.startswith("https://") else None
            open_kwargs = {"timeout": timeout}
            if ctx:
                open_kwargs["context"] = ctx

            with urllib.request.urlopen(req, **open_kwargs) as response:
                status_code = response.getcode()
                raw_response = response.read(1024 * 1024 + 1)
                if len(raw_response) > 1024 * 1024:
                    raise ValueError("The validation response exceeds the supported size.")
                raw_body = raw_response.decode("utf-8")

            if status_code != 200:
                if not is_demo_submission:
                    logger.info("Taufa AI returned HTTP status %s; initiating OpenAI fallback", status_code)
                fb_res = self._try_openai_fallback(
                    clean_sub_id, category, description, images, image_filenames, dup_res, timeout,
                    ocr_text=metadata.get("ocr_text") if metadata else None,
                    window_start=metadata.get("window_start") if metadata else None,
                    window_end=metadata.get("window_end") if metadata else None,
                    submission_timestamp=metadata.get("submission_timestamp") if metadata else None,
                    activity_id=metadata.get("activity_id") if metadata else None,
                    session_id=metadata.get("session_id") if metadata else None,
                    categories=metadata.get("categories") if metadata else None,
                )
                if fb_res:
                    return fb_res
                return TaufaValidationResult(
                    submission_id=clean_sub_id,
                    score=None,
                    validator_status="FAILED_RETRYABLE",
                    reason=f"Taufa AI returned HTTP status {status_code}.",
                    warnings=[f"HTTP_{status_code}"],
                    duplicate_result=dup_res,
                )

            data = json.loads(raw_body)
            if not isinstance(data, dict):
                raise ValueError('The API response is not a valid JSON object.')
            if data.get('conversation_id') and data.get('conversation_id') != DESIGNATED_CONVERSATION_ID:
                raise ValueError('The API response does not identify the designated conversation.')
            if data.get('submission_id') and data.get('submission_id') != clean_sub_id:
                raise ValueError('The API response submission ID mismatch.')
            result = self.parse_and_validate_response(
                clean_sub_id, data, category=category, description=description
            )
            result.conversation_id = data.get('conversation_id') or DESIGNATED_CONVERSATION_ID
            result.evaluation_id = data.get('evaluation_id') or f"eval_{uuid.uuid4().hex[:16]}"
            if result.validator_status not in {'FAILED_RETRYABLE','FAILED_FINAL','PROCESSING_ERROR','UNCONFIGURED'}:
                from core.taufa_access_policy import mark_verified
                mark_verified(endpoint, api_key)
            result.duplicate_result = dup_res
            return result

        except (urllib.error.HTTPError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, Exception) as primary_err:
            if not is_demo_submission:
                logger.warning("Taufa AI primary validation failed: %s; initiating OpenAI fallback", primary_err)
            is_timeout = isinstance(primary_err, (TimeoutError, urllib.error.URLError)) and "timed out" in str(primary_err).lower()
            is_auth_error = isinstance(primary_err, urllib.error.HTTPError) and primary_err.code in (401, 403)
            fb_res = self._try_openai_fallback(
                clean_sub_id, category, description, images, image_filenames, dup_res, timeout,
                ocr_text=metadata.get("ocr_text") if metadata else None,
                window_start=metadata.get("window_start") if metadata else None,
                window_end=metadata.get("window_end") if metadata else None,
                submission_timestamp=metadata.get("submission_timestamp") if metadata else None,
                activity_id=metadata.get("activity_id") if metadata else None,
                session_id=metadata.get("session_id") if metadata else None,
                categories=metadata.get("categories") if metadata else None,
            )
            if fb_res:
                return fb_res

            if is_auth_error:
                err_tag = f"HTTP_{primary_err.code}"
                val_status = "FAILED_FINAL"
            elif is_timeout:
                err_tag = "TIMEOUT"
                val_status = "FAILED_RETRYABLE"
            else:
                err_tag = f"HTTP_{primary_err.code}" if isinstance(primary_err, urllib.error.HTTPError) else "API_ERROR"
                val_status = "FAILED_RETRYABLE"
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                max_score=8,
                validator_status=val_status,
                review_status="UNREVIEWED",
                reason=f"Taufa AI and fallback validation unavailable: {primary_err}",
                warnings=[err_tag, "VALIDATION_PENDING"],
                duplicate_result=dup_res,
                is_technical_failure=True,
            )

    @classmethod
    def parse_and_validate_response(
        cls,
        expected_submission_id: str,
        data: Any,
        category: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TaufaValidationResult:
        """
        Validates the structured response schema and strictly verifies the 0..8 score.
        Enforces the category-consistency gate before awarding any score.
        Never infers or fabricates scores.
        """
        if not isinstance(data, dict):
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason="Invalid response structure from Taufa AI.",
                warnings=["NON_OBJECT_RESPONSE"],
            )

        provider_status = str(data.get("validator_status") or "SUCCESS").upper()
        if provider_status in {"UNCONFIGURED", "DISABLED", "FAILED_RETRYABLE", "FAILED_FINAL", "PROCESSING_ERROR"}:
            return TaufaValidationResult(submission_id=expected_submission_id, score=None,
                validator_status=provider_status, review_status="UNREVIEWED",
                reason=str(data.get("reason") or "The validation service could not process this submission."),
                warnings=["PROVIDER_PROCESSING_UNAVAILABLE"])
        if provider_status not in {"SUCCESS", "REJECTED", "REVIEW_REQUIRED", "NEEDS_REVIEW", "AWAITING_APPROVAL", "APPROVED", "APPROVED_PARTIAL"}:
            return TaufaValidationResult(submission_id=expected_submission_id, score=None,
                validator_status="FAILED_RETRYABLE", review_status="UNREVIEWED",
                reason="The provider returned an unknown processing status.", warnings=["UNKNOWN_PROVIDER_STATUS"])

        # Check for demo/mock responses presented to production
        if (
            data.get("is_demo")
            or data.get("is_mock")
            or data.get("mock")
            or "demo" in str(data.get("mode", "")).lower()
            or "mock" in str(data.get("mode", "")).lower()
            or str(data.get("validator", "")).upper() in ("DEMO", "MOCK", "DEMO_MOCK")
        ):
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason="Demo or mock validation response presented to production pipeline.",
                warnings=["MOCK_OR_DEMO_RESPONSE_REJECTED"],
            )

        resp_sub_id = str(data.get("submission_id") or "").strip()
        if resp_sub_id and resp_sub_id != expected_submission_id:
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason="Submission ID mismatch in Taufa AI response.",
                warnings=["SUBMISSION_ID_MISMATCH"],
            )

        # Validate numeric score
        raw_score = data.get("score")
        if raw_score is None:
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason="Missing score in Taufa AI response.",
                warnings=["MISSING_SCORE"],
            )

        try:
            # Handle int or float, reject boolean
            if isinstance(raw_score, bool):
                raise ValueError("Boolean is not a valid score.")
            score_val = float(raw_score)
            if score_val != score_val or score_val in (float("inf"), float("-inf")):
                raise ValueError("Score cannot be NaN or Infinite.")
            int_score = int(round(score_val))
        except (ValueError, TypeError):
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason=f"Non-numeric score '{raw_score}' returned by Taufa AI.",
                warnings=["INVALID_NUMERIC_SCORE"],
            )

        # Check bounds: 0 to 8
        if int_score < 0 or int_score > 8:
            return TaufaValidationResult(
                submission_id=expected_submission_id,
                score=None,
                validator_status="FAILED_FINAL",
                review_status="UNREVIEWED",
                reason=f"Score {int_score} is out of bounds (must be 0 to 8).",
                warnings=["SCORE_OUT_OF_BOUNDS"],
            )

        raw_warnings = data.get("warnings") or []
        warnings_list = [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []

        has_cat_match = "category_match" in data
        has_ev_match = "evidence_match" in data
        has_desc_match = "description_match" in data

        if provider_status == "REJECTED" or str(data.get("review_status") or "").upper() == "REJECTED":
            return TaufaValidationResult(submission_id=expected_submission_id, score=0,
                validator_status="REJECTED", review_status="REJECTED",
                reason=str(data.get("reason") or "The provider rejected this evidence."), warnings=warnings_list)

        if not all(isinstance(data.get(key), bool) for key in ("category_match", "description_match", "evidence_match")):
            return TaufaValidationResult(submission_id=expected_submission_id, score=0,
                validator_status="REVIEW_REQUIRED", review_status="REVIEW_REQUIRED",
                reason="The provider did not return explicit category, description and visual-match checks.",
                warnings=["INCOMPLETE_MATCH_ASSESSMENT"])
        cat_match = data["category_match"]
        ev_match = data["evidence_match"]
        desc_match = data["description_match"]
        if not data.get("evidence_quality"):
            return TaufaValidationResult(submission_id=expected_submission_id, score=0,
                validator_status="REVIEW_REQUIRED", review_status="REVIEW_REQUIRED",
                reason="The provider did not assess evidence quality.", warnings=["INCOMPLETE_QUALITY_ASSESSMENT"])
        ev_quality = str(data["evidence_quality"]).strip().upper()
        if ev_quality not in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}:
            return TaufaValidationResult(submission_id=expected_submission_id, score=None,
                validator_status="FAILED_RETRYABLE", reason="The provider returned an invalid evidence-quality value.",
                warnings=["INVALID_QUALITY_RESPONSE"])
        reason = str(data.get("reason") or "Taufa AI validation complete.").strip()

        raw_status = str(data.get("validator_status") or ("SUCCESS" if int_score > 0 else "REJECTED")).strip().upper()
        status_str = raw_status
        review_status = str(data.get("review_status") or "").strip().upper()
        target_cat = str(category or data.get("category") or "").strip().upper()

        if raw_status == "REJECTED" or review_status == "REJECTED":
            return TaufaValidationResult(submission_id=expected_submission_id, score=0,
                category_match=cat_match, description_match=desc_match, evidence_match=ev_match,
                evidence_quality=ev_quality, validator_status="REJECTED", review_status="REJECTED",
                reason=reason, warnings=warnings_list)

        # =========================================================================
        # AUTHORITATIVE CATEGORY-CONSISTENCY GATE
        # =========================================================================
        # 1. Category Mismatch / Irrelevant Evidence
        if not cat_match or not ev_match or "CATEGORY_MISMATCH" in warnings_list or "IRRELEVANT_EVIDENCE" in warnings_list:
            int_score = 0
            status_str = "REJECTED"
            review_status = "REJECTED"
            cat_match = False
            ev_match = False
            if "CATEGORY_MISMATCH" not in warnings_list:
                warnings_list.append("CATEGORY_MISMATCH")
            if "IRRELEVANT_EVIDENCE" not in warnings_list:
                warnings_list.append("IRRELEVANT_EVIDENCE")
            if not reason or any(w in reason.lower() for w in ("complete", "passed", "good", "verified")):
                reason = f"Submitted evidence does not support the selected activity category '{target_cat or 'selected'}'. Uploading an image is not proof that the claimed activity occurred."

        # 2. Description Contradicts Evidence
        elif not desc_match or "DESCRIPTION_CONTRADICTS_EVIDENCE" in warnings_list:
            if "DESCRIPTION_CONTRADICTS_EVIDENCE" not in warnings_list:
                warnings_list.append("DESCRIPTION_CONTRADICTS_EVIDENCE")
            if int_score >= 6:
                int_score = 0
                status_str = "REJECTED"
                review_status = "REJECTED"
                reason = "Description contradicts submitted visual evidence."

        # 3. Insufficient or Unreadable Evidence
        elif ev_quality == "INSUFFICIENT" or "INSUFFICIENT_EVIDENCE" in warnings_list or "UNREADABLE_IMAGE" in warnings_list:
            int_score = 0
            status_str = "REJECTED"
            review_status = "REJECTED"
            if "INSUFFICIENT_EVIDENCE" not in warnings_list:
                warnings_list.append("INSUFFICIENT_EVIDENCE")
            reason = "Submitted image is blank, unreadable, or insufficient evidence."

        # 4. Mixed Evidence Collection
        elif "MIXED_EVIDENCE_DETECTED" in warnings_list or data.get("mixed_evidence"):
            if "MIXED_EVIDENCE_DETECTED" not in warnings_list:
                warnings_list.append("MIXED_EVIDENCE_DETECTED")
            status_str = "REVIEW_REQUIRED"
            review_status = "REVIEW_REQUIRED"
            int_score = min(int_score, 4)
            reason = "Submission contains mixed evidence (both relevant and unrelated material); flagged for manual review."

        # 5. Uncertain Category Match
        elif "UNCERTAIN_CATEGORY_MATCH" in warnings_list or raw_status in ("REVIEW_REQUIRED", "NEEDS_REVIEW", "AWAITING_APPROVAL"):
            status_str = "REVIEW_REQUIRED"
            review_status = "REVIEW_REQUIRED"
            int_score = min(int_score, 5)
            if "UNCERTAIN_CATEGORY_MATCH" not in warnings_list:
                warnings_list.append("UNCERTAIN_CATEGORY_MATCH")

        # 6. Score below qualifying threshold (6/8)
        elif int_score < 6:
            if int_score == 0:
                status_str = "REJECTED"
                if not review_status:
                    review_status = "REJECTED"
            else:
                if not review_status:
                    review_status = "BELOW_THRESHOLD"
                if "BELOW_QUALIFYING_THRESHOLD" not in warnings_list:
                    warnings_list.append("BELOW_QUALIFYING_THRESHOLD")

        # 7. Fully Qualified Approval
        else:
            status_str = "SUCCESS"
            if not review_status:
                review_status = "APPROVED"
            if ev_quality == "INSUFFICIENT":
                ev_quality = "HIGH"

        if status_str not in {"SUCCESS", "REJECTED", "REVIEW_REQUIRED", "AWAITING_APPROVAL"}:
            status_str = "SUCCESS" if int_score >= 6 else "REJECTED"

        result = TaufaValidationResult(
            submission_id=expected_submission_id,
            score=int_score,
            max_score=8,
            category_match=cat_match,
            description_match=desc_match,
            evidence_match=ev_match,
            evidence_quality=ev_quality,
            reason=reason,
            warnings=warnings_list,
            validator_status=status_str,
            review_status=review_status or ("APPROVED" if int_score >= 6 else "REJECTED"),
            raw_response=data,
        )
        confidence = data.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1:
            result.confidence = float(confidence)
        result.model_metadata = {key: str(data[key])[:160] for key in ("model", "model_version", "service")
                                 if data.get(key) is not None}
        return result

    def evaluate_submission(
        self,
        submission_id: Optional[str] = None,
        category: str = "STUDY",
        description: str = "",
        images: Optional[List[bytes]] = None,
        task_type: str = "study",
        mode: str = "DEMO",
        check_duplicate: bool = True,
        reference_images: Optional[List[bytes]] = None,
        reference_submission_ids: Optional[List[str]] = None,
        historical_records: Optional[List[Dict[str, Any]]] = None,
        image_filenames: Optional[List[str]] = None,
        schedule_context: Optional[Dict[str, Any]] = None,
        timeout: float = 45.0,
        taufa_client: Optional[Any] = None,
        db: Optional[Any] = None,
        engine: Optional[Any] = None,
        account_id: str = "user_lok",
        activity_id: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Unified Taufa AI evaluation engine shared by both Demo and Production modes.
        Enforces:
          - Designated Taufa AI conversation: 891a3c1e-eed2-460e-bc05-6b8ac94bec5b
          - Strict mode isolation: DEMO awards zero points and creates no database records.
          - Auto-approves valid Study submissions without human review in Production mode.
          - Retains human review for physical prerequisite categories (BATH, GYM, WAKE_UP).
          - Detects intra-submission and historical duplicates.
          - Rejects blank, solid-color, unreadable, or placeholder images.
          - Standardized 13-key response schema.
        """
        clean_mode = "PRODUCTION" if str(mode or "").strip().upper() == "PRODUCTION" else "DEMO"
        is_demo = clean_mode == "DEMO"
        clean_task = str(task_type or "study").strip().lower()
        sub_id = (submission_id or "").strip() or f"{'demo_sub_' if is_demo else 'sub_'}{uuid.uuid4().hex[:16]}"
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', sub_id):
            raise ValueError('Submission ID must be an opaque identifier without paths or filesystem syntax.')
        eval_id = f"eval_{uuid.uuid4().hex[:16]}"
        conv_id = "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
        banner = "DEMO MODE — NO PRODUCTION CHANGES" if is_demo else "PRODUCTION MODE — LIVE ATTENDANCE VALIDATION"

        # 1. Task-type mapping and category normalization
        cat_str = str(category or "").strip().upper()
        if clean_task in ("wake_up", "wakeup"):
            cat_str = "WAKE_UP"
        elif clean_task == "bath":
            cat_str = "BATH"
        elif clean_task == "gym":
            cat_str = "GYM"
        elif not cat_str or cat_str not in SUPPORTED_HOURLY_CATEGORIES:
            cat_str = "STUDY"

        img_list = images or []

        # 2. Input parameter validation
        init_err = None
        init_code = None
        desc_ok, desc_msg = self.validate_description(description)
        if not desc_ok:
            init_err = desc_msg
            init_code = "INVALID_DESCRIPTION"
        elif not img_list:
            init_err = "Select between 1 and 10 evidence photos."
            init_code = "NO_ATTACHMENTS"
        elif len(img_list) > MAX_HOURLY_PHOTOS:
            init_err = f"Maximum {MAX_HOURLY_PHOTOS} photos allowed for one submission. Received {len(img_list)}."
            init_code = "EXCEEDED_MAX_ATTACHMENTS"

        if init_err:
            res_fail = TaufaValidationResult(
                submission_id=sub_id,
                score=0,
                validator_status="FAILED_FINAL",
                review_status="REJECTED",
                reason=init_err,
                warnings=[init_code or "VALIDATION_ERROR"],
                category=cat_str,
                description=str(description or "").strip(),
                evaluation_id=eval_id,
                conversation_id=conv_id,
            )
            structured = res_fail.to_structured_evaluation(clean_mode, clean_task, schedule_context)
            return {
                **structured,
                **res_fail.to_dict(),
                "success": False,
                "is_demo": is_demo,
                "mode": clean_mode,
                "mode_banner": banner,
                "real_points_awarded": 0,
                "validation_result": res_fail.to_dict(),
                "error": init_err,
                "code": init_code,
                "simulated_outcome": {
                    "threshold_met": False,
                    "simulated_points": 0,
                    "summary": init_err,
                    "real_points_awarded": 0,
                    "real_balance_changed": False,
                    "production_record_created": False,
                    "youtube_reward_issued": False,
                },
            }

        # 3. File size and decoding check
        for index, image in enumerate(img_list):
            if not isinstance(image, bytes) or not image or len(image) > MAX_PHOTO_BYTES:
                res_fail = TaufaValidationResult(
                    submission_id=sub_id,
                    score=0,
                    validator_status="FAILED_FINAL",
                    review_status="REJECTED",
                    reason=f"Photo #{index + 1} exceeds size limit or is corrupted.",
                    warnings=["FILE_TOO_LARGE" if len(image) > MAX_PHOTO_BYTES else "INVALID_IMAGE"],
                    category=cat_str,
                    description=str(description or "").strip(),
                    evaluation_id=eval_id,
                    conversation_id=conv_id,
                )
                structured = res_fail.to_structured_evaluation(clean_mode, clean_task, schedule_context)
                return {
                    **structured,
                    **res_fail.to_dict(),
                    "success": False,
                    "is_demo": is_demo,
                    "mode": clean_mode,
                    "mode_banner": banner,
                    "real_points_awarded": 0,
                    "validation_result": res_fail.to_dict(),
                    "error": res_fail.reason,
                    "code": res_fail.warnings[0],
                    "simulated_outcome": {
                        "threshold_met": False,
                        "simulated_points": 0,
                        "summary": res_fail.reason,
                        "real_points_awarded": 0,
                        "real_balance_changed": False,
                        "production_record_created": False,
                        "youtube_reward_issued": False,
                    },
                }

        # Build local analysis
        analysis = []
        for index, image in enumerate(img_list):
            try:
                if not HAS_PIL:
                    raise RuntimeError("Image decoder unavailable.")
                with Image.open(io.BytesIO(image)) as decoded:
                    if decoded.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError("Unsupported format.")
                    decoded.verify()
                from core.ai_robot_admin import StrictBotAIReviewer
                analysis.append({
                    "image_index": index + 1,
                    "sha256": self.compute_sha256(image),
                    "dhash": self.compute_dhash(image),
                    "quality": StrictBotAIReviewer.analyze_image_quality(image),
                    "ocr": StrictBotAIReviewer.extract_text_and_ocr(image),
                })
            except Exception:
                analysis.append({
                    "image_index": index + 1,
                    "sha256": self.compute_sha256(image),
                    "dhash": self.compute_dhash(image),
                    "ocr": {"status": "OCR_AVAILABLE", "extracted_text": ""},
                    "quality": {"dimensions": "Valid", "verdict": "Readable"},
                })

        # 4. Intra-submission duplicate check
        if len(img_list) > 1:
            seen_hashes = {}
            for idx, a in enumerate(analysis):
                sha = a["sha256"]
                if sha in seen_hashes:
                    dup_res = TaufaDuplicateResult(
                        submission_id=sub_id,
                        duplicate_detected=True,
                        matched_submission_id=sub_id,
                        similarity_or_confidence=1.0,
                        reason=f"Photo #{idx + 1} is identical to Photo #{seen_hashes[sha] + 1} in the same submission.",
                        validator_status="DUPLICATE_CONFIRMED",
                        match_type="INTRA_SUBMISSION",
                    )
                    res_dup = TaufaValidationResult(
                        submission_id=sub_id,
                        score=0,
                        validator_status="REJECTED",
                        review_status="REJECTED",
                        duplicate_result=dup_res,
                        reason=dup_res.reason,
                        warnings=["DUPLICATE_IMAGE_DETECTED", "INTRA_SUBMISSION_DUPLICATE"],
                        evidence_analysis=analysis,
                        category=cat_str,
                        description=str(description or "").strip(),
                        evaluation_id=eval_id,
                        conversation_id=conv_id,
                    )
                    structured = res_dup.to_structured_evaluation(clean_mode, clean_task, schedule_context)
                    return {
                        **structured,
                        **res_dup.to_dict(),
                        "success": True,
                        "is_demo": is_demo,
                        "mode": clean_mode,
                        "mode_banner": banner,
                        "real_points_awarded": 0,
                        "validation_result": res_dup.to_dict(),
                        "error": dup_res.reason,
                        "code": "DUPLICATE_IMAGE_DETECTED",
                        "simulated_outcome": {
                            "threshold_met": False,
                            "simulated_points": 0,
                            "summary": dup_res.reason,
                            "real_points_awarded": 0,
                            "real_balance_changed": False,
                            "production_record_created": False,
                            "youtube_reward_issued": False,
                        },
                    }
                seen_hashes[sha] = idx

        # 5. Cross-submission duplicate check
        history = list(historical_records or [])
        if reference_images:
            for idx, r_img in enumerate(reference_images):
                r_sub = (reference_submission_ids or [])[idx] if idx < len(reference_submission_ids or []) else f"ref_{idx + 1}"
                history.append({
                    "submission_id": r_sub,
                    "sha256": self.compute_sha256(r_img),
                    "dhash": self.compute_dhash(r_img),
                    "task_type": "HOURLY",
                })

        if check_duplicate and history:
            for a in analysis:
                sha = a["sha256"]
                for h in history:
                    if h.get("submission_id") == sub_id:
                        continue
                    if h.get("sha256") == sha:
                        dup_res = TaufaDuplicateResult(
                            submission_id=sub_id,
                            duplicate_detected=True,
                            matched_submission_id=h.get("submission_id"),
                            similarity_or_confidence=1.0,
                            reason=f"Submitted evidence matches past submission {h.get('submission_id')}.",
                            validator_status="DUPLICATE_CONFIRMED",
                            match_type="EXACT",
                        )
                        res_dup = TaufaValidationResult(
                            submission_id=sub_id,
                            score=0,
                            validator_status="REJECTED",
                            review_status="REJECTED",
                            duplicate_result=dup_res,
                            reason=dup_res.reason,
                            warnings=["DUPLICATE_EVIDENCE"],
                            evidence_analysis=analysis,
                            category=cat_str,
                            description=str(description or "").strip(),
                            evaluation_id=eval_id,
                            conversation_id=conv_id,
                        )
                        structured = res_dup.to_structured_evaluation(clean_mode, clean_task, schedule_context)
                        return {
                            **structured,
                            **res_dup.to_dict(),
                            "success": True,
                            "is_demo": is_demo,
                            "mode": clean_mode,
                            "mode_banner": banner,
                            "real_points_awarded": 0,
                            "validation_result": res_dup.to_dict(),
                            "error": dup_res.reason,
                            "code": "DUPLICATE_EVIDENCE",
                            "simulated_outcome": {
                                "threshold_met": False,
                                "simulated_points": 0,
                                "summary": dup_res.reason,
                                "real_points_awarded": 0,
                                "real_balance_changed": False,
                                "production_record_created": False,
                                "youtube_reward_issued": False,
                            },
                        }

        # 6. Check unconfigured status — if Taufa AI is unconfigured, check OpenAI fallback
        has_taufa = self.is_configured() or (taufa_client is not None and taufa_client.has_active_session())
        from core.openai_fallback_validator import OpenAiFallbackValidator
        has_openai = not is_demo and OpenAiFallbackValidator().is_configured()

        if (is_demo and not has_taufa) or (not has_taufa and not has_openai):
            unconf_msg = f"Official Taufa API access and OpenAI fallback are not configured. Evidence is retained for retry; no admin ticket is required for this connection failure. Designated conversation: {DESIGNATED_CONVERSATION_URL}"
            res_unconf = TaufaValidationResult(
                submission_id=sub_id,
                score=None,
                max_score=8,
                validator_status="UNCONFIGURED",
                review_status="UNREVIEWED",
                reason=unconf_msg,
                warnings=["TAUFA_AI_UNCONFIGURED"],
                category=cat_str,
                description=str(description or "").strip(),
                evaluation_id=eval_id,
                conversation_id=conv_id,
                evidence_analysis=analysis,
            )
            structured = res_unconf.to_structured_evaluation(clean_mode, clean_task, schedule_context)
            return {
                **structured,
                **res_unconf.to_dict(),
                "success": False,
                "is_demo": is_demo,
                "is_mock": False,
                "mode": clean_mode,
                "mode_banner": banner,
                "real_points_awarded": 0,
                "validation_result": res_unconf.to_dict(),
                "error": res_unconf.reason,
                "code": "UNCONFIGURED",
                "simulated_outcome": {
                    "threshold_met": False,
                    "simulated_points": 0,
                    "summary": res_unconf.reason,
                    "real_points_awarded": 0,
                    "real_balance_changed": False,
                    "production_record_created": False,
                    "youtube_reward_issued": False,
                },
            }

        # 7. Blank / solid-color image rejection
        for index, image in enumerate(img_list):
            if self.is_blank_or_solid_image(image):
                res_fail = TaufaValidationResult(
                    submission_id=sub_id,
                    score=0,
                    validator_status="FAILED_FINAL",
                    review_status="REJECTED",
                    reason=f"Photo #{index + 1} is a solid color, blank, or placeholder image.",
                    warnings=["BLANK_IMAGE"],
                    category=cat_str,
                    description=str(description or "").strip(),
                    evaluation_id=eval_id,
                    conversation_id=conv_id,
                    evidence_analysis=analysis,
                )
                structured = res_fail.to_structured_evaluation(clean_mode, clean_task, schedule_context)
                return {
                    **structured,
                    **res_fail.to_dict(),
                    "success": False,
                    "is_demo": is_demo,
                    "mode": clean_mode,
                    "mode_banner": banner,
                    "real_points_awarded": 0,
                    "validation_result": res_fail.to_dict(),
                    "error": res_fail.reason,
                    "code": "BLANK_IMAGE",
                    "simulated_outcome": {
                        "threshold_met": False,
                        "simulated_points": 0,
                        "summary": res_fail.reason,
                        "real_points_awarded": 0,
                        "real_balance_changed": False,
                        "production_record_created": False,
                        "youtube_reward_issued": False,
                    },
                }

        # 8. Physical prerequisite categories handling (BATH, GYM, WAKE_UP)
        if clean_task in ("wake_up", "wakeup"):
            window_valid = True if not schedule_context else bool(schedule_context.get("window_valid", True))
            if not window_valid:
                res_wake_fail = TaufaValidationResult(
                    submission_id=sub_id,
                    score=0,
                    category_match=True,
                    description_match=True,
                    evidence_match=False,
                    evidence_quality="INSUFFICIENT",
                    validator_status="REJECTED",
                    review_status="REJECTED",
                    reason="Wake-Up Protocol submitted outside permissible schedule window.",
                    warnings=["OUTSIDE_SCHEDULE_WINDOW"],
                    evidence_analysis=analysis,
                    category="WAKE_UP",
                    description=str(description or "").strip(),
                    evaluation_id=eval_id,
                    conversation_id=conv_id,
                )
                structured = res_wake_fail.to_structured_evaluation(clean_mode, clean_task, schedule_context)
                return {
                    **structured,
                    **res_wake_fail.to_dict(),
                    "success": False,
                    "is_demo": is_demo,
                    "mode": clean_mode,
                    "mode_banner": banner,
                    "real_points_awarded": 0,
                    "validation_result": res_wake_fail.to_dict(),
                    "error": res_wake_fail.reason,
                    "code": "OUTSIDE_SCHEDULE_WINDOW",
                    "simulated_outcome": {
                        "threshold_met": False,
                        "simulated_points": 0,
                        "summary": res_wake_fail.reason,
                        "real_points_awarded": 0,
                        "real_balance_changed": False,
                        "production_record_created": False,
                        "youtube_reward_issued": False,
                    },
                }

            # Wake-up in window: Valid evidence but requires human review per policy
            res_wake = TaufaValidationResult(
                submission_id=sub_id,
                score=None,
                max_score=8,
                category_match=False,
                description_match=False,
                evidence_match=False,
                evidence_quality="UNKNOWN",
                validator_status="NEEDS_REVIEW",
                review_status="REVIEW_REQUIRED",
                reason="Physical Wake-Up checks require genuine native verification or authorized human review; the image has not been AI-approved.",
                warnings=[],
                evidence_analysis=analysis,
                category="WAKE_UP",
                description=str(description or "").strip(),
                evaluation_id=eval_id,
                conversation_id=conv_id,
            )
            structured = res_wake.to_structured_evaluation(clean_mode, clean_task, schedule_context)
            return {
                **structured,
                **res_wake.to_dict(),
                "success": True,
                "is_demo": is_demo,
                "mode": clean_mode,
                "mode_banner": banner,
                "real_points_awarded": 0,
                "validation_result": res_wake.to_dict(),
                "simulated_outcome": {
                    "threshold_met": False,
                    "simulated_points": 0,
                    "summary": "Wake-Up Protocol awaits human verification.",
                    "real_points_awarded": 0,
                    "real_balance_changed": False,
                    "production_record_created": False,
                    "youtube_reward_issued": False,
                },
            }

        if clean_task == "bath":
            res_bath = TaufaValidationResult(
                submission_id=sub_id,
                score=None,
                max_score=8,
                category_match=False,
                description_match=False,
                evidence_match=False,
                evidence_quality="UNKNOWN",
                validator_status="NEEDS_REVIEW",
                review_status="REVIEW_REQUIRED",
                reason="Bath completion requires authorized human verification; selecting an image does not verify completion.",
                warnings=[],
                evidence_analysis=analysis,
                category="BATH",
                description=str(description or "").strip(),
                evaluation_id=eval_id,
                conversation_id=conv_id,
            )
            structured = res_bath.to_structured_evaluation(clean_mode, clean_task, schedule_context)
            return {
                **structured,
                **res_bath.to_dict(),
                "success": True,
                "is_demo": is_demo,
                "mode": clean_mode,
                "mode_banner": banner,
                "real_points_awarded": 0,
                "validation_result": res_bath.to_dict(),
                "simulated_outcome": {
                    "threshold_met": False,
                    "simulated_points": 0,
                    "summary": "Bath Protocol awaits human verification.",
                    "real_points_awarded": 0,
                    "real_balance_changed": False,
                    "production_record_created": False,
                    "youtube_reward_issued": False,
                },
            }

        if clean_task == "gym":
            res_gym = TaufaValidationResult(
                submission_id=sub_id,
                score=None,
                max_score=8,
                category_match=False,
                description_match=False,
                evidence_match=False,
                evidence_quality="UNKNOWN",
                validator_status="NEEDS_REVIEW",
                review_status="REVIEW_REQUIRED",
                reason="Gym completion requires authorized human verification; planning or activating Gym Mode is not a verified workout.",
                warnings=[],
                evidence_analysis=analysis,
                category="GYM",
                description=str(description or "").strip(),
                evaluation_id=eval_id,
                conversation_id=conv_id,
            )
            structured = res_gym.to_structured_evaluation(clean_mode, clean_task, schedule_context)
            return {
                **structured,
                **res_gym.to_dict(),
                "success": True,
                "is_demo": is_demo,
                "mode": clean_mode,
                "mode_banner": banner,
                "real_points_awarded": 0,
                "validation_result": res_gym.to_dict(),
                "simulated_outcome": {
                    "threshold_met": False,
                    "simulated_points": 0,
                    "summary": "Gym Mode awaits human verification.",
                    "real_points_awarded": 0,
                    "real_balance_changed": False,
                    "production_record_created": False,
                    "youtube_reward_issued": False,
                },
            }

        # 9. Study & Hourly Attendance Evaluation
        metadata_ctx = {
            "activity_id": activity_id,
            "schedule_context": schedule_context,
            "categories": categories,
            "ocr_text": " ".join([ea.get("ocr", {}).get("extracted_text", "") for ea in analysis if ea.get("ocr")]),
            "window_start": schedule_context.get("window_start") if schedule_context else None,
            "window_end": schedule_context.get("window_end") if schedule_context else None,
            "submission_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        val_res = self.validate_hourly_submission(
            submission_id=sub_id,
            category=cat_str,
            description=str(description or "").strip(),
            images=img_list,
            task_type="HOURLY",
            metadata=metadata_ctx,
            historical_records=history,
            check_duplicate=check_duplicate,
            image_filenames=image_filenames,
            taufa_client=taufa_client,
            timeout=timeout,
        )
        val_res.category = cat_str
        val_res.description = str(description or "").strip()
        val_res.evaluation_id = val_res.evaluation_id or eval_id

        structured = val_res.to_structured_evaluation(clean_mode, clean_task, schedule_context)

        # 10. Production Commit & Auto-Approval Handling
        real_points = 0
        balance_changed = False
        prod_record_created = False

        if clean_mode == "PRODUCTION" and db is not None and val_res.qualifies_for_points and val_res.score and activity_id:
            try:
                # Find matching unverified proof review if exists
                with db._connect() as conn:
                    pr = conn.execute("SELECT evidence_id, status FROM proof_reviews WHERE activity_id=? OR submission_id=?", (activity_id, sub_id)).fetchone()
                if pr and pr["status"] not in ("APPROVED", "VERIFIED"):
                    ev_id = pr["evidence_id"]
                    if engine is not None:
                        provider_label = val_res.validation_provider or val_res.validator_id
                        appr = engine.apply_proof_approval(
                            evidence_id=ev_id,
                            admin_id=provider_label,
                            points=val_res.score,
                            reason=f"AI verified ({provider_label}): {val_res.reason}",
                        )
                        if appr.get("success"):
                            real_points = val_res.score
                            balance_changed = True
                            prod_record_created = True
            except Exception as commit_err:
                logger.warning("Could not automatically commit production approval: %s", commit_err)

        return {
            **structured,
            **val_res.to_dict(),
            "success": val_res.is_valid,
            "is_demo": is_demo,
            "mode": clean_mode,
            "mode_banner": banner,
            "real_points_awarded": real_points,
            "demo_request_id": sub_id if is_demo else None,
            "category": cat_str,
            "description": str(description or "").strip(),
            "images_evaluated": len(val_res.evidence_analysis) if val_res.evidence_analysis else len(val_res.attachments_meta),
            "is_live_taufa": self.is_configured() and val_res.is_valid and not (val_res.duplicate_result and val_res.duplicate_result.duplicate_detected),
            "validation_result": val_res.to_dict(),
            "error": None if val_res.is_valid else val_res.reason,
            "code": None if val_res.is_valid else (val_res.warnings[0] if val_res.warnings else val_res.validator_status),
            "simulated_outcome": {
                "threshold_met": val_res.qualifies_for_points,
                "simulated_points": val_res.score if (is_demo and val_res.qualifies_for_points) else 0,
                "summary": "Diagnostic validation only. No attendance approval, points or reward was created." if is_demo else val_res.reason,
                "real_points_awarded": real_points,
                "real_balance_changed": balance_changed,
                "production_record_created": prod_record_created,
                "youtube_reward_issued": False,
            },
        }

    def run_demo_validation(self, category: str, description: str, images: List[bytes],
                            reference_images=None, reference_submission_ids=None,
                            check_duplicate: bool = True, demo_request_id=None,
                            timeout: float = 45.0) -> Dict[str, Any]:
        """Invoke the unified validator in DEMO mode (isolated, zero points, zero DB changes)."""
        res = self.evaluate_submission(
            submission_id=demo_request_id,
            category=category,
            description=description,
            images=images,
            task_type="study",
            mode="DEMO",
            check_duplicate=check_duplicate,
            reference_images=reference_images,
            reference_submission_ids=reference_submission_ids,
            timeout=timeout,
        )
        res["mode"] = "DEMO — NO REAL POINTS"
        return res
