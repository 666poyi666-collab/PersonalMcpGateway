from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from personal_mcp_gateway.desktop.window_state import (
    CARD_LAYOUT_VERSION,
    CARD_MIN_SIZE,
    FULL_SIZE,
    MIN_SIZE,
    PROJECT_LAYOUT_VERSION,
    WindowState,
    load_state,
    normalize_card_layout,
    normalize_card_visibility,
    normalize_hidden_cards,
    normalize_project_layout,
    save_state,
    state_dir,
)


def test_v3_state_round_trips_normalized_layout(tmp_path: Path) -> None:
    target = tmp_path / "window-state.json"
    saved = WindowState(
        x=120,
        y=64,
        width=1200,
        height=800,
        compact=True,
        on_top=True,
        theme="dark",
        project_layout={"watch": {"x": 420, "y": 0, "w": 580, "h": 360, "order": 0}},
    )
    assert PROJECT_LAYOUT_VERSION == 3
    assert saved.project_layout_version == PROJECT_LAYOUT_VERSION
    assert save_state(saved, target) is True
    loaded = load_state(target)
    assert loaded == saved
    assert loaded.size() == (1200, 800)
    assert loaded.project_layout_version == PROJECT_LAYOUT_VERSION


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    loaded = load_state(tmp_path / "absent.json")
    assert loaded.size() == FULL_SIZE
    assert loaded.compact is False
    assert loaded.project_layout_version == PROJECT_LAYOUT_VERSION
    assert loaded.card_layout_version == CARD_LAYOUT_VERSION
    assert loaded.card_visibility == {
        "foxlink": False,
        "watch": False,
        "journal": False,
        "personal": False,
        "bzsjk": False,
    }
    assert loaded.theme == "light"


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
    assert loaded.theme == "light"


def test_legacy_desktop_mode_and_hidden_cards_migrate_to_independent_visibility(
    tmp_path: Path,
) -> None:
    target = tmp_path / "desktop-mode.json"
    target.write_text(
        '{"compact":true,"on_top":true,"desktop_mode":true,'
        '"hidden_cards":["journal","bad","journal"]}',
        encoding="utf-8",
    )

    loaded = load_state(target)

    assert loaded.compact is True
    assert loaded.on_top is True
    assert loaded.card_visibility == {
        "foxlink": True,
        "watch": True,
        "journal": False,
        "personal": True,
        "bzsjk": True,
    }


def test_legacy_disabled_desktop_mode_migrates_to_all_cards_hidden(tmp_path: Path) -> None:
    target = tmp_path / "disabled-desktop-mode.json"
    target.write_text(
        '{"desktop_mode":false,"hidden_cards":[]}',
        encoding="utf-8",
    )

    loaded = load_state(target)

    assert not any(loaded.card_visibility.values())


def test_save_reports_failure_instead_of_raising(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert save_state(WindowState(), blocker / "nested" / "state.json") is False


def test_concurrent_saves_are_serialized_and_use_unique_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "window-state.json"
    temporary_names: list[str] = []
    original_replace = Path.replace

    def record_replace(source: Path, destination: Path) -> Path:
        if destination == target:
            temporary_names.append(source.name)
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", record_replace)
    states = [WindowState(x=index) for index in range(24)]

    def persist(state: WindowState) -> bool:
        return save_state(state, target)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(persist, states))

    assert all(results)
    assert len(temporary_names) == len(states)
    assert len(set(temporary_names)) == len(states)
    assert all(name.startswith(".window-state.json.") for name in temporary_names)
    assert not list(tmp_path.glob("*.tmp"))
    assert load_state(target).x in range(len(states))


def test_state_dir_honours_an_explicit_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_MCP_DESKTOP_HOME", r"C:\tmp\poyi-desktop")
    assert state_dir() == Path(r"C:\tmp\poyi-desktop")


def test_project_layout_uses_bounded_absolute_geometry_and_drops_invalid_entries() -> None:
    assert normalize_project_layout(
        {
            "watch": {"x": 999, "y": -4, "w": 300, "h": 99, "order": -2},
            "journal": {"x": 40.9, "y": 220.8, "w": 420.2, "h": 280.9, "order": 3},
            "": {"cols": 6},
            "bad": "not-a-tile",
        }
    ) == {
        "watch": {"x": 999, "y": 0, "w": 300, "h": 104, "order": 0},
        "journal": {"x": 40, "y": 220, "w": 420, "h": 280, "order": 3},
    }


def test_unversioned_freeform_layout_is_marked_for_one_time_renderer_migration(
    tmp_path: Path,
) -> None:
    target = tmp_path / "old-layout.json"
    target.write_text(
        '{"project_layout":{"watch":{"x":420,"y":0,"w":580,"h":360,"order":1}}}',
        encoding="utf-8",
    )

    loaded = load_state(target)

    assert loaded.project_layout_version == 1
    assert loaded.project_layout is not None
    assert loaded.project_layout["watch"]["x"] == 420


def test_v2_layout_is_marked_for_one_time_renderer_migration(tmp_path: Path) -> None:
    target = tmp_path / "v2-layout.json"
    target.write_text(
        '{"project_layout_version":2,"project_layout":'
        '{"watch":{"x":420,"y":0,"w":580,"h":360,"order":1}}}',
        encoding="utf-8",
    )

    loaded = load_state(target)

    assert loaded.project_layout_version == 2
    assert loaded.project_layout_version < PROJECT_LAYOUT_VERSION
    assert loaded.project_layout == {"watch": {"x": 420, "y": 0, "w": 580, "h": 360, "order": 1}}


def test_legacy_grid_layout_is_kept_for_renderer_migration() -> None:
    assert normalize_project_layout({"watch": {"cols": 99, "rows": 0, "order": 2}}) == {
        "watch": {"cols": 12, "rows": 1, "order": 2}
    }


def test_card_layout_keeps_independent_absolute_window_geometry() -> None:
    assert normalize_card_layout(
        {
            "foxlink": {"x": -320, "y": 48, "w": 640, "h": 280, "order": 2},
            "watch": {"x": 400.8, "y": 90.4, "w": 12, "h": 20, "order": -1},
            "bad": {"x": 1, "y": 2, "w": "wide", "h": 300},
        }
    ) == {
        "foxlink": {"x": -320, "y": 48, "w": 640, "h": 280, "order": 2},
        "watch": {
            "x": 400,
            "y": 90,
            "w": CARD_MIN_SIZE[0],
            "h": CARD_MIN_SIZE[1],
            "order": 0,
        },
    }


def test_card_layout_round_trips_separately_from_board_layout(tmp_path: Path) -> None:
    target = tmp_path / "cards.json"
    saved = WindowState(
        project_layout={"watch": {"x": 0, "y": 0, "w": 500, "h": 500, "order": 0}},
        card_layout={"watch": {"x": 960, "y": 44, "w": 420, "h": 300, "order": 1}},
    )

    assert save_state(saved, target) is True
    loaded = load_state(target)

    assert loaded.project_layout == saved.project_layout
    assert loaded.card_layout == saved.card_layout


def test_card_visibility_is_complete_normalized_and_persisted(tmp_path: Path) -> None:
    assert normalize_hidden_cards(["journal", "bad", "journal", 4, "watch"]) == [
        "journal",
        "watch",
    ]
    assert normalize_card_visibility({"journal": True, "watch": False, "bad": True}) == {
        "foxlink": False,
        "watch": False,
        "journal": True,
        "personal": False,
        "bzsjk": False,
    }
    target = tmp_path / "hidden-cards.json"
    saved = WindowState(
        card_visibility={
            "foxlink": True,
            "watch": False,
            "journal": True,
            "personal": False,
            "bzsjk": False,
        }
    )

    assert save_state(saved, target) is True
    assert load_state(target).card_visibility == saved.card_visibility
