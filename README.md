# Stock Agent

## 1. Task Summary

Stock Agent is a local-first stock monitoring agent framework for US market watch workflows.

It is designed to:

- Load demo or live market data.
- Build 30-minute bars.
- Run MA Cross, BOLL, MACD, KDJ, and Active-J strategies.
- Review candidate signals with supervisor checks.
- Store signals, traces, health metrics, and notifications in SQLite.
- Query signals, health status, traces, abnormal bars, and provider status through CLI.
- Accept controlled natural-language input from CLI, Telegram, or FastAPI.
- Route Chinese or English requests to typed tools through an optional model API.

Currently runnable market-data flow:

```text
Twelve Data 1m bars -> 30m bar builder -> strategy -> supervisor -> SQLite
        | provider failure
        v
demo CSV fallback -> strategy -> supervisor -> SQLite
```

Currently runnable interaction flow:

```text
CLI / Telegram / FastAPI
        -> persistent single-input gate
        -> ReAct tool-routing Agent or deterministic parser
        -> typed query tool
        -> SQLite / Data Lake result
```

### 1.1 Features Added Since The Previous Version

- Added the Twelve Data REST provider, request retry and credit-budget checks,
  1-minute source bars, 30-minute aggregation, and CSV fallback.
- Added a persistent global input owner shared by CLI, Telegram, and FastAPI.
  Only the active interface can submit commands; switching requires approval
  from the original interface and expires after 10 minutes by default.
- Added Telegram Bot API long polling, interface heartbeats, input-switch
  request/approval commands, and proactive approval notifications.
- Added a FastAPI workbench with query APIs, Agent plan/confirm APIs, input
  control, an approval page, and an SSE status stream.
- Added a bilingual ReAct routing Agent with typed tools. It asks for missing
  parameters, returns `no_suitable_tool` when no script matches, and never
  invents an unregistered script.
- Added OpenRouter-hosted Qwen configuration with `openrouter/free` fallback
  for temporary rate-limit or provider errors.
- Added regression coverage for Agent routing, all three input interfaces,
  FastAPI, Telegram transport, live-data configuration, and worker fallback.

## 2. Installation Requirements

- Python 3.12+
- uv
- Optional: pipx or uv tool

Development install:

```sh
uv sync --extra dev
```

Tool-style install:

```sh
pipx install .
```

Or:

```sh
uv tool install .
```

## 3. Interface Configuration

Default config file:

```text
configs/config.yaml
```

### 3.1 Demo Market Data Interface

Purpose: Validate the system without any external API key.

```yaml
provider:
  default: csv_demo
  csv_demo:
    path: data/sample/sample_bars.csv
```

### 3.2 Twelve Data Live Market Data Interface

Purpose: Fetch 1-minute live bars, aggregate them into the configured 30-minute
bars, and fall back to demo CSV when the provider is unavailable.

```yaml
provider:
  default: twelve_data
  priority:
    - twelve_data
  fallback:
    enabled: true
    order:
      - csv_demo
  csv_demo:
    path: data/sample/sample_bars.csv
  twelve_data:
    api_key_env: TWELVE_DATA_API_KEY
    base_url: https://api.twelvedata.com
    source_interval: 1min
    poll_interval_sec: 60
    request_timeout_sec: 15
    max_retries: 3
    credit_budget_per_minute: 8
```

PowerShell environment variable:

```powershell
$env:TWELVE_DATA_API_KEY = "your-twelve-data-key"
```

Watch symbols:

```yaml
symbols:
  default:
    - QQQ
    - NVDA
    - TSLA
```

### 3.3 Telegram Interface

Purpose: Enable the Telegram query/interaction entrypoint through Bot API long polling.

```yaml
telegram:
  enabled: true
  token_env: TELEGRAM_BOT_TOKEN
  allowed_user_ids:
    - 123456789
  admin_user_ids: []
  allowed_chat_ids: []
```

PowerShell environment variable:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-telegram-bot-token"
```

Current Telegram bot core supports:

- `/signals`
- `/health`
- `/news`
- `/schedule`
- `/provider-compare`
- `/abnormal-bars`
- `/trace SIGNAL_ID`
- `/input status`
- `/input request`
- `/input approve REQUEST_ID`
- `/input reject REQUEST_ID`
- Pending config changes
- High-risk trading/account/credential intent blocking

To enable automatic worker-to-Telegram push alerts, add:

```text
TelegramNotificationSink -> NotificationOutbox.dispatch_pending(...)
```

### 3.4 LLM Interface

Purpose: Use the OpenRouter-hosted Qwen model for Agent tool routing, not for
direct trading decisions.

```yaml
llm:
  enabled: true
  provider: openrouter
  model: qwen/qwen3-next-80b-a3b-instruct:free
  fallback_model: openrouter/free
  api_key_env: OPENROUTER_API_KEY
  base_url: https://openrouter.ai/api/v1
  request_timeout_sec: 45
  max_retries: 0
```

PowerShell environment variable:

```powershell
$env:OPENROUTER_API_KEY = "your-openrouter-key"
```

Integration shape:

```python
parser = LlmParser(client=openai_intent_client, enabled=True)
bot = TelegramBot(
    root=root,
    connection=connection,
    settings=settings,
    config_context=config_context,
    llm_parser=parser,
)
```

The model is used only to select a registered tool and extract its arguments.
Indicator calculation, signal generation, supervisor checks, and trading
actions remain outside the model.

Registered Agent tools:

- `query_signals`, `query_bars`, `query_health`, and `query_trace`
- `fetch_twelve_data_bars` for direct remote Twelve Data OHLCV requests
- `query_news`, `query_statistics`, and `query_schedule`
- `query_provider_compare`, `query_abnormal_bars`, and `query_config_changes`
- `ask_user` for missing parameters
- `no_suitable_tool` for unsupported requests

For symbol-specific market dynamics, K-line, or signal queries, the caller must
provide `from_ts`, `to_ts`, and an explicit IANA `timezone`. Both timestamps
must include a calendar date and clock time. Relative phrases such as “today”
or “recently” trigger a clarification instead of being guessed by the model.

Example natural-language request:

```text
请直接从 Twelve Data 获取 QQQ 在 2026-07-06 09:30 到
2026-07-06 10:30 America/New_York 的 1 分钟行情
```

`fetch_twelve_data_bars` calls the existing Twelve Data REST provider directly.
It does not read the local Data Lake, start the Worker, run a strategy, create a
signal, or require an MCP server. MCP can be added later if these tools need to
be exposed to external Agent clients.

### 3.5 FastAPI Workbench

Purpose: Expose read-only queries, controlled Agent input, input ownership, and
live status updates to a local web client.

Start the current web command implementation:

```powershell
uv run python -c "from pathlib import Path; from stock_agent.commands.web import run_web; raise SystemExit(run_web(Path.cwd()))"
```

Then open:

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/api/docs
```

The home page displays the active input interface and pending switch requests.
Submit an Agent request through the API:

```powershell
$body = @{ message = "查询 QQQ 今天的 MACD 信号" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/agent/plan" `
  -ContentType "application/json" `
  -Body $body
```

Main endpoints:

- `GET /api/v1/bars`
- `GET /api/v1/signals`
- `GET /api/v1/signals/{signal_id}/trace`
- `GET /api/v1/health`
- `GET /api/v1/config-changes`
- `POST /api/v1/agent/plan`
- `POST /api/v1/agent/runs/{run_id}/confirm`
- `GET /api/v1/input`
- `POST /api/v1/input/switch/requests`
- `GET /api/v1/events` (SSE)

### 3.6 Global Input Control

CLI, Telegram, and FastAPI are all output/query interfaces, but only one of
them may submit commands at a time.

- The active interface and pending switch requests are persisted in SQLite.
- A blocked interface receives the current active-interface message.
- The new interface submits a switch request.
- The original active interface must approve or reject the request.
- Requests expire after 600 seconds by default.
- The original interface must be online for a switch request to be created.

See `docs/input_control.md` for the command and API details.

## 4. Usage Steps And Expected Outputs

### Step 1. Initialize Config

Purpose: Generate the default config and environment example.

```sh
uv run stock-agent init-config
```

Expected output:

```text
exists: <project-root>/configs/config.yaml
exists: <project-root>/.env.example
```

### Step 2. Run Deployment Dry-Run Validation

Purpose: Confirm that config, demo CSV, and runtime directories are ready.

```sh
uv run stock-agent deploy-validate
```

Expected output:

```text
deploy_validation_status=ok
dry_run=true
root=<project-root>
config_path=<project-root>/configs/config.yaml
check | status | target | message
workdir | ok | <project-root> | project working directory
config | ok | <project-root>/configs/config.yaml | config loaded
sqlite_parent | ok | <project-root>/data/runtime | SQLite runtime directory
lake_parent | ok | <project-root>/data/lake | lake storage parent directory
duckdb_parent | ok | <project-root>/data/analytics | DuckDB analytics directory; created by runtime when needed
csv_demo | ok | <project-root>/data/sample/sample_bars.csv | demo CSV data source
```

### Step 3. Run Offline Demo

Purpose: Verify that demo data can generate a signal, pass supervisor review, and persist to SQLite.

```sh
uv run stock-agent run-demo
```

Expected output:

```text
2026-05-22T15:30:00Z QQQ signal alert: 1 strategy trigger(s), direction=buy_watch
ma_cross_demo_2_3 | buy_watch | strength=0.70 | confidence=0.90 | MA2 crossed above MA3
Run demo summary
bars_read=5
bars_used=5
candidate_signals=1
approved_signals=1
rejected_signals=0
notifications=2
sqlite_path=<project-root>/data/runtime/stock_agent.sqlite
```

### Step 4. Query Latest Signals

Purpose: Confirm that approved signals were persisted and can be queried.

```sh
uv run stock-agent cli signals --limit 5
```

Expected output:

```text
timestamp | signal_id | symbol | strategy_id | direction | strength | confidence | reason
2026-05-22T15:30:00Z | sig-qqq-ma2-ma3-20260522T153000Z | QQQ | ma_cross_demo_2_3 | buy_watch | 0.70 | 0.90 | MA2 crossed above MA3
```

### Step 5. Trace A Signal

Purpose: Confirm that a signal can be traced to source bars and trace chain records.

```sh
uv run stock-agent cli trace sig-qqq-ma2-ma3-20260522T153000Z
```

Expected output:

```text
trace_status=ok
signal_id=sig-qqq-ma2-ma3-20260522T153000Z
trace_id=trace-sig-qqq-ma2-ma3-20260522T153000Z
symbol=QQQ
strategy_id=ma_cross_demo_2_3
direction=buy_watch
supervisor_decision=approved
source_bar_ids=QQQ-30m-2026-05-22T14:30:00Z-demo_csv,QQQ-30m-2026-05-22T15:00:00Z-demo_csv,QQQ-30m-2026-05-22T15:30:00Z-demo_csv
trace_module=strategy_engine
trace_status=success
```

### Step 6. Run One Worker Tick

Purpose: Verify that one worker tick can complete provider, strategy, supervisor, and health flows.

```sh
uv run stock-agent worker --once
```

Expected output with the default config:

```text
worker_status=completed
ticks=1
errors=0
last_tick_summary:
tick_status=ok
trading_day=true
provider=csv_demo
raw_bars=0
prepared_bars=0
candidate_signals=0
approved_signals=0
rejected_signals=0
notifications=0
lake_writes=0
trace_count=3
errors=0
```

To make the demo worker process QQQ, update config:

```yaml
symbols:
  default:
    - QQQ
```

### Step 7. Run Continuous Watch Mode

Purpose: Keep the worker running at a fixed interval.

```sh
uv run stock-agent worker --interval-sec 60
```

Expected output shape:

```text
worker_status=completed
ticks=<tick-count>
errors=0
last_tick_summary:
tick_status=ok
provider=<provider-name>
approved_signals=<count>
notifications=<count>
```

### Step 8. Check Health

Purpose: Inspect provider, supervisor, notification, and worker status.

```sh
uv run stock-agent health --verbose
```

Expected output:

```text
health_status=healthy
module=run_demo
data_latency_sec=0.0
error_rate=0.0
consecutive_failures=0
alert_failures=0
verbose_health_status=ok
module | status
provider_registry | healthy
provider_compare | healthy
supervisor | healthy
worker | healthy
provider_success_rate=1.0000
provider_fallback_count=0
abnormal_bar_count=0
supervisor_intercept_count=0
notification_pending=0
notification_failed=0
config_review_backlog=0
recent_failed_traces=0
```

### Step 9. Check Telegram Entry Point

Purpose: Confirm that the Telegram command entrypoint is installed and reports current status.

```sh
uv run stock-agent telegram
```

Expected output with default config:

```text
telegram_status=disabled
reason=telegram.enabled is false
```

Expected output shape after enabling token:

```text
telegram_status=ready
listener=long_polling
workspace=<project-root>
```

### Step 10. Review Retention Dry-Run

Purpose: Review cleanup scope without deleting files.

```sh
uv run stock-agent retention
```

Expected output shape:

```text
retention_status=ok
dry_run=true
executed=false
affected_count=<count>
action | dataset | path | reason
```

To execute retention actions explicitly:

```sh
uv run stock-agent retention --execute
```

## 5. Test Verification

### Smoke Test

Purpose: Quickly verify the main README commands and Telegram bot core.

```sh
uv run --extra dev pytest tests/test_deploy_validate.py tests/test_run_demo.py tests/test_query_cli.py tests/test_telegram_bot.py
```

Expected output:

```text
17 passed
```

### Full Test

Purpose: Verify the full project.

```sh
uv run --extra dev pytest
```

Latest full verification result:

```text
313 passed, 1 xfailed
```

## 6. Common Commands

```sh
uv run stock-agent deploy-validate
uv run stock-agent run-demo
uv run stock-agent worker --once
uv run stock-agent worker --interval-sec 60
uv run stock-agent cli signals --limit 10
uv run stock-agent cli trace sig-qqq-ma2-ma3-20260522T153000Z
uv run stock-agent health --verbose
uv run stock-agent telegram
uv run stock-agent retention
uv run python -c "from pathlib import Path; from stock_agent.commands.web import run_web; raise SystemExit(run_web(Path.cwd()))"
```

## 7. Safety Boundary

Stock Agent is a market-watch and signal-alert agent. It is not an automated trading system.

Current safety boundaries:

- Trading firewall blocks high-risk trading, account, and credential intents.
- Message safety avoids guaranteed-return or auto-trading language.
- LLM is only an intent parser, not a trading executor.
- Config changes enter pending review and do not auto-apply.
- Supervisor checks must review candidate signals.

## 8. Disclaimer

This project is for educational, research, and portfolio demonstration purposes only.
It is not financial advice, investment advice, or an automated trading system.
Signals generated by this project should not be used as the sole basis for trading decisions.

Do not commit real API keys, Telegram tokens, broker credentials, account information, or other secrets.
Use environment variables for all credentials and sensitive configuration.

Live market data availability, latency, and quota depend on the configured provider and user entitlement.

## 9. License

This project is licensed under the MIT License. See `LICENSE` for details.
