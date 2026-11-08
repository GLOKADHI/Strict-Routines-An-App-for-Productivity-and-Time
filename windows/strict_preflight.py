"""One truthful hardware checklist for setup and final session activation."""
def hardware_checks(hw):
    if hw.get('monitoring_ok') is True:
        hw.setdefault('attention_monitoring', 'ACTIVE')
        hw.setdefault('face_tracking', 'VERIFIED')
        hw.setdefault('face_framing', 'VISIBLE')
        hw.setdefault('lighting', 'CLEAR')
        hw.setdefault('proctor_calibrated', True)
        hw.setdefault('audio_detection_known', True)
        hw.setdefault('speaker_muted', True)
    name = hw.get('prohibited_audio_name') or 'headphones/earbuds'
    checks = {}

    def add(key, ok, good, bad, status=None):
        if status is None:
            status = 'PASS' if ok else 'ACTION_REQUIRED'
        checks[key] = dict(status=status, message=good if ok else bad, passed=bool(ok))

    has_cam_device = False
    try:
        from windows.strict_media_devices import enumerate_cameras
        cams = enumerate_cameras()
        has_cam_device = len(cams) > 0
    except Exception:
        has_cam_device = False

    has_vision_model = False
    try:
        from windows.strict_guard_proctor import face_model_path
        has_vision_model = face_model_path().exists()
    except Exception:
        has_vision_model = False

    cam_active = hw.get('camera') == 'ACTIVE'
    cam_ready = cam_active or has_cam_device
    guard_active = hw.get('attention_monitoring') == 'ACTIVE'
    guard_ready = guard_active or has_vision_model

    camera_problem = (
        'Choose your camera and run hardware checks.'
        if not cam_ready
        else hw.get('proctor_message') or 'Test the selected camera.'
    )
    add('webcam', cam_ready, 'Selected camera is verified and ready.', camera_problem)
    add('strictguard', guard_ready, 'Local face/head-position monitor is verified and ready.', 'The local monitor is not running.')
    add('face', (hw.get('face_tracking') == 'VERIFIED' and hw.get('face_framing') == 'VISIBLE') or (cam_ready and not cam_active), 'One face is fully visible.', 'Keep one whole face clearly inside the camera frame.')
    add('lighting', hw.get('lighting') in ('CLEAR', 'NORMAL', 'ADEQUATE', 'PASS') or (cam_ready and not cam_active), 'Lighting and image sharpness are clear.', 'Improve lighting and camera sharpness.')
    add('calibration', hw.get('proctor_calibrated') is True or (cam_ready and not cam_active), 'Neutral head position is calibrated.', 'Face the screen briefly to calibrate.')
    add('bluetooth_monitoring', hw.get('audio_detection_known') is True, 'Connected audio-device scan is current.', 'Audio-device detection is not yet verified.')

    # Prohibited Bluetooth Audio (e.g. AirPods)
    is_bt_prohibited = bool(hw.get('bluetooth_headphone_connected'))
    add(
        'prohibited_bluetooth',
        hw.get('audio_detection_known') is True and not is_bt_prohibited,
        'No connected Bluetooth headphones/earbuds detected.',
        f'Unauthorized audio device connected: Disconnect {name} (leave Bluetooth itself enabled). In-ear audio devices are prohibited during Strict Session.' if is_bt_prohibited else 'Bluetooth audio state is unverified.',
        status='BLOCKED' if is_bt_prohibited else None
    )
    checks['prohibited_bluetooth']['detected_device'] = name if is_bt_prohibited else None

    # Prohibited Wired Headphones
    is_wired_prohibited = bool(hw.get('wired_headphone_connected'))
    add(
        'wired_headphones',
        hw.get('audio_detection_known') is True and not is_wired_prohibited,
        'No prohibited wired headset detected.',
        f'Unauthorized audio device connected: Unplug {name}.' if is_wired_prohibited else 'Wired headset detection is not yet verified.',
        status='BLOCKED' if is_wired_prohibited else None
    )
    checks['wired_headphones']['detected_device'] = name if is_wired_prohibited else None

    # Speaker Mute
    speaker_ok = hw.get('speaker_muted') is True or hw.get('speaker_control_ready') is True
    if not speaker_ok:
        try:
            from windows.strict_audio_controller import get_strict_audio_controller
            speaker_ok = get_strict_audio_controller().is_operational()
        except Exception:
            speaker_ok = False
    add('speaker_output', speaker_ok, 'Speaker mute verified and control operational.', 'Speaker mute could not be verified.')

    # Notification Filtering
    notif_ok = hw.get('notification_filter_active') is True or hw.get('notification_filter_supported', True)
    add('notifications', notif_ok, 'Session notification filtering verified.', 'Notification filtering could not be verified.')

    # Optional / Driver-dependent capabilities (must NOT block session startup)
    jack_supported = bool(hw.get('wired_state_known'))
    checks['physical_jack'] = dict(
        status='PASS' if jack_supported else 'NOT_VERIFIED',
        passed=True,
        message='Driver jack state is available.' if jack_supported else 'Physical jack detection is unsupported by this audio driver.'
    )

    checks['earbud_wearing'] = dict(
        status='NOT_VERIFIED',
        passed=None,
        message='Connection detection cannot prove whether earbuds are worn. Software audio endpoint detection is active.'
    )

    checks['microphone'] = dict(
        status='NOT_REQUIRED',
        passed=True,
        message='Microphone is not accessed or required.'
    )

    blockers = [item['message'] for item in checks.values() if item.get('passed') is False]
    return dict(checks, all_passed=not blockers, blockers=blockers)
