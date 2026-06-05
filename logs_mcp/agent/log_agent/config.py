"""Configuration for Log Agent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
AGENT_CONFIG_ENV_VAR = "LOG_AGENT_CONFIG"


class CenterApiConfig(BaseModel):
    """Center API settings for Agent."""

    base_url: str = Field(..., min_length=1)
    agent_token: str = Field(..., min_length=1)
    timeout_seconds: float = Field(default=10, gt=0)

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("agent_token")
    @classmethod
    def reject_placeholder_token(cls, value: str) -> str:
        token = value.strip()
        if not token or token == "change-me":
            raise ValueError("center.agent_token must be configured")
        return token


class LogDefinition(BaseModel):
    """Allowed log file definition."""

    name: str
    path: Path

    @field_validator("name")
    @classmethod
    def required_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("log name must not be empty")
        return stripped


class AgentRuntimeConfig(BaseModel):
    """Agent runtime settings."""

    heartbeat_interval_seconds: float = Field(default=10, gt=0)
    task_poll_interval_seconds: float = Field(default=3, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class AgentSettings(BaseModel):
    """Complete Agent settings."""

    server_id: str
    hostname: str | None = None
    ip: str | None = None
    env: str | None = None
    center: CenterApiConfig
    allow_logs: list[LogDefinition] = Field(default_factory=list)
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)

    @field_validator("server_id")
    @classmethod
    def required_server_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("server_id must not be empty")
        return stripped


def get_config_path() -> Path:
    configured = os.getenv(AGENT_CONFIG_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | Path | None = None) -> AgentSettings:
    config_path = Path(path).expanduser().resolve() if path else get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Create it from agent/config.example.yaml or set {AGENT_CONFIG_ENV_VAR}."
        )
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML object")
    return AgentSettings.model_validate(raw)
