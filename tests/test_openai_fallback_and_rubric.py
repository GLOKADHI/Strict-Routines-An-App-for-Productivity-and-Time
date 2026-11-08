"""
Unit & Integration Tests for StrictRecords Evidence Rubric and OpenAI Fallback Validator
"""

import io
import json
import pytest
from unittest.mock import MagicMock, patch

from core.rubric_loader import load_validation_rubric, get_rubric_file_path
from core.openai_fallback_validator import OpenAiFallbackValidator, OpenAiValidationResult
from core.taufa_ai_validator import TaufaAiValidator, TaufaValidationResult


def make_dummy_jpeg() -> bytes:
    """Generate minimal valid JPEG bytes."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x08\x00\x08\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"


def test_rubric_loader():
    """Verify that the rubric loader reads the markdown file and extracts the version."""
    content, version = load_validation_rubric(force_reload=True)
    assert "StrictRecords Evidence Validation Rubric" in content
    assert version == "1.0.0"
    assert "0 to 8 Points" in content
    assert "STUDY" in content
    assert "CODING" in content
    assert "VIDEO SHOOT" in content
    assert "VIDEO EDITING" in content


def test_openai_validator_no_images():
    """Rejection when no images are attached."""
    val = OpenAiFallbackValidator(api_key="test-key-mock")
    res = val.validate_evidence(
        submission_id="sub_empty_01",
        category="STUDY",
        description="Reading algorithms chapter",
        images=[],
    )
    assert not res.approved
    assert res.validation_status == "REJECTED"
    assert res.awarded_points == 0
    assert "NO_ATTACHMENTS_PROVIDED" in res.warnings
    assert not res.is_technical_failure


def test_openai_validator_unconfigured():
    """Returns PROVIDER_UNAVAILABLE when no API key is available."""
    val = OpenAiFallbackValidator(api_key="")
    with patch.dict("os.environ", {"OPENAI_API_KEY": "", "STRICTRECORDS_AI_API_KEY": ""}, clear=True):
        res = val.validate_evidence(
            submission_id="sub_unconf_01",
            category="STUDY",
            description="Reading algorithms chapter",
            images=[make_dummy_jpeg()],
        )
        assert not res.approved
        assert res.validation_status == "PROVIDER_UNAVAILABLE"
        assert res.is_technical_failure
        assert "OPENAI_API_KEY_MISSING" in res.warnings
        assert "VALIDATION_PENDING" in res.warnings


def test_openai_validator_multi_category():
    """Preserves multi-category submissions."""
    val = OpenAiFallbackValidator(api_key="test-key")
    cats = val._parse_categories("Study + Video Shooting + Video Editing")
    assert "Study" in cats
    assert "Video Shooting" in cats
    assert "Video Editing" in cats


def test_openai_validator_structured_response_validation():
    """Strictly validates all schema fields per Section 4."""
    val = OpenAiFallbackValidator(api_key="test-key")
    raw_payload = {
        "submission_id": "sub_valid_99",
        "validation_provider": "OPENAI_FALLBACK",
        "validation_status": "APPROVED",
        "approved": True,
        "activity_categories": ["STUDY", "CODING"],
        "evidence_quality": "STRONG",
        "activity_relevance": "HIGH",
        "awarded_points": 7,
        "explanation": "Handwritten notes and LeetCode workspace visible.",
        "rejection_reason": "",
        "confidence": 0.96,
        "rubric_version": "1.0.0",
    }
    result = val._validate_structured_response(
        raw_payload,
        submission_id="sub_valid_99",
        cat_list=["STUDY", "CODING"],
        ts="2026-11-04T12:00:00Z",
        rubric_ver="1.0.0",
        warnings=[],
        raw_response=raw_payload,
    )
    assert result.approved
    assert result.awarded_points == 7
    assert result.validation_provider == "OPENAI_FALLBACK"
    assert result.rubric_version == "1.0.0"
    assert result.activity_categories == ["STUDY", "CODING"]
    assert result.confidence == 0.96
    assert not result.is_technical_failure


def test_openai_validator_mock_api_call():
    """Mock HTTP POST response from OpenAI Chat Completions."""
    mock_response_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "submission_id": "sub_call_42",
                        "validation_provider": "OPENAI_FALLBACK",
                        "validation_status": "APPROVED",
                        "approved": True,
                        "activity_categories": ["CODING", "PROJECT"],
                        "evidence_quality": "EXEMPLARY",
                        "activity_relevance": "HIGH",
                        "awarded_points": 8,
                        "explanation": "Active IDE editor with complex Python project and terminal tests.",
                        "rejection_reason": "",
                        "confidence": 0.98,
                        "rubric_version": "1.0.0",
                    })
                }
            }
        ]
    }

    class MockHttpResp:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def getcode(self):
            return 200
        def read(self, *args):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    val = OpenAiFallbackValidator(api_key="sk-mock-valid-key-12345")
    with patch("urllib.request.urlopen", return_value=MockHttpResp(mock_response_json)):
        res = val.validate_evidence(
            submission_id="sub_call_42",
            category="CODING",
            description="Developing new backend features in Python",
            images=[make_dummy_jpeg()],
        )
        assert res.approved
        assert res.awarded_points == 8
        assert res.validation_provider == "OPENAI_FALLBACK"
        assert res.evidence_quality == "EXEMPLARY"
        assert not res.is_technical_failure


def test_taufa_ai_validator_invokes_openai_fallback_when_taufa_unconfigured():
    """TaufaAiValidator seamlessly invokes OpenAiFallbackValidator when Taufa AI has no active session."""
    validator = TaufaAiValidator()
    # Force Taufa unconfigured
    with patch.object(validator, "is_configured", return_value=False):
        mock_fb_result = OpenAiValidationResult(
            submission_id="sub_fallback_01",
            validation_provider="OPENAI_FALLBACK",
            validation_status="APPROVED",
            approved=True,
            activity_categories=["STUDY"],
            evidence_quality="STRONG",
            activity_relevance="HIGH",
            awarded_points=6,
            explanation="Approved by OpenAI fallback.",
            rejection_reason="",
            confidence=0.92,
            rubric_version="1.0.0",
        )
        with patch("core.openai_fallback_validator.OpenAiFallbackValidator.is_configured", return_value=True):
            with patch("core.openai_fallback_validator.OpenAiFallbackValidator.validate_evidence", return_value=mock_fb_result):
                res = validator.validate_hourly_submission(
                    submission_id="sub_fallback_01",
                    category="STUDY",
                    description="Studying database indexing and query plans",
                    images=[make_dummy_jpeg()],
                )
                assert res.approved
                assert res.score == 6
                assert res.validator_id == "OPENAI_FALLBACK"
                assert res.validation_provider == "OPENAI_FALLBACK"
                assert res.review_status == "APPROVED"
                assert res.qualifies_for_points


def test_taufa_ai_evaluate_submission_uses_openai_fallback():
    """evaluate_submission (endpoint handler) proceeds to fallback when Taufa is unavailable."""
    validator = TaufaAiValidator()
    with patch.object(validator, "is_configured", return_value=False):
        mock_fb_result = OpenAiValidationResult(
            submission_id="sub_eval_01",
            validation_provider="OPENAI_FALLBACK",
            validation_status="APPROVED",
            approved=True,
            activity_categories=["STUDY"],
            evidence_quality="STRONG",
            activity_relevance="HIGH",
            awarded_points=7,
            explanation="Visual inspection confirms rigorous study material.",
            rejection_reason="",
            confidence=0.95,
            rubric_version="1.0.0",
        )
        with patch("core.openai_fallback_validator.OpenAiFallbackValidator.is_configured", return_value=True):
            with patch("core.openai_fallback_validator.OpenAiFallbackValidator.validate_evidence", return_value=mock_fb_result):
                res = validator.evaluate_submission(
                    submission_id="sub_eval_01",
                    category="STUDY",
                    description="Studying compiler design and AST transformations",
                    images=[make_dummy_jpeg()],
                    mode="PRODUCTION",
                )
                assert res["success"] is True
                assert res["score"] == 7
                assert res["validation_provider"] == "OPENAI_FALLBACK"
                assert res["validation_status"] in ("SUCCESS", "APPROVED")
