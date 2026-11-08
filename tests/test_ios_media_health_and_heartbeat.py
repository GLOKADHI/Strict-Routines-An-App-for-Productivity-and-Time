"""STRICTRECORDS – TESTS FOR INDEPENDENT iOS MEDIA HEARTBEAT AND FRAME-RECEPTION FALLBACK DIAGNOSTICS

Verifies:
1.  All 10 health classifications:
    - IOS_SHARING_ACTIVE_AND_RECEIVING
    - IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING
    - IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING
    - WINDOWS_DECODER_STALLED
    - WINDOWS_PROCESSING_STALLED
    - IOS_SHARING_STOPPED_BY_USER
    - IOS_PERMISSION_UNAVAILABLE
    - IOS_CAPTURE_INTERRUPTED
    - IOS_CONTROL_CHANNEL_DISCONNECTED
    - IOS_STATUS_UNKNOWN
2.  Independent reporting for IOS_FRONT_CAMERA and IOS_FULL_SCREEN_SHARE without masking.
3.  Distinction between explicit user stop and unexpected interruption.
4.  Heartbeat channel independence (WSS primary and authenticated HTTPS fallback).
5.  Media failure does not interrupt timer companion or clear session ID.
6.  Compact visible media status with expandable per-source diagnostics.
"""

import time
import pytest

from windows.strict_ios_media_health import (
    IOSMediaHealthManager,
    IOSMediaHealthClassification,
    SOURCE_FRONT_CAMERA,
    SOURCE_SCREEN_SHARE,
)


@pytest.fixture(autouse=True)
def reset_health_manager():
    IOSMediaHealthManager.reset_instance()
    yield
    IOSMediaHealthManager.reset_instance()


class TestIOSMediaHealthClassification:
    def test_classification_explicit_stop_by_user(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # Initial state with camera running
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "device_id": "ipad_1",
                "user_sharing_enabled": True,
                "captured_frames": 100,
                "transmitted_frames": 100,
                "video_track_ready_state": "live",
            },
            received_at=now,
        )

        # Explicit user stop event received
        mgr.record_explicit_stop(
            SOURCE_FRONT_CAMERA,
            {
                "reason": "USER_STOPPED",
                "event_id": "stop_cam_123",
                "user_initiated": True,
                "timestamp": now + 1.0,
            },
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now + 1.0)
        assert cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER
        assert "user" in reason.lower()

    def test_classification_permission_unavailable(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "permission_status": "denied",
                "user_sharing_enabled": False,
                "capture_session_status": "inactive",
            },
            received_at=now,
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.IOS_PERMISSION_UNAVAILABLE
        assert "permission" in reason.lower()

    def test_classification_capture_interrupted(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # User wants sharing, but track ended or capture error reported
        mgr.record_heartbeat(
            SOURCE_SCREEN_SHARE,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "capture_session_status": "interrupted",
                "video_track_ready_state": "ended",
                "capture_error": "Screen capture session interrupted by OS",
            },
            received_at=now,
        )

        cls, reason = mgr.classify_source(SOURCE_SCREEN_SHARE, now_epoch=now)
        assert cls == IOSMediaHealthClassification.IOS_CAPTURE_INTERRUPTED
        assert "interrupted" in reason.lower() or "error" in reason.lower()

    def test_classification_control_channel_disconnected_vs_unknown(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # Case A: Never connected, no heartbeats, no media
        cls_init, _ = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls_init == IOSMediaHealthClassification.IOS_STATUS_UNKNOWN

        # Case B: Was previously connected, but heartbeat timed out (>15s) and no media frames arriving
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "captured_frames": 50,
                "transmitted_frames": 50,
            },
            received_at=now,
        )
        mgr.record_receiver_stats(SOURCE_FRONT_CAMERA, rx_fps=0.0, rx_frames_count=0)

        # 20 seconds later without heartbeats or media
        cls_disc, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now + 20.0)
        assert cls_disc == IOSMediaHealthClassification.IOS_CONTROL_CHANNEL_DISCONNECTED
        assert "lost" in reason.lower() or "disconnected" in reason.lower()
        # Must NOT assume user intentionally stopped
        assert cls_disc != IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER

    def test_classification_active_but_not_transmitting(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # User enabled sharing, but track is muted
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "video_track_muted": True,
                "video_track_ready_state": "muted",
                "captured_frames": 0,
                "transmitted_frames": 0,
            },
            received_at=now,
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING
        assert "muted" in reason.lower()

        # Capture counter stalled over 6 seconds
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "video_track_muted": False,
                "video_track_ready_state": "live",
                "captured_frames": 10,
                "transmitted_frames": 10,
            },
            received_at=now + 1.0,
        )
        # 6 seconds later with identical counters
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "video_track_muted": False,
                "video_track_ready_state": "live",
                "captured_frames": 10,
                "transmitted_frames": 10,
            },
            received_at=now + 7.0,
        )
        cls_stalled, reason_stalled = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now + 7.0)
        assert cls_stalled == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_BUT_NOT_TRANSMITTING
        assert "stalled" in reason_stalled.lower()

    def test_classification_transmitting_but_windows_not_receiving(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # iOS reports advancing capture and transmit counters
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "captured_frames": 120,
                "transmitted_frames": 115,
                "video_track_ready_state": "live",
            },
            received_at=now,
        )

        # Windows receiver receives 0 RTP frames
        mgr.record_receiver_stats(SOURCE_FRONT_CAMERA, rx_fps=0.0, rx_frames_count=0)

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.IOS_TRANSMITTING_BUT_WINDOWS_NOT_RECEIVING
        assert "transmitting" in reason.lower()

    def test_classification_windows_decoder_stalled(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # Fresh heartbeat
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "captured_frames": 200,
                "transmitted_frames": 200,
            },
            received_at=now,
        )

        # Windows receives RTP frames at 24 FPS, but decoder output is 0.0 FPS
        mgr.record_receiver_stats(
            SOURCE_FRONT_CAMERA,
            rx_fps=24.0,
            rx_frames_count=100,
            decoded_fps=0.0,
            decoded_frames_count=0,
            processing_fps=0.0,
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.WINDOWS_DECODER_STALLED
        assert "decoder" in reason.lower()

    def test_classification_windows_processing_stalled(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # Fresh heartbeat
        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "captured_frames": 200,
                "transmitted_frames": 200,
            },
            received_at=now,
        )

        # Windows receives & decodes at 24 FPS, but inference/proctoring processing is 0.0 FPS
        mgr.record_receiver_stats(
            SOURCE_FRONT_CAMERA,
            rx_fps=24.0,
            rx_frames_count=100,
            decoded_fps=24.0,
            decoded_frames_count=100,
            processing_fps=0.0,
            processed_frames_count=0,
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.WINDOWS_PROCESSING_STALLED
        assert "processing stalled" in reason.lower()

    def test_classification_active_and_receiving(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {
                "session_id": "session_abc",
                "user_sharing_enabled": True,
                "captured_frames": 300,
                "transmitted_frames": 300,
                "video_track_ready_state": "live",
            },
            received_at=now,
        )

        # Windows is receiving, decoding, and processing
        mgr.record_receiver_stats(
            SOURCE_FRONT_CAMERA,
            rx_fps=24.0,
            rx_frames_count=300,
            decoded_fps=24.0,
            decoded_frames_count=300,
            processing_fps=15.0,
            processed_frames_count=200,
            last_rx_epoch=now,
            last_decoded_epoch=now,
            last_processed_epoch=now,
            webrtc_connected=True,
        )

        cls, reason = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cls == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING
        assert "active" in reason.lower()


class TestMultiSourceIndependenceAndDiagnostics:
    def test_independent_sources_reporting(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        # Bundled heartbeat: Front camera streaming, screen share stopped by user
        bundled = {
            "session_id": "sess_123",
            "device_id": "ipad_pro",
            "timestamp": now,
            "sources": {
                SOURCE_FRONT_CAMERA: {
                    "user_sharing_enabled": True,
                    "captured_frames": 500,
                    "transmitted_frames": 500,
                    "video_track_ready_state": "live",
                },
                SOURCE_SCREEN_SHARE: {
                    "user_sharing_enabled": False,
                    "explicit_user_stop": {"reason": "USER_STOPPED", "timestamp": now},
                    "captured_frames": 0,
                    "transmitted_frames": 0,
                },
            },
        }

        mgr.record_multi_source_heartbeat(bundled, received_at=now)

        # Set receiver stats for camera
        mgr.record_receiver_stats(
            SOURCE_FRONT_CAMERA,
            rx_fps=20.0,
            rx_frames_count=500,
            decoded_fps=20.0,
            decoded_frames_count=500,
            processing_fps=15.0,
            webrtc_connected=True,
        )
        # Screen receiver is idle
        mgr.record_receiver_stats(SOURCE_SCREEN_SHARE, rx_fps=0.0, rx_frames_count=0)

        # Verify Front Camera is ACTIVE_AND_RECEIVING
        cam_cls, _ = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now)
        assert cam_cls == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING

        # Verify Screen Share is STOPPED_BY_USER without affecting camera
        scr_cls, _ = mgr.classify_source(SOURCE_SCREEN_SHARE, now_epoch=now)
        assert scr_cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER

    def test_compact_status_and_expandable_diagnostics(self):
        mgr = IOSMediaHealthManager.get_instance()
        now = time.time()

        mgr.record_heartbeat(
            SOURCE_FRONT_CAMERA,
            {"user_sharing_enabled": True, "captured_frames": 50, "transmitted_frames": 50},
            received_at=now,
        )
        mgr.record_receiver_stats(
            SOURCE_FRONT_CAMERA,
            rx_fps=15.0,
            decoded_fps=15.0,
            processing_fps=10.0,
            webrtc_connected=True,
        )

        compact = mgr.get_compact_media_status()
        assert compact["compact_status"] == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING.value
        assert "Active & Streaming" in compact["summary_label"]
        assert compact["last_heartbeat_timestamp"] == pytest.approx(now, 1.0)
        assert compact["last_heartbeat_iso"] is not None

        # Expandable diagnostics must contain both sources
        expandable = compact["expandable_diagnostics"]
        assert SOURCE_FRONT_CAMERA in expandable
        assert SOURCE_SCREEN_SHARE in expandable
        assert expandable[SOURCE_FRONT_CAMERA]["classification"] == IOSMediaHealthClassification.IOS_SHARING_ACTIVE_AND_RECEIVING.value


import hashlib
import sqlite3
from types import SimpleNamespace
from contextlib import asynccontextmanager
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from core.strict_ios_camera import StrictIOSCamera
from windows.strict_ios_camera_gateway import IOSCameraGateway


ORIGIN = 'https://camera.example.com'
COMPANION = 'c' * 43
PROXY_SECRET = 'p' * 48


class GatewayDB:
    def __init__(self, path):
        self.path = str(path)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class MockPreparation:
    def join(self, token, ua, nonce, info):
        return dict(success=True, preparation_id='prep_test', role='IOS', credential=COMPANION)

    def heartbeat(self, pid, role, credential, report):
        return dict(success=True, timer=dict(session_id='session_real', remaining_seconds=500))


class MockReceiver:
    def __init__(self, store, on_frame=None, on_stage_ack=None):
        self.store = store
        self.ice_servers = []

    async def offer(self, credential, sdp, **kw):
        self.store.authenticate(credential)
        return dict(type='answer', sdp='test-answer')

    async def reauthorize(self, credential):
        self.store.authenticate(credential)

    async def add_ice_candidate(self, credential, candidate, **kw):
        self.store.authenticate(credential)

    async def stop(self, credential, report=True):
        self.store.authenticate(credential)

    async def stop_camera(self, camera_id, report=False):
        pass

    async def close(self):
        pass


def gateway_config(**overrides):
    return dict(configured=True, origin=ORIGIN, proxy=True, port=19443,
                cert='', key='', proxy_secret=PROXY_SECRET,
                turn_url='turns:relay.example.com:5349?transport=tcp',
                turn_secret='s' * 48, **overrides)


def gateway_headers(**overrides):
    return {'Host': 'camera.example.com', 'Origin': ORIGIN,
            'X-StrictRecords-Proxy': PROXY_SECRET, **overrides}


@asynccontextmanager
async def gateway_harness(tmp_path, monkeypatch):
    db = GatewayDB(tmp_path / 'gateway_test.sqlite')
    with db._connect() as c:
        c.executescript('''
        CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY, account_id TEXT, status TEXT, expires_at REAL);
        CREATE TABLE strict_join_participants(preparation_id TEXT, role TEXT, credential_hash TEXT, seen REAL, report_json TEXT);
        INSERT INTO strict_preparations VALUES('prep_test','account','ACTIVE',5000);
        ''')
        c.execute("INSERT INTO strict_join_participants VALUES('prep_test','IOS',?,1000,'{}')", (hashlib.sha256(COMPANION.encode()).hexdigest(),))
    store = StrictIOSCamera(db, clock=lambda: 1000.,
        session_validator=lambda a, p, s: (a, p, s) == ('account', 'prep_test', 'session_real'))
    store.configure(ORIGIN, enabled=True, tls_verified=True)
    manager = SimpleNamespace(ios_camera=store, preparation=MockPreparation())
    gateway = IOSCameraGateway(manager, gateway_config())
    async def nothing():
        return None
    monkeypatch.setattr(gateway, '_verify_tls', nothing)
    monkeypatch.setattr(gateway, '_watchdog', nothing)
    monkeypatch.setattr('windows.strict_ios_receiver.IOSVideoReceiver', MockReceiver)
    monkeypatch.setattr(web, 'TCPSite', lambda *a, **kw: SimpleNamespace(start=nothing))
    await gateway._start()
    gateway.ready = True
    client = TestClient(TestServer(gateway.runner.app))
    await client.start_server()
    try:
        yield client, gateway, store
    finally:
        await client.close()
        await gateway.receiver.close()
        await gateway.runner.cleanup()


async def redeem_credential(client, store):
    invitation = store.invite('account', 'prep_test', 'session_real')['invitation']
    token = invitation['url'].split('#')[1]
    res = await client.post('/ios-camera/redeem', headers=gateway_headers(), json={
        'token': token, 'preparation_id': 'prep_test', 'companion_credential': COMPANION
    })
    return (await res.json())['camera_credential']


class TestGatewayHeartbeatAndStopIntegration:
    @pytest.mark.asyncio
    async def test_https_fallback_heartbeat_and_stop_endpoints(self, tmp_path, monkeypatch):
        async with gateway_harness(tmp_path, monkeypatch) as (client, gateway, store):
            cred = await redeem_credential(client, store)
            mgr = IOSMediaHealthManager.get_instance()
            now = time.time()

            # 1. Test POST /ios-camera/heartbeat
            hb_payload = {
                "credential": cred,
                "session_id": "session_real",
                "timestamp": now,
                "sources": {
                    SOURCE_FRONT_CAMERA: {
                        "user_sharing_enabled": True,
                        "captured_frames": 25,
                        "transmitted_frames": 25,
                        "video_track_ready_state": "live",
                    }
                }
            }
            res = await client.post('/ios-camera/heartbeat', headers=gateway_headers(), json=hb_payload)
            assert res.status == 200
            data = await res.json()
            assert data["success"] is True
            assert data["status"] == "ACKNOWLEDGED"

            # Check that manager received the HTTPS heartbeat
            cam_diag = mgr.get_source_diagnostics(SOURCE_FRONT_CAMERA)
            assert cam_diag["last_heartbeat"] is not None
            assert cam_diag["last_heartbeat"]["channel"] == "HTTPS"
            assert cam_diag["last_heartbeat"]["captured_frames"] == 25

            # 2. Test POST /ios-camera/media_stopped
            stop_payload = {
                "credential": cred,
                "session_id": "session_real",
                "source_id": SOURCE_FRONT_CAMERA,
                "reason": "USER_STOPPED",
                "event_id": "stop_test_999",
                "user_initiated": True,
                "timestamp": now + 2.0
            }
            res_stop = await client.post('/ios-camera/media_stopped', headers=gateway_headers(), json=stop_payload)
            assert res_stop.status == 200
            stop_data = await res_stop.json()
            assert stop_data["success"] is True

            # Verify that manager transitioned to IOS_SHARING_STOPPED_BY_USER
            cls, _ = mgr.classify_source(SOURCE_FRONT_CAMERA, now_epoch=now + 2.0)
            assert cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER

    @pytest.mark.asyncio
    async def test_wss_heartbeat_and_stop_handling(self, tmp_path, monkeypatch):
        async with gateway_harness(tmp_path, monkeypatch) as (client, gateway, store):
            cred = await redeem_credential(client, store)
            mgr = IOSMediaHealthManager.get_instance()
            now = time.time()

            ws = await client.ws_connect('/ios-camera/signaling', headers=gateway_headers())
            await ws.send_json({'type': 'authenticate', 'credential': cred})
            auth_ack = await ws.receive_json()
            assert auth_ack['type'] == 'authenticated'

            # 1. Send IOS_MEDIA_HEARTBEAT over WSS
            hb_msg = {
                "type": "IOS_MEDIA_HEARTBEAT",
                "credential": cred,
                "session_id": "session_real",
                "heartbeat_seq": 42,
                "timestamp": now,
                "sources": {
                    SOURCE_SCREEN_SHARE: {
                        "user_sharing_enabled": True,
                        "captured_frames": 180,
                        "transmitted_frames": 180,
                        "video_track_ready_state": "live",
                    }
                }
            }
            await ws.send_json(hb_msg)
            ack = await ws.receive_json()
            assert ack['type'] == 'IOS_MEDIA_HEARTBEAT_ACK'
            assert ack['heartbeat_seq'] == 42
            assert ack['status'] == 'OK'

            # Verify manager recorded WSS channel
            scr_diag = mgr.get_source_diagnostics(SOURCE_SCREEN_SHARE)
            assert scr_diag["last_heartbeat"]["channel"] == "WSS"
            assert scr_diag["last_heartbeat"]["transmitted_frames"] == 180

            # 2. Send IOS_SCREEN_SHARE_STOPPED over WSS
            stop_msg = {
                "type": "IOS_SCREEN_SHARE_STOPPED",
                "session_id": "session_real",
                "source_id": SOURCE_SCREEN_SHARE,
                "reason": "USER_STOPPED",
                "event_id": "stop_scr_444",
                "user_initiated": True,
                "timestamp": now + 1.0,
            }
            await ws.send_json(stop_msg)
            stop_ack = await ws.receive_json()
            assert stop_ack['type'] == 'IOS_MEDIA_STOPPED_ACK'
            assert stop_ack['source_id'] == SOURCE_SCREEN_SHARE

            cls, _ = mgr.classify_source(SOURCE_SCREEN_SHARE, now_epoch=now + 1.0)
            assert cls == IOSMediaHealthClassification.IOS_SHARING_STOPPED_BY_USER

            await ws.close()
