"""Private, paired-PC demo authority. Never writes points or proof records."""
import argparse
import contextlib
import hashlib
import hmac
import json
import logging
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cloud_points.demo_access import SCHEMA, DemoUnavailable, state


def effective_access(database, account, actual_score):
    """Read the same persisted allowance at the PC browser-policy boundary."""
    import time
    with contextlib.closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_access_state'").fetchone():
            return actual_score, False
        row=conn.execute('SELECT last_successful_activation_ms,expires_at_ms,status FROM demo_access_state WHERE account_id=?',(account,)).fetchone()
    now=int(time.time()*1000)
    active=bool(row and row[2]=='ACTIVE' and row[1]-row[0]==30000 and row[0]<=now<row[1])
    return (81 if active else actual_score),active


class PairedDemoStore:
    sqlite = True

    def __init__(self, database, device, token):
        self.database = Path(database).resolve()
        self.device, self.token = device, token

    def _connect(self):
        # mode=rw refuses to create an empty replacement for the paired database.
        conn = sqlite3.connect(self.database.as_uri() + '?mode=rw', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def authenticate(self, conn=None):
        if not self.device or len(self.device) > 200 or not self.token or len(self.token) > 4096:
            raise PermissionError('Registered device credential required')
        if conn is None:
            with contextlib.closing(self._connect()) as connection:
                return self.authenticate(connection)
        rows = conn.execute('''SELECT c.account_id,c.token_hash FROM device_credentials c
            JOIN devices d ON d.device_id=c.device_id
            WHERE c.device_id=? AND c.revoked_at IS NULL
            AND UPPER(COALESCE(d.pairing_state,''))!='REVOKED'
            AND UPPER(COALESCE(d.status,''))!='REVOKED' ''', (self.device,)).fetchall()
        digest = hashlib.sha256(self.token.encode()).hexdigest()
        for row in rows:
            retained = str(row['token_hash'])
            legacy_plaintext = not re.fullmatch(r'[0-9a-fA-F]{64}', retained)
            if (hmac.compare_digest(retained.encode(), digest.encode()) or
                    (legacy_plaintext and hmac.compare_digest(retained.encode(), self.token.encode()))):
                return {'account_id': str(row['account_id'])}
        raise PermissionError('Device registration is revoked or invalid')

    @contextlib.contextmanager
    def transaction(self):
        with contextlib.closing(self._connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                # Check registration again inside the serialized transaction.
                self.authenticate(conn)
                conn.execute(SCHEMA)
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def lock(self, conn, account):
        if self.authenticate(conn)['account_id'] != account:
            raise PermissionError('Account binding changed')


class LocalAccountDemoStore(PairedDemoStore):
    """For an already authenticated native/extension request, never a public header."""
    def __init__(self, database, account):
        super().__init__(database, '', '')
        self.account = account

    def authenticate(self, conn=None):
        if conn is None:
            with contextlib.closing(self._connect()) as connection:
                return self.authenticate(connection)
        if not conn.execute('SELECT 1 FROM users WHERE user_id=?', (self.account,)).fetchone():
            raise PermissionError('Unknown account')
        return {'account_id':self.account}


def create_server(database, port=8766):
    database = Path(database).resolve(strict=True)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass  # Never log device credentials or request headers.

        def reply(self, status, result):
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def handle_demo_request(self, activate):
            try:
                if activate:
                    length = int(self.headers.get('Content-Length', '0'))
                    if 0 < length <= 65536:
                        self.rfile.read(length)
                    if not 0 <= length <= 1024:
                        self.reply(413, {'success': False, 'error': 'Request too large'})
                        return
                bearer = self.headers.get('Authorization', '')
                if not bearer.startswith('Bearer '):
                    raise PermissionError('Registered device credential required')
                store = PairedDemoStore(database, self.headers.get('X-Device-Id', ''), bearer[7:])
                principal = store.authenticate()
                self.reply(200, state(store, principal, activate=activate))
            except DemoUnavailable as error:
                self.reply(409, {'success': False, 'code': 'DEMO_COOLDOWN',
                    'error': 'Demo access is limited to once every seven full days.',
                    'demo_access': error.state})
            except PermissionError:
                self.reply(401, {'success': False, 'error': 'Registered device authentication required'})
            except (ValueError, TypeError, KeyError, OverflowError):
                self.reply(409, {'success': False, 'error': 'Demo state could not be verified'})
            except Exception:
                logging.exception('Paired demo authority request failed')
                self.reply(503, {'success': False, 'error': 'Paired PC demo service unavailable'})

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'status': 'OK', 'service': 'paired-demo-access'})
            elif self.path == '/v1/demo-access/state':
                self.handle_demo_request(False)
            else:
                self.reply(404, {'error': 'Unknown endpoint'})

        def do_POST(self):
            if self.path == '/v1/demo-access/activate':
                self.handle_demo_request(True)
            else:
                self.reply(404, {'error': 'Unknown endpoint'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    log = root / 'data' / 'logs' / 'paired-demo-service.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log, level=logging.WARNING)
    with create_server(args.database, args.port) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
