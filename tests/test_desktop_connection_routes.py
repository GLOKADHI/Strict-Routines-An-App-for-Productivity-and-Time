import io
import json
from types import SimpleNamespace

from core.server import StrictRecordsRequestHandler


def request(path, *, client="127.0.0.1", origin="", manager=True):
    handler = object.__new__(StrictRecordsRequestHandler)
    handler.path = path
    handler.client_address = (client, 1234)
    handler.headers = {"Origin": origin}
    handler.wfile = io.BytesIO()
    codes, headers, connects = [], {}, []
    handler.send_response = codes.append
    handler.send_header = lambda name, value: headers.update({name: value})
    handler.end_headers = lambda: None
    def connection(connect=False):
        connects.append(connect)
        return dict(success=True, connected=True, extension_required=False, monitor_running=False,
                    native_browser=dict(connected=False, error="Not available"))
    handler.restriction_mgr = SimpleNamespace(desktop_connection_status=connection) if manager else None
    handler.do_GET()
    return codes[-1], handler.wfile.getvalue(), headers, connects


def test_local_html_and_script_are_served_without_extension_permissions():
    code, body, headers, _ = request('/connect')
    assert code == 200 and b'CONNECT TO STRICTROUTINES' in body
    assert b'browser_extension/popup.js' in body
    assert headers['Cache-Control'] == 'no-store'
    code, script, headers, _ = request('/browser_extension/popup.js')
    assert code == 200 and b'/api/restriction/desktop_connection' in script
    assert b'chrome.storage' not in script
    assert b'browser_extension/heartbeat' not in script


def test_local_file_connects_to_the_desktop_service_once():
    code, body, _, connects = request('/api/restriction/desktop_connection?connect=1', origin='null')
    result = json.loads(body)
    assert code == 200 and result['connected'] and not result['extension_required']
    assert connects == [True]
    assert not result['native_browser']['connected']


def test_read_only_status_does_not_start_another_connection():
    code, _, _, connects = request('/api/restriction/desktop_connection')
    assert code == 200 and connects == [False]


def test_unrelated_web_origins_and_remote_clients_are_refused():
    for client, origin in [('127.0.0.1', 'https://example.com'), ('192.168.1.1', '')]:
        code, _, _, connects = request('/api/restriction/desktop_connection?connect=1', client=client, origin=origin)
        assert code == 403 and connects == []
    assert request('/connect', client='192.168.1.1')[0] == 403


def test_missing_service_is_reported_unavailable():
    code, body, _, connects = request('/api/restriction/desktop_connection', manager=False)
    assert code == 503 and not json.loads(body)['connected'] and connects == []
