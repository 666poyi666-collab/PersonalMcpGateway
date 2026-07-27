import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_mcp_gateway.admin.dashboard import (
    CloudSyncObservation,
    DashboardMonitor,
    DashboardSyncProfile,
    PcOffCapability,
    _sync_truth,
    load_cloud_sync_observations,
)
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
    monitor.runtime.settings.cloud_sync_status_path.write_text(
        json.dumps(status), encoding="utf-8"
    )
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
        "readAvailable": True,
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
    assert _sync_truth(profile, fresh, "offline", now)["state"] == "offline"
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
