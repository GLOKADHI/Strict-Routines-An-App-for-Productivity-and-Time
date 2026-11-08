"""Bounded image preparation and immutable content-addressed evidence assets.

Original proof bytes and compact display copies have separate hashes. No model,
database, Windows configuration, or caller-owned input file is changed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from local_image_paths import DEFAULT_IMAGE_ROOTS, LocalImageInputError, resolve_local_image

MiB = 1024 * 1024
_CHUNK = 128 * 1024
_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
_VERSION = "evidence-storage-v1"


class StorageError(ValueError):
    def __init__(self, code: str, message: str, *, stage: str = "prepare", retryable: bool = False):
        super().__init__(message)
        self.code, self.stage, self.retryable = code, stage, retryable

    def as_dict(self) -> dict[str, Any]:
        return {"status": "failed", "code": self.code, "message": str(self), "stage": self.stage,
                "retryable": self.retryable}


@dataclass(frozen=True)
class CompressionPolicy:
    max_photo_edge: int = 1600
    jpeg_quality: int = 80
    ocr_edge: int = 2560
    thumbnail_edge: int = 320
    max_image_bytes: int = 25 * MiB
    max_aggregate_bytes: int = 125 * MiB
    max_files: int = 5
    max_pixels: int = 40_000_000

    def __post_init__(self):
        for key in ("max_photo_edge", "ocr_edge", "thumbnail_edge", "max_image_bytes", "max_aggregate_bytes", "max_files", "max_pixels"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{key} must be a positive integer.")
        if not isinstance(self.jpeg_quality, int) or not 1 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95.")
        if self.max_image_bytes > 25 * MiB or self.max_aggregate_bytes > 125 * MiB or self.max_files > 5 or self.max_pixels > 40_000_000:
            raise ValueError("Policy cannot exceed the evidence input safety limits.")
        if self.thumbnail_edge > self.max_photo_edge or self.max_photo_edge > self.ocr_edge:
            raise ValueError("Thumbnail and display edges must not exceed the OCR edge.")


@dataclass
class PreparedArtifact:
    metadata: dict[str, Any]
    vision_image: Image.Image
    ocr_image: Image.Image

    @property
    def image(self) -> Image.Image:
        return self.vision_image

    def close(self) -> None:
        seen = set()
        for image in (self.vision_image, self.ocr_image):
            if id(image) not in seen:
                image.close()
                seen.add(id(image))

    def __enter__(self) -> "PreparedArtifact":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def base64_encoded_size(size: int) -> int:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("Byte size must be a nonnegative integer.")
    return 4 * ((size + 2) // 3)


def estimate_base64_json_size(size: int, filename: str = "") -> int:
    envelope = json.dumps({"filename": filename, "image_base64": ""}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(envelope) + base64_encoded_size(size)


def preflight_sources(
    paths: Iterable[str | os.PathLike], policy: CompressionPolicy | None = None, *,
    transport: str = "binary", overhead_bytes: int = 1024, per_file_overhead_bytes: int = 256,
) -> dict[str, Any]:
    """Stat every occurrence before preparation; retain per-file failures.

    The wire estimate includes framing and filename bytes. Base64 transport is
    checked using its encoded size, not the smaller decoded byte count.
    """
    policy = policy or CompressionPolicy()
    if transport not in ("binary", "base64"):
        raise ValueError("Transport must be binary or base64.")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (overhead_bytes, per_file_overhead_bytes)):
        raise ValueError("Transport overhead must be nonnegative.")
    sources, aggregate, encoded, framing = [], 0, 0, overhead_bytes
    for index, raw_path in enumerate(paths):
        source = {"index": index, "status": "ready", "bytes": 0, "base64_bytes": 0}
        try:
            path = Path(raw_path)
            source["filename"] = path.name
            framing += per_file_overhead_bytes + len(json.dumps(path.name, ensure_ascii=False).encode("utf-8"))
            if not path.is_file():
                raise StorageError("SOURCE_UNAVAILABLE", "The source image is not an available file.", stage="preflight")
            size = path.stat().st_size
            source.update(bytes=size, base64_bytes=base64_encoded_size(size))
            aggregate += size
            encoded += source["base64_bytes"]
            if size == 0:
                raise StorageError("EMPTY_IMAGE", "The source image is empty.", stage="preflight")
            if size > policy.max_image_bytes:
                raise StorageError("IMAGE_BYTE_LIMIT", "The source image exceeds the per-image byte limit.", stage="preflight")
        except StorageError as exc:
            source.update(exc.as_dict())
        except (OSError, TypeError, ValueError):
            source.update(StorageError("SOURCE_UNAVAILABLE", "The source image could not be inspected.", stage="preflight").as_dict())
        sources.append(source)
    global_errors = []
    if not sources:
        global_errors.append({"code": "NO_IMAGES", "message": "Choose at least one image."})
    if len(sources) > policy.max_files:
        global_errors.append({"code": "FILE_COUNT_LIMIT", "message": "The image count exceeds the configured batch limit."})
    wire = (encoded if transport == "base64" else aggregate) + framing
    if aggregate > policy.max_aggregate_bytes or wire > policy.max_aggregate_bytes:
        global_errors.append({"code": "AGGREGATE_BYTE_LIMIT", "message": "The batch exceeds the byte limit including transport overhead."})
    return {"allowed": not global_errors, "count": len(sources), "aggregate_bytes": aggregate,
            "base64_bytes": encoded, "overhead_bytes": framing, "estimated_wire_bytes": wire,
            "transport": transport, "sources": sources, "global_errors": global_errors}


def _hash_file(path: Path, limit: int | None = None) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_CHUNK), b""):
            size += len(chunk)
            if limit is not None and size > limit:
                raise StorageError("IMAGE_BYTE_LIMIT", "The source image exceeds the per-image byte limit.")
            digest.update(chunk)
    return digest.hexdigest(), size


def _verify_existing(path: Path, digest: str, size: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise StorageError("ASSET_CONFLICT", "The content-addressed asset path is not a regular immutable file.", stage="storage")
    actual_digest, actual_size = _hash_file(path)
    if actual_digest != digest or actual_size != size:
        raise StorageError("ASSET_CORRUPT", "An existing evidence asset failed its saved identity check; it was not overwritten.", stage="storage")


def _publish_immutable(temp_path: Path, destination: Path, digest: str, size: int) -> bool:
    """Atomically publish a closed, complete file without replacing an old one."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        _verify_existing(destination, digest, size)
        return True
    try:
        # Same-store staging guarantees the same filesystem. link is an atomic
        # exclusive publication on Windows and POSIX, unlike replace/rename.
        os.link(temp_path, destination)
    except FileExistsError:
        _verify_existing(destination, digest, size)
        return True
    except OSError as exc:
        raise StorageError("ATOMIC_STORAGE_UNAVAILABLE", "The output filesystem cannot publish an immutable evidence asset atomically.", stage="storage", retryable=True) from exc
    _verify_existing(destination, digest, size)
    return False


def _snapshot_source(path: Path, staging: Path, policy: CompressionPolicy) -> tuple[Path, str, int]:
    fd, filename = tempfile.mkstemp(prefix="incoming-", suffix=".tmp", dir=staging)
    snapshot = Path(filename)
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as target, path.open("rb") as source:
            for chunk in iter(lambda: source.read(_CHUNK), b""):
                size += len(chunk)
                if size > policy.max_image_bytes:
                    raise StorageError("IMAGE_BYTE_LIMIT", "The source image exceeds the per-image byte limit.")
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        if not size:
            raise StorageError("EMPTY_IMAGE", "The source image is empty.")
        return snapshot, digest.hexdigest(), size
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise


def _fit_size(width: int, height: int, edge: int) -> tuple[int, int]:
    scale = min(1.0, edge / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode_asset(image: Image.Image, fmt: str, staging: Path, destination: Path, store: Path, policy: CompressionPolicy) -> dict[str, Any]:
    fd, filename = tempfile.mkstemp(prefix="encoded-", suffix=".tmp", dir=staging)
    temp = Path(filename)
    try:
        with os.fdopen(fd, "wb") as output:
            options = {"quality": policy.jpeg_quality, "optimize": True, "progressive": False} if fmt == "JPEG" else {"compress_level": 6}
            image.save(output, format=fmt, **options)
            output.flush()
            os.fsync(output.fileno())
        digest, size = _hash_file(temp)
        reused = _publish_immutable(temp, destination, digest, size)
        return {"relative_path": destination.relative_to(store).as_posix(), "sha256": digest,
                "bytes": size, "base64_bytes": base64_encoded_size(size), "width": image.width,
                "height": image.height, "format": fmt, "content_type": _FORMATS[fmt][1], "reused": reused}
    except OSError as exc:
        raise StorageError("STORAGE_WRITE_FAILED", "The compact evidence asset could not be saved.", stage="storage", retryable=True) from exc
    finally:
        temp.unlink(missing_ok=True)


def prepare(
    path: str | os.PathLike, store_dir: str | os.PathLike,
    policy: CompressionPolicy | None = None, *, kind: str = "photo",
    preserve_original: bool = True, attachment_id: str | None = None,
    allowed_roots: Iterable[str | os.PathLike] = DEFAULT_IMAGE_ROOTS,
    private_snapshot: bool = False,
) -> PreparedArtifact:
    policy = policy or CompressionPolicy()
    if kind not in ("photo", "screenshot", "document", "auto"):
        raise StorageError("INVALID_IMAGE_KIND", "Image kind must be photo, screenshot, document, or auto.")
    if attachment_id is not None and (not isinstance(attachment_id, str) or not attachment_id.strip() or len(attachment_id) > 256):
        raise StorageError("INVALID_ATTACHMENT_ID", "A supplied attachment ID must be a nonempty bounded string.")
    store = Path(store_dir).resolve()
    try:
        source_path = resolve_local_image(path, allowed_roots=allowed_roots,
                                         max_bytes=policy.max_image_bytes,
                                         max_pixels=policy.max_pixels)
    except LocalImageInputError as exc:
        raise StorageError("SOURCE_UNAVAILABLE", "The source image is not an authorized readable image.") from exc
    staging = store / ".staging"
    try:
        staging.mkdir(parents=True, exist_ok=True)
        if private_snapshot:
            private_root = (store / "scratch").resolve()
            if not source_path.is_relative_to(private_root):
                raise StorageError("SOURCE_UNAVAILABLE", "The temporary image is outside controlled upload storage.")
            source_hash, source_bytes = _hash_file(source_path, policy.max_image_bytes)
            if source_bytes <= 0:
                raise StorageError("EMPTY_IMAGE", "The source image is empty.")
            snapshot = source_path
        else:
            snapshot, source_hash, source_bytes = _snapshot_source(source_path, staging, policy)
    except OSError as exc:
        raise StorageError("SOURCE_READ_FAILED", "The source image could not be copied into bounded processing storage.", retryable=True) from exc
    ocr, vision, thumbnail = None, None, None
    try:
        with Image.open(snapshot) as header:
            fmt = str(header.format or "").upper()
            if fmt not in _FORMATS:
                raise StorageError("UNSUPPORTED_IMAGE_FORMAT", "Only JPEG, PNG, and WebP images are supported.")
            width, height = header.size
            if width <= 0 or height <= 0 or width * height > policy.max_pixels:
                raise StorageError("IMAGE_PIXEL_LIMIT", "The source image exceeds the decoded pixel limit.")
            if getattr(header, "n_frames", 1) != 1:
                raise StorageError("MULTIFRAME_IMAGE", "Animated and multi-frame images cannot be used as one proof image.")
            try:
                orientation = int(header.getexif().get(274, 1))
            except (TypeError, ValueError):
                orientation = 1
            if orientation not in range(1, 9):
                orientation = 1
            has_alpha = "A" in header.getbands() or "transparency" in header.info
        with Image.open(snapshot) as opened:
            fit = _fit_size(width, height, policy.ocr_edge)
            if fmt == "JPEG":
                opened.draft("RGB", fit)
            # Shrink before transpose/color conversion. PNG/WebP may decode the
            # bounded original once; all later copies are <= OCR-edge pixels.
            opened.thumbnail(fit, Image.Resampling.LANCZOS, reducing_gap=3.0)
            transposed = ImageOps.exif_transpose(opened)
            try:
                ocr = transposed.convert("RGBA" if has_alpha else "RGB")
            finally:
                transposed.close()
        normalized = (height, width) if orientation in (5, 6, 7, 8) else (width, height)
        vision = ocr.copy()
        vision.thumbnail((policy.max_photo_edge, policy.max_photo_edge), Image.Resampling.LANCZOS, reducing_gap=3.0)
        thumbnail = vision.copy()
        thumbnail.thumbnail((policy.thumbnail_edge, policy.thumbnail_edge), Image.Resampling.LANCZOS, reducing_gap=3.0)
        # Explicit documents/screenshots retain a lossless pixel encoding; PNG
        # auto inputs and transparency also retain PNG. Original bytes stay exact.
        output_format = "PNG" if has_alpha or kind in ("screenshot", "document") or (kind == "auto" and fmt == "PNG") else "JPEG"
        signature = json.dumps({"source_sha256": source_hash, "policy": asdict(policy), "kind": kind,
                                "storage_version": _VERSION, "pillow_version": Image.__version__}, sort_keys=True, separators=(",", ":"))
        processing_id = hashlib.sha256(signature.encode()).hexdigest()
        extension = _FORMATS[output_format][0]
        original_ref = {"preserved": bool(preserve_original), "sha256": source_hash, "bytes": source_bytes,
                        "base64_bytes": base64_encoded_size(source_bytes), "format": fmt, "relative_path": None}
        if preserve_original:
            destination = store / "assets" / "original" / f"{source_hash}.{_FORMATS[fmt][0]}"
            original_ref["reused"] = _publish_immutable(snapshot, destination, source_hash, source_bytes)
            original_ref["relative_path"] = destination.relative_to(store).as_posix()
        compact = _encode_asset(vision, output_format, staging, store / "assets" / "compact" / f"{processing_id}.{extension}", store, policy)
        thumb = _encode_asset(thumbnail, output_format, staging, store / "assets" / "thumbnail" / f"{processing_id}.{extension}", store, policy)
        if attachment_id is None:
            occurrence = str(source_path.resolve()) + "\0" + source_hash
            attachment_id = "att_" + hashlib.sha256(occurrence.encode("utf-8")).hexdigest()[:32]
        metadata = {"storage_version": _VERSION, "artifact_id": source_hash, "image_id": source_hash,
                    "processing_id": processing_id, "attachment_id": attachment_id,
                    "source_filename": source_path.name, "kind": kind, "original_sha256": source_hash,
                    "original_bytes": source_bytes, "original_width": width, "original_height": height,
                    "encoded_width": width, "encoded_height": height,
                    "normalized_width": normalized[0], "normalized_height": normalized[1],
                    "exif_orientation": orientation, "has_transparency": has_alpha,
                    "original": original_ref, "compact": compact, "thumbnail": thumb,
                    "ocr": {"persisted": False, "width": ocr.width, "height": ocr.height},
                    "compact_ratio": compact["bytes"] / source_bytes,
                    "retained_bytes": (source_bytes if preserve_original else 0) + compact["bytes"] + thumb["bytes"]}
        thumbnail.close()
        return PreparedArtifact(metadata, vision, ocr)
    except StorageError:
        for image in (ocr, vision, thumbnail):
            if image is not None:
                image.close()
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise StorageError("IMAGE_PIXEL_LIMIT", "The source image exceeds the safe decoded pixel limit.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        for image in (ocr, vision, thumbnail):
            if image is not None:
                image.close()
        raise StorageError("INVALID_IMAGE", "The source image is not a complete readable image.") from exc
    finally:
        snapshot.unlink(missing_ok=True)
