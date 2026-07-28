from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import time
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Self, cast
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

import httpx
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_mcp_gateway.admin import fleet
from personal_mcp_gateway.admin.widgets import WidgetHub
from personal_mcp_gateway.core.runtime import GatewayRuntime


class PcOffCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    read_available: bool
    write_available: bool
    continued_sync: bool

    @model_validator(mode="after")
    def prevent_false_continued_sync(self) -> Self:
        if self.continued_sync and not (self.read_available and self.write_available):
            raise ValueError("continued sync requires power-off read and write availability")
        return self


class AuthorityPcOffCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    read_available: bool = Field(alias="readAvailable")
    write_available: bool = Field(alias="writeAvailable")
    continued_sync: bool = Field(alias="continuedSync")

    @model_validator(mode="after")
    def prevent_false_continued_sync(self) -> Self:
        if self.continued_sync and not (self.read_available and self.write_available):
            raise ValueError("continued sync requires power-off read and write availability")
        return self


class DashboardSyncProfile(BaseModel):
    compliance: Literal["complete", "partial", "missing", "exempt"]
    data_plane: Literal["cloud_primary", "snapshot_mirror", "local_only"]
    pc_off: PcOffCapability
    local_dependency: Literal["none", "uplink", "runtime"]
    stale_after_seconds: int | None = Field(default=None, ge=60)

    @model_validator(mode="after")
    def prevent_false_power_off_claims(self) -> Self:
        if self.data_plane == "snapshot_mirror":
            if self.stale_after_seconds is None:
                raise ValueError("snapshot_mirror requires stale_after_seconds")
            if self.pc_off.continued_sync:
                raise ValueError("snapshot_mirror cannot claim continued sync")
        if self.data_plane == "local_only" and (
            self.pc_off.read_available or self.pc_off.write_available or self.pc_off.continued_sync
        ):
            raise ValueError("local_only cannot claim power-off availability")
        if self.pc_off.continued_sync and not (
            self.pc_off.read_available and self.pc_off.write_available
        ):
            raise ValueError("continued sync requires power-off read and write availability")
        return self


SyncFailureReason = Literal[
    "local_mcp_unreachable",
    "local_data_unavailable",
    "cloud_push_failed",
    "local_items_unavailable",
    "no_local_entries",
]

SyncTruthState = Literal["fresh", "stale", "offline", "blocked", "unknown"]
SyncBlockerReason = Literal[
    "authority_not_observed",
    "authority_fetch_failed",
    "authority_signature_invalid",
    "authority_status_expired",
    "authority_product_mismatch",
    "authority_revision_rollback",
    "authority_checkpoint_unavailable",
    "implementation_incomplete",
    "pc_off_acceptance_pending",
    "pc_runtime_required",
    "snapshot_incomplete",
    "local_mcp_unreachable",
    "cloud_push_failed",
    "local_data_unavailable",
    "local_items_unavailable",
    "no_local_entries",
]

AuthorityIssue = Literal[
    "authority_fetch_failed",
    "authority_signature_invalid",
    "authority_status_expired",
    "authority_product_mismatch",
    "authority_revision_rollback",
    "authority_checkpoint_unavailable",
]


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("sync timestamps must include a timezone")
    return value


class CloudSyncItemObservation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    result: Literal["success", "failed"]
    last_attempt_at: datetime = Field(alias="lastAttemptAt")
    last_successful_push_at: datetime | None = Field(default=None, alias="lastSuccessfulPushAt")
    reason: SyncFailureReason | None = None

    _timestamps_are_aware = field_validator("last_attempt_at", "last_successful_push_at")(
        _require_aware
    )


def _empty_sync_items() -> dict[str, CloudSyncItemObservation]:
    return {}


class CloudSyncObservation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    result: Literal["success", "partial", "failed", "no_changes"]
    source: Literal["pc-sync", "product-authority"]
    last_attempt_at: datetime = Field(alias="lastAttemptAt")
    last_successful_push_at: datetime | None = Field(default=None, alias="lastSuccessfulPushAt")
    last_complete_push_at: datetime | None = Field(default=None, alias="lastCompletePushAt")
    pushed_items: int = Field(default=0, alias="pushedItems", ge=0)
    skipped_items: int = Field(default=0, alias="skippedItems", ge=0)
    total_items: int = Field(default=0, alias="totalItems", ge=0)
    reason: SyncFailureReason | None = None
    last_verified_at: datetime | None = Field(default=None, alias="lastVerifiedAt")
    pending_count: int | None = Field(default=None, alias="pendingCount", ge=0)
    # This is an authority checkpoint, not an entity revision.  Keeping it
    # separate makes the dashboard/MCP surface useful without exposing data.
    revision: int | None = Field(default=None, ge=0)
    blocker_reason: SyncBlockerReason | None = Field(default=None, alias="blockerReason")
    items: dict[str, CloudSyncItemObservation] = Field(default_factory=_empty_sync_items)

    _timestamps_are_aware = field_validator(
        "last_attempt_at", "last_successful_push_at", "last_complete_push_at", "last_verified_at"
    )(_require_aware)


def _empty_sync_projects() -> dict[str, CloudSyncObservation]:
    return {}


class CloudSyncStatusDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schema_version: Literal[1, 2] = Field(alias="schemaVersion")
    generated_at: datetime = Field(alias="generatedAt")
    projects: dict[str, CloudSyncObservation] = Field(default_factory=_empty_sync_projects)

    _generated_at_is_aware = field_validator("generated_at")(_require_aware)


def _decode_base64url(value: str) -> bytes:
    if not value or not all(char.isalnum() or char in "-_" for char in value):
        raise ValueError("value must be unpadded base64url")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("value must be unpadded base64url") from exc


class AuthorityStatusEndpoint(BaseModel):
    """Pinned public verifier for a product-authority status document."""

    url: str
    public_key: str
    max_age_seconds: int = Field(default=900, ge=60, le=86_400)

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("authority status endpoint must be an HTTPS URL without user info")
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("authority status endpoint cannot target loopback")
        return value

    @field_validator("public_key")
    @classmethod
    def require_ed25519_public_key(cls, value: str) -> str:
        if len(_decode_base64url(value)) != 32:
            raise ValueError("authority status public_key must be a 32-byte Ed25519 key")
        return value


class AuthorityTruth(BaseModel):
    """The complete status projection covered by an authority signature."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    freshness: SyncTruthState
    last_verified_at: datetime = Field(alias="lastVerifiedAt")
    pending_count: int = Field(alias="pendingCount", ge=0)
    blocker_reason: SyncBlockerReason | None = Field(alias="blockerReason")
    pc_off: AuthorityPcOffCapability = Field(alias="pcOff")

    _last_verified_at_is_aware = field_validator("last_verified_at")(_require_aware)


class VerifiedAuthorityStatus(BaseModel):
    """A product-bound truth that already passed pinned-key verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str
    public_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    valid_until: datetime
    truth: AuthorityTruth

    _timestamps_are_aware = field_validator("issued_at", "valid_until")(_require_aware)

    def is_current_for(self, product_id: str, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("authority projection clock must include a timezone")
        return self.product_id == product_id and self.issued_at <= now < self.valid_until


class AuthorityStatusDocument(BaseModel):
    """Signed public metadata only; this contract never contains business data."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    product_id: str = Field(alias="productId", pattern=r"^[a-z][a-z0-9_]*$")
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    truth: AuthorityTruth
    signature: str

    _timestamps_are_aware = field_validator("issued_at", "expires_at")(_require_aware)

    @field_validator("signature")
    @classmethod
    def require_ed25519_signature(cls, value: str) -> str:
        if len(_decode_base64url(value)) != 64:
            raise ValueError("authority status signature must be an Ed25519 signature")
        return value

    @model_validator(mode="after")
    def require_valid_window(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("authority status expiry must follow issuance")
        return self


def _authority_signing_bytes(document: AuthorityStatusDocument) -> bytes:
    payload = document.model_dump(mode="json", by_alias=True, exclude={"signature"})
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _authority_truth_bytes(truth: AuthorityTruth) -> bytes:
    payload = truth.model_dump(mode="json", by_alias=True)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def verify_authority_status(
    payload: bytes,
    target: DashboardTarget,
    now: datetime,
    *,
    minimum_revision: int | None = None,
) -> tuple[VerifiedAuthorityStatus | None, AuthorityIssue | None]:
    """Fail closed unless a fresh document validates against the target's pinned key."""
    endpoint = target.authority_status
    if endpoint is None:
        return None, "authority_fetch_failed"
    if now.tzinfo is None:
        raise ValueError("authority verification clock must include a timezone")
    if minimum_revision is not None and minimum_revision < 0:
        raise ValueError("minimum authority revision cannot be negative")
    if len(payload) > 64_000:
        return None, "authority_fetch_failed"
    try:
        document = AuthorityStatusDocument.model_validate_json(payload)
    except (ValueError, UnicodeDecodeError):
        return None, "authority_signature_invalid"
    try:
        public_key_bytes = _decode_base64url(endpoint.public_key)
        key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        key.verify(_decode_base64url(document.signature), _authority_signing_bytes(document))
    except (InvalidSignature, ValueError):
        return None, "authority_signature_invalid"
    if document.product_id != target.id:
        return None, "authority_product_mismatch"
    if document.issued_at > now or document.expires_at <= now:
        return None, "authority_status_expired"
    if (now - document.issued_at).total_seconds() >= endpoint.max_age_seconds:
        return None, "authority_status_expired"
    if minimum_revision is not None and document.truth.revision < minimum_revision:
        return None, "authority_revision_rollback"
    return (
        VerifiedAuthorityStatus(
            product_id=document.product_id,
            public_key_hash=hashlib.sha256(public_key_bytes).hexdigest(),
            truth_hash=hashlib.sha256(_authority_truth_bytes(document.truth)).hexdigest(),
            issued_at=document.issued_at,
            valid_until=min(
                document.expires_at,
                document.issued_at + timedelta(seconds=endpoint.max_age_seconds),
            ),
            truth=document.truth,
        ),
        None,
    )


class DashboardTarget(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str
    icon: str
    accent: str
    health_url: str
    ready_url: str
    tunnel_ready_url: str | None = None
    mcp_service: str | None = None
    tunnel_service: str | None = None
    sync: DashboardSyncProfile | None = None
    authority_status: AuthorityStatusEndpoint | None = None
    enabled: bool = True

    @field_validator("health_url", "ready_url", "tunnel_ready_url")
    @classmethod
    def require_loopback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        internal = parsed.scheme == "internal" and parsed.hostname == "gateway"
        loopback = parsed.scheme in {"http", "https"} and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if not internal and not loopback:
            raise ValueError("Dashboard targets must use loopback HTTP endpoints")
        return value

    @model_validator(mode="after")
    def restrict_authority_status_to_cloud_primary(self) -> Self:
        if self.authority_status and (self.sync is None or self.sync.data_plane != "cloud_primary"):
            raise ValueError("authority_status requires a cloud_primary sync profile")
        return self


def _empty_targets() -> list[DashboardTarget]:
    return []


class DashboardConfig(BaseModel):
    targets: list[DashboardTarget] = Field(default_factory=_empty_targets)


DEFAULT_TARGETS = (
    DashboardTarget(
        id="personal",
        name="Personal Gateway",
        description="个人应用统一入口",
        icon="gateway",
        accent="#7c6cff",
        health_url="internal://gateway/healthz",
        ready_url="internal://gateway/readyz",
        tunnel_ready_url="http://127.0.0.1:8877/readyz",
        mcp_service="PoyiPersonalMcpGateway",
        tunnel_service="OpenAISecureMcpTunnel",
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
    ),
    DashboardTarget(
        id="watch",
        name="Watch MCP",
        description="训练、睡眠与手表控制",
        icon="watch",
        accent="#2dd4bf",
        health_url="http://127.0.0.1:8768/healthz",
        ready_url="http://127.0.0.1:8768/readyz",
        tunnel_ready_url="http://127.0.0.1:8880/readyz",
        mcp_service="PoyiWatchMcp",
        tunnel_service="PoyiWatchTunnel",
        sync=DashboardSyncProfile(
            compliance="partial",
            data_plane="snapshot_mirror",
            pc_off=PcOffCapability(
                read_available=True,
                write_available=False,
                continued_sync=False,
            ),
            local_dependency="uplink",
            stale_after_seconds=3 * 60 * 60,
        ),
    ),
    DashboardTarget(
        id="foxlink",
        name="Foxlink MCP",
        description="Foxlink 数据与业务能力",
        icon="link",
        accent="#fb923c",
        health_url="http://127.0.0.1:8770/healthz",
        ready_url="http://127.0.0.1:8770/readyz",
        tunnel_ready_url="http://127.0.0.1:8878/readyz",
        mcp_service="PoyiFoxlinkMcp",
        tunnel_service="FoxlinkSecureMcpTunnel",
        sync=DashboardSyncProfile(
            compliance="partial",
            data_plane="snapshot_mirror",
            pc_off=PcOffCapability(
                read_available=True,
                write_available=False,
                continued_sync=False,
            ),
            local_dependency="uplink",
            stale_after_seconds=3 * 60 * 60,
        ),
    ),
    DashboardTarget(
        id="journal",
        name="Journal MCP",
        description="日记、复盘与个人记录",
        icon="journal",
        accent="#f472b6",
        health_url="http://127.0.0.1:8780/healthz",
        ready_url="http://127.0.0.1:8780/readyz",
        tunnel_ready_url="http://127.0.0.1:8887/readyz",
        mcp_service="PoyiJournalMcp",
        tunnel_service="PoyiJournalTunnel",
        sync=DashboardSyncProfile(
            compliance="partial",
            data_plane="cloud_primary",
            pc_off=PcOffCapability(
                read_available=True,
                write_available=True,
                continued_sync=False,
            ),
            local_dependency="uplink",
        ),
    ),
)


def load_cloud_sync_observations(path: Path) -> dict[str, CloudSyncObservation]:
    try:
        if path.stat().st_size > 256_000:
            return {}
        document = CloudSyncStatusDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # This file is written by the Windows-side watchdog. It is a useful mirror
    # for snapshot widgets, but it is not a product authority and may never
    # self-upgrade a cloud-primary card by declaring source=product-authority.
    return {
        project_id: observation
        for project_id, observation in document.projects.items()
        if observation.source == "pc-sync"
    }


def _snapshot_state(
    profile: DashboardSyncProfile,
    observation: CloudSyncObservation | None,
    now: datetime,
) -> Literal["fresh", "stale", "incomplete", "never_synced", "unknown", "not_applicable"]:
    if profile.data_plane != "snapshot_mirror":
        return "not_applicable"
    if observation is None:
        return "unknown"
    successful_at = [
        item.last_successful_push_at
        for item in observation.items.values()
        if item.last_successful_push_at is not None
    ]
    if not successful_at:
        return "never_synced"
    if observation.total_items > len(successful_at):
        return "incomplete"
    oldest = min(successful_at)
    threshold = profile.stale_after_seconds
    if threshold is not None and (now - oldest).total_seconds() > threshold:
        return "stale"
    return "fresh"


def _last_verified_at(observation: CloudSyncObservation | None) -> datetime | None:
    if observation is None:
        return None
    if observation.last_verified_at is not None:
        return observation.last_verified_at
    successful = [
        item.last_successful_push_at
        for item in observation.items.values()
        if item.last_successful_push_at is not None
    ]
    if successful:
        return min(successful)
    return observation.last_complete_push_at or observation.last_successful_push_at


def _sync_truth(
    profile: DashboardSyncProfile,
    observation: CloudSyncObservation | None,
    _target_state: str,
    now: datetime,
    authority_issue: AuthorityIssue | None = None,
) -> dict[str, Any]:
    """Produce only a conservative, evidence-backed dashboard sync state."""
    verified_at = _last_verified_at(observation)
    pending_count = observation.pending_count if observation else None
    if observation and pending_count is None:
        pending_count = max(
            0,
            observation.total_items
            - sum(
                1 for item in observation.items.values() if item.last_successful_push_at is not None
            ),
        )

    def result(
        state: SyncTruthState,
        blocker: SyncBlockerReason | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "lastVerifiedAt": verified_at.isoformat() if verified_at else None,
            "pendingCount": pending_count,
            "blockerReason": blocker,
        }

    if profile.data_plane == "local_only":
        return result("blocked", "pc_runtime_required")
    if profile.compliance == "missing":
        return result("blocked", "implementation_incomplete")
    if profile.compliance != "complete":
        return result("blocked", "pc_off_acceptance_pending")
    if authority_issue == "authority_status_expired":
        return result("stale", authority_issue)
    if authority_issue in {"authority_signature_invalid", "authority_product_mismatch"}:
        return result("blocked", authority_issue)
    if authority_issue == "authority_fetch_failed":
        return result("unknown", authority_issue)
    if observation is None:
        return result("unknown", "authority_not_observed")
    # The local probe only says whether this Windows-hosted MCP process is up.
    # A cloud-primary sync badge must be backed by the product authority itself,
    # never by a stale PC-side mirror or by the local process state.
    if observation.source != "product-authority":
        return result("unknown", "authority_not_observed")
    if observation.result == "failed":
        blocker = observation.blocker_reason or observation.reason or "cloud_push_failed"
        return result("offline", blocker)
    if observation.blocker_reason:
        return result("blocked", observation.blocker_reason)
    if observation.result == "partial" or (pending_count is not None and pending_count > 0):
        return result("blocked", "snapshot_incomplete")
    if verified_at is None:
        return result("unknown", "authority_not_observed")
    threshold = profile.stale_after_seconds
    if threshold is not None and (now - verified_at).total_seconds() > threshold:
        return result("stale")
    return result("fresh")


def _sync_payload(
    profile: DashboardSyncProfile,
    observation: CloudSyncObservation | None,
    authority_status: VerifiedAuthorityStatus | None,
    now: datetime,
    target_state: str,
    authority_issue: AuthorityIssue | None = None,
) -> dict[str, Any]:
    runtime: dict[str, Any]
    if observation is None:
        runtime = {"result": "unknown"}
    else:
        runtime = observation.model_dump(mode="json", by_alias=True, exclude_none=True)
    authority_truth = authority_status.truth if authority_status is not None else None
    signed_truth: dict[str, Any] | None = (
        authority_truth.model_dump(mode="json", by_alias=True) if authority_truth else None
    )
    truth: dict[str, Any]
    if profile.data_plane == "cloud_primary":
        if authority_truth is not None:
            assert signed_truth is not None
            truth = {
                "state": authority_truth.freshness,
                "lastVerifiedAt": signed_truth["lastVerifiedAt"],
                "pendingCount": authority_truth.pending_count,
                "blockerReason": authority_truth.blocker_reason,
            }
        else:
            truth = {
                "state": "unknown",
                "lastVerifiedAt": None,
                "pendingCount": None,
                "blockerReason": None,
            }
    else:
        truth = _sync_truth(profile, observation, target_state, now)
    return {
        "compliance": profile.compliance,
        "dataPlane": profile.data_plane,
        "pcOff": {
            "readAvailable": authority_truth.pc_off.read_available
            if authority_truth is not None
            else False,
            "writeAvailable": authority_truth.pc_off.write_available
            if authority_truth is not None
            else False,
            "continuedSync": authority_truth.pc_off.continued_sync
            if authority_truth is not None
            else False,
        },
        "localDependency": profile.local_dependency,
        "staleAfterSeconds": profile.stale_after_seconds,
        "snapshotState": _snapshot_state(profile, observation, now),
        "truth": truth,
        # Only this channel is allowed to feed the cloud MCP projection.  The
        # local observation and verifier issue remain dashboard diagnostics.
        "authorityTruth": signed_truth,
        "authorityVerification": {
            "state": "verified"
            if authority_truth is not None
            else ("rejected" if authority_issue is not None else "missing"),
            "issue": authority_issue,
        },
        "observation": runtime,
    }


def cloud_mcp_summary(
    snapshot: dict[str, Any],
    verified_authorities: Mapping[str, VerifiedAuthorityStatus] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the deliberately small cloud-MCP status contract.

    The dashboard snapshot also contains local service probes, widgets,
    diagnostics, and recent activity.  None of those are cloud authority data,
    so this projection never forwards them.  It also intentionally copies
    individual fields instead of returning ``observation`` wholesale: future
    authority documents cannot accidentally add ciphertext or credentials to
    the MCP surface.
    """

    projection_time = now or datetime.now(UTC)
    if projection_time.tzinfo is None:
        raise ValueError("cloud summary clock must include a timezone")
    authorities = verified_authorities or {}
    products: list[dict[str, Any]] = []
    candidate_targets = snapshot.get("targets")
    targets: list[object] = (
        cast(list[object], candidate_targets) if isinstance(candidate_targets, list) else []
    )
    for candidate in targets:
        if not isinstance(candidate, dict):
            continue
        target = cast(dict[str, Any], candidate)
        product_id = target.get("id")
        sync = target.get("sync")
        if not isinstance(product_id, str) or not isinstance(sync, dict):
            continue
        candidate_authority = authorities.get(product_id)
        authority_status = (
            candidate_authority
            if isinstance(candidate_authority, VerifiedAuthorityStatus)
            and candidate_authority.is_current_for(product_id, projection_time)
            else None
        )
        authority_truth = authority_status.truth if authority_status is not None else None
        authority_payload = (
            authority_truth.model_dump(mode="json", by_alias=True)
            if authority_truth is not None
            else None
        )
        products.append(
            {
                "productId": product_id,
                "revision": authority_truth.revision if authority_truth is not None else None,
                "freshness": authority_truth.freshness
                if authority_truth is not None
                else "unknown",
                "lastVerifiedAt": authority_payload["lastVerifiedAt"]
                if authority_payload is not None
                else None,
                "pendingCount": authority_truth.pending_count
                if authority_truth is not None
                else None,
                "blockerReason": authority_truth.blocker_reason
                if authority_truth is not None
                else None,
                "pcOff": {
                    "readAvailable": authority_truth.pc_off.read_available
                    if authority_truth is not None
                    else False,
                    "writeAvailable": authority_truth.pc_off.write_available
                    if authority_truth is not None
                    else False,
                    "continuedSync": authority_truth.pc_off.continued_sync
                    if authority_truth is not None
                    else False,
                },
            }
        )
    return {
        "schemaVersion": 1,
        "generatedAt": snapshot.get("generatedAt")
        if isinstance(snapshot.get("generatedAt"), str)
        else None,
        "products": products,
    }


class DashboardMonitor:
    def __init__(self, runtime: GatewayRuntime) -> None:
        self.runtime = runtime
        self.widgets = WidgetHub(runtime.settings)
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._cache_lock = asyncio.Lock()
        self._last_states: dict[str, str] = {}
        self._status_events: deque[dict[str, Any]] = deque(maxlen=40)
        self._verified_authorities: dict[str, VerifiedAuthorityStatus] = {}
        self._authority_cache_valid_until: datetime | None = None

    def _cache_is_current(self) -> bool:
        if self._cache is None or time.monotonic() - self._cached_at >= 3:
            return False
        return (
            self._authority_cache_valid_until is None
            or datetime.now(UTC) < self._authority_cache_valid_until
        )

    async def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._cache_is_current():
            assert self._cache is not None
            return self._cache
        async with self._cache_lock:
            if not force and self._cache_is_current():
                assert self._cache is not None
                return self._cache
            self._cache = await self._build_snapshot()
            self._cached_at = time.monotonic()
            return self._cache

    async def cloud_mcp_summary(self) -> dict[str, Any]:
        """Build an authority-only projection without local diagnostic dependencies."""

        targets, _config_warning = self.targets()
        statuses, _issues = await self._fetch_authority_truths(targets)
        generated_at = datetime.now(UTC)
        current_authorities = {
            product_id: status
            for product_id, status in statuses.items()
            if status.is_current_for(product_id, generated_at)
        }
        authority_snapshot: dict[str, Any] = {
            "generatedAt": generated_at.isoformat(),
            "targets": [
                {"id": target.id, "sync": {}} for target in targets if target.sync is not None
            ],
        }
        return cloud_mcp_summary(
            authority_snapshot,
            current_authorities,
            now=generated_at,
        )

    async def _build_snapshot(self) -> dict[str, Any]:
        probe_started = time.perf_counter()
        targets, config_warning = self.targets()
        service_names = sorted(
            {
                name
                for target in targets
                for name in (target.mcp_service, target.tunnel_service)
                if name
            }
            | {fleet.WATCHDOG_SERVICE}
        )
        service_states, probed, local_sync_observations, authority_sync = await asyncio.gather(
            asyncio.to_thread(fleet.query_service_states, service_names),
            self._probe_all(targets),
            asyncio.to_thread(
                load_cloud_sync_observations, self.runtime.settings.cloud_sync_status_path
            ),
            self._fetch_authority_truths(targets),
        )
        authority_statuses, authority_issues = authority_sync
        activity, recent, widgets = await asyncio.gather(
            self._activity(), self._recent_invocations(), self.widgets.snapshot()
        )
        errors = await self.runtime.recent_errors(8)
        generated_at = datetime.now(UTC)
        current_authorities: dict[str, VerifiedAuthorityStatus] = {}
        for product_id, status in authority_statuses.items():
            if status.is_current_for(product_id, generated_at):
                current_authorities[product_id] = status
            else:
                authority_issues[product_id] = "authority_status_expired"
        self._verified_authorities = current_authorities
        self._authority_cache_valid_until = min(
            (status.valid_until for status in current_authorities.values()),
            default=None,
        )
        target_states = self._attach_services(
            targets,
            probed,
            service_states,
            sync_observations=local_sync_observations,
            authority_statuses=current_authorities,
            authority_issues=authority_issues,
            now=generated_at,
        )
        calls_24h = sum(int(bucket["calls"]) for bucket in activity)
        failures_24h = sum(int(bucket["failures"]) for bucket in activity)
        states = [str(target["state"]) for target in target_states]
        self._record_state_changes(target_states)
        return {
            "generatedAt": generated_at.isoformat(),
            "refreshIntervalSeconds": 4,
            "probeDurationMs": round((time.perf_counter() - probe_started) * 1000),
            "gateway": {
                "state": "online" if self.runtime.ready else "starting",
                "version": self.runtime.settings.version,
                "uptimeSeconds": int(time.monotonic() - self.runtime.started_at),
                "callsTotal": self.runtime.calls_total,
                "callsFailed": self.runtime.calls_failed,
            },
            "summary": {
                "total": len(target_states),
                "online": states.count("online"),
                "degraded": states.count("degraded"),
                "offline": states.count("offline"),
                "calls24h": calls_24h,
                "failures24h": failures_24h,
                "successRate": round(
                    ((calls_24h - failures_24h) / calls_24h * 100) if calls_24h else 100.0,
                    1,
                ),
            },
            "targets": target_states,
            "widgets": widgets,
            "activity": {"hourly": activity, "recent": recent},
            "errors": errors,
            "events": list(self._status_events),
            "configWarning": config_warning,
            "fleet": {
                "watchdog": {
                    "service": fleet.WATCHDOG_SERVICE,
                    "state": service_states.get(fleet.WATCHDOG_SERVICE, "unknown"),
                },
                "repairSupported": fleet.repair_supported(),
            },
        }

    async def _probe_all(self, targets: list[DashboardTarget]) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            return list(
                await asyncio.gather(*(self._probe_target(client, target) for target in targets))
            )

    async def _fetch_authority_truths(
        self,
        targets: list[DashboardTarget],
    ) -> tuple[dict[str, VerifiedAuthorityStatus], dict[str, AuthorityIssue]]:
        configured = [target for target in targets if target.authority_status is not None]
        if not configured:
            return {}, {}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(3.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_authority_truth(client, target) for target in configured)
            )
        statuses: dict[str, VerifiedAuthorityStatus] = {}
        issues: dict[str, AuthorityIssue] = {}
        for target, status, issue in results:
            if status is not None:
                checkpoint = await self._accept_authority_revision(status)
                if checkpoint == "accepted":
                    statuses[target.id] = status
                elif checkpoint == "rollback":
                    issues[target.id] = "authority_revision_rollback"
                else:
                    issues[target.id] = "authority_checkpoint_unavailable"
            if issue is not None:
                issues[target.id] = issue
        return statuses, issues

    async def _accept_authority_revision(
        self,
        status: VerifiedAuthorityStatus,
    ) -> Literal["accepted", "rollback", "unavailable"]:
        try:
            checkpoint = await self.runtime.database.fetchone(
                """
                INSERT INTO authority_revision_checkpoints(
                  product_id, public_key_hash, revision, truth_hash
                ) VALUES (?,?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET
                  public_key_hash=excluded.public_key_hash,
                  revision=excluded.revision,
                  truth_hash=excluded.truth_hash,
                  updated_at=CURRENT_TIMESTAMP
                WHERE excluded.revision > authority_revision_checkpoints.revision
                   OR (
                     excluded.revision = authority_revision_checkpoints.revision
                     AND excluded.truth_hash = authority_revision_checkpoints.truth_hash
                   )
                RETURNING revision, truth_hash
                """,
                (
                    status.product_id,
                    status.public_key_hash,
                    status.truth.revision,
                    status.truth_hash,
                ),
            )
        except Exception:
            return "unavailable"
        if checkpoint is None:
            return "rollback"
        if (
            checkpoint.get("revision") != status.truth.revision
            or checkpoint.get("truth_hash") != status.truth_hash
        ):
            return "rollback"
        return "accepted"

    @staticmethod
    async def _fetch_authority_truth(
        client: httpx.AsyncClient,
        target: DashboardTarget,
    ) -> tuple[DashboardTarget, VerifiedAuthorityStatus | None, AuthorityIssue | None]:
        endpoint = target.authority_status
        if endpoint is None:
            return target, None, "authority_fetch_failed"
        try:
            async with client.stream(
                "GET",
                endpoint.url,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status_code != 200:
                    return target, None, "authority_fetch_failed"
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > 64_000:
                    return target, None, "authority_fetch_failed"
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > 64_000:
                        return target, None, "authority_fetch_failed"
                    chunks.append(chunk)
        except (httpx.HTTPError, ValueError):
            return target, None, "authority_fetch_failed"
        status, issue = verify_authority_status(b"".join(chunks), target, datetime.now(UTC))
        return target, status, issue

    @staticmethod
    def _attach_services(
        targets: list[DashboardTarget],
        probed: list[dict[str, Any]],
        service_states: dict[str, str],
        sync_observations: dict[str, CloudSyncObservation] | None = None,
        authority_statuses: dict[str, VerifiedAuthorityStatus] | None = None,
        authority_issues: dict[str, AuthorityIssue] | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        observed = sync_observations or {}
        signed_statuses = authority_statuses or {}
        issues = authority_issues or {}
        attached_at = now or datetime.now(UTC)
        for target, payload in zip(targets, probed, strict=True):
            candidate_status = signed_statuses.get(target.id)
            authority_status = (
                candidate_status
                if candidate_status is not None
                and candidate_status.is_current_for(target.id, attached_at)
                else None
            )
            if target.mcp_service and isinstance(payload.get("mcp"), dict):
                payload["mcp"]["service"] = {
                    "name": target.mcp_service,
                    "state": service_states.get(target.mcp_service, "unknown"),
                }
            if target.tunnel_service and isinstance(payload.get("tunnel"), dict):
                payload["tunnel"]["service"] = {
                    "name": target.tunnel_service,
                    "state": service_states.get(target.tunnel_service, "unknown"),
                }
            payload["sync"] = (
                _sync_payload(
                    target.sync,
                    observed.get(target.id),
                    authority_status,
                    attached_at,
                    str(payload.get("state", "unknown")),
                    issues.get(target.id),
                )
                if target.sync
                else None
            )
        return probed

    def _record_state_changes(self, targets: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC).isoformat()
        current = {str(target["id"]): str(target["state"]) for target in targets}
        if self._last_states:
            names = {str(target["id"]): str(target["name"]) for target in targets}
            for target_id, state in current.items():
                previous = self._last_states.get(target_id)
                if previous is not None and previous != state:
                    self._status_events.appendleft(
                        {
                            "type": "status_change",
                            "target": target_id,
                            "name": names[target_id],
                            "fromState": previous,
                            "toState": state,
                            "occurredAt": now,
                        }
                    )
        self._last_states = current

    def targets(self) -> tuple[list[DashboardTarget], str | None]:
        targets = {target.id: target for target in DEFAULT_TARGETS}
        path = self.runtime.settings.dashboard_targets_path
        if not path.exists():
            return list(targets.values()), None
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            config = DashboardConfig.model_validate(document)
            for target in config.targets:
                if target.enabled:
                    # Override entries written before service names existed keep
                    # the default services for the same id.
                    default = targets.get(target.id)
                    if default is not None:
                        updates: dict[str, Any] = {}
                        if target.mcp_service is None and default.mcp_service:
                            updates["mcp_service"] = default.mcp_service
                        if target.tunnel_service is None and default.tunnel_service:
                            updates["tunnel_service"] = default.tunnel_service
                        if target.sync is None and default.sync:
                            updates["sync"] = default.sync
                        if updates:
                            target = target.model_copy(update=updates)
                    targets[target.id] = target
                else:
                    targets.pop(target.id, None)
        except (OSError, ValueError, yaml.YAMLError):
            return list(targets.values()), "dashboard-targets.yaml 配置无效; 正在使用默认目标"
        return list(targets.values()), None

    async def _probe_target(
        self, client: httpx.AsyncClient, target: DashboardTarget
    ) -> dict[str, Any]:
        if target.health_url.startswith("internal://"):
            health = {"ok": True, "latencyMs": 0, "status": 200, "version": None}
            ready = {
                "ok": self.runtime.ready,
                "latencyMs": 0,
                "status": 200 if self.runtime.ready else 503,
                "version": self.runtime.settings.version,
            }
        else:
            health, ready = await asyncio.gather(
                self._probe_url(client, target.health_url),
                self._probe_url(client, target.ready_url),
            )
        tunnel = (
            await self._probe_url(client, target.tunnel_ready_url)
            if target.tunnel_ready_url
            else None
        )
        if not health["ok"] or not ready["ok"]:
            state = "offline"
        elif tunnel is not None and not tunnel["ok"]:
            state = "degraded"
        else:
            state = "online"
        version = ready.get("version") or health.get("version")
        return {
            **target.model_dump(),
            "health_url": None,
            "ready_url": None,
            "tunnel_ready_url": None,
            "state": state,
            "version": version,
            "mcp": ready,
            "tunnel": tunnel,
        }

    @staticmethod
    async def _probe_url(client: httpx.AsyncClient, url: str | None) -> dict[str, Any]:
        if not url:
            return {"ok": False, "latencyMs": None, "status": None, "version": None}
        started = time.perf_counter()
        try:
            response = await client.get(url)
            latency = round((time.perf_counter() - started) * 1000)
            version: str | None = None
            try:
                payload: object = response.json()
                if isinstance(payload, dict):
                    payload_dict = cast(dict[str, Any], payload)
                    if payload_dict.get("version") is not None:
                        version = str(payload_dict["version"])
            except ValueError:
                pass
            return {
                "ok": response.status_code < 400,
                "latencyMs": latency,
                "status": response.status_code,
                "version": version,
            }
        except httpx.HTTPError:
            return {"ok": False, "latencyMs": None, "status": None, "version": None}

    async def _activity(self) -> list[dict[str, Any]]:
        rows = await self.runtime.database.fetchall(
            """
            SELECT strftime('%Y-%m-%dT%H:00:00Z', created_at) AS bucket,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN result = 'success' THEN 0 ELSE 1 END) AS failures
            FROM tool_invocations
            WHERE created_at >= datetime('now','-23 hours')
            GROUP BY bucket ORDER BY bucket
            """
        )
        indexed = {str(row["bucket"]): row for row in rows}
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        result: list[dict[str, Any]] = []
        for hours_ago in range(23, -1, -1):
            bucket_time = now.timestamp() - hours_ago * 3600
            bucket = datetime.fromtimestamp(bucket_time, UTC).strftime("%Y-%m-%dT%H:00:00Z")
            row: dict[str, Any] = indexed.get(bucket, {})
            result.append(
                {
                    "bucket": bucket,
                    "calls": int(row.get("calls", 0)),
                    "failures": int(row.get("failures", 0)),
                }
            )
        return result

    async def _recent_invocations(self) -> list[dict[str, Any]]:
        return await self.runtime.database.fetchall(
            """
            SELECT module_id AS module, tool_name AS tool, duration_ms AS durationMs,
                   result, created_at AS createdAt
            FROM tool_invocations ORDER BY id DESC LIMIT 8
            """
        )


_DASHBOARD_MONITORS: WeakKeyDictionary[GatewayRuntime, DashboardMonitor] = WeakKeyDictionary()


def get_dashboard_monitor(runtime: GatewayRuntime) -> DashboardMonitor:
    """Share one truth/cache/checkpoint coordinator across Gateway surfaces."""

    monitor = _DASHBOARD_MONITORS.get(runtime)
    if monitor is None:
        monitor = DashboardMonitor(runtime)
        _DASHBOARD_MONITORS[runtime] = monitor
    return monitor
