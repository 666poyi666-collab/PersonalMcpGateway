from pathlib import Path

import pytest

from personal_mcp_gateway.main import build_runtime
from personal_mcp_gateway.mcp.server import build_mcp_server, tool_names
from personal_mcp_gateway.settings import Settings


def test_tool_list_is_stable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERSONAL_MCP_SECRET_WATCH_PHONE_TOKEN", "test-pairing-code")
    repository_root = Path(__file__).parents[2]
    settings = Settings(data_dir=tmp_path, modules_dir=repository_root / "modules")
    server = build_mcp_server(build_runtime(settings))
    assert tool_names(server) == [
        "personal_diagnostics",
        "personal_get_module_status",
        "personal_get_recent_errors",
        "personal_list_modules",
        "personal_system_status",
        "watch_get_current_plan",
        "watch_get_latest_sleep",
        "watch_get_status",
        "watch_get_workout",
        "watch_list_plans",
        "watch_list_workouts",
        "watch_pause_workout",
        "watch_resume_workout",
        "watch_select_plan",
        "watch_set_plan",
        "watch_start_workout",
        "watch_stop_workout",
        "watch_summarize_sleep",
        "watch_sync_status",
    ]


def test_tool_list_stays_stable_without_watch_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PERSONAL_MCP_SECRET_WATCH_PHONE_TOKEN", raising=False)
    repository_root = Path(__file__).parents[2]
    settings = Settings(data_dir=tmp_path, modules_dir=repository_root / "modules")
    server = build_mcp_server(build_runtime(settings))

    assert "watch_get_status" in tool_names(server)
