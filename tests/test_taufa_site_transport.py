"""Real-site transport contract fixtures; these do not claim live model inference."""
import io
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from core.taufa_client import TaufaClient, STATE_ATTACHMENT_CONFIRMED


def assessment(submission_id="proof-1", **changes):
    return dict(submission_id=submission_id, score=7, category_match=True,
                description_match=True, evidence_match=True, evidence_quality="HIGH",
                reason="Visible fixture evidence", warnings=[], **changes)


def response(data):
    result = io.BytesIO(data if isinstance(data, bytes) else json.dumps(data).encode())
    result.status = 200
    return result


def client(tmp_path):
    value = TaufaClient(str(tmp_path / "session.json"))
    value._cached_session = {"cookie_header": "authjs.session-token=fixture-not-a-real-secret"}
    return value


def test_upload_original_bytes_before_sending_file_parts(tmp_path):
    image = Path("assets/strict_bot_mascot.png").read_bytes()
    requests = []
    def transport(request, **kwargs):
        requests.append(request)
        if request.full_url.endswith("/api/files/upload"):
            assert image in request.data
            assert b'name="file"' in request.data
            return response(dict(id="file-123", url="https://taufa.ai/files/fixture.png",
                                 filename="proof.png", mimeType="image/png", isImage=True))
        payload = json.loads(request.data)
        parts = payload["messages"][0]["parts"]
        assert parts[1] == dict(type="file", mediaType="image/png", filename="proof.png",
                                url="https://taufa.ai/files/fixture.png")
        assert "experimental_attachments" not in payload["messages"][0]
        stream = 'data: ' + json.dumps(dict(type="text-delta", delta=json.dumps(assessment()))) + '\n\ndata: [DONE]\n'
        return response(stream.encode())
    with patch("core.taufa_client._open_taufa_request", side_effect=transport):
        result = client(tmp_path).ask_score("PROJECT", "Visible character design", [image], ["proof.png"], submission_id="proof-1")
    assert len(requests) == 2
    assert result["score"] == 7
    assert result["attachments"][0]["provider_file_id"] == "file-123"


def test_failed_upload_never_confirms_or_sends_chat(tmp_path):
    states = []
    with patch("core.taufa_client._open_taufa_request", side_effect=OSError("offline")) as network:
        result = client(tmp_path).ask_score("PROJECT", "Visible design", [Path("assets/strict_bot_mascot.png").read_bytes()],
            state_callback=lambda state, _: states.append(state))
    assert result["score"] is None
    assert STATE_ATTACHMENT_CONFIRMED not in states
    assert network.call_count == 1


@pytest.mark.parametrize("text", [
    "Score: 8", json.dumps({"score": 8}),
    json.dumps({**assessment(), "score": 99}),
    json.dumps({**assessment(), "score": True}),
    json.dumps({**assessment(), "category_match": "true"}),
    json.dumps({**assessment(), "submission_id": "wrong"}),
    json.dumps({**assessment(), "warnings": ["IMAGE_NOT_INSPECTABLE"]}),
])
def test_incomplete_or_text_only_assessment_cannot_authorize_score(text):
    assert TaufaClient._extract_score_from_response(text, "proof-1")["score"] is None


def test_stream_error_is_not_a_successful_partial_assessment():
    stream = 'data: ' + json.dumps(dict(type="text-delta", delta=json.dumps(assessment())))
    stream += '\ndata: {"type":"error","errorText":"provider failed"}\n'
    with pytest.raises(ValueError):
        TaufaClient._clean_ai_stream(stream)


def test_no_images_never_reaches_provider(tmp_path):
    with patch("core.taufa_client._open_taufa_request") as network:
        result = client(tmp_path).ask_score("PROJECT", "Character artwork", [])
    assert result["score"] is None
    network.assert_not_called()


def test_relay_rejects_unpaired_device_and_keeps_session_management_on_pc():
    from core.server import StrictRecordsRequestHandler
    class RemoteHandler(StrictRecordsRequestHandler):
        def _is_local_request(self):
            return False
        def _get_authenticated_device_account(self, device_id, token):
            raise PermissionError("not paired")
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with patch("core.taufa_client.TaufaClient.ask_score") as inference:
            for path in ("/api/taufa/validate", "/api/taufa/ask", "/api/taufa/session", "/api/taufa/clear", "/api/demo/taufa_validation"):
                request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}",
                    data=b"{}" if path.endswith(("validate", "ask")) else b"",
                    headers={"Content-Type": "application/json"}, method="POST")
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                assert error.value.code == 403
            inference.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
