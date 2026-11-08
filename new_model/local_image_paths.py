"""One path boundary for local image inputs used by model and evidence code."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError


PROJECT_ROOT = Path(__file__).resolve().parent
MIB = 1024 * 1024
DEFAULT_IMAGE_ROOTS = (
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "TestImages",
    PROJECT_ROOT / "qwen_image_text" / "data",
)
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}


class LocalImageInputError(ValueError):
    """A safe, non-path-revealing validation error for local image input."""


def resolve_local_image(
    candidate: str | os.PathLike[str],
    *,
    allowed_roots: Iterable[str | os.PathLike[str]] = DEFAULT_IMAGE_ROOTS,
    relative_to: str | os.PathLike[str] = PROJECT_ROOT,
    max_bytes: int = 25 * MIB,
    max_pixels: int = 40_000_000,
) -> Path:
    """Resolve, confine and validate an image before returning its canonical path.

    Relative paths are based at ``relative_to``. The candidate must resolve below
    one of ``allowed_roots``. Symlinks are followed for containment checks, so a
    symlink that leaves an authorized root is rejected.
    """
    try:
        if not isinstance(candidate, (str, os.PathLike)):
            raise LocalImageInputError("The image input is invalid.")
        raw = os.fspath(candidate)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise LocalImageInputError("The image input is invalid.")
        if urlsplit(raw).scheme.lower() in {"http", "https", "file", "data"}:
            raise LocalImageInputError("Only local image files are supported.")
        # Reject traversal syntax even when normalization would land back inside a root.
        if ".." in re.split(r"[\\/]+", raw):
            raise LocalImageInputError("The image path is not allowed.")

        path = Path(raw)
        if not path.is_absolute():
            path = Path(relative_to) / path
        resolved = path.resolve(strict=True)
        roots = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
        if not any(resolved.is_relative_to(root) for root in roots):
            raise LocalImageInputError("The image path is outside the authorized image folders.")
        if not resolved.is_file():
            raise LocalImageInputError("The image file is unavailable.")
        size = resolved.stat().st_size
        if size <= 0 or size > max_bytes:
            raise LocalImageInputError("The image file is empty or exceeds the allowed size.")
        with Image.open(resolved) as image:
            if image.format not in SUPPORTED_FORMATS:
                raise LocalImageInputError("Only JPEG, PNG, and WebP images are supported.")
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise LocalImageInputError("The image dimensions exceed the allowed limit.")
            image.verify()
        return resolved
    except LocalImageInputError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise LocalImageInputError("The local image could not be read or validated.") from exc
