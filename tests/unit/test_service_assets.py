import json
from pathlib import Path
from xml.etree import ElementTree


def test_gateway_service_uses_direct_private_python() -> None:
    root = Path(__file__).parents[2]
    service = ElementTree.parse(root / "service" / "gateway-service.xml").getroot()
    runner = (root / "service" / "run-gateway-service.ps1").read_text(encoding="utf-8")
    installer = (root / "service" / "install.ps1").read_text(encoding="utf-8")

    assert service.findtext("executable") == "%PRIVATE_PYTHON%"
    assert service.findtext("arguments") == ("-s -m personal_mcp_gateway.service_bootstrap serve")
    environment = {item.attrib["name"]: item.attrib["value"] for item in service.findall("env")}
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "WATCH_MCP_PHONE_TOKEN" not in runner
    assert "watch-token.dpapi" not in runner
    assert "modules\\watch.yaml" in installer
    assert "watch-token.dpapi" in installer


def test_runtime_acl_is_repaired_and_verified_before_service_start() -> None:
    root = Path(__file__).parents[2]
    installer = (root / "service" / "install.ps1").read_text(encoding="utf-8")
    updater = (root / "fleet" / "Update-PoyiFleet.ps1").read_text(encoding="utf-8")
    watchdog = (root / "fleet" / "watchdog.ps1").read_text(encoding="utf-8")
    config = json.loads((root / "fleet" / "fleet-config.json").read_text(encoding="utf-8"))
    personal = next(project for project in config["projects"] if project["id"] == "personal")

    assert config["schemaVersion"] == 1
    for script in (installer, updater):
        assert "Assert-ServiceRuntimeReadAccess" in script
        assert "uvicorn\\supervisors\\statreload.py" in script
        assert "watchfiles\\_rust_notify*.pyd" in script
        assert "/grant:r" in script
    assert updater.index("Assert-ServiceRuntimeReadAccess") < updater.index(
        "Write-Host '  service read ACLs re-stamped"
    )
    assert personal["installDir"].endswith(r"Poyi\PersonalMcpGateway")
    assert personal["grantRead"] == [
        r"NT SERVICE\PoyiPersonalMcpGateway",
        r"NT SERVICE\OpenAISecureMcpTunnel",
    ]
    assert any("statreload.py" in pattern for pattern in personal["verifyReadGlobs"])
    assert any("_rust_notify*.pyd" in pattern for pattern in personal["verifyReadGlobs"])
    assert "Repair-InstallDirAcls" in watchdog
    assert "Runtime read ACL baseline applied and verified." in watchdog


def test_watchdog_repair_is_scoped_and_cannot_bypass_restart_budget() -> None:
    root = Path(__file__).parents[2]
    watchdog = (root / "fleet" / "watchdog.ps1").read_text(encoding="utf-8")
    fleet_installer = (root / "fleet" / "Install-PoyiFleet.ps1").read_text(encoding="utf-8")

    assert "-Filter 'repair-*.json'" in watchdog
    assert r"^repair-([A-Za-z0-9_-]{1,32})\.json$" in watchdog
    assert "Get-ValidRepairRequests" in watchdog
    assert "Test-RepairWakePending" in watchdog
    assert "Get-ChildItem -LiteralPath $script:TriggerDir -File" not in watchdog
    assert "restart-state.json" in watchdog
    assert "Save-RestartState" in watchdog
    assert "Write-Output $line" not in watchdog
    assert "Write-Host $line" in watchdog
    assert "$Forced -or (Test-RestartAllowed" not in watchdog
    assert "Stop-PortListeners" not in watchdog
    assert "refusing to kill an unverified PID" in watchdog
    assert "restart/5000/restart/15000/restart/60000/none/0" in fleet_installer


def test_all_install_paths_cap_restart_loops_and_verify_live_readiness() -> None:
    root = Path(__file__).parents[2]
    service_installer = (root / "service" / "install.ps1").read_text(encoding="utf-8")
    fleet_updater = (root / "fleet" / "Update-PoyiFleet.ps1").read_text(encoding="utf-8")
    service_xmls = [
        ElementTree.parse(root / "service" / "gateway-service.xml").getroot(),
        ElementTree.parse(root / "service" / "tunnel-service.xml").getroot(),
        ElementTree.parse(root / "fleet" / "PoyiFleetWatchdog.xml").getroot(),
    ]

    for script in (service_installer, fleet_updater):
        assert "restart/5000/restart/15000/restart/60000/none/0" in script
        assert "Set-BoundedFailureActions" in script
    for service in service_xmls:
        actions = [
            (item.attrib["action"], item.attrib["delay"])
            for item in service.findall("onfailure")
        ]
        assert actions == [
            ("restart", "5 sec"),
            ("restart", "15 sec"),
            ("restart", "60 sec"),
            ("none", "0 sec"),
        ]
    assert "http://127.0.0.1:8761/readyz" in fleet_updater
    assert "http://127.0.0.1:8877/readyz" in fleet_updater
    assert "Copy-VerifiedFile" in fleet_updater
    assert "Installed file hash mismatch" in fleet_updater
    assert "dashboard\\board-widgets.poyi.yaml" in fleet_updater
    assert "board-widgets.yaml" in fleet_updater


def test_watchdog_install_and_update_restore_delayed_auto_start() -> None:
    root = Path(__file__).parents[2]
    scripts = [
        (root / "fleet" / "Install-PoyiFleet.ps1").read_text(encoding="utf-8"),
        (root / "fleet" / "Update-PoyiFleet.ps1").read_text(encoding="utf-8"),
    ]

    for script in scripts:
        assert "sc.exe config PoyiFleetWatchdog start= delayed-auto" in script
        assert "Failed to enable watchdog delayed auto-start." in script


def test_sensitive_install_acls_fail_closed_per_operation() -> None:
    root = Path(__file__).parents[2]
    installer = (root / "service" / "install.ps1").read_text(encoding="utf-8")

    assert "function Invoke-IcaclsStrict" in installer
    assert "if ($LASTEXITCODE -ne 0) { throw $FailureMessage }" in installer
    for protected_path in (
        "$DataDir, '/inheritance:r'",
        "$serviceLogDir, '/inheritance:r'",
        "$gatewayServiceLogDir, '/inheritance:r'",
        "$tunnelServiceLogDir, '/inheritance:r'",
        "$tunnelLogDir, '/inheritance:r'",
        "'runtime-key.dpapi'",
        "'tunnel-id'",
    ):
        assert protected_path in installer
