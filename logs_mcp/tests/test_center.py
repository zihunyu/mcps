from __future__ import annotations

import httpx
import pytest

from log_center.app import create_app
from log_center.config import AuthConfig, CenterSettings, LimitsConfig, ServerConfig


def make_settings() -> CenterSettings:
    return CenterSettings(
        server=ServerConfig(),
        auth=AuthConfig(api_token="center-api-token", agent_token="agent-token"),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
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
                "logs": [{"name": "demo-log", "path": "/tmp/app.log"}],
            },
        )
        assert heartbeat.status_code == 200

        servers = await client.get("/api/log/servers", headers=api_headers)
        assert servers.json() == [{"server_id": "local-demo-01", "env": None, "status": "online"}]

        logs = await client.get("/api/log/server/local-demo-01/files", headers=api_headers)
        assert logs.json() == [{"log_name": "demo-log"}]

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
