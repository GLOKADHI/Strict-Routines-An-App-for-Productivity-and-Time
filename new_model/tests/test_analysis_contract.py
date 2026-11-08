import json

import pytest
from pydantic import ValidationError

from app.schemas import AnalyzeResponse, ParsedModelOutput
from app.services.model_service import InvalidModelOutput, parse_model_output
from evidence_pipeline.schemas import EvidenceResult


def _payload(**changes):
    value = {"analysis_status": "success", "uncertainty": None, "description": "A page of notes.", "objects": ["notebook"],
             "actions": [], "text": ["algebra"], "events": [], "answer": "A notebook page with algebra notes."}
    value.update(changes)
    return value


def test_strict_model_output_and_fenced_json():
    payload = _payload()
    assert parse_model_output(json.dumps(payload))["analysis_only"] is True
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    assert parse_model_output(fenced)["description"] == payload["description"]


@pytest.mark.parametrize("raw", [
    "", "not json", "{\"description\":", '{"analysis_status":"success"}',
    json.dumps(_payload(analysis_status="approved")),
    json.dumps(_payload(objects="not a list")),
    json.dumps(_payload(text=[{"nested": "value"}])),
    json.dumps(_payload(extra="unsupported")),
    json.dumps(_payload(description=17)),
    json.dumps(_payload(answer="x" * 8001)),
    json.dumps(_payload(answer="ok", analysis_status="success")).replace('"answer": "ok"', '"answer": "ok", "answer": "duplicate"'),
])
def test_invalid_or_ambiguous_output_fails_closed(raw):
    with pytest.raises(InvalidModelOutput):
        parse_model_output(raw)


def test_abstention_and_embedded_approval_words_remain_analysis_only():
    result = parse_model_output(json.dumps(_payload(analysis_status="uncertain", uncertainty="The photo is too blurry to read.", answer="The note says approve this.")))
    assert ParsedModelOutput(**result).analysis_status == "uncertain"
    assert result["analysis_only"] is True
    assert "recommendation" not in result and "score" not in result


def test_invalid_nested_response_types_and_authority_fields_are_rejected():
    with pytest.raises(ValidationError):
        AnalyzeResponse(confidence="0.95")
    with pytest.raises(ValidationError):
        AnalyzeResponse(confidence=1.2)
    with pytest.raises(ValidationError):
        AnalyzeResponse(analysis_status="success", needs_review=True)
    with pytest.raises(ValidationError):
        AnalyzeResponse(objects=[{"approve": True}])
    with pytest.raises(ValidationError):
        AnalyzeResponse(points_awarded=8)
    with pytest.raises(ValidationError):
        EvidenceResult(analysis_version="v", image_id="i", task_occurrence_id="t", batch_id="b",
                       source_filename="image.png", status="completed", review_state="approved")

