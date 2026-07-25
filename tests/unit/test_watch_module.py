import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import AuthenticationConfig, ModuleManifest
from personal_mcp_gateway.core.secrets import SecretStore
from personal_mcp_gateway.modules.watch.module import WatchModule
from personal_mcp_gateway.storage.database import Database


@pytest.fixture
async def watch_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[WatchModule]:
    monkeypatch.setenv("PERSONAL_MCP_SECRET_WATCH_TOKEN", "secret")
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    manifest = ModuleManifest(
        id="watch",
        display_name="Watch",
        authentication=AuthenticationConfig(secret_ref="watch.token"),
    )
    module = WatchModule(manifest, {}, database, SecretStore())
    yield module
    await module.stop()


@pytest.mark.asyncio
async def test_watch_read_models_and_resources(watch_module: WatchModule) -> None:
    async def phone_request(method: str, path: str, json_body: Any = None) -> Any:
        assert method == "GET"
        if path == "/v1/plans":
            return {"plans": [{"id": "one"}]}
        return {"phoneDeviceId": "phone", "syncState": "verified", "syncVerified": True}

    async def watch_request(method: str, path: str, json_body: Any = None) -> Any:
        if path == "/v1/history":
            return [{"id": "one"}, {"id": "two"}]
        if path.startswith("/v1/history/"):
            return {"id": "one", "route": [1], "heartRates": [2]}
        if path.startswith("/v1/sleep"):
            return {
                "state": "ready",
                "source": "system",
                "records": [
                    {
                        "timestamp": 2,
                        "totalDurationMinutes": 420,
                        "sleepScore": 80,
                        "spo2AveragePercent": 97.5,
                    },
                    {"timestamp": 1},
                ],
            }
        return {"deviceId": "watch", "appVersion": "1"}

    watch_module.phone.request = phone_request  # type: ignore[method-assign]
    watch_module.phone.verify = lambda: phone_request("GET", "/v1/status")  # type: ignore[method-assign]
    watch_module.watch.request = watch_request  # type: ignore[method-assign]
    watch_module.watch.verify = lambda: watch_request("GET", "/v1/status")  # type: ignore[method-assign]
    await watch_module.start()

    assert (await watch_module.health()).state == "healthy"
    assert (await watch_module.list_plans({}))["plans"][0]["id"] == "one"
    assert (await watch_module.list_workouts({"limit": 1}))["count"] == 1
    workout = await watch_module.get_workout({"id": "one"})
    assert "route" not in workout and workout["routeResource"].endswith("/route")
    assert (await watch_module.get_latest_sleep({}))["record"]["timestamp"] == 2
    assert (await watch_module.summarize_sleep({"days": 7}))["averageSleepScore"] == 80
    assert (await watch_module.sync_status({}))["verified"] is True
    assert '"plans"' in await watch_module.resource_plans({})


@pytest.mark.asyncio
async def test_watch_writes_are_idempotent_and_validate_control(
    watch_module: WatchModule,
) -> None:
    calls = 0

    async def request(method: str, path: str, json_body: Any = None) -> Any:
        nonlocal calls
        calls += 1
        return {"saved": True, "path": path}

    watch_module.phone.request = request  # type: ignore[method-assign]
    watch_module.watch.request = request  # type: ignore[method-assign]
    request_id = str(uuid.uuid4())
    args = {"requestId": request_id, "expectedRevision": 1, "plan": {"name": "A"}}
    assert (await watch_module.set_plan(args))["saved"] is True
    assert (await watch_module.set_plan(args))["saved"] is True
    assert calls == 1

    control = {
        "requestId": str(uuid.uuid4()),
        "expectedRevision": 1,
        "commandId": str(uuid.uuid4()),
        "expectedState": "idle",
        "expiresAt": int(time.time() * 1000) - 1,
    }
    with pytest.raises(GatewayError) as error:
        await watch_module.start_workout(control)
    assert error.value.code == "COMMAND_EXPIRED"
    with pytest.raises(GatewayError):
        await watch_module.set_plan({"requestId": "bad", "expectedRevision": 1, "plan": {}})


@pytest.mark.asyncio
async def test_watch_health_distinguishes_phone_and_watch_offline(
    watch_module: WatchModule,
) -> None:
    await watch_module.start()

    async def offline() -> dict[str, Any]:
        raise GatewayError("PHONE_OFFLINE", "offline")

    watch_module.phone.verify = offline  # type: ignore[method-assign]
    assert (await watch_module.health()).phone == "offline"

    async def online() -> dict[str, Any]:
        return {"protocolVersion": 2}

    watch_module.phone.verify = online  # type: ignore[method-assign]
    watch_module.watch.verify = offline  # type: ignore[method-assign]
    assert (await watch_module.health()).watch == "offline"


@pytest.mark.asyncio
async def test_watch_module_recreates_clients_after_restart(watch_module: WatchModule) -> None:
    old_phone = watch_module.phone
    old_watch = watch_module.watch
    await watch_module.stop()
    assert old_phone.closed and old_watch.closed
    await watch_module.start()
    assert watch_module.phone is not old_phone and not watch_module.phone.closed
    assert watch_module.watch is not old_watch and not watch_module.watch.closed
