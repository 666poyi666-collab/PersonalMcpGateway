from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Any

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import ToolDefinition
from personal_mcp_gateway.core.redaction import redact
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.result import failure, success
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database

LOGGER = logging.getLogger("personal_mcp_gateway")


class GatewayRuntime:
    def __init__(self, settings: Settings, database: Database, registry: ModuleRegistry) -> None:
        self.settings = settings
        self.database = database
        self.registry = registry
        self.started_at = time.monotonic()
        self.ready = False
        self.calls_total = 0
        self.calls_failed = 0
        self._lifecycle_lock = asyncio.Lock()
        self._lifecycle_users = 0
        self._tools: dict[str, ToolDefinition] = {tool.name: tool for tool in registry.tools()}
        self.admin_token = self._load_or_create_local_token("admin-token")
        self.admin_csrf_token = self._load_or_create_local_token("admin-csrf-token")

    async def start(self) -> None:
        async with self._lifecycle_lock:
            self._lifecycle_users += 1
            if self.ready:
                return
            try:
                await self.database.migrate()
                await self.database.cleanup()
                await self.registry.start()
                self.ready = True
            except Exception:
                self._lifecycle_users -= 1
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._lifecycle_users > 0:
                self._lifecycle_users -= 1
            if self._lifecycle_users > 0 or not self.ready:
                return
            self.ready = False
            await self.registry.stop()

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = str(arguments.get("requestId") or f"req_{uuid.uuid4().hex}")
        module_id = tool_name.split("_", 1)[0]
        started = time.monotonic()
        outcome = "INTERNAL_ERROR"
        self.calls_total += 1
        try:
            definition = self._tools.get(tool_name)
            if definition is None:
                raise GatewayError("NOT_FOUND", f"Unknown tool: {tool_name}")
            module = self.registry.get(module_id)
            if not module.manifest.enabled:
                raise GatewayError("MODULE_DISABLED", f"Module {module_id} is disabled")
            load_error = self.registry.load_errors().get(module_id)
            if load_error is not None:
                raise GatewayError(
                    "MODULE_UNAVAILABLE",
                    f"Module {module_id} is unavailable",
                    retryable=True,
                )
            try:
                data = await asyncio.wait_for(
                    definition.handler(arguments), timeout=definition.timeout_seconds
                )
            except TimeoutError as exc:
                raise GatewayError(
                    "MODULE_TIMEOUT",
                    f"Tool {tool_name} exceeded its execution timeout",
                    retryable=True,
                ) from exc
            result = success(module_id, request_id, data, source=module_id)
            outcome = "success"
            return result
        except GatewayError as exc:
            self.calls_failed += 1
            outcome = exc.code
            await self.database.record_error(module_id, exc.code, exc.message, redact(exc.details))
            return failure(module_id, request_id, exc)
        except Exception as exc:
            self.calls_failed += 1
            outcome = "INTERNAL_ERROR"
            error = GatewayError(
                "INTERNAL_ERROR",
                "The gateway could not complete the request",
                details={"type": type(exc).__name__},
            )
            await self.database.record_error(module_id, error.code, error.message, error.details)
            return failure(module_id, request_id, error)
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self.database.execute(
                """
                INSERT INTO tool_invocations(request_id,module_id,tool_name,duration_ms,result)
                VALUES (?,?,?,?,?)
                """,
                (request_id, module_id, tool_name, duration_ms, outcome),
            )
            LOGGER.info(
                json.dumps(
                    {
                        "event": "tool_call_completed",
                        "requestId": request_id,
                        "module": module_id,
                        "tool": tool_name,
                        "durationMs": duration_ms,
                        "result": outcome,
                    },
                    separators=(",", ":"),
                )
            )

    async def system_status(self) -> dict[str, Any]:
        health = await self.registry.all_health()
        return {
            "gateway": {
                "state": "online" if self.ready else "starting",
                "version": self.settings.version,
                "uptimeSeconds": int(time.monotonic() - self.started_at),
            },
            "modules": {
                key: value.model_dump(by_alias=True, exclude_none=True)
                for key, value in health.items()
            },
        }

    async def recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.database.fetchall(
            """
            SELECT module_id,code,message,details_json,created_at FROM recent_errors
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        )
        for row in rows:
            row["details"] = json.loads(row.pop("details_json"))
        return rows

    def _load_or_create_local_token(self, name: str) -> str:
        path = self.settings.data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path.read_text(encoding="ascii").strip()
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="ascii")
        return token
