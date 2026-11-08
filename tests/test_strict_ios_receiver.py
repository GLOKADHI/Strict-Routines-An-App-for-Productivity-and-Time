"""Receiver security/lifecycle tests without cameras, recordings or aiortc install."""
import asyncio
from types import SimpleNamespace

import pytest

import windows.strict_ios_receiver as module


CREDENTIAL = "c" * 64
SDP = "\r\n".join([
    "v=0", "o=- 1 1 IN IP4 127.0.0.1", "s=-", "t=0 0",
    "m=video 9 UDP/TLS/RTP/SAVPF 96", "c=IN IP4 0.0.0.0", "a=mid:0",
    "a=sendonly", "a=ice-ufrag:abcdefgh", "a=ice-pwd:abcdefghijklmnopqrstuvwx",
    "a=fingerprint:sha-256 " + ":".join(["AA"] * 32),
    "a=setup:actpass", "a=rtcp-mux", "a=rtpmap:96 VP8/90000", "",
])


class Store:
    def __init__(self):
        self.valid = True
        self.credential = CREDENTIAL
        self.frames = []
        self.states = []

    def authenticate(self, credential):
        if not self.valid or credential != self.credential:
            raise PermissionError("not authorized")
        return dict(camera_id="cam1", account_id="user1", preparation_id="prep1", session_id="session1",
                    frame_sequence=self.frames[-1] if self.frames else 0)

    def receiver_frame(self, credential, seq):
        self.authenticate(credential)
        if self.frames and seq <= self.frames[-1]:
            raise ValueError("replayed sequence")
        self.frames.append(seq)
        self.states.append("IOS_CAMERA_STREAMING")

    def receiver_identity(self, camera_id):
        assert camera_id == "cam1"
        return self.authenticate(self.credential)

    def receiver_frame_by_id(self, camera_id, seq):
        assert camera_id == "cam1"
        self.receiver_frame(self.credential, seq)

    def client_state(self, credential, state, diagnostics=None):
        self.authenticate(credential)
        assert state != "IOS_CAMERA_STREAMING"
        self.states.append(state)


class Frame:
    width = 1920
    height = 1080

    def reformat(self, width, height):
        assert width <= 640 and height <= 480
        self.output_size = (width, height)
        return self

    def to_ndarray(self, format):
        assert format == "bgr24"
        return SimpleNamespace(shape=(self.output_size[1], self.output_size[0], 3))


class Track:
    kind = "video"

    def __init__(self):
        self.queue = asyncio.Queue()
        self.stopped = False

    async def recv(self):
        value = await self.queue.get()
        if isinstance(value, Exception):
            raise value
        return value

    def stop(self):
        self.stopped = True


class Peer:
    peers = []

    def __init__(self, configuration):
        self.handlers = {}
        self.configuration = configuration
        self.connectionState = "new"
        self.closed = False
        self.candidates = []
        self.transceiver = SimpleNamespace(kind="video", direction=None, mid="0")
        self.__class__.peers.append(self)

    def on(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn
        return register

    async def setRemoteDescription(self, value):
        self.remote = value

    def getTransceivers(self):
        return [self.transceiver]

    async def createAnswer(self):
        return SimpleNamespace(type="answer", sdp=SDP.replace("a=sendonly", "a=recvonly"))

    async def setLocalDescription(self, value):
        self.localDescription = value

    async def addIceCandidate(self, candidate):
        self.candidates.append(candidate)

    async def close(self):
        self.closed = True


@pytest.fixture
def receiver(monkeypatch):
    Peer.peers = []
    runtime = (Peer, lambda **kw: SimpleNamespace(**kw), lambda **kw: SimpleNamespace(**kw),
               lambda **kw: SimpleNamespace(**kw), lambda value: SimpleNamespace(raw=value))
    monkeypatch.setattr(module, "_load_runtime", lambda: runtime)
    return module.IOSVideoReceiver(Store())


async def settle(predicate, attempts=100):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.002)
    assert predicate(), "asynchronous condition did not become true"


def test_offer_and_connected_signaling_never_claim_video(receiver):
    async def run():
        answer = await receiver.offer(CREDENTIAL, SDP)
        assert answer["type"] == "answer" and answer["session_id"] == "session1"
        peer = Peer.peers[-1]
        peer.connectionState = "connected"
        await peer.handlers["connectionstatechange"]()
        assert peer.transceiver.direction == "recvonly"
        assert not receiver.store.frames
        assert "IOS_CAMERA_STREAMING" not in receiver.store.states
        await receiver.close()
    asyncio.run(run())


def test_decoded_video_only_marks_streaming_and_delivers_bounded_memory_frame(receiver):
    async def run():
        delivered = []

        async def callback(identity, pixels):
            delivered.append((identity, pixels))

        receiver.on_frame = callback
        await receiver.offer(CREDENTIAL, SDP)
        track = Track()
        Peer.peers[-1].handlers["track"](track)
        await asyncio.sleep(0)
        assert not receiver.store.frames
        frame = Frame()
        await track.queue.put(frame)
        await settle(lambda: bool(delivered))
        assert receiver.store.frames == [1]
        assert delivered[0][0]["camera_id"] == "cam1"
        assert delivered[0][1].shape == (360, 640, 3)
        assert frame.output_size == (640, 360)
        await receiver.close()
        assert track.stopped
    asyncio.run(run())


def test_stall_reports_interrupted_and_actual_frames_recover_same_session(receiver):
    async def run():
        receiver.stall_seconds = 0.01
        await receiver.offer(CREDENTIAL, SDP)
        track = Track()
        Peer.peers[-1].handlers["track"](track)
        await settle(lambda: "IOS_CAMERA_INTERRUPTED" in receiver.store.states)
        assert not receiver.store.frames
        await track.queue.put(Frame())
        await settle(lambda: receiver.store.frames == [1])
        assert receiver.store.states[-1] == "IOS_CAMERA_STREAMING"
        assert receiver._peers["cam1"].identity["session_id"] == "session1"
        await receiver.close()
    asyncio.run(run())


def test_expired_credential_cannot_deliver_frames_or_remain_receiving(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        peer = Peer.peers[-1]
        track = Track()
        peer.handlers["track"](track)
        await asyncio.sleep(0)
        receiver.store.valid = False
        await track.queue.put(Frame())
        await settle(lambda: peer.closed)
        assert not receiver.store.frames and track.stopped
        await receiver.close()
    asyncio.run(run())


def test_reoffer_closes_old_peer_and_keeps_monotonic_frame_sequence(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        first = Peer.peers[-1]
        track = Track()
        first.handlers["track"](track)
        await track.queue.put(Frame())
        await settle(lambda: receiver.store.frames == [1])
        await receiver.offer(CREDENTIAL, SDP)
        assert first.closed and track.stopped
        second = Peer.peers[-1]
        another = Track()
        second.handlers["track"](another)
        await another.queue.put(Frame())
        await settle(lambda: receiver.store.frames == [1, 2])
        await receiver.stop_session("unrelated")
        assert not second.closed
        await receiver.stop_session("session1")
        assert second.closed and another.stopped
        await receiver.close()
    asyncio.run(run())


def test_unexpected_audio_track_closes_peer_and_never_reports_frames(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        peer = Peer.peers[-1]
        track = Track()
        track.kind = "audio"
        peer.handlers["track"](track)
        await settle(lambda: peer.closed)
        assert track.stopped and not receiver.store.frames
        await receiver.close()
    asyncio.run(run())


@pytest.mark.parametrize("sdp", [
    SDP + "m=audio 0 UDP/TLS/RTP/SAVPF 111\r\n",  # Even rejected audio sections are unnecessary.
    SDP + "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n",
    SDP.replace("m=video 9", "m=video 0"),
    SDP.replace("a=sendonly", "a=recvonly"),
    SDP.replace("UDP/TLS/RTP/SAVPF", "RTP/AVP"),
    SDP.replace("a=fingerprint:sha-256", "a=fingerprint:sha-1"),
    SDP + "a=x:" + "z" * 65536,
    SDP + "\x00",
    SDP + "a=candidate:x\r\n" * 129,
], ids=["rejected-audio", "data-channel", "rejected-video", "receive-only", "unencrypted", "sha1", "oversized", "control-character", "candidate-flood"])
def test_rejects_audio_data_unencrypted_or_oversized_sdp_before_peer_created(receiver, sdp):
    async def run():
        with pytest.raises(ValueError):
            await receiver.offer(CREDENTIAL, sdp)
        assert not Peer.peers
    asyncio.run(run())


def test_bad_credential_and_missing_optional_runtime_fail_clearly(receiver, monkeypatch):
    async def run():
        with pytest.raises(PermissionError):
            await receiver.offer("x" * 64, SDP)

        def unavailable():
            raise module.IOSReceiverUnavailable("optional aiortc runtime missing")

        monkeypatch.setattr(module, "_load_runtime", unavailable)
        with pytest.raises(module.IOSReceiverUnavailable, match="aiortc"):
            await receiver.offer(CREDENTIAL, SDP)
        assert not Peer.peers
    asyncio.run(run())


def test_ice_candidates_authenticated_bounded_and_video_only(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        good = {"candidate": "candidate:1 1 UDP 1 127.0.0.1 4000 typ host", "sdpMid": "0", "sdpMLineIndex": 0}
        await receiver.add_ice_candidate(CREDENTIAL, good)
        assert len(Peer.peers[-1].candidates) == 1
        for bad in [dict(good, sdpMLineIndex=1), dict(good, sdpMLineIndex=True),
                    dict(good, sdpMLineIndex=0.0), dict(good, candidate="candidate:" + "x" * 4096),
                    dict(good, candidate="candidate:x\r\na=malicious"), dict(good, extra="bad")]:
            with pytest.raises(ValueError):
                await receiver.add_ice_candidate(CREDENTIAL, bad)
        with pytest.raises(PermissionError):
            await receiver.add_ice_candidate("x" * 64, good)
        receiver._peers["cam1"].candidate_count = 128
        with pytest.raises(ValueError):
            await receiver.add_ice_candidate(CREDENTIAL, good)
        await receiver.close()
    asyncio.run(run())


def test_turn_requires_authenticated_configuration(receiver):
    async def run():
        receiver.ice_servers = [{"urls": "turn:relay.example:3478"}]
        with pytest.raises(ValueError, match="authenticated"):
            await receiver.offer(CREDENTIAL, SDP)
        assert not Peer.peers
        receiver.ice_servers = [{"urls": "turns:relay.example:5349", "username": "temporary", "credential": "temporary"}]
        await receiver.offer(CREDENTIAL, SDP)
        assert Peer.peers[-1].configuration.iceServers[0].username == "temporary"
        await receiver.close()
    asyncio.run(run())


def test_analysis_rate_is_bounded_without_recordings(receiver):
    async def run():
        receiver._clock = lambda: 100.0
        await receiver.offer(CREDENTIAL, SDP)
        track = Track()
        Peer.peers[-1].handlers["track"](track)
        for _ in range(20):
            await track.queue.put(Frame())
        await settle(lambda: track.queue.empty())
        assert receiver.store.frames == [1]
        await receiver.stop(CREDENTIAL)
        assert not receiver._peers and track.stopped
        await receiver.close()
        with pytest.raises(module.IOSReceiverUnavailable):
            await receiver.offer(CREDENTIAL, SDP)
    asyncio.run(run())


def test_credential_rotation_reauth_keeps_authenticated_media_and_stop_state(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        track = Track()
        Peer.peers[-1].handlers["track"](track)
        receiver.store.credential = "r" * 64
        await receiver.reauthorize(receiver.store.credential)
        await track.queue.put(Frame())
        await settle(lambda: receiver.store.frames == [1])
        assert receiver._peers["cam1"].credential == receiver.store.credential
        receiver.store.client_state(receiver.store.credential, "IOS_CAMERA_INTERRUPTED")
        await receiver.stop(receiver.store.credential, report=False)
        assert receiver.store.states[-1] == "IOS_CAMERA_INTERRUPTED"
        assert track.stopped
        await receiver.close()
    asyncio.run(run())


def test_trusted_gateway_cleanup_stops_waiting_track_after_revocation(receiver):
    async def run():
        await receiver.offer(CREDENTIAL, SDP)
        pc = Peer.peers[-1]
        track = Track()
        pc.handlers["track"](track)
        await asyncio.sleep(0)
        receiver.store.valid = False
        await receiver.stop_camera("cam1", report=False)
        assert pc.closed and track.stopped and not receiver._peers
        assert not receiver.store.frames
        await receiver.close()
    asyncio.run(run())


def test_real_aiortc_dtls_srtp_decodes_video_without_audio():
    """Optional real transport test: synthetic pixels only, no physical camera."""
    aiortc = pytest.importorskip("aiortc", reason="optional WebRTC runtime not installed")
    av = pytest.importorskip("av")

    async def run():
        received = asyncio.Event()
        observed = []

        async def callback(identity, pixels):
            observed.append((identity, pixels.shape))
            received.set()

        class SyntheticVideo(aiortc.VideoStreamTrack):
            async def recv(self):
                pts, base = await self.next_timestamp()
                frame = av.VideoFrame(width=640, height=480, format="yuv420p")
                for plane in frame.planes:
                    plane.update(bytes([128]) * plane.buffer_size)
                frame.pts, frame.time_base = pts, base
                return frame

        store = Store()
        receiver = module.IOSVideoReceiver(store, callback)
        sender = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        sender.addTrack(SyntheticVideo())
        sender.getTransceivers()[0].direction = "sendonly"
        try:
            offer = await sender.createOffer()
            await sender.setLocalDescription(offer)
            assert "m=audio" not in sender.localDescription.sdp
            answer = await receiver.offer(CREDENTIAL, sender.localDescription.sdp)
            assert not store.frames  # ICE/signaling has not delivered a decoded frame.
            assert "m=audio" not in answer["sdp"]
            await sender.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer["sdp"], type="answer"))
            await asyncio.wait_for(received.wait(), timeout=12)
            peer = receiver._peers["cam1"]
            assert peer.pc.connectionState == "connected"
            assert peer.pc.getTransceivers()[0].receiver.transport.state == "connected"  # DTLS established.
            assert store.frames and observed[0][1] == (480, 640, 3)
            assert observed[0][0]["session_id"] == "session1"
            assert all(t.kind == "video" for t in peer.pc.getTransceivers())
            stats = await peer.pc.getStats()
            assert any(s.type == "inbound-rtp" and s.kind == "video" and s.packetsReceived > 0 for s in stats.values())
        finally:
            await receiver.close()
            await sender.close()
    asyncio.run(run())
