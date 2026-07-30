from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from personal_mcp_gateway.desktop import native_window
from personal_mcp_gateway.desktop.native_window import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTLEFT,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    begin_window_resize,
    enable_transparent_background,
    install_frameless_resize,
    interpolate_window_rect,
    resize_hit_test,
    resize_smoothing_factor,
    resize_target_rect,
    set_desktop_window_mode,
)


class _FakeCall:
    def __init__(self, function: Callable[..., object]) -> None:
        self.function = function
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.function(*args)


class _ResizeUser32:
    def __init__(self) -> None:
        self.cursor_reads = 0
        self.button_reads = 0
        self.positions: list[tuple[int, int, int, int]] = []
        self.messages: list[tuple[object, ...]] = []
        self.foreground: list[int] = []
        self.released = False
        self.GetCursorPos = _FakeCall(self._get_cursor_pos)
        self.GetWindowRect = _FakeCall(self._get_window_rect)
        self.WindowFromPoint = _FakeCall(self._window_from_point)
        self.GetAsyncKeyState = _FakeCall(self._get_async_key_state)
        self.ReleaseCapture = _FakeCall(self._release_capture)
        self.SetForegroundWindow = _FakeCall(self._set_foreground_window)
        self.SendMessageW = _FakeCall(self._send_message)
        self.SetWindowPos = _FakeCall(self._set_window_pos)
        self.GetDpiForWindow = _FakeCall(self._get_dpi_for_window)

    def _get_cursor_pos(self, pointer: object) -> bool:
        point = cast(Any, pointer)._obj
        positions = ((1300, 1100), (1340, 1120), (1400, 1160))
        point.x, point.y = positions[min(self.cursor_reads, len(positions) - 1)]
        self.cursor_reads += 1
        return True

    @staticmethod
    def _get_window_rect(_hwnd: object, pointer: object) -> bool:
        rect = cast(Any, pointer)._obj
        rect.left, rect.top, rect.right, rect.bottom = 100, 200, 1300, 1100
        return True

    @staticmethod
    def _window_from_point(_point: object) -> int:
        return 456

    def _get_async_key_state(self, _key: object) -> int:
        self.button_reads += 1
        return 0x8000 if self.button_reads <= 2 else 0

    def _release_capture(self) -> bool:
        self.released = True
        return True

    def _set_foreground_window(self, hwnd: object) -> bool:
        self.foreground.append(int(cast(int, hwnd)))
        return True

    def _send_message(self, *args: object) -> int:
        self.messages.append(args)
        return 0

    def _set_window_pos(
        self,
        _hwnd: object,
        _after: object,
        left: object,
        top: object,
        width: object,
        height: object,
        _flags: object,
    ) -> bool:
        self.positions.append(
            (
                int(cast(int, left)),
                int(cast(int, top)),
                int(cast(int, width)),
                int(cast(int, height)),
            )
        )
        return True

    @staticmethod
    def _get_dpi_for_window(_hwnd: object) -> int:
        return 144


class _ImmediateThread:
    def __init__(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
        name: str,
        daemon: bool,
    ) -> None:
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon

    def start(self) -> None:
        self.target(*self.args)


class _DwmApi:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[int, tuple[int, int, int, int]]] = []
        self.DwmExtendFrameIntoClientArea = _FakeCall(self._extend_frame)

    def _extend_frame(self, hwnd: object, pointer: object) -> int:
        margins = cast(Any, pointer)._obj
        self.calls.append(
            (
                int(cast(int, hwnd)),
                (margins.left, margins.right, margins.top, margins.bottom),
            )
        )
        return self.result


class _DesktopUser32:
    def __init__(self) -> None:
        self.ex_style = native_window.WS_EX_APPWINDOW | 0x00000100
        self.position_after: list[int] = []
        self.GetWindowLongPtrW = _FakeCall(self._get_window_long)
        self.GetWindowLongW = self.GetWindowLongPtrW
        self.SetWindowLongPtrW = _FakeCall(self._set_window_long)
        self.SetWindowLongW = self.SetWindowLongPtrW
        self.SetWindowPos = _FakeCall(self._set_window_pos)

    def _get_window_long(self, _hwnd: object, index: object) -> int:
        assert int(cast(int, index)) == native_window.GWL_EXSTYLE
        return self.ex_style

    def _set_window_long(self, _hwnd: object, index: object, style: object) -> int:
        assert int(cast(int, index)) == native_window.GWL_EXSTYLE
        previous = self.ex_style
        self.ex_style = int(cast(int, style))
        return previous

    def _set_window_pos(
        self,
        _hwnd: object,
        insert_after: object,
        _left: object,
        _top: object,
        _width: object,
        _height: object,
        flags: object,
    ) -> bool:
        assert int(cast(int, flags)) == native_window.SWP_DESKTOP_MODE
        self.position_after.append(int(cast(int, insert_after)))
        return True


def test_resize_hit_test_covers_every_edge_and_corner() -> None:
    rect = (100, 200, 500, 600)
    assert resize_hit_test(rect, (101, 201), 10) == HTTOPLEFT
    assert resize_hit_test(rect, (499, 201), 10) == HTTOPRIGHT
    assert resize_hit_test(rect, (101, 599), 10) == HTBOTTOMLEFT
    assert resize_hit_test(rect, (499, 599), 10) == HTBOTTOMRIGHT
    assert resize_hit_test(rect, (101, 400), 10) == HTLEFT
    assert resize_hit_test(rect, (499, 400), 10) == HTRIGHT
    assert resize_hit_test(rect, (300, 201), 10) == HTTOP
    assert resize_hit_test(rect, (300, 599), 10) == HTBOTTOM
    assert resize_hit_test(rect, (300, 400), 10) is None


def test_native_resize_requires_a_real_window_handle() -> None:
    assert install_frameless_resize(object()) is False
    assert begin_window_resize(object(), "se") is False
    assert begin_window_resize(object(), "not-an-edge") is False
    assert enable_transparent_background(object()) is False
    assert set_desktop_window_mode(object(), True) is False


def test_transparent_background_extends_the_dwm_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dwmapi = _DwmApi()

    def fake_native_handle(_window: Any) -> int:
        return 321

    def fake_windll(_name: str, *, use_last_error: bool) -> _DwmApi:
        assert use_last_error is True
        return dwmapi

    monkeypatch.setattr(native_window.os, "name", "nt")
    monkeypatch.setattr(native_window, "native_handle", fake_native_handle)
    monkeypatch.setattr(
        native_window.ctypes,
        "WinDLL",
        fake_windll,
        raising=False,
    )

    assert enable_transparent_background(object()) is True
    assert dwmapi.calls == [(321, (-1, -1, -1, -1))]


def test_transparent_background_falls_back_when_dwm_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dwmapi = _DwmApi(result=-1)

    def fake_native_handle(_window: Any) -> int:
        return 321

    def fake_windll(_name: str, *, use_last_error: bool) -> _DwmApi:
        assert use_last_error is True
        return dwmapi

    monkeypatch.setattr(native_window.os, "name", "nt")
    monkeypatch.setattr(native_window, "native_handle", fake_native_handle)
    monkeypatch.setattr(
        native_window.ctypes,
        "WinDLL",
        fake_windll,
        raising=False,
    )

    assert enable_transparent_background(object()) is False


def test_desktop_mode_adds_and_restores_native_window_styles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user32 = _DesktopUser32()

    def fake_native_handle(_window: Any) -> int:
        return 321

    def fake_windll(_name: str, use_last_error: bool) -> _DesktopUser32:
        assert use_last_error is True
        return user32

    monkeypatch.setattr(native_window.os, "name", "nt")
    monkeypatch.setattr(native_window, "native_handle", fake_native_handle)
    monkeypatch.setattr(
        native_window.ctypes,
        "WinDLL",
        fake_windll,
        raising=False,
    )

    assert set_desktop_window_mode(object(), True) is True
    assert user32.ex_style & native_window.WS_EX_NOACTIVATE
    assert user32.ex_style & native_window.WS_EX_TOOLWINDOW
    assert not user32.ex_style & native_window.WS_EX_APPWINDOW
    assert user32.position_after == [native_window.HWND_BOTTOM]
    assert begin_window_resize(object(), "se") is False

    assert set_desktop_window_mode(object(), False) is True
    assert user32.ex_style == native_window.WS_EX_APPWINDOW | 0x00000100
    assert user32.position_after[-1] == native_window.HWND_NOTOPMOST


def test_resize_target_rect_moves_each_requested_boundary() -> None:
    rect = (100, 200, 500, 600)
    minimum = (240, 260)
    assert resize_target_rect(rect, (500, 600), (560, 640), "se", minimum) == (
        100,
        200,
        560,
        640,
    )
    assert resize_target_rect(rect, (100, 200), (140, 230), "nw", minimum) == (
        140,
        230,
        500,
        600,
    )


def test_resize_target_rect_enforces_minimum_from_the_active_edge() -> None:
    rect = (100, 200, 500, 600)
    assert resize_target_rect(rect, (500, 400), (250, 400), "e", (300, 300)) == (
        100,
        200,
        400,
        600,
    )
    assert resize_target_rect(rect, (100, 200), (450, 550), "nw", (300, 300)) == (
        200,
        300,
        500,
        600,
    )


def test_window_rect_interpolation_remains_available_for_geometry_callers() -> None:
    factor = resize_smoothing_factor(1 / 60)
    assert 0.2 < factor < 0.4
    current = (0.0, 0.0, 100.0, 100.0)
    target = (20, 10, 180, 160)
    moved = interpolate_window_rect(current, target, factor)
    assert 0 < moved[0] < target[0]
    assert 0 < moved[1] < target[1]
    assert current[2] < moved[2] < target[2]
    assert current[3] < moved[3] < target[3]
    assert resize_smoothing_factor(0) == 0
    assert resize_smoothing_factor(1, 0) == 1


def test_window_resize_tracks_pointer_without_smoothing_or_release_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user32 = _ResizeUser32()

    def fake_native_handle(_window: Any) -> int:
        return 123

    def fake_windll(_name: str, use_last_error: bool) -> _ResizeUser32:
        assert use_last_error is True
        return user32

    def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(native_window.os, "name", "nt")
    monkeypatch.setattr(native_window, "native_handle", fake_native_handle)
    monkeypatch.setattr(
        native_window.ctypes,
        "WinDLL",
        fake_windll,
        raising=False,
    )
    monkeypatch.setattr(native_window.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(native_window.time, "sleep", no_sleep)

    assert begin_window_resize(object(), "se") is True

    assert user32.messages and user32.messages[0][1] == native_window.WM_CANCELMODE
    assert user32.foreground == [123]
    assert user32.released is True
    assert user32.messages[1:] == []
    assert user32.positions == [
        (100, 200, 1240, 920),
        (100, 200, 1300, 960),
    ]
