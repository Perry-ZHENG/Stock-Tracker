# Stock Agent V2 Implementation Task Specification

## Overall goal

Build a non-trading investment-research Agent that converts a user question into bounded evidence collection, specialist analysis and a validated final report. The system may use LLMs to discover signals and reason about evidence, but must never place orders, connect to a broker, or turn a model proposal into an executable signal without deterministic validation and human approval.

## Mandatory constraints

- All external data, news and model output must be traceable to a task.
- A report claim must reference unaltered, registered evidence from that task.
- Model output is typed and validated before it reaches persistence or downstream agents.
- `require_current_data=true` rejects synthetic fallback data.
- Provider quota is reserved before external calls; retries must be bounded.
- MCP is read-only.
- Signal activation requires `Proposal -> Sandbox -> time-split validation -> human approval`.
- New production Python files use the `_v2` suffix where practical; existing shared files remain only if the V2 import graph calls them.

## Current implemented modules

| Module | Production code | Input | Output | Acceptance |
|---|---|---|---|---|
| Task lifecycle | `services/agent_service.py`, `services/entrypoints.py` | `ResearchRequest` and transport actor | durable `AgentTask`, `AgentPlan`, status | all transports call the same lifecycle service |
| V2 composition | `services/production_v2.py` | config, SQLite, provider/model adapters | registered runtime and step handlers | no V1 ReAct, broker or strategy import |
| Data evidence | `research/data_evidence.py` | symbols, interval, time window, freshness | `DataEvidence` or `EvidenceGap` | provider refs, quality flags and artifact refs persist |
| News evidence | `research/news_evidence.py` | symbols and window | `NewsEvidence` | cache/dedup results are registered evidence |
| Specialist agents | `agents/{anomaly,macro,signal_discovery,report}.py` | typed upstream evidence | typed analysis/proposal/draft | invalid model JSON or unverifiable evidence stops publication |
| Signal safety | `signals/`, `signal_lab/` | discovery proposal and history artifacts | candidate/validated/approved signal | no proposal becomes active without human approval |
| Report publication | `reports/`, `validation/` | evidence bundle, analyses, observations | validated `FinalReport` | every claim passes evidence and claim validation |
| Worker | `worker/research_v2.py`, `worker/scheduler.py` | durable running tasks | executed steps, traces, gaps | restart safely resumes ready steps only |
| Transport | `web/`, `cli.py`, `telegram/` | user request/control | task response/report | every transport goes through `ResearchEntryAdapter` |
| MCP | `mcp/`, `tooling/` | read-only JSON-RPC request | stored evidence/report/diagnostic data | write, trade and approval operations are rejected |

## Completed cleanup milestone

The repository has removed all V1 runtime modules and single-step test code:

- Removed: legacy ReAct, broker, continuous market-watch, formula strategies, notification outbox, old dialog/config commands, V1 API routes, old providers, CSV regression data and per-task tests.
- Retained: V2 Agent runtime, evidence, reports, signal-lab, Web/CLI/Telegram transports, read-only MCP, storage, observability and one offline end-to-end test.
- Retained migration baseline: `storage/migration_sql/0001_legacy.sql` is immutable because existing V2 databases verify migration checksums and still use generic audit tables.

## Future coding milestones

### M1: Robust report evidence binding

**Coding task**: make the model-facing report schema accept only `evidence_ids`, then map those IDs to canonical `EvidenceRef` objects in `agents/report.py` before validation.

**Input**: a draft response with claim text, confidence and `evidence_ids`.

**Output**: `ReportDraft` whose claims contain only canonical task evidence references.

**Acceptance**: malformed IDs produce a bounded repair prompt or `EvidenceGap`; a valid report cannot fail merely because the model reserialized evidence metadata differently.

### M2: Macro evidence MCP adapter

**Coding task**: add an allowlisted, read-only macro MCP adapter that normalizes source metadata into `MacroEvidenceItem`.

**Input**: task window and macro question.

**Output**: verified macro evidence or a durable explicit gap.

**Acceptance**: macro analysis runs only with allowlisted provenance; untrusted arbitrary URL/text input is rejected and traced.

### M3: External news provider

**Coding task**: implement a configured news-provider adapter with rate limit handling, source attribution and cache TTL.

**Input**: symbols and time window.

**Output**: normalized `NewsItem` records and `NewsEvidence`.

**Acceptance**: unavailable provider yields empty/gap evidence rather than fabricated news; cache prevents repeated equivalent calls.

### M4: Signal proposal lifecycle UI

**Coding task**: add Web views/API for proposed signals, sandbox outcomes, validation metrics and admin approval.

**Input**: persisted proposal/version records.

**Output**: human-reviewable signal lifecycle status.

**Acceptance**: the UI cannot call activation directly; only an authenticated configured admin can approve a version with a reason.

### M5: Evaluation corpus

**Coding task**: create versioned, task-level evaluation cases for full reports and evidence gaps rather than restoring unit tests.

**Input**: fixture task request, deterministic providers/model, expected outcome class.

**Output**: aggregate evaluation report with schema validity, evidence coverage and unsupported-claim rate.

**Acceptance**: tests remain end-to-end; no test directly calls isolated production step internals.

## Operating rule for future work

Implement one milestone at a time. At the beginning of a milestone, read this document and the current import graph. At the end, run the full V2 end-to-end test, validate Web/Worker startup, update this document and stop for user review before starting the next milestone.
