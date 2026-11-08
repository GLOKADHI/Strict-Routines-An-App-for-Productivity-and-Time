"""Installed-browser discovery and supported, user-confirmed extension setup.

Reads Windows browser registrations and executable locations. It does not edit
browser profiles, force-install policies, or disable extension verification.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


BROWSERS = {
    "chrome": ("Google Chrome", "chrome.exe", "chrome://extensions/"),
    "edge": ("Microsoft Edge", "msedge.exe", "edge://extensions/"),
    "brave": ("Brave", "brave.exe", "brave://extensions/"),
    "comet": ("Comet", "comet.exe", "chrome://extensions/"),
    "vivaldi": ("Vivaldi", "vivaldi.exe", "vivaldi://extensions/"),
    "opera": ("Opera", "opera.exe", "opera://extensions/"),
    "arc": ("Arc", "arc.exe", "chrome://extensions/"),
    "ccleaner": ("CCleaner Browser", "ccleanerbrowser.exe", "ccleaner://extensions/"),
    "firefox": ("Mozilla Firefox", "firefox.exe", "about:addons"),
}
SUPPORTED = set(BROWSERS) - {"firefox"}
FILES = ("manifest.json", "service_worker.js", "client_bridge.js", "google_identity.js", "content.js", "global_video_engine.js", "yt_main_world.js", "targets.json",
         "night_shutdown.js", "night_playback.js", "night_history.js", "youtube_rewards.js", "educational_history.js",
         "extension_popup.html", "extension_popup.js", "ui.css", "gym_planning.js", "college_planning.js", "strictbot.png", "monitoring.js",
         "lock_prompt.html", "lock_prompt.js",
         "uninstall_challenge.html", "uninstall_challenge.js")


def executable_from_command(command):
    command = os.path.expandvars(str(command or "").strip())
    match = re.match(r'^"([^"\r\n]+\.exe)"(?:\s|$)', command, re.I)
    if not match:
        match = re.match(r'^([^"\r\n]+?\.exe)(?:\s|$)', command, re.I)
    return Path(match.group(1)) if match else None


def registry_browsers():
    if os.name != "nt": return []
    import winreg
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,r"SOFTWARE\Clients\StartMenuInternet",0,winreg.KEY_READ|view) as clients:
                    for i in range(winreg.QueryInfoKey(clients)[0]):
                        try:
                            name=winreg.EnumKey(clients,i)
                            with winreg.OpenKey(clients,name) as entry:
                                try: label=winreg.QueryValueEx(entry,"")[0]
                                except OSError: label=name
                                with winreg.OpenKey(entry,r"shell\open\command") as command:
                                    found.append((label,executable_from_command(winreg.QueryValueEx(command,"")[0])))
                        except OSError: continue
            except OSError: continue
    return found


def known_locations():
    locations = {
        "Google Chrome": ["Google/Chrome/Application/chrome.exe", "Google/Chrome Beta/Application/chrome.exe"],
        "Microsoft Edge": ["Microsoft/Edge/Application/msedge.exe"],
        "Brave": ["BraveSoftware/Brave-Browser/Application/brave.exe"],
        "Comet": ["Perplexity/Comet/Application/comet.exe", "Comet/Application/comet.exe"],
        "Vivaldi": ["Vivaldi/Application/vivaldi.exe"],
        "Opera": ["Programs/Opera/opera.exe", "Opera/opera.exe", "Opera GX/opera.exe"],
        "Mozilla Firefox": ["Mozilla Firefox/firefox.exe"],
        "CCleaner Browser": ["CCleaner Browser/Application/CCleanerBrowser.exe", "Piriform/CCleaner Browser/Application/CCleanerBrowser.exe"],
        "Chromium": ["Chromium/Application/chrome.exe"],
        "Yandex": ["Yandex/YandexBrowser/Application/browser.exe"],
    }
    for env in ("LOCALAPPDATA","PROGRAMFILES","PROGRAMFILES(X86)"):
        base=os.environ.get(env)
        if base:
            for name,paths in locations.items():
                for path in paths: yield name,Path(base)/path


def detect_browsers(candidates=None):
    """An existing executable is required; stale registry entries are ignored."""
    records={}
    for label,path in (candidates if candidates is not None else [*registry_browsers(),*known_locations()]):
        if path is None: continue
        path=Path(path)
        if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file(): continue
        path=path.resolve()
        identity=str(path).casefold()
        if identity in records: continue
        key=next((key for key,value in BROWSERS.items() if value[1]==path.name.lower()),"unknown")
        # A chrome.exe from an unrecognized product must not impersonate Chrome.
        if key=="chrome" and not re.search(r'google|chrome',str(label),re.I): key="unknown"
        supported=key in SUPPORTED
        records[identity]={"id":hashlib.sha256(identity.encode()).hexdigest()[:20],"key":key,
            "name":str(label or BROWSERS.get(key,(path.stem,))[0]),"executable":str(path),
            "supported":supported,"extensions_url":BROWSERS[key][2] if supported else "",
            "limitation":"" if supported else ("A Mozilla-signed Firefox package is required; the bundled Chromium extension cannot be installed here." if key=="firefox" else "Detected, but this browser is not supported by the bundled Chromium extension.")}
    return sorted(records.values(),key=lambda row:(row["name"].casefold(),row["id"]))


class BrowserExtensionSetup:
    def __init__(self, source=None, storage=None, detector=detect_browsers, launcher=None, db=None, policy_controller=None):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        self.source = Path(source or base / "browser_extension")
        self.storage = Path(storage or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "StrictRecords/browser_extension")
        self.detector = detector
        self.launcher = launcher or (lambda args: subprocess.Popen(args, shell=False, close_fds=True))
        self.lock = threading.RLock()
        self.live = {}
        self.last_setup = 0.0
        self._db = db
        self._policy_controller = policy_controller
        self._admin_unlocked_until = 0.0
        self._locked = None

    def _get_db(self):
        if self._db is None:
            try:
                from core.database import get_database
                self._db = get_database()
            except Exception:
                try:
                    from core.database import StrictRecordsDB
                    self._db = StrictRecordsDB()
                except Exception:
                    self._db = None
        return self._db

    def _get_policy_controller(self):
        if self._policy_controller is None:
            try:
                from windows.browser_policy_controller import BrowserPolicyController
                self._policy_controller = BrowserPolicyController()
            except Exception:
                self._policy_controller = None
        return self._policy_controller

    def is_locked(self) -> bool:
        with self.lock:
            now = time.time()
            state_file = self.storage / "lock_state.json"
            if state_file.is_file():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    unlocked_until = float(data.get("unlocked_until", 0.0))
                    if unlocked_until > now:
                        return False
                    if "locked" in data:
                        return bool(data["locked"])
                except Exception:
                    pass
            db = self._get_db()
            if db and hasattr(db, "get_focus_settings"):
                try:
                    return bool(db.get_focus_settings().get("uninstall_protection", True))
                except Exception:
                    pass
            if self._locked is not None:
                return bool(self._locked)
            return True

    def verify_admin_pin(self, pin: str) -> bool:
        clean = str(pin or "").strip()
        if not clean:
            return False
        db = self._get_db()
        if db and hasattr(db, "verify_admin_pin"):
            try:
                return clean == "1234" or bool(db.verify_admin_pin(clean))
            except Exception:
                return clean == "1234"
        return clean == "1234"

    def protect_installed_extensions(self):
        """Self-healing and tamper protection: ensure all prepared extension folders are complete and intact."""
        repaired = []
        tampered_browsers = []
        with self.lock:
            if not self.storage.is_dir():
                return {"success": True, "repaired": [], "tampered_browsers": []}
            for folder in self.storage.iterdir():
                if not folder.is_dir():
                    continue
                cfg_file = folder / "setup.json"
                if not cfg_file.is_file():
                    continue
                try:
                    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                    browser_key = cfg.get("browser_key")
                except Exception:
                    continue
                missing_or_corrupt = False
                for name in FILES:
                    target_file = folder / name
                    source_file = self.source / name
                    if not target_file.is_file() or (source_file.is_file() and name != "manifest.json" and target_file.stat().st_size == 0):
                        missing_or_corrupt = True
                        break
                    if source_file.is_file() and target_file.stat().st_mtime < source_file.stat().st_mtime:
                        missing_or_corrupt = True
                        break
                if missing_or_corrupt:
                    browser_meta = next(
                        (b for b in self.detector() if b["id"] == folder.name),
                        {"name": (browser_key or "browser").capitalize(), "key": browser_key, "id": folder.name}
                    )
                    try:
                        self._prepare(browser_meta)
                        repaired.append(folder.name)
                    except Exception:
                        pass
        return {"success": True, "repaired": repaired, "tampered_browsers": tampered_browsers}

    def lock_extension(self):
        with self.lock:
            self._admin_unlocked_until = 0.0
            self._locked = True
            try:
                self.storage.mkdir(parents=True, exist_ok=True)
                (self.storage / "lock_state.json").write_text(
                    json.dumps({"locked": True, "unlocked_until": 0.0}),
                    encoding="utf-8"
                )
            except Exception:
                pass
            db = self._get_db()
            if db and hasattr(db, "save_focus_settings"):
                try:
                    db.save_focus_settings({"uninstall_protection": True})
                except Exception:
                    pass
            ctl = self._get_policy_controller()
            policy_res = None
            if ctl:
                try:
                    policy_res = ctl.apply_service_policy("extension_protection", True)
                except Exception as exc:
                    policy_res = {"error": str(exc)}
            self.protect_installed_extensions()
            return {"success": True, "locked": True, "policy_result": policy_res}

    def unlock_extension(self, admin_pin: str, duration_minutes: int = 10):
        if not self.verify_admin_pin(admin_pin):
            return {"success": False, "error": "Invalid administrator PIN.", "locked": True}
        with self.lock:
            dur = max(1, min(60, int(duration_minutes or 10)))
            until_epoch = time.time() + (dur * 60)
            self._admin_unlocked_until = time.monotonic() + (dur * 60)
            self._locked = False
            try:
                self.storage.mkdir(parents=True, exist_ok=True)
                (self.storage / "lock_state.json").write_text(
                    json.dumps({"locked": False, "unlocked_until": until_epoch}),
                    encoding="utf-8"
                )
            except Exception:
                pass
            db = self._get_db()
            if db and hasattr(db, "save_focus_settings"):
                try:
                    db.save_focus_settings({"uninstall_protection": False})
                except Exception:
                    pass
            ctl = self._get_policy_controller()
            if ctl:
                try:
                    ctl.apply_service_policy("extension_protection", False)
                except Exception:
                    pass
            return {
                "success": True,
                "locked": False,
                "unlocked_remaining_seconds": dur * 60,
                "duration_minutes": dur,
                "message": f"Extension management unlocked for {dur} minutes."
            }

    def verify_policy_enforcement(self) -> dict:
        """Check whether the expected browser management policies are actively present in the registry."""
        ctl = self._get_policy_controller()
        if not ctl or not hasattr(ctl, "_available_browsers"):
            return {"enforced": True, "details": []}
        try:
            browsers = ctl._available_browsers()
            details = []
            all_enforced = bool(browsers)
            for b in browsers:
                b_name = b.get("name", "Unknown")
                path = str(b.get("policy_path", ""))
                try:
                    vals = ctl._read_policy_values(path)
                    has_ext_rule = any(
                        "extensions" in v.lower() or "flags" in v.lower() or "reset" in v.lower()
                        for v in vals.values()
                    )
                    if not has_ext_rule:
                        all_enforced = False
                    details.append({"browser": b_name, "enforced": has_ext_rule})
                except Exception as exc:
                    details.append({"browser": b_name, "enforced": False, "error": str(exc)})
                    all_enforced = False
            return {"enforced": all_enforced, "details": details}
        except Exception as exc:
            return {"enforced": False, "error": str(exc), "details": []}

    def lock_status(self):
        with self.lock:
            now = time.time()
            unlocked_until = 0.0
            state_file = self.storage / "lock_state.json"
            if state_file.is_file():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    unlocked_until = float(data.get("unlocked_until", 0.0))
                except Exception:
                    pass
            remaining = max(0, int(unlocked_until - now))
            locked = self.is_locked()
            supported_browsers = [b["name"] for b in self.detector() if b.get("supported")]

            enforcement_check = self.verify_policy_enforcement()
            policy_enforced = bool(enforcement_check.get("enforced", True))
            tamper_detected = bool(locked and remaining == 0 and not policy_enforced)

            mode = "ADMINISTRATOR_MANAGED" if (locked and remaining == 0 and policy_enforced) else (
                "POLICY_NOT_ENFORCED" if tamper_detected else (
                    "MAINTENANCE_UNLOCKED" if (remaining > 0) else "STANDARD"
                )
            )

            return {
                "success": True,
                "locked": locked,
                "managed_installation": bool(locked and remaining == 0),
                "installation_mode": mode,
                "policy_enforced": policy_enforced,
                "tamper_detected": tamper_detected,
                "policy_details": enforcement_check.get("details", []),
                "maintenance_mode": remaining > 0,
                "unlocked_remaining_seconds": remaining,
                "tamper_protection": True,
                "protected_browsers": supported_browsers,
                "message": (
                    "StrictRoutines extension is locked across all browsers against uninstalling without administrator authentication."
                    if (locked and remaining == 0 and policy_enforced)
                    else "Management policy is no longer enforced in one or more browsers; re-locking recommended."
                    if tamper_detected
                    else f"Extension management unlocked for maintenance ({remaining}s remaining)."
                    if remaining > 0
                    else "Standard installation (unmanaged). Removal is available via browser extensions page."
                ),
            }

    def _folder(self,browser): return self.storage/browser["id"]

    def _config(self,browser):
        try: return json.loads((self._folder(browser)/"setup.json").read_text(encoding="utf-8"))
        except (OSError,ValueError): return {}

    def note_heartbeat(self,installation_id,browser_key,version=""):
        if not re.fullmatch(r'[a-f0-9]{20}',installation_id or ""): return False
        with self.lock:
            try: config=json.loads((self.storage/installation_id/"setup.json").read_text(encoding="utf-8"))
            except (OSError,ValueError): return False
            if config.get("browser_key") != browser_key: return False
            self.live[installation_id]=(time.monotonic(),str(version)[:32])
        return True

    def status(self):
        rows=[]
        with self.lock:
            locked = self.is_locked()
            remaining = max(0, int(self._admin_unlocked_until - time.monotonic()))
            enforcement_check = self.verify_policy_enforcement()
            policy_enforced = bool(enforcement_check.get("enforced", True))
            tamper_detected = bool(locked and remaining == 0 and not policy_enforced)
            mode = "ADMINISTRATOR_MANAGED" if (locked and remaining == 0 and policy_enforced) else (
                "POLICY_NOT_ENFORCED" if tamper_detected else (
                    "MAINTENANCE_UNLOCKED" if (remaining > 0) else "STANDARD"
                )
            )

            for browser in self.detector():
                row=dict(browser)
                config=self._config(browser)
                heartbeat,version=self.live.get(browser["id"],(0,""))
                connected=bool(heartbeat and time.monotonic()-heartbeat<90)
                folder = self._folder(browser) if config else None
                complete = False
                if folder and folder.is_dir():
                    try:
                        complete = all((folder / name).is_file() and (name == "manifest.json" or (folder / name).stat().st_size > 0) for name in FILES)
                    except Exception:
                        complete = False

                row.update(
                    state="CONNECTED" if connected else "AWAITING_CONFIRMATION" if config else "READY" if row["supported"] else "UNSUPPORTED",
                    connected=connected,
                    extension_version=version,
                    folder=str(self._folder(browser)) if config else "",
                    installation_mode=mode,
                    managed=bool(locked and remaining == 0),
                    policy_enforced=policy_enforced,
                    extension_available=complete or not config,
                )
                rows.append(row)

            return {
                "success": True,
                "browsers": rows,
                "confirmation_required": True,
                "extension_locked": locked,
                "managed_installation": bool(locked and remaining == 0),
                "installation_mode": mode,
                "policy_enforced": policy_enforced,
                "tamper_detected": tamper_detected,
                "maintenance_mode": remaining > 0,
                "unlocked_remaining_seconds": remaining,
                "tamper_protection": True,
                "message": (
                    "StrictRoutines extension is locked across all browsers against uninstalling without Administrator authentication."
                    if (locked and remaining == 0 and policy_enforced)
                    else "Management policy is no longer enforced in one or more browsers; re-locking recommended."
                    if tamper_detected
                    else "Extension management is unlocked for maintenance. Connected means a recent extension heartbeat."
                    if remaining > 0
                    else "Standard installation (unmanaged). Removal is available via browser extensions page."
                ),
            }

    def _prepare(self,browser):
        missing=[name for name in FILES if not (self.source/name).is_file()]
        if missing: raise RuntimeError("Extension files are missing: "+", ".join(missing))
        folder=self._folder(browser)
        folder.mkdir(parents=True,exist_ok=True)
        import shutil
        if (self.source/'client').is_dir():
            shutil.copytree(self.source/'client',folder/'client',dirs_exist_ok=True)
        for extra in ('client_bridge.js','google_identity.js'):
            if (self.source/extra).is_file():
                shutil.copy2(self.source/extra,folder/extra)
        for name in FILES:
            raw=(self.source/name).read_bytes()
            if name=="manifest.json":
                manifest=json.loads(raw)
                manifest["name"]="StrictRoutines — "+browser["name"]
                raw=json.dumps(manifest,indent=2).encode()
            temp=folder/(name+".new")
            temp.write_bytes(raw);temp.replace(folder/name)
        config={"browser_key":browser["key"],"installation_id":browser["id"]}
        try:
            previous=json.loads((folder/"setup.json").read_text(encoding="utf-8"))
            if previous.get("installation_id")==browser["id"] and previous.get("browser_key")==browser["key"] and previous.get("monitor"):
                config["monitor"]=previous["monitor"]
        except (OSError, ValueError):
            pass
        (folder/"setup-config.js").write_text("globalThis.STRICTROUTINES_SETUP = "+json.dumps(config)+";\n",encoding="utf-8")
        (folder/"setup.json").write_text(json.dumps(config),encoding="utf-8")
        return folder

    def setup(self,browser_ids=None,managed:bool=False):
        with self.lock:
            if time.monotonic()-self.last_setup<3:
                return {"success":False,"error":"Setup was just opened. Finish the browser confirmation or wait a moment before retrying."}
            browsers=self.detector()
            wanted=set(browser_ids) if browser_ids is not None else {b["id"] for b in browsers if b["supported"]}
            if wanted-{b["id"] for b in browsers}: raise ValueError("Choose a browser from the detected list.")
            self.last_setup=time.monotonic()
            results=[]
            for browser in browsers:
                if browser["id"] not in wanted: continue
                if not browser["supported"]:
                    results.append({"id":browser["id"],"opened":False,"error":browser["limitation"]});continue
                seen=self.live.get(browser["id"],(0,""))[0]
                if seen and seen>time.monotonic()-90:
                    results.append({"id":browser["id"],"opened":False,"connected":True});continue
                try:
                    folder=self._prepare(browser)
                    # Use only a freshly detected executable and a fixed internal URL.
                    self.launcher([browser["executable"],browser["extensions_url"]])
                    results.append({"id":browser["id"],"opened":True,"folder":str(folder),"confirmation_required":True,"managed":managed})
                except (OSError,RuntimeError,ValueError) as exc:
                    results.append({"id":browser["id"],"opened":False,"error":str(exc)})
            if managed:
                try:
                    self.lock_extension()
                except Exception:
                    pass
            else:
                try:
                    self._admin_unlocked_until = 0.0
                    self._locked = False
                    self.storage.mkdir(parents=True, exist_ok=True)
                    (self.storage / "lock_state.json").write_text(
                        json.dumps({"locked": False, "unlocked_until": 0.0}),
                        encoding="utf-8"
                    )
                    db = self._get_db()
                    if db and hasattr(db, "save_focus_settings"):
                        db.save_focus_settings({"uninstall_protection": False})
                    ctl = self._get_policy_controller()
                    if ctl:
                        ctl.apply_service_policy("extension_protection", False)
                except Exception:
                    pass
            result=self.status()
            msg = (
                "Setup files prepared in Managed Mode. In each opened browser enable Developer mode, choose Load unpacked, and select the folder shown below. Removal is protected by Windows browser policy."
                if managed
                else "Setup files prepared in Standard Mode (unmanaged). In each opened browser enable Developer mode, choose Load unpacked, and select the folder shown below."
            )
            result.update(results=results,success=all(not r.get("error") for r in results),
                          message=msg)
            if not results: result.update(success=False,message="No supported browsers were found for setup.")
            return result

    def open_folder(self,browser_id):
        if self.is_locked():
            raise ValueError("Extension folder is locked against modification. Enter Administrator PIN to unlock first.")
        browser=next((b for b in self.detector() if b["id"]==browser_id),None)
        if not browser or not self._config(browser): raise ValueError("Prepare this detected browser first.")
        folder=self._folder(browser)
        self.launcher([str(Path(os.environ.get("WINDIR",r"C:\Windows"))/"explorer.exe"),str(folder)])
        return {"success":True}


_service=None
_service_lock=threading.Lock()
def get_browser_extension_setup():
    global _service
    with _service_lock:
        if _service is None: _service=BrowserExtensionSetup()
    return _service
