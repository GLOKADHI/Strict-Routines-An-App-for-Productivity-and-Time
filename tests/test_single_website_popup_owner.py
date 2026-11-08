from unittest.mock import Mock, patch

import pytest

from windows.restriction_manager import YouTubeRestrictionManager, RestrictionState


def manager():
    m=YouTubeRestrictionManager()
    m._popup_controller=Mock(visible=False)
    m.get_points_status=lambda **kwargs:dict(current_score=-10,required_score=80,remaining_points=90,score_available=True)
    m.reconcile_browser_policies_if_needed=lambda:None
    m.get_app_locks=lambda:{}
    m.get_service_policy=lambda key:dict(effectiveAccessAllowed=False,requestedRestrictionEnabled=True,policyDecision='BLOCK')
    return m


@pytest.mark.parametrize('process',['chrome.exe','msedge.exe','comet.exe','brave.exe','opera.exe','vivaldi.exe','arc.exe','firefox.exe'])
@pytest.mark.parametrize('extension_online',[True,False])
def test_website_never_opens_desktop_duplicate(process,extension_online):
    m=manager()
    target=dict(action='CLOSE_NATIVE_TAB',process_name=process,hwnd=123,pid=456,tab_id='tab',app_key='youtube',target_app='YouTube',required_score=80)
    m.find_restricted_windows=lambda:[target]
    m.browser_extension_is_connected=lambda *args,**kwargs:extension_online
    m._is_front_popup_active=True
    m.lock_screen_visible=True
    result=m.check_and_enforce()
    m._popup_controller.show.assert_not_called()
    m._popup_controller.close.assert_called_once()
    assert result['popup_owner']=='BROWSER_EXTENSION'
    assert result['popup_visible'] is False
    assert result['native_popup_visible'] is False
    assert result['allowed'] is False


def test_direct_legacy_call_cannot_resurrect_browser_popup():
    m=manager();m._active_native_target=dict(action='CLOSE_TAB',process_name='comet.exe')
    m.show_front_popup('YouTube',-10,80,'Restricted')
    m._popup_controller.show.assert_not_called()


def test_native_spotify_popup_remains_available():
    m=manager();m.target_app_key='spotify'
    m._active_native_target=dict(action='CLOSE_APP',process_name='spotify.exe',hwnd=123)
    m._entry_session.observe(('spotify',123,456))
    with patch.dict('os.environ',{'PYTEST_CURRENT_TEST':''}):
        m.show_front_popup('Spotify',-10,60,'Music requires 60 points')
    m._popup_controller.show.assert_called_once()
    result=m.get_status()
    assert result['popup_owner']=='NATIVE_APP'
    assert result['native_popup_visible'] is True
    assert result['allowed'] is False


def test_status_never_exposes_dashboard_overlay_for_stale_browser_flags():
    m=manager();m.lock_screen_visible=True;m.state=RestrictionState.LOCK_SCREEN_ACTIVE
    m._active_native_target=dict(action='CLOSE_NATIVE_TAB',process_name='chrome.exe')
    result=m.get_status()
    assert result['popup_owner']=='BROWSER_EXTENSION'
    assert not result['popup_visible'] and not result['lock_screen_visible']
