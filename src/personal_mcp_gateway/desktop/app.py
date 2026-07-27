"""Native desktop shell for the Poyi Control Center.

Renders a frameless WebView2 window plus a status tray icon. All gateway traffic
runs through :mod:`personal_mcp_gateway.desktop.client` on the Python side, so the
page keeps a ``file://`` origin and never needs cross-origin access.
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from typing import Any

from personal_mcp_gateway.desktop.client import (
    STATUS_DISCONNECTED,
    DesktopSnapshot,
    GatewayClient,
    admin_base_url,
    tray_tooltip,
)
from personal_mcp_gateway.desktop.icons import build_tray_image
from personal_mcp_gateway.desktop.page import build_page
from personal_mcp_gateway.desktop.window_state import (
    COMPACT_SIZE,
    MIN_SIZE,
    PROJECT_LAYOUT_VERSION,
    WindowState,
    load_state,
    normalize_project_layout,
    save_state,
    state_dir,
)

POLL_SECONDS = 4.0
# The pywebview window paints this before the page loads; matching the saved
# theme's surface keeps startup from flashing the opposite mode.
BACKGROUND_DARK = "#080B12"
BACKGROUND_LIGHT = "#EEF0F6"
# Session-local, not ``Global\``: the control center is a per-user app, so a second
# desktop session gets its own window instead of being refused. ``Local\`` also needs
# no SeCreateGlobalPrivilege, which an unelevated shortcut may not hold.
_MUTEX_NAME = "Local\\PoyiPersonalMcpDesktop"
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance() -> object | None:
    """Return a handle when this is the only instance, else ``None``.

    Only ERROR_ALREADY_EXISTS counts as "another instance". Any other failure lets
    the app start anyway -- for a monitor, running twice beats never running.
    """
    if sys.platform != "win32":
        return object()
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        return None
    return handle or object()


class DesktopController:
    """Owns the polling loop, the window handle and the tray icon."""

    def __init__(self, client: GatewayClient | None = None) -> None:
        self.client = client or GatewayClient()
        self.state: WindowState = load_state()
        self.window: Any | None = None
        self.icon: Any | None = None
        self._snapshot = DesktopSnapshot(
            connected=False,
            status=STATUS_DISCONNECTED,
            fetched_at="",
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()

    # ---- polling -----------------------------------------------------
    def apply(self, snapshot: DesktopSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
        icon = self.icon
        if icon is None:
            return
        try:
            icon.icon = build_tray_image(snapshot.status)
            icon.title = tray_tooltip(snapshot)
        except Exception:  # a tray hiccup must never stop the poller
            pass

    def poll_forever(self) -> None:
        while not self._stop.is_set():
            self.apply(self.client.fetch())
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def refresh_now(self) -> None:
        self.apply(self.client.fetch(force=True))

    def current(self) -> DesktopSnapshot:
        with self._lock:
            return self._snapshot

    # ---- window ------------------------------------------------------
    def remember_geometry(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            if not self.state.compact:
                self.state.width = int(window.width)
                self.state.height = int(window.height)
            self.state.x = int(window.x)
            self.state.y = int(window.y)
        except Exception:
            return
        save_state(self.state)

    def show_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.show()
            window.restore()
            if self.state.desktop_mode:
                from personal_mcp_gateway.desktop import shell

                shell.set_native_desktop_mode(window, True)
        except Exception:
            pass

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        self.remember_geometry()
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        window = self.window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass


class DesktopApi:
    """Methods exposed to the page as ``pywebview.api.*``."""

    def __init__(self, controller: DesktopController) -> None:
        self._controller = controller

    def snapshot(self) -> dict[str, Any]:
        controller = self._controller
        payload = controller.current().to_payload()
        payload["view"] = {
            "compact": controller.state.compact,
            "onTop": controller.state.on_top,
            "desktopMode": controller.state.desktop_mode,
            "theme": controller.state.theme,
            "adminUrl": admin_base_url(),
            "projectLayoutVersion": controller.state.project_layout_version,
            "projectLayout": controller.state.project_layout or {},
        }
        return payload

    def refresh(self) -> dict[str, Any]:
        self._controller.refresh_now()
        return self.snapshot()

    def set_compact(self, compact: bool) -> dict[str, Any]:
        controller = self._controller
        if controller.state.desktop_mode:
            return self.snapshot()
        window = controller.window
        if window is not None and compact != controller.state.compact:
            if compact:
                controller.remember_geometry()
                window.resize(*COMPACT_SIZE)
            else:
                window.resize(controller.state.width, controller.state.height)
        controller.state.compact = compact
        save_state(controller.state)
        return self.snapshot()

    def set_on_top(self, on_top: bool) -> dict[str, Any]:
        controller = self._controller
        if controller.state.desktop_mode:
            return self.snapshot()
        window = controller.window
        if window is not None:
            try:
                window.on_top = on_top
            except Exception:
                pass
        controller.state.on_top = on_top
        save_state(controller.state)
        return self.snapshot()

    def set_desktop_mode(self, desktop_mode: bool) -> dict[str, Any]:
        controller = self._controller
        enabled = bool(desktop_mode)
        window = controller.window
        if enabled == controller.state.desktop_mode:
            return self.snapshot()

        if window is not None:
            from personal_mcp_gateway.desktop import shell

            if enabled and controller.state.compact:
                window.resize(controller.state.width, controller.state.height)
            if enabled:
                try:
                    window.on_top = False
                except Exception:
                    pass
            if not shell.set_native_desktop_mode(window, enabled):
                if enabled and controller.state.compact:
                    window.resize(*COMPACT_SIZE)
                if enabled and controller.state.on_top:
                    try:
                        window.on_top = True
                    except Exception:
                        pass
                return self.snapshot()

        if enabled:
            controller.state.compact = False
            controller.state.on_top = False
        controller.state.desktop_mode = enabled
        save_state(controller.state)
        return self.snapshot()

    def set_theme(self, theme: str) -> dict[str, Any]:
        controller = self._controller
        controller.state.theme = theme if theme in {"dark", "light"} else "light"
        save_state(controller.state)
        return self.snapshot()

    def set_project_layout(self, layout: dict[str, Any]) -> dict[str, Any]:
        controller = self._controller
        controller.state.project_layout = normalize_project_layout(layout)
        controller.state.project_layout_version = PROJECT_LAYOUT_VERSION
        save_state(controller.state)
        return self.snapshot()

    def reset_project_layout(self) -> dict[str, Any]:
        controller = self._controller
        controller.state.project_layout = {}
        controller.state.project_layout_version = PROJECT_LAYOUT_VERSION
        save_state(controller.state)
        return self.snapshot()

    def capture(self) -> dict[str, Any]:
        from personal_mcp_gateway.desktop.capture import capture_hwnd, native_handle

        window = self._controller.window
        try:
            path = capture_hwnd(native_handle(window))
        except (OSError, ValueError):
            return {"ok": False, "message": "截图失败"}
        return {"ok": True, "message": "截图已保存", "path": str(path)}

    def begin_window_resize(self, edge: str) -> dict[str, bool]:
        from personal_mcp_gateway.desktop import shell

        window = self._controller.window
        return {
            "ok": (
                window is not None
                and not self._controller.state.desktop_mode
                and shell.begin_native_resize(window, edge)
            )
        }

    def minimize(self) -> None:
        window = self._controller.window
        if window is not None:
            window.minimize()

    def hide_to_tray(self) -> None:
        controller = self._controller
        controller.remember_geometry()
        window = controller.window
        if window is not None:
            window.hide()

    def open_web(self) -> None:
        webbrowser.open(f"{admin_base_url()}/admin/status")

    def repair_fleet(self) -> dict[str, Any]:
        """Drop a repair request for the fleet watchdog.

        Works even while the gateway is down: the request is a file in the
        watchdog's trigger directory, not a gateway API call.
        """
        from personal_mcp_gateway.admin import fleet

        try:
            fleet.request_repair("desktop")
        except OSError:
            return {
                "ok": False,
                "message": "看护服务未部署或无权限: 请先运行 fleet\\Repair-PoyiFleet.cmd",
            }
        return {"ok": True, "message": "修复请求已发送: 看护服务正在处理"}

    def quit(self) -> None:
        self._controller.shutdown()


def tray_actions(controller: DesktopController, api: DesktopApi) -> dict[str, Any]:
    return {
        "show": controller.show_window,
        "compact": lambda: api.set_compact(not controller.state.compact),
        "desktop": lambda: api.set_desktop_mode(not controller.state.desktop_mode),
        "on_top": lambda: api.set_on_top(not controller.state.on_top),
        "web": api.open_web,
        "refresh": controller.refresh_now,
        "quit": controller.shutdown,
    }


def main() -> int:
    guard = acquire_single_instance()
    if guard is None:
        print("Poyi Control Center 已在运行: 请查看系统托盘", file=sys.stderr)
        return 0
    try:
        from personal_mcp_gateway.desktop import shell
    except ImportError:
        print("缺少桌面依赖: 请先安装 personal-mcp-gateway[desktop]", file=sys.stderr)
        return 2

    controller = DesktopController()
    api = DesktopApi(controller)
    state = controller.state
    width, height = COMPACT_SIZE if state.compact else state.size()
    storage = state_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)

    window = shell.create_window(
        page=build_page(),
        js_api=api,
        width=width,
        height=height,
        x=state.x,
        y=state.y,
        min_size=MIN_SIZE,
        on_top=state.on_top and not state.desktop_mode,
        background=BACKGROUND_DARK if state.theme == "dark" else BACKGROUND_LIGHT,
    )
    if window is None:
        print("无法创建桌面窗口: 请确认已安装 WebView2 运行时", file=sys.stderr)
        return 3
    controller.window = window

    def prepare_native_window() -> None:
        shell.enable_native_resize(window)

    window.events.before_show += prepare_native_window
    if state.desktop_mode:
        window.events.shown += lambda: shell.set_native_desktop_mode(window, True)

    def on_start() -> None:
        threading.Thread(target=controller.poll_forever, name="poyi-poll", daemon=True).start()
        try:
            icon = shell.build_tray_icon(controller, tray_actions(controller, api))
        except Exception:  # the window stays usable without a tray icon
            return
        controller.icon = icon
        shell.start_tray(icon)

    shell.run_window(on_start, storage)
    controller.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - desktop entry point
    raise SystemExit(main())
