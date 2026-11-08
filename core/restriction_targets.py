"""Explicit browser-domain catalog shared with the local companion extension."""
import json
import os
import sys
from urllib.parse import urlsplit

DEFAULT_TARGETS = {
    "youtubemusic": {
        "name": "YouTube Music",
        "category": "Music/Audio",
        "domains": ["music.youtube.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "youtube": {
        "name": "YouTube",
        "category": "Social Video",
        "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "instagram": {
        "name": "Instagram",
        "category": "Social Video",
        "domains": ["instagram.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "spotify": {
        "name": "Spotify",
        "category": "Music/Audio",
        "domains": ["open.spotify.com", "spotify.com", "play.spotify.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "applemusic": {
        "name": "Apple Music",
        "category": "Music/Audio",
        "domains": ["music.apple.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "amazonmusic": {
        "name": "Amazon Music",
        "category": "Music/Audio",
        "domains": [
            "music.amazon.com",
            "music.amazon.in",
            "music.amazon.co.uk",
            "music.amazon.de",
            "music.amazon.fr",
            "music.amazon.ca",
            "music.amazon.co.jp",
            "music.amazon.es",
            "music.amazon.it",
            "music.amazon.com.au",
        ],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "soundcloud": {
        "name": "SoundCloud",
        "category": "Music/Audio",
        "domains": ["soundcloud.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "gaana": {
        "name": "Gaana",
        "category": "Music/Audio",
        "domains": ["gaana.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "jiosaavn": {
        "name": "JioSaavn",
        "category": "Music/Audio",
        "domains": ["jiosaavn.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "wynk": {
        "name": "Wynk Music",
        "category": "Music/Audio",
        "domains": ["wynk.in"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "tidal": {
        "name": "Tidal",
        "category": "Music/Audio",
        "domains": ["tidal.com", "listen.tidal.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "deezer": {
        "name": "Deezer",
        "category": "Music/Audio",
        "domains": ["deezer.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "pandora": {
        "name": "Pandora",
        "category": "Music/Audio",
        "domains": ["pandora.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "bandcamp": {
        "name": "Bandcamp",
        "category": "Music/Audio",
        "domains": ["bandcamp.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "masstamilan": {
        "name": "masstamilan.dev",
        "category": "Music/Website Target",
        "domains": ["masstamilan.dev", "masstamilan.org", "masstamilan.audio"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "hungama": {
        "name": "Hungama Music",
        "category": "Music/Audio",
        "domains": ["hungama.com"],
        "thresholdSetting": "controlled_mode_threshold",
        "defaultThreshold": 60,
        "enabled": True,
    },
    "netflix": {
        "name": "Netflix",
        "category": "Video streaming",
        "domains": ["netflix.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "facebook": {
        "name": "Facebook",
        "category": "Social Video",
        "domains": ["facebook.com", "fb.watch", "fb.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "twitter": {
        "name": "Twitter / X",
        "category": "Social Video",
        "domains": ["twitter.com", "x.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "tiktok": {
        "name": "TikTok",
        "category": "Social Video",
        "domains": ["tiktok.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "reddit": {
        "name": "Reddit",
        "category": "Social Video",
        "domains": ["reddit.com", "v.redd.it", "redd.it"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "twitch": {
        "name": "Twitch",
        "category": "Social Video",
        "domains": ["twitch.tv"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "vimeo": {
        "name": "Vimeo",
        "category": "Social Video",
        "domains": ["vimeo.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "dailymotion": {
        "name": "Dailymotion",
        "category": "Social Video",
        "domains": ["dailymotion.com"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
    "threads": {
        "name": "Threads",
        "category": "Social Video",
        "domains": ["threads.net"],
        "thresholdSetting": "reward_threshold",
        "defaultThreshold": 80,
        "enabled": True,
    },
}

def _load_targets():
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "browser_extension", "targets.json"))
        candidates.append(os.path.join(sys._MEIPASS, "targets.json"))
    this_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(this_dir), "browser_extension", "targets.json"))
    candidates.append(os.path.join(this_dir, "browser_extension", "targets.json"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "browser_extension", "targets.json"))
        candidates.append(os.path.join(exe_dir, "targets.json"))
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(os.path.join(local_app, "StrictRecords", "browser_extension", "targets.json"))

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as source:
                    data = json.load(source)
                    if isinstance(data, dict) and data:
                        merged = dict(DEFAULT_TARGETS)
                        merged.update(data)
                        return merged
            except Exception:
                pass
    return DEFAULT_TARGETS

TARGETS = _load_targets()


def target_for_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "http"):
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        # 1. Exact domain matches first (e.g. music.youtube.com before youtube.com)
        for key, spec in TARGETS.items():
            if spec.get("enabled", True):
                for domain in spec["domains"]:
                    if host == domain.lower():
                        return key
        # 2. Subdomain suffix matches, preferring longer/more specific domain match
        best_match = None
        best_len = 0
        for key, spec in TARGETS.items():
            if spec.get("enabled", True):
                for domain in spec["domains"]:
                    d = domain.lower()
                    if host.endswith("." + d) and len(d) > best_len:
                        best_match = key
                        best_len = len(d)
        return best_match
    except ValueError:
        return None


def threshold_for(key, settings=None):
    spec = TARGETS.get(key, {})
    setting_name = spec.get("thresholdSetting", "controlled_mode_threshold")
    default_val = spec.get("defaultThreshold", 60)
    return int(getattr(settings, setting_name, default_val))
