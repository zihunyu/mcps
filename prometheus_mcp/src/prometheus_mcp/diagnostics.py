from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

RISK_ORDER = {"ok": 0, "unknown": 1, "warning": 2, "critical": 3}
RISK_LABELS = {value: key for key, value in RISK_ORDER.items()}

HOST_EXPORTER = "node_exporter"
MYSQL_EXPORTER = "mysqld_exporter"
JVM_EXPORTER = "jvm_exporter"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def round_float(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def human_bytes(value: Any) -> dict[str, Any] | None:
    number = to_float(value)
    if number is None:
        return None

    sign = -1 if number < 0 else 1
    scaled = abs(number)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    unit = units[0]
    for unit in units:
        if scaled < 1024 or unit == units[-1]:
            break
        scaled /= 1024

    scaled *= sign
    return {
        "bytes": number,
        "value": round(scaled, 2),
        "unit": unit,
        "text": f"{scaled:.2f} {unit}",
    }


def human_rate(value: Any, kind: str = "ops") -> dict[str, Any] | None:
    number = to_float(value)
    if number is None:
        return None
    if kind == "bytes":
        formatted = human_bytes(number)
        if formatted is None:
            return None
        return {
            "per_second": number,
            "value": formatted["value"],
            "unit": f"{formatted['unit']}/s",
            "text": f"{formatted['value']:.2f} {formatted['unit']}/s",
        }
    unit = "qps" if kind == "qps" else "ops/s"
    return {
        "per_second": number,
        "value": round(number, 2),
        "unit": unit,
        "text": f"{number:.2f} {unit}",
    }


def human_percent(value: Any) -> dict[str, Any] | None:
    number = to_float(value)
    if number is None:
        return None
    return {
        "value": round(number, 2),
        "unit": "percent",
        "text": f"{number:.2f}%",
    }


def human_duration(seconds: Any) -> dict[str, Any] | None:
    number = to_float(seconds)
    if number is None or number < 0:
        return None

    if number < 60:
        text = f"{number:.0f}s"
    elif number < 3600:
        text = f"{number / 60:.1f}m"
    elif number < 86400:
        text = f"{number / 3600:.1f}h"
    else:
        text = f"{number / 86400:.1f}d"
    return {"seconds": number, "text": text}


def percent_risk(value: Any, warning: float, critical: float, lower_is_bad: bool = False) -> str:
    number = to_float(value)
    if number is None:
        return "unknown"
    if lower_is_bad:
        if number <= critical:
            return "critical"
        if number <= warning:
            return "warning"
        return "ok"
    if number >= critical:
        return "critical"
    if number >= warning:
        return "warning"
    return "ok"


def aggregate_risk(findings: list[dict[str, Any]], default: str = "ok") -> str:
    if not findings:
        return default
    score = max(RISK_ORDER.get(finding.get("level", "unknown"), 1) for finding in findings)
    return RISK_LABELS.get(score, "unknown")


def finding(
    level: str,
    message: str,
    metric: str | None = None,
    value: Any | None = None,
    labels: dict[str, Any] | None = None,
    recommendation: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"level": level, "message": message}
    if metric is not None:
        item["metric"] = metric
    if value is not None:
        item["value"] = value
    if labels:
        item["labels"] = labels
    if recommendation:
        item["recommendation"] = recommendation
    return item


def unwrap_query_result(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("query_result", payload)


def vector_samples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = unwrap_query_result(payload)
    data = result.get("data", {}) or {}
    if data.get("resultType") == "scalar":
        scalar = data.get("result")
        if isinstance(scalar, list) and len(scalar) == 2:
            return [{"labels": {}, "value": to_float(scalar[1]), "raw_value": scalar[1], "timestamp": scalar[0]}]
        return []

    samples: list[dict[str, Any]] = []
    for item in data.get("result", []) or []:
        point = item.get("value")
        if not (isinstance(point, list) and len(point) == 2):
            continue
        samples.append(
            {
                "labels": item.get("metric", {}) or {},
                "value": to_float(point[1]),
                "raw_value": point[1],
                "timestamp": point[0],
            }
        )
    return samples


def first_value(payload: dict[str, Any]) -> float | None:
    samples = vector_samples(payload)
    return samples[0]["value"] if samples else None


def first_sample(payload: dict[str, Any]) -> dict[str, Any] | None:
    samples = vector_samples(payload)
    return samples[0] if samples else None


def values_by_label(payload: dict[str, Any], label: str = "metric") -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for sample in vector_samples(payload):
        key = sample["labels"].get(label)
        if key:
            values[key] = sample["value"]
    return values


def rows_by_metric_label(
    payload: dict[str, Any],
    group_labels: tuple[str, ...],
    metric_label: str = "metric",
) -> list[dict[str, Any]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for sample in vector_samples(payload):
        labels = sample["labels"]
        metric_name = labels.get(metric_label)
        if not metric_name:
            continue
        key = tuple(labels.get(label, "") for label in group_labels)
        row = rows.setdefault(
            key,
            {
                "labels": {label: labels.get(label) for label in group_labels if labels.get(label) is not None},
                "values": {},
                "samples": [],
            },
        )
        row["values"][metric_name] = sample["value"]
        row["samples"].append(sample)
    return list(rows.values())


def top_percent_samples(payload: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    samples = vector_samples(payload)
    samples = [sample for sample in samples if sample["value"] is not None]
    samples.sort(key=lambda sample: sample["value"] or 0, reverse=True)
    return samples[:limit]


def active_target_pairs(targets: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for target in (targets.get("data", {}) or {}).get("activeTargets", []) or []:
        labels = target.get("labels", {}) or {}
        server = labels.get("instance")
        job = labels.get("job")
        if server and job and target.get("health") == "up":
            pairs.add((server, job))
    return pairs


def classify_pair(pair: dict[str, Any], active_pairs: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    active_pairs = active_pairs or set()
    server = pair.get("server") or pair.get("instance")
    job = pair.get("exporter_job") or pair.get("job")
    series_count = to_float(pair.get("series_count"))
    is_active = bool(server and job and (server, job) in active_pairs)

    if job and "pushgateway" in job:
        source_type = "pushgateway_data"
    elif is_active:
        source_type = "active_target"
    elif series_count and series_count > 0:
        source_type = "historical_series"
    else:
        source_type = "stale_unknown"

    return {
        **pair,
        "server": server,
        "instance": server,
        "exporter_job": job,
        "job": job,
        "source_type": source_type,
        "active_target": is_active,
        "series_count": int(series_count) if series_count is not None else None,
    }


def build_coverage_report(pairs: list[dict[str, Any]], targets: dict[str, Any]) -> dict[str, Any]:
    active_pairs = active_target_pairs(targets)
    classified_pairs = [classify_pair(pair, active_pairs) for pair in pairs]

    jobs_by_server: dict[str, set[str]] = defaultdict(set)
    source_types_by_server: dict[str, set[str]] = defaultdict(set)
    active_by_server: dict[str, bool] = defaultdict(bool)
    series_by_server: dict[str, int] = defaultdict(int)

    for pair in classified_pairs:
        server = pair.get("server")
        job = pair.get("exporter_job")
        if not server or not job:
            continue
        jobs_by_server[server].add(job)
        source_types_by_server[server].add(pair.get("source_type", "stale_unknown"))
        active_by_server[server] = active_by_server[server] or bool(pair.get("active_target"))
        if pair.get("series_count") is not None:
            series_by_server[server] += int(pair["series_count"])

    servers = []
    for server in sorted(jobs_by_server):
        jobs = jobs_by_server[server]
        missing_exporters: list[str] = []
        if (MYSQL_EXPORTER in jobs or JVM_EXPORTER in jobs) and HOST_EXPORTER not in jobs:
            missing_exporters.append(HOST_EXPORTER)
        if HOST_EXPORTER in jobs and MYSQL_EXPORTER not in jobs and server.startswith("mysql"):
            missing_exporters.append(MYSQL_EXPORTER)

        servers.append(
            {
                "server": server,
                "exporter_jobs": sorted(jobs),
                "source_types": sorted(source_types_by_server[server]),
                "has_active_target": bool(active_by_server[server]),
                "series_count": series_by_server.get(server, 0),
                "missing_exporters": missing_exporters,
            }
        )

    source_counts = Counter(pair.get("source_type", "stale_unknown") for pair in classified_pairs)
    return {
        "server_count": len(servers),
        "pair_count": len(classified_pairs),
        "active_target_pairs": sorted([{"server": server, "exporter_job": job} for server, job in active_pairs], key=lambda item: (item["server"], item["exporter_job"])),
        "source_type_counts": dict(source_counts),
        "server_coverage": servers,
        "classified_server_exporter_pairs": classified_pairs,
        "coverage_warnings": [
            {
                "server": row["server"],
                "missing_exporters": row["missing_exporters"],
                "message": f"{row['server']} is missing exporter(s): {', '.join(row['missing_exporters'])}",
            }
            for row in servers
            if row["missing_exporters"]
        ],
    }


def coverage_for_server(coverage: dict[str, Any], server: str) -> dict[str, Any] | None:
    for row in coverage.get("server_coverage", []) or []:
        if row.get("server") == server:
            return row
    return None


def normalize_bytes_metric(value: Any) -> dict[str, Any]:
    return {"raw": to_float(value), "human": human_bytes(value)}


def normalize_percent_metric(value: Any) -> dict[str, Any]:
    return {"raw": to_float(value), "human": human_percent(value)}


def normalize_rate_metric(value: Any, kind: str = "ops") -> dict[str, Any]:
    return {"raw": to_float(value), "human": human_rate(value, kind)}
