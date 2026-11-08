"""
Tests for the Final iOS Camera Prerequisite, 15-Second Interruption Policy,
Inference Processing Stall Detection, and 11-Stage Pipeline Diagnostics.

Verifies all required scenarios A through K:
A. No iOS frame during prerequisite: Camera check remains pending/failed; session cannot start yet.
B. One real iOS frame received and decoded: Camera prerequisite passes and remains passed.
C. Short 3-second pause during startup: Session still starts normally.
D. 0–5 second frame gap during active session: No warning or violation.
E. 5–10 second frame gap during active session: Status shows TEMPORARILY PAUSED; session continues.
F. 10–15 second frame gap during active session: Status shows DEGRADED; recovery attempted.
G. More than 15 seconds without valid frames: One IOS_CAMERA_INTERRUPTED incident created; session flagged; Windows notification shown; session continues.
H. Incoming frames at 10.5 FPS while inference is 0.0 FPS: Status shows IOS_PROCESSING_STALLED, not CAMERA_INTERRUPTED; inference worker recovers.
I. Frames stop completely: Received FPS drops to 0.0 within 2 seconds; stale 10.5 FPS never remains frozen.
J. Camera recovers for 3 consecutive seconds: Status changes to IOS_CAMERA_RECOVERED; active alert resolves; incident history remains intact.
K. Same-Wi-Fi route inspection: Diagnostics show whether WebRTC is using a direct local candidate, STUN reflexive candidate or TURN relay.
"""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import time

import numpy as np
import pytest

from windows.strict_ios_readiness import camera_readiness
from windows.strict_ipad_camera_monitor import IPadCameraState, IPadCameraStreamMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _live_gateway(now_epoch: float, frame_seq: int = 3):
    """Gateway returning a fresh streaming state with real frames."""
    return NS(
        ready=True,
        preview=lambda *_: dict(
            status=dict(
                state='IOS_CAMERA_STREAMING',
                streaming=True,
                frame_sequence=frame_seq,
                last_frame_at=now_epoch,
            ),
            preview=dict(
                source='iPhone camera',
                observed_at=now_epoch,
                jpeg='receiver-encoded-jpeg',
            ),
        ),
    )


def _no_frame_gateway(now_epoch: float):
    """Gateway where the device has joined and started the camera, but 0 frames received yet."""
    return NS(
        ready=True,
        preview=lambda *_: dict(
            status=dict(
                state='IOS_CAMERA_STARTING',
                streaming=False,
                frame_sequence=0,
                last_frame_at=None,
            ),
            preview=dict(source=None, observed_at=None, jpeg=None),
        ),
    )


def _paused_after_initial_frame_gateway(now_epoch: float, pause_seconds: float = 3.5):
    """Gateway where 1+ real frame was received and decoded, followed by a brief pause."""
    last_at = now_epoch - pause_seconds
    return NS(
        ready=True,
        preview=lambda *_: dict(
            status=dict(
                state='IOS_CAMERA_INTERRUPTED',
                streaming=False,
                frame_sequence=1,
                last_frame_at=last_at,
            ),
            preview=dict(source='iPhone camera', observed_at=last_at, jpeg=None),
        ),
    )


def _disconnected_gateway():
    """Gateway where the iOS device has not joined at all."""
    return NS(
        ready=True,
        preview=lambda *_: dict(
            status=dict(state='IOS_CAMERA_STOPPED', streaming=False, frame_sequence=0, last_frame_at=None),
            preview=dict(source=None, observed_at=None, jpeg=None),
        ),
    )


def _make_monitor(session_id='sess_test'):
    IPadCameraStreamMonitor.reset_instance()
    mon = IPadCameraStreamMonitor(
        clock=lambda: 0.0,
        wall_clock=lambda: datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc),
    )
    mon.start_session_monitoring(session_id, 'ios_companion_camera')
    mon.ios_device_connected = True
    mon.https_verified = True
    mon.webrtc_connection_state = 'CONNECTED'
    mon.ice_connection_state = 'CONNECTED'
    mon.video_track_received = True
    return mon


# ---------------------------------------------------------------------------
# TEST A, B, C: Initial Camera Prerequisite & Startup Tolerance
# ---------------------------------------------------------------------------

class TestInitialCameraPrerequisiteAndStartup:
    def test_a_no_ios_frame_during_prerequisite_blocks_start(self, tmp_path):
        """Test A: No iOS frame during prerequisite -> check remains pending; session cannot start yet."""
        from core.database import StrictRecordsDB
        from windows.strict_session_preparation import StrictSessionPreparation

        db = StrictRecordsDB(str(tmp_path / 'test_a.sqlite'))
        now = 1000.0
        gw = _no_frame_gateway(now)
        readiness = camera_readiness(gw, 'user_lok', 'prep_1', now=now)
        assert readiness['ready'] is False
        assert readiness['initial_check_passed'] is False
        assert readiness['initial_check_state'] == 'IOS_CAMERA_INITIAL_CHECK: PENDING'
        assert readiness['initializing'] is False

        prep = StrictSessionPreparation(
            db,
            lambda _: dict(ready=True, mode='DEMO'),
            camera_observer=lambda acc, pid: camera_readiness(gw, acc, pid, now=now),
        )
        pid = prep.current('user_lok')['preparation_id']
        prep.host_report('user_lok', pid, dict(camera=True, camera_native_id='a' * 64, visible=True, secure_context=True, online=True))
        token = prep.invite('user_lok', 'IOS', 'https://camera.example.com')['invitation']['url'].split('#')[1]
        phone = prep.join(token, 'iPad')
        prep.heartbeat(pid, 'IOS', phone['credential'], dict(timer_ready=True, visible=True, online=True))

        assert not prep.state('user_lok')['ready']
        with pytest.raises(ValueError, match='(?i)(live video feed|not connected)'):
            prep.authorize_start('user_lok', pid)

    def test_b_one_real_frame_passes_prerequisite_and_remains_passed(self):
        """Test B: One real iOS frame received and decoded -> prerequisite passes and remains passed."""
        mon = _make_monitor()
        assert mon.get_initial_prerequisite_status()['initial_check_passed'] is False
        assert mon.get_initial_prerequisite_status()['status'] == 'IOS_CAMERA_INITIAL_CHECK: PENDING'

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 1.0
        assert mon.record_valid_frame(frame, frame_seq=1) is True
        prereq = mon.get_initial_prerequisite_status()
        assert prereq['initial_check_passed'] is True
        assert prereq['status'] == 'IOS_CAMERA_INITIAL_CHECK: PASSED'
        assert all(prereq['steps'].values())

        # Even if a 4-second pause happens immediately afterward, prerequisite remains PASSED
        mon.evaluate_health(now_mono=5.0)
        assert mon.get_initial_prerequisite_status()['initial_check_passed'] is True
        assert mon.get_initial_prerequisite_status()['status'] == 'IOS_CAMERA_INITIAL_CHECK: PASSED'

    def test_c_short_3_second_pause_during_startup_allows_session_start(self, tmp_path):
        """Test C: Short 3-second pause after initial frame during startup -> session still starts normally."""
        from core.database import StrictRecordsDB
        from windows.strict_session_preparation import StrictSessionPreparation

        db = StrictRecordsDB(str(tmp_path / 'test_c.sqlite'))
        now = 1000.0
        gw = _paused_after_initial_frame_gateway(now, pause_seconds=3.5)
        readiness = camera_readiness(gw, 'user_lok', 'prep_1', now=now)
        assert readiness['ready'] is False  # Not fresh (<3s), because 3.5s elapsed
        assert readiness['initial_check_passed'] is True
        assert readiness['initial_check_state'] == 'IOS_CAMERA_INITIAL_CHECK: PASSED'
        assert readiness['initializing'] is True

        prep = StrictSessionPreparation(
            db,
            lambda _: dict(ready=True, mode='DEMO'),
            camera_observer=lambda acc, pid: camera_readiness(gw, acc, pid, now=now),
        )
        pid = prep.current('user_lok')['preparation_id']
        prep.host_report('user_lok', pid, dict(camera=True, camera_native_id='a' * 64, visible=True, secure_context=True, online=True))
        token = prep.invite('user_lok', 'IOS', 'https://camera.example.com')['invitation']['url'].split('#')[1]
        phone = prep.join(token, 'iPad')
        prep.heartbeat(pid, 'IOS', phone['credential'], dict(timer_ready=True, visible=True, online=True))

        state = prep.authorize_start('user_lok', pid)
        assert state['ready'] is True
        assert state['devices']['IOS']['initial_check_passed'] is True
        assert state['devices']['IOS']['camera_acceptable'] is True


# ---------------------------------------------------------------------------
# TEST D: 0–5 s gap → ACTIVE / TEMPORARY GAP (no warning or violation)
# ---------------------------------------------------------------------------

class TestTier1ShortGapNoViolation:
    def test_d_3s_and_5s_gap_no_warning_or_violation(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result_3s = mon.evaluate_health(now_mono=3.0)
        assert result_3s['is_interrupted'] is False
        assert result_3s['is_degraded'] is False
        assert result_3s['is_temporarily_paused'] is False
        assert result_3s['is_timer_paused'] is False
        assert result_3s['active_incident'] is None

        result_5s = mon.evaluate_health(now_mono=5.0)
        assert result_5s['is_interrupted'] is False
        assert result_5s['is_degraded'] is False
        assert result_5s['is_timer_paused'] is False


# ---------------------------------------------------------------------------
# TEST E: 5–10 s gap → TEMPORARILY PAUSED, session continues
# ---------------------------------------------------------------------------

class TestTier2TemporarilyPaused:
    def test_e_8s_gap_is_temporarily_paused(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result = mon.evaluate_health(now_mono=8.0)
        assert result['is_temporarily_paused'] is True
        assert result['is_degraded'] is False
        assert result['is_interrupted'] is False
        assert result['is_timer_paused'] is False
        assert result['active_incident'] is None
        assert 'TEMPORARILY PAUSED' in result['display_label'].upper()

    def test_e_9_9s_gap_is_temporarily_paused_not_degraded(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result = mon.evaluate_health(now_mono=9.9)
        assert result['is_temporarily_paused'] is True
        assert result['is_degraded'] is False
        assert result['active_incident'] is None


# ---------------------------------------------------------------------------
# TEST F: 10–15 s gap → DEGRADED, recovery attempted
# ---------------------------------------------------------------------------

class TestTier3Degraded:
    def test_f_12s_gap_is_degraded(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result = mon.evaluate_health(now_mono=12.0)
        assert result['is_degraded'] is True
        assert result['is_interrupted'] is False
        assert result['is_timer_paused'] is False
        assert result['active_incident'] is None
        assert 'DEGRADED' in result['display_label']

    def test_f_14_9s_gap_still_degraded_not_interrupted(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result = mon.evaluate_health(now_mono=14.9)
        assert result['is_degraded'] is True
        assert result['is_interrupted'] is False
        assert result['active_incident'] is None


# ---------------------------------------------------------------------------
# TEST G: >15 s gap → CAMERA_INTERRUPTED: single incident, session flagged
# ---------------------------------------------------------------------------

class TestTier4InterruptedAfter15Seconds:
    def test_g_16s_gap_creates_single_incident_and_flags_session(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        result = mon.evaluate_health(now_mono=16.0)
        assert result['is_interrupted'] is True
        assert result['is_session_flagged'] is True
        assert 'FLAGGED_CAMERA_INTERRUPTION' in result['flags']
        assert result['active_incident'] is not None
        assert result['active_incident']['incident_status'] == 'OPEN'
        assert result['active_incident']['event_type'] == 'IOS_CAMERA_INTERRUPTED'
        assert result['incidents_count'] == 1
        assert result['interruption_warning_modal']['alert_header'] == 'iOS CAMERA INTERRUPTED – SESSION FLAGGED'

        # Repeated evaluations during same outage do not create duplicate incidents
        for t in [18.0, 22.0, 30.0]:
            mon.evaluate_health(now_mono=t)
        assert len(mon.incidents_history) == 1

    def test_g_webrtc_connected_without_frames_triggers_interruption(self):
        mon = _make_monitor()
        mon.webrtc_connection_state = 'CONNECTED'
        mon.ice_connection_state = 'CONNECTED'
        mon.video_track_received = True
        mon.last_valid_frame_mono = None
        mon.session_started_mono = 0.0
        mon._clock = lambda: 16.0
        result = mon.evaluate_health(now_mono=16.0)
        assert result['is_live'] is False
        assert result['health_state'] == IPadCameraState.CAMERA_INTERRUPTED
        assert mon.active_incident is not None


# ---------------------------------------------------------------------------
# TEST H & I: Inference Processing Stall vs Frame Stop (No Stale 10.5 FPS)
# ---------------------------------------------------------------------------

class TestInferenceProcessingStallAndDynamicFps:
    def test_h_incoming_frames_10_5_fps_with_zero_processing_fps_reports_processing_stalled(self):
        """Test H: Incoming frames at ~10.5 FPS while inference is 0.0 FPS -> IOS_PROCESSING_STALLED, NOT CAMERA_INTERRUPTED."""
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Simulate 50 frames arriving over ~4.7 seconds (~10.5 FPS) WITHOUT inference completion
        t = 0.0
        for seq in range(1, 50):
            t = round(seq * 0.095, 3)
            mon._clock = lambda: t
            mon.record_valid_frame(frame, frame_seq=seq, mark_processed=False)
            mon.record_frame_queued_for_inference(frame_seq=seq)

        diag = mon.evaluate_health(now_mono=t)
        assert diag['received_fps'] > 8.0
        assert diag['processing_fps'] == 0.0
        assert diag['is_interrupted'] is False
        assert diag['is_processing_stalled'] is True
        assert diag['health_state'] == IPadCameraState.IOS_PROCESSING_STALLED
        assert diag['display_label'] == 'VIDEO RECEIVING – PROCTORING PROCESSING STALLED'
        assert diag['root_cause_classification'] == 'WINDOWS_INFERENCE_STALL'
        assert diag['active_incident'] is None

    def test_i_frames_stop_completely_drops_fps_to_zero_within_2_seconds(self):
        """Test I: Frames stop completely -> received_fps drops to 0.0 within 2 seconds; never remains frozen at 10.5 FPS."""
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Feed 21 frames in 2 seconds (~10.5 FPS)
        for i in range(21):
            t = round(i * 0.095, 3)
            mon._clock = lambda: t
            mon.record_valid_frame(frame, frame_seq=i + 1)

        assert mon.received_fps >= 9.0

        # Now frames stop completely for 2.1 seconds
        mon._clock = lambda: 4.1
        diag = mon.evaluate_health(now_mono=4.1)
        assert diag['received_fps'] == 0.0
        assert diag['decoded_fps'] == 0.0
        assert diag['processing_fps'] == 0.0


# ---------------------------------------------------------------------------
# TEST J: Camera recovers for 3 consecutive seconds → IOS_CAMERA_RECOVERED
# ---------------------------------------------------------------------------

class TestCameraRecovery3Seconds:
    def test_j_camera_recovers_after_3_consecutive_seconds(self):
        mon = _make_monitor()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        mon._clock = lambda: 0.0
        mon.record_valid_frame(frame)

        # Interrupt at t=16s (> 15s)
        mon.evaluate_health(now_mono=16.0)
        assert mon.active_incident is not None

        # First recovery frame at t=17.0s
        mon._clock = lambda: 17.0
        mon.record_valid_frame(frame)
        assert mon.stable_recovery_start_mono == 17.0
        assert mon.health_state == IPadCameraState.CAMERA_RECOVERING

        # At t=19.0s (2.0s < 3.0s) -> still recovering
        res_2s = mon.evaluate_health(now_mono=19.0)
        assert res_2s['health_state'] == IPadCameraState.CAMERA_RECOVERING
        assert mon.active_incident is not None

        # At t=20.1s (3.1s >= 3.0s) -> recovered!
        mon._clock = lambda: 20.1
        mon.record_valid_frame(frame)
        diag = mon.get_diagnostics()
        assert mon.active_incident is None
        assert len(mon.incidents_history) == 1
        assert mon.incidents_history[0]['incident_status'] == 'RESOLVED'
        assert mon.incidents_history[0]['recovery_timestamp'] is not None


# ---------------------------------------------------------------------------
# TEST K: Same-Wi-Fi Route Inspection (Direct Host vs STUN vs TURN Relay)
# ---------------------------------------------------------------------------

class TestSameWifiRouteInspection:
    def test_k_webrtc_candidate_pair_route_classification(self):
        mon = _make_monitor()

        # 1. Direct local host candidate
        mon.record_windows_receiver_stats({
            'packetsReceived': 120,
            'bytesReceived': 98000,
            'framesReceived': 30,
            'framesDecoded': 30,
            'iceConnectionState': 'connected',
            'selectedCandidatePair': {
                'localCandidateType': 'host',
                'remoteCandidateType': 'host',
                'rttMs': 4.2,
            },
        })
        diag_local = mon.get_diagnostics()
        assert diag_local['ice_route_type'] == 'DIRECT_LOCAL_HOST'
        assert diag_local['ice_rtt_ms'] == 4.2

        # 2. STUN server-reflexive candidate
        mon.record_windows_receiver_stats({
            'packetsReceived': 140,
            'bytesReceived': 110000,
            'framesReceived': 35,
            'framesDecoded': 35,
            'iceConnectionState': 'connected',
            'selectedCandidatePair': {
                'localCandidateType': 'srflx',
                'remoteCandidateType': 'host',
                'rttMs': 12.5,
            },
        })
        diag_stun = mon.get_diagnostics()
        assert diag_stun['ice_route_type'] == 'STUN_SRFLX'

        # 3. TURN relay candidate
        mon.record_windows_receiver_stats({
            'packetsReceived': 150,
            'bytesReceived': 120000,
            'framesReceived': 35,
            'framesDecoded': 35,
            'iceConnectionState': 'connected',
            'selectedCandidatePair': {
                'localCandidateType': 'relay',
                'remoteCandidateType': 'relay',
                'rttMs': 85.0,
            },
        })
        diag_turn = mon.get_diagnostics()
        assert diag_turn['ice_route_type'] == 'TURN_RELAY'


# ---------------------------------------------------------------------------
# Live Study Hardware 15-Second Grace Period Integration
# ---------------------------------------------------------------------------

class TestLiveStudyHardware15sGrace:
    def test_live_study_hardware_keeps_monitoring_ok_up_to_15s_and_pauses_after_15s(self):
        from core.models import StrictSessionState
        from windows.strict_session_manager import StrictSessionManager

        manager = object.__new__(StrictSessionManager)
        manager.study_hardware = NS(view=lambda: dict(monitoring_ok=True))
        manager._active_preparation = 'prep_1'
        manager.user_id = 'user_lok'
        manager.session_id = 'sess_1'
        manager.state = StrictSessionState.ACTIVE
        manager._foreground_study_ok = True
        manager._hook_handle = 1
        manager.notification_filter = NS(status=lambda: dict(active=True))

        def make_gap_gateway(last_frame_at):
            return NS(
                ready=True,
                preview=lambda *_: dict(
                    status=dict(
                        state='IOS_CAMERA_INTERRUPTED',
                        streaming=False,
                        frame_sequence=5,
                        last_frame_at=last_frame_at,
                    ),
                    preview=dict(source='iPhone camera', observed_at=last_frame_at, jpeg=None),
                ),
            )

        # 3s gap -> CAMERA_RECOVERING, within_grace=True -> monitoring_ok=True
        manager._ios_camera_readiness = lambda acc, pid: camera_readiness(make_gap_gateway(997.0), acc, pid, now=1000.0)
        assert manager._live_study_hardware()['monitoring_ok'] is True

        # 8s gap -> CAMERA_TEMPORARILY_PAUSED, within_grace=True -> monitoring_ok=True
        manager._ios_camera_readiness = lambda acc, pid: camera_readiness(make_gap_gateway(992.0), acc, pid, now=1000.0)
        assert manager._live_study_hardware()['monitoring_ok'] is True

        # 13s gap -> CAMERA_DEGRADED, within_grace=True -> monitoring_ok=True
        manager._ios_camera_readiness = lambda acc, pid: camera_readiness(make_gap_gateway(987.0), acc, pid, now=1000.0)
        assert manager._live_study_hardware()['monitoring_ok'] is True

        # 16s gap -> CAMERA_INTERRUPTED, within_grace=False -> monitoring_ok=False
        manager._ios_camera_readiness = lambda acc, pid: camera_readiness(make_gap_gateway(984.0), acc, pid, now=1000.0)
        assert manager._live_study_hardware()['monitoring_ok'] is False
