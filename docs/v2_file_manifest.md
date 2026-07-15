# V2 File Manifest

The authoritative generated manifest is produced by `stock_agent.evaluation.migration_audit.MigrationAudit`.

| Classification | Rule | Removal policy |
|---|---|---|
| `new_v2` | `contracts/`, `services/`, `tooling/`, `signal_lab/`, `observability/`, `evaluation/` | Keep as the V2 implementation. |
| `reuse_v2` | Config, providers, storage, health, query, Web/Telegram transport | Keep while used by the official path. |
| `bridge_v2` | Legacy ReAct/Web/pipeline compatibility adapters | Remove only after import graph and runtime Trace checks are both zero. |

The manifest intentionally does not decide deletion from filename suffixes. `broker/` is retained as legacy code but is prohibited from V2 core imports.

## G8 Snapshot

Generated from the current static import graph on 2026-07-15. No bridge is eligible for removal: each still has one or more importers. Runtime hit counts must also be zero before removal, so this is a retention decision rather than a deletion request.

| Bridge | Importers | Decision |
|---|---:|---|
| `agent/runner.py` | 5 | retain |
| `agent/tools.py` | 6 | retain |
| `cli.py` | 2 | retain |
| `signals/pipeline.py` | 5 | retain |
| `web/agent_service.py` | 4 | retain |

New production files are `services/production_v2.py` and `worker/research_v2.py`. The audit classifies both as `new_v2`.
