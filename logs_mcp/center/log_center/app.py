"""FastAPI application for Log Center."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .config import CenterSettings
from .models import (
    AgentTasksResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    HeartbeatRequest,
    LogFileInfo,
    ServerInfo,
    TaskResultRequest,
    TaskResultResponse,
)
from .store import InMemoryStore

logger = logging.getLogger(__name__)


def create_app(settings: CenterSettings) -> FastAPI:
    """Create the Log Center API app."""

    app = FastAPI(title="Log Center", version="0.1.0")
    store = InMemoryStore(max_lines=settings.limits.max_lines)

    def require_api_token(authorization: Annotated[str | None, Header()] = None) -> None:
        require_bearer_token(authorization, settings.auth.api_token)

    def require_agent_token(authorization: Annotated[str | None, Header()] = None) -> None:
        require_bearer_token(authorization, settings.auth.agent_token)

    @app.post("/api/agent/heartbeat", dependencies=[Depends(require_agent_token)])
    def heartbeat(request: HeartbeatRequest) -> dict[str, str]:
        store.heartbeat(
            server_id=request.server_id,
            hostname=request.hostname,
            ip=request.ip,
            env=request.env,
            logs=request.logs,
        )
        logger.info("agent_heartbeat server_id=%s log_count=%s", request.server_id, len(request.logs))
        return {"status": "ok"}

    @app.get("/api/agent/tasks", response_model=AgentTasksResponse, dependencies=[Depends(require_agent_token)])
    def fetch_tasks(server_id: str) -> AgentTasksResponse:
        tasks = store.fetch_tasks(server_id)
        logger.info("agent_fetch_tasks server_id=%s task_count=%s", server_id, len(tasks))
        return AgentTasksResponse(tasks=tasks)

    @app.post("/api/agent/task/result", dependencies=[Depends(require_agent_token)])
    def upload_result(request: TaskResultRequest) -> dict[str, str]:
        try:
            store.save_result(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        logger.info("agent_task_result task_id=%s status=%s line_count=%s", request.task_id, request.status, len(request.lines))
        return {"status": "ok"}

    @app.get("/api/log/servers", response_model=list[ServerInfo], dependencies=[Depends(require_api_token)])
    def list_servers() -> list[ServerInfo]:
        return store.list_servers()

    @app.get("/api/log/server/{server_id}/files", response_model=list[LogFileInfo], dependencies=[Depends(require_api_token)])
    def list_logs(server_id: str) -> list[LogFileInfo]:
        return store.list_logs(server_id)

    @app.post("/api/log/task", response_model=CreateTaskResponse, dependencies=[Depends(require_api_token)])
    def create_task(request: CreateTaskRequest) -> CreateTaskResponse:
        if request.lines > settings.limits.max_lines:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"lines must be <= {settings.limits.max_lines}",
            )
        try:
            task = store.create_task(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        logger.info(
            "create_task task_id=%s server_id=%s log_name=%s lines=%s keyword_present=%s",
            task.task_id,
            task.server_id,
            task.log_name,
            task.lines,
            bool(task.keyword),
        )
        return CreateTaskResponse(task_id=task.task_id)

    @app.get("/api/log/task/{task_id}", response_model=TaskResultResponse, dependencies=[Depends(require_api_token)])
    def get_task_result(task_id: str) -> TaskResultResponse:
        try:
            return store.get_task_result(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return app


def require_bearer_token(authorization: str | None, expected_token: str) -> None:
    expected = f"Bearer {expected_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
