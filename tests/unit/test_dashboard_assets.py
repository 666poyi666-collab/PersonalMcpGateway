from __future__ import annotations

from personal_mcp_gateway.admin.routes import STATIC_ROOT


def test_dashboard_assets_ship_as_a_local_bundle() -> None:
    markup = (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8")
    style = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")

    assert 'href="/admin/assets/dashboard.css"' in markup
    assert 'src="/admin/assets/dashboard.js"' in markup
    assert "http://" not in markup
    assert "https://" not in markup
    assert style and script


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
