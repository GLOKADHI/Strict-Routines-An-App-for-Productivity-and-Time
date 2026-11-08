"""Pure geometry for the native, presentation-only camera preview."""

from dataclasses import dataclass
from math import isfinite


ASPECT_RATIO = 16 / 9
DEFAULT_WIDTH = 240
MIN_WIDTH = 160
MAX_WIDTH = 480
MARGIN = 12


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height


def _number(value, default):
    try:
        value = float(value)
        return value if isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def inner_workspace(workspace, margin=MARGIN):
    """Keep the preview reachable, including in unusually small workspaces."""
    width, height = max(1, workspace.width), max(1, workspace.height)
    mx, my = min(margin, (width - 1) // 2), min(margin, (height - 1) // 2)
    return Rect(workspace.x + mx, workspace.y + my, width - 2 * mx, height - 2 * my)


def fit_size(width, workspace, max_width=MAX_WIDTH):
    area = inner_workspace(workspace)
    upper = max(1, int(min(MAX_WIDTH, max_width, area.width, area.height * ASPECT_RATIO)))
    lower = min(MIN_WIDTH, upper)
    width = int(round(max(lower, min(upper, _number(width, DEFAULT_WIDTH)))))
    return width, max(1, int(round(width / ASPECT_RATIO)))


def clamp_rect(rect, workspace):
    width, height = fit_size(rect.width, workspace)
    area = inner_workspace(workspace)
    x = int(round(max(area.x, min(area.right - width, _number(rect.x, area.x)))))
    y = int(round(max(area.y, min(area.bottom - height, _number(rect.y, area.y)))))
    return Rect(x, y, width, height)


def relative_geometry(rect, workspace):
    rect = clamp_rect(rect, workspace)
    area = inner_workspace(workspace)
    return {
        'relative_left': (rect.x - area.x) / max(1, area.width - rect.width),
        'relative_top': (rect.y - area.y) / max(1, area.height - rect.height),
        'relative_width': rect.width / max(1, area.width),
    }


def restore_rect(data, workspace):
    """Restore both the relative size and position; accept older absolute files."""
    area = inner_workspace(workspace)
    width = data.get('width', DEFAULT_WIDTH)
    if 'relative_width' in data:
        fraction = max(0, min(1, _number(data['relative_width'], DEFAULT_WIDTH / area.width)))
        width = fraction * area.width
    width, height = fit_size(width, workspace)
    if 'relative_left' in data and 'relative_top' in data:
        rx = max(0, min(1, _number(data['relative_left'], 1)))
        ry = max(0, min(1, _number(data['relative_top'], 1)))
        x = area.x + round((area.width - width) * rx)
        y = area.y + round((area.height - height) * ry)
    else:
        x, y = data.get('left', area.right - width), data.get('top', area.bottom - height)
    return clamp_rect(Rect(x, y, width, height), workspace)


def resize_rect(start, direction, dx, dy, workspace):
    """Resize any edge/corner, retaining 16:9 and its opposite anchor."""
    if direction not in ('n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'):
        raise ValueError('Unknown resize direction')
    horizontal = dx * (-1 if 'w' in direction else 1)
    vertical = dy * ASPECT_RATIO * (-1 if 'n' in direction else 1)
    if direction in ('n', 's'):
        delta = vertical
    elif direction in ('e', 'w'):
        delta = horizontal
    else:
        delta = horizontal if abs(horizontal) >= abs(vertical) else vertical

    area = inner_workspace(workspace)
    cx, cy = start.x + start.width / 2, start.y + start.height / 2
    horizontal_room = (start.right - area.x if 'w' in direction else
                       area.right - start.x if 'e' in direction else
                       2 * min(cx - area.x, area.right - cx))
    vertical_room = (start.bottom - area.y if 'n' in direction else
                     area.bottom - start.y if 's' in direction else
                     2 * min(cy - area.y, area.bottom - cy))
    maximum = min(MAX_WIDTH, horizontal_room, vertical_room * ASPECT_RATIO)
    width, height = fit_size(start.width + delta, workspace, max_width=maximum)
    x = (start.right - width if 'w' in direction else start.x if 'e' in direction else cx - width / 2)
    y = (start.bottom - height if 'n' in direction else start.y if 's' in direction else cy - height / 2)
    return clamp_rect(Rect(round(x), round(y), width, height), workspace)
