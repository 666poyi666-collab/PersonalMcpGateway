from pathlib import Path

import pytest

from personal_mcp_gateway.main import build_runtime
from personal_mcp_gateway.mcp.server import build_mcp_server, tool_names
from personal_mcp_gateway.settings import Settings


def test_tool_list_is_stable(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    settings = Settings(data_dir=tmp_path, modules_dir=repository_root / "modules")
    server = build_mcp_server(build_runtime(settings))
    assert tool_names(server) == [
        "personal_diagnostics",
        "personal_get_module_status",
        "personal_get_recent_errors",
        "personal_list_modules",
        "personal_system_status",
    ]


@pytest.mark.asyncio
async def test_watch_surface_is_not_registered(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    settings = Settings(data_dir=tmp_path, modules_dir=repository_root / "modules")
    server = build_mcp_server(build_runtime(settings))

    assert all(not name.startswith("watch_") for name in tool_names(server))
    assert await server.list_resources() == []
    assert await server.list_resource_templates() == []
