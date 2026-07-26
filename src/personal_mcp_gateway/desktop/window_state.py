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
    theme: str = "dark"

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
    return WindowState(
        x=_coerce_int(data.get("x"), None),
        y=_coerce_int(data.get("y"), None),
        width=max(width, MIN_SIZE[0]),
        height=max(height, MIN_SIZE[1]),
        compact=bool(data.get("compact", False)),
        on_top=bool(data.get("on_top", False)),
        theme=theme if theme in {"dark", "light"} else "dark",
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
