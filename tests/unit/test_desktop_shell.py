from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

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
    assert os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] == "00000000"


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
