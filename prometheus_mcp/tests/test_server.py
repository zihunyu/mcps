from __future__ import annotations

import pytest

from prometheus_mcp.client import PrometheusClient
from prometheus_mcp.server import StaticBearerTokenVerifier, build_arg_parser, build_server, load_mcp_config


class StubClient(PrometheusClient):
    async def query(self, query, time=None, timeout=None):
        return {"status": "success", "query": query, "data": {"resultType": "vector", "result": []}}

    async def query_range(self, query, start, end, step):
        return {"status": "success", "query": query, "data": {"resultType": "matrix", "result": []}}

    async def list_metrics(self, prefix=None, limit=500, offset=0):
        metrics = ["up", "node_cpu_seconds_total", "mysql_global_status_threads_connected"]
        if prefix:
            metrics = [metric for metric in metrics if metric.startswith(prefix)]
        return {"status": "success", "data": metrics, "summary": {"total": len(metrics)}}

    async def metric_metadata(self, metric=None, limit=500, offset=0):
        return {"status": "success", "data": {}, "summary": {"metric": metric}}

    async def label_values(self, label, match=None, limit=1000):
        return {"status": "success", "data": ["prometheus"], "summary": {"label": label}}

    async def targets(self, state="any"):
        return {"status": "success", "data": {"activeTargets": []}, "summary": {"active_count": 0}}

    async def alerts(self, state=None):
        return {"status": "success", "data": {"alerts": []}, "summary": {"firing_count": 0}}

    async def rules(self, rule_type=None, rule_name=None, group_name=None):
        return {"status": "success", "data": {"groups": []}, "summary": {"rule_count": 0}}

    async def status(self, include_config=False):
        return {"status": "success", "data": {}, "summary": {"prometheus_version": "test"}}

    async def metrics_by_prefix_resource(self, prefix=None):
        return {"status": "success", "data": {"prefix": prefix}}

    async def health_summary(self):
        return {"status": "success", "summary": {"risk_notes": []}}

    async def inventory(self):
        return {"status": "success", "data": {"servers": ["linux-server-1"], "exporter_jobs": ["node_exporter"]}}

    async def monitoring_coverage(self):
        return {"status": "success", "summary": {"server_count": 1}, "data": {"server_coverage": []}}

    async def refresh_context(self):
        return {"status": "success", "summary": {"server_count": 1}}

    async def server_health(self, server, remember=True):
        return {"status": "success", "summary": {"server": server, "risk": "ok"}}

    async def mysql_health(self, server, remember=True):
        return {"status": "success", "summary": {"server": server, "risk": "ok"}}

    async def jvm_health(self, server, service=None, remember=True):
        return {"status": "success", "summary": {"server": server, "service": service, "risk": "ok"}}

    async def disk_health(self, server, mountpoint=None, remember=True):
        return {"status": "success", "summary": {"server": server, "mountpoint": mountpoint, "risk": "ok"}}

    async def context(self, server=None, topic=None):
        return {"status": "success", "summary": {"server": server, "topic": topic}}

    async def analysis_history(self, server=None, category=None, limit=20):
        return {"status": "success", "data": [], "summary": {"returned": 0}}

    async def remember_analysis(self, question, summary, server=None, category=None, tags=None, details=None):
        return {"status": "success", "data": {"question": question, "summary": summary}}

    async def list_query_templates(self, category=None, exporter_job=None):
        return {"status": "success", "data": [], "summary": {"total": 0}}

    async def get_query_template(self, template_id):
        return {"status": "success", "data": {"id": template_id}}

    async def render_query_template(self, template_id, server=None, variables=None):
        return {"status": "success", "data": {"id": template_id, "rendered_query": "up"}}

    async def run_query_template(self, template_id, server=None, variables=None, start=None, end=None, step=None):
        return {"status": "success", "summary": {"template_id": template_id}}


@pytest.mark.asyncio
async def test_server_registers_expected_tools() -> None:
    server = build_server(StubClient())
    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert {
        "prometheus_query",
        "prometheus_query_range",
        "prometheus_list_metrics",
        "prometheus_metric_metadata",
        "prometheus_label_values",
        "prometheus_targets",
        "prometheus_alerts",
        "prometheus_rules",
        "prometheus_status",
        "prometheus_health_summary",
        "prometheus_inventory",
        "prometheus_monitoring_coverage",
        "prometheus_refresh_context",
        "prometheus_server_health",
        "prometheus_mysql_health",
        "prometheus_jvm_health",
        "prometheus_disk_health",
        "prometheus_context",
        "prometheus_analysis_history",
        "prometheus_remember_analysis",
        "prometheus_list_query_templates",
        "prometheus_get_query_template",
        "prometheus_render_query_template",
        "prometheus_run_query_template",
    }.issubset(names)


@pytest.mark.asyncio
async def test_static_bearer_token_verifier() -> None:
    verifier = StaticBearerTokenVerifier("secret-token")

    assert await verifier.verify_token("wrong-token") is None
    access_token = await verifier.verify_token("secret-token")
    assert access_token is not None
    assert access_token.client_id == "prometheus-mcp-client"


@pytest.mark.asyncio
async def test_server_registers_expected_resources_and_templates() -> None:
    server = build_server(StubClient())
    resources = await server.list_resources()
    resource_uris = {str(resource.uri) for resource in resources}
    templates = await server.list_resource_templates()
    template_uris = {template.uriTemplate for template in templates}

    assert "prometheus://metrics/all" in resource_uris
    assert "prometheus://metrics/by-prefix" in resource_uris
    assert "prometheus://targets" in resource_uris
    assert "prometheus://alerts" in resource_uris
    assert "prometheus://rules" in resource_uris
    assert "prometheus://status" in resource_uris
    assert "prometheus://inventory" in resource_uris
    assert "prometheus://monitoring-coverage" in resource_uris
    assert "prometheus://context" in resource_uris
    assert "prometheus://analysis-history" in resource_uris
    assert "prometheus://templates" in resource_uris
    assert "prometheus://metrics/by-prefix/{prefix}" in template_uris
    assert "prometheus://context/{server}" in template_uris
    assert "prometheus://templates/{template_id}" in template_uris


def test_load_mcp_config_defaults_from_env_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MCP_TRANSPORT=stdio",
                "MCP_HOST=127.0.0.1",
                "MCP_PORT=8000",
                "MCP_PATH=/mcp",
                "MCP_AUTH_TOKEN=test-token",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("prometheus_mcp.server.DEFAULT_ENV_FILE", env_file)
    for key in ["MCP_TRANSPORT", "MCP_HOST", "MCP_PORT", "MCP_PATH", "MCP_AUTH_TOKEN"]:
        monkeypatch.delenv(key, raising=False)

    config = load_mcp_config()

    assert config["transport"] == "stdio"
    assert config["host"] == "127.0.0.1"
    assert config["port"] == 8000
    assert config["path"] == "/mcp"
    assert config["auth_token"] == "test-token"


def test_cli_args_override_mcp_config(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_AUTH_TOKEN=test-token\n", encoding="utf-8")
    monkeypatch.setattr("prometheus_mcp.server.DEFAULT_ENV_FILE", env_file)
    args = build_arg_parser().parse_args(
        ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000", "--path", "api/mcp"]
    )

    config = load_mcp_config(args)

    assert config == {
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 9000,
        "path": "/api/mcp",
        "auth_token": "test-token",
        "auth_issuer_url": None,
        "auth_resource_url": None,
    }


def test_build_server_enables_auth_when_token_is_set() -> None:
    server = build_server(StubClient(), auth_token="secret-token")

    assert server.settings.auth is not None
