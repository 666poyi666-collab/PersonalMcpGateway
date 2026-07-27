"""Gateway polling for the desktop shell.

The desktop window renders from a ``file://`` origin, so every HTTP call is made
here in Python and handed to the page through the pywebview JS bridge. That keeps
the browser out of cross-origin territory and lets the tray icon and the window
share one snapshot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx

DEFAULT_ADMIN_URL = "http://127.0.0.1:8761"

STATUS_ONLINE = "online"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"
STATUS_DISCONNECTED = "disconnected"

STATUS_LABELS = {
    STATUS_ONLINE: "全部正常",
    STATUS_DEGRADED: "部分降级",
    STATUS_OFFLINE: "存在离线",
    STATUS_DISCONNECTED: "网关未连接",
}


def admin_base_url() -> str:
    return os.environ.get("PERSONAL_MCP_ADMIN_URL", DEFAULT_ADMIN_URL).rstrip("/")


@dataclass(slots=True)
class DesktopSnapshot:
    """One polling result, safe to hand straight to the UI."""

    connected: bool
    status: str
    fetched_at: str
    data: dict[str, Any] | None = None
    error: str | None = None
    consecutive_failures: int = 0
    stale: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "status": self.status,
            "statusLabel": STATUS_LABELS.get(self.status, self.status),
            "fetchedAt": self.fetched_at,
            "data": self.data,
            "error": self.error,
            "consecutiveFailures": self.consecutive_failures,
            "stale": self.stale,
        }


def derive_status(payload: dict[str, Any] | None) -> str:
    """Collapse a dashboard payload into a single tray-level state."""
    if not payload:
        return STATUS_DISCONNECTED
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return STATUS_DISCONNECTED
    summary_dict = cast(dict[str, Any], summary)
    offline = int(summary_dict.get("offline", 0) or 0)
    degraded = int(summary_dict.get("degraded", 0) or 0)
    if offline:
        return STATUS_OFFLINE
    if degraded:
        return STATUS_DEGRADED
    return STATUS_ONLINE


def tray_tooltip(snapshot: DesktopSnapshot) -> str:
    """Short hover text for the tray icon; never leaks URLs or identifiers."""
    if not snapshot.connected or snapshot.data is None:
        return "Poyi Control Center\n网关未连接"
    summary = snapshot.data.get("summary")
    if not isinstance(summary, dict):
        return "Poyi Control Center\n网关未连接"
    summary_dict = cast(dict[str, Any], summary)
    online = int(summary_dict.get("online", 0) or 0)
    total = int(summary_dict.get("total", 0) or 0)
    label = STATUS_LABELS.get(snapshot.status, snapshot.status)
    return f"Poyi Control Center\n{online}/{total} 在线 · {label}"


@dataclass
class GatewayClient:
    """Polls the local admin API and remembers the last good snapshot."""

    base_url: str = field(default_factory=admin_base_url)
    timeout: float = 4.0
    _failures: int = field(default=0, init=False)
    _last_good: dict[str, Any] | None = field(default=None, init=False)

    def _failure(self, now: str, error: str) -> DesktopSnapshot:
        self._failures += 1
        if self._last_good is not None:
            return DesktopSnapshot(
                connected=True,
                status=STATUS_DEGRADED,
                fetched_at=now,
                data=self._last_good,
                error=error,
                consecutive_failures=self._failures,
                stale=True,
            )
        return DesktopSnapshot(
            connected=False,
            status=STATUS_DISCONNECTED,
            fetched_at=now,
            error=error,
            consecutive_failures=self._failures,
        )

    def fetch(self, *, force: bool = False) -> DesktopSnapshot:
        now = datetime.now(UTC).isoformat()
        url = f"{self.base_url}/admin/dashboard-data"
        params = {"force": "1"} if force else None
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return self._failure(now, type(exc).__name__)
        if not isinstance(payload, dict):
            return self._failure(now, "InvalidPayload")
        self._failures = 0
        data = cast(dict[str, Any], payload)
        self._last_good = data
        return DesktopSnapshot(
            connected=True,
            status=derive_status(data),
            fetched_at=now,
            data=data,
        )
