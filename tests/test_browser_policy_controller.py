import json
import unittest

from windows.browser_policy_controller import BrowserPolicyController, OWNERSHIP_PATH, SERVICE_RULES


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}
        self.fail_create = False

    def CreateKeyEx(self, _root, path, _reserved, _access):
        if self.fail_create:
            raise PermissionError("access denied")
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


class BrowserPolicyControllerTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.browser = {
            "name": "Google Chrome",
            "policy_path": r"Software\Policies\Google\Chrome\URLBlocklist",
            "executables": ("chrome.exe",),
            "candidates": (r"C:\Chrome\chrome.exe",),
        }
        self.controller = BrowserPolicyController(
            registry=self.registry,
            browsers=[self.browser],
            path_exists=lambda _path: True,
            executable_lookup=lambda _name: None,
            platform_name="nt",
        )

    def test_services_are_independent_and_external_entries_are_preserved(self):
        policy_path = self.browser["policy_path"]
        self.registry.keys[policy_path] = {"1": "github.com"}

        yt_on = self.controller.apply_service_policy("youtube", True)
        ig_on = self.controller.apply_service_policy("instagram", True)
        self.assertEqual("APPLIED", yt_on["enforcementStatus"])
        self.assertEqual("APPLIED", ig_on["enforcementStatus"])
        self.assertTrue(yt_on["urlBlocked"])
        self.assertTrue(ig_on["urlBlocked"])

        yt_off = self.controller.apply_service_policy("youtube", False)
        values = self.registry.keys[policy_path]
        self.assertEqual("APPLIED", yt_off["enforcementStatus"])
        self.assertFalse(yt_off["urlBlocked"])
        self.assertIn("github.com", values.values())
        self.assertTrue(any(value == SERVICE_RULES["instagram"][0] for value in values.values()))
        self.assertFalse(any("youtube" in value or "youtu.be" in value for value in values.values()))

    def test_preexisting_service_rule_is_not_claimed_or_removed(self):
        policy_path = self.browser["policy_path"]
        self.registry.keys[policy_path] = {"7": "[*.]youtube.com"}

        result = self.controller.apply_service_policy("youtube", True)
        self.assertEqual("APPLIED", result["enforcementStatus"])
        ownership = self.registry.keys[OWNERSHIP_PATH]
        owned = json.loads(ownership["youtube.google_chrome"])
        self.assertNotIn("[*.]youtube.com", [item["pattern"] for item in owned])

        self.controller.apply_service_policy("youtube", False)
        self.assertIn("[*.]youtube.com", self.registry.keys[policy_path].values())

    def test_missing_browser_and_registry_errors_are_reported(self):
        missing = BrowserPolicyController(
            registry=self.registry,
            browsers=[self.browser],
            path_exists=lambda _path: False,
            executable_lookup=lambda _name: None,
            platform_name="nt",
        ).apply_service_policy("instagram", True)
        self.assertEqual("FAILED", missing["enforcementStatus"])
        self.assertIn("No supported installed browser", missing["error"])

        self.registry.fail_create = True
        failed = self.controller.apply_service_policy("instagram", True)
        self.assertEqual("FAILED", failed["enforcementStatus"])
        self.assertIn("PermissionError", failed["error"])

    def test_url_allowlist_exception_prevents_claiming_service_is_blocked(self):
        policy_path = self.browser["policy_path"]
        allowlist_path = policy_path.replace("URLBlocklist", "URLAllowlist")
        self.registry.keys[policy_path] = {}
        self.registry.keys[allowlist_path] = {"1": "youtube.com/watch"}

        result = self.controller.apply_service_policy("youtube", True)

        self.assertEqual("FAILED", result["enforcementStatus"])
        self.assertIsNone(result["effectiveAccessAllowed"])
        self.assertIsNone(result["urlBlocked"])
        self.assertIn("URLAllowlist", result["error"])

    def test_external_partial_blocklist_prevents_claiming_service_is_fully_allowed(self):
        policy_path = self.browser["policy_path"]
        self.registry.keys[policy_path] = {"1": "youtube.com/watch"}

        result = self.controller.apply_service_policy("youtube", False)

        self.assertEqual("FAILED", result["enforcementStatus"])
        self.assertIsNone(result["effectiveAccessAllowed"])
        self.assertIsNone(result["urlBlocked"])
        self.assertIn("URLBlocklist", result["error"])


if __name__ == "__main__":
    unittest.main()
