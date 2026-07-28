"""The untyped GUI boundary: pywebview windows and pystray icons.

pywebview and pystray ship no type stubs, so the suppressions below are scoped to
this file. Everything with real logic lives in :mod:`personal_mcp_gateway.desktop.app`
and stays under strict checking.
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
# pyright: reportUnknownLambdaType=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from personal_mcp_gateway.desktop.client import STATUS_DISCONNECTED
from personal_mcp_gateway.desktop.icons import build_tray_image

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personal_mcp_gateway.desktop.app import DesktopController


class WindowFactory(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


def show_existing_window(title: str = "Poyi Control Center") -> bool:
    """Reveal this launcher's existing top-level window, if it is hidden."""
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    process_query_limited_information = 0x1000
    sw_show = 5
    sw_restore = 9

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    executable = os.path.normcase(os.path.abspath(sys.executable))
    existing: list[int] = []

    @callback_type
    def find_window(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        window_title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, window_title, len(window_title))
        if window_title.value != title:
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id.value,
        )
        if not process:
            return True
        try:
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                process,
                0,
                image,
                ctypes.byref(size),
            ):
                return True
            if os.path.normcase(os.path.abspath(image.value)) != executable:
                return True
        finally:
            kernel32.CloseHandle(process)

        existing.append(int(hwnd))
        return False

    user32.EnumWindows(find_window, 0)
    if not existing:
        return False
    hwnd = existing[0]
    user32.ShowWindowAsync(hwnd, sw_show)
    user32.ShowWindowAsync(hwnd, sw_restore)
    user32.SetForegroundWindow(hwnd)
    return True


def build_tray_icon(controller: DesktopController, actions: dict[str, Any]) -> Any:
    """Create the tray icon; ``actions`` maps menu labels to zero-arg callables."""
    import pystray

    def wrap(key: str):
        return lambda *_args: actions[key]()

    menu = pystray.Menu(
        pystray.MenuItem("显示看板", wrap("show"), default=True),
        pystray.MenuItem(
            "固定到桌面",
            wrap("desktop"),
            checked=lambda _i: controller.state.desktop_mode,
        ),
        pystray.MenuItem(
            "紧凑模式",
            wrap("compact"),
            checked=lambda _i: controller.state.compact,
            enabled=cast(Any, lambda _i: not controller.state.desktop_mode),
        ),
        pystray.MenuItem(
            "窗口置顶",
            wrap("on_top"),
            checked=lambda _i: controller.state.on_top,
            enabled=cast(Any, lambda _i: not controller.state.desktop_mode),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开网页版", wrap("web")),
        pystray.MenuItem("立即刷新", wrap("refresh")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", wrap("quit")),
    )
    return pystray.Icon(
        "poyi-control-center",
        build_tray_image(STATUS_DISCONNECTED),
        "Poyi Control Center",
        menu,
    )


def start_tray(icon: Any) -> None:
    threading.Thread(target=icon.run, name="poyi-tray", daemon=True).start()


def create_window(
    *,
    page: str,
    js_api: object,
    width: int,
    height: int,
    x: int | None,
    y: int | None,
    min_size: tuple[int, int],
    on_top: bool,
    background: str,
) -> Any:
    """``page`` is the fully inlined document, not a path -- see :mod:`.page`."""
    import webview

    return webview.create_window(
        "Poyi Control Center",
        html=page,
        js_api=js_api,
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=min_size,
        resizable=True,
        frameless=True,
        easy_drag=False,
        background_color=background,
        on_top=on_top,
    )


def enable_native_resize(window: Any) -> bool:
    from personal_mcp_gateway.desktop.native_window import install_frameless_resize

    return install_frameless_resize(window)


def begin_native_resize(window: Any, edge: str) -> bool:
    from personal_mcp_gateway.desktop.native_window import begin_window_resize

    action_type = __import__("System").Action
    native = getattr(window, "native", None)
    if native is None:
        return False
    if native.InvokeRequired:
        native.BeginInvoke(action_type(lambda: begin_window_resize(window, edge)))
        return True
    return begin_window_resize(window, edge)


def set_native_desktop_mode(window: Any, enabled: bool) -> bool:
    from personal_mcp_gateway.desktop.native_window import set_desktop_window_mode

    native = getattr(window, "native", None)
    if native is None:
        return False
    action_type = __import__("System").Action
    if native.InvokeRequired:
        result: list[bool] = []
        native.Invoke(action_type(lambda: result.append(set_desktop_window_mode(window, enabled))))
        return bool(result and result[0])
    return set_desktop_window_mode(window, enabled)


def run_window(on_start: Any, storage: Path) -> None:
    import webview

    webview.start(
        on_start,
        gui="edgechromium",
        private_mode=False,
        storage_path=str(storage),
    )
