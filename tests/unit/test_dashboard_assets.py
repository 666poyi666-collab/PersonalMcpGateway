from __future__ import annotations

from personal_mcp_gateway.admin.routes import STATIC_ROOT


def test_dashboard_assets_ship_as_a_local_bundle() -> None:
    markup = (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")
    profile = (STATIC_ROOT / "dashboard-profile.js").read_text(encoding="utf-8")

    assert 'href="/admin/assets/dashboard.css"' in markup
    assert 'src="/admin/assets/dashboard-profile.js"' in markup
    assert 'src="/admin/assets/dashboard.js"' in markup
    assert markup.index("dashboard-profile.js") < markup.index("dashboard.js")
    assert "http://" not in markup
    assert "https://" not in markup
    assert style and script and profile


def test_dashboard_profile_uses_local_encrypted_outbox_without_credentials() -> None:
    profile = (STATIC_ROOT / "dashboard-profile.js").read_text(encoding="utf-8")

    assert 'const DB_NAME = "poyi-dashboard-profile-v1"' in profile
    assert "indexedDB.open(DB_NAME, DB_VERSION)" in profile
    assert 'const key = existingKey ? null : await crypto.subtle.generateKey(' in profile
    assert '["encrypt", "decrypt"]' in profile
    assert 'database.transaction(["entities", "outbox", "meta"], "readwrite")' in profile
    assert "prepareExchange" in profile
    assert "applyExchange" in profile
    assert "exportKey" not in profile
    assert "Authorization" not in profile
    assert "fetch(" not in profile


def test_project_board_adapts_to_its_container_and_quarter_screen() -> None:
    style = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")

    assert "container: project-board / inline-size" in style
    assert "@container project-board (max-width: 760px)" in style
    assert "@container project-board (max-width: 380px)" in style
    assert "(max-width: 1280px) and (max-height: 800px)" in style
    assert 'card.dataset.priority = index < 2 ? "primary" : "secondary"' in script
    assert '[data-priority="secondary"]' in style


def test_project_art_directions_and_motion_fallback_are_preserved() -> None:
    style = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")

    for flavor in ("instrument", "sport", "paper", "neutral"):
        assert f".proj-{flavor}" in style
    assert "@keyframes instrument-scan" in style
    assert "@keyframes sport-drift" in style
    assert "@keyframes gateway-route" in style
    assert "@media (prefers-reduced-motion: reduce)" in style
    assert "body:not(.light) .proj-paper" in style
    assert "body:not(.light) .proj-instrument" in style


def test_project_tiles_support_pointer_drag_reordering() -> None:
    style = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")

    # A grip handle initiates a native drag; reordering persists through the same
    # encrypted layout profile the keyboard up/down buttons already use.
    assert ".proj-drag" in style
    assert ".proj.dragging" in style
    assert ".proj.drag-over-before" in style
    assert ".proj.drag-over-after" in style
    assert 'iconButton("grip"' in script
    assert 'section.addEventListener("dragstart"' in script
    assert "function reorderProject(" in script
    # Drag reorder must reuse profileState.layout, never introduce a new sync field.
    assert "profileState = { ...profileState, layout }" in script
