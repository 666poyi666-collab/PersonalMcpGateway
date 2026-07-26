from __future__ import annotations

from pathlib import Path

import pytest

from personal_mcp_gateway.desktop.window_state import (
    FULL_SIZE,
    MIN_SIZE,
    WindowState,
    load_state,
    save_state,
    state_dir,
)


def test_state_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "window-state.json"
    saved = WindowState(
        x=120, y=64, width=1200, height=800, compact=True, on_top=True, theme="light"
    )
    assert save_state(saved, target) is True
    loaded = load_state(target)
    assert loaded == saved
    assert loaded.size() == (1200, 800)


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    loaded = load_state(tmp_path / "absent.json")
    assert loaded.size() == FULL_SIZE
    assert loaded.compact is False
    assert loaded.theme == "dark"


def test_corrupt_or_unexpected_content_falls_back(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_state(broken).size() == FULL_SIZE

    wrong_type = tmp_path / "list.json"
    wrong_type.write_text("[1, 2]", encoding="utf-8")
    assert load_state(wrong_type).size() == FULL_SIZE


def test_undersized_and_invalid_fields_are_clamped(tmp_path: Path) -> None:
    target = tmp_path / "small.json"
    target.write_text(
        '{"width": 10, "height": 10, "x": "left", "theme": "neon"}',
        encoding="utf-8",
    )
    loaded = load_state(target)
    assert loaded.size() == MIN_SIZE
    assert loaded.x is None
    assert loaded.theme == "dark"


def test_save_reports_failure_instead_of_raising(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert save_state(WindowState(), blocker / "nested" / "state.json") is False


def test_state_dir_honours_an_explicit_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_MCP_DESKTOP_HOME", r"C:\tmp\poyi-desktop")
    assert state_dir() == Path(r"C:\tmp\poyi-desktop")
