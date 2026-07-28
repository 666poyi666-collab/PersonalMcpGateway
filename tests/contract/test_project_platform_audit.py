from __future__ import annotations

import copy
import hashlib
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


def run_audit(
    registry: Path, report: Path, *, verify_evidence: bool = False
) -> subprocess.CompletedProcess[str]:
    extra_args = ["-VerifyEvidence"] if verify_evidence else []
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
            *extra_args,
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def write_evidence_summary(
    root: Path,
    evidence_id: str,
    kind: str,
    claim: str,
    *,
    producer_command_id: str = "fixture-tests",
) -> JsonObject:
    path = root / "evidence" / f"{evidence_id}.json"
    write_json(
        path,
        {
            "schemaVersion": 1,
            "id": evidence_id,
            "kind": kind,
            "result": "pass",
            "capturedAt": "2026-07-28T00:00:00+08:00",
            "producerCommandId": producer_command_id,
            "claimSha256": sha256_text(claim),
        },
    )
    return {
        "id": evidence_id,
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def directory_tree_sha256(root: Path, include_paths: list[str]) -> str:
    files: set[Path] = set()
    for include_path in include_paths:
        candidate = root / include_path
        if candidate.is_file():
            files.add(candidate)
        else:
            files.update(path for path in candidate.rglob("*") if path.is_file())
    records: list[str] = []
    for path in sorted(files, key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        records.append(f"{relative}\0{path.stat().st_size}\0{sha256_file(path)}")
    return hashlib.sha256(("\n".join(records) + "\n").encode()).hexdigest()


def release_set_sha256(source_deployments: list[JsonObject]) -> str:
    records: list[str] = []
    for deployment in sorted(source_deployments, key=lambda value: cast(str, value["id"])):
        records.append(
            "|".join(
                (
                    cast(str, deployment["id"]),
                    cast(str, deployment["kind"]),
                    cast(str, deployment.get("commit", "")),
                    cast(str, deployment["treeHashAlgorithm"]),
                    cast(str, deployment["treeHash"]).lower(),
                    cast(str, deployment["deployedVersion"]).strip(),
                    cast(str, deployment["deployedAt"]),
                )
            )
        )
    return sha256_text("\n".join(records) + "\n")


def initialize_git_source(root: Path) -> tuple[str, str]:
    (root / "source.txt").write_text("fixture source\n", encoding="utf-8")
    commands = [
        ["git", "init"],
        ["git", "config", "user.name", "Project Platform Test"],
        ["git", "config", "user.email", "project-platform@example.invalid"],
        ["git", "remote", "add", "origin", "https://example.invalid/fixture-0"],
        ["git", "add", "--all"],
        ["git", "commit", "-m", "fixture source"],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return commit, tree


def install_fixture_sync_contract(
    registry: JsonObject, registry_root: Path, consumer_root: Path
) -> None:
    artifact_names = [
        "sync-envelope-v1.schema.json",
        "sync-envelope-v1.request.fixture.json",
        "sync-envelope-v1.response.fixture.json",
    ]
    for name in artifact_names:
        source = PLATFORM / "contracts" / name
        for target_root in (registry_root, consumer_root):
            target = target_root / "contracts" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    registry["protocolContracts"] = [
        {
            "id": "sync-envelope-v1",
            "projectIds": ["fixture-0"],
            "schemaRelativePath": "contracts/sync-envelope-v1.schema.json",
            "requestFixtureRelativePath": ("contracts/sync-envelope-v1.request.fixture.json"),
            "responseFixtureRelativePath": ("contracts/sync-envelope-v1.response.fixture.json"),
            "consumers": [
                {
                    "id": "fixture-client",
                    "projectIds": ["fixture-0"],
                    "repositoryPath": str(consumer_root),
                    "schemaRelativePath": "contracts/sync-envelope-v1.schema.json",
                    "requestFixtureRelativePath": (
                        "contracts/sync-envelope-v1.request.fixture.json"
                    ),
                    "responseFixtureRelativePath": (
                        "contracts/sync-envelope-v1.response.fixture.json"
                    ),
                }
            ],
        }
    ]


def build_complete_verification(
    root: Path, commit: str, tree: str, *, directory_tree: str | None = None
) -> JsonObject:
    evidence_dir = root / "evidence"
    evidence_dir.mkdir()
    artifact_sha256 = sha256_text("fixture-apk")
    device_attestation_sha256 = sha256_text("fixture-physical-device-attestation")
    rounds: list[JsonObject] = []
    for index, mutation in enumerate(("create", "update", "delete"), start=1):
        rounds.append(
            {
                "round": index,
                "mutationKind": mutation,
                "sourceDeviceRole": "phone",
                "pcIsolationMode": "all-local-participants-stopped",
                "pcUnavailable": True,
                "localServicesStopped": True,
                "cloudRevisionBefore": f"c{index}",
                "cloudRevisionAfter": f"c{index + 1}",
                "mcpReadWhilePcUnavailable": True,
                "restartCatchupVerified": True,
                "exactlyOnceVerified": True,
                "tombstoneVerified": mutation == "delete",
                "startedAt": f"2026-07-28T0{index}:00:00+08:00",
                "finishedAt": f"2026-07-28T0{index}:01:00+08:00",
                "evidenceId": f"pc-off-{index}",
            }
        )

    tree_algorithm = "git-tree-sha256" if len(tree) == 64 else "git-tree-sha1"
    source_deployments: list[JsonObject] = [
        {
            "id": "fixture-source",
            "kind": "git",
            "commit": commit,
            "treeHashAlgorithm": tree_algorithm,
            "treeHash": tree,
            "deployedVersion": "fixture-deployment-v1",
            "deployedAt": "2026-07-28T00:00:00+08:00",
            "evidenceId": "source-deployment",
        }
    ]
    if directory_tree is not None:
        source_deployments.append(
            {
                "id": "fixture-oauth",
                "kind": "directory",
                "treeHashAlgorithm": "sha256-path-content-v1",
                "treeHash": directory_tree,
                "deployedVersion": "fixture-oauth-version-v1",
                "deployedAt": "2026-07-28T00:00:00+08:00",
                "evidenceId": "oauth-deployment",
            }
        )
    release_set = release_set_sha256(source_deployments)
    evidence_files: list[JsonObject] = []
    for source_deployment in source_deployments:
        evidence_files.append(
            write_evidence_summary(
                root,
                cast(str, source_deployment["evidenceId"]),
                "source-deployment",
                "|".join(
                    (
                        "source",
                        cast(str, source_deployment["id"]),
                        cast(str, source_deployment["kind"]),
                        cast(str, source_deployment.get("commit", "")),
                        cast(str, source_deployment["treeHashAlgorithm"]),
                        cast(str, source_deployment["treeHash"]).lower(),
                        cast(str, source_deployment["deployedVersion"]),
                        cast(str, source_deployment["deployedAt"]),
                        release_set,
                    )
                ),
            )
        )
    evidence_files.extend(
        [
            write_evidence_summary(
                root,
                "remote-health",
                "remote-probe",
                "|".join(
                    (
                        "probe",
                        "health",
                        "GET",
                        "https://fixture.example.invalid/healthz",
                        "200",
                        "fixture-source",
                        "fixture-deployment-v1",
                        release_set,
                    )
                ),
            ),
            write_evidence_summary(
                root,
                "adb-phone",
                "adb-device",
                "|".join(
                    (
                        "adb",
                        "phone",
                        "app.fixture.mobile",
                        "1.0.0",
                        artifact_sha256,
                        device_attestation_sha256,
                        "fixture-source",
                        tree.lower(),
                        release_set,
                    )
                ),
            ),
            write_evidence_summary(
                root,
                "chatgpt-oauth-mcp",
                "chatgpt-oauth-mcp",
                "|".join(
                    (
                        "chatgpt",
                        "asdk_app_fixture",
                        "asdk_app_v_fixture",
                        "https://fixture.example.invalid/mcp",
                        "True",
                        "fixture_get_status",
                        release_set,
                    )
                ),
            ),
        ]
    )
    for round_value in rounds:
        evidence_files.append(
            write_evidence_summary(
                root,
                cast(str, round_value["evidenceId"]),
                "pc-off-round",
                "|".join(
                    (
                        "pc-off",
                        str(round_value["round"]),
                        cast(str, round_value["mutationKind"]),
                        cast(str, round_value["sourceDeviceRole"]),
                        cast(str, round_value["pcIsolationMode"]),
                        cast(str, round_value["cloudRevisionBefore"]),
                        cast(str, round_value["cloudRevisionAfter"]),
                        cast(str, round_value["startedAt"]),
                        cast(str, round_value["finishedAt"]),
                        release_set,
                    )
                ),
            )
        )

    return {
        "sourceDeployments": source_deployments,
        "releaseSetSha256": release_set,
        "evidenceFiles": evidence_files,
        "testCommands": [
            {
                "id": "fixture-tests",
            }
        ],
        "remoteProbes": [
            {
                "id": "health",
                "method": "GET",
                "url": "https://fixture.example.invalid/healthz",
                "expectedStatus": 200,
                "sourceDeploymentId": "fixture-source",
                "evidenceId": "remote-health",
            }
        ],
        "adbDevices": [
            {
                "role": "phone",
                "physicalDevice": True,
                "emulator": False,
                "adbState": "device",
                "packageName": "app.fixture.mobile",
                "appVersion": "1.0.0",
                "artifactSha256": artifact_sha256,
                "deviceAttestationSha256": device_attestation_sha256,
                "sourceDeploymentId": "fixture-source",
                "sourceTreeHash": tree,
                "evidenceId": "adb-phone",
            }
        ],
        "chatgptMcp": {
            "appId": "asdk_app_fixture",
            "appVersionId": "asdk_app_v_fixture",
            "mcpUrl": "https://fixture.example.invalid/mcp",
            "oauthConnected": True,
            "toolNames": ["fixture_get_status"],
            "evidenceId": "chatgpt-oauth-mcp",
        },
        "pcOffRounds": rounds,
    }


def test_canonical_registry_gateway_manifest_and_focus_topology() -> None:
    registry = load_json(PLATFORM / "projects.json")
    schema = load_json(PLATFORM / "project-platform.schema.json")
    gateway_manifest = load_json(ROOT / ".poyi" / "project-platform.json")
    manifest_template = load_json(PLATFORM / "templates" / "project-platform.json")
    Draft202012Validator.check_schema(schema)
    validator = cast(JsonValidator, Draft202012Validator(schema))
    validator.validate(gateway_manifest)
    validator.validate(manifest_template)
    assert manifest_template["sync"]["routes"] == {
        "exchange": "/sync/v2/exchange",
        "status": "/sync/v2/status",
    }

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
    assert focus["mcp"]["toolContractId"] == "focuslink-cloud-mcp-v1"
    assert focus["mcp"]["requiredScopes"] == ["focuslink:read"]
    assert focus["sync"]["cloudBaseUrl"] == foxlink_origin
    assert focus["sync"]["contractId"] == "sync-envelope-v1"
    acceptance = cast(JsonObject, focus["sync"]["acceptanceEvidence"])
    assert acceptance["requiredAdbRoles"] == ["phone", "tablet", "watch"]
    assert acceptance["requiredPcOffRounds"] == 3
    assert acceptance["requiredMutationKinds"] == ["create", "update", "delete"]
    assert acceptance["requiredDeviceSourceDeploymentId"] == "focuslink-client"
    assert acceptance["requiredInteractionRoles"] == ["phone", "tablet", "watch"]
    assert [command["id"] for command in acceptance["requiredTestCommands"]] == [
        "verify-source-deployments",
        "verify-remote-probes",
        "verify-adb-physical-devices",
        "verify-pc-off-rounds",
        "verify-chatgpt-oauth-mcp",
    ]
    assert all(command["executable"] == "node" for command in acceptance["requiredTestCommands"])
    assert all(command["scriptSha256"] is None for command in acceptance["requiredTestCommands"])
    assert acceptance["requiredChatGptMcp"] == {
        "appId": "asdk_app_6a6863c3636481919adeb26f92d8546c",
        "appVersionId": "asdk_app_v_6a6863c4c3808191b17156ce1cea8606",
        "mcpUrl": f"{foxlink_origin}/mcp",
        "requiredTools": [
            "focuslink_get_status",
            "focuslink_get_today_summary",
            "focuslink_list_focus_records",
            "focuslink_get_task_summary",
        ],
    }
    required_sources = {
        source["id"]: source for source in cast(list[JsonObject], acceptance["requiredSources"])
    }
    assert required_sources["focuslink-client"]["kind"] == "git"
    assert required_sources["foxlink-cloud-mcp"]["kind"] == "git"
    assert required_sources["poyi-oauth-as"]["kind"] == "directory"
    assert ".dev.vars" not in required_sources["poyi-oauth-as"]["includePaths"]
    foxlink_manifest = load_json(
        Path(cast(str, required_sources["foxlink-cloud-mcp"]["path"]))
        / ".poyi"
        / "project-platform.json"
    )
    validator.validate(foxlink_manifest)
    assert foxlink_manifest["sync"]["routes"] == {
        "exchange": "/sync/v2/exchange",
        "status": "/sync/v2/status",
        "pairOffers": "/sync/v1/pair/offers",
        "pairExchange": "/sync/v1/pair/exchange",
    }
    assert foxlink_manifest["sync"]["contractId"] == "sync-envelope-v1"
    assert foxlink_manifest["sync"]["envelopeVersion"] == 1
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

    sync_contract = next(
        contract
        for contract in cast(list[JsonObject], registry["protocolContracts"])
        if contract["id"] == "sync-envelope-v1"
    )
    assert {"focuslink", "suixinyiting", "do-not-phone"} <= set(
        cast(list[str], sync_contract["projectIds"])
    )
    mcp_contract = cast(list[JsonObject], registry["mcpContracts"])[0]
    assert mcp_contract["id"] == "focuslink-cloud-mcp-v1"
    assert mcp_contract["requiredScopes"] == ["focuslink:read"]
    assert mcp_contract["canonicalTools"] == [
        "focuslink_get_status",
        "focuslink_get_today_summary",
        "focuslink_list_focus_records",
        "focuslink_get_task_summary",
    ]


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


def test_focuslink_cloud_mcp_contract_is_read_only_scoped_and_truthful() -> None:
    schema = load_json(PLATFORM / "contracts" / "focuslink-cloud-mcp-v1.schema.json")
    request = load_json(PLATFORM / "contracts" / "focuslink-cloud-mcp-v1.request.fixture.json")
    response = load_json(PLATFORM / "contracts" / "focuslink-cloud-mcp-v1.response.fixture.json")
    Draft202012Validator.check_schema(schema)
    validator = cast(
        JsonValidator,
        Draft202012Validator(schema, format_checker=FormatChecker()),
    )
    validator.validate(request)
    validator.validate(response)

    assert request["tool"] == "focuslink_get_task_summary"
    assert response["schemaVersion"] == 1
    assert request["requiredScope"] == "focuslink:read"
    assert response["authority"] == "focuslink-account-do"
    assert response["freshness"]["state"] == "fresh"
    assert response["lastVerifiedAt"]

    invalid: list[JsonObject] = []
    legacy_scope = copy.deepcopy(request)
    legacy_scope["requiredScope"] = "foxlink:read"
    invalid.append(legacy_scope)

    legacy_tool = copy.deepcopy(request)
    legacy_tool["tool"] = "foxlink_get_today_summary"
    invalid.append(legacy_tool)

    fake_live_state = copy.deepcopy(response)
    fake_live_state["deviceOnline"] = True
    invalid.append(fake_live_state)

    fresh_without_verification = copy.deepcopy(response)
    fresh_without_verification["lastVerifiedAt"] = None
    invalid.append(fresh_without_verification)

    leaked_note = copy.deepcopy(response)
    leaked_note["recentSessions"][0]["note"] = "must not be in the cloud summary"
    invalid.append(leaked_note)

    assert all(not validator.is_valid(candidate) for candidate in invalid)

    unknown = copy.deepcopy(response)
    unknown["lastVerifiedAt"] = None
    unknown["freshness"] = {
        "state": "unknown",
        "ageMs": None,
        "staleAfterMs": 900000,
    }
    validator.validate(unknown)


def test_every_partial_sync_contract_declaration_is_registered_with_a_consumer() -> None:
    registry = load_json(PLATFORM / "projects.json")
    contracts = {
        contract["id"]: contract
        for contract in cast(list[JsonObject], registry["protocolContracts"])
    }

    for project in cast(list[JsonObject], registry["projects"]):
        if project["lifecycle"] != "active" or project["sync"]["status"] not in {
            "partial",
            "complete",
        }:
            continue
        # This contract test owns the canonical registry only. External
        # manifests can be missing, frozen, or on an independent revision and
        # are validated by the explicit cross-repository audit workflow.
        contract_id = cast(str, project["sync"]["contractId"])
        assert contract_id in contracts
        contract = contracts[contract_id]
        assert project["id"] in contract["projectIds"]
        assert any(
            project["id"] in consumer.get("projectIds", [])
            for consumer in cast(list[JsonObject], contract["consumers"])
        )


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


def test_manifest_contract_declaration_cannot_hide_registry_omission(tmp_path: Path) -> None:
    registry, roots, _ = fixture_registry(tmp_path)
    projects = cast(list[JsonObject], registry["projects"])
    project = projects[0]
    project["dataPolicy"] = "cloud_allowed"
    project["mcp"] = {
        "status": "partial",
        "dataPlane": "cloud_primary",
        "coverage": "Fixture cloud query.",
    }
    project["sync"] = {
        "status": "partial",
        "dataPlane": "cloud_primary",
        "mode": "bidirectional_delta",
        "pcOffBehavior": "Fixture contract intentionally omitted from registry.",
    }

    for item, root in zip(projects, roots, strict=True):
        if item is project:
            value = manifest(
                cast(str, item["id"]),
                cast(str, item["name"]),
                data_policy="cloud_allowed",
                mcp_status="partial",
                sync_status="partial",
                data_plane="cloud_primary",
            )
        else:
            value = manifest(cast(str, item["id"]), cast(str, item["name"]))
        write_json(root / ".poyi" / "project-platform.json", value)

    registry_path = tmp_path / "projects.json"
    report_path = tmp_path / "audit.md"
    write_json(registry_path, registry)
    result = run_audit(registry_path, report_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "manifest declares unregistered sync.contractId sync-envelope-v1" in (
        report_path.read_text(encoding="utf-8-sig")
    )


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
        "contractId": "sync-envelope-v1",
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
    assert result.returncode != 0, result.stdout + result.stderr
    report = report_path.read_text(encoding="utf-8-sig")
    assert "missing required properties: verification" in report
    assert "声明无效/证据缺失" in report


def test_complete_pc_off_claim_requires_bound_release_devices_and_three_rounds(
    tmp_path: Path,
) -> None:
    registry, roots, _ = fixture_registry(tmp_path)
    project = cast(list[JsonObject], registry["projects"])[0]
    root = roots[0]
    install_fixture_sync_contract(registry, tmp_path, root)
    fixture_script = root / "tests" / "fixture_evidence_gate.py"
    fixture_script.parent.mkdir(parents=True)
    fixture_script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    fixture_script_sha256 = sha256_file(fixture_script)
    commit, tree = initialize_git_source(root)
    directory_source = tmp_path / "oauth-source"
    (directory_source / "src").mkdir(parents=True)
    (directory_source / "src" / "worker.ts").write_text("export default {};\n", encoding="utf-8")
    write_json(directory_source / "package.json", {"name": "fixture-oauth"})
    directory_include_paths = ["src", "package.json"]
    directory_tree = directory_tree_sha256(directory_source, directory_include_paths)
    project["dataPolicy"] = "cloud_allowed"
    project["publicCloudBaseUrl"] = "https://fixture.example.invalid"
    project["mcp"] = {
        "status": "partial",
        "dataPlane": "cloud_primary",
        "cloudBaseUrl": "https://fixture.example.invalid",
        "coverage": "Fixture remote MCP is not claimed complete.",
    }
    project["sync"] = {
        "status": "complete",
        "dataPlane": "cloud_primary",
        "mode": "bidirectional_delta",
        "contractId": "sync-envelope-v1",
        "cloudBaseUrl": "https://fixture.example.invalid",
        "pcOffBehavior": "Verified by the structured acceptance evidence fixture.",
        "acceptanceEvidence": {
            "requiredSources": [
                {
                    "id": "fixture-source",
                    "path": str(root),
                    "kind": "git",
                },
                {
                    "id": "fixture-oauth",
                    "path": str(directory_source),
                    "kind": "directory",
                    "includePaths": directory_include_paths,
                },
            ],
            "requiredRemoteProbes": [
                {
                    "id": "health",
                    "method": "GET",
                    "path": "/healthz",
                    "expectedStatus": 200,
                    "sourceDeploymentId": "fixture-source",
                }
            ],
            "requiredAdbRoles": ["phone"],
            "requiredPcOffRounds": 3,
            "requiredMutationKinds": ["create", "update", "delete"],
            "requiredEvidenceIds": ["chatgpt-oauth-mcp"],
            "requiredTestCommands": [
                {
                    "id": "fixture-tests",
                    "executable": "python",
                    "scriptPath": "tests/fixture_evidence_gate.py",
                    "arguments": [],
                    "timeoutSeconds": 30,
                    "scriptSha256": fixture_script_sha256,
                }
            ],
            "requiredChatGptMcp": {
                "appId": "asdk_app_fixture",
                "appVersionId": "asdk_app_v_fixture",
                "mcpUrl": "https://fixture.example.invalid/mcp",
                "requiredTools": ["fixture_get_status"],
            },
        },
    }

    complete_manifest = manifest(
        "fixture-0",
        "Fixture 0",
        data_policy="cloud_allowed",
        mcp_status="partial",
        sync_status="complete",
        data_plane="cloud_primary",
        supports_pc_off=True,
    )
    cast(JsonObject, complete_manifest["mcp"])["cloudBaseUrl"] = "https://fixture.example.invalid"
    cast(JsonObject, complete_manifest["sync"])["cloudBaseUrl"] = "https://fixture.example.invalid"
    cast(list[JsonObject], complete_manifest["dataInventory"])[0]["coverage"] = "complete"
    verification = build_complete_verification(root, commit, tree, directory_tree=directory_tree)
    complete_manifest["verification"] = verification

    projects = cast(list[JsonObject], registry["projects"])
    for item, item_root in zip(projects, roots, strict=True):
        value = (
            complete_manifest
            if item is project
            else manifest(cast(str, item["id"]), cast(str, item["name"]))
        )
        write_json(item_root / ".poyi" / "project-platform.json", value)
    registry_path = tmp_path / "projects.json"
    report_path = tmp_path / "audit.md"
    write_json(registry_path, registry)

    valid = run_audit(registry_path, report_path)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert re.search(r"EvidenceGaps\s*:\s*0", valid.stdout)
    assert re.search(r"UnverifiedCompletionClaims\s*:\s*1", valid.stdout)
    assert re.search(r"FullyCompliantProjects\s*:\s*5", valid.stdout)
    structural_report = report_path.read_text(encoding="utf-8-sig")
    assert "待主动证据复核" in structural_report
    assert "已验证支持" not in structural_report

    active_report = tmp_path / "audit-active.md"
    active = run_audit(registry_path, active_report, verify_evidence=True)
    assert active.returncode != 0, active.stdout + active.stderr
    assert re.search(r"ActiveEvidenceChecks\s*:\s*True", active.stdout)
    assert "remote probe returned" in active_report.read_text(encoding="utf-8-sig")

    invalid_cases: list[tuple[str, JsonObject, str]] = []

    wrong_tree = copy.deepcopy(complete_manifest)
    cast(JsonObject, cast(JsonObject, wrong_tree["verification"])["sourceDeployments"][0])[
        "treeHash"
    ] = "f" * len(tree)
    invalid_cases.append(("tree", wrong_tree, "source tree hash does not match commit"))

    pending_deployment = copy.deepcopy(complete_manifest)
    cast(
        JsonObject,
        cast(JsonObject, pending_deployment["verification"])["sourceDeployments"][0],
    )["deployedVersion"] = "pending"
    invalid_cases.append(("deployment", pending_deployment, "deployedVersion is not verifiable"))

    missing_probe = copy.deepcopy(complete_manifest)
    cast(
        JsonObject,
        cast(JsonObject, missing_probe["verification"])["remoteProbes"][0],
    )["id"] = "not-health"
    invalid_cases.append(("probe", missing_probe, "required remote probe is missing"))

    fake_device = copy.deepcopy(complete_manifest)
    device = cast(JsonObject, cast(JsonObject, fake_device["verification"])["adbDevices"][0])
    device["physicalDevice"] = False
    device["emulator"] = True
    invalid_cases.append(("adb", fake_device, "does not equal the required constant"))

    wrong_chatgpt_app = copy.deepcopy(complete_manifest)
    cast(JsonObject, cast(JsonObject, wrong_chatgpt_app["verification"])["chatgptMcp"])["appId"] = (
        "asdk_app_stale"
    )
    invalid_cases.append(
        (
            "chatgpt",
            wrong_chatgpt_app,
            "ChatGPT evidence does not match the registered OAuth app/version/tools",
        )
    )

    partial_inventory = copy.deepcopy(complete_manifest)
    cast(list[JsonObject], partial_inventory["dataInventory"])[0]["coverage"] = "partial"
    invalid_cases.append(
        (
            "inventory",
            partial_inventory,
            "requires complete-or-exempt dataInventory coverage",
        )
    )

    one_way_sync = copy.deepcopy(complete_manifest)
    cast(JsonObject, one_way_sync["sync"])["supportsBidirectionalDelta"] = False
    invalid_cases.append(("one-way", one_way_sync, "does not equal the required constant"))

    only_two_rounds = copy.deepcopy(complete_manifest)
    cast(JsonObject, only_two_rounds["verification"])["pcOffRounds"] = cast(
        list[JsonObject], cast(JsonObject, only_two_rounds["verification"])["pcOffRounds"]
    )[:2]
    invalid_cases.append(("rounds", only_two_rounds, "must contain at least 3 items"))

    for case_id, invalid_manifest, expected_message in invalid_cases:
        write_json(root / ".poyi" / "project-platform.json", invalid_manifest)
        case_report = tmp_path / f"audit-{case_id}.md"
        result = run_audit(registry_path, case_report)
        assert result.returncode != 0, result.stdout + result.stderr
        report = case_report.read_text(encoding="utf-8-sig")
        assert expected_message in report
        assert "声明无效/证据缺失" in report
        if case_id == "rounds":
            assert "cloudRevisionBefore" not in report

    remote_evidence_path = root / "evidence" / "remote-health.json"
    original_remote_evidence = remote_evidence_path.read_bytes()
    write_json(remote_evidence_path, {"schemaVersion": 1, "result": "pass"})
    forged_evidence = copy.deepcopy(complete_manifest)
    forged_entries = cast(
        list[JsonObject], cast(JsonObject, forged_evidence["verification"])["evidenceFiles"]
    )
    next(entry for entry in forged_entries if entry["id"] == "remote-health")["sha256"] = (
        sha256_file(remote_evidence_path)
    )
    write_json(root / ".poyi" / "project-platform.json", forged_evidence)
    forged_report = tmp_path / "audit-forged-evidence.md"
    forged = run_audit(registry_path, forged_report)
    assert forged.returncode != 0, forged.stdout + forged.stderr
    assert "evidence summary contract failed" in forged_report.read_text(encoding="utf-8-sig")
    remote_evidence_path.write_bytes(original_remote_evidence)

    write_json(root / ".poyi" / "project-platform.json", complete_manifest)
    (root / "source.txt").write_text("unreleased source drift\n", encoding="utf-8")
    drift_report = tmp_path / "audit-source-drift.md"
    drift = run_audit(registry_path, drift_report)
    assert drift.returncode != 0, drift.stdout + drift.stderr
    assert "source changed after the recorded deployment tree" in drift_report.read_text(
        encoding="utf-8-sig"
    )
