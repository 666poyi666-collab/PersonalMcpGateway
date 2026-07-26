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
WIDGET_TYPES = frozenset({"text", "agenda", "projects", "remote"})

# How long one widget's result stays good. The dashboard snapshot itself caches
# for 3 seconds; these only need to keep the slow providers off that hot path.
_TTL_SECONDS = {"text": 3600.0, "agenda": 15.0, "projects": 60.0, "remote": 0.0}

_PROVIDER_TIMEOUT = 4.0
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
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        # Validated here rather than at render time so a typo is one clear
        # message instead of a permanently erroring card.
        if value not in WIDGET_TYPES:
            raise ValueError(f"unknown widget type: {value}")
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
        try:
            async with asyncio.timeout(_PROVIDER_TIMEOUT):
                if config.type == "text":
                    payload = self._provide_text(config)
                elif config.type == "agenda":
                    payload = await asyncio.to_thread(self._provide_agenda, config)
                elif config.type == "projects":
                    payload = await asyncio.to_thread(self._provide_projects, config)
                else:
                    payload = await self._provide_remote(config)
        except TimeoutError:
            payload = self._fail(config, "获取超时")
        except Exception:  # one bad widget must never take the board down
            payload = self._fail(config, "模块运行失败")
        ttl = _TTL_SECONDS.get(config.type, 0.0)
        self._cache[config.id] = (time.monotonic() + ttl, fingerprint, payload)
        return payload

    def _base(self, config: WidgetConfig) -> dict[str, Any]:
        return {
            "id": config.id,
            "type": config.type,
            "title": config.title,
            "subtitle": config.subtitle,
        }

    def _ok(self, config: WidgetConfig, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return {**self._base(config), "kind": kind, "ok": True, "data": data}

    def _fail(self, config: WidgetConfig, message: str) -> dict[str, Any]:
        return {**self._base(config), "kind": "text", "ok": False, "error": message}

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
            "title": path.name or str(path),
            "subtitle": None,
            "value": None,
            "state": None,
        }
        if not path.is_dir():
            item["subtitle"] = "路径不存在"
            return item
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        if branch is None:
            item["subtitle"] = "不是 Git 仓库"
            return item
        dirty = _git(path, "status", "--porcelain")
        changed = len(dirty.splitlines()) if dirty else 0
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
        item["subtitle"] = _clip(f"{branch} · {_relative_zh(committed)} · {subject}")
        return item

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
