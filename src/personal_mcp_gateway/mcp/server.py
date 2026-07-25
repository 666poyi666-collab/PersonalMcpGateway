from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.result import failure, success
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.mcp.sdk_compat import create_fastmcp


def build_mcp_server(runtime: GatewayRuntime) -> FastMCP:
    mcp = create_fastmcp(runtime)

    @mcp.tool(description="Get gateway and module health", structured_output=True)
    async def personal_system_status() -> dict[str, Any]:
        return success("personal", "system_status", await runtime.system_status())

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

    write_annotations = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True
    )

    async def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await runtime.invoke(name, arguments)

    async def watch_get_status() -> dict[str, Any]:
        return await invoke("watch_get_status", {})

    async def watch_get_current_plan() -> dict[str, Any]:
        return await invoke("watch_get_current_plan", {})

    async def watch_list_plans() -> dict[str, Any]:
        return await invoke("watch_list_plans", {})

    async def watch_set_plan(
        requestId: str, expectedRevision: int, plan: dict[str, Any]
    ) -> dict[str, Any]:
        return await invoke(
            "watch_set_plan",
            {"requestId": requestId, "expectedRevision": expectedRevision, "plan": plan},
        )

    async def watch_select_plan(
        requestId: str, expectedRevision: int, planId: str
    ) -> dict[str, Any]:
        return await invoke(
            "watch_select_plan",
            {
                "requestId": requestId,
                "expectedRevision": expectedRevision,
                "planId": planId,
            },
        )

    async def watch_list_workouts(limit: int = 20) -> dict[str, Any]:
        return await invoke("watch_list_workouts", {"limit": limit})

    async def watch_get_workout(id: str) -> dict[str, Any]:
        return await invoke("watch_get_workout", {"id": id})

    async def watch_get_latest_sleep() -> dict[str, Any]:
        return await invoke("watch_get_latest_sleep", {})

    async def watch_summarize_sleep(days: int = 7) -> dict[str, Any]:
        return await invoke("watch_summarize_sleep", {"days": days})

    def control_tool(name: str):
        async def handler(
            requestId: str,
            expectedRevision: int,
            commandId: str,
            expectedState: str,
            expiresAt: int,
        ) -> dict[str, Any]:
            return await invoke(
                name,
                {
                    "requestId": requestId,
                    "expectedRevision": expectedRevision,
                    "commandId": commandId,
                    "expectedState": expectedState,
                    "expiresAt": expiresAt,
                },
            )

        return handler

    async def watch_sync_status() -> dict[str, Any]:
        return await invoke("watch_sync_status", {})

    for function, name, description, tool_annotations in [
        (watch_get_status, "watch_get_status", "Get phone, watch, and workout state", None),
        (watch_get_current_plan, "watch_get_current_plan", "Get the selected plan", None),
        (watch_list_plans, "watch_list_plans", "List plans", None),
        (watch_set_plan, "watch_set_plan", "Create or update a plan", write_annotations),
        (watch_select_plan, "watch_select_plan", "Select and sync a plan", write_annotations),
        (watch_list_workouts, "watch_list_workouts", "List workout summaries", None),
        (watch_get_workout, "watch_get_workout", "Get a workout summary", None),
        (watch_get_latest_sleep, "watch_get_latest_sleep", "Get latest sleep", None),
        (watch_summarize_sleep, "watch_summarize_sleep", "Summarize sleep", None),
        (
            control_tool("watch_start_workout"),
            "watch_start_workout",
            "Start workout",
            write_annotations,
        ),
        (
            control_tool("watch_pause_workout"),
            "watch_pause_workout",
            "Pause workout",
            write_annotations,
        ),
        (
            control_tool("watch_resume_workout"),
            "watch_resume_workout",
            "Resume workout",
            write_annotations,
        ),
        (
            control_tool("watch_stop_workout"),
            "watch_stop_workout",
            "Stop workout",
            write_annotations,
        ),
        (watch_sync_status, "watch_sync_status", "Get synchronization status", None),
    ]:
        mcp.add_tool(
            function,
            name=name,
            description=description,
            annotations=tool_annotations,
            structured_output=True,
        )

    watch_module = next(
        (module for module in runtime.registry.modules() if module.module_id == "watch"), None
    )
    if watch_module is not None:
        resource_handlers = {item.uri: item.handler for item in watch_module.resources()}

        @mcp.resource("watch://status", name="Watch status", mime_type="application/json")
        async def watch_status_resource() -> str:
            return await resource_handlers["watch://status"]({})

        @mcp.resource("watch://plans", name="Watch plans", mime_type="application/json")
        async def watch_plans_resource() -> str:
            return await resource_handlers["watch://plans"]({})

        @mcp.resource(
            "watch://workouts/recent", name="Recent workouts", mime_type="application/json"
        )
        async def watch_recent_workouts_resource() -> str:
            return await resource_handlers["watch://workouts/recent"]({})

        @mcp.resource("watch://workouts/{id}", name="Workout summary", mime_type="application/json")
        async def watch_workout_resource(id: str) -> str:
            return await resource_handlers["watch://workouts/{id}"]({"id": id})

        @mcp.resource(
            "watch://workouts/{id}/route",
            name="Workout route",
            mime_type="application/json",
        )
        async def watch_route_resource(id: str) -> str:
            return await resource_handlers["watch://workouts/{id}/route"]({"id": id})

        @mcp.resource("watch://sleep/latest", name="Latest sleep", mime_type="application/json")
        async def watch_latest_sleep_resource() -> str:
            return await resource_handlers["watch://sleep/latest"]({})

        @mcp.resource("watch://diagnostics", name="Watch diagnostics", mime_type="application/json")
        async def watch_diagnostics_resource() -> str:
            return await resource_handlers["watch://diagnostics"]({})

    return mcp


def tool_names(mcp: FastMCP) -> list[str]:
    """Testing helper kept in the SDK boundary."""
    manager = mcp._tool_manager  # pyright: ignore[reportPrivateUsage]
    return sorted(manager._tools)  # pyright: ignore[reportPrivateUsage]
