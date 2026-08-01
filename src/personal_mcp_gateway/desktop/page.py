"""Assemble the desktop UI into one self-contained HTML document.

WebView2 refuses to load ``file://`` URIs whose path percent-encodes non-ASCII
characters, so an install under e.g. ``C:\\开发\\`` renders a blank window. Handing
the renderer a single inlined document removes the filesystem from the render path
entirely: it works from any install location, and the page keeps no origin, no
network access and nothing to resolve relative to.

The three source assets stay separate on disk for editing and testing; they are
spliced together here at startup.
"""

from __future__ import annotations

from pathlib import Path

STATIC_ROOT = Path(__file__).with_name("static")

_STYLE_LINK = '<link rel="stylesheet" href="desktop.css">'
_SCRIPT_TAG = '<script src="desktop.js"></script>'
_BODY_TAG = '<body class="booting desktop-mode" data-view="overview">'
_CARD_IDS = frozenset({"foxlink", "watch", "journal", "personal", "bzsjk"})


def _guard(asset: str, name: str) -> str:
    """Reject content that would terminate the tag it gets inlined into."""
    if "</script" in asset.lower() or "</style" in asset.lower():
        raise ValueError(f"{name} contains a closing tag that breaks inlining")
    return asset


def build_page(root: Path | None = None) -> str:
    """Return the full page with CSS and JS inlined."""
    base = root or STATIC_ROOT
    markup = (base / "desktop.html").read_text(encoding="utf-8")
    css = _guard((base / "desktop.css").read_text(encoding="utf-8"), "desktop.css")
    script = _guard((base / "desktop.js").read_text(encoding="utf-8"), "desktop.js")

    if _STYLE_LINK not in markup or _SCRIPT_TAG not in markup:
        raise ValueError("desktop.html no longer matches the expected asset tags")

    markup = markup.replace(_STYLE_LINK, f"<style>\n{css}\n</style>")
    return markup.replace(_SCRIPT_TAG, f"<script>\n{script}\n</script>")


def build_card_page(project_id: str, root: Path | None = None) -> str:
    """Return the shared renderer scoped to one independent desktop card."""
    if project_id not in _CARD_IDS:
        raise ValueError(f"unknown desktop card: {project_id}")
    page = build_page(root)
    if _BODY_TAG not in page:
        raise ValueError("desktop.html no longer matches the card page body tag")
    body = (
        '<body class="booting desktop-mode card-window" '
        f'data-view="overview" data-card-id="{project_id}">'
    )
    return page.replace(_BODY_TAG, body, 1)
