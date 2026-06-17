# Prometheus MCP 服务

这是一个只读的 Prometheus MCP Server，用于查询 Prometheus 指标、告警、采集目标、规则、元数据、运维健康摘要，并提供常用 PromQL 模板。

本项目约定：

- `server` 参数对应 Prometheus label `instance`，表示服务器。
- `exporter_job` 参数对应 Prometheus label `job`，表示部署的 exporter 或采集来源，例如 `node_exporter`、`jvm_exporter`、`mysqld_exporter`。
- JVM 业务服务名使用 label `service`。

## 配置

复制示例配置：

```powershell
Copy-Item .env.example .env
```

然后手动修改 `.env`。不要把真实 Prometheus 地址、token、内网主机名提交到仓库。

```ini
PROMETHEUS_BASE_URL=http://prometheus.example.com:9090
PROMETHEUS_TIMEOUT_SECONDS=20
PROMETHEUS_MAX_RANGE_SECONDS=604800
PROMETHEUS_MAX_RANGE_POINTS=11000

MCP_TRANSPORT=stdio
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp

MCP_AUTH_TOKEN=change-me
MCP_AUTH_ISSUER_URL=
MCP_AUTH_RESOURCE_URL=
```

读取优先级：命令行参数优先，其次系统环境变量，其次 `.env`，最后代码默认值。

认证说明：

- `stdio` 模式不需要 HTTP Authorization header。
- `streamable-http` 模式配置 `MCP_AUTH_TOKEN` 后，会要求请求带 `Authorization: Bearer <token>`。
- `MCP_AUTH_TOKEN` 必须在本地 `.env` 或系统环境变量中手动配置；`.env.example` 只能放占位值。
- `MCP_AUTH_ISSUER_URL` 和 `MCP_AUTH_RESOURCE_URL` 通常可以留空，服务会按监听地址自动生成 OAuth metadata URL。

## 安装

```powershell
python -m pip install -e .[test]
```

## 启动方式

默认按 `.env` 启动：

```powershell
python run.py
```

显式使用 `stdio`：

```powershell
python run.py --transport stdio
```

使用 `streamable-http`：

```powershell
python run.py --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

HTTP MCP 地址示例：

```text
http://127.0.0.1:8000/mcp
```

HTTP 客户端需要带认证头：

```http
Authorization: Bearer <your-token>
```

## MCP Tools

基础查询：

- `prometheus_query`：执行即时 PromQL 查询。
- `prometheus_query_range`：执行区间 PromQL 查询。
- `prometheus_list_metrics`：列出指标名，支持按前缀过滤和分页。
- `prometheus_metric_metadata`：查询指标类型、说明和单位。
- `prometheus_label_values`：查询 label 可选值。
- `prometheus_targets`：查询 active/dropped targets。
- `prometheus_alerts`：查询当前告警。
- `prometheus_rules`：查询告警规则和 recording rules。
- `prometheus_status`：查询 build、runtime、TSDB 和可选 config 状态。
- `prometheus_health_summary`：返回运维健康摘要。
- `prometheus_inventory`：返回服务器、exporter job、server/job 组合、JVM services 和挂载点。
- `prometheus_monitoring_coverage`：判断 server/job 覆盖面与数据来源类型，包括 `active_target`、`pushgateway_data`、`historical_series`、`stale_unknown`。
- `prometheus_refresh_context`：从当前 Prometheus live inventory 刷新 `prometheus_knowledge.json`，让新窗口直接复用最新 server/job 映射和覆盖面。
- `prometheus_context`：读取已沉淀的环境上下文、常用诊断路径和历史结论。
- `prometheus_analysis_history`：查看已记录的问题分析历史。
- `prometheus_remember_analysis`：把本次分析结论写入本地知识库，供后续新窗口复用。

诊断聚合：

- `prometheus_server_health`：按服务器聚合 CPU、内存、load、磁盘 Top、网络、磁盘 IO、告警和数据新鲜度。
- `prometheus_mysql_health`：按服务器聚合 MySQL 连接、QPS、慢查询、异常连接、锁、临时表、Buffer Pool、行操作和主机资源压力。
- `prometheus_jvm_health`：按服务器和可选 `service` 聚合 JVM heap/nonheap、memory pool、GC、线程、deadlock 和主机资源压力。
- `prometheus_disk_health`：按服务器和可选 `mountpoint` 聚合容量、剩余空间、inode、IO、告警、1 小时趋势和预计写满时间。

PromQL 模板：

- `prometheus_list_query_templates`：列出模板，可按 `category` 或 `exporter_job` 过滤。
- `prometheus_get_query_template`：查看单个模板。
- `prometheus_render_query_template`：只渲染 PromQL，不执行。
- `prometheus_run_query_template`：渲染并执行模板。

## MCP Resources

- `prometheus://metrics/all`
- `prometheus://metrics/by-prefix`
- `prometheus://metrics/by-prefix/{prefix}`
- `prometheus://targets`
- `prometheus://alerts`
- `prometheus://rules`
- `prometheus://status`
- `prometheus://inventory`
- `prometheus://monitoring-coverage`
- `prometheus://context`
- `prometheus://context/{server}`
- `prometheus://analysis-history`
- `prometheus://templates`
- `prometheus://templates/{template_id}`

## 新窗口快速进入上下文

为了避免每次新对话都重新确认 `instance/job/exporter`，服务内置本地知识库：

```text
prometheus_knowledge.json
```

新窗口可以先调用：

```json
{
  "tool": "prometheus_context",
  "arguments": {
    "server": "your-server",
    "topic": "mysql"
  }
}
```

它会返回：

- `server` 与 `instance` 的映射规则。
- `exporter_job` 与 `job` 的映射规则。
- 已知服务器和 exporter 组合。
- 最近一次 `prometheus_refresh_context` 得到的覆盖面和数据新鲜度。
- 针对 MySQL/Linux/JVM/磁盘的推荐模板列表。
- 已沉淀的诊断结论。

定期刷新上下文：

```json
{
  "tool": "prometheus_refresh_context",
  "arguments": {}
}
```

它会把 live inventory 写回 `prometheus_knowledge.json`，包括当前服务器、exporter、server/job 组合、数据来源类型和缺失 exporter 判断。

## 诊断工具调用示例

查看服务器整体状态：

```json
{
  "server": "your-server"
}
```

对应 tool：`prometheus_server_health`。

查看 MySQL 当前情况：

```json
{
  "server": "your-db-server"
}
```

对应 tool：`prometheus_mysql_health`。

查看 JVM 指定服务：

```json
{
  "server": "your-app-server",
  "service": "your-service"
}
```

对应 tool：`prometheus_jvm_health`。

查看指定挂载点磁盘：

```json
{
  "server": "your-server",
  "mountpoint": "/data"
}
```

对应 tool：`prometheus_disk_health`。

诊断工具统一返回：

- `summary`：风险等级、简短结论、生成时间。
- `findings`：阈值命中、告警、覆盖面/新鲜度问题。
- `metrics`：已归一化的关键指标，例如 GiB、TiB、MiB/s、qps、百分比。
- `recommended_next_queries`：需要继续深挖时的推荐模板。
- `raw`：保留压缩后的原始 Prometheus 查询信息，便于追踪。

## PromQL 模板分类

Linux / `node_exporter`：

- `linux_cpu_usage_percent`
- `linux_load_average`
- `linux_memory_overview`
- `linux_swap_usage`
- `linux_filesystem_usage`
- `linux_filesystem_usage_percent_top`
- `linux_inode_usage`
- `linux_disk_io_bytes_rate`
- `linux_network_io_bytes_rate`
- `linux_process_count`

JVM / `jvm_exporter`：

- `jvm_memory_overview`
- `jvm_heap_usage_by_service`
- `jvm_memory_pool_usage`
- `jvm_gc_rate`
- `jvm_gc_time_rate`
- `jvm_threads`
- `jvm_threads_state`
- `jvm_classes`

MySQL / `mysqld_exporter`：

- `mysql_uptime`
- `mysql_connections`
- `mysql_qps`
- `mysql_slow_queries_rate`
- `mysql_aborted_connects_rate`
- `mysql_network_bytes_rate`
- `mysql_innodb_buffer_pool_usage`
- `mysql_innodb_buffer_pool_hit_ratio`
- `mysql_tmp_disk_table_ratio`
- `mysql_table_lock_wait_rate`
- `mysql_row_ops_rate`

Prometheus 自身：

- `prometheus_tsdb_head_series`
- `prometheus_query_rate`
- `prometheus_rule_eval_duration`
- `prometheus_scrape_samples`
- `prometheus_target_health`

## 模板调用示例

Linux 内存概览：

```json
{
  "template_id": "linux_memory_overview",
  "server": "your-server"
}
```

Linux 指定挂载点磁盘使用：

```json
{
  "template_id": "linux_filesystem_usage",
  "server": "your-server",
  "variables": {
    "mountpoint": "/data"
  }
}
```

JVM 指定服务堆内存使用率：

```json
{
  "template_id": "jvm_heap_usage_by_service",
  "server": "your-app-server",
  "variables": {
    "service": "your-service"
  }
}
```

MySQL 连接信息：

```json
{
  "template_id": "mysql_connections",
  "server": "your-db-server"
}
```

## 测试

```powershell
python -m pytest -q
```

## 安全边界

服务只读，只生成和执行 PromQL，不修改 Prometheus 配置，不写入 Pushgateway，不创建或删除 Alertmanager 静默，也不删除任何监控数据。
