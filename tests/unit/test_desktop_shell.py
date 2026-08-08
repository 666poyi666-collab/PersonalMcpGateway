from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from personal_mcp_gateway.desktop import shell


def _create_test_window(*, transparent: bool = True) -> Any:
    return shell.create_window(
        page="<html></html>",
        js_api=object(),
        width=900,
        height=640,
        x=10,
        y=20,
        min_size=(420, 320),
        on_top=False,
        background="#102030",
        transparent=transparent,
    )


def test_create_window_requests_a_transparent_webview2_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    sentinel = object()

    def create_window(title: str, **options: object) -> object:
        calls.append((title, options))
        return sentinel

    monkeypatch.delenv("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", raising=False)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window))

    assert _create_test_window() is sentinel
    assert calls[0][0] == "Poyi Control Center"
    assert calls[0][1]["transparent"] is True
    assert calls[0][1]["background_color"] == "#102030"
    assert calls[0][1]["frameless"] is True
    assert calls[0][1]["easy_drag"] is False
    assert calls[0][1]["hidden"] is False
    assert calls[0][1]["focus"] is True
    assert calls[0][1]["shadow"] is True
    assert os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] == "00000000"


def test_card_window_options_keep_each_surface_hidden_until_visibility_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def create_window(title: str, **options: object) -> object:
        calls.append((title, options))
        return object()

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window))

    shell.create_window(
        page="<html></html>",
        js_api=object(),
        width=320,
        height=240,
        x=20,
        y=30,
        min_size=(220, 150),
        on_top=False,
        background="#080B12",
        title="Poyi Card - FocusLink",
        transparent=False,
        hidden=True,
        focus=False,
        easy_drag=False,
        shadow=False,
    )

    assert calls[0][0] == "Poyi Card - FocusLink"
    assert calls[0][1]["hidden"] is True
    assert calls[0][1]["transparent"] is False
    assert calls[0][1]["focus"] is False
    assert calls[0][1]["shadow"] is False
    assert calls[0][1]["min_size"] == (220, 150)


def test_create_window_retries_with_the_themed_background_on_old_pywebview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    sentinel = object()

    def create_window(_title: str, **options: object) -> object:
        calls.append(options)
        if "transparent" in options:
            raise TypeError("unexpected keyword argument 'transparent'")
        return sentinel

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window))

    assert _create_test_window() is sentinel
    assert len(calls) == 2
    assert calls[0]["transparent"] is True
    assert "transparent" not in calls[1]
    assert calls[1]["background_color"] == "#102030"


def test_native_transparency_uses_black_only_after_dwm_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    black = object()
    transparent = object()
    drawing = ModuleType("System.Drawing")
    drawing.Color = SimpleNamespace(Black=black, Transparent=transparent)  # type: ignore[attr-defined]
    webview = SimpleNamespace(DefaultBackgroundColor=object())
    native = SimpleNamespace(BackColor=object(), browser=SimpleNamespace(webview=webview))
    window = SimpleNamespace(native=native)

    def transparent_background_ready(_window: Any) -> bool:
        return True

    monkeypatch.setitem(sys.modules, "System.Drawing", drawing)
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.enable_transparent_background",
        transparent_background_ready,
    )

    assert shell.enable_native_transparency(window) is True
    assert webview.DefaultBackgroundColor is transparent
    assert native.BackColor is black


def test_native_transparency_keeps_the_fallback_background_when_dwm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = object()
    native = SimpleNamespace(BackColor=original)
    window = SimpleNamespace(native=native)

    def transparent_background_unavailable(_window: Any) -> bool:
        return False

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.enable_transparent_background",
        transparent_background_unavailable,
    )

    assert shell.enable_native_transparency(window) is False
    assert native.BackColor is original


def test_native_resize_enters_the_ui_thread_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[Any] = []

    def invoke(callback: Any) -> None:
        callbacks.append(callback)
        callback()

    native = SimpleNamespace(
        InvokeRequired=True,
        Invoke=invoke,
    )
    window = SimpleNamespace(native=native)
    calls: list[tuple[Any, str]] = []

    def make_action(callback: Any) -> Any:
        return callback

    def begin_resize(target: Any, edge: str) -> bool:
        calls.append((target, edge))
        return False

    system = ModuleType("System")
    system.Action = make_action  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.begin_window_resize",
        begin_resize,
    )

    assert shell.begin_native_resize(window, "se") is False
    assert len(callbacks) == 1
    assert calls == [(window, "se")]


def test_installing_native_resize_enters_the_ui_thread_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[Any] = []

    def invoke(callback: Any) -> None:
        callbacks.append(callback)
        callback()

    window = SimpleNamespace(
        native=SimpleNamespace(InvokeRequired=True, Invoke=invoke),
    )
    calls: list[Any] = []
    system = ModuleType("System")
    system.Action = lambda callback: callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "System", system)

    def install(target: Any) -> bool:
        calls.append(target)
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.install_frameless_resize",
        install,
    )

    assert shell.enable_native_resize(window) is True
    assert len(callbacks) == 1
    assert calls == [window]


def test_native_card_lifecycle_wrappers_enter_the_ui_thread_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[Any] = []

    def invoke(callback: Any) -> None:
        callbacks.append(callback)
        callback()

    window = SimpleNamespace(
        native=SimpleNamespace(
            InvokeRequired=True,
            Invoke=invoke,
        )
    )
    calls: list[tuple[object, ...]] = []
    system = ModuleType("System")
    system.Action = lambda callback: callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "System", system)

    def show_without_activation(target: Any) -> bool:
        calls.append(("show", target))
        return True

    def set_geometry(
        target: Any,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        calls.append(("geometry", target, x, y, width, height))
        return True

    def set_activation(target: Any, enabled: bool) -> bool:
        calls.append(("activation", target, enabled))
        return True

    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.show_window_without_activation",
        show_without_activation,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.set_window_geometry",
        set_geometry,
    )
    monkeypatch.setattr(
        "personal_mcp_gateway.desktop.native_window.set_window_activation",
        set_activation,
    )

    assert shell.show_native_window_without_activation(window) is True
    assert shell.set_native_window_geometry(window, 20, 30, 400, 260) is True
    assert shell.set_native_window_activation(window, True) is True
    assert len(callbacks) == 3
    assert calls == [
        ("show", window),
        ("geometry", window, 20, 30, 400, 260),
        ("activation", window, True),
    ]


def test_tray_menu_can_restore_each_independently_hidden_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Menu:
        SEPARATOR = object()

        def __init__(self, *items: object) -> None:
            self.items = list(items)

    class MenuItem:
        def __init__(self, text: str, action: object, **options: object) -> None:
            self.text = text
            self.action = action
            self.options = options

    class Icon:
        def __init__(self, name: str, image: object, title: str, menu: Menu) -> None:
            self.name = name
            self.image = image
            self.title = title
            self.menu = menu

    fake_pystray = SimpleNamespace(Menu=Menu, MenuItem=MenuItem, Icon=Icon)
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    def is_visible(project_id: str) -> bool:
        return project_id != "journal"

    controller = SimpleNamespace(card_is_visible=is_visible)

    def noop(*_args: object) -> None:
        return None

    actions = {
        "show_management": noop,
        "refresh": noop,
        "quit": noop,
        "toggle_card": noop,
        "show_all_cards": noop,
        "hide_all_cards": noop,
        "reset_cards": noop,
        "cards": {"foxlink": "FocusLink", "journal": "拾光日记"},
    }

    icon = shell.build_tray_icon(cast(Any, controller), actions)

    top_labels = [getattr(item, "text", "") for item in icon.menu.items]
    assert top_labels == [
        "打开管理面板",
        "显示全部桌面卡片",
        "隐藏全部桌面卡片",
        "单独开关卡片",
        "",
        "刷新状态",
        "",
        "退出",
    ]
    management_item = next(
        item for item in icon.menu.items if getattr(item, "text", "") == "打开管理面板"
    )
    assert management_item.options["default"] is True
    card_item = next(
        item for item in icon.menu.items if getattr(item, "text", "") == "单独开关卡片"
    )
    labels = [getattr(item, "text", "") for item in card_item.action.items]
    assert labels == [
        "FocusLink",
        "拾光日记",
        "",
        "显示全部卡片",
        "隐藏全部卡片",
        "恢复卡片默认位置与大小",
    ]
    journal = next(
        item for item in card_item.action.items if getattr(item, "text", "") == "拾光日记"
    )
    assert journal.options["checked"](object()) is False
