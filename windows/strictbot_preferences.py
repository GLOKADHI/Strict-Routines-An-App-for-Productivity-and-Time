"""Local Windows presentation preferences; never stores or grants an entitlement."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

DEFAULTS = dict(robot_size=100, popup_size=100, robot_position="left",
                popup_position="center", speed="normal", dim=22, transparency=96,
                walking=True, voice=False, monitor="application", grace_seconds=0.8,
                exclusions=[], daily_unblocks={})
_CACHE = {}
APPEARANCE_KEYS = tuple(key for key in DEFAULTS if key not in ('grace_seconds','exclusions','daily_unblocks'))


def appearance(values):
    return {key: values[key] for key in APPEARANCE_KEYS}


def merge_appearance(existing, changes):
    if not isinstance(changes, dict):
        raise ValueError('Settings must be an object')
    return normalize(dict(existing, **{key: value for key, value in changes.items() if key in APPEARANCE_KEYS}))


def normalize(values):
    result = dict(DEFAULTS)
    for key, lo, hi in (("robot_size", 40, 160), ("popup_size", 60, 150),
                        ("dim", 0, 65), ("transparency", 65, 100), ("grace_seconds", .2, 3)):
        try:
            result[key] = max(lo, min(hi, float(values.get(key, result[key]))))
        except (TypeError, ValueError):
            pass
    for key, choices in (("robot_position", ("left", "right", "automatic")),
                         ("popup_position", ("center", "upper-center")),
                         ("speed", ("slow", "normal", "fast")),
                         ("monitor", ("application", "primary", "cursor"))):
        if values.get(key) in choices:
            result[key] = values[key]
    for key in ("walking", "voice"):
        if isinstance(values.get(key), bool):
            result[key] = values[key]
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_unblocks = values.get("daily_unblocks", {})
    expired_apps = set()
    active_apps = set()
    if isinstance(daily_unblocks, dict):
        result["daily_unblocks"] = {
            str(k): [str(x).strip().lower() for x in v if str(x).strip().lower().endswith(".exe")]
            for k, v in daily_unblocks.items() if isinstance(v, list)
        }
        for date_str, apps in result["daily_unblocks"].items():
            if date_str == today_str:
                active_apps.update(apps)
            elif date_str < today_str:
                expired_apps.update(apps)
    else:
        result["daily_unblocks"] = {}

    raw_exclusions = values.get("exclusions", [])
    if isinstance(raw_exclusions, list):
        cleaned = [
            str(v).strip().lower() for v in raw_exclusions
            if str(v).strip().lower().endswith(".exe") and str(v).strip().lower() not in (expired_apps - active_apps)
        ]
        result["exclusions"] = cleaned[:100]

    for app in active_apps:
        if app not in result["exclusions"]:
            result["exclusions"].append(app)
    return result


class Preferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "StrictRecords" / "strictbot_pc.json"

    def load(self):
        cached = _CACHE.get(str(self.path))
        if cached and time.monotonic()-cached[0] < .8:
            return dict(cached[1])
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            result = normalize(values if isinstance(values, dict) else {})
        except (OSError, ValueError):
            result = normalize({})
        _CACHE[str(self.path)] = (time.monotonic(), result)
        return dict(result)

    def save(self, values):
        values = normalize(values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(".tmp")
        pending.write_text(json.dumps(values, indent=2), encoding="utf-8")
        os.replace(pending, self.path)
        _CACHE.pop(str(self.path), None)
        return values
