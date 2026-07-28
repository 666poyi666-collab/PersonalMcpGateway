from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from personal_mcp_gateway.admin.dashboard import get_dashboard_monitor
from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.result import failure, success
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.mcp.sdk_compat import create_fastmcp


def build_mcp_server(runtime: GatewayRuntime) -> FastMCP:
    mcp = create_fastmcp(runtime)
    dashboard = get_dashboard_monitor(runtime)

    @mcp.tool(description="Get gateway and module health", structured_output=True)
    async def personal_system_status() -> dict[str, Any]:
        return success("personal", "system_status", await runtime.system_status())

    @mcp.tool(
        description=(
            "Get cloud-authority sync freshness and safe operational summaries. "
            "Never returns encrypted product bodies, tokens, cookies, keys, or diagnostics."
        ),
        structured_output=True,
    )
    async def personal_cloud_sync_overview() -> dict[str, Any]:
        return success(
            "personal",
            "cloud_sync_overview",
            await dashboard.cloud_mcp_summary(),
        )

    @mcp.tool(description="List installed adapter modules", structured_output=True)
    async def personal_list_modules() -> dict[str, Any]:
        modules = [
            {
                "id": module.module_id,
                "displayName": module.display_name,
                "version": module.version,
                "enabled": module.manifest.enabled,
            }
            for module in runtime.registry.modules()
        ]
        modules.extend(
            {
                "id": module_id,
                "enabled": True,
                "state": "unavailable",
                "error": error.code,
            }
            for module_id, error in sorted(runtime.registry.load_errors().items())
        )
        return success("personal", "list_modules", {"modules": modules})

    @mcp.tool(description="Get one adapter module status", structured_output=True)
    async def personal_get_module_status(module_id: str) -> dict[str, Any]:
        try:
            health = await runtime.registry.health(module_id)
            return success(
                "personal",
                "module_status",
                health.model_dump(by_alias=True, exclude_none=True),
            )
        except GatewayError as exc:
            return failure("personal", "module_status", exc)

    @mcp.tool(description="Return redacted gateway diagnostics", structured_output=True)
    async def personal_diagnostics() -> dict[str, Any]:
        return success(
            "personal",
            "diagnostics",
            {
                "status": await runtime.system_status(),
                "database": str(runtime.settings.database_path.name),
                "mcpEndpoint": "/mcp",
                "adminEndpoint": f"127.0.0.1:{runtime.settings.admin_port}",
            },
        )

    @mcp.tool(description="Get recent redacted gateway errors", structured_output=True)
    async def personal_get_recent_errors(limit: int = 20) -> dict[str, Any]:
        return success("personal", "recent_errors", {"errors": await runtime.recent_errors(limit)})

    return mcp


def tool_names(mcp: FastMCP) -> list[str]:
    """Testing helper kept in the SDK boundary."""
    manager = mcp._tool_manager  # pyright: ignore[reportPrivateUsage]
    return sorted(manager._tools)  # pyright: ignore[reportPrivateUsage]
