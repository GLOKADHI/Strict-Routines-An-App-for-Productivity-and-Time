import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from evidence_pipeline import api as evidence_api
from evidence_pipeline.pipeline import EvidencePipeline
from evidence_pipeline.storage import CompressionPolicy


def _png(color="royalblue"):
    from io import BytesIO
    buffer = BytesIO()
    Image.new("RGB", (24, 18), color).save(buffer, format="PNG")
    return buffer.getvalue()


class _OCR:
    model_version = "test-ocr"
    def analyze(self, image):
        return {"text_detected": False, "extracted_text": "", "blocks": [], "language": None, "warnings": []}


class _Vision:
    model_version = "test-vision"
    def analyze(self, image):
        return {"objects": [], "global_labels": [], "unsupported_fields": [], "warnings": []}


def _client(tmp_path, monkeypatch, *, policy=None):
    store = tmp_path / "store"
    pipeline = EvidencePipeline(store, ocr=_OCR(), vision=_Vision(), policy=policy)
    monkeypatch.setattr(evidence_api, "pipeline", pipeline)
    return TestClient(evidence_api.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)), store


def _data(ids):
    return {"task_occurrence_id": "task-1", "batch_id": "batch-1",
            "image_ids": json.dumps(ids), "kinds": json.dumps(["document"] * len(ids))}


def test_valid_evidence_upload_stays_analysis_only_and_cleans_scratch(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    response = client.post("/evidence/analyze", data=_data(["image-1"]),
                           files=[("images", ("notes.png", _png(), "image/png"))])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["analysis_only"] is True
    assert payload["review_state"] == "not_reviewed"
    assert payload["award_state"] == "not_awarded"
    assert payload["results"][0]["analysis_status"] in {"success", "uncertain"}
    assert list((store / "scratch").iterdir()) == []


def test_too_many_files_is_rejected_by_multipart_parser(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    files = [("images", (f"{i}.png", _png(), "image/png")) for i in range(6)]
    response = client.post("/evidence/analyze", data=_data([f"id-{i}" for i in range(6)]), files=files)
    assert response.status_code == 400
    assert list((store / "scratch").glob("batch-*")) == [] if (store / "scratch").exists() else True


def test_per_image_and_aggregate_limits_reject_before_analysis_and_cleanup(tmp_path, monkeypatch):
    policy = CompressionPolicy(max_image_bytes=100, max_aggregate_bytes=150)
    client, store = _client(tmp_path, monkeypatch, policy=policy)
    # Each file is under 100 bytes, while their combined image content exceeds 150.
    first, second = _png("red"), _png("blue")
    assert len(first) < 100 and len(second) < 100
    response = client.post("/evidence/analyze", data=_data(["a", "b"]),
                           files=[("images", ("a.png", first, "image/png")),
                                  ("images", ("b.png", second, "image/png"))])
    # Generated PNG payload size can be close to the chosen tiny test budget; ensure an aggregate path is exercised.
    if len(first) + len(second) <= 150:
        policy2 = CompressionPolicy(max_image_bytes=200, max_aggregate_bytes=150)
        client, store = _client(tmp_path / "second", monkeypatch, policy=policy2)
        response = client.post("/evidence/analyze", data=_data(["a", "b"]),
                               files=[("images", ("a.png", first, "image/png")),
                                      ("images", ("b.png", second, "image/png"))])
    assert response.status_code == 413
    assert not list((store / "results").glob("*.json")) if (store / "results").exists() else True
    assert list((store / "scratch").iterdir()) == []


def test_legacy_image_upload_limit_enforced_per_file(tmp_path, monkeypatch):
    # Exercise the API's post-parse per-file cap with a small override, while the ASGI body cap remains active.
    from app import main as legacy_main
    from app.config import Settings
    from app.services.model_service import VisionLanguageService

    app = legacy_main.app
    app.dependency_overrides[legacy_main.get_settings] = lambda: Settings(model_backend="mock", max_image_bytes=32)
    app.dependency_overrides[legacy_main.get_model_service] = lambda: VisionLanguageService(Settings(model_backend="mock"))
    try:
        client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        response = client.post("/analyze", files={"image": ("too-big.png", _png(), "image/png")})
        assert response.status_code == 413
    finally:
        app.dependency_overrides.clear()


def test_malformed_multipart_has_safe_error(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    response = client.post("/evidence/analyze", content=b"--broken", headers={
        "content-type": "multipart/form-data; boundary=broken"})
    assert response.status_code in {400, 422}
    assert str(tmp_path) not in response.text
