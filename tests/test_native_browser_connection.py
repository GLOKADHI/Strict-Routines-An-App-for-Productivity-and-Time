from types import SimpleNamespace
from unittest.mock import patch
import time

from windows.native_browser_bridge import NativeBrowserBridge
from windows.restriction_manager import YouTubeRestrictionManager


def test_native_connect_starts_one_worker_and_reports_no_extension_requirement():
    bridge = NativeBrowserBridge()
    def worker():
        bridge.error = ""
        bridge._ready.set()
        time.sleep(0.4)
    bridge._worker = worker
    first = bridge.connect()
    thread = bridge._thread
    second = bridge.connect()
    assert first['connected'] and second['connected']
    assert not second['extension_required']
    assert bridge._thread is thread


def test_native_reader_drops_non_target_origins():
    bridge = NativeBrowserBridge()
    bridge._call = lambda *args: dict(supported=True, origin="https://payments.example", can_close=False)
    snapshot = bridge.inspect(1, "comet.exe")
    assert "origin" not in snapshot and "target" not in snapshot
    assert bridge.status()['browsers'][0]['url_detection'] is True


def test_native_reader_requires_exact_target_hostname():
    bridge = NativeBrowserBridge()
    for url, target in [("https://masstamilan.dev", "masstamilan"), ("https://masstamilan.dev.evil.example", None)]:
        bridge._call = lambda *args: dict(supported=True, origin=url, can_close=False)
        assert bridge.inspect(1, "comet.exe").get("target") == target


def test_hung_provider_is_not_reported_connected():
    bridge = NativeBrowserBridge()
    bridge.error = ""
    bridge._ready.set()
    bridge._thread = SimpleNamespace(is_alive=lambda: True)
    bridge._busy = True
    bridge._busy_since = time.monotonic() - 10
    assert not bridge.status()['connected']


def test_timeout_does_not_schedule_another_worker_or_duplicate_action():
    bridge = NativeBrowserBridge()
    bridge.error = ""
    bridge._ready.set()
    bridge._thread = SimpleNamespace(is_alive=lambda: True)
    result = bridge._call("inspect", (1,), 0.01)
    assert not result['supported']
    assert bridge._queue.qsize() == 1
    second = bridge._call("inspect", (1,), 0.01)
    assert not second['supported'] and bridge._queue.qsize() == 1


class Facts:
    def get_settings(self): return SimpleNamespace(reward_threshold=80, controlled_mode_threshold=60)


def test_html_connection_does_not_mark_an_extension_connected_or_change_scores():
    manager = YouTubeRestrictionManager(db=Facts())
    manager._native_browser.connect = lambda: dict(connected=True, extension_required=False)
    result = manager.desktop_connection_status(connect=True)
    assert result['connected'] and result['extension_required'] is False
    assert not manager.browser_extension_is_connected()
    assert manager.current_score == 0


def test_native_home_refuses_unsupported_tab_closure_without_closing_window():
    manager = YouTubeRestrictionManager(db=Facts())
    token = manager._entry_session.observe(("masstamilan", 1, 10, "tab-1"))
    manager._active_native_target = dict(action="CLOSE_NATIVE_TAB", app_key="masstamilan", hwnd=1, pid=10,
                                         tab_id="tab-1", origin="https://masstamilan.dev")
    manager.get_browser_tab_restriction = lambda *args, **kwargs: dict(restricted=True)
    manager._native_browser.close = lambda target: False
    with patch.object(manager, 'close_restricted_window') as close:
        assert not manager._native_home(token, {})
        close.assert_not_called()


def test_native_home_rechecks_authoritative_eligibility_on_click():
    manager = YouTubeRestrictionManager(db=Facts())
    token = manager._entry_session.observe(("masstamilan", 1, 10, "tab-1"))
    manager._active_native_target = dict(action="CLOSE_NATIVE_TAB", app_key="masstamilan", hwnd=1, pid=10,
                                         tab_id="tab-1", origin="https://masstamilan.dev")
    manager.get_browser_tab_restriction = lambda *args, **kwargs: dict(restricted=False)
    with patch.object(manager._native_browser, 'close') as close:
        assert manager._native_home(token, {})
        close.assert_not_called()
