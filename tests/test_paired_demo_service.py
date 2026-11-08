"""Exercise the private PC authority using temporary identities and databases."""
import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from cloud_points.demo_access import COOLDOWN_MS
from core.paired_demo_service import create_server

NOW = 1_793_446_400_000


@pytest.fixture
def authority(tmp_path):
    path = tmp_path / 'paired.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE devices(device_id TEXT PRIMARY KEY,pairing_state TEXT,status TEXT);
            CREATE TABLE device_credentials(device_id TEXT,account_id TEXT,token_hash TEXT,revoked_at TEXT);
            CREATE TABLE score_events(event_id TEXT,points INTEGER);
            INSERT INTO score_events VALUES('real-proof',7);
        ''')
        for device in ['phone', 'second']:
            conn.execute('INSERT INTO devices VALUES(?,?,?)', (device, 'REGISTERED', 'ONLINE'))
            conn.execute('INSERT INTO device_credentials VALUES(?,?,?,NULL)',
                         (device, 'account', hashlib.sha256(('token-' + device).encode()).hexdigest()))
    server = create_server(path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def request(authority, activate=False, device='phone', token=None, body=None):
    _, server = authority
    url = 'http://127.0.0.1:%s/v1/demo-access/%s' % (
        server.server_port, 'activate' if activate else 'state')
    req = urllib.request.Request(url, json.dumps(body or {}).encode() if activate else None,
        headers={'Authorization': 'Bearer ' + (token or 'token-' + device), 'X-Device-Id': device})
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def retained_records(path):
    with sqlite3.connect(path) as conn:
        return {name: conn.execute('SELECT * FROM ' + name).fetchall()
                for name in ['score_events', 'device_credentials', 'devices']}


def test_authentication_and_revocation_fail_before_cooldown_table_write(authority):
    path, _ = authority
    before = retained_records(path)
    assert request(authority, True, token='wrong')[0] == 401
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='demo_access_state'").fetchone()
        conn.execute("UPDATE device_credentials SET revoked_at='revoked'")
    assert request(authority, True)[0] == 401
    assert retained_records(path)['score_events'] == before['score_events']


def test_real_records_unchanged_and_receipt_uses_authenticated_account(authority):
    path, server = authority
    before = retained_records(path)
    with patch('cloud_points.demo_access._now', return_value=NOW):
        assert request(authority)[1]['demo_access']['available']
        code, reply = request(authority, True, body={'account_id': 'other', 'score': 999, 'timestamp': NOW * 5})
    assert server.server_address[0] == '127.0.0.1'
    assert code == 200
    result = reply['demo_access']
    assert result['account_id'] == 'account'
    assert result['effective_access_points'] == 81 and result['remaining_ms'] == 30000
    assert retained_records(path) == before


def test_second_device_and_restarted_service_share_cooldown(authority):
    path, _ = authority
    with patch('cloud_points.demo_access._now', return_value=NOW):
        assert request(authority, True)[0] == 200
    with patch('cloud_points.demo_access._now', return_value=NOW + 30000):
        code, reply = request(authority, True, device='second')
    assert code == 409 and reply['demo_access']['status'] == 'EXPIRED'
    assert reply['demo_access']['effective_access_points'] is None
    restarted = create_server(path, 0)
    t = threading.Thread(target=restarted.serve_forever, daemon=True); t.start()
    try:
        with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS - 1):
            assert request((path, restarted), True)[0] == 409
        with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS):
            assert request((path, restarted), True)[0] == 200
    finally:
        restarted.shutdown(); restarted.server_close(); t.join(2)


def test_simultaneous_activation_has_only_one_winner(authority):
    with patch('cloud_points.demo_access._now', return_value=NOW), ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: request(authority, True)[0], range(6)))
    assert results.count(200) == 1 and results.count(409) == 5


def test_corrupt_cooldown_does_not_grant_access(authority):
    path, _ = authority
    with patch('cloud_points.demo_access._now', return_value=NOW):
        assert request(authority, True)[0] == 200
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE demo_access_state SET expires_at_ms=1')
    with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS):
        code, reply = request(authority, True)
    assert code == 409 and not reply['success'] and 'demo_access' not in reply


def test_oversized_body_does_not_consume_activation(authority):
    assert request(authority, True, body={'text': 'x' * 1100})[0] == 413
    assert request(authority)[1]['demo_access']['available']
