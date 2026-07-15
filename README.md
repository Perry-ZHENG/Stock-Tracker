# Stock Agent

面向美股研究的本地优先 Agent 项目。它收集可追溯的市场数据与新闻证据，执行已审批的信号函数，按需调用专业分析 Agent，并生成带证据引用、限制条件和校验结果的研究报告。

项目只生成研究说明和报告，**不连接券商、不下单、不自动交易**。

## 核心能力

- FastAPI 工作台：一个进程同时提供服务端 API 和内嵌 Jinja 前端页面。
- V2 研究任务：提交研究请求、持久化计划和步骤、支持暂停、恢复、取消及重启恢复。
- Evidence-first：市场数据、新闻、分析和报告均通过 Task-scoped Artifact/Evidence 保存和校验。
- 多 Agent：Signal Discovery、异动分析、宏观分析、报告 Agent；计划、验证和人工审批保持确定性边界。
- 信号安全链：Proposal -> Candidate -> Sandbox -> 时间切分验证 -> 人工审批 -> Active Signal。
- 只读 MCP、运行 Trace、预算和 Provider freshness 诊断。

详细设计见 [V2 架构](docs/v2_architecture.md)、[运行手册](docs/v2_operations.md) 和 [V2 任务清单](task_agent_v2.md)。

## 项目结构

```text
src/stock_agent/
  web/                     FastAPI 路由、页面模板和 SSE
  commands/                CLI、Web、Telegram、Worker 启动入口
  services/
    production_v2.py       正式 V2 组合根：构造 AgentService 与真实 Step Handler
    agent_service.py       任务生命周期服务
    entrypoints.py         CLI / Web / Telegram 共用的研究入口
  agents/                  Orchestrator、Runtime、异动/宏观/报告/信号发现 Agent
  research/                DataEvidence、NewsEvidence、特征和宏观证据工作流
  reports/                 EvidenceBundle、ReportService、渲染器
  signals/                 Active Signal 执行、Registry、人工审批
  signal_lab/              Candidate Builder、Sandbox、验证与泄漏检查
  worker/
    research_v2.py         持久化 V2 研究任务 Worker
    pipeline.py             兼容的市场监控 Worker pipeline
  contracts/               Pydantic 输入/输出契约
  artifacts/ evidence/     内容寻址 Artifact 与 Evidence 服务
  storage/                 SQLite 迁移和 Repository
  observability/           Agent Trace、预算与健康诊断

configs/config.yaml        运行配置
data/sample/               离线 CSV 示例数据
tests/                     单元、集成、E2E 和安全测试
docs/                      架构、部署、运维与迁移文档
```

正式研究调用链：

```text
CLI / FastAPI / Telegram
  -> build_production_v2()
  -> ResearchEntryAdapter -> AgentService
  -> Orchestrator + AgentRuntime
  -> Data/News Evidence -> Specialist Agents -> ReportService
  -> Claim Validator -> FinalReport

stock-agent worker
  -> ResearchTaskWorkerV2（持久步骤与可重试证据缺口）
  -> 兼容市场监控 pipeline
```

## 环境要求

- Python `3.12+`
- `uv`，推荐用于依赖与脚本管理
- macOS、Linux 或 Windows 均可运行；本地工作台默认监听 `127.0.0.1`

安装开发依赖：

```sh
uv sync --extra dev
```

安装为本机命令行工具（任选其一）：

```sh
pipx install .
uv tool install .
```

也可以使用已经创建的虚拟环境：

```sh
.venv/bin/python --version
```

## 快速启动

### 1. 检查或创建配置

默认配置在 `configs/config.yaml`。若项目副本中没有该文件：

```sh
uv run stock-agent init-config
```

### 2. 一条命令启动前端、后端和 Worker

当前前端是 FastAPI 内嵌工作台，不需要单独启动 Node、Vue 或 React 服务。

```sh
./scripts/stack_v2.sh start
```

脚本会加载 `~/.config/stock-agent/env`、启动 FastAPI 工作台和常驻 V2 Worker，并把日志写入 `data/runtime/logs/`。首次从手工启动切换到脚本管理时，先在原来的 Web、Worker 终端按 `Ctrl+C`；之后使用以下命令统一管理：

```sh
./scripts/stack_v2.sh status
./scripts/stack_v2.sh logs
./scripts/stack_v2.sh restart
./scripts/stack_v2.sh stop
```

脚本只停止它自己启动并记录 PID 的进程，不会终止其他终端手工启动的服务。若仍需手工启动，可使用 `stock-agent web` 与 `stock-agent worker --interval-sec 5`。

打开以下地址：

- 工作台：`http://127.0.0.1:8000/`
- OpenAPI 文档：`http://127.0.0.1:8000/api/docs`
- 健康接口：`http://127.0.0.1:8000/api/v1/health`

### 网页如何发起研究

在工作台填写研究问题、标的代码、研究类型和回溯天数，点击“创建研究任务”。右侧会显示已提交的问题、Agent 步骤、证据缺口和最终报告。默认“离线示例”使用项目内的 `QQQ` 历史 CSV，因此没有行情密钥也可观察完整的数据收集流程；“近期实时数据”需要配置行情服务。它是异步研究任务界面：提交后必须有后台 Worker 执行，最终报告只会在证据和校验都完成后显示。

### CLI 交互入口

```sh
.venv/bin/stock-agent cli
```

CLI 适合查询项目状态与只读数据，例如输入“你能做什么”“show health”或“show me latest QQQ signals”。网页与 CLI 一次只允许一个入口提交命令：若 CLI 提示待批准切换，请在网页底部“输入控制状态”中批准即可。完整研究报告应通过网页研究工作台提交，或使用 `stock-agent research submit` 传入符合 `ResearchRequest` 契约的 JSON。

### 3. 用脚本提交研究任务

启动堆栈后，通过 FastAPI 提交一个研究任务。该脚本只创建任务，行情、新闻和模型调用由 Worker 按任务需求执行：

```sh
./scripts/submit_research_v2.sh \
  --symbol QQQ \
  --days 30 \
  --question "结合近一个月的价格、成交量与新闻，分析 QQQ 的异动是否具备持续性，并列出证据不足之处。"
```

可追加 `--current-data` 请求近期数据，或使用 `--report-type facts|anomaly|macro|signal|full` 收窄任务。任务提交后在工作台查看步骤、证据缺口与最终报告。

它只处理用户提交的 V2 研究任务，不会自行持续盯盘或轮询外部行情。只想手动处理一次 V2 研究任务时：

```sh
uv run stock-agent research work
```

只执行一个指定任务而不消费其他待执行任务时，追加任务 ID：

```sh
uv run stock-agent research work TASK_ID
```

### 外部 API 额度

V2 只在任务实际需要数据时请求外部服务。Twelve Data 的一分钟额度由 SQLite 统一预留，跨重启和多个 Worker 生效；默认不重试失败请求，因此单标的研究通常只产生一次行情请求。OpenRouter 遇到 `429` 也不会切换备用模型，以免消耗同一账户的额外额度；新任务默认最多 4 次模型调用。额度耗尽时任务会记录可追溯的证据缺口或明确的回退来源，不会持续重试。执行后请在任务诊断中确认 `provider_name=twelve_data`、`fallback_used=false`；若出现回退或额度不足，报告只能作为降级结果，不能当作 Twelve Data 验证结果。

保留的 V1 市场监控仅在明确需要时启用。它会持续请求数据，应使用提供商配置的安全轮询间隔（默认 60 秒），不要与 V2 研究 Worker 混用：

```sh
uv run stock-agent worker --include-legacy-market-watch --interval-sec 60
```

## 研究任务使用方式

### CLI 提交

创建 `request.json`：

```json
{
  "request_id": "qqq-facts-20260522",
  "question": "生成 QQQ 在该时段的事实型研究报告。",
  "symbols": ["QQQ"],
  "time_window": {
    "from_ts": "2026-05-22T13:30:00Z",
    "to_ts": "2026-05-22T20:00:00Z",
    "timezone": "America/New_York"
  },
  "report_type": "facts"
}
```

提交、查看状态和获取最终报告：

```sh
uv run stock-agent research submit --request-file request.json
uv run stock-agent research status TASK_ID
uv run stock-agent research report TASK_ID --format markdown
```

支持的 `report_type`：`facts`、`anomaly`、`macro`、`signal`、`full`。

### HTTP API 提交

```sh
curl -X POST http://127.0.0.1:8000/api/v2/research \
  -H 'Content-Type: application/json' \
  --data @- <<'JSON'
{
  "request": {
    "request_id": "qqq-facts-api",
    "question": "生成 QQQ 的事实型研究报告。",
    "symbols": ["QQQ"],
    "time_window": {
      "from_ts": "2026-05-22T13:30:00Z",
      "to_ts": "2026-05-22T20:00:00Z",
      "timezone": "America/New_York"
    },
    "report_type": "facts"
  }
}
JSON
```

后续调用：

```text
GET  /api/v2/research/{task_id}
POST /api/v2/research/{task_id}/pause
POST /api/v2/research/{task_id}/resume
POST /api/v2/research/{task_id}/cancel
GET  /api/v2/research/{task_id}/report
GET  /api/v2/research/{task_id}/diagnostics
GET  /api/v2/research/{task_id}/events
```

`diagnostics` 会返回任务计划、步骤 Trace、Provider freshness、模型预算和成本估计。SSE `events` 可用于前端轮询之外的状态刷新。

## 离线与外部接口

### 无密钥离线模式

无需任何密钥即可启动页面、访问 API、使用 SQLite 历史数据，并在 Twelve Data 不可用时回退到 `data/sample/sample_bars.csv`。示例 CSV 覆盖 `QQQ` 在 2026-05-22 的 30 分钟数据，适合用于上述示例请求。

没有模型密钥时，数据、新闻空证据和步骤 Trace 仍会正常持久化；报告步骤会写入明确的 `EvidenceGap`，并在 CLI/Web 的任务状态响应中展示，不会伪造 `FinalReport`。

### 可选环境变量

实时功能由环境变量提供，不要把密钥写入 Git：

```sh
# zsh / bash：每次提示输入后，在当前 shell 会话中导出变量。
read -rs TWELVE_DATA_API_KEY && export TWELVE_DATA_API_KEY
read -rs OPENROUTER_API_KEY && export OPENROUTER_API_KEY
read -rs TELEGRAM_BOT_TOKEN && export TELEGRAM_BOT_TOKEN
```

| 能力 | 需要的配置 | 当前行为 |
|---|---|---|
| 实时行情 | `TWELVE_DATA_API_KEY` | Twelve Data 不可用时回退 CSV。 |
| Agent 最终报告、宏观/信号发现推理 | `OPENROUTER_API_KEY` | 缺失时返回可追踪 `EvidenceGap`。 |
| Telegram 入口 | `TELEGRAM_BOT_TOKEN` 和 `telegram.enabled: true` | 支持受控提交、状态查询和管理员审批。 |
| 外部新闻 | `news` 配置和一个实现 `NewsProvider` 的接入 | 当前默认 `placeholder` 只产生空新闻证据，不会伪造新闻。 |

配置 LLM 后需重启 Web/Worker 进程。模型只可在受限 Schema、证据引用和预算内工作，不能获得交易或审批权限。

## 信号与安全边界

- Agent 不能下单、调用券商或审批信号。
- 新信号必须经过 Candidate AST 审查、隔离 Sandbox、时间切分验证和人工管理员审批。
- 仅已审批的 Active Signal Version 可以在 Worker 中执行。
- 新闻和 MCP 内容被视为不可信数据，不能改变系统权限或工具列表。
- 只有通过 Evidence 和 Claim Validator 的 ReportDraft 才可变为 `FinalReport`。
- 自动重试仅限 `bar`、`news`、`provider` 证据缺口；模型、MCP 和人工输入缺口会停止等待处理。

## 常用命令

```sh
uv run stock-agent health --verbose
uv run stock-agent run-demo
stock-agent run-demo
stock-agent deploy-validate
uv run stock-agent worker --once
uv run stock-agent research work
uv run stock-agent research work TASK_ID
uv run stock-agent research status TASK_ID
uv run stock-agent mcp-server
uv run --extra dev pytest
PYTHONPATH=src:tests .venv/bin/python -m pytest -q
```

## 测试与质量检查

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest -q
git diff --check
```

当前 V2 发布回归结果：`461 passed, 1 skipped, 1 xfailed`。

## 免责声明

本项目用于教育、研究和作品集展示，不构成投资建议、财务建议或自动交易系统。所有输出均应结合独立研究与风险判断使用。
