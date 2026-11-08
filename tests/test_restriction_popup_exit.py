import threading
import unittest
from unittest.mock import Mock, patch
from windows.restriction_popup import RestrictionPopupController
from windows.restriction_manager import YouTubeRestrictionManager


class PopupExitTests(unittest.TestCase):
    def test_home_action_is_off_ui_thread_and_deduplicated(self):
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def action(session,data):
            started.set();release.wait(2);return False
        controller=RestrictionPopupController(action)
        callback=Mock(side_effect=lambda result: finished.set())
        self.assertTrue(controller.request_home(1,{},callback))
        self.assertTrue(started.wait(1))
        self.assertFalse(controller.request_home(1,{},callback))
        release.set();self.assertTrue(finished.wait(1))
        callback.assert_called_once_with(False)
    def test_stale_popup_can_exit_without_touching_any_browser(self):
        manager=YouTubeRestrictionManager()
        with patch.object(manager._native_browser,'close') as close:
            self.assertTrue(manager._native_home(-1,{}))
            close.assert_not_called()
    def test_failed_exact_tab_close_still_requests_own_home_and_never_closes_browser(self):
        manager=YouTubeRestrictionManager()
        token=manager._entry_session.observe(('instagram',1,10,'tab'))
        manager._active_native_target=dict(action='CLOSE_NATIVE_TAB',app_key='instagram',hwnd=1,pid=10,tab_id='tab',origin='https://instagram.com')
        manager.get_browser_tab_restriction=lambda *args,**kwargs: {'restricted':True}
        manager._native_browser.close=lambda target: False
        manager.desktop_api=Mock()
        with patch.object(manager,'close_restricted_window') as close:
            self.assertFalse(manager._native_home(token,{}))
            manager.desktop_api.show_dashboard.assert_called_once_with(tab='cockpit')
            close.assert_not_called()
            self.assertFalse(manager.lock_screen_visible)
    def test_shutdown_stops_popup_and_native_workers(self):
        manager=YouTubeRestrictionManager()
        manager._popup_controller=Mock();manager._native_browser=Mock()
        manager.stop_watcher()
        manager._popup_controller.stop.assert_called_once()
        manager._native_browser.stop.assert_called_once()

    def test_native_cleanup_has_a_deadline(self):
        from desktop_app import bounded_cleanup
        release=threading.Event()
        self.assertFalse(bounded_cleanup(lambda: release.wait(1),timeout=.01))
        release.set()
        self.assertTrue(bounded_cleanup(lambda: None,timeout=1))
