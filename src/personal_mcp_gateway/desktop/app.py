"""Native desktop shell for the Poyi Control Center.

Renders a frameless WebView2 window plus a status tray icon. All gateway traffic
runs through :mod:`personal_mcp_gateway.desktop.client` on the Python side, so the
page keeps a ``file://`` origin and never needs cross-origin access.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser
from typing import Any

from personal_mcp_gateway.desktop.client import (
    DesktopSnapshot,
    GatewayClient,
    admin_base_url,
    snapshot_cache_path,
    tray_tooltip,
)
from personal_mcp_gateway.desktop.icons import build_tray_image
from personal_mcp_gateway.desktop.page import build_card_page, build_page
from personal_mcp_gateway.desktop.window_state import (
    CARD_LAYOUT_VERSION,
    CARD_MIN_SIZE,
    COMPACT_SIZE,
    DEFAULT_CARD_LAYOUT,
    MIN_SIZE,
    PROJECT_LAYOUT_VERSION,
    WindowState,
    load_state,
    normalize_card_layout,
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

CARD_PROJECT_IDS = ("foxlink", "watch", "journal", "personal", "bzsjk")
CARD_TITLES = {
    "foxlink": "FocusLink",
    "watch": "步序",
    "journal": "拾光日记",
    "personal": "服务中心",
    "bzsjk": "不做手机控",
}
CARD_SIZE_SCALES = {"small": 0.72, "medium": 1.0, "large": 1.35}
_ACTIVATION_SIGNAL_ATTEMPTS = 8
_ACTIVATION_SIGNAL_DELAY = 0.05


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


def show_existing_instance() -> bool:
    """Ask an already-running instance to open its management window."""
    try:
        from personal_mcp_gateway.desktop import shell
    except Exception:
        return False
    # Retry briefly to cover a second shortcut launch racing the first process's
    # cold startup.  The existing process owns all native surfaces.
    for attempt in range(_ACTIVATION_SIGNAL_ATTEMPTS):
        try:
            if shell.signal_activation_event():
                return True
        except Exception:
            pass
        if attempt + 1 < _ACTIVATION_SIGNAL_ATTEMPTS:
            time.sleep(_ACTIVATION_SIGNAL_DELAY)
    return False


class DesktopController:
    """Owns the polling loop, the window handle and the tray icon."""

    def __init__(self, client: GatewayClient | None = None) -> None:
        self.client = client or GatewayClient(cache_path=snapshot_cache_path())
        self.state: WindowState = load_state()
        self.window: Any | None = None
        self.card_windows: dict[str, Any] = {}
        self.icon: Any | None = None
        self._snapshot = self.client.initial_snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._card_state_lock = threading.RLock()
        self._card_save_timer: threading.Timer | None = None
        self._card_save_generation = 0
        self._card_geometry_armed: set[str] = set()
        self._card_geometry_suppression: dict[str, int] = {}
        self._activation_event: int | None = None
        self._activation_thread: threading.Thread | None = None

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
        """Request a refresh without blocking a WebView bridge/UI thread."""

        self._wake.set()

    def current(self) -> DesktopSnapshot:
        with self._lock:
            return self._snapshot

    def snapshot_payload(self, card_id: str | None = None) -> dict[str, Any]:
        payload = self.current().to_payload()
        payload["view"] = {
            "compact": self.state.compact,
            "onTop": self.state.on_top,
            "desktopMode": card_id is not None,
            "cardVisibility": dict(self.state.card_visibility),
            "visibleCardCount": sum(self.state.card_visibility.values()),
            "theme": self.state.theme,
            "adminUrl": admin_base_url(),
            "projectLayoutVersion": self.state.project_layout_version,
            "projectLayout": self.state.project_layout or {},
            "cardLayoutVersion": self.state.card_layout_version,
            "cardLayout": self.state.card_layout or {},
            "cardMode": card_id is not None,
            "cardId": card_id,
            "hiddenCards": [
                project_id
                for project_id in CARD_PROJECT_IDS
                if not self.state.card_visibility.get(project_id, False)
            ],
        }
        return payload

    def card_geometry(self, project_id: str) -> dict[str, int]:
        fallback = DEFAULT_CARD_LAYOUT[project_id]
        saved = (self.state.card_layout or {}).get(project_id, {})
        geometry = normalize_card_layout({project_id: {**fallback, **saved}})
        return geometry.get(project_id, dict(fallback))

    def _flush_card_state(self, generation: int) -> None:
        with self._card_state_lock:
            if generation != self._card_save_generation:
                return
            self._card_save_timer = None
            save_state(self.state)

    def _queue_card_state_save(self) -> None:
        with self._card_state_lock:
            if self._card_save_timer is not None:
                self._card_save_timer.cancel()
            self._card_save_generation += 1
            generation = self._card_save_generation
            timer = threading.Timer(0.18, self._flush_card_state, args=(generation,))
            timer.daemon = True
            self._card_save_timer = timer
            timer.start()

    def update_card_geometry(self, project_id: str, **changes: int) -> None:
        if self._stop.is_set() or project_id not in CARD_PROJECT_IDS:
            return
        with self._card_state_lock:
            layout = dict(self.state.card_layout or {})
            layout[project_id] = {**self.card_geometry(project_id), **changes}
            self.state.card_layout = normalize_card_layout(layout)
            self.state.card_layout_version = CARD_LAYOUT_VERSION
        self._queue_card_state_save()

    def _card_geometry_events_enabled(self, project_id: str) -> bool:
        with self._card_state_lock:
            return (
                project_id in self._card_geometry_armed
                and self._card_geometry_suppression.get(project_id, 0) == 0
            )

    def _apply_card_geometry(self, project_id: str, geometry: dict[str, int]) -> bool:
        window = self.card_windows.get(project_id)
        if window is None:
            return False
        from personal_mcp_gateway.desktop import shell

        with self._card_state_lock:
            self._card_geometry_suppression[project_id] = (
                self._card_geometry_suppression.get(project_id, 0) + 1
            )
        try:
            return shell.set_native_window_geometry(
                window,
                geometry["x"],
                geometry["y"],
                geometry["w"],
                geometry["h"],
            )
        finally:
            with self._card_state_lock:
                remaining = self._card_geometry_suppression.get(project_id, 1) - 1
                if remaining > 0:
                    self._card_geometry_suppression[project_id] = remaining
                else:
                    self._card_geometry_suppression.pop(project_id, None)

    def initialize_card_window(self, project_id: str) -> bool:
        """Prepare and replay one hidden card before accepting geometry callbacks."""
        window = self.card_windows.get(project_id)
        if project_id not in CARD_PROJECT_IDS or window is None:
            return False
        with self._card_state_lock:
            if project_id in self._card_geometry_armed:
                return True

        from personal_mcp_gateway.desktop import shell

        # Both operations happen while the pywebview surface is still hidden.
        shell.enable_native_transparency(window)
        shell.enable_native_resize(window)
        replayed = self._apply_card_geometry(project_id, self.card_geometry(project_id))
        if not replayed:
            return False
        with self._card_state_lock:
            self._card_geometry_armed.add(project_id)
        return True

    def register_card_window(self, project_id: str, window: Any) -> None:
        self.card_windows[project_id] = window
        with self._card_state_lock:
            self._card_geometry_armed.discard(project_id)
            self._card_geometry_suppression.pop(project_id, None)
        events = getattr(window, "events", None)
        if events is None:
            return

        def remember_move(x: int, y: int) -> None:
            if not self._card_geometry_events_enabled(project_id):
                return
            self.update_card_geometry(project_id, x=int(x), y=int(y))

        def remember_size(width: int, height: int) -> None:
            if not self._card_geometry_events_enabled(project_id):
                return
            self.update_card_geometry(project_id, w=int(width), h=int(height))

        events.moved += remember_move
        events.resized += remember_size

    def card_is_visible(self, project_id: str) -> bool:
        return project_id in CARD_PROJECT_IDS and bool(
            self.state.card_visibility.get(project_id, False)
        )

    def set_card_visible(self, project_id: str, visible: bool) -> bool:
        if project_id not in CARD_PROJECT_IDS:
            return False
        with self._card_state_lock:
            self.state.card_visibility = {
                **self.state.card_visibility,
                project_id: bool(visible),
            }
            save_state(self.state)

        window = self.card_windows.get(project_id)
        if window is None:
            return True
        if visible:
            return self._restore_card_window(project_id)
        from personal_mcp_gateway.desktop import shell

        try:
            window.hide()
            return shell.set_native_desktop_widget_mode(window, False)
        except Exception:
            return False

    def _restore_card_window(self, project_id: str) -> bool:
        window = self.card_windows.get(project_id)
        if window is None:
            return False
        if not self.card_is_visible(project_id):
            try:
                window.hide()
            except Exception:
                return False
            return True
        from personal_mcp_gateway.desktop import shell

        try:
            initialized = self.initialize_card_window(project_id)
            styled = shell.set_native_desktop_widget_mode(window, True)
            shown = shell.show_native_window_without_activation(window)
            return initialized and styled and shown
        except Exception:
            return False

    def set_card_size(self, project_id: str, preset: str) -> bool:
        scale = CARD_SIZE_SCALES.get(preset)
        window = self.card_windows.get(project_id)
        if scale is None or window is None:
            return False
        base = DEFAULT_CARD_LAYOUT[project_id]
        width = max(CARD_MIN_SIZE[0], round(base["w"] * scale))
        height = max(CARD_MIN_SIZE[1], round(base["h"] * scale))
        self.update_card_geometry(project_id, w=width, h=height)
        try:
            return self._apply_card_geometry(project_id, self.card_geometry(project_id))
        except Exception:
            return False

    def reset_card_geometry(self, project_id: str) -> bool:
        window = self.card_windows.get(project_id)
        if project_id not in CARD_PROJECT_IDS or window is None:
            return False
        geometry = dict(DEFAULT_CARD_LAYOUT[project_id])
        with self._card_state_lock:
            layout = dict(self.state.card_layout or {})
            layout.pop(project_id, None)
            self.state.card_layout = normalize_card_layout(layout) or None
            save_state(self.state)
        try:
            return self._apply_card_geometry(project_id, geometry)
        except Exception:
            return False

    def reset_card_layout(self) -> None:
        """Restore every card's saved state and native bounds to defaults."""
        with self._card_state_lock:
            self.state.card_layout = None
            self.state.card_layout_version = CARD_LAYOUT_VERSION
            save_state(self.state)
        for project_id in self.card_windows:
            try:
                self._apply_card_geometry(project_id, self.card_geometry(project_id))
            except Exception:
                pass

    def show_card_windows(self) -> bool:
        if len(self.card_windows) != len(CARD_PROJECT_IDS):
            return False

        succeeded = True
        ordered = sorted(
            self.card_windows.items(),
            key=lambda item: self.card_geometry(item[0]).get("order", 0),
        )
        for project_id, _window in ordered:
            try:
                if not self.card_is_visible(project_id):
                    continue
                if not self._restore_card_window(project_id):
                    succeeded = False
            except Exception:
                succeeded = False
        return succeeded

    def replay_card_visibility_after_startup(self) -> None:
        """Outlast WebView2's late hidden-state realization without blocking UI."""

        for delay in (0.35, 0.9, 1.8):
            if self._stop.wait(delay):
                return
            self.show_card_windows()

    def hide_card_windows(self) -> None:
        from personal_mcp_gateway.desktop import shell

        for window in self.card_windows.values():
            try:
                window.hide()
                shell.set_native_desktop_widget_mode(window, False)
            except Exception:
                pass

    def set_all_cards_visible(self, visible: bool) -> bool:
        """Show or hide every independent card without touching management."""

        enabled = bool(visible)
        with self._card_state_lock:
            self.state.card_visibility = {
                project_id: enabled for project_id in CARD_PROJECT_IDS
            }
            save_state(self.state)
        if enabled:
            return self.show_card_windows()
        self.hide_card_windows()
        return True

    def hide_management_window(self) -> None:
        """Hide only the management surface; desktop cards stay untouched."""
        window = self.window
        if window is None:
            return
        try:
            window.hide()
        except Exception:
            pass

    def set_management_activation(self, enabled: bool) -> None:
        window = self.window
        if window is None:
            return
        from personal_mcp_gateway.desktop import shell

        try:
            window.focus = bool(enabled)
            shell.set_native_window_activation(window, bool(enabled))
        except Exception:
            pass

    def activate_from_shortcut(self) -> None:
        """An explicit Desktop/Start Menu launch always opens management."""

        self.show_management_window()

    def start_activation_listener(self, event_handle: int | None) -> None:
        if event_handle is None or self._activation_thread is not None:
            return
        self._activation_event = event_handle

        def listen() -> None:
            from personal_mcp_gateway.desktop import shell

            while shell.wait_for_activation_event(event_handle):
                if self._stop.is_set():
                    return
                self.activate_from_shortcut()

        thread = threading.Thread(target=listen, name="poyi-activate", daemon=True)
        self._activation_thread = thread
        thread.start()

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

    def show_management_window(self) -> None:
        """Open and focus management without changing any desktop card."""

        window = self.window
        if window is None:
            return
        from personal_mcp_gateway.desktop import shell

        try:
            self.set_management_activation(True)
            window.show()
            window.restore()
            # WinForms can rebuild a FormBorderStyle.None window's GWL_STYLE
            # during Show/Restore and drop WS_THICKFRAME while leaving our
            # subclass installed. Reassert the resize contract afterwards.
            shell.enable_native_resize(window)
        except Exception:
            pass

    def show_window(self) -> None:
        """Backward-compatible alias for opening the management window."""

        self.show_management_window()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        activation_event = self._activation_event
        activation_thread = self._activation_thread
        self._activation_event = None
        self._activation_thread = None
        if activation_event is not None:
            from personal_mcp_gateway.desktop import shell

            shell.wake_activation_event(activation_event)
            if (
                activation_thread is not None
                and activation_thread is not threading.current_thread()
            ):
                activation_thread.join(timeout=1.0)
            shell.close_activation_event(activation_event)
        self.remember_geometry()
        with self._card_state_lock:
            self._card_save_generation += 1
            if self._card_save_timer is not None:
                self._card_save_timer.cancel()
                self._card_save_timer = None
            save_state(self.state)
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        window = self.window
        for card_window in self.card_windows.values():
            try:
                card_window.destroy()
            except Exception:
                pass
        self.card_windows.clear()
        self._card_geometry_armed.clear()
        self._card_geometry_suppression.clear()
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
        return self._controller.snapshot_payload()

    def refresh(self) -> dict[str, Any]:
        self._controller.refresh_now()
        return self.snapshot()

    def set_compact(self, compact: bool) -> dict[str, Any]:
        controller = self._controller
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
        """Compatibility alias for the former global desktop-mode switch."""

        return self.set_all_cards_visible(bool(desktop_mode))

    def set_all_cards_visible(self, visible: bool) -> dict[str, Any]:
        self._controller.set_all_cards_visible(bool(visible))
        return self.snapshot()

    def set_desktop_regions(
        self,
        regions: list[dict[str, Any]],
        full_window: bool = False,
    ) -> dict[str, bool]:
        controller = self._controller
        # Kept as a no-op for one-version compatibility with an already-cached
        # page. Independent native cards no longer use shared-window regions.
        _ = (controller, regions, full_window)
        return {"ok": False}

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

    def reset_card_layout(self) -> dict[str, Any]:
        controller = self._controller
        controller.reset_card_layout()
        return self.snapshot()

    def set_card_visible(self, project_id: str, visible: bool) -> dict[str, Any]:
        self._controller.set_card_visible(project_id, bool(visible))
        return self.snapshot()

    def show_all_cards(self) -> dict[str, Any]:
        return self.set_all_cards_visible(True)

    def hide_all_cards(self) -> dict[str, Any]:
        return self.set_all_cards_visible(False)

    def open_management(self) -> dict[str, Any]:
        self._controller.show_management_window()
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
        return {"ok": True, "message": "已提交自动修复, 后台处理不会卡住面板"}

    def quit(self) -> None:
        self._controller.shutdown()


class CardApi:
    """Small per-window bridge; the card identity never comes from page input."""

    def __init__(self, controller: DesktopController, project_id: str) -> None:
        self._controller = controller
        self._project_id = project_id

    def snapshot(self) -> dict[str, Any]:
        return self._controller.snapshot_payload(self._project_id)

    def refresh(self) -> dict[str, Any]:
        self._controller.refresh_now()
        return self.snapshot()

    def hide_to_tray(self) -> None:
        self._controller.set_card_visible(self._project_id, False)

    def hide_card(self) -> dict[str, bool]:
        return {"ok": self._controller.set_card_visible(self._project_id, False)}

    def open_management(self) -> dict[str, Any]:
        self._controller.show_management_window()
        return self.snapshot()

    def set_size(self, preset: str) -> dict[str, bool]:
        return {"ok": self._controller.set_card_size(self._project_id, preset)}

    def reset_geometry(self) -> dict[str, bool]:
        return {"ok": self._controller.reset_card_geometry(self._project_id)}

    def quit(self) -> None:
        self._controller.shutdown()


def tray_actions(controller: DesktopController, api: DesktopApi) -> dict[str, Any]:
    def toggle_card(project_id: str) -> dict[str, Any]:
        return api.set_card_visible(project_id, not controller.card_is_visible(project_id))

    return {
        "show_management": controller.activate_from_shortcut,
        "refresh": controller.refresh_now,
        "cards": CARD_TITLES,
        "toggle_card": toggle_card,
        "show_all_cards": api.show_all_cards,
        "hide_all_cards": api.hide_all_cards,
        "reset_cards": api.reset_card_layout,
        "quit": controller.shutdown,
    }


def main() -> int:
    background_launch = "--background" in sys.argv[1:]
    guard = acquire_single_instance()
    if guard is None:
        if not background_launch and not show_existing_instance():
            print("Poyi Control Center 已在运行: 请查看系统托盘", file=sys.stderr)
        return 0
    try:
        from personal_mcp_gateway.desktop import shell
    except ImportError:
        print("缺少桌面依赖: 请先安装 personal-mcp-gateway[desktop]", file=sys.stderr)
        return 2

    controller = DesktopController()
    activation_event = shell.create_activation_event()
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
        on_top=state.on_top,
        background=BACKGROUND_DARK if state.theme == "dark" else BACKGROUND_LIGHT,
        transparent=False,
        hidden=True,
        focus=False,
    )
    if window is None:
        if activation_event is not None:
            shell.close_activation_event(activation_event)
        print("无法创建桌面窗口: 请确认已安装 WebView2 运行时", file=sys.stderr)
        return 3
    controller.window = window

    def prepare_native_window() -> None:
        shell.enable_native_transparency(window)
        shell.enable_native_resize(window)

    window.events.before_show += prepare_native_window

    for project_id in CARD_PROJECT_IDS:
        geometry = controller.card_geometry(project_id)
        card_window = shell.create_window(
            page=build_card_page(project_id),
            js_api=CardApi(controller, project_id),
            width=geometry["w"],
            height=geometry["h"],
            x=geometry["x"],
            y=geometry["y"],
            min_size=CARD_MIN_SIZE,
            on_top=False,
            background=BACKGROUND_DARK,
            title=f"Poyi Card - {CARD_TITLES[project_id]}",
            transparent=False,
            hidden=True,
            focus=False,
            easy_drag=False,
            shadow=False,
        )
        if card_window is None:
            print(f"无法创建桌面磁贴窗口: {project_id}", file=sys.stderr)
            continue
        controller.register_card_window(project_id, card_window)

    def on_start() -> None:
        controller.start_activation_listener(activation_event)
        threading.Thread(target=controller.poll_forever, name="poyi-poll", daemon=True).start()
        # All top-level surfaces were created hidden. Finish their native setup
        # before the first display so neither WebView2 nor SetWindowPos can flash
        # an opaque or default-sized card.
        prepare_native_window()
        # Restore each saved card independently.  An explicit launch also opens
        # management; an autostart/background launch leaves management hidden.
        controller.show_card_windows()
        threading.Thread(
            target=controller.replay_card_visibility_after_startup,
            name="poyi-card-restore",
            daemon=True,
        ).start()
        if not background_launch:
            controller.activate_from_shortcut()
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
