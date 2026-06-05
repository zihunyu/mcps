from __future__ import annotations

import httpx
import pytest

from log_mcp.client import CenterClient, TaskPollTimeout
from log_mcp.config import CenterConfig
from log_mcp.models import CreateTaskRequest


def make_config() -> CenterConfig:
    return CenterConfig(
        base_url="http://center.local",
        api_token="token",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
        poll_timeout_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_list_servers_sends_bearer_token() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["authorization"] = request.headers["Authorization"]
        assert request.method == "GET"
        assert request.url.path == "/api/log/servers"
        return httpx.Response(200, json=[{"server_id": "prod-app-01", "env": "prod", "status": "online"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://center.local") as http:
        async with CenterClient(make_config(), http) as client:
            servers = await client.list_servers()

    assert seen_headers["authorization"] == "Bearer token"
    assert servers[0].server_id == "prod-app-01"


@pytest.mark.asyncio
async def test_list_server_logs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/log/server/prod-app-01/files"
        return httpx.Response(200, json=[{"log_name": "app-log"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://center.local") as http:
        async with CenterClient(make_config(), http) as client:
            logs = await client.list_server_logs("prod-app-01")

    assert logs[0].log_name == "app-log"


@pytest.mark.asyncio
async def test_read_log_creates_task_and_polls_until_finished() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/log/task":
            assert request.method == "POST"
            payload = request.read().decode()
            assert '"server_id":"prod-app-01"' in payload
            assert '"keyword":"ERROR"' in payload
            return httpx.Response(200, json={"task_id": "task-001"})
        if request.url.path == "/api/log/task/task-001":
            return httpx.Response(200, json={"task_id": "task-001", "status": "finished", "lines": ["ERROR xxx"]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://center.local") as http:
        async with CenterClient(make_config(), http) as client:
            result = await client.read_log(
                CreateTaskRequest(server_id="prod-app-01", log_name="app-error", lines=200, keyword="ERROR")
            )

    assert result.status == "finished"
    assert result.lines == ["ERROR xxx"]
    assert calls == [("POST", "/api/log/task"), ("GET", "/api/log/task/task-001")]


@pytest.mark.asyncio
async def test_read_log_returns_failed_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/log/task":
            return httpx.Response(200, json={"task_id": "task-001"})
        return httpx.Response(200, json={"task_id": "task-001", "status": "failed", "lines": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://center.local") as http:
        async with CenterClient(make_config(), http) as client:
            result = await client.read_log(CreateTaskRequest(server_id="prod-app-01", log_name="app-error", lines=200))

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_read_log_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/log/task":
            return httpx.Response(200, json={"task_id": "task-001"})
        return httpx.Response(200, json={"task_id": "task-001", "status": "running", "lines": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://center.local") as http:
        async with CenterClient(make_config(), http) as client:
            with pytest.raises(TaskPollTimeout):
                await client.read_log(CreateTaskRequest(server_id="prod-app-01", log_name="app-error", lines=200))
