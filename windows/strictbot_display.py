"""Physical work areas and per-monitor DPI from supported Windows APIs."""
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import sys


@dataclass(frozen=True)
class Display:
    handle: int
    work: tuple
    bounds: tuple
    dpi: int = 96
    primary: bool = True
    device: str = "Display"


def enable_thread_dpi():
    if sys.platform == "win32":
        try:
            user = c.windll.user32
            user.SetThreadDpiAwarenessContext.argtypes = [c.c_void_p]
            user.SetThreadDpiAwarenessContext.restype = c.c_void_p
            user.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        except (OSError, AttributeError):
            pass


def displays():
    if sys.platform != "win32":
        return [Display(0, (0, 0, 1366, 728), (0, 0, 1366, 768))]
    user = c.windll.user32
    class Info(c.Structure):
        _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT),
                    ("flags", w.DWORD), ("device", w.WCHAR * 32)]
    found = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, c.POINTER(w.RECT), w.LPARAM)
    def visit(handle, dc, rect, param):
        info = Info(); info.size = c.sizeof(info)
        if user.GetMonitorInfoW(handle, c.byref(info)):
            dpi_x = w.UINT(96); dpi_y = w.UINT(96)
            try:
                c.windll.shcore.GetDpiForMonitor(w.HANDLE(handle), 0, c.byref(dpi_x), c.byref(dpi_y))
            except (OSError, AttributeError):
                pass
            to_tuple = lambda r: (r.left, r.top, r.right, r.bottom)
            found.append(Display(int(handle), to_tuple(info.work), to_tuple(info.monitor),
                                 int(dpi_x.value), bool(info.flags & 1), info.device))
        return True
    callback = callback_type(visit)
    user.EnumDisplayMonitors(None, None, callback, 0)
    return found or [Display(0, (0, 0, user.GetSystemMetrics(0), user.GetSystemMetrics(1)),
                            (0, 0, user.GetSystemMetrics(0), user.GetSystemMetrics(1)))]


def select_display(hwnd=0, preference="application", previous=None):
    all_displays = displays()
    if sys.platform == "win32" and preference != "primary":
        user = c.windll.user32
        user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
        user.MonitorFromWindow.restype = w.HANDLE
        handle = None
        if preference == "cursor":
            point = w.POINT(); user.GetCursorPos(c.byref(point))
            handle = next((d.handle for d in all_displays if d.bounds[0] <= point.x < d.bounds[2]
                           and d.bounds[1] <= point.y < d.bounds[3]), None)
        elif hwnd and user.IsWindow(w.HWND(hwnd)):
            handle = user.MonitorFromWindow(w.HWND(hwnd), 2)
        elif previous:
            handle = previous
        match = next((d for d in all_displays if d.handle == handle), None)
        if match:
            return match
    return next((d for d in all_displays if d.primary), all_displays[0])


def layout(display, prefs):
    left, top, right, bottom = display.work
    width, height = right-left, bottom-top
    base = min(display.dpi / 96, width / 1050, height / 650)
    base = max(.35, base)
    popup_scale = base * prefs["popup_size"] / 100
    card_w = min(width*.70, 520*popup_scale)
    card_h = min(height*.82, 345*popup_scale)
    robot_scale = min(base * prefs["robot_size"] / 100, height*.75/330, width*.23/200)
    gap = 24*base
    robot_w = 175*robot_scale
    combined = card_w+robot_w+gap
    x = left + (width-combined)/2
    y = top + (height-card_h) * (.25 if prefs["popup_position"] == "upper-center" else .5)
    right_side = prefs["robot_position"] == "right"
    card_x = x if right_side else x+robot_w+gap
    anchor = card_x+card_w+gap+robot_w/2 if right_side else x+robot_w/2
    floor = max(y+card_h, top+height*.72)
    floor = min(bottom-12*base, max(top+330*robot_scale, floor))
    return dict(card=(round(card_w), round(card_h), round(card_x), round(y)),
                robot_scale=robot_scale, anchor=anchor-left, floor=floor-top,
                width=width, height=height, base=base, right_side=right_side)
