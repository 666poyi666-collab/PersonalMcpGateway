from __future__ import annotations

from personal_mcp_gateway.desktop.native_window import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTLEFT,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    begin_window_resize,
    install_frameless_resize,
    resize_hit_test,
)


def test_resize_hit_test_covers_every_edge_and_corner() -> None:
    rect = (100, 200, 500, 600)
    assert resize_hit_test(rect, (101, 201), 10) == HTTOPLEFT
    assert resize_hit_test(rect, (499, 201), 10) == HTTOPRIGHT
    assert resize_hit_test(rect, (101, 599), 10) == HTBOTTOMLEFT
    assert resize_hit_test(rect, (499, 599), 10) == HTBOTTOMRIGHT
    assert resize_hit_test(rect, (101, 400), 10) == HTLEFT
    assert resize_hit_test(rect, (499, 400), 10) == HTRIGHT
    assert resize_hit_test(rect, (300, 201), 10) == HTTOP
    assert resize_hit_test(rect, (300, 599), 10) == HTBOTTOM
    assert resize_hit_test(rect, (300, 400), 10) is None


def test_native_resize_requires_a_real_window_handle() -> None:
    assert install_frameless_resize(object()) is False
    assert begin_window_resize(object(), "se") is False
    assert begin_window_resize(object(), "not-an-edge") is False
