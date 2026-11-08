from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app, get_model_service
from app.services.model_service import VisionLanguageService
from app.config import Settings


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), "royalblue").save(buffer, format="PNG")
    return buffer.getvalue()


def test_health() -> None:
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analyze_in_mock_mode() -> None:
    mock_service = VisionLanguageService(Settings(model_backend="mock"))
    app.dependency_overrides[get_model_service] = lambda: mock_service
    try:
        client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        response = client.post(
            "/analyze",
            files={"image": ("sample.png", _image_bytes(), "image/png")},
            data={"question": "What is in this image?"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["metadata"]["original_width"] == 32
        assert payload["metadata"]["original_height"] == 24
        assert payload["metadata"]["width"] == 32
        assert payload["metadata"]["height"] == 24
        assert payload["objects"] == []
    finally:
        app.dependency_overrides.clear()


def test_unsafe_host_and_cross_origin_requests_are_rejected():
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert client.get("/health", headers={"host": "example.com"}).status_code == 400
    assert client.get("/health", headers={"origin": "https://attacker.example"}).status_code == 403


def test_non_loopback_peer_is_rejected():
    client = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.20", 50000))
    assert client.get("/health").status_code == 403


def test_oversized_declared_request_is_rejected_before_multipart_parsing():
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    response = client.post("/analyze", content=b"x" * (11 * 1024 * 1024 + 1),
                           headers={"content-type": "multipart/form-data; boundary=unused"})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body exceeds the allowed size."
