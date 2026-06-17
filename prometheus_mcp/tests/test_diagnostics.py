from __future__ import annotations

import pytest

from prometheus_mcp.client import PrometheusClient
from prometheus_mcp.diagnostics import build_coverage_report, classify_pair, human_bytes, human_rate


def vector_result(samples: list[tuple[dict, float]]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": labels, "value": [1, str(value)]}
                for labels, value in samples
            ],
        },
        "summary": {"result_count": len(samples), "result_type": "vector"},
    }


def template_payload(template_id: str, samples: list[tuple[dict, float]]) -> dict:
    return {
        "status": "success",
        "template": {"id": template_id},
        "query_result": vector_result(samples),
        "summary": {"template_id": template_id, "result_count": len(samples)},
    }


def test_human_bytes_and_rates() -> None:
    assert human_bytes(1024**3)["text"] == "1.00 GiB"
    assert human_rate(5 * 1024 * 1024, "bytes")["text"] == "5.00 MiB/s"
    assert human_rate(123.456, "qps")["text"] == "123.46 qps"


def test_classify_pair_source_types() -> None:
    assert (
        classify_pair(
            {"server": "db-server-1", "exporter_job": "mysqld_exporter", "series_count": 10},
            active_pairs={("db-server-1", "mysqld_exporter")},
        )["source_type"]
        == "active_target"
    )
    assert classify_pair({"server": "pgw", "exporter_job": "pushgateway_data", "series_count": 1})["source_type"] == "pushgateway_data"
    assert classify_pair({"server": "old", "exporter_job": "node_exporter", "series_count": 1})["source_type"] == "historical_series"
    assert classify_pair({"server": "unknown", "exporter_job": "node_exporter", "series_count": 0})["source_type"] == "stale_unknown"


def test_coverage_reports_missing_host_exporter() -> None:
    coverage = build_coverage_report(
        [{"server": "db1", "exporter_job": "mysqld_exporter", "series_count": 20}],
        {"data": {"activeTargets": []}},
    )

    assert coverage["source_type_counts"] == {"historical_series": 1}
    assert coverage["server_coverage"][0]["missing_exporters"] == ["node_exporter"]


class HealthStubClient(PrometheusClient):
    async def run_query_template(self, template_id, server=None, variables=None, start=None, end=None, step=None):
        match template_id:
            case "linux_cpu_usage_percent":
                return template_payload(template_id, [({"instance": server}, 91)])
            case "linux_memory_overview":
                return template_payload(
                    template_id,
                    [
                        ({"metric": "total_bytes", "instance": server}, 32 * 1024**3),
                        ({"metric": "available_bytes", "instance": server}, 2 * 1024**3),
                        ({"metric": "used_bytes", "instance": server}, 30 * 1024**3),
                        ({"metric": "usage_percent", "instance": server}, 93.75),
                    ],
                )
            case "linux_load_average":
                return template_payload(
                    template_id,
                    [
                        ({"load": "1m", "instance": server}, 6),
                        ({"load": "5m", "instance": server}, 5),
                        ({"load": "15m", "instance": server}, 4),
                    ],
                )
            case "linux_filesystem_usage_percent_top":
                return template_payload(template_id, [({"mountpoint": "/data1", "instance": server}, 96)])
            case "linux_disk_io_bytes_rate":
                return template_payload(template_id, [({"direction": "read", "device": "nvme0n1"}, 20 * 1024**2)])
            case "linux_network_io_bytes_rate":
                return template_payload(template_id, [({"direction": "receive", "device": "eth0"}, 1024**2)])
            case "linux_process_count":
                return template_payload(template_id, [({"metric": "running"}, 3), ({"metric": "blocked"}, 0)])
            case "linux_filesystem_usage":
                return template_payload(
                    template_id,
                    [
                        ({"metric": "total_bytes", "mountpoint": "/data1", "device": "nvme0n1"}, 100 * 1024**3),
                        ({"metric": "available_bytes", "mountpoint": "/data1", "device": "nvme0n1"}, 4 * 1024**3),
                        ({"metric": "used_bytes", "mountpoint": "/data1", "device": "nvme0n1"}, 96 * 1024**3),
                        ({"metric": "usage_percent", "mountpoint": "/data1", "device": "nvme0n1"}, 96),
                    ],
                )
            case "linux_inode_usage":
                return template_payload(
                    template_id,
                    [
                        ({"metric": "total_inodes", "mountpoint": "/data1", "device": "nvme0n1"}, 1000),
                        ({"metric": "free_inodes", "mountpoint": "/data1", "device": "nvme0n1"}, 700),
                        ({"metric": "used_inodes", "mountpoint": "/data1", "device": "nvme0n1"}, 300),
                        ({"metric": "usage_percent", "mountpoint": "/data1", "device": "nvme0n1"}, 30),
                    ],
                )
            case "mysql_connections":
                return template_payload(
                    template_id,
                    [
                        ({"metric": "threads_connected"}, 189),
                        ({"metric": "threads_running"}, 12),
                        ({"metric": "max_connections"}, 1000),
                        ({"metric": "connection_usage_percent"}, 18.9),
                    ],
                )
            case "mysql_qps":
                return template_payload(template_id, [({"instance": server}, 276)])
            case "mysql_slow_queries_rate" | "mysql_aborted_connects_rate" | "mysql_table_lock_wait_rate":
                return template_payload(template_id, [({"instance": server}, 0)])
            case "mysql_network_bytes_rate":
                return template_payload(template_id, [({"direction": "sent"}, 1024**2)])
            case "mysql_innodb_buffer_pool_usage":
                return template_payload(
                    template_id,
                    [
                        ({"metric": "data_bytes"}, 128 * 1024**2),
                        ({"metric": "dirty_bytes"}, 2 * 1024**2),
                        ({"metric": "pool_size_bytes"}, 128 * 1024**2),
                        ({"metric": "usage_percent"}, 88),
                    ],
                )
            case "mysql_innodb_buffer_pool_hit_ratio":
                return template_payload(template_id, [({"instance": server}, 97)])
            case "mysql_tmp_disk_table_ratio":
                return template_payload(template_id, [({"instance": server}, 1)])
            case "mysql_row_ops_rate":
                return template_payload(template_id, [({"operation": "read"}, 191000)])
            case "jvm_memory_overview":
                return template_payload(template_id, [({"metric": "usage_percent", "area": "heap"}, 91)])
            case "jvm_memory_pool_usage":
                return template_payload(template_id, [({"metric": "usage_percent", "pool": "Old Gen"}, 92)])
            case "jvm_gc_rate":
                return template_payload(template_id, [({"gc": "G1"}, 0.2)])
            case "jvm_gc_time_rate":
                return template_payload(template_id, [({"gc": "G1"}, 0.01)])
            case "jvm_threads":
                return template_payload(template_id, [({"metric": "current"}, 100), ({"metric": "deadlocked"}, 0)])
            case "jvm_threads_state" | "jvm_classes":
                return template_payload(template_id, [])
            case "jvm_heap_usage_by_service":
                return template_payload(template_id, [({"service": variables["service"]}, 91)])
        raise AssertionError(template_id)

    async def query(self, query, time=None, timeout=None):
        if "count(count by (cpu)" in query:
            return vector_result([({}, 8)])
        if "seconds_to_full" in query:
            return vector_result([({}, 3600)])
        if "predict_linear" in query:
            return vector_result([({}, -1)])
        return vector_result([({}, 90)])

    async def alerts(self, state=None):
        return {
            "status": "success",
            "data": {"alerts": [{"state": "firing", "labels": {"alertname": "NodeDiskUsageCritical", "severity": "critical", "instance": "linux-server-1", "mountpoint": "/data1"}}]},
            "summary": {"firing_count": 1},
        }

    async def inventory(self):
        return {
            "status": "success",
            "data": {
                "servers": ["db-server-1", "linux-server-1"],
                "exporter_jobs": ["node_exporter", "mysqld_exporter", "jvm_exporter"],
                "server_exporter_pairs": [
                    {"server": "db-server-1", "exporter_job": "node_exporter", "series_count": 10},
                    {"server": "db-server-1", "exporter_job": "mysqld_exporter", "series_count": 10},
                    {"server": "linux-server-1", "exporter_job": "node_exporter", "series_count": 10},
                ],
            },
        }

    async def targets(self, state="any"):
        return {"status": "success", "data": {"activeTargets": []}, "summary": {"active_count": 0}}


@pytest.mark.asyncio
async def test_server_health_output_structure() -> None:
    result = await HealthStubClient().server_health("db-server-1", remember=False)

    assert result["status"] == "success"
    assert result["summary"]["risk"] in {"warning", "critical"}
    assert result["data"]["metrics"]["memory"]["total"]["human"]["unit"] == "GiB"
    assert result["data"]["recommended_next_queries"]


@pytest.mark.asyncio
async def test_mysql_health_output_structure() -> None:
    result = await HealthStubClient().mysql_health("db-server-1", remember=False)

    assert result["status"] == "success"
    assert result["data"]["metrics"]["connections"]["threads_connected"] == 189
    assert result["data"]["metrics"]["query_rates"]["qps"]["human"]["unit"] == "qps"
    assert result["data"]["findings"]


@pytest.mark.asyncio
async def test_jvm_health_output_structure() -> None:
    result = await HealthStubClient().jvm_health("app-server-1", service="sample-service", remember=False)

    assert result["status"] == "success"
    assert result["summary"]["service"] == "sample-service"
    assert result["data"]["metrics"]["heap_by_service"]["raw"] == 91


@pytest.mark.asyncio
async def test_disk_health_output_structure() -> None:
    result = await HealthStubClient().disk_health("linux-server-1", mountpoint="/data1", remember=False)

    assert result["status"] == "success"
    assert result["summary"]["mountpoint"] == "/data1"
    assert result["data"]["metrics"]["filesystems"][0]["available"]["human"]["text"] == "4.00 GiB"
    assert any(item["level"] == "critical" for item in result["data"]["findings"])
