from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personal_mcp_gateway.desktop import capture as capture_module
from personal_mcp_gateway.desktop.app import (
    CARD_PROJECT_IDS,
    CardApi,
    DesktopApi,
    DesktopController,
    acquire_single_instance,
    main,
    show_existing_instance,
    tray_actions,
)
from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_ONLINE,
    DesktopSnapshot,
)
from personal_mcp_gateway.desktop.window_state import (
    CARD_LAYOUT_VERSION,
    COMPACT_SIZE,
    PROJECT_LAYOUT_VERSION,
    load_state,
    save_state,
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
        self.focus = True
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


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> FakeEvent:
        self.handlers.append(handler)
        return self

    def fire(self, *args: int) -> None:
        for handler in self.handlers:
            handler(*args)


class FakeCardWindow:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.events = SimpleNamespace(
            before_show=FakeEvent(),
            shown=FakeEvent(),
            moved=FakeEvent(),
            resized=FakeEvent(),
        )

    def show(self) -> None:
        self.actions.append("show")

    def restore(self) -> None:
        self.actions.append("restore")

    def hide(self) -> None:
        self.actions.append("hide")

    def destroy(self) -> None:
        self.actions.append("destroy")

    def move(self, x: int, y: int) -> None:
        self.actions.append(f"move:{x}:{y}")

    def resize(self, width: int, height: int) -> None:
        self.actions.append(f"resize:{width}:{height}")


class FailingShowCardWindow(FakeCardWindow):
    def show(self) -> None:
        self.actions.append("show")
        raise RuntimeError("card native host is not ready")


@pytest.fixture(autouse=True)
def _isolated_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the real ``%LOCALAPPDATA%`` profile."""
    monkeypatch.setenv("PERSONAL_MCP_DESKTOP_HOME", str(tmp_path / "desktop-home"))

    def set_geometry(
        window: FakeCardWindow,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        window.actions.append(f"native-geometry:{x}:{y}:{width}:{height}")
        # Win32 geometry changes synchronously produce the same pywebview events
        # as a user drag. Tests exercise the controller's suppression boundary.
        window.events.moved.fire(x, y)
        window.events.resized.fire(width, height)
        return True

    def show_without_activation(window: FakeCardWindow) -> bool:
        window.actions.append("native-show-no-activate")
        return True

    def accept_window(_window: Any) -> bool:
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.enable_native_transparency",
        accept_window,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.enable_native_resize",
        accept_window,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.set_native_window_geometry",
        set_geometry,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.show_native_window_without_activation",
        show_without_activation,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.set_native_desktop_widget_mode",
        _accept_native_mode,
    )


def _controller(client: FakeClient | None = None) -> DesktopController:
    return DesktopController(client=client or FakeClient())  # pyright: ignore[reportArgumentType]


def _accept_native_mode(_window: Any, _enabled: bool) -> bool:
    return True


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


def test_refresh_now_wakes_the_background_poller_without_fetching_on_the_ui_thread() -> None:
    client = FakeClient()
    controller = _controller(client)

    controller.refresh_now()

    assert client.calls == []
    assert controller._wake.is_set()  # pyright: ignore[reportPrivateUsage]


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


def test_showing_management_preserves_independent_card_visibility() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    controller.state.card_visibility["watch"] = True

    controller.show_window()

    assert window.events == ["show", "restore"]
    assert controller.state.card_visibility["watch"] is True


def test_showing_management_reasserts_native_resize_after_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence: list[str] = []

    class OrderedWindow(FakeWindow):
        def show(self) -> None:
            sequence.append("show")

        def restore(self) -> None:
            sequence.append("restore")

    controller = _controller()
    window = OrderedWindow()
    controller.window = window

    def enable_resize(target: Any) -> bool:
        assert target is window
        sequence.append("resize")
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.enable_native_resize",
        enable_resize,
    )

    controller.show_management_window()

    assert sequence == ["show", "restore", "resize"]


def test_showing_one_card_does_not_hide_or_reconfigure_management() -> None:
    controller = _controller()
    management = FakeWindow()
    card = FakeCardWindow()
    controller.window = management
    controller.card_windows["watch"] = card

    assert controller.set_card_visible("watch", True) is True

    assert management.events == []
    assert management.focus is True
    assert card.actions[-1] == "native-show-no-activate"


def test_snapshot_payload_carries_the_view_preferences() -> None:
    controller = _controller()
    payload = DesktopApi(controller).snapshot()
    assert payload["status"] == STATUS_DISCONNECTED
    assert payload["view"] == {
        "compact": False,
        "onTop": False,
        "desktopMode": False,
        "cardVisibility": {
            "foxlink": False,
            "watch": False,
            "journal": False,
            "personal": False,
            "bzsjk": False,
        },
        "visibleCardCount": 0,
        "theme": "light",
        "adminUrl": "http://127.0.0.1:8761",
        "projectLayoutVersion": PROJECT_LAYOUT_VERSION,
        "projectLayout": {},
        "cardLayoutVersion": CARD_LAYOUT_VERSION,
        "cardLayout": {},
        "cardMode": False,
        "cardId": None,
        "hiddenCards": ["foxlink", "watch", "journal", "personal", "bzsjk"],
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


def test_card_visibility_does_not_lock_management_compact_or_on_top() -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    controller.card_windows = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.state.compact = True
    controller.state.on_top = True
    controller.state.width = window.width
    controller.state.height = window.height
    api = DesktopApi(controller)

    payload = api.set_all_cards_visible(True)

    assert payload["view"]["desktopMode"] is False
    assert payload["view"]["visibleCardCount"] == len(CARD_PROJECT_IDS)
    assert payload["view"]["compact"] is True
    assert payload["view"]["onTop"] is True
    assert window.resized == []
    assert window.on_top is False

    api.set_compact(False)
    api.set_on_top(False)
    assert controller.state.compact is False
    assert controller.state.on_top is False
    assert all(load_state(state_path()).card_visibility.values())


def test_legacy_desktop_regions_bridge_is_always_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    window = FakeWindow()
    controller.window = window
    calls: list[tuple[Any, object, bool]] = []

    def set_regions(target: Any, regions: object, full_window: bool) -> bool:
        calls.append((target, regions, full_window))
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.set_native_desktop_regions",
        set_regions,
    )
    api = DesktopApi(controller)
    assert api.set_desktop_regions([], True) == {"ok": False}
    api.set_all_cards_visible(True)
    regions = [{"x": 1, "y": 2, "width": 30, "height": 40, "radius": 8}]
    assert api.set_desktop_regions(regions) == {"ok": False}
    assert calls == []


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


def test_card_api_binds_one_window_to_one_project_identity() -> None:
    controller = _controller()

    view = CardApi(controller, "journal").snapshot()["view"]

    assert view["cardMode"] is True
    assert view["cardId"] == "journal"


def test_card_move_and_resize_persist_without_touching_board_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    controller.state.project_layout = {"watch": {"x": 0, "y": 0, "w": 500, "h": 500, "order": 0}}
    card = FakeCardWindow()
    monkeypatch.setattr(
        controller,
        "_queue_card_state_save",
        lambda: save_state(controller.state),
    )
    controller.register_card_window("watch", card)

    # Realization can emit default WinForms bounds. Those are ignored until the
    # saved native geometry has been replayed and the card is explicitly armed.
    card.events.moved.fire(840, 56)
    card.events.resized.fire(510, 330)
    assert controller.state.card_layout is None

    assert controller.initialize_card_window("watch") is True
    assert controller.state.card_layout is None

    card.events.moved.fire(840, 56)
    card.events.resized.fire(510, 330)

    persisted = load_state(state_path())
    assert persisted.card_layout is not None
    assert persisted.card_layout["watch"] == {
        "x": 840,
        "y": 56,
        "w": 510,
        "h": 330,
        "order": 1,
    }
    assert persisted.project_layout == controller.state.project_layout


def test_startup_replay_retries_persisted_card_visibility_without_blocking_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    calls: list[bool] = []

    def no_wait(_delay: float | None = None) -> bool:
        return False

    def record_show() -> bool:
        calls.append(True)
        return True

    monkeypatch.setattr(
        controller._stop,  # pyright: ignore[reportPrivateUsage]
        "wait",
        no_wait,
    )
    monkeypatch.setattr(
        controller,
        "show_card_windows",
        record_show,
    )

    controller.replay_card_visibility_after_startup()

    assert calls == [True, True, True]


def test_stale_geometry_debounce_cannot_clear_the_new_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    timers: list[Any] = []

    class FakeTimer:
        def __init__(self, _delay: float, callback: Any, args: tuple[Any, ...]) -> None:
            self.callback = callback
            self.args = args
            self.cancelled = False
            self.daemon = False
            timers.append(self)

        def start(self) -> None:
            return None

        def cancel(self) -> None:
            self.cancelled = True

        def fire(self) -> None:
            self.callback(*self.args)

    monkeypatch.setattr("personal_mcp_gateway.desktop.app.threading.Timer", FakeTimer)

    controller.update_card_geometry("watch", x=100)
    first = timers[-1]
    controller.update_card_geometry("watch", x=200)
    second = timers[-1]

    assert first.cancelled is True
    first.fire()
    assert controller._card_save_timer is second  # pyright: ignore[reportPrivateUsage]
    second.fire()
    assert controller._card_save_timer is None  # pyright: ignore[reportPrivateUsage]
    persisted = load_state(state_path())
    assert persisted.card_layout is not None
    assert persisted.card_layout["watch"]["x"] == 200


def test_all_cards_can_be_shown_and_hidden_without_touching_management() -> None:
    controller = _controller()
    management = FakeWindow()
    controller.window = management
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards
    api = DesktopApi(controller)

    enabled = api.set_all_cards_visible(True)["view"]

    assert enabled["desktopMode"] is False
    assert enabled["visibleCardCount"] == len(CARD_PROJECT_IDS)
    assert all(enabled["cardVisibility"].values())
    assert management.events == []
    assert management.focus is True
    assert all(card.actions[-1] == "native-show-no-activate" for card in cards.values())
    assert all(
        any(action.startswith("native-geometry:") for action in card.actions)
        for card in cards.values()
    )
    assert all(
        "show" not in card.actions and "restore" not in card.actions for card in cards.values()
    )

    disabled = api.set_all_cards_visible(False)["view"]

    assert disabled["desktopMode"] is False
    assert disabled["visibleCardCount"] == 0
    assert not any(disabled["cardVisibility"].values())
    assert management.events == []
    assert management.focus is True
    assert all(card.actions[-1] == "hide" for card in cards.values())


def test_a_native_card_show_failure_does_not_touch_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    management = FakeWindow()
    controller.window = management
    cards: dict[str, FakeCardWindow] = {
        project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS
    }
    cards["journal"] = FailingShowCardWindow()
    controller.card_windows = cards

    def show_without_activation(window: FakeCardWindow) -> bool:
        window.actions.append("native-show-no-activate")
        return window is not cards["journal"]

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.show_native_window_without_activation",
        show_without_activation,
    )

    view = DesktopApi(controller).set_all_cards_visible(True)["view"]

    assert view["desktopMode"] is False
    assert view["visibleCardCount"] == len(CARD_PROJECT_IDS)
    assert management.events == []
    assert cards["journal"].actions[-1] == "native-show-no-activate"
    assert all(
        card.actions[-1] == "native-show-no-activate"
        for project_id, card in cards.items()
        if project_id != "journal"
    )


def test_a_card_visibility_failure_still_leaves_management_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    management = FakeWindow()
    controller.window = management
    card = FailingShowCardWindow()
    controller.card_windows["journal"] = card

    def reject_show(_window: Any) -> bool:
        return False

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.show_native_window_without_activation",
        reject_show,
    )

    assert controller.set_card_visible("journal", True) is False
    assert controller.state.card_visibility["journal"] is True
    assert load_state(state_path()).card_visibility["journal"] is True
    assert management.events == []


def test_one_card_can_be_hidden_and_restored_without_touching_its_peers() -> None:
    controller = _controller()
    controller.state.card_visibility = {project_id: True for project_id in CARD_PROJECT_IDS}
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards

    assert controller.set_card_visible("journal", False) is True

    assert cards["journal"].actions == ["hide"]
    assert all(not card.actions for key, card in cards.items() if key != "journal")
    assert load_state(state_path()).card_visibility["journal"] is False
    assert all(
        visible
        for project_id, visible in load_state(state_path()).card_visibility.items()
        if project_id != "journal"
    )

    assert controller.set_card_visible("journal", True) is True
    assert cards["journal"].actions[-1] == "native-show-no-activate"
    assert "show" not in cards["journal"].actions
    assert "restore" not in cards["journal"].actions
    assert all(load_state(state_path()).card_visibility.values())


def test_shortcut_activation_opens_management_without_restoring_hidden_cards() -> None:
    controller = _controller()
    controller.window = FakeWindow()
    controller.state.card_visibility = {project_id: False for project_id in CARD_PROJECT_IDS}
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards

    controller.activate_from_shortcut()

    assert not any(controller.state.card_visibility.values())
    assert all(not card.actions for card in cards.values())
    assert controller.window.events == ["show", "restore"]


def test_shortcut_activation_preserves_mixed_visibility_without_touching_cards() -> None:
    controller = _controller()
    controller.window = FakeWindow()
    controller.state.card_visibility = {
        project_id: project_id != "journal" for project_id in CARD_PROJECT_IDS
    }
    expected = dict(controller.state.card_visibility)
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards

    controller.activate_from_shortcut()

    assert controller.state.card_visibility == expected
    assert all(not card.actions for card in cards.values())
    assert controller.window.events == ["show", "restore"]


def test_card_size_presets_and_reset_affect_only_the_selected_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards
    monkeypatch.setattr(
        controller,
        "_queue_card_state_save",
        lambda: save_state(controller.state),
    )

    assert controller.set_card_size("watch", "small") is True
    assert cards["watch"].actions == ["native-geometry:370:48:310:223"]
    assert all(not card.actions for key, card in cards.items() if key != "watch")
    assert controller.state.card_layout is not None
    assert controller.state.card_layout["watch"]["w"] == 310
    assert controller.state.card_layout["watch"]["h"] == 223

    assert controller.reset_card_geometry("watch") is True
    assert cards["watch"].actions[-1] == "native-geometry:370:48:430:310"
    assert controller.state.card_layout is None


def test_programmatic_card_geometry_suppresses_move_and_resize_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    card = FakeCardWindow()
    controller.register_card_window("watch", card)
    assert controller.initialize_card_window("watch") is True
    monkeypatch.setattr(controller, "_queue_card_state_save", lambda: None)

    recorded: list[dict[str, int]] = []
    update = controller.update_card_geometry

    def record_update(project_id: str, **changes: int) -> None:
        recorded.append(changes)
        update(project_id, **changes)

    monkeypatch.setattr(controller, "update_card_geometry", record_update)

    assert controller.set_card_size("watch", "small") is True
    assert recorded == [{"w": 310, "h": 223}]

    assert controller.reset_card_geometry("watch") is True
    # The fake native call emits moved/resized events for both operations. Only
    # the controller's explicit size state change may reach the persistence path.
    assert recorded == [{"w": 310, "h": 223}]


def test_card_bridge_exposes_hide_size_and_reset_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    card = FakeCardWindow()
    controller.card_windows["foxlink"] = card
    monkeypatch.setattr(
        controller,
        "_queue_card_state_save",
        lambda: save_state(controller.state),
    )
    api = CardApi(controller, "foxlink")

    assert api.set_size("large") == {"ok": True}
    assert card.actions[-1] == "native-geometry:28:48:432:364"
    assert api.reset_geometry() == {"ok": True}
    assert api.hide_card() == {"ok": True}
    assert controller.state.card_visibility["foxlink"] is False
    assert load_state(state_path()).card_visibility["foxlink"] is False


def test_card_hide_to_tray_hides_only_its_own_window() -> None:
    controller = _controller()
    controller.state.card_visibility = {project_id: True for project_id in CARD_PROJECT_IDS}
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards

    CardApi(controller, "journal").hide_to_tray()

    assert cards["journal"].actions == ["hide"]
    assert controller.state.card_visibility["journal"] is False
    assert all(not card.actions for key, card in cards.items() if key != "journal")
    assert all(
        visible
        for project_id, visible in controller.state.card_visibility.items()
        if project_id != "journal"
    )


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


def test_tray_actions_control_management_and_cards_independently() -> None:
    controller = _controller()
    controller.window = FakeWindow()
    cards = {project_id: FakeCardWindow() for project_id in CARD_PROJECT_IDS}
    controller.card_windows = cards
    actions = tray_actions(controller, DesktopApi(controller))
    assert set(actions) == {
        "show_management",
        "refresh",
        "cards",
        "toggle_card",
        "show_all_cards",
        "hide_all_cards",
        "reset_cards",
        "quit",
    }

    actions["toggle_card"]("journal")
    assert controller.state.card_visibility["journal"] is True
    assert cards["journal"].actions[-1] == "native-show-no-activate"
    assert all(not card.actions for key, card in cards.items() if key != "journal")

    actions["show_management"]()
    assert controller.window.events == ["show", "restore"]
    assert controller.state.card_visibility["journal"] is True

    actions["show_all_cards"]()
    assert all(controller.state.card_visibility.values())
    actions["hide_all_cards"]()
    assert not any(controller.state.card_visibility.values())
    assert controller.window.events == ["show", "restore"]


def test_a_second_instance_is_refused() -> None:
    acquire_single_instance()
    assert acquire_single_instance() is None


def test_existing_instance_uses_only_the_controller_activation_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.signal_activation_event",
        lambda: True,
    )

    assert show_existing_instance() is True


def test_existing_instance_never_directly_reveals_the_management_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def skip_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.signal_activation_event",
        lambda: False,
    )
    monkeypatch.setattr("personal_mcp_gateway.desktop.app.time.sleep", skip_sleep)

    assert show_existing_instance() is False


def test_existing_instance_retries_the_activation_event_during_cold_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter((False, False, True))
    sleeps: list[float] = []

    def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.signal_activation_event",
        lambda: next(results),
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.time.sleep",
        record_sleep,
    )

    assert show_existing_instance() is True
    assert sleeps == [0.05, 0.05]


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


def test_a_background_second_launch_does_not_reveal_the_existing_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr("personal_mcp_gateway.desktop.app.sys.argv", ["app", "--background"])
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.acquire_single_instance",
        lambda: None,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.show_existing_instance",
        lambda: calls.append(True) or True,
    )

    assert main() == 0
    assert calls == []


@pytest.mark.parametrize("background_launch", [False, True])
def test_startup_restores_saved_cards_and_only_explicit_launch_opens_management(
    monkeypatch: pytest.MonkeyPatch,
    background_launch: bool,
) -> None:
    state = load_state()
    state.card_visibility["watch"] = True
    save_state(state)
    created: list[dict[str, Any]] = []
    windows: list[FakeCardWindow] = []

    def create_window(**options: Any) -> FakeCardWindow:
        created.append(options)
        window = FakeCardWindow()
        windows.append(window)
        return window

    def run_window(on_start: Any, _storage: Path) -> None:
        on_start()

    def skip_poll(_controller: DesktopController) -> None:
        return None

    def build_icon(_controller: DesktopController, _actions: dict[str, Any]) -> FakeIcon:
        return FakeIcon()

    def skip_tray(_icon: Any) -> None:
        return None

    argv = ["app", "--background"] if background_launch else ["app"]
    monkeypatch.setattr("personal_mcp_gateway.desktop.app.sys.argv", argv)
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.acquire_single_instance",
        lambda: object(),
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.create_activation_event",
        lambda: None,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.create_window",
        create_window,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.run_window",
        run_window,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.app.DesktopController.poll_forever",
        skip_poll,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.build_tray_icon",
        build_icon,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.shell.start_tray",
        skip_tray,
    )

    assert main() == 0
    assert len(created) == 1 + len(CARD_PROJECT_IDS)
    assert len(windows) == len(created)
    assert all(options["hidden"] is True for options in created)
    assert all(options["transparent"] is False for options in created)
    assert all(options["focus"] is False for options in created)

    management = windows[0]
    if background_launch:
        assert "show" not in management.actions
        assert "restore" not in management.actions
    else:
        assert "show" in management.actions
        assert "restore" in management.actions

    cards = dict(zip(CARD_PROJECT_IDS, windows[1:], strict=True))
    assert "native-show-no-activate" in cards["watch"].actions
    assert all(
        "native-show-no-activate" not in card.actions
        for project_id, card in cards.items()
        if project_id != "watch"
    )
