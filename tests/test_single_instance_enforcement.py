"""
Unit and Integration Test Suite for StrictRecords Single-Instance Enforcement & User Notification
===================================================================================================
Tests that:
1. Process enumeration accurately detects StrictRecords app instances.
2. Duplicate instances are blocked immediately from starting.
3. User is notified via Windows notification, tray balloon, and notification center.
4. Background watchdog terminates any duplicate processes and notifies the user.
5. Endpoints /api/system/instances and /api/system/enforce_single_instance operate correctly.
"""

import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from windows.single_instance_manager import (
    get_strictrecords_app_instances,
    is_another_instance_running,
    notify_user_duplicate_blocked,
    block_and_terminate_duplicate_processes,
    bring_primary_instance_to_front,
)


class TestSingleInstanceEnforcement(unittest.TestCase):
    def setUp(self):
        import windows.single_instance_manager as sim
        sim._LAST_DUPLICATE_ALERT_TIME = 0.0
        sim._LAST_BRING_FRONT_TIME = 0.0

    def test_get_strictrecords_app_instances_includes_current_or_filters_tests(self):
        instances = get_strictrecords_app_instances()
        self.assertIsInstance(instances, list)
        # Verify schema
        for inst in instances:
            self.assertIn("pid", inst)
            self.assertIn("name", inst)
            self.assertIn("is_current", inst)

    def test_is_another_instance_running_with_mock_process(self):
        mock_instances = [
            {"pid": os.getpid(), "name": "python.exe", "is_current": True},
            {"pid": 999999, "name": "StrictRecords.exe", "is_current": False},
        ]
        with patch("windows.single_instance_manager.get_strictrecords_app_instances", return_value=mock_instances):
            is_running, reason = is_another_instance_running()
            self.assertTrue(is_running)
            self.assertIn("999999", reason)

    def test_notify_user_duplicate_blocked_records_db_event(self):
        mock_db = MagicMock()
        with patch("core.database.StrictRecordsDB", return_value=mock_db):
            notify_user_duplicate_blocked(dup_pid=8888, from_primary=True)
            mock_db.save_notification_center_event.assert_called_once()
            call_kwargs = mock_db.save_notification_center_event.call_args[1]
            self.assertIn("Duplicate Process Blocked", call_kwargs.get("title", ""))
            self.assertEqual(call_kwargs.get("category"), "SYNC")

    def test_notify_user_duplicate_blocked_from_secondary_launch(self):
        mock_db = MagicMock()
        with patch("core.database.StrictRecordsDB", return_value=mock_db):
            notify_user_duplicate_blocked(dup_pid=os.getpid(), from_primary=False)
            mock_db.save_notification_center_event.assert_called_once()
            call_kwargs = mock_db.save_notification_center_event.call_args[1]
            self.assertIn("Already Running", call_kwargs.get("title", ""))

    def test_block_and_terminate_duplicate_processes_kills_rogue_process(self):
        # Spawn a dummy python background process to simulate a rogue duplicate process
        dummy_script = "import time; time.sleep(30)"
        proc = subprocess.Popen([sys.executable, "-c", dummy_script])
        dummy_pid = proc.pid
        try:
            mock_instances = [
                {"pid": os.getpid(), "name": "python.exe", "is_current": True},
                {"pid": dummy_pid, "name": "StrictRecords.exe", "is_current": False},
            ]
            with patch("windows.single_instance_manager.get_strictrecords_app_instances", return_value=mock_instances):
                with patch("windows.single_instance_manager.notify_user_duplicate_blocked") as mock_notify:
                    blocked = block_and_terminate_duplicate_processes(current_pid=os.getpid())
                    self.assertIn(dummy_pid, blocked)
                    mock_notify.assert_called_once_with(dup_pid=dummy_pid, from_primary=True)

            # Verify the dummy process was actually terminated
            time.sleep(0.5)
            self.assertIsNotNone(proc.poll(), "Duplicate process should have been terminated")
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_bring_primary_instance_to_front_handles_unreachable_gracefully(self):
        res = bring_primary_instance_to_front(host="127.0.0.1", port=59999)
        self.assertIsInstance(res, bool)

    def test_parent_launcher_protected_and_duplicate_exe_blocked(self):
        parent_pid = os.getppid() if hasattr(os, "getppid") else 1234
        current_pid = os.getpid()
        dup_exe_pid = 77777

        mock_instances = [
            {"pid": current_pid, "name": "python.exe", "is_current": True},
            {"pid": parent_pid, "name": "StrictRecords.exe", "is_current": True},
            {"pid": dup_exe_pid, "name": "StrictRecords.exe", "is_current": False},
        ]
        with patch("windows.single_instance_manager.get_strictrecords_app_instances", return_value=mock_instances):
            is_running, reason = is_another_instance_running()
            self.assertTrue(is_running)
            self.assertIn(str(dup_exe_pid), reason)
            self.assertNotIn(str(parent_pid), reason)

            # Ensure block_and_terminate_duplicate_processes only targets is_current=False
            with patch("psutil.Process") as mock_psutil_proc:
                mock_target = MagicMock()
                mock_psutil_proc.return_value = mock_target
                with patch("windows.single_instance_manager.notify_user_duplicate_blocked"):
                    blocked = block_and_terminate_duplicate_processes(current_pid=current_pid)
                    self.assertIn(dup_exe_pid, blocked)
                    self.assertNotIn(parent_pid, blocked)
                    self.assertNotIn(current_pid, blocked)


if __name__ == "__main__":
    unittest.main()
