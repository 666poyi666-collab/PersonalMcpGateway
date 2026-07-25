from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class ModuleDisabledError(GatewayError):
    def __init__(self, module_id: str) -> None:
        super().__init__("MODULE_DISABLED", f"Module {module_id} is disabled")
