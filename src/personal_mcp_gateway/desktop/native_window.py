"""Win32 behavior that pywebview's frameless form does not provide."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any

from personal_mcp_gateway.desktop.capture import native_handle

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCDESTROY = 0x0082
WM_NCLBUTTONDOWN = 0x00A1
WM_CANCELMODE = 0x001F

HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_REFRESH_FRAME = 0x0037
_SUBCLASS_ID = 0x504F5949


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


_resize_hooks: dict[int, tuple[Any, ...]] = {}
_edge_hits = {
    "n": HTTOP,
    "ne": HTTOPRIGHT,
    "e": HTRIGHT,
    "se": HTBOTTOMRIGHT,
    "s": HTBOTTOM,
    "sw": HTBOTTOMLEFT,
    "w": HTLEFT,
    "nw": HTTOPLEFT,
}


def resize_hit_test(
    rect: tuple[int, int, int, int], point: tuple[int, int], border: int
) -> int | None:
    """Return the Win32 resize direction for a point inside a window rectangle."""
    left, top, right, bottom = rect
    x, y = point
    on_left = left <= x < left + border
    on_right = right - border <= x < right
    on_top = top <= y < top + border
    on_bottom = bottom - border <= y < bottom
    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM
    return None


def install_frameless_resize(window: Any, border: int = 9) -> bool:
    """Give a frameless pywebview window native edge and corner resizing."""
    hwnd = native_handle(window)
    if os.name != "nt" or hwnd <= 0:
        return False
    if hwnd in _resize_hooks:
        return True

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    result_type = ctypes.c_ssize_t
    subclass_proc = ctypes.WINFUNCTYPE(
        result_type,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi is not None:
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT

    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = result_type
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, result_type]
    set_style.restype = result_type

    comctl32.SetWindowSubclass.argtypes = [
        wintypes.HWND,
        subclass_proc,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    comctl32.SetWindowSubclass.restype = wintypes.BOOL
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, subclass_proc, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
    comctl32.DefSubclassProc.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    comctl32.DefSubclassProc.restype = result_type

    @subclass_proc
    def window_proc(
        native_hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
        _subclass_id: int,
        _reference: int,
    ) -> int:
        try:
            maximized = bool(user32.IsZoomed(native_hwnd))
            if message == WM_NCCALCSIZE and wparam and not maximized:
                return 0
            if message == WM_NCHITTEST and not maximized:
                window_rect = _Rect()
                if user32.GetWindowRect(native_hwnd, ctypes.byref(window_rect)):
                    dpi = int(get_dpi(native_hwnd)) if get_dpi is not None else 96
                    edge = max(7, round(border * max(96, dpi) / 96))
                    packed = int(lparam)
                    x = ctypes.c_short(packed & 0xFFFF).value
                    y = ctypes.c_short((packed >> 16) & 0xFFFF).value
                    hit = resize_hit_test(
                        (window_rect.left, window_rect.top, window_rect.right, window_rect.bottom),
                        (x, y),
                        edge,
                    )
                    if hit is not None:
                        return hit
            if message == WM_NCDESTROY:
                result = int(comctl32.DefSubclassProc(native_hwnd, message, wparam, lparam))
                _resize_hooks.pop(int(native_hwnd), None)
                return result
        except Exception:
            pass
        return int(comctl32.DefSubclassProc(native_hwnd, message, wparam, lparam))

    if not comctl32.SetWindowSubclass(hwnd, window_proc, _SUBCLASS_ID, 0):
        return False
    _resize_hooks[hwnd] = (window_proc, user32, comctl32)

    style = int(get_style(hwnd, GWL_STYLE))
    ctypes.set_last_error(0)
    previous = int(set_style(hwnd, GWL_STYLE, style | WS_THICKFRAME))
    if previous == 0 and ctypes.get_last_error():
        comctl32.RemoveWindowSubclass(hwnd, window_proc, _SUBCLASS_ID)
        _resize_hooks.pop(hwnd, None)
        return False
    if not user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_REFRESH_FRAME):
        set_style(hwnd, GWL_STYLE, style)
        comctl32.RemoveWindowSubclass(hwnd, window_proc, _SUBCLASS_ID)
        _resize_hooks.pop(hwnd, None)
        return False
    return True


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def begin_window_resize(window: Any, edge: str) -> bool:
    """Hand one WebView pointer-down to the native Windows sizing loop."""
    hwnd = native_handle(window)
    hit = _edge_hits.get(edge)
    if os.name != "nt" or hwnd <= 0 or hit is None:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [_Point]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.ReleaseCapture.argtypes = []
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    point = _Point()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return False
    packed = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
    child = user32.WindowFromPoint(point)
    if child and int(child) != hwnd:
        user32.SendMessageW(child, WM_CANCELMODE, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, hit, packed)
    return True
