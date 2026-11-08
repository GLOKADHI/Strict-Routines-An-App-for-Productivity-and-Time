"""
Two fixed study WebView2 controls inside the existing EXE form.
Pane headers swap positions; the bounded divider resizes without reparenting
or navigating either browser. Session state remains backend-authoritative.

Updated with:
- Maximized browser workspace and compact upper monitoring header
- Distraction-Free Focus Layout toggle
- Floating, draggable, resizable webcam preview overlay with eye-icon visibility toggle
- High-density monitoring strip with real-time proctoring, screen-share, and device indicators
- Side-by-side ChatGPT and LeetCode browser panes with independent scrolling
"""

import threading
import base64
import os
import datetime
from urllib.parse import urlsplit
from windows.strict_focus_layout import StudyPaneLayout

CHATGPT_HOSTS = {'chatgpt.com', 'openai.com', 'oaistatic.com', 'oaiusercontent.com', 'auth0.com', 'challenges.cloudflare.com'}
TAB_URLS = {'chatgpt': 'https://chatgpt.com/', 'leetcode': 'https://leetcode.com/'}
TAB_HOSTS = {'chatgpt': CHATGPT_HOSTS, 'leetcode': {'leetcode.com'}}
ALLOWED = CHATGPT_HOSTS | {'leetcode.com'}


def allowed_url(url):
    try:
        u = urlsplit(str(url))
        host = (u.hostname or '').lower()
        return u.scheme == 'https' and u.port in (None, 443) and not u.username and not u.password and any(host == d or host.endswith('.' + d) for d in ALLOWED)
    except ValueError:
        return False


def blocked_accelerator(key, *, ctrl=False, alt=False):
    return bool((ctrl and key in {9, 0x21, 0x22, 0x54, 0x4E, 0x57, 0x4C, 0x73})
                or (alt and key in {9, 0x25, 0x27, 0x73}))


def tab_for_url(url):
    if not allowed_url(url):
        return None
    host = urlsplit(str(url)).hostname.lower()
    return next((key for key, hosts in TAB_HOSTS.items() if any(host == h or host.endswith('.' + h) for h in hosts)), None)


class NativeStrictFocus:
    def __init__(self, api):
        self.api = api
        self.panel = None
        self.view = None
        self.form = None
        self.labels = {}
        self.ready = False
        self.old = None
        self.generation = 0
        self.preview_image = None
        self.views = {}
        self.tab_buttons = {}
        self.active_tab = 'chatgpt'
        self.pane_layout = StudyPaneLayout()
        from windows.strict_focus_preferences import load
        self.pane_layout.restore_preferences(load())
        self.active_tab=self.pane_layout.active
        self.chrome = None
        self.panes = {}
        self.site_panes = {}
        self.layout_toolbar = None
        self.workspace = None
        self.splitter = None
        self._arrange = None
        self._select_tab = None
        self._drag_handlers = {}
        self.offline_navigation = []
        self._divider_handlers = None
        self._hud_texts = ('', '')
        self.demo_mode = False

        # Workspace & Camera Enhancements
        self.camera_overlay = None
        self.focus_layout_active = False
        self._parent_move_handler = None
        self._parent_resize_handler = None
        self._clock_timer = None

    def available(self):
        return bool(self.api._window and getattr(self.api._window, 'native', None))

    def owns_foreground(self, pid):
        return pid == os.getpid()

    def focus(self):
        if not self.ready:
            return False
        from System import Action
        generation=self.generation

        def restore():
            if generation!=self.generation or not self.ready or self.panel is None or self.panel.IsDisposed:return
            self.panel.Visible = True
            self.panel.BringToFront()
            self.form.Show()
            self.form.Activate()
            self.view.Focus()
            if self.camera_overlay and not self.camera_overlay.is_user_hidden:
                self.camera_overlay.show()

        self.form.BeginInvoke(Action(restore))
        return True

    def toggle_focus_layout(self):
        """Toggles between Standard Compact Monitoring and Distraction-Free Focus Layout."""
        self.focus_layout_active = not self.focus_layout_active
        if self.chrome:
            self.chrome.toggle()
        top = self.labels.get('header')
        monitoring_strip = self.labels.get('monitoring_strip')
        compact_container = self.labels.get('compact_focus_container')
        if top and monitoring_strip and compact_container:
            if self.focus_layout_active:
                top.Height = 34
                monitoring_strip.Visible = False
                compact_container.Visible = True
            else:
                top.Height = 88
                monitoring_strip.Visible = True
                compact_container.Visible = False

    def open(self, session_id, complete, *, offline_validation=False):
        self.session_id = session_id
        self._offline_validation=offline_validation
        if offline_validation:self.pane_layout=StudyPaneLayout()
        if self.panel is not None:
            return {'success': False, 'error': 'The focus control is already open.'}
        if not self.available():
            return {'success': False, 'error': 'Open Strict Session inside the Windows EXE.'}

        from System import Action, Uri
        from System.Drawing import Color, Font, FontStyle, Point, Size, Rectangle, ContentAlignment
        from System.Windows.Forms import (
            Panel, Label, Button, DockStyle, FormWindowState, FormBorderStyle,
            FlatStyle, PictureBox, PictureBoxSizeMode, ProgressBar, ProgressBarStyle,
            Cursors, MouseButtons, AccessibleRole, Timer
        )
        from Microsoft.Web.WebView2.WinForms import WebView2

        native = self.api._window.native
        done = threading.Event()
        result = {}
        self.generation += 1
        generation = self.generation

        def build():
            try:
                if generation != self.generation:
                    done.set()
                    return
                from windows.webview_lifecycle import find_webview_on_ui_thread
                local = find_webview_on_ui_thread(native)
                if local is None or local.CoreWebView2 is None:
                    raise RuntimeError('Main WebView2 is not ready.')
                if self.panel is not None:
                    raise RuntimeError('The embedded focus view is already open.')

                self.form = native
                self.old = (native.WindowState, native.FormBorderStyle, native.Bounds, native.TopMost)

                panel = Panel()
                panel.Dock = DockStyle.Fill
                panel.BackColor = Color.FromArgb(12, 15, 23)

                from windows.strict_focus_badges import PolishedFocusChrome
                self.chrome=PolishedFocusChrome(self)
                top=self.chrome.header
                self.labels['header']=top

                lbl_alert=Label();lbl_alert.Dock=DockStyle.Top;lbl_alert.Height=20
                lbl_alert.BackColor=Color.FromArgb(42,35,25);lbl_alert.ForeColor=Color.FromArgb(196,174,131)
                lbl_alert.Font=Font('Segoe UI',8);lbl_alert.AutoEllipsis=True
                lbl_alert.UseCompatibleTextRendering=False
                lbl_alert.Visible=False;self.labels['alert']=lbl_alert

                # ==========================================================
                # BOTTOM BAR: Session info + Red outlined End Session button
                # ==========================================================
                bottom = Panel()
                bottom.Dock = DockStyle.Bottom
                bottom.Height = 44
                bottom.BackColor = Color.FromArgb(12, 15, 23)

                btn_bottom_chevron = Button()
                btn_bottom_chevron.Text = "^"
                btn_bottom_chevron.ForeColor = Color.FromArgb(100, 116, 139)
                btn_bottom_chevron.BackColor = Color.Transparent
                btn_bottom_chevron.FlatStyle = FlatStyle.Flat
                btn_bottom_chevron.FlatAppearance.BorderSize = 0
                btn_bottom_chevron.Font = Font("Segoe UI", 9, FontStyle.Bold)
                btn_bottom_chevron.Left = 8
                btn_bottom_chevron.Top = 9
                btn_bottom_chevron.Click += lambda *_: self.toggle_focus_layout()
                btn_bottom_chevron.Width = 26
                btn_bottom_chevron.Height = 26
                bottom.Controls.Add(btn_bottom_chevron)

                lbl_bottom_info = Label()
                lbl_bottom_info.UseCompatibleTextRendering=False;lbl_bottom_info.AutoEllipsis=True
                lbl_bottom_info.Text = "Allowed: chatgpt.com, leetcode.com   |   Focus: DSA + Practice"
                lbl_bottom_info.ForeColor = Color.FromArgb(148, 163, 184)
                lbl_bottom_info.Font = Font("Segoe UI", 9)
                lbl_bottom_info.Left = 38
                lbl_bottom_info.Top = 13
                lbl_bottom_info.AutoSize = True
                bottom.Controls.Add(lbl_bottom_info)
                self.labels['status'] = lbl_bottom_info

                btn_end = Button()
                btn_end.Enabled=False
                btn_end.Text = "EXIT DEMO & RESTORE DESKTOP" if self.demo_mode else "▢  End Session"
                btn_end.ForeColor = Color.FromArgb(248, 113, 113)
                btn_end.BackColor = Color.FromArgb(24, 16, 20)
                btn_end.FlatStyle = FlatStyle.Flat
                btn_end.FlatAppearance.BorderSize = 1
                btn_end.FlatAppearance.BorderColor = Color.FromArgb(239, 68, 68)
                btn_end.Font = Font("Segoe UI", 9.5, FontStyle.Bold)
                btn_end.Width = 150
                btn_end.Height = 32
                btn_end.Top = 6
                btn_end.Cursor = Cursors.Hand
                btn_end.Click += lambda *_: threading.Thread(target=complete, args=(session_id,), daemon=True).start()
                bottom.Controls.Add(btn_end)
                self.labels['end'] = btn_end


                btn_controls = Button()
                btn_controls.Text = "Admin Exit"
                btn_controls.ForeColor = Color.FromArgb(148, 163, 184)
                btn_controls.BackColor = Color.FromArgb(20, 26, 38)
                btn_controls.FlatStyle = FlatStyle.Flat
                btn_controls.FlatAppearance.BorderSize = 0
                btn_controls.Font = Font("Segoe UI", 8.5)
                btn_controls.Width = 90
                btn_controls.Height = 32
                btn_controls.Top = 6
                btn_controls.Cursor = Cursors.Hand

                def show_controls(*_):
                    if self.demo_mode:
                        threading.Thread(target=complete, args=(session_id,), daemon=True).start()
                        return
                    panel.Visible = False
                    if self.camera_overlay:
                        self.camera_overlay.window.Visible = False
                    threading.Thread(
                        target=lambda: self.api._window.evaluate_js("navAppPage('strict-session');openEndSessionModal();"),
                        daemon=True
                    ).start()

                btn_controls.Click += show_controls
                bottom.Controls.Add(btn_controls)
                self.labels['controls'] = btn_controls

                def layout_bottom(*_):
                    bw = bottom.ClientSize.Width
                    btn_end.Width = min(270,max(150,bw//3)) if self.demo_mode else 190
                    btn_end.Left = max(0,bw - btn_end.Width - 12)
                    btn_controls.Visible = not self.demo_mode
                    btn_controls.Left = btn_end.Left - btn_controls.Width - 8
                    lbl_bottom_info.AutoSize=False
                    lbl_bottom_info.Width=max(0,(btn_end.Left if self.demo_mode else btn_controls.Left)-lbl_bottom_info.Left-8)
                self._layout_bottom=layout_bottom
                bottom.Resize += layout_bottom
                layout_bottom()

                self._layout_proc=self.chrome.arrange

                # ==========================================================
                # WORKSPACE: Dual Browser Panes (ChatGPT & LeetCode)
                # Occupies all remaining vertical space!
                # ==========================================================
                browser = WebView2()
                browser.Dock = DockStyle.Fill
                browser.DefaultBackgroundColor = Color.FromArgb(15, 17, 25)
                leetcode = WebView2()
                leetcode.Dock = DockStyle.Fill
                leetcode.DefaultBackgroundColor = Color.FromArgb(15, 17, 25)
                self.views = {'chatgpt': browser, 'leetcode': leetcode}

                workspace = Panel()
                workspace.Dock = DockStyle.Fill
                workspace.BackColor = Color.FromArgb(7, 10, 16)
                self.workspace = workspace

                splitter = Panel()
                splitter.BackColor = Color.FromArgb(30, 41, 59)
                splitter.Cursor = Cursors.VSplit
                splitter.TabStop = True
                splitter.AccessibleName = 'Resize study panes'
                splitter.AccessibleRole = AccessibleRole.Separator
                self.splitter = splitter
                workspace.Controls.Add(splitter)

                # Center divider pill icon (<->)
                divider_icon = Label()
                divider_icon.Text = '⋮'
                divider_icon.ForeColor = Color.FromArgb(148, 163, 184)
                divider_icon.BackColor = Color.FromArgb(20, 26, 38)
                divider_icon.Font = Font("Segoe UI", 9, FontStyle.Bold)
                divider_icon.Width = StudyPaneLayout.DIVIDER
                divider_icon.Height = 24
                divider_icon.TextAlign = ContentAlignment.MiddleCenter
                divider_icon.Cursor = Cursors.VSplit
                splitter.Controls.Add(divider_icon)

                def layout_divider(*_):
                    divider_icon.Left = 0
                    divider_icon.Top = (splitter.Height - 24) // 2
                splitter.Resize += layout_divider

                def arrange(*_):
                    if workspace.IsDisposed:
                        return
                    bounds = self.pane_layout.geometry(workspace.ClientSize.Width, workspace.ClientSize.Height)
                    workspace.SuspendLayout()
                    try:
                        for name, pane in self.panes.items():
                            pane.Visible = name in bounds
                            if name in bounds:
                                pane.Bounds = Rectangle(*bounds[name])
                        for ui in self.site_panes.values():ui.set_split(self.pane_layout.side_by_side)
                        if self.layout_toolbar:self.layout_toolbar.update(self.pane_layout)
                        splitter.Visible = bounds['divider'] is not None
                        if bounds['divider'] is not None:
                            splitter.Bounds = Rectangle(*bounds['divider'])
                            splitter.BringToFront()
                    finally:
                        workspace.ResumeLayout()
                    if self.camera_overlay:
                        self.camera_overlay.on_parent_move_or_resize()

                self._arrange = arrange
                workspace.Resize += arrange
                workspace.LocationChanged += arrange

                def select_tab(key, focus=True):
                    if not self.pane_layout.select(key):
                        return
                    self.active_tab = key
                    self.view = self.views[key]
                    arrange()
                    if focus:
                        if self.view.Visible:self.view.Focus()
                        else:self.panes[key].Focus()
                self._select_tab = select_tab

                def set_layout_mode(side_by_side):
                    self.pane_layout.side_by_side = side_by_side
                    arrange()
                    select_tab(self.active_tab)
                    self._save_workspace_preferences()

                from windows.strict_browser_pane import StudyBrowserPane
                def swap_panes():
                    self.pane_layout.swap();arrange();self._save_workspace_preferences()
                def focus_site(key):
                    self.pane_layout.side_by_side=False;select_tab(key);self._save_workspace_preferences()
                def restore_split():
                    self.pane_layout.ratio=.5;self.pane_layout.order=['chatgpt','leetcode'];set_layout_mode(True)
                from windows.strict_layout_toolbar import SharedStudyToolbar
                self.layout_toolbar=SharedStudyToolbar({
                    'split':lambda:set_layout_mode(True),'swap':swap_panes,
                    'chatgpt':lambda:focus_site('chatgpt'),'leetcode':lambda:focus_site('leetcode'),
                    'restore':restore_split})
                for key,label in [('chatgpt','ChatGPT'),('leetcode','LeetCode')]:
                    view=self.views[key]
                    ui=StudyBrowserPane(key,label,TAB_URLS[key],view,select_tab,swap_panes,
                        lambda:set_layout_mode(not self.pane_layout.side_by_side),offline=offline_validation)
                    self.site_panes[key]=ui
                    pane=ui.host;header=ui.toolbar;tab_btn=ui.tab_button
                    self.tab_buttons[key]=tab_btn
                    drag={}
                    def tab_down(sender,event,target=key,gesture=drag):
                        if event.Button==MouseButtons.Left:
                            gesture['origin']=sender.PointToScreen(Point(event.X,event.Y))
                            gesture['active']=False
                    def tab_move(sender,event,gesture=drag):
                        if 'origin' not in gesture:return
                        current=sender.PointToScreen(Point(event.X,event.Y));origin=gesture['origin']
                        if abs(current.X-origin.X)+abs(current.Y-origin.Y)>8:
                            gesture['active']=True;sender.Capture=True
                    def tab_up(sender,event,target=key,gesture=drag):
                        if gesture.get('active'):
                            point=workspace.PointToClient(sender.PointToScreen(Point(event.X,event.Y)))
                            if self.pane_layout.drop(target,point.X,point.Y,workspace.Width,workspace.Height):
                                arrange();self._save_workspace_preferences()
                        gesture.clear();sender.Capture=False
                    tab_btn.MouseDown+=tab_down;tab_btn.MouseMove+=tab_move;tab_btn.MouseUp+=tab_up
                    self._drag_handlers[key]=(tab_btn,tab_down,tab_move,tab_up)
                    self.labels[key+'_address']=ui.address
                    self.labels['tabbar']=header
                    workspace.Controls.Add(pane);self.panes[key]=pane
                    def got_focus(sender,event,target=key):select_tab(target,False)
                    view.GotFocus+=got_focus

                # Splitter Drag Events
                resizing = {}
                def divider_down(sender, event):
                    if event.Button == MouseButtons.Left:
                        resizing['active'] = True
                        sender.Capture = True

                def divider_move(sender, event):
                    if not resizing.get('active'):
                        return
                    point = workspace.PointToClient(sender.PointToScreen(Point(event.X, event.Y)))
                    if self.pane_layout.resize(point.X, workspace.Width):
                        arrange()

                def divider_up(sender, event):
                    resizing.clear()
                    sender.Capture = False
                    self._save_workspace_preferences()

                splitter.MouseDown += divider_down
                self._divider_handlers=(divider_down,divider_move,divider_up)
                splitter.MouseMove += divider_move
                splitter.MouseUp += divider_up
                divider_icon.MouseDown += divider_down
                divider_icon.MouseMove += divider_move
                divider_icon.MouseUp += divider_up

                # Assemble Panel
                panel.Controls.Add(workspace)
                panel.Controls.Add(self.layout_toolbar.host)
                panel.Controls.Add(lbl_alert)
                panel.Controls.Add(top)
                panel.Controls.Add(bottom)

                native.Controls.Add(panel)
                panel.BringToFront()
                self.panel = panel
                self.active_tab=self.pane_layout.active
                self.view = self.views[self.active_tab]
                arrange()

                # ==========================================================
                # FLOATING CAMERA PREVIEW (Draggable, Resizable, Eye-Toggle)
                # ==========================================================
                from windows.floating_camera_preview import FloatingCameraPreview
                self.camera_overlay = FloatingCameraPreview(
                    self.form,
                    bounds_control=self.workspace,
                    persist=not offline_validation,
                    demo=offline_validation,
                    on_visibility_changed=self._on_camera_visibility_changed
                )
                self.labels['preview']=self.camera_overlay.picture
                self.labels['battery']=self.chrome.nodes['battery']

                # Hook parent window movement and resize
                def on_native_move(*_):
                    if self.camera_overlay:
                        self.camera_overlay.on_parent_move_or_resize()

                def on_native_resize(*_):
                    if self.camera_overlay:
                        self.camera_overlay.on_parent_move_or_resize()
                    if hasattr(self, '_layout_proc'):
                        self._layout_proc()

                native.Move += on_native_move
                native.Resize += on_native_resize
                self._parent_move_handler = on_native_move
                self._parent_resize_handler = on_native_resize

                # Initialize WebView2 Browsers
                initialized_tabs = set()
                def initialized(key, sender, args):
                    try:
                        if generation != self.generation:
                            return
                        if not args.IsSuccess:
                            result.update(success=False, error=f"Could not initialize {key}: {args.InitializationException}")
                            done.set()
                            return
                        view = self.views[key]
                        settings = view.CoreWebView2.Settings
                        settings.IsStatusBarEnabled = False
                        settings.AreDefaultContextMenusEnabled = True
                        settings.IsZoomControlEnabled = True
                        settings.AreDevToolsEnabled = False

                        def filter_navigation(nav_sender, nav_args):
                            target_url = str(nav_args.Uri)
                            if not (offline_validation and target_url=='about:blank') and not allowed_url(target_url):
                                nav_args.Cancel = True
                                self.offline_navigation.append(target_url)
                                try:
                                    from windows.strict_violation_engine import get_strict_violation_engine
                                    sid = getattr(self, 'session_id', None) or 'active_session'
                                    get_strict_violation_engine().record_browser_tab_switch(sid, target_url)
                                except Exception:
                                    pass

                        view.CoreWebView2.NavigationStarting += filter_navigation
                        self.site_panes[key].attach_navigation(allowed_url)
                        def open_existing_popup(sender,args):
                            args.Handled=True
                            if offline_validation or generation!=self.generation:return
                            uri=str(args.Uri);target=tab_for_url(uri)
                            if target and self.views[target].CoreWebView2 is not None:
                                self.views[target].CoreWebView2.Navigate(uri)
                                select_tab(target)
                            else:
                                try:
                                    from windows.strict_violation_engine import get_strict_violation_engine
                                    sid = getattr(self, 'session_id', None) or 'active_session'
                                    get_strict_violation_engine().record_browser_tab_switch(sid, uri)
                                except Exception:
                                    pass
                        view.CoreWebView2.NewWindowRequested+=open_existing_popup

                        initialized_tabs.add(key)
                        if initialized_tabs == {'chatgpt', 'leetcode'}:
                            self.ready = True
                            if self.camera_overlay and not self.camera_overlay.is_user_hidden:self.camera_overlay.show()
                            if not offline_validation:
                                for name,current in self.views.items():current.CoreWebView2.Navigate(TAB_URLS[name])
                            result.update(success=True)
                            done.set()
                    except Exception as error:
                        result.update(success=False, error=str(error))
                    finally:
                        if result.get('success') or result.get('error'):
                            done.set()

                browser.CoreWebView2InitializationCompleted += lambda sender, args: initialized('chatgpt', sender, args)
                leetcode.CoreWebView2InitializationCompleted += lambda sender, args: initialized('leetcode', sender, args)
                browser.EnsureCoreWebView2Async(local.CoreWebView2.Environment)
                leetcode.EnsureCoreWebView2Async(local.CoreWebView2.Environment)
            except Exception as error:
                result.update(success=False, error=str(error))
                done.set()

        try:
            native.BeginInvoke(Action(build))
        except Exception as error:
            return {'success': False, 'error': str(error)}

        if not done.wait(15):
            self.close()
            return {'success': False, 'error': 'Embedded study browser did not initialize in time.'}
        if not result.get('success'):
            self.close()
        return result

    def _handle_camera_badge_click(self):
        """Allows restoring the floating camera preview by clicking the camera indicator in Card 1."""
        if self.camera_overlay:
            if self.camera_overlay.is_user_hidden or not self.camera_overlay.window.Visible:
                self.camera_overlay.show()
            else:
                self.camera_overlay.hide()

    def _save_workspace_preferences(self):
        if getattr(self,'_offline_validation',False):return
        from windows.strict_focus_preferences import save
        save(self.pane_layout)

    def _on_camera_visibility_changed(self,is_visible):
        self._last_encoded_preview=None
        if self.chrome:
            if hasattr(self.chrome,'update_camera_button'):self.chrome.update_camera_button()
        if self.view is not None:self.view.Focus()

    def update(self, status, hardware):
        if not self.ready:
            return
        from System import Action
        from System.Drawing import Color
        from System.Windows.Forms import Cursors
        generation=self.generation

        def apply():
            if not self.ready or generation!=self.generation:
                return

            self.demo_mode=status.get('mode')=='DEMO'
            if self.camera_overlay:
                initialized=hardware.get('camera')=='ACTIVE' and hardware.get('attention_monitoring')=='ACTIVE'
                self.camera_overlay.set_presentation_state(demo=getattr(self,'_offline_validation',False),
                    status_text='LIVE' if initialized and hardware.get('camera_preview',{}).get('current') else 'UNVERIFIED',
                    processing_text='StrictGuard active' if initialized else 'No verified monitoring service')
            remaining=status.get('adaptive_remaining_seconds') if status.get('state')=='ADAPTIVE_COMPLETION' else status.get('focus_remaining_seconds')
            seconds=int(max(0,remaining or 0)+.999)
            target=int(status.get('focus_target_seconds') or 2100)
            if status.get('state')=='ADAPTIVE_COMPLETION':target=2400
            timer_text='Study complete' if status.get('focus_completed_at') else f'{seconds//60:02d}:{seconds%60:02d} / {target//60:02d}:{target%60:02d}'
            self.chrome.update(status,hardware,timer_text,target,seconds)
            left_v=hardware.get('leftward_violations',0)
            max_v=hardware.get('max_leftward_violations',15)
            self.labels['controls'].Visible=not self.demo_mode
            if getattr(self,'_layout_bottom',None):self._layout_bottom()

            # 3. Dynamic Webcam Preview Frame Processing
            preview = hardware.get('camera_preview') or {}
            encoded = preview.get('jpeg') if preview.get('current') and not getattr(self,'_offline_validation',False) else None
            image = None
            changed=encoded!=getattr(self,'_last_encoded_preview',None)
            visible=self.camera_overlay and not self.camera_overlay.is_user_hidden
            if visible and changed and isinstance(encoded, str) and len(encoded) < 65536:
                try:
                    from System import Array, Byte
                    from System.IO import MemoryStream
                    from System.Drawing import Image, Bitmap
                    stream = MemoryStream(Array[Byte](base64.b64decode(encoded, validate=True)))
                    original = Image.FromStream(stream)
                    image = Bitmap(original)
                    original.Dispose()
                    stream.Dispose()
                except Exception:
                    pass

            if self.camera_overlay and changed and visible:
                self.camera_overlay.update_frame(image)
                self._last_encoded_preview=encoded
            else:
                if image is not None:
                    image.Dispose()

            # 4. Warnings & Alert Banner
            if 'alert' in self.labels:
                lbl_alert = self.labels['alert']
                alerts = hardware.get('active_phone_alerts') or status.get('active_phone_alerts') or []
                if alerts:
                    first_alert = alerts[0]
                    app_t = first_alert.get('app_name') or first_alert.get('package_name') or 'Unauthorized Application'
                    title = first_alert.get('title') or 'ANDROID USAGE DETECTED'
                    lbl_alert.Text = f"⚠️ STRICTGUARD: {title} ({app_t}) — Session restricted / timer paused."
                    lbl_alert.ForeColor = Color.FromArgb(255, 75, 75)
                    lbl_alert.Visible = True
                elif status.get('android_connection_lost'):
                    lbl_alert.Text = "⚠️ ANDROID CONNECTION LOST — Focus timer paused. Reconnect StrictRecords companion to resume."
                    lbl_alert.ForeColor = Color.FromArgb(255, 75, 75)
                    lbl_alert.Visible = True
                elif getattr(self,'_offline_validation',False):
                    lbl_alert.Text='OFFLINE DEMO  ·  No real monitoring or session is running.'
                    lbl_alert.ForeColor=Color.FromArgb(196,174,131);lbl_alert.Height=20;lbl_alert.Visible=True
                elif hardware.get('prohibited_audio_detected') or hardware.get('bluetooth_headphone_connected'):
                    dev_name = hardware.get('prohibited_audio_name') or 'Unauthorized Audio Device'
                    lbl_alert.Text = f'⚠️ Unauthorized Audio Device Detected: {dev_name} — Disconnect device to continue.'
                    lbl_alert.ForeColor = Color.FromArgb(255, 75, 75)
                    lbl_alert.Visible = True
                elif hardware.get('session_failed') or left_v > max_v:
                    lbl_alert.Text = f"Attention limit exceeded ({left_v} / {max_v}). Strict Session failed."
                    lbl_alert.ForeColor = Color.FromArgb(255, 70, 70)
                    lbl_alert.Visible = True
                elif left_v > 0:
                    lbl_alert.Text = f"🤖 STRICTGUARD ALERT (Violation {left_v} of {max_v}): Sustained left-side attention for >60s. Return focus to study screen ({max_v - left_v} warnings remaining)."
                    lbl_alert.ForeColor = Color.FromArgb(255, 190, 40)
                    lbl_alert.Visible = True
                elif hardware.get('face_tracking') != 'VERIFIED':
                    lbl_alert.Text = hardware.get('proctor_message') or 'Keep your face visible within camera view.'
                    lbl_alert.ForeColor = Color.FromArgb(255, 190, 40)
                    lbl_alert.Visible = True
                else:
                    lbl_alert.Visible = False
                self.chrome.tips.SetToolTip(lbl_alert,lbl_alert.Text)

            # 5. End Session button state
            reward_ok = status.get('reward_eligible', True) and (left_v <= max_v) and (hardware.get('headphone_violations', 0) < 2)
            if 'end' in self.labels:
                btn_end = self.labels['end']
                btn_end.Enabled = self.demo_mode or bool(
                    status.get('end_enabled')
                    and hardware.get('monitoring_ok')
                    and hardware.get('notification_filter_active')
                    and not hardware.get('prohibited_audio_detected')
                    and reward_ok
                )
                if btn_end.Enabled:
                    btn_end.Text = "EXIT DEMO & RESTORE DESKTOP" if self.demo_mode else "▢  End Session"
                    btn_end.ForeColor = Color.FromArgb(248, 113, 113)
                    btn_end.FlatAppearance.BorderColor = Color.FromArgb(239, 68, 68)
                else:
                    btn_end.Text = "▢  End Session (Locked)"
                    btn_end.ForeColor = Color.FromArgb(148, 163, 184)
                    btn_end.FlatAppearance.BorderColor = Color.FromArgb(71, 85, 105)

        self.form.BeginInvoke(Action(apply))

    def show(self):
        if self.ready:
            from System import Action
            generation=self.generation

            def show():
                if generation!=self.generation or not self.ready or self.panel is None or self.panel.IsDisposed:return
                self.panel.Visible = True
                self.panel.BringToFront()
                if self.camera_overlay and not self.camera_overlay.is_user_hidden:
                    self.camera_overlay.show()

            self.form.BeginInvoke(Action(show))
            return True
        return False

    def close(self):
        self.generation += 1
        if not self.form:
            return
        from System import Action

        def restore():
            self.ready = False
            self._save_workspace_preferences()
            if self.chrome:self.chrome.dispose();self.chrome=None
            if self.camera_overlay:
                self.camera_overlay.dispose()
                self.camera_overlay = None

            if self._parent_move_handler and self.form:
                try: self.form.Move -= self._parent_move_handler
                except Exception: pass
            if self._parent_resize_handler and self.form:
                try: self.form.Resize -= self._parent_resize_handler
                except Exception: pass

            for ui in self.site_panes.values():ui.dispose()
            self.site_panes={}
            if self.layout_toolbar:self.layout_toolbar.dispose();self.layout_toolbar=None
            if self.panel is not None:
                self.form.Controls.Remove(self.panel)
                self.panel.Dispose()
            self.panel = None
            self.view = None
            self.views = {}
            self.tab_buttons = {}
            self.panes = {}
            self.workspace = None
            self.splitter = None
            self._arrange = None
            self._drag_handlers = {}
            self._select_tab = None
            self.pane_layout = StudyPaneLayout()
            from windows.strict_focus_preferences import load
            self.pane_layout.restore_preferences(load())
            self._divider_handlers = None
            self.labels = {}
            self.offline_navigation = []
            self.demo_mode = False

            if self.old:
                self.form.FormBorderStyle = self.old[1]
                self.form.WindowState = self.old[0]
                self.form.Bounds = self.old[2]
                self.form.TopMost = self.old[3]
                self.old = None

        try:
            self.form.BeginInvoke(Action(restore))
        except Exception:
            self.ready = False
            self.panel = None
            self.view = None


# The user-supplied React frontend is the actual running session component.
# Keep the former native view available for isolated legacy component tests only.
from windows.strict_zip_frontend import ZipStrictFocus as EmbeddedStrictFocus
