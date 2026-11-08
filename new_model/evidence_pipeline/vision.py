"""Isolated, offline object detection; no task verification or model downloads.

YOLOX Nano release 0.1.1rc0 preprocessing/decoding follows the upstream
Apache-2.0 deployment contract. Model attribution and license are in models/.
The legacy ten-class classifier is not used as general visual intelligence.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Any

from PIL import Image

from .schemas import ModelUnavailable


COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)
MODEL_VERSION = "YOLOX-Nano-COCO-0.1.1rc0"
MODEL_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
MODEL_BYTES = 3_659_407


class CompactVisionAdapter:
    """Lazy CPU-only ONNX detector with verified local weights and bounded output.

    Bounding boxes use pixels in the supplied PIL image: [x, y, width, height].
    Global labels are an aggregation of detected categories, not scene captions.
    Material, pattern, identity and general garment classification are unsupported.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        confidence_threshold: float = 0.30,
        iou_threshold: float = 0.45,
        max_objects: int = 100,
    ):
        if not math.isfinite(confidence_threshold) or not 0 < confidence_threshold < 1:
            raise ValueError("Confidence threshold must be finite and between zero and one.")
        if not math.isfinite(iou_threshold) or not 0 < iou_threshold < 1:
            raise ValueError("IoU threshold must be finite and between zero and one.")
        if isinstance(max_objects, bool) or not isinstance(max_objects, int) or not 1 <= max_objects <= 100:
            raise ValueError("Object limit must be between 1 and 100.")
        self.model_dir = Path(model_dir) if model_dir is not None else Path(__file__).parent / "models"
        self.confidence_threshold = float(confidence_threshold)
        self.iou_threshold = float(iou_threshold)
        self.max_objects = max_objects
        self._lock = threading.RLock()
        self._session = None
        self._np = None
        self._grid = None
        self._strides = None

    def _load(self):
        with self._lock:
            if self._session is not None:
                return
            model = self.model_dir / "yolox_nano.onnx"
            manifest = self.model_dir / "yolox_nano.manifest.json"
            if not model.is_file() or not manifest.is_file():
                raise ModelUnavailable("The isolated YOLOX Nano weights/manifest are unavailable. No automatic download is attempted.")
            try:
                details = json.loads(manifest.read_text(encoding="utf-8"))
                if details.get("sha256") != MODEL_SHA256 or details.get("model_version") != MODEL_VERSION:
                    raise ModelUnavailable("The vision manifest does not match the supported pinned model.")
                if model.stat().st_size != MODEL_BYTES:
                    raise ModelUnavailable("The vision model size does not match its manifest.")
                digest = hashlib.sha256()
                with model.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != MODEL_SHA256:
                    raise ModelUnavailable("The vision model failed SHA-256 integrity verification.")
                import numpy as np
                import onnxruntime as ort
            except ModelUnavailable:
                raise
            except (ImportError, OSError, ValueError) as exc:
                raise ModelUnavailable("Vision requires local NumPy and ONNX Runtime; existing environments are not modified.") from exc
            try:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
                inputs, outputs = session.get_inputs(), session.get_outputs()
                if len(inputs) != 1 or inputs[0].shape != [1, 3, 416, 416] or inputs[0].type != "tensor(float)":
                    raise ModelUnavailable("The vision input shape does not match the pinned YOLOX Nano model.")
                if len(outputs) != 1 or outputs[0].shape != [1, 3549, 85]:
                    raise ModelUnavailable("The vision output shape does not match its COCO object-detection contract.")
                grids, strides = [], []
                for stride in (8, 16, 32):
                    size = 416 // stride
                    y, x = np.mgrid[:size, :size]
                    grids.append(np.column_stack((x.ravel(), y.ravel())))
                    strides.append(np.full((size * size, 1), stride, dtype=np.float32))
                self._grid = np.concatenate(grids).astype(np.float32)
                self._strides = np.concatenate(strides)
                self._np, self._session = np, session
            except ModelUnavailable:
                raise
            except Exception as exc:
                raise ModelUnavailable("The verified vision model could not initialize on the local CPU runtime.") from exc

    def _input(self, image: Image.Image):
        np = self._np
        ratio = min(416 / image.width, 416 / image.height)
        width, height = max(1, int(image.width * ratio)), max(1, int(image.height * ratio))
        # Resize before converting to an array, bounding additional pixel memory.
        resized = image.resize((width, height), Image.Resampling.BILINEAR).convert("RGB")
        canvas = np.full((416, 416, 3), 114.0, dtype=np.float32)
        canvas[:height, :width] = np.asarray(resized, dtype=np.float32)
        # This older release expects RGB / 255 and ImageNet mean/std, not the
        # unnormalized BGR preprocessing used by newer YOLOX releases.
        canvas /= 255.0
        canvas -= np.array((0.485, 0.456, 0.406), dtype=np.float32)
        canvas /= np.array((0.229, 0.224, 0.225), dtype=np.float32)
        return np.ascontiguousarray(canvas.transpose(2, 0, 1)[None]), ratio

    def _decode(self, raw, ratio: float, width: int, height: int):
        np = self._np
        predictions = np.asarray(raw, dtype=np.float32)
        if predictions.shape != (1, 3549, 85) or not np.isfinite(predictions).all():
            raise RuntimeError("Vision returned an invalid prediction tensor.")
        predictions = predictions[0]
        class_scores = predictions[:, 4:5] * predictions[:, 5:]
        classes = class_scores.argmax(axis=1)
        confidence = class_scores[np.arange(len(predictions)), classes]
        mask = (confidence >= self.confidence_threshold) & (confidence <= 1.0)
        rows = np.flatnonzero(mask)
        if not rows.size:
            return [], False
        # Limit NMS work even if an unusual input produces thousands of candidates.
        rows = rows[np.argsort(-confidence[rows], kind="stable")[:2000]]
        center = (predictions[rows, :2] + self._grid[rows]) * self._strides[rows]
        extent = np.exp(np.clip(predictions[rows, 2:4], -20, 20)) * self._strides[rows]
        boxes = np.column_stack((center - extent / 2, center + extent / 2)) / ratio
        boxes[:, (0, 2)] = np.clip(boxes[:, (0, 2)], 0, width)
        boxes[:, (1, 3)] = np.clip(boxes[:, (1, 3)], 0, height)
        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        boxes, scores, ids = boxes[valid], confidence[rows][valid], classes[rows][valid]
        remaining = np.argsort(-scores, kind="stable")
        selected = []
        while remaining.size and len(selected) < self.max_objects:
            current = int(remaining[0])
            selected.append(current)
            rest = remaining[1:]
            if not rest.size:
                remaining = rest
                break
            intersection_width = np.maximum(0, np.minimum(boxes[current, 2], boxes[rest, 2]) - np.maximum(boxes[current, 0], boxes[rest, 0]))
            intersection_height = np.maximum(0, np.minimum(boxes[current, 3], boxes[rest, 3]) - np.maximum(boxes[current, 1], boxes[rest, 1]))
            intersection = intersection_width * intersection_height
            area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            overlap = intersection / np.maximum(area[current] + area[rest] - intersection, 1e-8)
            remaining = rest[(ids[rest] != ids[current]) | (overlap <= self.iou_threshold)]
        objects = []
        for index in selected:
            x1, y1, x2, y2 = (round(float(coordinate), 2) for coordinate in boxes[index])
            box_width, box_height = round(x2 - x1, 2), round(y2 - y1, 2)
            if box_width <= 0 or box_height <= 0:
                continue
            objects.append({"label": COCO_CLASSES[int(ids[index])], "confidence": round(float(scores[index]), 4),
                "bbox": [x1, y1, box_width, box_height], "source": "detected"})
        return objects, bool(remaining.size)

    def analyze(self, image: Image.Image) -> dict[str, Any]:
        if not isinstance(image, Image.Image) or image.width <= 0 or image.height <= 0:
            raise ValueError("Vision requires a decoded, nonempty PIL image.")
        self._load()
        with self._lock:
            tensor, ratio = self._input(image)
            output = self._session.run(None, {self._session.get_inputs()[0].name: tensor})[0]
            objects, limited = self._decode(output, ratio, image.width, image.height)
        aggregated = {}
        for item in objects:
            aggregated[item["label"]] = max(aggregated.get(item["label"], 0), item["confidence"])
        warnings = [
            "YOLOX Nano recognizes 80 COCO object categories; predictions may miss or mislabel objects outside that domain.",
            "Global labels are derived from detected objects, not an independent scene classifier or caption.",
            "This detector does not read text, verify task completion, identify general garments, measure patterns or determine material composition.",
        ]
        if not objects:
            warnings.append("No object met the detection threshold; this does not prove the image contains no objects.")
        if limited:
            warnings.append("The bounded detection limit was reached; additional objects may be omitted.")
        return {"status": "COMPLETED", "engine": "ONNX Runtime CPU", "model_version": MODEL_VERSION,
            "model_sha256": MODEL_SHA256, "confidence_threshold": self.confidence_threshold,
            "objects": objects, "global_labels": [
                {"label": label, "confidence": score, "source": "derived_from_detections"}
                for label, score in sorted(aggregated.items(), key=lambda entry: (-entry[1], entry[0]))],
            "patterns": [], "surface_appearance": [], "possible_materials": [], "warnings": warnings,
            "supported_fields": {"objects": True, "bounding_boxes": True, "global_labels": True,
                "independent_scene_classification": False, "general_clothing_labels": False,
                "ocr": False, "patterns": False, "surface_appearance": False, "possible_materials": False}}
