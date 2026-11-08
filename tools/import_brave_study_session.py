"""One-time authorized import using the EXE's shared WebView2 profile.

No web request, browser navigation, backend restart or session activation occurs.
The hidden bootstrap has no JS bridge. Its report contains counts/status only.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--verify-existing', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    from windows.brave_study_session import read_brave_chatgpt_cookies, StudyCookieError, apply_to_cookie_manager
    try:
        batch = read_brave_chatgpt_cookies()
    except StudyCookieError as error:
        result = dict(success=False, error=str(error))
        print(json.dumps(result))
        return 1
    if not args.apply and not args.verify_existing:
        print(json.dumps(dict(batch.summary(), success=True, applied=False)))
        batch.cookies.clear()
        return 0

    storage = Path(os.environ['LOCALAPPDATA']) / 'StrictRecords/WebView2'
    os.environ['WEBVIEW2_USER_DATA_FOLDER'] = str(storage)
    # Match the desktop EXE's interactive desktop and DPI setup before creating
    # any WinForms handles. A tool host can start on a different desktop.
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('StrictRecords.StrictRoutines.Desktop')
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    user32.OpenDesktopW.restype = ctypes.c_void_p
    user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
    desktop = user32.OpenDesktopW('Default', 0, False, 0x01FF)
    if desktop:
        user32.SetThreadDesktop(desktop)
    # Pywebview's normal settings match the running EXE, so WebView2 shares the
    # existing browser process/profile rather than opening/modifying its SQLite.
    import webview
    result = {}
    finished = threading.Event()
    ui_state = {}
    window = webview.create_window('StrictRecords session import', html='<html><body></body></html>',
                                  hidden=True, width=400, height=200)

    def complete():
        batch.cookies.clear()
        finished.set()
        if ui_state.get('timer'):
            ui_state['timer'].Stop()
            ui_state['timer'].Dispose()
        window.destroy()

    def loaded():
        from System import Action
        from windows.webview_lifecycle import find_webview_on_ui_thread

        def apply():
            stage = 'find_control'
            try:
                from System.Windows.Forms import Timer
                control = find_webview_on_ui_thread(window.native)
                core = control.CoreWebView2
                stage = 'profile_identity'
                result.update(destination_profile=str(core.Profile.ProfileName) or 'Default (legacy)',
                              private_profile=bool(core.Profile.IsInPrivateModeEnabled),
                              browser_process_id=int(core.BrowserProcessId))
                if (core.Profile.IsInPrivateModeEnabled
                        or str(core.Profile.ProfileName) not in {'', 'Default'}
                        or Path(str(core.Environment.UserDataFolder)).resolve() != storage.resolve()):
                    raise RuntimeError()
                stage = 'apply_cookies'
                result.update(apply_to_cookie_manager(core.CookieManager, batch) if args.apply
                              else dict(batch.summary(), success=True, imported=0))
                result.update(applied=args.apply, browser_process_id=int(core.BrowserProcessId),
                              destination_profile=str(core.Profile.ProfileName) or 'Default (legacy)')
                if not result['success']:
                    complete()
                    return
                # Validate values/flags in memory on the COM owning thread. The
                # only persisted evidence is the count; no cookie data export.
                expected = {(c.domain, c.name, c.path): c for c in batch.cookies}
                uris = sorted({'https://' + c.domain.lstrip('.') + c.path for c in batch.cookies})
                tasks = [core.CookieManager.GetCookiesAsync(uri) for uri in uris]
                timer = Timer()
                timer.Interval = 150
                ui_state['timer'] = timer

                def verified(*_):
                    if not all(task.IsCompleted for task in tasks):
                        return
                    try:
                        actual = {(str(c.Domain), str(c.Name), str(c.Path)): c
                                  for task in tasks for c in task.Result}
                        matched = 0
                        for key, source in expected.items():
                            value = actual.get(key)
                            if (value is not None and str(value.Value) == source.value
                                    and bool(value.IsSecure) == source.secure
                                    and bool(value.IsHttpOnly) == source.http_only
                                    and bool(value.IsSession) == (source.expires is None)
                                    and str(value.SameSite) == {-1: 'Lax', 0: 'None', 1: 'Lax', 2: 'Strict'}[source.same_site]
                                    and (source.expires is None or abs(float(value.Expires.Subtract(
                                        __import__('System').DateTime(1970, 1, 1)).TotalSeconds) - source.expires) < 1)):
                                matched += 1
                        result['verified'] = matched
                        result['success'] = matched == len(expected)
                        if not result['success']:
                            result['error'] = 'Some imported cookie attributes could not be verified.'
                    except Exception:
                        result.update(success=False, error='Cookie read-back verification failed.')
                    complete()

                timer.Tick += verified
                timer.Start()
            except Exception as error:
                result.update(success=False, stage=stage, error_type=type(error).__name__,
                              error='The shared desktop browser profile could not be initialized.')
                complete()
        window.native.BeginInvoke(Action(apply))

    def watchdog():
        if not finished.wait(35):
            result.update(success=False, error='The desktop browser import timed out.')
            try:
                window.destroy()
            except Exception:
                pass

    window.events.loaded += loaded
    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(gui='edgechromium', storage_path=str(storage), private_mode=False, debug=False)
    if not result:
        result.update(success=False, error='The desktop browser did not initialize.')
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
