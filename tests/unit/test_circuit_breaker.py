import time

import pytest

from personal_mcp_gateway.core.circuit_breaker import CircuitBreaker, CircuitState
from personal_mcp_gateway.core.errors import GatewayError


def test_circuit_opens_and_half_opens() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=0.01)
    breaker.failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(GatewayError, match="circuit is open"):
        breaker.before_call()
    time.sleep(0.02)
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.success()
    assert breaker.state is CircuitState.CLOSED
