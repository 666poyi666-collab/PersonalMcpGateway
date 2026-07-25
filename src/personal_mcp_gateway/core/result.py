from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from personal_mcp_gateway.core.errors import GatewayError


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def success(
    module: str,
    request_id: str,
    data: Any,
    *,
    source: str = "gateway",
    verified: bool = True,
) -> dict[str, Any]:
    return {
        "ok": True,
        "module": module,
        "requestId": request_id,
        "data": data,
        "meta": {
            "source": source,
            "verified": verified,
            "timestamp": now_iso(),
        },
    }


def failure(module: str, request_id: str, error: GatewayError) -> dict[str, Any]:
    return {
        "ok": False,
        "module": module,
        "requestId": request_id,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "details": error.details,
        },
    }
