"""The supplied React Strict Session UI hosted in the existing desktop form.

Only the trusted local UI receives host telemetry and presentation commands.
Approved sites remain two independent, real WebView2 instances, never iframes.
Session policy and completion remain with the existing backend.
"""
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

log = logging.getLogger('StrictSessionFrontend')
UI_ORIGIN = 'https://strict-session.local'
SITES = {'chatgpt': 'https://chatgpt.com/', 'leetcode': 'https://leetcode.com/'}


def frontend_directory():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    packaged = root / 'strict_session_frontend'
    return packaged if packaged.is_dir() else root / 'strictfocus/imported-session-ui/dist'


def trusted_ui_source(source):
    try:
        uri = urlsplit(str(source))
        return uri.scheme == 'https' and uri.hostname == 'strict-session.local' and uri.port in (None, 443) and not uri.username and not uri.password
    except ValueError:
        return False


def scaled_rect(value, viewport, width, height):
    """Clamp DOM CSS coordinates to the native client area; reject nonfinite input."""
    if not isinstance(value, dict) or not isinstance(viewport, dict):
        raise ValueError('Invalid frontend rectangle')
    numbers = [float(value.get(k, 0)) for k in ('x', 'y', 'width', 'height')]
    vw, vh = float(viewport['width']), float(viewport['height'])
    if not all(math.isfinite(n) for n in (*numbers, vw, vh)) or vw <= 0 or vh <= 0:
        raise ValueError('Invalid frontend dimensions')
    x, y, w, h = numbers
    left = min(width, max(0, round(x * width / vw)))
    top = min(height, max(0, round(y * height / vh)))
    right = min(width, max(left, round((x + max(0, w)) * width / vw)))
    bottom = min(height, max(top, round((y + max(0, h)) * height / vh)))
    return left, top, right - left, bottom - top


class ZipStrictFocus:
    def __init__(self, api):
        self.api = api
        self.panel = self.form = self.chrome_view = self.view = None
        self.views = {}
        self.ready = False
        self.generation = 0
        self.old = None
        self.status, self.hardware = {}, {}
        self.browser = {key: dict(state='loading', url=url, error='') for key, url in SITES.items()}
        self._last_layout = None
        self._offline_validation = False
        self._timer = None
        self._navigation = {}
        self._load_started = {}
        self._complete = None
        self._end_pending = False
        self.end_enabled = False
        self._initialized = set()
        self._ui_ready = False
        self._open_done = None
        self._open_result = None
        self.ui_command_audit = []
        from windows.strict_integrity_events import IntegrityEpisodes
        self._ui_integrity=IntegrityEpisodes();self._last_attempt={}
        self._ios_invitation = None

    def available(self):
        return bool(self.api._window and getattr(self.api._window, 'native', None) and (frontend_directory() / 'index.html').is_file())

    def owns_foreground(self, pid):
        return pid == os.getpid()

    def _queue(self, callback):
        from System import Action
        generation = self.generation
        def run():
            if generation == self.generation and self.panel is not None and not self.panel.IsDisposed:
                callback()
        self.form.BeginInvoke(Action(run))

    def open(self, session_id, complete, *, offline_validation=False):
        if self.panel is not None:
            return dict(success=False, error='The focus workspace is already open.')
        if not self.available():
            return dict(success=False, error='The packaged Strict Session frontend is unavailable. Reinstall the current Windows build.')
        self._offline_validation = offline_validation
        self.status, self.hardware = {}, {}
        self.browser = {key: dict(state='loading', url=url, error='') for key, url in SITES.items()}
        self.end_enabled = False
        self._end_pending = False
        self._navigation, self._load_started = {}, {}
        self._ui_integrity.reset();self._last_attempt={}
        self._complete = lambda: complete(session_id)
        self.generation += 1
        generation = self.generation
        self._open_done, self._open_result = threading.Event(), {}
        from System import Action
        from System.Drawing import Color
        from System.Windows.Forms import Panel, DockStyle, Timer
        from Microsoft.Web.WebView2.WinForms import WebView2
        native = self.api._window.native

        def build():
            try:
                if generation != self.generation:
                    return
                from windows.webview_lifecycle import find_webview_on_ui_thread
                local = find_webview_on_ui_thread(native)
                if local is None or local.CoreWebView2 is None:
                    raise RuntimeError('Main desktop WebView2 has not initialized.')
                self.form = native
                self.old = (native.WindowState, native.FormBorderStyle, native.Bounds, native.TopMost)
                if not offline_validation:
                    from System.Windows.Forms import FormWindowState,FormBorderStyle
                    native.FormBorderStyle=getattr(FormBorderStyle,'None');native.WindowState=FormWindowState.Maximized
                panel = Panel(); panel.Dock = DockStyle.Fill; panel.BackColor = Color.FromArgb(8, 11, 17)
                self.panel = panel
                chrome = WebView2(); chrome.Dock = DockStyle.Fill
                panel.Controls.Add(chrome); self.chrome_view = chrome
                for key in SITES:
                    view = WebView2(); view.Visible = False
                    panel.Controls.Add(view); self.views[key] = view
                    view.CoreWebView2InitializationCompleted += lambda sender, args, name=key: self._initialize_site(name, args) if generation==self.generation and self.panel is not None else None
                    view.EnsureCoreWebView2Async(local.CoreWebView2.Environment)
                self.view = self.views['chatgpt']
                native.Controls.Add(panel); panel.BringToFront()
                chrome.CoreWebView2InitializationCompleted += lambda sender,args: self._initialize_chrome(sender,args) if generation==self.generation and self.panel is not None else None
                chrome.EnsureCoreWebView2Async(local.CoreWebView2.Environment)
                timer = Timer(); timer.Interval = 1000
                timer.Tick += self._check_loading; timer.Start(); self._timer = timer
                panel.Resize += self._resize
            except Exception as error:
                log.exception('Could not initialize imported Strict Session frontend')
                self._open_result.update(success=False, error=str(error)); self._open_done.set()
        native.BeginInvoke(Action(build))
        if not self._open_done.wait(20):
            self.close()
            return dict(success=False, error='The Strict Session frontend did not finish loading. Retry after the main window has loaded.')
        result = dict(self._open_result)
        if not result.get('success'):
            self.close()
        return result

    def _initialize_chrome(self, sender, args):
        try:
            if not args.IsSuccess:
                raise RuntimeError(str(args.InitializationException))
            from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind, CoreWebView2PermissionState
            core = self.chrome_view.CoreWebView2
            core.SetVirtualHostNameToFolderMapping('strict-session.local', str(frontend_directory()), CoreWebView2HostResourceAccessKind.DenyCors)
            core.Settings.AreDevToolsEnabled = False
            core.Settings.AreDefaultContextMenusEnabled = False
            core.Settings.IsStatusBarEnabled = False
            core.Settings.IsWebMessageEnabled = True
            core.Settings.AreBrowserAcceleratorKeysEnabled=False
            self._attach_shortcut_guard(self.chrome_view)
            core.WebMessageReceived += self._message
            def permission(sender, event):
                # The React UI consumes only host-owned preview frames. It must
                # never open a second camera/microphone or permission prompt.
                event.State = CoreWebView2PermissionState.Deny
            core.PermissionRequested += permission
            def navigation(sender, event):
                if not trusted_ui_source(event.Uri):
                    event.Cancel = True
            core.NavigationStarting += navigation
            core.NewWindowRequested += lambda sender, event: setattr(event, 'Handled', True)
            def loaded(sender, event):
                if not event.IsSuccess:
                    self._open_result.update(success=False, error='The local Strict Session interface could not load.')
                    self._open_done.set()
            core.NavigationCompleted += loaded
            self._initialized.add('chrome')
            core.Navigate(UI_ORIGIN + '/index.html?host=1&offline=' + ('1' if self._offline_validation else '0'))
        except Exception as error:
            self._open_result.update(success=False, error=str(error)); self._open_done.set()

    def _initialize_site(self, key, args):
        try:
            generation=self.generation
            if not args.IsSuccess:
                raise RuntimeError(str(args.InitializationException))
            from windows.embedded_strict_focus import allowed_url, tab_for_url
            view = self.views[key]; core = view.CoreWebView2
            core.Settings.AreDevToolsEnabled = False
            core.Settings.IsStatusBarEnabled = False
            core.Settings.IsWebMessageEnabled = False
            core.Settings.AreBrowserAcceleratorKeysEnabled = False
            # KeyDown is attached to the actual host controls as well as a
            # document-start listener, covering chrome and focused site content.
            self._attach_shortcut_guard(view)
            def starting(sender, event):
                if generation!=self.generation or self.panel is None:
                    event.Cancel=True;return
                if self._offline_validation or not allowed_url(event.Uri):
                    event.Cancel = True
                    if not self._offline_validation:
                        self._record_attempt('UNAUTHORIZED_URL','Navigation outside the approved study sites was blocked.')
                        self.browser[key]['error'] = 'Navigation outside the approved study sites was blocked.'
                        self._push()
                    return
                self._navigation[key] = event.NavigationId
                self._load_started[key] = time.monotonic()
                self.browser[key].update(state='loading', url=str(event.Uri), error='')
                self._push(); self._arrange()
            def completed(sender, event):
                if generation!=self.generation or self.panel is None:return
                if event.NavigationId != self._navigation.get(key):
                    return
                from windows.strict_browser_pane import load_error_message
                self._load_started.pop(key, None)
                self.browser[key].update(state='ready' if event.IsSuccess else 'error',
                                         error='' if event.IsSuccess else load_error_message(event.WebErrorStatus))
                self._push(); self._arrange()
            def popup(sender, event):
                event.Handled = True
                if generation!=self.generation or self.panel is None:return
                self._record_attempt('TAB_SWITCH_ATTEMPT','Opening another browser tab or window was blocked. Use the two approved panes.')
            core.NavigationStarting += starting; core.NavigationCompleted += completed
            core.NewWindowRequested += popup
            core.SourceChanged += lambda *_: self._site_source(key) if generation==self.generation and self.panel is not None else None
            core.HistoryChanged += lambda *_: self._site_source(key) if generation==self.generation and self.panel is not None else None
            def failed(*_):
                if generation!=self.generation or self.panel is None:return
                self.browser[key].update(state='error', error='The browser stopped responding. Retry this study site.')
                self._push(); self._arrange()
            core.ProcessFailed += failed
            view.GotFocus += lambda *_: setattr(self, 'view', view)
            self._initialized.add(key)
            self._finish_open()
        except Exception as error:
            self._open_result.update(success=False, error=str(error)); self._open_done.set()

    def _finish_open(self):
        if self._ui_ready and self._initialized == {'chrome', 'chatgpt', 'leetcode'} and not self.ready:
            self.ready = True
            if self._offline_validation:
                for value in self.browser.values():
                    value.update(state='offline')
            else:
                for key, url in SITES.items():
                    self.views[key].CoreWebView2.Navigate(url)
            self._push()
            self._open_result.update(success=True, frontend='supplied-react-zip')
            self._open_done.set()

    def _site_source(self, key):
        core=self.views[key].CoreWebView2
        self.browser[key].update(url=str(core.Source),canGoBack=bool(core.CanGoBack),canGoForward=bool(core.CanGoForward))
        self._push()

    def _check_loading(self, *_):
        for key, started in list(self._load_started.items()):
            if time.monotonic() - started >= 15 and self.browser[key]['state'] == 'loading':
                self._load_started.pop(key, None)
                self.browser[key].update(state='error', error='This site is taking longer than expected. Check your internet connection and retry.')
                self._push(); self._arrange()

    def _message(self, sender, event):
        if not trusted_ui_source(event.Source) or not self.panel or self.panel.IsDisposed:
            return
        try:
            raw = str(event.WebMessageAsJson)
            if len(raw) > 32768:
                return
            message = json.loads(raw)
            if not isinstance(message, dict):
                return
            kind = message.get('type')
            if kind == 'UI_READY':
                self._ui_ready = True; self._finish_open(); self._push()
            elif kind == 'LAYOUT':
                viewport=message.get('viewport')
                for key in SITES:
                    scaled_rect((message.get('panes') or {}).get(key) or {},viewport,self.panel.Width,self.panel.Height)
                camera=message.get('camera') or {}
                if camera.get('visible'):scaled_rect(camera,viewport,self.panel.Width,self.panel.Height)
                self._last_layout = message; self._arrange()
            elif kind == 'NAVIGATE':
                if self._offline_validation:self.ui_command_audit.append(dict(message))
                self._navigate(message)
            elif kind == 'SESSION_REQUEST_END':
                self._request_end()
            elif kind == 'IOS_CAMERA_INVITE':
                if self._offline_validation:
                    self._ios_invitation=dict(success=False,error='Offline layout preview cannot pair a camera.')
                    self._push()
                else:
                    def issue():
                        try:
                            # The EXE may use Antigravity's separate backend. Go
                            # through the same local authority in both modes.
                            import urllib.request
                            import urllib.error
                            origin='http://127.0.0.1:'+str(int(self.api._port))
                            request=urllib.request.Request(origin+'/api/strict_session/preparation/camera_invite',
                                data=json.dumps({'preparation_id':self.hardware.get('ios_preparation_id')}).encode(),
                                headers={'Content-Type':'application/json','Origin':origin,'X-StrictRoutines-Study':'1'})
                            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                            try:
                                with opener.open(request,timeout=10) as response:result=json.load(response)
                            except urllib.error.HTTPError as error:
                                result=json.load(error)
                        except (ValueError,PermissionError,AttributeError,OSError) as error:
                            result=dict(success=False,error=str(error))
                        self._queue(lambda: (setattr(self,'_ios_invitation',result),self._push()))
                    threading.Thread(target=issue,name='StrictIOSInvitation',daemon=True).start()
            elif kind == 'WINDOW_CONTROL':
                action = (message.get('payload') or {}).get('action')
                from System.Windows.Forms import FormWindowState
                if action == 'MAXIMIZE' and self._offline_validation:
                    self.form.WindowState = FormWindowState.Normal if self.form.WindowState == FormWindowState.Maximized else FormWindowState.Maximized
                elif action == 'MINIMIZE' and self._offline_validation:
                    self.form.WindowState = FormWindowState.Minimized
                elif action == 'CLOSE':
                    self._request_end()
            elif kind == 'SHORTCUT_BLOCKED':
                self._record_attempt('TAB_SWITCH_ATTEMPT','A browser tab/window switching shortcut was blocked.')
        except (ValueError, TypeError, KeyError, AttributeError):
            log.warning('Ignored invalid Strict Session UI message')

    def _navigate(self, message):
        if self._offline_validation:
            return
        key = message.get('pane')
        if key not in SITES:
            return
        core = self.views[key].CoreWebView2
        action = message.get('action')
        if action == 'back' and core.CanGoBack:
            core.GoBack()
        elif action == 'forward' and core.CanGoForward:
            core.GoForward()
        elif action == 'reload':
            core.Navigate(self.browser[key]['url'])
        elif action == 'navigate':
            from windows.embedded_strict_focus import allowed_url
            url = message.get('url')
            if isinstance(url, str) and len(url) < 4096 and allowed_url(url):
                core.Navigate(url)
            else:self._record_attempt('UNAUTHORIZED_URL','Navigation outside the approved study sites was blocked.')
        self.view = self.views[key]

    def _record_attempt(self,category,message):
        now=time.monotonic()
        if self._offline_validation or now-self._last_attempt.get(category,-100)<2:return
        self._last_attempt[category]=now
        self._ui_integrity.record(category,message,source='BROWSER')

    def integrity_events(self):
        with self._ui_integrity.lock:
            events=self._ui_integrity.snapshot();self._ui_integrity.events.clear()
            return events

    def _attach_shortcut_guard(self,view):
        from windows.embedded_strict_focus import blocked_accelerator
        def key(sender,event):
            if blocked_accelerator(int(event.KeyCode),ctrl=bool(event.Control),alt=bool(event.Alt)):
                event.Handled=True;event.SuppressKeyPress=True
                self._record_attempt('TAB_SWITCH_ATTEMPT','A tab or window switching shortcut was blocked.')
        view.KeyDown+=key
        try:
            import ctypes
            def accelerator(sender,event):
                user32=ctypes.windll.user32
                if blocked_accelerator(int(event.VirtualKey),ctrl=bool(user32.GetKeyState(0x11)&0x8000),alt=bool(user32.GetKeyState(0x12)&0x8000)):
                    event.Handled=True
                    self._record_attempt('TAB_SWITCH_ATTEMPT','A browser switching shortcut was blocked.')
            view.CoreWebView2Controller.AcceleratorKeyPressed+=accelerator
        except AttributeError:
            # Some WinForms SDK versions expose only the control event.
            pass

    def _request_end(self):
        if not self.end_enabled or self._end_pending or not self._complete:
            return
        self._end_pending = True
        def finish():
            try:
                self._complete()
            finally:
                self._end_pending = False
        threading.Thread(target=finish, name='StrictSessionUiComplete', daemon=True).start()

    def _resize(self, *_):
        # React reports fresh CSS geometry; avoid stretching stale rectangles.
        if self.chrome_view and self.chrome_view.CoreWebView2:
            self.chrome_view.CoreWebView2.PostWebMessageAsJson('{"type":"HOST_RESIZE"}')
        self._arrange()

    def _arrange(self):
        if not self._last_layout or self.panel is None or self.panel.IsDisposed:
            return
        from System.Drawing import Rectangle, Region
        data = self._last_layout; viewport = data.get('viewport')
        if not isinstance(viewport, dict):
            return
        camera = data.get('camera') or {}
        occlusion = scaled_rect(camera, viewport, self.panel.Width, self.panel.Height) if camera.get('visible') else None
        for key, view in self.views.items():
            value = (data.get('panes') or {}).get(key) or {}
            rect = scaled_rect(value, viewport, self.panel.Width, self.panel.Height)
            visible = bool(value.get('visible') and not data.get('overlayOpen') and not self._offline_validation and self.browser[key]['state'] == 'ready' and rect[2] > 0 and rect[3] > 0)
            view.Visible = visible
            if not visible:
                continue
            view.Bounds = Rectangle(*rect)
            region = Region(Rectangle(0, 0, rect[2], rect[3]))
            if occlusion:
                region.Exclude(Rectangle(occlusion[0] - rect[0], occlusion[1] - rect[1], occlusion[2], occlusion[3]))
            previous = view.Region; view.Region = region
            if previous:
                previous.Dispose()
            view.BringToFront()

    def _push(self):
        if self.chrome_view is None or self.chrome_view.IsDisposed or self.chrome_view.CoreWebView2 is None or not self._ui_ready:
            return
        from windows.strict_zip_state import runtime_payload
        payload = runtime_payload(self.status, self.hardware, self._offline_validation, self.browser)
        payload['ios_invitation'] = self._ios_invitation
        self.end_enabled = payload['endEnabled']
        self.chrome_view.CoreWebView2.PostWebMessageAsJson(json.dumps(dict(type='RUNTIME_STATE', payload=payload), allow_nan=False))

    def update(self, status, hardware):
        if not self.form or not self.panel:
            return
        def apply():
            self.status, self.hardware = dict(status), dict(hardware)
            self._push()
        self._queue(apply)

    def show(self):
        if not self.ready:
            return False
        self._queue(lambda: (setattr(self.panel, 'Visible', True), self.panel.BringToFront()))
        return True

    def focus(self):
        if not self.ready:
            return False
        def restore():
            self.panel.Visible = True; self.panel.BringToFront()
            self.form.Show(); self.form.Activate()
            (self.view if self.view and self.view.Visible else self.chrome_view).Focus()
        self._queue(restore)
        return True

    def toggle_focus_layout(self):
        if hasattr(self, 'chrome') and self.chrome:
            if hasattr(self.chrome, 'toggle'):
                return self.chrome.toggle()
        return False

    def close(self):
        self.generation += 1
        self.ready = False
        if not self.form:
            return
        from System import Action
        def restore():
            if self._timer:
                self._timer.Stop(); self._timer.Dispose(); self._timer = None
            if self.panel:
                self.form.Controls.Remove(self.panel); self.panel.Dispose()
            self.panel = self.chrome_view = self.view = None
            self.views = {}; self._last_layout = None; self._initialized = set(); self._ui_ready = False
            if self.old:
                self.form.FormBorderStyle = self.old[1]; self.form.WindowState = self.old[0]
                self.form.Bounds = self.old[2]; self.form.TopMost = self.old[3]; self.old = None
        try:
            self.form.BeginInvoke(Action(restore))
        except Exception:
            self.panel = None
