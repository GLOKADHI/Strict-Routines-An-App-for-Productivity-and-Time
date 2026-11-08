"""Camera sizing and placement checks that do not load WinForms or services."""

import pytest

from windows.camera_preview_geometry import (
    ASPECT_RATIO, MARGIN, MAX_WIDTH, MIN_WIDTH, Rect, clamp_rect, fit_size,
    inner_workspace, relative_geometry, resize_rect, restore_rect,
)


WORKSPACE = Rect(100, 200, 1200, 800)


def assert_inside(rect, workspace=WORKSPACE):
    area = inner_workspace(workspace)
    assert area.x <= rect.x < rect.right <= area.right
    assert area.y <= rect.y < rect.bottom <= area.bottom
    assert abs(rect.height - rect.width / ASPECT_RATIO) <= .5


def test_first_position_is_small_16_by_9_and_clear_of_top_toolbar():
    rect = restore_rect({}, WORKSPACE)
    assert (rect.width, rect.height) == (240, 135)
    assert rect.right == WORKSPACE.right - 12
    assert rect.bottom == WORKSPACE.bottom - 12
    assert rect.y > WORKSPACE.y + 40


@pytest.mark.parametrize('width', [-1000, 1, 160, 240, 480, 9000, None, 'invalid', float('nan')])
def test_size_has_real_minimum_maximum_and_aspect_ratio(width):
    w, h = fit_size(width, WORKSPACE)
    assert MIN_WIDTH <= w <= MAX_WIDTH
    assert abs(h - w / ASPECT_RATIO) <= .5


@pytest.mark.parametrize('workspace', [Rect(-500, -200, 400, 300), Rect(50, 50, 100, 60), Rect(1, 2, 1, 1)])
def test_clamp_fits_small_and_negative_coordinate_workspaces(workspace):
    assert_inside(clamp_rect(Rect(-9000, 9999, 10000, 9999), workspace), workspace)


@pytest.mark.parametrize('direction,dx,dy', [
    ('n', 0, -36), ('s', 0, 36), ('e', 64, 0), ('w', -64, 0),
    ('nw', -64, -36), ('ne', 64, -36), ('sw', -64, 36), ('se', 64, 36),
])
def test_every_edge_and_corner_resizes_with_opposite_anchor(direction, dx, dy):
    start = Rect(450, 450, 240, 135)
    rect = resize_rect(start, direction, dx, dy, WORKSPACE)
    assert (rect.width, rect.height) == (304, 171)
    assert_inside(rect)
    if 'w' in direction:
        assert rect.right == start.right
    elif 'e' in direction:
        assert rect.x == start.x
    else:
        assert rect.x + rect.width / 2 == start.x + start.width / 2
    if 'n' in direction:
        assert rect.bottom == start.bottom
    elif 's' in direction:
        assert rect.y == start.y
    else:
        assert rect.y + rect.height / 2 == start.y + start.height / 2


@pytest.mark.parametrize('direction', ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'])
@pytest.mark.parametrize('delta', [-10000, 10000])
def test_resizing_at_workspace_boundary_stays_bounded(direction, delta):
    start = restore_rect({}, WORKSPACE)
    rect = resize_rect(start, direction, delta, delta, WORKSPACE)
    assert_inside(rect)
    assert MIN_WIDTH <= rect.width <= MAX_WIDTH


def test_relative_position_and_size_survive_workspace_move_and_resize():
    original = restore_rect({'width': 240, 'relative_left': .25, 'relative_top': .75}, WORKSPACE)
    data = relative_geometry(original, WORKSPACE)
    moved = restore_rect(data, Rect(600, 700, WORKSPACE.width, WORKSPACE.height))
    assert moved.x - original.x == 500
    assert moved.y - original.y == 500
    assert moved.width == original.width
    wider = restore_rect(data, Rect(600, 700, 1516, 1000))
    assert wider.width == round(data['relative_width'] * (1516 - 2 * MARGIN))
    restored = restore_rect(relative_geometry(wider, Rect(600, 700, 1516, 1000)), WORKSPACE)
    assert abs(restored.x - original.x) <= 1
    assert abs(restored.y - original.y) <= 1
    assert restored.width == original.width


def test_legacy_absolute_file_migrates_height_to_16_by_9():
    rect = restore_rect({'left': 250, 'top': 350, 'width': 240, 'height': 208}, WORKSPACE)
    assert rect == Rect(250, 350, 240, 135)


def test_invalid_saved_values_are_reachable():
    rect = restore_rect({'width': 'bad', 'relative_left': float('nan'),
                         'relative_top': 'bad', 'relative_width': float('inf')}, WORKSPACE)
    assert_inside(rect)


def test_unknown_resize_handle_is_rejected():
    with pytest.raises(ValueError):
        resize_rect(Rect(200, 300, 240, 135), 'center', 20, 20, WORKSPACE)
