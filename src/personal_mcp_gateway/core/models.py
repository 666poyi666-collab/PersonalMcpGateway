from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ExecutionMode(StrEnum):
    HTTP = "http"
    SUBPROCESS = "subprocess"
    IN_PROCESS = "in_process"


class PermissionLevel(StrEnum):
    READ = "READ"
    WRITE_REVERSIBLE = "WRITE_REVERSIBLE"
    WRITE_DESTRUCTIVE = "WRITE_DESTRUCTIVE"


class HealthState(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    NOT_INSTALLED = "not_installed"


class ModuleHealth(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    state: HealthState
    message: str | None = None
    last_success_at: str | None = Field(default=None, alias="lastSuccessAt")
    phone: str | None = None
    watch: str | None = None
    protocol_version: Any | None = Field(default=None, alias="protocolVersion")
    watch_version: Any | None = Field(default=None, alias="watchVersion")


class GatewayResult[ResultData](BaseModel):
    """Stable public envelope shape used by module and protocol boundaries."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    module: str
    request_id: str = Field(alias="requestId")
    data: ResultData | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class DiscoveryConfig(BaseModel):
    type: str = "mdns"
    service_type: str = ""
    expected_device_id: str = ""


class FallbackConfig(BaseModel):
    base_url: str = ""


class AuthenticationConfig(BaseModel):
    type: str = "pairing_token"
    secret_ref: str = ""


class TimeoutConfig(BaseModel):
    connect_seconds: float = 3
    read_seconds: float = 15
    write_seconds: float = 20


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=5)
    base_delay_ms: int = Field(default=300, ge=0, le=5000)


class ModuleManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str
    enabled: bool = True
    adapter_version: int = Field(default=1, ge=1)
    priority: int = 100
    execution_mode: ExecutionMode = ExecutionMode.HTTP
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    authentication: AuthenticationConfig = Field(default_factory=AuthenticationConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ResourceHandler = Callable[[dict[str, str]], Awaitable[str]]


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    permission: PermissionLevel
    idempotent: bool
    timeout_seconds: float
    handler: ToolHandler = Field(exclude=True)


class ResourceDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    uri: str
    name: str
    description: str
    handler: ResourceHandler = Field(exclude=True)


class GatewayModule(Protocol):
    module_id: str
    display_name: str
    version: str
    manifest: ModuleManifest

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> ModuleHealth: ...
    def tools(self) -> list[ToolDefinition]: ...
    def resources(self) -> list[ResourceDefinition]: ...
