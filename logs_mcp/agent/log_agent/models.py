"""Pydantic models for Agent API payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "finished", "failed"]


class HeartbeatLog(BaseModel):
    """Log file reported in heartbeat."""

    name: str
    path: str


class HeartbeatRequest(BaseModel):
    """Agent heartbeat payload."""

    server_id: str
    hostname: str | None = None
    ip: str | None = None
    env: str | None = None
    logs: list[HeartbeatLog]


class AgentTask(BaseModel):
    """Task fetched from Center."""

    task_id: str
    log_name: str
    keyword: str | None = None
    lines: int


class TasksResponse(BaseModel):
    """Tasks fetched from Center."""

    tasks: list[AgentTask] = Field(default_factory=list)


class TaskResultRequest(BaseModel):
    """Task result uploaded to Center."""

    task_id: str
    status: TaskStatus
    lines: list[str] = Field(default_factory=list)
    error: str | None = None
