from __future__ import annotations

import asyncio
from collections.abc import Iterable

from personal_mcp_gateway.core.errors import GatewayError, ModuleDisabledError
from personal_mcp_gateway.core.models import (
    GatewayModule,
    HealthState,
    ModuleHealth,
    ResourceDefinition,
    ToolDefinition,
)


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, GatewayModule] = {}
        self._load_errors: dict[str, GatewayError] = {}
        self._lock = asyncio.Lock()

    def register(self, module: GatewayModule) -> None:
        if module.module_id in self._modules:
            raise ValueError(f"Duplicate module id: {module.module_id}")
        known_tools = {tool.name for item in self._modules.values() for tool in item.tools()}
        duplicates = known_tools.intersection(tool.name for tool in module.tools())
        if duplicates:
            raise ValueError(f"Duplicate tool names: {sorted(duplicates)}")
        self._modules[module.module_id] = module

    def record_load_error(self, module_id: str, error: GatewayError) -> None:
        self._load_errors[module_id] = error

    def load_errors(self) -> dict[str, GatewayError]:
        return dict(self._load_errors)

    def modules(self) -> list[GatewayModule]:
        return sorted(
            self._modules.values(),
            key=lambda item: (item.manifest.priority, item.module_id),
        )

    def tools(self) -> list[ToolDefinition]:
        return sorted(
            (tool for module in self.modules() for tool in module.tools()),
            key=lambda tool: (
                self._modules[tool.name.split("_", 1)[0]].manifest.priority,
                tool.name,
            ),
        )

    def resources(self) -> list[ResourceDefinition]:
        return sorted(
            (resource for module in self.modules() for resource in module.resources()),
            key=lambda resource: resource.uri,
        )

    async def start(self) -> None:
        for module in self.modules():
            if not module.manifest.enabled:
                continue
            try:
                await self._start_module(module)
            except Exception as exc:
                self.record_load_error(
                    module.module_id,
                    GatewayError(
                        "MODULE_UNAVAILABLE",
                        f"Module {module.module_id} failed to start",
                        retryable=True,
                        details={"type": type(exc).__name__},
                    ),
                )

    async def stop(self) -> None:
        for module in reversed(self.modules()):
            try:
                await asyncio.wait_for(
                    module.stop(), timeout=module.manifest.timeouts.connect_seconds
                )
            except Exception:
                continue

    async def restart(self, module_id: str) -> ModuleHealth:
        async with self._lock:
            module = self.get(module_id)
            if not module.manifest.enabled:
                raise ModuleDisabledError(module_id)
            await asyncio.wait_for(module.stop(), timeout=module.manifest.timeouts.connect_seconds)
            await self._start_module(module)
            self._load_errors.pop(module_id, None)
            return await module.health()

    async def _start_module(self, module: GatewayModule) -> None:
        last_error: Exception | None = None
        for attempt in range(module.manifest.retry.max_attempts):
            try:
                await asyncio.wait_for(
                    module.start(), timeout=module.manifest.timeouts.connect_seconds
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt + 1 < module.manifest.retry.max_attempts:
                    delay = module.manifest.retry.base_delay_ms / 1000 * (2**attempt)
                    await asyncio.sleep(delay)
        if last_error is not None:
            raise last_error

    def get(self, module_id: str) -> GatewayModule:
        try:
            return self._modules[module_id]
        except KeyError as exc:
            raise GatewayError("NOT_FOUND", f"Unknown module: {module_id}") from exc

    async def health(self, module_id: str) -> ModuleHealth:
        module = self.get(module_id)
        if not module.manifest.enabled:
            return ModuleHealth(state=HealthState.DISABLED)
        if module_id in self._load_errors:
            return ModuleHealth(
                state=HealthState.UNAVAILABLE,
                message=self._load_errors[module_id].message,
            )
        try:
            return await module.health()
        except Exception as exc:
            return ModuleHealth(state=HealthState.UNAVAILABLE, message=type(exc).__name__)

    async def all_health(self) -> dict[str, ModuleHealth]:
        result = {
            module.module_id: await self.health(module.module_id) for module in self.modules()
        }
        for module_id, error in self._load_errors.items():
            result.setdefault(
                module_id,
                ModuleHealth(state=HealthState.UNAVAILABLE, message=error.message),
            )
        return result

    def __iter__(self) -> Iterable[GatewayModule]:
        return iter(self.modules())
