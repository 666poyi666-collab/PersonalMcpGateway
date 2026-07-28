from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[2]
PLATFORM = ROOT / "standards" / "project-platform"
AUDIT_SCRIPT = PLATFORM / "scripts" / "audit-projects.ps1"
JsonObject = dict[str, Any]


class JsonValidator(Protocol):
    def validate(self, instance: object) -> None: ...

    def is_valid(self, instance: object) -> bool: ...


def load_json(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def powershell_executable() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the project-platform audit contract")
    return executable


def run_audit(registry: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(AUDIT_SCRIPT),
            "-RegistryPath",
            str(registry),
            "-ManifestStrict",
            "-ReportPath",
            str(report),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def manifest(
    project_id: str,
    name: str,
    *,
    data_policy: str = "local_only",
    mcp_status: str = "exempt",
    sync_status: str = "exempt",
    data_plane: str = "local_only",
    supports_pc_off: bool = False,
) -> JsonObject:
    authority = "none" if data_policy == "local_only" else "cloud"
    return {
        "standardVersion": 1,
        "id": project_id,
        "name": name,
        "runtimeProject": True,
        "dataPolicy": data_policy,
        "dataInventory": [
            {
                "id": "fixture-data",
                "classification": "internal",
                "dataPlane": data_plane,
                "mcpExposure": "none" if data_policy == "local_only" else "resource",
                "coverage": "exempt" if data_policy == "local_only" else "partial",
                "reason": "Fixture policy declaration.",
            }
        ],
        "mcp": {
            "status": mcp_status,
            "dataPlane": data_plane,
            "cloudBaseUrl": None,
            "routes": {
                "mcp": "/mcp",
                "health": "/healthz",
                "ready": "/readyz",
                "oauthResourceMetadata": "/.well-known/oauth-protected-resource/mcp",
            },
        },
        "sync": {
            "status": sync_status,
            "dataPlane": data_plane,
            "authority": authority,
            "routes": {
                "exchange": "/sync/v2/exchange",
                "status": "/sync/v2/status",
            },
            "contractId": "sync-envelope-v1",
            "envelopeVersion": 1,
            "supportsPcOff": supports_pc_off,
            "supportsBidirectionalDelta": supports_pc_off,
        },
        "knownGaps": [],
    }


def fixture_registry(tmp_path: Path) -> tuple[JsonObject, list[Path], Path]:
    roots = [tmp_path / f"repo-{index}" for index in range(6)]
    for root in roots:
        root.mkdir()
    publish_root = tmp_path / "repo-0-publish"
    publish_root.mkdir()

    projects: list[JsonObject] = []
    for index, root in enumerate(roots):
        repository_paths = [str(root)]
        repository_urls = [f"https://example.invalid/fixture-{index}"]
        if index == 0:
            repository_paths.append(str(publish_root))
            repository_urls.append("https://example.invalid/fixture-0-publish")
        projects.append(
            {
                "id": f"fixture-{index}",
                "name": f"Fixture {index}",
                "lifecycle": "active",
                "runtimeProject": True,
                "repositoryUrls": repository_urls,
                "repositoryPaths": repository_paths,
                "manifestRepositoryPath": str(root),
                "dataPolicy": "local_only",
                "manifestRelativePath": ".poyi/project-platform.json",
                "mcp": {
                    "status": "exempt",
                    "dataPlane": "local_only",
                    "coverage": "Fixture local-only exemption.",
                },
                "sync": {
                    "status": "exempt",
                    "dataPlane": "local_only",
                    "mode": "local_only",
                    "pcOffBehavior": "No cloud state.",
                },
                "requirementsMet": True,
                "priority": "exempt",
            }
        )

    registry: JsonObject = {
        "standardVersion": 1,
        "scope": {
            "officialRepositoryCount": 7,
            "activeRuntimeProjectCount": 6,
        },
        "canonicalRoutes": {
            "mcp": "/mcp",
            "health": "/healthz",
            "ready": "/readyz",
            "oauthResourceMetadata": "/.well-known/oauth-protected-resource/mcp",
            "syncExchange": "/sync/v2/exchange",
            "syncStatus": "/sync/v2/status",
            "pairOffers": "/sync/v1/pair/offers",
            "pairExchange": "/sync/v1/pair/exchange",
        },
        "projects": projects,
        "securityFindings": [],
        "policyExceptions": [],
        "supportServices": [],
    }
    return registry, roots, publish_root


def write_json(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_canonical_registry_gateway_manifest_and_focus_topology() -> None:
    registry = load_json(PLATFORM / "projects.json")
    schema = load_json(PLATFORM / "project-platform.schema.json")
    gateway_manifest = load_json(ROOT / ".poyi" / "project-platform.json")
    Draft202012Validator.check_schema(schema)
    validator = cast(JsonValidator, Draft202012Validator(schema))
    validator.validate(gateway_manifest)

    local_manifest = manifest("local-fixture", "Local Fixture")
    validator.validate(local_manifest)
    bad_mcp_plane = copy.deepcopy(local_manifest)
    cast(JsonObject, bad_mcp_plane["mcp"])["dataPlane"] = "cloud_primary"
    bad_sync_plane = copy.deepcopy(local_manifest)
    cast(JsonObject, bad_sync_plane["sync"])["dataPlane"] = "snapshot_mirror"
    bad_inventory_plane = copy.deepcopy(local_manifest)
    cast(list[JsonObject], bad_inventory_plane["dataInventory"])[0]["dataPlane"] = "cloud_primary"
    bad_inventory_exposure = copy.deepcopy(local_manifest)
    cast(list[JsonObject], bad_inventory_exposure["dataInventory"])[0]["mcpExposure"] = "resource"
    bad_inventory_coverage = copy.deepcopy(local_manifest)
    cast(list[JsonObject], bad_inventory_coverage["dataInventory"])[0]["coverage"] = "complete"
    local_only_violations = [
        bad_mcp_plane,
        bad_sync_plane,
        bad_inventory_plane,
        bad_inventory_exposure,
        bad_inventory_coverage,
    ]
    assert all(not validator.is_valid(candidate) for candidate in local_only_violations)

    active = [
        project
        for project in cast(list[JsonObject], registry["projects"])
        if project["lifecycle"] == "active" and project["runtimeProject"] is True
    ]
    assert len(active) == registry["scope"]["activeRuntimeProjectCount"] == 6
    assert len({project["manifestRepositoryPath"] for project in active}) == 6

    by_id = {project["id"]: project for project in active}
    assert by_id["suixinyiting"]["manifestRepositoryPath"] == "C:\\开发\\手表音乐软件"
    assert by_id["suixinyiting"]["dataPolicy"] == "cloud_allowed_with_media_exclusions"
    assert by_id["suixinyiting"]["mcp"]["status"] == "missing"
    assert by_id["suixinyiting"]["sync"]["status"] == "partial"
    assert by_id["do-not-phone"]["dataPolicy"] == "sensitive_cloud_allowed"
    assert by_id["do-not-phone"]["mcp"]["status"] == "partial"
    assert by_id["do-not-phone"]["sync"]["status"] == "partial"

    gateway = by_id["personal-mcp-gateway"]
    assert gateway["runtimeDiagnostics"] == {
        "dataPlane": "local_only",
        "allowedCloudRecords": ["last-heartbeat", "audit"],
        "createsStateWhilePcOff": False,
    }
    assert gateway_manifest["runtimeDiagnostics"] == gateway["runtimeDiagnostics"]
    assert gateway_manifest["mcp"]["cloudBaseUrl"] is None
    assert gateway_manifest["sync"]["supportsPcOff"] is False

    focus = by_id["focuslink"]
    foxlink_origin = "https://foxlink-mcp.focuslink-poyi-6465e9.workers.dev"
    assert focus["publicCloudBaseUrl"] == foxlink_origin
    assert focus["mcp"]["cloudBaseUrl"] == foxlink_origin
    assert focus["sync"]["cloudBaseUrl"] == foxlink_origin
    assert focus["internalAuthority"]["exposure"] == "edge_contained_noncanonical"
    assert focus["internalAuthority"]["publiclyReachable"] is False
    assert focus["internalAuthority"]["publicCanonical"] is False
    assert focus["internalAuthority"]["private"] is False
    assert focus["internalAuthority"]["registeredInManifest"] is False
    assert "focuslink-sync" not in json.dumps(registry, ensure_ascii=False)
    support = {
        service["id"]: service for service in cast(list[JsonObject], registry["supportServices"])
    }
    assert support["foxlink-cloud-mcp"]["publicCanonical"] is True
    assert support["foxlink-cloud-mcp"]["registeredInManifest"] is True
    assert support["foxlink-cloud-mcp"]["canonicalContractDeployed"] is False
    assert support["foxlink-cloud-mcp"]["deployed"] is False
    assert support["focuslink-device-sync-worker"]["publicCanonical"] is False
    assert support["focuslink-device-sync-worker"]["private"] is False
    assert support["focuslink-device-sync-worker"]["registeredInManifest"] is False
    assert "health" not in support["focuslink-device-sync-worker"]


def test_sync_envelope_v1_rejects_plaintext_and_legacy_drafts() -> None:
    schema = load_json(PLATFORM / "contracts" / "sync-envelope-v1.schema.json")
    request = load_json(PLATFORM / "contracts" / "sync-envelope-v1.request.fixture.json")
    response = load_json(PLATFORM / "contracts" / "sync-envelope-v1.response.fixture.json")
    Draft202012Validator.check_schema(schema)
    validator = cast(
        JsonValidator,
        Draft202012Validator(schema, format_checker=FormatChecker()),
    )
    validator.validate(request)
    validator.validate(response)
    assert response["protocolVersion"] == 2
    assert response["envelopeVersion"] == 1
    assert response["product"] == request["product"]

    invalid: list[JsonObject] = []

    plaintext = copy.deepcopy(request)
    plaintext["mutations"][0]["payload"] = {"must": "not reach cloud"}
    invalid.append(plaintext)

    old_operations = copy.deepcopy(request)
    old_operations["operations"] = old_operations.pop("mutations")
    invalid.append(old_operations)

    old_kind = copy.deepcopy(request)
    mutation = old_kind["mutations"][0]
    mutation["kind"] = mutation.pop("operation")
    invalid.append(old_kind)

    bad_cursor = copy.deepcopy(request)
    bad_cursor["cursor"] = "opaque-cursor"
    invalid.append(bad_cursor)

    unencrypted_response = copy.deepcopy(response)
    unencrypted_response["authority"] = "remote_authoritative"
    invalid.append(unencrypted_response)

    invalid_operation_id = copy.deepcopy(request)
    invalid_operation_id["mutations"][0]["opId"] = "op-not-a-uuid"
    invalid.append(invalid_operation_id)

    assert all(not validator.is_valid(candidate) for candidate in invalid)


def test_manifest_gate_scans_six_canonical_roots_and_rejects_publish_substitute(
    tmp_path: Path,
) -> None:
    registry, roots, publish_root = fixture_registry(tmp_path)
    registry_path = tmp_path / "projects.json"
    report_path = tmp_path / "audit.md"
    write_json(registry_path, registry)

    publish_manifest = manifest("fixture-0", "Fixture 0")
    write_json(publish_root / ".poyi" / "project-platform.json", publish_manifest)
    missing = run_audit(registry_path, report_path)
    assert missing.returncode == 2, missing.stdout + missing.stderr
    assert re.search(r"ManifestGaps\s*:\s*6", missing.stdout)

    projects = cast(list[JsonObject], registry["projects"])
    for project, root in zip(projects, roots, strict=True):
        write_json(
            root / ".poyi" / "project-platform.json",
            manifest(cast(str, project["id"]), cast(str, project["name"])),
        )
    complete = run_audit(registry_path, report_path)
    assert complete.returncode == 0, complete.stdout + complete.stderr
    assert re.search(r"ValidManifests\s*:\s*6", complete.stdout)
    assert re.search(r"ManifestGaps\s*:\s*0", complete.stdout)

    bad = load_json(roots[3] / ".poyi" / "project-platform.json")
    bad["id"] = "wrong-project"
    write_json(roots[3] / ".poyi" / "project-platform.json", bad)
    mismatch = run_audit(registry_path, report_path)
    assert mismatch.returncode == 1, mismatch.stdout + mismatch.stderr
    assert re.search(r"RegistryIssues\s*:\s*1", mismatch.stdout)


def test_fake_watch_complete_without_verifiable_evidence_fails(tmp_path: Path) -> None:
    registry, roots, _ = fixture_registry(tmp_path)
    projects = cast(list[JsonObject], registry["projects"])
    watch = projects[1]
    watch["id"] = "watchintervals"
    watch["name"] = "WatchIntervals"
    watch["dataPolicy"] = "cloud_allowed"
    watch["mcp"] = {
        "status": "partial",
        "dataPlane": "cloud_primary",
        "coverage": "Implementation in progress.",
    }
    watch["sync"] = {
        "status": "complete",
        "dataPlane": "cloud_primary",
        "mode": "bidirectional_delta",
        "pcOffBehavior": "Claimed complete without evidence.",
    }

    for project, root in zip(projects, roots, strict=True):
        if project is watch:
            value = manifest(
                "watchintervals",
                "WatchIntervals",
                data_policy="cloud_allowed",
                mcp_status="partial",
                sync_status="complete",
                data_plane="cloud_primary",
                supports_pc_off=True,
            )
        else:
            value = manifest(cast(str, project["id"]), cast(str, project["name"]))
        write_json(root / ".poyi" / "project-platform.json", value)

    registry_path = tmp_path / "projects.json"
    report_path = tmp_path / "audit.md"
    write_json(registry_path, registry)
    result = run_audit(registry_path, report_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert re.search(r"EvidenceGaps\s*:\s*1", result.stdout)
    assert "complete status has no verification declaration" in report_path.read_text(
        encoding="utf-8-sig"
    )
