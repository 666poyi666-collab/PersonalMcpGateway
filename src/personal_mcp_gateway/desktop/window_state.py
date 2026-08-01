"""User-scoped persistence for desktop window geometry and view preferences."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

FULL_SIZE = (1160, 760)
# Sized so the compact panel is snug around the meter, the target list and the KPI
# footer. Measured at 4 targets; the stage scrolls past that rather than the window
# carrying permanent dead space under the last row.
COMPACT_SIZE = (392, 520)
MIN_SIZE = (360, 480)
# v3 keeps the bounded wire shape but lets the renderer interpret it as
# normalized, elastic geometry instead of a fixed-pixel canvas.
PROJECT_LAYOUT_VERSION = 3
MAX_PROJECT_POSITION = 100_000
MAX_PROJECT_TILE_SIZE = 20_000

# Desktop cards are native top-level windows. Their geometry is deliberately
# separate from the normalized layout used by the management board so moving a
# desktop card can never reflow or resize another card.
CARD_LAYOUT_VERSION = 1
CARD_MIN_SIZE = (220, 150)
MAX_CARD_POSITION = 100_000
MAX_CARD_SIZE = 20_000
DEFAULT_CARD_LAYOUT: dict[str, dict[str, int]] = {
    "foxlink": {"x": 28, "y": 48, "w": 320, "h": 270, "order": 0},
    "watch": {"x": 370, "y": 48, "w": 430, "h": 310, "order": 1},
    "journal": {"x": 822, "y": 48, "w": 360, "h": 310, "order": 2},
    "personal": {"x": 28, "y": 382, "w": 650, "h": 410, "order": 3},
    "bzsjk": {"x": 700, "y": 382, "w": 360, "h": 330, "order": 4},
}
CARD_IDS = frozenset(DEFAULT_CARD_LAYOUT)


def state_dir() -> Path:
    root = os.environ.get("PERSONAL_MCP_DESKTOP_HOME")
    if root:
        return Path(root)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(local) / "Poyi" / "PersonalMcpDesktop"


def state_path() -> Path:
    return state_dir() / "window-state.json"


@dataclass(slots=True)
class WindowState:
    x: int | None = None
    y: int | None = None
    width: int = FULL_SIZE[0]
    height: int = FULL_SIZE[1]
    compact: bool = False
    on_top: bool = False
    # The operator prefers a light board; dark stays one titlebar click away.
    theme: str = "light"
    project_layout_version: int = PROJECT_LAYOUT_VERSION
    desktop_mode: bool = False
    project_layout: dict[str, dict[str, int]] | None = None
    card_layout_version: int = CARD_LAYOUT_VERSION
    card_layout: dict[str, dict[str, int]] | None = None
    hidden_cards: list[str] | None = None

    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


def _coerce_int(value: object, fallback: int | None) -> int | None:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return fallback


def normalize_project_layout(value: object) -> dict[str, dict[str, int]]:
    """Keep layout geometry bounded while preserving legacy migration inputs."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    entries = cast(dict[object, object], value)
    for raw_id, raw_tile in entries.items():
        if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 64:
            continue
        if not isinstance(raw_tile, dict):
            continue
        tile = cast(dict[str, Any], raw_tile)
        order = _coerce_int(tile.get("order"), len(result)) or 0
        if any(key in tile for key in ("x", "y", "w", "h")):
            result[raw_id] = {
                "x": max(
                    0,
                    min(
                        MAX_PROJECT_POSITION,
                        _coerce_int(tile.get("x"), 0) or 0,
                    ),
                ),
                "y": max(
                    0,
                    min(
                        MAX_PROJECT_POSITION,
                        _coerce_int(tile.get("y"), 0) or 0,
                    ),
                ),
                "w": max(
                    1,
                    min(
                        MAX_PROJECT_TILE_SIZE,
                        _coerce_int(tile.get("w"), 500) or 500,
                    ),
                ),
                "h": max(
                    104,
                    min(
                        MAX_PROJECT_TILE_SIZE,
                        _coerce_int(tile.get("h"), 360) or 360,
                    ),
                ),
                "order": max(0, min(999, order)),
            }
            continue

        # One release used a coarse cols/rows grid. Preserve it long enough for
        # the renderer to migrate it into freeform geometry on the next save.
        cols = _coerce_int(tile.get("cols"), 6) or 6
        rows = _coerce_int(tile.get("rows"), 1) or 1
        result[raw_id] = {
            "cols": max(3, min(12, cols)),
            "rows": max(1, min(3, rows)),
            "order": max(0, min(999, order)),
        }
    return result


def normalize_card_layout(value: object) -> dict[str, dict[str, int]]:
    """Validate absolute screen geometry for independent desktop cards."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    entries = cast(dict[object, object], value)
    for raw_id, raw_card in entries.items():
        if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 64:
            continue
        if not isinstance(raw_card, dict):
            continue
        card = cast(dict[str, Any], raw_card)
        if not all(key in card for key in ("x", "y", "w", "h")):
            continue
        x = _coerce_int(card.get("x"), None)
        y = _coerce_int(card.get("y"), None)
        width = _coerce_int(card.get("w"), None)
        height = _coerce_int(card.get("h"), None)
        if None in (x, y, width, height):
            continue
        order = _coerce_int(card.get("order"), len(result)) or 0
        result[raw_id] = {
            "x": max(-MAX_CARD_POSITION, min(MAX_CARD_POSITION, cast(int, x))),
            "y": max(-MAX_CARD_POSITION, min(MAX_CARD_POSITION, cast(int, y))),
            "w": max(CARD_MIN_SIZE[0], min(MAX_CARD_SIZE, cast(int, width))),
            "h": max(CARD_MIN_SIZE[1], min(MAX_CARD_SIZE, cast(int, height))),
            "order": max(0, min(999, order)),
        }
    return result


def normalize_hidden_cards(value: object) -> list[str]:
    """Keep a stable, deduplicated list of known cards hidden by the user."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in cast(list[object], value):
        if isinstance(item, str) and item in CARD_IDS and item not in result:
            result.append(item)
    return result


def load_state(path: Path | None = None) -> WindowState:
    """Read persisted state, falling back to defaults on any corruption."""
    target = path or state_path()
    try:
        raw: object = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WindowState()
    if not isinstance(raw, dict):
        return WindowState()
    data = cast(dict[str, Any], raw)
    width = _coerce_int(data.get("width"), FULL_SIZE[0]) or FULL_SIZE[0]
    height = _coerce_int(data.get("height"), FULL_SIZE[1]) or FULL_SIZE[1]
    theme = data.get("theme")
    layout = normalize_project_layout(data.get("project_layout"))
    card_layout = normalize_card_layout(data.get("card_layout"))
    raw_layout_version = _coerce_int(data.get("project_layout_version"), None)
    layout_version = (
        PROJECT_LAYOUT_VERSION
        if not layout
        else max(1, min(PROJECT_LAYOUT_VERSION, raw_layout_version or 1))
    )
    desktop_mode = bool(data.get("desktop_mode", False))
    return WindowState(
        x=_coerce_int(data.get("x"), None),
        y=_coerce_int(data.get("y"), None),
        width=max(width, MIN_SIZE[0]),
        height=max(height, MIN_SIZE[1]),
        compact=bool(data.get("compact", False)) and not desktop_mode,
        on_top=bool(data.get("on_top", False)) and not desktop_mode,
        theme=theme if theme in {"dark", "light"} else "light",
        project_layout_version=layout_version,
        desktop_mode=desktop_mode,
        project_layout=layout,
        card_layout_version=CARD_LAYOUT_VERSION,
        card_layout=card_layout or None,
        hidden_cards=normalize_hidden_cards(data.get("hidden_cards")) or None,
    )


def save_state(state: WindowState, path: Path | None = None) -> bool:
    """Persist state atomically; a failure here must never break the app."""
    target = path or state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError:
        return False
    return True
