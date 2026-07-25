from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.storage.database import Database


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def claim(
        self, scope: str, request_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> dict[str, Any] | None:
        digest = request_hash(payload)
        now = int(time.time())
        await self.database.execute("DELETE FROM idempotency_records WHERE expires_at < ?", (now,))
        inserted = await self.database.execute(
            """
            INSERT OR IGNORE INTO idempotency_records
              (scope,request_id,request_hash,status,expires_at)
            VALUES (?,?,?,'in_progress',?)
            """,
            (scope, request_id, digest, now + ttl_seconds),
        )
        row = await self.database.fetchone(
            "SELECT request_hash,status,result_json FROM idempotency_records "
            "WHERE scope=? AND request_id=?",
            (scope, request_id),
        )
        if row is None:
            raise GatewayError("INTERNAL_ERROR", "Unable to claim idempotency record")
        if row["request_hash"] != digest:
            raise GatewayError(
                "CONFLICT", "The requestId was already used with a different payload"
            )
        if not inserted and row["status"] == "in_progress":
            raise GatewayError(
                "REQUEST_IN_PROGRESS",
                "An identical request is already being processed",
                retryable=True,
            )
        if row["status"] == "completed" and row["result_json"]:
            result: object = json.loads(str(row["result_json"]))
            return cast(dict[str, Any], result) if isinstance(result, dict) else {"value": result}
        if row["status"] == "failed" and row["result_json"]:
            result: object = json.loads(str(row["result_json"]))
            if isinstance(result, dict):
                error_result = cast(dict[str, Any], result)
                raw_details = error_result.get("details", {})
                details = cast(dict[str, Any], raw_details) if isinstance(raw_details, dict) else {}
                raise GatewayError(
                    str(error_result.get("code", "INTERNAL_ERROR")),
                    str(error_result.get("message", "Previous request failed")),
                    retryable=bool(error_result.get("retryable", False)),
                    details=details,
                )
        return None

    async def complete(self, scope: str, request_id: str, result: dict[str, Any]) -> None:
        await self.database.execute(
            "UPDATE idempotency_records SET status='completed',result_json=? "
            "WHERE scope=? AND request_id=?",
            (json.dumps(result, ensure_ascii=False, separators=(",", ":")), scope, request_id),
        )

    async def fail(self, scope: str, request_id: str, error: GatewayError) -> None:
        payload = {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "details": error.details,
        }
        await self.database.execute(
            "UPDATE idempotency_records SET status='failed',result_json=? "
            "WHERE scope=? AND request_id=?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), scope, request_id),
        )
