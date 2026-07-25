from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import RetryConfig, TimeoutConfig
from personal_mcp_gateway.modules.watch.client import EndpointConfig, WatchHttpClient, encoded_id
from personal_mcp_gateway.storage.database import Database


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[WatchHttpClient]:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    value = WatchHttpClient(
        EndpointConfig("phone", "", "expected", "phoneDeviceId", "http://phone", "token"),
        database,
        TimeoutConfig(),
        RetryConfig(max_attempts=1),
    )
    yield value
    await value.close()


def test_response_mapping_and_encoding(client: WatchHttpClient) -> None:
    assert client._decode(httpx.Response(200, json={"ok": True}))["ok"] is True  # pyright: ignore[reportPrivateUsage]
    assert encoded_id("a/b") == "a%2Fb"
    for status, code in [(401, "PHONE_AUTH_FAILED"), (404, "NOT_FOUND"), (429, "RATE_LIMITED")]:
        with pytest.raises(GatewayError) as error:
            client._decode(httpx.Response(status, json={}))  # pyright: ignore[reportPrivateUsage]
        assert error.value.code == code
    with pytest.raises(GatewayError) as invalid:
        client._decode(httpx.Response(200, text="not-json"))  # pyright: ignore[reportPrivateUsage]
    assert invalid.value.code == "MODULE_PROTOCOL_ERROR"


@pytest.mark.asyncio
async def test_verify_rejects_wrong_identity(client: WatchHttpClient) -> None:
    async def request(method: str, path: str, json_body: object = None) -> object:
        return {"phoneDeviceId": "wrong"}

    client.request = request  # type: ignore[method-assign]
    with pytest.raises(GatewayError) as error:
        await client.verify()
    assert error.value.code == "AUTH_FAILED"
