"""Configuration loading for Log MCP."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
CONFIG_ENV_VAR = "LOG_MCP_CONFIG"


class CenterConfig(BaseModel):
    """Log Center API settings."""

    base_url: str = Field(..., min_length=1)
    api_token: str = Field(..., min_length=1)
    timeout_seconds: float = Field(default=10, gt=0)
    poll_interval_seconds: float = Field(default=1, gt=0)
    poll_timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("api_token")
    @classmethod
    def reject_placeholder_token(cls, value: str) -> str:
        token = value.strip()
        if not token or token == "change-me":
            raise ValueError("center.api_token must be configured")
        return token


class McpConfig(BaseModel):
    """FastMCP runtime settings."""

    transport: Literal["stdio", "sse", "streamable-http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8081, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class AuthConfig(BaseModel):
    """MCP access-token settings."""

    bearer_token: str | None = None

    @field_validator("bearer_token")
    @classmethod
    def normalize_bearer_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token or token == "change-me":
            raise ValueError("auth.bearer_token must be configured")
        return token


class LimitsConfig(BaseModel):
    """Tool input limits."""

    default_lines: int = Field(default=200, ge=1)
    max_lines: int = Field(default=5000, ge=1)

    @field_validator("max_lines")
    @classmethod
    def max_not_smaller_than_default(cls, value: int, info: Any) -> int:
        default_lines = info.data.get("default_lines")
        if default_lines is not None and value < default_lines:
            raise ValueError("limits.max_lines must be >= limits.default_lines")
        return value


class DownloadConfig(BaseModel):
    """Downloaded log file settings."""

    dir: Path = Field(default=Path("./downloads"))
    public_base_url: str = Field(default="http://127.0.0.1:8081", min_length=1)
    token_ttl_seconds: int = Field(default=1800, gt=0)

    @field_validator("public_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        base_url = value.rstrip("/")
        if not base_url:
            raise ValueError("download.public_base_url must not be empty")
        return base_url


class AppConfig(BaseModel):
    """Complete Log MCP configuration."""

    center: CenterConfig
    mcp: McpConfig = Field(default_factory=McpConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)


def get_config_path() -> Path:
    """Return the configured YAML path."""

    configured = os.getenv(CONFIG_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate Log MCP YAML configuration."""

    config_path = Path(path).expanduser().resolve() if path else get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Create it from mcp/config.example.yaml or set {CONFIG_ENV_VAR}."
        )

    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML object")

    return AppConfig.model_validate(raw)
