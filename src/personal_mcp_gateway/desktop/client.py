"""Gateway polling for the desktop shell.

The desktop window renders from a ``file://`` origin, so every HTTP call is made
here in Python and handed to the page through the pywebview JS bridge. That keeps
the browser out of cross-origin territory and lets the tray icon and the window
share one snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from personal_mcp_gateway.desktop.window_state import state_dir

DEFAULT_ADMIN_URL = "http://127.0.0.1:8761"
LAST_GOOD_CACHE_SCHEMA_VERSION = 1
MAX_CACHE_BYTES = 4 * 1024 * 1024
MAX_REPORTED_FAILURES = 999
RETRY_DELAYS_SECONDS = (4.0, 8.0, 16.0, 30.0, 60.0)

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


def snapshot_cache_path() -> Path:
    return state_dir() / "last-good-dashboard.json"


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_dashboard_data(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    data = cast(dict[str, Any], value)
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return False
    summary_data = cast(dict[str, Any], summary)
    counts = [
        _nonnegative_int(summary_data.get(name))
        for name in ("total", "online", "degraded", "offline")
    ]
    if any(count is None for count in counts):
        return False
    total, online, degraded, offline = cast(list[int], counts)
    if online + degraded + offline > total:
        return False
    for name in ("targets", "widgets", "errors", "events"):
        candidate = data.get(name)
        if candidate is not None and not isinstance(candidate, list):
            return False
        if isinstance(candidate, list):
            candidate_items = cast(list[object], candidate)
            if any(not isinstance(item, dict) for item in candidate_items):
                return False
    targets = data.get("targets")
    if isinstance(targets, list):
        for target in cast(list[object], targets):
            target_data = cast(dict[str, Any], target)
            if not isinstance(target_data.get("id"), str) or not target_data["id"]:
                return False
    activity = data.get("activity")
    if activity is not None:
        if not isinstance(activity, dict):
            return False
        activity_data = cast(dict[str, Any], activity)
        for name in ("hourly", "recent"):
            candidate = activity_data.get(name)
            if candidate is not None and not isinstance(candidate, list):
                return False
            if isinstance(candidate, list):
                candidate_items = cast(list[object], candidate)
                if any(not isinstance(item, dict) for item in candidate_items):
                    return False
    for name in ("gateway", "fleet"):
        candidate = data.get(name)
        if candidate is not None and not isinstance(candidate, dict):
            return False
    return True


def _load_last_good(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    cache = cast(dict[str, Any], raw)
    if cache.get("schemaVersion") != LAST_GOOD_CACHE_SCHEMA_VERSION:
        return None
    data = cache.get("data")
    if not _valid_dashboard_data(data):
        return None
    return cast(dict[str, Any], data)


def _save_last_good(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    payload = {
        "schemaVersion": LAST_GOOD_CACHE_SCHEMA_VERSION,
        "savedAt": datetime.now(UTC).isoformat(),
        "data": data,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CACHE_BYTES:
        return
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    except OSError:
        # Cache persistence is best-effort; it must never stop live polling.
        return
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
    if not payload or not _valid_dashboard_data(payload):
        return STATUS_DISCONNECTED
    summary_dict = cast(dict[str, Any], payload["summary"])
    offline = cast(int, summary_dict["offline"])
    degraded = cast(int, summary_dict["degraded"])
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
    cache_path: Path | None = None
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _last_good: dict[str, Any] | None = field(default=None, init=False)
    _last_error: str | None = field(default=None, init=False)
    _next_attempt_at: float = field(default=0.0, init=False)
    _last_snapshot: DesktopSnapshot | None = field(default=None, init=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _request_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._last_good = _load_last_good(self.cache_path)

    def initial_snapshot(self) -> DesktopSnapshot:
        now = datetime.now(UTC).isoformat()
        with self._state_lock:
            if self._last_good is None:
                return DesktopSnapshot(
                    connected=False,
                    status=STATUS_DISCONNECTED,
                    fetched_at=now,
                )
            return DesktopSnapshot(
                connected=True,
                status=STATUS_DEGRADED,
                fetched_at=now,
                data=self._last_good,
                error="StartupCache",
                stale=True,
            )

    def _snapshot_for_failure(self, now: str, error: str) -> DesktopSnapshot:
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

    def _failure(self, now: str, error: str) -> DesktopSnapshot:
        with self._state_lock:
            self._failures = min(self._failures + 1, MAX_REPORTED_FAILURES)
            self._last_error = error
            delay_index = min(self._failures - 1, len(RETRY_DELAYS_SECONDS) - 1)
            self._next_attempt_at = self.clock() + RETRY_DELAYS_SECONDS[delay_index]
            snapshot = self._snapshot_for_failure(now, error)
            self._last_snapshot = snapshot
            return snapshot

    def fetch(self, *, force: bool = False) -> DesktopSnapshot:
        if not self._request_lock.acquire(blocking=False):
            with self._request_lock:
                pass
            with self._state_lock:
                return self._last_snapshot or self._snapshot_for_failure(
                    datetime.now(UTC).isoformat(), "RequestInFlight"
                )
        try:
            return self._fetch_once(force=force)
        finally:
            self._request_lock.release()

    def _fetch_once(self, *, force: bool) -> DesktopSnapshot:
        now = datetime.now(UTC).isoformat()
        with self._state_lock:
            if not force and self.clock() < self._next_attempt_at:
                snapshot = self._snapshot_for_failure(
                    now, self._last_error or "RetryBackoff"
                )
                self._last_snapshot = snapshot
                return snapshot
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
        data = cast(dict[str, Any], payload)
        if not _valid_dashboard_data(data):
            return self._failure(now, "InvalidPayload")
        with self._state_lock:
            self._failures = 0
            self._last_error = None
            self._next_attempt_at = 0.0
            self._last_good = data
            _save_last_good(self.cache_path, data)
        snapshot = DesktopSnapshot(
            connected=True,
            status=derive_status(data),
            fetched_at=now,
            data=data,
        )
        with self._state_lock:
            self._last_snapshot = snapshot
        return snapshot
