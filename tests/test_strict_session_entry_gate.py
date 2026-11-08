"""
Tests for Strict Session Entry Gate:
Windows EXE must strictly prohibit starting or continuing the Strict Session
unless Windows has actually received, decoded, and verified live incoming video frames from iOS.
"""

import time
import pytest
from core.database import StrictRecordsDB
from windows.strict_session_manager import StrictSessionManager, StrictSessionState


class DummyGateway:
    def __init__(self, streaming=False, age=0.0):
        self.ready = True
        self.origin = "https://camera.example.trycloudflare.com"
        self._streaming = streaming
        self._age = age
        self.verification_details = {
            "hostname": "camera.example.trycloudflare.com",
            "dns_resolved": True,
            "https_reachable": True,
            "companion_route_ok": True,
            "wss_signaling_ok": True,
            "error": "",
        }

    def preview(self, account, pid):
        now = time.time()
        last_frame = (now - self._age) if self._streaming else None
        return {
            "status": {
                "streaming": self._streaming,
                "state": "IOS_CAMERA_STREAMING" if self._streaming else "IOS_CAMERA_STOPPED",
                "frame_sequence": 15 if self._streaming else 0,
                "last_frame_at": last_frame,
            },
            "preview": {
                "source": "iPhone camera",
                "observed_at": last_frame,
                "jpeg": "dGVzdF9qcGVn" if self._streaming else None,
            },
            "tls_verified": True,
            "source": "iPhone camera",
        }


def test_session_start_strictly_blocked_when_ios_stream_not_confirmed(tmp_path):
    """Session start must fail if Windows EXE has not confirmed live video feed from iOS."""
    db = StrictRecordsDB(str(tmp_path / "test_entry_gate.db"))
    manager = StrictSessionManager(db=db, data_dir=str(tmp_path))

    # Configure dummy gateway with NO active streaming
    manager.ios_camera_gateway = DummyGateway(streaming=False)

    prep = manager.preparation.current(manager.user_id)
    pid = prep["preparation_id"]

    # Pair iOS companion
    invite = manager.preparation.invite(manager.user_id, "IOS", "https://camera.example.trycloudflare.com")
    token = invite["invitation"]["url"].split("#")[1]
    join_res = manager.preparation.join(token, "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15")

    # Send companion heartbeat
    manager.preparation.heartbeat(
        pid, "IOS", join_res["credential"],
        {"timer_ready": True, "visible": True, "online": True}
    )

    # Attempt to start session without confirmed iOS camera feed
    res = manager.start_session(
        preparation_id=pid,
        monitoring_consent=True,
    )

    # Must be rejected!
    assert res["success"] is False
    assert "Cannot enter Strict Session" in res["error"]
    assert "video" in res["error"] or "Camera" in res["error"] or "iOS" in res["error"] or "iPhone" in res["error"]
    assert manager.state == StrictSessionState.NORMAL


def test_session_start_allowed_once_ios_camera_feed_is_confirmed(tmp_path, monkeypatch):
    """Session start succeeds once Windows EXE confirms live video feed from iOS."""
    db = StrictRecordsDB(str(tmp_path / "test_entry_gate2.db"))
    manager = StrictSessionManager(db=db, data_dir=str(tmp_path))

    # Configure gateway with ACTIVE streaming (fresh frames < 2s ago)
    manager.ios_camera_gateway = DummyGateway(streaming=True, age=0.5)

    # Register host report & pass mock preflight
    prep = manager.preparation.current(manager.user_id)
    pid = prep["preparation_id"]

    # Pair iOS companion
    invite = manager.preparation.invite(manager.user_id, "IOS", "https://camera.example.trycloudflare.com")
    token = invite["invitation"]["url"].split("#")[1]
    join_res = manager.preparation.join(token, "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15")

    # Companion heartbeat
    manager.preparation.heartbeat(
        pid, "IOS", join_res["credential"],
        {"timer_ready": True, "visible": True, "online": True}
    )

    manager.preparation.host_report(
        manager.user_id,
        pid,
        {
            "camera": True,
            "microphone": False,
            "visible": True,
            "secure_context": True,
            "online": True,
            "camera_native_id": "0000000000000000000000000000000000000000000000000000000000000001",
        },
    )

    # Mock environment to ready
    manager.preparation.environment = lambda refresh=False: {
        "ready": True,
        "mode": "DEMO",
        "internet": True,
        "secure_browser": True,
        "allowlist_configured": True,
        "hardware_required": False,
    }

    # Verify authorize_start succeeds
    state = manager.preparation.authorize_start(manager.user_id, pid)
    assert state is not None
    assert state["preparation_id"] == pid
    assert state["devices"]["IOS"]["camera_ready"] is True
