"""Board widget framework: config handling, providers, isolation, caching."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from personal_mcp_gateway.admin.widgets import WidgetHub
from personal_mcp_gateway.settings import Settings


def build_hub(tmp_path: Path, transport: httpx.AsyncBaseTransport | None = None) -> WidgetHub:
    return WidgetHub(Settings(data_dir=tmp_path), transport=transport)


def write_config(tmp_path: Path, body: str) -> None:
    (tmp_path / "board-widgets.yaml").write_text(body, encoding="utf-8")


async def test_no_config_shows_the_getting_started_card(tmp_path: Path) -> None:
    widgets = await build_hub(tmp_path).snapshot()
    assert [widget["id"] for widget in widgets] == ["getting_started"]
    assert widgets[0]["ok"] is True
    assert "board-widgets.yaml" in widgets[0]["data"]["body"]


async def test_invalid_config_becomes_one_clear_error_card(tmp_path: Path) -> None:
    write_config(tmp_path, "widgets:\n  - id: bad\n    type: nonsense\n    title: X\n")
    widgets = await build_hub(tmp_path).snapshot()
    assert len(widgets) == 1
    assert widgets[0]["ok"] is False
    assert "board-widgets.yaml" in widgets[0]["error"]


async def test_text_widget_renders_and_missing_body_fails_alone(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
widgets:
  - id: note
    type: text
    title: 便签
    options:
      body: 第一行\\n第二行
  - id: broken
    type: text
    title: 空的
""",
    )
    widgets = await build_hub(tmp_path).snapshot()
    by_id = {widget["id"]: widget for widget in widgets}
    assert by_id["note"]["ok"] is True
    assert by_id["note"]["kind"] == "text"
    assert by_id["broken"]["ok"] is False  # one bad widget never hides the rest


async def test_agenda_missing_file_is_an_empty_hint_not_an_error(tmp_path: Path) -> None:
    write_config(tmp_path, "widgets:\n  - id: agenda\n    type: agenda\n    title: 日程\n")
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is True
    assert widgets[0]["data"]["items"] == []
    assert "agenda.yaml" in widgets[0]["data"]["empty"]


async def test_agenda_filters_past_sorts_and_fixes_unquoted_times(tmp_path: Path) -> None:
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    (tmp_path / "agenda.yaml").write_text(
        f"""
- date: {today - timedelta(days=1)}
  title: 已经过去
- date: {tomorrow}
  time: 14:30
  title: 明天下午
- date: {today}
  time: "09:00"
  title: 今天早上
  note: 备注
""",
        encoding="utf-8",
    )
    write_config(tmp_path, "widgets:\n  - id: agenda\n    type: agenda\n    title: 日程\n")
    widgets = await build_hub(tmp_path).snapshot()
    items = widgets[0]["data"]["items"]
    assert [item["title"] for item in items] == ["今天早上", "明天下午"]
    assert items[0]["value"] == "今天 09:00"
    assert items[0]["subtitle"] == "备注"
    # PyYAML reads unquoted 14:30 as the sexagesimal int 870; it must come back.
    assert items[1]["value"] == "明天 14:30"


async def test_projects_reports_missing_paths_and_real_repos(tmp_path: Path) -> None:
    repo = tmp_path / "demo-project"
    plain = tmp_path / "plain-folder"
    plain.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-b", "main", str(repo))
    (repo / "a.txt").write_text("one", encoding="utf-8")
    git("-C", str(repo), "add", "a.txt")
    git("-C", str(repo), "commit", "-m", "first commit")
    (repo / "b.txt").write_text("dirty", encoding="utf-8")

    write_config(
        tmp_path,
        f"""
widgets:
  - id: projects
    type: projects
    title: 项目进度
    options:
      repos:
        - {repo}
        - {plain}
        - {tmp_path / "missing"}
""",
    )
    widgets = await build_hub(tmp_path).snapshot()
    items = widgets[0]["data"]["items"]
    assert [item["title"] for item in items] == ["demo-project", "plain-folder", "missing"]
    assert items[0]["value"] == "1 处未提交"
    assert items[0]["subtitle"].startswith("main · ")
    assert "first commit" in items[0]["subtitle"]
    assert items[1]["subtitle"] == "不是 Git 仓库"
    assert items[2]["subtitle"] == "路径不存在"


async def test_projects_without_repos_hints_at_the_docs(tmp_path: Path) -> None:
    write_config(tmp_path, "widgets:\n  - id: projects\n    type: projects\n    title: 项目\n")
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is True
    assert "options.repos" in widgets[0]["data"]["empty"]


async def test_remote_rejects_anything_that_is_not_loopback(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
widgets:
  - id: outside
    type: remote
    title: 外部
    options:
      url: https://example.com/widget
""",
    )
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is False
    assert "loopback" in widgets[0]["error"]


async def test_remote_passes_known_fields_and_caps_the_rest(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "kind": "list",
                "subtitle": "来自本机服务",
                "data": {
                    "items": [
                        {"title": "x" * 500, "value": 42, "state": "online", "extra": "dropped"}
                    ]
                    + [{"title": f"row {index}"} for index in range(60)],
                },
            },
        )

    write_config(
        tmp_path,
        """
widgets:
  - id: feed
    type: remote
    title: 数据源
    options:
      url: http://127.0.0.1:9999/widget
""",
    )
    widgets = await build_hub(tmp_path, transport=httpx.MockTransport(handler)).snapshot()
    widget = widgets[0]
    assert widget["ok"] is True
    assert widget["subtitle"] == "来自本机服务"
    items = widget["data"]["items"]
    assert len(items) == 40  # hard cap
    assert len(items[0]["title"]) <= 120
    assert items[0]["state"] == "online"
    assert "extra" not in items[0]


async def test_remote_rejects_unknown_kinds_and_broken_payloads(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        "weird": {"kind": "hologram", "data": {}},
        "flat": ["not", "a", "dict"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path.strip("/")])

    write_config(
        tmp_path,
        """
widgets:
  - id: weird
    type: remote
    title: A
    options: {url: "http://127.0.0.1:9999/weird"}
  - id: flat
    type: remote
    title: B
    options: {url: "http://127.0.0.1:9999/flat"}
""",
    )
    widgets = await build_hub(tmp_path, transport=httpx.MockTransport(handler)).snapshot()
    assert all(widget["ok"] is False for widget in widgets)


async def test_results_are_cached_until_the_config_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = WidgetHub._provide_text  # pyright: ignore[reportPrivateUsage]

    def counting(self: WidgetHub, config: Any) -> dict[str, Any]:
        calls["count"] += 1
        return original(self, config)

    monkeypatch.setattr(WidgetHub, "_provide_text", counting)
    write_config(
        tmp_path,
        "widgets:\n  - id: note\n    type: text\n    title: 一\n    options: {body: hi}\n",
    )
    hub = build_hub(tmp_path)
    first = await hub.snapshot()
    second = await hub.snapshot()
    assert calls["count"] == 1  # text TTL keeps the provider off the hot path
    assert first[0]["title"] == second[0]["title"] == "一"

    write_config(
        tmp_path,
        "widgets:\n  - id: note\n    type: text\n    title: 二\n    options: {body: hi}\n",
    )
    third = await hub.snapshot()
    assert calls["count"] == 2  # a config edit invalidates the cache at once
    assert third[0]["title"] == "二"
