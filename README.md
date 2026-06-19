# Stock Tracker

Stock Tracker is a local-first stock monitoring agent for US market watch workflows.
It ingests market data, builds 30-minute bars, runs rule-based strategies,
supervises candidate signals, stores traceable decision memory, and exposes
CLI/Telegram-style query flows.

The project is intentionally demo-first: it can run without live API keys, then
be extended to live market data, LLM-assisted command parsing, and Telegram
interaction.

## What This Agent Does

```text
Market data provider
  -> Bar validation / aggregation / quarantine
  -> Strategy tools: MA Cross, BOLL, MACD, KDJ, Active-J
  -> Supervisor guardrails: schema, warmup, trace, indicator recomputation
  -> SQLite memory: signals, trace_chain, health, notifications, audits
  -> Outbox / CLI notification / query interfaces
```


## Current Integration Status

| Area | Current state |
|---|---|
| Demo signal flow | Complete and runnable offline. |
| Worker loop | Complete for scheduled ticks, provider fetch, strategies, supervisor, storage, and CLI notification. |
| Live market data | Alpha Vantage adapter exists; configure `MARKET_DATA_API_KEY` and `provider.live.name=alpha_vantage`. |
| CLI notification | Implemented through the notification outbox. |
| Telegram query bot core | Implemented and tested as a safe adapter core. |
| Telegram live polling runner | Not fully wired as a production bot runner yet; `stock-agent telegram` currently reports readiness/skeleton status. |
| Worker -> Telegram push | Extension seam exists through `NotificationOutbox`; a `TelegramNotificationSink` still needs to be added for automatic push alerts. |
| LLM parsing | Parser seam exists; a production OpenAI client adapter still needs to be wired to `LlmParser`. |

## Requirements

- Python 3.12 or newer
- `uv` for development
- Optional: `pipx` or `uv tool` for command-style installation

## Install And Bootstrap

For editable development:

```sh
uv sync --extra dev
uv run stock-agent init-config
uv run stock-agent deploy-validate
```

For command-style local installs:

```sh
pipx install .
stock-agent init-config
stock-agent run-demo
```

Or:

```sh
uv tool install .
stock-agent init-config
stock-agent run-demo
```

Expected validation output:

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

This proves the local config, demo CSV, runtime directory, and deployment
preconditions are present. It does not start the worker or call external APIs.

## Demo: Generate A Signal Offline

Run:

```sh
uv run stock-agent run-demo
```

Expected output:

```text
2026-05-22T15:30:00Z QQQ signal alert: 1 strategy trigger(s), direction=buy_watch
ma_cross_demo_2_3 | buy_watch | strength=0.70 | confidence=0.90 | MA2 上穿 MA3，触发黄金交叉观察提醒
Run demo summary
bars_read=5
bars_used=5
candidate_signals=1
approved_signals=1
rejected_signals=0
notifications=2
sqlite_path=<project-root>/data/runtime/stock_agent.sqlite
```

This confirms:

- Demo bars were read successfully.
- A candidate signal was generated.
- Supervisor approved the signal.
- Notifications were produced.
- Runtime state was written to SQLite.

## Demo: Query The Signal

Run:

```sh
uv run stock-agent cli signals --limit 5
```

Expected output:

```text
timestamp | signal_id | symbol | strategy_id | direction | strength | confidence | reason
2026-05-22T15:30:00Z | sig-qqq-ma2-ma3-20260522T153000Z | QQQ | ma_cross_demo_2_3 | buy_watch | 0.70 | 0.90 | MA2 上穿 MA3，触发黄金交叉观察提醒
```

Then trace the signal:

```sh
uv run stock-agent cli trace sig-qqq-ma2-ma3-20260522T153000Z
```

Expected output excerpt:

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

This proves the agent decision is traceable back to source bars and strategy
execution.

## Demo: Run One Worker Tick

Run:

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

Why `raw_bars=0` in this exact demo? The default config watches
`AAPL`, `MSFT`, and `NVDA`, while the bundled signal demo CSV uses `QQQ`. This
is still a successful worker tick, but it does not create a new signal.

To make the demo worker process QQQ, set:

```yaml
symbols:
  default:
    - QQQ
```

Then run:

```sh
uv run stock-agent worker --once
```

For a real deployment, replace the demo provider with a live provider as shown
below.

## Continuous Watch Mode

Run:

```sh
uv run stock-agent worker --interval-sec 60
```

The worker will:

1. Check market schedule.
2. Fetch bars from the configured provider.
3. Quarantine abnormal bars.
4. Build strategy inputs.
5. Generate candidate signals.
6. Run supervisor checks.
7. Persist approved signals and traces.
8. Enqueue notifications.
9. Record health metrics.


## Health Check

Run:

```sh
uv run stock-agent health --verbose
```

Expected output excerpt after the demo:

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

This confirms the agent can report operational state, data quality status,
supervisor activity, notification backlog, and recent failures.

## Use Live Market Data

The project includes an Alpha Vantage adapter. Update `configs/config.yaml`:

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

Set your API key:

```powershell
$env:MARKET_DATA_API_KEY = "your-alpha-vantage-key"
```

Choose symbols:

```yaml
symbols:
  default:
    - QQQ
    - NVDA
    - TSLA
```

Run one live tick:

```sh
uv run stock-agent worker --once
```

Then inspect:

```sh
uv run stock-agent cli signals --limit 10
uv run stock-agent health --verbose
```

Notes:

- Alpha Vantage availability, delay, and quota depend on your key entitlement.
- The registry records provider failures and fallback attempts.
- If live fails and fallback is enabled, `csv_demo` can keep the system from
  crashing during local validation.

## Enable More Strategies

The default config enables MA Cross and BOLL. To enable MACD, KDJ, and Active-J:

```yaml
strategies:
  ma_cross:
    enabled: true
    pairs:
      - [3, 5]
      - [5, 10]
      - [10, 20]
  boll:
    enabled: true
    window: 20
    bandwidth_baseline_window: 20
  macd:
    enabled: true
    fast: 12
    slow: 26
    signal: 9
  kdj:
    enabled: true
    window: 9
    k_smoothing: 3
    d_smoothing: 3
  active_j:
    enabled: true
    j_threshold: 80.0
    ma_window: 80
    kdj_window: 9
    k_smoothing: 3
    d_smoothing: 3
```

After changing strategies:

```sh
uv run stock-agent worker --once
uv run stock-agent cli signals --limit 20
```

## Telegram Integration

### Current Telegram Check

Run:

```sh
uv run stock-agent telegram
```

Expected output with default config:

```text
telegram_status=disabled
reason=telegram.enabled is false
```

This confirms the Telegram command entrypoint is installed. The current command
is a skeleton runner; the tested core is `TelegramBot`, which can safely handle
allowlisted user queries, pending config changes, and high-risk command blocking.

### Configure Telegram Settings

Update `configs/config.yaml`:

```yaml
telegram:
  enabled: true
  token_env: TELEGRAM_BOT_TOKEN
  allowed_user_ids:
    - 123456789
```

Set the token:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-telegram-bot-token"
```

Run:

```sh
uv run stock-agent telegram
```

With a token present, the current runner reports readiness/skeleton status. To
turn it into a production polling bot, wire a Telegram SDK polling loop to
`TelegramBot.handle_update(...)`.

### Telegram Bot Core Behavior

The existing bot core supports:

- `/signals`
- `/health`
- `/news`
- `/schedule`
- `/provider-compare`
- `/abnormal-bars`
- `/trace SIGNAL_ID`
- Safe natural-language read-only intents through `LlmParser`
- Pending config changes for admin users
- Blocking high-risk trading/account/credential intents

Test proof:

```sh
uv run --extra dev pytest tests/test_telegram_bot.py
```

Expected result:

```text
tests/test_telegram_bot.py ..... [100%]
```

### Add Automatic Telegram Push Alerts

The worker currently enqueues CLI notifications when a notification stream is
available. To push alerts to Telegram automatically:

1. Add a `TelegramNotificationSink` implementing `NotificationDeliverySink`.
2. Read `TELEGRAM_BOT_TOKEN` and target chat/user IDs from config/env.
3. Register the sink in `WorkerPipeline`:

```python
dispatch_result = outbox.dispatch_pending(
    {
        "cli": CliNotificationSink(self.notification_stream),
        "telegram": TelegramNotificationSink(token=token, chat_ids=chat_ids),
    },
    max_retries=5,
)
```

4. Enqueue channels with `["cli", "telegram"]`.
5. Verify with a fake sink test before sending real messages.

Recommended validation:

```sh
uv run --extra dev pytest tests/test_notifications.py tests/test_telegram_bot.py
uv run stock-agent worker --once
```

## LLM / Agent Integration

The project is a deterministic agent by default. It does not need an LLM to make
trading decisions. This is intentional: market-watch decisions should be
repeatable, traceable, and supervised.

The LLM seam is for command parsing and explanation, not for direct order
placement or unsupervised trading.

Existing LLM parser behavior:

```text
Natural language
  -> LlmParser
  -> validated CommandIntent schema
  -> QueryService or pending config-change review
  -> TradingActionFirewall blocks high-risk intents
```

Configuration shape:

```yaml
llm:
  enabled: true
  provider: openai
  model: gpt-4.1-mini
  api_key_env: OPENAI_API_KEY
```

Environment:

```powershell
$env:OPENAI_API_KEY = "your-openai-key"
```

To connect a production LLM client:

1. Implement a small client function that sends the prompt from `LlmParser` to
   your model and returns a JSON string.
2. Instantiate:

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

3. Keep `validate_intent(...)` and `TradingActionFirewall` in the path.
4. Do not let the LLM directly call provider, broker, or notification APIs.

Recommended safety rules:

- LLM may classify user intent.
- LLM may help format explanations.
- LLM must emit only validated command intents.
- LLM must not generate direct buy/sell/transfer/credential actions.
- Config changes must remain pending review.

## Verification Commands

Run a focused smoke suite:

```sh
uv run --extra dev pytest tests/test_deploy_validate.py tests/test_run_demo.py tests/test_query_cli.py tests/test_telegram_bot.py
```

Expected result:

```text
17 passed
```

Run the full suite:

```sh
uv run --extra dev pytest
```

Expected result from the latest full verification:

```text
269 passed, 1 xfailed
```

## Common Operations

```sh
# Validate local deployment preconditions
uv run stock-agent deploy-validate

# Run offline demo
uv run stock-agent run-demo

# Run one worker tick
uv run stock-agent worker --once

# Run continuous watch
uv run stock-agent worker --interval-sec 60

# Query latest signals
uv run stock-agent cli signals --limit 10

# Trace one signal
uv run stock-agent cli trace sig-qqq-ma2-ma3-20260522T153000Z

# Inspect health and observability
uv run stock-agent health --verbose

# Review retention plan without deleting data
uv run stock-agent retention
```

`stock-agent retention` is dry-run by default. It only applies retention actions
when explicitly called with `--execute`.

## Deployment

Deployment templates are under:

- `deploy/systemd/stock-agent-worker.service`
- `deploy/launchd/com.example.stock-agent.worker.plist`
- `deploy/pm2/ecosystem.config.cjs`

Before installing a template on a host:

```sh
stock-agent deploy-validate
```

Then run the worker through your chosen process manager:

```sh
stock-agent worker --interval-sec 60
```

See `docs/deployment.md` for template-specific notes.

## Important Safety Boundary

This project is a market-watch and signal assistant. It is not an automated
trading system.

The system intentionally includes:

- Trading action firewall
- Message safety review
- Config review workflow
- Traceable signal memory
- Supervisor recomputation

Do not wire the LLM directly to brokerage actions. If broker integration is
added later, keep human approval, permission isolation, and audit logging in the
path.
