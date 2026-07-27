from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from personal_mcp_gateway.desktop.capture import native_handle, screenshot_path


def test_screenshot_path_uses_the_pictures_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = screenshot_path(datetime(2026, 7, 27, 12, 34, 56))
    assert path == (
        tmp_path / "Pictures" / "Poyi Control Center" / "Poyi-Control-Center-20260727-123456.png"
    )


def test_native_handle_accepts_dotnet_style_handles() -> None:
    class Handle:
        def ToInt64(self) -> int:
            return 42

    class Native:
        def __init__(self) -> None:
            self.Handle = Handle()

    class Window:
        native = Native()

    assert native_handle(Window()) == 42
    assert native_handle(object()) == 0
