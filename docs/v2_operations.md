# Stock Agent V2 Operations

## Environment

- Python `3.12+`; install development dependencies with `uv sync --extra dev`.
- Use `stock-agent mcp-server` only for read-only stdio MCP queries.
- Real provider and LLM credentials remain environment variables; benchmark and tests require no credentials or network.

## Research Operations

Submit a typed `ResearchRequest` with `stock-agent research submit --request-file REQUEST.json`. Inspect status with `research status TASK_ID`, render a final report with `research report TASK_ID`, and inspect redacted diagnostics at `GET /api/v2/research/TASK_ID/diagnostics`.

Budget limits are carried by `ExecutionBudget`: agent steps, model calls, Tool calls and task duration. MCP is allowlisted and read-only. Signal activation is an authenticated human-admin operation; no CLI, Agent or MCP request can bypass that boundary.

## Release Checks

```sh
.venv/bin/python -m pytest tests/test_v2_benchmark.py -q
RUN_FULL_BENCHMARK=1 .venv/bin/python -m pytest tests/test_v2_benchmark.py -m full_benchmark -q
.venv/bin/python -m pytest tests/test_research_safety_integration.py -q
```

Run `MigrationAudit(ROOT).build_manifest()` before deleting a bridge file. Review its importers and runtime hits, then perform the full migration and interface regression suite.
