from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DURATION_RE = re.compile(r"^\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y)$")


@dataclass(frozen=True)
class TemplateVariable:
    description: str
    required: bool = False
    default: str | int | float | None = None
    label: str | None = None
    kind: str = "string"

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "label": self.label,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class QueryTemplate:
    id: str
    category: str
    exporter_job: str
    description: str
    query: str
    variables: dict[str, TemplateVariable]
    unit: str
    query_type: str = "instant"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "exporter_job": self.exporter_job,
            "description": self.description,
            "query": self.query,
            "variables": {key: value.to_dict() for key, value in self.variables.items()},
            "unit": self.unit,
            "query_type": self.query_type,
        }


SERVER_VARIABLE = TemplateVariable(
    description='服务器名，对应 Prometheus label：instance，例如 server="your-server" 会渲染为 instance="your-server"。',
    label="instance",
)
WINDOW_VARIABLE = TemplateVariable(
    description='PromQL rate/irate 使用的时间窗口，例如 "5m"、"1m"、"1h"。',
    default="5m",
    kind="duration",
)
MOUNTPOINT_VARIABLE = TemplateVariable(
    description='Linux 挂载点，对应 label：mountpoint，例如 "/"、"/data1"。',
    label="mountpoint",
)
DEVICE_VARIABLE = TemplateVariable(
    description='设备名，对应 label：device，例如 "eth0"、"nvme0n1"。',
    label="device",
)
SERVICE_VARIABLE = TemplateVariable(
    description='JVM 业务服务名，对应 label：service，例如 "your-service"。',
    label="service",
)
REQUIRED_SERVICE_VARIABLE = TemplateVariable(
    description='JVM 业务服务名，对应 label：service，例如 "your-service"。',
    required=True,
    label="service",
)
TOPK_VARIABLE = TemplateVariable(
    description="返回 Top N 条结果。",
    default=10,
    kind="int",
)

LINUX_FS_FILTER = 'fstype!~"tmpfs|fuse.lxcfs|squashfs|vfat|iso9660|configfs|autofs|gpfs|vboxsf|nfs|smb",mountpoint!~"/boot.*|/var/lib/docker/.*"'


QUERY_TEMPLATES: dict[str, QueryTemplate] = {
    "linux_cpu_usage_percent": QueryTemplate(
        id="linux_cpu_usage_percent",
        category="linux",
        exporter_job="node_exporter",
        description="Linux CPU 使用率百分比，server 对应 instance。",
        query='100 - (avg by (instance) (rate(node_cpu_seconds_total{{selector},mode="idle"}[[window]])) * 100)',
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="percent",
    ),
    "linux_load_average": QueryTemplate(
        id="linux_load_average",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 1/5/15 分钟 load average。",
        query='label_replace(node_load1{{selector}}, "load", "1m", "instance", ".*") or label_replace(node_load5{{selector}}, "load", "5m", "instance", ".*") or label_replace(node_load15{{selector}}, "load", "15m", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="count",
    ),
    "linux_memory_overview": QueryTemplate(
        id="linux_memory_overview",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 内存总量、可用量、已用量和使用率。",
        query='label_replace(node_memory_MemTotal_bytes{{selector}}, "metric", "total_bytes", "instance", ".*") or label_replace(node_memory_MemAvailable_bytes{{selector}}, "metric", "available_bytes", "instance", ".*") or label_replace(node_memory_MemTotal_bytes{{selector}} - node_memory_MemAvailable_bytes{{selector}}, "metric", "used_bytes", "instance", ".*") or label_replace((1 - node_memory_MemAvailable_bytes{{selector}} / node_memory_MemTotal_bytes{{selector}}) * 100, "metric", "usage_percent", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="mixed",
    ),
    "linux_swap_usage": QueryTemplate(
        id="linux_swap_usage",
        category="linux",
        exporter_job="node_exporter",
        description="Linux swap 总量、空闲量、已用量和使用率。",
        query='label_replace(node_memory_SwapTotal_bytes{{selector}}, "metric", "total_bytes", "instance", ".*") or label_replace(node_memory_SwapFree_bytes{{selector}}, "metric", "free_bytes", "instance", ".*") or label_replace(node_memory_SwapTotal_bytes{{selector}} - node_memory_SwapFree_bytes{{selector}}, "metric", "used_bytes", "instance", ".*") or label_replace((1 - node_memory_SwapFree_bytes{{selector}} / node_memory_SwapTotal_bytes{{selector}}) * 100, "metric", "usage_percent", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="mixed",
    ),
    "linux_filesystem_usage": QueryTemplate(
        id="linux_filesystem_usage",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 文件系统总量、可用量、已用量和使用率，可按 mountpoint/device 过滤。",
        query=f'label_replace(node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "total_bytes", "mountpoint", ".*") or label_replace(node_filesystem_avail_bytes{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "available_bytes", "mountpoint", ".*") or label_replace(node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}} - node_filesystem_avail_bytes{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "used_bytes", "mountpoint", ".*") or label_replace((node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}} - node_filesystem_avail_bytes{{{{selector}},{LINUX_FS_FILTER}}}) / node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}} * 100, "metric", "usage_percent", "mountpoint", ".*")',
        variables={"server": SERVER_VARIABLE, "mountpoint": MOUNTPOINT_VARIABLE, "device": DEVICE_VARIABLE},
        unit="mixed",
    ),
    "linux_filesystem_usage_percent_top": QueryTemplate(
        id="linux_filesystem_usage_percent_top",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 磁盘使用率 Top N。",
        query=f'topk([[topk]], (node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}} - node_filesystem_avail_bytes{{{{selector}},{LINUX_FS_FILTER}}}) / node_filesystem_size_bytes{{{{selector}},{LINUX_FS_FILTER}}} * 100)',
        variables={"server": SERVER_VARIABLE, "topk": TOPK_VARIABLE},
        unit="percent",
    ),
    "linux_inode_usage": QueryTemplate(
        id="linux_inode_usage",
        category="linux",
        exporter_job="node_exporter",
        description="Linux inode 总量、可用量、已用量和使用率。",
        query=f'label_replace(node_filesystem_files{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "total_inodes", "mountpoint", ".*") or label_replace(node_filesystem_files_free{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "free_inodes", "mountpoint", ".*") or label_replace(node_filesystem_files{{{{selector}},{LINUX_FS_FILTER}}} - node_filesystem_files_free{{{{selector}},{LINUX_FS_FILTER}}}, "metric", "used_inodes", "mountpoint", ".*") or label_replace((1 - node_filesystem_files_free{{{{selector}},{LINUX_FS_FILTER}}} / node_filesystem_files{{{{selector}},{LINUX_FS_FILTER}}}) * 100, "metric", "usage_percent", "mountpoint", ".*")',
        variables={"server": SERVER_VARIABLE, "mountpoint": MOUNTPOINT_VARIABLE, "device": DEVICE_VARIABLE},
        unit="mixed",
    ),
    "linux_disk_io_bytes_rate": QueryTemplate(
        id="linux_disk_io_bytes_rate",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 磁盘读写字节速率。",
        query='label_replace(rate(node_disk_read_bytes_total{{selector},device!~"^(ram|loop|fd).*"}[[window]]), "direction", "read", "device", ".*") or label_replace(rate(node_disk_written_bytes_total{{selector},device!~"^(ram|loop|fd).*"}[[window]]), "direction", "write", "device", ".*")',
        variables={"server": SERVER_VARIABLE, "device": DEVICE_VARIABLE, "window": WINDOW_VARIABLE},
        unit="bytes_per_second",
    ),
    "linux_network_io_bytes_rate": QueryTemplate(
        id="linux_network_io_bytes_rate",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 网卡收发字节速率，默认排除 lo、docker、veth、br 网卡。",
        query='label_replace(rate(node_network_receive_bytes_total{{selector},device!~"^(lo|docker.*|veth.*|br-.*)$"}[[window]]), "direction", "receive", "device", ".*") or label_replace(rate(node_network_transmit_bytes_total{{selector},device!~"^(lo|docker.*|veth.*|br-.*)$"}[[window]]), "direction", "transmit", "device", ".*")',
        variables={"server": SERVER_VARIABLE, "device": DEVICE_VARIABLE, "window": WINDOW_VARIABLE},
        unit="bytes_per_second",
    ),
    "linux_process_count": QueryTemplate(
        id="linux_process_count",
        category="linux",
        exporter_job="node_exporter",
        description="Linux 运行中和阻塞进程数量。",
        query='label_replace(node_procs_running{{selector}}, "metric", "running", "instance", ".*") or label_replace(node_procs_blocked{{selector}}, "metric", "blocked", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="count",
    ),
    "jvm_memory_overview": QueryTemplate(
        id="jvm_memory_overview",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM heap/nonheap 内存 used、max 和使用率。",
        query='label_replace(jvm_memory_used_bytes{{selector}}, "metric", "used_bytes", "area", ".*") or label_replace(jvm_memory_max_bytes{{selector}}, "metric", "max_bytes", "area", ".*") or label_replace(jvm_memory_used_bytes{{selector}} / jvm_memory_max_bytes{{selector}} * 100, "metric", "usage_percent", "area", ".*")',
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE},
        unit="mixed",
    ),
    "jvm_heap_usage_by_service": QueryTemplate(
        id="jvm_heap_usage_by_service",
        category="jvm",
        exporter_job="jvm_exporter",
        description="按 JVM service 查看 heap 使用率。",
        query='jvm_memory_used_bytes{{selector},area="heap"} / jvm_memory_max_bytes{{selector},area="heap"} * 100',
        variables={"server": SERVER_VARIABLE, "service": REQUIRED_SERVICE_VARIABLE},
        unit="percent",
    ),
    "jvm_memory_pool_usage": QueryTemplate(
        id="jvm_memory_pool_usage",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM 各 memory pool used、max 和使用率。",
        query='label_replace(jvm_memory_pool_used_bytes{{selector}}, "metric", "used_bytes", "pool", ".*") or label_replace(jvm_memory_pool_max_bytes{{selector}}, "metric", "max_bytes", "pool", ".*") or label_replace(jvm_memory_pool_used_bytes{{selector}} / jvm_memory_pool_max_bytes{{selector}} * 100, "metric", "usage_percent", "pool", ".*")',
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE},
        unit="mixed",
    ),
    "jvm_gc_rate": QueryTemplate(
        id="jvm_gc_rate",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM GC 次数速率。",
        query="rate(jvm_gc_collection_seconds_count{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE, "window": WINDOW_VARIABLE},
        unit="count_per_second",
    ),
    "jvm_gc_time_rate": QueryTemplate(
        id="jvm_gc_time_rate",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM GC 耗时速率。",
        query="rate(jvm_gc_collection_seconds_sum{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE, "window": WINDOW_VARIABLE},
        unit="seconds_per_second",
    ),
    "jvm_threads": QueryTemplate(
        id="jvm_threads",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM 当前线程、daemon、peak 和 deadlocked 数。",
        query='label_replace(jvm_threads_current{{selector}}, "metric", "current", "instance", ".*") or label_replace(jvm_threads_daemon{{selector}}, "metric", "daemon", "instance", ".*") or label_replace(jvm_threads_peak{{selector}}, "metric", "peak", "instance", ".*") or label_replace(jvm_threads_deadlocked{{selector}}, "metric", "deadlocked", "instance", ".*")',
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE},
        unit="count",
    ),
    "jvm_threads_state": QueryTemplate(
        id="jvm_threads_state",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM 线程状态分布。",
        query="jvm_threads_state{{selector}}",
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE},
        unit="count",
    ),
    "jvm_classes": QueryTemplate(
        id="jvm_classes",
        category="jvm",
        exporter_job="jvm_exporter",
        description="JVM class 当前加载数、加载速率和卸载速率。",
        query='label_replace(jvm_classes_currently_loaded{{selector}}, "metric", "currently_loaded", "instance", ".*") or label_replace(rate(jvm_classes_loaded_total{{selector}}[[window]]), "metric", "loaded_per_second", "instance", ".*") or label_replace(rate(jvm_classes_unloaded_total{{selector}}[[window]]), "metric", "unloaded_per_second", "instance", ".*")',
        variables={"server": SERVER_VARIABLE, "service": SERVICE_VARIABLE, "window": WINDOW_VARIABLE},
        unit="mixed",
    ),
    "mysql_uptime": QueryTemplate(
        id="mysql_uptime",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 运行时间。",
        query="mysql_global_status_uptime{{selector}}",
        variables={"server": SERVER_VARIABLE},
        unit="seconds",
    ),
    "mysql_connections": QueryTemplate(
        id="mysql_connections",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 当前连接、运行线程、最大连接和连接使用率。",
        query='label_replace(mysql_global_status_threads_connected{{selector}}, "metric", "threads_connected", "instance", ".*") or label_replace(mysql_global_status_threads_running{{selector}}, "metric", "threads_running", "instance", ".*") or label_replace(mysql_global_variables_max_connections{{selector}}, "metric", "max_connections", "instance", ".*") or label_replace(mysql_global_status_threads_connected{{selector}} / mysql_global_variables_max_connections{{selector}} * 100, "metric", "connection_usage_percent", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="mixed",
    ),
    "mysql_qps": QueryTemplate(
        id="mysql_qps",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL QPS。",
        query="rate(mysql_global_status_queries{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="queries_per_second",
    ),
    "mysql_slow_queries_rate": QueryTemplate(
        id="mysql_slow_queries_rate",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 慢查询速率。",
        query="rate(mysql_global_status_slow_queries{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="count_per_second",
    ),
    "mysql_aborted_connects_rate": QueryTemplate(
        id="mysql_aborted_connects_rate",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 异常连接速率。",
        query="rate(mysql_global_status_aborted_connects{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="count_per_second",
    ),
    "mysql_network_bytes_rate": QueryTemplate(
        id="mysql_network_bytes_rate",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 网络收发字节速率。",
        query='label_replace(rate(mysql_global_status_bytes_received{{selector}}[[window]]), "direction", "received", "instance", ".*") or label_replace(rate(mysql_global_status_bytes_sent{{selector}}[[window]]), "direction", "sent", "instance", ".*")',
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="bytes_per_second",
    ),
    "mysql_innodb_buffer_pool_usage": QueryTemplate(
        id="mysql_innodb_buffer_pool_usage",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL InnoDB buffer pool 数据量、脏页数据量、配置总量和使用率。",
        query='label_replace(mysql_global_status_innodb_buffer_pool_bytes_data{{selector}}, "metric", "data_bytes", "instance", ".*") or label_replace(mysql_global_status_innodb_buffer_pool_bytes_dirty{{selector}}, "metric", "dirty_bytes", "instance", ".*") or label_replace(mysql_global_variables_innodb_buffer_pool_size{{selector}}, "metric", "pool_size_bytes", "instance", ".*") or label_replace(mysql_global_status_innodb_buffer_pool_bytes_data{{selector}} / mysql_global_variables_innodb_buffer_pool_size{{selector}} * 100, "metric", "usage_percent", "instance", ".*")',
        variables={"server": SERVER_VARIABLE},
        unit="mixed",
    ),
    "mysql_innodb_buffer_pool_hit_ratio": QueryTemplate(
        id="mysql_innodb_buffer_pool_hit_ratio",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL InnoDB buffer pool 命中率。",
        query='(1 - rate(mysql_global_status_innodb_buffer_pool_reads{{selector}}[[window]]) / rate(mysql_global_status_innodb_buffer_pool_read_requests{{selector}}[[window]])) * 100',
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="percent",
    ),
    "mysql_tmp_disk_table_ratio": QueryTemplate(
        id="mysql_tmp_disk_table_ratio",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 临时磁盘表比例。",
        query="rate(mysql_global_status_created_tmp_disk_tables{{selector}}[[window]]) / rate(mysql_global_status_created_tmp_tables{{selector}}[[window]]) * 100",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="percent",
    ),
    "mysql_table_lock_wait_rate": QueryTemplate(
        id="mysql_table_lock_wait_rate",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL 表锁等待速率。",
        query="rate(mysql_global_status_table_locks_waited{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="count_per_second",
    ),
    "mysql_row_ops_rate": QueryTemplate(
        id="mysql_row_ops_rate",
        category="mysql",
        exporter_job="mysqld_exporter",
        description="MySQL InnoDB 行操作速率。",
        query="rate(mysql_global_status_innodb_row_ops_total{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="count_per_second",
    ),
    "prometheus_tsdb_head_series": QueryTemplate(
        id="prometheus_tsdb_head_series",
        category="prometheus",
        exporter_job="prometheus",
        description="Prometheus 当前 head series 数。",
        query="prometheus_tsdb_head_series{{selector}}",
        variables={"server": SERVER_VARIABLE},
        unit="count",
    ),
    "prometheus_query_rate": QueryTemplate(
        id="prometheus_query_rate",
        category="prometheus",
        exporter_job="prometheus",
        description="Prometheus HTTP 请求速率。",
        query="sum by (handler) (rate(prometheus_http_requests_total{{selector}}[[window]]))",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="requests_per_second",
    ),
    "prometheus_rule_eval_duration": QueryTemplate(
        id="prometheus_rule_eval_duration",
        category="prometheus",
        exporter_job="prometheus",
        description="Prometheus 规则平均评估耗时。",
        query="rate(prometheus_rule_evaluation_duration_seconds_sum{{selector}}[[window]]) / rate(prometheus_rule_evaluation_duration_seconds_count{{selector}}[[window]])",
        variables={"server": SERVER_VARIABLE, "window": WINDOW_VARIABLE},
        unit="seconds",
    ),
    "prometheus_scrape_samples": QueryTemplate(
        id="prometheus_scrape_samples",
        category="prometheus",
        exporter_job="prometheus",
        description="Prometheus 每次 scrape 采集样本数。",
        query="scrape_samples_scraped",
        variables={},
        unit="count",
    ),
    "prometheus_target_health": QueryTemplate(
        id="prometheus_target_health",
        category="prometheus",
        exporter_job="prometheus",
        description="Prometheus target 健康状态，1 表示 up，0 表示 down。",
        query="up",
        variables={},
        unit="boolean",
    ),
}


def promql_escape(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def label_matcher(label: str, value: Any) -> str:
    return f'{label}="{promql_escape(value)}"'


def normalize_template_variables(
    template: QueryTemplate,
    server: str | None,
    variables: dict[str, Any] | None,
) -> dict[str, Any]:
    values = dict(variables or {})
    if server is not None:
        values["server"] = server
    if "exporter_job" not in values:
        values["exporter_job"] = template.exporter_job

    for name, variable in template.variables.items():
        if name not in values and variable.default is not None:
            values[name] = variable.default
        if variable.required and (name not in values or values[name] in {None, ""}):
            raise ValueError(f"missing required variable: {name}")
        if name in values and values[name] not in {None, ""}:
            if variable.kind == "duration" and not DURATION_RE.match(str(values[name])):
                raise ValueError(f"invalid duration variable {name}: {values[name]!r}")
            if variable.kind == "int":
                try:
                    values[name] = int(values[name])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid integer variable {name}: {values[name]!r}") from exc
                if values[name] < 1:
                    raise ValueError(f"integer variable {name} must be greater than zero")

    return values


def render_query_template(
    template_id: str,
    server: str | None = None,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    template = QUERY_TEMPLATES.get(template_id)
    if template is None:
        return {
            "status": "error",
            "errorType": "not_found",
            "error": f"unknown query template: {template_id}",
            "template_id": template_id,
        }

    try:
        values = normalize_template_variables(template, server, variables)
    except ValueError as exc:
        return {
            "status": "error",
            "errorType": "validation",
            "error": str(exc),
            "template_id": template_id,
        }

    matchers = [label_matcher("job", values["exporter_job"])]
    for name, variable in template.variables.items():
        if variable.label and name in values and values[name] not in {None, ""}:
            matchers.append(label_matcher(variable.label, values[name]))

    selector = ",".join(matchers)
    query = template.query.replace("{selector}", selector)
    for name, value in values.items():
        variable = template.variables.get(name)
        replacement = f"[{value}]" if variable and variable.kind == "duration" else str(value)
        query = query.replace(f"[[{name}]]", replacement)

    return {
        "status": "success",
        "data": {
            **template.to_dict(),
            "rendered_query": query,
            "resolved_variables": values,
            "semantics": {
                "server": 'server 参数映射 Prometheus label instance，例如 server="your-server" 等价于 instance="your-server"。',
                "exporter_job": 'exporter_job 映射 Prometheus label job，例如 node_exporter、jvm_exporter、mysqld_exporter。',
            },
        },
    }


def list_query_templates(category: str | None = None, exporter_job: str | None = None) -> list[dict[str, Any]]:
    templates = []
    for template in QUERY_TEMPLATES.values():
        if category and template.category != category:
            continue
        if exporter_job and template.exporter_job != exporter_job:
            continue
        templates.append(template.to_dict())
    return sorted(templates, key=lambda item: (item["category"], item["id"]))


def get_query_template(template_id: str) -> dict[str, Any] | None:
    template = QUERY_TEMPLATES.get(template_id)
    return template.to_dict() if template else None
