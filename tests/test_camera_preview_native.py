"""Isolated native component checks: no session, camera, WebView, or services."""

import json
import ctypes
from ctypes import wintypes

import pytest

clr = pytest.importorskip('clr')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Drawing import Bitmap, Point, Size
from System.Windows.Forms import Form, Panel, MouseButtons, MouseEventArgs

import windows.floating_camera_preview as preview_module
from windows.floating_camera_preview import FloatingCameraPreview


@pytest.fixture
def component(tmp_path, monkeypatch):
    monkeypatch.setattr(preview_module, '_geometry_file', lambda: tmp_path / 'camera-ui.json')
    parent = Form()
    parent.Size = Size(1200, 800)
    events = []
    preview = FloatingCameraPreview(parent, on_visibility_changed=events.append)
    yield preview, parent, events
    preview.dispose()
    parent.Dispose()


def test_actual_native_size_and_eight_resize_handles(component):
    preview, parent, _ = component
    assert preview.window.Owner == parent
    assert (preview.window.Width, preview.window.Height) == (240, 135)
    assert (preview.picture.Width, preview.picture.Height) == (240, 135)
    assert set(preview.resize_handles) == {'n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'}
    for direction, handle in preview.resize_handles.items():
        assert handle.Parent == preview.picture
        assert str(handle.Tag) == direction
        assert handle.Width > 0 and handle.Height > 0
        assert handle.AccessibleName.startswith('Resize camera preview')
    for width, height in ((320, 180), (180, 101), (240, 135)):
        preview.cycle_size()
        assert (preview.window.Width, preview.window.Height) == (width, height)


def test_demo_is_explicit_and_rejects_sample_image(component):
    preview, _, _ = component
    preview.set_presentation_state(demo=True)
    preview.update_frame(Bitmap(16, 9))
    assert preview.current_frame is None
    assert preview.picture.Image is None
    assert preview.unavailable_label.Text == 'CAMERA PREVIEW UNAVAILABLE – DEMO'
    assert preview.badge_live.Text == 'DEMO'


def test_only_host_supplied_processing_state_is_presented(component):
    preview, _, _ = component
    preview.update_frame(Bitmap(16, 9))
    assert preview.badge_live.Text == 'CAMERA'
    assert preview.processing_label.Text == ''
    preview.set_presentation_state(status_text='READY', processing_text='Capture active')
    assert preview.badge_live.Text == 'READY'
    assert preview.processing_label.Text == 'Capture active'
    preview.hide()
    assert preview.is_user_hidden
    assert preview.current_frame is None
    # A hide cannot synthesize a capture lifecycle transition.
    assert preview.processing_label.Text == 'Capture active'


def test_hide_and_restore_geometry_preserve_host_callback_contract(component):
    preview, parent, events = component
    preview.set_clamped_location(parent.Left + 160, parent.Top + 150)
    preview.hide()
    assert events == [False]
    data = json.loads(preview_module._geometry_file().read_text(encoding='utf-8'))
    assert data['version'] == 2
    assert data['user_hidden'] is True
    assert 0 <= data['relative_left'] <= 1
    assert 0 <= data['relative_top'] <= 1
    assert 0 < data['relative_width'] <= 1
    other = FloatingCameraPreview(parent)
    try:
        assert other.is_user_hidden is True
        assert (other.window.Width, other.window.Height) == (preview.window.Width, preview.window.Height)
        assert other.window.Location == preview.window.Location
    finally:
        other.dispose()


def test_native_component_tracks_workspace_move_without_services(component):
    preview, parent, _ = component
    original = preview.window.Location
    parent.Location = Point(parent.Left + 70, parent.Top + 45)
    preview.on_parent_move_or_resize()
    assert preview.window.Left == original.X + 70
    assert preview.window.Top == original.Y + 45
    assert preview.window.Width == 240


def _pointer_event(sender, screen_point):
    point = sender.PointToClient(screen_point)
    return MouseEventArgs(MouseButtons.Left, 1, point.X, point.Y, 0)


def test_actual_native_drag_handler_moves_and_clamps_without_os_input(component):
    preview, _, _ = component
    area = preview._workspace()
    preview.set_clamped_location(area.x + 250, area.y + 200)
    start = preview.window.Location
    header = preview.header
    pointer = header.PointToScreen(Point(30, 12))
    preview._on_drag_down(header, _pointer_event(header, pointer))
    try:
        preview._on_drag_move(header, _pointer_event(header, Point(pointer.X + 57, pointer.Y + 31)))
        assert preview.window.Left == start.X + 57
        assert preview.window.Top == start.Y + 31
        preview._on_drag_move(header, _pointer_event(header, Point(area.x - 5000, area.y - 5000)))
        assert preview.window.Left == area.x + 12
        assert preview.window.Top == area.y + 12
    finally:
        preview._on_drag_up(header, _pointer_event(header, pointer))
    assert not preview._dragging
    assert not header.Capture


@pytest.mark.parametrize('direction,dx,dy', [
    ('n', 0, -36), ('s', 0, 36), ('e', 64, 0), ('w', -64, 0),
    ('nw', -64, -36), ('ne', 64, -36), ('sw', -64, 36), ('se', 64, 36),
])
def test_all_native_resize_handler_paths_without_os_input(component, direction, dx, dy):
    preview, _, _ = component
    area = preview._workspace()
    preview.set_clamped_location(area.x + 250, area.y + 200)
    start = preview.window.Bounds
    handle = preview.resize_handles[direction]
    pointer = handle.PointToScreen(Point(handle.Width // 2, handle.Height // 2))
    preview._on_resize_down(handle, _pointer_event(handle, pointer))
    try:
        assert preview._resize_direction == direction
        moved = Point(pointer.X + dx, pointer.Y + dy)
        preview._on_resize_move(handle, _pointer_event(handle, moved))
        assert (preview.window.Width, preview.window.Height) == (304, 171)
        if 'w' in direction:
            assert preview.window.Right == start.Right
        elif 'e' in direction:
            assert preview.window.Left == start.Left
        else:
            assert preview.window.Left + preview.window.Width / 2 == start.Left + start.Width / 2
        if 'n' in direction:
            assert preview.window.Bottom == start.Bottom
        elif 's' in direction:
            assert preview.window.Top == start.Top
        else:
            assert preview.window.Top + preview.window.Height / 2 == start.Top + start.Height / 2
        assert preview.window.Left >= area.x + 12
        assert preview.window.Top >= area.y + 12
        assert preview.window.Right <= area.right - 12
        assert preview.window.Bottom <= area.bottom - 12
    finally:
        preview._on_resize_up(handle, _pointer_event(handle, pointer))
    assert not preview._resizing
    assert not handle.Capture


def _actual_window_rect(control):
    """Read the real HWND, independent of the pure geometry helper."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    rect = wintypes.RECT()
    assert user32.GetWindowRect(control.Handle.ToInt64(), ctypes.byref(rect))
    return rect


def _assert_native_inside_visible_workspace(preview, parent, workspace):
    camera = _actual_window_rect(preview.window)
    client = parent.RectangleToScreen(parent.ClientRectangle)
    pane = workspace.RectangleToScreen(workspace.ClientRectangle)
    left, top = max(client.Left, pane.Left), max(client.Top, pane.Top)
    right, bottom = min(client.Right, pane.Right), min(client.Bottom, pane.Bottom)
    assert camera.left >= left + 12
    assert camera.top >= top + 12
    assert camera.right <= right - 12
    assert camera.bottom <= bottom - 12
    assert abs((camera.bottom - camera.top) - (camera.right - camera.left) * 9 / 16) <= .5


@pytest.mark.parametrize('width,height', [(1280, 720), (1366, 768), (1600, 900), (1920, 1080)])
def test_native_client_intersection_at_four_sizes_all_corners_and_resize_edges(width, height):
    parent = Form()
    parent.Location = Point(40, 30)
    parent.Size = Size(width, height)
    workspace = Panel()
    workspace.Location = Point(16, 170)
    # Reproduce the bug: a native workspace can extend beyond its owner client.
    workspace.Size = Size(parent.ClientSize.Width + 160, parent.ClientSize.Height + 80)
    parent.Controls.Add(workspace)
    preview = FloatingCameraPreview(parent, bounds_control=workspace, persist=False, demo=True)
    try:
        visible = preview._workspace()
        client = parent.RectangleToScreen(parent.ClientRectangle)
        assert visible.right == client.Right
        assert visible.bottom == client.Bottom
        for corner in ('Top left', 'Top right', 'Bottom left', 'Bottom right'):
            preview.move_to_corner(corner)
            _assert_native_inside_visible_workspace(preview, parent, workspace)
        for dx, dy in ((-10000, -10000), (10000, -10000), (-10000, 10000), (10000, 10000)):
            preview.set_clamped_location(dx, dy)
            _assert_native_inside_visible_workspace(preview, parent, workspace)
        for direction in ('n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'):
            preview.set_clamped_location(visible.x + 250, visible.y + 140)
            handle = preview.resize_handles[direction]
            pointer = handle.PointToScreen(Point(handle.Width // 2, handle.Height // 2))
            preview._on_resize_down(handle, _pointer_event(handle, pointer))
            try:
                dx = -5000 if 'w' in direction else 5000 if 'e' in direction else 0
                dy = -5000 if 'n' in direction else 5000 if 's' in direction else 0
                preview._on_resize_move(handle, _pointer_event(handle, Point(pointer.X + dx, pointer.Y + dy)))
                _assert_native_inside_visible_workspace(preview, parent, workspace)
            finally:
                preview._on_resize_up(handle, _pointer_event(handle, pointer))
    finally:
        preview.dispose()
        parent.Dispose()


def test_native_resize_events_recompute_intersection_and_margin_without_manual_clamp():
    parent = Form()
    parent.Size = Size(1920, 1080)
    workspace = Panel()
    workspace.Location = Point(16, 170)
    workspace.Size = Size(2100, 1200)
    parent.Controls.Add(workspace)
    preview = FloatingCameraPreview(parent, bounds_control=workspace, persist=False)
    try:
        preview.move_to_corner('Bottom right')
        for width, height in ((1280, 720), (1366, 768), (1600, 900), (1920, 1080)):
            parent.Size = Size(width, height)
            _assert_native_inside_visible_workspace(preview, parent, workspace)
        workspace.Location = Point(44, 210)
        workspace.Size = Size(900, 500)
        _assert_native_inside_visible_workspace(preview, parent, workspace)
    finally:
        preview.dispose()
        parent.Dispose()


def test_owned_native_window_uses_nonactivating_style_without_global_topmost(component):
    preview, parent, _ = component
    handle = preview.window.Handle.ToInt64()
    style = preview._native_api.GetWindowLongW(handle, -20)
    assert style & 0x08000000  # WS_EX_NOACTIVATE
    assert style & 0x00000080  # WS_EX_TOOLWINDOW
    assert not style & 0x00000008  # WS_EX_TOPMOST
    assert preview.window.Owner == parent
    assert preview.window.TopMost is False
