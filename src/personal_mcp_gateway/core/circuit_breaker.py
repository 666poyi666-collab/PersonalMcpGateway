from __future__ import annotations

import time
from enum import StrEnum

from personal_mcp_gateway.core.errors import GatewayError


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 15) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.opened_at = 0.0
        self.state = CircuitState.CLOSED

    def before_call(self) -> None:
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at < self.recovery_seconds:
                raise GatewayError(
                    "DEPENDENCY_OFFLINE", "Dependency circuit is open", retryable=True
                )
            self.state = CircuitState.HALF_OPEN

    def success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def failure(self) -> None:
        self.failures += 1
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
