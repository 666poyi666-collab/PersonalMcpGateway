from __future__ import annotations

import os

from personal_mcp_gateway.core.errors import GatewayError


class SecretStore:
    """Resolve logical secret references without putting values in module YAML."""

    def get(self, reference: str) -> str:
        key = "PERSONAL_MCP_SECRET_" + reference.upper().replace(".", "_").replace("-", "_")
        value = os.environ.get(key, "")
        if not value:
            raise GatewayError("AUTH_FAILED", f"Secret is not configured: {reference}")
        return value
