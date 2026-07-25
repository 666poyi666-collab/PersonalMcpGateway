import asyncio
from pathlib import Path

import pytest

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.storage.database import Database
from personal_mcp_gateway.storage.idempotency_store import IdempotencyStore


@pytest.mark.asyncio
async def test_returns_first_result_and_rejects_hash_conflict(tmp_path: Path) -> None:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    store = IdempotencyStore(database)
    assert await store.claim("write", "id-1", {"value": 1}, 60) is None
    await store.complete("write", "id-1", {"saved": True})
    assert await store.claim("write", "id-1", {"value": 1}, 60) == {"saved": True}
    with pytest.raises(GatewayError) as error:
        await store.claim("write", "id-1", {"value": 2}, 60)
    assert error.value.code == "CONFLICT"


@pytest.mark.asyncio
async def test_replays_first_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    store = IdempotencyStore(database)
    payload = {"value": 1}
    await store.claim("write", "id-2", payload, 60)
    await store.fail("write", "id-2", GatewayError("CONFLICT", "old revision"))
    with pytest.raises(GatewayError, match="old revision"):
        await store.claim("write", "id-2", payload, 60)


@pytest.mark.asyncio
async def test_only_one_concurrent_caller_claims_execution(tmp_path: Path) -> None:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    store = IdempotencyStore(database)

    results = await asyncio.gather(
        store.claim("write", "id-3", {"value": 1}, 60),
        store.claim("write", "id-3", {"value": 1}, 60),
        return_exceptions=True,
    )

    assert results.count(None) == 1
    errors = [result for result in results if isinstance(result, GatewayError)]
    assert len(errors) == 1
    assert errors[0].code == "REQUEST_IN_PROGRESS"
