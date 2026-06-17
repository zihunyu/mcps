from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from .diagnostics import (
    aggregate_risk,
    build_coverage_report,
    coverage_for_server,
    finding,
    first_value,
    human_duration,
    human_rate,
    human_percent,
    normalize_bytes_metric,
    normalize_percent_metric,
    normalize_rate_metric,
    percent_risk,
    rows_by_metric_label,
    top_percent_samples,
    utc_now_iso,
    values_by_label,
    vector_samples,
)
from .knowledge import add_analysis_note, context_for, filter_analysis_history, read_knowledge, write_knowledge
from .templates import get_query_template, list_query_templates, promql_escape, render_query_template

DEFAULT_BASE_URL = "http://localhost:9090"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RANGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_RANGE_POINTS = 11000
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

LABEL_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
DURATION_RE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h|d|w|y)?\s*$")

METRIC_CATEGORIES: dict[str, dict[str, Any]] = {
    "mysql": {
        "label": "MySQL",
        "description": "connections, queries, InnoDB, buffer pool, temporary tables, slow queries, locks, replication/status variables",
        "prefixes": ("mysql_",),
    },
    "node": {
        "label": "Node",
        "description": "CPU, memory, disk, filesystem, network, load, processes, inode, operating system info",
        "prefixes": ("node_",),
    },
    "prometheus": {
        "label": "Prometheus",
        "description": "query engine, TSDB, rule evaluation, HTTP, target scraping, notification queues",
        "prefixes": ("prometheus_", "promhttp_"),
    },
    "go": {
        "label": "Go runtime",
        "description": "goroutines, GC, scheduler, heap, memory statistics, process runtime internals",
        "prefixes": ("go_",),
    },
    "jvm_jmx": {
        "label": "JVM/JMX",
        "description": "heap memory, GC, threads, class loading, JMX exporter state",
        "prefixes": ("jvm_", "jmx_"),
    },
    "pushgateway": {
        "label": "Pushgateway",
        "description": "push time, push failures, HTTP requests, push size and duration",
        "prefixes": ("pushgateway_", "push_"),
    },
    "process": {
        "label": "Process",
        "description": "process CPU, memory, file descriptors, network receive/transmit, named process groups",
        "prefixes": ("process_", "namedprocess_"),
    },
    "alerts": {
        "label": "Alerts",
        "description": "Prometheus alert state series",
        "prefixes": ("ALERTS", "ALERTS_FOR_STATE"),
    },
}


class QueryParams(BaseModel):
    query: str = Field(min_length=1)
    time: str | None = None
    timeout: float | None = Field(default=None, gt=0)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be empty")
        return value


class QueryRangeParams(BaseModel):
    query: str = Field(min_length=1)
    start: str
    end: str
    step: str | int | float

    @field_validator("query", "start", "end")
    @classmethod
    def strip_required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value


def parse_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def config_value(key: str, default: str, file_values: dict[str, str]) -> str:
    return os.getenv(key) or file_values.get(key) or default


def load_config_from_env(env_file: str | Path | None = None) -> dict[str, Any]:
    file_values = parse_env_file(env_file or DEFAULT_ENV_FILE)
    return {
        "base_url": config_value("PROMETHEUS_BASE_URL", DEFAULT_BASE_URL, file_values),
        "timeout_seconds": float(
            config_value("PROMETHEUS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS), file_values)
        ),
        "max_range_seconds": int(
            config_value("PROMETHEUS_MAX_RANGE_SECONDS", str(DEFAULT_MAX_RANGE_SECONDS), file_values)
        ),
        "max_range_points": int(
            config_value("PROMETHEUS_MAX_RANGE_POINTS", str(DEFAULT_MAX_RANGE_POINTS), file_values)
        ),
    }


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/query"):
        base_url = base_url.removesuffix("/query")
    return base_url or DEFAULT_BASE_URL


def parse_prometheus_time(value: str | int | float) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    raw = str(value).strip()
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except ValueError:
        pass

    iso_value = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(iso_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_duration_seconds(value: str | int | float) -> float:
    if isinstance(value, int | float):
        return float(value)

    match = DURATION_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid duration: {value!r}")

    number = float(match.group("value"))
    unit = match.group("unit") or "s"
    multipliers = {
        "ms": 0.001,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 7 * 86400,
        "y": 365 * 86400,
    }
    return number * multipliers[unit]


def validation_error_payload(error: ValidationError, query: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "errorType": "validation",
        "error": json.loads(error.json()),
    }
    if query is not None:
        payload["query"] = query
    return payload


def simple_error_payload(error_type: str, error: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "status": "error",
        "errorType": error_type,
        "error": error,
    }
    payload.update(extra)
    return payload


def metric_prefix(metric_name: str) -> str:
    for category, config in METRIC_CATEGORIES.items():
        if any(metric_name.startswith(prefix) for prefix in config["prefixes"]):
            return category
    return metric_name.split("_", 1)[0] if "_" in metric_name else metric_name


def summarize_metric_names(metric_names: list[str]) -> dict[str, Any]:
    category_counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    for name in metric_names:
        category = metric_prefix(name)
        category_counts[category] += 1
        if len(samples[category]) < 10:
            samples[category].append(name)

    return {
        "total": len(metric_names),
        "categories": [
            {
                "category": category,
                "label": METRIC_CATEGORIES.get(category, {}).get("label", category),
                "description": METRIC_CATEGORIES.get(category, {}).get("description", ""),
                "count": count,
                "sample_metrics": samples[category],
            }
            for category, count in category_counts.most_common()
        ],
    }


def summarize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for metric, entries in metadata.items():
        for entry in entries or []:
            rows.append(
                {
                    "metric": metric,
                    "type": entry.get("type", "unknown"),
                    "unit": entry.get("unit", ""),
                    "help": entry.get("help", ""),
                }
            )

    return {
        "metric_count": len(metadata),
        "metadata_entry_count": len(rows),
        "types": dict(Counter(row["type"] for row in rows)),
        "categories": summarize_metric_names(sorted(metadata.keys()))["categories"],
    }


def summarize_targets(data: dict[str, Any]) -> dict[str, Any]:
    active = data.get("activeTargets", []) or []
    dropped = data.get("droppedTargets", []) or []
    health_counts = Counter(target.get("health", "unknown") for target in active)
    jobs = Counter((target.get("labels") or {}).get("job", "unknown") for target in active)
    unhealthy = [
        {
            "job": (target.get("labels") or {}).get("job"),
            "instance": (target.get("labels") or {}).get("instance"),
            "health": target.get("health"),
            "lastError": target.get("lastError"),
            "scrapeUrl": target.get("scrapeUrl"),
        }
        for target in active
        if target.get("health") != "up" or target.get("lastError")
    ]
    return {
        "active_count": len(active),
        "dropped_count": len(dropped),
        "health": dict(health_counts),
        "jobs": dict(jobs),
        "unhealthy_targets": unhealthy,
    }


def summarize_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    by_state = Counter(alert.get("state", "unknown") for alert in alerts)
    by_name = Counter((alert.get("labels") or {}).get("alertname", "unknown") for alert in alerts)
    by_severity = Counter((alert.get("labels") or {}).get("severity", "unknown") for alert in alerts)
    firing = [alert for alert in alerts if alert.get("state") == "firing"]
    return {
        "total": len(alerts),
        "firing_count": len(firing),
        "states": dict(by_state),
        "severities": dict(by_severity),
        "alertnames": dict(by_name),
        "firing_alerts": [
            {
                "alertname": (alert.get("labels") or {}).get("alertname"),
                "severity": (alert.get("labels") or {}).get("severity"),
                "instance": (alert.get("labels") or {}).get("instance"),
                "mountpoint": (alert.get("labels") or {}).get("mountpoint"),
                "state": alert.get("state"),
                "activeAt": alert.get("activeAt"),
                "value": alert.get("value"),
                "summary": (alert.get("annotations") or {}).get("summary"),
                "description": (alert.get("annotations") or {}).get("description"),
            }
            for alert in firing
        ],
    }


def summarize_rules(groups: list[dict[str, Any]]) -> dict[str, Any]:
    rule_count = 0
    state_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    group_summaries = []

    for group in groups:
        rules = group.get("rules", []) or []
        rule_count += len(rules)
        for rule in rules:
            state_counts[rule.get("state", "unknown")] += 1
            type_counts[rule.get("type", "unknown")] += 1
        group_summaries.append(
            {
                "name": group.get("name"),
                "file": group.get("file"),
                "interval": group.get("interval"),
                "rule_count": len(rules),
                "evaluationTime": group.get("evaluationTime"),
                "lastEvaluation": group.get("lastEvaluation"),
            }
        )

    return {
        "group_count": len(groups),
        "rule_count": rule_count,
        "states": dict(state_counts),
        "types": dict(type_counts),
        "groups": group_summaries,
    }


def scalar_or_vector_values(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data", {})
    if data.get("resultType") == "scalar":
        value = data.get("result")
        return [{"value": value[1], "timestamp": value[0]}] if isinstance(value, list) and len(value) == 2 else []

    values = []
    for item in data.get("result", []) or []:
        metric = item.get("metric", {})
        point = item.get("value")
        if isinstance(point, list) and len(point) == 2:
            values.append({"metric": metric, "value": point[1], "timestamp": point[0]})
    return values


class PrometheusClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_range_seconds: int = DEFAULT_MAX_RANGE_SECONDS,
        max_range_points: int = DEFAULT_MAX_RANGE_POINTS,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.max_range_seconds = max_range_seconds
        self.max_range_points = max_range_points

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "PrometheusClient":
        return cls(**load_config_from_env(env_file=env_file))

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = {}
        for key, value in (params or {}).items():
            if value is not None:
                request_params[key] = value

        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds) as client:
                response = await client.get(path, params=request_params)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            return simple_error_payload("timeout", str(exc), path=path, params=request_params)
        except httpx.HTTPStatusError as exc:
            return simple_error_payload(
                "http_status",
                str(exc),
                status_code=exc.response.status_code,
                path=path,
                params=request_params,
                response_text=exc.response.text,
            )
        except httpx.RequestError as exc:
            return simple_error_payload("request", str(exc), path=path, params=request_params)
        except ValueError as exc:
            return simple_error_payload("decode", str(exc), path=path, params=request_params)

        if not isinstance(payload, dict):
            return simple_error_payload("decode", "Prometheus response was not a JSON object", path=path)

        return payload

    async def query(self, query: str, time: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        try:
            params = QueryParams(query=query, time=time, timeout=timeout)
        except ValidationError as exc:
            return validation_error_payload(exc, query=query)

        timeout_param = f"{params.timeout:g}s" if params.timeout is not None else None
        payload = await self._get(
            "/api/v1/query",
            {"query": params.query, "time": params.time, "timeout": timeout_param},
        )
        payload.setdefault("query", params.query)
        payload["summary"] = {
            "result_count": len(payload.get("data", {}).get("result", []) or []),
            "result_type": payload.get("data", {}).get("resultType"),
        }
        return payload

    async def query_range(self, query: str, start: str, end: str, step: str | int | float) -> dict[str, Any]:
        try:
            params = QueryRangeParams(query=query, start=start, end=end, step=step)
        except ValidationError as exc:
            return validation_error_payload(exc, query=query)

        try:
            start_dt = parse_prometheus_time(params.start)
            end_dt = parse_prometheus_time(params.end)
            step_seconds = parse_duration_seconds(params.step)
        except ValueError as exc:
            return simple_error_payload("validation", str(exc), query=params.query)

        span_seconds = (end_dt - start_dt).total_seconds()
        if span_seconds <= 0:
            return simple_error_payload("validation", "end must be later than start", query=params.query)
        if span_seconds > self.max_range_seconds:
            return simple_error_payload(
                "validation",
                f"query_range span exceeds {self.max_range_seconds} seconds",
                query=params.query,
                span_seconds=span_seconds,
            )
        if step_seconds <= 0:
            return simple_error_payload("validation", "step must be greater than zero", query=params.query)

        estimated_points = int(span_seconds / step_seconds) + 1
        if estimated_points > self.max_range_points:
            return simple_error_payload(
                "validation",
                f"query_range would request about {estimated_points} points per series; limit is {self.max_range_points}",
                query=params.query,
                estimated_points=estimated_points,
            )

        payload = await self._get(
            "/api/v1/query_range",
            {
                "query": params.query,
                "start": params.start,
                "end": params.end,
                "step": params.step,
            },
        )
        payload.setdefault("query", params.query)
        payload["summary"] = {
            "result_count": len(payload.get("data", {}).get("result", []) or []),
            "result_type": payload.get("data", {}).get("resultType"),
            "span_seconds": span_seconds,
            "step_seconds": step_seconds,
            "estimated_points_per_series": estimated_points,
        }
        return payload

    async def list_metrics(self, prefix: str | None = None, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        if limit < 1 or limit > 5000:
            return simple_error_payload("validation", "limit must be between 1 and 5000")
        if offset < 0:
            return simple_error_payload("validation", "offset must be greater than or equal to zero")

        payload = await self._get("/api/v1/label/__name__/values")
        if payload.get("status") != "success":
            return payload

        metrics = sorted(payload.get("data", []) or [])
        if prefix:
            metrics = [name for name in metrics if name.startswith(prefix)]

        total = len(metrics)
        page = metrics[offset : offset + limit]
        return {
            "status": "success",
            "data": page,
            "summary": {
                "total": total,
                "returned": len(page),
                "limit": limit,
                "offset": offset,
                "prefix": prefix,
                "has_more": offset + limit < total,
                "metric_categories": summarize_metric_names(metrics)["categories"],
            },
        }

    async def metric_metadata(self, metric: str | None = None, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        if limit < 1 or limit > 5000:
            return simple_error_payload("validation", "limit must be between 1 and 5000")
        if offset < 0:
            return simple_error_payload("validation", "offset must be greater than or equal to zero")

        payload = await self._get("/api/v1/metadata", {"metric": metric})
        if payload.get("status") != "success":
            return payload

        metadata = payload.get("data", {}) or {}
        items = sorted(metadata.items())
        total = len(items)
        page = dict(items[offset : offset + limit])
        return {
            "status": "success",
            "data": page,
            "summary": {
                **summarize_metadata(metadata),
                "returned": len(page),
                "limit": limit,
                "offset": offset,
                "metric": metric,
                "has_more": offset + limit < total,
            },
        }

    async def label_values(self, label: str, match: list[str] | None = None, limit: int = 1000) -> dict[str, Any]:
        label = label.strip()
        if not LABEL_NAME_RE.match(label):
            return simple_error_payload("validation", f"invalid label name: {label!r}")
        if limit < 1 or limit > 10000:
            return simple_error_payload("validation", "limit must be between 1 and 10000")

        params: dict[str, Any] = {}
        if match:
            params["match[]"] = match

        payload = await self._get(f"/api/v1/label/{label}/values", params)
        if payload.get("status") != "success":
            return payload

        values = sorted(payload.get("data", []) or [])
        return {
            "status": "success",
            "data": values[:limit],
            "summary": {
                "label": label,
                "total": len(values),
                "returned": min(limit, len(values)),
                "has_more": len(values) > limit,
                "match": match,
            },
        }

    async def targets(self, state: str = "any") -> dict[str, Any]:
        if state not in {"any", "active", "dropped"}:
            return simple_error_payload("validation", "state must be one of: any, active, dropped")

        payload = await self._get("/api/v1/targets", {"state": state})
        if payload.get("status") == "success":
            payload["summary"] = summarize_targets(payload.get("data", {}) or {})
        return payload

    async def alerts(self, state: str | None = None) -> dict[str, Any]:
        payload = await self._get("/api/v1/alerts")
        if payload.get("status") != "success":
            return payload

        alerts = payload.get("data", {}).get("alerts", []) or []
        if state:
            alerts = [alert for alert in alerts if alert.get("state") == state]

        return {
            "status": "success",
            "data": {"alerts": alerts},
            "summary": summarize_alerts(alerts),
        }

    async def rules(
        self,
        rule_type: str | None = None,
        rule_name: str | None = None,
        group_name: str | None = None,
    ) -> dict[str, Any]:
        if rule_type is not None and rule_type not in {"alert", "record"}:
            return simple_error_payload("validation", "rule_type must be one of: alert, record")

        payload = await self._get("/api/v1/rules", {"type": rule_type})
        if payload.get("status") != "success":
            return payload

        groups = payload.get("data", {}).get("groups", []) or []
        filtered_groups = []
        for group in groups:
            if group_name and group.get("name") != group_name:
                continue
            rules = group.get("rules", []) or []
            if rule_name:
                rules = [rule for rule in rules if rule.get("name") == rule_name]
            if rules or not rule_name:
                copied = dict(group)
                copied["rules"] = rules
                filtered_groups.append(copied)

        return {
            "status": "success",
            "data": {"groups": filtered_groups},
            "summary": summarize_rules(filtered_groups),
        }

    async def status(self, include_config: bool = False) -> dict[str, Any]:
        build = await self._get("/api/v1/status/buildinfo")
        runtime = await self._get("/api/v1/status/runtimeinfo")
        tsdb = await self._get("/api/v1/status/tsdb")
        config = await self._get("/api/v1/status/config") if include_config else None

        payload = {
            "status": "success",
            "data": {
                "buildinfo": build.get("data") if build.get("status") == "success" else None,
                "runtimeinfo": runtime.get("data") if runtime.get("status") == "success" else None,
                "tsdb": tsdb.get("data") if tsdb.get("status") == "success" else None,
            },
            "errors": {
                "buildinfo": build if build.get("status") != "success" else None,
                "runtimeinfo": runtime if runtime.get("status") != "success" else None,
                "tsdb": tsdb if tsdb.get("status") != "success" else None,
            },
        }
        if include_config:
            payload["data"]["config"] = config.get("data") if config and config.get("status") == "success" else None
            payload["errors"]["config"] = config if config and config.get("status") != "success" else None

        tsdb_data = payload["data"]["tsdb"] or {}
        runtime_data = payload["data"]["runtimeinfo"] or {}
        build_data = payload["data"]["buildinfo"] or {}
        payload["summary"] = {
            "prometheus_version": build_data.get("version"),
            "revision": build_data.get("revision"),
            "go_version": build_data.get("goVersion") or build_data.get("goversion"),
            "start_time": runtime_data.get("startTime"),
            "storage_retention": runtime_data.get("storageRetention"),
            "head_series": (tsdb_data.get("headStats") or {}).get("numSeries"),
            "head_chunks": (tsdb_data.get("headStats") or {}).get("numChunks"),
        }
        return payload

    async def metrics_by_prefix_resource(self, prefix: str | None = None) -> dict[str, Any]:
        result = await self.list_metrics(prefix=prefix, limit=5000, offset=0)
        if result.get("status") != "success":
            return result
        metrics = result.get("data", []) or []
        return {
            "status": "success",
            "data": {
                "prefix": prefix,
                "summary": summarize_metric_names(metrics),
            },
        }

    async def inventory(self) -> dict[str, Any]:
        jobs = await self.label_values("job", limit=10000)
        instances = await self.label_values("instance", limit=10000)
        services = await self.label_values("service", limit=10000)
        mountpoints = await self.label_values("mountpoint", limit=10000)
        pairs = await self.query('count({__name__=~".+"}) by (job, instance)')

        pair_rows = []
        if pairs.get("status") == "success":
            for item in pairs.get("data", {}).get("result", []) or []:
                metric = item.get("metric", {}) or {}
                value = item.get("value", [None, None])
                pair_rows.append(
                    {
                        "server": metric.get("instance"),
                        "instance": metric.get("instance"),
                        "exporter_job": metric.get("job"),
                        "job": metric.get("job"),
                        "series_count": int(float(value[1])) if isinstance(value, list) and len(value) > 1 else None,
                    }
                )

        return {
            "status": "success",
            "summary": {
                "server_label": "server 参数映射 Prometheus label instance；例如 server=\"your-server\" 等价于 instance=\"your-server\"。",
                "exporter_job_label": "exporter_job 参数映射 Prometheus label job；job 表示部署的 exporter/采集来源。",
                "server_count": len(instances.get("data", []) or []),
                "exporter_job_count": len(jobs.get("data", []) or []),
                "service_count": len(services.get("data", []) or []),
                "mountpoint_count": len(mountpoints.get("data", []) or []),
            },
            "data": {
                "servers": instances.get("data", []) or [],
                "instances": instances.get("data", []) or [],
                "exporter_jobs": jobs.get("data", []) or [],
                "jobs": jobs.get("data", []) or [],
                "server_exporter_pairs": pair_rows,
                "services": services.get("data", []) or [],
                "mountpoints": mountpoints.get("data", []) or [],
            },
            "errors": {
                "jobs": jobs if jobs.get("status") != "success" else None,
                "instances": instances if instances.get("status") != "success" else None,
                "services": services if services.get("status") != "success" else None,
                "mountpoints": mountpoints if mountpoints.get("status") != "success" else None,
                "server_exporter_pairs": pairs if pairs.get("status") != "success" else None,
            },
        }

    async def monitoring_coverage(self) -> dict[str, Any]:
        inventory = await self.inventory()
        targets = await self.targets()
        if inventory.get("status") != "success":
            return inventory

        coverage = build_coverage_report(
            inventory.get("data", {}).get("server_exporter_pairs", []) or [],
            targets if targets.get("status") == "success" else {"data": {"activeTargets": []}},
        )
        return {
            "status": "success",
            "summary": {
                "server_count": coverage["server_count"],
                "pair_count": coverage["pair_count"],
                "source_type_counts": coverage["source_type_counts"],
                "coverage_warning_count": len(coverage["coverage_warnings"]),
                "semantics": {
                    "active_target": "Prometheus /targets reports this server/job as an active healthy scrape target.",
                    "pushgateway_data": "The data source is Pushgateway-related rather than a direct server scrape.",
                    "historical_series": "Series exist in Prometheus, but this server/job is not an active scrape target now.",
                    "stale_unknown": "No active target and no current series count was confirmed.",
                },
            },
            "data": coverage,
            "errors": {
                "inventory": inventory if inventory.get("status") != "success" else None,
                "targets": targets if targets.get("status") != "success" else None,
            },
        }

    async def refresh_context(self) -> dict[str, Any]:
        inventory = await self.inventory()
        targets = await self.targets()
        if inventory.get("status") != "success":
            return inventory

        coverage = build_coverage_report(
            inventory.get("data", {}).get("server_exporter_pairs", []) or [],
            targets if targets.get("status") == "success" else {"data": {"activeTargets": []}},
        )
        knowledge = read_knowledge()
        inventory_data = inventory.get("data", {}) or {}
        knowledge["known_servers"] = sorted(inventory_data.get("servers", []) or [])
        knowledge["known_exporter_jobs"] = sorted(inventory_data.get("exporter_jobs", []) or [])
        knowledge["known_server_exporter_pairs"] = [
            {
                "server": pair.get("server"),
                "exporter_job": pair.get("exporter_job"),
                "source_type": pair.get("source_type"),
                "active_target": pair.get("active_target"),
                "series_count": pair.get("series_count"),
            }
            for pair in coverage.get("classified_server_exporter_pairs", [])
            if pair.get("server") and pair.get("exporter_job")
        ]
        knowledge["inventory_coverage"] = {
            "refreshed_at": utc_now_iso(),
            "server_count": coverage["server_count"],
            "pair_count": coverage["pair_count"],
            "source_type_counts": coverage["source_type_counts"],
            "server_coverage": coverage["server_coverage"],
            "coverage_warnings": coverage["coverage_warnings"],
        }
        write_knowledge(knowledge)

        return {
            "status": "success",
            "summary": {
                "message": "prometheus_knowledge.json refreshed from live inventory",
                "server_count": len(knowledge["known_servers"]),
                "exporter_job_count": len(knowledge["known_exporter_jobs"]),
                "pair_count": len(knowledge["known_server_exporter_pairs"]),
                "source_type_counts": coverage["source_type_counts"],
                "coverage_warning_count": len(coverage["coverage_warnings"]),
            },
            "data": {
                "known_servers": knowledge["known_servers"],
                "known_exporter_jobs": knowledge["known_exporter_jobs"],
                "known_server_exporter_pairs": knowledge["known_server_exporter_pairs"],
                "inventory_coverage": knowledge["inventory_coverage"],
            },
            "errors": {
                "targets": targets if targets.get("status") != "success" else None,
            },
        }

    async def context(self, server: str | None = None, topic: str | None = None) -> dict[str, Any]:
        return context_for(server=server, topic=topic)

    async def analysis_history(
        self,
        server: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            return simple_error_payload("validation", "limit must be between 1 and 200")
        knowledge = read_knowledge()
        history = filter_analysis_history(knowledge, server=server, category=category, limit=limit)
        return {
            "status": "success",
            "data": history,
            "summary": {
                "returned": len(history),
                "server": server,
                "category": category,
            },
        }

    async def remember_analysis(
        self,
        question: str,
        summary: str,
        server: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not question.strip():
            return simple_error_payload("validation", "question must not be empty")
        if not summary.strip():
            return simple_error_payload("validation", "summary must not be empty")
        note = add_analysis_note(
            question=question.strip(),
            summary=summary.strip(),
            server=server,
            category=category,
            tags=tags,
            details=details,
        )
        return {
            "status": "success",
            "data": note,
            "summary": {
                "message": "analysis note persisted for future chats",
                "server": server,
                "category": category,
            },
        }

    async def list_query_templates(
        self,
        category: str | None = None,
        exporter_job: str | None = None,
    ) -> dict[str, Any]:
        templates = list_query_templates(category=category, exporter_job=exporter_job)
        categories = dict(Counter(template["category"] for template in templates))
        exporter_jobs = dict(Counter(template["exporter_job"] for template in templates))
        return {
            "status": "success",
            "data": templates,
            "summary": {
                "total": len(templates),
                "categories": categories,
                "exporter_jobs": exporter_jobs,
                "filters": {"category": category, "exporter_job": exporter_job},
                "semantics": {
                    "server": "server 参数映射 label instance。",
                    "exporter_job": "exporter_job 参数映射 label job，表示 exporter/采集来源。",
                },
            },
        }

    async def get_query_template(self, template_id: str) -> dict[str, Any]:
        template = get_query_template(template_id)
        if template is None:
            return simple_error_payload("not_found", f"unknown query template: {template_id}", template_id=template_id)
        return {"status": "success", "data": template}

    async def render_query_template(
        self,
        template_id: str,
        server: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return render_query_template(template_id=template_id, server=server, variables=variables)

    async def run_query_template(
        self,
        template_id: str,
        server: str | None = None,
        variables: dict[str, Any] | None = None,
        start: str | None = None,
        end: str | None = None,
        step: str | None = None,
    ) -> dict[str, Any]:
        rendered = await self.render_query_template(template_id=template_id, server=server, variables=variables)
        if rendered.get("status") != "success":
            return rendered

        template_data = rendered["data"]
        query = template_data["rendered_query"]
        if start or end or step or template_data.get("query_type") == "range":
            if not (start and end and step):
                return simple_error_payload(
                    "validation",
                    "range template execution requires start, end, and step",
                    template_id=template_id,
                    rendered_query=query,
                )
            result = await self.query_range(query=query, start=start, end=end, step=step)
        else:
            result = await self.query(query=query)

        return {
            "status": result.get("status", "error"),
            "template": template_data,
            "query_result": result,
            "summary": {
                "template_id": template_id,
                "category": template_data.get("category"),
                "exporter_job": template_data.get("exporter_job"),
                "unit": template_data.get("unit"),
                "result_count": result.get("summary", {}).get("result_count"),
                "result_type": result.get("summary", {}).get("result_type"),
            },
        }

    async def server_health(self, server: str, remember: bool = True) -> dict[str, Any]:
        server = server.strip()
        if not server:
            return simple_error_payload("validation", "server must not be empty")

        templates = await self._run_template_map(
            server,
            [
                {"key": "cpu", "template_id": "linux_cpu_usage_percent", "variables": {"window": "5m"}},
                {"key": "memory", "template_id": "linux_memory_overview"},
                {"key": "load", "template_id": "linux_load_average"},
                {"key": "filesystems_top", "template_id": "linux_filesystem_usage_percent_top", "variables": {"topk": 10}},
                {"key": "disk_io", "template_id": "linux_disk_io_bytes_rate", "variables": {"window": "5m"}},
                {"key": "network_io", "template_id": "linux_network_io_bytes_rate", "variables": {"window": "5m"}},
                {"key": "processes", "template_id": "linux_process_count"},
            ],
        )
        selector = self._selector("node_exporter", server)
        cpu_expr = f'100 - (avg by (instance) (rate(node_cpu_seconds_total{{{selector},mode="idle"}}[5m])) * 100)'
        memory_expr = (
            f'(1 - node_memory_MemAvailable_bytes{{{selector}}} / '
            f'node_memory_MemTotal_bytes{{{selector}}}) * 100'
        )
        trends = await self._query_map(
            {
                "cpu_avg_1h": f"avg_over_time(({cpu_expr})[1h:5m])",
                "cpu_max_1h": f"max_over_time(({cpu_expr})[1h:5m])",
                "memory_avg_1h": f"avg_over_time(({memory_expr})[1h:5m])",
                "memory_max_1h": f"max_over_time(({memory_expr})[1h:5m])",
                "cpu_cores": f"count(count by (cpu) (node_cpu_seconds_total{{{selector}}}))",
            }
        )
        alerts = await self.alerts(state="firing")
        coverage = await self.monitoring_coverage()

        cpu_value = first_value(templates["cpu"])
        memory_values = values_by_label(templates["memory"], "metric")
        load_values = values_by_label(templates["load"], "load")
        process_values = values_by_label(templates["processes"], "metric")
        disk_top = top_percent_samples(templates["filesystems_top"], limit=10)
        firing_alerts = self._alerts_for_server(alerts, server)
        server_coverage = coverage_for_server(coverage.get("data", {}), server) if coverage.get("status") == "success" else None

        findings = self._coverage_findings(server_coverage)
        self._append_percent_finding(
            findings,
            "cpu_usage_percent",
            cpu_value,
            warning=85,
            critical=95,
            message="CPU usage is high.",
        )
        self._append_percent_finding(
            findings,
            "memory_usage_percent",
            memory_values.get("usage_percent"),
            warning=90,
            critical=97,
            message="Memory usage is high.",
        )
        cpu_cores = first_value(trends["cpu_cores"])
        if cpu_cores and load_values.get("15m") and load_values["15m"] > cpu_cores:
            findings.append(
                finding(
                    "warning",
                    "15 minute load is above CPU core count.",
                    metric="node_load15",
                    value={"load15": round(load_values["15m"], 2), "cpu_cores": round(cpu_cores, 2)},
                )
            )
        for sample in disk_top:
            self._append_percent_finding(
                findings,
                "filesystem_usage_percent",
                sample.get("value"),
                warning=80,
                critical=95,
                message="Filesystem usage is high.",
                labels=sample.get("labels"),
            )
        findings.extend(self._alert_findings(firing_alerts))

        risk = aggregate_risk(findings)
        headline = self._headline("server", server, risk, findings)
        metrics = {
            "cpu": {
                "current_percent": normalize_percent_metric(cpu_value),
                "avg_1h_percent": normalize_percent_metric(first_value(trends["cpu_avg_1h"])),
                "max_1h_percent": normalize_percent_metric(first_value(trends["cpu_max_1h"])),
                "cores": first_value(trends["cpu_cores"]),
            },
            "memory": {
                "total": normalize_bytes_metric(memory_values.get("total_bytes")),
                "used": normalize_bytes_metric(memory_values.get("used_bytes")),
                "available": normalize_bytes_metric(memory_values.get("available_bytes")),
                "usage_percent": normalize_percent_metric(memory_values.get("usage_percent")),
                "avg_1h_percent": normalize_percent_metric(first_value(trends["memory_avg_1h"])),
                "max_1h_percent": normalize_percent_metric(first_value(trends["memory_max_1h"])),
            },
            "load": {key: round(value, 2) if value is not None else None for key, value in load_values.items()},
            "processes": process_values,
            "filesystems_top": [self._percent_sample(sample) for sample in disk_top],
            "disk_io": self._rate_samples(templates["disk_io"], "bytes", limit=20),
            "network_io": self._rate_samples(templates["network_io"], "bytes", limit=20),
            "firing_alerts": firing_alerts,
            "coverage": server_coverage,
        }

        note = self._remember_diagnostic(
            remember=remember,
            category="server",
            server=server,
            summary=headline,
            details={"risk": risk, "findings": findings[:10], "metrics": metrics},
        )
        return self._diagnostic_payload(
            diagnostic="server_health",
            server=server,
            risk=risk,
            headline=headline,
            findings=findings,
            metrics=metrics,
            recommended_next_queries=[
                "linux_cpu_usage_percent",
                "linux_memory_overview",
                "linux_filesystem_usage_percent_top",
                "linux_disk_io_bytes_rate",
                "linux_network_io_bytes_rate",
            ],
            raw={"templates": self._compact_results(templates), "trends": self._compact_results(trends), "alerts": alerts},
            knowledge_note=note,
        )

    async def mysql_health(self, server: str, remember: bool = True) -> dict[str, Any]:
        server = server.strip()
        if not server:
            return simple_error_payload("validation", "server must not be empty")

        templates = await self._run_template_map(
            server,
            [
                {"key": "connections", "template_id": "mysql_connections"},
                {"key": "qps", "template_id": "mysql_qps", "variables": {"window": "5m"}},
                {"key": "slow_queries", "template_id": "mysql_slow_queries_rate", "variables": {"window": "5m"}},
                {"key": "aborted_connects", "template_id": "mysql_aborted_connects_rate", "variables": {"window": "5m"}},
                {"key": "network", "template_id": "mysql_network_bytes_rate", "variables": {"window": "5m"}},
                {"key": "buffer_pool", "template_id": "mysql_innodb_buffer_pool_usage"},
                {"key": "buffer_pool_hit_ratio", "template_id": "mysql_innodb_buffer_pool_hit_ratio", "variables": {"window": "5m"}},
                {"key": "tmp_disk_table_ratio", "template_id": "mysql_tmp_disk_table_ratio", "variables": {"window": "5m"}},
                {"key": "table_lock_wait", "template_id": "mysql_table_lock_wait_rate", "variables": {"window": "5m"}},
                {"key": "row_ops", "template_id": "mysql_row_ops_rate", "variables": {"window": "5m"}},
                {"key": "host_cpu", "template_id": "linux_cpu_usage_percent", "variables": {"window": "5m"}},
                {"key": "host_memory", "template_id": "linux_memory_overview"},
                {"key": "host_load", "template_id": "linux_load_average"},
            ],
        )
        mysql_selector = self._selector("mysqld_exporter", server)
        qps_expr = f"rate(mysql_global_status_queries{{{mysql_selector}}}[5m])"
        slow_expr = f"rate(mysql_global_status_slow_queries{{{mysql_selector}}}[5m])"
        row_read_expr = f'rate(mysql_global_status_innodb_row_ops_total{{{mysql_selector},operation="read"}}[5m])'
        hit_expr = (
            f"(1 - rate(mysql_global_status_innodb_buffer_pool_reads{{{mysql_selector}}}[5m]) / "
            f"rate(mysql_global_status_innodb_buffer_pool_read_requests{{{mysql_selector}}}[5m])) * 100"
        )
        trends = await self._query_map(
            {
                "qps_avg_1h": f"avg_over_time(({qps_expr})[1h:5m])",
                "qps_max_1h": f"max_over_time(({qps_expr})[1h:5m])",
                "slow_queries_avg_1h": f"avg_over_time(({slow_expr})[1h:5m])",
                "slow_queries_max_1h": f"max_over_time(({slow_expr})[1h:5m])",
                "row_reads_avg_1h": f"avg_over_time(({row_read_expr})[1h:5m])",
                "row_reads_max_1h": f"max_over_time(({row_read_expr})[1h:5m])",
                "buffer_pool_hit_ratio_avg_1h": f"avg_over_time(({hit_expr})[1h:5m])",
            }
        )
        alerts = await self.alerts(state="firing")
        coverage = await self.monitoring_coverage()

        conn = values_by_label(templates["connections"], "metric")
        buffer_pool = values_by_label(templates["buffer_pool"], "metric")
        host_memory = values_by_label(templates["host_memory"], "metric")
        host_load = values_by_label(templates["host_load"], "load")
        qps = first_value(templates["qps"])
        slow_queries = first_value(templates["slow_queries"])
        aborted_connects = first_value(templates["aborted_connects"])
        hit_ratio = first_value(templates["buffer_pool_hit_ratio"])
        tmp_disk_table_ratio = first_value(templates["tmp_disk_table_ratio"])
        table_lock_wait = first_value(templates["table_lock_wait"])
        row_ops_samples = vector_samples(templates["row_ops"])
        firing_alerts = self._alerts_for_server(alerts, server)
        server_coverage = coverage_for_server(coverage.get("data", {}), server) if coverage.get("status") == "success" else None

        findings = self._coverage_findings(server_coverage)
        if server_coverage and "mysqld_exporter" not in set(server_coverage.get("exporter_jobs", [])):
            findings.append(finding("warning", "No mysqld_exporter series were found for this server."))
        self._append_percent_finding(
            findings,
            "connection_usage_percent",
            conn.get("connection_usage_percent"),
            warning=80,
            critical=90,
            message="MySQL connection usage is high.",
        )
        self._append_rate_finding(
            findings,
            "slow_queries_rate",
            slow_queries,
            warning=0.1,
            critical=1,
            message="Slow query rate is elevated.",
        )
        self._append_rate_finding(
            findings,
            "aborted_connects_rate",
            aborted_connects,
            warning=0.1,
            critical=1,
            message="Aborted connection rate is elevated.",
        )
        self._append_percent_finding(
            findings,
            "buffer_pool_hit_ratio",
            hit_ratio,
            warning=99,
            critical=95,
            message="InnoDB buffer pool hit ratio is low.",
            lower_is_bad=True,
        )
        self._append_percent_finding(
            findings,
            "tmp_disk_table_ratio",
            tmp_disk_table_ratio,
            warning=10,
            critical=30,
            message="Temporary disk table ratio is high.",
        )
        self._append_rate_finding(
            findings,
            "table_lock_wait_rate",
            table_lock_wait,
            warning=0.1,
            critical=1,
            message="Table lock wait rate is elevated.",
        )
        self._append_percent_finding(
            findings,
            "host_cpu_usage_percent",
            first_value(templates["host_cpu"]),
            warning=85,
            critical=95,
            message="Host CPU usage is high while MySQL is running.",
        )
        self._append_percent_finding(
            findings,
            "host_memory_usage_percent",
            host_memory.get("usage_percent"),
            warning=90,
            critical=97,
            message="Host memory usage is high while MySQL is running.",
        )
        findings.extend(self._alert_findings(firing_alerts))

        row_ops = self._rate_samples(templates["row_ops"], "ops", limit=20)
        network = self._rate_samples(templates["network"], "bytes", limit=10)
        metrics = {
            "connections": {
                "threads_connected": conn.get("threads_connected"),
                "threads_running": conn.get("threads_running"),
                "max_connections": conn.get("max_connections"),
                "usage_percent": normalize_percent_metric(conn.get("connection_usage_percent")),
            },
            "query_rates": {
                "qps": normalize_rate_metric(qps, "qps"),
                "qps_avg_1h": normalize_rate_metric(first_value(trends["qps_avg_1h"]), "qps"),
                "qps_max_1h": normalize_rate_metric(first_value(trends["qps_max_1h"]), "qps"),
                "slow_queries_per_second": normalize_rate_metric(slow_queries, "ops"),
                "slow_queries_avg_1h": normalize_rate_metric(first_value(trends["slow_queries_avg_1h"]), "ops"),
                "slow_queries_max_1h": normalize_rate_metric(first_value(trends["slow_queries_max_1h"]), "ops"),
                "aborted_connects_per_second": normalize_rate_metric(aborted_connects, "ops"),
            },
            "innodb": {
                "buffer_pool_data": normalize_bytes_metric(buffer_pool.get("data_bytes")),
                "buffer_pool_dirty": normalize_bytes_metric(buffer_pool.get("dirty_bytes")),
                "buffer_pool_size": normalize_bytes_metric(buffer_pool.get("pool_size_bytes")),
                "buffer_pool_usage_percent": normalize_percent_metric(buffer_pool.get("usage_percent")),
                "buffer_pool_hit_ratio": normalize_percent_metric(hit_ratio),
                "buffer_pool_hit_ratio_avg_1h": normalize_percent_metric(
                    first_value(trends["buffer_pool_hit_ratio_avg_1h"])
                ),
                "row_ops": row_ops,
                "row_reads_avg_1h": normalize_rate_metric(first_value(trends["row_reads_avg_1h"]), "ops"),
                "row_reads_max_1h": normalize_rate_metric(first_value(trends["row_reads_max_1h"]), "ops"),
            },
            "locks_and_temp_tables": {
                "tmp_disk_table_ratio": normalize_percent_metric(tmp_disk_table_ratio),
                "table_lock_wait_per_second": normalize_rate_metric(table_lock_wait, "ops"),
            },
            "network": network,
            "host": {
                "cpu_usage_percent": normalize_percent_metric(first_value(templates["host_cpu"])),
                "memory_usage_percent": normalize_percent_metric(host_memory.get("usage_percent")),
                "memory_total": normalize_bytes_metric(host_memory.get("total_bytes")),
                "memory_available": normalize_bytes_metric(host_memory.get("available_bytes")),
                "load": host_load,
            },
            "firing_alerts": firing_alerts,
            "coverage": server_coverage,
            "raw_row_ops_sample_count": len(row_ops_samples),
        }

        risk = aggregate_risk(findings)
        headline = self._headline("mysql", server, risk, findings)
        note = self._remember_diagnostic(
            remember=remember,
            category="mysql",
            server=server,
            summary=headline,
            details={"risk": risk, "findings": findings[:10], "metrics": metrics},
        )
        return self._diagnostic_payload(
            diagnostic="mysql_health",
            server=server,
            risk=risk,
            headline=headline,
            findings=findings,
            metrics=metrics,
            recommended_next_queries=[
                "mysql_connections",
                "mysql_qps",
                "mysql_slow_queries_rate",
                "mysql_innodb_buffer_pool_usage",
                "mysql_innodb_buffer_pool_hit_ratio",
                "mysql_row_ops_rate",
                "linux_cpu_usage_percent",
                "linux_memory_overview",
            ],
            raw={"templates": self._compact_results(templates), "trends": self._compact_results(trends), "alerts": alerts},
            knowledge_note=note,
        )

    async def jvm_health(self, server: str, service: str | None = None, remember: bool = True) -> dict[str, Any]:
        server = server.strip()
        service = service.strip() if service else None
        if not server:
            return simple_error_payload("validation", "server must not be empty")

        variables = {"service": service} if service else {}
        specs = [
            {"key": "memory", "template_id": "jvm_memory_overview", "variables": variables},
            {"key": "memory_pool", "template_id": "jvm_memory_pool_usage", "variables": variables},
            {"key": "gc_rate", "template_id": "jvm_gc_rate", "variables": {**variables, "window": "5m"}},
            {"key": "gc_time_rate", "template_id": "jvm_gc_time_rate", "variables": {**variables, "window": "5m"}},
            {"key": "threads", "template_id": "jvm_threads", "variables": variables},
            {"key": "threads_state", "template_id": "jvm_threads_state", "variables": variables},
            {"key": "classes", "template_id": "jvm_classes", "variables": {**variables, "window": "5m"}},
            {"key": "host_cpu", "template_id": "linux_cpu_usage_percent", "variables": {"window": "5m"}},
            {"key": "host_memory", "template_id": "linux_memory_overview"},
        ]
        if service:
            specs.append({"key": "heap_by_service", "template_id": "jvm_heap_usage_by_service", "variables": {"service": service}})
        templates = await self._run_template_map(server, specs)
        alerts = await self.alerts(state="firing")
        coverage = await self.monitoring_coverage()

        memory_rows = rows_by_metric_label(templates["memory"], ("area", "service"))
        pool_rows = rows_by_metric_label(templates["memory_pool"], ("pool", "service"))
        thread_values = values_by_label(templates["threads"], "metric")
        host_memory = values_by_label(templates["host_memory"], "metric")
        gc_rate_total = self._sum_values(templates["gc_rate"])
        gc_time_rate_total = self._sum_values(templates["gc_time_rate"])
        heap_usage = first_value(templates.get("heap_by_service", {})) if service else None
        firing_alerts = self._alerts_for_server(alerts, server)
        server_coverage = coverage_for_server(coverage.get("data", {}), server) if coverage.get("status") == "success" else None

        findings = self._coverage_findings(server_coverage)
        if server_coverage and "jvm_exporter" not in set(server_coverage.get("exporter_jobs", [])):
            findings.append(finding("warning", "No jvm_exporter series were found for this server."))
        for row in memory_rows:
            usage = row.get("values", {}).get("usage_percent")
            area = row.get("labels", {}).get("area", "unknown")
            self._append_percent_finding(
                findings,
                "jvm_memory_usage_percent",
                usage,
                warning=80,
                critical=90,
                message=f"JVM {area} memory usage is high.",
                labels=row.get("labels"),
            )
        for row in pool_rows:
            usage = row.get("values", {}).get("usage_percent")
            pool = row.get("labels", {}).get("pool", "unknown")
            self._append_percent_finding(
                findings,
                "jvm_memory_pool_usage_percent",
                usage,
                warning=85,
                critical=95,
                message=f"JVM memory pool {pool} usage is high.",
                labels=row.get("labels"),
            )
        if heap_usage is not None:
            self._append_percent_finding(
                findings,
                "jvm_heap_usage_percent",
                heap_usage,
                warning=80,
                critical=90,
                message="JVM heap usage for the selected service is high.",
                labels={"service": service},
            )
        if thread_values.get("deadlocked") and thread_values["deadlocked"] > 0:
            findings.append(
                finding(
                    "critical",
                    "JVM deadlocked threads were detected.",
                    metric="jvm_threads_deadlocked",
                    value=thread_values["deadlocked"],
                    labels={"service": service} if service else None,
                )
            )
        if gc_time_rate_total is not None and gc_time_rate_total >= 0.2:
            findings.append(
                finding(
                    "warning",
                    "JVM GC time rate is elevated.",
                    metric="jvm_gc_collection_seconds_sum_rate",
                    value=round(gc_time_rate_total, 4),
                )
            )
        self._append_percent_finding(
            findings,
            "host_cpu_usage_percent",
            first_value(templates["host_cpu"]),
            warning=85,
            critical=95,
            message="Host CPU usage is high while JVM is running.",
        )
        self._append_percent_finding(
            findings,
            "host_memory_usage_percent",
            host_memory.get("usage_percent"),
            warning=90,
            critical=97,
            message="Host memory usage is high while JVM is running.",
        )
        findings.extend(self._alert_findings(firing_alerts))

        metrics = {
            "memory": [self._memory_row(row) for row in memory_rows],
            "memory_pools": [self._memory_row(row) for row in pool_rows],
            "heap_by_service": normalize_percent_metric(heap_usage) if service else None,
            "gc": {
                "collections_per_second": normalize_rate_metric(gc_rate_total, "ops"),
                "seconds_per_second": {"raw": gc_time_rate_total, "human": human_rate(gc_time_rate_total, "ops")},
                "collectors": self._rate_samples(templates["gc_rate"], "ops", limit=20),
            },
            "threads": {
                "current": thread_values.get("current"),
                "daemon": thread_values.get("daemon"),
                "peak": thread_values.get("peak"),
                "deadlocked": thread_values.get("deadlocked"),
                "states": vector_samples(templates["threads_state"])[:50],
            },
            "classes": vector_samples(templates["classes"])[:20],
            "host": {
                "cpu_usage_percent": normalize_percent_metric(first_value(templates["host_cpu"])),
                "memory_usage_percent": normalize_percent_metric(host_memory.get("usage_percent")),
                "memory_total": normalize_bytes_metric(host_memory.get("total_bytes")),
                "memory_available": normalize_bytes_metric(host_memory.get("available_bytes")),
            },
            "firing_alerts": firing_alerts,
            "coverage": server_coverage,
        }

        risk = aggregate_risk(findings)
        headline = self._headline("jvm", server, risk, findings)
        note = self._remember_diagnostic(
            remember=remember,
            category="jvm",
            server=server,
            summary=headline,
            details={"risk": risk, "service": service, "findings": findings[:10], "metrics": metrics},
        )
        return self._diagnostic_payload(
            diagnostic="jvm_health",
            server=server,
            risk=risk,
            headline=headline,
            findings=findings,
            metrics=metrics,
            recommended_next_queries=[
                "jvm_memory_overview",
                "jvm_memory_pool_usage",
                "jvm_gc_rate",
                "jvm_gc_time_rate",
                "jvm_threads",
                "linux_cpu_usage_percent",
                "linux_memory_overview",
            ],
            raw={"templates": self._compact_results(templates), "alerts": alerts},
            knowledge_note=note,
            extra_summary={"service": service},
        )

    async def disk_health(
        self,
        server: str,
        mountpoint: str | None = None,
        remember: bool = True,
    ) -> dict[str, Any]:
        server = server.strip()
        mountpoint = mountpoint.strip() if mountpoint else None
        if not server:
            return simple_error_payload("validation", "server must not be empty")

        variables = {"mountpoint": mountpoint} if mountpoint else {}
        templates = await self._run_template_map(
            server,
            [
                {"key": "filesystem", "template_id": "linux_filesystem_usage", "variables": variables},
                {"key": "inode", "template_id": "linux_inode_usage", "variables": variables},
                {"key": "filesystems_top", "template_id": "linux_filesystem_usage_percent_top", "variables": {"topk": 10}},
                {"key": "disk_io", "template_id": "linux_disk_io_bytes_rate", "variables": {"window": "5m"}},
            ],
        )
        trend_queries: dict[str, str] = {}
        if mountpoint:
            selector = self._selector("node_exporter", server, {"mountpoint": mountpoint})
            usage_expr = (
                f"(node_filesystem_size_bytes{{{selector}}} - node_filesystem_avail_bytes{{{selector}}}) / "
                f"node_filesystem_size_bytes{{{selector}}} * 100"
            )
            trend_queries = {
                "usage_avg_1h": f"avg_over_time(({usage_expr})[1h:5m])",
                "usage_max_1h": f"max_over_time(({usage_expr})[1h:5m])",
                "available_predicted_24h": f"predict_linear(node_filesystem_avail_bytes{{{selector}}}[6h], 24 * 3600)",
                "seconds_to_full": f"node_filesystem_avail_bytes{{{selector}}} / -deriv(node_filesystem_avail_bytes{{{selector}}}[6h])",
            }
        trends = await self._query_map(trend_queries) if trend_queries else {}
        alerts = await self.alerts(state="firing")
        coverage = await self.monitoring_coverage()

        fs_rows = rows_by_metric_label(templates["filesystem"], ("mountpoint", "device"))
        inode_rows = rows_by_metric_label(templates["inode"], ("mountpoint", "device"))
        disk_top = top_percent_samples(templates["filesystems_top"], limit=10)
        firing_alerts = self._alerts_for_server(alerts, server, mountpoint=mountpoint)
        server_coverage = coverage_for_server(coverage.get("data", {}), server) if coverage.get("status") == "success" else None

        findings = self._coverage_findings(server_coverage)
        for row in fs_rows:
            usage = row.get("values", {}).get("usage_percent")
            self._append_percent_finding(
                findings,
                "filesystem_usage_percent",
                usage,
                warning=80,
                critical=95,
                message="Filesystem usage is high.",
                labels=row.get("labels"),
            )
        for row in inode_rows:
            usage = row.get("values", {}).get("usage_percent")
            self._append_percent_finding(
                findings,
                "inode_usage_percent",
                usage,
                warning=85,
                critical=95,
                message="Inode usage is high.",
                labels=row.get("labels"),
            )
        seconds_to_full = first_value(trends.get("seconds_to_full", {})) if trends else None
        if seconds_to_full is not None and seconds_to_full > 0:
            if seconds_to_full <= 86400:
                level = "critical"
            elif seconds_to_full <= 7 * 86400:
                level = "warning"
            else:
                level = "ok"
            if level != "ok":
                findings.append(
                    finding(
                        level,
                        "Filesystem may fill soon based on 6h linear trend.",
                        metric="seconds_to_full",
                        value=human_duration(seconds_to_full),
                        labels={"mountpoint": mountpoint} if mountpoint else None,
                    )
                )
        findings.extend(self._alert_findings(firing_alerts))

        filesystems = [self._filesystem_row(row) for row in fs_rows]
        inodes = [self._inode_row(row) for row in inode_rows]
        metrics = {
            "filesystems": filesystems,
            "inodes": inodes,
            "top_usage": [self._percent_sample(sample) for sample in disk_top],
            "disk_io": self._rate_samples(templates["disk_io"], "bytes", limit=20),
            "trend": {
                "usage_avg_1h": normalize_percent_metric(first_value(trends.get("usage_avg_1h", {}))) if trends else None,
                "usage_max_1h": normalize_percent_metric(first_value(trends.get("usage_max_1h", {}))) if trends else None,
                "available_predicted_24h": normalize_bytes_metric(
                    first_value(trends.get("available_predicted_24h", {}))
                )
                if trends
                else None,
                "seconds_to_full": human_duration(seconds_to_full),
            },
            "firing_alerts": firing_alerts,
            "coverage": server_coverage,
        }

        risk = aggregate_risk(findings)
        headline = self._headline("disk", server, risk, findings)
        note = self._remember_diagnostic(
            remember=remember,
            category="disk",
            server=server,
            summary=headline,
            details={"risk": risk, "mountpoint": mountpoint, "findings": findings[:10], "metrics": metrics},
        )
        return self._diagnostic_payload(
            diagnostic="disk_health",
            server=server,
            risk=risk,
            headline=headline,
            findings=findings,
            metrics=metrics,
            recommended_next_queries=[
                "linux_filesystem_usage",
                "linux_inode_usage",
                "linux_filesystem_usage_percent_top",
                "linux_disk_io_bytes_rate",
            ],
            raw={"templates": self._compact_results(templates), "trends": self._compact_results(trends), "alerts": alerts},
            knowledge_note=note,
            extra_summary={"mountpoint": mountpoint},
        )

    async def health_summary(self) -> dict[str, Any]:
        targets = await self.targets()
        alerts = await self.alerts()
        rules = await self.rules()
        status = await self.status()

        queries = {
            "up": "up",
            "tsdb_head_series": "prometheus_tsdb_head_series",
            "disk_usage_top10": 'topk(10, (node_filesystem_size_bytes{fstype!~"tmpfs|fuse.lxcfs|squashfs|vfat|iso9660|configfs|autofs|gpfs|vboxsf|nfs|smb",mountpoint!~"/boot.*|/var/lib/docker/.*"} - node_filesystem_avail_bytes) / node_filesystem_size_bytes)',
            "cpu_usage_percent": '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
            "memory_usage_percent": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
            "mysql_threads_connected": "mysql_global_status_threads_connected",
            "mysql_slow_queries_rate": "rate(mysql_global_status_slow_queries[5m])",
        }

        query_results: dict[str, Any] = {}
        for key, expression in queries.items():
            result = await self.query(expression)
            query_results[key] = {
                "query": expression,
                "status": result.get("status"),
                "summary": result.get("summary"),
                "values": scalar_or_vector_values(result)[:20],
                "error": result.get("error"),
                "errorType": result.get("errorType"),
            }

        alert_summary = alerts.get("summary", {})
        target_summary = targets.get("summary", {})
        status_summary = status.get("summary", {})
        rule_summary = rules.get("summary", {})

        return {
            "status": "success",
            "summary": {
                "prometheus": status_summary,
                "targets": target_summary,
                "alerts": alert_summary,
                "rules": rule_summary,
                "risk_notes": self._build_risk_notes(target_summary, alert_summary, query_results),
            },
            "data": {
                "targets": targets.get("data"),
                "alerts": alerts.get("data"),
                "rules": rules.get("data"),
                "status": status.get("data"),
                "queries": query_results,
            },
        }

    def _build_risk_notes(
        self,
        target_summary: dict[str, Any],
        alert_summary: dict[str, Any],
        query_results: dict[str, Any],
    ) -> list[str]:
        notes: list[str] = []
        firing_count = alert_summary.get("firing_count", 0)
        if firing_count:
            notes.append(f"{firing_count} firing alert(s) require attention.")

        unhealthy_targets = target_summary.get("unhealthy_targets", [])
        if unhealthy_targets:
            notes.append(f"{len(unhealthy_targets)} target(s) are unhealthy or reporting scrape errors.")

        disk_values = query_results.get("disk_usage_top10", {}).get("values", [])
        high_disk = []
        for item in disk_values:
            try:
                if float(item.get("value", 0)) >= 0.8:
                    high_disk.append(item)
            except (TypeError, ValueError):
                continue
        if high_disk:
            notes.append(f"{len(high_disk)} filesystem sample(s) are at or above 80% usage.")

        if not notes:
            notes.append("No immediate target, alert, or high disk usage risk found from the sampled queries.")
        return notes

    async def _run_template_map(self, server: str, specs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        async def run_one(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            key = spec["key"]
            try:
                result = await self.run_query_template(
                    template_id=spec["template_id"],
                    server=server,
                    variables=spec.get("variables"),
                    start=spec.get("start"),
                    end=spec.get("end"),
                    step=spec.get("step"),
                )
            except Exception as exc:  # pragma: no cover - defensive isolation for one failed template
                result = simple_error_payload(
                    "exception",
                    str(exc),
                    template_id=spec.get("template_id"),
                    server=server,
                )
            return key, result

        pairs = await asyncio.gather(*(run_one(spec) for spec in specs))
        return dict(pairs)

    async def _query_map(self, queries: dict[str, str]) -> dict[str, dict[str, Any]]:
        async def run_one(key: str, query: str) -> tuple[str, dict[str, Any]]:
            try:
                result = await self.query(query)
            except Exception as exc:  # pragma: no cover - defensive isolation for one failed query
                result = simple_error_payload("exception", str(exc), query=query)
            return key, result

        pairs = await asyncio.gather(*(run_one(key, query) for key, query in queries.items()))
        return dict(pairs)

    def _selector(self, exporter_job: str, server: str | None = None, labels: dict[str, Any] | None = None) -> str:
        matchers = [f'job="{promql_escape(exporter_job)}"']
        if server:
            matchers.append(f'instance="{promql_escape(server)}"')
        for key, value in (labels or {}).items():
            if value is not None and value != "":
                matchers.append(f'{key}="{promql_escape(value)}"')
        return ",".join(matchers)

    def _alerts_for_server(
        self,
        alerts: dict[str, Any],
        server: str,
        mountpoint: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        for alert in (alerts.get("data", {}) or {}).get("alerts", []) or []:
            labels = alert.get("labels", {}) or {}
            if labels.get("instance") != server:
                continue
            if mountpoint and labels.get("mountpoint") != mountpoint:
                continue
            rows.append(
                {
                    "alertname": labels.get("alertname"),
                    "severity": labels.get("severity"),
                    "instance": labels.get("instance"),
                    "mountpoint": labels.get("mountpoint"),
                    "device": labels.get("device"),
                    "state": alert.get("state"),
                    "activeAt": alert.get("activeAt"),
                    "value": alert.get("value"),
                    "summary": (alert.get("annotations") or {}).get("summary"),
                    "description": (alert.get("annotations") or {}).get("description"),
                }
            )
        return rows

    def _alert_findings(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings = []
        for alert in alerts:
            severity = (alert.get("severity") or "").lower()
            alertname = alert.get("alertname") or "alert"
            level = "critical" if severity == "critical" or "critical" in alertname.lower() else "warning"
            findings.append(
                finding(
                    level,
                    f"Firing alert: {alertname}.",
                    metric="ALERTS",
                    value=alert.get("value"),
                    labels={key: value for key, value in alert.items() if key in {"instance", "mountpoint", "device"}},
                    recommendation=alert.get("description") or alert.get("summary"),
                )
            )
        return findings

    def _coverage_findings(self, server_coverage: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not server_coverage:
            return [
                finding(
                    "unknown",
                    "No live inventory coverage was found for this server.",
                    recommendation="Run prometheus_refresh_context or prometheus_inventory to confirm server/job mappings.",
                )
            ]

        findings = []
        source_types = set(server_coverage.get("source_types", []) or [])
        if not server_coverage.get("has_active_target"):
            if "historical_series" in source_types:
                level = "warning"
                message = "Server has Prometheus series, but no active scrape target is currently healthy."
            elif "pushgateway_data" in source_types:
                level = "unknown"
                message = "Server data appears to come from Pushgateway-related series, not direct active scrape."
            else:
                level = "unknown"
                message = "Server data freshness is unknown."
            findings.append(
                finding(
                    level,
                    message,
                    metric="inventory_coverage",
                    value={"source_types": sorted(source_types)},
                    recommendation="Check prometheus_targets and exporter deployment if fresh live scraping is required.",
                )
            )
        for exporter in server_coverage.get("missing_exporters", []) or []:
            findings.append(
                finding(
                    "warning",
                    f"Expected exporter is missing: {exporter}.",
                    metric="inventory_coverage",
                    value=exporter,
                )
            )
        return findings

    def _append_percent_finding(
        self,
        findings: list[dict[str, Any]],
        metric: str,
        value: Any,
        warning: float,
        critical: float,
        message: str,
        labels: dict[str, Any] | None = None,
        lower_is_bad: bool = False,
    ) -> None:
        level = percent_risk(value, warning=warning, critical=critical, lower_is_bad=lower_is_bad)
        if level in {"warning", "critical"}:
            findings.append(
                finding(
                    level,
                    message,
                    metric=metric,
                    value=human_percent(value),
                    labels=labels,
                )
            )

    def _append_rate_finding(
        self,
        findings: list[dict[str, Any]],
        metric: str,
        value: Any,
        warning: float,
        critical: float,
        message: str,
        labels: dict[str, Any] | None = None,
    ) -> None:
        number = first_value({"data": {"resultType": "scalar", "result": [0, value]}}) if value is not None else None
        if number is None:
            return
        if number >= critical:
            level = "critical"
        elif number >= warning:
            level = "warning"
        else:
            return
        findings.append(
            finding(
                level,
                message,
                metric=metric,
                value=human_rate(number, "ops"),
                labels=labels,
            )
        )

    def _headline(self, diagnostic: str, server: str, risk: str, findings: list[dict[str, Any]]) -> str:
        if not findings:
            return f"{server} {diagnostic} risk is ok; no threshold breach was found in sampled metrics."
        primary = sorted(findings, key=lambda item: RISK_ORDER_FOR_CLIENT.get(item.get("level", "unknown"), 1), reverse=True)[0]
        return f"{server} {diagnostic} risk is {risk}: {primary.get('message')}"

    def _remember_diagnostic(
        self,
        remember: bool,
        category: str,
        server: str,
        summary: str,
        details: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not remember:
            return None
        try:
            return add_analysis_note(
                question=f"prometheus_{category}_health({server})",
                summary=summary,
                server=server,
                category=category,
                tags=["auto_diagnostic", "health"],
                details={
                    "risk": details.get("risk"),
                    "service": details.get("service"),
                    "mountpoint": details.get("mountpoint"),
                    "findings": details.get("findings", [])[:10],
                },
            )
        except Exception as exc:  # pragma: no cover - persistence failure must not break diagnostics
            return {"status": "error", "errorType": "knowledge_write", "error": str(exc)}

    def _diagnostic_payload(
        self,
        diagnostic: str,
        server: str,
        risk: str,
        headline: str,
        findings: list[dict[str, Any]],
        metrics: dict[str, Any],
        recommended_next_queries: list[str],
        raw: dict[str, Any],
        knowledge_note: dict[str, Any] | None,
        extra_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = {
            "diagnostic": diagnostic,
            "server": server,
            "risk": risk,
            "headline": headline,
            "finding_count": len(findings),
            "generated_at": utc_now_iso(),
        }
        if extra_summary:
            summary.update(extra_summary)
        return {
            "status": "success",
            "summary": summary,
            "data": {
                "findings": findings,
                "metrics": metrics,
                "recommended_next_queries": recommended_next_queries,
                "knowledge_note": knowledge_note,
                "raw": raw,
            },
        }

    def _compact_results(self, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, payload in results.items():
            result = payload.get("query_result", payload)
            compact[key] = {
                "status": payload.get("status"),
                "template": (payload.get("template") or {}).get("id"),
                "query": result.get("query"),
                "summary": payload.get("summary") or result.get("summary"),
                "samples": vector_samples(payload)[:5],
                "errorType": payload.get("errorType") or result.get("errorType"),
                "error": payload.get("error") or result.get("error"),
            }
        return compact

    def _percent_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        return {
            "labels": sample.get("labels", {}),
            "value": normalize_percent_metric(sample.get("value")),
            "timestamp": sample.get("timestamp"),
        }

    def _rate_samples(self, payload: dict[str, Any], kind: str = "ops", limit: int = 20) -> list[dict[str, Any]]:
        rows = []
        for sample in vector_samples(payload)[:limit]:
            rows.append(
                {
                    "labels": sample.get("labels", {}),
                    "value": normalize_rate_metric(sample.get("value"), kind),
                    "timestamp": sample.get("timestamp"),
                }
            )
        return rows

    def _sum_values(self, payload: dict[str, Any]) -> float | None:
        samples = [sample["value"] for sample in vector_samples(payload) if sample.get("value") is not None]
        return sum(samples) if samples else None

    def _memory_row(self, row: dict[str, Any]) -> dict[str, Any]:
        values = row.get("values", {}) or {}
        return {
            "labels": row.get("labels", {}),
            "used": normalize_bytes_metric(values.get("used_bytes")),
            "max": normalize_bytes_metric(values.get("max_bytes")),
            "usage_percent": normalize_percent_metric(values.get("usage_percent")),
        }

    def _filesystem_row(self, row: dict[str, Any]) -> dict[str, Any]:
        values = row.get("values", {}) or {}
        return {
            "labels": row.get("labels", {}),
            "total": normalize_bytes_metric(values.get("total_bytes")),
            "used": normalize_bytes_metric(values.get("used_bytes")),
            "available": normalize_bytes_metric(values.get("available_bytes")),
            "usage_percent": normalize_percent_metric(values.get("usage_percent")),
        }

    def _inode_row(self, row: dict[str, Any]) -> dict[str, Any]:
        values = row.get("values", {}) or {}
        return {
            "labels": row.get("labels", {}),
            "total_inodes": values.get("total_inodes"),
            "used_inodes": values.get("used_inodes"),
            "free_inodes": values.get("free_inodes"),
            "usage_percent": normalize_percent_metric(values.get("usage_percent")),
        }


RISK_ORDER_FOR_CLIENT = {"ok": 0, "unknown": 1, "warning": 2, "critical": 3}
