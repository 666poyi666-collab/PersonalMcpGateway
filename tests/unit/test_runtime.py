import asyncio
from pathlib import Path
from typing import Any

import pytest

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import (
    HealthState,
    ModuleHealth,
    ModuleManifest,
    PermissionLevel,
    ToolDefinition,
)
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database


class RuntimeModule:
    module_id = "demo"
    display_name = "Demo"
    version = "1"

    def __init__(self, handler: Any, *, enabled: bool = True) -> None:
        self.manifest = ModuleManifest(id="demo", display_name="Demo", enabled=enabled)
        self.handler = handler

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health(self) -> ModuleHealth:
        return ModuleHealth(state=HealthState.HEALTHY)

    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="demo_run",
                description="run",
                permission=PermissionLevel.READ,
                idempotent=True,
                timeout_seconds=1,
                handler=self.handler,
            )
        ]

    def resources(self) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_runtime_success_failure_and_unknown(tmp_path: Path) -> None:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("fail"):
            raise GatewayError("CONFLICT", "conflict")
        return {"value": 1}

    database = Database(tmp_path / "gateway.db")
    registry = ModuleRegistry()
    registry.register(RuntimeModule(handler))
    runtime = GatewayRuntime(Settings(data_dir=tmp_path), database, registry)
    await runtime.start()

    assert (await runtime.invoke("demo_run", {}))["ok"] is True
    failed = await runtime.invoke("demo_run", {"fail": True})
    assert failed["error"]["code"] == "CONFLICT"
    missing = await runtime.invoke("missing_tool", {})
    assert missing["error"]["code"] == "NOT_FOUND"
    assert runtime.calls_total == 3 and runtime.calls_failed == 2
    assert len(await runtime.recent_errors(999)) == 2
    assert (await runtime.system_status())["gateway"]["state"] == "online"
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_blocks_disabled_or_unavailable_module(tmp_path: Path) -> None:
    async def handler(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    database = Database(tmp_path / "gateway.db")
    registry = ModuleRegistry()
    module = RuntimeModule(handler, enabled=False)
    registry.register(module)
    runtime = GatewayRuntime(Settings(data_dir=tmp_path), database, registry)
    await runtime.start()
    assert (await runtime.invoke("demo_run", {}))["error"]["code"] == "MODULE_DISABLED"

    module.manifest.enabled = True
    registry.record_load_error("demo", GatewayError("AUTH_FAILED", "missing"))
    assert (await runtime.invoke("demo_run", {}))["error"]["code"] == "MODULE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_runtime_maps_tool_timeout(tmp_path: Path) -> None:
    async def handler(_: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(2)
        return {}

    module = RuntimeModule(handler)
    module.tools = lambda: [  # type: ignore[method-assign]
        ToolDefinition(
            name="demo_run",
            description="run",
            permission=PermissionLevel.READ,
            idempotent=True,
            timeout_seconds=0.001,
            handler=handler,
        )
    ]
    database = Database(tmp_path / "gateway.db")
    registry = ModuleRegistry()
    registry.register(module)
    runtime = GatewayRuntime(Settings(data_dir=tmp_path), database, registry)
    await runtime.start()
    result = await runtime.invoke("demo_run", {})
    assert result["error"]["code"] == "MODULE_TIMEOUT"
