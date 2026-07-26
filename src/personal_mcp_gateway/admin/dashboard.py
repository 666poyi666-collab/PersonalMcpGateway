from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator

from personal_mcp_gateway.admin import fleet
from personal_mcp_gateway.admin.widgets import WidgetHub
from personal_mcp_gateway.core.runtime import GatewayRuntime


class DashboardTarget(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str
    icon: str
    accent: str
    health_url: str
    ready_url: str
    tunnel_ready_url: str | None = None
    mcp_service: str | None = None
    tunnel_service: str | None = None
    enabled: bool = True

    @field_validator("health_url", "ready_url", "tunnel_ready_url")
    @classmethod
    def require_loopback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        internal = parsed.scheme == "internal" and parsed.hostname == "gateway"
        loopback = parsed.scheme in {"http", "https"} and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if not internal and not loopback:
            raise ValueError("Dashboard targets must use loopback HTTP endpoints")
        return value


def _empty_targets() -> list[DashboardTarget]:
    return []


class DashboardConfig(BaseModel):
    targets: list[DashboardTarget] = Field(default_factory=_empty_targets)


DEFAULT_TARGETS = (
    DashboardTarget(
        id="personal",
        name="Personal Gateway",
        description="个人应用统一入口",
        icon="gateway",
        accent="#7c6cff",
        health_url="internal://gateway/healthz",
        ready_url="internal://gateway/readyz",
        tunnel_ready_url="http://127.0.0.1:8877/readyz",
        mcp_service="PoyiPersonalMcpGateway",
        tunnel_service="OpenAISecureMcpTunnel",
    ),
    DashboardTarget(
        id="watch",
        name="Watch MCP",
        description="训练、睡眠与手表控制",
        icon="watch",
        accent="#2dd4bf",
        health_url="http://127.0.0.1:8768/healthz",
        ready_url="http://127.0.0.1:8768/readyz",
        tunnel_ready_url="http://127.0.0.1:8880/readyz",
        mcp_service="PoyiWatchMcp",
        tunnel_service="PoyiWatchTunnel",
    ),
    DashboardTarget(
        id="foxlink",
        name="Foxlink MCP",
        description="Foxlink 数据与业务能力",
        icon="link",
        accent="#fb923c",
        health_url="http://127.0.0.1:8770/healthz",
        ready_url="http://127.0.0.1:8770/readyz",
        tunnel_ready_url="http://127.0.0.1:8878/readyz",
        mcp_service="PoyiFoxlinkMcp",
        tunnel_service="FoxlinkSecureMcpTunnel",
    ),
    DashboardTarget(
        id="journal",
        name="Journal MCP",
        description="日记、复盘与个人记录",
        icon="journal",
        accent="#f472b6",
        health_url="http://127.0.0.1:8780/healthz",
        ready_url="http://127.0.0.1:8780/readyz",
        tunnel_ready_url="http://127.0.0.1:8887/readyz",
        mcp_service="PoyiJournalMcp",
        tunnel_service="PoyiJournalTunnel",
    ),
)


class DashboardMonitor:
    def __init__(self, runtime: GatewayRuntime) -> None:
        self.runtime = runtime
        self.widgets = WidgetHub(runtime.settings)
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._cache_lock = asyncio.Lock()
        self._last_states: dict[str, str] = {}
        self._status_events: deque[dict[str, Any]] = deque(maxlen=40)

    async def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._cache is not None and time.monotonic() - self._cached_at < 3:
            return self._cache
        async with self._cache_lock:
            if not force and self._cache is not None and time.monotonic() - self._cached_at < 3:
                return self._cache
            self._cache = await self._build_snapshot()
            self._cached_at = time.monotonic()
            return self._cache

    async def _build_snapshot(self) -> dict[str, Any]:
        probe_started = time.perf_counter()
        targets, config_warning = self.targets()
        service_names = sorted(
            {
                name
                for target in targets
                for name in (target.mcp_service, target.tunnel_service)
                if name
            }
            | {fleet.WATCHDOG_SERVICE}
        )
        service_states, probed = await asyncio.gather(
            asyncio.to_thread(fleet.query_service_states, service_names),
            self._probe_all(targets),
        )
        target_states = self._attach_services(targets, probed, service_states)
        activity, recent, widgets = await asyncio.gather(
            self._activity(), self._recent_invocations(), self.widgets.snapshot()
        )
        errors = await self.runtime.recent_errors(8)
        calls_24h = sum(int(bucket["calls"]) for bucket in activity)
        failures_24h = sum(int(bucket["failures"]) for bucket in activity)
        states = [str(target["state"]) for target in target_states]
        self._record_state_changes(target_states)
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "refreshIntervalSeconds": 4,
            "probeDurationMs": round((time.perf_counter() - probe_started) * 1000),
            "gateway": {
                "state": "online" if self.runtime.ready else "starting",
                "version": self.runtime.settings.version,
                "uptimeSeconds": int(time.monotonic() - self.runtime.started_at),
                "callsTotal": self.runtime.calls_total,
                "callsFailed": self.runtime.calls_failed,
            },
            "summary": {
                "total": len(target_states),
                "online": states.count("online"),
                "degraded": states.count("degraded"),
                "offline": states.count("offline"),
                "calls24h": calls_24h,
                "failures24h": failures_24h,
                "successRate": round(
                    ((calls_24h - failures_24h) / calls_24h * 100) if calls_24h else 100.0,
                    1,
                ),
            },
            "targets": target_states,
            "widgets": widgets,
            "activity": {"hourly": activity, "recent": recent},
            "errors": errors,
            "events": list(self._status_events),
            "configWarning": config_warning,
            "fleet": {
                "watchdog": {
                    "service": fleet.WATCHDOG_SERVICE,
                    "state": service_states.get(fleet.WATCHDOG_SERVICE, "unknown"),
                },
                "repairSupported": fleet.repair_supported(),
            },
        }

    async def _probe_all(self, targets: list[DashboardTarget]) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            return list(
                await asyncio.gather(*(self._probe_target(client, target) for target in targets))
            )

    @staticmethod
    def _attach_services(
        targets: list[DashboardTarget],
        probed: list[dict[str, Any]],
        service_states: dict[str, str],
    ) -> list[dict[str, Any]]:
        for target, payload in zip(targets, probed, strict=True):
            if target.mcp_service and isinstance(payload.get("mcp"), dict):
                payload["mcp"]["service"] = {
                    "name": target.mcp_service,
                    "state": service_states.get(target.mcp_service, "unknown"),
                }
            if target.tunnel_service and isinstance(payload.get("tunnel"), dict):
                payload["tunnel"]["service"] = {
                    "name": target.tunnel_service,
                    "state": service_states.get(target.tunnel_service, "unknown"),
                }
        return probed

    def _record_state_changes(self, targets: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC).isoformat()
        current = {str(target["id"]): str(target["state"]) for target in targets}
        if self._last_states:
            names = {str(target["id"]): str(target["name"]) for target in targets}
            for target_id, state in current.items():
                previous = self._last_states.get(target_id)
                if previous is not None and previous != state:
                    self._status_events.appendleft(
                        {
                            "type": "status_change",
                            "target": target_id,
                            "name": names[target_id],
                            "fromState": previous,
                            "toState": state,
                            "occurredAt": now,
                        }
                    )
        self._last_states = current

    def targets(self) -> tuple[list[DashboardTarget], str | None]:
        targets = {target.id: target for target in DEFAULT_TARGETS}
        path = self.runtime.settings.dashboard_targets_path
        if not path.exists():
            return list(targets.values()), None
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            config = DashboardConfig.model_validate(document)
            for target in config.targets:
                if target.enabled:
                    # Override entries written before service names existed keep
                    # the default services for the same id.
                    default = targets.get(target.id)
                    if default is not None:
                        updates: dict[str, str] = {}
                        if target.mcp_service is None and default.mcp_service:
                            updates["mcp_service"] = default.mcp_service
                        if target.tunnel_service is None and default.tunnel_service:
                            updates["tunnel_service"] = default.tunnel_service
                        if updates:
                            target = target.model_copy(update=updates)
                    targets[target.id] = target
                else:
                    targets.pop(target.id, None)
        except (OSError, ValueError, yaml.YAMLError):
            return list(targets.values()), "dashboard-targets.yaml 配置无效; 正在使用默认目标"
        return list(targets.values()), None

    async def _probe_target(
        self, client: httpx.AsyncClient, target: DashboardTarget
    ) -> dict[str, Any]:
        if target.health_url.startswith("internal://"):
            health = {"ok": True, "latencyMs": 0, "status": 200, "version": None}
            ready = {
                "ok": self.runtime.ready,
                "latencyMs": 0,
                "status": 200 if self.runtime.ready else 503,
                "version": self.runtime.settings.version,
            }
        else:
            health, ready = await asyncio.gather(
                self._probe_url(client, target.health_url),
                self._probe_url(client, target.ready_url),
            )
        tunnel = (
            await self._probe_url(client, target.tunnel_ready_url)
            if target.tunnel_ready_url
            else None
        )
        if not health["ok"] or not ready["ok"]:
            state = "offline"
        elif tunnel is not None and not tunnel["ok"]:
            state = "degraded"
        else:
            state = "online"
        version = ready.get("version") or health.get("version")
        return {
            **target.model_dump(),
            "health_url": None,
            "ready_url": None,
            "tunnel_ready_url": None,
            "state": state,
            "version": version,
            "mcp": ready,
            "tunnel": tunnel,
        }

    @staticmethod
    async def _probe_url(client: httpx.AsyncClient, url: str | None) -> dict[str, Any]:
        if not url:
            return {"ok": False, "latencyMs": None, "status": None, "version": None}
        started = time.perf_counter()
        try:
            response = await client.get(url)
            latency = round((time.perf_counter() - started) * 1000)
            version: str | None = None
            try:
                payload: object = response.json()
                if isinstance(payload, dict):
                    payload_dict = cast(dict[str, Any], payload)
                    if payload_dict.get("version") is not None:
                        version = str(payload_dict["version"])
            except ValueError:
                pass
            return {
                "ok": response.status_code < 400,
                "latencyMs": latency,
                "status": response.status_code,
                "version": version,
            }
        except httpx.HTTPError:
            return {"ok": False, "latencyMs": None, "status": None, "version": None}

    async def _activity(self) -> list[dict[str, Any]]:
        rows = await self.runtime.database.fetchall(
            """
            SELECT strftime('%Y-%m-%dT%H:00:00Z', created_at) AS bucket,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN result = 'success' THEN 0 ELSE 1 END) AS failures
            FROM tool_invocations
            WHERE created_at >= datetime('now','-23 hours')
            GROUP BY bucket ORDER BY bucket
            """
        )
        indexed = {str(row["bucket"]): row for row in rows}
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        result: list[dict[str, Any]] = []
        for hours_ago in range(23, -1, -1):
            bucket_time = now.timestamp() - hours_ago * 3600
            bucket = datetime.fromtimestamp(bucket_time, UTC).strftime("%Y-%m-%dT%H:00:00Z")
            row: dict[str, Any] = indexed.get(bucket, {})
            result.append(
                {
                    "bucket": bucket,
                    "calls": int(row.get("calls", 0)),
                    "failures": int(row.get("failures", 0)),
                }
            )
        return result

    async def _recent_invocations(self) -> list[dict[str, Any]]:
        return await self.runtime.database.fetchall(
            """
            SELECT module_id AS module, tool_name AS tool, duration_ms AS durationMs,
                   result, created_at AS createdAt
            FROM tool_invocations ORDER BY id DESC LIMIT 8
            """
        )
