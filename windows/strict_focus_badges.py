"""Native presentation only, with pure geometry/state helpers for headless QA."""
import math
from windows.strict_focus_chrome import FocusChrome, monitoring_details

COMPACT_KEYS = ('Webcam', 'Face', 'Head direction', 'Screen sharing',
                'Screen monitoring', 'Bluetooth audio', 'Wired audio', 'Speaker', 'Browser allowlist')
CAPTIONS = {'full': ('Camera', 'Face', 'Gaze', 'Share', 'Proctor', 'Bluetooth', 'Wired', 'Speaker', 'Browser'),
            'short': ('Cam', 'Face', 'Gaze', 'Share', 'Proctor', 'BT', 'Wired', 'Spkr', 'Web'),
            'icon': ('',) * len(COMPACT_KEYS)}
NAMES = dict(zip(COMPACT_KEYS, ('Camera', 'Face', 'Gaze', 'Screen sharing', 'Screen proctoring',
                              'Bluetooth audio', 'Wired audio', 'Speaker', 'Browser allowlist')))
GLYPHS = dict(zip(COMPACT_KEYS, ('camera', 'face', 'gaze', 'share', 'proctor', 'bluetooth', 'wired', 'speaker', 'browser')))


def _number(value, default=0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def unverified_source(status, hardware, offline=False):
    """An offline layout sample cannot establish live hardware evidence."""
    state = str(status.get('state') or '').upper()
    camera = str(hardware.get('camera') or '').upper()
    return bool(offline or state.startswith(('OFFLINE_', 'SIMULATED', 'SYNTHETIC'))
                or camera.startswith(('OFFLINE_', 'SIMULATED', 'SYNTHETIC'))
                or status.get('synthetic') is True or hardware.get('synthetic') is True)


def component_states(status, hardware, browser_ready=False, offline=False):
    def explicit(key, alternate=None):
        value = hardware.get(key, status.get(key))
        if value is None and alternate:
            value = hardware.get(alternate, status.get(alternate))
        return (('ACTIVE', 'Verified by the current report') if value is True else
                ('PENDING', 'Not active in the current report') if value is False else
                ('UNVERIFIED', 'No verified report available'))
    cam = hardware.get('camera'); face = hardware.get('face_tracking')
    attention = hardware.get('attention_monitoring')
    known = hardware.get('audio_detection_known') is True
    left = _number(hardware.get('leftward_seconds'))
    result = {
        'Webcam': ('ACTIVE', 'Selected camera is producing frames') if cam == 'ACTIVE' else
                  ('FAILED', 'Camera unavailable') if cam in ('ERROR', 'UNAVAILABLE', 'NO_FRAME', 'MODEL_MISSING') else
                  ('PENDING', 'Start or retry the camera test') if cam in ('STOPPED', 'NOT_STARTED') else
                  ('UNVERIFIED', 'No real camera report'),
        'Face': ('ACTIVE', 'Face verified') if face == 'VERIFIED' else
                ('WARNING', 'Keep your whole face visible') if hardware.get('face_present') is False else
                ('UNVERIFIED', 'Face tracking not verified'),
        'Head direction': ('WARNING', 'Leftward head position') if left > 0 else
                          ('ACTIVE', 'Forward head estimate; not eye-only gaze') if attention == 'ACTIVE' and face == 'VERIFIED' and hardware.get('proctor_calibrated') is True else
                          ('UNVERIFIED', 'Gaze/head tracking not calibrated'),
        'Screen sharing': explicit('screen_sharing_active', 'screen_capture_active'),
        'Screen monitoring': explicit('screen_proctoring_active', 'screen_monitoring_active'),
        'Bluetooth audio': ('WARNING', 'Disconnect connected Bluetooth audio') if hardware.get('bluetooth_headphone_connected') is True else
                           ('CLEAR', 'No connected Bluetooth audio') if known else
                           ('UNVERIFIED', 'Audio scan not verified'),
        'Wired audio': ('WARNING', 'Disconnect connected wired audio') if hardware.get('wired_headphone_connected') is True else
                       ('CLEAR', 'No wired headset detected') if known and hardware.get('wired_state_known') is True else
                       ('UNVERIFIED', 'Physical jack/driver not verified'),
        'Speaker': ('CLEAR', 'Speaker output is muted') if hardware.get('speaker_muted') is True else
                   ('WARNING', 'Speaker output is not muted') if hardware.get('speaker_muted') is False else
                   ('UNVERIFIED', 'No current mute report'),
        'Browser allowlist': ('ACTIVE', 'Approved-site navigation filtering is attached') if browser_ready else
                             ('UNVERIFIED', 'Browser restrictions not verified'),
        'Android synchronization': explicit('android_connected'),
        'iOS synchronization': explicit('ios_connected'),
    }
    for key in ('Android synchronization', 'iOS synchronization'):
        if result[key][0] == 'PENDING': result[key] = ('WARNING', 'Device synchronization is disconnected')
    if unverified_source(status, hardware, offline):
        return {key: ('UNVERIFIED', 'Offline layout sample; no live verification. ' + detail)
                for key, (_, detail) in result.items()}
    return result


def device_states(status, hardware, offline=False):
    """UI readiness alone says nothing about host/mobile connectivity."""
    states = component_states(status, hardware, offline=offline)
    host = next((hardware.get(key, status.get(key)) for key in ('windows_connected', 'pc_connected', 'host_connected')
                 if hardware.get(key, status.get(key)) is not None), None)
    windows = (('ACTIVE', 'Windows connection verified by the current report') if host is True else
               ('WARNING', 'Windows connection is disconnected') if host is False else
               ('UNVERIFIED', 'Windows interface open; host connectivity is not separately reported'))
    if unverified_source(status, hardware, offline):
        windows = ('UNVERIFIED', 'Offline layout sample; Windows connectivity is not verified')
    return {'pc': windows, 'android': states['Android synchronization'], 'ios': states['iOS synchronization']}


def battery_reading(status, hardware, offline=False):
    """Only a finite, in-range telemetry percentage may fill the battery."""
    if unverified_source(status, hardware, offline): return None, None
    raw = hardware.get('battery_percent')
    percent = _number(raw, -1) if not isinstance(raw, bool) else -1
    percent = round(percent) if 0 <= percent <= 100 else None
    charging = hardware.get('charging')
    return percent, charging if isinstance(charging, bool) else None


def countdown_candidates(timer_text, seconds):
    """Keep the full countdown first, then an explicit remaining value."""
    full = str(timer_text or '—')
    remaining = max(0, int(_number(seconds)))
    concise = (f'{remaining // 3600}:{remaining // 60 % 60:02d}:{remaining % 60:02d}'
               if remaining >= 3600 else f'{remaining // 60:02d}:{remaining % 60:02d}') + ' left'
    return tuple(dict.fromkeys([full] + ([concise] if '/' in full else []) + ['—']))


def fit_countdown(timer_text, seconds, available_width, measure):
    """Use measured native width; the full value stays in accessibility."""
    return next((text for text in countdown_candidates(timer_text, seconds)
                 if measure(text) <= max(0, available_width)), '')


def header_geometry(width):
    """Two compact rows; all nine monitor controls remain visible at 640px."""
    actual_width = max(0, int(width)); width = max(640, actual_width)
    narrow = width < 840
    bounds = {'title': (12, 3, 230 if narrow else 252, 24),
              'toggle': (width - 40, 2, 28, 27)}
    mode_left = bounds['title'][0] + bounds['title'][2] + 8
    bounds['mode'] = (mode_left, 5, 72 if narrow else 104, 21)
    battery_width = 82 if narrow else 98
    battery_left = bounds['toggle'][0] - 10 - battery_width
    timer_width = min(224 if narrow else 260, battery_left - 12 - mode_left - bounds['mode'][2] - 8)
    timer_left = battery_left - 12 - timer_width
    bounds.update(timer=(timer_left, 1, timer_width, 27), progress=(timer_left + 3, 28, timer_width - 6, 2),
                  battery_icon=(battery_left, 9, 26, 14), battery=(battery_left + 31, 2, battery_width - 31, 27))
    x = 12
    for key, cell in (('pc', 72), ('android', 72), ('ios', 42)):
        bounds[key] = (x, 33, cell, 23)
        x += cell + 4
    x += 8
    camera_width = 32 if width < 1000 else 118
    bounds['camera'] = (width - 12 - camera_width, 33, camera_width, 23)
    available = bounds['camera'][0] - 8 - x
    sizes = {'full': (80, 64, 68, 74, 86, 96, 78, 90, 90),
             'short': (60, 60, 62, 66, 80, 48, 66, 72, 74), 'icon': (26,) * len(COMPACT_KEYS)}
    mode = next((key for key in ('full', 'short', 'icon')
                 if sum(sizes[key]) + 4 * (len(COMPACT_KEYS) - 1) <= available), 'icon')
    for key, cell in zip(COMPACT_KEYS, sizes[mode]):
        bounds['compact_' + key] = (x, 33, cell, 23); x += cell + 4
    if actual_width < 640:
        bounds = {key: (round(x * actual_width / 640), y,
                        max(0, round((x + w) * actual_width / 640) - round(x * actual_width / 640)), h)
                  for key, (x, y, w, h) in bounds.items()}
    return {'bounds': bounds, 'caption_mode': mode}


class PolishedFocusChrome(FocusChrome):
    def __init__(self, focus):
        self._polished_ready = False
        super().__init__(focus)
        from System.Drawing import Color, Font, FontStyle, ContentAlignment
        from System.Windows.Forms import Label, PictureBox, Padding
        self._alignment = ContentAlignment; self._padding = Padding
        self._glyph_images = {}; self._timer_text = '—'; self._timer_seconds = 0
        self._states = component_states({}, {}); self._device_states = device_states({}, {})
        def badge(key, section=False):
            node = Label(); node.AutoSize = False; node.AutoEllipsis = False
            node.UseCompatibleTextRendering = False
            node.TextAlign = ContentAlignment.MiddleCenter
            node.Font = Font('Segoe UI', 8.5, FontStyle.Bold)
            node.BackColor = self.header.BackColor if section else Color.FromArgb(38, 40, 43)
            node.ForeColor = Color.FromArgb(151, 154, 159)
            self.header.Controls.Add(node); self.nodes[key] = node
            return node
        for key, title in (('session_group', 'SESSION'), ('device_group', 'Device Status'), ('monitor_group', 'Monitoring')):
            node = badge(key, True); node.Text = title; node.AccessibleName = title; node.Visible = False
        self.header.AccessibleName = 'Strict Session. Session, Device Status and Monitoring'
        for key in ('mode', 'pc', 'android', 'ios'): badge(key)
        self.nodes['mode'].Font = Font('Segoe UI', 8, FontStyle.Bold)
        self.compact_keys = list(COMPACT_KEYS)
        for key in self.compact_keys:
            badge('compact_' + key)
        self.details_keys = list(monitoring_details({}, {})) + ['Windows host', 'Android synchronization', 'iOS synchronization']
        for key in self.details_keys:
            if key in self.nodes: continue
            node = badge(key); self.header.Controls.Remove(node); self.nodes['details'].Controls.Add(node)
            node.TextAlign = ContentAlignment.MiddleLeft; node.AutoEllipsis = True
        self.nodes['devices'].Visible = False; self.nodes['monitor'].Visible = False
        icon = PictureBox(); self.header.Controls.Add(icon); self.nodes['battery_icon'] = icon
        self._battery_image = None; self._battery_value = None
        self.nodes['title'].Font = Font('Segoe UI', 9.5, FontStyle.Bold)
        self.nodes['title'].AutoEllipsis = False
        self.nodes['timer'].Font = Font('Consolas', 12.5, FontStyle.Bold)
        self.nodes['timer'].AutoEllipsis = False; self.nodes['timer'].TextAlign = ContentAlignment.MiddleRight
        self.nodes['camera'].Font = Font('Segoe UI', 8)
        self.nodes['camera'].ForeColor = Color.FromArgb(203, 205, 209)
        self.nodes['toggle'].ForeColor = Color.FromArgb(203, 205, 209)
        self._polished_ready = True
        self.update({'state': 'OFFLINE_UI_PREVIEW'} if getattr(focus, '_offline_validation', False) else {}, {}, '—', 1, 0)

    def _state_color(self, state):
        rgb = ((91, 201, 143) if state in ('ACTIVE', 'CLEAR') else (228, 177, 83) if state in ('WARNING', 'PENDING') else
               (237, 108, 111) if state == 'FAILED' else (149, 153, 160))
        return self._color.FromArgb(*rgb)

    def _glyph(self, kind, state='UNVERIFIED'):
        key = (kind, state)
        if key in self._glyph_images: return self._glyph_images[key]
        from System.Drawing import Bitmap, Graphics, Pen, SolidBrush
        from System.Drawing.Drawing2D import SmoothingMode
        image = Bitmap(4, 4) if kind == 'dot' else Bitmap(16, 16)
        graphics = Graphics.FromImage(image)
        graphics.SmoothingMode = SmoothingMode.AntiAlias
        pen = Pen(self._state_color(state), 1.25); brush = SolidBrush(self._state_color(state))
        if kind == 'dot':
            graphics.FillEllipse(brush, 0, 0, 3, 3)
        elif kind == 'camera':
            graphics.DrawRectangle(pen, 1, 4, 13, 10); graphics.DrawRectangle(pen, 4, 2, 5, 2); graphics.DrawEllipse(pen, 5, 6, 5, 5)
        elif kind == 'face':
            graphics.DrawEllipse(pen, 3, 1, 10, 13); graphics.FillEllipse(brush, 5, 5, 1.5, 1.5)
            graphics.FillEllipse(brush, 10, 5, 1.5, 1.5); graphics.DrawArc(pen, 5, 7, 6, 4, 0, 180)
        elif kind in ('gaze', 'eye'):
            graphics.DrawArc(pen, 1, 3, 14, 10, 180, 180); graphics.DrawArc(pen, 1, 3, 14, 10, 0, 180); graphics.DrawEllipse(pen, 6, 6, 4, 4)
        elif kind in ('share', 'proctor', 'pc'):
            graphics.DrawRectangle(pen, 1, 2, 13, 10); graphics.DrawLine(pen, 7, 12, 7, 15); graphics.DrawLine(pen, 4, 15, 11, 15)
            if kind == 'share':
                graphics.DrawLine(pen, 7, 10, 7, 4); graphics.DrawLine(pen, 7, 4, 4, 7); graphics.DrawLine(pen, 7, 4, 10, 7)
            elif kind == 'proctor':
                graphics.DrawLine(pen, 4, 7, 6, 9); graphics.DrawLine(pen, 6, 9, 11, 4)
        elif kind == 'bluetooth':
            for a, b, c, d in ((7, 1, 7, 15), (7, 1, 12, 5), (12, 5, 3, 12), (3, 4, 12, 11), (12, 11, 7, 15)):
                graphics.DrawLine(pen, a, b, c, d)
        elif kind == 'wired':
            graphics.DrawArc(pen, 2, 1, 11, 13, 180, 180); graphics.DrawRectangle(pen, 1, 7, 3, 6)
            graphics.DrawRectangle(pen, 11, 7, 3, 6); graphics.DrawLine(pen, 12, 13, 9, 15)
        elif kind == 'speaker':
            graphics.DrawRectangle(pen, 1, 6, 3, 5)
            for a, b, c, d in ((4, 6, 8, 3), (8, 3, 8, 14), (8, 14, 4, 11), (11, 6, 15, 11), (11, 11, 15, 6)):
                graphics.DrawLine(pen, a, b, c, d)
        elif kind == 'browser':
            graphics.DrawEllipse(pen, 1, 1, 14, 14); graphics.DrawEllipse(pen, 5, 1, 6, 14); graphics.DrawLine(pen, 1, 8, 15, 8)
        elif kind == 'android':
            graphics.DrawArc(pen, 2, 4, 12, 10, 180, 180); graphics.DrawRectangle(pen, 2, 9, 12, 5)
            graphics.DrawLine(pen, 4, 5, 2, 2); graphics.DrawLine(pen, 11, 5, 13, 2)
            graphics.FillEllipse(brush, 5, 6, 1.5, 1.5); graphics.FillEllipse(brush, 10, 6, 1.5, 1.5)
        elif kind == 'ios':
            graphics.DrawRectangle(pen, 4, 1, 8, 14); graphics.DrawLine(pen, 7, 12, 9, 12)
        graphics.Dispose(); pen.Dispose(); brush.Dispose()
        self._glyph_images[key] = image
        return image

    def _fit_timer(self):
        from System.Windows.Forms import TextRenderer, TextFormatFlags
        from System.Drawing import Size
        node = self.nodes['timer']; flags = TextFormatFlags.SingleLine
        measure = lambda text: TextRenderer.MeasureText(text, node.Font, Size(32767, 32767), flags).Width
        node.Text = fit_countdown(self._timer_text, self._timer_seconds, max(0, node.Width - 8), measure)
        node.AccessibleName = 'Session countdown: ' + str(self._timer_text)
        self.tips.SetToolTip(node, node.AccessibleName)

    def update_camera_button(self):
        if not self._polished_ready: return
        overlay = getattr(self.focus, 'camera_overlay', None); hidden = bool(overlay and overlay.is_user_hidden)
        node = self.nodes['camera']
        node.Text = ('Show preview' if hidden else 'Hide preview') if node.Width >= 90 else ''
        node.Image = self._glyph('eye')
        node.ImageAlign = self._alignment.MiddleLeft if node.Text else self._alignment.MiddleCenter
        from System.Windows.Forms import TextImageRelation
        node.TextImageRelation = TextImageRelation.ImageBeforeText
        node.TextAlign = self._alignment.MiddleCenter; node.Padding = self._padding(4, 0, 4, 0)
        node.AccessibleName = ('Show' if hidden else 'Hide') + ' camera preview; monitoring continues'
        self.tips.SetToolTip(node, node.AccessibleName)

    def arrange(self, *_):
        if not self._polished_ready: return super().arrange()
        from System.Drawing import Rectangle
        width = max(0, self.header.ClientSize.Width); layout = header_geometry(width)
        for key, bounds in layout['bounds'].items(): self.nodes[key].Bounds = Rectangle(*bounds)
        mode = getattr(self, '_mode_text', 'PRODUCTION')
        self.nodes['mode'].Text = 'DEMO' if mode == 'OFFLINE DEMO' else 'PROD' if width < 840 and mode == 'PRODUCTION' else mode
        for key, caption in zip(COMPACT_KEYS, CAPTIONS[layout['caption_mode']]):
            node = self.nodes['compact_' + key]; node.Visible = True; node.Text = caption
            node.ImageAlign = self._alignment.MiddleLeft if caption else self._alignment.MiddleCenter
            node.TextAlign = self._alignment.MiddleRight
            node.Padding = self._padding(3, 0, 3, 0) if caption else self._padding(0)
        self._fit_timer(); self.update_camera_button()
        details = self.nodes['details']; columns = 5 if width >= 1100 else 4 if width >= 800 else 3 if width >= 550 else 2
        rows = (len(self.details_keys) + columns - 1) // columns
        row_h = 22
        details.Bounds = Rectangle(10, 60, max(0, width - 20), rows * row_h)
        for index, key in enumerate(self.details_keys):
            cell = max(1, details.Width // columns)
            self.nodes[key].Bounds = Rectangle(index % columns * cell + 4, index // columns * row_h, max(0, cell - 8), 20)
            self.nodes[key].Font = Font('Segoe UI', 8.5)
        self.EXPANDED_HEIGHT = min(108, 62 + rows * row_h)
        if self.expanded and self.header.Height != self.EXPANDED_HEIGHT: self.header.Height = self.EXPANDED_HEIGHT

    def _battery(self, percent, charging):
        value = (percent, charging)
        if value == self._battery_value and self._battery_image is not None: return
        self._battery_value = value
        from System.Drawing import Bitmap, Graphics, Pen, SolidBrush, Point
        image = Bitmap(26, 14); graphics = Graphics.FromImage(image)
        color = self._state_color('CLEAR' if percent is not None and percent > 20 else 'WARNING' if percent is not None else 'UNVERIFIED')
        pen = Pen(color, 1); brush = SolidBrush(color)
        graphics.DrawRectangle(pen, 0, 1, 22, 11); graphics.FillRectangle(brush, 23, 4, 3, 5)
        if percent is not None: graphics.FillRectangle(brush, 2, 3, round(19 * percent / 100), 7)
        if charging is True:
            from System import Array
            bolt = SolidBrush(self._color.FromArgb(242, 243, 245))
            graphics.FillPolygon(bolt, Array[Point]([Point(13, 1), Point(7, 8), Point(11, 8), Point(9, 13), Point(16, 5), Point(12, 5)])); bolt.Dispose()
        graphics.Dispose(); pen.Dispose(); brush.Dispose()
        old = self._battery_image; self._battery_image = image; self.nodes['battery_icon'].Image = image
        if old: old.Dispose()

    def update(self, status, hardware, timer_text, target, seconds):
        super().update(status, hardware, timer_text, target, seconds)
        offline = getattr(self.focus, '_offline_validation', False); sample = unverified_source(status, hardware, offline)
        demo = status.get('mode') == 'DEMO'
        self.nodes['title'].Text = 'StrictRecords · Strict Session'; self.nodes['title'].AccessibleName = self.nodes['title'].Text
        self.tips.SetToolTip(self.nodes['title'], self.nodes['title'].Text)
        self._mode_text = 'OFFLINE DEMO' if sample else 'DEMO' if demo else 'PRODUCTION'
        self.nodes['mode'].AccessibleName = 'Session mode: ' + self._mode_text
        self.tips.SetToolTip(self.nodes['mode'], self.nodes['mode'].AccessibleName)
        self._timer_text, self._timer_seconds = timer_text, seconds
        percent, charging = battery_reading(status, hardware, offline); self._battery(percent, charging)
        self.nodes['battery'].Text = '—' if percent is None else str(percent) + '%'
        battery_detail = ('Battery unverified' if percent is None else 'Battery ' + str(percent) + '%') + (' · Charging' if charging is True else ' · On battery' if charging is False else ' · Charging state unverified')
        for key in ('battery', 'battery_icon'):
            self.nodes[key].AccessibleName = battery_detail; self.tips.SetToolTip(self.nodes[key], battery_detail)
        self._states = component_states(status, hardware, self.focus.ready and not sample, offline)
        self._device_states = device_states(status, hardware, offline)
        raw = monitoring_details(status, hardware, self.focus.ready and not sample)
        for key, value in raw.items():
            state = (self._states[key][0] if key in self._states else
                     'ACTIVE' if value in ('ACTIVE', 'VERIFIED', 'CLEAR', 'MUTED', 'FORWARD') else
                     'FAILED' if value in ('ERROR', 'FAILED', 'UNAVAILABLE', 'NO_FRAME', 'MODEL_MISSING') else
                     'WARNING' if value in ('OFF', 'STOPPED', 'NOT_STARTED', 'LEFT', 'CONNECTED', 'NOT MUTED') else 'UNVERIFIED')
            node = self.nodes[key]
            node.Text = NAMES.get(key, key) + ': ' + str(value) + (' · offline sample' if sample else '')
            node.ForeColor = self._state_color('UNVERIFIED' if sample else state)
            node.AccessibleName = node.Text + ('. Unverified offline sample' if sample else '')
            if key in self._states: node.AccessibleName += '. ' + self._states[key][1]
            self.tips.SetToolTip(node, node.AccessibleName)
        for key, (state, detail) in self._states.items():
            title = NAMES.get(key, key); accessible = title + ': ' + state + '. ' + detail
            if key in self.compact_keys:
                node = self.nodes['compact_' + key]; node.ForeColor = self._state_color(state)
                node.Image = self._glyph(GLYPHS[key], state); node.AccessibleName = accessible; self.tips.SetToolTip(node, accessible)
            if key in self.nodes and key not in raw:
                node = self.nodes[key]
                actual = raw.get(key, 'CONNECTED' if state == 'ACTIVE' else 'DISCONNECTED' if state == 'WARNING' else 'UNVERIFIED')
                node.Text = title + ': ' + str(actual) + (' · offline sample' if sample else '')
                node.ForeColor = self._state_color(state); node.AccessibleName = node.Text + '. ' + detail
                self.tips.SetToolTip(node, node.AccessibleName)
        for key, title in (('pc', 'Windows'), ('android', 'Android'), ('ios', 'iOS')):
            state, detail = self._device_states[key]; node = self.nodes[key]
            node.Text = 'PC' if key=='pc' else title; node.Image = self._glyph('dot', state); node.ImageAlign = self._alignment.MiddleLeft
            node.TextAlign = self._alignment.MiddleRight; node.Padding = self._padding(2, 0, 2, 0)
            node.ForeColor = self._state_color(state); node.AccessibleName = title + ': ' + state + '. ' + detail
            self.tips.SetToolTip(node, node.AccessibleName)
        state, detail = self._device_states['pc']; node = self.nodes['Windows host']
        node.Text = 'Windows host: ' + state; node.ForeColor = self._state_color(state)
        node.AccessibleName = node.Text + '. ' + detail; self.tips.SetToolTip(node, node.AccessibleName)
        self.arrange()

    def dispose(self):
        if self._battery_image:
            self.nodes['battery_icon'].Image = None; self._battery_image.Dispose(); self._battery_image = None
        for node in self.nodes.values():
            if getattr(node, 'Image', None) in self._glyph_images.values(): node.Image = None
        for image in self._glyph_images.values(): image.Dispose()
        self._glyph_images.clear()
        super().dispose()
