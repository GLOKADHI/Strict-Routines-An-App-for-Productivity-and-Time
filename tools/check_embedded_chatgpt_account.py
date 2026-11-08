"""Read the ChatGPT session through StrictRecords' existing browser profile.

No Computer Use, authentication-form automation, JS injection or cookie export.
Only a status and the explicitly requested account email may leave memory.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from windows.chatgpt_account_check import SESSION_URL, account_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    storage = Path(os.environ['LOCALAPPDATA']) / 'StrictRecords/WebView2'
    os.environ['WEBVIEW2_USER_DATA_FOLDER'] = str(storage)
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

    import webview
    window = webview.create_window('StrictRecords account verification',
                                  html='<html><body></body></html>', hidden=True,
                                  width=400, height=200)
    result = {}
    state = {}
    finished = threading.Event()

    def complete(answer):
        if finished.is_set():
            return
        result.update(answer)
        finished.set()
        if state.get('timer'):
            state['timer'].Stop()
            state['timer'].Dispose()
        window.destroy()

    def loaded():
        from System import Action
        from windows.webview_lifecycle import find_webview_on_ui_thread

        def begin():
            if state.get('started') or finished.is_set():
                return
            state['started'] = True
            try:
                from System.IO import StreamReader
                from System.Windows.Forms import Timer
                control = find_webview_on_ui_thread(window.native)
                core = control.CoreWebView2
                if (core.Profile.IsInPrivateModeEnabled
                        or str(core.Profile.ProfileName) not in {'', 'Default'}
                        or Path(str(core.Environment.UserDataFolder)).resolve() != storage.resolve()):
                    raise RuntimeError()
                result['browser_process_id'] = int(core.BrowserProcessId)
                result['profile'] = 'StrictRecords persistent browser'

                def navigating(_, event):
                    # Do not navigate to login or automate any security challenge.
                    uri = urlsplit(str(event.Uri))
                    if uri.scheme != 'https' or uri.hostname != 'chatgpt.com' or uri.path != '/api/auth/session':
                        event.Cancel = True
                        complete(dict(success=False, authenticated=False,
                                      error='ChatGPT requires interactive sign-in or a security check.'))

                def response_received(_, event):
                    if str(event.Request.Uri).rstrip('/') != SESSION_URL:
                        return
                    response = event.Response
                    status = int(response.StatusCode)
                    content_type = (str(response.Headers.GetHeader('Content-Type'))
                                    if response.Headers.Contains('Content-Type') else '')
                    if status != 200 or 'json' not in content_type.lower():
                        complete(account_result(status, content_type, '', args.email))
                        return
                    state['response_task'] = response.GetContentAsync()
                    state['status'], state['content_type'] = status, content_type

                def poll(*_):
                    task = state.get('response_task')
                    if task is None or not task.IsCompleted:
                        return
                    try:
                        stream = task.Result
                        reader = StreamReader(stream)
                        try:
                            # The full website response stays inside this process.
                            # Bound the amount read; no body is logged or saved.
                            from System import Array, Char
                            chars = Array.CreateInstance(Char, 131073)
                            length = reader.ReadBlock(chars, 0, 131073)
                            if length > 131072:
                                raise ValueError()
                            text = ''.join(str(chars[i]) for i in range(length))
                        finally:
                            reader.Dispose()
                        answer = account_result(state['status'], state['content_type'], text, args.email)
                        del text
                        complete(answer)
                    except Exception:
                        complete(dict(success=False, authenticated=False,
                                      error='The ChatGPT account response could not be verified.'))

                timer = Timer()
                timer.Interval = 200
                timer.Tick += poll
                state['timer'] = timer
                core.NavigationStarting += navigating
                core.WebResourceResponseReceived += response_received
                timer.Start()
                core.Navigate(SESSION_URL)
            except Exception:
                complete(dict(success=False, authenticated=False,
                              error='The embedded browser account check could not initialize.'))

        window.native.BeginInvoke(Action(begin))

    def watchdog():
        if not finished.wait(35):
            result.update(success=False, authenticated=False,
                          error='The ChatGPT account check timed out; sign-in is unverified.')
            try:
                window.destroy()
            except Exception:
                pass

    window.events.loaded += loaded
    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(gui='edgechromium', storage_path=str(storage), private_mode=False, debug=False)
    if not result:
        result.update(success=False, authenticated=False, error='The browser did not initialize.')
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result.get('account_matches') else 1


if __name__ == '__main__':
    raise SystemExit(main())
