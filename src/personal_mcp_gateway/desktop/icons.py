"""Tray and shortcut artwork.

Status hues are the reserved status palette validated for both the dark and the
light chart surface; they are never reused for chart series.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_OFFLINE,
    STATUS_ONLINE,
)

STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    STATUS_ONLINE: (12, 163, 12),
    STATUS_DEGRADED: (250, 178, 25),
    STATUS_OFFLINE: (208, 59, 59),
    STATUS_DISCONNECTED: (137, 135, 129),
}

_BRAND_VIOLET = (144, 133, 233)
_TILE = (18, 22, 34, 255)
_TRAY_SIZE = 64

# The badge shape carries the state alongside the hue, matching the dashboard dots:
# circle = healthy, triangle = degraded, diamond = offline, square = no link. Hue
# alone is not enough here -- good and crit are only DeltaE 4.1 apart under
# deuteranopia -- and the tray is the one status surface still visible once the
# window is hidden, so it must not be the least accessible one.
_SHAPES = {
    STATUS_ONLINE: "circle",
    STATUS_DEGRADED: "triangle",
    STATUS_OFFLINE: "diamond",
    STATUS_DISCONNECTED: "square",
}


def status_color(status: str) -> tuple[int, int, int]:
    return STATUS_COLORS.get(status, STATUS_COLORS[STATUS_DISCONNECTED])


def status_shape(status: str) -> str:
    return _SHAPES.get(status, _SHAPES[STATUS_DISCONNECTED])


# A triangle and a diamond cover half of their bounding box; a circle covers .785
# and a square all of it. Drawn to one box the shapes would read as four different
# sizes, so each box is scaled to even out the inked area.
# The triangle stops at 1.20 rather than the 1.25 area-match: centring on the
# centroid lifts the apex by a third of the half-span, and 1.25 pushed it past the
# tile edge.
_AREA_TRIM = {"circle": 1.0, "square": 0.89, "diamond": 1.25, "triangle": 1.20}


def _draw_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, span: float, status: str) -> None:
    """Stamp the status mark, shaped by state, centred on ``cx``/``cy``."""
    shape = status_shape(status)
    half = span * _AREA_TRIM[shape] / 2
    x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
    fill = (*status_color(status), 255)
    if shape == "circle":
        draw.ellipse((x0, y0, x1, y1), fill=fill)
    elif shape == "triangle":
        # A triangle's centroid sits a third of the half-span below its box centre,
        # so it is lifted by that much: the mark centres on the tile like the others
        # instead of hanging off the bottom edge.
        lift = half / 3
        draw.polygon([(cx, y0 - lift), (x1, y1 - lift), (x0, y1 - lift)], fill=fill)
    elif shape == "diamond":
        draw.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=fill)
    else:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=half / 5, fill=fill)


def build_tray_image(status: str, size: int = _TRAY_SIZE) -> Image.Image:
    """A rounded brand tile with a shaped status badge, so state reads at 16px.

    Drawn oversampled and downscaled: Pillow does not antialias polygons, and the
    badge shapes are what distinguish the states, so jagged edges at tray size
    would defeat the encoding.
    """
    scale = max(1, min(4, 512 // max(size, 1)))
    edge = size * scale
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    pad = edge / 8
    draw.rounded_rectangle(
        (pad, pad, edge - pad, edge - pad),
        radius=edge / 5,
        fill=_TILE,
        outline=(*_BRAND_VIOLET, 190),
        width=max(2, int(edge / 22)),
    )

    # Centred, not a corner badge: at 16px a corner mark has to be tiny to clear the
    # rounded edge, and tiny is exactly where shape stops being readable.
    _draw_badge(draw, edge / 2, edge / 2, edge * 0.46, status)
    if scale == 1:
        return image
    # Pillow types resize()'s size parameter as a union containing Unknown, so the
    # call reads as partially unknown however it is written.
    return image.resize((size, size), Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]


def write_app_icon(destination: Path) -> Path:
    """Emit a multi-resolution .ico for the desktop and Start Menu shortcuts."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = build_tray_image(STATUS_ONLINE, 256)
    base.save(destination, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    return destination
