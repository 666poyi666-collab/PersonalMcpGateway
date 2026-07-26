from __future__ import annotations

import httpx
import respx

from personal_mcp_gateway.desktop.client import (
    STATUS_DEGRADED,
    STATUS_DISCONNECTED,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    DesktopSnapshot,
    GatewayClient,
    derive_status,
    tray_tooltip,
)

URL = "http://127.0.0.1:8761/admin/dashboard-data"


def _payload(online: int = 4, degraded: int = 0, offline: int = 0) -> dict[str, object]:
    return {
        "summary": {
            "total": online + degraded + offline,
            "online": online,
            "degraded": degraded,
            "offline": offline,
        }
    }


def test_derive_status_prefers_the_most_severe_state() -> None:
    assert derive_status(_payload()) == STATUS_ONLINE
    assert derive_status(_payload(3, degraded=1)) == STATUS_DEGRADED
    assert derive_status(_payload(2, degraded=1, offline=1)) == STATUS_OFFLINE


def test_derive_status_handles_missing_or_malformed_payloads() -> None:
    assert derive_status(None) == STATUS_DISCONNECTED
    assert derive_status({}) == STATUS_DISCONNECTED
    assert derive_status({"summary": "nope"}) == STATUS_DISCONNECTED


def test_tray_tooltip_reports_counts_without_leaking_endpoints() -> None:
    snapshot = DesktopSnapshot(
        connected=True,
        status=STATUS_ONLINE,
        fetched_at="2026-07-26T10:00:00Z",
        data=_payload(),
    )
    tooltip = tray_tooltip(snapshot)
    assert "4/4 在线" in tooltip
    assert "127.0.0.1" not in tooltip

    disconnected = DesktopSnapshot(
        connected=False,
        status=STATUS_DISCONNECTED,
        fetched_at="2026-07-26T10:00:00Z",
    )
    assert "网关未连接" in tray_tooltip(disconnected)


@respx.mock
def test_fetch_returns_a_connected_snapshot() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=_payload(3, degraded=1)))
    snapshot = GatewayClient().fetch()
    assert snapshot.connected is True
    assert snapshot.status == STATUS_DEGRADED
    assert snapshot.consecutive_failures == 0
    assert snapshot.to_payload()["statusLabel"] == "部分降级"


@respx.mock
def test_fetch_counts_consecutive_failures_and_never_raises() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    client = GatewayClient()
    first = client.fetch()
    second = client.fetch()
    assert first.connected is False
    assert first.status == STATUS_DISCONNECTED
    assert second.consecutive_failures == 2
    assert second.data is None


@respx.mock
def test_fetch_rejects_non_object_payloads() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    snapshot = GatewayClient().fetch()
    assert snapshot.connected is False
    assert snapshot.error == "InvalidPayload"


@respx.mock
def test_fetch_recovers_after_a_failure() -> None:
    route = respx.get(URL)
    route.mock(side_effect=httpx.ConnectError("refused"))
    client = GatewayClient()
    client.fetch()
    route.mock(return_value=httpx.Response(200, json=_payload()))
    recovered = client.fetch()
    assert recovered.connected is True
    assert recovered.consecutive_failures == 0
