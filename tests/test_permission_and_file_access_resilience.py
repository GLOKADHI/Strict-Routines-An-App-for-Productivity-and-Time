"""Unit and regression tests for Windows file access resilience and explicit WebView2 permissions."""

import io
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from core.database import StrictRecordsDB
from core.hourly_submission_service import HourlySubmissionService


class TestPermissionAndFileAccessResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_db.sqlite")
        self.storage_root = os.path.join(self.temp_dir, "STRICTRECORDS")
        self.db = StrictRecordsDB(db_path=self.db_path)
        self.engine = MagicMock()
        self.engine.storage_root = self.storage_root
        self.service = HourlySubmissionService(self.db, self.engine, storage_root=self.storage_root)

        # Generate a valid tiny PNG image
        img = Image.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.valid_png_bytes = buf.getvalue()

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_safe_write_evidence_normal_flow(self):
        """Verifies _safe_write_evidence_file writes the file and cleans up temp."""
        final_path = os.path.join(self.storage_root, "test_dir", "ev_test_1.png")
        temp_path = final_path + ".uploading"

        HourlySubmissionService._safe_write_evidence_file(temp_path, final_path, self.valid_png_bytes)

        self.assertTrue(os.path.isfile(final_path))
        self.assertEqual(os.path.getsize(final_path), len(self.valid_png_bytes))
        self.assertFalse(os.path.isfile(temp_path))

    def test_02_safe_write_evidence_handles_transient_winerror5_lock(self):
        """Simulates Windows Defender holding a transient read lock (WinError 5) on .uploading."""
        final_path = os.path.join(self.storage_root, "test_dir", "ev_test_lock.png")
        temp_path = final_path + ".uploading"

        orig_replace = os.replace
        calls = {"count": 0}

        def mock_replace(src, dst):
            calls["count"] += 1
            if calls["count"] < 3:
                # Simulate WinError 5 Access is denied on first 2 attempts
                raise PermissionError(5, "Access is denied (simulated lock)", src)
            return orig_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            HourlySubmissionService._safe_write_evidence_file(temp_path, final_path, self.valid_png_bytes)

        self.assertTrue(calls["count"] >= 3, "Expected at least 3 retry attempts")
        self.assertTrue(os.path.isfile(final_path))
        self.assertEqual(os.path.getsize(final_path), len(self.valid_png_bytes))
        self.assertFalse(os.path.isfile(temp_path))

    def test_03_safe_write_evidence_falls_back_to_copy_on_persistent_winerror5(self):
        """Simulates persistent WinError 5 on os.replace, verifying fallback to copyfile."""
        final_path = os.path.join(self.storage_root, "test_dir", "ev_test_fallback.png")
        temp_path = final_path + ".uploading"

        def mock_replace_always_fail(src, dst):
            raise PermissionError(5, "Access is denied (persistent lock)", src)

        with patch("os.replace", side_effect=mock_replace_always_fail):
            HourlySubmissionService._safe_write_evidence_file(temp_path, final_path, self.valid_png_bytes)

        self.assertTrue(os.path.isfile(final_path))
        self.assertEqual(os.path.getsize(final_path), len(self.valid_png_bytes))
        self.assertFalse(os.path.isfile(temp_path))



if __name__ == "__main__":
    unittest.main()
