"""
Tests for StrictRecords Desktop Application lifecycle,
single-instance enforcement, and window activation logic.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from desktop_app import _bring_existing_window_to_front, is_server_running


class TestDesktopAppLifecycle(unittest.TestCase):
    def test_bring_existing_window_returns_false_when_no_window(self):
        # When server is not running or no window exists, should return False
        result = _bring_existing_window_to_front(host="127.0.0.1", port=59999)
        self.assertFalse(result)

    def test_server_running_probe(self):
        # Port 59999 is definitely not running
        self.assertFalse(is_server_running("127.0.0.1", 59999))


if __name__ == "__main__":
    unittest.main()
