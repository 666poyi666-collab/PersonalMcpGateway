from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from personal_mcp_gateway.admin import fleet
from personal_mcp_gateway.admin.dashboard import DashboardMonitor, DashboardTarget


def test_request_repair_writes_trigger_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    written = fleet.request_repair("test")
    assert written.parent == tmp_path
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["requestId"]
    assert payload["source"] == "test"
    assert payload["requestedAt"]


def test_request_repair_coalesces_duplicate_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    first = fleet.request_repair("desktop")
    first_payload = first.read_text(encoding="utf-8")
    second = fleet.request_repair("desktop")

    assert second == first
    assert second.read_text(encoding="utf-8") == first_payload
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        ".repair-desktop.lease.json",
        "repair-desktop.json",
    ]


def test_request_repair_is_safe_under_thread_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))

    def request(_: int) -> Path:
        return fleet.request_repair("desktop")

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(request, range(128)))

    assert len(set(results)) == 1
    trigger = tmp_path / "repair-desktop.json"
    assert json.loads(trigger.read_text(encoding="utf-8"))["source"] == "desktop"
    assert list(tmp_path.glob("*.tmp")) == []


def test_consumed_request_stays_coalesced_during_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    trigger = fleet.request_repair("desktop")
    trigger.unlink()

    repeated = fleet.request_repair("desktop")

    assert repeated == trigger
    assert repeated.exists() is False


def test_corrupt_existing_request_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    trigger = tmp_path / "repair-desktop.json"
    trigger.write_text('{"schemaVersion":0}', encoding="utf-8")

    fleet.request_repair("desktop")

    payload = json.loads(trigger.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["source"] == "desktop"
    assert payload["requestId"]


def test_request_repair_rejects_unsafe_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        fleet.request_repair("../escape")
    assert list(tmp_path.iterdir()) == []


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
    result = DashboardMonitor._attach_services(  # pyright: ignore[reportPrivateUsage]
        [target], probed, states
    )
    assert result[0]["mcp"]["service"] == {"name": "PoyiWatchMcp", "state": "stopped"}
    assert result[0]["tunnel"]["service"] == {"name": "PoyiWatchTunnel", "state": "running"}


def test_attach_services_handles_missing_tunnel_probe():
    target = _target(mcp_service="PoyiWatchMcp", tunnel_service="PoyiWatchTunnel")
    probed = [{"mcp": {"ok": True}, "tunnel": None}]
    result = DashboardMonitor._attach_services(  # pyright: ignore[reportPrivateUsage]
        [target], probed, {"PoyiWatchMcp": "running"}
    )
    assert result[0]["tunnel"] is None
