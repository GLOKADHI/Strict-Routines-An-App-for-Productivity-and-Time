"""Security tests use only disposable SQLite state; no cameras or live accounts."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.strict_ios_camera import StrictIOSCamera, trusted_origin


class DB:
    def __init__(self, path):
        self.path = str(path)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def rig(tmp_path):
    db = DB(tmp_path / 'camera.sqlite')
    now = [1000.]
    live = [True]
    with db._connect() as c:
        c.executescript('''
        CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY,account_id TEXT,status TEXT,expires_at REAL);
        CREATE TABLE strict_join_participants(preparation_id TEXT,role TEXT,credential_hash TEXT,seen REAL,report_json TEXT);
        CREATE TABLE score_events(id TEXT,points INTEGER);
        INSERT INTO score_events VALUES('real_score',8);
        INSERT INTO strict_preparations VALUES('prep_test','account','ACTIVE',5000);
        ''')
        c.execute("INSERT INTO strict_join_participants VALUES('prep_test','IOS',?,1000,?)", (digest('c' * 43), json.dumps({'timer_ready': True})))
    validator = lambda account, pid, sid: live[0] and (account, pid, sid) == ('account', 'prep_test', 'session_real')
    store = StrictIOSCamera(db, clock=lambda: now[0], session_validator=validator)
    store.configure('https://camera.example.com', enabled=True, tls_verified=True)
    return db, store, now, live, validator


def invite(store):
    result = store.invite('account', 'prep_test', 'session_real')
    return result, result['invitation']['url'].split('#')[1]


def paired(store):
    _, token = invite(store)
    binding = store.redeem(token, 'prep_test', 'c' * 43)
    store.client_state(binding['credential'], 'IOS_VIDEO_CONNECTING')
    return binding


@pytest.mark.parametrize('origin', [
    'http://camera.example.com', 'https://192.168.29.9', 'https://127.0.0.1',
    'https://[::1]', 'https://localhost', 'https://foo.local',
    'https://user:secret@camera.example.com', 'https://camera.example.com/path',
    'https://camera.example.com?token=x', 'https://camera.example.com#x',
    'https://camera.example.com:99999', 'https://-bad.example.com', 'https://bad..com',
])
def test_camera_origin_rejects_insecure_or_ambiguous_addresses(origin):
    with pytest.raises(ValueError):
        trusted_origin(origin)


def test_origin_normalization_and_no_enable_without_actual_tls_probe(rig):
    _, store, _, _, _ = rig
    assert trusted_origin('https://CAMERA.Example.com:443/') == 'https://camera.example.com'
    store.configure('https://camera.example.com', enabled=True)
    with pytest.raises(PermissionError, match='disabled'):
        invite(store)
    store.configure('https://camera.example.com', enabled=False, tls_verified=True)
    with pytest.raises(PermissionError):
        invite(store)


def test_invitation_hash_only_scope_and_default_three_minute_expiry(rig):
    db, store, now, _, _ = rig
    result, token = invite(store)
    assert result['invitation']['expires_at'] == now[0] + 180
    assert result['invitation']['url'].startswith('https://camera.example.com/ios-camera?preparation_id=prep_test#')
    with db._connect() as c:
        row = dict(c.execute('SELECT * FROM strict_ios_camera_invites').fetchone())
    assert row['token_hash'] == digest(token)
    assert token not in json.dumps(row)
    assert row['account_id'] == 'account' and row['session_id'] == 'session_real'
    assert row['participant_hash'] == digest('c' * 43)
    assert row['redeemed_at'] is None


def test_unknown_account_session_and_unregistered_device_cannot_invite(rig):
    db, store, _, _, _ = rig
    with pytest.raises(PermissionError):
        store.invite('another', 'prep_test', 'session_real')
    with pytest.raises(PermissionError):
        store.invite('account', 'prep_test', 'other_session')
    with db._connect() as c:
        c.execute('DELETE FROM strict_join_participants')
    with pytest.raises(PermissionError):
        invite(store)


def test_opening_is_metadata_only_and_does_not_redeem_or_authenticate(rig):
    _, store, _, _, _ = rig
    _, token = invite(store)
    assert store.opened(token)['state'] == 'IOS_INVITE_OPENED'
    assert store.status('account', 'prep_test')['state'] == 'IOS_INVITE_OPENED'
    with pytest.raises(PermissionError):
        store.authenticate(token)
    assert store.redeem(token, 'prep_test', 'c' * 43)['state'] == 'IOS_DEVICE_AUTHENTICATED'


def test_companion_credential_and_preparation_required_not_ua_or_url_parameters(rig):
    _, store, _, _, _ = rig
    _, token = invite(store)
    with pytest.raises(PermissionError):
        store.redeem(token, 'other_prep', 'c' * 43)
    with pytest.raises(PermissionError):
        store.redeem(token, 'prep_test', 'x' * 43)
    binding = store.redeem(token, 'prep_test', 'c' * 43)
    assert store.authenticate(binding['credential'])['session_id'] == 'session_real'
    with pytest.raises(PermissionError):
        store.redeem(token, 'prep_test', 'c' * 43)


def test_expiry_replay_and_replacement_invitation(rig):
    _, store, now, _, _ = rig
    _, expired = invite(store)
    now[0] += 180
    assert store.status('account', 'prep_test')['state'] == 'IOS_INVITE_EXPIRED'
    with pytest.raises(PermissionError):
        store.redeem(expired, 'prep_test', 'c' * 43)
    _, old = invite(store)
    _, new = invite(store)
    with pytest.raises(PermissionError):
        store.redeem(old, 'prep_test', 'c' * 43)
    binding = store.redeem(new, 'prep_test', 'c' * 43)
    with pytest.raises(PermissionError):
        store.redeem(new, 'prep_test', 'c' * 43)
    assert store.authenticate(binding['credential'])['camera_id'] == binding['camera_id']


def test_concurrent_redemption_is_atomically_single_use(rig):
    db, store, now, _, validator = rig
    _, token = invite(store)
    second = StrictIOSCamera(db, clock=lambda: now[0], session_validator=validator)
    second.configure('https://camera.example.com', enabled=True, tls_verified=True)
    def attempt(service):
        try:
            return service.redeem(token, 'prep_test', 'c' * 43)
        except PermissionError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [store, second]))
    assert sum(item is not None for item in results) == 1
    with db._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strict_ios_camera_bindings').fetchone()[0] == 1


def test_failed_redemptions_count_toward_rate_limit_and_survive_restart(rig):
    db, store, now, _, validator = rig
    _, token = invite(store)
    for _ in range(10):
        with pytest.raises(PermissionError):
            store.redeem('z' * 43, 'prep_test', 'c' * 43)
    restarted = StrictIOSCamera(db, clock=lambda: now[0], session_validator=validator)
    restarted.configure('https://camera.example.com', enabled=True, tls_verified=True)
    with pytest.raises(PermissionError, match='Too many'):
        restarted.redeem(token, 'prep_test', 'c' * 43)
    now[0] += 60
    assert restarted.redeem(token, 'prep_test', 'c' * 43)['success']


def test_credential_rotation_invalidates_previous_secret_and_reuses_identity(rig):
    _, store, now, _, _ = rig
    binding = paired(store)
    original = binding['credential']
    now[0] += 90
    rotated = store.rotate(original)
    assert rotated['camera_id'] == binding['camera_id']
    assert rotated['credential'] != original
    assert rotated['expires_at'] == now[0] + 600
    with pytest.raises(PermissionError):
        store.authenticate(original)
    assert store.authenticate(rotated['credential'])['camera_id'] == binding['camera_id']
    now[0] += 600
    with pytest.raises(PermissionError):
        store.authenticate(rotated['credential'])


def test_signaling_or_client_report_cannot_claim_actual_streaming(rig):
    _, store, now, _, _ = rig
    binding = paired(store)
    secret = binding['credential']
    with pytest.raises(ValueError):
        store.client_state(secret, 'IOS_CAMERA_STREAMING', {'frames_received': 999})
    store.client_state(secret, 'IOS_VIDEO_CONNECTING', {'signaling': 'connected', 'peer': 'connected'})
    assert not store.status('account', 'prep_test')['streaming']
    store.receiver_frame(secret, 1)
    status = store.status('account', 'prep_test')
    assert status['state'] == 'IOS_CAMERA_STREAMING' and status['streaming']
    assert status['last_frame_at'] == now[0]
    with pytest.raises(ValueError):
        store.receiver_frame(secret, 1)
    now[0] += 8
    stalled = store.status('account', 'prep_test')
    assert stalled['state'] == 'IOS_CAMERA_INTERRUPTED' and not stalled['streaming']
    store.receiver_frame(secret, 2)
    assert store.status('account', 'prep_test')['streaming']


def test_startup_does_not_reuse_old_streaming_claim(rig):
    db, store, now, _, validator = rig
    binding = paired(store)
    store.receiver_frame(binding['credential'], 1)
    restarted = StrictIOSCamera(db, clock=lambda: now[0], session_validator=validator)
    restarted.configure('https://camera.example.com', enabled=True, tls_verified=True)
    assert not restarted.status('account', 'prep_test')['streaming']
    assert restarted.authenticate(binding['credential'])['frame_sequence'] == 1
    restarted.receiver_frame(binding['credential'], 2)
    assert restarted.status('account', 'prep_test')['streaming']


def test_background_stop_and_diagnostics_do_not_modify_registered_companion(rig):
    db, store, _, _, _ = rig
    binding = paired(store)
    with db._connect() as c:
        before = tuple(c.execute('SELECT * FROM strict_join_participants').fetchone())
    store.client_state(binding['credential'], 'IOS_CAMERA_INTERRUPTED', {
        'visible': False, 'permission': 'granted', 'error_name': 'NotReadableError',
        'token': 'SECRET', 'jpeg': 'PRIVATE_IMAGE', 'url': 'https://bad/?key=SECRET',
        'peer': 'disconnected', 'signaling': 'connected'})
    status = store.status('account', 'prep_test')
    assert status['state'] == 'IOS_CAMERA_INTERRUPTED'
    assert status['diagnostics']['error_name'] == 'NotReadableError'
    assert all(key not in status['diagnostics'] for key in ('token', 'jpeg', 'url'))
    store.client_state(binding['credential'], 'IOS_CAMERA_STOPPED')
    with db._connect() as c:
        assert tuple(c.execute('SELECT * FROM strict_join_participants').fetchone()) == before


@pytest.mark.parametrize('cause', ['session_end', 'participant_replaced', 'prep_finished', 'camera_revoked'])
def test_live_authorization_checks_prevent_stale_cross_session_or_device_access(rig, cause):
    db, store, _, live, _ = rig
    binding = paired(store)
    if cause == 'session_end':
        live[0] = False
    elif cause == 'participant_replaced':
        with db._connect() as c:
            c.execute("UPDATE strict_join_participants SET credential_hash=?", (digest('new_registration'),))
    elif cause == 'prep_finished':
        with db._connect() as c:
            c.execute("UPDATE strict_preparations SET status='FINISHED'")
    else:
        store.client_state(binding['credential'], 'IOS_CAMERA_REVOKED')
    for action in (lambda: store.authenticate(binding['credential']),
                   lambda: store.receiver_identity(binding['camera_id']),
                   lambda: store.receiver_frame(binding['credential'], 1),
                   lambda: store.receiver_frame_by_id(binding['camera_id'], 1)):
        with pytest.raises(PermissionError):
            action()
    assert not store.status('account', 'prep_test')['streaming']


def test_end_session_revokes_tokens_and_credentials_without_touching_history(rig):
    db, store, _, _, _ = rig
    binding = paired(store)
    _, pending = invite(store)
    with db._connect() as c:
        before_scores = [tuple(row) for row in c.execute('SELECT * FROM score_events')]
        before_companion = [tuple(row) for row in c.execute('SELECT * FROM strict_join_participants')]
    store.stop_session('session_real')
    with pytest.raises(PermissionError):
        store.authenticate(binding['credential'])
    with pytest.raises(PermissionError):
        store.redeem(pending, 'prep_test', 'c' * 43)
    with db._connect() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM score_events')] == before_scores
        assert [tuple(row) for row in c.execute('SELECT * FROM strict_join_participants')] == before_companion
        text = json.dumps([dict(row) for row in c.execute('SELECT * FROM strict_ios_camera_bindings')])
    assert binding['credential'] not in text and pending not in text


def test_audio_track_diagnostic_revokes_video_only_authorization(rig):
    _, store, _, _, _ = rig
    binding = paired(store)
    result = store.client_state(binding['credential'], 'IOS_CAMERA_INITIALIZING', {'audio_tracks_present': True})
    assert result['state'] == 'IOS_CAMERA_REVOKED'
    with pytest.raises(PermissionError):
        store.authenticate(binding['credential'])


def test_buffered_receiver_frames_do_not_revive_explicitly_stopped_camera(rig):
    _, store, _, _, _ = rig
    binding = paired(store)
    store.receiver_frame(binding['credential'], 1)
    store.client_state(binding['credential'], 'IOS_CAMERA_STOPPED')
    with pytest.raises(ValueError, match='not accepting media'):
        store.receiver_frame(binding['credential'], 2)
    assert not store.status('account', 'prep_test')['streaming']


def test_no_validator_no_active_camera_authority(rig):
    db, _, now, _, _ = rig
    store = StrictIOSCamera(db, clock=lambda: now[0])
    store.configure('https://camera.example.com', enabled=True, tls_verified=True)
    with pytest.raises(PermissionError, match='active Windows'):
        invite(store)
