"""Isolated native check of the production Google settings card and API.

Uses disposable app data, starts no enforcement or cloud worker, and never opens
the production database. The normal desktop application uses these same modules.
"""
from pathlib import Path
import json
import logging
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cloud_sync_manager import CloudSyncManager
from core.database import StrictRecordsDB
from core.google_auth import GoogleAuthConfig, GoogleAuthManager
from core.server import StrictRecordsRequestHandler
from core.ui_dashboard import DASHBOARD_HTML


def check_html():
    """Reuse the real component and its real JavaScript, without other features."""
    source = DASHBOARD_HTML
    css = source.split('<style>', 1)[1].split('</style>', 1)[0]
    card = source.split('<!-- GOOGLE_DRIVE_SYNC_START -->', 1)[1].split('<!-- GOOGLE_DRIVE_SYNC_END -->', 1)[0]
    script = source.split('// Google Drive Sync UI Logic & Multi-Stage Connection Feedback', 1)[1].split('</script>', 1)[0]
    helpers = """
    async function apiGet(path) { return (await fetch(path)).json(); }
    async function apiPost(path, body) { return (await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json(); }
    function showToast(message) { document.getElementById('checkMessage').textContent=message; }
    """
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<title>StrictRoutines — Google Connection Check</title><style>' + css +
        'body{display:block!important;overflow:auto!important;padding:24px;font-family:Segoe UI,sans-serif}'
        'main{max-width:1040px;margin:auto}button:disabled{opacity:.45;cursor:not-allowed}'
        '</style></head><body><main><h1>Google connection check</h1>'
        '<p>Isolated desktop check · disposable data · cloud worker off · production app data untouched</p>'
        '<p id="checkMessage" role="status"></p>' + card +
        '</main><script>' + helpers + script + '</script></body></html>')


def create_check_server(data_dir, auth_dir=None):
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    db = StrictRecordsDB(str(data_dir / 'isolated-check.db'))
    db.register_or_heartbeat_device('windows_google_check', 'Isolated Google check', 'WINDOWS')
    credential_dir = Path(auth_dir).resolve() if auth_dir else data_dir
    auth = GoogleAuthManager(GoogleAuthConfig(str(credential_dir / 'google_client_config.json')),
                             str(credential_dir / 'google_session.enc'))
    cloud = CloudSyncManager(db, auth)
    html = check_html()
    if auth_dir:
        html = html.replace('disposable data · cloud worker off · production app data untouched',
            'production Google session · isolated test database · cloud uploads disabled')
    page = html.encode('utf-8')

    class CheckHandler(StrictRecordsRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                self.close_connection = True
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(page)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(page)
                self.wfile.flush()
            elif path in {'/api/google/auth/config', '/api/google/auth/status',
                          '/api/google/connection/status', '/api/google/sync/status',
                          '/api/google/drive/folder'}:
                self._handle_google_desktop_request(path)
            else:
                self._send_json({'error': 'Not part of the isolated Google check'}, status=404)

        def do_POST(self):
            if auth_dir and urlsplit(self.path).path in {'/api/google/sync/enable', '/api/google/sync/trigger', '/api/google/sync/retry'}:
                self._send_json({'success': False, 'error': 'Cloud uploads are disabled in this connection check. Enable sync in the normal application after reviewing its data.'}, status=409)
                return
            # Reuse the production bounded JSON parser and Google routes only.
            if urlsplit(self.path).path.startswith('/api/google/'):
                super().do_POST()
            else:
                self._send_json({'error': 'Not part of the isolated Google check'}, status=404)

    CheckHandler.db = db
    CheckHandler.google_auth = auth
    CheckHandler.cloud_sync_mgr = cloud
    server = ThreadingHTTPServer(('127.0.0.1', 0), CheckHandler)
    server.daemon_threads = True
    return server


def main(data_dir=None, auth_dir=None):
    import webview
    data_dir = Path(data_dir) if data_dir else ROOT / '.codex-work/google-desktop-20261029/native-check'
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(data_dir / 'native-check.log'), level=logging.INFO)
    server = create_check_server(data_dir, auth_dir=auth_dir)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}/'
    (data_dir / 'running.json').write_text(json.dumps({'url': url, 'isolated': True}), encoding='utf-8')
    try:
        window = webview.create_window('StrictRoutines — Google Connection Check', url,
                                        width=1080, height=850, min_size=(720, 560))
        def record_event(event):
            with (data_dir / 'lifecycle.log').open('a', encoding='utf-8') as log:
                log.write(event + '\n')
        window.events.shown += lambda: record_event('Native window shown')
        window.events.loaded += lambda: record_event('WebView page loaded')
        window.events.closed += lambda: record_event('Native window closed')
        record_event('Starting native WebView')
        webview.start(gui='edgechromium', private_mode=True)
        record_event('Native WebView loop returned')
    except Exception:
        import traceback
        (data_dir / 'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == '__main__':
    main()
