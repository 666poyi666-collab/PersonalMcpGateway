from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_mcp_gateway.admin import fleet
from personal_mcp_gateway.admin.dashboard import DashboardMonitor, DashboardTarget


def test_request_repair_writes_trigger_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    written = fleet.request_repair("test")
    assert written.parent == tmp_path
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["source"] == "test"
    assert payload["requestedAt"]


def test_request_repair_fails_without_trigger_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path / "missing"))
    assert fleet.repair_supported() is False
    with pytest.raises(OSError):
        fleet.request_repair("test")


def _target(**overrides: object) -> DashboardTarget:
    values: dict[str, object] = {
        "id": "watch",
        "name": "Watch MCP",
        "description": "d",
        "icon": "watch",
        "accent": "#fff",
        "health_url": "http://127.0.0.1:8768/healthz",
        "ready_url": "http://127.0.0.1:8768/readyz",
    }
    values.update(overrides)
    return DashboardTarget.model_validate(values)


def test_attach_services_annotates_probes():
    target = _target(mcp_service="PoyiWatchMcp", tunnel_service="PoyiWatchTunnel")
    probed = [{"mcp": {"ok": False}, "tunnel": {"ok": True}}]
    states = {"PoyiWatchMcp": "stopped", "PoyiWatchTunnel": "running"}
    result = DashboardMonitor._attach_services([target], probed, states)
    assert result[0]["mcp"]["service"] == {"name": "PoyiWatchMcp", "state": "stopped"}
    assert result[0]["tunnel"]["service"] == {"name": "PoyiWatchTunnel", "state": "running"}


def test_attach_services_handles_missing_tunnel_probe():
    target = _target(mcp_service="PoyiWatchMcp", tunnel_service="PoyiWatchTunnel")
    probed = [{"mcp": {"ok": True}, "tunnel": None}]
    result = DashboardMonitor._attach_services([target], probed, {"PoyiWatchMcp": "running"})
    assert result[0]["tunnel"] is None
