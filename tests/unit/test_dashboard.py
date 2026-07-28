import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from personal_mcp_gateway.admin.dashboard import (
    AuthorityIssue,
    AuthorityStatusDocument,
    AuthorityStatusEndpoint,
    CloudSyncObservation,
    DashboardMonitor,
    DashboardSyncProfile,
    DashboardTarget,
    PcOffCapability,
    _authority_signing_bytes,  # pyright: ignore[reportPrivateUsage]
    _sync_truth,  # pyright: ignore[reportPrivateUsage]
    cloud_mcp_summary,
    load_cloud_sync_observations,
    verify_authority_status,
)
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database


def build_monitor(tmp_path: Path) -> DashboardMonitor:
    settings = Settings(data_dir=tmp_path)
    runtime = GatewayRuntime(settings, Database(settings.database_path), ModuleRegistry())
    return DashboardMonitor(runtime)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _authority_target(public_key: str) -> DashboardTarget:
    return DashboardTarget(
        id="journal",
        name="Journal",
        description="Encrypted journal sync",
        icon="journal",
        accent="#fff",
        health_url="http://127.0.0.1:8780/healthz",
        ready_url="http://127.0.0.1:8780/readyz",
        sync=DashboardSyncProfile(
            compliance="complete",
            data_plane="cloud_primary",
            pc_off=PcOffCapability(
                read_available=True,
                write_available=True,
                continued_sync=True,
            ),
            local_dependency="none",
            stale_after_seconds=300,
        ),
        authority_status=AuthorityStatusEndpoint(
            url="https://authority.example.test/sync/v2/status",
            public_key=public_key,
            max_age_seconds=300,
        ),
    )


def _signed_authority_status(
    private_key: Ed25519PrivateKey,
    *,
    now: datetime,
    product_id: str = "journal",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    revision: int = 12,
    freshness: str = "fresh",
    last_verified_at: datetime | None = None,
    pending_count: int = 0,
    blocker_reason: str | None = None,
    pc_off: dict[str, bool] | None = None,
) -> bytes:
    effective_issued_at = issued_at or now
    document = AuthorityStatusDocument.model_validate(
        {
            "schemaVersion": 1,
            "productId": product_id,
            "issuedAt": effective_issued_at.isoformat(),
            "expiresAt": (expires_at or now + timedelta(minutes=2)).isoformat(),
            "truth": {
                "revision": revision,
                "freshness": freshness,
                "lastVerifiedAt": (last_verified_at or effective_issued_at).isoformat(),
                "pendingCount": pending_count,
                "blockerReason": blocker_reason,
                "pcOff": pc_off
                or {
                    "readAvailable": True,
                    "writeAvailable": True,
                    "continuedSync": True,
                },
            },
            "signature": _base64url(bytes(64)),
        }
    )
    payload = document.model_dump(mode="json", by_alias=True)
    payload["signature"] = _base64url(private_key.sign(_authority_signing_bytes(document)))
    return json.dumps(payload, separators=(",", ":")).encode()


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


def test_default_sync_contract_separates_cloud_snapshot_and_local_runtime(tmp_path: Path) -> None:
    targets, warning = build_monitor(tmp_path).targets()
    by_id = {target.id: target.sync for target in targets}

    assert warning is None
    assert by_id["journal"] is not None
    assert by_id["journal"].data_plane == "cloud_primary"
    assert by_id["journal"].pc_off.read_available is True
    assert by_id["journal"].pc_off.write_available is True
    assert by_id["journal"].pc_off.continued_sync is False
    assert by_id["watch"] is not None
    assert by_id["watch"].data_plane == "snapshot_mirror"
    assert by_id["watch"].pc_off.read_available is True
    assert by_id["watch"].pc_off.write_available is False
    assert by_id["watch"].pc_off.continued_sync is False
    assert by_id["personal"] is not None
    assert by_id["personal"].data_plane == "local_only"
    assert by_id["personal"].local_dependency == "runtime"


def test_existing_target_override_keeps_default_sync_contract(tmp_path: Path) -> None:
    monitor = build_monitor(tmp_path)
    tmp_path.joinpath("dashboard-targets.yaml").write_text(
        """
targets:
  - id: watch
    name: Watch custom
    description: custom
    icon: watch
    accent: '#fff'
    health_url: http://127.0.0.1:8768/healthz
    ready_url: http://127.0.0.1:8768/readyz
""",
        encoding="utf-8",
    )

    targets, warning = monitor.targets()
    watch = next(target for target in targets if target.id == "watch")

    assert warning is None
    assert watch.sync is not None
    assert watch.sync.data_plane == "snapshot_mirror"


def test_dashboard_rejects_snapshot_mirror_that_claims_continued_sync(tmp_path: Path) -> None:
    monitor = build_monitor(tmp_path)
    tmp_path.joinpath("dashboard-targets.yaml").write_text(
        """
targets:
  - id: demo
    name: Demo
    description: invalid sync claim
    icon: service
    accent: '#fff'
    health_url: http://127.0.0.1:8790/healthz
    ready_url: http://127.0.0.1:8790/readyz
    sync:
      compliance: complete
      data_plane: snapshot_mirror
      stale_after_seconds: 3600
      pc_off:
        read_available: true
        write_available: true
        continued_sync: true
      local_dependency: none
""",
        encoding="utf-8",
    )

    targets, warning = monitor.targets()

    assert [target.id for target in targets] == ["personal", "watch", "foxlink", "journal"]
    assert warning is not None


def test_sync_status_is_sanitized_and_attached_with_conservative_freshness(
    tmp_path: Path,
) -> None:
    monitor = build_monitor(tmp_path)
    now = datetime.now(UTC)
    status = {
        "schemaVersion": 1,
        "generatedAt": now.isoformat(),
        "projects": {
            "watch": {
                "result": "partial",
                "source": "pc-sync",
                "lastAttemptAt": now.isoformat(),
                "lastSuccessfulPushAt": now.isoformat(),
                "pushedItems": 1,
                "skippedItems": 1,
                "totalItems": 2,
                "reason": "local_items_unavailable",
                "privateError": "must not escape",
                "items": {
                    "fresh": {
                        "result": "success",
                        "lastAttemptAt": now.isoformat(),
                        "lastSuccessfulPushAt": now.isoformat(),
                    },
                    "old": {
                        "result": "failed",
                        "lastAttemptAt": now.isoformat(),
                        "lastSuccessfulPushAt": (now - timedelta(hours=4)).isoformat(),
                        "reason": "local_data_unavailable",
                    },
                },
            }
        },
    }
    monitor.runtime.settings.cloud_sync_status_path.write_text(json.dumps(status), encoding="utf-8")
    observations = load_cloud_sync_observations(monitor.runtime.settings.cloud_sync_status_path)
    watch = next(target for target in monitor.targets()[0] if target.id == "watch")
    payload = DashboardMonitor._attach_services(  # pyright: ignore[reportPrivateUsage]
        [watch],
        [{"mcp": {"ok": True}, "tunnel": None}],
        {},
        sync_observations=observations,
    )[0]

    assert payload["sync"]["dataPlane"] == "snapshot_mirror"
    assert payload["sync"]["pcOff"] == {
        "readAvailable": False,
        "writeAvailable": False,
        "continuedSync": False,
    }
    assert payload["sync"]["snapshotState"] == "stale"
    assert payload["sync"]["truth"] == {
        "state": "blocked",
        "lastVerifiedAt": (now - timedelta(hours=4)).isoformat(),
        "pendingCount": 0,
        "blockerReason": "pc_off_acceptance_pending",
    }
    assert payload["sync"]["observation"]["source"] == "pc-sync"
    assert "privateError" not in payload["sync"]["observation"]


def test_local_status_cannot_self_declare_product_authority(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    status = {
        "schemaVersion": 2,
        "generatedAt": now.isoformat(),
        "projects": {
            "journal": {
                "result": "success",
                "source": "product-authority",
                "lastAttemptAt": now.isoformat(),
                "lastVerifiedAt": now.isoformat(),
                "pendingCount": 0,
            }
        },
    }
    path = tmp_path / "cloud-sync-status.json"
    path.write_text(json.dumps(status), encoding="utf-8")

    assert load_cloud_sync_observations(path) == {}


def test_signed_authority_status_is_verified_and_product_bound() -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    payload = _signed_authority_status(private_key, now=now)

    status, issue = verify_authority_status(payload, target, now)
    assert issue is None
    assert status is not None
    assert status.product_id == "journal"
    assert status.truth.revision == 12
    assert status.truth.freshness == "fresh"
    assert status.truth.pending_count == 0
    assert status.truth.pc_off.continued_sync is True

    expired = _signed_authority_status(
        private_key,
        now=now,
        issued_at=now - timedelta(minutes=4),
        expires_at=now - timedelta(minutes=1),
    )
    assert verify_authority_status(expired, target, now) == (None, "authority_status_expired")

    wrong_product = _signed_authority_status(private_key, now=now, product_id="watch")
    assert verify_authority_status(wrong_product, target, now) == (
        None,
        "authority_product_mismatch",
    )


def test_authority_consumer_matches_independent_producer_wire_contract() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    unsigned = {
        "schemaVersion": 1,
        "productId": "journal",
        "issuedAt": "2026-07-28T12:00:00Z",
        "expiresAt": "2026-07-28T12:02:00Z",
        "truth": {
            "revision": 12,
            "freshness": "fresh",
            "lastVerifiedAt": "2026-07-28T11:59:00Z",
            "pendingCount": 0,
            "blockerReason": None,
            "pcOff": {
                "readAvailable": True,
                "writeAvailable": True,
                "continuedSync": True,
            },
        },
    }
    producer_bytes = json.dumps(
        unsigned,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    payload = {
        **unsigned,
        "signature": _base64url(private_key.sign(producer_bytes)),
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    parsed = AuthorityStatusDocument.model_validate_json(encoded)

    assert _authority_signing_bytes(parsed) == producer_bytes
    status, issue = verify_authority_status(encoded, _authority_target(public_key), now)
    assert issue is None
    assert status is not None
    assert status.truth.model_dump(mode="json", by_alias=True) == unsigned["truth"]


def test_authority_max_age_boundary_is_expired() -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    assert verify_authority_status(
        _signed_authority_status(
            private_key,
            now=now,
            issued_at=now - timedelta(seconds=300),
            expires_at=now + timedelta(seconds=1),
        ),
        _authority_target(public_key),
        now,
    ) == (None, "authority_status_expired")


@pytest.mark.parametrize(
    ("truth_updates"),
    [
        {"freshness": "fresh", "pendingCount": 1},
        {"freshness": "fresh", "blockerReason": "snapshot_incomplete"},
        {"freshness": "blocked", "blockerReason": None},
        {"freshness": "stale", "pendingCount": 1},
    ],
)
def test_signed_authority_status_rejects_contradictory_truth(
    truth_updates: dict[str, object],
) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    payload = json.loads(_signed_authority_status(private_key, now=now))
    payload["truth"].update(truth_updates)
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    signing_bytes = json.dumps(
        unsigned, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["signature"] = _base64url(private_key.sign(signing_bytes))

    assert verify_authority_status(
        json.dumps(payload).encode(), _authority_target(public_key), now
    ) == (None, "authority_signature_invalid")


def test_signed_authority_status_rejects_verification_after_issuance() -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    payload = json.loads(_signed_authority_status(private_key, now=now))
    payload["truth"]["lastVerifiedAt"] = (now + timedelta(seconds=1)).isoformat()
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    signing_bytes = json.dumps(
        unsigned, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["signature"] = _base64url(private_key.sign(signing_bytes))

    assert verify_authority_status(
        json.dumps(payload).encode(), _authority_target(public_key), now
    ) == (None, "authority_signature_invalid")


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("revision", 13),
        ("lastVerifiedAt", "2026-07-28T00:00:00+00:00"),
        ("freshness", "stale"),
        ("pendingCount", 1),
        ("blockerReason", "snapshot_incomplete"),
    ],
)
def test_signed_authority_status_rejects_tampering_of_every_truth_field(
    field: str,
    tampered_value: object,
) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    tampered = json.loads(_signed_authority_status(private_key, now=now))
    tampered["truth"][field] = tampered_value

    assert verify_authority_status(json.dumps(tampered).encode(), target, now) == (
        None,
        "authority_signature_invalid",
    )


@pytest.mark.parametrize("field", ["readAvailable", "writeAvailable", "continuedSync"])
def test_signed_authority_status_rejects_tampering_of_pc_off_fields(field: str) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    tampered = json.loads(_signed_authority_status(private_key, now=now))
    tampered["truth"]["pcOff"][field] = False

    assert verify_authority_status(json.dumps(tampered).encode(), target, now) == (
        None,
        "authority_signature_invalid",
    )


def test_signed_authority_status_rejects_revision_rollback() -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    assert verify_authority_status(
        _signed_authority_status(private_key, now=now, revision=11),
        _authority_target(public_key),
        now,
        minimum_revision=12,
    ) == (None, "authority_revision_rollback")


@respx.mock
@pytest.mark.asyncio
async def test_monitor_keeps_revision_checkpoint_and_rejects_rollback(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    endpoint = target.authority_status
    assert endpoint is not None
    route = respx.get(endpoint.url)
    route.side_effect = [
        httpx.Response(
            200,
            content=_signed_authority_status(private_key, now=now, revision=12),
        ),
        httpx.Response(
            200,
            content=_signed_authority_status(private_key, now=now, revision=11),
        ),
    ]
    monitor = build_monitor(tmp_path)
    await monitor.runtime.database.migrate()

    statuses, issues = await monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [target]
    )
    assert statuses["journal"].truth.revision == 12
    assert issues == {}

    restarted_monitor = build_monitor(tmp_path)
    await restarted_monitor.runtime.database.migrate()
    statuses, issues = await restarted_monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [target]
    )
    assert statuses == {}
    assert issues == {"journal": "authority_revision_rollback"}


@respx.mock
@pytest.mark.asyncio
async def test_monitor_rejects_conflicting_truth_at_same_revision(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    endpoint = target.authority_status
    assert endpoint is not None
    route = respx.get(endpoint.url)
    route.side_effect = [
        httpx.Response(
            200,
            content=_signed_authority_status(
                private_key,
                now=now,
                revision=12,
                pending_count=0,
            ),
        ),
        httpx.Response(
            200,
            content=_signed_authority_status(
                private_key,
                now=now,
                revision=12,
                freshness="blocked",
                pending_count=1,
                blocker_reason="snapshot_incomplete",
            ),
        ),
    ]
    monitor = build_monitor(tmp_path)
    await monitor.runtime.database.migrate()

    statuses, issues = await monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [target]
    )
    assert statuses["journal"].truth.pending_count == 0
    assert issues == {}

    statuses, issues = await monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [target]
    )
    assert statuses == {}
    assert issues == {"journal": "authority_revision_rollback"}


@respx.mock
@pytest.mark.asyncio
async def test_monitor_does_not_reset_product_revision_on_key_rotation(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    first_key = Ed25519PrivateKey.generate()
    rotated_key = Ed25519PrivateKey.generate()
    first_target = _authority_target(
        _base64url(first_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    )
    rotated_target = _authority_target(
        _base64url(rotated_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    )
    endpoint = first_target.authority_status
    assert endpoint is not None
    route = respx.get(endpoint.url)
    route.side_effect = [
        httpx.Response(
            200,
            content=_signed_authority_status(first_key, now=now, revision=12),
        ),
        httpx.Response(
            200,
            content=_signed_authority_status(rotated_key, now=now, revision=11),
        ),
    ]
    monitor = build_monitor(tmp_path)
    await monitor.runtime.database.migrate()

    statuses, issues = await monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [first_target]
    )
    assert statuses["journal"].truth.revision == 12
    assert issues == {}

    statuses, issues = await monitor._fetch_authority_truths(  # pyright: ignore[reportPrivateUsage]
        [rotated_target]
    )
    assert statuses == {}
    assert issues == {"journal": "authority_revision_rollback"}


def test_authority_endpoint_rejects_loopback_and_non_cloud_profiles() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    with pytest.raises(ValueError, match="cannot target loopback"):
        DashboardTarget.model_validate(
            {
                **_authority_target(public_key).model_dump(),
                "authority_status": {
                    "url": "https://127.0.0.1/status",
                    "public_key": public_key,
                },
            }
        )

    with pytest.raises(ValueError, match="requires a cloud_primary"):
        DashboardTarget(
            id="local",
            name="Local",
            description="Local runtime",
            icon="service",
            accent="#fff",
            health_url="http://127.0.0.1:8790/healthz",
            ready_url="http://127.0.0.1:8790/readyz",
            sync=DashboardSyncProfile(
                compliance="missing",
                data_plane="local_only",
                pc_off=PcOffCapability(
                    read_available=False,
                    write_available=False,
                    continued_sync=False,
                ),
                local_dependency="runtime",
            ),
            authority_status=AuthorityStatusEndpoint(
                url="https://authority.example.test/status",
                public_key=public_key,
            ),
        )


def test_sync_truth_uses_only_authority_observations_for_freshness() -> None:
    now = datetime.now(UTC)
    profile = DashboardSyncProfile(
        compliance="complete",
        data_plane="cloud_primary",
        pc_off=PcOffCapability(
            read_available=True,
            write_available=True,
            continued_sync=True,
        ),
        local_dependency="none",
        stale_after_seconds=60,
    )
    fresh = CloudSyncObservation(
        result="success",
        source="product-authority",
        lastAttemptAt=now,
        lastVerifiedAt=now,
        pendingCount=0,
    )

    assert _sync_truth(profile, fresh, "online", now)["state"] == "fresh"
    # The local MCP card can be offline while the cloud authority still has a
    # verifiable fresh state; those two facts must not overwrite each other.
    assert _sync_truth(profile, fresh, "offline", now)["state"] == "fresh"
    assert _sync_truth(profile, None, "online", now) == {
        "state": "unknown",
        "lastVerifiedAt": None,
        "pendingCount": None,
        "blockerReason": "authority_not_observed",
    }

    stale = fresh.model_copy(update={"last_verified_at": now - timedelta(seconds=61)})
    assert _sync_truth(profile, stale, "online", now)["state"] == "stale"
    blocked = fresh.model_copy(update={"pending_count": 2})
    assert _sync_truth(profile, blocked, "online", now)["state"] == "blocked"

    mirror_only = fresh.model_copy(update={"source": "pc-sync"})
    assert _sync_truth(profile, mirror_only, "online", now) == {
        "state": "unknown",
        "lastVerifiedAt": now.isoformat(),
        "pendingCount": 0,
        "blockerReason": "authority_not_observed",
    }


def test_cloud_mcp_summary_uses_verified_truth_not_larger_local_revision() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    status, issue = verify_authority_status(
        _signed_authority_status(
            private_key,
            now=now,
            revision=12,
            freshness="blocked",
            pending_count=2,
            blocker_reason="snapshot_incomplete",
        ),
        target,
        now,
    )
    assert issue is None
    assert status is not None
    local_observation = CloudSyncObservation(
        result="success",
        source="pc-sync",
        lastAttemptAt=now,
        lastVerifiedAt=now + timedelta(days=1),
        pendingCount=0,
        revision=999_999,
    )
    targets = DashboardMonitor._attach_services(  # pyright: ignore[reportPrivateUsage]
        [target],
        [{"id": "journal", "state": "offline"}],
        {},
        sync_observations={"journal": local_observation},
        authority_statuses={"journal": status},
        now=now,
    )

    payload = cloud_mcp_summary(
        {"generatedAt": "2026-07-28T12:00:00+00:00", "targets": targets},
        {"journal": status},
        now=now,
    )
    assert payload == {
        "schemaVersion": 1,
        "generatedAt": "2026-07-28T12:00:00+00:00",
        "products": [
            {
                "productId": "journal",
                "revision": 12,
                "freshness": "blocked",
                "lastVerifiedAt": "2026-07-28T12:00:00Z",
                "pendingCount": 2,
                "blockerReason": "snapshot_incomplete",
                "pcOff": {
                    "readAvailable": True,
                    "writeAvailable": True,
                    "continuedSync": True,
                },
            }
        ],
    }


def test_cloud_mcp_summary_rechecks_authority_expiry_at_projection_time() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    expires_at = now + timedelta(seconds=1)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    status, issue = verify_authority_status(
        _signed_authority_status(private_key, now=now, expires_at=expires_at),
        _authority_target(public_key),
        now,
    )
    assert issue is None
    assert status is not None
    snapshot: dict[str, Any] = {"targets": [{"id": "journal", "sync": {}}]}

    assert (
        cloud_mcp_summary(
            snapshot,
            {"journal": status},
            now=now,
        )["products"][0]["freshness"]
        == "fresh"
    )
    expired = cloud_mcp_summary(
        snapshot,
        {"journal": status},
        now=expires_at,
    )["products"][0]
    assert expired == {
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


@pytest.mark.asyncio
async def test_monitor_cloud_overview_does_not_call_local_snapshot_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    target = _authority_target(public_key)
    status, issue = verify_authority_status(
        _signed_authority_status(private_key, now=now),
        target,
        now,
    )
    assert issue is None
    assert status is not None
    monitor = build_monitor(tmp_path)

    async def local_snapshot_must_not_run(*, force: bool = False) -> dict[str, Any]:
        del force
        raise AssertionError("local diagnostic snapshot was called")

    async def verified_authority_only(
        targets: list[DashboardTarget],
    ) -> tuple[dict[str, object], dict[str, AuthorityIssue]]:
        assert targets == [target]
        return {"journal": status}, {}

    monkeypatch.setattr(monitor, "snapshot", local_snapshot_must_not_run)
    monkeypatch.setattr(monitor, "targets", lambda: ([target], None))
    monkeypatch.setattr(monitor, "_fetch_authority_truths", verified_authority_only)

    product = (await monitor.cloud_mcp_summary())["products"][0]
    assert product["revision"] == 12
    assert product["freshness"] == "fresh"


def test_cloud_mcp_summary_without_authority_is_entirely_unknown() -> None:
    payload = cloud_mcp_summary(
        {
            "generatedAt": "2026-07-28T12:00:00+00:00",
            "targets": [
                {
                    "id": "journal",
                    "sync": {
                        "pcOff": {},
                        "truth": {
                            "state": "fresh",
                            "lastVerifiedAt": "2099-01-01T00:00:00+00:00",
                            "pendingCount": 0,
                            "blockerReason": None,
                        },
                        "observation": {"revision": 999_999},
                    },
                }
            ],
        }
    )

    assert payload["products"][0] == {
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


@pytest.mark.parametrize(
    "issue",
    [
        "authority_fetch_failed",
        "authority_signature_invalid",
        "authority_status_expired",
        "authority_product_mismatch",
        "authority_revision_rollback",
        "authority_checkpoint_unavailable",
    ],
)
def test_cloud_mcp_summary_fails_closed_for_every_authority_issue(
    issue: AuthorityIssue,
) -> None:
    now = datetime.now(UTC)
    target = _authority_target(_base64url(bytes(32)))
    local_observation = CloudSyncObservation(
        result="success",
        source="pc-sync",
        lastAttemptAt=now,
        lastVerifiedAt=now,
        pendingCount=0,
        revision=999_999,
    )
    targets = DashboardMonitor._attach_services(  # pyright: ignore[reportPrivateUsage]
        [target],
        [{"id": "journal", "state": "online"}],
        {},
        sync_observations={"journal": local_observation},
        authority_issues={"journal": issue},
        now=now,
    )

    product = cloud_mcp_summary({"targets": targets})["products"][0]
    assert product["revision"] is None
    assert product["freshness"] == "unknown"
    assert product["lastVerifiedAt"] is None
    assert product["pendingCount"] is None
    assert product["blockerReason"] is None
    assert product["pcOff"] == {
        "readAvailable": False,
        "writeAvailable": False,
        "continuedSync": False,
    }


@pytest.mark.parametrize(
    "missing_field",
    ["revision", "freshness", "lastVerifiedAt", "pendingCount", "blockerReason", "pcOff"],
)
def test_cloud_mcp_summary_rejects_forged_or_partial_authority_truth(
    missing_field: str,
) -> None:
    authority_truth: dict[str, object] = {
        "revision": 12,
        "freshness": "fresh",
        "lastVerifiedAt": "2026-07-28T11:59:00+00:00",
        "pendingCount": 0,
        "blockerReason": None,
        "pcOff": {
            "readAvailable": True,
            "writeAvailable": True,
            "continuedSync": True,
        },
    }
    authority_truth.pop(missing_field)

    product = cloud_mcp_summary(
        {
            "targets": [
                {
                    "id": "journal",
                    "sync": {"pcOff": {}, "authorityTruth": authority_truth},
                }
            ]
        }
    )["products"][0]
    assert product["revision"] is None
    assert product["freshness"] == "unknown"
    assert product["lastVerifiedAt"] is None
    assert product["pendingCount"] is None
    assert product["blockerReason"] is None
    assert product["pcOff"] == {
        "readAvailable": False,
        "writeAvailable": False,
        "continuedSync": False,
    }


def test_cloud_mcp_summary_allowlist_does_not_leak_private_surfaces() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    private_key = Ed25519PrivateKey.generate()
    public_key = _base64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    status, issue = verify_authority_status(
        _signed_authority_status(private_key, now=now),
        _authority_target(public_key),
        now,
    )
    assert issue is None
    assert status is not None
    payload = cloud_mcp_summary(
        {
            "generatedAt": "2026-07-28T12:00:00+00:00",
            "targets": [
                {
                    "id": "journal",
                    "name": "private name must not escape",
                    "sync": {
                        "pcOff": {},
                        "authorityTruth": {
                            "revision": 12,
                            "freshness": "fresh",
                            "lastVerifiedAt": "2026-07-28T11:59:00+00:00",
                            "pendingCount": 0,
                            "blockerReason": None,
                            "pcOff": {
                                "readAvailable": True,
                                "writeAvailable": True,
                                "continuedSync": True,
                            },
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
        },
        {"journal": status},
        now=now,
    )

    encoded = json.dumps(payload, sort_keys=True)
    assert payload["products"][0]["freshness"] == "fresh"
    assert payload["products"][0]["pcOff"] == {
        "readAvailable": True,
        "writeAvailable": True,
        "continuedSync": True,
    }
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
