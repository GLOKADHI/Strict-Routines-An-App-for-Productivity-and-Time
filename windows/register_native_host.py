"""
StrictRecords — Windows Native Messaging Host Registry Installer
================================================================
Registers com.strictrecords.native_host in:
- HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\com.strictrecords.native_host
- HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts\\com.strictrecords.native_host

Supports install and uninstall operations.
"""

from __future__ import annotations

import os
import sys
import winreg
import json
from pathlib import Path

HOST_NAME = "com.strictrecords.native_host"
MANIFEST_FILE = "com.strictrecords.native_host.json"


def get_manifest_path() -> str:
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'windows'
    bridge=root/'StrictRecordsNativeBridge.exe'
    if not bridge.is_file():raise FileNotFoundError('The packaged native bridge is missing')
    destination=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'StrictRecords/native_host'
    destination.mkdir(parents=True,exist_ok=True)
    manifest=destination/MANIFEST_FILE
    manifest.write_text(json.dumps(dict(name=HOST_NAME,description='StrictRecords durable Night History',
        path=str(bridge),type='stdio',allowed_origins=['chrome-extension://iincmekgilffpjmmmcgloliadppioajc/']),indent=2),encoding='utf-8')
    return str(manifest)


def register_host() -> bool:
    manifest_path = get_manifest_path()
    if not os.path.isfile(manifest_path):
        print(f"Error: Manifest file does not exist at {manifest_path}")
        return False
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if manifest.get('allowed_origins')!=['chrome-extension://iincmekgilffpjmmmcgloliadppioajc/'] or not Path(manifest.get('path','')).is_file():
        raise ValueError('Native registration requires the exact companion origin and a real host executable')

    reg_targets = [
        (winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Perplexity\Comet\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Chromium\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{HOST_NAME}"),
    ]

    success = True
    for root_key, subkey in reg_targets:
        try:
            with winreg.CreateKey(root_key, subkey) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, manifest_path)
            print(f"Registered {HOST_NAME} at {subkey}")
        except Exception as exc:
            print(f"Failed to register at {subkey}: {exc}")
            success = False

    return success


def unregister_host() -> bool:
    reg_targets = [
        (winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Perplexity\Comet\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Chromium\NativeMessagingHosts\{HOST_NAME}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{HOST_NAME}"),
    ]

    for root_key, subkey in reg_targets:
        try:
            winreg.DeleteKey(root_key, subkey)
            print(f"Unregistered {subkey}")
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"Error unregistering {subkey}: {exc}")

    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        unregister_host()
    else:
        register_host()
