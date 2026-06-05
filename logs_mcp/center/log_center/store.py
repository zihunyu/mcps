"""In-memory storage for Log Center V1."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from .models import (
    AgentLogFile,
    AgentTask,
    CreateTaskRequest,
    LogFileInfo,
    LogFileRecord,
    ServerInfo,
    ServerRecord,
    TaskRecord,
    TaskResultRequest,
    TaskResultResponse,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore:
    """Thread-safe in-memory Center store."""

    def __init__(self, max_lines: int) -> None:
        self._max_lines = max_lines
        self._servers: dict[str, ServerRecord] = {}
        self._logs: dict[tuple[str, str], LogFileRecord] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = RLock()

    def heartbeat(
        self,
        server_id: str,
        hostname: str | None,
        ip: str | None,
        env: str | None,
        logs: list[AgentLogFile],
    ) -> None:
        with self._lock:
            now = utc_now()
            current = self._servers.get(server_id)
            created_at = current.created_at if current else now
            self._servers[server_id] = ServerRecord(
                server_id=server_id,
                hostname=hostname,
                ip=ip,
                env=env,
                status="online",
                last_heartbeat=now,
                created_at=created_at,
            )
            for log in logs:
                key = (server_id, log.name)
                current_log = self._logs.get(key)
                self._logs[key] = LogFileRecord(
                    server_id=server_id,
                    log_name=log.name,
                    log_path=log.path,
                    enabled=True,
                    created_at=current_log.created_at if current_log else now,
                )

    def list_servers(self) -> list[ServerInfo]:
        with self._lock:
            return [
                ServerInfo(server_id=server.server_id, env=server.env, status=server.status)
                for server in sorted(self._servers.values(), key=lambda item: item.server_id)
            ]

    def list_logs(self, server_id: str) -> list[LogFileInfo]:
        with self._lock:
            return [
                LogFileInfo(log_name=log.log_name)
                for log in sorted(self._logs.values(), key=lambda item: item.log_name)
                if log.server_id == server_id and log.enabled
            ]

    def create_task(self, request: CreateTaskRequest) -> TaskRecord:
        with self._lock:
            if request.server_id not in self._servers:
                raise KeyError(f"server not registered: {request.server_id}")
            if (request.server_id, request.log_name) not in self._logs:
                raise KeyError(f"log not registered: {request.server_id}/{request.log_name}")
            now = utc_now()
            task_id = uuid4().hex
            task = TaskRecord(
                task_id=task_id,
                server_id=request.server_id,
                log_name=request.log_name,
                keyword=request.keyword,
                lines=min(request.lines, self._max_lines),
                status="pending",
                created_at=now,
                updated_at=now,
            )
            self._tasks[task_id] = task
            return task

    def fetch_tasks(self, server_id: str) -> list[AgentTask]:
        with self._lock:
            now = utc_now()
            fetched: list[AgentTask] = []
            for task in self._tasks.values():
                if task.server_id != server_id or task.status != "pending":
                    continue
                task.status = "running"
                task.updated_at = now
                fetched.append(
                    AgentTask(
                        task_id=task.task_id,
                        log_name=task.log_name,
                        keyword=task.keyword,
                        lines=task.lines,
                    )
                )
            return fetched

    def save_result(self, result: TaskResultRequest) -> None:
        with self._lock:
            task = self._tasks.get(result.task_id)
            if task is None:
                raise KeyError(f"task not found: {result.task_id}")
            task.status = result.status
            task.result_lines = result.lines
            task.error = result.error
            task.updated_at = utc_now()

    def get_task_result(self, task_id: str) -> TaskResultResponse:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            return TaskResultResponse(
                task_id=task.task_id,
                status=task.status,
                lines=task.result_lines,
                error=task.error,
            )
