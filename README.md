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
- Provide integration seams for Telegram, LLM-based intent parsing, and live data providers.

Currently runnable end-to-end flow:

```text
demo CSV -> bar builder -> strategy -> supervisor -> SQLite -> CLI query / CLI alert
```

External flows that still require production wiring:

```text
worker -> Telegram push alert
LLM client -> LlmParser
production live provider operations
```

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

### 3.2 Alpha Vantage Live Market Data Interface

Purpose: Switch the market data source from demo CSV to a live provider.

```yaml
provider:
  default: live
  priority:
    - live
    - csv_demo
  fallback:
    enabled: true
    order:
      - csv_demo
  csv_demo:
    path: data/sample/sample_bars.csv
  live:
    name: alpha_vantage
    api_key_env: MARKET_DATA_API_KEY
```

PowerShell environment variable:

```powershell
$env:MARKET_DATA_API_KEY = "your-alpha-vantage-key"
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

Purpose: Enable the Telegram query/interaction entrypoint. The current command runner is a skeleton; the core bot adapter is implemented and tested.

```yaml
telegram:
  enabled: true
  token_env: TELEGRAM_BOT_TOKEN
  allowed_user_ids:
    - 123456789
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
- Pending config changes
- High-risk trading/account/credential intent blocking

To enable automatic worker-to-Telegram push alerts, add:

```text
TelegramNotificationSink -> NotificationOutbox.dispatch_pending(...)
```

### 3.4 LLM Interface

Purpose: Use an LLM only for natural-language-to-intent parsing, not for direct trading decisions.

```yaml
llm:
  enabled: true
  provider: openai
  model: gpt-4.1-mini
  api_key_env: OPENAI_API_KEY
```

PowerShell environment variable:

```powershell
$env:OPENAI_API_KEY = "your-openai-key"
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
listener=skeleton
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
269 passed, 1 xfailed
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
```

## 7. Safety Boundary

Stock Agent is a market-watch and signal-alert agent. It is not an automated trading system.

Current safety boundaries:

- Trading firewall blocks high-risk trading, account, and credential intents.
- Message safety avoids guaranteed-return or auto-trading language.
- LLM is only an intent parser, not a trading executor.
- Config changes enter pending review and do not auto-apply.
- Supervisor checks must review candidate signals.
