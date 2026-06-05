"""Async client for Log Center API."""

from __future__ import annotations

import asyncio
import time

import httpx

from .config import CenterConfig
from .models import CreateTaskRequest, CreateTaskResponse, LogFileInfo, ServerInfo, TaskResult


class CenterClientError(RuntimeError):
    """Base error for Log Center client failures."""


class CenterRequestError(CenterClientError):
    """Raised when Log Center returns an invalid or failed response."""


class TaskPollTimeout(CenterClientError):
    """Raised when a task does not finish before the configured timeout."""


class CenterClient:
    """HTTP client wrapper for the Log Center API."""

    def __init__(self, config: CenterConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "CenterClient":
        if self._client is None:
            self._client = self._new_client()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_servers(self) -> list[ServerInfo]:
        response = await self._request("GET", "/api/log/servers")
        payload = response.json()
        if not isinstance(payload, list):
            raise CenterRequestError("Expected server list response from Log Center")
        return [ServerInfo.model_validate(item) for item in payload]

    async def list_server_logs(self, server_id: str) -> list[LogFileInfo]:
        if not server_id.strip():
            raise ValueError("server_id must not be empty")
        response = await self._request("GET", f"/api/log/server/{server_id.strip()}/files")
        payload = response.json()
        if not isinstance(payload, list):
            raise CenterRequestError("Expected log file list response from Log Center")
        return [LogFileInfo.model_validate(item) for item in payload]

    async def create_task(self, request: CreateTaskRequest) -> CreateTaskResponse:
        response = await self._request("POST", "/api/log/task", json=request.model_dump())
        return CreateTaskResponse.model_validate(response.json())

    async def get_task_result(self, task_id: str) -> TaskResult:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        response = await self._request("GET", f"/api/log/task/{task_id.strip()}")
        return TaskResult.model_validate(response.json())

    async def read_log(self, request: CreateTaskRequest) -> TaskResult:
        task = await self.create_task(request)
        deadline = time.monotonic() + self._config.poll_timeout_seconds

        while True:
            result = await self.get_task_result(task.task_id)
            if result.status in {"finished", "failed"}:
                return result
            if time.monotonic() >= deadline:
                raise TaskPollTimeout(f"Task {task.task_id} did not finish before timeout")
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        client = self._get_client()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._config.api_token}"
        try:
            response = await client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CenterRequestError(
                f"Log Center returned HTTP {exc.response.status_code} for {method} {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CenterRequestError(f"Failed to call Log Center {method} {path}: {exc}") from exc
        return response

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._config.base_url,
            headers={"Authorization": f"Bearer {self._config.api_token}"},
            timeout=self._config.timeout_seconds,
        )
