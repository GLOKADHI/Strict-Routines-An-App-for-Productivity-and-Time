"""CLI tool to easily check, lock, or unlock StrictRoutines browser extensions from any directory."""

import os
import sys

# Ensure repository root is in sys.path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from windows.browser_setup import get_browser_extension_setup


def main():
    setup = get_browser_extension_setup()
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    if action == "unlock":
        pin = sys.argv[2] if len(sys.argv) > 2 else "1234"
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        res = setup.unlock_extension(admin_pin=pin, duration_minutes=duration)
        print("Unlock Result:", res)
    elif action == "lock":
        res = setup.lock_extension()
        print("Lock Result:", res)
    elif action == "status":
        res = setup.lock_status()
        print("Lock Status:", res)
    else:
        print(f"Unknown action: {action}. Available actions: unlock [pin] [mins], lock, status")


if __name__ == "__main__":
    main()
