"""HTTPS gateway authorization tests. No live domain, camera, TLS bypass or media.

The test-only loopback proxy is authenticated with a disposable shared secret.
The production default-CA probe is replaced, not asserted as passed by these tests.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from core.strict_ios_camera import StrictIOSCamera
from windows.strict_ios_camera_gateway import IOSCameraGateway, camera_configuration, temporary_ice_servers
import windows.strict_ios_camera_gateway as gateway_module


ORIGIN = 'https://camera.example.com'
COMPANION = 'c' * 43
PROXY_SECRET = 'p' * 48


class DB:
    def __init__(self, path):
        self.path = str(path)

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


class Preparation:
    def __init__(self):
        self.calls = []

    def join(self, token, ua, nonce, info):
        self.calls.append(('join', token, ua, nonce, info))
        return dict(success=True, preparation_id='prep_test', role='IOS', credential=COMPANION)

    def heartbeat(self, pid, role, credential, report):
        self.calls.append(('heartbeat', pid, role, credential, report))
        if credential != COMPANION:
            raise PermissionError('wrong companion')
        return dict(success=True, timer=dict(session_id='session_real', remaining_seconds=500))


class Receiver:
    """Signaling spy. Real media receiver has separate decoder/encryption tests."""
    def __init__(self, store, on_frame=None):
        self.store = store
        self.events = []
        self.ice_servers = []

    async def offer(self, credential, sdp):
        self.store.authenticate(credential)
        self.events.append(('offer', credential, sdp))
        return dict(type='answer', sdp='test-answer')

    async def reauthorize(self, credential):
        self.store.authenticate(credential)
        self.events.append(('reauthorize', credential))

    async def add_ice_candidate(self, credential, candidate):
        self.store.authenticate(credential)
        self.events.append(('candidate', credential, candidate))

    async def stop(self, credential, report=True):
        self.store.authenticate(credential)
        self.events.append(('stop', credential, report))

    async def stop_camera(self, camera_id, report=False):
        self.events.append(('stop_camera', camera_id, report))

    async def close(self):
        self.events.append(('close',))


def config(**overrides):
    return dict(configured=True, origin=ORIGIN, proxy=True, port=19443,
                cert='', key='', proxy_secret=PROXY_SECRET,
                turn_url='turns:relay.example.com:5349?transport=tcp',
                turn_secret='s' * 48, **overrides)


def headers(**overrides):
    return {'Host': 'camera.example.com', 'Origin': ORIGIN,
            'X-StrictRecords-Proxy': PROXY_SECRET, **overrides}


@asynccontextmanager
async def harness(tmp_path, monkeypatch, *, ready=True):
    db = DB(tmp_path / 'gateway.sqlite')
    with db._connect() as c:
        c.executescript('''
        CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY, account_id TEXT, status TEXT, expires_at REAL);
        CREATE TABLE strict_join_participants(preparation_id TEXT, role TEXT, credential_hash TEXT, seen REAL, report_json TEXT);
        INSERT INTO strict_preparations VALUES('prep_test','account','ACTIVE',5000);
        ''')
        c.execute("INSERT INTO strict_join_participants VALUES('prep_test','IOS',?,1000,'{}')", (hashlib.sha256(COMPANION.encode()).hexdigest(),))
    store = StrictIOSCamera(db, clock=lambda: 1000.,
        session_validator=lambda a, p, s: (a, p, s) == ('account', 'prep_test', 'session_real'))
    store.configure(ORIGIN, enabled=ready, tls_verified=ready)
    manager = SimpleNamespace(ios_camera=store, preparation=Preparation())
    gateway = IOSCameraGateway(manager, config())
    async def nothing():
        return None
    monkeypatch.setattr(gateway, '_verify_tls', nothing)
    monkeypatch.setattr(gateway, '_watchdog', nothing)
    monkeypatch.setattr('windows.strict_ios_receiver.IOSVideoReceiver', Receiver)
    # _start builds the actual middleware/routes; only its separate production
    # listener is suppressed. TestServer owns its isolated ephemeral loopback port.
    monkeypatch.setattr(web, 'TCPSite', lambda *a, **kw: SimpleNamespace(start=nothing))
    await gateway._start()
    gateway.ready = ready
    client = TestClient(TestServer(gateway.runner.app))
    await client.start_server()
    try:
        yield client, gateway, store
    finally:
        await client.close()
        await gateway.receiver.close()
        await gateway.runner.cleanup()


def issue(store):
    invitation = store.invite('account', 'prep_test', 'session_real')['invitation']
    return invitation['url'].split('#')[1]


async def redeem(client, store):
    token = issue(store)
    response = await client.post('/ios-camera/redeem', headers=headers(), json={
        'token': token, 'preparation_id': 'prep_test', 'companion_credential': COMPANION})
    assert response.status == 200
    return await response.json()


def test_missing_config_and_insecure_config_never_start_camera():
    assert camera_configuration({})['configured'] is False
    base = {'STRICTRECORDS_IOS_CAMERA_ORIGIN': ORIGIN,
            'STRICTRECORDS_IOS_CAMERA_PROXY': '1',
            'STRICTRECORDS_IOS_CAMERA_PROXY_SECRET': PROXY_SECRET,
            'STRICTRECORDS_IOS_CAMERA_TURN_URL': 'turns:relay.example.com:5349',
            'STRICTRECORDS_IOS_CAMERA_TURN_SECRET': 's' * 48}
    assert camera_configuration(base)['configured']
    for key, value in [('STRICTRECORDS_IOS_CAMERA_ORIGIN', 'http://camera.example.com'),
                       ('STRICTRECORDS_IOS_CAMERA_ORIGIN', 'https://192.168.0.2'),
                       ('STRICTRECORDS_IOS_CAMERA_PROXY_SECRET', 'short'),
                       ('STRICTRECORDS_IOS_CAMERA_TURN_URL', 'turn:relay.example.com'),
                       ('STRICTRECORDS_IOS_CAMERA_TURN_SECRET', 'short'),
                       ('STRICTRECORDS_IOS_CAMERA_PORT', '80')]:
        with pytest.raises(ValueError):
            camera_configuration({**base, key: value})


def test_turn_credentials_are_ephemeral_and_camera_scoped():
    values = temporary_ice_servers(config(), {'camera_id': 'camera-one'}, now=1000.)
    entry = values[0]
    assert entry['username'] == '1600:camera-one'
    expected = base64.b64encode(hmac.new(('s' * 48).encode(), b'1600:camera-one', hashlib.sha1).digest()).decode()
    assert entry['credential'] == expected
    assert entry['urls'] == ['turns:relay.example.com:5349?transport=tcp']
    assert temporary_ice_servers(config(), {'camera_id': 'camera-two'}, now=1000.)[0]['credential'] != expected
    assert temporary_ice_servers(config(), {'camera_id': 'camera-one'}, now=1100.)[0]['credential'] != expected


def test_actual_camera_page_security_headers_and_no_general_backend_routes(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, _, _):
            response = await client.get('/ios-camera', headers=headers())
            assert response.status == 200
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.headers['Referrer-Policy'] == 'no-referrer'
            assert response.headers['X-Frame-Options'] == 'DENY'
            assert 'microphone=()' in response.headers['Permissions-Policy']
            csp = response.headers['Content-Security-Policy']
            assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
            assert 'wss://camera.example.com' in csp and 'ws://' not in csp
            assert response.headers.get('Access-Control-Allow-Origin') is None
            for path in ('/api/admin', '/api/score', '/api/strict_session/preparation/invite'):
                response = await client.post(path, headers=headers(), json={})
                assert response.status == 404
    asyncio.run(run())


def test_exact_host_origin_and_proxy_authentication_reject_cross_origin(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, _, _):
            for bad in (headers(Host='evil.example.com'),
                        headers(Host='camera.example.com.evil.test'),
                        headers(Origin='https://evil.example.com'),
                        headers(Origin='http://camera.example.com'),
                        headers(Origin='null'),
                        headers(**{'X-StrictRecords-Proxy': 'wrong'}),
                        headers(**{'Sec-Fetch-Site': 'cross-site'})):
                response = await client.post('/ios-camera/opened', headers=bad, json={'token': 'x' * 43})
                assert response.status == 403
            missing_origin = headers()
            missing_origin.pop('Origin')
            response = await client.post('/ios-camera/opened', headers=missing_origin, json={})
            assert response.status == 403
            public = {'Host': 'camera.example.com'}
            response = await client.get('/ios-camera', headers=public)
            assert response.status == 403
    asyncio.run(run())


def test_camera_cannot_redeem_or_signal_before_tls_verification(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch, ready=False) as (client, _, _):
            response = await client.post('/ios-camera/redeem', headers=headers(), json={})
            assert response.status == 503
            with pytest.raises(WSServerHandshakeError) as error:
                await client.ws_connect('/ios-camera/signaling', headers=headers())
            assert error.value.status == 503
    asyncio.run(run())


def test_redeem_mapping_identity_replay_and_refresh(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, _, store):
            token = issue(store)
            body = dict(token=token, preparation_id='prep_test', companion_credential=COMPANION)
            response = await client.post('/ios-camera/redeem', headers=headers(), json={**body, 'companion_credential': 'wrong' * 10})
            assert response.status == 403
            response = await client.post('/ios-camera/redeem', headers=headers(), json=body)
            result = await response.json()
            assert response.status == 200
            assert result['session_id'] == 'session_real' and result['preparation_id'] == 'prep_test'
            assert 'credential' not in result and 'expires_at' not in result
            assert result['camera_credential'] and result['credential_expires_at'] == 1600
            assert result['ice_servers'][0]['urls'][0].startswith('turns:')
            replay = await client.post('/ios-camera/redeem', headers=headers(), json=body)
            assert replay.status == 403
            response = await client.post('/ios-camera/refresh', headers=headers(), json={'camera_credential': result['camera_credential']})
            rotated = await response.json()
            assert response.status == 200 and rotated['camera_id'] == result['camera_id']
            assert rotated['camera_credential'] != result['camera_credential']
            with pytest.raises(PermissionError):
                store.authenticate(result['camera_credential'])
            assert store.authenticate(rotated['camera_credential'])['session_id'] == 'session_real'
    asyncio.run(run())


def test_client_status_cannot_mark_streaming_or_upload_frames(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, gateway, store):
            binding = await redeem(client, store)
            body = dict(camera_credential=binding['camera_credential'])
            denied = await client.post('/ios-camera/status', headers=headers(), json={**body, 'state': 'IOS_CAMERA_STREAMING', 'diagnostics': {'frames': 999}})
            assert denied.status == 409
            result = await client.post('/ios-camera/status', headers=headers(), json={**body,
                'state': 'IOS_CAMERA_PERMISSION_PENDING', 'diagnostics': {'permission': 'denied', 'error_name': 'NotAllowedError', 'jpeg': 'PRIVATE'}})
            assert result.status == 200
            assert store.status('account', 'prep_test')['diagnostics'] == {'permission': 'denied', 'error_name': 'NotAllowedError'}
            assert not store.status('account', 'prep_test')['streaming']
            for path in ('/ios-camera/frame', '/ios-camera/video', '/ios-camera/upload'):
                result = await client.post(path, headers=headers(), json={**body, 'jpeg': 'PRIVATE'})
                assert result.status == 404
            assert not gateway.receiver.events
    asyncio.run(run())


def test_wss_origin_and_first_message_authentication_are_mandatory(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, gateway, _):
            for origin in ('https://evil.example.com', 'http://camera.example.com', 'null'):
                with pytest.raises(WSServerHandshakeError) as error:
                    await client.ws_connect('/ios-camera/signaling', headers=headers(Origin=origin))
                assert error.value.status == 403
            for message in ({'type': 'offer', 'sdp': 'no-auth'}, {'type': 'authenticate', 'credential': 'x' * 43}):
                ws = await client.ws_connect('/ios-camera/signaling', headers=headers())
                await ws.send_json(message)
                result = await asyncio.wait_for(ws.receive(), 2)
                assert result.type == WSMsgType.CLOSE and result.data == 4003
                await ws.close()
            assert not gateway.receiver.events
    asyncio.run(run())


def test_authenticated_wss_signaling_does_not_claim_media_and_allows_stop(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, gateway, store):
            binding = await redeem(client, store)
            ws = await client.ws_connect('/ios-camera/signaling', headers=headers())
            await ws.send_json({'type': 'authenticate', 'credential': binding['camera_credential']})
            result = await ws.receive_json()
            assert result['type'] == 'authenticated' and result['ice_servers']
            assert not store.status('account', 'prep_test')['streaming']
            await ws.send_json({'type': 'offer', 'sdp': 'valid-test-offer'})
            result = await ws.receive_json()
            assert result == {'type': 'answer', 'sdp': 'test-answer'}
            assert not store.status('account', 'prep_test')['streaming']
            await ws.send_json({'type': 'status', 'state': 'IOS_CAMERA_STOPPED'})
            await ws.send_json({'type': 'unknown'})
            closed = await asyncio.wait_for(ws.receive(), 2)
            assert closed.type == WSMsgType.CLOSE and closed.data == 4003
            assert any(event[0] == 'stop' for event in gateway.receiver.events)
            assert store.status('account', 'prep_test')['state'] == 'IOS_CAMERA_STOPPED'
            await ws.close()
    asyncio.run(run())


def test_rotated_credentials_refresh_receiver_turn_and_cleanup_invalid_old_secret(tmp_path, monkeypatch):
    async def run():
        counter = [0]
        def ice_values(_config, _identity, now=None):
            counter[0] += 1
            return [dict(urls=['turns:relay.example.com'], username='ice-' + str(counter[0]), credential='temporary')]
        monkeypatch.setattr(gateway_module, 'temporary_ice_servers', ice_values)
        async with harness(tmp_path, monkeypatch) as (client, gateway, store):
            binding = await redeem(client, store)
            ws = await client.ws_connect('/ios-camera/signaling', headers=headers())
            await ws.send_json({'type': 'authenticate', 'credential': binding['camera_credential']})
            first_ice = (await ws.receive_json())['ice_servers']
            response = await client.post('/ios-camera/refresh', headers=headers(), json={'camera_credential': binding['camera_credential']})
            rotated = await response.json()
            await ws.send_json({'type': 'authenticate', 'credential': rotated['camera_credential']})
            await ws.send_json({'type': 'offer', 'sdp': 'offer-after-credential-rotation'})
            answer = await ws.receive_json()
            # Optional reauth acknowledgement is permitted before the answer.
            if answer['type'] == 'authenticated':
                answer = await ws.receive_json()
            assert answer['type'] == 'answer'
            assert gateway.receiver.ice_servers != first_ice
            assert any(event[:2] == ('reauthorize', rotated['camera_credential']) for event in gateway.receiver.events)
            # Rotate again without WSS reauth. Closing this socket must release its
            # receiver even though its old credential can no longer authenticate.
            store.rotate(rotated['camera_credential'])
            await ws.close()
            for _ in range(20):
                if binding['camera_id'] not in gateway.sockets:
                    break
                await asyncio.sleep(.01)
            assert any(event[:2] == ('stop_camera', binding['camera_id']) for event in gateway.receiver.events)
    asyncio.run(run())


def test_trusted_timer_retains_exact_inline_script_hash_and_identity(tmp_path, monkeypatch):
    async def run():
        from core.strict_companion_client import JS
        async with harness(tmp_path, monkeypatch) as (client, gateway, _):
            response = await client.get('/strict-session/companion', headers=headers())
            assert response.status == 200
            expected = base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode()
            assert "script-src 'sha256-" + expected + "'" in response.headers['Content-Security-Policy']
            assert 'audio: true' not in await response.text()
            response = await client.post('/api/strict_session/preparation/heartbeat', headers=headers(), json={
                'preparation_id': 'prep_test', 'role': 'IOS', 'credential': COMPANION,
                'report': {'visible': True, 'timer_ready': True}})
            assert response.status == 200
            assert (await response.json())['timer']['session_id'] == 'session_real'
            assert gateway.manager.preparation.calls[-1][0] == 'heartbeat'
            response = await client.post('/api/strict_session/preparation/heartbeat', headers=headers(), json={'credential': 'wrong'})
            assert response.status == 403
    asyncio.run(run())


def test_json_objects_required_instead_of_unhandled_attribute_errors(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, _, _):
            for path in ('/ios-camera/redeem', '/ios-camera/refresh', '/ios-camera/status',
                         '/ios-camera/opened', '/api/strict_session/preparation/join',
                         '/api/strict_session/preparation/heartbeat'):
                response = await client.post(path, headers=headers(), json=[])
                assert response.status in (400, 409), path
            ws = await client.ws_connect('/ios-camera/signaling', headers=headers())
            await ws.send_json([])
            result = await asyncio.wait_for(ws.receive(), 2)
            assert result.type == WSMsgType.CLOSE and result.data == 4003
            await ws.close()
    asyncio.run(run())


def test_wss_instance_probe_can_verify_readiness_without_authorizing_camera(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch, ready=False) as (client, gateway, store):
            ws = await client.ws_connect('/ios-camera/signaling', headers=headers(**{'X-StrictRecords-Probe': gateway.probe}))
            result = await ws.receive_json()
            assert result == {'success': True, 'probe': gateway.probe}
            assert not gateway.receiver.events and not gateway.sockets
            await ws.close()
            for bad in (headers(**{'X-StrictRecords-Probe': 'wrong'}),
                        headers(Origin='https://evil.example', **{'X-StrictRecords-Probe': gateway.probe})):
                with pytest.raises(WSServerHandshakeError) as failure:
                    await client.ws_connect('/ios-camera/signaling', headers=bad)
                assert failure.value.status in (403, 503)
    asyncio.run(run())


def test_timer_enrollment_and_direct_camera_pages_load_their_respective_clients(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, _, _):
            timer = await client.get('/strict-session/companion?preparation_id=prep_test', headers=headers())
            assert '<script src="/ios-camera/join.js"' in await timer.text()
            camera = await client.get('/ios-camera?preparation_id=prep_test', headers=headers())
            assert '<script src="/ios-camera/client.js"' in await camera.text()
            assert 'microphone=()' in camera.headers['Permissions-Policy']
    asyncio.run(run())


def test_signaling_cannot_upload_jpeg_frames_or_claim_streaming(tmp_path, monkeypatch):
    async def run():
        async with harness(tmp_path, monkeypatch) as (client, gateway, store):
            binding = await redeem(client, store)
            ws = await client.ws_connect('/ios-camera/signaling', headers=headers())
            await ws.send_json({'type': 'authenticate', 'credential': binding['camera_credential']})
            assert (await ws.receive_json())['type'] == 'authenticated'
            await ws.send_json({'type': 'frame', 'jpeg': 'untrusted-picture', 'sequence': 1})
            closed = await asyncio.wait_for(ws.receive(), 2)
            assert closed.type == WSMsgType.CLOSE and closed.data == 4003
            assert not store.status('account', 'prep_test')['streaming']
            assert not gateway.preview('account', 'prep_test')['preview']
            await ws.close()
    asyncio.run(run())


def test_tls_verification_recovers_after_slow_dns_propagation(monkeypatch):
    import socket
    from contextlib import asynccontextmanager
    observations = []
    store = SimpleNamespace(configure=lambda *args, **kwargs: observations.append(kwargs))
    gateway = IOSCameraGateway(SimpleNamespace(ios_camera=store), config())
    attempts = [0]

    def resolve(*args):
        attempts[0] += 1
        if attempts[0] <= 13:
            raise socket.gaierror('temporary DNS propagation')
        return []

    class Response:
        status = 200
        async def json(self):
            return {'probe': gateway.probe}

    class Socket:
        async def receive_json(self, timeout):
            return {'probe': gateway.probe}
        async def close(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @asynccontextmanager
        async def get(self, *args, **kwargs):
            yield Response()
        @asynccontextmanager
        async def ws_connect(self, url, **kwargs):
            assert kwargs['origin'] == ORIGIN
            assert kwargs['headers']['X-StrictRecords-Probe'] == gateway.probe
            yield Socket()

    async def no_wait(*args):
        pass

    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr('aiohttp.ClientSession', Client)
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    asyncio.run(gateway._verify_tls())
    assert attempts[0] == 14
    assert gateway.ready and not gateway.error
    assert gateway.verification_details['wss_signaling_ok']
    assert observations == [dict(enabled=True, tls_verified=True)]
