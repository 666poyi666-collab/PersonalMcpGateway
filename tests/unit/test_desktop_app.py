from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from personal_mcp_gateway.desktop.app import (
    DesktopApi,
    DesktopController,
    acquire_single_instance,
    tray_actions,
)
from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_ONLINE,
    DesktopSnapshot,
)
from personal_mcp_gateway.desktop.window_state import COMPACT_SIZE, load_state, state_path


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
        "theme": "dark",
        "adminUrl": "http://127.0.0.1:8761",
    }


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


def test_an_unknown_theme_falls_back_to_dark() -> None:
    api = DesktopApi(_controller())
    assert api.set_theme("light")["view"]["theme"] == "light"
    assert api.set_theme("neon")["view"]["theme"] == "dark"


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


def test_tray_actions_toggle_the_current_view_state() -> None:
    controller = _controller()
    controller.window = FakeWindow()
    actions = tray_actions(controller, DesktopApi(controller))
    assert set(actions) == {"show", "compact", "on_top", "web", "refresh", "quit"}

    actions["compact"]()
    assert controller.state.compact is True
    actions["on_top"]()
    assert controller.state.on_top is True
    actions["show"]()
    assert "show" in controller.window.events


def test_a_second_instance_is_refused() -> None:
    acquire_single_instance()
    assert acquire_single_instance() is None
