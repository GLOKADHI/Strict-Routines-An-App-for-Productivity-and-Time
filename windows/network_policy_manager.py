"""
STRICTRECORDS NETWORK POLICY MANAGER
Enforces OS-level domain access controls, proxy auto-configuration, and dynamic website gating (Sections 6, 7, 25).
Strongest supported mechanisms for Windows deployment:
- WinINet User Proxy & PAC (Proxy Auto-Configuration) filtering
- Dynamic privilege domain routing: Allowed domains pass; unauthorized/entertainment domains
  route to the StrictRecords Local Lock Screen when privilege is BLOCKED.
- Clean session restoration (always removes PAC/proxy on exit or crash recovery).
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

logger = logging.getLogger("NetworkPolicyManager")

INTERNET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


class NetworkPolicyManager:
    """
    Manages OS-level network policy and browser routing.
    Configures dynamic domain filtering based on StrictRecords policy and privilege state.
    """

    def __init__(self, pac_dir: Optional[str] = None):
        self.pac_dir = pac_dir or os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StrictRecords"
        )
        os.makedirs(self.pac_dir, exist_ok=True)
        self.pac_path = os.path.join(self.pac_dir, "strict_policy.pac")
        self.backup_path = os.path.join(self.pac_dir, 'strict_network_backup.json')
        self.is_active: bool = False
        self.allowed_domains: List[str] = []
        self.entertainment_domains: List[str] = [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "instagram.com",
            "www.instagram.com",
            "facebook.com",
            "www.facebook.com",
            "vimeo.com",
            "netflix.com",
            "spotify.com",
        ]
        self.entertainment_allowed: bool = False
        self.local_block_endpoint: str = "http://127.0.0.1:8765/api/strict_session/block_page"

    def apply_policy(
        self,
        allowed_domains: List[str],
        entertainment_allowed: bool = False,
    ) -> bool:
        """
        Generates and applies dynamic PAC script to enforce allowed domains.
        """
        self.allowed_domains = allowed_domains or []
        self.entertainment_allowed = entertainment_allowed

        # Build set of currently permitted domains
        permitted: Set[str] = set()
        for d in self.allowed_domains:
            permitted.add(d.lower().strip())
            if not d.startswith("www."):
                permitted.add(f"www.{d.lower().strip()}")

        if entertainment_allowed:
            for d in self.entertainment_domains:
                permitted.add(d.lower().strip())

        # Generate PAC (Proxy Auto-Configuration) JavaScript
        pac_content = self._generate_pac_script(list(permitted))
        try:
            with open(self.pac_path, "w", encoding="utf-8") as f:
                f.write(pac_content)
            logger.info(f"Generated PAC filter with {len(permitted)} permitted domains at {self.pac_path}")

            # Register PAC URL in Windows Internet Settings (WinINet)
            if HAS_WINREG and os.name == "nt":
                pac_file_url = f"file:///{self.pac_path.replace(os.sep, '/')}"
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    INTERNET_SETTINGS_KEY,
                    0,
                    winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
                ) as key:
                    if not os.path.exists(self.backup_path):
                        try:
                            value, kind = winreg.QueryValueEx(key, 'AutoConfigURL')
                            previous = dict(present=True,value=value,kind=kind)
                        except FileNotFoundError:
                            previous = dict(present=False)
                        with open(self.backup_path,'w',encoding='utf-8') as backup:
                            json.dump(previous,backup)
                    winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, pac_file_url)
                self._refresh_settings()
                logger.info(f"Configured Windows AutoConfigURL: {pac_file_url}")

            self.is_active = True
            return True
        except Exception as e:
            logger.error(f"Failed to apply network policy: {e}")
            return False

    def update_privilege(self, entertainment_allowed: bool) -> bool:
        """Dynamically re-evaluates and updates network policy based on privilege changes."""
        return self.apply_policy(self.allowed_domains, entertainment_allowed=entertainment_allowed)

    def restore_network(self) -> bool:
        """
        Safely removes PAC and restores standard Windows network routing.
        Guaranteed safe across reboots or crashes.
        """
        try:
            if HAS_WINREG and os.name == "nt" and os.path.exists(self.backup_path):
                with open(self.backup_path,encoding='utf-8') as backup:
                    previous = json.load(backup)
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    INTERNET_SETTINGS_KEY,
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    try:
                        if previous.get('present'):
                            winreg.SetValueEx(key,'AutoConfigURL',0,previous['kind'],previous['value'])
                        else:
                            winreg.DeleteValue(key, "AutoConfigURL")
                    except FileNotFoundError:
                        pass
                logger.info("Removed Windows AutoConfigURL setting.")
                os.remove(self.backup_path)
                self._refresh_settings()

            if os.path.exists(self.pac_path):
                try:
                    os.remove(self.pac_path)
                except Exception:
                    pass

            self.is_active = False
            return True
        except Exception as e:
            logger.error(f"Error restoring network policy: {e}")
            return False

    def is_domain_permitted(self, domain: str) -> bool:
        """Checks if a given domain is currently permitted under active policy."""
        dom = domain.lower().strip()
        if dom in ("127.0.0.1", "localhost"):
            return True
        for allowed in self.allowed_domains:
            if dom == allowed.lower() or dom.endswith(f".{allowed.lower()}"):
                return True
        if self.entertainment_allowed:
            for ent in self.entertainment_domains:
                if dom == ent.lower() or dom.endswith(f".{ent.lower()}"):
                    return True
        return False

    @staticmethod
    def _refresh_settings():
        import ctypes
        for option in (39,37):
            ctypes.windll.wininet.InternetSetOptionW(None,option,None,0)

    def _generate_pac_script(self, permitted_domains: List[str]) -> str:
        """
        Generates standard PAC FindProxyForURL script.
        Permitted domains -> DIRECT
        Localhost / Intranet -> DIRECT
        Non-permitted domains -> PROXY 127.0.0.1:8765 (Routes to local block screen)
        """
        domain_checks = []
        for d in permitted_domains:
            domain_checks.append(f'shExpMatch(host, "*.{d}") || host === "{d}"')

        condition = " || ".join(domain_checks) if domain_checks else "false"

        return f"""// StrictRecords Dynamic Network Policy PAC
function FindProxyForURL(url, host) {{
    // Always allow localhost and loopback for StrictRecords API & UI
    if (shExpMatch(host, "127.0.0.1") || shExpMatch(host, "localhost") || isInNet(host, "127.0.0.0", "255.0.0.0")) {{
        return "DIRECT";
    }}

    // Check permitted domains
    if ({condition}) {{
        return "DIRECT";
    }}

    // Redirect blocked traffic to StrictRecords Local Proctor Port
    return "PROXY 127.0.0.1:8765";
}}
"""
