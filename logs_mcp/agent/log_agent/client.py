"""HTTP client used by Log Agent."""

from __future__ import annotations

import httpx

from .config import AgentSettings
from .models import HeartbeatLog, HeartbeatRequest, TaskResultRequest, TasksResponse


class AgentClient:
    """Client for Agent-facing Center APIs."""

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.center.base_url,
            headers={"Authorization": f"Bearer {settings.center.agent_token}"},
            timeout=settings.center.timeout_seconds,
        )

    async def __aenter__(self) -> "AgentClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._client.aclose()

    async def heartbeat(self) -> None:
        payload = HeartbeatRequest(
            server_id=self._settings.server_id,
            hostname=self._settings.hostname,
            ip=self._settings.ip,
            env=self._settings.env,
            logs=[
                HeartbeatLog(name=log.name, path=str(log.path))
                for log in self._settings.allow_logs
            ],
        )
        response = await self._client.post("/api/agent/heartbeat", json=payload.model_dump())
        response.raise_for_status()

    async def fetch_tasks(self) -> TasksResponse:
        response = await self._client.get("/api/agent/tasks", params={"server_id": self._settings.server_id})
        response.raise_for_status()
        return TasksResponse.model_validate(response.json())

    async def upload_result(self, result: TaskResultRequest) -> None:
        response = await self._client.post("/api/agent/task/result", json=result.model_dump())
        response.raise_for_status()
