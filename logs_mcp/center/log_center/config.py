"""Configuration for Log Center."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
CENTER_CONFIG_ENV_VAR = "LOG_CENTER_CONFIG"


class AuthConfig(BaseModel):
    """Token settings for Center APIs."""

    api_token: str = Field(..., min_length=1)
    agent_token: str = Field(..., min_length=1)

    @field_validator("api_token", "agent_token")
    @classmethod
    def reject_placeholder_token(cls, value: str) -> str:
        token = value.strip()
        if not token or token == "change-me":
            raise ValueError("auth tokens must be configured")
        return token


class ServerConfig(BaseModel):
    """HTTP server settings."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"


class LimitsConfig(BaseModel):
    """Center-side task limits."""

    default_lines: int = Field(default=200, ge=1)
    max_lines: int = Field(default=5000, ge=1)


class CenterSettings(BaseModel):
    """Complete Center settings."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig
    limits: LimitsConfig = Field(default_factory=LimitsConfig)


def get_config_path() -> Path:
    configured = os.getenv(CENTER_CONFIG_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> CenterSettings:
    config_path = Path(path).expanduser().resolve() if path else get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Create it from center/config.example.yaml or set {CENTER_CONFIG_ENV_VAR}."
        )
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML object")
    return CenterSettings.model_validate(raw)
