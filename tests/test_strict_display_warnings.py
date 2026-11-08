from copy import deepcopy
import pytest
from windows.strict_display_warnings import DisplayWarningTracker, derive_warnings


def state(**report_changes):
    report=dict(fullscreen=True, visible=True, timer_ready=True, display_mode='fullscreen')
    report.update(report_changes)
    return dict(devices={role:dict(joined=True, fresh=True, report=dict(report)) for role in ('ANDROID','IOS')})


def test_current_fullscreen_devices_have_no_warnings_and_inputs_are_unchanged():
    value=state();before=deepcopy(value)
    assert derive_warnings(value)==[] and value==before


@pytest.mark.parametrize('role',['ANDROID','IOS'])
@pytest.mark.parametrize('kind,code', [('exit','FULLSCREEN_EXITED'),('lock','HIDDEN'),('stale','STALE'),('unsupported','FULLSCREEN_UNAVAILABLE')])
def test_allowed_phone_fullscreen_exit_lock_sleep_does_not_trigger_a_violation(role,kind,code):
    value=state();device=value['devices'][role]
    if kind=='exit':device['report'].update(fullscreen=False,display_mode='page')
    elif kind=='lock':device['report']['visible']=False
    elif kind=='stale':device['fresh']=False
    else:device['report'].update(fullscreen=False,display_mode='expanded')
    warnings=derive_warnings(value)
    assert warnings==[]


def test_not_joined_or_not_yet_in_a_display_does_not_raise_an_exit_warning():
    value=state();value['devices']['ANDROID']['joined']=False;value['devices']['IOS']['report']={}
    assert derive_warnings(value)==[]


@pytest.mark.parametrize('change,code', [({'known':False},'UNKNOWN'),({'visible':False},'HIDDEN'),({'fullscreen':False},'FULLSCREEN_EXITED'),({'foreground':False},'NOT_FOREGROUND'),({'focus_view_visible':False},'FOCUS_VIEW_HIDDEN')])
def test_windows_warnings_require_an_activated_focus_view(change,code):
    native=dict(known=True,visible=True,fullscreen=True,foreground=True,focus_view_visible=True);native.update(change)
    assert derive_warnings(state(),native)==[]
    assert derive_warnings(state(),native,windows_expected=True)[0].code==code


def test_alerts_are_deduplicated_and_restoration_is_not_faked_by_disconnect():
    tracker=DisplayWarningTracker();bad=state(fullscreen=False,display_mode='page')
    for device in bad['devices'].values():device['session_interrupted']=True
    assert len(tracker.update(bad)['new_warnings'])==1
    assert tracker.update(bad)['new_warnings']==[]
    gone=deepcopy(bad)
    for device in gone['devices'].values():device['joined']=False
    assert tracker.update(gone)['restored']==[]
    tracker.update(bad)
    assert set(tracker.update(state())['restored'])=={'IOS'}


def test_browser_tab_observation_alerts_without_claiming_unauthorized_switch():
    value=state();value['devices']['IOS'].update(integrity_state='BACKGROUND_REASON_UNKNOWN',integrity_reason='PAGE_EXIT_OBSERVED')
    warning=derive_warnings(value)[0]
    assert warning.code=='PAGE_EXIT_UNCONFIRMED' and 'unconfirmed' in warning.message
    value['devices']['IOS']['integrity_reason']='PAGE_HIDDEN'
    assert derive_warnings(value)[0].code=='PAGE_HIDDEN_UNCONFIRMED'


def test_removed_android_companion_cannot_raise_a_session_alert():
    value=state();value['devices']['ANDROID'].update(session_interrupted=True,integrity_state='APP_SWITCH_CONFIRMED')
    assert derive_warnings(value)==[]
