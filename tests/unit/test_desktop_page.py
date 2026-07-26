from __future__ import annotations

from pathlib import Path

import pytest

from personal_mcp_gateway.desktop.page import STATIC_ROOT, build_page


def _stub(root: Path, *, css: str = "body{color:red}", script: str = "var a=1;") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "desktop.html").write_text(
        '<html><head><link rel="stylesheet" href="desktop.css"></head>'
        '<body><script src="desktop.js"></script></body></html>',
        encoding="utf-8",
    )
    (root / "desktop.css").write_text(css, encoding="utf-8")
    (root / "desktop.js").write_text(script, encoding="utf-8")
    return root


def test_the_real_page_inlines_both_assets() -> None:
    page = build_page()
    assert "<style>" in page and "<script>" in page
    # No external reference survives: WebView2 cannot resolve a relative path from an
    # `html=` document, and a non-ASCII install path breaks file:// entirely.
    assert 'href="desktop.css"' not in page
    assert 'src="desktop.js"' not in page


def test_the_inlined_page_carries_the_real_asset_content() -> None:
    page = build_page()
    css = (STATIC_ROOT / "desktop.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "desktop.js").read_text(encoding="utf-8")
    assert css in page
    assert script in page


def test_the_page_never_references_a_remote_origin() -> None:
    page = build_page()
    assert "http://" not in page
    assert "https://" not in page


def test_assets_are_inlined_from_an_explicit_root(tmp_path: Path) -> None:
    page = build_page(_stub(tmp_path / "static"))
    assert "<style>\nbody{color:red}\n</style>" in page
    assert "<script>\nvar a=1;\n</script>" in page


def test_content_that_would_break_out_of_its_tag_is_rejected(tmp_path: Path) -> None:
    root = _stub(tmp_path / "escape", script='var x = "</script><img src=x>";')
    with pytest.raises(ValueError, match="closing tag"):
        build_page(root)


def test_a_renamed_asset_tag_fails_loudly(tmp_path: Path) -> None:
    root = _stub(tmp_path / "drift")
    (root / "desktop.html").write_text("<html><body>nothing here</body></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="asset tags"):
        build_page(root)
