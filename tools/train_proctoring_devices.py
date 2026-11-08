"""
STRICTRECORDS & EXAM PROCTOR — Device Detection Training & Validation System
Trains and evaluates YOLOv8 detectors for proctoring devices (worn earbuds, mobile phones)
using the curated starter dataset at datasets/strict_session_devices_600.
Exports trained weights to ONNX for seamless integration into OpenCV / ONNXRuntime proctoring pipelines.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TrainProctoringDevices")

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / "datasets/strict_session_devices_600"
DEFAULT_OUTPUT_DIR = ROOT / "runs/proctoring_models"

SUPPORTED_TARGETS = ("earbud_worn", "mobile_phone")

KNOWN_LIMITATIONS = [
    "231 of 300 earbud images are frames from a single video sequence, restricted to train split.",
    "Covers generic wireless earbuds; specific Apple branding is uncertified.",
    "Starter collection designed to prototype and benchmark the proctoring pipeline.",
    "Requires broader multi-subject webcam diversity and negative background samples before production violation enforcement."
]


def reconcile_data_yaml(target: str, dataset_root: Optional[Path] = None) -> Path:
    """Ensure data.yaml path points to the current absolute directory on disk."""
    base_dir = (dataset_root or DATASET_ROOT) / target
    yaml_path = base_dir / "data.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"data.yaml not found at {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    expected_path = str(base_dir.resolve()).replace("\\", "/")
    if data.get("path") != expected_path:
        logger.info(f"Reconciling data.yaml path: {data.get('path')} -> {expected_path}")
        data["path"] = expected_path
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    return yaml_path


def get_dataset_statistics(target: str, dataset_root: Optional[Path] = None) -> Dict[str, Any]:
    """Inspects dataset splits and counts available images and labels."""
    base_dir = (dataset_root or DATASET_ROOT) / target
    stats = {"target": target, "splits": {}}
    total_images = 0
    total_labels = 0

    for split in ("train", "val", "test"):
        img_dir = base_dir / "images" / split
        lbl_dir = base_dir / "labels" / split
        images = list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.png"))
        labels = list(lbl_dir.glob("*.txt"))
        stats["splits"][split] = {
            "image_count": len(images),
            "label_count": len(labels),
        }
        total_images += len(images)
        total_labels += len(labels)

    stats["total_images"] = total_images
    stats["total_labels"] = total_labels
    return stats


def train_target_model(
    target: str,
    epochs: int = 5,
    imgsz: int = 320,
    batch: int = 16,
    device: str = "cpu",
    output_dir: Optional[Path] = None,
    base_model: str = "yolov8n.pt",
    seed: int = 20261104,
) -> Dict[str, Any]:
    """
    Trains a YOLOv8 detector on the specified proctoring device target.
    """
    from ultralytics import YOLO

    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target '{target}'. Must be one of {SUPPORTED_TARGETS}")

    yaml_path = reconcile_data_yaml(target)
    run_dir = (output_dir or DEFAULT_OUTPUT_DIR) / target
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"--- Starting training for [{target}] ---")
    logger.info(f"Config: epochs={epochs}, imgsz={imgsz}, batch={batch}, device={device}, base={base_model}")

    start_time = time.time()
    model = YOLO(base_model)

    train_results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        seed=seed,
        project=str(run_dir.parent),
        name=target,
        exist_ok=True,
        plots=True,
        save=True,
        verbose=True,
    )
    training_duration = time.time() - start_time

    weights_dir = run_dir / "weights"
    best_pt = weights_dir / "best.pt"
    last_pt = weights_dir / "last.pt"

    val_metrics = {}
    if hasattr(train_results, "results_dict"):
        val_metrics = train_results.results_dict
    elif hasattr(model, "trainer") and hasattr(model.trainer, "metrics"):
        val_metrics = model.trainer.metrics

    result = {
        "target": target,
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "device": device,
        "training_duration_seconds": round(training_duration, 2),
        "best_weights": str(best_pt) if best_pt.exists() else None,
        "last_weights": str(last_pt) if last_pt.exists() else None,
        "val_metrics": {k: float(v) for k, v in val_metrics.items() if isinstance(v, (int, float))},
    }
    return result


def evaluate_on_split(
    model_path: Path,
    yaml_path: Path,
    split: str = "test",
    imgsz: int = 320,
    device: str = "cpu",
) -> Dict[str, Any]:
    """Evaluates a trained model on a specific dataset split (val or test)."""
    from ultralytics import YOLO

    if not model_path.exists():
        raise FileNotFoundError(f"Model checkpoint not found: {model_path}")

    model = YOLO(str(model_path))
    val_results = model.val(
        data=str(yaml_path),
        split=split,
        imgsz=imgsz,
        device=device,
        verbose=False,
    )

    metrics = {
        "split": split,
        "precision": float(val_results.results_dict.get("metrics/precision(B)", 0.0)),
        "recall": float(val_results.results_dict.get("metrics/recall(B)", 0.0)),
        "mAP50": float(val_results.results_dict.get("metrics/mAP50(B)", 0.0)),
        "mAP50_95": float(val_results.results_dict.get("metrics/mAP50-95(B)", 0.0)),
        "fitness": float(val_results.fitness) if hasattr(val_results, "fitness") else 0.0,
        "speed_preprocess_ms": float(val_results.speed.get("preprocess", 0.0)),
        "speed_inference_ms": float(val_results.speed.get("inference", 0.0)),
        "speed_postprocess_ms": float(val_results.speed.get("postprocess", 0.0)),
    }
    return metrics


def export_to_onnx(model_path: Path, imgsz: int = 320) -> Path:
    """Exports PyTorch weights (.pt) to ONNX (.onnx) for OpenCV / ONNXRuntime."""
    from ultralytics import YOLO

    if not model_path.exists():
        raise FileNotFoundError(f"Model path does not exist: {model_path}")

    logger.info(f"Exporting {model_path} to ONNX (imgsz={imgsz})...")
    model = YOLO(str(model_path))
    exported_path = model.export(format="onnx", imgsz=imgsz, dynamic=False, simplify=True)
    onnx_path = Path(exported_path)

    # Verify with onnxruntime
    try:
        import onnxruntime as ort
        session = ort.InferenceSession(str(onnx_path))
        input_meta = session.get_inputs()[0]
        logger.info(f"ONNX verified successfully with onnxruntime. Input: {input_meta.name} shape={input_meta.shape}")
    except Exception as ex:
        logger.warning(f"ONNX validation check warning: {ex}")

    return onnx_path


def generate_visual_predictions(
    model_path: Path,
    target: str,
    output_dir: Path,
    num_samples: int = 5,
    conf_thresh: float = 0.25,
) -> List[str]:
    """Generates visual detection previews on sample test images."""
    from ultralytics import YOLO

    test_img_dir = DATASET_ROOT / target / "images/test"
    test_images = sorted(list(test_img_dir.glob("*.jpg")) + list(test_img_dir.glob("*.png")))
    if not test_images:
        return []

    samples = test_images[:num_samples]
    preview_dir = output_dir / "sample_predictions"
    preview_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(model_path))
    saved_paths = []

    for img_path in samples:
        results = model.predict(source=str(img_path), conf=conf_thresh, verbose=False)
        for r in results:
            annotated_frame = r.plot()
            out_file = preview_dir / f"pred_{img_path.name}"
            cv2.imwrite(str(out_file), annotated_frame)
            saved_paths.append(str(out_file))

    logger.info(f"Saved {len(saved_paths)} sample visual predictions to {preview_dir}")
    return saved_paths


def run_pipeline(
    target: str = "earbud_worn",
    epochs: int = 5,
    imgsz: int = 320,
    batch: int = 16,
    device: str = "cpu",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    export_onnx_flag: bool = True,
    evaluate_test_flag: bool = True,
) -> Dict[str, Any]:
    """Runs end-to-end dataset check, training, test evaluation, ONNX export, and reporting."""
    targets = SUPPORTED_TARGETS if target == "both" else [target]
    summary_report: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "dataset_root": str(DATASET_ROOT),
        "targets_executed": targets,
        "results": {},
        "limitations": KNOWN_LIMITATIONS,
    }

    for t in targets:
        logger.info(f"\n==================================================")
        logger.info(f"EXECUTING PROCTORING DETECTION PIPELINE: [{t}]")
        logger.info(f"==================================================")

        stats = get_dataset_statistics(t)
        train_res = train_target_model(
            target=t,
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            output_dir=output_dir,
        )

        best_weights = Path(train_res["best_weights"]) if train_res.get("best_weights") else None
        target_output_dir = output_dir / t

        test_metrics = {}
        if evaluate_test_flag and best_weights and best_weights.exists():
            yaml_path = reconcile_data_yaml(t)
            test_metrics = evaluate_on_split(best_weights, yaml_path, split="test", imgsz=imgsz, device=device)

        onnx_file = None
        if export_onnx_flag and best_weights and best_weights.exists():
            onnx_path = export_to_onnx(best_weights, imgsz=imgsz)
            onnx_file = str(onnx_path)

        predictions = []
        if best_weights and best_weights.exists():
            predictions = generate_visual_predictions(best_weights, t, target_output_dir)

        summary_report["results"][t] = {
            "dataset_stats": stats,
            "training": train_res,
            "test_evaluation": test_metrics,
            "onnx_model": onnx_file,
            "sample_predictions": predictions,
        }

    report_path = output_dir / "training_summary.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary_report, f, indent=2)

    logger.info(f"\n[OK] Pipeline completed! Summary report written to: {report_path}")
    return summary_report


def parse_args():
    parser = argparse.ArgumentParser(description="StrictRecords Proctoring Device Detection Training")
    parser.add_argument(
        "--target",
        choices=["earbud_worn", "mobile_phone", "both"],
        default="earbud_worn",
        help="Target device detector to train (default: earbud_worn)",
    )
    parser.add_argument("--epochs", type=int, default=5, help="Number of training epochs (default: 5)")
    parser.add_argument("--imgsz", type=int, default=320, help="Image resolution for training (default: 320)")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (default: 16)")
    parser.add_argument("--device", type=str, default="cpu", help="Device to train on ('cpu' or '0')")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory to save runs")
    parser.add_argument("--no-onnx", action="store_true", help="Disable automatic ONNX export")
    parser.add_argument("--no-test-eval", action="store_true", help="Skip evaluation on the test split")
    return parser.parse_args()


def main():
    args = parse_args()
    run_pipeline(
        target=args.target,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        output_dir=args.output_dir,
        export_onnx_flag=not args.no_onnx,
        evaluate_test_flag=not args.no_test_eval,
    )


if __name__ == "__main__":
    main()
