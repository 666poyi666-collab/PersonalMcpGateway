from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from personal_mcp_gateway.admin import fleet
from personal_mcp_gateway.admin.dashboard import DashboardMonitor, DashboardTarget

POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
WATCHDOG = Path(__file__).parents[2] / "fleet" / "watchdog.ps1"


def _powershell_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _run_watchdog_contract(trigger_dir: Path, body: str) -> dict[str, object]:
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for watchdog contract tests")
    script = f"""
. '{_powershell_literal(WATCHDOG)}'
$script:TriggerDir = '{_powershell_literal(trigger_dir)}'
$script:DataDir = '{_powershell_literal(trigger_dir.parent)}'
$script:LogPath = Join-Path $script:DataDir 'watchdog-test.log'
$script:MaintenanceFlag = Join-Path $script:DataDir 'maintenance.flag'
$script:LastForcedPass = [DateTime]::MinValue
function Write-Log([string]$Level, [string]$Message) {{ }}
function Write-FleetEvent([int]$EventId, [string]$Type, [string]$Message) {{ }}
function Repair-DataDirAcls($Config) {{ }}
function Repair-InstallDirAcls($Config, [string]$ServiceName = '') {{ return $true }}
function Invoke-FleetPass($Config, [bool]$InBootGrace, [bool]$Forced = $false) {{ }}
{body}
"""
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        timeout=20,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, completed.stderr
    result: object = json.loads(lines[-1])
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


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


def test_watchdog_consumes_python_repair_request_via_powershell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger_dir = tmp_path / "triggers"
    trigger_dir.mkdir()
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(trigger_dir))
    trigger = fleet.request_repair("desktop")
    lease = trigger_dir / ".repair-desktop.lease.json"

    result = _run_watchdog_contract(
        trigger_dir,
        """
$validBefore = @(Get-ValidRepairRequests).Count
$triggered = Test-RepairTrigger ([pscustomobject]@{})
[ordered]@{
    validBefore = $validBefore
    triggered = [bool]$triggered
    triggerExists = Test-Path -LiteralPath (Join-Path $script:TriggerDir 'repair-desktop.json')
    leaseExists = Test-Path -LiteralPath (Join-Path $script:TriggerDir '.repair-desktop.lease.json')
    wakePending = Test-RepairWakePending
} | ConvertTo-Json -Compress
""",
    )

    assert result == {
        "validBefore": 1,
        "triggered": True,
        "triggerExists": False,
        "leaseExists": True,
        "wakePending": False,
    }
    assert trigger.exists() is False
    assert lease.exists() is True


def test_watchdog_wake_ignores_invalid_expired_and_sidecar_files(
    tmp_path: Path,
) -> None:
    trigger_dir = tmp_path / "triggers"
    trigger_dir.mkdir()
    now = datetime.now(UTC)
    expired = {
        "schemaVersion": 1,
        "requestId": "7b7a67b3-97f5-4e2a-ac07-5cef46ec1f49",
        "requestedAt": (now - timedelta(minutes=11)).isoformat(),
        "source": "expired",
    }
    (trigger_dir / "repair-invalid.json").write_text("not-json", encoding="utf-8")
    (trigger_dir / "repair-expired.json").write_text(json.dumps(expired), encoding="utf-8")
    (trigger_dir / ".repair-desktop.lease.json").write_text("{}", encoding="utf-8")
    (trigger_dir / "repair-desktop.json.processing").write_text("{}", encoding="utf-8")

    result = _run_watchdog_contract(
        trigger_dir,
        """
[ordered]@{
    valid = @(Get-ValidRepairRequests).Count
    wakePending = Test-RepairWakePending
} | ConvertTo-Json -Compress
""",
    )

    assert result == {"valid": 0, "wakePending": False}


def test_watchdog_wake_respects_cooldown_and_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger_dir = tmp_path / "triggers"
    trigger_dir.mkdir()
    monkeypatch.setenv("POYI_FLEET_TRIGGER_DIR", str(trigger_dir))
    fleet.request_repair("desktop")

    result = _run_watchdog_contract(
        trigger_dir,
        """
$valid = @(Get-ValidRepairRequests).Count
$script:LastForcedPass = Get-Date
$duringCooldown = Test-RepairWakePending
$script:LastForcedPass = [DateTime]::MinValue
New-Item -ItemType File -Path $script:MaintenanceFlag -Force | Out-Null
$duringMaintenance = Test-RepairWakePending
Remove-Item -LiteralPath $script:MaintenanceFlag -Force
$ready = Test-RepairWakePending
[ordered]@{
    valid = $valid
    duringCooldown = $duringCooldown
    duringMaintenance = $duringMaintenance
    ready = $ready
} | ConvertTo-Json -Compress
""",
    )

    assert result == {
        "valid": 1,
        "duringCooldown": False,
        "duringMaintenance": False,
        "ready": True,
    }


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
