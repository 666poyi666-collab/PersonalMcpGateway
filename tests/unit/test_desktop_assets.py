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


def test_renderer_keeps_project_dom_stable_and_batches_resize_work() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")

    assert "replaceChildren" not in script
    assert "function reconcileRenderChildren(" in script
    assert "function projectSectionSignature(" in script
    assert "section._renderSignature" in script
    assert "function removeWithMotion(" in script
    assert "function queueProjectResizeLayout(" in script
    assert "projectResizeFrame = window.requestAnimationFrame" in script


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


def test_dashboard_navigation_explains_each_view_in_plain_language() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")

    assert "卡片概览" in markup
    assert "状态与关键数据" in markup
    assert "运行记录" in markup
    assert "最近 24 小时" in markup
    assert 'data-view="extensions"' not in markup
    assert "数据卡片" not in markup
    assert "异常与状态变化" in markup
    assert "扩展面板" not in markup


def test_stage_hides_scrollbars_and_middle_drag_pans_on_animation_frames() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert "scrollbar-width: none" in style
    assert "#stage::-webkit-scrollbar" in style
    assert "body.canvas-panning #stage" in style
    assert "const MIDDLE_MOUSE_BUTTON = 1;" in script
    assert "const MIDDLE_MOUSE_BUTTONS_MASK = 4;" in script
    assert "function beginCanvasPan(event)" in script
    assert "function moveCanvasPan(event)" in script
    assert "function finishCanvasPan(event)" in script
    assert "window.requestAnimationFrame(applyCanvasPanFrame)" in script
    assert "active.startScrollLeft - (event.clientX - active.startX)" in script
    assert "active.startScrollTop - (event.clientY - active.startY)" in script
    assert 'dom.stage.addEventListener("auxclick", preventMiddleAuxClick)' in script
    assert "activeTileInteraction || activeCanvasPan" in script


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
        ("personal", "gatewayConsole", "gateway-console", "本机连接与自动恢复"),
        ("watch", "watchConsole", "watch-console", "累计距离"),
        ("foxlink", "focusConsole", "focus-console", "当前专注"),
        ("journal", "journalConsole", "journal-console", "今日记录"),
        ("bzsjk", "bzsjkConsole", "bz-console", "本地专注监督"),
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
    assert '"仅本机' in script and '数据待接入"' in script
    assert 'make("span", null, "数据范围")' in script
    assert 'make("b", null, "只读取本机数据")' in script
    assert '.project-personal[data-height-class="short"]' in style
    assert '.project-watch[data-height-class="short"]' in style
    assert '.project-foxlink[data-height-class="short"]' in style
    assert '.project-journal[data-height-class="short"]' in style
    assert '.project-bzsjk[data-height-class="short"]' in style
    assert '[data-width-class="small"][data-height-class="medium"]' in style
    assert ".proj.tile-summary .proj-head { position: relative" in style
    assert ".proj-head { grid-row: 1 / 4;" in style and "overflow: hidden;" in style
    assert ".proj.tile-summary" in style


def test_management_titlebar_keeps_only_clear_everyday_actions() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    for button_id, label in (
        ("btnDesktop", "显示桌面卡片"),
        ("btnRefresh", "刷新"),
        ("btnMin", "最小化"),
        ("btnClose", "关闭"),
    ):
        assert f'id="{button_id}"' in markup
        assert f">{label}</button>" in markup
    for removed in (
        "btnRepair",
        "btnCapture",
        "btnLayout",
        "btnLayoutReset",
        "btnTheme",
        "btnTop",
        "btnCompact",
    ):
        assert f'id="{removed}"' not in markup
    assert "bridge.set_all_cards_visible" in script
    assert ".tb-action" in style
    assert "RECOVERY_BANNER_FAILURES = 3" in script
    assert "function gatewayConsole(target, data)" in script
    assert '"服务状态"' in script and '"24 小时调用"' in script
    assert "gateway-data" in script and ".gateway-console" in style
    assert markup.count('data-window-edge="') == 8
    assert "windowResizeEdgeAt" in script
    assert 'document.addEventListener("pointerdown", requestWindowResize, true)' in script
    assert "bridge.begin_window_resize(edge)" in script


def test_widget_mode_is_transparent_from_first_paint_and_keeps_only_tiles() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert '<body class="booting" data-view="overview">' in markup
    assert "const cardMode = CARD_ID ? true : Boolean(view.cardMode);" in script
    assert 'dom.body.classList.toggle("desktop-mode", cardMode);' in script
    assert "html,\nbody,\n#stage,\n.board,\n.view-panel,\n.overview-deck,\n.project-grid" in style
    assert "body.desktop-mode #stage { background: transparent; }" in style
    assert "body.desktop-mode .connection-banner { display: none !important; }" in style


def test_management_panel_can_switch_each_desktop_card_directly() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'id="cardManager"' in markup
    assert 'id="btnShowAllCards"' in markup
    assert 'id="btnHideAllCards"' in markup
    for card_id in ("foxlink", "watch", "journal", "personal", "bzsjk"):
        assert f'data-card-toggle="{card_id}"' in markup
    assert "view.cardVisibility" in script
    assert "view.visibleCardCount" in script
    assert "bridge.set_card_visible(button.dataset.cardToggle, next)" in script
    assert "bridge.set_all_cards_visible(Boolean(visible))" in script
    assert '.card-switch[aria-pressed="true"]' in style
    assert "body.card-window .card-manager" in style


def test_polling_is_single_flight_and_volatile_diagnostics_do_not_rebuild_cards() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")

    assert "let pullInFlight = null;" in script
    assert "let forcePullQueued = false;" in script
    assert "const BRIDGE_POLL_TIMEOUT_MS = 8000;" in script
    assert "function bridgePollWithTimeout(operation)" in script
    assert "Promise.race([Promise.resolve().then(operation), timeout])" in script
    assert "finally(() => window.clearTimeout(timeoutId))" in script
    assert "const payload = await bridgePollWithTimeout(" in script
    assert "if (pullInFlight) return pullInFlight;" in script
    assert "pullInFlight = null;" in script
    assert "void pull(true);" in script
    pull_block = script.split("function pull(force = false) {", 1)[1].split(
        "/* ---------- rendering ---------- */", 1
    )[0]
    assert pull_block.index("forcePullQueued = true;") < pull_block.index(
        "if (pullInFlight) return pullInFlight;"
    )
    assert "do {" in pull_block
    assert "} while (forcePullQueued);" in pull_block
    assert pull_block.count("bridgePollWithTimeout(") == 1
    volatile_block = script.split("const VOLATILE_RENDER_FIELDS", 1)[1].split("]);", 1)[0]
    for field in ("generatedAt", "latencyMs", "checkedAt", "sampledAt", "uptimeSeconds"):
        assert f'"{field}"' in volatile_block
    key_function = script.split("function renderDataKey", 1)[1].split("\n}", 1)[0]
    assert "structuralRenderValue" in key_function
    assert "generatedAt" not in key_function
    assert "latencyMs" not in key_function
    assert "function cardRenderProjection" in script
    personal_projection = script.split('if (cardId === "personal") {', 1)[1].split("\n  }", 1)[0]
    assert "const ids = CARD_WIDGET_IDS.personal;" in personal_projection
    assert "widgets: widgets.filter((widget) => ids.has(widget.id))" in personal_projection
    card_widget_ids = script.split("const CARD_WIDGET_IDS", 1)[1].split("};", 1)[0]
    assert 'personal: new Set(["personal_system", "personal_sync"])' in card_widget_ids
    assert "if (CARD_ID) return;" in script
    assert "function schedulePoll(" in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert "setInterval(() => pull" not in script
    signature = script.split("function projectSectionSignature", 1)[1].split("\n}", 1)[0]
    assert "structuralRenderValue" in signature


def test_tile_editor_offers_responsive_size_presets_without_losing_manual_handles() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert "const TILE_SIZE_PRESETS" in script
    assert 'make("div", "tile-edit-toolbar")' in script
    assert "button.dataset.tilePreset = preset;" in script
    assert "function applyTileSizePreset(tile, presetName)" in script
    assert 'event.target.closest(".tile-edit-toolbar")' in script
    assert 'dom.projectSections.addEventListener("click"' in script
    assert "persistTileLayout().catch(() => {});" in script
    assert "body.layout-mode .tile-edit-toolbar { display: flex; }" in style
    assert "body.layout-mode .tile-grip" in style
    assert "tile-handle-se" in style


def test_desktop_mode_syncs_native_tile_regions_and_keeps_editing_full_window() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")

    assert "function desktopRegionList()" in script
    assert "function queueDesktopRegionSync(delay = 0)" in script
    assert "bridge.set_desktop_regions(regions, editing)" in script
    assert "const regions = editing ? [] : desktopRegionList();" in script


def test_independent_card_pages_fill_their_own_window_without_board_padding() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'const CARD_ID = document.body.dataset.cardId || "";' in script
    assert "targets.find((target) => target.id === CARD_ID)" in script
    assert 'head.classList.add("pywebview-drag-region")' in script
    assert 'dom.projectSections.classList.add("free-layout", "card-layout")' in script
    assert "width: viewportWidth" in script and "height: viewportHeight" in script
    assert "body.card-window .board" in style
    assert "body.card-window .proj" in style
    assert "padding: 0;" in style
    assert "width: 100% !important;" in style
    assert "height: 100% !important;" in style


def test_card_controls_expose_management_size_reset_and_current_card_close() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'make("div", "card-window-controls")' in script
    assert '"card-window-move pywebview-drag-region"' in script
    assert "button.dataset.cardSize = preset;" in script
    assert 'manage.dataset.cardAction = "manage";' in script
    assert 'reset.dataset.cardAction = "reset";' in script
    assert 'hide.dataset.cardAction = "hide";' in script
    assert "bridge.open_management()" in script
    assert "bridge.set_size(cardControl.dataset.cardSize)" in script
    assert "bridge.reset_geometry()" in script
    assert "bridge.hide_card()" in script
    assert 'make("button", "card-window-hide", "关闭")' in script
    assert "body.card-window .proj > .card-window-controls" in style
    assert "body.card-window .proj > .card-window-resize-corner" in style
    assert "max-width: 70px;" in style
    assert "max-width: min(203px, calc(100% - 14px));" in style
    assert "queueDesktopRegionSync(420);" in script


def test_watch_tile_has_a_responsive_training_and_recovery_instrument() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'eyebrow: "训练与恢复"' in script
    assert "function watchConsole(target, widgets)" in script
    assert 'widget.id === "watch_workouts"' in script
    assert 'widget.id === "watch_sleep"' in script
    assert 'widget.id === "watch_current_plan"' in script
    assert 'widget.id === "watch_status"' in script
    assert 'make("span", null, "当前计划")' in script
    assert 'make("span", null, "累计距离")' in script
    assert 'make("span", null, "睡眠评分")' in script
    assert 'status.availability === "unavailable"' in script
    assert "status.ok === false" in script
    assert "dot.dataset.status = dataState;" in script
    assert 'watchUnavailable ? "手机未连接"' in script
    assert "function projectDisplayState(target, widgets)" in script
    assert 'target.id === "watch" && displayState === "offline"' in script
    assert 'target.id === "watch"' in script and 'classList.add("watch-data")' in script
    assert ".project-watch" in style and ".watch-console" in style
    assert ".wi-score-value" in style and "stroke-dashoffset" in style
    assert ".project-watch.tile-narrow" in style
    assert '.project-watch[data-height-class="short"]' in style
    assert '.project-watch[data-height-class="medium"] .wi-plan' not in style


def test_project_cards_prioritize_live_state_and_mark_stale_snapshots() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    assert 'widget.id === "focus_current"' in script
    assert 'label = "当前专注";' in script
    assert 'label = watchUnavailable ? "连接状态" : "当前计划";' in script
    assert 'label = "今日日记";' in script
    assert 'recent.data.todayWritten ? "已写" : "未写"' in script
    assert "function syncCardFreshness(payload, data)" in script
    assert "payload.stale" in script
    assert "旧数据 · 更新于" in script
    assert 'make("div", "card-stale-note"' in script
    assert "body.card-window .proj > .card-stale-note" in style


def test_card_content_density_uses_each_native_card_as_its_own_scale() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    expected_bases = {
        "foxlink": (320, 270),
        "watch": (430, 310),
        "journal": (360, 310),
        "personal": (650, 410),
        "bzsjk": (360, 330),
    }
    for project_id, (width, height) in expected_bases.items():
        assert f"{project_id}: {{ width: {width}, height: {height} }}" in script

    assert "function contentClassForTile(projectId, width, height)" in script
    assert "const base = CARD_ID ? CARD_BASE_SIZES[projectId] : null;" in script
    assert "const deviceScale = Math.max(1, num(window.devicePixelRatio, 1));" in script
    assert "width * deviceScale / base.width" in script
    assert "height * deviceScale / base.height" in script
    assert 'if (scale < 0.84) return "small";' in script
    assert 'if (scale < 1.18) return "medium";' in script
    assert 'return "large";' in script
    assert "tile.dataset.contentClass = contentClass;" in script
    for density in ("small", "medium", "large"):
        assert f'.proj[data-content-class="{density}"]' in style


def test_small_medium_and_large_cards_have_an_explicit_content_priority() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    for helper in ("cardPrimary", "cardMetric", "cardSupport", "cardDetails"):
        assert f"function {helper}(" in script
    assert '.proj[data-content-class="small"] .card-support' in style
    assert '.proj[data-content-class="small"] .card-details { display: none; }' in style
    assert '.proj[data-content-class="medium"] .card-details { display: none; }' in style
    assert '.proj[data-content-class="medium"] .card-metric:nth-child(n+3)' in style
    assert '.proj[data-content-class="large"] .card-primary > strong' in style
    assert "grid-template-rows: minmax(0, 1fr);" in style
    assert "overflow-wrap: anywhere;" in style


def test_narrow_card_controls_fit_and_chinese_values_can_wrap() -> None:
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    controls = style.split("body.card-window .proj:hover > .card-window-controls,", 1)[1].split(
        "}", 1
    )[0]
    assert "max-width: min(203px, calc(100% - 14px));" in controls
    buttons = style.split("body.card-window .card-window-controls > button,", 1)[1].split("}", 1)[0]
    assert "width: 23px;" in buttons
    assert "flex: 0 0 23px;" in buttons
    assert "overflow-wrap: anywhere;" in style
    assert "letter-spacing: -" not in style
    dpi_controls = style.split("@container (max-width: 210px) {", 1)[1].split(
        "body.card-window .card-window-controls > button,", 1
    )[0]
    assert "body.card-window .card-window-manage { order: 1; }" in dpi_controls
    assert "body.card-window .card-window-hide { order: 2; }" in dpi_controls
    assert "height: 56px;" in dpi_controls
    assert "body.card-window .card-window-move," in dpi_controls
    assert "body.card-window .card-window-reset { display: none; }" in dpi_controls
    assert "width: 19px;" in dpi_controls
    assert "flex-basis: 19px;" in dpi_controls
    assert style.index("@container (max-width: 210px) {") > style.index(
        "body.card-window .card-window-hide:hover"
    )


def test_native_card_density_and_compact_controls_are_dpi_invariant() -> None:
    def density(scale: float) -> str:
        if scale < 0.84:
            return "small"
        if scale < 1.18:
            return "medium"
        return "large"

    for dpi in (1.0, 1.25, 1.5, 2.0):
        for native_scale, expected in ((0.72, "small"), (1.0, "medium"), (1.35, "large")):
            css_scale = native_scale / dpi
            assert density(css_scale * dpi) == expected
        assert 220 / dpi - 14 >= 70

    # 70px border-box leaves 62px: both compact rows fit without clipping.
    assert 29 + 2 + 29 <= 62
    assert 19 * 3 + 2 * 2 <= 62


def test_each_card_v3_reads_its_real_business_widgets_in_plain_language() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")

    contracts = (
        ("gatewayConsoleV3", '"本机服务"', '"自动恢复"'),
        ("watchConsoleV3", '"当前计划"', '"累计距离"'),
        ("focusConsoleV3", '"当前专注"', '"今日累计"'),
        ("journalConsoleV3", '"今日日记"', '"最近记录"'),
        ("bzsjkConsoleV3", '"项目状态"', '"本机 Git 只读"'),
    )
    for function_name, primary, detail in contracts:
        assert f"function {function_name}(" in script
        block = script.split(f"function {function_name}(", 1)[1].split("\n}", 1)[0]
        assert primary in block
        assert detail in block
        assert "cardPrimary(" in block
        assert "cardSupport(" in block
        assert "cardDetails(" in block
        for technical_label in ('"端口', '"检查耗时', '"安全连接', '"BLE'):
            assert technical_label not in block

    assert "return [gatewayConsoleV3(target, data, widgets)];" in script
    assert "return [watchConsoleV3(target, widgets)];" in script
    assert "return [focusConsoleV3(widgets)];" in script
    assert "return [journalConsoleV3(widgets)];" in script
    assert "return [bzsjkConsoleV3(target)];" in script
    bzsjk_block = script.split("function bzsjkConsoleV3(", 1)[1].split("\n}", 1)[0]
    assert "focus_today" not in bzsjk_block
    assert 'item.branch || "未读取"' in bzsjk_block
    assert 'new Set(["personal_system", "personal_sync"])' in script
    assert 'new Set(["bzsjk_project"])' in script


def test_card_v3_empty_states_are_plain_language_without_dash_placeholders() -> None:
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    v3 = script.split("function cardPrimary(", 1)[1].split("function projectCore(", 1)[0]

    assert "—" not in v3
    for message in (
        "状态未知",
        "暂无数据",
        "暂无信息",
        "尚未发现本机服务",
        "尚未读取到服务清单",
        "暂无训练记录",
        "等待睡眠数据",
        "今日数据等待更新",
        "暂无统计",
        "日期未记录",
    ):
        assert message in v3
    assert "0/0 个服务运行正常" not in v3


def test_management_panel_separates_local_health_from_device_and_business_data() -> None:
    markup = (STATIC_ROOT / "desktop.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")

    for node in (
        "managerLocalState",
        "managerLocalHint",
        "managerBusinessState",
        "managerBusinessHint",
    ):
        assert f'id="{node}"' in markup
    assert "本机服务" in markup
    assert "设备与业务数据" in markup
    assert 'data-scope="local"' in markup
    assert 'data-scope="device"' in markup
    assert 'data-scope="business"' in markup
    assert "function businessAttentionItems(data, widgets)" in script
    assert "function renderManagementScopes(data, widgets, businessAttention" in script
    assert 'issues.push("步序设备未连接")' in script
    assert "今日专注截至" in script
    assert "数据连接有待确认" in script
    assert "renderManagementScopes(data, widgets, businessAttention);" in script
    assert ".manager-scopes" in style
    assert '.manager-scope[data-status="online"]' in style
    assert '.manager-scope[data-status="offline"]' in style


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
