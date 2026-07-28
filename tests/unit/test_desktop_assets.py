from __future__ import annotations

import itertools
from pathlib import Path

from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_OFFLINE,
    STATUS_ONLINE,
)
from personal_mcp_gateway.desktop.icons import (
    build_tray_image,
    status_color,
    status_shape,
    write_app_icon,
)
from personal_mcp_gateway.desktop.page import STATIC_ROOT

ASSETS = ("desktop.html", "desktop.css", "desktop.js")
STATUSES = (STATUS_ONLINE, STATUS_DEGRADED, STATUS_OFFLINE, STATUS_DISCONNECTED)


def _mark_pixels(status: str, size: int) -> set[tuple[int, int]]:
    """Coordinates of the status mark, found by colour so the tile is excluded."""
    image = build_tray_image(status, size).convert("RGBA")
    # Raw bytes rather than load() or getdata(): a loaded pixel is typed float |
    # tuple so unpacking never checks, and getdata() is deprecated in Pillow 14.
    raw = image.tobytes()
    want = status_color(status)
    found: set[tuple[int, int]] = set()
    for index in range(size * size):
        red, green, blue, alpha = raw[index * 4 : index * 4 + 4]
        near = max(abs(red - want[0]), abs(green - want[1]), abs(blue - want[2])) < 26
        if alpha > 200 and near:
            found.add((index % size, index // size))
    return found


def test_desktop_assets_ship_with_the_package() -> None:
    for name in ASSETS:
        asset = STATIC_ROOT / name
        assert asset.is_file(), name
        assert asset.stat().st_size > 0, name


def test_page_loads_only_local_assets() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    assert "desktop.css" in markup
    assert "desktop.js" in markup
    assert "http://" not in markup
    assert "https://" not in markup


def test_renderer_never_makes_its_own_network_calls() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "pywebview.api" in script


def test_unified_shell_keeps_live_status_with_the_board() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert '<body class="booting" data-view="overview">' in markup
    assert '<span class="view-live">' in markup
    status_nodes = ("sbDot", "sbState", "sbGuard", "sbSync", "sbProbe")
    assert all(f'id="{node}"' in markup for node in status_nodes)
    assert '<footer class="statusbar">' not in markup
    shell_rows = "grid-template-rows: var(--titlebar) var(--viewbar) minmax(0, 1fr);"
    assert "display: grid;" in style and shell_rows in style
    assert "#stage { min-width: 0; min-height: 0; overflow: auto;" in style
    assert ".overview-deck" in style and ".overview-rail" in style
    assert 'id="overviewRail"' in markup and 'id="matrixHeading"' in markup


def test_layout_v3_normalizes_legacy_geometry_once_and_persists_units() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")

    assert "const PROJECT_LAYOUT_VERSION = 3;" in script
    assert "function geometryFromUnits(units, viewportWidth, viewportHeight)" in script
    assert "if (sourceVersion >= PROJECT_LAYOUT_VERSION)" in script
    assert "const bounds = layoutBounds(projectLayout);" in script
    assert "x: num(saved.x) / bounds.right * LAYOUT_SCALE" in script
    assert "w: num(saved.w) / bounds.right * LAYOUT_SCALE" in script
    migration_guard = (
        "if (sourceVersion < PROJECT_LAYOUT_VERSION && Object.keys(projectLayout).length)"
    )
    assert migration_guard in script
    assert "projectLayoutVersion = PROJECT_LAYOUT_VERSION;" in script
    assert "bridge.set_project_layout(projectLayout).catch(() => {});" in script
    assert "x: Math.round(rect.left / viewportWidth * LAYOUT_SCALE)" in script
    assert "y: Math.round(rect.top / viewportHeight * LAYOUT_SCALE)" in script
    assert "w: Math.round(rect.width / viewportWidth * LAYOUT_SCALE)" in script
    assert "h: Math.round(rect.height / viewportHeight * LAYOUT_SCALE)" in script


def test_sync_chips_distinguish_cloud_snapshot_and_local_semantics() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert "function syncChip(sync)" in script
    assert 'plane === "cloud_primary"' in script
    assert 'plane === "snapshot_mirror"' in script
    assert 'plane === "local_only"' in script
    assert script.count('"多端持续同步"') == 1
    assert '"本机副本暂停上行"' in script
    assert '"云端快照"' in script and "关机后不再更新" in script
    assert '"本机数据"' in script and '"关机后离线"' in script
    assert "chip.dataset.plane = plane;" in script
    assert "chip.dataset.state = sync && sync.compliance" in script
    assert "observation.lastSuccessfulPushAt" in script
    assert "vitals.append(syncChip(target.sync))" in script
    assert 'continuity.classList.add("gw-sync-chip")' in script
    assert '.sync-chip[data-plane="snapshot_mirror"]' in style
    assert '.sync-chip[data-plane="local_only"]' in style


def test_every_project_has_a_dedicated_console_and_responsive_contract() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    contracts = (
        ("personal", "gatewayConsole", "gateway-console", "MCP ROUTING FABRIC"),
        ("watch", "watchConsole", "watch-console", "TOTAL DISTANCE"),
        ("foxlink", "focusConsole", "focus-console", "TEMPORAL FIELD / TODAY"),
        ("journal", "journalConsole", "journal-console", "REVIEW LEDGER"),
        ("bzsjk", "bzsjkConsole", "bz-console", "DISCIPLINE CORE / LOCAL"),
    )
    for project_id, function_name, class_name, identity in contracts:
        assert f"function {function_name}(" in script
        assert f'if (target.id === "{project_id}")' in script
        assert f'"{class_name}"' in script
        assert f".{class_name}" in style
        assert identity in script

    assert 'display: "不做手机控"' in script
    assert 'String(item.title || "").trim() === "不做手机控"' in script
    assert 'dataPlane: "local_only"' in script
    assert 'make("small", null, "关机后无云同步")' in script
    assert '"NO CLOUD CLAIM"' in script
    assert '.project-personal[data-height-class="short"]' in style
    assert '.project-watch[data-height-class="short"]' in style
    assert '.project-foxlink[data-height-class="short"]' in style
    assert '.project-journal[data-height-class="short"]' in style
    assert '.project-bzsjk[data-height-class="short"]' in style
    assert '[data-width-class="small"][data-height-class="medium"]' in style
    assert ".proj.tile-summary .proj-head { position: relative" in style
    assert ".proj-head { grid-row: 1 / 4;" in style and "overflow: hidden;" in style
    assert ".proj.tile-summary" in style


def test_capture_and_freeform_tile_controls_ship_together() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'id="btnCapture"' in markup
    assert 'id="btnDesktop"' in markup
    assert 'id="btnLayout"' in markup
    assert "bridge.capture()" in script
    assert "bridge.set_project_layout(projectLayout)" in script
    assert "bridge.set_desktop_mode(next)" in script
    assert "projectLayoutVersion" in script
    assert "RECOVERY_BANNER_FAILURES = 3" in script
    assert "function gatewayConsole(target, data)" in script
    assert '"MCP ROUTING FABRIC"' in script
    assert '"SERVICE FABRIC"' in script and '"24H TRAFFIC"' in script
    assert "gateway-data" in script and ".gateway-console" in style
    assert ".gw-route-node" in style and ".gw-service-matrix" in style
    assert "startScrollLeft" in script and "updateProjectCanvasSize" in script
    assert "width: min(1460px, 100%)" not in style
    assert "body.desktop-mode .window-resize-zone" in style
    assert "pointerdown" in script and "requestAnimationFrame" in script
    assert "ResizeObserver" in script and "snapTileRect" in script
    assert 'window.addEventListener("resize", markWindowResizing)' in script
    assert "body.window-resizing" in style
    assert markup.count('data-window-edge="') == 8
    assert "windowResizeEdgeAt" in script
    assert 'document.addEventListener("pointerdown", requestWindowResize, true)' in script
    assert "bridge.begin_window_resize(edge)" in script
    assert "window-resize-se::after" in style
    assert "tile-handle-nw" in style and "layout-guide.visible" in style
    assert "dragstart" not in script and "tile-resizer" not in style


def test_watch_tile_has_a_responsive_training_and_recovery_instrument() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'eyebrow: "INTERVAL ENGINE / OWW221"' in script
    assert "function watchConsole(target, widgets)" in script
    assert 'widget.id === "watch_workouts"' in script
    assert 'widget.id === "watch_sleep"' in script
    assert 'make("span", null, "TOTAL DISTANCE")' in script
    assert 'make("span", null, "SLEEP SCORE")' in script
    assert 'target.id === "watch"' in script and 'classList.add("watch-data")' in script
    assert ".project-watch" in style and ".watch-console" in style
    assert ".wi-score-value" in style and "stroke-dashoffset" in style
    assert ".project-watch.tile-narrow" in style
    assert '.project-watch[data-height-class="short"]' in style


def test_every_status_maps_to_a_distinct_tray_colour() -> None:
    colours = {status_color(status) for status in STATUSES}
    assert len(colours) == len(STATUSES)
    assert status_color("nonsense") == status_color(STATUS_DISCONNECTED)


def test_every_status_maps_to_a_distinct_tray_shape() -> None:
    shapes = {status_shape(status) for status in STATUSES}
    assert len(shapes) == len(STATUSES)
    assert status_shape("nonsense") == status_shape(STATUS_DISCONNECTED)


def test_no_two_tray_states_share_a_silhouette() -> None:
    """Healthy and critical are DeltaE 4.1 apart under deuteranopia.

    So the tray cannot encode state in hue alone. This pins the encoding down to
    the weakest claim that still catches the regression: reuse one shape for two
    states and their masks land on top of each other (IoU ~1.0). Concentric marks
    of matched area overlap heavily even when clearly different, so the bar is
    "not the same outline", not a distinctness score.
    """
    marks = {status: _mark_pixels(status, 64) for status in STATUSES}
    for status, pixels in marks.items():
        assert len(pixels) > 200, status
    for first, second in itertools.combinations(STATUSES, 2):
        overlap = len(marks[first] & marks[second]) / len(marks[first] | marks[second])
        assert overlap < 0.90, (first, second, overlap)


def test_the_status_mark_never_escapes_the_tile() -> None:
    for status in STATUSES:
        for size in (32, 64, 256):
            pixels = _mark_pixels(status, size)
            assert pixels, (status, size)
            pad = size / 8
            assert min(x for x, _ in pixels) >= pad, (status, size)
            assert min(y for _, y in pixels) >= pad, (status, size)
            assert max(x for x, _ in pixels) <= size - pad, (status, size)
            assert max(y for _, y in pixels) <= size - pad, (status, size)


def test_tray_image_renders_at_tray_size() -> None:
    for size in (16, 24, 64):
        image = build_tray_image(STATUS_ONLINE, size)
        assert image.size == (size, size)
        assert image.mode == "RGBA"


def test_app_icon_is_written_as_a_multi_size_ico(tmp_path: Path) -> None:
    target = write_app_icon(tmp_path / "nested" / "poyi.ico")
    assert target.is_file()
    assert target.read_bytes()[:4] == b"\x00\x00\x01\x00"
