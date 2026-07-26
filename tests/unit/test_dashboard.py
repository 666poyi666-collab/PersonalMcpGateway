from pathlib import Path

from personal_mcp_gateway.admin.dashboard import DashboardMonitor
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database


def build_monitor(tmp_path: Path) -> DashboardMonitor:
    settings = Settings(data_dir=tmp_path)
    runtime = GatewayRuntime(settings, Database(settings.database_path), ModuleRegistry())
    return DashboardMonitor(runtime)


def test_dashboard_targets_can_extend_and_hide_defaults(tmp_path: Path) -> None:
    monitor = build_monitor(tmp_path)
    tmp_path.joinpath("dashboard-targets.yaml").write_text(
        """
targets:
  - id: watch
    name: Watch MCP
    description: hidden
    icon: watch
    accent: '#fff'
    health_url: http://127.0.0.1:8768/healthz
    ready_url: http://127.0.0.1:8768/readyz
    enabled: false
  - id: demo
    name: Demo MCP
    description: local extension
    icon: service
    accent: '#60a5fa'
    health_url: http://localhost:8790/healthz
    ready_url: http://localhost:8790/readyz
""",
        encoding="utf-8",
    )

    targets, warning = monitor.targets()

    assert warning is None
    assert [target.id for target in targets] == ["personal", "foxlink", "journal", "demo"]


def test_dashboard_rejects_non_loopback_targets(tmp_path: Path) -> None:
    monitor = build_monitor(tmp_path)
    tmp_path.joinpath("dashboard-targets.yaml").write_text(
        """
targets:
  - id: remote
    name: Remote
    description: blocked
    icon: service
    accent: red
    health_url: https://example.invalid/healthz
    ready_url: https://example.invalid/readyz
""",
        encoding="utf-8",
    )

    targets, warning = monitor.targets()

    assert [target.id for target in targets] == ["personal", "watch", "foxlink", "journal"]
    assert warning is not None
