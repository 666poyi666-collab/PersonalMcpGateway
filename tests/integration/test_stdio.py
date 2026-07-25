import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_initializes_and_lists_stable_tools(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PERSONAL_MCP_DATA_DIR"] = str(tmp_path)
    environment["PERSONAL_MCP_MODULES_DIR"] = str(Path(__file__).parents[2] / "modules")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "personal_mcp_gateway.main", "stdio"],
        env=environment,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            response = await session.list_tools()

    names = {tool.name for tool in response.tools}
    assert "personal_system_status" in names
    assert "watch_set_plan" in names
