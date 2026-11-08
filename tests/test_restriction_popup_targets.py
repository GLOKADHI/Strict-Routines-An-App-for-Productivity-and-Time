"""Isolated restriction regressions: no production database, registry or window actions."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from core.restriction_targets import TARGETS, target_for_url, threshold_for
from windows.restriction_popup import EntrySession, bounded_geometry
from windows.restriction_manager import YouTubeRestrictionManager


def test_session_polling_and_concurrent_claims():
    session = EntrySession()
    token = session.observe(("spotify", 1, 100))
    with ThreadPoolExecutor(max_workers=12) as workers:
        assert sum(workers.map(lambda _: session.claim(token), range(100))) == 1
    assert session.observe(("spotify", 1, 100)) == token
    session.observe(None)
    again = session.observe(("spotify", 1, 100))
    assert again != token and session.claim(again)
    assert not session.claim(token)


def test_domains_are_exact_and_not_search_string_matches():
    for key, spec in TARGETS.items():
        for domain in spec["domains"]:
            assert target_for_url("https://" + domain) == key
            assert target_for_url("https://sub." + domain + "/") == key
            assert target_for_url("https://" + domain + ".evil.example/") is None
    for url in ["https://example.com/?url=masstamilan.dev", "https://notmasstamilan.dev", "file://masstamilan.dev", "https://masstamilan.dev@example.com"]:
        assert target_for_url(url) is None


def test_config_retains_existing_threshold_settings_and_no_samsung_windows_target():
    settings = SimpleNamespace(reward_threshold=80, controlled_mode_threshold=60)
    assert "samsung_music" not in TARGETS
    for key in ("spotify", "gaana", "jiosaavn", "masstamilan"):
        assert threshold_for(key, settings) == 60
    assert threshold_for("youtube", settings) == threshold_for("instagram", settings) == 80


def test_geometry_is_bounded_at_scaling_levels_and_secondary_displays():
    for scale in (1, 1.25, 1.5, 1.75, 2, 2.5):
        for bounds in [(0, 0, 1920, 1040), (0, 0, 800, 560), (-1280, 0, 0, 680), (1920, 0, 3000, 1800)]:
            w,h,x,y = bounded_geometry(bounds, scale, 460 * scale)
            assert w > 0 and h > 0
            assert bounds[0] <= x and bounds[1] <= y
            assert x+w <= bounds[2] and y+h <= bounds[3]


def test_tk_header_font_measurement_at_supported_scaling_levels():
    import tkinter as tk
    from tkinter.font import Font
    root = tk.Tk()
    root.withdraw()
    try:
        for scale in (1, 1.25, 1.5, 2):
            root.tk.call("tk", "scaling", scale * 96 / 72)
            brand = Font(root, family="Segoe UI", size=13, weight="bold")
            chip = Font(root, family="Segoe UI", size=10, weight="bold")
            w, *_ = bounded_geometry((0, 0, 1920, 1040), scale, 460 * scale)
            assert brand.measure("StrictBot") + chip.measure("APP RESTRICTED") + 56 < w
    finally:
        root.destroy()


class Facts:
    def get_daily_record(self,*args,**kwargs):return None
    def get_settings(self):
        return SimpleNamespace(reward_threshold=80, controlled_mode_threshold=60)


class Score:
    value = 0
    unavailable = False
    def recalculate_daily_score(self, date, **kwargs):
        if self.unavailable:
            raise RuntimeError("offline")
        return {"final_score": self.value}


def test_music_checks_current_confirmed_score_without_modifying_it():
    scorer = Score()
    manager = YouTubeRestrictionManager(db=Facts(), engine=scorer)
    for score in (-30, 0, 59, 60, 79, 80):
        scorer.value = score
        for key in ("spotify", "gaana", "jiosaavn", "masstamilan"):
            result = manager.get_browser_tab_restriction(key, actual_url="https://" + TARGETS[key]["domains"][0])
            assert result["restricted"] is (score < 60)
            assert result["score"] == score and result["requiredScore"] == 60
            assert scorer.value == score
    assert manager.get_browser_tab_restriction("masstamilan", actual_url="https://bank.example")['restricted'] is False


def test_points_failure_does_not_fabricate_a_score():
    scorer = Score()
    scorer.unavailable = True
    manager = YouTubeRestrictionManager(db=Facts(), engine=scorer)
    result = manager.get_browser_tab_restriction("spotify", actual_url="https://open.spotify.com")
    assert result["restricted"] and result["unavailable"] and result["score"] is None


def test_no_unverified_browser_close_or_keyboard_fallback():
    manager = YouTubeRestrictionManager(db=Facts(), engine=Score())
    with patch("windows.restriction_manager.HAS_WIN32", True), patch("windows.restriction_manager.win32gui") as gui:
        assert not manager.close_restricted_window(1, 100, "comet.exe", "CLOSE_TAB")
        gui.PostMessage.assert_not_called()
