"""Pure presentation checks: no session, native window, camera, or browser."""
import pytest

from windows.strict_focus_badges import (
    CAPTIONS, COMPACT_KEYS, battery_reading, component_states, countdown_candidates,
    device_states, fit_countdown, header_geometry,
)
from windows.strict_focus_chrome import monitoring_details


@pytest.mark.parametrize('width', [640, 700, 839, 840, 898, 899, 900, 999, 1000, 1280, 1366, 1582, 1600, 1920])
def test_compact_header_keeps_title_devices_and_every_monitor_inside_sixty_pixels(width):
    layout = header_geometry(width)
    bounds = layout['bounds']
    assert all('compact_' + key in bounds for key in COMPACT_KEYS)
    for x, y, w, h in bounds.values():
        assert w > 0 and h > 0
        assert 0 <= x < x + w <= width
        assert 0 <= y < y + h <= 60
    rows = [('title', 'mode', 'timer', 'battery_icon', 'battery', 'toggle'),
            ('pc', 'android', 'ios') +
            tuple('compact_' + key for key in COMPACT_KEYS) + ('camera',)]
    for row in rows:
        for first, second in zip(row, row[1:]):
            assert bounds[first][0] + bounds[first][2] <= bounds[second][0]


def test_narrow_header_uses_all_icons_and_wide_header_adds_concise_names():
    assert header_geometry(640)['caption_mode'] == 'icon'
    assert header_geometry(900)['caption_mode'] == 'short'
    assert header_geometry(1280)['caption_mode'] == 'full'
    assert header_geometry(1582)['caption_mode'] == 'full'


def test_complete_session_title_has_space_and_screen_labels_stay_distinct():
    assert header_geometry(640)['bounds']['title'][2] >= 230
    assert header_geometry(1280)['bounds']['title'][2] >= 252
    for mode in ('short', 'full'):
        captions = dict(zip(COMPACT_KEYS, CAPTIONS[mode]))
        assert captions['Screen sharing'] == 'Share'
        assert captions['Screen monitoring'] == 'Proctor'


def test_smaller_initial_native_header_never_creates_negative_bounds():
    for width in (0, 120, 320, 560):
        for x, _, w, _ in header_geometry(width)['bounds'].values():
            assert 0 <= x <= x + w <= width


def test_windows_requires_an_explicit_report_and_offline_samples_never_verify_any_device():
    assert device_states({}, {})['pc'][0] == 'UNVERIFIED'
    status = dict(windows_connected=True, android_connected=True, ios_connected=False)
    states = device_states(status, {})
    assert states['pc'][0] == states['android'][0] == 'ACTIVE'
    assert states['ios'][0] == 'WARNING'
    assert {state for state, _ in device_states(status, {}, offline=True).values()} == {'UNVERIFIED'}
    assert device_states({}, dict(host_connected=False))['pc'][0] == 'WARNING'
    assert device_states(dict(pc_connected='true'), {})['pc'][0] == 'UNVERIFIED'


@pytest.mark.parametrize('source', ['OFFLINE_LAYOUT', 'SIMULATED', 'SYNTHETIC_REPORT'])
def test_synthetic_source_suppresses_green_even_when_fixture_contains_live_looking_booleans(source):
    status = dict(state=source, android_connected=True, ios_connected=True)
    hardware = dict(camera='ACTIVE', face_tracking='VERIFIED', attention_monitoring='ACTIVE',
                    proctor_calibrated=True, audio_detection_known=True, wired_state_known=True,
                    speaker_muted=True, screen_sharing_active=True, screen_proctoring_active=True,
                    battery_percent=65, charging=True)
    assert {state for state, _ in component_states(status, hardware, True).values()} == {'UNVERIFIED'}
    assert battery_reading(status, hardware) == (None, None)


def test_monitoring_components_remain_independent_and_retain_separate_raw_diagnostics():
    hardware = dict(camera='ACTIVE', face_tracking='VERIFIED', attention_monitoring='ACTIVE',
                    proctor_calibrated=False, screen_capture_active=True, screen_proctoring_active=False,
                    audio_detection_known=True, wired_state_known=False, speaker_muted=False)
    states = component_states({}, hardware)
    assert states['Webcam'][0] == states['Face'][0] == 'ACTIVE'
    assert states['Head direction'][0] == states['Wired audio'][0] == 'UNVERIFIED'
    assert states['Screen sharing'][0] == 'ACTIVE'
    assert states['Screen monitoring'][0] == 'PENDING'
    assert states['Speaker'][0] == 'WARNING'
    detail = monitoring_details({}, hardware)
    assert detail['Webcam'] == 'ACTIVE'
    assert detail['StrictGuard'] == 'ACTIVE'
    assert detail['Head direction'] == 'UNVERIFIED'
    assert detail['Screen sharing'] == 'ACTIVE' and detail['Screen monitoring'] == 'OFF'
    assert detail['Wired audio'] == 'UNVERIFIED' and detail['Speaker'] == 'NOT MUTED'


def test_failed_camera_and_pending_camera_stay_distinct_and_bad_head_data_does_not_crash():
    assert component_states({}, dict(camera='NO_FRAME'))['Webcam'][0] == 'FAILED'
    assert component_states({}, dict(camera='NOT_STARTED'))['Webcam'][0] == 'PENDING'
    assert component_states({}, dict(leftward_seconds='unreadable'))['Head direction'][0] == 'UNVERIFIED'
    assert monitoring_details({}, dict(leftward_seconds='unreadable'))['Head direction'] == 'UNVERIFIED'


@pytest.mark.parametrize('percent', [None, True, False, -1, 101, float('inf'), float('nan'), 'unreadable'])
def test_missing_or_invalid_battery_percentage_cannot_fill_a_battery(percent):
    assert battery_reading({}, dict(battery_percent=percent, charging=None)) == (None, None)


def test_battery_and_charging_are_separate_real_readings():
    assert battery_reading({}, dict(battery_percent=76, charging=True)) == (76, True)
    assert battery_reading({}, dict(battery_percent=0, charging=False)) == (0, False)
    assert battery_reading({}, dict(battery_percent=76, charging='yes')) == (76, None)
    assert battery_reading({}, dict(camera='OFFLINE_TEST', battery_percent=76, charging=True)) == (None, None)


def test_countdown_uses_full_text_when_it_fits_then_a_measured_remaining_fallback():
    text = '09:59 / 10:00'
    measure = lambda value: len(value) * 10
    assert fit_countdown(text, 599, 200, measure) == text
    assert fit_countdown(text, 599, 100, measure) == '09:59 left'
    assert fit_countdown(text, 599, 10, measure) == '—'
    assert fit_countdown(text, 599, 0, measure) == ''
    assert countdown_candidates(text, 599)[0] == text
    assert '1:01:01 left' in countdown_candidates('61:01 / 70:00', 3661)
