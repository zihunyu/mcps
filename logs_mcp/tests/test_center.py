from __future__ import annotations

import httpx
import anyio
import pytest

from log_center.app import create_app
from log_center.config import AuthConfig, CenterSettings, LimitsConfig, ServerConfig


def make_settings() -> CenterSettings:
    return CenterSettings(
        server=ServerConfig(),
        auth=AuthConfig(api_token="center-api-token", agent_token="agent-token"),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
    )


def make_fast_timeout_settings() -> CenterSettings:
    return CenterSettings(
        server=ServerConfig(),
        auth=AuthConfig(api_token="center-api-token", agent_token="agent-token"),
        limits=LimitsConfig(
            default_lines=200,
            max_lines=5000,
            running_timeout_seconds=0.01,
            server_offline_after_seconds=0.01,
        ),
    )


@pytest.mark.asyncio
async def test_center_registers_agent_and_completes_task() -> None:
    transport = httpx.ASGITransport(app=create_app(make_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        agent_headers = {"Authorization": "Bearer agent-token"}
        api_headers = {"Authorization": "Bearer center-api-token"}

        heartbeat = await client.post(
            "/api/agent/heartbeat",
            headers=agent_headers,
            json={
                "server_id": "local-demo-01",
                "hostname": "local-demo-01",
                "logs": [
                    {
                        "name": "demo-log",
                        "path": "/tmp/app.log",
                        "exists": True,
                        "size_bytes": 123,
                        "modified_at": "2026-06-05T00:00:00Z",
                    }
                ],
            },
        )
        assert heartbeat.status_code == 200

        servers = await client.get("/api/log/servers", headers=api_headers)
        server_payload = servers.json()
        assert server_payload[0]["server_id"] == "local-demo-01"
        assert server_payload[0]["status"] == "online"
        assert server_payload[0]["last_heartbeat"] is not None

        logs = await client.get("/api/log/server/local-demo-01/files", headers=api_headers)
        assert logs.json() == [
            {
                "log_name": "demo-log",
                "exists": True,
                "size_bytes": 123,
                "modified_at": "2026-06-05T00:00:00Z",
            }
        ]

        created = await client.post(
            "/api/log/task",
            headers=api_headers,
            json={"server_id": "local-demo-01", "log_name": "demo-log", "lines": 20, "keyword": "ERROR"},
        )
        assert created.status_code == 200
        task_id = created.json()["task_id"]

        fetched = await client.get("/api/agent/tasks", headers=agent_headers, params={"server_id": "local-demo-01"})
        assert fetched.json() == {
            "tasks": [{"task_id": task_id, "log_name": "demo-log", "keyword": "ERROR", "lines": 20}]
        }

        uploaded = await client.post(
            "/api/agent/task/result",
            headers=agent_headers,
            json={"task_id": task_id, "status": "finished", "lines": ["ERROR one"]},
        )
        assert uploaded.status_code == 200

        result = await client.get(f"/api/log/task/{task_id}", headers=api_headers)
        assert result.json() == {"task_id": task_id, "status": "finished", "lines": ["ERROR one"], "error": None}


@pytest.mark.asyncio
async def test_center_rejects_invalid_token() -> None:
    transport = httpx.ASGITransport(app=create_app(make_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/log/servers", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_center_marks_server_offline_after_heartbeat_timeout() -> None:
    transport = httpx.ASGITransport(app=create_app(make_fast_timeout_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        agent_headers = {"Authorization": "Bearer agent-token"}
        api_headers = {"Authorization": "Bearer center-api-token"}
        await client.post(
            "/api/agent/heartbeat",
            headers=agent_headers,
            json={"server_id": "local-demo-01", "logs": [{"name": "demo-log", "path": "/tmp/app.log"}]},
        )
        await anyio.sleep(0.02)

        servers = await client.get("/api/log/servers", headers=api_headers)

    assert servers.json()[0]["status"] == "offline"


@pytest.mark.asyncio
async def test_center_recovers_running_task_after_timeout() -> None:
    transport = httpx.ASGITransport(app=create_app(make_fast_timeout_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        agent_headers = {"Authorization": "Bearer agent-token"}
        api_headers = {"Authorization": "Bearer center-api-token"}
        await client.post(
            "/api/agent/heartbeat",
            headers=agent_headers,
            json={"server_id": "local-demo-01", "logs": [{"name": "demo-log", "path": "/tmp/app.log"}]},
        )
        created = await client.post(
            "/api/log/task",
            headers=api_headers,
            json={"server_id": "local-demo-01", "log_name": "demo-log", "lines": 20},
        )
        task_id = created.json()["task_id"]
        first_fetch = await client.get("/api/agent/tasks", headers=agent_headers, params={"server_id": "local-demo-01"})
        await anyio.sleep(0.02)
        result_after_timeout = await client.get(f"/api/log/task/{task_id}", headers=api_headers)
        second_fetch = await client.get("/api/agent/tasks", headers=agent_headers, params={"server_id": "local-demo-01"})

    assert first_fetch.json()["tasks"][0]["task_id"] == task_id
    assert result_after_timeout.json()["status"] == "pending"
    assert second_fetch.json()["tasks"][0]["task_id"] == task_id
