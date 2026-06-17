from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from prometheus_mcp.client import PrometheusClient, load_config_from_env, parse_duration_seconds, summarize_alerts
from prometheus_mcp.templates import render_query_template


def make_client(handler) -> PrometheusClient:
    transport = httpx.MockTransport(handler)

    class TestClient(PrometheusClient):
        async def _get(self, path, params=None):
            request_params = {key: value for key, value in (params or {}).items() if value is not None}
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=transport,
            ) as client:
                response = await client.get(path, params=request_params)
                return response.json()

    return TestClient(base_url="http://prometheus.test")


def json_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.mark.asyncio
async def test_query_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        params = parse_qs(request.url.query.decode())
        assert params["query"] == ["up"]
        return json_response({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]}})

    result = await make_client(handler).query("up")

    assert result["status"] == "success"
    assert result["query"] == "up"
    assert result["summary"]["result_count"] == 1


@pytest.mark.asyncio
async def test_query_validation_error() -> None:
    result = await make_client(lambda request: json_response({})).query("   ")

    assert result["status"] == "error"
    assert result["errorType"] == "validation"


@pytest.mark.asyncio
async def test_query_range_rejects_too_many_points() -> None:
    client = make_client(lambda request: json_response({}))
    client.max_range_points = 10

    result = await client.query_range("up", "2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z", "1s")

    assert result["status"] == "error"
    assert result["errorType"] == "validation"
    assert "points" in result["error"]


@pytest.mark.asyncio
async def test_query_range_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        params = parse_qs(request.url.query.decode())
        assert params["step"] == ["60s"]
        return json_response({"status": "success", "data": {"resultType": "matrix", "result": []}})

    result = await make_client(handler).query_range("up", "2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z", "60s")

    assert result["status"] == "success"
    assert result["summary"]["estimated_points_per_series"] == 61


@pytest.mark.asyncio
async def test_list_metrics_filters_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"status": "success", "data": ["node_cpu_seconds_total", "mysql_up", "node_memory_MemTotal_bytes"]})

    result = await make_client(handler).list_metrics(prefix="node_", limit=1, offset=1)

    assert result["status"] == "success"
    assert result["data"] == ["node_memory_MemTotal_bytes"]
    assert result["summary"]["total"] == 2
    assert result["summary"]["has_more"] is False


@pytest.mark.asyncio
async def test_metadata_summary_counts_types() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "status": "success",
                "data": {
                    "up": [{"type": "gauge", "unit": "", "help": "up"}],
                    "http_requests_total": [{"type": "counter", "unit": "", "help": "requests"}],
                },
            }
        )

    result = await make_client(handler).metric_metadata()

    assert result["status"] == "success"
    assert result["summary"]["types"] == {"gauge": 1, "counter": 1}
    assert result["summary"]["metric_count"] == 2


@pytest.mark.asyncio
async def test_targets_summary_detects_unhealthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "status": "success",
                "data": {
                    "activeTargets": [
                        {"labels": {"job": "prometheus", "instance": "localhost:9090"}, "health": "up"},
                        {"labels": {"job": "node", "instance": "node1"}, "health": "down", "lastError": "timeout"},
                    ],
                    "droppedTargets": [{}],
                },
            }
        )

    result = await make_client(handler).targets()

    assert result["summary"]["active_count"] == 2
    assert result["summary"]["dropped_count"] == 1
    assert result["summary"]["health"] == {"up": 1, "down": 1}
    assert result["summary"]["unhealthy_targets"][0]["instance"] == "node1"


@pytest.mark.asyncio
async def test_alert_filter_and_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "status": "success",
                "data": {
                    "alerts": [
                        {"state": "firing", "labels": {"alertname": "Disk", "severity": "critical", "instance": "n1"}},
                        {"state": "pending", "labels": {"alertname": "CPU", "severity": "warning", "instance": "n2"}},
                    ]
                },
            }
        )

    result = await make_client(handler).alerts(state="firing")

    assert result["summary"]["firing_count"] == 1
    assert result["summary"]["severities"] == {"critical": 1}
    assert result["data"]["alerts"][0]["labels"]["alertname"] == "Disk"


@pytest.mark.asyncio
async def test_rules_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "status": "success",
                "data": {
                    "groups": [
                        {
                            "name": "node_disk_alerts",
                            "file": "/rules.yml",
                            "rules": [
                                {"name": "Disk", "type": "alerting", "state": "firing"},
                                {"name": "CPU", "type": "alerting", "state": "inactive"},
                            ],
                        }
                    ]
                },
            }
        )

    result = await make_client(handler).rules(rule_name="Disk")

    assert result["summary"]["group_count"] == 1
    assert result["summary"]["rule_count"] == 1
    assert result["summary"]["states"] == {"firing": 1}


@pytest.mark.asyncio
async def test_inventory_maps_server_and_exporter_job() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/label/job/values":
            return json_response({"status": "success", "data": ["node_exporter", "mysqld_exporter"]})
        if path == "/api/v1/label/instance/values":
            return json_response({"status": "success", "data": ["app-server-1", "db-server-1"]})
        if path == "/api/v1/label/service/values":
            return json_response({"status": "success", "data": ["sample-service"]})
        if path == "/api/v1/label/mountpoint/values":
            return json_response({"status": "success", "data": ["/", "/data"]})
        if path == "/api/v1/query":
            return json_response(
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {"metric": {"instance": "app-server-1", "job": "node_exporter"}, "value": [1, "10"]},
                            {"metric": {"instance": "db-server-1", "job": "mysqld_exporter"}, "value": [1, "20"]},
                        ],
                    },
                }
            )
        raise AssertionError(path)

    result = await make_client(handler).inventory()

    assert result["data"]["servers"] == ["app-server-1", "db-server-1"]
    assert set(result["data"]["exporter_jobs"]) == {"node_exporter", "mysqld_exporter"}
    assert result["data"]["server_exporter_pairs"][0]["server"] == "app-server-1"
    assert result["data"]["server_exporter_pairs"][0]["exporter_job"] == "node_exporter"


@pytest.mark.asyncio
async def test_template_run_executes_rendered_query() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = parse_qs(request.url.query.decode())["query"][0]
        return json_response({"status": "success", "data": {"resultType": "vector", "result": []}})

    result = await make_client(handler).run_query_template("mysql_connections", server="db-server-1")

    assert result["status"] == "success"
    assert result["summary"]["template_id"] == "mysql_connections"
    assert 'job="mysqld_exporter"' in captured["query"]
    assert 'instance="db-server-1"' in captured["query"]


@pytest.mark.asyncio
async def test_template_run_requires_range_arguments_when_any_range_argument_is_present() -> None:
    result = await make_client(lambda request: json_response({})).run_query_template(
        "linux_cpu_usage_percent",
        server="app-server-1",
        start="2026-06-01T00:00:00Z",
    )

    assert result["status"] == "error"
    assert result["errorType"] == "validation"


def test_parse_duration_seconds() -> None:
    assert parse_duration_seconds("15s") == 15
    assert parse_duration_seconds("2m") == 120
    assert parse_duration_seconds("1h") == 3600


def test_load_config_from_env_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PROMETHEUS_BASE_URL=http://from-file:9090",
                "PROMETHEUS_TIMEOUT_SECONDS=7",
                "PROMETHEUS_MAX_RANGE_SECONDS=60",
                "PROMETHEUS_MAX_RANGE_POINTS=99",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PROMETHEUS_BASE_URL", raising=False)
    monkeypatch.setenv("PROMETHEUS_TIMEOUT_SECONDS", "11")

    config = load_config_from_env(env_file)

    assert config["base_url"] == "http://from-file:9090"
    assert config["timeout_seconds"] == 11
    assert config["max_range_seconds"] == 60
    assert config["max_range_points"] == 99


def test_summarize_alerts_firing_context() -> None:
    summary = summarize_alerts(
        [
            {
                "state": "firing",
                "labels": {"alertname": "NodeDiskUsageCritical", "severity": "critical", "instance": "linux-server-1", "mountpoint": "/data1"},
                "value": "0.95",
            }
        ]
    )

    assert summary["firing_count"] == 1
    assert summary["firing_alerts"][0]["mountpoint"] == "/data1"


def test_template_render_maps_server_to_instance_and_job_to_exporter() -> None:
    result = render_query_template("linux_memory_overview", server="app-server-1")

    query = result["data"]["rendered_query"]
    assert 'instance="app-server-1"' in query
    assert 'job="node_exporter"' in query


def test_template_render_escapes_label_values() -> None:
    result = render_query_template(
        "linux_filesystem_usage",
        server='hk3"1',
        variables={"mountpoint": '/data\\x"'},
    )

    query = result["data"]["rendered_query"]
    assert 'instance="hk3\\"1"' in query
    assert 'mountpoint="/data\\\\x\\""' in query


def test_template_render_required_variable_error() -> None:
    result = render_query_template("jvm_heap_usage_by_service", server="app-server-1")

    assert result["status"] == "error"
    assert result["errorType"] == "validation"
    assert "service" in result["error"]


@pytest.mark.asyncio
async def test_context_returns_known_fast_path() -> None:
    result = await make_client(lambda request: json_response({})).context(server="db-server-1", topic="mysql")

    assert result["status"] == "success"
    assert result["summary"]["server"] == "db-server-1"
    assert "mysql_connections" in result["summary"]["fast_templates"]
    assert result["data"]["relevant_server_exporter_pairs"]
    assert "live_servers" in result["data"]


@pytest.mark.asyncio
async def test_analysis_history_filters_known_note(monkeypatch) -> None:
    monkeypatch.setattr(
        "prometheus_mcp.client.read_knowledge",
        lambda: {
            "analysis_history": [
                {
                    "server": "db-server-1",
                    "category": "mysql",
                    "question": "sample mysql question",
                    "summary": "sample mysql summary",
                }
            ]
        },
    )
    result = await make_client(lambda request: json_response({})).analysis_history(server="db-server-1", category="mysql")

    assert result["status"] == "success"
    assert result["data"]
    assert result["data"][0]["server"] == "db-server-1"
