from __future__ import annotations

from personal_mcp_gateway.admin.routes import build_admin_app
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.mcp.server import build_mcp_server


def build_apps(runtime: GatewayRuntime):
    mcp = build_mcp_server(runtime)
    return mcp.streamable_http_app(), build_admin_app(runtime), mcp
