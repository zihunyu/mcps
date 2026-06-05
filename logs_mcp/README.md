### 注意事项
```
mcp功能的依赖因为最低版本的限制，最低只能使用python3.10
center和agent分别做了最低python3.6 3.7的兼容

mcp配置文件下载地址要与mcp启动的端口一致
```






# Log MCP V1

Log MCP V1 是一个轻量级日志查询系统，包含 3 个组件：

- **Log Center**：中心服务，负责服务器注册、日志注册、任务创建、任务分发和结果查询。
- **Log Agent**：部署在每台日志服务器上，只读取白名单日志文件，执行查询任务并回传结果。
- **Log MCP**：MCP 服务，给 AI Client 暴露日志查询工具，内部调用 Log Center API。

默认部署方式是：客户端只配置一个 Log MCP；每台需要查询日志的服务器部署一个 Log Agent。

```text
AI Client
  ↓
Log MCP
  ↓
Log Center API
  ↓
Log Agent
  ↓
各服务器本机日志文件
```

## 已实现功能

- Log Center 服务。
- Log Agent 服务。
- Log MCP 服务。
- Agent Token 认证。
- API Token 认证。
- 日志白名单。
- 最大读取行数限制，默认 200 行，最大 5000 行。
- 关键字过滤。
- MCP 工具调用。
- 日志下载到 MCP 本地文件，并返回临时下载 URL，避免大量日志直接进入对话上下文。
- Python 启动入口。
- 保留 Python 3.10+ 主版本，同时提供低版本兼容版 Agent 和 Center。

## MCP 工具

本服务注册 4 个 MCP 工具：

```text
list_log_servers
list_server_logs
read_log
download_log
```

查看已注册工具：

```powershell
python run.py --list-tools
```

输出应包含：

```text
Registered MCP tools:
- list_log_servers
- list_server_logs
- read_log
- download_log
```

工具说明：

- `list_log_servers()`：查询已注册服务器列表。
- `list_server_logs(server_id)`：查询指定服务器的日志列表。
- `read_log(server_id, log_name, lines=200, keyword=None)`：查询指定服务器、指定日志的最近 N 行，可选关键字过滤。
- `download_log(server_id, log_name, lines=200, keyword=None)`：查询指定日志并保存到 MCP 本地文件，只返回文件路径、临时下载 URL、过期时间、行数、文件大小等元信息，不返回日志正文。

`read_log` 会把日志正文直接返回给 MCP 客户端，日志越多，消耗 token 越多。大量日志推荐使用 `download_log`，它只下载不分析；只有你明确要求分析某个下载文件时，才读取文件内容进行分析。`download_log` 返回的 `download_url` 只有在 Log MCP 以 HTTP 可访问方式运行时才可用，例如 `streamable-http`。

## 本机完整演示

以下步骤可以在一台机器上完整跑通 Center、Agent、MCP 和 MCP 客户端调用。

### 1. 安装依赖

```powershell
python -m pip install -e .
```

### 2. 准备配置文件

```powershell
Copy-Item .\center\config.example.yaml .\center\config.yaml
Copy-Item .\agent\config.example.yaml .\agent\config.yaml
Copy-Item .\mcp\config.example.yaml .\mcp\config.yaml
```

默认示例配置已经可以本机跑通：

- Center 地址：`http://127.0.0.1:8000`
- Agent Token：`agent-token`
- API Token：`center-api-token`
- Agent 白名单日志：`./demo_logs/app.log`

### 3. 启动 Log Center

打开第一个终端：

```powershell
$env:LOG_CENTER_CONFIG = "D:\0715\git_codex\log_mcp\center\config.yaml"
python center_run.py
```

默认监听：

```text
http://127.0.0.1:8000
```

### 4. 启动 Log Agent

打开第二个终端：

```powershell
$env:LOG_AGENT_CONFIG = "D:\0715\git_codex\log_mcp\agent\config.yaml"
python agent_run.py
```

Agent 启动后会：

- 向 Center 发送心跳。
- 上报 `local-demo-01` 服务器。
- 上报 `demo-log` 日志文件。
- 每 3 秒拉取一次任务。
- 读取 `allow_logs` 中允许的日志文件并回传结果。

### 5. 调用 MCP 获取日志

打开第三个终端：

```powershell
$env:LOG_MCP_CONFIG = "D:\0715\git_codex\log_mcp\mcp\config.yaml"
python mcp_client_demo.py --server-id local-demo-01 --log-name demo-log --lines 20 --keyword ERROR
```

该脚本会通过 MCP stdio 启动 `python run.py`，然后依次调用：

- `list_log_servers`
- `list_server_logs`
- `read_log`

如果一切正常，`read_log` 会返回 `demo_logs/app.log` 中包含 `ERROR` 的日志行。

如果只想下载日志、不打印日志正文：

```powershell
$env:LOG_MCP_CONFIG = "D:\0715\git_codex\log_mcp\mcp\config.yaml"
python mcp_client_demo.py --server-id local-demo-01 --log-name demo-log --lines 20 --keyword ERROR --download
```

`download_log` 会返回类似结果：

```json
{
  "task_id": "task-001",
  "status": "finished",
  "file_path": "D:\\0715\\git_codex\\log_mcp\\downloads\\local-demo-01\\demo-log-20260605-093000-task-001.log",
  "download_url": "http://127.0.0.1:8081/downloads/临时token",
  "expires_at": "2026-06-05T10:30:00Z",
  "line_count": 2,
  "size_bytes": 64
}
```

如果当前 MCP 是 `stdio` 模式，`file_path` 仍然有效，但它表示运行 Log MCP 的机器上的路径。要让客户端通过浏览器或 `curl` 下载到自己本机，需要把 Log MCP 以 HTTP 方式启动，并确保 `download.public_base_url` 是客户端能访问到的地址。

## 直接调用 Center API

也可以不用 MCP，直接用 Center API 验证链路。

查询服务器：

```powershell
Invoke-RestMethod `
  -Headers @{ Authorization = "Bearer center-api-token" } `
  http://127.0.0.1:8000/api/log/servers
```

查询某台服务器日志列表：

```powershell
Invoke-RestMethod `
  -Headers @{ Authorization = "Bearer center-api-token" } `
  http://127.0.0.1:8000/api/log/server/local-demo-01/files
```

创建日志查询任务：

```powershell
$body = @{
  server_id = "local-demo-01"
  log_name = "demo-log"
  lines = 20
  keyword = "ERROR"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer center-api-token" } `
  -Body $body `
  http://127.0.0.1:8000/api/log/task
```

## 生产部署方式

生产环境要求 Python 3.10 或更高版本。MCP SDK 本身要求 Python 3.10+，所以在保留 MCP 功能的前提下，不能支持更低版本的 Python。
如果老服务器只能运行低版本 Python，可以使用独立兼容版：

- `agent_python3.6/` + `agent_run_python3.6.py`：支持 Python 3.6+，适合部署在老日志服务器。
- `center_python3.7/` + `center_run_python3.7.py`：支持 Python 3.7+，使用 Flask 实现同样的 Center API。
- `mcp/` + `run.py`：仍要求 Python 3.10+。

Linux 上不要直接假设 `python` 是新版本，先检查：

```bash
python --version
python3 --version
python3.10 --version
```

如果 `python` 不是 3.10+，请使用 `python3.10` 或其他 3.10+ 解释器启动和安装依赖。

### 1. 部署 Log Center

在中心服务器部署 Log Center。

配置文件：`center/config.yaml`

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  log_level: "info"
auth:
  api_token: "替换为强随机 API Token"
  agent_token: "替换为强随机 Agent Token"
limits:
  default_lines: 200
  max_lines: 5000
```

启动：

```powershell
$env:LOG_CENTER_CONFIG = "D:\deploy\log_mcp\center\config.yaml"
python center_run.py
```

Linux 示例：

```bash
export LOG_CENTER_CONFIG=/opt/log_mcp/center/config.yaml
python3.10 center_run.py
```

需要放通 Agent 和 MCP 到 Center 的网络访问，例如：

```text
http://<center-ip>:8000
```

### 2. 在每台日志服务器部署 Log Agent

每台需要被查询日志的 Linux 服务器都部署一个 Agent。

配置文件：`agent/config.yaml`

```yaml
server_id: "prod-app-01"
hostname: "prod-app-01"
ip: "10.0.0.11"
env: "prod"
center:
  base_url: "http://<center-ip>:8000"
  agent_token: "和 Center 中 auth.agent_token 一致"
  timeout_seconds: 10
allow_logs:
  - name: "app-log"
    path: "/data/logs/app/app.log"
  - name: "app-error"
    path: "/data/logs/app/error.log"
  - name: "nginx-access"
    path: "/var/log/nginx/access.log"
runtime:
  heartbeat_interval_seconds: 10
  task_poll_interval_seconds: 3
  log_level: "INFO"
```

启动：

```bash
export LOG_AGENT_CONFIG=/opt/log_mcp/agent/config.yaml
python3.10 agent_run.py
```

Agent 只能读取 `allow_logs` 中配置的日志，不能读取任意路径，也不会执行 shell。

低版本 Agent 兼容版安装依赖：

```bash
python3.6 -m pip install -r requirements_agent_python3.6.txt
```

低版本 Agent 兼容版启动：

```bash
export LOG_AGENT_CONFIG=/opt/log_mcp/agent/config.yaml
python3.6 agent_run_python3.6.py
```

兼容版 Agent 使用同一份 `agent/config.yaml` 配置格式，仍然只读取 `allow_logs` 白名单日志，HTTP 调用使用标准库 `urllib`，不依赖 MCP SDK、httpx 或 pydantic。

### 3. 部署 Log MCP

Log MCP 可以部署在 Center 同一台服务器，也可以部署在 AI Client 能访问的位置。

配置文件：`mcp/config.yaml`

```yaml
center:
  base_url: "http://<center-ip>:8000"
  api_token: "和 Center 中 auth.api_token 一致"
  timeout_seconds: 10
  poll_interval_seconds: 1
  poll_timeout_seconds: 30
mcp:
  transport: "stdio"
  host: "127.0.0.1"
  port: 8081
  log_level: "INFO"
limits:
  default_lines: 200
  max_lines: 5000
download:
  dir: "./downloads"
  public_base_url: "http://<mcp-ip>:8081"
  token_ttl_seconds: 1800
```

stdio 启动：

```powershell
$env:LOG_MCP_CONFIG = "D:\deploy\log_mcp\mcp\config.yaml"
python run.py
```

MCP 客户端配置：

```text
command: python
args: ["run.py"]
env:
  LOG_MCP_CONFIG: "D:\deploy\log_mcp\mcp\config.yaml"
```

如果使用 HTTP MCP，把 `mcp.transport` 改为 `streamable-http`，并设置：

```yaml
mcp:
  transport: "streamable-http"
  host: "0.0.0.0"
  port: 8081
auth:
  bearer_token: "替换为强随机 MCP Token"
download:
  public_base_url: "http://<mcp-ip>:8081"
```

启动后 MCP HTTP 地址：

```text
http://<mcp-ip>:8081/mcp
```

HTTP 模式建议设置 MCP 访问令牌，避免任何人都能直接调用 MCP：

```yaml
auth:
  bearer_token: "替换为强随机 MCP Token"
```

MCP HTTP 客户端调用时需要携带：

```text
Authorization: Bearer <auth.bearer_token>
```

`auth.bearer_token` 写在 `mcp/config.yaml` 中。未设置时保持旧行为，不强制鉴权，但不建议在生产环境这样暴露 HTTP MCP。
该令牌用于保护 MCP HTTP 接口，例如 `/mcp`；临时下载链接 `/downloads/<token>` 不要求额外请求头，方便浏览器或 `curl` 直接下载。

临时日志下载地址格式：

```text
http://<mcp-ip>:8081/downloads/<token>
```

`token_ttl_seconds` 默认 1800 秒，也就是 30 分钟。token 保存在 Log MCP 进程内存中，MCP 重启后旧 URL 会失效，但已下载到 `download.dir` 的文件仍会保留。

### 4. 可选：部署低版本兼容 Center

如果 Center 服务器只能使用 Python 3.7，可以使用兼容版 Center。配置文件仍使用 `center/config.yaml`。

安装依赖：

```bash
python3.7 -m pip install -r requirements_center_python3.7.txt
```

启动：

```bash
export LOG_CENTER_CONFIG=/opt/log_mcp/center/config.yaml
python3.7 center_run_python3.7.py
```

兼容版 Center 使用 Flask 实现，与主版本保持相同 API、相同 Bearer Token 认证方式。Log MCP 的 `center.base_url` 可以直接指向兼容版 Center。

## Docker Compose

当前 `docker-compose.yml` 会启动：

- `log-center`
- `log-mcp`

Agent 通常部署在每台日志服务器上，所以 Compose 默认不启动 Agent。

启动前确认 `center/config.example.yaml` 和 `mcp/config.compose.yaml` 中 token 一致，然后运行：

```powershell
Copy-Item .\center\config.example.yaml .\center\config.yaml
docker compose up --build
```

Compose 中 Log MCP 使用 HTTP 模式：

```text
http://127.0.0.1:8081/mcp
```

Compose 示例中 `download.public_base_url` 默认为：

```text
http://127.0.0.1:8081
```

如果客户端不在运行 Docker Compose 的机器上，请把 `mcp/config.compose.yaml` 中的 `download.public_base_url` 改为客户端能访问的 IP 或域名。

## 安全说明

- Center API 使用 `auth.api_token`，供 MCP 调用。
- Agent API 使用 `auth.agent_token`，供 Agent 调用。
- Agent 只读取 `allow_logs` 白名单路径。
- Agent 不执行 shell。
- MCP 不读取日志文件，只调用 Center API。
- `download_log` 只把日志保存为 MCP 本地文件，不自动分析，也不返回日志正文。
- `auth.bearer_token` 保护 MCP HTTP 接口，避免未授权客户端调用 MCP 工具。
- `download_url` 使用临时随机 token，有效期由 `download.token_ttl_seconds` 控制，默认 30 分钟；下载链接不再要求额外 Bearer 请求头。
- `download_url` 不替代权限控制，生产环境建议只在可信网络、VPN 或反向代理鉴权后暴露 Log MCP HTTP 服务。
- `lines` 最大值为 5000。

## 测试

```powershell
python -m pytest
```
