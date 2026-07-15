# Stock Agent V2 Architecture

## Official Research Path

```text
CLI / FastAPI / Telegram
 -> ResearchEntryAdapter
 -> AgentService
 -> Orchestrator + AgentRuntime
 -> Data/News Workflow, Tool Gateway, MCP (read-only), Signal Sandbox
 -> Evidence/Artifact/Signal Registry
 -> Report Agent -> Claim Validator -> FinalReport
```

`AgentService` owns task status (`pending -> running -> paused/completed/cancelled`), while `TaskRepository` owns durable plans, steps, messages and payloads. V2 Agents are only `signal_discovery`, `anomaly_analysis`, `macro_analysis` and `report`; planning, validation and approval are deterministic gates, not model roles.

## Safety And Recovery

- `ResearchSafetyPolicy` runs at entry, service, planning, Tool/MCP, Sandbox, Registry and report-finalisation boundaries.
- A candidate must pass AST policy, isolated Sandbox execution, leakage validation and human approval before it is active. V2 never places trades.
- `AgentTrace` links task, plan, step, model/tool/Sandbox output and final report. `BudgetLedger` persists model/tool counts, token estimates and Sandbox resource totals.
- Restart-safe workers claim durable steps. A failed or blocked call records an audit and, when a task exists, a task-scoped safety Trace.

## Compatibility Status

`web/agent_service.py`, `agent/runner.py`, `agent/tools.py` and `signals/pipeline.py` are `bridge_v2`: they remain for legacy UI/hybrid paths and are not deletion candidates until `MigrationAudit` reports zero importers and runtime hits. `broker/` is outside the V2 allowlist.
