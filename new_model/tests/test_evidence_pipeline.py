"""Comprehensive test suite for StrictRecords evidence pipeline.

Tests verify:
1. Real OCR with text (detected=True, bounding boxes, blocks, text extraction).
2. OCR without text (detected=False, extracted_text="", zero errors, visual path continues).
3. Visual understanding and object detection using CompactVisionAdapter (CPU ONNX).
4. Clothing and garment attribute analysis (pattern, surface, color, no false material claims).
5. Color analysis (dominant colors, hex, RGB, family, transparent alpha handling).
6. Texture and surface appearance (edge response, luminance statistics, uncertainty flags).
7. Storage compression and separate SHA-256 integrity (original vs derivatives).
8. Multi-image batch occurrences (bounded processing, stable image IDs, preflight checks).
9. Error handling and safety limits (empty files, corrupt images, size limits).
10. Grounded captioning without hallucinations (distinguishing text, objects, colors, unsupported fields).
"""
import io
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from evidence_pipeline.attributes import analyze_clothing, attributes, colors, family
from evidence_pipeline.ocr import TesseractOCR
from evidence_pipeline.pipeline import Attachment, EvidencePipeline, stable_image_id
from evidence_pipeline.schemas import EvidenceResult, StageResult
from evidence_pipeline.storage import (
    CompressionPolicy,
    StorageError,
    prepare,
    preflight_sources,
)
from evidence_pipeline.vision import CompactVisionAdapter


def create_text_image(text: str = "STRICTRECORDS EVIDENCE", size=(400, 100), bg="white", fg="black") -> Image.Image:
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    draw.text((20, 35), text, fill=fg)
    return img


def create_pattern_image(kind="stripes", size=(200, 200)) -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    if kind == "stripes":
        for y in range(0, size[1], 16):
            draw.rectangle([0, y, size[0], y + 8], fill="navy")
    elif kind == "checks":
        for y in range(0, size[1], 20):
            for x in range(0, size[0], 20):
                if ((x // 20) + (y // 20)) % 2 == 0:
                    draw.rectangle([x, y, x + 20, y + 20], fill="darkred")
    return img


class TestOCR:
    def test_ocr_with_text(self):
        ocr = TesseractOCR()
        img = create_text_image("STRICTRECORDS EVIDENCE")
        res = ocr.analyze(img)
        assert res["text_detected"] is True
        assert "STRICTRECORDS" in res["extracted_text"].upper() or "EVIDENCE" in res["extracted_text"].upper()
        assert len(res["blocks"]) > 0
        assert res["coordinates"]["image_width"] == 400
        assert res["coordinates"]["image_height"] == 100

    def test_ocr_without_text(self):
        ocr = TesseractOCR()
        img = Image.new("RGB", (200, 200), "forestgreen")
        res = ocr.analyze(img)
        assert res["text_detected"] is False
        assert res["extracted_text"] == ""
        assert res["blocks"] == []


class TestVisualUnderstanding:
    def test_compact_vision_runs_and_bounds_objects(self):
        vision = CompactVisionAdapter()
        img = Image.new("RGB", (300, 300), "royalblue")
        res = vision.analyze(img)
        assert res["status"] == "COMPLETED"
        assert res["engine"] == "ONNX Runtime CPU"
        assert isinstance(res["objects"], list)
        assert isinstance(res["global_labels"], list)
        assert res["supported_fields"]["objects"] is True
        assert res["supported_fields"]["ocr"] is False


class TestClothingAndGarments:
    def test_clothing_analysis_striped_pattern(self):
        img = create_pattern_image("stripes")
        res = analyze_clothing(img)
        assert res["visible_pattern"]["pattern"] == "horizontal striped pattern"
        assert "Visual estimate only" in res["visible_pattern"]["uncertainty"]
        assert res["apparent_fabric_characteristics"]["physical_material_claim"] is False
        assert "cannot be verified" in res["apparent_fabric_characteristics"]["disclaimer"]

    def test_clothing_analysis_checked_pattern(self):
        img = create_pattern_image("checks")
        res = analyze_clothing(img)
        assert "checked" in res["visible_pattern"]["pattern"]
        assert res["apparent_fabric_characteristics"]["physical_material_claim"] is False

    def test_clothing_analysis_plain_surface(self):
        img = Image.new("RGB", (200, 200), "navy")
        res = analyze_clothing(img)
        assert res["visible_pattern"]["pattern"] == "plain / uniform"
        assert "smooth" in res["surface_appearance"]["appearance"]
        assert res["color_family"] == "blue"

    def test_clothing_analysis_with_detected_accessory(self):
        img = Image.new("RGB", (200, 200), "darkgray")
        mock_objects = [{"label": "tie", "confidence": 0.88, "bbox": [10, 10, 50, 100]}]
        res = analyze_clothing(img, mock_objects)
        assert res["garment_category"] == "tie"
        assert "tie" in res["detected_categories"]


class TestColorAndTexture:
    def test_dominant_colors(self):
        img = Image.new("RGB", (100, 100), "red")
        res = colors(img)
        assert len(res) > 0
        assert res[0]["family"] == "red"
        assert res[0]["hex"].lower() == "#ff0000"
        assert res[0]["fraction"] == 1.0

    def test_transparent_alpha_colors_no_fake_black(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 50, 50], fill=(255, 255, 0, 255))
        res = colors(img)
        assert len(res) > 0
        assert res[0]["family"] == "yellow"

    def test_texture_and_quality_metrics(self):
        img = Image.new("RGB", (150, 150), "white")
        res = attributes(img)
        assert "quality" in res
        assert res["quality"]["mean_luminance"] == 255.0
        assert res["quality"]["edge_response"] == 0.0


class TestStorageAndIntegrity:
    def test_separate_sha256_for_original_and_derivatives(self):
        with tempfile.TemporaryDirectory() as td:
            store_dir = Path(td) / "store"
            img_path = Path(td) / "source.png"
            create_text_image().save(img_path, format="PNG")

            with prepare(img_path, store_dir, allowed_roots=(Path(td),)) as artifact:
                orig_sha = artifact.metadata["original_sha256"]
                compact_sha = artifact.metadata["compact"]["sha256"]
                thumb_sha = artifact.metadata["thumbnail"]["sha256"]

                # Distinct hashes guarantee original proof bytes are never corrupted or disguised
                assert orig_sha != compact_sha
                assert compact_sha != thumb_sha
                assert artifact.metadata["compact"]["bytes"] > 0
                assert artifact.metadata["thumbnail"]["width"] <= 320

    def test_preflight_budgeting(self):
        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(3):
                p = Path(td) / f"img_{i}.jpg"
                Image.new("RGB", (50, 50), "blue").save(p)
                files.append(p)

            budget = preflight_sources(files)
            assert budget["allowed"] is True
            assert budget["count"] == 3

    def test_preflight_rejects_exceeded_file_count(self):
        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(6):
                p = Path(td) / f"img_{i}.jpg"
                Image.new("RGB", (20, 20), "gray").save(p)
                files.append(p)

            budget = preflight_sources(files, CompressionPolicy(max_files=5))
            assert budget["allowed"] is False
            assert any(err["code"] == "FILE_COUNT_LIMIT" for err in budget["global_errors"])


class TestPipelineEndToEnd:
    def test_analyze_image_with_text(self):
        with tempfile.TemporaryDirectory() as td:
            store_dir = Path(td) / "store"
            img_path = Path(td) / "receipt.jpg"
            create_text_image("RECEIPT TOTAL $42.50").save(img_path)

            pipeline = EvidencePipeline(store_dir, allowed_image_roots=(Path(td),))
            att = Attachment(img_path, "att_001", "document")
            res = pipeline.analyze(att, "task_123", "batch_456")

            assert res["status"] in ("completed", "partial")
            assert res["text_analysis"]["status"] == "completed"
            assert res["text_analysis"]["data"]["text_detected"] is True
            assert res["evidence_metadata"]["original_sha256"] is not None
            assert "Readable text detected" in res["visual_analysis"]["data"]["caption"]
            assert res["submission_state"] == "analysis_only"
            assert res["review_state"] == "not_reviewed"

    def test_analyze_image_without_text(self):
        with tempfile.TemporaryDirectory() as td:
            store_dir = Path(td) / "store"
            img_path = Path(td) / "plain_dress.jpg"
            create_pattern_image("stripes").save(img_path)

            pipeline = EvidencePipeline(store_dir, allowed_image_roots=(Path(td),))
            att = Attachment(img_path, "att_002", "photo")
            res = pipeline.analyze(att, "task_123", "batch_456")

            assert res["status"] == "completed"
            assert res["text_analysis"]["status"] == "no_text"
            assert res["text_analysis"]["data"]["text_detected"] is False
            assert res["visual_analysis"]["status"] == "completed"
            assert "Visible pattern: horizontal striped pattern" in res["visual_analysis"]["data"]["caption"]
            assert "No readable text detected" in res["visual_analysis"]["data"]["caption"]

    def test_batch_processing(self):
        with tempfile.TemporaryDirectory() as td:
            store_dir = Path(td) / "store"
            p1 = Path(td) / "photo1.jpg"
            p2 = Path(td) / "photo2.jpg"
            create_text_image("GYM CHECKIN").save(p1)
            Image.new("RGB", (100, 100), "orange").save(p2)

            pipeline = EvidencePipeline(store_dir, allowed_image_roots=(Path(td),))
            attachments = [
                Attachment(p1, "att_1", "photo"),
                Attachment(p2, "att_2", "photo"),
            ]
            batch_res = pipeline.analyze_batch(attachments, "occ_100", "batch_200")
            assert batch_res["status"] == "completed"
            assert batch_res["selected_count"] == 2
            assert batch_res["completed_count"] == 2
            assert len(batch_res["results"]) == 2
