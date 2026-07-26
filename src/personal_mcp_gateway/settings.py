from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "Poyi" / "PersonalMcpGateway"
    return Path.home() / ".personal-mcp-gateway"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PERSONAL_MCP_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    modules_dir: Path = Path("modules")
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8760
    admin_host: str = "127.0.0.1"
    admin_port: int = 8761
    log_level: str = "INFO"
    version: str = "0.1.0.dev0"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "gateway.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "gateway.jsonl"

    @property
    def dashboard_targets_path(self) -> Path:
        return self.data_dir / "dashboard-targets.yaml"
