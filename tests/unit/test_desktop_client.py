from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import respx

from personal_mcp_gateway.desktop.client import (
    MAX_REPORTED_FAILURES,
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
    now = [0.0]
    client = GatewayClient(clock=lambda: now[0])
    first = client.fetch()
    now[0] = 4.0
    second = client.fetch()
    assert first.connected is False
    assert first.status == STATUS_DISCONNECTED
    assert second.consecutive_failures == 2
    assert second.data is None


@respx.mock
def test_fetch_keeps_the_last_good_snapshot_during_a_transient_failure() -> None:
    route = respx.get(URL)
    route.mock(return_value=httpx.Response(200, json=_payload()))
    client = GatewayClient()
    good = client.fetch()

    route.mock(side_effect=httpx.ConnectError("refused"))
    recovering = client.fetch()

    assert recovering.connected is True
    assert recovering.status == STATUS_DEGRADED
    assert recovering.stale is True
    assert recovering.data == good.data
    assert recovering.consecutive_failures == 1


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
    now = [0.0]
    client = GatewayClient(clock=lambda: now[0])
    client.fetch()
    now[0] = 4.0
    route.mock(return_value=httpx.Response(200, json=_payload()))
    recovered = client.fetch()
    assert recovered.connected is True
    assert recovered.consecutive_failures == 0
    assert recovered.stale is False


@respx.mock
def test_last_good_snapshot_survives_a_simulated_desktop_restart(tmp_path: Path) -> None:
    cache = tmp_path / "last-good.json"
    route = respx.get(URL)
    route.mock(return_value=httpx.Response(200, json=_payload()))
    first_process = GatewayClient(cache_path=cache)
    good = first_process.fetch()

    route.mock(side_effect=httpx.ConnectError("refused"))
    restarted_process = GatewayClient(cache_path=cache)
    recovered = restarted_process.fetch()

    assert recovered.connected is True
    assert recovered.stale is True
    assert recovered.data == good.data
    assert recovered.consecutive_failures == 1


@respx.mock
def test_corrupt_or_untrusted_cache_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "last-good.json"
    cache.write_text('{"schemaVersion":1,"data":{"summary":"invalid"}}', encoding="utf-8")
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    snapshot = GatewayClient(cache_path=cache).fetch()

    assert snapshot.connected is False
    assert snapshot.data is None


@respx.mock
def test_malformed_cache_counts_never_raise(tmp_path: Path) -> None:
    cache = tmp_path / "last-good.json"
    cache.write_text(
        '{"schemaVersion":1,"data":{"summary":'
        '{"total":1,"online":"boom","degraded":0,"offline":0}}}',
        encoding="utf-8",
    )
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    snapshot = GatewayClient(cache_path=cache).fetch()

    assert snapshot.connected is False
    assert snapshot.data is None


@respx.mock
def test_cache_rejects_non_object_render_rows(tmp_path: Path) -> None:
    cache = tmp_path / "last-good.json"
    cache.write_text(
        '{"schemaVersion":1,"data":{"summary":'
        '{"total":1,"online":1,"degraded":0,"offline":0},'
        '"targets":[null],"widgets":[],"events":[],"errors":[]}}',
        encoding="utf-8",
    )
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    snapshot = GatewayClient(cache_path=cache).fetch()

    assert snapshot.connected is False
    assert snapshot.data is None


@respx.mock
def test_failure_counter_is_bounded(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    client = GatewayClient(cache_path=tmp_path / "last-good.json")
    client._failures = MAX_REPORTED_FAILURES  # pyright: ignore[reportPrivateUsage]

    snapshot = client.fetch()

    assert snapshot.consecutive_failures == MAX_REPORTED_FAILURES


@respx.mock
def test_polling_uses_bounded_backoff_and_force_bypasses_it() -> None:
    route = respx.get(URL)
    route.mock(side_effect=httpx.ConnectError("refused"))
    now = [0.0]
    client = GatewayClient(clock=lambda: now[0])

    client.fetch()
    for second in (1.0, 2.0, 3.0):
        now[0] = second
        assert client.fetch().consecutive_failures == 1
    assert route.call_count == 1

    client.fetch(force=True)
    assert route.call_count == 2
    assert client.fetch().consecutive_failures == 2


@respx.mock
def test_oversized_payload_does_not_replace_existing_cache(tmp_path: Path) -> None:
    cache = tmp_path / "last-good.json"
    route = respx.get(URL)
    route.mock(return_value=httpx.Response(200, json=_payload()))
    GatewayClient(cache_path=cache).fetch()
    original = cache.read_bytes()
    oversized = _payload()
    oversized["events"] = [{"message": "x" * (5 * 1024 * 1024)}]
    route.mock(return_value=httpx.Response(200, json=oversized))

    GatewayClient(cache_path=cache).fetch()

    assert cache.read_bytes() == original


def test_overlapping_fetches_are_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def fake_get(
        _client: httpx.Client,
        _url: str,
        *,
        params: object | None = None,
        **_kwargs: object,
    ) -> httpx.Response:
        nonlocal calls
        del params
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, json=_payload(), request=httpx.Request("GET", URL))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    client = GatewayClient()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.fetch)
        assert entered.wait(2)
        second = executor.submit(client.fetch, force=True)
        release.set()
        snapshots = [first.result(timeout=2), second.result(timeout=2)]

    assert calls == 1
    assert all(snapshot.connected for snapshot in snapshots)
