# Jenkins 发版 MCP

这是一个用 Python 编写的 Jenkins 发版 MCP Server。它通过 Jenkins Remote Access API 触发白名单内的 Jenkins Job，并提供队列查询、构建状态等待、日志读取等工具，适合给 Codex、Claude Desktop、MCP Inspector 或其他 MCP 客户端使用。

默认安全策略：

- 只能触发 `config/allowed_jobs.yml` 中声明的 Job。
- 只能提交 Job 配置中允许的参数。
- 真实发版必须显式传 `dry_run=false` 且 `confirm=true`。
- 使用 Jenkins API Token，不保存 Jenkins 登录密码。

## 1. Jenkins 前置要求

1. Jenkins 用户需要具备目标 Job 的 `Read` 和 `Build` 权限。
2. 使用 Jenkins 用户自己的 API Token，不要使用登录密码。
3. 目标 Job 如果是参数化构建，需要参数名和白名单配置一致。
4. Jenkins 启用 CSRF Protection 时，使用用户名 + API Token 的 Basic Auth 通常不需要额外 crumb。Jenkins 官方文档说明 API Token 请求会豁免 CSRF crumb。

参考文档：

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Jenkins Remote Access API](https://www.jenkins.io/doc/book/using/remote-access-api/)
- [Jenkins CSRF Protection](https://www.jenkins.io/doc/book/security/csrf-protection/)

## 2. 安装

建议先创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

开发和测试依赖：

```powershell
python -m pip install -e ".[test]"
pytest
```

## 3. 配置环境变量

复制示例文件：

```powershell
Copy-Item .env.example .env
Copy-Item config/allowed_jobs.example.yml config/allowed_jobs.yml
```

程序启动时会自动读取当前目录下的 `.env` 文件。也就是说，只要你在
`D:\path\to\jenkins_mcp\.env` 里写好了下面这些配置，通常不需要再手动执行
`$env:...`。

`.env` 示例：

```dotenv
JENKINS_URL=https://jenkins.example.com
JENKINS_USER=release-bot
JENKINS_API_TOKEN=replace-with-api-token
JENKINS_ALLOWED_JOBS_FILE=config/allowed_jobs.yml
```

如果你不想用 `.env`，也可以直接在 PowerShell 里设置环境变量：

```powershell
$env:JENKINS_URL="https://jenkins.example.com"
$env:JENKINS_USER="release-bot"
$env:JENKINS_API_TOKEN="replace-with-api-token"
$env:JENKINS_ALLOWED_JOBS_FILE="config/allowed_jobs.yml"
```

可选变量：

```powershell
$env:JENKINS_REQUEST_TIMEOUT_SECONDS="30"
$env:JENKINS_VERIFY_SSL="true"
$env:MCP_HTTP_HOST="127.0.0.1"
$env:MCP_HTTP_PORT="8000"
```

MCP HTTP 鉴权配置：

```dotenv
MCP_BEARER_TOKEN=请换成至少32位随机字符串
```

安全说明：

- `--host 127.0.0.1` 只允许本机访问，适合本机 MCP 客户端。
- 如果你改成 `--host 0.0.0.0`，或者把端口暴露到局域网/公网，必须配置 `MCP_BEARER_TOKEN`。
- 配置 token 后，客户端请求 `/mcp` 必须带 `Authorization: Bearer <token>`。

QQ 邮箱通知可选配置：

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ邮箱@qq.com
SMTP_PASSWORD=QQ邮箱SMTP授权码
SMTP_FROM=你的QQ邮箱@qq.com
SMTP_TO=接收人1@example.com,接收人2@example.com
SMTP_USE_SSL=true
```

注意：`SMTP_PASSWORD` 填 QQ 邮箱的 SMTP 授权码，不是 QQ 登录密码。

如果你的配置文件不叫 `.env`，可以这样指定：

```powershell
$env:JENKINS_ENV_FILE="D:\path\to\jenkins_mcp\.env.prod"
python run_server.py --transport streamable-http --host 127.0.0.1 --port 8000
```

## 4. 配置 Job 白名单

`config/allowed_jobs.yml` 示例：

```yaml
jobs:
  app-prod:
    display_name: App Production Release
    jenkins_path: release-folder/app-prod-release
    description: Release the app service to production.
    required_params:
      - VERSION
      - ENV
    allowed_params:
      - VERSION
      - ENV
      - CANARY
      - CHANGE_ID
    parameter_options:
      ENV:
        - prod
        - staging
```

字段说明：

- `app-prod`：暴露给 MCP 客户端的逻辑 Job 名称。
- `display_name`：展示名称。
- `jenkins_path`：真实 Jenkins Job 路径。Folder Job 写成 `folder/job-name`，代码会转换为 Jenkins URL `/job/folder/job/job-name/`。
- `required_params`：发版时必须提供的参数。
- `allowed_params`：允许提交给 Jenkins 的参数。未列出的参数会被拒绝。
- `parameter_options`：可选。用于限制某个参数允许传哪些值，适合 Jenkins 的“选项参数”。

Jenkins “选项参数”要这样填写：

```text
名称: models

选项:
service-model
service-api
service-web
```

对应配置：

```yaml
required_params:
  - models
allowed_params:
  - models
parameter_options:
  models:
    - service-model
    - service-api
    - service-web
```

注意：`allowed_params` 写的是参数名称，也就是 `models`；`parameter_options.models`
下面写的才是 Jenkins 选项值，也就是具体模块名。

如果 Jenkins Job 没有参数：

```yaml
jobs:
  healthcheck-release:
    display_name: Healthcheck Release
    jenkins_path: healthcheck-release
    allowed_params: []
    required_params: []
```

## 5. 启动 MCP

### stdio 模式

适合由本机 MCP 客户端直接启动：

```powershell
python run_server.py --transport stdio
```

如果已经执行过 `python -m pip install -e .`，也可以使用模块启动：

```powershell
python -m jenkins_release_mcp --transport stdio
```

也可以用安装后的命令：

```powershell
jenkins-release-mcp --transport stdio
```

### streamable-http 模式

适合服务化访问：

```powershell
python run_server.py --transport streamable-http --host 127.0.0.1 --port 8000
```

如果已经执行过 `python -m pip install -e .`，也可以使用模块启动：

```powershell
python -m jenkins_release_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

MCP HTTP 地址：

```text
http://127.0.0.1:8000/mcp
```

## 6. MCP 客户端配置示例

### stdio

```json
{
  "mcpServers": {
    "jenkins-release": {
      "command": "python",
      "args": ["-m", "jenkins_release_mcp", "--transport", "stdio"],
      "env": {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "release-bot",
        "JENKINS_API_TOKEN": "replace-with-api-token",
        "JENKINS_ALLOWED_JOBS_FILE": "D:\\path\\to\\jenkins_mcp\\config\\allowed_jobs.yml"
      }
    }
  }
}
```

### streamable-http

先启动服务：

```powershell
python -m jenkins_release_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

客户端连接：

```text
http://127.0.0.1:8000/mcp
```

如果配置了 `MCP_BEARER_TOKEN`，客户端界面这样填：

```text
名称: jenkins-release
URL: http://127.0.0.1:8000/mcp
Bearer 令牌环境变量: MCP_BEARER_TOKEN
标头: 不填
来自环境变量的标头: 不填
```

然后在 MCP 客户端所在环境里设置同名变量：

```powershell
$env:MCP_BEARER_TOKEN="你的随机长token"
```

MCP Inspector 示例：

```powershell
npx -y @modelcontextprotocol/inspector
```

在 Inspector UI 中选择 Streamable HTTP，然后填入 `http://127.0.0.1:8000/mcp`。

## 7. 可用工具

### list_release_jobs

查看当前白名单内允许发版的 Jenkins Job。

请求参数：无。

返回示例：

```json
{
  "jobs": [
    {
      "name": "app-prod",
      "display_name": "App Production Release",
      "description": "Release the app service to production.",
      "jenkins_path": "release-folder/app-prod-release",
      "job_url": "https://jenkins.example.com/job/release-folder/job/app-prod-release/",
      "required_params": ["VERSION", "ENV"],
      "allowed_params": ["VERSION", "ENV", "CANARY", "CHANGE_ID"]
    }
  ]
}
```

### trigger_release

校验并触发 Jenkins Job。建议每次真实发版前先 dry-run。

Dry-run 示例：

```json
{
  "job_name": "app-prod",
  "params": {
    "VERSION": "1.2.3",
    "ENV": "prod",
    "CHANGE_ID": "CHG-10086"
  },
  "dry_run": true
}
```

真实触发示例：

```json
{
  "job_name": "app-prod",
  "params": {
    "VERSION": "1.2.3",
    "ENV": "prod",
    "CHANGE_ID": "CHG-10086"
  },
  "dry_run": false,
  "confirm": true
}
```

返回示例：

```json
{
  "dry_run": false,
  "job_name": "app-prod",
  "jenkins_path": "release-folder/app-prod-release",
  "job_url": "https://jenkins.example.com/job/release-folder/job/app-prod-release/",
  "params": {
    "VERSION": "1.2.3",
    "ENV": "prod",
    "CHANGE_ID": "CHG-10086"
  },
  "queue_url": "https://jenkins.example.com/queue/item/123/",
  "queue_id": 123,
  "message": "Jenkins build was triggered."
}
```

### get_queue_item

查询 Jenkins 队列状态。

```json
{
  "queue_id": 123
}
```

如果队列已经进入构建，会返回 `build_number` 和 `build_url`。

### get_build_status

查询构建状态。

```json
{
  "job_name": "app-prod",
  "build_number": 45
}
```

常见结果：

- `building=true`：仍在运行。
- `result=SUCCESS`：成功。
- `result=FAILURE`：失败。
- `result=ABORTED`：被中止。

### wait_for_build

等待队列进入构建并等待构建结束。

从队列等待：

```json
{
  "job_name": "app-prod",
  "queue_id": 123,
  "timeout_seconds": 1800,
  "poll_interval_seconds": 5
}
```

已知构建号时等待：

```json
{
  "job_name": "app-prod",
  "build_number": 45,
  "timeout_seconds": 1800,
  "poll_interval_seconds": 5
}
```

### get_build_log

读取 Jenkins progressive console log。

```json
{
  "job_name": "app-prod",
  "build_number": 45,
  "start_offset": 0,
  "max_chars": 12000
}
```

### get_build_changes

查看 Jenkins 记录的代码提交人和代码变动。

```json
{
  "job_name": "app-prod",
  "build_number": 45
}
```

返回内容包括 commit id、提交人、提交说明、提交时间、影响文件。

注意：这些信息来自 Jenkins build 的 `changeSet` / `changeSets`。只有 Jenkins 构建已经进入 SCM checkout，或者构建结束后，才大概率能查到。刚触发、还在排队、还没拉代码时，返回空是正常的。

### get_build_branch

查看 Jenkins 构建实际 checkout 的代码分支和 commit。

```json
{
  "job_name": "app-prod",
  "build_number": 3176
}
```

返回示例：

```json
{
  "job_name": "app-prod",
  "build_number": 3176,
  "branch": "release/20250116_v5.40.0.25.01",
  "commit_hash": "63073f1cc0c74fd2af14553e7a1a76b61f9bcd8c",
  "remote_url": "http://git.example.com/app.git",
  "build_url": "http://jenkins.example.com/job/app-prod-release/3176/",
  "message": "Jenkins checkout branch was found."
}
```

### trigger_multi_module_release

多模块逐个触发 Jenkins。当前默认使用 `models` 参数。

Dry-run：

```json
{
  "job_name": "app-prod",
  "modules": [
    "service-api",
    "service-web"
  ],
  "dry_run": true
}
```

真实触发：

```json
{
  "job_name": "app-prod",
  "modules": [
    "service-api",
    "service-web"
  ],
  "dry_run": false,
  "confirm": true
}
```

它会逐个触发 Jenkins，每个模块一个 queue/build，返回每个模块对应的 `queue_id`。

### wait_release_and_notify

等待单个发版完成，并发送 QQ 邮箱通知。

```json
{
  "job_name": "app-prod",
  "queue_id": 123,
  "module": "service-web",
  "timeout_seconds": 1800
}
```

成功邮件会包含 Job、模块、构建号、构建地址、提交人摘要。失败邮件会自动带失败日志摘要。

邮件标题和正文使用中文。成功标题示例：

```text
【发版成功】app-prod / service-web / #3176
```

正文会包含：

- 任务名
- 模块名
- 构建号和构建地址
- 代码分支
- Commit Hash
- 提交人
- 代码变动摘要
- 失败日志摘要，如果发版失败

如果没有配置 SMTP，工具不会影响 Jenkins 状态，只会返回：

```json
{
  "notification": {
    "sent": false,
    "error": "SMTP is not configured..."
  }
}
```

### release_and_notify_background

触发发版后立刻返回本地后台任务 ID，MCP 服务会在后台继续监控 Jenkins，并在完成后发送邮件。Codex 不需要保持工具调用等待。

```json
{
  "job_name": "app-prod",
  "params": {
    "models": "service-web"
  },
  "module": "service-web",
  "confirm": true,
  "timeout_seconds": 1800,
  "poll_interval_seconds": 5
}
```

返回示例：

```json
{
  "task": {
    "task_id": "4e5f...",
    "status": "queued",
    "queue_id": 123,
    "message": "Jenkins 发版已触发，后台正在监控。"
  }
}
```

注意：后台任务状态保存在 MCP 服务进程内。重启 MCP 服务后，本地任务记录会消失，但已经触发的 Jenkins 构建不会被取消。

### get_release_task

按后台任务 ID 查询发版进度。

```json
{
  "task_id": "4e5f..."
}
```

常见状态：`queued`、`running`、`success`、`failed`、`error`。

### list_release_tasks

查看当前 MCP 服务进程内最近的后台发版任务。

```json
{
  "limit": 20
}
```

### wait_multi_module_release_and_notify

等待多个模块发版完成，并发送一封汇总邮件。

`releases` 可以直接使用 `trigger_multi_module_release` 返回的条目整理：

```json
{
  "job_name": "app-prod",
  "releases": [
    {
      "module": "service-api",
      "queue_id": 123
    },
    {
      "module": "service-web",
      "queue_id": 124
    }
  ],
  "timeout_seconds": 1800
}
```

### list_recent_releases

查看最近发版记录。

```json
{
  "job_name": "app-prod",
  "limit": 10
}
```

返回构建号、状态、开始时间、耗时、构建地址和 Jenkins 参数。

返回中的 `next_offset` 可以用于下一次继续读取：

```json
{
  "job_name": "app-prod",
  "build_number": 45,
  "start_offset": 0,
  "next_offset": 12000,
  "has_more_data": true,
  "text": "..."
}
```

## 8. 推荐发版流程

1. 调用 `list_release_jobs`，确认 Job 名称和参数。
2. 调用 `trigger_release`，传 `dry_run=true`，确认即将触发的 Jenkins URL 和参数。
3. 调用 `trigger_release`，传 `dry_run=false`、`confirm=true`，真实触发发版。
4. 用返回的 `queue_id` 调用 `wait_release_and_notify`，等待完成并发送邮件。
5. 如果只想看状态不发邮件，调用 `wait_for_build`。
6. 如果构建失败或卡住，调用 `get_build_log` 查看日志。
7. 构建产生 build number 后，调用 `get_build_changes` 查看提交人和代码变动。

如果希望 Codex 不持续等待，可以使用后台闭环流程：

1. 调用 `release_and_notify_background`，它会立刻返回 `task_id`。
2. MCP 服务后台继续监控 Jenkins，并在成功/失败后发送邮件。
3. 需要查看进度时调用 `get_release_task`。

## 9. 故障排查

### 401 或 403

检查：

- `JENKINS_USER` 是否正确。
- `JENKINS_API_TOKEN` 是否是 API Token。
- 用户是否有目标 Job 的 `Read` 和 `Build` 权限。

### 404

检查 `jenkins_path`。Folder Job 要写成：

```yaml
jenkins_path: folder-name/job-name
```

它会被转换为：

```text
/job/folder-name/job/job-name/
```

### 参数被拒绝

检查参数名是否在 `allowed_params` 中，并且所有 `required_params` 都已传入。

### 真实发版没有触发

真实触发必须同时满足：

```json
{
  "dry_run": false,
  "confirm": true
}
```

只设置 `dry_run=false` 会被拒绝。

### 队列长时间阻塞

调用 `get_queue_item` 查看 `why` 字段。常见原因包括无可用 agent、上游构建未结束、Job 被禁用、并发策略限制。

### 日志太长

使用 `get_build_log` 的 `next_offset` 分页读取，不要一次要求返回完整日志。
