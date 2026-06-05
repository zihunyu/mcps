"""Pydantic models for Log Center API payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TaskStatus = Literal["pending", "running", "finished", "failed"]


class ServerInfo(BaseModel):
    """Server returned by Log Center."""

    model_config = ConfigDict(extra="ignore")

    server_id: str
    env: str | None = None
    status: str | None = None


class LogFileInfo(BaseModel):
    """Registered log file returned by Log Center."""

    model_config = ConfigDict(extra="ignore")

    log_name: str


class CreateTaskRequest(BaseModel):
    """Request body for creating a log query task."""

    server_id: str
    log_name: str
    lines: int = Field(ge=1)
    keyword: str | None = None

    @field_validator("server_id", "log_name")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CreateTaskResponse(BaseModel):
    """Response from task creation."""

    model_config = ConfigDict(extra="ignore")

    task_id: str


class TaskResult(BaseModel):
    """Task result returned by Log Center."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    status: TaskStatus | str
    lines: list[str] = Field(default_factory=list)


class ToolError(BaseModel):
    """Structured error payload returned by MCP tools."""

    error: str
    detail: str


class ReadLogResponse(BaseModel):
    """Payload returned by the read_log MCP tool."""

    task_id: str
    status: str
    lines: list[str]


class DownloadLogResponse(BaseModel):
    """Payload returned by the download_log MCP tool."""

    task_id: str
    status: str
    file_path: str
    download_url: str
    expires_at: str
    line_count: int
    size_bytes: int
