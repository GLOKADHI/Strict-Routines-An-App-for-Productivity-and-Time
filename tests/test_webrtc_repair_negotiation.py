"""
Unit tests for WebRTC negotiation repair:
- Resilient ICE candidate handling (Safari/Chrome standard dictionary fields)
- Queuing pending candidates before remote description installation
- End-of-candidates notification support
- 23-stage lifecycle recording and earliest-failing identification
- Frame confirmation payload generation (IOS_CAMERA_FRAME_READY)
- Sanitized technical diagnostics export
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from windows.strict_ipad_camera_monitor import (
    IPadCameraStreamMonitor,
    CONNECTION_STAGES_23,
)
from windows.strict_ios_receiver import IOSVideoReceiver, _Peer


def test_23_connection_stages_defined():
    """Verify all 23 defined stages are present in the monitor configuration."""
    assert len(CONNECTION_STAGES_23) == 23
    assert CONNECTION_STAGES_23[0] == "1_ios_companion_invitation_auth"
    assert CONNECTION_STAGES_23[1] == "2_device_registration"
    assert CONNECTION_STAGES_23[2] == "3_signaling_websocket_connect"
    assert CONNECTION_STAGES_23[3] == "4_webrtc_peer_connection_create"
    assert CONNECTION_STAGES_23[7] == "8_sdp_offer_delivery_windows"
    assert CONNECTION_STAGES_23[8] == "9_remote_description_installation_windows"
    assert CONNECTION_STAGES_23[10] == "11_sdp_answer_delivery_ios"
    assert CONNECTION_STAGES_23[22] == "23_windows_camera_inference_processing"


def test_record_detailed_stage_and_earliest_failure():
    """Verify that recording stages correctly identifies the earliest failing stage."""
    mon = IPadCameraStreamMonitor()

    # Record first 3 stages as SUCCESS
    mon.record_detailed_stage("1_ios_companion_invitation_auth", success=True)
    mon.record_detailed_stage("2_device_registration", success=True)
    mon.record_detailed_stage("3_signaling_websocket_connect", success=True)

    # Record stage 4 as FAILED
    mon.record_detailed_stage(
        "4_webrtc_peer_connection_create",
        success=False,
        error_message="RTCPeerConnection initialization failed"
    )

    earliest = mon.identify_earliest_failing_stage(
        now_mono=100.0,
        elapsed_without_frames=15.0,
        processing_stalled=False
    )
    assert earliest == "4_webrtc_peer_connection_create"


def test_frame_confirmation_payload_generation():
    """Verify IOS_CAMERA_FRAME_READY payload formatting."""
    mon = IPadCameraStreamMonitor()
    mon.session_id = "sess_test_123"
    mon.frame_sequence = 42
    mon.processed_frames_count = 40

    payload = mon.get_frame_confirmation_payload()

    assert payload["type"] == "IOS_CAMERA_FRAME_READY"
    assert payload["session_id"] == "sess_test_123"
    assert payload["source_id"] == "IOS_FRONT_CAMERA"
    assert payload["received_frame_sequence"] == 42
    assert payload["processed_frame_sequence"] == 40
    assert payload["confirmed"] is True


def test_sanitized_diagnostics_export():
    """Verify that export_diagnostics_sanitized includes 23 stages and redacts secrets."""
    mon = IPadCameraStreamMonitor()
    mon.record_detailed_stage(
        "1_ios_companion_invitation_auth",
        session_id="sess_123",
        connection_id="conn_sec_token_9999",
        success=True
    )

    diag = mon.export_diagnostics_sanitized()
    assert "detailed_stages_23" in diag
    assert len(diag["detailed_stages_23"]) == 23
    assert "exported_at_iso" in diag
    assert "token" not in diag
    assert "credential" not in diag
    assert "turn_secret" not in diag


@pytest.mark.asyncio
async def test_resilient_ice_candidate_parsing():
    """Verify add_ice_candidate does not raise ValueError on standard Safari/Chrome candidate dictionaries."""
    mock_store = MagicMock()
    mock_identity = {
        "camera_id": "cam_test_1",
        "account_id": "user_test",
        "session_id": "sess_test",
        "expires_at": 9999999999.0,
        "revoked": 0,
        "state": "IOS_CAMERA_READY"
    }
    mock_store.lookup.return_value = mock_identity
    mock_store.authenticate.return_value = mock_identity

    receiver = IOSVideoReceiver(mock_store)

    safari_candidate = {
        "candidate": "candidate:842163049 1 udp 1677729535 192.168.1.100 54321 typ srflx raddr 192.168.1.100 rport 54321 generation 0 ufrag abcd network-id 1 network-cost 10",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
        "usernameFragment": "abcd",
        "foundation": "842163049",
        "component": "rtp",
        "priority": 1677729535,
        "address": "192.168.1.100",
        "protocol": "udp",
        "port": 54321,
        "type": "srflx",
        "tcpType": None
    }

    cred1 = "test_camera_credential_32_chars_1"
    peer = _Peer(
        credential=cred1,
        identity={
            "camera_id": "cam_test_1",
            "account_id": "user_test",
            "session_id": "sess_test"
        },
        pc=MagicMock()
    )
    peer.remote_description_installed = True
    peer.pc.addIceCandidate = AsyncMock()
    receiver._peers["cam_test_1"] = peer

    await receiver.add_ice_candidate(cred1, safari_candidate, tx_id="tx_1", connection_id="conn_test")
    assert peer.pc.addIceCandidate.called


@pytest.mark.asyncio
async def test_ice_candidate_queued_before_remote_description():
    """Verify that candidates received prior to remote description installation are queued and not discarded."""
    mock_store = MagicMock()
    mock_identity = {
        "camera_id": "cam_test_2",
        "account_id": "user_test",
        "session_id": "sess_test",
        "expires_at": 9999999999.0,
        "revoked": 0,
        "state": "IOS_CAMERA_READY"
    }
    mock_store.lookup.return_value = mock_identity
    mock_store.authenticate.return_value = mock_identity

    receiver = IOSVideoReceiver(mock_store)

    cred2 = "test_camera_credential_32_chars_2"
    peer = _Peer(
        credential=cred2,
        identity={
            "camera_id": "cam_test_2",
            "account_id": "user_test",
            "session_id": "sess_test"
        },
        pc=MagicMock()
    )
    peer.remote_description_installed = False  # Not yet installed
    peer.pc.addIceCandidate = AsyncMock()
    receiver._peers["cam_test_2"] = peer

    candidate_dict = {
        "candidate": "candidate:1 1 udp 2130706431 192.168.1.50 50000 typ host",
        "sdpMid": "0",
        "sdpMLineIndex": 0
    }

    await receiver.add_ice_candidate(cred2, candidate_dict)

    # Did not call addIceCandidate directly yet
    assert not peer.pc.addIceCandidate.called
    # Successfully placed into pending_candidates queue
    assert len(peer.pending_candidates) == 1
    assert peer.pending_candidates[0].ip == "192.168.1.50"
    assert peer.pending_candidates[0].port == 50000


@pytest.mark.asyncio
async def test_end_of_candidates_handling():
    """Verify that empty/null candidate indicating end-of-candidates does not error."""
    mock_store = MagicMock()
    mock_identity = {
        "camera_id": "cam_test_3",
        "account_id": "user_test",
        "session_id": "sess_test",
        "expires_at": 9999999999.0,
        "revoked": 0,
        "state": "IOS_CAMERA_READY"
    }
    mock_store.lookup.return_value = mock_identity
    mock_store.authenticate.return_value = mock_identity

    receiver = IOSVideoReceiver(mock_store)

    cred3 = "test_camera_credential_32_chars_3"
    peer = _Peer(
        credential=cred3,
        identity={
            "camera_id": "cam_test_3",
            "account_id": "user_test",
            "session_id": "sess_test"
        },
        pc=MagicMock()
    )
    peer.remote_description_installed = True
    receiver._peers["cam_test_3"] = peer

    # None candidate
    await receiver.add_ice_candidate(cred3, None)

    # Empty string candidate
    await receiver.add_ice_candidate(cred3, {"candidate": "", "sdpMid": "0"})
    assert True
