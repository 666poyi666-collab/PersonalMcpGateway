from __future__ import annotations

from typing import Any, cast

SENSITIVE_KEYS = {
    "authorization",
    "pairingcode",
    "pairingtoken",
    "runtimeapikey",
    "token",
    "api_key",
    "apikey",
    "secret",
    "latitude",
    "longitude",
    "route",
    "stages",
    "content",
    "body",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in cast(list[object], value)]
    return value
