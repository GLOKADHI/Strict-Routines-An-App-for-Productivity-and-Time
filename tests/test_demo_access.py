"""Transactional isolation/cooldown tests. No real account activation is consumed."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import json
import sqlite3
import time
from unittest.mock import patch

import pytest

from cloud_points.demo_access import state, DemoUnavailable, DURATION_MS, COOLDOWN_MS
from cloud_points.service import Application
from cloud_points.store import Store
from tests.test_cloud_points import envelope

NOW = 1_793_446_400_000


@pytest.fixture
def account(tmp_path):
    store = Store(str(tmp_path / 'cloud.db'), test_sqlite=True)
    store.initialize()
    for device, role in [('android', 'AUTHORITY'), ('windows', 'REPLICA'), ('second', 'REPLICA')]:
        store.provision('user_lok', device, hashlib.sha256(('token-' + device).encode()).hexdigest(),
                        role, int(time.time()) + 3600, authority_device='android')
    store.publish(store.authenticate('android', 'token-android'), envelope())
    return store


def principal(store, device='android'):
    return store.authenticate(device, 'token-' + device)


def points_bytes(store):
    with store.transaction() as tx:
        tables = [r['name'] for r in tx.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'points_%' ORDER BY name").fetchall()]
        return json.dumps({name: [dict(r) for r in tx.execute('SELECT * FROM ' + name).fetchall()]
                           for name in tables}, sort_keys=True).encode()


def test_override_does_not_write_any_points_history_summary_review_or_sync_record(account):
    before = points_bytes(account)
    with patch('cloud_points.demo_access._now', return_value=NOW):
        result = state(account, principal(account), activate=True)['demo_access']
    assert result['effective_access_points'] == 81
    assert result['remaining_ms'] == 30_000
    assert points_bytes(account) == before
    with account.transaction() as tx:
        columns = {r['name'] for r in tx.execute('PRAGMA table_info(demo_access_state)').fetchall()}
        assert columns == {'account_id', 'last_successful_activation_ms', 'expires_at_ms', 'status'}
        assert tx.execute('SELECT COUNT(*) AS n FROM demo_access_state').fetchone()['n'] == 1


def test_thirty_second_boundary_and_latest_real_score_are_independent(account):
    with patch('cloud_points.demo_access._now', return_value=NOW):
        state(account, principal(account), activate=True)
    with patch('cloud_points.demo_access._now', return_value=NOW + 29_999):
        assert state(account, principal(account))['demo_access']['effective_access_points'] == 81
    # A genuine authority checkpoint may change the real balance during the demo.
    account.publish(principal(account), envelope(events=[dict(event_id='sleep-1', event_type='MI_FITNESS_SLEEP_BONUS', points=4, eligible=True, audit={'evidence_id':'proof-ref'}),
        dict(event_id='new-sleep', event_type='MI_FITNESS_SLEEP_BONUS', points=3, eligible=True, audit={'evidence_id':'new-proof'})], revision=2))
    latest = points_bytes(account)
    with patch('cloud_points.demo_access._now', return_value=NOW + 30_000):
        result = state(account, principal(account))['demo_access']
    assert result['status'] == 'EXPIRED' and result['effective_access_points'] is None
    assert account.state(principal(account), '2026-10-30')['confirmed_balance'] == 7
    assert points_bytes(account) == latest


def test_cooldown_survives_restart_and_all_devices_share_one_activation(account):
    with patch('cloud_points.demo_access._now', return_value=NOW):
        state(account, principal(account), activate=True)
    restarted = Store(account.url, test_sqlite=True)
    restarted.initialize()
    for device in ['android', 'windows', 'second']:
        with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS - 1):
            with pytest.raises(DemoUnavailable): state(restarted, principal(restarted, device), activate=True)
    with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS):
        assert state(restarted, principal(restarted, 'windows'), activate=True)['demo_access']['effective_access_points'] == 81


def test_simultaneous_taps_and_devices_cannot_duplicate_weekly_activation(account):
    def attempt(device):
        try: return state(account, principal(account, device), activate=True)['success']
        except DemoUnavailable: return False
    with patch('cloud_points.demo_access._now', return_value=NOW), ThreadPoolExecutor(8) as workers:
        results = list(workers.map(attempt, ['android', 'windows', 'second', 'android'] * 4))
    assert sum(results) == 1


def test_untrusted_client_clock_or_account_cannot_change_cooldown(account):
    app = Application(account)
    def request(body):
        raw = json.dumps(body).encode(); status = []
        output = app(dict(PATH_INFO='/v1/demo-access/activate', REQUEST_METHOD='POST',
            **{'wsgi.url_scheme':'https', 'wsgi.input':io.BytesIO(raw)}, CONTENT_LENGTH=str(len(raw)),
            HTTP_AUTHORIZATION='Bearer token-android', HTTP_X_DEVICE_ID='android'), lambda s, _: status.append(s))
        return int(status[0].split()[0]), json.loads(b''.join(output))
    with patch('cloud_points.demo_access._now', return_value=NOW):
        code, reply = request({'account_id':'someone-else', 'timestamp':NOW + COOLDOWN_MS * 9, 'score':81})
    assert code == 200 and reply['demo_access']['account_id'] == 'user_lok'
    with patch('cloud_points.demo_access._now', return_value=NOW + 1):
        assert request({'timestamp':NOW + COOLDOWN_MS * 9})[0] == 409


def test_persistence_failure_has_no_grant_and_consumes_no_activation(account):
    original = account.transaction
    @contextmanager
    def broken():
        with original() as tx:
            class Guard:
                def execute(self, sql, args=()):
                    if 'INSERT INTO demo_access_state' in sql: raise sqlite3.OperationalError('fixture disk failure')
                    return tx.execute(sql, args)
            yield Guard()
    before = points_bytes(account)
    with patch.object(account, 'transaction', broken), patch('cloud_points.demo_access._now', return_value=NOW):
        with pytest.raises(sqlite3.OperationalError): state(account, principal(account), activate=True)
    assert points_bytes(account) == before
    with patch('cloud_points.demo_access._now', return_value=NOW):
        assert state(account, principal(account))['demo_access']['available']


def test_real_state_and_cloud_envelope_never_receive_simulated_balance(account):
    before = account.state(principal(account), '2026-10-30')
    with patch('cloud_points.demo_access._now', return_value=NOW):
        state(account, principal(account), activate=True)
    after = account.state(principal(account), '2026-10-30')
    for key in ('envelope','confirmed_balance','raw_verified_points','ledger_revision','pending_changes','pending_proposals'):
        assert after[key] == before[key]
    assert 'demo_access' not in after


def test_reenrollment_after_local_clear_cannot_reset_account_cooldown(account):
    with patch('cloud_points.demo_access._now', return_value=NOW):
        state(account, principal(account), activate=True)
    account.provision('user_lok','reinstalled',hashlib.sha256(b'new-token').hexdigest(),
        'REPLICA',int(time.time())+3600,authority_device='android')
    with patch('cloud_points.demo_access._now', return_value=NOW+100000):
        with pytest.raises(DemoUnavailable):
            state(account,account.authenticate('reinstalled','new-token'),activate=True)


def test_demo_endpoint_requires_authentication_before_any_state_write(account):
    status=[]
    result=Application(account)(dict(PATH_INFO='/v1/demo-access/activate',REQUEST_METHOD='POST',
        **{'wsgi.url_scheme':'https','wsgi.input':io.BytesIO(b'{}')},CONTENT_LENGTH='2',
        HTTP_AUTHORIZATION='Bearer wrong-token',HTTP_X_DEVICE_ID='android'),lambda value,_:status.append(value))
    assert status[0].startswith('401')
    assert json.loads(b''.join(result))['code']=='AUTHENTICATION_REQUIRED'
    with account.transaction() as tx:
        assert tx.execute('SELECT COUNT(*) AS n FROM demo_access_state').fetchone()['n']==0


@pytest.mark.parametrize('assignment', ["expires_at_ms=1", "last_successful_activation_ms=-1", "status='UNKNOWN'"])
def test_uncertain_stored_cooldown_fails_closed(account, assignment):
    with patch('cloud_points.demo_access._now', return_value=NOW):
        state(account, principal(account), activate=True)
    with account.transaction() as tx:
        tx.execute("UPDATE demo_access_state SET " + assignment)
    before = points_bytes(account)
    with patch('cloud_points.demo_access._now', return_value=NOW + COOLDOWN_MS * 2):
        with pytest.raises(ValueError): state(account, principal(account), activate=True)
    assert points_bytes(account) == before
