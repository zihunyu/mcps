"""Pydantic models for Log Center."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TaskStatus = Literal["pending", "running", "finished", "failed"]


class AgentLogFile(BaseModel):
    """Log file reported by an Agent."""

    name: str
    path: str

    @field_validator("name", "path")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped


class HeartbeatRequest(BaseModel):
    """Agent heartbeat payload."""

    server_id: str
    hostname: str | None = None
    ip: str | None = None
    env: str | None = None
    logs: list[AgentLogFile] = Field(default_factory=list)

    @field_validator("server_id")
    @classmethod
    def required_server_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("server_id must not be empty")
        return stripped


class ServerRecord(BaseModel):
    """Registered server record."""

    server_id: str
    hostname: str | None = None
    ip: str | None = None
    env: str | None = None
    status: str = "online"
    last_heartbeat: datetime
    created_at: datetime


class LogFileRecord(BaseModel):
    """Registered log file record."""

    server_id: str
    log_name: str
    log_path: str
    enabled: bool = True
    created_at: datetime


class ServerInfo(BaseModel):
    """Public server response."""

    server_id: str
    env: str | None = None
    status: str


class LogFileInfo(BaseModel):
    """Public log file response."""

    log_name: str


class CreateTaskRequest(BaseModel):
    """Create task request from MCP."""

    server_id: str
    log_name: str
    lines: int = Field(default=200, ge=1)
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
    """Create task response."""

    task_id: str


class AgentTask(BaseModel):
    """Task payload fetched by Agent."""

    task_id: str
    log_name: str
    keyword: str | None = None
    lines: int


class AgentTasksResponse(BaseModel):
    """Agent task list response."""

    tasks: list[AgentTask]


class TaskResultRequest(BaseModel):
    """Agent task result upload."""

    task_id: str
    lines: list[str] = Field(default_factory=list)
    status: TaskStatus = "finished"
    error: str | None = None


class TaskRecord(BaseModel):
    """Stored task record."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    server_id: str
    log_name: str
    keyword: str | None = None
    lines: int
    status: TaskStatus = "pending"
    result_lines: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskResultResponse(BaseModel):
    """Task result returned to MCP."""

    task_id: str
    status: TaskStatus
    lines: list[str] = Field(default_factory=list)
    error: str | None = None
