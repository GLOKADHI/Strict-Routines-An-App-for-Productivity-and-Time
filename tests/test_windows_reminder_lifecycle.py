import threading
import unittest
from unittest.mock import patch
from windows.notification_service import StrictNotificationService


class WindowsReminderLifecycleTests(unittest.TestCase):
    def test_stop_joins_worker_and_restart_never_resurrects_old_loop(self):
        service=StrictNotificationService(poll_interval_sec=0.01)
        entered=threading.Event()
        def slow_poll():
            entered.set()
            threading.Event().wait(.15)
        with patch.object(service,'_poll_strictrecords_notifications',side_effect=slow_poll):
            service.start();self.assertTrue(entered.wait(2))
            old=service._thread;service.start();self.assertIs(service._thread,old)
            service.stop();self.assertFalse(old.is_alive())
            service.start();self.assertIsNot(service._thread,old)
            service.stop();self.assertFalse(service._thread.is_alive())
