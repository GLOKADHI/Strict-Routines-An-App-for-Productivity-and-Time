"""Source boundary checks; behavioral cases run in Kotlin and emulator instrumentation."""
from pathlib import Path

SOURCE=Path('android/app/src/main/java/com/strictrecords/app')


def test_media_callbacks_cannot_create_a_second_popup_or_write_points():
    text=(SOURCE/'BackgroundMusicRestrictionManager.kt').read_text(encoding='utf-8-sig')
    assert 'showStrictBotPopup' not in text
    for forbidden in ('addScoreEvent(', 'insertScoreEvent(', 'execSQL(', 'requestAudioFocus(', 'setStreamVolume(', 'dispatchMediaKeyEvent('):
        assert forbidden not in text
    assert 'Intent(Intent.ACTION_MEDIA_BUTTON).setPackage(packageName)' in text


def test_selected_external_session_scope_is_checked_before_controls():
    text=(SOURCE/'BackgroundMusicRestrictionManager.kt').read_text(encoding='utf-8-sig')
    assert 'pkg != context.packageName && isMonitoredPlaybackPackage(pkg)' in text
    assert 'TargetAppMonitoringPolicy.mayMonitor(context, pkg)' in text
    assert 'mutableMapOf<MediaSession.Token, Subscription>()' in text
    assert 'registerCallback(callback, worker)' in text
    assert 'controller.transportControls.pause()' in text
    assert 'controller.transportControls.stop()' in text


def test_financial_guard_precedes_transition_classification():
    text=(SOURCE/'StrictAccessibilityService.kt').read_text(encoding='utf-8-sig')
    event=text[text.index('val eventPkg = event.packageName?.toString()'):]
    assert event.index('FinancialSafePause.isProtectedFinancialPackage(this, eventPkg)') < event.index('ForegroundWindowRules.isApplicationTransition(')
    assert 'if (transition)' in event
    assert 'keepPresentedTarget = StrictBotOverlayController.ownsOverlayForeground(packageEvents.packageName)' in text


def test_media_listener_never_reads_notification_content():
    text=(SOURCE/'StrictMediaNotificationListener.kt').read_text(encoding='utf-8-sig')
    for forbidden in ('sbn.notification', 'sbn?.notification', '.extras', 'getText(', 'getRootInActiveWindow('):
        assert forbidden not in text
    assert 'isMonitoredPlaybackPackage(pkg)' in text


def test_both_popup_surfaces_pass_the_exact_restricted_package():
    overlay=(SOURCE/'StrictBotOverlayController.kt').read_text(encoding='utf-8-sig')
    fallback=(SOURCE/'RestrictionLockActivity.kt').read_text(encoding='utf-8-sig')
    assert 'pauseMediaAudio(hostContext, targetPackage)' in overlay
    assert 'pauseMediaAudio(context, targetPackage)' in overlay
    assert fallback.count('pauseMediaAudio(this, targetPkgName)')==2
    assert 'requestAudioFocus(' not in overlay
