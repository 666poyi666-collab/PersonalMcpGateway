"""Win32 behavior that pywebview's frameless form does not provide."""

from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from ctypes import wintypes
from typing import Any

from personal_mcp_gateway.desktop.capture import native_handle
from personal_mcp_gateway.desktop.window_state import MIN_SIZE

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCDESTROY = 0x0082
WM_CANCELMODE = 0x001F
WM_NCLBUTTONDOWN = 0x00A1

HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_THICKFRAME = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x00000002
HWND_BOTTOM = 1
HWND_NOTOPMOST = -2
SWP_REFRESH_FRAME = 0x0037
SWP_RESIZE_FRAME = 0x0014
SWP_DESKTOP_MODE = 0x0033
_SUBCLASS_ID = 0x504F5949
_VK_LBUTTON = 0x01
_RESIZE_FRAME_SECONDS = 1 / 120
_RESIZE_RESPONSE_SECONDS = 0.055


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _Margins(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("right", ctypes.c_int),
        ("top", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


_resize_hooks: dict[int, tuple[Any, ...]] = {}
_resize_sessions: dict[int, threading.Event] = {}
_resize_session_lock = threading.Lock()
_desktop_window_styles: dict[int, int] = {}
_desktop_window_handles: set[int] = set()
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


def resize_target_rect(
    rect: tuple[int, int, int, int],
    start: tuple[int, int],
    cursor: tuple[int, int],
    edge: str,
    minimum: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Return a pointer-driven rectangle, retained for geometry callers and tests."""
    left, top, right, bottom = rect
    delta_x = cursor[0] - start[0]
    delta_y = cursor[1] - start[1]
    if "w" in edge:
        left += delta_x
    if "e" in edge:
        right += delta_x
    if "n" in edge:
        top += delta_y
    if "s" in edge:
        bottom += delta_y

    minimum_width, minimum_height = minimum
    if right - left < minimum_width:
        if "w" in edge:
            left = right - minimum_width
        else:
            right = left + minimum_width
    if bottom - top < minimum_height:
        if "n" in edge:
            top = bottom - minimum_height
        else:
            bottom = top + minimum_height
    return (left, top, right, bottom)


def resize_smoothing_factor(
    elapsed_seconds: float, response_seconds: float = _RESIZE_RESPONSE_SECONDS
) -> float:
    """Return the legacy frame-rate-independent easing factor for callers."""
    if elapsed_seconds <= 0:
        return 0.0
    if response_seconds <= 0:
        return 1.0
    return 1.0 - math.exp(-elapsed_seconds / response_seconds)


def interpolate_window_rect(
    current: tuple[float, float, float, float],
    target: tuple[int, int, int, int],
    factor: float,
) -> tuple[float, float, float, float]:
    """Interpolate rectangle boundaries without driving the native resize path."""
    amount = max(0.0, min(1.0, factor))
    return (
        current[0] + (target[0] - current[0]) * amount,
        current[1] + (target[1] - current[1]) * amount,
        current[2] + (target[2] - current[2]) * amount,
        current[3] + (target[3] - current[3]) * amount,
    )


def enable_transparent_background(window: Any) -> bool:
    """Let transparent WebView2 pixels compose with windows behind the board.

    pywebview makes the WebView2 controller transparent. Extending the DWM frame
    across the client area supplies the matching alpha-capable native surface.
    Failure is deliberately non-fatal: the themed ``background_color`` remains a
    readable fallback on hosts where DWM composition is unavailable.
    """
    hwnd = native_handle(window)
    if os.name != "nt" or hwnd <= 0:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        extend_frame = dwmapi.DwmExtendFrameIntoClientArea
        extend_frame.argtypes = [wintypes.HWND, ctypes.POINTER(_Margins)]
        extend_frame.restype = ctypes.c_long
        margins = _Margins(-1, -1, -1, -1)
        return int(extend_frame(hwnd, ctypes.byref(margins))) == 0
    except (AttributeError, OSError):
        return False


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
            if (
                message == WM_NCHITTEST
                and not maximized
                and int(native_hwnd) not in _desktop_window_handles
            ):
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
                with _resize_session_lock:
                    session = _resize_sessions.pop(int(native_hwnd), None)
                if session is not None:
                    session.set()
                _desktop_window_handles.discard(int(native_hwnd))
                _desktop_window_styles.pop(int(native_hwnd), None)
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


def set_desktop_window_mode(window: Any, enabled: bool, alpha: int = 255) -> bool:
    """Lock the board below normal windows without fading readable tile content."""
    hwnd = native_handle(window)
    if os.name != "nt" or hwnd <= 0:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    result_type = ctypes.c_ssize_t
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = result_type
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, result_type]
    set_style.restype = result_type
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.COLORREF,
        wintypes.BYTE,
        wintypes.DWORD,
    ]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
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

    current_style = int(get_style(hwnd, GWL_EXSTYLE))
    if enabled:
        if hwnd in _desktop_window_handles:
            return bool(user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_DESKTOP_MODE))
        _desktop_window_styles[hwnd] = current_style
        ctypes.set_last_error(0)
        previous = int(
            set_style(
                hwnd,
                GWL_EXSTYLE,
                current_style | WS_EX_LAYERED | WS_EX_NOACTIVATE,
            )
        )
        if previous == 0 and ctypes.get_last_error():
            _desktop_window_styles.pop(hwnd, None)
            return False
        if not user32.SetLayeredWindowAttributes(
            hwnd,
            0,
            max(1, min(255, int(alpha))),
            LWA_ALPHA,
        ):
            set_style(hwnd, GWL_EXSTYLE, current_style)
            _desktop_window_styles.pop(hwnd, None)
            return False
        if not user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_DESKTOP_MODE):
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            set_style(hwnd, GWL_EXSTYLE, current_style)
            _desktop_window_styles.pop(hwnd, None)
            return False
        with _resize_session_lock:
            session = _resize_sessions.pop(hwnd, None)
        if session is not None:
            session.set()
        _desktop_window_handles.add(hwnd)
        return True

    original_style = _desktop_window_styles.pop(
        hwnd,
        current_style & ~(WS_EX_LAYERED | WS_EX_NOACTIVATE),
    )
    _desktop_window_handles.discard(hwnd)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    ctypes.set_last_error(0)
    previous = int(set_style(hwnd, GWL_EXSTYLE, original_style))
    if previous == 0 and ctypes.get_last_error():
        return False
    return bool(user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_DESKTOP_MODE))


def _track_window_resize(
    hwnd: int,
    edge: str,
    original: tuple[int, int, int, int],
    start: tuple[int, int],
    minimum: tuple[int, int],
    cancel: threading.Event,
    user32: Any,
) -> None:
    """Apply pointer bounds directly so live resize ignores the OS outline setting."""
    point = _Point(start[0], start[1])
    last_applied = original
    saw_button_down = False
    grace_deadline = time.perf_counter() + 0.12
    try:
        while not cancel.is_set():
            frame_started = time.perf_counter()
            button_down = bool(user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)
            if not button_down:
                if not saw_button_down and frame_started < grace_deadline:
                    time.sleep(_RESIZE_FRAME_SECONDS)
                    continue
                break
            saw_button_down = True

            if user32.GetCursorPos(ctypes.byref(point)):
                target = resize_target_rect(
                    original,
                    start,
                    (int(point.x), int(point.y)),
                    edge,
                    minimum,
                )
                if target != last_applied:
                    left, top, right, bottom = target
                    user32.SetWindowPos(
                        hwnd,
                        0,
                        left,
                        top,
                        max(1, right - left),
                        max(1, bottom - top),
                        SWP_RESIZE_FRAME,
                    )
                    last_applied = target

            spent = time.perf_counter() - frame_started
            time.sleep(max(0.0, _RESIZE_FRAME_SECONDS - spent))
    finally:
        with _resize_session_lock:
            if _resize_sessions.get(hwnd) is cancel:
                _resize_sessions.pop(hwnd, None)


def begin_window_resize(window: Any, edge: str) -> bool:
    """Track a WebView edge drag at 120 Hz with no smoothing or release lag."""
    hwnd = native_handle(window)
    if os.name != "nt" or hwnd <= 0 or edge not in _edge_hits or hwnd in _desktop_window_handles:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [_Point]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
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

    point = _Point()
    window_rect = _Rect()
    if not user32.GetCursorPos(ctypes.byref(point)) or not user32.GetWindowRect(
        hwnd, ctypes.byref(window_rect)
    ):
        return False
    child = user32.WindowFromPoint(point)
    if child and int(child) != hwnd:
        user32.SendMessageW(child, WM_CANCELMODE, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.ReleaseCapture()

    dpi = int(get_dpi(hwnd)) if get_dpi is not None else 96
    scale = max(96, dpi) / 96
    minimum = (round(MIN_SIZE[0] * scale), round(MIN_SIZE[1] * scale))
    original = (
        int(window_rect.left),
        int(window_rect.top),
        int(window_rect.right),
        int(window_rect.bottom),
    )
    start = (int(point.x), int(point.y))
    cancel = threading.Event()
    with _resize_session_lock:
        previous = _resize_sessions.get(hwnd)
        if previous is not None:
            previous.set()
        _resize_sessions[hwnd] = cancel
    threading.Thread(
        target=_track_window_resize,
        args=(hwnd, edge, original, start, minimum, cancel, user32),
        name="poyi-window-resize",
        daemon=True,
    ).start()
    return True
