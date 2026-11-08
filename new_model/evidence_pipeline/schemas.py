from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class AnalysisContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


StageStatus = Literal["completed", "no_text", "unavailable", "timeout", "failed"]
EvidenceStatus = Literal["completed", "partial", "failed"]


class ModelUnavailable(RuntimeError):
    pass


class StageResult(AnalysisContract):
    status: StageStatus
    engine: StrictStr = Field(min_length=1, max_length=128)
    elapsed_ms: float = Field(default=0, ge=0, le=86_400_000)
    attempts: int = Field(default=1, ge=1, le=100)
    error: StrictStr | None = Field(default=None, max_length=512)
    data: dict[str, Any] = Field(default_factory=dict)


class EvidenceResult(AnalysisContract):
    analysis_only: Literal[True] = True
    analysis_version: StrictStr = Field(min_length=1, max_length=64)
    image_id: StrictStr = Field(min_length=1, max_length=256)
    task_occurrence_id: StrictStr = Field(min_length=1, max_length=256)
    batch_id: StrictStr = Field(min_length=1, max_length=256)
    source_filename: StrictStr = Field(max_length=255)
    status: EvidenceStatus
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)
    text_analysis: StageResult | None = None
    visual_analysis: StageResult | None = None
    warnings: list[StrictStr] = Field(default_factory=list, max_length=64)
    elapsed_ms: float = Field(default=0, ge=0, le=86_400_000)
    submission_state: Literal["analysis_only"] = "analysis_only"
    review_state: Literal["not_reviewed"] = "not_reviewed"
    award_state: Literal["not_awarded"] = "not_awarded"

    @property
    def analysis_status(self) -> Literal["success", "uncertain", "invalid"]:
        return {"completed": "success", "partial": "uncertain", "failed": "invalid"}[self.status]

    def to_dict(self) -> dict[str, Any]:
        value = self.model_dump()
        value["analysis_status"] = self.analysis_status
        return value


class EvidenceBatchResult(AnalysisContract):
    analysis_only: Literal[True] = True
    analysis_version: StrictStr = Field(min_length=1, max_length=64)
    task_occurrence_id: StrictStr = Field(min_length=1, max_length=256)
    batch_id: StrictStr = Field(min_length=1, max_length=256)
    status: EvidenceStatus
    selected_count: int = Field(ge=0, le=5)
    processed_count: int = Field(ge=0, le=5)
    completed_count: int = Field(ge=0, le=5)
    results: list[EvidenceResult] = Field(max_length=5)
    budget: dict[str, Any]
    elapsed_ms: float = Field(ge=0, le=86_400_000)
    review_state: Literal["not_reviewed"] = "not_reviewed"
    award_state: Literal["not_awarded"] = "not_awarded"

    @property
    def analysis_status(self) -> Literal["success", "uncertain", "invalid"]:
        return {"completed": "success", "partial": "uncertain", "failed": "invalid"}[self.status]

    def to_dict(self) -> dict[str, Any]:
        value = self.model_dump()
        value["analysis_status"] = self.analysis_status
        value["results"] = [result.to_dict() for result in self.results]
        return value
