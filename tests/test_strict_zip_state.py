"""Truthful host-to-React presentation; no native UI or production data."""
import base64
import time
from copy import deepcopy

import pytest

from windows.strict_zip_state import end_enabled, frontend_snapshot, runtime_payload


JPEG = base64.b64encode(b'\xff\xd8sample-host-frame\xff\xd9').decode()


def live_hardware(**extra):
    return dict(camera='ACTIVE', attention_monitoring='ACTIVE', face_tracking='VERIFIED',
                face_present=True, proctor_calibrated=True, monitoring_ok=True,
                camera_preview=dict(current=True, jpeg=JPEG), last_frame_age_seconds=.2,
                **extra)


def test_missing_observations_stay_unverified_and_do_not_invent_devices():
    s = frontend_snapshot({}, {})
    assert s['camera']['faceDetected'] is None and not s['camera']['isStreaming']
    assert s['camera']['gazeDirection'] == 'UNVERIFIED'
    assert s['screenCapture']['screenShareActive'] is None
    assert s['proctoring']['screenProctoringEnabled'] is None
    assert s['proctoring']['audioState'] == 'UNVERIFIED'
    assert s['battery']['percentage'] is None
    assert all(d['status'] == 'AWAITING_HOST' for d in s['deviceConnections']['devices'].values())
    assert not end_enabled({}, {})


def test_offline_strips_even_all_green_mock_hardware():
    h = live_hardware(battery_percent=55, charging=True, screen_capture_active=True,
                      screen_proctoring_active=True, speaker_muted=True, audio_detection_known=True)
    status = dict(mode='DEMO', state='OFFLINE_UI_PREVIEW', focus_remaining_seconds=599,
                  focus_target_seconds=600, ios_connected=True, android_connected=True, windows_connected=True)
    p = runtime_payload(status, h, offline=True)
    s = p['snapshot']
    assert s['meta']['environmentMode'] == 'DEMO'
    assert s['timer']['remainingSeconds'] == 599 and s['timer']['runState'] == 'PAUSED'
    assert s['camera']['externalFrameDataUrl'] is None and not s['camera']['isConnected']
    assert s['battery']['percentage'] is None and p['hardware'] == {}
    assert s['screenCapture']['screenShareActive'] is None
    assert not s['proctoring']['monitoringActive']
    assert all(not d['isVerified'] for d in s['deviceConnections']['devices'].values())
    assert p['endEnabled']


@pytest.mark.parametrize('bad', [None, '', 'invalid', 'data:image/jpeg;base64,' + JPEG, 'A' * 66000],
                         ids=['missing', 'empty', 'bad-base64', 'unexpected-data-url', 'oversized'])
def test_camera_rejects_invalid_or_oversized_frames(bad):
    h = live_hardware()
    h['camera_preview']['jpeg'] = bad
    assert frontend_snapshot({}, h)['camera']['externalFrameDataUrl'] is None


def test_camera_needs_current_initialized_fresh_host_frame():
    h = live_hardware()
    s = frontend_snapshot({}, h)
    assert s['camera']['sourceType'] == 'HOST_NATIVE_STREAM'
    assert s['camera']['externalFrameDataUrl'] == 'data:image/jpeg;base64,' + JPEG
    assert s['camera']['faceDetected'] is True
    assert s['camera']['gazeDirection'] == 'CENTER'
    h['leftward_seconds'] = 61
    assert frontend_snapshot({}, h)['camera']['gazeDirection'] == 'LEFT'
    for key, value in [('last_frame_age_seconds', 5), ('camera', 'STOPPED'), ('attention_monitoring', 'STOPPED')]:
        altered = deepcopy(h)
        altered[key] = value
        assert not frontend_snapshot({}, altered)['camera']['isStreaming']
    h['camera_preview']['current'] = False
    assert frontend_snapshot({}, h)['camera']['faceDetected'] is None


def test_screen_share_and_proctoring_are_independent_and_not_derived_from_camera():
    h = live_hardware(screen_capture_active=True, screen_proctoring_active=False)
    s = frontend_snapshot({}, h)
    assert s['screenCapture']['screenShareActive'] is True
    assert s['proctoring']['screenProctoringEnabled'] is False
    h.pop('screen_capture_active')
    assert frontend_snapshot({}, h)['screenCapture']['screenShareActive'] is None


@pytest.mark.parametrize('bad', [True, False, -1, 101, 'nan', float('inf')])
def test_battery_rejects_invalid_percentages(bad):
    assert frontend_snapshot({}, dict(battery_percent=bad))['battery']['percentage'] is None


def test_battery_audio_and_actual_companion_connection_are_projected_without_lockdown_claims():
    s = frontend_snapshot(dict(ios_connected=True), dict(battery_percent=86, charging=True,
                          bluetooth_headphone_connected=True, prohibited_audio_name='WF-1000XM5',
                          audio_detection_known=True, bluetooth_adapter_state='ON', speaker_muted=True))
    assert s['battery']['percentage'] == 86 and s['battery']['powerSource'] == 'AC_CHARGING'
    assert s['bluetooth']['adapterState'] == 'ON'
    assert s['bluetooth']['devices'][0]['name'] == 'WF-1000XM5'
    assert not s['bluetooth']['devices'][0]['isAllowed']
    assert s['proctoring']['audioState'] == 'MUTED'
    assert s['deviceConnections']['devices']['IOS']['isVerified']
    assert not s['deviceConnections']['devices']['IOS']['lockdownActive']
    assert s['deviceConnections']['devices']['ANDROID']['status'] == 'AWAITING_HOST'


def test_adaptive_and_paused_timer_follow_backend_without_local_pause_permission():
    status = dict(state='ADAPTIVE_COMPLETION', adaptive_remaining_seconds=120,
                  focus_target_seconds=2100, elapsed_seconds=2280, monitoring_ok=True, monitoring_resumed=True)
    t = frontend_snapshot(status, {})['timer']
    assert t['remainingSeconds'] == 120 and t['totalDurationSeconds'] == 2400
    assert t['runState'] == 'RUNNING' and not t['allowUserPause']
    status['monitoring_ok'] = False
    assert frontend_snapshot(status, {})['timer']['runState'] == 'PAUSED'


def test_existing_end_gate_requires_all_host_checks_and_preserves_demo_exit():
    status = dict(end_enabled=True, reward_eligible=True)
    h = dict(monitoring_ok=True, notification_filter_active=True)
    assert end_enabled(status, h)
    for key in ('monitoring_ok', 'notification_filter_active'):
        assert not end_enabled(status, dict(h, **{key: False}))
    assert not end_enabled(status, dict(h, prohibited_audio_detected=True))
    assert not end_enabled(status, dict(h, leftward_violations=16))
    assert not end_enabled(status, dict(h, headphone_violations=2))
    assert not end_enabled(dict(status, reward_eligible=False), h)
    assert end_enabled(dict(mode='DEMO'), {})


def test_snapshot_and_payload_do_not_mutate_host_data_or_leak_browser_authority():
    status = dict(state='ACTIVE_FOCUS', focus_target_seconds=2100, focus_remaining_seconds=2000)
    h = live_hardware()
    b = dict(chatgpt=dict(state='loading', url='https://chatgpt.com/'))
    originals = deepcopy((status, h, b))
    p = runtime_payload(status, h, browser=b)
    p['hardware']['camera_preview']['current'] = False
    p['browser']['chatgpt']['state'] = 'ready'
    assert (status, h, b) == originals
    assert p['snapshot']['browserRestrictions']['providerStatus'] == 'AWAITING_HOST'
    assert not p['snapshot']['browserRestrictions']['blockExternalClipboard']


def test_iphone_preview_is_separate_and_needs_actual_recent_receiver_frame():
    now = time.time()
    h = live_hardware(ios_camera=dict(state='IOS_CAMERA_STREAMING', streaming=True, last_frame_at=now),
                      ios_camera_preview=dict(jpeg=JPEG, source='iPhone camera', strictguard='ACTIVE',
                                              face='VISIBLE', observed_at=now))
    p = runtime_payload({}, h)
    assert p['snapshot']['camera']['sourceType'] == 'HOST_NATIVE_STREAM'
    assert p['ios_camera_preview']['source'] == 'iPhone camera'
    assert p['ios_camera_preview']['jpeg'] == JPEG
    assert p['ios_camera']['streaming'] is True
    assert p['ios_camera_preview']['strictguard'] == 'ACTIVE'
    stale = deepcopy(h)
    stale['ios_camera']['last_frame_at'] = now - 10
    assert runtime_payload({}, stale)['ios_camera_preview']['jpeg'] is None
    assert runtime_payload({}, stale)['ios_camera']['streaming'] is False
    stale = deepcopy(h)
    stale['ios_camera_preview']['observed_at'] = now - 4
    assert runtime_payload({}, stale)['ios_camera_preview']['jpeg'] is None
    assert runtime_payload({}, h, offline=True)['ios_camera_preview']['jpeg'] is None
    assert runtime_payload({}, h, offline=True)['ios_camera']['streaming'] is False


def test_iphone_preview_rejects_wrong_source_invalid_image_and_unverified_guard():
    now = time.time()
    h = dict(ios_camera=dict(state='IOS_CAMERA_STREAMING', streaming=True, last_frame_at=now),
             ios_camera_preview=dict(jpeg=JPEG, source='Windows webcam', strictguard='ACTIVE', observed_at=now))
    assert runtime_payload({}, h)['ios_camera_preview']['jpeg'] is None
    h['ios_camera_preview'].update(source='iPhone camera', jpeg='not-jpeg')
    assert runtime_payload({}, h)['ios_camera_preview']['jpeg'] is None
    h['ios_camera_preview'].update(jpeg=JPEG, strictguard='UNVERIFIED')
    p = runtime_payload({}, h)
    assert p['ios_camera_preview']['jpeg'] == JPEG
    assert p['ios_camera_preview']['strictguard'] == 'UNVERIFIED'
    assert p['snapshot']['camera']['sourceType'] == 'NONE'
    assert not p['snapshot']['camera']['isStreaming']
