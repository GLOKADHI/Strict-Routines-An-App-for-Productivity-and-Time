"""Apply YouTube and Instagram restrictions through supported Chromium policies.

Only values written by StrictRecords are removed when a restriction is lifted. Existing
browser policy values are preserved, including policy entries owned by an administrator.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # winreg is available only on Windows; the injected adapter keeps unit tests portable.
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - exercised on non-Windows hosts
    winreg = None  # type: ignore


SERVICE_RULES: Dict[str, Tuple[str, ...]] = {
    "youtube": ("[*.]youtube.com", "youtu.be", "[*.]youtube-nocookie.com"),
    "instagram": ("[*.]instagram.com",),
    "extension_protection": (
        "chrome://extensions*",
        "chrome://settings/extensions*",
        "chrome://settings/reset*",
        "chrome://flags*",
        "edge://extensions*",
        "edge://settings/extensions*",
        "edge://settings/reset*",
        "edge://flags*",
        "brave://extensions*",
        "brave://settings/extensions*",
        "brave://settings/reset*",
        "brave://flags*",
        "opera://extensions*",
        "opera://settings/reset*",
        "opera://flags*",
        "comet://extensions*",
        "comet://settings/extensions*",
        "comet://settings/reset*",
        "comet://flags*",
    ),
}

STRICTROUTINES_EXTENSION_ID = "iincmekgilffpjmmmcgloliadppioajc"

DEFAULT_BROWSERS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "Google Chrome",
        "policy_path": r"Software\Policies\Google\Chrome\URLBlocklist",
        "extension_settings_path": r"Software\Policies\Google\Chrome\ExtensionSettings",
        "executables": ("chrome.exe",),
        "candidates": (
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ),
    },
    {
        "name": "Microsoft Edge",
        "policy_path": r"Software\Policies\Microsoft\Edge\URLBlocklist",
        "extension_settings_path": r"Software\Policies\Microsoft\Edge\ExtensionSettings",
        "executables": ("msedge.exe",),
        "candidates": (
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        ),
    },
    {
        "name": "Brave",
        "policy_path": r"Software\Policies\BraveSoftware\Brave\URLBlocklist",
        "extension_settings_path": r"Software\Policies\BraveSoftware\Brave\ExtensionSettings",
        "executables": ("brave.exe",),
        "candidates": (
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ),
    },
    {
        "name": "Opera",
        "policy_path": r"Software\Policies\Opera\URLBlocklist",
        "executables": ("opera.exe",),
        "candidates": (
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Opera", "opera.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Opera", "opera.exe"),
        ),
    },
    {
        "name": "Comet",
        "policy_path": r"Software\Policies\Perplexity\Comet\URLBlocklist",
        "additional_policy_paths": (
            r"Software\Policies\Chromium\URLBlocklist",
        ),
        "executables": ("comet.exe",),
        "candidates": (
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Perplexity", "Comet", "Application", "comet.exe"),
        ),
    },
)

OWNERSHIP_PATH = r"Software\StrictRecords\BrowserPolicyOwnership"


class BrowserPolicyController:
    """Manage URLBlocklist policy entries in the current Windows user's registry hive."""

    def __init__(
        self,
        registry: Any = None,
        browsers: Optional[Iterable[Dict[str, Any]]] = None,
        path_exists: Any = os.path.isfile,
        executable_lookup: Any = shutil.which,
        platform_name: Optional[str] = None,
    ) -> None:
        self.registry = registry if registry is not None else winreg
        self.browsers = tuple(browsers) if browsers is not None else DEFAULT_BROWSERS
        self.path_exists = path_exists
        self.executable_lookup = executable_lookup
        self.platform_name = platform_name or os.name

    def _available_browsers(self) -> List[Dict[str, Any]]:
        available = []
        for browser in self.browsers:
            found = any(self.path_exists(path) for path in browser.get("candidates", ()) if path)
            if not found:
                found = any(self.executable_lookup(name) for name in browser.get("executables", ()))
            if found:
                available.append(browser)
        return available

    def _open(self, path: str, create: bool = False) -> Any:
        reg = self.registry
        access = reg.KEY_READ | reg.KEY_WRITE
        if create:
            return reg.CreateKeyEx(reg.HKEY_CURRENT_USER, path, 0, access)
        return reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, access)

    def _read_values(self, key: Any) -> Dict[str, str]:
        values: Dict[str, str] = {}
        index = 0
        while True:
            try:
                name, value, _kind = self.registry.EnumValue(key, index)
            except OSError:
                break
            values[str(name)] = str(value)
            index += 1
        return values

    def _read_owned(self, service: str, browser_name: str) -> List[Dict[str, str]]:
        reg = self.registry
        value_name = f"{service}.{self._slug(browser_name)}"
        try:
            key = self._open(OWNERSHIP_PATH)
        except OSError:
            return []
        try:
            try:
                raw, _kind = reg.QueryValueEx(key, value_name)
                parsed = json.loads(str(raw))
                if isinstance(parsed, list):
                    return [
                        {"name": str(item["name"]), "pattern": str(item["pattern"])}
                        for item in parsed
                        if isinstance(item, dict) and "name" in item and "pattern" in item
                    ]
            except (OSError, ValueError, TypeError, KeyError):
                pass
        finally:
            reg.CloseKey(key)
        return []

    def _write_owned(self, service: str, browser_name: str, owned: List[Dict[str, str]]) -> None:
        reg = self.registry
        value_name = f"{service}.{self._slug(browser_name)}"
        key = self._open(OWNERSHIP_PATH, create=True)
        try:
            if owned:
                reg.SetValueEx(key, value_name, 0, reg.REG_SZ, json.dumps(owned, separators=(",", ":")))
            else:
                try:
                    reg.DeleteValue(key, value_name)
                except OSError:
                    pass
        finally:
            reg.CloseKey(key)

    @staticmethod
    def _slug(value: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")

    @staticmethod
    def _next_value_name(values: Dict[str, str]) -> str:
        numeric = [int(name) for name in values if name.isdigit()]
        candidate = max(numeric, default=0) + 1
        while str(candidate) in values:
            candidate += 1
        return str(candidate)

    @staticmethod
    def _rule_blocks_service(rule: str, service: str) -> bool:
        """Conservatively identify whole-service rules in an existing URLBlocklist."""
        value = (rule or "").strip().lower()
        if value in ("*", "*://*/*"):
            return True
        if service == "extension_protection":
            ext_rules = [r.lower().strip() for r in SERVICE_RULES.get("extension_protection", ())]
            if any(r == value or (r.endswith("*") and value.startswith(r.rstrip("*"))) for r in ext_rules):
                return True
            return "extensions" in value or "flags" in value or "reset" in value
        domains = {
            "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
            "instagram": ("instagram.com",),
        }.get(service, ())
        if not domains:
            return False
        # URLBlocklist domain entries without a path and [*.]domain entries cover the
        # service root and its subdomains. Path-specific entries are not treated as a
        # whole-service restriction.
        for domain in domains:
            clean = value.replace("[*.]", "").replace("*.", "")
            if clean in (domain, f"https://{domain}", f"http://{domain}", f"https://www.{domain}"):
                return True
            if clean.startswith(domain + "/") or clean.startswith("www." + domain + "/"):
                continue
            if clean in (f"*.{domain}", f"https://*.{domain}", f"http://*.{domain}"):
                return True
        return False

    @staticmethod
    def _rule_overlaps_service(rule: str, service: str) -> bool:
        """Conservatively detect URL filters that can affect any URL on a service."""
        value = (rule or "").strip().lower()
        if not value:
            return False
        if value in ("*", "*://*/*"):
            return True
        if service == "extension_protection":
            return "extensions" in value or "flags" in value or "reset" in value

        domains = {
            "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
            "instagram": ("instagram.com",),
        }.get(service, ())
        if not domains:
            return False

        # URLBlocklist and URLAllowlist use host/path URL filters. Paths and schemes
        # do not change whether a rule overlaps the service; a path-specific rule is
        # still enough to make a blanket ALLOWED/BLOCKED status inaccurate.
        host_pattern = value.split("://", 1)[-1]
        host_pattern = host_pattern.split("/", 1)[0].split("@", 1)[-1]
        if host_pattern.startswith("[*.]"):
            host_pattern = host_pattern[4:]
        elif host_pattern.startswith("*."):
            host_pattern = host_pattern[2:]
        host_pattern = host_pattern.lstrip(".")
        if ":" in host_pattern and not host_pattern.startswith("["):
            host_pattern = host_pattern.split(":", 1)[0]
        host_pattern = host_pattern.strip("*")
        if host_pattern == "":
            return True

        return any(
            host_pattern == domain or host_pattern.endswith("." + domain)
            for domain in domains
        )

    def _read_policy_values(self, path: str) -> Dict[str, str]:
        try:
            key = self._open(path)
        except FileNotFoundError:
            return {}
        try:
            return self._read_values(key)
        finally:
            self.registry.CloseKey(key)

    def _apply_to_browser(self, service: str, enabled: bool, browser: Dict[str, Any]) -> Dict[str, Any]:
        reg = self.registry
        name = str(browser["name"])
        rules = SERVICE_RULES[service]
        try:
            key = self._open(str(browser["policy_path"]), create=enabled)
        except FileNotFoundError:
            if enabled:
                raise
            self._write_owned(service, name, [])
            return {
                "browser": name,
                "policyPath": str(browser["policy_path"]),
                "enforcementStatus": "APPLIED",
                "ruleActive": False,
                "urlBlocked": False,
                "effectiveAccessAllowed": True,
                "verified": True,
                "ownedRuleCount": 0,
                "error": None,
            }
        try:
            current = self._read_values(key)
            owned = self._read_owned(service, name)
            if enabled:
                # Reuse a previously assigned value when possible, and repair it if an
                # external change deleted it. The ownership record is persisted first so
                # a crash during a multi-value update can be repaired on retry.
                owned_by_pattern = {item["pattern"]: item["name"] for item in owned}
                next_owned: List[Dict[str, str]] = []
                for rule in rules:
                    existing_same = any(value.strip().lower() == rule.lower() for value in current.values())
                    if existing_same and rule not in owned_by_pattern:
                        continue  # another policy already owns this restriction
                    value_name = owned_by_pattern.get(rule)
                    if not value_name:
                        value_name = self._next_value_name(current)
                    next_owned.append({"name": value_name, "pattern": rule})
                    current[value_name] = rule
                self._write_owned(service, name, next_owned)
                for item in next_owned:
                    reg.SetValueEx(key, item["name"], 0, reg.REG_SZ, item["pattern"])
            else:
                remaining_owned: List[Dict[str, str]] = []
                for item in owned:
                    if current.get(item["name"], "").strip().lower() == item["pattern"].lower():
                        try:
                            reg.DeleteValue(key, item["name"])
                        except OSError:
                            remaining_owned.append(item)
                self._write_owned(service, name, remaining_owned)

            if service == "extension_protection" and browser.get("extension_settings_path"):
                ext_base = str(browser["extension_settings_path"])
                ext_key_path = f"{ext_base}\\{STRICTROUTINES_EXTENSION_ID}"
                try:
                    if enabled:
                        ext_key = self._open(ext_key_path, create=True)
                        try:
                            reg.SetValueEx(ext_key, "toolbar_pin", 0, reg.REG_SZ, "force_pinned")
                        finally:
                            reg.CloseKey(ext_key)
                    else:
                        try:
                            ext_key = self._open(ext_key_path, create=False)
                            try:
                                reg.DeleteValue(ext_key, "toolbar_pin")
                            except OSError:
                                pass
                            finally:
                                reg.CloseKey(ext_key)
                        except (OSError, FileNotFoundError):
                            pass
                except Exception:
                    pass

            for add_path in browser.get("additional_policy_paths", ()):
                try:
                    add_key = self._open(str(add_path), create=enabled)
                    try:
                        add_vals = self._read_values(add_key)
                        if enabled:
                            for rule in rules:
                                if not any(v.strip().lower() == rule.lower() for v in add_vals.values()):
                                    vn = self._next_value_name(add_vals)
                                    add_vals[vn] = rule
                                    reg.SetValueEx(add_key, vn, 0, reg.REG_SZ, rule)
                        else:
                            for vn, val in list(add_vals.items()):
                                if any(val.strip().lower() == rule.lower() for rule in rules):
                                    try:
                                        reg.DeleteValue(add_key, vn)
                                    except OSError:
                                        pass
                    finally:
                        reg.CloseKey(add_key)
                except Exception:
                    pass

            applied_values = self._read_values(key)
            managed_entries = self._read_owned(service, name)
            managed_match = all(applied_values.get(item["name"], "").strip().lower() == item["pattern"].lower() for item in managed_entries)
            expected_entries_present = all(any(value.strip().lower() == rule.lower() for value in applied_values.values()) for rule in rules) if enabled else not managed_entries
            external_block = any(self._rule_overlaps_service(value, service) for value in applied_values.values())
            allowlist_path = str(browser.get("allowlist_path") or browser["policy_path"].replace("URLBlocklist", "URLAllowlist"))
            allowlist_values = self._read_policy_values(allowlist_path)
            allowlist_conflict = enabled and any(
                self._rule_overlaps_service(value, service) for value in allowlist_values.values()
            )
            if enabled:
                confirmed = managed_match and expected_entries_present and not allowlist_conflict
                service_blocked = external_block or confirmed
            else:
                # Preserve externally managed rules, but do not claim the service is
                # available when another URLBlocklist entry still matches it.
                confirmed = managed_match and expected_entries_present and not external_block
                service_blocked = external_block
            return {
                "browser": name,
                "policyPath": str(browser["policy_path"]),
                "enforcementStatus": "APPLIED" if confirmed else "FAILED",
                "ruleActive": service_blocked,
                "urlBlocked": service_blocked if confirmed else None,
                "effectiveAccessAllowed": not service_blocked if confirmed else None,
                "verified": confirmed,
                "ownedRuleCount": len(managed_entries),
                "error": (
                    "A browser URLAllowlist rule overrides some or all of this restriction."
                    if allowlist_conflict
                    else "An existing browser URLBlocklist rule still matches this service."
                    if not enabled and external_block
                    else None if confirmed
                    else "The browser policy registry values did not match the requested state."
                ),
            }
        finally:
            reg.CloseKey(key)

    def apply_service_policy(
        self,
        service: str,
        restriction_enabled: Optional[bool] = None,
        effective_restriction_enabled: Optional[bool] = None,
        effective_access_allowed: Optional[bool] = None,
        reason: str = "",
        browser_restrictions: Optional[Dict[str, bool]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if restriction_enabled is None:
            if effective_access_allowed is not None:
                restriction_enabled = not bool(effective_access_allowed)
            elif effective_restriction_enabled is not None:
                restriction_enabled = bool(effective_restriction_enabled)
            else:
                restriction_enabled = True
        clean_service = (service or "").strip().lower()
        if clean_service not in SERVICE_RULES:
            return {
                "controller": "WindowsBrowserURLPolicy",
                "service": clean_service,
                "enforcementStatus": "FAILED",
                "ruleActive": None,
                "urlBlocked": None,
                "verified": False,
                "error": "Unsupported restriction service.",
            }
        if self.platform_name != "nt" or self.registry is None:
            return {
                "controller": "WindowsBrowserURLPolicy",
                "service": clean_service,
                "enforcementStatus": "FAILED",
                "ruleActive": None,
                "urlBlocked": None,
                "verified": False,
                "error": "Windows browser policy registry is unavailable on this platform.",
            }

        browsers = self._available_browsers()
        if not browsers:
            return {
                "controller": "WindowsBrowserURLPolicy",
                "service": clean_service,
                "enforcementStatus": "FAILED",
                "ruleActive": None,
                "urlBlocked": None,
                "verified": False,
                "error": "No supported installed browser (Chrome or Edge) was found for URLBlocklist policy.",
                "browsers": [],
            }

        results = []
        for browser in browsers:
            try:
                browser_name = str(browser.get("name", ""))
                browser_enabled = bool(
                    (browser_restrictions or {}).get(browser_name, restriction_enabled)
                )
                result = self._apply_to_browser(clean_service, browser_enabled, browser)
                result["requestedRestrictionEnabled"] = browser_enabled
                results.append(result)
            except Exception as exc:
                results.append({
                    "browser": str(browser.get("name", "Unknown browser")),
                    "enforcementStatus": "FAILED",
                    "ruleActive": None,
                    "urlBlocked": None,
                    "verified": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        all_confirmed = bool(results) and all(item.get("verified") for item in results)
        blocked = any(item.get("ruleActive") is True for item in results)
        if not all_confirmed:
            error = next((item.get("error") for item in results if item.get("error")), "Browser policy update failed.")
        else:
            error = None
        return {
            "controller": "WindowsBrowserURLPolicy",
            "service": clean_service,
            "enforcementStatus": "APPLIED" if all_confirmed else "FAILED",
            "policyStatus": "APPLIED" if all_confirmed else "FAILED",
            "ruleActive": blocked,
            "urlBlocked": blocked if all_confirmed else None,
            "effectiveRestrictionEnabled": blocked if all_confirmed else None,
            "effectiveAccessAllowed": not blocked if all_confirmed else None,
            "verified": all_confirmed,
            "reason": "Windows Chromium URLBlocklist policy",
            "error": error,
            "browsers": results,
        }

