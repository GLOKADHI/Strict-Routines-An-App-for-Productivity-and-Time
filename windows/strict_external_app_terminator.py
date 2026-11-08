"""
STRICTRECORDS – STRICT SESSION: AUTOMATIC EXTERNAL APPLICATION TERMINATOR
Enhanced Windows desktop application shutdown & distraction enforcement engine.

Categorized application detection, identity verification, graceful shutdown (WM_CLOSE),
controlled process termination, and continuous session-launch monitoring.

Categories:
A. Browsers (Chrome, Edge [msedge.exe], Firefox, Brave, Opera, Vivaldi, etc.)
B. Entertainment Applications (Netflix, Disney+, OTT, Discord, Telegram, WhatsApp, Slack, etc.)
C. Games (Steam, Epic Games, Xbox, Riot, Battle.net, EA, etc.)
D. Video & Music Players (VLC, Windows Media Player, Spotify, iTunes, PotPlayer, foobar2000, etc.)

Protected:
- Windows system & shell processes (explorer.exe, dwm.exe, svchost.exe, etc.)
- Antivirus & security services (msmpeng.exe, etc.)
- Hardware, audio, network, bluetooth & accessibility services
- StrictRecords EXE & dedicated study browser (msedgewebview2.exe owned by StrictRecords)
"""

import ctypes
import ctypes.wintypes
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("StrictAppTerminator")

# ---------------------------------------------------------------------------
# CATEGORY CATALOGS (Process Executable Names & Display Names)
# ---------------------------------------------------------------------------

BROWSERS: Dict[str, str] = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "opera_gx.exe": "Opera GX",
    "vivaldi.exe": "Vivaldi",
    "arc.exe": "Arc",
    "chromium.exe": "Chromium",
    "yandex.exe": "Yandex Browser",
    "tor.exe": "Tor Browser",
    "waterfox.exe": "Waterfox",
    "floorp.exe": "Floorp",
    "librewolf.exe": "LibreWolf",
    "sidekick.exe": "Sidekick",
    "epic.exe": "Epic Privacy Browser",
    "maxthon.exe": "Maxthon",
    "ucbrowser.exe": "UC Browser",
    "ccleanerbrowser.exe": "CCleaner Browser",
    "avastbrowser.exe": "Avast Secure Browser",
}

ENTERTAINMENT_APPS: Dict[str, str] = {
    "netflix.exe": "Netflix",
    "disney.exe": "Disney+",
    "disneyplus.exe": "Disney+",
    "primevideo.exe": "Prime Video",
    "amazonvideo.exe": "Prime Video",
    "hulu.exe": "Hulu",
    "hotstar.exe": "Disney+ Hotstar",
    "crunchyroll.exe": "Crunchyroll",
    "stremio.exe": "Stremio",
    "kodi.exe": "Kodi",
    "plex.exe": "Plex",
    "plexmediaplayer.exe": "Plex Media Player",
    "popcorntime.exe": "Popcorn Time",
    "discord.exe": "Discord",
    "discordcanary.exe": "Discord Canary",
    "discordptb.exe": "Discord PTB",
    "telegram.exe": "Telegram",
    "whatsapp.exe": "WhatsApp",
    "slack.exe": "Slack",
    "signal.exe": "Signal",
    "tiktok.exe": "TikTok",
    "instagram.exe": "Instagram",
    "skype.exe": "Skype",
    "viber.exe": "Viber",
    "line.exe": "LINE",
    "wechat.exe": "WeChat",
}

GAMES: Dict[str, str] = {
    "steam.exe": "Steam",
    "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games Launcher",
    "epicwebhelper.exe": "Epic Games",
    "xboxapp.exe": "Xbox",
    "gamingapp.exe": "Xbox Gaming App",
    "xboxpcapp.exe": "Xbox App",
    "xboxtcui.exe": "Xbox Live UI",
    "xboxgamebar.exe": "Xbox Game Bar",
    "xboxgamebarft.exe": "Xbox Game Bar",
    "battle.net.exe": "Battle.net",
    "riotclientux.exe": "Riot Client",
    "riotclientservices.exe": "Riot Client Services",
    "leagueclient.exe": "League of Legends",
    "valorant.exe": "VALORANT",
    "origin.exe": "Origin",
    "eadesktop.exe": "EA Desktop",
    "eaconnect_mounter.exe": "EA Connect",
    "ubisoftconnect.exe": "Ubisoft Connect",
    "upc.exe": "Ubisoft Connect",
    "gog_galaxy.exe": "GOG Galaxy",
    "galaxyclient.exe": "GOG Galaxy Client",
    "minecraft.exe": "Minecraft",
    "robloxplayerbeta.exe": "Roblox Player",
    "robloxplayerlauncher.exe": "Roblox Launcher",
    "robloxstudiobeta.exe": "Roblox Studio",
}

VIDEO_MUSIC_PLAYERS: Dict[str, str] = {
    "vlc.exe": "VLC Media Player",
    "wmplayer.exe": "Windows Media Player",
    "microsoft.media.player.exe": "Windows Media Player",
    "spotify.exe": "Spotify",
    "itunes.exe": "iTunes",
    "foobar2000.exe": "foobar2000",
    "aimp.exe": "AIMP",
    "potplayer.exe": "PotPlayer",
    "potplayermini.exe": "PotPlayer",
    "potplayermini64.exe": "PotPlayer",
    "kmplayer.exe": "KMPlayer",
    "kmplayer64.exe": "KMPlayer",
    "mpv.exe": "mpv",
    "winamp.exe": "Winamp",
    "musicbee.exe": "MusicBee",
    "audacious.exe": "Audacious",
    "gom.exe": "GOM Player",
    "gomp.exe": "GOM Player",
    "mediamonkey.exe": "MediaMonkey",
    "cplay.exe": "cPlay",
    "video.ui.exe": "Movies & TV",
    "music.ui.exe": "Groove Music",
}

CATEGORY_MAP = {
    "BROWSERS": ("Browsers", BROWSERS),
    "ENTERTAINMENT": ("Entertainment & Social Media", ENTERTAINMENT_APPS),
    "GAMES": ("Games", GAMES),
    "MEDIA_PLAYERS": ("Video & Music Players", VIDEO_MUSIC_PLAYERS),
}

ALL_PROHIBITED_NAMES: Set[str] = (
    set(BROWSERS.keys()) | set(ENTERTAINMENT_APPS.keys()) | set(GAMES.keys()) | set(VIDEO_MUSIC_PLAYERS.keys())
)

# ---------------------------------------------------------------------------
# PROTECTED PROCESSES (Never Terminate Under Any Circumstances)
# ---------------------------------------------------------------------------

PROTECTED_PROCESS_NAMES: Set[str] = {
    # Windows Kernel & Session Manager
    "system", "registry", "idle", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "sihost.exe", "fontdrvhost.exe",
    "spoolsv.exe", "runtimebroker.exe", "ctfmon.exe", "conhost.exe", "dllhost.exe",
    "taskhostw.exe", "wlanext.exe", "dasHost.exe",
    # Windows Shell & Taskbar
    "explorer.exe", "taskmgr.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "searchui.exe", "textinputhost.exe", "applicationframehost.exe",
    # Antivirus & Windows Security
    "securityhealthservice.exe", "securityhealthsystray.exe", "msmpeng.exe", "mssense.exe",
    "avastsvc.exe", "avp.exe", "nortonsecurity.exe", "mbam.exe", "bdservicehost.exe",
    # Hardware & Audio Engine
    "audiodg.exe", "nvcontainer.exe", "nvdisplay.container.exe", "amdfendrs.exe",
    # Accessibility Services
    "utilman.exe", "narrator.exe", "magnify.exe", "osk.exe", "nvda.exe", "jfw.exe", "voiceaccess.exe",
    # Credential & Security Handlers
    "lockapp.exe", "logonui.exe", "consent.exe", "credentialuibroker.exe",
    # Password Managers
    "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe",
    # StrictRecords Suite
    "strictrecords.exe", "strictrecordsapp.exe", "strictroutines.exe",
    "strictrecordsdesktop.exe", "examproctor.exe", "strictrecordsnativebridge.exe",
    "python.exe", "pythonw.exe",
}

SAFE_PATH_KEYWORDS = ("system32", "syswow64", "windows defender", "antivirus", "security", "driver")


def get_windows_session_id(pid: int) -> Optional[int]:
    """Returns the Windows Terminal Session ID for a given process."""
    if os.name != "nt":
        return 0
    try:
        val = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(val)):
            return val.value
    except Exception:
        pass
    return None


def is_process_ancestor(ancestor_pid: int, child_pid: int) -> bool:
    """Checks whether ancestor_pid is an ancestor of child_pid."""
    if ancestor_pid == child_pid:
        return True
    try:
        import psutil
        cur = psutil.Process(child_pid)
        count = 0
        while cur.pid != 0 and count < 30:
            count += 1
            if cur.pid == ancestor_pid:
                return True
            cur = cur.parent()
            if cur is None:
                break
    except Exception:
        pass
    return False


def get_process_windows(pid: int) -> List[int]:
    """Enumerates visible top-level window handles belonging to a process using ctypes."""
    if os.name != "nt":
        return []
    windows = []
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            w_pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(w_pid))
            if w_pid.value == pid:
                windows.append(hwnd)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass
    return windows


class StrictExternalAppTerminator:
    """
    Authoritative manager for scanning, warning, gracefully closing,
    and terminating prohibited external applications during Strict Session.
    """

    def __init__(self, db: Optional[Any] = None, current_pid: Optional[int] = None):
        self.db = db
        self.current_pid = current_pid or os.getpid()
        self.current_session = get_windows_session_id(self.current_pid)
        self._monitoring_active = False
        self._strict_study_browser_pids: Set[int] = set()

    def register_study_browser_pid(self, pid: int):
        """Registers a dedicated StrictRecords WebView2 study browser process ID."""
        if pid:
            self._strict_study_browser_pids.add(pid)

    def is_protected(self, pid: int, proc_name: str, exe_path: Optional[str] = None) -> bool:
        """
        Determines whether a process is protected from termination.
        Guarantees that Windows OS, drivers, accessibility, and StrictRecords
        (including its dedicated study browser) are never touched.
        """
        if pid in (0, 4, self.current_pid):
            return True

        # Never touch other Windows sessions (e.g., background service session 0 or other users)
        if self.current_session is not None:
            proc_session = get_windows_session_id(pid)
            if proc_session is not None and proc_session != self.current_session:
                return True

        # Check StrictRecords ancestry (host process + all child processes)
        if is_process_ancestor(self.current_pid, pid):
            return True

        # Check registered study browser PIDs
        if pid in self._strict_study_browser_pids or any(is_process_ancestor(sp, pid) for sp in self._strict_study_browser_pids):
            return True

        name_lower = (proc_name or "").lower().strip()
        if name_lower in PROTECTED_PROCESS_NAMES:
            return True

        # Check preferences exclusions (including daily unblocks)
        try:
            from windows.strictbot_preferences import Preferences
            if name_lower in Preferences().load().get("exclusions", []):
                return True
        except Exception:
            pass

        # WebView2 Protection:
        # msedgewebview2.exe belongs to embedded runtimes (including StrictRecords ChatGPT/LeetCode study browser).
        # Standalone Microsoft Edge is msedge.exe. Do not terminate msedgewebview2.exe.
        if name_lower == "msedgewebview2.exe":
            return True

        # Safety keywords in name
        if any(w in name_lower for w in ("antivirus", "security", "defender", "driver", "system", "accessibility")):
            return True

        # Path checks
        if exe_path:
            path_lower = exe_path.lower().replace("/", "\\")
            if any(k in path_lower for k in ("\\windows\\system32\\", "\\windows\\syswow64\\", "\\programdata\\microsoft\\windows defender\\")):
                return True
            # Protect processes running from the current StrictRecords installation directory
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).lower()
            if path_lower.startswith(app_dir):
                return True

        return False

    def identify_application(self, proc_name: str, exe_path: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
        """
        Identifies whether a process belongs to one of the prohibited categories.
        Returns: (category_key, category_label, display_name) or None.
        """
        name_lower = (proc_name or "").lower().strip()
        path_lower = (exe_path or "").lower().replace("/", "\\") if exe_path else ""

        # 1. Exact catalog match by executable name
        for cat_key, (cat_label, cat_dict) in CATEGORY_MAP.items():
            if name_lower in cat_dict:
                return cat_key, cat_label, cat_dict[name_lower]

        # 2. Path-based heuristics for installed PC games & entertainment
        if path_lower:
            # Games directories
            if any(g in path_lower for g in ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\gog galaxy\\games\\", "\\xboxgames\\")):
                display = os.path.splitext(os.path.basename(path_lower))[0].replace("_", " ").title()
                return "GAMES", "Games", f"{display} (Game)"

            # Opera special launcher
            if name_lower == "launcher.exe" and "opera" in path_lower:
                return "BROWSERS", "Browsers", "Opera"

            # Spotify special helper / exe
            if "spotify" in path_lower and name_lower.startswith("spotify"):
                return "MEDIA_PLAYERS", "Video & Music Players", "Spotify"

            # Discord special helper / exe
            if "discord" in path_lower and name_lower.startswith("discord"):
                return "ENTERTAINMENT", "Entertainment & Social Media", "Discord"

            # Steam web helper
            if "steam" in path_lower and name_lower == "steamwebhelper.exe":
                return "GAMES", "Games", "Steam"

        return None

    def scan_prohibited_applications(self) -> Dict[str, Any]:
        """
        Scans all running processes in the current Windows user session.
        Uses fast two-stage evaluation: filters by name first (sub-millisecond),
        then inspects executable path and metadata only for matching candidates.
        """
        import psutil

        apps_by_name: Dict[str, Dict[str, Any]] = {}
        total_procs = 0

        # Stage 1: Fast scan by name
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                info = proc.info
                pid = info.get("pid")
                name = info.get("name") or ""
                if not pid or not name or pid in (0, 4, self.current_pid):
                    continue

                name_lower = name.lower().strip()
                if name_lower in PROTECTED_PROCESS_NAMES or name_lower == "msedgewebview2.exe":
                    continue

                # Quick filter: is name in catalog or candidate?
                is_candidate = name_lower in ALL_PROHIBITED_NAMES or name_lower in ("launcher.exe", "javaw.exe")
                if not is_candidate:
                    continue

                # Stage 2: Deep inspection of candidate process
                try:
                    proc_obj = psutil.Process(pid)
                    exe = proc_obj.exe()
                    create_time = proc_obj.create_time()
                except Exception:
                    continue

                if self.is_protected(pid, name, exe):
                    continue

                match = self.identify_application(name, exe)
                if not match:
                    continue

                cat_key, cat_label, display_name = match
                total_procs += 1

                if display_name not in apps_by_name:
                    apps_by_name[display_name] = {
                        "app_key": name.lower(),
                        "display_name": display_name,
                        "category": cat_key,
                        "category_label": cat_label,
                        "root_pid": pid,
                        "pids": [pid],
                        "exe_path": exe,
                        "started": create_time,
                    }
                else:
                    apps_by_name[display_name]["pids"].append(pid)
                    if create_time < apps_by_name[display_name]["started"]:
                        apps_by_name[display_name]["started"] = create_time
                        apps_by_name[display_name]["root_pid"] = pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:
                log.debug("Process scan error on PID: %s", e)

        prohibited_list = list(apps_by_name.values())
        return {
            "prohibited_apps": prohibited_list,
            "total_apps": len(prohibited_list),
            "total_processes": total_procs,
            "clean": len(prohibited_list) == 0,
        }

    def close_prohibited_applications(
        self,
        apps: List[Dict[str, Any]],
        grace_period: float = 3.5,
        force: bool = True
    ) -> Dict[str, Any]:
        """
        Executes the required two-phase shutdown:
        Phase 1: Graceful shutdown request (WM_CLOSE to visible windows, terminate() for workers)
                 Wait for configurable grace period to allow saving work.
        Phase 2: Controlled process termination (kill()) for remaining processes.
        """
        import psutil

        all_target_pids: Set[int] = set()
        for app in apps:
            for p in app.get("pids", []):
                all_target_pids.add(p)

        if not all_target_pids:
            return {"success": True, "closed_apps": [], "failed_apps": [], "clean": True}

        log.info("Requesting graceful shutdown for %d prohibited processes: %s", len(all_target_pids), all_target_pids)

        # -------------------------------------------------------------------
        # Phase 1: Graceful Shutdown (WM_CLOSE + terminate)
        # -------------------------------------------------------------------
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010

        target_procs: List[psutil.Process] = []
        for pid in list(all_target_pids):
            try:
                proc = psutil.Process(pid)
                if self.is_protected(pid, proc.name(), proc.exe()):
                    continue
                target_procs.append(proc)

                # Send WM_CLOSE to all top-level windows of this process
                windows = get_process_windows(pid)
                for hwnd in windows:
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

                # Also post to any children's windows
                try:
                    for child in proc.children(recursive=True):
                        if not self.is_protected(child.pid, child.name(), child.exe()):
                            for ch_hwnd in get_process_windows(child.pid):
                                user32.PostMessageW(ch_hwnd, WM_CLOSE, 0, 0)
                except Exception:
                    pass

                # If no visible window was found, invoke standard graceful terminate()
                if not windows:
                    try:
                        proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Wait for the grace period
        gone, alive = psutil.wait_procs(target_procs, timeout=grace_period)
        log.info("Graceful shutdown wait finished: %d exited, %d remaining", len(gone), len(alive))

        # -------------------------------------------------------------------
        # Phase 2: Controlled Process Termination Where Necessary
        # -------------------------------------------------------------------
        if alive and force:
            log.info("Terminating %d stubborn prohibited processes forcefully...", len(alive))
            by_pid = {p.pid: p for p in alive}

            def depth(proc):
                count = 0
                try:
                    cur = proc.parent()
                    while cur and cur.pid in by_pid and count < 20:
                        count += 1
                        cur = cur.parent()
                except Exception:
                    pass
                return count

            sorted_procs = sorted(alive, key=depth, reverse=True)
            for p in sorted_procs:
                try:
                    p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    log.debug("Process kill error on PID %d: %s", p.pid, e)

            # Wait briefly to confirm exit
            gone2, alive2 = psutil.wait_procs(alive, timeout=1.5)
            alive = alive2

        # -------------------------------------------------------------------
        # Phase 3: Verification
        # -------------------------------------------------------------------
        closed_apps = []
        failed_apps = []
        alive_pids = {p.pid for p in alive}

        for app in apps:
            app_pids = set(app.get("pids", []))
            remaining = app_pids.intersection(alive_pids)
            if not remaining:
                closed_apps.append(app["display_name"])
            else:
                failed_apps.append({
                    "display_name": app["display_name"],
                    "remaining_pids": list(remaining)
                })

        all_clean = len(failed_apps) == 0
        return {
            "success": all_clean,
            "closed_apps": closed_apps,
            "failed_apps": failed_apps,
            "clean": all_clean,
        }

    def check_and_block_process(self, pid: int, proc_name: str) -> Optional[Dict[str, Any]]:
        """
        Used during active Strict Session monitoring.
        If a newly launched or foreground process belongs to a prohibited category,
        terminates it and returns event metadata.
        """
        import psutil

        if self.is_protected(pid, proc_name):
            return None

        exe_path = None
        try:
            p = psutil.Process(pid)
            exe_path = p.exe()
        except Exception:
            pass

        match = self.identify_application(proc_name, exe_path)
        if not match:
            return None

        cat_key, cat_label, display_name = match
        log.warning("Prohibited application detected during Strict Session: %s (PID: %d)", display_name, pid)

        # Close process tree
        try:
            p = psutil.Process(pid)
            user32 = ctypes.windll.user32
            for hwnd in get_process_windows(pid):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            gone, alive = psutil.wait_procs([p], timeout=1.0)
            if alive:
                p.kill()
                psutil.wait_procs(alive, timeout=0.5)
        except Exception as e:
            log.debug("Error blocking process %d (%s): %s", pid, proc_name, e)

        return {
            "blocked": True,
            "app_name": display_name,
            "category": cat_key,
            "category_label": cat_label,
            "pid": pid,
            "proc_name": proc_name,
        }

    def stop_monitoring(self):
        """Stops active monitoring and cleans up."""
        self._monitoring_active = False
        self._strict_study_browser_pids.clear()

    # =========================================================================
    # PART 3: EXTERNAL BROWSER CLEANUP PREREQUISITE & CLOSURE ACKNOWLEDGEMENT
    # =========================================================================

    PRIMARY_BROWSER_LABELS: Dict[str, str] = {
        "chrome.exe": "Chrome",
        "brave.exe": "Brave",
        "msedge.exe": "Edge",
        "firefox.exe": "Firefox",
        "opera.exe": "Opera",
        "opera_gx.exe": "Opera GX",
        "vivaldi.exe": "Vivaldi",
        "arc.exe": "Arc",
    }

    def _scan_external_browsers_detailed(self) -> Dict[str, Dict[str, Any]]:
        """
        Scans running processes for standalone external desktop browsers.
        Strictly excludes msedgewebview2.exe, StrictRoutines.exe, and protected system processes.
        Also ignores headless Windows background Edge helpers (--no-startup-window / Widgets)
        that have zero visible windows.
        """
        import psutil

        browser_catalog = BROWSERS
        detected: Dict[str, Dict[str, Any]] = {}

        # First pass: collect all candidate browser processes and their visible windows
        procs_by_exe: Dict[str, List[Tuple[int, str, List[int], List[str]]]] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info.get("pid")
                name = (proc.info.get("name") or "").strip()
                if not pid or not name or pid in (0, 4, self.current_pid):
                    continue
                name_lower = name.lower()
                if name_lower not in browser_catalog:
                    continue
                if name_lower in PROTECTED_PROCESS_NAMES or name_lower == "msedgewebview2.exe":
                    continue
                proc_obj = psutil.Process(pid)
                exe_path = ""
                cmdline: List[str] = []
                try:
                    exe_path = proc_obj.exe()
                except Exception:
                    pass
                try:
                    cmdline = proc_obj.cmdline() or []
                except Exception:
                    pass
                if self.is_protected(pid, name, exe_path):
                    continue
                wins = get_process_windows(pid)
                procs_by_exe.setdefault(name_lower, []).append((pid, exe_path, wins, cmdline))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue

        for name_lower, entries in procs_by_exe.items():
            total_windows = sum(len(w) for _, _, w, _ in entries)
            # For msedge.exe on Windows 11, background system helpers (--no-startup-window /
            # --utility-sub-type / --type=renderer) can run with 0 visible windows when Edge is closed.
            if name_lower == "msedge.exe" and total_windows == 0:
                has_interactive_root = False
                for _, _, _, cmd in entries:
                    cmd_str = " ".join(cmd).lower()
                    if (
                        "--type=" not in cmd_str
                        and "--no-startup-window" not in cmd_str
                        and "--webview" not in cmd_str
                        and "--headless" not in cmd_str
                        and cmd_str.strip() != ""
                    ):
                        has_interactive_root = True
                        break
                if not has_interactive_root:
                    continue

            short_name = self.PRIMARY_BROWSER_LABELS.get(
                name_lower, browser_catalog.get(name_lower, name_lower)
            )
            pids = [p for p, _, _, _ in entries]
            all_wins: List[int] = []
            for _, _, w, _ in entries:
                all_wins.extend(w)

            detected[short_name] = {
                "browser_name": short_name,
                "display_name": browser_catalog.get(name_lower, short_name),
                "process_name": name_lower,
                "pids": pids,
                "window_count": len(all_wins),
                "hwnds": all_wins,
            }

        return detected

    def run_external_browser_cleanup(
        self,
        preparation_id: Optional[str] = None,
        grace_period: float = 3.0,
        force_kill_authorized: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes Part 3 Prerequisite: External Browser Cleanup.
        1. Enumerates running external browsers and visible windows.
        2. Requests graceful closure (WM_CLOSE to top-level windows).
        3. Waits for processes to exit and rechecks Windows process/window state.
        4. Records explicit per-browser closure acknowledgement (PIDs, window count,
           close request timestamp, actual exit timestamp, verified status).
        """
        import psutil
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        initial_detected = self._scan_external_browsers_detailed()

        user32 = ctypes.windll.user32 if os.name == "nt" else None
        WM_CLOSE = 0x0010

        target_procs: List[Any] = []
        close_requested_at: Dict[str, str] = {}

        for short_name, info in initial_detected.items():
            req_ts = datetime.now(timezone.utc).isoformat()
            close_requested_at[short_name] = req_ts
            for pid in info["pids"]:
                try:
                    p = psutil.Process(pid)
                    if self.is_protected(pid, p.name()):
                        continue
                    target_procs.append(p)
                    wins = get_process_windows(pid)
                    if user32 and wins:
                        for hwnd in wins:
                            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    elif not wins:
                        # Worker / renderer / background browser process without top-level window
                        try:
                            p.terminate()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        if target_procs:
            gone, alive = psutil.wait_procs(target_procs, timeout=grace_period)
            # If top-level windows closed, clean up any remaining windowless helper processes
            remaining_with_windows = [p for p in alive if len(get_process_windows(p.pid)) > 0]
            if not remaining_with_windows and alive:
                for p in alive:
                    try:
                        p.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                psutil.wait_procs(alive, timeout=1.0)
            elif alive and force_kill_authorized:
                for p in alive:
                    try:
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                psutil.wait_procs(alive, timeout=1.0)

        exit_iso = datetime.now(timezone.utc).isoformat()
        remaining_detected = self._scan_external_browsers_detailed()

        # Build comprehensive per-browser acknowledgement table
        standard_order = ["Chrome", "Brave", "Edge", "Firefox", "Opera"]
        all_browser_keys = list(dict.fromkeys(
            standard_order + list(initial_detected.keys()) + list(remaining_detected.keys())
        ))

        acknowledgements: List[Dict[str, Any]] = []
        open_browsers: List[str] = []
        closed_verified_browsers: List[str] = []

        for b_name in all_browser_keys:
            init_info = initial_detected.get(b_name)
            rem_info = remaining_detected.get(b_name)
            proc_name = (
                (init_info or rem_info or {}).get("process_name")
                or next((k for k, v in self.PRIMARY_BROWSER_LABELS.items() if v == b_name), f"{b_name.lower()}.exe")
            )

            if init_info and not rem_info:
                # Was running -> now closed and verified
                ack = {
                    "browser_name": b_name,
                    "process_name": proc_name,
                    "was_running": True,
                    "initial_pids": init_info["pids"],
                    "initial_window_count": init_info["window_count"],
                    "remaining_pids": [],
                    "remaining_window_count": 0,
                    "close_request_timestamp": close_requested_at.get(b_name, now_iso),
                    "actual_exit_timestamp": exit_iso,
                    "closure_acknowledged": True,
                    "state": "CLOSED_VERIFIED",
                    "status_label": f"{b_name}: Closed – Verified",
                    "failure_reason": None,
                }
                closed_verified_browsers.append(b_name)
                acknowledgements.append(ack)
            elif rem_info:
                # Still open after graceful close request
                rem_wins = rem_info["window_count"]
                rem_pids = rem_info["pids"]
                reason = (
                    f"{b_name} remained open ({rem_wins} window(s), PIDs {rem_pids}) — "
                    "check for unsaved tabs, download prompts, or dialog boxes and click Retry Browser Cleanup."
                )
                ack = {
                    "browser_name": b_name,
                    "process_name": proc_name,
                    "was_running": True,
                    "initial_pids": init_info["pids"] if init_info else rem_pids,
                    "initial_window_count": init_info["window_count"] if init_info else rem_wins,
                    "remaining_pids": rem_pids,
                    "remaining_window_count": rem_wins,
                    "close_request_timestamp": close_requested_at.get(b_name, now_iso),
                    "actual_exit_timestamp": None,
                    "closure_acknowledged": False,
                    "state": "OPEN_MANUAL_CLOSURE_REQUIRED",
                    "status_label": f"{b_name}: Open – Manual Closure Required ({rem_wins} window(s))",
                    "failure_reason": reason,
                }
                open_browsers.append(b_name)
                acknowledgements.append(ack)
            else:
                # Was not running
                ack = {
                    "browser_name": b_name,
                    "process_name": proc_name,
                    "was_running": False,
                    "initial_pids": [],
                    "initial_window_count": 0,
                    "remaining_pids": [],
                    "remaining_window_count": 0,
                    "close_request_timestamp": now_iso,
                    "actual_exit_timestamp": now_iso,
                    "closure_acknowledged": True,
                    "state": "NOT_RUNNING",
                    "status_label": f"{b_name}: Not Running",
                    "failure_reason": None,
                }
                acknowledgements.append(ack)

        passed = len(open_browsers) == 0
        per_browser_summary = {a["browser_name"]: a["status_label"].split(": ", 1)[-1] for a in acknowledgements}
        browsers_dict = {a["browser_name"]: a for a in acknowledgements}
        desc = (
            "All external browsers closed and verified."
            if passed
            else f"External browsers still open: {', '.join(open_browsers)}. Close them or click Retry Browser Cleanup."
        )
        result = {
            "success": passed,
            "passed": passed,
            "status": "EXTERNAL_BROWSERS_CLOSED" if passed else "EXTERNAL_BROWSERS_OPEN",
            "prerequisite_status": "EXTERNAL_BROWSERS_CLOSED: PASSED" if passed else "EXTERNAL_BROWSERS_OPEN",
            "header": "EXTERNAL BROWSER CLEANUP",
            "description": desc,
            "message": desc,
            "preparation_id": preparation_id,
            "checked_at": exit_iso,
            "closed_verified_browsers": closed_verified_browsers,
            "open_browsers": open_browsers,
            "blocking_browsers": open_browsers,
            "per_browser_summary": per_browser_summary,
            "browsers": browsers_dict,
            "acknowledgements": acknowledgements,
        }
        self._last_browser_cleanup_report = result
        return result

    def verify_external_browsers_closed_live(
        self,
        preparation_id: Optional[str] = None,
        auto_cleanup_if_needed: bool = False,
    ) -> Dict[str, Any]:
        """
        Live non-destructive check of whether all external browsers remain closed.
        Preserves prior 'Closed – Verified' acknowledgements from the current preparation
        unless the user reopened an external browser before clicking Start Strict Session.
        """
        from datetime import datetime, timezone

        prev = getattr(self, "_last_browser_cleanup_report", None)
        if prev is None or (preparation_id and prev.get("preparation_id") not in (None, preparation_id)):
            if auto_cleanup_if_needed:
                return self.run_external_browser_cleanup(preparation_id=preparation_id)

        live_detected = self._scan_external_browsers_detailed()
        if live_detected and auto_cleanup_if_needed:
            return self.run_external_browser_cleanup(preparation_id=preparation_id)

        now_iso = datetime.now(timezone.utc).isoformat()
        prev_acks = {
            a["browser_name"]: a for a in (prev.get("acknowledgements", []) if isinstance(prev, dict) else [])
        }
        standard_order = ["Chrome", "Brave", "Edge", "Firefox", "Opera"]
        all_browser_keys = list(dict.fromkeys(
            standard_order + list(prev_acks.keys()) + list(live_detected.keys())
        ))

        acknowledgements: List[Dict[str, Any]] = []
        open_browsers: List[str] = []
        closed_verified_browsers: List[str] = []

        for b_name in all_browser_keys:
            live_info = live_detected.get(b_name)
            prior_ack = prev_acks.get(b_name)
            proc_name = (
                (live_info or prior_ack or {}).get("process_name")
                or next((k for k, v in self.PRIMARY_BROWSER_LABELS.items() if v == b_name), f"{b_name.lower()}.exe")
            )
            if live_info:
                wins = live_info["window_count"]
                pids = live_info["pids"]
                open_browsers.append(b_name)
                acknowledgements.append({
                    "browser_name": b_name,
                    "process_name": proc_name,
                    "was_running": True,
                    "initial_pids": pids,
                    "initial_window_count": wins,
                    "remaining_pids": pids,
                    "remaining_window_count": wins,
                    "close_request_timestamp": prior_ack.get("close_request_timestamp") if prior_ack else None,
                    "actual_exit_timestamp": None,
                    "closure_acknowledged": False,
                    "state": "OPEN_MANUAL_CLOSURE_REQUIRED",
                    "status_label": f"{b_name}: Open – Closure Required ({wins} window(s))",
                    "failure_reason": f"{b_name} is currently open (PIDs {pids}). Click Retry Browser Cleanup to close it.",
                })
            elif prior_ack and prior_ack.get("state") == "CLOSED_VERIFIED":
                closed_verified_browsers.append(b_name)
                acknowledgements.append(dict(prior_ack))
            else:
                acknowledgements.append({
                    "browser_name": b_name,
                    "process_name": proc_name,
                    "was_running": False,
                    "initial_pids": [],
                    "initial_window_count": 0,
                    "remaining_pids": [],
                    "remaining_window_count": 0,
                    "close_request_timestamp": (prior_ack or {}).get("close_request_timestamp", now_iso),
                    "actual_exit_timestamp": (prior_ack or {}).get("actual_exit_timestamp", now_iso),
                    "closure_acknowledged": True,
                    "state": "NOT_RUNNING",
                    "status_label": f"{b_name}: Not Running",
                    "failure_reason": None,
                })

        passed = len(open_browsers) == 0
        per_browser_summary = {a["browser_name"]: a["status_label"].split(": ", 1)[-1] for a in acknowledgements}
        browsers_dict = {a["browser_name"]: a for a in acknowledgements}
        desc = (
            "All external browsers closed and verified."
            if passed
            else f"External browsers currently open: {', '.join(open_browsers)}. Click Retry Browser Cleanup."
        )
        return {
            "success": passed,
            "passed": passed,
            "status": "EXTERNAL_BROWSERS_CLOSED" if passed else "EXTERNAL_BROWSERS_OPEN",
            "prerequisite_status": "EXTERNAL_BROWSERS_CLOSED: PASSED" if passed else "EXTERNAL_BROWSERS_OPEN",
            "header": "EXTERNAL BROWSER CLEANUP",
            "description": desc,
            "message": desc,
            "preparation_id": preparation_id,
            "checked_at": now_iso,
            "closed_verified_browsers": closed_verified_browsers,
            "open_browsers": open_browsers,
            "blocking_browsers": open_browsers,
            "per_browser_summary": per_browser_summary,
            "browsers": browsers_dict,
            "acknowledgements": acknowledgements,
        }
