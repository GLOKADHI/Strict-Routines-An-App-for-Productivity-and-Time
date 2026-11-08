import json
import pytest
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from System.Drawing import Point, Size, Rectangle
from System.Windows.Forms import Form, FormBorderStyle
import windows.floating_camera_preview as fcp
from windows.floating_camera_preview import FloatingCameraPreview
from windows.embedded_strict_focus import EmbeddedStrictFocus, allowed_url, blocked_accelerator


@pytest.fixture(autouse=True)
def isolate_geometry_file(tmp_path, monkeypatch):
    test_geom = tmp_path / "strict_camera_preview_geometry.json"
    monkeypatch.setattr(fcp, "_geometry_file", lambda: test_geom)
    yield
    if test_geom.exists():
        try:
            test_geom.unlink()
        except OSError:
            pass


def test_floating_camera_creation_and_clamping(tmp_path):
    parent = Form()
    parent.Width = 1200
    parent.Height = 800

    cam = FloatingCameraPreview(parent)
    assert cam.window is not None
    assert cam.window.Owner == parent
    assert cam.window.Width >= FloatingCameraPreview.MIN_WIDTH
    assert cam.window.Height > 0

    # Test clamping
    cam.set_clamped_location(-1000, -1000)
    assert cam.window.Left >= 0 or cam.window.Left >= parent.Left
    assert cam.window.Top >= 0 or cam.window.Top >= parent.Top

    cam.dispose()
    parent.Dispose()


def test_eye_icon_hide_and_restore():
    parent = Form()
    parent.Width = 1000
    parent.Height = 700

    visibility_events = []
    cam = FloatingCameraPreview(parent, on_visibility_changed=lambda v: visibility_events.append(v))

    # Initial state
    assert not cam.is_user_hidden

    # Hide via eye icon
    cam.hide()
    assert cam.is_user_hidden
    assert not cam.window.Visible
    assert False in visibility_events

    # Restore via show
    cam.show()
    assert not cam.is_user_hidden
    assert cam.window.Visible
    assert True in visibility_events

    cam.dispose()
    parent.Dispose()


def test_cycle_size_aspect_ratio():
    parent = Form()
    parent.Width = 1000
    parent.Height = 700
    cam = FloatingCameraPreview(parent)

    # Initial standard size 240
    cam.window.Width = 240
    cam.cycle_size()
    assert cam.window.Width == 320
    assert cam.window.Height == 180  # 16:9

    cam.cycle_size()
    assert cam.window.Width == 180
    assert cam.window.Height == 101  # 16:9 (rounded)

    cam.cycle_size()
    assert cam.window.Width == 240
    assert cam.window.Height == 135  # 16:9

    cam.dispose()
    parent.Dispose()


def test_geometry_persistence():
    parent = Form()
    parent.Width = 1000
    parent.Height = 700
    cam = FloatingCameraPreview(parent)

    cam.window.Location = Point(500, 300)
    cam.window.Size = Size(240, 135)
    cam.is_user_hidden = True
    cam.save_geometry()

    geom_file = fcp._geometry_file()
    assert geom_file.exists()
    data = json.loads(geom_file.read_text(encoding='utf-8'))
    assert data.get('width') == 240
    assert data.get('height') == 135
    assert data.get('user_hidden') is True

    # New instance should load saved geometry
    cam2 = FloatingCameraPreview(parent)
    assert cam2.is_user_hidden is True
    assert cam2.window.Width == 240
    assert cam2.window.Height == 135

    cam.dispose()
    cam2.dispose()
    parent.Dispose()


def test_focus_layout_toggle_logic():
    class DummyApi:
        _window = None
    from windows.strict_focus_badges import PolishedFocusChrome
    focus=EmbeddedStrictFocus(DummyApi())
    focus.chrome=PolishedFocusChrome(focus)
    try:
        assert focus.chrome.header.Height==60
        focus.toggle_focus_layout()
        assert focus.chrome.expanded and focus.chrome.header.Height>60
        focus.toggle_focus_layout()
        assert not focus.chrome.expanded and focus.chrome.header.Height==60
    finally:
        focus.chrome.dispose()
