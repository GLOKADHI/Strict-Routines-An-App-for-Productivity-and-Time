import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import ANALYSIS_VERSION
from .attributes import analyze_clothing, attributes, colors
from .ocr import TesseractOCR
from local_image_paths import DEFAULT_IMAGE_ROOTS, LocalImageInputError, resolve_local_image
from .schemas import EvidenceBatchResult, EvidenceResult, ModelUnavailable, StageResult
from .storage import CompressionPolicy, prepare, preflight_sources


@dataclass(frozen=True)
class Attachment:
    path: Path
    image_id: str
    kind: str = "photo"
    private_snapshot: bool = False


def stable_image_id(task_occurrence_id: str, attachment_key: str) -> str:
    return hashlib.sha256((task_occurrence_id + "\0" + attachment_key).encode()).hexdigest()[:32]


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".result-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EvidencePipeline:
    """Read-only proof assistant; results never approve evidence or award points."""

    def __init__(self, store_dir: Path, ocr: Any = None, vision: Any = None,
                 policy: CompressionPolicy | None = None,
                 allowed_image_roots: Iterable[str | os.PathLike] | None = None):
        self.store_dir = Path(store_dir)
        self.policy = policy or CompressionPolicy()
        self.allowed_image_roots = tuple(allowed_image_roots) if allowed_image_roots is not None else (
            *DEFAULT_IMAGE_ROOTS, self.store_dir / "scratch")
        self.ocr = ocr if ocr is not None else TesseractOCR()
        if vision is None:
            from .vision import CompactVisionAdapter
            vision = CompactVisionAdapter()
        self.vision = vision

    @staticmethod
    def _stage(engine, image, previous: dict | None, retry_failed: bool, text: bool = False) -> StageResult:
        version = getattr(engine, "model_version", engine.__class__.__name__)
        if previous and previous.get("engine") == version:
            if previous.get("status") in ("completed", "no_text") or not retry_failed:
                cached = dict(previous)
                if cached.get("status") not in ("completed", "no_text"):
                    cached["error"] = "Previous analysis stage did not complete."
                return StageResult(**cached)
        start = time.perf_counter()
        attempts = (previous or {}).get("attempts", 0) + 1
        try:
            data = engine.analyze(image)
            status = "no_text" if text and not data.get("text_detected", False) else "completed"
            return StageResult(status=status, engine=version,
                               elapsed_ms=(time.perf_counter()-start)*1000,
                               attempts=attempts, data=data)
        except ModelUnavailable as exc:
            status = "unavailable"
            error_type = type(exc).__name__
        except TimeoutError as exc:
            status = "timeout"
            error_type = type(exc).__name__
        except Exception as exc:
            status = "failed"
            error_type = type(exc).__name__
        safe_error = f"Analysis stage failed ({error_type})."
        return StageResult(status=status, engine=version, elapsed_ms=(time.perf_counter()-start)*1000,
                           attempts=attempts, error=safe_error,
                           data={"text_detected": False, "extracted_text": "", "blocks": [], "language": None} if text else {})

    def analyze(self, attachment: Attachment, task_occurrence_id: str, batch_id: str,
                retry_failed: bool = True) -> dict:
        if not task_occurrence_id or not attachment.image_id or not batch_id:
            raise ValueError("Nonempty task occurrence, attachment and batch IDs are required.")
        if any(not isinstance(value, str) or len(value) > 256
               for value in (task_occurrence_id, attachment.image_id, batch_id)):
            raise ValueError("Analysis identifiers must be bounded strings.")
        try:
            safe_path = resolve_local_image(attachment.path, allowed_roots=self.allowed_image_roots,
                                            max_bytes=self.policy.max_image_bytes,
                                            max_pixels=self.policy.max_pixels)
        except LocalImageInputError:
            return EvidenceResult(analysis_version=ANALYSIS_VERSION, image_id=attachment.image_id,
                                 task_occurrence_id=task_occurrence_id, batch_id=batch_id,
                                 source_filename="image", status="failed",
                                 warnings=["The source is not an authorized readable image."]).to_dict()
        attachment = Attachment(safe_path, attachment.image_id, attachment.kind, attachment.private_snapshot)
        start = time.perf_counter()
        cache_key = hashlib.sha256((task_occurrence_id+"\0"+attachment.image_id).encode()).hexdigest()
        result_path = self.store_dir / "results" / (cache_key+".json")
        previous = None
        if result_path.is_file():
            try:
                previous = json.loads(result_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                previous = None
        result = EvidenceResult(analysis_version=ANALYSIS_VERSION, image_id=attachment.image_id,
                                task_occurrence_id=task_occurrence_id, batch_id=batch_id,
                                source_filename=attachment.path.name[:255], status="failed")
        try:
            with prepare(attachment.path, self.store_dir, self.policy, kind=attachment.kind,
                         preserve_original=True, attachment_id=attachment.image_id,
                         allowed_roots=self.allowed_image_roots,
                         private_snapshot=attachment.private_snapshot) as artifact:
                result.evidence_metadata = artifact.metadata
                sha = artifact.metadata["original_sha256"]
                if previous and previous.get("evidence_metadata", {}).get("original_sha256") != sha:
                    raise ValueError("Attachment ID already refers to different original bytes; use a new attachment ID.")
                reusable = previous if (previous and previous.get("analysis_version") == ANALYSIS_VERSION and
                                        previous.get("evidence_metadata", {}).get("processing_id") == artifact.metadata.get("processing_id")) else {}
                result.text_analysis = self._stage(self.ocr, artifact.ocr_image, reusable.get("text_analysis"), retry_failed, text=True)
                result.visual_analysis = self._stage(self.vision, artifact.vision_image, reusable.get("visual_analysis"), retry_failed)
                measured = attributes(artifact.vision_image)
                visual = result.visual_analysis.data
                visual.update({k: v for k, v in measured.items() if k != "warnings"})
                objects = visual.get("objects", [])
                for obj in objects:
                    box = obj.get("bbox")
                    if box and len(box) == 4:
                        x, y, w, h = map(int, box)
                        x, y = max(0, x), max(0, y)
                        w, h = min(w, artifact.vision_image.width-x), min(h, artifact.vision_image.height-y)
                        if w > 0 and h > 0:
                            obj["colors"] = colors(artifact.vision_image, [x, y, w, h])
                            obj["color_scope"] = "detected bounding rectangle; may include background"
                clothing = analyze_clothing(artifact.vision_image, objects)
                visual["clothing_analysis"] = clothing

                labels = list(dict.fromkeys(o["label"] for o in objects if o.get("label")))
                measured_families = list(dict.fromkeys(c["family"] for c in visual["colors"][:3]))
                parts = []
                if labels:
                    parts.append("Detected objects: " + ", ".join(labels[:8]) + ".")
                if measured_families:
                    parts.append("Measured image colors: " + ", ".join(measured_families) + ".")
                if clothing.get("visible_pattern", {}).get("pattern"):
                    parts.append(f"Visible pattern: {clothing['visible_pattern']['pattern']}.")
                if clothing.get("surface_appearance", {}).get("appearance"):
                    parts.append(f"Surface: {clothing['surface_appearance']['appearance']}.")
                if result.text_analysis.data.get("text_detected"):
                    extracted = result.text_analysis.data.get("extracted_text", "").strip().replace("\n", " ")
                    preview = (extracted[:60] + "…") if len(extracted) > 60 else extracted
                    parts.append(f"Readable text detected: \"{preview}\".")
                else:
                    parts.append("No readable text detected.")
                visual["caption"] = " ".join(parts) or "No supported visual category or readable text was identified."
                visual["caption_source"] = "grounded_summary_of_detected_and_measured_fields"
                visual["confidence"] = None
                visual["unsupported_fields"] = list(dict.fromkeys(visual.get("unsupported_fields", []) +
                    ["verified_activity", "physical_material", "true_object_color", "hidden_details", "user_identity"]))
                result.warnings.extend(measured["warnings"])
                result.warnings.extend(result.text_analysis.data.get("warnings", []))
                result.warnings.extend(visual.get("warnings", []))
                ok_text = result.text_analysis.status in ("completed", "no_text")
                ok_vision = result.visual_analysis.status == "completed"
                result.status = "completed" if ok_text and ok_vision else "partial" if ok_text or ok_vision else "failed"
                # Pixel facts may be useful even when both model stages are unavailable.
                if result.status == "failed" and visual.get("colors"):
                    result.status = "partial"
                result.warnings.append("Analysis does not establish routine completion, liveness, approval or score entitlement.")
        except Exception as exc:
            result.warnings.append(f"Input or storage validation failed ({type(exc).__name__}).")
            if previous:
                # An ID conflict or transient corrupt source must not overwrite a valid completed result.
                result.warnings.append("Prior stored result retained; this attempt was not persisted.")
                result.elapsed_ms = (time.perf_counter()-start)*1000
                return result.to_dict()
        result.elapsed_ms = (time.perf_counter()-start)*1000
        payload = result.to_dict()
        _atomic_json(result_path, payload)
        return payload

    def analyze_batch(self, attachments: list[Attachment], task_occurrence_id: str, batch_id: str,
                      retry_failed: bool = True) -> dict:
        start = time.perf_counter()
        ids = [a.image_id for a in attachments]
        if len(set(ids)) != len(ids):
            raise ValueError("Every selected attachment must have a distinct image ID.")
        if (not isinstance(task_occurrence_id, str) or not isinstance(batch_id, str)
                or not task_occurrence_id or not batch_id
                or len(task_occurrence_id) > 256 or len(batch_id) > 256
                or any(not isinstance(image_id, str) or not image_id or len(image_id) > 256 for image_id in ids)):
            raise ValueError("Analysis identifiers must be nonempty bounded strings.")
        try:
            attachments = [Attachment(resolve_local_image(
                a.path, allowed_roots=self.allowed_image_roots,
                max_bytes=self.policy.max_image_bytes, max_pixels=self.policy.max_pixels), a.image_id, a.kind,
                a.private_snapshot)
                for a in attachments]
        except LocalImageInputError:
            failures = [EvidenceResult(analysis_version=ANALYSIS_VERSION, image_id=a.image_id,
                                       task_occurrence_id=task_occurrence_id, batch_id=batch_id,
                                       source_filename="image", status="failed",
                                       warnings=["The source is not an authorized readable image."])
                        for a in attachments]
            return EvidenceBatchResult(
                analysis_version=ANALYSIS_VERSION, task_occurrence_id=task_occurrence_id, batch_id=batch_id,
                status="failed", selected_count=len(attachments), processed_count=len(attachments),
                completed_count=0, results=failures,
                budget={"allowed": False, "global_errors": [{"code": "UNAUTHORIZED_IMAGE_PATH"}]},
                elapsed_ms=(time.perf_counter()-start)*1000).to_dict()
        preflight = preflight_sources([a.path for a in attachments], self.policy)
        global_errors = preflight.get("global_errors", [])
        results = []
        for attachment in attachments:
            if global_errors:
                results.append(EvidenceResult(analysis_version=ANALYSIS_VERSION, image_id=attachment.image_id,
                                              task_occurrence_id=task_occurrence_id, batch_id=batch_id,
                                              source_filename=attachment.path.name[:255], status="failed",
                                              warnings=[str(e.get("message", "Input rejected.")) for e in global_errors]).to_dict())
            else:
                results.append(self.analyze(attachment, task_occurrence_id, batch_id, retry_failed))
        completed = sum(r["status"] == "completed" for r in results)
        status = "completed" if results and completed == len(results) else "partial" if any(r["status"] != "failed" for r in results) else "failed"
        validated_results = []
        for item in results:
            payload = dict(item)
            payload.pop("analysis_status", None)
            validated_results.append(EvidenceResult(**payload))
        return EvidenceBatchResult(
            analysis_version=ANALYSIS_VERSION, task_occurrence_id=task_occurrence_id, batch_id=batch_id,
            status=status, selected_count=len(attachments), processed_count=len(results),
            completed_count=completed, results=validated_results,
            budget=preflight, elapsed_ms=(time.perf_counter()-start)*1000).to_dict()
