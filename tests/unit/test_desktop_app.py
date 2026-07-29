from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from personal_mcp_gateway.desktop import capture as capture_module
from personal_mcp_gateway.desktop.app import (
    DesktopApi,
    DesktopController,
    acquire_single_instance,
    main,
    tray_actions,
)
from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_ONLINE,
    DesktopSnapshot,
)
from personal_mcp_gateway.desktop.window_state import (
    COMPACT_SIZE,
    PROJECT_LAYOUT_VERSION,
    load_state,
    state_path,
)


class FakeClient:
    """Stands in for :class:`GatewayClient` without touching the network."""

    def __init__(self, snapshots: list[DesktopSnapshot] | None = None) -> None:
        self.calls: list[bool] = []
        self._snapshots = snapshots or []

    def fetch(self, *, force: bool = False) -> DesktopSnapshot:
        self.calls.append(force)
        if self._snapshots:
            return self._snapshots.pop(0)
        return DesktopSnapshot(connected=True, status=STATUS_ONLINE, fetched_at="t")

    def initial_snapshot(self) -> DesktopSnapshot:
        return DesktopSnapshot(
            connected=False,
            status=STATUS_DISCONNECTED,
            fetched_at="",
        )


class FakeWindow:
    def __init__(self) -> None:
        self.width = 1000
        self.height = 700
        self.x = 40
        self.y = 24
        self.on_top = False
        self.events: list[str] = []
        self.resized: list[tuple[int, int]] = []

    def resize(self, width: int, height: int) -> None:
        self.resized.append((width, height))

    def show(self) -> None:
        self.events.append("show")

    def restore(self) -> None:
        self.events.append("restore")

    def minimize(self) -> None:
        self.events.append("minimize")

    def hide(self) -> None:
        self.events.append("hide")

    def destroy(self) -> None:
        self.events.append("destroy")


class FakeIcon:
    def __init__(self) -> None:
        self.icon: Any = None
        self.title: str = ""
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the real ``%LOCALAPPDATA%`` profile."""
    monkeypatch.setenv("PERSONAL_MCP_DESKTOP_HOME", str(tmp_path / "desktop-home"))


def _controller(client: FakeClient | None = None) -> DesktopController:
    return DesktopController(client=client or FakeClient())  # pyright: ignore[reportArgumentType]


def test_controller_exposes_persisted_snapshot_before_the_first_poll(tmp_path: Path) -> None:
    from personal_mcp_gateway.desktop.client import GatewayClient

    cache = tmp_path / "last-good.json"
    cache.write_text(
        '{"schemaVersion":1,"savedAt":"2026-07-29T00:00:00+00:00",'
        '"data":{"summary":{"total":1,"online":1,"degraded":0,"offline":0},'
        '"targets":[],"widgets":[],"errors":[],"events":[]}}',
        encoding="utf-8",
    )

    controller = DesktopController(client=GatewayClient(cache_path=cache))

    assert controller.current().connected is True
    assert controller.current().stale is True
    assert controller.current().data is not None


def test_apply_updates_the_snapshot_and_the_tray_icon() -> None:
    controller = _controller()
    icon = FakeIcon()
    controller.icon = icon

    controller.apply(DesktopSnapshot(connected=True, status=STATUS_DEGRADED, fetched_at="t"))

    assert controller.current().status == STATUS_DEGRADED
    assert icon.icon is not None
    assert "Poyi Control Center" in icon.title


def test_a_broken_tray_icon_never_breaks_the_poller() -> None:
    class ExplodingIcon(FakeIcon):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "icon" and value is not None:
                raise RuntimeError("tray gone")
            super().__setattr__(name, value)

    controller = _controller()
    controller.icon = ExplodingIcon()

    controller.apply(DesktopSnapshot(connected=True, status=STATUS_ONLINE, fetched_at="t"))

    assert controller.current().status == STATUS_ONLINE


def test_poll_loop_runs_until_shutdown_is_requested() -> None:
    client = FakeClient()
    controller = _controller(client)
    thread = threading.Thread(target=controller.poll_forever, daemon=True)
    thread.start()
    try:
        deadline = threading.Event()
        while not client.calls and not deadline.wait(0.02):
            pass
        assert client.calls  # at least one poll happened
    finally:
        controller.shutdown()
        thread.join(timeout=5)
    assert thread.is_alive() is False


def test_refresh_now_bypasses_the_server_side_cache() -> None:
    client = FakeClient()
    controller = _controller(client)
    controller.refresh_now()
    assert client.calls == [True]


def test_geometry_is_persisted_but_compact_size_is_not() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window

    controller.remember_geometry()
    assert load_state(state_path()).size() == (1000, 700)

    controller.state.compact = True
    window.width, window.height = COMPACT_SIZE
    window.x, window.y = 300, 200
    controller.remember_geometry()

    persisted = load_state(state_path())
    assert persisted.size() == (1000, 700)  # the full-size geometry survives
    assert (persisted.x, persisted.y) == (300, 200)


def test_geometry_survives_a_window_that_cannot_report_its_position() -> None:
    class HeadlessWindow(FakeWindow):
        @property
        def x(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
            raise RuntimeError("no native handle yet")

        @x.setter
        def x(self, value: int) -> None:
            return

    controller = _controller()
    controller.window = HeadlessWindow()
    controller.remember_geometry()  # must not raise
    assert state_path().exists() is False


def test_window_helpers_are_safe_before_a_window_exists() -> None:
    controller = _controller()
    controller.show_window()
    controller.remember_geometry()
    controller.shutdown()  # no window, no icon


def test_shutdown_stops_the_tray_and_destroys_the_window() -> None:
    controller = _controller()
    icon, window = FakeIcon(), FakeWindow()
    controller.icon, controller.window = icon, window

    controller.shutdown()

    assert icon.stopped is True
    assert "destroy" in window.events


def test_snapshot_payload_carries_the_view_preferences() -> None:
    controller = _controller()
    payload = DesktopApi(controller).snapshot()
    assert payload["status"] == STATUS_DISCONNECTED
    assert payload["view"] == {
        "compact": False,
        "onTop": False,
        "desktopMode": False,
        "theme": "light",
        "adminUrl": "http://127.0.0.1:8761",
        "projectLayoutVersion": PROJECT_LAYOUT_VERSION,
        "projectLayout": {},
    }
    assert payload["stale"] is False


def test_compact_mode_resizes_and_restores_the_remembered_size() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    api = DesktopApi(controller)

    api.set_compact(True)
    assert window.resized[-1] == COMPACT_SIZE
    assert load_state(state_path()).compact is True

    api.set_compact(False)
    assert window.resized[-1] == (1000, 700)
    assert load_state(state_path()).compact is False


def test_setting_the_same_compact_value_does_not_resize() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    DesktopApi(controller).set_compact(False)
    assert window.resized == []


def test_always_on_top_reaches_the_window_and_the_saved_state() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window

    DesktopApi(controller).set_on_top(True)

    assert window.on_top is True
    assert load_state(state_path()).on_top is True


def test_desktop_mode_locks_out_conflicting_window_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    controller.state.compact = True
    controller.state.on_top = True
    controller.state.width = window.width
    controller.state.height = window.height
    calls: list[bool] = []

    def set_mode(_window: Any, enabled: bool) -> bool:
        calls.append(enabled)
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.set_native_desktop_mode",
        set_mode,
    )
    api = DesktopApi(controller)

    payload = api.set_desktop_mode(True)

    assert calls == [True]
    assert window.resized[-1] == (1000, 700)
    assert window.on_top is False
    assert payload["view"]["desktopMode"] is True
    assert payload["view"]["compact"] is False
    assert payload["view"]["onTop"] is False
    assert load_state(state_path()).desktop_mode is True

    api.set_compact(True)
    api.set_on_top(True)
    assert controller.state.compact is False
    assert controller.state.on_top is False

    assert api.set_desktop_mode(False)["view"]["desktopMode"] is False
    assert calls == [True, False]


def test_an_unknown_theme_falls_back_to_light() -> None:
    api = DesktopApi(_controller())
    assert api.set_theme("dark")["view"]["theme"] == "dark"
    assert api.set_theme("neon")["view"]["theme"] == "light"


def test_project_layout_is_validated_and_persisted() -> None:
    controller = _controller()
    api = DesktopApi(controller)

    payload = api.set_project_layout({"watch": {"x": 422, "y": 18, "w": 560, "h": 344, "order": 1}})

    assert payload["view"]["projectLayout"]["watch"] == {
        "x": 422,
        "y": 18,
        "w": 560,
        "h": 344,
        "order": 1,
    }
    persisted = load_state(state_path())
    assert payload["view"]["projectLayoutVersion"] == PROJECT_LAYOUT_VERSION
    assert persisted.project_layout == payload["view"]["projectLayout"]
    assert persisted.project_layout_version == PROJECT_LAYOUT_VERSION

    reset = api.reset_project_layout()["view"]
    assert reset["projectLayout"] == {}
    assert reset["projectLayoutVersion"] == PROJECT_LAYOUT_VERSION


def test_capture_uses_the_native_window_without_activating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    target = tmp_path / "board.png"
    captured: list[int] = []

    def handle(_candidate: Any) -> int:
        return 42

    def capture(hwnd: int) -> Path:
        captured.append(hwnd)
        return target

    monkeypatch.setattr(capture_module, "native_handle", handle)
    monkeypatch.setattr(capture_module, "capture_hwnd", capture)

    assert DesktopApi(controller).capture() == {
        "ok": True,
        "message": "截图已保存",
        "path": str(target),
    }
    assert captured == [42]
    assert window.events == []


def test_capture_failure_is_reported_without_breaking_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(_candidate: Any) -> int:
        return 42

    def fail(_hwnd: int) -> Path:
        raise OSError("capture unavailable")

    monkeypatch.setattr(capture_module, "native_handle", handle)
    monkeypatch.setattr(capture_module, "capture_hwnd", fail)

    assert DesktopApi(_controller()).capture() == {"ok": False, "message": "截图失败"}


def test_minimize_and_hide_reach_the_window() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    api = DesktopApi(controller)

    api.minimize()
    api.hide_to_tray()

    assert window.events == ["minimize", "hide"]


def test_bridge_methods_are_safe_without_a_window() -> None:
    api = DesktopApi(_controller())
    api.minimize()
    api.hide_to_tray()
    api.quit()


def test_open_web_targets_the_local_admin_status_page(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def record(url: str, new: int = 0, autoraise: bool = True) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("personal_mcp_gateway.desktop.app.webbrowser.open", record)
    DesktopApi(_controller()).open_web()
    assert opened == ["http://127.0.0.1:8761/admin/status"]


def test_tray_actions_toggle_the_current_view_state(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    controller.window = FakeWindow()

    def set_mode(_window: Any, _enabled: bool) -> bool:
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.set_native_desktop_mode",
        set_mode,
    )
    actions = tray_actions(controller, DesktopApi(controller))
    assert set(actions) == {
        "show",
        "compact",
        "desktop",
        "on_top",
        "web",
        "refresh",
        "quit",
    }

    actions["compact"]()
    assert controller.state.compact is True
    actions["on_top"]()
    assert controller.state.on_top is True
    actions["desktop"]()
    assert controller.state.desktop_mode is True
    assert controller.state.compact is False
    assert controller.state.on_top is False
    actions["show"]()
    assert "show" in controller.window.events


def test_a_second_instance_is_refused() -> None:
    acquire_single_instance()
    assert acquire_single_instance() is None


def test_a_second_launch_reveals_the_existing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.acquire_single_instance",
        lambda: None,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.show_existing_instance",
        lambda: calls.append(True) or True,
    )

    assert main() == 0
    assert calls == [True]
