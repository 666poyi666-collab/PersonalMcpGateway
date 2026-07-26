from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import ExecutionMode, ModuleManifest
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.secrets import SecretStore
from personal_mcp_gateway.storage.database import Database


def load_modules(directory: Path, database: Database, secrets: SecretStore) -> ModuleRegistry:
    registry = ModuleRegistry()
    if not directory.exists():
        return registry
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw_value = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw_value, dict):
                raise ValueError("manifest root must be an object")
            raw = cast(dict[str, Any], raw_value)
            manifest = ModuleManifest.model_validate(raw)
            if not manifest.enabled:
                continue
            if manifest.execution_mode is not ExecutionMode.HTTP:
                raise GatewayError(
                    "MODULE_PROTOCOL_ERROR",
                    f"Execution mode {manifest.execution_mode} is reserved but not implemented",
                )
            raise GatewayError("MODULE_UNAVAILABLE", f"No adapter is installed for {manifest.id}")
        except GatewayError as exc:
            registry.record_load_error(path.stem, exc)
        except Exception as exc:
            registry.record_load_error(
                path.stem,
                GatewayError(
                    "MODULE_PROTOCOL_ERROR",
                    f"Invalid module manifest: {path.name}",
                    details={"type": type(exc).__name__},
                ),
            )
    return registry
