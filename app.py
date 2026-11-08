"""
STRICTRECORDS — Desktop Application Launcher
Starts Central Sync Server, Cockpit & Admin Console, Windows Activity Agent,
and opens the browser interface.
"""

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Native diagnostics open before server/database startup and never change access state.
if __name__ == "__main__" and any(flag in sys.argv for flag in ("--strictbot-preview", "--strictbot-settings")):
    from windows.strictbot_preview import main as strictbot_preview_main
    strictbot_preview_main()
    raise SystemExit(0)

if __name__ == "__main__" and "--verify-build-report" in sys.argv:
    import desktop_app
    desktop_app.write_build_verification_report(sys.argv[sys.argv.index("--verify-build-report")+1])
    raise SystemExit(0)

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StrictRecords.StrictRoutines.Desktop")
    except Exception:
        pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import create_server
from windows.agent import StrictRecordsWindowsAgent


def _close_pyi_splash():
    try:
        import pyi_splash
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def is_server_running(host: str, port: int) -> bool:
    try:
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
        url = f"http://{probe_host}:{port}/api/system/health"
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        try:
            probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
            url = f"http://{probe_host}:{port}/api/status"
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False


def start_windows_agent_daemon(agent: StrictRecordsWindowsAgent, interval: int = 5, stop_event: threading.Event = None):
    while not (stop_event and stop_event.is_set()):
        try:
            agent.sample_current_activity(
                sample_duration_seconds=float(interval),
                enforce_restrictions=True,
                show_gui_popup=True,
            )
        except Exception:
            time.sleep(1)
        time.sleep(max(1, interval))


def main():
    parser = argparse.ArgumentParser(description="StrictRecords Desktop Application")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0 for LAN and local pairing)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web", action="store_true", help="Launch in default web browser instead of native desktop window")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser or desktop window")
    parser.add_argument("--no-agent", action="store_true", help="Do not run background Windows activity agent")
    parser.add_argument("--agent-interval", type=int, default=5, help="Agent polling interval in seconds")
    parser.add_argument("--background", action="store_true", help="Start minimized to system tray")
    args, unknown = parser.parse_known_args()

    # If native desktop mode is requested (default, unless --web is passed), delegate to desktop_app
    if not args.web and not args.no_browser:
        try:
            import desktop_app
            desktop_app.run_desktop_app()
            return
        except Exception as e:
            print(f"[NOTE] Native desktop shell fallback to browser launcher: {e}")

    app_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    app_url = f"http://{app_host}:{args.port}"
    print("=" * 65)
    print("STRICTRECORDS — DAILY DISCIPLINE OPERATING SYSTEM")
    print("=" * 65)

    if is_server_running(args.host, args.port):
        print(f"StrictRecords server is already active at {app_url}")
        _close_pyi_splash()
        if not args.no_browser:
            print("Opening browser...")
            webbrowser.open(app_url)
        return

    _close_pyi_splash()
    db = StrictRecordsDB()
    engine = ScoringEngine(db=db)
    agent = StrictRecordsWindowsAgent(db=db, engine=engine)

    stop_event = threading.Event()
    if not args.no_agent:
        agent_thread = threading.Thread(
            target=start_windows_agent_daemon,
            args=(agent, args.agent_interval, stop_event),
            daemon=True,
        )
        agent_thread.start()
        print("[OK] Background Windows Activity & Discipline Agent active.")

    server = create_server(host=args.host, port=args.port)
    print(f"[OK] StrictRecords Server online at {app_url} (Listening on {args.host}:{args.port} for Wi-Fi & USB pairing)")
    print("[OK] User Cockpit & Admin Console ready.")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(app_url)).start()

    print("\nPress Ctrl+C to terminate StrictRecords safely.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping StrictRecords...")
    finally:
        stop_event.set()
        server.server_close()
        print("StrictRecords stopped cleanly.")


if __name__ == "__main__":
    main()
