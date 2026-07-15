# V2 File Manifest

The authoritative generated manifest is produced by `stock_agent.evaluation.migration_audit.MigrationAudit`.

| Classification | Rule | Removal policy |
|---|---|---|
| `new_v2` | `contracts/`, `services/`, `tooling/`, `signal_lab/`, `observability/`, `evaluation/` | Keep as the V2 implementation. |
| `reuse_v2` | Config, providers, storage, health, query, Web/Telegram transport | Keep while used by the official path. |
| `bridge_v2` | Legacy ReAct/Web/pipeline compatibility adapters | Remove only after import graph and runtime Trace checks are both zero. |

The manifest intentionally does not decide deletion from filename suffixes. `broker/` is retained as legacy code but is prohibited from V2 core imports.
