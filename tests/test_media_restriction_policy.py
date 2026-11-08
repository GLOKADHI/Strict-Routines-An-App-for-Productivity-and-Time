"""
StrictRecords – Comprehensive Media Restriction Policy & SUN NXT Schedule Test Suite
=====================================================================================
Covers all 21 test scenarios defined in Section 6 of the specification:
1. Open YouTube outside an entitlement -> Blocked
2. Play Netflix outside an entitlement -> Blocked
3. Start Spotify outside an entitlement -> Blocked
4. Play a local video/audio file -> Blocked
5. Start browser-based video -> Blocked
6. Minimize a restricted player -> Playback remains blocked
7. Start background audio -> Playback remains blocked
8. Activate picture-in-picture -> Playback remains blocked
9. Launch SUN NXT at 8:24 PM -> Blocked
10. Launch SUN NXT at 8:25 PM -> Allowed
11. Play SUN NXT at 9:00 PM -> Allowed
12. Play SUN NXT at 9:34 PM -> Allowed
13. Continue SUN NXT playback at 9:35 PM -> Automatically restricted
14. Refresh SUN NXT at 9:35 PM -> Still restricted
15. Restart SUN NXT after 9:35 PM -> Still restricted
16. Play YouTube during the SUN NXT allowance -> Blocked
17. Play Spotify during the SUN NXT allowance -> Blocked
18. Claim a valid authorized media reward -> Only its specified access is allowed
19. Use an expired reward -> Blocked
20. Restart the device during a reward -> Remaining entitlement is preserved; no reset
21. Change the device clock -> Does not extend the scheduled allowance
22. Open multiple browser tabs with restricted media -> All unauthorized players remain blocked
23. Essential OS audio / emergency calls -> Always allowed
24. Continuous playback violation deduplication
"""

import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta
import pytest

from core.media_restriction_policy import (
    ALLOWED_BY_REWARD,
    ALLOWED_BY_SUNNXT_SCHEDULE,
    ENTITLEMENT_EXPIRED,
    IST,
    MEDIA_ALLOWED,
    MEDIA_BLOCKED,
    STANDARD_RESTRICTION_MESSAGE,
    CentralizedMediaPolicyEngine,
    EntitlementLedger,
    is_sunnxt_target,
    is_sunnxt_window_active,
)


@pytest.fixture
def temp_ledger(tmp_path):
    ledger_file = str(tmp_path / "test_ledger.json")
    ledger = EntitlementLedger(persistence_file=ledger_file)
    return ledger, ledger_file


@pytest.fixture
def policy_engine(temp_ledger):
    ledger, _ = temp_ledger
    return CentralizedMediaPolicyEngine(ledger=ledger)


def make_ist_time(hour: int, minute: int, second: int = 0) -> datetime:
    """Creates an IST datetime for today at specified hour:minute:second."""
    now = datetime.now(IST)
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)


# Test 1: Open YouTube outside an entitlement -> Blocked
def test_01_youtube_outside_entitlement_blocked(policy_engine):
    t = make_ist_time(14, 0)  # 2:00 PM
    res = policy_engine.evaluate_playback("youtube.com", player_type="browser_video", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]
    assert STANDARD_RESTRICTION_MESSAGE in res["message"]


# Test 2: Play Netflix outside an entitlement -> Blocked
def test_02_netflix_outside_entitlement_blocked(policy_engine):
    t = make_ist_time(16, 30)  # 4:30 PM
    res = policy_engine.evaluate_playback("netflix.com", player_type="browser_video", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 3: Start Spotify outside an entitlement -> Blocked
def test_03_spotify_outside_entitlement_blocked(policy_engine):
    t = make_ist_time(10, 15)  # 10:15 AM
    res = policy_engine.evaluate_playback("open.spotify.com", player_type="browser_audio", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 4: Play a local video/audio file -> Blocked
def test_04_local_media_player_blocked(policy_engine):
    t = make_ist_time(15, 0)
    for player in ["vlc.exe", "wmplayer.exe", "mpc-hc64.exe", "potplayer.exe"]:
        res = policy_engine.evaluate_playback(player, player_type="local_player", now_dt=t)
        assert res["decision"] == MEDIA_BLOCKED
        assert not res["is_permitted"]


# Test 5: Start browser-based video -> Blocked
def test_05_browser_based_video_blocked(policy_engine):
    t = make_ist_time(11, 45)
    res = policy_engine.evaluate_playback("dailymotion.com", player_type="browser_video", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 6: Minimize a restricted player -> Playback remains blocked
def test_06_minimized_restricted_player_remains_blocked(policy_engine):
    t = make_ist_time(13, 0)
    res = policy_engine.evaluate_playback("spotify.exe", player_type="minimized_player", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 7: Start background audio -> Playback remains blocked
def test_08_background_audio_remains_blocked(policy_engine):
    t = make_ist_time(17, 20)
    res = policy_engine.evaluate_playback("com.spotify.music", player_type="background_audio", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 8: Activate picture-in-picture -> Playback remains blocked
def test_08_picture_in_picture_remains_blocked(policy_engine):
    t = make_ist_time(18, 0)
    res = policy_engine.evaluate_playback("picture-in-picture", player_type="pip", now_dt=t)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 9: Launch SUN NXT at 8:24 PM -> Blocked
def test_09_sunnxt_at_8_24_pm_blocked(policy_engine):
    t_824 = make_ist_time(20, 24, 59)
    assert not is_sunnxt_window_active(t_824)

    res_web = policy_engine.evaluate_playback("sunnxt.com", player_type="web", now_dt=t_824)
    assert res_web["decision"] == MEDIA_BLOCKED
    assert not res_web["is_permitted"]

    res_apk = policy_engine.evaluate_playback("com.suntv.sunnxt", player_type="android_app", now_dt=t_824)
    assert res_apk["decision"] == MEDIA_BLOCKED
    assert not res_apk["is_permitted"]


# Test 10: Launch SUN NXT at 8:25 PM -> Allowed
def test_10_sunnxt_at_8_25_pm_allowed(policy_engine):
    t_825 = make_ist_time(20, 25, 0)
    assert is_sunnxt_window_active(t_825)

    res_web = policy_engine.evaluate_playback("sunnxt.com", player_type="web", now_dt=t_825)
    assert res_web["decision"] == ALLOWED_BY_SUNNXT_SCHEDULE
    assert res_web["is_permitted"]
    assert res_web["remaining_seconds"] == 70 * 60

    res_apk = policy_engine.evaluate_playback("com.suntv.sunnxt", player_type="android_app", now_dt=t_825)
    assert res_apk["decision"] == ALLOWED_BY_SUNNXT_SCHEDULE
    assert res_apk["is_permitted"]


# Test 11: Play SUN NXT at 9:00 PM -> Allowed
def test_11_sunnxt_at_9_00_pm_allowed(policy_engine):
    t_900 = make_ist_time(21, 0, 0)
    assert is_sunnxt_window_active(t_900)

    res = policy_engine.evaluate_playback("https://www.sunnxt.com/movie/watch", player_type="web", now_dt=t_900)
    assert res["decision"] == ALLOWED_BY_SUNNXT_SCHEDULE
    assert res["is_permitted"]
    assert res["remaining_seconds"] == 35 * 60


# Test 12: Play SUN NXT at 9:34 PM -> Allowed
def test_12_sunnxt_at_9_34_pm_allowed(policy_engine):
    t_934 = make_ist_time(21, 34, 59)
    assert is_sunnxt_window_active(t_934)

    res = policy_engine.evaluate_playback("com.sunsnxt.app", player_type="android_app", now_dt=t_934)
    assert res["decision"] == ALLOWED_BY_SUNNXT_SCHEDULE
    assert res["is_permitted"]
    assert res["remaining_seconds"] == 1


# Test 13: Continue SUN NXT playback at 9:35 PM -> Automatically restricted
def test_13_sunnxt_at_9_35_pm_automatically_restricted(policy_engine):
    t_935 = make_ist_time(21, 35, 0)
    assert not is_sunnxt_window_active(t_935)

    res = policy_engine.evaluate_playback("sunnxt.com", player_type="web", now_dt=t_935)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 14: Refresh SUN NXT at 9:35 PM -> Still restricted
def test_14_refresh_sunnxt_at_9_35_pm_still_restricted(policy_engine):
    t_935_10 = make_ist_time(21, 35, 10)
    res = policy_engine.evaluate_playback("sunnxt.com", player_type="web", now_dt=t_935_10)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 15: Restart SUN NXT after 9:35 PM -> Still restricted
def test_15_restart_sunnxt_after_9_35_pm_still_restricted(policy_engine):
    t_940 = make_ist_time(21, 40, 0)
    res = policy_engine.evaluate_playback("com.suntv.sunnxt", player_type="android_app", now_dt=t_940)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 16: Play YouTube during the SUN NXT allowance -> Blocked
def test_16_youtube_during_sunnxt_allowance_blocked(policy_engine):
    t_830 = make_ist_time(20, 30, 0)
    assert is_sunnxt_window_active(t_830)

    # SUN NXT is allowed
    assert policy_engine.evaluate_playback("sunnxt.com", now_dt=t_830)["is_permitted"]

    # YouTube is STRICTLY BLOCKED even during SUN NXT window
    yt_res = policy_engine.evaluate_playback("youtube.com", now_dt=t_830)
    assert yt_res["decision"] == MEDIA_BLOCKED
    assert not yt_res["is_permitted"]
    assert "Only SUN NXT is permitted" in yt_res["reason"]


# Test 17: Play Spotify during the SUN NXT allowance -> Blocked
def test_17_spotify_during_sunnxt_allowance_blocked(policy_engine):
    t_830 = make_ist_time(20, 30, 0)

    # Spotify is STRICTLY BLOCKED even during SUN NXT window
    res = policy_engine.evaluate_playback("spotify.com", player_type="browser_audio", now_dt=t_830)
    assert res["decision"] == MEDIA_BLOCKED
    assert not res["is_permitted"]


# Test 18: Claim a valid authorized media reward -> Only its specified access is allowed
def test_18_valid_reward_claim_only_specified_access_allowed(policy_engine):
    t = make_ist_time(15, 0)
    # Grant reward for netflix.com for 30 minutes (1800 seconds)
    policy_engine.ledger.grant_entitlement(
        entitlement_id="reward_netflix_123",
        source="REWARD",
        authorized_target="netflix.com",
        duration_seconds=1800,
        now_dt=t,
    )

    # Netflix should now be allowed
    res_netflix = policy_engine.evaluate_playback("netflix.com", now_dt=t)
    assert res_netflix["decision"] == ALLOWED_BY_REWARD
    assert res_netflix["is_permitted"]
    assert 1790 <= res_netflix["remaining_seconds"] <= 1800

    # Other services (e.g. YouTube, Spotify) MUST remain blocked!
    res_yt = policy_engine.evaluate_playback("youtube.com", now_dt=t)
    assert res_yt["decision"] == MEDIA_BLOCKED
    assert not res_yt["is_permitted"]

    res_spot = policy_engine.evaluate_playback("spotify.com", now_dt=t)
    assert res_spot["decision"] == MEDIA_BLOCKED
    assert not res_spot["is_permitted"]


# Test 19: Use an expired reward -> Blocked
def test_19_expired_reward_blocked(policy_engine):
    t_start = make_ist_time(15, 0)
    policy_engine.ledger.grant_entitlement(
        entitlement_id="reward_exp_test",
        source="REWARD",
        authorized_target="youtube.com",
        duration_seconds=600,  # 10 minutes
        now_dt=t_start,
    )

    # At 15:05 (within 10m): Allowed
    t_mid = make_ist_time(15, 5)
    assert policy_engine.evaluate_playback("youtube.com", now_dt=t_mid)["is_permitted"]

    # At 15:11 (after 10m): Blocked / Expired
    t_exp = make_ist_time(15, 11)
    res_exp = policy_engine.evaluate_playback("youtube.com", now_dt=t_exp)
    assert res_exp["decision"] == ENTITLEMENT_EXPIRED
    assert not res_exp["is_permitted"]


# Test 20: Restart the device during a reward -> Remaining entitlement is preserved
def test_20_restart_device_during_reward_preserves_remaining_entitlement(temp_ledger):
    ledger, ledger_file = temp_ledger
    engine = CentralizedMediaPolicyEngine(ledger=ledger)

    t_start = make_ist_time(16, 0)
    engine.ledger.grant_entitlement(
        entitlement_id="reward_restart_test",
        source="REWARD",
        authorized_target="vlc.exe",
        duration_seconds=3600,  # 1 hour
        now_dt=t_start,
    )

    # Advance time by 20 minutes (1200 seconds)
    t_20min = make_ist_time(16, 20)

    # Simulate restart by creating a completely new ledger and engine pointing to the same file
    new_ledger = EntitlementLedger(persistence_file=ledger_file)
    new_engine = CentralizedMediaPolicyEngine(ledger=new_ledger)

    res = new_engine.evaluate_playback("vlc.exe", now_dt=t_20min)
    assert res["decision"] == ALLOWED_BY_REWARD
    assert res["is_permitted"]
    # Remaining time should be approximately 2400 seconds (40 mins), NOT reset to 3600
    assert 2390 <= res["remaining_seconds"] <= 2410


# Test 21: Change the device clock -> Does not extend the scheduled allowance
def test_21_clock_change_does_not_extend_scheduled_allowance(policy_engine):
    # During SUN NXT window at 21:00
    t_900 = make_ist_time(21, 0)
    res_1 = policy_engine.evaluate_playback("sunnxt.com", now_dt=t_900)
    assert res_1["is_permitted"]
    assert res_1["remaining_seconds"] == 35 * 60

    # User attempts to change clock backwards by 30 minutes to 20:30 to extend allowance
    t_fake_back = make_ist_time(20, 30)
    # Monotonic trusted clock prevents backwards extension
    trusted_now = policy_engine.ledger.get_trusted_now(t_fake_back)
    assert trusted_now >= t_900  # Enforces monotonic progression


# Test 22: Open multiple browser tabs with restricted media -> All unauthorized players remain blocked
def test_22_multiple_browser_tabs_unauthorized_players_blocked(policy_engine):
    t = make_ist_time(12, 0)
    tab_targets = [
        "https://www.youtube.com/watch?v=123",
        "https://www.netflix.com/browse",
        "https://open.spotify.com/track/456",
        "https://www.twitch.tv/streamer",
        "https://vimeo.com/789",
    ]
    for tab in tab_targets:
        res = policy_engine.evaluate_playback(tab, player_type="browser_tab", now_dt=t)
        assert res["decision"] == MEDIA_BLOCKED
        assert not res["is_permitted"]


# Test 23: Essential OS audio / alarms / emergency calls -> Always allowed
def test_23_essential_os_audio_and_alarms_never_blocked(policy_engine):
    t = make_ist_time(2, 0)  # 2:00 AM middle of night
    essential_targets = [
        "alarm",
        "com.android.deskclock",
        "emergency",
        "system_notification",
        "com.google.android.dialer",
        "audiodg.exe",
    ]
    for ess in essential_targets:
        res = policy_engine.evaluate_playback(ess, now_dt=t)
        assert res["decision"] == MEDIA_ALLOWED
        assert res["is_permitted"]


# Test 24: Continuous playback violation deduplication
def test_24_violation_deduplication(policy_engine):
    t = make_ist_time(14, 0)
    # Continuous playback calls for youtube.com
    for _ in range(5):
        res = policy_engine.evaluate_playback("youtube.com", device_id="desktop", now_dt=t)
        assert res["decision"] == MEDIA_BLOCKED

    # Check recent violations list: should contain only 1 deduplicated entry
    violations = policy_engine.get_recent_violations()
    matching = [v for v in violations if v["target"] == "youtube.com"]
    assert len(matching) == 1
