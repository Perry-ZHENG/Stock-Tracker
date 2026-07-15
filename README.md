# Stock Agent V2

一个本地优先、证据驱动的美股研究 Agent。它根据用户问题按需收集行情和新闻，编排专业 Agent，生成可追溯、带限制条件的研究报告。

项目**不接入券商、不下单、不自动交易**。输出仅为研究说明，不构成投资建议。

## 核心流程

```text
FastAPI / CLI / Telegram
  -> ResearchEntryAdapter -> AgentService
  -> Orchestrator + AgentRuntime
  -> DataEvidence + NewsEvidence
  -> Anomaly / Macro / Signal Discovery / Report Agents
  -> Evidence + Claim Validation -> FinalReport
```

- `DataEvidence`：按任务时间范围请求 Twelve Data；额度或接口失败时，历史研究可降级到明确标注的 `synthetic_demo` 数据。
- `NewsEvidence`：缓存和去重新闻，缺少新闻时产生空证据，不伪造新闻事实。
- 专业 Agent：异动、宏观、信号发现和报告由受约束的模型调用完成。
- 确定性边界：计划、证据注册、模型预算、声明校验、信号 Sandbox、时间切分验证和人工审批不由模型绕过。
- `FinalReport`：只有报告的每项声明都引用任务内已验证证据时才会发布。

## 代码结构

```text
src/stock_agent/
  agents/             V2 编排、运行时、异动/宏观/信号发现/报告 Agent
  services/           生产组合根与统一任务生命周期
  research/           数据、新闻、特征和宏观证据工作流
  signals/ signal_lab/ 已审批信号执行、发现、Sandbox 和验证
  reports/ validation/ 报告组装、渲染和证据/声明校验
  artifacts/ evidence/ 任务级 Artifact 和 Evidence 存储
  providers/          Twelve Data 与明确标注的 synthetic fallback
  worker/             持久化 V2 研究任务执行器
  web/                FastAPI API 与内嵌工作台
  mcp/ tooling/       只读 MCP 服务与工具适配层
  storage/            SQLite 迁移、任务/报告/信号 Repository
  telegram/           可选的 V2 研究任务传输层

scripts/stack_v2.sh           统一启动 Web 和 Worker
scripts/submit_research_v2.sh 通过 FastAPI 创建研究任务
tests/test_v2_end_to_end.py   唯一保留的离线端到端验证
```

旧版 ReAct、持续盯盘、公式策略、Broker、通知管道、V1 API 和单步测试均已移除。

## 环境

- Python `3.12+`
- 推荐 `uv`

```sh
uv sync --extra dev
```

复制或编辑 `configs/config.yaml`。模型和外部 API 密钥放在本机环境文件，不要提交到 Git：

```sh
mkdir -p ~/.config/stock-agent
chmod 700 ~/.config/stock-agent
cat >> ~/.config/stock-agent/env <<'EOF'
TWELVE_DATA_API_KEY=your_twelve_key
GEMINI_API_KEY=your_gemini_key
EOF
chmod 600 ~/.config/stock-agent/env
```

使用 Gemini 的 OpenAI 兼容接口时，在 `configs/config.yaml` 设置：

```yaml
llm:
  enabled: true
  provider: gemini
  model: gemini-3.5-flash
  api_key_env: GEMINI_API_KEY
  base_url: https://generativelanguage.googleapis.com/v1beta/openai/
  request_timeout_sec: 60
  max_retries: 0
```

也可以使用 OpenRouter 或其他 OpenAI 兼容端点：修改 `provider`、`model`、`api_key_env` 和 `base_url` 即可。

## 启动

Web 和 Worker 必须同时运行：Web 负责提交和展示，Worker 负责执行持久化步骤。

```sh
./scripts/stack_v2.sh start
```

脚本自动读取 `~/.config/stock-agent/env`，日志写入 `data/runtime/logs/`。

```sh
./scripts/stack_v2.sh status
./scripts/stack_v2.sh logs
./scripts/stack_v2.sh restart
./scripts/stack_v2.sh stop
```

打开：

- 工作台：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/api/docs`
- 健康检查：`http://127.0.0.1:8000/api/v2/health`

首次从手工启动切换到脚本管理时，先在原有 Web、Worker 终端按 `Ctrl+C`，避免 Worker 锁冲突。

## 创建研究

在网页填写问题、标的、研究类型和时间范围，点击“创建研究任务”。网页会显示计划步骤、证据缺口、状态和已校验的最终报告。

也可通过脚本提交：

```sh
./scripts/submit_research_v2.sh \
  --symbol QQQ \
  --days 30 \
  --question "结合近一个月的价格、成交量与新闻，分析 QQQ 的异动是否具备持续性，并列出证据不足之处。"
```

可追加 `--current-data` 请求近期数据。历史研究若 Twelve Data 不可用，可使用明确标注的 synthetic fallback；近期数据任务不会以 synthetic 数据冒充实时行情。

CLI 也提供同一套 V2 生命周期：

```sh
uv run stock-agent research submit --request-file request.json
uv run stock-agent research status TASK_ID
uv run stock-agent research work TASK_ID
uv run stock-agent research report TASK_ID --format markdown
```

`research work` 只用于本地排障；正常场景由常驻 Worker 执行。`pause`、`resume`、`cancel` 和 `retry-report` 都经过同一个 `AgentService`。

## HTTP API

```text
POST /api/v2/research
GET  /api/v2/research/{task_id}
POST /api/v2/research/{task_id}/pause
POST /api/v2/research/{task_id}/resume
POST /api/v2/research/{task_id}/cancel
POST /api/v2/research/{task_id}/retry-report
GET  /api/v2/research/{task_id}/report
GET  /api/v2/research/{task_id}/diagnostics
GET  /api/v2/research/{task_id}/events
```

输入接口协调 API 位于 `/api/v2/input`。它只控制 CLI、Web、Telegram 三个合法入口的互斥提交权，不影响后台 Worker。

## 验证

项目不保留单元测试或按任务拆分的测试。唯一保留测试验证完整 V2 链路，使用离线 Fixture Provider 和确定性模型，不消耗 API 额度：

```sh
uv run pytest tests/test_v2_end_to_end.py
```

详细设计见 [V2 架构](docs/v2_architecture.md)、[运行手册](docs/v2_operations.md)、[任务说明](task_agent_v2.md) 和 [文件清单](docs/v2_file_manifest.md)。
