"""Board widget framework: config handling, providers, isolation, caching."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from personal_mcp_gateway.admin import widgets as widget_module
from personal_mcp_gateway.admin.widgets import (
    WidgetHub,
    _present_mcp_payload,  # pyright: ignore[reportPrivateUsage]
)
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
    assert items[0]["repoId"] == "demo_project"
    assert items[0]["branch"] == "main"
    assert items[0]["dirtyCount"] == 1
    assert items[0]["lastCommitAt"] is not None
    assert items[1]["subtitle"] == "不是 Git 仓库"
    assert items[1]["branch"] is None
    assert items[2]["subtitle"] == "路径不存在"
    assert widgets[0]["availability"] == "available"
    assert widgets[0]["freshness"] == "current"


async def test_projects_without_repos_hints_at_the_docs(tmp_path: Path) -> None:
    write_config(tmp_path, "widgets:\n  - id: projects\n    type: projects\n    title: 项目\n")
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is True
    assert "options.repos" in widgets[0]["data"]["empty"]


async def test_projects_does_not_treat_a_nested_folder_as_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "nested"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-b", "main", str(repo))
    nested.mkdir()
    write_config(
        tmp_path,
        f"""
widgets:
  - id: projects
    type: projects
    title: 项目
    options:
      repos:
        - {nested}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["data"]["items"][0]["subtitle"] == "不是 Git 仓库"


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


async def test_group_accent_and_flavor_flow_through_to_the_payload(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
widgets:
  - id: focus
    type: text
    title: 专注
    group: FocusLink
    accent: "#007A55"
    flavor: instrument
    options: {body: x}
""",
    )
    widgets = await build_hub(tmp_path).snapshot()
    widget = widgets[0]
    assert widget["group"] == "FocusLink"
    assert widget["accent"] == "#007A55"
    assert widget["flavor"] == "instrument"


async def test_bad_accent_or_flavor_is_a_config_error(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        "widgets:\n  - id: a\n    type: text\n    title: X\n    flavor: hologram\n",
    )
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is False

    write_config(
        tmp_path,
        "widgets:\n  - id: a\n    type: text\n    title: X\n    accent: red\n",
    )
    widgets = await build_hub(tmp_path).snapshot()
    assert widgets[0]["ok"] is False


async def test_mcp_widget_gate_rejects_bad_configs_without_network(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
widgets:
  - id: outside
    type: mcp
    title: A
    options: {url: "https://example.com/mcp", tool: foxlink_get_status}
  - id: writer
    type: mcp
    title: B
    options: {url: "http://127.0.0.1:8770/mcp", tool: foxlink_start_focus}
  - id: toolless
    type: mcp
    title: C
    options: {url: "http://127.0.0.1:8770/mcp"}
""",
    )
    widgets = await build_hub(tmp_path).snapshot()
    by_id = {widget["id"]: widget for widget in widgets}
    assert "loopback" in by_id["outside"]["error"]
    assert "只读" in by_id["writer"]["error"]
    assert "options.tool" in by_id["toolless"]["error"]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PHONE_OFFLINE", "手机当前离线"),
        ("PHONE_TIMEOUT", "手机响应超时"),
        ("WATCH_OFFLINE", "手表当前离线"),
        ("WATCH_TIMEOUT", "手表响应超时"),
    ],
)
async def test_mcp_device_unavailable_is_an_honest_neutral_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, expected: str
) -> None:
    async def offline(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"isError": True, "error": {"code": code}}

    monkeypatch.setattr(widget_module, "_call_mcp_tool", offline)
    write_config(
        tmp_path,
        """
widgets:
  - id: watch
    type: mcp
    title: 训练汇总
    options: {url: "http://127.0.0.1:8768/mcp", tool: watch_summarize_workouts}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["ok"] is False
    assert widget["kind"] == "text"
    assert widget["availability"] == "unavailable"
    assert widget["freshness"] == "unknown"
    assert widget["errorCode"] == code
    assert expected in widget["data"]["body"]


async def test_mcp_timeout_is_a_bounded_unavailable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stalled(*_args: object, **_kwargs: object) -> dict[str, Any]:
        await asyncio.sleep(60)
        return {}

    monkeypatch.setattr(widget_module, "_MCP_TIMEOUT", 0.001)
    monkeypatch.setattr(widget_module, "_call_mcp_tool", stalled)
    write_config(
        tmp_path,
        """
widgets:
  - id: watch
    type: mcp
    title: 训练汇总
    options: {url: "http://127.0.0.1:8768/mcp", tool: watch_summarize_workouts}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["ok"] is False
    assert widget["availability"] == "unavailable"
    assert widget["errorCode"] == "PROVIDER_TIMEOUT"
    assert widget["data"]["state"] == "unavailable"


async def test_wrapped_source_error_never_becomes_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"ok": False, "error": {"code": "DATA_NOT_READY", "message": "raw detail"}}

    monkeypatch.setattr(widget_module, "_call_mcp_tool", unavailable)
    write_config(
        tmp_path,
        """
widgets:
  - id: source
    type: mcp
    title: 来源
    options: {url: "http://127.0.0.1:8770/mcp", tool: foxlink_get_status}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["ok"] is False
    assert widget["availability"] == "unavailable"
    assert widget["errorCode"] == "DATA_NOT_READY"
    assert "raw detail" not in str(widget)


async def test_source_date_marks_old_today_summary_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def old_summary(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "date": "2026-01-01",
                "sessionCount": 3,
                "activeElapsedMs": 60_000,
                "pauseElapsedMs": 0,
            },
        }

    monkeypatch.setattr(widget_module, "_call_mcp_tool", old_summary)
    write_config(
        tmp_path,
        """
widgets:
  - id: focus_today
    type: mcp
    title: 今日专注
    options: {url: "http://127.0.0.1:8770/mcp", tool: foxlink_get_today_summary}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["ok"] is True
    assert widget["availability"] == "stale"
    assert widget["freshness"] == "stale"
    assert widget["data"]["isToday"] is False


async def test_personal_overview_is_an_allowed_read_only_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def overview(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {"products": [{"productId": "watch", "freshness": "unknown"}]},
        }

    monkeypatch.setattr(widget_module, "_call_mcp_tool", overview)
    write_config(
        tmp_path,
        """
widgets:
  - id: personal_sync
    type: mcp
    title: 数据连接
    options: {url: "http://127.0.0.1:8760/mcp", tool: personal_cloud_sync_overview}
""",
    )

    widget = (await build_hub(tmp_path).snapshot())[0]
    assert widget["ok"] is True
    assert widget["availability"] == "stale"
    assert widget["data"]["productCount"] == 1


def test_mcp_presenters_shape_the_real_payloads() -> None:
    kind, data = _present_mcp_payload(
        "personal_system_status",
        {
            "ok": True,
            "data": {
                "gateway": {"state": "online", "version": "1.2.3", "uptimeSeconds": 93},
                "modules": {
                    "watch": {"state": "online", "secret": "drop-me"},
                    "journal": {"state": "offline", "secret": "drop-me"},
                },
            },
        },
    )
    assert kind == "stat"
    assert data["gatewayState"] == "online"
    assert data["moduleCount"] == 2
    assert data["onlineModuleCount"] == 1
    assert data["uptimeSeconds"] == 93
    assert "drop-me" not in str(data)

    kind, data = _present_mcp_payload(
        "personal_cloud_sync_overview",
        {
            "ok": True,
            "data": {
                "products": [
                    {"productId": "watch", "freshness": "fresh", "private": "drop-me"},
                    {"productId": "foxlink", "freshness": "blocked"},
                    {"productId": "journal", "freshness": "unknown"},
                ]
            },
        },
    )
    assert kind == "keyvalue"
    assert data["productCount"] == 3
    assert data["freshCount"] == 1
    assert data["blockedCount"] == 1
    assert data["unknownCount"] == 1
    assert data["freshness"] == "stale"
    assert "drop-me" not in str(data)

    kind, data = _present_mcp_payload(
        "foxlink_get_current_session",
        {
            "ok": True,
            "data": {
                "state": "running",
                "sessionId": "secret-session-id",
                "currentSegmentId": "secret-segment-id",
                "currentTaskId": "secret-task-id",
                "currentTaskTitle": "复习立体几何",
                "currentTaskSource": "ticktick",
                "activeElapsedMs": 3_900_000,
                "lastTick": int(datetime.now().timestamp() * 1000),
            },
        },
    )
    assert kind == "stat"
    assert data["value"] == "复习立体几何"
    assert data["label"] == "正在专注"
    assert "1 小时 05 分" in data["note"]
    assert "secret-session-id" not in str(data)
    assert "secret-segment-id" not in str(data)
    assert "secret-task-id" not in str(data)
    assert data["activeMinutes"] == 65
    assert data["taskTitle"] == "复习立体几何"

    kind, data = _present_mcp_payload(
        "foxlink_get_today_summary",
        {
            "ok": True,
            "data": {
                "date": "2026-07-25",
                "sessionCount": 3,
                "activeElapsedMs": 10331904,
                "pauseElapsedMs": 10786202,
            },
        },
    )
    assert kind == "stat"
    assert data["value"] == "2 小时 52 分"
    assert "3 次会话" in data["note"]
    assert data["isToday"] is False
    assert data["freshness"] == "stale"
    assert data["activeMinutes"] == 172

    kind, data = _present_mcp_payload(
        "watch_get_current_plan",
        {
            "name": "day1",
            "group": "减肥",
            "stages": [{"kind": "RUN"}, {"kind": "WALK"}],
            "deviceId": "must-not-leak",
        },
    )
    assert kind == "keyvalue"
    pairs = {pair["label"]: pair["value"] for pair in data["pairs"]}
    assert pairs == {"当前计划": "减肥 · day1", "阶段数": "2"}
    assert data["planName"] == "day1"
    assert data["stageCount"] == 2
    assert "must-not-leak" not in str(data)

    kind, data = _present_mcp_payload(
        "watch_get_status",
        {
            "phone": {
                "phoneDeviceId": "must-not-leak",
                "watchConnection": {
                    "connectionState": "DISCONNECTED",
                    "bulkTransport": "lan",
                    "lanAvailable": True,
                    "lastDisconnectReason": "gatt_147",
                },
            },
            "connection": {
                "phone": "online",
                "watch": "online",
                "watchStatus": {"deviceId": "also-secret", "transport": "multi"},
            },
        },
    )
    assert kind == "keyvalue"
    pairs = {pair["label"]: pair["value"] for pair in data["pairs"]}
    assert pairs["手表"] == "在线"
    assert pairs["连接方式"] == "自动选择"
    assert "gatt_147" not in str(data)
    assert data["watchOnline"] is True
    assert data["phoneState"] == "online"
    assert data["bleState"] == "disconnected"
    assert "must-not-leak" not in str(data)
    assert "also-secret" not in str(data)

    kind, data = _present_mcp_payload(
        "watch_summarize_workouts",
        {
            "workoutCount": 11,
            "totalDistanceMeters": 2671.6,
            "totalActiveDurationMs": 7790154.0,
            "averageHeartRate": 105,
            "latest": {"planName": "day1", "planGroup": "减肥"},
        },
    )
    assert kind == "keyvalue"
    pairs = {pair["label"]: pair["value"] for pair in data["pairs"]}
    assert pairs["训练次数"] == "11"
    assert pairs["总距离"] == "2.67 km"
    assert pairs["最近计划"] == "减肥 · day1"
    assert data["workoutCount"] == 11
    assert data["activeMinutes"] == 129

    kind, data = _present_mcp_payload(
        "watch_get_latest_sleep",
        {
            "state": "ready",
            "record": {
                "totalDurationMinutes": 315,
                "sleepScore": 69,
                "heartRateRangeBpm": {"minimum": 55, "maximum": 69},
            },
        },
    )
    assert kind == "keyvalue"
    pairs = {pair["label"]: pair["value"] for pair in data["pairs"]}
    assert pairs["睡眠时长"] == "5 小时 15 分"
    assert pairs["心率区间"] == "55-69 bpm"
    assert data["hasRecord"] is True
    assert data["totalMinutes"] == 315
    assert data["sleepScore"] == 69


def test_journal_presenter_never_leaks_diary_body_text() -> None:
    today = datetime.now().date().isoformat()
    kind, data = _present_mcp_payload(
        "journal_list_recent",
        {
            "ok": True,
            "data": {
                "items": [
                    {
                        "date": today,
                        "title": "复盘",
                        "mood": 3,
                        "tags": ["生活"],
                        "summary": "这是绝不能上墙的私密正文",
                    }
                ]
            },
        },
    )
    assert kind == "list"
    item = data["items"][0]
    assert item["title"] == "复盘"
    assert item["value"] == today
    assert item["subtitle"] == "平稳"
    assert item["mood"] == "平稳"
    assert item["hasImage"] is False
    assert data["todayWritten"] is True
    assert data["latestEntryDate"] == today
    assert "私密正文" not in str(data)
    assert "生活" not in str(data)


def test_unknown_read_tool_falls_back_to_scalar_keyvalue() -> None:
    kind, data = _present_mcp_payload(
        "journal_get_status",
        {"ok": True, "data": {"entryCount": 7, "latestUpdatedAt": "bad-date"}},
    )
    assert kind == "stat"
    assert data["value"] == "7"
    assert data["note"] is None

    kind, data = _present_mcp_payload("something_get_odd", {"alpha": 1, "beta": "two"})
    assert kind == "keyvalue"
    assert {pair["label"] for pair in data["pairs"]} == {"alpha", "beta"}

    kind, data = _present_mcp_payload(
        "something_get_nested",
        {"private": {"body": "must-not-leak", "deviceId": "also-secret"}},
    )
    assert kind == "text"
    assert "must-not-leak" not in str(data)
    assert "also-secret" not in str(data)


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
