"""
StrictRecords — Validation Rubric Loader
========================================
Loads version-controlled evidence validation rubric from validation/config/strictrecords_evidence_rubric.md.
Provides consistent rules to both Taufa AI validator and official OpenAI fallback validator.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("StrictRecords.RubricLoader")

DEFAULT_RUBRIC_PATH = Path("validation/config/strictrecords_evidence_rubric.md")
DEFAULT_RUBRIC_VERSION = "1.0.0"

_CACHED_RUBRIC: Tuple[str, str] | None = None


def get_rubric_file_path() -> Path:
    """Resolve absolute path to validation rubric markdown file."""
    # Check environment override
    env_path = os.environ.get("STRICTRECORDS_RUBRIC_FILE")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    # Standard workspace path
    if DEFAULT_RUBRIC_PATH.is_file():
        return DEFAULT_RUBRIC_PATH

    # Search up to parent directories if running from subdirectory or test directory
    curr = Path.cwd()
    for parent in [curr] + list(curr.parents)[:4]:
        candidate = parent / "validation" / "config" / "strictrecords_evidence_rubric.md"
        if candidate.is_file():
            return candidate

    return DEFAULT_RUBRIC_PATH


def load_validation_rubric(force_reload: bool = False) -> Tuple[str, str]:
    """
    Returns (rubric_text, rubric_version).
    Thread-safe cached read of the markdown rubric.
    """
    global _CACHED_RUBRIC
    if not force_reload and _CACHED_RUBRIC is not None:
        return _CACHED_RUBRIC

    path = get_rubric_file_path()
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
            version = DEFAULT_RUBRIC_VERSION
            ver_match = re.search(r"^Version:\s*([^\r\n]+)", content, re.MULTILINE | re.IGNORECASE)
            if ver_match:
                version = ver_match.group(1).strip()
            _CACHED_RUBRIC = (content, version)
            logger.info("Loaded StrictRecords evidence validation rubric version %s from %s", version, path)
            return _CACHED_RUBRIC
        except Exception as e:
            logger.warning("Could not read rubric file %s: %s; using embedded fallback", path, e)

    # Embedded fallback matching canonical rubric
    fallback = (
        "# StrictRecords Evidence Validation Rubric (Embedded Fallback)\n"
        "Version: 1.0.0-fallback\n\n"
        "SCORING SYSTEM FOR PROOF (0 to 8 points):\n"
        "Score\tWhat it looks like\n"
        "0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim\n"
        "1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content\n"
        "2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity\n"
        "3\tRight subject, but weak proof: recycled or identical evidence reused from another slot\n"
        "4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour\n"
        "5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports\n"
        "6\tAuthentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)\n"
        "7\tStrong, specific, timestamped evidence matching category and description closely\n"
        "8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point\n"
    )
    _CACHED_RUBRIC = (fallback, "1.0.0-fallback")
    return _CACHED_RUBRIC
