"""
STRICTRECORDS BROWSER POLICY MANAGER
Manages the designated approved browser (Comet.exe / WebView2), dynamic domain allowlists,
and real-time site navigation restrictions (Sections 6, 7, 12).
"""

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Set

try:
    import win32gui
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

logger = logging.getLogger("BrowserPolicyManager")


class BrowserPolicyManager:
    """
    Coordinates browser execution and URL allowlist enforcement.
    Designated browser: Comet.exe (or configured alternative).
    """

    def __init__(self, designated_browser: str = "Comet.exe"):
        self.designated_browser = designated_browser
        self.is_active: bool = False
        self.browser_proc: Optional[subprocess.Popen] = None
        self.allowed_domains: List[str] = []
        self.entertainment_allowed: bool = False

    def configure(
        self,
        designated_browser: str,
        allowed_domains: List[str],
        entertainment_allowed: bool = False,
    ):
        self.designated_browser = designated_browser or "Comet.exe"
        self.allowed_domains = allowed_domains or []
        self.entertainment_allowed = entertainment_allowed

    def launch_approved_browser(self, start_url: str = "http://127.0.0.1:8765", profile_dir=None) -> Dict[str, Any]:
        """
        Launches the designated browser with strict session flags if available.
        """
        try:
            # Look for browser on system or in typical locations
            browser_path = self._locate_browser(self.designated_browser)
            if not browser_path:
                logger.warning(
                    f"Designated browser '{self.designated_browser}' not found as standalone binary. "
                    "Operating with embedded WebView2 environment."
                )
                return {
                    "success": False,
                    "error": "The configured study browser is unavailable. Choose an installed approved browser.",
                    "mode": "UNAVAILABLE",
                    "browser": self.designated_browser,
                    "url": start_url,
                }

            args = [browser_path, f"--app={start_url}"]
            if profile_dir:
                os.makedirs(profile_dir, exist_ok=True)
                args += [f'--user-data-dir={os.path.abspath(profile_dir)}', '--kiosk', '--start-fullscreen', '--no-first-run']
            self.browser_proc = subprocess.Popen(args)
            self.is_active = True
            logger.info(f"Launched approved browser {browser_path} (PID: {self.browser_proc.pid})")
            return {
                "success": True,
                "mode": "STANDALONE_BROWSER",
                "browser": self.designated_browser,
                "pid": self.browser_proc.pid,
                "url": start_url,
            }
        except Exception as e:
            logger.error(f"Failed to launch approved browser: {e}")
            return {"success": False, "error": str(e)}

    def check_active_browser_navigation(self, active_title: str) -> Optional[Dict[str, Any]]:
        """
        Inspects window title for blocked domain access patterns.
        Returns block event dict if an unauthorized site is detected.
        """
        title_lower = (active_title or "").lower().strip()
        if any(se in title_lower for se in [
            " - google search", "google search",
            " - bing", "bing search",
            " - duckduckgo", "duckduckgo",
            " - brave search", "brave search",
            " - perplexity", "perplexity",
            "github", "stackoverflow", "wikipedia"
        ]):
            return None

        # Check for entertainment keywords when entertainment is blocked
        if not self.entertainment_allowed:
            import re
            for ent_kw in ["youtube", "netflix", "instagram", "facebook", "tiktok", "vimeo", "twitch"]:
                if re.search(r'(?:\b|[\s\-_•|])' + re.escape(ent_kw) + r'(?:\b|[\s\-_•|]|$)', title_lower):
                    logger.warning(f"Detected unauthorized entertainment navigation: {active_title}")
                    return {
                        "action": "BLOCKED",
                        "reason": f"Entertainment site '{ent_kw}' is restricted under current score/privilege policy.",
                        "matched_keyword": ent_kw,
                        "title": active_title,
                    }

        return None

    def close_browser(self):
        """Cleanly terminates designated browser on session end."""
        if self.browser_proc:
            try:
                import psutil
                children = psutil.Process(self.browser_proc.pid).children(recursive=True)
                self.browser_proc.terminate()
                for child in children:
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                self.browser_proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.browser_proc.kill()
                except Exception:
                    pass
            self.browser_proc = None
        self.is_active = False

    def _locate_browser(self, name: str) -> Optional[str]:
        # Search PATH, LocalAppData, and Program Files
        candidates = [
            name,
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Perplexity\Comet\Application\comet.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", name, f"{name}.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), name, f"{name}.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), name, f"{name}.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", name, f"{name}.exe"),
        ]
        for c in candidates:
            if os.path.isabs(c) and os.path.exists(c):
                return c
        return None
