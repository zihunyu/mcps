from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from log_mcp.client import CenterClient
from log_mcp.config import AppConfig, CenterConfig, DownloadConfig, LimitsConfig, McpConfig
from log_mcp.models import CreateTaskRequest, LogFileInfo, ServerInfo, TaskResult
from log_mcp.tools import create_mcp_server
from log_mcp.server import main


class FakeCenterClient:
    last_request: CreateTaskRequest | None = None
    result_status: str = "finished"

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
        return TaskResult(task_id="task-001", status=self.result_status, lines=["ERROR xxx"])


def make_app_config(download_dir: Path | None = None) -> AppConfig:
    return AppConfig(
        center=CenterConfig(base_url="http://center.local", api_token="token"),
        mcp=McpConfig(),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
        download=DownloadConfig(
            dir=download_dir or Path("./downloads"),
            public_base_url="http://mcp.local:8081/",
            token_ttl_seconds=1800,
        ),
    )


async def call_tool(server: Any, name: str, arguments: dict[str, Any] | None = None) -> Any:
    tool_manager = server._tool_manager
    tool = tool_manager._tools[name]
    result = await tool.fn(**(arguments or {}))
    return result


@pytest.mark.asyncio
async def test_list_log_servers_tool() -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(server, "list_log_servers")

    assert result == [{"server_id": "prod-app-01", "env": "prod", "status": "online"}]


@pytest.mark.asyncio
async def test_list_server_logs_tool() -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(server, "list_server_logs", {"server_id": "prod-app-01"})

    assert result == [{"log_name": "prod-app-01-app-log"}]


@pytest.mark.asyncio
async def test_read_log_tool_uses_default_lines_and_keyword() -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "read_log",
        {"server_id": "prod-app-01", "log_name": "app-error", "keyword": "ERROR"},
    )

    assert result == {"task_id": "task-001", "status": "finished", "lines": ["ERROR xxx"]}
    assert fake.last_request is not None
    assert fake.last_request.lines == 200
    assert fake.last_request.keyword == "ERROR"


@pytest.mark.asyncio
async def test_read_log_tool_rejects_too_many_lines() -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "read_log",
        {"server_id": "prod-app-01", "log_name": "app-error", "lines": 5001},
    )

    assert result["error"] == "read_log"
    assert "lines must be <= 5000" in result["detail"]


@pytest.mark.asyncio
async def test_download_log_tool_saves_file_without_returning_lines(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error", "keyword": "ERROR"},
    )

    file_path = Path(result["file_path"])
    assert file_path.exists()
    assert file_path.read_text(encoding="utf-8") == "ERROR xxx\n"
    assert result["task_id"] == "task-001"
    assert result["status"] == "finished"
    assert result["download_url"].startswith("http://mcp.local:8081/downloads/")
    assert result["expires_at"].endswith("Z")
    assert result["line_count"] == 1
    assert result["size_bytes"] > 0
    assert "lines" not in result
    assert fake.last_request is not None
    assert fake.last_request.lines == 200


@pytest.mark.asyncio
async def test_download_log_sanitizes_file_path(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "download_log",
        {"server_id": "../prod/app", "log_name": "..\\app/error"},
    )

    file_path = Path(result["file_path"])
    assert file_path.exists()
    assert file_path.is_relative_to(tmp_path.resolve())
    assert ".." not in file_path.parts


@pytest.mark.asyncio
async def test_download_log_rejects_too_many_lines(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error", "lines": 5001},
    )

    assert result["error"] == "download_log"
    assert "lines must be <= 5000" in result["detail"]
    assert not list(tmp_path.rglob("*.log"))


@pytest.mark.asyncio
async def test_download_log_failed_task_does_not_create_success_file(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    fake.result_status = "failed"
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error"},
    )

    assert result["error"] == "download_log"
    assert "ended with status failed" in result["detail"]
    assert not list(tmp_path.rglob("*.log"))


@pytest.mark.asyncio
async def test_download_url_returns_saved_file(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    result = await call_tool(
        server,
        "download_log",
        {"server_id": "prod-app-01", "log_name": "app-error"},
    )
    token = result["download_url"].rsplit("/", 1)[-1]

    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/downloads/{token}")

    assert response.status_code == 200
    assert response.text == "ERROR xxx\n"
    assert "app-error" in response.headers["content-disposition"]
    assert "lines" not in result


@pytest.mark.asyncio
async def test_download_url_rejects_invalid_token(tmp_path: Path) -> None:
    fake = FakeCenterClient()
    server = create_mcp_server(make_app_config(tmp_path), client_factory=lambda: fake)  # type: ignore[arg-type]

    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/downloads/missing-token")

    assert response.status_code == 404


def test_list_tools_cli_outputs_registered_tools(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_load_config() -> AppConfig:
        raise AssertionError("--list-tools must not require a config file")

    monkeypatch.setattr("log_mcp.server.load_config", fail_load_config)

    main(["--list-tools"])

    output = capsys.readouterr().out
    assert "list_log_servers" in output
    assert "list_server_logs" in output
    assert "read_log" in output
    assert "download_log" in output
