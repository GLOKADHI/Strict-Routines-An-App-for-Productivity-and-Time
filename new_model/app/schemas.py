from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class StrictResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ImageMetadata(StrictResult):
    filename: StrictStr | None = Field(default=None, max_length=255)
    content_type: StrictStr | None = Field(default=None, max_length=128)
    original_width: int
    original_height: int
    width: int
    height: int
    format: StrictStr | None = Field(default=None, max_length=16)


class Prediction(StrictResult):
    label: StrictStr = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)


ShortObservation = Annotated[StrictStr, Field(max_length=1000)]
TextObservation = Annotated[StrictStr, Field(max_length=4000)]


class AnalyzeResponse(StrictResult):
    """Strict analysis result; it carries no approval or scoring authority."""

    analysis_only: Literal[True] = True
    analysis_status: Literal["success", "uncertain"] = "success"
    description: StrictStr = Field(default="", max_length=4000)
    objects: list[ShortObservation] = Field(default_factory=list, max_length=32)
    actions: list[ShortObservation] = Field(default_factory=list, max_length=32)
    text: list[TextObservation] = Field(default_factory=list, max_length=64)
    events: list[ShortObservation] = Field(default_factory=list, max_length=32)
    answer: StrictStr = Field(default="", max_length=8000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = False
    top_predictions: list[Prediction] = Field(default_factory=list)
    metadata: ImageMetadata | None = None

    @model_validator(mode="after")
    def review_status_matches_uncertainty(self):
        if self.needs_review != (self.analysis_status == "uncertain"):
            raise ValueError("Uncertain analyses must be marked for review, and confident analyses must not be.")
        return self


class ParsedModelOutput(StrictResult):
    """Validated advisory observations parsed from a model's JSON response."""

    analysis_only: Literal[True] = True
    analysis_status: Literal["success", "uncertain", "invalid"]
    uncertainty: StrictStr | None = Field(max_length=1000)
    description: StrictStr = Field(max_length=4000)
    objects: list[ShortObservation] = Field(max_length=32)
    actions: list[ShortObservation] = Field(max_length=32)
    text: list[TextObservation] = Field(max_length=64)
    events: list[ShortObservation] = Field(max_length=32)
    answer: StrictStr = Field(max_length=8000)

    @model_validator(mode="after")
    def uncertainty_required_for_abstention(self):
        if self.analysis_status in {"uncertain", "invalid"} and not (self.uncertainty or "").strip():
            raise ValueError("Uncertain and invalid results must explain why they cannot be relied on.")
        return self
