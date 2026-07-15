# V2 Operations

## Start and stop

```sh
./scripts/stack_v2.sh start
./scripts/stack_v2.sh status
./scripts/stack_v2.sh logs
./scripts/stack_v2.sh stop
```

The script starts FastAPI and the durable V2 Worker. Stop any manually started Web or Worker process before using it.

## Submit and inspect

```sh
./scripts/submit_research_v2.sh --symbol QQQ --days 30 --question "分析 QQQ 异动的持续性并说明证据不足。"
uv run stock-agent research status TASK_ID
uv run stock-agent research report TASK_ID --format markdown
```

For one-off local diagnosis only:

```sh
uv run stock-agent research work TASK_ID
```

## Expected task states

- `running`: the Worker has ready steps or is waiting for an explicit, non-retryable evidence input.
- `paused`: a user paused the task; resume through the same transport.
- `completed`: a validated `FinalReport` was published.
- `cancelled` or `failed`: lifecycle ended without a final report.

An `EvidenceGap` is not success. It records why a required input is missing, such as exhausted provider quota, absent model configuration, or macro evidence not supplied through an allowlisted source.

## Diagnostics

`GET /api/v2/research/{task_id}/diagnostics` returns task traces and its model/tool budget. Use it to confirm provider selection, fallback status, step ownership and evidence references without exposing credentials.

## Configuration and secrets

Place secrets in `~/.config/stock-agent/env` with mode `600`. Keep `configs/config.yaml` free of credentials. Restart both Web and Worker after changing model, provider or key configuration.
