from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CENTER_COMPAT_DIR = ROOT_DIR / "center_python3.7"
if str(CENTER_COMPAT_DIR) not in sys.path:
    sys.path.insert(0, str(CENTER_COMPAT_DIR))

from log_center_compat.app import create_app  # noqa: E402
from log_center_compat.config import AuthConfig, CenterSettings, LimitsConfig, ServerConfig, load_config  # noqa: E402


def make_settings() -> CenterSettings:
    return CenterSettings(
        server=ServerConfig(),
        auth=AuthConfig(api_token="center-api-token", agent_token="agent-token"),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
    )


def test_center_compat_registers_agent_and_completes_task() -> None:
    app = create_app(make_settings())
    client = app.test_client()
    agent_headers = {"Authorization": "Bearer agent-token"}
    api_headers = {"Authorization": "Bearer center-api-token"}

    heartbeat = client.post(
        "/api/agent/heartbeat",
        headers=agent_headers,
        json={
            "server_id": "local-demo-01",
            "hostname": "local-demo-01",
            "logs": [{"name": "demo-log", "path": "/tmp/app.log"}],
        },
    )
    assert heartbeat.status_code == 200

    servers = client.get("/api/log/servers", headers=api_headers)
    assert servers.json == [{"server_id": "local-demo-01", "env": None, "status": "online"}]

    logs = client.get("/api/log/server/local-demo-01/files", headers=api_headers)
    assert logs.json == [{"log_name": "demo-log"}]

    created = client.post(
        "/api/log/task",
        headers=api_headers,
        json={"server_id": "local-demo-01", "log_name": "demo-log", "lines": 20, "keyword": "ERROR"},
    )
    assert created.status_code == 200
    task_id = created.json["task_id"]

    fetched = client.get("/api/agent/tasks", headers=agent_headers, query_string={"server_id": "local-demo-01"})
    assert fetched.json == {
        "tasks": [{"task_id": task_id, "log_name": "demo-log", "keyword": "ERROR", "lines": 20}]
    }

    uploaded = client.post(
        "/api/agent/task/result",
        headers=agent_headers,
        json={"task_id": task_id, "status": "finished", "lines": ["ERROR one"]},
    )
    assert uploaded.status_code == 200

    result = client.get("/api/log/task/{0}".format(task_id), headers=api_headers)
    assert result.json == {"task_id": task_id, "status": "finished", "lines": ["ERROR one"], "error": None}


def test_center_compat_rejects_invalid_token() -> None:
    client = create_app(make_settings()).test_client()

    response = client.get("/api/log/servers", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


def test_center_compat_rejects_too_many_lines() -> None:
    client = create_app(make_settings()).test_client()
    agent_headers = {"Authorization": "Bearer agent-token"}
    api_headers = {"Authorization": "Bearer center-api-token"}
    client.post(
        "/api/agent/heartbeat",
        headers=agent_headers,
        json={"server_id": "local-demo-01", "logs": [{"name": "demo-log", "path": "/tmp/app.log"}]},
    )

    response = client.post(
        "/api/log/task",
        headers=api_headers,
        json={"server_id": "local-demo-01", "log_name": "demo-log", "lines": 5001},
    )

    assert response.status_code == 400
    assert "lines must be <= 5000" in response.json["detail"]


def test_center_compat_loads_yaml_config(tmp_path: Path) -> None:
    config_path = tmp_path / "center.yaml"
    config_path.write_text(
        """
server:
  host: "0.0.0.0"
  port: 8010
  log_level: "info"
auth:
  api_token: "center-api-token"
  agent_token: "agent-token"
limits:
  default_lines: 100
  max_lines: 1000
""",
        encoding="utf-8",
    )

    settings = load_config(config_path)

    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 8010
    assert settings.auth.api_token == "center-api-token"
    assert settings.limits.max_lines == 1000
