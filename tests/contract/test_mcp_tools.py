import json
import time
from pathlib import Path

import pytest

from personal_mcp_gateway.admin.dashboard import get_dashboard_monitor
from personal_mcp_gateway.main import build_runtime
from personal_mcp_gateway.mcp.server import build_mcp_server, tool_names
from personal_mcp_gateway.settings import Settings


def test_tool_list_is_stable(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    settings = Settings(data_dir=tmp_path, modules_dir=repository_root / "modules")
    server = build_mcp_server(build_runtime(settings))
    assert tool_names(server) == [
        "personal_cloud_sync_overview",
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


@pytest.mark.asyncio
async def test_cloud_overview_tool_fails_closed_and_does_not_leak_snapshot_data(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[2]
    runtime = build_runtime(Settings(data_dir=tmp_path, modules_dir=repository_root / "modules"))
    server = build_mcp_server(runtime)
    monitor = get_dashboard_monitor(runtime)
    monitor._cache = {  # pyright: ignore[reportPrivateUsage]
        "generatedAt": "2026-07-28T12:00:00+00:00",
        "targets": [
            {
                "id": "journal",
                "sync": {
                    "pcOff": {
                        "readAvailable": True,
                        "writeAvailable": True,
                        "continuedSync": True,
                    },
                    "authorityTruth": {
                        "revision": 999_999,
                        "freshness": "fresh",
                        "lastVerifiedAt": "2099-01-01T00:00:00+00:00",
                        "pendingCount": 0,
                        "blockerReason": None,
                    },
                    "observation": {
                        "token": "token-secret-value",
                        "body": "body-secret-value",
                        "ciphertext": "ciphertext-secret-value",
                    },
                },
            }
        ],
        "widgets": [{"content": "widget-secret-value"}],
        "errors": [{"message": "error-secret-value"}],
    }
    monitor._cached_at = time.monotonic()  # pyright: ignore[reportPrivateUsage]
    tool = server._tool_manager._tools[  # pyright: ignore[reportPrivateUsage]
        "personal_cloud_sync_overview"
    ]

    result = await tool.run({})

    products = {product["productId"]: product for product in result["data"]["products"]}
    assert products["journal"] == {
        "productId": "journal",
        "revision": None,
        "freshness": "unknown",
        "lastVerifiedAt": None,
        "pendingCount": None,
        "blockerReason": None,
        "pcOff": {
            "readAvailable": False,
            "writeAvailable": False,
            "continuedSync": False,
        },
    }
    encoded = json.dumps(result, sort_keys=True)
    for forbidden in (
        "token-secret-value",
        "body-secret-value",
        "ciphertext-secret-value",
        "widget-secret-value",
        "error-secret-value",
        "widgets",
        "errors",
    ):
        assert forbidden not in encoded
    for forbidden_key in ("token", "body", "ciphertext", "widgets", "errors"):
        assert f'"{forbidden_key}"' not in encoded
