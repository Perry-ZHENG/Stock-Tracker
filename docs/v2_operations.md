# Stock Agent V2 Operations

## Environment

- Python `3.12+`; install development dependencies with `uv sync --extra dev`.
- Use `stock-agent mcp-server` only for read-only stdio MCP queries.
- Real provider and LLM credentials remain environment variables; benchmark and tests require no credentials or network.

## Research Operations

Submit a typed `ResearchRequest` with `stock-agent research submit --request-file REQUEST.json`. `stock-agent worker` drains ready V2 tasks on every tick and does not autonomously poll market data; the request time window and `require_current_data` constraint decide whether the task needs current or historical data. `stock-agent research work` performs one explicit V2-only drain for local troubleshooting, while `stock-agent research work TASK_ID` drains only that task. The legacy V1 market-watch pipeline remains opt-in through `stock-agent worker --include-legacy-market-watch --interval-sec 60`. Inspect status with `research status TASK_ID`, render a final report with `research report TASK_ID`, and inspect redacted diagnostics at `GET /api/v2/research/TASK_ID/diagnostics`.

## Provider Credit Guard

The V2 provider registry reserves Twelve Data credits in SQLite before issuing HTTP. The reservation is shared across Worker restarts and concurrent processes for the same runtime database. A request reserves the worst case for its configured retry count; the production default is `max_retries: 0`, so a single-symbol task reserves one credit and never repeats a `429` automatically. A credit-exhaustion gap is not eligible for automatic evidence replanning. OpenRouter `429` responses also do not invoke the fallback model; a new task has a default limit of four model calls. Verify `provider_name=twelve_data` and `fallback_used=false` in diagnostics when an evaluation explicitly requires Twelve Data; a CSV fallback is a transparent degraded result, not a Twelve Data validation.

The default CLI, FastAPI `create_app()`, and Telegram listener all construct the same `build_production_v2()` composition root. Missing model credentials, missing allowlisted MCP evidence, or an unsafe input produce a durable evidence gap instead of a fabricated report or a trade action.

Budget limits are carried by `ExecutionBudget`: agent steps, model calls, Tool calls and task duration. MCP is allowlisted and read-only. Signal activation is an authenticated human-admin operation; no CLI, Agent or MCP request can bypass that boundary.

## Release Checks

```sh
.venv/bin/python -m pytest tests/test_v2_benchmark.py -q
RUN_FULL_BENCHMARK=1 .venv/bin/python -m pytest tests/test_v2_benchmark.py -m full_benchmark -q
.venv/bin/python -m pytest tests/test_research_safety_integration.py -q
```

Run `MigrationAudit(ROOT).build_manifest()` before deleting a bridge file. Review its importers and runtime hits, then perform the full migration and interface regression suite.
