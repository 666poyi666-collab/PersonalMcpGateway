from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from personal_mcp_gateway.core.runtime import GatewayRuntime


def create_fastmcp(runtime: GatewayRuntime) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]):
        await runtime.start()
        try:
            yield {"runtime": runtime}
        finally:
            await runtime.stop()

    return FastMCP(
        name="Personal MCP Gateway",
        stateless_http=True,
        json_response=True,
        host=runtime.settings.mcp_host,
        port=runtime.settings.mcp_port,
        streamable_http_path="/mcp",
        lifespan=lifespan,
    )
