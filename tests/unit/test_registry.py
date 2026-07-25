from typing import Any

import pytest

from personal_mcp_gateway.core.models import (
    HealthState,
    ModuleHealth,
    ModuleManifest,
    PermissionLevel,
    ResourceDefinition,
    ToolDefinition,
)
from personal_mcp_gateway.core.registry import ModuleRegistry


class FakeModule:
    display_name = "Fake"
    version = "1"

    def __init__(self, module_id: str, priority: int, tool_name: str) -> None:
        self.module_id = module_id
        self.manifest = ModuleManifest(id=module_id, display_name=module_id, priority=priority)
        self._tool_name = tool_name

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health(self) -> ModuleHealth:
        return ModuleHealth(state=HealthState.HEALTHY)

    async def handler(self, _: dict[str, Any]) -> dict[str, Any]:
        return {}

    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tool_name,
                description="test",
                permission=PermissionLevel.READ,
                idempotent=True,
                timeout_seconds=1,
                handler=self.handler,
            )
        ]

    def resources(self) -> list[ResourceDefinition]:
        return []


class FailingModule(FakeModule):
    async def start(self) -> None:
        raise RuntimeError("offline")


def test_module_and_tool_order_is_stable() -> None:
    registry = ModuleRegistry()
    registry.register(FakeModule("zeta", 20, "zeta_get_value"))
    registry.register(FakeModule("alpha", 10, "alpha_get_value"))
    assert [module.module_id for module in registry.modules()] == ["alpha", "zeta"]
    assert [tool.name for tool in registry.tools()] == ["alpha_get_value", "zeta_get_value"]


def test_duplicate_tool_is_rejected() -> None:
    registry = ModuleRegistry()
    registry.register(FakeModule("alpha", 10, "alpha_get_value"))
    with pytest.raises(ValueError, match="Duplicate tool names"):
        registry.register(FakeModule("beta", 20, "alpha_get_value"))


@pytest.mark.asyncio
async def test_start_failure_is_isolated_and_restart_is_serialized() -> None:
    registry = ModuleRegistry()
    good = FakeModule("alpha", 10, "alpha_get_value")
    bad = FailingModule("zeta", 20, "zeta_get_value")
    bad.manifest.retry.max_attempts = 1
    registry.register(good)
    registry.register(bad)
    await registry.start()
    assert (await registry.health("alpha")).state == HealthState.HEALTHY
    assert (await registry.health("zeta")).state == HealthState.UNAVAILABLE
    assert (await registry.restart("alpha")).state == HealthState.HEALTHY
    await registry.stop()
