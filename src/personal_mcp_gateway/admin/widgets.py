"""Pluggable board widgets for the control center.

A *widget* is one card on the board: a schedule, a project's git freshness, a
note, or any local service that answers a small JSON contract. "Module" already
means an MCP backend in this codebase, so the board deliberately uses a second
word -- the two systems share nothing but the screen.

Widgets are declared in ``board-widgets.yaml`` in the gateway data directory and
picked up on the next snapshot, no restart needed. Every provider is wrapped so
a broken widget renders as an error card instead of taking the board down, and
``remote`` sources are restricted to loopback for the same reason dashboard
targets are: this process must never be talked into calling out.

Payloads carry repository basenames rather than full paths -- the snapshot only
ever travels to loopback UIs today, but nothing downstream should have to strip
paths if that changes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator

from personal_mcp_gateway.settings import Settings

WIDGET_KINDS = frozenset({"stat", "list", "keyvalue", "text"})
WIDGET_TYPES = frozenset({"text", "agenda", "projects", "remote", "mcp"})
# Presentation flavors let each source project keep its own art direction on the
# shared board: FocusLink's hairline instrument, the journal's ink-on-paper, the
# watch's dark sport dial. The renderers own the actual styles.
WIDGET_FLAVORS = frozenset({"neutral", "instrument", "paper", "sport"})

# The board is observational: an mcp widget may call a tool only when its name
# carries a read verb. Everything else -- start/stop/set/append and friends --
# is refused before a connection is even opened.
_READ_TOOL = re.compile(
    r"(^|_)(get|list|summarize|search|read|status|health|capabilities|overview)(_|$)"
)

# How long one widget's result stays good. The dashboard snapshot itself caches
# for 3 seconds; these only need to keep the slow providers off that hot path.
_TTL_SECONDS = {"text": 3600.0, "agenda": 15.0, "projects": 60.0, "remote": 0.0, "mcp": 30.0}
_MCP_TOOL_TTL_SECONDS = {
    "foxlink_get_current_session": 4.0,
    "watch_get_status": 4.0,
    "watch_get_current_plan": 15.0,
    "personal_system_status": 4.0,
    "personal_cloud_sync_overview": 15.0,
}

_PROVIDER_TIMEOUT = 4.0
# A dashboard observation must never hold the whole control center behind one
# offline phone. Providers that need longer should expose a cached summary.
_MCP_TIMEOUT = 3.0
_REMOTE_TIMEOUT = 2.5
_GIT_TIMEOUT = 3.0
_MAX_REPOS = 12
_MAX_LIST_ITEMS = 40
_MAX_PAIRS = 24
_MAX_TEXT = 2000
_CLIP = 300

_GETTING_STARTED_BODY = (
    "这里是你的自定义看板区: 日程、项目进度、新闻、股票、专注统计, 以后都接到这里。\n"
    "在网关数据目录 (Windows 服务默认 C:\\ProgramData\\Poyi\\PersonalMcpGateway) "
    "新建 board-widgets.yaml, 保存后几秒内自动出现, 无需重启。\n"
    "内置模块类型: text (文字便签)、agenda (日程)、projects (项目进度)、"
    "remote (自定义数据源), 写法示例见 docs/board-widgets.md。"
)


class WidgetConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: str
    title: str
    subtitle: str | None = None
    group: str | None = Field(default=None, max_length=24)
    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    flavor: str = "neutral"
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        # Validated here rather than at render time so a typo is one clear
        # message instead of a permanently erroring card.
        if value not in WIDGET_TYPES:
            raise ValueError(f"unknown widget type: {value}")
        return value

    @field_validator("flavor")
    @classmethod
    def known_flavor(cls, value: str) -> str:
        if value not in WIDGET_FLAVORS:
            raise ValueError(f"unknown widget flavor: {value}")
        return value


def _empty_widgets() -> list[WidgetConfig]:
    return []


class BoardConfig(BaseModel):
    widgets: list[WidgetConfig] = Field(default_factory=_empty_widgets)

    @field_validator("widgets")
    @classmethod
    def unique_ids(cls, value: list[WidgetConfig]) -> list[WidgetConfig]:
        seen: set[str] = set()
        for widget in value:
            if widget.id in seen:
                raise ValueError(f"duplicate widget id: {widget.id}")
            seen.add(widget.id)
        return value


def _clip(value: object, limit: int = _CLIP) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _repo_id(path: Path) -> str:
    name = path.name.casefold()
    known = {
        "personalmcpgateway": "personal",
        "journal-cloud-mcp": "journal_cloud",
        "手表开发": "watch_app",
        "日记复盘": "journal_app",
        "不做手机控": "bzsjk",
    }
    if name in known:
        return known[name]
    normalized = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return normalized or "project"


def _relative_zh(moment: datetime) -> str:
    seconds = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def _require_loopback(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def _sanitize_remote(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Copy only the fields the renderers know, with hard size caps.

    Remote widgets are other local services; a buggy one must be able to flood
    neither the snapshot nor the DOM.
    """
    if kind == "stat":
        return {
            "value": _clip(data.get("value", "—"), 40),
            "label": _clip(data.get("label", ""), 80),
            "note": _clip(data["note"], 120) if data.get("note") is not None else None,
        }
    if kind == "text":
        return {"body": _clip(data.get("body", ""), _MAX_TEXT)}
    if kind == "keyvalue":
        pairs_raw = data.get("pairs")
        pairs: list[dict[str, str]] = []
        if isinstance(pairs_raw, list):
            for entry in cast(list[Any], pairs_raw)[:_MAX_PAIRS]:
                if isinstance(entry, dict):
                    entry_dict = cast(dict[str, Any], entry)
                    pairs.append(
                        {
                            "label": _clip(entry_dict.get("label", ""), 80),
                            "value": _clip(entry_dict.get("value", ""), 120),
                        }
                    )
        return {"pairs": pairs}
    items_raw = data.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(items_raw, list):
        for entry in cast(list[Any], items_raw)[:_MAX_LIST_ITEMS]:
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            state = entry_dict.get("state")
            items.append(
                {
                    "title": _clip(entry_dict.get("title", ""), 120),
                    "subtitle": (
                        _clip(entry_dict["subtitle"])
                        if entry_dict.get("subtitle") is not None
                        else None
                    ),
                    "value": (
                        _clip(entry_dict["value"], 60)
                        if entry_dict.get("value") is not None
                        else None
                    ),
                    "state": state if state in {"online", "degraded", "offline"} else None,
                }
            )
    empty = data.get("empty")
    return {"items": items, "empty": _clip(empty, 120) if empty is not None else None}


class WidgetHub:
    """Loads the board config and runs every widget provider fault-isolated."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._cache: dict[str, tuple[float, str, dict[str, Any]]] = {}

    # ---- public ------------------------------------------------------
    async def snapshot(self) -> list[dict[str, Any]]:
        configs, error = self._load_config()
        if error is not None:
            return [
                {
                    "id": "board_config",
                    "type": "text",
                    "title": "扩展面板",
                    "subtitle": "board-widgets.yaml",
                    "kind": "text",
                    "ok": False,
                    "error": error,
                }
            ]
        if configs is None:
            return [self._getting_started()]
        return list(await asyncio.gather(*(self._run(config) for config in configs)))

    # ---- config ------------------------------------------------------
    def _load_config(self) -> tuple[list[WidgetConfig] | None, str | None]:
        path = self.settings.board_widgets_path
        if not path.exists():
            return None, None
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return BoardConfig.model_validate(document).widgets, None
        except (OSError, ValueError, yaml.YAMLError):
            return None, "board-widgets.yaml 配置无效; 写法见 docs/board-widgets.md"

    def _getting_started(self) -> dict[str, Any]:
        return {
            "id": "getting_started",
            "type": "text",
            "title": "扩展面板",
            "subtitle": "还没有自定义模块",
            "kind": "text",
            "ok": True,
            "data": {"body": _GETTING_STARTED_BODY},
        }

    # ---- execution ---------------------------------------------------
    async def _run(self, config: WidgetConfig) -> dict[str, Any]:
        fingerprint = config.model_dump_json()
        cached = self._cache.get(config.id)
        if cached is not None:
            expires, cached_fingerprint, payload = cached
            if cached_fingerprint == fingerprint and time.monotonic() < expires:
                return payload
        budget = _MCP_TIMEOUT if config.type == "mcp" else _PROVIDER_TIMEOUT
        try:
            async with asyncio.timeout(budget):
                if config.type == "text":
                    payload = self._provide_text(config)
                elif config.type == "agenda":
                    payload = await asyncio.to_thread(self._provide_agenda, config)
                elif config.type == "projects":
                    payload = await asyncio.to_thread(self._provide_projects, config)
                elif config.type == "mcp":
                    payload = await self._provide_mcp(config)
                else:
                    payload = await self._provide_remote(config)
        except TimeoutError:
            if config.type == "mcp":
                payload = self._mcp_unavailable(
                    config,
                    "数据暂时不可用 · 后台会自动重试",
                    "PROVIDER_TIMEOUT",
                )
            else:
                payload = self._fail(config, "获取超时")
        except Exception:  # one bad widget must never take the board down
            payload = self._fail(config, "模块运行失败")
        tool = config.options.get("tool") if config.type == "mcp" else None
        ttl = (
            _MCP_TOOL_TTL_SECONDS.get(tool, _TTL_SECONDS["mcp"])
            if isinstance(tool, str)
            else _TTL_SECONDS.get(config.type, 0.0)
        )
        if not payload.get("ok", False):
            # A disconnected device should stay local to its card and must not
            # start a multi-second retry on every dashboard poll.
            ttl = max(ttl, 15.0)
        elif config.type == "projects":
            payload["availability"] = "available"
            payload["freshness"] = "current"
            payload["sampledAt"] = datetime.now(UTC).isoformat()
        self._cache[config.id] = (time.monotonic() + ttl, fingerprint, payload)
        return payload

    def _base(self, config: WidgetConfig) -> dict[str, Any]:
        return {
            "id": config.id,
            "type": config.type,
            "title": config.title,
            "subtitle": config.subtitle,
            "group": config.group,
            "accent": config.accent,
            "flavor": config.flavor,
        }

    def _ok(self, config: WidgetConfig, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return {**self._base(config), "kind": kind, "ok": True, "data": data}

    def _fail(self, config: WidgetConfig, message: str) -> dict[str, Any]:
        return {**self._base(config), "kind": "text", "ok": False, "error": message}

    def _mcp_unavailable(
        self,
        config: WidgetConfig,
        message: str,
        error_code: str,
    ) -> dict[str, Any]:
        """Return a truthful business-data failure without raw MCP diagnostics."""
        return {
            **self._base(config),
            "kind": "text",
            "ok": False,
            "availability": "unavailable",
            "freshness": "unknown",
            "error": message,
            "errorCode": error_code,
            "data": {
                "body": message,
                "state": "unavailable",
                "reason": error_code,
            },
            "sampledAt": datetime.now(UTC).isoformat(),
        }

    # ---- providers ---------------------------------------------------
    def _provide_text(self, config: WidgetConfig) -> dict[str, Any]:
        body = config.options.get("body")
        if not isinstance(body, str) or not body.strip():
            return self._fail(config, "text 模块缺少 options.body")
        return self._ok(config, "text", {"body": _clip(body, _MAX_TEXT)})

    def _provide_agenda(self, config: WidgetConfig) -> dict[str, Any]:
        raw_file = config.options.get("file")
        name = raw_file if isinstance(raw_file, str) and raw_file.strip() else "agenda.yaml"
        path = Path(name)
        if not path.is_absolute():
            path = self.settings.data_dir / path
        empty_hint = f"把日程写进 {path.name} (格式见 docs/board-widgets.md)"
        if not path.exists():
            return self._ok(config, "list", {"items": [], "empty": empty_hint})
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError):
            return self._fail(config, f"{path.name} 无法解析")
        if not isinstance(document, list):
            return self._fail(config, f"{path.name} 应当是一个条目列表")
        limit = config.options.get("limit")
        keep = limit if isinstance(limit, int) and 0 < limit <= _MAX_LIST_ITEMS else 6
        today = datetime.now().date()
        upcoming: list[tuple[date, str, dict[str, Any]]] = []
        for entry in cast(list[Any], document):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            when = _coerce_date(entry_dict.get("date"))
            if when is None or when < today:
                continue
            clock = _coerce_clock(entry_dict.get("time"))
            upcoming.append((when, clock or "", entry_dict))
        upcoming.sort(key=lambda item: (item[0], item[1]))
        items: list[dict[str, Any]] = []
        for when, clock, entry_dict in upcoming[:keep]:
            day = (when - today).days
            label = "今天" if day == 0 else "明天" if day == 1 else when.strftime("%m-%d")
            items.append(
                {
                    "title": _clip(entry_dict.get("title", "(未命名)"), 120),
                    "subtitle": (
                        _clip(entry_dict["note"]) if entry_dict.get("note") is not None else None
                    ),
                    "value": f"{label} {clock}".strip(),
                    "state": None,
                }
            )
        return self._ok(config, "list", {"items": items, "empty": empty_hint})

    def _provide_projects(self, config: WidgetConfig) -> dict[str, Any]:
        repos_raw = config.options.get("repos")
        repos = (
            [entry for entry in cast(list[Any], repos_raw) if isinstance(entry, str)]
            if isinstance(repos_raw, list)
            else []
        )
        if not repos:
            hint = "在 options.repos 里列出项目路径 (见 docs/board-widgets.md)"
            return self._ok(config, "list", {"items": [], "empty": hint})
        if shutil.which("git") is None:
            return self._fail(config, "未找到 git, 无法读取项目状态")
        items = [self._scan_repo(Path(entry)) for entry in repos[:_MAX_REPOS]]
        return self._ok(config, "list", {"items": items, "empty": None})

    def _scan_repo(self, path: Path) -> dict[str, Any]:
        item: dict[str, Any] = {
            "repoId": _repo_id(path),
            "title": path.name or str(path),
            "subtitle": None,
            "value": None,
            "state": None,
            "branch": None,
            "dirtyCount": None,
            "lastCommitAt": None,
        }
        if not path.is_dir():
            item["subtitle"] = "路径不存在"
            return item
        top_level = _git(path, "rev-parse", "--show-toplevel")
        try:
            is_repository_root = (
                top_level is not None and Path(top_level).resolve() == path.resolve()
            )
        except OSError:
            is_repository_root = False
        if not is_repository_root:
            item["subtitle"] = "不是 Git 仓库"
            return item
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        if branch is None:
            item["subtitle"] = "不是 Git 仓库"
            return item
        dirty = _git(path, "status", "--porcelain")
        changed = len(dirty.splitlines()) if dirty else 0
        item["branch"] = branch
        item["dirtyCount"] = changed
        item["value"] = f"{changed} 处未提交" if changed else "工作区干净"
        head = _git(path, "log", "-1", "--format=%ct\t%s")
        if head is None:
            item["subtitle"] = f"{branch} · 尚无提交"
            return item
        stamp, _, subject = head.partition("\t")
        try:
            committed = datetime.fromtimestamp(int(stamp), UTC)
        except (OverflowError, ValueError):
            item["subtitle"] = _clip(f"{branch} · {subject}")
            return item
        item["lastCommitAt"] = committed.isoformat()
        item["subtitle"] = _clip(f"{branch} · {_relative_zh(committed)} · {subject}")
        return item

    async def _provide_mcp(self, config: WidgetConfig) -> dict[str, Any]:
        """Call one read-only tool on a loopback MCP and present its result.

        This is how the board goes deeper than health for the independent MCP
        projects: today's focus total from Foxlink, workout and sleep summaries
        from the watch, journal counts and recent titles from 拾光 -- all
        through the same tools ChatGPT uses, so there is no second data path.
        """
        url = config.options.get("url")
        if not isinstance(url, str) or not _require_loopback(url):
            return self._fail(config, "mcp 模块只接受 loopback 地址 (127.0.0.1)")
        tool = config.options.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return self._fail(config, "mcp 模块缺少 options.tool")
        tool = tool.strip()
        if not _READ_TOOL.search(tool):
            return self._fail(config, f"{tool} 不是只读工具; 看板只允许 get/list/summarize 类调用")
        args_raw = config.options.get("args")
        args = cast(dict[str, Any], args_raw) if isinstance(args_raw, dict) else {}
        try:
            payload = await _call_mcp_tool(url, tool, args)
        except Exception:
            return self._mcp_unavailable(
                config,
                "本机数据服务暂时无法访问",
                "MCP_UNREACHABLE",
            )
        if payload is None:
            return self._mcp_unavailable(config, "数据源没有返回可用摘要", "NO_DATA")
        if payload.get("isError"):
            error = payload.get("error")
            error_dict = cast(dict[str, Any], error) if isinstance(error, dict) else {}
            code = str(error_dict.get("code") or "")
            recovery_messages = {
                "PHONE_OFFLINE": "手机当前离线 · 等待后台服务或下一次云端同步恢复",
                "PHONE_TIMEOUT": "手机响应超时 · 等待后台服务恢复后自动重试",
                "WATCH_OFFLINE": "手表当前离线 · 等待连接恢复或下一次云端同步",
                "WATCH_TIMEOUT": "手表响应超时 · 等待连接恢复后自动重试",
            }
            if code in recovery_messages:
                return self._mcp_unavailable(config, recovery_messages[code], code)
            return self._mcp_unavailable(config, "数据源暂时不可用", code or "TOOL_ERROR")
        if payload.get("ok") is False:
            error = payload.get("error")
            error_dict = cast(dict[str, Any], error) if isinstance(error, dict) else {}
            code = str(error_dict.get("code") or "SOURCE_UNAVAILABLE")
            return self._mcp_unavailable(config, "数据源暂时不可用", code)
        kind, data = _present_mcp_payload(tool, payload)
        result = self._ok(config, kind, data)
        freshness = str(data.get("freshness") or "current")
        result["availability"] = "stale" if freshness == "stale" else "available"
        result["freshness"] = freshness
        result["sampledAt"] = datetime.now(UTC).isoformat()
        return result

    async def _provide_remote(self, config: WidgetConfig) -> dict[str, Any]:
        url = config.options.get("url")
        if not isinstance(url, str) or not _require_loopback(url):
            return self._fail(config, "remote 模块只接受 loopback 地址 (127.0.0.1)")
        try:
            async with httpx.AsyncClient(
                timeout=_REMOTE_TIMEOUT, trust_env=False, transport=self._transport
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload: object = response.json()
        except httpx.HTTPError:
            return self._fail(config, "数据源无法访问")
        except ValueError:
            return self._fail(config, "数据源返回的不是 JSON")
        if not isinstance(payload, dict):
            return self._fail(config, "数据源返回格式不正确")
        payload_dict = cast(dict[str, Any], payload)
        kind = payload_dict.get("kind")
        if kind not in WIDGET_KINDS:
            return self._fail(config, "数据源返回的 kind 不受支持")
        data = payload_dict.get("data")
        data_dict = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        result = self._ok(config, str(kind), _sanitize_remote(str(kind), data_dict))
        subtitle = payload_dict.get("subtitle")
        if isinstance(subtitle, str) and config.subtitle is None:
            result["subtitle"] = _clip(subtitle, 80)
        return result


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _coerce_clock(value: object) -> str | None:
    # PyYAML reads an unquoted 14:30 as the sexagesimal integer 870, so an int
    # here is almost always a time the operator forgot to quote. Turn it back.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value < 1440:
        return f"{value // 60:02d}:{value % 60:02d}"
    if isinstance(value, str):
        return value.strip() or None
    return None


async def _call_mcp_tool(url: str, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Run one tool against a loopback MCP and return its structured payload."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    if result.isError:
        error: dict[str, Any] = {}
        for item in result.content:
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            start = text.find("{")
            if start < 0:
                continue
            try:
                parsed: object = json.loads(text[start:])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                error = cast(dict[str, Any], parsed)
                break
        return {"isError": True, "error": error}
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                parsed: object = json.loads(text)
            except ValueError:
                return {"text": text}
            return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {"value": parsed}
    return None


def _unwrap_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    # Journal and Foxlink wrap results as {ok, data}; the watch returns bare.
    data = payload.get("data")
    if payload.get("ok") is True and isinstance(data, dict):
        return cast(dict[str, Any], data)
    return payload


def _fmt_hours(milliseconds: object) -> str:
    try:
        minutes = int(float(cast(Any, milliseconds)) / 60000)
    except (TypeError, ValueError):
        return "—"
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时 {minutes % 60:02d} 分"


def _fmt_km(meters: object) -> str:
    try:
        value = float(cast(Any, meters))
    except (TypeError, ValueError):
        return "—"
    return f"{value / 1000:.2f} km" if value >= 1000 else f"{value:.0f} m"


def _fmt_epoch_relative(milliseconds: object) -> str | None:
    try:
        moment = datetime.fromtimestamp(float(cast(Any, milliseconds)) / 1000, UTC)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return _relative_zh(moment)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(cast(Any, value)))
    except (TypeError, ValueError):
        return default


_MOOD_GLYPHS = {1: "低落", 2: "一般", 3: "平稳", 4: "不错", 5: "很好"}


def _present_mcp_payload(tool: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Shape a known tool's result for the board; unknown tools get keyvalue.

    Presenters are curated per tool from the real payloads each MCP returns
    today, so the cards read like the source project rather than raw JSON.
    Journal items deliberately surface date/title/mood only -- diary body text
    never reaches a board that hangs on a wall.
    """
    data = _unwrap_envelope(payload)
    if tool == "personal_system_status":
        gateway_raw = data.get("gateway")
        gateway = cast(dict[str, Any], gateway_raw) if isinstance(gateway_raw, dict) else {}
        modules_raw = data.get("modules")
        modules = cast(dict[str, Any], modules_raw) if isinstance(modules_raw, dict) else {}
        online = 0
        for module_raw in modules.values():
            if not isinstance(module_raw, dict):
                continue
            module = cast(dict[str, Any], module_raw)
            if str(module.get("state") or "").lower() == "online":
                online += 1
        module_count = len(modules)
        state = str(gateway.get("state") or "unknown").lower()
        uptime_seconds = _as_int(gateway.get("uptimeSeconds"))
        return "stat", {
            "value": "运行中" if state == "online" else "正在启动",
            "label": "控制中心",
            "note": f"已运行 {_fmt_hours(uptime_seconds * 1000)}",
            "gatewayState": state,
            "version": str(gateway.get("version") or "") or None,
            "uptimeSeconds": uptime_seconds,
            "moduleCount": module_count,
            "onlineModuleCount": online,
            "freshness": "current",
        }
    if tool == "personal_cloud_sync_overview":
        products_raw = data.get("products")
        products = cast(list[Any], products_raw) if isinstance(products_raw, list) else []
        states: list[str] = []
        for product_raw in products:
            if not isinstance(product_raw, dict):
                continue
            product = cast(dict[str, Any], product_raw)
            states.append(str(product.get("freshness") or "unknown"))
        fresh = states.count("fresh")
        blocked = states.count("blocked")
        stale = states.count("stale")
        unknown = len(states) - fresh - blocked - stale
        overall = "current" if products and fresh == len(products) else "stale"
        pairs = [
            {"label": "已确认", "value": str(fresh)},
            {"label": "待处理", "value": str(blocked)},
            {"label": "旧快照", "value": str(stale)},
            {"label": "待确认", "value": str(unknown)},
        ]
        return "keyvalue", {
            "pairs": pairs,
            "productCount": len(products),
            "freshCount": fresh,
            "blockedCount": blocked,
            "staleCount": stale,
            "unknownCount": unknown,
            "freshness": overall,
        }
    if tool == "foxlink_get_current_session":
        state = str(data.get("state") or "idle").strip().lower()
        title = str(data.get("currentTaskTitle") or "").strip()
        source = str(data.get("currentTaskSource") or "").strip()
        state_label = {
            "running": "正在专注",
            "paused": "专注已暂停",
            "idle": "当前无专注",
            "stopped": "当前无专注",
        }.get(state, "专注状态待确认")
        note_parts: list[str] = []
        if title:
            note_parts.append(f"已专注 {_fmt_hours(data.get('activeElapsedMs', 0))}")
        if source:
            note_parts.append(source)
        last_tick = _fmt_epoch_relative(data.get("lastTick"))
        if last_tick:
            note_parts.append(f"更新 {last_tick}")
        return "stat", {
            "value": _clip(title or state_label, 120),
            "label": state_label,
            "note": " · ".join(note_parts) or "等待下一次专注",
            "sessionState": state,
            "taskTitle": title or None,
            "taskSource": source or None,
            "activeMinutes": _as_int(data.get("activeElapsedMs")) // 60000,
            "pauseMinutes": _as_int(data.get("pauseElapsedMs")) // 60000,
            "freshness": "current",
        }
    if tool == "foxlink_get_today_summary":
        sessions = data.get("sessionCount", 0)
        note = f"{sessions} 次会话 · 暂停 {_fmt_hours(data.get('pauseElapsedMs', 0))}"
        source_date = str(data.get("date") or "").strip()
        is_today = source_date == datetime.now().date().isoformat()
        return "stat", {
            "value": _fmt_hours(data.get("activeElapsedMs", 0)),
            "label": (
                f"今日有效专注 · {source_date}"
                if is_today
                else f"旧数据 · 截至 {source_date or '未知日期'}"
            ),
            "note": note,
            "sourceDate": source_date or None,
            "isToday": is_today,
            "sessionCount": _as_int(sessions),
            "activeMinutes": _as_int(data.get("activeElapsedMs")) // 60000,
            "pauseMinutes": _as_int(data.get("pauseElapsedMs")) // 60000,
            "freshness": "current" if is_today else "stale",
        }
    if tool == "watch_get_status":
        connection_raw = data.get("connection")
        phone_raw = data.get("phone")
        connection = (
            cast(dict[str, Any], connection_raw) if isinstance(connection_raw, dict) else {}
        )
        phone = cast(dict[str, Any], phone_raw) if isinstance(phone_raw, dict) else {}
        watch_status_raw = connection.get("watchStatus")
        watch_status = (
            cast(dict[str, Any], watch_status_raw) if isinstance(watch_status_raw, dict) else {}
        )
        link_raw = phone.get("watchConnection")
        link = cast(dict[str, Any], link_raw) if isinstance(link_raw, dict) else {}
        phone_state = str(connection.get("phone") or "unknown").lower()
        watch_state = str(connection.get("watch") or "unknown").lower()
        transport = str(
            watch_status.get("transport")
            or link.get("primaryTransport")
            or link.get("bulkTransport")
            or "—"
        )
        ble_state = str(link.get("connectionState") or "unknown").upper()
        transport_label = {
            "ble": "蓝牙",
            "lan": "局域网",
            "multi": "自动选择",
            "—": "未连接",
        }.get(transport.lower(), "已连接")
        pairs = [
            {"label": "手机", "value": "在线" if phone_state == "online" else "离线"},
            {"label": "手表", "value": "在线" if watch_state == "online" else "离线"},
            {"label": "连接方式", "value": transport_label},
        ]
        return "keyvalue", {
            "pairs": pairs,
            "watchOnline": watch_state == "online",
            "phoneOnline": phone_state == "online",
            "bleConnected": ble_state == "CONNECTED",
            "phoneState": phone_state,
            "watchState": watch_state,
            "transport": transport if transport != "—" else None,
            "bleState": ble_state.lower(),
            "freshness": "current",
        }
    if tool == "watch_get_current_plan":
        name = str(data.get("name") or "").strip()
        group = str(data.get("group") or "").strip()
        stages_raw = data.get("stages")
        stages = cast(list[Any], stages_raw) if isinstance(stages_raw, list) else []
        label = " · ".join(part for part in (group, name) if part) or "暂无当前计划"
        pairs = [
            {"label": "当前计划", "value": _clip(label, 60)},
            {"label": "阶段数", "value": str(len(stages))},
        ]
        requirement = str(data.get("requirement") or "").strip()
        if requirement:
            pairs.append({"label": "要求", "value": _clip(requirement, 80)})
        return "keyvalue", {
            "pairs": pairs,
            "hasPlan": bool(name or group),
            "planName": name or None,
            "planGroup": group or None,
            "planLabel": label,
            "stageCount": len(stages),
            "requirement": requirement or None,
            "freshness": "current",
        }
    if tool == "watch_summarize_workouts":
        latest = data.get("latest")
        latest_dict = cast(dict[str, Any], latest) if isinstance(latest, dict) else {}
        plan = str(latest_dict.get("planName") or "").strip()
        plan_group = str(latest_dict.get("planGroup") or "").strip()
        pairs = [
            {"label": "训练次数", "value": str(data.get("workoutCount", 0))},
            {"label": "总距离", "value": _fmt_km(data.get("totalDistanceMeters", 0))},
            {"label": "累计活动", "value": _fmt_hours(data.get("totalActiveDurationMs", 0))},
            {"label": "平均心率", "value": f"{data.get('averageHeartRate', 0)} bpm"},
        ]
        if plan:
            label = f"{plan_group} · {plan}" if plan_group else plan
            pairs.append({"label": "最近计划", "value": _clip(label, 40)})
        return "keyvalue", {
            "pairs": pairs,
            "workoutCount": _as_int(data.get("workoutCount")),
            "totalDistanceMeters": _as_int(data.get("totalDistanceMeters")),
            "activeMinutes": _as_int(data.get("totalActiveDurationMs")) // 60000,
            "averageHeartRate": _as_int(data.get("averageHeartRate")),
            "latestPlanName": plan or None,
            "latestPlanGroup": plan_group or None,
            "freshness": "current",
        }
    if tool == "watch_get_latest_sleep":
        record = data.get("record")
        record_dict = cast(dict[str, Any], record) if isinstance(record, dict) else {}
        if not record_dict:
            return "text", {
                "body": "暂无睡眠记录",
                "hasRecord": False,
                "freshness": "current",
            }
        minutes = int(record_dict.get("totalDurationMinutes", 0) or 0)
        heart = record_dict.get("heartRateRangeBpm")
        heart_dict = cast(dict[str, Any], heart) if isinstance(heart, dict) else {}
        pairs = [
            {"label": "睡眠时长", "value": f"{minutes // 60} 小时 {minutes % 60:02d} 分"},
            {"label": "睡眠评分", "value": str(record_dict.get("sleepScore", "—"))},
        ]
        if heart_dict:
            low, high = heart_dict.get("minimum", "—"), heart_dict.get("maximum", "—")
            pairs.append({"label": "心率区间", "value": f"{low}-{high} bpm"})
        return "keyvalue", {
            "pairs": pairs,
            "hasRecord": True,
            "totalMinutes": minutes,
            "sleepScore": _as_int(record_dict.get("sleepScore")),
            "heartRateMinimum": _as_int(heart_dict.get("minimum")) if heart_dict else None,
            "heartRateMaximum": _as_int(heart_dict.get("maximum")) if heart_dict else None,
            "freshness": "current",
        }
    if tool == "journal_get_status":
        updated = str(data.get("latestUpdatedAt") or "")
        note = None
        if updated:
            try:
                moment = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                note = f"最近更新 {_relative_zh(moment)}"
            except ValueError:
                note = None
        return "stat", {
            "value": str(data.get("entryCount", 0)),
            "label": "日记总数",
            "note": note,
            "entryCount": _as_int(data.get("entryCount")),
            "latestUpdatedAt": updated or None,
            "freshness": "current",
        }
    if tool == "journal_list_recent":
        items_raw = data.get("items")
        items: list[dict[str, Any]] = []
        if isinstance(items_raw, list):
            for entry in cast(list[Any], items_raw)[:8]:
                if not isinstance(entry, dict):
                    continue
                entry_dict = cast(dict[str, Any], entry)
                mood = entry_dict.get("mood")
                mood_label = _MOOD_GLYPHS.get(mood) if isinstance(mood, int) else None
                items.append(
                    {
                        "title": _clip(str(entry_dict.get("title") or "(无标题)"), 60),
                        "subtitle": mood_label,
                        "value": str(entry_dict.get("date") or ""),
                        "state": None,
                        "mood": mood_label,
                        "hasImage": bool(entry_dict.get("hasImage")),
                    }
                )
        today = datetime.now().date().isoformat()
        latest_date = str(items[0].get("value") or "") if items else ""
        return "list", {
            "items": items,
            "empty": "还没有日记",
            "todayWritten": any(str(item.get("value") or "") == today for item in items),
            "latestEntryDate": latest_date or None,
            "freshness": "current",
        }
    # Unknown read tool: surface its scalar fields honestly rather than raw JSON.
    pairs = [
        {"label": _clip(key, 40), "value": _clip(value, 80)}
        for key, value in data.items()
        if isinstance(value, str | int | float | bool)
    ][:8]
    if pairs:
        return "keyvalue", {"pairs": pairs}
    return "text", {
        "body": "这个数据源还没有适合卡片展示的摘要",
        "freshness": "unknown",
    }


def _git(path: Path, *args: str) -> str | None:
    """Run one read-only git command; ``None`` means it failed or timed out.

    The gateway service runs as a virtual account, so git's dubious-ownership
    guard would reject every repository the operator owns. Trusting exactly the
    path the operator wrote into their own config restores the read -- and
    nothing else.
    """
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(  # fixed argv, no shell
            ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
