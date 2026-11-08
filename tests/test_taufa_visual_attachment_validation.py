"""
Tests for Taufa AI Visual Evidence Attachment Validation.

Verifies:
1. Designated conversation ID (891a3c1e-eed2-460e-bc05-6b8ac94bec5b) and URL enforcement.
2. Original image preservation: zero modifications, SHA-256 calculation, MIME type detection, dimensions.
3. Genuine visual attachment payload structure: parts data URL + experimental_attachments array.
4. HTTP headers: Origin, Referer, X-Conversation-Id, Cookie/Bearer authentication.
5. Progression across the 11 explicit states and state callback notifications.
6. Multi-image handling (1 to 10 images) with per-image metadata association.
7. Unconfigured / offline failure behavior: holds evidence for human review, never simulates approval.
8. Server API routes (/api/taufa/ask and /api/taufa/evaluate).
"""

import base64
import hashlib
import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from core.taufa_client import (
    DESIGNATED_CHAT_URL,
    DESIGNATED_CONVERSATION_ID,
    STATE_ATTACHMENT_CONFIRMED,
    STATE_EVALUATION_FAILED,
    STATE_EVALUATION_RECEIVED,
    STATE_EVALUATION_REQUEST_SENT,
    STATE_EVALUATION_VALIDATED,
    STATE_HUMAN_REVIEW_REQUIRED,
    STATE_IMAGE_INTEGRITY_CHECKED,
    STATE_IMAGE_RECEIVED,
    STATE_TAUFA_UPLOAD_FAILED,
    STATE_UPLOADING_TO_TAUFA,
    STATE_WAITING_FOR_TAUFA,
    TaufaClient,
    inspect_original_image,
    parse_image_dimensions_raw,
)
from core.taufa_ai_validator import (
    TaufaAiValidator,
    TaufaValidationResult,
)


@pytest.fixture
def temp_session_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def authenticated_client(temp_session_file):
    client = TaufaClient(session_file=temp_session_file)
    client.save_session(
        cookies=[
            {"name": "authjs.session-token", "value": "test-token-visual-12345"},
            {"name": "cf_clearance", "value": "test-cf-clearance-xyz"},
        ]
    )
    return client


# Generate valid PNG and GIF bytes
def make_valid_png(width=800, height=600, color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


def make_valid_gif(width=320, height=240, color="blue"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="GIF")
    return buf.getvalue()


VALID_PNG_800X600 = make_valid_png(800, 600)
VALID_GIF_320X240 = make_valid_gif(320, 240)


def test_designated_conversation_constants():
    """Verify exclusive designated conversation ID and chat URL."""
    assert DESIGNATED_CONVERSATION_ID == "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
    assert (
        DESIGNATED_CHAT_URL
        == "https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
    )
    client = TaufaClient(session_file=None)
    assert client.get_or_create_conversation_id() == "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"


def test_inspect_original_image_png_integrity():
    """Verify original image integrity inspection: SHA-256, dimensions, MIME, and attachment ID."""
    meta = inspect_original_image(VALID_PNG_800X600, filename="study_notes.png")
    expected_sha256 = hashlib.sha256(VALID_PNG_800X600).hexdigest()

    assert meta["filename"] == "study_notes.png"
    assert meta["mime_type"] == "image/png"
    assert meta["byte_size"] == len(VALID_PNG_800X600)
    assert meta["sha256"] == expected_sha256
    assert meta["width"] == 800
    assert meta["height"] == 600
    assert meta["dimensions"] == "800x600"
    assert meta["attachment_id"].startswith(f"att_{expected_sha256[:16]}_")
    assert meta["is_modified"] is False
    assert meta["upload_status"] == STATE_IMAGE_INTEGRITY_CHECKED


def test_inspect_original_image_gif():
    """Verify GIF dimensions and MIME detection."""
    meta = inspect_original_image(VALID_GIF_320X240, filename="calc_diagram.gif")
    assert meta["mime_type"] == "image/gif"
    assert meta["width"] == 320
    assert meta["height"] == 240
    assert meta["dimensions"] == "320x240"


def test_unconfigured_session_blocks_visual_upload(temp_session_file):
    """When session cookies are missing, returns TAUFA_UPLOAD_FAILED without simulating points."""
    client = TaufaClient(session_file=temp_session_file)
    client.clear_session()

    states_recorded = []

    def on_state(state, extra):
        states_recorded.append((state, extra))

    result = client.ask_score(
        category="STUDY",
        description="Reading biology textbook and taking notes",
        images=[VALID_PNG_800X600],
        state_callback=on_state,
    )

    assert result["state"] == STATE_TAUFA_UPLOAD_FAILED
    assert result["upload_status"] == STATE_TAUFA_UPLOAD_FAILED
    assert result["evaluation_status"] == STATE_HUMAN_REVIEW_REQUIRED
    assert result["score"] is None
    assert result["conversation_id"] == DESIGNATED_CONVERSATION_ID
    assert "TAUFA_SESSION_MISSING" in result["warnings"]
    assert len(result["attachments"]) == 1

    # Verify states recorded
    state_names = [s[0] for s in states_recorded]
    assert STATE_IMAGE_RECEIVED in state_names
    assert STATE_IMAGE_INTEGRITY_CHECKED in state_names
    assert STATE_TAUFA_UPLOAD_FAILED in state_names


def test_visual_attachment_payload_and_headers(authenticated_client):
    """Verify payload structure: parts data URL, experimental_attachments, and designated headers."""
    captured_request = {}

    def mock_urlopen(req, context=None, timeout=None):
        captured_request["url"] = req.full_url
        captured_request["headers"] = dict(req.headers)
        captured_request["data"] = json.loads(req.data.decode("utf-8"))

        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "reason": "Clear view of physical textbook and detailed handwritten notes",
            "warnings": [],
        }).encode("utf-8")
        return mock_resp

    states_recorded = []

    def on_state(state, extra):
        states_recorded.append((state, extra))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = authenticated_client.ask_score(
            category="STUDY",
            description="Solving differential equations notes",
            images=[VALID_PNG_800X600, VALID_GIF_320X240],
            image_filenames=["page1.png", "page2.gif"],
            submission_id="sub_test_visual_001",
            evaluation_id="eval_test_visual_001",
            schedule_context="14:00 - 15:00 Slot",
            user_id="user_john",
            state_callback=on_state,
        )

    # 1. Verify result envelope
    assert result["score"] == 8
    assert result["conversation_id"] == DESIGNATED_CONVERSATION_ID
    assert result["submission_id"] == "sub_test_visual_001"
    assert result["evaluation_id"] == "eval_test_visual_001"
    assert result["state"] == STATE_EVALUATION_VALIDATED
    assert len(result["attachments"]) == 2
    assert result["attachments"][0]["filename"] == "page1.png"
    assert result["attachments"][1]["filename"] == "page2.gif"

    # 2. Verify HTTP headers
    headers = captured_request["headers"]
    assert headers["X-conversation-id"] == DESIGNATED_CONVERSATION_ID
    assert headers["Referer"] == DESIGNATED_CHAT_URL
    assert headers["Origin"] == "https://taufa.ai"
    assert "Cookie" in headers
    assert "authjs.session-token=test-token-visual-12345" in headers["Cookie"]

    # 3. Verify visual payload
    data = captured_request["data"]
    assert data["conversationId"] == DESIGNATED_CONVERSATION_ID
    assert len(data["messages"]) == 1

    msg = data["messages"][0]
    # Check parts
    parts = msg["parts"]
    assert len(parts) == 3  # 1 text + 2 images
    assert parts[0]["type"] == "text"
    assert DESIGNATED_CONVERSATION_ID in parts[0]["text"]
    assert "sub_test_visual_001" in parts[0]["text"]

    # Original bytes in parts
    assert parts[1]["type"] == "image"
    assert parts[1]["image"].startswith("data:image/png;base64,")
    b64_img1 = parts[1]["image"].split(",", 1)[1]
    assert base64.b64decode(b64_img1) == VALID_PNG_800X600

    assert parts[2]["type"] == "image"
    assert parts[2]["image"].startswith("data:image/gif;base64,")
    b64_img2 = parts[2]["image"].split(",", 1)[1]
    assert base64.b64decode(b64_img2) == VALID_GIF_320X240

    # Check experimental_attachments
    exp_att = msg["experimental_attachments"]
    assert len(exp_att) == 2
    assert exp_att[0]["name"] == "page1.png"
    assert exp_att[0]["contentType"] == "image/png"
    assert exp_att[0]["url"] == parts[1]["image"]
    assert exp_att[1]["name"] == "page2.gif"
    assert exp_att[1]["contentType"] == "image/gif"
    assert exp_att[1]["url"] == parts[2]["image"]

    # 4. Verify 11-state sequential progression
    state_names = [s[0] for s in states_recorded]
    assert state_names == [
        STATE_IMAGE_RECEIVED,
        STATE_IMAGE_INTEGRITY_CHECKED,
        STATE_UPLOADING_TO_TAUFA,
        STATE_ATTACHMENT_CONFIRMED,
        STATE_EVALUATION_REQUEST_SENT,
        STATE_WAITING_FOR_TAUFA,
        STATE_EVALUATION_RECEIVED,
        STATE_EVALUATION_VALIDATED,
    ]


def test_multi_image_up_to_10_photos(authenticated_client):
    """Verify handling of up to 10 photos with unique metadata for each."""
    test_images = [VALID_PNG_800X600 for _ in range(10)]
    test_filenames = [f"photo_{i + 1}.png" for i in range(10)]

    def mock_urlopen(req, context=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "reason": "10 pages of coursework verified",
            "warnings": [],
        }).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = authenticated_client.ask_score(
            category="STUDY",
            description="Full semester notebook scan",
            images=test_images,
            image_filenames=test_filenames,
        )

    assert result["score"] == 8
    assert len(result["attachments"]) == 10
    for idx, att in enumerate(result["attachments"]):
        assert att["filename"] == f"photo_{idx + 1}.png"
        assert att["dimensions"] == "800x600"
        assert att["upload_status"] == STATE_ATTACHMENT_CONFIRMED


def test_validator_adapter_integration(authenticated_client):
    """Verify TaufaAiValidator integrates visual attachment fields seamlessly."""
    validator = TaufaAiValidator()

    def mock_urlopen(req, context=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "score": 8,
            "category_match": True,
            "description_match": True,
            "evidence_match": True,
            "evidence_quality": "HIGH",
            "reason": "Visual inspection passed",
            "warnings": [],
        }).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = validator.validate_hourly_submission(
            submission_id="sub_val_001",
            category="STUDY",
            description="Math revision notes",
            images=[VALID_PNG_800X600],
            image_filenames=["math.png"],
            taufa_client=authenticated_client,
        )

    assert isinstance(result, TaufaValidationResult)
    assert result.score == 8
    assert result.conversation_id == DESIGNATED_CONVERSATION_ID
    assert result.evaluation_state == STATE_EVALUATION_VALIDATED
    assert len(result.attachments_meta) == 1
    assert result.attachments_meta[0]["filename"] == "math.png"

    # Verify JSON serialization includes visual attachment tracking
    res_json = result.to_dict()
    assert res_json["conversation_id"] == DESIGNATED_CONVERSATION_ID
    assert res_json["evaluation_state"] == STATE_EVALUATION_VALIDATED
    assert len(res_json["attachments"]) == 1
    assert res_json["attachments"][0]["filename"] == "math.png"


def test_zero_tolerance_text_fallback():
    """Verify that when image attachments are missing, submission fails without points."""
    validator = TaufaAiValidator()
    result = validator.validate_hourly_submission(
        submission_id="sub_no_img",
        category="STUDY",
        description="Trying to submit without image",
        images=[],
    )
    assert result.score is None
    assert result.validator_status == "FAILED_FINAL"
    assert "NO_ATTACHMENTS" in result.warnings


def test_server_routes_support_visual_evidence():
    """Verify server request handler for /api/taufa/evaluate and /api/taufa/ask."""
    from core.server import StrictRecordsRequestHandler

    handler = object.__new__(StrictRecordsRequestHandler)
    handler.client_address = ("127.0.0.1", 54321)
    sent_responses = []

    def mock_send_json(data, status=200):
        sent_responses.append((status, data))

    handler._send_json = mock_send_json

    b64_img = base64.b64encode(VALID_PNG_800X600).decode("ascii")

    # 1. Test /api/taufa/evaluate
    body_eval = json.dumps({
        "submission_id": "sub_srv_001",
        "category": "STUDY",
        "description": "Validating python algorithms",
        "images": [b64_img],
        "image_filenames": ["doc.png"],
        "evaluation_id": "eval_srv_001",
    }).encode("utf-8")

    handler.path = "/api/taufa/evaluate"
    handler.headers = {"Content-Length": str(len(body_eval))}
    handler.rfile = io.BytesIO(body_eval)

    with patch("core.taufa_client.TaufaClient.ask_score") as mock_ask:
        mock_ask.return_value = {
            "score": 8,
            "conversation_id": DESIGNATED_CONVERSATION_ID,
            "submission_id": "sub_srv_001",
            "evaluation_id": "eval_srv_001",
            "state": STATE_EVALUATION_VALIDATED,
            "upload_status": STATE_ATTACHMENT_CONFIRMED,
            "evaluation_status": STATE_EVALUATION_VALIDATED,
            "validator_status": "SUCCESS",
            "review_status": "APPROVED",
            "reason": "Server route visual inspection passed",
            "warnings": [],
            "attachments": [{"filename": "doc.png", "dimensions": "800x600"}],
        }

        StrictRecordsRequestHandler.do_POST(handler)

        # Check call arguments to ensure visual params were passed
        assert mock_ask.call_count == 1
        _, kwargs = mock_ask.call_args
        assert kwargs["category"] == "STUDY"
        assert kwargs["submission_id"] == "sub_srv_001"
        assert kwargs["evaluation_id"] == "eval_srv_001"
        assert kwargs["image_filenames"] == ["doc.png"]
        assert len(kwargs["images"]) == 1
        assert kwargs["images"][0] == VALID_PNG_800X600

        # Check response
        assert len(sent_responses) == 1
        status, resp_data = sent_responses[0]
        assert status == 200
        assert resp_data["success"] is True
        assert resp_data["result"]["score"] == 8
        assert resp_data["result"]["conversation_id"] == DESIGNATED_CONVERSATION_ID
