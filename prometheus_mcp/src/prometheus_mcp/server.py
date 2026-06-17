from __future__ import annotations

import argparse
import asyncio
from hmac import compare_digest
import json
import os
import sys
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from .client import DEFAULT_ENV_FILE, PrometheusClient, config_value, parse_env_file

VALID_TRANSPORTS = {"stdio", "streamable-http"}
DEFAULT_MCP_TRANSPORT = "stdio"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8000
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_MCP_AUTH_TOKEN = ""
DEFAULT_MCP_AUTH_CLIENT_ID = "prometheus-mcp-client"


class StaticBearerTokenVerifier:
    def __init__(self, token: str, client_id: str = DEFAULT_MCP_AUTH_CLIENT_ID) -> None:
        token = token.strip()
        if not token:
            raise ValueError("MCP auth token must not be empty")
        self._token = token
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        if not compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=[],
            claims={"auth_type": "static_bearer"},
        )


def load_mcp_config(args: argparse.Namespace | None = None) -> dict[str, Any]:
    file_values = parse_env_file(DEFAULT_ENV_FILE)
    transport = config_value("MCP_TRANSPORT", DEFAULT_MCP_TRANSPORT, file_values)
    host = config_value("MCP_HOST", DEFAULT_MCP_HOST, file_values)
    port = int(config_value("MCP_PORT", str(DEFAULT_MCP_PORT), file_values))
    path = config_value("MCP_PATH", DEFAULT_MCP_PATH, file_values)
    auth_token = config_value("MCP_AUTH_TOKEN", DEFAULT_MCP_AUTH_TOKEN, file_values).strip()
    auth_issuer_url = config_value("MCP_AUTH_ISSUER_URL", "", file_values).strip()
    auth_resource_url = config_value("MCP_AUTH_RESOURCE_URL", "", file_values).strip()

    if args is not None:
        transport = args.transport or transport
        host = args.host or host
        port = args.port or port
        path = args.path or path

    transport = transport.strip()
    path = path if path.startswith("/") else f"/{path}"
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"unsupported MCP transport: {transport}; expected one of {sorted(VALID_TRANSPORTS)}")

    return {
        "transport": transport,
        "host": host,
        "port": port,
        "path": path,
        "auth_token": auth_token,
        "auth_issuer_url": auth_issuer_url or None,
        "auth_resource_url": auth_resource_url or None,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 Prometheus MCP Server")
    parser.add_argument(
        "--transport",
        choices=sorted(VALID_TRANSPORTS),
        default=None,
        help="MCP 启动模式：stdio 或 streamable-http。默认读取 .env 中的 MCP_TRANSPORT。",
    )
    parser.add_argument("--host", default=None, help="streamable-http 监听地址，默认读取 MCP_HOST。")
    parser.add_argument("--port", type=int, default=None, help="streamable-http 监听端口，默认读取 MCP_PORT。")
    parser.add_argument("--path", default=None, help="streamable-http MCP 路径，默认读取 MCP_PATH。")
    return parser


def configure_windows_http_event_loop(transport: str) -> None:
    if transport != "streamable-http" or sys.platform != "win32":
        return

    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is not None:
        asyncio.set_event_loop_policy(policy_factory())


def as_resource_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def advertised_http_base_url(host: str, port: int) -> str:
    advertised_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    if ":" in advertised_host and not advertised_host.startswith("["):
        advertised_host = f"[{advertised_host}]"
    return f"http://{advertised_host}:{port}"


def build_auth_settings(
    host: str,
    port: int,
    path: str,
    issuer_url: str | None = None,
    resource_url: str | None = None,
) -> AuthSettings:
    base_url = advertised_http_base_url(host, port)
    return AuthSettings(
        issuer_url=issuer_url or base_url,
        resource_server_url=resource_url or f"{base_url}{path}",
        required_scopes=[],
    )


def build_server(
    client: PrometheusClient | None = None,
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    path: str = DEFAULT_MCP_PATH,
    auth_token: str | None = None,
    auth_issuer_url: str | None = None,
    auth_resource_url: str | None = None,
) -> FastMCP:
    prometheus = client or PrometheusClient.from_env()
    server_kwargs: dict[str, Any] = {
        "name": "prometheus-mcp",
        "instructions": (
            "Read-only Prometheus MCP server. Use these tools to query PromQL, "
            "discover metrics, inspect metadata, targets, alerts, rules, and "
            "summarize operational health. In this server, the user-facing "
            "server parameter maps to Prometheus label instance, and exporter_job "
            "maps to Prometheus label job."
        ),
        "host": host,
        "port": port,
        "streamable_http_path": path,
    }
    if auth_token:
        server_kwargs["auth"] = build_auth_settings(
            host=host,
            port=port,
            path=path,
            issuer_url=auth_issuer_url,
            resource_url=auth_resource_url,
        )
        server_kwargs["token_verifier"] = StaticBearerTokenVerifier(auth_token)

    mcp = FastMCP(**server_kwargs)

    @mcp.tool(description="Execute an instant PromQL query against Prometheus.")
    async def prometheus_query(query: str, time: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        return await prometheus.query(query=query, time=time, timeout=timeout)

    @mcp.tool(description="Execute a bounded PromQL range query against Prometheus.")
    async def prometheus_query_range(query: str, start: str, end: str, step: str) -> dict[str, Any]:
        return await prometheus.query_range(query=query, start=start, end=end, step=step)

    @mcp.tool(description="List metric names, with optional prefix filtering and pagination.")
    async def prometheus_list_metrics(
        prefix: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await prometheus.list_metrics(prefix=prefix, limit=limit, offset=offset)

    @mcp.tool(description="Read metric metadata including type, help, and unit.")
    async def prometheus_metric_metadata(
        metric: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await prometheus.metric_metadata(metric=metric, limit=limit, offset=offset)

    @mcp.tool(description="List values for a Prometheus label, optionally constrained by match selectors.")
    async def prometheus_label_values(
        label: str,
        match: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        return await prometheus.label_values(label=label, match=match, limit=limit)

    @mcp.tool(description="Read active and dropped target state from Prometheus.")
    async def prometheus_targets(state: str = "any") -> dict[str, Any]:
        return await prometheus.targets(state=state)

    @mcp.tool(description="Read current Prometheus alerts, with optional state filtering.")
    async def prometheus_alerts(state: str | None = None) -> dict[str, Any]:
        return await prometheus.alerts(state=state)

    @mcp.tool(description="Read alerting and recording rules, with optional type/name/group filtering.")
    async def prometheus_rules(
        rule_type: str | None = None,
        rule_name: str | None = None,
        group_name: str | None = None,
    ) -> dict[str, Any]:
        return await prometheus.rules(rule_type=rule_type, rule_name=rule_name, group_name=group_name)

    @mcp.tool(description="Read Prometheus build, runtime, TSDB, and optional config status.")
    async def prometheus_status(include_config: bool = False) -> dict[str, Any]:
        return await prometheus.status(include_config=include_config)

    @mcp.tool(description="Return an operations-focused health summary for targets, alerts, TSDB, disk, CPU, memory, and MySQL.")
    async def prometheus_health_summary() -> dict[str, Any]:
        return await prometheus.health_summary()

    @mcp.tool(description="List known servers, exporter jobs, server/exporter pairs, JVM services, and mountpoints.")
    async def prometheus_inventory() -> dict[str, Any]:
        return await prometheus.inventory()

    @mcp.tool(description="Classify monitoring coverage and freshness for server/exporter pairs.")
    async def prometheus_monitoring_coverage() -> dict[str, Any]:
        return await prometheus.monitoring_coverage()

    @mcp.tool(description="Refresh prometheus_knowledge.json from live inventory and target freshness.")
    async def prometheus_refresh_context() -> dict[str, Any]:
        return await prometheus.refresh_context()

    @mcp.tool(description="Diagnose host health for one server: CPU, memory, load, disk, network, disk IO, alerts, and data freshness.")
    async def prometheus_server_health(server: str, remember: bool = True) -> dict[str, Any]:
        return await prometheus.server_health(server=server, remember=remember)

    @mcp.tool(description="Diagnose MySQL health for one server: connections, QPS, slow queries, locks, buffer pool, row operations, and host pressure.")
    async def prometheus_mysql_health(server: str, remember: bool = True) -> dict[str, Any]:
        return await prometheus.mysql_health(server=server, remember=remember)

    @mcp.tool(description="Diagnose JVM health for one server and optional service: heap, nonheap, GC, threads, deadlocks, and host pressure.")
    async def prometheus_jvm_health(
        server: str,
        service: str | None = None,
        remember: bool = True,
    ) -> dict[str, Any]:
        return await prometheus.jvm_health(server=server, service=service, remember=remember)

    @mcp.tool(description="Diagnose disk health for one server and optional mountpoint: capacity, remaining space, inode, alerts, and fill forecast.")
    async def prometheus_disk_health(
        server: str,
        mountpoint: str | None = None,
        remember: bool = True,
    ) -> dict[str, Any]:
        return await prometheus.disk_health(server=server, mountpoint=mountpoint, remember=remember)

    @mcp.tool(description="Read persisted environment context, mappings, fast diagnostic paths, and prior findings for new chats.")
    async def prometheus_context(server: str | None = None, topic: str | None = None) -> dict[str, Any]:
        return await prometheus.context(server=server, topic=topic)

    @mcp.tool(description="Read remembered Prometheus analysis history, optionally filtered by server and category.")
    async def prometheus_analysis_history(
        server: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await prometheus.analysis_history(server=server, category=category, limit=limit)

    @mcp.tool(description="Persist a concise analysis note so future chats can skip repeated discovery.")
    async def prometheus_remember_analysis(
        question: str,
        summary: str,
        server: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await prometheus.remember_analysis(
            question=question,
            summary=summary,
            server=server,
            category=category,
            tags=tags,
            details=details,
        )

    @mcp.tool(description="List built-in PromQL query templates, optionally filtered by category or exporter job.")
    async def prometheus_list_query_templates(
        category: str | None = None,
        exporter_job: str | None = None,
    ) -> dict[str, Any]:
        return await prometheus.list_query_templates(category=category, exporter_job=exporter_job)

    @mcp.tool(description="Get one PromQL query template by template_id.")
    async def prometheus_get_query_template(template_id: str) -> dict[str, Any]:
        return await prometheus.get_query_template(template_id=template_id)

    @mcp.tool(description="Render a PromQL query template without executing it.")
    async def prometheus_render_query_template(
        template_id: str,
        server: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await prometheus.render_query_template(template_id=template_id, server=server, variables=variables)

    @mcp.tool(description="Render and execute a PromQL query template. Provide start/end/step for range execution.")
    async def prometheus_run_query_template(
        template_id: str,
        server: str | None = None,
        variables: dict[str, Any] | None = None,
        start: str | None = None,
        end: str | None = None,
        step: str | None = None,
    ) -> dict[str, Any]:
        return await prometheus.run_query_template(
            template_id=template_id,
            server=server,
            variables=variables,
            start=start,
            end=end,
            step=step,
        )

    @mcp.resource("prometheus://metrics/all", mime_type="application/json")
    async def metrics_all() -> str:
        return as_resource_text(await prometheus.list_metrics(limit=5000))

    @mcp.resource("prometheus://metrics/by-prefix", mime_type="application/json")
    async def metrics_by_prefix() -> str:
        return as_resource_text(await prometheus.metrics_by_prefix_resource())

    @mcp.resource("prometheus://metrics/by-prefix/{prefix}", mime_type="application/json")
    async def metrics_by_specific_prefix(prefix: str) -> str:
        return as_resource_text(await prometheus.metrics_by_prefix_resource(prefix=prefix))

    @mcp.resource("prometheus://targets", mime_type="application/json")
    async def targets_resource() -> str:
        return as_resource_text(await prometheus.targets())

    @mcp.resource("prometheus://alerts", mime_type="application/json")
    async def alerts_resource() -> str:
        return as_resource_text(await prometheus.alerts())

    @mcp.resource("prometheus://rules", mime_type="application/json")
    async def rules_resource() -> str:
        return as_resource_text(await prometheus.rules())

    @mcp.resource("prometheus://status", mime_type="application/json")
    async def status_resource() -> str:
        return as_resource_text(await prometheus.status())

    @mcp.resource("prometheus://inventory", mime_type="application/json")
    async def inventory_resource() -> str:
        return as_resource_text(await prometheus.inventory())

    @mcp.resource("prometheus://monitoring-coverage", mime_type="application/json")
    async def monitoring_coverage_resource() -> str:
        return as_resource_text(await prometheus.monitoring_coverage())

    @mcp.resource("prometheus://context", mime_type="application/json")
    async def context_resource() -> str:
        return as_resource_text(await prometheus.context())

    @mcp.resource("prometheus://context/{server}", mime_type="application/json")
    async def context_for_server_resource(server: str) -> str:
        return as_resource_text(await prometheus.context(server=server))

    @mcp.resource("prometheus://analysis-history", mime_type="application/json")
    async def analysis_history_resource() -> str:
        return as_resource_text(await prometheus.analysis_history())

    @mcp.resource("prometheus://templates", mime_type="application/json")
    async def templates_resource() -> str:
        return as_resource_text(await prometheus.list_query_templates())

    @mcp.resource("prometheus://templates/{template_id}", mime_type="application/json")
    async def template_resource(template_id: str) -> str:
        return as_resource_text(await prometheus.get_query_template(template_id=template_id))

    return mcp


mcp = build_server()


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    config = load_mcp_config(args)
    configure_windows_http_event_loop(config["transport"])
    server = build_server(
        host=config["host"],
        port=config["port"],
        path=config["path"],
        auth_token=config["auth_token"],
        auth_issuer_url=config["auth_issuer_url"],
        auth_resource_url=config["auth_resource_url"],
    )

    if config["transport"] == "stdio":
        server.run(transport="stdio")
        return

    os.environ.setdefault("FASTMCP_HOST", config["host"])
    os.environ.setdefault("FASTMCP_PORT", str(config["port"]))
    server.run(transport="streamable-http")
