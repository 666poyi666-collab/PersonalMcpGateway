from pathlib import Path

import pytest

from personal_mcp_gateway.storage.database import Database


@pytest.mark.asyncio
async def test_migrations_are_repeatable(tmp_path: Path) -> None:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    await database.migrate()
    rows = await database.fetchall("SELECT version FROM schema_migrations")
    assert rows == [{"version": 1}, {"version": 2}]
    mode = await database.fetchone("PRAGMA journal_mode")
    assert mode == {"journal_mode": "wal"}
    assert await database.readiness() == {
        "state": "ready",
        "schemaVersion": 2,
        "expectedSchemaVersion": 2,
    }


@pytest.mark.asyncio
async def test_cleanup_applies_retention_windows(tmp_path: Path) -> None:
    database = Database(tmp_path / "gateway.db")
    await database.migrate()
    await database.execute(
        "INSERT INTO tool_invocations"
        "(request_id,module_id,tool_name,duration_ms,result,created_at) "
        "VALUES ('r','m','t',1,'ok',datetime('now','-8 days'))"
    )
    await database.execute(
        "INSERT INTO audit_events(event,summary_json,created_at) "
        "VALUES ('old','{}',datetime('now','-91 days'))"
    )
    await database.cleanup()
    assert await database.fetchall("SELECT * FROM tool_invocations") == []
    assert await database.fetchall("SELECT * FROM audit_events") == []
