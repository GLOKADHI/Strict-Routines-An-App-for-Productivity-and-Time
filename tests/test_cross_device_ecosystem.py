"""
StrictRecords Cross-Device AI Proof, Shared Scoring, and Device Registration Tests
Verifies:
1. PC and Android are two registered clients of ONE StrictRecords Account.
2. Shared score consistency across Android and Windows.
3. Cross-device AI proof verification and anti-cheat hash deduplication.
4. Device registration and Wi-Fi pairing mesh.
5. Admin review integration affecting shared score.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from core.ai_analyzer import AIProofAnalyzer
from core.connection_pipeline import ConnectionPipelineManager, ConnectionState, TransportType
from core.database import StrictRecordsDB
from core.models import ScoreEventType
from core.scoring_engine import ScoringEngine
from windows.restriction_manager import YouTubeRestrictionManager


class TestCrossDeviceEcosystem(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cross_device.db")
        self.db = StrictRecordsDB(self.db_path)
        self.engine = ScoringEngine(db=self.db)
        self.conn_mgr = ConnectionPipelineManager(db=self.db, scoring_engine=self.engine)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_single_account_two_registered_clients(self):
        """PC and Android are registered clients of ONE StrictRecords Account (user_lok)."""
        # Register Windows Client
        win_dev = self.conn_mgr.register_client_device(
            device_id="windows_lok_pc",
            device_name="Windows 11 PC",
            platform="WINDOWS",
            app_version="2.4.0",
            battery_level=100,
            user_id="user_lok",
            transport="LOCAL_WIFI",
        )
        self.assertTrue(win_dev["registered"])
        self.assertEqual(win_dev["account_id"], "user_lok")

        # Register Android Client
        and_dev = self.conn_mgr.register_client_device(
            device_id="android_lok_phone",
            device_name="Samsung Galaxy",
            platform="ANDROID",
            app_version="2.0.0",
            battery_level=92,
            user_id="user_lok",
            transport="LOCAL_WIFI",
        )
        self.assertTrue(and_dev["registered"])
        self.assertEqual(and_dev["account_id"], "user_lok")

        # Verify Device Mesh
        mesh = self.conn_mgr.get_account_device_mesh(user_id="user_lok")
        self.assertEqual(mesh["account_id"], "user_lok")
        self.assertIsNotNone(mesh["windows_client"])
        self.assertIsNotNone(mesh["android_client"])
        self.assertEqual(mesh["mesh_status"], "ONLINE")

    def test_shared_score_updates_from_android_to_windows(self):
        """Points earned on Android update the central shared score and unlock Windows privileges."""
        # Setup restriction manager on Windows PC
        restr_mgr = YouTubeRestrictionManager(db=self.db, engine=self.engine)
        initial_status = restr_mgr.get_points_status()
        self.assertEqual(initial_status["current_score"], 0)
        self.assertEqual(initial_status["remaining_points"], 80)

        # Android earns morning wake-up points (+20) and bath points (+10)
        sync_items = [
            {
                "sync_id": "sync_wake_01",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": "se_wake_01",
                    "date": self.today,
                    "event_type": ScoreEventType.WAKEUP_COMPLETED.value,
                    "points": 20,
                    "description": "Android wake-up verified on-time",
                    "audit": {"Device": "Android (Lok Phone)"},
                },
            },
            {
                "sync_id": "sync_bath_02",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": "se_bath_02",
                    "date": self.today,
                    "event_type": ScoreEventType.BATH_COMPLETED.value,
                    "points": 10,
                    "description": "Android cold shower bath complete",
                    "audit": {"Device": "Android (Lok Phone)"},
                },
            },
        ]

        # Android syncs with central server
        sync_res = self.engine.process_sync_batch(sync_items, device_id="android_lok_phone")
        self.assertEqual(sync_res["applied_count"], 2)
        self.assertEqual(sync_res["shared_score"], 30)

        # Clear cache and check score on Windows
        restr_mgr._points_cache = None
        updated_status = restr_mgr.get_points_status()
        self.assertEqual(updated_status["current_score"], 30)
        self.assertEqual(updated_status["remaining_points"], 50)

        # Android completes more tasks to reach 85 points (> 80 reward threshold)
        bonus_items = [
            {
                "sync_id": "sync_work_03",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": "se_work_03",
                    "date": self.today,
                    "event_type": ScoreEventType.HOURLY_PRODUCTIVE.value,
                    "points": 40,
                    "description": "Focus and deep work completed",
                },
            },
            {
                "sync_id": "sync_gym_04",
                "payload_type": "SCORE_EVENT",
                "payload": {
                    "event_id": "se_gym_04",
                    "date": self.today,
                    "event_type": ScoreEventType.GYM_COMPLETED.value,
                    "points": 15,
                    "description": "Evening gym session completed",
                },
            },
        ]
        sync_res2 = self.engine.process_sync_batch(bonus_items, device_id="android_lok_phone")
        self.assertEqual(sync_res2["shared_score"], 85)
        self.assertTrue(sync_res2["reward_unlocked"])

        # Windows verifies privilege unlocked
        restr_mgr._points_cache = None
        unlocked_status = restr_mgr.get_points_status()
        self.assertEqual(unlocked_status["current_score"], 85)
        self.assertEqual(unlocked_status["remaining_points"], 0)

    def test_ai_proof_analyzer_valid_and_anti_cheat(self):
        """AI Proof Analyzer correctly verifies authentic proof and rejects duplicate hashes."""
        # 1. Authentic proof submission from Android
        res = AIProofAnalyzer.analyze_proof(
            evidence_id="ev_test_wake",
            task_code="WAKE_UP",
            task_title="Morning Wake-Up Checkpoint",
            timestamp=f"{self.today} 07:02:15",
            device_id="android_lok_phone",
            platform="ANDROID",
            sha256_hash="abc123uniquehash456",
            existing_hashes=["prior_hash_1", "prior_hash_2"],
        )
        # A hash and a claim, without actual retained image data, are not proof.
        self.assertEqual(res["verdict"], "NEEDS_REVIEW")
        self.assertFalse(res["auto_approved"])
        self.assertEqual(res["suggested_points"], 0)

        # 2. Tampered / Duplicate proof submission (Anti-Cheat check)
        res_tampered = AIProofAnalyzer.analyze_proof(
            evidence_id="ev_test_cheat",
            task_code="GYM",
            task_title="Evening Gym Workout Session",
            timestamp=f"{self.today} 18:30:00",
            device_id="android_lok_phone",
            platform="ANDROID",
            sha256_hash="prior_hash_1",  # Matches existing hash!
            existing_hashes=["prior_hash_1", "prior_hash_2"],
        )
        self.assertEqual(res_tampered["verdict"], "REJECTED")
        self.assertFalse(res_tampered["auto_approved"])
        self.assertEqual(res_tampered["suggested_points"], 0)
        self.assertTrue(any(s["code"] == "DUPLICATE_PHOTO_HASH" for s in res_tampered["anti_cheat_signals"]))

    def test_admin_cross_device_proof_review_affects_shared_score(self):
        """Admin reviewing and approving proof awards points directly to the shared account score."""
        initial_score = self.engine.get_full_day_state(self.today)["daily_record"]["final_score"]

        # Register a pending proof
        from PIL import Image
        import hashlib
        retained_path=os.path.join(self.test_dir,'pending-research.jpg')
        Image.new('RGB',(120,120),(80,100,130)).save(retained_path,format='JPEG')
        with open(retained_path,'rb') as retained:retained_hash=hashlib.sha256(retained.read()).hexdigest()
        self.db.register_proof_review(
            evidence_id="ev_pending_01",
            date=self.today,
            task_code="CUSTOM_TASK",
            task_title="Research Presentation Prep",
            file_path=retained_path,
            file_hash=retained_hash,
            user_id="user_lok",
        )

        # Admin approves from mobile / desktop console
        approval_res = self.engine.apply_proof_approval(
            evidence_id="ev_pending_01",
            admin_id="Admin Mobile",
            points=5,
            reason="Verified research slides",
        )
        self.assertEqual(approval_res["status"], "APPROVED")

        # Verify shared score incremented by 5
        updated_score = self.engine.get_full_day_state(self.today)["daily_record"]["final_score"]
        self.assertEqual(updated_score, initial_score + 5)

    def test_shared_account_summary(self):
        """Unified Account Summary provides full cross-device visibility."""
        summary = self.engine.get_shared_account_summary(user_id="user_lok", date=self.today)
        self.assertEqual(summary["account"]["user_id"], "user_lok")
        self.assertIn("shared_score", summary)
        self.assertIn("privileges", summary)
        self.assertIn("registered_clients", summary)
        self.assertIn("sync_health", summary)


if __name__ == "__main__":
    unittest.main()
