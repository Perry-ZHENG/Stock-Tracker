# V2 File Manifest

The current production import graph is V2-only.

| Area | Primary files |
|---|---|
| Composition and lifecycle | `services/production_v2.py`, `services/agent_service.py`, `services/entrypoints.py` |
| Multi-agent runtime | `agents/orchestrator.py`, `agents/planner.py`, `agents/runtime.py`, `agents/{anomaly,macro,signal_discovery,report}.py` |
| Evidence and artifacts | `research/{data_evidence,news_evidence}.py`, `evidence/service.py`, `artifacts/` |
| Signal safety | `signals/{registry,runner,approval}.py`, `signal_lab/` |
| Reports and validation | `reports/`, `validation/`, `storage/report_repository.py` |
| Transports | `web/`, `cli.py`, `telegram/`, `commands/{web,worker,telegram,mcp_server}.py` |
| Background execution | `worker/{research_v2,scheduler,recovery,identity}.py` |
| External boundaries | `providers/{registry,twelve_data,synthetic_demo_v2}.py`, `mcp/`, `tooling/` |
| Durable storage | `storage/`, `observability/`, `health/` |

Removed source categories: V1 ReAct code, broker integration, continuous market-watch worker, formula strategies, notifications, V1 dialog/config commands, V1 API routes, legacy providers, old CSV regression data and all single-step tests.

`storage/migration_sql/0001_legacy.sql` is retained unchanged because migration checksums are immutable and its generic tables are still read by V2 persistence and audit paths. It does not re-enable V1 runtime code.
