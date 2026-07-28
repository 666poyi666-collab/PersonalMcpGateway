from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS modules (
      module_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, version TEXT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS module_health (
      module_id TEXT PRIMARY KEY, state TEXT NOT NULL, health_json TEXT NOT NULL,
      checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idempotency_records (
      scope TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
      status TEXT NOT NULL, result_json TEXT, expires_at INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(scope, request_id)
    );
    CREATE TABLE IF NOT EXISTS tool_invocations (
      id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, module_id TEXT NOT NULL,
      tool_name TEXT NOT NULL, duration_ms INTEGER NOT NULL, result TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, summary_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS recent_errors (
      id INTEGER PRIMARY KEY AUTOINCREMENT, module_id TEXT NOT NULL, code TEXT NOT NULL,
      message TEXT NOT NULL, details_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS discovered_endpoints (
      module_id TEXT NOT NULL, device_id TEXT NOT NULL, base_url TEXT NOT NULL,
      verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(module_id, device_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS authority_revision_checkpoints (
      product_id TEXT NOT NULL,
      public_key_hash TEXT NOT NULL,
      revision INTEGER NOT NULL CHECK(revision >= 0),
      truth_hash TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(product_id)
    );
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    async def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(self._run_sync, operation)

    def _run_sync(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            return operation(connection)

    async def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version INTEGER PRIMARY KEY,
                  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for index, script in enumerate(MIGRATIONS, start=1):
                applied = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?", (index,)
                ).fetchone()
                if not applied:
                    connection.executescript(script)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (index,)
                    )

        await self._run(operation)

    async def readiness(self) -> dict[str, Any]:
        """Verify that the live database is readable and fully migrated."""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            integrity = connection.execute("PRAGMA quick_check(1)").fetchone()
            integrity_state = str(integrity[0]) if integrity else "missing"
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            schema_version = int(row[0] or 0) if row else 0
            expected_schema_version = len(MIGRATIONS)
            if integrity_state != "ok" or schema_version != expected_schema_version:
                raise RuntimeError("database_not_ready")
            return {
                "state": "ready",
                "schemaVersion": schema_version,
                "expectedSchemaVersion": expected_schema_version,
            }

        return await self._run(operation)

    async def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        """Execute one serialized statement and return its affected row count."""

        return await self._run(lambda connection: connection.execute(sql, parameters).rowcount)

    async def fetchone(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

        return await self._run(operation)

    async def fetchall(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await self._run(
            lambda connection: [dict(row) for row in connection.execute(sql, parameters)]
        )

    async def record_error(
        self, module_id: str, code: str, message: str, details: dict[str, Any]
    ) -> None:
        await self.execute(
            "INSERT INTO recent_errors(module_id,code,message,details_json) VALUES (?,?,?,?)",
            (module_id, code, message, json.dumps(details, separators=(",", ":"))),
        )

    async def cleanup(self) -> None:
        now = int(time.time())
        await self.execute("DELETE FROM idempotency_records WHERE expires_at < ?", (now,))
        await self.execute(
            "DELETE FROM tool_invocations WHERE created_at < datetime('now','-7 days')"
        )
        await self.execute("DELETE FROM recent_errors WHERE created_at < datetime('now','-7 days')")
        await self.execute("DELETE FROM audit_events WHERE created_at < datetime('now','-90 days')")
