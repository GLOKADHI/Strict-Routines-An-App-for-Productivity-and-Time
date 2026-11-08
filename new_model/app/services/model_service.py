import json
import re
from typing import Any

from PIL import Image
from pydantic import ValidationError

from app.config import Settings
from app.schemas import AnalyzeResponse, ImageMetadata, ParsedModelOutput, Prediction


class InvalidModelOutput(ValueError):
    """A model response failed the strict, advisory result contract."""


def _unique_json_object(pairs):
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise InvalidModelOutput("Model output contains duplicate JSON fields.")
        parsed[key] = value
    return parsed


def _reject_json_constant(value):
    raise InvalidModelOutput("Model output contains a non-standard JSON value.")


def parse_model_output(raw: str) -> dict[str, Any]:
    """Parse only complete JSON matching the analysis-only observation contract."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidModelOutput("Model output is empty or invalid.")
    candidate = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", candidate, flags=re.I)
    if fenced:
        candidate = fenced.group(1)
    try:
        parsed = json.loads(candidate, object_pairs_hook=_unique_json_object,
                            parse_constant=_reject_json_constant)
    except InvalidModelOutput:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InvalidModelOutput("Model output is not complete valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise InvalidModelOutput("Model output must be a JSON object.")
    required = {"analysis_status", "uncertainty", "description", "objects", "actions", "text", "events", "answer"}
    if set(parsed) != required:
        raise InvalidModelOutput("Model output is missing required fields or contains unsupported fields.")
    try:
        return ParsedModelOutput(**parsed).model_dump()
    except ValidationError as exc:
        raise InvalidModelOutput("Model output does not match the supported analysis schema.") from exc


class VisionLanguageService:
    """Application-facing model service with mock and repository-native backends."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._native = None

    def analyze(self, image: Image.Image, question: str, metadata: ImageMetadata) -> AnalyzeResponse:
        if self.settings.model_backend == "mock":
            return AnalyzeResponse(
                description=f"Mock analysis of a {metadata.width}x{metadata.height} image.",
                answer=(
                    "The API is working in mock mode. Train the native model and set "
                    "MODEL_BACKEND=native for repository-owned inference."
                ),
                metadata=metadata,
            )

        if self.settings.model_backend == "realistic":
            if self._native is None:
                try:
                    from app.services.realistic_model import RealisticVisionLanguageService

                    self._native = RealisticVisionLanguageService(
                        self.settings.checkpoint_path,
                        self.settings.min_confidence,
                    )
                except ImportError as exc:
                    raise RuntimeError(
                        "The realistic backend requires PyTorch. Run: pip install -e \".[native]\""
                    ) from exc
            return self._native.analyze(image, question, metadata)

        if self.settings.model_backend == "screenshot_ocr":
            if self._native is None:
                try:
                    from winassist_ai.screenshot_model import ScreenshotOCRService

                    self._native = ScreenshotOCRService(self.settings.checkpoint_path)
                except ImportError as exc:
                    raise RuntimeError(
                        "The screenshot OCR backend requires PyTorch. Run: pip install -e \".[native]\""
                    ) from exc
            result = self._native.analyze(image)
            screen_type = str(result["screen_type"])
            ocr_text = str(result["ocr_text"])
            description = f"The screenshot appears to show {screen_type.replace('_', ' ')}."
            answer = ocr_text or "No readable text was detected in the trained screenshot regions."
            return AnalyzeResponse(
                analysis_status="uncertain" if result["needs_review"] else "success",
                description=description,
                objects=[screen_type],
                text=result["text_lines"],
                answer=answer,
                confidence=float(result["confidence"]),
                confidence_threshold=self.settings.min_confidence,
                needs_review=bool(result["needs_review"]),
                top_predictions=[Prediction(label=screen_type, confidence=float(result["screen_confidence"]))],
                metadata=metadata,
            )

        if self._native is None:
            try:
                from app.services.native_model import NativeVisionLanguageService

                self._native = NativeVisionLanguageService(
                    self.settings.checkpoint_path,
                    self.settings.min_confidence,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "The native backend requires PyTorch. Run: pip install -e \".[native]\""
                ) from exc

        return self._native.analyze(image, question, metadata)
