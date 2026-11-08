from windows.strict_focus_layout import StudyPaneLayout
from windows.strict_focus_chrome import monitoring_details
from windows.strict_focus_badges import component_states
from windows.strict_browser_pane import toolbar_geometry,load_error_message
from windows.strict_layout_toolbar import shared_toolbar_geometry


def test_wide_workspace_is_equal_by_default_and_divider_stays_between_quarters():
    view=StudyPaneLayout();bounds=view.geometry(1608,800)
    assert bounds['chatgpt'][2]==bounds['leetcode'][2]==800
    view.resize(-100,1608);assert view.geometry(1608,800)['chatgpt'][2]==400
    view.resize(99999,1608);assert view.geometry(1608,800)['chatgpt'][2]==1200


def test_saved_layout_restores_ratio_order_and_mode_without_touching_browser_identity():
    view=StudyPaneLayout();view.restore_preferences(dict(ratio=.6,order=['leetcode','chatgpt'],active='leetcode',side_by_side=False))
    assert view.ratio==.6 and view.order==['leetcode','chatgpt'] and not view.side_by_side
    assert view.geometry(1600,800)==dict(leetcode=(0,0,1600,800),divider=None)
    view.restore_preferences(dict(ratio=.17,order=['third','chatgpt']))
    assert view.ratio==.25 and set(view.order)=={'chatgpt','leetcode'}


def test_camera_cannot_imply_screen_sharing_or_screen_monitoring_or_verified_jack():
    detail=monitoring_details({},dict(camera='ACTIVE',face_tracking='VERIFIED',attention_monitoring='ACTIVE',
        proctor_calibrated=True,audio_detection_known=True,wired_state_known=False,speaker_muted=True))
    assert detail['Webcam']=='ACTIVE' and detail['StrictGuard']=='ACTIVE'
    assert detail['Screen sharing']==detail['Screen monitoring']==detail['Wired audio']=='UNVERIFIED'
    assert detail['Head direction']=='FORWARD'
    assert monitoring_details({}, {})['Head direction']=='UNVERIFIED'


def test_previous_attention_warning_does_not_claim_current_left_direction():
    detail=monitoring_details({},dict(attention_monitoring='ACTIVE',face_tracking='VERIFIED',proctor_calibrated=True,
        leftward_seconds=0,leftward_violations=1))
    assert detail['Head direction']=='FORWARD' and detail['Violations']=='1/15'
    detail=monitoring_details({},dict(screen_sharing_active=False,screen_monitoring_active=True))
    assert detail['Screen sharing']=='OFF' and detail['Screen monitoring']=='ACTIVE'


def test_toolbar_controls_do_not_clip_or_overlap_at_supported_pane_widths():
    for width in (320,400,560,787,1200):
        layout=toolbar_geometry(width)
        controls=[layout[name] for name in ('tab','back','forward','reload')]
        for x,y,w,h in controls:assert 0<=x<x+w<=width and y+h<=34
        for first,second in zip(controls,controls[1:]):assert first[0]+first[2]<=second[0]
        assert layout['address'][2]==width-20 and layout['address'][1]>=34
        assert 'swap' not in layout and 'mode' not in layout


def test_shared_layout_controls_are_unique_and_fit_minimum_window():
    for width in (640,1280,1366,1600,1920):
        layout=shared_toolbar_geometry(width)
        assert set(layout)=={'split','swap','chatgpt','leetcode','restore'}
        controls=list(layout.values())
        assert all(x>=0 and x+w<=width and y+h<=36 for x,y,w,h in controls)
        assert all(a[0]+a[2]<=b[0] for a,b in zip(controls,controls[1:]))


def test_component_badges_never_turn_synthetic_or_missing_reports_green():
    badges=component_states(dict(mode='DEMO'),dict(camera='OFFLINE_TEST',face_tracking='SIMULATED'))
    assert set(state for state,detail in badges.values())=={'UNVERIFIED'}
    badges=component_states(dict(ios_connected=False,android_connected=True),dict(camera='ACTIVE',speaker_muted=True,
        audio_detection_known=True,wired_state_known=False,screen_capture_active=True),True)
    assert badges['Webcam'][0]=='ACTIVE' and badges['Screen sharing'][0]=='ACTIVE'
    assert badges['Screen monitoring'][0]==badges['Wired audio'][0]=='UNVERIFIED'
    assert badges['iOS synchronization'][0]=='WARNING' and badges['Android synchronization'][0]=='ACTIVE'
    assert badges['Speaker'][0]=='CLEAR'


def test_browser_errors_offer_recovery_without_bypassing_certificate_checks():
    assert 'internet' in load_error_message('ServerUnreachable') or 'retry' in load_error_message('ServerUnreachable')
    assert 'could not be verified' in load_error_message('CertificateExpired')
    assert 'retry' in load_error_message('Timeout')
