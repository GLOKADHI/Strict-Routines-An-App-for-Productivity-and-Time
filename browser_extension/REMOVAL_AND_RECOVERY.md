# StrictRoutines Browser Companion — Managed Installation, Removal Protection & Legitimate Recovery

This document outlines the architecture, policy mechanisms, compatibility, and transparent removal instructions for the StrictRoutines browser companion extension on Windows.

---

## 1. Architecture & Policy Mechanisms

The objective of the managed installation workflow is to prevent accidental, impulsive, or casual removal of the StrictRoutines extension during active discipline sessions.

### Why Supported Browser Policies are Used
- **Extension Sandbox Limitations**: Ordinary browser extension JavaScript code runs inside a restricted browser sandbox. An extension **cannot prevent its own removal** using extension code alone.
- **Supported Enterprise Mechanisms**: Chromium-based browsers (Google Chrome, Microsoft Edge, Brave, Comet) natively support administrator management policies configured through the Windows Registry.
- **Specific Policies Enforced**:
  - **`URLBlocklist`**: Configured at `Software\Policies\Google\Chrome\URLBlocklist` (and Edge / Brave equivalents). Blocks browser-internal extension management pages:
    - `chrome://extensions*`
    - `edge://extensions*`
    - `chrome://settings/extensions*`
    - `chrome://settings/reset*` (prevents resetting browser settings to disable extensions)
    - `chrome://flags*`
  - **`ExtensionSettings`**: Configured at `Software\Policies\Google\Chrome\ExtensionSettings\<extension_id>`. Sets `toolbar_pin: "force_pinned"` to keep the extension visibly pinned to the toolbar.

---

## 2. Browser & Platform Compatibility

| Browser | Supported | Minimum Version | Management Mechanism |
| :--- | :---: | :---: | :--- |
| **Google Chrome** | **Yes** | 120+ | `HKCU\Software\Policies\Google\Chrome\URLBlocklist` & `ExtensionSettings` |
| **Microsoft Edge** | **Yes** | 120+ | `HKCU\Software\Policies\Microsoft\Edge\URLBlocklist` & `ExtensionSettings` |
| **Brave** | **Yes** | 120+ | `HKCU\Software\Policies\BraveSoftware\Brave\URLBlocklist` |
| **Comet** | **Yes** | 120+ | `HKCU\Software\Policies\Perplexity\Comet\URLBlocklist` |
| **Opera** | **Yes** | 100+ | `HKCU\Software\Policies\Opera\URLBlocklist` |
| **Mozilla Firefox** | **No** | N/A | Firefox requires Mozilla-signed XPI packages; unpacked Chromium bundle is unsupported. |

### Privilege Requirements
- Policies are applied in the Current User hive (`HKEY_CURRENT_USER`).
- Standard Windows user permissions are sufficient for `HKCU` policy enforcement.
- Does not affect other Windows user accounts on the machine.

---

## 3. Transparency & Anti-Malware Integrity

StrictRoutines adheres strictly to safe administrative design principles:
1. **Transparent Registry Keys**: All values are stored in documented, standard Windows policy locations. No keys are obfuscated or hidden.
2. **Explicit Ownership Tracking**: Only policies created by StrictRoutines are tracked under:
   `HKCU\Software\StrictRecords\BrowserPolicyOwnership`
   Pre-existing user or enterprise policies are never overwritten or deleted.
3. **No OS Resistance**: StrictRoutines does **not** block Task Manager, does not tamper with Windows Security/Defender, does not block OS administration, and does not alter core system binaries.
4. **No Hidden Reinstallation**: If an administrator removes the extension or clears the management policy, StrictRoutines **does not** run a hidden background process to reinstall it behind the administrator's back.
5. **No Profile Corruption**: Does not modify browser preference JSON files directly. All settings operate through standard Chromium policy interfaces.

---

## 4. Legitimate Recovery & Removal Instructions

Legitimate device administrators have two clear, documented methods to remove the extension and lift policies:

### Method 1: Using the StrictRecords Desktop App (Recommended)
1. Launch **StrictRecords.exe**.
2. Go to **Controls &rarr; Browser Companions**.
3. Click **Administrator Maintenance Unlock**.
4. Enter the 4-digit Administrator PIN (default: `1234` or as configured in your profile settings).
5. The desktop app temporarily removes all `URLBlocklist` rules for 10 minutes and unlocks the extension folders.
6. Open your browser to `chrome://extensions` (or `edge://extensions`) and click **Remove** on StrictRoutines.
7. To permanently disable management, set `uninstall_protection` to false in focus settings or leave the policy unlocked.

### Method 2: Manual Windows Registry Removal (Without StrictRecords)
If StrictRecords is uninstalled or inaccessible, the administrator can remove the policy entries manually at any time using PowerShell or Regedit:

```powershell
# 1. Close all running browser windows
Stop-Process -Name chrome, msedge, brave -Force -ErrorAction SilentlyContinue

# 2. View and remove StrictRoutines URLBlocklist entries
# Chrome:
Remove-Item -Path "HKCU:\Software\Policies\Google\Chrome\URLBlocklist" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "HKCU:\Software\Policies\Google\Chrome\ExtensionSettings" -Recurse -Force -ErrorAction SilentlyContinue

# Edge:
Remove-Item -Path "HKCU:\Software\Policies\Microsoft\Edge\URLBlocklist" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "HKCU:\Software\Policies\Microsoft\Edge\ExtensionSettings" -Recurse -Force -ErrorAction SilentlyContinue

# 3. Clean up the StrictRecords ownership tracker
Remove-Item -Path "HKCU:\Software\StrictRecords\BrowserPolicyOwnership" -Recurse -Force -ErrorAction SilentlyContinue

# 4. Remove local unpacked extension files
Remove-Item -Path "$env:LOCALAPPDATA\StrictRecords\browser_extension" -Recurse -Force -ErrorAction SilentlyContinue
```

After running these commands and reopening the browser:
- `chrome://extensions` is fully accessible again.
- The extension can be removed with standard one-click removal.
- The browser will no longer report URLBlocklist restrictions.

### Method 3: Self-Discipline Coding-Challenge Uninstall Authorization
For self-directed users who want to lift management policies without entering an administrator PIN:
1. In the StrictRoutines management app or via `http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge`, click **Request Uninstall**.
2. Solve the randomized local coding question (string reversal, loop accumulator, array sum, or Fibonacci).
3. The answer is verified locally. You have up to 3 attempts (exhaustion initiates a 5-minute cooldown).
4. Upon passing the challenge and confirming, the desktop app removes the `URLBlocklist` and `ExtensionSettings` management policies across all browsers, logs an authoritative audit event, and allows one-click uninstallation via `chrome://extensions`.

---

## 5. Protection Boundaries Summary

- **What it Protects Against**: Casual, impulsive, or accidental clicks on "Remove from Chrome" during a discipline session, and direct navigation to `chrome://extensions` to disable the companion while rules are active.
- **What it Does Not Claim**: Does not claim removal is impossible under all circumstances. A device administrator with registry access, the 4-digit PIN, or a completed coding challenge can always legitimately recover or remove the extension.
- **Companion Monitoring & Penalty Schedule**: If the extension is disabled while Chrome is running, a 2-minute grace period initiates with a visible Windows countdown notification. Re-enabling within 2 minutes cancels the countdown with zero penalties. If disabled beyond 2 minutes, penalties accrue at 1 point per complete minute (`floor(max(0, duration - 2 min))`), routed idempotently through the authoritative points ledger. Closing Chrome or shutting down Windows does NOT count as extension disablement.
