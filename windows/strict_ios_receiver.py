"""Authenticated, video-only iOS WebRTC receiver.

Media is decoded in memory and optionally handed to a separate StrictGuard input.
This module never records frames or substitutes the Windows webcam.  aiortc is
loaded lazily so an unconfigured optional iOS camera cannot prevent EXE startup.
"""
from __future__ import annotations

import asyncio
import collections
import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class IOSReceiverUnavailable(RuntimeError):
    pass


def _load_runtime():
    try:
        from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
        from aiortc.sdp import candidate_from_sdp
    except ImportError as error:
        raise IOSReceiverUnavailable(
            "iOS WebRTC receiver unavailable: install the optional aiortc runtime before enabling camera streaming."
        ) from error
    return RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, candidate_from_sdp


def validate_offer(sdp: str) -> str:
    """Reject data/audio and oversized signaling before invoking the SDP parser."""
    if not isinstance(sdp, str) or not sdp or len(sdp.encode("utf-8")) > 65536:
        raise ValueError("A video-only SDP offer of at most 64 KiB is required.")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in sdp):
        raise ValueError("Invalid SDP control characters.")
    lines = sdp.replace("\r\n", "\n").split("\n")
    if any(len(line) > 4096 for line in lines):
        raise ValueError("SDP line is too long.")
    media = [line.split() for line in lines if line.startswith("m=")]
    if not (1 <= len(media) <= 2) or any(m[0] != "m=video" or len(m) < 4 for m in media):
        raise ValueError("Video media sections (up to two: camera and screen share) are permitted; audio and data are forbidden.")
    for m in media:
        if m[1] == "0" or m[2] != "UDP/TLS/RTP/SAVPF":
            raise ValueError("An active encrypted WebRTC video track is required.")
    if any(line in {"a=recvonly", "a=inactive"} for line in lines):
        raise ValueError("The iPhone must send a video track.")
    if not any(line.startswith("a=fingerprint:sha-256 ") for line in lines):
        raise ValueError("A DTLS SHA-256 fingerprint is required.")
    if not any(line.startswith("a=ice-ufrag:") for line in lines) or not any(line.startswith("a=ice-pwd:") for line in lines):
        raise ValueError("WebRTC ICE credentials are required.")
    if sum(line.startswith("a=candidate:") for line in lines) > 128:
        raise ValueError("Too many ICE candidates.")
    return sdp


@dataclass
class _Peer:
    credential: str = field(repr=False)
    identity: dict
    pc: Any
    tasks: set = field(default_factory=set)
    tracks: list = field(default_factory=list)
    track_sources: dict = field(default_factory=dict)
    sequence: int = 0
    last_frame_at: float | None = None
    last_delivered_at: float | None = None
    candidate_count: int = 0
    closed: bool = False
    frame_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    screen_track: Any = None
    screen_sequence: int = 0
    screen_last_frame_at: float | None = None
    screen_last_delivered_at: float | None = None
    screen_frame_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    connection_id: str = ""
    tx_id: str = ""
    connection_generation: int = 1
    pending_candidates: list = field(default_factory=list)
    remote_description_installed: bool = False
    is_screen: bool = False

    # Distinct counters across 4 layers
    frames_received_count: int = 0
    frames_decoded_count: int = 0
    frames_queued_count: int = 0
    frames_processed_count: int = 0
    frames_dropped_count: int = 0

    screen_frames_received_count: int = 0
    screen_frames_decoded_count: int = 0
    screen_frames_queued_count: int = 0
    screen_frames_processed_count: int = 0
    screen_frames_dropped_count: int = 0

    # Rate tracking over 2.0s sliding window
    rx_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))
    decoded_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))
    processed_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))

    screen_rx_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))
    screen_decoded_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))
    screen_processed_timestamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=60))

    last_pli_sent_at: float = 0.0

    # 4-layer health diagnostics
    layer_diagnostics: dict = field(default_factory=lambda: {
        "signaling": {"state": "NEW", "tx_id": "", "generation": 1},
        "transport": {
            "ice_state": "new", "dtls_state": "new", "selected_pair": "none",
            "packets_received": 0, "packets_lost": 0, "jitter": 0.0, "rtt_ms": 0.0,
            "bytes_received": 0, "bitrate_bps": 0
        },
        "media": {
            "camera_track_state": "off", "screen_track_state": "off",
            "rx_fps": 0.0, "decoded_fps": 0.0, "dropped_frames": 0,
            "last_decoded_frame_ts": 0.0, "resolution": "none"
        },
        "processing": {
            "camera_queue_depth": 0, "screen_queue_depth": 0,
            "processed_fps": 0.0, "latency_ms": 0.0, "worker_health": "IDLE"
        }
    })

    def calculate_fps(self, timestamps_deque, now: float | None = None) -> float:
        t = time.time() if now is None else now
        valid = [ts for ts in timestamps_deque if 0.0 <= (t - ts) <= 2.0]
        if not valid:
            return 0.0
        if len(valid) == 1:
            return 1.0
        dt = max(0.1, valid[-1] - valid[0]) if len(valid) > 1 else 1.0
        return round((len(valid) - 1) / dt, 1)


class IOSVideoReceiver:
    """One authenticated receive-only peer per camera registration.

    ``store.authenticate`` verifies public signaling operations. The internal
    ``receiver_identity`` checks registration/session/expiry for each frame, so
    credential rotation need not interrupt an already authenticated media peer.
    Only decoded ``track.recv`` frames call ``store.receiver_frame_by_id``.
    Signaling/ICE connection alone cannot set STREAMING.
    The optional callback receives (identity, BGR ndarray), without disk storage.
    All public methods run on the same asyncio event loop as the WSS service.
    """

    def __init__(self, store, on_frame: Callable | None = None, on_stage_ack: Callable | None = None, on_keyframe_needed: Callable | None = None, ice_servers=None):
        self.store = store
        self.on_frame = on_frame
        self.on_stage_ack = on_stage_ack
        self.on_keyframe_needed = on_keyframe_needed
        self.ice_servers = list(ice_servers or [])
        self._peers: dict[str, _Peer] = {}
        self._screen_peers: dict[str, _Peer] = {}
        self._frame_sequences: dict[str, int] = {}
        self._runtime = None
        self._clock = time.monotonic
        self.stall_seconds = 15.0
        self.max_fps = 15.0
        self._closed = False

    def _identity(self, credential):
        if not isinstance(credential, str) or not 32 <= len(credential) <= 512:
            raise PermissionError("Invalid camera credential.")
        identity = self.store.authenticate(credential)
        if not isinstance(identity, dict) or not identity.get("camera_id") or not identity.get("session_id"):
            raise PermissionError("Camera identity is unavailable.")
        return dict(identity)

    def _configuration(self):
        _, _, configuration, server, _ = self._runtime
        values = []
        if len(self.ice_servers) > 8:
            raise ValueError("At most eight configured ICE servers are permitted.")
        for item in self.ice_servers:
            if not isinstance(item, dict):
                raise ValueError("ICE server configuration must be a mapping.")
            urls = item.get("urls", [])
            urls = [urls] if isinstance(urls, str) else urls
            if not isinstance(urls, list) or not 1 <= len(urls) <= 8:
                raise ValueError("Invalid ICE server URLs.")
            if any(not isinstance(url, str) or len(url) > 1024 or not re.match(r"^(stun|stuns|turn|turns):[^\s]+$", url) for url in urls):
                raise ValueError("Only configured STUN/TURN server URLs are permitted.")
            if any(url.startswith(("turn:", "turns:")) for url in urls) and not (item.get("username") and item.get("credential")):
                raise ValueError("TURN requires authenticated, temporary credentials.")
            values.append(server(urls=urls, username=item.get("username"), credential=item.get("credential")))
        return configuration(iceServers=values)

    def _task(self, peer, coroutine):
        task = asyncio.create_task(coroutine)
        peer.tasks.add(task)
        task.add_done_callback(peer.tasks.discard)
        return task

    def _state(self, peer, state, diagnostics=None):
        try:
            self.store.client_state(peer.credential, state, diagnostics or {})
        except (PermissionError, ValueError):
            # Revoked/expired sessions must not be revived by stale media events.
            return False
        return True

    async def emit_stage_ack(self, peer, stage: str, source_id: str = "IOS_FRONT_CAMERA", frame_seq: int = 0):
        if not self.on_stage_ack:
            return
        try:
            is_screen = source_id in ("IOS_SCREEN_SHARE", "IOS_FULL_SCREEN_SHARE")
            rx_ts = peer.screen_rx_timestamps if is_screen else peer.rx_timestamps
            dec_ts = peer.screen_decoded_timestamps if is_screen else peer.decoded_timestamps
            proc_ts = peer.screen_processed_timestamps if is_screen else peer.processed_timestamps

            payload = {
                "type": "IOS_CAMERA_FRAME_ACK",
                "session_id": peer.identity.get("session_id", ""),
                "connection_generation": getattr(peer, "connection_generation", 1),
                "connection_id": peer.connection_id,
                "source_id": source_id,
                "frame_sequence": frame_seq or (peer.screen_sequence if is_screen else peer.sequence),
                "timestamp": time.time(),
                "stage": stage,
                "current_fps": {
                    "received_fps": peer.calculate_fps(rx_ts),
                    "decoded_fps": peer.calculate_fps(dec_ts),
                    "processed_fps": peer.calculate_fps(proc_ts),
                }
            }
            if inspect.iscoroutinefunction(self.on_stage_ack):
                await self.on_stage_ack(peer.identity["camera_id"], payload)
            else:
                self.on_stage_ack(peer.identity["camera_id"], payload)

            try:
                from windows.strict_ios_media_health import IOSMediaHealthManager
                IOSMediaHealthManager.get_instance().record_receiver_stats(
                    source_id=source_id,
                    rx_fps=peer.calculate_fps(rx_ts),
                    rx_frames_count=peer.screen_frames_received_count if is_screen else peer.frames_received_count,
                    decoded_fps=peer.calculate_fps(dec_ts),
                    decoded_frames_count=peer.screen_sequence if is_screen else peer.sequence,
                    processing_fps=peer.calculate_fps(proc_ts),
                    processed_frames_count=peer.screen_processed_count if is_screen else getattr(peer, "frames_processed_count", 0),
                    last_rx_epoch=rx_ts[-1] if rx_ts else None,
                    last_decoded_epoch=dec_ts[-1] if dec_ts else None,
                    last_processed_epoch=proc_ts[-1] if proc_ts else None,
                    webrtc_connected=getattr(peer.pc, "connectionState", "new") in ("connected", "completed"),
                )
            except Exception:
                pass
        except Exception:
            pass

    async def offer(self, camera_credential, sdp, tx_id=None, connection_id=None, source_id=None, renegotiate=False):
        if self._closed:
            raise IOSReceiverUnavailable("iOS camera receiver has stopped.")
        identity = self._identity(camera_credential)
        validate_offer(sdp)
        if self._runtime is None:
            self._runtime = _load_runtime()
        factory, description, _, _, _ = self._runtime
        configuration = self._configuration()
        is_screen_peer = source_id in ("IOS_SCREEN_SHARE", "IOS_FULL_SCREEN_SHARE")
        peer_dict = self._screen_peers if is_screen_peer else self._peers
        existing = peer_dict.get(identity["camera_id"])

        is_renegotiation = False
        if existing and not existing.closed:
            conn_matches = bool(connection_id and connection_id == existing.connection_id)
            pc_alive = getattr(existing.pc, "connectionState", "new") not in ("failed", "closed")
            if (renegotiate or conn_matches) and pc_alive:
                is_renegotiation = True
                peer = existing
                pc = existing.pc
                peer.tx_id = str(tx_id or "")

        if not is_renegotiation:
            if existing:
                await self._close_peer(existing, report=False)
            pc = factory(configuration=configuration)
            peer = _Peer(camera_credential, identity, pc)
            peer.connection_generation = (existing.connection_generation + 1) if existing else 1
            peer.is_screen = is_screen_peer
            if is_screen_peer:
                peer.screen_sequence = 0
            else:
                peer.sequence = max(int(identity.get("frame_sequence", 0)), self._frame_sequences.get(identity["camera_id"], 0))
            peer_dict[identity["camera_id"]] = peer

        @pc.on("track")
        def on_track(track):
            if track.kind != "video" or len(peer.tracks) >= 2:
                track.stop()
                if not peer.tracks:
                    self._state(peer, "IOS_CAMERA_INTERRUPTED", {"error_name": "UnexpectedMediaTrack"})
                    self._task(peer, self._close_peer(peer, report=False))
                return

            is_screen = False
            track_id = getattr(track, 'id', None)
            src = peer.track_sources.get(track_id) if track_id else None
            if peer.is_screen or src in ("IOS_SCREEN_SHARE", "IOS_FULL_SCREEN_SHARE"):
                is_screen = True
            elif hasattr(track, "label") and any(k in str(track.label).lower() for k in ("screen", "display")):
                is_screen = True
            elif peer.tracks:  # If camera track already registered, 2nd track is screen share
                is_screen = True

            peer.tracks.append(track)
            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                IPadCameraStreamMonitor.get_instance().record_detailed_stage(
                    "19_incoming_video_track_windows",
                    session_id=peer.identity.get("session_id", ""),
                    connection_id=peer.connection_id,
                    source_id="IOS_FULL_SCREEN_SHARE" if is_screen else "IOS_FRONT_CAMERA",
                    success=True
                )
            except Exception:
                pass

            if is_screen:
                peer.screen_track = track
                try:
                    from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
                    IPadScreenShareMonitor.get_instance().start_track(
                        getattr(track, 'id', 'screen_track'),
                        preparation_id=peer.identity.get("preparation_id"),
                    )
                except Exception:
                    pass
                self._task(peer, self._consume_screen(peer, track))
            else:
                self._task(peer, self._consume(peer, track))

        @pc.on("connectionstatechange")
        async def on_connection_state():
            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                state_str = str(pc.connectionState).upper()
                ice_str = str(getattr(pc, "iceConnectionState", "UNKNOWN")).upper()
                IPadCameraStreamMonitor.get_instance().record_connection_status(
                    device_connected=state_str in {"CONNECTED", "CONNECTING"},
                    https_verified=True,
                    webrtc_state=state_str,
                    ice_state=ice_str,
                    track_received=bool(peer.tracks),
                )
            except Exception:
                pass
            if not peer.closed and pc.connectionState in {"failed", "disconnected", "closed"}:
                self._state(peer, "IOS_RECONNECTING", {"peer_connection": pc.connectionState})

        @pc.on("iceconnectionstatechange")
        async def on_ice_state():
            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                ice_str = str(getattr(pc, "iceConnectionState", "UNKNOWN")).upper()
                state_str = str(pc.connectionState).upper()
                IPadCameraStreamMonitor.get_instance().record_connection_status(
                    device_connected=state_str in {"CONNECTED", "CONNECTING"},
                    https_verified=True,
                    webrtc_state=state_str,
                    ice_state=ice_str,
                    track_received=bool(peer.tracks),
                )
            except Exception:
                pass

        try:
            self._state(peer, "IOS_WEBRTC_NEGOTIATING")
            peer.connection_id = str(connection_id or "")
            peer.tx_id = str(tx_id or "")

            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                mon = IPadCameraStreamMonitor.get_instance()
                mon.record_detailed_stage(
                    "8_sdp_offer_delivery_windows",
                    session_id=identity["session_id"],
                    connection_id=peer.connection_id,
                    success=True
                )
            except Exception:
                pass

            await pc.setRemoteDescription(description(sdp=sdp, type="offer"))
            peer.remote_description_installed = True

            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                mon = IPadCameraStreamMonitor.get_instance()
                mon.record_detailed_stage(
                    "9_remote_description_installation_windows",
                    session_id=identity["session_id"],
                    connection_id=peer.connection_id,
                    success=True
                )
            except Exception:
                pass

            # Apply any candidates that arrived while offer was being set up
            if peer.pending_candidates:
                for cand in list(peer.pending_candidates):
                    try:
                        await peer.pc.addIceCandidate(cand)
                    except Exception:
                        pass
                peer.pending_candidates.clear()

            for transceiver in pc.getTransceivers():
                if transceiver.kind != "video":
                    raise ValueError("Only video transceivers are permitted.")
                transceiver.direction = "recvonly"

            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Wait with bounded timeout (max 1.2s) for local ICE gathering to complete
            gather_start = time.monotonic()
            while getattr(pc, "iceGatheringState", "complete") != "complete" and (time.monotonic() - gather_start) < 1.2:
                await asyncio.sleep(0.04)

            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                mon = IPadCameraStreamMonitor.get_instance()
                mon.record_detailed_stage(
                    "10_sdp_answer_generation",
                    session_id=identity["session_id"],
                    connection_id=peer.connection_id,
                    success=True
                )
                mon.record_detailed_stage(
                    "13_local_ice_candidate_gathering",
                    session_id=identity["session_id"],
                    connection_id=peer.connection_id,
                    success=True
                )
            except Exception:
                pass

            self._identity(camera_credential)  # Do not return credentials after session termination.
            self._state(peer, "IOS_VIDEO_CONNECTING")
            if not is_renegotiation:
                self._task(peer, self._poll_stats(peer))
                if self.on_frame:
                    self._task(peer, self._deliver_frames(peer))
            return {
                "type": "answer",
                "sdp": pc.localDescription.sdp,
                "camera_id": identity["camera_id"],
                "session_id": identity["session_id"],
                "tx_id": peer.tx_id,
                "connection_id": peer.connection_id,
                "connection_generation": peer.connection_generation,
                "source_id": "IOS_SCREEN_SHARE" if peer.is_screen else "IOS_FRONT_CAMERA",
            }
        except Exception:
            await self._close_peer(peer, report=False)
            raise

    async def add_ice_candidate(self, camera_credential, candidate, tx_id=None, connection_id=None, source_id=None):
        identity = self._identity(camera_credential)
        is_screen_peer = source_id in ("IOS_SCREEN_SHARE", "IOS_FULL_SCREEN_SHARE")
        peer_dict = self._screen_peers if is_screen_peer else self._peers
        peer = peer_dict.get(identity["camera_id"]) or self._peers.get(identity["camera_id"]) or self._screen_peers.get(identity["camera_id"])
        if not peer or peer.closed or peer.credential != camera_credential:
            return
        if candidate is None or candidate == {"candidate": ""} or candidate == "":
            try:
                await peer.pc.addIceCandidate(None)
            except Exception:
                pass
            return
        STANDARD_CANDIDATE_KEYS = {
            "candidate", "sdpMid", "sdpMLineIndex", "usernameFragment",
            "foundation", "component", "priority", "address", "protocol",
            "port", "type", "tcpType", "relatedAddress", "relatedPort"
        }
        if not isinstance(candidate, dict) or (set(candidate.keys()) - STANDARD_CANDIDATE_KEYS):
            raise ValueError("Invalid candidate format.")
        value = candidate.get("candidate")
        if not value or not isinstance(value, str) or value == "":
            try:
                await peer.pc.addIceCandidate(None)
            except Exception:
                pass
            return
        if len(value) > 4096 or "\r" in value or "\n" in value:
            raise ValueError("Invalid candidate content.")
        mid = candidate.get("sdpMid")
        index = candidate.get("sdpMLineIndex")
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int) or index != 0:
                raise ValueError("Invalid sdpMLineIndex.")

        if peer.candidate_count >= 128:
            raise ValueError("Too many ICE candidates.")

        if self._runtime is None:
            self._runtime = _load_runtime()

        try:
            cand_str = value[len("candidate:"):] if value.startswith("candidate:") else value
            parsed = self._runtime[4](cand_str)
            parsed.sdpMid = mid
            parsed.sdpMLineIndex = index
            peer.candidate_count += 1

            has_remote = bool(getattr(peer, "remote_description_installed", False))
            if not has_remote:
                peer.pending_candidates.append(parsed)
            else:
                await peer.pc.addIceCandidate(parsed)
                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    IPadCameraStreamMonitor.get_instance().record_detailed_stage(
                        "15_remote_ice_candidate_installation",
                        session_id=identity.get("session_id", ""),
                        connection_id=peer.connection_id,
                        success=True
                    )
                except Exception:
                    pass
        except Exception as e:
            try:
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                IPadCameraStreamMonitor.get_instance().record_detailed_stage(
                    "15_remote_ice_candidate_installation",
                    session_id=identity.get("session_id", ""),
                    connection_id=peer.connection_id,
                    success=False,
                    error_message=str(e)
                )
            except Exception:
                pass

    async def reauthorize(self, camera_credential):
        """Bind a WSS-authenticated rotated credential to its existing media peer."""
        identity = self._identity(camera_credential)
        peer = self._peers.get(identity["camera_id"])
        if peer and not peer.closed:
            if peer.identity["session_id"] != identity["session_id"]:
                raise PermissionError("Camera session identity changed.")
            peer.credential = camera_credential
            peer.identity = identity
        return {"camera_id": identity["camera_id"], "session_id": identity["session_id"]}

    async def _consume(self, peer, track):
        pending = None
        stalled = False
        try:
            while not peer.closed:
                self.store.receiver_identity(peer.identity["camera_id"])
                if pending is None:
                    pending = asyncio.create_task(track.recv())
                done, _ = await asyncio.wait({pending}, timeout=self.stall_seconds)
                if not done:
                    if not stalled:
                        self._state(peer, "IOS_CAMERA_INTERRUPTED", {"error_name": "VideoFrameStall"})
                        stalled = True
                    continue
                frame = pending.result()
                pending = None
                self.store.receiver_identity(peer.identity["camera_id"])
                width, height = frame.width, frame.height
                if not isinstance(width, int) or not isinstance(height, int) or not 1 <= width <= 4096 or not 1 <= height <= 4096:
                    raise ValueError("Invalid decoded video dimensions.")
                now = self._clock()
                now_epoch = time.time()
                peer.frames_received_count += 1
                peer.rx_timestamps.append(now_epoch)

                if peer.frames_received_count == 1 or peer.frames_received_count % 30 == 0:
                    self._task(peer, self.emit_stage_ack(peer, "RTP_RECEIVING", "IOS_FRONT_CAMERA", peer.sequence))

                # Decode every received track frame, but bound analysis/preview work.
                if peer.last_delivered_at is not None and now - peer.last_delivered_at < 1 / self.max_fps:
                    peer.frames_dropped_count += 1
                    continue
                scale = min(1.0, 640 / width, 480 / height)
                pixels = frame.reformat(width=max(1, int(width * scale)), height=max(1, int(height * scale))).to_ndarray(format="bgr24")
                peer.last_delivered_at = peer.last_frame_at = now
                peer.sequence += 1
                peer.frames_decoded_count += 1
                peer.decoded_timestamps.append(now_epoch)

                if peer.frames_decoded_count == 1 or peer.frames_decoded_count % 30 == 0:
                    self._task(peer, self.emit_stage_ack(peer, "FRAME_DECODED", "IOS_FRONT_CAMERA", peer.sequence))

                self.store.receiver_frame_by_id(peer.identity["camera_id"], peer.sequence)
                self._frame_sequences[peer.identity["camera_id"]] = peer.sequence
                stalled = False

                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    mon = IPadCameraStreamMonitor.get_instance()
                    mon.record_detailed_stage(
                        "20_rtp_packet_reception",
                        session_id=peer.identity.get("session_id", ""),
                        connection_id=peer.connection_id,
                        success=True
                    )
                    mon.record_detailed_stage(
                        "21_video_frame_assembly_decoding",
                        session_id=peer.identity.get("session_id", ""),
                        connection_id=peer.connection_id,
                        success=True
                    )
                except Exception:
                    pass

                if self.on_frame:
                    if peer.frame_queue.full():
                        try:
                            peer.frame_queue.get_nowait()
                            peer.frames_dropped_count += 1
                        except asyncio.QueueEmpty:
                            pass
                    peer.frames_queued_count += 1
                    peer.frame_queue.put_nowait(pixels)
                    if peer.frames_queued_count == 1 or peer.frames_queued_count % 30 == 0:
                        self._task(peer, self.emit_stage_ack(peer, "FRAME_QUEUED", "IOS_FRONT_CAMERA", peer.sequence))
                    try:
                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                        IPadCameraStreamMonitor.get_instance().record_detailed_stage(
                            "22_windows_frame_queue_insertion",
                            session_id=peer.identity.get("session_id", ""),
                            connection_id=peer.connection_id,
                            success=True
                        )
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except (PermissionError, ValueError):
            self._state(peer, "IOS_CAMERA_INTERRUPTED", {"error_name": "CameraAuthorizationOrFrameError"})
        except Exception:
            self._state(peer, "IOS_CAMERA_INTERRUPTED", {"error_name": "VideoTrackEnded"})
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            track.stop()
            if track in peer.tracks:
                peer.tracks.remove(track)
            if not peer.tracks and not peer.closed:
                await self._close_peer(peer, report=False)

    async def _consume_screen(self, peer, track):
        """Processes incoming full-device iPad screen sharing frames."""
        pending = None
        stalled = False
        try:
            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
            screen_mon = IPadScreenShareMonitor.get_instance()
            from windows.strict_dual_camera_proctor import StrictDualCameraProctor
            dual_proctor = StrictDualCameraProctor.get_instance()

            while not peer.closed:
                try:
                    pending = asyncio.create_task(track.recv())
                    done, _ = await asyncio.wait({pending}, timeout=self.stall_seconds)
                    if not done:
                        if not stalled:
                            screen_mon.check_watchdog()
                            stalled = True
                        continue
                    frame = pending.result()
                    pending = None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    break

                width, height = frame.width, frame.height
                if not isinstance(width, int) or not isinstance(height, int) or not 1 <= width <= 4096 or not 1 <= height <= 4096:
                    continue

                now = self._clock()
                now_epoch = time.time()
                screen_mon.record_received_frame(now_epoch)

                # Bound processing rate to ~15 FPS max
                if peer.screen_last_delivered_at is not None and now - peer.screen_last_delivered_at < 1 / self.max_fps:
                    continue

                scale = min(1.0, 1280 / width, 720 / height)
                pixels = frame.reformat(width=max(1, int(width * scale)), height=max(1, int(height * scale))).to_ndarray(format="bgr24")
                peer.screen_last_delivered_at = peer.screen_last_frame_at = now
                peer.screen_sequence += 1
                stalled = False

                # Route to dedicated screen monitor and proctor (strictly IOS_FULL_SCREEN_SHARE)
                screen_mon.process_frame(pixels, timestamp_epoch=now_epoch, source_id="IOS_FULL_SCREEN_SHARE")
                dual_proctor.submit_ios_screen_frame(pixels, timestamp=now_epoch)

        except asyncio.CancelledError:
            raise
        except Exception as err:
            pass
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            track.stop()
            try:
                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor
                IPadScreenShareMonitor.get_instance().stop_track()
            except Exception:
                pass
            if track in peer.tracks:
                peer.tracks.remove(track)
            peer.screen_track = None
            if not peer.tracks and not peer.closed:
                await self._close_peer(peer, report=False)

    def set_track_sources(self, camera_credential, sources: dict) -> None:
        """Signaled mapping of WebRTC track IDs to source roles."""
        if not isinstance(sources, dict):
            return
        try:
            camera_id = camera_credential
            if camera_credential not in self._peers and camera_credential not in self._screen_peers:
                identity = self._identity(camera_credential)
                camera_id = identity["camera_id"]
            for peer_dict in (self._peers, self._screen_peers):
                peer = peer_dict.get(camera_id)
                if peer:
                    peer.track_sources.update(sources)
        except Exception:
            pass

    async def _poll_stats(self, peer):
        """Periodically polls RTCPeerConnection.getStats() on Windows (~every 2s) without recreating peers."""
        while not peer.closed:
            await asyncio.sleep(2.0)
            if peer.closed or not hasattr(peer.pc, "getStats"):
                break
            try:
                report = await peer.pc.getStats()
                stats_payload = {
                    "packetsReceived": 0,
                    "bytesReceived": 0,
                    "packetsLost": 0,
                    "jitter": 0.0,
                    "framesReceived": peer.sequence,
                    "framesDecoded": peer.sequence,
                    "framesDropped": 0,
                    "iceConnectionState": str(getattr(peer.pc, "iceConnectionState", "CONNECTED")).upper(),
                }
                values = report.values() if hasattr(report, "values") else []
                candidates = {}
                for item in values:
                    stype = getattr(item, "type", "")
                    if stype == "inbound-rtp" and getattr(item, "kind", "video") == "video":
                        stats_payload["packetsReceived"] = int(getattr(item, "packetsReceived", 0) or 0)
                        stats_payload["bytesReceived"] = int(getattr(item, "bytesReceived", 0) or 0)
                        stats_payload["packetsLost"] = int(getattr(item, "packetsLost", 0) or 0)
                        stats_payload["jitter"] = float(getattr(item, "jitter", 0.0) or 0.0)
                    elif stype in ("local-candidate", "remote-candidate"):
                        cid = getattr(item, "id", None)
                        if cid:
                            candidates[cid] = getattr(item, "candidateType", getattr(item, "type", "unknown"))
                    elif stype == "candidate-pair" and getattr(item, "state", "") == "succeeded":
                        stats_payload["candidatePairState"] = "succeeded"
                        rtt = getattr(item, "currentRoundTripTime", None)
                        if rtt is not None:
                            stats_payload["rttMs"] = round(float(rtt) * 1000.0, 1)
                        loc_id = getattr(item, "localCandidateId", None)
                        rem_id = getattr(item, "remoteCandidateId", None)
                        if loc_id in candidates:
                            stats_payload["localCandidateType"] = str(candidates[loc_id])
                        if rem_id in candidates:
                            stats_payload["remoteCandidateType"] = str(candidates[rem_id])

                        try:
                            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                            mon = IPadCameraStreamMonitor.get_instance()
                            mon.record_detailed_stage(
                                "16_ice_connectivity_checks",
                                session_id=peer.identity.get("session_id", ""),
                                connection_id=peer.connection_id,
                                success=True
                            )
                            mon.record_detailed_stage(
                                "17_ice_selected_candidate_pair",
                                session_id=peer.identity.get("session_id", ""),
                                connection_id=peer.connection_id,
                                ice_state=f"{stats_payload.get('localCandidateType')} -> {stats_payload.get('remoteCandidateType')}",
                                success=True
                            )
                            mon.record_detailed_stage(
                                "18_dtls_connection",
                                session_id=peer.identity.get("session_id", ""),
                                connection_id=peer.connection_id,
                                success=True
                            )
                        except Exception:
                            pass
                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                IPadCameraStreamMonitor.get_instance().record_windows_receiver_stats(stats_payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _deliver_frames(self, peer):
        while not peer.closed:
            pixels = await peer.frame_queue.get()
            try:
                if inspect.iscoroutinefunction(self.on_frame):
                    await self.on_frame(dict(peer.identity), pixels)
                else:
                    await asyncio.to_thread(self.on_frame, dict(peer.identity), pixels)
                peer.frames_processed_count += 1
                peer.processed_timestamps.append(time.time())
                if peer.frames_processed_count == 1 or peer.frames_processed_count % 30 == 0:
                    self._task(peer, self.emit_stage_ack(peer, "FRAME_PROCESSED", "IOS_FRONT_CAMERA", peer.sequence))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # A processing failure does NOT mean the encrypted camera vanished.
                # Report the processing error to the inference health monitor separately.
                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    IPadCameraStreamMonitor.get_instance().record_processing_error(type(err).__name__)
                except Exception:
                    pass

    async def _close_peer(self, peer, *, report=True):
        if peer.closed:
            return
        peer.closed = True
        if self._peers.get(peer.identity["camera_id"]) is peer:
            self._peers.pop(peer.identity["camera_id"], None)
        if self._screen_peers.get(peer.identity["camera_id"]) is peer:
            self._screen_peers.pop(peer.identity["camera_id"], None)
        if self._screen_peers.get(peer.identity["camera_id"]) is peer:
            self._screen_peers.pop(peer.identity["camera_id"], None)
        for track in peer.tracks:
            track.stop()
        current = asyncio.current_task()
        tasks = [task for task in peer.tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await peer.pc.close()
        if report:
            self._state(peer, "IOS_CAMERA_STOPPED")

    async def stop(self, camera_credential, *, report=True):
        identity = self._identity(camera_credential)
        peer = self._peers.get(identity["camera_id"])
        if peer and peer.credential == camera_credential:
            await self._close_peer(peer, report=report)

    async def stop_camera(self, camera_id, *, report=False):
        """Trusted gateway cleanup, including expired or already revoked grants.

        This method is server-internal: never dispatch untrusted camera IDs to it.
        The WSS owner calls it on termination even when credential auth now fails.
        """
        peer = self._peers.get(camera_id)
        if peer:
            await self._close_peer(peer, report=report)

    async def stop_session(self, session_id):
        for peer in list(self._peers.values()) + list(self._screen_peers.values()):
            if peer.identity["session_id"] == session_id:
                await self._close_peer(peer)

    async def close(self):
        self._closed = True
        for peer in list(self._peers.values()) + list(self._screen_peers.values()):
            await self._close_peer(peer)

