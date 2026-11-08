"""Native floating camera presentation; capture and monitoring stay with the host.

The complete component defaults to 240 x 135. Its header overlays the 16:9
camera canvas, so controls do not add height to the small preview.
"""

import json
import os
import ctypes
from pathlib import Path

from System.Drawing import Color, Font, FontStyle, Point, Size, ContentAlignment
from System.Windows.Forms import (
    Form, FormBorderStyle, Panel, PictureBox, Label, Button,
    PictureBoxSizeMode, FlatStyle, MouseButtons, Cursors, FormStartPosition,
    ContextMenuStrip, ToolStripMenuItem, ToolTip,
)

from windows.camera_preview_geometry import (
    DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH, Rect, clamp_rect,
    relative_geometry, restore_rect, resize_rect,
)


def _geometry_file():
    try:
        from core.database import _default_database_path
        parent = Path(_default_database_path()).parent
    except Exception:
        parent = Path(os.environ.get('LOCALAPPDATA', '.')) / 'StrictRecords'
    parent.mkdir(parents=True, exist_ok=True)
    return parent / 'strict_camera_preview_geometry.json'


class FloatingCameraPreview:
    DEFAULT_WIDTH = DEFAULT_WIDTH
    DEFAULT_HEIGHT = 135
    MIN_WIDTH = MIN_WIDTH
    MAX_WIDTH = MAX_WIDTH
    HEADER_HEIGHT = 24

    def __init__(self, parent_form, on_visibility_changed=None, bounds_control=None,
                 persist=True, demo=False):
        self.parent_form = parent_form
        self.persist = persist
        self.bounds_control = bounds_control or parent_form
        self.on_visibility_changed = on_visibility_changed
        self.is_user_hidden = False
        self._dragging = False
        self._resizing = False
        self._resize_direction = 'se'
        self._relative_geometry = None
        self._demo = bool(demo)
        self._status_text = None
        self._processing_text = None
        self.current_frame = None
        self._tooltip = ToolTip()
        self._native_api = ctypes.WinDLL('user32', use_last_error=True)
        self._native_api.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
        self._native_api.GetWindowLongW.restype = ctypes.c_long
        self._native_api.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
        self._native_api.SetWindowLongW.restype = ctypes.c_long
        self._native_api.GetForegroundWindow.restype = ctypes.c_void_p
        self._native_api.SetWindowPos.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        )
        self._native_api.SetWindowPos.restype = ctypes.c_int

        window = Form()
        window.FormBorderStyle = getattr(FormBorderStyle, 'None')
        window.ShowInTaskbar = False
        window.Owner = parent_form
        window.StartPosition = FormStartPosition.Manual
        window.BackColor = Color.FromArgb(24, 24, 26)
        window.Size = Size(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        window.AccessibleName = 'Floating camera preview'
        self.window = window
        window.HandleCreated += self._configure_owned_window

        picture = PictureBox()
        picture.SizeMode = PictureBoxSizeMode.Zoom
        picture.BackColor = Color.FromArgb(24, 24, 26)
        picture.AccessibleName = 'Camera preview image'
        window.Controls.Add(picture)
        self.picture = picture

        # A graphite overlay header keeps the whole component at 16:9.
        header = Panel()
        header.BackColor = Color.FromArgb(38, 38, 41)
        header.Cursor = Cursors.SizeAll
        picture.Controls.Add(header)
        self.header = header
        self._tooltip.SetToolTip(header, 'Drag preview. Right-click to move to a corner.')

        badge = Label()
        badge.ForeColor = Color.FromArgb(208, 208, 212)
        badge.BackColor = Color.Transparent
        badge.Font = Font('Segoe UI', 7, FontStyle.Bold)
        badge.UseCompatibleTextRendering = False
        badge.AutoEllipsis = True
        badge.Left, badge.Top, badge.Width, badge.Height = 8, 5, 76, 16
        badge.Cursor = Cursors.SizeAll
        header.Controls.Add(badge)
        self.badge_live = badge  # Retained for compatibility; never infers LIVE.

        menu = ContextMenuStrip()
        for corner in ('Top left', 'Top right', 'Bottom left', 'Bottom right'):
            item = ToolStripMenuItem(corner)
            def anchor(*_, target=corner):
                self.move_to_corner(target)
            item.Click += anchor
            menu.Items.Add(item)
        header.ContextMenuStrip = menu
        self.corner_menu = menu

        def button(text, description, click):
            control = Button()
            control.Text = text
            control.ForeColor = Color.FromArgb(208, 208, 212)
            control.BackColor = Color.FromArgb(38, 38, 41)
            control.FlatStyle = FlatStyle.Flat
            control.FlatAppearance.BorderSize = 0
            control.FlatAppearance.MouseOverBackColor = Color.FromArgb(64, 64, 68)
            control.Font = Font('Segoe UI', 8)
            control.UseCompatibleTextRendering = False
            control.Width, control.Height, control.Top = 22, 20, 2
            control.Cursor = Cursors.Hand
            control.AccessibleName = description
            control.Click += click
            header.Controls.Add(control)
            self._tooltip.SetToolTip(control, description)
            return control

        self.btn_close = button('×', 'Hide camera preview', lambda *_: self.hide())
        self.btn_size = button('↗', 'Cycle preview size', lambda *_: self.cycle_size())
        self.btn_eye = button('👁', 'Hide camera preview only', lambda *_: self.hide())
        self.btn_eye.Font = Font('Segoe UI Symbol', 9)

        unavailable = Label()
        unavailable.ForeColor = Color.FromArgb(180, 180, 186)
        unavailable.BackColor = picture.BackColor
        unavailable.Font = Font('Segoe UI', 8, FontStyle.Regular)
        unavailable.UseCompatibleTextRendering = False
        unavailable.TextAlign = ContentAlignment.MiddleCenter
        picture.Controls.Add(unavailable)
        self.unavailable_label = unavailable

        processing = Label()
        processing.ForeColor = Color.FromArgb(190, 190, 196)
        processing.BackColor = Color.FromArgb(32, 32, 35)
        processing.Font = Font('Segoe UI', 7)
        processing.UseCompatibleTextRendering = False
        processing.AutoEllipsis = True
        processing.TextAlign = ContentAlignment.MiddleCenter
        picture.Controls.Add(processing)
        self.processing_label = processing

        # Native controls receive real pointer capture on every edge and corner.
        self.resize_handles = {}
        cursor_for = {
            'n': Cursors.SizeNS, 's': Cursors.SizeNS,
            'e': Cursors.SizeWE, 'w': Cursors.SizeWE,
            'nw': Cursors.SizeNWSE, 'se': Cursors.SizeNWSE,
            'ne': Cursors.SizeNESW, 'sw': Cursors.SizeNESW,
        }
        for direction in ('n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'):
            handle = Label()
            handle.Tag = direction
            handle.BackColor = Color.FromArgb(57, 57, 61)
            handle.Cursor = cursor_for[direction]
            handle.AccessibleName = 'Resize camera preview ' + direction
            handle.MouseDown += self._on_resize_down
            handle.MouseMove += self._on_resize_move
            handle.MouseUp += self._on_resize_up
            picture.Controls.Add(handle)
            self.resize_handles[direction] = handle
        self.grip = self.resize_handles['se']

        for control in (header, badge):
            control.MouseDown += self._on_drag_down
            control.MouseMove += self._on_drag_move
            control.MouseUp += self._on_drag_up
        window.Resize += self._layout
        self._layout()
        self._refresh_presentation()
        self.load_geometry()
        self._host_geometry_handler = lambda *_: self.on_parent_move_or_resize()
        parent_form.LocationChanged += self._host_geometry_handler
        parent_form.ClientSizeChanged += self._host_geometry_handler
        self._tracks_bounds_separately = self.bounds_control != parent_form
        if self._tracks_bounds_separately:
            self.bounds_control.LocationChanged += self._host_geometry_handler
            self.bounds_control.SizeChanged += self._host_geometry_handler
        # WinForms finishes applying its own extended styles after HandleCreated.
        self._configure_owned_window()

    def _layout(self, *_):
        width, height = self.window.ClientSize.Width, self.window.ClientSize.Height
        self.picture.Location = Point(0, 0)
        self.picture.Size = Size(width, height)
        self.header.Location = Point(0, 0)
        self.header.Size = Size(width, self.HEADER_HEIGHT)
        for index, control in enumerate((self.btn_close, self.btn_size, self.btn_eye)):
            control.Left = width - 27 - index * 22
        self.unavailable_label.Location = Point(12, self.HEADER_HEIGHT + 4)
        self.unavailable_label.Size = Size(max(1, width - 24), max(1, height - self.HEADER_HEIGHT - 14))
        self.processing_label.Location = Point(5, max(self.HEADER_HEIGHT, height - 22))
        self.processing_label.Size = Size(max(1, width - 10), 17)
        edge, corner = 5, 10
        rectangles = {
            'n': (corner, 0, max(1, width - 2 * corner), edge),
            's': (corner, height - edge, max(1, width - 2 * corner), edge),
            'w': (0, corner, edge, max(1, height - 2 * corner)),
            'e': (width - edge, corner, edge, max(1, height - 2 * corner)),
            'nw': (0, 0, corner, corner), 'ne': (width - corner, 0, corner, corner),
            'sw': (0, height - corner, corner, corner),
            'se': (width - corner, height - corner, corner, corner),
        }
        self.unavailable_label.BringToFront()
        self.processing_label.BringToFront()
        self.header.BringToFront()
        for direction, handle in self.resize_handles.items():
            x, y, w, h = rectangles[direction]
            handle.Location, handle.Size = Point(x, y), Size(w, h)
            handle.BringToFront()

    def _workspace(self):
        # A child can extend beyond its visible owner when a layout is narrow.
        # Clamp to the actual on-screen intersection, not its requested bounds.
        # Creating a HWND can change initial DPI and non-client metrics. Finish
        # that before sampling either rectangle, including before the first show.
        owner_handle = self.parent_form.Handle
        workspace_handle = self.bounds_control.Handle
        workspace = self.bounds_control.RectangleToScreen(self.bounds_control.ClientRectangle)
        client = self.parent_form.RectangleToScreen(self.parent_form.ClientRectangle)
        left, top = max(workspace.Left, client.Left), max(workspace.Top, client.Top)
        right, bottom = min(workspace.Right, client.Right), min(workspace.Bottom, client.Bottom)
        if right <= left or bottom <= top:
            # A transient, not-yet-laid-out workspace must not strand the HWND.
            left, top, right, bottom = client.Left, client.Top, client.Right, client.Bottom
        return Rect(left, top, max(1, right - left), max(1, bottom - top))

    def _configure_owned_window(self, *_):
        # This is an owned tool window, never a global topmost window. Its own
        # native controls can be used without taking focus from the study pane.
        handle = self.window.Handle.ToInt64()
        style = self._native_api.GetWindowLongW(handle, -20)
        self._native_api.SetWindowLongW(handle, -20, style | 0x08000000 | 0x00000080)

    def _keep_above_owner(self):
        if not self.window.Visible or not self.parent_form.IsHandleCreated:
            return
        # Raise only while our owner is foreground; other apps keep their place.
        if self._native_api.GetForegroundWindow() == self.parent_form.Handle.ToInt64():
            self._native_api.SetWindowPos(
                self.window.Handle.ToInt64(), 0, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0200,  # no size/move/activate/owner-order
            )

    def _rect(self):
        return Rect(self.window.Left, self.window.Top, self.window.Width, self.window.Height)

    def _apply_rect(self, rect, remember=True):
        self.window.Size = Size(rect.width, rect.height)
        self.window.Location = Point(rect.x, rect.y)
        if remember:
            self._relative_geometry = relative_geometry(rect, self._workspace())

    def _on_drag_down(self, sender, event):
        if event.Button == MouseButtons.Left:
            self._dragging = True
            self._drag_start_cursor = sender.PointToScreen(Point(event.X, event.Y))
            self._drag_start_location = self.window.Location
            sender.Capture = True

    def _on_drag_move(self, sender, event):
        if self._dragging:
            cursor = sender.PointToScreen(Point(event.X, event.Y))
            self.set_clamped_location(
                self._drag_start_location.X + cursor.X - self._drag_start_cursor.X,
                self._drag_start_location.Y + cursor.Y - self._drag_start_cursor.Y,
            )

    def _on_drag_up(self, sender, event):
        if self._dragging:
            self._dragging = False
            sender.Capture = False
            self.save_geometry()

    def _on_resize_down(self, sender, event):
        if event.Button == MouseButtons.Left:
            self._resizing = True
            self._resize_direction = str(sender.Tag or 'se')
            self._resize_start_cursor = sender.PointToScreen(Point(event.X, event.Y))
            self._resize_start_rect = self._rect()
            sender.Capture = True

    def _on_resize_move(self, sender, event):
        if self._resizing:
            cursor = sender.PointToScreen(Point(event.X, event.Y))
            rect = resize_rect(
                self._resize_start_rect, self._resize_direction,
                cursor.X - self._resize_start_cursor.X,
                cursor.Y - self._resize_start_cursor.Y, self._workspace(),
            )
            self._apply_rect(rect)

    def _on_resize_up(self, sender, event):
        if self._resizing:
            self._resizing = False
            sender.Capture = False
            self.save_geometry()

    def cycle_size(self):
        width = self.window.Width
        target = 240 if width < 210 else 320 if width < 280 else 180
        self._apply_rect(clamp_rect(Rect(self.window.Left, self.window.Top, target, 1), self._workspace()))
        self.save_geometry()

    def set_clamped_location(self, target_x, target_y):
        rect = Rect(target_x, target_y, self.window.Width, self.window.Height)
        self._apply_rect(clamp_rect(rect, self._workspace()))

    def clamp_to_parent(self):
        self.set_clamped_location(self.window.Left, self.window.Top)

    def on_parent_move_or_resize(self):
        """Keep the preview at its relative location when the workspace changes."""
        if not self.window.IsDisposed and not (self._dragging or self._resizing):
            self._apply_rect(restore_rect(self._relative_geometry or {}, self._workspace()), remember=False)
            self._keep_above_owner()

    def move_to_corner(self, corner):
        area = self._workspace()
        self._apply_rect(restore_rect({
            'width': self.window.Width,
            'relative_left': 0 if corner.endswith('left') else 1,
            'relative_top': 0 if corner.startswith('Top') else 1,
        }, area))
        self.save_geometry()

    def show(self):
        """Restore presentation only; the host owns capture and monitoring."""
        changed = self.is_user_hidden or not self.window.Visible
        self.is_user_hidden = False
        self.on_parent_move_or_resize()
        if not self.window.Visible:
            self._configure_owned_window()
            self.window.Visible = True
            self._keep_above_owner()
        if changed:
            self.save_geometry()
        if changed and callable(self.on_visibility_changed):
            try:
                self.on_visibility_changed(True)
            except Exception:
                pass

    def hide(self):
        """Hide presentation only; no capture or monitoring lifecycle calls."""
        self.is_user_hidden = True
        self.window.Visible = False
        self.update_frame(None)
        self.save_geometry()
        if callable(self.on_visibility_changed):
            try:
                self.on_visibility_changed(False)
            except Exception:
                pass

    def toggle(self):
        self.hide() if self.window.Visible else self.show()

    def set_presentation_state(self, demo=None, status_text=None, processing_text=None):
        """Display host-supplied facts. This never starts/stops processing."""
        if demo is not None:
            self._demo = bool(demo)
        self._status_text = str(status_text) if status_text else None
        self._processing_text = str(processing_text) if processing_text else None
        if self._demo and self.current_frame is not None:
            self.update_frame(None)
        else:
            self._refresh_presentation()

    def _refresh_presentation(self):
        live=not self._demo and self._status_text=='LIVE' and self.current_frame is not None
        self.badge_live.Text = 'DEMO' if self._demo else 'LIVE' if live else ('CAMERA' if self._status_text=='LIVE' else self._status_text or 'CAMERA')
        self.badge_live.ForeColor=Color.FromArgb(91,201,143) if live else Color.FromArgb(180,184,190)
        self._tooltip.SetToolTip(self.badge_live, self._status_text or 'Camera preview')
        self.unavailable_label.Text = (
            'CAMERA PREVIEW UNAVAILABLE – DEMO' if self._demo else 'Camera preview unavailable'
        )
        self.unavailable_label.Visible = self._demo or self.current_frame is None
        self.processing_label.Text = self._processing_text or ''
        self.processing_label.Visible = bool(self._processing_text) and not self._demo

    def update_frame(self, bitmap):
        """Present an actual host frame; demo mode never displays sample imagery."""
        if self._demo and bitmap is not None:
            try:
                bitmap.Dispose()
            except Exception:
                pass
            bitmap = None
        previous = self.current_frame
        self.current_frame = bitmap
        if not self.window.IsDisposed:
            self.picture.Image = bitmap
            self._refresh_presentation()
        if previous is not None and previous != bitmap:
            try:
                previous.Dispose()
            except Exception:
                pass

    def load_geometry(self):
        data = {}
        if self.persist:
            try:
                loaded = json.loads(_geometry_file().read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError, TypeError):
                pass
        self.is_user_hidden = bool(data.get('user_hidden', False))
        # Bottom-right avoids the native pane toolbars on first use.
        self._apply_rect(restore_rect(data, self._workspace()))
        self.window.Visible = False

    def save_geometry(self):
        if not self.persist:
            return
        try:
            rect = self._rect()
            data = {
                'version': 2, 'left': rect.x, 'top': rect.y,
                'width': rect.width, 'height': rect.height,
                'user_hidden': self.is_user_hidden,
                **relative_geometry(rect, self._workspace()),
            }
            _geometry_file().write_text(json.dumps(data, indent=2), encoding='utf-8')
        except (OSError, ValueError, TypeError):
            pass

    def dispose(self):
        self.parent_form.LocationChanged -= self._host_geometry_handler
        self.parent_form.ClientSizeChanged -= self._host_geometry_handler
        if self._tracks_bounds_separately:
            self.bounds_control.LocationChanged -= self._host_geometry_handler
            self.bounds_control.SizeChanged -= self._host_geometry_handler
        self.save_geometry()
        self.update_frame(None)
        self._tooltip.Dispose()
        self.corner_menu.Dispose()
        if not self.window.IsDisposed:
            self.window.Close()
            self.window.Dispose()
