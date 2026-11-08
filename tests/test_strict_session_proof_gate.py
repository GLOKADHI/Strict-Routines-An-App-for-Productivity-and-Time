"""Windows strict-session scoring tests isolated from live OS controls."""

import io
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from core.database import StrictRecordsDB
from core.models import HourlyProofStatus
from core.scoring_engine import ScoringEngine
from windows.restriction_manager import YouTubeRestrictionManager
from windows.strict_session_manager import StrictSessionManager


class TestStrictSessionProofGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="strict_session_proof_gate_")
        self.db = StrictRecordsDB(os.path.join(self.temp_dir.name, "test.db"))
        self.manager = StrictSessionManager(db=self.db, data_dir=self.temp_dir.name)
        # These methods may apply a machine-wide PAC rule on Windows. Keep this
        # unit test focused on scoring and prevent any live network/registry edits.
        self.manager.network_policy_manager = SimpleNamespace(
            update_privilege=lambda _allowed: True,
            is_active=False,
        )
        self.manager.browser_policy_manager = SimpleNamespace(
            entertainment_allowed=False,
            is_active=False,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fresh_start_and_missing_proof_keep_score_at_zero_and_privilege_blocked(self):
        self.assertEqual(self.manager.current_score, 0)
        self.assertEqual(self.manager.verified_points, 0)
        self.assertEqual(self.manager.hourly_proof_status, HourlyProofStatus.PENDING)

        result = self.manager.evaluate_privilege()
        self.assertEqual(result["current_score"], 0)
        self.assertFalse(result["allowed"])

        restriction = YouTubeRestrictionManager(db=self.db, engine=self.manager._scoring_engine)
        self.assertEqual(restriction.get_points_status()["current_score"], 0)
        restriction.db = None
        restriction._points_cache = None
        self.assertEqual(restriction.get_points_status()["current_score"], 0)
        oversized = restriction.request_educational_unlock(
            reason="programming tutorial",
            duration_minutes=999999,
        )
        self.assertFalse(oversized["success"])

        result = self.manager.submit_hourly_proof(evidence_id="not-a-proof")
        self.assertEqual(result["status"], HourlyProofStatus.INVALID.value)
        self.assertEqual(result["privilege"]["current_score"], 0)
        self.assertFalse(result["privilege"]["allowed"])
        self.assertEqual(self.db.get_score_events(result["timestamp"][:10], is_dev_mode=False), [])

    def test_only_approved_retained_image_contributes_to_strict_session_score(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (120, 120), color=(45, 95, 150)).save(image_bytes, format="JPEG")
        scoring = ScoringEngine(db=self.db, storage_root=self.temp_dir.name)
        evidence = scoring.store_evidence_file(
            date=datetime.now().strftime("%Y-%m-%d"),
            record_type="Hourly",
            related_record_id="hourly-test",
            device_id="windows-test-device",
            file_bytes=image_bytes.getvalue(),
            filename_hint="proof.jpg",
        )
        approved = scoring.apply_proof_approval(
            evidence_id=evidence["evidence_id"],
            admin_id="independent-test-admin",
            points=7,
            reason="Regression test approval",
        )
        self.assertEqual(approved["points_awarded"], 7)

        with patch.object(ScoringEngine, "evaluate_proof_with_ai", return_value={"decision": "REVIEW_REQUIRED"}):
            result = self.manager.submit_hourly_proof(evidence["evidence_id"])
        self.assertEqual(result["status"], HourlyProofStatus.VALID.value)
        self.assertEqual(result["privilege"]["current_score"], 7)
        self.assertEqual(result["privilege"]["verified_points"], 7)
        self.assertFalse(result["privilege"]["allowed"], "A valid proof under the threshold must not unlock privileges")
        restriction = YouTubeRestrictionManager(db=self.db, engine=scoring)
        self.assertEqual(restriction.get_points_status()["current_score"], 7)


if __name__ == "__main__":
    unittest.main()
