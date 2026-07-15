# V2 Architecture

Stock Agent V2 is a task-oriented research system, not a real-time market-watch or trading system.

```text
User Transport (Web / CLI / Telegram)
  -> ResearchEntryAdapter
  -> AgentService + Orchestrator
  -> Durable Agent Plan and Task-scoped Artifacts
  -> DataEvidence / NewsEvidence
  -> Anomaly, Macro, Signal Discovery, Report Agents
  -> Deterministic Evidence and Claim Validation
  -> FinalReport

Worker
  -> ResearchTaskWorkerV2
  -> AgentRuntime executes ready durable steps
```

## Agent Boundaries

| Component | Responsibility | Model allowed | Deterministic boundary |
|---|---|---:|---|
| `DataEvidenceWorkflow` | Fetch and normalize requested market data | No | Provider allowlist, quota reservation, bar validation, artifact registration |
| `NewsEvidenceWorkflow` | Retrieve and cluster task news evidence | No | Provider/cache policy and evidence references |
| `AnomalyAnalysisAgent` | Interpret price, volume and news context | No by default | Metrics and evidence provenance remain fixed |
| `MacroAnalysisAgent` | Explain allowed macro evidence | Yes | Requires verified allowlisted macro input |
| `SignalDiscoveryAgent` | Propose reusable signals | Yes | Proposal only; Sandbox and human approval are separate |
| `ReportAgent` | Build evidence-grounded research narrative | Yes | Claim validator rejects altered or unknown evidence |
| `AgentRuntime` | Execute durable planned steps | Calls model only via registered agent | Budget ledger, traces, typed I/O and artifact persistence |

`SignalProposal` never becomes executable just because a model proposed it. The path is `Proposal -> Candidate -> Sandbox -> time-split validation -> human approval -> ActiveSignal`.

## External Services

- Twelve Data is the preferred market-data provider. SQLite reserves its per-minute request budget before HTTP calls.
- `synthetic_demo` is a clearly labelled fallback for historical-flow validation only; it is rejected for `require_current_data=true`.
- News providers supply evidence, never conclusions.
- The LLM endpoint is configurable through `llm.provider`, `llm.model`, `llm.api_key_env`, and `llm.base_url`.
- MCP is read-only and exposes stored research evidence, reports, active signals and diagnostics. It cannot trade, approve signals or write files.

## Removed V1 Scope

The V1 ReAct agent, continuous market-watch pipeline, formula strategy engine, broker adapter, notification outbox, V1 HTTP API and legacy CLI/config commands are intentionally absent. The immutable SQLite baseline migration remains only because V2 reuses generic tables such as task traces, health, news and input coordination.
