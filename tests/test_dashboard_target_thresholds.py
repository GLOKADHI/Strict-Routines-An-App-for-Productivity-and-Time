from types import SimpleNamespace

from windows.restriction_manager import YouTubeRestrictionManager


def test_target_presentation_reports_saved_thresholds_without_changing_score():
    settings = SimpleNamespace(reward_threshold=95, controlled_mode_threshold=75)
    manager = YouTubeRestrictionManager(db=SimpleNamespace(get_settings=lambda: settings))
    manager.current_score = -10
    targets = {target['id']: target for target in manager.configured_targets_status()}
    assert targets['youtube']['configuredThreshold'] == 95
    assert targets['instagram']['configuredThreshold'] == 95
    assert targets['spotify']['configuredThreshold'] == 75
    assert targets['masstamilan']['configuredThreshold'] == 75
    assert targets['spotify']['defaultThreshold'] == 60
    assert manager.current_score == -10
    assert settings.reward_threshold == 95 and settings.controlled_mode_threshold == 75


def test_target_presentation_does_not_substitute_defaults_if_settings_cannot_be_read():
    def unavailable():
        raise RuntimeError('settings unavailable')

    manager = YouTubeRestrictionManager(db=SimpleNamespace(get_settings=unavailable))
    assert all(target['configuredThreshold'] is None for target in manager.configured_targets_status())
