from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib import request

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_COMPAT_DIR = ROOT_DIR / "agent_python3.6"
if str(AGENT_COMPAT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_COMPAT_DIR))

from log_agent_compat.config import load_config  # noqa: E402
from log_agent_compat.client import build_heartbeat_log  # noqa: E402
from log_agent_compat.reader import read_tail_lines  # noqa: E402
from log_agent_compat.worker import get_allowed_log_path, run_once  # noqa: E402


def test_agent_compat_loads_yaml_config(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.yaml"
    log_path = tmp_path / "app.log"
    log_path.write_text("INFO ok\n", encoding="utf-8")
    config_path.write_text(
        f"""
server_id: "local-demo-01"
hostname: "local-demo-01"
ip: "127.0.0.1"
env: "dev"
center:
  base_url: "http://127.0.0.1:8000/"
  agent_token: "agent-token"
  timeout_seconds: 2
allow_logs:
  - name: "demo-log"
    path: "{log_path.as_posix()}"
runtime:
  heartbeat_interval_seconds: 1
  task_poll_interval_seconds: 1
  log_level: "INFO"
""",
        encoding="utf-8",
    )

    settings = load_config(config_path)

    assert settings.server_id == "local-demo-01"
    assert settings.center.base_url == "http://127.0.0.1:8000"
    assert settings.allow_logs[0].name == "demo-log"


def test_agent_compat_reads_tail_lines_with_keyword(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "\n".join(["INFO start", "ERROR first", "INFO middle", "ERROR second", "INFO done"]),
        encoding="utf-8",
    )

    result = read_tail_lines(log_file, 4, keyword="ERROR")

    assert result == ["ERROR first", "ERROR second"]


def test_agent_compat_rejects_unregistered_log(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.yaml"
    log_path = tmp_path / "app.log"
    log_path.write_text("INFO ok\n", encoding="utf-8")
    config_path.write_text(
        f"""
server_id: "local-demo-01"
center:
  base_url: "http://127.0.0.1:8000"
  agent_token: "agent-token"
allow_logs:
  - name: "demo-log"
    path: "{log_path.as_posix()}"
""",
        encoding="utf-8",
    )
    settings = load_config(config_path)

    with pytest.raises(PermissionError, match="allow_logs"):
        get_allowed_log_path(settings, "other-log")


def test_agent_compat_heartbeat_log_reports_file_metadata(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text("INFO ok\n", encoding="utf-8")

    result = build_heartbeat_log("demo-log", log_file)

    assert result["name"] == "demo-log"
    assert result["exists"] is True
    assert result["size_bytes"] == log_file.stat().st_size
    assert result["modified_at"].endswith("Z")


def test_agent_compat_once_cycle_with_center(tmp_path: Path, unused_tcp_port: int) -> None:
    center_dir = ROOT_DIR / "center_python3.7"
    if str(center_dir) not in sys.path:
        sys.path.insert(0, str(center_dir))
    from log_center_compat.app import create_app  # noqa: E402
    from log_center_compat.config import AuthConfig, CenterSettings, LimitsConfig, ServerConfig  # noqa: E402

    settings = CenterSettings(
        server=ServerConfig(port=unused_tcp_port),
        auth=AuthConfig(api_token="center-api-token", agent_token="agent-token"),
        limits=LimitsConfig(default_lines=200, max_lines=5000),
    )
    app = create_app(settings)
    server, thread = _run_flask_in_thread(app, unused_tcp_port)
    try:
        log_path = tmp_path / "app.log"
        log_path.write_text("INFO start\nERROR one\n", encoding="utf-8")
        agent_config = tmp_path / "agent.yaml"
        agent_config.write_text(
            f"""
server_id: "local-demo-01"
center:
  base_url: "http://127.0.0.1:{unused_tcp_port}"
  agent_token: "agent-token"
  timeout_seconds: 2
allow_logs:
  - name: "demo-log"
    path: "{log_path.as_posix()}"
runtime:
  heartbeat_interval_seconds: 1
  task_poll_interval_seconds: 1
""",
            encoding="utf-8",
        )
        agent_settings = load_config(agent_config)
        run_once(agent_settings, _NoTaskClient(agent_settings))

        task_id = _center_post(
            unused_tcp_port,
            "/api/log/task",
            {"server_id": "local-demo-01", "log_name": "demo-log", "lines": 20, "keyword": "ERROR"},
            "center-api-token",
        )["task_id"]
        run_once(agent_settings, _NoTaskClient(agent_settings))
        result = _center_get(unused_tcp_port, "/api/log/task/{0}".format(task_id), "center-api-token")

        assert result["status"] == "finished"
        assert result["lines"] == ["ERROR one"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


class _NoTaskClient(object):
    def __init__(self, settings):
        from log_agent_compat.client import AgentClient

        self._client = AgentClient(settings)

    def heartbeat(self):
        return self._client.heartbeat()

    def fetch_tasks(self):
        return self._client.fetch_tasks()

    def upload_result(self, result):
        return self._client.upload_result(result)


def _run_flask_in_thread(app, port):
    import threading
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", port, app)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server, thread


def _center_post(port, path, payload, token):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        "http://127.0.0.1:{0}{1}".format(port, path),
        data=body,
        headers={"Authorization": "Bearer {0}".format(token), "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _center_get(port, path, token):
    req = request.Request(
        "http://127.0.0.1:{0}{1}".format(port, path),
        headers={"Authorization": "Bearer {0}".format(token)},
        method="GET",
    )
    with request.urlopen(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))
