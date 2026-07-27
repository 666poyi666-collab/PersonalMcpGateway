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

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from personal_mcp_gateway.desktop.client import STATUS_DISCONNECTED
from personal_mcp_gateway.desktop.icons import build_tray_image

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personal_mcp_gateway.desktop.app import DesktopController


class WindowFactory(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


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
