"""Automatic receiver fixtures use socketpair/fake radio and disposable databases only."""
import hashlib
import json
import socket
import time

import pytest

from core import bluetooth_protocol as wire
from core.bluetooth_auto_sync import AutomaticBluetoothSyncManager, CONFIG_KEY, MODE, automatic_key, automatic_receipt_text
from core.database import StrictRecordsDB
from tests.bluetooth_fakes import FakeBackend, PEER
from tests.test_bluetooth_transfer_v2 import DEVICE, package

TOKEN = 'fixture-device-token-only'
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


@pytest.fixture
def automatic(tmp_path):
    db = StrictRecordsDB(str(tmp_path / 'automatic.sqlite'))
    with db._connect() as c:
        c.execute('''INSERT INTO devices(device_id,user_id,device_name,platform,app_version,last_seen,sync_status)
                     VALUES(?,?,?,?,?,?,?)''', (DEVICE, 'user_lok', 'Fixture phone', 'ANDROID', 'fixture', '2026-10-19', 'OFFLINE'))
        c.execute('''INSERT INTO device_credentials(credential_id,device_id,account_id,token_hash,issued_at)
                     VALUES(?,?,?,?,?)''', ('fixture-credential', DEVICE, 'user_lok', TOKEN_HASH, '2026-10-19'))
    clock = [1000]
    manager = AutomaticBluetoothSyncManager(db, backend_factory=FakeBackend, clock=lambda: clock[0])
    yield db, manager, clock
    manager.stop()


def enable(m):
    assert m.configure(True, PEER, DEVICE, 'QUEUE_RECONCILIATION')['success']
    m.tick()
    assert m._active_session


def exchange(m, payload=None, mutate=None, before_payload=None):
    left, right = socket.socketpair()
    right.settimeout(3)
    m._backend.listener.connections.put((left, (PEER, 1)))
    hello = wire.read_frame(right)
    assert hello['auth_mode'] == MODE
    value = payload or package()
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    key = automatic_key(TOKEN_HASH, DEVICE, 'user_lok')
    auth = dict(protocol=wire.PROTOCOL, version=2, auth_mode=MODE, session_id=hello['session_id'],
                package_id=hashlib.sha256(raw).hexdigest(), device_id=DEVICE, account_id='user_lok',
                payload_length=len(raw), record_count=wire.package_count(value),
                credential_tag=hashlib.sha256(TOKEN_HASH.encode()).hexdigest())
    text = wire.auth_text(auth['session_id'], hello['challenge'], auth['package_id'], DEVICE, 'user_lok', len(raw), auth['record_count'])
    auth['authenticator'] = wire.mac(key, text)
    if mutate: mutate(auth)
    wire.send_frame(right, auth)
    try:
        ack = wire.read_frame(right)
        assert ack['receiver_mac'] == wire.mac(key, 'RECEIVER\n' + text)
        if before_payload: before_payload()
        right.sendall(raw)
        return wire.read_frame(right), auth
    finally:
        right.close()


def test_off_by_default_and_restored_without_secrets(automatic):
    db, m, _ = automatic
    m.tick()
    assert m._backend is None and m.get_status()['config'] == {'enabled': False}
    enable(m)
    status = m.get_status()
    assert 'session_code' not in status
    assert TOKEN not in json.dumps(status) and TOKEN_HASH not in json.dumps(status)
    restored = AutomaticBluetoothSyncManager(db, backend_factory=FakeBackend)
    assert restored.get_status()['config']['device_id'] == DEVICE
    restored.stop()


@pytest.mark.parametrize('enabled,peer,device', [('true', PEER, DEVICE), (True, 'AA:BB:CC:DD:EE:99', DEVICE), (True, PEER, 'unregistered')])
def test_explicit_registered_paired_selection_required(automatic, enabled, peer, device):
    _, m, _ = automatic
    assert not m.configure(enabled, peer, device)['success']
    assert m._backend is None


def test_automatic_commit_and_repeated_package_are_idempotent(automatic):
    db, m, _ = automatic
    enable(m)
    receipt, _ = exchange(m)
    assert receipt['success'] and receipt['confirmed_score'] == 4
    assert receipt['automatic_receipt_mac'] == wire.mac(automatic_key(TOKEN_HASH, DEVICE, 'user_lok'), automatic_receipt_text(receipt))
    # The receiver closes after one bounded transaction and advertises again.
    for _ in range(100):
        if m._active_session is None: break
        time.sleep(.005)
    m.tick()
    duplicate, _ = exchange(m)
    assert duplicate['transferred_count'] == 0 and duplicate['already_present_count'] == 1
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM score_events WHERE event_id='sleep-4'").fetchone()[0] == 1


@pytest.mark.parametrize('change', ['device_id', 'account_id', 'auth_mode', 'authenticator', 'credential_tag', 'session_id'])
def test_wrong_identity_key_mode_or_replayed_session_never_receives_data(automatic, change):
    _, m, _ = automatic
    enable(m)
    with pytest.raises((ConnectionError, OSError)):
        exchange(m, mutate=lambda auth: auth.update({change: 'incorrect'}))
    assert m._last_receipt is None


@pytest.mark.parametrize('action', ['revoke', 'disable', 'change_account'])
def test_authorization_checked_again_before_atomic_commit(automatic, action):
    db, m, _ = automatic
    enable(m)
    def change():
        with db._connect() as conn:
            if action == 'revoke': conn.execute("UPDATE device_credentials SET revoked_at='now' WHERE device_id=?", (DEVICE,))
            elif action == 'disable': conn.execute('UPDATE system_config SET value=? WHERE key=?', (json.dumps({'enabled': False}), CONFIG_KEY))
            else: conn.execute("UPDATE device_credentials SET account_id='other' WHERE device_id=?", (DEVICE,))
    result, _ = exchange(m, before_payload=change)
    assert not result['success']
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM score_events WHERE event_id='sleep-4'").fetchone()[0] == 0


def test_adapter_failure_retries_with_bounded_backoff(automatic):
    _, m, clock = automatic
    attempts = []
    class Off(FakeBackend):
        @staticmethod
        def capability():
            attempts.append(1)
            return False, 'Fixture radio is off'
    m.backend_factory = Off
    assert m.configure(True, PEER, DEVICE)['success']
    for delay in [15, 30, 60, 120, 240, 300, 300]:
        m.tick()
        count = len(attempts)
        assert m._next_attempt == clock[0] + delay
        m.tick()
        assert len(attempts) == count
        clock[0] += delay


def test_disabling_closes_automatic_listener_but_keeps_records(automatic):
    db, m, _ = automatic
    enable(m)
    backend = m._backend
    assert m.configure(False)['success']
    assert backend.closed and m._active_session is None
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM device_credentials WHERE device_id=?', (DEVICE,)).fetchone()[0] == 1


def test_new_pending_claim_is_retained_without_awarding_it(automatic):
    _, m, _ = automatic
    enable(m)
    payload = package()
    payload['proof_reviews'] = [dict(evidence_id='unsupported-claim', date=payload['date'], task_code='WAKE_UP',
        status='APPROVED', points_awarded=15, submitted_at=payload['date']+'T08:00:00+05:30', reviewed_by='fixture')]
    receipt, _ = exchange(m, payload)
    assert receipt['success'] and receipt['confirmed_score'] == 4 and receipt['pending_metadata_count'] == 1
    signature = receipt['automatic_receipt_mac']
    receipt['pending_metadata_count'] = 0
    assert signature != wire.mac(automatic_key(TOKEN_HASH, DEVICE, 'user_lok'), automatic_receipt_text(receipt))
