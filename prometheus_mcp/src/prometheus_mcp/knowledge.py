from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_FILE = PROJECT_ROOT / "prometheus_knowledge.json"

BUILTIN_CONTEXT: dict[str, Any] = {
    "version": 1,
    "purpose": "Persisted Prometheus MCP context for new chats so they can skip repeated environment discovery.",
    "semantics": {
        "server": 'User-facing "server" maps to Prometheus label "instance". Example: server="your-server" -> instance="your-server".',
        "exporter_job": 'User-facing "exporter_job" maps to Prometheus label "job". job means exporter or scrape source, not business service.',
        "jvm_service": 'JVM business service maps to label "service". Example: service="your-service".',
    },
    "known_servers": [
        "linux-server-1",
        "app-server-1",
        "db-server-1",
        "localhost:9090",
        "pushgateway:9091",
    ],
    "known_exporter_jobs": [
        "node_exporter",
        "jvm_exporter",
        "mysqld_exporter",
        "prometheus",
        "pushgateway_data",
    ],
    "known_server_exporter_pairs": [
        {"server": "linux-server-1", "exporter_job": "node_exporter"},
        {"server": "app-server-1", "exporter_job": "node_exporter"},
        {"server": "app-server-1", "exporter_job": "jvm_exporter"},
        {"server": "db-server-1", "exporter_job": "node_exporter"},
        {"server": "db-server-1", "exporter_job": "mysqld_exporter"},
        {"server": "localhost:9090", "exporter_job": "prometheus"},
        {"server": "pushgateway:9091", "exporter_job": "pushgateway_data"},
    ],
    "diagnostic_fast_paths": {
        "linux": [
            "linux_cpu_usage_percent",
            "linux_memory_overview",
            "linux_load_average",
            "linux_filesystem_usage_percent_top",
            "linux_disk_io_bytes_rate",
            "linux_network_io_bytes_rate",
        ],
        "mysql": [
            "mysql_connections",
            "mysql_qps",
            "mysql_slow_queries_rate",
            "mysql_aborted_connects_rate",
            "mysql_innodb_buffer_pool_usage",
            "mysql_innodb_buffer_pool_hit_ratio",
            "mysql_tmp_disk_table_ratio",
            "mysql_table_lock_wait_rate",
            "mysql_row_ops_rate",
            "linux_cpu_usage_percent",
            "linux_memory_overview",
            "linux_load_average",
            "linux_filesystem_usage_percent_top",
        ],
        "jvm": [
            "jvm_memory_overview",
            "jvm_heap_usage_by_service",
            "jvm_memory_pool_usage",
            "jvm_gc_rate",
            "jvm_gc_time_rate",
            "jvm_threads",
            "jvm_threads_state",
            "linux_cpu_usage_percent",
            "linux_memory_overview",
        ],
        "disk": [
            "linux_filesystem_usage",
            "linux_filesystem_usage_percent_top",
            "linux_inode_usage",
            "linux_disk_io_bytes_rate",
        ],
    },
    "known_findings": [
        {
            "server": "db-server-1",
            "category": "mysql",
            "summary": (
                "Example MySQL diagnosis memory. Replace this with live findings by calling "
                "prometheus_remember_analysis or prometheus_refresh_context."
            ),
            "recommended_templates": [
                "mysql_connections",
                "mysql_qps",
                "mysql_innodb_buffer_pool_usage",
                "mysql_innodb_buffer_pool_hit_ratio",
                "mysql_row_ops_rate",
                "linux_cpu_usage_percent",
                "linux_memory_overview",
                "linux_load_average",
            ],
        },
        {
            "server": "linux-server-1",
            "category": "disk",
            "summary": (
                "Example disk diagnosis memory. Replace this with live findings by calling "
                "prometheus_remember_analysis or prometheus_refresh_context."
            ),
            "recommended_templates": [
                "linux_filesystem_usage",
                "linux_filesystem_usage_percent_top",
                "linux_inode_usage",
            ],
        },
    ],
    "analysis_history": [],
}


def read_knowledge(path: str | Path | None = None) -> dict[str, Any]:
    knowledge = deepcopy(BUILTIN_CONTEXT)
    knowledge_path = Path(path or DEFAULT_KNOWLEDGE_FILE)
    if not knowledge_path.exists():
        return knowledge

    try:
        stored = json.loads(knowledge_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return knowledge

    for key, value in stored.items():
        if isinstance(value, list) and isinstance(knowledge.get(key), list):
            knowledge[key] = _merge_list(knowledge[key], value)
        elif isinstance(value, dict) and isinstance(knowledge.get(key), dict):
            merged = dict(knowledge[key])
            merged.update(value)
            knowledge[key] = merged
        else:
            knowledge[key] = value
    return knowledge


def write_knowledge(data: dict[str, Any], path: str | Path | None = None) -> None:
    knowledge_path = Path(path or DEFAULT_KNOWLEDGE_FILE)
    knowledge_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_analysis_note(
    question: str,
    summary: str,
    server: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    details: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    knowledge = read_knowledge(path)
    note = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "server": server,
        "category": category,
        "question": question,
        "summary": summary,
        "tags": tags or [],
        "details": details or {},
    }
    history = knowledge.setdefault("analysis_history", [])
    history.insert(0, note)
    knowledge["analysis_history"] = history[:200]
    write_knowledge(knowledge, path)
    return note


def filter_analysis_history(
    knowledge: dict[str, Any],
    server: str | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    history = knowledge.get("analysis_history", []) or []
    results = []
    for note in history:
        if server and note.get("server") != server:
            continue
        if category and note.get("category") != category:
            continue
        results.append(note)
        if len(results) >= limit:
            break
    return results


def context_for(
    server: str | None = None,
    topic: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    knowledge = read_knowledge(path)
    normalized_topic = topic.lower() if topic else None

    relevant_pairs = []
    for pair in knowledge.get("known_server_exporter_pairs", []) or []:
        if server and pair.get("server") != server:
            continue
        relevant_pairs.append(pair)

    relevant_findings = []
    for finding in knowledge.get("known_findings", []) or []:
        if server and finding.get("server") != server:
            continue
        if normalized_topic and finding.get("category") != normalized_topic:
            continue
        relevant_findings.append(finding)

    fast_templates: list[str] = []
    if normalized_topic:
        fast_templates.extend(knowledge.get("diagnostic_fast_paths", {}).get(normalized_topic, []) or [])
    for finding in relevant_findings:
        fast_templates.extend(finding.get("recommended_templates", []) or [])

    fast_templates = list(dict.fromkeys(fast_templates))
    inventory_coverage = knowledge.get("inventory_coverage", {}) or {}
    server_coverage = None
    for row in inventory_coverage.get("server_coverage", []) or []:
        if server and row.get("server") == server:
            server_coverage = row
            break
    live_servers = [row.get("server") for row in inventory_coverage.get("server_coverage", []) or [] if row.get("server")]
    live_pairs = [
        {
            "server": row.get("server"),
            "exporter_job": exporter_job,
            "source_types": row.get("source_types", []),
            "has_active_target": row.get("has_active_target"),
            "series_count": row.get("series_count"),
        }
        for row in inventory_coverage.get("server_coverage", []) or []
        for exporter_job in row.get("exporter_jobs", []) or []
    ]

    return {
        "status": "success",
        "summary": {
            "how_to_use": (
                "Read this context first in new chats. Use known server/job mappings directly and skip repeated "
                "environment confirmation unless the user asks for freshness/target availability."
            ),
            "server": server,
            "topic": topic,
            "server_label": knowledge.get("semantics", {}).get("server"),
            "exporter_job_label": knowledge.get("semantics", {}).get("exporter_job"),
            "fast_templates": fast_templates,
            "coverage_refreshed_at": inventory_coverage.get("refreshed_at"),
            "server_coverage": server_coverage,
            "live_server_count": len(live_servers),
        },
        "data": {
            "semantics": knowledge.get("semantics", {}),
            "known_servers": knowledge.get("known_servers", []),
            "known_exporter_jobs": knowledge.get("known_exporter_jobs", []),
            "relevant_server_exporter_pairs": relevant_pairs,
            "inventory_coverage": inventory_coverage,
            "live_servers": live_servers,
            "live_server_exporter_pairs": live_pairs,
            "diagnostic_fast_paths": knowledge.get("diagnostic_fast_paths", {}),
            "relevant_findings": relevant_findings,
            "recent_analysis_history": filter_analysis_history(
                knowledge,
                server=server,
                category=normalized_topic,
                limit=10,
            ),
        },
    }


def _merge_list(first: list[Any], second: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*first, *second]:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged
