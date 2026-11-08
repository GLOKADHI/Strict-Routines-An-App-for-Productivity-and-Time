"""
Unit and regression tests for proctoring device detector training pipeline (tools/train_proctoring_devices.py).
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.train_proctoring_devices import (
    DATASET_ROOT,
    KNOWN_LIMITATIONS,
    SUPPORTED_TARGETS,
    get_dataset_statistics,
    reconcile_data_yaml,
    run_pipeline,
)


class TestTrainProctoringDevices(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_dataset_statistics_and_splits(self):
        """Verify that dataset splits match expected counts from the starter collection."""
        earbud_stats = get_dataset_statistics("earbud_worn")
        self.assertEqual(earbud_stats["total_images"], 300)
        self.assertEqual(earbud_stats["splits"]["train"]["image_count"], 235)
        self.assertEqual(earbud_stats["splits"]["val"]["image_count"], 30)
        self.assertEqual(earbud_stats["splits"]["test"]["image_count"], 35)

        phone_stats = get_dataset_statistics("mobile_phone")
        self.assertEqual(phone_stats["total_images"], 300)
        self.assertEqual(phone_stats["splits"]["train"]["image_count"], 240)
        self.assertEqual(phone_stats["splits"]["val"]["image_count"], 30)
        self.assertEqual(phone_stats["splits"]["test"]["image_count"], 30)

    def test_02_reconcile_data_yaml(self):
        """Verify that data.yaml paths resolve to absolute directory on this filesystem."""
        for target in SUPPORTED_TARGETS:
            yaml_path = reconcile_data_yaml(target)
            self.assertTrue(yaml_path.exists())
            text = yaml_path.read_text(encoding="utf-8")
            self.assertIn("names:", text)
            self.assertIn("nc: 1", text)
            self.assertIn("train: images/train", text)

    def test_03_limitations_documented(self):
        """Verify known limitations acknowledge the single-video 231 earbud frames."""
        limitations_text = " ".join(KNOWN_LIMITATIONS)
        self.assertIn("231", limitations_text)
        self.assertIn("single video", limitations_text.lower())
        self.assertIn("generic wireless earbuds", limitations_text.lower())

    def test_04_end_to_end_quick_train_and_onnx_export(self):
        """Run a 1-epoch quick pipeline verification, checking best.pt, best.onnx, and summary report."""
        output_dir = self.temp_dir / "test_run"
        report = run_pipeline(
            target="earbud_worn",
            epochs=1,
            imgsz=320,
            batch=16,
            device="cpu",
            output_dir=output_dir,
            export_onnx_flag=True,
            evaluate_test_flag=True,
        )

        self.assertIn("results", report)
        self.assertIn("earbud_worn", report["results"])
        earbud_res = report["results"]["earbud_worn"]

        # Check weights exist
        best_pt = Path(earbud_res["training"]["best_weights"])
        self.assertTrue(best_pt.exists(), f"best.pt missing: {best_pt}")

        # Check ONNX exists
        onnx_file = Path(earbud_res["onnx_model"])
        self.assertTrue(onnx_file.exists(), f"best.onnx missing: {onnx_file}")

        # Check test metrics exist
        test_metrics = earbud_res["test_evaluation"]
        self.assertEqual(test_metrics["split"], "test")
        self.assertIn("mAP50", test_metrics)

        # Check report file exists
        report_file = output_dir / "training_summary.json"
        self.assertTrue(report_file.exists())
        saved_data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["targets_executed"], ["earbud_worn"])


if __name__ == "__main__":
    unittest.main()
