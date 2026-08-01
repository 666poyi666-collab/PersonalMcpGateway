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


_ACTIVATION_EVENT_NAME = "Local\\PoyiPersonalMcpDesktopActivate"


def create_activation_event() -> int | None:
    """Create the auto-reset event used to wake the running desktop instance."""
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    handle = kernel32.CreateEventW(None, False, False, _ACTIVATION_EVENT_NAME)
    return int(handle) if handle else None


def signal_activation_event() -> bool:
    """Notify the existing instance that an interactive shortcut was opened."""
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    event_modify_state = 0x0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenEventW(event_modify_state, False, _ACTIVATION_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def wait_for_activation_event(handle: int) -> bool:
    """Block until a shortcut wakes ``handle``; false means the wait failed."""
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    infinite = 0xFFFFFFFF
    wait_object_0 = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(handle, infinite) == wait_object_0


def wake_activation_event(handle: int) -> bool:
    """Wake the listener during shutdown."""
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    return bool(kernel32.SetEvent(handle))


def close_activation_event(handle: int) -> None:
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def build_tray_icon(controller: DesktopController, actions: dict[str, Any]) -> Any:
    """Create the tray icon; ``actions`` maps menu labels to zero-arg callables."""
    import pystray

    def wrap(key: str):
        return lambda *_args: actions[key]()

    def wrap_card(project_id: str):
        return lambda *_args: actions["toggle_card"](project_id)

    def card_checked(project_id: str):
        return lambda _item: controller.card_is_visible(project_id)

    card_labels = cast(dict[str, str], actions.get("cards", {}))
    card_menu = pystray.Menu(
        *(
            pystray.MenuItem(
                label,
                wrap_card(project_id),
                checked=card_checked(project_id),
            )
            for project_id, label in card_labels.items()
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("显示全部磁贴", wrap("show_all_cards")),
        pystray.MenuItem("恢复默认位置与大小", wrap("reset_cards")),
    )

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
        pystray.MenuItem("桌面磁贴", card_menu),
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
    transparent: bool = True,
    title: str = "Poyi Control Center",
    hidden: bool = False,
    focus: bool = True,
    easy_drag: bool = False,
    shadow: bool = True,
) -> Any:
    """``page`` is the fully inlined document, not a path -- see :mod:`.page`."""
    if transparent:
        # WebView2 reads this before the controller exists. It prevents the
        # otherwise visible opaque flash while pywebview applies its transparent
        # DefaultBackgroundColor. Preserve an explicit operator override.
        os.environ.setdefault("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "00000000")
    import webview

    options: dict[str, Any] = {
        "html": page,
        "js_api": js_api,
        "width": width,
        "height": height,
        "x": x,
        "y": y,
        "min_size": min_size,
        "resizable": True,
        "frameless": True,
        "easy_drag": easy_drag,
        "hidden": hidden,
        "focus": focus,
        "shadow": shadow,
        "background_color": background,
        "on_top": on_top,
        "transparent": transparent,
    }
    create_webview_window = cast(Any, webview.create_window)
    try:
        return create_webview_window(title, **options)
    except TypeError as error:
        # The locked pywebview version supports transparency, but retain a solid
        # themed fallback for an older system package instead of aborting startup.
        if "transparent" not in str(error):
            raise
        options.pop("transparent")
        return create_webview_window(title, **options)


def enable_native_resize(window: Any) -> bool:
    from personal_mcp_gateway.desktop.native_window import install_frameless_resize

    return install_frameless_resize(window)


def enable_native_transparency(window: Any) -> bool:
    from personal_mcp_gateway.desktop.native_window import enable_transparent_background

    if not enable_transparent_background(window):
        return False
    try:
        color = __import__("System.Drawing", fromlist=["Color"]).Color

        native = window.native
        # A black WinForms backing brush is the transparent key for the extended
        # DWM glass surface. WebView2 content remains fully color-accurate; only
        # pixels left transparent by the page reveal windows behind the board.
        native.browser.webview.DefaultBackgroundColor = color.Transparent
        native.BackColor = color.Black
    except Exception:
        # pywebview's own background remains readable if a non-WinForms host or
        # an unusual WebView2 build cannot expose these native properties.
        return False
    return True


def begin_native_resize(window: Any, edge: str) -> bool:
    from personal_mcp_gateway.desktop.native_window import begin_window_resize

    action_type = __import__("System").Action
    native = getattr(window, "native", None)
    if native is None:
        return False
    if native.InvokeRequired:
        # The JS bridge calls us from a worker thread. Enter the Win32 sizing
        # loop before that pointer-down can be released; queueing with
        # BeginInvoke races WebView2's mouse message and produces a dead drag.
        result = [False]

        def begin_resize() -> None:
            result[0] = begin_window_resize(window, edge)

        native.Invoke(action_type(begin_resize))
        return result[0]
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


def set_native_desktop_widget_mode(window: Any, enabled: bool) -> bool:
    """Apply taskbar-free desktop-widget styles without resizing the card."""
    from personal_mcp_gateway.desktop.native_window import set_desktop_widget_mode

    native = getattr(window, "native", None)
    if native is None:
        return False
    action_type = __import__("System").Action
    if native.InvokeRequired:
        result: list[bool] = []
        native.Invoke(action_type(lambda: result.append(set_desktop_widget_mode(window, enabled))))
        return bool(result and result[0])
    return set_desktop_widget_mode(window, enabled)


def set_native_desktop_regions(
    window: Any,
    regions: object,
    full_window: bool = False,
) -> bool:
    from personal_mcp_gateway.desktop.native_window import set_desktop_window_regions

    native = getattr(window, "native", None)
    if native is None:
        return False
    action_type = __import__("System").Action
    if native.InvokeRequired:
        result: list[bool] = []

        def apply_regions() -> None:
            result.append(set_desktop_window_regions(window, regions, full_window))

        native.Invoke(action_type(apply_regions))
        return bool(result and result[0])
    return set_desktop_window_regions(window, regions, full_window)


def run_window(on_start: Any, storage: Path) -> None:
    import webview

    webview.start(
        on_start,
        gui="edgechromium",
        private_mode=False,
        storage_path=str(storage),
    )
