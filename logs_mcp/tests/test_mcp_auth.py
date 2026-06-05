from __future__ import annotations

import httpx
import pytest

from log_mcp.auth import protect_http_app
from log_mcp.config import AppConfig, AuthConfig, CenterConfig, DownloadConfig, LimitsConfig, McpConfig
from log_mcp.models import CreateTaskRequest, LogFileInfo, ServerInfo, TaskResult
from log_mcp.tools import create_mcp_server


class FakeCenterClient:
    last_request: CreateTaskRequest | None = None

    async def __aenter__(self) -> "FakeCenterClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def list_servers(self) -> list[ServerInfo]:
        return [ServerInfo(server_id="prod-app-01", env="prod", status="online")]

    async def list_server_logs(self, server_id: str) -> list[LogFileInfo]:
        return [LogFileInfo(log_name=f"{server_id}-app-log")]

    async def read_log(self, request: CreateTaskRequest) -> TaskResult:
        self.last_request = request
        return TaskResult(task_id="task-001", status="finished", lines=["ERROR xxx"])


def make_app_config(download_dir) -> AppConfig:
    return AppConfig(
        center=CenterConfig(base_url="http://center.local", api_token="token"),
        mcp=McpConfig(),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
        download=DownloadConfig(
            dir=download_dir,
            public_base_url="http://mcp.local:8081/",
            token_ttl_seconds=1800,
        ),
    )


async def call_tool(server, name, arguments=None):
    tool_manager = server._tool_manager
    tool = tool_manager._tools[name]
    return await tool.fn(**(arguments or {}))


@pytest.mark.asyncio
async def test_mcp_bearer_token_does_not_block_download_route(tmp_path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]
    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error"},
    )
    token = result["download_url"].rsplit("/", 1)[-1]
    app = protect_http_app(server.streamable_http_app(), "secret-token")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        missing = await client.get(f"/downloads/{token}")
        wrong = await client.get(f"/downloads/{token}", headers={"Authorization": "Bearer wrong"})
        ok = await client.get(f"/downloads/{token}", headers={"Authorization": "Bearer secret-token"})

    assert missing.status_code == 200
    assert wrong.status_code == 200
    assert ok.status_code == 200
    assert ok.text == "ERROR xxx\n"


@pytest.mark.asyncio
async def test_mcp_bearer_token_protects_mcp_route(tmp_path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]
    app = protect_http_app(server.streamable_http_app(), "secret-token")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        missing = await client.post("/mcp")
        wrong = await client.post("/mcp", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_mcp_bearer_token_disabled_keeps_http_app_accessible(tmp_path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]
    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error"},
    )
    token = result["download_url"].rsplit("/", 1)[-1]
    app = protect_http_app(server.streamable_http_app(), None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/downloads/{token}")

    assert response.status_code == 200


def test_mcp_bearer_token_config_is_trimmed() -> None:
    config = AuthConfig(bearer_token="  secret-token  ")

    assert config.bearer_token == "secret-token"
