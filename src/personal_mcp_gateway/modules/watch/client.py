from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote

import httpx
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from personal_mcp_gateway.core.circuit_breaker import CircuitBreaker
from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import RetryConfig, TimeoutConfig
from personal_mcp_gateway.storage.database import Database


@dataclass(slots=True)
class EndpointConfig:
    role: str
    service_type: str
    expected_device_id: str
    id_field: str
    fallback_base_url: str
    pairing_code: str


class _Listener(ServiceListener):
    def __init__(self) -> None:
        self.addresses: list[tuple[str, int]] = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=1200)
        if info:
            port = info.port
            if port is not None:
                self.addresses.extend((address, port) for address in info.parsed_addresses())

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        return None


class WatchHttpClient:
    def __init__(
        self,
        config: EndpointConfig,
        database: Database,
        timeouts: TimeoutConfig,
        retry: RetryConfig,
    ) -> None:
        self.config = config
        self.database = database
        self.retry = retry
        self.breaker = CircuitBreaker()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=timeouts.connect_seconds,
                read=timeouts.read_seconds,
                write=timeouts.write_seconds,
                pool=timeouts.connect_seconds,
            ),
            headers={"X-Pairing-Code": config.pairing_code},
        )
        self._runtime_base_url = ""

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def closed(self) -> bool:
        return self.client.is_closed

    async def request(self, method: str, path: str, json_body: Any | None = None) -> Any:
        self.breaker.before_call()
        base_urls = await self._candidate_urls()
        last_error: GatewayError | None = None
        for base_url in base_urls:
            for attempt in range(self.retry.max_attempts):
                try:
                    response = await self.client.request(
                        method, base_url.rstrip("/") + path, json=json_body
                    )
                    result = self._decode(response)
                    self.breaker.success()
                    self._runtime_base_url = base_url
                    await self._save_endpoint(base_url, result if path == "/v1/status" else None)
                    return result
                except GatewayError as exc:
                    last_error = exc
                    if not exc.retryable:
                        raise
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = self._offline_error(type(exc).__name__)
                if attempt + 1 < self.retry.max_attempts:
                    await asyncio.sleep(self.retry.base_delay_ms / 1000 * (2**attempt))
        self.breaker.failure()
        raise last_error or self._offline_error("no_endpoint")

    async def _candidate_urls(self) -> list[str]:
        candidates: list[str] = []
        if self._runtime_base_url:
            candidates.append(self._runtime_base_url)
        if self.config.expected_device_id:
            row = await self.database.fetchone(
                "SELECT base_url FROM discovered_endpoints WHERE module_id=? AND device_id=?",
                (f"watch.{self.config.role}", self.config.expected_device_id),
            )
            if row:
                candidates.append(str(row["base_url"]))
        candidates.extend(await asyncio.to_thread(self._discover_urls))
        if self.config.fallback_base_url:
            candidates.append(self.config.fallback_base_url)
        result: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in result:
                result.append(candidate)
        return result

    def _discover_urls(self) -> list[str]:
        if not self.config.service_type:
            return []
        listener = _Listener()
        zeroconf = Zeroconf()
        browser = ServiceBrowser(zeroconf, self.config.service_type, listener)
        try:
            time.sleep(1.5)
            return [f"http://{host}:{port}" for host, port in listener.addresses]
        finally:
            browser.cancel()
            zeroconf.close()

    def _decode(self, response: httpx.Response) -> Any:
        if response.status_code == 401:
            prefix = "PHONE" if self.config.role == "phone" else "WATCH"
            raise GatewayError(f"{prefix}_AUTH_FAILED", "Pairing credential was rejected")
        if response.status_code == 404:
            raise GatewayError("NOT_FOUND", "Requested WatchIntervals object was not found")
        if response.status_code == 409:
            try:
                raw_details: object = response.json()
            except ValueError:
                raw_details = {}
            details = cast(dict[str, Any], raw_details) if isinstance(raw_details, dict) else {}
            code = str(details.get("error", "CONFLICT")).upper()
            raise GatewayError(code, "WatchIntervals rejected the request", details=details)
        if response.status_code == 429:
            raise GatewayError("RATE_LIMITED", "WatchIntervals rate limit exceeded", retryable=True)
        if response.status_code >= 500:
            raise self._offline_error(f"http_{response.status_code}")
        if response.status_code >= 400:
            raise GatewayError("INVALID_ARGUMENT", "WatchIntervals rejected the request")
        try:
            value: object = response.json()
            return value
        except ValueError as exc:
            raise GatewayError(
                "MODULE_PROTOCOL_ERROR", "WatchIntervals returned invalid JSON", retryable=True
            ) from exc

    async def verify(self) -> dict[str, Any]:
        result = await self.request("GET", "/v1/status")
        if not isinstance(result, dict):
            raise GatewayError("MODULE_PROTOCOL_ERROR", "Status response must be an object")
        result_object = cast(dict[str, Any], result)
        actual = str(result_object.get(self.config.id_field, ""))
        expected = self.config.expected_device_id
        if expected and actual != expected:
            raise GatewayError(
                "AUTH_FAILED", "Discovered device identity does not match the paired device"
            )
        return result_object

    async def _save_endpoint(self, base_url: str, status: Any | None) -> None:
        device_id = self.config.expected_device_id
        if isinstance(status, dict):
            status_object = cast(dict[str, Any], status)
            device_id = str(status_object.get(self.config.id_field, device_id))
        if not device_id:
            return
        await self.database.execute(
            """
            INSERT INTO discovered_endpoints(module_id,device_id,base_url,verified_at)
            VALUES (?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(module_id,device_id) DO UPDATE SET
              base_url=excluded.base_url,verified_at=CURRENT_TIMESTAMP
            """,
            (f"watch.{self.config.role}", device_id, base_url),
        )

    def _offline_error(self, reason: str) -> GatewayError:
        prefix = "PHONE" if self.config.role == "phone" else "WATCH"
        code = f"{prefix}_OFFLINE"
        if reason in {"ReadTimeout", "ConnectTimeout"}:
            code = "MODULE_TIMEOUT"
        return GatewayError(code, f"{self.config.role.title()} is offline", retryable=True)


def encoded_id(value: str) -> str:
    return quote(value, safe="")
