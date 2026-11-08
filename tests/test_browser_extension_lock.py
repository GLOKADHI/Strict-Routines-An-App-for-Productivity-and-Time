import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from windows.browser_policy_controller import BrowserPolicyController, SERVICE_RULES
from windows.browser_setup import BrowserExtensionSetup
from windows.restriction_manager import YouTubeRestrictionManager


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    def CreateKeyEx(self, _root, path, _reserved, _access):
        return self.keys.setdefault(path, {})

    def OpenKey(self, _root, path, _reserved=0, _access=0):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self.keys[path]

    def CloseKey(self, _key):
        return None

    def EnumValue(self, key, index):
        items = list(key.items())
        if index >= len(items):
            raise OSError("no more values")
        name, value = items[index]
        return name, value, self.REG_SZ

    def QueryValueEx(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        return key[name], self.REG_SZ

    def SetValueEx(self, key, name, _reserved, _kind, value):
        key[name] = value

    def DeleteValue(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        del key[name]


class FakeDB:
    def __init__(self, admin_pin="1234", uninstall_protection=True):
        self._admin_pin = admin_pin
        self._focus_settings = {"uninstall_protection": uninstall_protection}

    def verify_admin_pin(self, pin):
        return str(pin).strip() == self._admin_pin

    def get_focus_settings(self, user_id="user_lok"):
        return dict(self._focus_settings)

    def save_focus_settings(self, settings, user_id="user_lok"):
        self._focus_settings.update(settings)
        return dict(self._focus_settings)


class BrowserExtensionLockTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.browsers = [
            {
                "name": "Google Chrome",
                "policy_path": r"Software\Policies\Google\Chrome\URLBlocklist",
                "extension_settings_path": r"Software\Policies\Google\Chrome\ExtensionSettings",
                "executables": ("chrome.exe",),
                "candidates": (r"C:\Chrome\chrome.exe",),
            },
            {
                "name": "Microsoft Edge",
                "policy_path": r"Software\Policies\Microsoft\Edge\URLBlocklist",
                "extension_settings_path": r"Software\Policies\Microsoft\Edge\ExtensionSettings",
                "executables": ("msedge.exe",),
                "candidates": (r"C:\Edge\msedge.exe",),
            },
            {
                "name": "Brave",
                "policy_path": r"Software\Policies\BraveSoftware\Brave\URLBlocklist",
                "extension_settings_path": r"Software\Policies\BraveSoftware\Brave\ExtensionSettings",
                "executables": ("brave.exe",),
                "candidates": (r"C:\Brave\brave.exe",),
            },
        ]
        self.policy_controller = BrowserPolicyController(
            registry=self.registry,
            browsers=self.browsers,
            path_exists=lambda _path: True,
            executable_lookup=lambda _name: None,
            platform_name="nt",
        )
        self.db = FakeDB()
        self.temp_storage = tempfile.mkdtemp()
        self.launched = []
        self.setup = BrowserExtensionSetup(
            storage=Path(self.temp_storage),
            detector=lambda: [
                {"id": "chrome_id", "key": "chrome", "name": "Google Chrome", "supported": True, "executable": r"C:\Chrome\chrome.exe", "extensions_url": "chrome://extensions/"},
                {"id": "edge_id", "key": "edge", "name": "Microsoft Edge", "supported": True, "executable": r"C:\Edge\msedge.exe", "extensions_url": "edge://extensions/"},
            ],
            launcher=self.launched.append,
            db=self.db,
            policy_controller=self.policy_controller,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_storage, ignore_errors=True)

    def test_extension_protection_rules_apply_and_remove(self):
        res_lock = self.policy_controller.apply_service_policy("extension_protection", True)
        self.assertEqual("APPLIED", res_lock["enforcementStatus"])
        self.assertTrue(res_lock["ruleActive"])

        chrome_rules = list(self.registry.keys[r"Software\Policies\Google\Chrome\URLBlocklist"].values())
        self.assertIn("chrome://extensions*", chrome_rules)
        self.assertIn("edge://extensions*", chrome_rules)

        res_unlock = self.policy_controller.apply_service_policy("extension_protection", False)
        self.assertEqual("APPLIED", res_unlock["enforcementStatus"])
        self.assertFalse(res_unlock["ruleActive"])

        chrome_rules_after = list(self.registry.keys[r"Software\Policies\Google\Chrome\URLBlocklist"].values())
        self.assertFalse(any("extensions" in r for r in chrome_rules_after))

    def test_extension_setup_lock_state_and_admin_unlock(self):
        self.assertTrue(self.setup.is_locked())
        status = self.setup.lock_status()
        self.assertTrue(status["locked"])
        self.assertFalse(status["maintenance_mode"])

        with self.assertRaises(ValueError):
            self.setup.open_folder("chrome_id")

        bad_res = self.setup.unlock_extension("wrong_pin", duration_minutes=10)
        self.assertFalse(bad_res["success"])
        self.assertTrue(self.setup.is_locked())

        good_res = self.setup.unlock_extension("1234", duration_minutes=5)
        self.assertTrue(good_res["success"])
        self.assertFalse(self.setup.is_locked())

        status_unlocked = self.setup.lock_status()
        self.assertFalse(status_unlocked["locked"])
        self.assertTrue(status_unlocked["maintenance_mode"])
        self.assertGreater(status_unlocked["unlocked_remaining_seconds"], 0)

        relock_res = self.setup.lock_extension()
        self.assertTrue(relock_res["success"])
        self.assertTrue(self.setup.is_locked())

    def test_tamper_attempt_detection(self):
        rm = YouTubeRestrictionManager.__new__(YouTubeRestrictionManager)
        self.assertTrue(rm._is_extension_tamper_attempt("Extensions - Google Chrome"))
        self.assertTrue(rm._is_extension_tamper_attempt("Extensions - Microsoft Edge"))
        self.assertTrue(rm._is_extension_tamper_attempt("Extensions - Brave"))
        self.assertTrue(rm._is_extension_tamper_attempt("chrome://extensions"))
        self.assertTrue(rm._is_extension_tamper_attempt('Remove "StrictRoutines — Google Chrome"?'))
        self.assertTrue(rm._is_extension_tamper_attempt("Remove StrictRoutines"))
        self.assertTrue(rm._is_extension_tamper_attempt("Remove extension"))
        self.assertFalse(rm._is_extension_tamper_attempt("Python Tutorial - YouTube"))
        self.assertFalse(rm._is_extension_tamper_attempt("GitHub - strictrecords"))


    def test_extension_settings_toolbar_pin_application_and_removal(self):
        ext_chrome_key = r"Software\Policies\Google\Chrome\ExtensionSettings\iincmekgilffpjmmmcgloliadppioajc"
        ext_edge_key = r"Software\Policies\Microsoft\Edge\ExtensionSettings\iincmekgilffpjmmmcgloliadppioajc"
        ext_brave_key = r"Software\Policies\BraveSoftware\Brave\ExtensionSettings\iincmekgilffpjmmmcgloliadppioajc"

        # Apply extension protection
        res_lock = self.policy_controller.apply_service_policy("extension_protection", True)
        self.assertEqual("APPLIED", res_lock["enforcementStatus"])
        self.assertEqual("force_pinned", self.registry.keys.get(ext_chrome_key, {}).get("toolbar_pin"))
        self.assertEqual("force_pinned", self.registry.keys.get(ext_edge_key, {}).get("toolbar_pin"))
        self.assertEqual("force_pinned", self.registry.keys.get(ext_brave_key, {}).get("toolbar_pin"))

        # Unlock / remove extension protection
        res_unlock = self.policy_controller.apply_service_policy("extension_protection", False)
        self.assertEqual("APPLIED", res_unlock["enforcementStatus"])
        self.assertNotIn("toolbar_pin", self.registry.keys.get(ext_chrome_key, {}))
        self.assertNotIn("toolbar_pin", self.registry.keys.get(ext_edge_key, {}))
        self.assertNotIn("toolbar_pin", self.registry.keys.get(ext_brave_key, {}))

    def test_verify_policy_enforcement_and_tamper_detection(self):
        # Initial locked state with active policies
        lock_res = self.setup.lock_extension()
        self.assertTrue(lock_res["success"])

        # Check enforcement is verified
        enforcement = self.setup.verify_policy_enforcement()
        self.assertTrue(enforcement["enforced"])
        self.assertTrue(len(enforcement["details"]) > 0)

        # Status reflects ADMINISTRATOR_MANAGED
        status = self.setup.lock_status()
        self.assertEqual("ADMINISTRATOR_MANAGED", status["installation_mode"])
        self.assertTrue(status["managed_installation"])
        self.assertTrue(status["policy_enforced"])
        self.assertFalse(status["tamper_detected"])

        # Simulate tampering: external tool or user deleted URLBlocklist rules
        self.registry.keys[r"Software\Policies\Google\Chrome\URLBlocklist"] = {}

        # Enforcement check detects failure
        tampered_enforcement = self.setup.verify_policy_enforcement()
        self.assertFalse(tampered_enforcement["enforced"])

        # Status reflects POLICY_NOT_ENFORCED and flags tamper_detected
        tampered_status = self.setup.lock_status()
        self.assertEqual("POLICY_NOT_ENFORCED", tampered_status["installation_mode"])
        self.assertFalse(tampered_status["policy_enforced"])
        self.assertTrue(tampered_status["tamper_detected"])

        overall_status = self.setup.status()
        self.assertEqual("POLICY_NOT_ENFORCED", overall_status["installation_mode"])
        self.assertTrue(overall_status["tamper_detected"])

    def test_setup_managed_mode_vs_standard_mode(self):
        # 1. Managed Setup
        res_managed = self.setup.setup(managed=True)
        self.assertTrue(res_managed["success"])
        self.assertEqual("ADMINISTRATOR_MANAGED", res_managed["installation_mode"])
        self.assertTrue(res_managed["managed_installation"])
        self.assertTrue(self.setup.is_locked())
        chrome_rules = list(self.registry.keys.get(r"Software\Policies\Google\Chrome\URLBlocklist", {}).values())
        self.assertTrue(any("extensions" in r for r in chrome_rules))

        # Reset setup throttle timer
        self.setup.last_setup = 0.0

        # 2. Standard (Unmanaged) Setup
        res_standard = self.setup.setup(managed=False)
        self.assertTrue(res_standard["success"])
        self.assertEqual("STANDARD", res_standard["installation_mode"])
        self.assertFalse(res_standard["managed_installation"])
        self.assertFalse(self.setup.is_locked())
        chrome_rules_after = list(self.registry.keys.get(r"Software\Policies\Google\Chrome\URLBlocklist", {}).values())
        self.assertFalse(any("extensions" in r for r in chrome_rules_after))

        status = self.setup.status()
        self.assertEqual("STANDARD", status["installation_mode"])
        self.assertFalse(status["managed_installation"])

    def test_maintenance_unlock_mode_status(self):
        self.setup.lock_extension()
        unlock_res = self.setup.unlock_extension("1234", duration_minutes=10)
        self.assertTrue(unlock_res["success"])

        status = self.setup.lock_status()
        self.assertEqual("MAINTENANCE_UNLOCKED", status["installation_mode"])
        self.assertTrue(status["maintenance_mode"])
        self.assertFalse(status["managed_installation"])

        overall = self.setup.status()
        self.assertEqual("MAINTENANCE_UNLOCKED", overall["installation_mode"])
        self.assertTrue(overall["maintenance_mode"])


if __name__ == "__main__":
    unittest.main()
